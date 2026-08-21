# memory-sync server

Self-hosted sync backend for [memory-sync](../README.md): FastAPI + PostgreSQL 16
(RLS) + Caddy (HTTPS, zstd/gzip).

## Run

```bash
cp ../.env.example ../.env   # fill in real values
docker compose up -d         # from the repo root
curl -fsS https://your-domain.com/readyz
```

## Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /v1/auth/register` | Create account (invite code optional) |
| `POST /v1/auth/login` | Log in |
| `POST /v1/auth/refresh` | Rotate refresh token |
| `POST /v1/devices/register` | Bind a device Ed25519 key |
| `GET /v1/devices` | List devices |
| `POST /v1/sync/push` | Push a versioned document (signed) |
| `GET /v1/sync/pull` | Pull events after a seq cursor (signed) |
| `GET /v1/sync/bootstrap` | Initial snapshot (signed) |
| `POST /v1/sync/resolve` | Resolve a conflict (signed) |

Every sync/device call requires a Bearer access token plus the
`X-Device-Id` / `X-Device-Timestamp` / `X-Device-Nonce` /
`X-Device-Signature` headers (Ed25519).

## Development

```bash
python -m venv .venv
.venv/Scripts/pip install -r api/requirements.txt pytest httpx
.venv/Scripts/python -m pytest tests/
```

`tests/test_pure.py` exercises path validation, content hashing, the auth
limiter and the signature checks without a database.
