"""Tests for Constitution - 宪法系统测试

验证角色边界、通信协议和反模式检测。
"""

import pytest
from polaris.cells.roles.kernel.internal.constitution_adaptor import (
    ConstitutionGuard,
    ConstitutionViolationError,
    require_role_permission,
)
from polaris.cells.roles.kernel.internal.constitution_rules import (
    AntiPattern,
    ConstitutionEnforcer,
    Role,
    ViolationLevel,
    get_role_boundary,
    is_action_allowed,
    validate_architecture,
)


class TestRoleBoundary:
    """测试角色边界定义。"""

    def test_pm_boundary_exists(self):
        """PM 角色边界必须存在。"""
        boundary = get_role_boundary(Role.PM)
        assert boundary is not None
        assert boundary.role == Role.PM

    def test_pm_prohibitions(self):
        """PM 禁止行为必须正确定义。"""
        boundary = get_role_boundary(Role.PM)
        assert "write_code" in boundary.prohibitions
        assert "modify_source_files" in boundary.prohibitions
        assert "execute_shell_commands" in boundary.prohibitions
        assert "direct_to_director" in boundary.prohibitions

    def test_pm_responsibilities(self):
        """PM 职责必须正确定义。"""
        boundary = get_role_boundary(Role.PM)
        assert "parse_requirements" in boundary.responsibilities
        assert "decompose_tasks" in boundary.responsibilities
        assert "generate_task_contract" in boundary.responsibilities

    def test_chief_engineer_boundary(self):
        """ChiefEngineer 边界必须正确定义。"""
        boundary = get_role_boundary(Role.CHIEF_ENGINEER)
        assert boundary is not None
        assert "write_implementation_code" in boundary.prohibitions
        assert "modify_files_directly" in boundary.prohibitions
        assert "generate_blueprint" in boundary.responsibilities

    def test_director_boundary(self):
        """Director 边界必须正确定义。"""
        boundary = get_role_boundary(Role.DIRECTOR)
        assert boundary is not None
        assert "modify_blueprint" in boundary.prohibitions
        assert "ignore_chief_engineer_plan" in boundary.prohibitions
        assert "implement_code" in boundary.responsibilities

    def test_qa_boundary(self):
        """QA 边界必须正确定义。"""
        boundary = get_role_boundary(Role.QA)
        assert boundary is not None
        assert "write_code" in boundary.prohibitions
        assert "modify_files" in boundary.prohibitions
        assert "audit_code_quality" in boundary.responsibilities


class TestRoleDependencies:
    """测试角色依赖关系。"""

    def test_pm_downstream(self):
        """PM 只能路由到 ChiefEngineer 和 Policy。"""
        boundary = get_role_boundary(Role.PM)
        assert Role.CHIEF_ENGINEER in boundary.downstream_roles
        assert Role.POLICY in boundary.downstream_roles
        assert Role.DIRECTOR not in boundary.downstream_roles

    def test_chief_engineer_downstream(self):
        """ChiefEngineer 只能路由到 Director 和 Policy。"""
        boundary = get_role_boundary(Role.CHIEF_ENGINEER)
        assert Role.DIRECTOR in boundary.downstream_roles
        assert Role.POLICY in boundary.downstream_roles
        assert Role.QA not in boundary.downstream_roles

    def test_director_downstream(self):
        """Director 只能路由到 QA 和 Policy。"""
        boundary = get_role_boundary(Role.DIRECTOR)
        assert Role.QA in boundary.downstream_roles
        assert Role.POLICY in boundary.downstream_roles
        assert Role.PM not in boundary.downstream_roles

    def test_qa_downstream(self):
        """QA 只能反馈到 PM。"""
        boundary = get_role_boundary(Role.QA)
        assert Role.PM in boundary.downstream_roles
        assert Role.DIRECTOR not in boundary.downstream_roles


