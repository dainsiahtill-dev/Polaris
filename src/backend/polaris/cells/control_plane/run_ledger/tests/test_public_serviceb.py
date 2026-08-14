from __future__ import annotations

import fcntl
import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from typing import Any

import polaris.cells.control_plane.run_ledger.public.ledger as run_ledger_module
import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    AppendToolCallLifecycleEventCommandV1,
    ReadRunLedgerProjectionBarrierQueryV1,
    ReadRunLedgerProjectionQueryV1,
    ReadRunProvenanceBundleQueryV1,
    RunLedger,
    append_run_ledger_event,
    append_tool_call_lifecycle_event,
    build_run_ledger_projection,
    build_tool_call_lifecycle_receipt,
    read_run_ledger_projection,
    read_run_ledger_projection_barrier,
    read_run_provenance_bundle,
    service as run_ledger_service,
    summarize_run_ledger_projection,
)
from polaris.cells.control_plane.run_ledger.public.projection import _directed_effect_receipt_payload_hash
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    QueryFactEventsV1,
    append_fact_event,
    query_fact_events,
)
from polaris.cells.events.fact_stream.public.contracts import (
    BootstrapFactStreamWorkspaceCommandV1,
    FactStreamError,
)
from polaris.cells.events.fact_stream.public.workspace_bootstrap import (
    bootstrap_fact_stream_workspace,
)
from polaris.kernelone.events.sourcing.models import EventEnvelope
from polaris.kernelone.storage import resolve_logical_path


@pytest.fixture(autouse=True)
def _bootstrap_run_ledger_fact_streams(tmp_path: Path) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            streams=("execution.control_plane", "task_runtime.execution"),
            maintenance_reason="run_ledger_public_service_tests",
        )
    )


def _control_plane_facts(workspace: Path, *, run_id: str) -> list[dict[str, Any]]:
    return list(
        query_fact_events(
            QueryFactEventsV1(
                workspace=str(workspace),
                stream="execution.control_plane",
                run_id=run_id,
                strict_integrity=True,
            )
        ).events
    )


def _append_control_plane_fact(
    workspace: Path,
    *,
    run_id: str,
    event: dict[str, Any],
) -> Any:
    ledger = RunLedger(workspace, run_id=run_id)
    canonical_event = ledger.prepare_idempotent_event(event)
    event_type = str(canonical_event.get("event_type") or "control_plane_event").strip()
    return append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="execution.control_plane",
            event_type=event_type,
            payload={
                "schema_version": "execution.control_plane.fact.v1",
                "run_id": run_id,
                "event": canonical_event,
            },
            source="control_plane.run_ledger",
            run_id=run_id,
            task_id=str(canonical_event.get("task_id") or "").strip() or None,
            correlation_id=str(canonical_event.get("turn_id") or canonical_event.get("event_id") or "").strip() or None,
            idempotency_key=ledger.fact_idempotency_key(canonical_event),
            durability="fsync",
            strict_integrity=True,
        )
    )


def _append_raw_control_plane_fact(
    workspace: Path,
    *,
    run_id: str,
    payload: dict[str, Any],
    source: str = "unrelated.control_plane",
    idempotency_key: str = "unrelated:fact",
) -> Any:
    return append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="execution.control_plane",
            event_type="unrelated.control_plane.event",
            payload=payload,
            source=source,
            run_id=run_id,
            idempotency_key=idempotency_key,
            durability="fsync",
            strict_integrity=True,
        )
    )


def _canonical_projection_row(
    ledger: RunLedger,
    event: dict[str, Any],
    *,
    recorded_at: str = "2026-07-19T00:00:00+00:00",
) -> tuple[str, dict[str, Any]]:
    payload = ledger.prepare_idempotent_event(event)
    payload["recorded_at"] = recorded_at
    row = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )
    return row, payload


def _assert_projection_flock_available(ledger: RunLedger) -> None:
    with ledger.path.open("a+b") as probe:
        fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(probe.fileno(), fcntl.LOCK_UN)




