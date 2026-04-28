"""Tests for Akashic Memory Integration (ContextOS 3.0 P1)."""

import pytest

from polaris.kernelone.context.context_os.memory.candidates import (
    MemoryCandidate,
    MemoryCandidateProvider,
    MemoryFreshness,
)
from polaris.kernelone.context.context_os.memory.conflict_checker import (
    ConflictChecker,
    ConflictResult,
    ConflictStatus,
)
from polaris.kernelone.context.context_os.memory.manager import (
    MemoryManager,
    MemoryProjection,
)


class TestMemoryFreshness:
    """Test MemoryFreshness enum."""

    def test_enum_values(self) -> None:
        assert MemoryFreshness.CURRENT.value == "current"
        assert MemoryFreshness.RECENT.value == "recent"
        assert MemoryFreshness.STALE.value == "stale"
        assert MemoryFreshness.UNKNOWN.value == "unknown"


class TestMemoryCandidate:
    """Test MemoryCandidate dataclass."""

    def test_create_candidate(self) -> None:
        candidate = MemoryCandidate(
            memory_id="mem_001",
            content="Implemented feature X",
            source_session_id="session_001",
            freshness=MemoryFreshness.CURRENT,
            relevance_score=0.8,
        )
        assert candidate.memory_id == "mem_001"
        assert candidate.freshness == MemoryFreshness.CURRENT
        assert candidate.relevance_score == 0.8

    def test_to_dict(self) -> None:
        candidate = MemoryCandidate(
            memory_id="mem_001",
            content="Implemented feature X",
            source_session_id="session_001",
        )
        d = candidate.to_dict()
        assert d["memory_id"] == "mem_001"
        assert d["freshness"] == "unknown"


class TestMemoryCandidateProvider:
    """Test MemoryCandidateProvider class."""

    def test_create_provider(self) -> None:
        provider = MemoryCandidateProvider(workspace=".")
        assert provider.workspace == "."

    def test_recall_empty(self) -> None:
        provider = MemoryCandidateProvider()
        candidates = provider.recall(query="test")
        assert len(candidates) == 0

    def test_calculate_relevance(self) -> None:
        provider = MemoryCandidateProvider()
        relevance = provider._calculate_relevance("implement feature X", "implement feature X")
        assert relevance > 0.5

    def test_calculate_relevance_empty(self) -> None:
        provider = MemoryCandidateProvider()
        relevance = provider._calculate_relevance("", "test")
        assert relevance == 0.0

    def test_determine_freshness(self) -> None:
        provider = MemoryCandidateProvider()
        freshness = provider._determine_freshness("")
        assert freshness == MemoryFreshness.UNKNOWN


class TestConflictStatus:
    """Test ConflictStatus enum."""

    def test_enum_values(self) -> None:
        assert ConflictStatus.NONE.value == "none"
        assert ConflictStatus.POSSIBLE.value == "possible"
        assert ConflictStatus.CONFIRMED.value == "confirmed"


class TestConflictResult:
    """Test ConflictResult dataclass."""

    def test_create_result(self) -> None:
        result = ConflictResult(
            status=ConflictStatus.NONE,
            reason="No conflict",
        )
        assert result.status == ConflictStatus.NONE

    def test_to_dict(self) -> None:
        result = ConflictResult(
            status=ConflictStatus.CONFIRMED,
            reason="Direct contradiction",
        )
        d = result.to_dict()
        assert d["status"] == "confirmed"


class TestConflictChecker:
    """Test ConflictChecker class."""

    def test_create_checker(self) -> None:
        checker = ConflictChecker()
        assert len(checker.CONTRADICTION_PATTERNS) > 0

    def test_check_no_conflict(self) -> None:
        checker = ConflictChecker()
        result = checker.check(
            memory_content="Implemented feature X",
            current_facts=["goal: implement feature X"],
        )
        assert result.status == ConflictStatus.NONE

    def test_check_contradiction(self) -> None:
        checker = ConflictChecker()
        result = checker.check(
            memory_content="Feature X is not working",
            current_facts=["feature X is working correctly"],
        )
        assert result.status == ConflictStatus.CONFIRMED

    def test_check_status_conflict(self) -> None:
        checker = ConflictChecker()
        result = checker.check(
            memory_content="Task is completed",
            current_facts=["Task is in_progress"],
        )
        assert result.status == ConflictStatus.POSSIBLE

    def test_check_empty(self) -> None:
        checker = ConflictChecker()
        result = checker.check(
            memory_content="",
            current_facts=[],
        )
        assert result.status == ConflictStatus.NONE


class TestMemoryManager:
    """Test MemoryManager class."""

    def test_create_manager(self) -> None:
        manager = MemoryManager()
        assert manager.workspace == "."

    def test_process_empty(self) -> None:
        manager = MemoryManager()
        projections = manager.process(
            query="test",
            current_facts=["fact1"],
        )
        assert len(projections) == 0


class TestMemoryProjection:
    """Test MemoryProjection dataclass."""

    def test_create_projection(self) -> None:
        memory = MemoryCandidate(
            memory_id="mem_001",
            content="test",
            source_session_id="session_001",
        )
        conflict = ConflictResult(
            status=ConflictStatus.NONE,
            reason="No conflict",
        )
        projection = MemoryProjection(
            memory=memory,
            conflict_result=conflict,
            injection_allowed=True,
            injection_reason="Valid memory",
        )
        assert projection.injection_allowed is True

    def test_to_dict(self) -> None:
        memory = MemoryCandidate(
            memory_id="mem_001",
            content="test",
            source_session_id="session_001",
        )
        conflict = ConflictResult(
            status=ConflictStatus.NONE,
            reason="No conflict",
        )
        projection = MemoryProjection(
            memory=memory,
            conflict_result=conflict,
            injection_allowed=True,
            injection_reason="Valid memory",
        )
        d = projection.to_dict()
        assert d["injection_allowed"] is True
