"""Tool Executor Service - 工具执行服务层

根据 KERNELONE_KERNEL_REFACTOR_BLUEPRINT 实现的服务层组件，
负责工具调用的执行、超时控制和错误分类。

Architecture:
    - ToolExecutor: 服务层核心，执行工具调用
    - ToolCall: 工具调用数据模型
    - ToolResult: 工具执行结果数据模型
    - ToolError: 结构化工具错误异常

Responsibilities:
    - 单工具执行（带超时控制）
    - 批量工具执行（并发控制）
    - 错误分类和重试提示
    - 结果规范化
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from polaris.kernelone.constants import DIRECTOR_TIMEOUT_SECONDS
from polaris.kernelone.errors import ErrorCategory as _CanonicalErrorCategory

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleProfile

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 常量定义
# ═══════════════════════════════════════════════════════════════════════════

# 超时配置（秒）
#: Default timeout for Director role (sourced from kernelone.constants)
TIMEOUT_DIRECTOR_SECONDS: float = DIRECTOR_TIMEOUT_SECONDS
TIMEOUT_DEFAULT_SECONDS = 60  # 其他角色默认超时

# 批量执行并发限制
MAX_CONCURRENT_TOOL_EXECUTIONS = 5


# ═══════════════════════════════════════════════════════════════════════════
# 枚举类型
# ═══════════════════════════════════════════════════════════════════════════


def __getattr__(name: str):
    """Provide deprecation warnings for direct module imports."""
    if name == "ErrorCategory":
        warnings.warn(
            "ErrorCategory has been moved to polaris.kernelone.errors. "
            "Please update imports to use: from polaris.kernelone.errors import ErrorCategory",
            DeprecationWarning,
            stacklevel=2,
        )
        return _CanonicalErrorCategory
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# For type checking and runtime compatibility
ErrorCategory = _CanonicalErrorCategory


# ═══════════════════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ToolCall:
    """工具调用请求数据模型

    Attributes:
        tool: 工具名称（规范化后的 canonical name）
        args: 工具参数字典
        call_id: 可选的调用标识符（用于追踪）
    """

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""

    def __post_init__(self) -> None:
        # 确保 tool 名称非空且有效
        normalized_tool = str(self.tool or "").strip()
        if not normalized_tool:
            object.__setattr__(self, "tool", "")
        else:
            object.__setattr__(self, "tool", normalized_tool)
        # 确保 args 是字典
        if not isinstance(self.args, dict):
            object.__setattr__(self, "args", {})


@dataclass(frozen=True)
class ToolResult:
    """工具执行结果数据模型

    Attributes:
        success: 是否执行成功
        tool: 工具名称
        result: 执行结果数据
        error: 错误信息（如果失败）
        error_category: 错误分类
        retryable: 是否可重试
        execution_time_ms: 执行耗时（毫秒）
        call_id: 调用标识符（与 ToolCall 对应）
    """

    success: bool
    tool: str
    result: Any = None
    error: str | None = None
    error_category: ErrorCategory = ErrorCategory.UNKNOWN
    retryable: bool = False
    execution_time_ms: float = 0.0
    call_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式（用于序列化）"""
        return {
            "success": self.success,
            "tool": self.tool,
            "result": self.result,
            "error": self.error,
            "error_category": self.error_category.value,
            "retryable": self.retryable,
            "execution_time_ms": self.execution_time_ms,
            "call_id": self.call_id,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 异常类型
# ═══════════════════════════════════════════════════════════════════════════


class ToolError(Exception):
    """结构化工具错误异常

    提供丰富的错误上下文，支持重试决策。

    Attributes:
        message: 错误消息
        error_code: 错误代码
        error_category: 错误分类
        retryable: 是否可重试
        context: 额外上下文信息
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "tool_error",
        error_category: ErrorCategory = ErrorCategory.UNKNOWN,
        retryable: bool = False,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(str(message or "").strip() or "Tool execution failed")
        self.error_code = str(error_code or "").strip() or "tool_error"
        self.error_category = error_category
        self.retryable = bool(retryable)
        self.context = dict(context) if context else {}

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式（符合 ADR-003 错误规范）"""
        return {
            "code": self.error_code,
            "message": str(self),
            "category": self.error_category.value,
            "retryable": self.retryable,
            "details": dict(self.context),
        }


class ToolTimeoutError(ToolError):
    """工具执行超时错误"""

    def __init__(
        self,
        message: str,
        *,
        timeout_seconds: float = 0.0,
        context: dict[str, Any] | None = None,
    ) -> None:
        merged_context = dict(context) if context else {}
        merged_context["timeout_seconds"] = timeout_seconds
        super().__init__(
            message,
            error_code="tool_timeout",
            error_category=ErrorCategory.TIMEOUT,
            retryable=True,
            context=merged_context,
        )
        self.timeout_seconds = timeout_seconds


class ToolAuthorizationError(ToolError):
    """工具授权错误"""

    def __init__(
        self,
        message: str,
        *,
        tool_name: str = "",
        context: dict[str, Any] | None = None,
    ) -> None:
        merged_context = dict(context) if context else {}
        merged_context["tool_name"] = tool_name
        super().__init__(
            message,
            error_code="tool_unauthorized",
            error_category=ErrorCategory.AUTHORIZATION,
            retryable=False,
            context=merged_context,
        )
        self.tool_name = tool_name


# ═══════════════════════════════════════════════════════════════════════════
# 协议定义（用于依赖注入）
# ═══════════════════════════════════════════════════════════════════════════


class ToolExecutionBackend(Protocol):
    """工具执行后端协议

    允许注入不同的工具执行实现（真实执行、Mock、测试替身等）
    """

    async def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        workspace: str = "",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """执行工具调用

        Args:
            tool_name: 工具名称
            args: 工具参数
            workspace: 工作区路径
            session_id: 会话ID

        Returns:
            工具执行结果字典，必须包含 "success" 键
        """
        ...


# ═══════════════════════════════════════════════════════════════════════════
# 核心服务类
# ═══════════════════════════════════════════════════════════════════════════


class ToolExecutor:
    """工具执行服务

    负责工具调用的执行、超时控制和错误分类。
    支持单工具执行和批量执行两种模式。

    Responsibilities:
        - 单工具执行（带超时控制）
        - 批量执行（并发控制）
        - 错误分类和重试提示
        - 结果规范化

    Example:
        >>> executor = ToolExecutor(workspace=".")
        >>> result = await executor.execute_single(
        ...     call=ToolCall(tool="read_file", args={"path": "test.py"}),
        ...     profile=role_profile
        ... )
        >>> if result.success:
        ...     print(result.result)
        ... else:
        ...     print(f"Error: {result.error} (retryable: {result.retryable})")
    """

    def __init__(
        self,
        workspace: str = ".",
        *,
        backend: ToolExecutionBackend | None = None,
        default_timeout: int = TIMEOUT_DEFAULT_SECONDS,
        director_timeout: float | int = TIMEOUT_DIRECTOR_SECONDS,
    ) -> None:
        """初始化工具执行器

        Args:
            workspace: 工作区路径
            backend: 工具执行后端（可选，用于依赖注入）
            default_timeout: 默认超时时间（秒）
            director_timeout: Director 角色超时时间（秒）
        """
        self._workspace = str(workspace or ".").strip() or "."
        self._backend = backend
        self._default_timeout = max(1, int(default_timeout))
        self._director_timeout = max(1, int(director_timeout))

        # 并发控制信号量
        self._concurrency_limit = asyncio.Semaphore(MAX_CONCURRENT_TOOL_EXECUTIONS)

    def _resolve_timeout(self, profile: RoleProfile) -> int:
        """根据角色解析超时时间

        Director 角色需要更长的超时时间（代码执行可能耗时较长）

        Args:
            profile: 角色配置

        Returns:
            超时时间（秒）
        """
        role_id = str(getattr(profile, "role_id", "") or "").strip().lower()

        # Director 角色使用更长的超时
        if role_id in ("director", "executor", "worker"):
            return self._director_timeout

        # 检查角色是否配置了自定义超时
        tool_policy = getattr(profile, "tool_policy", None)
        if tool_policy:
            configured_timeout = getattr(tool_policy, "tool_timeout_seconds", 0)
            if isinstance(configured_timeout, int) and configured_timeout > 0:
                return configured_timeout

        return self._default_timeout

    def _classify_error(
        self,
        error: Exception,
        tool_name: str,
    ) -> tuple[ErrorCategory, bool]:
        """分类错误并决定是否可以重试

        Args:
            error: 异常对象
            tool_name: 工具名称

        Returns:
            (错误分类, 是否可重试)
        """
        error_msg = str(error or "").lower()

        # 超时错误
        if isinstance(error, asyncio.TimeoutError) or "timeout" in error_msg:
            return ErrorCategory.TIMEOUT, True

        # 速率限制
        if any(kw in error_msg for kw in ("rate limit", "rate_limit", "too many requests", "429")):
            return ErrorCategory.RATE_LIMIT, True

        # 网络错误
        if any(kw in error_msg for kw in ("network", "connection", "timeout", "unreachable", "refused")):
            return ErrorCategory.NETWORK_ERROR, True

        # 服务不可用
        if any(kw in error_msg for kw in ("unavailable", "503", "502", "504", "maintenance")):
            return ErrorCategory.SERVICE_UNAVAILABLE, True

        # 授权错误
        if isinstance(error, ToolAuthorizationError) or any(
            kw in error_msg for kw in ("unauthorized", "forbidden", "permission", "access denied", "403")
        ):
            return ErrorCategory.AUTHORIZATION, False

        # 权限不足
        if any(kw in error_msg for kw in ("permission denied", "not permitted", "insufficient privileges")):
            return ErrorCategory.PERMISSION_DENIED, False

        # 资源不存在
        if any(kw in error_msg for kw in ("not found", "does not exist", "no such file", "404")):
            return ErrorCategory.NOT_FOUND, False

        # 参数验证失败
        if any(kw in error_msg for kw in ("validation", "invalid", "required", "missing", "bad request", "400")):
            return ErrorCategory.VALIDATION, False

        # 无效参数
        if any(kw in error_msg for kw in ("invalid argument", "illegal argument", "type error")):
            return ErrorCategory.INVALID_ARGUMENT, False

        # 不支持的操作
        if any(kw in error_msg for kw in ("not supported", "unsupported", "not implemented")):
            return ErrorCategory.UNSUPPORTED_OPERATION, False

        # 临时故障（可重试）
        if any(kw in error_msg for kw in ("temporary", "transient", "retry")):
            return ErrorCategory.TEMPORARY_FAILURE, True

        # 默认：未知错误，不可重试
        return ErrorCategory.UNKNOWN, False

    def _normalize_result(
        self,
        raw_result: Any,
        tool_name: str,
        call_id: str,
        execution_time_ms: float,
    ) -> ToolResult:
        """规范化工具执行结果

        Args:
            raw_result: 原始执行结果
            tool_name: 工具名称
            call_id: 调用标识符
            execution_time_ms: 执行耗时

        Returns:
            规范化的 ToolResult
        """
        # 如果是字典，提取标准字段
        if isinstance(raw_result, dict):
            success = bool(raw_result.get("success", raw_result.get("ok", True)))
            result_data = raw_result.get("result", raw_result.get("data", raw_result))
            error_msg = raw_result.get("error", raw_result.get("message", None))

            # 如果 error 存在但 success 为 True，调整为失败状态
            if error_msg and success and isinstance(error_msg, str) and error_msg.strip():
                success = False

            return ToolResult(
                success=success,
                tool=tool_name,
                result=result_data if success else None,
                error=error_msg if not success else None,
                error_category=ErrorCategory.UNKNOWN if success else self._classify_error_from_message(error_msg),
                retryable=False if success else self._is_retryable_from_message(error_msg),
                execution_time_ms=execution_time_ms,
                call_id=call_id,
            )

        # 非字典结果，视为成功
        return ToolResult(
            success=True,
            tool=tool_name,
            result=raw_result,
            error=None,
            error_category=ErrorCategory.UNKNOWN,
            retryable=False,
            execution_time_ms=execution_time_ms,
            call_id=call_id,
        )

    def _classify_error_from_message(self, error_msg: str | None) -> ErrorCategory:
        """从错误消息分类错误类型"""
        if not error_msg:
            return ErrorCategory.UNKNOWN

        error_lower = str(error_msg).lower()

        if "timeout" in error_lower:
            return ErrorCategory.TIMEOUT
        if "rate limit" in error_lower or "429" in error_lower:
            return ErrorCategory.RATE_LIMIT
        if "unauthorized" in error_lower or "forbidden" in error_lower or "403" in error_lower:
            return ErrorCategory.AUTHORIZATION
        if "not found" in error_lower or "404" in error_lower:
            return ErrorCategory.NOT_FOUND
        if "permission" in error_lower:
            return ErrorCategory.PERMISSION_DENIED
        if "validation" in error_lower or "invalid" in error_lower:
            return ErrorCategory.VALIDATION

        return ErrorCategory.UNKNOWN

    def _is_retryable_from_message(self, error_msg: str | None) -> bool:
        """从错误消息判断是否可重试"""
        if not error_msg:
            return False

        error_lower = str(error_msg).lower()
        retryable_keywords = [
            "timeout",
            "rate limit",
            "temporary",
            "transient",
            "network",
            "connection",
            "unavailable",
            "retry",
            "503",
            "502",
            "504",
        ]

        return any(kw in error_lower for kw in retryable_keywords)

    async def _execute_with_backend(
        self,
        tool_name: str,
        args: dict[str, Any],
        timeout: int,
    ) -> Any:
        """使用后端执行工具调用（带超时）

        Args:
            tool_name: 工具名称
            args: 工具参数
            timeout: 超时时间（秒）

        Returns:
            原始执行结果

        Raises:
            ToolTimeoutError: 执行超时
            ToolError: 执行错误
        """
        start_time = asyncio.get_event_loop().time()

        try:
            # 使用注入的后端或默认后端
            if self._backend is not None:
                # 使用注入的后端
                coro = self._backend.execute(
                    tool_name,
                    args,
                    workspace=self._workspace,
                )
            else:
                # 使用默认后端（AgentAccelToolExecutor）
                coro = self._execute_with_default_backend(tool_name, args)

            # 带超时执行
            result = await asyncio.wait_for(coro, timeout=timeout)

            return result

        except asyncio.TimeoutError as e:
            elapsed = asyncio.get_event_loop().time() - start_time
            raise ToolTimeoutError(
                f"Tool '{tool_name}' execution timed out after {timeout}s",
                timeout_seconds=timeout,
                context={"elapsed_seconds": elapsed},
            ) from e

    async def _execute_with_default_backend(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """使用默认后端（AgentAccelToolExecutor）执行工具

        Args:
            tool_name: 工具名称
            args: 工具参数

        Returns:
            执行结果字典
        """
        from polaris.kernelone.llm.toolkit import AgentAccelToolExecutor

        executor = AgentAccelToolExecutor(
            workspace=self._workspace,
        )
        result: dict[str, Any] | None = None
        try:
            raw_result = executor.execute(tool_name, dict(args))
            # 确保返回字典
            result = {"success": True, "result": raw_result} if not isinstance(raw_result, dict) else raw_result
        except (RuntimeError, ValueError) as exc:
            # 确保异常时也能正确清理资源
            result = {"success": False, "error": str(exc)}
            raise
        finally:
            # 清理资源：优先尝试异步close，然后同步close_sync
            try:
                close_async = getattr(executor, "close", None)
                if callable(close_async):
                    if asyncio.iscoroutinefunction(close_async):
                        await close_async()
                    else:
                        close_async()
                else:
                    close_sync = getattr(executor, "close_sync", None)
                    if callable(close_sync):
                        close_sync()
            except (RuntimeError, ValueError) as cleanup_exc:
                logger.debug("Tool executor cleanup failed: %s", cleanup_exc)

        return result if result is not None else {"success": False, "error": "Unknown error"}

    async def execute_single(
        self,
        call: ToolCall,
        profile: RoleProfile,
    ) -> ToolResult:
        """执行单个工具调用（公共API，带并发控制）

        核心方法，执行单个工具调用并返回规范化结果。
        包含完整的错误处理和超时控制。

        Args:
            call: 工具调用请求
            profile: 角色配置（用于确定超时和权限）

        Returns:
            ToolResult: 执行结果

        Example:
            >>> call = ToolCall(tool="read_file", args={"path": "test.py"})
            >>> result = await executor.execute_single(call, profile)
            >>> if result.success:
            ...     print(result.result)
        """
        # 公共API使用信号量控制并发
        async with self._concurrency_limit:
            return await self._execute_single_impl(call, profile)

    async def _execute_single_impl(
        self,
        call: ToolCall,
        profile: RoleProfile,
    ) -> ToolResult:
        """执行单个工具调用的内部实现（无信号量控制）

        用于批量执行时在外层统一控制并发，避免嵌套信号量问题。

        Args:
            call: 工具调用请求
            profile: 角色配置（用于确定超时和权限）

        Returns:
            ToolResult: 执行结果
        """
        tool_name = str(call.tool or "").strip()
        call_id = str(call.call_id or "").strip()

        if not tool_name:
            return ToolResult(
                success=False,
                tool="unknown",
                error="Tool name is empty",
                error_category=ErrorCategory.VALIDATION,
                retryable=False,
                call_id=call_id,
            )

        start_time = asyncio.get_event_loop().time()

        try:
            # 解析超时时间
            timeout = self._resolve_timeout(profile)

            # 执行工具（无信号量，由调用方控制）
            raw_result = await self._execute_with_backend(
                tool_name,
                call.args,
                timeout,
            )

            execution_time_ms = (asyncio.get_event_loop().time() - start_time) * 1000

            # 规范化结果
            return self._normalize_result(
                raw_result,
                tool_name,
                call_id,
                execution_time_ms,
            )

        except ToolTimeoutError as e:
            execution_time_ms = (asyncio.get_event_loop().time() - start_time) * 1000
            logger.warning("Tool execution timeout: %s after %ss", tool_name, e.timeout_seconds)
            return ToolResult(
                success=False,
                tool=tool_name,
                error=str(e),
                error_category=ErrorCategory.TIMEOUT,
                retryable=True,
                execution_time_ms=execution_time_ms,
                call_id=call_id,
            )

        except ToolAuthorizationError as e:
            execution_time_ms = (asyncio.get_event_loop().time() - start_time) * 1000
            logger.warning("Tool authorization failed: %s - %s", tool_name, e)
            return ToolResult(
                success=False,
                tool=tool_name,
                error=str(e),
                error_category=ErrorCategory.AUTHORIZATION,
                retryable=False,
                execution_time_ms=execution_time_ms,
                call_id=call_id,
            )

        except Exception as e:
            execution_time_ms = (asyncio.get_event_loop().time() - start_time) * 1000
            error_category, retryable = self._classify_error(e, tool_name)

            logger.exception("Tool execution failed: %s", tool_name)

            return ToolResult(
                success=False,
                tool=tool_name,
                error=str(e),
                error_category=error_category,
                retryable=retryable,
                execution_time_ms=execution_time_ms,
                call_id=call_id,
            )

    async def execute_batch(
        self,
        calls: list[ToolCall],
        profile: RoleProfile,
    ) -> list[ToolResult]:
        """批量执行工具调用

        并发执行多个工具调用，受 MAX_CONCURRENT_TOOL_EXECUTIONS 限制。
        每个工具调用独立超时，互不影响。

        Args:
            calls: 工具调用请求列表
            profile: 角色配置

        Returns:
            ToolResult 列表，顺序与输入一致

        Example:
            >>> calls = [
            ...     ToolCall(tool="read_file", args={"path": "a.py"}),
            ...     ToolCall(tool="read_file", args={"path": "b.py"}),
            ... ]
            >>> results = await executor.execute_batch(calls, profile)
            >>> for r in results:
            ...     print(f"{r.tool}: {'OK' if r.success else 'FAIL'}")
        """
        if not calls:
            return []

        # 使用信号量限制并发数，在任务创建级别控制
        # 而不是依赖 execute_single 内部的信号量（那会失去批量控制意义）
        async def _execute_with_semaphore(call: ToolCall) -> ToolResult:
            async with self._concurrency_limit:
                return await self._execute_single_impl(call, profile)

        # 创建受限制的任务列表
        tasks = [_execute_with_semaphore(call) for call in calls]

        # 并发执行，等待所有完成
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理可能的异常（理论上不应发生，因为 execute_single 捕获所有异常）
        processed_results: list[ToolResult] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                # 异常情况（防御性编程）
                call = calls[i]
                processed_results.append(
                    ToolResult(
                        success=False,
                        tool=call.tool,
                        error=f"Unexpected error: {result}",
                        error_category=ErrorCategory.UNKNOWN,
                        retryable=False,
                        call_id=call.call_id,
                    )
                )
            else:
                # result is ToolResult (not Exception)
                processed_results.append(result)  # type: ignore[arg-type]

        return processed_results

    async def execute_with_fallback(
        self,
        call: ToolCall,
        profile: RoleProfile,
        *,
        max_retries: int = 2,
        retry_delay_base: float = 1.0,
    ) -> ToolResult:
        """执行工具调用（带重试机制）

        对于可重试错误，自动进行重试。

        Args:
            call: 工具调用请求
            profile: 角色配置
            max_retries: 最大重试次数
            retry_delay_base: 基础重试延迟（秒，指数退避）

        Returns:
            ToolResult: 最终执行结果
        """
        last_result: ToolResult | None = None

        for attempt in range(max_retries + 1):
            result = await self.execute_single(call, profile)
            last_result = result

            # 成功或不可重试，直接返回
            if result.success or not result.retryable:
                return result

            # 最后一次尝试，返回结果
            if attempt >= max_retries:
                break

            # 计算退避延迟
            delay = retry_delay_base * (2**attempt)
            logger.info(
                "Retrying tool '%s' after %.1fs (attempt %d/%d)",
                call.tool,
                delay,
                attempt + 1,
                max_retries,
            )
            await asyncio.sleep(delay)

        return (
            last_result
            if last_result
            else ToolResult(
                success=False,
                tool=call.tool,
                error="Max retries exceeded",
                error_category=ErrorCategory.UNKNOWN,
                retryable=False,
                call_id=call.call_id,
            )
        )


# ═══════════════════════════════════════════════════════════════════════════
# 工厂函数
# ═══════════════════════════════════════════════════════════════════════════


def create_tool_executor(
    workspace: str = ".",
    *,
    backend: ToolExecutionBackend | None = None,
) -> ToolExecutor:
    """创建 ToolExecutor 实例的工厂函数

    Args:
        workspace: 工作区路径
        backend: 可选的执行后端

    Returns:
        ToolExecutor 实例
    """
    return ToolExecutor(
        workspace=workspace,
        backend=backend,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 向后兼容
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    "MAX_CONCURRENT_TOOL_EXECUTIONS",
    # 常量
    "TIMEOUT_DEFAULT_SECONDS",
    "TIMEOUT_DIRECTOR_SECONDS",
    # 枚举
    "ErrorCategory",
    "ToolAuthorizationError",
    "ToolCall",
    # 异常
    "ToolError",
    # 协议
    "ToolExecutionBackend",
    # 核心类
    "ToolExecutor",
    "ToolResult",
    "ToolTimeoutError",
    # 工厂
    "create_tool_executor",
]
