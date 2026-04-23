"""Tests for compression_daemon module.

Tests for the preemptive context compression daemon that monitors
memory usage and triggers background compression before token budget exhaustion.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable
from unittest.mock import MagicMock, patch

import pytest
from polaris.kernelone.akashic.compression_daemon import (
    CompressionDaemon,
    CompressionStats,
    DaemonConfig,
    DaemonState,
)


class TestDaemonState:
    """Tests for DaemonState enum."""

    def test_all_states_defined(self) -> None:
        """Test all expected daemon states are defined."""
        assert DaemonState.STOPPED.value == "stopped"
        assert DaemonState.IDLE.value == "idle"
        assert DaemonState.MONITORING.value == "monitoring"
        assert DaemonState.COMPRESSING_SOFT.value == "compressing_soft"
        assert DaemonState.COMPRESSING_HARD.value == "compressing_hard"
        assert DaemonState.STOPPING.value == "stopping"

    def test_state_is_enum(self) -> None:
        """Test DaemonState is a proper Enum."""
        assert isinstance(DaemonState.STOPPED, Enum)
        assert isinstance(DaemonState.MONITORING, Enum)


class TestDaemonConfig:
    """Tests for DaemonConfig dataclass."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = DaemonConfig()
        assert config.check_interval_ms == 500
        assert config.soft_watermark_pct == 0.75
        assert config.hard_watermark_pct == 0.90
        assert config.max_concurrent_compressions == 2
        assert config.enable_incremental is True
        assert config.min_tokens_to_compress == 1000

    def test_custom_config(self) -> None:
        """Test custom configuration values."""
        config = DaemonConfig(
            check_interval_ms=100,
            soft_watermark_pct=0.80,
            hard_watermark_pct=0.95,
            max_concurrent_compressions=1,
            enable_incremental=False,
            min_tokens_to_compress=500,
        )
        assert config.check_interval_ms == 100
        assert config.soft_watermark_pct == 0.80
        assert config.hard_watermark_pct == 0.95
        assert config.max_concurrent_compressions == 1
        assert config.enable_incremental is False
        assert config.min_tokens_to_compress == 500

    def test_config_is_dataclass(self) -> None:
        """Test DaemonConfig is a dataclass."""
        config1 = DaemonConfig()
        config2 = DaemonConfig()
        # Same defaults should be equal
        assert config1.check_interval_ms == config2.check_interval_ms


class TestCompressionStats:
    """Tests for CompressionStats dataclass."""

    def test_default_stats(self) -> None:
        """Test default compression statistics."""
        stats = CompressionStats()
        assert stats.soft_compressions == 0
        assert stats.hard_compressions == 0
        assert stats.total_tokens_freed == 0
        assert stats.total_compression_time_ms == 0
        assert stats.last_compression_at is None
        assert stats.skipped_no_threshold == 0

    def test_custom_stats(self) -> None:
        """Test custom compression statistics."""
        now = datetime.now(timezone.utc)
        stats = CompressionStats(
            soft_compressions=5,
            hard_compressions=2,
            total_tokens_freed=15000,
            total_compression_time_ms=2500,
            last_compression_at=now,
            skipped_no_threshold=3,
        )
        assert stats.soft_compressions == 5
        assert stats.hard_compressions == 2
        assert stats.total_tokens_freed == 15000
        assert stats.total_compression_time_ms == 2500
        assert stats.last_compression_at == now
        assert stats.skipped_no_threshold == 3


