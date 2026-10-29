"""Shared publish/claim helpers for task_market claim-readiness tests.

Extracted so sibling test modules (test_servicea, test_serviceb) stop
importing test classes from each other (test-as-library debt).
"""

from __future__ import annotations

from pathlib import Path

from polaris.cells.runtime.task_market.public.contracts import (
    ClaimTaskWorkItemCommandV1,
    PublishTaskWorkItemCommandV1,
    TaskWorkItemResultV1,
)
from polaris.cells.runtime.task_market.public.service import TaskMarketService


def publish_gate_item(
    service: TaskMarketService,
    workspace: Path,
    task_id: str,
    *,
    is_leaf: bool = True,
    depends_on: tuple[str, ...] = (),
    parent: str = "",
) -> None:
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id=f"tr-{task_id}",
            run_id="run-gate",
            task_id=task_id,
            stage="pending_exec",
            source_role="chief_engineer",
            payload={"title": task_id},
            is_leaf=is_leaf,
            depends_on=tuple(depends_on),
            parent_task_id=parent,
        )
    )


def scan_claim(service: TaskMarketService, workspace: Path) -> TaskWorkItemResultV1:
    return service.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="dw-1",
            worker_role="director",
        )
    )