def test_projection_exposes_failed_tool_lifecycle_without_dropped_dispatch() -> None:
    lifecycle = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-1",
                "results": [
                    {
                        "call_id": "call-1",
                        "tool_name": "write_file",
                        "status": "success",
                    }
                ],
                "success_count": 1,
                "failure_count": 0,
            }
        ],
    ).to_dict()
    lifecycle["failure_class"] = "missing-effect-receipt"
    projection = build_run_ledger_projection(
        [
            {
                "event_type": "gate_evaluated",
                "stage": "director",
                "gate": {"name": "director", "ok": True, "summary": "started"},
                "job_token": {
                    "token_id": "token-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {},
            },
            {
                "event_type": "tool_call_lifecycle",
                "tool_call_lifecycle_receipt": lifecycle,
            },
        ]
    )
    summary = summarize_run_ledger_projection(projection)

    assert projection["ok"] is False
    assert projection["integrity_ok"] is False
    assert projection["tool_lifecycle"]["ok"] is False
    assert projection["tool_lifecycle"]["failed_count"] == 1
    assert projection["tool_lifecycle"]["dropped_count"] == 0
    assert projection["tool_lifecycle"]["events"][0]["failed"] is True
    assert projection["tool_lifecycle"]["events"][0]["failure_class"] == "MISSING_EFFECT_RECEIPT"
    assert summary["detail"] == "run ledger projection tool lifecycle failed: MISSING_EFFECT_RECEIPT"
    assert summary["missing"] == []
    assert summary["failed_control_plane_events"] == ["MISSING_EFFECT_RECEIPT"]


def test_projection_exposes_task_boundary_failure() -> None:
    projection = build_run_ledger_projection(
        [
            {
                "event_type": "gate_evaluated",
                "stage": "director",
                "gate": {"name": "director", "ok": True, "summary": "started"},
                "job_token": {
                    "token_id": "token-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {},
            },
            {
                "event_type": "task_boundary_verdict",
                "task_boundary_verdict": {
                    "schema_version": "polaris.task_boundary_verdict.v1",
                    "task_id": "TASK-1",
                    "status": "missing_entrypoint_target",
                    "ok": False,
                    "failure_class": "MISSING_ENTRYPOINT_TARGET",
                    "responsible_layer": "task_boundary",
                    "reason": "package.json references src/index.js",
                    "missing_entrypoint_targets": ["src/index.js"],
                },
            },
            _successful_tool_lifecycle_event(),
        ]
    )
    summary = summarize_run_ledger_projection(projection)

    assert projection["ok"] is False
    assert projection["outcome_ok"] is False
    assert projection["task_boundary"]["ok"] is False
    assert projection["task_boundary"]["latest"]["failure_class"] == "MISSING_ENTRYPOINT_TARGET"
    assert summary["detail"] == "run ledger projection task boundary failed: MISSING_ENTRYPOINT_TARGET"


def test_projection_preserves_completed_boundary_after_zero_effect_mutation_bypass() -> None:
    projection = build_run_ledger_projection(
        [
            {
                "event_type": "gate_evaluated",
                "stage": "director",
                "gate": {"name": "director", "ok": True, "summary": "started"},
                "job_token": {
                    "token_id": "token-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {},
            },
            {
                "event_type": "task_boundary_verdict",
                "run_id": "director-1",
                "task_id": "TASK-1",
                "append_id": "append-complete",
                "content_id": "content-complete",
                "task_boundary_verdict": {
                    "task_id": "TASK-1",
                    "run_id": "director-1",
                    "status": "completed_verified",
                    "ok": True,
                    "failure_class": "PASSED",
                    "reason": "all delivery obligations passed",
                    "evidence_refs": ["receipt-complete"],
                },
            },
            {
                "event_type": "task_boundary_verdict",
                "run_id": "director-1",
                "task_id": "TASK-1",
                "append_id": "append-deferred",
                "content_id": "content-deferred",
                "task_boundary_verdict": {
                    "task_id": "TASK-1",
                    "run_id": "director-1",
                    "status": "deferred_followup_required",
                    "ok": False,
                    "failure_class": "DEFERRED_FOLLOWUP_REQUIRED",
                    "reason": "mutation_bypass_blocked",
                    "evidence_refs": ["receipt-read-only"],
                    "target_files": [],
                    "completed_artifacts": [],
                    "tool_dispatch": {},
                },
            },
            _successful_tool_lifecycle_event(),
        ]
    )

    boundary = projection["task_boundary"]
    assert boundary["ok"] is True
    assert boundary["verdict_count"] == 2
    assert boundary["historical_failed_count"] == 1
    assert boundary["suppressed_non_mutating_deferred_count"] == 1
    assert boundary["latest"]["status"] == "completed_verified"
    assert boundary["latest_by_task"]["TASK-1"]["status"] == "completed_verified"


def test_projection_does_not_suppress_mutation_bypass_without_completed_boundary() -> None:
    projection = build_run_ledger_projection(
        [
            {
                "event_type": "task_boundary_verdict",
                "run_id": "director-1",
                "task_id": "TASK-1",
                "append_id": "append-deferred",
                "content_id": "content-deferred",
                "task_boundary_verdict": {
                    "task_id": "TASK-1",
                    "run_id": "director-1",
                    "status": "deferred_followup_required",
                    "ok": False,
                    "failure_class": "DEFERRED_FOLLOWUP_REQUIRED",
                    "reason": "mutation_bypass_blocked",
                    "evidence_refs": ["receipt-read-only"],
                    "target_files": [],
                    "completed_artifacts": [],
                    "tool_dispatch": {},
                },
            }
        ]
    )

    boundary = projection["task_boundary"]
    assert boundary["ok"] is False
    assert boundary["suppressed_non_mutating_deferred_count"] == 0
    assert boundary["latest"]["status"] == "deferred_followup_required"


def test_projection_real_boundary_failure_invalidates_completed_boundary() -> None:
    projection = build_run_ledger_projection(
        [
            {
                "event_type": "task_boundary_verdict",
                "run_id": "director-1",
                "task_id": "TASK-1",
                "append_id": "append-complete",
                "content_id": "content-complete",
                "task_boundary_verdict": {
                    "task_id": "TASK-1",
                    "run_id": "director-1",
                    "status": "completed_verified",
                    "ok": True,
                    "failure_class": "PASSED",
                    "reason": "all delivery obligations passed",
                    "evidence_refs": ["receipt-complete"],
                },
            },
            {
                "event_type": "task_boundary_verdict",
                "run_id": "director-1",
                "task_id": "TASK-1",
                "append_id": "append-failed",
                "content_id": "content-failed",
                "task_boundary_verdict": {
                    "task_id": "TASK-1",
                    "run_id": "director-1",
                    "status": "missing_entrypoint_target",
                    "ok": False,
                    "failure_class": "MISSING_ENTRYPOINT_TARGET",
                    "reason": "entrypoint disappeared",
                    "missing_entrypoint_targets": ["src/index.ts"],
                },
            },
        ]
    )

    boundary = projection["task_boundary"]
    assert boundary["ok"] is False
    assert boundary["suppressed_non_mutating_deferred_count"] == 0
    assert boundary["latest"]["failure_class"] == "MISSING_ENTRYPOINT_TARGET"


def test_public_projection_summary_normalizes_task_boundary_failure_alias() -> None:
    summary = summarize_run_ledger_projection(
        {
            "source": "run_ledger",
            "ok": False,
            "gate_count": 1,
            "capability": {"ok": True},
            "task_boundary": {
                "ok": False,
                "latest": {
                    "ok": False,
                    "failure_class": "missing-entrypoint-target",
                },
            },
        }
    )

    assert summary["detail"] == "run ledger projection task boundary failed: MISSING_ENTRYPOINT_TARGET"
    assert summary["failed_control_plane_events"] == ["MISSING_ENTRYPOINT_TARGET"]


def test_public_projection_carries_task_boundary_and_tool_lifecycle(tmp_path: Path) -> None:
    lifecycle = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=0,
        dispatched_tool_calls_count=0,
        receipts=[],
        dispatch_status="dropped",
        failure_class="TOOL_DISPATCH_DROPPED",
    ).to_dict()
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "gate": {"name": "director", "ok": True, "summary": "started"},
                "job_token": {
                    "token_id": "token-1",
                    "run_id": "run-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {},
            },
        )
    )
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "tool_call_lifecycle",
                "run_id": "run-1",
                "task_id": "TASK-1",
                "job_token": {"project_id": "P1", "capability_audit": {"ok": True, "issues": []}},
                "tool_call_lifecycle_receipt": lifecycle,
            },
        )
    )
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "task_boundary_verdict",
                "run_id": "run-1",
                "task_id": "TASK-1",
                "job_token": {"project_id": "P1", "capability_audit": {"ok": True, "issues": []}},
                "task_boundary_verdict": {
                    "schema_version": "polaris.task_boundary_verdict.v1",
                    "task_id": "TASK-1",
                    "status": "deferred_followup_required",
                    "ok": False,
                    "failure_class": "DEFERRED_FOLLOWUP_REQUIRED",
                    "responsible_layer": "execution_control_plane",
                    "reason": "needs follow-up",
                },
            },
        )
    )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-1")
    ).projection

    assert projection["tool_lifecycle"]["dropped_count"] == 1
    assert projection["task_boundary"]["latest"]["failure_class"] == "DEFERRED_FOLLOWUP_REQUIRED"
    assert projection["task_boundary"]["historical_failed_count"] == 1
    assert projection["task_boundary"]["latest_by_task"]["TASK-1"]["failure_class"] == ("DEFERRED_FOLLOWUP_REQUIRED")
    assert projection["projects"][0]["tool_lifecycle"]["dropped_count"] == 1
    assert projection["projects"][0]["task_boundary"]["latest"]["failure_class"] == "DEFERRED_FOLLOWUP_REQUIRED"


