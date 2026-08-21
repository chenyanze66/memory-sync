import hashlib
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Jsonb

from .config import get_settings
from .db import transaction
from .dependencies import DeviceIdentity, active_device
from .schemas import PushRequest, ResolveRequest

router = APIRouter(prefix="/v1/sync", tags=["sync"])


def normalize_path(value: str) -> str:
    if "\\" in value or "\x00" in value or value.startswith("/"):
        raise HTTPException(status_code=422, detail="path must be a POSIX relative path")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise HTTPException(status_code=422, detail="path contains an invalid segment")
    normalized = "/".join(parts)
    return normalized


def verify_content(content: str, content_hash: str, deleted: bool) -> bytes:
    raw = content.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    if len(raw) > get_settings().max_content_bytes:
        raise HTTPException(status_code=413, detail="content exceeds configured limit")
    if deleted and raw:
        raise HTTPException(status_code=422, detail="tombstone content must be empty")
    if hashlib.sha256(raw).hexdigest() != content_hash:
        raise HTTPException(status_code=422, detail="content_hash mismatch")
    return raw


async def check_storage_quota(conn, user_id) -> None:
    """Raise 413 when the user is over the per-user storage quota.

    Runs inside the caller's transaction AFTER the new version is inserted;
    the raise aborts the transaction so the insert rolls back. Counts active
    (non-deleted) documents and the byte size of their head versions.
    """
    settings = get_settings()
    result = await conn.execute(
        """
        SELECT COUNT(*) FILTER (WHERE d.status <> 'deleted'),
               COALESCE(SUM(LENGTH(v.content)), 0)
        FROM documents d
        LEFT JOIN document_versions v ON v.id = d.head_version_id
        WHERE d.user_id = %s
        """,
        (user_id,),
    )
    files, total_bytes = await result.fetchone()
    if files > settings.storage_max_files or total_bytes > settings.storage_max_bytes:
        raise HTTPException(
            status_code=413,
            detail="user storage quota exceeded (files=%d/%d, bytes=%d/%d)"
            % (files, settings.storage_max_files, total_bytes, settings.storage_max_bytes),
        )


async def operation_result(conn, device_id, operation_id):
    result = await conn.execute(
        "SELECT result_payload FROM operations WHERE device_id=%s AND operation_id=%s",
        (device_id, operation_id),
    )
    row = await result.fetchone()
    return row[0] if row else None


