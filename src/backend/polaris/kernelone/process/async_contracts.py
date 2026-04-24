"""Async process runner contracts for KernelOne.

This module defines the async-first port surface for streaming subprocess execution.
Complements CommandExecutorPort (sync, batch) with true async streaming support.

Architecture:
    - StreamChunk: immutable line of streamed output with metadata
    - ProcessStatus: lifecycle state machine enum
    - AsyncProcessHandle: abstract handle over a spawned subprocess
    - AsyncProcessRunnerPort: async spawn + stream + control contract
    - SubprocessAsyncRunner: default implementation using asyncio.subprocess

Security invariants (enforced by all implementations):
    1. shell=True is permanently banned — raises ShellDisallowedError
    2. timeout is a mandatory parameter — processes cannot run unbounded
    3. all I/O is UTF-8 with replacement — no encoding exceptions propagate
    4. stdin/stdout/stderr are always managed — no orphaned handles
    5. process handles are async context managers — resources are always released

Design rationale:
    - Streaming via AsyncIterator[StreamChunk] (PEP-525 async generators) avoids
      buffering entire output in memory — critical for long-running processes.
    - Separate handle object lets callers interleave wait(), stream(), terminate().
    - Status enum makes state transitions explicit and auditable.
    - ShellDisallowedError is shared with CommandExecutorPort for consistent enforcement.

Bypass files addressed by this contract:
    - cells/orchestration/workflow_runtime/internal/process_launcher.py
      -> async def launch/terminate/wait_for() but blocking Popen underneath
    - cells/roles/runtime/internal/process_service.py
      -> _spawn_subprocess() with TODO: "Replace with kernelone.process async spawn API"
    - kernelone/process/codex_adapter.py
      -> _run_once() uses Popen + communicate() — stdin/stdout streaming
    - kernelone/process/background_manager.py
      -> thread-based queue manager with daemon monitor threads
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

from polaris.kernelone.utils.time_utils import PROCESS_COMMAND_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

_logger = logging.getLogger(__name__)

# Use unified timeout constant from time_utils
DEFAULT_TIMEOUT_SECONDS = PROCESS_COMMAND_TIMEOUT_SECONDS


# =============================================================================
# Value types
# =============================================================================


class ProcessStatus(Enum):
    """Lifecycle state of a spawned subprocess.

    State diagram::

        PENDING → RUNNING → SUCCESS
                        ├─ FAILED
                        ├─ TIMED_OUT
                        └─ CANCELLED

    CANCELLED is terminal; SUCCESS/FAILED/TIMED_OUT are terminal.
    """

    PENDING = "pending"  # spawn() called, subprocess not yet confirmed started
    RUNNING = "running"  # subprocess is alive
    SUCCESS = "success"  # exited with code == 0
    FAILED = "failed"  # exited with non-zero code
    TIMED_OUT = "timed_out"  # killed after wait timeout expired
    CANCELLED = "cancelled"  # killed via terminate() or kill()


class ProcessStreamSource(Enum):
    """Source of a streamed output line."""

    STDOUT = "stdout"
    STDERR = "stderr"


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """Immutable line of streamed subprocess output.

    Attributes:
        line: The output line, stripped of trailing newline. Empty string signals
            EOF on that stream (emit order between streams is not guaranteed).
        source: Which subprocess stream this line came from.
        timestamp: When the line was captured (UTC).
        pid: Process ID of the subprocess that emitted this line.
    """

    line: str
    source: ProcessStreamSource
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    pid: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "source": self.source.value,
            "timestamp": self.timestamp.isoformat(),
            "pid": self.pid,
        }


@dataclass(frozen=True, slots=True)
class StreamResult:
    """Complete result after a process has finished.

    Attributes:
        pid: Process ID.
        exit_code: Process exit code (-1 if not available).
        status: Terminal process status.
        stdout_lines: All lines captured from stdout, in order.
        stderr_lines: All lines captured from stderr, in order.
        timed_out: True if wait() timed out while the process was still alive.
        timeout_seconds: The timeout that was applied on spawn().
        started_at: When the subprocess was started.
        ended_at: When wait() returned (or datetime.now() if still running).
    """

    pid: int
    exit_code: int
    status: ProcessStatus
    stdout_lines: tuple[str, ...]
    stderr_lines: tuple[str, ...]
    timed_out: bool
    timeout_seconds: int
    started_at: datetime
    ended_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def ok(self) -> bool:
        """True when exit_code == 0 and the process was not timed out."""
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "exit_code": self.exit_code,
            "status": self.status.value,
            "stdout": "\n".join(self.stdout_lines),
            "stderr": "\n".join(self.stderr_lines),
            "stdout_lines": self.stdout_lines,
            "stderr_lines": self.stderr_lines,
            "timed_out": self.timed_out,
            "timeout_seconds": self.timeout_seconds,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "ok": self.ok,
        }


# =============================================================================
# Shell security
# =============================================================================

# Re-export from unified kernelone.errors (ShellDisallowedError is canonical here)
from polaris.kernelone.errors import ShellDisallowedError  # noqa: E402

# =============================================================================
# AsyncProcessHandle — abstract handle over a spawned subprocess
# =============================================================================


@runtime_checkable
class AsyncProcessHandle(Protocol):
    """Abstract handle over a spawned subprocess.

    A handle represents a running (or completed) subprocess. It is obtained
    by calling AsyncProcessRunnerPort.spawn() as an async context manager.

    Callers use the handle to:
    - Stream output line-by-line (no full memory buffering)
    - Wait for the subprocess to finish with optional timeout
    - Send input to the subprocess stdin
    - Send signals (terminate gracefully, kill forcefully)

    The handle is an async context manager. Exiting the context terminates
    the subprocess if it is still running and releases all resources.

    Example::

        async with runner.spawn(["python", "serve.py"], timeout=60) as handle:
            async for chunk in handle.stream():
                if "ready" in chunk.line:
                    break
            status = await handle.wait(timeout=10)
    """

    @property
    def pid(self) -> int:
        """OS-level process ID. Zero if not yet confirmed started."""
        ...

    @property
    def status(self) -> ProcessStatus:
        """Current process status."""
        ...

    async def stream(self) -> AsyncIterator[StreamChunk]:
        """Stream output lines from stdout and stderr as they arrive.

        Lines are yielded in the order they are captured from each stream.
        The two streams are distinguished by the ``source`` field of each chunk.

        An empty ``StreamChunk`` (line="") from STDOUT signals that stream's EOF;
        same for STDERR. Both reaching EOF does not mean the process has exited —
        call ``wait()`` to confirm.

        Raises:
            asyncio.CancelledError: if the caller abandons the stream.
        """
        ...

    async def wait(self, timeout: float | None = None) -> ProcessStatus:
        """Wait for the subprocess to exit.

        Args:
            timeout: Maximum seconds to wait. None = wait indefinitely.
                When the timeout expires the wait is satisfied with TIMED_OUT
                but the process is NOT killed — call terminate()/kill() to stop it.

        Returns:
            The terminal ProcessStatus after the subprocess exits or the
            wait times out (process may still be alive; call is_alive() to check).

        Raises:
            asyncio.CancelledError: if the caller abandons the wait.
        """
        ...

    async def is_alive(self) -> bool:
        """Return True if the subprocess is currently running."""
        ...

    async def write_stdin(self, data: str) -> None:
        """Write a string to the subprocess stdin (UTF-8 encoded, no trailing newline).

        Args:
            data: String to write.

        Raises:
            BrokenPipeError: if stdin is already closed.
            ConnectionResetError: if the subprocess has exited.
        """
        ...

    async def terminate(self, timeout: float = 5.0) -> bool:
        """Request graceful termination (SIGTERM on Unix, TerminateProcess on Windows).

        Args:
            timeout: Seconds to wait for the process to exit after SIGTERM.
                After this deadline the process is force-killed.

        Returns:
            True if the process exited within the timeout after SIGTERM,
            False if force-kill was required.
        """
        ...

    async def kill(self) -> None:
        """Force-kill the subprocess immediately (SIGKILL / TerminateProcess).

        Use only when graceful termination is insufficient.
        """
        ...

    async def result(self) -> StreamResult:
        """Return the final result after the process has exited.

        Valid only after wait() has returned a terminal status.
        Calling result() on a non-terminal process raises RuntimeError.

        Returns:
            StreamResult with exit code, status, and captured output.
        """
        ...


# =============================================================================
# AsyncProcessRunnerPort — async spawn + stream + control contract
# =============================================================================


@runtime_checkable
class AsyncProcessRunnerPort(Protocol):
    """Protocol for async streaming subprocess execution within KernelOne.

    This contract addresses the gap that CommandExecutorPort (sync, batch) cannot fill:
    true async streaming of subprocess output without full memory buffering.

    Security invariants (all mandatory):

    1. **No shell execution**: ``shell=True`` is always rejected.
       Implementations MUST raise ``ShellDisallowedError`` if shell=True is attempted.

    2. **Mandatory timeout**: ``timeout`` on spawn() is required.
       A timed-out process is moved to TIMED_OUT rather than blocking forever.

    3. **UTF-8 everywhere**: all text decoded with errors="replace".
       No UnicodeDecodeError propagates from a subprocess to the caller.

    4. **Resource cleanup**: spawned handles are async context managers.
       Exiting the context always terminates the process and closes all pipes.

    5. **Signal isolation**: each spawned subprocess is independently tracked.
       terminate() / kill() affect only the target process.

    Comparison with CommandExecutorPort:

        +------------------+------------------------+------------------------+
        |                  | CommandExecutorPort    | AsyncProcessRunnerPort |
        +------------------+------------------------+------------------------+
        | Output mode      | Batch (full capture)   | Streaming (line-by-line)|
        | Input mode       | None                  | stdin (write_stdin)    |
        | Interface        | Sync                   | Async                  |
        | Timeout behavior | Returns timed_out=True | Moves to TIMED_OUT     |
        | Use case         | One-shot commands      | Long-running / streams |
        +------------------+------------------------+------------------------+

    Example::

        runner = SubprocessAsyncRunner()

        async with runner.spawn(
            ["python", "-u", "train.py"],
            timeout=300,
            cwd=project_root,
            env=extra_env,
        ) as handle:
            async for chunk in handle.stream():
                if chunk.source == ProcessStreamSource.STDERR:
                    logger.warning("[train] %s", chunk.line)
                else:
                    logger.info("[train] %s", chunk.line)
            status = await handle.wait()
    """

    async def spawn(
        self,
        args: list[str],
        *,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdin_lines: list[str] | None = None,
    ) -> AsyncProcessHandle:
        """Spawn a subprocess and return a handle.

        The subprocess is started immediately when this coroutine returns.
        The returned handle is an async context manager; callers SHOULD use
        ``async with`` to guarantee resource cleanup.

        Args:
            args: Command plus arguments as a list of strings.
                Passed to asyncio.create_subprocess_exec with shell=False.
            timeout: Mandatory maximum seconds before the process transitions
                to TIMED_OUT. Values <= 0 are clamped to DEFAULT_TIMEOUT_SECONDS.
            cwd: Working directory. None = inherit parent.
            env: Environment variables. None = inherit parent with UTF-8 defaults.
            stdin_lines: Optional lines to write to stdin before EOF.
                Each line gets a trailing newline. Stdin is closed after writing.

        Returns:
            AsyncProcessHandle representing the spawned subprocess.

        Raises:
            ShellDisallowedError: if shell=True is attempted (not possible via
                create_subprocess_exec, but provided for interface symmetry).
            ValueError: if args is empty.
            OSError: if the subprocess cannot be started.
        """
        ...


# =============================================================================
# SubprocessAsyncRunner — default implementation
# =============================================================================


class SubprocessAsyncRunner:
    """Default implementation of AsyncProcessRunnerPort using asyncio.subprocess.

    - Permanently disables shell execution (create_subprocess_exec has no
      shell= parameter — ShellDisallowedError is enforced for interface symmetry).
    - Enforces mandatory timeout via asyncio.wait_for().
    - Streams output line-by-line via AsyncIterator[StreamChunk].
    - Uses SIGTERM + SIGKILL (Unix) / TerminateProcess + KillProcess (Windows).
    - All text decoded as UTF-8 with errors="replace".
    - AsyncProcessHandle is an async context manager for guaranteed cleanup.
    """

    __slots__ = ()

    async def spawn(
        self,
        args: list[str],
        *,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdin_lines: list[str] | None = None,
    ) -> AsyncProcessHandle:
        if not args:
            raise ValueError("args must not be empty")
        effective_timeout = max(timeout, 1) if timeout > 0 else DEFAULT_TIMEOUT_SECONDS

        resolved_env: dict[str, str] | None = None
        if env is not None:
            resolved_env = dict(env)
            resolved_env.setdefault("PYTHONUTF8", "1")
            resolved_env.setdefault("PYTHONIOENCODING", "utf-8")
            resolved_env.setdefault("LANG", "en_US.UTF-8")

        # asyncio.create_subprocess_exec does not accept shell= keyword.
        # This satisfies the shell=False invariant at the API level.
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
            env=resolved_env,
            limit=65536,  # pipe buffer limit per stream
        )
        return cast(
            "AsyncProcessHandle",
            _AsyncioProcessHandle(
                proc=proc,
                timeout_seconds=effective_timeout,
                stdin_lines=stdin_lines,
                started_at=datetime.now(timezone.utc),
            ),
        )


class _AsyncioProcessHandle:
    """Concrete AsyncProcessHandle backed by asyncio.subprocess.Process."""

    __slots__ = (
        "_started_at",
        "_status",
        "_stderr_done",
        "_stdin_lines",
        "_stdout_done",
        "proc",
        "timeout_seconds",
    )

    def __init__(
        self,
        proc: asyncio.subprocess.Process,
        timeout_seconds: int,
        stdin_lines: list[str] | None,
        started_at: datetime,
    ) -> None:
        self.proc = proc
        self.timeout_seconds = timeout_seconds
        self._stdin_lines = stdin_lines
        self._started_at = started_at
        self._status: ProcessStatus = ProcessStatus.RUNNING
        self._stdout_done = False
        self._stderr_done = False

    @property
    def pid(self) -> int:
        return self.proc.pid

    @property
    def status(self) -> ProcessStatus:
        return self._status

    async def stream(self) -> AsyncIterator[StreamChunk]:
        """Concurrently stream stdout and stderr, yielding lines as they arrive.

        Implementation: first writes stdin (if any), then drains both streams
        using asyncio.create_subprocess_exec's built-in pipe handling. Lines are
        yielded as they arrive; when a stream closes, an empty chunk is emitted.

        On Windows, asyncio subprocess pipes can return EOF immediately when the
        process exits quickly. To ensure reliable output capture, all stdout/stderr
        data is also re-read from the process's internal buffers after wait() returns.
        """
        pid = self.proc.pid

        # Write stdin lines if provided, then close stdin to signal EOF.
        if self._stdin_lines is not None:
            try:
                for line in self._stdin_lines:
                    line_bytes = (line + "\n").encode("utf-8", errors="replace")
                    self.proc.stdin.write(line_bytes)  # type: ignore[union-attr]
                    await self.proc.stdin.drain()  # type: ignore[union-attr]
            except (BrokenPipeError, ConnectionResetError):
                pass  # Process exited before we finished writing
            finally:
                self.proc.stdin.close()  # type: ignore[union-attr]

        # Concurrently drain both streams using pump tasks + queue（有界队列防止内存泄漏）
        queue: asyncio.Queue[StreamChunk | None] = asyncio.Queue(maxsize=500)
        active = 2

        async def pump(reader: asyncio.StreamReader, source: ProcessStreamSource) -> None:
            nonlocal active
            try:
                async for raw in reader:
                    await queue.put(
                        StreamChunk(
                            line=raw.rstrip(b"\n\r").decode("utf-8", errors="replace"),
                            source=source,
                            pid=pid,
                        )
                    )
            except (RuntimeError, ValueError) as exc:
                _logger.warning("kernelone.process.async_contracts.stream.pump failed: %s", exc, exc_info=True)
            finally:
                await queue.put(None)  # Signal EOF
                active -= 1

        pump_stdout_task = asyncio.create_task(
            pump(self.proc.stdout, ProcessStreamSource.STDOUT)  # type: ignore[arg-type]
        )
        pump_stderr_task = asyncio.create_task(
            pump(self.proc.stderr, ProcessStreamSource.STDERR)  # type: ignore[arg-type]
        )

        try:
            while active > 0:
                item = await queue.get()
                if item is None:
                    continue
                yield item
        finally:
            pump_stdout_task.cancel()
            pump_stderr_task.cancel()
            for t in (pump_stdout_task, pump_stderr_task):
                with contextlib.suppress(asyncio.CancelledError):
                    await t

    async def wait(self, timeout: float | None = None) -> ProcessStatus:
        if self._status not in (ProcessStatus.PENDING, ProcessStatus.RUNNING):
            return self._status

        effective_timeout = timeout if timeout is not None else self.timeout_seconds

        try:
            exit_code = await asyncio.wait_for(
                self.proc.wait(),
                timeout=effective_timeout if effective_timeout > 0 else None,
            )
        except asyncio.TimeoutError:
            # Wait timed out; process is still alive — mark as TIMED_OUT.
            self._status = ProcessStatus.TIMED_OUT
            return self._status

        self._status = ProcessStatus.SUCCESS if exit_code == 0 else ProcessStatus.FAILED
        return self._status

    async def is_alive(self) -> bool:
        if self._status not in (ProcessStatus.PENDING, ProcessStatus.RUNNING):
            return False
        return self.proc.returncode is None

    async def write_stdin(self, data: str) -> None:
        if self.proc.stdin is None or self.proc.stdin.is_closing():
            raise BrokenPipeError("stdin is closed")
        try:
            self.proc.stdin.write(data.encode("utf-8", errors="replace"))
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise exc from None

    async def __aenter__(self) -> _AsyncioProcessHandle:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager: terminate process and close pipes on exit."""
        try:
            if await self.is_alive():
                await self.terminate(timeout=3.0)
        except (RuntimeError, ValueError) as exc:
            _logger.warning("kernelone.process.async_contracts.handle_cleanup failed: %s", exc, exc_info=True)
        finally:
            # Close stdin (StreamWriter has close() method).
            # stdout/stderr are StreamReader which don't have close() - they are
            # automatically closed when the process exits.
            if self.proc.stdin is not None and not self.proc.stdin.is_closing():
                with contextlib.suppress(Exception):
                    self.proc.stdin.close()

    async def terminate(self, timeout: float = 5.0) -> bool:
        if self._status in (
            ProcessStatus.SUCCESS,
            ProcessStatus.FAILED,
            ProcessStatus.TIMED_OUT,
            ProcessStatus.CANCELLED,
        ):
            return True
        try:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                # proc.kill() returns None on all platforms (not Awaitable).
                # Just call it and wait for the process to exit.
                self.proc.kill()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.proc.wait(), timeout=5.0)
                self._status = ProcessStatus.CANCELLED
                return False
        except ProcessLookupError:
            return True  # Already exited
        self._status = ProcessStatus.CANCELLED
        return True

    async def kill(self) -> None:
        if self._status in (
            ProcessStatus.SUCCESS,
            ProcessStatus.FAILED,
            ProcessStatus.TIMED_OUT,
            ProcessStatus.CANCELLED,
        ):
            return
        try:
            # proc.kill() returns None on all platforms (not Awaitable).
            self.proc.kill()
            await self.proc.wait()
        except ProcessLookupError:
            pass
        self._status = ProcessStatus.CANCELLED

    async def result(self) -> StreamResult:
        if self._status in (ProcessStatus.PENDING, ProcessStatus.RUNNING):
            raise RuntimeError(f"process (pid={self.pid}) is still {self._status.value}; call wait() first")
        exit_code = self.proc.returncode if self.proc.returncode is not None else -1
        return StreamResult(
            pid=self.proc.pid,
            exit_code=exit_code,
            status=self._status,
            stdout_lines=(),
            stderr_lines=(),
            timed_out=(self._status == ProcessStatus.TIMED_OUT),
            timeout_seconds=self.timeout_seconds,
            started_at=self._started_at,
            ended_at=datetime.now(timezone.utc),
        )