def test_read_run_ledger_projection_aggregates_factory_children_without_workspace_leakage(
    tmp_path: Path,
) -> None:
    factory_run_id = "factory-r26"
    project_id = "L1-01"
    parent_run_id = "ce65-parent-gate"
    child_run_ids = ("director-1", "director-2", "director-3", "director-4")

    for index, child_run_id in enumerate(child_run_ids, start=1):
        _append_task_runtime_execution_fact(
            tmp_path,
            run_id=child_run_id,
            factory_run_id=factory_run_id,
            project_id=project_id,
            task_id=f"TASK-{index}",
        )
    _append_task_runtime_execution_fact(
        tmp_path,
        run_id=child_run_ids[0],
        factory_run_id=factory_run_id,
        project_id=project_id,
        task_id="TASK-1-RETRY",
    )
    _append_task_runtime_execution_fact(
        tmp_path,
        run_id="other-factory-director",
        factory_run_id="factory-r27",
        project_id=project_id,
        task_id="TASK-OTHER-FACTORY",
    )
    _append_task_runtime_execution_fact(
        tmp_path,
        run_id="other-project-director",
        factory_run_id=factory_run_id,
        project_id="L1-02",
        task_id="TASK-OTHER-PROJECT",
    )

    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id=parent_run_id,
            event={
                "event_type": "gate_evaluated",
                "stage": "real_run",
                "gate": {"name": "real_run_gate", "ok": True, "summary": "parent gate passed"},
                "job_token": {
                    "token_id": "parent-token",
                    "run_id": parent_run_id,
                    "project_id": project_id,
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {},
            },
        )
    )
    lifecycle = build_tool_call_lifecycle_receipt(
        run_id=child_run_ids[0],
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=0,
        dispatched_tool_calls_count=0,
        receipts=[],
        dispatch_status="dropped",
        failure_class="TOOL_DISPATCH_DROPPED",
    ).to_dict()
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id=child_run_ids[0],
            event={
                "event_type": "tool_call_lifecycle",
                "run_id": child_run_ids[0],
                "task_id": "TASK-1",
                "job_token": {"project_id": project_id, "capability_audit": {"ok": True, "issues": []}},
                "tool_call_lifecycle_receipt": lifecycle,
            },
        )
    )
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id=child_run_ids[1],
            event={
                "event_type": "task_boundary_verdict",
                "run_id": child_run_ids[1],
                "task_id": "TASK-2",
                "job_token": {"project_id": project_id, "capability_audit": {"ok": True, "issues": []}},
                "task_boundary_verdict": {
                    "schema_version": "polaris.task_boundary_verdict.v1",
                    "task_id": "TASK-2",
                    "status": "deferred_followup_required",
                    "ok": False,
                    "failure_class": "DEFERRED_FOLLOWUP_REQUIRED",
                    "responsible_layer": "execution_control_plane",
                    "reason": "needs follow-up",
                },
            },
        )
    )
    for child_run_id in child_run_ids[2:]:
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id=child_run_id,
                event={
                    "event_type": "gate_evaluated",
                    "stage": "director",
                    "gate": {"name": child_run_id, "ok": True, "summary": "director completed"},
                    "job_token": {
                        "token_id": f"{child_run_id}-token",
                        "run_id": child_run_id,
                        "project_id": project_id,
                        "capability_audit": {"ok": True, "issues": []},
                        "gate_policy": {},
                    },
                    "physical_evidence": {},
                },
            )
        )
    for unrelated_run_id in ("other-factory-director", "other-project-director"):
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id=unrelated_run_id,
                event={
                    "event_type": "gate_evaluated",
                    "stage": "director",
                    "gate": {"name": f"{unrelated_run_id}-gate", "ok": True, "summary": "must exclude"},
                    "job_token": {
                        "token_id": f"{unrelated_run_id}-token",
                        "run_id": unrelated_run_id,
                        "project_id": project_id,
                        "capability_audit": {"ok": True, "issues": []},
                        "gate_policy": {},
                    },
                    "physical_evidence": {},
                },
            )
        )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            run_id=parent_run_id,
            factory_run_id=factory_run_id,
            project_id=project_id,
        )
    ).projection

    assert projection["query_scope"] == {
        "run_id": parent_run_id,
        "factory_run_id": factory_run_id,
        "project_id": project_id,
    }
    assert projection["consumed_run_ids"] == [parent_run_id, *child_run_ids]
    assert len(projection["projects"]) == 1
    assert projection["projects"][0]["project_id"] == project_id
    assert projection["run_projection"]["gate_count"] == 3
    assert [gate["name"] for gate in projection["run_projection"]["gates"]] == [
        "real_run_gate",
        "director-3",
        "director-4",
    ]
    assert projection["tool_lifecycle"]["dropped_count"] == 1
    assert projection["task_boundary"]["latest"]["failure_class"] == "DEFERRED_FOLLOWUP_REQUIRED"
    rendered = json.dumps(projection, sort_keys=True)
    assert "other-factory-director-gate" not in rendered
    assert "other-project-director-gate" not in rendered


