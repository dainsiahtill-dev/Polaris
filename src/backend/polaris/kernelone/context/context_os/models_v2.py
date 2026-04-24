"""Pydantic V2 Models - ContextOS 数据模型验证层

ADR-0067: ContextOS 2.0 摘要策略选型 - Layer 5 数据模型与校验

基于 Pydantic V2 的严格数据验证，提供：
- 字段级类型校验
- 嵌套模型验证
- JSON Schema 自动生成
- 性能优化 (重载 __init__)

向后兼容:
- 提供 from_dataclass() 工厂方法
- 提供 to_dataclass() 转换方法
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class DialogAct(str, Enum):
    """Dialog act types for conversation classification.

    These acts represent the semantic function of a user/assistant message
    in the conversational flow, enabling proper attention and intent tracking.
    """

    AFFIRM = "affirm"
    DENY = "deny"
    PAUSE = "pause"
    REDIRECT = "redirect"
    CLARIFY = "clarify"
    COMMIT = "commit"
    CANCEL = "cancel"
    STATUS_ACK = "status_ack"
    NOISE = "noise"
    UNKNOWN = "unknown"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        return tuple(member.value for member in cls)

    @classmethod
    def is_high_priority(cls, act: str) -> bool:
        """High-priority dialog acts that should never be treated as low-signal."""
        return act in {
            cls.AFFIRM,
            cls.DENY,
            cls.PAUSE,
            cls.REDIRECT,
            cls.CLARIFY,
            cls.COMMIT,
            cls.CANCEL,
        }


class TranscriptEventV2(BaseModel):
    """TranscriptEvent V2 - Pydantic 验证版本"""

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
        str_strip_whitespace=True,
    )

    event_id: str = Field(..., min_length=1)
    sequence: int = Field(..., ge=0)
    role: str = Field(default="")
    kind: str = Field(default="")
    route: str = Field(default="")
    content: str = Field(default="")
    source_turns: tuple[str, ...] = Field(default_factory=tuple)
    artifact_id: str | None = None
    created_at: str = Field(default="")
    metadata: tuple[tuple[str, Any], ...] = Field(default_factory=tuple)
    content_ref_hash: str = Field(default="")
    content_ref_size: int = Field(default=0)
    content_ref_mime: str = Field(default="")
    content_ref_encoding: str = Field(default="utf-8")

    @field_validator("sequence", mode="before")
    @classmethod
    def validate_sequence(cls, v: Any) -> int:
        if isinstance(v, str):
            return int(v)
        return int(v)

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, v: Any) -> tuple[tuple[str, Any], ...]:
        if isinstance(v, dict):
            return tuple(sorted(v.items()))
        if not isinstance(v, (list, tuple)):
            return ()
        return tuple(v)

    @model_validator(mode="before")
    @classmethod
    def handle_dict_metadata(cls, data: Any) -> Any:
        if isinstance(data, dict) and "metadata" in data:
            metadata = data["metadata"]
            if isinstance(metadata, dict):
                data["metadata"] = tuple(sorted(metadata.items()))
        return data

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
        if self.content_ref_hash:
            d["content_ref"] = {
                "hash": self.content_ref_hash,
                "size": self.content_ref_size,
                "mime": self.content_ref_mime,
                "encoding": self.content_ref_encoding,
            }
        return d


class ArtifactRecordV2(BaseModel):
    """ArtifactRecord V2 - Pydantic 验证版本"""

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
        str_strip_whitespace=True,
    )

    artifact_id: str = Field(..., min_length=1)
    artifact_type: str = Field(default="")
    mime_type: str = Field(default="")
    token_count: int = Field(default=0, ge=0)
    char_count: int = Field(default=0, ge=0)
    peek: str = Field(default="")
    keys: tuple[str, ...] = Field(default_factory=tuple)
    content: str = Field(default="")
    source_event_ids: tuple[str, ...] = Field(default_factory=tuple)
    restore_tool: str = Field(default="read_artifact")
    metadata: tuple[tuple[str, Any], ...] = Field(default_factory=tuple)

    @field_validator("token_count", "char_count", mode="before")
    @classmethod
    def validate_counts(cls, v: Any) -> int:
        if isinstance(v, str):
            return int(v)
        return int(v)

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, v: Any) -> tuple[tuple[str, Any], ...]:
        if isinstance(v, dict):
            return tuple(sorted(v.items()))
        if not isinstance(v, (list, tuple)):
            return ()
        return tuple(v)

    def to_stub(self) -> dict[str, Any]:
        """Serialize to stub format for artifact_stubs in ContextOSProjection."""
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

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
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
        return d


class BudgetPlanV2(BaseModel):
    """BudgetPlan V2 - Pydantic 验证版本"""

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
    )

    model_context_window: int = Field(default=128000, ge=0)
    output_reserve: int = Field(default=0, ge=0)
    tool_reserve: int = Field(default=0, ge=0)
    safety_margin: int = Field(default=0, ge=0)
    input_budget: int = Field(default=0, ge=0)
    retrieval_budget: int = Field(default=0, ge=0)
    soft_limit: int = Field(default=0, ge=0)
    hard_limit: int = Field(default=0, ge=0)
    emergency_limit: int = Field(default=0, ge=0)
    current_input_tokens: int = Field(default=0, ge=0)
    expected_next_input_tokens: int = Field(default=0)
    p95_tool_result_tokens: int = Field(default=0, ge=0)
    planned_retrieval_tokens: int = Field(default=0, ge=0)
    validation_error: str = Field(default="")

    @field_validator("*", mode="before")
    @classmethod
    def validate_int_fields(cls, value: Any, info: Any) -> Any:
        field_type = cls.model_fields.get(info.field_name)
        if field_type and field_type.annotation is int and isinstance(value, str):
            try:
                return int(value)
            except ValueError as exc:
                raise ValueError(f"Field {info.field_name} must be an integer, got {value!r}") from exc
        return value

    def validate_invariants(self) -> BudgetPlanV2:
        """验证 BudgetPlan 不变式"""
        if self.expected_next_input_tokens > self.model_context_window:
            overrun = self.expected_next_input_tokens - self.model_context_window
            from polaris.kernelone.errors import BudgetExceededError

            raise BudgetExceededError(
                f"BudgetPlan invariant violated: expected_next_input_tokens "
                f"({self.expected_next_input_tokens}) exceeds model_context_window "
                f"({self.model_context_window}) by {overrun} tokens",
                limit=self.model_context_window,
                requested=self.expected_next_input_tokens,
                current=self.current_input_tokens,
            )
        return self

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict matching the original dataclass format."""
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

    def to_context_window_status_event(self) -> Any:
        """Create ContextWindowStatus event for telemetry."""
        from polaris.kernelone.events.typed import ContextWindowStatus

        return ContextWindowStatus.create(
            current_tokens=self.current_input_tokens,
            max_tokens=self.model_context_window,
            segment_breakdown=None,
            critical_threshold=80.0,
            run_id="",
            workspace="",
        )


