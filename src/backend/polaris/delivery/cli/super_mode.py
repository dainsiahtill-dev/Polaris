from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

SUPER_ROLE = "super"

_CODE_ACTION_KEYWORDS = (
    "修复",
    "完善",
    "修改",
    "实现",
    "开发",
    "重构",
    "优化",
    "fix",
    "implement",
    "improve",
    "refactor",
    "build",
)
_CODE_TARGET_KEYWORDS = (
    "代码",
    "文件",
    "模块",
    "函数",
    "类",
    "接口",
    "bug",
    "code",
    "file",
    "module",
    "function",
    "class",
    ".py",
    ".ts",
    ".js",
)
_CODE_ARTIFACT_HINTS = (
    "orchestrator",
    "controller",
    "service",
    "runtime",
    "session",
    "pipeline",
    "adapter",
    "handler",
    "kernel",
    "cli",
)
_ARCHITECT_KEYWORDS = (
    "架构",
    "蓝图",
    "设计",
    "方案",
    "adr",
    "architecture",
    "design",
)
_CHIEF_ENGINEER_KEYWORDS = (
    "根因",
    "分析",
    "审查",
    "评审",
    "排查",
    "review",
    "troubleshoot",
    "root cause",
)
_QA_KEYWORDS = (
    "测试",
    "验证",
    "回归",
    "验收",
    "qa",
    "test",
    "verify",
    "validation",
)
_PM_KEYWORDS = (
    "计划",
    "规划",
    "拆分",
    "排期",
    "roadmap",
    "plan",
)


@dataclass(frozen=True, slots=True)
class SuperRouteDecision:
    roles: tuple[str, ...]
    reason: str
    fallback_role: str


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _has_code_artifact_hint(text: str) -> bool:
    if _contains_any(text, _CODE_TARGET_KEYWORDS):
        return True
    if _contains_any(text, _CODE_ARTIFACT_HINTS):
        return True
    return bool(re.search(r"[a-zA-Z_][\w/.-]*\.(py|ts|js|jsx|tsx|java|go|rs|cpp|c|h|yaml|yml|json|md)\b", text))


def build_super_readonly_message(*, role: str, original_request: str) -> str:
    clean_request = str(original_request or "").strip()
    clean_role = str(role or "").strip() or "unknown"
    return (
        "[mode:analyze]\n"
        "[SUPER_MODE_READONLY_STAGE]\n"
        f"stage_role: {clean_role}\n"
        "stage_type: readonly_planning\n\n"
        "instructions:\n"
        "- This stage is read-only.\n"
        "- Do not attempt to satisfy a write contract in this stage.\n"
        "- Use only tools exposed to your current role.\n"
        "- Produce role-appropriate planning or analysis output for the next stage or the user.\n"
        "- IMPORTANT: End your response with a structured TASK_LIST in JSON format.\n\n"
        "structured_output_format:\n"
        "```json\n"
        "{\n"
        '  "tasks": [\n'
        "    {\n"
        '      "subject": "concise task title",\n'
        '      "description": "detailed implementation steps",\n'
        '      "target_files": ["path/to/file.py"],\n'
        '      "estimated_hours": 0.5\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "```\n\n"
        f"original_user_request:\n{clean_request}\n"
        "[/SUPER_MODE_READONLY_STAGE]"
    )


class SuperModeRouter:
    """Deterministic intent router for CLI SUPER mode."""

    def decide(self, message: str, *, fallback_role: str) -> SuperRouteDecision:
        text = str(message or "").strip().lower()

        if _contains_any(text, _ARCHITECT_KEYWORDS):
            return SuperRouteDecision(
                roles=("architect",),
                reason="architecture_design",
                fallback_role=fallback_role,
            )

        code_delivery = _contains_any(text, _CODE_ACTION_KEYWORDS) and _has_code_artifact_hint(text)
        if code_delivery:
            return SuperRouteDecision(
                roles=("pm", "director"),
                reason="code_delivery",
                fallback_role=fallback_role,
            )
        if _contains_any(text, _CHIEF_ENGINEER_KEYWORDS):
            return SuperRouteDecision(
                roles=("chief_engineer",),
                reason="technical_analysis",
                fallback_role=fallback_role,
            )
        if _contains_any(text, _QA_KEYWORDS):
            return SuperRouteDecision(
                roles=("qa",),
                reason="qa_validation",
                fallback_role=fallback_role,
            )
        if _contains_any(text, _PM_KEYWORDS):
            return SuperRouteDecision(
                roles=("pm",),
                reason="planning",
                fallback_role=fallback_role,
            )
        return SuperRouteDecision(
            roles=(fallback_role,),
            reason="fallback",
            fallback_role=fallback_role,
        )


