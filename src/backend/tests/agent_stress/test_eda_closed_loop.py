"""EDA closed-loop integration tests: PM → CE → Director → QA.

These tests verify the complete TaskMarket EDA pipeline with real
TaskMarketService, in-memory JSON store, and mock consumers simulating
the four role workers.

Pipeline stages:
  PENDING_DESIGN  (CE polls here)
      ↓ ack(blueprint_id, ...)
  PENDING_EXEC    (Director polls here)
      ↓ ack(target_files, scope, ...)
  PENDING_QA      (QA polls here)
      ↓ ack(terminal_status=resolved|rejected)
  terminal (resolved / rejected / dead_letter)
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1,
    MoveTaskToDeadLetterCommandV1,
    PublishTaskWorkItemCommandV1,
    QueryTaskMarketStatusV1,
    RenewTaskLeaseCommandV1,
)
from polaris.cells.runtime.task_market.public.service import TaskMarketService

from .conftest_eda import (
    MockCEConsumer,
    MockDirectorConsumer,
    MockQAConsumer,
    get_task_status,
    make_pm_payload,
    publish_design_task,
    publish_exec_task,
)

# =============================================================================
# T0: Sanity — single item lifecycle
# =============================================================================


def test_publish_to_pending_design_creates_item(tmp_path: Path) -> None:
    """Verify publish_work_item creates a task in pending_design stage."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    result = publish_design_task(
        svc=svc,
        workspace=workspace,
        task_id="sanity-1",
        trace_id="trace-sanity",
        run_id="run-sanity",
        run_dir=str(tmp_path / "runs"),
        cache_root=str(tmp_path / "cache"),
    )

    assert result.ok is True
    assert result.status == "pending_design"

    # Query the item directly from store
    item = get_task_status(svc, workspace, "sanity-1")
    assert item.get("task_id") == "sanity-1"
    assert item.get("stage") == "pending_design"
    assert item.get("status") == "pending_design"


def test_publish_to_pending_exec_creates_item(tmp_path: Path) -> None:
    """Verify publish_work_item creates a task in pending_exec stage (mainline mode)."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    result = publish_exec_task(
        svc=svc,
        workspace=workspace,
        task_id="mainline-1",
        trace_id="trace-mainline",
        run_id="run-mainline",
        run_dir=str(tmp_path / "runs"),
        cache_root=str(tmp_path / "cache"),
    )

    assert result.ok is True
    assert result.status == "pending_exec"


# =============================================================================
# T1: CE stage — claim pending_design, ack pending_exec
# =============================================================================


def test_ce_claim_from_pending_design_succeeds(tmp_path: Path) -> None:
    """CE can claim a pending_design task that has required runtime context."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    # PM publishes to pending_design with runtime context
    publish_design_task(
        svc=svc,
        workspace=workspace,
        task_id="t-ce-claim-1",
        trace_id="trace-ce",
        run_id="run-ce",
        run_dir=str(tmp_path / "runs"),
        cache_root=str(tmp_path / "cache"),
    )

    # CE consumer claims
    ce = MockCEConsumer(workspace=str(workspace), worker_id="ce-1")
    results = ce.poll_once()

    assert len(results) == 1
    assert results[0]["ok"] is True
    assert results[0]["next_stage"] == "pending_exec"

    # Verify item is now in pending_exec
    item = get_task_status(svc, workspace, "t-ce-claim-1")
    assert item.get("stage") == "pending_exec"


