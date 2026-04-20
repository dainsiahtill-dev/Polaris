"""Code-domain adapter for State-First Context OS."""

from __future__ import annotations

import re

from polaris.kernelone.context.control_plane_noise import is_control_plane_noise

from ..helpers import _trim_text
from ..models_v2 import (
    ArtifactRecordV2 as ArtifactRecord,
    RoutingClassEnum as RoutingClass,
    TranscriptEventV2 as TranscriptEvent,
    WorkingStateV2 as WorkingState,
)
from ..policies import StateFirstContextOSPolicy
from .contracts import DomainRoutingDecision, DomainStatePatchHints
from .generic import GenericContextDomainAdapter, _normalize_text, _unique

_CODE_PATH_RE = re.compile(
    r"([A-Za-z]:\\[^ \n\r\t]+|[/\\][^ \n\r\t]+|`[^`]+\.(py|md|ya?ml|json|toml|ts|tsx|js|jsx|sql|sh|ps1)`|\b[\w./\\-]+\.(py|md|ya?ml|json|toml|ts|tsx|js|jsx|sql|sh|ps1)\b)",
    re.IGNORECASE,
)
_SYMBOL_RE = re.compile(
    r"(?<![A-Za-z0-9_])([A-Z][A-Za-z0-9_]{2,}(?:\(\))?|[a-z_][A-Za-z0-9_]{2,}\(\))(?=[^A-Za-z0-9_]|$)"
)
_CODE_GOAL_PATTERNS = (
    re.compile(r"(error|bug|fix|refactor|patch|diff|compile|test|stacktrace|traceback)", re.IGNORECASE),
    re.compile(r"(错误|失败|修复|重构|补丁|测试|编译|回归)"),
)
_CODE_ARTIFACT_PATTERNS = (
    re.compile(r"```"),
    re.compile(r"\b(pytest|traceback|exception|stack trace|diff|patch|repo|symbol)\b", re.IGNORECASE),
)
_CODE_LOOP_PATTERNS = (
    re.compile(r"\b(run tests|add test|fix bug|apply patch|refactor|read file|grep|search code)\b", re.IGNORECASE),
    re.compile(r"(跑测试|补测试|修复 bug|打补丁|重构|读取文件|搜索代码)"),
)

# === Code-Domain Attention Runtime Enhancements (A7) ===

# Code-specific follow-up action patterns
_CODE_FOLLOWUP_PATTERNS = (
    re.compile(
        r"(需要我帮你修复|要不要我修复|我帮你打补丁|需要我测试|帮你跑测试|需要我重构|帮你重构|需要我实现|帮你实现|需要我补测试)",
        re.IGNORECASE,
    ),
    re.compile(r"(fix|patch|test|refactor|implement|add|run) this", re.IGNORECASE),
)

# Code-specific intent patterns (stronger promotion)
_CODE_INTENT_FIX = (re.compile(r"\b(fix|修复|bug|错误|error|patch|补丁)\b", re.IGNORECASE),)
_CODE_INTENT_TEST = (re.compile(r"\b(test|测试|pytest|coverage|验收)\b", re.IGNORECASE),)
_CODE_INTENT_REFACTOR = (re.compile(r"\b(refactor|重构|重写|rewrite|optimize|优化)\b", re.IGNORECASE),)
_CODE_INTENT_IMPLEMENT = (re.compile(r"\b(implement|实现|add|新增|create|创建)\b", re.IGNORECASE),)
_CODE_INTENT_READ = (re.compile(r"\b(read|查看|show|display|cat|open)\b", re.IGNORECASE),)

# Code-specific artifact weighting
_CODE_WEIGHT_PATTERNS = (
    re.compile(r"\b(test_|tests/|__test__|spec)\b", re.IGNORECASE),  # Test files higher priority
    re.compile(r"\b(main|entry|init|setup)\b", re.IGNORECASE),  # Entry points
    re.compile(r"\b(config|settings|constants)\b", re.IGNORECASE),  # Config files
)

# Code workflow hints
_CODE_WORKFLOW_HINTS = {
    "fix": "确认问题后，先写测试复现 bug，再修复代码",
    "test": "先理解被测代码，再补充测试用例，最后运行验证",
    "refactor": "确认重构范围，写好测试保护，逐步重构",
    "implement": "明确接口和边界，先写核心逻辑",
    "read": "先确认文件路径和符号位置，再读取内容",
}


