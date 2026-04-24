from __future__ import annotations

import contextlib
import os
import subprocess
import threading
import time
from typing import TYPE_CHECKING

from polaris.kernelone.fs.encoding import build_utf8_env

from .codex_command_utils import _normalize_command

if TYPE_CHECKING:
    import queue
    from collections.abc import Callable

# Try to import PTY support for real-time terminal output
try:
    import pty  # noqa: F401

    HAS_PTY = True
except ImportError:
    HAS_PTY = False

try:
    import winpty

    HAS_WINPTY = True
except ImportError:
    HAS_WINPTY = False


def _run_cli(
    command: str,
    args: list[str],
    cwd: str,
    env: dict[str, str] | None,
    timeout: int,
    input_text: str | None,
) -> tuple[int, str, str, int]:
    """Execute CLI command (no timeout by default)"""
    cmd = _normalize_command(command) + args
    start = time.time()
    # 默认超时300秒（5分钟），防止进程无限挂起
    effective_timeout = timeout if timeout and timeout > 0 else 300
    result = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        cwd=cwd or None,
        env=build_utf8_env(env),
        timeout=effective_timeout,
    )
    latency_ms = int((time.time() - start) * 1000)
    return result.returncode, result.stdout or "", result.stderr or "", latency_ms


def _run_cli_streaming(
    command: str,
    args: list[str],
    cwd: str,
    env: dict[str, str] | None,
    input_text: str | None,
    stdout_callback: Callable[[str], None] | None = None,
    stderr_callback: Callable[[str], None] | None = None,
) -> tuple[int, str, str, int]:
    """Execute CLI command with streaming output"""
    cmd = _normalize_command(command) + args
    start = time.time()

    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if input_text else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd or None,
        env=build_utf8_env(env),
        bufsize=1,
    )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def read_stream(stream, lines_list, callback) -> None:
        for line in iter(stream.readline, ""):
            if not line:
                break
            lines_list.append(line)
            if callback:
                callback(line.rstrip("\n"))
        stream.close()

    stdout_thread = threading.Thread(target=read_stream, args=(process.stdout, stdout_lines, stdout_callback))
    stderr_thread = threading.Thread(target=read_stream, args=(process.stderr, stderr_lines, stderr_callback))

    stdout_thread.start()
    stderr_thread.start()

    if input_text and process.stdin:
        process.stdin.write(input_text)
        process.stdin.close()

    process.wait()
    stdout_thread.join()
    stderr_thread.join()

    latency_ms = int((time.time() - start) * 1000)
    return (process.returncode, "".join(stdout_lines), "".join(stderr_lines), latency_ms)


def _run_cli_pty(
    command: str,
    args: list[str],
    cwd: str,
    env: dict[str, str] | None,
    input_text: str | None,
    output_queue: queue.Queue,
) -> tuple[int, str, int]:
    """Execute CLI command using PTY for real terminal behavior (Windows/Linux/Mac)

    This provides true real-time output by using a pseudo-terminal.
    Output is pushed to the queue as it arrives.
    """
    import platform

    system = platform.system().lower()
    cmd = _normalize_command(command) + args

    if system == "windows" and HAS_WINPTY:
        return _run_winpty(cmd, cwd, env, input_text, output_queue)
    elif system != "windows" and HAS_PTY:
        return _run_unix_pty(cmd, cwd, env, input_text, output_queue)
    else:
        return _run_cli_pty_fallback(cmd, cwd, env, input_text, output_queue)


def _run_winpty(
    cmd: list[str],
    cwd: str,
    env: dict[str, str] | None,
    input_text: str | None,
    output_queue: queue.Queue,
) -> tuple[int, str, int]:
    """Run command using winpty on Windows"""
    start = time.time()
    try:
        process = winpty.PtyProcess.spawn(
            cmd,
            cwd=cwd or None,
            env=build_utf8_env(env),
        )

        # Send input if provided
        if input_text:
            process.write(input_text)

        output_chunks: list[str] = []

        while True:
            try:
                data = process.read(timeout=100)
                if data:
                    output_chunks.append(data)
                    output_queue.put(("stdout", data))
            except winpty.WinptyTimeout:
                pass

            if not process.isalive():
                try:
                    while True:
                        data = process.read(timeout=50)
                        if not data:
                            break
                        output_chunks.append(data)
                        output_queue.put(("stdout", data))
                except OSError:
                    break
                break

        latency_ms = int((time.time() - start) * 1000)
        return process.getexitcode(), "".join(output_chunks), latency_ms
    except (RuntimeError, ValueError) as e:
        output_queue.put(("stderr", str(e)))
        return 1, "", 0


def _run_unix_pty(
    cmd: list[str],
    cwd: str,
    env: dict[str, str] | None,
    input_text: str | None,
    output_queue: queue.Queue,
) -> tuple[int, str, int]:
    """Run command using pty on Unix/Linux/Mac"""
    import pty  # type: ignore[import]
    import select

    start = time.time()
    master_fd, slave_fd = pty.openpty()  # type: ignore[attr-defined, union-attr]

    try:
        process = subprocess.Popen(
            cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd or None,
            env=build_utf8_env(env),
            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
        )

        os.close(slave_fd)

        # Send input if provided
        if input_text:
            os.write(master_fd, input_text.encode("utf-8"))

        output_chunks: list[str] = []

        while True:
            try:
                ready, _, _ = select.select([master_fd], [], [], 0.1)
                if master_fd in ready:
                    try:
                        data = os.read(master_fd, 1024).decode("utf-8", errors="replace")
                        if data:
                            output_chunks.append(data)
                            output_queue.put(("stdout", data))
                    except OSError:
                        break
            except OSError:
                break

            if process.poll() is not None:
                try:
                    while True:
                        ready, _, _ = select.select([master_fd], [], [], 0.1)
                        if master_fd in ready:
                            data = os.read(master_fd, 1024).decode("utf-8", errors="replace")
                            if not data:
                                break
                            output_chunks.append(data)
                            output_queue.put(("stdout", data))
                        else:
                            break
                except OSError:
                    break
                break

        latency_ms = int((time.time() - start) * 1000)
        return process.returncode, "".join(output_chunks), latency_ms

    finally:
        with contextlib.suppress(OSError):
            os.close(master_fd)


def _run_cli_pty_fallback(
    cmd: list[str],
    cwd: str,
    env: dict[str, str] | None,
    input_text: str | None,
    output_queue: queue.Queue,
) -> tuple[int, str, int]:
    """Fallback: Use subprocess with immediate unbuffered output"""
    start = time.time()

    process = subprocess.Popen(
        cmd,
        shell=False,
        stdin=subprocess.PIPE if input_text else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd or None,
        env=build_utf8_env(env),
        bufsize=0,
    )

    output_chunks: list[str] = []

    def read_output() -> None:
        stdout = process.stdout
        assert stdout is not None
        while True:
            chunk = stdout.read(1)
            if not chunk:
                break
            output_chunks.append(chunk)
            output_queue.put(("stdout", chunk))

    reader_thread = threading.Thread(target=read_output)
    reader_thread.daemon = True
    reader_thread.start()

    if input_text and process.stdin:
        process.stdin.write(input_text)
        process.stdin.close()

    process.wait()
    reader_thread.join(timeout=1)

    latency_ms = int((time.time() - start) * 1000)
    return process.returncode, "".join(output_chunks), latency_ms
