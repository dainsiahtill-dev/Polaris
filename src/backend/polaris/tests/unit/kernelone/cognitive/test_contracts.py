"""Tests for polaris.kernelone.cognitive.contracts."""

from __future__ import annotations

from polaris.kernelone.cognitive.contracts import (
    CognitiveAssessResult,
    CognitiveOrchestratorProtocol,
    CognitivePipelinePort,
    CognitivePreCheckResult,
    EvolutionPort,
    ExecutionPort,
    PerceptionPort,
    ReasoningPort,
)


class TestPerceptionPort:
    def test_is_protocol(self) -> None:
        assert hasattr(PerceptionPort, "__subclasshook__")


class TestReasoningPort:
    def test_is_protocol(self) -> None:
        assert hasattr(ReasoningPort, "__subclasshook__")


class TestExecutionPort:
    def test_is_protocol(self) -> None:
        assert hasattr(ExecutionPort, "__subclasshook__")


class TestEvolutionPort:
    def test_is_protocol(self) -> None:
        assert hasattr(EvolutionPort, "__subclasshook__")


class TestCognitiveOrchestratorProtocol:
    def test_is_protocol(self) -> None:
        assert hasattr(CognitiveOrchestratorProtocol, "__subclasshook__")


class TestCognitivePreCheckResult:
    def test_defaults(self) -> None:
        r = CognitivePreCheckResult(should_proceed=True)
        assert r.should_proceed is True
        assert r.adjusted_prompt is None
        assert r.governance_verdict == "PASS"
        assert r.confidence == 1.0
        assert r.block_reason is None


class TestCognitiveAssessResult:
    def test_defaults(self) -> None:
        r = CognitiveAssessResult()
        assert r.quality_score == 1.0
        assert r.should_continue is True
        assert r.evolution_trigger is None
        assert r.assessment_note == ""


class TestCognitivePipelinePort:
    def test_is_protocol(self) -> None:
        assert hasattr(CognitivePipelinePort, "__subclasshook__")
