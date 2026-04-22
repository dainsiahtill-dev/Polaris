"""Prompt toolkit-based input session with integrated status prompt."""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING, Any

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.lexers import SimpleLexer
    from prompt_toolkit.styles import Style

    _PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    _PROMPT_TOOLKIT_AVAILABLE = False
    Style = None  # type: ignore[assignment, misc]
    HTML = None  # type: ignore[assignment, misc]

if TYPE_CHECKING:
    pass

# Prompt style (lazy initialization)
_PROMPT_STYLE: Style | None = None


def _get_prompt_style() -> Style:
    """Get or create the prompt style (lazy initialization)."""
    global _PROMPT_STYLE
    if _PROMPT_STYLE is None and _PROMPT_TOOLKIT_AVAILABLE and Style is not None:
        _PROMPT_STYLE = Style.from_dict(
            {
                "symbol": "#36m bold",  # cyan bold
                "role": "#32m bold",  # green bold
                "ws": "#90a3b4",  # muted blue-gray
                "arrow": "#36m",  # cyan
                "prompt": "#36m",  # cyan
            }
        )
    # Return a dummy style if still None (shouldn't happen when prompt_toolkit is available)
    if _PROMPT_STYLE is None:
        _PROMPT_STYLE = Style.from_dict({})  # type: ignore[operator]
    return _PROMPT_STYLE


# Role symbols
_ROLE_SYMBOLS = {
    "director": "◉",
    "pm": "◆",
    "architect": "◇",
    "chief_engineer": "◈",
    "qa": "◎",
    "super": "✦",
}


class PromptInputSession:
    """Prompt session using prompt-toolkit with integrated status.

    Design: Status is integrated into the prompt on a SINGLE line:
    ┌─────────────────────────────────────────────────────────────┐
    │  ◉ [director]  ~/projects/myapp                    ›      │
    └─────────────────────────────────────────────────────────────┘

    Supports Oh My Posh via optional omp_config/omp_executable.
    """

    def __init__(
        self,
        *,
        role: str = "director",
        session_id: str = "",
        completions: dict[str, list[str]] | None = None,
        workspace: str = "",
        omp_config: str | None = None,
        omp_executable: str = "oh-my-posh",
    ) -> None:
        self._role = role
        self._session_id = session_id
        self._completions = completions or {}
        self._workspace = workspace
        self._omp_config = omp_config
        self._omp_executable = omp_executable
        self._omp_available = True
        self._session: PromptSession | None = None

    def _get_symbol(self) -> str:
        return _ROLE_SYMBOLS.get(self._role.lower(), "▸")

    def _render_omp(self) -> str | None:
        """Try to render prompt using Oh My Posh.

        Returns the rendered prompt string if OMP is available, None otherwise.
        """
        if not self._omp_available:
            return None

        executable = self._omp_executable or "oh-my-posh"
        config_path = self._omp_config
        common_args = [executable]
        command_variants: list[list[str]] = [
            [*common_args, "print", "primary", "--shell", "pwsh"],
            [*common_args, "print", "primary"],
            [*common_args, "prompt", "print", "primary", "--shell", "pwsh"],
            [*common_args, "prompt", "print", "primary"],
        ]
        if config_path:
            command_variants = [[*variant, "--config", config_path] for variant in command_variants]

        env = os.environ.copy()
        env["POLARIS_ROLE"] = self._role
        env["POLARIS_SESSION_ID"] = self._session_id
        env["POLARIS_WORKSPACE"] = self._workspace
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

        self._omp_available = False
        return None

    def _build_prompt(self) -> HTML | str:
        """Build the integrated single-line prompt with status.

        First tries Oh My Posh, then falls back to HTML-based rich prompt.
        """
        # Try OMP first
        omp_prompt = self._render_omp()
        if omp_prompt:
            return omp_prompt

        # Fall back to HTML prompt
        if not _PROMPT_TOOLKIT_AVAILABLE or HTML is None:
            return "› "

        symbol = self._get_symbol()
        # Truncate workspace if too long
        ws = self._workspace
        if len(ws) > 35:
            ws = "~/" + ws.split("/")[-1] if "/" in ws else ws[-32:]

        # Build HTML prompt: symbol [role]  workspace  ›
        prompt_html = f"<symbol>{symbol}</symbol> <role>[{self._role}]</role>  <ws>{ws}</ws>  <prompt>› </prompt>"
        return HTML(prompt_html)

    def prompt(self) -> str:
        """Run the prompt session and return user input."""
        if not _PROMPT_TOOLKIT_AVAILABLE:
            # Fallback to plain input
            return input("› ")

        try:
            session: Any = PromptSession(
                message=self._build_prompt(),
                style=_get_prompt_style(),
                lexer=SimpleLexer(),
                enable_history_search=True,
                complete_in_thread=True,
            )
            self._session = session
            result = session.prompt()
            return result
        except (EOFError, KeyboardInterrupt):
            raise
        except (RuntimeError, ValueError):
            # Fallback on any error
            return input("› ")

    def set_role(self, role: str) -> None:
        """Update the role (recreates prompt)."""
        self._role = role

    def set_workspace(self, workspace: str) -> None:
        """Update the workspace (recreates prompt)."""
        self._workspace = workspace


def create_prompt_session(
    *,
    role: str = "director",
    session_id: str = "",
    completions: dict[str, list[str]] | None = None,
    workspace: str = "",
    omp_config: str | None = None,
    omp_executable: str = "oh-my-posh",
) -> PromptInputSession:
    """Factory to create a prompt input session."""
    return PromptInputSession(
        role=role,
        session_id=session_id,
        completions=completions,
        workspace=workspace,
        omp_config=omp_config,
        omp_executable=omp_executable,
    )
