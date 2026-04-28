"""tests.agent_stress 的人类观测终端。

纯终端模式，不依赖 Electron。通过 Rich Live 渲染
tests.agent_stress.runner 的实时输出，并支持在 Windows
中新开控制台窗口运行观测器。

【重构说明】
此文件现在是向后兼容的入口点。实际实现已迁移到 observer/ 包中：
- observer/constants.py: 常量定义
- observer/renderers.py: 渲染样式辅助函数
- observer/projection.py: RuntimeProjection 类
- observer/state.py: ObserverState 类
- observer/cli.py: CLI 辅助函数
- observer/fastapi_entrypoint.py: 主入口和运行逻辑
"""

from __future__ import annotations

# 从新包导入所有公开接口，保持完全向后兼容
from .observer import (
    # 常量
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
    ObserverState,
    # 核心类
    RuntimeProjection,
    # CLI
    _build_observer_command,
    _build_runner_command,
    _clone_namespace,
    # 渲染辅助
    _event_badge,
    _event_label,
    # 主入口
    _extract_runtime_root_from_settings_line,
    _extract_workspace_from_settings_line,
    _format_role_status,
    _get_reasoning_border_style,
    _map_taskboard_status_label,
    _reasoning_event_style,
    _redact_command_for_log,
    _resolve_observer_output_dir,
    _role_badge,
    _run_observer,
    _runtime_event_visual,
    _spawn_new_console,
    build_observer_parser,
    logger,
    main,
)

# 保持向后兼容的导出
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
    "_extract_runtime_root_from_settings_line",
    "_extract_workspace_from_settings_line",
    "_resolve_observer_output_dir",
    "_run_observer",
    "_spawn_new_console",
    "build_observer_parser",
    "main",
]

if __name__ == "__main__":
    main()
