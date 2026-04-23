"""ExplorationToolPolicy - 探索工具冷却与预算策略。

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §11 ExplorationToolPolicy

对高频探索工具（glob, ripgrep, list_directory）实施：
    1. 工具级别调用次数追踪（基于语义等效归一化）
    2. 冷却机制（超过阈值后拒绝同类工具调用）
    3. 独立预算限制

语义等效归一化：
    - 同一文件的不同操作被视为"等效"（read_file不同行范围）
    - 同一目录的搜索被视为"等效"（search_code不同pattern）
    - 使用 normalize_tool_signature() 计算语义签名
"""

from __future__ import annotations

import os
from typing import Any

from polaris.cells.roles.kernel.internal.circuit_breaker import normalize_tool_signature

from .core import CanonicalToolCall, PolicyViolation

# 探索工具分类（可配置）
EXPLORATION_TOOL_CATEGORIES: dict[str, frozenset[str]] = {
    "file_search": frozenset(
        [
            "glob",
            "list_directory",
            "ls",
            "find_files",
            "repo_tree",
            "dir_tree",
            "directory_tree",
            "list_dir",
        ]
    ),
    "content_search": frozenset(
        [
            "ripgrep",
            "grep",
            "search",
            "search_code",
            "rg",
            "repo_rg",
            "find",
            "find_pattern",
            "search_files",
            "code_search",
        ]
    ),
    "file_read": frozenset(
        [
            "read_file",
            "read_files",
            "read_multiple_files",
            "cat",
            "view_file",
            "get_file_content",
            "fetch_file",
        ]
    ),
}

# 编辑工具列表 - 用于失败后的诊断读取例外
EDIT_TOOLS: frozenset[str] = frozenset(
    [
        "precision_edit",
        "apply_patch",
        "edit_file",
        "replace",
        "replace_in_file",
        "write_file",
        "create_file",
    ]
)


