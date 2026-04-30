"""Tab auto-completion and command history for Polaris CLI using readline."""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

# Optional readline import for cross-platform compatibility
try:
    import readline
except ImportError:
    readline = None

# Optional prompt-toolkit import for enhanced input
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.filters import is_tty as prompt_toolkit_is_tty
    from prompt_toolkit.lexers import SimpleLexer
    from prompt_toolkit.styles import Style

    _PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    _PROMPT_TOOLKIT_AVAILABLE = False
    PromptSession = None
    prompt_toolkit_is_tty = None
    SimpleLexer = None
    Style = None

# readline is conditionally imported; mypy cannot see its attributes when
# the import is optional.  All readline usage is already guarded with
# ``if readline is None: return`` runtime checks.
if TYPE_CHECKING:
    import readline as _readline_stub
else:
    _readline_stub = readline

__all__ = [
    "CLICCompleter",
    "get_history_file_path",
    "load_history",
    "readline_input",
    "save_history",
]

# Command definitions for tab completion
_ROLES: tuple[str, ...] = ("pm", "architect", "chief_engineer", "director", "qa")
_JSON_MODES: tuple[str, ...] = ("raw", "pretty", "pretty-color")
_PROMPT_STYLES: tuple[str, ...] = ("plain", "omp")
_SESSION_SUBCMDS: tuple[str, ...] = ("list", "show", "switch", "clear")

# Known LLM models for /model command completion
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

# Commands that take no arguments
_NO_ARG_COMMANDS: frozenset[str] = frozenset({"/help", "/exit", "/quit", "/new", "/refresh"})

# Commands that need file path completion
_FILE_PATH_COMMANDS: frozenset[str] = frozenset({"/read", "/write"})

# History file path
_HISTORY_FILE_NAME = ".polaris_cli_history"
_MAX_HISTORY_ENTRIES = 1000


def get_history_file_path() -> Path:
    """Get the path to the CLI history file."""
    home = Path.home()
    return home / _HISTORY_FILE_NAME


def load_history() -> None:
    """Load command history from the history file."""
    if readline is None:
        return
    history_path = get_history_file_path()
    if not history_path.exists():
        return
    try:
        _readline_stub.read_history_file(str(history_path))
        # Trim to max entries
        current_len = _readline_stub.get_current_history_length()
        if current_len > _MAX_HISTORY_ENTRIES:
            excess = current_len - _MAX_HISTORY_ENTRIES
            for _ in range(excess):
                _readline_stub.remove_history_item(0)
    except OSError:
        pass


def save_history() -> None:
    """Save command history to the history file."""
    if readline is None:
        return
    history_path = get_history_file_path()
    with contextlib.suppress(OSError):
        _readline_stub.write_history_file(str(history_path))


class CLICCompleter:
    """Completer for Polaris CLI commands using readline."""

    def __init__(self) -> None:
        self._in_file_path_mode = False
        self._current_path: str = ""

    def complete(self, text: str, state: int) -> str | None:
        """Return the completion candidate at given state.

        This is called by readline for each character typed during
        completion.
        """
        # Check if we're completing a file path after /read or /write
        if self._in_file_path_mode:
            return self._complete_file_path(text, state)

        # Check if the text starts with /
        if not text.startswith("/") and state == 0:
            # For non-/text, return nothing (standard input mode)
            return None

        # Handle slash commands
        return self._complete_command(text, state)

    def _complete_command(self, text: str, state: int) -> str | None:
        """Complete slash commands."""
        # Get all unique command prefixes
        commands: list[str] = []

        # Add command prefixes with their sub-options
        commands.extend(f"/role {r}" for r in _ROLES)
        commands.extend(f"/json {m}" for m in _JSON_MODES)
        commands.extend(f"/prompt {p}" for p in _PROMPT_STYLES)
        commands.extend(f"/session {s}" for s in _SESSION_SUBCMDS)
        commands.extend(f"/model {m}" for m in _KNOWN_MODELS)
        commands.extend(_NO_ARG_COMMANDS)

        # Filter by text
        matching = [cmd for cmd in commands if cmd.startswith(text)]
        if state < len(matching):
            return matching[state]
        return None

    def _complete_file_path(self, text: str, state: int) -> str | None:
        """Complete file paths for /read and /write commands."""
        # Determine the directory and prefix
        if text.startswith("/") or text.startswith("~"):
            # Absolute path or home dir
            expand = os.path.expanduser(text)
            if os.path.isdir(expand):
                dir_path = expand
                prefix = ""
            else:
                dir_path = os.path.dirname(expand)
                prefix = os.path.basename(expand)
        elif text.startswith("./") or text.startswith("../"):
            dir_path = os.path.dirname(text) or "."
            prefix = os.path.basename(text)
        else:
            # Relative path - use current directory
            dir_path = "."
            prefix = text

        if not dir_path:
            dir_path = "."

        try:
            entries = os.listdir(dir_path)
        except OSError:
            entries = []

        # Build completions with trailing slash for directories
        completions: list[str] = []
        for entry in entries:
            if prefix and not entry.startswith(prefix):
                continue

            full_path = os.path.join(dir_path, entry)
            # Determine what to insert
            if os.path.isdir(full_path):
                completions.append(entry + "/")
            else:
                completions.append(entry)

        if state < len(completions):
            # Return the completion without prefix adjustment -
            # readline handles this
            result = completions[state]
            # If we had a path prefix, prepend the directory part
            if (
                prefix
                and dir_path != "."
                and (text.startswith("/") or text.startswith("~") or text.startswith("./") or text.startswith("../"))
            ):
                result = os.path.join(os.path.dirname(text), result)
            return result
        return None

    def set_file_path_mode(self, enabled: bool, current_text: str = "") -> None:
        """Enable or disable file path completion mode."""
        self._in_file_path_mode = enabled
        self._current_path = current_text


