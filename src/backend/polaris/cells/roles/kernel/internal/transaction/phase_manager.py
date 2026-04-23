"""PhaseManager — 基于物理副作用的确定性阶段管理器。

FIX-20250421: 彻底替换字符串匹配的 Phase 判定，用工具实际执行结果驱动状态机。
所有组件通过 PhaseManager 查询当前阶段，不再各自猜测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from polaris.cells.roles.kernel.internal.transaction.write_authority import (
    extract_target_path_from_payload,
    is_authoritative_write_path,
)


class Phase(str, Enum):
    """系统执行阶段枚举 — 这是唯一合法的阶段定义。

    状态流转规则：
    - EXPLORING: 允许 broad exploration（glob/repo_rg/repo_tree）
    - CONTENT_GATHERED: 已真实读取文件内容（read_file/repo_read_*）
    - IMPLEMENTING: 已执行写操作（write_file/edit_file/apply_diff）
    - VERIFYING: 进入验证阶段（执行测试/验证）
    - DONE: 任务完成

    单向流转：EXPLORING → CONTENT_GATHERED → IMPLEMENTING → VERIFYING → DONE
    特殊情况：任何阶段都可以直接 DONE（final_answer）
    """

    EXPLORING = "exploring"
    CONTENT_GATHERED = "content_gathered"
    IMPLEMENTING = "implementing"
    VERIFYING = "verifying"
    DONE = "done"


# 工具分类定义 — 这是唯一的工具类型真相来源
_BROAD_EXPLORATION_TOOLS: frozenset[str] = frozenset(
    {
        "glob",
        "repo_tree",
        "repo_rg",
        "grep",
        "search_code",
        "ripgrep",
        "find",
    }
)

_READ_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "repo_read_head",
        "repo_read_slice",
        "repo_read_tail",
        "repo_read_around",
        "repo_read_range",
    }
)

_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "write_file",
        "edit_file",
        "edit_blocks",
        "precision_edit",
        "search_replace",
        "apply_diff",
        "repo_apply_diff",
        "append_to_file",
    }
)

_VERIFICATION_TOOLS: frozenset[str] = frozenset(
    {
        "execute_command",
        "pytest",
        "npm_test",
        "run_tests",
        "python",
        "node",
    }
)

# FIX-20250422: 每个阶段允许的工具白名单 —— 架构级防死循环
_PHASE_TOOL_WHITELIST: dict[Phase, frozenset[str]] = {
    Phase.EXPLORING: frozenset(
        {
            "glob",
            "repo_tree",
            "repo_rg",
            "grep",
            "search_code",
            "ripgrep",
            "find",
            "read_file",
            "repo_read_head",
            "repo_read_slice",
            "repo_read_tail",
            "repo_read_around",
            "repo_read_range",
        }
    ),
    Phase.CONTENT_GATHERED: frozenset(
        {
            # CONTENT_GATHERED 阶段：只允许写工具和验证工具
            # 禁止所有读工具和探索工具，防止无限重读死循环
            "write_file",
            "edit_file",
            "edit_blocks",
            "precision_edit",
            "search_replace",
            "apply_diff",
            "repo_apply_diff",
            "append_to_file",
            "execute_command",
            "pytest",
            "npm_test",
            "run_tests",
            "python",
            "node",
            "final_answer",
            "ask_user",
        }
    ),
    Phase.IMPLEMENTING: frozenset(
        {
            "write_file",
            "edit_file",
            "edit_blocks",
            "precision_edit",
            "search_replace",
            "apply_diff",
            "repo_apply_diff",
            "append_to_file",
            "execute_command",
            "pytest",
            "npm_test",
            "run_tests",
            "python",
            "node",
            "final_answer",
            "ask_user",
        }
    ),
    Phase.VERIFYING: frozenset(
        {
            "execute_command",
            "pytest",
            "npm_test",
            "run_tests",
            "python",
            "node",
            "final_answer",
            "ask_user",
        }
    ),
    Phase.DONE: frozenset(),  # 终态，不允许任何工具
}


@dataclass(frozen=True)
class ToolResult:
    """工具执行结果的轻量表示 — 用于驱动 Phase 流转。"""

    tool_name: str
    success: bool = True
    bytes_read: int = 0  # 对于 read 工具，实际读取的字节数
    is_write: bool = False  # 是否有写副作用
    is_authoritative_write: bool = False  # 是否满足 materialize 语义
    target_path: str = ""  # 目标文件（若可解析）

    @classmethod
    def from_batch_result(cls, result: dict[str, Any]) -> ToolResult:
        """从 batch_receipt 的 result 条目构造。"""
        tool_name = str(result.get("tool_name", ""))
        status = result.get("status", "")
        success = status == "success"

        # 计算 bytes_read（如果是 read 工具且成功）
        bytes_read = 0
        if success and tool_name in _READ_TOOLS:
            result_data = result.get("result")
            if isinstance(result_data, dict):
                content = result_data.get("content", "")
                if isinstance(content, str):
                    bytes_read = len(content.encode("utf-8"))
            elif isinstance(result_data, str):
                bytes_read = len(result_data.encode("utf-8"))

        # 判断是否有写副作用
        is_write = tool_name in _WRITE_TOOLS
        target_path = extract_target_path_from_payload(result) or ""
        is_authoritative_write = is_write and is_authoritative_write_path(target_path)

        return cls(
            tool_name=tool_name,
            success=success,
            bytes_read=bytes_read,
            is_write=is_write,
            is_authoritative_write=is_authoritative_write,
            target_path=target_path,
        )


@dataclass
class PhaseManager:
    """阶段管理器 — 基于物理副作用驱动状态流转。

    核心原则：
    1. 只听工具执行的事实，不听 LLM 的宣称
    2. 只有真正读取了文件内容才算 CONTENT_GATHERED
    3. 只有执行了写工具才算 IMPLEMENTING

    Usage:
        pm = PhaseManager()

        # Turn 0: glob + repo_rg
        pm.transition([ToolResult("glob"), ToolResult("repo_rg")])
        assert pm.current_phase == Phase.EXPLORING  # 没变！

        # Turn 1: read_file
        pm.transition([ToolResult("read_file", bytes_read=1024)])
        assert pm.current_phase == Phase.CONTENT_GATHERED
    """

    _current_phase: Phase = field(default=Phase.EXPLORING)
    _phase_history: list[tuple[Phase, list[str]]] = field(default_factory=list)
    # FIX-20250421-v3: 阶段停留计数器（用于超时熔断）
    _turns_in_current_phase: int = 0
    _max_turns_per_phase: int = 3

    @property
    def current_phase(self) -> Phase:
        """当前阶段 — 只读。"""
        return self._current_phase

    @property
    def phase_history(self) -> list[tuple[Phase, list[str]]]:
        """阶段流转历史 — 用于审计。"""
        return self._phase_history.copy()

    def transition(self, tool_results: list[ToolResult]) -> Phase:
        """唯一合法的状态流转入口。

        根据工具执行结果推进阶段：
        - 任何成功的写工具 → IMPLEMENTING
        - 任何读取了内容的 read 工具 → CONTENT_GATHERED
        - 探索工具（glob/repo_rg）不推进状态

        Args:
            tool_results: 本 Turn 执行的所有工具结果

        Returns:
            流转后的新阶段（可能不变）
        """
        tool_names = [r.tool_name for r in tool_results if r.success]

        # 规则 1: 任何写操作 → IMPLEMENTING
        if any(r.is_authoritative_write for r in tool_results if r.success):
            if self._current_phase != Phase.IMPLEMENTING:
                self._phase_history.append((self._current_phase, tool_names))
                self._current_phase = Phase.IMPLEMENTING
                self._turns_in_current_phase = 1
            else:
                self._turns_in_current_phase += 1
            return self._current_phase

        # 规则 2: 任何验证工具 → VERIFYING（只在已实现后才允许）
        if self._current_phase in {Phase.IMPLEMENTING, Phase.CONTENT_GATHERED} and any(
            r.tool_name in _VERIFICATION_TOOLS for r in tool_results if r.success
        ):
            if self._current_phase != Phase.VERIFYING:
                self._phase_history.append((self._current_phase, tool_names))
                self._current_phase = Phase.VERIFYING
                self._turns_in_current_phase = 1
            else:
                self._turns_in_current_phase += 1
            return self._current_phase

        # 规则 3: 真正读取了文件内容 → CONTENT_GATHERED
        if any(r.bytes_read > 0 for r in tool_results if r.success):
            if self._current_phase == Phase.EXPLORING:  # 只能单向推进
                self._phase_history.append((self._current_phase, tool_names))
                self._current_phase = Phase.CONTENT_GATHERED
                self._turns_in_current_phase = 1
            else:
                self._turns_in_current_phase += 1
            return self._current_phase

        # 探索工具不推进状态，但增加停留计数
        self._turns_in_current_phase += 1
        return self._current_phase

    def can_transition_to(self, target_phase: Phase) -> bool:
        """检查是否允许从当前阶段流转到目标阶段。

        主要用于防御 LLM 幻觉（模型声称要进入某阶段）。
        """
        if target_phase == self._current_phase:
            return True

        # 定义允许的流转
        allowed_transitions: dict[Phase, set[Phase]] = {
            Phase.EXPLORING: {Phase.CONTENT_GATHERED, Phase.DONE},
            Phase.CONTENT_GATHERED: {Phase.IMPLEMENTING, Phase.DONE},
            Phase.IMPLEMENTING: {Phase.VERIFYING, Phase.DONE},
            Phase.VERIFYING: {Phase.DONE},
            Phase.DONE: set(),  # 终态
        }

        return target_phase in allowed_transitions.get(self._current_phase, set())

    def validate_tools_for_phase(self, tool_results: list[ToolResult]) -> tuple[bool, str]:
        """验证工具组合是否符合当前阶段的约束。

        Returns:
            (是否合法, 错误信息)
        """
        # EXPLORING 阶段：允许探索，但警告如果只探索不读取
        if self._current_phase == Phase.EXPLORING:
            has_read = any(r.bytes_read > 0 for r in tool_results if r.success)
            if not has_read and len(tool_results) > 0:
                # 只有探索工具 → 提示需要读取
                return True, "提示：你已通过探索工具定位文件，下一步请调用 read_file 读取内容"
            return True, ""

        # CONTENT_GATHERED 阶段：允许读取验证，但必须开始写操作
        if self._current_phase == Phase.CONTENT_GATHERED:
            has_write = any(r.is_authoritative_write for r in tool_results if r.success)
            has_read = any(r.bytes_read > 0 for r in tool_results if r.success)
            has_explore = any(r.tool_name in _BROAD_EXPLORATION_TOOLS for r in tool_results if r.success)

            if has_explore and not has_write:
                return False, (
                    "阶段错误：你已在 CONTENT_GATHERED 阶段（已读取过文件），"
                    "不应继续使用探索工具（glob/repo_rg）。"
                    "请直接调用 write_file/edit_file 执行修改。"
                )

            # FIX-20250422: 架构级防死循环 —— CONTENT_GATHERED 阶段硬阻止无限重读
            # 如果已连续多次只读不写（turns_in_phase > max_turns_per_phase），
            # 返回 False 硬阻止工具执行，强制 LLM 进入写阶段或收口
            if has_read and not has_write and self._turns_in_current_phase > self._max_turns_per_phase:
                return False, (
                    f"阶段强制终止：你已在 CONTENT_GATHERED 阶段停留 {self._turns_in_current_phase} 个回合，"
                    f"超过最大限制 {self._max_turns_per_phase}。"
                    f"系统已强制阻止所有读工具。"
                    f"你必须立即调用 write_file/edit_file 执行修改，或输出 final_answer 结束任务。"
                )

            # 软性警告：如果已连续多次只读不写，但尚未超过硬限制
            if has_read and not has_write and self._turns_in_current_phase >= 2:
                return True, (
                    f"警告：你已在 CONTENT_GATHERED 阶段停留 {self._turns_in_current_phase} 个回合，"
                    f"但仍未执行任何写操作。这是第 {self._turns_in_current_phase} 次读取。"
                    f"系统将在 {self._max_turns_per_phase} 次尝试后强制终止。"
                    f"请立即调用 write_file/edit_file 执行修改！"
                )
            if has_read and not has_write:
                return True, (
                    "[DEBUG][FIX-20250422] validate_tools_for_phase: "
                    f"CONTENT_GATHERED phase, read_only batch, turns_in_phase={self._turns_in_current_phase}. "
                    "Allowed this turn but write tool REQUIRED next turn."
                )
            return True, ""

        # IMPLEMENTING 阶段：禁止 broad exploration
        if self._current_phase == Phase.IMPLEMENTING:
            has_explore = any(r.tool_name in _BROAD_EXPLORATION_TOOLS for r in tool_results if r.success)
            if has_explore:
                return False, (
                    "阶段错误：当前在 IMPLEMENTING 阶段（已开始修改），"
                    "禁止使用探索工具（glob/repo_rg/repo_tree）。"
                    "请完成当前修改或调用 final_answer 结束。"
                )
            return True, ""

        return True, ""

    def get_phase_constraint_prompt(self) -> str:
        """获取当前阶段的约束提示语 — 注入到 System Prompt。"""
        constraints: dict[Phase, str] = {
            Phase.EXPLORING: (
                "当前阶段：探索（EXPLORING）。允许使用 glob/repo_rg 定位文件，但必须在下一回合调用 read_file 读取内容。"
            ),
            Phase.CONTENT_GATHERED: (
                "当前阶段：内容已收集（CONTENT_GATHERED）。你已读取文件，"
                "现在必须调用 write_file/edit_file 执行修改，禁止继续探索。"
            ),
            Phase.IMPLEMENTING: (
                "当前阶段：实现（IMPLEMENTING）。你正在执行修改，严禁使用 glob/repo_rg 等探索工具，请专注完成写入。"
            ),
            Phase.VERIFYING: ("当前阶段：验证中（VERIFYING）。请运行测试或验证修复效果。"),
            Phase.DONE: "当前阶段：已完成（DONE）。",
        }
        return constraints.get(self._current_phase, "继续执行任务。")

    def is_phase_timeout(self) -> tuple[bool, str]:
        """检查当前阶段是否超时（停留超过 max_turns_per_phase）。

        Returns:
            (是否超时, 超时提示信息)
        """
        if self._turns_in_current_phase > self._max_turns_per_phase:
            return True, (
                f"阶段超时警告：你已在 {self._current_phase.value} 阶段停留 "
                f"{self._turns_in_current_phase} 个回合（超过最大限制 {self._max_turns_per_phase}）。"
                "请立即推进到下一阶段或结束任务。"
            )
        return False, ""

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（用于跨 Turn 持久化）。"""
        return {
            "current_phase": self._current_phase.value,
            "phase_history": [{"from": phase.value, "tools": tools} for phase, tools in self._phase_history],
            "turns_in_current_phase": self._turns_in_current_phase,
            "max_turns_per_phase": self._max_turns_per_phase,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PhaseManager:
        """从 dict 反序列化（用于跨 Turn 恢复）。"""
        pm = cls()
        pm._current_phase = Phase(data.get("current_phase", "exploring"))
        pm._turns_in_current_phase = data.get("turns_in_current_phase", 0)
        pm._max_turns_per_phase = data.get("max_turns_per_phase", 3)
        _history = data.get("phase_history", [])
        pm._phase_history = [(Phase(item["from"]), item.get("tools", [])) for item in _history]
        return pm


# 便捷函数：从 batch_receipt 构造 ToolResult 列表
def extract_tool_results_from_batch_receipt(batch_receipt: dict[str, Any] | None) -> list[ToolResult]:
    """从 batch_receipt 提取所有工具结果。"""
    if not batch_receipt:
        return []

    results = batch_receipt.get("results") or batch_receipt.get("raw_results") or []
    return [ToolResult.from_batch_result(r) for r in results if isinstance(r, dict)]


def has_authoritative_write_receipt(batch_receipt: dict[str, Any] | None) -> bool:
    """Return True when at least one successful authoritative write exists in the receipt."""
    return any(
        result.success and result.is_authoritative_write
        for result in extract_tool_results_from_batch_receipt(batch_receipt)
    )
