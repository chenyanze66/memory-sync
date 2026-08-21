import asyncio
import hashlib
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings
from .db import transaction
from .security import decode_access_token, verify_device_signature

bearer = HTTPBearer(auto_error=False)
_auth_rate_lock = asyncio.Lock()
_auth_limiter: "AuthLimiter | None" = None


def client_ip_from_xff(xff: str | None, client_host: str | None) -> str:
    """Trusted client IP for rate limiting.

    Caddy overwrites ``X-Forwarded-For`` with the direct remote peer (see
    Caddyfile), so the right-most value is authoritative; when absent, fall
    back to the socket peer address.
    """
    if xff and xff.strip():
        return xff.split(",")[-1].strip()
    return client_host or "unknown"


class AuthLimiter:
    """In-process per-client attempt limiter with a hard key cap.

    Fits the single-worker 2G deployment: at most ``max_keys`` clients are
    tracked. When the table is full, fully-expired keys are purged first, then
    the least-recently-used key is evicted to make room.
    """

    def __init__(self, max_attempts: int, window_seconds: float, max_keys: int = 4096):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._clients: OrderedDict[str, deque[float]] = OrderedDict()

    @property
    def key_count(self) -> int:
        return len(self._clients)

    def allow(self, client: str, now: float) -> bool:
        """Record an attempt for ``client``; return False when over the cap."""
        cutoff = now - self.window_seconds
        attempts = self._clients.get(client)
        if attempts is None:
            if len(self._clients) >= self.max_keys:
                self._evict_stale(now)
                while len(self._clients) >= self.max_keys:
                    self._clients.popitem(last=False)
            attempts = deque()
            self._clients[client] = attempts
        else:
            self._clients.move_to_end(client)
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if len(attempts) >= self.max_attempts:
            return False
        attempts.append(now)
        return True

    def _evict_stale(self, now: float) -> None:
        cutoff = now - self.window_seconds
        for key in [k for k, q in self._clients.items() if q and q[-1] <= cutoff]:
            del self._clients[key]


def _limiter() -> AuthLimiter:
    global _auth_limiter
    if _auth_limiter is None:
        settings = get_settings()
        _auth_limiter = AuthLimiter(
            max_attempts=settings.auth_rate_limit_attempts,
            window_seconds=settings.auth_rate_limit_window_seconds,
        )
    return _auth_limiter


async def auth_rate_limit(request: Request) -> None:
    """Small in-process limiter suitable for the single-worker 2G deployment."""
    client = client_ip_from_xff(
        request.headers.get("x-forwarded-for"),
        request.client.host if request.client else None,
    )
    async with _auth_rate_lock:
        if not _limiter().allow(client, time.monotonic()):
            raise HTTPException(status_code=429, detail="too many authentication attempts")


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> UUID:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="bearer token required"
        )
    user_id = decode_access_token(credentials.credentials)
    # Reject disabled or deleted accounts immediately. auth_accounts is not
    # RLS-protected, so no app.user_id is needed here; each request's own
    # transaction still sets it for the RLS-protected business tables.
    async with transaction() as conn:
        result = await conn.execute(
            "SELECT 1 FROM auth_accounts WHERE id=%s AND disabled_at IS NULL",
            (user_id,),
        )
        if await result.fetchone() is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="account disabled or not found",
            )
    return user_id


@dataclass(frozen=True)
class DeviceIdentity:
    user_id: UUID
    device_id: UUID


def canonical_request(request: Request, body: bytes, timestamp: str, nonce: str) -> bytes:
    raw_query = request.url.query
    target = request.url.path + (f"?{raw_query}" if raw_query else "")
    body_hash = hashlib.sha256(body).hexdigest()
    return f"{request.method.upper()}\n{target}\n{timestamp}\n{nonce}\n{body_hash}".encode()


async def active_device(
    request: Request,
    user_id: UUID = Depends(current_user),
    device_id: UUID = Header(alias="X-Device-Id"),
    device_timestamp: str = Header(alias="X-Device-Timestamp"),
    device_nonce: UUID = Header(alias="X-Device-Nonce"),
    device_signature: str = Header(alias="X-Device-Signature"),
) -> DeviceIdentity:
    try:
        timestamp = int(device_timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid device timestamp") from exc

    if abs(int(time.time()) - timestamp) > get_settings().device_clock_skew_seconds:
        raise HTTPException(status_code=401, detail="device timestamp outside allowed window")

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > get_settings().max_request_bytes:
                raise HTTPException(status_code=413, detail="request body exceeds configured limit")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid content-length") from exc
    body = await request.body()
    if len(body) > get_settings().max_request_bytes:
        raise HTTPException(status_code=413, detail="request body exceeds configured limit")
    async with transaction(user_id) as conn:
        result = await conn.execute(
            "SELECT public_key FROM devices WHERE id=%s AND user_id=%s AND status='active'",
            (device_id, user_id),
        )
        row = await result.fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="unknown or revoked device")

        verify_device_signature(
            row[0],
            device_signature,
            canonical_request(request, body, device_timestamp, str(device_nonce)),
        )
        inserted = await conn.execute(
            """
            INSERT INTO device_nonces(device_id, user_id, nonce, expires_at)
            VALUES (%s, %s, %s, now() + interval '10 minutes')
            ON CONFLICT DO NOTHING
            RETURNING nonce
            """,
            (device_id, user_id, device_nonce),
        )
        if await inserted.fetchone() is None:
            raise HTTPException(status_code=409, detail="replayed device nonce")
        await conn.execute("UPDATE devices SET last_seen_at=now() WHERE id=%s", (device_id,))

    return DeviceIdentity(user_id=user_id, device_id=device_id)