@router.post("/push")
async def push(payload: PushRequest, identity: DeviceIdentity = Depends(active_device)):
    path = normalize_path(payload.path)
    normalized = verify_content(payload.content, payload.content_hash, payload.deleted).decode()
    async with transaction(identity.user_id) as conn:
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"{identity.device_id}:{payload.operation_id}",),
        )
        replay = await operation_result(conn, identity.device_id, payload.operation_id)
        if replay is not None:
            return replay

        space_result = await conn.execute("SELECT id FROM spaces WHERE slug=%s", (payload.space,))
        space = await space_result.fetchone()
        if space is None:
            raise HTTPException(status_code=404, detail="space not found")

        document_id = uuid4()
        await conn.execute(
            """
            INSERT INTO documents(id,user_id,space_id,path)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (space_id,path) DO NOTHING
            """,
            (document_id, identity.user_id, space[0], path),
        )
        doc_result = await conn.execute(
            "SELECT id,head_version_id,status FROM documents WHERE space_id=%s AND path=%s FOR UPDATE",
            (space[0], path),
        )
        document_id, head_version_id, doc_status = await doc_result.fetchone()

        # Validate base ownership before the head-content noop shortcut so a
        # bogus/foreign base is never acked as "noop".
        if payload.base_version_id is not None:
            base_result = await conn.execute(
                "SELECT 1 FROM document_versions WHERE id=%s AND document_id=%s",
                (payload.base_version_id, document_id),
            )
            if await base_result.fetchone() is None:
                raise HTTPException(
                    status_code=409, detail="base_version_id does not belong to this document"
                )

        if head_version_id is not None:
            head_result = await conn.execute(
                "SELECT content_hash,deleted FROM document_versions WHERE id=%s", (head_version_id,)
            )
            head_hash, head_deleted = await head_result.fetchone()
            if head_hash == payload.content_hash and head_deleted == payload.deleted:
                response = {
                    "result": "noop",
                    "version_id": str(head_version_id),
                    "head_version_id": str(head_version_id),
                    "event_seq": None,
                }
                await conn.execute(
                    "INSERT INTO operations(operation_id,device_id,user_id,result_type,result_version_id,result_payload) VALUES (%s,%s,%s,'noop',%s,%s)",
                    (
                        payload.operation_id,
                        identity.device_id,
                        identity.user_id,
                        head_version_id,
                        Jsonb(response),
                    ),
                )
                return response

        version_id = uuid4()
        # Once conflicted, keep the displayed head stable until /resolve merges all leaves.
        advance_head = doc_status != "conflicted" and head_version_id == payload.base_version_id
        result_type = "accepted" if advance_head else "conflict"
        if head_version_id is None and payload.base_version_id is not None:
            result_type = "conflict"
            advance_head = False
        parent_ids = [payload.base_version_id] if payload.base_version_id else []
        await conn.execute(
            """
            INSERT INTO document_versions(
                id,document_id,user_id,parent_version_ids,content_hash,content,deleted,
                author_device_id,client_modified_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                version_id,
                document_id,
                identity.user_id,
                parent_ids,
                payload.content_hash,
                normalized,
                payload.deleted,
                identity.device_id,
                payload.client_modified_at,
            ),
        )
        if advance_head:
            await conn.execute(
                "UPDATE documents SET head_version_id=%s,status=%s WHERE id=%s",
                (
                    version_id,
                    "deleted" if payload.deleted else "normal",
                    document_id,
                ),
            )
        else:
            await conn.execute(
                "UPDATE documents SET status='conflicted' WHERE id=%s", (document_id,)
            )

        await check_storage_quota(conn, identity.user_id)

        event_result = await conn.execute(
            """
            INSERT INTO sync_events(user_id,space_id,document_id,version_id,event_type)
            VALUES (%s,%s,%s,%s,%s) RETURNING seq
            """,
            (identity.user_id, space[0], document_id, version_id, result_type),
        )
        event_seq = (await event_result.fetchone())[0]
        response = {
            "result": result_type,
            "version_id": str(version_id),
            "head_version_id": str(version_id if advance_head else head_version_id)
            if (advance_head or head_version_id)
            else None,
            "event_seq": event_seq,
        }
        await conn.execute(
            "INSERT INTO operations(operation_id,device_id,user_id,result_type,result_version_id,result_payload) VALUES (%s,%s,%s,%s,%s,%s)",
            (
                payload.operation_id,
                identity.device_id,
                identity.user_id,
                result_type,
                version_id,
                Jsonb(response),
            ),
        )
        return response


@router.get("/pull")
async def pull(
    space: str,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=2, ge=1, le=2),
    identity: DeviceIdentity = Depends(active_device),
):
    async with transaction(identity.user_id) as conn:
        result = await conn.execute(
            """
            SELECT e.seq,e.event_type,d.id,d.path,d.status,v.id,v.parent_version_ids,
                   v.content_hash,v.content,v.deleted,v.created_at,v.author_device_id
            FROM sync_events e
            JOIN spaces s ON s.id=e.space_id
            JOIN documents d ON d.id=e.document_id
            JOIN document_versions v ON v.id=e.version_id
            WHERE s.slug=%s AND e.seq>%s
            ORDER BY e.seq ASC LIMIT %s
            """,
            (space, after_seq, limit),
        )
        rows = await result.fetchall()
    keys = [
        "seq",
        "event_type",
        "document_id",
        "path",
        "document_status",
        "version_id",
        "parent_version_ids",
        "content_hash",
        "content",
        "deleted",
        "created_at",
        "author_device_id",
    ]
    events = [dict(zip(keys, row, strict=True)) for row in rows]
    return {"events": events, "next_seq": events[-1]["seq"] if events else after_seq}


@router.get("/bootstrap")
async def bootstrap(
    space: str,
    after_path: str | None = Query(default=None, max_length=1024),
    snapshot_seq: int | None = Query(default=None, ge=0),
    limit: int = Query(default=2, ge=1, le=2),
    identity: DeviceIdentity = Depends(active_device),
):
    if after_path is not None:
        after_path = normalize_path(after_path)
    async with transaction(identity.user_id) as conn:
        result = await conn.execute(
            """
            SELECT d.id,d.path,d.status,v.id,v.content_hash,v.content,v.deleted,v.created_at
            FROM documents d JOIN spaces s ON s.id=d.space_id
            LEFT JOIN document_versions v ON v.id=d.head_version_id
            WHERE s.slug=%s AND (%s::citext IS NULL OR d.path>%s::citext)
            ORDER BY d.path LIMIT %s
            """,
            (space, after_path, after_path, limit + 1),
        )
        rows = await result.fetchall()
        cursor_result = await conn.execute(
            """
            SELECT coalesce(max(e.seq),0) FROM sync_events e
            JOIN spaces s ON s.id=e.space_id WHERE s.slug=%s
            """,
            (space,),
        )
        current_cursor = (await cursor_result.fetchone())[0]
        if snapshot_seq is not None and snapshot_seq > current_cursor:
            raise HTTPException(status_code=409, detail="snapshot_seq is ahead of server cursor")
        cursor = current_cursor if snapshot_seq is None else snapshot_seq
    has_more = len(rows) > limit
    rows = rows[:limit]
    keys = [
        "document_id",
        "path",
        "status",
        "version_id",
        "content_hash",
        "content",
        "deleted",
        "created_at",
    ]
    return {
        "documents": [dict(zip(keys, row, strict=True)) for row in rows],
        "snapshot_seq": cursor,
        "has_more": has_more,
        "next_after_path": str(rows[-1][1]) if has_more and rows else None,
    }


@router.post("/resolve")
async def resolve(payload: ResolveRequest, identity: DeviceIdentity = Depends(active_device)):
    normalized = verify_content(payload.content, payload.content_hash, payload.deleted).decode()
    async with transaction(identity.user_id) as conn:
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"{identity.device_id}:{payload.operation_id}",),
        )
        replay = await operation_result(conn, identity.device_id, payload.operation_id)
        if replay is not None:
            return replay
        doc_result = await conn.execute(
            "SELECT id,space_id,status FROM documents WHERE id=%s FOR UPDATE",
            (payload.document_id,),
        )
        doc = await doc_result.fetchone()
        if doc is None:
            raise HTTPException(status_code=404, detail="document not found")
        leaves_result = await conn.execute(
            """
            SELECT v.id FROM document_versions v
            WHERE v.document_id=%s
              AND NOT EXISTS (
                SELECT 1 FROM document_versions child
                WHERE child.document_id=v.document_id AND v.id=ANY(child.parent_version_ids)
              )
            ORDER BY v.id
            """,
            (payload.document_id,),
        )
        leaves = {row[0] for row in await leaves_result.fetchall()}
        if leaves != set(payload.parent_version_ids):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "conflict leaves changed",
                    "leaf_version_ids": [str(v) for v in leaves],
                },
            )
        version_id = uuid4()
        await conn.execute(
            """
            INSERT INTO document_versions(id,document_id,user_id,parent_version_ids,content_hash,content,deleted,author_device_id,client_modified_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                version_id,
                payload.document_id,
                identity.user_id,
                payload.parent_version_ids,
                payload.content_hash,
                normalized,
                payload.deleted,
                identity.device_id,
                payload.client_modified_at,
            ),
        )
        await conn.execute(
            "UPDATE documents SET head_version_id=%s,status=%s WHERE id=%s",
            (version_id, "deleted" if payload.deleted else "normal", payload.document_id),
        )
        await check_storage_quota(conn, identity.user_id)
        event_result = await conn.execute(
            "INSERT INTO sync_events(user_id,space_id,document_id,version_id,event_type) VALUES (%s,%s,%s,%s,'resolved') RETURNING seq",
            (identity.user_id, doc[1], payload.document_id, version_id),
        )
        seq = (await event_result.fetchone())[0]
        response = {
            "result": "accepted",
            "version_id": str(version_id),
            "head_version_id": str(version_id),
            "event_seq": seq,
        }
        await conn.execute(
            "INSERT INTO operations(operation_id,device_id,user_id,result_type,result_version_id,result_payload) VALUES (%s,%s,%s,'accepted',%s,%s)",
            (
                payload.operation_id,
                identity.device_id,
                identity.user_id,
                version_id,
                Jsonb(response),
            ),
        )
        return response
