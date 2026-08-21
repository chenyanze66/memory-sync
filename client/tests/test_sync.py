"""Sync engine: Markdown discovery, pull application, conflicts, pushes."""

import json
from datetime import datetime, timezone

import pytest

from memory_sync_client.api import ApiClient, ApiError
from memory_sync_client.config import Config
from memory_sync_client.crypto import sha256_hex
from memory_sync_client.sync import (
    SyncEngine,
    build_push_entry,
    discover_markdown,
    text_hash,
)

FIXED_CLOCK = lambda: datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
CONFLICT_TS = "20260819T120000Z"


def event(path, content: bytes, version_id="v1", deleted=False):
    """Server-style event: ``content`` is raw text (never base64)."""
    text = content.decode("utf-8")
    return {
        "path": path,
        "content": None if deleted else text,
        "content_hash": "" if deleted else text_hash(text),
        "version_id": version_id,
        "deleted": deleted,
    }


class StubApi:
    """Minimal ApiClient stand-in: returns canned pull responses and records pushes."""

    def __init__(self, pull_responses=None):
        self.pull_responses = list(pull_responses or [])
        self.pushes = []
        self.push_conflict_paths = set()

    def pull(self, after_seq, token, device_id, private_key, limit=2):
        if not self.pull_responses:
            return {"events": [], "next_seq": after_seq}
        return self.pull_responses.pop(0)

    def push(self, entry, token, device_id, private_key):
        if entry["path"] in self.push_conflict_paths:
            raise ApiError(409, {"detail": "version conflict"})
        self.pushes.append(json.loads(json.dumps(entry)))
        return {"ok": True}


def make_engine(tmp_path, config=None, api=None, **kwargs):
    sync_root = tmp_path / "sync"
    sync_root.mkdir()
    cfg = config or Config(access_token="tok", device_id="dev-1", private_key="priv")
    return SyncEngine(cfg, api or StubApi(), sync_root, clock=FIXED_CLOCK, **kwargs)


# -- discovery ---------------------------------------------------------------


def test_discover_markdown_filters(tmp_path):
    root = tmp_path / "sync"
    root.mkdir()
    (root / "a.md").write_text("a")
    (root / "notes.txt").write_text("nope")
    (root / ".git").mkdir()
    (root / ".git" / "hidden.md").write_text("x")
    (root / "sub").mkdir()
    (root / "sub" / "b.md").write_text("b")
    (root / "conflicts").mkdir()
    (root / "conflicts" / "old.md").write_text("c")
    assert discover_markdown(root) == ["a.md", "sub/b.md"]


def test_discover_markdown_case_insensitive_extension(tmp_path):
    root = tmp_path / "sync"
    root.mkdir()
    (root / "Upper.MD").write_text("x")
    (root / "Lower.md").write_text("y")
    (root / "not.md.bak").write_text("z")
    assert discover_markdown(root) == ["Lower.md", "Upper.MD"]


# -- pull application --------------------------------------------------------


def test_pull_applies_clean_event(tmp_path):
    engine = make_engine(tmp_path)
    content = b"# fresh note"
    engine.api.pull_responses = [{"events": [event("notes/a.md", content)], "next_seq": 3}]
    result = engine.pull()
    assert result["applied"] == 1
    assert result["conflicts"] == []
    assert result["next_seq"] == 3
    assert (engine.sync_root / "notes" / "a.md").read_bytes() == content
    assert engine.config.snapshot["notes/a.md"] == sha256_hex(content)
    assert engine.config.versions["notes/a.md"] == "v1"
    assert engine.config.last_seq == 3
    assert engine.config.last_sync_at == CONFLICT_TS


def test_pull_ignores_empty_path_events(tmp_path):
    engine = make_engine(tmp_path)
    engine.api.pull_responses = [{"events": [{"seq": 1}], "next_seq": 1}]
    result = engine.pull()
    assert result["applied"] == 0


