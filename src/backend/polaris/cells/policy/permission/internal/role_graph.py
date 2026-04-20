"""Permission Role Graph - Handles role inheritance and expansion"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


class PermissionRoleGraph:
    """权限角色图

    管理角色继承关系，支持角色展开和循环检测。

    默认角色层次结构：
    - admin -> manager, developer, viewer
    - manager -> developer, viewer
    - developer -> viewer
    - viewer -> (none)
    """

    # Default role hierarchy (parent -> children it includes)
    DEFAULT_EDGES: dict[str, list[str]] = {
        "admin": ["manager", "developer", "viewer"],
        "manager": ["developer", "viewer"],
        "developer": ["viewer"],
        "viewer": [],
    }

    def __init__(self, edges: dict[str, list[str]] | None = None) -> None:
        """初始化角色图

        Args:
            edges: 自定义角色边关系，默认为 DEFAULT_EDGES
                      传入空字典 {} 会创建真正的空图
        """
        self.edges = self.DEFAULT_EDGES.copy() if edges is None else edges
        self._reverse_edges = self._build_reverse_edges()

    def _build_reverse_edges(self) -> dict[str, list[str]]:
        """构建反向边映射（child -> parents）

        用于快速查找角色的所有父角色（即包含该角色的角色）。
        """
        reverse: dict[str, list[str]] = defaultdict(list)
        for parent, children in self.edges.items():
            for child in children:
                reverse[child].append(parent)
        return dict(reverse)

    def expand_roles(self, role: str) -> set[str]:
        """展开角色继承

        将角色展开为包含该角色及其所有继承角色的集合。
        例如：developer -> {developer, manager, admin}

        Args:
            role: 起始角色

        Returns:
            Set[str]: 展开后的所有角色集合
        """
        visited: set[str] = set()
        stack = [role]

        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)

            # Add all parents (roles that include this role)
            for parent in self._reverse_edges.get(current, []):
                stack.append(parent)

        return visited

    def get_immediate_parents(self, role: str) -> list[str]:
        """获取直接父角色

        返回直接包含该角色的角色列表。
        """
        return self._reverse_edges.get(role, [])

    def get_immediate_children(self, role: str) -> list[str]:
        """获取直接子角色

        返回该角色直接包含的角色列表。
        """
        return self.edges.get(role, [])

    def has_cycle(self) -> bool:
        """检测角色图中是否存在循环

        使用 DFS 三色标记法检测循环。

        Returns:
            bool: 如果存在循环返回 True，否则返回 False
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        all_roles = set(self.edges.keys()) | set(self._reverse_edges.keys())
        color = dict.fromkeys(all_roles, WHITE)

        def dfs(node: str) -> bool:
            color[node] = GRAY

            for parent in self._reverse_edges.get(node, []):
                if color[parent] == GRAY:
                    return True  # Back edge found - cycle
                if color[parent] == WHITE and dfs(parent):
                    return True

            color[node] = BLACK
            return False

        return any(color[role] == WHITE and dfs(role) for role in color)

    def get_all_roles(self) -> set[str]:
        """获取图中的所有角色"""
        return set(self.edges.keys()) | set(self._reverse_edges.keys())

    def add_role(self, role: str, includes: list[str] | None = None) -> None:
        """添加新角色

        Args:
            role: 角色标识
            includes: 该角色包含的子角色列表
        """
        if role not in self.edges:
            self.edges[role] = includes or []
            self._reverse_edges = self._build_reverse_edges()

    def remove_role(self, role: str) -> None:
        """移除角色

        Args:
            role: 要移除的角色标识
        """
        if role in self.edges:
            del self.edges[role]
            self._reverse_edges = self._build_reverse_edges()

    def get_role_hierarchy(self, role: str) -> dict[str, Any]:
        """获取角色的完整层次结构

        Returns:
            Dict containing:
            - role: 当前角色
            - inherits_from: 继承自哪些角色（父角色）
            - includes: 包含哪些角色（子角色）
            - expanded: 展开后的所有角色
        """
        return {
            "role": role,
            "inherits_from": self.get_immediate_parents(role),
            "includes": self.get_immediate_children(role),
            "expanded": list(self.expand_roles(role)),
        }
