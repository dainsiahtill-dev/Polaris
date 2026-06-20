"""Tests for director_consumer.py."""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
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
    def test_direct_route_uses_pm_contract_without_blueprint(self, mock_get_svc: MagicMock) -> None:
        """Direct PM->Director work should not require ChiefEngineer blueprint metadata."""
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-direct-1"
        claim_result.lease_token = "lease-direct"
        claim_result.payload = {
            "route": "direct_to_director",
            "blueprint_required": False,
            "scope_paths": ["src/main.py"],
        }

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
            return_value={"changed_files": ["src/main.py"], "duration": 1, "side_effects": []},
        ):
            results = consumer.poll_once()

        assert results[0]["ok"] is True
        mock_svc.fail_task_stage.assert_not_called()
        ack_call = mock_svc.acknowledge_task_stage.call_args[0][0]
        assert ack_call.next_stage == "pending_qa"
        assert ack_call.metadata["blueprint_id"] == "pm-direct::task-direct-1"
        assert ack_call.metadata["route"] == "direct_to_director"
        assert ack_call.metadata["blueprint_required"] is False
        assert ack_call.metadata["director_execution_authority"] == "pm_task_contract"

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
    def test_verified_existing_scope_advances_to_qa_without_changed_files(self, mock_get_svc: MagicMock) -> None:
        """Existing-scope verification is execution evidence for already-satisfied fission steps."""
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "PM-0001-1-S2"
        claim_result.lease_token = "lease-existing"
        claim_result.payload = {
            "blueprint_id": "bp-PM-0001-1",
            "construction_step": {
                "step_id": "PM-0001-1-S2",
                "target_file": "calculator.py",
                "verify": "python -m py_compile calculator.py",
            },
            "target_files": ["calculator.py"],
            "scope_paths": ["calculator.py"],
        }

        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.acknowledge_task_stage.return_value = MagicMock(ok=True, status="pending_qa")

        consumer = DirectorExecutionConsumer(workspace="/test", worker_id="d1")
        with patch.object(
            consumer,
            "_execute_task",
            return_value={
                "changed_files": [],
                "duration": 1,
                "side_effects": [],
                "director_adapter_result": {
                    "success": True,
                    "task_id": "PM-0001-1-S2",
                    "tools_executed": 3,
                    "materialization_mode": "verified_existing_workspace_scope",
                    "existing_contract_evidence": {
                        "ok": True,
                        "reason": "declared_scope_present",
                        "existing_paths": ["calculator.py"],
                    },
                },
            },
        ):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["ok"] is True
        mock_svc.fail_task_stage.assert_not_called()
        ack_call = mock_svc.acknowledge_task_stage.call_args[0][0]
        assert ack_call.next_stage == "pending_qa"
        assert ack_call.metadata["changed_files"] == []
        assert ack_call.metadata["director_evidence_status"] == "verified_existing_workspace_scope"
        assert ack_call.metadata["director_adapter"]["existing_contract_evidence"]["existing_paths"] == [
            "calculator.py"
        ]

    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_step_target_not_in_changed_files_requeues(self, mock_get_svc: MagicMock) -> None:
        """A fission step that changed OTHER files but not its declared
        target_file must not advance to QA (live I3-r9: the readme.md step
        wrote index.html and sailed through)."""
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "PM-1-S1"
        claim_result.lease_token = "lease-step"
        claim_result.payload = {
            "blueprint_id": "bp-001",
            "construction_step": {
                "step_id": "PM-1-S1",
                "target_file": "readme.md",
                "verify": "test -f ./readme.md",
            },
        }
        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.fail_task_stage.return_value = MagicMock(ok=True, status="pending_exec")

        consumer = DirectorExecutionConsumer(workspace="/test", worker_id="d1")
        with patch.object(
            consumer,
            "_execute_task",
            return_value={"changed_files": ["index.html"], "duration": 1, "side_effects": []},
        ):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["ok"] is False
        assert results[0]["reason"] == "step_target_missing"
        mock_svc.acknowledge_task_stage.assert_not_called()
        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.error_code == "EXEC_TARGET_MISSING"
        assert fail_call.requeue_stage == "pending_exec"
        assert "readme.md" in fail_call.error_message

    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_direct_route_target_file_not_in_changed_files_requeues(self, mock_get_svc: MagicMock) -> None:
        """Direct PM->Director single-file work must also enforce target_files."""
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "D4-SAT-3"
        claim_result.lease_token = "lease-direct"
        claim_result.payload = {
            "route": "direct_to_director",
            "target_files": ["worker_3.py"],
            "scope_paths": ["worker_3.py"],
        }
        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.fail_task_stage.return_value = MagicMock(ok=True, status="pending_exec")

        consumer = DirectorExecutionConsumer(workspace="/test", worker_id="d1")
        with patch.object(
            consumer,
            "_execute_task",
            return_value={"changed_files": ["worker_4.py"], "duration": 1, "side_effects": []},
        ):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["ok"] is False
        assert results[0]["reason"] == "step_target_missing"
        mock_svc.acknowledge_task_stage.assert_not_called()
        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.error_code == "EXEC_TARGET_MISSING"
        assert fail_call.requeue_stage == "pending_exec"
        assert "worker_3.py" in fail_call.error_message

    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_execute_task_forwards_bounce_teaching_to_adapter_context(
        self,
        mock_get_svc: MagicMock,
        tmp_path: Any,
        monkeypatch: Any,
    ) -> None:
        """A requeued step's last_failure must reach the adapter context —
        blind retries make no changes and die (live I3-r10)."""
        mock_get_svc.return_value = MagicMock()
        seen_context: dict[str, Any] = {}

        class FakeDirectorAdapter:
            def __init__(self, workspace: str) -> None:
                self.workspace = workspace

            async def execute(
                self,
                *,
                task_id: str,
                input_data: dict[str, Any],
                context: dict[str, Any],
            ) -> dict[str, Any]:
                seen_context.update(context)
                return {"success": True, "task_id": task_id, "tool_results": []}

        monkeypatch.setattr(
            "polaris.cells.roles.adapters.public.service.create_role_adapter",
            lambda role_id, workspace: FakeDirectorAdapter(workspace),
        )

        consumer = DirectorExecutionConsumer(workspace=str(tmp_path), worker_id="d1")
        consumer._execute_task(
            "PM-1-S1",
            {
                "title": "step",
                "construction_step": {"step_id": "PM-1-S1", "target_file": "index.html"},
                "last_failure": {
                    "error_code": "QA_step_verify_failed",
                    "error_message": "step verify failed: grep -q levelDisplay",
                },
            },
            "lease-teach",
        )
        assert seen_context["construction_step"]["target_file"] == "index.html"
        assert seen_context["last_failure"]["error_code"] == "QA_step_verify_failed"
        assert seen_context["task_id"] == "PM-1-S1"
        assert seen_context["pm_task_id"] == "PM-1-S1"
        assert seen_context["metadata"]["task_id"] == "PM-1-S1"
        assert seen_context["metadata"]["pm_task_id"] == "PM-1-S1"
        assert seen_context["metadata"]["task_market_task_id"] == "PM-1-S1"

    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_step_target_covered_advances_to_qa(self, mock_get_svc: MagicMock) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "PM-1-S2"
        claim_result.lease_token = "lease-step2"
        claim_result.payload = {
            "blueprint_id": "bp-001",
            "construction_step": {"step_id": "PM-1-S2", "target_file": "style.css"},
        }
        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.acknowledge_task_stage.return_value = MagicMock(ok=True, status="pending_qa")

        consumer = DirectorExecutionConsumer(workspace="/test", worker_id="d1")
        with patch.object(
            consumer,
            "_execute_task",
            return_value={"changed_files": ["./style.css"], "duration": 1, "side_effects": []},
        ):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["ok"] is True
        mock_svc.fail_task_stage.assert_not_called()

    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_execute_task_delegates_to_director_adapter_for_existing_workspace(
        self,
        mock_get_svc: MagicMock,
        tmp_path: Any,
        monkeypatch: Any,
    ) -> None:
        mock_get_svc.return_value = MagicMock()

        class FakeDirectorAdapter:
            def __init__(self, workspace: str) -> None:
                self.workspace = workspace

            async def execute(
                self,
                *,
                task_id: str,
                input_data: dict[str, Any],
                context: dict[str, Any],
            ) -> dict[str, Any]:
                assert task_id == "task-real"
                assert input_data["task_id"] == "task-real"
                assert input_data["pm_task_id"] == "pm-1"
                assert context["metadata"]["task_market_stage"] == "pending_exec"
                return {
                    "success": True,
                    "task_id": task_id,
                    "tools_executed": 1,
                    "materialization_mode": "unit_test",
                    "tool_results": [
                        {
                            "tool_name": "write_file",
                            "status": "success",
                            "effect_receipt": {
                                "file": "src/main.py",
                                "bytes_written": 12,
                                "changed": True,
                            },
                        }
                    ],
                }

        monkeypatch.setattr(
            "polaris.cells.roles.adapters.public.service.create_role_adapter",
            lambda role_id, workspace: FakeDirectorAdapter(workspace),
        )

        consumer = DirectorExecutionConsumer(workspace=str(tmp_path), worker_id="d1")
        result = consumer._execute_task(
            "task-real",
            {
                "source_pm_task_id": "pm-1",
                "title": "Create module",
                "goal": "Create a real module",
                "target_files": ["src/main.py"],
            },
            "lease-real",
        )

        assert result["changed_files"] == ["src/main.py"]
        assert result["duration"] >= 0
        assert result["side_effects"] == []
        assert result["director_adapter_result"]["success"] is True
        assert result["director_adapter_result"]["materialization_mode"] == "unit_test"

    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_execute_task_preserves_verified_existing_scope_evidence(
        self,
        mock_get_svc: MagicMock,
        tmp_path: Any,
        monkeypatch: Any,
    ) -> None:
        mock_get_svc.return_value = MagicMock()

        class FakeDirectorAdapter:
            async def execute(
                self,
                *,
                task_id: str,
                input_data: dict[str, Any],
                context: dict[str, Any],
            ) -> dict[str, Any]:
                return {
                    "success": True,
                    "task_id": task_id,
                    "tools_executed": 2,
                    "materialization_mode": "verified_existing_workspace_scope",
                    "existing_contract_evidence": {
                        "ok": True,
                        "reason": "declared_scope_present",
                        "candidate_paths": ["./calculator.py"],
                        "existing_paths": ["./calculator.py"],
                        "missing_paths": [],
                    },
                }

        monkeypatch.setattr(
            "polaris.cells.roles.adapters.public.service.create_role_adapter",
            lambda role_id, workspace: FakeDirectorAdapter(),
        )

        consumer = DirectorExecutionConsumer(workspace=str(tmp_path), worker_id="d1")
        result = consumer._execute_task(
            "PM-0001-1-S2",
            {
                "source_pm_task_id": "PM-0001-1-S2",
                "title": "Verify existing calculator",
                "target_files": ["calculator.py"],
            },
            "lease-existing",
        )

        summary = result["director_adapter_result"]
        assert result["changed_files"] == []
        assert summary["success"] is True
        assert summary["materialization_mode"] == "verified_existing_workspace_scope"
        assert summary["existing_contract_evidence"]["ok"] is True
        assert summary["existing_contract_evidence"]["existing_paths"] == ["./calculator.py"]

    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_execute_task_requeues_failed_director_adapter(
        self,
        mock_get_svc: MagicMock,
        tmp_path: Any,
        monkeypatch: Any,
    ) -> None:
        mock_get_svc.return_value = MagicMock()

        class FakeDirectorAdapter:
            def __init__(self, workspace: str) -> None:
                self.workspace = workspace

            async def execute(
                self,
                *,
                task_id: str,
                input_data: dict[str, Any],
                context: dict[str, Any],
            ) -> dict[str, Any]:
                return {
                    "success": False,
                    "task_id": task_id,
                    "error": "director_no_materialized_changes",
                    "failure_stage": "director_materialization",
                }

        monkeypatch.setattr(
            "polaris.cells.roles.adapters.public.service.create_role_adapter",
            lambda role_id, workspace: FakeDirectorAdapter(workspace),
        )

        consumer = DirectorExecutionConsumer(workspace=str(tmp_path), worker_id="d1")

        with pytest.raises(RuntimeError, match="director_no_materialized_changes"):
            consumer._execute_task("task-failed", {"title": "Bad task"}, "lease-failed")

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
    def test_safe_parallel_conflict_uses_single_target_file(self, mock_get_svc: MagicMock) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-conflict"
        claim_result.lease_token = "lease-conflict"
        claim_result.payload = {"blueprint_id": "bp-002", "target_file": "src/a.py"}

        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.fail_task_stage.return_value = MagicMock(ok=False)

        consumer = DirectorExecutionConsumer(workspace="/test", worker_id="d1", enable_safe_parallel=True)
        with patch.object(consumer._conflict_detector, "check_conflict", return_value=True) as check_conflict:
            results = consumer.poll_once()

        assert results[0]["reason"] == "scope_conflict"
        assert check_conflict.call_args.args[2] == ["src/a.py"]

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

    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_active_claim_snapshot_is_visible_only_during_execution(self, mock_get_svc: MagicMock) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-active"
        claim_result.lease_token = "lease-active"
        claim_result.payload = {"blueprint_id": "bp-active"}

        ack_result = MagicMock()
        ack_result.ok = True
        ack_result.status = "pending_qa"

        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.acknowledge_task_stage.return_value = ack_result

        consumer = DirectorExecutionConsumer(workspace="/test", worker_id="d1", visibility_timeout_seconds=600)
        execution_started = threading.Event()
        release_execution = threading.Event()

        def _blocking_execute(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            execution_started.set()
            assert release_execution.wait(timeout=5.0)
            return {"changed_files": ["src/main.py"], "duration": 0, "side_effects": []}

        result_box: dict[str, Any] = {}

        def _run_poll() -> None:
            with patch.object(consumer, "_execute_task", side_effect=_blocking_execute):
                result_box["results"] = consumer.poll_once()

        thread = threading.Thread(target=_run_poll)
        thread.start()
        try:
            assert execution_started.wait(timeout=5.0)
            snapshot = consumer.active_claim_watchdog_snapshot()
            assert snapshot["task_id"] == "task-active"
            assert isinstance(snapshot["started_monotonic"], float)
            assert snapshot["timeout_seconds"] == 600.0
            release_execution.set()
            thread.join(timeout=5.0)
            assert not thread.is_alive()
        finally:
            release_execution.set()
            thread.join(timeout=5.0)

        assert result_box["results"][0]["ok"] is True
        assert consumer.active_claim_watchdog_snapshot()["started_monotonic"] is None


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

    def test_detects_conflict_with_other_in_execution_single_target_file(self) -> None:
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
                        "payload": {"target_file": "src/main.py"},
                    },
                )
            )
            assert detector.check_conflict("/test", "task-1", ["src/main.py"]) is True


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