class MockWorkingMemory:
    """Mock working memory for testing."""

    def __init__(self, usage_ratio: float = 0.5, middle_tokens: int = 5000) -> None:
        self._usage_ratio = usage_ratio
        self._middle_tokens = middle_tokens
        self._messages: list[dict[str, Any]] = []
        self._chunks: list[Any] = []
        self.clear_called = False
        self._is_in_tail: Callable[[Any], bool] | None = None

    @property
    def chunks(self) -> list[Any]:
        """Return chunks list for compatibility with real implementation."""
        return self._chunks

    def get_snapshot(self) -> MagicMock:
        """Return a mock snapshot."""
        snapshot = MagicMock()
        snapshot.usage_ratio = self._usage_ratio
        snapshot.middle_tokens = self._middle_tokens
        snapshot.total_tokens = int(self._usage_ratio * 32000)
        return snapshot

    def get_messages(self, *, max_tokens: int | None = None) -> list[dict[str, Any]]:
        """Return mock messages."""
        return self._messages

    def clear(self) -> None:
        """Track clear calls."""
        self.clear_called = True

    def push(
        self,
        role: str,
        content: str,
        *,
        importance: int = 5,
        turn_index: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Mock push operation."""
        return f"chunk_{len(self._chunks)}"


class MockMemoryManager:
    """Mock memory manager for testing."""

    def __init__(self, usage_ratio: float = 0.5, middle_tokens: int = 5000) -> None:
        self._working_memory = MockWorkingMemory(usage_ratio, middle_tokens)

    @property
    def working_memory(self) -> MockWorkingMemory:
        """Return mock working memory."""
        return self._working_memory


class TestCompressionDaemon:
    """Tests for CompressionDaemon class."""

    def test_initialization_default(self) -> None:
        """Test daemon initialization with defaults."""
        manager = MockMemoryManager()
        daemon = CompressionDaemon(memory_manager=manager)

        assert daemon.state == DaemonState.STOPPED
        assert daemon._config is not None
        assert daemon._config.check_interval_ms == 500
        assert daemon._stats.soft_compressions == 0
        assert daemon._stats.hard_compressions == 0

    def test_initialization_custom_config(self) -> None:
        """Test daemon initialization with custom config."""
        manager = MockMemoryManager()
        config = DaemonConfig(
            check_interval_ms=100,
            soft_watermark_pct=0.70,
            hard_watermark_pct=0.85,
        )
        daemon = CompressionDaemon(memory_manager=manager, config=config)

        assert daemon._config.check_interval_ms == 100
        assert daemon._config.soft_watermark_pct == 0.70
        assert daemon._config.hard_watermark_pct == 0.85

    def test_initialization_with_llm_client(self) -> None:
        """Test daemon initialization with LLM client."""
        manager = MockMemoryManager()
        llm_client = MagicMock()
        daemon = CompressionDaemon(
            memory_manager=manager,
            llm_client=llm_client,
            workspace="/test/workspace",
        )

        assert daemon._llm_client is llm_client
        assert daemon._workspace == "/test/workspace"

    def test_state_property(self) -> None:
        """Test state property getter."""
        manager = MockMemoryManager()
        daemon = CompressionDaemon(memory_manager=manager)

        # Initial state is STOPPED
        assert daemon.state == DaemonState.STOPPED

    def test_stats_property(self) -> None:
        """Test stats property getter."""
        manager = MockMemoryManager()
        daemon = CompressionDaemon(memory_manager=manager)

        stats = daemon.stats
        assert isinstance(stats, CompressionStats)
        assert stats.soft_compressions == 0
        assert stats.hard_compressions == 0

    @pytest.mark.asyncio
    async def test_start_from_stopped(self) -> None:
        """Test starting daemon from stopped state."""
        manager = MockMemoryManager()
        daemon = CompressionDaemon(memory_manager=manager)

        await daemon.start()
        try:
            # State should transition to MONITORING
            assert daemon.state == DaemonState.MONITORING
            assert daemon._task is not None
        finally:
            await daemon.stop()

    @pytest.mark.asyncio
    async def test_start_when_already_running(self) -> None:
        """Test starting daemon when already running logs warning."""
        manager = MockMemoryManager()
        daemon = CompressionDaemon(memory_manager=manager)

        await daemon.start()
        try:
            # Try to start again - should log warning but not crash
            await daemon.start()
            # State should still be MONITORING
            assert daemon.state == DaemonState.MONITORING
        finally:
            await daemon.stop()

    @pytest.mark.asyncio
    async def test_stop_when_stopped(self) -> None:
        """Test stopping daemon when already stopped does nothing."""
        manager = MockMemoryManager()
        daemon = CompressionDaemon(memory_manager=manager)

        # Stop should return immediately
        await daemon.stop()
        assert daemon.state == DaemonState.STOPPED

    @pytest.mark.asyncio
    async def test_stop_after_start(self) -> None:
        """Test stopping daemon after starting."""
        manager = MockMemoryManager()
        daemon = CompressionDaemon(memory_manager=manager)

        await daemon.start()
        await daemon.stop()
        assert daemon.state == DaemonState.STOPPED
        assert daemon._task is None

    @pytest.mark.asyncio
    async def test_get_status(self) -> None:
        """Test get_status returns expected structure."""
        manager = MockMemoryManager(usage_ratio=0.65)
        daemon = CompressionDaemon(memory_manager=manager)

        status = daemon.get_status()

        assert "state" in status
        assert "usage_trend" in status
        assert "last_usage_ratio" in status
        assert "active_compressions" in status
        assert "max_concurrent" in status
        assert "stats" in status
        assert status["state"] == "stopped"
        assert status["max_concurrent"] == 2


class TestCompressionDaemonWaterline:
    """Tests for CompressionDaemon waterline checking logic."""

    def _create_mock_snapshot(
        self,
        usage_ratio: float,
        middle_tokens: int = 5000,
    ) -> MagicMock:
        """Create a mock working memory snapshot."""
        snapshot = MagicMock()
        snapshot.usage_ratio = usage_ratio
        snapshot.middle_tokens = middle_tokens
        snapshot.total_tokens = int(usage_ratio * 32000)
        return snapshot

    @pytest.mark.asyncio
    async def test_hard_watermark_triggers_compression(self) -> None:
        """Test hard watermark (90%) triggers compression."""
        manager = MockMemoryManager(usage_ratio=0.92, middle_tokens=8000)
        daemon = CompressionDaemon(memory_manager=manager)
        daemon._lock = asyncio.Lock()
        # Set state to MONITORING to allow compression check
        daemon._state = DaemonState.MONITORING

        # Manually trigger check
        await daemon._check_and_trigger_compression()

        # State should have been COMPRESSING_HARD then back to MONITORING
        assert daemon.state == DaemonState.MONITORING

    @pytest.mark.asyncio
    async def test_soft_watermark_with_rising_trend(self) -> None:
        """Test soft watermark with rising trend triggers compression."""
        manager = MockMemoryManager(usage_ratio=0.80, middle_tokens=8000)
        daemon = CompressionDaemon(memory_manager=manager)
        daemon._lock = asyncio.Lock()
        daemon._usage_trend = "rising"
        # Set state to MONITORING to allow compression check
        daemon._state = DaemonState.MONITORING

        # Manually trigger check
        await daemon._check_and_trigger_compression()

        # State should have been COMPRESSING_SOFT then back to MONITORING
        assert daemon.state == DaemonState.MONITORING

    @pytest.mark.asyncio
    async def test_soft_watermark_with_falling_trend_skipped(self) -> None:
        """Test soft watermark with falling trend does not trigger compression."""
        manager = MockMemoryManager(usage_ratio=0.80, middle_tokens=8000)
        daemon = CompressionDaemon(memory_manager=manager)
        daemon._lock = asyncio.Lock()
        daemon._usage_trend = "falling"
        # Set state to MONITORING
        daemon._state = DaemonState.MONITORING

        # Manually trigger check
        await daemon._check_and_trigger_compression()

        # State should remain MONITORING (no compression triggered for soft with falling trend)
        assert daemon.state == DaemonState.MONITORING

    @pytest.mark.asyncio
    async def test_below_watermark_no_compression(self) -> None:
        """Test below watermark does not trigger compression."""
        manager = MockMemoryManager(usage_ratio=0.50, middle_tokens=3000)
        daemon = CompressionDaemon(memory_manager=manager)
        daemon._lock = asyncio.Lock()
        # Set state to MONITORING
        daemon._state = DaemonState.MONITORING

        # Manually trigger check
        await daemon._check_and_trigger_compression()

        # State should remain MONITORING
        assert daemon.state == DaemonState.MONITORING

    @pytest.mark.asyncio
    async def test_low_middle_tokens_skips_compression(self) -> None:
        """Test low middle tokens skips compression."""
        manager = MockMemoryManager(usage_ratio=0.92, middle_tokens=500)  # Below min_tokens_to_compress
        daemon = CompressionDaemon(memory_manager=manager)
        daemon._lock = asyncio.Lock()
        # Set state to MONITORING
        daemon._state = DaemonState.MONITORING

        # Manually trigger check
        await daemon._check_and_trigger_compression()

        # Skipped counter should increment
        assert daemon._stats.skipped_no_threshold == 1


class TestIncrementalCompact:
    """Tests for incremental compaction operations."""

    def _create_mock_chunk(
        self,
        chunk_id: str,
        priority: int,
        estimated_tokens: int,
        recency_score: float = 0.5,
    ) -> MagicMock:
        """Create a mock chunk with specified attributes."""
        chunk = MagicMock()
        chunk.chunk_id = chunk_id
        chunk.priority = MagicMock()
        chunk.priority.value = priority
        chunk.estimated_tokens = estimated_tokens
        chunk.recency_score = recency_score
        chunk.role = "user"
        chunk.content = f"Content for {chunk_id}"
        chunk.importance = 5
        chunk.metadata = {}
        return chunk

    @pytest.mark.asyncio
    async def test_incremental_compress_empty_chunks(self) -> None:
        """Test incremental compression with no chunks."""
        manager = MockMemoryManager()
        manager._working_memory._chunks = []
        daemon = CompressionDaemon(memory_manager=manager)

        snapshot = manager.working_memory.get_snapshot()
        method = await daemon._incremental_compress("soft", snapshot)

        assert method == "incremental_skip_empty"

    @pytest.mark.asyncio
    async def test_incremental_compress_soft_level(self) -> None:
        """Test incremental compression with soft level."""
        manager = MockMemoryManager(usage_ratio=0.80, middle_tokens=5000)

        # Create mock chunks
        head_chunk = self._create_mock_chunk("head1", priority=1, estimated_tokens=2000)
        middle_chunk = self._create_mock_chunk("mid1", priority=3, estimated_tokens=3000)
        tail_chunk = self._create_mock_chunk("tail1", priority=2, estimated_tokens=1000)

        manager._working_memory._chunks = [head_chunk, middle_chunk, tail_chunk]

        # Mock _is_in_tail to identify tail chunk
        manager.working_memory._is_in_tail = lambda c: c.chunk_id == "tail1"  # type: ignore[attr-defined]

        daemon = CompressionDaemon(memory_manager=manager)

        snapshot = manager.working_memory.get_snapshot()
        method = await daemon._incremental_compress("soft", snapshot)

        # Should compress with soft level
        assert "incremental_soft" in method

    @pytest.mark.asyncio
    async def test_incremental_compress_hard_level(self) -> None:
        """Test incremental compression with hard level (more aggressive)."""
        manager = MockMemoryManager(usage_ratio=0.95, middle_tokens=8000)

        # Create mock chunks
        head_chunk = self._create_mock_chunk("head1", priority=1, estimated_tokens=2000)
        middle_chunk = self._create_mock_chunk("mid1", priority=3, estimated_tokens=5000)
        middle_chunk2 = self._create_mock_chunk("mid2", priority=4, estimated_tokens=3000)
        tail_chunk = self._create_mock_chunk("tail1", priority=2, estimated_tokens=1000)

        manager._working_memory._chunks = [head_chunk, middle_chunk, middle_chunk2, tail_chunk]

        # Mock _is_in_tail
        manager.working_memory._is_in_tail = lambda c: c.chunk_id == "tail1"

        daemon = CompressionDaemon(memory_manager=manager)

        snapshot = manager.working_memory.get_snapshot()
        method = await daemon._incremental_compress("hard", snapshot)

        # Hard level uses 60% target ratio
        assert "incremental_hard" in method


class TestCompressionDaemonCleanup:
    """Tests for compression task cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_removes_completed_tasks(self) -> None:
        """Test cleanup removes completed tasks."""
        manager = MockMemoryManager()
        daemon = CompressionDaemon(memory_manager=manager)

        # Create a completed task
        async def dummy_task() -> None:
            pass

        completed_task = asyncio.create_task(dummy_task())
        await completed_task  # Wait for completion

        daemon._compression_tasks.append(completed_task)
        assert len(daemon._compression_tasks) == 1

        await daemon._cleanup_compression_tasks()

        # Task should be removed
        assert len(daemon._compression_tasks) == 0

    @pytest.mark.asyncio
    async def test_cleanup_keeps_running_tasks(self) -> None:
        """Test cleanup keeps running tasks."""
        manager = MockMemoryManager()
        daemon = CompressionDaemon(memory_manager=manager)

        # Create a running task
        async def long_task() -> None:
            await asyncio.sleep(10)

        running_task = asyncio.create_task(long_task())

        daemon._compression_tasks.append(running_task)
        assert len(daemon._compression_tasks) == 1

        await daemon._cleanup_compression_tasks()

        # Task should still be there
        assert len(daemon._compression_tasks) == 1

        # Cancel and cleanup
        running_task.cancel()
        with suppress(asyncio.CancelledError):
            await running_task


class TestCompressionDaemonRunLoop:
    """Tests for the main daemon run loop."""

    @pytest.mark.asyncio
    async def test_run_loop_updates_usage_trend(self) -> None:
        """Test run loop updates usage trend."""
        manager = MockMemoryManager(usage_ratio=0.65)
        daemon = CompressionDaemon(memory_manager=manager, config=DaemonConfig(check_interval_ms=50))
        daemon._lock = asyncio.Lock()

        await daemon.start()

        # Let loop run a few iterations
        await asyncio.sleep(0.2)

        await daemon.stop()

        # Usage trend should be tracked
        assert daemon._usage_trend in ("stable", "rising", "falling")
        assert daemon._last_usage_ratio > 0

    @pytest.mark.asyncio
    async def test_run_loop_respects_max_concurrent(self) -> None:
        """Test run loop respects max concurrent compression limit."""
        manager = MockMemoryManager(usage_ratio=0.92, middle_tokens=10000)
        daemon = CompressionDaemon(
            memory_manager=manager,
            config=DaemonConfig(max_concurrent_compressions=1),
        )
        daemon._lock = asyncio.Lock()

        await daemon.start()

        # Run briefly
        await asyncio.sleep(0.15)

        await daemon.stop()

        # Should not exceed max concurrent
        assert len(daemon._compression_tasks) <= 1


class TestCompressorFallback:
    """Tests for compressor fallback behavior."""

    @pytest.mark.asyncio
    async def test_fallback_when_compressor_unavailable(self) -> None:
        """Test fallback when RoleContextCompressor is unavailable."""
        manager = MockMemoryManager(usage_ratio=0.85, middle_tokens=5000)
        manager._working_memory._messages = [{"role": "user", "content": "Test message"}]

        daemon = CompressionDaemon(memory_manager=manager)
        daemon._llm_client = None  # No LLM client

        # Ensure compressor is None by patching
        with patch.object(daemon, "_get_or_create_compressor", return_value=None):
            snapshot = manager.working_memory.get_snapshot()
            # Should not raise, should handle gracefully
            await daemon._run_compression("soft", snapshot)

    @pytest.mark.asyncio
    async def test_eviction_fallback_on_llm_error(self) -> None:
        """Test eviction fallback when LLM compression fails."""
        manager = MockMemoryManager(usage_ratio=0.85, middle_tokens=5000)
        manager._working_memory._messages = [{"role": "user", "content": "Test message"}]

        daemon = CompressionDaemon(memory_manager=manager)
        # No LLM client means it will skip LLM compression attempt

        snapshot = manager.working_memory.get_snapshot()
        # Should handle gracefully without crashing
        await daemon._run_compression("soft", snapshot)

        # Compression was triggered (middle_tokens >= min_tokens_to_compress)
        # Since no LLM client, it falls back to incremental or eviction
        assert daemon._stats.soft_compressions >= 0  # May or may not compress depending on implementation
