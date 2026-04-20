"""Tests for Permission Service

单元测试：权限决策服务、RBAC 策略评估、角色权限检查。
"""

import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from polaris.cells.policy.permission.internal.permission_service import (
    PermissionService,
    get_permission_service,
    reset_permission_service,
)
from polaris.cells.roles.profile.internal.schema import (
    Action,
    PolicyEffect,
    Resource,
    ResourceType,
    Subject,
    SubjectType,
)


@pytest.fixture
def permission_service():
    """创建权限服务实例"""
    reset_permission_service()
    service = PermissionService(workspace="")
    return service


@pytest_asyncio.fixture
async def initialized_service(permission_service):
    """创建并初始化权限服务"""
    await permission_service.initialize()
    return permission_service


class TestPermissionServiceSchema:
    """测试权限模型"""

    def test_subject_creation(self):
        """测试主体创建"""
        subject = Subject(type=SubjectType.ROLE, id="pm")
        assert subject.type == SubjectType.ROLE
        assert subject.id == "pm"

    def test_resource_creation(self):
        """测试资源创建"""
        resource = Resource(
            type=ResourceType.FILE,
            pattern="**/*.py",
            path="/workspace/src/fastapi_entrypoint.py",
        )
        assert resource.type == ResourceType.FILE
        assert resource.pattern == "**/*.py"

    def test_action_creation(self):
        """测试操作创建"""
        action = Action.READ
        assert action == Action.READ
        assert action.value == "read"


class TestPermissionServiceInit:
    """测试权限服务初始化"""

    @pytest.mark.asyncio
    async def test_initialize(self, permission_service):
        """测试服务初始化"""
        await permission_service.initialize()
        assert permission_service._initialized is True

    @pytest.mark.asyncio
    async def test_builtin_policies_loaded(self, initialized_service):
        """测试内置策略加载"""
        policies = initialized_service.list_policies()
        assert len(policies) > 0

        # 验证关键策略存在
        policy_ids = [p["id"] for p in policies]
        assert "pm-read-all" in policy_ids
        assert "director-write-all" in policy_ids
        assert "director-deny-sensitive" in policy_ids


class TestPermissionCheck:
    """测试权限检查"""

    @pytest.mark.asyncio
    async def test_pm_read_permission(self, initialized_service):
        """测试 PM 角色读取权限"""
        result = await initialized_service.check_permission(
            subject=Subject(type=SubjectType.ROLE, id="pm"),
            resource=Resource(type=ResourceType.FILE, pattern="**/*.py"),
            action=Action.READ,
        )

        assert result.allowed is True
        assert result.decision == "allow"
        assert "pm-read-all" in result.matched_policies

    @pytest.mark.asyncio
    async def test_pm_write_permission_denied(self, initialized_service):
        """测试 PM 角色写入权限被拒绝"""
        result = await initialized_service.check_permission(
            subject=Subject(type=SubjectType.ROLE, id="pm"),
            resource=Resource(type=ResourceType.FILE, pattern="**/*.py"),
            action=Action.WRITE,
        )

        # PM 没有写入权限（没有匹配的 allow 策略）
        assert result.allowed is False
        assert result.decision == "deny"

    @pytest.mark.asyncio
    async def test_director_write_permission(self, initialized_service):
        """测试 Director 角色写入权限"""
        result = await initialized_service.check_permission(
            subject=Subject(type=SubjectType.ROLE, id="director"),
            resource=Resource(type=ResourceType.FILE, pattern="**/src/fastapi_entrypoint.py"),
            action=Action.WRITE,
        )

        assert result.allowed is True
        assert result.decision == "allow"
        assert "director-write-all" in result.matched_policies

    @pytest.mark.asyncio
    async def test_director_sensitive_file_denied(self, initialized_service):
        """测试 Director 访问敏感文件被拒绝"""
        result = await initialized_service.check_permission(
            subject=Subject(type=SubjectType.ROLE, id="director"),
            resource=Resource(type=ResourceType.FILE, pattern="**/.env"),
            action=Action.WRITE,
        )

        # 敏感文件被高优先级 deny 策略覆盖
        assert result.allowed is False
        assert result.decision == "deny"
        assert "director-deny-sensitive" in result.matched_policies

    @pytest.mark.asyncio
    async def test_unknown_role_default_deny(self, initialized_service):
        """测试未知角色默认拒绝"""
        result = await initialized_service.check_permission(
            subject=Subject(type=SubjectType.ROLE, id="unknown_role"),
            resource=Resource(type=ResourceType.FILE, pattern="**/*"),
            action=Action.READ,
        )

        assert result.allowed is False
        assert result.decision == "deny"


class TestRoleManagement:
    """测试角色管理"""

    @pytest.mark.asyncio
    async def test_list_roles(self, initialized_service):
        """测试列出角色"""
        roles = await initialized_service.list_roles()

        assert len(roles) >= 5  # 至少 5 个内置角色

        role_ids = [r["id"] for r in roles]
        assert "pm" in role_ids
        assert "director" in role_ids
        assert "qa" in role_ids

    @pytest.mark.asyncio
    async def test_assign_role(self, initialized_service):
        """测试分配角色"""
        result = await initialized_service.assign_role(
            subject_type=SubjectType.USER,
            subject_id="user-123",
            role_id="pm",
        )

        assert result["assigned"] is True
        assert result["role_id"] == "pm"

    @pytest.mark.asyncio
    async def test_assign_invalid_role(self, initialized_service):
        """测试分配无效角色"""
        from polaris.cells.policy.permission.internal.permission_service import PermissionServiceError

        with pytest.raises(PermissionServiceError):
            await initialized_service.assign_role(
                subject_type=SubjectType.USER,
                subject_id="user-123",
                role_id="nonexistent_role",
            )


class TestEffectivePermissions:
    """测试有效权限查询"""

    @pytest.mark.asyncio
    async def test_get_effective_permissions_pm(self, initialized_service):
        """测试获取 PM 有效权限"""
        permissions = await initialized_service.get_effective_permissions(
            subject=Subject(type=SubjectType.ROLE, id="pm")
        )

        assert isinstance(permissions, list)
        # PM 应该有一些工具权限
        assert len(permissions) > 0

    @pytest.mark.asyncio
    async def test_get_effective_permissions_director(self, initialized_service):
        """测试获取 Director 有效权限"""
        permissions = await initialized_service.get_effective_permissions(
            subject=Subject(type=SubjectType.ROLE, id="director")
        )

        assert isinstance(permissions, list)
        # Director 应该有更多权限
        assert len(permissions) > 0


class TestPolicyDetails:
    """测试策略详情"""

    @pytest.mark.asyncio
    async def test_get_policy(self, initialized_service):
        """测试获取策略详情"""
        policy = initialized_service.get_policy("director-write-all")

        assert policy is not None
        assert policy.id == "director-write-all"
        assert policy.effect == PolicyEffect.ALLOW
        assert policy.priority == 50


class TestPermissionServiceSingleton:
    """测试单例模式"""

    @pytest.mark.asyncio
    async def test_get_service_singleton(self):
        """测试获取服务单例"""
        reset_permission_service()

        service1 = await get_permission_service(workspace="")
        service2 = await get_permission_service(workspace="")

        # 应该是同一个实例
        assert service1 is service2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
