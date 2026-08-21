import base64
import hashlib
import os
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1] / "api"))
os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@127.0.0.1:5432/unused")
os.environ.setdefault("JWT_SECRET", "x" * 64)
os.environ.setdefault("REGISTRATION_INVITE_CODE", "y" * 32)

from app.dependencies import AuthLimiter, client_ip_from_xff
from app.main import app
from app.security import decode_public_key, verify_device_signature
from app.sync import normalize_path, verify_content


@pytest.mark.parametrize("path", ["memory/CURRENT.md", "daily/2026-08-19.md", "AGENTS.md"])
def test_valid_paths(path):
    assert normalize_path(path) == path


@pytest.mark.parametrize("path", ["/etc/passwd", "../secret", "a//b", "a\\b", "./a"])
def test_invalid_paths(path):
    with pytest.raises(HTTPException):
        normalize_path(path)


def test_content_hash_and_lf_normalization(monkeypatch):
    raw = b"a\nb\n"
    value = verify_content("a\r\nb\r\n", hashlib.sha256(raw).hexdigest(), False)
    assert value == raw


def test_ed25519_signature():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes_raw()
    encoded = base64.b64encode(public).decode()
    canonical = b"POST\n/v1/sync/push\n1\nnonce\nhash"
    signature = base64.b64encode(private.sign(canonical)).decode()
    verify_device_signature(decode_public_key(encoded), signature, canonical)
    with pytest.raises(HTTPException):
        verify_device_signature(public, signature, canonical + b"x")


def test_request_body_limit_rejects_before_routing():
    client = TestClient(app)
    response = client.post("/not-a-route", content=b"x" * 1_258_292)
    assert response.status_code == 413


def test_request_body_at_limit_passes_middleware():
    client = TestClient(app)
    response = client.post("/not-a-route", content=b"x" * 1_258_291)
    assert response.status_code == 404


def test_client_ip_uses_trusted_rightmost_xff():
    assert client_ip_from_xff("203.0.113.9", "10.0.0.2") == "203.0.113.9"
    # Caddy overwrites X-Forwarded-For, so a spoofed left value is ignored.
    assert client_ip_from_xff("198.51.100.7, 203.0.113.9", "10.0.0.2") == "203.0.113.9"
    assert client_ip_from_xff("  203.0.113.9  ", "10.0.0.2") == "203.0.113.9"
    assert client_ip_from_xff("", "10.0.0.2") == "10.0.0.2"
    assert client_ip_from_xff(None, "10.0.0.2") == "10.0.0.2"
    assert client_ip_from_xff(None, None) == "unknown"


def test_limiter_blocks_after_attempt_cap_and_recovers():
    limiter = AuthLimiter(max_attempts=3, window_seconds=60, max_keys=16)
    now = 1000.0
    for _ in range(3):
        assert limiter.allow("203.0.113.9", now) is True
    assert limiter.allow("203.0.113.9", now) is False
    # Window elapsed -> attempts are accepted again.
    assert limiter.allow("203.0.113.9", now + 61) is True


def test_limiter_evicts_lru_when_key_table_full():
    limiter = AuthLimiter(max_attempts=3, window_seconds=300, max_keys=3)
    now = 1000.0
    for key in ("a", "b", "c"):
        assert limiter.allow(key, now) is True
    assert limiter.key_count == 3
    limiter.allow("a", now)  # refresh "a" so "b" becomes the LRU key
    assert limiter.allow("d", now) is True
    assert limiter.key_count == 3
    # "b" was evicted, so its counter reset.
    for _ in range(3):
        assert limiter.allow("b", now) is True
    assert limiter.allow("b", now) is False


def test_limiter_purges_stale_keys_when_full():
    limiter = AuthLimiter(max_attempts=1, window_seconds=60, max_keys=2)
    now = 1000.0
    limiter.allow("stale", now)
    limiter.allow("other", now)
    # Both tracked keys are fully expired; a new key purges them before evicting.
    assert limiter.allow("fresh", now + 61) is True
    assert limiter.key_count == 1
    assert limiter.allow("other", now + 61) is True
