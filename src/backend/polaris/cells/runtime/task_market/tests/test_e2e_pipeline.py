"""End-to-end integration tests for the mainline-durable pipeline.

Tests the full PM publish → CE claim/ack → Director claim/ack → QA claim/ack → resolved
lifecycle through ConsumerLoopManager with fake consumers.
"""

from __future__ import annotations

import threading
import time

from polaris.cells.runtime.task_market.internal.consumer_loop import ConsumerLoopManager
from polaris.cells.runtime.task_market.internal.service import TaskMarketService
from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1,
    PublishTaskWorkItemCommandV1,
    ResolveHumanReviewCommandV1,
)

# ---------------------------------------------------------------------------
# Fake consumers
# ---------------------------------------------------------------------------


class FakeCEConsumer:
    """Claims pending_design, acks to pending_exec with blueprint metadata."""

    def __init__(
        self,
        workspace: str,
        worker_id: str,
        poll_interval: float = 0.1,
        visibility_timeout_seconds: int = 60,
        **kwargs: object,
    ) -> None:
        self._service = TaskMarketService()
        self._workspace = workspace
        self._worker_id = worker_id
        self._poll_interval = poll_interval
        self._visibility_timeout = visibility_timeout_seconds
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                result = self._service.claim_work_item(
                    ClaimTaskWorkItemCommandV1(
                        workspace=self._workspace,
                        stage="pending_design",
                        worker_id=self._worker_id,
                        worker_role="chief_engineer",
                        visibility_timeout_seconds=self._visibility_timeout,
                    )
                )
                if result.ok:
                    self._service.acknowledge_task_stage(
                        AcknowledgeTaskStageCommandV1(
                            workspace=self._workspace,
                            task_id=result.task_id,
                            lease_token=result.lease_token,
                            next_stage="pending_exec",
                            summary="CE produced blueprint",
                            metadata={"blueprint_id": "bp-fake-1"},
                        )
                    )
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(self._poll_interval)

    def stop(self) -> None:
        self._stop.set()


class FakeDirectorConsumer:
    """Claims pending_exec, acks to pending_qa with execution metadata."""

    def __init__(
        self,
        workspace: str,
        worker_id: str,
        poll_interval: float = 0.1,
        visibility_timeout_seconds: int = 60,
        enable_safe_parallel: bool = False,
        **kwargs: object,
    ) -> None:
        self._service = TaskMarketService()
        self._workspace = workspace
        self._worker_id = worker_id
        self._poll_interval = poll_interval
        self._visibility_timeout = visibility_timeout_seconds
        self._stop = threading.Event()
        self._fail_once = False
        self._failed_tasks: set[str] = set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                result = self._service.claim_work_item(
                    ClaimTaskWorkItemCommandV1(
                        workspace=self._workspace,
                        stage="pending_exec",
                        worker_id=self._worker_id,
                        worker_role="director",
                        visibility_timeout_seconds=self._visibility_timeout,
                    )
                )
                if result.ok:
                    self._service.acknowledge_task_stage(
                        AcknowledgeTaskStageCommandV1(
                            workspace=self._workspace,
                            task_id=result.task_id,
                            lease_token=result.lease_token,
                            next_stage="pending_qa",
                            summary="Director executed",
                            metadata={"execution_id": "exec-fake-1"},
                        )
                    )
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(self._poll_interval)

    def stop(self) -> None:
        self._stop.set()


class FakeQAConsumer:
    """Claims pending_qa, acks with terminal_status='resolved'."""

    def __init__(
        self,
        workspace: str,
        worker_id: str,
        poll_interval: float = 0.1,
        visibility_timeout_seconds: int = 60,
        **kwargs: object,
    ) -> None:
        self._service = TaskMarketService()
        self._workspace = workspace
        self._worker_id = worker_id
        self._poll_interval = poll_interval
        self._visibility_timeout = visibility_timeout_seconds
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                result = self._service.claim_work_item(
                    ClaimTaskWorkItemCommandV1(
                        workspace=self._workspace,
                        stage="pending_qa",
                        worker_id=self._worker_id,
                        worker_role="qa",
                        visibility_timeout_seconds=self._visibility_timeout,
                    )
                )
                if result.ok:
                    self._service.acknowledge_task_stage(
                        AcknowledgeTaskStageCommandV1(
                            workspace=self._workspace,
                            task_id=result.task_id,
                            lease_token=result.lease_token,
                            terminal_status="resolved",
                            summary="QA verified",
                            metadata={"verdict": "pass"},
                        )
                    )
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(self._poll_interval)

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONSUMER_TYPES = {
    "chief_engineer": FakeCEConsumer,
    "director": FakeDirectorConsumer,
    "qa": FakeQAConsumer,
}


def _wait_for_status(
    service: TaskMarketService,
    workspace: str,
    task_id: str,
    expected_status: str,
    timeout: float = 30.0,
) -> bool:
    """Poll until a task reaches the expected status."""
    from polaris.cells.runtime.task_market.public.contracts import QueryTaskMarketStatusV1

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = service.query_status(QueryTaskMarketStatusV1(workspace=workspace, include_payload=False, limit=500))
        for item in result.items:
            if item.get("task_id") == task_id and item.get("status") == expected_status:
                return True
        time.sleep(0.2)
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_e2e_happy_path(tmp_path) -> None:
    """PM publish → CE → Director → QA → resolved through daemon threads."""
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    # Publish task at pending_design.
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=workspace,
            trace_id="trace-e2e-1",
            run_id="run-e2e-1",
            task_id="task-e2e-1",
            stage="pending_design",
            source_role="pm",
            payload={"title": "E2E happy path"},
        )
    )

    # Start consumer loop with fake consumers.
    manager = ConsumerLoopManager(
        workspace,
        poll_interval=0.1,
    )
    manager.start(consumer_types=_CONSUMER_TYPES, service=service)

    try:
        reached = _wait_for_status(service, workspace, "task-e2e-1", "resolved", timeout=15.0)
        assert reached, "Task did not reach 'resolved' status in time"
    finally:
        manager.stop()


