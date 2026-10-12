"""A scoped task may learn from a committed read batch before deciding a write."""

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.kernel.transaction_turn_completion import _resolve_completion_status
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel
from polaris.cells.roles.kernel.internal.turn_transaction_controller import TurnTransactionController


@pytest.mark.asyncio
async def test_materialization_read_batch_returns_progress_without_forced_write_retry(tmp_path: Path) -> None:
    source = tmp_path / "input.py"
    source.write_text("VALUE = 42\n", encoding="utf-8")
    provider_calls: list[dict[str, Any]] = []
    dispatched_batches: list[Any] = []

    async def provider(request: dict[str, Any]) -> dict[str, Any]:
        provider_calls.append(request)
        assert len(provider_calls) == 1, "An observation must not trigger another decision in the same turn"
        return {
            "content": "",
            "tool_calls": [
                {
                    "id": "read_input",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"file":"input.py"}'},
                }
            ],
            "model": "fixture",
            "usage": {},
        }

    async def tools(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        dispatched_batches.append((tool_name, arguments))
        return {
            "success": True,
            "tool": tool_name,
            "result": {"file": "input.py", "content": source.read_text(encoding="utf-8")},
        }

    config = TransactionConfig(domain="code", role_id="director", workspace=str(tmp_path), mutation_guard_mode="strict")
    config.continuous_execution = True
    kernel = TransactionKernel(llm_provider=provider, tool_runtime=tools, config=config)
    result = await kernel.execute(
        "observation-turn",
        [{"role": "user", "content": "[mode:materialize]\nImplement answer.py using the value from input.py"}],
        [
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "write_file"}},
        ],
    )
    assert len(provider_calls) == 1
    assert len(dispatched_batches) == 1
    assert result["kind"] == "continue_multi_turn"
    assert result["batch_receipt"]["results"][0]["result"]["content"] == "VALUE = 42\n"
    assert not (tmp_path / "answer.py").exists()


def test_observation_completion_is_pending_without_invented_error() -> None:
    metadata: dict[str, Any] = {}
    error, is_complete = _resolve_completion_status(
        kind="continue_multi_turn",
        finalization={"needs_followup_workflow": True, "workflow_reason": "observation"},
        metadata=metadata,
    )
    assert error is None
    assert is_complete is False
    assert metadata["needs_followup_workflow"] is True
    assert metadata["workflow_reason"] == "observation"


def test_terminal_tool_failure_is_failed_in_nonstream_commit_projection() -> None:
    assert (
        TurnTransactionController._completion_status_from_result(
            {
                "kind": "tool_failure",
                "finalization": {"error": "POLICY_FAILURE"},
            }
        )
        == "failed"
    )


def test_continuous_session_prompt_does_not_claim_there_is_no_next_turn() -> None:
    config = TransactionConfig(domain="code", continuous_execution=True)
    controller = TurnTransactionController(llm_provider=None, tool_runtime=None, config=config)
    messages = controller._build_decision_messages(
        [{"role": "user", "content": "[mode:materialize]\n当前阶段: implementing\nImplement answer.py"}],
        [
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "write_file"}},
        ],
    )
    prompt = "\n".join(str(message.get("content", "")) for message in messages)
    assert "there is no subsequent turn" not in prompt
    assert "You MUST call at least one callable write tool in this turn" not in prompt


@pytest.mark.asyncio
async def test_real_failed_command_stays_in_receipt_for_next_turn(tmp_path: Path) -> None:
    gate = tmp_path / "gate.py"
    gate.write_text("raise AssertionError('behavior mismatch')\n", encoding="utf-8")

    async def provider(request: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": "",
            "model": "fixture",
            "usage": {},
            "tool_calls": [
                {
                    "id": "gate",
                    "type": "function",
                    "function": {
                        "name": "execute_command",
                        "arguments": '{"command":"python gate.py"}',
                    },
                }
            ],
        }

    async def tools(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        completed = subprocess.run(
            [sys.executable, str(gate)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert completed.returncode == 1
        return {
            "success": False,
            "tool": tool_name,
            "error": completed.stderr,
            "result": {"exit_code": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr},
        }

    kernel = TransactionKernel(
        llm_provider=provider,
        tool_runtime=tools,
        config=TransactionConfig(
            domain="code",
            role_id="director",
            workspace=str(tmp_path),
            mutation_guard_mode="strict",
            continuous_execution=True,
        ),
    )
    result = await kernel.execute(
        "verification-turn",
        [{"role": "user", "content": "[mode:materialize]\nImplement answer.py and verify its behavior"}],
        [
            {"type": "function", "function": {"name": "execute_command"}},
            {"type": "function", "function": {"name": "write_file"}},
        ],
    )
    assert result["kind"] == "continue_multi_turn"
    row = result["batch_receipt"]["results"][0]
    assert row["status"] != "success"
    assert "behavior mismatch" in str(row)


@pytest.mark.parametrize("failure_class", ["POLICY_FAILURE", "MISSING_EFFECT_RECEIPT"])
@pytest.mark.asyncio
async def test_policy_and_missing_effect_feedback_remain_terminal(tmp_path: Path, failure_class: str) -> None:
    async def provider(request: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": "",
            "model": "fixture",
            "usage": {},
            "tool_calls": [
                {
                    "id": "denied-read",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"file":"input.py"}'},
                }
            ],
        }

    async def tools(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"success": False, "error": "denied by execution policy", "result": {"failure_class": failure_class}}

    kernel = TransactionKernel(
        llm_provider=provider,
        tool_runtime=tools,
        config=TransactionConfig(domain="code", workspace=str(tmp_path), continuous_execution=True),
    )
    result = await kernel.execute(
        "denied-turn",
        [{"role": "user", "content": "[mode:materialize]\nImplement answer.py"}],
        [
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "write_file"}},
        ],
    )
    assert result["kind"] != "continue_multi_turn"
    assert not result.get("finalization", {}).get("needs_followup_workflow")
    assert result["finalization"]["error"]
    assert failure_class in str(result["batch_receipt"])
