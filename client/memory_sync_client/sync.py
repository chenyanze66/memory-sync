"""Markdown discovery and the pull/push sync engine.

Content is UTF-8 text only: push sends ``content`` as raw text (never
base64) and ``content_hash`` is the SHA-256 of the server-normalized text
(CRLF/CR folded to LF, then UTF-8 encoded). Pull events carry raw text that
is written verbatim to disk. Snapshot semantics:
``config.snapshot[rel_path]`` is the normalized content hash of the last
content this client treated as current (either applied from the server or
pushed). ``pending_conflicts`` marks paths where the server copy was
diverted to ``conflicts/<UTC timestamp>/<rel_path>``; the local file is
never overwritten. A path leaves ``pending_conflicts`` when both sides
agree on the same content hash again.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .api import ApiClient, ApiError
from .config import Config
from .crypto import sha256_hex

MARKDOWN_SUFFIXES = {".md"}
CONFLICTS_DIR_NAME = "conflicts"


def is_markdown(name: str) -> bool:
    return Path(name).suffix.lower() in MARKDOWN_SUFFIXES


def normalize_text(text: str) -> bytes:
    """Fold line endings exactly like the server, then encode UTF-8.

    The server hashes ``content.replace("\\r\\n", "\\n").replace("\\r", "\\n")``
    encoded as UTF-8; ``content_hash`` must match that byte-for-byte.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def text_hash(text: str) -> str:
    """Server-normalized content hash for UTF-8 text."""
    return sha256_hex(normalize_text(text))


def file_text_hash(path: str | Path) -> str:
    """Normalized content hash of a UTF-8 text file.

    Raises ValueError when the file is not valid UTF-8, so non-text Markdown
    is rejected instead of being hashed or pushed as opaque bytes.
    """
    data = Path(path).read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path} is not valid UTF-8: markdown must be UTF-8 text") from exc
    return text_hash(text)


def discover_markdown(sync_root: str | Path) -> list[str]:
    """Return sorted forward-slash relative paths of Markdown files.

    Hidden directories (dot-prefixed) and the conflicts tree are skipped.
    """
    root = Path(sync_root)
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        keep: list[str] = []
        for name in dirnames:
            if name.startswith(".") or name == CONFLICTS_DIR_NAME:
                continue
            keep.append(name)
        dirnames[:] = keep
        for filename in filenames:
            if not is_markdown(filename):
                continue
            rel = (rel_dir / filename).as_posix()
            if rel.startswith(".") or rel.startswith(f"{CONFLICTS_DIR_NAME}/"):
                continue
            found.append(rel)
    return sorted(found)


