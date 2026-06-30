from __future__ import annotations

import pytest
from polaris.cells.orchestration.workflow_activity.internal.activities.qa_activities import (
    _qa_activity_classification,
)
from polaris.cells.orchestration.workflow_activity.internal.workflows import qa_workflow
from polaris.cells.orchestration.workflow_activity.internal.workflows.qa_workflow import (
    _payload_classification,
    _register_traceability_verdict_activity,
    _workflow_classification,
)


def test_qa_activity_classification_uses_canonical_failure_class() -> None:
    classification = _qa_activity_classification(
        passed=False,
        reason="Unit QA command failed",
    )

    assert classification["schema_version"] == "polaris.qa_failure_classification.v1"
    assert classification["failure_class"] == "IMPLEMENTATION_DEFECT"
    assert classification["route"] == "pending_exec"
    assert classification["repairable_by_director"] is True
    assert classification["responsible_layer"] == "director"


def test_qa_activity_classification_routes_test_environment_failures_to_qa() -> None:
    classification = _qa_activity_classification(
        passed=False,
        reason="QA command runtime error",
        failure_class="TEST_ENVIRONMENT_FAILURE",
    )

    assert classification["failure_class"] == "TEST_ENVIRONMENT_FAILURE"
    assert classification["route"] == "pending_qa"
    assert classification["repairable_by_director"] is False
    assert classification["responsible_layer"] == "qa_infra"


def test_workflow_classification_marks_unfinished_director_as_incomplete_materialization() -> None:
    classification = _workflow_classification(
        passed=False,
        reason="director_status_failed",
        director_status="failed",
    )

    assert classification["failure_class"] == "INCOMPLETE_MATERIALIZATION"
    assert classification["route"] == "pending_exec"
    assert classification["repairable_by_director"] is True
    assert classification["responsible_layer"] == "director"


def test_workflow_payload_classification_reads_nested_activity_payload() -> None:
    nested = {
        "schema_version": "polaris.qa_failure_classification.v1",
        "failure_class": "scope_mismatch",
        "route": "pending_design",
        "reason": "scope mismatch",
        "repairable_by_director": False,
        "severity": "high",
        "requires_ce_replan": True,
        "requires_pm_revision": False,
        "owner": "chief_engineer",
        "responsible_layer": "chief_engineer",
        "evidence_refs": [],
    }

    normalized = _payload_classification({"payload": {"qa_failure_classification": nested}})

    assert normalized["schema_version"] == "polaris.qa_failure_classification.v1"
    assert normalized["failure_class"] == "BLUEPRINT_SCOPE_MISMATCH"


def test_workflow_payload_classification_rejects_legacy_schema() -> None:
    nested = {
        "schema_version": "qa.failure_classification.v1",
        "failure_class": "IMPLEMENTATION_DEFECT",
    }

    assert _payload_classification({"payload": {"qa_failure_classification": nested}}) == {}


@pytest.mark.asyncio
async def test_traceability_verdict_activity_writes_classification_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _capture_node(*_args: object, **kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(qa_workflow, "safe_register_node", _capture_node)
    classification = _workflow_classification(
        passed=False,
        reason="qa_failed",
        director_status="completed",
    )

    result = await _register_traceability_verdict_activity(
        {
            "run_id": "run-1",
            "workspace": "",
            "passed": False,
            "reason": "qa_failed",
            "evidence": {"qa_failure_classification": classification},
        }
    )

    metadata = captured["metadata"]
    assert result["success"] is True
    assert isinstance(metadata, dict)
    assert metadata["failure_class"] == "IMPLEMENTATION_DEFECT"
    assert metadata["qa_failure_classification"] == classification
