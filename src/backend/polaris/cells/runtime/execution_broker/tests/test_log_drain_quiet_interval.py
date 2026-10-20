"""Quiet output intervals must not cancel a broker's live stream reader."""

from __future__ import annotations

import asyncio
import hashlib
import sys
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from polaris.cells.runtime.execution_broker.internal import (
    project_verification_authority as verification_owner,
    service as service_module,
)
from polaris.cells.runtime.execution_broker.public.contracts import (
    ExecutionProcessStatusV1,
    LaunchExecutionProcessCommandV1,
)
from polaris.cells.runtime.execution_broker.public.project_verification import (
    ProjectVerificationArtifactInputV1,
    ProjectVerificationExecutionAuthorityV1,
    ResolveProjectVerificationAuthorityQueryV1,
    query_project_verification_receipt,
    run_project_verification,
)
from polaris.cells.runtime.execution_broker.public.service import ExecutionBrokerService
from polaris.cells.runtime.execution_broker.tests.test_project_verification_receipts import (
    _authority_hash,
    _AuthorityPort,
    _verification_command,
    _verification_query,
)
from polaris.kernelone.runtime import (
    ExecutionFacade,
    ExecutionHandle,
    ExecutionLane,
    ExecutionRuntime,
    ExecutionSnapshot,
    ExecutionStatus,
)


class _TimedStreamHandle:
    """Controlled stream; snapshots deliberately cannot replace missing output."""

    execution_id = "quiet-stream-fixture"

    def __init__(
        self,
        chunks: tuple[tuple[float, str], ...] = (),
        *,
        block_after_prefix: bool = False,
        terminal: bool = False,
        fail_after_prefix: bool = False,
    ) -> None:
        self.chunks = chunks
        self.block_after_prefix = block_after_prefix
        self.fail_after_prefix = fail_after_prefix
        self.status = ExecutionStatus.FAILED if terminal else ExecutionStatus.RUNNING
        self.waiting = asyncio.Event()
        self.closed = asyncio.Event()
        self.read_tasks: list[asyncio.Task[Any]] = []
        self.stream_calls = 0

    def snapshot(self) -> ExecutionSnapshot:
        return ExecutionSnapshot(
            execution_id=self.execution_id,
            name="quiet-stream-fixture",
            lane=ExecutionLane.SUBPROCESS,
            status=self.status,
            submitted_at=datetime.now(timezone.utc),
            timeout_seconds=None,
            metadata={},
            result={},
        )

    async def wait(self, timeout: float | None = None) -> ExecutionStatus:
        return self.status

    async def stream(self) -> AsyncIterator[Any]:
        self.stream_calls += 1
        try:
            yield SimpleNamespace(line="TAP version 13")
            for delay, line in self.chunks:
                self._record_reader()
                await asyncio.sleep(delay)
                yield SimpleNamespace(line=line)
            if self.fail_after_prefix:
                self._record_reader()
                raise RuntimeError("stream fixture failed")
            if self.block_after_prefix:
                self._record_reader()
                self.waiting.set()
                await asyncio.Event().wait()
            self.status = ExecutionStatus.FAILED
        finally:
            self.closed.set()

    def _record_reader(self) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.read_tasks.append(task)


