"""
Turn Engine Contracts - 事务型 Turn 的核心契约定义

这是 TransactionKernel / TurnTransactionController 的协议真相源。
所有结构化 turn 协议对象都通过 frozen models 暴露，同时保留最小
mapping 风格兼容接口，避免在迁移期强制重写所有调用点。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, NewType

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ============ 基础类型 ============
ToolCallId = NewType("ToolCallId", str)
TurnId = NewType("TurnId", str)
BatchId = NewType("BatchId", str)


class _FrozenMappingModel(BaseModel):
    """Frozen Pydantic model with dict-like compatibility helpers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

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


# ============ 工具分类真相源 ============
# 以下集合是 Transaction Kernel 协议层对工具分类的唯一工程级真相源。
# 业务模块（如 constants.py）应从此处导入，禁止重复定义。

_READONLY_TOOLS: frozenset[str] = frozenset(
    {
        # 通用文件系统/搜索只读工具 (15个)
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
        # Polaris canonical read tools (7个)
        "repo_tree",
        "repo_rg",
        "repo_read_head",
        "repo_read_tail",
        "repo_read_slice",
        "repo_read_around",
        "treesitter_find_symbol",
    }
)
"""只读工具集合 — 可并行执行。这是工程级分类真相源。"""

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
"""异步工具集合 — 返回 pending receipt，不等待完成。这是工程级分类真相源。"""


def _infer_execution_mode(tool_name: str) -> ToolExecutionMode:
    """根据工具名推断执行模式 — 工程级分类入口。

    这是 Transaction Kernel 对工具执行模式分类的唯一权威函数。
    业务模块不应自行实现分类逻辑。
    """
    normalized = tool_name.lower().replace("-", "_")
    if normalized in _READONLY_TOOLS:
        return ToolExecutionMode.READONLY_PARALLEL
    if normalized in _ASYNC_TOOLS:
        return ToolExecutionMode.ASYNC_RECEIPT
    return ToolExecutionMode.WRITE_SERIAL


def _infer_effect_type(tool_name: str, execution_mode: ToolExecutionMode | None) -> ToolEffectType:
    """根据工具名和执行模式推断副作用类型。"""
    if execution_mode in {ToolExecutionMode.READONLY_PARALLEL, ToolExecutionMode.READONLY_SERIAL}:
        return ToolEffectType.READ
    if execution_mode == ToolExecutionMode.ASYNC_RECEIPT:
        return ToolEffectType.ASYNC
    normalized = tool_name.lower().replace("-", "_")
    if normalized in _READONLY_TOOLS:
        return ToolEffectType.READ
    if normalized in _ASYNC_TOOLS:
        return ToolEffectType.ASYNC
    return ToolEffectType.WRITE


# ============ 核心数据结构 ============
class ToolInvocation(_FrozenMappingModel):
    """单个工具调用定义。"""

    call_id: ToolCallId
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    effect_type: ToolEffectType
    execution_mode: ToolExecutionMode

    @model_validator(mode="after")
    def _normalize(self) -> ToolInvocation:
        execution_mode = self.execution_mode or _infer_execution_mode(self.tool_name)
        effect_type = self.effect_type or _infer_effect_type(self.tool_name, execution_mode)
        object.__setattr__(self, "execution_mode", execution_mode)
        object.__setattr__(self, "effect_type", effect_type)
        return self


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
    """工具批执行完成的收据。"""

    batch_id: BatchId
    turn_id: TurnId
    results: list[ToolExecutionResult] = Field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    pending_async_count: int = 0
    has_pending_async: bool = False
    raw_results: list[dict[str, Any]] = Field(default_factory=list)


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
    # Phase 1.5: Failure classification for continuation policy
    failure_class: FailureClass | None = None


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


class ResolutionCode(str, Enum):
    """Turn 结束后的 resolution 语义。

    注意：这不是 TurnDecisionKind 的扩展，而是 outcome 层面的 resolution。
    """

    COMPLETED = "completed"
    FAIL_CLOSED = "fail_closed"
    HANDOFF_WORKFLOW = "handoff_workflow"
    NEED_HUMAN = "need_human"


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


class FailureClass(str, Enum):
    """Turn 失败分类。

    用于驱动 ContinuationPolicy 的自我保护决策。
    """

    CONTRACT_VIOLATION = "contract_violation"
    RUNTIME_FAILURE = "runtime_failure"
    DURABILITY_FAILURE = "durability_failure"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    POLICY_FAILURE = "policy_failure"


class TurnOutcome(_FrozenMappingModel):
    """Turn 完成后唯一可被下游消费的 canonical result。

    约束：
    1. 每个 turn 只产生一个 TurnOutcome
    2. TurnLedger 是审计源，不是消费面
    3. outcome_status 必须是枚举值，禁止自由文本
    4. continuation_hint 是 derived projection，不是独立 truth source
    """

    turn_id: TurnId
    run_id: str
    decision: TurnDecision
    execution: ToolBatchExecution | None = None
    closing: FinalizationRecord | None = None
    outcome_status: OutcomeStatus
    resolution_code: ResolutionCode
    failure_class: FailureClass | None = None
    commit_ref: CommitReceipt | None = None
    continuation_hint: ContinuationHint | None = None
    user_visible_result_ref: str | None = None

    def to_summary_dict(self) -> dict[str, Any]:
        """生成轻量摘要，供 Orchestrator 快速消费。"""
        return {
            "turn_id": self.turn_id,
            "outcome_status": self.outcome_status.value,
            "resolution_code": self.resolution_code.value,
            "failure_class": self.failure_class.value if self.failure_class else None,
            "continuation_hint": self.continuation_hint.to_dict() if self.continuation_hint else None,
            "commit_snapshot_id": self.commit_ref.snapshot_id if self.commit_ref else None,
        }
