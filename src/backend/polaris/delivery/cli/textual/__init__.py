"""Polaris Textual TUI Console

基于 Textual 框架的可折叠 CLI 界面。

Usage:
    python -m polaris.delivery.cli chat --mode console --backend textual --debug
"""

from polaris.delivery.cli.textual.console import PolarisTextualConsole, run_textual_console
from polaris.delivery.cli.textual.models import DebugItem, MessageItem, MessageType
from polaris.delivery.cli.textual.styles import (
    ThemeColors,
    ThemeManager,
    ThemeMode,
    get_console_css,
    get_theme_colors,
    get_theme_manager,
)

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
