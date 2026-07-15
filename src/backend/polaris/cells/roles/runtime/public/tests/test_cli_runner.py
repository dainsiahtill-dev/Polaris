"""Task-mode CLI coverage for canonical TaskRuntime execution attempts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.public.cli_runner import CliRunner
from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1
from polaris.cells.roles.runtime.public.service import RoleRuntimeService
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService


@pytest.mark.asyncio
async def test_execute_role_task_mode_claims_canonical_attempt_and_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import polaris.cells.roles.runtime.public.cli_runner as cli_runner_module

    runtime = RoleRuntimeService()
    captured: list[Any] = []
    call_order: list[str] = []

    original_bootstrap = cli_runner_module.bootstrap_fact_stream_workspace
    original_ensure_task_row = TaskRuntimeService.ensure_task_row

    def record_bootstrap(command: Any) -> Any:
        call_order.append("bootstrap")
        assert command.maintenance_reason == "roles_runtime_cli_execute_startup"
        return original_bootstrap(command)

    def record_ensure_task_row(self: TaskRuntimeService, *args: Any, **kwargs: Any) -> dict[str, Any]:
        call_order.append("task_runtime_io")
        return original_ensure_task_row(self, *args, **kwargs)

    monkeypatch.setattr(cli_runner_module, "bootstrap_fact_stream_workspace", record_bootstrap)
    monkeypatch.setattr(TaskRuntimeService, "ensure_task_row", record_ensure_task_row)

    async def fake_execute(command: Any) -> RoleExecutionResultV1:
        captured.append(command)
        return RoleExecutionResultV1(
            ok=True,
            status="ok",
            role="director",
            workspace=str(tmp_path),
            task_id="CLI-41",
            run_id="run-41",
            output="completed",
        )

    monkeypatch.setattr(runtime, "execute_role_task", fake_execute)

    result = await CliRunner(runtime).execute_role(
        "director",
        {
            "workspace": str(tmp_path),
            "task_id": "CLI-41",
            "run_id": "run-41",
            "message": "complete the CLI task",
        },
    )

    assert result["ok"] is True
    assert call_order.index("bootstrap") < call_order.index("task_runtime_io")
    assert len(captured) == 1
    command = captured[0]
    identity = command.execution_attempt
    assert identity is not None
    assert identity.external_task_id == "CLI-41"
    assert identity.role_id == "director"
    assert identity.run_id == "run-41"

    terminal_claim = TaskRuntimeService(str(tmp_path)).claim_execution(
        identity.task_id,
        worker_id="director-worker",
        role_id="director",
        run_id="run-41",
        external_task_id="CLI-41",
    )
    assert terminal_claim["success"] is False
    assert terminal_claim["reason"] == "task_terminal"


@pytest.mark.asyncio
async def test_execute_role_task_mode_requires_caller_scope(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="task_id is required"):
        await CliRunner(RoleRuntimeService()).execute_role(
            "director",
            {"workspace": str(tmp_path), "message": "unscoped task"},
        )