class EpisodeCardV2(BaseModel):
    """EpisodeCard V2 - Pydantic 验证版本"""

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
        str_strip_whitespace=True,
    )

    episode_id: str = Field(..., min_length=1)
    from_sequence: int = Field(default=0, ge=0)
    to_sequence: int = Field(default=0, ge=0)
    intent: str = Field(default="")
    outcome: str = Field(default="")
    decisions: tuple[str, ...] = Field(default_factory=tuple)
    facts: tuple[str, ...] = Field(default_factory=tuple)
    artifact_refs: tuple[str, ...] = Field(default_factory=tuple)
    entities: tuple[str, ...] = Field(default_factory=tuple)
    reopen_conditions: tuple[str, ...] = Field(default_factory=tuple)
    source_spans: tuple[str, ...] = Field(default_factory=tuple)
    digest_64: str = Field(default="")
    digest_256: str = Field(default="")
    digest_1k: str = Field(default="")
    sealed_at: float = Field(default=0.0, ge=0)
    status: str = Field(default="sealed")
    reopened_at: str = Field(default="")
    reopen_reason: str = Field(default="")

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


class StateEntryV2(BaseModel):
    """StateEntry V2 - Pydantic 验证版本"""

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
        str_strip_whitespace=True,
    )

    entry_id: str = Field(..., min_length=1)
    path: str = Field(default="")
    value: str = Field(default="")
    source_turns: tuple[str, ...] = Field(default_factory=tuple)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    updated_at: str = Field(default="")
    supersedes: str | None = None

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, v: Any) -> float:
        if isinstance(v, str):
            return float(v)
        return float(v)

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
        return d

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> StateEntryV2 | None:
        if not isinstance(payload, dict):
            return None
        source_turns_raw = payload.get("source_turns", [])
        return cls(
            entry_id=str(payload.get("entry_id") or "").strip() or "unknown",
            path=str(payload.get("path") or "").strip(),
            value=str(payload.get("value") or ""),
            source_turns=tuple(
                v for v in (source_turns_raw if isinstance(source_turns_raw, (list, tuple)) else []) if v
            ),
            confidence=float(payload.get("confidence", 0.0)),
            updated_at=str(payload.get("updated_at") or "").strip(),
            supersedes=payload.get("supersedes"),
        )