@pytest.fixture
async def broker(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[ExecutionBrokerService]:
    monkeypatch.delenv("KERNELONE_EXECUTION_BROKER_LOG_DRAIN_MAX_SECONDS", raising=False)
    runtime = ExecutionRuntime(async_concurrency=1, blocking_concurrency=1, process_concurrency=1)
    service = ExecutionBrokerService(facade=ExecutionFacade(runtime=runtime))
    try:
        yield service
    finally:
        await service.close()


def _start_drain(broker: ExecutionBrokerService, handle: _TimedStreamHandle, log_path: Path) -> asyncio.Task[None]:
    task = asyncio.create_task(broker._drain_stream_to_log(cast(ExecutionHandle, handle), str(log_path)))
    broker._process_log_tasks.set(handle.execution_id, task)
    return task


def _assert_reader_reclaimed(broker: ExecutionBrokerService, handle: _TimedStreamHandle) -> None:
    assert handle.closed.is_set()
    assert handle.stream_calls == 1
    assert handle.read_tasks and all(task.done() for task in handle.read_tasks)
    assert handle.execution_id not in broker._process_log_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("quiet_seconds", [0.0, 1.134006])
async def test_prefix_and_later_assertion_survive_quiet_interval(
    tmp_path: Path, broker: ExecutionBrokerService, quiet_seconds: float
) -> None:
    """Cancelling an idle read loses the assertion; result fallback is empty."""
    handle = _TimedStreamHandle(
        ((quiet_seconds, "not ok 10 - generic business rule"), (0.0, "AssertionError: expected 8, actual 7"))
    )
    log_path = tmp_path / "quiet.log"
    await asyncio.wait_for(_start_drain(broker, handle, log_path), timeout=4.0)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert lines[1:4] == [
        "TAP version 13",
        "not ok 10 - generic business rule",
        "AssertionError: expected 8, actual 7",
    ]
    assert "[execution_broker] terminal" in lines[-1]
    assert handle.snapshot().result == {}
    _assert_reader_reclaimed(broker, handle)


@pytest.mark.asyncio
async def test_explicit_cancel_reclaims_pending_read_and_iterator(
    tmp_path: Path, broker: ExecutionBrokerService
) -> None:
    handle = _TimedStreamHandle(block_after_prefix=True)
    log_path = tmp_path / "cancelled.log"
    task = _start_drain(broker, handle, log_path)
    await asyncio.wait_for(handle.waiting.wait(), timeout=2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    _assert_reader_reclaimed(broker, handle)
    assert "TAP version 13" in log_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["terminal_idle", "diagnostic_cap"])
async def test_bounded_stop_reclaims_pending_read_and_iterator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, broker: ExecutionBrokerService, stop: str
) -> None:
    handle = _TimedStreamHandle(block_after_prefix=True, terminal=stop == "terminal_idle")
    if stop == "terminal_idle":
        monkeypatch.setattr(service_module, "_LOG_DRAIN_TERMINAL_IDLE_SECONDS", 0.0)
    else:
        monkeypatch.setenv("KERNELONE_EXECUTION_BROKER_LOG_DRAIN_MAX_SECONDS", "1.1")
    log_path = tmp_path / f"{stop}.log"
    await asyncio.wait_for(_start_drain(broker, handle, log_path), timeout=4.0)

    _assert_reader_reclaimed(broker, handle)
    content = log_path.read_text(encoding="utf-8")
    assert "TAP version 13" in content
    marker = "terminal" if stop == "terminal_idle" else "closed_before_terminal"
    assert f"[execution_broker] {marker}" in content


@pytest.mark.asyncio
async def test_stream_failure_is_consumed_once_and_resources_close(
    tmp_path: Path, broker: ExecutionBrokerService
) -> None:
    handle = _TimedStreamHandle(fail_after_prefix=True)
    log_path = tmp_path / "failed-stream.log"
    await asyncio.wait_for(_start_drain(broker, handle, log_path), timeout=2.0)

    _assert_reader_reclaimed(broker, handle)
    assert len(handle.read_tasks) == 1
    assert log_path.read_text(encoding="utf-8").count("TAP version 13") == 1


@pytest.mark.asyncio
async def test_iterator_close_failure_is_visible_and_does_not_skip_log_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, broker: ExecutionBrokerService
) -> None:
    class CloseFailingIterator:
        def __init__(self) -> None:
            self.emitted = False

        def __aiter__(self) -> CloseFailingIterator:
            return self

        async def __anext__(self) -> Any:
            if self.emitted:
                raise StopAsyncIteration
            self.emitted = True
            return SimpleNamespace(line="close-failure-prefix")

        async def aclose(self) -> None:
            raise OSError("private iterator cleanup failure")

    handle = _TimedStreamHandle()
    stream = CloseFailingIterator()
    monkeypatch.setattr(handle, "stream", lambda: stream)
    logged_errors: list[tuple[str, dict[str, Any]]] = []
    log_closed: list[bool] = []
    real_error = service_module._logger.error
    real_close = service_module._AsyncLineLog.close

    def observe_error(event: str, **kwargs: Any) -> None:
        logged_errors.append((event, kwargs))
        real_error(event, **kwargs)

    async def observe_close(log: service_module._AsyncLineLog) -> None:
        await real_close(log)
        log_closed.append(log._handle.closed)

    monkeypatch.setattr(service_module._logger, "error", observe_error)
    monkeypatch.setattr(service_module._AsyncLineLog, "close", observe_close)
    log_path = tmp_path / "close-failure.log"
    await asyncio.wait_for(_start_drain(broker, handle, log_path), timeout=2.0)

    assert logged_errors == [
        (
            "execution_broker.log_drain.stream_close_failed",
            {
                "execution_id": handle.execution_id,
                "error": "private iterator cleanup failure",
                "error_type": "OSError",
                "exc_info": True,
            },
        )
    ]
    assert log_closed == [True]
    assert handle.execution_id not in broker._process_log_tasks
    content = log_path.read_text(encoding="utf-8")
    assert "close-failure-prefix" in content
    assert "[execution_broker] closed_before_terminal" in content


