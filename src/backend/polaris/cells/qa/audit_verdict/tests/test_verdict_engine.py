"""Tests for canonical QA verdict conflict resolution and classification."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from polaris.cells.qa.audit_verdict.internal.verdict_engine import (
    QA_VERDICT_CONFLICT_MATRIX_V1,
    QAVerdictEngine,
)


def _task_boundary(
    task_id: str,
    run_id: str,
    *,
    ok: bool = True,
    status: str = "completed_verified",
    failure_class: str = "PASSED",
    reason: str = "Task boundary verified",
    responsible_layer: str = "execution_control_plane",
) -> dict[str, object]:
    return {
        "schema_version": "polaris.task_boundary_verdict.v1",
        "task_id": task_id,
        "run_id": run_id,
        "status": status,
        "ok": ok,
        "failure_class": failure_class,
        "reason": reason,
        "responsible_layer": responsible_layer,
    }


def _canonical_ledger(
    task_id: str,
    run_id: str,
    *,
    boundary: dict[str, object] | None = None,
    **extra: object,
) -> dict[str, object]:
    selected = boundary or _task_boundary(task_id, run_id)
    return {
        "schema_version": 1,
        "source": "run_ledger_projection",
        "available": True,
        "consumed_run_ids": [run_id],
        "task_boundary": {
            "latest_by_task": {task_id: selected},
            "latest": selected,
            "failed": [] if bool(selected.get("ok")) else [selected],
        },
        **extra,
    }


def _build(
    tmp_path: Path,
    *,
    task_id: str = "TASK-1",
    run_id: str = "RUN-1",
    ledger: dict[str, object] | None = None,
    barrier: dict[str, object] | None = None,
    payload_extra: dict[str, object] | None = None,
    **kwargs: Any,
):
    payload: dict[str, object] = {"task_id": task_id, "run_id": run_id, **(payload_extra or {})}
    return QAVerdictEngine(str(tmp_path)).build_envelope(
        task_id=task_id,
        payload=payload,
        ledger_projection=ledger or _canonical_ledger(task_id, run_id),
        barrier=barrier or {},
        **kwargs,
    )


@pytest.mark.parametrize(
    ("run_id", "ledger", "expected_conflict"),
    [
        ("", {}, "missing_run_id"),
        ("RUN-1", {"source": "run_ledger_projection", "available": False}, "ledger_projection_unavailable"),
        (
            "RUN-1",
            {"source": "factory_projection", "available": True, "consumed_run_ids": ["RUN-1"]},
            "ledger_projection_source_invalid",
        ),
        (
            "RUN-1",
            {"source": "run_ledger_projection", "available": True, "consumed_run_ids": ["RUN-OTHER"]},
            "ledger_projection_run_scope_mismatch",
        ),
    ],
)
def test_canonical_projection_conflicts_fail_closed(
    tmp_path: Path,
    run_id: str,
    ledger: dict[str, object],
    expected_conflict: str,
) -> None:
    envelope = _build(tmp_path, run_id=run_id, ledger=ledger)

    assert envelope.verdict == "BLOCKED"
    assert envelope.ok is False
    assert envelope.next_stage == "pending_qa"
    assert envelope.terminal_status == ""
    assert expected_conflict in envelope.evidence["conflict_matrix"]["conflicts"]


def test_unsatisfied_barrier_beats_local_pass(tmp_path: Path) -> None:
    envelope = _build(
        tmp_path,
        payload_extra={"min_append_id": "append-1"},
        barrier={"run_id": "RUN-1", "barrier_satisfied": False},
        audit_result={"verdict": "PASS", "ok": True},
    )

    assert envelope.verdict == "BLOCKED"
    assert envelope.next_stage == "pending_qa"
    assert "projection_barrier_unsatisfied" in envelope.evidence["conflict_matrix"]["conflicts"]


def test_satisfied_barrier_and_completed_task_boundary_allow_pass(tmp_path: Path) -> None:
    envelope = _build(
        tmp_path,
        payload_extra={"min_append_id": "append-1"},
        barrier={
            "schema_version": "run_ledger.projection_barrier.v1",
            "run_id": "RUN-1",
            "barrier_satisfied": True,
            "consumed_append_ids": ["append-1"],
        },
        audit_result={"verdict": "PASS", "ok": True},
        artifact_quality={"errors": [], "issues": []},
    )

    assert envelope.verdict == "PASS"
    assert envelope.ok is True
    assert envelope.next_stage == ""
    assert envelope.terminal_status == "resolved"
    assert envelope.classification.failure_class is None
    assert envelope.evidence["conflict_matrix"]["conflicts"] == []


@pytest.mark.parametrize(
    "boundary",
    [
        {},
        {"task_id": "TASK-1", "run_id": "RUN-1", "status": "pending", "ok": True},
        _task_boundary("TASK-OTHER", "RUN-1"),
        _task_boundary("TASK-1", "RUN-OTHER"),
    ],
)
def test_missing_or_non_authoritative_task_boundary_blocks(tmp_path: Path, boundary: dict[str, object]) -> None:
    ledger = _canonical_ledger("TASK-1", "RUN-1")
    ledger["task_boundary"] = {
        "latest_by_task": {"TASK-1": boundary} if boundary else {},
        "latest": boundary,
        "failed": [],
    }

    envelope = _build(tmp_path, ledger=ledger)

    assert envelope.verdict == "BLOCKED"
    assert envelope.next_stage == "pending_qa"
    conflicts = envelope.evidence["conflict_matrix"]["conflicts"]
    assert any(code.startswith("task_boundary_") for code in conflicts)


def test_conflict_matrix_precedence_is_explicit_and_unique() -> None:
    conflicts = [rule.conflict for rule in QA_VERDICT_CONFLICT_MATRIX_V1]

    assert conflicts[0].value == "missing_run_id"
    assert len(conflicts) == len(set(conflicts))


def test_typed_artifact_quality_missing_target_routes_to_director(tmp_path: Path) -> None:
    envelope = _build(
        tmp_path,
        artifact_quality={
            "errors": ["legacy artifact quality text"],
            "issues": [
                {
                    "code": "declared_target_missing",
                    "message": "declared target file src/index.js is missing",
                    "path": "src/index.js",
                }
            ],
        },
    )

    assert envelope.verdict == "FAIL"
    assert envelope.next_stage == "pending_exec"
    assert envelope.classification.failure_class == "INCOMPLETE_MATERIALIZATION"
    assert envelope.classification.repairable_by_director is True


def test_tool_lifecycle_dropped_projection_blocks_as_platform_failure(tmp_path: Path) -> None:
    ledger = _canonical_ledger(
        "TASK-1",
        "RUN-1",
        tool_lifecycle={
            "ok": False,
            "dropped_count": 1,
            "failed_count": 1,
            "events": [
                {
                    "dropped": True,
                    "failed": True,
                    "status": "dropped",
                    "failure_class": "TOOL_DISPATCH_DROPPED",
                    "reason": "provider emitted a write_file call but dispatch was not committed",
                }
            ],
        },
    )

    envelope = _build(tmp_path, ledger=ledger)

    assert envelope.verdict == "BLOCKED"
    assert envelope.next_stage == "waiting_human"
    assert envelope.classification.failure_class == "TOOL_DISPATCH_DROPPED"


def test_failed_task_boundary_beats_unrelated_latest_success(tmp_path: Path) -> None:
    failed = _task_boundary(
        "TASK-1",
        "RUN-1",
        ok=False,
        status="incomplete_materialization",
        failure_class="INCOMPLETE_MATERIALIZATION",
        reason="TASK-1 remains incomplete",
        responsible_layer="director",
    )
    ledger = _canonical_ledger("TASK-1", "RUN-1", boundary=failed)
    ledger["task_boundary"] = {
        "latest": _task_boundary("TASK-OTHER", "RUN-1"),
        "latest_by_task": {"TASK-1": failed},
        "failed": [failed],
    }

    envelope = _build(tmp_path, ledger=ledger)

    assert envelope.verdict == "FAIL"
    assert envelope.next_stage == "pending_exec"
    assert envelope.classification.failure_class == "INCOMPLETE_MATERIALIZATION"


def test_failed_required_evidence_is_compiler_or_test_failure(tmp_path: Path) -> None:
    ledger = _canonical_ledger(
        "TASK-1",
        "RUN-1",
        evidence_policy={
            "required_modalities": ["command"],
            "missing_required_modalities": [],
            "failed_required_modalities": ["command"],
        },
    )

    envelope = _build(tmp_path, ledger=ledger)

    assert envelope.verdict == "FAIL"
    assert envelope.next_stage == "pending_exec"
    assert envelope.classification.failure_class == "COMPILER_OR_TEST_FAILURE"


def test_non_completed_boundary_cannot_authorize_local_pass(tmp_path: Path) -> None:
    boundary = _task_boundary(
        "TASK-1",
        "RUN-1",
        ok=True,
        status="dependency_not_unlocked",
        failure_class="PASSED",
    )

    envelope = _build(
        tmp_path,
        ledger=_canonical_ledger("TASK-1", "RUN-1", boundary=boundary),
        audit_result={"verdict": "PASS", "ok": True},
    )

    assert envelope.verdict == "BLOCKED"
    assert envelope.next_stage == "pending_qa"
    assert envelope.classification.failure_class == "LEDGER_PROJECTION_INCOMPLETE"


def test_prose_cannot_override_typed_qa_failure_class(tmp_path: Path) -> None:
    envelope = _build(
        tmp_path,
        gate_summary="scope mismatch, unauthorized, and contract ambiguous",
        audit_result={
            "verdict": "FAIL",
            "failure_class": "COMPILER_OR_TEST_FAILURE",
            "responsible_layer": "director",
        },
    )

    assert envelope.verdict == "FAIL"
    assert envelope.next_stage == "pending_exec"
    assert envelope.classification.failure_class == "COMPILER_OR_TEST_FAILURE"
    assert envelope.classification.responsible_layer == "director"
