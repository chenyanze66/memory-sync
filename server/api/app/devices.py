from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException

from .db import transaction
from .dependencies import current_user
from .schemas import DeviceRegisterRequest
from .security import decode_public_key

router = APIRouter(prefix="/v1/devices", tags=["devices"])


@router.post("/register", status_code=201)
async def register_device(payload: DeviceRegisterRequest, user_id: UUID = Depends(current_user)):
    device_id = uuid4()
    try:
        async with transaction(user_id) as conn:
            await conn.execute(
                """
                INSERT INTO devices(id,user_id,name,platform,public_key)
                VALUES (%s,%s,%s,%s,%s)
                """,
                (
                    device_id,
                    user_id,
                    payload.name,
                    payload.platform,
                    decode_public_key(payload.public_key),
                ),
            )
    except Exception as exc:
        if getattr(exc, "sqlstate", None) == "23505":
            raise HTTPException(status_code=409, detail="device public key already registered") from exc
        raise
    return {"device_id": device_id}


@router.get("")
async def list_devices(user_id: UUID = Depends(current_user)):
    async with transaction(user_id) as conn:
        result = await conn.execute(
            "SELECT id,name,platform,status,created_at,last_seen_at,revoked_at FROM devices ORDER BY created_at"
        )
        rows = await result.fetchall()
    return {
        "devices": [
            dict(
                zip(
                    [
                        "id",
                        "name",
                        "platform",
                        "status",
                        "created_at",
                        "last_seen_at",
                        "revoked_at",
                    ],
                    row,
                    strict=True,
                )
            )
            for row in rows
        ]
    }


@router.post("/{device_id}/revoke")
async def revoke_device(device_id: UUID, user_id: UUID = Depends(current_user)):
    async with transaction(user_id) as conn:
        result = await conn.execute(
            "UPDATE devices SET status='revoked',revoked_at=now() WHERE id=%s AND status='active' RETURNING id",
            (device_id,),
        )
        if await result.fetchone() is None:
            raise HTTPException(status_code=404, detail="active device not found")
    return {"revoked": True, "device_id": device_id}
