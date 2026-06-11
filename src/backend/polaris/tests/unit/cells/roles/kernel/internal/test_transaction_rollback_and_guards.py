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
    filter_out_of_scope_write_invocations,
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
# mutation target drift filtering
# ---------------------------------------------------------------------------


class TestMutationTargetDriftFiltering:
    def test_drops_extra_out_of_scope_write_when_valid_target_write_exists(self) -> None:
        invocations: list[dict[str, Any]] = [
            {
                "tool_name": "write_file",
                "arguments": {"file": "package.json", "content": "{}"},
            },
            {
                "tool_name": "write_file",
                "arguments": {"file": "README.md", "content": "# App"},
            },
            {
                "tool_name": "write_file",
                "arguments": {"file": "pyproject.toml", "content": "[project]"},
            },
        ]

        filtered, dropped = filter_out_of_scope_write_invocations(
            "Implement Project Scaffolding target_files: package.json, README.md",
            invocations,
        )

        assert dropped == ("pyproject.toml",)
        assert [item["arguments"]["file"] for item in filtered] == ["package.json", "README.md"]

    def test_keeps_all_out_of_scope_writes_for_strict_guard_failure(self) -> None:
        invocations: list[dict[str, Any]] = [
            {
                "tool_name": "write_file",
                "arguments": {"file": "pyproject.toml", "content": "[project]"},
            }
        ]

        filtered, dropped = filter_out_of_scope_write_invocations(
            "Implement Project Scaffolding target_files: package.json, README.md",
            invocations,
        )

        assert dropped == ()
        assert filtered == invocations


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
    async def test_readonly_retry_batch_ignites_bootstrap_immediately(self, orchestrator: RetryOrchestrator) -> None:
        """A failed retry attempt that emitted ONLY safe read tools must switch to the
        bootstrap read path on that very attempt (not just on the final one).

        Weak models respond to "you must write" by first asking to read the target
        file — that is correct recovery behavior, and burning the remaining retries
        on it traps models that have never seen the file content.
        """
        ledger = TurnLedger(turn_id="t-readonly-bootstrap")
        state_machine = TurnStateMachine(turn_id="t-readonly-bootstrap")
        state_machine.state = TurnState.TOOL_BATCH_EXECUTING

        execute_calls: list[int] = []

        async def _violating_execute(*_a: Any, **_kw: Any) -> Any:
            execute_calls.append(1)
            raise RuntimeError("single_batch_contract_violation: retry batch used tools outside narrowed set")

        orchestrator.execute_tool_batch = _violating_execute  # type: ignore[method-assign]

        mock_decision: dict[str, Any] = {
            "kind": TurnDecisionKind.TOOL_BATCH,
            "tool_batch": {"invocations": [{"tool": "read_file", "arguments": {"file": "django/core/checks.py"}}]},
            "metadata": {"workspace": "."},
        }
        orchestrator.decoder.decode = MagicMock(return_value=mock_decision)  # type: ignore[method-assign]

        mock_response = MagicMock()
        mock_response.native_tool_calls = []
        orchestrator.call_llm_for_decision = AsyncMock(return_value=mock_response)  # type: ignore[method-assign]

        # Entering the bootstrap path is observable via execute_read_bootstrap_batch;
        # returning None there makes the orchestrator fail fast with a distinct error.
        bootstrap_calls: list[str] = []

        async def _bootstrap(*_a: Any, **kwargs: Any) -> None:
            bootstrap_calls.append(str(kwargs.get("turn_id")))
            return None

        orchestrator.execute_read_bootstrap_batch = _bootstrap  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="bootstrap read receipt missing"):
            await orchestrator.retry_tool_batch_after_contract_violation(
                turn_id="t-readonly-bootstrap",
                context=[{"role": "user", "content": "fix the bug in checks.py"}],
                tool_definitions=[],
                state_machine=state_machine,
                ledger=ledger,
                stream=False,
            )

        # Bootstrap must have been entered after the FIRST failed read-only attempt.
        assert bootstrap_calls == ["t-readonly-bootstrap"]
        assert len(execute_calls) == 1

    @pytest.mark.asyncio
    async def test_readonly_original_batch_bootstraps_without_retry_reask(
        self, orchestrator: RetryOrchestrator
    ) -> None:
        """Wave-5: a READ-ONLY ORIGINAL violating batch must be bootstrapped AS-IS.

        Discarding the model's (often correct-path) reads and re-asking makes weak
        models emit worse calls under retry pressure — run10a live capture showed the
        original `read_file django/core/checks/model_checks.py` replaced by
        hallucinated `C:\\Users\\user\\Desktop\\vue-element-admin\\...` reads.
        """
        ledger = TurnLedger(turn_id="t-w5")
        state_machine = TurnStateMachine(turn_id="t-w5")
        state_machine.state = TurnState.TOOL_BATCH_EXECUTING

        original_decision: dict[str, Any] = {
            "kind": TurnDecisionKind.TOOL_BATCH,
            "tool_batch": {
                "invocations": [
                    {
                        "call_id": "c-orig",
                        "tool_name": "read_file",
                        "arguments": {"file": "django/core/checks/model_checks.py"},
                    }
                ]
            },
            "metadata": {"workspace": "."},
        }

        captured_batches: list[Any] = []

        async def _bootstrap(*_a: Any, **kwargs: Any) -> None:
            captured_batches.append(kwargs.get("tool_batch"))
            return None

        orchestrator.execute_read_bootstrap_batch = _bootstrap  # type: ignore[method-assign]
        llm_calls: list[int] = []

        async def _llm(*_a: Any, **_kw: Any) -> Any:
            llm_calls.append(1)
            response = MagicMock()
            response.native_tool_calls = []
            return response

        orchestrator.call_llm_for_decision = _llm  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="bootstrap read receipt missing"):
            await orchestrator.retry_tool_batch_after_contract_violation(
                turn_id="t-w5",
                context=[{"role": "user", "content": "fix the E028 bug"}],
                tool_definitions=[],
                state_machine=state_machine,
                ledger=ledger,
                stream=False,
                original_decision=original_decision,
            )

        # The ORIGINAL invocations were bootstrapped; no retry LLM re-ask happened.
        assert len(captured_batches) == 1
        invocations = captured_batches[0].get("invocations")
        assert invocations[0]["arguments"]["file"] == "django/core/checks/model_checks.py"
        assert llm_calls == []

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

    @pytest.mark.asyncio
    async def test_bootstrap_followup_uses_deterministic_write_when_model_repeats_no_write(
        self,
        orchestrator: RetryOrchestrator,
        tmp_path: Path,
    ) -> None:
        """Weak local models may keep emitting execute_command after bootstrap; use write_file fallback.

        Uses an isolated workspace: the deterministic fallback only fires for
        user-named targets that do NOT already exist (P0-B de-fang, 2026-06-10).
        """
        orchestrator.config.max_retry_attempts = 1
        ledger = TurnLedger(turn_id="t-006")
        state_machine = TurnStateMachine(turn_id="t-006")
        state_machine.state = TurnState.TOOL_BATCH_EXECUTING
        tool_definitions = [
            {"type": "function", "function": {"name": "repo_tree"}},
            {"type": "function", "function": {"name": "execute_command"}},
            {"type": "function", "function": {"name": "write_file"}},
        ]
        bootstrap_decision: dict[str, Any] = {
            "kind": TurnDecisionKind.TOOL_BATCH,
            "tool_batch": {
                "invocations": [
                    {
                        "tool_name": "repo_tree",
                        "arguments": {"path": "."},
                    }
                ]
            },
            "metadata": {"workspace": str(tmp_path)},
        }
        no_write_decision: dict[str, Any] = {
            "kind": TurnDecisionKind.TOOL_BATCH,
            "tool_batch": {
                "invocations": [
                    {
                        "tool_name": "execute_command",
                        "arguments": {"cmd": "npm test"},
                    }
                ]
            },
            "metadata": {},
        }
        decoded_decisions = [bootstrap_decision, no_write_decision, no_write_decision, no_write_decision]
        orchestrator.decoder.decode = MagicMock(side_effect=decoded_decisions)  # type: ignore[method-assign]
        mock_response = MagicMock()
        mock_response.native_tool_calls = []
        orchestrator.call_llm_for_decision = AsyncMock(return_value=mock_response)  # type: ignore[method-assign]
        orchestrator.execute_read_bootstrap_batch = AsyncMock(
            return_value={
                "results": [
                    {
                        "tool_name": "read_file",
                        "status": "error",
                        "arguments": {"file": "package.json"},
                        "result": {"file": "package.json", "error": "File not found: package.json"},
                    }
                ]
            }
        )  # type: ignore[method-assign]
        write_calls: list[dict[str, Any]] = []

        async def _execute_tool_batch(decision: Any, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            tool_batch = decision.get("tool_batch")
            invocations = list(tool_batch.get("invocations", []) if isinstance(tool_batch, dict) else [])
            if not invocations and hasattr(tool_batch, "invocations"):
                invocations = list(tool_batch.invocations)
            tool_names = [str(item.get("tool_name") or item.tool_name) for item in invocations]
            if "write_file" not in tool_names:
                raise RuntimeError(
                    "single_batch_contract_violation: mutation requested but no write tool invocation in decision batch"
                )
            invocation = invocations[0]
            raw_arguments = invocation.get("arguments") if isinstance(invocation, dict) else invocation.arguments
            arguments = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
            write_calls.append(dict(arguments))
            return {"ok": True, "tool_results": [{"tool": "write_file", "success": True, "result": arguments}]}

        orchestrator.execute_tool_batch = _execute_tool_batch  # type: ignore[method-assign]

        result = await orchestrator.retry_tool_batch_after_contract_violation(
            turn_id="t-006",
            context=[{"role": "user", "content": "Create project scaffold target_files: package.json"}],
            tool_definitions=tool_definitions,
            state_machine=state_machine,
            ledger=ledger,
            stream=False,
        )

        assert result["ok"] is True
        assert write_calls
        assert write_calls[0]["file"] == "package.json"
        assert "no test specified" not in write_calls[0]["content"]
        assert "package manifest check passed" in write_calls[0]["content"]
        assert '" --' in write_calls[0]["content"]

    @pytest.mark.asyncio
    async def test_bootstrap_followup_uses_deterministic_write_when_model_returns_no_tool_batch(
        self,
        orchestrator: RetryOrchestrator,
    ) -> None:
        """A blank/summary follow-up from a weak model must still materialize a scoped write."""
        orchestrator.config.max_retry_attempts = 1
        ledger = TurnLedger(turn_id="t-007")
        state_machine = TurnStateMachine(turn_id="t-007")
        state_machine.state = TurnState.TOOL_BATCH_EXECUTING
        tool_definitions = [
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "write_file"}},
        ]
        bootstrap_decision: dict[str, Any] = {
            "kind": TurnDecisionKind.TOOL_BATCH,
            "tool_batch": {
                "invocations": [
                    {
                        "tool_name": "read_file",
                        "arguments": {"file": "src/services/dag.service.ts"},
                    }
                ]
            },
            "metadata": {"workspace": "."},
        }
        no_tool_batch_decision: dict[str, Any] = {
            "kind": TurnDecisionKind.FINAL_ANSWER,
            "visible_message": "",
            "metadata": {},
        }
        orchestrator.decoder.decode = MagicMock(  # type: ignore[method-assign]
            side_effect=[bootstrap_decision, no_tool_batch_decision]
        )
        mock_response = MagicMock()
        mock_response.native_tool_calls = []
        orchestrator.call_llm_for_decision = AsyncMock(return_value=mock_response)  # type: ignore[method-assign]
        orchestrator.execute_read_bootstrap_batch = AsyncMock(
            return_value={
                "results": [
                    {
                        "tool_name": "read_file",
                        "status": "success",
                        "arguments": {"file": "src/services/dag.service.ts"},
                        "result": {
                            "file": "src/services/dag.service.ts",
                            "content": "import { Injectable } from '@nestjs/common';\nexport class DagService {}\n",
                        },
                    }
                ]
            }
        )  # type: ignore[method-assign]
        write_calls: list[dict[str, Any]] = []

        async def _execute_tool_batch(decision: Any, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            tool_batch = decision.get("tool_batch")
            invocations = list(tool_batch.get("invocations", []) if isinstance(tool_batch, dict) else [])
            if not invocations and hasattr(tool_batch, "invocations"):
                invocations = list(tool_batch.invocations)
            tool_names = [str(item.get("tool_name") or item.tool_name) for item in invocations]
            if "write_file" not in tool_names:
                raise RuntimeError(
                    "single_batch_contract_violation: mutation requested but no write tool invocation in decision batch"
                )
            invocation = invocations[0]
            raw_arguments = invocation.get("arguments") if isinstance(invocation, dict) else invocation.arguments
            arguments = dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
            write_calls.append(dict(arguments))
            return {"ok": True, "tool_results": [{"tool": "write_file", "success": True, "result": arguments}]}

        orchestrator.execute_tool_batch = _execute_tool_batch  # type: ignore[method-assign]

        result = await orchestrator.retry_tool_batch_after_contract_violation(
            turn_id="t-007",
            context=[
                {
                    "role": "user",
                    "content": "Implement DAG Dependency Validation Engine target_files: src/services/dag.service.ts",
                }
            ],
            tool_definitions=tool_definitions,
            state_machine=state_machine,
            ledger=ledger,
            stream=False,
        )

        assert result["ok"] is True
        assert write_calls
        assert write_calls[0]["file"] == "src/services/dag.service.ts"
        assert "@nestjs/common" not in write_calls[0]["content"]
        assert "DagValidationError" in write_calls[0]["content"]
        assert "Circular task dependency detected" in write_calls[0]["content"]
