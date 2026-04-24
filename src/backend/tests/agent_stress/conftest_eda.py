"""Shared fixtures and mock consumers for EDA closed-loop stress tests.

This module provides deterministic integration test infrastructure for the full
PM → CE → Director → QA single-round closed loop via TaskMarket EDA hub.

All fixtures use ``tmp_path`` for workspace isolation so tests are fully
independent (no shared state, no order dependencies).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1,
    PublishTaskWorkItemCommandV1,
    QueryTaskMarketStatusV1,
)
from polaris.cells.runtime.task_market.public.service import (
    TaskMarketService,
    get_task_market_service,
    reset_task_market_service,
)

# ---------------------------------------------------------------------------
# Isolation fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_task_market_service():
    """Reset the TaskMarketService singleton before each test.

    The singleton is shared by all real consumers (CEConsumer, etc.).
    Resetting it ensures each test gets an isolated service instance.
    """
    reset_task_market_service()
    os.environ["KERNELONE_TASK_MARKET_STORE"] = "json"
    yield
    reset_task_market_service()
    os.environ.pop("KERNELONE_TASK_MARKET_STORE", None)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Temporary workspace directory simulating a real project root."""
    ws = tmp_path / "test_workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


# ---------------------------------------------------------------------------
# Payload builders — minimal valid payloads for each pipeline stage
# ---------------------------------------------------------------------------


def make_pm_payload(
    task_id: str,
    *,
    title: str = "Implement login API",
    workspace: str = "",
    run_dir: str = "",
    cache_root: str = "",
    run_id: str = "",
    pm_iteration: int = 1,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Minimal PM task payload published to pending_design or pending_exec."""
    return {
        "task_id": task_id,
        "title": title,
        "workspace": workspace,
        "run_dir": run_dir,
        "cache_root": cache_root,
        "run_id": run_id,
        "pm_iteration": pm_iteration,
        **(extra or {}),
    }


def make_ce_ack_metadata(
    task_id: str,
    blueprint_id: str | None = None,
    guardrails: list[str] | None = None,
    no_touch_zones: list[str] | None = None,
    scope_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Metadata that CE includes when acknowledging to pending_exec."""
    return {
        "blueprint_id": blueprint_id or f"bp-{task_id}",
        "guardrails": guardrails or ["no_delete", "preserve_tests"],
        "no_touch_zones": no_touch_zones or [".git", "node_modules"],
        "scope_paths": scope_paths or [],
        "ce_generated": True,
    }


def make_director_ack_metadata(
    task_id: str,
    target_files: list[str] | None = None,
    scope: list[str] | None = None,
    summary: str = "Implementation complete",
) -> dict[str, Any]:
    """Metadata that Director includes when acknowledging to pending_qa."""
    return {
        "target_files": target_files or [],
        "scope": scope or [],
        "execution_summary": summary,
        "director_generated": True,
    }


# ---------------------------------------------------------------------------
# Mock consumers — simulate the real role consumers without LLM calls
# ---------------------------------------------------------------------------


class MockCEConsumer:
    """Simulates CEConsumer.poll_once() for pending_design tasks.

    In a real run this would call CE preflight and generate a blueprint.
    In this mock we validate required runtime context fields and return
    fake CE output metadata.
    """

    def __init__(self, workspace: str, worker_id: str = "ce-mock-1") -> None:
        self.workspace = workspace
        self.worker_id = worker_id
        self.visibility_timeout = 900
        self.svc = get_task_market_service()

    def poll_once(self) -> list[dict[str, Any]]:
        """Claim one pending_design task, process, ack to pending_exec."""
        claim = self.svc.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=self.workspace,
                stage="pending_design",
                worker_id=self.worker_id,
                worker_role="chief_engineer",
                visibility_timeout_seconds=self.visibility_timeout,
            )
        )
        if not claim.ok:
            return []

        payload = dict(claim.payload) if claim.payload else {}
        task_id = claim.task_id
        lease_token = claim.lease_token

        # Simulate CE preflight — validate required context fields exist
        run_dir = payload.get("run_dir", "")
        cache_root = payload.get("cache_root", "")
        if not run_dir or not cache_root:
            # Missing runtime context — requeue
            self.svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self.workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code="MISSING_RUNTIME_CONTEXT",
                    error_message="run_dir or cache_root missing in payload",
                    requeue_stage="pending_design",
                )
            )
            return [{"task_id": task_id, "ok": False, "reason": "missing_runtime_context"}]

        # Simulate CE generating blueprint metadata
        ack_meta = make_ce_ack_metadata(
            task_id=task_id,
            blueprint_id=f"bp-{task_id}",
            guardrails=["no_delete", "preserve_tests"],
            no_touch_zones=[".git", "node_modules"],
            scope_paths=["src/login.py"],
        )

        self.svc.acknowledge_task_stage(
            AcknowledgeTaskStageCommandV1(
                workspace=self.workspace,
                task_id=task_id,
                lease_token=lease_token,
                next_stage="pending_exec",
                summary="Blueprint generated by CE",
                metadata=ack_meta,
            )
        )
        return [{"task_id": task_id, "ok": True, "next_stage": "pending_exec"}]


