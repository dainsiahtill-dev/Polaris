"""Event-driven supervisor for durable project-completion convergence."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress

from polaris.cells.orchestration.workflow_orchestration.public.project_completion import (
    AdvanceProjectCompletionCommandV1,
    ProjectCompletionAdvanceResultV1,
)

AdvanceCallable = Callable[[AdvanceProjectCompletionCommandV1], Awaitable[ProjectCompletionAdvanceResultV1]]
RecoverCallable = Callable[[], Awaitable[tuple[AdvanceProjectCompletionCommandV1, ...]]]
logger = logging.getLogger(__name__)


class EventDrivenProjectCompletionSupervisorV1:
    """Advance registered projects only on explicit owner/control-plane wakes."""

    def __init__(self, *, advance: AdvanceCallable, recover: RecoverCallable | None = None) -> None:
        self._advance = advance
        self._recover = recover
        self._queue: asyncio.Queue[AdvanceProjectCompletionCommandV1 | None] = asyncio.Queue()
        self._active: dict[str, AdvanceProjectCompletionCommandV1] = {}
        self._queued: set[str] = set()
        self._worker: asyncio.Task[None] | None = None
        self._last_results: dict[str, ProjectCompletionAdvanceResultV1] = {}

    @staticmethod
    def _key(command: AdvanceProjectCompletionCommandV1) -> str:
        identity = command.identity
        return "\x1f".join(
            (identity.workspace, identity.project_id, identity.run_id, identity.completion_contract_hash)
        )

    async def start(self) -> None:
        if self._worker is not None:
            return
        self._worker = asyncio.create_task(self._run(), name="project_completion_supervisor")
        try:
            await self._recover_durable_commands()
        except Exception:
            worker = self._worker
            self._worker = None
            if worker is not None:
                worker.cancel()
                with suppress(asyncio.CancelledError):
                    await worker
            raise

    async def stop(self) -> None:
        worker = self._worker
        if worker is None:
            return
        await self._queue.put(None)
        await worker
        self._worker = None

    async def submit(self, command: AdvanceProjectCompletionCommandV1) -> None:
        key = self._key(command)
        self._active[key] = command
        if key in self._queued:
            return
        self._queued.add(key)
        await self._queue.put(command)

    async def wake(self) -> None:
        """Replay durable registrations, then reconsider every active identity."""

        await self._recover_durable_commands()
        for command in tuple(self._active.values()):
            await self.submit(command)

    async def _recover_durable_commands(self) -> None:
        recover = self._recover
        if recover is None:
            return
        for command in await recover():
            if type(command) is not AdvanceProjectCompletionCommandV1:
                raise TypeError("project completion recovery returned a non-command")
            await self.submit(command)

    def last_result(self, command: AdvanceProjectCompletionCommandV1) -> ProjectCompletionAdvanceResultV1 | None:
        return self._last_results.get(self._key(command))

    async def _run(self) -> None:
        while True:
            command = await self._queue.get()
            if command is None:
                return
            key = self._key(command)
            self._queued.discard(key)
            try:
                result = await self._advance(command)
            except Exception:
                # Keep the identity active.  A later explicit owner/control
                # wake retries; no timer or busy loop is introduced.
                logger.exception(
                    "Project completion advance failed; identity remains active: %s",
                    key,
                )
                continue
            self._last_results[key] = result
            if result.terminal:
                self._active.pop(key, None)


_bound_supervisor: EventDrivenProjectCompletionSupervisorV1 | None = None


def bind_project_completion_supervisor(supervisor: EventDrivenProjectCompletionSupervisorV1) -> None:
    global _bound_supervisor
    if _bound_supervisor is not None and _bound_supervisor is not supervisor:
        raise RuntimeError("project_completion_supervisor_conflicting_rebind")
    _bound_supervisor = supervisor


def clear_project_completion_supervisor(supervisor: EventDrivenProjectCompletionSupervisorV1) -> None:
    global _bound_supervisor
    if _bound_supervisor is supervisor:
        _bound_supervisor = None


async def submit_project_completion_command(command: AdvanceProjectCompletionCommandV1) -> None:
    if _bound_supervisor is None:
        raise RuntimeError("project_completion_supervisor_unbound")
    await _bound_supervisor.submit(command)


__all__ = [
    "EventDrivenProjectCompletionSupervisorV1",
    "bind_project_completion_supervisor",
    "clear_project_completion_supervisor",
    "submit_project_completion_command",
]