class WorkingStateV2(BaseModel):
    """WorkingState V2 - Pydantic 验证版本"""

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
    )

    user_profile: UserProfileStateV2 = Field(default_factory=lambda: UserProfileStateV2())
    task_state: TaskStateViewV2 = Field(default_factory=lambda: TaskStateViewV2())
    decision_log: tuple[DecisionEntryV2, ...] = Field(default_factory=tuple)
    active_entities: tuple[StateEntryV2, ...] = Field(default_factory=tuple)
    active_artifacts: tuple[str, ...] = Field(default_factory=tuple)
    temporal_facts: tuple[StateEntryV2, ...] = Field(default_factory=tuple)
    state_history: tuple[StateEntryV2, ...] = Field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_profile": self.user_profile.to_dict() if hasattr(self.user_profile, "to_dict") else {},
            "task_state": self.task_state.to_dict() if hasattr(self.task_state, "to_dict") else {},
            "decision_log": [item.to_dict() for item in self.decision_log],
            "active_entities": [item.to_dict() for item in self.active_entities],
            "active_artifacts": list(self.active_artifacts),
            "temporal_facts": [item.to_dict() for item in self.temporal_facts],
            "state_history": [item.to_dict() for item in self.state_history],
        }

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> WorkingStateV2:
        """Create WorkingStateV2 from dict payload, compatible with v1 API."""
        if not isinstance(payload, dict):
            return cls()
        from .models_v2 import (
            DecisionEntryV2,
            StateEntryV2,
            TaskStateViewV2,
            UserProfileStateV2,
        )

        def _parse_state_entry(v: dict[str, Any] | None) -> StateEntryV2 | None:
            return StateEntryV2.from_mapping(v) if isinstance(v, dict) else None

        def _parse_decision_entry(v: dict[str, Any] | None) -> DecisionEntryV2 | None:
            return DecisionEntryV2.from_mapping(v) if isinstance(v, dict) else None

        ws_payload = payload if payload else {}
        user_profile_payload = ws_payload.get("user_profile", {})
        task_state_payload = ws_payload.get("task_state", {})

        return cls(
            user_profile=UserProfileStateV2(
                preferences=tuple(
                    s
                    for s in (_parse_state_entry(v) for v in user_profile_payload.get("preferences", []))
                    if s is not None
                ),
                style=tuple(
                    s for s in (_parse_state_entry(v) for v in user_profile_payload.get("style", [])) if s is not None
                ),
                persistent_facts=tuple(
                    s
                    for s in (_parse_state_entry(v) for v in user_profile_payload.get("persistent_facts", []))
                    if s is not None
                ),
            ),
            task_state=TaskStateViewV2(
                current_goal=_parse_state_entry(task_state_payload.get("current_goal")),
                accepted_plan=tuple(
                    s
                    for s in (_parse_state_entry(v) for v in task_state_payload.get("accepted_plan", []))
                    if s is not None
                ),
                open_loops=tuple(
                    s
                    for s in (_parse_state_entry(v) for v in task_state_payload.get("open_loops", []))
                    if s is not None
                ),
                blocked_on=tuple(
                    s
                    for s in (_parse_state_entry(v) for v in task_state_payload.get("blocked_on", []))
                    if s is not None
                ),
                deliverables=tuple(
                    s
                    for s in (_parse_state_entry(v) for v in task_state_payload.get("deliverables", []))
                    if s is not None
                ),
            ),
            decision_log=tuple(
                s for s in (_parse_decision_entry(v) for v in ws_payload.get("decision_log", [])) if s is not None
            ),
            active_entities=tuple(
                s for s in (_parse_state_entry(v) for v in ws_payload.get("active_entities", [])) if s is not None
            ),
            active_artifacts=tuple(v for v in ws_payload.get("active_artifacts", []) if v),
            temporal_facts=tuple(
                s for s in (_parse_state_entry(v) for v in ws_payload.get("temporal_facts", [])) if s is not None
            ),
            state_history=tuple(
                s for s in (_parse_state_entry(v) for v in ws_payload.get("state_history", [])) if s is not None
            ),
        )


