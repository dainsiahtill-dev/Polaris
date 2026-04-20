"""SandboxPolicy - deterministic sandbox checks.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polaris.kernelone.security.dangerous_patterns import is_dangerous_command


@dataclass(slots=True)
class SandboxDecision:
    """Sandbox evaluation result."""

    allowed: bool
    reason: str | None = None


class SandboxPolicy:
    """Compatibility sandbox policy used by legacy callers."""

    SAFE_PATH_PREFIXES: tuple[str, ...] = (
        "/tmp/",
        "/var/tmp/",
        "runtime/",
    )
    ALLOWED_NETWORK_PATTERNS: tuple[str, ...] = (
        "localhost",
        "127.0.0.1",
        "::1",
    )
    BLOCKED_PORTS: tuple[int, ...] = (
        22,
        23,
        3389,
        5432,
        3306,
        1433,
    )

    _TRAVERSAL_RE = re.compile(r"(\.\./|\.\.\\|%2e%2e%2f|%2e%2e%5c)", re.IGNORECASE)
    _SENSITIVE_PATH_RE = re.compile(
        r"(^/etc/passwd$|^/etc/shadow$|/\.ssh/|\\\.ssh\\|^c:\\windows\\system32)",
        re.IGNORECASE,
    )
    _DANGEROUS_ENV_RE = re.compile(
        r"(LD_PRELOAD|LD_LIBRARY_PATH|DYLD_INSERT_LIBRARIES|API_KEY|TOKEN|PASSWORD|SECRET)",
        re.IGNORECASE,
    )

    @classmethod
    def from_env(cls) -> SandboxPolicy:
        instance = cls()

        ports_raw = str(os.environ.get("POLARIS_SANDBOX_BLOCKED_PORTS", "") or "").strip()
        if ports_raw:
            parsed_ports: list[int] = []
            for token in ports_raw.split(","):
                item = token.strip()
                if not item:
                    continue
                try:
                    parsed_ports.append(int(item))
                except (TypeError, ValueError):
                    continue
            if parsed_ports:
                instance.BLOCKED_PORTS = tuple(parsed_ports)

        hosts_raw = str(os.environ.get("POLARIS_SANDBOX_ALLOWED_HOSTS", "") or "").strip()
        if hosts_raw:
            hosts = tuple(item.strip() for item in hosts_raw.split(",") if item.strip())
            if hosts:
                instance.ALLOWED_NETWORK_PATTERNS = hosts

        return instance

    def evaluate_fs_scope(
        self,
        path: str,
        workspace: str = "",
    ) -> SandboxDecision:
        token = str(path or "").strip()
        if not token:
            return SandboxDecision(allowed=False, reason="path is empty")

        lowered = token.replace("\\", "/").lower()
        if self._TRAVERSAL_RE.search(lowered):
            return SandboxDecision(allowed=False, reason="path traversal detected")
        if self._SENSITIVE_PATH_RE.search(lowered):
            return SandboxDecision(allowed=False, reason="sensitive path is forbidden")

        if workspace:
            try:
                workspace_root = Path(workspace).expanduser().resolve()
                candidate = Path(token).expanduser()
                resolved = candidate.resolve() if candidate.is_absolute() else (workspace_root / candidate).resolve()
                if resolved != workspace_root and workspace_root not in resolved.parents:
                    return SandboxDecision(
                        allowed=False,
                        reason="path is outside workspace sandbox",
                    )
            except (RuntimeError, ValueError):
                return SandboxDecision(allowed=False, reason="invalid sandbox path")

        return SandboxDecision(allowed=True)

    def evaluate_network_scope(
        self,
        host: str,
        port: int,
    ) -> SandboxDecision:
        host_token = str(host or "").strip().lower()
        if not host_token:
            return SandboxDecision(allowed=False, reason="host is empty")
        if int(port) in self.BLOCKED_PORTS:
            return SandboxDecision(allowed=False, reason="blocked port")
        if host_token in {item.lower() for item in self.ALLOWED_NETWORK_PATTERNS}:
            return SandboxDecision(allowed=True)
        return SandboxDecision(allowed=False, reason="external network access denied")

    def evaluate_process_scope(
        self,
        command: str,
        args: list[str] | None = None,
    ) -> SandboxDecision:
        cmd = str(command or "").strip()
        arg_list = [str(item or "").strip() for item in list(args or [])]
        full = " ".join([cmd, *arg_list]).strip()
        if not full:
            return SandboxDecision(allowed=False, reason="empty command")
        if is_dangerous_command(full):
            return SandboxDecision(allowed=False, reason="dangerous command pattern")
        return SandboxDecision(allowed=True)

    def evaluate_env_scope(
        self,
        env_vars: dict[str, str] | None,
    ) -> SandboxDecision:
        data = dict(env_vars or {})
        for key, value in data.items():
            k = str(key or "")
            v = str(value or "")
            if self._DANGEROUS_ENV_RE.search(k):
                return SandboxDecision(allowed=False, reason=f"forbidden env var: {k}")
            if len(v) > 1024 * 64:
                return SandboxDecision(allowed=False, reason=f"env var too large: {k}")
        return SandboxDecision(allowed=True)

    def evaluate(
        self,
        calls: list[Any],
    ) -> tuple[list[Any], list[Any], list[Any]]:
        """Evaluate a call batch and return (approved, blocked, violations)."""
        approved: list[Any] = []
        blocked: list[Any] = []
        violations: list[Any] = []

        for call in list(calls or []):
            if isinstance(call, dict):
                tool_name = str(call.get("tool") or call.get("name") or "").strip()
                raw_args = call.get("args") or call.get("arguments") or {}
            else:
                tool_name = str(getattr(call, "tool", "") or getattr(call, "name", "")).strip()
                raw_args = getattr(call, "args", None)
                if raw_args is None:
                    raw_args = getattr(call, "arguments", None)
            args = raw_args if isinstance(raw_args, dict) else {}

            decision = self._evaluate_single(tool_name, args)
            if decision.allowed:
                approved.append(call)
            else:
                blocked.append(call)
                violations.append(
                    {
                        "policy": "SandboxPolicy",
                        "tool": tool_name,
                        "reason": str(decision.reason or "sandbox denied"),
                        "critical": True,
                    }
                )

        return approved, blocked, violations

    def _evaluate_single(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> SandboxDecision:
        token = str(tool_name or "").strip().lower()
        if not token:
            return SandboxDecision(allowed=False, reason="empty tool name")

        path = str(args.get("path") or args.get("file") or args.get("filepath") or args.get("directory") or "").strip()
        if path:
            fs_decision = self.evaluate_fs_scope(path)
            if not fs_decision.allowed:
                return fs_decision

        command = str(args.get("command") or args.get("cmd") or "").strip()
        if command:
            process_decision = self.evaluate_process_scope(command)
            if not process_decision.allowed:
                return process_decision

        host = str(args.get("host") or "").strip()
        port = args.get("port")
        if host and isinstance(port, int):
            net_decision = self.evaluate_network_scope(host, port)
            if not net_decision.allowed:
                return net_decision

        env_vars = args.get("env")
        if isinstance(env_vars, dict):
            env_decision = self.evaluate_env_scope(env_vars)
            if not env_decision.allowed:
                return env_decision

        return SandboxDecision(allowed=True)
