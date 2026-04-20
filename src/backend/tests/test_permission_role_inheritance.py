"""Tests for Permission Role Graph

单元测试：角色继承图的展开、循环检测和层次结构查询。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from polaris.cells.policy.permission.internal.role_graph import PermissionRoleGraph


@pytest.fixture
def default_graph():
    """创建默认角色图"""
    return PermissionRoleGraph()


@pytest.fixture
def custom_graph():
    """创建自定义角色图"""
    edges = {
        "super_admin": ["admin", "manager", "user"],
        "admin": ["manager", "user"],
        "manager": ["user"],
        "user": [],
        "guest": [],
    }
    return PermissionRoleGraph(edges)


class TestRoleGraphInitialization:
    """测试角色图初始化"""

    def test_default_edges(self, default_graph):
        """测试默认边关系"""
        assert "admin" in default_graph.edges
        assert "manager" in default_graph.edges
        assert "developer" in default_graph.edges
        assert "viewer" in default_graph.edges

    def test_custom_edges(self, custom_graph):
        """测试自定义边关系"""
        assert "super_admin" in custom_graph.edges
        assert custom_graph.edges["super_admin"] == ["admin", "manager", "user"]


class TestRoleExpansion:
    """测试角色展开

    默认角色层次结构（parent -> children it includes）：
    - admin -> manager, developer, viewer
    - manager -> developer, viewer
    - developer -> viewer
    - viewer -> (none)

    展开方向：子角色向上查找所有父角色
    - viewer 展开 -> viewer, developer, manager, admin
    - developer 展开 -> developer, manager, admin
    - manager 展开 -> manager, admin
    - admin 展开 -> admin
    """

    def test_expand_viewer(self, default_graph):
        """测试 viewer 角色展开"""
        result = default_graph.expand_roles("viewer")

        # viewer 是最底层角色，展开后包含所有父角色
        assert "viewer" in result
        assert "developer" in result
        assert "manager" in result
        assert "admin" in result

    def test_expand_developer(self, default_graph):
        """测试 developer 角色展开"""
        result = default_graph.expand_roles("developer")

        # developer 展开包含 manager 和 admin
        assert "developer" in result
        assert "manager" in result
        assert "admin" in result
        assert "viewer" not in result  # viewer 是子角色，不是父角色

    def test_expand_manager(self, default_graph):
        """测试 manager 角色展开"""
        result = default_graph.expand_roles("manager")

        # manager 展开只包含 admin
        assert "manager" in result
        assert "admin" in result
        assert "developer" not in result  # developer 是兄弟角色
        assert "viewer" not in result  # viewer 是孙角色

    def test_expand_admin(self, default_graph):
        """测试 admin 角色展开"""
        result = default_graph.expand_roles("admin")

        # admin 是顶层角色，展开后只包含自己
        assert result == {"admin"}

    def test_expand_unknown_role(self, default_graph):
        """测试未知角色展开"""
        result = default_graph.expand_roles("unknown")

        # 未知角色只包含自己
        assert result == {"unknown"}

    def test_expand_custom_graph(self, custom_graph):
        """测试自定义图角色展开"""
        result = custom_graph.expand_roles("user")

        # user 是最底层，展开后包含所有父角色
        assert "user" in result
        assert "manager" in result
        assert "admin" in result
        assert "super_admin" in result


class TestImmediateParents:
    """测试直接父角色查询"""

    def test_viewer_parents(self, default_graph):
        """测试 viewer 的父角色"""
        parents = default_graph.get_immediate_parents("viewer")

        # viewer 被 developer, manager, admin 包含
        assert "developer" in parents
        assert "manager" in parents
        assert "admin" in parents

    def test_developer_parents(self, default_graph):
        """测试 developer 的父角色"""
        parents = default_graph.get_immediate_parents("developer")

        assert "manager" in parents
        assert "admin" in parents
        assert "viewer" not in parents  # viewer 是子角色

    def test_admin_parents(self, default_graph):
        """测试 admin 的父角色"""
        parents = default_graph.get_immediate_parents("admin")

        # admin 是顶层角色，没有父角色
        assert parents == []


class TestImmediateChildren:
    """测试直接子角色查询"""

    def test_admin_children(self, default_graph):
        """测试 admin 的子角色"""
        children = default_graph.get_immediate_children("admin")

        assert "manager" in children
        assert "developer" in children
        assert "viewer" in children

    def test_manager_children(self, default_graph):
        """测试 manager 的子角色"""
        children = default_graph.get_immediate_children("manager")

        assert "developer" in children
        assert "viewer" in children
        assert "admin" not in children

    def test_viewer_children(self, default_graph):
        """测试 viewer 的子角色"""
        children = default_graph.get_immediate_children("viewer")

        # viewer 是最底层，没有子角色
        assert children == []


class TestCycleDetection:
    """测试循环检测"""

    def test_default_graph_no_cycle(self, default_graph):
        """测试默认图无循环"""
        assert default_graph.has_cycle() is False

    def test_custom_graph_no_cycle(self, custom_graph):
        """测试自定义图无循环"""
        assert custom_graph.has_cycle() is False

    def test_simple_cycle(self):
        """测试简单循环检测"""
        # A -> B -> A (循环)
        edges = {
            "A": ["B"],
            "B": ["A"],
        }
        graph = PermissionRoleGraph(edges)

        assert graph.has_cycle() is True

    def test_indirect_cycle(self):
        """测试间接循环检测"""
        # A -> B -> C -> A (循环)
        edges = {
            "A": ["B"],
            "B": ["C"],
            "C": ["A"],
        }
        graph = PermissionRoleGraph(edges)

        assert graph.has_cycle() is True

    def test_self_loop(self):
        """测试自循环检测"""
        edges = {
            "A": ["A"],
        }
        graph = PermissionRoleGraph(edges)

        assert graph.has_cycle() is True


class TestAllRoles:
    """测试获取所有角色"""

    def test_default_all_roles(self, default_graph):
        """测试获取默认所有角色"""
        roles = default_graph.get_all_roles()

        assert "admin" in roles
        assert "manager" in roles
        assert "developer" in roles
        assert "viewer" in roles

    def test_custom_all_roles(self, custom_graph):
        """测试获取自定义所有角色"""
        roles = custom_graph.get_all_roles()

        assert "super_admin" in roles
        assert "admin" in roles
        assert "manager" in roles
        assert "user" in roles
        assert "guest" in roles


class TestRoleHierarchy:
    """测试角色层次结构查询"""

    def test_viewer_hierarchy(self, default_graph):
        """测试 viewer 层次结构"""
        hierarchy = default_graph.get_role_hierarchy("viewer")

        assert hierarchy["role"] == "viewer"
        assert "developer" in hierarchy["inherits_from"]
        assert hierarchy["includes"] == []
        assert "viewer" in hierarchy["expanded"]
        assert "developer" in hierarchy["expanded"]

    def test_admin_hierarchy(self, default_graph):
        """测试 admin 层次结构"""
        hierarchy = default_graph.get_role_hierarchy("admin")

        assert hierarchy["role"] == "admin"
        assert hierarchy["inherits_from"] == []
        assert "manager" in hierarchy["includes"]
        assert "developer" in hierarchy["includes"]
        assert "viewer" in hierarchy["includes"]
        assert hierarchy["expanded"] == ["admin"]  # admin 展开只包含自己


class TestRoleModification:
    """测试角色修改"""

    def test_add_role(self, default_graph):
        """测试添加角色"""
        default_graph.add_role("super_user", ["admin"])

        assert "super_user" in default_graph.edges
        assert default_graph.edges["super_user"] == ["admin"]

    def test_add_role_without_includes(self, default_graph):
        """测试添加无包含角色的角色"""
        default_graph.add_role("observer")

        assert "observer" in default_graph.edges
        assert default_graph.edges["observer"] == []

    def test_remove_role(self, default_graph):
        """测试移除角色"""
        default_graph.remove_role("manager")

        assert "manager" not in default_graph.edges

    def test_remove_nonexistent_role(self, default_graph):
        """测试移除不存在的角色"""
        # 不应抛出异常
        default_graph.remove_role("nonexistent")


class TestEdgeCases:
    """测试边界情况"""

    def test_empty_graph(self):
        """测试空图"""
        graph = PermissionRoleGraph({})

        # 空图没有预定义角色，但 expand_roles 仍返回传入的角色
        assert graph.get_all_roles() == set()
        assert graph.has_cycle() is False
        assert graph.expand_roles("any") == {"any"}

    def test_single_role(self):
        """测试单角色图"""
        graph = PermissionRoleGraph({"solo": []})

        assert graph.expand_roles("solo") == {"solo"}
        assert graph.has_cycle() is False

    def test_diamond_inheritance(self):
        """测试菱形继承"""
        #     A
        #    / \
        #   B   C
        #    \ /
        #     D
        edges = {
            "A": ["B", "C"],
            "B": ["D"],
            "C": ["D"],
            "D": [],
        }
        graph = PermissionRoleGraph(edges)

        expanded = graph.expand_roles("D")
        assert "A" in expanded
        assert "B" in expanded
        assert "C" in expanded
        assert "D" in expanded
        assert graph.has_cycle() is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
