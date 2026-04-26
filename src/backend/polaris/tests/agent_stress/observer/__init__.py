"""Observer 模块 - 人类观测终端。

重构后的包结构：
- constants: 常量定义
- renderers: 渲染样式辅助函数
- projection: 实时投影系统 (RuntimeProjection)
- state: 观测器状态 (ObserverState)
- cli: CLI 辅助函数
- main: 主入口
"""

from __future__ import annotations

import asyncio

# CLI 和主入口
from .cli import (
    _build_observer_command,
    _build_runner_command,
    _clone_namespace,
    _redact_command_for_log,
)

# 常量
from .constants import (
    BACKEND_DIR,
    CREATE_NEW_CONSOLE_FLAG,
    DEFAULT_LOG_NAME,
    IS_WINDOWS,
    PROJECT_ROOT,
    PROJECTION_EVENT_BADGES,
    PROJECTION_EVENT_LABELS,
    PROJECTION_EVENT_LINE_MAX_CHARS,
    PROJECTION_EVENT_STYLES,
    PROJECTION_REASONING_LINE_MAX_CHARS,
    PROJECTION_REASONING_VIEWPORT_LINES,
    PROJECTION_ROLE_BADGES,
    ROLE_DISPLAY,
    ROUND_HEADER_PREFIX,
    ROUND_RESULT_PREFIX,
    STEP_PREFIX,
    logger,
)
from .main import (
    _extract_workspace_from_settings_line,
    _resolve_observer_output_dir,
    _run_observer,
    _should_spawn_new_console_window,
    _spawn_new_console,
    build_observer_parser,
    main,
)

# 投影系统
from .projection import RuntimeProjection

# 渲染辅助
from .renderers import (
    _event_badge,
    _event_label,
    _format_role_status,
    _get_reasoning_border_style,
    _map_taskboard_status_label,
    _reasoning_event_style,
    _role_badge,
    _runtime_event_visual,
)

# 状态管理
from .state import ObserverState


async def observe_runner(args, spawn_window: bool = False) -> int:
    """兼容 runner.py 的观测入口。"""
    if spawn_window and _should_spawn_new_console_window():
        return int(await asyncio.to_thread(_spawn_new_console, args))
    return int(await _run_observer(args))


__all__ = [
    # 常量
    "BACKEND_DIR",
    "CREATE_NEW_CONSOLE_FLAG",
    "DEFAULT_LOG_NAME",
    "IS_WINDOWS",
    "PROJECT_ROOT",
    "PROJECTION_EVENT_BADGES",
    "PROJECTION_EVENT_LABELS",
    "PROJECTION_EVENT_LINE_MAX_CHARS",
    "PROJECTION_EVENT_STYLES",
    "PROJECTION_REASONING_LINE_MAX_CHARS",
    "PROJECTION_REASONING_VIEWPORT_LINES",
    "PROJECTION_ROLE_BADGES",
    "ROLE_DISPLAY",
    "ROUND_HEADER_PREFIX",
    "ROUND_RESULT_PREFIX",
    "STEP_PREFIX",
    "logger",
    # 渲染辅助
    "_event_badge",
    "_event_label",
    "_format_role_status",
    "_get_reasoning_border_style",
    "_map_taskboard_status_label",
    "_reasoning_event_style",
    "_role_badge",
    "_runtime_event_visual",
    # 核心类
    "RuntimeProjection",
    "ObserverState",
    # CLI
    "_build_observer_command",
    "_build_runner_command",
    "_clone_namespace",
    "_redact_command_for_log",
    # 主入口
    "_extract_workspace_from_settings_line",
    "_resolve_observer_output_dir",
    "_run_observer",
    "_should_spawn_new_console_window",
    "_spawn_new_console",
    "build_observer_parser",
    "main",
    "observe_runner",
]
