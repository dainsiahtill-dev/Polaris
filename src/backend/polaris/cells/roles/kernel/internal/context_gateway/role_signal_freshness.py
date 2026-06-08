"""跨 turn 的角色信号 freshness 记忆（会话/任务级，进程内有界缓存）。

解决"ProjectionEngine/RoleContextGateway 每 turn 新建 → 学习/记忆状态跨 turn 丢失"
的架构阻断：把"模型上一次实际看到的每个信号的 freshness_key"按 cache_key（task_id）
记住，供下一 turn 的预算熔断判断"该信号自上次注入以来是否变化"。

语义：``record`` 只记录**本 turn 实际注入**的信号 freshness（model 看到的版本）；
``get_previous`` 返回该 key 历史累计的 {signal_id: freshness_key}。未变化（current==prev）
且处于预算压力时，allocator 会断流该 nice-to-have 信号，把窗口让给即时工具结果。

有界 LRU（默认 256 个 key），避免无限增长；进程内、加锁，足够单进程 agent 会话使用。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Mapping

_MAX_KEYS = 256


class RoleSignalFreshnessCache:
    """task/session 级 freshness 记忆，有界 LRU。"""

    def __init__(self, max_keys: int = _MAX_KEYS) -> None:
        self._max_keys = max(1, max_keys)
        self._data: OrderedDict[str, dict[str, str]] = OrderedDict()
        self._lock = threading.Lock()

    def get_previous(self, cache_key: str) -> dict[str, str]:
        """返回该 key 记住的 {signal_id: freshness_key}（拷贝）；无则空 dict。"""
        if not cache_key:
            return {}
        with self._lock:
            entry = self._data.get(cache_key)
            if entry is None:
                return {}
            self._data.move_to_end(cache_key)
            return dict(entry)

    def record(self, cache_key: str, injected_freshness: Mapping[str, str]) -> None:
        """合并记录本 turn 实际注入的信号 freshness（model 看到的版本）。"""
        if not cache_key or not injected_freshness:
            return
        with self._lock:
            entry = self._data.get(cache_key)
            if entry is None:
                entry = {}
            entry.update({str(k): str(v) for k, v in injected_freshness.items() if v})
            self._data[cache_key] = entry
            self._data.move_to_end(cache_key)
            while len(self._data) > self._max_keys:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


# 进程内单例（gateway 每 turn 新建，但缓存是模块级 → 跨 turn 存活）。
_GLOBAL_CACHE = RoleSignalFreshnessCache()


def get_previous_freshness(cache_key: str) -> dict[str, str]:
    return _GLOBAL_CACHE.get_previous(cache_key)


def record_injected_freshness(cache_key: str, injected_freshness: Mapping[str, str]) -> None:
    _GLOBAL_CACHE.record(cache_key, injected_freshness)


def reset_freshness_cache() -> None:
    """测试用：清空全局缓存。"""
    _GLOBAL_CACHE.clear()
