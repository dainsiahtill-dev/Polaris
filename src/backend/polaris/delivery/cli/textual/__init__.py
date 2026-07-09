"""Polaris Textual TUI Console

基于 Textual 框架的可折叠 CLI 界面。

Usage:
    python -m polaris.delivery.cli chat --mode console --backend textual --debug
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from polaris.delivery.cli.textual.models import DebugItem, MessageItem, MessageType
from polaris.delivery.cli.textual.styles import (
    ThemeColors,
    ThemeManager,
    ThemeMode,
    get_console_css,
    get_theme_colors,
    get_theme_manager,
)

if TYPE_CHECKING:
    from polaris.delivery.cli.textual.console import PolarisTextualConsole, run_textual_console

_LAZY_EXPORT_MODULES = {
    "PolarisTextualConsole": "polaris.delivery.cli.textual.console",
    "run_textual_console": "polaris.delivery.cli.textual.console",
}


def __getattr__(name: str) -> Any:
    """Lazily import Textual-backed console exports."""
    module_name = _LAZY_EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    import importlib

    module = importlib.import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


__all__ = [
    # Models
    "DebugItem",
    "MessageItem",
    "MessageType",
    # Console
    "PolarisTextualConsole",
    # Theme
    "ThemeColors",
    "ThemeManager",
    "ThemeMode",
    "get_console_css",
    "get_theme_colors",
    "get_theme_manager",
    "run_textual_console",
]