def _extract_code_entities(text: str) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for match in _CODE_PATH_RE.findall(text):
        token = match[0] if isinstance(match, tuple) else match
        normalized = str(token).strip("`").replace("\\", "/").strip()
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower())
            result.append(normalized)
    for match in _SYMBOL_RE.findall(text):
        token = str(match).strip()
        lowered = token.lower()
        if token and lowered not in seen:
            seen.add(lowered)
            result.append(token)
    return tuple(result)


def _extract_code_followup_intent(text: str) -> str | None:
    """Extract code-specific follow-up action intent from assistant message.

    Returns one of: fix, test, refactor, implement, read
    """
    content_lower = text.lower()
    # Check for Chinese patterns first
    if re.search(r"(修复|打补丁|补丁)", content_lower):
        return "fix"
    if re.search(r"(测试|跑测试|验收)", content_lower):
        return "test"
    if re.search(r"(重构|重写)", content_lower):
        return "refactor"
    if re.search(r"(实现|新增|添加|创建)", content_lower):
        return "implement"
    if re.search(r"(读取|查看|读文件)", content_lower):
        return "read"
    # Check for English patterns
    for pattern in _CODE_INTENT_FIX:
        if pattern.search(content_lower):
            return "fix"
    for pattern in _CODE_INTENT_TEST:
        if pattern.search(content_lower):
            return "test"
    for pattern in _CODE_INTENT_REFACTOR:
        if pattern.search(content_lower):
            return "refactor"
    for pattern in _CODE_INTENT_IMPLEMENT:
        if pattern.search(content_lower):
            return "implement"
    for pattern in _CODE_INTENT_READ:
        if pattern.search(content_lower):
            return "read"
    return None


def _get_code_workflow_hint(intent: str | None) -> str:
    """Get workflow hint for code-specific intent."""
    if intent and intent in _CODE_WORKFLOW_HINTS:
        return _CODE_WORKFLOW_HINTS[intent]
    return ""


def _calculate_code_artifact_weight(content: str) -> float:
    """Calculate weight multiplier for code artifacts.

    Higher priority for:
    - Test files
    - Entry points (main, init, setup)
    - Config files
    """
    weight = 1.0
    content_lower = content.lower()
    if _CODE_WEIGHT_PATTERNS[0].search(content_lower):  # Test files
        weight *= 1.2
    if _CODE_WEIGHT_PATTERNS[1].search(content_lower):  # Entry points
        weight *= 1.1
    if _CODE_WEIGHT_PATTERNS[2].search(content_lower):  # Config files
        weight *= 1.1
    return weight


