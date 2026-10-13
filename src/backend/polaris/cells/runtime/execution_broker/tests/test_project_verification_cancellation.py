"""Cancellation reaches the existing physical verifier owner without success receipts."""

from __future__ import annotations

import asyncio
import inspect
import time
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

import pytest
from polaris.cells.runtime.execution_broker.internal import project_verification_authority as owner
from polaris.cells.runtime.execution_broker.internal.project_verification_sandbox import (
    PreparedProjectVerificationSandbox,
)
from polaris.cells.runtime.execution_broker.internal.service import ExecutionBrokerService
from polaris.cells.runtime.execution_broker.public import project_verification as broker
from polaris.cells.runtime.execution_broker.public.contracts import (
    ExecutionProcessHandleV1,
    ExecutionProcessLaunchResultV1,
    ExecutionProcessStatusV1,
    ExecutionProcessWaitResultV1,
    LaunchExecutionProcessCommandV1,
)
from polaris.cells.runtime.execution_broker.tests.test_project_verification_receipts import (
    _authority_hash,
    _AuthorityPort,
    _bind_runner,
    _Runner,
    _verification_command,
    _verification_query,
)
from polaris.kernelone.process import ProcessTreeCancelledError, ProcessTreeDrainError, ProcessTreeRunControl


def _source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('original')\n", encoding="utf-8")
    monkeypatch.setattr(owner, "_EXECUTION_AUTHORITY_PORT", _AuthorityPort())


def _command_authority(monkeypatch: pytest.MonkeyPatch, argv: tuple[str, ...]) -> None:
    authority_port = _AuthorityPort(timeout_seconds=40.0)
    resolve_authority = authority_port.resolve_project_verification_authority

    def resolve(
        query: broker.ResolveProjectVerificationAuthorityQueryV1,
    ) -> broker.ProjectVerificationExecutionAuthorityV1:
        original: broker.ProjectVerificationExecutionAuthorityV1 = resolve_authority(query)
        return replace(
            original,
            argv=argv,
            command_authority_hash=_authority_hash(task_id="task-1", modality="test", argv=argv, cwd="."),
        )

    monkeypatch.setattr(authority_port, "resolve_project_verification_authority", resolve)
    monkeypatch.setattr(owner, "_EXECUTION_AUTHORITY_PORT", authority_port)


def test_control_exposes_only_irreversible_request_and_single_use_claim() -> None:
    control = ProcessTreeRunControl()
    assert getattr(control, "cancellation_requested", None) is False
    control.claim_for_execution()
    with pytest.raises(RuntimeError, match="already used"):
        control.claim_for_execution()
    control.cancel()
    assert control.cancellation_requested is True
    with pytest.raises(AttributeError):
        control.cancellation_requested = False


def test_cancel_before_spawn_produces_no_process_or_success_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "control" in inspect.signature(broker.run_project_verification).parameters
    _source(tmp_path, monkeypatch)
    runner = _Runner()
    _bind_runner(monkeypatch, runner)
    command = _verification_command(tmp_path)
    control = ProcessTreeRunControl()
    control.cancel()
    with pytest.raises(ProcessTreeCancelledError, match="before_spawn"):
        broker.run_project_verification(command, control=control)
    assert runner.calls == 0
    assert broker.query_project_verification_receipt(_verification_query(command)) is None


def _descendants(pid: int) -> set[int]:
    pending = [pid]
    descendants: set[int] = set()
    while pending:
        parent = pending.pop()
        path = Path(f"/proc/{parent}/task/{parent}/children")
        try:
            children = [int(item) for item in path.read_text(encoding="utf-8").split()]
        except FileNotFoundError:
            continue
        for child in children:
            if child not in descendants:
                descendants.add(child)
                pending.append(child)
    return descendants


def _running(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] not in {"Z", "X"}


