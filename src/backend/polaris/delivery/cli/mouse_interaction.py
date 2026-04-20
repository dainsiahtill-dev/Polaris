"""终端鼠标交互模块

提供终端鼠标事件支持，用于实现可点击的折叠区域。

支持：
- 启用/禁用鼠标追踪模式
- 鼠标点击检测
- 可点击区域定义
- 与 Windows 和 Unix 终端兼容

Usage:
    from polaris.delivery.cli.mouse_interaction import MouseTracker, ClickableRegion

    tracker = MouseTracker()
    tracker.enable_mouse()

    # 检测点击
    event = tracker.read_mouse_event()
    if event and event.button == MouseButton.LEFT:
        if tracker.is_click_in_region(event.x, event.y, my_region):
            # 处理点击
            pass
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

import logging

logger = logging.getLogger(__name__)


class MouseButton(Enum):
    """鼠标按钮"""

    LEFT = 0
    MIDDLE = 1
    RIGHT = 2
    RELEASE = 3
    WHEEL_UP = 4
    WHEEL_DOWN = 5


@dataclass
class MouseEvent:
    """鼠标事件"""

    button: MouseButton
    x: int  # 列 (1-indexed)
    y: int  # 行 (1-indexed)
    ctrl: bool = False
    shift: bool = False
    alt: bool = False


@dataclass
class ClickableRegion:
    """可点击区域"""

    id: str
    x_start: int  # 起始列 (1-indexed)
    x_end: int  # 结束列 (inclusive, 1-indexed)
    y_start: int  # 起始行 (1-indexed)
    y_end: int  # 结束行 (inclusive, 1-indexed)
    on_click: Callable[[], None] | None = None

    def contains(self, x: int, y: int) -> bool:
        """检查坐标是否在区域内"""
        return self.x_start <= x <= self.x_end and self.y_start <= y <= self.y_end


# Windows 平台使用 msvcrt, Unix 平台使用 termios/tty
_is_windows: bool = sys.platform == "win32"
_has_termios: bool = False
_has_select: bool = False

if not _is_windows:
    try:
        import termios as _termios_module  # type: ignore[attr-defined,import-not-found]
        import tty as _tty_module  # type: ignore[attr-defined,import-not-found]

        _has_termios = True
    except ImportError:
        _has_termios = False

try:
    import select as _select_module  # type: ignore[attr-defined,import-not-found]

    _has_select = True
except ImportError:
    _has_select = False

# Windows 特定的导入
if _is_windows:
    try:
        import msvcrt as _msvcrt_module  # type: ignore[attr-defined,import-not-found]
    except ImportError:
        _msvcrt_module = None  # type: ignore[assignment]


class MouseTracker:
    """鼠标追踪器

    用于检测终端中的鼠标点击事件。

    通过 ANSI 转义序列启用鼠标追踪模式。
    支持常见的终端模拟器（xterm, Windows Terminal, iTerm2 等）。

    Example:
        >>> tracker = MouseTracker()
        >>> tracker.enable_mouse()
        >>> print("Click anywhere...")
        >>> event = tracker.read_mouse_event()
        >>> if event:
        ...     print(f"Clicked at row={event.y}, col={event.x}")
    """

    # ANSI 转义序列
    _ENABLE_MOUSE = "\x1b[?1000h\x1b[?1002h\x1b[?1015h\x1b[?1006h"
    _DISABLE_MOUSE = "\x1b[?1000l\x1b[?1002l\x1b[?1015l\x1b[?1006l"
    _MOUSE_PREFIX = "\x1b[<"
    _MOUSE_SUFFIX = "M"

    def __init__(self) -> None:
        self._enabled = False
        self._regions: list[ClickableRegion] = []
        self._saved_terminal_settings: tuple | None = None

    def enable_mouse(self) -> bool:
        """启用鼠标追踪

        发送 ANSI 转义序列启用鼠标追踪模式。

        Returns:
            是否成功启用
        """
        if self._enabled:
            return True

        # 检查是否是 TTY
        if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
            return False

        try:
            # Unix 平台：保存终端设置并设置为 cbreak 模式
            if _has_termios:
                # 使用全局变量，避免未定义变量的类型错误
                import builtins

                termios = builtins.__dict__.get("_termios_module") or __import__("termios")
                tty = builtins.__dict__.get("_tty_module") or __import__("tty")
                # 保存终端设置
                self._saved_terminal_settings = termios.tcgetattr(sys.stdin)  # type: ignore[arg-type]

                # 设置终端为 cbreak 模式（按键可用，但保留 Ctrl+C 等）
                tty.setcbreak(sys.stdin.fileno())  # type: ignore[arg-type]

            # 输出启用序列
            sys.stdout.write(self._ENABLE_MOUSE)
            sys.stdout.flush()

            self._enabled = True
            return True
        except (RuntimeError, ValueError):
            return False

    def disable_mouse(self) -> None:
        """禁用鼠标追踪"""
        if not self._enabled:
            return

        try:
            # 输出禁用序列
            sys.stdout.write(self._DISABLE_MOUSE)
            sys.stdout.flush()

            # 恢复终端设置 (仅 Unix)
            if self._saved_terminal_settings is not None and _has_termios:
                import builtins

                termios = builtins.__dict__.get("_termios_module") or __import__("termios")
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._saved_terminal_settings)  # type: ignore[arg-type]

            self._enabled = False
            self._saved_terminal_settings = None
        except (RuntimeError, ValueError) as e:
            logger.debug("Failed to disable mouse tracking: %s", e)

    def add_region(self, region: ClickableRegion) -> None:
        """添加可点击区域"""
        self._regions.append(region)

    def remove_region(self, region_id: str) -> None:
        """移除可点击区域"""
        self._regions = [r for r in self._regions if r.id != region_id]

    def clear_regions(self) -> None:
        """清空所有可点击区域"""
        self._regions.clear()

    def find_region_at(self, x: int, y: int) -> ClickableRegion | None:
        """查找指定坐标处的区域"""
        for region in reversed(self._regions):  # 从后往前，后添加的优先
            if region.contains(x, y):
                return region
        return None

    def _parse_sgr_mouse(self, data: str) -> MouseEvent | None:
        """解析 SGR 扩展格式鼠标事件

        格式: CSI < Pb ; Px ; Py M
        - CSI = \x1b[
        - Pb = 按钮编码
        - Px = X 坐标
        - Py = Y 坐标
        - M = 事件类型
        """
        if not data.endswith("M"):
            return None

        # 去掉末尾的 M
        data = data[:-1]

        # 分割坐标
        parts = data.split(";")
        if len(parts) < 3:
            return None

        try:
            button_code = int(parts[0])
            x = int(parts[1])
            y = int(parts[2])
        except ValueError:
            return None

        # 解析按钮
        ctrl = (button_code & 16) != 0
        shift = (button_code & 4) != 0
        alt = (button_code & 8) != 0
        button = button_code & 3

        # 按钮码转换
        if button == 0:
            actual_button = MouseButton.LEFT
        elif button == 1:
            actual_button = MouseButton.MIDDLE
        elif button == 2:
            actual_button = MouseButton.RIGHT
        elif button == 3:
            actual_button = MouseButton.RELEASE
        elif button == 64:
            actual_button = MouseButton.WHEEL_UP
        elif button == 65:
            actual_button = MouseButton.WHEEL_DOWN
        else:
            return None

        return MouseEvent(
            button=actual_button,
            x=x,
            y=y,
            ctrl=ctrl,
            shift=shift,
            alt=alt,
        )

    def read_mouse_event(self, timeout_ms: int = 0) -> MouseEvent | None:
        """读取鼠标事件（非阻塞）

        Args:
            timeout_ms: 超时毫秒数，0 表示非阻塞

        Returns:
            鼠标事件，如果超时或无效则返回 None
        """
        if not self._enabled:
            return None

        # 检查是否有数据可用
        if _has_select:
            readable, _, _ = _select_module.select([sys.stdin], [], [], timeout_ms / 1000.0)  # type: ignore[arg-type]
            if not readable:
                return None
        elif _is_windows and _msvcrt_module is not None:
            # Windows 平台使用 msvcrt
            if not _msvcrt_module.kbhit():  # type: ignore[union-attr]
                return None
        else:
            return None

        # 读取数据 - 统一使用字符串类型
        data = ""
        try:
            if _is_windows and _msvcrt_module is not None:
                # Windows: 使用 msvcrt
                while True:
                    raw_char = _msvcrt_module.getch()  # type: ignore[union-attr]
                    char: str
                    if isinstance(raw_char, bytes):
                        char = raw_char.decode("latin-1", errors="replace")
                    else:
                        char = chr(raw_char)
                    data += char
                    if data.endswith("M") or data.endswith("m"):
                        break
                    if len(data) > 20:
                        break
            # Unix: 使用 select + read
            elif hasattr(sys.stdin, "read"):
                first_char: str = sys.stdin.read(1)  # type: ignore[union-attr]
                if first_char != "\x1b":
                    # 非转义字符，可能不是鼠标事件
                    return None

                # 读取剩余部分
                remaining = ""
                while True:
                    if _has_select:
                        readable, _, _ = _select_module.select([sys.stdin], [], [], 0.05)  # type: ignore[arg-type]
                        if not readable:
                            break
                    next_char = sys.stdin.read(1)  # type: ignore[union-attr]
                    remaining += next_char
                    if next_char in ("M", "m", "t"):
                        break

                data = first_char + remaining

        except (RuntimeError, ValueError):
            return None

        # 解析鼠标事件
        if data.startswith(self._MOUSE_PREFIX) and data.endswith("M"):
            return self._parse_sgr_mouse(data[2:])  # 去掉 CSI [

        return None

    def wait_for_click(self) -> MouseEvent | None:
        """等待鼠标点击"""
        while True:
            event = self.read_mouse_event(timeout_ms=100)
            if event and event.button == MouseButton.LEFT:
                return event

    @property
    def is_enabled(self) -> bool:
        """是否已启用鼠标追踪"""
        return self._enabled


class MouseAwareRenderer:
    """支持鼠标的渲染器

    混入此类以获得鼠标点击支持。
    """

    def __init__(self) -> None:
        self._mouse_tracker = MouseTracker()
        self._current_line = 0  # 当前输出行

    def _start_mouse(self) -> bool:
        """启用鼠标支持"""
        return self._mouse_tracker.enable_mouse()

    def _stop_mouse(self) -> None:
        """禁用鼠标支持"""
        self._mouse_tracker.disable_mouse()

    def _make_clickable_region(
        self,
        region_id: str,
        on_click: Callable[[], None],
        lines: int = 1,
    ) -> ClickableRegion:
        """创建可点击区域（最后一行）

        Args:
            region_id: 区域 ID
            on_click: 点击回调
            lines: 跨越的行数

        Returns:
            ClickableRegion
        """
        start_line = self._current_line - lines + 1
        return ClickableRegion(
            id=region_id,
            x_start=1,
            x_end=200,  # 足够宽
            y_start=start_line,
            y_end=self._current_line,
            on_click=on_click,
        )

    def _increment_line(self) -> None:
        """增加行计数器"""
        self._current_line += 1


def enable_mouse_mode() -> bool:
    """全局启用鼠标模式

    Returns:
        是否成功启用
    """
    tracker = MouseTracker()
    return tracker.enable_mouse()


def disable_mouse_mode() -> None:
    """全局禁用鼠标模式"""
    tracker = MouseTracker()
    tracker.disable_mouse()
