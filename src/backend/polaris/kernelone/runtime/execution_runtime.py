"""Unified execution substrate for KernelOne runtime.

This module consolidates async tasks, blocking I/O offload, and subprocess
execution behind one tracked runtime. The goal is to give upper layers a
single place for concurrency limits, timeout handling, cancellation, and
execution state visibility without introducing Polaris business semantics.

Design constraints:
    - KernelOne-only: no business state ownership or workflow policy.
    - Async-first: callers interact through async handles and snapshots.
    - Threads are only for blocking I/O offload, not long-running orchestration.
    - Subprocess lane must reclaim timed-out processes to reduce orphan risk.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from functools import partial
from typing import TYPE_CHECKING, Any

from polaris.kernelone.constants import (
    EXECUTION_CLEANUP_THRESHOLD,
    EXECUTION_DEFAULT_ASYNC_CONCURRENCY,
    EXECUTION_DEFAULT_BLOCKING_CONCURRENCY,
    EXECUTION_DEFAULT_PROCESS_CONCURRENCY,
    EXECUTION_DEFAULT_PROCESS_TIMEOUT_SECONDS,
    EXECUTION_MAX_RETAINED_STATES,
    EXECUTION_MAX_TERMINAL_STATES,
)
from polaris.kernelone.process.async_contracts import (
    PopenAsyncHandle,
    StreamChunk,
    StreamResult,
    SubprocessPopenRunner,
)
from polaris.kernelone.trace import create_task_with_context, get_tracer
from polaris.kernelone.utils.time_utils import utc_now as _utc_now

from .metrics import get_metrics

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from pathlib import Path

logger = logging.getLogger(__name__)


class ExecutionLane(Enum):
    """Unified runtime execution lanes."""

    ASYNC_TASK = "async_task"
    BLOCKING_IO = "blocking_io"
    SUBPROCESS = "subprocess"


class ExecutionStatus(Enum):
    """Lifecycle state shared by all execution lanes."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {
            ExecutionStatus.SUCCESS,
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMED_OUT,
            ExecutionStatus.CANCELLED,
        }


@dataclass(frozen=True, slots=True)
class ExecutionSnapshot:
    """Immutable execution state snapshot."""

    execution_id: str
    name: str
    lane: ExecutionLane
    status: ExecutionStatus
    submitted_at: datetime
    timeout_seconds: float | None
    metadata: dict[str, Any]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: Any = None
    error: str = ""
    pid: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == ExecutionStatus.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "name": self.name,
            "lane": self.lane.value,
            "status": self.status.value,
            "submitted_at": self.submitted_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "timeout_seconds": self.timeout_seconds,
            "metadata": dict(self.metadata),
            "error": self.error,
            "pid": self.pid,
            "ok": self.ok,
        }


@dataclass(slots=True)
class _ExecutionState:
    execution_id: str
    name: str
    lane: ExecutionLane
    status: ExecutionStatus
    submitted_at: datetime
    timeout_seconds: float | None
    metadata: dict[str, Any]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: Any = None
    error: str = ""
    pid: int | None = None
    task: asyncio.Task[Any] | None = None
    process_handle: PopenAsyncHandle | None = None
    completed: asyncio.Event = field(default_factory=asyncio.Event)

    def snapshot(self) -> ExecutionSnapshot:
        return ExecutionSnapshot(
            execution_id=self.execution_id,
            name=self.name,
            lane=self.lane,
            status=self.status,
            submitted_at=self.submitted_at,
            timeout_seconds=self.timeout_seconds,
            metadata=dict(self.metadata),
            started_at=self.started_at,
            finished_at=self.finished_at,
            result=self.result,
            error=self.error,
            pid=self.pid,
        )


