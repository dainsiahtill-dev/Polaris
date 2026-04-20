"""Performance benchmarks for high-concurrency claim/ack scenarios."""

from __future__ import annotations

import threading
import time

from polaris.cells.runtime.task_market.internal.service import TaskMarketService
from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    PublishTaskWorkItemCommandV1,
    QueryTaskMarketStatusV1,
)


def _publish_batch(
    service: TaskMarketService,
    workspace: str,
    count: int,
    *,
    prefix: str = "task",
    stage: str = "pending_design",
    plan_id: str = "",
    plan_revision_id: str = "",
) -> list[str]:
    """Publish a batch of work items, return task IDs."""
    task_ids: list[str] = []
    for i in range(count):
        tid = f"{prefix}-{i}"
        service.publish_work_item(
            PublishTaskWorkItemCommandV1(
                workspace=workspace,
                trace_id=f"trace-{tid}",
                run_id="run-bench",
                task_id=tid,
                stage=stage,
                source_role="pm",
                plan_id=plan_id,
                plan_revision_id=plan_revision_id,
                payload={"idx": i},
            )
        )
        task_ids.append(tid)
    return task_ids


def test_publish_throughput(tmp_path) -> None:
    """Benchmark: publish 100 work items and measure throughput."""
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    count = 100
    t0 = time.monotonic()
    task_ids = _publish_batch(service, workspace, count)
    elapsed = time.monotonic() - t0

    assert len(task_ids) == count
    ops_per_sec = count / elapsed if elapsed > 0 else float("inf")
    # Basic sanity: should handle at least 30 publishes/sec on any machine.
    assert ops_per_sec >= 30, f"Publish throughput too low: {ops_per_sec:.1f} ops/sec"


def test_claim_throughput(tmp_path) -> None:
    """Benchmark: sequential claim of 50 work items."""
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    _publish_batch(service, workspace, 50)

    count = 50
    claimed = 0
    t0 = time.monotonic()
    for _ in range(count):
        result = service.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=workspace,
                stage="pending_design",
                worker_id="bench-worker",
                worker_role="chief_engineer",
                visibility_timeout_seconds=60,
            )
        )
        if result.ok:
            claimed += 1
    elapsed = time.monotonic() - t0

    assert claimed == count
    ops_per_sec = count / elapsed if elapsed > 0 else float("inf")
    assert ops_per_sec >= 30, f"Claim throughput too low: {ops_per_sec:.1f} ops/sec"


def test_acknowledge_throughput(tmp_path) -> None:
    """Benchmark: sequential acknowledge of 50 claimed items."""
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    _publish_batch(service, workspace, 50)

    # Claim all first.
    lease_tokens: dict[str, str] = {}
    for _ in range(50):
        result = service.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=workspace,
                stage="pending_design",
                worker_id="bench-worker",
                worker_role="chief_engineer",
                visibility_timeout_seconds=60,
            )
        )
        if result.ok:
            lease_tokens[result.task_id] = result.lease_token

    # Benchmark ack.
    t0 = time.monotonic()
    for tid, lease in lease_tokens.items():
        service.acknowledge_task_stage(
            AcknowledgeTaskStageCommandV1(
                workspace=workspace,
                task_id=tid,
                lease_token=lease,
                next_stage="pending_exec",
                summary="bench ack",
            )
        )
    elapsed = time.monotonic() - t0

    ops_per_sec = len(lease_tokens) / elapsed if elapsed > 0 else float("inf")
    assert ops_per_sec >= 30, f"Ack throughput too low: {ops_per_sec:.1f} ops/sec"


