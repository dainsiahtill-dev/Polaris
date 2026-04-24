from __future__ import annotations

import asyncio
import importlib
import time
from pathlib import Path

import pytest
from polaris.bootstrap.config import Settings
from polaris.cells.orchestration.pm_planning.public.service import PMService, ProcessHandle
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


def test_pm_service_build_command_uses_runtime_json_log_rel_path(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    settings = Settings(workspace=str(workspace))
    service = PMService(settings, StorageLayout(settings.workspace, settings.runtime_base))

    cmd = service._build_command(loop_mode=False)
    assert "--json-log" in cmd
    json_log_value = cmd[cmd.index("--json-log") + 1]
    assert json_log_value == "runtime/events/pm.events.jsonl"


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
