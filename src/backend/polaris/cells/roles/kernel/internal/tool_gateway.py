"""Role Tool Gateway - 角色工具网关

严格执行角色的工具白名单策略，防止越权工具调用。

工具白名单策略：
    工具白名单由角色 Profile 的 tool_policy 定义，Gateway 严格执行白名单检查。
    工具身份基于 canonical name，禁止使用别名映射绕过白名单。
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.cells.control_plane.run_ledger.public.contracts import AppendRunLedgerEventCommandV1
from polaris.cells.control_plane.run_ledger.public.ledger import stable_hash
from polaris.cells.control_plane.run_ledger.public.service import append_run_ledger_event
from polaris.cells.director.runtime.public.directed_effect_contracts import (
    DirectedEffectImmutableItemsV1,
    hash_directed_effect_arguments,
    require_directed_effect_immutable_items,
)
from polaris.kernelone.llm.toolkit.tool_normalization import (
    get_available_tools,
    normalize_tool_arguments,
    normalize_tool_arguments_from_snapshot,
)
from polaris.kernelone.security.dangerous_patterns import is_path_traversal
from polaris.kernelone.tool_execution.contracts import (
    CapturedToolSpecSnapshotV1,
    canonicalize_tool_name,
    frozen_node_to_value,
)
from polaris.kernelone.tool_execution.tool_categories import (
    is_code_write_tool,
    is_command_execution_tool,
    is_file_delete_tool,
)
from polaris.kernelone.utils import utc_now_iso

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleProfile

logger = logging.getLogger(__name__)

_DIRECTED_EFFECT_POLICY_VERSION = "role-tool-policy.v1"


def _directed_effect_hash_json(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    import hashlib

    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class DirectedEffectGatewayPolicyInputsV1:
    """Immutable current gateway policy inputs for one authoritative DEO guard."""

    role_policy_id: str
    role_policy_hash: str
    canonical_allow_list_hash: str
    capability_scope: tuple[str, ...]
    capability_scope_hash: str
    job_token_id: str
    job_token_evidence_hash: str
    job_token_restriction_evidence: DirectedEffectImmutableItemsV1
    execution_envelope_hash: str
    allowed_command_hash: str
    policy_version: str

    def __post_init__(self) -> None:
        for name in ("role_policy_id", "job_token_id", "policy_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"{name} must be canonical")
        for name in (
            "role_policy_hash",
            "canonical_allow_list_hash",
            "capability_scope_hash",
            "job_token_evidence_hash",
            "execution_envelope_hash",
            "allowed_command_hash",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64 or any(
                char not in "0123456789abcdef" for char in value
            ):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if self.capability_scope != tuple(sorted(set(self.capability_scope))):
            raise ValueError("capability_scope must be sorted and unique")
        restrictions = require_directed_effect_immutable_items(
            "job_token_restriction_evidence",
            self.job_token_restriction_evidence,
        )
        if self.job_token_evidence_hash != hash_directed_effect_arguments(restrictions):
            raise ValueError("job_token_evidence_hash must bind restrictions")


class ToolAuthorizationError(Exception):
    """工具授权失败异常"""

    pass


class RoleToolGateway:
    """角色工具网关

    根据角色的工具策略，严格控制工具调用的权限。

    使用示例:
        >>> gateway = RoleToolGateway(profile)
        >>> can_execute, reason = gateway.check_tool_permission("search_code")
        >>> if can_execute:
        ...     result = gateway.execute_tool("search_code", {"query": "..."})
        ... else:
        ...     raise ToolAuthorizationError(reason)
    """

    # NOTE: TOOL_ALIASES removed - use normalize_tool_name() from tool_normalization.py
    # which is the single source of truth for tool name aliases.

    def __init__(
        self,
        profile: RoleProfile,
        workspace: str = "",
        *,
        session_id: str | None = None,
        session_memory_provider: Any | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        iteration: int = 0,
        capability_scope: list[str] | tuple[str, ...] | frozenset[str] | None = None,
        capability_token: dict[str, Any] | None = None,
    ) -> None:
        """初始化工具网关

        Args:
            profile: 角色Profile
            workspace: 工作区路径
            session_id: 会话ID
            session_memory_provider: 会话内存提供者
            run_id: 运行时ID（用于事件追踪）
            task_id: 当前角色任务ID（用于工具事件归因）
            iteration: 当前 turn 内的工具调用轮次（用于日志审计）
            capability_scope: 当前回合不可变写权限路径范围
            capability_token: 当前回合 Job Token evidence，用于物理 effect receipt
        """
        self.profile = profile
        self.policy = profile.tool_policy
        self.workspace = workspace
        self.session_id = str(session_id or "").strip() or None
        self.session_memory_provider = session_memory_provider
        self._capability_scope = tuple(
            str(item or "").replace("\\", "/").strip("/")
            for item in (capability_scope or ())
            if str(item or "").strip()
        )
        self._capability_token = dict(capability_token or {})
        self._execution_count = 0
        self._run_id = str(run_id or "").strip() or None
        self._task_id = str(task_id or "").strip() or None
        self.iteration = iteration
        # FailureBudget: 跨工具调用持久化失败预算状态（HALLUCINATION_LOOP 检测）
        from polaris.kernelone.tool_execution.failure_budget import FailureBudget

        self._failure_budget = FailureBudget()

    def reset_execution_count(self) -> None:
        """重置当前回合的工具调用计数。

        计数语义为"单次请求/单回合"，不得跨回合累积。
        """
        self._execution_count = 0

    def set_iteration(self, iteration: int) -> None:
        """设置当前 turn 内的工具调用轮次（用于日志审计）。"""
        self.iteration = max(0, int(iteration))

    def _canonical_tool_whitelist(self) -> frozenset[str]:
        """Return the canonical, executor-enforceable role tool whitelist.

        Missing or empty whitelists are fail-closed. An explicit wildcard remains
        possible, but it is expanded against registered canonical tools before it
        reaches the executor so ``None`` never means "allow everything".
        """
        whitelist = getattr(self.policy, "whitelist", None) or ()
        if not whitelist:
            return frozenset()

        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        registered = tuple(ToolSpecRegistry.get_all_canonical_names())
        allowed: set[str] = set()
        for item in whitelist:
            raw = str(item or "").strip()
            if not raw:
                continue
            canonical = self._normalize_tool_name(raw).strip().lower()
            if not canonical:
                continue
            if any(char in canonical for char in ("*", "?", "[")):
                allowed.update(name for name in registered if self._match_wildcard(name, canonical))
                continue
            allowed.add(canonical)
        return frozenset(allowed)

    def _get_allowed_tools_for_executor(self) -> frozenset[str]:
        """Extract the canonical tool whitelist for executor-level enforcement.

        Missing or empty whitelists return an empty set, causing executor-level
        enforcement to reject every tool. This keeps provider schema, gateway
        policy, and runtime execution aligned when a profile is misconfigured.
        """
        return self._canonical_tool_whitelist()

    def close(self) -> None:
        close = getattr(self.session_memory_provider, "close", None)
        if callable(close):
            close()

    @property
    def policy_id(self) -> str:
        """策略标识"""
        return self.policy.policy_id

    def capture_directed_effect_policy_inputs(
        self,
    ) -> DirectedEffectGatewayPolicyInputsV1:
        """Capture one canonical policy/JobToken source without exposing mutable state."""

        capability_scope = tuple(sorted(set(self._capability_scope)))
        token_id = str(self._capability_token.get("token_id") or "").strip()
        envelope_hash = str(
            self._capability_token.get("execution_envelope_hash") or ""
        ).strip()
        capability_audit_ok = self._capability_token.get("capability_audit_ok")
        if (
            not token_id
            or len(envelope_hash) != 64
            or any(char not in "0123456789abcdef" for char in envelope_hash)
            or capability_audit_ok is not True
        ):
            raise ValueError("authoritative JobToken evidence unavailable")
        raw_commands = self._capability_token.get("allowed_commands") or ()
        if isinstance(raw_commands, str):
            raw_commands = (raw_commands,)
        if not isinstance(raw_commands, (list, tuple, set, frozenset)):
            raise ValueError("allowed_commands must be a sequence")
        allowed_commands = tuple(
            sorted({str(item).strip() for item in raw_commands if str(item).strip()})
        )
        allowed_commands_hash = hash_directed_effect_arguments(
            (("allowed_commands", allowed_commands),)
        )
        allowed_paths_hash = hash_directed_effect_arguments(
            (("allowed_paths", capability_scope),)
        )
        token_hash = _directed_effect_hash_json(self._capability_token)
        restrictions: DirectedEffectImmutableItemsV1 = (
            ("allowed_commands", allowed_commands),
            ("allowed_commands_hash", allowed_commands_hash),
            ("allowed_paths", capability_scope),
            ("allowed_paths_hash", allowed_paths_hash),
            ("job_token_hash", token_hash),
            ("job_token_id", token_id),
        )
        policy_payload = (
            asdict(self.policy)
            if is_dataclass(self.policy) and not isinstance(self.policy, type)
            else {
                "whitelist": tuple(getattr(self.policy, "whitelist", ()) or ()),
                "blacklist": tuple(getattr(self.policy, "blacklist", ()) or ()),
                "allow_code_write": bool(getattr(self.policy, "allow_code_write", False)),
                "allow_command_execution": bool(
                    getattr(self.policy, "allow_command_execution", False)
                ),
                "allow_file_delete": bool(
                    getattr(self.policy, "allow_file_delete", False)
                ),
            }
        )
        canonical_allow_list = tuple(sorted(self._canonical_tool_whitelist()))
        return DirectedEffectGatewayPolicyInputsV1(
            role_policy_id=self.policy_id,
            role_policy_hash=_directed_effect_hash_json(policy_payload),
            canonical_allow_list_hash=hash_directed_effect_arguments(
                (("canonical_allow_list", canonical_allow_list),)
            ),
            capability_scope=capability_scope,
            capability_scope_hash=allowed_paths_hash,
            job_token_id=token_id,
            job_token_evidence_hash=hash_directed_effect_arguments(restrictions),
            job_token_restriction_evidence=restrictions,
            execution_envelope_hash=envelope_hash,
            allowed_command_hash=allowed_commands_hash,
            policy_version=_DIRECTED_EFFECT_POLICY_VERSION,
        )

    # 安全拒绝标记 - 用于在拒绝工具调用时提供人类可读的拒绝原因
    # 这些标记被基准测试用于检测模型是否正确拒绝危险操作
    REFUSAL_MARKERS: tuple[str, ...] = (
        "不能",
        "禁止",
        "不允许",
        "拒绝",
        "危险",
        "安全",
        "dangerous",
        "forbidden",
    )

    def check_tool_permission(self, tool_name: str, tool_args: dict | None = None) -> tuple[bool, str]:
        """Compatibility entry that captures and normalizes exactly one owner view."""
        from polaris.cells.roles.kernel.public.turn_contracts import classify_tool_invocation

        raw_tool_name = str(tool_name)
        raw_arguments = dict(tool_args) if isinstance(tool_args, Mapping) else {}
        classification = classify_tool_invocation(raw_tool_name)
        snapshot = classification.snapshot
        if not isinstance(snapshot, CapturedToolSpecSnapshotV1) or classification.error_code is not None:
            return False, self._format_refusal_message("工具规范化失败", raw_tool_name)
        if classification.normalization_required:
            try:
                normalized_arguments = normalize_tool_arguments_from_snapshot(snapshot, raw_arguments)
            except ValueError:
                return False, self._format_refusal_message("工具规范化失败", raw_tool_name)
        else:
            normalized_arguments = raw_arguments
        return self.check_tool_permission_from_snapshot(
            raw_tool_name=raw_tool_name,
            canonical_tool_name=classification.canonical_tool_name,
            normalized_tool_args=normalized_arguments,
            tool_snapshot=snapshot,
        )

    def check_tool_permission_from_snapshot(
        self,
        *,
        raw_tool_name: str,
        canonical_tool_name: str,
        normalized_tool_args: Mapping[str, object],
        tool_snapshot: CapturedToolSpecSnapshotV1,
    ) -> tuple[bool, str]:
        """Apply gateway policy using only one previously captured tool view."""
        if not self._snapshot_matches_bound_tool(
            raw_tool_name=raw_tool_name,
            canonical_tool_name=canonical_tool_name,
            tool_snapshot=tool_snapshot,
        ):
            return False, self._format_refusal_message("工具快照不一致", raw_tool_name)

        requested_tool_name = raw_tool_name
        normalized_args = dict(normalized_tool_args)

        snapshot_category = self._snapshot_tool_category(tool_snapshot)

        def deny(base_message: str) -> tuple[bool, str]:
            return False, self._format_refusal_message_from_snapshot(
                base_message,
                snapshot_category,
            )

        if snapshot_category is None:
            return deny("工具分类不一致")
        if snapshot_category not in {"read", "write", "exec", "delete"}:
            return deny("工具分类不支持")

        whitelist = self._canonical_tool_whitelist_from_snapshot(tool_snapshot)
        if canonical_tool_name.lower() not in whitelist:
            tool_label = (
                f"{requested_tool_name} (canonical: {canonical_tool_name})"
                if requested_tool_name != canonical_tool_name
                else requested_tool_name
            )
            return deny(f"工具 '{tool_label}' 不在角色白名单中")

        blacklist = self._canonical_tool_blacklist_from_snapshot(tool_snapshot)
        if canonical_tool_name.lower() in blacklist:
            return deny(f"工具 '{canonical_tool_name}' 在角色黑名单中")

        if snapshot_category == "write":
            if not self.policy.allow_code_write:
                return deny(f"角色无权使用代码写入工具 '{canonical_tool_name}'")
            if "scope" in normalized_args and not self._validate_scope(normalized_args["scope"]):
                return deny("scope约束验证失败")

        if snapshot_category == "exec":
            if not self.policy.allow_command_execution:
                return deny(f"角色无权执行命令 '{canonical_tool_name}'")
            if "command" in normalized_args and self._is_dangerous_command(str(normalized_args["command"])):
                return deny("命令包含危险操作")

        if snapshot_category == "delete" and not self.policy.allow_file_delete:
            return deny(f"角色无权删除文件 '{canonical_tool_name}'")

        if self._execution_count >= self.policy.max_tool_calls_per_turn:
            return deny(f"超过单次请求最大工具调用次数 ({self.policy.max_tool_calls_per_turn})")

        for key in ["path", "file", "filepath", "target", "source"]:
            if key in normalized_args:
                path = str(normalized_args[key])
                if self._is_path_traversal(path):
                    return deny(f"路径 '{path}' 包含穿越序列")

        return True, "授权通过"

    @staticmethod
    def _snapshot_matches_bound_tool(
        *,
        raw_tool_name: str,
        canonical_tool_name: str,
        tool_snapshot: CapturedToolSpecSnapshotV1,
    ) -> bool:
        """Reject forged or mutated snapshots without consulting the registry."""
        try:
            validated = CapturedToolSpecSnapshotV1(
                raw_tool_name=tool_snapshot.raw_tool_name,
                canonical_tool_name=tool_snapshot.canonical_tool_name,
                registered=tool_snapshot.registered,
                canonical_effective_spec=tool_snapshot.canonical_effective_spec,
                canonical_name_view=tool_snapshot.canonical_name_view,
                alias_binding_view=tool_snapshot.alias_binding_view,
            )
        except (TypeError, ValueError):
            return False
        if not validated.registered:
            return False
        if raw_tool_name != validated.raw_tool_name or canonical_tool_name != validated.canonical_tool_name:
            return False
        if (
            tool_snapshot.tool_spec_hash != validated.tool_spec_hash
            or tool_snapshot.canonical_name_view_hash != validated.canonical_name_view_hash
            or tool_snapshot.alias_binding_hash != validated.alias_binding_hash
            or tool_snapshot.snapshot_hash != validated.snapshot_hash
        ):
            return False
        names = frozen_node_to_value(validated.canonical_name_view)
        aliases = frozen_node_to_value(validated.alias_binding_view)
        if not isinstance(names, list) or not isinstance(aliases, dict) or canonical_tool_name not in names:
            return False
        return aliases.get(validated.raw_tool_name) == canonical_tool_name

    @staticmethod
    def _snapshot_tool_category(tool_snapshot: CapturedToolSpecSnapshotV1) -> str | None:
        """Return one validated category using only immutable snapshot evidence."""
        effective_spec = frozen_node_to_value(tool_snapshot.canonical_effective_spec)
        if not isinstance(effective_spec, dict):
            return None
        raw_category = effective_spec.get("category")
        if not isinstance(raw_category, str):
            return None
        category = raw_category.strip().lower()
        if not category:
            return None

        raw_categories = effective_spec.get("categories")
        if raw_categories is not None:
            if not isinstance(raw_categories, list) or not raw_categories:
                return None
            declared_categories = {
                value.strip().lower() for value in raw_categories if isinstance(value, str) and value.strip()
            }
            if len(declared_categories) != len(raw_categories) or declared_categories != {category}:
                return None

        expected_effects = {
            "read": "read",
            "write": "write",
            "exec": "write",
            "delete": "write",
            "async": "async",
        }
        for field_name in ("effect_type", "effect"):
            raw_effect = effective_spec.get(field_name)
            if raw_effect is not None and (
                not isinstance(raw_effect, str)
                or category not in expected_effects
                or raw_effect.strip().lower() != expected_effects[category]
            ):
                return None
        return category

    def _canonical_tool_whitelist_from_snapshot(
        self,
        tool_snapshot: CapturedToolSpecSnapshotV1,
    ) -> frozenset[str]:
        return self._canonical_policy_tools_from_snapshot(getattr(self.policy, "whitelist", None), tool_snapshot)

    def _canonical_tool_blacklist_from_snapshot(
        self,
        tool_snapshot: CapturedToolSpecSnapshotV1,
    ) -> frozenset[str]:
        return self._canonical_policy_tools_from_snapshot(getattr(self.policy, "blacklist", None), tool_snapshot)

    def _canonical_policy_tools_from_snapshot(
        self,
        policy_tools: Any,
        tool_snapshot: CapturedToolSpecSnapshotV1,
    ) -> frozenset[str]:
        """Resolve role policy aliases only from the supplied frozen views."""
        if not policy_tools:
            return frozenset()
        aliases = frozen_node_to_value(tool_snapshot.alias_binding_view)
        names = frozen_node_to_value(tool_snapshot.canonical_name_view)
        if not isinstance(aliases, dict) or not isinstance(names, list):
            return frozenset()
        registered = {str(name).lower() for name in names if isinstance(name, str)}
        allowed: set[str] = set()
        for item in policy_tools:
            raw = str(item or "").strip()
            if not raw:
                continue
            normalized = raw.lower().replace("-", "_")
            resolved = aliases.get(normalized, normalized)
            canonical = str(resolved).lower() if isinstance(resolved, str) else ""
            if any(char in canonical for char in ("*", "?", "[")):
                allowed.update(name for name in registered if self._match_wildcard(name, canonical))
            elif canonical in registered:
                allowed.add(canonical)
        return frozenset(allowed)

    def _format_refusal_message(self, base_message: str, tool_name: str) -> str:
        """格式化拒绝消息，添加安全拒绝标记。

        Args:
            base_message: 基础拒绝消息
            tool_name: 工具名称

        Returns:
            包含拒绝标记的格式化消息
        """
        # 为不同类型的工具添加相关的拒绝标记
        markers = list(self.REFUSAL_MARKERS)

        # 根据工具类型添加特定标记
        if self._is_file_delete_tool(tool_name):
            # 文件删除类工具
            return f"{base_message} [拒绝: 不能删除/禁止删除/危险操作]"
        elif self._is_command_execution_tool(tool_name):
            # 命令执行类工具
            return f"{base_message} [拒绝: 不能执行/禁止执行/危险命令]"
        elif self._is_code_write_tool(tool_name):
            # 代码写入类工具
            return f"{base_message} [拒绝: 不能写入/禁止写入/危险操作]"
        else:
            # 通用拒绝标记
            return f"{base_message} [拒绝: {'/'.join(markers[:3])}]"

    def _format_refusal_message_from_snapshot(
        self,
        base_message: str,
        snapshot_category: str | None,
    ) -> str:
        """Format a bound-entry denial without reading active category caches."""
        markers = list(self.REFUSAL_MARKERS)
        if snapshot_category == "delete":
            return f"{base_message} [拒绝: 不能删除/禁止删除/危险操作]"
        if snapshot_category == "exec":
            return f"{base_message} [拒绝: 不能执行/禁止执行/危险命令]"
        if snapshot_category == "write":
            return f"{base_message} [拒绝: 不能写入/禁止写入/危险操作]"
        return f"{base_message} [拒绝: {'/'.join(markers[:3])}]"

    def _emit_tool_event_to_journal(
        self,
        event_type: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        result: Any = None,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Emit tool event to {role}.llm.events.jsonl (sync fallback when MessageBus unavailable).

        Writes to: {runtime_root}/events/{role}.llm.events.jsonl

        This is the safety-net fallback ensuring tool events are never silently dropped
        when UEP MessageBus is unavailable (e.g., in benchmark runs without
        assemble_core_services()).
        """
        run_id = self._run_id
        if not run_id:
            return

        try:
            workspace = os.path.abspath(str(self.workspace or "").strip() or os.getcwd())

            # Resolve runtime root via storage layout
            try:
                from polaris.cells.storage.layout import resolve_polaris_roots

                roots = resolve_polaris_roots(workspace)
                runtime_root = roots.runtime_root
            except (RuntimeError, ValueError):
                from polaris.kernelone.storage import resolve_runtime_path

                runtime_root = resolve_runtime_path(workspace, "runtime")

            role = str(self.profile.role_id or "unknown").strip().lower() or "unknown"
            events_dir = os.path.join(runtime_root, "events")
            os.makedirs(events_dir, exist_ok=True)
            journal_path = os.path.join(events_dir, f"{role}.llm.events.jsonl")

            data: dict[str, Any] = {
                "event_type": event_type,
                "tool": tool_name,
                "iteration": self.iteration,
            }
            if self._task_id:
                data["task_id"] = self._task_id
            if arguments is not None:
                data["args"] = arguments
            if result is not None:
                data["result"] = result
            if error is not None:
                data["error"] = error
            if duration_ms is not None:
                data["duration_ms"] = duration_ms

            journal_entry = {
                "schema_version": 1,
                "ts": utc_now_iso(),
                "ts_epoch": time.time(),
                "seq": int(time.time() * 1000) % 1000000,
                "event_id": str(uuid.uuid4())[:8],
                "run_id": run_id,
                "role": role,
                "source": "tool_gateway",
                "event": event_type,
                "data": data,
            }
            if self._task_id:
                journal_entry["task_id"] = self._task_id

            with open(journal_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(journal_entry, ensure_ascii=False) + "\n")
        except (RuntimeError, ValueError):
            # Audit emission must never break the main flow
            pass

    def _schedule_uep_event(
        self,
        event_type: str,
        tool_name: str,
        payload: dict[str, Any],
    ) -> None:
        """Schedule async UEP event emission from sync gateway context.

        Uses call_soon_threadsafe to schedule the coroutine on the running
        event loop without blocking. This is fire-and-forget - if the loop
        is not running, the event is silently dropped (file fallback exists).

        Args:
            event_type: Event type (tool_call, tool_result, tool_error)
            tool_name: Tool name
            payload: Event payload dict
        """
        run_id = self._run_id
        if not run_id:
            return
        workspace = str(self.workspace or "").strip() or ""
        role = str(self.profile.role_id or "unknown")
        enriched_payload = dict(payload)
        if self._task_id:
            enriched_payload.setdefault("task_id", self._task_id)

        try:
            loop = __import__("asyncio").get_running_loop()
        except RuntimeError:
            # No running event loop - file fallback exists, UEP emission skipped
            return

        try:

            async def _emit() -> None:
                from polaris.kernelone.events.uep_publisher import UEPEventPublisher

                publisher = UEPEventPublisher()
                await publisher.publish_stream_event(
                    workspace=workspace,
                    run_id=run_id,
                    role=role,
                    event_type=event_type,
                    payload=enriched_payload,
                )

            loop.call_soon_threadsafe(loop.create_task, _emit())
        except (RuntimeError, ValueError):
            # Fire-and-forget - must never break sync execution
            pass

    def execute_tool(self, tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
        """执行工具调用（带权限检查）

        Args:
            tool_name: 工具名称
            tool_args: 工具参数

        Returns:
            工具执行结果

        Raises:
            ToolAuthorizationError: 权限检查失败
        """
        logger.debug(
            "[execute_tool] called: tool=%s args=%s run_id=%s",
            tool_name,
            tool_args,
            self._run_id,
        )
        # 1) 工具别名先归一化，再进入授权/路径/命令门禁。
        can_execute, reason = self.check_tool_permission(tool_name, tool_args)
        if not can_execute:
            logger.warning(f"[{self.profile.role_id}] 工具调用被拒绝: {tool_name} - {reason}")
            self._emit_tool_event_to_journal(
                event_type="tool_error",
                tool_name=tool_name,
                error=f"授权失败: {reason}",
            )
            self._schedule_uep_event(
                event_type="tool_error",
                tool_name=tool_name,
                payload={"tool": tool_name, "error": f"授权失败: {reason}"},
            )
            raise ToolAuthorizationError(reason)

        # 2) 执行工具（通过 llm_toolkit 执行器）
        requested_tool = self._normalize_tool_name(tool_name)
        requested_args = self._normalize_tool_args(requested_tool, tool_args)
        execution_tool = requested_tool
        execution_args = requested_args

        # Emit tool_call AFTER auth succeeds (both file + UEP)
        self._emit_tool_event_to_journal(
            event_type="tool_call",
            tool_name=requested_tool,
            arguments=execution_args,
        )
        self._schedule_uep_event(
            event_type="tool_call",
            tool_name=requested_tool,
            payload={"tool": requested_tool, "args": execution_args},
        )

        try:
            from polaris.kernelone.llm.toolkit import AgentAccelToolExecutor

            executor = AgentAccelToolExecutor(
                workspace=self.workspace or ".",
                session_id=self.session_id,
                session_memory_provider=self.session_memory_provider,
                failure_budget=self._failure_budget,
                allowed_tools=self._get_allowed_tools_for_executor(),
                capability_scope=list(self._capability_scope),
                capability_token=self._capability_token,
            )
            try:
                result = executor.execute(execution_tool, dict(execution_args))
            finally:
                close_sync = getattr(executor, "close_sync", None)
                if callable(close_sync):
                    close_sync()

            normalized_success = True
            normalized_payload: Any = result
            error_message = ""
            effect_receipt = result.get("effect_receipt") if isinstance(result, dict) else None
            if isinstance(result, dict):
                ok_flag = result.get("ok")
                success_flag = result.get("success")
                if isinstance(ok_flag, bool):
                    normalized_success = ok_flag
                elif isinstance(success_flag, bool):
                    normalized_success = success_flag
                else:
                    normalized_success = not bool(str(result.get("error") or "").strip())

                if "result" in result:
                    normalized_payload = result.get("result")
                elif "data" in result:
                    normalized_payload = result.get("data")
                if effect_receipt is None and isinstance(normalized_payload, dict):
                    nested_effect_receipt = normalized_payload.get("effect_receipt")
                    if isinstance(nested_effect_receipt, dict):
                        effect_receipt = nested_effect_receipt

                if not normalized_success:
                    error_message = (
                        str(result.get("error") or result.get("message") or "").strip()
                        or "Tool returned unsuccessful result"
                    )
                    # Append suggestion if present - it contains diagnostic info
                    # that helps LLM correct its next attempt (e.g., actual content snippets)
                    suggestion = result.get("suggestion")
                    if suggestion and str(suggestion).strip():
                        error_message = f"{error_message} | {suggestion}"

            # Extract error context from tool result for unified error handling
            # This propagates error_type/retryable/blocked_tools/loop_break from tool executor through
            # to TransactionKernel so workflow can make decisions based on error semantics.
            error_type: str | None = None
            retryable = True
            blocked_tools: tuple[str, ...] = ()
            loop_break = False
            if not normalized_success and isinstance(result, dict):
                error_type = result.get("error_type")
                retryable = result.get("retryable", True)
                blocked_tools = tuple(result.get("blocked_tools") or [])
                loop_break = result.get("loop_break", False)

            if normalized_success:
                logger.debug(
                    "[%s] 工具执行成功: requested=%s executed=%s",
                    self.profile.role_id,
                    requested_tool,
                    execution_tool,
                )
            else:
                logger.warning(
                    "[%s] 工具执行返回失败结果: %s - %s",
                    self.profile.role_id,
                    execution_tool,
                    error_message or "unknown_error",
                )

            # Count ALL executions (success and failure) toward the per-turn limit
            self._execution_count += 1

            # Emit tool_result to file (fallback) + UEP
            # Include error_type/retryable/loop_break at top level for visibility
            self._emit_tool_event_to_journal(
                event_type="tool_result",
                tool_name=requested_tool,
                result={
                    "success": normalized_success,
                    "payload": normalized_payload,
                    "error": error_message,
                    "error_type": error_type,
                    "retryable": retryable,
                    "loop_break": loop_break,
                },
            )
            self._schedule_uep_event(
                event_type="tool_result",
                tool_name=requested_tool,
                payload={
                    "tool": requested_tool,
                    "success": normalized_success,
                    "result": normalized_payload,
                    "error": error_message,
                    "error_type": error_type,
                    "retryable": retryable,
                    "loop_break": loop_break,
                },
            )

            # Append mutation receipt to platform Run Ledger (fire-and-forget)
            self._append_tool_receipt_to_run_ledger(
                tool_name=execution_tool,
                execution_args=execution_args,
                effect_receipt=effect_receipt if isinstance(effect_receipt, dict) else None,
                normalized_success=normalized_success,
            )

            # 返回结果
            # error_type/retryable/blocked_tools/loop_break are at top level for direct access
            response = {
                "success": normalized_success,
                "tool": requested_tool,
                "result": normalized_payload,
                "error": error_message or None,
                "error_type": error_type,
                "retryable": retryable,
                "blocked_tools": blocked_tools,
                "loop_break": loop_break,
            }
            if isinstance(effect_receipt, dict):
                response["effect_receipt"] = effect_receipt
            return response

        except (RuntimeError, ValueError) as e:
            logger.error(
                "[%s] 工具执行失败: requested=%s executed=%s - %s",
                self.profile.role_id,
                requested_tool,
                execution_tool,
                e,
            )

            # Emit tool_error to file (fallback) + schedule UEP emission
            self._emit_tool_event_to_journal(
                event_type="tool_error",
                tool_name=requested_tool,
                error=str(e),
            )
            self._schedule_uep_event(
                event_type="tool_error",
                tool_name=requested_tool,
                payload={"tool": requested_tool, "error": str(e)},
            )
            raise

    def execute_tools(self, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """批量执行工具调用

        Args:
            tool_calls: 工具调用列表 [{"tool": name, "args": {...}}, ...]

        Returns:
            执行结果列表
        """
        results = []
        for call in tool_calls:
            tool_name = call.get("tool") or call.get("name", "")
            tool_args = call.get("args") or call.get("arguments", {})
            requested_name = str(tool_name or "").strip()
            canonical_tool_name = self._normalize_tool_name(requested_name)
            if canonical_tool_name == "write_file" and requested_name.lower() != "write_file":
                execution_tool_name = canonical_tool_name
                execution_tool_args = self._normalize_tool_args(canonical_tool_name, tool_args)
            else:
                execution_tool_name = requested_name
                execution_tool_args = tool_args

            try:
                result = self.execute_tool(execution_tool_name, execution_tool_args)
                results.append(result)
            except ToolAuthorizationError as e:
                results.append(
                    {
                        "success": False,
                        "tool": execution_tool_name,
                        "error": str(e),
                        "authorized": False,
                    }
                )

        return results

    def filter_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """根据策略过滤工具列表

        Args:
            tools: 原始工具定义列表

        Returns:
            过滤后的工具列表
        """
        # Get TS availability first
        from polaris.kernelone.llm.toolkit.ts_availability import is_tree_sitter_available

        ts_availability = is_tree_sitter_available()

        # Filter by TS availability using get_available_tools
        tool_names = [t.get("name", "").lower() for t in tools]
        available_names = get_available_tools(tool_names, ts_availability)
        available_names_set: set[str] = set(available_names)

        if not self.policy.whitelist:
            return []  # 空白名单 = 禁止所有工具

        allowed = []
        for tool in tools:
            tool_name = tool.get("name", "").lower()
            # Skip tools not available due to TS unavailability
            if tool_name not in available_names_set:
                continue
            can_use, _ = self.check_tool_permission(tool_name)
            if can_use:
                allowed.append(tool)

        return allowed

    def get_available_tools(self) -> list[str]:
        """获取角色可用的工具列表"""
        if not self.policy.whitelist:
            return []
        return self.policy.whitelist.copy()

    def _is_code_write_tool(self, tool_name: str) -> bool:
        """检查是否为代码写入类工具"""
        return is_code_write_tool(tool_name)

    def _is_command_execution_tool(self, tool_name: str) -> bool:
        """检查是否为命令执行类工具"""
        return is_command_execution_tool(tool_name)

    def _is_file_delete_tool(self, tool_name: str) -> bool:
        """检查是否为文件删除类工具"""
        return is_file_delete_tool(tool_name)

    def _is_dangerous_command(self, command: str) -> bool:
        """检查命令是否包含危险操作。

        Uses canonical is_dangerous_command from kernelone.security.dangerous_patterns.
        """
        from polaris.kernelone.security.dangerous_patterns import is_dangerous_command

        return is_dangerous_command(command)

    def _is_path_traversal(self, path: str) -> bool:
        """检查路径是否包含穿越序列

        Uses canonical is_path_traversal from kernelone.security.dangerous_patterns,
        with URL decoding to handle encoded traversal patterns.
        """

        # 1. URL 编码检测（多种编码格式）
        try:
            decoded = urllib.parse.unquote(path)
            decoded_again = urllib.parse.unquote(decoded)
            if decoded_again != decoded:
                path = decoded_again
        except (RuntimeError, ValueError) as exc:
            logger.debug("url unquote failed, using original path: %s", exc)

        # 2. 使用 canonical 源头进行穿越模式检测
        return is_path_traversal(path)

    def _normalize_tool_name(self, tool_name: str) -> str:
        """规范化工具名称，使用 canonicalize_tool_name 进行完整别名解析。

        Args:
            tool_name: 原始工具名称（可能是别名）

        Returns:
            规范化后的工具名称（canonical name）
        """
        return canonicalize_tool_name(tool_name, keep_unknown=True)

    @staticmethod
    def _normalize_tool_args(tool_name: str, tool_args: dict[str, Any] | None) -> dict[str, Any]:
        return normalize_tool_arguments(tool_name, tool_args)

    def _validate_scope(self, scope: Any) -> bool:
        """验证scope约束"""
        return not (isinstance(scope, dict) and "files" not in scope and "directories" not in scope)

    def _match_wildcard(self, tool_name: str, pattern: str) -> bool:
        """通配符匹配"""
        import fnmatch

        return fnmatch.fnmatch(tool_name.lower(), pattern.lower())

    def _is_mutation_tool(self, canonical_tool_name: str) -> bool:
        """Return True for tools that mutate workspace state (write/execute)."""
        return (
            self._is_code_write_tool(canonical_tool_name)
            or self._is_command_execution_tool(canonical_tool_name)
            or self._is_file_delete_tool(canonical_tool_name)
        )

    def _append_tool_receipt_to_run_ledger(
        self,
        *,
        tool_name: str,
        execution_args: dict[str, Any],
        effect_receipt: dict[str, Any] | None,
        normalized_success: bool,
    ) -> None:
        """Append a tool_receipt event to the platform Run Ledger.

        Fire-and-forget: failures are logged but never break tool execution.
        Only mutation tools (write/command/delete) produce ledger receipts.
        """
        if not normalized_success:
            return
        if not self._is_mutation_tool(tool_name):
            return
        run_id = str(self._run_id or "").strip()
        if not run_id:
            return
        workspace = str(self.workspace or "").strip()
        if not workspace:
            return

        try:
            # Determine the target path from args
            target_path = ""
            for key in ("path", "file", "filepath", "target", "command"):
                value = execution_args.get(key)
                if isinstance(value, str) and value.strip():
                    target_path = value.strip()
                    break

            event: dict[str, Any] = {
                "schema_version": 1,
                "event_type": "tool_receipt",
                "run_id": run_id,
                "tool": tool_name,
                "target_path": target_path,
                "success": True,
                "ts": utc_now_iso(),
            }
            if self._task_id:
                event["task_id"] = self._task_id
            if self._capability_token:
                event["job_token_id"] = str(self._capability_token.get("token_id") or "").strip()
                envelope_hash = str(self._capability_token.get("execution_envelope_hash") or "").strip()
                if envelope_hash:
                    event["execution_envelope_hash"] = envelope_hash
            if isinstance(effect_receipt, dict) and effect_receipt:
                event["effect_receipt"] = effect_receipt
                # Compute content hash delta if effect_receipt contains file info
                old_hash = str(effect_receipt.get("old_hash") or "").strip()
                new_hash = str(effect_receipt.get("new_hash") or "").strip()
                if new_hash:
                    event["file_hash_delta"] = {
                        "old": old_hash,
                        "new": new_hash,
                        "changed": old_hash != new_hash,
                    }

            event["content_id"] = stable_hash(
                {k: v for k, v in event.items() if k not in {"content_id", "event_id", "recorded_at"}}
            )
            event["event_id"] = event["content_id"]

            append_run_ledger_event(
                AppendRunLedgerEventCommandV1(
                    workspace=str(Path(workspace)),
                    run_id=run_id,
                    event=event,
                )
            )
        except (OSError, RuntimeError, ValueError) as exc:
            logger.debug("Run Ledger tool receipt append failed: %s", exc)


class ToolGatewayManager:
    """工具网关管理器

    管理多个角色的工具网关实例。
    """

    def __init__(self, workspace: str = "") -> None:
        self.workspace = workspace
        self._gateways: dict[str, RoleToolGateway] = {}

    def get_gateway(self, profile: RoleProfile) -> RoleToolGateway:
        """获取角色的工具网关"""
        if profile.role_id not in self._gateways:
            self._gateways[profile.role_id] = RoleToolGateway(profile, self.workspace)
        return self._gateways[profile.role_id]

    def clear(self) -> None:
        """清除所有网关缓存"""
        self._gateways.clear()