def test_ce_claim_from_pending_design_fails_without_runtime_context(tmp_path: Path) -> None:
    """CE consumer requeues when PM payload is missing run_dir or cache_root (B1 regression)."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    # PM publishes WITHOUT required runtime context fields
    svc.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-no-ctx",
            run_id="run-no-ctx",
            task_id="t-no-ctx-1",
            stage="pending_design",
            source_role="pm",
            payload=make_pm_payload(
                task_id="t-no-ctx-1",
                title="Task without context",
                # Deliberately omit: run_dir, cache_root
            ),
            priority="high",
            max_attempts=3,
        )
    )

    # CE should fail/requeue the task
    ce = MockCEConsumer(workspace=str(workspace), worker_id="ce-1")
    results = ce.poll_once()

    assert len(results) == 1
    assert results[0]["ok"] is False
    assert "runtime_context" in results[0]["reason"]


def test_ce_ack_metadata_appears_in_payload_after_ack(tmp_path: Path) -> None:
    """After CE acks, the item.payload must contain blueprint_id etc. for Director (B2 regression)."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    publish_design_task(
        svc=svc,
        workspace=workspace,
        task_id="t-ce-meta-1",
        run_dir=str(tmp_path / "runs"),
        cache_root=str(tmp_path / "cache"),
    )

    ce = MockCEConsumer(workspace=str(workspace), worker_id="ce-1")
    ce.poll_once()

    # Verify Director can see CE-generated fields in payload
    item = get_task_status(svc, workspace, "t-ce-meta-1")
    assert item.get("payload", {}).get("blueprint_id") == "bp-t-ce-meta-1"
    assert item.get("payload", {}).get("ce_generated") is True
    assert "guardrails" in item.get("payload", {})


# =============================================================================
# T2: Director stage — claim pending_exec, ack pending_qa
# =============================================================================


def test_director_claim_from_pending_exec_succeeds(tmp_path: Path) -> None:
    """Director can claim a pending_exec task whose payload has blueprint_id from CE."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    # Simulate full pipeline: PM → CE
    publish_design_task(
        svc=svc,
        workspace=workspace,
        task_id="t-dir-1",
        run_dir=str(tmp_path / "runs"),
        cache_root=str(tmp_path / "cache"),
    )
    MockCEConsumer(workspace=str(workspace)).poll_once()

    # Director claims from pending_exec
    director = MockDirectorConsumer(workspace=str(workspace), worker_id="dir-1")
    results = director.poll_once()

    assert len(results) == 1
    assert results[0]["ok"] is True
    assert results[0]["next_stage"] == "pending_qa"

    # Verify item is now in pending_qa
    item = get_task_status(svc, workspace, "t-dir-1")
    assert item.get("stage") == "pending_qa"


def test_director_acks_with_scope_paths_for_qa(tmp_path: Path) -> None:
    """Director's ack metadata must include scope_paths so QA knows what to audit."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    # PM → CE
    publish_design_task(
        svc=svc,
        workspace=workspace,
        task_id="t-dir-scope-1",
        run_dir=str(tmp_path / "runs"),
        cache_root=str(tmp_path / "cache"),
    )
    MockCEConsumer(workspace=str(workspace)).poll_once()

    # Director executes
    director = MockDirectorConsumer(workspace=str(workspace), worker_id="dir-1")
    director.poll_once()

    # Verify Director's execution_metadata is in payload for QA
    item = get_task_status(svc, workspace, "t-dir-scope-1")
    assert item.get("payload", {}).get("director_generated") is True
    assert "target_files" in item.get("payload", {})
    assert "scope" in item.get("payload", {})


# =============================================================================
# T3: QA stage — claim pending_qa, resolve / reject
# =============================================================================


