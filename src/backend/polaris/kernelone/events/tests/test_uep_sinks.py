"""Unit tests for UEP v2.0 Sinks (JournalSink, ArchiveSink, AuditHashSink)."""

from __future__ import annotations

import asyncio
import gzip
import json
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType
from polaris.kernelone.storage import resolve_runtime_path


class TestJournalSink:
    """Test suite for JournalSink."""

    @pytest.fixture
    def temp_workspace(self) -> Generator[str, None, None]:
        """Create a temporary workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Note: Storage layout resolves runtime_root to a system cache
            # location outside the workspace by design. Tests should query
            # the actual resolved path, not assume workspace-relative paths.
            # Also, metadata dir is .polaris (not .polaris) per bootstrap config.
            yield str(tmpdir)

    @pytest.fixture
    def message_bus(self) -> MessageBus:
        """Create a fresh MessageBus."""
        return MessageBus()

    @pytest.mark.asyncio
    async def test_journal_sink_writes_stream_event(
        self,
        temp_workspace: str,
        message_bus: MessageBus,
    ) -> None:
        """Test JournalSink writes stream events to journal."""
        from polaris.infrastructure.log_pipeline.journal_sink import JournalSink

        sink = JournalSink(message_bus)
        await sink.start()

        run_id = "test-run-001"
        msg = Message(
            type=MessageType.RUNTIME_EVENT,
            sender="test",
            payload={
                "topic": "runtime.event.stream",
                "workspace": temp_workspace,
                "run_id": run_id,
                "role": "director",
                "event_type": "tool_call",
                "payload": {"tool": "read_file", "args": {"path": "test.py"}},
                "timestamp": "2026-03-31T12:00:00Z",
            },
        )

        await message_bus.publish(msg)
        await asyncio.sleep(0.1)  # Allow async handler to complete

        # Check journal files created
        # Query the actual resolved path (runtime_root is resolved to system cache)
        from polaris.kernelone.storage import resolve_storage_roots

        roots = resolve_storage_roots(temp_workspace)
        logs_dir = Path(roots.runtime_root) / "runs" / run_id / "logs"
        norm_journal = logs_dir / "journal.norm.jsonl"

        assert norm_journal.exists(), f"Expected {norm_journal} to exist"

        # Read and verify content
        with open(norm_journal, encoding="utf-8") as f:
            lines = f.readlines()

        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["channel"] == "llm"
        assert event["domain"] == "llm"
        assert event["kind"] == "action"  # tool_call maps to action
        assert event["actor"] == "director"
        assert "tool_call" in event["message"]

        await sink.stop()

    @pytest.mark.asyncio
    async def test_journal_sink_writes_lifecycle_event(
        self,
        temp_workspace: str,
        message_bus: MessageBus,
    ) -> None:
        """Test JournalSink writes lifecycle events to journal."""
        from polaris.infrastructure.log_pipeline.journal_sink import JournalSink

        sink = JournalSink(message_bus)
        await sink.start()

        run_id = "test-run-002"
        msg = Message(
            type=MessageType.RUNTIME_EVENT,
            sender="test",
            payload={
                "topic": "runtime.event.llm",
                "workspace": temp_workspace,
                "run_id": run_id,
                "role": "director",
                "event_type": "call_start",
                "metadata": {"model": "gpt-4", "call_id": "call-123"},
                "timestamp": "2026-03-31T12:00:00Z",
            },
        )

        await message_bus.publish(msg)
        await asyncio.sleep(0.1)

        # Query the actual resolved path (runtime_root is resolved to system cache)
        from polaris.kernelone.storage import resolve_storage_roots

        roots = resolve_storage_roots(temp_workspace)
        logs_dir = Path(roots.runtime_root) / "runs" / run_id / "logs"
        norm_journal = logs_dir / "journal.norm.jsonl"

        assert norm_journal.exists()

        with open(norm_journal, encoding="utf-8") as f:
            event = json.loads(f.readline())

        assert event["kind"] == "state"
        assert event["severity"] == "info"
        assert "call_start" in event["message"]

        await sink.stop()

    @pytest.mark.asyncio
    async def test_journal_sink_ignores_unknown_topics(
        self,
        temp_workspace: str,
        message_bus: MessageBus,
    ) -> None:
        """Test JournalSink ignores messages with unknown topics."""
        from polaris.infrastructure.log_pipeline.journal_sink import JournalSink

        sink = JournalSink(message_bus)
        await sink.start()

        run_id = "test-run-003"
        msg = Message(
            type=MessageType.RUNTIME_EVENT,
            sender="test",
            payload={
                "topic": "unknown.topic",
                "workspace": temp_workspace,
                "run_id": run_id,
                "role": "director",
                "event_type": "something",
                "payload": {},
                "timestamp": "2026-03-31T12:00:00Z",
            },
        )

        await message_bus.publish(msg)
        await asyncio.sleep(0.1)

        # No journal should be created
        logs_dir = Path(resolve_runtime_path(temp_workspace, f"runtime/runs/{run_id}/logs"))
        assert not logs_dir.exists()

        await sink.stop()

    @pytest.mark.asyncio
    async def test_journal_sink_skips_without_workspace(
        self,
        message_bus: MessageBus,
    ) -> None:
        """Test JournalSink skips events without workspace."""
        from polaris.infrastructure.log_pipeline.journal_sink import JournalSink

        sink = JournalSink(message_bus)
        await sink.start()

        msg = Message(
            type=MessageType.RUNTIME_EVENT,
            sender="test",
            payload={
                "topic": "runtime.event.stream",
                "workspace": "",
                "run_id": "test-run",
                "role": "director",
                "event_type": "content_chunk",
                "payload": {"content": "hello"},
                "timestamp": "2026-03-31T12:00:00Z",
            },
        )

        # Should not raise
        await message_bus.publish(msg)
        await asyncio.sleep(0.1)

        await sink.stop()


class TestArchiveSink:
    """Test suite for ArchiveSink."""

    @pytest.fixture
    def temp_workspace(self) -> Generator[str, None, None]:
        """Create a temporary workspace with history structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Note: Metadata dir is .polaris per bootstrap config.
            # Tests should query resolve_polaris_roots for actual paths.
            yield str(tmpdir)

    @pytest.fixture
    def message_bus(self) -> MessageBus:
        """Create a fresh MessageBus."""
        return MessageBus()

    @pytest.mark.asyncio
    async def test_archive_sink_buffers_and_flushes_on_complete(
        self,
        temp_workspace: str,
        message_bus: MessageBus,
    ) -> None:
        """Test ArchiveSink buffers events and flushes on complete."""
        from polaris.cells.archive.run_archive.internal.archive_sink import ArchiveSink

        sink = ArchiveSink(message_bus)
        await sink.start()

        turn_id = "turn-abc-123"
        session_id = "session-xyz"

        # Send multiple stream events
        events: list[dict[str, Any]] = [
            {"type": "content_chunk", "content": "Hello"},
            {"type": "thinking_chunk", "content": "Let me think"},
            {"type": "tool_call", "tool": "read_file", "args": {}},
        ]

        for event in events:
            event_type = event["type"]
            msg = Message(
                type=MessageType.RUNTIME_EVENT,
                sender="test",
                payload={
                    "topic": "runtime.event.stream",
                    "workspace": temp_workspace,
                    "run_id": session_id,
                    "turn_id": turn_id,
                    "role": "director",
                    "event_type": event_type,
                    "payload": event,
                    "timestamp": "2026-03-31T12:00:00Z",
                },
            )
            await message_bus.publish(msg)

        # Query the actual resolved history path (uses .polaris per bootstrap config)
        from polaris.cells.storage.layout.internal.layout_business import resolve_polaris_roots

        roots = resolve_polaris_roots(temp_workspace)
        archive_dir = Path(roots.history_root) / "runs" / turn_id

        # No archive yet (not flushed)
        assert not archive_dir.exists()

        # Send complete event to flush
        complete_msg = Message(
            type=MessageType.RUNTIME_EVENT,
            sender="test",
            payload={
                "topic": "runtime.event.stream",
                "workspace": temp_workspace,
                "run_id": session_id,
                "turn_id": turn_id,
                "role": "director",
                "event_type": "complete",
                "payload": {"result": "success"},
                "timestamp": "2026-03-31T12:00:01Z",
            },
        )
        await message_bus.publish(complete_msg)
        await asyncio.sleep(0.1)

        # Now archive should exist
        assert archive_dir.exists()
        archive_file = archive_dir / "stream_events.jsonl.gz"
        assert archive_file.exists()

        # Verify content
        with gzip.open(archive_file, "rt", encoding="utf-8") as f:
            lines = f.readlines()

        assert len(lines) == 5  # header + 4 events (3 intermediate + complete)

        header = json.loads(lines[0])
        assert header["type"] == "header"
        assert header["turn_id"] == turn_id
        assert header["session_id"] == session_id
        assert header["event_count"] == 4

        await sink.stop()

    @pytest.mark.asyncio
    async def test_archive_sink_flushes_on_error(
        self,
        temp_workspace: str,
        message_bus: MessageBus,
    ) -> None:
        """Test ArchiveSink flushes on error event."""
        from polaris.cells.archive.run_archive.internal.archive_sink import ArchiveSink

        sink = ArchiveSink(message_bus)
        await sink.start()

        turn_id = "turn-error-123"

        # Send an event
        msg = Message(
            type=MessageType.RUNTIME_EVENT,
            sender="test",
            payload={
                "topic": "runtime.event.stream",
                "workspace": temp_workspace,
                "run_id": "session-1",
                "turn_id": turn_id,
                "role": "director",
                "event_type": "content_chunk",
                "payload": {"content": "partial"},
                "timestamp": "2026-03-31T12:00:00Z",
            },
        )
        await message_bus.publish(msg)

        # Send error event
        error_msg = Message(
            type=MessageType.RUNTIME_EVENT,
            sender="test",
            payload={
                "topic": "runtime.event.stream",
                "workspace": temp_workspace,
                "run_id": "session-1",
                "turn_id": turn_id,
                "role": "director",
                "event_type": "error",
                "payload": {"error": "something went wrong"},
                "timestamp": "2026-03-31T12:00:01Z",
            },
        )
        await message_bus.publish(error_msg)
        await asyncio.sleep(0.1)

        # Query the actual resolved history path
        from polaris.cells.storage.layout.internal.layout_business import resolve_polaris_roots

        roots = resolve_polaris_roots(temp_workspace)
        archive_dir = Path(roots.history_root) / "runs" / turn_id
        assert archive_dir.exists()

        await sink.stop()

    @pytest.mark.asyncio
    async def test_archive_sink_ignores_non_stream_events(
        self,
        temp_workspace: str,
        message_bus: MessageBus,
    ) -> None:
        """Test ArchiveSink ignores non-stream events."""
        from polaris.cells.archive.run_archive.internal.archive_sink import ArchiveSink

        sink = ArchiveSink(message_bus)
        await sink.start()

        # Send lifecycle event (should be ignored)
        msg = Message(
            type=MessageType.RUNTIME_EVENT,
            sender="test",
            payload={
                "topic": "runtime.event.llm",
                "workspace": temp_workspace,
                "run_id": "session-1",
                "turn_id": "turn-123",
                "role": "director",
                "event_type": "call_start",
                "metadata": {},
                "timestamp": "2026-03-31T12:00:00Z",
            },
        )
        await message_bus.publish(msg)
        await asyncio.sleep(0.1)

        # No archive should be created (check actual resolved path)
        from polaris.cells.storage.layout.internal.layout_business import resolve_polaris_roots

        roots = resolve_polaris_roots(temp_workspace)
        archive_dir = Path(roots.history_root) / "runs"
        # History root exists but runs subdir should be empty or not exist
        if archive_dir.exists():
            assert len(list(archive_dir.glob("*"))) == 0

        await sink.stop()


