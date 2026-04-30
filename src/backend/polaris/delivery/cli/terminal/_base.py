"""Shared constants and utility functions for terminal console modules."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shlex
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# Disable asyncio debug early to prevent log pollution
# Use environment variable approach for cross-version compatibility
os.environ.setdefault("PYTHONASYNCIODEBUG", "0")

# Optional readline import for keyboard mode support
try:
    import readline
except ImportError:
    readline = None

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

# Sentinel for unset output format (CLI case: --output-format not provided)
_UNSET = object()

_INFRASTRUCTURE_LOGGERS: tuple[str, ...] = (
    "polaris.infrastructure.llm.provider_bootstrap",
    "polaris.cells.roles.session.internal.session_persistence",
    "polaris.cells.roles.session.internal.role_session_service",
)

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
  /skill [command]   Skill management (list|load <name>|reload)
  /exit              Exit console

Launch option:
  --super            Enable intent-based SUPER mode (auto routes PM/Director/etc.)
"""

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

_KERNELONE_ART = """
[cyan bold]◈ Polaris CLI[/cyan bold] [dim]·[/dim] [white]Polaris[/white]
"""


def _get_role_symbol(role: str) -> str:
    """Get unicode symbol for a role prompt."""
    return ROLE_PROMPT_SYMBOLS.get(role.lower(), "▸")


def _get_tool_style(tool_name: str) -> str:
    """Get color style for a tool name."""
    return TOOL_NAME_STYLES.get(tool_name, "cyan")


def _suppress_infrastructure_logs() -> dict[str, int]:
    """Temporarily suppress INFO/DEBUG logs from polaris infrastructure loggers.

    Returns a dict mapping logger names to their previous levels so that
    _restore_infrastructure_logs can restore them.
    """
    previous_levels: dict[str, int] = {}
    for name in _INFRASTRUCTURE_LOGGERS:
        log = logging.getLogger(name)
        previous_levels[name] = log.level
        log.setLevel(logging.WARNING)
    return previous_levels


def _restore_infrastructure_logs(previous_levels: dict[str, int]) -> None:
    """Restore loggers to their previous levels after banner display."""
    for name, level in previous_levels.items():
        log = logging.getLogger(name)
        log.setLevel(level)


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


def _get_current_model() -> str | None:
    """Get the currently configured LLM model from environment.

    SSOT: Model info must come from ContextOS via event payload.
    Environment variable is only a fallback, not the primary source.
    Returns None if not set - caller must handle appropriately.
    """
    return os.environ.get("KERNELONE_PM_MODEL")


def _set_current_model(model: str) -> None:
    """Set the LLM model for subsequent requests via environment variable."""
    os.environ["KERNELONE_PM_MODEL"] = model


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


def _detect_keymode_from_shell() -> str:
    """Detect keymode from $SHELLOPTS environment variable."""
    try:
        opts = shlex.split(os.environ.get("SHELLOPTS", ""))
        return "vi" if "vi" in opts else "emacs"
    except (RuntimeError, ValueError):
        return "emacs"


def _get_default_keymode() -> str:
    """Resolve the default keymode: env var, persisted file, or auto-detection."""
    env_keymode = os.environ.get("KERNELONE_CLI_KEYMODE", "auto").lower()
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
            rl.parse_and_bind("set editing-mode vi")
            rl.parse_and_bind("vi-insert-mode-key")
            rl.parse_and_bind("vi-command-mode-key")
        else:
            rl.parse_and_bind("set editing-mode emacs")
        return True
    except (RuntimeError, ValueError):
        return False


def _save_keymode(keymode: str) -> None:
    """Persist the keymode preference to ~/.polaris_cli_keymode."""
    with contextlib.suppress(Exception):
        _KEYMODE_FILE.write_text(keymode, "utf-8")


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