class DecisionEntryV2(BaseModel):
    """DecisionEntry V2 - Pydantic 验证版本"""

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
        str_strip_whitespace=True,
    )

    decision_id: str = Field(..., min_length=1)
    summary: str = Field(default="")
    source_turns: tuple[str, ...] = Field(default_factory=tuple)
    updated_at: str = Field(default="")
    kind: str = Field(default="decision")
    supersedes: str | None = None
    basis_refs: tuple[str, ...] = Field(default_factory=tuple)

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

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> DecisionEntryV2 | None:
        if not isinstance(payload, dict):
            return None
        source_turns_raw = payload.get("source_turns", [])
        basis_refs_raw = payload.get("basis_refs", [])
        return cls(
            decision_id=str(payload.get("decision_id") or payload.get("entry_id") or "").strip() or "unknown",
            summary=str(payload.get("summary") or payload.get("value") or ""),
            source_turns=tuple(
                v for v in (source_turns_raw if isinstance(source_turns_raw, (list, tuple)) else []) if v
            ),
            updated_at=str(payload.get("updated_at") or "").strip(),
            kind=str(payload.get("kind") or "decision").strip(),
            supersedes=payload.get("supersedes"),
            basis_refs=tuple(v for v in (basis_refs_raw if isinstance(basis_refs_raw, (list, tuple)) else []) if v),
        )


class UserProfileStateV2(BaseModel):
    """UserProfileState V2"""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    preferences: tuple[StateEntryV2, ...] = Field(default_factory=tuple)
    style: tuple[StateEntryV2, ...] = Field(default_factory=tuple)
    persistent_facts: tuple[StateEntryV2, ...] = Field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "preferences": [item.to_dict() for item in self.preferences],
            "style": [item.to_dict() for item in self.style],
            "persistent_facts": [item.to_dict() for item in self.persistent_facts],
        }


class TaskStateViewV2(BaseModel):
    """TaskStateView V2"""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    current_goal: StateEntryV2 | None = None
    accepted_plan: tuple[StateEntryV2, ...] = Field(default_factory=tuple)
    open_loops: tuple[StateEntryV2, ...] = Field(default_factory=tuple)
    blocked_on: tuple[StateEntryV2, ...] = Field(default_factory=tuple)
    deliverables: tuple[StateEntryV2, ...] = Field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_goal": self.current_goal.to_dict() if self.current_goal is not None else None,
            "accepted_plan": [item.to_dict() for item in self.accepted_plan],
            "open_loops": [item.to_dict() for item in self.open_loops],
            "blocked_on": [item.to_dict() for item in self.blocked_on],
            "deliverables": [item.to_dict() for item in self.deliverables],
        }


