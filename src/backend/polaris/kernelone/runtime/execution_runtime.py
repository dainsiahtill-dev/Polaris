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
from typing import TYPE_CHECKING, Any, cast

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
from polaris.kernelone.process.process_tree import ProcessTreeDrainError
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
        runner: SubprocessPopenRunner | None = None

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
            resource = (
                runner._take_failed_spawn_resource(exc)
                if runner is not None
                and type(runner) is SubprocessPopenRunner
                and isinstance(exc, ProcessTreeDrainError)
                else None
            )
            if resource is not None:
                state.process_handle = resource
                state.pid = resource.pid
                self._record_process_cleanup(state, False, "official process group binding failed")
                state.metadata["process_slot_retained"] = True
                task_started = True  # This state, not a new task/registry, retains the slot.
            self._mark_failed(state, exc)
            if resource is not None:
                self._record_process_cleanup(state, False, "official process group binding failed")
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
        if state.status.terminal and not self._has_unproven_process(state):
            return False
        if state.lane == ExecutionLane.SUBPROCESS and state.process_handle is not None:
            return await self.terminate(execution_id)
        if self._has_unproven_process(state):
            self._record_process_cleanup(state, False, "process handle missing")
            return False
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

    async def terminate(self, execution_id: str, *, timeout: float = 5.0) -> bool:
        state = self._require_state(execution_id)
        if state.status.terminal and not self._has_unproven_process(state):
            return False
        if state.lane != ExecutionLane.SUBPROCESS:
            return await self.cancel(execution_id)
        handle = state.process_handle
        if handle is None:
            if self._has_unproven_process(state):
                self._record_process_cleanup(state, False, "process handle missing")
                return False
            return await self.cancel(execution_id)
        alive = await self._observe_process_alive(handle)
        if alive is False:
            self._record_process_cleanup(state, True)
            with contextlib.suppress(RuntimeError):
                state.result = await handle.result()
            return True
        if alive is not True:
            self._record_process_cleanup(state, False, "physical liveness unavailable before termination")
            return False
        try:
            terminated = await handle.terminate(timeout=timeout)
        except Exception as exc:
            logger.exception("Owned process termination failed: execution_id=%s", state.execution_id)
            self._record_process_cleanup(state, False, f"terminate failed: {type(exc).__name__}: {exc}")
            return False
        if await self._observe_process_alive(handle) is not False:
            self._record_process_cleanup(state, False, "terminate returned without a confirmed physical exit")
            return False
        self._record_process_cleanup(state, True)
        task = state.task
        if task is not None and not task.done():
            with contextlib.suppress(asyncio.CancelledError):
                await task
        with contextlib.suppress(RuntimeError):
            state.result = await handle.result()
        return cast("bool", (terminated))

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
        """Close logical submissions, retaining any unproven process owner."""
        if self._closed:
            return
        self._closed = True
        if cancel_running:
            active_ids = [
                state.execution_id
                for state in self._states.values()
                if not state.status.terminal or self._has_unproven_process(state)
            ]
            for execution_id in active_ids:
                try:
                    await self.cancel(execution_id)
                except Exception as exc:
                    logger.exception("Runtime close cleanup failed: execution_id=%s", execution_id)
                    state = self._require_state(execution_id)
                    if state.lane == ExecutionLane.SUBPROCESS:
                        self._record_process_cleanup(state, False, f"close cleanup failed: {exc}")
            for state in self._states.values():
                if self._has_unproven_process(state):
                    logger.warning("Runtime closed with physical cleanup UNKNOWN: execution_id=%s", state.execution_id)
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
        """Compact logical history while retaining every unproven process owner."""
        if not self._states:
            self._cleanup_triggered = False
            return
        terminal_states = {
            eid: state
            for eid, state in self._states.items()
            if state.status.terminal and not self._has_unproven_process(state)
        }
        retained_states = {
            eid: state
            for eid, state in self._states.items()
            if not state.status.terminal or self._has_unproven_process(state)
        }
        if not terminal_states:
            self._cleanup_triggered = False
            return
        sorted_terminal = sorted(
            terminal_states.items(),
            key=lambda item: item[1].finished_at or item[1].started_at or item[1].submitted_at,
            reverse=True,
        )
        kept_terminal = dict(sorted_terminal[:EXECUTION_MAX_TERMINAL_STATES])
        self._states = {**kept_terminal, **retained_states}
        self._cleanup_triggered = False
        logger.debug(
            "States compacted: total=%d kept_terminal=%d retained=%d max_allowed=%d",
            len(self._states),
            len(kept_terminal),
            len(retained_states),
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
        """Finish logical processing while retaining unproven physical cleanup."""
        import subprocess

        tracer = get_tracer()
        metrics = get_metrics()
        start_time = time.monotonic()
        with tracer.span(f"subprocess.{state.name or state.execution_id}") as span:
            span.set_tag("execution_id", state.execution_id)
            span.set_tag("lane", state.lane.value)
            if state.pid:
                span.set_tag("pid", state.pid)
            if state.timeout_seconds:
                span.set_tag("timeout_seconds", state.timeout_seconds)
            handle = state.process_handle
            state.metadata["process_cleanup_status"] = "unknown"
            state.metadata["process_slot_retained"] = False
            try:
                if handle is None:
                    state.status = ExecutionStatus.FAILED
                    state.error = "process handle missing"
                    self._record_process_cleanup(state, False, "process handle missing")
                    return
                try:
                    process_status = await handle.wait(timeout=state.timeout_seconds)
                    if process_status.value == ExecutionStatus.SUCCESS.value:
                        state.status = ExecutionStatus.SUCCESS
                    elif process_status.value == ExecutionStatus.FAILED.value:
                        state.status = ExecutionStatus.FAILED
                    elif process_status.value == ExecutionStatus.CANCELLED.value:
                        state.status = ExecutionStatus.CANCELLED
                    else:
                        state.status = ExecutionStatus.TIMED_OUT
                        state.error = f"subprocess timed out after {state.timeout_seconds} seconds"
                        await self._handle_process_timeout(state, handle, span)
                    if state.metadata.get("process_cleanup_status") != "confirmed":
                        if state.status == ExecutionStatus.TIMED_OUT:
                            pass  # The cleanup attempt already recorded UNKNOWN.
                        elif await self._observe_process_alive(handle) is False:
                            self._record_process_cleanup(state, True)
                        else:
                            state.status = ExecutionStatus.FAILED
                            self._record_process_cleanup(state, False, "wait returned without a physical exit")
                except (subprocess.TimeoutExpired, TimeoutError):
                    state.status = ExecutionStatus.TIMED_OUT
                    state.error = f"subprocess timed out after {state.timeout_seconds} seconds"
                    await self._handle_process_timeout(state, handle, span)
                except asyncio.CancelledError:
                    if not state.status.terminal:
                        state.status = ExecutionStatus.CANCELLED
                        state.error = "execution cancelled"
                    await self._handle_process_timeout(state, handle, span)
                    raise
                except Exception as exc:
                    logger.exception("Owned process wait failed: execution_id=%s", state.execution_id)
                    self._mark_failed(state, exc)
                    await self._handle_process_timeout(state, handle, span)
                if state.metadata.get("process_cleanup_status") == "confirmed":
                    with contextlib.suppress(RuntimeError):
                        state.result = await handle.result()
                else:
                    state.result = None
                if state.status == ExecutionStatus.FAILED and not state.error:
                    state.error = _summarize_process_failure(state.result)
                span.set_tag("status", state.status.value)
                if state.error:
                    span.set_tag("error", state.error)
            finally:
                if state.metadata.get("process_cleanup_status") != "confirmed":
                    self._record_process_cleanup(state, False, "physical cleanup was not confirmed")
                    state.metadata["process_slot_retained"] = True
                else:
                    self._process_semaphore.release()
                metrics.record_end(state.lane.value, state.status.value, time.monotonic() - start_time)
                metrics.update_states(
                    len(self._states), sum(1 for item in self._states.values() if not item.status.terminal)
                )
                if state.pid is None and handle is not None:
                    state.pid = handle.pid
                state.finished_at = _utc_now()
                state.completed.set()

    @staticmethod
    def _has_unproven_process(state: _ExecutionState) -> bool:
        return state.lane == ExecutionLane.SUBPROCESS and state.metadata.get("process_cleanup_status") == "unknown"

    @staticmethod
    async def _observe_process_alive(handle: PopenAsyncHandle) -> bool | None:
        try:
            observed: object = await handle.is_alive()
        except Exception:
            logger.exception("Owned process liveness observation failed")
            return None
        return observed if isinstance(observed, bool) else None

    def _record_process_cleanup(self, state: _ExecutionState, confirmed: bool, detail: str = "") -> None:
        previous = state.metadata.get("process_cleanup_status")
        state.metadata["process_cleanup_status"] = "confirmed" if confirmed else "unknown"
        if confirmed:
            if state.metadata.get("process_slot_retained") is True:
                state.metadata["process_slot_retained"] = False
                self._process_semaphore.release()
            if (
                previous == "unknown"
                and "process_cleanup_unproven" in state.error
                and "subsequently confirmed" not in state.error
            ):
                state.error += "; physical cleanup subsequently confirmed"
            return
        state.result = None
        if detail:
            state.metadata["process_cleanup_error"] = detail
        if "process_cleanup_unproven" not in state.error:
            state.error = (
                (state.error + "; " if state.error else "")
                + "process_cleanup_unproven"
                + (": " + detail if detail else "")
            )

    async def _handle_process_timeout(
        self,
        state: _ExecutionState,
        handle: PopenAsyncHandle,
        span: Any | None = None,
    ) -> bool:
        """Attempt bounded owned cleanup; unknown is never physical confirmation."""
        logger.warning(
            "Process timed out: execution_id=%s name=%s timeout=%s",
            state.execution_id,
            state.name,
            state.timeout_seconds,
        )
        state.metadata["process_cleanup_status"] = "unknown"
        confirmed = False
        errors: list[str] = []
        if span is not None:
            span.add_event("timeout_handling_started")
        try:
            alive = await self._observe_process_alive(handle)
            if alive is False:
                confirmed = True
                return True
            if alive is not True:
                errors.append("physical liveness could not be observed")
                return False
            terminated: bool | None = None
            try:
                terminated = await handle.terminate(timeout=1.0)
            except Exception as exc:
                errors.append(f"terminate failed: {type(exc).__name__}: {exc}")
                logger.exception("terminate() failed: execution_id=%s error=%s", state.execution_id, exc)
            alive = await self._observe_process_alive(handle)
            if alive is False:
                confirmed = True
                if terminated is False:
                    get_metrics().record_process_kill()
                    if span is not None:
                        span.add_event("process_force_killed")
                elif span is not None:
                    span.add_event("process_gracefully_terminated")
                return True
            if alive is not True:
                errors.append("physical liveness unavailable after terminate")
                return False
            logger.warning("Graceful termination failed, forcing kill: execution_id=%s", state.execution_id)
            try:
                await handle.kill()
            except Exception as exc:
                errors.append(f"kill failed: {type(exc).__name__}: {exc}")
                logger.exception("kill() failed: execution_id=%s error=%s", state.execution_id, exc)
            confirmed = await self._observe_process_alive(handle) is False
            if confirmed:
                get_metrics().record_process_kill()
                if span is not None:
                    span.add_event("process_force_killed")
            else:
                errors.append("physical exit remains unproven after kill")
            return confirmed
        finally:
            self._record_process_cleanup(state, confirmed, "; ".join(errors))

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
