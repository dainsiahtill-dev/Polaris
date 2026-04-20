"""High-level facade for KernelOne unified execution runtime.

This facade is designed for migration at scale. It provides a single import
surface that supports:

1. Typed submission specs (async, blocking I/O, subprocess).
2. Batch submission and batch wait/cancel flows.
3. One-shot helpers that run and return terminal snapshots.
4. Process output collection without exposing low-level stream handling.

The facade does not own business semantics. It only composes the technical
runtime primitives from ``execution_runtime``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import weakref
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from polaris.kernelone.constants import EXECUTION_DEFAULT_PROCESS_TIMEOUT_SECONDS
from polaris.kernelone.runtime.execution_runtime import (
    ExecutionHandle,
    ExecutionLane,
    ExecutionRuntime,
    ExecutionSnapshot,
    ExecutionStatus,
    get_shared_execution_runtime,
    reset_shared_execution_runtime,
)
from polaris.kernelone.trace import create_task_with_context

# Backward compatibility alias
DEFAULT_PROCESS_TIMEOUT_SECONDS = EXECUTION_DEFAULT_PROCESS_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Iterable, Sequence
    from pathlib import Path

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AsyncTaskSpec:
    """Submission specification for async task lane."""

    name: str
    coroutine_factory: Callable[[], Awaitable[Any]]
    timeout_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BlockingIoSpec:
    """Submission specification for blocking I/O lane."""

    name: str
    func: Callable[..., Any]
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    """Submission specification for subprocess lane."""

    name: str
    args: list[str]
    cwd: Path | None = None
    env: dict[str, str] | None = None
    stdin_lines: list[str] | None = None
    timeout_seconds: float | None = DEFAULT_PROCESS_TIMEOUT_SECONDS
    metadata: dict[str, Any] = field(default_factory=dict)


ExecutionSpec = AsyncTaskSpec | BlockingIoSpec | ProcessSpec


@dataclass(frozen=True, slots=True)
class BatchWaitResult:
    """Result of waiting for multiple executions."""

    statuses: dict[str, ExecutionStatus]
    snapshots: dict[str, ExecutionSnapshot]
    timed_out_execution_ids: tuple[str, ...]
    elapsed_seconds: float

    @property
    def all_completed(self) -> bool:
        return len(self.timed_out_execution_ids) == 0


@dataclass(frozen=True, slots=True)
class BatchCancelResult:
    """Result of canceling multiple executions."""

    cancelled_execution_ids: tuple[str, ...]
    skipped_execution_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProcessRunResult:
    """Terminal result for a one-shot process run with collected output."""

    snapshot: ExecutionSnapshot
    stdout_lines: tuple[str, ...]
    stderr_lines: tuple[str, ...]

    @property
    def status(self) -> ExecutionStatus:
        return self.snapshot.status

    @property
    def ok(self) -> bool:
        return self.snapshot.ok


class ExecutionFacade:
    """Feature-rich facade over ``ExecutionRuntime`` for fast integration."""

    def __init__(self, runtime: ExecutionRuntime | None = None) -> None:
        self._runtime = runtime or get_shared_execution_runtime()
        self._known_handles: dict[str, ExecutionHandle] = {}

    @property
    def runtime(self) -> ExecutionRuntime:
        return self._runtime

    def submit_async_task(self, spec: AsyncTaskSpec) -> ExecutionHandle:
        handle = self._runtime.submit_async(
            name=spec.name,
            coroutine_factory=spec.coroutine_factory,
            timeout_seconds=spec.timeout_seconds,
            metadata=spec.metadata,
        )
        self._remember_handle(handle)
        return handle

    def submit_blocking_io(self, spec: BlockingIoSpec) -> ExecutionHandle:
        handle = self._runtime.submit_blocking(
            name=spec.name,
            func=spec.func,
            args=spec.args,
            kwargs=spec.kwargs,
            timeout_seconds=spec.timeout_seconds,
            metadata=spec.metadata,
        )
        self._remember_handle(handle)
        return handle

    async def submit_process(self, spec: ProcessSpec) -> ExecutionHandle:
        handle = await self._runtime.submit_process(
            name=spec.name,
            args=spec.args,
            cwd=spec.cwd,
            env=spec.env,
            stdin_lines=spec.stdin_lines,
            timeout_seconds=spec.timeout_seconds,
            metadata=spec.metadata,
        )
        self._remember_handle(handle)
        return handle

    async def submit(self, spec: ExecutionSpec) -> ExecutionHandle:
        if isinstance(spec, AsyncTaskSpec):
            return self.submit_async_task(spec)
        if isinstance(spec, BlockingIoSpec):
            return self.submit_blocking_io(spec)
        return await self.submit_process(spec)

    async def submit_many(self, specs: Sequence[ExecutionSpec]) -> list[ExecutionHandle]:
        handles: list[ExecutionHandle] = []
        for spec in specs:
            handles.append(await self.submit(spec))
        return handles

    async def wait_one(
        self,
        handle_or_id: ExecutionHandle | str,
        *,
        timeout: float | None = None,
    ) -> ExecutionStatus:
        handle = self.resolve_handle(handle_or_id)
        return await handle.wait(timeout=timeout)

    async def wait_many(
        self,
        handles_or_ids: Iterable[ExecutionHandle | str],
        *,
        timeout_per_item: float | None = None,
        overall_timeout: float | None = None,
    ) -> BatchWaitResult:
        start = time.monotonic()
        handles = self._normalize_handles(handles_or_ids)
        statuses: dict[str, ExecutionStatus] = {}
        snapshots: dict[str, ExecutionSnapshot] = {}
        timed_out: list[str] = []

        deadline = time.monotonic() + float(overall_timeout) if overall_timeout is not None else None

        for handle in handles:
            execution_id = handle.execution_id
            effective_timeout = timeout_per_item
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out.append(execution_id)
                    statuses[execution_id] = handle.snapshot().status
                    snapshots[execution_id] = handle.snapshot()
                    continue
                effective_timeout = remaining if effective_timeout is None else min(float(effective_timeout), remaining)

            try:
                statuses[execution_id] = await handle.wait(timeout=effective_timeout)
            except TimeoutError:
                timed_out.append(execution_id)
                statuses[execution_id] = handle.snapshot().status
            snapshots[execution_id] = handle.snapshot()

        elapsed = time.monotonic() - start
        return BatchWaitResult(
            statuses=statuses,
            snapshots=snapshots,
            timed_out_execution_ids=tuple(timed_out),
            elapsed_seconds=elapsed,
        )

    async def cancel_many(
        self,
        handles_or_ids: Iterable[ExecutionHandle | str],
    ) -> BatchCancelResult:
        cancelled: list[str] = []
        skipped: list[str] = []

        for handle in self._normalize_handles(handles_or_ids):
            execution_id = handle.execution_id
            changed = await handle.cancel()
            if changed:
                cancelled.append(execution_id)
            else:
                skipped.append(execution_id)

        return BatchCancelResult(
            cancelled_execution_ids=tuple(cancelled),
            skipped_execution_ids=tuple(skipped),
        )

    async def run_async_task(self, spec: AsyncTaskSpec) -> ExecutionSnapshot:
        handle = self.submit_async_task(spec)
        await handle.wait(timeout=spec.timeout_seconds)
        return handle.snapshot()

    async def run_blocking_io(self, spec: BlockingIoSpec) -> ExecutionSnapshot:
        handle = self.submit_blocking_io(spec)
        await handle.wait(timeout=spec.timeout_seconds)
        return handle.snapshot()

    async def run_process(
        self,
        spec: ProcessSpec,
        *,
        collect_output: bool = True,
        output_line_limit: int = 20000,
        wait_timeout: float | None = None,
    ) -> ProcessRunResult:
        handle = await self.submit_process(spec)
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        collector_task: asyncio.Task[None] | None = None
        if collect_output:
            collector_deadline = time.monotonic() + 30.0  # 30s default timeout
            collector_task = create_task_with_context(
                self._collect_process_output(
                    handle,
                    stdout_lines=stdout_lines,
                    stderr_lines=stderr_lines,
                    output_line_limit=max(1, int(output_line_limit)),
                    deadline=collector_deadline,
                ),
                name=f"kernelone-process-collector-{handle.execution_id}",
            )

        effective_wait_timeout = wait_timeout if wait_timeout is not None else spec.timeout_seconds
        await handle.wait(timeout=effective_wait_timeout)

        if collector_task is not None:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(collector_task, timeout=2.0)
            if not collector_task.done():
                collector_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await collector_task

        return ProcessRunResult(
            snapshot=handle.snapshot(),
            stdout_lines=tuple(stdout_lines),
            stderr_lines=tuple(stderr_lines),
        )

    def snapshot(self, handle_or_id: ExecutionHandle | str) -> ExecutionSnapshot:
        handle = self.resolve_handle(handle_or_id)
        return handle.snapshot()

    def list_runtime_snapshots(
        self,
        *,
        lane: ExecutionLane | None = None,
        status: ExecutionStatus | None = None,
    ) -> list[ExecutionSnapshot]:
        return self._runtime.list_snapshots(lane=lane, status=status)

    def resolve_handle(self, handle_or_id: ExecutionHandle | str) -> ExecutionHandle:
        if isinstance(handle_or_id, ExecutionHandle):
            self._remember_handle(handle_or_id)
            return handle_or_id
        handle = self._known_handles.get(str(handle_or_id))
        if handle is not None:
            return handle
        raise KeyError(f"Unknown execution handle id: {handle_or_id}")

    def list_known_handles(self) -> list[ExecutionHandle]:
        return list(self._known_handles.values())

    async def close(self, *, cancel_running: bool = True) -> None:
        await self._runtime.close(cancel_running=cancel_running)
        self._known_handles.clear()

    async def _collect_process_output(
        self,
        handle: ExecutionHandle,
        *,
        stdout_lines: list[str],
        stderr_lines: list[str],
        output_line_limit: int,
        deadline: float | None,
    ) -> None:
        """Collect process output with timeout protection to prevent permanent blocking.

        Args:
            handle: Process execution handle.
            stdout_lines: List to append stdout lines to.
            stderr_lines: List to append stderr lines to.
            output_line_limit: Maximum number of lines to collect.
            deadline: Wall-clock deadline (using time.monotonic()).
        """
        stream_iter = handle.stream().__aiter__()
        start_time = time.monotonic()

        while True:
            if deadline is not None:
                elapsed = time.monotonic() - start_time
                remaining = deadline - elapsed
                if remaining <= 0:
                    break
                timeout = min(remaining, 1.0)
            else:
                timeout = 1.0

            try:
                chunk = await asyncio.wait_for(
                    stream_iter.__anext__(),
                    timeout=timeout,
                )
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                if deadline is not None:
                    elapsed = time.monotonic() - start_time
                    if elapsed >= deadline:
                        break
                continue
            except asyncio.CancelledError:
                raise
            except (RuntimeError, ValueError) as exc:
                _logger.debug(
                    "Error collecting output from %s, continuing: %s",
                    handle.execution_id,
                    exc,
                )
                break

            if not chunk.line:
                continue
            if chunk.source.value == "stdout":
                if len(stdout_lines) < output_line_limit:
                    stdout_lines.append(chunk.line)
                continue
            if len(stderr_lines) < output_line_limit:
                stderr_lines.append(chunk.line)

    def _remember_handle(self, handle: ExecutionHandle) -> None:
        self._known_handles[handle.execution_id] = handle

    def _normalize_handles(
        self,
        handles_or_ids: Iterable[ExecutionHandle | str],
    ) -> list[ExecutionHandle]:
        handles: list[ExecutionHandle] = []
        seen: set[str] = set()
        for item in handles_or_ids:
            handle = self.resolve_handle(item)
            execution_id = handle.execution_id
            if execution_id in seen:
                continue
            seen.add(execution_id)
            handles.append(handle)
        return handles


_SHARED_FACADES: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, ExecutionFacade] = weakref.WeakKeyDictionary()


def get_shared_execution_facade(
    *,
    loop: asyncio.AbstractEventLoop | None = None,
) -> ExecutionFacade:
    """Return loop-scoped shared facade for execution runtime."""

    target_loop = loop or asyncio.get_running_loop()
    facade = _SHARED_FACADES.get(target_loop)
    if facade is None:
        runtime = get_shared_execution_runtime(loop=target_loop)
        facade = ExecutionFacade(runtime=runtime)
        _SHARED_FACADES[target_loop] = facade
    return facade


async def reset_shared_execution_facade(
    *,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Dispose loop-scoped shared facade and runtime."""

    target_loop = loop or asyncio.get_running_loop()
    _SHARED_FACADES.pop(target_loop, None)
    await reset_shared_execution_runtime(loop=target_loop)


