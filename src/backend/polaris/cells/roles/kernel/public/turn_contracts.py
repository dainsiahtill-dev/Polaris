"""
Turn Engine Contracts - 事务型 Turn 的核心契约定义

这是 TransactionKernel / TurnTransactionController 的协议真相源。
所有结构化 turn 协议对象都通过 frozen models 暴露，同时保留最小
mapping 风格兼容接口，避免在迁移期强制重写所有调用点。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, NewType

from polaris.kernelone.tool_execution.contracts import CapturedToolSpecSnapshotV1
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

# ============ 基础类型 ============
ToolCallId = NewType("ToolCallId", str)
TurnId = NewType("TurnId", str)
BatchId = NewType("BatchId", str)


class _FrozenMappingModel(BaseModel):
    """Frozen Pydantic model with dict-like compatibility helpers."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def keys(self) -> list[str]:
        return list(self.__class__.model_fields.keys())

    def items(self) -> list[tuple[str, Any]]:
        return [(key, getattr(self, key)) for key in self.__class__.model_fields]

    def values(self) -> list[Any]:
        return [getattr(self, key) for key in self.__class__.model_fields]

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="python")


# ============ 枚举定义 ============
class TurnDecisionKind(str, Enum):
    """Turn 决策类型。"""

    FINAL_ANSWER = "final_answer"
    TOOL_BATCH = "tool_batch"
    ASK_USER = "ask_user"
    HANDOFF_WORKFLOW = "handoff_workflow"
    HANDOFF_DEVELOPMENT = "handoff_development"


class FinalizeMode(str, Enum):
    """
    工具执行后的收口策略。

    - NONE: 直接返回工具结果，不再请求 LLM
    - LOCAL: 本地模板渲染结果
    - LLM_ONCE: 允许一次显式总结请求（强制禁止再调工具）
    """

    NONE = "none"
    LOCAL = "local"
    LLM_ONCE = "llm_once"


class ToolExecutionMode(str, Enum):
    """工具执行模式。"""

    READONLY_PARALLEL = "readonly_parallel"
    READONLY_SERIAL = "readonly_serial"
    WRITE_SERIAL = "write_serial"
    ASYNC_RECEIPT = "async_receipt"


class ToolEffectType(str, Enum):
    """工具副作用类型。"""

    READ = "read"
    WRITE = "write"
    ASYNC = "async"


class ControlPlaneEvent(str, Enum):
    """控制平面事件类型。"""

    DECISION = "decision"
    TELEMETRY = "telemetry"
    POLICY_VERDICT = "policy_verdict"
    BUDGET_STATUS = "budget_status"


class DataPlaneEvent(str, Enum):
    """数据平面事件类型。"""

    TRUTH_LOG_APPEND = "truth_log_append"
    WORKING_STATE_UPDATE = "working_state_update"
    RECEIPT_STORE_PUT = "receipt_store_put"
    PROMPT_PROJECTION = "prompt_projection"


@dataclass(frozen=True, slots=True)
class ToolClassificationV1:
    """One immutable ToolSpecRegistry classification capture for a tool call."""

    raw_tool_name: str
    canonical_tool_name: str
    registered: bool
    effect_type: ToolEffectType
    execution_mode: ToolExecutionMode
    snapshot: Any | None
    normalization_required: bool
    error_code: Literal["deo_tool_normalization_failed"] | None = None


@dataclass(frozen=True, slots=True)
class _CapturedEffectiveSpecSemanticsV1:
    """Pure semantic verdict derived from one captured effective ToolSpec."""

    effect_type: ToolEffectType
    execution_mode: ToolExecutionMode
    normalization_required: bool
    error_code: Literal["deo_tool_normalization_failed"] | None


# Compatibility projections for legacy transaction constants. They are not read
# by the canonical classifier, decoder, gateway, or directed-effect guard.
_READONLY_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "list_directory",
        "grep",
        "search_code",
        "glob",
        "find",
        "cat",
        "head",
        "tail",
        "wc",
        "diff",
        "stat",
        "exists",
        "get_file_info",
        "search_files",
        "repo_tree",
        "repo_rg",
        "repo_read_head",
        "repo_read_tail",
        "repo_read_slice",
        "repo_read_around",
        "treesitter_find_symbol",
    }
)
# Compatibility projection for legacy transaction constants. The canonical
# classifier never reads this set; unregistered names are conservative writes.
_ASYNC_TOOLS: frozenset[str] = frozenset(
    {
        "create_pull_request",
        "submit_job",
        "trigger_ci",
        "deploy",
        "send_notification",
        "webhook",
        "async_task",
        "long_running_task",
    }
)


