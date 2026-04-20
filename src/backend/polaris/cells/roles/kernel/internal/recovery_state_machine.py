"""Recovery State Machine for Circuit Breaker handling.

Implements the recovery flow after a circuit breaker is triggered:
    CIRCUIT_BREAKER_TRIGGERED
            │
            ▼
    ┌───────────────┐
    │  PAUSE_EXEC   │ ──► Inject recovery prompt
    │  (1 turn)     │
    └───────────────┘
            │
            ▼
    ┌───────────────┐
    │  RETRY_CHECK  │ ──► Did model self-correct?
    │               │
    └───────────────┘
            │
        ┌───┴───┐
        ▼       ▼
    RESUME  ESCALATE
    (continue)  (human review)

Usage:
    from polaris.cells.roles.kernel.internal.recovery_state_machine import (
        RecoveryStateMachine,
        RecoveryState,
        CircuitBreakerContext,
    )

    sm = RecoveryStateMachine()
    context = CircuitBreakerContext(
        breaker_type="same_tool",
        tool_name="read_file",
        reason="重复执行3次",
    )
    sm.handle_circuit_breaker(context)

    # In next turn
    if sm.state == RecoveryState.PAUSE_EXEC:
        sm.inject_recovery_prompt(transcript)
    elif sm.state == RecoveryState.RETRY_CHECK:
        if sm.check_recovery_success(tool_results):
            sm.transition_to(RecoveryState.RESUME)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class RecoveryState(Enum):
    """States in the recovery state machine."""

    IDLE = auto()  # Normal operation
    CIRCUIT_BREAKER_TRIGGERED = auto()  # Breaker just triggered
    PAUSE_EXEC = auto()  # Paused for recovery prompt injection
    RETRY_CHECK = auto()  # Checking if model self-corrected
    RESUME = auto()  # Successfully recovered
    ESCALATE = auto()  # Failed to recover, needs human review


@dataclass(frozen=True)
class CircuitBreakerContext:
    """Context information about a circuit breaker trigger."""

    breaker_type: str  # same_tool | cross_tool | stagnation | thinking
    tool_name: str
    reason: str
    recovery_hint: str = ""
    timestamp: float = field(default_factory=lambda: __import__("time").time())

    def to_prompt_text(self) -> str:
        """Generate recovery prompt text from context."""
        type_descriptions = {
            "same_tool": f"工具'{self.tool_name}'被重复调用",
            "cross_tool": "检测到跨工具循环模式",
            "stagnation": "探查阶段超时（连续多次读取无写入）",
            "thinking": "输出格式违规（缺少<thinking>标签）",
        }
        desc = type_descriptions.get(self.breaker_type, "未知原因")

        base_prompt = f"""[SYSTEM CIRCUIT BREAKER TRIGGERED]
原因: {desc}
详情: {self.reason}

强制恢复程序：
1. 停止当前探查行为
2. 回顾已有信息（见上文）
3. 选择以下之一执行：
   A) 如果有足够信息：执行写入操作完成当前任务
   B) 如果信息不足：明确说明缺失什么，而不是继续探查
   C) 如果任务已完成：直接给出最终答案

{self.recovery_hint}