class CodeContextDomainAdapter(GenericContextDomainAdapter):
    """Code-first enhancement adapter on top of the generic runtime.

    This adapter extends the generic Context OS with code-domain specific
    intelligence for:
    - Code-specific follow-up action recognition
    - Stronger intent promotion for code-fix/test/patch/read operations
    - Code-specific artifact weighting
    - Code workflow hints
    """

    adapter_id = "code"

    def classify_event(
        self,
        event: TranscriptEvent,
        *,
        policy: StateFirstContextOSPolicy,
    ) -> DomainRoutingDecision:
        base = super().classify_event(event, policy=policy)
        content = event.content
        if base.route == RoutingClass.ARCHIVE:
            return base

        # === Code-specific intent classification (stronger promotion) ===
        # Check for code-specific intents first with higher confidence
        intent = _extract_code_followup_intent(content)
        if intent:
            # Code-specific intents get higher priority
            if intent in ("fix", "patch"):
                return DomainRoutingDecision(
                    route=RoutingClass.PATCH,
                    confidence=0.95,
                    reasons=(f"code_intent_{intent}",),
                    metadata={"code_workflow_hint": _get_code_workflow_hint(intent)},
                )
            elif intent in ("test", "refactor"):
                return DomainRoutingDecision(
                    route=RoutingClass.PATCH,
                    confidence=0.93,
                    reasons=(f"code_intent_{intent}",),
                    metadata={"code_workflow_hint": _get_code_workflow_hint(intent)},
                )
            elif intent == "read":
                return DomainRoutingDecision(
                    route=RoutingClass.CLEAR,
                    confidence=0.90,
                    reasons=("code_intent_read",),
                    metadata={"code_workflow_hint": _get_code_workflow_hint(intent)},
                )

        if any(pattern.search(content) for pattern in _CODE_ARTIFACT_PATTERNS):
            return DomainRoutingDecision(
                route=RoutingClass.ARCHIVE,
                confidence=0.98,
                reasons=("code_artifact",),
            )
        if _extract_code_entities(content):
            return DomainRoutingDecision(
                route=RoutingClass.PATCH,
                confidence=0.91,
                reasons=("code_entities",),
            )
        if any(pattern.search(content) for pattern in _CODE_GOAL_PATTERNS):
            return DomainRoutingDecision(
                route=RoutingClass.PATCH,
                confidence=0.89,
                reasons=("code_goal",),
            )
        return base

    def classify_assistant_followup(
        self,
        event: TranscriptEvent,
        *,
        policy: StateFirstContextOSPolicy,
    ) -> DomainRoutingDecision | None:
        """Classify assistant follow-up action for code-domain.

        Returns enhanced decision if this is a code-specific follow-up question.
        """
        if event.role != "assistant":
            return None

        content = event.content
        intent = _extract_code_followup_intent(content)

        if intent:
            return DomainRoutingDecision(
                route=RoutingClass.CLEAR,  # Follow-up questions should not be archived
                confidence=0.95,
                reasons=(f"code_followup_{intent}",),
                metadata={
                    "code_intent": intent,
                    "code_workflow_hint": _get_code_workflow_hint(intent),
                    "is_code_domain_followup": True,
                },
            )
        return None

    def build_artifact(
        self,
        event: TranscriptEvent,
        *,
        artifact_id: str,
        policy: StateFirstContextOSPolicy,
    ) -> ArtifactRecord | None:
        artifact = super().build_artifact(event, artifact_id=artifact_id, policy=policy)
        if artifact is None:
            return None
        content = event.content.lstrip()
        artifact_type = artifact.artifact_type
        if content.startswith("```"):
            artifact_type = "code_block"
        elif event.kind == "tool_result":
            artifact_type = "code_tool_result"
        elif _extract_code_entities(event.content):
            artifact_type = "file_excerpt"

        # Calculate code-specific weight
        code_weight = _calculate_code_artifact_weight(content)

        # Determine code intent from content
        code_intent = _extract_code_followup_intent(event.content)

        return ArtifactRecord(
            artifact_id=artifact.artifact_id,
            artifact_type=artifact_type,
            mime_type=artifact.mime_type,
            token_count=artifact.token_count,
            char_count=artifact.char_count,
            peek=artifact.peek,
            keys=artifact.keys,
            content=artifact.content,
            source_event_ids=artifact.source_event_ids,
            restore_tool=artifact.restore_tool,
            metadata={  # type: ignore[arg-type]
                **dict(artifact.metadata),
                "adapter_id": self.adapter_id,
                "entities": list(_extract_code_entities(event.content)),
                "code_weight": code_weight,
                "code_intent": code_intent,
                "code_workflow_hint": _get_code_workflow_hint(code_intent),
            },
        )

    def extract_state_hints(self, event: TranscriptEvent) -> DomainStatePatchHints:
        base = super().extract_state_hints(event)
        content = _normalize_text(event.content)
        if not content or event.role in {"tool", "system"} or is_control_plane_noise(content):
            return base
        entities = list(base.entities)
        persistent_facts = list(base.persistent_facts)
        open_loops = list(base.open_loops)
        goals = list(base.goals)
        decisions = list(base.decisions)

        # Extract code entities
        for entity in _extract_code_entities(content):
            entities.append(entity)
            persistent_facts.append(entity)

        # Extract code-specific intent
        code_intent = _extract_code_followup_intent(content)
        if code_intent:
            # Add code-specific open loop
            open_loops.append(f"code_{code_intent}:{content[:100]}")

        if any(pattern.search(content) for pattern in _CODE_GOAL_PATTERNS):
            goals.append(content)
        if any(pattern.search(content) for pattern in _CODE_LOOP_PATTERNS):
            open_loops.append(content)
        if any(pattern.search(content) for pattern in _CODE_GOAL_PATTERNS) and not decisions:
            decisions.append(_trim_text(content, max_chars=220))

        return DomainStatePatchHints(
            goals=_unique(goals),
            accepted_plan=base.accepted_plan,
            open_loops=_unique(open_loops),
            blocked_on=base.blocked_on,
            deliverables=base.deliverables,
            preferences=base.preferences,
            style=base.style,
            persistent_facts=_unique(persistent_facts),
            temporal_facts=base.temporal_facts,
            entities=_unique(entities),
            decisions=_unique(decisions),
        )

    def should_seal_episode(
        self,
        *,
        closed_events: tuple[TranscriptEvent, ...],
        active_window: tuple[TranscriptEvent, ...],
        working_state: WorkingState,
    ) -> bool:
        if super().should_seal_episode(
            closed_events=closed_events,
            active_window=active_window,
            working_state=working_state,
        ):
            return True
        return any(event.kind == "tool_result" or event.artifact_id for event in closed_events)
