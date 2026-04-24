"""State-First Context OS models.

.. deprecated::
    This module is deprecated. Use models_v2.py (Pydantic v2 models) instead.
"""

from __future__ import annotations

import contextlib
import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.events.typed import ContextWindowStatus

    from .content_store import ContentRef

warnings.warn(
    "polaris.kernelone.context.context_os.models is deprecated. Use models_v2.py (Pydantic v2 models) instead.",
    DeprecationWarning,
    stacklevel=2,
)


def _tuple_str(values: Any) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    result: list[str] = []
    for item in values:
        token = str(item or "").strip()
        if token:
            result.append(token)
    return tuple(result)


def _copy_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _freeze_mapping(value: Any) -> tuple[tuple[str, Any], ...]:
    """Convert a dict to an immutable tuple of tuples for frozen dataclass fields."""
    if not isinstance(value, dict):
        return ()
    return tuple(sorted(value.items()))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class RoutingClass:
    CLEAR: str = "clear"
    PATCH: str = "patch"
    ARCHIVE: str = "archive"
    SUMMARIZE: str = "summarize"


class DialogAct:
    """Dialog act types for conversation classification.

    .. deprecated::
        Use models_v2.DialogAct (Enum) instead.

    These acts represent the semantic function of a user/assistant message
    in the conversational flow, enabling proper attention and intent tracking.
    """

    AFFIRM = "affirm"  # 确认: 需要/好的/可以/是
    DENY = "deny"  # 否定: 不/不要/不用
    PAUSE = "pause"  # 暂停: 先别/等一下
    REDIRECT = "redirect"  # 重定向: 改成另外一个
    CLARIFY = "clarify"  # 澄清: 什么意思/再说说
    COMMIT = "commit"  # 承诺: 就这样/确定/就这样吧
    CANCEL = "cancel"  # 取消: 取消/算了/不要了
    STATUS_ACK = "status_ack"  # 状态确认: 知道了/好的收到
    NOISE = "noise"  # 无意义/低信号
    UNKNOWN = "unknown"  # 未分类

    @classmethod
    def values(cls) -> tuple[str, ...]:
        warnings.warn(
            "DialogAct is deprecated. Use models_v2.DialogAct instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return (
            cls.AFFIRM,
            cls.DENY,
            cls.PAUSE,
            cls.REDIRECT,
            cls.CLARIFY,
            cls.COMMIT,
            cls.CANCEL,
            cls.STATUS_ACK,
            cls.NOISE,
            cls.UNKNOWN,
        )

    @classmethod
    def is_high_priority(cls, act: str) -> bool:
        """High-priority dialog acts that should never be treated as low-signal."""
        warnings.warn(
            "DialogAct.is_high_priority is deprecated. Use models_v2.DialogAct.is_high_priority instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return act in {
            cls.AFFIRM,
            cls.DENY,
            cls.PAUSE,
            cls.REDIRECT,
            cls.CLARIFY,
            cls.COMMIT,
            cls.CANCEL,
        }


