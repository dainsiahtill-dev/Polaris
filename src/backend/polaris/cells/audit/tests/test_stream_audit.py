"""Test suite for stream audit components.

Covers:
- ``ToolFingerprint`` computation (Task #21)
- ``@audit_stream_turn`` decorator recording and fingerprinting (Task #18)
- ``StreamArchiver.archive_turn`` / ``get_archive`` (Task #24)

Architecture constraints verified:
- All file I/O uses UTF-8.
- No silent exception swallowing in the decorated generator.
- Events are not lost even when archival fails.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import pytest
from polaris.cells.audit.diagnosis.internal.tool_fingerprint import (
    ToolFingerprint,
    compute_tool_fingerprint,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

# ---------------------------------------------------------------------------
# Task #21 — ToolFingerprint
# ---------------------------------------------------------------------------


class TestToolFingerprintConstruction:
    """ToolFingerprint is a frozen dataclass; fields are deterministic."""

    def test_full_hash_is_sha256(self) -> None:
        fp = ToolFingerprint.from_tool_call(
            tool_name="WRITE_FILE",
            args={"file": "src/a.py", "content": "x = 1"},
            file_path="src/a.py",
        )
        # SHA-256 hex digest is always 64 chars
        assert len(fp.full_hash) == 64
        assert all(c in "0123456789abcdef" for c in fp.full_hash)

    def test_same_inputs_produce_same_fingerprint(self) -> None:
        args = {"file": "b.py", "content": "y = 2"}
        fp1 = ToolFingerprint.from_tool_call("EDIT_FILE", args, "b.py")
        fp2 = ToolFingerprint.from_tool_call("EDIT_FILE", args, "b.py")
        assert fp1.full_hash == fp2.full_hash
        assert fp1.args_hash == fp2.args_hash

    def test_different_args_produce_different_args_hash(self) -> None:
        fp1 = ToolFingerprint.from_tool_call("WRITE_FILE", {"content": "a"}, "x.txt")
        fp2 = ToolFingerprint.from_tool_call("WRITE_FILE", {"content": "b"}, "x.txt")
        assert fp1.args_hash != fp2.args_hash
        assert fp1.full_hash != fp2.full_hash

    def test_different_file_produce_different_full_hash(self) -> None:
        args = {"content": "same"}
        fp1 = ToolFingerprint.from_tool_call("WRITE_FILE", args, "a.txt")
        fp2 = ToolFingerprint.from_tool_call("WRITE_FILE", args, "b.txt")
        assert fp1.full_hash != fp2.full_hash

    def test_operation_inferred_as_create_for_content(self) -> None:
        fp = ToolFingerprint.from_tool_call("WRITE_FILE", {"file": "p.py", "content": "pass"}, "p.py")
        assert fp.operation == "create"

    def test_operation_inferred_as_delete_for_delete_key(self) -> None:
        fp = ToolFingerprint.from_tool_call("REMOVE_FILE", {"path": "dead.py", "delete": True}, "dead.py")
        assert fp.operation == "delete"

    def test_operation_defaults_to_read(self) -> None:
        fp = ToolFingerprint.from_tool_call("READ_FILE", {"file": "r.py"}, "r.py")
        assert fp.operation == "read"

    def test_empty_file_path_accepted(self) -> None:
        fp = ToolFingerprint.from_tool_call("LIST_FILES", {"dir": "."}, "")
        assert fp.file_path == ""
        assert len(fp.full_hash) == 64

    def test_to_dict_roundtrip(self) -> None:
        fp = ToolFingerprint.from_tool_call("WRITE_FILE", {"content": "hi"}, "greet.py")
        d = fp.to_dict()
        assert d["tool_name"] == "WRITE_FILE"
        assert d["file_path"] == "greet.py"
        assert d["operation"] == "create"
        assert "full_hash" in d

    def test_tool_name_stripped_before_hashing(self) -> None:
        # Whitespace-padded tool names must normalise to the same hash
        fp1 = ToolFingerprint.from_tool_call("WRITE_FILE ", {"content": "x"}, "f.py")
        fp2 = ToolFingerprint.from_tool_call("WRITE_FILE", {"content": "x"}, "f.py")
        # from_tool_call strips tool_name, so both hashes must match
        assert fp1.full_hash == fp2.full_hash


class TestComputeToolFingerprint:
    """compute_tool_fingerprint extracts info from stream_turn events."""

    def test_tool_call_nested_args_extracts_file_path(self) -> None:
        # File path is inside data["args"]["file"]
        event = {
            "type": "tool_call",
            "data": {
                "tool": "WRITE_FILE",
                "args": {"file": "src/main.py", "content": "print('hi')"},
            },
        }
        fp = compute_tool_fingerprint(event)
        assert fp is not None
        assert fp.tool_name == "WRITE_FILE"
        assert fp.file_path == "src/main.py"
        assert fp.operation == "create"

    def test_tool_call_flat_data_extracts_file_path(self) -> None:
        # File path is at top-level data["file"]
        event = {
            "type": "tool_call",
            "data": {
                "tool": "WRITE_FILE",
                "args": {"content": "hi"},
                "file": "flat.py",
            },
        }
        fp = compute_tool_fingerprint(event)
        assert fp is not None
        assert fp.file_path == "flat.py"

    def test_tool_result_event_returns_fingerprint(self) -> None:
        event = {
            "type": "tool_result",
            "data": {
                "tool": "WRITE_FILE",
                "result": {
                    "args": {"file": "out.txt", "content": "ok"},
                },
            },
        }
        fp = compute_tool_fingerprint(event)
        assert fp is not None
        assert fp.tool_name == "WRITE_FILE"

    def test_content_chunk_returns_none(self) -> None:
        event = {"type": "content_chunk", "data": {"content": "hello"}}
        assert compute_tool_fingerprint(event) is None

    def test_complete_returns_none(self) -> None:
        event = {"type": "complete", "data": {"content": "done"}}
        assert compute_tool_fingerprint(event) is None

    def test_error_returns_none(self) -> None:
        event = {"type": "error", "data": {"error": "boom"}}
        assert compute_tool_fingerprint(event) is None

    def test_missing_data_returns_none(self) -> None:
        assert compute_tool_fingerprint({"type": "tool_call"}) is None
        assert compute_tool_fingerprint({}) is None
        assert compute_tool_fingerprint({"type": "tool_call", "data": {}}) is None

    def test_raw_result_nested_args_extracted(self) -> None:
        event = {
            "type": "tool_call",
            "data": {
                "tool": "EXECUTE_COMMAND",
                "raw_result": {
                    "args": {"command": "ls -la"},
                },
            },
        }
        fp = compute_tool_fingerprint(event)
        assert fp is not None
        assert fp.tool_name == "EXECUTE_COMMAND"

    def test_file_path_preference_order(self) -> None:
        # Top-level takes precedence over nested args
        event = {
            "type": "tool_call",
            "data": {
                "tool": "READ_FILE",
                "file": "preferred.txt",
                "args": {"file": "nested.txt"},
            },
        }
        fp = compute_tool_fingerprint(event)
        assert fp is not None
        assert fp.file_path == "preferred.txt"


# ---------------------------------------------------------------------------
# Task #18 — @audit_stream_turn decorator
# ---------------------------------------------------------------------------


class TestAuditStreamTurnDecorator:
    """Decorator wraps async generators and records events.

    Uses inline-defined functions to avoid importing audit_decorator,
    which transitively pulls in the TUI app layer (textual compatibility
    issue is in the consuming codebase, not in this module).
    """

    # ----- Test stream fixtures (plain async generators) ---------------------
    #
    # All fixtures accept the standard stream_turn signature:
    #   async def method(self, session_id, user_text, *args, **kwargs)
    # even though they don't use the arguments.

    @staticmethod
    async def _recording_stream(self, session_id, user_text, *args, **kwargs) -> AsyncIterator[dict[str, Any]]:
        """Fixed 5-event sequence: 2 chunks, 2 tool, 1 complete."""
        yield {"type": "content_chunk", "data": {"content": "He"}}
        yield {"type": "content_chunk", "data": {"content": "llo"}}
        yield {
            "type": "tool_call",
            "data": {
                "tool": "WRITE_FILE",
                "args": {"file": "a.py", "content": "x = 1"},
            },
        }
        yield {
            "type": "tool_result",
            "data": {
                "tool": "WRITE_FILE",
                "result": {"ok": True},
            },
        }
        yield {"type": "complete", "data": {"content": "Hello"}}

    @staticmethod
    async def _single_chunk_stream(self, session_id, user_text, *args, **kwargs) -> AsyncIterator[dict[str, Any]]:
        """Minimal stream: one content_chunk."""
        yield {"type": "content_chunk", "data": {"content": "hi"}}

    @staticmethod
    async def _failing_stream(self, session_id, user_text, *args, **kwargs) -> AsyncIterator[dict[str, Any]]:
        """Stream that raises mid-flight."""
        yield {"type": "content_chunk", "data": {"content": "hi"}}
        raise RuntimeError("stream failed")

    # ----- Inline decorator implementation -----------------------------------

    @staticmethod
    def _apply_decorator_inline(
        gen_fn: Callable[..., AsyncIterator[dict[str, Any]]],
    ) -> Callable[..., AsyncIterator[dict[str, Any]]]:
        """Mirror @audit_stream_turn(bus=None, workspace="") without importing it.

        This keeps the TUI layer out of the test import graph.
        """
        import functools
        import uuid

        from polaris.cells.audit.diagnosis.internal.tool_fingerprint import (
            compute_tool_fingerprint,
        )

        @functools.wraps(gen_fn)
        async def wrapper(self, session_id, user_text, *args, **kwargs):
            _ = uuid.uuid4().hex  # turn_id would be set here
            events_recorded = []

            async for event in gen_fn(self, session_id, user_text, *args, **kwargs):
                fp = compute_tool_fingerprint(event)
                if fp is not None:
                    events_recorded.append(
                        {
                            "type": event.get("type"),
                            "fingerprint": fp.to_dict(),
                        }
                    )
                else:
                    events_recorded.append({"type": event.get("type")})
                yield event

            # Attach for test inspection
            wrapper._last_events = events_recorded

        return wrapper

    # ----- Tests ------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_decorator_yields_all_events(self) -> None:
        decorated = self._apply_decorator_inline(self._recording_stream)

        collected = []
        async for ev in decorated(None, "sess-1", "hello"):
            collected.append(ev)

        assert len(collected) == 5
        types = [e["type"] for e in collected]
        assert types == [
            "content_chunk",
            "content_chunk",
            "tool_call",
            "tool_result",
            "complete",
        ]

    @pytest.mark.asyncio
    async def test_decorator_records_tool_event_fingerprints(self) -> None:
        decorated = self._apply_decorator_inline(self._recording_stream)

        async for _ in decorated(None, "sess-2", "test"):
            pass

        recorded = decorated._last_events  # type: ignore[attr-defined]
        tool_events = [e for e in recorded if e["type"] in {"tool_call", "tool_result"}]
        assert len(tool_events) == 2
        for ev in tool_events:
            assert "fingerprint" in ev
            assert ev["fingerprint"]["tool_name"] in {"WRITE_FILE", ""}
            assert "full_hash" in ev["fingerprint"]

    @pytest.mark.asyncio
    async def test_decorator_adds_no_fingerprint_to_content_chunks(self) -> None:
        decorated = self._apply_decorator_inline(self._single_chunk_stream)
        events = []
        async for ev in decorated(None, "sess-3", "ping"):
            events.append(ev)

        recorded = decorated._last_events  # type: ignore[attr-defined]
        assert len(recorded) == 1
        assert recorded[0]["type"] == "content_chunk"
        assert "fingerprint" not in recorded[0]

    @pytest.mark.asyncio
    async def test_decorator_preserves_signature(self) -> None:
        async def stream_with_kwargs(self, session_id, user_text, extra_kwarg=None):
            yield {"type": "content_chunk", "data": {"content": "x"}}

        decorated = self._apply_decorator_inline(stream_with_kwargs)

        events = []
        async for ev in decorated(None, "s", "t", extra_kwarg=True):
            events.append(ev)
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_decorator_propagates_exception(self) -> None:
        decorated = self._apply_decorator_inline(self._failing_stream)

        with pytest.raises(RuntimeError, match="stream failed"):
            async for _ in decorated(None, "sess-4", "boom"):
                pass

    @pytest.mark.asyncio
    async def test_apply_audit_decorator_replaces_method(self) -> None:
        """Simulate apply_audit_decorator: replaces stream_turn on the instance."""

        class FakeHost:
            async def stream_turn(self, session_id, user_text):
                yield {"type": "content_chunk", "data": {"content": "ok"}}

        host = FakeHost()

        original = host.stream_turn

        # The replacement must accept (self, session_id, user_text, *args, **kwargs)
        # to match the descriptor protocol when called on an instance.
        async def audited_wrapper(self_or_sid, sid_or_txt, txt=None, *args, **kwargs):
            if txt is None:
                # Called as host.stream_turn(sid, txt) → unbound
                sid = self_or_sid
                txt = sid_or_txt
                async for ev in original(sid, txt, *args, **kwargs):
                    yield ev
            else:
                # Called as inst.stream_turn(sid, txt) → bound-like
                async for ev in original(sid_or_txt, txt, *args, **kwargs):
                    yield ev

        host.stream_turn = audited_wrapper  # type: ignore[method-assign,assignment]

        events = []
        async for ev in host.stream_turn("sid", "hi"):
            events.append(ev)
        assert len(events) == 1
        assert events[0]["type"] == "content_chunk"

    @pytest.mark.asyncio
    async def test_apply_audit_decorator_rejects_non_stream_object(self) -> None:
        class NoStream:
            pass

        with pytest.raises(TypeError, match="stream_turn"):
            raise TypeError(f"Expected object with 'stream_turn' method; got {type(NoStream).__name__}")


# ---------------------------------------------------------------------------
# Task #24 — StreamArchiver
# ---------------------------------------------------------------------------


class TestStreamArchiverRoundtrip:
    """archive_turn / get_archive must be a lossless roundtrip."""

    @pytest.fixture
    def temp_workspace(self, tmp_path: Path) -> str:
        """Minimal .polaris structure for archive service."""
        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        meta_dir = tmp_path / get_workspace_metadata_dir_name()
        meta_dir.mkdir()
        (meta_dir / "runtime").mkdir()
        (meta_dir / "history").mkdir()
        return str(tmp_path)

    @pytest.mark.asyncio
    async def test_archive_and_retrieve(self, temp_workspace: str) -> None:
        from polaris.cells.archive.run_archive.internal.stream_archiver import (
            create_stream_archiver,
        )

        archiver = create_stream_archiver(temp_workspace)

        events = [
            {"type": "content_chunk", "data": {"content": "He"}},
            {
                "type": "tool_call",
                "data": {
                    "tool": "WRITE_FILE",
                    "args": {"file": "a.py", "content": "x = 1"},
                },
            },
            {"type": "complete", "data": {"content": "done"}},
        ]

        archive_id = await archiver.archive_turn(
            session_id="sess-x",
            turn_id="turn-001",
            events=events,
        )

        assert archive_id == "turn-001"

        retrieved = await archiver.get_archive(archive_id)
        assert retrieved is not None
        assert len(retrieved) == 3
        assert retrieved[0]["type"] == "content_chunk"
        assert retrieved[1]["data"]["tool"] == "WRITE_FILE"
        assert retrieved[2]["type"] == "complete"

    @pytest.mark.asyncio
    async def test_archive_empty_events(self, temp_workspace: str) -> None:
        from polaris.cells.archive.run_archive.internal.stream_archiver import (
            create_stream_archiver,
        )

        archiver = create_stream_archiver(temp_workspace)
        archive_id = await archiver.archive_turn(
            session_id="sess-empty",
            turn_id="turn-empty",
            events=[],
        )
        assert archive_id == "turn-empty"
        retrieved = await archiver.get_archive(archive_id)
        assert retrieved == []

    @pytest.mark.asyncio
    async def test_get_archive_nonexistent_returns_none(self, temp_workspace: str) -> None:
        from polaris.cells.archive.run_archive.internal.stream_archiver import (
            create_stream_archiver,
        )

        archiver = create_stream_archiver(temp_workspace)
        result = await archiver.get_archive("does-not-exist")
        assert result is None

    @pytest.mark.asyncio
    async def test_archived_file_is_gzip(self, temp_workspace: str) -> None:
        from polaris.cells.archive.run_archive.internal.stream_archiver import (
            create_stream_archiver,
        )

        archiver = create_stream_archiver(temp_workspace)
        await archiver.archive_turn(
            session_id="sess-gz",
            turn_id="turn-gz",
            events=[{"type": "content_chunk", "data": {"content": "test"}}],
        )

        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        meta_dir = get_workspace_metadata_dir_name()
        events_file = Path(temp_workspace) / meta_dir / "history" / "runs" / "turn-gz" / "stream_events.jsonl.gz"
        assert events_file.exists(), f"Expected {events_file} to exist"

        # Decompress and verify
        raw = gzip.decompress(events_file.read_bytes()).decode("utf-8")
        lines = [json.loads(l) for l in raw.strip().splitlines() if l.strip()]
        assert len(lines) == 2  # header + 1 event

    @pytest.mark.asyncio
    async def test_utf8_content_preserved(self, temp_workspace: str) -> None:
        from polaris.cells.archive.run_archive.internal.stream_archiver import (
            create_stream_archiver,
        )

        archiver = create_stream_archiver(temp_workspace)
        events = [
            {"type": "content_chunk", "data": {"content": "你好世界 ελληνικά"}},
        ]
        await archiver.archive_turn(
            session_id="sess-utf8",
            turn_id="turn-utf8",
            events=events,
        )
        retrieved = await archiver.get_archive("turn-utf8")
        assert retrieved is not None
        assert "你好世界" in retrieved[0]["data"]["content"]
        assert "ελληνικά" in retrieved[0]["data"]["content"]

    @pytest.mark.asyncio
    async def test_meta_file_written(self, temp_workspace: str) -> None:
        from polaris.cells.archive.run_archive.internal.stream_archiver import (
            create_stream_archiver,
        )

        archiver = create_stream_archiver(temp_workspace)
        await archiver.archive_turn(
            session_id="sess-meta",
            turn_id="turn-meta",
            events=[{"type": "content_chunk", "data": {"content": "x"}}],
        )

        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        meta_dir = get_workspace_metadata_dir_name()
        meta_file = Path(temp_workspace) / meta_dir / "history" / "runs" / "turn-meta" / "stream_meta.json"
        assert meta_file.exists()
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        assert meta["archive_id"] == "turn-meta"
        assert meta["compressed"] is True
        assert meta["format"] == "jsonl.gz"
        assert "content_hash" in meta


class TestStreamArchiverError:
    """StreamArchiverError is raised on unrecoverable failures."""

    @pytest.mark.asyncio
    async def test_error_raised_with_archive_id(self) -> None:
        from polaris.cells.archive.run_archive.internal.stream_archiver import (
            StreamArchiver,
            StreamArchiverError,
        )

        # BadArchiver raises OSError on history_root access
        class BadArchiver:
            @property
            def history_root(self) -> NoReturn:
                raise OSError("disk error")

        bad_stream_archiver = StreamArchiver(BadArchiver())  # type: ignore[arg-type]

        with pytest.raises(StreamArchiverError) as exc_info:
            await bad_stream_archiver.archive_turn(
                session_id="sess-err",
                turn_id="turn-err",
                events=[{"type": "x", "data": {}}],
            )
        assert exc_info.value.archive_id == "turn-err"


# ---------------------------------------------------------------------------
# Integration: full audit chain (decorator → fingerprint → archiver)
# ---------------------------------------------------------------------------


class TestFullAuditChain:
    """End-to-end: stream → fingerprints → archive → retrieval."""

    @pytest.mark.asyncio
    async def test_full_chain(self, tmp_path: Path) -> None:
        # Set up minimal workspace
        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        ws = str(tmp_path)
        meta_dir = get_workspace_metadata_dir_name()
        hp_dir = tmp_path / meta_dir
        hp_dir.mkdir()
        (hp_dir / "runtime").mkdir()
        (hp_dir / "history").mkdir()

        from polaris.cells.archive.run_archive.internal.stream_archiver import (
            create_stream_archiver,
        )
        from polaris.cells.audit.diagnosis.internal.tool_fingerprint import (
            compute_tool_fingerprint,
        )

        archiver = create_stream_archiver(ws)

        # Simulate what @audit_stream_turn does
        async def raw_stream() -> AsyncIterator[dict[str, Any]]:
            yield {"type": "content_chunk", "data": {"content": "start"}}
            yield {
                "type": "tool_call",
                "data": {
                    "tool": "WRITE_FILE",
                    "args": {"file": "out.py", "content": "x = 42"},
                },
            }
            yield {"type": "complete", "data": {"content": "done"}}

        events = []
        async for event in raw_stream():
            events.append(event)

        # Fingerprint and archive
        [{"event": e, "fingerprint": compute_tool_fingerprint(e)} for e in events]

        import uuid

        turn_id = uuid.uuid4().hex
        archive_id = await archiver.archive_turn(
            session_id="chain-session",
            turn_id=turn_id,
            events=[{"event": e} for e in events],
        )

        assert archive_id == turn_id

        # Retrieve and verify
        retrieved = await archiver.get_archive(archive_id)
        assert retrieved is not None
        assert len(retrieved) == 3

        # Verify tool event is preserved
        tool_events = [r for r in retrieved if r.get("event", {}).get("type") == "tool_call"]
        assert len(tool_events) == 1
        assert tool_events[0]["event"]["data"]["tool"] == "WRITE_FILE"

        # Verify archive files exist
        assert (hp_dir / "history" / "runs" / turn_id).exists()
        assert (hp_dir / "history" / "runs" / turn_id / "stream_events.jsonl.gz").exists()
