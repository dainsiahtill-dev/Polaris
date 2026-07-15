"""Execution / session / status / aggregate / stream / event contracts.

Execution, session, status, aggregate-plan, stream, and event contract
dataclasses for roles.runtime, plus ``RoleRuntimeError`` and the
``IRoleRuntime`` Protocol. This module is independent of the object-
composition core and depends only on the foundation validation helpers.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from polaris.cells.roles.runtime.public.contracts._validation import (
    _normalize_history,
    _normalize_optional_domain,
    _normalize_string_tuple,
    _require_non_empty,
    _to_dict_copy,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    TaskRuntimeExecutionAttemptIdentityV1,
)


@dataclass(frozen=True)
class ExecuteRoleTaskCommandV1:
    """Execute one role task under the runtime role kernel.

    ``session_id`` is TaskRuntime execution-attempt authority for this command,
    never a role-chat session identifier.
    """

    role: str
    task_id: str
    workspace: str
    objective: str
    run_id: str | None = None
    session_id: str | None = None
    domain: str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    timeout_seconds: int | None = None
    stream: bool = False
    host_kind: str | None = None  # Task #2: unified host protocol
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "objective", _require_non_empty("objective", self.objective))
        object.__setattr__(self, "domain", _normalize_optional_domain(self.domain))
        object.__setattr__(self, "context", _to_dict_copy(self.context))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        forbidden_metadata_keys = {
            "task_runtime_session_id",
            "task_runtime_execution_attempt",
        }
        supplied_authority_keys = forbidden_metadata_keys.intersection(self.metadata)
        if supplied_authority_keys:
            raise ValueError(
                "TaskRuntime execution authority must use typed execution_attempt, not metadata: "
                f"{sorted(supplied_authority_keys)!r}"
            )
        if self.execution_attempt is not None and not isinstance(
            self.execution_attempt,
            TaskRuntimeExecutionAttemptIdentityV1,
        ):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1 or None")
        if self.execution_attempt is not None:
            supplied_session_id = (
                _require_non_empty("session_id", self.session_id) if self.session_id is not None else None
            )
            canonical_session_id = self.execution_attempt.session_id
            if supplied_session_id is not None and supplied_session_id != canonical_session_id:
                raise ValueError("session_id must equal execution_attempt.session_id")
            object.__setattr__(self, "session_id", canonical_session_id)
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0 when provided")


@dataclass(frozen=True)
class ExecuteRoleSessionCommandV1:
    """Execute one user turn on an existing role session."""

    role: str
    session_id: str
    workspace: str
    user_message: str
    run_id: str | None = None
    task_id: str | None = None
    domain: str | None = None
    history: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    stream: bool = True
    stream_options: StreamTurnOptions | None = None
    host_kind: str | None = None  # Task #2: unified host protocol
    timeout_seconds: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "session_id", _require_non_empty("session_id", self.session_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "user_message", _require_non_empty("user_message", self.user_message))
        object.__setattr__(self, "domain", _normalize_optional_domain(self.domain))
        object.__setattr__(self, "history", _normalize_history(self.history))
        object.__setattr__(self, "context", _to_dict_copy(self.context))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if self.stream_options is not None and not isinstance(self.stream_options, StreamTurnOptions):
            raise TypeError("stream_options must be a StreamTurnOptions instance")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0 when provided")


@dataclass(frozen=True)
class GetRoleRuntimeStatusQueryV1:
    """Query role runtime health/status for one workspace."""

    workspace: str
    role: str | None = None
    include_agent_health: bool = True
    include_queue: bool = True
    include_tools: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.role is not None:
            object.__setattr__(self, "role", _require_non_empty("role", self.role))


@dataclass(frozen=True)
class BuildAggregateRolePlanQueryV1:
    """Build a deterministic role-lobe plan for an aggregate model wrapper.

    This is a query-only contract. It does not execute roles, call an LLM, or
    mutate runtime state; callers use the result to decide how to compose role
    turns behind a single external model-like interface.
    """

    workspace: str
    objective: str
    role_ids: tuple[str, ...] = field(default_factory=tuple)
    failure_signals: tuple[str, ...] = field(default_factory=tuple)
    failure_evidence: Mapping[str, Any] = field(default_factory=dict)
    domain: str | None = None
    include_virtual_lobes: bool = True
    context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "objective", _require_non_empty("objective", self.objective))
        object.__setattr__(self, "role_ids", _normalize_string_tuple("role_ids", self.role_ids))
        object.__setattr__(self, "failure_signals", _normalize_string_tuple("failure_signals", self.failure_signals))
        object.__setattr__(self, "failure_evidence", _to_dict_copy(self.failure_evidence))
        object.__setattr__(self, "domain", _normalize_optional_domain(self.domain))
        object.__setattr__(self, "context", _to_dict_copy(self.context))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class AuditAggregateRuntimeIntegrationsQueryV1:
    """Audit aggregate-model integrations against current runtime entrypoints."""

    workspace: str
    role_ids: tuple[str, ...] = field(default_factory=tuple)
    include_virtual_lobes: bool = True
    context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "role_ids", _normalize_string_tuple("role_ids", self.role_ids))
        object.__setattr__(self, "context", _to_dict_copy(self.context))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class AggregateRoleLobeV1:
    """One internal functional lobe of a Polaris aggregate role plan."""

    lobe_id: str
    title: str
    phase: str
    role_ids: tuple[str, ...]
    virtual_role_ids: tuple[str, ...]
    capability_refs: tuple[str, ...]
    attention_masks: tuple[str, ...]
    memory_triggers: tuple[str, ...]
    compute_tier: str
    handoff_keys: tuple[str, ...]
    takeover_triggers: tuple[str, ...]
    output_contract: str
    status: str = "active"
    missing_role_ids: tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "lobe_id", _require_non_empty("lobe_id", self.lobe_id))
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "phase", _require_non_empty("phase", self.phase))
        object.__setattr__(self, "role_ids", _normalize_string_tuple("role_ids", self.role_ids))
        object.__setattr__(self, "virtual_role_ids", _normalize_string_tuple("virtual_role_ids", self.virtual_role_ids))
        object.__setattr__(self, "capability_refs", _normalize_string_tuple("capability_refs", self.capability_refs))
        object.__setattr__(self, "attention_masks", _normalize_string_tuple("attention_masks", self.attention_masks))
        object.__setattr__(self, "memory_triggers", _normalize_string_tuple("memory_triggers", self.memory_triggers))
        object.__setattr__(self, "compute_tier", _require_non_empty("compute_tier", self.compute_tier))
        object.__setattr__(self, "handoff_keys", _normalize_string_tuple("handoff_keys", self.handoff_keys))
        object.__setattr__(
            self,
            "takeover_triggers",
            _normalize_string_tuple("takeover_triggers", self.takeover_triggers),
        )
        object.__setattr__(self, "output_contract", _require_non_empty("output_contract", self.output_contract))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "missing_role_ids", _normalize_string_tuple("missing_role_ids", self.missing_role_ids))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class AggregateCognitiveLedgerEntryV1:
    """One internal state handoff in the aggregate model plan."""

    sequence: int
    lobe_id: str
    phase: str
    compute_tier: str
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    handoff_to: tuple[str, ...]
    takeover_triggers: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("sequence must be >= 0")
        object.__setattr__(self, "lobe_id", _require_non_empty("lobe_id", self.lobe_id))
        object.__setattr__(self, "phase", _require_non_empty("phase", self.phase))
        object.__setattr__(self, "compute_tier", _require_non_empty("compute_tier", self.compute_tier))
        object.__setattr__(self, "reads", _normalize_string_tuple("reads", self.reads))
        object.__setattr__(self, "writes", _normalize_string_tuple("writes", self.writes))
        object.__setattr__(self, "handoff_to", _normalize_string_tuple("handoff_to", self.handoff_to))
        object.__setattr__(
            self,
            "takeover_triggers",
            _normalize_string_tuple("takeover_triggers", self.takeover_triggers),
        )


@dataclass(frozen=True)
class AggregateTakeoverDirectiveV1:
    """Planned internal lobe takeover for an observed failure signal."""

    trigger: str
    lobe_id: str
    compute_tier: str
    reason: str
    evidence_keys: tuple[str, ...]
    action_contract: str
    next_lobes: tuple[str, ...]
    status: str = "planned"

    def __post_init__(self) -> None:
        object.__setattr__(self, "trigger", _require_non_empty("trigger", self.trigger))
        object.__setattr__(self, "lobe_id", _require_non_empty("lobe_id", self.lobe_id))
        object.__setattr__(self, "compute_tier", _require_non_empty("compute_tier", self.compute_tier))
        object.__setattr__(self, "reason", _require_non_empty("reason", self.reason))
        object.__setattr__(self, "evidence_keys", _normalize_string_tuple("evidence_keys", self.evidence_keys))
        object.__setattr__(self, "action_contract", _require_non_empty("action_contract", self.action_contract))
        object.__setattr__(self, "next_lobes", _normalize_string_tuple("next_lobes", self.next_lobes))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))


@dataclass(frozen=True)
class AggregateRuntimeEntrypointCheckV1:
    """Runtime-verifiable production entrypoint evidence for one integration."""

    entrypoint: str
    check_type: str
    ok: bool
    evidence: str
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "entrypoint", _require_non_empty("entrypoint", self.entrypoint))
        object.__setattr__(self, "check_type", _require_non_empty("check_type", self.check_type))
        object.__setattr__(self, "evidence", _require_non_empty("evidence", self.evidence))
        if self.reason:
            object.__setattr__(self, "reason", str(self.reason).strip())


@dataclass(frozen=True)
class AggregateRuntimeIntegrationV1:
    """One auditable Polaris-unique technology mapped to runtime entrypoints."""

    tech_id: str
    title: str
    status: str
    priority: str
    production_entrypoints: tuple[str, ...]
    trigger_keys: tuple[str, ...]
    evidence_keys: tuple[str, ...]
    runtime_effects: tuple[str, ...]
    benefit: str
    capability_refs: tuple[str, ...] = field(default_factory=tuple)
    entrypoint_checks: tuple[AggregateRuntimeEntrypointCheckV1, ...] = field(default_factory=tuple)
    entrypoints_verified: bool = False
    missing_entrypoints: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tech_id", _require_non_empty("tech_id", self.tech_id))
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "priority", _require_non_empty("priority", self.priority))
        object.__setattr__(
            self,
            "production_entrypoints",
            _normalize_string_tuple("production_entrypoints", self.production_entrypoints),
        )
        object.__setattr__(self, "trigger_keys", _normalize_string_tuple("trigger_keys", self.trigger_keys))
        object.__setattr__(self, "evidence_keys", _normalize_string_tuple("evidence_keys", self.evidence_keys))
        object.__setattr__(self, "runtime_effects", _normalize_string_tuple("runtime_effects", self.runtime_effects))
        object.__setattr__(self, "benefit", _require_non_empty("benefit", self.benefit))
        object.__setattr__(self, "capability_refs", _normalize_string_tuple("capability_refs", self.capability_refs))
        object.__setattr__(self, "entrypoint_checks", tuple(self.entrypoint_checks))
        object.__setattr__(self, "entrypoints_verified", bool(self.entrypoints_verified))
        object.__setattr__(
            self,
            "missing_entrypoints",
            _normalize_string_tuple("missing_entrypoints", self.missing_entrypoints),
        )


@dataclass(frozen=True)
class AggregateRuntimeAuditResultV1:
    """Machine-readable aggregate runtime integration audit result."""

    ok: bool
    workspace: str
    aggregate_model_id: str
    integrations: tuple[AggregateRuntimeIntegrationV1, ...]
    wired_count: int
    available_count: int
    planned_bridge_count: int
    verified_entrypoint_count: int
    missing_entrypoint_count: int
    priority_wired: tuple[str, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(
            self, "aggregate_model_id", _require_non_empty("aggregate_model_id", self.aggregate_model_id)
        )
        object.__setattr__(self, "integrations", tuple(self.integrations))
        if not self.integrations:
            raise ValueError("integrations must include at least one entry")
        if (
            self.wired_count < 0
            or self.available_count < 0
            or self.planned_bridge_count < 0
            or self.verified_entrypoint_count < 0
            or self.missing_entrypoint_count < 0
        ):
            raise ValueError("integration counts must be >= 0")
        object.__setattr__(self, "priority_wired", _normalize_string_tuple("priority_wired", self.priority_wired))
        object.__setattr__(self, "warnings", _normalize_string_tuple("warnings", self.warnings))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class AggregateRolePlanResultV1:
    """Query result for an aggregate role/lobe composition plan."""

    ok: bool
    workspace: str
    objective: str
    aggregate_model_id: str
    lobes: tuple[AggregateRoleLobeV1, ...]
    execution_order: tuple[str, ...]
    current_role_ids: tuple[str, ...]
    required_capability_refs: tuple[str, ...]
    runtime_integrations: tuple[AggregateRuntimeIntegrationV1, ...] = field(default_factory=tuple)
    cognitive_ledger: tuple[AggregateCognitiveLedgerEntryV1, ...] = field(default_factory=tuple)
    compute_policy: Mapping[str, Any] = field(default_factory=dict)
    takeover_directive: AggregateTakeoverDirectiveV1 | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "objective", _require_non_empty("objective", self.objective))
        object.__setattr__(
            self, "aggregate_model_id", _require_non_empty("aggregate_model_id", self.aggregate_model_id)
        )
        object.__setattr__(self, "lobes", tuple(self.lobes))
        object.__setattr__(self, "execution_order", _normalize_string_tuple("execution_order", self.execution_order))
        object.__setattr__(self, "current_role_ids", _normalize_string_tuple("current_role_ids", self.current_role_ids))
        object.__setattr__(
            self,
            "required_capability_refs",
            _normalize_string_tuple("required_capability_refs", self.required_capability_refs),
        )
        object.__setattr__(self, "runtime_integrations", tuple(self.runtime_integrations))
        object.__setattr__(self, "cognitive_ledger", tuple(self.cognitive_ledger))
        object.__setattr__(self, "compute_policy", _to_dict_copy(self.compute_policy))
        object.__setattr__(self, "warnings", _normalize_string_tuple("warnings", self.warnings))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class AggregateChatMessageV1:
    """Chat message for the aggregate model wrapper."""

    role: str
    content: str
    name: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "content", _require_non_empty("content", self.content))
        if self.name is not None:
            object.__setattr__(self, "name", _require_non_empty("name", self.name))


def _normalize_chat_messages(messages: Any) -> tuple[AggregateChatMessageV1, ...]:
    if messages is None:
        return ()
    if isinstance(messages, str | bytes):
        raise ValueError("messages must be an iterable of chat message entries")

    try:
        iterator = iter(messages)
    except TypeError as exc:
        raise ValueError("messages must be an iterable of chat message entries") from exc

    normalized: list[AggregateChatMessageV1] = []
    for index, item in enumerate(iterator):
        if isinstance(item, AggregateChatMessageV1):
            normalized.append(item)
            continue
        if isinstance(item, Mapping):
            normalized.append(
                AggregateChatMessageV1(
                    role=str(item.get("role") or "").strip(),
                    content=str(item.get("content") or "").strip(),
                    name=str(item.get("name")).strip() if item.get("name") is not None else None,
                )
            )
            continue
        raise ValueError(f"messages entries must be AggregateChatMessageV1 or mapping (index={index})")
    return tuple(normalized)


@dataclass(frozen=True)
class AggregateChatCompletionsCommandV1:
    """Single-model-shaped command for a Polaris aggregate LLM wrapper.

    `plan_only` is side-effect free. `single_turn` executes one selected
    concrete role. `lobe_chain` executes a bounded sequence of concrete roles
    selected from the aggregate lobe plan.
    """

    workspace: str
    messages: tuple[AggregateChatMessageV1, ...]
    model: str = "polaris.aggregate_llm.v1"
    domain: str | None = None
    role_ids: tuple[str, ...] = field(default_factory=tuple)
    failure_signals: tuple[str, ...] = field(default_factory=tuple)
    failure_evidence: Mapping[str, Any] = field(default_factory=dict)
    execution_mode: str = "plan_only"
    session_id: str | None = None
    run_id: str | None = None
    include_virtual_lobes: bool = True
    context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "messages", _normalize_chat_messages(self.messages))
        if not self.messages:
            raise ValueError("messages must include at least one chat message")
        object.__setattr__(self, "model", _require_non_empty("model", self.model))
        object.__setattr__(self, "domain", _normalize_optional_domain(self.domain))
        object.__setattr__(self, "role_ids", _normalize_string_tuple("role_ids", self.role_ids))
        object.__setattr__(self, "failure_signals", _normalize_string_tuple("failure_signals", self.failure_signals))
        object.__setattr__(self, "failure_evidence", _to_dict_copy(self.failure_evidence))
        mode = str(self.execution_mode or "").strip().lower()
        if mode not in {"plan_only", "single_turn", "lobe_chain"}:
            raise ValueError("execution_mode currently supports 'plan_only', 'single_turn', or 'lobe_chain'")
        object.__setattr__(self, "execution_mode", mode)
        object.__setattr__(self, "context", _to_dict_copy(self.context))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class AggregateChatChoiceV1:
    """One chat-completions choice emitted by the aggregate model wrapper."""

    index: int
    message: AggregateChatMessageV1
    finish_reason: str = "stop"

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("index must be >= 0")
        object.__setattr__(self, "finish_reason", _require_non_empty("finish_reason", self.finish_reason))


@dataclass(frozen=True)
class AggregateChatCompletionsResultV1:
    """Chat-completions-shaped result for the Polaris aggregate model wrapper."""

    id: str
    object: str
    model: str
    choices: tuple[AggregateChatChoiceV1, ...]
    usage: Mapping[str, Any] = field(default_factory=dict)
    aggregate_plan: AggregateRolePlanResultV1 | None = None
    execution_result: RoleExecutionResultV1 | None = None
    execution_results: tuple[RoleExecutionResultV1, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require_non_empty("id", self.id))
        object.__setattr__(self, "object", _require_non_empty("object", self.object))
        object.__setattr__(self, "model", _require_non_empty("model", self.model))
        object.__setattr__(self, "choices", tuple(self.choices))
        if not self.choices:
            raise ValueError("choices must include at least one choice")
        object.__setattr__(self, "usage", _to_dict_copy(self.usage))
        object.__setattr__(self, "execution_results", tuple(self.execution_results))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RoleTaskStartedEventV1:
    """Event emitted when role runtime starts a task."""

    event_id: str
    role: str
    task_id: str
    workspace: str
    started_at: str
    run_id: str | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "started_at", _require_non_empty("started_at", self.started_at))


@dataclass(frozen=True)
class RoleTaskCompletedEventV1:
    """Event emitted when role runtime completes a task."""

    event_id: str
    role: str
    task_id: str
    workspace: str
    status: str
    completed_at: str
    run_id: str | None = None
    session_id: str | None = None
    output_summary: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "completed_at", _require_non_empty("completed_at", self.completed_at))


@dataclass(frozen=True)
class RoleExecutionResultV1:
    """Unified role execution result for task/session calls."""

    ok: bool
    status: str
    role: str
    workspace: str
    task_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    output: str = ""
    thinking: str | None = None
    tool_calls: tuple[str, ...] = field(default_factory=tuple)
    artifacts: tuple[str, ...] = field(default_factory=tuple)
    usage: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    # 完整回话历史 (role, content) 对列表 — 用于非流式模式下的 session 持久化
    turn_history: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "tool_calls", tuple(str(v) for v in self.tool_calls))
        object.__setattr__(self, "artifacts", tuple(str(v) for v in self.artifacts))
        object.__setattr__(self, "usage", _to_dict_copy(self.usage))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        object.__setattr__(self, "turn_history", list(self.turn_history))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


# ── Stream contract types ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class StreamTurnOptions:
    """Options for streamed role chat turns (Task #2)."""

    stream: bool = True
    context: dict[str, Any] | None = None
    history_limit: int | None = None
    prompt_appendix: str | None = None


class StandardStreamEvent(dict):
    """Dict-subclass canonical stream event for the contracts layer (Task #2).

    Mirrors the dataclass in ``console_protocol`` but as a dict so callers
    that expect ``isinstance(result, dict)`` receive a compatible type.
    """

    def __init__(
        self,
        type: str = "",
        data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            type=str(type),
            data=dict(data) if data else {},
            metadata=dict(metadata) if metadata else {},
        )

    @property
    def event_type(self) -> str:
        return self["type"]

    @property
    def event_data(self) -> dict[str, Any]:
        return self["data"]


class RoleRuntimeError(RuntimeError):
    """Structured runtime contract error for roles.runtime."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "role_runtime_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        normalized_message = _require_non_empty("message", message)
        super().__init__(normalized_message)
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": dict(self.details),
        }


@runtime_checkable
class IRoleRuntime(Protocol):
    """Public role runtime interface.

    Notes:
    - `execute_role` is retained as a compatibility method for older callsites.
    - New code should use `execute_role_task` or `execute_role_session`.
    """

    async def execute_role_task(
        self,
        command: ExecuteRoleTaskCommandV1,
    ) -> RoleExecutionResultV1:
        """Execute one task command."""

    async def execute_role_session(
        self,
        command: ExecuteRoleSessionCommandV1,
    ) -> RoleExecutionResultV1:
        """Execute one session-turn command."""

    async def get_runtime_status(
        self,
        query: GetRoleRuntimeStatusQueryV1,
    ) -> Mapping[str, Any]:
        """Return runtime status snapshot."""

    async def build_aggregate_role_plan(
        self,
        query: BuildAggregateRolePlanQueryV1,
    ) -> AggregateRolePlanResultV1:
        """Return a query-only aggregate role/lobe composition plan."""

    async def audit_aggregate_runtime_integrations(
        self,
        query: AuditAggregateRuntimeIntegrationsQueryV1,
    ) -> AggregateRuntimeAuditResultV1:
        """Return runtime integration audit for aggregate-model technology."""

    async def chat_completions(
        self,
        command: AggregateChatCompletionsCommandV1,
    ) -> AggregateChatCompletionsResultV1:
        """Return a model-shaped aggregate chat completion."""

    async def execute_role(
        self,
        role_id: str,
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Compatibility method for pre-contract callsites."""

    def stream_chat_turn(
        self,
        command: ExecuteRoleSessionCommandV1,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream role chat turn events as an async iterator."""
