"""Tests for the evidence-driven QA verdict engine."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.qa.audit_verdict.internal.verdict_engine import QAVerdictEngine, diff_verdicts


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


def test_missing_required_evidence_routes_to_director_repair(tmp_path: Path) -> None:
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

    assert payload["verdict"] == "FAIL"
    assert payload["next_stage"] == "pending_exec"
    assert payload["classification"]["failure_class"] == "EXECUTION_EVIDENCE_MISSING"
    assert payload["classification"]["repairable_by_director"] is True
    assert payload["authority"]["contract_hash"] == "contract-hash"


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
    assert payload["classification"]["failure_class"] == "TEST_ENVIRONMENT_FAILURE"
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


def test_verdict_diff_reports_shadow_mismatch(tmp_path: Path) -> None:
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
        legacy_verdict="PASS",
        legacy_terminal_status="resolved",
        engine_envelope=envelope,
    )

    assert diff["mismatch"] is True
    assert set(diff["mismatches"]) == {"verdict", "next_stage", "terminal_status"}
    assert diff["engine"]["classification"]["failure_class"] == "EXECUTION_EVIDENCE_MISSING"
