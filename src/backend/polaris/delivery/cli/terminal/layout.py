"""Terminal layout, formatting, prompt rendering, spinner, and banner."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polaris.delivery.cli.terminal._base import (
    _SPINNER_FRAMES,
    _format_time,
    _get_polaris_version,
    _normalize_prompt_style,
    _safe_text,
    _truncate_workspace,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ConsoleRenderState:
    prompt_style: str = "plain"
    json_render: str = "raw"
    output_format: str = "text"
    omp_config: str | None = None
    omp_executable: str = "oh-my-posh"


@dataclass(slots=True)
class _TurnExecutionResult:
    role: str
    session_id: str
    final_content: str = ""
    saw_error: bool = False


class _PromptRenderer:
    def __init__(self, state: _ConsoleRenderState) -> None:
        self._state = state
        self._omp_available = True

    def reset(self) -> None:
        self._omp_available = True

    def render(self, *, role: str, session_id: str, workspace: Path) -> str:
        # Use simple arrow prompt when TTY (role shown in input box border)
        if sys.stdout.isatty():
            return "\x1b[36m›\x1b[0m "
        plain_prompt = f"{role}> "
        if _normalize_prompt_style(self._state.prompt_style) != "omp":
            return plain_prompt
        if not self._omp_available:
            return plain_prompt

        rendered = self._render_omp_segment(
            segment="primary",
            role=role,
            session_id=session_id,
            workspace=workspace,
        )
        if rendered:
            return rendered
        self._omp_available = False
        return plain_prompt

    def render_spinner_label(self, *, role: str, session_id: str, workspace: Path) -> str:
        base_label = "LLM request in progress"
        if _normalize_prompt_style(self._state.prompt_style) != "omp":
            return base_label
        if not self._omp_available:
            return base_label

        rendered = self._render_omp_segment(
            segment="secondary",
            role=role,
            session_id=session_id,
            workspace=workspace,
        )
        if not rendered:
            return base_label
        return f"{rendered}{base_label}"

    def _render_omp_segment(
        self,
        *,
        segment: str,
        role: str,
        session_id: str,
        workspace: Path,
    ) -> str | None:
        executable = _safe_text(self._state.omp_executable) or "oh-my-posh"
        config_path = _safe_text(self._state.omp_config)
        common_args = [executable]
        command_variants: list[list[str]] = [
            [*common_args, "print", segment, "--shell", "pwsh"],
            [*common_args, "print", segment],
            [*common_args, "prompt", "print", segment, "--shell", "pwsh"],
            [*common_args, "prompt", "print", segment],
        ]
        if config_path:
            command_variants = [[*variant, "--config", config_path] for variant in command_variants]

        env = os.environ.copy()
        env["KERNELONE_ROLE"] = role
        env["KERNELONE_SESSION_ID"] = session_id
        env["KERNELONE_WORKSPACE"] = str(workspace)
        env["POSH_SHELL"] = "pwsh"

        for command in command_variants:
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    check=False,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    timeout=1.5,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue

            if completed.returncode != 0:
                continue
            token = str(completed.stdout or "").rstrip("\r\n")
            if token:
                return token + " "
        return None


def _spinner_output_stream() -> Any:
    return getattr(sys, "stderr", None)


def _spinner_enabled_for_stream(stream: Any) -> bool:
    toggle = _safe_text(os.environ.get("KERNELONE_CLI_SPINNER")).lower()
    if toggle in {"0", "false", "off", "no"}:
        return False
    if toggle in {"1", "true", "on", "yes"}:
        return True
    return bool(stream is not None and hasattr(stream, "isatty") and stream.isatty())


class _TurnSpinner:
    """Lightweight CLI spinner shown while waiting for first stream event."""

    def __init__(
        self,
        *,
        enabled: bool,
        stream: Any,
        label: str = "LLM request in progress",
        interval_seconds: float = 0.08,
    ) -> None:
        self._enabled = bool(enabled)
        self._stream = stream
        self._label = str(label or "LLM request in progress")
        self._interval_seconds = float(interval_seconds)
        self._task: asyncio.Task[None] | None = None
        self._active = False
        self._line_width = 0

    def start(self) -> None:
        """Start the spinner if not already running."""
        if not self._enabled or self._task is not None:
            return
        self._active = True
        self._task = asyncio.create_task(self._spin())

    def restart(self) -> None:
        """Restart the spinner (cancel old task, clear line, then start new)."""
        if not self._enabled:
            return
        # Cancel any stale task before dropping the reference to avoid
        # dangling tasks that asyncio warns about during loop shutdown.
        if self._task is not None and not self._task.done():
            self._task.cancel()
        # Synchronously clear the current spinner line if running
        if self._line_width:
            clear_line = " " * self._line_width
            print(f"\r{clear_line}\r", end="", flush=True, file=self._stream)
        self._task = None
        self._active = True
        self._task = asyncio.create_task(self._spin())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._active = False
        try:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        finally:
            self._task = None

    async def _spin(self) -> None:
        frame_index = 0
        try:
            while self._active:
                frame = _SPINNER_FRAMES[frame_index]
                line = f"[{frame}] {self._label}"
                self._line_width = max(self._line_width, len(line))
                print(f"\r{line}", end="", flush=True, file=self._stream)
                frame_index = (frame_index + 1) % len(_SPINNER_FRAMES)
                await asyncio.sleep(self._interval_seconds)
        finally:
            if self._line_width:
                clear_line = " " * self._line_width
                print(f"\r{clear_line}\r", end="", flush=True, file=self._stream)


def _create_turn_spinner(*, label: str = "LLM request in progress") -> _TurnSpinner:
    stream = _spinner_output_stream()
    return _TurnSpinner(
        enabled=_spinner_enabled_for_stream(stream),
        stream=stream,
        label=label,
    )


def _build_render_state(
    *,
    prompt_style: str | None,
    omp_config: str | None,
    json_render: str | None,
    output_format: str | None,
) -> _ConsoleRenderState:
    env_prompt_style = os.environ.get("KERNELONE_CLI_PROMPT_STYLE")
    env_json_render = os.environ.get("KERNELONE_CLI_JSON_RENDER")
    env_omp_config = os.environ.get("KERNELONE_CLI_OMP_CONFIG")
    env_omp_executable = os.environ.get("KERNELONE_CLI_OMP_BIN")
    env_output_format = os.environ.get("KERNELONE_CLI_OUTPUT_FORMAT")
    from polaris.delivery.cli.terminal._base import (
        _normalize_json_render,
        _normalize_prompt_style,
        _resolve_output_format,
    )
    resolved_format = _resolve_output_format(output_format or env_output_format)
    return _ConsoleRenderState(
        prompt_style=_normalize_prompt_style(prompt_style or env_prompt_style),
        json_render=_normalize_json_render(json_render or env_json_render),
        output_format=resolved_format,
        omp_config=_safe_text(omp_config or env_omp_config) or None,
        omp_executable=_safe_text(env_omp_executable) or "oh-my-posh",
    )


def _print_banner(
    *,
    workspace: Path,
    role: str,
    session_id: str,
    allowed_roles: frozenset[str],
    render_state: _ConsoleRenderState,
    session_created: float | None = None,
    message_count: int | None = None,
) -> None:
    # Check skip banner env var
    if os.environ.get("KERNELONE_CLI_SKIP_BANNER", "").lower() in {"1", "true", "yes", "on"}:
        return

    # Skip banner in JSON output mode
    if render_state.output_format in ("json", "json-pretty"):
        return

    version = _get_polaris_version()
    product = "Polaris CLI" if not version else f"Polaris CLI v{version}"
    session_time = _format_time(session_created)
    ws_display = _truncate_workspace(workspace)

    # Build role list with current role highlighted
    sorted_roles = sorted(allowed_roles)
    role_display = "  ".join(f"[{r}]" if r == role else r for r in sorted_roles)

    # Try Rich panel first
    try:
        if not sys.stdout.isatty():
            raise RuntimeError("not a TTY")

        from rich.console import Console
        from rich.panel import Panel
        from rich.text import Text

        console = Console()

        # Build content with styled lines
        lines = [
            Text.assemble(
                ("◈ ", "cyan bold"),
                (product, "cyan bold"),
                ("  ·  ", "dim"),
                ("interactive session", "dim"),
            ),
            Text(""),
            Text.assemble(("workspace  ", "cyan"), ws_display),
            Text.assemble(("role       ", "cyan"), role_display),
        ]
        if message_count is not None:
            lines.append(Text.assemble(("messages   ", "cyan"), f"{message_count} in session"))

        lines.append(Text(""))
        lines.append(Text("Type /help or press Tab to autocomplete", style="dim"))

        # Print as single panel with compact width
        console.print(
            Panel(
                Text("\n").join(lines),
                border_style="cyan",
                title=f" [dim]session={session_id[:18]}[/dim]",
                subtitle=f"[dim]{session_time}[/dim]" if session_time else None,
                width=70,
                padding=(1, 2),
            )
        )
        return
    except (RuntimeError, ValueError):
        logger.warning("Failed to render status box with rich")
    # Fallback: plain text box-drawing
    horiz = "═" * 62
    vert = "║"

    # Line 1: Header bar
    left_info = f"{product}  │  session={session_id[:20]}"
    right_info = session_time
    padding = 62 - len(left_info) - len(right_info) - 4  # -4 for spaces and ║
    if padding < 0:
        padding = 0
    print(f"╔{horiz}╗")
    print(f"{vert}  {left_info}{' ' * padding}{right_info}   {vert}")

    # Line 2: Divider
    print(f"╠{horiz}╣")

    # Line 3: Workspace
    print(f"{vert}  workspace:   {ws_display:<54} {vert}")

    # Line 4: Role list
    print(f"{vert}  role:       {role_display:<52} {vert}")

    # Line 5: Message count (if available)
    has_msg_count = message_count is not None
    if has_msg_count:
        msg_count_str = f"{message_count} in session"
        print(f"{vert}  messages:   {msg_count_str:<54} {vert}")

    # Line 6: Divider
    print(f"╠{horiz}╣")

    # Line 7: Render options
    render_line = f"render:     prompt:{render_state.prompt_style}  json:{render_state.json_render}  keymode:auto"
    print(f"{vert}  {render_line:<60} {vert}")

    # Line 8: Help hint
    print(f"{vert}  Type /help for commands.  Tab to autocomplete.          {vert}")

    # Line 9: Footer
    print(f"╚{horiz}╝")