class ContextOSSnapshotV2(BaseModel):
    """ContextOSSnapshot V2 - Pydantic 验证版本"""

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
    )

    version: int = Field(default=1, ge=1)
    mode: str = Field(default="state_first_context_os_v1")
    adapter_id: str = Field(default="generic")
    transcript_log: tuple[TranscriptEventV2, ...] = Field(default_factory=tuple)
    working_state: WorkingStateV2 = Field(default_factory=WorkingStateV2)
    artifact_store: tuple[ArtifactRecordV2, ...] = Field(default_factory=tuple)
    episode_store: tuple[EpisodeCardV2, ...] = Field(default_factory=tuple)
    budget_plan: BudgetPlanV2 | None = None
    updated_at: str = Field(default="")
    pending_followup: PendingFollowUpV2 | None = None

    def to_dict(self, receipt_store: Any | None = None) -> dict[str, Any]:
        """Serialize to dict with optional auto-archive for large transcripts.

        If total transcript content > 5000 chars and no receipt_store provided,
        creates an internal ReceiptStore and replaces large content with refs.
        """
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
            "content_map": {},
        }

        if owns_receipt_store:
            assert receipt_store is not None
            result["_receipt_store_export"] = receipt_store.export_receipts()

        return result

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> ContextOSSnapshotV2 | None:
        """Deserialize from dict, restoring content from receipt refs if present."""
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

        # Parse transcript_log
        transcript_log: list[TranscriptEventV2] = []
        for v in transcript_log_data:
            try:
                transcript_log.append(TranscriptEventV2.model_validate(v))
            except Exception:  # noqa: BLE001
                # Fallback: construct from raw fields
                evt = TranscriptEventV2(
                    event_id=str(v.get("event_id", "")),
                    sequence=int(v.get("sequence", 0)),
                    role=str(v.get("role", "")),
                    kind=str(v.get("kind", "")),
                    route=str(v.get("route", "")),
                    content=str(v.get("content", "")),
                    source_turns=tuple(v.get("source_turns", [])),
                    artifact_id=v.get("artifact_id"),
                    created_at=str(v.get("created_at", "")),
                )
                transcript_log.append(evt)

        # Parse working_state
        ws_payload = payload.get("working_state")
        if ws_payload is None:
            working_state = WorkingStateV2()
        else:
            working_state = WorkingStateV2(
                user_profile=UserProfileStateV2(
                    preferences=tuple(
                        s
                        for s in (
                            StateEntryV2.from_mapping(v)
                            for v in ws_payload.get("user_profile", {}).get("preferences", [])
                        )
                        if s is not None
                    ),
                    style=tuple(
                        s
                        for s in (
                            StateEntryV2.from_mapping(v) for v in ws_payload.get("user_profile", {}).get("style", [])
                        )
                        if s is not None
                    ),
                    persistent_facts=tuple(
                        s
                        for s in (
                            StateEntryV2.from_mapping(v)
                            for v in ws_payload.get("user_profile", {}).get("persistent_facts", [])
                        )
                        if s is not None
                    ),
                ),
                task_state=TaskStateViewV2(
                    current_goal=StateEntryV2.from_mapping(ws_payload.get("task_state", {}).get("current_goal"))
                    if ws_payload.get("task_state", {}).get("current_goal")
                    else None,
                    open_loops=tuple(
                        s
                        for s in (
                            StateEntryV2.from_mapping(v) for v in ws_payload.get("task_state", {}).get("open_loops", [])
                        )
                        if s is not None
                    ),
                    blocked_on=tuple(
                        s
                        for s in (
                            StateEntryV2.from_mapping(v) for v in ws_payload.get("task_state", {}).get("blocked_on", [])
                        )
                        if s is not None
                    ),
                ),
            )

        # Parse budget_plan
        bp_payload = payload.get("budget_plan")
        budget_plan = BudgetPlanV2.model_validate(bp_payload) if bp_payload else None

        # Parse pending_followup
        pf_payload = payload.get("pending_followup")
        pending_followup = PendingFollowUpV2.model_validate(pf_payload) if pf_payload else None

        return cls(
            version=max(1, int(payload.get("version", 1))),
            mode=str(payload.get("mode") or "state_first_context_os_v1").strip(),
            adapter_id=str(payload.get("adapter_id") or "generic").strip() or "generic",
            transcript_log=tuple(transcript_log),
            working_state=working_state,
            artifact_store=tuple(ArtifactRecordV2.model_validate(v) for v in payload.get("artifact_store", [])),
            episode_store=tuple(EpisodeCardV2.model_validate(v) for v in payload.get("episode_store", [])),
            budget_plan=budget_plan,
            updated_at=str(payload.get("updated_at") or "").strip(),
            pending_followup=pending_followup,
        )