def test_qa_resolves_task_with_complete_metadata(tmp_path: Path) -> None:
    """QA resolves a task when CE blueprint_id and Director execution_summary are present."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    # Full pipeline: PM → CE → Director
    publish_design_task(
        svc=svc,
        workspace=workspace,
        task_id="t-qa-resolve-1",
        run_dir=str(tmp_path / "runs"),
        cache_root=str(tmp_path / "cache"),
    )
    MockCEConsumer(workspace=str(workspace)).poll_once()
    MockDirectorConsumer(workspace=str(workspace)).poll_once()

    # QA resolves
    qa = MockQAConsumer(workspace=str(workspace), worker_id="qa-1")
    results = qa.poll_once()

    assert len(results) == 1
    assert results[0]["ok"] is True
    assert results[0]["verdict"] == "PASS"
    assert results[0]["status"] == "resolved"

    # Verify item is terminal resolved
    item = get_task_status(svc, workspace, "t-qa-resolve-1")
    assert item.get("status") == "resolved"


def test_qa_rejects_task_missing_blueprint_id(tmp_path: Path) -> None:
    """QA rejects a task that reached pending_qa without blueprint_id (CE bypassed/failed)."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    # PM publishes directly to pending_exec WITHOUT going through CE (mainline bypass)
    svc.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-no-ce",
            run_id="run-no-ce",
            task_id="t-no-ce-1",
            stage="pending_exec",
            source_role="pm",
            payload=make_pm_payload(
                task_id="t-no-ce-1",
                run_dir=str(tmp_path / "runs"),
                cache_root=str(tmp_path / "cache"),
            ),
            priority="high",
            max_attempts=2,
        )
    )

    # Director processes (finds no blueprint_id, dead-letters)
    director = MockDirectorConsumer(workspace=str(workspace), worker_id="dir-1")
    results = director.poll_once()

    assert results[0]["ok"] is False
    assert results[0]["reason"] == "missing_blueprint"

    # Task should be in dead_letter
    item = get_task_status(svc, workspace, "t-no-ce-1")
    assert item.get("status") == "dead_letter"


# =============================================================================
# T4: Full closed-loop — PM → CE → Director → QA → RESOLVED
# =============================================================================


def test_full_pipeline_pm_to_qa_resolved(tmp_path: Path) -> None:
    """Complete single-round closed loop: PM publishes → CE designs → Director executes → QA resolves."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    task_id = "t-full-loop-1"

    # Step 1: PM publishes to pending_design
    pm_result = svc.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-full",
            run_id="run-full",
            task_id=task_id,
            stage="pending_design",
            source_role="pm",
            payload=make_pm_payload(
                task_id=task_id,
                workspace=str(workspace),
                run_dir=str(tmp_path / "runs"),
                cache_root=str(tmp_path / "cache"),
                run_id="run-full",
            ),
            priority="high",
            max_attempts=3,
        )
    )
    assert pm_result.ok is True
    assert pm_result.status == "pending_design"

    # Step 2: CE consumer polls and advances to pending_exec
    ce = MockCEConsumer(workspace=str(workspace), worker_id="ce-full")
    ce_results = ce.poll_once()
    assert len(ce_results) == 1
    assert ce_results[0]["ok"] is True

    # Step 3: Director consumer polls and advances to pending_qa
    director = MockDirectorConsumer(workspace=str(workspace), worker_id="dir-full")
    dir_results = director.poll_once()
    assert len(dir_results) == 1
    assert dir_results[0]["ok"] is True
    assert dir_results[0]["next_stage"] == "pending_qa"

    # Step 4: QA consumer polls and resolves
    qa = MockQAConsumer(workspace=str(workspace), worker_id="qa-full")
    qa_results = qa.poll_once()
    assert len(qa_results) == 1
    assert qa_results[0]["ok"] is True
    assert qa_results[0]["verdict"] == "PASS"
    assert qa_results[0]["status"] == "resolved"

    # Verify terminal state
    item = get_task_status(svc, workspace, task_id)
    assert item.get("status") == "resolved"
    assert item.get("stage") == "pending_qa"  # stage doesn't advance on terminal

    # Verify full payload chain
    payload = item.get("payload", {})
    assert payload.get("blueprint_id") == f"bp-{task_id}"
    assert payload.get("director_generated") is True
    assert payload.get("ce_generated") is True
    assert payload.get("verdict") == "PASS"


def test_pipeline_payload_metadata_propagation(tmp_path: Path) -> None:
    """Payload metadata set by CE must survive through Director ack to reach QA (B2 regression)."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    task_id = "t-meta-propagate"

    # PM → CE: CE sets blueprint_id, guardrails, scope_paths
    publish_design_task(
        svc=svc,
        workspace=workspace,
        task_id=task_id,
        run_dir=str(tmp_path / "runs"),
        cache_root=str(tmp_path / "cache"),
    )
    MockCEConsumer(workspace=str(workspace)).poll_once()

    # Director reads blueprint_id from payload, adds its own metadata, acks to QA
    MockDirectorConsumer(workspace=str(workspace)).poll_once()

    # QA reads both CE and Director metadata from the same payload
    MockQAConsumer(workspace=str(workspace)).poll_once()

    # Verify the full merge chain
    item = get_task_status(svc, workspace, task_id)
    payload = item.get("payload", {})

    # CE fields survived through Director ack
    assert payload.get("blueprint_id") == f"bp-{task_id}"
    assert payload.get("guardrails") == ["no_delete", "preserve_tests"]
    assert payload.get("no_touch_zones") == [".git", "node_modules"]
    assert payload.get("scope_paths") == ["src/login.py"]

    # Director fields also present
    assert payload.get("director_generated") is True
    assert payload.get("target_files") == ["src/login.py", "tests/test_login.py"]


