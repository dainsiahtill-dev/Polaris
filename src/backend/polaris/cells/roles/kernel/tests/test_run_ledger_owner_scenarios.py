"""Test-only owner scenarios use real authority and commit; no Native qualification."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import os
from pathlib import Path
from typing import Any

import psutil
import pytest
from polaris.cells.events.fact_stream.public import (
    QueryPayloadFactEventsV1,
    QuerySegmentedFactLedgerHeadV1,
    query_payload_fact_events,
    query_payload_fact_ledger_presence,
)
from polaris.cells.roles.kernel.internal.transaction import outcome_commit
from polaris.cells.runtime.task_runtime.public.execution_effect_closure import (
    QueryExecutionEffectClosureV1,
    query_execution_effect_closure,
)
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter


def scenario_module() -> Any:
    name = "polaris.cells.roles.kernel.tests.run_ledger_owner_scenarios"
    assert importlib.util.find_spec(name) is not None, "Kernel-owned real commit scenario producer is missing"
    return importlib.import_module(name)


@pytest.mark.asyncio
async def test_failed_cli_scenario_uses_canonical_commit_and_real_negative_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    producer = scenario_module()
    calls = []
    original = outcome_commit.commit_turn_result

    def observe(**kwargs: Any) -> Any:
        calls.append((kwargs["run_id"], kwargs["task_id"], kwargs["completion_status"]))
        return original(**kwargs)

    monkeypatch.setattr(outcome_commit, "commit_turn_result", observe)
    case = await producer.produce_failed_cli_owner_case(tmp_path)
    batch = case["batch"]
    physical = batch["raw_results"][0]["result"]
    assert physical["exit_code"] == 2
    assert physical["effect_recovery"]["state"] == "DEAD_LETTER"
    assert batch["raw_results"][0]["effect_receipt"] is None
    assert batch["raw_results"][0]["effect_receipt_commit"] is None
    assert calls == [(case["attempt"].run_id, case["attempt"].external_task_id, "failed")]
    assert case["commit_receipt"].fact_stream == "roles.kernel.turn_outcomes.v3"
    facts = query_payload_fact_events(
        QueryPayloadFactEventsV1(workspace=str(tmp_path), logical_stream="roles.kernel.turn_outcomes.v3", limit=100)
    ).events
    assert [fact["event_type"] for fact in facts] == ["turn_outcome_prepared", "turn_outcome_committed"]
    committed = next(fact for fact in facts if fact["event_type"] == "turn_outcome_committed")
    assert committed["expanded_payload"]["session_binding"]["session_id"] == case["attempt"].session_id
    filesystem = KernelFileSystem(str(tmp_path), get_default_adapter())
    before = {str(path): filesystem.workspace_read_bytes(str(path)) for path in tmp_path.rglob("*") if path.is_file()}
    closure = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), case["attempt"]))
    assert closure.status == "closed", closure
    assert closure.receipts == () and len(closure.terminal_failures) == 1
    assert closure.terminal_failures[0].exit_code == 2
    assert closure.execution_authority is False
    assert before == {
        str(path): filesystem.workspace_read_bytes(str(path)) for path in tmp_path.rglob("*") if path.is_file()
    }
    assert not psutil.Process().children(recursive=True)
    filesystem.workspace_write_text(
        "synthetic-process-observation.json",
        json.dumps({"runner_pid": os.getpid(), "remaining_children": [], "exit_code": 2}),
        encoding="utf-8",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["v2", "v3"])
async def test_readonly_scenario_returns_public_values_and_recommits_without_io(tmp_path: Path, protocol: str) -> None:
    case = await scenario_module().produce_readonly_owner_case(tmp_path, protocol=protocol, recommit=True)
    assert not {"ledger", "commit_args", "durable"} & set(case)
    assert len(case["calls"]) == 3
    assert len(case["commit_receipts"]) == 2
    assert case["commit_receipts"][0] == case["commit_receipts"][1]
    from polaris.cells.roles.kernel.public.turn_contracts import BatchReceipt

    assert case["outcome"].execution.receipt.model_dump(mode="json") == BatchReceipt.model_validate(
        case["merged"]
    ).model_dump(mode="json")


@pytest.mark.asyncio
async def test_owner_commit_failure_is_not_replaced_by_a_fabricated_fact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(**kwargs: Any) -> Any:
        raise RuntimeError("synthetic canonical commit unavailable")

    monkeypatch.setattr(outcome_commit, "commit_turn_result", fail)
    with pytest.raises(RuntimeError, match="synthetic canonical commit unavailable"):
        await scenario_module().produce_readonly_owner_case(tmp_path)
    assert not query_payload_fact_ledger_presence(
        QuerySegmentedFactLedgerHeadV1(workspace=str(tmp_path), logical_stream="roles.kernel.turn_outcomes.v3")
    )


@pytest.mark.parametrize("scenario", ["deferred_followup", "newer_missing_verifier"])
def test_boundary_scenario_calls_original_producer_before_public_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    from polaris.cells.events.fact_stream.public import QueryFactEventsV1, query_fact_events
    from polaris.cells.roles.kernel.internal.kernel import task_boundary

    producer = scenario_module()
    attempt = producer.claim_synthetic_attempt(str(tmp_path))
    name = (
        "append_deferred_followup_task_boundary_verdict"
        if scenario == "deferred_followup"
        else "append_director_task_boundary_verdict"
    )
    original = getattr(task_boundary, name)
    called = []

    def observe(**kwargs: Any) -> Any:
        called.append(kwargs["task_id"])
        return original(**kwargs)

    monkeypatch.setattr(task_boundary, name, observe)
    verdict = producer.produce_task_boundary_case(
        tmp_path, task_id=attempt.external_task_id, run_id=attempt.run_id, scenario=scenario
    )
    facts = query_fact_events(
        QueryFactEventsV1(workspace=str(tmp_path), stream="execution.control_plane", strict_integrity=True, limit=100)
    ).events
    assert called == [attempt.external_task_id]
    assert len(facts) == 1
    assert facts[0]["payload"]["event"]["task_boundary_verdict"] == verdict


@pytest.mark.asyncio
async def test_real_terminal_taskruntime_authority_rejects_before_command_and_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.runtime.task_runtime.public import (
        SettleTaskRuntimeExecutionAttemptCommandV1,
        settle_task_runtime_execution_attempt_typed,
    )

    producer = scenario_module()
    original = producer.claim_synthetic_attempt

    def settle_before_prepare(workspace: str) -> Any:
        attempt = original(workspace)
        result = settle_task_runtime_execution_attempt_typed(
            SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=workspace, identity=attempt, outcome="failed", summary="Synthetic real terminal interleaving"
            )
        )
        assert result.success
        return attempt

    monkeypatch.setattr(producer, "claim_synthetic_attempt", settle_before_prepare)
    with pytest.raises(AssertionError) as rejected:
        await producer.produce_failed_cli_owner_case(tmp_path)
    assert "denied" in str(rejected.value)
    assert not query_payload_fact_ledger_presence(
        QuerySegmentedFactLedgerHeadV1(workspace=str(tmp_path), logical_stream="roles.kernel.turn_outcomes.v3")
    )
    assert not psutil.Process().children(recursive=True)


@pytest.mark.asyncio
async def test_file_prewrite_scenario_uses_real_owner_commit_and_public_negative_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.kernel.public.turn_contracts import CommitReceipt, TurnOutcome

    producer = scenario_module()
    original = outcome_commit.commit_turn_result
    calls: list[tuple[str, str, str]] = []

    def observe(**kwargs: Any) -> Any:
        calls.append((kwargs["run_id"], kwargs["task_id"], kwargs["completion_status"]))
        return original(**kwargs)

    monkeypatch.setattr(outcome_commit, "commit_turn_result", observe)
    case = await producer.produce_file_prewrite_owner_case(tmp_path, aliases=True)
    assert not {"ledger", "gateway", "prepared_batch", "runtime", "durable"} & set(case)
    assert isinstance(case["outcome"], TurnOutcome)
    assert isinstance(case["commit_receipt"], CommitReceipt)
    attempt = case["attempt"]
    assert calls == [(attempt.run_id, attempt.external_task_id, "failed")]
    batch = case["batch"]
    row = batch["raw_results"][0]
    assert row["result"]["failure_kind"] == "file_prewrite_rejected"
    assert row["effect_receipt"] is row["effect_receipt_commit"] is None
    assert case["settled"].success
    assert (tmp_path / "src/a.txt").read_text(encoding="utf-8") == "alpha\nbeta\n"
    facts = query_payload_fact_events(
        QueryPayloadFactEventsV1(workspace=str(tmp_path), logical_stream="roles.kernel.turn_outcomes.v3", limit=100)
    ).events
    assert [event["event_type"] for event in facts] == ["turn_outcome_prepared", "turn_outcome_committed"]
    committed = facts[-1]
    assert committed["event_id"] == case["commit_receipt"].fact_event_id
    assert committed["expanded_payload"]["outcome_hash"] == case["commit_receipt"].outcome_hash
    closure = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempt))
    assert closure.status == "closed" and closure.receipts == ()
    assert len(closure.terminal_failures) == 1 and closure.terminal_failures[0].failure_kind == "file_prewrite_rejected"


@pytest.mark.asyncio
@pytest.mark.parametrize("legal", [False, True])
async def test_file_policy_guard_boundary_retains_real_preview_rejection_and_legal_edit(
    tmp_path: Path,
    legal: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer = scenario_module()
    original = producer.create_director_directed_effect_mutation_port
    calls = []

    def observe(**kwargs: Any) -> Any:
        calls.append(kwargs["workspace"])
        return original(**kwargs)

    monkeypatch.setattr(producer, "create_director_directed_effect_mutation_port", observe)
    case = await producer.produce_file_policy_guard_case(tmp_path, legal=legal)
    assert len(calls) == int(legal)
    if legal:
        assert case["status"] == "authorized"
        assert case["outcome"].outcome_status.value == "completed"
        row = case["batch"]["raw_results"][0]
        assert row["status"] == "success", row
        assert row["effect_receipt_commit"]["code"] == "receipt_committed"
        assert (tmp_path / "src/a.txt").read_text(encoding="utf-8") == "alpha\nBETA\n"
    else:
        assert case["status"] == "denied" and case["error_code"] == "deo_tool_normalization_failed"
        assert case["before"] == case["after"]
        assert (tmp_path / "src/a.txt").read_text(encoding="utf-8") == "alpha\nbeta\n"
        assert not query_payload_fact_ledger_presence(
            QuerySegmentedFactLedgerHeadV1(workspace=str(tmp_path), logical_stream="roles.kernel.turn_outcomes.v3")
        )


def test_file_prewrite_consumers_use_named_owner_scenarios_without_private_foreign_imports() -> None:
    cells = Path(__file__).resolve().parents[3]
    consumers = (
        cells / "runtime/task_runtime/public/tests/test_file_prewrite_negative_closure.py",
        cells / "control_plane/run_ledger/tests/test_file_prewrite_negative_settlement.py",
    )
    for path in consumers:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert ".internal." not in str(node.module), (path, node.module)
                assert not any(alias.name.startswith("_") for alias in node.names), (path, node.module)
        assert not any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_commit_owner"
            for node in tree.body
        )


@pytest.mark.asyncio
async def test_corrupt_file_owner_scenario_is_explicit_negative_without_real_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.events.fact_stream.public import QueryFactEventsV1, query_fact_events

    def forbidden_commit(**_kwargs: Any) -> Any:
        raise AssertionError("synthetic corrupt fact must never be presented as a real owner commit")

    def mismatch(outcome: dict[str, Any]) -> None:
        outcome["decision"]["metadata"]["native_tool_call_envelopes"][0]["arguments_hash"] = "f" * 64

    monkeypatch.setattr(outcome_commit, "commit_turn_result", forbidden_commit)
    case = await scenario_module().produce_file_prewrite_owner_case(tmp_path, aliases=True, mutate=mismatch)
    assert case["synthetic_owner_corruption"] is True
    assert not {"commit_receipt", "sealed_turn", "outcome"} & set(case)
    facts = query_fact_events(
        QueryFactEventsV1(
            workspace=str(tmp_path),
            stream="roles.kernel.turn_outcomes",
            strict_integrity=True,
            limit=100,
        )
    ).events
    assert len(facts) == 1
    outcome = facts[0]["payload"]["outcome"]
    assert outcome["decision"]["metadata"]["native_tool_call_envelopes"][0]["arguments_hash"] == "f" * 64
    assert outcome["execution"]["invocations"][0]["arguments"]["start"] == 7
    assert (
        outcome["execution"]["receipt"]["raw_results"][0]["result"]["prewrite_rejection"]["arguments_hash"] != "f" * 64
    )
    assert (
        query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), case["attempt"])).status
        == "unknown"
    )


@pytest.mark.asyncio
async def test_corrupt_file_owner_scenario_refuses_noop_forgery(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="requires an actual owner-input corruption"):
        await scenario_module().produce_file_prewrite_owner_case(tmp_path, mutate=lambda _outcome: None)
