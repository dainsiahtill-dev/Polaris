"""Constitution Integration - 宪法集成层

将宪法检查嵌入到现有角色执行流程中。
所有角色必须通过此层进行行为验证。
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any, TypeVar

from polaris.cells.roles.kernel.internal.constitution_rules import (
    AntiPattern,
    ConstitutionEnforcer,
    Role,
    is_action_allowed,
)
from polaris.kernelone.errors import ConstitutionViolationError as _BaseConstitutionViolationError

if TYPE_CHECKING:
    from collections.abc import Callable

T = TypeVar("T")


class ConstitutionViolationError(_BaseConstitutionViolationError):
    """宪法违规错误 (继承自 KernelOne ValidationError)。"""

    def __init__(
        self,
        role: Role,
        action: str,
        anti_pattern: AntiPattern,
        detail: str = "",
    ) -> None:
        self.role = role
        self.action = action
        self.anti_pattern = anti_pattern
        self.severity = type(anti_pattern).__name__
        message = f"Constitution Violation [{anti_pattern.value}] by {role.value}: {action}. {detail}"
        super().__init__(
            message,
            rule_name=f"constitution_{anti_pattern.value}",
            violation_type=type(anti_pattern).__name__,
        )


class ConstitutionGuard:
    """宪法守卫 - 为角色执行提供运行时保护。"""

    def __init__(self, strict_mode: bool = True) -> None:
        self.enforcer = ConstitutionEnforcer()
        self.strict_mode = strict_mode
        self._violations: list[dict[str, Any]] = []

    def guard_action(self, role: Role, action: str) -> ConstitutionViolationError | None:
        """守卫角色行为。

        Returns:
            如果违规且严格模式下，返回错误；否则返回 None
        """
        anti_pattern = self.enforcer.check_role_action(role, action)

        if anti_pattern:
            error = ConstitutionViolationError(
                role=role,
                action=action,
                anti_pattern=anti_pattern,
            )

            if self.strict_mode:
                return error
            else:
                # 非严格模式下只记录警告
                self._violations.append(
                    {
                        "role": role.value,
                        "action": action,
                        "anti_pattern": anti_pattern.value,
                        "severity": "WARNING",
                    }
                )

        return None

    def guard_communication(
        self,
        from_role: Role,
        to_role: Role,
        message: dict[str, Any],
    ) -> list[ConstitutionViolationError]:
        """守卫角色通信。

        Returns:
            违规错误列表
        """
        errors = self.enforcer.check_communication(from_role, to_role, message)
        violations = []

        for error_msg in errors:
            violation = ConstitutionViolationError(
                role=from_role,
                action=f"communicate_to_{to_role.value}",
                anti_pattern=AntiPattern.DIRECT_COUPLING,
                detail=error_msg,
            )
            violations.append(violation)

            if self.strict_mode:
                raise violation

        return violations

    def guard_state_access(
        self,
        accessor_role: Role,
        owner_role: Role,
        state_key: str,
    ) -> ConstitutionViolationError | None:
        """守卫状态访问。

        Returns:
            如果违规，返回错误；否则返回 None
        """
        allowed = self.enforcer.check_state_access(accessor_role, owner_role, state_key)

        if not allowed:
            error = ConstitutionViolationError(
                role=accessor_role,
                action=f"access_state:{owner_role.value}.{state_key}",
                anti_pattern=AntiPattern.PRIVATE_STATE_LEAK,
            )

            if self.strict_mode:
                return error

        return None

    def get_violations(self) -> list[dict[str, Any]]:
        """获取所有违规记录。"""
        return self._violations + self.enforcer.get_violations()


def constitutional_role(role: Role, strict: bool = True):
    """宪法角色装饰器 - 为类方法添加宪法检查。

    Usage:
        @constitutional_role(Role.PM)
        class PMNode:
            def execute(self, context):
                # 自动检查 PM 的所有行为
                pass
    """

    def decorator(cls: Any) -> Any:
        original_init = cls.__init__
        original_execute = getattr(cls, "execute", None)

        @functools.wraps(original_init)
        def new_init(self: Any, *args: Any, **kwargs: Any) -> None:
            self._constitution_role = role
            self._constitution_guard = ConstitutionGuard(strict_mode=strict)
            original_init(self, *args, **kwargs)

        def guarded_execute(self: Any, *args: Any, **kwargs: Any) -> Any:
            guard = getattr(self, "_constitution_guard", None)

            if guard and original_execute:
                # 检查 execute 是否是允许的行为
                error = guard.guard_action(role, "execute")
                if error:
                    raise error

                return original_execute(self, *args, **kwargs)

            return original_execute(self, *args, **kwargs) if original_execute else None

        # Replace __init__ using functools.wraps to preserve the original
        cls.__init__ = new_init
        if original_execute:
            cls.execute = guarded_execute

        # Add宪法检查方法 as proper methods
        def _check_constitution(self: Any, action: str) -> Any:
            guard = getattr(self, "_constitution_guard", None)
            constitution_role = getattr(self, "_constitution_role", None)
            if guard and constitution_role:
                return guard.guard_action(constitution_role, action)
            return None

        def _assert_allowed(self: Any, action: str) -> None:
            guard = getattr(self, "_constitution_guard", None)
            constitution_role = getattr(self, "_constitution_role", None)
            if guard and constitution_role:
                _assert_action_allowed(guard, constitution_role, action)

        cls._check_constitution = _check_constitution
        cls._assert_allowed = _assert_allowed

        return cls

    return decorator


def _assert_action_allowed(guard: ConstitutionGuard, role: Role, action: str) -> None:
    """断言行为被允许，否则抛出异常。"""
    error = guard.guard_action(role, action)
    if error:
        raise error


def require_role_permission(role: Role, action: str):
    """函数装饰器 - 要求特定权限。

    Usage:
        @require_role_permission(Role.DIRECTOR, "write_code")
        def implement_feature(self, ...):
            pass
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not is_action_allowed(role, action):
                raise ConstitutionViolationError(
                    role=role,
                    action=action,
                    anti_pattern=AntiPattern.ROLE_OVERREACH,
                    detail=f"Role {role.value} is not allowed to perform {action}",
                )
            return func(*args, **kwargs)

        return wrapper

    return decorator


