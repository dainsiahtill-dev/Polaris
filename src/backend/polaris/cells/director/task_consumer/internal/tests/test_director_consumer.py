"""Tests for director_consumer.py."""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

from polaris.cells.director.task_consumer.internal.director_consumer import (
    DirectorExecutionConsumer,
    ScopeConflictDetector,
    UnrecoverableExecutionError,
)


class TestDirectorExecutionConsumerInit:
    def test_valid_construction(self) -> None:
        with patch(
            "polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service"
        ) as mock_get:
            mock_get.return_value = MagicMock()
            consumer = DirectorExecutionConsumer(workspace="/test/workspace", worker_id="dir-1")
            assert consumer._workspace == "/test/workspace"
            assert consumer._worker_id == "dir-1"
            assert consumer._visibility_timeout == 1800
            assert consumer._poll_interval == 5.0
            assert consumer._enable_safe_parallel is False

    def test_custom_params(self) -> None:
        with patch(
            "polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service"
        ) as mock_get:
            mock_get.return_value = MagicMock()
            consumer = DirectorExecutionConsumer(
                workspace="/test",
                worker_id="custom_dir",
                visibility_timeout_seconds=600,
                poll_interval=10.0,
                enable_safe_parallel=True,
            )
            assert consumer._visibility_timeout == 600
            assert consumer._poll_interval == 10.0
            assert consumer._enable_safe_parallel is True
            assert consumer._lease_renew_interval_seconds == 60.0

    def test_stop_event_initial_state(self) -> None:
        with patch(
            "polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service"
        ) as mock_get:
            mock_get.return_value = MagicMock()
            consumer = DirectorExecutionConsumer(workspace="/test", worker_id="d1")
            assert consumer._stop_event is not None
            assert not consumer._stop_event.is_set()