# =============================================================================
# T5: Lease lifecycle — visibility timeout, renew, stale token
# =============================================================================


def test_visibility_timeout_allows_reclaim(tmp_path: Path) -> None:
    """After visibility_timeout expires, another worker can claim the same task."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    # PM publishes to pending_exec
    publish_exec_task(
        svc=svc,
        workspace=workspace,
        task_id="t-vis-1",
        run_dir=str(tmp_path / "runs"),
        cache_root=str(tmp_path / "cache"),
    )

    # Director-1 claims with 1-second timeout
    claim1 = svc.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-1",
            worker_role="director",
            visibility_timeout_seconds=1,
        )
    )
    assert claim1.ok is True
    assert claim1.lease_token

    # Wait for lease to expire
    time.sleep(1.5)

    # Director-2 can now claim the same task (different lease_token)
    claim2 = svc.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-2",
            worker_role="director",
            visibility_timeout_seconds=60,
        )
    )
    assert claim2.ok is True
    assert claim2.lease_token != claim1.lease_token


def test_renew_lease_extends_expiry(tmp_path: Path) -> None:
    """Worker can renew its own lease before it expires, extending the visibility window."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    publish_exec_task(
        svc=svc,
        workspace=workspace,
        task_id="t-renew-1",
        run_dir=str(tmp_path / "runs"),
        cache_root=str(tmp_path / "cache"),
    )

    # Claim with short timeout
    claim = svc.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-renew",
            worker_role="director",
            visibility_timeout_seconds=60,
        )
    )
    assert claim.ok is True
    lease_token = claim.lease_token

    # Renew with longer timeout
    renewed = svc.renew_task_lease(
        RenewTaskLeaseCommandV1(
            workspace=str(workspace),
            task_id="t-renew-1",
            lease_token=lease_token,
            visibility_timeout_seconds=300,
        )
    )
    assert renewed.ok is True
    # lease_expires_at must be an ISO UTC string (A2 regression check)
    assert "T" in renewed.lease_expires_at
    assert "+00:00" in renewed.lease_expires_at or "Z" in renewed.lease_expires_at


def test_stale_lease_token_raises_error(tmp_path: Path) -> None:
    """Using an expired or wrong lease_token raises StaleLeaseTokenError (A4 regression)."""
    from polaris.cells.runtime.task_market.internal.errors import StaleLeaseTokenError

    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    publish_exec_task(
        svc=svc,
        workspace=workspace,
        task_id="t-stale-1",
        run_dir=str(tmp_path / "runs"),
        cache_root=str(tmp_path / "cache"),
    )

    # Claim with short timeout
    claim = svc.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-stale",
            worker_role="director",
            visibility_timeout_seconds=1,
        )
    )
    assert claim.ok is True

    # Wait for expiry
    time.sleep(1.5)

    # Try to acknowledge with expired token — must raise StaleLeaseTokenError
    with pytest.raises(StaleLeaseTokenError):
        svc.acknowledge_task_stage(
            AcknowledgeTaskStageCommandV1(
                workspace=str(workspace),
                task_id="t-stale-1",
                lease_token=claim.lease_token,
                next_stage="pending_qa",
                summary="Should fail",
            )
        )