def test_pull_keeps_clean_local_file_untouched(tmp_path):
    engine = make_engine(tmp_path)
    content = b"server content"
    engine.api.pull_responses = [{"events": [event("notes/a.md", content)], "next_seq": 1}]
    engine.pull()
    assert (engine.sync_root / "notes" / "a.md").read_bytes() == content


def test_pull_conflict_preserves_local_and_writes_conflict_copy(tmp_path):
    engine = make_engine(tmp_path)
    target = engine.sync_root / "notes" / "a.md"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"local edit")
    local_sha = sha256_hex(b"local edit")
    engine.config.snapshot["notes/a.md"] = sha256_hex(b"old agreed")
    server = b"server edit"
    engine.api.pull_responses = [{"events": [event("notes/a.md", server)], "next_seq": 1}]

    result = engine.pull()
    assert result["conflicts"] == ["notes/a.md"]
    assert target.read_bytes() == b"local edit"  # never overwritten
    conflict_copy = engine.sync_root / "conflicts" / CONFLICT_TS / "notes" / "a.md"
    assert conflict_copy.read_bytes() == server
    assert engine.config.pending_conflicts["notes/a.md"] == CONFLICT_TS
    assert engine.config.snapshot["notes/a.md"] == local_sha


def test_pull_conflict_is_idempotent(tmp_path):
    engine = make_engine(tmp_path)
    target = engine.sync_root / "notes" / "a.md"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"local edit")
    engine.config.snapshot["notes/a.md"] = sha256_hex(b"old agreed")
    server = b"server edit"
    pull_response = {"events": [event("notes/a.md", server)], "next_seq": 1}
    engine.api.pull_responses = [pull_response]
    engine.pull()
    assert len(list((engine.sync_root / "conflicts").iterdir())) == 1

    # A second pull with the same event must not create a second conflict dir.
    engine.api.pull_responses = [pull_response]
    result = engine.pull()
    assert result["conflicts"] == ["notes/a.md"]
    assert len(list((engine.sync_root / "conflicts").iterdir())) == 1
    assert target.read_bytes() == b"local edit"


def test_pull_conflict_resolves_when_hashes_agree(tmp_path):
    engine = make_engine(tmp_path)
    target = engine.sync_root / "notes" / "a.md"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"local edit")
    engine.config.snapshot["notes/a.md"] = sha256_hex(b"old agreed")
    engine.api.pull_responses = [{"events": [event("notes/a.md", b"server edit")], "next_seq": 1}]
    engine.pull()
    assert "notes/a.md" in engine.config.pending_conflicts

    # User adopts the server copy; the same event now matches local content.
    target.write_bytes(b"server edit")
    engine.api.pull_responses = [{"events": [event("notes/a.md", b"server edit")], "next_seq": 2}]
    result = engine.pull()
    assert result["conflicts"] == []
    assert engine.config.pending_conflicts == {}
    assert engine.config.snapshot["notes/a.md"] == sha256_hex(b"server edit")


def test_pull_delete_removes_clean_file(tmp_path):
    engine = make_engine(tmp_path)
    target = engine.sync_root / "notes" / "a.md"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"content")
    engine.config.snapshot["notes/a.md"] = sha256_hex(b"content")
    engine.api.pull_responses = [{"events": [event("notes/a.md", b"", deleted=True)], "next_seq": 2}]
    result = engine.pull()
    assert result["applied"] == 1
    assert not target.exists()
    assert "notes/a.md" not in engine.config.snapshot


def test_pull_delete_conflict_preserves_local(tmp_path):
    engine = make_engine(tmp_path)
    target = engine.sync_root / "notes" / "a.md"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"local edits")
    engine.config.snapshot["notes/a.md"] = sha256_hex(b"old agreed")
    engine.api.pull_responses = [{"events": [event("notes/a.md", b"", deleted=True)], "next_seq": 2}]
    result = engine.pull()
    assert result["conflicts"] == ["notes/a.md"]
    assert target.exists()
    copy = engine.sync_root / "conflicts" / CONFLICT_TS / "notes" / "a.md"
    assert copy.read_bytes() == b"local edits"


