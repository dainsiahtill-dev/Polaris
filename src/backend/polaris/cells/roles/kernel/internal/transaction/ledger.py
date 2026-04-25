"""Turn 级审计账本与配置。

本模块包含：
- TransactionConfig: 事务控制器配置
- VisibleOutput: 可见输出包装
- TurnLedger: 单次 Turn 的完整审计轨迹
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    DeliveryContract,
    MutationObligationState,
)
from polaris.cells.roles.kernel.internal.transaction.modification_contract import (
    ModificationContract,
)
from polaris.cells.roles.kernel.internal.transaction.phase_manager import (
    PhaseManager,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    CommitReceipt,
    ContinuationHint,
    FailureClass,
    FinalizationRecord,
    OutcomeStatus,
    ResolutionCode,
    ToolBatchExecution,
    TurnDecision,
    TurnId,
    TurnOutcome,
)
from polaris.cells.roles.kernel.public.turn_events import TurnPhaseEvent

logger = logging.getLogger(__name__)


@dataclass
class TransactionConfig:
    """事务控制器配置"""

    domain: Literal["document", "code"] = "document"
    max_tool_execution_time_ms: int = 60000
    enable_streaming: bool = True
    # LLM_ONCE强制约束
    llm_once_forces_tool_choice_none: bool = True
    # 探索移交阈值（与 DecodeConfig.max_tools_per_turn 默认值对齐）
    handoff_threshold_tools: int = 10
    # 开发工作流运行时（用于 HANDOFF_DEVELOPMENT）
    development_runtime: Any | None = None

    # === SLM 协处理器配置 (Cognitive Coprocessor) ===
    slm_enabled: bool = True
    slm_provider: str = "ollama"
    slm_model_name: str = "glm-4.7-flash:latest"
    slm_base_url: str = ""  # 空则使用 OLLAMA_HOST 环境变量或 localhost
    slm_timeout: int = 30
    slm_keep_alive: str = "5m"  # Ollama 模型显存驻留时间 ("-1"=永久, "5m", "30m"等)

    # === 意图 Embedding 配置 ===
    intent_embedding_enabled: bool = True
    intent_embedding_threshold: float = 0.72

    # === 突变守卫模式 ===
    # strict: 检测到 mutation 意图但无写工具时抛 RuntimeError（强制 retry）
    # warn:  仅记录警告日志，放行原始 LLM 决策
    # off:   完全关闭 mutation guard
    mutation_guard_mode: Literal["strict", "warn", "off"] = "warn"

    # === 结果截断配置 ===
    max_per_tool_result_chars: int = 3000
    max_total_result_chars: int = 8000

    # === Inline Patch Escape 阈值 ===
    inline_patch_escape_threshold: float = 0.60

    # === ModificationContract 认知就绪门禁 ===
    # True: 使用 ModificationContract 就绪评估替代机械式 turns_in_phase 检查
    # False: 回退到 FIX-20250422-v2 的 turns_in_phase >= 2 硬阻断
    enable_modification_contract: bool = True

    # === 重试配置 ===
    max_retry_attempts: int = 4

    # === Effect Policy 模式 ===
    effect_policy_mode: str = "default"

    def __post_init__(self) -> None:
        """Validate configuration invariants."""
        if self.max_tool_execution_time_ms < 1000:
            raise ValueError(f"max_tool_execution_time_ms must be >= 1000, got {self.max_tool_execution_time_ms}")
        if self.max_retry_attempts < 0:
            raise ValueError(f"max_retry_attempts must be >= 0, got {self.max_retry_attempts}")
        if self.max_per_tool_result_chars < 100:
            raise ValueError(f"max_per_tool_result_chars must be >= 100, got {self.max_per_tool_result_chars}")
        if self.max_total_result_chars < self.max_per_tool_result_chars:
            raise ValueError(
                f"max_total_result_chars ({self.max_total_result_chars}) must be >= "
                f"max_per_tool_result_chars ({self.max_per_tool_result_chars})"
            )
        if self.handoff_threshold_tools < 1:
            raise ValueError(f"handoff_threshold_tools must be >= 1, got {self.handoff_threshold_tools}")
        if not (0.0 <= self.inline_patch_escape_threshold <= 1.0):
            raise ValueError(
                f"inline_patch_escape_threshold must be in [0.0, 1.0], got {self.inline_patch_escape_threshold}"
            )
        if not (0.0 <= self.intent_embedding_threshold <= 1.0):
            raise ValueError(f"intent_embedding_threshold must be in [0.0, 1.0], got {self.intent_embedding_threshold}")


@dataclass
class VisibleOutput:
    """可见输出"""

    content: str
    reasoning: str | None = None
    format: str = "markdown"


@dataclass
class TurnLedger:
    """
    Turn账本 - 记录单次turn的完整轨迹

    用于：
    1. 审计追踪
    2. 状态恢复
    3. 性能分析
    4. 调试诊断
    """

    turn_id: str
    started_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    completed_at_ms: int = 0

    # LLM调用记录
    llm_calls: list[dict] = field(default_factory=list)

    # 工具执行记录
    tool_executions: list[dict] = field(default_factory=list)

    # 决策记录
    decisions: list[dict] = field(default_factory=list)
    tool_batch_count: int = 0

    # 事件序列
    events: list[TurnPhaseEvent] = field(default_factory=list)

    # 状态历史
    state_history: list[tuple[str, int]] = field(default_factory=list)

    # 监控账本（Phase 7）
    kernel_guard_assert_count: int = 0
    kernel_guard_assert_failures: int = 0
    speculative_attempted_call_ids: set[str] = field(default_factory=set)
    speculative_successful_call_ids: set[str] = field(default_factory=set)
    canonical_tool_call_ids: set[str] = field(default_factory=set)

    # Mutation guard 软警告记录
    mutation_guard_warnings: list[dict] = field(default_factory=list)

    # 异常标记（finalization 等阶段的协议偏离记录）
    anomaly_flags: list[dict] = field(default_factory=list)

    # Session-level state snapshots recorded at turn boundaries.
    # Each entry captures the session-persistent PhaseManager and
    # ModificationContract state so cross-turn mutations are auditable
    # via the ledger commit path (single commit point principle).
    session_state_snapshots: list[dict[str, Any]] = field(default_factory=list)

    # 交付契约与突变义务追踪（Phase 2）
    delivery_contract: DeliveryContract = field(default_factory=DeliveryContract)
    mutation_obligation: MutationObligationState = field(default_factory=MutationObligationState)
    # FIX-20250421-v3: 原始交付模式（Turn 0 设定后冻结，用于 continuation prompt 不降级）
    _original_delivery_mode: str | None = None

    # FIX-20250421: Implementing phase 阻断标记（用于 continuation prompt）
    _implementing_phase_block_triggered: bool = field(default=False)
    # FIX-20250421: PhaseManager — 基于事实的阶段管理器
    phase_manager: PhaseManager = field(default_factory=PhaseManager)
    # FIX-20250422-v3: ModificationContract — 修改契约认知子状态
    modification_contract: ModificationContract = field(default_factory=ModificationContract)

    def set_delivery_contract(self, contract: DeliveryContract) -> None:
        """设置交付契约。

        FIX-20250421-v3: 首次设置时保存原始 delivery_mode，后续 continuation prompt 使用它
        防止 delivery_mode 在 continuation turns 中丢失。
        """
        self.delivery_contract = contract
        if self._original_delivery_mode is None:
            self._original_delivery_mode = contract.mode.value
            logger.debug("original_delivery_mode_frozen: %s", self._original_delivery_mode)

    def record_llm_call(self, phase: str, model: str, tokens_in: int, tokens_out: int) -> None:
        """记录LLM调用"""
        self.llm_calls.append(
            {
                "phase": phase,
                "model": model,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "timestamp_ms": int(time.time() * 1000),
            }
        )

    def record_tool_execution(self, tool_name: str, call_id: str, status: str, duration_ms: int) -> None:
        """记录工具执行"""
        if call_id:
            self.canonical_tool_call_ids.add(call_id)
        self.tool_executions.append(
            {
                "tool_name": tool_name,
                "call_id": call_id,
                "status": status,
                "duration_ms": duration_ms,
                "timestamp_ms": int(time.time() * 1000),
            }
        )

    def record_mutation_guard_warning(self, *, reason: str, user_request: str | None = None) -> None:
        """记录 mutation guard 软警告（warn 模式下使用）。"""
        self.mutation_guard_warnings.append(
            {
                "reason": reason,
                "user_request": user_request,
                "timestamp_ms": int(time.time() * 1000),
            }
        )

    def record_kernel_guard_assert(self, passed: bool) -> None:
        """记录 KernelGuard 断言执行结果。"""
        self.kernel_guard_assert_count += 1
        if not passed:
            self.kernel_guard_assert_failures += 1

    def record_speculative_outcome(self, call_id: str, outcome: Mapping[str, Any]) -> None:
        """记录 speculative 执行结果（仅统计真实启用且可执行的调用）。"""
        if not call_id:
            return
        if bool(outcome.get("enabled")) is not True:
            return
        error = str(outcome.get("error") or "").strip()
        if error == "non_readonly_tool":
            return
        self.speculative_attempted_call_ids.add(call_id)
        if not error:
            self.speculative_successful_call_ids.add(call_id)

    def record_session_state_snapshot(
        self,
        *,
        phase_manager_state: dict[str, Any] | None = None,
        modification_contract_state: dict[str, Any] | None = None,
    ) -> None:
        """Record a snapshot of session-level state at a turn boundary.

        Session-level state (PhaseManager, ModificationContract) persists across
        turns on the TurnTransactionController but historically was never captured
        in the per-turn TurnLedger.  This violates the single-commit-point
        principle because cross-turn mutations become invisible to audit.

        This method is called from ``_build_turn_result()`` *before* the ledger
        is finalized, so every turn's ledger contains a serialized snapshot of
        the session state as it existed at commit time.

        Args:
            phase_manager_state: ``PhaseManager.to_dict()`` output, or *None*
                if the session has no PhaseManager yet.
            modification_contract_state: ``ModificationContract.to_dict()``
                output, or *None* if the session has no ModificationContract yet.
        """
        snapshot: dict[str, Any] = {
            "timestamp_ms": int(time.time() * 1000),
            "phase_manager": phase_manager_state,
            "modification_contract": modification_contract_state,
        }
        self.session_state_snapshots.append(snapshot)
        logger.debug(
            "session_state_snapshot_recorded: turn_id=%s phase=%s mc_status=%s",
            self.turn_id,
            (phase_manager_state or {}).get("current_phase", "N/A"),
            (modification_contract_state or {}).get("status", "N/A"),
        )

    def build_monitoring_metrics(self, final_kind: str) -> dict[str, float]:
        """构建 Phase 7 监控指标快照（per-turn）。"""
        assert_count = max(0, self.kernel_guard_assert_count)
        assert_failures = max(0, self.kernel_guard_assert_failures)
        speculative_attempts = len(self.speculative_attempted_call_ids)
        speculative_false_positives = len(self.speculative_attempted_call_ids - self.canonical_tool_call_ids)
        speculative_hits = len(self.speculative_successful_call_ids & self.canonical_tool_call_ids)

        assert_fail_rate = (assert_failures / assert_count) if assert_count > 0 else 0.0
        hit_rate = (speculative_hits / speculative_attempts) if speculative_attempts > 0 else 0.0
        false_positive_rate = speculative_false_positives / speculative_attempts if speculative_attempts > 0 else 0.0

        return {
            "transaction_kernel.violation_count": float(assert_failures),
            "turn.single_batch_ratio": 1.0 if self.tool_batch_count <= 1 else 0.0,
            "workflow.handoff_rate": 1.0 if final_kind == "handoff_workflow" else 0.0,
            "kernel_guard.assert_fail_rate": float(assert_fail_rate),
            "speculative.hit_rate": float(hit_rate),
            "speculative.false_positive_rate": float(false_positive_rate),
        }

    def record_decision(self, decision: TurnDecision) -> None:
        """记录决策"""
        # NOTE: 历史实现将 decision 按 dict 访问；为保持兼容保留原逻辑
        _decision: Any = decision  # type: ignore[assignment]
        self.decisions.append(
            {
                "kind": _decision.get("kind", "").value
                if hasattr(_decision.get("kind"), "value")
                else str(_decision.get("kind", "")),
                "finalize_mode": _decision.get("finalize_mode", "").value
                if hasattr(_decision.get("finalize_mode"), "value")
                else str(_decision.get("finalize_mode", "")),
                "tool_count": (lambda tb: len(tb.get("invocations", [])) if tb else 0)(_decision.get("tool_batch")),
            }
        )

    def record_tool_batch_resolved(self, resolution_kind: str = "final_answer") -> None:
        """标记最后一个 tool_batch 决策已解决（收口或移交），以满足 KernelGuard 断言。"""
        if not self.decisions:
            return
        last_kind = self.decisions[-1].get("kind")
        if last_kind != "tool_batch":
            return
        # 幂等性保护：若前一个决策已经是 synthetic resolution，不再追加
        if len(self.decisions) >= 2 and self.decisions[-2].get("kind") == resolution_kind:
            return
        self.decisions.append(
            {
                "kind": resolution_kind,
                "finalize_mode": "none",
                "tool_count": 0,
            }
        )

    def finalize(self) -> None:
        """完成账本"""
        self.completed_at_ms = int(time.time() * 1000)

    def get_duration_ms(self) -> int:
        """获取总耗时"""
        if self.completed_at_ms:
            return self.completed_at_ms - self.started_at_ms
        return int(time.time() * 1000) - self.started_at_ms

    def to_turn_outcome(
        self,
        run_id: str,
        decision: TurnDecision,
        execution: ToolBatchExecution | None = None,
        closing: FinalizationRecord | None = None,
        failure_class: FailureClass | None = None,
        commit_ref: CommitReceipt | None = None,
    ) -> TurnOutcome:
        """从账本生成 TurnOutcome。

        这是 ledger 到 canonical result 的标准转换入口。
        下游应消费 TurnOutcome，而不是直接读取 ledger。
        """
        # 推断 outcome_status
        if failure_class == FailureClass.CONTRACT_VIOLATION:
            outcome_status = OutcomeStatus.PANIC
            resolution_code = ResolutionCode.FAIL_CLOSED
        elif failure_class == FailureClass.DURABILITY_FAILURE or failure_class is not None:
            outcome_status = OutcomeStatus.FAILED
            resolution_code = ResolutionCode.FAIL_CLOSED
        elif self.decisions and self.decisions[-1].get("kind") in ("handoff_workflow", "handoff_development"):
            outcome_status = OutcomeStatus.HANDED_OFF
            resolution_code = ResolutionCode.HANDOFF_WORKFLOW
        elif self.decisions and self.decisions[-1].get("kind") == "ask_user":
            outcome_status = OutcomeStatus.HANDED_OFF
            resolution_code = ResolutionCode.NEED_HUMAN
        else:
            outcome_status = OutcomeStatus.COMPLETED
            resolution_code = ResolutionCode.COMPLETED

        # 生成 continuation hint（derived projection）
        continuation_hint = None
        if self.decisions:
            last_decision = self.decisions[-1]
            blocked = self.mutation_obligation.blocked_reason
            blocked_str = ""
            if blocked is not None:
                blocked_str = blocked.value if hasattr(blocked, "value") else str(blocked)
            continuation_hint = ContinuationHint(
                goal_progress_summary=last_decision.get("reasoning_summary"),
                new_refs=[tool.get("tool_name", "") for tool in self.tool_executions],
                blocked_reason=blocked_str,
                continuation_hint="explore" if self.tool_batch_count > 0 else "stop",
            )

        return TurnOutcome(
            turn_id=TurnId(self.turn_id),
            run_id=run_id,
            decision=decision,
            execution=execution,
            closing=closing,
            outcome_status=outcome_status,
            resolution_code=resolution_code,
            failure_class=failure_class,
            commit_ref=commit_ref,
            continuation_hint=continuation_hint,
        )

    def to_audit_log(self) -> dict[str, Any]:
        """转换为审计日志格式。

        The ``session_state_snapshots`` key is only included when at least one
        snapshot has been recorded, preserving backward compatibility for
        existing consumers that do not expect the field.
        """
        final_kind = str(self.decisions[-1].get("kind", "")) if self.decisions else ""
        audit: dict[str, Any] = {
            "turn_id": self.turn_id,
            "duration_ms": self.get_duration_ms(),
            "llm_calls": len(self.llm_calls),
            "tool_calls": len(self.tool_executions),
            "decisions": self.decisions,
            "states": self.state_history,
            "monitoring": self.build_monitoring_metrics(final_kind=final_kind),
            "completed": self.completed_at_ms > 0,
            "anomaly_flags": self.anomaly_flags,
            "mutation_guard_warnings": self.mutation_guard_warnings,
            "delivery_mode": self.delivery_contract.mode.value,
            "mutation_obligation": self.mutation_obligation.to_audit_dict(),
        }
        if self.session_state_snapshots:
            audit["session_state_snapshots"] = self.session_state_snapshots
        return audit
