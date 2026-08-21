# memory-sync-client

Python CLI for [memory-sync](../README.md) - keeps your Markdown memory files
in sync across devices.

## Install

```bash
pip install ./client
```

Requires Python >= 3.10 and the `cryptography` package.

## Usage

```bash
memory-sync register --server https://your-domain.com --email you@example.com --display-name me
memory-sync login    --server https://your-domain.com --email you@example.com
memory-sync status
memory-sync sync ./notes
```

Config is stored per-user (Windows: `%APPDATA%\memory-sync\config.json`,
elsewhere: `~/.memory-sync/config.json`) with permissions locked to the
current user.

## How sync works

1. Pull pending server events (seq cursor) and apply them; conflicting server
   copies go to `conflicts/<UTC timestamp>/<path>` - local files are never
   overwritten.
2. Push local Markdown files whose normalized SHA-256 hash changed, tagged
   with the `base_version_id` they were edited from.
3. A stale base is rejected (409) and the client pulls again, so all devices
   converge.

See the [top-level README](../README.md) for the full protocol and security
model.

## Development

```bash
python -m venv .venv
.venv/Scripts/pip install cryptography pytest   # Windows
.venv/bin/pip install cryptography pytest        # macOS/Linux
.venv/Scripts/python -m pytest tests/
```

Tests use an injected fake transport - no network, no server needed.