def classify_tool_invocation(raw_tool_name: str) -> ToolClassificationV1:
    """Capture one authoritative ToolSpec view and classify it conservatively.

    Registry inconsistency and unknown names remain mutation candidates but carry
    no normalizable snapshot, so callers must deny before authorization/effect.
    """
    from polaris.kernelone.tool_execution.tool_spec_registry import (
        ToolSpecRegistry,
        ToolSpecRegistryConsistencyError,
    )

    raw = str(raw_tool_name)
    if raw.strip().startswith("__"):
        raise ValueError("synthetic_tool_invocation_forbidden")
    try:
        snapshot = ToolSpecRegistry.capture_effective_spec(raw)
    except ToolSpecRegistryConsistencyError:
        return ToolClassificationV1(
            raw_tool_name=raw,
            canonical_tool_name=raw,
            registered=False,
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
            snapshot=None,
            normalization_required=True,
            error_code="deo_tool_normalization_failed",
        )

    return _classify_captured_snapshot(raw, snapshot)


def _classify_captured_snapshot(
    raw_tool_name: str,
    snapshot: CapturedToolSpecSnapshotV1,
) -> ToolClassificationV1:
    """Derive classification from one already-captured registry snapshot only."""
    if not snapshot.registered:
        return ToolClassificationV1(
            raw_tool_name=raw_tool_name,
            canonical_tool_name=snapshot.canonical_tool_name,
            registered=False,
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
            snapshot=snapshot,
            normalization_required=False,
        )

    semantics = _validate_captured_effective_spec_semantics(snapshot)

    return ToolClassificationV1(
        raw_tool_name=raw_tool_name,
        canonical_tool_name=snapshot.canonical_tool_name,
        registered=True,
        effect_type=semantics.effect_type,
        execution_mode=semantics.execution_mode,
        snapshot=snapshot,
        normalization_required=semantics.normalization_required,
        error_code=semantics.error_code,
    )


def _validate_captured_effective_spec_semantics(
    snapshot: CapturedToolSpecSnapshotV1,
) -> _CapturedEffectiveSpecSemanticsV1:
    """Classify category fields together, rejecting ambiguous captured semantics."""
    from polaris.kernelone.tool_execution.contracts import frozen_node_to_value

    effective_spec = frozen_node_to_value(snapshot.canonical_effective_spec)
    if not isinstance(effective_spec, dict):
        return _invalid_captured_effective_spec_semantics()

    semantic_values: set[str] = set()
    category = effective_spec.get("category")
    if category is not None:
        if not isinstance(category, str):
            return _invalid_captured_effective_spec_semantics()
        semantic_values.add(category.strip().lower())

    categories = effective_spec.get("categories")
    if categories is not None:
        if not isinstance(categories, list) or not all(isinstance(value, str) for value in categories):
            return _invalid_captured_effective_spec_semantics()
        semantic_values.update(value.strip().lower() for value in categories)

    effect_type = effective_spec.get("effect_type")
    if effect_type is not None:
        if not isinstance(effect_type, str):
            return _invalid_captured_effective_spec_semantics()
        semantic_values.add(effect_type.strip().lower())

    semantic_effects = {
        "read": ToolEffectType.READ,
        "write": ToolEffectType.WRITE,
        "async": ToolEffectType.ASYNC,
        "exec": ToolEffectType.WRITE,
        "execute": ToolEffectType.WRITE,
        "delete": ToolEffectType.WRITE,
        "mutation": ToolEffectType.WRITE,
        "mutate": ToolEffectType.WRITE,
    }
    if not semantic_values or any(value not in semantic_effects for value in semantic_values):
        return _invalid_captured_effective_spec_semantics()

    effects = {semantic_effects[value] for value in semantic_values}
    if len(effects) != 1:
        return _invalid_captured_effective_spec_semantics()

    resolved_effect = effects.pop()
    if resolved_effect is ToolEffectType.READ:
        return _CapturedEffectiveSpecSemanticsV1(
            effect_type=ToolEffectType.READ,
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
            normalization_required=False,
            error_code=None,
        )
    if resolved_effect is ToolEffectType.ASYNC:
        return _CapturedEffectiveSpecSemanticsV1(
            effect_type=ToolEffectType.ASYNC,
            execution_mode=ToolExecutionMode.ASYNC_RECEIPT,
            normalization_required=True,
            error_code=None,
        )
    return _CapturedEffectiveSpecSemanticsV1(
        effect_type=ToolEffectType.WRITE,
        execution_mode=ToolExecutionMode.WRITE_SERIAL,
        normalization_required=True,
        error_code=None,
    )