class ExecutionHandle:
    """Public handle over one execution submission."""

    __slots__ = ("_execution_id", "_runtime")

    def __init__(self, runtime: ExecutionRuntime, execution_id: str) -> None:
        self._runtime = runtime
        self._execution_id = execution_id

    @property
    def execution_id(self) -> str:
        return self._execution_id

    @property
    def pid(self) -> int | None:
        return self.snapshot().pid

    @property
    def process(self) -> Any | None:
        state = self._runtime._require_state(self._execution_id)
        handle = state.process_handle
        if handle is None:
            return None
        return handle.process

    def snapshot(self) -> ExecutionSnapshot:
        return self._runtime.get_snapshot(self._execution_id)

    async def wait(self, timeout: float | None = None) -> ExecutionStatus:
        return await self._runtime.wait(self._execution_id, timeout=timeout)

    async def wait_snapshot(self, timeout: float | None = None) -> ExecutionSnapshot:
        await self.wait(timeout=timeout)
        return self.snapshot()

    async def cancel(self) -> bool:
        return await self._runtime.cancel(self._execution_id)

    async def terminate(self, timeout: float = 5.0) -> bool:
        return await self._runtime.terminate(self._execution_id, timeout=timeout)

    async def stream(self) -> AsyncIterator[StreamChunk]:
        async for chunk in self._runtime.stream(self._execution_id):
            yield chunk


def _summarize_process_failure(result: Any) -> str:
    """Return a compact stderr/stdout tail for failed subprocess snapshots."""
    stderr_lines = getattr(result, "stderr_lines", ())
    stdout_lines = getattr(result, "stdout_lines", ())
    if isinstance(result, dict):
        stderr_lines = result.get("stderr_lines", stderr_lines)
        stdout_lines = result.get("stdout_lines", stdout_lines)

    def normalize(lines: Any) -> list[str]:
        if not isinstance(lines, (list, tuple)):
            return []
        return [str(line) for line in lines if str(line).strip()]

    stderr_tail = normalize(stderr_lines)[-8:]
    stdout_tail = normalize(stdout_lines)[-4:]
    if stderr_tail:
        return "\n".join(stderr_tail)
    if stdout_tail:
        return "\n".join(stdout_tail)
    return "subprocess exited with non-zero status"


