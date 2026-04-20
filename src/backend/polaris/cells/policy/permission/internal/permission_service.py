"""Permission Service - 统一权限决策服务

基于 RBAC 模型的权限决策中心（Permission Decision Point）。

提供统一的权限检查接口，整合现有的 RoleToolGateway 逻辑，
支持角色-权限-资源三元组的细粒度权限控制。

Phase 7 更新：
- 集成 PermissionConditionEvaluator 实现条件评估
- 集成 PermissionRoleGraph 实现角色继承

Phase 8 更新（P0-09 权限决策审计）：
- 每次 ALLOW/DENY 决策写入 audit.evidence（通过 PermissionAuditPort 公开契约）
- 审计失败可观测（logger.error），不影响权限决策本身

状态管理策略（重构后）：
- PermissionService 实例内部持有策略等状态。
- create_permission_service() 用于显式创建独立实例，适合 DI 和测试隔离。
- get_permission_service() 提供按 workspace 复用的进程内共享实例，保持既有调用契约稳定。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Protocol

from polaris.cells.policy.permission.internal.condition_evaluator import (
    ConditionType,
    EvaluationContext as ConditionEvalContext,
    PermissionCondition,
    PermissionConditionEvaluator,
)
from polaris.cells.policy.permission.internal.role_graph import PermissionRoleGraph
from polaris.cells.roles.profile.public.service import (
    Action,
    PermissionCheckResult,
    Policy,
    PolicyEffect,
    Resource,
    ResourceType,
    Subject,
    SubjectType,
)

logger = logging.getLogger(__name__)

_PERMISSION_SERVICE_CACHE: dict[str, PermissionService] = {}
_PERMISSION_SERVICE_CACHE_LOCK = Lock()


# ---------------------------------------------------------------------------
# Audit port (Protocol-based, dependency-injected)
# ---------------------------------------------------------------------------


class PermissionAuditPort(Protocol):
    """Port for recording permission decisions to the audit evidence store.

    Implementors must be thread-safe and must never raise; exceptions are the
    caller's responsibility to handle before propagation reaches the decision
    path.
    """

    def record_decision(
        self,
        *,
        subject: str,
        action: str,
        resource: str,
        result: str,
        reason: str,
        timestamp: str,
        request_id: str,
    ) -> None:
        """Record one ALLOW/DENY decision.

        Args:
            subject: Identifier of the requesting subject (e.g. "role:pm").
            action: The action attempted (e.g. "read", "write", "execute").
            resource: The resource path/pattern targeted.
            result: "ALLOW" or "DENY".
            reason: Human-readable explanation from the matched policy.
            timestamp: ISO-8601 UTC timestamp of the decision.
            request_id: Unique request identifier (UUID).
        """
        ...


class PermissionServiceError(Exception):
    """权限服务异常"""

    pass


@dataclass
class DecisionContext:
    """权限决策上下文

    包含权限决策所需的所有上下文信息。
    """

    task_id: str | None = None
    session_id: str | None = None
    request_id: str | None = None
    workspace: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class PermissionService:
    """统一权限决策服务

    作为 Permission Decision Point (PDP)，负责：
    1. 策略的加载和管理
    2. 权限请求的评估
    3. 与现有 RoleToolGateway 的集成
    4. 条件评估和角色继承（Phase 7）
    5. ALLOW/DENY 决策审计（Phase 8）
    """

    def __init__(
        self,
        workspace: str = "",
        audit_sink: PermissionAuditPort | None = None,
    ) -> None:
        """初始化权限服务

        Args:
            workspace: 工作区路径
            audit_sink: 审计写入接口（可选）。若为 None，决策不写入审计；
                        若注入实例，每次 ALLOW/DENY 决策均异步写入，写入
                        失败仅记录 logger.error，不影响决策结果。
        """
        self.workspace = workspace
        self._policies: dict[str, Policy] = {}
        self._role_permissions: dict[str, set[str]] = {}  # role_id -> permission_ids
        self._subject_role_bindings: dict[tuple[SubjectType, str], set[str]] = {}
        self._initialized = False

        # Phase 7: 集成条件评估器和角色图
        self._condition_evaluator = PermissionConditionEvaluator()
        self._role_graph = PermissionRoleGraph()

        # Phase 8: 审计 sink（可 None，测试时注入 mock）
        self._audit_sink: PermissionAuditPort | None = audit_sink

    async def initialize(self) -> None:
        """初始化服务，加载内置策略"""
        if self._initialized:
            return

        # 加载内置 RBAC 策略
        self._load_builtin_policies()
        # 从 RoleToolGateway 转换权限
        await self._sync_from_tool_gateway()

        self._initialized = True
        logger.info(f"[PermissionService] Initialized with {len(self._policies)} policies")

    def _load_builtin_policies(self) -> None:
        """加载内置 RBAC 策略"""
        # 基于 builtin_profiles.py 中的角色定义，创建对应的 RBAC 策略
        builtin_policies = [
            # PM 角色策略 - 只读
            Policy(
                id="pm-read-all",
                name="PM 只读所有文件",
                effect=PolicyEffect.ALLOW,
                subjects=[Subject(type=SubjectType.ROLE, id="pm")],
                resources=[Resource(type=ResourceType.FILE, pattern="**/*")],
                actions=[Action.READ],
                priority=10,
            ),
            Policy(
                id="pm-use-read-tools",
                name="PM 使用只读工具",
                effect=PolicyEffect.ALLOW,
                subjects=[Subject(type=SubjectType.ROLE, id="pm")],
                resources=[Resource(type=ResourceType.TOOL, pattern="*")],
                actions=[Action.EXECUTE],
                priority=10,
                # 条件限制：仅限只读类工具
            ),
            # Architect 角色策略 - 只读分析
            Policy(
                id="architect-read-all",
                name="Architect 只读所有文件",
                effect=PolicyEffect.ALLOW,
                subjects=[Subject(type=SubjectType.ROLE, id="architect")],
                resources=[Resource(type=ResourceType.FILE, pattern="**/*")],
                actions=[Action.READ],
                priority=10,
            ),
            # Chief Engineer 角色策略 - 只读分析
            Policy(
                id="chief-engineer-read-all",
                name="Chief Engineer 只读所有文件",
                effect=PolicyEffect.ALLOW,
                subjects=[Subject(type=SubjectType.ROLE, id="chief_engineer")],
                resources=[Resource(type=ResourceType.FILE, pattern="**/*")],
                actions=[Action.READ],
                priority=10,
            ),
            # Director 角色策略 - 读写执行
            Policy(
                id="director-read-all",
                name="Director 读取所有文件",
                effect=PolicyEffect.ALLOW,
                subjects=[Subject(type=SubjectType.ROLE, id="director")],
                resources=[Resource(type=ResourceType.FILE, pattern="**/*")],
                actions=[Action.READ],
                priority=50,
            ),
            Policy(
                id="director-write-all",
                name="Director 写入所有文件",
                effect=PolicyEffect.ALLOW,
                subjects=[Subject(type=SubjectType.ROLE, id="director")],
                resources=[Resource(type=ResourceType.FILE, pattern="**/*")],
                actions=[Action.WRITE],
                priority=50,
            ),
            Policy(
                id="director-execute-tools",
                name="Director 执行工具",
                effect=PolicyEffect.ALLOW,
                subjects=[Subject(type=SubjectType.ROLE, id="director")],
                resources=[Resource(type=ResourceType.TOOL, pattern="*")],
                actions=[Action.EXECUTE],
                priority=50,
            ),
            Policy(
                id="director-execute-commands",
                name="Director 执行命令",
                effect=PolicyEffect.ALLOW,
                subjects=[Subject(type=SubjectType.ROLE, id="director")],
                resources=[Resource(type=ResourceType.API, pattern="execute_command")],
                actions=[Action.EXECUTE],
                priority=50,
            ),
            # Director 敏感文件保护
            Policy(
                id="director-deny-sensitive",
                name="Director 禁止操作敏感文件",
                effect=PolicyEffect.DENY,
                subjects=[Subject(type=SubjectType.ROLE, id="director")],
                resources=[
                    Resource(type=ResourceType.FILE, pattern="**/.env"),
                    Resource(type=ResourceType.FILE, pattern="**/.env.*"),
                    Resource(type=ResourceType.FILE, pattern="**/secrets/**"),
                    Resource(type=ResourceType.FILE, pattern="**/credentials/**"),
                    Resource(type=ResourceType.FILE, pattern="**/*.key"),
                    Resource(type=ResourceType.FILE, pattern="**/*.pem"),
                    Resource(type=ResourceType.FILE, pattern="**/*.crt"),
                    Resource(type=ResourceType.FILE, pattern="**/*.p12"),
                    Resource(type=ResourceType.FILE, pattern="**/*.pfx"),
                    Resource(type=ResourceType.FILE, pattern="**/id_rsa*"),
                    Resource(type=ResourceType.FILE, pattern="**/id_ed25519*"),
                    Resource(type=ResourceType.FILE, pattern="**/.npmrc"),
                    Resource(type=ResourceType.FILE, pattern="**/.pypirc"),
                    Resource(type=ResourceType.FILE, pattern="**/aws_credentials"),
                    Resource(type=ResourceType.FILE, pattern="**/.aws/credentials"),
                    Resource(type=ResourceType.FILE, pattern="**/.docker/config.json"),
                ],
                actions=[Action.READ, Action.WRITE, Action.DELETE],
                priority=100,  # 高优先级，覆盖 allow
            ),
            # QA 角色策略 - 只读 + 执行测试
            Policy(
                id="qa-read-all",
                name="QA 读取所有文件",
                effect=PolicyEffect.ALLOW,
                subjects=[Subject(type=SubjectType.ROLE, id="qa")],
                resources=[Resource(type=ResourceType.FILE, pattern="**/*")],
                actions=[Action.READ],
                priority=30,
            ),
            Policy(
                id="qa-execute-commands",
                name="QA 执行命令",
                effect=PolicyEffect.ALLOW,
                subjects=[Subject(type=SubjectType.ROLE, id="qa")],
                resources=[Resource(type=ResourceType.API, pattern="execute_command")],
                actions=[Action.EXECUTE],
                priority=30,
            ),
            # 禁止删除策略（通用）
            Policy(
                id="global-deny-delete-sensitive",
                name="全局禁止删除敏感文件",
                effect=PolicyEffect.DENY,
                subjects=[
                    Subject(type=SubjectType.ROLE, id="pm"),
                    Subject(type=SubjectType.ROLE, id="architect"),
                    Subject(type=SubjectType.ROLE, id="chief_engineer"),
                    Subject(type=SubjectType.ROLE, id="qa"),
                ],
                resources=[
                    Resource(type=ResourceType.FILE, pattern="**/.git/**"),
                    Resource(type=ResourceType.FILE, pattern="**/package-lock.json"),
                    Resource(type=ResourceType.FILE, pattern="**/yarn.lock"),
                ],
                actions=[Action.DELETE],
                priority=200,  # 最高优先级
            ),
        ]

        for policy in builtin_policies:
            self._policies[policy.id] = policy

        logger.debug(f"[PermissionService] Loaded {len(builtin_policies)} builtin policies")

    async def _sync_from_tool_gateway(self) -> None:
        """从现有 RoleToolGateway 同步权限配置

        将现有的工具级别权限配置转换为 RBAC 权限。
        """
        try:
            from polaris.cells.roles.kernel.public.service import RoleToolGateway
            from polaris.cells.roles.profile.public.service import load_core_roles, registry

            # 加载核心角色配置
            load_core_roles()

            # 获取所有已注册的角色
            profiles = registry.get_all_profiles()

            for role_id, profile in profiles.items():
                # 创建 RoleToolGateway 实例以获取工具策略
                gateway = RoleToolGateway(profile, self.workspace)

                # 提取工具权限
                tool_permissions: set[str] = set()

                # 白名单工具 -> execute 权限
                for tool in gateway.get_available_tools():
                    tool_permissions.add(f"tool:execute:{tool}")

                # 根据策略标志添加额外权限
                if profile.tool_policy.allow_code_write:
                    tool_permissions.add("file:write:**/*")
                if profile.tool_policy.allow_command_execution:
                    tool_permissions.add("api:execute:execute_command")
                if profile.tool_policy.allow_file_delete:
                    tool_permissions.add("file:delete:**/*")

                self._role_permissions[role_id] = tool_permissions

            logger.debug(f"[PermissionService] Synced permissions for {len(self._role_permissions)} roles")

        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("[PermissionService] Failed to sync from tool gateway: %s", exc)

    # ------------------------------------------------------------------
    # Phase 8: Audit helper (fire-and-forget, observable)
    # ------------------------------------------------------------------

    def _emit_audit_decision(
        self,
        *,
        subject: Subject,
        resource: Resource,
        action: Action,
        result: str,
        reason: str,
        context: DecisionContext | None,
    ) -> None:
        """Write one ALLOW/DENY decision to the audit evidence store.

        Failures are logged at ERROR level but never raised — the permission
        decision must not be affected by audit availability.

        Args:
            subject: Requesting subject.
            resource: Targeted resource.
            action: Attempted action.
            result: "ALLOW" or "DENY".
            reason: Policy-level reason string.
            context: Optional request context (used for request_id extraction).
        """
        if self._audit_sink is None:
            return

        request_id = ((context.request_id or "").strip() if context else "") or str(uuid.uuid4())

        subject_str = f"{subject.type.value}:{subject.id}"
        resource_str = resource.path or resource.pattern or str(resource.type.value)
        action_str = action.value
        timestamp = datetime.now(timezone.utc).isoformat()

        try:
            self._audit_sink.record_decision(
                subject=subject_str,
                action=action_str,
                resource=resource_str,
                result=result,
                reason=reason,
                timestamp=timestamp,
                request_id=request_id,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.error(
                "[PermissionService] Audit write failed (decision=%s subject=%s "
                "action=%s resource=%s request_id=%s): %s",
                result,
                subject_str,
                action_str,
                resource_str,
                request_id,
                exc,
                exc_info=True,
            )

    async def check_permission(
        self,
        subject: Subject,
        resource: Resource,
        action: Action,
        context: DecisionContext | None = None,
    ) -> PermissionCheckResult:
        """检查权限

        评估主体对资源的操作权限。

        Args:
            subject: 请求主体
            resource: 目标资源
            action: 请求的操作
            context: 决策上下文

        Returns:
            PermissionCheckResult: 权限检查结果
        """
        if not self._initialized:
            await self.initialize()

        # Phase 7: 展开角色继承
        expanded_roles = self._role_graph.expand_roles(subject.id)

        # 查找匹配的策略（考虑所有展开后的角色）
        matched_policies: list[Policy] = []
        deny_policies: list[Policy] = []
        allow_policies: list[Policy] = []

        for policy in self._policies.values():
            if not policy.enabled:
                continue

            # 检查主体匹配（考虑角色继承）
            if not self._matches_subject_with_inheritance(policy, subject, expanded_roles):
                continue

            # 检查资源匹配
            if not policy.matches_resource(resource):
                continue

            # 检查操作匹配
            if not policy.matches_action(action):
                continue

            # Phase 7: 评估条件
            if policy.conditions:
                condition_result = self._evaluate_policy_conditions(policy, subject, resource, action, context)
                if not condition_result.matched:
                    # 条件不满足，跳过此策略
                    continue

            matched_policies.append(policy)

            if policy.effect == PolicyEffect.DENY:
                deny_policies.append(policy)
            else:
                allow_policies.append(policy)

        # 决策合并：deny 优先，按优先级排序
        if deny_policies:
            deny_policies.sort(key=lambda p: p.priority, reverse=True)
            reason = f"denied by policy: {deny_policies[0].id}"
            self._emit_audit_decision(
                subject=subject,
                resource=resource,
                action=action,
                result="DENY",
                reason=reason,
                context=context,
            )
            return PermissionCheckResult(
                allowed=False,
                decision="deny",
                matched_policies=[p.id for p in deny_policies],
                reason=reason,
            )

        if allow_policies:
            allow_policies.sort(key=lambda p: p.priority, reverse=True)
            reason = f"allowed by policy: {allow_policies[0].id}"
            self._emit_audit_decision(
                subject=subject,
                resource=resource,
                action=action,
                result="ALLOW",
                reason=reason,
                context=context,
            )
            return PermissionCheckResult(
                allowed=True,
                decision="allow",
                matched_policies=[p.id for p in allow_policies],
                reason=reason,
            )

        # 无匹配策略：默认拒绝
        reason = "no matching policy found, default deny"
        self._emit_audit_decision(
            subject=subject,
            resource=resource,
            action=action,
            result="DENY",
            reason=reason,
            context=context,
        )
        return PermissionCheckResult(
            allowed=False,
            decision="deny",
            matched_policies=[],
            reason=reason,
        )

    def _matches_subject_with_inheritance(
        self,
        policy: Policy,
        subject: Subject,
        expanded_roles: set[str],
    ) -> bool:
        """检查主体是否匹配策略（考虑角色继承）

        Args:
            policy: 策略
            subject: 请求主体
            expanded_roles: 展开后的所有角色

        Returns:
            bool: 是否匹配
        """
        for configured in policy.subjects:
            if configured.type != subject.type:
                continue
            # 直接匹配或继承匹配
            if configured.id in ("*", subject.id) or configured.id in expanded_roles:
                return True
        return False

    def _evaluate_policy_conditions(
        self,
        policy: Policy,
        subject: Subject,
        resource: Resource,
        action: Action,
        context: DecisionContext | None,
    ) -> Any:
        """评估策略条件

        Args:
            policy: 策略
            subject: 请求主体
            resource: 目标资源
            action: 请求的操作
            context: 决策上下文

        Returns:
            ConditionResult: 条件评估结果
        """
        if not policy.conditions:
            from polaris.cells.policy.permission.internal.condition_evaluator import ConditionResult

            return ConditionResult(matched=True, reason="No conditions")

        # 构建评估上下文
        eval_context = ConditionEvalContext(
            action=action.value,
            target_path=resource.path or resource.pattern,
            role=subject.id,
            timestamp=datetime.now(timezone.utc),
            custom_data={
                "policy_id": policy.id,
                "workspace": context.workspace if context else "",
                "task_id": context.task_id if context else None,
                "session_id": context.session_id if context else None,
            },
        )

        # 转换策略条件为 PermissionCondition 列表
        conditions = self._convert_policy_conditions(policy.conditions)

        # 评估所有条件（默认使用 "all" 模式）
        match_mode = policy.conditions.get("condition_mode", "all")
        return self._condition_evaluator.evaluate_all(conditions, eval_context, match_mode)

    def _convert_policy_conditions(self, conditions_dict: dict[str, Any]) -> list[PermissionCondition]:
        """将策略条件字典转换为 PermissionCondition 列表

        Args:
            conditions_dict: 策略条件字典

        Returns:
            List[PermissionCondition]: 条件列表
        """
        conditions = []

        # 处理文件路径条件
        if "file_path" in conditions_dict:
            conditions.append(
                PermissionCondition(
                    type=ConditionType.FILE_PATH,
                    pattern=conditions_dict["file_path"],
                )
            )

        # 处理时间范围条件
        if "time_range" in conditions_dict:
            time_range = conditions_dict["time_range"]
            from datetime import time

            start_time = None
            end_time = None
            if "start" in time_range:
                start_time = time.fromisoformat(time_range["start"])
            if "end" in time_range:
                end_time = time.fromisoformat(time_range["end"])

            conditions.append(
                PermissionCondition(
                    type=ConditionType.TIME_RANGE,
                    start_time=start_time,
                    end_time=end_time,
                )
            )

        # 处理资源限制条件
        if "resource_limit" in conditions_dict:
            resource_limit = conditions_dict["resource_limit"]
            conditions.append(
                PermissionCondition(
                    type=ConditionType.RESOURCE_LIMIT,
                    resource_type=resource_limit.get("type", "default"),
                    limit=resource_limit.get("limit", 0),
                )
            )

        # 处理自定义条件
        if "custom" in conditions_dict:
            custom = conditions_dict["custom"]
            conditions.append(
                PermissionCondition(
                    type=ConditionType.CUSTOM,
                    custom_evaluator=custom.get("evaluator", "default"),
                    config=custom.get("config", {}),
                )
            )

        return conditions

    async def check_tool_permission(
        self,
        role_id: str,
        tool_name: str,
        tool_args: dict[str, Any] | None = None,
    ) -> PermissionCheckResult:
        """检查工具调用权限

        整合现有的 RoleToolGateway 检查逻辑。

        Args:
            role_id: 角色 ID
            tool_name: 工具名称
            tool_args: 工具参数

        Returns:
            PermissionCheckResult: 权限检查结果
        """
        if not self._initialized:
            await self.initialize()

        # 首先通过 RBAC 策略检查
        rbac_result = await self.check_permission(
            subject=Subject(type=SubjectType.ROLE, id=role_id),
            resource=Resource(type=ResourceType.TOOL, pattern=tool_name),
            action=Action.EXECUTE,
        )

        # 如果 RBAC 拒绝，直接返回
        if not rbac_result.allowed:
            return rbac_result

        # 如果 RBAC 允许，委托给 RoleToolGateway 进行更细粒度的检查
        try:
            from polaris.cells.roles.kernel.public.service import RoleToolGateway
            from polaris.cells.roles.profile.public.service import registry

            profile = registry.get_profile(role_id)
            if profile:
                gateway = RoleToolGateway(profile, self.workspace)
                allowed, reason = gateway.check_tool_permission(tool_name, tool_args)

                if not allowed:
                    return PermissionCheckResult(
                        allowed=False,
                        decision="deny",
                        matched_policies=rbac_result.matched_policies,
                        reason=f"tool gateway: {reason}",
                    )

            return rbac_result

        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("[PermissionService] Tool gateway check failed: %s", exc)
            # 如果 RoleToolGateway 检查失败，回退到 RBAC 决策
            return rbac_result

    async def get_effective_permissions(self, subject: Subject) -> list[str]:
        """获取主体的有效权限列表

        包括直接权限和继承的权限。

        Args:
            subject: 主体

        Returns:
            权限 ID 列表
        """
        if not self._initialized:
            await self.initialize()

        permissions: set[str] = set()
        if subject.type == SubjectType.ROLE:
            # Phase 7: 展开角色继承
            expanded_roles = self._role_graph.expand_roles(subject.id)
            for role_id in expanded_roles:
                permissions.update(self._role_permissions.get(role_id, set()))
        else:
            role_ids = self._subject_role_bindings.get((subject.type, subject.id), set())
            for role_id in role_ids:
                # Phase 7: 展开角色继承
                expanded_roles = self._role_graph.expand_roles(role_id)
                for expanded_role_id in expanded_roles:
                    permissions.update(self._role_permissions.get(expanded_role_id, set()))

        return sorted(list(permissions))

    async def list_roles(self) -> list[dict[str, Any]]:
        """列出所有角色及其权限统计

        Returns:
            角色信息列表
        """
        if not self._initialized:
            await self.initialize()

        from polaris.cells.roles.profile.public.service import load_core_roles, registry

        # 确保核心角色已加载
        load_core_roles()

        roles = []
        profiles = registry.get_all_profiles()

        for role_id, profile in profiles.items():
            # Phase 7: 获取角色继承信息
            hierarchy = self._role_graph.get_role_hierarchy(role_id)

            role_info = {
                "id": role_id,
                "display_name": profile.display_name,
                "description": profile.description,
                "permission_count": len(self._role_permissions.get(role_id, set())),
                "inherits_from": hierarchy["inherits_from"],
                "includes": hierarchy["includes"],
                "expanded_roles": hierarchy["expanded"],
                "priority": 10 if role_id in ["pm", "qa"] else 50,
            }
            roles.append(role_info)

        return roles

    async def assign_role(
        self,
        subject_type: SubjectType,
        subject_id: str,
        role_id: str,
    ) -> dict[str, Any]:
        """分配角色

        Args:
            subject_type: 主体类型
            subject_id: 主体 ID
            role_id: 角色 ID

        Returns:
            分配结果

        Note:
            当前角色分配功能尚未持久化，仅返回状态信息。
        """
        if not self._initialized:
            await self.initialize()

        # 验证角色是否存在
        from polaris.cells.roles.profile.public.service import load_core_roles, registry

        # 确保核心角色已加载
        load_core_roles()

        profile = registry.get_profile(role_id)
        if not profile:
            raise PermissionServiceError(f"Role not found: {role_id}")

        normalized_subject_id = str(subject_id or "").strip()
        if not normalized_subject_id:
            raise PermissionServiceError("subject_id is required")

        key = (subject_type, normalized_subject_id)
        self._subject_role_bindings.setdefault(key, set()).add(role_id)

        return {
            "assigned": True,
            "subject": {"type": subject_type.value, "id": normalized_subject_id},
            "role_id": role_id,
            "effective_role_count": len(self._subject_role_bindings.get(key, set())),
        }

    def get_policy(self, policy_id: str) -> Policy | None:
        """获取策略详情"""
        return self._policies.get(policy_id)

    def list_policies(self) -> list[dict[str, Any]]:
        """列出所有策略"""
        return [p.to_dict() for p in self._policies.values()]

    # Phase 7: 新增方法 - 条件评估
    def evaluate_condition(
        self,
        condition: PermissionCondition,
        context: ConditionEvalContext,
    ) -> Any:
        """评估单个条件

        Args:
            condition: 条件定义
            context: 评估上下文

        Returns:
            ConditionResult: 评估结果
        """
        return self._condition_evaluator.evaluate(condition, context)

    def evaluate_conditions(
        self,
        conditions: list[PermissionCondition],
        context: ConditionEvalContext,
        match_mode: str = "all",
    ) -> Any:
        """评估多个条件

        Args:
            conditions: 条件列表
            context: 评估上下文
            match_mode: 匹配模式，"all" 或 "any"

        Returns:
            ConditionResult: 综合评估结果
        """
        return self._condition_evaluator.evaluate_all(conditions, context, match_mode)

    # Phase 7: 新增方法 - 角色图操作
    def expand_roles(self, role: str) -> set[str]:
        """展开角色继承

        Args:
            role: 起始角色

        Returns:
            Set[str]: 展开后的所有角色
        """
        return self._role_graph.expand_roles(role)

    def get_role_hierarchy(self, role: str) -> dict[str, Any]:
        """获取角色层次结构

        Args:
            role: 角色标识

        Returns:
            Dict: 包含 inherits_from, includes, expanded 的字典
        """
        return self._role_graph.get_role_hierarchy(role)

    def check_role_cycle(self) -> bool:
        """检查角色图中是否存在循环

        Returns:
            bool: 如果存在循环返回 True
        """
        return self._role_graph.has_cycle()


# =============================================================================
# 工厂函数（无全局缓存，支持 DI）
# =============================================================================


def create_permission_service(workspace: str = "") -> PermissionService:
    """工厂函数：创建一个新的 PermissionService 实例。

    调用方（应用层、DI 容器）负责实例生命周期和缓存策略。
    测试可直接使用 ``PermissionService(workspace=...)`` 或本函数，
    无需任何全局清理。

    Args:
        workspace: 工作区路径

    Returns:
        PermissionService: 全新的权限服务实例（未初始化）。
    """
    return PermissionService(workspace=_normalize_workspace(workspace))


def _normalize_workspace(workspace: str) -> str:
    return str(workspace or "")


async def get_permission_service(workspace: str = "") -> PermissionService:
    """获取按 workspace 复用的共享权限服务实例。

    对现有调用方保持稳定：同一 workspace 返回同一实例。
    需要测试隔离或显式生命周期控制时，使用 create_permission_service()。

    Args:
        workspace: 工作区路径

    Returns:
        PermissionService 实例（已初始化）。
    """
    normalized_workspace = _normalize_workspace(workspace)
    with _PERMISSION_SERVICE_CACHE_LOCK:
        service = _PERMISSION_SERVICE_CACHE.get(normalized_workspace)

    if service is None:
        candidate = PermissionService(workspace=normalized_workspace)
        await candidate.initialize()
        with _PERMISSION_SERVICE_CACHE_LOCK:
            service = _PERMISSION_SERVICE_CACHE.get(normalized_workspace)
            if service is None:
                _PERMISSION_SERVICE_CACHE[normalized_workspace] = candidate
                service = candidate
    elif not service._initialized:
        await service.initialize()

    return service


def get_permission_service_sync(workspace: str = "") -> PermissionService:
    """同步获取按 workspace 复用的共享权限服务实例。

    返回值未自动 initialize()，调用方需在异步边界完成初始化。

    Args:
        workspace: 工作区路径

    Returns:
        PermissionService 实例（可能未初始化）。
    """
    normalized_workspace = _normalize_workspace(workspace)
    with _PERMISSION_SERVICE_CACHE_LOCK:
        service = _PERMISSION_SERVICE_CACHE.get(normalized_workspace)
        if service is None:
            service = PermissionService(workspace=normalized_workspace)
            _PERMISSION_SERVICE_CACHE[normalized_workspace] = service
    return service


def reset_permission_service() -> None:
    """清空共享权限服务缓存。"""
    with _PERMISSION_SERVICE_CACHE_LOCK:
        _PERMISSION_SERVICE_CACHE.clear()