class ConstitutionalRoleContext:
    """宪法角色上下文 - 包装 RoleContext 添加宪法检查。"""

    def __init__(
        self,
        role: Role,
        context: Any,  # RoleContext
        guard: ConstitutionGuard | None = None,
    ) -> None:
        self._role = role
        self._context = context
        self._guard = guard or ConstitutionGuard()
        self._access_log: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        """拦截属性访问，检查是否越界。"""
        # 检查是否访问其他角色的私有状态
        if name.endswith("_result") and name != f"{self._role.value.lower()}_result":
            other_role_name = name.replace("_result", "").upper()
            try:
                other_role = Role(other_role_name)
                error = self._guard.guard_state_access(self._role, other_role, name)
                if error:
                    self._access_log.append(
                        {
                            "action": "state_access_denied",
                            "key": name,
                            "error": str(error),
                        }
                    )
                    raise AttributeError(f"Constitution violation: {error}")
            except ValueError:
                pass  # Not a role-related attribute

        self._access_log.append({"action": "access", "key": name})
        return getattr(self._context, name)

    def get_previous_result(self, role_name: str) -> dict[str, Any] | None:
        """安全获取其他角色结果。"""
        try:
            # 检查通信是否允许
            error = self._guard.guard_action(self._role, f"read_{role_name}_result")
            if error:
                raise ConstitutionViolationError(
                    role=self._role,
                    action=f"read_{role_name}_result",
                    anti_pattern=AntiPattern.STATE_SHARING,
                    detail=f"Cannot access {role_name} result directly",
                )

            return self._context.get_previous_result(role_name)
        except ValueError:
            return self._context.get_previous_result(role_name)

    def send_to(self, target_role: Role, message: dict[str, Any]) -> None:
        """安全发送消息给其他角色。"""
        violations = self._guard.guard_communication(self._role, target_role, message)
        if violations:
            raise violations[0]

        self._access_log.append(
            {
                "action": "send",
                "to": target_role.value,
                "message_type": message.get("type", "unknown"),
            }
        )


class RoleActionRegistry:
    """角色行为注册表 - 追踪各角色的行为。"""

    def __init__(self) -> None:
        self._actions: dict[Role, list[str]] = {}
        self._guard = ConstitutionGuard()
        self._violations: list[dict[str, Any]] = []

    def reset_for_testing(self) -> None:
        """Reset for test isolation."""
        self._violations.clear()
        self._actions.clear()

    def register_action(self, role: Role, action: str) -> ConstitutionViolationError | None:
        """注册角色行为并检查合规性。"""
        if role not in self._actions:
            self._actions[role] = []

        error = self._guard.guard_action(role, action)
        if error:
            return error

        self._actions[role].append(action)
        return None

    def get_role_actions(self, role: Role) -> list[str]:
        """获取角色的行为历史。"""
        return self._actions.get(role, [])

    def generate_report(self) -> dict[str, Any]:
        """生成合规报告。"""
        return {
            "role_actions": {role.value: actions for role, actions in self._actions.items()},
            "violations": self._guard.get_violations(),
        }


# 全局注册表
_global_registry = RoleActionRegistry()


def register_role_action(role: Role, action: str) -> ConstitutionViolationError | None:
    """全局函数 - 注册角色行为。"""
    return _global_registry.register_action(role, action)


def get_constitution_report() -> dict[str, Any]:
    """获取宪法执行报告。"""
    return _global_registry.generate_report()


__all__ = [
    "ConstitutionGuard",
    "ConstitutionViolationError",
    "ConstitutionalRoleContext",
    "RoleActionRegistry",
    "constitutional_role",
    "get_constitution_report",
    "register_role_action",
    "require_role_permission",
]