class TestAuditHashSink:
    """Test suite for AuditHashSink."""

    @pytest.fixture
    def temp_workspace(self) -> Generator[str, None, None]:
        """Create a temporary workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Note: Metadata dir is .polaris per bootstrap config.
            yield str(tmpdir)

    @pytest.fixture
    def message_bus(self) -> MessageBus:
        """Create a fresh MessageBus."""
        return MessageBus()

    @pytest.mark.asyncio
    async def test_audit_hash_sink_appends_to_store(
        self,
        temp_workspace: str,
        message_bus: MessageBus,
    ) -> None:
        """Test AuditHashSink appends events to AuditStore."""
        from polaris.infrastructure.audit.sinks.audit_hash_sink import AuditHashSink

        sink = AuditHashSink(message_bus)
        await sink.start()

        # Setup audit store
        from polaris.infrastructure.audit.adapters.store_adapter import AuditStoreAdapter
        from polaris.kernelone.audit.registry import set_audit_store_factory

        set_audit_store_factory(lambda root: AuditStoreAdapter(Path(root)))

        msg = Message(
            type=MessageType.RUNTIME_EVENT,
            sender="test",
            payload={
                "topic": "runtime.event.audit",
                "workspace": temp_workspace,
                "run_id": "run-audit-001",
                "role": "director",
                "event_type": "tool_execution",
                "data": {"tool": "read_file", "success": True},
                "timestamp": "2026-03-31T12:00:00Z",
            },
        )

        await message_bus.publish(msg)
        await asyncio.sleep(0.1)

        # Verify audit log exists (AuditStore creates 'audit' subdir under runtime_root)
        # runtime_root is resolved via resolve_storage_roots (system cache location)
        from polaris.kernelone.storage import resolve_storage_roots

        roots = resolve_storage_roots(temp_workspace)
        audit_dir = Path(roots.runtime_root) / "audit"
        audit_files = list(audit_dir.glob("audit-*.jsonl"))
        assert len(audit_files) == 1

        await sink.stop()

    @pytest.mark.asyncio
    async def test_audit_hash_sink_ignores_non_audit_events(
        self,
        temp_workspace: str,
        message_bus: MessageBus,
    ) -> None:
        """Test AuditHashSink ignores non-audit events."""
        from polaris.infrastructure.audit.sinks.audit_hash_sink import AuditHashSink

        sink = AuditHashSink(message_bus)
        await sink.start()

        # Send stream event (should be ignored)
        msg = Message(
            type=MessageType.RUNTIME_EVENT,
            sender="test",
            payload={
                "topic": "runtime.event.stream",
                "workspace": temp_workspace,
                "run_id": "run-123",
                "role": "director",
                "event_type": "content_chunk",
                "payload": {},
                "timestamp": "2026-03-31T12:00:00Z",
            },
        )

        await message_bus.publish(msg)
        await asyncio.sleep(0.1)

        await sink.stop()


class TestSinkIntegration:
    """Integration tests with multiple sinks."""

    @pytest.fixture
    def temp_workspace(self) -> Generator[str, None, None]:
        """Create a temporary workspace."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Note: Metadata dir is .polaris per bootstrap config.
            # Tests should query resolved paths for actual locations.
            yield str(tmpdir)

    @pytest.fixture
    def message_bus(self) -> MessageBus:
        """Create a fresh MessageBus."""
        return MessageBus()

    @pytest.mark.asyncio
    async def test_all_sinks_receive_same_event(
        self,
        temp_workspace: str,
        message_bus: MessageBus,
    ) -> None:
        """Test that all sinks can receive the same published event."""
        from polaris.cells.archive.run_archive.internal.archive_sink import ArchiveSink
        from polaris.infrastructure.audit.sinks.audit_hash_sink import AuditHashSink
        from polaris.infrastructure.log_pipeline.journal_sink import JournalSink

        # Setup all sinks
        journal_sink = JournalSink(message_bus)
        archive_sink = ArchiveSink(message_bus)
        audit_sink = AuditHashSink(message_bus)

        await journal_sink.start()
        await archive_sink.start()
        await audit_sink.start()

        # Setup audit store
        from polaris.infrastructure.audit.adapters.store_adapter import AuditStoreAdapter
        from polaris.kernelone.audit.registry import set_audit_store_factory

        set_audit_store_factory(lambda root: AuditStoreAdapter(Path(root)))

        # Publish multiple stream events with complete
        turn_id = "turn-integration-001"
        for i in range(3):
            msg = Message(
                type=MessageType.RUNTIME_EVENT,
                sender="test",
                payload={
                    "topic": "runtime.event.stream",
                    "workspace": temp_workspace,
                    "run_id": "session-1",
                    "turn_id": turn_id,
                    "role": "director",
                    "event_type": "content_chunk",
                    "payload": {"content": f"chunk {i}"},
                    "timestamp": f"2026-03-31T12:00:0{i}Z",
                },
            )
            await message_bus.publish(msg)

        # Send complete to flush archive
        complete_msg = Message(
            type=MessageType.RUNTIME_EVENT,
            sender="test",
            payload={
                "topic": "runtime.event.stream",
                "workspace": temp_workspace,
                "run_id": "session-1",
                "turn_id": turn_id,
                "role": "director",
                "event_type": "complete",
                "payload": {"result": "done"},
                "timestamp": "2026-03-31T12:00:03Z",
            },
        )
        await message_bus.publish(complete_msg)
        await asyncio.sleep(0.2)

        # Verify journal was written (resolve_storage_roots for runtime path)
        from polaris.kernelone.storage import resolve_storage_roots

        roots = resolve_storage_roots(temp_workspace)
        logs_dir = Path(roots.runtime_root) / "runs" / "session-1" / "logs"
        assert (logs_dir / "journal.norm.jsonl").exists()

        # Verify archive was written (resolve_polaris_roots for history path)
        from polaris.cells.storage.layout.internal.layout_business import resolve_polaris_roots

        hp_roots = resolve_polaris_roots(temp_workspace)
        archive_dir = Path(hp_roots.history_root) / "runs" / turn_id
        assert (archive_dir / "stream_events.jsonl.gz").exists()

        await journal_sink.stop()
        await archive_sink.stop()
        await audit_sink.stop()