def build_director_handoff_message(*, original_request: str, pm_output: str) -> str:
    clean_request = str(original_request or "").strip()
    clean_pm_output = str(pm_output or "").strip() or "(pm produced no textual plan)"
    return (
        "[mode:materialize]\n"
        "[SUPER_MODE_HANDOFF]\n"
        f"original_user_request:\n{clean_request}\n\n"
        "planning_role: pm\n"
        "execution_role: director\n\n"
        "instructions:\n"
        "- You are receiving a PM-generated execution plan.\n"
        "- Execute against the plan instead of restarting high-level planning.\n"
        "- If the plan still lacks detail, do only the minimum additional inspection required to execute.\n\n"
        f"pm_plan:\n{clean_pm_output}\n"
        "[/SUPER_MODE_HANDOFF]"
    )


@dataclass(frozen=True, slots=True)
class SuperTaskItem:
    """Structured task extracted from PM output."""

    subject: str
    description: str
    target_files: tuple[str, ...]
    estimated_hours: float


def extract_task_list_from_pm_output(pm_output: str) -> list[SuperTaskItem]:
    """Extract structured task list from PM's JSON output.

    Looks for ```json blocks containing a 'tasks' array, or inline JSON.
    Returns empty list if no valid task structure found.
    """
    text = str(pm_output or "").strip()
    if not text:
        return []

    # Try fenced json block first
    fenced_match = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", text)
    if fenced_match:
        try:
            data = json.loads(fenced_match.group(1))
            return _parse_task_data(data)
        except json.JSONDecodeError:
            logger.debug("extract_task_list: fenced JSON parse failed")

    # Try inline JSON object with tasks key (balanced bracket matching)
    tasks_start = text.find('"tasks"')
    if tasks_start == -1:
        tasks_start = text.find("'tasks'")
    if tasks_start != -1:
        colon_idx = text.find(":", tasks_start)
        if colon_idx != -1:
            bracket_start = text.find("[", colon_idx)
            if bracket_start != -1:
                depth = 0
                bracket_end = -1
                for i, ch in enumerate(text[bracket_start:], start=bracket_start):
                    if ch == "[":
                        depth += 1
                    elif ch == "]":
                        depth -= 1
                        if depth == 0:
                            bracket_end = i + 1
                            break
                if bracket_end != -1:
                    try:
                        data = {"tasks": json.loads(text[bracket_start:bracket_end])}
                        return _parse_task_data(data)
                    except json.JSONDecodeError:
                        logger.debug("extract_task_list: inline JSON parse failed")

    # Fallback: try to find any JSON object in the last 2KB of output
    last_chunk = text[-2048:]
    brace_match = re.search(r"(\{[\s\S]*\"tasks\"[\s\S]*\})", last_chunk)
    if brace_match:
        try:
            data = json.loads(brace_match.group(1))
            return _parse_task_data(data)
        except json.JSONDecodeError:
            logger.debug("extract_task_list: fallback JSON parse failed")

    return []


def _parse_task_data(data: dict[str, Any]) -> list[SuperTaskItem]:
    """Parse task list from decoded JSON data."""
    items: list[SuperTaskItem] = []
    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list):
        return items
    for task in raw_tasks:
        if not isinstance(task, dict):
            continue
        subject = str(task.get("subject", "")).strip()
        if not subject:
            continue
        description = str(task.get("description", "")).strip()
        target_files = task.get("target_files", [])
        if isinstance(target_files, str):
            target_files = [target_files]
        elif not isinstance(target_files, list):
            target_files = []
        estimated = float(task.get("estimated_hours", 0.0) or 0.0)
        items.append(
            SuperTaskItem(
                subject=subject,
                description=description,
                target_files=tuple(str(f).strip() for f in target_files if str(f).strip()),
                estimated_hours=estimated,
            )
        )
    logger.info("extract_task_list: parsed %d tasks from PM output", len(items))
    return items


__all__ = [
    "SUPER_ROLE",
    "SuperModeRouter",
    "SuperRouteDecision",
    "SuperTaskItem",
    "build_director_handoff_message",
    "build_super_readonly_message",
    "extract_task_list_from_pm_output",
]