@pytest.mark.asyncio
async def test_running_verifier_cancel_drains_actual_detached_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "control" in inspect.signature(broker.run_project_verification).parameters
    _source(tmp_path, monkeypatch)
    script = (
        "import subprocess,sys,time; from pathlib import Path; "
        "subprocess.Popen([sys.executable,'-c',"
        "\"import time; from pathlib import Path; time.sleep(20); Path('late.txt').write_text('escaped',encoding='utf-8')\"],"
        "start_new_session=True); "
        "Path('ready.txt').write_text('ready',encoding='utf-8'); time.sleep(30)"
    )
    argv = ("python", "-c", script)

    _command_authority(monkeypatch, argv)
    launch_process = ExecutionBrokerService.launch_process
    handles: list[tuple[ExecutionBrokerService, ExecutionProcessHandleV1]] = []

    async def observe_launch(
        service: ExecutionBrokerService, command: LaunchExecutionProcessCommandV1
    ) -> ExecutionProcessLaunchResultV1:
        launched = await launch_process(service, command)
        if launched.handle is not None:
            handles.append((service, launched.handle))
        return launched

    monkeypatch.setattr(ExecutionBrokerService, "launch_process", observe_launch)
    command = _verification_command(tmp_path)
    control = ProcessTreeRunControl()
    worker = asyncio.create_task(asyncio.to_thread(broker.run_project_verification, command, control=control))
    try:
        deadline = time.monotonic() + 10.0
        while not (tmp_path / "ready.txt").is_file() and not worker.done() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert (tmp_path / "ready.txt").is_file(), worker.exception() if worker.done() else "process never ready"
        assert len(handles) == 1 and handles[0][1].pid is not None
        processes = {handles[0][1].pid, *_descendants(handles[0][1].pid)}
        assert len(processes) >= 3
        started = time.monotonic()
        control.cancel()
        with pytest.raises(ProcessTreeCancelledError, match="drained"):
            await asyncio.wait_for(asyncio.shield(worker), timeout=5.0)
        assert time.monotonic() - started < 5.0
        assert not any(_running(pid) for pid in processes)
        assert not (tmp_path / "late.txt").exists()
        assert broker.query_project_verification_receipt(_verification_query(command)) is None
    finally:
        control.cancel()
        for service, handle in handles:
            await service.terminate_process(handle)
        if not worker.done():
            with suppress(ProcessTreeCancelledError):
                await asyncio.wait_for(asyncio.shield(worker), timeout=5.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unknown_status",
    [None, ExecutionProcessStatusV1.UNKNOWN, ExecutionProcessStatusV1.RUNNING, ExecutionProcessStatusV1.QUEUED],
)
async def test_unproven_termination_keeps_snapshot_and_never_seals_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unknown_status: ExecutionProcessStatusV1 | None
) -> None:
    _source(tmp_path, monkeypatch)
    _command_authority(
        monkeypatch,
        (
            "python",
            "-c",
            "import time; from pathlib import Path; Path('ready.txt').write_text('ready',encoding='utf-8'); time.sleep(30)",
        ),
    )
    prepare = owner.prepare_project_verification_sandbox
    snapshots: list[PreparedProjectVerificationSandbox] = []

    def observe_prepare(
        *,
        workspace: str,
        inputs: tuple[broker.ProjectVerificationArtifactInputV1, ...],
        request_hash: str,
        cwd: str = ".",
    ) -> PreparedProjectVerificationSandbox:
        prepared = prepare(workspace=workspace, inputs=inputs, request_hash=request_hash, cwd=cwd)
        snapshots.append(prepared)
        return prepared

    terminate = ExecutionBrokerService.terminate_process
    wait = ExecutionBrokerService.wait_process
    stop_requested = False

    async def unproven_termination(
        service: ExecutionBrokerService,
        handle: ExecutionProcessHandleV1 | str,
        *,
        timeout_seconds: float = 5.0,
    ) -> bool:
        nonlocal stop_requested
        stopped = await terminate(service, handle, timeout_seconds=timeout_seconds)
        stop_requested = True
        return False if unknown_status is None else stopped

    async def unproven_status(
        service: ExecutionBrokerService,
        handle: ExecutionProcessHandleV1 | str,
        *,
        timeout_seconds: float | None = None,
    ) -> ExecutionProcessWaitResultV1:
        terminal = await wait(service, handle, timeout_seconds=timeout_seconds)
        return replace(terminal, status=unknown_status) if stop_requested and unknown_status is not None else terminal

    monkeypatch.setattr(owner, "prepare_project_verification_sandbox", observe_prepare)
    monkeypatch.setattr(ExecutionBrokerService, "terminate_process", unproven_termination)
    monkeypatch.setattr(ExecutionBrokerService, "wait_process", unproven_status)
    command = _verification_command(tmp_path)
    control = ProcessTreeRunControl()
    worker = asyncio.create_task(asyncio.to_thread(broker.run_project_verification, command, control=control))
    try:
        deadline = time.monotonic() + 10.0
        while not (tmp_path / "ready.txt").is_file() and not worker.done() and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert (tmp_path / "ready.txt").is_file(), worker.exception() if worker.done() else "process never ready"
        control.cancel()
        with pytest.raises(ProcessTreeDrainError, match="unproven"):
            await asyncio.wait_for(asyncio.shield(worker), timeout=5.0)
        assert len(snapshots) == 1 and snapshots[0].snapshot_root.is_dir()
        assert broker.query_project_verification_receipt(_verification_query(command)) is None
    finally:
        control.cancel()
        if not worker.done():
            with suppress(ProcessTreeCancelledError, ProcessTreeDrainError):
                await asyncio.wait_for(asyncio.shield(worker), timeout=5.0)
        for snapshot in snapshots:
            snapshot.cleanup()