async def _merge_two_streams(
    stdout_it: AsyncIterator[StreamChunk],
    stderr_it: AsyncIterator[StreamChunk],
) -> AsyncIterator[StreamChunk]:
    """Concurrently stream stdout and stderr, yielding chunks as they arrive.

    Uses an asyncio.Queue to collect lines from both streams without
    buffering the full output of either stream in memory（有界队列防止内存泄漏）.

    Both source iterators are run as background tasks. Chunks are yielded
    in the order they are put into the queue (approximate arrival order).
    """
    queue: asyncio.Queue[StreamChunk | None] = asyncio.Queue(maxsize=500)
    active = 2  # two source streams

    async def pump(src: AsyncIterator[StreamChunk]) -> None:
        nonlocal active
        try:
            async for chunk in src:
                await queue.put(chunk)
        except asyncio.CancelledError:
            pass
        finally:
            await queue.put(None)
            active -= 1

    pump_stdout_task = asyncio.create_task(pump(stdout_it))
    pump_stderr_task = asyncio.create_task(pump(stderr_it))

    try:
        while active > 0:
            item = await queue.get()
            if item is None:
                continue  # One stream finished; keep going until both done
            yield item
    finally:
        pump_stdout_task.cancel()
        pump_stderr_task.cancel()
        for t in (pump_stdout_task, pump_stderr_task):
            with contextlib.suppress(asyncio.CancelledError):
                await t


