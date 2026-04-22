from __future__ import annotations

import re
from dataclasses import dataclass

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


__all__ = [
    "SUPER_ROLE",
    "SuperModeRouter",
    "SuperRouteDecision",
    "build_director_handoff_message",
]
