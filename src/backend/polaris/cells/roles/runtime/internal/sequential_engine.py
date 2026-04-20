"""Sequential Engine - 顺序执行引擎

提供结构化的多步骤执行能力，包含预算控制、进展检测和终止条件管理。
这是 vNext 内核化的核心组件，实现了：
- 单一回合驱动器
- 子状态隔离（metadata.seq.*）
- 统一预算与终止
- 幂等恢复机制

设计原则：
- Sequential 只负责"提议"，主状态机负责"落账"
- 通过 State Proxy 进行写保护，防止污染主状态
- 通过事件系统提供可观测性
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 枚举与常量
# ═══════════════════════════════════════════════════════════════════════════

# P1-TYPE-007: Import canonical Sequential types from profile.schema
# This is the single source of truth for sequential execution types


class TerminationReason(Enum):
    """终止原因枚举"""

    SEQ_COMPLETED = "seq_completed"  # 正常完成
    SEQ_NO_PROGRESS = "seq_no_progress"  # 无进展
    SEQ_BUDGET_EXHAUSTED = "seq_budget_exhausted"  # 预算耗尽
    SEQ_TOOL_FAIL_RECOVERABLE_EXHAUSTED = "seq_tool_fail_recoverable_exhausted"  # 工具失败重试耗尽
    SEQ_OUTPUT_INVALID_EXHAUSTED = "seq_output_invalid_exhausted"  # 输出无效耗尽
    SEQ_RESERVED_KEY_VIOLATION = "seq_reserved_key_violation"  # 保留字违规
    SEQ_CRASH_ORPHAN = "seq_crash_orphan"  # 崩溃孤立
    SEQ_ERROR = "seq_error"  # 执行错误


class FailureClass(Enum):
    """失败类别（用于路由决策）"""

    SUCCESS = "success"
    RETRYABLE = "retryable"
    VALIDATION_FAIL = "validation_fail"
    INTERNAL_BUG = "internal_bug"
    UNKNOWN = "unknown"


class RetryHint(Enum):
    """重试提示（指导外层决策）"""

    HANDOFF = "handoff"  # 正常移交
    STAGNATION = "stagnation"  # 停滞重试
    ESCALATE = "escalate"  # 升级
    COOLDOWN_RETRY = "cooldown_retry"  # 冷却重试
    MANUAL_REVIEW = "manual_review"  # 人工审查
    ALERT = "alert"  # 告警
    AUDIT_RECOVER = "audit_recover"  # 审计恢复


class StepStatus(Enum):
    """单步执行状态"""

    PENDING = "pending"
    STARTED = "started"
    TOOL_INVOKED = "tool_invoked"
    TOOL_COMPLETED = "tool_completed"
    FINISHING = "finishing"
    FINISHED = "finished"


# 保留字集合（禁止直接写入 metadata.seq.* 的字段）
RESERVED_KEYS = {
    "phase",
    "status",
    "retry_count",
    "max_retries",
    "completed_phases",
    "workflow_state",
    "task_id",
    "run_id",
}

# 默认预算配置
DEFAULT_BUDGET_CONFIG: dict[str, int | bool] = {
    "max_steps": 12,
    "max_tool_calls_total": 24,
    "max_no_progress_steps": 3,
    "max_wall_time_seconds": 120,
    "max_same_error_fingerprint": 2,
    "progress_info_incremental": False,
    "idempotency_check": True,
}

# 工具写入操作集合
WRITE_TOOL_NAMES = {"write_file", "search_replace", "edit_file", "append_to_file", "patch_apply", "apply_patch"}


# ═══════════════════════════════════════════════════════════════════════════
# 数据结构定义
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class SequentialBudget:
    """Sequential 预算配置"""

    max_steps: int = 12
    max_tool_calls_total: int = 24
    max_no_progress_steps: int = 3
    max_wall_time_seconds: int = 120
    max_same_error_fingerprint: int = 2
    progress_info_incremental: bool = False  # 是否将信息增量视为进展
    idempotency_check: bool = True  # 是否启用幂等检查


@dataclass
class SequentialStats:
    """Sequential 执行统计结果"""

    steps: int = 0
    tool_calls: int = 0
    no_progress: int = 0
    termination_reason: str = ""
    budget_exhausted: bool = False
    failure_class: str = ""
    retry_hint: str = ""
    error_fingerprints: dict[str, int] = field(default_factory=dict)
    tool_outcomes: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class StepDecision:
    """单步决策"""

    step_index: int
    intent: str  # 本步目标
    planned_actions: list[str] = field(default_factory=list)
    tool_plan: list[dict[str, Any]] = field(default_factory=list)
    expected_progress_signal: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)


@dataclass
class StepResult:
    """单步执行结果"""

    step_index: int
    status: StepStatus
    decision: StepDecision | None = None
    tool_call_id: str | None = None
    tool_result: dict[str, Any] | None = None
    error: str | None = None
    progress_detected: bool = False
    proposed_intents: list[dict[str, Any]] = field(default_factory=list)
    operation_digest: str | None = None  # 操作指纹


@dataclass
class SeqState:
    """Sequential 运行时状态（存储在 metadata.seq.* 中）"""

    seq_session_id: str = ""
    outer_attempt_id: str = ""
    step_index: int = 0
    tool_calls_count: int = 0
    no_progress_count: int = 0
    wall_time_elapsed: float = 0.0
    start_time: str | None = None
    status: str = "idle"  # idle, running, paused, completed, failed
    steps: list[dict[str, Any]] = field(default_factory=list)
    tool_outcomes: dict[str, dict[str, Any]] = field(default_factory=dict)
    error_fingerprints: dict[str, int] = field(default_factory=dict)
    last_error: str | None = None
    termination_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════════════
# 事件系统
# ═══════════════════════════════════════════════════════════════════════════


class SeqEventType:
    """Sequential 事件类型"""

    START = "seq.start"
    STEP = "seq.step"
    PROGRESS = "seq.progress"
    NO_PROGRESS = "seq.no_progress"
    TERMINATION = "seq.termination"
    RESERVED_KEY_VIOLATION = "seq.reserved_key_violation"
    ERROR = "seq.error"


@dataclass
class SeqEvent:
    """Sequential 事件"""

    event_type: str
    run_id: str
    role: str
    task_id: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    step_index: int = 0
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "run_id": self.run_id,
            "role": self.role,
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "step_index": self.step_index,
            "payload": self.payload,
        }


class SeqEventEmitter:
    """Sequential 事件发射器 with bounded history (O(1) eviction)."""

    def __init__(self, max_history_size: int = 1000) -> None:
        self._listeners: list[Callable[[SeqEvent], None]] = []
        # Use deque with maxlen for O(1) bounded list operations (automatic LRU eviction)
        self._event_history: deque[SeqEvent] = deque(maxlen=max_history_size)
        self._max_history_size = max_history_size

    def add_listener(self, listener: Callable[[SeqEvent], None]) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[SeqEvent], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def emit(self, event: SeqEvent) -> None:
        # deque with maxlen automatically discards oldest when full (O(1))
        self._event_history.append(event)

        # Also emit to kernelone event system for audit trail
        self._emit_to_kernelone(event)

        for listener in self._listeners:
            try:
                listener(event)
            except (RuntimeError, ValueError) as e:
                logger.warning(f"Sequential event listener failed: {e}")

    def _emit_to_kernelone(self, event: SeqEvent) -> None:
        """Emit seq event to kernelone event system for audit trail."""
        try:
            from polaris.kernelone.events import emit_event

            event_path = "runtime/events/seq"
            emit_event(
                event_path=event_path,
                kind="observation",
                actor="System",
                name=str(event.event_type or "").strip(),
                summary=f"Seq {event.event_type}: run_id={event.run_id}",
                refs={
                    "run_id": event.run_id,
                    "role": event.role,
                    "task_id": event.task_id,
                    "step_index": event.step_index,
                },
                output=event.payload,
            )
        except ImportError:
            # KernelOne events not available, skip audit trail
            pass
        except (RuntimeError, ValueError):
            # Audit emission must not break the main flow
            pass

    def get_events(self, run_id: str | None = None, limit: int = 100) -> list[SeqEvent]:
        events = list(self._event_history)
        if run_id:
            events = [e for e in events if e.run_id == run_id]
        return events[-limit:]


_global_seq_emitter: SeqEventEmitter | None = None


def get_seq_emitter() -> SeqEventEmitter:
    global _global_seq_emitter
    if _global_seq_emitter is None:
        _global_seq_emitter = SeqEventEmitter()
    return _global_seq_emitter


def emit_seq_event(
    event_type: str,
    run_id: str,
    role: str,
    task_id: str | None = None,
    step_index: int = 0,
    payload: dict[str, Any] | None = None,
) -> None:
    """发射 Sequential 事件"""
    event = SeqEvent(
        event_type=event_type,
        run_id=run_id,
        role=role,
        task_id=task_id,
        step_index=step_index,
        payload=payload or {},
    )
    get_seq_emitter().emit(event)


# ═══════════════════════════════════════════════════════════════════════════
# 状态代理（写保护）
# ═══════════════════════════════════════════════════════════════════════════


class ReservedKeyViolationError(Exception):
    """保留字违规异常"""

    def __init__(self, key: str, attempted_value: Any) -> None:
        self.key = key
        self.attempted_value = attempted_value
        super().__init__(f"Reserved key violation: '{key}' cannot be written to metadata.seq.*")


class SequentialStateProxy:
    """Sequential 状态写代理

    提供对 metadata.seq.* 的受控写入，防止污染主状态。
    所有写入都会经过保留字检查。
    """

    def __init__(
        self,
        state: SeqState,
        emit_violation: bool = True,
        fail_fast: bool = True,
    ) -> None:
        """
        Args:
            state: 底层状态对象
            emit_violation: 是否发射违规事件
            fail_fast: 是否在 Dev/Test 环境快速失败
        """
        self._state = state
        self._emit_violation = emit_violation
        self._fail_fast = fail_fast
        self._run_id = ""
        self._role = ""
        self._task_id: str | None = ""

    def set_context(self, run_id: str, role: str, task_id: str | None = None) -> None:
        """设置上下文信息用于事件发射"""
        self._run_id = run_id
        self._role = role
        self._task_id = task_id

    def write(self, key: str, value: Any) -> None:
        """写入状态字段（经过保留字检查）"""
        # 保留字检查
        if key in RESERVED_KEYS:
            self._handle_violation(key, value)
            return

        # 写入状态
        if hasattr(self._state, key):
            setattr(self._state, key, value)

    def _handle_violation(self, key: str, value: Any) -> None:
        """处理保留字违规"""
        error = ReservedKeyViolationError(key, value)

        # 发射违规事件
        if self._emit_violation and self._run_id:
            emit_seq_event(
                event_type=SeqEventType.RESERVED_KEY_VIOLATION,
                run_id=self._run_id,
                role=self._role,
                task_id=self._task_id,
                payload={
                    "key": key,
                    "value_type": type(value).__name__,
                    "error": str(error),
                },
            )

        # Dev/Test 环境快速失败
        if self._fail_fast:
            raise error

        logger.warning(f"Reserved key violation blocked: {key}")

    def get_state(self) -> SeqState:
        """获取底层状态"""
        return self._state


# ═══════════════════════════════════════════════════════════════════════════
# 进展检测器
# ═══════════════════════════════════════════════════════════════════════════


class SeqProgressDetector:
    """Sequential 进展检测器

    检测步骤是否有实际进展。
    支持 Type-A (Artifact推进)、Type-B (Validation改善)、Type-C (Blocker明确化)、Type-D (信息增量)
    """

    def __init__(
        self,
        progress_info_incremental: bool = False,
        max_no_progress_steps: int = 3,
    ) -> None:
        self.progress_info_incremental = progress_info_incremental
        self.max_no_progress_steps = max_no_progress_steps
        self._previous_state: dict[str, Any] = {}

    def detect_progress(
        self,
        tool_result: dict[str, Any] | None,
        step_decision: StepDecision,
        current_state: SeqState,
    ) -> tuple[bool, list[str]]:
        """
        检测是否有进展

        Returns:
            (progress_detected, signals) - 是否有进展以及检测到的信号
        """
        signals = []

        # Type-A: Artifact 推进
        if tool_result and self._check_artifact_progress(tool_result):
            signals.append("artifact_progress")
            return True, signals

        # Type-B: Validation 改善
        if self._check_validation_progress(tool_result):
            signals.append("validation_progress")
            return True, signals

        # Type-C: Blocker 明确化
        if self._check_blocker_clarification(step_decision):
            signals.append("blocker_clarified")
            return True, signals

        # Type-D: 信息增量（可选）
        if self.progress_info_incremental and self._check_info_incremental(step_decision):
            signals.append("info_incremental")
            return True, signals

        return False, signals

    def _check_artifact_progress(self, tool_result: dict[str, Any]) -> bool:
        """Type-A: 检查是否有文件变更"""
        if not isinstance(tool_result, dict):
            return False

        # 检查写入类工具结果
        tool_name = str(tool_result.get("tool", "")).lower()
        if tool_name in WRITE_TOOL_NAMES:
            return tool_result.get("success", False)

        # 检查 changed_files 增加（如果有）
        return tool_result.get("changed_files_count", 0) > 0

    def _check_validation_progress(self, tool_result: dict[str, Any] | None) -> bool:
        """Type-B: 检查验证是否改善"""
        if not isinstance(tool_result, dict):
            return False

        # 检查测试通过数提升
        test_passed = tool_result.get("tests_passed_delta", 0)
        if test_passed > 0:
            return True

        # 检查门禁状态变化
        gate_status = tool_result.get("gate_status_delta")
        return bool(gate_status and gate_status.get("from_fail_to_pass"))

    def _check_blocker_clarification(self, step_decision: StepDecision) -> bool:
        """Type-C: 检查 blocker 是否明确化"""
        # 检查意图中是否有新的 blocker 记录
        return any("blocker" in str(intent).lower() for intent in step_decision.planned_actions)

    def _check_info_incremental(self, step_decision: StepDecision) -> bool:
        """Type-D: 检查是否有信息增量"""
        # 检查是否有新的证据/依赖关系发现
        for signal in step_decision.expected_progress_signal:
            if signal in ("dependency_found", "evidence_discovered", "unknown_identified"):
                return True
        return False


# ═══════════════════════════════════════════════════════════════════════════
# 核心引擎
# ═══════════════════════════════════════════════════════════════════════════


class SequentialEngine:
    """Sequential 执行引擎

    驱动结构化的多步骤执行，包含：
    - 预算控制（步数、工具调用、无进展次数、时间）
    - 进展检测
    - 幂等恢复
    - 终止条件管理
    """

    def __init__(
        self,
        workspace: str,
        budget: SequentialBudget | None = None,
        trace_level: str = "summary",
    ) -> None:
        """
        Args:
            workspace: 工作区路径
            budget: 预算配置（默认使用 DEFAULT_BUDGET_CONFIG）
            trace_level: 跟踪级别 (off|summary|detailed)
        """
        self.workspace = workspace
        self.budget = budget or SequentialBudget(
            max_steps=int(DEFAULT_BUDGET_CONFIG["max_steps"]),
            max_tool_calls_total=int(DEFAULT_BUDGET_CONFIG["max_tool_calls_total"]),
            max_no_progress_steps=int(DEFAULT_BUDGET_CONFIG["max_no_progress_steps"]),
            max_wall_time_seconds=int(DEFAULT_BUDGET_CONFIG["max_wall_time_seconds"]),
            max_same_error_fingerprint=int(DEFAULT_BUDGET_CONFIG["max_same_error_fingerprint"]),
            progress_info_incremental=bool(DEFAULT_BUDGET_CONFIG["progress_info_incremental"]),
            idempotency_check=bool(DEFAULT_BUDGET_CONFIG["idempotency_check"]),
        )
        self.trace_level = trace_level

        # 内部状态
        self._state = SeqState()
        self._state_proxy = SequentialStateProxy(self._state)
        self._progress_detector = SeqProgressDetector(
            progress_info_incremental=self.budget.progress_info_incremental,
            max_no_progress_steps=self.budget.max_no_progress_steps,
        )
        self._llm_caller = None  # 延迟初始化
        self._tool_gateway = None  # 延迟初始化
        self._last_llm_digest: str | None = None

        # 当前执行上下文
        self._current_role = ""
        self._current_run_id = ""
        self._current_task_id: str | None = ""

    def set_context(self, role: str, run_id: str, task_id: str | None = None) -> None:
        """设置执行上下文"""
        self._current_role = role
        self._current_run_id = run_id
        self._current_task_id = task_id
        self._state_proxy.set_context(run_id, role, task_id)

    def set_dependencies(
        self,
        llm_caller: Any = None,
        tool_gateway: Any = None,
    ) -> None:
        """设置依赖组件（延迟初始化）"""
        self._llm_caller = llm_caller
        self._tool_gateway = tool_gateway

    async def execute(
        self,
        initial_message: str,
        profile: Any,
    ) -> SequentialStats:
        """
        执行 Sequential 循环

        Args:
            initial_message: 初始消息
            profile: 角色 Profile

        Returns:
            SequentialStats 执行统计结果
        """
        # 初始化会话
        await self._init_session()

        # 发射开始事件
        emit_seq_event(
            event_type=SeqEventType.START,
            run_id=self._current_run_id,
            role=self._current_role,
            task_id=self._current_task_id,
            payload={
                "budget": {
                    "max_steps": self.budget.max_steps,
                    "max_tool_calls_total": self.budget.max_tool_calls_total,
                    "max_no_progress_steps": self.budget.max_no_progress_steps,
                    "max_wall_time_seconds": self.budget.max_wall_time_seconds,
                },
                "message_preview": initial_message[:200],
            },
        )

        start_time = time.time()
        step_index = 0

        try:
            while step_index < self.budget.max_steps:
                # 检查时间预算
                elapsed = time.time() - start_time
                if elapsed > self.budget.max_wall_time_seconds:
                    await self._terminate(TerminationReason.SEQ_BUDGET_EXHAUSTED)
                    break

                # 检查工具调用预算
                if self._state.tool_calls_count >= self.budget.max_tool_calls_total:
                    await self._terminate(TerminationReason.SEQ_BUDGET_EXHAUSTED)
                    break

                # 执行单步
                step_result = await self._execute_step(
                    step_index=step_index,
                    message=initial_message if step_index == 0 else "",
                    profile=profile,
                )

                # 检查无进展
                if not step_result.progress_detected:
                    self._state.no_progress_count += 1

                    if self._state.no_progress_count >= self.budget.max_no_progress_steps:
                        emit_seq_event(
                            event_type=SeqEventType.NO_PROGRESS,
                            run_id=self._current_run_id,
                            role=self._current_role,
                            task_id=self._current_task_id,
                            step_index=step_index,
                            payload={"no_progress_count": self._state.no_progress_count},
                        )
                        await self._terminate(TerminationReason.SEQ_NO_PROGRESS)
                        break
                else:
                    # 有进展，重置无进展计数
                    self._state.no_progress_count = 0
                    emit_seq_event(
                        event_type=SeqEventType.PROGRESS,
                        run_id=self._current_run_id,
                        role=self._current_role,
                        task_id=self._current_task_id,
                        step_index=step_index,
                    )

                # 检查错误指纹（如果存在错误）
                if step_result.error and step_result.error.strip():
                    self._track_error_fingerprint(step_result.error)

                    fingerprint = self._compute_error_fingerprint(step_result.error)
                    error_count = self._state.error_fingerprints.get(fingerprint, 0)

                    if error_count >= self.budget.max_same_error_fingerprint:
                        await self._terminate(TerminationReason.SEQ_TOOL_FAIL_RECOVERABLE_EXHAUSTED)
                        break

                # 记录步骤
                self._record_step(step_result)

                # 检查是否完成
                if step_result.status == StepStatus.FINISHED and step_result.tool_result:
                    # 检查是否有更多工具调用需要执行
                    has_pending_tools = self._check_pending_tools(step_result)
                    if not has_pending_tools:
                        await self._terminate(TerminationReason.SEQ_COMPLETED)
                        break

                step_index += 1
                self._state.step_index = step_index

            # 预算耗尽检查
            if step_index >= self.budget.max_steps and self._state.status != "completed":
                await self._terminate(TerminationReason.SEQ_BUDGET_EXHAUSTED)

        except (RuntimeError, ValueError) as e:
            logger.exception(f"Sequential engine error at step {step_index}")
            await self._terminate_with_error(str(e))

        # 返回统计结果
        return self._build_stats()

    async def _init_session(self) -> None:
        """初始化会话"""
        self._state = SeqState(
            seq_session_id=str(uuid.uuid4()),
            start_time=datetime.now(timezone.utc).isoformat(),
            status="running",
        )
        self._state_proxy = SequentialStateProxy(self._state)
        self._state_proxy.set_context(self._current_run_id, self._current_role, self._current_task_id)

    async def _execute_step(
        self,
        step_index: int,
        message: str,
        profile: Any,
    ) -> StepResult:
        """执行单步"""
        # 发射步骤开始事件
        emit_seq_event(
            event_type=SeqEventType.STEP,
            run_id=self._current_run_id,
            role=self._current_role,
            task_id=self._current_task_id,
            step_index=step_index,
            payload={"status": "started"},
        )

        # 创建步骤决策
        decision = StepDecision(
            step_index=step_index,
            intent=f"Step {step_index} execution",
            planned_actions=[],
            tool_plan=[],
        )

        # 记录步骤开始
        result = StepResult(
            step_index=step_index,
            status=StepStatus.STARTED,
            decision=decision,
            progress_detected=False,
        )

        # 如果没有 LLM 调用器，返回空结果
        if not self._llm_caller:
            result.status = StepStatus.FINISHED
            return result

        prompt = self._build_step_prompt(step_index=step_index, message=message)
        try:
            llm_raw = await self._invoke_llm(prompt=prompt, profile=profile)
        except (RuntimeError, ValueError) as exc:
            result.error = f"llm_call_failed: {exc}"
            result.status = StepStatus.FINISHED
            return result

        llm_payload = self._normalize_llm_payload(llm_raw)
        llm_text = llm_payload["text"]
        decision.intent = llm_payload["intent"] or f"Step {step_index} execution"
        decision.planned_actions = llm_payload["planned_actions"]
        decision.tool_plan = llm_payload["tool_plan"]

        if llm_text:
            result.tool_result = {
                "tool": "llm_response",
                "success": True,
                "has_more_tools": False,
                "content_length": len(llm_text),
            }
            result.progress_detected = self._record_llm_progress(llm_text)
        else:
            result.tool_result = {
                "tool": "llm_response",
                "success": False,
                "has_more_tools": False,
                "content_length": 0,
            }
            result.progress_detected = False

        result.status = StepStatus.FINISHED
        return result

    def _build_step_prompt(self, *, step_index: int, message: str) -> str:
        task = str(message or "").strip() or "Continue the assigned task."
        role = str(self._current_role or "director").strip()
        return (
            f"Role: {role}\n"
            f"Sequential Step: {step_index}\n"
            "You must provide concrete next-step reasoning and action.\n\n"
            f"Task:\n{task}\n"
        )

    async def _invoke_llm(self, *, prompt: str, profile: Any) -> Any:
        """Invoke injected LLM caller with tolerant signature matching."""
        if not callable(self._llm_caller):
            raise RuntimeError("SequentialEngine llm_caller is not callable")

        call_specs = (
            (),
            {"prompt": prompt, "role": self._current_role, "profile": profile},
            {"prompt": prompt, "role": self._current_role},
            {"prompt": prompt},
        )
        last_signature_error: Exception | None = None
        for kwargs in call_specs:
            try:
                outcome = self._llm_caller(**kwargs) if kwargs else self._llm_caller(prompt)
            except TypeError as exc:
                last_signature_error = exc
                continue
            if asyncio.iscoroutine(outcome):
                return await outcome
            return outcome
        if last_signature_error:
            raise last_signature_error
        raise RuntimeError("SequentialEngine failed to invoke llm_caller")

    def _normalize_llm_payload(self, raw: Any) -> dict[str, Any]:
        text = ""
        intent = ""
        planned_actions: list[str] = []
        tool_plan: list[dict[str, Any]] = []

        if isinstance(raw, dict):
            text = str(raw.get("content") or raw.get("response") or raw.get("text") or raw.get("message") or "").strip()
            intent = str(raw.get("intent") or "").strip()
            if isinstance(raw.get("planned_actions"), list):
                planned_actions = [str(item).strip() for item in raw["planned_actions"] if str(item).strip()]
            raw_tool_plan = raw.get("tool_plan")
            if isinstance(raw_tool_plan, list):
                tool_plan = [item for item in raw_tool_plan if isinstance(item, dict)][:8]
        else:
            text = str(raw or "").strip()

        if not planned_actions and text:
            first_line = text.splitlines()[0].strip()
            if first_line:
                planned_actions = [first_line]

        return {
            "text": text,
            "intent": intent,
            "planned_actions": planned_actions,
            "tool_plan": tool_plan,
        }

    def _record_llm_progress(self, text: str) -> bool:
        token = str(text or "").strip()
        if not token:
            return False
        digest = hashlib.sha256(token.encode("utf-8", errors="ignore")).hexdigest()[:16]
        if digest == self._last_llm_digest:
            return False
        self._last_llm_digest = digest
        return True

    def _check_pending_tools(self, step_result: StepResult) -> bool:
        """检查是否有待执行的工具"""
        # 如果有工具结果且成功，可能需要继续执行
        if step_result.tool_result:
            return step_result.tool_result.get("has_more_tools", False)
        return False

    def _track_error_fingerprint(self, error: str) -> None:
        """跟踪错误指纹"""
        fingerprint = self._compute_error_fingerprint(error)
        current = self._state.error_fingerprints.get(fingerprint, 0)
        self._state.error_fingerprints[fingerprint] = current + 1

    def _compute_error_fingerprint(self, error: str) -> str:
        """计算错误指纹"""
        # 简化实现：取错误消息的哈希
        normalized = error.lower().strip()[:100]
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def _record_step(self, step_result: StepResult) -> None:
        """记录步骤到状态"""
        step_record = {
            "step_index": step_result.step_index,
            "status": step_result.status.value,
            "progress_detected": step_result.progress_detected,
            "error": step_result.error,
            "tool_call_id": step_result.tool_call_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._state.steps.append(step_record)

    async def _terminate(self, reason: TerminationReason) -> None:
        """终止执行"""
        self._state.status = "completed"
        self._state.termination_reason = reason.value

        emit_seq_event(
            event_type=SeqEventType.TERMINATION,
            run_id=self._current_run_id,
            role=self._current_role,
            task_id=self._current_task_id,
            step_index=self._state.step_index,
            payload={
                "reason": reason.value,
                "steps": self._state.step_index + 1,
                "tool_calls": self._state.tool_calls_count,
            },
        )

    async def _terminate_with_error(self, error: str) -> None:
        """因错误终止"""
        self._state.status = "failed"
        self._state.last_error = error
        self._state.termination_reason = TerminationReason.SEQ_ERROR.value

        emit_seq_event(
            event_type=SeqEventType.ERROR,
            run_id=self._current_run_id,
            role=self._current_role,
            task_id=self._current_task_id,
            step_index=self._state.step_index,
            payload={"error": error},
        )

    def _build_stats(self) -> SequentialStats:
        """构建统计结果"""
        # 计算 failure_class 和 retry_hint
        failure_class, retry_hint = self._map_termination_to_action(self._state.termination_reason or "")

        return SequentialStats(
            steps=self._state.step_index + 1,
            tool_calls=self._state.tool_calls_count,
            no_progress=self._state.no_progress_count,
            termination_reason=self._state.termination_reason or "",
            budget_exhausted=self._state.termination_reason
            in (
                TerminationReason.SEQ_BUDGET_EXHAUSTED.value,
                TerminationReason.SEQ_NO_PROGRESS.value,
            ),
            failure_class=failure_class.value if failure_class else "",
            retry_hint=retry_hint.value if retry_hint else "",
            error_fingerprints=dict(self._state.error_fingerprints),
            tool_outcomes=dict(self._state.tool_outcomes),
        )

    def _map_termination_to_action(
        self,
        termination_reason: str,
    ) -> tuple[FailureClass, RetryHint]:
        """终止原因映射到外层动作"""
        mapping = {
            TerminationReason.SEQ_COMPLETED.value: (FailureClass.SUCCESS, RetryHint.HANDOFF),
            TerminationReason.SEQ_NO_PROGRESS.value: (FailureClass.RETRYABLE, RetryHint.STAGNATION),
            TerminationReason.SEQ_BUDGET_EXHAUSTED.value: (FailureClass.RETRYABLE, RetryHint.ESCALATE),
            TerminationReason.SEQ_TOOL_FAIL_RECOVERABLE_EXHAUSTED.value: (
                FailureClass.RETRYABLE,
                RetryHint.COOLDOWN_RETRY,
            ),
            TerminationReason.SEQ_OUTPUT_INVALID_EXHAUSTED.value: (
                FailureClass.VALIDATION_FAIL,
                RetryHint.MANUAL_REVIEW,
            ),
            TerminationReason.SEQ_RESERVED_KEY_VIOLATION.value: (FailureClass.INTERNAL_BUG, RetryHint.ALERT),
            TerminationReason.SEQ_CRASH_ORPHAN.value: (FailureClass.UNKNOWN, RetryHint.AUDIT_RECOVER),
            TerminationReason.SEQ_ERROR.value: (FailureClass.UNKNOWN, RetryHint.ESCALATE),
        }
        return mapping.get(termination_reason, (FailureClass.UNKNOWN, RetryHint.ESCALATE))

    # ═══════════════════════════════════════════════════════════════════════════
    # 幂等恢复支持
    # ═══════════════════════════════════════════════════════════════════════════

    async def recover(self, saved_state: SeqState) -> bool:
        """
        从保存的状态恢复

        Args:
            saved_state: 之前保存的状态

        Returns:
            是否恢复成功
        """
        if not saved_state.seq_session_id:
            return False

        self._state = saved_state
        self._state_proxy = SequentialStateProxy(self._state)
        self._state_proxy.set_context(self._current_run_id, self._current_role, self._current_task_id)

        # 恢复后从最后一个未完成的步骤继续
        for i, step in enumerate(self._state.steps):
            if step.get("status") != StepStatus.FINISHED.value:
                logger.info(f"Recovering from step {i}")
                return True

        return True

    def get_state(self) -> SeqState:
        """获取当前状态（用于持久化）"""
        return self._state


# ═══════════════════════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════════════════════


def create_sequential_budget(
    max_steps: int | None = None,
    max_tool_calls_total: int | None = None,
    max_no_progress_steps: int | None = None,
    max_wall_time_seconds: int | None = None,
    progress_info_incremental: bool | None = None,
    idempotency_check: bool | None = None,
    **kwargs: int | bool,
) -> SequentialBudget:
    """创建预算配置的便捷函数"""
    return SequentialBudget(
        max_steps=max_steps if max_steps is not None else DEFAULT_BUDGET_CONFIG["max_steps"],
        max_tool_calls_total=max_tool_calls_total
        if max_tool_calls_total is not None
        else DEFAULT_BUDGET_CONFIG["max_tool_calls_total"],
        max_no_progress_steps=max_no_progress_steps
        if max_no_progress_steps is not None
        else DEFAULT_BUDGET_CONFIG["max_no_progress_steps"],
        max_wall_time_seconds=max_wall_time_seconds
        if max_wall_time_seconds is not None
        else DEFAULT_BUDGET_CONFIG["max_wall_time_seconds"],
        progress_info_incremental=progress_info_incremental
        if progress_info_incremental is not None
        else bool(DEFAULT_BUDGET_CONFIG["progress_info_incremental"]),
        idempotency_check=idempotency_check
        if idempotency_check is not None
        else bool(DEFAULT_BUDGET_CONFIG["idempotency_check"]),
    )


def should_enable_sequential(role: str, enabled_roles: list[str] | None = None) -> bool:
    """
    判断是否应该为角色启用 Sequential 模式

    Args:
        role: 角色标识
        enabled_roles: 启用的角色列表（默认 ["director", "adaptive"]）

    Returns:
        是否启用
    """
    if enabled_roles is None:
        enabled_roles = ["director", "adaptive"]

    role_lower = role.lower().strip()
    return role_lower in [r.lower().strip() for r in enabled_roles]
