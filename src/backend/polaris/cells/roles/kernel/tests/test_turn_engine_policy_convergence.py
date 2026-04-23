"""Tests for TurnEngine PolicyLayer convergence.

Covers the integration contract between TurnEngine and PolicyLayer, with
emphasis on the stall-detection semantics introduced in Phase 7.

Test structure
──────────────
1. Unit tests — PolicyLayer / BudgetPolicy stall semantics in isolation
2. Integration tests — TurnEngine (both run() and run_stream()) with
   PolicyLayer driven by environment-variable-controlled budgets

Key semantics being verified
────────────────────────────
- precheck_stall() increments _stall_count BEFORE budget.evaluate() sees it
  (pre-increment pattern, matching ToolLoopController.register_cycle())
- stall_count > max_stall_cycles → STOP  (strict >, NOT >=)
  e.g. max_stall_cycles=0: stall_count=0→ALLOW, stall_count=1→BLOCK
- BudgetPolicy returns stop_reason for all budget exhaustion conditions
- TurnEngine run()/run_stream() terminates when policy_result.stop_reason is set
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.policy.layer import (
    BudgetPolicy,
    CanonicalToolCall,
    PolicyLayer,
    PolicyResult,
)
from polaris.cells.roles.profile.public.service import RoleExecutionMode, RoleTurnRequest


def _native_tool_call(
    tool: str,
    arguments: dict[str, object],
    *,
    call_id: str = "call_read_file",
) -> dict[str, object]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": tool,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helper: mock sub-policies with correct return signatures
# ─────────────────────────────────────────────────────────────────────────────


def _tool_policy_pass() -> SimpleNamespace:
    """Return a mock tool_policy that approves all calls (3-tuple)."""

    def evaluate(calls):
        return list(calls), [], []

    return SimpleNamespace(evaluate=evaluate)


def _approval_policy_pass() -> SimpleNamespace:
    """Return a mock approval_policy that auto-approves all calls (3-tuple)."""

    def evaluate(calls):
        return list(calls), [], []

    return SimpleNamespace(evaluate=evaluate, clear_pending=lambda: None)


def _sandbox_policy_pass() -> SimpleNamespace:
    """Return a mock sandbox_policy that approves all calls (3-tuple)."""

    def evaluate(calls):
        return list(calls), [], []

    return SimpleNamespace(evaluate=evaluate)


def _redaction_policy_pass() -> SimpleNamespace:
    """Return a mock redaction_policy that passes through unchanged."""
    return SimpleNamespace(
        redact=lambda x: x,
        redact_dict=dict,
        redact_tool_result=dict,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


class _StubRegistry:
    def __init__(self, profile: object) -> None:
        self._profile = profile

    def get_profile_or_raise(self, _role: str) -> object:
        return self._profile


def _build_kernel(
    tool_policy_overrides: dict | None = None,
    *,
    prompt_builder: Any | None = None,
    llm_invoker: Any | None = None,
) -> RoleExecutionKernel:
    """Build a kernel with minimal tool_policy.

    tool_policy_overrides allows tests to pass only the fields they need;
    missing fields use PolicyLayer.getattr()-based defaults.

    Args:
        tool_policy_overrides: 工具策略覆盖
        prompt_builder: 可选的 prompt_builder 用于依赖注入
        llm_invoker: 可选的 llm_invoker 用于依赖注入
    """
    tool_policy_defaults = {
        "policy_id": "pm-policy-v1",
        "whitelist": ["read_file"],
        "blacklist": [],
        "allow_code_write": False,
        "allow_command_execution": False,
        "allow_file_delete": False,
        "max_tool_calls_per_turn": 50,
    }
    if tool_policy_overrides:
        tool_policy_defaults.update(tool_policy_overrides)

    # FIX: Add context_policy for RoleContextGateway compatibility
    context_policy = SimpleNamespace(
        max_context_tokens=100000,
        max_history_turns=20,
        compression_strategy="none",
        include_project_structure=False,
        include_task_history=False,
    )

    profile = SimpleNamespace(
        role_id="pm",
        model="gpt-4o-mini",
        provider_id="openai",
        version="1.0.0",
        tool_policy=SimpleNamespace(**tool_policy_defaults),
        context_policy=context_policy,
    )
    # FIX: 支持依赖注入 mock 服务
    return RoleExecutionKernel(
        workspace=".",
        registry=_StubRegistry(profile),  # type: ignore[arg-type]
        prompt_builder=prompt_builder,
        llm_invoker=llm_invoker,
    )


def _canonical(
    tool: str,
    args: dict | None = None,
    call_id: str = "",
) -> CanonicalToolCall:
    return CanonicalToolCall(tool=tool, args=args or {}, call_id=call_id, raw_content="")


# ─────────────────────────────────────────────────────────────────────────────
# Part 1 — Unit tests: BudgetPolicy stall semantics
# ─────────────────────────────────────────────────────────────────────────────


class TestBudgetPolicyStallSemantics:
    """Verify BudgetPolicy.evaluate() stall-check uses strict '>' (not '>=').

    Key invariant:
        stall_count == 0           → ALLOW  (first identical cycle)
        stall_count == 1, max=0   → BLOCK  (1 > 0)
        stall_count == 2, max=1   → BLOCK  (2 > 1)
    """

    def test_stall_check_is_strict_greater_than(self) -> None:
        """stall_count=1 with max_stall_cycles=0 must be blocked."""
        policy = BudgetPolicy(max_tool_calls=999, max_turns=999, max_stall_cycles=0)
        calls = [_canonical("read_file", {"path": "a.py"})]

        approved, blocked, stop_reason, _violations = policy.evaluate(
            calls,
            tool_call_count=1,
            turn_count=1,
            stall_count=1,
        )

        assert stop_reason is not None, "stall_count=1 > max=0 → must stop"
        assert "stalled" in stop_reason
        assert len(approved) == 0
        assert len(blocked) == 1

    def test_first_identical_cycle_allowed_when_max_zero(self) -> None:
        """stall_count=0 with max_stall_cycles=0 must be allowed (first identical)."""
        policy = BudgetPolicy(max_tool_calls=999, max_turns=999, max_stall_cycles=0)
        calls = [_canonical("read_file", {"path": "a.py"})]

        approved, blocked, stop_reason, _violations = policy.evaluate(
            calls,
            tool_call_count=1,
            turn_count=1,
            stall_count=0,
        )

        assert stop_reason is None, "stall_count=0 is not > max=0 → must allow"
        assert len(approved) == 1
        assert len(blocked) == 0

    def test_stall_resets_when_signature_differs(self) -> None:
        """Different tool+args produce different cycle signatures → stall resets."""
        policy = BudgetPolicy(max_tool_calls=999, max_turns=999, max_stall_cycles=0)
        calls_a = [_canonical("read_file", {"path": "a.py"})]
        calls_b = [_canonical("read_file", {"path": "b.py"})]

        # First identical call: allowed
        _, _, stop, _ = policy.evaluate(calls_a, tool_call_count=1, turn_count=1, stall_count=0)
        assert stop is None

        # Different call (different args): allowed — stall_count would reset to 0
        _, _, stop, _ = policy.evaluate(calls_b, tool_call_count=2, turn_count=2, stall_count=0)
        assert stop is None

    def test_second_identical_cycle_with_max_one_is_blocked(self) -> None:
        """stall_count=2 > max_stall_cycles=1 must be blocked."""
        policy = BudgetPolicy(max_tool_calls=999, max_turns=999, max_stall_cycles=1)
        calls = [_canonical("read_file", {"path": "a.py"})]

        _approved, _blocked, stop_reason, _violations = policy.evaluate(
            calls,
            tool_call_count=2,
            turn_count=2,
            stall_count=2,
        )

        assert stop_reason is not None
        assert "stalled" in stop_reason

    def test_max_tool_calls_exceeded_returns_stop_reason(self) -> None:
        """When tool_call_count >= max_tool_calls, stop_reason must be set."""
        policy = BudgetPolicy(max_tool_calls=3, max_turns=999, max_stall_cycles=99)
        calls = [_canonical("read_file")]

        _, _, stop_reason, _ = policy.evaluate(
            calls,
            tool_call_count=3,  # equal to max
            turn_count=1,
            stall_count=0,
        )

        assert stop_reason == "max_tool_calls_exceeded"

    def test_max_turns_exceeded_returns_stop_reason(self) -> None:
        """When turn_count >= max_turns, stop_reason must be set."""
        policy = BudgetPolicy(max_tool_calls=999, max_turns=2, max_stall_cycles=99)
        calls: list[CanonicalToolCall] = []

        _, _, stop_reason, _ = policy.evaluate(
            calls,
            tool_call_count=0,
            turn_count=2,  # equal to max
            stall_count=0,
        )

        assert stop_reason == "max_turns_exceeded"


# ─────────────────────────────────────────────────────────────────────────────
# Part 2 — Unit tests: PolicyLayer.precheck_stall()
# ─────────────────────────────────────────────────────────────────────────────


def _make_layer(
    max_tool_calls: int = 999,
    max_turns: int = 999,
    max_stall_cycles: int = 0,
) -> PolicyLayer:
    """Construct a PolicyLayer with all-default mock sub-policies."""
    return PolicyLayer(
        tool_policy=_tool_policy_pass(),  # type: ignore[arg-type]
        budget_policy=BudgetPolicy(
            max_tool_calls=max_tool_calls,
            max_turns=max_turns,
            max_stall_cycles=max_stall_cycles,
        ),
        approval_policy=_approval_policy_pass(),  # type: ignore[arg-type]
        sandbox_policy=_sandbox_policy_pass(),  # type: ignore[arg-type]
        redaction_policy=_redaction_policy_pass(),  # type: ignore[arg-type]
    )


class TestPolicyLayerPrecheckStall:
    """Verify PolicyLayer.precheck_stall() semantics.

    precheck_stall() MUST be called BEFORE budget.evaluate() and MUST
    update _stall_count so that budget.evaluate() sees the incremented value.
    """

    def test_precheck_stall_returns_incremented_count_on_identical_cycle(self) -> None:
        """Second identical cycle: precheck_stall() returns 1."""
        layer = _make_layer(max_stall_cycles=0)
        calls = [_canonical("read_file", {"path": "a.py"})]

        # First cycle — no prior signature → stall_count stays 0
        stall0 = layer.precheck_stall(calls)
        assert stall0 == 0

        # Second identical cycle — precheck increments to 1
        stall1 = layer.precheck_stall(calls)
        assert stall1 == 1

    def test_precheck_stall_resets_on_different_cycle(self) -> None:
        """Different tool args reset _stall_count to 0."""
        layer = _make_layer(max_stall_cycles=0)
        calls_a = [_canonical("read_file", {"path": "a.py"})]
        calls_b = [_canonical("read_file", {"path": "b.py"})]

        layer.precheck_stall(calls_a)  # stall_count = 0
        layer.precheck_stall(calls_a)  # stall_count = 1
        layer.precheck_stall(calls_b)  # different → stall_count = 0
        layer.precheck_stall(calls_b)  # identical to prev → stall_count = 1

        # With precheck_stall_count=1 (as returned above), stall is triggered
        result = layer.evaluate(calls_b, precheck_stall_count=1)
        assert result.stop_reason is not None
        assert "stalled" in result.stop_reason

    def test_precheck_stall_empty_calls_returns_current_count(self) -> None:
        """precheck_stall([]) returns current _stall_count without modification."""
        layer = _make_layer(max_stall_cycles=0)
        layer.precheck_stall([_canonical("read_file", {"path": "a.py"})])
        layer.precheck_stall([_canonical("read_file", {"path": "a.py"})])  # count = 1

        result_count = layer.precheck_stall([])

        assert result_count == 1
        assert layer._stall_count == 1

    def test_precheck_stall_updates_last_signature(self) -> None:
        """After precheck_stall, _last_cycle_signature is non-empty."""
        layer = _make_layer(max_stall_cycles=0)
        calls = [_canonical("read_file", {"path": "x.py"})]

        layer.precheck_stall(calls)

        assert layer._last_cycle_signature != ""


class TestPolicyLayerEvaluate:
    """Verify PolicyLayer.evaluate() combines sub-policies correctly."""

    def test_evaluate_empty_calls_returns_snapshot(self) -> None:
        """evaluate([]) returns a PolicyResult with no stop_reason (budget not exceeded)."""
        layer = _make_layer(max_tool_calls=999, max_turns=999, max_stall_cycles=99)
        result = layer.evaluate([], budget_state={"tool_call_count": 0, "turn_count": 0})
        assert result.stop_reason is None
        assert isinstance(result, PolicyResult)

    def test_evaluate_calls_passed_to_tool_policy(self) -> None:
        """Non-empty calls must be forwarded to tool_policy."""
        tool_calls = [_canonical("read_file", {"path": "a.py"})]
        call_log: list = []

        def capture_tool_policy_eval(calls: list) -> tuple:
            call_log.append(("tool_policy", len(calls)))
            return list(calls), [], []

        layer = PolicyLayer(
            tool_policy=SimpleNamespace(evaluate=capture_tool_policy_eval),  # type: ignore[arg-type]
            budget_policy=BudgetPolicy(max_tool_calls=999, max_turns=999, max_stall_cycles=99),
            approval_policy=_approval_policy_pass(),  # type: ignore[arg-type]
            sandbox_policy=_sandbox_policy_pass(),  # type: ignore[arg-type]
            redaction_policy=_redaction_policy_pass(),  # type: ignore[arg-type]
        )

        result = layer.evaluate(tool_calls, budget_state={"tool_call_count": 0, "turn_count": 0})

        assert ("tool_policy", 1) in call_log
        assert len(result.approved_calls) == 1
        assert result.stop_reason is None

    def test_evaluate_respects_precheck_stall_count(self) -> None:
        """precheck_stall_count=1 with max_stall_cycles=0 must trigger stop_reason."""
        layer = _make_layer(max_stall_cycles=0)
        calls = [_canonical("read_file", {"path": "a.py"})]

        result = layer.evaluate(
            calls,
            budget_state={"tool_call_count": 1, "turn_count": 1},
            precheck_stall_count=1,
        )

        assert result.stop_reason is not None
        assert "stalled" in result.stop_reason


class TestPolicyLayerReset:
    """Verify PolicyLayer.reset() clears accumulated state."""

    def test_reset_clears_stall_count(self) -> None:
        """After reset(), _stall_count must be 0 and signature must be empty."""
        layer = _make_layer(max_stall_cycles=0)
        layer.precheck_stall([_canonical("read_file", {"path": "a.py"})])
        layer.precheck_stall([_canonical("read_file", {"path": "a.py"})])
        assert layer._stall_count == 1

        layer.reset()

        assert layer._stall_count == 0
        assert layer._last_cycle_signature == ""


# ─────────────────────────────────────────────────────────────────────────────
# Part 3 — Integration tests: TurnEngine + PolicyLayer
# ─────────────────────────────────────────────────────────────────────────────


class TestTurnEnginePolicyIntegration:
    """End-to-end tests verifying TurnEngine respects PolicyLayer stop decisions."""

    @pytest.fixture(autouse=True)
    def _reset_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Reset PolicyLayer budget env vars before each test."""
        monkeypatch.delenv("KERNELONE_TOOL_LOOP_MAX_STALL_CYCLES", raising=False)
        monkeypatch.delenv("KERNELONE_TOOL_LOOP_MAX_TOTAL_CALLS", raising=False)

    def test_run_single_failed_tool_cycle_does_not_trigger_stall(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Single-turn kernel.run() under TransactionKernel: one tool call + LLM_ONCE finalization.

        New architecture has no multi-turn stall loop. A single failed tool call is
        followed by one finalization LLM call and completes normally.
        """
        llm_call_count = [0]

        mock_prompt_builder = SimpleNamespace(
            build_fingerprint=lambda _p, _a: SimpleNamespace(full_hash="fp-stall", core_hash="fp-stall"),
            build_system_prompt=lambda _p, _a: "system-prompt",
            build_retry_prompt=lambda _b, _q, _a: "system-prompt",
        )

        async def _fake_llm_call(*args: Any, **kwargs: Any) -> Any:
            llm_call_count[0] += 1
            if llm_call_count[0] == 1:
                # Decision phase: tool call
                return SimpleNamespace(
                    content="读取 missing.py。",
                    tool_calls=[_native_tool_call("read_file", {"path": "missing.py"})],
                    tool_call_provider="openai",
                    token_estimate=30,
                    error=None,
                    error_category=None,
                    metadata={},
                )
            # Finalization phase: plain answer
            return SimpleNamespace(
                content="文件不存在，无法读取。",
                tool_calls=[],
                tool_call_provider="auto",
                token_estimate=10,
                error=None,
                error_category=None,
                metadata={},
            )

        mock_llm_caller = SimpleNamespace(call=_fake_llm_call)
        kernel = _build_kernel(prompt_builder=mock_prompt_builder)
        kernel._injected_llm_caller = mock_llm_caller

        async def _fake_execute_single_tool(
            self: Any,
            tool_name: str,
            args: dict[str, Any],
            context: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            return {
                "success": False,
                "tool": tool_name,
                "error": "FileNotFound",
                "result": {"ok": False, "error": "FileNotFound"},
            }

        monkeypatch.setattr(RoleExecutionKernel, "_execute_single_tool", _fake_execute_single_tool)

        request = RoleTurnRequest(
            mode=RoleExecutionMode.CHAT,
            workspace=".",
            message="open missing",
            history=[],
            context_override={},
            validate_output=False,
        )

        result = asyncio.run(kernel.run("pm", request))

        assert result.error is None
        assert result.is_complete is True
        # 1 decision call + 1 finalization call
        assert llm_call_count[0] == 2
        assert len(result.tool_results) == 1
        assert result.tool_results[0]["success"] is False

    def test_run_stream_single_failed_tool_cycle_does_not_trigger_stall(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Single-turn run_stream() under TransactionKernel: one tool call + finalization.

        Stream path uses call_stream for decision and call() for LLM_ONCE finalization.
        """
        llm_call_count = [0]

        mock_prompt_builder = SimpleNamespace(
            build_fingerprint=lambda _p, _a: SimpleNamespace(full_hash="fp-strm-stall"),
            build_system_prompt=lambda _p, _a: "system-prompt",
        )

        async def _fake_call_stream(*args: Any, **kwargs: Any):
            yield {
                "type": "reasoning_chunk",
                "content": "用户要求打开 missing.py 文件。我需要使用 read_file 工具读取该文件。",
            }
            yield {
                "type": "tool_call",
                "tool": "read_file",
                "args": {"path": "missing.py"},
                "call_id": "call_missing",
                "metadata": {"provider_id": "openai"},
            }

        async def _fake_call(*args: Any, **kwargs: Any) -> Any:
            llm_call_count[0] += 1
            return SimpleNamespace(
                content="文件不存在，无法读取。",
                tool_calls=[],
                tool_call_provider="auto",
                token_estimate=10,
                error=None,
                error_category=None,
                metadata={},
            )

        mock_llm_caller = SimpleNamespace(call_stream=_fake_call_stream, call=_fake_call)
        kernel = _build_kernel(prompt_builder=mock_prompt_builder)
        kernel._injected_llm_caller = mock_llm_caller

        async def _fake_execute_single_tool(
            self: Any,
            tool_name: str,
            args: dict[str, Any],
            context: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            return {
                "success": False,
                "tool": tool_name,
                "error": "FileNotFound",
                "result": {"ok": False, "error": "FileNotFound"},
            }

        monkeypatch.setattr(RoleExecutionKernel, "_execute_single_tool", _fake_execute_single_tool)

        request = RoleTurnRequest(
            mode=RoleExecutionMode.CHAT,
            workspace=".",
            message="open missing",
            history=[],
            context_override={},
        )

        async def _collect() -> list[dict[str, Any]]:
            events = []
            async for ev in kernel.run_stream("pm", request):
                events.append(ev)
            return events

        events = asyncio.run(_collect())
        errors = [ev for ev in events if ev.get("type") == "error"]
        tool_results = [ev for ev in events if ev.get("type") == "tool_result"]

        assert not errors
        assert len(tool_results) == 1
        assert any(ev.get("type") == "complete" for ev in events)


# ─────────────────────────────────────────────────────────────────────────────
# Part 4 — Regression: precheck_stall + evaluate pattern correctness
# ─────────────────────────────────────────────────────────────────────────────


class TestPrecheckEvaluatePattern:
    """Regression tests for the precheck_stall() + evaluate() call pattern.

    The key invariant:
        count = precheck_stall(calls)
        evaluate(calls, precheck_stall_count=count)
        → budget.evaluate() sees the SAME count that precheck_stall() returned
    """

    def test_precheck_and_evaluate_agree_on_stall_count(self) -> None:
        """The count returned by precheck_stall() must trigger the block in evaluate()."""
        layer = _make_layer(max_stall_cycles=0)
        calls = [_canonical("read_file", {"path": "a.py"})]

        layer.precheck_stall(calls)  # 0
        returned_count = layer.precheck_stall(calls)  # 1

        result = layer.evaluate(
            calls,
            budget_state={"tool_call_count": 1, "turn_count": 1},
            precheck_stall_count=returned_count,
        )

        assert result.stop_reason is not None, "stall_count=1 > max=0 → must stop"
        assert "stalled" in result.stop_reason

    def test_no_precheck_uses_internal_count(self) -> None:
        """Without precheck_stall_count, PolicyLayer uses its own _stall_count.

        budget_state restores counts from a prior call. Here we simulate a mid-run
        state where stall_count has already been incremented to 1 (e.g. by a prior
        evaluate call). Passing budget_state with stall_count=1 reflects that.
        """
        layer = _make_layer(max_stall_cycles=0)
        calls = [_canonical("read_file", {"path": "a.py"})]

        # budget_state reflects "we are at turn 1, already detected 1 identical cycle"
        result = layer.evaluate(
            calls,
            budget_state={"tool_call_count": 1, "turn_count": 1, "stall_count": 1},
            precheck_stall_count=None,  # not passed → uses budget_state stall_count
        )

        assert result.stop_reason is not None, "stall_count=1 > max=0 → must stop"

    def test_different_signature_resets_stall_for_evaluate(self) -> None:
        """Different calls make PolicyLayer stall-check impossible even with max_stall_cycles=0."""
        layer = _make_layer(max_stall_cycles=0)
        calls_a = [_canonical("read_file", {"path": "a.py"})]
        calls_b = [_canonical("read_file", {"path": "b.py"})]

        # Build stall with a.py
        layer.precheck_stall(calls_a)
        layer.precheck_stall(calls_a)  # count = 1
        # Different call resets
        layer.precheck_stall(calls_b)  # count = 0

        result = layer.evaluate(
            calls_b,
            budget_state={"tool_call_count": 2, "turn_count": 2},
            precheck_stall_count=0,
        )

        assert result.stop_reason is None


# ─────────────────────────────────────────────────────────────────────────────
# Part 4 — ExplorationToolPolicy tests
# ─────────────────────────────────────────────────────────────────────────────


class TestExplorationToolPolicy:
    """Unit tests for ExplorationToolPolicy."""

    def test_exploration_tool_classification(self) -> None:
        """Test that tools are correctly classified as exploration tools."""
        from polaris.cells.roles.kernel.internal.policy.layer import ExplorationToolPolicy

        policy = ExplorationToolPolicy()

        # File search tools
        assert policy.is_exploration_tool("glob")
        assert policy.is_exploration_tool("list_directory")
        assert policy.is_exploration_tool("ls")

        # Content search tools
        assert policy.is_exploration_tool("ripgrep")
        assert policy.is_exploration_tool("grep")

        # File read tools
        assert policy.is_exploration_tool("read_file")

        # Non-exploration tools
        assert not policy.is_exploration_tool("write_file")
        assert not policy.is_exploration_tool("execute_command")

        # Check categories
        assert policy.get_tool_category("glob") == "file_search"
        assert policy.get_tool_category("ripgrep") == "content_search"
        assert policy.get_tool_category("read_file") == "file_read"
        assert policy.get_tool_category("write_file") is None

    def test_tool_cooldown_after_threshold(self) -> None:
        """Test that tools enter cooldown after exceeding cooldown threshold."""
        from polaris.cells.roles.kernel.internal.policy.layer import (
            CanonicalToolCall,
            ExplorationToolPolicy,
        )

        # Set max_duplicate_actions high enough so cooldown is the blocking mechanism
        policy = ExplorationToolPolicy(cooldown_after_calls=3, max_duplicate_actions=10)

        calls = [CanonicalToolCall(tool="glob", args={"pattern": "*.py"})]

        # First 3 calls should be approved
        for i in range(3):
            approved, blocked, violations = policy.evaluate(calls)
            assert len(approved) == 1, f"Call {i + 1} should be approved"
            assert len(blocked) == 0, f"Call {i + 1} should not be blocked"

        # 4th call should be blocked (cooldown)
        approved, blocked, violations = policy.evaluate(calls)
        assert len(approved) == 0, "4th call should be blocked due to cooldown"
        assert len(blocked) == 1, "4th call should be blocked"
        assert "cooldown" in violations[0].reason.lower()

    def test_max_calls_per_tool(self) -> None:
        """Test that tools are blocked after exceeding max_calls_per_tool."""
        from polaris.cells.roles.kernel.internal.policy.layer import (
            CanonicalToolCall,
            ExplorationToolPolicy,
        )

        policy = ExplorationToolPolicy(max_calls_per_tool=2, cooldown_after_calls=10)

        calls = [CanonicalToolCall(tool="glob", args={"pattern": "*.py"})]

        # First 2 calls should be approved
        for i in range(2):
            approved, blocked, violations = policy.evaluate(calls)
            assert len(approved) == 1, f"Call {i + 1} should be approved"

        # 3rd call should be blocked (max limit)
        approved, _blocked, violations = policy.evaluate(calls)
        assert len(approved) == 0, "3rd call should be blocked"
        assert "limit exceeded" in violations[0].reason.lower()

    def test_exploration_budget_exhausted(self) -> None:
        """Test that exploration tools are blocked when exploration budget is exhausted."""
        from polaris.cells.roles.kernel.internal.policy.layer import (
            CanonicalToolCall,
            ExplorationToolPolicy,
        )

        policy = ExplorationToolPolicy(max_exploration_calls=2)

        calls = [CanonicalToolCall(tool="glob", args={"pattern": "*.py"})]

        # First 2 calls should be approved
        for i in range(2):
            approved, blocked, violations = policy.evaluate(calls)
            assert len(approved) == 1, f"Call {i + 1} should be approved"

        # 3rd call should be blocked (budget exhausted)
        approved, _blocked, violations = policy.evaluate(calls)
        assert len(approved) == 0, "3rd call should be blocked"
        assert "budget exhausted" in violations[0].reason.lower()

    def test_non_exploration_tools_pass_through(self) -> None:
        """Test that non-exploration tools bypass ExplorationToolPolicy."""
        from polaris.cells.roles.kernel.internal.policy.layer import (
            CanonicalToolCall,
            ExplorationToolPolicy,
        )

        policy = ExplorationToolPolicy(max_exploration_calls=0)

        calls = [CanonicalToolCall(tool="write_file", args={"path": "test.py"})]

        approved, blocked, _violations = policy.evaluate(calls)
        assert len(approved) == 1, "Non-exploration tools should pass through"
        assert len(blocked) == 0

    def test_stats_tracking(self) -> None:
        """Test that ExplorationToolPolicy tracks statistics correctly."""
        from polaris.cells.roles.kernel.internal.policy.layer import (
            CanonicalToolCall,
            ExplorationToolPolicy,
        )

        policy = ExplorationToolPolicy(
            max_exploration_calls=10,
            cooldown_after_calls=2,
        )

        # Call glob 2 times
        glob_calls = [CanonicalToolCall(tool="glob", args={"pattern": "*.py"})]
        policy.evaluate(glob_calls)
        policy.evaluate(glob_calls)

        # Call ripgrep 1 time
        rg_calls = [CanonicalToolCall(tool="ripgrep", args={"pattern": "TODO"})]
        policy.evaluate(rg_calls)

        stats = policy.get_stats()
        assert stats["total_exploration_calls"] == 3
        assert stats["tool_call_counts"]["glob"] == 2
        assert stats["tool_call_counts"]["ripgrep"] == 1
        assert stats["category_call_counts"]["file_search"] == 2
        assert stats["category_call_counts"]["content_search"] == 1
        assert "glob" in stats["tools_in_cooldown"]

    def test_reset_clears_state(self) -> None:
        """Test that reset() clears all tracking state."""
        from polaris.cells.roles.kernel.internal.policy.layer import (
            CanonicalToolCall,
            ExplorationToolPolicy,
        )

        policy = ExplorationToolPolicy(cooldown_after_calls=3)

        calls = [CanonicalToolCall(tool="glob", args={"pattern": "*.py"})]
        policy.evaluate(calls)
        policy.evaluate(calls)
        policy.evaluate(calls)  # Enters cooldown

        assert len(policy.get_stats()["tools_in_cooldown"]) == 1

        policy.reset()

        stats = policy.get_stats()
        assert stats["total_exploration_calls"] == 0
        assert stats["tool_call_counts"] == {}
        assert stats["tools_in_cooldown"] == []

    def test_different_tools_have_independent_cooldown(self) -> None:
        """Test that different tools have independent cooldown tracking."""
        from polaris.cells.roles.kernel.internal.policy.layer import (
            CanonicalToolCall,
            ExplorationToolPolicy,
        )

        policy = ExplorationToolPolicy(cooldown_after_calls=2)

        # Call glob 2 times (hits cooldown)
        glob_calls = [CanonicalToolCall(tool="glob", args={"pattern": "*.py"})]
        policy.evaluate(glob_calls)
        policy.evaluate(glob_calls)

        # Call ripgrep 1 time (should still be allowed)
        rg_calls = [CanonicalToolCall(tool="ripgrep", args={"pattern": "TODO"})]
        approved, _blocked, _ = policy.evaluate(rg_calls)
        assert len(approved) == 1, "ripgrep should not be affected by glob cooldown"


class TestExplorationToolPolicyIntegration:
    """Integration tests for ExplorationToolPolicy within PolicyLayer."""

    def test_policy_layer_integrates_exploration_policy(self) -> None:
        """Test that PolicyLayer correctly integrates ExplorationToolPolicy."""
        # Create a policy layer with ExplorationToolPolicy
        from polaris.cells.roles.kernel.internal.policy.layer import (
            ApprovalPolicy,
            BudgetPolicy,
            CanonicalToolCall,
            ExplorationToolPolicy,
            PolicyLayer,
            RedactionPolicy,
            SandboxPolicy,
            ToolPolicy,
        )

        tool_policy = ToolPolicy(whitelist=["glob", "read_file", "write_file"])
        budget_policy = BudgetPolicy(max_tool_calls=100)
        approval_policy = ApprovalPolicy()
        sandbox_policy = SandboxPolicy()
        redaction_policy = RedactionPolicy()
        exploration_policy = ExplorationToolPolicy(
            max_exploration_calls=2,
            cooldown_after_calls=1,
        )

        layer = PolicyLayer(
            tool_policy=tool_policy,
            budget_policy=budget_policy,
            approval_policy=approval_policy,
            sandbox_policy=sandbox_policy,
            redaction_policy=redaction_policy,
            exploration_policy=exploration_policy,
        )

        calls = [CanonicalToolCall(tool="glob", args={"pattern": "*.py"})]

        # First call should pass through all policies
        result = layer.evaluate(calls)
        assert len(result.approved_calls) == 1
        assert len(result.blocked_calls) == 0
        assert result.exploration_stats["total_exploration_calls"] == 1

        # Second call should be blocked by ExplorationToolPolicy
        result = layer.evaluate(calls)
        assert len(result.approved_calls) == 0
        assert len(result.blocked_calls) == 1
        assert any(v.policy == "ExplorationToolPolicy" for v in result.violations)