class PendingFollowUpV2(BaseModel):
    """PendingFollowUp V2"""

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
        str_strip_whitespace=True,
    )

    action: str = Field(default="")
    source_event_id: str = Field(default="")
    source_sequence: int = Field(default=0, ge=0)
    status: str = Field(default="pending")
    updated_at: str = Field(default="")

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
        return self.status == "pending"


class RunCardV2(BaseModel):
    """RunCard V2"""

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
        str_strip_whitespace=True,
    )

    current_goal: str = Field(default="")
    hard_constraints: tuple[str, ...] = Field(default_factory=tuple)
    open_loops: tuple[str, ...] = Field(default_factory=tuple)
    active_entities: tuple[str, ...] = Field(default_factory=tuple)
    active_artifacts: tuple[str, ...] = Field(default_factory=tuple)
    recent_decisions: tuple[str, ...] = Field(default_factory=tuple)
    next_action_hint: str = Field(default="")
    latest_user_intent: str = Field(default="")
    pending_followup_action: str = Field(default="")
    pending_followup_status: str = Field(default="")
    last_turn_outcome: str = Field(default="")

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_goal": self.current_goal,
            "hard_constraints": list(self.hard_constraints),
            "open_loops": list(self.open_loops),
            "active_entities": list(self.active_entities),
            "active_artifacts": list(self.active_artifacts),
            "recent_decisions": list(self.recent_decisions),
            "next_action_hint": self.next_action_hint,
            "latest_user_intent": self.latest_user_intent,
            "pending_followup_action": self.pending_followup_action,
            "pending_followup_status": self.pending_followup_status,
            "last_turn_outcome": self.last_turn_outcome,
        }

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> RunCardV2 | None:
        if not isinstance(payload, dict):
            return None
        hard_constraints_raw = payload.get("hard_constraints", [])
        open_loops_raw = payload.get("open_loops", [])
        active_entities_raw = payload.get("active_entities", [])
        active_artifacts_raw = payload.get("active_artifacts", [])
        recent_decisions_raw = payload.get("recent_decisions", [])
        return cls(
            current_goal=str(payload.get("current_goal") or "").strip(),
            hard_constraints=tuple(
                v for v in (hard_constraints_raw if isinstance(hard_constraints_raw, (list, tuple)) else []) if v
            ),
            open_loops=tuple(v for v in (open_loops_raw if isinstance(open_loops_raw, (list, tuple)) else []) if v),
            active_entities=tuple(
                v for v in (active_entities_raw if isinstance(active_entities_raw, (list, tuple)) else []) if v
            ),
            active_artifacts=tuple(
                v for v in (active_artifacts_raw if isinstance(active_artifacts_raw, (list, tuple)) else []) if v
            ),
            recent_decisions=tuple(
                v for v in (recent_decisions_raw if isinstance(recent_decisions_raw, (list, tuple)) else []) if v
            ),
            next_action_hint=str(payload.get("next_action_hint") or "").strip(),
            latest_user_intent=str(payload.get("latest_user_intent") or "").strip(),
            pending_followup_action=str(payload.get("pending_followup_action") or "").strip(),
            pending_followup_status=str(payload.get("pending_followup_status") or "").strip(),
            last_turn_outcome=str(payload.get("last_turn_outcome") or "").strip(),
        )


class RoutingClassEnum(str, Enum):
    """Routing class for transcript event processing."""

    CLEAR = "clear"
    PATCH = "patch"
    ARCHIVE = "archive"
    SUMMARIZE = "summarize"