def test_read_run_ledger_projection_scoped_legacy_fallback_uses_selected_run_ids(tmp_path: Path) -> None:
    _append_task_runtime_execution_fact(
        tmp_path,
        run_id="director-legacy",
        factory_run_id="factory-r26",
        project_id="L1-01",
        task_id="TASK-LEGACY",
    )
    _append_task_runtime_execution_fact(
        tmp_path,
        run_id="director-legacy-unrelated",
        factory_run_id="factory-r27",
        project_id="L1-01",
        task_id="TASK-UNRELATED",
    )
    for run_id, gate_name in (
        ("director-legacy", "legacy-director-gate"),
        ("director-legacy-unrelated", "unrelated-legacy-gate"),
    ):
        RunLedger(tmp_path, run_id=run_id).append_event(
            {
                "event_type": "gate_evaluated",
                "gate": {"name": gate_name, "ok": True, "summary": "legacy event"},
                "job_token": {
                    "token_id": f"{run_id}-token",
                    "run_id": run_id,
                    "project_id": "L1-01",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {},
            }
        )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            factory_run_id="factory-r26",
            project_id="L1-01",
        )
    ).projection

    assert projection["consumed_run_ids"] == ["director-legacy"]
    assert [gate["name"] for gate in projection["run_projection"]["gates"]] == ["legacy-director-gate"]


def test_read_run_ledger_projection_scope_miss_does_not_widen_to_workspace(tmp_path: Path) -> None:
    _append_task_runtime_execution_fact(
        tmp_path,
        run_id="other-factory-director",
        factory_run_id="factory-r27",
        project_id="L1-01",
        task_id="TASK-OTHER",
    )
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="other-factory-director",
            event={
                "event_type": "gate_evaluated",
                "gate": {"name": "other-factory-gate", "ok": True, "summary": "must not leak"},
                "job_token": {
                    "token_id": "other-factory-token",
                    "run_id": "other-factory-director",
                    "project_id": "L1-01",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {},
            },
        )
    )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            factory_run_id="factory-r26",
            project_id="L1-01",
        )
    ).projection

    assert projection["available"] is False
    assert projection["consumed_run_ids"] == []
    assert projection["query_scope"] == {
        "run_id": "",
        "factory_run_id": "factory-r26",
        "project_id": "L1-01",
    }


def test_read_run_ledger_projection_evidence_policy_failed_is_not_ok(tmp_path: Path) -> None:
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "stage": "real_run",
                "gate": {"name": "real_run_gate", "ok": True, "summary": "gate saw evidence"},
                "job_token": {
                    "token_id": "token-1",
                    "run_id": "run-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {
                        "enabled_evidence_modalities": ["command"],
                        "required_evidence_modalities": ["command"],
                    },
                },
                "physical_evidence": {
                    "modalities": {
                        "command": {
                            "present": True,
                            "ok": False,
                            "detail": "pytest failed",
                        }
                    }
                },
            },
        )
    )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-1")
    ).projection

    assert projection["ok"] is False
    assert projection["failed"] == 1
    assert projection["evidence_policy"]["ok"] is False
    assert projection["evidence_policy"]["missing_required_modalities"] == []
    assert projection["evidence_policy"]["failed_required_modalities"] == ["command"]


