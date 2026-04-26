"""渲染辅助函数和样式配置。

提供各种渲染样式相关的工具函数。
"""

from __future__ import annotations

from .constants import (
    PROJECTION_EVENT_BADGES,
    PROJECTION_EVENT_LABELS,
    PROJECTION_EVENT_STYLES,
    PROJECTION_ROLE_BADGES,
    ROLE_DISPLAY,
)


def _get_reasoning_border_style(event_type: str) -> str:
    """根据事件类型返回对应的边框颜色。"""
    styles = {
        "llm_waiting": "bold yellow",
        "llm_completed": "bold green",
        "llm_failed": "bold red",
        "tool_failure": "bold red",
        "error": "bold red",
        "tool_call": "bold green",
        "tool_result": "green",
        "tool_summary": "green",
        "thinking_chunk": "yellow",
        "thinking_preview": "yellow",
        "content_chunk": "cyan",
        "content_preview": "bright_cyan",
        "turn_badge": "bright_black",
    }
    return styles.get(str(event_type or "").strip().lower(), "cyan")


def _role_badge(role: str) -> str:
    """获取角色徽章。"""
    return PROJECTION_ROLE_BADGES.get(str(role or "").strip().lower(), "🤖")


def _event_badge(event_type: str) -> str:
    """获取事件徽章。"""
    return PROJECTION_EVENT_BADGES.get(str(event_type or "").strip().lower(), "🤖")


def _event_label(event_type: str) -> str:
    """获取事件标签。"""
    return PROJECTION_EVENT_LABELS.get(str(event_type or "").strip().lower(), "消息")


def _reasoning_event_style(event_type: str, *, is_continuation: bool = False) -> str:
    """获取推理事件样式。"""
    if is_continuation:
        return "white"
    return PROJECTION_EVENT_STYLES.get(str(event_type or "").strip().lower(), "white")


def _format_role_status(role: str, state_token: str, mode: str) -> str:
    """格式化单个角色状态为人类友好格式。"""
    role_config = ROLE_DISPLAY.get(role, {"icon": "◆", "label": role.upper(), "color": "white"})
    icon = role_config["icon"]
    label = role_config["label"]

    # 状态指示器
    if state_token == "IDLE":
        state_indicator = "○"
        state_label = "空闲"
    elif state_token in ("RUN", "RUNNING"):
        state_indicator = "●"
        state_label = "运行"
    else:
        state_indicator = "◐"
        state_label = state_token

    # 构建显示文本
    if mode:
        return f"{icon} {label}: {state_indicator} {state_label} [{mode}]"
    return f"{icon} {label}: {state_indicator} {state_label}"


def _map_taskboard_status_label(
    status: str,
    qa_state: str = "",
    resume_state: str = "",
) -> str:
    """将任务板状态映射为人类可读的标签。"""
    normalized = str(status or "").strip().lower()
    qa = str(qa_state or "").strip().lower()
    resume = str(resume_state or "").strip().lower()
    if normalized in {"ready", "pending", "queued", "todo"}:
        if resume in {"resumable", "suspended", "expired"}:
            return "待恢复"
        if qa == "rework":
            return "未开始（QA打回）"
        return "未开始"
    if normalized in {"in_progress", "running", "claimed", "executing"}:
        if resume == "resumed":
            return "恢复执行中"
        if qa == "rework":
            return "执行中（QA打回）"
        return "执行中"
    if normalized in {"completed", "done", "success"}:
        if qa == "pending":
            return "已完成，等待QA验证"
        if qa == "failed":
            return "已完成，QA未通过"
        if qa == "passed":
            return "已完成"
        return "已完成"
    if normalized in {"failed", "error", "cancelled", "canceled", "timeout", "timed_out"}:
        if qa == "exhausted":
            return "失败（重试耗尽）"
        return "失败"
    if normalized in {"blocked", "stalled"}:
        return "阻塞"
    if qa == "rework":
        return "未开始（QA打回）"
    return "未开始"


def _format_taskboard_execution_backend_label(
    execution_backend: str,
    projection_scenario: str = "",
) -> str:
    """将执行后端映射为紧凑的人类可读标签。"""
    backend = str(execution_backend or "").strip().lower()
    scenario = str(projection_scenario or "").strip().lower()
    if not backend:
        return ""

    if backend == "projection_generate":
        if scenario:
            return f"投影生成:{scenario}"
        return "投影生成"
    if backend == "projection_refresh_mapping":
        return "回映刷新"
    if backend == "projection_reproject":
        if scenario:
            return f"重投影:{scenario}"
        return "重投影"
    if backend == "code_edit":
        return "代码编辑"
    if scenario:
        return f"{backend}:{scenario}"
    return backend


def _runtime_event_visual(kind: str) -> tuple[str, str, str]:
    """运行时事件视觉元素。"""
    token = str(kind or "").strip().lower()
    if token.startswith("role_event:"):
        role = token.split(":", 1)[1].strip().upper() or "ROLE"
        return "👥", f"{role} 角色事件", "yellow"
    if token.startswith("factory:"):
        stage = token.split(":", 1)[1].replace("_", " ").strip() or "event"
        return "🏭", f"Factory {stage}", "bright_blue"
    if token == "dialogue":
        return "💬", "对话", "bright_blue"
    if token == "file_edit":
        return "🧩", "文件变更", "cyan"
    if token == "task_trace":
        return "🛰", "任务跟踪", "yellow"
    if token == "local_execute_start":
        return "🚀", "执行启动", "green"
    if token in {"runtime_event", "process_stream"}:
        return "📡", "运行流", "white"
    label = token or "event"
    return "📌", label, "white"
