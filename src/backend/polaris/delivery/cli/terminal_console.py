"""Unified terminal console host for Polaris CLI (role-switch capable)."""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Disable asyncio debug early to prevent log pollution
# Use environment variable approach for cross-version compatibility
os.environ.setdefault("PYTHONASYNCIODEBUG", "0")

from polaris.cells.roles.host.public import RoleHostKind, get_capability_profile
from polaris.delivery.cli.cli_completion import (
    load_history,
    readline_input,
    save_history,
)
from polaris.delivery.cli.cli_prompt import (
    create_prompt_session,
)
from polaris.delivery.cli.context_status import (
    ContextStats,
    render_context_panel,
)
from polaris.delivery.cli.super_mode import (
    SUPER_ROLE,
    SuperModeRouter,
    build_director_handoff_message,
)
from polaris.kernelone.fs.encoding import enforce_utf8

# Optional readline import for keyboard mode support
try:
    import readline
except ImportError:
    readline = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from polaris.delivery.cli.director.console_host import RoleConsoleHost

# RoleConsoleHost is lazy-imported inside run_role_console() to avoid circular import
# (director/__init__.py re-exports from this module).

logger = logging.getLogger(__name__)

_ALLOWED_BACKENDS: frozenset[str] = frozenset({"auto", "plain"})
_EXIT_COMMANDS: frozenset[str] = frozenset({"/exit", "/quit", ":q"})
_HELP_COMMANDS: frozenset[str] = frozenset({"/help", "/?"})
_JSON_RENDER_MODES: frozenset[str] = frozenset({"raw", "pretty", "pretty-color"})
_OUTPUT_FORMAT_MODES: frozenset[str] = frozenset({"text", "json", "json-pretty", "json-stream"})
_PROMPT_STYLES: frozenset[str] = frozenset({"plain", "omp"})
_SPINNER_FRAMES: tuple[str, ...] = ("|", "/", "-", "\\")
_KEYMODE_FILE = Path.home() / ".polaris_cli_keymode"

# Pattern to filter empty tool call blocks that LLM sometimes outputs as text content
_EMPTY_TOOL_BLOCK_RE = re.compile(r"^\s*(<tool>\s*</tool>|</tool>)\s*$", re.IGNORECASE)
_KEYMODE_VALUES: frozenset[str] = frozenset({"auto", "vi", "emacs"})

# Role prompt symbols for visual enhancement (V3)
ROLE_PROMPT_SYMBOLS: dict[str, str] = {
    "director": "◉",
    "pm": "◆",
    "architect": "◇",
    "chief_engineer": "◈",
    "qa": "◎",
    "super": "✦",
}


def _get_role_symbol(role: str) -> str:
    """Get unicode symbol for a role prompt."""
    return ROLE_PROMPT_SYMBOLS.get(role.lower(), "▸")


# Tool name color styles for execution highlighting (V4/V5)
TOOL_NAME_STYLES: dict[str, str] = {
    "read_file": "blue",
    "write_file": "green",
    "edit_file": "yellow",
    "execute": "red bold",
    "bash": "red bold",
    "search": "cyan",
    "ripgrep": "cyan",
    "grep": "cyan",
    "glob": "magenta",
    "list_directory": "dim",
    "read": "blue",
    "write": "green",
    "str_replace_editor": "yellow",
}


def _get_tool_style(tool_name: str) -> str:
    """Get color style for a tool name."""
    return TOOL_NAME_STYLES.get(tool_name, "cyan")


# Sentinel for unset output format (CLI case: --output-format not provided)
_UNSET = object()

# ---------------------------------------------------------------------------
# Log suppression helpers (keep Banner noise-free)
# ---------------------------------------------------------------------------

_INFRASTRUCTURE_LOGGERS: tuple[str, ...] = (
    "polaris.infrastructure.llm.provider_bootstrap",
    "polaris.cells.roles.session.internal.session_persistence",
    "polaris.cells.roles.session.internal.role_session_service",
)


def _suppress_infrastructure_logs() -> dict[str, int]:
    """Temporarily suppress INFO/DEBUG logs from polaris infrastructure loggers.

    Returns a dict mapping logger names to their previous levels so that
    _restore_infrastructure_logs can restore them.
    """
    import logging

    previous_levels: dict[str, int] = {}
    for name in _INFRASTRUCTURE_LOGGERS:
        logger = logging.getLogger(name)
        previous_levels[name] = logger.level
        logger.setLevel(logging.WARNING)
    return previous_levels


def _restore_infrastructure_logs(previous_levels: dict[str, int]) -> None:
    """Restore loggers to their previous levels after banner display."""
    import logging

    for name, level in previous_levels.items():
        logger = logging.getLogger(name)
        logger.setLevel(level)


# ---------------------------------------------------------------------------
# Onboarding guide for first-time CLI users
# ---------------------------------------------------------------------------

_ONBOARD_MARKER_PATH = os.path.expanduser("~/.polaris_cli_onboarded")

_ONBOARD_WELCOME_PANEL_RICH = """[bold cyan]Interactive Commands:[/bold cyan]
  /role <name>    Switch role (pm/architect/director/qa)
  /json <mode>    Set JSON output (raw/pretty/pretty-color)
  /prompt <style>  Set prompt style (plain/omp)
  /session        Show current session info
  /new <title>    Start a new session
  /exit           Exit console

[bold cyan]Tips:[/bold cyan]
  • Tab to complete commands
  • Up/Down arrows for command history
  • Launch with --super for intent-based multi-role orchestration
  • --json=pretty-color for colored diff output
  • --debug for verbose LLM stream"""

_ONBOARD_WELCOME_PANEL_PLAIN = """
Welcome to Polaris CLI
======================

Interactive Commands:
  /role <name>    Switch role (pm/architect/director/qa)
  /json <mode>    Set JSON output (raw/pretty/pretty-color)
  /prompt <style>  Set prompt style (plain/omp)
  /session        Show current session info
  /new <title>    Start a new session
  /exit           Exit console

Tips:
  • Tab to complete commands
  • Up/Down arrows for command history
  • Launch with --super for intent-based multi-role orchestration
  • --json=pretty-color for colored diff output
  • --debug for verbose LLM stream

Press Enter to continue..."""


def _show_onboarding() -> None:
    """Show onboarding welcome panel for first-time CLI users.

    Idempotent: marks onboarding complete immediately after shown.
    Skipped entirely if stdin is not a TTY (non-interactive mode).
    """
    if not sys.stdin.isatty():
        return
    if os.path.exists(_ONBOARD_MARKER_PATH):
        return

    # Try Rich panel first
    try:
        from rich.console import Console
        from rich.panel import Panel

        console = Console()
        console.print(
            Panel(
                _ONBOARD_WELCOME_PANEL_RICH,
                title="[bold]Welcome to Polaris CLI[/bold]",
                border_style="cyan",
            )
        )
    except (RuntimeError, ValueError):
        print(_ONBOARD_WELCOME_PANEL_PLAIN)

    # Wait for Enter (skip in non-interactive mode)
    with contextlib.suppress(EOFError, KeyboardInterrupt):
        input("Press Enter to continue...")

    # Mark onboarding complete (idempotent)
    try:
        with open(_ONBOARD_MARKER_PATH, "w", encoding="utf-8") as f:
            f.write("")
    except OSError:
        pass


# Model pricing per million tokens (prompt, completion)
_MODEL_PRICING_PER_M: dict[str, tuple[float, float]] = {
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-3-opus": (15.0, 75.0),
    "gpt-4o": (2.50, 10.0),
}

# Known LLM models for tab completion and validation
_KNOWN_MODELS: tuple[str, ...] = (
    "claude-3-5-sonnet",
    "claude-3-5-haiku",
    "claude-3-opus",
    "claude-sonnet-4",
    "claude-3-haiku-20250514",
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "ollama/llama3",
    "ollama/codellama",
    "kimi-for-coding",
)


def _get_current_model() -> str | None:
    """Get the currently configured LLM model from environment.

    SSOT: Model info must come from ContextOS via event payload.
    Environment variable is only a fallback, not the primary source.
    Returns None if not set - caller must handle appropriately.
    """
    return os.environ.get("POLARIS_PM_MODEL")


