"""Terminal observation consumes real DEO state without granting execution."""

from __future__ import annotations

import importlib
import importlib.util
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import _real_effect
from polaris.cells.runtime.task_runtime.public import (
    SettleTaskRuntimeExecutionAttemptCommandV1,
    settle_task_runtime_execution_attempt_typed,
)


def _api() -> Any:
    name = "polaris.cells.runtime.task_runtime.public.execution_effect_closure"
    assert importlib.util.find_spec(name), "terminal execution effect observer is missing"
    return importlib.import_module(name)


@pytest.mark.asyncio
async def test_real_terminal_commit_is_observed_without_any_writes(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("before", encoding="utf-8")
    attempt, rows = await _real_effect(tmp_path)
    settled = settle_task_runtime_execution_attempt_typed(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=str(tmp_path), identity=attempt, outcome="failed", summary="ordinary delivery failure"
        )
    )
    assert settled.success, settled
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    api = _api()
    result = api.query_execution_effect_closure(
        api.QueryExecutionEffectClosureV1(workspace=str(tmp_path), execution_attempt=attempt)
    )
    assert result.status == "closed", result
    assert result.execution_authority is False
    assert result.receipts[0].receipt_hash == rows[0]["effect_receipt"]["receipt_hash"]
    assert result.receipts[0].commit_event_id == rows[0]["effect_receipt_commit"]["event_id"]
    from polaris.cells.runtime.task_runtime.public import (
        GetDirectedEffectParentRegistryQueryV1,
        get_directed_effect_parent_registry,
    )

    assert (
        get_directed_effect_parent_registry(
            GetDirectedEffectParentRegistryQueryV1(
                workspace=str(tmp_path), task_id=attempt.task_id, execution_attempt=attempt
            )
        ).ok
        is False
    )
    assert result == api.query_execution_effect_closure(
        api.QueryExecutionEffectClosureV1(workspace=str(tmp_path), execution_attempt=attempt)
    )
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["active", "foreign_session", "foreign_run", "foreign_task", "foreign_workspace"])
async def test_active_or_foreign_attempt_never_returns_closure(tmp_path: Path, attack: str) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("before", encoding="utf-8")
    attempt, _rows = await _real_effect(tmp_path)
    if attack != "active":
        assert settle_task_runtime_execution_attempt_typed(
            SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=str(tmp_path), identity=attempt, outcome="failed", summary="failed"
            )
        ).success
        attempt = replace(
            attempt,
            **{
                "foreign_session": {"session_id": "foreign"},
                "foreign_run": {"run_id": "foreign"},
                "foreign_task": {"task_id": 999},
                "foreign_workspace": {"workspace": str(tmp_path / "other")},
            }[attack],
        )
    api = _api()
    result = api.query_execution_effect_closure(
        api.QueryExecutionEffectClosureV1(workspace=str(tmp_path), execution_attempt=attempt)
    )
    assert result.status != "closed"
    assert result.receipts == ()
    assert result.execution_authority is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attack", ["missing_session", "missing_parent_close", "corrupt_operation", "forged_terminal_session"]
)
async def test_unknown_or_open_mutation_never_returns_closure(tmp_path: Path, attack: str) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("before", encoding="utf-8")
    attempt, _rows = await _real_effect(tmp_path)
    if attack != "forged_terminal_session":
        assert settle_task_runtime_execution_attempt_typed(
            SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=str(tmp_path), identity=attempt, outcome="failed", summary="failed"
            )
        ).success
    session = tmp_path / ".polaris/runtime/tasks/task_1.session.json"
    if attack == "missing_session":
        session.unlink()
    elif attack == "forged_terminal_session":
        data = json.loads(session.read_text(encoding="utf-8"))
        data.update(status="failed", resumable=False)
        session.write_text(json.dumps(data), encoding="utf-8")
    elif attack == "missing_parent_close":
        registry = next((tmp_path / ".polaris/runtime/events").glob("deo_parent_registry_v1_*.jsonl"))
        lines = registry.read_text(encoding="utf-8").splitlines(keepends=True)
        registry.write_text("".join(lines[:-1]), encoding="utf-8")
    else:
        operation = next((tmp_path / ".polaris/runtime/events").glob("deo_operation_v1_*.jsonl"))
        operation.write_text(
            operation.read_text(encoding="utf-8").replace("receipt_hash", "forged_hash", 1), encoding="utf-8"
        )
    api = _api()
    result = api.query_execution_effect_closure(api.QueryExecutionEffectClosureV1(str(tmp_path), attempt))
    assert result.status != "closed", result
    assert result.receipts == ()
