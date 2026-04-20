"""Unified interaction contract planning for roles.kernel.

This module centralizes the runtime decision about:
- high-level turn intent;
- tool calling mode;
- structured output contract usage;
- prompt/runtime guidance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleProfile


class TurnIntent(str, Enum):
    """High-level intent categories used by the prompt/runtime contract."""

    ANALYZE = "analyze"
    PLAN = "plan"
    DESIGN = "design"
    EXECUTE = "execute"
    REVIEW = "review"
    GENERAL = "general"


@dataclass(frozen=True)
class ProviderCapabilities:
    """Provider/model capability snapshot relevant to one turn."""

    supports_native_tools: bool = False
    supports_json_schema: bool = False
    supports_stream_native_tools: bool = False


@dataclass(frozen=True)
class InteractionContract:
    """Single-source plan for one role turn."""

    role_id: str
    intent: TurnIntent
    tool_mode: str
    output_mode: str
    tool_whitelist: tuple[str, ...] = ()
    response_schema_name: str | None = None
    native_tools_enabled: bool = False
    structured_output_enabled: bool = False
    text_tool_fallback_allowed: bool = False
    text_response_fallback_allowed: bool = False
    notes: tuple[str, ...] = ()

    def to_metadata(self) -> dict[str, Any]:
        """Serialize to a stable debug-friendly mapping."""
        return {
            "role_id": self.role_id,
            "intent": self.intent.value,
            "tool_mode": self.tool_mode,
            "output_mode": self.output_mode,
            "tool_whitelist": list(self.tool_whitelist),
            "response_schema_name": self.response_schema_name,
            "native_tools_enabled": self.native_tools_enabled,
            "structured_output_enabled": self.structured_output_enabled,
            "text_tool_fallback_allowed": self.text_tool_fallback_allowed,
            "text_response_fallback_allowed": self.text_response_fallback_allowed,
            "notes": list(self.notes),
        }


_ANALYZE_PATTERNS = (
    r"总结",
    r"阅读",
    r"分析",
    r"解释",
    r"调查",
    r"review",
    r"summarize",
    r"analy[sz]e",
    r"inspect",
    r"understand",
)
_PLAN_PATTERNS = (
    r"规划",
    r"拆解",
    r"计划",
    r"roadmap",
    r"plan",
    r"task",
)
_DESIGN_PATTERNS = (
    r"架构",
    r"设计",
    r"adr",
    r"architecture",
    r"design",
)
_EXECUTE_PATTERNS = (
    r"实现",
    r"落地",
    r"推进",
    r"开工",
    r"开始执行",
    r"修改",
    r"修复",
    r"执行",
    r"补丁",
    r"implement",
    r"edit",
    r"fix",
    r"patch",
    r"refactor",
)
_REVIEW_PATTERNS = (
    r"审查",
    r"验收",
    r"测试",
    r"验证",
    r"qa",
    r"review",
    r"verify",
    r"test",
)


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    token = str(text or "").strip().lower()
    if not token:
        return False
    return any(re.search(pattern, token, re.IGNORECASE) for pattern in patterns)


def infer_turn_intent(
    *,
    role_id: str,
    message: str,
    domain: str = "code",
) -> TurnIntent:
    """Infer the dominant runtime intent for the current turn."""

    role_token = str(role_id or "").strip().lower()
    message_token = str(message or "").strip()
    domain_token = str(domain or "").strip().lower()

    # Execute intent must dominate review when both appear in a single request
    # (e.g. "落地代码并验证"), otherwise runtime drifts into summarize/review mode.
    if _matches_any(message_token, _EXECUTE_PATTERNS):
        return TurnIntent.EXECUTE
    if _matches_any(message_token, _REVIEW_PATTERNS):
        return TurnIntent.REVIEW
    if _matches_any(message_token, _DESIGN_PATTERNS):
        return TurnIntent.DESIGN
    if _matches_any(message_token, _PLAN_PATTERNS):
        return TurnIntent.PLAN
    if _matches_any(message_token, _ANALYZE_PATTERNS):
        return TurnIntent.ANALYZE

    if role_token == "director":
        return TurnIntent.EXECUTE
    if role_token == "architect":
        return TurnIntent.DESIGN
    if role_token == "qa":
        return TurnIntent.REVIEW
    if role_token == "pm":
        return TurnIntent.PLAN
    if role_token == "chief_engineer":
        return TurnIntent.DESIGN
    if domain_token == "research":
        return TurnIntent.ANALYZE
    return TurnIntent.GENERAL


def build_interaction_contract(
    *,
    profile: RoleProfile,
    message: str,
    domain: str = "code",
    stream: bool = False,
    response_model: type | None = None,
    capabilities: ProviderCapabilities | None = None,
) -> InteractionContract:
    """Build the unified runtime contract for one prompt/LLM turn."""

    caps = capabilities or ProviderCapabilities()
    role_id = str(getattr(profile, "role_id", "") or "").strip().lower()
    whitelist = tuple(
        str(name).strip()
        for name in list(getattr(getattr(profile, "tool_policy", None), "whitelist", []) or [])
        if str(name).strip()
    )
    intent = infer_turn_intent(role_id=role_id, message=message, domain=domain)

    native_tools_enabled = bool(whitelist) and (
        caps.supports_stream_native_tools if stream else caps.supports_native_tools
    )
    tool_mode = "disabled"
    if whitelist:
        tool_mode = "native_only" if native_tools_enabled else "native_required_but_unavailable"

    structured_output_enabled = bool(response_model) and (not stream) and caps.supports_json_schema
    output_mode = "structured_json" if structured_output_enabled else "plain_text"

    notes: list[str] = []
    if native_tools_enabled:
        notes.append("runtime_managed_native_tools")
    elif whitelist:
        notes.append("provider_missing_native_tool_support")

    if structured_output_enabled:
        notes.append("runtime_managed_json_schema")
    elif response_model is not None and not stream:
        notes.append("schema_validation_fallback")

    return InteractionContract(
        role_id=role_id,
        intent=intent,
        tool_mode=tool_mode,
        output_mode=output_mode,
        tool_whitelist=whitelist,
        response_schema_name=getattr(response_model, "__name__", None) if response_model else None,
        native_tools_enabled=native_tools_enabled,
        structured_output_enabled=structured_output_enabled,
        text_tool_fallback_allowed=False,
        text_response_fallback_allowed=response_model is not None and not stream,
        notes=tuple(notes),
    )


def render_runtime_contract_guidance(contract: InteractionContract) -> str:
    """Render concise runtime contract guidance for the system prompt."""

    lines = [
        "【运行时契约】",
        "1. 工具调用和结构化输出由运行时/API 约束，不要自创 XML、伪 JSON 协议或演示性标签。",
        "2. 回答只表达任务语义本身，不要复述 schema、不要解释格式规则、不要输出占位示例。",
    ]

    if contract.native_tools_enabled:
        lines.append("3. 当前回合优先使用原生工具调用；不要在可见文本中输出工具包装器。")
    elif contract.tool_whitelist:
        lines.append("3. 当前 provider 不支持原生工具；本回合不得退回文本工具协议，应由运行时/模型配置修复能力缺口。")
    else:
        lines.append("3. 当前回合不允许工具调用。")

    if contract.structured_output_enabled:
        lines.append("4. 当前回合最终结果受 JSON Schema 约束，直接返回符合契约的结果对象。")
    else:
        lines.append("4. 当前回合以自然语言语义输出为主，必要时由运行时在后置阶段校验/规范化。")

    return "\n".join(lines)
