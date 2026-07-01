"""W1.5c regression: request overhead (tools/template) is budget-accounted.

Window-sensitive tests live HERE (polaris/tests/unit/kernelone/), never in
cells/roles/kernel/tests/ whose conftest pins max_context_tokens=128000.
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.llm.contracts import ModelSpec
from polaris.kernelone.llm.engine._executor_base import (
    clamp_output_tokens_to_window,
    estimate_payload_overhead_tokens,
)
from polaris.kernelone.llm.engine.prompt_budget import TokenBudgetManager


def _spec(window: int = 16384) -> ModelSpec:
    return ModelSpec(
        provider_id="openai_compat-test",
        provider_type="openai_compat",
        model="qwen-test",
        max_context_tokens=window,
        max_output_tokens=8192,
    )


def _tools(n: int = 17, schema_chars: int = 900) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": f"tool_{i}",
                "description": "x" * (schema_chars // 3),
                "parameters": {
                    "type": "object",
                    "properties": {"arg": {"type": "string", "description": "y" * (schema_chars // 3)}},
                },
            },
        }
        for i in range(n)
    ]


class TestOverheadEstimator:
    def test_tools_dominate_overhead(self) -> None:
        cfg = {"tools": _tools()}
        overhead = estimate_payload_overhead_tokens(cfg, None)
        # 17 dense-JSON schemas must register as thousands of tokens, not zero.
        assert overhead > 3000

    def test_no_tools_minimal_overhead(self) -> None:
        overhead = estimate_payload_overhead_tokens({}, [{"role": "user", "content": "hi"}])
        assert overhead <= 64


class TestEnforceWithOverhead:
    def test_allowed_prompt_shrinks_by_overhead(self) -> None:
        manager = TokenBudgetManager()
        spec = _spec()
        base = manager.enforce("short", spec, requested_output_tokens=4096)
        loaded = manager.enforce("short", spec, requested_output_tokens=4096, overhead_tokens=4000)
        assert int(loaded.allowed_prompt_tokens) <= int(base.allowed_prompt_tokens) - 3500
        assert loaded.overhead_tokens == 4000

    def test_zero_overhead_preserves_legacy_contract(self) -> None:
        manager = TokenBudgetManager()
        spec = _spec()
        legacy = manager.enforce("hello world", spec, requested_output_tokens=4096)
        assert legacy.overhead_tokens == 0
        assert legacy.allowed

    def test_l102_live_numbers_now_compress(self) -> None:
        """Replicate the live failure: prompt ~12.4k true tokens + 4k output +
        ~2k unaccounted tools used to sail through; with overhead accounted the
        budget must force compression below the window."""
        manager = TokenBudgetManager()
        spec = _spec(16384)
        big_prompt = "x" * (12000 * 4)
        decision = manager.enforce(big_prompt, spec, requested_output_tokens=4096, overhead_tokens=2049)
        assert decision.allowed
        budgeted_total = decision.allowed_prompt_tokens + 4096 + 2049
        assert budgeted_total <= 16384


class TestClampWithOverhead:
    def test_clamp_includes_overhead(self) -> None:
        cfg: dict[str, Any] = {"max_tokens": 4096, "tools": _tools()}
        overhead = estimate_payload_overhead_tokens(cfg, None)
        clamp_output_tokens_to_window(cfg, _spec(), "中" * 11000, overhead_tokens=overhead)
        clamped = int(cfg["max_tokens"])
        assert clamped + 11000 + overhead + 64 <= 16384 + 256