def test_read_run_ledger_projection_repair_missing_evidence_is_failed_not_missing(tmp_path: Path) -> None:
    append_result = append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "stage": "director_repair",
                "gate": {"name": "director_repair_gate", "ok": True, "summary": "repair wrote file"},
                "job_token": {
                    "token_id": "token-1",
                    "run_id": "run-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {
                        "enabled_evidence_modalities": ["repair"],
                        "required_evidence_modalities": ["repair"],
                    },
                },
                "physical_evidence": {
                    "repair_receipts": [
                        {
                            "receipt_id": "repair-1",
                            "source_tool": "deterministic_typescript_return_object_semicolon_repair",
                            "status": "applied",
                            "authoritative": False,
                            "evidence_status": "missing_evidence",
                        }
                    ],
                    "receipt_authority_policy": {
                        "schema_version": "director.repair_receipt_authority_policy.v1",
                        "authoritative_success": False,
                        "receipt_count": 1,
                        "missing_evidence_receipt_count": 1,
                        "failed_evidence_receipt_count": 0,
                        "non_authoritative_receipt_count": 1,
                    },
                },
            },
        )
    )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-1")
    ).projection
    canonical = build_run_ledger_projection([append_result.receipt["event"]])

    repair_modality = canonical["gates"][0]["evidence_modalities"]["repair"]
    assert projection["ok"] is False
    assert projection["evidence_policy"]["missing_required_modalities"] == []
    assert projection["evidence_policy"]["failed_required_modalities"] == ["repair"]
    assert repair_modality["present"] is True
    assert repair_modality["ok"] is False
    assert repair_modality["metadata"]["blocker"] == "repair_missing_revalidation_evidence"
    assert repair_modality["metadata"]["missing_evidence_receipt_count"] == 1


def test_read_run_ledger_projection_environment_prep_failed_is_failed_not_missing(tmp_path: Path) -> None:
    append_result = append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "stage": "director_repair",
                "gate": {"name": "director_repair_gate", "ok": True, "summary": "env prep ran"},
                "job_token": {
                    "token_id": "token-1",
                    "run_id": "run-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {
                        "enabled_evidence_modalities": ["environment_prep"],
                        "required_evidence_modalities": ["environment_prep"],
                    },
                },
                "physical_evidence": {
                    "environment_prep_receipts": [
                        {
                            "schema_version": "director.environment_prep_receipt.v1",
                            "plan_id": "env-prep-1",
                            "ecosystem": "node",
                            "package_manager": "npm",
                            "command": ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"],
                            "exit_code": 1,
                            "status": "failed",
                            "manifest": "package.json",
                            "error_code": "environment_prep_command_failed",
                        }
                    ],
                },
            },
        )
    )

    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-1")
    ).projection
    canonical = build_run_ledger_projection([append_result.receipt["event"]])

    env_modality = canonical["gates"][0]["evidence_modalities"]["environment_prep"]
    assert projection["ok"] is False
    assert projection["missing_required_modalities"] == []
    assert projection["failed_required_modalities"] == ["environment_prep"]
    assert projection["failed_evidence_details"]["required_modalities"] == ["environment_prep"]
    assert projection["evidence_policy"]["missing_required_modalities"] == []
    assert projection["evidence_policy"]["failed_required_modalities"] == ["environment_prep"]
    assert env_modality["present"] is True
    assert env_modality["ok"] is False
    assert env_modality["metadata"]["failed_receipt_count"] == 1
    assert env_modality["metadata"]["error_codes"] == ["environment_prep_command_failed"]


def test_read_run_ledger_projection_barrier_waits_for_effect_receipt(tmp_path: Path) -> None:
    result = append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-barrier",
            event={
                "event_type": "gate_evaluated",
                "stage": "director_mutation",
                "gate": {"name": "director_mutation", "ok": True, "summary": "effect persisted"},
                "job_token": {
                    "token_id": "token-barrier",
                    "run_id": "run-barrier",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {
                        "enabled_evidence_modalities": ["tool_receipt"],
                        "required_evidence_modalities": ["tool_receipt"],
                    },
                },
                "physical_evidence": {
                    "modalities": {
                        "tool_receipt": {
                            "present": True,
                            "ok": True,
                            "detail": "write_file receipt recorded",
                        }
                    }
                },
            },
        )
    )
    lifecycle_append_id = _append_successful_tool_lifecycle_event(tmp_path, run_id="run-barrier")
    event = result.receipt["event"]

    barrier_result = read_run_ledger_projection_barrier(
        ReadRunLedgerProjectionBarrierQueryV1(
            workspace=str(tmp_path),
            run_id="run-barrier",
            min_append_id=str(event["append_id"]),
        )
    )

    assert barrier_result.barrier["barrier_satisfied"] is True
    assert barrier_result.barrier["consumed_until_append_id"] == lifecycle_append_id
    assert event["append_id"] in barrier_result.barrier["consumed_append_ids"]
    assert barrier_result.projection["available"] is True
    assert barrier_result.projection["ok"] is True


def test_read_run_ledger_projection_barrier_reports_unsatisfied_snapshot(tmp_path: Path) -> None:
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-barrier-miss",
            event={
                "event_type": "gate_evaluated",
                "stage": "qa",
                "gate": {"name": "qa_verdict", "ok": True, "summary": "qa passed"},
                "job_token": {
                    "token_id": "token-barrier-miss",
                    "run_id": "run-barrier-miss",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {},
            },
        )
    )

    barrier_result = read_run_ledger_projection_barrier(
        ReadRunLedgerProjectionBarrierQueryV1(
            workspace=str(tmp_path),
            run_id="run-barrier-miss",
            min_append_id="append-not-yet-consumed",
            timeout_ms=0,
        )
    )

    assert barrier_result.barrier["barrier_satisfied"] is False
    assert barrier_result.barrier["event_count"] == 1
    assert barrier_result.projection["available"] is True