def _set_current_model(model: str) -> None:
    """Set the LLM model for subsequent requests via environment variable."""
    os.environ["POLARIS_PM_MODEL"] = model


def _extract_token_usage(payload: Mapping[str, Any]) -> dict[str, int] | None:
    """Extract token usage from payload, checking multiple possible fields."""
    # Try direct usage field first
    usage = _as_mapping(payload.get("usage"))
    if usage:
        return {
            "prompt_tokens": int(usage.get("prompt_tokens", 0)),
            "completion_tokens": int(usage.get("completion_tokens", 0)),
            "total_tokens": int(usage.get("total_tokens", 0)),
        }

    # Try token_usage field
    token_usage = _as_mapping(payload.get("token_usage"))
    if token_usage:
        return {
            "prompt_tokens": int(token_usage.get("prompt_tokens", 0)),
            "completion_tokens": int(token_usage.get("completion_tokens", 0)),
            "total_tokens": int(token_usage.get("total_tokens", 0)),
        }

    # Try llm_usage field
    llm_usage = _as_mapping(payload.get("llm_usage"))
    if llm_usage:
        return {
            "prompt_tokens": int(llm_usage.get("prompt_tokens", 0)),
            "completion_tokens": int(llm_usage.get("completion_tokens", 0)),
            "total_tokens": int(llm_usage.get("total_tokens", 0)),
        }

    return None


def _estimate_cost(prompt_tokens: int, completion_tokens: int, model: str) -> str:
    """Calculate estimated cost based on token counts and model."""
    pricing = _MODEL_PRICING_PER_M.get(model.lower())
    if pricing is None:
        return "n/a"
    prompt_cost_per_m, completion_cost_per_m = pricing
    prompt_cost = (prompt_tokens / 1_000_000) * prompt_cost_per_m
    completion_cost = (completion_tokens / 1_000_000) * completion_cost_per_m
    total_cost = prompt_cost + completion_cost
    return f"~${total_cost:.4f}"


def _print_token_stats(payload: Mapping[str, Any], elapsed_seconds: float) -> None:
    """Print token statistics after complete event if token info is available.

    Shows cost/throughput if model and token usage are available.
    Shows context panel only if context_budget is available.
    """
    token_usage = _extract_token_usage(payload)

    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    has_token_usage = False
    if token_usage is not None:
        prompt_tokens = token_usage["prompt_tokens"]
        completion_tokens = token_usage["completion_tokens"]
        total_tokens = token_usage["total_tokens"]
        has_token_usage = True

    # Get model from payload - SSOT requires this from ContextOS, no hardcoded fallback
    model = _safe_text(payload.get("model"))
    if not model:
        # Model not in payload - skip stats display
        return

    # Resolve context limit from context_budget in event (ContextOS resolved)
    # SSOT: Context window MUST come from ContextOS via ModelCatalog resolution
    context_budget = payload.get("context_budget")
    if not isinstance(context_budget, Mapping):
        # context_budget not in payload - skip panel display
        return
    model_context_window = context_budget.get("model_context_window")
    if not isinstance(model_context_window, (int, float)) or model_context_window <= 0:
        # model_context_window invalid - skip panel display
        return
    context_limit = int(model_context_window)

    # Best practice: distinguish ContextOS estimate vs LLM actual
    # estimated_input_tokens: ContextOS budget estimation (before LLM call)
    # current_input_tokens: actual tokens from LLM response
    estimated_input_tokens = int(context_budget.get("current_input_tokens", 0))
    current_input_tokens = prompt_tokens  # LLM actual prompt_tokens

    # Calculate optional metrics for unified display
    cost_per_1k = None
    throughput = None
    if has_token_usage and elapsed_seconds > 0:
        cost_str = _estimate_cost(prompt_tokens, completion_tokens, model)
        if cost_str and cost_str != "n/a":
            with contextlib.suppress(ValueError):
                cost_per_1k = float(cost_str.replace("$", ""))
        throughput = total_tokens / elapsed_seconds

    # Create context stats for unified panel display
    stats = ContextStats(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        context_limit=context_limit,
        current_input_tokens=current_input_tokens,
        estimated_input_tokens=estimated_input_tokens,
        cost_per_1k=cost_per_1k,
        throughput=throughput,
    )

    # Try rich panel first
    panel_str = render_context_panel(stats, compact=True)
    if panel_str:
        # Use Rich Console to render markup (print() outputs raw markup text)
        try:
            from rich.console import Console

            rich_console = Console(force_terminal=True)
            rich_console.print(panel_str)
        except (RuntimeError, ValueError):
            print(panel_str)
        return

    # Fallback: plain text format
    print("[Token Stats]")
    print(f"  prompt tokens:     {prompt_tokens:>8,}")
    print(f"  completion tokens: {completion_tokens:>8,}")
    print(f"  total tokens:      {total_tokens:>8,}")
    if elapsed_seconds > 0:
        throughput = total_tokens / elapsed_seconds
        print(f"  throughput:       {throughput:.1f} tok/s")
    cost_str = _estimate_cost(prompt_tokens, completion_tokens, model)
    if cost_str:
        print(f"  estimated cost:    {cost_str}")


_HELP_TEXT = """Commands:
  /help              Show this help
  /session           Show current role + session id
  /new [title]       Start a new session (optional title)
  /role <role>       Switch active role (role-bound session + governance)
  /model [name]      Show or switch LLM model (no args = show current)
  /json [mode]       Show/switch tool JSON render mode (raw|pretty|pretty-color)
  /prompt [style]    Show/switch prompt style (plain|omp)
  /keymode [mode]    Show/switch keyboard mode (vi|emacs)
  /dryrun [on|off]   Toggle or query dry-run mode (no tool execution)
  /exit              Exit console

Launch option:
  --super            Enable intent-based SUPER mode (auto routes PM/Director/etc.)
"""


def _detect_keymode_from_shell() -> str:
    """Detect keymode from $SHELLOPTS environment variable."""
    try:
        opts = shlex.split(os.environ.get("SHELLOPTS", ""))
        return "vi" if "vi" in opts else "emacs"
    except (RuntimeError, ValueError):
        return "emacs"


def _get_default_keymode() -> str:
    """Resolve the default keymode: env var, persisted file, or auto-detection."""
    env_keymode = os.environ.get("POLARIS_CLI_KEYMODE", "auto").lower()
    if env_keymode in _KEYMODE_VALUES:
        return env_keymode
    # Try loading from persisted file
    try:
        if _KEYMODE_FILE.exists():
            saved = _KEYMODE_FILE.read_text("utf-8").strip().lower()
            if saved in _KEYMODE_VALUES:
                return saved
    except (RuntimeError, ValueError):
        logger.warning("Failed to load keymode from file: %s", _KEYMODE_FILE)
    # Fallback to auto-detection
    return _detect_keymode_from_shell()


def _resolve_keymode(token: str | None) -> str:
    """Normalize a keymode token, returning 'emacs' for invalid values."""
    if token is None:
        return "emacs"
    normalized = token.strip().lower()
    return normalized if normalized in _KEYMODE_VALUES else "emacs"


def _apply_keymode(keymode: str) -> bool:
    """Apply the specified keymode to readline. Returns True on success."""
    if readline is None:
        return False
    try:
        rl = readline
        if keymode == "vi":
            rl.parse_and_bind("set editing-mode vi")  # type: ignore[attr-defined]
            rl.parse_and_bind("vi-insert-mode-key")  # type: ignore[attr-defined]
            rl.parse_and_bind("vi-command-mode-key")  # type: ignore[attr-defined]
        else:
            rl.parse_and_bind("set editing-mode emacs")  # type: ignore[attr-defined]
        return True
    except (RuntimeError, ValueError):
        return False


def _save_keymode(keymode: str) -> None:
    """Persist the keymode preference to ~/.polaris_cli_keymode."""
    with contextlib.suppress(Exception):
        _KEYMODE_FILE.write_text(keymode, "utf-8")


def _safe_text(value: object | None) -> str:
    return str(value or "").strip()


def _coerce_bool(value: object | None) -> bool:
    if isinstance(value, bool):
        return value
    token = _safe_text(value).lower()
    return token in {"1", "true", "yes", "on", "debug"}