def _invalid_captured_effective_spec_semantics() -> _CapturedEffectiveSpecSemanticsV1:
    """Keep invalid or ambiguous semantics on the fail-closed mutation path."""
    return _CapturedEffectiveSpecSemanticsV1(
        effect_type=ToolEffectType.WRITE,
        execution_mode=ToolExecutionMode.WRITE_SERIAL,
        normalization_required=True,
        error_code="deo_tool_normalization_failed",
    )


def tool_classification_matches_snapshot(classification: ToolClassificationV1) -> bool:
    """Verify classification identity against its captured snapshot without rereads."""
    snapshot = classification.snapshot
    if classification.error_code is not None or snapshot is None or not snapshot.registered:
        return False
    return _classification_matches_captured_snapshot(classification)


def _classification_matches_captured_snapshot(classification: ToolClassificationV1) -> bool:
    """Purely rederive a supplied captured classification without an authorization verdict."""
    snapshot = classification.snapshot
    if not isinstance(snapshot, CapturedToolSpecSnapshotV1):
        return False
    try:
        from polaris.kernelone.tool_execution.contracts import frozen_node_to_value

        if classification.raw_tool_name != snapshot.raw_tool_name:
            return False
        canonical_snapshot = CapturedToolSpecSnapshotV1(
            raw_tool_name=snapshot.raw_tool_name,
            canonical_tool_name=snapshot.canonical_tool_name,
            registered=snapshot.registered,
            canonical_effective_spec=snapshot.canonical_effective_spec,
            canonical_name_view=snapshot.canonical_name_view,
            alias_binding_view=snapshot.alias_binding_view,
        )
        if canonical_snapshot != snapshot:
            return False
        if snapshot.registered:
            canonical_names = frozen_node_to_value(snapshot.canonical_name_view)
            alias_bindings = frozen_node_to_value(snapshot.alias_binding_view)
            if (
                not isinstance(canonical_names, list)
                or not all(isinstance(name, str) for name in canonical_names)
                or snapshot.canonical_tool_name not in canonical_names
                or not isinstance(alias_bindings, dict)
                or alias_bindings.get(snapshot.raw_tool_name) != snapshot.canonical_tool_name
            ):
                return False
        return classification == _classify_captured_snapshot(classification.raw_tool_name, snapshot)
    except (AttributeError, TypeError, ValueError):
        return False


def _infer_execution_mode(tool_name: str) -> ToolExecutionMode:
    """Compatibility delegate to the public canonical classifier."""
    return classify_tool_invocation(tool_name).execution_mode


def _infer_effect_type(tool_name: str, execution_mode: ToolExecutionMode | None = None) -> ToolEffectType:
    """Compatibility delegate to the public canonical classifier."""
    return classify_tool_invocation(tool_name).effect_type


