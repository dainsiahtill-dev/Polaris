"""Regression tests for exact-run causal attribution."""

from __future__ import annotations

from polaris.cells.audit.diagnosis.internal.exact_run_causal_audit import build_exact_run_causal_report


def _factory(*, status: str = "failed", failed_stages: list[str] | None = None) -> dict[str, object]:
    return {
        "available": True,
        "status": status,
        "failed_stages": failed_stages or [],
        "chain_completed": status == "completed",
    }


def _ledger(
    *,
    ok: bool,
    integrity_ok: bool = True,
    outcome_ok: bool = True,
    revision_issues: list[str] | None = None,
    failed_required: list[str] | None = None,
    missing_required: list[str] | None = None,
    task_boundary_ok: bool = True,
) -> dict[str, object]:
    return {
        "available": True,
        "ok": ok,
        "status": "completed" if ok else "failed",
        "failed_control_plane_events": [],
        "run_projection": {
            "integrity_ok": integrity_ok,
            "outcome_ok": outcome_ok,
            "historical_failed_gate_count": 7,
            "gate_revisions": {"issues": revision_issues or [], "resolved_forks": []},
        },
        "evidence_policy": {
            "failed_required_modalities": failed_required or [],
            "missing_required_modalities": missing_required or [],
        },
        "evidence_modalities": {
            "qa": {"ok": True, "total": 2, "failed": 0},
            "command": {"ok": not bool(failed_required), "total": 2},
        },
        "task_boundary": {
            "ok": task_boundary_ok,
            "historical_failed_count": 3,
            "latest_by_task": {"TASK-1": {"status": "completed_verified"}},
        },
        "tool_lifecycle": {"ok": True, "effect_receipt_count": 2, "failed_count": 5},
    }


def test_gate_revision_fork_is_primary_even_when_physical_delivery_is_green() -> None:
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-1",
        project_id="L3-21",
        factory_projection=_factory(failed_stages=["quality_gate"]),
        ledger_projection=_ledger(
            ok=False,
            integrity_ok=False,
            outcome_ok=False,
            revision_issues=["gate_revision_chain_fork_or_stale:218"],
        ),
        terminal_task_runtime_projection=None,
    )

    assert report["root_cause_code"] == "control_plane.run_ledger.gate_revision_fork_after_runtime_reentry"
    assert report["responsible_cell"] == "control_plane.run_ledger"
    assert report["retry_boundary"] == "same_run_quality_gate_only"
    assert report["pm_ce_restart_allowed"] is False
    assert report["target_project_defect"] is False
    assert report["authority_contradiction_detected"] is True


def test_historical_failures_do_not_override_current_delivery_verified() -> None:
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-2",
        project_id="L3-21",
        factory_projection=_factory(status="completed"),
        ledger_projection=_ledger(ok=True),
        terminal_task_runtime_projection=None,
    )

    assert report["current_status"] == "DELIVERY_VERIFIED"
    assert report["project_id"] == "L3-21"
    assert report["root_cause_code"] == ""
    assert report["historical_error_count"] == 10


def test_real_failed_verifier_is_target_defect_and_retries_only_that_verifier() -> None:
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-3",
        project_id="L3-21",
        factory_projection=_factory(failed_stages=["quality_gate"]),
        ledger_projection=_ledger(ok=False, outcome_ok=False, failed_required=["command"]),
        terminal_task_runtime_projection=None,
    )

    assert report["root_cause_code"] == "qa.audit_verdict.required_evidence_failed"
    assert report["responsible_cell"] == "qa.audit_verdict"
    assert report["retry_boundary"] == "same_failed_verifier_only"
    assert report["target_project_defect"] is True


def test_role_failure_requires_final_provider_request_evidence() -> None:
    report = build_exact_run_causal_report(
        workspace="/tmp/project",
        factory_run_id="factory-4",
        project_id="L3-21",
        factory_projection=_factory(failed_stages=["director_dispatch"]),
        ledger_projection=_ledger(ok=True),
        terminal_task_runtime_projection=None,
    )

    assert report["root_cause_code"] == "director.runtime.stage_failed"
    assert report["evidence_gaps"] == ["final_provider_request_unavailable_for_role_or_tool_failure"]
