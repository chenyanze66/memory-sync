"""ApiClient request construction against a fake transport.

The fake transport captures every request; signed requests are verified by
recomputing the canonical bytes from the captured headers/body and checking
the signature with the public key.
"""

import json
import time
import uuid

import pytest

from memory_sync_client.api import ApiClient, ApiError
from memory_sync_client.crypto import canonical_bytes, generate_keypair, verify
from memory_sync_client.sync import build_push_entry, text_hash

BASE = "https://api.example.com"


class FakeTransport:
    def __init__(self, responses):
        self.calls = []
        self.responses = responses

    def request(self, method, url, headers, body):
        self.calls.append((method, url, dict(headers), body))
        key = (method, url)
        if key in self.responses:
            status, payload = self.responses[key]
            raw = json.dumps(payload).encode("utf-8")
            return status, {"Content-Type": "application/json"}, raw
        return 500, {}, b'{"detail": "no fake response"}'

    def last(self):
        return self.calls[-1]


def make_client(transport):
    return ApiClient(BASE, transport=transport)


def test_register_sends_expected_body_and_invite_header():
    transport = FakeTransport(
        {("POST", f"{BASE}/v1/auth/register"): (200, {"access_token": "at", "refresh_token": "rt"})}
    )
    client = make_client(transport)
    response = client.register("a@b.c", "pw", "Alice", "INV-1")
    assert response["access_token"] == "at"
    method, url, headers, body = transport.last()
    assert (method, url) == ("POST", f"{BASE}/v1/auth/register")
    assert headers["X-Invite-Code"] == "INV-1"
    assert headers["Content-Type"] == "application/json"
    assert json.loads(body) == {"email": "a@b.c", "password": "pw", "display_name": "Alice"}


def test_login_sends_expected_body():
    transport = FakeTransport({("POST", f"{BASE}/v1/auth/login"): (200, {"access_token": "at"})})
    client = make_client(transport)
    client.login("a@b.c", "pw")
    method, url, headers, body = transport.last()
    assert url == f"{BASE}/v1/auth/login"
    assert json.loads(body) == {"email": "a@b.c", "password": "pw"}


def test_register_device_sends_expected_body_and_bearer():
    transport = FakeTransport(
        {("POST", f"{BASE}/v1/devices/register"): (200, {"device_id": "dev-1", "name": "pc"})}
    )
    client = make_client(transport)
    response = client.register_device("pc", "windows", "pubkey", "tok")
    assert response["device_id"] == "dev-1"
    method, url, headers, body = transport.last()
    assert url == f"{BASE}/v1/devices/register"
    assert headers["Authorization"] == "Bearer tok"
    assert json.loads(body) == {"name": "pc", "platform": "windows", "public_key": "pubkey"}


def test_signed_push_headers_and_signature():
    public, private = generate_keypair()
    transport = FakeTransport({("POST", f"{BASE}/v1/sync/push"): (200, {"ok": True})})
    client = make_client(transport)
    entry = build_push_entry("notes/a.md", b"# hi", base_version_id="v1")
    client.push(entry, "tok", "dev-1", private)

    method, url, headers, body = transport.last()
    assert url == f"{BASE}/v1/sync/push"
    assert headers["Authorization"] == "Bearer tok"
    assert headers["X-Device-Id"] == "dev-1"
    timestamp = headers["X-Device-Timestamp"]
    assert timestamp.isdigit()
    assert abs(int(time.time()) - int(timestamp)) < 120
    uuid.UUID(headers["X-Device-Nonce"])  # must be a valid UUID
    canonical = canonical_bytes("POST", "/v1/sync/push", int(timestamp), headers["X-Device-Nonce"], body)
    assert verify(public, canonical, headers["X-Device-Signature"])

    payload = json.loads(body)
    assert payload["space"] == "user-global"
    assert payload["path"] == "notes/a.md"
    assert payload["base_version_id"] == "v1"
    assert payload["content"] == "# hi"  # raw text, not base64
    assert payload["content_hash"] == text_hash("# hi")  # server normalization
    assert payload["deleted"] is False
    uuid.UUID(payload["operation_id"])


def test_pull_url_and_parsing():
    public, private = generate_keypair()
    transport = FakeTransport(
        {
            (
                "GET",
                f"{BASE}/v1/sync/pull?space=user-global&after_seq=4&limit=2",
            ): (200, {"events": [{"path": "a.md"}], "next_seq": 6})
        }
    )
    client = make_client(transport)
    response = client.pull(4, "tok", "dev-1", private)
    assert response == {"events": [{"path": "a.md"}], "next_seq": 6}
    method, url, headers, body = transport.last()
    assert method == "GET"
    assert url == f"{BASE}/v1/sync/pull?space=user-global&after_seq=4&limit=2"
    assert body is None
    canonical = canonical_bytes("GET", "/v1/sync/pull?space=user-global&after_seq=4&limit=2", int(headers["X-Device-Timestamp"]), headers["X-Device-Nonce"], b"")
    assert verify(public, canonical, headers["X-Device-Signature"])


def test_refresh_sends_refresh_token_body():
    transport = FakeTransport(
        {("POST", f"{BASE}/v1/auth/refresh"): (200, {"access_token": "at2", "refresh_token": "rt2"})}
    )
    client = make_client(transport)
    response = client.refresh("rt-old")
    assert response["access_token"] == "at2"
    assert response["refresh_token"] == "rt2"
    method, url, headers, body = transport.last()
    assert url == f"{BASE}/v1/auth/refresh"
    assert json.loads(body) == {"refresh_token": "rt-old"}


def test_non_2xx_raises_api_error():
    transport = FakeTransport({("POST", f"{BASE}/v1/auth/login"): (401, {"detail": "bad creds"})})
    client = make_client(transport)
    with pytest.raises(ApiError) as excinfo:
        client.login("a@b.c", "pw")
    assert excinfo.value.status == 401
    assert excinfo.value.detail == {"detail": "bad creds"}