class TestAdapterFailureMessage:
    """Fix-10 (live I3-r12): the market bounce carried only the generic
    director_materialization_quality_failed marker — the next claimant learned
    nothing. The message must name the first concrete quality error."""

    @staticmethod
    def _message(adapter_result: dict[str, Any]) -> str:
        from polaris.cells.director.task_consumer.internal.director_consumer import (
            _adapter_failure_message,
        )

        return _adapter_failure_message(adapter_result)

    def test_carries_first_quality_error_detail(self) -> None:
        message = self._message(
            {
                "error": "director_materialization_quality_failed",
                "artifact_quality_errors": [
                    'step verify failed (exit 1): ... | failing clause [3/3]: [ "$(wc -l < ./style.css)" -le 120 ]',
                    "second error",
                ],
            }
        )
        assert message.startswith("director_materialization_quality_failed: ")
        assert "failing clause [3/3]" in message

    def test_detail_is_truncated(self) -> None:
        message = self._message(
            {"error": "director_materialization_quality_failed", "artifact_quality_errors": ["x" * 1000]}
        )
        assert len(message) <= len("director_materialization_quality_failed: ") + 400

    def test_without_quality_errors_keeps_marker_only(self) -> None:
        assert self._message({"error": "boom"}) == "boom"
        assert self._message({"artifact_quality_errors": []}) == "director_adapter_execution_failed"

    def test_blank_quality_entries_are_skipped(self) -> None:
        assert self._message({"error": "boom", "artifact_quality_errors": ["", None]}) == "boom"