# =============================================================================
# T6: Failure and requeue — director failure, max_attempts, dead letter
# =============================================================================


def test_director_failure_requeues_to_pending_exec(tmp_path: Path) -> None:
    """When Director fails without to_dead_letter, task requeues to pending_exec for retry."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    # PM publishes to pending_exec with blueprint_id (skip CE design phase)
    svc.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-requeue",
            run_id="run-requeue",
            task_id="t-requeue-1",
            stage="pending_exec",
            source_role="pm",
            payload={
                "task_id": "t-requeue-1",
                "workspace": str(workspace),
                "run_dir": str(tmp_path / "runs"),
                "cache_root": str(tmp_path / "cache"),
                "run_id": "run-requeue",
                "pm_iteration": 1,
                "blueprint_id": "bp-requeue-1",
            },
            priority="high",
            max_attempts=2,
        )
    )

    # Claim via service (simulates Director claiming)
    claim = svc.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="dir-req-1",
            worker_role="director",
            visibility_timeout_seconds=60,
        )
    )
    assert claim.ok is True
    lease_token = claim.lease_token

    # Simulate execution failure — requeue to pending_exec (MUST fail before ack)
    svc.fail_task_stage(
        FailTaskStageCommandV1(
            workspace=str(workspace),
            task_id="t-requeue-1",
            lease_token=lease_token,
            error_code="EXEC_FAILED",
            error_message="Simulated exec failure",
            requeue_stage="pending_exec",
        )
    )

    # Verify item is back in pending_exec
    item_after = get_task_status(svc, workspace, "t-requeue-1")
    assert item_after.get("stage") == "pending_exec"


def test_dead_letter_after_max_attempts(tmp_path: Path) -> None:
    """After max_attempts is exhausted on claim, task moves to dead_letter.

    This tests the retry_exhausted_on_claim path inside claim_work_item.
    The task must be in pending_exec status (not yet acknowledged) for the
    retry-exhausted claim to select it as a candidate.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    # Publish with max_attempts=1 — will exhaust on SECOND claim attempt
    svc.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-dlq",
            run_id="run-dlq",
            task_id="t-dlq-1",
            stage="pending_exec",
            source_role="pm",
            payload=make_pm_payload(
                task_id="t-dlq-1",
                run_dir=str(tmp_path / "runs"),
                cache_root=str(tmp_path / "cache"),
                extra={"blueprint_id": "bp-dlq-1"},
            ),
            priority="high",
            max_attempts=1,
        )
    )

    # First claim — succeeds (attempts=0 < max_attempts=1)
    claim1 = svc.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="dir-dlq-1",
            worker_role="director",
            visibility_timeout_seconds=1,  # Short timeout for test
        )
    )
    assert claim1.ok is True
    assert claim1.status == "in_execution"
    # After this claim: attempts=1 (exhausted for max_attempts=1)

    # Lease expires (wait for 1-second visibility timeout)
    time.sleep(1.5)

    # Second claim — fails because attempts=1 >= max_attempts=1
    # The task is visible again (lease expired), but retry is exhausted
    claim2 = svc.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="dir-dlq-2",
            worker_role="director",
            visibility_timeout_seconds=60,
        )
    )
    assert claim2.ok is False
    assert claim2.reason == "retry_exhausted_on_claim"

    # Task should now be in dead_letter
    item = get_task_status(svc, workspace, "t-dlq-1")
    assert item.get("status") == "dead_letter"


