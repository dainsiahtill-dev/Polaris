"""Tests for the evidence-driven QA verdict engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from polaris.cells.control_plane.run_ledger.public import empty_tool_lifecycle_summary
from polaris.cells.qa.audit_verdict.internal.verdict_engine import (
    QAVerdictEngine as ProductionQAVerdictEngine,
    classify_qa_audit_failure,
    diff_verdicts,
)
from polaris.cells.qa.audit_verdict.public.contracts import QaVerdictEnvelopeV1


def _canonical_ledger(partial: dict[str, Any]) -> dict[str, Any]:
    ledger = {
        "schema_version": 1,
        "source": "run_ledger_projection",
        "available": True,
        "consumed_run_ids": ["run-qa"],
        **partial,
    }
    task_boundary = dict(ledger.get("task_boundary") or {})
    latest = dict(task_boundary.get("latest") or {})
    if not latest:
        latest = {
            "status": "completed_verified",
            "ok": True,
            "failure_class": "PASSED",
            "responsible_layer": "execution_control_plane",
            "reason": "Task boundary verified",
        }
    latest.update(
        {
            "schema_version": "polaris.task_boundary_verdict.v1",
            "task_id": "task-qa",
            "run_id": "run-qa",
        }
    )
    task_boundary["latest"] = latest
    task_boundary["latest_by_task"] = {"task-qa": latest}
    task_boundary.setdefault("failed", [] if bool(latest.get("ok")) else [latest])
    ledger["task_boundary"] = task_boundary
    ledger.setdefault("tool_lifecycle", empty_tool_lifecycle_summary(requirement=False))
    return ledger


class QAVerdictEngine(ProductionQAVerdictEngine):
    """Inject canonical control-plane framing into classification unit tests."""

    def build_envelope(self, *args: Any, **kwargs: Any) -> QaVerdictEnvelopeV1:
        kwargs["ledger_projection"] = _canonical_ledger(dict(kwargs.get("ledger_projection") or {}))
        return super().build_envelope(*args, **kwargs)


def _payload() -> dict[str, object]:
    return {
        "task_id": "task-qa",
        "job_token": {
            "token_id": "token-qa",
            "run_id": "run-qa",
            "contract_hash": "contract-hash",
            "blueprint_hash": "blueprint-hash",
            "target_files": ["src/app.py"],
            "allowed_paths": ["src/app.py"],
            "capability_audit": {"ok": True, "issues": []},
        },
    }


def test_missing_required_evidence_blocks_on_execution_control_plane(tmp_path: Path) -> None:
    envelope = QAVerdictEngine(str(tmp_path)).build_envelope(
        task_id="task-qa",
        payload=_payload(),
        ledger_projection={
            "audit_path": str(tmp_path / "runtime" / "control_plane" / "ledger"),
            "evidence_policy": {
                "required_modalities": ["command"],
                "missing_required_modalities": ["command"],
                "failed_required_modalities": [],
            },
        },
        artifact_quality={"errors": []},
    )
    payload = envelope.to_dict()

    assert payload["verdict"] == "BLOCKED"
    assert payload["next_stage"] == "pending_qa"
    assert payload["classification"]["failure_class"] == "EXECUTION_EVIDENCE_MISSING"
    assert payload["classification"]["repairable_by_director"] is False
    assert payload["classification"]["responsible_layer"] == "execution_control_plane"
    assert payload["authority"]["contract_hash"] == "contract-hash"


def test_missing_director_changed_files_is_execution_control_plane_evidence_failure(
    tmp_path: Path,
) -> None:
    audit_result = {
        "verdict": "FAIL",
        "metrics": {"missing_director_changed_files_evidence": True},
        "findings": ["Director changed_files evidence is required for code task QA"],
    }

    failure_class, responsible_layer = classify_qa_audit_failure(audit_result)
    envelope = QAVerdictEngine(str(tmp_path)).build_envelope(
        task_id="task-qa",
        payload=_payload(),
        audit_result=audit_result,
        ledger_projection={
            "audit_path": "runtime/control_plane/ledger",
            "evidence_policy": {},
        },
        artifact_quality={"errors": []},
    )
    payload = envelope.to_dict()

    assert failure_class == "EXECUTION_EVIDENCE_MISSING"
    assert responsible_layer == "execution_control_plane"
    assert payload["verdict"] == "BLOCKED"
    assert payload["next_stage"] == "pending_qa"
    assert payload["classification"]["failure_class"] == "EXECUTION_EVIDENCE_MISSING"
    assert payload["classification"]["responsible_layer"] == "execution_control_plane"
    assert payload["classification"]["repairable_by_director"] is False


def test_unsatisfied_barrier_blocks_qa_instead_of_director_repair(tmp_path: Path) -> None:
    envelope = QAVerdictEngine(str(tmp_path)).build_envelope(
        task_id="task-qa",
        payload=_payload(),
        ledger_projection={"audit_path": "runtime/control_plane/ledger", "evidence_policy": {}},
        barrier={"barrier_satisfied": False, "ledger_paths": ["runtime/control_plane/ledger/run-qa.ndjson"]},
        artifact_quality={"errors": []},
    )
    payload = envelope.to_dict()

    assert payload["verdict"] == "BLOCKED"
    assert payload["next_stage"] == "pending_qa"
    assert payload["classification"]["failure_class"] == "LEDGER_PROJECTION_INCOMPLETE"
    assert payload["classification"]["repairable_by_director"] is False


def test_contract_amendment_routes_to_ce_replan(tmp_path: Path) -> None:
    envelope = QAVerdictEngine(str(tmp_path)).build_envelope(
        task_id="task-qa",
        payload=_payload(),
        ledger_projection={"audit_path": "runtime/control_plane/ledger", "evidence_policy": {}},
        artifact_quality={
            "errors": [],
            "contract_amendment_request": {
                "reason": "imported symbol has no declared provider",
                "suggested_owner": "chief_engineer",
            },
        },
    )
    payload = envelope.to_dict()

    assert payload["verdict"] == "FAIL"
    assert payload["next_stage"] == "pending_design"
    assert payload["classification"]["failure_class"] == "BLUEPRINT_SCOPE_MISMATCH"
    assert payload["classification"]["requires_ce_replan"] is True
    assert payload["classification"]["repairable_by_director"] is False


def test_tool_dispatch_dropped_routes_to_platform_block(tmp_path: Path) -> None:
    envelope = QAVerdictEngine(str(tmp_path)).build_envelope(
        task_id="task-qa",
        payload=_payload(),
        ledger_projection={
            "audit_path": "runtime/control_plane/ledger",
            "evidence_policy": {},
            "tool_lifecycle": {
                "ok": False,
                "dropped_count": 1,
                "events": [{"failure_class": "TOOL_DISPATCH_DROPPED"}],
            },
        },
        artifact_quality={"errors": []},
    )
    payload = envelope.to_dict()

    assert payload["verdict"] == "BLOCKED"
    assert payload["next_stage"] == "waiting_human"
    assert payload["classification"]["failure_class"] == "TOOL_DISPATCH_DROPPED"
    assert payload["classification"]["repairable_by_director"] is False
    assert payload["classification"]["responsible_layer"] == "execution_control_plane"


def test_tool_lifecycle_failure_routes_to_platform_block(tmp_path: Path) -> None:
    envelope = QAVerdictEngine(str(tmp_path)).build_envelope(
        task_id="task-qa",
        payload=_payload(),
        ledger_projection={
            "audit_path": "runtime/control_plane/ledger",
            "evidence_policy": {},
            "tool_lifecycle": {
                "ok": False,
                "dropped_count": 0,
                "failed_count": 1,
                "events": [
                    {
                        "failed": True,
                        "failure_class": "MISSING_EFFECT_RECEIPT",
                        "reason": "write_file success lacked an effect receipt",
                    }
                ],
            },
        },
        artifact_quality={"errors": []},
    )
    payload = envelope.to_dict()

    assert payload["verdict"] == "BLOCKED"
    assert payload["next_stage"] == "waiting_human"
    assert payload["classification"]["failure_class"] == "MISSING_EFFECT_RECEIPT"
    assert payload["classification"]["repairable_by_director"] is False
    assert payload["classification"]["responsible_layer"] == "execution_control_plane"


def test_missing_entrypoint_target_routes_to_ce_replan(tmp_path: Path) -> None:
    envelope = QAVerdictEngine(str(tmp_path)).build_envelope(
        task_id="task-qa",
        payload=_payload(),
        ledger_projection={
            "audit_path": "runtime/control_plane/ledger",
            "evidence_policy": {},
            "task_boundary": {
                "ok": False,
                "latest": {
                    "status": "missing_entrypoint_target",
                    "ok": False,
                    "failure_class": "MISSING_ENTRYPOINT_TARGET",
                    "responsible_layer": "task_boundary",
                    "reason": "Manifest references src/index.js outside current/downstream artifacts",
                },
            },
        },
        artifact_quality={"errors": []},
    )
    payload = envelope.to_dict()

    assert payload["verdict"] == "FAIL"
    assert payload["next_stage"] == "pending_design"
    assert payload["classification"]["failure_class"] == "MISSING_ENTRYPOINT_TARGET"
    assert payload["classification"]["requires_ce_replan"] is True
    assert payload["classification"]["repairable_by_director"] is False


def test_incomplete_materialization_routes_to_director_retry(tmp_path: Path) -> None:
    envelope = QAVerdictEngine(str(tmp_path)).build_envelope(
        task_id="task-qa",
        payload=_payload(),
        ledger_projection={
            "audit_path": "runtime/control_plane/ledger",
            "evidence_policy": {},
            "task_boundary": {
                "ok": False,
                "latest": {
                    "status": "incomplete_materialization",
                    "ok": False,
                    "failure_class": "INCOMPLETE_MATERIALIZATION",
                    "responsible_layer": "director",
                    "reason": "Required target files were not materialized",
                },
            },
        },
        artifact_quality={"errors": []},
    )
    payload = envelope.to_dict()

    assert payload["verdict"] == "FAIL"
    assert payload["next_stage"] == "pending_exec"
    assert payload["classification"]["failure_class"] == "INCOMPLETE_MATERIALIZATION"
    assert payload["classification"]["repairable_by_director"] is True


def test_task_boundary_failure_class_alias_routes_to_director_retry(tmp_path: Path) -> None:
    envelope = QAVerdictEngine(str(tmp_path)).build_envelope(
        task_id="task-qa",
        payload=_payload(),
        ledger_projection={
            "audit_path": "runtime/control_plane/ledger",
            "evidence_policy": {},
            "task_boundary": {
                "ok": False,
                "latest": {
                    "status": "incomplete_materialization",
                    "ok": False,
                    "failure_class": "incomplete-materialization",
                    "responsible_layer": "director",
                    "reason": "Required target files were not materialized",
                },
            },
        },
        artifact_quality={"errors": []},
    )
    payload = envelope.to_dict()

    assert payload["verdict"] == "FAIL"
    assert payload["next_stage"] == "pending_exec"
    assert payload["classification"]["failure_class"] == "INCOMPLETE_MATERIALIZATION"
    assert payload["classification"]["repairable_by_director"] is True


def test_deferred_followup_routes_to_director_retry(tmp_path: Path) -> None:
    envelope = QAVerdictEngine(str(tmp_path)).build_envelope(
        task_id="task-qa",
        payload=_payload(),
        ledger_projection={
            "audit_path": "runtime/control_plane/ledger",
            "evidence_policy": {},
            "task_boundary": {
                "ok": False,
                "latest": {
                    "status": "deferred_followup_required",
                    "ok": False,
                    "failure_class": "DEFERRED_FOLLOWUP_REQUIRED",
                    "responsible_layer": "execution_control_plane",
                    "reason": "mutation_bypass_blocked",
                },
            },
        },
        artifact_quality={"errors": []},
    )
    payload = envelope.to_dict()

    assert payload["verdict"] == "FAIL"
    assert payload["next_stage"] == "pending_exec"
    assert payload["classification"]["failure_class"] == "DEFERRED_FOLLOWUP_REQUIRED"
    assert payload["classification"]["repairable_by_director"] is True


def test_task_boundary_evidence_missing_blocks_on_execution_control_plane(tmp_path: Path) -> None:
    envelope = QAVerdictEngine(str(tmp_path)).build_envelope(
        task_id="task-qa",
        payload=_payload(),
        ledger_projection={
            "audit_path": "runtime/control_plane/ledger",
            "evidence_policy": {},
            "task_boundary": {
                "ok": False,
                "latest": {
                    "status": "execution_evidence_missing",
                    "ok": False,
                    "failure_class": "EXECUTION_EVIDENCE_MISSING",
                    "responsible_layer": "director",
                    "reason": "Required execution evidence was not committed",
                },
            },
        },
        artifact_quality={"errors": []},
    )
    payload = envelope.to_dict()

    assert payload["verdict"] == "BLOCKED"
    assert payload["next_stage"] == "pending_qa"
    assert payload["classification"]["failure_class"] == "EXECUTION_EVIDENCE_MISSING"
    assert payload["classification"]["repairable_by_director"] is False
    assert payload["classification"]["responsible_layer"] == "execution_control_plane"
    assert payload["classification"]["owner"] == "execution_control_plane"


def test_task_boundary_implementation_defect_routes_to_director_retry(tmp_path: Path) -> None:
    envelope = QAVerdictEngine(str(tmp_path)).build_envelope(
        task_id="task-qa",
        payload=_payload(),
        ledger_projection={
            "audit_path": "runtime/control_plane/ledger",
            "evidence_policy": {},
            "task_boundary": {
                "ok": False,
                "latest": {
                    "status": "required_verifier_failed",
                    "ok": False,
                    "failure_class": "IMPLEMENTATION_DEFECT",
                    "responsible_layer": "director",
                    "reason": "Required verifier execution failed",
                },
            },
        },
        artifact_quality={"errors": []},
    )
    payload = envelope.to_dict()

    assert payload["verdict"] == "FAIL"
    assert payload["next_stage"] == "pending_exec"
    assert payload["classification"]["failure_class"] == "IMPLEMENTATION_DEFECT"
    assert payload["classification"]["repairable_by_director"] is True
    assert payload["classification"]["owner"] == "director"


def test_task_boundary_dependency_not_unlocked_blocks_execution_control_plane(tmp_path: Path) -> None:
    envelope = QAVerdictEngine(str(tmp_path)).build_envelope(
        task_id="task-qa",
        payload=_payload(),
        ledger_projection={
            "audit_path": "runtime/control_plane/ledger",
            "evidence_policy": {},
            "task_boundary": {
                "ok": False,
                "latest": {
                    "status": "dependency_not_unlocked",
                    "ok": False,
                    "failure_class": "DEPENDENCY_NOT_UNLOCKED",
                    "responsible_layer": "execution_control_plane",
                    "reason": "Task dependencies are not unlocked for completion",
                },
            },
        },
        artifact_quality={"errors": []},
    )
    payload = envelope.to_dict()

    assert payload["verdict"] == "BLOCKED"
    assert payload["next_stage"] == "pending_exec"
    assert payload["classification"]["failure_class"] == "DEPENDENCY_NOT_UNLOCKED"
    assert payload["classification"]["repairable_by_director"] is False
    assert payload["classification"]["owner"] == "execution_control_plane"


def test_verdict_diff_reports_local_projection_mismatch(tmp_path: Path) -> None:
    envelope = QAVerdictEngine(str(tmp_path)).build_envelope(
        task_id="task-qa",
        payload=_payload(),
        ledger_projection={
            "audit_path": "runtime/control_plane/ledger",
            "evidence_policy": {"missing_required_modalities": ["command"]},
        },
        artifact_quality={"errors": []},
    )

    diff = diff_verdicts(
        fallback_verdict="PASS",
        fallback_terminal_status="resolved",
        engine_envelope=envelope,
    )

    assert diff["mismatch"] is True
    assert set(diff["mismatches"]) == {"verdict", "next_stage", "terminal_status"}
    assert diff["engine"]["classification"]["failure_class"] == "EXECUTION_EVIDENCE_MISSING"
