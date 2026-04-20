"""Minimum smoke test suite for director.tasking sub-Cell.

Phase 3 migration gap (a): add director.tasking tests.
"""

from __future__ import annotations

import pytest
from polaris.cells.director.tasking import internal as tasking_internal, public as tasking_public
from polaris.cells.director.tasking.internal import (
    TaskQueueConfig,
    WorkerPoolConfig,
    get_generic_bootstrap_files,
    get_intelligent_bootstrap_files,
    get_python_bootstrap_files,
    get_typescript_bootstrap_files,
)
from polaris.cells.director.tasking.public.contracts import (
    CancelTaskCommandV1,
    CreateTaskCommandV1,
    DirectorTaskingError,
    TaskCreatedResultV1,
    TaskResultQueryV1,
    TaskResultResultV1,
    TaskStatusQueryV1,
    TaskStatusResultV1,
)


class TestCreateTaskCommandV1HappyPath:
    def test_minimal_fields(self) -> None:
        cmd = CreateTaskCommandV1(subject="Fix login", workspace="/ws")
        assert cmd.subject == "Fix login"
        assert cmd.workspace == "/ws"

    def test_default_priority_is_medium(self) -> None:
        cmd = CreateTaskCommandV1(subject="x", workspace="/ws")
        assert cmd.priority == "medium"

    def test_default_description_is_empty_string(self) -> None:
        cmd = CreateTaskCommandV1(subject="x", workspace="/ws")
        assert cmd.description == ""

    def test_default_blocked_by_is_empty_list(self) -> None:
        cmd = CreateTaskCommandV1(subject="x", workspace="/ws")
        assert cmd.blocked_by == []

    def test_metadata_dict_is_copied(self) -> None:
        meta = {"key": "value"}
        cmd = CreateTaskCommandV1(subject="x", workspace="/ws", metadata=meta)
        assert cmd.metadata == meta
        meta["injected"] = True  # type: ignore[assignment]
        assert "injected" not in cmd.metadata


class TestCreateTaskCommandV1EdgeCases:
    def test_empty_subject_raises(self) -> None:
        with pytest.raises(ValueError, match="subject"):
            CreateTaskCommandV1(subject="", workspace="/ws")

    def test_whitespace_subject_raises(self) -> None:
        with pytest.raises(ValueError, match="subject"):
            CreateTaskCommandV1(subject="   ", workspace="/ws")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            CreateTaskCommandV1(subject="x", workspace="")


class TestCancelTaskCommandV1:
    def test_valid_command(self) -> None:
        cmd = CancelTaskCommandV1(task_id="t-001", workspace="/ws", reason="cancelled")
        assert cmd.task_id == "t-001"
        assert cmd.reason == "cancelled"

    def test_default_reason_is_empty(self) -> None:
        cmd = CancelTaskCommandV1(task_id="t-002", workspace="/ws")
        assert cmd.reason == ""

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            CancelTaskCommandV1(task_id="", workspace="/ws")


class TestTaskStatusQueryV1:
    def test_default_limit_is_50(self) -> None:
        q = TaskStatusQueryV1(workspace="/ws")
        assert q.limit == 50

    def test_all_optional_fields_none_by_default(self) -> None:
        q = TaskStatusQueryV1(workspace="/ws")
        assert q.task_id is None
        assert q.status is None


class TestTaskResultQueryV1:
    def test_valid_query(self) -> None:
        q = TaskResultQueryV1(task_id="t-003", workspace="/ws")
        assert q.task_id == "t-003"

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            TaskResultQueryV1(task_id="", workspace="/ws")


class TestTaskCreatedResultV1:
    def test_success_result_needs_no_error(self) -> None:
        result = TaskCreatedResultV1(ok=True, task_id="t-004", workspace="/ws", subject="fix bug")
        assert result.ok is True
        assert result.error_code is None

    def test_failed_result_without_error_raises(self) -> None:
        with pytest.raises(ValueError, match="error_code or error_message"):
            TaskCreatedResultV1(ok=False, task_id="t-005", workspace="/ws", subject="x")

    def test_failed_result_with_error_code_valid(self) -> None:
        result = TaskCreatedResultV1(ok=False, task_id="t-006", workspace="/ws", subject="x", error_code="TASK_TIMEOUT")
        assert result.error_code == "TASK_TIMEOUT"

    def test_default_status_is_pending(self) -> None:
        result = TaskCreatedResultV1(ok=True, task_id="t-007", workspace="/ws", subject="y")
        assert result.status == "pending"


class TestTaskStatusResultV1:
    def test_result_with_empty_tasks(self) -> None:
        result = TaskStatusResultV1(ok=True, workspace="/ws")
        assert result.tasks == []
        assert result.count == 0


class TestTaskResultResultV1:
    def test_success_result(self) -> None:
        result = TaskResultResultV1(
            ok=True, task_id="t-009", workspace="/ws", success=True, output="done", duration_ms=1500
        )
        assert result.success is True
        assert result.duration_ms == 1500

    def test_failed_result_without_error_raises(self) -> None:
        with pytest.raises(ValueError, match="error_code or error_message"):
            TaskResultResultV1(ok=False, task_id="t-010", workspace="/ws")

    def test_evidence_default_empty_list(self) -> None:
        result = TaskResultResultV1(ok=True, task_id="t-011", workspace="/ws")
        assert result.evidence == []