def test_append_run_ledger_event_publishes_control_plane_projection_event(tmp_path: Path, monkeypatch) -> None:
    class FakePublisher:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        def publish(self, *, subject: str, payload: dict[str, object]) -> bool:
            self.calls.append((subject, payload))
            return True

    publisher = FakePublisher()
    monkeypatch.setenv("KERNELONE_JETSTREAM_PUBLISH", "1")
    monkeypatch.setattr(run_ledger_service, "get_log_jetstream_publisher", lambda: publisher)
    monkeypatch.setattr(
        run_ledger_service,
        "resolve_storage_roots",
        lambda workspace: SimpleNamespace(workspace_key="workspace-key"),
    )

    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "stage": "qa_verifier",
                "gate": {"name": "qa_verifier", "ok": True, "summary": "qa verified"},
                "job_token": {
                    "token_id": "token-1",
                    "run_id": "run-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {},
            },
        )
    )

    assert len(publisher.calls) == 1
    subject, payload = publisher.calls[0]
    event_payload = payload["payload"]
    assert subject == "hp.runtime.workspace-key.status.control_plane"
    assert payload["schema_version"] == "runtime.v2"
    assert payload["channel"] == "status.control_plane"
    assert payload["kind"] == "control_plane_ledger_projection_update"
    assert isinstance(event_payload, dict)
    projection = event_payload["projection"]
    assert isinstance(projection, dict)
    assert projection["source"] == "run_ledger_projection"
    assert projection["available"] is True
    assert projection["projects"][0]["project_id"] == "P1"


def test_projection_transport_does_not_embed_full_durable_ledger_event(tmp_path: Path, monkeypatch) -> None:
    class FakePublisher:
        def __init__(self) -> None:
            self.payload: dict[str, object] = {}

        def publish(self, *, subject: str, payload: dict[str, object]) -> bool:
            del subject
            self.payload = payload
            return True

    publisher = FakePublisher()
    monkeypatch.setenv("KERNELONE_JETSTREAM_PUBLISH", "1")
    monkeypatch.setattr(run_ledger_service, "get_log_jetstream_publisher", lambda: publisher)
    monkeypatch.setattr(
        run_ledger_service,
        "resolve_storage_roots",
        lambda workspace: SimpleNamespace(workspace_key="workspace-key"),
    )
    huge_repair = {
        "full_evidence_ref": "runtime/qa/workspace-validation.json",
        "full_evidence_sha256": "a" * 64,
        "nested": "x" * 2_000_000,
    }

    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-large-event",
            event={
                "event_type": "gate_evaluated",
                "stage": "workspace_validation",
                "gate": {"name": "workspace_validation", "ok": False, "summary": "repair pending"},
                "job_token": {"token_id": "token-large", "run_id": "run-large-event", "project_id": "P1"},
                "physical_evidence": {"repair_result": huge_repair},
            },
        )
    )

    encoded = json.dumps(publisher.payload, ensure_ascii=False).encode("utf-8")
    event_payload = publisher.payload["payload"]
    assert isinstance(event_payload, dict)
    ledger_event = event_payload["ledger_event"]
    assert isinstance(ledger_event, dict)
    assert "physical_evidence" not in ledger_event
    assert ledger_event["physical_evidence_summary"]["repair_evidence_ref"] == ("runtime/qa/workspace-validation.json")
    assert len(encoded) < 64_000


def test_projection_transport_drops_unbounded_runtime_history(tmp_path: Path, monkeypatch) -> None:
    class FakePublisher:
        def __init__(self) -> None:
            self.payload: dict[str, object] = {}

        def publish(self, *, subject: str, payload: dict[str, object]) -> bool:
            del subject
            self.payload = payload
            return True

    publisher = FakePublisher()
    monkeypatch.setenv("KERNELONE_JETSTREAM_PUBLISH", "1")
    monkeypatch.setattr(run_ledger_service, "get_log_jetstream_publisher", lambda: publisher)
    monkeypatch.setattr(
        run_ledger_service,
        "resolve_storage_roots",
        lambda workspace: SimpleNamespace(workspace_key="workspace-key"),
    )
    huge_history = {"events": ["x" * 8_000 for _ in range(160)]}
    projection = {
        "schema_version": 1,
        "source": "run_ledger_projection",
        "available": True,
        "ok": False,
        "status": "blocked",
        "total": 1,
        "projected": 1,
        "missing": 0,
        "failed": 1,
        "detail": "one failed project",
        "tool_lifecycle": huge_history,
        "run_projection": huge_history,
        "projects": [
            {
                "project_id": "P1",
                "ok": False,
                "integrity_ok": True,
                "outcome_ok": False,
                "gate_count": 1,
                "failed_gate_count": 1,
                "latest_token_id": "token-1",
                "detail": "verifier failed",
                "missing": [],
                "tool_lifecycle": huge_history,
            }
        ],
    }
    monkeypatch.setattr(
        run_ledger_service,
        "read_run_ledger_projection",
        lambda _query: SimpleNamespace(projection=projection),
    )

    assert run_ledger_service._publish_run_ledger_projection_update(
        workspace=tmp_path,
        run_id="run-1",
        event={"event_id": "event-1", "event_type": "gate_evaluated"},
    )

    encoded = json.dumps(publisher.payload, ensure_ascii=False).encode("utf-8")
    event_payload = publisher.payload["payload"]
    assert isinstance(event_payload, dict)
    transported = event_payload["projection"]
    assert isinstance(transported, dict)
    assert "tool_lifecycle" not in transported
    assert "run_projection" not in transported
    assert "tool_lifecycle" not in transported["projects"][0]
    assert transported["projects"][0]["project_id"] == "P1"
    assert len(encoded) < 64_000


