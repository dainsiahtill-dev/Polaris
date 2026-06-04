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


def _qa_runtime_metadata(workflow_input: QAWorkflowInput) -> dict[str, Any]:
    metadata = dict(workflow_input.metadata) if isinstance(workflow_input.metadata, dict) else {}
    metadata.setdefault("qa_session_id", f"qa-{workflow_input.run_id}")
    metadata.setdefault("cognitive_runtime_required", True)
    metadata.setdefault("context_os_expected", True)
    return metadata


def _activity_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload_value = payload.get("payload") if isinstance(payload, dict) else {}
    return cast("dict[str, Any]", payload_value if isinstance(payload_value, dict) else {})


def _result_errors(*payloads: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for payload in payloads:
        raw_errors = payload.get("errors") if isinstance(payload, dict) else None
        if isinstance(raw_errors, list):
            errors.extend(str(item).strip() for item in raw_errors if str(item).strip())
    return errors


def _result_path(payload: dict[str, Any]) -> str:
    activity_payload = _activity_payload(payload)
    return str(activity_payload.get("result_path") or "").strip()


@workflow.defn
class QAWorkflow(WorkflowQueryState):
    """Run unit and integration QA checks after Director completes."""

    def __init__(self) -> None:
        super().__init__()

    @workflow.run
    async def run(self, workflow_input: QAWorkflowInput) -> QAWorkflowResult:
        metadata = _qa_runtime_metadata(workflow_input)
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
            _receipt_success, receipt_payload = _result_success(
                await workflow.execute_activity(
                    "record_qa_cognitive_receipt",
                    {
                        "run_id": workflow_input.run_id,
                        "workspace": workflow_input.workspace,
                        "status": "skipped",
                        "reason": reason,
                        "summary": "QA skipped because Director did not complete cleanly",
                        "metadata": metadata,
                    },
                    start_to_close_timeout=timedelta(minutes=2),
                )
            )
            return QAWorkflowResult(
                run_id=workflow_input.run_id,
                passed=False,
                reason=reason,
                evidence={
                    "skipped": True,
                    "cognitive_runtime": _activity_payload(receipt_payload).get("cognitive_runtime_receipt", {}),
                },
            )

        unit_success, unit_payload = _result_success(
            await workflow.execute_activity(
                "run_unit_qa",
                {
                    "run_id": workflow_input.run_id,
                    "workspace": workflow_input.workspace,
                    "metadata": metadata,
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
                    "metadata": metadata,
                },
                start_to_close_timeout=timedelta(minutes=15),
            )
        )
        qa_core_passed = bool(unit_success and integration_success)
        receipt_success, receipt_payload = _result_success(
            await workflow.execute_activity(
                "record_qa_cognitive_receipt",
                {
                    "run_id": workflow_input.run_id,
                    "workspace": workflow_input.workspace,
                    "status": "completed" if qa_core_passed else "failed",
                    "reason": "qa_passed" if qa_core_passed else "qa_failed",
                    "summary": "QA workflow completed",
                    "unit": _activity_payload(unit_payload),
                    "integration": _activity_payload(integration_payload),
                    "errors": _result_errors(unit_payload, integration_payload),
                    "evidence_refs": [
                        path for path in (_result_path(unit_payload), _result_path(integration_payload)) if path
                    ],
                    "metadata": metadata,
                },
                start_to_close_timeout=timedelta(minutes=2),
            )
        )

        evidence_payload = await workflow.execute_activity(
            "collect_evidence",
            {
                "run_id": workflow_input.run_id,
                "evidence": {
                    "unit": _activity_payload(unit_payload),
                    "integration": _activity_payload(integration_payload),
                    "cognitive_runtime": _activity_payload(receipt_payload).get("cognitive_runtime_receipt", {}),
                },
                "metadata": metadata,
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

        passed = bool(qa_core_passed and receipt_success)
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
