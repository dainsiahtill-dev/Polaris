from __future__ import annotations

import asyncio
import importlib
import time
from pathlib import Path

import pytest
from polaris.bootstrap.config import Settings
from polaris.cells.orchestration.pm_planning.public.service import PMService, ProcessHandle
from polaris.cells.runtime.execution_broker.public.contracts import (
    ExecutionProcessHandleV1,
    ExecutionProcessLaunchResultV1,
    ExecutionProcessStatusV1,
)
from polaris.domain.exceptions import ProcessAlreadyRunningError
from polaris.kernelone.storage import StorageLayout


class _FakeRunningProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0

    def kill(self) -> None:
        return None


class _FakeFinishedProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.terminated = False

    def poll(self) -> int:
        return 0

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0

    def kill(self) -> None:
        self.terminated = True


@pytest.mark.asyncio
async def test_pm_run_once_is_single_flight_under_concurrency(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    settings = Settings(workspace=str(workspace))
    storage = StorageLayout(settings.workspace, settings.runtime_base)
    service = PMService(settings, storage)

    async def _fake_check_backend_available() -> None:
        return None

    async def _fake_clear_stop_flag() -> None:
        return None

    spawn_count = 0

    async def _fake_spawn_process(_cmd, log_path: str) -> ProcessHandle:
        nonlocal spawn_count
        spawn_count += 1
        # Keep start window open to expose race conditions.
        await asyncio.sleep(0.05)
        return ProcessHandle(
            process=_FakeRunningProcess(pid=9000 + spawn_count),
            log_handle=None,
            log_path=log_path,
            started_at=time.time(),
            execution_id=f"exec-{spawn_count}",
        )

    service._check_backend_available = _fake_check_backend_available  # type: ignore[method-assign]
    service._clear_stop_flag = _fake_clear_stop_flag  # type: ignore[method-assign]
    service._build_command = lambda loop_mode, resume=False: ["python", "-V"]  # type: ignore[method-assign]
    service._resolve_log_path = lambda: str(tmp_path / "pm.process.log")  # type: ignore[method-assign]
    service._spawn_process = _fake_spawn_process  # type: ignore[method-assign]

    results = await asyncio.gather(
        service.run_once(),
        service.run_once(),
        return_exceptions=True,
    )

    success = [item for item in results if isinstance(item, dict) and item.get("ok") is True]
    failures = [item for item in results if isinstance(item, ProcessAlreadyRunningError)]

    assert len(success) == 1
    assert len(failures) == 1
    assert spawn_count == 1
    response = success[0]
    assert response["execution_id"] == "exec-1"
    assert response["mode"] == "run_once"
    assert response["log_path"].endswith("pm.process.log")
    assert Path(response["contract_path"]).name == "pm_tasks.contract.json"


@pytest.mark.parametrize(
    "broker_status",
    [ExecutionProcessStatusV1.QUEUED, ExecutionProcessStatusV1.RUNNING],
)
def test_pm_status_uses_broker_active_state_when_process_handle_is_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    broker_status: ExecutionProcessStatusV1,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    settings = Settings(workspace=str(workspace))
    service = PMService(settings, StorageLayout(settings.workspace, settings.runtime_base))
    process = _FakeFinishedProcess(pid=4321)
    service._handle = ProcessHandle(
        process=process,
        log_path=str(tmp_path / "pm.process.log"),
        started_at=time.time(),
        mode="run_once",
        execution_id="exec-active",
    )

    class FakeBroker:
        def get_process_status(self, query) -> ExecutionProcessStatusV1:
            assert query.execution_id == "exec-active"
            return broker_status

    pm_service_module = importlib.import_module("polaris.cells.orchestration.pm_planning.service")
    monkeypatch.setattr(pm_service_module, "get_execution_broker_service", lambda: FakeBroker())

    status = service.get_status()

    assert status["running"] is True
    assert status["status"] == broker_status.value
    assert status["source"] == "execution_broker"
    assert status["execution_id"] == "exec-active"
    assert service.handle.execution_id == "exec-active"
    assert process.terminated is False


@pytest.mark.asyncio
async def test_pm_run_once_rejects_duplicate_when_broker_is_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    settings = Settings(workspace=str(workspace))
    service = PMService(settings, StorageLayout(settings.workspace, settings.runtime_base))
    service._handle = ProcessHandle(
        process=_FakeFinishedProcess(pid=4321),
        log_path=str(tmp_path / "pm.process.log"),
        started_at=time.time(),
        mode="run_once",
        execution_id="exec-running",
    )

    class FakeBroker:
        def get_process_status(self, query) -> ExecutionProcessStatusV1:
            assert query.execution_id == "exec-running"
            return ExecutionProcessStatusV1.RUNNING

    pm_service_module = importlib.import_module("polaris.cells.orchestration.pm_planning.service")
    monkeypatch.setattr(pm_service_module, "get_execution_broker_service", lambda: FakeBroker())

    with pytest.raises(ProcessAlreadyRunningError):
        await service.run_once()


def test_pm_status_falls_back_to_process_handle_when_broker_lookup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    settings = Settings(workspace=str(workspace))
    service = PMService(settings, StorageLayout(settings.workspace, settings.runtime_base))
    service._handle = ProcessHandle(
        process=_FakeRunningProcess(pid=4321),
        log_path=str(tmp_path / "pm.process.log"),
        started_at=time.time(),
        mode="run_once",
        execution_id="exec-missing",
    )

    class FakeBroker:
        def get_process_status(self, query) -> ExecutionProcessStatusV1:
            assert query.execution_id == "exec-missing"
            raise RuntimeError("execution not found")

    pm_service_module = importlib.import_module("polaris.cells.orchestration.pm_planning.service")
    monkeypatch.setattr(pm_service_module, "get_execution_broker_service", lambda: FakeBroker())

    status = service.get_status()

    assert status["running"] is True
    assert status["status"] is None
    assert status["source"] == "handle"


def test_pm_build_command_includes_director_workflow_controls(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    settings = Settings(workspace=str(workspace))
    settings.pm.runs_director = True
    settings.director.execution_mode = "serial"
    settings.director.max_parallel_tasks = 7
    settings.director.ready_timeout_seconds = 11
    settings.director.claim_timeout_seconds = 12
    settings.director.phase_timeout_seconds = 130
    settings.director.complete_timeout_seconds = 14
    settings.director.task_timeout_seconds = 900

    storage = StorageLayout(settings.workspace, settings.runtime_base)
    service = PMService(settings, storage)

    cmd = service._build_command(loop_mode=False)
    joined = " ".join(cmd)

    assert "--director-workflow-execution-mode serial" in joined
    assert "--director-max-parallel-tasks 7" in joined
    assert "--director-ready-timeout-seconds 11" in joined
    assert "--director-claim-timeout-seconds 12" in joined
    assert "--director-phase-timeout-seconds 130" in joined
    assert "--director-complete-timeout-seconds 14" in joined
    assert "--director-task-timeout-seconds 900" in joined


def test_pm_build_command_uses_existing_migrated_cli(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    settings = Settings(workspace=str(workspace))
    service = PMService(settings, StorageLayout(settings.workspace, settings.runtime_base))

    cmd = service._build_command(loop_mode=False)
    script_path = Path(cmd[1])

    assert script_path.exists()
    assert script_path.as_posix().endswith("src/backend/polaris/delivery/cli/pm/cli.py")


def test_pm_service_refreshes_storage_layout_when_workspace_changes(tmp_path) -> None:
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir(parents=True, exist_ok=True)
    workspace_b.mkdir(parents=True, exist_ok=True)

    settings = Settings(workspace=str(workspace_a))
    service = PMService(settings, StorageLayout(settings.workspace, settings.runtime_base))

    expected_a = (
        StorageLayout(Path(str(workspace_a)), settings.runtime_base).get_path("logs", "pm.process.log").resolve()
    )
    resolved_a = Path(service._resolve_log_path()).resolve()
    assert resolved_a == expected_a

    settings.workspace = workspace_b
    service.refresh_storage_layout()

    expected_b = (
        StorageLayout(Path(str(workspace_b)), settings.runtime_base).get_path("logs", "pm.process.log").resolve()
    )
    resolved_b = Path(service._resolve_log_path()).resolve()
    assert resolved_b == expected_b


def test_pm_service_rebinds_to_application_settings_object(tmp_path: Path) -> None:
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir(parents=True, exist_ok=True)
    workspace_b.mkdir(parents=True, exist_ok=True)

    original_settings = Settings(workspace=str(workspace_a))
    service = PMService(original_settings, StorageLayout(original_settings.workspace, original_settings.runtime_base))

    updated_settings = Settings(workspace=str(workspace_b))
    service.rebind_settings(updated_settings)

    resolved_log_path = Path(service._resolve_log_path()).resolve()
    expected_log_path = (
        StorageLayout(Path(str(workspace_b)), updated_settings.runtime_base)
        .get_path("logs", "pm.process.log")
        .resolve()
    )
    assert resolved_log_path == expected_log_path
    assert Path(str(service._settings.workspace)).resolve() == workspace_b.resolve()


def test_pm_service_build_command_uses_runtime_json_log_rel_path(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    settings = Settings(workspace=str(workspace))
    service = PMService(settings, StorageLayout(settings.workspace, settings.runtime_base))

    cmd = service._build_command(loop_mode=False)
    assert "--json-log" in cmd
    json_log_value = cmd[cmd.index("--json-log") + 1]
    assert json_log_value == "runtime/events/pm.events.jsonl"


@pytest.mark.asyncio
async def test_pm_spawn_process_propagates_runtime_cache_root(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    settings = Settings(workspace=str(workspace))
    service = PMService(settings, StorageLayout(settings.workspace, settings.runtime_base))

    launched_env: dict[str, str] = {}

    class FakeBroker:
        async def launch_process(self, command):
            launched_env.update(dict(command.env))
            handle = ExecutionProcessHandleV1(
                execution_id="exec-1",
                pid=1234,
                name="pm-service",
                workspace=str(workspace),
                log_path=command.log_path,
            )
            return ExecutionProcessLaunchResultV1(success=True, handle=handle)

        def resolve_runtime_process(self, _handle):
            return _FakeRunningProcess(pid=1234)

    pm_service_module = importlib.import_module("polaris.cells.orchestration.pm_planning.service")
    monkeypatch.setattr(pm_service_module, "get_execution_broker_service", lambda: FakeBroker())

    await service._spawn_process(["python", "-V"], str(tmp_path / "pm.process.log"))

    assert launched_env["KERNELONE_RUNTIME_CACHE_ROOT"] == str(settings.runtime_base)
    assert launched_env["KERNELONE_WORKSPACE"] == str(workspace.resolve())


def test_pm_service_prefers_persisted_workspace_when_configured_workspace_is_default(
    tmp_path,
    monkeypatch,
) -> None:
    workspace_stale = tmp_path / "workspace-stale"
    workspace_target = tmp_path / "workspace-target"
    workspace_stale.mkdir(parents=True, exist_ok=True)
    workspace_target.mkdir(parents=True, exist_ok=True)

    settings = Settings(workspace=str(workspace_stale))
    service = PMService(settings, StorageLayout(settings.workspace, settings.runtime_base))
    pm_service_module = importlib.import_module("polaris.cells.orchestration.pm_planning.service")

    monkeypatch.setattr(
        pm_service_module,
        "find_workspace_root",
        lambda _start: workspace_stale,
    )
    monkeypatch.setattr(
        "polaris.cells.storage.layout.public.service.load_persisted_settings",
        lambda _workspace="": {"workspace": str(workspace_target)},
    )

    cmd = service._build_command(loop_mode=False)
    workspace_arg = cmd[cmd.index("--workspace") + 1]
    assert Path(workspace_arg).resolve() == workspace_target.resolve()
    assert Path(str(settings.workspace)).resolve() == workspace_target.resolve()
