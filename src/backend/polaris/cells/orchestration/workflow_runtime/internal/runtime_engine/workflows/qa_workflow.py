"""Workflow workflow for QA verification."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast

from polaris.cells.orchestration.workflow_runtime.internal.models import QAWorkflowInput, QAWorkflowResult
from polaris.cells.orchestration.workflow_runtime.internal.runtime_queries import WorkflowQueryState
from polaris.cells.orchestration.workflow_runtime.internal.workflow_client import get_workflow_api

workflow = get_workflow_api()


def _result_success(payload: Any) -> tuple[bool, dict[str, Any]]:
    if isinstance(payload, dict):
        return bool(payload.get("success")), payload
    return False, {}


@workflow.defn
class QAWorkflow(WorkflowQueryState):
    """Run unit and integration QA checks after Director completes."""

    def __init__(self) -> None:
        super().__init__()

    @workflow.run
    async def run(self, workflow_input: QAWorkflowInput) -> QAWorkflowResult:
        self._record_event(
            stage="qa_started",
            message="QA workflow started",
            details={
                "run_id": workflow_input.run_id,
                "director_status": workflow_input.director_status,
            },
        )
        if workflow_input.director_status != "completed":
            reason = f"director_status_{workflow_input.director_status or 'unknown'}"
            self._record_event(
                stage="qa_skipped",
                message="QA skipped because Director did not complete cleanly",
                details={"reason": reason},
            )
            return QAWorkflowResult(
                run_id=workflow_input.run_id,
                passed=False,
                reason=reason,
                evidence={"skipped": True},
            )

        unit_success, unit_payload = _result_success(
            await workflow.execute_activity(
                "run_unit_qa",
                {
                    "run_id": workflow_input.run_id,
                    "workspace": workflow_input.workspace,
                    "metadata": (dict(workflow_input.metadata) if isinstance(workflow_input.metadata, dict) else {}),
                },
                start_to_close_timeout=timedelta(minutes=10),
            )
        )
        integration_success, integration_payload = _result_success(
            await workflow.execute_activity(
                "run_integration_qa",
                {
                    "run_id": workflow_input.run_id,
                    "workspace": workflow_input.workspace,
                    "metadata": (dict(workflow_input.metadata) if isinstance(workflow_input.metadata, dict) else {}),
                },
                start_to_close_timeout=timedelta(minutes=15),
            )
        )

        evidence_payload = await workflow.execute_activity(
            "collect_evidence",
            {
                "run_id": workflow_input.run_id,
                "evidence": {
                    "unit": unit_payload.get("payload") if isinstance(unit_payload, dict) else {},
                    "integration": (
                        integration_payload.get("payload") if isinstance(integration_payload, dict) else {}
                    ),
                },
                "metadata": (dict(workflow_input.metadata) if isinstance(workflow_input.metadata, dict) else {}),
            },
            start_to_close_timeout=timedelta(minutes=2),
        )
        evidence: dict[str, Any] = {}
        if isinstance(evidence_payload, dict):
            payload = evidence_payload.get("payload")
            if isinstance(payload, dict):
                evidence = cast(
                    "dict[str, Any]",
                    payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {},
                )

        passed = bool(unit_success and integration_success)
        reason = "qa_passed" if passed else "qa_failed"
        self._record_event(
            stage="qa_completed",
            message="QA workflow completed",
            details={"run_id": workflow_input.run_id, "passed": passed},
        )
        return QAWorkflowResult(
            run_id=workflow_input.run_id,
            passed=passed,
            reason=reason,
            evidence=evidence,
        )
