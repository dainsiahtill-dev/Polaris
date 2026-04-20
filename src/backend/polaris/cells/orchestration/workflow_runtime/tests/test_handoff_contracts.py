"""Regression tests for workflow handoff and pending async receipt contracts."""

from __future__ import annotations

from polaris.cells.orchestration.workflow_runtime.internal.models import (
    DirectorWorkflowInput,
    PMWorkflowInput,
    TaskContract,
)


def test_task_contract_preserves_pending_async_receipt_payload() -> None:
    raw_task = {
        "id": "task-async-1",
        "title": "Resume async receipt",
        "goal": "continue the workflow after a pending async tool",
        "handoff_reason": "async_operation",
        "pending_async_count": 1,
        "pending_async_receipts": [
            {
                "call_id": "async_1",
                "tool_name": "create_pull_request",
                "status": "pending",
            }
        ],
    }

    contract = TaskContract.from_mapping(raw_task)

    assert contract.task_id == "task-async-1"
    assert contract.payload["handoff_reason"] == "async_operation"
    assert contract.payload["pending_async_count"] == 1
    assert contract.payload["pending_async_receipts"][0]["tool_name"] == "create_pull_request"

    round_tripped = contract.to_dict()
    assert round_tripped["handoff_reason"] == "async_operation"
    assert round_tripped["pending_async_count"] == 1
    assert round_tripped["pending_async_receipts"][0]["call_id"] == "async_1"


def test_director_workflow_input_keeps_handoff_context_and_pending_receipts() -> None:
    workflow_input = DirectorWorkflowInput.from_mapping(
        {
            "workspace": ".",
            "run_id": "run-async-handoff",
            "tasks": [
                {
                    "id": "task-async-1",
                    "title": "Resume async receipt",
                    "goal": "continue the workflow after a pending async tool",
                    "handoff_reason": "async_operation",
                    "pending_async_count": 1,
                    "pending_async_receipts": [
                        {
                            "call_id": "async_1",
                            "tool_name": "create_pull_request",
                            "status": "pending",
                        }
                    ],
                }
            ],
            "metadata": {
                "handoff_reason": "async_operation",
                "pending_async_count": 1,
            },
        }
    )

    assert workflow_input.run_id == "run-async-handoff"
    assert workflow_input.metadata["handoff_reason"] == "async_operation"
    assert workflow_input.metadata["pending_async_count"] == 1
    assert workflow_input.tasks[0].payload["pending_async_receipts"][0]["tool_name"] == "create_pull_request"


def test_pm_workflow_input_preserves_precomputed_handoff_tasks() -> None:
    workflow_input = PMWorkflowInput.from_mapping(
        {
            "workspace": ".",
            "run_id": "run-pm-handoff",
            "precomputed_payload": {
                "tasks": [
                    {
                        "id": "task-pm-1",
                        "title": "Prepare async handoff",
                        "goal": "hand off pending receipt data to workflow runtime",
                        "handoff_reason": "async_operation",
                        "pending_async_count": 1,
                        "pending_async_receipts": [
                            {
                                "call_id": "async_1",
                                "tool_name": "create_pull_request",
                                "status": "pending",
                            }
                        ],
                    }
                ]
            },
            "metadata": {
                "handoff_reason": "async_operation",
            },
        }
    )

    tasks = workflow_input.payload_tasks()

    assert workflow_input.run_id == "run-pm-handoff"
    assert workflow_input.metadata["handoff_reason"] == "async_operation"
    assert len(tasks) == 1
    assert tasks[0].to_dict()["pending_async_count"] == 1
    assert tasks[0].to_dict()["pending_async_receipts"][0]["tool_name"] == "create_pull_request"
