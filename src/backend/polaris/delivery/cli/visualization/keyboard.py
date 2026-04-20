"""键盘快捷键模块

定义折叠/展开相关的快捷键，避免与终端控制字符冲突。

终端控制字符冲突说明:
- Ctrl+C: SIGINT (中断)
- Ctrl+D: EOF
- Ctrl+Z: SIGTSTP (挂起)
- Ctrl+\\: SIGQUIT

解决方案: 使用 Alt 键组合或自定义序列

Example:
    >>> from polaris.delivery.cli.visualization.keyboard import FoldShortcut
    >>> FoldShortcut.EXPAND_ALL_DEBUG.value
    'alt+d'
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FoldShortcut(Enum):
    """折叠相关快捷键定义

    所有快捷键都避免使用终端控制字符。
    使用 Alt 键组合作为主要方案。
    """

    # ============= 按类型折叠/展开 =============

    # DEBUG 类型操作 (使用 Alt+D)
    EXPAND_ALL_DEBUG = "alt+d"
    COLLAPSE_ALL_DEBUG = "alt+shift+d"

    # THINKING 类型操作 (使用 Alt+T)
    EXPAND_ALL_THINKING = "alt+t"
    COLLAPSE_ALL_THINKING = "alt+shift+t"

    # TOOL 类型操作 (使用 Alt+O)
    EXPAND_ALL_TOOL = "alt+o"
    COLLAPSE_ALL_TOOL = "alt+shift+o"

    # ============= 通用操作 =============

    # 切换当前项 (Space)
    TOGGLE_CURRENT = "space"

    # 展开/折叠全部 (使用 Ctrl+Alt 组合，避免冲突)
    EXPAND_ALL = "ctrl+alt+["
    COLLAPSE_ALL = "ctrl+alt+]"

    # 永久展开当前项
    PERMANENT_EXPAND = "ctrl+enter"

    # ============= 层级导航 =============

    # 折叠到指定层级 (1-9)
    FOLD_TO_LEVEL_1 = "1"
    FOLD_TO_LEVEL_2 = "2"
    FOLD_TO_LEVEL_3 = "3"
    FOLD_TO_LEVEL_4 = "4"
    FOLD_TO_LEVEL_5 = "5"
    FOLD_TO_LEVEL_6 = "6"
    FOLD_TO_LEVEL_7 = "7"
    FOLD_TO_LEVEL_8 = "8"
    FOLD_TO_LEVEL_9 = "9"
    FOLD_TO_LEVEL_ALL = "0"  # 展开所有

    # ============= 搜索/过滤 =============

    # 显示/隐藏 DEBUG
    TOGGLE_DEBUG_VISIBLE = "alt+shift+b"

    # 显示/隐藏 THINKING
    TOGGLE_THINKING_VISIBLE = "alt+shift+t"

    # 搜索
    SEARCH = "ctrl+f"


@dataclass
class KeyboardShortcutConfig:
    """键盘快捷键配置

    用于运行时快捷键绑定和配置。
    """

    # 是否启用快捷键
    enabled: bool = True

    # 是否启用 Alt 组合键 (某些终端不支持)
    alt_enabled: bool = True

    # 是否启用 Ctrl+Alt 组合键
    ctrl_alt_enabled: bool = True

    # 自定义快捷键映射 (覆盖默认)
    custom_shortcuts: dict[FoldShortcut, str] | None = None

    @classmethod
    def default(cls) -> KeyboardShortcutConfig:
        """获取默认配置"""
        return cls()

    @classmethod
    def minimal(cls) -> KeyboardShortcutConfig:
        """最小化配置 (仅 Space 和 数字键)"""
        return cls(
            alt_enabled=False,
            ctrl_alt_enabled=False,
        )

    def get_shortcut(self, shortcut: FoldShortcut) -> str:
        """获取快捷键字符串

        Args:
            shortcut: 快捷键枚举

        Returns:
            快捷键字符串
        """
        if self.custom_shortcuts and shortcut in self.custom_shortcuts:
            return self.custom_shortcuts[shortcut]
        return shortcut.value

    def is_available(self, shortcut: FoldShortcut) -> bool:
        """检查快捷键是否可用

        Args:
            shortcut: 快捷键枚举

        Returns:
            是否可用
        """
        value = shortcut.value

        if "alt+" in value and not self.alt_enabled:
            return False
        return not ("ctrl+alt+" in value and not self.ctrl_alt_enabled)


# ANSI Escape 序列常量 (用于模拟按键)
class ANSIEscapeSeq:
    """ANSI 转义序列常量"""

    # Alt 键前缀
    ALT_PREFIX = "\x1b["
    ALT_SUFFIX = "~"

    # 常用 Alt 组合
    ALT_D = "\x1b[d"  # Alt+D
    ALT_T = "\x1b[t"  # Alt+T
    ALT_O = "\x1b[o"  # Alt+O

    # Ctrl+Alt 组合
    CTRL_ALT_LBRACKET = "\x1b[27;6]"  # Ctrl+Alt+[
    CTRL_ALT_RBRACKET = "\x1b[27;6]"  # Ctrl+Alt+]

    # 特殊键
    SPACE = " "
    ENTER = "\n"
    ESCAPE = "\x1b"


def parse_escape_sequence(sequence: str) -> FoldShortcut | None:
    """解析转义序列

    Args:
        sequence: 转义序列字符串

    Returns:
        对应的快捷键名称，或 None
    """
    mapping = {
        ANSIEscapeSeq.ALT_D: FoldShortcut.EXPAND_ALL_DEBUG,
        ANSIEscapeSeq.ALT_T: FoldShortcut.EXPAND_ALL_THINKING,
        ANSIEscapeSeq.ALT_O: FoldShortcut.EXPAND_ALL_TOOL,
    }

    return mapping.get(sequence)


# 快捷键冲突检查
TERMINAL_CONTROL_CHARS = {
    "\x03": "Ctrl+C (SIGINT)",
    "\x04": "Ctrl+D (EOF)",
    "\x1a": "Ctrl+Z (SIGTSTP)",
    "\x1c": "Ctrl+\\ (SIGQUIT)",
    "\x1d": "Ctrl+]",
}


def validate_shortcut(shortcut: str) -> tuple[bool, str | None]:
    """验证快捷键是否与终端控制字符冲突

    Args:
        shortcut: 快捷键字符串

    Returns:
        (是否安全, 冲突描述或 None)
    """
    for char, name in TERMINAL_CONTROL_CHARS.items():
        if char in shortcut:
            return False, name

    return True, None


# 导出常用快捷键常量
EXPAND_DEBUG = FoldShortcut.EXPAND_ALL_DEBUG
COLLAPSE_DEBUG = FoldShortcut.COLLAPSE_ALL_DEBUG
EXPAND_THINKING = FoldShortcut.EXPAND_ALL_THINKING
COLLAPSE_THINKING = FoldShortcut.COLLAPSE_ALL_THINKING
TOGGLE_CURRENT = FoldShortcut.TOGGLE_CURRENT
EXPAND_ALL = FoldShortcut.EXPAND_ALL
COLLAPSE_ALL = FoldShortcut.COLLAPSE_ALL