class ExecutionRuntime:
    """Unified execution runtime with lane-based concurrency control."""

    def __init__(
        self,
        *,
        async_concurrency: int = EXECUTION_DEFAULT_ASYNC_CONCURRENCY,
        blocking_concurrency: int = EXECUTION_DEFAULT_BLOCKING_CONCURRENCY,
        process_concurrency: int = EXECUTION_DEFAULT_PROCESS_CONCURRENCY,
        process_runner_factory: Callable[[], SubprocessPopenRunner] | None = None,
        max_retained_states: int | None = None,
    ) -> None:
        self._async_semaphore = asyncio.Semaphore(max(1, async_concurrency))
        self._blocking_semaphore = asyncio.Semaphore(max(1, blocking_concurrency))
        self._process_semaphore = asyncio.Semaphore(max(1, process_concurrency))
        self._blocking_executor = ThreadPoolExecutor(
            max_workers=max(1, blocking_concurrency),
            thread_name_prefix="kernelone-blocking-io",
        )
        self._process_runner_factory = process_runner_factory or SubprocessPopenRunner
        self._states: dict[str, _ExecutionState] = {}
        self._closed = False
        self._max_retained_states = max_retained_states or EXECUTION_MAX_RETAINED_STATES
        self._cleanup_triggered = False

    def submit_async(
        self,
        *,
        name: str,
        coroutine_factory: Callable[[], Awaitable[Any]],
        timeout_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionHandle:
        state = self._register_state(
            name=name,
            lane=ExecutionLane.ASYNC_TASK,
            timeout_seconds=timeout_seconds,
            metadata=metadata,
        )
        state.task = create_task_with_context(
            self._run_async_submission(state, coroutine_factory),
            name=f"kernelone-exec-{state.execution_id}",
        )
        return ExecutionHandle(self, state.execution_id)

    def submit_blocking(
        self,
        *,
        name: str,
        func: Callable[..., Any],
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionHandle:
        state = self._register_state(
            name=name,
            lane=ExecutionLane.BLOCKING_IO,
            timeout_seconds=timeout_seconds,
            metadata=metadata,
        )
        state.task = create_task_with_context(
            self._run_blocking_submission(
                state,
                func,
                args=args,
                kwargs=kwargs or {},
            ),
            name=f"kernelone-exec-{state.execution_id}",
        )
        return ExecutionHandle(self, state.execution_id)

    async def submit_process(
        self,
        *,
        name: str,
        args: list[str],
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdin_lines: list[str] | None = None,
        timeout_seconds: float | None = EXECUTION_DEFAULT_PROCESS_TIMEOUT_SECONDS,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionHandle:
        """Submit a subprocess execution task.

        Args:
            name: Task name for identification.
            args: Command arguments to execute.
            cwd: Working directory for the subprocess.
            env: Environment variables for the subprocess.
            stdin_lines: Lines to write to stdin.
            timeout_seconds: Timeout in seconds (default: 300).
            metadata: Additional metadata dictionary.

        Returns:
            ExecutionHandle: Handle to monitor the execution.

        Raises:
            RuntimeError: If runtime is closed.
            Exception: Propagates from subprocess spawn or runner factory.
        """
        state = self._register_state(
            name=name,
            lane=ExecutionLane.SUBPROCESS,
            timeout_seconds=timeout_seconds,
            metadata=metadata,
        )
        task_started = False

        await self._process_semaphore.acquire()
        try:
            state.started_at = _utc_now()
            state.status = ExecutionStatus.RUNNING

            runner = self._process_runner_factory()
            process_handle = await runner.spawn(
                args=args,
                cwd=cwd,
                env=env,
                stdin_lines=stdin_lines,
                timeout=int(timeout_seconds or EXECUTION_DEFAULT_PROCESS_TIMEOUT_SECONDS),
            )
            state.process_handle = process_handle
            state.pid = process_handle.pid
            state.task = create_task_with_context(
                self._await_process_completion(state),
                name=f"kernelone-exec-{state.execution_id}",
            )
            task_started = True
            return ExecutionHandle(self, state.execution_id)
        except (RuntimeError, ValueError) as exc:
            self._mark_failed(state, exc)
            raise
        finally:
            # Release semaphore if task wasn't started successfully.
            # If task started, _await_process_completion handles release.
            if not task_started:
                self._process_semaphore.release()

    def get_snapshot(self, execution_id: str) -> ExecutionSnapshot:
        return self._require_state(execution_id).snapshot()

    def list_snapshots(
        self,
        *,
        lane: ExecutionLane | None = None,
        status: ExecutionStatus | None = None,
    ) -> list[ExecutionSnapshot]:
        snapshots: list[ExecutionSnapshot] = []
        for state in self._states.values():
            if lane is not None and state.lane != lane:
                continue
            if status is not None and state.status != status:
                continue
            snapshots.append(state.snapshot())
        snapshots.sort(key=lambda item: item.submitted_at)
        return snapshots

    async def wait(
        self,
        execution_id: str,
        *,
        timeout: float | None = None,
    ) -> ExecutionStatus:
        state = self._require_state(execution_id)
        if state.status.terminal:
            return state.status
        try:
            await asyncio.wait_for(state.completed.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"Execution {execution_id} did not complete within {timeout} seconds",
            ) from exc
        return state.status

    async def cancel(self, execution_id: str) -> bool:
        state = self._require_state(execution_id)
        if state.status.terminal:
            return False

        if state.lane == ExecutionLane.SUBPROCESS and state.process_handle is not None:
            return await self.terminate(execution_id)

        task = state.task
        if task is None:
            state.status = ExecutionStatus.CANCELLED
            state.finished_at = _utc_now()
            state.completed.set()
            return True

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        if not state.status.terminal:
            state.status = ExecutionStatus.CANCELLED
            state.error = "execution cancelled"
            state.finished_at = _utc_now()
            state.completed.set()
        return True

    async def terminate(
        self,
        execution_id: str,
        *,
        timeout: float = 5.0,
    ) -> bool:
        state = self._require_state(execution_id)
        if state.status.terminal:
            return False

        if state.lane != ExecutionLane.SUBPROCESS:
            return await self.cancel(execution_id)

        handle = state.process_handle
        if handle is None:
            return await self.cancel(execution_id)

        terminated = await handle.terminate(timeout=timeout)
        task = state.task
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return terminated

    async def stream(self, execution_id: str) -> AsyncIterator[StreamChunk]:
        state = self._require_state(execution_id)
        if state.lane != ExecutionLane.SUBPROCESS:
            return

        while state.process_handle is None and not state.status.terminal:
            await asyncio.sleep(0.01)
            state = self._require_state(execution_id)

        handle = state.process_handle
        if handle is None:
            return

        async for chunk in handle.stream():
            yield chunk

    async def close(self, *, cancel_running: bool = True) -> None:
        """Close the runtime and optionally cancel running executions.

        Args:
            cancel_running: If True, cancel all running executions before closing.
        """
        if self._closed:
            return
        self._closed = True

        if cancel_running:
            active_ids = [state.execution_id for state in self._states.values() if not state.status.terminal]
            for execution_id in active_ids:
                with contextlib.suppress(Exception):
                    await self.cancel(execution_id)

        self._blocking_executor.shutdown(wait=False, cancel_futures=True)

    def health_check(self) -> dict[str, Any]:
        """Perform runtime health check.

        Returns a dictionary with current runtime health status including
        active executions, state counts, and concurrency availability.

        Returns:
            Health status dictionary with the following structure:
            - healthy: Overall health status (always True if runtime is accessible)
            - timestamp: Current UTC timestamp
            - runtime: Runtime state information
            - concurrency: Available concurrency slots per lane
        """
        metrics = get_metrics()

        return {
            "healthy": True,
            "timestamp": _utc_now().isoformat(),
            "runtime": {
                "active_executions": dict(metrics.active_executions),
                "states_retained": metrics.states_retained,
                "states_active": metrics.states_active,
                "closed": self._closed,
            },
            "concurrency": {
                "async_available": self._async_semaphore._value,
                "blocking_available": self._blocking_semaphore._value,
                "process_available": self._process_semaphore._value,
            },
        }

    def get_metrics_text(self) -> str:
        """Get Prometheus-formatted metrics text.

        Returns:
            Multi-line Prometheus text exposition format string.
        """
        return get_metrics().to_prometheus_text()

    def _register_state(
        self,
        *,
        name: str,
        lane: ExecutionLane,
        timeout_seconds: float | None,
        metadata: dict[str, Any] | None,
    ) -> _ExecutionState:
        if self._closed:
            raise RuntimeError("ExecutionRuntime is closed")
        execution_id = f"exec-{uuid.uuid4().hex[:12]}"
        state = _ExecutionState(
            execution_id=execution_id,
            name=str(name or lane.value),
            lane=lane,
            status=ExecutionStatus.QUEUED,
            submitted_at=_utc_now(),
            timeout_seconds=timeout_seconds,
            metadata=dict(metadata or {}),
        )
        self._states[execution_id] = state
        self._check_and_schedule_cleanup(state)
        return state

    def _check_and_schedule_cleanup(self, state: _ExecutionState) -> None:
        """Check if cleanup should be triggered and schedule it if needed.

        Cleanup is triggered when:
        1. A terminal state is added
        2. Total state count exceeds the threshold

        Args:
            state: The state that was just registered.
        """
        if not state.status.terminal or self._cleanup_triggered:
            return

        threshold = int(self._max_retained_states * EXECUTION_CLEANUP_THRESHOLD)
        if len(self._states) >= threshold:
            self._schedule_cleanup()

    def _schedule_cleanup(self) -> None:
        """Schedule an async cleanup task to compact states.

        Uses call_later to schedule cleanup asynchronously, avoiding
        blocking the caller. Multiple calls are deduplicated via
        _cleanup_triggered flag.
        """
        if self._cleanup_triggered:
            return

        self._cleanup_triggered = True

        try:
            loop = asyncio.get_running_loop()

            # call_later callback must return None
            def _schedule_compact() -> None:
                """Schedule compact after delay. call_later requires () -> None."""
                _ = asyncio.ensure_future(self._compact_states())  # noqa: RUF006

            loop.call_later(0.1, _schedule_compact)
        except RuntimeError:
            # No running event loop, clear flag and cleanup will happen
            # on the next registration with a running loop
            self._cleanup_triggered = False

    async def _compact_states(self) -> None:
        """Compact the states dictionary by removing old terminal states.

        Cleanup policy:
        1. All non-terminal states (running executions) are preserved
        2. Terminal states are sorted by completion time, keeping the newest

        Raises:
            RuntimeError: If the event loop is closed.
        """
        if not self._states:
            self._cleanup_triggered = False
            return

        terminal_states = {eid: state for eid, state in self._states.items() if state.status.terminal}
        non_terminal_states = {eid: state for eid, state in self._states.items() if not state.status.terminal}

        if not terminal_states:
            self._cleanup_triggered = False
            return

        # Sort terminal states by completion time (newest first)
        sorted_terminal = sorted(
            terminal_states.items(),
            key=lambda item: item[1].finished_at or item[1].started_at or item[1].submitted_at,
            reverse=True,
        )

        # Keep only the most recent terminal states
        kept_terminal = dict(sorted_terminal[:EXECUTION_MAX_TERMINAL_STATES])

        # Rebuild states dict preserving all non-terminal states
        self._states = {**kept_terminal, **non_terminal_states}
        self._cleanup_triggered = False

        logger.debug(
            "States compacted: total=%d kept_terminal=%d non_terminal=%d max_allowed=%d",
            len(self._states),
            len(kept_terminal),
            len(non_terminal_states),
            self._max_retained_states,
        )

    @property
    def states_count(self) -> int:
        """Return the current number of tracked states."""
        return len(self._states)

    @property
    def active_states_count(self) -> int:
        """Return the number of active (non-terminal) states."""
        return sum(1 for state in self._states.values() if not state.status.terminal)

    def _require_state(self, execution_id: str) -> _ExecutionState:
        state = self._states.get(execution_id)
        if state is None:
            raise KeyError(f"Unknown execution_id: {execution_id}")
        return state

    async def _run_async_submission(
        self,
        state: _ExecutionState,
        coroutine_factory: Callable[[], Awaitable[Any]],
    ) -> None:
        await self._run_with_lane(
            state,
            semaphore=self._async_semaphore,
            runner=coroutine_factory,
        )

    async def _run_blocking_submission(
        self,
        state: _ExecutionState,
        func: Callable[..., Any],
        *,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        async def runner() -> Any:
            loop = asyncio.get_running_loop()
            call = partial(func, *args, **kwargs)
            return await loop.run_in_executor(self._blocking_executor, call)

        await self._run_with_lane(
            state,
            semaphore=self._blocking_semaphore,
            runner=runner,
        )

    async def _run_with_lane(
        self,
        state: _ExecutionState,
        *,
        semaphore: asyncio.Semaphore,
        runner: Callable[[], Awaitable[Any]],
    ) -> None:
        """Run task on specified execution lane with tracing and metrics.

        Args:
            state: Execution state object.
            semaphore: Concurrency limit semaphore.
            runner: Coroutine factory to execute.
        """
        tracer = get_tracer()
        metrics = get_metrics()
        span_name = f"{state.lane.value}.{state.name or state.execution_id}"
        start_time = time.monotonic()

        with tracer.span(span_name) as span:
            span.set_tag("execution_id", state.execution_id)
            span.set_tag("lane", state.lane.value)
            if state.timeout_seconds:
                span.set_tag("timeout_seconds", state.timeout_seconds)

            metrics.record_start(state.lane.value)

            try:
                async with semaphore:
                    state.started_at = _utc_now()
                    state.status = ExecutionStatus.RUNNING

                    if state.timeout_seconds and state.timeout_seconds > 0:
                        state.result = await asyncio.wait_for(
                            runner(),
                            timeout=state.timeout_seconds,
                        )
                    else:
                        state.result = await runner()

                    state.status = ExecutionStatus.SUCCESS
                    span.set_tag("status", "success")

            except asyncio.TimeoutError:
                state.status = ExecutionStatus.TIMED_OUT
                state.error = f"{state.lane.value} execution timed out after {state.timeout_seconds} seconds"
                span.set_tag("status", "timed_out")
                span.set_tag("error", state.error)

            except asyncio.CancelledError:
                state.status = ExecutionStatus.CANCELLED
                state.error = "execution cancelled"
                span.set_tag("status", "cancelled")
                raise

            except (RuntimeError, ValueError) as exc:
                self._mark_failed(state, exc)
                span.set_tag("status", "failed")
                span.set_tag("error", str(exc))
                raise

            finally:
                duration = time.monotonic() - start_time
                span.set_tag("duration_seconds", duration)

                metrics.record_end(
                    state.lane.value,
                    state.status.value,
                    duration,
                )
                metrics.update_states(
                    len(self._states),
                    sum(1 for s in self._states.values() if not s.status.terminal),
                )

                state.finished_at = _utc_now()
                state.completed.set()

    async def _await_process_completion(self, state: _ExecutionState) -> None:
        """Wait for process completion and handle all lifecycle scenarios.

        This method handles:
        - Normal process exit (SUCCESS/FAILED)
        - Timeout with guaranteed process termination
        - Cancellation with graceful termination attempt
        - Unexpected exceptions with cleanup

        Args:
            state: Execution state tracking the process lifecycle.
        """
        import subprocess

        tracer = get_tracer()
        metrics = get_metrics()
        span_name = f"subprocess.{state.name or state.execution_id}"
        start_time = time.monotonic()

        with tracer.span(span_name) as span:
            span.set_tag("execution_id", state.execution_id)
            span.set_tag("lane", state.lane.value)
            if state.pid:
                span.set_tag("pid", state.pid)
            if state.timeout_seconds:
                span.set_tag("timeout_seconds", state.timeout_seconds)

            handle = state.process_handle
            if handle is None:
                state.status = ExecutionStatus.FAILED
                state.error = "process handle missing"
                span.set_tag("status", "failed")
                span.set_tag("error", "process handle missing")
                state.finished_at = _utc_now()
                state.completed.set()
                self._process_semaphore.release()
                return

            try:
                process_status = await handle.wait(timeout=state.timeout_seconds)
                if process_status.value == ExecutionStatus.SUCCESS.value:
                    state.status = ExecutionStatus.SUCCESS
                    span.set_tag("status", "success")
                elif process_status.value == ExecutionStatus.FAILED.value:
                    state.status = ExecutionStatus.FAILED
                    span.set_tag("status", "failed")
                elif process_status.value == ExecutionStatus.CANCELLED.value:
                    state.status = ExecutionStatus.CANCELLED
                    span.set_tag("status", "cancelled")
                else:
                    state.status = ExecutionStatus.TIMED_OUT
                    state.error = f"subprocess timed out after {state.timeout_seconds} seconds"
                    span.set_tag("status", "timed_out")
                    span.set_tag("error", state.error)
                    await self._handle_process_timeout(state, handle, span)

                with contextlib.suppress(RuntimeError):
                    state.result = await handle.result()

                if state.status == ExecutionStatus.FAILED and not state.error:
                    state.error = _summarize_process_failure(state.result)
                    if state.error:
                        span.set_tag("error", state.error)

            except subprocess.TimeoutExpired:
                state.status = ExecutionStatus.TIMED_OUT
                state.error = f"subprocess timed out after {state.timeout_seconds} seconds"
                span.set_tag("status", "timed_out")
                span.set_tag("error", state.error)
                await self._handle_process_timeout(state, handle, span)

            except asyncio.CancelledError:
                if not state.status.terminal:
                    state.status = ExecutionStatus.CANCELLED
                    state.error = "execution cancelled"
                    span.set_tag("status", "cancelled")
                    with contextlib.suppress(Exception):
                        await handle.terminate(timeout=1.0)
                raise

            except (RuntimeError, ValueError) as exc:
                self._mark_failed(state, exc)
                span.set_tag("status", "failed")
                span.set_tag("error", str(exc))
                with contextlib.suppress(Exception):
                    await handle.kill()

            finally:
                duration = time.monotonic() - start_time
                span.set_tag("duration_seconds", duration)

                metrics.record_end(
                    state.lane.value,
                    state.status.value,
                    duration,
                )
                metrics.update_states(
                    len(self._states),
                    sum(1 for s in self._states.values() if not s.status.terminal),
                )

                if state.pid is None:
                    state.pid = handle.pid
                state.finished_at = _utc_now()
                state.completed.set()
                self._process_semaphore.release()

    async def _handle_process_timeout(
        self,
        state: _ExecutionState,
        handle: PopenAsyncHandle,
        span: Any | None = None,
    ) -> None:
        """Handle process timeout with guaranteed termination.

        This method ensures the timed-out process is always terminated:
        1. First attempts graceful termination (SIGTERM)
        2. If graceful termination fails, force-kills the process

        Args:
            state: Execution state to update.
            handle: Process handle to terminate.
            span: Optional span to record termination details.
        """
        logger.warning(
            "Process timed out: execution_id=%s name=%s timeout=%s",
            state.execution_id,
            state.name,
            state.timeout_seconds,
        )

        if span is not None:
            span.add_event("timeout_handling_started")

        try:
            terminated = await handle.terminate(timeout=1.0)
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "terminate() raised exception, forcing kill: execution_id=%s error=%s",
                state.execution_id,
                exc,
            )
            terminated = False

        if not terminated:
            logger.warning(
                "Graceful termination failed, forcing kill: execution_id=%s",
                state.execution_id,
            )
            try:
                await handle.kill()
                get_metrics().record_process_kill()
                if span is not None:
                    span.add_event("process_force_killed")
            except (RuntimeError, ValueError) as exc:
                logger.warning(
                    "kill() raised exception: execution_id=%s error=%s",
                    state.execution_id,
                    exc,
                )
        elif span is not None:
            span.add_event("process_gracefully_terminated")

    def _mark_failed(self, state: _ExecutionState, exc: Exception) -> None:
        logger.warning(
            "KernelOne execution failed: id=%s lane=%s error=%s",
            state.execution_id,
            state.lane.value,
            exc,
        )
        state.status = ExecutionStatus.FAILED
        state.error = str(exc)
        state.finished_at = _utc_now()
        state.completed.set()


_SHARED_RUNTIMES: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, ExecutionRuntime] = weakref.WeakKeyDictionary()


def get_shared_execution_runtime(
    *,
    loop: asyncio.AbstractEventLoop | None = None,
) -> ExecutionRuntime:
    """Return a loop-scoped shared execution runtime."""

    target_loop = loop or asyncio.get_running_loop()
    runtime = _SHARED_RUNTIMES.get(target_loop)
    if runtime is None:
        runtime = ExecutionRuntime()
        _SHARED_RUNTIMES[target_loop] = runtime
    return runtime


async def reset_shared_execution_runtime(
    *,
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Dispose the loop-scoped shared execution runtime."""

    target_loop = loop or asyncio.get_running_loop()
    runtime = _SHARED_RUNTIMES.pop(target_loop, None)
    if runtime is not None:
        await runtime.close()


__all__ = [
    "EXECUTION_CLEANUP_THRESHOLD",
    "EXECUTION_DEFAULT_ASYNC_CONCURRENCY",
    "EXECUTION_DEFAULT_BLOCKING_CONCURRENCY",
    "EXECUTION_DEFAULT_PROCESS_CONCURRENCY",
    "EXECUTION_DEFAULT_PROCESS_TIMEOUT_SECONDS",
    "EXECUTION_MAX_RETAINED_STATES",
    "EXECUTION_MAX_TERMINAL_STATES",
    "ExecutionHandle",
    "ExecutionLane",
    "ExecutionRuntime",
    "ExecutionSnapshot",
    "ExecutionStatus",
    "StreamChunk",
    "StreamResult",
    "get_shared_execution_runtime",
    "reset_shared_execution_runtime",
]