def test_move_to_dead_letter_records_transition(tmp_path: Path) -> None:
    """move_task_to_dead_letter must record from_status BEFORE mutation (A3 regression)."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    svc.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-dlq-tr",
            run_id="run-dlq-tr",
            task_id="t-dlq-tr-1",
            stage="pending_exec",
            source_role="pm",
            payload=make_pm_payload(
                task_id="t-dlq-tr-1",
                run_dir=str(tmp_path / "runs"),
                cache_root=str(tmp_path / "cache"),
            ),
            priority="medium",
            max_attempts=2,
        )
    )

    # Move directly to DLQ (no claim needed for this path)
    svc.move_task_to_dead_letter(
        MoveTaskToDeadLetterCommandV1(
            workspace=str(workspace),
            task_id="t-dlq-tr-1",
            reason="unrecoverable_error",
            error_code="FATAL",
        )
    )

    # Verify transition: from_status should be pending_exec (before DLQ mutation)
    from polaris.cells.runtime.task_market.internal import store as store_module

    real_store = store_module.get_store(str(workspace))
    transitions = real_store.load_transitions("t-dlq-tr-1")
    last = transitions[-1]
    assert last["from_status"] == "pending_exec"
    assert last["to_status"] == "dead_letter"


# =============================================================================
# T7: Query status and counts
# =============================================================================


def test_query_status_returns_correct_counts(tmp_path: Path) -> None:
    """query_status must return accurate counts per status across all pipeline stages."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    # Publish tasks in different stages
    publish_design_task(svc, workspace, task_id="q-design-1", run_dir=str(tmp_path / "runs"), cache_root=str(tmp_path / "cache"))
    publish_design_task(svc, workspace, task_id="q-design-2", run_dir=str(tmp_path / "runs"), cache_root=str(tmp_path / "cache"))
    publish_exec_task(svc, workspace, task_id="q-exec-1", run_dir=str(tmp_path / "runs"), cache_root=str(tmp_path / "cache"))
    publish_exec_task(svc, workspace, task_id="q-exec-2", run_dir=str(tmp_path / "runs"), cache_root=str(tmp_path / "cache"))
    publish_exec_task(svc, workspace, task_id="q-exec-3", run_dir=str(tmp_path / "runs"), cache_root=str(tmp_path / "cache"))

    result = svc.query_status(
        QueryTaskMarketStatusV1(workspace=str(workspace), include_payload=False)
    )

    assert result.total == 5
    # pending_design: 2, pending_exec: 3
    assert result.counts.get("pending_design", 0) == 2
    assert result.counts.get("pending_exec", 0) == 3


def test_query_status_with_stage_filter(tmp_path: Path) -> None:
    """query_status with stage=filter must only return items in that stage."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    publish_design_task(svc, workspace, task_id="qs-design-1", run_dir=str(tmp_path / "runs"), cache_root=str(tmp_path / "cache"))
    publish_exec_task(svc, workspace, task_id="qs-exec-1", run_dir=str(tmp_path / "runs"), cache_root=str(tmp_path / "cache"))

    result = svc.query_status(
        QueryTaskMarketStatusV1(workspace=str(workspace), stage="pending_design")
    )
    assert result.total == 1
    assert result.items[0].get("task_id") == "qs-design-1"


# =============================================================================
# T8: Parent-child dependency tracking (fractal structure foundation)
# =============================================================================


def test_parent_child_dependency_tracking(tmp_path: Path) -> None:
    """Parent task published with is_leaf=False and parent_task_id set; child is published separately."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    # Parent task (non-leaf, group=plan-alpha)
    parent_result = svc.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-parent",
            run_id="run-parent",
            task_id="t-parent-1",
            stage="pending_exec",
            source_role="pm",
            payload=make_pm_payload(
                task_id="t-parent-1",
                workspace=str(workspace),
                run_dir=str(tmp_path / "runs"),
                cache_root=str(tmp_path / "cache"),
            ),
            parent_task_id="",
            is_leaf=False,
            max_attempts=3,
        )
    )
    assert parent_result.ok is True

    # Child task with parent reference
    child_result = svc.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-child",
            run_id="run-parent",
            task_id="t-child-1",
            stage="pending_exec",
            source_role="pm",
            payload=make_pm_payload(
                task_id="t-child-1",
                workspace=str(workspace),
                run_dir=str(tmp_path / "runs"),
                cache_root=str(tmp_path / "cache"),
                extra={"parent_id": "t-parent-1"},
            ),
            parent_task_id="t-parent-1",
            is_leaf=True,
            max_attempts=3,
        )
    )
    assert child_result.ok is True

    # Verify parent is marked non-leaf
    parent_item = get_task_status(svc, workspace, "t-parent-1")
    assert parent_item.get("is_leaf") is False

    # Verify child is marked leaf
    child_item = get_task_status(svc, workspace, "t-child-1")
    assert child_item.get("is_leaf") is True
    assert child_item.get("parent_task_id") == "t-parent-1"