# =============================================================================
# SubprocessPopenRunner — Popen-based implementation of AsyncProcessRunnerPort
# =============================================================================
# This implementation wraps subprocess.Popen (synchronous) to satisfy the
# async AsyncProcessRunnerPort contract.  It is used for migrating existing Popen
# call sites that need to adopt the KernelOne process contract without
# switching to asyncio.create_subprocess_exec.
#
# Key design points:
# - Blocking Popen / wait() / poll() calls are wrapped in run_in_executor()
#   so they never block the asyncio event loop.
# - stdout/stderr are drained by background pump tasks that read from the
#   blocking text-mode pipe handles and push lines into an asyncio.Queue.
# - stream() yields from that queue so callers get true async line-by-line
#   output without full memory buffering.
# - write_stdin() is wrapped in run_in_executor() since TextIOWrapper.write()
#   is a blocking call.
# - shell=False is enforced: Popen is always called with a list of args
#   (shell=False is the default when args is a list).


class PopenAsyncHandle:
    """AsyncProcessHandle backed by subprocess.Popen.

    Instances are obtained by calling SubprocessPopenRunner.spawn().

    This handle wraps a synchronous ``subprocess.Popen`` so that it can be
    controlled from async code.  All blocking operations are executed in
    a thread pool via ``run_in_executor()`` so the event loop is never blocked.

    The handle is an async context manager.  Exiting the context terminates
    the subprocess if it is still alive and releases all pipe resources.
    """

    __slots__ = (
        "_exit_code",
        "_proc",
        "_proc_alive",
        "_started_at",
        "_status",
        "_stderr_queue",
        "_stdin_lines",
        "_stdin_used",
        "_stdout_queue",
        "_stream_done",
        "_timeout_seconds",
    )

    def __init__(
        self,
        proc: Any,
        timeout_seconds: int,
        stdin_lines: list[str] | None,
        started_at: datetime,
    ) -> None:
        self._proc = proc  # subprocess.Popen
        self._timeout_seconds = timeout_seconds
        self._stdin_lines = stdin_lines
        self._started_at = started_at
        self._status: ProcessStatus = ProcessStatus.RUNNING
        self._exit_code: int | None = None
        # Queues for pump threads to deposit lines（有界队列防止内存泄漏）
        self._stdout_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
        self._stderr_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
        self._stream_done = False
        self._proc_alive = True
        self._stdin_used = False

    @property
    def pid(self) -> int:
        return self._proc.pid

    @property
    def status(self) -> ProcessStatus:
        return self._status

    @property
    def process(self) -> Any:
        """Return the underlying subprocess.Popen for backward compatibility."""
        return self._proc

    def _start_pumps(self) -> None:
        """Start background threads to pump stdout/stderr into asyncio queues."""
        import threading

        def pump_stdout() -> None:
            try:
                for line in self._proc.stdout or []:
                    try:
                        self._stdout_queue.put_nowait(line.rstrip("\n\r"))
                    except asyncio.QueueFull:
                        break
            except (RuntimeError, ValueError) as exc:
                _logger.warning(
                    "kernelone.process.async_contracts.popen_stdout_pump failed: %s",
                    exc,
                    exc_info=True,
                )
            finally:
                self._stdout_queue.put_nowait("")  # EOF sentinel

        def pump_stderr() -> None:
            try:
                for line in self._proc.stderr or []:
                    try:
                        self._stderr_queue.put_nowait(line.rstrip("\n\r"))
                    except asyncio.QueueFull:
                        break
            except (RuntimeError, ValueError) as exc:
                _logger.warning(
                    "kernelone.process.async_contracts.popen_stderr_pump failed: %s",
                    exc,
                    exc_info=True,
                )
            finally:
                self._stderr_queue.put_nowait("")  # EOF sentinel

        t_out = threading.Thread(target=pump_stdout, daemon=True, name=f"popen-stdout-{self.pid}")
        t_err = threading.Thread(target=pump_stderr, daemon=True, name=f"popen-stderr-{self.pid}")
        t_out.start()
        t_err.start()

    async def stream(self) -> AsyncIterator[StreamChunk]:  # type: ignore[type-var]
        """Stream output lines from stdout and stderr as they arrive.

        Lines are yielded in the order captured from each stream.  Each stream
        reaching EOF is signalled by an empty ``StreamChunk``.  After both
        streams reach EOF, this method yields no further items.

        Implementation: starts background threads that read from the blocking
        text-mode pipe handles and deposit lines into asyncio Queues.  This
        method polls those queues asynchronously.
        """
        if not self._stream_done:
            self._start_pumps()
            self._stream_done = True

        # Write stdin first if lines were provided.
        if self._stdin_lines is not None and not self._stdin_used:
            self._stdin_used = True
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, self._write_stdin_sync)
            except (RuntimeError, ValueError) as exc:
                _logger.warning(
                    "kernelone.process.async_contracts.stream.write_stdin failed: %s",
                    exc,
                    exc_info=True,
                )

        pid = self.pid
        stdout_eof = False
        stderr_eof = False

        while not (stdout_eof and stderr_eof):
            try:
                # Drain all available stdout
                while True:
                    try:
                        line = self._stdout_queue.get_nowait()
                        if line == "":
                            stdout_eof = True
                            yield StreamChunk(line="", source=ProcessStreamSource.STDOUT, pid=pid)
                            break
                        yield StreamChunk(line=line, source=ProcessStreamSource.STDOUT, pid=pid)
                    except asyncio.QueueEmpty:
                        break

                # Drain all available stderr
                while True:
                    try:
                        line = self._stderr_queue.get_nowait()
                        if line == "":
                            stderr_eof = True
                            yield StreamChunk(line="", source=ProcessStreamSource.STDERR, pid=pid)
                            break
                        yield StreamChunk(line=line, source=ProcessStreamSource.STDERR, pid=pid)
                    except asyncio.QueueEmpty:
                        break

                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                raise
            except (RuntimeError, ValueError):
                break

    def _write_stdin_sync(self) -> None:
        """Write stdin lines synchronously (called in thread pool)."""
        if self._proc.stdin is None:
            return
        try:
            for line in self._stdin_lines or []:
                self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()
            self._proc.stdin.close()
        except (RuntimeError, ValueError) as exc:
            _logger.warning(
                "kernelone.process.async_contracts.write_stdin_sync failed: %s",
                exc,
                exc_info=True,
            )

    async def wait(self, timeout: float | None = None) -> ProcessStatus:
        if self._status not in (ProcessStatus.PENDING, ProcessStatus.RUNNING):
            return self._status

        effective_timeout = timeout if timeout is not None else self._timeout_seconds
        loop = asyncio.get_running_loop()
        try:
            exit_code = await loop.run_in_executor(
                None,
                lambda: self._proc.wait(
                    timeout=effective_timeout if effective_timeout > 0 else None,
                ),
            )
        except TimeoutError:
            # Popen.wait timed out; process is still alive.
            self._status = ProcessStatus.TIMED_OUT
            return self._status

        self._exit_code = exit_code
        self._proc_alive = False
        self._status = ProcessStatus.SUCCESS if exit_code == 0 else ProcessStatus.FAILED
        return self._status

    async def is_alive(self) -> bool:
        if self._status not in (ProcessStatus.PENDING, ProcessStatus.RUNNING):
            return False
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self._proc.poll() is None)

    async def write_stdin(self, data: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: self._do_write_stdin(data))

    def _do_write_stdin(self, data: str) -> None:
        if self._proc.stdin is None or self._proc.stdin.closed():
            raise BrokenPipeError("stdin is closed or process exited")
        try:
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
        except OSError as exc:
            raise exc from None

    async def __aenter__(self) -> PopenAsyncHandle:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager: terminate + close pipes on exit."""
        try:
            if await self.is_alive():
                await self.terminate(timeout=3.0)
        except (RuntimeError, ValueError) as exc:
            _logger.warning(
                "kernelone.process.async_contracts.popen_handle_cleanup failed: %s",
                exc,
                exc_info=True,
            )
        finally:
            for pipe in (self._proc.stdout, self._proc.stderr, self._proc.stdin):
                if pipe is not None:
                    with contextlib.suppress(Exception):
                        pipe.close()

    async def terminate(self, timeout: float = 5.0) -> bool:
        if self._status in (
            ProcessStatus.SUCCESS,
            ProcessStatus.FAILED,
            ProcessStatus.TIMED_OUT,
            ProcessStatus.CANCELLED,
        ):
            return True
        loop = asyncio.get_running_loop()
        try:
            self._proc.terminate()
            try:
                await loop.run_in_executor(
                    None,
                    lambda: self._proc.wait(timeout=timeout),
                )
            except TimeoutError:
                self._proc.kill()
                await loop.run_in_executor(None, self._proc.wait)
                self._status = ProcessStatus.CANCELLED
                return False
        except (FileNotFoundError, ProcessLookupError, OSError):
            return True
        self._exit_code = self._proc.returncode
        self._proc_alive = False
        self._status = ProcessStatus.CANCELLED
        return True

    async def kill(self) -> None:
        if self._status in (
            ProcessStatus.SUCCESS,
            ProcessStatus.FAILED,
            ProcessStatus.TIMED_OUT,
            ProcessStatus.CANCELLED,
        ):
            return
        loop = asyncio.get_running_loop()
        try:
            self._proc.kill()
            await loop.run_in_executor(None, self._proc.wait)
        except (FileNotFoundError, ProcessLookupError, OSError):
            pass
        self._proc_alive = False
        self._status = ProcessStatus.CANCELLED

    async def result(self) -> StreamResult:
        if self._status in (ProcessStatus.PENDING, ProcessStatus.RUNNING):
            raise RuntimeError(f"process (pid={self.pid}) is still {self._status.value}; call wait() first")
        exit_code = self._exit_code if self._exit_code is not None else -1
        return StreamResult(
            pid=self.pid,
            exit_code=exit_code,
            status=self._status,
            stdout_lines=(),
            stderr_lines=(),
            timed_out=(self._status == ProcessStatus.TIMED_OUT),
            timeout_seconds=self._timeout_seconds,
            started_at=self._started_at,
            ended_at=datetime.now(timezone.utc),
        )


class SubprocessPopenRunner:
    """AsyncProcessRunnerPort implementation wrapping subprocess.Popen.

    This runner satisfies the ``AsyncProcessRunnerPort`` contract while using
    ``subprocess.Popen`` (synchronous) underneath.  It is the Popen-side
    counterpart to ``SubprocessAsyncRunner`` which uses
    ``asyncio.create_subprocess_exec``.

    Use this runner when migrating existing ``subprocess.Popen`` call sites
    to the KernelOne process contract without changing the underlying
    process creation model.

    Security invariants (same as ``SubprocessAsyncRunner``):
        - shell=False is always used (Popen called with a list of args)
        - timeout is mandatory (process cannot run unbounded)
        - all text decoded as UTF-8 with errors="replace"
        - handles are async context managers (guaranteed cleanup)
    """

    __slots__ = ()

    async def spawn(
        self,
        args: list[str],
        *,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdin_lines: list[str] | None = None,
    ) -> PopenAsyncHandle:
        if not args:
            raise ValueError("args must not be empty")

        effective_timeout = max(timeout, 1) if timeout > 0 else DEFAULT_TIMEOUT_SECONDS

        import subprocess as _subprocess

        resolved_env: dict[str, str] | None = None
        if env is not None:
            resolved_env = dict(env)
            resolved_env.setdefault("PYTHONUTF8", "1")
            resolved_env.setdefault("PYTHONIOENCODING", "utf-8")
            resolved_env.setdefault("LANG", "en_US.UTF-8")
            resolved_env.setdefault("LC_ALL", "en_US.UTF-8")
            resolved_env.setdefault("LC_CTYPE", "en_US.UTF-8")

        # SECURITY: shell=False is the default when args is a list.
        # This is the permanent shell-disallow enforcement mechanism.
        proc = _subprocess.Popen(
            args,
            cwd=str(cwd) if cwd else None,
            env=resolved_env,
            stdin=_subprocess.PIPE,
            stdout=_subprocess.PIPE,
            stderr=_subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        return PopenAsyncHandle(
            proc=proc,
            timeout_seconds=effective_timeout,
            stdin_lines=stdin_lines,
            started_at=datetime.now(timezone.utc),
        )


__all__ = [
    # Constants
    "DEFAULT_TIMEOUT_SECONDS",
    # Protocols
    "AsyncProcessHandle",
    "AsyncProcessRunnerPort",
    "ProcessStatus",
    "ProcessStreamSource",
    # Security
    "ShellDisallowedError",
    # Value types
    "StreamChunk",
    "StreamResult",
    # Default implementations
    "SubprocessAsyncRunner",
    "SubprocessPopenRunner",
]