class TestPreStatePunchList:
    """Fix-13 缺陷清单(punch list): 改建式步骤的施工单必须携带现状勘察 ——
    live I3-r13 编辑模式 0/5: 模型读到看似完整的目标文件拒绝动笔, 三试零 diff。"""

    @staticmethod
    def _punch(step: dict[str, Any], cwd: str) -> dict[str, Any] | None:
        from polaris.cells.director.task_consumer.internal.director_consumer import (
            _pre_state_punch_list,
        )

        return _pre_state_punch_list(step, cwd=cwd)

    def test_no_verify_returns_none(self, tmp_path: Any) -> None:
        assert self._punch({"target_file": "a.md"}, str(tmp_path)) is None

    def test_passing_pre_state_reports_exit_zero(self, tmp_path: Any) -> None:
        (tmp_path / "main.js").write_text("const LEVELS = []\n", encoding="utf-8")
        step = {"verify": "test -f ./main.js && grep -q 'const LEVELS' ./main.js"}
        result = self._punch(step, str(tmp_path))
        assert result == {"exit_code": 0, "failing_clauses": [], "total_clauses": 2}

    def test_failing_clauses_are_listed(self, tmp_path: Any) -> None:
        (tmp_path / "main.js").write_text("function draw() {}\n", encoding="utf-8")
        step = {
            "verify": (
                "test -f ./main.js && grep -q 'const LEVELS' ./main.js"
                " && grep -q 'function loadLevel' ./main.js && grep -q 'function draw' ./main.js"
            )
        }
        result = self._punch(step, str(tmp_path))
        assert result is not None
        assert result["exit_code"] != 0
        assert result["total_clauses"] == 4
        assert result["failing_clauses"] == [
            "grep -Fq 'const LEVELS' ./main.js",
            "grep -Fq 'function loadLevel' ./main.js",
        ]

    def test_state_carrying_chain_reports_whole_failure_only(self, tmp_path: Any) -> None:
        step = {"verify": "cd src && test -f app.js"}
        result = self._punch(step, str(tmp_path))
        assert result is not None
        assert result["exit_code"] != 0
        assert result["failing_clauses"] == []

    def test_top_level_or_reports_whole_failure_only(self, tmp_path: Any) -> None:
        step = {"verify": "test -f ./a.md && grep -q x ./a.md || test -f ./b.md"}
        result = self._punch(step, str(tmp_path))
        assert result is not None
        assert result["failing_clauses"] == []

    def test_list_verify_is_joined(self, tmp_path: Any) -> None:
        step = {"verify": ["test -f ./a.md", "grep -q x ./a.md"]}
        result = self._punch(step, str(tmp_path))
        assert result is not None
        assert result["exit_code"] != 0
        assert result["total_clauses"] == 2


