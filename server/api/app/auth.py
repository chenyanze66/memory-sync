from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status

from .config import get_settings
from .db import transaction
from .dependencies import auth_rate_limit
from .schemas import LoginRequest, RefreshRequest, RegisterRequest, TokenPair
from .security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])


async def issue_tokens(conn, user_id) -> TokenPair:
    refresh_token, token_hash = new_refresh_token()
    expires_at = datetime.now(UTC) + timedelta(days=get_settings().refresh_token_days)
    await conn.execute(
        "INSERT INTO refresh_tokens(id,user_id,token_hash,expires_at) VALUES (%s,%s,%s,%s)",
        (uuid4(), user_id, token_hash, expires_at),
    )
    return TokenPair(access_token=create_access_token(user_id), refresh_token=refresh_token)


@router.post("/register", response_model=TokenPair, status_code=201)
async def register(
    payload: RegisterRequest,
    invite_code: str | None = Header(default=None, alias="X-Invite-Code"),
    _: None = Depends(auth_rate_limit),
):
    required = get_settings().registration_invite_code
    if required and not __import__("secrets").compare_digest(
        invite_code or "", required
    ):
        raise HTTPException(status_code=403, detail="invalid invite code")
    user_id = uuid4()
    try:
        async with transaction() as conn:
            await conn.execute(
                "INSERT INTO auth_accounts(id,email,password_hash) VALUES (%s,lower(%s),%s)",
                (user_id, str(payload.email), hash_password(payload.password)),
            )
            await conn.execute("SELECT set_config('app.user_id', %s, true)", (str(user_id),))
            await conn.execute(
                "INSERT INTO profiles(user_id,display_name) VALUES (%s,%s)",
                (user_id, payload.display_name),
            )
            await conn.execute(
                "INSERT INTO spaces(id,user_id,slug,kind,classification) VALUES (%s,%s,'user-global','user','P1')",
                (uuid4(), user_id),
            )
            return await issue_tokens(conn, user_id)
    except Exception as exc:
        if getattr(exc, "sqlstate", None) == "23505":
            raise HTTPException(status_code=409, detail="email already registered") from exc
        raise


@router.post("/login", response_model=TokenPair)
async def login(payload: LoginRequest, _: None = Depends(auth_rate_limit)):
    async with transaction() as conn:
        result = await conn.execute(
            "SELECT id,password_hash,disabled_at FROM auth_accounts WHERE email=lower(%s)",
            (str(payload.email),),
        )
        row = await result.fetchone()
        if row is None or row[2] is not None:
            # Burn one Argon2 verify so missing/disabled accounts cost the same
            # as a failed password check (no account-enumeration timing oracle).
            verify_password(DUMMY_PASSWORD_HASH, payload.password)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials"
            )
        if not verify_password(row[1], payload.password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials"
            )
        return await issue_tokens(conn, row[0])


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, _: None = Depends(auth_rate_limit)):
    # Defensive: any token-shaped input must produce a clean 401, never a 500.
    token = payload.refresh_token.strip()
    if not (32 <= len(token) <= 512) or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in token):
        raise HTTPException(status_code=401, detail="invalid refresh token")
    token_hash = hash_refresh_token(token)
    async with transaction() as conn:
        result = await conn.execute(
            """
            SELECT t.id,t.user_id FROM refresh_tokens t
            JOIN auth_accounts a ON a.id=t.user_id
            WHERE t.token_hash=%s AND t.revoked_at IS NULL AND t.expires_at>now()
              AND a.disabled_at IS NULL
            FOR UPDATE
            """,
            (token_hash,),
        )
        row = await result.fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="invalid refresh token")
        await conn.execute("UPDATE refresh_tokens SET revoked_at=now() WHERE id=%s", (row[0],))
        return await issue_tokens(conn, row[1])
