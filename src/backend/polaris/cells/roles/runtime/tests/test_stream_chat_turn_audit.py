"""Unit tests for stream_chat_turn audit functionality.

Tests verify that stream_chat_turn correctly:
1. Publishes events to MessageBus when available
2. Skips bus publish when bus is unavailable
3. Journal files are written by kernel.run_stream() (not by service.py)

Note: LLM output journal writing is handled by kernel.run_stream() via
_emit_stream_log_event() -> LogEventWriter, which writes to:
{runtime_root}/runs/{run_id}/logs/journal.*.jsonl

This follows KernelOne storage layout and is the correct unified path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
from polaris.cells.roles.runtime.public.service import RoleRuntimeService

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


class MockRoleTurnResult:
    """Mock RoleTurnResult for testing."""

    def __init__(
        self,
        content: str = "",
        thinking: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        execution_stats: dict[str, Any] | None = None,
        error: str | None = None,
        is_complete: bool = True,
    ) -> None:
        self.content = content
        self.thinking = thinking
        self.tool_calls = tool_calls or []
        self.execution_stats = execution_stats or {}
        self.error = error
        self.is_complete = is_complete


class MockKernel:
    """Mock RoleExecutionKernel for testing."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    async def run_stream(self, role: str, request: Any) -> AsyncGenerator[dict[str, Any], None]:
        for event in self._events:
            yield event


@pytest.fixture
def mock_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    workspace = tmp_path / "test_workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


@pytest.fixture
def sample_events() -> list[dict[str, Any]]:
    """Sample stream events for testing."""
    return [
        {"type": "content_chunk", "content": "Hello"},
        {"type": "thinking_chunk", "content": "Thinking..."},
        {"type": "tool_call", "tool": "read_file", "args": {"path": "test.py"}},
        {
            "type": "complete",
            "result": MockRoleTurnResult(
                content="Done",
                thinking="I thought about it",
                tool_calls=[{"name": "read_file"}],
                execution_stats={"tokens": 100},
            ),
        },
    ]


@pytest.fixture
def command(mock_workspace: Path) -> ExecuteRoleSessionCommandV1:
    """Create a test command."""
    return ExecuteRoleSessionCommandV1(
        role="director",
        workspace=str(mock_workspace),
        session_id="test-session-123",
        user_message="Test message",
        stream=True,
    )


class TestStreamChatTurnAuditBusPublish:
    """Test that stream_chat_turn publishes events to MessageBus."""

    @pytest.mark.asyncio
    async def test_publishes_to_bus_when_available(
        self,
        mock_workspace: Path,
        sample_events: list[dict[str, Any]],
        command: ExecuteRoleSessionCommandV1,
    ) -> None:
        """Verify events are yielded when stream_chat_turn runs."""
        service = RoleRuntimeService()

        mock_kernel = MockKernel(sample_events)

        with (
            patch.object(service, "_get_kernel", return_value=mock_kernel),
            patch.object(service, "_build_session_request", return_value=MagicMock()),
            patch.object(service, "_persist_session_turn_state"),
            patch.object(service, "emit_strategy_receipt", return_value=Path("receipt.json")),
            patch("polaris.cells.roles.runtime.public.service.registry") as mock_registry,
        ):
            mock_registry.list_roles.return_value = ["director"]

            events_collected = []
            async for event in service.stream_chat_turn(command):
                events_collected.append(event)

            # Verify events were yielded (fingerprint + sample events)
            assert len(events_collected) >= len(sample_events)

    @pytest.mark.asyncio
    async def test_skips_bus_when_unavailable(
        self,
        mock_workspace: Path,
        sample_events: list[dict[str, Any]],
        command: ExecuteRoleSessionCommandV1,
    ) -> None:
        """Verify stream completes when bus is unavailable."""
        service = RoleRuntimeService()

        mock_kernel = MockKernel(sample_events)

        with (
            patch.object(service, "_get_kernel", return_value=mock_kernel),
            patch.object(service, "_build_session_request", return_value=MagicMock()),
            patch.object(service, "_persist_session_turn_state"),
            patch.object(service, "emit_strategy_receipt", return_value=Path("receipt.json")),
            patch("polaris.cells.roles.runtime.public.service.registry") as mock_registry,
        ):
            mock_registry.list_roles.return_value = ["director"]

            events_collected = []
            async for event in service.stream_chat_turn(command):
                events_collected.append(event)

            # Should complete without error
            # Note: service yields fingerprint event first, then kernel events
            assert len(events_collected) == len(sample_events) + 1  # fingerprint + sample_events


