"""LLM Call Cache - LLM 调用缓存

提供基于提示词指纹的请求缓存，减少重复 LLM 调用成本。
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """缓存条目"""

    key: str
    response_content: str
    token_estimate: int
    created_at: datetime = field(default_factory=datetime.now)
    hit_count: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


class LLMCache:
    """LLM 调用缓存

    基于提示词指纹 + 上下文摘要哈希进行缓存。
    支持 TTL 过期和手动清理。

    优化说明:
    - 使用 OrderedDict 实现 O(1) 的 LRU 驱逐
    - 驱逐操作在达到容量限制时自动触发
    - 过期条目在访问时惰性清理
    """

    def __init__(
        self,
        max_size: int = 1000,
        ttl_minutes: int = 60,
        enable_cache: bool = True,
    ) -> None:
        """初始化缓存

        Args:
            max_size: 最大缓存条目数
            ttl_minutes: 缓存过期时间（分钟）
            enable_cache: 是否启用缓存
        """
        # Use OrderedDict for O(1) LRU operations
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._max_size = max_size
        self._ttl = timedelta(minutes=ttl_minutes)
        self._enable_cache = enable_cache
        self._lock = threading.RLock()
        self._stats = {
            "hits": 0,
            "misses": 0,
            "evictions": 0,
        }

    def _make_key(
        self,
        prompt_fingerprint: str,
        context_summary: str,
        temperature: float,
        model: str,
    ) -> str:
        """生成缓存键

        Args:
            prompt_fingerprint: 提示词指纹
            context_summary: 上下文摘要
            temperature: 温度参数
            model: 模型名称

        Returns:
            缓存键
        """
        # 组合所有参数
        raw = f"{prompt_fingerprint}|{context_summary}|{temperature}|{model}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def get(
        self,
        prompt_fingerprint: str,
        context_summary: str,
        temperature: float,
        model: str,
    ) -> str | None:
        """获取缓存的响应

        Args:
            prompt_fingerprint: 提示词指纹
            context_summary: 上下文摘要
            temperature: 温度参数
            model: 模型名称

        Returns:
            缓存的响应内容，如果没有缓存则返回 None
        """
        if not self._enable_cache:
            return None

        with self._lock:
            key = self._make_key(prompt_fingerprint, context_summary, temperature, model)
            entry = self._cache.get(key)

            if entry is None:
                self._stats["misses"] += 1
                return None

            # 检查是否过期
            if datetime.now() - entry.created_at > self._ttl:
                del self._cache[key]
                self._stats["misses"] += 1
                return None

            # 命中缓存 - 移动到末尾标记为最近使用
            self._cache.move_to_end(key)
            entry.hit_count += 1
            self._stats["hits"] += 1
            logger.debug(f"LLM cache hit: {key[:8]}... (hit_count={entry.hit_count})")
            return entry.response_content

    def put(
        self,
        prompt_fingerprint: str,
        context_summary: str,
        temperature: float,
        model: str,
        response_content: str,
        token_estimate: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """存储响应到缓存

        Args:
            prompt_fingerprint: 提示词指纹
            context_summary: 上下文摘要
            temperature: 温度参数
            model: 模型名称
            response_content: 响应内容
            token_estimate: token 估算
            metadata: 额外元数据
        """
        if not self._enable_cache:
            return

        with self._lock:
            key = self._make_key(prompt_fingerprint, context_summary, temperature, model)

            # Check if key already exists (update case)
            if key in self._cache:
                # Update existing entry and move to end
                self._cache[key] = CacheEntry(
                    key=key,
                    response_content=response_content,
                    token_estimate=token_estimate,
                    metadata=metadata or {},
                )
                self._cache.move_to_end(key)
                logger.debug(f"LLM cache updated: {key[:8]}...")
                return

            # Check if we need to evict (at capacity)
            if len(self._cache) >= self._max_size:
                # O(1) eviction of oldest item (FIFO order in OrderedDict)
                oldest_key, _ = self._cache.popitem(last=False)
                self._stats["evictions"] += 1
                logger.debug(f"LLM cache evicted: {oldest_key[:8]}...")

            # Add new entry
            self._cache[key] = CacheEntry(
                key=key,
                response_content=response_content,
                token_estimate=token_estimate,
                metadata=metadata or {},
            )
            logger.debug(f"LLM cache put: {key[:8]}...")

    def clear(self) -> None:
        """清空缓存"""
        with self._lock:
            self._cache.clear()
            logger.info("LLM cache cleared")

    def get_stats(self) -> dict[str, Any]:
        """获取缓存统计

        Returns:
            缓存统计信息
        """
        with self._lock:
            total = self._stats["hits"] + self._stats["misses"]
            hit_rate = self._stats["hits"] / total if total > 0 else 0.0

            return {
                "hits": self._stats["hits"],
                "misses": self._stats["misses"],
                "evictions": self._stats["evictions"],
                "size": len(self._cache),
                "max_size": self._max_size,
                "hit_rate": round(hit_rate * 100, 2),
                "enabled": self._enable_cache,
            }

    def invalidate_by_pattern(self, pattern: str) -> int:
        """根据模式使缓存失效

        Args:
            pattern: 模式字符串

        Returns:
            失效的条目数
        """
        with self._lock:
            keys_to_delete = [k for k, v in self._cache.items() if pattern in v.response_content]
            for key in keys_to_delete:
                del self._cache[key]
            return len(keys_to_delete)


# 全局缓存实例
_global_cache: LLMCache | None = None
_cache_lock = threading.Lock()


def get_global_llm_cache() -> LLMCache:
    """获取全局 LLM 缓存实例"""
    global _global_cache
    with _cache_lock:
        if _global_cache is None:
            _global_cache = LLMCache(
                max_size=1000,
                ttl_minutes=60,
                enable_cache=True,
            )
        return _global_cache


def set_global_llm_cache(cache: LLMCache) -> None:
    """设置全局 LLM 缓存实例"""
    global _global_cache
    with _cache_lock:
        _global_cache = cache
