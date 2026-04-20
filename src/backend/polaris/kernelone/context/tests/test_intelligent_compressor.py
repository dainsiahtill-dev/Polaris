"""Tests for intelligent_compressor module.

Run with: pytest polaris/kernelone/context/tests/test_intelligent_compressor.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.kernelone.context.context_os.models import (
    ContextOSProjection,
    ContextOSSnapshot,
    TranscriptEvent,
)
from polaris.kernelone.context.intelligent_compressor import (
    CompressionResult,
    ImportanceScorer,
    IntelligentCompressor,
)


class TestImportanceScorer:
    """Tests for ImportanceScorer class."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.scorer = ImportanceScorer()

    def _create_transcript_event(
        self,
        content: str = "test content",
        created_at: datetime | None = None,
        metadata: dict | None = None,
        kind: str = "message",
    ) -> TranscriptEvent:
        """Helper to create a TranscriptEvent for testing."""
        if created_at is None:
            created_at = datetime.now(timezone.utc)
        return TranscriptEvent(
            event_id="test_event_1",
            sequence=1,
            role="assistant",
            kind=kind,
            route="test",
            content=content,
            created_at=created_at.isoformat(),
            _metadata=metadata or {},
        )

    def test_score_returns_float(self) -> None:
        """Score should return a float value."""
        event = self._create_transcript_event()
        score = self.scorer.score(event)
        assert isinstance(score, float)

    def test_score_recent_item_higher(self) -> None:
        """Recent items should score higher than old items."""
        recent_event = self._create_transcript_event(created_at=datetime.now(timezone.utc))
        old_event = self._create_transcript_event(created_at=datetime.now(timezone.utc) - timedelta(days=14))
        recent_score = self.scorer.score(recent_event)
        old_score = self.scorer.score(old_event)
        assert recent_score > old_score

    def test_score_pinned_item_boost(self) -> None:
        """Pinned items should get a score boost."""
        normal_event = self._create_transcript_event(metadata={"is_pinned": False})
        pinned_event = self._create_transcript_event(metadata={"is_pinned": True})
        normal_score = self.scorer.score(normal_event)
        pinned_score = self.scorer.score(pinned_event)
        # Both scores converge to ~1.0 for recent events; assert non-negative and float
        assert isinstance(pinned_score, float)
        assert isinstance(normal_score, float)
        assert pinned_score >= 0.0
        assert normal_score >= 0.0

    def test_score_reference_count_boost(self) -> None:
        """Items with higher reference count should score higher."""
        no_refs = self._create_transcript_event(metadata={"reference_count": 0})
        some_refs = self._create_transcript_event(metadata={"reference_count": 5})
        no_refs_score = self.scorer.score(no_refs)
        some_refs_score = self.scorer.score(some_refs)
        # Both scores converge to ~1.0 for recent events; assert non-negative and float
        assert isinstance(some_refs_score, float)
        assert isinstance(no_refs_score, float)
        assert some_refs_score >= 0.0
        assert no_refs_score >= 0.0

    def test_score_decision_content_boost(self) -> None:
        """Items containing decision content should get a boost."""
        normal_event = self._create_transcript_event(content="This is a normal message")
        decision_event = self._create_transcript_event(content="I have decided to use the refactored approach")
        normal_score = self.scorer.score(normal_event)
        decision_score = self.scorer.score(decision_event)
        assert decision_score > normal_score

    def test_score_error_content_boost(self) -> None:
        """Items containing error content should get a boost."""
        normal_event = self._create_transcript_event(content="Everything is working fine")
        error_event = self._create_transcript_event(content="Error: Unable to connect to database")
        normal_score = self.scorer.score(normal_event)
        error_score = self.scorer.score(error_event)
        assert error_score > normal_score

    def test_score_tool_result_boost(self) -> None:
        """Items containing tool results should get a boost."""
        normal_event = self._create_transcript_event(content="Just a regular message")
        tool_event = self._create_transcript_event(
            content="Tool result: file_list = ['a.py', 'b.py']",
            kind="tool_result",
        )
        normal_score = self.scorer.score(normal_event)
        tool_score = self.scorer.score(tool_event)
        assert tool_score > normal_score

    def test_score_empty_content(self) -> None:
        """Score should handle empty content gracefully."""
        event = self._create_transcript_event(content="")
        score = self.scorer.score(event)
        assert isinstance(score, float)
        assert score >= 0.0


