"""Tests for recovery state machine.

Tests the circuit breaker recovery flow:
    CIRCUIT_BREAKER_TRIGGERED → PAUSE_EXEC → RETRY_CHECK → (RESUME | ESCALATE)
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.recovery_state_machine import (
    CircuitBreakerContext,
    RecoveryState,
    RecoveryStateMachine,
)


class TestRecoveryStateTransitions:
    """Tests for recovery state machine transitions."""

    def test_initial_state_is_idle(self):
        """State machine starts in IDLE state."""
        sm = RecoveryStateMachine()
        assert sm.state == RecoveryState.IDLE

    def test_circuit_breaker_triggers_pause(self):
        """Circuit breaker triggers transition to PAUSE_EXEC."""
        sm = RecoveryStateMachine()
        context = CircuitBreakerContext(
            breaker_type="same_tool",
            tool_name="read_file",
            reason="重复执行3次",
        )
        sm.handle_circuit_breaker(context)
        assert sm.state == RecoveryState.PAUSE_EXEC
        assert sm.breaker_context == context

    def test_inject_prompt_transitions_to_retry_check(self):
        """Injecting recovery prompt transitions to RETRY_CHECK."""
        sm = RecoveryStateMachine()
        context = CircuitBreakerContext(
            breaker_type="same_tool",
            tool_name="read_file",
            reason="重复执行3次",
        )
        sm.handle_circuit_breaker(context)

        history = []
        sm.inject_recovery_prompt(history)

        assert sm.state == RecoveryState.RETRY_CHECK
        assert len(history) == 1
        assert history[0]["role"] == "system"
        assert "CIRCUIT BREAKER" in history[0]["content"]

    def test_successful_write_resumes(self):
        """Write operation after recovery attempt marks success."""
        sm = RecoveryStateMachine()
        context = CircuitBreakerContext(
            breaker_type="stagnation",
            tool_name="read_file",
            reason="探查阶段超时",
        )
        sm.handle_circuit_breaker(context)
        history = []
        sm.inject_recovery_prompt(history)

        # Simulate write tool execution
        tool_results = [{"tool": "write_file", "success": True}]
        success = sm.check_recovery_success(tool_results, "")

        assert success is True

    def test_final_answer_resumes(self):
        """Final answer without tools marks success."""
        sm = RecoveryStateMachine()
        context = CircuitBreakerContext(
            breaker_type="same_tool",
            tool_name="read_file",
            reason="重复执行",
        )
        sm.handle_circuit_breaker(context)
        history = []
        sm.inject_recovery_prompt(history)

        # Simulate final answer
        content = "任务完成。结论是..."
        tool_results = []
        success = sm.check_recovery_success(tool_results, content)

        assert success is True

    def test_max_attempts_escalates(self):
        """Exceeded max recovery attempts escalates."""
        sm = RecoveryStateMachine()
        sm.max_recovery_attempts = 2
        context = CircuitBreakerContext(
            breaker_type="same_tool",
            tool_name="read_file",
            reason="重复执行",
        )
        sm.handle_circuit_breaker(context)
        history = []
        sm.inject_recovery_prompt(history)

        # 3 failed checks (exceeds max of 2)
        for _ in range(3):
            sm.check_recovery_success([{"tool": "read_file"}], "继续探查")

        assert sm.state == RecoveryState.ESCALATE

    def test_read_only_streak_escalates(self):
        """Read-only streak after recovery escalates."""
        sm = RecoveryStateMachine()
        context = CircuitBreakerContext(
            breaker_type="stagnation",
            tool_name="read_file",
            reason="探查阶段超时",
        )
        sm.handle_circuit_breaker(context)
        history = []
        sm.inject_recovery_prompt(history)

        # 2 consecutive read-only after recovery attempt
        sm.check_recovery_success([{"tool": "read_file"}], "")
        sm.check_recovery_success([{"tool": "repo_tree"}], "继续探查")

        assert sm.state == RecoveryState.ESCALATE

    def test_reset_returns_to_idle(self):
        """Reset returns state machine to IDLE."""
        sm = RecoveryStateMachine()
        context = CircuitBreakerContext(
            breaker_type="same_tool",
            tool_name="read_file",
            reason="重复执行",
        )
        sm.handle_circuit_breaker(context)

        sm.reset()

        assert sm.state == RecoveryState.IDLE
        assert sm.breaker_context is None
        assert sm.recovery_attempts == 0


class TestRecoveryPromptContent:
    """Tests for recovery prompt generation."""

    def test_same_tool_prompt(self):
        """Same-tool breaker generates correct prompt."""
        context = CircuitBreakerContext(
            breaker_type="same_tool",
            tool_name="read_file",
            reason="重复执行3次",
            recovery_hint="请检查工具结果是否已解析",
        )
        prompt = context.to_prompt_text()

        assert "CIRCUIT BREAKER TRIGGERED" in prompt
        assert "read_file" in prompt
        assert "重复执行" in prompt
        assert "禁止再次调用" in prompt

    def test_stagnation_prompt(self):
        """Stagnation breaker generates correct prompt."""
        context = CircuitBreakerContext(
            breaker_type="stagnation",
            tool_name="read_file",
            reason="连续5次读取无写入",
            recovery_hint="请立即执行写入",
        )
        prompt = context.to_prompt_text()

        assert "探查阶段超时" in prompt
        assert "强制恢复程序" in prompt

    def test_cross_tool_prompt(self):
        """Cross-tool breaker generates correct prompt."""
        context = CircuitBreakerContext(
            breaker_type="cross_tool",
            tool_name="repo_tree",
            reason="ABAB循环模式",
        )
        prompt = context.to_prompt_text()

        assert "跨工具循环" in prompt or "检测到" in prompt


class TestRecoveryStatus:
    """Tests for recovery status reporting."""

    def test_status_includes_all_fields(self):
        """Status report includes all relevant fields."""
        sm = RecoveryStateMachine()
        context = CircuitBreakerContext(
            breaker_type="same_tool",
            tool_name="read_file",
            reason="重复执行",
        )
        sm.handle_circuit_breaker(context)

        status = sm.get_status()

        assert status["state"] == "PAUSE_EXEC"
        assert status["breaker_type"] == "same_tool"
        assert status["breaker_tool"] == "read_file"
        assert status["recovery_attempts"] == 0
        assert status["max_recovery_attempts"] == 3
