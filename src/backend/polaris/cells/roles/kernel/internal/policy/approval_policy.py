"""ApprovalPolicy - deterministic manual-approval gate.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from polaris.kernelone.utils.time_utils import utc_now_iso as _utc_now_iso


@dataclass(slots=True)
class ApprovalRequirement:
    """Approval request payload."""

    tool_name: str
    reason: str
    requested_at: str


class ApprovalPolicy:
    """Compatibility approval policy for legacy imports."""

    HIGH_RISK_TOOLS: frozenset[str] = frozenset(
        {
            "execute_command",
            "run_shell",
            "exec_cmd",
            "shell_execute",
            "system_call",
            "delete_file",
            "remove_file",
            "rm_file",
            "cleanup_file",
            "delete_directory",
            "write_file",
            "create_file",
            "modify_file",
            "search_replace",
            "edit_file",
            "append_to_file",
        }
    )

    _DANGEROUS_COMMAND_PATTERNS: tuple[str, ...] = (
        r"rm\s+-rf\s+[/~]",
        r"rm\s+-rf\s+\$HOME",
        r"dd\s+if=/dev/(zero|urandom)",
        r"mkfs\.",
        r"format\s+[a-z]:",
        r":\(\)\s*\{.*\|.*&.*\}",
        r"curl.*\|.*sh",
        r"wget.*\|.*sh",
        r"powershell.*-enc",
        r"cmd\.exe\s+/c",
        r"bash\s+-c",
        r"sh\s+-c",
    )
    _DANGEROUS_COMMAND_RE = re.compile("|".join(_DANGEROUS_COMMAND_PATTERNS), re.IGNORECASE)

    def __init__(
        self,
        *,
        require_approval_for: list[str] | None = None,
        require_approval_patterns: list[str] | None = None,
    ) -> None:
        self._require_approval_for = {
            str(item or "").strip().lower()
            for item in (require_approval_for or self.HIGH_RISK_TOOLS)
            if str(item or "").strip()
        }
        pattern_items = list(require_approval_patterns or [])
        if pattern_items:
            self._require_approval_re = tuple(re.compile(str(item), re.IGNORECASE) for item in pattern_items)
        else:
            self._require_approval_re = ()
        self._pending_by_call_id: dict[str, ApprovalRequirement] = {}

    @classmethod
    def from_env(cls) -> ApprovalPolicy:
        def _split_csv(raw: str) -> list[str]:
            return [item.strip() for item in str(raw or "").split(",") if item.strip()]

        tools = _split_csv(os.environ.get("KERNELONE_APPROVAL_TOOLS", ""))
        patterns = _split_csv(os.environ.get("KERNELONE_APPROVAL_PATTERNS", ""))
        return cls(
            require_approval_for=tools or None,
            require_approval_patterns=patterns or None,
        )

    def requires_approval(
        self,
        tool_name: str,
        args: dict[str, Any] | None,
        state: Any | None = None,
    ) -> bool:
        del state  # reserved compatibility parameter

        token = str(tool_name or "").strip().lower()
        if not token:
            return False
        if token in self._require_approval_for:
            return True

        safe_args = dict(args or {})
        command = str(safe_args.get("command") or safe_args.get("cmd") or safe_args.get("script") or "")
        if command and self._DANGEROUS_COMMAND_RE.search(command):
            return True
        return bool(command and any(pattern.search(command) for pattern in self._require_approval_re))

    def request_approval(
        self,
        tool_name: str,
        args: dict[str, Any] | None,
        state: Any | None = None,
    ) -> ApprovalRequirement:
        reason = self._build_approval_reason(tool_name, dict(args or {}), state=state)
        return ApprovalRequirement(
            tool_name=str(tool_name or "").strip(),
            reason=reason,
            requested_at=_utc_now_iso(),
        )

    def evaluate(
        self,
        calls: list[Any],
    ) -> tuple[list[Any], list[Any], list[Any]]:
        """Evaluate a call batch.

        Returns:
            (auto_approved_calls, requires_approval_calls, violations)
        """
        approved: list[Any] = []
        requires: list[Any] = []
        for call in list(calls or []):
            if isinstance(call, dict):
                tool_name = str(call.get("tool") or call.get("name") or "").strip()
                raw_args = call.get("args") or call.get("arguments") or {}
                call_id = str(call.get("call_id") or call.get("id") or "")
            else:
                tool_name = str(getattr(call, "tool", "") or getattr(call, "name", "")).strip()
                raw_args = getattr(call, "args", None)
                if raw_args is None:
                    raw_args = getattr(call, "arguments", None)
                call_id = str(getattr(call, "call_id", "") or getattr(call, "id", "") or "")
            args = raw_args if isinstance(raw_args, dict) else {}
            if self.requires_approval(tool_name, args):
                requires.append(call)
                requirement = self.request_approval(tool_name, args)
                if call_id:
                    self._pending_by_call_id[call_id] = requirement
            else:
                approved.append(call)
        return approved, requires, []

    def approve(self, call_id: str) -> bool:
        token = str(call_id or "").strip()
        if not token:
            return False
        return self._pending_by_call_id.pop(token, None) is not None

    def reject(self, call_id: str, reason: str = "") -> bool:
        del reason  # reserved compatibility parameter
        token = str(call_id or "").strip()
        if not token:
            return False
        return self._pending_by_call_id.pop(token, None) is not None

    def clear_pending(self) -> None:
        self._pending_by_call_id.clear()

    def _build_approval_reason(
        self,
        tool_name: str,
        args: dict[str, Any] | None,
        *,
        state: Any | None = None,
    ) -> str:
        del state  # reserved compatibility parameter

        token = str(tool_name or "").strip()
        safe_args = dict(args or {})
        if not token:
            return "Empty tool name requires manual verification."

        lowered = token.lower()
        command = str(safe_args.get("command") or safe_args.get("cmd") or safe_args.get("script") or "")

        if lowered in self._require_approval_for and command:
            return f"Tool '{token}' is high-risk and command execution must be reviewed."
        if lowered in self._require_approval_for:
            return f"Tool '{token}' is high-risk and requires manual confirmation."
        if command and self._DANGEROUS_COMMAND_RE.search(command):
            return f"Command pattern in '{token}' matches dangerous-operation rules."
        return f"Tool '{token}' requires manual approval by policy."
