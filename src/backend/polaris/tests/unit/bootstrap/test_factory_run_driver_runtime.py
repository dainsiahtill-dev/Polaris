from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from polaris.bootstrap import factory_run_driver_runtime as driver_module
from polaris.bootstrap.factory_run_driver_runtime import FactoryRunDriverRuntimeV1


@dataclass
class _Run:
    id: str
    status: str


class _Service:
    def __init__(self, runs: list[_Run]) -> None:
        self._runs = {run.id: run for run in runs}
        self.recovered_run_ids: list[str] = []
        self.resumed_run_ids: list[str] = []

    async def list_runs(self) -> list[dict[str, str]]:
        return [{"id": run.id, "status": run.status} for run in self._runs.values()]

    async def get_run(self, run_id: str) -> _Run | None:
        return self._runs.get(run_id)

    async def recover_run(self, run_id: str) -> _Run:
        self.recovered_run_ids.append(run_id)
        run = self._runs[run_id]
        run.status = "recovering"
        return run

    async def resume_recovered_run(self, run_id: str) -> _Run:
        self.resumed_run_ids.append(run_id)
        return self._runs[run_id]


@pytest.mark.asyncio
async def test_start_recovers_running_and_recovering_runs_without_http_request() -> None:
    service = _Service(
        [
            _Run("run-running", "running"),
            _Run("run-recovering", "recovering"),
            _Run("run-completed", "completed"),
        ]
    )
    executed: list[tuple[str, str]] = []

    async def _execute(_service: object, run_id: str, payload: object, _state: object) -> None:
        executed.append((run_id, str(payload)))

    runtime = FactoryRunDriverRuntimeV1(
        workspace="/workspace",
        service=service,
        state=object(),
        execute=_execute,
        build_recovery_payload=lambda run, _workspace: f"payload:{run.id}",
    )

    await runtime.start()
    await runtime.wait_idle()
    await runtime.stop()

    assert executed == [
        ("run-recovering", "payload:run-recovering"),
        ("run-running", "payload:run-running"),
    ]
    assert service.recovered_run_ids == ["run-recovering", "run-running"]
    assert service.resumed_run_ids == ["run-recovering", "run-running"]


@pytest.mark.asyncio
async def test_start_does_not_resume_when_recovery_restores_terminal_checkpoint() -> None:
    """A cancelled commit ACK may restore the exact failed stage checkpoint.

    The startup list snapshot still says ``recovering`` in that case.  Once
    ``recover_run`` returns the authoritative terminal status, the driver must
    not open a physical-attempt epoch or submit the run automatically.
    """

    class _TerminalRestoreService(_Service):
        async def recover_run(self, run_id: str) -> _Run:
            self.recovered_run_ids.append(run_id)
            run = self._runs[run_id]
            run.status = "failed"
            return run

    service = _TerminalRestoreService([_Run("run-cancelled-ack", "recovering")])
    executed: list[str] = []

    async def _execute(_service: object, run_id: str, _payload: object, _state: object) -> None:
        executed.append(run_id)

    runtime = FactoryRunDriverRuntimeV1(
        workspace="/workspace",
        service=service,
        state=object(),
        execute=_execute,
        build_recovery_payload=lambda _run, _workspace: object(),
    )

    await runtime.start()
    await runtime.wait_idle()
    await runtime.stop()

    assert service.recovered_run_ids == ["run-cancelled-ack"]
    assert service.resumed_run_ids == []
    assert executed == []


@pytest.mark.asyncio
async def test_start_recovers_failed_run_with_committed_local_rework_action() -> None:
    service = _Service([_Run("run-failed", "failed")])
    executed: list[str] = []

    async def _execute(_service: object, run_id: str, _payload: object, _state: object) -> None:
        executed.append(run_id)

    runtime = FactoryRunDriverRuntimeV1(
        workspace="/workspace",
        service=service,
        state=object(),
        execute=_execute,
        build_recovery_payload=lambda _run, _workspace: object(),
        recover_committed_run_ids=lambda: ("run-failed",),
    )

    await runtime.start()
    await runtime.wait_idle()
    await runtime.stop()

    assert executed == ["run-failed"]
    assert service.recovered_run_ids == []
    assert service.resumed_run_ids == []


@pytest.mark.asyncio
async def test_start_does_not_block_backend_readiness_on_slow_recovery() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class _SlowService(_Service):
        async def list_runs(self) -> list[dict[str, str]]:
            entered.set()
            await release.wait()
            return await super().list_runs()

    service = _SlowService([_Run("run-1", "running")])
    runtime = FactoryRunDriverRuntimeV1(
        workspace="/workspace",
        service=service,
        state=object(),
        execute=lambda *_args: asyncio.sleep(0),
        build_recovery_payload=lambda _run, _workspace: object(),
    )

    await asyncio.wait_for(runtime.start(), timeout=0.1)
    await entered.wait()
    assert runtime.active_run_ids == ()

    release.set()
    await runtime.wait_idle()
    await runtime.stop()


