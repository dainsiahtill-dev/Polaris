"""Tests for polaris.cells.context.catalog.internal.evolution_engine.

Covers EvolutionEngine initialization, LLM availability check,
and evolution insights generation.
"""

from __future__ import annotations

import pytest
from polaris.cells.context.catalog.internal.evolution_engine import EvolutionEngine

# ---------------------------------------------------------------------------
# EvolutionEngine initialization
# ---------------------------------------------------------------------------


class TestEvolutionEngineInit:
    def test_init_sets_workspace(self) -> None:
        engine = EvolutionEngine("/workspace")
        assert engine.workspace == "/workspace"

    def test_llm_available_true_with_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        engine = EvolutionEngine("/workspace")
        assert engine._llm_available is True

    def test_llm_available_true_with_anthropic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        engine = EvolutionEngine("/workspace")
        assert engine._llm_available is True

    def test_llm_available_true_with_ollama(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
        engine = EvolutionEngine("/workspace")
        assert engine._llm_available is True

    def test_llm_available_false_when_no_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        engine = EvolutionEngine("/workspace")
        assert engine._llm_available is False


# ---------------------------------------------------------------------------
# get_evolution_insights
# ---------------------------------------------------------------------------


class TestGetEvolutionInsights:
    @pytest.mark.asyncio
    async def test_skipped_when_llm_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        engine = EvolutionEngine("/workspace")
        result = await engine.get_evolution_insights("test.cell", {})
        assert result["status"] == "skipped"
        assert "LLM infrastructure not configured" in result["reason"]

    @pytest.mark.asyncio
    async def test_returns_insights_when_llm_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        engine = EvolutionEngine("/workspace")
        result = await engine.get_evolution_insights("test.cell", {"summary": "test"})
        assert result["status"] == "active"
        assert result["cell_id"] == "test.cell"
        assert "strategy" in result
        assert "goals" in result
        assert "roadmap" in result
        assert isinstance(result["goals"], list)
        assert isinstance(result["roadmap"], list)

    @pytest.mark.asyncio
    async def test_insights_content_structure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        engine = EvolutionEngine("/workspace")
        result = await engine.get_evolution_insights("context.catalog", {})
        assert result["strategy"] == "Autonomous Evolution"
        assert any("interface" in g.lower() for g in result["goals"])
        assert any("Phase" in r for r in result["roadmap"])
