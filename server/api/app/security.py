import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import HTTPException, status

from .config import get_settings

password_hasher = PasswordHasher(time_cost=2, memory_cost=19_456, parallelism=1)

# Fixed verification target for missing/disabled accounts so login timing does
# not reveal whether an email is registered. Hashed once at import time, never
# per request.
DUMMY_PASSWORD_HASH = password_hasher.hash("memory-sync-timing-equalization-dummy")


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def create_access_token(user_id: UUID) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "type": "access",
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> UUID:
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
        if payload.get("type") != "access":
            raise ValueError("wrong token type")
        return UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid access token"
        ) from exc


def new_refresh_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(48)
    return token, hashlib.sha256(token.encode()).hexdigest()


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def decode_public_key(value: str) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="public_key must be valid base64") from exc
    if len(raw) != 32:
        raise HTTPException(status_code=422, detail="public_key must contain 32 raw Ed25519 bytes")
    return raw


def verify_device_signature(public_key: bytes, signature_b64: str, canonical: bytes) -> None:
    try:
        signature = base64.b64decode(signature_b64, validate=True)
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, canonical)
    except (ValueError, InvalidSignature) as exc:
        raise HTTPException(status_code=401, detail="invalid device signature") from exc