@dataclass(slots=True)
class DialogActResult:
    """Result of dialog act classification."""

    act: str = ""
    confidence: float = 0.0
    triggers: tuple[str, ...] = ()
    _metadata: dict[str, Any] | tuple[tuple[str, Any], ...] = field(default_factory=tuple)

    @property
    def metadata(self) -> tuple[tuple[str, Any], ...]:
        return self._metadata if isinstance(self._metadata, tuple) else _freeze_mapping(self._metadata)

    def __post_init__(self) -> None:
        warnings.warn(
            "DialogActResult dataclass is deprecated. Use models_v2.DialogActResultV2 instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        if not isinstance(self._metadata, tuple):
            self._metadata = _freeze_mapping(self._metadata) if isinstance(self._metadata, dict) else ()

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> DialogActResult:
        if not isinstance(payload, dict):
            return cls()
        return cls(
            act=str(payload.get("act") or DialogAct.UNKNOWN).strip(),
            confidence=max(0.0, min(1.0, _safe_float(payload.get("confidence"), default=0.0))),
            triggers=_tuple_str(payload.get("triggers")),
            _metadata=_freeze_mapping(payload.get("metadata")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "act": self.act,
            "confidence": self.confidence,
            "triggers": list(self.triggers),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class PendingFollowUp:
    """Represents a pending follow-up action from assistant that awaits user resolution.

    This is a first-class state object that tracks the lifecycle of assistant
    follow-up questions (e.g., "需要我帮你实现吗?").
    """

    action: str = ""
    source_event_id: str = ""
    source_sequence: int = 0
    status: str = "pending"  # pending|confirmed|denied|paused|redirected|expired
    updated_at: str = ""

    def __post_init__(self) -> None:
        warnings.warn(
            "PendingFollowUp dataclass is deprecated. Use models_v2.PendingFollowUpV2 instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> PendingFollowUp:
        if not isinstance(payload, dict):
            return cls()
        return cls(
            action=str(payload.get("action") or "").strip(),
            source_event_id=str(payload.get("source_event_id") or "").strip(),
            source_sequence=max(0, _safe_int(payload.get("source_sequence"), default=0)),
            status=str(payload.get("status") or "pending").strip(),
            updated_at=str(payload.get("updated_at") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "source_event_id": self.source_event_id,
            "source_sequence": self.source_sequence,
            "status": self.status,
            "updated_at": self.updated_at,
        }

    def is_resolved(self) -> bool:
        return self.status in {"confirmed", "denied", "paused", "redirected", "expired"}

    def is_blocking(self) -> bool:
        """Pending follow-up blocks episode sealing until resolved."""
        return self.status == "pending"


@dataclass(slots=True)
class TranscriptEvent:
    event_id: str
    sequence: int
    role: str
    kind: str
    route: str
    content: str
    source_turns: tuple[str, ...] = ()
    artifact_id: str | None = None
    created_at: str = ""
    _metadata: dict[str, Any] | tuple[tuple[str, Any], ...] = field(default_factory=tuple)
    content_ref: ContentRef | None = None

    @property
    def metadata(self) -> tuple[tuple[str, Any], ...]:
        return self._metadata if isinstance(self._metadata, tuple) else _freeze_mapping(self._metadata)

    def __post_init__(self) -> None:
        warnings.warn(
            "TranscriptEvent dataclass is deprecated. Use models_v2.TranscriptEventV2 instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        if not isinstance(self._metadata, tuple):
            self._metadata = _freeze_mapping(self._metadata) if isinstance(self._metadata, dict) else ()

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> TranscriptEvent | None:
        if not isinstance(payload, dict):
            return None
        return cls(
            event_id=str(payload.get("event_id") or "").strip(),
            sequence=_safe_int(payload.get("sequence"), default=0),
            role=str(payload.get("role") or "").strip(),
            kind=str(payload.get("kind") or "").strip(),
            route=str(payload.get("route") or "").strip(),
            content=str(payload.get("content") or ""),
            source_turns=_tuple_str(payload.get("source_turns")),
            artifact_id=str(payload.get("artifact_id") or "").strip() or None,
            created_at=str(payload.get("created_at") or "").strip(),
            _metadata=_freeze_mapping(payload.get("metadata")),
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "role": self.role,
            "kind": self.kind,
            "route": self.route,
            "content": self.content,
            "source_turns": list(self.source_turns),
            "artifact_id": self.artifact_id,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }
        if self.content_ref is not None:
            d["content_ref"] = {
                "hash": self.content_ref.hash,
                "size": self.content_ref.size,
                "mime": self.content_ref.mime,
                "encoding": self.content_ref.encoding,
            }
        return d


@dataclass(slots=True)
class ArtifactRecord:
    artifact_id: str
    artifact_type: str
    mime_type: str
    token_count: int
    char_count: int
    peek: str
    keys: tuple[str, ...] = ()
    content: str = ""
    source_event_ids: tuple[str, ...] = ()
    restore_tool: str = "read_artifact"
    _metadata: dict[str, Any] | tuple[tuple[str, Any], ...] = field(default_factory=tuple)

    @property
    def metadata(self) -> tuple[tuple[str, Any], ...]:
        return self._metadata if isinstance(self._metadata, tuple) else _freeze_mapping(self._metadata)

    def __post_init__(self) -> None:
        warnings.warn(
            "ArtifactRecord dataclass is deprecated. Use models_v2.ArtifactRecordV2 instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        if not isinstance(self._metadata, tuple):
            self._metadata = _freeze_mapping(self._metadata) if isinstance(self._metadata, dict) else ()

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> ArtifactRecord | None:
        if not isinstance(payload, dict):
            return None
        return cls(
            artifact_id=str(payload.get("artifact_id") or "").strip(),
            artifact_type=str(payload.get("artifact_type") or "").strip(),
            mime_type=str(payload.get("mime_type") or "").strip(),
            token_count=max(0, _safe_int(payload.get("token_count"), default=0)),
            char_count=max(0, _safe_int(payload.get("char_count"), default=0)),
            peek=str(payload.get("peek") or "").strip(),
            keys=_tuple_str(payload.get("keys")),
            content=str(payload.get("content") or ""),
            source_event_ids=_tuple_str(payload.get("source_event_ids")),
            restore_tool=str(payload.get("restore_tool") or "read_artifact").strip(),
            _metadata=_freeze_mapping(payload.get("metadata")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "mime_type": self.mime_type,
            "token_count": self.token_count,
            "char_count": self.char_count,
            "peek": self.peek,
            "keys": list(self.keys),
            "content": self.content,
            "source_event_ids": list(self.source_event_ids),
            "restore_tool": self.restore_tool,
            "metadata": dict(self.metadata),
        }

    def to_stub(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "type": self.artifact_type,
            "mime": self.mime_type,
            "tokens": self.token_count,
            "chars": self.char_count,
            "peek": self.peek,
            "keys": list(self.keys),
            "restore_tool": self.restore_tool,
        }


@dataclass(frozen=True, slots=True)
class StateEntry:
    entry_id: str
    path: str
    value: str
    source_turns: tuple[str, ...]
    confidence: float
    updated_at: str
    supersedes: str | None = None
    value_ref: ContentRef | None = None

    def __post_init__(self) -> None:
        warnings.warn(
            "StateEntry dataclass is deprecated. Use models_v2.StateEntryV2 instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> StateEntry | None:
        if not isinstance(payload, dict):
            return None
        return cls(
            entry_id=str(payload.get("entry_id") or "").strip(),
            path=str(payload.get("path") or "").strip(),
            value=str(payload.get("value") or "").strip(),
            source_turns=_tuple_str(payload.get("source_turns")),
            confidence=max(0.0, min(1.0, _safe_float(payload.get("confidence"), default=0.0))),
            updated_at=str(payload.get("updated_at") or "").strip(),
            supersedes=str(payload.get("supersedes") or "").strip() or None,
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "entry_id": self.entry_id,
            "path": self.path,
            "value": self.value,
            "source_turns": list(self.source_turns),
            "confidence": self.confidence,
            "updated_at": self.updated_at,
            "supersedes": self.supersedes,
        }
        if self.value_ref is not None:
            d["value_ref"] = {
                "hash": self.value_ref.hash,
                "size": self.value_ref.size,
                "mime": self.value_ref.mime,
                "encoding": self.value_ref.encoding,
            }
        return d


@dataclass(frozen=True, slots=True)
class DecisionEntry:
    decision_id: str
    summary: str
    source_turns: tuple[str, ...]
    updated_at: str
    kind: str = "decision"
    supersedes: str | None = None
    basis_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        warnings.warn(
            "DecisionEntry dataclass is deprecated. Use models_v2.DecisionEntryV2 instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> DecisionEntry | None:
        if not isinstance(payload, dict):
            return None
        summary = str(payload.get("summary") or payload.get("value") or "").strip()
        return cls(
            decision_id=str(payload.get("decision_id") or "").strip(),
            summary=summary,
            source_turns=_tuple_str(payload.get("source_turns")),
            updated_at=str(payload.get("updated_at") or "").strip(),
            kind=str(payload.get("kind") or "decision").strip() or "decision",
            supersedes=str(payload.get("supersedes") or "").strip() or None,
            basis_refs=_tuple_str(payload.get("basis_refs")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "summary": self.summary,
            "value": self.summary,
            "source_turns": list(self.source_turns),
            "updated_at": self.updated_at,
            "kind": self.kind,
            "supersedes": self.supersedes,
            "basis_refs": list(self.basis_refs),
        }

    @property
    def value(self) -> str:
        return self.summary


@dataclass(frozen=True, slots=True)
class RunCard:
    """Run Card v2 - Extended with attention runtime fields.

    This extends the original RunCard with explicit attention semantics:
    - latest_user_intent: The most recent explicit user intent (may be short)
    - pending_followup_action: The action the assistant is awaiting confirmation for
    - pending_followup_status: Resolution status of pending follow-up
    - last_turn_outcome: The outcome of the last user turn (affirm/deny/redirect/etc.)
    """

    current_goal: str = ""
    hard_constraints: tuple[str, ...] = ()
    open_loops: tuple[str, ...] = ()
    active_entities: tuple[str, ...] = ()
    active_artifacts: tuple[str, ...] = ()
    recent_decisions: tuple[str, ...] = ()
    next_action_hint: str = ""
    # === Run Card v2: Attention Runtime Fields ===
    latest_user_intent: str = ""
    pending_followup_action: str = ""
    pending_followup_status: str = ""  # pending|confirmed|denied|paused|redirected|expired
    last_turn_outcome: str = ""  # affirm|deny|pause|redirect|clarify|commit|cancel|status_ack|noise|unknown

    def __post_init__(self) -> None:
        warnings.warn(
            "RunCard dataclass is deprecated. Use models_v2.RunCardV2 instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> RunCard | None:
        if not isinstance(payload, dict):
            return None
        return cls(
            current_goal=str(payload.get("current_goal") or "").strip(),
            hard_constraints=_tuple_str(payload.get("hard_constraints")),
            open_loops=_tuple_str(payload.get("open_loops")),
            active_entities=_tuple_str(payload.get("active_entities")),
            active_artifacts=_tuple_str(payload.get("active_artifacts")),
            recent_decisions=_tuple_str(payload.get("recent_decisions")),
            next_action_hint=str(payload.get("next_action_hint") or "").strip(),
            # Run Card v2 fields
            latest_user_intent=str(payload.get("latest_user_intent") or "").strip(),
            pending_followup_action=str(payload.get("pending_followup_action") or "").strip(),
            pending_followup_status=str(payload.get("pending_followup_status") or "").strip(),
            last_turn_outcome=str(payload.get("last_turn_outcome") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_goal": self.current_goal,
            "hard_constraints": list(self.hard_constraints),
            "open_loops": list(self.open_loops),
            "active_entities": list(self.active_entities),
            "active_artifacts": list(self.active_artifacts),
            "recent_decisions": list(self.recent_decisions),
            "next_action_hint": self.next_action_hint,
            # Run Card v2 fields
            "latest_user_intent": self.latest_user_intent,
            "pending_followup_action": self.pending_followup_action,
            "pending_followup_status": self.pending_followup_status,
            "last_turn_outcome": self.last_turn_outcome,
        }

    def detect_intent_switch(self, new_intent: str) -> bool:
        """Detect if user has switched intent from exploration to execution.

        Uses heuristic-based keyword matching to identify transitions like:
        - "看/分析/检查" → "写/创建/修改"
        - "read/analyze/check" → "write/create/edit"

        Args:
            new_intent: The new user intent text

        Returns:
            True if intent switch is detected (view→write transition)
        """
        if not self.latest_user_intent or not new_intent:
            return False

        old_lower = self.latest_user_intent.lower().strip()
        new_lower = new_intent.lower().strip()

        # Define action verb sets
        view_verbs = {
            "看",
            "分析",
            "检查",
            "读取",
            "查看",
            "探查",
            "了解",
            "read",
            "analyze",
            "check",
            "inspect",
            "view",
            "explore",
            "look",
            "see",
            "find",
            "search",
        }
        write_verbs = {
            "写",
            "创建",
            "修改",
            "生成",
            "添加",
            "实现",
            "write",
            "create",
            "edit",
            "modify",
            "generate",
            "add",
            "implement",
            "build",
            "make",
        }

        old_has_view = any(v in old_lower for v in view_verbs)
        new_has_write = any(v in new_lower for v in write_verbs)

        return old_has_view and new_has_write

    def generate_intent_switch_summary(self, new_intent: str) -> str:
        """Generate a summary when intent switch is detected.

        Instead of discarding old goal content, extract a concise summary
        that preserves key findings while freeing up context space.

        Args:
            new_intent: The new user intent

        Returns:
            Summary string for the old intent's completion
        """
        old_goal = self.current_goal or self.latest_user_intent
        if not old_goal:
            return ""

        # Generate concise summary based on old goal content
        # In production, this could use LLM to generate a one-sentence summary
        summary_parts = []

        if self.active_artifacts:
            summary_parts.append(f"已探明{len(self.active_artifacts)}个相关对象")

        if self.recent_decisions:
            summary_parts.append(f"决策: {self.recent_decisions[-1]}")

        if summary_parts:
            return f"[已完成: {old_goal}] " + "; ".join(summary_parts)
        return f"[已完成: {old_goal}]"


@dataclass(frozen=True, slots=True)
class ContextSliceSelection:
    selection_type: str
    ref: str
    reason: str

    def __post_init__(self) -> None:
        warnings.warn(
            "ContextSliceSelection dataclass is deprecated. Use models_v2.ContextSliceSelectionV2 instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> ContextSliceSelection | None:
        if not isinstance(payload, dict):
            return None
        return cls(
            selection_type=str(payload.get("type") or payload.get("selection_type") or "").strip(),
            ref=str(payload.get("ref") or "").strip(),
            reason=str(payload.get("reason") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.selection_type,
            "ref": self.ref,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ContextSlicePlan:
    plan_id: str
    budget_tokens: int
    roots: tuple[str, ...] = ()
    included: tuple[ContextSliceSelection, ...] = ()
    excluded: tuple[ContextSliceSelection, ...] = ()
    pressure_level: str = "normal"

    def __post_init__(self) -> None:
        warnings.warn(
            "ContextSlicePlan dataclass is deprecated. Use models_v2.ContextSlicePlanV2 instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> ContextSlicePlan | None:
        if not isinstance(payload, dict):
            return None
        return cls(
            plan_id=str(payload.get("plan_id") or "").strip(),
            budget_tokens=max(0, _safe_int(payload.get("budget_tokens"), default=0)),
            roots=_tuple_str(payload.get("roots")),
            included=tuple(
                item
                for item in (ContextSliceSelection.from_mapping(v) for v in payload.get("included", []))
                if item is not None
            ),
            excluded=tuple(
                item
                for item in (ContextSliceSelection.from_mapping(v) for v in payload.get("excluded", []))
                if item is not None
            ),
            pressure_level=str(payload.get("pressure_level") or "normal").strip() or "normal",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "budget_tokens": self.budget_tokens,
            "roots": list(self.roots),
            "included": [item.to_dict() for item in self.included],
            "excluded": [item.to_dict() for item in self.excluded],
            "pressure_level": self.pressure_level,
        }


@dataclass(frozen=True, slots=True)
class EpisodeCard:
    """64/256/1k三层摘要的闭环历史卡片"""

    episode_id: str
    from_sequence: int
    to_sequence: int
    intent: str
    outcome: str
    decisions: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    reopen_conditions: tuple[str, ...] = ()
    source_spans: tuple[str, ...] = ()
    digest_64: str = ""
    digest_256: str = ""
    digest_1k: str = ""
    sealed_at: float = 0.0
    status: str = "sealed"
    reopened_at: str = ""
    reopen_reason: str = ""

    def __post_init__(self) -> None:
        warnings.warn(
            "EpisodeCard dataclass is deprecated. Use models_v2.EpisodeCardV2 instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> EpisodeCard | None:
        if not isinstance(payload, dict):
            return None
        return cls(
            episode_id=str(payload.get("episode_id") or "").strip(),
            from_sequence=_safe_int(payload.get("from_sequence"), default=0),
            to_sequence=_safe_int(payload.get("to_sequence"), default=0),
            intent=str(payload.get("intent") or "").strip(),
            outcome=str(payload.get("outcome") or "").strip(),
            decisions=_tuple_str(payload.get("decisions")),
            facts=_tuple_str(payload.get("facts")),
            artifact_refs=_tuple_str(payload.get("artifact_refs")),
            entities=_tuple_str(payload.get("entities")),
            reopen_conditions=_tuple_str(payload.get("reopen_conditions")),
            source_spans=_tuple_str(payload.get("source_spans")),
            digest_64=str(payload.get("digest_64") or "").strip(),
            digest_256=str(payload.get("digest_256") or "").strip(),
            digest_1k=str(payload.get("digest_1k") or payload.get("narrative_1k") or "").strip(),
            sealed_at=float(payload.get("sealed_at") or 0.0),
            status=str(payload.get("status") or "sealed").strip() or "sealed",
            reopened_at=str(payload.get("reopened_at") or "").strip(),
            reopen_reason=str(payload.get("reopen_reason") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "from_sequence": self.from_sequence,
            "to_sequence": self.to_sequence,
            "intent": self.intent,
            "outcome": self.outcome,
            "decisions": list(self.decisions),
            "facts": list(self.facts),
            "artifact_refs": list(self.artifact_refs),
            "entities": list(self.entities),
            "reopen_conditions": list(self.reopen_conditions),
            "source_spans": list(self.source_spans),
            "digest_64": self.digest_64,
            "digest_256": self.digest_256,
            "digest_1k": self.digest_1k,
            "sealed_at": self.sealed_at,
            "status": self.status,
            "reopened_at": self.reopened_at,
            "reopen_reason": self.reopen_reason,
        }


@dataclass(frozen=True, slots=True)
class BudgetPlan:
    model_context_window: int
    output_reserve: int
    tool_reserve: int
    safety_margin: int
    input_budget: int
    retrieval_budget: int
    soft_limit: int
    hard_limit: int
    emergency_limit: int
    current_input_tokens: int = 0
    expected_next_input_tokens: int = 0
    p95_tool_result_tokens: int = 0
    planned_retrieval_tokens: int = 0
    validation_error: str = ""

    def __post_init__(self) -> None:
        warnings.warn(
            "BudgetPlan dataclass is deprecated. Use models_v2.BudgetPlanV2 instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    def validate_invariants(self) -> None:
        """Validate BudgetPlan invariants and raise BudgetExceededError if invalid.

        Checks:
        - expected_next_input_tokens should not exceed model_context_window

        Raises:
            BudgetExceededError: If expected_next_input_tokens exceeds model_context_window.
        """
        if self.expected_next_input_tokens > self.model_context_window:
            overrun = self.expected_next_input_tokens - self.model_context_window
            from polaris.kernelone.errors import BudgetExceededError

            raise BudgetExceededError(
                message=(
                    f"BudgetPlan invariant violated: expected_next_input_tokens "
                    f"({self.expected_next_input_tokens}) exceeds model_context_window "
                    f"({self.model_context_window}) by {overrun} tokens"
                ),
                limit=self.model_context_window,
                requested=self.expected_next_input_tokens,
                current=self.current_input_tokens,
            )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> BudgetPlan | None:
        if not isinstance(payload, dict):
            return None
        return cls(
            model_context_window=_safe_int(payload.get("model_context_window"), default=0),
            output_reserve=_safe_int(payload.get("output_reserve"), default=0),
            tool_reserve=_safe_int(payload.get("tool_reserve"), default=0),
            safety_margin=_safe_int(payload.get("safety_margin"), default=0),
            input_budget=_safe_int(payload.get("input_budget"), default=0),
            retrieval_budget=_safe_int(payload.get("retrieval_budget"), default=0),
            soft_limit=_safe_int(payload.get("soft_limit"), default=0),
            hard_limit=_safe_int(payload.get("hard_limit"), default=0),
            emergency_limit=_safe_int(payload.get("emergency_limit"), default=0),
            current_input_tokens=_safe_int(payload.get("current_input_tokens"), default=0),
            expected_next_input_tokens=_safe_int(payload.get("expected_next_input_tokens"), default=0),
            p95_tool_result_tokens=_safe_int(payload.get("p95_tool_result_tokens"), default=0),
            planned_retrieval_tokens=_safe_int(payload.get("planned_retrieval_tokens"), default=0),
            validation_error=str(payload.get("validation_error") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_context_window": self.model_context_window,
            "output_reserve": self.output_reserve,
            "tool_reserve": self.tool_reserve,
            "safety_margin": self.safety_margin,
            "input_budget": self.input_budget,
            "retrieval_budget": self.retrieval_budget,
            "soft_limit": self.soft_limit,
            "hard_limit": self.hard_limit,
            "emergency_limit": self.emergency_limit,
            "current_input_tokens": self.current_input_tokens,
            "expected_next_input_tokens": self.expected_next_input_tokens,
            "p95_tool_result_tokens": self.p95_tool_result_tokens,
            "planned_retrieval_tokens": self.planned_retrieval_tokens,
            "validation_error": self.validation_error,
        }

    def to_context_window_status_event(
        self,
        segment_breakdown: dict[str, int] | None = None,
        critical_threshold: float = 80.0,
    ) -> ContextWindowStatus:
        """Create a ContextWindowStatus event from this BudgetPlan.

        Args:
            segment_breakdown: Optional breakdown of tokens by segment
            critical_threshold: Percentage threshold for is_critical flag (default 80%)

        Returns:
            ContextWindowStatus event
        """
        from polaris.kernelone.events.typed import ContextWindowStatus

        current_tokens = self.current_input_tokens
        max_tokens = self.model_context_window

        return ContextWindowStatus.create(
            current_tokens=current_tokens,
            max_tokens=max_tokens,
            segment_breakdown=segment_breakdown,
            critical_threshold=critical_threshold,
        )


@dataclass(frozen=True, slots=True)
class UserProfileState:
    preferences: tuple[StateEntry, ...] = ()
    style: tuple[StateEntry, ...] = ()
    persistent_facts: tuple[StateEntry, ...] = ()

    def __post_init__(self) -> None:
        warnings.warn(
            "UserProfileState dataclass is deprecated. Use models_v2.UserProfileStateV2 instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> UserProfileState:
        if not isinstance(payload, dict):
            return cls()
        return cls(
            preferences=tuple(
                item
                for item in (StateEntry.from_mapping(v) for v in payload.get("preferences", []))
                if item is not None
            ),
            style=tuple(
                item for item in (StateEntry.from_mapping(v) for v in payload.get("style", [])) if item is not None
            ),
            persistent_facts=tuple(
                item
                for item in (StateEntry.from_mapping(v) for v in payload.get("persistent_facts", []))
                if item is not None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "preferences": [item.to_dict() for item in self.preferences],
            "style": [item.to_dict() for item in self.style],
            "persistent_facts": [item.to_dict() for item in self.persistent_facts],
        }


@dataclass(frozen=True, slots=True)
class TaskStateView:
    current_goal: StateEntry | None = None
    accepted_plan: tuple[StateEntry, ...] = ()
    open_loops: tuple[StateEntry, ...] = ()
    blocked_on: tuple[StateEntry, ...] = ()
    deliverables: tuple[StateEntry, ...] = ()

    def __post_init__(self) -> None:
        warnings.warn(
            "TaskStateView dataclass is deprecated. Use models_v2.TaskStateViewV2 instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> TaskStateView:
        if not isinstance(payload, dict):
            return cls()
        return cls(
            current_goal=StateEntry.from_mapping(payload.get("current_goal")),
            accepted_plan=tuple(
                item
                for item in (StateEntry.from_mapping(v) for v in payload.get("accepted_plan", []))
                if item is not None
            ),
            open_loops=tuple(
                item for item in (StateEntry.from_mapping(v) for v in payload.get("open_loops", [])) if item is not None
            ),
            blocked_on=tuple(
                item for item in (StateEntry.from_mapping(v) for v in payload.get("blocked_on", [])) if item is not None
            ),
            deliverables=tuple(
                item
                for item in (StateEntry.from_mapping(v) for v in payload.get("deliverables", []))
                if item is not None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_goal": self.current_goal.to_dict() if self.current_goal is not None else None,
            "accepted_plan": [item.to_dict() for item in self.accepted_plan],
            "open_loops": [item.to_dict() for item in self.open_loops],
            "blocked_on": [item.to_dict() for item in self.blocked_on],
            "deliverables": [item.to_dict() for item in self.deliverables],
        }


@dataclass(frozen=True, slots=True)
class WorkingState:
    user_profile: UserProfileState = field(default_factory=UserProfileState)
    task_state: TaskStateView = field(default_factory=TaskStateView)
    decision_log: tuple[DecisionEntry, ...] = ()
    active_entities: tuple[StateEntry, ...] = ()
    active_artifacts: tuple[str, ...] = ()
    temporal_facts: tuple[StateEntry, ...] = ()
    state_history: tuple[StateEntry, ...] = ()

    def __post_init__(self) -> None:
        warnings.warn(
            "WorkingState dataclass is deprecated. Use models_v2.WorkingStateV2 instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> WorkingState:
        if not isinstance(payload, dict):
            return cls()
        return cls(
            user_profile=UserProfileState.from_mapping(payload.get("user_profile")),
            task_state=TaskStateView.from_mapping(payload.get("task_state")),
            decision_log=tuple(
                item
                for item in (DecisionEntry.from_mapping(v) for v in payload.get("decision_log", []))
                if item is not None
            ),
            active_entities=tuple(
                item
                for item in (StateEntry.from_mapping(v) for v in payload.get("active_entities", []))
                if item is not None
            ),
            active_artifacts=_tuple_str(payload.get("active_artifacts")),
            temporal_facts=tuple(
                item
                for item in (StateEntry.from_mapping(v) for v in payload.get("temporal_facts", []))
                if item is not None
            ),
            state_history=tuple(
                item
                for item in (StateEntry.from_mapping(v) for v in payload.get("state_history", []))
                if item is not None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_profile": self.user_profile.to_dict(),
            "task_state": self.task_state.to_dict(),
            "decision_log": [item.to_dict() for item in self.decision_log],
            "active_entities": [item.to_dict() for item in self.active_entities],
            "active_artifacts": list(self.active_artifacts),
            "temporal_facts": [item.to_dict() for item in self.temporal_facts],
            "state_history": [item.to_dict() for item in self.state_history],
        }


@dataclass(frozen=True, slots=True)
class ContextOSSnapshot:
    version: int = 1
    mode: str = "state_first_context_os_v1"
    adapter_id: str = "generic"
    transcript_log: tuple[TranscriptEvent, ...] = ()
    working_state: WorkingState = field(default_factory=WorkingState)
    artifact_store: tuple[ArtifactRecord, ...] = ()
    episode_store: tuple[EpisodeCard, ...] = ()
    budget_plan: BudgetPlan | None = None
    updated_at: str = ""
    # Attention Runtime: pending follow-up state
    pending_followup: PendingFollowUp | None = None
    # v2.1: content-addressable map for deduplicated persistence
    content_map: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        warnings.warn(
            "ContextOSSnapshot dataclass is deprecated. Use models_v2.ContextOSSnapshotV2 instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> ContextOSSnapshot | None:
        if not isinstance(payload, dict):
            return None

        receipt_export: dict[str, str] = dict(payload.get("_receipt_store_export") or {})

        def _restore_receipt(event_data: dict[str, Any]) -> dict[str, Any]:
            content = event_data.get("content", "")
            if isinstance(content, str) and content.startswith("<receipt_ref:") and content.endswith(">"):
                ref_id = content[len("<receipt_ref:") : -1]
                actual = receipt_export.get(ref_id)
                if actual is not None:
                    event_data = dict(event_data)
                    event_data["content"] = actual
            return event_data

        transcript_log_raw = payload.get("transcript_log", [])
        transcript_log_data = [_restore_receipt(v) for v in transcript_log_raw]

        return cls(
            version=max(1, _safe_int(payload.get("version"), default=1)),
            mode=str(payload.get("mode") or "state_first_context_os_v1").strip(),
            adapter_id=str(payload.get("adapter_id") or "generic").strip() or "generic",
            transcript_log=tuple(
                item for item in (TranscriptEvent.from_mapping(v) for v in transcript_log_data) if item is not None
            ),
            working_state=WorkingState.from_mapping(payload.get("working_state")),
            artifact_store=tuple(
                item
                for item in (ArtifactRecord.from_mapping(v) for v in payload.get("artifact_store", []))
                if item is not None
            ),
            episode_store=tuple(
                item
                for item in (EpisodeCard.from_mapping(v) for v in payload.get("episode_store", []))
                if item is not None
            ),
            budget_plan=BudgetPlan.from_mapping(payload.get("budget_plan")),
            updated_at=str(payload.get("updated_at") or "").strip(),
            pending_followup=PendingFollowUp.from_mapping(payload.get("pending_followup")),
            content_map=dict(payload.get("content_map") or {}),
        )

    def to_dict(self, receipt_store: Any | None = None) -> dict[str, Any]:
        max_inline_transcript_chars = 5000
        total_chars = sum(len(evt.content) for evt in self.transcript_log)

        owns_receipt_store = False
        if receipt_store is None and total_chars > max_inline_transcript_chars:
            from polaris.kernelone.context.receipt_store import ReceiptStore

            receipt_store = ReceiptStore()
            owns_receipt_store = True

        use_receipt_refs = receipt_store is not None

        transcript_log_data: list[dict[str, Any]] = []
        for item in self.transcript_log:
            d = item.to_dict()
            if use_receipt_refs and len(item.content) > 200:
                assert receipt_store is not None
                ref_id = f"evt_{item.event_id}"
                receipt_store.put(ref_id, item.content)
                d["content"] = f"<receipt_ref:{ref_id}>"
            transcript_log_data.append(d)

        result: dict[str, Any] = {
            "version": self.version,
            "mode": self.mode,
            "adapter_id": self.adapter_id,
            "transcript_log": transcript_log_data,
            "working_state": self.working_state.to_dict(),
            "artifact_store": [item.to_dict() for item in self.artifact_store],
            "episode_store": [item.to_dict() for item in self.episode_store],
            "budget_plan": self.budget_plan.to_dict() if self.budget_plan is not None else None,
            "updated_at": self.updated_at,
            "pending_followup": self.pending_followup.to_dict() if self.pending_followup is not None else None,
            "content_map": self.content_map,
        }

        if owns_receipt_store:
            assert receipt_store is not None
            result["_receipt_store_export"] = receipt_store.export_receipts()

        return result


@dataclass(frozen=True, slots=True)
class ContextOSProjection:
    snapshot: ContextOSSnapshot
    head_anchor: str
    tail_anchor: str
    active_window: tuple[TranscriptEvent, ...] = ()
    artifact_stubs: tuple[ArtifactRecord, ...] = ()
    episode_cards: tuple[EpisodeCard, ...] = ()
    run_card: RunCard | None = None
    context_slice_plan: ContextSlicePlan | None = None

    def __post_init__(self) -> None:
        warnings.warn(
            "ContextOSProjection dataclass is deprecated. Use models_v2.ContextOSProjectionV2 instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot": self.snapshot.to_dict(),
            "head_anchor": self.head_anchor,
            "tail_anchor": self.tail_anchor,
            "active_window": [item.to_dict() for item in self.active_window],
            "artifact_stubs": [item.to_stub() for item in self.artifact_stubs],
            "episode_cards": [item.to_dict() for item in self.episode_cards],
            "run_card": self.run_card.to_dict() if self.run_card is not None else None,
            "context_slice_plan": (self.context_slice_plan.to_dict() if self.context_slice_plan is not None else None),
        }

    # Emergency compression thresholds
    MAX_EVENTS_BEFORE_EMERGENCY_COMPACT = 50
    EMERGENCY_COMPACT_TOKEN_RATIO = 0.5

    def compress(self, target_tokens: int, llm: Any = None) -> ContextOSProjection:
        """Compress active_window to target token count using turn-block-aware selection.

        Preserves entire turn blocks for the current (most recent) turn, ensuring
        that tool calls within a single turn are not arbitrarily split during compression.
        Past turns can be compressed/summarized, but the current turn's events stay together.

        Only operates on the active_window view; does NOT modify snapshot.transcript_log
        (preserving event sourcing integrity).

        Args:
            target_tokens: Target token count for the compressed window.
            llm: Optional LLM for intelligent compression (not used in current implementation).

        Returns:
            New ContextOSProjection with compressed active_window.
        """
        from dataclasses import replace as _replace

        if target_tokens <= 0:
            return self

        # Emergency compression: too many events triggers aggressive reduction
        event_count = len(self.active_window)
        if event_count > self.MAX_EVENTS_BEFORE_EMERGENCY_COMPACT:
            import logging

            logger = logging.getLogger(__name__)
            original_target = target_tokens
            target_tokens = int(target_tokens * self.EMERGENCY_COMPACT_TOKEN_RATIO)
            logger.warning(
                f"Emergency compact triggered: {event_count} events exceed "
                f"{self.MAX_EVENTS_BEFORE_EMERGENCY_COMPACT} limit. "
                f"Reducing target from {original_target} to {target_tokens} tokens."
            )
            # Record metrics
            try:
                from polaris.cells.roles.kernel.internal.metrics import get_dead_loop_metrics

                get_dead_loop_metrics().record_emergency_compaction(event_count)
            except ImportError:
                pass  # Metrics not available

        # Import the canonical token estimator
        from polaris.kernelone.context._token_estimator import estimate_tokens as _estimate_tokens

        # Calculate current token count
        current_tokens = sum(_estimate_tokens(e.content) for e in self.active_window)
        if current_tokens <= target_tokens:
            return self

        # Step 1: Identify the current turn (most recent turn identifier)
        # Events have source_turns like ("t0",) or ("t1", "t2") for multi-turn history
        current_turn: str | None = None
        max_turn_num = -1
        for event in self.active_window:
            for turn in event.source_turns:
                # Parse turn number from "t{N}" format
                if turn.startswith("t") and turn[1:].isdigit():
                    turn_num = int(turn[1:])
                    if turn_num > max_turn_num:
                        max_turn_num = turn_num
                        current_turn = turn
                    break

        # Step 2: Group events by turn block
        turn_blocks: dict[str, list[tuple[int, TranscriptEvent]]] = {}
        for i, event in enumerate(self.active_window):
            turn_key: str
            if event.source_turns and any(t == current_turn for t in event.source_turns):
                turn_key = current_turn if current_turn is not None else f"_fallback_{i}"
            elif event.source_turns:
                turn_key = event.source_turns[0]
            else:
                turn_key = f"_global_{i}"
            if turn_key not in turn_blocks:
                turn_blocks[turn_key] = []
            turn_blocks[turn_key].append((i, event))

        # Step 3: Score each turn block
        # Current turn gets highest priority, older turns get lower priority
        scored_blocks: list[tuple[float, str, list[tuple[int, TranscriptEvent]]]] = []
        for turn_key, events_in_turn in turn_blocks.items():
            score = 0.0
            is_current_turn = turn_key == current_turn

            for i, event in events_in_turn:
                # Base recency score
                score += i * 0.1
                # PATCH routing priority
                if event.route.upper() == "PATCH":
                    score += 10.0
                # ARCHIVE routing gets negative score (but we preserve current turn anyway)
                if event.route.upper() == "ARCHIVE":
                    score -= 5.0

            # Boost current turn significantly - it must be preserved as a block
            if is_current_turn:
                score += 1000.0

            scored_blocks.append((score, turn_key, events_in_turn))

        scored_blocks.sort(reverse=True)  # Highest score first

        # Step 4: Greedy selection of turn blocks until target_tokens
        selected_indices: set[int] = set()
        accumulated = 0
        current_turn_block_preserved = False

        for _score, turn_key, events_in_turn in scored_blocks:
            is_current_turn = turn_key == current_turn
            block_tokens = sum(_estimate_tokens(e.content) for _, e in events_in_turn)

            # If this is the current turn, we MUST preserve it entirely
            if is_current_turn:
                for i, _event in events_in_turn:
                    selected_indices.add(i)
                accumulated += block_tokens
                current_turn_block_preserved = True
                continue

            # For past turns, apply normal budget constraints
            if accumulated + block_tokens <= target_tokens:
                for i, _event in events_in_turn:
                    selected_indices.add(i)
                accumulated += block_tokens
            else:
                # Past turn doesn't fit - try to fit individual events (oldest first)
                for _, event in sorted(events_in_turn, key=lambda x: x[0]):  # Oldest first
                    event_tokens = _estimate_tokens(event.content)
                    if accumulated + event_tokens <= target_tokens:
                        selected_indices.add(event.sequence if hasattr(event, "sequence") else 0)
                        accumulated += event_tokens

            # Stop once we reach 90% of target (acceptable threshold)
            if accumulated >= target_tokens * 0.9:
                break

        # Build compressed window in original order
        compressed_window = tuple(
            self.active_window[i] for i in sorted(selected_indices) if i < len(self.active_window)
        )

        # Safety check: if current turn was somehow lost, add it back
        if not current_turn_block_preserved and self.active_window:
            last_event = self.active_window[-1]
            if last_event.source_turns and any(t == current_turn for t in last_event.source_turns):
                # Current turn was lost - this is a critical safety fallback
                # Add all current turn events back
                for i, evt in enumerate(self.active_window):
                    if (
                        evt.source_turns
                        and any(t == current_turn for t in evt.source_turns)
                        and i not in selected_indices
                    ):
                        selected_indices.add(i)
                compressed_window = tuple(
                    self.active_window[i] for i in sorted(selected_indices) if i < len(self.active_window)
                )

        return _replace(self, active_window=compressed_window)

    def to_prompt_dict(self) -> dict[str, Any]:
        """返回轻量级 prompt 数据, 排除完整 snapshot. 用于 prompt 注入, 不要用于持久化."""
        return {
            "head_anchor": self.head_anchor,
            "tail_anchor": self.tail_anchor,
            "active_window": [item.to_dict() for item in self.active_window],
            "artifact_stubs": [a.to_stub() for a in self.artifact_stubs],
            "episode_cards": [e.to_dict() for e in self.episode_cards],
            "run_card": self.run_card.to_dict() if self.run_card is not None else None,
            "context_slice_plan": self.context_slice_plan.to_dict() if self.context_slice_plan is not None else None,
        }


@dataclass(frozen=True, slots=True)
class StateFirstContextOSPolicy:
    """Policy for State-First Context OS behavior.

    All policy values can be overridden by environment variables with the
    KERNELONE_CONTEXT_OS_ prefix (e.g., KERNELONE_CONTEXT_OS_ENABLE_DIALOG_ACT).

    Use the `from_env()` factory method to create a policy instance that
    respects environment variable overrides.
    """

    def __post_init__(self) -> None:
        warnings.warn(
            "StateFirstContextOSPolicy dataclass is deprecated. Use models_v2.StateFirstContextOSPolicyV2 instead.",
            DeprecationWarning,
            stacklevel=2,
        )

    model_context_window: int = 128_000
    default_history_window_messages: int = 8
    artifact_char_threshold: int = 1200
    artifact_token_threshold: int = 280
    max_artifact_stubs: int = 4
    max_episode_cards: int = 4
    max_open_loops: int = 6
    max_stable_facts: int = 8
    max_decisions: int = 6
    max_active_window_messages: int = 18
    min_recent_messages_pinned: int = 3
    p95_tool_result_tokens: int = 2048
    planned_retrieval_tokens: int = 1536

    # === Token Budget Ratios (extracted from hardcoded values) ===
    # These control how the context window budget is divided
    output_reserve_ratio: float = 0.18  # 18% for model output
    tool_reserve_ratio: float = 0.10  # 10% for tool call results
    safety_margin_ratio: float = 0.05  # 5% safety buffer (Claude Code: max(2048, 0.05C))
    # Minimum reserves to prevent zero-budget edge cases
    output_reserve_min: int = 1024
    tool_reserve_min: int = 512
    safety_margin_min: int = 2048
    retrieval_ratio: float = 0.12  # 12% of input for retrieval
    # Active window budget ratio (for _collect_active_window token allocation)
    active_window_budget_ratio: float = 0.45  # 45% of input_budget for active window

    # === Attention Runtime Feature Switches (A1-A5 rollout) ===
    # Enable deterministic dialog act classification
    enable_dialog_act: bool = True
    # Minimum recent messages to hard-pin in active window
    min_recent_floor: int = 3
    # Prevent episode sealing when pending follow-up exists
    prevent_seal_on_pending: bool = True
    # Enable attention observability debug trace
    enable_attention_trace: bool = True
    # Enable seal guard blocking rules
    enable_seal_guard: bool = True

    @classmethod
    def from_env(cls) -> StateFirstContextOSPolicy:
        """Create a policy instance with environment variable overrides.

        Environment variables follow the pattern:
        KERNELONE_CONTEXT_OS_<FIELD_NAME> (uppercase with underscores)

        Examples:
            KERNELONE_CONTEXT_OS_ENABLE_DIALOG_ACT=false
            KERNELONE_CONTEXT_OS_MAX_OPEN_LOOPS=10
            KERNELONE_CONTEXT_OS_MODEL_CONTEXT_WINDOW=65536

        Returns:
            A new StateFirstContextOSPolicy instance with overrides applied.
        """
        import os
        from dataclasses import replace

        # Map of environment variable names to field names
        env_overrides: dict[str, str] = {
            "KERNELONE_CONTEXT_OS_ENABLE_DIALOG_ACT": "enable_dialog_act",
            "KERNELONE_CONTEXT_OS_PREVENT_SEAL_ON_PENDING": "prevent_seal_on_pending",
            "KERNELONE_CONTEXT_OS_ENABLE_ATTENTION_TRACE": "enable_attention_trace",
            "KERNELONE_CONTEXT_OS_ENABLE_SEAL_GUARD": "enable_seal_guard",
            "KERNELONE_CONTEXT_OS_MIN_RECENT_FLOOR": "min_recent_floor",
            "KERNELONE_CONTEXT_OS_MODEL_CONTEXT_WINDOW": "model_context_window",
            "KERNELONE_CONTEXT_OS_DEFAULT_HISTORY_WINDOW_MESSAGES": "default_history_window_messages",
            "KERNELONE_CONTEXT_OS_MAX_ACTIVE_WINDOW_MESSAGES": "max_active_window_messages",
            "KERNELONE_CONTEXT_OS_MIN_RECENT_MESSAGES_PINNED": "min_recent_messages_pinned",
            "KERNELONE_CONTEXT_OS_MAX_OPEN_LOOPS": "max_open_loops",
            "KERNELONE_CONTEXT_OS_MAX_STABLE_FACTS": "max_stable_facts",
            "KERNELONE_CONTEXT_OS_MAX_DECISIONS": "max_decisions",
            "KERNELONE_CONTEXT_OS_MAX_ARTIFACT_STUBS": "max_artifact_stubs",
            "KERNELONE_CONTEXT_OS_MAX_EPISODE_CARDS": "max_episode_cards",
            "KERNELONE_CONTEXT_OS_ARTIFACT_CHAR_THRESHOLD": "artifact_char_threshold",
            "KERNELONE_CONTEXT_OS_ARTIFACT_TOKEN_THRESHOLD": "artifact_token_threshold",
            "KERNELONE_CONTEXT_OS_P95_TOOL_RESULT_TOKENS": "p95_tool_result_tokens",
            "KERNELONE_CONTEXT_OS_PLANNED_RETRIEVAL_TOKENS": "planned_retrieval_tokens",
        }

        # Collect overrides
        kwargs: dict[str, bool | int] = {}
        for env_name, field_name in env_overrides.items():
            env_value = os.environ.get(env_name)
            if env_value is None:
                continue

            # Determine the field type and convert
            field_type = type(getattr(StateFirstContextOSPolicy, field_name))
            converted: bool | int
            try:
                if field_type is bool:
                    # Handle string boolean values
                    converted = env_value.lower() in ("true", "1", "yes", "on")
                elif field_type is int:
                    converted = int(env_value)
                else:
                    continue
                kwargs[field_name] = converted
            except (ValueError, TypeError):
                # Silently ignore invalid values to maintain stability
                pass

        # Create instance with overrides using replace()
        if kwargs:
            return replace(cls(), **kwargs)  # type: ignore[arg-type]
        return cls()


class SnapshotSummaryView:
    """Lightweight snapshot summary — avoids full to_dict() serialization.

    Extracts only the fields needed for LLM context injection without
    materializing the full transcript_log (the ~109KB "nuke" problem).
    """

    @staticmethod
    def from_snapshot(snapshot: ContextOSSnapshot) -> dict[str, Any]:
        ws = snapshot.working_state
        ts = ws.task_state if hasattr(ws, "task_state") else None
        return {
            "version": snapshot.version,
            "transcript_events_count": len(snapshot.transcript_log),
            "goal": ts.current_goal.value if ts and ts.current_goal else None,
            "open_loops_count": len(ts.open_loops) if ts else 0,
            "decisions_count": len(ws.decision_log) if hasattr(ws, "decision_log") else 0,
            "artifacts_count": len(snapshot.artifact_store),
            "episodes_count": len(snapshot.episode_store),
            "has_pending_followup": snapshot.pending_followup is not None,
            "content_map_entries": len(getattr(snapshot, "content_map", {})),
        }


# === DEPRECATED: Compatibility aliases for v1 → v2 migration ===
# These aliases allow consumer code to migrate incrementally from v1 to v2.
# The v1 classes themselves are deprecated; use the V2 classes directly when possible.
#
# Migration guide:
#   OLD: from polaris.kernelone.context.context_os.models import ArtifactRecord
#   NEW: from polaris.kernelone.context.context_os.models_v2 import ArtifactRecordV2
#

# Compatibility aliases: re-export V2 classes under V1 names.
# These names are already defined in this module as v1 dataclasses,
# so the re-export triggers an assignment incompatibility in mypy.
# We suppress the error per-line to preserve runtime behavior while
# keeping the file type-check clean.
with contextlib.suppress(ImportError):
    from .models_v2 import (  # type: ignore[assignment]  # v2 classes shadow v1 dataclasses intentionally
        ArtifactRecordV2 as ArtifactRecord,
        BudgetPlanV2 as BudgetPlan,
        ContextOSProjectionV2 as ContextOSProjection,
        ContextOSSnapshotV2 as ContextOSSnapshot,
        ContextSlicePlanV2 as ContextSlicePlan,
        ContextSliceSelectionV2 as ContextSliceSelection,
        DecisionEntryV2 as DecisionEntry,
        DialogActResultV2 as DialogActResult,
        EpisodeCardV2 as EpisodeCard,
        PendingFollowUpV2 as PendingFollowUp,
        RunCardV2 as RunCard,
        StateEntryV2 as StateEntry,
        TaskStateViewV2 as TaskStateView,
        TranscriptEventV2 as TranscriptEvent,
        UserProfileStateV2 as UserProfileState,
        WorkingStateV2 as WorkingState,
    )

__all__ = [
    "ArtifactRecord",
    "BudgetPlan",
    "ContextOSProjection",
    "ContextOSSnapshot",
    "ContextSlicePlan",
    "ContextSliceSelection",
    "DecisionEntry",
    "DialogAct",
    "DialogActResult",
    "DialogActResult",
    "EpisodeCard",
    "PendingFollowUp",
    "RunCard",
    "SnapshotSummaryView",
    "StateEntry",
    "StateFirstContextOSPolicy",
    "TaskStateView",
    "TranscriptEvent",
    "UserProfileState",
    "WorkingState",
]
