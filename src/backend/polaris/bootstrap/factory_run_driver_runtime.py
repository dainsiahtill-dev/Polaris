"""Backend-lifespan owner for durable Factory run execution tasks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService

logger = logging.getLogger(__name__)


class _FactoryRunServicePort(Protocol):
    async def list_runs(self) -> list[dict[str, Any]]: ...

    async def get_run(self, run_id: str) -> object | None: ...

    async def recover_run(self, run_id: str) -> object: ...

    async def resume_recovered_run(self, run_id: str) -> object: ...


# The composition root supplies concrete Factory service/run/payload/state
# types.  Keeping this bootstrap owner free of delivery-layer imports requires
# an intentionally structural ``Any`` boundary here; the service field itself
# remains constrained by ``_FactoryRunServicePort``.
FactoryRunExecutePort = Callable[[Any, str, Any, Any], Coroutine[Any, Any, None]]
FactoryRunRecoveryPayloadPort = Callable[[Any, str], Any]
FactoryCommittedRunIdsPort = Callable[[], tuple[str, ...]]


def _status_token(value: object) -> str:
    token = getattr(value, "value", value)
    return str(token or "").strip().lower()


def recover_committed_factory_run_ids(workspace: str) -> tuple[str, ...]:
    """Return failed/nonterminal runs whose exact owner row is ready for rework."""

    projection = TaskRuntimeService(workspace).query_observable_task_rows_projection()
    if not projection.authoritative or projection.degraded:
        return ()
    run_ids: set[str] = set()
    for row in projection.rows:
        if _status_token(row.get("status")) != "pending":
            continue
        metadata = row.get("metadata")
        metadata_map = metadata if isinstance(metadata, Mapping) else {}
        record = metadata_map.get("factory_local_rework")
        record_map = record if isinstance(record, Mapping) else {}
        action_id = str(record_map.get("action_id") or "").strip()
        factory_run_id = str(record_map.get("factory_run_id") or "").strip()
        if len(action_id) == 64 and all(char in "0123456789abcdef" for char in action_id.lower()) and factory_run_id:
            run_ids.add(factory_run_id)
    return tuple(sorted(run_ids))


@dataclass(slots=True)
class FactoryRunDriverRuntimeV1:
    """Own Factory driver task lifetime and recover nonterminal runs at boot."""

    workspace: str
    service: _FactoryRunServicePort
    state: object
    execute: FactoryRunExecutePort
    build_recovery_payload: FactoryRunRecoveryPayloadPort
    recover_committed_run_ids: FactoryCommittedRunIdsPort | None = None
    _tasks: dict[str, asyncio.Task[None]] = field(default_factory=dict, init=False, repr=False)
    _recovery_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _started: bool = field(default=False, init=False, repr=False)

    @property
    def active_run_ids(self) -> tuple[str, ...]:
        return tuple(sorted(run_id for run_id, task in self._tasks.items() if not task.done()))

    async def start(self, *, recover: bool = True) -> None:
        if self._started:
            return
        self._started = True
        if not recover:
            return
        # Historical recovery may reconcile hundreds of TaskRuntime/settlement
        # facts and wait for child-session proof.  It must not sit on FastAPI's
        # lifespan critical path: live L1-04 accumulated 631 facts and Launcher
        # killed an otherwise healthy backend before /identity could open.
        self._recovery_task = asyncio.create_task(
            self._recover_nonterminal_runs(),
            name="factory-run-driver:startup-recovery",
        )
        self._recovery_task.add_done_callback(self._on_recovery_done)

    async def _recover_nonterminal_runs(self) -> None:
        rows = await self.service.list_runs()
        status_resumable_ids = {
            str(row.get("id") or "").strip()
            for row in rows
            if isinstance(row, Mapping)
            and _status_token(row.get("status")) in {"running", "recovering"}
            and str(row.get("id") or "").strip()
        }
        committed_ids = {
            str(run_id or "").strip()
            for run_id in (self.recover_committed_run_ids() if self.recover_committed_run_ids else ())
            if str(run_id or "").strip()
        }
        resumable_ids = sorted(status_resumable_ids | committed_ids)
        for run_id in resumable_ids:
            run = await self.service.get_run(run_id)
            if run is None:
                continue
            if run_id not in committed_ids and _status_token(getattr(run, "status", "")) not in {
                "running",
                "recovering",
            }:
                continue
            # A process restart loses the process-local physical-attempt
            # coordinator and workspace lifecycle claim.  Replaying those
            # authorities is a mandatory predecessor to any router mutation;
            # executing the persisted run directly would fail closed with
            # ``factory_physical_attempt_replay_required`` and leave the run
            # stranded.  Terminal runs selected only by a committed same-task
            # action already went through closeout and must not be recovered.
            if run_id in status_resumable_ids:
                run = await self.service.recover_run(run_id)
                # Recovery may close an exact cancellation cut by restoring the
                # authoritative terminal checkpoint (for example, a failed QA
                # stage whose event/checkpoint were durable but whose commit ACK
                # was interrupted).  The list snapshot above is then stale:
                # reopening a physical-attempt epoch would execute a terminal
                # run, fail with factory_physical_attempt_replay_required, and
                # fence its workspace lease.  Only genuinely nonterminal state
                # may proceed to resume/submission.
                if _status_token(getattr(run, "status", "")) not in {
                    "running",
                    "recovering",
                }:
                    continue
                run = await self.service.resume_recovered_run(run_id)
            payload = self.build_recovery_payload(run, self.workspace)
            self.submit(run_id, payload=payload)

    @staticmethod
    def _on_recovery_done(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error("Factory run startup recovery failed: %s", error, exc_info=error)

    def submit(self, run_id: str, *, payload: object) -> asyncio.Task[None]:
        if not self._started:
            raise RuntimeError("factory_run_driver_runtime_not_started")
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise ValueError("factory_run_driver_run_id_required")
        current = self._tasks.get(normalized_run_id)
        if current is not None and not current.done():
            return current
        task: asyncio.Task[None] = asyncio.create_task(
            self.execute(self.service, normalized_run_id, payload, self.state),
            name=f"factory-run-driver:{normalized_run_id}",
        )
        self._tasks[normalized_run_id] = task

        def _done(done: asyncio.Task[None]) -> None:
            self._on_done(normalized_run_id, done)

        task.add_done_callback(_done)
        return task

    async def wait_run_idle(self, run_id: str) -> None:
        """Wait until the exact run's lifespan-owned driver has exited.

        A Factory run can already expose terminal child-session/lease evidence
        while its driver is still persisting summaries and executing terminal
        closeout.  Reopening the run during that window changes it to
        ``recovering``, but ``submit`` correctly deduplicates against the old
        live task; the old closeout then writes ``failed`` again and the retry
        is silently lost.  Retry control must cross this process-local task
        boundary before mutating durable run state.
        """

        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise ValueError("factory_run_driver_run_id_required")
        current = self._tasks.get(normalized_run_id)
        if current is None or current.done():
            return
        await asyncio.gather(asyncio.shield(current), return_exceptions=True)

    def _on_done(self, run_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(run_id) is task:
            self._tasks.pop(run_id, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "Factory run driver %s finished with unhandled exception: %s",
                run_id,
                error,
                exc_info=error,
            )

    async def wait_idle(self) -> None:
        recovery_task = self._recovery_task
        if recovery_task is not None:
            await asyncio.gather(asyncio.shield(recovery_task))
        tasks = tuple(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks)

    async def stop(self) -> None:
        recovery_task = self._recovery_task
        if recovery_task is not None and not recovery_task.done():
            recovery_task.cancel()
        if recovery_task is not None:
            await asyncio.gather(recovery_task, return_exceptions=True)
        self._recovery_task = None
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._started = False


__all__ = [
    "FactoryCommittedRunIdsPort",
    "FactoryRunDriverRuntimeV1",
    "FactoryRunExecutePort",
    "FactoryRunRecoveryPayloadPort",
    "bind_factory_run_driver_runtime",
    "clear_factory_run_driver_runtime",
    "get_bound_factory_run_driver_runtime",
    "get_factory_run_driver_runtime",
    "recover_committed_factory_run_ids",
]


_FACTORY_RUN_DRIVER_RUNTIME: FactoryRunDriverRuntimeV1 | None = None


def bind_factory_run_driver_runtime(runtime: FactoryRunDriverRuntimeV1) -> None:
    global _FACTORY_RUN_DRIVER_RUNTIME
    if _FACTORY_RUN_DRIVER_RUNTIME is not None and _FACTORY_RUN_DRIVER_RUNTIME is not runtime:
        raise RuntimeError("factory_run_driver_runtime_conflicting_rebind")
    _FACTORY_RUN_DRIVER_RUNTIME = runtime


def get_factory_run_driver_runtime() -> FactoryRunDriverRuntimeV1:
    if _FACTORY_RUN_DRIVER_RUNTIME is None:
        raise RuntimeError("factory_run_driver_runtime_unbound")
    return _FACTORY_RUN_DRIVER_RUNTIME


def get_bound_factory_run_driver_runtime() -> FactoryRunDriverRuntimeV1 | None:
    """Return the lifespan owner when composition has bound one."""

    return _FACTORY_RUN_DRIVER_RUNTIME


def clear_factory_run_driver_runtime(runtime: FactoryRunDriverRuntimeV1) -> None:
    global _FACTORY_RUN_DRIVER_RUNTIME
    if _FACTORY_RUN_DRIVER_RUNTIME is runtime:
        _FACTORY_RUN_DRIVER_RUNTIME = None
