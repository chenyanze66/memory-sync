"""Thin HTTP client over urllib with an injectable transport.

The default transport uses ``urllib.request``; tests inject a fake transport
so no network is ever touched. Signed requests carry the device headers and
sign the canonical bytes from :mod:`memory_sync_client.crypto`.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Protocol

from .crypto import canonical_bytes, sign


class ApiError(Exception):
    """A non-2xx response, or a transport failure with a status."""

    def __init__(
        self,
        status: int | None,
        detail: Any = None,
        *,
        method: str = "",
        url: str = "",
    ) -> None:
        super().__init__(f"{method} {url} -> HTTP {status}: {detail!r}")
        self.status = status
        self.detail = detail


class Transport(Protocol):
    """Request/response boundary; implement for tests or alternate stacks."""

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Return (status, response_headers, response_body)."""


class UrllibTransport:
    """Default transport backed by urllib.request."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[int, dict[str, str], bytes]:
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={k: v for k, v in headers.items() if v is not None},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()


class ApiClient:
    """FastAPI memory sync endpoints; all network goes through a Transport."""

    def __init__(self, server_url: str, transport: Transport | None = None) -> None:
        self.server_url = server_url.rstrip("/")
        self.transport: Transport = transport or UrllibTransport()

    # -- unsigned auth calls -------------------------------------------------

    def register(
        self, email: str, password: str, display_name: str, invite_code: str
    ) -> Any:
        body = json.dumps(
            {"email": email, "password": password, "display_name": display_name}
        ).encode("utf-8")
        return self._request(
            "POST",
            "/v1/auth/register",
            headers={"X-Invite-Code": invite_code},
            body=body,
        )

    def login(self, email: str, password: str) -> Any:
        body = json.dumps({"email": email, "password": password}).encode("utf-8")
        return self._request("POST", "/v1/auth/login", body=body)

    def refresh(self, refresh_token: str) -> Any:
        """Exchange a refresh token for a new TokenPair."""
        body = json.dumps({"refresh_token": refresh_token}).encode("utf-8")
        return self._request("POST", "/v1/auth/refresh", body=body)

    def register_device(
        self, name: str, platform: str, public_key: str, token: str
    ) -> Any:
        body = json.dumps(
            {"name": name, "platform": platform, "public_key": public_key}
        ).encode("utf-8")
        return self._request(
            "POST",
            "/v1/devices/register",
            headers={"Authorization": f"Bearer {token}"},
            body=body,
        )

    def list_devices(self, token: str) -> Any:
        return self._request(
            "GET", "/v1/devices", headers={"Authorization": f"Bearer {token}"}
        )

    # -- signed sync calls ---------------------------------------------------

    def signed_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: bytes | None = None,
        token: str,
        device_id: str,
        private_key: str,
    ) -> Any:
        query = urllib.parse.urlencode(params) if params else ""
        path_and_query = f"{path}?{query}" if query else path
        timestamp = int(time.time())
        nonce = str(uuid.uuid4())
        signature = sign(private_key, canonical_bytes(method, path_and_query, timestamp, nonce, body or b""))
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Device-Id": device_id,
            "X-Device-Timestamp": str(timestamp),
            "X-Device-Nonce": nonce,
            "X-Device-Signature": signature,
        }
        return self._request(method, path_and_query, headers=headers, body=body)

    def push(
        self,
        entry: dict[str, Any],
        token: str,
        device_id: str,
        private_key: str,
    ) -> Any:
        body = json.dumps(entry).encode("utf-8")
        return self.signed_request(
            "POST",
            "/v1/sync/push",
            body=body,
            token=token,
            device_id=device_id,
            private_key=private_key,
        )

    def pull(
        self,
        after_seq: int,
        token: str,
        device_id: str,
        private_key: str,
        limit: int = 2,
    ) -> Any:
        return self.signed_request(
            "GET",
            "/v1/sync/pull",
            params={"space": "user-global", "after_seq": after_seq, "limit": limit},
            token=token,
            device_id=device_id,
            private_key=private_key,
        )

    # -- internals -----------------------------------------------------------

    def _request(
        self,
        method: str,
        path_and_query: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> Any:
        url = self.server_url + path_and_query
        request_headers: dict[str, str] = {"Accept": "application/json"}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update({k: v for k, v in headers.items() if v is not None})
        status, _resp_headers, raw = self.transport.request(
            method, url, request_headers, body
        )
        detail: Any = None
        if raw:
            try:
                detail = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                detail = raw.decode("utf-8", "replace")
        if not 200 <= status < 300:
            raise ApiError(status, detail, method=method, url=url)
        return detail
