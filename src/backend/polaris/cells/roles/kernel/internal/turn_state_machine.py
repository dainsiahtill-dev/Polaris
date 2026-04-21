"""
Turn State Machine - 事务型Turn的状态机

Intent: Models the internal execution state of a SINGLE AI conversation turn.
- TurnState tracks: IDLE → CONTEXT_BUILT → DECISION_REQUESTED → TOOL_EXECUTION → ...
- This is orthogonal to TaskPhase which tracks business task lifecycle.

IMPORTANT: Do NOT confuse TurnState with TaskPhase.
- TurnState = AI turn execution state (this file)
- TaskPhase = Business task lifecycle phases (polaris/domain/state_machine/task_phase.py)

核心约束：
1. 禁止从TOOL_BATCH_EXECUTED回到DECISION_REQUESTED（防止continuation loop）
2. FINALIZATION_REQUESTED禁止触发TOOL_BATCH_EXECUTING（防止工具链）
3. 所有状态转换必须经过显式验证

已适配 KernelOne StateMachinePort 接口 (polaris/kernelone/state_machine.py)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from polaris.kernelone.state_machine import InvalidStateTransitionError

# =============================================================================
# State Enum
# =============================================================================


class TurnState(str, Enum):
    """Turn事务状态机 (StrEnum for serialization)"""

    IDLE = "idle"
    CONTEXT_BUILT = "context_built"
    DECISION_REQUESTED = "decision_requested"  # LLM请求已发出
    DECISION_RECEIVED = "decision_received"  # 原始响应已收到
    DECISION_DECODED = "decision_decoded"  # TurnDecision已解码

    # 分支：直接完成
    FINAL_ANSWER_READY = "final_answer_ready"

    # 分支：需要工具
    TOOL_BATCH_EXECUTING = "tool_batch_executing"
    TOOL_BATCH_EXECUTED = "tool_batch_executed"

    # 分支：收口
    FINALIZATION_REQUESTED = "finalization_requested"  # 仅LLM_ONCE模式
    FINALIZATION_RECEIVED = "finalization_received"

    # 分支：移交
    HANDOFF_WORKFLOW = "handoff_workflow"
    HANDOFF_DEVELOPMENT = "handoff_development"

    # 分支：等待用户输入（ASK_USER 决策的干净终端状态）
    SUSPENDED = "suspended"

    # 终止状态
    COMPLETED = "completed"
    FAILED = "failed"


# =============================================================================
# State Transition Rules
# =============================================================================

# Valid state transitions
_VALID_TRANSITIONS: dict[TurnState, set[TurnState]] = {
    TurnState.IDLE: {TurnState.CONTEXT_BUILT},
    TurnState.CONTEXT_BUILT: {TurnState.DECISION_REQUESTED},
    TurnState.DECISION_REQUESTED: {TurnState.DECISION_RECEIVED, TurnState.FAILED},
    TurnState.DECISION_RECEIVED: {TurnState.DECISION_DECODED, TurnState.FAILED},
    TurnState.DECISION_DECODED: {
        TurnState.FINAL_ANSWER_READY,
        TurnState.TOOL_BATCH_EXECUTING,
        TurnState.HANDOFF_WORKFLOW,
        TurnState.HANDOFF_DEVELOPMENT,
        TurnState.SUSPENDED,
        TurnState.FAILED,
    },
    TurnState.TOOL_BATCH_EXECUTING: {
        TurnState.TOOL_BATCH_EXECUTED,
        TurnState.DECISION_DECODED,  # retry rollback: allow rollback to DECISION_DECODED for contract violation retry
        TurnState.FAILED,
    },
    TurnState.TOOL_BATCH_EXECUTED: {
        TurnState.COMPLETED,  # finalize_mode=none/local
        TurnState.FINALIZATION_REQUESTED,  # finalize_mode=llm_once
        TurnState.HANDOFF_WORKFLOW,  # 有pending async
        TurnState.HANDOFF_DEVELOPMENT,
        TurnState.FAILED,
    },
    TurnState.FINALIZATION_REQUESTED: {
        TurnState.FINALIZATION_RECEIVED,
        TurnState.HANDOFF_WORKFLOW,
        TurnState.HANDOFF_DEVELOPMENT,
        TurnState.FAILED,
    },
    TurnState.FINALIZATION_RECEIVED: {TurnState.COMPLETED, TurnState.FAILED},
    TurnState.FINAL_ANSWER_READY: {TurnState.COMPLETED},
    TurnState.HANDOFF_WORKFLOW: {TurnState.COMPLETED},  # 移交完成，当前turn结束
    TurnState.HANDOFF_DEVELOPMENT: {TurnState.COMPLETED},  # 开发移交完成，当前turn结束
    TurnState.COMPLETED: set(),  # 终止状态
    TurnState.FAILED: set(),  # 终止状态
}

# 关键约束：明确禁止的转换（这些是旧架构的根源问题）
_FORBIDDEN_TRANSITIONS: set[tuple[TurnState, TurnState]] = {
    # 工具执行后禁止回到决策请求（这是旧continuation loop的根源）
    (TurnState.TOOL_BATCH_EXECUTED, TurnState.DECISION_REQUESTED),
    (TurnState.TOOL_BATCH_EXECUTED, TurnState.CONTEXT_BUILT),
    (TurnState.TOOL_BATCH_EXECUTED, TurnState.DECISION_DECODED),
    # finalization禁止触发新一轮工具
    (TurnState.FINALIZATION_REQUESTED, TurnState.TOOL_BATCH_EXECUTING),
    (TurnState.FINALIZATION_REQUESTED, TurnState.TOOL_BATCH_EXECUTED),
    # 禁止从任何中间状态跳回IDLE
    (TurnState.TOOL_BATCH_EXECUTING, TurnState.IDLE),
    (TurnState.FINALIZATION_REQUESTED, TurnState.IDLE),
    (TurnState.DECISION_REQUESTED, TurnState.IDLE),
    # 禁止跳过必要阶段
    (TurnState.IDLE, TurnState.TOOL_BATCH_EXECUTING),
    (TurnState.IDLE, TurnState.FINALIZATION_REQUESTED),
    # SUSPENDED是终态，禁止从它转换到任何状态（保持WAITING_HUMAN语义）
    (TurnState.SUSPENDED, TurnState.TOOL_BATCH_EXECUTING),
    (TurnState.SUSPENDED, TurnState.DECISION_REQUESTED),
    (TurnState.SUSPENDED, TurnState.CONTEXT_BUILT),
}

# Terminal states
_TERMINAL_STATES: set[TurnState] = {TurnState.COMPLETED, TurnState.FAILED, TurnState.SUSPENDED}


# =============================================================================
# State Machine (Implements StateMachinePort)
# =============================================================================


@dataclass
class TurnStateMachine:
    """
    单个turn的状态机实例

    实现 StateMachinePort 接口，提供标准状态机行为。

    Phase 3.5 Enhancements:
    - Hierarchical sub-states
    - Pause/resume capability
    - Rollback to checkpoint

    使用示例：
        sm = TurnStateMachine(turn_id="turn_1")
        sm.transition_to(TurnState.CONTEXT_BUILT)
        sm.transition_to(TurnState.DECISION_REQUESTED)
        # ... 状态转换会自动验证
    """

    # Identity
    turn_id: str = ""

    # Internal state
    _state: TurnState = field(default=TurnState.IDLE)
    _history: list[tuple[TurnState, float]] = field(default_factory=list)
    _metadata: dict[str, Any] = field(default_factory=dict)

    # Phase 3.5: Hierarchical sub-states
    _sub_state: str = ""
    _sub_state_history: list[tuple[str, float]] = field(default_factory=list)

    # Phase 3.5: Pause/resume support
    _is_paused: bool = False
    _pause_metadata: dict[str, Any] = field(default_factory=dict)

    # Phase 3.5: Checkpoint support
    _checkpoints: list[dict[str, Any]] = field(default_factory=list)

    # Class-level transition rules
    _VALID_TRANSITIONS: dict[TurnState, set[TurnState]] = field(default_factory=lambda: _VALID_TRANSITIONS.copy())
    _FORBIDDEN_TRANSITIONS: set[tuple[TurnState, TurnState]] = field(
        default_factory=lambda: _FORBIDDEN_TRANSITIONS.copy()
    )
    _TERMINAL_STATES: set[TurnState] = field(default_factory=lambda: _TERMINAL_STATES.copy())

    def __post_init__(self) -> None:
        """Initialize history with initial state."""
        if not self._history:
            self._history.append((TurnState.IDLE, time.time()))

    # -------------------------------------------------------------------------
    # StateMachinePort Implementation
    # -------------------------------------------------------------------------

    @property
    def current_state(self) -> TurnState:
        """Return the current state enum value."""
        return self._state

    @property
    def state(self) -> TurnState:
        """Alias for current_state (legacy compatibility)."""
        return self._state

    @state.setter
    def state(self, value: TurnState) -> None:
        """Set current state directly (use transition_to for valid transitions)."""
        self._state = value

    def can_transition_to(self, new_state: TurnState) -> bool:
        """检查是否可以转换到目标状态"""
        if (self._state, new_state) in self._FORBIDDEN_TRANSITIONS:
            return False
        return new_state in self._VALID_TRANSITIONS.get(self._state, set())

    def transition_to(self, new_state: TurnState, context: dict[str, Any] | None = None) -> None:
        """
        执行状态转换，强制检查规则

        所有状态转换必须经过此函数。
        违反规则会抛出InvalidStateTransitionError。
        """
        # Check forbidden transitions
        if (self._state, new_state) in self._FORBIDDEN_TRANSITIONS:
            raise InvalidStateTransitionError(
                f"Invalid transition: {self._state.name} -> {new_state.name} "
                f"(FORBIDDEN: architectural violation: prevents continuation loop)",
                current_state=self._state.name,
                target_state=new_state.name,
            )

        # Check allowed transitions
        allowed = self._VALID_TRANSITIONS.get(self._state, set())
        if new_state not in allowed:
            allowed_str = ", ".join(s.name for s in allowed) if allowed else "none (terminal)"
            raise InvalidStateTransitionError(
                f"Invalid transition: {self._state.name} -> {new_state.name}. "
                f"Valid transitions from {self._state.name}: {allowed_str}",
                current_state=self._state.name,
                target_state=new_state.name,
            )

        # Execute transition
        old_state = self._state
        self._state = new_state
        self._history.append((new_state, time.time()))

        # [DEBUG] Structured log for every state transition — critical for observability
        import logging

        logging.getLogger(__name__).debug(
            "[DEBUG] turn_state_transition: turn_id=%s %s -> %s",
            self.turn_id,
            old_state.name,
            new_state.name,
        )

        if context:
            self._metadata[f"{old_state.name}_to_{new_state.name}"] = context

    def is_terminal(self) -> bool:
        """是否处于终止状态"""
        return self._state in self._TERMINAL_STATES

    def is_failed(self) -> bool:
        """是否失败"""
        return self._state == TurnState.FAILED

    # -------------------------------------------------------------------------
    # Additional Methods
    # -------------------------------------------------------------------------

    def get_history(self) -> list[tuple[TurnState, float]]:
        """获取状态历史"""
        return self._history.copy()

    def get_duration_ms(self) -> int:
        """获取总耗时（毫秒）"""
        if len(self._history) < 2:
            return 0
        start = self._history[0][1]
        end = self._history[-1][1]
        return int((end - start) * 1000)

    def assert_in_state(self, expected: TurnState | set[TurnState]) -> None:
        """断言当前状态，用于开发时防护"""
        expected_set = {expected} if isinstance(expected, TurnState) else expected
        if self._state not in expected_set:
            raise AssertionError(
                f"Expected state in {[s.name for s in expected_set]}, but current state is {self._state.name}"
            )

    # -------------------------------------------------------------------------
    # Phase 3.5: Hierarchical Sub-States
    # -------------------------------------------------------------------------

    def set_sub_state(self, sub_state: str) -> None:
        """Phase 3.5: Set hierarchical sub-state within current state.

        Args:
            sub_state: Sub-state identifier
        """
        self._sub_state = sub_state
        self._sub_state_history.append((sub_state, time.time()))

    def get_sub_state(self) -> str:
        """Phase 3.5: Get current sub-state.

        Returns:
            Current sub-state string
        """
        return self._sub_state

    def clear_sub_state(self) -> None:
        """Phase 3.5: Clear current sub-state."""
        self._sub_state = ""

    # -------------------------------------------------------------------------
    # Phase 3.5: Pause/Resume Support
    # -------------------------------------------------------------------------

    def pause(self, metadata: dict[str, Any] | None = None) -> None:
        """Phase 3.5: Pause the state machine.

        Args:
            metadata: Optional metadata about pause reason
        """
        if self._is_paused:
            return
        self._is_paused = True
        self._pause_metadata = dict(metadata) if metadata else {}
        self._pause_metadata["paused_at"] = time.time()
        import logging

        logging.getLogger(__name__).debug(
            "[DEBUG] turn_state_paused: turn_id=%s state=%s",
            self.turn_id,
            self._state.name,
        )

    def resume(self) -> dict[str, Any]:
        """Phase 3.5: Resume the state machine.

        Returns:
            Pause metadata for reference

        Raises:
            RuntimeError: If not currently paused
        """
        if not self._is_paused:
            raise RuntimeError(f"Cannot resume TurnStateMachine {self.turn_id}: not paused")
        pause_duration = time.time() - self._pause_metadata.get("paused_at", time.time())
        self._is_paused = False
        result = dict(self._pause_metadata)
        result["pause_duration_s"] = pause_duration
        self._pause_metadata = {}
        import logging

        logging.getLogger(__name__).debug(
            "[DEBUG] turn_state_resumed: turn_id=%s state=%s pause_duration=%.2f",
            self.turn_id,
            self._state.name,
            pause_duration,
        )
        return result

    def is_paused(self) -> bool:
        """Phase 3.5: Check if state machine is paused.

        Returns:
            True if paused
        """
        return self._is_paused

    # -------------------------------------------------------------------------
    # Phase 3.5: Checkpoint and Rollback
    # -------------------------------------------------------------------------

    def create_checkpoint(self, label: str = "") -> str:
        """Phase 3.5: Create a checkpoint of current state.

        Args:
            label: Optional label for the checkpoint

        Returns:
            Checkpoint ID
        """
        checkpoint_id = f"cp_{len(self._checkpoints)}_{int(time.time() * 1000)}"
        checkpoint = {
            "id": checkpoint_id,
            "label": label,
            "state": self._state,
            "sub_state": self._sub_state,
            "history": list(self._history),
            "sub_state_history": list(self._sub_state_history),
            "metadata": dict(self._metadata),
            "timestamp": time.time(),
        }
        self._checkpoints.append(checkpoint)
        import logging

        logging.getLogger(__name__).debug(
            "[DEBUG] checkpoint_created: turn_id=%s checkpoint_id=%s state=%s",
            self.turn_id,
            checkpoint_id,
            self._state.name,
        )
        return checkpoint_id

    def rollback_to_checkpoint(self, checkpoint_id: str) -> bool:
        """Phase 3.5: Rollback to a previous checkpoint.

        Args:
            checkpoint_id: ID of checkpoint to rollback to

        Returns:
            True if rollback successful

        Raises:
            ValueError: If checkpoint not found
        """
        target_checkpoint = None
        for cp in self._checkpoints:
            if cp["id"] == checkpoint_id:
                target_checkpoint = cp
                break

        if target_checkpoint is None:
            raise ValueError(f"Checkpoint {checkpoint_id} not found")

        self._state = target_checkpoint["state"]
        self._sub_state = target_checkpoint["sub_state"]
        self._history = list(target_checkpoint["history"])
        self._sub_state_history = list(target_checkpoint["sub_state_history"])
        self._metadata = dict(target_checkpoint["metadata"])

        import logging

        logging.getLogger(__name__).warning(
            "[DEBUG] rollback_to_checkpoint: turn_id=%s checkpoint_id=%s restored_state=%s",
            self.turn_id,
            checkpoint_id,
            self._state.name,
        )
        return True

    def get_latest_checkpoint(self) -> dict[str, Any] | None:
        """Phase 3.5: Get the most recent checkpoint.

        Returns:
            Latest checkpoint dict or None if no checkpoints
        """
        if not self._checkpoints:
            return None
        return dict(self._checkpoints[-1])

    def clear_checkpoints(self) -> int:
        """Phase 3.5: Clear all checkpoints.

        Returns:
            Number of checkpoints cleared
        """
        count = len(self._checkpoints)
        self._checkpoints.clear()
        return count

    # -------------------------------------------------------------------------
    # Serialization
    # -------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "turn_id": self.turn_id,
            "current_state": self._state.name,
            "is_terminal": self.is_terminal(),
            "is_failed": self.is_failed(),
            "is_paused": self._is_paused,
            "sub_state": self._sub_state,
            "duration_ms": self.get_duration_ms(),
            "history": [{"state": state.name, "timestamp": ts} for state, ts in self._history],
            "checkpoint_count": len(self._checkpoints),
        }

    def __repr__(self) -> str:
        return f"TurnStateMachine(turn_id={self.turn_id}, state={self._state.name})"


# =============================================================================
# Module Exports (Backward Compatibility)
# =============================================================================

# Re-export with public names (without leading underscore)
VALID_TRANSITIONS = _VALID_TRANSITIONS
FORBIDDEN_TRANSITIONS = _FORBIDDEN_TRANSITIONS
_TERMINAL_STATES = _TERMINAL_STATES

__all__ = [
    "FORBIDDEN_TRANSITIONS",
    "VALID_TRANSITIONS",
    "InvalidStateTransitionError",
    "TurnState",
    "TurnStateMachine",
]
