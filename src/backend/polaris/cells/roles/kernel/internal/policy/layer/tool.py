"""ToolPolicy - 工具权限策略。

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §11 ToolPolicy

封装 RoleToolGateway 的权限检查逻辑，提供独立的 evaluate() 接口。
"""

from __future__ import annotations

import fnmatch
import logging
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from polaris.kernelone.security.dangerous_patterns import is_dangerous_command
from polaris.kernelone.tool_execution.tool_categories import (
    is_code_write_tool,
    is_command_execution_tool,
    is_file_delete_tool,
)

from .core import CanonicalToolCall, PolicyViolation

if TYPE_CHECKING:
    from polaris.cells.roles.profile.internal.schema import RoleProfile

logger = logging.getLogger(__name__)


# NOTE: TOOL_CATEGORIES removed - use tool_categories module as SSOT


@dataclass(slots=True)
class ToolPolicyConfig:
    """工具策略配置。"""

    whitelist: list[str] = field(default_factory=list)
    blacklist: list[str] = field(default_factory=list)
    allow_code_write: bool = False
    allow_command_execution: bool = False
    allow_file_delete: bool = False
    max_tool_calls_per_turn: int = 64
    workspace: str = ""
    policy_id: str = ""


class ToolPolicy:
    """工具权限策略。

    Blueprint: §11 ToolPolicy

    封装 RoleToolGateway 的权限检查逻辑，提供独立的 evaluate() 接口。
    与 RoleToolGateway 的区别：
        - RoleToolGateway: 执行工具 + 权限检查（副作用）
        - ToolPolicy: 仅评估权限（无副作用，纯函数风格）

    Phase 3 基于 RoleProfile.tool_policy 初始化。
    Phase 4 支持从 ToolRegistry 加载额外工具约束。
    """

    def __init__(
        self,
        *,
        whitelist: list[str] | None = None,
        blacklist: list[str] | None = None,
        allow_code_write: bool = False,
        allow_command_execution: bool = False,
        allow_file_delete: bool = False,
        max_tool_calls_per_turn: int = 64,
        workspace: str = "",
        policy_id: str = "",
    ) -> None:
        self.whitelist = whitelist or []
        self.blacklist = blacklist or []
        self.allow_code_write = allow_code_write
        self.allow_command_execution = allow_command_execution
        self.allow_file_delete = allow_file_delete
        self.max_tool_calls_per_turn = max_tool_calls_per_turn
        self.workspace = workspace
        self.policy_id = policy_id

        # 规范化集合
        self._whitelist_lower = {t.lower() for t in self.whitelist}
        self._blacklist_lower = {t.lower() for t in self.blacklist}

    @classmethod
    def from_profile(cls, profile: RoleProfile, workspace: str = "") -> ToolPolicy:
        """从 RoleProfile 构造 ToolPolicy。

        使用 getattr() 提供防御性默认值，容忍部分 tool_policy 配置
        （如测试 fixture 仅提供 whitelist 而无 blacklist）。
        """
        tp = profile.tool_policy
        return cls(
            whitelist=list(getattr(tp, "whitelist", [])),
            blacklist=list(getattr(tp, "blacklist", [])),
            allow_code_write=getattr(tp, "allow_code_write", True),
            allow_command_execution=getattr(tp, "allow_command_execution", True),
            allow_file_delete=getattr(tp, "allow_file_delete", True),
            max_tool_calls_per_turn=getattr(tp, "max_tool_calls_per_turn", 20),
            workspace=workspace,
            policy_id=getattr(tp, "policy_id", ""),
        )

    def evaluate(
        self,
        calls: list[CanonicalToolCall],
    ) -> tuple[list[CanonicalToolCall], list[CanonicalToolCall], list[PolicyViolation]]:
        """评估工具调用权限。

        Args:
            calls: 待评估的工具调用列表。

        Returns:
            (approved_calls, blocked_calls, violations)
        """
        approved: list[CanonicalToolCall] = []
        blocked: list[CanonicalToolCall] = []
        violations: list[PolicyViolation] = []

        for call in calls:
            tool_lower = call.tool.lower()
            v = self._evaluate_single(call, tool_lower)
            if v is None:
                approved.append(call)
            else:
                blocked.append(call)
                violations.append(v)

        return approved, blocked, violations

    def _evaluate_single(
        self,
        call: CanonicalToolCall,
        tool_lower: str,
    ) -> PolicyViolation | None:
        """评估单个工具调用。返回 None 表示批准。"""
        # 1. 黑名单检查
        if tool_lower in self._blacklist_lower:
            return PolicyViolation(
                policy="ToolPolicy",
                tool=call.tool,
                reason=f"tool '{call.tool}' is on the blacklist",
                is_critical=False,
            )

        # 2. 白名单检查（空白名单 = 禁止所有）
        if self._whitelist_lower and not any(fnmatch.fnmatch(tool_lower, p.lower()) for p in self._whitelist_lower):
            return PolicyViolation(
                policy="ToolPolicy",
                tool=call.tool,
                reason=f"tool '{call.tool}' is not in the whitelist",
                is_critical=False,
            )

        # 3. 代码写入权限
        if self._is_code_write_tool(tool_lower) and not self.allow_code_write:
            return PolicyViolation(
                policy="ToolPolicy",
                tool=call.tool,
                reason=f"role does not allow code-write tool '{call.tool}'",
                is_critical=False,
            )

        # 4. 命令执行权限
        if self._is_command_execution_tool(tool_lower) and not self.allow_command_execution:
            return PolicyViolation(
                policy="ToolPolicy",
                tool=call.tool,
                reason=f"role does not allow command-execution tool '{call.tool}'",
                is_critical=False,
            )

        # 5. 文件删除权限
        if self._is_file_delete_tool(tool_lower) and not self.allow_file_delete:
            return PolicyViolation(
                policy="ToolPolicy",
                tool=call.tool,
                reason=f"role does not allow file-delete tool '{call.tool}'",
                is_critical=False,
            )

        # 6. 路径穿越检查
        path_violation = self._check_path_traversal(call)
        if path_violation is not None:
            return path_violation

        # 7. 危险命令检查
        command_violation = self._check_dangerous_command(call)
        if command_violation is not None:
            return command_violation

        return None

    def _check_path_traversal(self, call: CanonicalToolCall) -> PolicyViolation | None:
        """检查路径穿越。"""
        path_keys = ("path", "file", "filepath", "target", "source")
        for key in path_keys:
            if key not in call.args:
                continue
            path = str(call.args[key])
            if self._is_path_traversal(path):
                return PolicyViolation(
                    policy="ToolPolicy",
                    tool=call.tool,
                    reason=f"path traversal detected in '{key}': '{path}'",
                    is_critical=True,
                )
        return None

    def _check_dangerous_command(self, call: CanonicalToolCall) -> PolicyViolation | None:
        """检查危险命令。"""
        if not is_command_execution_tool(call.tool):
            return None
        command = str(call.args.get("command", ""))
        if is_dangerous_command(command):
            return PolicyViolation(
                policy="ToolPolicy",
                tool=call.tool,
                reason=f"dangerous command pattern detected: '{command[:80]}'",
                is_critical=True,
            )
        return None

    def _is_code_write_tool(self, tool_name: str) -> bool:
        return is_code_write_tool(tool_name)

    def _is_command_execution_tool(self, tool_name: str) -> bool:
        return is_command_execution_tool(tool_name)

    def _is_file_delete_tool(self, tool_name: str) -> bool:
        return is_file_delete_tool(tool_name)

    def _is_path_traversal(self, path: str) -> bool:
        """检查路径是否包含穿越序列。

        SECURITY: Logs at warning level for security audit trail.
        """
        # URL 编码检测
        try:
            decoded = urllib.parse.unquote(path)
            decoded2 = urllib.parse.unquote(decoded)
            if decoded2 != decoded:
                path = decoded2
        except (RuntimeError, ValueError) as exc:
            # SECURITY FIX (P1-021): Log at warning level for security audit.
            logger.warning("Security: URL decoding failed for path=%r: %s", path[:100], exc)

        dangerous = [
            "../",
            "..\\",
            "..\\/",
            "%2e%2e%2f",
            "%252e%252e%252f",
            "%2e%2e%5c",
            "%252e%252e%255c",
            "..;",
            "..%00",
        ]
        for p in dangerous:
            if p in path.lower():
                return True

        normalized = str(path or "").strip()
        workspace_root = Path(self.workspace).expanduser().resolve() if self.workspace else None

        # 绝对路径检查
        is_absolute = bool(re.match(r"^[a-zA-Z]:[/\\]", normalized) or normalized.startswith(("/", "\\")))
        if is_absolute:
            try:
                candidate = Path(normalized).expanduser().resolve()
                if workspace_root and (candidate == workspace_root or workspace_root in candidate.parents):
                    return False
            except (OSError, ValueError) as exc:
                # SECURITY FIX (P1-021): Log at warning level for security audit.
                logger.warning("Security: Absolute path resolution failed path=%r: %s", normalized[:100], exc)
            return True

        # 相对路径检查
        try:
            base = workspace_root or Path.cwd().resolve()
            resolved = (base / normalized).resolve()
            if resolved != base and base not in resolved.parents:
                return True
        except (OSError, ValueError) as exc:
            # SECURITY FIX (P1-021): Log at warning level for security audit.
            # Fail-secure: if resolution fails, treat as traversal attempt.
            logger.warning("Security: Relative path resolution failed path=%r: %s", normalized[:100], exc)

        return False


__all__ = [
    "ToolPolicy",
    "ToolPolicyConfig",
]
