"""Polaris CLI compatibility shim — emits warnings for deprecated entry points."""

from __future__ import annotations

import logging
import sys
import warnings
from typing import Final

logger: logging.Logger = logging.getLogger(__name__)

# Legacy entry point aliases that emit deprecation warnings
_LEGACY_ENTRY_POINTS: Final[set[str]] = {
    "polaris-director",
    "polaris-pm",
    "polaris-cli",
    "polaris-director",
    "polaris-pm",
}


def emit_compat_warnings(argv: list[str]) -> None:
    """Emit warnings for deprecated CLI entry points and flag usage.

    Args:
        argv: The raw command-line arguments (sys.argv).
    """
    program = argv[0] if argv else ""
    program_name = program.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].replace(".exe", "")

    if program_name in _LEGACY_ENTRY_POINTS:
        _warn(
            f"Entry point '{program_name}' is deprecated. Use 'polaris-cli' or 'polaris-lazy' instead.",
            DeprecationWarning,
            stacklevel=3,
        )


def warn_if_old_runtime_mode(mode: str) -> None:
    """Warn if a deprecated runtime mode is requested.

    Args:
        mode: The runtime mode string requested by the user.
    """
    deprecated_modes = {"rich", "textual", "server"}
    if mode in deprecated_modes:
        _warn(
            f"Runtime mode '{mode}' is deprecated or non-canonical. Prefer 'interactive' or 'console' mode.",
            DeprecationWarning,
            stacklevel=2,
        )


def warn_if_no_workspace(workspace: str | None) -> None:
    """Warn if no workspace is explicitly specified.

    Args:
        workspace: The workspace path, or None if not specified.
    """
    if not workspace:
        _warn(
            "No --workspace specified; defaulting to current directory. "
            "This behaviour may change in a future release. "
            "Always pass an explicit --workspace argument.",
            UserWarning,
            stacklevel=2,
        )


def _warn(message: str, category: type[Warning], stacklevel: int) -> None:
    """Emit a warning both via warnings.warn and logger.warning."""
    warnings.warn(message, category, stacklevel=stacklevel)
    logger.warning("%s", message)


def check_compat(argv: list[str] | None = None) -> None:
    """Top-level compatibility check to be called at CLI entry points.

    Args:
        argv: Optional override for sys.argv.
    """
    args = argv if argv is not None else (sys.argv if hasattr(sys, "argv") else [])
    emit_compat_warnings(list(args))
