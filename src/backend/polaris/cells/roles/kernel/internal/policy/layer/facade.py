"""PolicyLayer - 统一策略层 facade。

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §11 PolicyLayer

组合 ToolPolicy + BudgetPolicy + ApprovalPolicy + SandboxPolicy +
RedactionPolicy + ExplorationToolPolicy，提供单一 evaluate() 接口供 TurnEngine 调用。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from .approval import ApprovalPolicy
from .budget import BudgetPolicy
from .core import CanonicalToolCall, PolicyResult, PolicyViolation
from .exploration import ExplorationToolPolicy
from .redaction import RedactionPolicy
from .sandbox import SandboxPolicy
from .tool import ToolPolicy

if TYPE_CHECKING:
    from polaris.cells.roles.profile.internal.schema import RoleProfile


class PolicyLayer:
    """统一策略层。

    Blueprint: §11 PolicyLayer

    Phase 3: 组合 ToolPolicy + BudgetPolicy + ApprovalPolicy + SandboxPolicy +
              RedactionPolicy + ExplorationToolPolicy。
    提供单一 evaluate() 接口供 TurnEngine 调用。

    设计约束：
        1. 所有子策略按固定顺序评估：
           ToolPolicy → BudgetPolicy → ExplorationToolPolicy → ApprovalPolicy → SandboxPolicy
        2. 顺序很重要：ToolPolicy 先过滤非法工具，再 BudgetPolicy 做预算检查，
           ExplorationToolPolicy 对探索工具实施冷却机制
        3. PolicyResult.violations 累积所有子策略的违规
        4. stop_reason 由 BudgetPolicy 设置（预算耗尽/stall），ExplorationToolPolicy/
           ToolPolicy/SandboxPolicy 不设置 stop_reason

    使用示例::

        layer = PolicyLayer.from_kernel(kernel, profile, workspace=".")
        result = layer.evaluate(
            [CanonicalToolCall(tool="read_file", args={"path": "README.md"})],
            budget_state={"tool_call_count": 0, "turn_count": 0, ...},
        )
        if result.stop_reason:
            return result.stop_reason
        for call in result.blocked_calls:
            print(f"Blocked: {call.tool} — {result.violations}")
    """

    __slots__ = (
        "_last_cycle_signature",
        "_stall_count",
        "_tool_call_count",
        "_total_tokens",
        "_turn_count",
        "_wall_time_started",
        "approval_policy",
        "budget_policy",
        "exploration_policy",
        "redaction_policy",
        "sandbox_policy",
        "tool_policy",
    )

    def __init__(
        self,
        tool_policy: ToolPolicy,
        budget_policy: BudgetPolicy,
        approval_policy: ApprovalPolicy,
        sandbox_policy: SandboxPolicy,
        redaction_policy: RedactionPolicy,
        exploration_policy: ExplorationToolPolicy | None = None,
    ) -> None:
        self.tool_policy = tool_policy
        self.budget_policy = budget_policy
        self.approval_policy = approval_policy
        self.sandbox_policy = sandbox_policy
        self.redaction_policy = redaction_policy
        self.exploration_policy = exploration_policy or ExplorationToolPolicy.from_env()

        # 状态累积（跨 evaluate 调用）
        self._tool_call_count: int = 0
        self._turn_count: int = 0
        self._total_tokens: int = 0
        self._wall_time_started: float = time.monotonic()
        self._stall_count: int = 0
        self._last_cycle_signature: str = ""

    @classmethod
    def from_kernel(
        cls,
        kernel: Any,
        profile: RoleProfile,
        workspace: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> PolicyLayer:
        """从 RoleExecutionKernel 构造 PolicyLayer。

        从 kernel._tool_gateways 或 profile 提取策略配置。
        这是 TurnEngine.__init__ 中构造 PolicyLayer 的标准方式。

        Args:
            kernel: RoleExecutionKernel 实例。
            profile: RoleProfile 实例。
            workspace: 工作区路径。
            metadata: 可选的元数据，用于传递运行时配置（如 max_total_tool_calls）。
        """
        tool_policy = ToolPolicy.from_profile(profile, workspace=workspace)
        # 如果 metadata 中有工具循环配置，使用它；否则从环境变量读取
        if metadata:
            budget_policy = BudgetPolicy.from_metadata(metadata)
            exploration_policy = ExplorationToolPolicy.from_metadata(metadata)
        else:
            budget_policy = BudgetPolicy.from_env()
            exploration_policy = ExplorationToolPolicy.from_env()
        approval_policy = ApprovalPolicy.from_env()
        sandbox_policy = SandboxPolicy.from_env()
        redaction_policy = RedactionPolicy.from_env()
        return cls(
            tool_policy=tool_policy,
            budget_policy=budget_policy,
            approval_policy=approval_policy,
            sandbox_policy=sandbox_policy,
            redaction_policy=redaction_policy,
            exploration_policy=exploration_policy,
        )

    def evaluate(
        self,
        calls: list[CanonicalToolCall],
        *,
        budget_state: dict[str, Any] | None = None,
        precheck_stall_count: int | None = None,
        task_metadata: dict[str, Any] | None = None,
    ) -> PolicyResult:
        """评估工具调用列表。

        执行顺序：
            1. ToolPolicy   → 过滤黑名单/白名单/权限
            2. BudgetPolicy → 预算 + stall 检查
            3. ExplorationToolPolicy → 探索工具冷却机制
            4. ApprovalPolicy → 标记需审批的调用
            5. SandboxPolicy → 沙箱边界检查

        Args:
            calls: 待评估的 CanonicalToolCall 列表。
            budget_state: 可选，预算快照（用于增量更新）。
            precheck_stall_count: 可选，预检查的 stall 计数。

        Returns:
            PolicyResult — 包含 approved_calls, blocked_calls,
                          requires_approval, stop_reason, violations, exploration_stats。
        """
        if not calls:
            return PolicyResult(
                budget_state=self.budget_policy.budget_snapshot(
                    self._tool_call_count,
                    self._turn_count,
                    self._total_tokens,
                    self._get_wall_time(),
                    self._stall_count,
                ),
                exploration_stats=self.exploration_policy.get_stats(),
            )

        # 从 budget_state 恢复状态（如有）
        if budget_state:
            self._tool_call_count = int(budget_state.get("tool_call_count", 0))
            self._turn_count = int(budget_state.get("turn_count", 0))
            self._total_tokens = int(budget_state.get("total_tokens", 0))
            self._stall_count = int(budget_state.get("stall_count", 0))

        all_violations: list[PolicyViolation] = []
        current: list[CanonicalToolCall] = list(calls)

        # ── 1. ToolPolicy ────────────────────────────────────────────────────
        approved, blocked, tool_violations = self.tool_policy.evaluate(current)
        all_violations.extend(tool_violations)

        # ── 2. BudgetPolicy ─────────────────────────────────────────────────
        budget_approved, budget_blocked, stop_reason, budget_violations = self.budget_policy.evaluate(
            approved,
            tool_call_count=self._tool_call_count,
            turn_count=self._turn_count,
            total_tokens=self._total_tokens,
            wall_time_seconds=self._get_wall_time(),
            stall_count=precheck_stall_count if precheck_stall_count is not None else self._stall_count,
        )
        all_violations.extend(budget_violations)

        # precheck_stall_count 已由 precheck_stall() 处理；仅在未调用时更新
        if precheck_stall_count is None:
            if stop_reason and "stalled" in stop_reason:
                self._stall_count += 1
            elif budget_approved:
                sig = BudgetPolicy.compute_cycle_signature(budget_approved, [])
                if sig == self._last_cycle_signature:
                    self._stall_count += 1
                else:
                    self._stall_count = 0
                self._last_cycle_signature = sig

        # 更新 tool_call_count
        self._tool_call_count += len(budget_approved)

        # BudgetPolicy 拦截的调用
        for call in budget_blocked:
            all_violations.append(
                PolicyViolation(
                    policy="BudgetPolicy",
                    tool=call.tool,
                    reason=stop_reason or "budget exceeded",
                    is_critical=True,
                )
            )

        # ── 3. ExplorationToolPolicy（仅对 budget_approved 调用）────────────
        exploration_approved, exploration_blocked, exploration_violations = self.exploration_policy.evaluate(
            budget_approved,
            task_metadata=task_metadata,
        )
        all_violations.extend(exploration_violations)

        # ExplorationToolPolicy 拦截的调用（记录但不作为严重违规）
        for call in exploration_blocked:
            all_violations.append(
                PolicyViolation(
                    policy="ExplorationToolPolicy",
                    tool=call.tool,
                    reason="exploration tool cooldown or budget exceeded",
                    is_critical=False,
                )
            )

        # ── 4. ApprovalPolicy（仅对 exploration_approved 调用）──────────────
        auto_approved, needs_approval, approval_violations = self.approval_policy.evaluate(exploration_approved)
        all_violations.extend(approval_violations)

        # ── 5. SandboxPolicy（仅对 auto_approved 调用）────────────────────
        sandbox_approved, sandbox_blocked, sandbox_violations = self.sandbox_policy.evaluate(auto_approved)
        all_violations.extend(sandbox_violations)

        # SandboxPolicy 拦截的调用
        for call in sandbox_blocked:
            all_violations.append(
                PolicyViolation(
                    policy="SandboxPolicy",
                    tool=call.tool,
                    reason="sandbox constraint violated",
                    is_critical=True,
                )
            )

        return PolicyResult(
            approved_calls=sandbox_approved,
            blocked_calls=list(blocked) + budget_blocked + exploration_blocked + sandbox_blocked,
            requires_approval=needs_approval,
            stop_reason=stop_reason,
            violations=tuple(all_violations),
            budget_state=self.budget_policy.budget_snapshot(
                self._tool_call_count,
                self._turn_count,
                self._total_tokens,
                self._get_wall_time(),
                self._stall_count,
            ),
            exploration_stats=self.exploration_policy.get_stats(),
        )

    def record_turn(self) -> None:
        """记录一次 LLM 调用（更新 turn_count）。"""
        self._turn_count += 1

    def precheck_stall(self, calls: list[CanonicalToolCall]) -> int:
        """预检查 stall 条件（在工具执行前调用）。

        计算当前 calls 的 cycle_signature，与 _last_cycle_signature 比对，
        然后更新 _stall_count。返回更新后的 stall_count 值（供 BudgetPolicy 使用）。

        语义与 ToolLoopController.register_cycle() 一致：
        _stall_cycles > max_stall_cycles 时停止；stall_cycles=0 允许第 1 个相同 cycle。

        Args:
            calls: 当前 cycle 的工具调用列表。

        Returns:
            更新后的 _stall_count 值。
        """
        if not calls:
            return self._stall_count
        sig = BudgetPolicy.compute_cycle_signature(calls, [])
        if sig and sig == self._last_cycle_signature:
            self._stall_count += 1
        else:
            self._stall_count = 0
        self._last_cycle_signature = sig
        return self._stall_count

    def reset(self) -> None:
        """重置累积状态（新 turn 开始时调用）。"""
        self._tool_call_count = 0
        self._turn_count = 0
        self._total_tokens = 0
        self._stall_count = 0
        self._last_cycle_signature = ""
        self._wall_time_started = time.monotonic()
        self.approval_policy.clear_pending()
        self.exploration_policy.reset()

    def _get_wall_time(self) -> float:
        return time.monotonic() - self._wall_time_started


__all__ = [
    "PolicyLayer",
]