def test_pull_conflict_preserves_untracked_local_file(tmp_path):
    """Two devices independently create the same path: a never-tracked local
    file must be preserved and the server copy diverted into conflicts/."""
    engine = make_engine(tmp_path)
    target = engine.sync_root / "notes" / "todo.md"
    target.parent.mkdir(parents=True)
    local = b"# my todo from device A"
    target.write_bytes(local)
    server = b"# todo from device B"
    engine.api.pull_responses = [{"events": [event("notes/todo.md", server)], "next_seq": 1}]

    result = engine.pull()
    assert result["conflicts"] == ["notes/todo.md"]
    assert target.read_bytes() == local  # local bytes untouched
    conflict_copy = engine.sync_root / "conflicts" / CONFLICT_TS / "notes" / "todo.md"
    assert conflict_copy.read_bytes() == server
    assert engine.config.pending_conflicts["notes/todo.md"] == CONFLICT_TS
    assert engine.config.snapshot["notes/todo.md"] == sha256_hex(local)


def test_pull_rejects_unsafe_paths(tmp_path):
    engine = make_engine(tmp_path)
    engine.api.pull_responses = [{"events": [event("../escape.md", b"x")], "next_seq": 1}]
    with pytest.raises(ValueError):
        engine.pull()


# -- push --------------------------------------------------------------------


def test_build_push_entry_fields():
    entry = build_push_entry("a.md", b"# hi", base_version_id="v3")
    assert entry["space"] == "user-global"
    assert entry["path"] == "a.md"
    assert entry["base_version_id"] == "v3"
    assert entry["content_hash"] == text_hash("# hi")
    assert entry["content"] == "# hi"  # raw text, not base64
    assert entry["deleted"] is False
    assert len(entry["operation_id"]) == 36


def test_push_sends_raw_text_and_server_normalized_hash(tmp_path):
    engine = make_engine(tmp_path)
    target = engine.sync_root / "crlf.md"
    target.write_bytes(b"a\r\nb\r\nc")  # CRLF line endings on disk
    result = engine.push()
    assert result["pushed"] == ["crlf.md"]
    pushed = engine.api.pushes[0]
    assert pushed["content"] == "a\r\nb\r\nc"  # raw text on the wire
    # Hash must match the server: CRLF/CR folded to LF, then UTF-8 encoded.
    assert pushed["content_hash"] == text_hash("a\r\nb\r\nc")
    assert pushed["content_hash"] == sha256_hex(b"a\nb\nc")
    assert engine.config.snapshot["crlf.md"] == pushed["content_hash"]


def test_push_rejects_non_utf8_markdown(tmp_path):
    engine = make_engine(tmp_path)
    bad = engine.sync_root / "bad.md"
    bad.write_bytes(b"\xff\xfe\x00 binary bytes")
    good = engine.sync_root / "good.md"
    good.write_bytes(b"# fine")
    result = engine.push()
    assert result["invalid"] == ["bad.md"]
    assert result["pushed"] == ["good.md"]
    assert [p["path"] for p in engine.api.pushes] == ["good.md"]


def test_text_hash_folds_lone_carriage_return():
    # The server's two-step replace folds a lone \r to \n as well.
    assert text_hash("a\rb") == text_hash("a\nb")
    assert text_hash("a\rb") == sha256_hex(b"a\nb")


def test_pull_keeps_raw_text_unchanged(tmp_path):
    """Raw text like ``test`` and base64-looking text like ``YWJj`` must be
    written verbatim; no base64 decoding is applied to pull content."""
    engine = make_engine(tmp_path)
    engine.api.pull_responses = [
        {
            "events": [
                event("plain.md", b"test"),
                event("b64ish.md", b"YWJj"),
                event("crlf.md", b"line1\r\nline2"),
            ],
            "next_seq": 3,
        }
    ]
    result = engine.pull()
    assert result["applied"] == 3
    assert (engine.sync_root / "plain.md").read_bytes() == b"test"
    assert (engine.sync_root / "b64ish.md").read_bytes() == b"YWJj"
    assert (engine.sync_root / "crlf.md").read_bytes() == b"line1\r\nline2"
    assert engine.config.snapshot["b64ish.md"] == text_hash("YWJj")