class TestRepairShrinkGuard:
    """R7-C (I3-r28): deterministic anti-shrink backstop on repair turns —
    a weak model asked to fix one error must not rewrite the file smaller
    (live r28: main.js 5762B/22 constructs -> 3095B/12)."""

    @staticmethod
    def _prior_size(workspace: str, payload: dict[str, Any]) -> int | None:
        from polaris.cells.director.task_consumer.internal.director_consumer import (
            _repair_prior_target_size,
        )

        return _repair_prior_target_size(workspace, payload)

    @staticmethod
    def _shrink_error(workspace: str, target: str, prior: int) -> str | None:
        from polaris.cells.director.task_consumer.internal.director_consumer import (
            _repair_shrink_error,
        )

        return _repair_shrink_error(workspace, target, prior)

    @staticmethod
    def _repair_payload(target: str = "main.js") -> dict[str, Any]:
        return {
            "construction_step": {"step_id": "S", "target_file": target},
            "last_failure": {"error_code": "QA_syntax_failed", "error_message": "main.js:42 token ';'"},
        }

    def test_prior_size_returns_bytes_on_repair_turn(self, tmp_path: Any) -> None:
        (tmp_path / "main.js").write_text("x" * 5000, encoding="utf-8")
        assert self._prior_size(str(tmp_path), self._repair_payload()) == 5000

    def test_prior_size_none_without_last_failure(self, tmp_path: Any) -> None:
        (tmp_path / "main.js").write_text("x" * 5000, encoding="utf-8")
        payload = {"construction_step": {"step_id": "S", "target_file": "main.js"}}
        assert self._prior_size(str(tmp_path), payload) is None

    def test_prior_size_none_when_target_absent(self, tmp_path: Any) -> None:
        assert self._prior_size(str(tmp_path), self._repair_payload()) is None

    def test_shrink_below_floor_returns_teaching_error(self, tmp_path: Any) -> None:
        (tmp_path / "main.js").write_text("x" * 1000, encoding="utf-8")  # 50% of 2000 < 0.6 floor
        err = self._shrink_error(str(tmp_path), "main.js", 2000)
        assert err is not None
        assert "REPAIR SHRANK" in err and "edit_blocks" in err and "2000" in err

    def test_preserved_size_returns_none(self, tmp_path: Any) -> None:
        (tmp_path / "main.js").write_text("x" * 1950, encoding="utf-8")  # ~same size, one-char fix
        assert self._shrink_error(str(tmp_path), "main.js", 2000) is None

    def test_tiny_prior_file_not_guarded(self, tmp_path: Any) -> None:
        (tmp_path / "main.js").write_text("x" * 10, encoding="utf-8")
        assert self._shrink_error(str(tmp_path), "main.js", 300) is None  # below MIN_PRIOR_BYTES

    def test_env_overrides_ratio(self, tmp_path: Any, monkeypatch: Any) -> None:
        (tmp_path / "main.js").write_text("x" * 850, encoding="utf-8")  # 850/1000 = 0.85
        # default 0.6: 850 >= 600 -> ok; raise floor to 0.9: 850 < 900 -> violation
        assert self._shrink_error(str(tmp_path), "main.js", 1000) is None
        monkeypatch.setenv("KERNELONE_REPAIR_SHRINK_GUARD_RATIO", "0.9")
        assert self._shrink_error(str(tmp_path), "main.js", 1000) is not None