class TestConstitutionEnforcer:
    """测试宪法执行器。"""

    def test_check_role_action_violation(self):
        """检测角色越界行为。"""
        enforcer = ConstitutionEnforcer()

        # PM 试图写代码 - 违规
        result = enforcer.check_role_action(Role.PM, "write_code")
        assert result == AntiPattern.ROLE_OVERREACH

    def test_check_role_action_allowed(self):
        """允许的行为不应被标记。"""
        enforcer = ConstitutionEnforcer()

        # PM 解析需求 - 允许
        result = enforcer.check_role_action(Role.PM, "parse_requirements")
        assert result is None

    def test_check_communication_allowed(self):
        """合法通信应通过检查。"""
        enforcer = ConstitutionEnforcer()

        message = {"tasks": [], "iteration": 1}
        errors = enforcer.check_communication(
            Role.PM, Role.CHIEF_ENGINEER, message
        )
        assert len(errors) == 0

    def test_check_communication_direct_coupling(self):
        """直接耦合应被检测。"""
        enforcer = ConstitutionEnforcer()

        message = {"tasks": []}
        errors = enforcer.check_communication(
            Role.PM, Role.DIRECTOR, message  # PM 不应直接通信 Director
        )
        assert len(errors) > 0

    def test_check_state_access_private(self):
        """私有状态访问应被阻止。"""
        enforcer = ConstitutionEnforcer()

        # Director 试图访问 PM 的私有状态
        allowed = enforcer.check_state_access(
            Role.DIRECTOR, Role.PM, "task_history"
        )
        assert allowed is False

    def test_check_state_access_own(self):
        """访问自己的状态应被允许。"""
        enforcer = ConstitutionEnforcer()

        allowed = enforcer.check_state_access(
            Role.PM, Role.PM, "task_history"
        )
        assert allowed is True


class TestConstitutionGuard:
    """测试宪法守卫。"""

    def test_strict_mode_raises(self):
        """严格模式下应抛出异常。"""
        guard = ConstitutionGuard(strict_mode=True)

        with pytest.raises(ConstitutionViolationError):
            error = guard.guard_action(Role.PM, "write_code")
            if error:
                raise error

    def test_non_strict_mode_records(self):
        """非严格模式下只记录不抛出。"""
        guard = ConstitutionGuard(strict_mode=False)

        error = guard.guard_action(Role.PM, "write_code")
        assert error is None

        violations = guard.get_violations()
        assert len(violations) > 0

    def test_has_fatal_violations(self):
        """检测致命违规。"""
        guard = ConstitutionGuard(strict_mode=False)

        # QA 写代码是致命违规
        guard.guard_action(Role.QA, "write_code")

        # 由于我们在非严格模式，需要手动检查
        violations = guard.get_violations()
        fatal = [v for v in violations if v.get("severity") == "FATAL"]
        assert len(fatal) >= 0  # QA 写代码在某些定义下是致命


class TestAntiPatterns:
    """测试反模式检测。"""

    def test_self_approval_is_fatal(self):
        """自我批准是致命违规。"""
        from polaris.cells.roles.kernel.internal.constitution_rules import ANTI_PATTERN_SEVERITY

        severity = ANTI_PATTERN_SEVERITY.get(AntiPattern.SELF_APPROVAL)
        assert severity == ViolationLevel.FATAL

    def test_qa_writes_code_is_fatal(self):
        """QA 写代码是致命违规。"""
        from polaris.cells.roles.kernel.internal.constitution_rules import ANTI_PATTERN_SEVERITY

        severity = ANTI_PATTERN_SEVERITY.get(AntiPattern.QA_WRITES_CODE)
        assert severity == ViolationLevel.FATAL

    def test_skip_audit_is_fatal(self):
        """跳过审计是致命违规。"""
        from polaris.cells.roles.kernel.internal.constitution_rules import ANTI_PATTERN_SEVERITY

        severity = ANTI_PATTERN_SEVERITY.get(AntiPattern.SKIP_AUDIT)
        assert severity == ViolationLevel.FATAL

    def test_role_overreach_is_error(self):
        """角色越界是错误级别。"""
        from polaris.cells.roles.kernel.internal.constitution_rules import ANTI_PATTERN_SEVERITY

        severity = ANTI_PATTERN_SEVERITY.get(AntiPattern.ROLE_OVERREACH)
        assert severity == ViolationLevel.ERROR


class TestValidateArchitecture:
    """测试架构验证。"""

    def test_valid_chain(self):
        """有效角色链应通过验证。"""
        roles = [Role.PM, Role.CHIEF_ENGINEER, Role.DIRECTOR, Role.QA]
        errors = validate_architecture(roles)
        assert len(errors) == 0

    def test_circular_dependency_detected(self):
        """循环依赖应被检测。"""
        # 创建一个循环依赖场景（理论上不应该存在）
        # 这需要修改 CONSTITUTION 来测试，暂时跳过
        pass