禁止再次调用 '{self.tool_name}'，直到明确进展被确认。
"""
        return base_prompt


@dataclass
class RecoveryStateMachine:
    """State machine for handling circuit breaker recovery.

    Tracks recovery progress and determines when to resume normal operation
    or escalate to human review.
    """

    state: RecoveryState = field(default=RecoveryState.IDLE)
    breaker_context: CircuitBreakerContext | None = field(default=None)
    pause_start_time: float = field(default=0.0)
    recovery_attempts: int = field(default=0)
    max_recovery_attempts: int = 3

    # Success criteria tracking
    _last_tool_was_read_only: bool = field(default=False)
    _consecutive_read_only_after_recovery: int = field(default=0)
    _workspace_modified_after_recovery: bool = field(default=False)

    def handle_circuit_breaker(self, context: CircuitBreakerContext) -> None:
        """Handle circuit breaker trigger.

        Args:
            context: Context information about the breaker trigger
        """
        logger.warning(f"[RECOVERY] Circuit breaker triggered: {context.breaker_type} (tool={context.tool_name})")
        self.breaker_context = context
        self.state = RecoveryState.CIRCUIT_BREAKER_TRIGGERED
        self.pause_start_time = __import__("time").time()
        self.recovery_attempts = 0
        self._consecutive_read_only_after_recovery = 0
        self._workspace_modified_after_recovery = False

        # Immediate transition to PAUSE_EXEC
        self.transition_to(RecoveryState.PAUSE_EXEC)

    def transition_to(self, new_state: RecoveryState) -> None:
        """Transition to a new state with logging."""
        old_state = self.state
        self.state = new_state
        logger.info(f"[RECOVERY] State transition: {old_state.name} -> {new_state.name}")

    def inject_recovery_prompt(self, history: list[dict[str, Any]]) -> None:
        """Inject recovery prompt into transcript history.

        Args:
            history: Transcript history to inject into
        """
        if self.state != RecoveryState.PAUSE_EXEC or not self.breaker_context:
            return

        prompt = self.breaker_context.to_prompt_text()
        history.append(
            {
                "role": "system",
                "content": prompt,
                "metadata": {
                    "source": "circuit_breaker_recovery",
                    "breaker_type": self.breaker_context.breaker_type,
                },
            }
        )

        logger.info("[RECOVERY] Injected recovery prompt into transcript")
        self.transition_to(RecoveryState.RETRY_CHECK)

    def check_recovery_success(
        self,
        tool_results: list[dict[str, Any]],
        assistant_content: str = "",
    ) -> bool:
        """Check if model has successfully recovered.

        Recovery is successful if:
        1. Model produced a write operation (file modification)
        2. Model provided a final answer (no tool calls)
        3. Model explicitly acknowledged the circuit breaker

        Args:
            tool_results: Recent tool execution results
            assistant_content: Assistant's response content

        Returns:
            True if recovery is successful
        """
        if self.state != RecoveryState.RETRY_CHECK:
            return False

        self.recovery_attempts += 1

        # Check for write operations
        write_tools = {
            "write_file",
            "append_to_file",
            "edit_file",
            "precision_edit",
            "delete_file",
            "move_file",
            "create_directory",
            "execute_command",
        }

        has_write = any(str(r.get("tool", "")).strip() in write_tools for r in tool_results if isinstance(r, dict))

        if has_write:
            self._workspace_modified_after_recovery = True
            logger.info("[RECOVERY] Success: Write operation detected")
            return True

        # Check for final answer (no tool calls in content, or explicit completion)
        completion_markers = {
            "任务完成",
            "已完成",
            "final answer",
            "结论",
            "总结",
            "答案",
        }
        has_completion_marker = any(m in assistant_content.lower() for m in completion_markers)

        if has_completion_marker and not tool_results:
            logger.info("[RECOVERY] Success: Final answer provided")
            return True

        # Check for acknowledgment
        acknowledgment_markers = {
            "理解",
            "收到",
            "明白",
            "acknowledged",
            "understood",
        }
        has_ack = any(m in assistant_content.lower() for m in acknowledgment_markers)

        # Track read-only streak after recovery attempt
        all_read_only = all(
            str(r.get("tool", "")).strip() not in write_tools for r in tool_results if isinstance(r, dict)
        )

        if all_read_only and tool_results:
            self._consecutive_read_only_after_recovery += 1
        else:
            self._consecutive_read_only_after_recovery = 0

        # Fail if too many recovery attempts or read-only streak
        if self.recovery_attempts >= self.max_recovery_attempts:
            logger.warning(f"[RECOVERY] Failed: Max recovery attempts ({self.max_recovery_attempts})")
            self.transition_to(RecoveryState.ESCALATE)
            return False

        if self._consecutive_read_only_after_recovery >= 2:
            logger.warning("[RECOVERY] Failed: Read-only streak after recovery attempt")
            self.transition_to(RecoveryState.ESCALATE)
            return False

        if has_ack and not all_read_only:
            logger.info("[RECOVERY] Success: Acknowledgment with action")
            return True

        return False

    def mark_resumed(self) -> None:
        """Mark recovery as successfully completed."""
        if self.state == RecoveryState.RETRY_CHECK:
            elapsed = __import__("time").time() - self.pause_start_time
            logger.info(f"[RECOVERY] Successfully resumed after {elapsed:.1f}s")
            self.transition_to(RecoveryState.RESUME)

    def mark_escalated(self, reason: str) -> None:
        """Mark recovery as failed and escalated.

        Args:
            reason: Reason for escalation
        """
        logger.error(f"[RECOVERY] Escalated: {reason}")
        self.transition_to(RecoveryState.ESCALATE)

    def reset(self) -> None:
        """Reset state machine to IDLE."""
        if self.state not in (RecoveryState.IDLE, RecoveryState.RESUME):
            logger.info("[RECOVERY] Resetting state machine to IDLE")
        self.state = RecoveryState.IDLE
        self.breaker_context = None
        self.pause_start_time = 0.0
        self.recovery_attempts = 0
        self._consecutive_read_only_after_recovery = 0
        self._workspace_modified_after_recovery = False

    def get_status(self) -> dict[str, Any]:
        """Get current recovery status for debugging/monitoring."""
        return {
            "state": self.state.name,
            "breaker_type": self.breaker_context.breaker_type if self.breaker_context else None,
            "breaker_tool": self.breaker_context.tool_name if self.breaker_context else None,
            "recovery_attempts": self.recovery_attempts,
            "max_recovery_attempts": self.max_recovery_attempts,
            "consecutive_read_only": self._consecutive_read_only_after_recovery,
            "workspace_modified": self._workspace_modified_after_recovery,
        }


# Global recovery state machine instance (per-turn lifecycle)
_recovery_sm: RecoveryStateMachine | None = None


def get_recovery_state_machine() -> RecoveryStateMachine:
    """Get the global recovery state machine instance."""
    global _recovery_sm
    if _recovery_sm is None:
        _recovery_sm = RecoveryStateMachine()
    return _recovery_sm


def reset_recovery_state_machine() -> None:
    """Reset the global recovery state machine."""
    global _recovery_sm
    _recovery_sm = None


__all__ = [
    "CircuitBreakerContext",
    "RecoveryState",
    "RecoveryStateMachine",
    "get_recovery_state_machine",
    "reset_recovery_state_machine",
]
