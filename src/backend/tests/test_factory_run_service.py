"""Tests for FactoryRunService and FactoryStore."""

import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    FactoryConfig,
    FactoryRun,
    FactoryRunService,
    FactoryRunStatus,
    OrchestrationStageExecutor,
    StageResult,
)
from polaris.cells.factory.pipeline.internal.factory_store import FactoryStore
from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import CommandResult
from polaris.kernelone.storage import resolve_runtime_path


class FakeStageExecutor:
    """Deterministic stage executor for FactoryRunService tests."""

    def __init__(self, fail_stages: set[str] | None = None) -> None:
        self.fail_stages = fail_stages or set()

    async def execute(self, stage: str, run: FactoryRun, context: dict) -> StageResult:
        if stage in self.fail_stages:
            return StageResult(
                stage=stage,
                status="failed",
                output=f"{stage} failed",
                artifacts=[],
            )

        return StageResult(
            stage=stage,
            status="success",
            output=f"{stage} completed",
            artifacts=[f"artifacts/{stage}.json"],
        )


class SlowStageExecutor:
    """Slow executor used to validate cancellation/heartbeat behavior."""

    def __init__(self, sleep_seconds: float = 0.2) -> None:
        self.sleep_seconds = sleep_seconds

    async def execute(self, stage: str, run: FactoryRun, context: dict) -> StageResult:
        await asyncio.sleep(self.sleep_seconds)
        return StageResult(
            stage=stage,
            status="success",
            output=f"{stage} completed slowly",
            artifacts=[],
        )


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
async def service(temp_workspace):
    """Create a FactoryRunService instance"""
    svc = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
    yield svc


@pytest.fixture
def store(temp_workspace):
    """Create a FactoryStore instance"""
    return FactoryStore(temp_workspace / ".polaris" / "factory")


class TestFactoryConfig:
    """Test FactoryConfig dataclass"""

    def test_default_config(self):
        config = FactoryConfig(name="test-run")
        assert config.name == "test-run"
        assert config.description is None
        assert config.stages == []
        assert config.auto_dispatch is True
        assert config.checkpoint_interval == 300

    def test_custom_config(self):
        config = FactoryConfig(
            name="full-run",
            description="A full factory run",
            stages=["docs_generation", "pm_planning"],
            auto_dispatch=False,
            checkpoint_interval=600,
        )
        assert config.name == "full-run"
        assert config.description == "A full factory run"
        assert config.stages == ["docs_generation", "pm_planning"]
        assert config.auto_dispatch is False
        assert config.checkpoint_interval == 600


class TestFactoryRun:
    """Test FactoryRun dataclass"""

    def test_to_dict(self):
        config = FactoryConfig(name="test-run")
        run = FactoryRun(
            id="factory_abc123", config=config, status=FactoryRunStatus.PENDING, created_at="2025-01-01T00:00:00"
        )

        data = run.to_dict()
        assert data["id"] == "factory_abc123"
        assert data["config"]["name"] == "test-run"
        assert data["status"] == "pending"
        assert data["created_at"] == "2025-01-01T00:00:00"

    def test_from_dict(self):
        data = {
            "id": "factory_xyz789",
            "config": {"name": "recovery-test", "stages": ["stage1"]},
            "status": "running",
            "created_at": "2025-01-01T12:00:00",
            "stages_completed": ["stage1"],
            "metadata": {"key": "value"},
        }

        run = FactoryRun.from_dict(data)
        assert run.id == "factory_xyz789"
        assert run.config.name == "recovery-test"
        assert run.status == FactoryRunStatus.RUNNING
        assert run.stages_completed == ["stage1"]
        assert run.metadata == {"key": "value"}


