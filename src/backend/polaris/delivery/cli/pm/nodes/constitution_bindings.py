"""Constitution Bindings - 宪法绑定适配器

为现有角色节点添加宪法校验，确保运行时符合宪法约束。

使用方式:
    在 coordinator.py 中导入并启用:
    from polaris.delivery.cli.pm.nodes.constitution_bindings import enable_constitutional_bindings
    enable_constitutional_bindings(strict=True)
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.public import (
    CONSTITUTION,
    AntiPattern,
    ConstitutionalRoleContext,
    ConstitutionEnforcer,
    ConstitutionGuard,
    ConstitutionViolationError,
    Role,
)
from polaris.delivery.cli.pm.nodes.protocols import RoleContext, RoleResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from polaris.delivery.cli.pm.nodes.base import BaseRoleNode

# 角色到 Role 枚举的映射
_ROLE_MAPPING: dict[str, Role] = {
    "PM": Role.PM,
    "ChiefEngineer": Role.CHIEF_ENGINEER,
    "Director": Role.DIRECTOR,
    "QA": Role.QA,
}


class ConstitutionalRoleNodeMixin:
    """宪法角色节点混入 - 为 BaseRoleNode 添加宪法支持。"""

    _constitution_enabled: bool = False
    _constitution_guard: ConstitutionGuard | None = None
    _constitution_role: Role | None = None

    def enable_constitution(self, strict: bool = True) -> None:
        """启用宪法约束。"""
        self._constitution_enabled = True
        self._constitution_guard = ConstitutionGuard(strict_mode=strict)

        # 自动识别角色
        role_name = getattr(self, "role_name", None)
        if role_name and role_name in _ROLE_MAPPING:
            self._constitution_role = _ROLE_MAPPING[role_name]

    def _enforce_action(self, action: str) -> None:
        """强制执行行为检查。"""
        if not self._constitution_enabled or not self._constitution_guard:
            return

        role = self._constitution_role
        if role is None:
            return

        error = self._constitution_guard.guard_action(role, action)
        if error:
            raise error

    def _enforce_communication(
        self,
        target_role: Role,
        message: dict[str, Any],
    ) -> None:
        """强制执行通信检查。"""
        if not self._constitution_enabled or not self._constitution_guard:
            return

        role = self._constitution_role
        if role is None:
            return

        violations = self._constitution_guard.guard_communication(role, target_role, message)
        if violations:
            raise violations[0]

    def _wrap_context(self, context: RoleContext) -> ConstitutionalRoleContext | RoleContext:
        """包装上下文以添加宪法检查。"""
        if not self._constitution_enabled:
            return context

        role = self._constitution_role
        if role is None:
            return context

        return ConstitutionalRoleContext(
            role=role,
            context=context,
            guard=self._constitution_guard,
        )

    def get_constitution_report(self) -> dict[str, Any]:
        """获取宪法执行报告。"""
        if not self._constitution_guard:
            return {"enabled": False}

        return {
            "enabled": True,
            "role": self._constitution_role.value if self._constitution_role else None,
            "violations": self._constitution_guard.get_violations(),
        }


def constitutional_execute(method: Callable) -> Callable:
    """装饰器 - 为 execute 方法添加宪法检查。"""

    @functools.wraps(method)
    def wrapper(self: Any, context: RoleContext, *args, **kwargs) -> RoleResult:
        # 检查是否启用了宪法
        if not getattr(self, "_constitution_enabled", False):
            return method(self, context, *args, **kwargs)

        role = getattr(self, "_constitution_role", None)
        guard = getattr(self, "_constitution_guard", None)

        if not role or not guard:
            return method(self, context, *args, **kwargs)

        # 检查 execute 权限
        error = guard.guard_action(role, "execute")
        if error:
            # 返回错误结果而不是抛出异常
            return RoleResult(
                success=False,
                exit_code=1,
                error=f"Constitution violation: {error}",
                error_code="CONSTITUTION_VIOLATION",
            )

        # 包装上下文
        wrapped_context = ConstitutionalRoleContext(
            role=role,
            context=context,
            guard=guard,
        )

        # 执行方法
        return method(self, wrapped_context, *args, **kwargs)

    return wrapper


class ConstitutionalCoordinator:
    """宪法协调器包装 - 为协调器添加宪法校验。"""

    def __init__(self, coordinator: Any, strict: bool = True) -> None:
        self._coordinator = coordinator
        self._guard = ConstitutionGuard(strict_mode=strict)
        self._enforcer = ConstitutionEnforcer()
        self._strict = strict

    def run_iteration(self, *args, **kwargs) -> Any:
        """运行迭代，带宪法检查。"""
        # 获取当前要执行的角色
        state = getattr(self._coordinator, "state", None)
        if state:
            current_role = getattr(state, "current_role", None)
            if current_role:
                self._validate_role_transition(current_role)

        return self._coordinator.run_iteration(*args, **kwargs)

    def _validate_role_transition(self, role_name: str) -> None:
        """验证角色转换是否合法。"""
        role = _ROLE_MAPPING.get(role_name)
        if not role:
            return

        boundary = CONSTITUTION.get(role)
        if not boundary:
            return

        # 检查上游依赖是否满足
        state = getattr(self._coordinator, "state", None)
        if not state:
            return

        completed_roles = getattr(state, "completed_roles", [])

        for upstream in boundary.upstream_roles:
            if upstream.value not in completed_roles:
                error_msg = f"Constitution violation: {role.value} requires {upstream.value} to complete first"
                if self._strict:
                    raise ConstitutionViolationError(
                        role=role,
                        action="start_execution",
                        anti_pattern=AntiPattern.SKIP_AUDIT if upstream == Role.QA else AntiPattern.SKIP_BLUEPRINT,
                        detail=error_msg,
                    )

    def get_violations(self) -> list[dict[str, Any]]:
        """获取违规记录。"""
        return self._guard.get_violations()


def patch_role_node(base_node_class: type[BaseRoleNode]) -> type[BaseRoleNode]:
    """为角色节点类打补丁，添加宪法支持。

    Usage:
        from polaris.delivery.cli.pm.nodes.pm_node import PMNode
        from polaris.delivery.cli.pm.nodes.constitution_bindings import patch_role_node

        ConstitutionalPMNode = patch_role_node(PMNode)
        node = ConstitutionalPMNode()
        node.enable_constitution(strict=True)
    """

    class ConstitutionalNode(base_node_class, ConstitutionalRoleNodeMixin):  # type: ignore[valid-type,misc]
        """宪法增强的角色节点。"""

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._constitution_enabled = False
            self._constitution_guard = None
            self._constitution_role = None

            # 自动识别角色
            role_name = getattr(self, "role_name", None)
            if role_name and role_name in _ROLE_MAPPING:
                self._constitution_role = _ROLE_MAPPING[role_name]

        def execute(self, context: RoleContext) -> RoleResult:
            """带宪法检查的 execute。"""
            if not self._constitution_enabled:
                return super().execute(context)

            # 检查权限
            self._enforce_action("execute")

            # 检查具体行为
            role_name = self._constitution_role.value if self._constitution_role else ""

            if role_name == "PM":
                self._enforce_pm_actions(context)
            elif role_name == "ChiefEngineer":
                self._enforce_ce_actions(context)
            elif role_name == "Director":
                self._enforce_director_actions(context)
            elif role_name == "QA":
                self._enforce_qa_actions(context)

            return super().execute(context)

        def _enforce_pm_actions(self, context: RoleContext) -> None:
            """PM 特定检查。"""
            # PM 禁止直接写代码
            self._enforce_action("parse_requirements")
            self._enforce_action("decompose_tasks")

            # 检查是否试图跳过 CE
            tasks = context.get_tasks()
            if tasks:
                for task in tasks:
                    # 如果任务复杂但未走 CE，标记违规
                    target_files = task.get("target_files", [])
                    acceptance = task.get("acceptance_criteria", [])
                    if len(target_files) >= 3 or len(acceptance) >= 4:
                        # 复杂任务必须走 CE
                        ce_result = context.get_previous_result("ChiefEngineer")
                        if not ce_result:
                            # 检查是否是直接发送到 Director
                            pass  # 由业务逻辑处理

        def _enforce_ce_actions(self, context: RoleContext) -> None:
            """ChiefEngineer 特定检查。"""
            # CE 禁止直接修改文件
            self._enforce_action("generate_blueprint")

        def _enforce_director_actions(self, context: RoleContext) -> None:
            """Director 特定检查。"""
            # Director 必须遵循蓝图
            ce_result = context.get_previous_result("ChiefEngineer")
            if ce_result:
                # 确保 Director 读取了蓝图
                pass  # 由业务逻辑验证

        def _enforce_qa_actions(self, context: RoleContext) -> None:
            """QA 特定检查。"""
            # QA 禁止写代码
            self._enforce_action("audit_code_quality")

    return ConstitutionalNode


def enable_constitutional_bindings(strict: bool = True) -> dict[str, Any]:
    """启用所有角色节点的宪法绑定。

    此函数应该 coordinator.py 启动时调用。

    Returns:
        启用状态报告
    """
    patched_roles_list: list[str] = []
    errors_list: list[str] = []
    report: dict[str, Any] = {
        "enabled": True,
        "strict": strict,
        "patched_roles": patched_roles_list,
        "errors": errors_list,
    }

    try:
        # 延迟导入避免循环依赖
        from polaris.delivery.cli.pm.nodes.chief_engineer_node import ChiefEngineerNode
        from polaris.delivery.cli.pm.nodes.director_node import DirectorNode
        from polaris.delivery.cli.pm.nodes.pm_node import PMNode
        from polaris.delivery.cli.pm.nodes.qa_node import QANode

        # 为每个节点类打补丁
        roles_to_patch = [
            ("PM", PMNode),
            ("ChiefEngineer", ChiefEngineerNode),
            ("Director", DirectorNode),
            ("QA", QANode),
        ]

        for role_name, node_class in roles_to_patch:
            try:
                # 检查是否已经有 enable_constitution 方法
                if not hasattr(node_class, "enable_constitution"):
                    # 添加 mixin 方法 (使用 Any 绕过静态类型检查)
                    node_cls: Any = node_class
                    node_cls.enable_constitution = lambda self, s=strict: (
                        ConstitutionalRoleNodeMixin.enable_constitution(self, s)
                    )  # type: ignore[assignment]
                    node_cls._enforce_action = ConstitutionalRoleNodeMixin._enforce_action  # type: ignore[assignment]
                    node_cls._enforce_communication = ConstitutionalRoleNodeMixin._enforce_communication  # type: ignore[assignment]
                    node_cls.get_constitution_report = ConstitutionalRoleNodeMixin.get_constitution_report  # type: ignore[assignment]

                    # 保存原始 execute
                    if hasattr(node_class, "execute"):
                        original_execute = node_class.execute

                        @functools.wraps(original_execute)
                        def wrapped_execute(self, context, orig=original_execute, role=role_name):
                            if getattr(self, "_constitution_enabled", False):
                                guard = getattr(self, "_constitution_guard", None)
                                role_enum = _ROLE_MAPPING.get(role)
                                if guard and role_enum:
                                    error = guard.guard_action(role_enum, "execute")
                                    if error:
                                        return RoleResult(
                                            success=False,
                                            exit_code=1,
                                            error=f"Constitution violation: {error}",
                                            error_code="CONSTITUTION_VIOLATION",
                                        )
                            return orig(self, context)

                        node_cls.execute = wrapped_execute  # type: ignore[assignment]

                patched_roles_list.append(role_name)
            except (RuntimeError, ValueError) as e:
                errors_list.append(f"Failed to patch {role_name}: {e}")

    except ImportError as e:
        errors_list.append(f"Import error: {e}")

    return report


__all__ = [
    "_ROLE_MAPPING",
    "ConstitutionalCoordinator",
    "ConstitutionalRoleNodeMixin",
    "constitutional_execute",
    "enable_constitutional_bindings",
    "patch_role_node",
]
