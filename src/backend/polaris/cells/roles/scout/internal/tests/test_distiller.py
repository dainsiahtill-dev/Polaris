"""Tests for DeterministicDistiller and LLMDistiller (UTF-8)."""

import pytest
from polaris.cells.roles.scout.internal.distiller import DeterministicDistiller, LLMDistiller
from polaris.cells.roles.scout.public.contracts import ScoutFinding


@pytest.mark.asyncio
async def test_deterministic_distiller_lists_findings_within_budget() -> None:
    findings = [ScoutFinding(path="a.py", line=1, symbol="pay", snippet="def pay():", confidence=0.9)]
    out = await DeterministicDistiller().distill(query="pay", findings=findings, token_budget=200)
    assert "a.py:1" in out
    assert "pay" in out


@pytest.mark.asyncio
async def test_deterministic_distiller_respects_char_budget() -> None:
    findings = [ScoutFinding(path=f"f{i}.py", line=i, snippet="x" * 50, confidence=0.1) for i in range(50)]
    out = await DeterministicDistiller().distill(query="x", findings=findings, token_budget=20)
    assert len(out) <= 20 * 4  # ~4 chars/token


@pytest.mark.asyncio
async def test_llm_distiller_uses_injected_caller_and_falls_back_on_error() -> None:
    async def good_caller(prompt: str) -> str:
        return "LLM SUMMARY"

    async def bad_caller(prompt: str) -> str:
        raise RuntimeError("provider down")

    findings = [ScoutFinding(path="a.py", line=1, snippet="def pay()", confidence=0.9)]
    ok = await LLMDistiller(call=good_caller).distill(query="pay", findings=findings, token_budget=100)
    assert ok == "LLM SUMMARY"

    fb = await LLMDistiller(call=bad_caller).distill(query="pay", findings=findings, token_budget=100)
    assert "a.py:1" in fb  # falls back to deterministic