class DialogActResultV2(BaseModel):
    """Result of dialog act classification."""

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
        str_strip_whitespace=True,
    )

    act: str = Field(default=DialogAct.UNKNOWN)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    triggers: tuple[str, ...] = Field(default_factory=tuple)
    metadata: tuple[tuple[str, Any], ...] = Field(default_factory=tuple)

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, v: Any) -> tuple[tuple[str, Any], ...]:
        if isinstance(v, dict):
            return tuple(sorted(v.items()))
        if not isinstance(v, (list, tuple)):
            return ()
        return tuple(v)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for API responses."""
        return {
            "act": self.act,
            "confidence": self.confidence,
            "triggers": list(self.triggers),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> DialogActResultV2:
        if not isinstance(payload, dict):
            return cls()
        return cls(
            act=str(payload.get("act") or "").strip() or DialogAct.UNKNOWN,
            confidence=max(0.0, min(1.0, float(payload.get("confidence", 0.0)))),
            triggers=tuple(v for v in (payload.get("triggers") or []) if v),
            metadata=payload.get("metadata", {}),  # type: ignore[arg-type]
        )


class ContextSliceSelectionV2(BaseModel):
    """Selection criteria for context slice."""

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
        str_strip_whitespace=True,
    )

    selection_type: str = Field(default="")
    ref: str = Field(default="")
    reason: str = Field(default="")

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> ContextSliceSelectionV2 | None:
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


class ContextSlicePlanV2(BaseModel):
    """Plan for context slice selection."""

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
        str_strip_whitespace=True,
    )

    plan_id: str = Field(default="")
    budget_tokens: int = Field(default=0, ge=0)
    roots: tuple[str, ...] = Field(default_factory=tuple)
    included: tuple[ContextSliceSelectionV2, ...] = Field(default_factory=tuple)
    excluded: tuple[ContextSliceSelectionV2, ...] = Field(default_factory=tuple)
    pressure_level: str = Field(default="normal")

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> ContextSlicePlanV2 | None:
        if not isinstance(payload, dict):
            return None
        included = tuple(
            v
            for v in (ContextSliceSelectionV2.from_mapping(item) for item in payload.get("included", []))
            if v is not None
        )
        excluded = tuple(
            v
            for v in (ContextSliceSelectionV2.from_mapping(item) for item in payload.get("excluded", []))
            if v is not None
        )
        roots_raw = payload.get("roots", [])
        roots = tuple(v for v in (roots_raw if isinstance(roots_raw, (list, tuple)) else []) if v)
        return cls(
            plan_id=str(payload.get("plan_id") or "").strip(),
            budget_tokens=max(0, int(payload.get("budget_tokens", 0))),
            roots=roots,
            included=included,
            excluded=excluded,
            pressure_level=str(payload.get("pressure_level", "normal")).strip() or "normal",
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


class ContextOSProjectionV2(BaseModel):
    """ContextOSProjection V2"""

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
    )

    snapshot: ContextOSSnapshotV2
    head_anchor: str = Field(default="")
    tail_anchor: str = Field(default="")
    active_window: tuple[TranscriptEventV2, ...] = Field(default_factory=tuple)
    artifact_stubs: tuple[ArtifactRecordV2, ...] = Field(default_factory=tuple)
    episode_cards: tuple[EpisodeCardV2, ...] = Field(default_factory=tuple)
    run_card: RunCardV2 | None = None
    context_slice_plan: ContextSlicePlanV2 | None = None
    # Phase 1.5: Structured findings for cognitive continuity across turns
    structured_findings: dict[str, Any] | None = None

    def to_prompt_dict(self, filter_control_plane: bool = True) -> list[dict[str, Any]]:
        """Generate LLM messages, optionally filtering control-plane messages.

        Args:
            filter_control_plane: If True, exclude messages with metadata.plane == "control"
        """
        messages: list[dict[str, Any]] = []
        for item in self.active_window:
            if filter_control_plane:
                metadata_dict = dict(item.metadata) if item.metadata else {}
                if metadata_dict.get("plane") == "control":
                    continue
            messages.append(
                {
                    "role": item.role,
                    "content": item.content,
                }
            )
        return messages

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


# 更新 forward references
EpisodeCardV2.model_rebuild()
WorkingStateV2.model_rebuild()
ContextOSSnapshotV2.model_rebuild()