class MockDirectorConsumer:
    """Simulates DirectorExecutionConsumer.poll_once() for pending_exec tasks."""

    def __init__(self, workspace: str, worker_id: str = "director-mock-1") -> None:
        self.workspace = workspace
        self.worker_id = worker_id
        self.visibility_timeout = 1800
        self.svc = get_task_market_service()

    def poll_once(self) -> list[dict[str, Any]]:
        """Claim one pending_exec task, process, ack to pending_qa."""
        claim = self.svc.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=self.workspace,
                stage="pending_exec",
                worker_id=self.worker_id,
                worker_role="director",
                visibility_timeout_seconds=self.visibility_timeout,
            )
        )
        if not claim.ok:
            return []

        payload = dict(claim.payload) if claim.payload else {}
        task_id = claim.task_id
        lease_token = claim.lease_token

        # Validate blueprint_id exists (set by CE acknowledge)
        blueprint_id = payload.get("blueprint_id", "")
        if not blueprint_id:
            # No blueprint — move to dead letter
            self.svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self.workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code="MISSING_BLUEPRINT",
                    error_message="No blueprint_id in payload",
                    to_dead_letter=True,
                )
            )
            return [{"task_id": task_id, "ok": False, "reason": "missing_blueprint"}]

        # Simulate director execution — generate execution metadata
        ack_meta = make_director_ack_metadata(
            task_id=task_id,
            target_files=["src/login.py", "tests/test_login.py"],
            scope=["src/login.py"],
            summary="Implementation complete",
        )

        self.svc.acknowledge_task_stage(
            AcknowledgeTaskStageCommandV1(
                workspace=self.workspace,
                task_id=task_id,
                lease_token=lease_token,
                next_stage="pending_qa",
                summary="Execution complete",
                metadata=ack_meta,
            )
        )
        return [{"task_id": task_id, "ok": True, "next_stage": "pending_qa"}]


class MockQAConsumer:
    """Simulates QAConsumer.poll_once() for pending_qa tasks."""

    def __init__(self, workspace: str, worker_id: str = "qa-mock-1") -> None:
        self.workspace = workspace
        self.worker_id = worker_id
        self.visibility_timeout = 900
        self.svc = get_task_market_service()

    def poll_once(self) -> list[dict[str, Any]]:
        """Claim one pending_qa task, audit, resolve."""
        claim = self.svc.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=self.workspace,
                stage="pending_qa",
                worker_id=self.worker_id,
                worker_role="qa",
                visibility_timeout_seconds=self.visibility_timeout,
            )
        )
        if not claim.ok:
            return []

        task_id = claim.task_id
        lease_token = claim.lease_token
        payload = dict(claim.payload) if claim.payload else {}

        # Simulate QA audit — verify blueprint_id and execution_metadata exist
        blueprint_id = payload.get("blueprint_id", "")
        execution_summary = payload.get("execution_summary", "")

        if not blueprint_id or not execution_summary:
            # Incomplete metadata — reject
            self.svc.acknowledge_task_stage(
                AcknowledgeTaskStageCommandV1(
                    workspace=self.workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    terminal_status="rejected",
                    summary="QA rejected: missing required metadata",
                    metadata={"verdict": "REJECT", "reason": "incomplete_metadata"},
                )
            )
            return [{"task_id": task_id, "ok": True, "verdict": "REJECT", "status": "rejected"}]

        # Simulate QA passing — resolve
        self.svc.acknowledge_task_stage(
            AcknowledgeTaskStageCommandV1(
                workspace=self.workspace,
                task_id=task_id,
                lease_token=lease_token,
                terminal_status="resolved",
                summary="QA resolved: all checks passed",
                metadata={
                    "verdict": "PASS",
                    "score": 100.0,
                    "findings": [],
                },
            )
        )
        return [{"task_id": task_id, "ok": True, "verdict": "PASS", "status": "resolved"}]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def publish_design_task(
    svc: TaskMarketService,
    workspace: Path,
    *,
    task_id: str = "task-design-1",
    trace_id: str = "trace-1",
    run_id: str = "run-1",
    run_dir: str = "",
    cache_root: str = "",
    extra_payload: dict[str, Any] | None = None,
) -> Any:
    """Publish a task to pending_design (simulating PM in EDA/shadow mode)."""
    return svc.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id=trace_id,
            run_id=run_id,
            task_id=task_id,
            stage="pending_design",
            source_role="pm",
            payload=make_pm_payload(
                task_id=task_id,
                workspace=str(workspace),
                run_dir=run_dir,
                cache_root=cache_root,
                run_id=run_id,
                extra=extra_payload,
            ),
            priority="high",
            max_attempts=3,
        )
    )


def publish_exec_task(
    svc: TaskMarketService,
    workspace: Path,
    *,
    task_id: str = "task-exec-1",
    trace_id: str = "trace-1",
    run_id: str = "run-1",
    run_dir: str = "",
    cache_root: str = "",
    pm_iteration: int = 1,
    extra_payload: dict[str, Any] | None = None,
) -> Any:
    """Publish a task directly to pending_exec (simulating PM in mainline mode)."""
    return svc.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id=trace_id,
            run_id=run_id,
            task_id=task_id,
            stage="pending_exec",
            source_role="pm",
            payload=make_pm_payload(
                task_id=task_id,
                workspace=str(workspace),
                run_dir=run_dir,
                cache_root=cache_root,
                run_id=run_id,
                pm_iteration=pm_iteration,
                extra=extra_payload,
            ),
            priority="high",
            max_attempts=3,
        )
    )


def get_task_status(svc: TaskMarketService, workspace: Path, task_id: str) -> dict[str, Any]:
    """Query task market status for a specific task."""
    result = svc.query_status(QueryTaskMarketStatusV1(workspace=str(workspace), include_payload=True))
    for item in result.items:
        if item.get("task_id") == task_id:
            return item
    return {}
