"""Routing Cache - 多级缓存."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from polaris.kernelone.role.routing.context import RoutingContext

logger = logging.getLogger(__name__)


@dataclass
class CacheStats:
    """缓存统计"""

    l1_hits: int = 0
    l2_hits: int = 0
    l3_hits: int = 0
    misses: int = 0

    @property
    def total_hits(self) -> int:
        return self.l1_hits + self.l2_hits + self.l3_hits

    @property
    def hit_rate(self) -> float:
        total = self.total_hits + self.misses
        return self.total_hits / total if total > 0 else 0.0


@dataclass
class CachedResult:
    """缓存条目"""

    result: Any
    timestamp: float
    ttl: float
    level: str  # "L1" | "L2" | "L3"

    def is_expired(self) -> bool:
        return time.time() - self.timestamp > self.ttl


class RoutingCache:
    """多级路由缓存

    L1: Exact Match (context_hash) - TTL: 5分钟
    L2: Partial Match (task_type + domain) - TTL: 15分钟
    L3: User Preference (user_id) - 长期有效
    """

    # TTL 配置 (秒)
    L1_TTL = 300  # 5分钟
    L2_TTL = 900  # 15分钟
    L3_TTL = 86400 * 7  # 7天

    def __init__(self) -> None:
        self._l1_cache: dict[str, CachedResult] = {}
        self._l2_cache: dict[str, CachedResult] = {}
        self._l3_cache: dict[str, CachedResult] = {}
        self._stats = CacheStats()

    def get(self, context: RoutingContext) -> Any | None:
        """获取缓存结果

        优先级: L1 > L2 > L3
        """
        # L1: 精确匹配
        key_l1 = self._make_l1_key(context)
        if key_l1 in self._l1_cache:
            cached = self._l1_cache[key_l1]
            if not cached.is_expired():
                self._stats.l1_hits += 1
                logger.debug(f"L1 cache hit: {key_l1}")
                return cached.result
            else:
                del self._l1_cache[key_l1]

        # L2: 部分匹配 (task_type + domain)
        key_l2 = self._make_l2_key(context)
        if key_l2 in self._l2_cache:
            cached = self._l2_cache[key_l2]
            if not cached.is_expired():
                self._stats.l2_hits += 1
                logger.debug(f"L2 cache hit: {key_l2}")
                return cached.result
            else:
                del self._l2_cache[key_l2]

        # L3: 用户偏好 (user_id based)
        if context.session_id:
            key_l3 = self._make_l3_key(context)
            if key_l3 in self._l3_cache:
                cached = self._l3_cache[key_l3]
                if not cached.is_expired():
                    self._stats.l3_hits += 1
                    logger.debug(f"L3 cache hit: {key_l3}")
                    return cached.result

        self._stats.misses += 1
        return None

    def set(self, context: RoutingContext, result: Any) -> None:
        """设置缓存"""
        # L1: 精确匹配
        key_l1 = self._make_l1_key(context)
        self._l1_cache[key_l1] = CachedResult(
            result=result,
            timestamp=time.time(),
            ttl=self.L1_TTL,
            level="L1",
        )

        # L2: task_type + domain
        key_l2 = self._make_l2_key(context)
        self._l2_cache[key_l2] = CachedResult(
            result=result,
            timestamp=time.time(),
            ttl=self.L2_TTL,
            level="L2",
        )

        # L3: user preference
        if context.session_id:
            key_l3 = self._make_l3_key(context)
            self._l3_cache[key_l3] = CachedResult(
                result=result,
                timestamp=time.time(),
                ttl=self.L3_TTL,
                level="L3",
            )

        logger.debug(f"Cached result at L1/L2/L3: {key_l1[:16]}...")

    def invalidate(self, context: RoutingContext | None = None) -> None:
        """失效缓存"""
        if context is None:
            # 清除所有
            self._l1_cache.clear()
            self._l2_cache.clear()
            self._l3_cache.clear()
            logger.info("All routing cache cleared")
        else:
            key_l1 = self._make_l1_key(context)
            self._l1_cache.pop(key_l1, None)
            self._l2_cache.pop(self._make_l2_key(context), None)
            if context.session_id:
                self._l3_cache.pop(self._make_l3_key(context), None)

    def get_stats(self) -> CacheStats:
        """获取缓存统计"""
        return self._stats

    def cleanup_expired(self) -> int:
        """清理过期条目,返回清理数量"""
        count = 0

        # L1 cleanup
        expired_l1 = [k for k, v in self._l1_cache.items() if v.is_expired()]
        for k in expired_l1:
            del self._l1_cache[k]
            count += 1

        # L2 cleanup
        expired_l2 = [k for k, v in self._l2_cache.items() if v.is_expired()]
        for k in expired_l2:
            del self._l2_cache[k]
            count += 1

        # L3 cleanup
        expired_l3 = [k for k, v in self._l3_cache.items() if v.is_expired()]
        for k in expired_l3:
            del self._l3_cache[k]
            count += 1

        if count > 0:
            logger.info(f"Cleaned up {count} expired cache entries")

        return count

    def _make_l1_key(self, context: RoutingContext) -> str:
        """生成 L1 缓存键 (精确匹配)"""
        data = {
            "task_type": context.task_type,
            "domain": context.domain,
            "intent": context.intent,
            "session_phase": context.session_phase,
            "session_id": context.session_id,
        }
        return hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def _make_l2_key(self, context: RoutingContext) -> str:
        """生成 L2 缓存键 (task_type + domain)"""
        data = {
            "task_type": context.task_type,
            "domain": context.domain,
        }
        return hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def _make_l3_key(self, context: RoutingContext) -> str:
        """生成 L3 缓存键 (user preference)"""
        return f"user_pref:{context.session_id}"