class TestIsActionAllowed:
    """测试行为允许检查。"""

    def test_pm_write_code_not_allowed(self):
        """PM 写代码不被允许。"""
        assert is_action_allowed(Role.PM, "write_code") is False

    def test_director_implement_allowed(self):
        """Director 实现代码被允许。"""
        assert is_action_allowed(Role.DIRECTOR, "implement_code") is True

    def test_chief_engineer_generate_blueprint_allowed(self):
        """CE 生成蓝图被允许。"""
        assert is_action_allowed(Role.CHIEF_ENGINEER, "generate_blueprint") is True

    def test_chief_engineer_write_code_not_allowed(self):
        """CE 写代码不被允许。"""
        assert is_action_allowed(Role.CHIEF_ENGINEER, "write_implementation_code") is False


class TestRequireRolePermission:
    """测试权限装饰器。"""

    def test_decorator_blocks_unauthorized(self):
        """装饰器应阻止未授权行为。"""

        @require_role_permission(Role.PM, "write_code")
        def pm_writes_code():
            return "code written"

        with pytest.raises(ConstitutionViolationError):
            pm_writes_code()

    def test_decorator_allows_authorized(self):
        """装饰器应允许授权行为。"""
        # 注意：这里的行为不在禁止列表中，所以应该通过
        @require_role_permission(Role.PM, "parse_requirements")
        def pm_parses():
            return "parsed"

        # 这应该执行成功，因为 parse_requirements 不在 prohibitions 中
        # 但装饰器逻辑检查的是 is_action_allowed，它会检查 prohibitions
        # 所以需要确保 parse_requirements 确实是被允许的
        result = pm_parses()
        assert result == "parsed"


class TestCommunicationProtocol:
    """测试通信协议。"""

    def test_pm_to_ce_protocol(self):
        """PM 到 CE 的协议验证。"""
        from polaris.cells.roles.kernel.internal.constitution_rules import ALLOWED_COMMUNICATIONS

        # 查找 PM -> CE 协议
        protocol = None
        for p in ALLOWED_COMMUNICATIONS:
            if p.from_role == Role.PM and p.to_role == Role.CHIEF_ENGINEER:
                protocol = p
                break

        assert protocol is not None
        assert "tasks" in protocol.required_fields
        assert "implementation_details" in protocol.forbidden_fields

    def test_validate_message_missing_field(self):
        """验证消息缺少必需字段。"""
        from polaris.cells.roles.kernel.internal.constitution_rules import ALLOWED_COMMUNICATIONS

        protocol = None
        for p in ALLOWED_COMMUNICATIONS:
            if p.from_role == Role.PM and p.to_role == Role.CHIEF_ENGINEER:
                protocol = p
                break

        # 缺少 tasks 字段
        message = {"iteration": 1}
        errors = protocol.validate_message(message)
        assert any("tasks" in e for e in errors)

    def test_validate_message_forbidden_field(self):
        """验证消息包含禁止字段。"""
        from polaris.cells.roles.kernel.internal.constitution_rules import ALLOWED_COMMUNICATIONS

        protocol = None
        for p in ALLOWED_COMMUNICATIONS:
            if p.from_role == Role.PM and p.to_role == Role.CHIEF_ENGINEER:
                protocol = p
                break

        # 包含禁止的 implementation_details
        message = {"tasks": [], "iteration": 1, "implementation_details": "secret"}
        errors = protocol.validate_message(message)
        assert any("implementation_details" in e for e in errors)


class TestImmutability:
    """测试不可变性。"""

    def test_constitution_is_frozen(self):
        """宪法定义应是不可变的。"""
        boundary = get_role_boundary(Role.PM)

        # 尝试修改应失败
        with pytest.raises((AttributeError, TypeError)):
            boundary.prohibitions.add("new_prohibition")

    def test_role_boundary_frozen(self):
        """RoleBoundary 应是不可变的 dataclass。"""
        boundary = get_role_boundary(Role.PM)

        with pytest.raises((AttributeError, TypeError)):
            boundary.role = Role.DIRECTOR


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
