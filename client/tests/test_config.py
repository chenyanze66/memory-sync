"""Config save/load roundtrip, default path resolution, best-effort lockdown."""

import os
import stat

from memory_sync_client.config import (
    Config,
    default_config_path,
    load_config,
    save_config,
    secure_file,
)


def test_roundtrip_preserves_all_fields(tmp_path):
    path = tmp_path / "cfg.json"
    cfg = Config(
        server_url="https://api.example.com",
        email="a@b.c",
        display_name="Alice",
        device_id="dev-1",
        device_name="pc",
        public_key="pub",
        private_key="priv",
        access_token="at",
        refresh_token="rt",
        sync_root=str(tmp_path / "mem"),
        snapshot={"notes/a.md": "abc123"},
        versions={"notes/a.md": "v9"},
        pending_conflicts={"notes/b.md": "20260819T120000Z"},
        last_seq=42,
        last_sync_at="2026-08-19T12:00:00Z",
    )
    save_config(cfg, path)
    assert load_config(path) == cfg


def test_missing_file_yields_empty_config(tmp_path):
    cfg = load_config(tmp_path / "nope.json")
    assert cfg.device_id == ""
    assert cfg.email == ""
    assert cfg.snapshot == {}
    assert cfg.last_seq == 0


def test_corrupt_file_yields_empty_config(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_config(path) == Config()


def test_default_path_uses_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert default_config_path() == tmp_path / "memory-sync" / "config.json"


def test_default_path_falls_back_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr("memory_sync_client.config.Path.home", lambda: tmp_path)
    assert default_config_path() == tmp_path / ".memory-sync" / "config.json"


def test_save_creates_parent_directories(tmp_path):
    path = tmp_path / "deep" / "nest" / "cfg.json"
    save_config(Config(email="x@y.z"), path)
    assert load_config(path).email == "x@y.z"


def test_secure_file_is_best_effort_and_leaves_file_usable(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text("{}", encoding="utf-8")
    secure_file(path)  # must not raise even where icacls/chmod are unavailable
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "{}"


def test_atomic_save_replaces_previous_content(tmp_path):
    path = tmp_path / "cfg.json"
    save_config(Config(email="first@x.y"), path)
    save_config(Config(email="second@x.y"), path)
    assert load_config(path).email == "second@x.y"
    assert not path.with_name(path.name + ".tmp").exists()