def _create_default_completer() -> CLICCompleter:
    """Create the default completer instance."""
    return CLICCompleter()


# Global completer instance
_default_completer: CLICCompleter | None = None


def _get_completer() -> CLICCompleter | None:
    """Get or create the global completer instance."""
    if readline is None:
        return None
    global _default_completer
    if _default_completer is None:
        _default_completer = _create_default_completer()
    return _default_completer


def _setup_readline_completion(completer: CLICCompleter) -> None:
    """Set up readline with the completer."""
    if readline is None:
        return
    try:
        _readline_stub.set_completer(completer.complete)
        # Enable tab completion
        _readline_stub.parse_and_bind("tab: complete")
        # For partial completions
        _readline_stub.parse_and_bind("?: complete")
    except (RuntimeError, ValueError):
        import logging

        logger = logging.getLogger(__name__)
        logger.warning("Failed to setup readline tab completion")
        pass


def _teardown_readline_completion() -> None:
    """Clean up readline completion."""
    if readline is None:
        return
    with contextlib.suppress(Exception):
        _readline_stub.set_completer(None)


def _prompt_toolkit_available() -> bool:
    """Check if prompt-toolkit is available and should be used."""
    if not _PROMPT_TOOLKIT_AVAILABLE or prompt_toolkit_is_tty is None:
        return False
    # Only use prompt-toolkit in TTY mode
    try:
        return sys.stdin.isatty() and prompt_toolkit_is_tty()
    except (RuntimeError, ValueError):
        return False


# Prompt style for prompt-toolkit session (only defined when prompt-toolkit is available)
_PROMPT_STYLE: Style | None = None

# Role symbols for toolbar
_ROLE_SYMBOLS: dict[str, str] = {
    "director": "◉",
    "pm": "◆",
    "architect": "◇",
    "chief_engineer": "◈",
    "qa": "◎",
}


def _get_prompt_style() -> Style:
    """Get or create the prompt style (lazy initialization)."""
    global _PROMPT_STYLE
    if _PROMPT_STYLE is None and _PROMPT_TOOLKIT_AVAILABLE and Style is not None:
        _PROMPT_STYLE = Style.from_dict(
            {
                "symbol": "#36m",  # cyan
                "role": "#32m",  # green
                "arrow": "#36m",  # cyan
                "toolbar": "#36m",  # cyan
                "toolbar-bg": "#1e1e1e",
            }
        )
    if _PROMPT_STYLE is None:
        raise RuntimeError("Prompt style could not be initialized")
    return _PROMPT_STYLE


def _build_toolbar(role: str = "director", session_id: str = "") -> str:
    """Build the bottom toolbar text."""
    symbol = _ROLE_SYMBOLS.get(role.lower(), "▸")
    width = 60
    dash_count = max(10, width - len(f" {symbol} [{role}] "))
    dashes = "─" * dash_count
    return f" {symbol} [{role}] {dashes}"


def _prompt_toolkit_input(
    prompt: str,
    *,
    role: str = "director",
    session_id: str = "",
) -> str:
    """Read input using prompt-toolkit with bottom toolbar."""
    try:
        session: PromptSession[str] = PromptSession(
            message=prompt,
            bottom_toolbar=lambda: _build_toolbar(role, session_id),
            style=_get_prompt_style(),
            lexer=SimpleLexer(),
            enable_history_search=True,
            complete_in_thread=True,
        )
        return session.prompt()
    except (EOFError, KeyboardInterrupt):
        raise
    except (RuntimeError, ValueError):
        # Fall back to readline-based input on any error
        return input(prompt)


def readline_input(
    prompt: str,
    *,
    file_path_commands: frozenset[str] | None = None,
    role: str = "director",
    session_id: str = "",
) -> str:
    """Read input with prompt-toolkit if available, otherwise readline or fallback.

    Args:
        prompt: The prompt to display.
        file_path_commands: Set of commands that trigger file path completion.
        role: The current role for toolbar display.
        session_id: The current session ID for toolbar display.

    Returns:
        The input string from the user.
    """
    # Try prompt-toolkit first
    if _prompt_toolkit_available():
        return _prompt_toolkit_input(prompt, role=role, session_id=session_id)

    # Fall back to readline-based input
    completer = _get_completer()
    if file_path_commands is None:
        file_path_commands = _FILE_PATH_COMMANDS

    if completer is None:
        return input(prompt)

    try:
        _setup_readline_completion(completer)

        # Check if the prompt indicates a file path command
        # This is a heuristic - we check if prompt starts with known patterns
        stripped = prompt.rstrip()
        needs_file_completion = any(stripped.startswith(cmd) for cmd in file_path_commands)
        completer.set_file_path_mode(needs_file_completion)

        result = input(prompt)
        return result
    except (RuntimeError, ValueError):
        # Fall back to plain input on any error
        return input(prompt)
    finally:
        completer.set_file_path_mode(False)
        _teardown_readline_completion()


# Simple completer function for direct use
def _simple_completer(text: str, state: int) -> str | None:
    """A simple completer function suitable for readline.set_completer()."""
    completer = _get_completer()
    if completer is None:
        return None
    return completer.complete(text, state)