class TestDirectorTaskingError:
    def test_default_code(self) -> None:
        err = DirectorTaskingError("something went wrong")
        assert err.code == "director_tasking_error"

    def test_custom_code_and_details(self) -> None:
        err = DirectorTaskingError("task not found", code="TASK_NOT_FOUND", details={"task_id": "t-012"})
        assert err.code == "TASK_NOT_FOUND"
        assert err.details == {"task_id": "t-012"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            DirectorTaskingError("")


class TestTaskQueueConfigDefaults:
    def test_default_max_queue_size(self) -> None:
        cfg = TaskQueueConfig()
        assert cfg.max_queue_size == 1000

    def test_default_timeout_is_300(self) -> None:
        cfg = TaskQueueConfig()
        assert cfg.default_timeout_seconds == 300

    def test_priority_and_dependency_tracking_enabled(self) -> None:
        cfg = TaskQueueConfig()
        assert cfg.enable_priority_scheduling is True
        assert cfg.enable_dependency_tracking is True


class TestWorkerPoolConfigDefaults:
    def test_default_min_workers_is_1(self) -> None:
        cfg = WorkerPoolConfig()
        assert cfg.min_workers == 1

    def test_default_max_consecutive_failures_is_3(self) -> None:
        cfg = WorkerPoolConfig()
        assert cfg.max_consecutive_failures == 3

    def test_default_heartbeat_timeout_is_60(self) -> None:
        cfg = WorkerPoolConfig()
        assert cfg.heartbeat_timeout_seconds == 60

    def test_default_auto_scaling_is_enabled(self) -> None:
        cfg = WorkerPoolConfig()
        assert cfg.enable_auto_scaling is True

    def test_custom_values_accepted(self) -> None:
        cfg = WorkerPoolConfig(min_workers=2, max_workers=8, enable_auto_scaling=False)
        assert cfg.min_workers == 2
        assert cfg.enable_auto_scaling is False


class TestBootstrapCatalogPureFunctions:
    def test_get_generic_bootstrap_returns_list(self) -> None:
        # Public legacy function takes 0 args
        result = get_generic_bootstrap_files()
        assert isinstance(result, list)

    def test_get_generic_bootstrap_items_have_path_and_content(self) -> None:
        files = get_generic_bootstrap_files()
        for f in files:
            assert "path" in f
            assert "content" in f

    def test_get_python_bootstrap_returns_list(self) -> None:
        # Public legacy function takes 0 args
        result = get_python_bootstrap_files()
        assert isinstance(result, list)

    def test_get_typescript_bootstrap_returns_list(self) -> None:
        # Public legacy function takes 0 args
        result = get_typescript_bootstrap_files()
        assert isinstance(result, list)

    def test_get_intelligent_bootstrap_python_returns_list(self) -> None:
        result = get_intelligent_bootstrap_files("python", None, "build API", "REST endpoints")
        assert isinstance(result, list)

    def test_get_intelligent_bootstrap_unknown_language_falls_back_to_generic(self) -> None:
        result = get_intelligent_bootstrap_files("cobol", None, "legacy", "old")
        assert isinstance(result, list)
        assert len(result) > 0


class TestPublicSurfaceReExports:
    def test_contracts_exported(self) -> None:
        assert hasattr(tasking_public, "CreateTaskCommandV1")
        assert hasattr(tasking_public, "CancelTaskCommandV1")
        assert hasattr(tasking_public, "TaskStatusQueryV1")
        assert hasattr(tasking_public, "TaskResultQueryV1")
        assert hasattr(tasking_public, "TaskCreatedResultV1")
        assert hasattr(tasking_public, "TaskStatusResultV1")
        assert hasattr(tasking_public, "TaskResultResultV1")
        assert hasattr(tasking_public, "DirectorTaskingError")

    def test_services_exported(self) -> None:
        assert hasattr(tasking_public, "TaskQueueConfig")
        assert hasattr(tasking_public, "TaskService")
        assert hasattr(tasking_public, "WorkerPoolConfig")
        assert hasattr(tasking_public, "WorkerService")


class TestInternalModuleReExports:
    def test_bootstrap_functions_exported(self) -> None:
        assert hasattr(tasking_internal, "get_generic_bootstrap_files")
        assert hasattr(tasking_internal, "get_intelligent_bootstrap_files")
        assert hasattr(tasking_internal, "get_python_bootstrap_files")
        assert hasattr(tasking_internal, "get_typescript_bootstrap_files")

    def test_task_lifecycle_exported(self) -> None:
        assert hasattr(tasking_internal, "TaskQueueConfig")
        assert hasattr(tasking_internal, "TaskService")
        assert hasattr(tasking_internal, "TaskServiceDeps")

    def test_worker_exported(self) -> None:
        assert hasattr(tasking_internal, "WorkerPoolConfig")
        assert hasattr(tasking_internal, "WorkerService")
        assert hasattr(tasking_internal, "WorkerExecutor")
        assert hasattr(tasking_internal, "CodeGenerationResult")
