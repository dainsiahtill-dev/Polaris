"""QA verification workflow.

Migrated from:
  polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/workflows/qa_workflow.py

ACGA 2.0: This module is Cell-local and must NOT be imported by other Cells
without going through the public contract.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from polaris.cells.orchestration.workflow_activity.internal.activities.base import register_activity
from polaris.cells.orchestration.workflow_activity.internal.embedded_api import get_workflow_api
from polaris.cells.orchestration.workflow_activity.internal.models import QAWorkflowInput, QAWorkflowResult
from polaris.cells.orchestration.workflow_activity.internal.runtime_queries import WorkflowQueryState
from polaris.cells.orchestration.workflow_activity.internal.workflow_client import get_activity_api
from polaris.kernelone.traceability.internal.safety import safe_register_node
from polaris.kernelone.traceability.public.service import create_traceability_service

workflow = get_workflow_api()
activity = get_activity_api()
logger = logging.getLogger(__name__)


def _result_success(payload: Any) -> tuple[bool, dict[str, Any]]:
    if isinstance(payload, dict):
        return bool(payload.get("success")), payload
    return False, {}


@register_activity("register_traceability_verdict")
@activity.defn(name="register_traceability_verdict")
async def _register_traceability_verdict_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Register a qa_verdict traceability node as a bypass observer."""
    run_id = str((payload or {}).get("run_id") or "").strip()
    workspace = str((payload or {}).get("workspace") or "").strip()
    passed = bool((payload or {}).get("passed"))
    reason = str((payload or {}).get("reason") or "").strip()
    evidence = (payload or {}).get("evidence")
    evidence_dict = evidence if isinstance(evidence, dict) else {}

    trace_service = create_traceability_service(workspace) if workspace else None
    safe_register_node(
        trace_service,
        node_kind="qa_verdict",
        role="qa",
        external_id=f"qa-{run_id}",
        content=json.dumps(
            {"passed": passed, "reason": reason, "evidence": evidence_dict},
            ensure_ascii=False,
        ),
        metadata={"run_id": run_id, "workspace": workspace, "reason": reason},
    )
    return {"success": True}


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
                evidence_payload_data = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
                evidence = evidence_payload_data  # type: ignore[assignment]

        passed = bool(unit_success and integration_success)
        reason = "qa_passed" if passed else "qa_failed"
        self._record_event(
            stage="qa_completed",
            message="QA workflow completed",
            details={"run_id": workflow_input.run_id, "passed": passed},
        )
        try:
            await workflow.execute_activity(
                "register_traceability_verdict",
                {
                    "run_id": workflow_input.run_id,
                    "workspace": workflow_input.workspace,
                    "passed": passed,
                    "reason": reason,
                    "evidence": evidence,
                },
                start_to_close_timeout=timedelta(minutes=1),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Traceability registration activity failed for run %s: %s",
                workflow_input.run_id,
                exc,
                exc_info=False,
            )
        return QAWorkflowResult(
            run_id=workflow_input.run_id,
            passed=passed,
            reason=reason,
            evidence=evidence,
        )