def test_push_sends_only_changed_files(tmp_path):
    engine = make_engine(tmp_path)
    clean = engine.sync_root / "notes" / "clean.md"
    clean.parent.mkdir(parents=True)
    clean.write_bytes(b"unchanged")
    engine.config.snapshot["notes/clean.md"] = sha256_hex(b"unchanged")
    dirty = engine.sync_root / "notes" / "dirty.md"
    dirty.write_bytes(b"new")

    result = engine.push()
    assert result["pushed"] == ["notes/dirty.md"]
    assert result["unchanged"] == 1
    assert [p["path"] for p in engine.api.pushes] == ["notes/dirty.md"]
    assert engine.config.snapshot["notes/dirty.md"] == sha256_hex(b"new")


def test_push_sends_deleted_for_missing_files(tmp_path):
    engine = make_engine(tmp_path)
    engine.config.snapshot["notes/gone.md"] = sha256_hex(b"old content")
    result = engine.push()
    assert result["pushed"] == ["notes/gone.md"]
    assert engine.api.pushes[0]["deleted"] is True
    # Tombstone content_hash must match the empty payload the server hashes,
    # not the deleted file's content (that mismatch was the 422 bug).
    assert engine.api.pushes[0]["content_hash"] == text_hash("")
    assert "notes/gone.md" not in engine.config.snapshot


def test_push_409_records_conflict(tmp_path):
    engine = make_engine(tmp_path)
    dirty = engine.sync_root / "notes" / "dirty.md"
    dirty.parent.mkdir(parents=True)
    dirty.write_bytes(b"new")
    engine.api.push_conflict_paths = {"notes/dirty.md"}
    result = engine.push()
    assert result["conflicts"] == ["notes/dirty.md"]
    assert result["pushed"] == []


def test_run_pulls_then_pushes(tmp_path):
    engine = make_engine(tmp_path)
    server = b"from server"
    engine.api.pull_responses = [{"events": [event("remote.md", server)], "next_seq": 5}]
    local = engine.sync_root / "local.md"
    local.write_bytes(b"mine")
    result = engine.run()
    assert result["pull"]["applied"] == 1
    assert result["push"]["pushed"] == ["local.md"]
    assert (engine.sync_root / "remote.md").read_bytes() == server


def test_run_accumulates_conflicts_across_pull_and_push(tmp_path):
    """Conflicts found in the first pull must survive the post-push re-pull,
    and the follow-up pull must add the server copy for the 409'd path."""
    engine = make_engine(tmp_path)

    c1 = engine.sync_root / "c1.md"
    c1.write_bytes(b"local c1")
    engine.config.snapshot["c1.md"] = sha256_hex(b"old c1")
    c2 = engine.sync_root / "c2.md"
    c2.write_bytes(b"new c2")
    engine.api.push_conflict_paths = {"c2.md"}

    # First pull: c1 conflicts, remote.md applies, then an empty page.
    # Follow-up pull (after the c2 push conflict): the server's newer c2
    # version arrives and conflicts, then an empty page.
    engine.api.pull_responses = [
        {
            "events": [event("c1.md", b"server c1"), event("remote.md", b"applied")],
            "next_seq": 2,
        },
        {"events": [], "next_seq": 2},
        {"events": [event("c2.md", b"server c2 v2")], "next_seq": 3},
        {"events": [], "next_seq": 3},
    ]

    result = engine.run()

    assert set(result["pull"]["conflicts"]) == {"c1.md", "c2.md"}
    assert result["push"]["conflicts"] == ["c2.md"]
    assert result["pull"]["next_seq"] == 3
    assert result["pull"]["applied"] == 1  # remote.md, applied once
    # The follow-up pull materialized the server copy for the 409'd path.
    conflict_copy = engine.sync_root / "conflicts" / CONFLICT_TS / "c2.md"
    assert conflict_copy.read_bytes() == b"server c2 v2"
    # Both conflicted local files are preserved.
    assert c1.read_bytes() == b"local c1"
    assert c2.read_bytes() == b"new c2"
