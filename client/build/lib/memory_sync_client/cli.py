"""argparse CLI: register / login / status / sync.

Passwords come from ``--password``, the ``MEMORY_SYNC_PASSWORD`` env var, or
an interactive ``getpass`` prompt — never echoed and never printed. Tests
inject a fake transport through ``main(..., transport=...)``.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import os
import platform
import sys
from pathlib import Path
from typing import Any, Sequence

from .api import ApiClient, ApiError, Transport
from .config import Config, default_config_path, load_config, save_config
from .crypto import generate_keypair
from .sync import SyncEngine, discover_markdown

SECRET_ENV = "MEMORY_SYNC_PASSWORD"


def _password(args: argparse.Namespace) -> str:
    if args.password:
        return args.password
    env = os.environ.get(SECRET_ENV)
    if env:
        return env
    return getpass.getpass("Password: ")


def _make_client(args: argparse.Namespace, server_url: str) -> ApiClient:
    transport = getattr(args, "_transport", None)
    return ApiClient(server_url, transport=transport)


def _fingerprint(public_key_b64: str) -> str:
    if not public_key_b64:
        return "(none)"
    try:
        raw = base64.b64decode(public_key_b64)
    except (ValueError, TypeError):
        return "(invalid)"
    return hashlib.sha256(raw).hexdigest()[:16]


def _cmd_register(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    server_url = args.server or cfg.server_url
    if not server_url:
        print("error: --server URL is required (or set one in the config)", file=sys.stderr)
        return 2
    password = _password(args)
    api = _make_client(args, server_url)
    response = api.register(args.email, password, args.display_name, args.invite_code)
    cfg.server_url = server_url
    cfg.email = args.email
    cfg.display_name = args.display_name
    cfg.access_token = response.get("access_token") or ""
    cfg.refresh_token = response.get("refresh_token") or ""
    if not cfg.private_key:
        public_key, private_key = generate_keypair()
        cfg.public_key, cfg.private_key = public_key, private_key
    if not cfg.device_id:
        device_name = args.device_name or platform.node() or "windows-device"
        device = api.register_device(
            device_name, "windows", cfg.public_key, cfg.access_token
        )
        cfg.device_id = device.get("device_id") or device.get("id") or ""
        cfg.device_name = device.get("name") or device_name
    save_config(cfg, args.config)
    print(f"registered {cfg.email} on device {cfg.device_name or cfg.device_id}")
    return 0


def _cmd_login(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    server_url = args.server or cfg.server_url
    if not server_url:
        print("error: --server URL is required (or set one in the config)", file=sys.stderr)
        return 2
    password = _password(args)
    api = _make_client(args, server_url)
    response = api.login(args.email, password)
    cfg.server_url = server_url
    cfg.email = args.email
    cfg.access_token = response.get("access_token") or ""
    cfg.refresh_token = response.get("refresh_token") or ""
    if not cfg.private_key:
        public_key, private_key = generate_keypair()
        cfg.public_key, cfg.private_key = public_key, private_key
    if not cfg.device_id:
        device_name = args.device_name or platform.node() or "windows-device"
        device = api.register_device(
            device_name, "windows", cfg.public_key, cfg.access_token
        )
        cfg.device_id = device.get("device_id") or device.get("id") or ""
        cfg.device_name = device.get("name") or device_name
    save_config(cfg, args.config)
    print(f"logged in as {cfg.email}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    sync_root = args.sync_root or cfg.sync_root
    md_count = len(discover_markdown(sync_root)) if sync_root else 0
    print(f"server:        {cfg.server_url or '(not set)'}")
    print(f"email:         {cfg.email or '(not set)'}")
    print(f"display name:  {cfg.display_name or '(not set)'}")
    print(f"device:        {cfg.device_name or '(unnamed)'} ({cfg.device_id or 'no id'})")
    print(f"device key:    {_fingerprint(cfg.public_key)}")
    print(f"sync root:     {sync_root or '(not set)'}")
    print(f"last sync:     {cfg.last_sync_at or '(never)'}")
    print(f"last seq:      {cfg.last_seq}")
    print(f"local markdown:{md_count}")
    print(f"tracked files: {len(cfg.snapshot)}")
    print(f"conflicts:     {len(cfg.pending_conflicts)}")
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    if not (cfg.access_token and cfg.device_id and cfg.private_key):
        print("error: not registered; run 'memory-sync register' or 'memory-sync login'", file=sys.stderr)
        return 1
    sync_root = Path(args.sync_root) if args.sync_root else (
        Path(cfg.sync_root) if cfg.sync_root else Path.cwd()
    )
    if args.sync_root:
        cfg.sync_root = str(sync_root)
    api = _make_client(args, cfg.server_url)
    try:
        result = _run_sync(cfg, api, sync_root, args.limit)
    except ApiError as exc:
        if exc.status != 401 or not cfg.refresh_token:
            raise
        # Access token expired: try one refresh with the stored refresh token.
        try:
            response = api.refresh(cfg.refresh_token)
        except ApiError as refresh_error:
            if refresh_error.status == 401:
                print("error: refresh token rejected; run 'memory-sync login'", file=sys.stderr)
                return 1
            raise
        cfg.access_token = response.get("access_token") or cfg.access_token
        new_refresh = response.get("refresh_token")
        if new_refresh:
            cfg.refresh_token = new_refresh
        save_config(cfg, args.config)
        result = _run_sync(cfg, _make_client(args, cfg.server_url), sync_root, args.limit)

    save_config(cfg, args.config)
    pull = result["pull"]
    push = result["push"]
    print(f"pull: {pull['applied']} applied, {len(pull['conflicts'])} conflicted (next_seq={pull['next_seq']})")
    print(f"push: {len(push['pushed'])} pushed, {push['unchanged']} unchanged, {len(push['conflicts'])} conflicted")
    for path in push.get("invalid", []):
        print(f"skipped (not UTF-8 text): {path}", file=sys.stderr)
    all_conflicts = sorted(set(pull["conflicts"]) | set(push["conflicts"]))
    if all_conflicts:
        for path in all_conflicts:
            print(f"conflict: {path} (server copy under <sync-root>/conflicts/)")
        return 1
    return 0


def _run_sync(cfg: Config, api: ApiClient, sync_root: Path, limit: int) -> dict[str, Any]:
    engine = SyncEngine(cfg, api, sync_root, limit=limit)
    return engine.run()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory-sync",
        description="Windows-friendly CLI client for the FastAPI memory sync API.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help=f"config file path (default: {default_config_path()})",
    )
    parser.add_argument(
        "--server",
        default=None,
        help="API base URL, e.g. https://api.example.com",
    )
    parser.add_argument(
        "--sync-root",
        default=None,
        help="folder containing Markdown files to sync",
    )
    parser.add_argument("--device-name", default=None, help="device name to register")

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--config", default=None, help="config file path")
        subparser.add_argument("--server", default=None, help="API base URL")
        subparser.add_argument("--sync-root", default=None, help="folder containing Markdown files to sync")
        subparser.add_argument("--device-name", default=None, help="device name to register")

    sub = parser.add_subparsers(dest="command", required=True)

    p_register = sub.add_parser("register", help="create an account and register this device")
    add_common(p_register)
    p_register.add_argument("--email", required=True)
    p_register.add_argument("--password", default=None, help=f"password (default: ${SECRET_ENV} env or prompt)")
    p_register.add_argument("--display-name", required=True)
    p_register.add_argument("--invite-code", required=True)
    p_register.set_defaults(handler=_cmd_register)

    p_login = sub.add_parser("login", help="log in and (re)register this device")
    add_common(p_login)
    p_login.add_argument("--email", required=True)
    p_login.add_argument("--password", default=None, help=f"password (default: ${SECRET_ENV} env or prompt)")
    p_login.set_defaults(handler=_cmd_login)

    p_status = sub.add_parser("status", help="show local config summary")
    add_common(p_status)
    p_status.set_defaults(handler=_cmd_status)

    p_sync = sub.add_parser("sync", help="pull pending events, then push local changes")
    add_common(p_sync)
    p_sync.add_argument("--limit", type=int, default=2, help="pull page size (default: 2)")
    p_sync.set_defaults(handler=_cmd_sync)

    return parser


def main(argv: Sequence[str] | None = None, *, transport: Transport | None = None) -> int:
    """Entry point; returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if transport is not None:
        args._transport = transport
    try:
        return int(args.handler(args))
    except ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if exc.status == 401:
            print("hint: run 'memory-sync login' to refresh your session", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