@pytest.mark.asyncio
async def test_submit_deduplicates_live_driver_but_allows_later_reentry() -> None:
    service = _Service([_Run("run-1", "running")])
    entered = asyncio.Event()
    release = asyncio.Event()
    executions = 0

    async def _execute(_service: object, _run_id: str, _payload: object, _state: object) -> None:
        nonlocal executions
        executions += 1
        entered.set()
        await release.wait()

    runtime = FactoryRunDriverRuntimeV1(
        workspace="/workspace",
        service=service,
        state=object(),
        execute=_execute,
        build_recovery_payload=lambda _run, _workspace: object(),
    )

    await runtime.start(recover=False)
    first = runtime.submit("run-1", payload=object())
    await entered.wait()
    duplicate = runtime.submit("run-1", payload=object())
    assert duplicate is first
    assert executions == 1

    release.set()
    await first
    await asyncio.sleep(0)

    entered.clear()
    release.clear()
    second = runtime.submit("run-1", payload=object())
    await entered.wait()
    assert second is not first
    assert executions == 2

    release.set()
    await second
    await runtime.stop()


@pytest.mark.asyncio
async def test_wait_run_idle_crosses_terminal_closeout_before_retry_submission() -> None:
    """A retry waiter must not return while the previous same-run driver is live."""

    service = _Service([_Run("run-1", "failed")])
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _execute(_service: object, _run_id: str, _payload: object, _state: object) -> None:
        entered.set()
        await release.wait()

    runtime = FactoryRunDriverRuntimeV1(
        workspace="/workspace",
        service=service,
        state=object(),
        execute=_execute,
        build_recovery_payload=lambda _run, _workspace: object(),
    )
    await runtime.start(recover=False)
    runtime.submit("run-1", payload=object())
    await entered.wait()

    waiter = asyncio.create_task(runtime.wait_run_idle("run-1"))
    await asyncio.sleep(0)
    assert waiter.done() is False

    release.set()
    await waiter
    await asyncio.sleep(0)
    assert runtime.active_run_ids == ()
    await runtime.stop()


@pytest.mark.asyncio
async def test_stop_cancels_lifespan_owned_driver_tasks() -> None:
    service = _Service([_Run("run-1", "running")])
    entered = asyncio.Event()

    async def _execute(_service: object, _run_id: str, _payload: object, _state: object) -> None:
        entered.set()
        await asyncio.Event().wait()

    runtime = FactoryRunDriverRuntimeV1(
        workspace="/workspace",
        service=service,
        state=object(),
        execute=_execute,
        build_recovery_payload=lambda _run, _workspace: object(),
    )

    await runtime.start(recover=False)
    task = runtime.submit("run-1", payload=object())
    await entered.wait()
    await runtime.stop()

    assert task.cancelled()
    assert runtime.active_run_ids == ()


@pytest.mark.asyncio
async def test_new_lifespan_recovers_run_after_previous_driver_shutdown() -> None:
    service = _Service([_Run("run-1", "running")])
    first_entered = asyncio.Event()

    async def _first_execute(_service: object, _run_id: str, _payload: object, _state: object) -> None:
        first_entered.set()
        await asyncio.Event().wait()

    first_runtime = FactoryRunDriverRuntimeV1(
        workspace="/workspace",
        service=service,
        state=object(),
        execute=_first_execute,
        build_recovery_payload=lambda _run, _workspace: object(),
    )
    await first_runtime.start(recover=False)
    first_runtime.submit("run-1", payload=object())
    await first_entered.wait()
    await first_runtime.stop()

    recovered: list[str] = []

    async def _second_execute(_service: object, run_id: str, _payload: object, _state: object) -> None:
        recovered.append(run_id)

    second_runtime = FactoryRunDriverRuntimeV1(
        workspace="/workspace",
        service=service,
        state=object(),
        execute=_second_execute,
        build_recovery_payload=lambda _run, _workspace: object(),
    )
    await second_runtime.start()
    await second_runtime.wait_idle()
    await second_runtime.stop()

    assert recovered == ["run-1"]


def test_recover_committed_factory_run_ids_requires_pending_authoritative_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Projection:
        authoritative = True
        degraded = False
        rows = (
            {
                "status": "pending",
                "metadata": {
                    "factory_local_rework": {
                        "action_id": "a" * 64,
                        "factory_run_id": "run-ready",
                    }
                },
            },
            {
                "status": "completed",
                "metadata": {
                    "factory_local_rework": {
                        "action_id": "b" * 64,
                        "factory_run_id": "run-finished",
                    }
                },
            },
            {
                "status": "pending",
                "metadata": {
                    "factory_local_rework": {
                        "action_id": "not-a-hash",
                        "factory_run_id": "run-forged",
                    }
                },
            },
        )

    class _TaskRuntime:
        def __init__(self, _workspace: str) -> None:
            pass

        @staticmethod
        def query_observable_task_rows_projection() -> _Projection:
            return _Projection()

    monkeypatch.setattr(driver_module, "TaskRuntimeService", _TaskRuntime)

    assert driver_module.recover_committed_factory_run_ids("/workspace") == ("run-ready",)
