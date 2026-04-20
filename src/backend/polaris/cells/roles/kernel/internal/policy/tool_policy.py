"""ToolPolicy - compatibility facade with deterministic implementation.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from polaris.cells.roles.kernel.internal.policy.layer import (
    CanonicalToolCall,
    ToolPolicy as LayerToolPolicy,
)


@dataclass(slots=True)
class ToolPolicyDecision:
    """Single-call policy decision."""

    allowed: bool
    reason: str
    requires_approval: bool = False


class ToolPolicy:
    """Compatibility wrapper for legacy callers.

    The canonical runtime path uses `policy.layer.ToolPolicy` via `PolicyLayer`.
    This facade keeps older imports callable and deterministic.
    """

    _APPROVAL_REQUIRED_TOOLS: frozenset[str] = frozenset(
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

    def __init__(
        self,
        *,
        whitelist: list[str] | None = None,
        blacklist: list[str] | None = None,
        allow_code_write: bool = True,
        allow_command_execution: bool = True,
        allow_file_delete: bool = True,
        max_tool_calls_per_turn: int = 64,
        workspace: str = "",
        policy_id: str = "",
    ) -> None:
        self.whitelist = list(whitelist or [])
        self.blacklist = list(blacklist or [])
        self.allow_code_write = bool(allow_code_write)
        self.allow_command_execution = bool(allow_command_execution)
        self.allow_file_delete = bool(allow_file_delete)
        self.max_tool_calls_per_turn = int(max_tool_calls_per_turn)
        self.workspace = str(workspace or "")
        self.policy_id = str(policy_id or "")

    @classmethod
    def from_profile(cls, profile: Any, workspace: str = "") -> ToolPolicy:
        """Build from role profile tool-policy block."""
        policy = getattr(profile, "tool_policy", None)
        if policy is None:
            return cls(workspace=workspace)
        return cls(
            whitelist=list(getattr(policy, "whitelist", []) or []),
            blacklist=list(getattr(policy, "blacklist", []) or []),
            allow_code_write=bool(getattr(policy, "allow_code_write", True)),
            allow_command_execution=bool(getattr(policy, "allow_command_execution", True)),
            allow_file_delete=bool(getattr(policy, "allow_file_delete", True)),
            max_tool_calls_per_turn=int(getattr(policy, "max_tool_calls_per_turn", 64)),
            workspace=workspace,
            policy_id=str(getattr(policy, "policy_id", "") or ""),
        )

    @classmethod
    def from_env(cls) -> ToolPolicy:
        """Build from environment defaults."""

        def _split_csv(raw: str) -> list[str]:
            return [item.strip() for item in str(raw or "").split(",") if item.strip()]

        def _safe_int(raw: str, default: int) -> int:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default

        whitelist = _split_csv(os.environ.get("POLARIS_TOOL_POLICY_WHITELIST", ""))
        blacklist = _split_csv(os.environ.get("POLARIS_TOOL_POLICY_BLACKLIST", ""))
        return cls(
            whitelist=whitelist,
            blacklist=blacklist,
            allow_code_write=os.environ.get("POLARIS_ALLOW_CODE_WRITE", "true").lower() in {"1", "true", "yes"},
            allow_command_execution=os.environ.get("POLARIS_ALLOW_COMMAND_EXEC", "true").lower()
            in {"1", "true", "yes"},
            allow_file_delete=os.environ.get("POLARIS_ALLOW_FILE_DELETE", "true").lower() in {"1", "true", "yes"},
            max_tool_calls_per_turn=max(
                1,
                _safe_int(os.environ.get("POLARIS_TOOL_LOOP_MAX_TOTAL_CALLS", "64"), 64),
            ),
        )

    def _build_delegate(self, state: Any | None = None) -> LayerToolPolicy:
        # Legacy callers may provide `ConversationState.loaded_tools` but no whitelist.
        loaded_tools = []
        if state is not None:
            raw_loaded = getattr(state, "loaded_tools", None)
            if isinstance(raw_loaded, list):
                loaded_tools = [str(item).strip() for item in raw_loaded if str(item).strip()]

        whitelist = self.whitelist or loaded_tools
        return LayerToolPolicy(
            whitelist=whitelist,
            blacklist=self.blacklist,
            allow_code_write=self.allow_code_write,
            allow_command_execution=self.allow_command_execution,
            allow_file_delete=self.allow_file_delete,
            max_tool_calls_per_turn=self.max_tool_calls_per_turn,
            workspace=self.workspace,
            policy_id=self.policy_id,
        )

    @staticmethod
    def _normalize_args(raw_args: Any) -> dict[str, Any]:
        if isinstance(raw_args, dict):
            return dict(raw_args)
        if isinstance(raw_args, str):
            token = raw_args.strip()
            if not token:
                return {}
            try:
                parsed = json.loads(token)
            except (RuntimeError, ValueError):
                return {"raw": token}
            return dict(parsed) if isinstance(parsed, dict) else {"raw": token}
        return {}

    @classmethod
    def _requires_approval(cls, tool_name: str) -> bool:
        return str(tool_name or "").strip().lower() in cls._APPROVAL_REQUIRED_TOOLS

    def _coerce_call(self, call: Any) -> CanonicalToolCall:
        if isinstance(call, CanonicalToolCall):
            return call
        if isinstance(call, dict):
            name = str(call.get("tool") or call.get("name") or "").strip()
            args = self._normalize_args(call.get("args") or call.get("arguments"))
            call_id = str(call.get("call_id") or call.get("id") or "")
            raw = str(call.get("raw") or call.get("raw_content") or "")
            return CanonicalToolCall(tool=name, args=args, call_id=call_id, raw_content=raw)
        name = str(getattr(call, "tool", "") or getattr(call, "name", "")).strip()
        raw_args = getattr(call, "args", None)
        if raw_args is None:
            raw_args = getattr(call, "arguments", None)
        args = self._normalize_args(raw_args)
        call_id = str(getattr(call, "call_id", "") or getattr(call, "id", "") or "")
        raw = str(getattr(call, "raw", "") or getattr(call, "raw_content", "") or "")
        return CanonicalToolCall(tool=name, args=args, call_id=call_id, raw_content=raw)

    def evaluate_calls(
        self,
        calls: list[Any],
        state: Any | None = None,
    ) -> tuple[list[CanonicalToolCall], list[CanonicalToolCall], list[Any]]:
        delegate = self._build_delegate(state)
        canonical_calls = [self._coerce_call(item) for item in list(calls or [])]
        return delegate.evaluate(canonical_calls)

    def evaluate(
        self,
        tool_name: str | list[Any],
        args: dict[str, Any] | None = None,
        state: Any | None = None,
    ) -> ToolPolicyDecision | tuple[list[CanonicalToolCall], list[CanonicalToolCall], list[Any]]:
        """Evaluate one tool invocation."""
        if isinstance(tool_name, list):
            return self.evaluate_calls(tool_name, state)

        normalized_tool = str(tool_name or "").strip()
        if not normalized_tool:
            return ToolPolicyDecision(allowed=False, reason="tool name is empty")

        normalized_args = self._normalize_args(args)
        call = CanonicalToolCall(tool=normalized_tool, args=normalized_args)
        delegate = self._build_delegate(state)
        approved, blocked, violations = delegate.evaluate([call])

        if blocked:
            reason = violations[0].reason if violations else f"tool '{normalized_tool}' rejected by policy"
            return ToolPolicyDecision(
                allowed=False,
                reason=reason,
                requires_approval=self._requires_approval(normalized_tool),
            )

        if approved:
            return ToolPolicyDecision(
                allowed=True,
                reason="allowed",
                requires_approval=self._requires_approval(normalized_tool),
            )

        return ToolPolicyDecision(
            allowed=False,
            reason=f"tool '{normalized_tool}' was not approved",
            requires_approval=self._requires_approval(normalized_tool),
        )

    def filter(
        self,
        tool_calls: list[Any],
        state: Any | None = None,
    ) -> list[Any]:
        """Filter legacy tool-call objects by deterministic policy evaluation."""
        filtered: list[Any] = []
        for call in list(tool_calls or []):
            tool_name = ""
            args: dict[str, Any] = {}

            if isinstance(call, dict):
                tool_name = str(call.get("tool") or call.get("name") or "").strip()
                args = self._normalize_args(call.get("args") or call.get("arguments"))
            else:
                tool_name = str(getattr(call, "tool", "") or getattr(call, "name", "")).strip()
                raw_args = getattr(call, "args", None)
                if raw_args is None:
                    raw_args = getattr(call, "arguments", None)
                args = self._normalize_args(raw_args)

            decision = self.evaluate(tool_name, args, state)
            if isinstance(decision, ToolPolicyDecision) and decision.allowed:
                filtered.append(call)
        return filtered
