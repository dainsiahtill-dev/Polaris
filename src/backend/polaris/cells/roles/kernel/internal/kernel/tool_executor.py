"""Tool Executor - 工具执行器

负责工具调用执行、写预算分割、事件发射等。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.kernel.error_handler import (
    LLMEventType,
    normalize_observer_value,
)
from polaris.cells.roles.kernel.internal.kernel.helpers import summarize_args
from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway, ToolAuthorizationError
from polaris.cells.roles.kernel.internal.transaction.constants import WRITE_TOOLS

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal._tool_gateway_di import _DelegatingToolGateway
    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
    from polaris.cells.roles.kernel.internal.output_parser import ToolCallResult
    from polaris.cells.roles.kernel.public.contracts import ToolGatewayPort
    from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest

logger = logging.getLogger(__name__)

_DIRECT_SCOPE_KEYS: tuple[str, ...] = (
    "capability_scope",
    "allowed_scope",
    "allowed_scope_paths",
    "allowed_paths",
    "allowed_write_paths",
    "allowed_read_paths",
    "authorized_paths",
    "scope_paths",
    "target_files",
    "pm_target_files",
    "repair_target_files",
)

_SCOPE_CONTAINER_KEYS: tuple[str, ...] = (
    "job_token",
    "control_plane_job_token",
    "capability_token",
    "capability",
    "capability_permissions",
    "permissions",
    "authorization",
    "director_execution_envelope",
    "task_execution_envelope",
    "execution_envelope",
)

_NO_AUTHORIZED_SCOPE_SENTINEL = "__polaris_no_authorized_paths__"
_CAPABILITY_TOKEN_KEYS: tuple[str, ...] = ("job_token", "control_plane_job_token", "capability_token")
_DIRECT_COMMAND_KEYS: tuple[str, ...] = ("allowed_commands", "authorized_commands", "commands")


def _scope_items(value: Any) -> list[str]:
    """Coerce a scope-like value into string path candidates."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        items: list[str] = []
        for key in _DIRECT_SCOPE_KEYS:
            items.extend(_scope_items(value.get(key)))
        for key in ("path", "file", "target", "source"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                items.append(raw.strip())
        return items
    if isinstance(value, (list, tuple, set)):
        items = []
        for entry in value:
            items.extend(_scope_items(entry))
        return items
    return []


def _command_items(value: Any) -> list[str]:
    """Coerce a command allowlist-like value into string candidates."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        items: list[str] = []
        for key in _DIRECT_COMMAND_KEYS:
            items.extend(_command_items(value.get(key)))
        return items
    if isinstance(value, (list, tuple, set)):
        items = []
        for entry in value:
            items.extend(_command_items(entry))
        return items
    return []


def _mapping_from_value(value: Any) -> dict[str, Any] | None:
    """Return a mapping from dict-like platform contracts without importing them."""
    if isinstance(value, dict):
        return value
    to_dict = getattr(value, "to_dict", None)
    if not callable(to_dict):
        return None
    try:
        payload = to_dict()
    except (RuntimeError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_scope_path(value: str) -> str | None:
    """Return a workspace-relative capability path or None for unsafe input."""
    text = str(value or "").replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    if not text or text in {".", "/"}:
        return None
    if text.startswith("/") or (len(text) >= 2 and text[1] == ":"):
        return None
    parts = [part for part in text.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _collect_scope_from_mapping(mapping: dict[str, Any]) -> tuple[list[str], bool]:
    candidates: list[str] = []
    saw_scope_source = False
    for key in _DIRECT_SCOPE_KEYS:
        if key in mapping:
            saw_scope_source = True
            candidates.extend(_scope_items(mapping.get(key)))
    for key in _SCOPE_CONTAINER_KEYS:
        nested = _mapping_from_value(mapping.get(key))
        if nested is not None:
            nested_candidates, nested_saw_source = _collect_scope_from_mapping(nested)
            saw_scope_source = saw_scope_source or nested_saw_source
            candidates.extend(nested_candidates)
    return candidates, saw_scope_source


def derive_role_turn_capability_scope(request: RoleTurnRequest) -> tuple[str, ...] | None:
    """Derive immutable per-turn write capability paths from runtime context.

    Only request metadata/context_override are considered. Model-supplied tool
    arguments are intentionally ignored here and cannot expand this scope later.
    """
    candidates: list[str] = []
    saw_scope_source = False
    for container in (getattr(request, "metadata", None), getattr(request, "context_override", None)):
        mapping = _mapping_from_value(container)
        if mapping is None:
            continue
        container_candidates, container_saw_source = _collect_scope_from_mapping(mapping)
        candidates.extend(container_candidates)
        saw_scope_source = saw_scope_source or container_saw_source

    if not saw_scope_source:
        return None

    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        path = _normalize_scope_path(candidate)
        if not path or path in seen:
            continue
        seen.add(path)
        normalized.append(path)

    return tuple(normalized or (_NO_AUTHORIZED_SCOPE_SENTINEL,))


def _first_capability_token_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    for key in _CAPABILITY_TOKEN_KEYS:
        token = _mapping_from_value(mapping.get(key))
        if token is not None:
            return token
    envelope_token = _execution_envelope_capability_token(mapping)
    if envelope_token:
        return envelope_token
    if any(str(mapping.get(key) or "").strip() for key in ("token_id", "contract_hash", "blueprint_hash")):
        return dict(mapping)
    return {}


def _hash_ref_value(value: Any) -> str:
    mapping = _mapping_from_value(value)
    if mapping is None:
        return ""
    return str(mapping.get("hash") or "").strip()


def _execution_envelope_capability_token(mapping: dict[str, Any]) -> dict[str, Any]:
    envelopes: list[dict[str, Any]] = []
    if str(mapping.get("schema_version") or "").strip() == "polaris.execution_envelope.v1":
        envelopes.append(mapping)
    for key in ("director_execution_envelope", "task_execution_envelope", "execution_envelope"):
        envelope = _mapping_from_value(mapping.get(key))
        if envelope is not None:
            envelopes.append(envelope)
    for envelope in envelopes:
        authorization = _mapping_from_value(envelope.get("authorization")) or {}
        envelope_hash = str(envelope.get("envelope_hash") or "").strip()
        token_id = str(authorization.get("capability_token_ref") or envelope.get("envelope_id") or "").strip()
        if not token_id and envelope_hash:
            token_id = f"execution-envelope:{envelope_hash[:16]}"
        if not token_id:
            continue
        handoff_decision = _mapping_from_value(envelope.get("handoff_decision")) or {}
        return {
            "source": "director.execution_envelope.authorization",
            "token_id": token_id,
            "run_id": str(envelope.get("run_id") or ""),
            "project_id": str(envelope.get("workspace") or ""),
            "stage": "director_execution",
            "contract_hash": _hash_ref_value(envelope.get("pm_contract")),
            "blueprint_hash": _hash_ref_value(envelope.get("ce_blueprint")),
            "execution_envelope_hash": envelope_hash,
            "allowed_paths": authorization.get("allowed_write_paths") or authorization.get("target_files") or (),
            "target_files": authorization.get("target_files") or (),
            "allowed_commands": authorization.get("allowed_commands") or (),
            "capability_audit": {"ok": bool(handoff_decision.get("allowed"))},
        }
    return {}


def _job_token_evidence(value: dict[str, Any], scope: tuple[str, ...] | None) -> dict[str, Any]:
    if not value:
        return {}
    token_id = str(value.get("token_id") or "").strip()
    if not token_id:
        return {}
    capability_audit = value.get("capability_audit")
    capability_audit_map = capability_audit if isinstance(capability_audit, dict) else {}
    evidence = {
        "source": str(value.get("source") or "control_plane.job_token"),
        "token_id": token_id,
        "run_id": str(value.get("run_id") or ""),
        "project_id": str(value.get("project_id") or ""),
        "stage": str(value.get("stage") or ""),
        "contract_hash": str(value.get("contract_hash") or ""),
        "blueprint_hash": str(value.get("blueprint_hash") or ""),
        "execution_envelope_hash": str(value.get("execution_envelope_hash") or ""),
        "capability_audit_ok": bool(capability_audit_map.get("ok")),
        "allowed_scope": list(scope or ()),
    }
    allowed_commands = _command_items(value)
    if allowed_commands:
        evidence["allowed_commands"] = allowed_commands
    return evidence


def derive_role_turn_capability_token(
    request: RoleTurnRequest,
    capability_scope: tuple[str, ...] | None,
) -> dict[str, Any]:
    """Derive immutable Job Token evidence for tool receipts."""

    for container in (getattr(request, "metadata", None), getattr(request, "context_override", None)):
        mapping = _mapping_from_value(container)
        if mapping is None:
            continue
        token = _first_capability_token_mapping(mapping)
        evidence = _job_token_evidence(token, capability_scope)
        if evidence:
            if not evidence.get("execution_envelope_hash"):
                envelope_hash = str(mapping.get("execution_envelope_hash") or "").strip()
                if envelope_hash:
                    evidence["execution_envelope_hash"] = envelope_hash
            return evidence
    return {}


class KernelToolExecutor:
    """内核工具执行器

    负责：
    - 创建工具网关
    - 执行工具调用
    - 写预算分割
    - 发射工具事件
    """

    __slots__ = ("_kernel", "_workspace")

    def __init__(self, kernel: RoleExecutionKernel, workspace: str) -> None:
        """初始化工具执行器

        Args:
            kernel: RoleExecutionKernel 实例
            workspace: 工作区路径
        """
        self._kernel = kernel
        self._workspace = workspace

    def create_gateway(
        self,
        profile: RoleProfile,
        request: RoleTurnRequest,
        tool_gateway: ToolGatewayPort | None = None,
    ) -> RoleToolGateway | _DelegatingToolGateway:
        """Create one per-request tool gateway with session-aware execution context.

        Args:
            profile: 角色配置
            request: 回合请求
            tool_gateway: 外部注入的工具网关

        Returns:
            工具网关实例
        """
        from polaris.cells.roles.kernel.internal._tool_gateway_di import _DelegatingToolGateway
        from polaris.cells.roles.session.public import RoleSessionContextMemoryService

        # M1: 检查是否注入了外部 tool_gateway
        if tool_gateway is not None:
            if isinstance(tool_gateway, RoleToolGateway):
                return tool_gateway
            return _DelegatingToolGateway(tool_gateway)

        # 默认行为：每次请求创建新实例
        session_id = str((request.metadata or {}).get("session_id") or "").strip() or None
        memory_provider = RoleSessionContextMemoryService() if session_id else None
        logger.debug(
            "[create_gateway] request.run_id=%s profile=%s",
            getattr(request, "run_id", None),
            getattr(profile, "role_id", None),
        )
        capability_scope = derive_role_turn_capability_scope(request)
        capability_token = derive_role_turn_capability_token(request, capability_scope)
        return RoleToolGateway(
            profile,
            self._workspace,
            session_id=session_id,
            session_memory_provider=memory_provider,
            run_id=getattr(request, "run_id", None),
            task_id=getattr(request, "task_id", None),
            capability_scope=capability_scope,
            capability_token=capability_token,
        )

    async def execute_tools(
        self,
        profile: RoleProfile,
        request: RoleTurnRequest,
        tool_calls: list[ToolCallResult],
        tool_gateway: ToolGatewayPort | None = None,
    ) -> list[dict[str, Any]]:
        """执行工具调用

        Args:
            profile: 角色配置
            request: 回合请求
            tool_calls: 工具调用列表
            tool_gateway: 外部注入的工具网关

        Returns:
            工具执行结果列表
        """
        gateway = self.create_gateway(profile, request, tool_gateway)
        gateway.reset_execution_count()

        try:
            results = []
            for call in tool_calls:
                tool_name = str(call.tool or "").strip()
                try:
                    result = gateway.execute_tool(tool_name, call.args)
                    results.append(result)
                except ToolAuthorizationError as exc:
                    logger.warning("工具授权被拒绝 (%s): %s", tool_name, exc)
                    results.append(
                        {
                            "success": False,
                            "tool": tool_name,
                            "error": str(exc),
                            "authorized": False,
                        }
                    )
                except (RuntimeError, ValueError) as exc:
                    logger.warning("工具执行异常 (%s): %s", tool_name, exc)
                    results.append(
                        {
                            "success": False,
                            "tool": tool_name,
                            "error": str(exc),
                        }
                    )

            return results
        finally:
            gateway.close()

    @staticmethod
    def split_tool_calls_by_write_budget(
        role_id: str,
        tool_calls: list[ToolCallResult],
    ) -> tuple[list[ToolCallResult], list[ToolCallResult], int]:
        """Split tool calls into executable and deferred by write-call budget.

        Args:
            role_id: 角色标识
            tool_calls: 工具调用列表

        Returns:
            (executable_calls, deferred_calls, write_limit)
        """
        from polaris.cells.roles.kernel.internal.kernel.helpers import resolve_role_write_call_limit

        if not tool_calls:
            return [], [], 0
        write_limit = resolve_role_write_call_limit(role_id)
        if write_limit <= 0:
            return list(tool_calls), [], 0

        executable: list[ToolCallResult] = []
        deferred: list[ToolCallResult] = []
        write_calls_seen = 0
        for call in tool_calls:
            tool_name = str(getattr(call, "tool", "") or "").strip().lower()
            is_write_tool = tool_name in WRITE_TOOLS
            if is_write_tool and write_calls_seen >= write_limit:
                deferred.append(call)
                continue
            executable.append(call)
            if is_write_tool:
                write_calls_seen += 1

        return executable, deferred, write_limit

    def emit_tool_execute_events(
        self,
        profile: RoleProfile,
        run_id: str,
        task_id: str | None,
        attempt: int,
        mode_value: str,
        tool_calls: list[ToolCallResult],
        emit_event: Any,
    ) -> None:
        """发射工具执行前事件

        Args:
            profile: 角色配置
            run_id: 运行 ID
            task_id: 任务 ID
            attempt: 尝试次数
            mode_value: 模式值
            tool_calls: 工具调用列表
            emit_event: 事件发射函数
        """
        for call in tool_calls:
            tool_name = str(call.tool or "").strip()
            safe_args = normalize_observer_value(call.args if isinstance(call.args, dict) else {})
            emit_event(
                event_type=LLMEventType.TOOL_EXECUTE,
                role=profile.role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                tool_calls_count=1,
                metadata={
                    "mode": mode_value,
                    "tool_name": tool_name,
                    "tool": tool_name,
                    "args": safe_args,
                    "args_summary": summarize_args(safe_args),
                },
            )

    def emit_tool_result_events_and_collect_errors(
        self,
        profile: RoleProfile,
        run_id: str,
        task_id: str | None,
        attempt: int,
        mode_value: str,
        tool_calls: list[ToolCallResult],
        executed_tool_results: list[dict[str, Any]],
        emit_event: Any,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """发射工具结果事件并收集错误

        Args:
            profile: 角色配置
            run_id: 运行 ID
            task_id: 任务 ID
            attempt: 尝试次数
            mode_value: 模式值
            tool_calls: 工具调用列表
            executed_tool_results: 已执行的工具结果
            emit_event: 事件发射函数

        Returns:
            (errors, results)
        """
        failed_tool_errors: list[str] = []
        for i, result in enumerate(executed_tool_results):
            call = tool_calls[i] if i < len(tool_calls) else None
            result_payload = result if isinstance(result, dict) else {}
            safe_result_payload = normalize_observer_value(result_payload)
            normalized_result = result_payload.get("result")
            if isinstance(normalized_result, dict):
                safe_result = normalize_observer_value(normalized_result)
            elif normalized_result is not None:
                safe_result = {"value": normalize_observer_value(normalized_result)}
            else:
                safe_result = {}
            emit_event(
                event_type=LLMEventType.TOOL_RESULT,
                role=profile.role_id,
                run_id=run_id,
                task_id=task_id,
                attempt=attempt,
                tool_calls_count=1,
                tool_errors_count=0 if bool(result_payload.get("success", False)) else 1,
                metadata={
                    "tool_name": str(getattr(call, "tool", "") or "").strip(),
                    "tool": str(getattr(call, "tool", "") or "").strip(),
                    "mode": mode_value,
                    "args": normalize_observer_value(
                        getattr(call, "args", {}) if isinstance(getattr(call, "args", {}), dict) else {}
                    ),
                    "success": bool(result_payload.get("success", False)),
                    "authorized": result_payload.get("authorized"),
                    "result": safe_result,
                    "result_payload": safe_result_payload,
                    "error": str(result_payload.get("error") or "").strip() or None,
                },
            )
            if not result_payload.get("success", False):
                tool_name = tool_calls[i].tool if i < len(tool_calls) else "unknown"
                failed_tool_errors.append(
                    f"工具 '{tool_name}' 执行失败: {result_payload.get('error', 'Unknown error')}"
                )
        return failed_tool_errors, executed_tool_results

    @staticmethod
    def append_deferred_notice(
        deferred_tool_calls: list[ToolCallResult],
        write_call_limit: int,
        executed_tool_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """追加 deferred notice 到结果列表

        Args:
            deferred_tool_calls: 待执行的工具调用
            write_call_limit: 写调用限制
            executed_tool_results: 已执行的工具结果

        Returns:
            带有 deferred notice 的结果列表
        """
        if deferred_tool_calls:
            executed_tool_results = list(executed_tool_results)
            executed_tool_results.append(
                {
                    "success": True,
                    "tool": "write_call_budget",
                    "result": {
                        "deferred_count": len(deferred_tool_calls),
                        "limit_per_turn": write_call_limit,
                        "message": "Deferred write tool calls to next internal tool round.",
                    },
                    "error": None,
                }
            )
        return executed_tool_results

    @staticmethod
    def log_deferred_write_calls(
        role_id: str,
        deferred_tool_calls: list[ToolCallResult],
        write_call_limit: int,
    ) -> None:
        """记录 deferred write tool calls

        Args:
            role_id: 角色标识
            deferred_tool_calls: 待执行的工具调用
            write_call_limit: 写调用限制
        """
        if deferred_tool_calls:
            logger.info(
                "[%s] deferred %d write tool calls (limit=%d per turn)",
                role_id,
                len(deferred_tool_calls),
                write_call_limit,
            )


__all__ = ["KernelToolExecutor", "derive_role_turn_capability_scope"]