# ============ 核心数据结构 ============
class ToolInvocation(_FrozenMappingModel):
    """单个工具调用定义。"""

    call_id: ToolCallId
    tool_name: str
    raw_tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    effect_type: ToolEffectType | None = None
    execution_mode: ToolExecutionMode | None = None
    classification: ToolClassificationV1 | None = None

    @classmethod
    def _from_captured_classification(
        cls,
        *,
        call_id: ToolCallId,
        raw_tool_name: str,
        arguments: dict[str, object],
        classification: ToolClassificationV1,
    ) -> ToolInvocation:
        """Internal decoder factory preserving its single captured classification."""
        return cls.model_validate(
            {
                "call_id": call_id,
                "raw_tool_name": raw_tool_name,
                "tool_name": classification.canonical_tool_name,
                "arguments": arguments,
                "classification": classification,
            },
            context={"tool_invocation_factory_capability": _TOOL_INVOCATION_FACTORY_CAPABILITY},
        )

    @model_validator(mode="before")
    @classmethod
    def _isolate_public_classification_injection(cls, value: object, info: ValidationInfo) -> object:
        """Keep captured classifications confined to the internal decoder factory."""
        if isinstance(value, cls):
            return value
        if (
            isinstance(value, dict)
            and "classification" in value
            and (
                info.context is None
                or info.context.get("tool_invocation_factory_capability") is not _TOOL_INVOCATION_FACTORY_CAPABILITY
            )
        ):
            return {key: item for key, item in value.items() if key != "classification"}
        return value

    @model_validator(mode="after")
    def _normalize(self, info: ValidationInfo) -> ToolInvocation:
        raw_tool_name = self.raw_tool_name if self.raw_tool_name is not None else self.tool_name
        raw_tool_name = str(raw_tool_name)
        if not raw_tool_name.strip():
            raise ValueError("raw_tool_name must be non-empty")
        if raw_tool_name.strip().startswith("__"):
            raise ValueError("synthetic_tool_invocation_forbidden")
        internal_factory = (
            info.context is not None
            and info.context.get("tool_invocation_factory_capability") is _TOOL_INVOCATION_FACTORY_CAPABILITY
        )
        classification = self.classification if internal_factory else classify_tool_invocation(raw_tool_name)
        if classification is None or (
            internal_factory and not _classification_matches_captured_snapshot(classification)
        ):
            raise ValueError("deo_tool_classification_mismatch: missing classification")
        if classification.raw_tool_name != raw_tool_name:
            raise ValueError("deo_tool_classification_mismatch: raw tool name differs from classifier")
        if self.effect_type is not None and self.effect_type is not classification.effect_type:
            raise ValueError("deo_tool_classification_mismatch: effect_type")
        if self.execution_mode is not None and self.execution_mode is not classification.execution_mode:
            raise ValueError("deo_tool_classification_mismatch: execution_mode")
        object.__setattr__(self, "raw_tool_name", raw_tool_name)
        object.__setattr__(self, "tool_name", classification.canonical_tool_name)
        object.__setattr__(self, "effect_type", classification.effect_type)
        object.__setattr__(self, "execution_mode", classification.execution_mode)
        object.__setattr__(self, "classification", classification)
        return self


_TOOL_INVOCATION_FACTORY_CAPABILITY = object()


class ToolBatch(_FrozenMappingModel):
    """工具批定义。"""

    batch_id: BatchId
    invocations: list[ToolInvocation] = Field(default_factory=list)
    parallel_readonly: list[ToolInvocation] = Field(default_factory=list)
    readonly_serial: list[ToolInvocation] = Field(default_factory=list)
    serial_writes: list[ToolInvocation] = Field(default_factory=list)
    async_receipts: list[ToolInvocation] = Field(default_factory=list)


class TurnDecision(_FrozenMappingModel):
    """
    单个 turn 的唯一决策来源。

    约束：
    1. kind=FINAL_ANSWER 时，tool_batch 必须为 None
    2. kind=TOOL_BATCH 时，执行来源只能是 tool_batch
    3. visible_message 仅面向用户显示，不参与执行
    4. reasoning_summary 仅面向观测，永不执行
    """

    turn_id: TurnId
    kind: TurnDecisionKind
    visible_message: str
    reasoning_summary: str | None = None
    tool_batch: ToolBatch | None = None
    finalize_mode: FinalizeMode
    domain: Literal["document", "code"]
    metadata: dict[str, Any] = Field(default_factory=dict)


# ============ 执行结果 ============
class ToolExecutionResult(_FrozenMappingModel):
    """单个工具执行结果。"""

    call_id: ToolCallId
    tool_name: str
    status: Literal["success", "error", "pending", "timeout", "aborted"]
    result: Any = None
    execution_time_ms: int = 0
    effect_receipt: dict[str, Any] | None = None


class BatchReceipt(_FrozenMappingModel):
    """工具批执行完成的收据。

    ``effect_receipts`` keeps the authoritative effect evidence emitted by
    policy-gated tool adapters.  It is separate from ``results`` because a
    batch may commit an effect receipt even when a provider-facing result row
    is unavailable or intentionally redacted.
    """

    batch_id: BatchId
    turn_id: TurnId
    results: list[ToolExecutionResult] = Field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    pending_async_count: int = 0
    has_pending_async: bool = False
    raw_results: list[dict[str, Any]] = Field(default_factory=list)
    effect_receipts: list[dict[str, Any]] = Field(default_factory=list)


class TurnFinalization(_FrozenMappingModel):
    """LLM_ONCE 模式的最终收口。"""

    turn_id: TurnId
    mode: Literal["none", "local", "llm_once"]
    final_visible_message: str
    needs_followup_workflow: bool = False
    workflow_reason: str | None = None


