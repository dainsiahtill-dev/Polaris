"""Tests for MemoryManager exception recovery scenarios."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from polaris.kernelone.akashic.memory_manager import (
    MemoryManager,
    MemoryManagerConfig,
)


class MockWorkingMemory:
    """Mock working memory for testing."""

    def __init__(self) -> None:
        self.chunks = []
        self._reset_turn_count = 0
        self._clear_count = 0
        self.promotion_queue: list[str] = []
        self.semantic_promotion_queue: list[str] = []

    def reset_turn(self) -> None:
        self._reset_turn_count += 1

    def clear(self) -> None:
        self._clear_count += 1

    def clear_promotion_queue(self) -> None:
        self.promotion_queue.clear()

    def clear_semantic_promotion_queue(self) -> None:
        self.semantic_promotion_queue.clear()

    def get_promotion_queue(self) -> list[str]:
        return self.promotion_queue

    def get_semantic_promotion_queue(self) -> list[str]:
        return self.semantic_promotion_queue

    def get_snapshot(self) -> MagicMock:
        mock = MagicMock()
        mock.total_tokens = 100
        mock.chunk_count = 5
        mock.usage_ratio = 0.5
        mock.compression_triggered = False
        return mock


class MockEpisodicMemory:
    """Mock episodic memory for testing."""

    def __init__(self) -> None:
        self.store_turn_count = 0
        self.seal_episode_count = 0
        self.store_error = False
        self.seal_error = False

    async def store_turn(self, turn_index: int, messages: list[dict], metadata: dict | None = None) -> str:
        self.store_turn_count += 1
        if self.store_error:
            raise RuntimeError("Simulated store error")
        return f"turn_{turn_index}"

    async def seal_episode(self, session_id: str, summary: str) -> str:
        self.seal_episode_count += 1
        if self.seal_error:
            raise RuntimeError("Simulated seal error")
        return f"episode_{session_id}"


class MockSemanticMemory:
    """Mock semantic memory for testing."""

    def __init__(self) -> None:
        self.add_count = 0
        self.add_error = False

    async def add(self, text: str, metadata: dict | None = None, importance: int = 5) -> str:
        self.add_count += 1
        if self.add_error:
            raise RuntimeError("Simulated add error")
        return f"mem_{self.add_count}"

    async def get(self, memory_id: str) -> dict | None:
        return {"id": memory_id, "text": "mock text"}

    async def search(self, query: str, top_k: int = 10, min_importance: int = 1) -> list[tuple[str, float]]:
        return []

    async def delete(self, memory_id: str) -> bool:
        return True

    def get_stats(self) -> dict:
        return {"size": self.add_count}


class TestMemoryManagerExceptionRecovery:
    """Tests for MemoryManager exception recovery scenarios."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.config = MemoryManagerConfig(
            enable_semantic_cache=False,
            enable_episodic_promotion=True,
            enable_tier_sync=False,
        )

    @pytest.mark.asyncio
    async def test_initialize_with_failing_working_memory(self) -> None:
        """Test MemoryManager handles initialization with failing working memory."""
        config = MemoryManagerConfig(enable_tier_sync=False)

        manager = MemoryManager(config=config)
        manager._working_memory = MagicMock()
        manager._working_memory.reset_turn.side_effect = RuntimeError("Init error")

        await manager.initialize()
        assert manager._initialized is True

    @pytest.mark.asyncio
    async def test_session_with_failing_episodic_memory(self) -> None:
        """Test session handles failing episodic memory gracefully."""
        manager = MemoryManager(config=self.config)
        mock_working = MockWorkingMemory()
        mock_episodic = MockEpisodicMemory()
        mock_episodic.store_error = True

        manager._working_memory = mock_working
        manager._episodic_memory = mock_episodic

        await manager.initialize()
        await manager.begin_session("test_session")

        assert manager._session_active is True

    @pytest.mark.asyncio
    async def test_end_session_with_failing_episodic_seal(self) -> None:
        """Test end_session handles failing seal_episode gracefully."""
        manager = MemoryManager(config=self.config)
        mock_working = MockWorkingMemory()
        mock_episodic = MockEpisodicMemory()
        mock_episodic.seal_error = True

        manager._working_memory = mock_working
        manager._episodic_memory = mock_episodic

        await manager.initialize()
        await manager.begin_session("test_session")
        episode_id = await manager.end_session()

        assert episode_id == ""
        assert manager._session_active is False

    @pytest.mark.asyncio
    async def test_tier_coordinator_with_failing_promotion(self) -> None:
        """Test tier coordinator handles failing promotions gracefully."""
        manager = MemoryManager(config=self.config)
        mock_working = MockWorkingMemory()
        mock_working.chunks = [MagicMock(chunk_id="chunk1", content="test", importance=10)]
        mock_working.promotion_queue.append("chunk1")

        mock_episodic = MockEpisodicMemory()
        mock_episodic.store_error = True

        mock_semantic = MockSemanticMemory()

        manager._working_memory = mock_working
        manager._episodic_memory = mock_episodic
        manager._semantic_memory = mock_semantic

        await manager.initialize()

        coordinator = manager.tier_coordinator
        result = await coordinator.sync_tiers()

        assert "working" in result

    @pytest.mark.asyncio
    async def test_graceful_shutdown_after_errors(self) -> None:
        """Test graceful shutdown even after errors occurred."""
        manager = MemoryManager(config=self.config)
        mock_working = MockWorkingMemory()
        mock_episodic = MockEpisodicMemory()
        mock_semantic = MockSemanticMemory()

        manager._working_memory = mock_working
        manager._episodic_memory = mock_episodic
        manager._semantic_memory = mock_semantic

        await manager.initialize()
        await manager.begin_session("test_session")

        await manager.shutdown()

        assert manager._shutdown is True
        assert manager._session_active is False

    @pytest.mark.asyncio
    async def test_double_initialization_is_safe(self) -> None:
        """Test calling initialize twice is safe."""
        manager = MemoryManager(config=self.config)
        await manager.initialize()
        await manager.initialize()

        assert manager._initialized is True

    @pytest.mark.asyncio
    async def test_double_shutdown_is_safe(self) -> None:
        """Test calling shutdown twice is safe."""
        manager = MemoryManager(config=self.config)
        await manager.initialize()
        await manager.shutdown()
        await manager.shutdown()

        assert manager._shutdown is True

    @pytest.mark.asyncio
    async def test_session_end_without_begin_is_safe(self) -> None:
        """Test ending session without begin is safe."""
        manager = MemoryManager(config=self.config)
        await manager.initialize()

        episode_id = await manager.end_session()

        assert episode_id == ""

    @pytest.mark.asyncio
    async def test_get_status_with_failing_tier(self) -> None:
        """Test get_status handles failing tier gracefully."""
        manager = MemoryManager(config=self.config)

        mock_working = MagicMock()
        mock_working.get_snapshot.side_effect = RuntimeError("Snapshot error")

        manager._working_memory = mock_working

        status = manager.get_status()

        assert "tiers" in status
        assert status["tiers"]["working_memory"].get("error") is not None

    @pytest.mark.asyncio
    async def test_tier_sync_with_empty_queue(self) -> None:
        """Test tier sync with empty promotion queue."""
        manager = MemoryManager(config=self.config)
        mock_working = MockWorkingMemory()
        mock_episodic = MockEpisodicMemory()
        mock_semantic = MockSemanticMemory()

        manager._working_memory = mock_working
        manager._episodic_memory = mock_episodic
        manager._semantic_memory = mock_semantic

        await manager.initialize()

        coordinator = manager.tier_coordinator
        result = await coordinator.sync_tiers()

        assert result.get("working") == 0


class TestMemoryManagerConfig:
    """Tests for MemoryManagerConfig."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = MemoryManagerConfig()
        assert config.enable_semantic_cache is True
        assert config.enable_episodic_promotion is True
        assert config.enable_tier_sync is True
        assert config.promotion_importance_threshold == 7
        assert config.sync_interval_seconds == 60.0

    def test_custom_config(self) -> None:
        """Test custom configuration values."""
        config = MemoryManagerConfig(
            enable_semantic_cache=False,
            promotion_importance_threshold=10,
            sync_interval_seconds=120.0,
        )
        assert config.enable_semantic_cache is False
        assert config.promotion_importance_threshold == 10
        assert config.sync_interval_seconds == 120.0