def test_append_run_ledger_event_publish_failure_is_visible_and_retry_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish_results = iter((False, True))
    monkeypatch.setenv("KERNELONE_JETSTREAM_PUBLISH", "1")
    monkeypatch.setattr(
        run_ledger_service,
        "_publish_run_ledger_projection_update",
        lambda **_kwargs: next(publish_results),
    )
    command = AppendRunLedgerEventCommandV1(
        workspace=str(tmp_path),
        run_id="run-publish-retry",
        event={"event_type": "gate_evaluated", "gate": {"name": "qa", "ok": True}},
    )

    with pytest.raises(RuntimeError, match="projection publish failed"):
        append_run_ledger_event(command)

    facts_after_failure = _control_plane_facts(tmp_path, run_id="run-publish-retry")
    rows_after_failure = RunLedger(tmp_path, run_id="run-publish-retry").read_events()
    assert len(facts_after_failure) == 1
    assert len(rows_after_failure) == 1

    replay = append_run_ledger_event(command)

    assert _control_plane_facts(tmp_path, run_id="run-publish-retry") == facts_after_failure
    assert RunLedger(tmp_path, run_id="run-publish-retry").read_events() == rows_after_failure
    assert replay.receipt["event"] == rows_after_failure[0]


def test_read_run_ledger_projection_ignores_migration_ledgers_by_default(tmp_path: Path) -> None:
    _write_ledger_event(tmp_path)

    result = read_run_ledger_projection(ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path), run_id="run-1"))
    projection = result.projection

    assert projection["source"] == "run_ledger_projection"
    assert projection["available"] is False
    assert projection["ok"] is False
    assert projection["migration_ledgers_included"] is False
    assert projection["projects"] == []


def test_read_run_ledger_projection_can_include_migration_ledgers_explicitly_for_migration(
    tmp_path: Path,
) -> None:
    _write_ledger_event(tmp_path)

    result = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            run_id="run-1",
            include_migration_ledgers=True,
        )
    )
    projection = result.projection

    assert projection["source"] == "run_ledger_projection"
    assert projection["available"] is True
    assert projection["ok"] is True
    assert projection["migration_ledgers_included"] is True
    assert projection["projects"][0]["project_id"] == "P1"
    assert projection["evidence_policy"]["enabled_modalities"] == ["browser"]
    assert projection["evidence_policy"]["required_modalities"] == []


def test_read_run_ledger_projection_returns_empty_when_no_ledger_exists(tmp_path: Path) -> None:
    result = read_run_ledger_projection(ReadRunLedgerProjectionQueryV1(workspace=str(tmp_path)))
    projection = result.projection

    assert projection["source"] == "run_ledger_projection"
    assert projection["available"] is False
    assert projection["status"] == "pending"
    assert projection["migration_ledgers_included"] is False
    assert projection["projects"] == []


def test_read_run_provenance_bundle_links_contract_blueprint_envelope_and_receipts(tmp_path: Path) -> None:
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "stage": "qa_verifier",
                "trace_id": "trace-1",
                "gate": {
                    "name": "qa_verifier",
                    "ok": True,
                    "summary": "verified",
                    "content_id": "qa-hash",
                },
                "job_token": {
                    "token_id": "token-1",
                    "run_id": "run-1",
                    "task_id": "TASK-1",
                    "project_id": "P1",
                    "contract_hash": "pm-hash",
                    "blueprint_hash": "ce-hash",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {
                        "enabled_evidence_modalities": ["tool_receipt", "command"],
                        "required_evidence_modalities": ["tool_receipt"],
                    },
                },
                "physical_evidence": {
                    "modalities": {"tool_receipt": {"present": True, "ok": True, "detail": "receipt verified"}},
                    "tool_receipts": [
                        {
                            "effect_receipt": _authoritative_directed_effect_receipt(run_id="run-1"),
                            "effect_receipt_commit": _authoritative_directed_effect_receipt_commit(run_id="run-1"),
                        }
                    ],
                    "commands": [{"command": "python -m unittest", "ok": True, "exit_code": 0}],
                    "final_request_context_audit": {
                        "schema_version": "llm.final_request_context_audit.v1",
                        "final_request_evidence_coverage": {
                            "schema_version": "polaris.final_request_evidence_coverage.v1",
                            "request_hash": "provider-request-hash",
                            "workflow_chain": {
                                "pm_contract_hash": "pm-hash",
                                "ce_blueprint_hash": "ce-hash",
                                "handoff_decision_hash": "handoff-hash",
                                "execution_profile_hash": "profile-hash",
                                "execution_envelope_hash": "envelope-hash",
                            },
                        },
                    },
                    "context_snapshot_ref": "abcdefabcdefabcdefabcdef",
                },
            },
        )
    )
    _append_successful_tool_lifecycle_event(tmp_path, run_id="run-1")

    bundle = read_run_provenance_bundle(ReadRunProvenanceBundleQueryV1(workspace=str(tmp_path), run_id="run-1")).bundle

    assert bundle["schema_version"] == "polaris.run_provenance_bundle.v1"
    assert bundle["bundle_id"].startswith("run-prov-")
    assert bundle["run_id"] == "run-1"
    assert bundle["task_id"] == "TASK-1"
    assert bundle["status"] == "success"
    assert bundle["pm_contract_hash"] == "pm-hash"
    assert bundle["ce_blueprint_hash"] == "ce-hash"
    assert bundle["handoff_decision_hash"] == "handoff-hash"
    assert bundle["execution_envelope_hash"] == "envelope-hash"
    assert bundle["final_provider_request_hashes"] == ["provider-request-hash"]
    assert bundle["tool_receipt_hashes"]
    assert bundle["command_receipt_hashes"]
    assert "abcdefabcdefabcdefabcdef" in bundle["evidence_refs"]
    assert bundle["invalid_evidence_refs"] == []


