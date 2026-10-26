"""Real resource owners may close while product evidence remains failed/missing."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    AppendToolCallLifecycleEventCommandV1,
    append_run_ledger_event,
    append_tool_call_lifecycle_event,
    build_tool_call_lifecycle_receipt,
    query_factory_settlement_barrier,
)
from polaris.cells.control_plane.run_ledger.public.committed_turn_receipts import query_committed_turn_receipts
from polaris.cells.control_plane.run_ledger.public.ledger import stable_hash
from polaris.cells.roles.kernel.tests.run_ledger_owner_scenarios import (
    produce_failed_cli_owner_case,
    produce_readonly_owner_case,
    produce_readonly_source_owner_case,
)
from polaris.cells.runtime.task_runtime.public import (
    BindRuntimeTaskToFactoryRunCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    bind_runtime_task_to_factory_run,
    settle_task_runtime_execution_attempt_typed,
)
from polaris.cells.runtime.task_runtime.public.execution_effect_closure import (
    QueryExecutionEffectClosureV1,
    query_execution_effect_closure,
)
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter

_FACTORY = "factory-failed-product"


def _append_gate(root: Path, attempt: Any, batch: dict[str, Any], *, include_batch: bool = True) -> dict[str, Any]:
    lifecycle = build_tool_call_lifecycle_receipt(
        run_id=attempt.run_id,
        task_id=attempt.external_task_id,
        turn_id=batch["turn_id"],
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[batch],
    ).to_dict()
    if batch["turn_id"] == "read-turn":
        lifecycle["native_tool_call_envelope_refs"] = [
            {
                "schema_version": "native_tool_call_envelope.v1",
                "index": 0,
                "call_id": "read-call",
                "tool_name": "read_file",
                "arguments_hash": stable_hash({"file_path": "source.txt"}),
            }
        ]
    append_tool_call_lifecycle_event(
        AppendToolCallLifecycleEventCommandV1(
            workspace=str(root),
            run_id=attempt.run_id,
            task_id=attempt.external_task_id,
            turn_id=batch["turn_id"],
            role="director",
            project_id="private-project",
            lifecycle_receipt=lifecycle,
        )
    )
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(root),
            run_id=attempt.run_id,
            event={
                "event_type": "gate_evaluated",
                "run_id": attempt.run_id,
                "stage": "qa_verifier",
                "gate": {"name": "semantic_qa", "ok": False, "summary": "product review failed"},
                "job_token": {
                    "run_id": attempt.run_id,
                    "project_id": "private-project",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {
                        "required_evidence_modalities": ["qa", "command"],
                        "enabled_evidence_modalities": ["qa", "command"],
                    },
                },
                "physical_evidence": {"batch_receipt": batch if include_batch else {}},
            },
        )
    )
    return lifecycle


async def _readonly_failed_product(root: Path, *, include_batch: bool = True, task_outcome: str = "failed"):
    case = await produce_readonly_source_owner_case(root, factory_run_id=_FACTORY)
    attempt, batch = case["attempt"], case["batch"]
    assert batch["results"][0]["status"] == "success"
    assert batch["results"][0]["result"]["content"] == "actual readonly source"
    assert settle_task_runtime_execution_attempt_typed(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=str(root), identity=attempt, outcome=task_outcome, summary="independent product review failed"
        )
    ).success
    observed = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(root), attempt))
    assert observed.status == "closed", observed
    assert observed.receipts == observed.terminal_failures == ()
    lifecycle = _append_gate(root, attempt, batch, include_batch=include_batch)
    owner = query_committed_turn_receipts(root, lifecycle_receipts=[lifecycle])
    assert owner[0].status == "observed", owner[0].reason
    return attempt, batch, lifecycle


@pytest.mark.asyncio
@pytest.mark.parametrize("include_batch", [True, False])
@pytest.mark.parametrize("task_outcome", ["failed", "completed"])
async def test_real_successful_readonly_resources_release_while_product_gate_fails(
    tmp_path: Path, include_batch: bool, task_outcome: str
) -> None:
    await _readonly_failed_product(tmp_path, include_batch=include_batch, task_outcome=task_outcome)
    result = query_factory_settlement_barrier(tmp_path, _FACTORY)
    assert result.release_allowed is True, result
    assert result.closed is True
    assert result.passed is False
    assert "qa" in result.missing_required_modalities
    assert "run_ledger_gate_failed" in result.blocking_reasons


@pytest.mark.asyncio
async def test_real_failed_cli_resources_close_without_product_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = await produce_failed_cli_owner_case(tmp_path)
    attempt, batch = case["attempt"], case["batch"]
    assert batch["raw_results"][0]["result"]["exit_code"] == 2
    assert bind_runtime_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=str(tmp_path), task_id=str(attempt.task_id), factory_run_id=_FACTORY
        )
    ).ok
    _append_gate(tmp_path, attempt, batch)
    result = query_factory_settlement_barrier(tmp_path, _FACTORY)
    assert result.release_allowed is True, result
    assert result.passed is False


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["unknown", "missing", "foreign_run", "foreign_workspace", "duplicate"])
@pytest.mark.parametrize("no_missing", [False, True])
async def test_readonly_transport_cannot_hide_unproven_resource_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str, no_missing: bool
) -> None:
    from polaris.cells.control_plane.run_ledger.public import service

    await _readonly_failed_product(tmp_path)
    observe = service._terminal_execution_effect_observations

    def damaged(workspace, facts):
        original = observe(workspace, facts)
        assert len(original) == 1 and original[0].status == "closed"
        item = original[0]
        if defect == "missing":
            return ()
        if defect == "duplicate":
            return (item, item)
        if defect == "unknown":
            return (replace(item, status="unknown", reason="test_missing_owner_proof"),)
        key, value = ("run_id", "foreign-run") if defect == "foreign_run" else ("workspace", str(tmp_path / "foreign"))
        return (replace(item, execution_attempt=replace(item.execution_attempt, **{key: value})),)

    monkeypatch.setattr(service, "_terminal_execution_effect_observations", damaged)
    if no_missing:
        build = service.build_run_ledger_projection

        def without_missing(*args, **kwargs):
            projection = build(*args, **kwargs)
            projection["evidence_policy"]["missing_required_modalities"] = []
            return projection

        monkeypatch.setattr(service, "build_run_ledger_projection", without_missing)
    result = service.query_factory_settlement_barrier(tmp_path, _FACTORY)
    assert result.release_allowed is False, result
    assert result.passed is False


@pytest.mark.asyncio
async def test_one_query_reuses_strict_owner_history_for_multiple_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.control_plane.run_ledger.public import committed_turn_receipts as owner

    _, _, lifecycle = await _readonly_failed_product(tmp_path)
    legacy_read = owner.query_fact_events
    payload_read = owner.query_payload_fact_events
    calls = {"legacy": 0, "payload": 0}

    def read_legacy(query):
        calls["legacy"] += 1
        return legacy_read(query)

    def read_payload(query):
        calls["payload"] += 1
        return payload_read(query)

    monkeypatch.setattr(owner, "query_fact_events", read_legacy)
    monkeypatch.setattr(owner, "query_payload_fact_events", read_payload)
    observations = owner.query_committed_turn_receipts(tmp_path, lifecycle_receipts=[lifecycle] * 5)
    assert len(observations) == 5 and all(item.status == "observed" for item in observations)
    assert calls["legacy"] <= 2, calls
    assert calls["payload"] <= 2, calls


@pytest.mark.asyncio
async def test_shared_snapshot_rejects_payload_corruption_after_first_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.control_plane.run_ledger.public import committed_turn_receipts as owner
    from polaris.cells.events.fact_stream.public import FactPayloadReferenceV1

    _, _, lifecycle = await _readonly_failed_product(tmp_path)
    read = owner.query_payload_fact_events
    changed = False

    def corrupt_after_read(query):
        nonlocal changed
        page = read(query)
        if not changed:
            changed = True
            ref = FactPayloadReferenceV1.from_record(page.events[0]["storage_record"]["payload"]["payload_ref"])
            KernelFileSystem(str(tmp_path), get_default_adapter()).write_text(ref.logical_path, "{}", encoding="utf-8")
        return page

    monkeypatch.setattr(owner, "query_payload_fact_events", corrupt_after_read)
    observations = owner.query_committed_turn_receipts(tmp_path, lifecycle_receipts=[lifecycle] * 2)
    assert changed and all(item.status == "unknown" for item in observations)
    assert all(item.batch_receipt == {} for item in observations)


def test_shared_snapshot_rejects_legacy_head_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.cells.control_plane.run_ledger.public import committed_turn_receipts as owner
    from polaris.cells.control_plane.run_ledger.tests.test_committed_turn_receipts import _append, _fixture

    payload, lifecycle = _fixture(tmp_path)
    _append(tmp_path, payload)
    read = owner.query_fact_events
    changed = False

    def append_after_read(query):
        nonlocal changed
        page = read(query)
        if not changed:
            changed = True
            _append(tmp_path, {**payload, "transition_id": "new-owner-transition"})
        return page

    monkeypatch.setattr(owner, "query_fact_events", append_after_read)
    observations = owner.query_committed_turn_receipts(tmp_path, lifecycle_receipts=[lifecycle] * 2)
    assert changed and all(item.status == "unknown" for item in observations)
    assert all(item.reason == "owner_legacy_snapshot_changed" for item in observations)


async def _actual_layout_resource_control(root: Path, *, product_ok: bool):
    """Real private read/owner evidence; the product gate is an explicit test control."""
    data = await produce_readonly_owner_case(root, reordered=True, mixed_serializers=True)
    attempt = data["attempt"]
    factory = "factory-layout-resource-control"
    assert bind_runtime_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=str(root),
            task_id=str(attempt.task_id),
            factory_run_id=factory,
        )
    ).ok
    assert settle_task_runtime_execution_attempt_typed(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=str(root),
            identity=attempt,
            outcome="completed" if product_ok else "failed",
            summary="private controlled product outcome",
        )
    ).success
    owner = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(root), attempt))
    assert owner.status == "closed" and owner.receipts == owner.terminal_failures == ()
    append_tool_call_lifecycle_event(
        AppendToolCallLifecycleEventCommandV1(
            workspace=str(root),
            run_id=attempt.run_id,
            task_id=attempt.external_task_id,
            turn_id=data["lifecycle"]["turn_id"],
            role="director",
            project_id="private-layout-project",
            lifecycle_receipt=data["lifecycle"],
        )
    )
    required = [] if product_ok else ["qa"]
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(root),
            run_id=attempt.run_id,
            event={
                "event_type": "gate_evaluated",
                "run_id": attempt.run_id,
                "stage": "qa_verifier",
                "gate": {
                    "name": "private_product_control",
                    "ok": product_ok,
                    "summary": "private controlled product gate",
                },
                "job_token": {
                    "run_id": attempt.run_id,
                    "project_id": "private-layout-project",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {"required_evidence_modalities": required, "enabled_evidence_modalities": required},
                },
                # No transport mirror: resource proof must come from immutable owner.
                "physical_evidence": {"batch_receipt": {}},
            },
        )
    )
    observed = query_committed_turn_receipts(root, lifecycle_receipts=[data["lifecycle"]])
    assert len(observed) == 1 and observed[0].status == "observed", observed
    return factory, data


@pytest.mark.asyncio
@pytest.mark.parametrize("product_ok", [False, True])
async def test_real_parallel_layout_resources_and_controlled_product_outcome(tmp_path: Path, product_ok: bool) -> None:
    import json
    from dataclasses import asdict

    factory, data = await _actual_layout_resource_control(tmp_path, product_ok=product_ok)
    result = query_factory_settlement_barrier(tmp_path, factory)
    # Engineering observation only: this file is not a platform fact source.
    (tmp_path / "resource-count-observation.json").write_text(
        json.dumps(
            {
                "controlled_product_ok": product_ok,
                "acceptance_policy": [] if product_ok else ["qa"],
                "actual_read_calls": len(data["calls"]),
                "owner_ref_hashes": data["lifecycle"]["batch_receipt_refs"],
                "barrier": asdict(result),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    assert result.release_allowed is True and result.closed is True, result
    assert result.passed is product_ok, result
    assert result.effect_receipt_count == 0
    if not product_ok:
        assert "qa" in result.missing_required_modalities


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["committed_read", "task_owner"])
async def test_same_parallel_child_refs_cannot_replace_missing_parent_or_task_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    from polaris.cells.control_plane.run_ledger.public import service

    factory, _ = await _actual_layout_resource_control(tmp_path, product_ok=True)
    before = service.query_factory_settlement_barrier(tmp_path, factory)
    assert before.release_allowed is True
    if missing == "committed_read":
        monkeypatch.setattr(service, "query_committed_turn_receipts", lambda *args, **kwargs: ())
    else:
        monkeypatch.setattr(service, "_terminal_execution_effect_observations", lambda *args, **kwargs: ())
    result = service.query_factory_settlement_barrier(tmp_path, factory)
    assert result.release_allowed is False and result.closed is False and result.passed is False, result