@pytest.mark.asyncio
async def test_public_broker_persists_stdout_and_stderr_after_quiet_interval(
    tmp_path: Path, broker: ExecutionBrokerService
) -> None:
    """An isolated generic subprocess proves the real broker/log consumer path."""
    log_path = tmp_path / "physical-stream.log"
    command = LaunchExecutionProcessCommandV1(
        name="quiet-output-private-fixture",
        args=(
            sys.executable,
            "-c",
            "import sys,time; print('TAP prefix',flush=True); time.sleep(1.2); "
            "print('not ok 1 - private fixture',flush=True); "
            "print('AssertionError: expected 8, actual 7',file=sys.stderr,flush=True); sys.exit(1)",
        ),
        workspace=str(tmp_path),
        timeout_seconds=5.0,
        log_path=str(log_path),
    )
    launched = await broker.launch_process(command)
    assert launched.success and launched.handle is not None
    waited = await broker.wait_process(launched.handle, timeout_seconds=5.0)

    assert waited.status == ExecutionProcessStatusV1.FAILED
    assert waited.exit_code == 1
    content = log_path.read_text(encoding="utf-8")
    for line in ("TAP prefix", "not ok 1 - private fixture", "AssertionError: expected 8, actual 7"):
        assert content.splitlines().count(line) == 1
    assert "status=failed exit_code=1" in content
    assert not broker._process_log_tasks


def test_actual_verifier_log_persists_into_failed_receipt_and_cold_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Synthetic authority only; physical runner, sandbox, log and sealed store are real."""
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir()
    source.write_text("fixture_value = 7\n", encoding="utf-8")
    test_source = tmp_path / "test_quiet.py"
    test_source.write_text(
        "import sys,time\n"
        "def test_private_quiet_fixture():\n"
        "    print('quiet-verifier-prefix',flush=True)\n"
        "    time.sleep(1.2)\n"
        "    print('quiet-verifier-stderr',file=sys.stderr,flush=True)\n"
        "    assert 7 == 8, 'quiet-verifier-assertion expected 8 actual 7'\n",
        encoding="utf-8",
    )

    class QuietVerifierAuthority(_AuthorityPort):
        def resolve_project_verification_authority(
            self, query: ResolveProjectVerificationAuthorityQueryV1
        ) -> ProjectVerificationExecutionAuthorityV1:
            authority = super().resolve_project_verification_authority(query)
            argv = ("python", "-m", "pytest", "-q", "-s")
            return replace(
                authority,
                argv=argv,
                command_authority_hash=_authority_hash(task_id="task-1", modality="test", argv=argv, cwd="."),
                input_artifacts=(
                    ProjectVerificationArtifactInputV1(obligation_id="artifact.main", path="src/main.py"),
                    ProjectVerificationArtifactInputV1(obligation_id="artifact.test", path="test_quiet.py"),
                ),
            )

    monkeypatch.setattr(verification_owner, "_EXECUTION_AUTHORITY_PORT", QuietVerifierAuthority())
    command = _verification_command(tmp_path)
    completed = run_project_verification(command)
    receipt = completed.receipt
    assert completed.spawned and receipt is not None
    assert receipt.exit_code == 1 and not receipt.succeeded and not receipt.timed_out
    for line in ("quiet-verifier-prefix", "quiet-verifier-stderr", "quiet-verifier-assertion expected 8 actual 7"):
        assert line in receipt.output_excerpt
    logs = list((tmp_path / ".polaris" / "runtime" / "evidence" / "project-verification-output").glob("*.log"))
    assert len(logs) == 1
    assert receipt.output_hash == hashlib.sha256(logs[0].read_bytes()).hexdigest()

    # Public query opens the persisted receipt store again; any process launch
    # here would be a second execution rather than cold receipt recovery.
    def reject_launch(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("cold receipt query must not execute a verifier")

    monkeypatch.setattr(verification_owner._ExecutionBrokerProjectVerificationRunner, "run", reject_launch)
    recovered = query_project_verification_receipt(_verification_query(command))
    assert recovered == receipt
    assert recovered is not None and recovered.output_hash == receipt.output_hash
    assert "quiet-verifier-assertion expected 8 actual 7" in recovered.output_excerpt