class TestFactoryStore:
    """Test FactoryStore persistence"""

    @pytest.mark.asyncio
    async def test_save_and_get_run(self, store, temp_workspace):
        config = FactoryConfig(name="test-run")
        run = FactoryRun(
            id="factory_test123",
            config=config,
            status=FactoryRunStatus.PENDING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        await store.save_run(run)

        # Verify file was created
        run_file = store.get_run_dir(run.id) / "run.json"
        assert run_file.exists()

        # Verify we can retrieve it
        retrieved = await store.get_run(run.id)
        assert retrieved is not None
        assert retrieved.id == run.id
        assert retrieved.config.name == run.config.name

    @pytest.mark.asyncio
    async def test_save_run_retries_windows_replace_conflict(self, store, monkeypatch):
        config = FactoryConfig(name="replace-retry")
        run = FactoryRun(
            id="factory_replace_retry",
            config=config,
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        original_replace = Path.replace
        attempts = {"count": 0}

        def flaky_replace(self: Path, target: Path):
            if self.name.startswith("run.json.") and attempts["count"] < 2:
                attempts["count"] += 1
                raise PermissionError("[WinError 5] Access is denied")
            return original_replace(self, target)

        monkeypatch.setattr(Path, "replace", flaky_replace)
        await store.save_run(run)

        saved = await store.get_run(run.id)
        assert saved is not None
        assert attempts["count"] == 2

    @pytest.mark.asyncio
    async def test_get_nonexistent_run(self, store):
        result = await store.get_run("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_checkpoint(self, store, temp_workspace):
        config = FactoryConfig(name="test-run")
        run = FactoryRun(
            id="factory_checkpoint_test",
            config=config,
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

        await store.checkpoint(run)

        # Verify checkpoint was created
        checkpoint_dir = store.get_run_dir(run.id) / "checkpoints"
        checkpoints = list(checkpoint_dir.glob("*.json"))
        assert len(checkpoints) == 1

    @pytest.mark.asyncio
    async def test_append_and_get_events(self, store, temp_workspace):
        run_id = "factory_events_test"

        # Create run directory first
        store.get_run_dir(run_id).mkdir(parents=True, exist_ok=True)

        # Append events
        event1 = {"type": "started", "timestamp": "2025-01-01T00:00:00"}
        event2 = {"type": "stage_completed", "stage": "test", "timestamp": "2025-01-01T00:01:00"}

        await store.append_event(run_id, event1)
        await store.append_event(run_id, event2)

        # Retrieve events
        events = await store.get_events(run_id)
        assert len(events) == 2
        assert events[0]["type"] == "started"
        assert events[1]["type"] == "stage_completed"

    def test_list_runs(self, store, temp_workspace):
        # Initially empty
        assert store.list_runs() == []

        # Create some run directories
        (store.base_dir / "run1").mkdir()
        (store.base_dir / "run2").mkdir()
        (store.base_dir / "not_a_run.txt").touch()

        runs = store.list_runs()
        assert len(runs) == 2
        assert "run1" in runs
        assert "run2" in runs


class TestFactoryRunService:
    """Test FactoryRunService"""

    @pytest.mark.asyncio
    async def test_create_run(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(name="test-run", stages=["stage1", "stage2"])

        run = await service.create_run(config)

        assert run.id.startswith("factory_")
        assert run.config.name == "test-run"
        assert run.status == FactoryRunStatus.PENDING
        assert run.created_at is not None

        # Verify directory structure
        run_dir = service.store.get_run_dir(run.id)
        assert (run_dir / "artifacts").exists()
        assert (run_dir / "events").exists()
        assert (run_dir / "checkpoints").exists()

    @pytest.mark.asyncio
    async def test_start_run(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(name="test-run")
        run = await service.create_run(config)

        started = await service.start_run(run.id)

        assert started.status == FactoryRunStatus.RUNNING
        assert started.started_at is not None

    @pytest.mark.asyncio
    async def test_execute_stage_success(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(name="test-run")
        run = await service.create_run(config)
        await service.start_run(run.id)

        result = await service.execute_stage(run.id, "docs_generation")

        assert result.stage == "docs_generation"
        assert result.status == "success"
        assert result.output is not None
        assert result.started_at is not None
        assert result.completed_at is not None

        # Verify run state updated
        updated_run = await service.get_run(run.id)
        assert "docs_generation" in updated_run.stages_completed
        assert updated_run.recovery_point == "docs_generation"
        assert updated_run.metadata["last_successful_stage"] == "docs_generation"

    @pytest.mark.asyncio
    async def test_execute_stage_cancellation_is_preserved(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=SlowStageExecutor(sleep_seconds=0.2))
        run = await service.create_run(FactoryConfig(name="test-run"))
        await service.start_run(run.id)

        stage_task = asyncio.create_task(
            service.execute_stage(
                run.id,
                "director_dispatch",
                context={"heartbeat_interval_seconds": 0.05},
            )
        )
        await asyncio.sleep(0.05)
        cancelled = await service.cancel_run(run.id, reason="operator stop")
        assert cancelled.status == FactoryRunStatus.CANCELLED

        result = await stage_task
        assert result.status == "cancelled"

        updated = await service.get_run(run.id)
        assert updated is not None
        assert updated.status == FactoryRunStatus.CANCELLED
        assert "director_dispatch" not in updated.stages_completed

    @pytest.mark.asyncio
    async def test_execute_stage_emits_heartbeat_events_for_long_stage(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=SlowStageExecutor(sleep_seconds=0.16))
        run = await service.create_run(FactoryConfig(name="test-run"))
        await service.start_run(run.id)

        result = await service.execute_stage(
            run.id,
            "pm_planning",
            context={"heartbeat_interval_seconds": 0.05},
        )
        assert result.status == "success"

        events = await service.get_run_events(run.id)
        heartbeat_events = [
            event for event in events if event.get("type") == "stage_heartbeat" and event.get("stage") == "pm_planning"
        ]
        assert heartbeat_events

    @pytest.mark.asyncio
    async def test_execute_stage_not_found(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())

        with pytest.raises(ValueError, match="Run nonexistent not found"):
            await service.execute_stage("nonexistent", "docs_generation")

    @pytest.mark.asyncio
    async def test_pause_and_resume(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(name="test-run")
        run = await service.create_run(config)
        await service.start_run(run.id)

        # Pause
        paused = await service.execute_pause(run.id)
        assert paused.status == FactoryRunStatus.PAUSED

        # Verify pause event
        events = await service.get_run_events(run.id)
        pause_events = [e for e in events if e["type"] == "paused"]
        assert len(pause_events) == 1

        # Resume
        resumed = await service.execute_resume(run.id)
        assert resumed.status == FactoryRunStatus.RUNNING

        # Verify resume event
        events = await service.get_run_events(run.id)
        resume_events = [e for e in events if e["type"] == "resumed"]
        assert len(resume_events) == 1

    @pytest.mark.asyncio
    async def test_complete_run(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(name="test-run")
        run = await service.create_run(config)
        await service.start_run(run.id)

        completed = await service.complete_run(run.id, success=True)

        assert completed.status == FactoryRunStatus.COMPLETED
        assert completed.completed_at is not None

        # Verify completion event
        events = await service.get_run_events(run.id)
        complete_events = [e for e in events if e["type"] == "completed"]
        assert len(complete_events) == 1
        assert complete_events[0]["success"] is True

    @pytest.mark.asyncio
    async def test_complete_run_keeps_cancelled_status(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        run = await service.create_run(FactoryConfig(name="test-run"))
        await service.start_run(run.id)
        await service.cancel_run(run.id, reason="operator stop")

        completed = await service.complete_run(run.id, success=True)
        assert completed.status == FactoryRunStatus.CANCELLED

        events = await service.get_run_events(run.id)
        complete_events = [event for event in events if event.get("type") == "completed"]
        assert complete_events == []

    @pytest.mark.asyncio
    async def test_list_runs(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())

        # Create multiple runs
        config1 = FactoryConfig(name="run-1")
        config2 = FactoryConfig(name="run-2")

        run1 = await service.create_run(config1)
        run2 = await service.create_run(config2)

        runs = await service.list_runs()

        assert len(runs) == 2
        run_ids = [r["id"] for r in runs]
        assert run1.id in run_ids
        assert run2.id in run_ids

    @pytest.mark.asyncio
    async def test_recover_run(self, temp_workspace):
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(name="test-run")
        run = await service.create_run(config)
        await service.start_run(run.id)

        # Execute a stage to have something to recover to
        await service.execute_stage(run.id, "docs_generation")

        # Simulate a crash by manually setting status back to RUNNING
        run = await service.get_run(run.id)
        run.status = FactoryRunStatus.RUNNING
        await service.store.save_run(run)

        # Recover
        recovered = await service.recover_run(run.id)

        assert recovered.status == FactoryRunStatus.RECOVERING
        assert recovered.recovery_point == "docs_generation"

    @pytest.mark.asyncio
    async def test_all_stage_handlers(self, temp_workspace):
        """Test all stage handlers return proper StageResult"""
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(name="test-run")
        run = await service.create_run(config)
        await service.start_run(run.id)

        stages = ["docs_generation", "pm_planning", "director_dispatch", "quality_gate"]

        for stage in stages:
            result = await service.execute_stage(run.id, stage)
            assert result.stage == stage
            assert result.status == "success"
            assert result.output is not None
            assert len(result.artifacts) > 0


class _ImmediateFailureCommandService:
    def __init__(self) -> None:
        self.queried = False

    async def query_run_status(self, run_id: str) -> CommandResult:
        self.queried = True
        return CommandResult(run_id=run_id, status="running", message="should not be queried")


class _DetailedFailureCommandService:
    def __init__(self) -> None:
        self.query_calls = 0

    async def execute_pm_run(self, workspace: str, run_type: str, options: dict) -> CommandResult:
        del workspace, run_type, options
        return CommandResult(
            run_id="pm-run-detailed-failure",
            status="running",
            message="PM run started",
        )

    async def query_run_status(self, run_id: str) -> CommandResult:
        self.query_calls += 1
        return CommandResult(
            run_id=run_id,
            status="failed",
            message=("Run status: failed | failed_task=task-0-pm (pm) | error=PM contract normalization failed"),
            metadata={
                "failed_task_count": 1,
                "failed_tasks": [
                    {
                        "task_id": "task-0-pm",
                        "role_id": "pm",
                        "status": "failed",
                        "error_category": "runtime",
                        "error_message": "PM contract normalization failed",
                    }
                ],
            },
        )


class _CompletedCommandService:
    def __init__(self) -> None:
        self.query_calls = 0

    async def execute_pm_run(self, workspace: str, run_type: str, options: dict) -> CommandResult:
        del workspace, run_type, options
        return CommandResult(
            run_id="pm-run-completed",
            status="running",
            message="PM run started",
        )

    async def execute_qa_run(self, workspace: str, target: str, options: dict) -> CommandResult:
        del workspace, target, options
        return CommandResult(
            run_id="qa-run-completed",
            status="running",
            message="QA run started",
        )

    async def execute_director_run(self, workspace: str, tasks: list | None, options: dict) -> CommandResult:
        del workspace, tasks, options
        return CommandResult(
            run_id="director-run-completed",
            status="running",
            message="Director run started",
        )

    async def query_run_status(self, run_id: str) -> CommandResult:
        self.query_calls += 1
        return CommandResult(
            run_id=run_id,
            status="completed",
            message="Run status: completed",
            metadata={},
        )


class _NeverTerminalCommandService:
    def __init__(self) -> None:
        self.query_calls = 0

    async def query_run_status(self, run_id: str) -> CommandResult:
        self.query_calls += 1
        return CommandResult(
            run_id=run_id,
            status="running",
            message="still running",
            metadata={},
        )


class _DirectorFailedCommandService(_CompletedCommandService):
    async def query_run_status(self, run_id: str) -> CommandResult:
        self.query_calls += 1
        return CommandResult(
            run_id=run_id,
            status="failed",
            message="Run status: failed | failed_task=task-0-director (director) | error=tool_failed",
            metadata={
                "failed_task_count": 1,
                "task_status_counts": {"failed": 1},
            },
        )


class _DirectorCompletedMetadataProgressService(_CompletedCommandService):
    async def query_run_status(self, run_id: str) -> CommandResult:
        self.query_calls += 1
        return CommandResult(
            run_id=run_id,
            status="completed",
            message="Run status: completed",
            metadata={
                "task_count": 1,
                "task_status_counts": {"completed": 1},
                "failed_task_count": 0,
            },
        )


class _TestStageExecutor(OrchestrationStageExecutor):
    def __init__(self, workspace: Path, command_service: object) -> None:
        super().__init__(workspace)
        self._command_service = command_service

    def _build_orchestration_service(self, context: dict):
        return self._command_service


class TestOrchestrationStageExecutor:
    @pytest.mark.asyncio
    async def test_poll_run_completion_short_circuits_immediate_failure(self, temp_workspace):
        command_service = _ImmediateFailureCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        initial = CommandResult(
            run_id="pm-test-001",
            status="failed",
            message="No module named 'pytz'",
            reason_code="PM_RUN_FAILED",
        )

        result = await executor._poll_run_completion(command_service, initial, timeout_seconds=60)

        assert result is initial
        assert command_service.queried is False

    @pytest.mark.asyncio
    async def test_poll_run_completion_honors_abort_checker(self, temp_workspace):
        command_service = _NeverTerminalCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        initial = CommandResult(
            run_id="pm-test-cancelled",
            status="running",
            message="PM run started",
        )

        async def _abort_checker() -> str | None:
            return "operator stop"

        result = await executor._poll_run_completion(
            command_service,
            initial,
            timeout_seconds=60,
            poll_interval=0.01,
            abort_checker=_abort_checker,
        )

        assert result.status == "cancelled"
        assert "operator stop" in str(result.message)
        assert command_service.query_calls == 0

    @pytest.mark.asyncio
    async def test_docs_stage_uses_extended_default_timeout_budget(self, temp_workspace, monkeypatch):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_docs_timeout_budget",
            config=FactoryConfig(name="test-run", stages=["docs_generation"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        captured: dict[str, int] = {}

        async def _fake_poll(service, command_result, timeout_seconds, abort_checker):
            del service, abort_checker
            captured["timeout_seconds"] = int(timeout_seconds)
            return CommandResult(
                run_id=command_result.run_id,
                status="completed",
                message="Run status: completed",
                metadata={},
            )

        monkeypatch.setattr(executor, "_poll_run_completion", _fake_poll)
        monkeypatch.setattr(executor, "_ensure_docs_artifacts", lambda directive, summary: [])
        monkeypatch.setattr(executor, "_artifact_exists", lambda relative_path, min_chars=1: True)

        result = await executor._execute_docs_generation(run, context={"directive": "Generate docs"})

        assert result.status == "success"
        assert captured["timeout_seconds"] == 600

    @pytest.mark.asyncio
    async def test_pm_stage_uses_extended_default_timeout_budget(self, temp_workspace, monkeypatch):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_pm_timeout_budget",
            config=FactoryConfig(name="test-run", stages=["pm_planning"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        captured: dict[str, int] = {}

        async def _fake_poll(service, command_result, timeout_seconds, abort_checker):
            del service, abort_checker
            captured["timeout_seconds"] = int(timeout_seconds)
            return CommandResult(
                run_id=command_result.run_id,
                status="completed",
                message="Run status: completed",
                metadata={},
            )

        monkeypatch.setattr(executor, "_poll_run_completion", _fake_poll)
        monkeypatch.setattr(executor, "_validate_pm_plan_contract", lambda relative_path: None)
        monkeypatch.setattr(executor, "_artifact_exists", lambda relative_path, min_chars=1: True)

        result = await executor._execute_pm_planning(run, context={"directive": "Plan implementation tasks"})

        assert result.status == "success"
        assert captured["timeout_seconds"] == 600

    @pytest.mark.asyncio
    async def test_pm_stage_failure_output_includes_failed_task_details(self, temp_workspace):
        command_service = _DetailedFailureCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_pm_failure",
            config=FactoryConfig(name="test-run", stages=["pm_planning"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        result = await executor._execute_pm_planning(
            run,
            context={"directive": "Plan implementation tasks"},
        )

        assert result.status == "failed"
        assert "failed_task=task-0-pm (pm)" in str(result.output)
        assert "PM contract normalization failed" in str(result.output)
        assert "signals=" in str(result.output)
        assert "runtime/signals/pm_planning.signals.json" in result.artifacts
        signal_path = Path(
            resolve_runtime_path(
                str(temp_workspace),
                "runtime/signals/pm_planning.signals.json",
            )
        )
        payload = json.loads(signal_path.read_text(encoding="utf-8"))
        rows = payload.get("signals") if isinstance(payload, dict) else []
        assert isinstance(rows, list)
        codes = {str(item.get("code") or "") for item in rows if isinstance(item, dict)}
        assert "pm.run_status_non_success" in codes
        assert "pm.contract_issue_detected" in codes
        assert command_service.query_calls >= 1

    @pytest.mark.asyncio
    async def test_pm_stage_requires_materialized_plan_artifact(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_pm_missing_plan",
            config=FactoryConfig(name="test-run", stages=["pm_planning"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        result = await executor._execute_pm_planning(
            run,
            context={"directive": "Plan implementation tasks"},
        )

        assert result.status == "failed"
        assert "signals=" in str(result.output)
        assert "runtime/signals/pm_planning.signals.json" in result.artifacts
        signal_path = Path(
            resolve_runtime_path(
                str(temp_workspace),
                "runtime/signals/pm_planning.signals.json",
            )
        )
        payload = json.loads(signal_path.read_text(encoding="utf-8"))
        rows = payload.get("signals") if isinstance(payload, dict) else []
        assert isinstance(rows, list)
        assert any(
            isinstance(item, dict)
            and str(item.get("code") or "") == "pm.contract_issue_detected"
            and "missing_tasks_plan" in str(item.get("detail") or "")
            for item in rows
        )

    @pytest.mark.asyncio
    async def test_pm_stage_accepts_valid_plan_artifact(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_pm_valid_plan",
            config=FactoryConfig(name="test-run", stages=["pm_planning"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            """{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "实现账户实体",
      "goal": "完成账单核心实体与校验",
      "scope": "src/account",
      "steps": ["实现实体", "补充测试"],
      "acceptance": ["`pytest` 通过", "接口返回字段正确"]
    }
  ]
}
""",
            encoding="utf-8",
        )

        result = await executor._execute_pm_planning(
            run,
            context={"directive": "Plan implementation tasks"},
        )

        assert result.status == "success"
        assert result.artifacts == ["tasks/plan.json"]

    @pytest.mark.asyncio
    async def test_director_stage_fails_when_plan_lineage_missing(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_director_missing_plan",
            config=FactoryConfig(name="test-run", stages=["director_dispatch"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        result = await executor._execute_director_dispatch(
            run,
            context={},
        )

        assert result.status == "failed"
        assert "error_code=director.task_lineage_missing" in str(result.output)
        assert "dispatch/log.json" in result.artifacts

    @pytest.mark.asyncio
    async def test_director_stage_fails_when_upstream_run_non_success(self, temp_workspace):
        command_service = _DirectorFailedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_director_failed_run",
            config=FactoryConfig(name="test-run", stages=["director_dispatch"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            """{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "实现账户实体",
      "goal": "完成账单核心实体与校验",
      "scope": "src/account",
      "steps": ["实现实体", "补充测试"],
      "acceptance": ["`pytest` 通过", "接口返回字段正确"]
    }
  ]
}
""",
            encoding="utf-8",
        )
        task_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/task_1.json"))
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            """{
  "id": 1,
  "subject": "实现账户实体",
  "description": "实现与测试",
  "status": "pending",
  "created_at": 1735689600.0,
  "updated_at": 1735689600.0,
  "blocked_by": [],
  "blocks": [],
  "owner": "",
  "assignee": "",
  "tags": [],
  "priority": 1,
  "estimated_hours": 2.0,
  "result_summary": "",
  "metadata": {}
}
""",
            encoding="utf-8",
        )

        result = await executor._execute_director_dispatch(
            run,
            context={"director_max_rounds": 1},
        )

        assert result.status == "failed"
        assert "error_code=director.run_status_non_success" in str(result.output)
        assert "dispatch/log.json" in result.artifacts

    @pytest.mark.asyncio
    async def test_director_stage_accepts_metadata_execution_evidence_without_taskboard_progress(
        self,
        temp_workspace,
    ):
        command_service = _DirectorCompletedMetadataProgressService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_director_metadata_progress",
            config=FactoryConfig(name="test-run", stages=["director_dispatch"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            """{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "实现账户实体",
      "goal": "完成账单核心实体与校验",
      "scope": "src/account",
      "steps": ["实现实体", "补充测试"],
      "acceptance": ["`pytest` 通过", "接口返回字段正确"]
    }
  ]
}
""",
            encoding="utf-8",
        )
        task_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/task_1.json"))
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            """{
  "id": 1,
  "subject": "实现账户实体",
  "description": "实现与测试",
  "status": "pending",
  "created_at": 1735689600.0,
  "updated_at": 1735689600.0,
  "blocked_by": [],
  "blocks": [],
  "owner": "",
  "assignee": "",
  "tags": [],
  "priority": 1,
  "estimated_hours": 2.0,
  "result_summary": "",
  "metadata": {}
}
""",
            encoding="utf-8",
        )

        result = await executor._execute_director_dispatch(
            run,
            context={"director_max_rounds": 2},
        )

        assert result.status == "success"
        assert "error_code=none" in str(result.output)
        assert "dispatch/log.json" in result.artifacts

    @pytest.mark.asyncio
    async def test_quality_gate_uses_report_verdict(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_quality_gate",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            """{
  "passed": false,
  "score": 52,
  "critical_issue_count": 2
}
""",
            encoding="utf-8",
        )

        result = await executor._execute_quality_gate(
            run,
            context={"qa_target": "Quality gate"},
        )

        assert result.status == "failed"
        assert "qa_passed=False" in str(result.output)
        assert result.artifacts == ["runtime/qa/report.json"]

    @pytest.mark.asyncio
    async def test_default_stage_handler(self, temp_workspace):
        """Test unknown stage uses default handler"""
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(name="test-run")
        run = await service.create_run(config)
        await service.start_run(run.id)

        result = await service.execute_stage(run.id, "unknown_stage")

        assert result.status == "skipped"
        assert "No handler" in result.output

    @pytest.mark.asyncio
    async def test_execute_stage_failure_sets_failure_metadata(self, temp_workspace):
        service = FactoryRunService(
            temp_workspace,
            executor=FakeStageExecutor(fail_stages={"quality_gate"}),
        )
        run = await service.create_run(FactoryConfig(name="test-run"))
        await service.start_run(run.id)

        result = await service.execute_stage(run.id, "quality_gate")

        assert result.status == "failed"
        updated_run = await service.get_run(run.id)
        assert updated_run.status == FactoryRunStatus.FAILED
        assert updated_run.metadata["last_failed_stage"] == "quality_gate"
        assert updated_run.metadata["failure"]["code"] == "FACTORY_STAGE_FAILED"


class TestFactoryRunStatus:
    """Test FactoryRunStatus enum"""

    def test_status_values(self):
        assert FactoryRunStatus.PENDING == "pending"
        assert FactoryRunStatus.RUNNING == "running"
        assert FactoryRunStatus.PAUSED == "paused"
        assert FactoryRunStatus.COMPLETED == "completed"
        assert FactoryRunStatus.FAILED == "failed"
        assert FactoryRunStatus.RECOVERING == "recovering"

    def test_status_from_string(self):
        assert FactoryRunStatus("pending") == FactoryRunStatus.PENDING
        assert FactoryRunStatus("running") == FactoryRunStatus.RUNNING