def test_e2e_dead_letter_path(tmp_path) -> None:
    """Task with max_attempts=1 should dead-letter after the first consumer claim+fail."""

    class FailOnceCEConsumer:
        """Claims pending_design then fails with to_dead_letter=True."""

        def __init__(
            self,
            workspace: str,
            worker_id: str,
            poll_interval: float = 0.1,
            visibility_timeout_seconds: int = 60,
            **kwargs: object,
        ) -> None:
            self._service = TaskMarketService()
            self._workspace = workspace
            self._worker_id = worker_id
            self._poll_interval = poll_interval
            self._visibility_timeout = visibility_timeout_seconds
            self._stop = threading.Event()

        def run(self) -> None:
            while not self._stop.is_set():
                try:
                    result = self._service.claim_work_item(
                        ClaimTaskWorkItemCommandV1(
                            workspace=self._workspace,
                            stage="pending_design",
                            worker_id=self._worker_id,
                            worker_role="chief_engineer",
                            visibility_timeout_seconds=self._visibility_timeout,
                        )
                    )
                    if result.ok:
                        self._service.fail_task_stage(
                            FailTaskStageCommandV1(
                                workspace=self._workspace,
                                task_id=result.task_id,
                                lease_token=result.lease_token,
                                error_code="deliberate_failure",
                                error_message="intentional fail for test",
                                to_dead_letter=True,
                            )
                        )
                except Exception:  # noqa: BLE001
                    pass
                self._stop.wait(self._poll_interval)

        def stop(self) -> None:
            self._stop.set()

    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=workspace,
            trace_id="trace-dl-1",
            run_id="run-dl-1",
            task_id="task-dl-1",
            stage="pending_design",
            source_role="pm",
            payload={"title": "dead letter test"},
            max_attempts=1,
        )
    )

    consumer_types = {
        "chief_engineer": FailOnceCEConsumer,
        "director": FakeDirectorConsumer,
        "qa": FakeQAConsumer,
    }
    manager = ConsumerLoopManager(workspace, poll_interval=0.1)
    manager.start(consumer_types=consumer_types, service=service)

    try:
        reached = _wait_for_status(service, workspace, "task-dl-1", "dead_letter", timeout=15.0)
        assert reached, "Task did not reach 'dead_letter' status in time"
    finally:
        manager.stop()


def test_e2e_human_review_escalation_and_resolve(tmp_path) -> None:
    """QA escalates to human review → director resolves → task continues."""

    class EscalateQAConsumer:
        """Claims pending_qa and escalates to human review instead of resolving."""

        def __init__(
            self,
            workspace: str,
            worker_id: str,
            poll_interval: float = 0.1,
            visibility_timeout_seconds: int = 60,
            **kwargs: object,
        ) -> None:
            self._service = TaskMarketService()
            self._workspace = workspace
            self._worker_id = worker_id
            self._poll_interval = poll_interval
            self._visibility_timeout = visibility_timeout_seconds
            self._stop = threading.Event()

        def run(self) -> None:
            while not self._stop.is_set():
                try:
                    result = self._service.claim_work_item(
                        ClaimTaskWorkItemCommandV1(
                            workspace=self._workspace,
                            stage="pending_qa",
                            worker_id=self._worker_id,
                            worker_role="qa",
                            visibility_timeout_seconds=self._visibility_timeout,
                        )
                    )
                    if result.ok:
                        self._service.acknowledge_task_stage(
                            AcknowledgeTaskStageCommandV1(
                                workspace=self._workspace,
                                task_id=result.task_id,
                                lease_token=result.lease_token,
                                next_stage="waiting_human",
                                summary="QA requests human review",
                                metadata={"escalate_to_human_review": True},
                            )
                        )
                except Exception:  # noqa: BLE001
                    pass
                self._stop.wait(self._poll_interval)

        def stop(self) -> None:
            self._stop.set()

    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    # Publish task.
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=workspace,
            trace_id="trace-hr-1",
            run_id="run-hr-1",
            task_id="task-hr-1",
            stage="pending_design",
            source_role="pm",
            payload={"title": "human review test"},
        )
    )

    consumer_types = {
        "chief_engineer": FakeCEConsumer,
        "director": FakeDirectorConsumer,
        "qa": EscalateQAConsumer,
    }
    manager = ConsumerLoopManager(workspace, poll_interval=0.1)
    manager.start(consumer_types=consumer_types, service=service)

    try:
        # Wait for task to reach waiting_human.
        reached = _wait_for_status(service, workspace, "task-hr-1", "waiting_human", timeout=15.0)
        assert reached, "Task did not reach 'waiting_human' status in time"

        # Resolve the human review.
        service.resolve_human_review(
            ResolveHumanReviewCommandV1(
                workspace=workspace,
                task_id="task-hr-1",
                resolution="force_resolve",
                resolved_by="director:bot-1",
                note="human review resolved by director",
            )
        )

        # Verify task is now resolved.
        reached_final = _wait_for_status(service, workspace, "task-hr-1", "resolved", timeout=5.0)
        assert reached_final, "Task did not reach 'resolved' after human review resolution"
    finally:
        manager.stop()
