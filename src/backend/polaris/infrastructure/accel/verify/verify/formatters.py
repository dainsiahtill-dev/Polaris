"""Command parsing and formatting utilities for verify orchestrator."""

from __future__ import annotations

import os
import re
import shlex
import shutil
from pathlib import Path
from typing import Any


def command_binary(command: str) -> str:
    """Extract the binary name from a shell command."""
    effective_command = effective_shell_command(command)
    parts = shlex.split(effective_command, posix=os.name != "nt")
    return parts[0] if parts else ""


def effective_shell_command(command: str) -> str:
    """Get the effective command after handling chained commands."""
    text = str(command or "").strip()
    if "&&" not in text:
        return text
    parts = [part.strip() for part in text.split("&&") if part.strip()]
    return parts[-1] if parts else text


def command_workdir(project_dir: Path, command: str) -> Path:
    """Determine the working directory for a command."""
    text = str(command or "").strip()
    if "&&" not in text:
        return project_dir
    first, _, _tail = text.partition("&&")
    prefix = first.strip()
    match = re.match(
        r'^cd\s+(?:/d\s+)?(?:"([^"]+)"|\'([^\']+)\'|([^"\']\S*))$',
        prefix,
        flags=re.IGNORECASE,
    )
    if not match:
        return project_dir
    path_token = next((item for item in match.groups() if item), "")
    if not path_token:
        return project_dir
    candidate = Path(path_token)
    if candidate.is_absolute():
        return candidate
    return (project_dir / candidate).resolve()


def extract_python_module(command: str) -> str:
    """Extract the Python module name from a python -m command."""
    effective_command = effective_shell_command(command)
    parts = shlex.split(effective_command, posix=os.name != "nt")
    if len(parts) < 3:
        return ""
    if str(parts[1]).strip() != "-m":
        return ""
    return str(parts[2]).strip()


def parse_command_tokens(command: str) -> list[str]:
    """Parse command into tokens safely."""
    try:
        return shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return [part for part in str(command or "").strip().split(" ") if part]


def extract_node_script(command: str) -> str:
    """Extract the npm/pnpm/yarn script name from a command."""
    tokens = parse_command_tokens(effective_shell_command(command))
    if not tokens:
        return ""
    binary = str(tokens[0]).strip().lower()
    if binary not in {"npm", "pnpm", "yarn"}:
        return ""
    if len(tokens) <= 1:
        return ""
    if binary == "yarn":
        script = str(tokens[1]).strip().lower()
        return script if script and not script.startswith("-") else ""
    action = str(tokens[1]).strip().lower()
    if action in {"test", "lint", "typecheck", "build"}:
        return action
    if action in {"run", "run-script"} and len(tokens) >= 3:
        script = str(tokens[2]).strip().lower()
        return script if script else ""
    return ""


def split_pytest_target(token: str) -> str:
    """Split pytest target to get the file path."""
    value = str(token or "").strip().strip('"').strip("'")
    if "::" not in value:
        return value
    return str(value.split("::", 1)[0]).strip()


def extract_pytest_targets(command: str) -> list[str]:
    """Extract pytest test target paths from a command."""
    if "pytest" not in str(command).lower():
        return []
    effective = effective_shell_command(command)
    parts = shlex.split(effective, posix=os.name != "nt")
    if not parts:
        return []
    targets: list[str] = []
    for token in parts[1:]:
        cleaned = str(token).strip().strip('"').strip("'")
        if not cleaned or cleaned.startswith("-"):
            continue
        if cleaned.endswith(".py"):
            targets.append(cleaned.replace("\\", "/"))
    return targets


def resolve_windows_compatible_command(project_dir: Path, command: str) -> tuple[str, str]:
    """Resolve Windows-compatible command fallback for make."""
    text = str(command or "").strip()
    if os.name != "nt":
        return text, ""
    if not text:
        return text, ""
    binary = command_binary(text).strip().lower()
    if binary != "make":
        return text, ""
    if shutil.which("make") is not None:
        return text, ""

    parts = shlex.split(text, posix=False)
    if len(parts) < 2:
        return text, ""
    target = str(parts[1]).strip().lower()
    if target != "install-pre-commit-hooks":
        return text, ""

    fallback = "python -m pre_commit install"
    if not shutil.which("python"):
        fallback = "pre-commit install"
    return fallback, "windows_make_target_compat:install-pre-commit-hooks"


def normalize_positive_int(value: Any, default_value: int) -> int:
    """Normalize a value to a positive integer."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return max(1, int(default_value))
    return max(1, parsed)


def normalize_bool(value: Any, default_value: bool = False) -> bool:
    """Normalize a value to boolean."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default_value
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on"}


def normalize_optional_positive_float(value: Any) -> float | None:
    """Normalize a value to optional positive float."""
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return float(parsed)


__all__ = [
    "command_binary",
    "command_workdir",
    "effective_shell_command",
    "extract_node_script",
    "extract_pytest_targets",
    "extract_python_module",
    "normalize_bool",
    "normalize_optional_positive_float",
    "normalize_positive_int",
    "parse_command_tokens",
    "resolve_windows_compatible_command",
    "split_pytest_target",
]
