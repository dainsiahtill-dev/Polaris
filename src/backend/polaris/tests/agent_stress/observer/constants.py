"""Observer 模块常量定义。

纯常量定义，无外部依赖（除标准库外）。
"""

from __future__ import annotations

import logging
import os
import subprocess

from tests.agent_stress.paths import BACKEND_ROOT, ensure_backend_root_on_syspath

logger = logging.getLogger("observer.projection")

ensure_backend_root_on_syspath()

# 路径常量
PROJECT_ROOT = BACKEND_ROOT
BACKEND_DIR = BACKEND_ROOT

# 平台检测
IS_WINDOWS = os.name == "nt"
CREATE_NEW_CONSOLE_FLAG = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)

# 日志前缀
STEP_PREFIX = "## Step "
ROUND_HEADER_PREFIX = "压测轮次 #"
ROUND_RESULT_PREFIX = "[Result] Round #"
DEFAULT_LOG_NAME = "human_observer.log"

# 投影显示配置
PROJECTION_REASONING_VIEWPORT_LINES = 16
PROJECTION_REASONING_LINE_MAX_CHARS = 220
PROJECTION_EVENT_LINE_MAX_CHARS = 160

# 角色徽章
PROJECTION_ROLE_BADGES: dict[str, str] = {
    "pm": "🧭",
    "architect": "🏛",
    "chief_engineer": "🧪",
    "director": "⚙",
    "qa": "🛡",
    "system": "🖥",
}

# 事件徽章
PROJECTION_EVENT_BADGES: dict[str, str] = {
    "thinking_chunk": "💭",
    "thinking_preview": "🧠",
    "content_chunk": "📝",
    "content_preview": "📄",
    "llm_waiting": "⏳",
    "llm_completed": "✅",
    "llm_failed": "❌",
    "tool_call": "🛠",
    "tool_result": "✅",
    "tool_summary": "🧰",
    "tool_failure": "❌",
    "turn_badge": "🏁",
    "error": "⚠",
    "llm": "🤖",
}

# 事件标签
PROJECTION_EVENT_LABELS: dict[str, str] = {
    "thinking_chunk": "思考中",
    "thinking_preview": "思考摘要",
    "content_chunk": "回答生成",
    "content_preview": "回答预览",
    "llm_waiting": "请求中",
    "llm_completed": "已完成",
    "llm_failed": "失败",
    "tool_call": "工具调用",
    "tool_result": "工具结果",
    "tool_summary": "工具汇总",
    "tool_failure": "工具失败",
    "turn_badge": "回合标记",
    "error": "异常",
    "llm": "消息",
}

# 事件样式
PROJECTION_EVENT_STYLES: dict[str, str] = {
    "thinking_chunk": "bold yellow",
    "thinking_preview": "yellow",
    "content_chunk": "cyan",
    "content_preview": "bright_cyan",
    "llm_waiting": "bold yellow",
    "llm_completed": "green",
    "llm_failed": "bold red",
    "tool_call": "bold green",
    "tool_result": "green",
    "tool_summary": "green",
    "tool_failure": "bold red",
    "turn_badge": "bright_black",
    "error": "bold red",
    "llm": "white",
}

# 角色显示配置
ROLE_DISPLAY: dict[str, dict[str, str]] = {
    "pm": {"icon": "🧭", "label": "PM", "color": "cyan"},
    "architect": {"icon": "🏛", "label": "Architect", "color": "magenta"},
    "chief_engineer": {"icon": "🧪", "label": "Chief Engineer", "color": "blue"},
    "director": {"icon": "⚙", "label": "Director", "color": "green"},
    "qa": {"icon": "🛡", "label": "QA", "color": "red"},
}
