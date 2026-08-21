"""CLI wiring: register/login/status/sync through a fake transport."""

import json

from memory_sync_client.cli import main
from memory_sync_client.config import load_config
from memory_sync_client.crypto import generate_keypair
from memory_sync_client.sync import text_hash

BASE = "https://api.example.com"


class FakeTransport:
    def __init__(self, responses):
        self.calls = []
        self.responses = responses

    def request(self, method, url, headers, body):
        self.calls.append((method, url, dict(headers), body))
        key = (method, url)
        if key in self.responses:
            value = self.responses[key]
            # A list value is a queue: first call pops, the last entry stays.
            if isinstance(value, list):
                if len(value) > 1:
                    value = value.pop(0)
                else:
                    value = value[0]
            status, payload = value
            return status, {"Content-Type": "application/json"}, json.dumps(payload).encode()
        return 404, {}, b'{"detail": "no fake response"}'


def test_register_writes_config_without_echoing_password(tmp_path, capsys):
    config_path = tmp_path / "cfg.json"
    transport = FakeTransport(
        {
            ("POST", f"{BASE}/v1/auth/register"): (200, {"access_token": "at", "refresh_token": "rt"}),
            ("POST", f"{BASE}/v1/devices/register"): (200, {"device_id": "dev-1", "name": "pc"}),
        }
    )
    rc = main(
        [
            "register",
            "--config", str(config_path),
            "--server", BASE,
            "--email", "a@b.c",
            "--password", "s3cret!",
            "--display-name", "Alice",
            "--invite-code", "INV-1",
        ],
        transport=transport,
    )
    assert rc == 0
    cfg = load_config(config_path)
    assert cfg.email == "a@b.c"
    assert cfg.device_id == "dev-1"
    assert cfg.access_token == "at"
    assert cfg.refresh_token == "rt"
    assert cfg.public_key and cfg.private_key
    out = capsys.readouterr().out
    assert "s3cret!" not in out


def test_login_uses_stored_server_and_persists_device(tmp_path):
    config_path = tmp_path / "cfg.json"
    from memory_sync_client.config import Config, save_config

    save_config(Config(server_url=BASE), config_path)
    transport = FakeTransport(
        {
            ("POST", f"{BASE}/v1/auth/login"): (200, {"access_token": "at2"}),
            ("POST", f"{BASE}/v1/devices/register"): (200, {"device_id": "dev-2", "name": "pc"}),
        }
    )
    rc = main(
        ["login", "--config", str(config_path), "--email", "a@b.c", "--password", "pw"],
        transport=transport,
    )
    assert rc == 0
    cfg = load_config(config_path)
    assert cfg.access_token == "at2"
    assert cfg.device_id == "dev-2"