class ExplorationToolPolicy:
    """探索工具冷却与预算策略。

    Blueprint: §11 ExplorationToolPolicy（新增）

    Phase 3+: 对高频探索工具（glob, ripgrep, list_directory）实施：
        1. 工具级别调用次数追踪
        2. 冷却机制（超过阈值后拒绝同类工具调用）
        3. 独立预算限制
        4. 自适应冷却（Phase 5 新增）：根据任务进度动态调整阈值

    设计原则：
        1. 探索工具分类：将工具按行为特征分类
        2. 频率追踪：追踪每个工具的累计调用次数
        3. 冷却阈值：超过阈值后触发冷却，拒绝后续同类调用
        4. 探索预算：探索工具共享独立预算池
        5. 自适应冷却：根据任务进度允许更多/更少的探索工具调用

    探索工具分类：
        - file_search: glob, list_directory, find_files
        - content_search: ripgrep, grep, search, search_code
        - file_read: read_file, read_multiple_files
        - batch_read: glob + read 组合模式
    """

    def __init__(
        self,
        *,
        max_exploration_calls: int = 32,
        max_calls_per_tool: int = 8,
        cooldown_after_calls: int = 4,
        tool_categories: dict[str, frozenset[str]] | None = None,
        workspace: str = "",
        max_duplicate_actions: int = 3,
        # Phase 5 新增: 自适应冷却参数
        enable_adaptive_cooldown: bool = True,
        min_cooldown_calls: int = 6,
        max_cooldown_calls: int = 16,
    ) -> None:
        """构造探索工具策略。

        Args:
            max_exploration_calls: 探索工具总调用次数上限
            max_calls_per_tool: 单个工具调用次数上限
            cooldown_after_calls: 单个工具调用此次数后进入冷却期
            tool_categories: 工具分类映射（覆盖默认分类）
            workspace: 工作区路径
            max_duplicate_actions: 相同动作（工具+参数）最大重复次数，超过则阻断
            enable_adaptive_cooldown: 启用自适应冷却（根据任务进度调整阈值）
            min_cooldown_calls: 任务前期使用的最小冷却阈值
            max_cooldown_calls: 任务后期使用的最大冷却阈值
        """
        self.max_exploration_calls = max_exploration_calls
        self.max_calls_per_tool = max_calls_per_tool
        self.cooldown_after_calls = cooldown_after_calls
        self.workspace = workspace
        self.max_duplicate_actions = max_duplicate_actions
        # Phase 5 新增
        self.enable_adaptive_cooldown = enable_adaptive_cooldown
        self.min_cooldown_calls = min_cooldown_calls
        self.max_cooldown_calls = max_cooldown_calls

        # 合并自定义分类（允许扩展，不允许覆盖核心分类）
        self._categories: dict[str, frozenset[str]] = dict(EXPLORATION_TOOL_CATEGORIES)
        if tool_categories:
            for cat, tools in tool_categories.items():
                if cat in self._categories:
                    self._categories[cat] = self._categories[cat] | frozenset(tools)
                else:
                    self._categories[cat] = frozenset(tools)

        # 构建工具到分类的反向映射
        self._tool_to_category: dict[str, str] = {}
        for category, tools in self._categories.items():
            for tool in tools:
                self._tool_to_category[tool.lower()] = category

        # 调用统计
        self._tool_call_counts: dict[str, int] = {}
        self._category_call_counts: dict[str, int] = {}
        self._cooldown_tools: set[str] = set()
        self._total_exploration_calls: int = 0

        # 语义等效归一化追踪（Phase 6 新增）
        # 使用语义签名而不是原始工具名来追踪冷却
        self._semantic_counts: dict[str, int] = {}  # semantic_sig -> count
        self._cooldown_sigs: set[str] = set()  # semantic signatures in cooldown
        self._last_semantic_sig: str = ""  # 上次调用的语义签名

        # 重复动作断路器
        self._duplicate_action_counts: dict[str, int] = {}
        self._last_action_signature: str = ""

    @classmethod
    def from_env(cls) -> ExplorationToolPolicy:
        """从环境变量构造（Phase 3 默认值）。"""

        def _int(name: str, default: int, minimum: int, maximum: int) -> int:
            raw = os.environ.get(name, str(default))
            try:
                parsed = int(raw)
            except (TypeError, ValueError):
                parsed = default
            return max(minimum, min(parsed, maximum))

        def _bool(name: str, default: bool) -> bool:
            raw = os.environ.get(name, str(default)).lower()
            return raw in ("true", "1", "yes", "on")

        return cls(
            max_exploration_calls=_int("KERNELONE_EXPLORATION_MAX_CALLS", 32, 1, 256),
            max_calls_per_tool=_int("KERNELONE_EXPLORATION_MAX_CALLS_PER_TOOL", 8, 1, 64),
            cooldown_after_calls=_int("KERNELONE_EXPLORATION_COOLDOWN_AFTER", 4, 1, 32),
            # Phase 5 新增环境变量
            enable_adaptive_cooldown=_bool("KERNELONE_EXPLORATION_ADAPTIVE_COOLDOWN", True),
            min_cooldown_calls=_int("KERNELONE_EXPLORATION_MIN_COOLDOWN", 6, 2, 32),
            max_cooldown_calls=_int("KERNELONE_EXPLORATION_MAX_COOLDOWN", 16, 4, 64),
        )

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any] | None = None) -> ExplorationToolPolicy:
        """从 metadata 构造探索工具策略。

        用于评测场景需要更高/更低的探索工具限制。

        Args:
            metadata: 包含探索工具配置的字典，支持：
                - max_exploration_calls: 探索工具总调用次数上限
                - max_calls_per_tool: 单个工具调用次数上限
                - cooldown_after_calls: 单工具调用此次数后进入冷却期
                - enable_adaptive_cooldown: 启用自适应冷却
                - min_cooldown_calls: 任务前期使用的最小冷却阈值
                - max_cooldown_calls: 任务后期使用的最大冷却阈值
        """
        if not metadata:
            return cls.from_env()

        def _int(key: str, default: int, minimum: int, maximum: int) -> int:
            val = metadata.get(key)
            if val is None:
                return default
            try:
                parsed = int(val)
            except (TypeError, ValueError):
                parsed = default
            return max(minimum, min(parsed, maximum))

        return cls(
            max_exploration_calls=_int("max_exploration_calls", 32, 1, 512),
            max_calls_per_tool=_int("max_calls_per_tool", 8, 1, 128),
            cooldown_after_calls=_int("cooldown_after_calls", 4, 1, 64),
            max_duplicate_actions=_int("max_duplicate_actions", 3, 1, 16),
            # Phase 5 新增参数
            enable_adaptive_cooldown=metadata.get("enable_adaptive_cooldown", True),
            min_cooldown_calls=_int("min_cooldown_calls", 6, 2, 32),
            max_cooldown_calls=_int("max_cooldown_calls", 16, 4, 64),
        )

    def get_tool_category(self, tool_name: str) -> str | None:
        """获取工具所属的探索分类。"""
        return self._tool_to_category.get(tool_name.lower())

    def is_exploration_tool(self, tool_name: str) -> bool:
        """判断是否为探索工具。"""
        return tool_name.lower() in self._tool_to_category

    def is_in_cooldown(self, tool_name: str) -> bool:
        """判断工具是否处于冷却期。

        语义感知：如果同一工具的不同语义操作（如读取不同文件）不应互相阻塞。
        """
        # 首先检查原始工具名冷却（兼容性）
        if tool_name.lower() in self._cooldown_tools:
            return True
        # 然后检查语义签名冷却
        return bool(self._cooldown_sigs)

    def _get_semantic_signature(self, tool_name: str, args: dict[str, Any]) -> str:
        """计算工具调用的语义签名。

        语义签名将同一工具的不同操作（如读取不同文件）归一化为不同签名，
        使它们互相不触发冷却。
        """
        sig = normalize_tool_signature(tool_name, args)
        return f"{sig[0]}:{sig[1]}"

    def _is_semantic_in_cooldown(self, tool_name: str, args: dict[str, Any]) -> bool:
        """判断工具调用的语义签名是否处于冷却期。"""
        sig = self._get_semantic_signature(tool_name, args)
        return sig in self._cooldown_sigs

    def _record_semantic_call(self, tool_name: str, args: dict[str, Any]) -> str:
        """记录语义调用并返回签名。

        Returns:
            语义签名
        """
        sig = self._get_semantic_signature(tool_name, args)
        self._semantic_counts[sig] = self._semantic_counts.get(sig, 0) + 1
        self._last_semantic_sig = sig
        return sig

    def _compute_adaptive_cooldown_threshold(self) -> int:
        """根据任务进度动态计算冷却阈值。

        自适应策略：
        - 如果自适应被禁用，返回 cooldown_after_calls
        - 如果 cooldown_after_calls <= 4（默认值为 4），尊重用户的严格设置
        - 任务前期 (<50% 预算): 使用原始阈值
        - 任务中期 (50%-80%): 使用原始阈值
        - 任务后期 (>80%): 可能轻微增加阈值，确保任务能完成

        注意：此方法只增加阈值，不减少用户设置的严格限制。

        Returns:
            动态计算的冷却阈值。
        """
        if not self.enable_adaptive_cooldown:
            return self.cooldown_after_calls

        # 如果 cooldown_after_calls <= 4（默认值为 4），尊重用户的严格设置
        if self.cooldown_after_calls <= 4:
            return self.cooldown_after_calls

        # 计算预算使用比例
        usage_ratio = self._total_exploration_calls / max(1, self.max_exploration_calls)

        if usage_ratio < 0.8:
            # 任务前期和中期：使用原始阈值
            return self.cooldown_after_calls
        else:
            # 任务后期：允许适度增加阈值（最多 +4）
            return min(self.cooldown_after_calls + 4, self.max_cooldown_calls)

    def evaluate(
        self,
        calls: list[CanonicalToolCall],
        task_metadata: dict[str, Any] | None = None,
    ) -> tuple[list[CanonicalToolCall], list[CanonicalToolCall], list[PolicyViolation]]:
        """评估探索工具调用。

        Args:
            calls: 待评估的工具调用列表。
            task_metadata: 可选的元数据，用于动态策略调整。
                - task_phase: 任务阶段 ("explore", "implement", "verify")

        Returns:
            (approved_calls, blocked_calls, violations)
        """
        approved: list[CanonicalToolCall] = []
        blocked: list[CanonicalToolCall] = []
        violations: list[PolicyViolation] = []

        # 计算自适应冷却阈值
        adaptive_cooldown_threshold = self._compute_adaptive_cooldown_threshold()
        task_phase = task_metadata.get("task_phase") if task_metadata else None

        for call in calls:
            tool_lower = call.tool.lower()

            # 非探索工具直接批准
            if not self.is_exploration_tool(call.tool):
                approved.append(call)
                continue

            # 检查探索总预算
            remaining_exploration = self.max_exploration_calls - self._total_exploration_calls
            if remaining_exploration <= 0:
                blocked.append(call)
                violations.append(
                    PolicyViolation(
                        policy="ExplorationToolPolicy",
                        tool=call.tool,
                        reason=f"exploration budget exhausted: {self._total_exploration_calls}/{self.max_exploration_calls}",
                        is_critical=False,
                    )
                )
                continue

            # 检查工具级别冷却（语义感知）
            # 如果语义签名不同，即使同一工具也不应被冷却阻塞
            in_raw_cooldown = tool_lower in self._cooldown_tools
            in_semantic_cooldown = self._is_semantic_in_cooldown(call.tool, call.args)

            # 验证阶段允许有限度的重复（确保任务能完成）
            if task_phase == "verify" and in_raw_cooldown:
                current_count = self._tool_call_counts.get(tool_lower, 0)
                if current_count < self.max_calls_per_tool:
                    # Verify 阶段允许超出冷却
                    approved.append(call)
                    self._record_tool_call(call.tool, call.tool_key(), tool_args=call.args)
                    violations.append(
                        PolicyViolation(
                            policy="ExplorationToolPolicy",
                            tool=call.tool,
                            reason="verify_phase_override: allowing call in cooldown for task completion",
                            is_critical=False,
                        )
                    )
                    continue

            # BUGFIX: 编辑工具失败后允许一次性的 read_file 诊断读取
            # 防止死循环：edit失败 -> 需要read_file诊断 -> read_file被cooldown阻止 -> 无法修复
            # 这个检查必须在冷却检查之前，因为诊断读取需要绕过冷却
            last_tool_failed = task_metadata.get("last_tool_failed") if task_metadata else None
            if (
                last_tool_failed
                and last_tool_failed.get("tool") in EDIT_TOOLS
                and last_tool_failed.get("failed", False)
                and self.get_tool_category(call.tool) == "file_read"
            ):
                # 允许一次性的诊断读取，但不记录为正式调用（避免影响统计）
                approved.append(call)
                violations.append(
                    PolicyViolation(
                        policy="ExplorationToolPolicy",
                        tool=call.tool,
                        reason=f"diagnostic_read_override: allowing {call.tool} after {last_tool_failed['tool']} failure for diagnosis",
                        is_critical=False,
                    )
                )
                continue

            # 只有在同一语义操作重复时才阻塞
            if in_raw_cooldown and in_semantic_cooldown:
                current_count = self._tool_call_counts.get(tool_lower, 0)
                semantic_sig = self._get_semantic_signature(call.tool, call.args)
                semantic_count = self._semantic_counts.get(semantic_sig, 0)

                blocked.append(call)
                violations.append(
                    PolicyViolation(
                        policy="ExplorationToolPolicy",
                        tool=call.tool,
                        reason=f"tool '{call.tool}' semantic cooldown: sig='{semantic_sig[:20]}...' count={semantic_count} threshold={adaptive_cooldown_threshold}",
                        is_critical=False,
                    )
                )
                continue

            # 检查单工具调用次数
            current_count = self._tool_call_counts.get(tool_lower, 0)
            if current_count >= self.max_calls_per_tool:
                blocked.append(call)
                violations.append(
                    PolicyViolation(
                        policy="ExplorationToolPolicy",
                        tool=call.tool,
                        reason=f"tool '{call.tool}' call limit exceeded: {current_count}/{self.max_calls_per_tool}",
                        is_critical=False,
                    )
                )
                continue

            # 检查重复动作断路器（相同工具+参数）
            action_sig = call.tool_key()
            if action_sig == self._last_action_signature:
                self._duplicate_action_counts[action_sig] = self._duplicate_action_counts.get(action_sig, 1) + 1
                if self._duplicate_action_counts[action_sig] > self.max_duplicate_actions:
                    blocked.append(call)
                    violations.append(
                        PolicyViolation(
                            policy="ExplorationToolPolicy",
                            tool=call.tool,
                            reason=f"duplicate action loop detected: same call repeated {self._duplicate_action_counts[action_sig]} times (limit={self.max_duplicate_actions}). Use a different tool or approach.",
                            is_critical=False,
                        )
                    )
                    continue
            else:
                # 不同动作，清除该签名的重复计数
                self._duplicate_action_counts.pop(action_sig, None)

            # 批准并更新统计
            approved.append(call)
            self._record_tool_call(
                call.tool, action_sig, adaptive_threshold=adaptive_cooldown_threshold, tool_args=call.args
            )

        return approved, blocked, violations

    def _record_tool_call(
        self,
        tool_name: str,
        action_sig: str = "",
        adaptive_threshold: int | None = None,
        tool_args: dict[str, Any] | None = None,
    ) -> None:
        """记录一次工具调用并更新统计。

        Args:
            tool_name: 工具名称
            action_sig: 动作签名（tool:args），用于重复动作检测
            adaptive_threshold: 自适应冷却阈值（可选）
            tool_args: 工具参数（用于语义签名计算）
        """
        tool_lower = tool_name.lower()
        self._tool_call_counts[tool_lower] = self._tool_call_counts.get(tool_lower, 0) + 1
        self._total_exploration_calls += 1

        # 更新分类统计
        category = self.get_tool_category(tool_name)
        if category:
            self._category_call_counts[category] = self._category_call_counts.get(category, 0) + 1

        # 语义等效归一化：记录并检查语义签名
        sig = self._record_semantic_call(tool_lower, tool_args or {})

        # 检查是否需要进入冷却（使用自适应阈值）
        # 语义签名冷却：只有相同语义操作才触发冷却
        semantic_count = self._semantic_counts.get(sig, 0)
        effective_threshold = adaptive_threshold if adaptive_threshold is not None else self.cooldown_after_calls
        if semantic_count >= effective_threshold:
            self._cooldown_sigs.add(sig)

        # 同时保留原始工具冷却（兼容性）
        current_count = self._tool_call_counts.get(tool_lower, 0)
        if current_count >= effective_threshold:
            self._cooldown_tools.add(tool_lower)

        # 更新最后动作签名
        if action_sig:
            self._last_action_signature = action_sig

    def get_stats(self) -> dict[str, Any]:
        """返回当前统计信息。"""
        return {
            "total_exploration_calls": self._total_exploration_calls,
            "max_exploration_calls": self.max_exploration_calls,
            "tool_call_counts": dict(self._tool_call_counts),
            "category_call_counts": dict(self._category_call_counts),
            "tools_in_cooldown": list(self._cooldown_tools),
            "semantic_counts": dict(self._semantic_counts),
            "cooldown_sigs": list(self._cooldown_sigs),
            "duplicate_action_counts": dict(self._duplicate_action_counts),
            "last_action_signature": self._last_action_signature,
            "last_semantic_sig": self._last_semantic_sig,
        }

    def reset(self) -> None:
        """重置统计状态。"""
        self._tool_call_counts.clear()
        self._category_call_counts.clear()
        self._cooldown_tools.clear()
        self._total_exploration_calls = 0
        # 语义追踪重置
        self._semantic_counts.clear()
        self._cooldown_sigs.clear()
        self._last_semantic_sig = ""
        self._duplicate_action_counts.clear()
        self._last_action_signature = ""


__all__ = [
    "EXPLORATION_TOOL_CATEGORIES",
    "ExplorationToolPolicy",
]