def build_push_entry(
    path: str,
    content: bytes,
    *,
    base_version_id: str | None = None,
    deleted: bool = False,
    operation_id: str | None = None,
) -> dict[str, Any]:
    """Build one POST /v1/sync/push payload.

    ``content`` is sent as raw UTF-8 text; ``content_hash`` is the SHA-256
    of the server-normalized text. Non-UTF-8 bytes raise ValueError.
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"cannot push {path!r}: markdown must be UTF-8 text") from exc
    return {
        "operation_id": operation_id or str(uuid.uuid4()),
        "space": "user-global",
        "path": path,
        "base_version_id": base_version_id,
        "content_hash": text_hash(text),
        "content": text,
        "deleted": deleted,
    }


def decode_event_content(event: dict[str, Any]) -> bytes:
    """Return an event's ``content`` as raw UTF-8 bytes.

    The server returns ``content`` as raw text (a base64-looking string like
    ``YWJj`` stays verbatim); no base64 decoding is attempted.
    """
    value = event.get("content")
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return str(value).encode("utf-8")


def _safe_rel_path(path: str) -> Path:
    """Reject absolute paths and traversal outside the sync root."""
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"unsafe sync path: {path!r}")
    return candidate


class SyncEngine:
    """Applies pull events and pushes local Markdown changes."""

    def __init__(
        self,
        config: Config,
        api: ApiClient,
        sync_root: str | Path,
        *,
        limit: int = 2,
        max_pages: int = 1000,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.api = api
        self.sync_root = Path(sync_root)
        self.limit = limit
        self.max_pages = max_pages
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _ts(self) -> str:
        return self._clock().strftime("%Y%m%dT%H%M%SZ")

    def _auth(self) -> tuple[str, str, str]:
        if not (self.config.access_token and self.config.device_id and self.config.private_key):
            raise RuntimeError("not logged in; run 'memory-sync login' first")
        return self.config.access_token, self.config.device_id, self.config.private_key

    def _write_conflict(self, ts: str, rel_path: str, content: bytes) -> Path:
        target = self.sync_root / CONFLICTS_DIR_NAME / ts / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def _write_file(self, rel_path: str, content: bytes) -> None:
        target = self.sync_root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    # -- pull ----------------------------------------------------------------

    def pull(self) -> dict[str, Any]:
        """Fetch and apply pending events; return a summary."""
        token, device_id, private_key = self._auth()
        after = self.config.last_seq
        applied = 0
        conflicts: list[str] = []
        pages = 0
        while True:
            response = self.api.pull(after, token, device_id, private_key, limit=self.limit)
            events = response.get("events") or []
            next_seq = int(response.get("next_seq") or after)
            for event in events:
                outcome, path = self._apply_event(event)
                if outcome == "applied" or outcome == "deleted":
                    applied += 1
                elif outcome == "conflict":
                    conflicts.append(path)
            if not events or next_seq <= after:
                break
            after = next_seq
            self.config.last_seq = next_seq
            pages += 1
            if pages >= self.max_pages:
                break
        self.config.last_sync_at = self._ts()
        return {"applied": applied, "conflicts": conflicts, "next_seq": after, "pages": pages}

    def _apply_event(self, event: dict[str, Any]) -> tuple[str, str]:
        """Apply one pull event. Returns (outcome, path)."""
        path = event.get("path")
        if not path or not isinstance(path, str):
            return ("skipped", "")
        rel = _safe_rel_path(path)
        version_id = event.get("version_id")
        deleted = bool(event.get("deleted"))
        content = b"" if deleted else decode_event_content(event)
        if event.get("content_hash"):
            content_hash = event["content_hash"]
        else:
            content_hash = text_hash(content.decode("utf-8"))
        local_path = self.sync_root / rel
        local_sha = None
        if local_path.exists():
            try:
                local_sha = file_text_hash(local_path)
            except ValueError:
                pass  # not UTF-8 text: treated as divergent and preserved

        if deleted:
            return self._apply_delete(path, rel, local_path, local_sha)

        if path in self.config.pending_conflicts:
            if local_sha == content_hash:
                # Sides agree again: conflict resolved by adopting server content.
                self.config.pending_conflicts.pop(path, None)
                self._write_file(path, content)
                self.config.snapshot[path] = content_hash
            else:
                ts = self.config.pending_conflicts[path]
                self._write_conflict(ts, path, content)
                return ("conflict", path)
        else:
            agreed = self.config.snapshot.get(path)
            untracked_diverges = (
                agreed is None
                and local_sha is not None
                and content_hash != local_sha
            )
            tracked_diverges = (
                agreed is not None
                and (
                    local_sha is None
                    or (local_sha != agreed and content_hash != local_sha)
                )
            )
            if untracked_diverges or tracked_diverges:
                # The server copy clashes with local content: a never-tracked
                # file (e.g. created independently on another device), a local
                # file unreadable as UTF-8, or edits since the last agreed
                # content. Preserve the local file and divert the server copy
                # into conflicts/.
                ts = self._ts()
                self._write_conflict(ts, path, content)
                self.config.pending_conflicts[path] = ts
                self.config.snapshot[path] = local_sha or agreed
                return ("conflict", path)
            self._write_file(path, content)
            self.config.snapshot[path] = content_hash

        if version_id:
            self.config.versions[path] = version_id
        return ("applied", path)

    def _apply_delete(
        self, path: str, rel: Path, local_path: Path, local_sha: str | None
    ) -> tuple[str, str]:
        if path in self.config.pending_conflicts:
            # Server deleted a path we conflicted on: keep the local file;
            # the next push will re-create it on the server.
            self.config.pending_conflicts.pop(path, None)
            self.config.snapshot.pop(path, None)
            self.config.versions.pop(path, None)
            return ("conflict", path)
        agreed = self.config.snapshot.get(path)
        if agreed is not None and (local_sha is None or local_sha != agreed):
            # Server delete would drop local edits: preserve them and report.
            ts = self._ts()
            self._write_conflict(ts, path, local_path.read_bytes())
            self.config.pending_conflicts[path] = ts
            return ("conflict", path)
        if local_path.exists():
            local_path.unlink()
        self.config.snapshot.pop(path, None)
        self.config.versions.pop(path, None)
        return ("deleted", path)

    # -- push ----------------------------------------------------------------

    def push(self) -> dict[str, Any]:
        """Push changed and deleted Markdown; return a summary."""
        token, device_id, private_key = self._auth()
        pushed: list[str] = []
        conflicts: list[str] = []
        invalid: list[str] = []
        unchanged = 0
        existing = discover_markdown(self.sync_root)
        known = set(self.config.snapshot)

        for rel in existing:
            local_path = self.sync_root / rel
            try:
                local_sha = file_text_hash(local_path)
            except ValueError:
                invalid.append(rel)
                continue
            if self.config.snapshot.get(rel) == local_sha:
                unchanged += 1
                continue
            entry = build_push_entry(
                rel,
                local_path.read_bytes(),
                base_version_id=self.config.versions.get(rel),
            )
            outcome = self._push_entry(entry, rel, local_sha, token, device_id, private_key)
            if outcome == "pushed":
                pushed.append(rel)
            elif outcome == "conflict":
                conflicts.append(rel)

        for rel in sorted(known - set(existing)):
            # Tombstones carry empty content; the server validates content_hash
            # against that empty payload (sha256 of ""), not against the file
            # being deleted. Sending the deleted file's hash caused 422.
            entry = build_push_entry(
                rel,
                b"",
                base_version_id=self.config.versions.get(rel),
                deleted=True,
            )
            outcome = self._push_entry(entry, rel, None, token, device_id, private_key)
            if outcome == "pushed":
                self.config.snapshot.pop(rel, None)
                self.config.versions.pop(rel, None)
                pushed.append(rel)
            elif outcome == "conflict":
                conflicts.append(rel)

        self.config.last_sync_at = self._ts()
        return {
            "pushed": pushed,
            "unchanged": unchanged,
            "conflicts": conflicts,
            "invalid": invalid,
        }

    def _push_entry(
        self,
        entry: dict[str, Any],
        rel: str,
        local_sha: str | None,
        token: str,
        device_id: str,
        private_key: str,
    ) -> str:
        try:
            self.api.push(entry, token, device_id, private_key)
        except ApiError as exc:
            if exc.status == 409:
                return "conflict"
            raise
        if local_sha is not None:
            self.config.snapshot[rel] = local_sha
        self.config.pending_conflicts.pop(rel, None)
        return "pushed"

    # -- run -----------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """Pull pending events, push local changes, then pull once more.

        The trailing pull advances the seq cursor past events created by our
        own push, so the next run never re-fetches them and misfires a
        conflict against files we just wrote locally.
        """
        pull_result = self.pull()
        push_result = self.push()
        follow_up = self.pull()
        pull_result = {
            "applied": pull_result["applied"] + follow_up["applied"],
            "conflicts": sorted(
                set(pull_result["conflicts"]) | set(follow_up["conflicts"])
            ),
            "next_seq": follow_up["next_seq"],
            "pages": pull_result["pages"] + follow_up["pages"],
        }
        return {"pull": pull_result, "push": push_result}