def _as_mapping(value: object | None) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _tool_name(payload: Mapping[str, Any]) -> str:
    name = _safe_text(payload.get("tool"))
    if name:
        return name
    result = _as_mapping(payload.get("result"))
    return _safe_text(result.get("tool")) or "tool"


def _tool_path(payload: Mapping[str, Any]) -> str:
    for source in (
        payload,
        _as_mapping(payload.get("args")),
        _as_mapping(payload.get("result")),
        _as_mapping(payload.get("raw_result")),
    ):
        for key in ("file_path", "file", "path", "filepath", "target_file"):
            token = _safe_text(source.get(key))
            if token:
                return token.replace("\\", "/")
    return ""


def _tool_status(payload: Mapping[str, Any]) -> str:
    for source in (
        payload,
        _as_mapping(payload.get("result")),
        _as_mapping(payload.get("raw_result")),
    ):
        success = source.get("success")
        if isinstance(success, bool):
            return "ok" if success else "failed"
        ok = source.get("ok")
        if isinstance(ok, bool):
            return "ok" if ok else "failed"
    error_text = _safe_text(payload.get("error"))
    return "failed" if error_text else "done"


def _tool_error(payload: Mapping[str, Any]) -> str:
    for source in (
        payload,
        _as_mapping(payload.get("result")),
        _as_mapping(payload.get("raw_result")),
    ):
        for key in ("error", "message"):
            token = _safe_text(source.get(key))
            if token:
                return token
    return _safe_text(payload.get("error"))


def _normalize_json_render(mode: str | None) -> str:
    token = _safe_text(mode).lower()
    return token if token in _JSON_RENDER_MODES else "raw"


def _normalize_prompt_style(style: str | None) -> str:
    token = _safe_text(style).lower()
    return token if token in _PROMPT_STYLES else "plain"


def _normalize_output_format(fmt: str | None) -> str:
    """Normalize output format, handling 'json-stream' alias."""
    token = _safe_text(fmt).lower()
    if token == "json-stream":
        return "json"
    return token if token in _OUTPUT_FORMAT_MODES else "text"


def _stdout_is_tty() -> bool:
    """Check if stdout is a TTY."""
    stream = getattr(sys, "stdout", None)
    if stream is None:
        return False
    try:
        return bool(stream.isatty())
    except (RuntimeError, ValueError):
        return False


def _resolve_output_format(explicit_format: str | None | object) -> str:
    """Resolve the effective output format.

    Rules:
    - If explicit_format is _UNSET (CLI case: --output-format not provided), apply TTY detection
    - If explicit_format is None (PolarisRoleConsole case or explicit None), use 'text'
    - If explicit_format is an actual string, normalize and use it
    """
    # CLI case: --output-format not provided, apply pipe-safe TTY detection
    if explicit_format is _UNSET:
        if not _stdout_is_tty():
            return "json"
        return "text"
    # explicit_format is None or an actual string
    if explicit_format is None:
        # PolarisRoleConsole case or explicit None: default to text
        return "text"
    # Actual format string
    explicit_format_token = explicit_format if isinstance(explicit_format, str) else None
    normalized = _normalize_output_format(explicit_format_token)
    if normalized != "text" or explicit_format_token == "text":
        return normalized
    return "text"


def _json_event_packet(event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": event_type,
        "data": dict(payload),
    }


