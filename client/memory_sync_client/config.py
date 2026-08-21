"""Local configuration storage.

Config lives in a single JSON file (default
``%APPDATA%\\memory-sync\\config.json``) holding the server URL, account,
device keys, tokens, and the last-known sync state. The file is written
atomically and then locked down: mode 0600 on POSIX, and on Windows a
best-effort ``icacls /inheritance:r /grant:r "%USERNAME%:F"`` so only the
current user can read it.
"""

from __future__ import annotations

import getpass
import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Config:
    server_url: str = ""
    email: str = ""
    display_name: str = ""
    device_id: str = ""
    device_name: str = ""
    public_key: str = ""
    private_key: str = ""
    access_token: str = ""
    refresh_token: str = ""
    sync_root: str = ""
    # rel_path -> sha256 of the last content this client treated as current
    snapshot: dict[str, str] = field(default_factory=dict)
    # rel_path -> last seen server version id (used as push base_version_id)
    versions: dict[str, str] = field(default_factory=dict)
    # rel_path -> UTC timestamp of the conflict directory holding the server copy
    pending_conflicts: dict[str, str] = field(default_factory=dict)
    last_seq: int = 0
    last_sync_at: str = ""


def default_config_path() -> Path:
    """Return the per-user config path (Windows-first)."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "memory-sync" / "config.json"
    return Path.home() / ".memory-sync" / "config.json"


def resolve_config_path(path: str | Path | None) -> Path:
    return Path(path) if path else default_config_path()


def load_config(path: str | Path | None = None) -> Config:
    """Load a config file; a missing or corrupt file yields an empty Config."""
    config_path = resolve_config_path(path)
    config = Config()
    if not config_path.exists():
        return config
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return config
    if not isinstance(data, dict):
        return config
    for key, default in asdict(config).items():
        if key in data and isinstance(data[key], type(default)):
            setattr(config, key, data[key])
    if not isinstance(config.snapshot, dict):
        config.snapshot = {}
    if not isinstance(config.versions, dict):
        config.versions = {}
    if not isinstance(config.pending_conflicts, dict):
        config.pending_conflicts = {}
    return config


def save_config(config: Config, path: str | Path | None = None) -> None:
    """Atomically persist the config and restrict file permissions."""
    config_path = resolve_config_path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_name(config_path.name + ".tmp")
    tmp.write_text(
        json.dumps(asdict(config), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    secure_file(tmp)
    os.replace(tmp, config_path)
    secure_file(config_path)


def secure_file(path: str | Path) -> None:
    """Best-effort user-only restriction; never raises.

    On Windows this shells out to ``icacls``; on POSIX it applies mode 0600.
    Failures (e.g. no icacls, sandboxed environments) are ignored so the
    client still works where ACL tooling is unavailable.
    """
    config_path = Path(path)
    try:
        if os.name == "nt":
            subprocess.run(
                [
                    "icacls",
                    str(config_path),
                    "/inheritance:r",
                    "/grant:r",
                    f"{getpass.getuser()}:F",
                ],
                capture_output=True,
                check=False,
            )
        else:
            os.chmod(config_path, 0o600)
    except (OSError, subprocess.SubprocessError):
        pass
