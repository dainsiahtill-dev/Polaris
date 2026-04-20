"""Safe command parsing/execution helpers for PM/QA runtime."""

from __future__ import annotations

import os
import shlex
import subprocess


def _strip_wrapping_quotes(token: str) -> str:
    text = str(token or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def parse_command_args(command: str) -> list[str]:
    """Parse command text into subprocess arg list without shell expansion."""
    raw = str(command or "").strip()
    if not raw:
        raise ValueError("empty command")

    try:
        tokens = shlex.split(raw, posix=(os.name != "nt"))
    except ValueError as exc:
        raise ValueError(f"invalid command syntax: {exc}") from exc

    if os.name == "nt":
        tokens = [_strip_wrapping_quotes(token) for token in tokens]

    normalized = [str(token).strip() for token in tokens if str(token).strip()]
    if not normalized:
        raise ValueError("empty command")
    return normalized


def run_command(
    command: str,
    *,
    cwd: str,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    """Run parsed command with `shell=False` and UTF-8 text mode."""
    argv = parse_command_args(command)
    return subprocess.run(
        argv,
        cwd=cwd,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