class TestDirectorExecutionConsumerPollOnce:
    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_no_claimable_tasks_returns_empty_list(self, mock_get_svc: MagicMock) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc
        mock_result = MagicMock()
        mock_result.ok = False
        mock_result.task_id = ""
        mock_result.lease_token = ""
        mock_svc.claim_work_item.return_value = mock_result

        consumer = DirectorExecutionConsumer(workspace="/test", worker_id="d1")
        results = consumer.poll_once()
        assert results == []

    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_successful_execution_acks_pending_qa(self, mock_get_svc: MagicMock) -> None:
        """Verify director claims, executes, and advances to PENDING_QA."""
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        # Claim returns a task with blueprint_id
        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-exec-1"
        claim_result.lease_token = "lease-xyz"
        claim_result.payload = {
            "blueprint_id": "bp-001",
            "scope_paths": ["/src/main.py"],
        }

        ack_result = MagicMock()
        ack_result.ok = True
        ack_result.status = "pending_qa"

        # First call returns the claim, second call returns ok=False to break loop
        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.acknowledge_task_stage.return_value = ack_result

        consumer = DirectorExecutionConsumer(workspace="/test", worker_id="d1")
        with patch.object(
            consumer,
            "_execute_task",
            return_value={"changed_files": ["src/main.py"], "duration": 1, "side_effects": []},
        ):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["task_id"] == "task-exec-1"
        assert results[0]["ok"] is True
        assert results[0]["status"] == "pending_qa"

        # Verify ack was called with correct next_stage=pending_qa
        ack_call = mock_svc.acknowledge_task_stage.call_args[0][0]
        assert ack_call.next_stage == "pending_qa"
        assert ack_call.metadata["changed_files"] == ["src/main.py"]
        assert ack_call.metadata["director_evidence_status"] == "changed_files_reported"

    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_no_execution_evidence_requeues_pending_exec(self, mock_get_svc: MagicMock) -> None:
        """Placeholder/no-evidence execution must not advance to QA."""
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-no-evidence"
        claim_result.lease_token = "lease-no-evidence"
        claim_result.payload = {
            "blueprint_id": "bp-no-evidence",
            "target_files": ["src/main.py"],
            "scope_paths": ["src"],
        }

        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.fail_task_stage.return_value = MagicMock(ok=True, status="pending_exec")

        consumer = DirectorExecutionConsumer(workspace="/test", worker_id="d1")
        results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["task_id"] == "task-no-evidence"
        assert results[0]["ok"] is False
        assert results[0]["reason"] == "missing_execution_evidence"
        mock_svc.acknowledge_task_stage.assert_not_called()

        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.error_code == "EXEC_NO_EVIDENCE"
        assert fail_call.requeue_stage == "pending_exec"
        assert fail_call.metadata["target_files"] == ["src/main.py"]

    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_successful_execution_registers_and_commits_compensation_actions(self, mock_get_svc: MagicMock) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-exec-saga"
        claim_result.lease_token = "lease-saga"
        claim_result.payload = {"blueprint_id": "bp-saga"}

        ack_result = MagicMock()
        ack_result.ok = True
        ack_result.status = "pending_qa"

        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.acknowledge_task_stage.return_value = ack_result

        consumer = DirectorExecutionConsumer(workspace="/test", worker_id="d1")
        with patch.object(
            consumer,
            "_execute_task",
            return_value={
                "changed_files": ["src/main.py"],
                "duration": 1,
                "side_effects": [
                    {
                        "type": "file_delete",
                        "target": "temp/generated.py",
                        "reverse_data": {"content": "restored"},
                    }
                ],
            },
        ):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["ok"] is True
        assert results[0]["saga_actions"] == 1
        mock_svc.register_compensation_action.assert_called_once()
        register_call = mock_svc.register_compensation_action.call_args.kwargs
        assert register_call["task_id"] == "task-exec-saga"
        assert register_call["lease_token"] == "lease-saga"
        assert register_call["action"]["action_type"] == "file_delete"
        assert register_call["action"]["target"] == "temp/generated.py"
        mock_svc.commit_compensation_actions.assert_called_once()

    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_missing_blueprint_dead_letters(self, mock_get_svc: MagicMock) -> None:
        """Verify task without blueprint_id is moved to dead_letter."""
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-no-bp"
        claim_result.lease_token = "lease-abc"
        claim_result.payload = {}  # No blueprint_id

        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.fail_task_stage.return_value = MagicMock()

        consumer = DirectorExecutionConsumer(workspace="/test", worker_id="d1")
        results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["task_id"] == "task-no-bp"
        assert results[0]["ok"] is False
        assert results[0]["reason"] == "missing_blueprint"

        # Verify dead-letter: fail called with to_dead_letter=True
        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.error_code == "MISSING_BLUEPRINT"
        assert fail_call.to_dead_letter is True

    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_execution_failure_requeues_pending_exec(self, mock_get_svc: MagicMock) -> None:
        """Verify execution failure requeues to pending_exec."""
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-fail"
        claim_result.lease_token = "lease-fail"
        claim_result.payload = {"blueprint_id": "bp-fail"}

        fail_result = MagicMock()
        fail_result.ok = False

        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.fail_task_stage.return_value = fail_result

        consumer = DirectorExecutionConsumer(workspace="/test", worker_id="d1")

        # Patch _execute_task to raise
        with patch.object(consumer, "_execute_task", side_effect=RuntimeError("exec crashed")):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["task_id"] == "task-fail"
        assert results[0]["ok"] is False
        assert "exec crashed" in results[0]["reason"]

        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.requeue_stage == "pending_exec"
        assert fail_call.error_code == "EXEC_FAILED"
        mock_svc.compensate_task.assert_not_called()

    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_unrecoverable_execution_compensates_and_dead_letters(self, mock_get_svc: MagicMock) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-fatal"
        claim_result.lease_token = "lease-fatal"
        claim_result.payload = {"blueprint_id": "bp-fatal"}

        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.fail_task_stage.return_value = MagicMock(ok=False)

        consumer = DirectorExecutionConsumer(workspace="/test", worker_id="d1")
        with patch.object(
            consumer,
            "_execute_task",
            side_effect=UnrecoverableExecutionError("fatal-execution-error"),
        ):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["task_id"] == "task-fatal"
        assert results[0]["ok"] is False
        assert results[0]["dead_lettered"] is True
        mock_svc.compensate_task.assert_called_once()
        compensate_call = mock_svc.compensate_task.call_args.kwargs
        assert compensate_call["task_id"] == "task-fatal"
        assert compensate_call["initiator"] == "director_consumer"

        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.to_dead_letter is True
        assert fail_call.error_code == "EXEC_UNRECOVERABLE"

    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_scope_conflict_when_safe_parallel_enabled(self, mock_get_svc: MagicMock) -> None:
        """Verify scope conflict requeues when enable_safe_parallel=True."""
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-conflict"
        claim_result.lease_token = "lease-conflict"
        claim_result.payload = {"blueprint_id": "bp-002", "scope_paths": ["/src/a.py"]}

        fail_result = MagicMock()
        fail_result.ok = False

        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.fail_task_stage.return_value = fail_result

        consumer = DirectorExecutionConsumer(workspace="/test", worker_id="d1", enable_safe_parallel=True)

        # Patch conflict detector to return True (conflict found)
        with patch.object(
            consumer._conflict_detector,
            "check_conflict",
            return_value=True,
        ):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["task_id"] == "task-conflict"
        assert results[0]["ok"] is False
        assert results[0]["reason"] == "scope_conflict"

        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.error_code == "SCOPE_CONFLICT"
        assert fail_call.requeue_stage == "pending_exec"

    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_execution_renews_lease_heartbeat(self, mock_get_svc: MagicMock) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-heartbeat"
        claim_result.lease_token = "lease-heartbeat"
        claim_result.payload = {"blueprint_id": "bp-heartbeat"}

        ack_result = MagicMock()
        ack_result.ok = True
        ack_result.status = "pending_qa"

        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.acknowledge_task_stage.return_value = ack_result

        consumer = DirectorExecutionConsumer(
            workspace="/test",
            worker_id="d1",
            lease_renew_interval_seconds=0.01,
        )

        def _slow_execute(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            time.sleep(0.12)
            return {"changed_files": ["src/main.py"], "duration": 0, "side_effects": []}

        with patch.object(
            consumer,
            "_execute_task",
            side_effect=_slow_execute,
        ):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["ok"] is True
        assert mock_svc.renew_task_lease.call_count >= 1


class TestScopeConflictDetector:
    def test_no_conflict_when_empty_scope(self) -> None:
        detector = ScopeConflictDetector()
        with patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service"):
            result = detector.check_conflict("/test", "task-1", [])
            assert result is False

    def test_returns_false_regardless_of_service_response(self) -> None:
        """No overlap -> no conflict."""
        detector = ScopeConflictDetector()
        with patch(
            "polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service"
        ) as mock_get:
            mock_svc = MagicMock()
            mock_get.return_value = mock_svc
            mock_svc.query_status.return_value = MagicMock(
                items=(
                    {
                        "task_id": "task-2",
                        "status": "in_execution",
                        "payload": {"scope_paths": ["/src/other.py"]},
                    },
                )
            )

            result = detector.check_conflict("/test", "task-1", ["/src/main.py"])
            assert result is False

    def test_detects_conflict_with_other_in_execution_scope_overlap(self) -> None:
        detector = ScopeConflictDetector()
        with patch(
            "polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service"
        ) as mock_get:
            mock_svc = MagicMock()
            mock_get.return_value = mock_svc
            mock_svc.query_status.return_value = MagicMock(
                items=(
                    {
                        "task_id": "task-2",
                        "status": "in_execution",
                        "payload": {"scope_paths": ["/src/main.py"]},
                    },
                )
            )
            assert detector.check_conflict("/test", "task-1", ["/src/main.py"]) is True


class TestDirectorExecutionConsumerRunStop:
    """Tests for the run() / stop() durable consumer loop."""

    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_run_polls_until_stop(self, mock_get_svc: MagicMock) -> None:
        """run() loops until stop() signals _stop_event."""
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        # No claimable tasks -> poll_once returns [] immediately.
        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.return_value = no_claim

        consumer = DirectorExecutionConsumer(
            workspace="/test",
            worker_id="run-test",
            poll_interval=0.02,
        )

        poll_count = 0
        original_poll = consumer.poll_once

        def counting_poll() -> list[dict]:
            nonlocal poll_count
            poll_count += 1
            return original_poll()

        consumer.poll_once = counting_poll

        thread = threading.Thread(target=consumer.run, daemon=True)
        thread.start()

        # Wait for at least 3 poll cycles.
        deadline = time.monotonic() + 2.0
        while poll_count < 3 and time.monotonic() < deadline:
            time.sleep(0.01)

        consumer.stop()
        thread.join(timeout=2.0)

        assert poll_count >= 3
        assert not thread.is_alive()

    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_run_isolates_exceptions(self, mock_get_svc: MagicMock) -> None:
        """Exceptions in poll_once do not kill the run loop."""
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        consumer = DirectorExecutionConsumer(
            workspace="/test",
            worker_id="exc-test",
            poll_interval=0.02,
        )

        poll_count = 0

        def flaky_poll() -> list[dict]:
            nonlocal poll_count
            poll_count += 1
            if poll_count <= 2:
                raise RuntimeError("transient failure")
            # After 2 failures, return empty to allow stop.
            return []

        consumer.poll_once = flaky_poll

        thread = threading.Thread(target=consumer.run, daemon=True)
        thread.start()

        # Wait for at least 4 poll cycles (2 failures + 2 normal).
        deadline = time.monotonic() + 2.0
        while poll_count < 4 and time.monotonic() < deadline:
            time.sleep(0.01)

        consumer.stop()
        thread.join(timeout=2.0)

        assert poll_count >= 4
        assert not thread.is_alive()