def _json_event_text(packet: Mapping[str, Any], *, mode: str) -> str:
    if mode == "raw":
        return json.dumps(dict(packet), ensure_ascii=False)
    return json.dumps(dict(packet), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Structured JSON event output
# ---------------------------------------------------------------------------


def _build_structured_json_event(
    event_type: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a structured JSON event according to the schema."""
    timestamp = datetime.datetime.utcnow().isoformat() + "Z"
    result_map = _as_mapping(payload.get("result", {}))

    if event_type == "content_chunk":
        return {
            "type": "content_chunk",
            "content": str(payload.get("content") or ""),
            "timestamp": timestamp,
        }

    if event_type == "tool_call":
        return {
            "type": "tool_call",
            "tool": _safe_text(payload.get("tool")) or _tool_name(payload),
            "args": dict(payload.get("args", {})),
            "timestamp": timestamp,
        }

    if event_type == "tool_result":
        success_val = result_map.get("success")
        if success_val is None:
            success_val = result_map.get("ok")
        success = bool(success_val) if isinstance(success_val, bool) else None

        duration_ms: int | None = None
        duration = result_map.get("duration_ms") or result_map.get("duration")
        if duration is not None:
            with contextlib.suppress(ValueError, TypeError):
                duration_ms = int(duration)

        event: dict[str, Any] = {
            "type": "tool_result",
            "tool": _safe_text(payload.get("tool")) or _tool_name(payload),
            "success": success,
            "timestamp": timestamp,
        }
        if duration_ms is not None:
            event["duration_ms"] = duration_ms
        return event

    if event_type == "complete":
        tokens = payload.get("tokens")
        if isinstance(tokens, Mapping):
            token_data: dict[str, int] = {}
            prompt_tokens = tokens.get("prompt") or tokens.get("prompt_tokens")
            completion_tokens = tokens.get("completion") or tokens.get("completion_tokens")
            total_tokens = tokens.get("total") or tokens.get("total_tokens")
            if prompt_tokens is not None:
                with contextlib.suppress(ValueError, TypeError):
                    token_data["prompt"] = int(prompt_tokens)
            if completion_tokens is not None:
                with contextlib.suppress(ValueError, TypeError):
                    token_data["completion"] = int(completion_tokens)
            if total_tokens is not None:
                with contextlib.suppress(ValueError, TypeError):
                    token_data["total"] = int(total_tokens)
            elif "prompt" in token_data and "completion" in token_data:
                token_data["total"] = token_data["prompt"] + token_data["completion"]
        else:
            token_data = {}

        event = {
            "type": "complete",
            "content": str(payload.get("content") or ""),
            "timestamp": timestamp,
        }
        if token_data:
            event["tokens"] = token_data
        return event

    return {
        "type": event_type,
        "data": dict(payload),
        "timestamp": timestamp,
    }


def _print_structured_json_event(
    event: dict[str, Any],
    *,
    pretty: bool = False,
) -> None:
    """Print a structured JSON event to stdout (no ANSI codes)."""
    line = json.dumps(event, ensure_ascii=False, indent=2) if pretty else json.dumps(event, ensure_ascii=False)
    print(line, flush=True)


def _print_error_event(payload: Mapping[str, Any]) -> None:
    """Print an error event (used in JSON output mode)."""
    error_text = _tool_error(payload) or "unknown streaming error"
    event = {
        "type": "error",
        "error": error_text,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    }
    _print_structured_json_event(event)


# ---------------------------------------------------------------------------
# Diff detection and ANSI-colored rendering
# ---------------------------------------------------------------------------

_ANSI_GREEN = "\x1b[32m"
_ANSI_RED = "\x1b[31m"
_ANSI_RESET = "\x1b[0m"
_ANSI_BOLD = "\x1b[1m"
_ANSI_DIM = "\x1b[2m"
_ANSI_YELLOW = "\x1b[33m"
_ANSI_CYAN = "\x1b[36m"


def _extract_diff_text(payload: Mapping[str, Any]) -> str:
    """从 payload 中提取 diff/patch 字段。"""
    for source in (payload, _as_mapping(payload.get("result")), _as_mapping(payload.get("raw_result"))):
        for key in ("patch", "diff", "diff_patch", "workspace_diff", "unified_diff"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _has_diff_content(payload: Mapping[str, Any]) -> bool:
    """检查 payload 是否包含 diff 内容。"""
    return bool(_extract_diff_text(payload))


def _render_diff_ansi(diff_text: str, *, operation: str = "modify", max_lines: int = 200) -> str:
    """使用 ANSI 转义序列渲染带颜色的 diff（不依赖 Rich）。

    绿色行表示新增，红色表示删除，上下文行为默认色。
    """
    lines = diff_text.splitlines()
    visible = lines[:max_lines]
    has_unified = any(line.startswith("+++ ") or line.startswith("--- ") or line.startswith("@@") for line in visible)
    output: list[str] = []

    for line in visible:
        if has_unified:
            if line.startswith("+++ ") or line.startswith("--- "):
                output.append(f"{_ANSI_BOLD}{line}{_ANSI_RESET}")
            elif line.startswith("@@"):
                output.append(f"{_ANSI_DIM}{line}{_ANSI_RESET}")
            elif line.startswith("+") and not line.startswith("+++ "):
                output.append(f"{_ANSI_GREEN}{line}{_ANSI_RESET}")
            elif line.startswith("-") and not line.startswith("--- "):
                output.append(f"{_ANSI_RED}{line}{_ANSI_RESET}")
            else:
                output.append(line)
        else:
            if operation == "delete":
                output.append(f"{_ANSI_RED}{line}{_ANSI_RESET}")
            else:
                output.append(f"{_ANSI_GREEN}{line}{_ANSI_RESET}")

    remaining = len(lines) - len(visible)
    if remaining > 0:
        output.append(f"{_ANSI_DIM}... ({remaining} more lines){_ANSI_RESET}")
    return "\n".join(output)


def _print_json_with_rich(packet: Mapping[str, Any]) -> bool:
    try:
        from rich.console import Console
        from rich.syntax import Syntax
    except (RuntimeError, ValueError):
        return False

    rendered = json.dumps(dict(packet), ensure_ascii=False, indent=2)
    Console().print(Syntax(rendered, "json", theme="ansi_dark"))
    return True


# V4: Rich tool call/result highlighting
def _print_tool_call_rich(tool_name: str, args: dict[str, Any]) -> None:
    """Print a tool call with Rich colored output."""
    style = _get_tool_style(tool_name)
    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    try:
        from rich.console import Console
        from rich.text import Text

        console = Console()
        text = Text.assemble(
            ("▸ ", "cyan"),
            (tool_name, style),
            (f"({args_str})", "dim"),
        )
        console.print(text)
    except (RuntimeError, ValueError):
        print(f"▸ {tool_name}({args_str})")


def _print_tool_result_rich(
    tool_name: str,
    success: bool | None,
    duration_ms: int | None,
    error: str | None = None,
) -> None:
    """Print a tool result with Rich colored status indicator."""
    if success is True:
        status = "✓"
        status_style = "green"
    elif success is False:
        status = "✗"
        status_style = "red"
    else:
        status = "?"
        status_style = "yellow"

    duration = f" ({duration_ms}ms)" if duration_ms is not None else ""
    try:
        from rich.console import Console
        from rich.text import Text

        console = Console()
        text = Text.assemble(
            (f"{status} ", status_style),
            (tool_name, "cyan"),
            (duration, "dim"),
        )
        console.print(text)
        if error:
            error_text = Text.assemble(
                ("  └─ ", "dim"),
                (error[:200], "red"),
            )
            console.print(error_text)
    except (RuntimeError, ValueError):
        print(f"{status} {tool_name}{duration}")
        if error:
            print(f"  └─ {error[:200]}")


def _print_stream_json_event(*, event_type: str, payload: Mapping[str, Any], json_render: str) -> None:
    packet = _json_event_packet(event_type, payload)
    mode = _normalize_json_render(json_render)

    # V4: Handle tool_call with rich colored output
    if event_type == "tool_call" and mode == "pretty-color":
        tool_name = _safe_text(payload.get("tool")) or _tool_name(payload)
        args = _as_mapping(payload.get("args"))
        _print_tool_call_rich(tool_name, args)
        return

    # V4: Handle tool_result with rich colored status
    if event_type == "tool_result" and mode == "pretty-color":
        result_map = _as_mapping(payload.get("result", {}))
        tool_name = _safe_text(payload.get("tool")) or _tool_name(payload)
        success_val = result_map.get("success")
        if success_val is None:
            success_val = result_map.get("ok")
        success = bool(success_val) if isinstance(success_val, bool) else None
        duration_ms: int | None = None
        duration = result_map.get("duration_ms") or result_map.get("duration")
        if duration is not None:
            with contextlib.suppress(ValueError, TypeError):
                duration_ms = int(duration)
        error = _tool_error(payload)
        _print_tool_result_rich(tool_name, success, duration_ms, error)
        return

    # 检测是否为工具结果且包含 diff 内容
    if event_type in ("tool_result", "tool_call") and _has_diff_content(payload):
        diff_text = _extract_diff_text(payload)
        operation = _safe_text(_as_mapping(payload.get("result", {})).get("operation", "modify"))
        # 打印摘要行（工具名 + 状态）
        result_map = _as_mapping(payload.get("result", {}))
        success = result_map.get("success")
        status = "ok" if success is True else ("fail" if success is False else "?")
        tool_name = _safe_text(payload.get("tool")) or _safe_text(result_map.get("tool")) or event_type
        if mode == "pretty-color":
            # pretty-color: 打印彩色 diff
            print(f"[tool] {tool_name}  →  {status}")
            print(_render_diff_ansi(diff_text, operation=operation))
        elif mode == "pretty":
            # pretty: 打印 diff（无颜色）
            print(f"[tool] {tool_name}  →  {status}")
            print(diff_text)
        else:
            print(_json_event_text(packet, mode=mode))
        return

    if mode == "pretty-color":
        if _print_json_with_rich(packet):
            return
        mode = "pretty"
    print(_json_event_text(packet, mode=mode))


def _supports_dim_debug() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    stream = getattr(sys, "stdout", None)
    return bool(stream is not None and hasattr(stream, "isatty") and stream.isatty())


def _style_debug_line(text: str) -> str:
    if not _supports_dim_debug():
        return text
    return f"\x1b[90m\x1b[2m{text}\x1b[0m"


def _debug_body_text(payload: Mapping[str, Any], *, json_render: str) -> str:
    packet = (
        dict(payload.get("payload") or {}) if isinstance(payload.get("payload"), Mapping) else payload.get("payload")
    )
    if isinstance(packet, str):
        return packet
    mode = _normalize_json_render(json_render)
    if mode == "pretty-color":
        mode = "pretty"
    if mode == "raw":
        return json.dumps(packet, ensure_ascii=False)
    return json.dumps(packet, ensure_ascii=False, indent=2)


def _print_debug_event(payload: Mapping[str, Any], *, json_render: str) -> None:
    category = _safe_text(payload.get("category")) or "debug"
    label = _safe_text(payload.get("label")) or "event"
    source = _safe_text(payload.get("source"))
    tags = _as_mapping(payload.get("tags"))

    header = f"[debug][{category}][{label}]"
    if source:
        header += f"[source={source}]"
    if tags:
        tag_bits = " ".join(f"{key}={value}" for key, value in sorted(tags.items()))
        if tag_bits:
            header += f" {tag_bits}"
    print(_style_debug_line(header))

    body_text = _debug_body_text(payload, json_render=json_render)
    for line in str(body_text or "").splitlines():
        print(_style_debug_line(f"[debug] {line}"))


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
        env["POLARIS_ROLE"] = role
        env["POLARIS_SESSION_ID"] = session_id
        env["POLARIS_WORKSPACE"] = str(workspace)
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
    toggle = _safe_text(os.environ.get("POLARIS_CLI_SPINNER")).lower()
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
    env_prompt_style = os.environ.get("POLARIS_CLI_PROMPT_STYLE")
    env_json_render = os.environ.get("POLARIS_CLI_JSON_RENDER")
    env_omp_config = os.environ.get("POLARIS_CLI_OMP_CONFIG")
    env_omp_executable = os.environ.get("POLARIS_CLI_OMP_BIN")
    env_output_format = os.environ.get("POLARIS_CLI_OUTPUT_FORMAT")
    resolved_format = _resolve_output_format(output_format or env_output_format)
    return _ConsoleRenderState(
        prompt_style=_normalize_prompt_style(prompt_style or env_prompt_style),
        json_render=_normalize_json_render(json_render or env_json_render),
        output_format=resolved_format,
        omp_config=_safe_text(omp_config or env_omp_config) or None,
        omp_executable=_safe_text(env_omp_executable) or "oh-my-posh",
    )


async def _stream_turn(
    host: RoleConsoleHost,
    *,
    role: str,
    session_id: str,
    message: str,
    json_render: str,
    debug: bool,
    spinner_label: str,
    dry_run: bool = False,
    output_format: str = "text",
    enable_cognitive: bool | None = None,
) -> _TurnExecutionResult:
    # Determine if we're in structured JSON output mode
    use_json_output = output_format in ("json", "json-pretty")
    json_output_pretty = output_format == "json-pretty"

    spinner = _create_turn_spinner(label=spinner_label)
    if not use_json_output:
        spinner.start()
        # Show minimal context status indicator while waiting
        try:
            from rich.console import Console

            Console()
            status_line = "[dim]🤖 LLM request started...[/dim]"
            print(status_line, end="\r", flush=True)
        except (RuntimeError, ValueError):
            logger.warning("Failed to create rich console for spinner")
    turn_start_time = time.monotonic()
    first_token_time: float | None = None
    content_open = False
    thinking_open = False
    saw_content_chunk = False
    saw_thinking_chunk = False
    thinking_tail_newline = True
    # Spinner stops when user-visible content arrives or error occurs
    stop_spinner_event_types = {
        "content_chunk",
        "thinking_chunk",
        "complete",
        "error",
    }
    # Dry-run state
    dry_run_count = 0
    dry_run_done = False
    saw_error = False
    content_parts: list[str] = []
    final_content = ""

    def _dry_run_banner() -> None:
        print(f"{_ANSI_YELLOW}=== DRY-RUN MODE: No tools will be executed ==={_ANSI_RESET}")

    def _dry_run_tool_line(tool_name: str, args: dict[str, Any]) -> None:
        args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
        print(f"  {_ANSI_YELLOW}[DRY-RUN] Would execute: {tool_name}({args_str}){_ANSI_RESET}")
        print(f"  {_ANSI_YELLOW}[DRY-RUN] Skipping actual execution{_ANSI_RESET}")

    def _dry_run_summary(count: int) -> None:
        print(f"{_ANSI_YELLOW}Dry-run complete: {count} tool call(s) would be executed{_ANSI_RESET}")

    def _show_ttft_if_first_token() -> None:
        nonlocal first_token_time
        if first_token_time is not None:
            return
        first_token_time = time.monotonic()
        ttft_ms = (first_token_time - turn_start_time) * 1000
        # Brief TTFT display that gets overwritten by first content
        print(f"\r[dim]TTFT: {ttft_ms:.0f}ms[/dim]  ", end="", flush=True)

    def _close_content_stream() -> None:
        nonlocal content_open
        if content_open:
            print()
            content_open = False

    def _open_thinking_stream() -> None:
        nonlocal thinking_open, thinking_tail_newline
        if thinking_open:
            return
        _close_content_stream()
        print("<thinking>")
        thinking_open = True
        thinking_tail_newline = True

    def _write_thinking_chunk(chunk: str) -> None:
        nonlocal saw_thinking_chunk, thinking_tail_newline
        if not chunk:
            return
        _open_thinking_stream()
        print(chunk, end="", flush=True)
        saw_thinking_chunk = True
        thinking_tail_newline = chunk.endswith("\n")

    def _close_thinking_stream() -> None:
        nonlocal thinking_open, thinking_tail_newline
        if not thinking_open:
            return
        if not thinking_tail_newline:
            print()
        print("</thinking>")
        thinking_open = False
        thinking_tail_newline = True

    try:
        logger.debug("stream_turn started: session_id=%s role=%s", session_id, role)
        async for event in host.stream_turn(
            session_id,
            message,
            context={
                "role": role,
                "host_kind": _safe_text(getattr(host.config, "host_kind", RoleHostKind.CLI.value))
                or RoleHostKind.CLI.value,
            },
            role=role,
            debug=debug,
            enable_cognitive=enable_cognitive,
        ):
            event_type = _safe_text(event.get("type"))
            logger.debug("stream_turn event: type=%s", event_type)
            if event_type in stop_spinner_event_types:
                await spinner.stop()

            # In dry-run mode, stop after all tool calls are processed
            if dry_run and dry_run_done:
                break

            payload = _as_mapping(event.get("data"))

            if event_type == "content_chunk":
                _show_ttft_if_first_token()
                chunk = str(payload.get("content") or "")
                if chunk and not _EMPTY_TOOL_BLOCK_RE.match(chunk):
                    content_parts.append(chunk)
                    if use_json_output:
                        _print_structured_json_event(
                            _build_structured_json_event("content_chunk", {"content": chunk}),
                            pretty=json_output_pretty,
                        )
                    else:
                        _close_thinking_stream()
                        print(chunk, end="", flush=True)
                    content_open = True
                    saw_content_chunk = True
                continue

            if event_type == "thinking_chunk":
                _show_ttft_if_first_token()
                chunk = str(payload.get("content") or "")
                _write_thinking_chunk(chunk)
                continue

            if event_type == "tool_call":
                if use_json_output:
                    tool_name = _safe_text(payload.get("tool"))
                    tool_args = _as_mapping(payload.get("args"))
                    _print_structured_json_event(
                        _build_structured_json_event(
                            "tool_call",
                            {"tool": tool_name, "args": tool_args},
                        ),
                        pretty=json_output_pretty,
                    )
                else:
                    _close_thinking_stream()
                    _close_content_stream()
                    _print_stream_json_event(
                        event_type="tool_call",
                        payload=payload,
                        json_render=json_render,
                    )
                if dry_run:
                    tool_name = _safe_text(payload.get("tool"))
                    tool_args = _as_mapping(payload.get("args"))
                    _dry_run_tool_line(tool_name, tool_args)
                    dry_run_count += 1
                continue

            if event_type == "tool_result":
                if use_json_output:
                    tool_name = _safe_text(payload.get("tool"))
                    duration_ms = payload.get("duration_ms")
                    error_msg = _tool_error(payload)
                    success = error_msg is None
                    event_payload = {
                        "tool": tool_name,
                        "success": success,
                    }
                    if duration_ms is not None:
                        event_payload["duration_ms"] = duration_ms
                    _print_structured_json_event(
                        _build_structured_json_event("tool_result", event_payload),
                        pretty=json_output_pretty,
                    )
                else:
                    _close_thinking_stream()
                    _close_content_stream()
                    _print_stream_json_event(
                        event_type="tool_result",
                        payload=payload,
                        json_render=json_render,
                    )
                # In dry-run mode, tool_result signals end of tool call sequence
                if dry_run:
                    dry_run_done = True
                # Restart spinner to show LLM is still processing after tool execution
                if not use_json_output:
                    spinner.restart()
                continue

            if event_type == "debug":
                _close_thinking_stream()
                _close_content_stream()
                _print_debug_event(payload, json_render=json_render)
                continue

            if event_type == "error":
                saw_error = True
                if use_json_output:
                    error_text = _tool_error(payload) or "unknown streaming error"
                    _print_error_event({"error": error_text})
                else:
                    _close_thinking_stream()
                    _close_content_stream()
                    error_text = _tool_error(payload) or "unknown streaming error"
                    print(f"[error] {error_text}", file=sys.stderr)
                continue

            if event_type == "complete":
                thinking = str(payload.get("thinking") or "")
                if thinking and not saw_thinking_chunk:
                    _write_thinking_chunk(thinking)
                _close_thinking_stream()
                content = str(payload.get("content") or "")
                if content:
                    final_content = content
                if content and not saw_content_chunk:
                    if use_json_output:
                        _print_structured_json_event(
                            _build_structured_json_event("content_chunk", {"content": content}),
                            pretty=json_output_pretty,
                        )
                    else:
                        print(content, end="", flush=True)
                    content_open = True
                    saw_content_chunk = True
                if not use_json_output and content_open:
                    print()
                    content_open = False
                if not dry_run:
                    elapsed = time.monotonic() - turn_start_time
                    if use_json_output:
                        token_usage = _extract_token_usage(payload)
                        if token_usage:
                            tokens_dict = {
                                "prompt": token_usage["prompt_tokens"],
                                "completion": token_usage["completion_tokens"],
                                "total": token_usage["total_tokens"],
                            }
                        else:
                            tokens_dict = {}
                        # Include context_budget for context window display if available
                        context_budget = payload.get("context_budget")
                        complete_payload: dict[str, Any] = {"tokens": tokens_dict}
                        if isinstance(context_budget, Mapping):
                            complete_payload["context_budget"] = dict(context_budget)
                        if not saw_content_chunk:
                            complete_payload["content"] = content
                        _print_structured_json_event(
                            _build_structured_json_event("complete", complete_payload),
                            pretty=json_output_pretty,
                        )
                    else:
                        _print_token_stats(payload, elapsed)
                # In dry-run mode, complete signals end
                if dry_run:
                    dry_run_done = True

        _close_thinking_stream()
        if content_open:
            print()
        logger.debug("stream_turn event loop exhausted normally")
    finally:
        # Gracefully stop spinner even if the event loop is shutting down.
        # Suppress CancelledError: during asyncio.run() teardown, pending tasks
        # (including the spinner) are cancelled; awaiting them would re-raise.
        if not use_json_output:
            with contextlib.suppress(asyncio.CancelledError):
                await spinner.stop()

    if dry_run:
        _dry_run_summary(dry_run_count)
    if not final_content and content_parts:
        final_content = "".join(content_parts)
    return _TurnExecutionResult(
        role=role,
        session_id=session_id,
        final_content=final_content,
        saw_error=saw_error,
    )


def _run_streaming_turn(
    host: RoleConsoleHost,
    *,
    role: str,
    session_id: str,
    message: str,
    json_render: str,
    debug: bool,
    spinner_label: str,
    dry_run: bool = False,
    output_format: str = "text",
    enable_cognitive: bool | None = None,
) -> _TurnExecutionResult:
    try:
        return asyncio.run(
            _stream_turn(
                host,
                role=role,
                session_id=session_id,
                message=message,
                json_render=json_render,
                debug=debug,
                spinner_label=spinner_label,
                dry_run=dry_run,
                output_format=output_format,
                enable_cognitive=enable_cognitive,
            )
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        # User interrupted or task cancelled - graceful shutdown
        return _TurnExecutionResult(role=role, session_id=session_id, saw_error=True)
    except Exception as exc:
        # Surface unexpected errors so users know *why* the turn aborted.
        logger.exception("Streaming turn aborted unexpectedly: %s", exc)
        raise


def _normalize_role(role: str | None) -> str:
    token = _safe_text(role).lower()
    return token or "director"


def _get_polaris_version() -> str:
    """Try to get polaris version, return empty string if unavailable."""
    try:
        import polaris

        return getattr(polaris, "__version__", "") or ""
    except (RuntimeError, ValueError):
        return ""


def _truncate_workspace(workspace: Path, max_len: int = 50) -> str:
    """Truncate workspace path if > max_len chars, showing ... prefix."""
    ws_str = str(workspace)
    if len(ws_str) <= max_len:
        return ws_str
    return f"...{ws_str[-(max_len - 3) :]}"


def _format_time(ts: float | None) -> str:
    """Format Unix timestamp to HH:MM:SS, return empty string if None."""
    if ts is None:
        return ""
    from datetime import datetime

    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _print_status_bar(role: str, keymode: str = "auto") -> None:
    """Print a compact status bar below the banner with role symbol and hints."""
    symbol = ROLE_PROMPT_SYMBOLS.get(role.lower(), "▸")
    hints = "tab: complete  ·  ↑↓: history  ·  /help"
    try:
        from rich.console import Console
        from rich.text import Text

        console = Console()
        status = Text.assemble(
            (f" {symbol} ", "cyan bold"),
            (f"[{role}]", "green"),
            ("  ·  ", "dim"),
            (hints, "dim"),
        )
        console.print(status)
    except (RuntimeError, ValueError):
        print(f"{symbol} [{role}]  {hints}")


_POLARIS_ART = """
[cyan bold]◈ Polaris CLI[/cyan bold] [dim]·[/dim] [white]Polaris[/white]
"""


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
    if os.environ.get("POLARIS_CLI_SKIP_BANNER", "").lower() in {"1", "true", "yes", "on"}:
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


def _resolve_role_session(
    host: RoleConsoleHost,
    *,
    role: str,
    role_sessions: dict[str, str],
    host_kind: str,
    session_id: str | None = None,
    session_title: str | None = None,
) -> str:
    capability_profile = _build_role_capability_profile(role=role, host_kind=host_kind)
    explicit_session_id = _safe_text(session_id) or None
    context_config = {
        "role": role,
        "host_kind": host_kind,
        "governance_scope": f"role:{role}",
    }
    if explicit_session_id:
        session_payload = host.ensure_session(
            session_id=explicit_session_id,
            title=_safe_text(session_title) or None,
            context_config=context_config,
            capability_profile=capability_profile,
        )
    else:
        session_payload = host.create_session(
            title=_safe_text(session_title) or None,
            context_config=context_config,
            capability_profile=capability_profile,
        )
    resolved = _safe_text(session_payload.get("id"))
    if not resolved:
        raise RuntimeError(f"failed to resolve role session id for role={role}")
    role_sessions[role] = resolved
    return resolved


def _build_role_capability_profile(*, role: str, host_kind: str) -> dict[str, Any]:
    profile = get_capability_profile(host_kind).to_dict()
    metadata = dict(profile.get("metadata") or {})
    metadata.update(
        {
            "role": role,
            "governance_scope": f"role:{role}",
            "source": "polaris.delivery.cli.terminal_console",
        }
    )
    profile["metadata"] = metadata
    profile["role"] = role
    return profile


def _console_display_role(*, role: str, super_mode: bool) -> str:
    return SUPER_ROLE if super_mode else role


def _run_super_turn(
    host: RoleConsoleHost,
    *,
    fallback_role: str,
    role_sessions: dict[str, str],
    host_kind: str,
    session_title: str | None,
    workspace_path: Path,
    render_state: _ConsoleRenderState,
    prompt_renderer: _PromptRenderer,
    message: str,
    json_render: str,
    debug: bool,
    dry_run: bool,
    output_format: str,
    enable_cognitive: bool | None = None,
) -> _TurnExecutionResult:
    decision = SuperModeRouter().decide(message, fallback_role=fallback_role)
    logger.debug(
        "super_mode decision: fallback_role=%s reason=%s roles=%s",
        fallback_role,
        decision.reason,
        ",".join(decision.roles),
    )
    handoff_message = message
    last_result: _TurnExecutionResult | None = None
    for index, next_role in enumerate(decision.roles):
        next_session_id = role_sessions.get(next_role) or _resolve_role_session(
            host,
            role=next_role,
            role_sessions=role_sessions,
            host_kind=host_kind,
            session_title=session_title,
        )
        if index > 0 and next_role == "director":
            if last_result is None or last_result.saw_error or not last_result.final_content.strip():
                break
            handoff_message = build_director_handoff_message(
                original_request=message,
                pm_output=last_result.final_content,
            )
        last_result = _run_streaming_turn(
            host,
            role=next_role,
            session_id=next_session_id,
            message=handoff_message,
            json_render=json_render,
            debug=debug,
            spinner_label=prompt_renderer.render_spinner_label(
                role=next_role,
                session_id=next_session_id,
                workspace=workspace_path,
            ),
            dry_run=dry_run,
            output_format=output_format,
            enable_cognitive=enable_cognitive,
        )
    if last_result is None:
        active_session_id = role_sessions.get(fallback_role) or _resolve_role_session(
            host,
            role=fallback_role,
            role_sessions=role_sessions,
            host_kind=host_kind,
            session_title=session_title,
        )
        return _TurnExecutionResult(role=fallback_role, session_id=active_session_id, saw_error=True)
    return last_result


class PolarisRoleConsole:
    """Compatibility wrapper object for app-style console invocation."""

    def __init__(
        self,
        *,
        workspace: str | Path,
        role: str = "director",
        backend: str = "auto",
        session_id: str | None = None,
        session_title: str | None = None,
        prompt_style: str = "plain",
        omp_config: str | None = None,
        json_render: str = "raw",
        debug: bool = False,
        batch: bool = False,
        model: str | None = None,
        dry_run: bool = False,
        output_format: str | None = "text",
        super_mode: bool = False,
    ) -> None:
        self.workspace = str(Path(workspace).resolve())
        self.role = _normalize_role(role)
        self.backend = _safe_text(backend) or "auto"
        self.session_id = _safe_text(session_id) or None
        self.session_title = _safe_text(session_title) or None
        self.prompt_style = _normalize_prompt_style(prompt_style)
        self.omp_config = _safe_text(omp_config) or None
        self.json_render = _normalize_json_render(json_render)
        self.debug = bool(debug)
        self.batch = bool(batch)
        self.model = model
        self.dry_run = bool(dry_run)
        self.output_format = output_format
        self.super_mode = bool(super_mode)

    def run(self) -> int:
        return run_role_console(
            workspace=self.workspace,
            role=self.role,
            backend=self.backend,
            session_id=self.session_id,
            session_title=self.session_title,
            prompt_style=self.prompt_style,
            omp_config=self.omp_config,
            json_render=self.json_render,
            debug=self.debug,
            batch=self.batch,
            model=self.model,
            dry_run=self.dry_run,
            output_format=self.output_format,
            super_mode=self.super_mode,
        )


class PolarisLazyClaude(PolarisRoleConsole):
    """Legacy class name kept for backward compatibility."""


def _run_batch_mode(
    host: RoleConsoleHost,
    *,
    role: str,
    session_id: str,
    message: str,
    json_render: str,
    debug: bool,
    output_format: str,
    enable_cognitive: bool | None = None,
) -> int:
    """Run a single turn in batch mode: read stdin, stream output, exit on complete."""
    import signal

    exit_code = 0

    def _sigint_handler(signum: int, frame: Any) -> None:
        nonlocal exit_code
        exit_code = 130

    old_handler = signal.signal(signal.SIGINT, _sigint_handler)

    try:
        _run_streaming_turn(
            host,
            role=role,
            session_id=session_id,
            message=message,
            json_render=json_render,
            debug=debug,
            spinner_label="",
            output_format=output_format,
            enable_cognitive=enable_cognitive,
        )
    except KeyboardInterrupt:
        exit_code = 130
    except (RuntimeError, ValueError):
        exit_code = 1
    finally:
        signal.signal(signal.SIGINT, old_handler)

    return exit_code


def _trigger_slm_warmup() -> None:
    """Fire-and-forget background SLM warmup so the model is resident by first user message."""

    def _warmup() -> None:
        logger.debug("[SLM warmup] 后台线程启动 (daemon=%s)", threading.current_thread().daemon)
        try:
            from polaris.cells.roles.kernel.internal.transaction.cognitive_gateway import (
                CognitiveGateway,
            )

            async def _init_and_wait() -> None:
                logger.debug("[SLM warmup] 正在初始化 CognitiveGateway...")
                gateway = await CognitiveGateway.default()
                logger.debug("[SLM warmup] CognitiveGateway 初始化完成")
                # 关键：必须显式等待后台 warmup task 完成，否则 asyncio.run()
                # 在 default() 返回后就会关闭事件循环，cancel 所有 pending task。
                if gateway._warmup_task is not None and not gateway._warmup_task.done():
                    logger.debug("[SLM warmup] 等待后台 _silent_warmup task 完成 (timeout=15s)...")
                    with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                        await asyncio.wait_for(gateway._warmup_task, timeout=15.0)
                    logger.debug("[SLM warmup] 后台 _silent_warmup task 已结束")
                else:
                    logger.debug("[SLM warmup] 无待处理的 warmup task (可能已跳过)")

            logger.debug("[SLM warmup] 启动事件循环...")
            asyncio.run(_init_and_wait())
            logger.debug("[SLM warmup] 事件循环已关闭，SLM 预热流程结束")
        except Exception as exc:  # noqa: BLE001
            logger.debug("[SLM warmup] 预热线程异常 (静默忽略): %s", exc, exc_info=True)

    threading.Thread(target=_warmup, daemon=True, name="slm-warmup").start()
    logger.debug("[SLM warmup] 已提交 daemon 线程 '%s'", "slm-warmup")


def run_role_console(
    *,
    workspace: str | Path = ".",
    role: str = "director",
    backend: str = "auto",
    session_id: str | None = None,
    session_title: str | None = None,
    prompt_style: str | None = None,
    omp_config: str | None = None,
    json_render: str | None = None,
    debug: bool | None = None,
    batch: bool = False,
    model: str | None = None,
    dry_run: bool = False,
    output_format: str | None = None,
    enable_cognitive: bool | None = None,
    super_mode: bool = False,
) -> int:
    # Enforce UTF-8 encoding for Chinese characters and other Unicode output
    enforce_utf8()
    # Apply initial model from CLI flag if provided
    if model:
        _set_current_model(model)
    workspace_path = Path(workspace).resolve()
    role_token = _normalize_role(role)
    backend_token = _safe_text(backend).lower() or "auto"
    if backend_token not in _ALLOWED_BACKENDS:
        print(
            f"[console] backend={backend_token!r} is deprecated; using plain terminal output.",
            file=sys.stderr,
        )
    render_state = _build_render_state(
        prompt_style=prompt_style,
        omp_config=omp_config,
        json_render=json_render,
        output_format=output_format,
    )
    debug_enabled = _coerce_bool(debug if debug is not None else os.environ.get("POLARIS_CLI_DEBUG"))
    if debug_enabled:
        # --debug 标志降低日志级别并确保 handler 输出 DEBUG
        polaris_logger = logging.getLogger("polaris")
        polaris_logger.setLevel(logging.DEBUG)
        logger.setLevel(logging.DEBUG)
        # 避免重复添加 handler：检查层级中是否已有 StreamHandler
        _has_stream = False
        _check: logging.Logger | None = polaris_logger
        while _check is not None:
            for h in _check.handlers:
                if isinstance(h, logging.StreamHandler):
                    _has_stream = True
                    h.setLevel(logging.DEBUG)
            _check.setLevel(logging.DEBUG)
            if not _check.propagate:
                break
            _check = _check.parent
        if not _has_stream:
            _handler = logging.StreamHandler(sys.stderr)
            _handler.setLevel(logging.DEBUG)
            _handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
            polaris_logger.addHandler(_handler)
    prompt_renderer = _PromptRenderer(render_state)

    from polaris.delivery.cli.director.console_host import RoleConsoleHost

    host = RoleConsoleHost(str(workspace_path), role=role_token)
    host_kind = _safe_text(getattr(host.config, "host_kind", RoleHostKind.CLI.value)) or RoleHostKind.CLI.value
    allowed_roles = frozenset(
        str(item).strip().lower() for item in getattr(host, "_ALLOWED_ROLES", ()) if str(item).strip()
    ) or frozenset({"director", "pm", "architect", "chief_engineer", "qa"})
    role_sessions: dict[str, str] = {}
    active_session_id = _resolve_role_session(
        host,
        role=role_token,
        role_sessions=role_sessions,
        host_kind=host_kind,
        session_id=session_id,
        session_title=session_title,
    )

    # Suppress infrastructure logs and Instructor warning around banner display
    import warnings

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*Instructor.*")
        warnings.filterwarnings("ignore", message=".*instructor.*")
        previous_log_levels = _suppress_infrastructure_logs()
        _print_banner(
            workspace=workspace_path,
            role=_console_display_role(role=role_token, super_mode=super_mode),
            session_id=active_session_id,
            allowed_roles=allowed_roles | (frozenset({SUPER_ROLE}) if super_mode else frozenset()),
            render_state=render_state,
        )
        _restore_infrastructure_logs(previous_log_levels)

    _show_onboarding()
    _trigger_slm_warmup()

    # Initialize keyboard mode
    current_keymode = _get_default_keymode()
    _apply_keymode(current_keymode)

    # Load command history
    load_history()

    # Batch mode: read stdin and run single turn, then exit
    if batch:
        batch_message = sys.stdin.read().strip()
        if not batch_message:
            return 0
        if super_mode:
            result = _run_super_turn(
                host,
                fallback_role=role_token,
                role_sessions=role_sessions,
                host_kind=host_kind,
                session_title=session_title,
                workspace_path=workspace_path,
                render_state=render_state,
                prompt_renderer=prompt_renderer,
                message=batch_message,
                json_render=render_state.json_render,
                debug=debug_enabled,
                dry_run=dry_run,
                output_format=render_state.output_format,
                enable_cognitive=enable_cognitive,
            )
            return 1 if result.saw_error else 0
        return _run_batch_mode(
            host,
            role=role_token,
            session_id=active_session_id,
            message=batch_message,
            json_render=render_state.json_render,
            debug=debug_enabled,
            output_format=render_state.output_format,
            enable_cognitive=enable_cognitive,
        )

    current_role = role_token
    current_dry_run = dry_run

    # Create prompt session for TTY mode (prompt-toolkit with integrated status)
    prompt_session = None
    if sys.stdout.isatty():
        prompt_session = create_prompt_session(
            role=_console_display_role(role=current_role, super_mode=super_mode),
            session_id=active_session_id,
            workspace=str(workspace_path),
            omp_config=render_state.omp_config,
            omp_executable=render_state.omp_executable,
        )

    while True:
        # Update session role if changed
        if prompt_session is not None:
            prompt_session.set_role(_console_display_role(role=current_role, super_mode=super_mode))

        try:
            if prompt_session is not None:
                # Use prompt-toolkit session with bottom toolbar
                raw = prompt_session.prompt()
            else:
                # Non-TTY or fallback: use readline_input
                raw = readline_input(
                    prompt_renderer.render(
                        role=_console_display_role(role=current_role, super_mode=super_mode),
                        session_id=active_session_id,
                        workspace=workspace_path,
                    ),
                    role=current_role,
                    session_id=active_session_id,
                )
        except EOFError:
            print()
            save_history()
            return 0
        except KeyboardInterrupt:
            print()
            save_history()
            return 130

        message = _safe_text(raw)
        if not message:
            continue
        if message in _EXIT_COMMANDS:
            save_history()
            return 0
        if message in _HELP_COMMANDS:
            print(_HELP_TEXT)
            continue
        if message == "/session":
            if super_mode:
                print(f"role={SUPER_ROLE} fallback_role={current_role} session={active_session_id}")
            else:
                print(f"role={current_role} session={active_session_id}")
            continue
        if message.startswith("/new"):
            title = _safe_text(message.removeprefix("/new")) or None
            session_payload = host.create_session(
                title=title,
                context_config={
                    "role": current_role,
                    "host_kind": host_kind,
                    "governance_scope": f"role:{current_role}",
                },
                capability_profile=_build_role_capability_profile(
                    role=current_role,
                    host_kind=host_kind,
                ),
            )
            active_session_id = _safe_text(session_payload.get("id"))
            if not active_session_id:
                raise RuntimeError("failed to create role session")
            role_sessions[current_role] = active_session_id
            if super_mode:
                print(f"role={SUPER_ROLE} fallback_role={current_role} session={active_session_id}")
            else:
                print(f"role={current_role} session={active_session_id}")
            continue
        if message.startswith("/role"):
            next_role = _safe_text(message.removeprefix("/role")).lower()
            if not next_role:
                print("[error] role name required after /role", file=sys.stderr)
                continue
            if next_role not in allowed_roles:
                print(
                    f"[error] unsupported role={next_role!r}; allowed={', '.join(sorted(allowed_roles))}",
                    file=sys.stderr,
                )
                continue
            current_role = next_role
            active_session_id = role_sessions.get(current_role) or _resolve_role_session(
                host,
                role=current_role,
                role_sessions=role_sessions,
                host_kind=host_kind,
            )
            if super_mode:
                print(f"role={SUPER_ROLE} fallback_role={current_role} session={active_session_id}")
            else:
                print(f"role={current_role} session={active_session_id}")
            continue
        if message.startswith("/json"):
            next_mode = _safe_text(message.removeprefix("/json")).lower()
            if not next_mode:
                print(f"json_render={render_state.json_render}")
                continue
            if next_mode not in _JSON_RENDER_MODES:
                print(
                    f"[error] unsupported json render mode={next_mode!r}; "
                    f"allowed={', '.join(sorted(_JSON_RENDER_MODES))}",
                    file=sys.stderr,
                )
                continue
            render_state.json_render = next_mode
            print(f"json_render={render_state.json_render}")
            continue
        if message.startswith("/prompt"):
            next_style = _safe_text(message.removeprefix("/prompt")).lower()
            if not next_style:
                omp_desc = render_state.omp_config or "-"
                print(f"prompt_style={render_state.prompt_style} omp_config={omp_desc}")
                continue
            if next_style not in _PROMPT_STYLES:
                print(
                    f"[error] unsupported prompt style={next_style!r}; allowed={', '.join(sorted(_PROMPT_STYLES))}",
                    file=sys.stderr,
                )
                continue
            render_state.prompt_style = next_style
            prompt_renderer.reset()
            print(f"prompt_style={render_state.prompt_style}")
            continue
        if message.startswith("/keymode"):
            next_keymode = _safe_text(message.removeprefix("/keymode")).lower()
            if not next_keymode:
                print(f"keymode={current_keymode}")
                continue
            resolved = _resolve_keymode(next_keymode)
            if resolved != current_keymode:
                current_keymode = resolved
                _apply_keymode(current_keymode)
                _save_keymode(current_keymode)
            print(f"keymode={current_keymode}")
            continue
        if message.startswith("/model"):
            next_model = _safe_text(message.removeprefix("/model")).lower()
            if not next_model:
                current_model = _get_current_model()
                print(f"model={current_model or 'not configured via environment'}")
                continue
            # Validate model name
            if next_model not in _KNOWN_MODELS:
                print(f"[error] unknown model={next_model!r}; known models:", file=sys.stderr)
                for m in _KNOWN_MODELS:
                    print(f"  {m}", file=sys.stderr)
                continue
            _set_current_model(next_model)
            print(f"[model] Switched to: {next_model}")
            print("[model] Warning: Model switch takes effect on next message")
            continue
        if message.startswith("/dryrun"):
            next_dryrun = _safe_text(message.removeprefix("/dryrun")).lower()
            if not next_dryrun:
                print(f"dry_run={current_dry_run}")
                continue
            if next_dryrun == "on":
                current_dry_run = True
                print(f"dry_run={current_dry_run}")
            elif next_dryrun == "off":
                current_dry_run = False
                print(f"dry_run={current_dry_run}")
            else:
                print("[error] unsupported dryrun value; use /dryrun [on|off]", file=sys.stderr)
            continue

        try:
            if super_mode:
                result = _run_super_turn(
                    host,
                    fallback_role=current_role,
                    role_sessions=role_sessions,
                    host_kind=host_kind,
                    session_title=session_title,
                    workspace_path=workspace_path,
                    render_state=render_state,
                    prompt_renderer=prompt_renderer,
                    message=raw,
                    json_render=render_state.json_render,
                    debug=debug_enabled,
                    dry_run=current_dry_run,
                    output_format=render_state.output_format,
                    enable_cognitive=enable_cognitive,
                )
                active_session_id = result.session_id
            else:
                _run_streaming_turn(
                    host,
                    role=current_role,
                    session_id=active_session_id,
                    message=raw,
                    json_render=render_state.json_render,
                    debug=debug_enabled,
                    spinner_label=prompt_renderer.render_spinner_label(
                        role=current_role,
                        session_id=active_session_id,
                        workspace=workspace_path,
                    ),
                    dry_run=current_dry_run,
                    output_format=render_state.output_format,
                    enable_cognitive=enable_cognitive,
                )
        except KeyboardInterrupt:
            print()
            print("[console] interrupted current turn", file=sys.stderr)
        except (RuntimeError, ValueError) as exc:
            print(f"[error] {exc}", file=sys.stderr)


def run_director_console(
    *,
    workspace: str | Path = ".",
    role: str = "director",
    backend: str = "auto",
    session_id: str | None = None,
    session_title: str | None = None,
    prompt_style: str | None = None,
    omp_config: str | None = None,
    json_render: str | None = None,
    debug: bool | None = None,
    batch: bool = False,
    model: str | None = None,
    dry_run: bool = False,
    enable_cognitive: bool | None = None,
    super_mode: bool = False,
) -> int:
    """Legacy alias retained for compatibility with Director entry points."""
    return run_role_console(
        workspace=workspace,
        role=role or "director",
        backend=backend,
        session_id=session_id,
        session_title=session_title,
        prompt_style=prompt_style,
        omp_config=omp_config,
        json_render=json_render,
        debug=debug,
        batch=batch,
        model=model,
        dry_run=dry_run,
        enable_cognitive=enable_cognitive,
        super_mode=super_mode,
    )


__all__ = [
    "PolarisLazyClaude",
    "PolarisRoleConsole",
    "run_director_console",
    "run_role_console",
]