class TestRepairShrinkGuardWiring:
    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_shrunk_repair_requeues_pending_exec(self, mock_get_svc: MagicMock, tmp_path: Any) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc
        target = tmp_path / "main.js"
        target.write_text("// working game\n" + "x" * 6000, encoding="utf-8")
        prior = target.stat().st_size

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-repair-1"
        claim_result.lease_token = "lease-r"
        claim_result.payload = {
            "blueprint_id": "bp-r",
            "construction_step": {"step_id": "S", "target_file": "main.js"},
            "last_failure": {"error_code": "QA_syntax_failed", "error_message": "main.js:42 token ';'"},
        }
        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.fail_task_stage.return_value = MagicMock(ok=True, status="pending_exec")

        consumer = DirectorExecutionConsumer(workspace=str(tmp_path), worker_id="d1")

        def _shrink_exec(task_id: str, payload: dict[str, Any], lease_token: str) -> dict[str, Any]:
            target.write_text("// tiny rewrite\n", encoding="utf-8")
            return {"changed_files": ["main.js"], "duration": 1, "side_effects": []}

        with patch.object(consumer, "_execute_task", side_effect=_shrink_exec):
            results = consumer.poll_once()

        assert results[0]["ok"] is False
        assert results[0]["reason"] == "repair_shrank_file"
        mock_svc.acknowledge_task_stage.assert_not_called()
        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.error_code == "REPAIR_SHRANK_FILE"
        assert fail_call.requeue_stage == "pending_exec"
        assert str(prior) in fail_call.error_message

    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_preserving_repair_acks_pending_qa(self, mock_get_svc: MagicMock, tmp_path: Any) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc
        target = tmp_path / "main.js"
        target.write_text("// working game\n" + "x" * 6000, encoding="utf-8")

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-repair-2"
        claim_result.lease_token = "lease-r2"
        claim_result.payload = {
            "blueprint_id": "bp-r",
            "construction_step": {"step_id": "S", "target_file": "main.js"},
            "last_failure": {"error_code": "QA_syntax_failed", "error_message": "main.js:42 token ';'"},
        }
        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_svc.acknowledge_task_stage.return_value = MagicMock(ok=True, status="pending_qa")

        consumer = DirectorExecutionConsumer(workspace=str(tmp_path), worker_id="d1")

        def _edit_exec(task_id: str, payload: dict[str, Any], lease_token: str) -> dict[str, Any]:
            # localized fix: same size, one char changed
            target.write_text("// working game\n" + "y" + "x" * 5999, encoding="utf-8")
            return {"changed_files": ["main.js"], "duration": 1, "side_effects": []}

        with patch.object(consumer, "_execute_task", side_effect=_edit_exec):
            results = consumer.poll_once()

        assert results[0]["ok"] is True
        assert results[0]["status"] == "pending_qa"
        # no REPAIR_SHRANK_FILE failure was raised
        for call in mock_svc.fail_task_stage.call_args_list:
            assert call[0][0].error_code != "REPAIR_SHRANK_FILE"