class TurnResult(_FrozenMappingModel):
    """单个 turn 的完整结果。"""

    turn_id: TurnId
    kind: Literal[
        "final_answer",
        "tool_batch_with_receipt",
        "handoff_workflow",
        "ask_user",
        "continue_multi_turn",
        "inline_patch_escape_blocked",
        "mutation_bypass_blocked",
    ]
    visible_content: str
    decision: TurnDecision | dict[str, Any]
    batch_receipt: BatchReceipt | dict[str, Any] | None = None
    finalization: TurnFinalization | dict[str, Any] | None = None
    workflow_context: dict[str, Any] | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    protocol_version: str = "2.2"


# ============ Orchestrator 层扩展契约 ============
class TurnContinuationMode(str, Enum):
    """Turn 结束后，Orchestrator 层的继续执行模式。"""

    END_SESSION = "end_session"
    AUTO_CONTINUE = "auto_continue"
    WAITING_HUMAN = "waiting_human"
    HANDOFF_EXPLORATION = "handoff_exploration"
    HANDOFF_DEVELOPMENT = "handoff_development"
    SPECULATIVE_CONTINUE = "speculative_continue"


class TurnOutcomeEnvelope(BaseModel):
    """Orchestrator 层对 TurnResult 的包装，附加继续执行意图。"""

    model_config = ConfigDict(extra="forbid")

    turn_result: TurnResult
    continuation_mode: TurnContinuationMode
    next_intent: str | None = None
    session_patch: dict[str, Any] = Field(default_factory=dict)
    artifacts_to_persist: list[dict[str, Any]] = Field(default_factory=list)
    speculative_hints: dict[str, Any] = Field(default_factory=dict)
    # Phase 1.5: turn-level failure classification for continuation policy
    failure_class: TurnFailureClass | None = None
    # Phase 2: Status Contract Protocol — subagent 必须报告明确状态
    agent_status: AgentStatus | None = Field(
        default=None,
        description="Subagent reported status: DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT",
    )
    status_evidence: dict[str, Any] | None = Field(
        default=None,
        description="Supporting evidence for agent_status (verification output, error details, etc.)",
    )


# ============ 上下文定义 ============
class TurnContext(_FrozenMappingModel):
    """Turn 执行上下文。"""

    user_message: str
    conversation_history: list[dict[str, Any]] = Field(default_factory=list)
    domain: str = "document"
    workspace: str = "."


class RawLLMResponse(_FrozenMappingModel):
    """LLM 原始响应结构。"""

    content: str
    thinking: str | None = None
    native_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    model: str = "unknown"
    usage: dict[str, Any] = Field(default_factory=dict)


# ============ Phase 1: Transaction Kernel Hardening ============
# 以下 schema 是 TurnTransactionController 的 canonical outcome 契约。
# 命名约束：工程一等公民命名，禁止认知隐喻（如 heartbeat, neural, hippocampus）。


class OutcomeStatus(str, Enum):
    """Turn 最终状态。"""

    COMPLETED = "completed"
    FAILED = "failed"
    PANIC = "panic"
    HANDED_OFF = "handed_off"
    CANCELLED = "cancelled"


class ResolutionCode(str, Enum):
    """Turn 结束后的 resolution 语义。

    注意：这不是 TurnDecisionKind 的扩展，而是 outcome 层面的 resolution。
    """

    COMPLETED = "completed"
    FAIL_CLOSED = "fail_closed"
    HANDOFF_WORKFLOW = "handoff_workflow"
    NEED_HUMAN = "need_human"
    CANCELLED = "cancelled"


class ContinuationHint(_FrozenMappingModel):
    """为 Orchestrator 和 UI 提供的轻量 continuation hint。

    这是 derived projection，不是独立 truth source。
    可以从 snapshot / truthlog / findings 重建。
    """

    goal_progress_summary: str | None = None
    new_refs: list[str] = Field(default_factory=list)
    blocked_reason: str | None = None
    continuation_hint: str | None = None
    derived: bool = True

    @classmethod
    def rebuild_from(
        cls,
        snapshot: dict[str, Any],
        truthlog: list[dict[str, Any]],
    ) -> ContinuationHint:
        """从 snapshot 和 truthlog 重建 continuation hint。

        证明这是一个可重建的 derived projection。
        """
        # 最小实现：从 snapshot 提取关键字段
        return cls(
            goal_progress_summary=snapshot.get("goal_progress_summary"),
            new_refs=snapshot.get("new_refs", []),
            blocked_reason=snapshot.get("blocked_reason"),
            continuation_hint=snapshot.get("continuation_hint"),
            derived=True,
        )