def test_is_leaf_field_is_preserved_through_lifecycle(tmp_path: Path) -> None:
    """The is_leaf field is correctly stored and preserved through stage transitions."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    # Publish non-leaf task
    svc.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=str(workspace),
            trace_id="trace-nonleaf",
            run_id="run-nonleaf",
            task_id="t-nonleaf-1",
            stage="pending_exec",
            source_role="pm",
            payload=make_pm_payload(
                task_id="t-nonleaf-1",
                run_dir=str(tmp_path / "runs"),
                cache_root=str(tmp_path / "cache"),
                extra={"blueprint_id": "bp-nonleaf"},
            ),
            is_leaf=False,  # Non-leaf — orchestrator must decompose
            max_attempts=2,
        )
    )

    # Verify is_leaf=False is stored
    parent_item = get_task_status(svc, workspace, "t-nonleaf-1")
    assert parent_item.get("is_leaf") is False

    # Non-leaf tasks CAN currently be claimed (is_leaf enforcement is a future fractal feature)
    claim = svc.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="director-x",
            worker_role="director",
            visibility_timeout_seconds=60,
        )
    )
    assert claim.ok is True  # Currently allowed — fractal is_leaf enforcement is TBD

    # Claim advances to in_execution, is_leaf remains False
    items = svc._get_store(str(workspace)).load_items()
    item = items["t-nonleaf-1"]
    assert item.is_leaf is False
    assert item.status == "in_execution"


# =============================================================================
# T9: Acknowledge stage merges metadata into payload (B2 end-to-end)
# =============================================================================


def test_acknowledge_stage_merges_metadata_into_payload(tmp_path: Path) -> None:
    """AcknowledgeTaskStageCommand.metadata must be merged into item.payload for downstream consumers."""
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    svc = TaskMarketService()

    publish_exec_task(
        svc=svc,
        workspace=workspace,
        task_id="t-ack-merge-1",
        run_dir=str(tmp_path / "runs"),
        cache_root=str(tmp_path / "cache"),
    )

    # Claim
    claim = svc.claim_work_item(
        ClaimTaskWorkItemCommandV1(
            workspace=str(workspace),
            stage="pending_exec",
            worker_id="dir-ack-merge",
            worker_role="director",
            visibility_timeout_seconds=60,
        )
    )
    assert claim.ok is True

    # Ack with custom metadata
    svc.acknowledge_task_stage(
        AcknowledgeTaskStageCommandV1(
            workspace=str(workspace),
            task_id="t-ack-merge-1",
            lease_token=claim.lease_token,
            next_stage="pending_qa",
            summary="Done",
            metadata={
                "scope_paths": ["/src/main.py"],
                "guardrails": ["preserve_fmt"],
                "blueprint_id": "bp-ack-merge",
            },
        )
    )

    # Verify downstream can read merged metadata
    item = get_task_status(svc, workspace, "t-ack-merge-1")
    payload = item.get("payload", {})
    assert payload.get("scope_paths") == ["/src/main.py"]
    assert payload.get("guardrails") == ["preserve_fmt"]
    assert payload.get("blueprint_id") == "bp-ack-merge"
