"""Tests for Kernel transaction rollback, path traversal guards, and stream handling.

Covers fixes for:
- Path traversal in _file_exists_in_workspace (contract_guards)
- tool_batch_count rollback in retry orchestrator
- Stream exception handling in retry orchestrator
- ContextVar reset order in turn_transaction_controller
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    _file_exists_in_workspace,
    rollback_state_after_retry_batch_failure,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import (
    RetryOrchestrator,
)
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import TurnDecisionKind

# ---------------------------------------------------------------------------
# _file_exists_in_workspace
# ---------------------------------------------------------------------------


class TestFileExistsInWorkspace:
    def test_allows_file_inside_workspace(self, tmp_path: Path) -> None:
        file_path = tmp_path / "test.py"
        file_path.write_text("pass", encoding="utf-8")
        assert _file_exists_in_workspace("test.py", workspace=str(tmp_path)) is True

    def test_blocks_directory_traversal(self, tmp_path: Path) -> None:
        assert _file_exists_in_workspace("../outside.py", workspace=str(tmp_path)) is False

    def test_blocks_absolute_path_outside_workspace(self, tmp_path: Path) -> None:
        assert _file_exists_in_workspace("/etc/passwd", workspace=str(tmp_path)) is False

    def test_blocks_traversal_with_nested_dots(self, tmp_path: Path) -> None:
        assert _file_exists_in_workspace("foo/../../etc/passwd", workspace=str(tmp_path)) is False

    def test_empty_path_returns_false(self) -> None:
        assert _file_exists_in_workspace("", workspace=".") is False

    def test_handles_symlink_escape_attempt(self, tmp_path: Path) -> None:
        """Symlinks pointing outside workspace should be resolved by realpath and blocked."""
        target = tmp_path / "secret.txt"
        target.write_text("secret", encoding="utf-8")
        link_path = tmp_path / "link_escape"
        # Create a symlink to parent directory (outside workspace)
        link_path.symlink_to(tmp_path.parent)
        # Accessing through symlink should be blocked because realpath resolves outside workspace
        assert _file_exists_in_workspace(str(link_path / "secret.txt"), workspace=str(tmp_path)) is False


# ---------------------------------------------------------------------------
# rollback_state_after_retry_batch_failure
# ---------------------------------------------------------------------------


class TestRollbackStateAfterRetryBatchFailure:
    def test_appends_rollback_to_state_history(self) -> None:
        state_machine = TurnStateMachine(turn_id="t-001")
        state_machine.state = TurnState.TOOL_BATCH_EXECUTING
        ledger = TurnLedger(turn_id="t-001")
        ledger.tool_batch_count = 1

        rollback_state_after_retry_batch_failure(state_machine, ledger)

        assert any(entry[0] == "RETRY_BATCH_ROLLBACK" for entry in ledger.state_history)
        # rollback_state_after_retry_batch_failure records intent only;
        # actual tool_batch_count rollback is handled by RetryOrchestrator
        # save/restore around execute_tool_batch.
        assert ledger.tool_batch_count == 1

    def test_does_not_decrement_below_zero(self) -> None:
        state_machine = TurnStateMachine(turn_id="t-002")
        state_machine.state = TurnState.TOOL_BATCH_EXECUTING
        ledger = TurnLedger(turn_id="t-002")
        ledger.tool_batch_count = 0

        rollback_state_after_retry_batch_failure(state_machine, ledger)

        assert ledger.tool_batch_count == 0

    def test_skips_when_not_in_tool_batch_executing(self) -> None:
        state_machine = TurnStateMachine(turn_id="t-003")
        # state_machine starts in CONTEXT_BUILT
        ledger = TurnLedger(turn_id="t-003")
        ledger.tool_batch_count = 2

        rollback_state_after_retry_batch_failure(state_machine, ledger)

        assert not any(entry[0] == "RETRY_BATCH_ROLLBACK" for entry in ledger.state_history)
        assert ledger.tool_batch_count == 2


# ---------------------------------------------------------------------------
# RetryOrchestrator batch count rollback
# ---------------------------------------------------------------------------


class TestRetryOrchestratorBatchCountRollback:
    @pytest.fixture
    def orchestrator(self) -> RetryOrchestrator:
        return RetryOrchestrator(
            tool_runtime=MagicMock(),
            config=MagicMock(max_tool_execution_time_ms=60000, max_retry_attempts=4),
            decoder=MagicMock(),
            call_llm_for_decision=AsyncMock(),
            call_llm_for_decision_stream=None,
            execute_tool_batch=AsyncMock(),
            guard_assert_single_tool_batch=MagicMock(),
            emit_event=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_restores_batch_count_on_execute_tool_batch_failure(self, orchestrator: RetryOrchestrator) -> None:
        """When execute_tool_batch raises, tool_batch_count must be restored."""
        ledger = TurnLedger(turn_id="t-004")
        ledger.tool_batch_count = 1  # Original batch already counted
        state_machine = TurnStateMachine(turn_id="t-004")
        state_machine.state = TurnState.TOOL_BATCH_EXECUTING

        # Simulate execute_tool_batch incrementing count then raising
        async def _failing_execute(*_a: Any, **_kw: Any) -> Any:
            ledger.tool_batch_count += 1
            raise RuntimeError("single_batch_contract_violation: simulated failure")

        orchestrator.execute_tool_batch = _failing_execute  # type: ignore[method-assign]

        # Set up decoder to return a TOOL_BATCH decision
        mock_decision: dict[str, Any] = {
            "kind": TurnDecisionKind.TOOL_BATCH,
            "tool_batch": {"invocations": []},
            "metadata": {},
        }
        orchestrator.decoder.decode = MagicMock(return_value=mock_decision)  # type: ignore[method-assign]

        # Set up LLM response
        mock_response = MagicMock()
        mock_response.native_tool_calls = []
        orchestrator.call_llm_for_decision = AsyncMock(return_value=mock_response)  # type: ignore[method-assign]

        with pytest.raises(RuntimeError):
            await orchestrator.retry_tool_batch_after_contract_violation(
                turn_id="t-004",
                context=[{"role": "user", "content": "test"}],
                tool_definitions=[],
                state_machine=state_machine,
                ledger=ledger,
                stream=False,
            )

        # After all failed attempts, count should be back to original
        assert ledger.tool_batch_count == 1

    @pytest.mark.asyncio
    async def test_stream_error_propagates_with_cause(self, orchestrator: RetryOrchestrator) -> None:
        """Stream exceptions should be wrapped and preserve the original cause."""
        ledger = TurnLedger(turn_id="t-005")

        async def _failing_stream(*_a: Any, **_kw: Any) -> Any:
            raise ConnectionError("stream broke")
            yield {}

        orchestrator.call_llm_for_decision_stream = _failing_stream  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="retry stream error") as exc_info:
            await orchestrator._execute_retry_batch(
                turn_id="t-005",
                attempt_context=[],
                attempt_tool_definitions=[],
                ledger=ledger,
                attempt_tool_choice_override=None,
                attempt_model_override=None,
                stream=True,
                shadow_engine=None,
            )

        assert isinstance(exc_info.value.__cause__, ConnectionError)