_T = TypeVar("_T")


def run_sync(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run a coroutine from synchronous context, handling nested event loops.

    This is a utility for code that may be called from both sync and async contexts.

    - If no event loop is running, uses asyncio.run() directly.
    - If an event loop is running, runs the coroutine in a separate thread
      with its own event loop to avoid nested event loop RuntimeError.

    Args:
        coro: The coroutine to run.

    Returns:
        The result of the coroutine.

    Raises:
        The exception raised by the coroutine if it fails.

    Example:
        # Called from sync context
        result = run_sync(facade.run_process(spec))

        # Called from async context (safe - runs in thread)
        result = run_sync(facade.run_process(spec))
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop, safe to use asyncio.run()
        return asyncio.run(coro)

    # Already in an event loop - run in a separate thread with its own loop
    # This avoids nested event loop RuntimeError
    import threading

    result: Any = None
    exception: BaseException | None = None

    def run_in_thread() -> None:
        nonlocal result, exception
        try:
            result = asyncio.run(coro)
        except (RuntimeError, ValueError) as e:
            # Capture all exceptions including CancelledError for proper handling
            exception = e

    thread = threading.Thread(target=run_in_thread)
    thread.start()
    thread.join()

    if exception is not None:
        _logger.debug(
            "run_sync: coroutine raised %s: %s",
            type(exception).__name__,
            exception,
        )
        raise exception
    return result


__all__ = [
    "AsyncTaskSpec",
    "BatchCancelResult",
    "BatchWaitResult",
    "BlockingIoSpec",
    "ExecutionFacade",
    "ExecutionSpec",
    "ProcessRunResult",
    "ProcessSpec",
    "get_shared_execution_facade",
    "reset_shared_execution_facade",
    "run_sync",
]
