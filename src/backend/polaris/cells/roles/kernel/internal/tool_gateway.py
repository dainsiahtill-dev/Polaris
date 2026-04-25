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
import uuid
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.toolkit.tool_normalization import (
    get_available_tools,
    normalize_tool_arguments,
)
from polaris.kernelone.security.dangerous_patterns import is_path_traversal
from polaris.kernelone.tool_execution.contracts import canonicalize_tool_name
from polaris.kernelone.tool_execution.tool_categories import (
    is_code_write_tool,
    is_command_execution_tool,
    is_file_delete_tool,
)
from polaris.kernelone.utils import utc_now_iso

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleProfile

logger = logging.getLogger(__name__)


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
        iteration: int = 0,
    ) -> None:
        """初始化工具网关

        Args:
            profile: 角色Profile
            workspace: 工作区路径
            session_id: 会话ID
            session_memory_provider: 会话内存提供者
            run_id: 运行时ID（用于事件追踪）
            iteration: 当前 turn 内的工具调用轮次（用于日志审计）
        """
        self.profile = profile
        self.policy = profile.tool_policy
        self.workspace = workspace
        self.session_id = str(session_id or "").strip() or None
        self.session_memory_provider = session_memory_provider
        self._execution_count = 0
        self._run_id = str(run_id or "").strip() or None
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

    def _get_allowed_tools_for_executor(self) -> frozenset[str] | None:
        """Extract the canonical tool whitelist for executor-level enforcement.

        Returns None when no whitelist is configured (all tools allowed),
        or a frozenset of canonical tool names.
        """
        whitelist = getattr(self.policy, "whitelist", None)
        if not whitelist:
            return None
        # Resolve aliases in whitelist to canonical names
        from polaris.kernelone.tool_execution.contracts import canonicalize_tool_name

        return frozenset(canonicalize_tool_name(t, keep_unknown=True) for t in whitelist)

    def close(self) -> None:
        close = getattr(self.session_memory_provider, "close", None)
        if callable(close):
            close()

    @property
    def policy_id(self) -> str:
        """策略标识"""
        return self.policy.policy_id

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
        """检查工具调用权限

        Args:
            tool_name: 工具名称
            tool_args: 工具参数（用于额外校验）

        Returns:
            (是否允许, 拒绝原因)
        """
        requested_tool_name = tool_name

        # 1. 检查白名单（空白名单=禁止所有）—— 必须在别名归一化之前执行 (CLAUDE.md §6.6)
        if self.policy.whitelist:
            whitelist_lower = [w.lower() for w in self.policy.whitelist]
            if requested_tool_name.lower() not in whitelist_lower:
                allowed = any(
                    self._match_wildcard(requested_tool_name.lower(), w.lower()) for w in self.policy.whitelist
                )
                if not allowed:
                    return False, self._format_refusal_message(
                        f"工具 '{requested_tool_name}' 不在角色白名单中", requested_tool_name
                    )

        canonical_tool_name = self._normalize_tool_name(requested_tool_name)

        # 2. 检查黑名单
        if canonical_tool_name in [b.lower() for b in self.policy.blacklist]:
            return False, self._format_refusal_message(
                f"工具 '{canonical_tool_name}' 在角色黑名单中", requested_tool_name
            )

        # 3. 检查代码写入权限
        if self._is_code_write_tool(canonical_tool_name):
            if not self.policy.allow_code_write:
                return False, self._format_refusal_message(
                    f"角色无权使用代码写入工具 '{canonical_tool_name}'", requested_tool_name
                )

            # 额外检查：是否有scope约束
            if tool_args and "scope" in tool_args and not self._validate_scope(tool_args["scope"]):
                return False, self._format_refusal_message("scope约束验证失败", requested_tool_name)

        # 4. 检查命令执行权限
        if self._is_command_execution_tool(canonical_tool_name):
            if not self.policy.allow_command_execution:
                return False, self._format_refusal_message(
                    f"角色无权执行命令 '{canonical_tool_name}'", requested_tool_name
                )

            # 检查命令内容是否危险
            if tool_args and "command" in tool_args and self._is_dangerous_command(tool_args["command"]):
                return False, self._format_refusal_message("命令包含危险操作", requested_tool_name)

        # 5. 检查文件删除权限
        if self._is_file_delete_tool(canonical_tool_name) and not self.policy.allow_file_delete:
            return False, self._format_refusal_message(f"角色无权删除文件 '{canonical_tool_name}'", requested_tool_name)

        # 6. 检查调用次数限制
        if self._execution_count >= self.policy.max_tool_calls_per_turn:
            return False, self._format_refusal_message(
                f"超过单次请求最大工具调用次数 ({self.policy.max_tool_calls_per_turn})", requested_tool_name
            )

        # 7. 路径穿越检查 - 使用 canonical 名称进行参数归一化，避免别名绕过
        if tool_args:
            normalized_args = self._normalize_tool_args(canonical_tool_name, tool_args)
            for key in ["path", "file", "filepath", "target", "source"]:
                if key in normalized_args:
                    path = str(normalized_args[key])
                    if self._is_path_traversal(path):
                        return False, self._format_refusal_message(f"路径 '{path}' 包含穿越序列", requested_tool_name)

        return True, "授权通过"

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
                    payload=payload,
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
        # 1) 白名单拦截必须在映射前：按请求层原始工具名做授权检查 (CLAUDE.md §6.6)
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
            # to TurnEngine so Workflow can make decisions based on error semantics
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

            # 返回结果
            # error_type/retryable/blocked_tools/loop_break are at top level for direct access
            return {
                "success": normalized_success,
                "tool": requested_tool,
                "result": normalized_payload,
                "error": error_message or None,
                "error_type": error_type,
                "retryable": retryable,
                "blocked_tools": blocked_tools,
                "loop_break": loop_break,
            }

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

            try:
                result = self.execute_tool(tool_name, tool_args)
                results.append(result)
            except ToolAuthorizationError as e:
                results.append(
                    {
                        "success": False,
                        "tool": tool_name,
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