class TestPunchListWiring:
    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_execute_task_attaches_pre_state_verify_to_context(
        self,
        mock_get_svc: MagicMock,
        tmp_path: Any,
        monkeypatch: Any,
    ) -> None:
        mock_get_svc.return_value = MagicMock()
        (tmp_path / "main.js").write_text("function draw() {}\n", encoding="utf-8")
        seen_context: dict[str, Any] = {}

        class FakeDirectorAdapter:
            def __init__(self, workspace: str) -> None:
                self.workspace = workspace

            async def execute(
                self,
                *,
                task_id: str,
                input_data: dict[str, Any],
                context: dict[str, Any],
            ) -> dict[str, Any]:
                seen_context.update(context)
                return {"success": True, "task_id": task_id, "tool_results": []}

        monkeypatch.setattr(
            "polaris.cells.roles.adapters.public.service.create_role_adapter",
            lambda role_id, workspace: FakeDirectorAdapter(workspace),
        )

        consumer = DirectorExecutionConsumer(workspace=str(tmp_path), worker_id="d1")
        consumer._execute_task(
            "PM-1-S1",
            {
                "title": "edit step",
                "construction_step": {
                    "step_id": "PM-1-S1",
                    "target_file": "main.js",
                    "verify": "test -f ./main.js && grep -q 'const LEVELS' ./main.js",
                },
            },
            "lease-punch",
        )
        pre_state = seen_context["pre_state_verify"]
        assert pre_state["exit_code"] != 0
        assert pre_state["failing_clauses"] == ["grep -Fq 'const LEVELS' ./main.js"]

    @patch("polaris.cells.director.task_consumer.internal.director_consumer.get_task_market_service")
    def test_execute_task_injects_consumed_interfaces(
        self,
        mock_get_svc: MagicMock,
        tmp_path: Any,
        monkeypatch: Any,
    ) -> None:
        from polaris.kernelone.quality.interface_ledger import record_declared_interfaces

        mock_get_svc.return_value = MagicMock()
        # A sibling file (index.html) froze identifiers; the main.js step must reuse them.
        record_declared_interfaces(
            str(tmp_path),
            str(tmp_path),
            [{"step_id": "PM-1-S0", "target_file": "index.html", "interface_names": ["gameCanvas", "score"]}],
        )
        seen_context: dict[str, Any] = {}

        class FakeDirectorAdapter:
            def __init__(self, workspace: str) -> None:
                self.workspace = workspace

            async def execute(
                self,
                *,
                task_id: str,
                input_data: dict[str, Any],
                context: dict[str, Any],
            ) -> dict[str, Any]:
                seen_context.update(context)
                return {"success": True, "task_id": task_id, "tool_results": []}

        monkeypatch.setattr(
            "polaris.cells.roles.adapters.public.service.create_role_adapter",
            lambda role_id, workspace: FakeDirectorAdapter(workspace),
        )

        consumer = DirectorExecutionConsumer(workspace=str(tmp_path), worker_id="d1")
        consumer._execute_task(
            "PM-1-S3",
            {
                "title": "write main.js",
                "cache_root": str(tmp_path),
                "construction_step": {"step_id": "PM-1-S3", "target_file": "main.js"},
            },
            "lease-iface",
        )
        consumed = seen_context["consumed_interfaces"]
        assert "index.html" in consumed
        assert consumed["index.html"]["identifiers"] == ["gameCanvas", "score"]
        assert "main.js" not in consumed  # the step's own target is excluded
