"""Polaris Constitution - 宪法（不可变量）

定义各角色的不可变边界、禁止行为和通信契约。
确保角色高度解耦，任何角色变更不得违反此宪法。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class Role(str, Enum):
    """已定义的角色枚举。新增角色必须在此注册。"""

    PM = "PM"  # Project Manager - 任务编排
    CHIEF_ENGINEER = "ChiefEngineer"  # 技术负责人 - 设计图纸
    DIRECTOR = "Director"  # 执行主管 - 按图施工
    QA = "QA"  # 质量审计 - 审计验收
    POLICY = "Policy"  # 策略闸门 - 合规检查
    FINOPS = "FinOps"  # 预算控制 - 成本管理

    # 扩展角色（预留）
    ARCHITECT = "Architect"  # 系统架构师 - 系统架构
    SECURITY = "Security"  # 安全审查 - 安全审查


class ViolationLevel(Enum):
    """违反级别。"""

    WARNING = auto()  # 警告，可运行时检测
    ERROR = auto()  # 错误，必须立即修复
    FATAL = auto()  # 致命，系统拒绝启动


@dataclass(frozen=True)
class RoleBoundary:
    """角色边界定义（不可变）。

    frozen=True 确保此类实例创建后不可修改，
    实现真正的宪法级约束。
    """

    role: Role

    # 核心职责（必须做）
    responsibilities: frozenset[str]

    # 绝对禁止（不得做）- 违反即触发否决
    prohibitions: frozenset[str]

    # 输入契约（可接收什么）
    input_contracts: frozenset[str]

    # 输出契约（可产出什么）
    output_contracts: frozenset[str]

    # 可调用角色（下游依赖）
    downstream_roles: frozenset[Role]

    # 可被调用角色（上游依赖）
    upstream_roles: frozenset[Role]

    # 私有状态（不得被其他角色直接访问）
    private_state_keys: frozenset[str] = field(default_factory=frozenset)

    def validate_action(self, action: str) -> ViolationLevel | None:
        """验证行为是否违反边界。"""
        if action in self.prohibitions:
            return ViolationLevel.ERROR
        return None

    def can_receive_from(self, role: Role) -> bool:
        """检查是否可接收来自某角色的输入。"""
        return role in self.upstream_roles

    def can_send_to(self, role: Role) -> bool:
        """检查是否可发送输出给某角色。"""
        return role in self.downstream_roles


# =============================================================================
# 宪法：角色边界定义（不可变）
# =============================================================================

CONSTITUTION: dict[Role, RoleBoundary] = {
    # ==========================================================================
    # PM (Project Manager) - 任务编排、需求管理、路由调度
    # ==========================================================================
    Role.PM: RoleBoundary(
        role=Role.PM,
        responsibilities=frozenset(
            [
                "parse_requirements",  # 解析需求
                "decompose_tasks",  # 任务分解
                "generate_task_contract",  # 生成任务契约
                "route_to_chief_engineer",  # 路由到技术负责人
                "collect_feedback",  # 收集反馈
            ]
        ),
        prohibitions=frozenset(
            [
                "write_code",  # 禁止直接写代码
                "modify_source_files",  # 禁止修改源代码
                "execute_shell_commands",  # 禁止执行 shell 命令
                "access_git_internals",  # 禁止直接操作 git
                "make_architectural_decisions",  # 禁止做架构决策（这是 CE 的职责）
                "skip_chief_engineer",  # 禁止跳过 CE（复杂任务）
                "direct_to_director",  # 禁止直接指挥 Director
            ]
        ),
        input_contracts=frozenset(
            [
                "user_requirements",  # 用户需求
                "gap_report",  # 差距报告
                "qa_feedback",  # QA 反馈
                "previous_pm_result",  # 上一轮 PM 结果
            ]
        ),
        output_contracts=frozenset(
            [
                "task_contract",  # 任务契约 (pm_tasks.contract.json)
                "task_list",  # 任务列表
                "routing_decision",  # 路由决策
            ]
        ),
        downstream_roles=frozenset([Role.CHIEF_ENGINEER, Role.POLICY]),
        upstream_roles=frozenset([Role.QA]),  # QA 可反馈到 PM
        private_state_keys=frozenset(
            [
                "pm_iteration",
                "task_history",
                "user_preferences",
            ]
        ),
    ),
    # ==========================================================================
    # ChiefEngineer (技术负责人) - 设计图纸、生成蓝图
    # ==========================================================================
    Role.CHIEF_ENGINEER: RoleBoundary(
        role=Role.CHIEF_ENGINEER,
        responsibilities=frozenset(
            [
                "analyze_task_complexity",  # 分析任务复杂度
                "generate_blueprint",  # 生成施工蓝图
                "design_architecture",  # 架构设计
                "define_module_boundaries",  # 定义模块边界
                "specify_interfaces",  # 指定接口
                "detect_tech_stack",  # 技术栈检测
            ]
        ),
        prohibitions=frozenset(
            [
                "write_implementation_code",  # 禁止写实现代码
                "execute_tests",  # 禁止执行测试
                "modify_files_directly",  # 禁止直接修改文件
                "bypass_director",  # 禁止绕过 Director
                "change_requirements",  # 禁止改变需求（只能建议）
                "make_qa_decisions",  # 禁止做 QA 决策
            ]
        ),
        input_contracts=frozenset(
            [
                "task_contract",  # PM 的任务契约
                "source_code_context",  # 源代码上下文
                "existing_architecture",  # 现有架构
            ]
        ),
        output_contracts=frozenset(
            [
                "task_blueprint",  # 任务蓝图
                "construction_plan",  # 施工计划
                "scope_for_apply",  # 应用范围
                "constraints",  # 约束条件
            ]
        ),
        downstream_roles=frozenset([Role.DIRECTOR, Role.POLICY]),
        upstream_roles=frozenset([Role.PM]),
        private_state_keys=frozenset(
            [
                "design_decisions",
                "architecture_notes",
                "tech_stack_analysis",
            ]
        ),
    ),
    # ==========================================================================
    # Director (执行主管) - 按图施工、代码实现
    # ==========================================================================
    Role.DIRECTOR: RoleBoundary(
        role=Role.DIRECTOR,
        responsibilities=frozenset(
            [
                "implement_code",  # 实现代码
                "follow_blueprint",  # 遵循蓝图
                "execute_task",  # 执行任务
                "collect_evidence",  # 收集证据
                "report_progress",  # 报告进度
            ]
        ),
        prohibitions=frozenset(
            [
                "modify_blueprint",  # 禁止修改蓝图
                "ignore_chief_engineer_plan",  # 禁止忽略 CE 计划
                "change_task_scope",  # 禁止改变任务范围
                "skip_tests",  # 禁止跳过测试（必须执行）
                "bypass_qa",  # 禁止绕过 QA
                "make_architectural_changes",  # 禁止做架构变更（超出蓝图）
                "access_other_task_state",  # 禁止访问其他任务状态
            ]
        ),
        input_contracts=frozenset(
            [
                "task_blueprint",  # CE 的蓝图
                "task_contract",  # PM 的任务契约
                "source_files",  # 源文件
            ]
        ),
        output_contracts=frozenset(
            [
                "code_changes",  # 代码变更
                "evidence_artifacts",  # 证据产物
                "execution_report",  # 执行报告
            ]
        ),
        downstream_roles=frozenset([Role.QA, Role.POLICY]),
        upstream_roles=frozenset([Role.CHIEF_ENGINEER, Role.PM]),
        private_state_keys=frozenset(
            [
                "execution_context",
                "tool_outputs",
                "temporary_files",
            ]
        ),
    ),
    # ==========================================================================
    # QA (质量审计) - 独立审计、否决权、最终验收
    # ==========================================================================
    Role.QA: RoleBoundary(
        role=Role.QA,
        responsibilities=frozenset(
            [
                "audit_code_quality",  # 审计代码质量
                "verify_requirements_met",  # 验证需求满足
                "execute_tests",  # 执行测试
                "issue_veto",  # 行使否决权
                "request_changes",  # 请求变更
                "provide_feedback",  # 提供反馈
            ]
        ),
        prohibitions=frozenset(
            [
                "write_code",  # 禁止写代码（完全独立）
                "modify_files",  # 禁止修改文件
                "modify_blueprint",  # 禁止修改蓝图
                "bypass_audit",  # 禁止绕过审计流程
                "approve_own_work",  # 禁止批准自己的工作
                "direct_command_director",  # 禁止直接指挥 Director
            ]
        ),
        input_contracts=frozenset(
            [
                "code_changes",  # Director 的代码变更
                "execution_report",  # 执行报告
                "task_contract",  # 原始任务契约
                "task_blueprint",  # 施工蓝图
            ]
        ),
        output_contracts=frozenset(
            [
                "audit_report",  # 审计报告
                "veto_decision",  # 否决决定
                "feedback_to_pm",  # 给 PM 的反馈
            ]
        ),
        downstream_roles=frozenset([Role.PM]),  # 只反馈到 PM，不直接指挥
        upstream_roles=frozenset([Role.DIRECTOR]),
        private_state_keys=frozenset(
            [
                "audit_history",
                "test_results",
                "veto_records",
            ]
        ),
    ),
    # ==========================================================================
    # Policy (策略闸门) - 非 LLM 策略闸门
    # ==========================================================================
    Role.POLICY: RoleBoundary(
        role=Role.POLICY,
        responsibilities=frozenset(
            [
                "enforce_lint_rules",  # 执行 lint 规则
                "enforce_type_check",  # 执行类型检查
                "validate_format",  # 验证格式
                "check_compliance",  # 检查合规
            ]
        ),
        prohibitions=frozenset(
            [
                "modify_code",  # 禁止修改代码
                "make_logical_changes",  # 禁止做逻辑变更
                "bypass_veto",  # 禁止绕过否决
                "override_qa",  # 禁止覆盖 QA 决定
            ]
        ),
        input_contracts=frozenset(
            [
                "code_artifacts",  # 代码产物
                "policy_config",  # 策略配置
            ]
        ),
        output_contracts=frozenset(
            [
                "policy_violations",  # 策略违规
                "compliance_report",  # 合规报告
            ]
        ),
        downstream_roles=frozenset([Role.PM, Role.CHIEF_ENGINEER, Role.DIRECTOR]),
        upstream_roles=frozenset([Role.PM, Role.CHIEF_ENGINEER, Role.DIRECTOR, Role.QA]),
        private_state_keys=frozenset(
            [
                "policy_cache",
                "violation_history",
            ]
        ),
    ),
    # ==========================================================================
    # FinOps (预算控制) - 预算控制
    # ==========================================================================
    Role.FINOPS: RoleBoundary(
        role=Role.FINOPS,
        responsibilities=frozenset(
            [
                "track_token_usage",  # 跟踪 token 使用
                "enforce_budget_limits",  # 执行预算限制
                "report_costs",  # 报告成本
                "alert_on_overrun",  # 超限告警
            ]
        ),
        prohibitions=frozenset(
            [
                "halt_execution",  # 禁止停止执行（只能建议）
                "modify_logic",  # 禁止修改逻辑
                "bypass_roles",  # 禁止绕过角色
            ]
        ),
        input_contracts=frozenset(
            [
                "token_usage",  # token 使用量
                "execution_metrics",  # 执行指标
            ]
        ),
        output_contracts=frozenset(
            [
                "budget_report",  # 预算报告
                "cost_alerts",  # 成本告警
            ]
        ),
        downstream_roles=frozenset([Role.PM, Role.CHIEF_ENGINEER, Role.DIRECTOR]),
        upstream_roles=frozenset([Role.PM, Role.CHIEF_ENGINEER, Role.DIRECTOR, Role.QA]),
        private_state_keys=frozenset(
            [
                "budget_history",
                "cost_projections",
            ]
        ),
    ),
}


# =============================================================================
# 宪法：通信协议契约（不可变）
# =============================================================================


@dataclass(frozen=True)
class CommunicationProtocol:
    """角色间通信协议定义。"""

    from_role: Role
    to_role: Role
    message_types: frozenset[str]
    required_fields: frozenset[str]
    forbidden_fields: frozenset[str]

    def validate_message(self, message: dict[str, Any]) -> list[str]:
        """验证消息是否符合协议，返回错误列表。"""
        errors = []

        # 检查必需字段
        for req_field in self.required_fields:
            if req_field not in message:
                errors.append(f"Missing required field: {req_field}")

        # 检查禁止字段
        for forbidden_field in self.forbidden_fields:
            if forbidden_field in message:
                errors.append(f"Forbidden field present: {forbidden_field}")

        return errors


# 定义所有合法通信路径
ALLOWED_COMMUNICATIONS: list[CommunicationProtocol] = [
    # PM -> ChiefEngineer
    CommunicationProtocol(
        from_role=Role.PM,
        to_role=Role.CHIEF_ENGINEER,
        message_types=frozenset(["task_contract", "routing_decision"]),
        required_fields=frozenset(["tasks", "iteration"]),
        forbidden_fields=frozenset(["implementation_details", "code_changes"]),
    ),
    # ChiefEngineer -> Director
    CommunicationProtocol(
        from_role=Role.CHIEF_ENGINEER,
        to_role=Role.DIRECTOR,
        message_types=frozenset(["task_blueprint", "construction_plan"]),
        required_fields=frozenset(["task_id", "blueprint_scope"]),
        forbidden_fields=frozenset(["test_results", "audit_opinion"]),
    ),
    # Director -> QA
    CommunicationProtocol(
        from_role=Role.DIRECTOR,
        to_role=Role.QA,
        message_types=frozenset(["code_changes", "evidence_artifacts"]),
        required_fields=frozenset(["task_id", "changes"]),
        forbidden_fields=frozenset(["self_approval", "skip_audit_flag"]),
    ),
    # QA -> PM (反馈闭环)
    CommunicationProtocol(
        from_role=Role.QA,
        to_role=Role.PM,
        message_types=frozenset(["audit_report", "veto_decision", "feedback"]),
        required_fields=frozenset(["task_id", "verdict"]),
        forbidden_fields=frozenset(["code_patch", "direct_command"]),
    ),
    # Policy -> All (广播)
    CommunicationProtocol(
        from_role=Role.POLICY,
        to_role=Role.PM,  # 代表所有角色
        message_types=frozenset(["policy_violations", "compliance_report"]),
        required_fields=frozenset(["violations"]),
        forbidden_fields=frozenset(["override_flag"]),
    ),
    # FinOps -> All (广播)
    CommunicationProtocol(
        from_role=Role.FINOPS,
        to_role=Role.PM,  # 代表所有角色
        message_types=frozenset(["budget_report", "cost_alerts"]),
        required_fields=frozenset(["usage", "limit"]),
        forbidden_fields=frozenset(["halt_flag"]),
    ),
]


# =============================================================================
# 宪法：反模式定义（不可变）
# =============================================================================


class AntiPattern(str, Enum):
    """已识别的架构反模式。检测到即触发否决。"""

    # 角色越界
    ROLE_OVERREACH = "role_overreach"  # 角色做超出职责的事
    DIRECT_COUPLING = "direct_coupling"  # 角色直接耦合（跳过中间层）
    STATE_SHARING = "state_sharing"  # 角色间共享可变状态

    # 流程破坏
    SKIP_BLUEPRINT = "skip_blueprint"  # Director 跳过蓝图
    SKIP_AUDIT = "skip_audit"  # 绕过 QA 审计
    PM_DIRECT_CODE = "pm_direct_code"  # PM 直接写代码

    # 数据污染
    MODIFY_INPUT = "modify_input"  # 修改输入契约
    PRIVATE_STATE_LEAK = "private_state_leak"  # 私有状态泄露
    CIRCULAR_DEPENDENCY = "circular_dependency"  # 循环依赖

    # 权力滥用
    SELF_APPROVAL = "self_approval"  # 自己批准自己的工作
    QA_WRITES_CODE = "qa_writes_code"  # QA 写代码
    CE_EXECUTES = "ce_executes"  # CE 执行实现


ANTI_PATTERN_SEVERITY: dict[AntiPattern, ViolationLevel] = {
    AntiPattern.ROLE_OVERREACH: ViolationLevel.ERROR,
    AntiPattern.DIRECT_COUPLING: ViolationLevel.ERROR,
    AntiPattern.STATE_SHARING: ViolationLevel.FATAL,
    AntiPattern.SKIP_BLUEPRINT: ViolationLevel.ERROR,
    AntiPattern.SKIP_AUDIT: ViolationLevel.FATAL,
    AntiPattern.PM_DIRECT_CODE: ViolationLevel.ERROR,
    AntiPattern.MODIFY_INPUT: ViolationLevel.WARNING,
    AntiPattern.PRIVATE_STATE_LEAK: ViolationLevel.ERROR,
    AntiPattern.CIRCULAR_DEPENDENCY: ViolationLevel.FATAL,
    AntiPattern.SELF_APPROVAL: ViolationLevel.FATAL,
    AntiPattern.QA_WRITES_CODE: ViolationLevel.FATAL,
    AntiPattern.CE_EXECUTES: ViolationLevel.ERROR,
}


# =============================================================================
# 宪法执行器
# =============================================================================


class ConstitutionEnforcer:
    """宪法执行器 - 运行时检查角色行为是否符合宪法。"""

    def __init__(self, constitution: dict[Role, RoleBoundary] = CONSTITUTION) -> None:
        self.constitution = constitution
        self._violations: list[dict[str, Any]] = []

    def check_role_action(
        self,
        role: Role,
        action: str,
        context: dict[str, Any] | None = None,
    ) -> AntiPattern | None:
        """检查角色行为是否违反宪法。

        Returns:
            如果违反，返回对应的反模式；否则返回 None
        """
        boundary = self.constitution.get(role)
        if not boundary:
            return None

        # 检查禁止行为
        if action in boundary.prohibitions:
            self._record_violation(role, action, AntiPattern.ROLE_OVERREACH)
            return AntiPattern.ROLE_OVERREACH

        return None

    def check_communication(
        self,
        from_role: Role,
        to_role: Role,
        message: dict[str, Any],
    ) -> list[str]:
        """检查通信是否符合宪法。

        Returns:
            违规错误列表，空列表表示合法
        """
        errors = []

        # 检查角色边界
        from_boundary = self.constitution.get(from_role)
        to_boundary = self.constitution.get(to_role)

        if from_boundary and not from_boundary.can_send_to(to_role):
            errors.append(f"Constitution violation: {from_role.value} cannot directly communicate with {to_role.value}")

        if to_boundary and not to_boundary.can_receive_from(from_role):
            errors.append(
                f"Constitution violation: {to_role.value} cannot receive direct communication from {from_role.value}"
            )

        # 检查协议
        for protocol in ALLOWED_COMMUNICATIONS:
            if protocol.from_role == from_role and protocol.to_role == to_role:
                protocol_errors = protocol.validate_message(message)
                errors.extend(protocol_errors)
                break

        return errors

    def check_state_access(
        self,
        accessor_role: Role,
        owner_role: Role,
        state_key: str,
    ) -> bool:
        """检查状态访问是否合法。

        Returns:
            True if access is allowed, False otherwise
        """
        if accessor_role == owner_role:
            return True

        owner_boundary = self.constitution.get(owner_role)
        if not owner_boundary:
            return True

        # 检查是否是私有状态
        if state_key in owner_boundary.private_state_keys:
            self._record_violation(
                accessor_role,
                f"access_private_state:{owner_role.value}.{state_key}",
                AntiPattern.PRIVATE_STATE_LEAK,
            )
            return False

        return True

    def _record_violation(
        self,
        role: Role,
        action: str,
        anti_pattern: AntiPattern,
    ) -> None:
        """记录违规。"""
        self._violations.append(
            {
                "role": role.value,
                "action": action,
                "anti_pattern": anti_pattern.value,
                "severity": ANTI_PATTERN_SEVERITY.get(anti_pattern, ViolationLevel.WARNING).name,
            }
        )

    def get_violations(
        self,
        min_level: ViolationLevel = ViolationLevel.WARNING,
    ) -> list[dict[str, Any]]:
        """获取违规记录。"""
        return [v for v in self._violations if ViolationLevel[v["severity"]].value >= min_level.value]

    def has_fatal_violations(self) -> bool:
        """检查是否有致命违规。"""
        return any(v["severity"] == ViolationLevel.FATAL.name for v in self._violations)


# =============================================================================
# 便捷函数
# =============================================================================


def get_role_boundary(role: Role) -> RoleBoundary | None:
    """获取角色边界定义。"""
    return CONSTITUTION.get(role)


def is_action_allowed(role: Role, action: str) -> bool:
    """检查行为是否被允许。"""
    boundary = CONSTITUTION.get(role)
    if not boundary:
        return False
    return action not in boundary.prohibitions


def get_allowed_downstream(role: Role) -> frozenset[Role]:
    """获取允许的下游角色。"""
    boundary = CONSTITUTION.get(role)
    if not boundary:
        return frozenset()
    return boundary.downstream_roles


def validate_architecture(roles: list[Role]) -> list[str]:
    """验证角色架构是否合法（无循环依赖等）。

    注意：QA->PM 的反馈闭环是架构设计的合法部分，不被视为循环依赖。
    本函数检测的是真正的循环依赖（如 A->B->C->A 的调用链）。

    Returns:
        错误列表，空列表表示架构合法
    """
    errors = []
    role_set: set[Role] = set(roles)

    # 检查循环依赖 - 使用独立的访问跟踪避免反馈闭环误报
    # QA->PM 是合法的反馈闭环，不应被视为循环依赖
    _feedback_loops = frozenset(
        [
            (Role.QA, Role.PM),  # QA 反馈到 PM 是合法的
        ]
    )

    def has_cycle(start_role: Role) -> bool:
        """检测从 start_role 出发是否存在真正的循环依赖。"""
        visited: set[Role] = set()
        path: list[Role] = []

        def dfs(role: Role) -> bool:
            if role in visited:
                # 检查这是否是反馈闭环（合法）还是循环依赖（非法）
                if role in path:
                    # 找到环路，检查是否是允许的反馈闭环
                    cycle_start = path.index(role)
                    cycle = [*path[cycle_start:], role]
                    return any(
                        (cycle[i], cycle[i + 1]) not in _feedback_loops for i in range(len(cycle) - 1)
                    )  # 只是反馈闭环，合法
                return False

            visited.add(role)
            path.append(role)

            boundary = CONSTITUTION.get(role)
            if boundary:
                for downstream in boundary.downstream_roles:
                    if downstream not in role_set:
                        continue
                    if (role, downstream) in _feedback_loops:
                        continue
                    if dfs(downstream):
                        return True

            path.pop()
            return False

        return dfs(start_role)

    for role in roles:
        if has_cycle(role):
            errors.append(f"Circular dependency detected starting from {role.value}")

    return errors


__all__ = [
    "ALLOWED_COMMUNICATIONS",
    "ANTI_PATTERN_SEVERITY",
    # 宪法数据
    "CONSTITUTION",
    "AntiPattern",
    "CommunicationProtocol",
    "ConstitutionEnforcer",
    # 核心枚举
    "Role",
    # 核心类
    "RoleBoundary",
    "ViolationLevel",
    "get_allowed_downstream",
    # 便捷函数
    "get_role_boundary",
    "is_action_allowed",
    "validate_architecture",
]
