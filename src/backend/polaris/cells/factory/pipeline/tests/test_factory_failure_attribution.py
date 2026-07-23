"""Regression coverage for structured Factory-stage failure attribution.

All text in this module is UTF-8.
"""

from __future__ import annotations

from polaris.cells.factory.pipeline.internal.bench_gates import (
    apply_factory_bench_failure_taxonomy,
)


def _run_ledger_without_task_boundary() -> dict[str, object]:
    return {
        "schema_version": 1,
        "source": "run_ledger",
        "ok": False,
        "integrity_ok": True,
        "outcome_ok": False,
        "event_count": 1,
        "gate_count": 1,
        "missing": [],
        "failed_gates": [],
        "capability": {"ok": True, "issues": [], "latest_token_id": "token-1"},
        "evidence_policy": {
            "ok": False,
            "integrity_ok": True,
            "outcome_ok": False,
            "required_modalities": [],
            "missing_required_modalities": [],
            "failed_required_modalities": [],
        },
    }


def test_structured_ce_failure_precedes_expected_downstream_boundary_absence() -> None:
    record: dict[str, object] = {
        "all_checks_passed": False,
        "run_ledger_projection": _run_ledger_without_task_boundary(),
        "chain": {
            "audit_bundle": {
                "current_stage": "chief_engineer_review",
                "failure": {
                    "stage": "chief_engineer_review",
                    "code": "FACTORY_STAGE_FAILED",
                    "error_code": "chief_engineer.llm_review_failed",
                    "failure_class": "ROLE_LLM_REVIEW_FAILED",
                    "responsible_layer": "chief_engineer",
                    "root_cause_hint": "Request timeout (240.0s)",
                    "detail": "Chief Engineer portfolio review failed",
                    "recoverable": True,
                },
            }
        },
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "chief_engineer_blueprint"
    assert taxonomy["root_cause_signature"] == "chief_engineer_blueprint:llm_review_failed"
    assert taxonomy["authoritative"] is True
    assert taxonomy["evidence"] == ["Request timeout (240.0s)"]


def test_missing_boundary_remains_causal_without_structured_upstream_failure() -> None:
    record: dict[str, object] = {
        "all_checks_passed": False,
        "run_ledger_projection": _run_ledger_without_task_boundary(),
    }

    taxonomy = apply_factory_bench_failure_taxonomy(record)

    assert taxonomy["category"] == "control_plane"
    assert taxonomy["root_cause_signature"] == "control_plane:task_boundary_verdict_missing"
