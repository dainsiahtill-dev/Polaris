"""Embedding Cache for Semantic Intent Inference."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingEntry:
    """Embedding 缓存条目"""

    vector: list[float]
    timestamp: float
    ttl: float = 3600  # 1小时默认

    def is_expired(self) -> bool:
        return time.time() - self.timestamp > self.ttl


class EmbeddingCache:
    """Embedding 向量缓存

    用于加速语义意图推断的 Slow Path。
    缓存常用查询的 embedding 结果。
    """

    def __init__(self, max_size: int = 1000, ttl: float = 3600) -> None:
        self._cache: dict[str, EmbeddingEntry] = {}
        self._max_size = max_size
        self._ttl = ttl
        self._hits = 0
        self._misses = 0

    def get(self, text: str) -> list[float] | None:
        """获取缓存的 embedding"""
        key = self._make_key(text)

        if key in self._cache:
            entry = self._cache[key]
            if not entry.is_expired():
                self._hits += 1
                return entry.vector
            else:
                del self._cache[key]

        self._misses += 1
        return None

    def set(self, text: str, vector: list[float]) -> None:
        """缓存 embedding"""
        # LRU 淘汰
        if len(self._cache) >= self._max_size:
            oldest = min(self._cache.items(), key=lambda x: x[1].timestamp)
            del self._cache[oldest[0]]
            logger.debug(f"Evicted oldest embedding: {oldest[0][:20]}...")

        key = self._make_key(text)
        self._cache[key] = EmbeddingEntry(
            vector=vector,
            timestamp=time.time(),
            ttl=self._ttl,
        )

    def get_stats(self) -> dict[str, Any]:
        """获取缓存统计"""
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0

        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": hit_rate,
            "size": len(self._cache),
            "max_size": self._max_size,
        }

    def clear(self) -> None:
        """清空缓存"""
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    def _make_key(self, text: str) -> str:
        """生成缓存键"""
        return hashlib.md5(text.encode()).hexdigest()
