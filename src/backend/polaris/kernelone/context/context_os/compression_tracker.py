"""Compression State Tracker - 压缩状态追踪

ADR-0067: ContextOS 2.0 摘要策略选型

追踪压缩历史，避免重复压缩，优化性能。

特点:
- 压缩历史: 记录每次压缩的内容哈希和结果
- 去重: 相同内容不重复压缩
- 统计: 压缩率、CPU 时间等指标收集
- 缓存淘汰: LRU 缓存管理
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompressionRecord:
    """压缩记录"""

    content_hash: str
    original_size: int
    compressed_size: int
    compression_ratio: float
    strategy: str
    content_type: str
    timestamp: float
    duration_ms: float
    tokens_saved: int = 0


@dataclass
class CompressionStats:
    """压缩统计"""

    total_compressions: int = 0
    total_tokens_saved: int = 0
    total_bytes_saved: int = 0
    total_duration_ms: float = 0.0
    strategy_usage: dict[str, int] = field(default_factory=dict)
    content_type_usage: dict[str, int] = field(default_factory=dict)
    cache_hits: int = 0
    cache_misses: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_compressions": self.total_compressions,
            "total_tokens_saved": self.total_tokens_saved,
            "total_bytes_saved": self.total_bytes_saved,
            "total_duration_ms": self.total_duration_ms,
            "avg_duration_ms": (self.total_duration_ms / self.total_compressions if self.total_compressions > 0 else 0),
            "avg_bytes_saved_per_compression": (
                self.total_bytes_saved / self.total_compressions if self.total_compressions > 0 else 0
            ),
            "strategy_usage": dict(self.strategy_usage),
            "content_type_usage": dict(self.content_type_usage),
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_hit_rate": (
                self.cache_hits / (self.cache_hits + self.cache_misses)
                if (self.cache_hits + self.cache_misses) > 0
                else 0
            ),
        }


class CompressionStateTracker:
    """压缩状态追踪器

    追踪压缩历史，避免重复压缩，提供统计信息。

    Example:
        ```python
        tracker = CompressionStateTracker(max_cache_size=1000)

        # 检查是否已压缩过
        content = "..."
        content_hash = tracker.compute_hash(content)

        if tracker.is_compressed(content_hash):
            cached = tracker.get_cached(content_hash)
            print(f"Cache hit! Ratio: {cached.compression_ratio}")
        else:
            # 执行压缩
            result = compressor.summarize(content, max_tokens)
            tracker.record(
                content_hash=content_hash,
                original_size=len(content),
                compressed_size=len(result),
                strategy=strategy.name,
                content_type=content_type,
                duration_ms=duration,
            )
        ```
    """

    def __init__(
        self,
        max_cache_size: int = 1000,
        ttl_seconds: float = 3600.0,
    ) -> None:
        """初始化压缩状态追踪器

        Args:
            max_cache_size: 最大缓存条目数
            ttl_seconds: 缓存条目 TTL (秒)
        """
        self._cache: OrderedDict[str, CompressionRecord] = OrderedDict()
        self._max_cache_size = max_cache_size
        self._ttl_seconds = ttl_seconds
        self._stats = CompressionStats()
        self._compression_history: list[CompressionRecord] = []

    @staticmethod
    def compute_hash(content: str) -> str:
        """计算内容哈希

        Args:
            content: 内容字符串

        Returns:
            SHA256 哈希值
        """
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def is_compressed(self, content_hash: str) -> bool:
        """检查内容是否已压缩过

        Args:
            content_hash: 内容哈希

        Returns:
            True 如果存在有效缓存
        """
        if content_hash not in self._cache:
            self._stats.cache_misses += 1
            return False

        record = self._cache[content_hash]

        # 检查 TTL
        if time.time() - record.timestamp > self._ttl_seconds:
            del self._cache[content_hash]
            self._stats.cache_misses += 1
            return False

        # 移动到末尾 (LRU)
        self._cache.move_to_end(content_hash)
        self._stats.cache_hits += 1
        return True

    def get_cached(self, content_hash: str) -> CompressionRecord | None:
        """获取缓存的压缩记录

        Args:
            content_hash: 内容哈希

        Returns:
            压缩记录，如果不存在或过期则返回 None
        """
        if content_hash not in self._cache:
            return None

        record = self._cache[content_hash]

        # 检查 TTL
        if time.time() - record.timestamp > self._ttl_seconds:
            del self._cache[content_hash]
            return None

        return record

    def record(
        self,
        content_hash: str,
        original_size: int,
        compressed_size: int,
        strategy: str,
        content_type: str,
        duration_ms: float,
        tokens_saved: int = 0,
    ) -> CompressionRecord:
        """记录压缩结果

        Args:
            content_hash: 内容哈希
            original_size: 原始大小
            compressed_size: 压缩后大小
            strategy: 使用的压缩策略
            content_type: 内容类型
            duration_ms: 压缩耗时 (毫秒)
            tokens_saved: 节省的 token 数

        Returns:
            压缩记录
        """
        compression_ratio = compressed_size / original_size if original_size > 0 else 1.0
        bytes_saved = original_size - compressed_size

        record = CompressionRecord(
            content_hash=content_hash,
            original_size=original_size,
            compressed_size=compressed_size,
            compression_ratio=compression_ratio,
            strategy=strategy,
            content_type=content_type,
            timestamp=time.time(),
            duration_ms=duration_ms,
            tokens_saved=tokens_saved,
        )

        # 更新缓存
        if content_hash in self._cache:
            self._cache.move_to_end(content_hash)
        self._cache[content_hash] = record

        # LRU 淘汰
        while len(self._cache) > self._max_cache_size:
            self._cache.popitem(last=False)

        # 更新历史
        self._compression_history.append(record)
        if len(self._compression_history) > 10000:
            self._compression_history = self._compression_history[-5000:]

        # 更新统计
        self._stats.total_compressions += 1
        self._stats.total_bytes_saved += bytes_saved
        self._stats.total_tokens_saved += tokens_saved
        self._stats.total_duration_ms += duration_ms
        self._stats.strategy_usage[strategy] = self._stats.strategy_usage.get(strategy, 0) + 1
        self._stats.content_type_usage[content_type] = self._stats.content_type_usage.get(content_type, 0) + 1

        return record

    def get_stats(self) -> CompressionStats:
        """获取压缩统计

        Returns:
            压缩统计对象
        """
        return self._stats

    def get_recent_records(
        self,
        n: int = 10,
        strategy: str | None = None,
    ) -> list[CompressionRecord]:
        """获取最近的压缩记录

        Args:
            n: 返回记录数
            strategy: 可选，按策略过滤

        Returns:
            压缩记录列表
        """
        records = self._compression_history
        if strategy:
            records = [r for r in records if r.strategy == strategy]
        return records[-n:]

    def get_best_compression(
        self,
        content_type: str | None = None,
    ) -> CompressionRecord | None:
        """获取最佳压缩记录

        Args:
            content_type: 可选，按内容类型过滤

        Returns:
            压缩率最低的记录
        """
        records = self._compression_history
        if content_type:
            records = [r for r in records if r.content_type == content_type]

        if not records:
            return None

        return min(records, key=lambda r: r.compression_ratio)

    def reset(self) -> None:
        """重置追踪器状态"""
        self._cache.clear()
        self._stats = CompressionStats()
        self._compression_history.clear()

    def prune_expired(self) -> int:
        """清理过期缓存条目

        Returns:
            清理的条目数
        """
        current_time = time.time()
        expired_keys = [
            key for key, record in self._cache.items() if current_time - record.timestamp > self._ttl_seconds
        ]

        for key in expired_keys:
            del self._cache[key]

        return len(expired_keys)

    def get_cache_size(self) -> int:
        """获取当前缓存大小"""
        return len(self._cache)

    def should_skip_compression(
        self,
        content_hash: str,
        min_improvement_ratio: float = 0.05,
    ) -> tuple[bool, CompressionRecord | None]:
        """判断是否应该跳过压缩

        基于缓存历史，判断压缩是否值得。

        Args:
            content_hash: 内容哈希
            min_improvement_ratio: 最小改进比例

        Returns:
            (是否跳过, 缓存记录或None)
        """
        if not self.is_compressed(content_hash):
            return False, None

        cached = self.get_cached(content_hash)
        if cached is None:
            return False, None

        # 如果之前的压缩率已经很好，跳过
        if cached.compression_ratio <= min_improvement_ratio:
            logger.debug(
                f"Skipping compression for {content_hash}: previous ratio {cached.compression_ratio:.2%} is good enough"
            )
            return True, cached

        return False, cached
