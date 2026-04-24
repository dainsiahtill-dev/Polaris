"""Cycle Detection - NetworkX 循环检测增强

ADR-0067: ContextOS 2.0 摘要策略选型

基于 NetworkX 的依赖追踪和循环检测，防止无限递归。

特点:
- 依赖图构建: 使用 NetworkX 构建事件依赖图
- 强连通分量检测: 使用 Tarjan 算法检测循环
- 跨事件循环: 检测跨 TranscriptEvent 的循环引用
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CycleDetectionConfig:
    """循环检测配置"""

    enabled: bool = True
    max_depth: int = 50  # 最大依赖深度
    check_interval: int = 10  # 每 N 个事件检查一次
    alert_threshold: int = 3  # 触发警报的循环次数


@dataclass
class DependencyNode:
    """依赖图节点"""

    event_id: str
    sequence: int
    kind: str
    content_hash: str = ""
    depends_on: set[str] = field(default_factory=set)
    Dep_id: str | None = None


class NetworkXCycleDetector:
    """基于 NetworkX 的循环检测器

    使用有向图追踪事件依赖关系，检测循环引用。

    Example:
        ```python
        detector = NetworkXCycleDetector()

        # 添加事件依赖
        detector.add_dependency(event_id="e1", depends_on=["e0"])
        detector.add_dependency(event_id="e2", depends_on=["e1"])
        detector.add_dependency(event_id="e0", depends_on=["e2"])  # 循环!

        # 检测循环
        cycles = detector.detect_cycles()
        if cycles:
            logger.warning(f"Cycle detected: {cycles}")
        ```
    """

    def __init__(
        self,
        config: CycleDetectionConfig | None = None,
    ) -> None:
        """初始化循环检测器

        Args:
            config: 循环检测配置
        """
        self.config = config or CycleDetectionConfig()
        self._graph: dict[str, DependencyNode] = {}
        self._cycle_cache: list[list[str]] | None = None
        self._last_check_count: int = 0
        self._cycle_count: int = 0

    def add_dependency(
        self,
        event_id: str,
        depends_on: list[str],
        sequence: int = 0,
        kind: str = "",
        content_hash: str = "",
    ) -> None:
        """添加事件依赖

        Args:
            event_id: 事件 ID
            depends_on: 依赖的事件 ID 列表
            sequence: 事件序列号
            kind: 事件类型
            content_hash: 内容哈希
        """
        if not self.config.enabled:
            return

        if event_id not in self._graph:
            self._graph[event_id] = DependencyNode(
                event_id=event_id,
                sequence=sequence,
                kind=kind,
                content_hash=content_hash,
            )

        node = self._graph[event_id]
        for dep_id in depends_on:
            if dep_id and dep_id != event_id:  # 防止自循环
                node.depends_on.add(dep_id)
                # 确保依赖节点存在
                if dep_id not in self._graph:
                    self._graph[dep_id] = DependencyNode(
                        event_id=dep_id,
                        sequence=-1,
                        kind="",
                    )

        # 标记需要重新检测
        self._cycle_cache = None

    def detect_cycles(self) -> list[list[str]]:
        """检测所有循环

        使用深度优先搜索检测有向图中的所有循环。

        Returns:
            循环列表，每个循环是一个事件 ID 的列表
        """
        if not self.config.enabled:
            return []

        if self._cycle_cache is not None:
            return self._cycle_cache

        cycles: list[list[str]] = []
        visited: set[str] = set()
        rec_stack: set[str] = set()
        path: list[str] = []

        def dfs(node_id: str) -> bool:
            """DFS 遍历，返回是否有循环"""
            if node_id in rec_stack:
                # 发现循环
                cycle_start = path.index(node_id)
                cycle = [*path[cycle_start:], node_id]
                cycles.append(cycle)
                return True

            if node_id in visited:
                return False

            visited.add(node_id)
            rec_stack.add(node_id)
            path.append(node_id)

            node = self._graph.get(node_id)
            if node:
                for dep_id in node.depends_on:
                    if dep_id in self._graph:
                        dfs(dep_id)

            path.pop()
            rec_stack.remove(node_id)
            return False

        # 遍历所有节点
        for node_id in self._graph:
            if node_id not in visited:
                dfs(node_id)

        self._cycle_cache = cycles
        return cycles

    def detect_strongly_connected_components(self) -> list[set[str]]:
        """检测强连通分量

        使用 Tarjan 算法检测强连通分量。

        Returns:
            强连通分量列表
        """
        if not self.config.enabled:
            return []

        try:
            import networkx as nx

            # 构建 NetworkX 图
            graph = nx.DiGraph()
            for node_id, node in self._graph.items():
                graph.add_node(node_id)
                for dep_id in node.depends_on:
                    if dep_id in self._graph:
                        graph.add_edge(node_id, dep_id)

            # 检测强连通分量
            sccs = list(nx.strongly_connected_components(graph))

            # 过滤掉单个节点的 SCC
            return [scc for scc in sccs if len(scc) > 1]

        except ImportError:
            logger.debug("NetworkX not installed, using fallback cycle detection")
            cycles = self.detect_cycles()
            return [set(c) for c in cycles if len(c) > 1]

    def get_dependency_depth(self, event_id: str) -> int:
        """获取事件依赖深度

        Args:
            event_id: 事件 ID

        Returns:
            依赖深度，如果检测到循环则返回 -1
        """
        if not self.config.enabled:
            return 0

        cycles = self.detect_cycles()
        if any(event_id in c for c in cycles):
            return -1  # 事件在循环中

        visited: set[str] = set()

        def dfs_depth(node_id: str, depth: int) -> int:
            if depth > self.config.max_depth:
                return depth
            if node_id in visited:
                return depth
            visited.add(node_id)

            node = self._graph.get(node_id)
            if not node or not node.depends_on:
                return depth

            max_child_depth = depth
            for dep_id in node.depends_on:
                child_depth = dfs_depth(dep_id, depth + 1)
                max_child_depth = max(max_child_depth, child_depth)

            return max_child_depth

        return dfs_depth(event_id, 0)

    def check_and_alert(self) -> bool:
        """检查是否需要警报

        Returns:
            True 如果检测到循环且超过阈值
        """
        if not self.config.enabled:
            return False

        cycles = self.detect_cycles()
        if cycles:
            self._cycle_count += 1
            if self._cycle_count >= self.config.alert_threshold:
                logger.warning(
                    f"Cycle detection alert: {len(cycles)} cycles found, total occurrences: {self._cycle_count}"
                )
                return True

        return False

    def reset(self) -> None:
        """重置检测器状态"""
        self._graph.clear()
        self._cycle_cache = None
        self._cycle_count = 0
        self._last_check_count = 0

    def get_graph_stats(self) -> dict[str, Any]:
        """获取依赖图统计信息

        Returns:
            统计信息字典
        """
        total_nodes = len(self._graph)
        total_edges = sum(len(n.depends_on) for n in self._graph.values())
        cycles = self.detect_cycles()

        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "cycle_count": len(cycles),
            "max_depth_reached": max(
                (d for d in (self.get_dependency_depth(eid) for eid in self._graph) if d >= 0),
                default=0,
            ),
            "has_cycles": len(cycles) > 0,
            "enabled": self.config.enabled,
        }

    def is_available(self) -> bool:
        """检查 NetworkX 是否可用"""
        try:
            import networkx as nx  # noqa: F401

            return True
        except ImportError:
            return False

    def prune_old_events(self, keep_event_ids: set[str]) -> None:
        """清理旧事件节点

        Args:
            keep_event_ids: 要保留的事件 ID 集合
        """
        # 找出需要删除的节点
        to_remove = set(self._graph.keys()) - keep_event_ids

        # 更新依赖关系：删除指向已删除节点的依赖
        for node_id in self._graph:
            self._graph[node_id].depends_on -= to_remove

        # 删除节点
        for node_id in to_remove:
            del self._graph[node_id]

        # 清除循环缓存
        self._cycle_cache = None