def test_read_run_provenance_bundle_rejects_path_shaped_context_snapshot_ref(
    tmp_path: Path,
) -> None:
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "stage": "llm_request",
                "gate": {"name": "llm_request", "ok": True, "summary": "snapshot emitted"},
                "job_token": {
                    "token_id": "token-1",
                    "run_id": "run-1",
                    "task_id": "TASK-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                },
                "physical_evidence": {
                    "context_snapshot_ref": "runtime/contexts/aa/provider-request.json",
                    "evidence_ref": "runtime/evidence/provider-request.json",
                },
            },
        )
    )

    bundle = read_run_provenance_bundle(ReadRunProvenanceBundleQueryV1(workspace=str(tmp_path), run_id="run-1")).bundle

    assert "runtime/contexts/aa/provider-request.json" not in bundle["evidence_refs"]
    assert "runtime/evidence/provider-request.json" in bundle["evidence_refs"]
    assert bundle["invalid_evidence_refs"] == [
        {
            "ref_type": "context_snapshot_ref",
            "value": "runtime/contexts/aa/provider-request.json",
            "reason": "context hash must be a 24-character lowercase hexadecimal string",
        }
    ]


def test_read_run_provenance_bundle_marks_failed_required_evidence_as_failed(
    tmp_path: Path,
) -> None:
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(tmp_path),
            run_id="run-1",
            event={
                "event_type": "gate_evaluated",
                "stage": "real_run",
                "gate": {"name": "real_run_gate", "ok": True, "summary": "gate emitted command evidence"},
                "job_token": {
                    "token_id": "token-1",
                    "run_id": "run-1",
                    "task_id": "TASK-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {"required_evidence_modalities": ["command"]},
                },
                "physical_evidence": {
                    "modalities": {
                        "command": {
                            "present": True,
                            "ok": False,
                            "detail": "npm test failed",
                            "exit_code": 1,
                        }
                    },
                    "commands": [{"command": "npm test", "ok": False, "exit_code": 1}],
                },
            },
        )
    )

    bundle = read_run_provenance_bundle(ReadRunProvenanceBundleQueryV1(workspace=str(tmp_path), run_id="run-1")).bundle

    assert bundle["status"] == "failed"
    assert bundle["missing_required_modalities"] == []
    assert bundle["failed_required_modalities"] == ["command"]


def test_read_run_provenance_bundle_exposes_missing_authority_hashes(tmp_path: Path) -> None:
    bundle = read_run_provenance_bundle(
        ReadRunProvenanceBundleQueryV1(workspace=str(tmp_path), run_id="missing-run")
    ).bundle

    assert bundle["schema_version"] == "polaris.run_provenance_bundle.v1"
    assert bundle["run_id"] == "missing-run"
    assert bundle["status"] == "blocked"
    assert bundle["pm_contract_hash"] == "missing:pm_contract_hash"
    assert bundle["ce_blueprint_hash"] == "missing:ce_blueprint_hash"
    assert bundle["handoff_decision_hash"] == "missing:handoff_decision_hash"
    assert bundle["execution_envelope_hash"] == "missing:execution_envelope_hash"


def test_summarize_run_ledger_projection_tool_result_failed_is_recoverable_not_integrity() -> None:
    """M08 (caller side): summarize_run_ledger_projection must respect failure_status.failed.
    A recoverable TOOL_RESULT_FAILED (tool ran, ok=False) is product-quality, not integrity.
    L1-01 r27 still died on TOOL_RESULT_FAILED because this caller checked the summary's
    raw ok flag instead of the M08 failed verdict.
    """
    from polaris.cells.control_plane.run_ledger.public.tool_lifecycle import build_tool_call_lifecycle_receipt

    receipt = build_tool_call_lifecycle_receipt(
        run_id="run-1",
        task_id="TASK-1",
        turn_id="turn-1",
        role="director",
        native_tool_calls_count=1,
        decoded_tool_calls_count=1,
        dispatched_tool_calls_count=1,
        receipts=[
            {
                "batch_id": "batch-1",
                "failure_count": 1,
                "results": [{"tool_name": "write_file", "status": "failed", "reason": "deo_director_policy_denied"}],
            }
        ],
        reason="deo_director_policy_denied",
    ).to_dict()
    projection = build_run_ledger_projection(
        [
            {
                "event_type": "gate_evaluated",
                "stage": "director",
                "gate": {"name": "director", "ok": True, "summary": "started"},
                "job_token": {
                    "token_id": "token-1",
                    "project_id": "P1",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
                "physical_evidence": {},
            },
            {"event_type": "tool_call_lifecycle", "tool_call_lifecycle_receipt": receipt},
        ]
    )
    summary = summarize_run_ledger_projection(projection)
    assert projection["tool_lifecycle"]["ok"] is False
    assert summary["ok"] is True  # recoverable per M08, not integrity break
    assert summary["failed_control_plane_events"] == []