class ToolBatchExecution(_FrozenMappingModel):
    """工具批次执行记录。"""

    batch_id: BatchId
    invocations: list[ToolInvocation] = Field(default_factory=list)
    receipt: BatchReceipt | None = None
    side_effect_class: Literal["readonly", "local_write", "external_write"] = "readonly"


class FinalizationRecord(_FrozenMappingModel):
    """收口策略执行记录。"""

    mode: FinalizeMode
    final_visible_message: str
    closed_without_tools: bool = True


class CommitReceipt(_FrozenMappingModel):
    """Commit protocol 的收据。

    证明本次 turn 已通过 durable commit protocol 写入系统。
    """

    turn_id: TurnId
    snapshot_id: str
    truthlog_seq_range: tuple[int, int]
    sealed_at: str  # ISO 8601
    validation_passed: bool
    fact_stream: str = ""
    fact_event_id: str = ""
    fact_event_seq: int | None = None
    fact_storage_path: str = ""
    outcome_hash: str = ""


class SealedTurn(_FrozenMappingModel):
    """已封印的 turn。

    封印后的 turn 不可修改，是系统 truth 的一部分。
    """

    turn_id: TurnId
    commit_receipt: CommitReceipt
    outcome_status: OutcomeStatus
    resolution_code: ResolutionCode
    sealed_at: str  # ISO 8601
    parent_snapshot_id: str | None = None


class TurnFailureClass(str, Enum):
    """Turn 失败分类。

    用于驱动 ContinuationPolicy 的自我保护决策；这是角色内局部/local 分类，
    不是 Run Ledger 跨层 failure taxonomy。
    """

    CONTRACT_VIOLATION = "contract_violation"
    RUNTIME_FAILURE = "runtime_failure"
    DURABILITY_FAILURE = "durability_failure"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    POLICY_FAILURE = "policy_failure"
    CANCELLATION = "cancellation"


class AgentStatus(str, Enum):
    """Subagent 状态契约 — Superpowers 风格的状态报告协议。

    在多 agent 协作场景中，subagent 必须返回明确状态，
    Controller 据此决定下一步（重试/补充上下文/升级人工/终止）。

    状态语义：
    - DONE: 任务完成，所有验收标准满足
    - DONE_WITH_CONCERNS: 完成任务但存在未解决的次要问题
    - BLOCKED: 遇到无法自行解决的阻塞（权限/依赖/架构决策）
    - NEEDS_CONTEXT: 需要更多上下文才能继续（缺文件/缺定义/缺接口）
    """

    DONE = "done"
    DONE_WITH_CONCERNS = "done_with_concerns"
    BLOCKED = "blocked"
    NEEDS_CONTEXT = "needs_context"


class TurnOutcome(_FrozenMappingModel):
    """Turn 完成后唯一可被下游消费的 canonical result。

    约束：
    1. 每个 turn 只产生一个 TurnOutcome
    2. TurnLedger 是审计源，不是消费面
    3. outcome_status 必须是枚举值，禁止自由文本
    4. continuation_hint 是 derived projection，不是独立 truth source
    """

    schema_version: str = "roles.kernel.turn_outcome.v1"
    turn_id: TurnId
    run_id: str
    decision: TurnDecision | None = None
    execution: ToolBatchExecution | None = None
    closing: FinalizationRecord | None = None
    outcome_status: OutcomeStatus
    resolution_code: ResolutionCode
    failure_class: TurnFailureClass | None = None
    commit_ref: CommitReceipt | None = None
    continuation_hint: ContinuationHint | None = None
    user_visible_result_ref: str | None = None
    failure_reason: str | None = None

    def to_summary_dict(self) -> dict[str, Any]:
        """生成轻量摘要，供 Orchestrator 快速消费。"""
        return {
            "turn_id": self.turn_id,
            "outcome_status": self.outcome_status.value,
            "resolution_code": self.resolution_code.value,
            "failure_class": self.failure_class.value if self.failure_class else None,
            "continuation_hint": self.continuation_hint.to_dict() if self.continuation_hint else None,
            "commit_snapshot_id": self.commit_ref.snapshot_id if self.commit_ref else None,
            "commit_event_id": self.commit_ref.fact_event_id if self.commit_ref else None,
            "commit_event_seq": self.commit_ref.fact_event_seq if self.commit_ref else None,
        }
