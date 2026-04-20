"""SandboxPolicy - 沙箱约束策略。

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §11 SandboxPolicy

路径约束 + 命令白名单。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .core import CanonicalToolCall, PolicyViolation

logger = logging.getLogger(__name__)


class SandboxPolicy:
    """沙箱约束策略。

    Blueprint: §11 SandboxPolicy

    Phase 3: 路径约束 + 命令白名单。
    Phase 4: 进程隔离、网络隔离（通过 AsyncProcessRunnerPort 实现）。

    SandboxPolicy 与 ToolPolicy 的路径检查的区别：
        - ToolPolicy: 检查 workspace 级别的路径穿越
        - SandboxPolicy: 检查更严格的沙箱边界（如只读目录、临时目录限制）
    """

    def __init__(
        self,
        *,
        allowed_paths: list[str] | None = None,
        read_only_paths: list[str] | None = None,
        temp_dir: str | None = None,
        network_allowed: bool = True,
    ) -> None:
        self.allowed_paths = allowed_paths or []
        self.read_only_paths = read_only_paths or []
        self.temp_dir = temp_dir
        self.network_allowed = network_allowed

    @classmethod
    def from_env(cls) -> SandboxPolicy:
        """从环境变量构造（Phase 3 默认全允许）。"""
        raw_allowed = os.environ.get("POLARIS_SANDBOX_ALLOWED_PATHS", "").strip()
        raw_readonly = os.environ.get("POLARIS_SANDBOX_READONLY_PATHS", "").strip()
        network = os.environ.get("POLARIS_SANDBOX_NETWORK", "true").lower() not in ("false", "0", "no")
        return cls(
            allowed_paths=[p.strip() for p in raw_allowed.split(",") if p.strip()],
            read_only_paths=[p.strip() for p in raw_readonly.split(",") if p.strip()],
            network_allowed=network,
        )

    def evaluate(
        self,
        calls: list[CanonicalToolCall],
    ) -> tuple[list[CanonicalToolCall], list[CanonicalToolCall], list[PolicyViolation]]:
        """评估沙箱约束。

        Args:
            calls: 待评估的工具调用列表。

        Returns:
            (approved_calls, blocked_calls, violations)
        """
        approved: list[CanonicalToolCall] = []
        blocked: list[CanonicalToolCall] = []
        violations: list[PolicyViolation] = []

        for call in calls:
            v = self._evaluate_single(call)
            if v is None:
                approved.append(call)
            else:
                blocked.append(call)
                violations.append(v)

        return approved, blocked, violations

    def _evaluate_single(self, call: CanonicalToolCall) -> PolicyViolation | None:
        """评估单个调用。返回 None 表示批准。"""
        path_keys = ("path", "file", "filepath", "target", "source", "directory")
        for key in path_keys:
            if key not in call.args:
                continue
            path = str(call.args[key])

            # 只读路径检查
            for ro_path in self.read_only_paths:
                if self._path_within(path, ro_path):
                    return PolicyViolation(
                        policy="SandboxPolicy",
                        tool=call.tool,
                        reason=f"path within read-only sandbox: '{path}'",
                        is_critical=True,
                    )

            # 允许路径白名单检查
            if self.allowed_paths and not any(self._path_within(path, ap) for ap in self.allowed_paths):
                return PolicyViolation(
                    policy="SandboxPolicy",
                    tool=call.tool,
                    reason=f"path outside allowed sandbox: '{path}'",
                    is_critical=False,
                )

        # 网络访问检查
        if not self.network_allowed and call.tool.lower() in ("execute_command", "run_shell", "curl", "wget", "fetch"):
            cmd = str(call.args.get("command", "")).lower()
            if any(n in cmd for n in ("http://", "https://", "ftp://")):
                return PolicyViolation(
                    policy="SandboxPolicy",
                    tool=call.tool,
                    reason="network access is not allowed in sandbox",
                    is_critical=True,
                )

        return None

    @staticmethod
    def _path_within(path: str, sandbox: str) -> bool:
        """判断 path 是否在 sandbox 目录内。

        SECURITY: Fail-secure - if path resolution fails, treat as outside sandbox.
        """
        try:
            p = Path(path).expanduser().resolve()
            s = Path(sandbox).expanduser().resolve()
            return s == p or s in p.parents
        except (OSError, ValueError) as exc:
            # SECURITY FIX (P1-020): Fail-secure on path resolution errors.
            # If we cannot resolve the path, treat it as outside sandbox (blocked).
            logger.warning("Sandbox path resolution failed for path=%r sandbox=%r: %s", path, sandbox, exc)
            return False


__all__ = [
    "SandboxPolicy",
]
