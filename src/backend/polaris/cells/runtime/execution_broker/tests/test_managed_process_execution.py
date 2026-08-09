"""GR3B-B4/B5 tests for managed-process orchestrator (shipped public entry)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from polaris.bootstrap.managed_process_receipt_owner import configure_managed_process_receipt_owner
from polaris.cells.events.fact_stream.public.contracts import BootstrapFactStreamWorkspaceCommandV1
from polaris.cells.events.fact_stream.public.workspace_bootstrap import bootstrap_fact_stream_workspace
from polaris.cells.runtime.execution_broker.public import (
    ManagedProcessAuthorityV1,
    RunManagedProcessCommandV1,
    run_managed_process,
)


class _FakeRunner:
    def __init__(self) -> None:
        self.launches: list[tuple[str, ...]] = []
        self.terminates: list[str] = []
        self.waits: list[str] = []
        self.fail_launch = False
        self.timeout_on_wait = False
        self.exit_code = 0
        self.launch_count = 0

    async def launch(
        self,
        *,
        name: str,
        args: tuple[str, ...],
        workspace: str,
        timeout_seconds: float | None,
        env: dict[str, str] | Any,
    ) -> tuple[bool, str | None, str | None]:
        self.launch_count += 1
        self.launches.append(args)
        if self.fail_launch:
            return False, None, "inject_launch_fail"
        return True, f"exec-{self.launch_count}", None

    async def wait(
        self,
        execution_id: str,
        *,
        timeout_seconds: float | None,
    ) -> tuple[int | None, bool, bool]:
        self.waits.append(execution_id)
        if self.timeout_on_wait:
            return None, True, False
        return self.exit_code, False, self.exit_code == 0

    async def terminate(self, execution_id: str) -> bool:
        self.terminates.append(execution_id)
        return True


def _authority(
    *,
    effect_key: str = "effect-1",
    token: str = "lease-token",
    expires_in: float = 3600.0,
) -> ManagedProcessAuthorityV1:
    return ManagedProcessAuthorityV1(
        attempt_id="attempt-1",
        lease_id="lease-1",
        effect_key=effect_key,
        lease_expires_at_unix=time.time() + expires_in,
        authority_token=token,
    )


def _command(workspace: Path, **kwargs: Any) -> RunManagedProcessCommandV1:
    return RunManagedProcessCommandV1(
        workspace=str(workspace),
        run_id=str(kwargs.get("run_id") or "run-1"),
        name=str(kwargs.get("name") or "managed-demo"),
        args=tuple(kwargs.get("args") or ("echo", "ok")),
        authority=kwargs.get("authority") or _authority(effect_key=str(kwargs.get("effect_key") or "effect-1")),
        timeout_seconds=5.0,
        task_id="TASK-1",
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    configure_managed_process_receipt_owner()
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            streams=("execution.control_plane", "task_runtime.execution"),
            maintenance_reason="gr3b_b4_managed_process_execution_tests",
        )
    )
    return tmp_path


def test_fail_before_spawn_on_expired_lease(workspace: Path) -> None:
    runner = _FakeRunner()
    result = run_managed_process(
        _command(
            workspace,
            authority=_authority(token="t", expires_in=-10.0),
        ),
        runner=runner,
    )
    assert result.spawned is False
    assert result.code == "execution_broker.authority_lease_expired"
    assert result.details.get("fail_before_spawn") is True
    assert runner.launch_count == 0
    assert result.missing_evidence is True


def test_fail_before_spawn_on_missing_token(workspace: Path) -> None:
    runner = _FakeRunner()
    result = run_managed_process(
        _command(workspace, authority=_authority(token="")),
        runner=runner,
    )
    assert result.spawned is False
    assert result.code == "execution_broker.authority_token_missing"
    assert runner.launch_count == 0


def test_happy_path_receipt_and_ledger_projection(workspace: Path) -> None:
    runner = _FakeRunner()
    runner.exit_code = 0
    result = run_managed_process(_command(workspace), runner=runner)
    assert result.spawned is True
    assert result.process_ok is True
    assert result.receipt_hash
    assert result.receipt_ref
    assert result.missing_evidence is False
    assert result.evidence_presence == "present_succeeded"
    assert result.ledger_projected is True
    assert result.ledger_projection_pending is False
    assert runner.launch_count == 1


def test_nonzero_exit_is_present_failed_not_missing(workspace: Path) -> None:
    runner = _FakeRunner()
    runner.exit_code = 7
    result = run_managed_process(_command(workspace, effect_key="fx-fail"), runner=runner)
    assert result.spawned is True
    assert result.process_ok is False
    assert result.exit_code == 7
    assert result.missing_evidence is False
    assert result.evidence_presence == "present_failed"
    assert result.receipt_hash


def test_timeout_terminates_once_and_writes_failed_receipt(workspace: Path) -> None:
    runner = _FakeRunner()
    runner.timeout_on_wait = True
    result = run_managed_process(_command(workspace, effect_key="fx-timeout"), runner=runner)
    assert result.timed_out is True
    assert result.terminate_count == 1
    assert len(runner.terminates) == 1
    assert result.missing_evidence is False
    assert result.evidence_presence == "present_failed"
    # second call must not terminate again or re-spawn
    second = run_managed_process(_command(workspace, effect_key="fx-timeout"), runner=runner)
    assert second.spawned is False
    assert second.code == "execution_broker.managed_process_duplicate_launch_refused"
    assert runner.launch_count == 1
    assert len(runner.terminates) == 1


def test_duplicate_launch_refused(workspace: Path) -> None:
    runner = _FakeRunner()
    first = run_managed_process(_command(workspace, effect_key="fx-dup"), runner=runner)
    assert first.spawned is True
    second = run_managed_process(_command(workspace, effect_key="fx-dup"), runner=runner)
    assert second.spawned is False
    assert "duplicate_launch" in second.code
    assert runner.launch_count == 1


def test_projection_pending_does_not_respawn(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _FakeRunner()
    calls = {"n": 0}

    def _boom(*_a: Any, **_k: Any) -> Any:
        calls["n"] += 1
        raise RuntimeError("ledger down")

    monkeypatch.setattr(
        "polaris.cells.runtime.execution_broker.public.managed_process_execution.project_managed_process_lifecycle",
        _boom,
    )
    first = run_managed_process(_command(workspace, effect_key="fx-pend"), runner=runner)
    assert first.receipt_hash
    assert first.ledger_projection_pending is True
    assert first.ledger_projected is False
    assert first.missing_evidence is False
    assert runner.launch_count == 1

    # Retry: still no spawn; may re-attempt projection only
    second = run_managed_process(_command(workspace, effect_key="fx-pend"), runner=runner)
    assert second.spawned is False
    assert runner.launch_count == 1
    assert second.ledger_projection_pending is True or second.ledger_projected is True


def test_real_os_process_echo(workspace: Path) -> None:
    result = run_managed_process(
        RunManagedProcessCommandV1(
            workspace=str(workspace),
            run_id="run-real",
            name="echo-real",
            args=("echo", "managed-b4"),
            authority=_authority(effect_key="fx-real"),
            timeout_seconds=10.0,
        )
    )
    assert result.spawned is True
    assert result.exit_code == 0
    assert result.receipt_hash
    assert result.missing_evidence is False
    assert result.ledger_projected is True