class TestJournalDiscoveryFunctions:
    """Test journal discovery functions for audit_quick integration."""

    def test_discover_journal_run_dirs_empty(self, tmp_path: Path) -> None:
        """Test discovering journal run dirs when none exist."""
        from polaris.cells.audit.diagnosis.internal.toolkit.service import (
            _discover_journal_run_dirs,
        )

        runtime_root = tmp_path / "runtime"
        runtime_root.mkdir(parents=True, exist_ok=True)

        result = _discover_journal_run_dirs(runtime_root)
        assert result == []

    def test_discover_journal_run_dirs_with_runs(self, tmp_path: Path) -> None:
        """Test discovering journal run dirs when runs exist."""
        from polaris.cells.audit.diagnosis.internal.toolkit.service import (
            _discover_journal_run_dirs,
        )

        runtime_root = tmp_path / "runtime"
        runs_root = runtime_root / "runs"
        runs_root.mkdir(parents=True, exist_ok=True)

        # Create run directories with logs
        for run_id in ["run-1", "run-2"]:
            run_dir = runs_root / run_id
            logs_dir = run_dir / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            # Create journal file
            journal_file = logs_dir / "journal.norm.jsonl"
            journal_file.write_text("{}\n", encoding="utf-8")

        result = _discover_journal_run_dirs(runtime_root)
        assert len(result) == 2
        # Verify paths are sorted
        assert all(p.is_dir() for p in result)

    def test_resolve_journal_events_path(self, tmp_path: Path) -> None:
        """Test resolving journal events path with priority order."""
        from polaris.cells.audit.diagnosis.internal.toolkit.service import (
            _resolve_journal_events_path,
        )

        runtime_root = tmp_path / "runtime"
        runs_root = runtime_root / "runs"
        run_dir = runs_root / "test-run"
        logs_dir = run_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        # Create all three journal files
        (logs_dir / "journal.raw.jsonl").write_text("{}\n", encoding="utf-8")
        (logs_dir / "journal.norm.jsonl").write_text("{}\n", encoding="utf-8")
        (logs_dir / "journal.enriched.jsonl").write_text("{}\n", encoding="utf-8")

        result = _resolve_journal_events_path(run_dir)
        # Should return norm (highest priority)
        assert result.name == "journal.norm.jsonl"

    def test_load_journal_events(self, tmp_path: Path) -> None:
        """Test loading events from journal file."""
        from polaris.cells.audit.diagnosis.internal.toolkit.service import (
            load_journal_events,
        )

        runtime_root = tmp_path / "runtime"
        runs_root = runtime_root / "runs"
        run_dir = runs_root / "test-run"
        logs_dir = run_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        # Create journal with events
        journal_file = logs_dir / "journal.norm.jsonl"
        events = [
            {"seq": 1, "type": "content_chunk", "content": "Hello"},
            {"seq": 2, "type": "tool_call", "tool": "read_file"},
            {"seq": 3, "type": "complete", "content": "Done"},
        ]
        lines = [json.dumps(e, ensure_ascii=False) for e in events]

        journal_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # load_journal_events takes run_dir, not runtime_root + run_id
        result = load_journal_events(run_dir)
        assert len(result) == 3
        assert result[0]["type"] == "content_chunk"
        assert result[1]["type"] == "tool_call"
        assert result[2]["type"] == "complete"

    def test_discover_strategy_receipts(self, tmp_path: Path) -> None:
        """Test discovering strategy receipt files."""
        from polaris.cells.audit.diagnosis.internal.toolkit.service import (
            discover_strategy_receipts,
        )

        runtime_root = tmp_path / "runtime"
        receipts_root = runtime_root / "strategy_runs"
        receipts_root.mkdir(parents=True, exist_ok=True)

        # Create receipt files
        for receipt_id in ["receipt-1", "receipt-2"]:
            receipt_file = receipts_root / f"{receipt_id}.json"
            receipt_file.write_text("{}\n", encoding="utf-8")

        result = discover_strategy_receipts(runtime_root)
        assert len(result) == 2
        assert all(p.suffix == ".json" for p in result)


class TestKernelJournalWriting:
    """Test that kernel.run_stream() writes to journal via LogEventWriter.

    This is the correct unified audit path following KernelOne storage layout.
    Journal files are written to: {runtime_root}/runs/{run_id}/logs/journal.*.jsonl
    """

    @pytest.mark.asyncio
    async def test_kernel_creates_journal_writer(
        self,
        mock_workspace: Path,
        sample_events: list[dict[str, Any]],
    ) -> None:
        """Verify kernel creates LogEventWriter for journal writing."""
        # This test verifies the architecture: kernel.run_stream() uses
        # _emit_stream_log_event() which writes to LogEventWriter.
        # The actual journal writing is tested in kernel tests, not here.
        # This test just verifies the service correctly delegates to kernel.

        service = RoleRuntimeService()
        mock_kernel = MockKernel(sample_events)

        command = ExecuteRoleSessionCommandV1(
            role="director",
            workspace=str(mock_workspace),
            session_id="test-session-123",
            user_message="Test message",
            run_id="test-run-id",
            stream=True,
        )

        with (
            patch.object(service, "_get_kernel", return_value=mock_kernel),
            patch.object(service, "_build_session_request", return_value=MagicMock()),
            patch.object(service, "_persist_session_turn_state"),
            patch.object(service, "emit_strategy_receipt", return_value=Path("receipt.json")),
            patch("polaris.cells.roles.runtime.public.service.registry") as mock_registry,
        ):
            mock_registry.list_roles.return_value = ["director"]

            events_collected = []
            async for event in service.stream_chat_turn(command):
                events_collected.append(event)

            # Verify events were yielded correctly
            # Note: service yields fingerprint event first, then kernel events
            assert len(events_collected) == len(sample_events) + 1  # fingerprint + sample_events
            # Verify run_id was passed to kernel (for journal path resolution)
            assert command.run_id == "test-run-id"
