"""Shared CLI logging policy for Polaris delivery entry points."""

from __future__ import annotations

import logging
import os
import sys

CLI_LOG_LEVEL_CHOICES: tuple[str, ...] = (
    "debug",
    "info",
    "warn",
    "warning",
    "error",
    "critical",
)

_LOG_LEVEL_ALIASES: dict[str, str] = {
    "debug": "debug",
    "info": "info",
    "warn": "warning",
    "warning": "warning",
    "error": "error",
    "critical": "critical",
    "fatal": "critical",
}
_DEFAULT_LOG_LEVEL = "warning"
_DEFAULT_LOG_FORMAT = "[%(levelname)s] %(name)s: %(message)s"


def normalize_log_level(level: str | None) -> str:
    """Normalize CLI log-level token to a canonical name."""

    token = str(level or "").strip().lower()
    if not token:
        return _DEFAULT_LOG_LEVEL
    normalized = _LOG_LEVEL_ALIASES.get(token)
    if normalized is None:
        supported = ", ".join(sorted(CLI_LOG_LEVEL_CHOICES))
        raise ValueError(f"Unsupported --log-level={level!r}. Supported: {supported}")
    return normalized


def resolve_log_level(level: str | None, *, env_var: str = "KERNELONE_CLI_LOG_LEVEL") -> str:
    """Resolve CLI log level from flag first, then environment."""

    explicit = str(level or "").strip()
    if explicit:
        return normalize_log_level(explicit)
    env_level = str(os.environ.get(env_var, "") or "").strip()
    if env_level:
        return normalize_log_level(env_level)
    return _DEFAULT_LOG_LEVEL


def configure_cli_logging(level: str | None = None) -> int:
    """Configure root logging for CLI host output and return numeric level."""

    resolved = resolve_log_level(level)
    numeric_level = getattr(logging, resolved.upper(), logging.WARNING)
    logging.basicConfig(
        level=numeric_level,
        format=_DEFAULT_LOG_FORMAT,
        stream=sys.stderr,
        force=True,
    )
    return int(numeric_level)
