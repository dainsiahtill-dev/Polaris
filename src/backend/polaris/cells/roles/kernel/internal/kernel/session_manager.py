"""Session Manager - 会话状态管理

负责：
- run_id 解析
- 流日志写入器创建
- 废弃参数处理
- 上下文构建
- 系统提示词构建
- 流式上下文请求构建
"""

from __future__ import annotations

import json
import logging
import os
import warnings
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.context_gateway import ContextRequest
from polaris.infrastructure.log_pipeline.writer import LogEventWriter, get_writer
from polaris.kernelone.storage import resolve_storage_roots

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest

logger = logging.getLogger(__name__)


class SessionManager:
    """会话状态管理器

    管理每个回合的会话相关状态，包括 run_id 解析、上下文构建、
    系统提示词构建等。
    """

    __slots__ = ("_kernel",)

    def __init__(self, kernel: Any) -> None:
        """初始化会话管理器

        Args:
            kernel: RoleExecutionKernel 实例
        """
        self._kernel = kernel

    def resolve_stream_run_id(self, request_run_id: str | None) -> str:
        """Resolve stream run_id from request or workspace runtime metadata."""
        requested = str(request_run_id or "").strip()
        if requested:
            return requested

        workspace = str(self._kernel.workspace or "").strip() or os.getcwd()
        try:
            roots = resolve_storage_roots(workspace)
            latest_run_file = os.path.join(roots.runtime_root, "latest_run.json")
            if os.path.isfile(latest_run_file):
                with open(latest_run_file, encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, dict) and payload.get("run_id"):
                    return str(payload.get("run_id", "").strip())
        except (RuntimeError, ValueError):
            logger.warning("Failed to resolve stream run_id from latest_run.json", exc_info=True)
        import uuid

        return f"auto_{uuid.uuid4().hex[:12]}"

    def build_stream_log_writer(self, run_id: str) -> LogEventWriter | None:
        """Create a log writer for streaming events."""
        if not run_id:
            return None
        workspace = str(self._kernel.workspace or "").strip() or os.getcwd()
        try:
            return get_writer(workspace=workspace, run_id=run_id)
        except (RuntimeError, ValueError):
            logger.warning("Failed to create stream log writer for run_id=%s", run_id, exc_info=True)
            return None

    @staticmethod
    def process_deprecated_params(request: RoleTurnRequest) -> str:
        """处理废弃参数"""
        appendix_parts: list[str] = []
        seen: set[str] = set()

        if request.prompt_appendix:
            token = str(request.prompt_appendix).strip()
            if token and token not in seen:
                seen.add(token)
                appendix_parts.append(token)

        if request.system_prompt:
            token = str(request.system_prompt).strip()
            if token:
                warnings.warn(
                    "RoleTurnRequest.system_prompt is deprecated; use prompt_appendix instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
                if token not in seen:
                    seen.add(token)
                    appendix_parts.append(token)

        extra_context = getattr(request, "extra_context", None)
        if extra_context:
            token = f"【额外上下文】\n{extra_context}"
            if token not in seen:
                seen.add(token)
                appendix_parts.append(token)

        return "\n\n".join(appendix_parts)

    @staticmethod
    def build_context(profile: RoleProfile, request: RoleTurnRequest) -> ContextRequest:
        """构建上下文请求"""
        context_os_snapshot = None
        if isinstance(request.context_override, dict):
            context_os_snapshot = request.context_override.get("context_os_snapshot")
        return ContextRequest(
            message=request.message,
            history=tuple(request.history) if request.history else (),
            task_id=request.task_id,
            context_os_snapshot=context_os_snapshot,
        )

    def build_system_prompt_for_request(
        self,
        profile: RoleProfile,
        request: RoleTurnRequest,
        prompt_appendix: str,
    ) -> str:
        """Build system prompt with domain-aware fallback compatibility."""
        domain = str(getattr(request, "domain", "") or "").strip().lower() or "code"
        try:
            return self._kernel._get_prompt_builder().build_system_prompt(
                profile,
                prompt_appendix,
                domain=domain,
                message=str(getattr(request, "message", "") or ""),
            )
        except TypeError:
            return self._kernel._get_prompt_builder().build_system_prompt(profile, prompt_appendix)

    @staticmethod
    def build_context_request_for_stream(messages: list[dict[str, Any]], request: RoleTurnRequest) -> Any:
        """Build a minimal ContextRequest for legacy call_stream compatibility."""

        def _normalize_user_text(value: Any) -> str:
            return str(value or "").replace("\ufeff", "").strip()

        history: list[tuple[str, str]] = []
        for msg in messages:
            role_label = str(msg.get("role", ""))
            content = str(msg.get("content", ""))
            if role_label in ("user", "assistant", "tool"):
                history.append((role_label, content))

        normalized_current = _normalize_user_text(request.message)
        if normalized_current:
            history = [
                (role_label, content)
                for role_label, content in history
                if not (role_label == "user" and _normalize_user_text(content) == normalized_current)
            ]

        return ContextRequest(
            message=request.message,
            history=tuple(history),
            task_id=request.task_id,
        )


__all__ = ["SessionManager"]
