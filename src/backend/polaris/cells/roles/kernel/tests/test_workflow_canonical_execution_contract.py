"""Native handoff preserves the current tool surface and actual receipts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
)
from polaris.cells.roles.kernel.internal.exploration_workflow import ExplorationWorkflowRuntime
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel
from polaris.cells.roles.kernel.public.turn_events import CompletionEvent, ToolBatchEvent


def _surface(name: str) -> list[dict[str, Any]]:
    return [{"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}]


def _kernel(tmp_path: Path, *, handoff: bool = True, failed: bool = False):
    calls: list[tuple[str, dict[str, Any]]] = []
    bootstrap_fact_stream_workspace(BootstrapFactStreamWorkspaceCommandV1(
        workspace=tmp_path,
        logical_streams=("run_ledger",),
        maintenance_reason="private handoff execution contract fixture",
    ))

    async def provider(_request: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": "[handoff_workflow]" if handoff else "Read the file",
            "tool_calls": [{"id": "native_read", "type": "function", "function": {
                "name": "read_file", "arguments": json.dumps({"path": "main.py"}),
            }}],
            "model": "private-test-provider",
            "usage": {},
        }

    async def tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append((name, arguments))
        if failed:
            return {"success": False, "error": "read denied", "authorized": False}
        return {"success": True, "result": "def main(): pass"}

    kernel = TransactionKernel(
        llm_provider=provider,
        tool_runtime=tool,
        workflow_runtime=ExplorationWorkflowRuntime(tool_executor=tool, max_steps=1),
        config=TransactionConfig(workspace=str(tmp_path), run_id="handoff-contract", task_id="task-1"),
    )
    return kernel, calls


@pytest.mark.asyncio
async def test_native_handoff_records_original_call_once(tmp_path: Path) -> None:
    kernel, calls = _kernel(tmp_path)
    result = await kernel.execute("handoff", [{"role": "user", "content": "Read main.py"}], _surface("read_file"))
    assert len(calls) == 1
    assert result["kind"] == "handoff_workflow"
    ledger = result["ledger"]
    assert ledger.tool_batch_count == 1
    assert len(ledger.invocation_batches) == 1
    assert [row["call_id"] for row in ledger.tool_executions] == ["native_read"]
    assert result["metrics"]["tool_calls"] == 1
    receipt = result["workflow_context"]["batch_receipt"]
    assert receipt["results"][0]["call_id"] == "native_read"


@pytest.mark.asyncio
@pytest.mark.parametrize("handoff", [True, False], ids=["delegate", "retry-bootstrap"])
async def test_unoffered_native_call_never_executes(tmp_path: Path, handoff: bool) -> None:
    kernel, calls = _kernel(tmp_path, handoff=handoff)
    with pytest.raises(RuntimeError, match="outside|surface|offered"):
        await kernel.execute("unoffered", [{"role": "user", "content": "Read main.py"}], _surface("repo_tree"))
    assert calls == []


@pytest.mark.asyncio
async def test_failed_native_receipt_cannot_complete_handoff(tmp_path: Path) -> None:
    kernel, calls = _kernel(tmp_path, failed=True)
    result = await kernel.execute("failed", [{"role": "user", "content": "Read main.py"}], _surface("read_file"))
    assert len(calls) == 1
    assert result["kind"] == "workflow_execution_error"
    assert "FAILED" in result["state_trajectory"]
    assert result["workflow_context"]["batch_receipt"]["failure_count"] == 1


@pytest.mark.asyncio
async def test_stream_handoff_has_the_same_real_receipt(tmp_path: Path) -> None:
    kernel, calls = _kernel(tmp_path)
    events = [event async for event in kernel.execute_stream(
        "stream-handoff", [{"role": "user", "content": "Read main.py"}], _surface("read_file"),
    )]
    assert len(calls) == 1
    results = [event for event in events if isinstance(event, ToolBatchEvent) and event.status == "success"]
    assert [event.call_id for event in results] == ["native_read"]
    completion = [event for event in events if isinstance(event, CompletionEvent)][-1]
    assert completion.status == "handoff"
    assert completion.tool_calls == 1


@pytest.mark.asyncio
async def test_empty_tool_surface_does_not_authorize_handoff(tmp_path: Path) -> None:
    kernel, calls = _kernel(tmp_path)
    with pytest.raises(RuntimeError, match="tool_dispatch_dropped"):
        await kernel.execute("empty", [{"role": "user", "content": "Read main.py"}], [])
    assert calls == []
