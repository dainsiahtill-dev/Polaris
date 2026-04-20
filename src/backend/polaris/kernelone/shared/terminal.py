"""终端输出工具。

提供 ANSI 颜色支持和终端检测功能。

Example:
    >>> from polaris.kernelone.shared import supports_color
    >>> if supports_color():
    ...     print(f"{ANSI_COLORS['ERROR']}Error:{ANSI_RESET} message")
"""

from __future__ import annotations

import os
import sys
from typing import Final

# ANSI 颜色代码
ANSI_RESET: Final[str] = "\x1b[0m"

ANSI_COLORS: Final[dict[str, str]] = {
    "INFO": "\x1b[36m",
    "TURN": "\x1b[34m",
    "COMMAND": "\x1b[33m",
    "FILE": "\x1b[32m",
    "TOOL": "\x1b[35m",
    "THINKING": "\x1b[90m",
    "ERROR": "\x1b[31m",
    "AGENT": "\x1b[36m",
    # 额外标准颜色
    "reset": "\x1b[0m",
    "bold": "\x1b[1m",
    "dim": "\x1b[2m",
    "red": "\x1b[91m",
    "green": "\x1b[92m",
    "yellow": "\x1b[93m",
    "blue": "\x1b[94m",
    "magenta": "\x1b[95m",
    "cyan": "\x1b[96m",
    "white": "\x1b[97m",
    "black": "\x1b[30m",
    "gray": "\x1b[90m",
    # 背景色
    "bg_red": "\x1b[41m",
    "bg_green": "\x1b[42m",
    "bg_yellow": "\x1b[43m",
}

# 模块级状态
ANSI_ENABLED: bool = False


def supports_ansi() -> bool:
    """检测终端是否支持 ANSI 颜色。

    Returns:
        如果终端支持 ANSI 颜色返回 True
    """
    global ANSI_ENABLED

    if ANSI_ENABLED:
        return True

    # 检查环境变量
    if os.environ.get("NO_COLOR"):
        return False

    # 检查 stderr 是否为 tty
    if hasattr(sys.stderr, "isatty") and not sys.stderr.isatty():
        return False

    # Windows 检查
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            mode = ctypes.c_ulong()
            handle = kernel32.GetStdHandle(-12)  # STD_OUTPUT_HANDLE
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                ANSI_ENABLED = True
                return True
            return False
        except (RuntimeError, ValueError):
            return False

    # Unix 检查
    term = os.environ.get("TERM", "")
    return term not in ("", "dumb")


def set_ansi_enabled(enabled: bool) -> None:
    """设置 ANSI 颜色启用状态。

    Args:
        enabled: 是否启用 ANSI 颜色
    """
    global ANSI_ENABLED
    ANSI_ENABLED = bool(enabled)


def supports_color() -> bool:
    """检测是否应该输出颜色。

    Returns:
        如果应该输出颜色返回 True
    """
    if not ANSI_ENABLED:
        return False
    if os.environ.get("NO_COLOR"):
        return False
    # 统一使用 stderr 进行检测
    if hasattr(sys.stderr, "isatty"):
        return sys.stderr.isatty()
    return False


def colorize(label: str, text: str, enabled: bool) -> str:
    """为文本添加颜色标签。

    Args:
        label: 标签名称，用于查找对应颜色
        text: 要着色的文本
        enabled: 是否启用着色

    Returns:
        着色后的文本
    """
    if not enabled:
        return f"[{label}] {text}"
    color = ANSI_COLORS.get(label, "")
    if color:
        return f"{color}[{label}] {text}{ANSI_RESET}"
    return f"[{label}] {text}"
