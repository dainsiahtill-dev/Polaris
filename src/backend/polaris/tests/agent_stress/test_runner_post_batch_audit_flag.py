from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from .backend_bootstrap import ManagedBackendSession
from .backend_context import BackendContext
from .engine import RoundResult
from .project_pool import PROJECT_POOL, ProjectDefinition


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _FakeEngine:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    async def run_round(
        self,
        round_index: int,
        project: ProjectDefinition,
        remediation_notes: str = "",
        start_from_override: str = "architect",
    ) -> RoundResult:
        del remediation_notes
        project_workspace = self._workspace / "projects" / project.id
        project_workspace.mkdir(parents=True, exist_ok=True)
        return RoundResult(
            round_number=round_index,
            project=project,
            start_time=_utc_now_iso(),
            entry_stage=start_from_override,
            end_time=_utc_now_iso(),
            overall_result="PASS",
            workspace_artifacts={"workspace": str(project_workspace)},
        )


@pytest.mark.asyncio
async def test_project_serial_skips_batch_audit_when_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from .runner import AgentStressRunner

    runner = AgentStressRunner(
        workspace=tmp_path,
        rounds=1,
        attempts_per_project=1,
        round_batch_limit=1,
        post_batch_audit=False,
    )

    audit_calls = 0

    async def _save_intermediate_results() -> None:
        return None

    async def _run_batch_audit_and_pause(*args, **kwargs) -> None:
        del args, kwargs
        nonlocal audit_calls
        audit_calls += 1

    monkeypatch.setattr(runner, "_save_intermediate_results", _save_intermediate_results)
    monkeypatch.setattr(runner, "_run_batch_audit_and_pause", _run_batch_audit_and_pause)

    await runner._run_project_serial(_FakeEngine(tmp_path), [PROJECT_POOL[0]])

    assert audit_calls == 0


@pytest.mark.asyncio
async def test_project_serial_runs_batch_audit_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from .runner import AgentStressRunner

    runner = AgentStressRunner(
        workspace=tmp_path,
        rounds=1,
        attempts_per_project=1,
        round_batch_limit=1,
        post_batch_audit=True,
    )

    audit_calls = 0

    async def _save_intermediate_results() -> None:
        return None

    async def _run_batch_audit_and_pause(*args, **kwargs) -> None:
        del args, kwargs
        nonlocal audit_calls
        audit_calls += 1

    monkeypatch.setattr(runner, "_save_intermediate_results", _save_intermediate_results)
    monkeypatch.setattr(runner, "_run_batch_audit_and_pause", _run_batch_audit_and_pause)

    await runner._run_project_serial(_FakeEngine(tmp_path), [PROJECT_POOL[0]])

    assert audit_calls == 1


@pytest.mark.asyncio
async def test_runner_forwards_fresh_workspace_to_backend_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from .runner import DEFAULT_STRESS_RAMDISK, AgentStressRunner

    workspace = tmp_path / "stress-workspace"
    runner = AgentStressRunner(
        workspace=workspace,
        rounds=1,
    )
    captured: dict[str, object] = {}

    async def _fake_ensure_backend_session(**kwargs):
        captured.update(kwargs)
        return ManagedBackendSession(
            context=BackendContext(
                backend_url="http://127.0.0.1:51236",
                token="bootstrap-token",
                source="terminal-auto-bootstrap",
            ),
            auto_bootstrapped=True,
            startup_workspace=str(kwargs["startup_workspace"]),
            ramdisk_root=str(kwargs["ramdisk_root"]),
        )

    monkeypatch.setattr("polaris.tests.agent_stress.runner.ensure_backend_session", _fake_ensure_backend_session)

    await runner._ensure_backend_session()

    assert Path(captured["startup_workspace"]) == workspace.resolve()
    assert Path(captured["ramdisk_root"]) == Path(DEFAULT_STRESS_RAMDISK).resolve()