def test_status_prints_no_secrets(tmp_path, capsys):
    config_path = tmp_path / "cfg.json"
    from memory_sync_client.config import Config, save_config

    save_config(
        Config(
            server_url=BASE,
            email="a@b.c",
            display_name="Alice",
            device_id="dev-1",
            device_name="pc",
            public_key="cHVia2V5",
            private_key="c2VjcmV0a2V5",
            access_token="at-secret",
            refresh_token="rt-secret",
            last_seq=7,
        ),
        config_path,
    )
    rc = main(["status", "--config", str(config_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "at-secret" not in out
    assert "rt-secret" not in out
    assert "c2VjcmV0a2V5" not in out
    assert "a@b.c" in out
    assert "dev-1" in out


def test_sync_end_to_end(tmp_path, capsys):
    sync_root = tmp_path / "mem"
    sync_root.mkdir()
    (sync_root / "a.md").write_bytes(b"hello")
    config_path = tmp_path / "cfg.json"
    from memory_sync_client.config import Config, save_config

    _, private_key = generate_keypair()
    save_config(
        Config(
            server_url=BASE,
            email="a@b.c",
            device_id="dev-1",
            private_key=private_key,
            access_token="tok",
        ),
        config_path,
    )
    transport = FakeTransport(
        {
            ("GET", f"{BASE}/v1/sync/pull?space=user-global&after_seq=0&limit=2"): (
                200,
                {"events": [], "next_seq": 0},
            ),
            ("POST", f"{BASE}/v1/sync/push"): (200, {"ok": True}),
        }
    )
    rc = main(
        ["sync", "--config", str(config_path), "--sync-root", str(sync_root)],
        transport=transport,
    )
    assert rc == 0
    cfg = load_config(config_path)
    assert cfg.snapshot["a.md"] == text_hash("hello")
    out = capsys.readouterr().out
    assert "1 pushed" in out


def test_sync_exits_nonzero_on_conflict(tmp_path, capsys):
    sync_root = tmp_path / "mem"
    sync_root.mkdir()
    target = sync_root / "a.md"
    target.write_bytes(b"local edit")
    config_path = tmp_path / "cfg.json"
    from memory_sync_client.config import Config, save_config

    _, private_key = generate_keypair()
    save_config(
        Config(
            server_url=BASE,
            email="a@b.c",
            device_id="dev-1",
            private_key=private_key,
            access_token="tok",
            snapshot={"a.md": text_hash("old agreed")},
        ),
        config_path,
    )
    server = "server edit"
    event = {
        "path": "a.md",
        "content": server,  # raw text, as the server sends it
        "content_hash": text_hash(server),
        "version_id": "v1",
        "deleted": False,
    }
    transport = FakeTransport(
        {
            ("GET", f"{BASE}/v1/sync/pull?space=user-global&after_seq=0&limit=2"): (
                200,
                {"events": [event], "next_seq": 1},
            ),
            ("GET", f"{BASE}/v1/sync/pull?space=user-global&after_seq=1&limit=2"): (
                200,
                {"events": [], "next_seq": 1},
            ),
            ("POST", f"{BASE}/v1/sync/push"): (200, {"ok": True}),
        }
    )
    rc = main(
        ["sync", "--config", str(config_path), "--sync-root", str(sync_root)],
        transport=transport,
    )
    assert rc == 1
    assert target.read_bytes() == b"local edit"
    out = capsys.readouterr().out
    assert "conflict: a.md" in out


def test_sync_requires_login(tmp_path, capsys):
    config_path = tmp_path / "cfg.json"
    rc = main(["sync", "--config", str(config_path)], transport=FakeTransport({}))
    assert rc == 1
    assert "not registered" in capsys.readouterr().err


def test_sync_refreshes_expired_token_once(tmp_path, capsys):
    sync_root = tmp_path / "mem"
    sync_root.mkdir()
    (sync_root / "a.md").write_bytes(b"hello")
    config_path = tmp_path / "cfg.json"
    from memory_sync_client.config import Config, save_config

    _, private_key = generate_keypair()
    save_config(
        Config(
            server_url=BASE,
            email="a@b.c",
            device_id="dev-1",
            private_key=private_key,
            access_token="tok-expired",
            refresh_token="rt-old",
        ),
        config_path,
    )
    pull_url = f"{BASE}/v1/sync/pull?space=user-global&after_seq=0&limit=2"
    transport = FakeTransport(
        {
            ("GET", pull_url): [
                (401, {"detail": "token expired"}),
                (200, {"events": [], "next_seq": 0}),
            ],
            ("POST", f"{BASE}/v1/auth/refresh"): (
                200,
                {"access_token": "tok-new", "refresh_token": "rt-new"},
            ),
            ("POST", f"{BASE}/v1/sync/push"): (200, {"ok": True}),
        }
    )
    rc = main(
        ["sync", "--config", str(config_path), "--sync-root", str(sync_root)],
        transport=transport,
    )
    assert rc == 0
    cfg = load_config(config_path)
    assert cfg.access_token == "tok-new"
    assert cfg.refresh_token == "rt-new"
    refresh_call = next(
        c for c in transport.calls if c[1] == f"{BASE}/v1/auth/refresh"
    )
    assert json.loads(refresh_call[3]) == {"refresh_token": "rt-old"}


def test_sync_refresh_rejected_returns_login_hint(tmp_path, capsys):
    sync_root = tmp_path / "mem"
    sync_root.mkdir()
    (sync_root / "a.md").write_bytes(b"hello")
    config_path = tmp_path / "cfg.json"
    from memory_sync_client.config import Config, save_config

    _, private_key = generate_keypair()
    save_config(
        Config(
            server_url=BASE,
            email="a@b.c",
            device_id="dev-1",
            private_key=private_key,
            access_token="tok-expired",
            refresh_token="rt-revoked",
        ),
        config_path,
    )
    pull_url = f"{BASE}/v1/sync/pull?space=user-global&after_seq=0&limit=2"
    transport = FakeTransport(
        {
            ("GET", pull_url): (401, {"detail": "token expired"}),
            ("POST", f"{BASE}/v1/auth/refresh"): (401, {"detail": "refresh rejected"}),
        }
    )
    rc = main(
        ["sync", "--config", str(config_path), "--sync-root", str(sync_root)],
        transport=transport,
    )
    assert rc == 1
    assert "refresh token rejected" in capsys.readouterr().err


def test_sync_reports_non_utf8_file(tmp_path, capsys):
    sync_root = tmp_path / "mem"
    sync_root.mkdir()
    (sync_root / "bad.md").write_bytes(b"\xff\xfe not text")
    config_path = tmp_path / "cfg.json"
    from memory_sync_client.config import Config, save_config

    _, private_key = generate_keypair()
    save_config(
        Config(
            server_url=BASE,
            email="a@b.c",
            device_id="dev-1",
            private_key=private_key,
            access_token="tok",
        ),
        config_path,
    )
    pull_url = f"{BASE}/v1/sync/pull?space=user-global&after_seq=0&limit=2"
    transport = FakeTransport(
        {
            ("GET", pull_url): (200, {"events": [], "next_seq": 0}),
        }
    )
    rc = main(
        ["sync", "--config", str(config_path), "--sync-root", str(sync_root)],
        transport=transport,
    )
    assert rc == 0
    err = capsys.readouterr().err
    assert "skipped (not UTF-8 text): bad.md" in err