def test_concurrent_claims_no_double_claim(tmp_path) -> None:
    """Multiple threads claiming from the same pool — no double claims."""
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    _publish_batch(service, workspace, 20)

    claimed_tasks: dict[str, str] = {}  # task_id -> worker_id
    lock = threading.Lock()
    barrier = threading.Barrier(4)
    errors: list[str] = []

    def worker(worker_id: str) -> None:
        barrier.wait(timeout=5.0)
        while True:
            result = service.claim_work_item(
                ClaimTaskWorkItemCommandV1(
                    workspace=workspace,
                    stage="pending_design",
                    worker_id=worker_id,
                    worker_role="chief_engineer",
                    visibility_timeout_seconds=60,
                )
            )
            if not result.ok:
                break
            with lock:
                if result.task_id in claimed_tasks:
                    errors.append(
                        f"Double claim: task={result.task_id} by {worker_id} and {claimed_tasks[result.task_id]}"
                    )
                claimed_tasks[result.task_id] = worker_id

    threads = [threading.Thread(target=worker, args=(f"worker-{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30.0)

    assert not errors, f"Double claims detected: {errors}"
    assert len(claimed_tasks) == 20


def test_concurrent_publish_and_claim(tmp_path) -> None:
    """Concurrent publishers and claimers operating simultaneously."""
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    publish_count = 50
    claimed_ids: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def publisher() -> None:
        barrier.wait(timeout=5.0)
        for i in range(publish_count):
            service.publish_work_item(
                PublishTaskWorkItemCommandV1(
                    workspace=workspace,
                    trace_id=f"trace-cp-{i}",
                    run_id="run-concurrent",
                    task_id=f"cp-task-{i}",
                    stage="pending_design",
                    source_role="pm",
                    payload={"idx": i},
                )
            )

    def claimer() -> None:
        barrier.wait(timeout=5.0)
        time.sleep(0.1)  # Give publisher a head start.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            result = service.claim_work_item(
                ClaimTaskWorkItemCommandV1(
                    workspace=workspace,
                    stage="pending_design",
                    worker_id="claimer-1",
                    worker_role="chief_engineer",
                    visibility_timeout_seconds=60,
                )
            )
            if result.ok:
                with lock:
                    claimed_ids.append(result.task_id)
            else:
                # Check if all items published and claimed.
                with lock:
                    if len(claimed_ids) >= publish_count:
                        break
                time.sleep(0.01)

    pub_thread = threading.Thread(target=publisher)
    claim_thread = threading.Thread(target=claimer)

    t0 = time.monotonic()
    pub_thread.start()
    claim_thread.start()
    pub_thread.join(timeout=15.0)
    claim_thread.join(timeout=15.0)
    elapsed = time.monotonic() - t0

    # All items should eventually be claimed.
    assert len(claimed_ids) == publish_count, (
        f"Expected {publish_count} claims, got {len(claimed_ids)} in {elapsed:.2f}s"
    )


def test_full_lifecycle_throughput(tmp_path) -> None:
    """Benchmark: publish → claim → ack → claim → ack → resolve for 20 items."""
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    count = 20
    _publish_batch(service, workspace, count)

    t0 = time.monotonic()

    # Stage 1: claim pending_design → ack to pending_exec.
    for _ in range(count):
        result = service.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=workspace,
                stage="pending_design",
                worker_id="ce-bench",
                worker_role="chief_engineer",
                visibility_timeout_seconds=60,
            )
        )
        assert result.ok
        service.acknowledge_task_stage(
            AcknowledgeTaskStageCommandV1(
                workspace=workspace,
                task_id=result.task_id,
                lease_token=result.lease_token,
                next_stage="pending_exec",
                summary="design done",
            )
        )

    # Stage 2: claim pending_exec → ack to pending_qa.
    for _ in range(count):
        result = service.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=workspace,
                stage="pending_exec",
                worker_id="dir-bench",
                worker_role="director",
                visibility_timeout_seconds=60,
            )
        )
        assert result.ok
        service.acknowledge_task_stage(
            AcknowledgeTaskStageCommandV1(
                workspace=workspace,
                task_id=result.task_id,
                lease_token=result.lease_token,
                next_stage="pending_qa",
                summary="exec done",
            )
        )

    # Stage 3: claim pending_qa → resolve.
    for _ in range(count):
        result = service.claim_work_item(
            ClaimTaskWorkItemCommandV1(
                workspace=workspace,
                stage="pending_qa",
                worker_id="qa-bench",
                worker_role="qa",
                visibility_timeout_seconds=60,
            )
        )
        assert result.ok
        service.acknowledge_task_stage(
            AcknowledgeTaskStageCommandV1(
                workspace=workspace,
                task_id=result.task_id,
                lease_token=result.lease_token,
                terminal_status="resolved",
                summary="qa passed",
            )
        )

    elapsed = time.monotonic() - t0

    # Verify all resolved.
    status = service.query_status(QueryTaskMarketStatusV1(workspace=workspace))
    resolved_count = sum(1 for item in status.items if item["status"] == "resolved")
    assert resolved_count == count

    # 60 total operations (20 x 3 stages, each = claim + ack = 2 ops = 120 ops).
    total_ops = count * 6  # 3 stages x 2 ops per stage
    ops_per_sec = total_ops / elapsed if elapsed > 0 else float("inf")
    assert ops_per_sec >= 20, f"Full lifecycle throughput too low: {ops_per_sec:.1f} ops/sec"


def test_query_status_performance(tmp_path) -> None:
    """Benchmark: query_status with 200 items."""
    service = TaskMarketService()
    workspace = str(tmp_path / "ws")

    _publish_batch(service, workspace, 200)

    iterations = 20
    t0 = time.monotonic()
    for _ in range(iterations):
        status = service.query_status(QueryTaskMarketStatusV1(workspace=workspace))
        assert status.total == 200
    elapsed = time.monotonic() - t0

    avg_ms = (elapsed / iterations) * 1000
    # Should average under 200ms per query.
    assert avg_ms < 200, f"query_status too slow: {avg_ms:.1f}ms avg"