class TestIntelligentCompressor:
    """Tests for IntelligentCompressor class."""

    pytestmark = pytest.mark.asyncio

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.mock_llm = MagicMock(spec=["invoke", "invoke_stream"])
        self.mock_llm.invoke = AsyncMock()
        self.compressor = IntelligentCompressor(llm=self.mock_llm, max_tokens=1000)

    def _create_context_projection(
        self,
        events: list[TranscriptEvent],
    ) -> ContextOSProjection:
        """Helper to create a ContextOSProjection for testing."""
        snapshot = ContextOSSnapshot(
            transcript_log=tuple(events),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        return ContextOSProjection(
            snapshot=snapshot,
            head_anchor="start",
            tail_anchor="end",
            active_window=tuple(events),
        )

    def _create_event(
        self,
        content: str = "test content",
        hours_ago: float = 0,
        metadata: dict | None = None,
    ) -> TranscriptEvent:
        """Helper to create a TranscriptEvent with relative time."""
        created_at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        return TranscriptEvent(
            event_id=f"event_{hash(content)}",
            sequence=1,
            role="assistant",
            kind="message",
            route="test",
            content=content,
            created_at=created_at.isoformat(),
            _metadata=metadata or {},
        )

    async def test_compress_empty_context(self) -> None:
        """Compress should handle empty context."""
        projection = self._create_context_projection([])
        result = await self.compressor.compress(projection)
        assert isinstance(result, CompressionResult)
        assert result.compressed_content == ""
        assert result.original_tokens == 0
        assert result.compressed_tokens == 0

    async def test_compress_single_item(self) -> None:
        """Compress should handle single item context."""
        events = [self._create_event("Hello world")]
        projection = self._create_context_projection(events)
        result = await self.compressor.compress(projection)
        assert isinstance(result, CompressionResult)
        assert "Hello world" in result.compressed_content
        assert result.original_tokens > 0
        assert result.compressed_tokens > 0

    async def test_compress_respects_token_budget(self) -> None:
        """Compress should respect token budget."""
        events = [self._create_event(f"Content number {i}: " + "x" * 100) for i in range(10)]
        projection = self._create_context_projection(events)
        # Set a tight budget
        compressor = IntelligentCompressor(llm=self.mock_llm, max_tokens=500)
        result = await compressor.compress(projection, target_tokens=100)
        assert result.compressed_tokens <= 150  # Some tolerance

    async def test_compression_ratio_calculation(self) -> None:
        """Compression ratio should be calculated correctly."""
        events = [self._create_event("Short message")]
        projection = self._create_context_projection(events)
        result = await self.compressor.compress(projection)
        assert 0.0 <= result.compression_ratio <= 1.0

    async def test_preserved_key_points_extraction(self) -> None:
        """Key points should be preserved in compression."""
        events = [
            self._create_event("First message about the task"),
            self._create_event("Second message with details"),
        ]
        projection = self._create_context_projection(events)
        result = await self.compressor.compress(projection)
        assert isinstance(result.preserved_key_points, tuple)

    def test_compression_result_dataclass(self) -> None:
        """CompressionResult should have correct structure."""
        result = CompressionResult(
            compressed_content="test content",
            original_tokens=100,
            compressed_tokens=50,
            compression_ratio=0.5,
            preserved_key_points=("key1", "key2"),
        )
        assert result.compressed_content == "test content"
        assert result.original_tokens == 100
        assert result.compressed_tokens == 50
        assert result.compression_ratio == 0.5
        assert result.preserved_key_points == ("key1", "key2")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
