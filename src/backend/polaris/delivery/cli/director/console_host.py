from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleSessionCommandV1,
    IRoleRuntime,
)
from polaris.cells.roles.runtime.public.service import RoleRuntimeService
from polaris.cells.roles.session.public import (
    AttachmentMode,
    RoleHostKind,
    RoleSessionService,
    SessionState,
    SessionType,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.context.context_os import summarize_context_os_payload
from polaris.kernelone.context.history_materialization import SessionContinuityStrategy
from polaris.kernelone.context.session_continuity import SessionContinuityProjection
from polaris.kernelone.telemetry.debug_stream import (
    debug_stream_session,
    emit_debug_event,
)

logger = logging.getLogger(__name__)


class RoleConsoleHostError(RuntimeError):
    """Structured error for the role console host layer."""


class RoleSessionNotFoundError(RoleConsoleHostError):
    """Raised when a requested role session does not exist."""


@dataclass(frozen=True, slots=True)
class RoleConsoleHostConfig:
    """Configuration for the role console host."""

    workspace: str
    role: str = "director"
    host_kind: str = RoleHostKind.CLI.value
    session_type: str = SessionType.STANDALONE.value
    attachment_mode: str = AttachmentMode.ISOLATED.value
    default_session_title: str = "Role CLI"


def _copy_mapping(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    return token in {"1", "true", "yes", "on", "debug"}


def _normalize_user_message_token(value: Any) -> str:
    """Normalize user message text for duplicate detection across transports."""
    token = str(value or "")
    token = token.replace("\r\n", "\n").replace("\r", "\n")
    token = token.replace("\ufeff", "").strip()
    return token


class RequestClarity:
    """Request clarity levels for Director role."""

    EXECUTABLE = "executable"
    SEMI_CLEAR = "semi_clear"
    VAGUE = "vague"


def _is_continuation_intent(request: str) -> bool:
    """Detect if the user message is a continuation intent (e.g. '继续', 'go on').

    These short messages are NOT vague when there is prior session context;
    they mean 'continue the previous task'.
    """
    token = str(request or "").strip().lower()
    if not token:
        return False
    continuation_markers = {
        "继续",
        "continue",
        "go on",
        "proceed",
        "next",
        "下一步",
        "接着",
        "往下",
        "往下走",
        "继续执行",
        "继续干",
        "继续弄",
        "继续搞",
        "继续写",
        "继续改",
        "继续修",
        "ok",
        "好的",
        "行",
        "可以",
        "没问题",
        "sure",
        "yes",
        "y",
    }
    return any(marker in token for marker in continuation_markers)


def _assess_director_request_clarity(request: str) -> str:
    """Assess if a request is clear enough for Director to execute.

    Director should only receive requests that specify:
    1. Target file(s) or specific location
    2. Specific modification/action to perform

    Returns:
        RequestClarity.EXECUTABLE: Clear enough to execute
        RequestClarity.SEMI_CLEAR: Might need clarification
        RequestClarity.VAGUE: Too vague, should be rejected
    """
    token = str(request or "").strip()
    if not token:
        return RequestClarity.VAGUE

    # FIX-20250422-v6: Continuation intents (e.g. '继续') are not vague when
    # there is prior session context. The caller checks session history before
    # blocking, so we treat them as executable here to pass the clarity gate.
    if _is_continuation_intent(token):
        return RequestClarity.EXECUTABLE

    # Check for target file patterns (common code file extensions)
    has_target_file = bool(re.search(r"[\w/\\.-]+\.(py|ts|js|jsx|tsx|java|go|rs|cpp|c|h|yaml|yml|json|md)", token))

    # Check for specific action keywords
    action_keywords = [
        "添加",
        "修复",
        "删除",
        "修改",
        "替换",
        "插入",
        "更新",
        "add",
        "fix",
        "delete",
        "remove",
        "modify",
        "replace",
        "insert",
        "update",
        "implement",
        "create",
        "refactor",
        "优化",
        "重构",
        "实现",
    ]
    has_specific_action = any(kw in token.lower() for kw in action_keywords)

    # Check for line numbers or specific locations
    has_location = bool(re.search(r"(第\d+行|line\s+\d+|:\d+|函数\w+|类\w+|方法\w+)", token))

    # Vague keywords that suggest exploration rather than execution
    vague_keywords = [
        "完善",
        "改进",
        "优化",
        "看看",
        "了解一下",
        "分析一下",
        "improve",
        "enhance",
        "optimize",
        "explore",
        "investigate",
        "看看",
        "检查一下",
        "了解一下",
        "分析一下",
    ]
    has_vague_keyword = any(kw in token.lower() for kw in vague_keywords)

    # If the request is very short, it's likely vague
    is_too_short = len(token) < 15

    # Scoring
    score = 0
    if has_target_file:
        score += 40
    if has_specific_action:
        score += 40
    if has_location:
        score += 20
    if has_vague_keyword:
        score -= 30
    if is_too_short:
        score -= 20

    if score >= 60:
        return RequestClarity.EXECUTABLE
    if score >= 30:
        return RequestClarity.SEMI_CLEAR
    return RequestClarity.VAGUE


def _message_history(session_payload: Mapping[str, Any], *, limit: int | None = None) -> list[dict[str, Any]]:
    messages = session_payload.get("messages")
    if not isinstance(messages, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in messages:
        if isinstance(item, dict):
            normalized.append(dict(item))
    if limit is None or limit < 0:
        return normalized
    if limit == 0:
        return []
    return normalized[-int(limit) :]


def _session_summary(session_payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(session_payload.get("id") or "").strip(),
        "role": str(session_payload.get("role") or "").strip(),
        "workspace": session_payload.get("workspace"),
        "title": session_payload.get("title"),
        "host_kind": str(session_payload.get("host_kind") or "").strip(),
        "session_type": str(session_payload.get("session_type") or "").strip(),
        "attachment_mode": str(session_payload.get("attachment_mode") or "").strip(),
        "state": str(session_payload.get("state") or "").strip(),
        "message_count": int(session_payload.get("message_count") or 0),
        "updated_at": session_payload.get("updated_at"),
        "context_config": _copy_mapping(
            session_payload.get("context_config")
            if isinstance(session_payload.get("context_config"), Mapping)
            else None
        ),
        "capability_profile": _copy_mapping(
            session_payload.get("capability_profile")
            if isinstance(session_payload.get("capability_profile"), Mapping)
            else None
        ),
    }


def _extract_tool_args(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    args = payload.get("args")
    return dict(args) if isinstance(args, Mapping) else {}


def _extract_tool_name(payload: Mapping[str, Any] | None) -> str:
    if not isinstance(payload, Mapping):
        return ""
    for source in (payload, payload.get("result"), payload.get("raw_result")):
        if isinstance(source, Mapping):
            tool_name = str(source.get("tool") or "").strip()
            if tool_name:
                return tool_name
    return ""


def _extract_path_token(payload: Mapping[str, Any] | None) -> str:
    if not isinstance(payload, Mapping):
        return ""
    for source in (payload, payload.get("result"), payload.get("raw_result"), payload.get("args")):
        if not isinstance(source, Mapping):
            continue
        for key in ("file_path", "file", "path", "filepath", "target_file"):
            token = str(source.get(key) or "").strip()
            if token:
                return token.replace("\\", "/")
    return ""


def _extract_tool_success(payload: Mapping[str, Any] | None) -> bool | None:
    if not isinstance(payload, Mapping):
        return None
    for source in (payload, payload.get("raw_result"), payload.get("result")):
        if not isinstance(source, Mapping):
            continue
        success = source.get("success")
        if isinstance(success, bool):
            return success
        ok = source.get("ok")
        if isinstance(ok, bool):
            return ok
    error_text = _extract_error_text(payload)
    if error_text:
        return False
    return None


def _extract_error_text(payload: Mapping[str, Any] | None) -> str:
    if not isinstance(payload, Mapping):
        return ""
    for source in (payload, payload.get("raw_result"), payload.get("result")):
        if not isinstance(source, Mapping):
            continue
        for key in ("error", "message"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def _ensure_minimal_runtime_bindings() -> None:
    """Install the minimal KernelOne bindings required by standalone CLI hosts."""
    try:
        from polaris.bootstrap.assembly import ensure_minimal_kernelone_bindings
        from polaris.infrastructure.llm.provider_bootstrap import inject_kernelone_provider_runtime

        ensure_minimal_kernelone_bindings()
        inject_kernelone_provider_runtime()
    except (RuntimeError, ValueError) as exc:  # pragma: no cover - defensive bootstrap
        logger.debug("Director console minimal runtime bootstrap unavailable: %s", exc)


class RoleConsoleHost:
    """Service/controller layer for a canonical role CLI client."""

    # Canonical set of roles supported by the role console host.
    _ALLOWED_ROLES: frozenset[str] = frozenset({"director", "pm", "architect", "chief_engineer", "qa"})

    def __init__(
        self,
        workspace: str,
        *,
        role: str = "director",
        session_service_factory: Callable[[], RoleSessionService] | None = None,
        task_service_factory: Callable[[str], TaskRuntimeService] | None = None,
        runtime_service_factory: Callable[[], IRoleRuntime] | None = None,
        config: RoleConsoleHostConfig | None = None,
    ) -> None:
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise ValueError("workspace is required for RoleConsoleHost")

        self.config = config or RoleConsoleHostConfig(workspace=workspace_token, role=role)
        self.workspace = self.config.workspace
        self.role = self.config.role
        self._session_service_factory = session_service_factory or RoleSessionService
        self._task_service_factory = task_service_factory or TaskRuntimeService
        runtime_factory = runtime_service_factory or RoleRuntimeService
        self._runtime_service = runtime_factory()
        self._task_service: TaskRuntimeService | None = None
        self._task_service_error: str | None = None
        self._continuity_strategy: SessionContinuityStrategy = SessionContinuityStrategy()
        self._cognitive_middleware_cache: dict[str, Any] = {}
        # Bootstrap kernel bindings once at init; runtime service is the only
        # allowed streaming entrypoint for role chat.
        _ensure_minimal_runtime_bindings()

    def add_role_host_kind(self, kind: str) -> None:
        """Register an additional role host kind at runtime."""
        normalized = str(kind or "").strip().lower()
        if not normalized:
            raise ValueError("add_role_host_kind requires a non-empty string")
        # Dynamically extend the class-level frozenset by replacing the class attribute.
        # This is safe because frozenset is immutable; we create a new one.
        new_allowed = frozenset(self._ALLOWED_ROLES) | {normalized}
        type(self)._ALLOWED_ROLES = new_allowed

    def _get_task_service(self, *, required: bool = True) -> TaskRuntimeService | None:
        if self._task_service is None and self._task_service_error is None:
            try:
                _ensure_minimal_runtime_bindings()
                self._task_service = self._task_service_factory(self.workspace)
            except (RuntimeError, ValueError) as exc:
                self._task_service_error = f"{type(exc).__name__}: {exc}"
                logger.warning("Director task runtime unavailable for %s: %s", self.workspace, self._task_service_error)
                if required:
                    raise RoleConsoleHostError("Task runtime is not available for the role console") from exc
                return None
        if self._task_service is None:
            if required:
                raise RoleConsoleHostError(
                    self._task_service_error or "Task runtime is not available for the role console"
                )
            return None
        return self._task_service

    def _get_cognitive_middleware(self, enable_cognitive: bool | None = None) -> Any | None:
        """Get or create the CognitiveMiddleware instance.

        Args:
            enable_cognitive: Override for cognitive middleware enablement.
                None: use default (enabled)
                True: explicitly enable
                False: explicitly disable

        Returns:
            CognitiveMiddleware instance if available and enabled, None otherwise.
        """
        if enable_cognitive is False:
            return None
        key = self.workspace or ""
        if key not in self._cognitive_middleware_cache:
            try:
                from polaris.kernelone.cognitive.middleware import get_cognitive_middleware

                self._cognitive_middleware_cache[key] = get_cognitive_middleware(
                    workspace=self.workspace,
                    enabled=enable_cognitive,
                )
            except (RuntimeError, ValueError) as exc:
                logger.debug("Cognitive middleware unavailable: %s", exc)
                self._cognitive_middleware_cache[key] = False
        cached = self._cognitive_middleware_cache.get(key)
        if cached is False:
            return None
        return cached

    @contextlib.contextmanager
    def _session_service(self) -> Any:
        service = self._session_service_factory()
        if hasattr(service, "__enter__") and hasattr(service, "__exit__"):
            with service as entered:
                yield entered
            return
        try:
            yield service
        finally:
            close = getattr(service, "close", None)
            if callable(close):
                close()

    def _session_query_kwargs(self) -> dict[str, str]:
        return self._session_query_kwargs_for_role(self.role)

    def _session_query_kwargs_for_role(self, role: str | None) -> dict[str, str]:
        role_token = str(role or "").strip() or self.role
        return {
            "role": role_token,
            "host_kind": self.config.host_kind,
            "workspace": self.workspace,
            "session_type": self.config.session_type,
        }

    @staticmethod
    def _use_orchestrator(capability_profile: Mapping[str, Any] | None = None) -> bool:
        """Check whether session orchestrator should be used.

        Priority:
        1. POLARIS_ENABLE_SESSION_ORCHESTRATOR env var
        2. capability_profile["enable_session_orchestrator"]
        """
        env_flag = os.environ.get("POLARIS_ENABLE_SESSION_ORCHESTRATOR", "").strip().lower()
        if env_flag in {"1", "true", "yes"}:
            return True
        if env_flag in {"0", "false", "no"}:
            return False
        return _coerce_bool(capability_profile.get("enable_session_orchestrator") if capability_profile else None)

    @staticmethod
    def _normalize_orchestrator_event(event: Any) -> dict[str, Any] | None:
        """Normalize a TurnEvent from the orchestrator into console_host dict format."""
        # Handle dataclass events from the new event system
        event_type_name = type(event).__name__ if not isinstance(event, str) else ""
        if event_type_name == "ContentChunkEvent":
            return {"type": "content_chunk", "data": {"content": str(getattr(event, "chunk", "") or "")}}
        if event_type_name == "ToolBatchEvent":
            status = str(getattr(event, "status", "") or "")
            tool_name = str(getattr(event, "tool_name", "") or "")
            arguments = getattr(event, "arguments", None) or {}
            result = getattr(event, "result", None)
            error = getattr(event, "error", None)
            if status == "started":
                return {
                    "type": "tool_call",
                    "data": {"tool": tool_name, "args": dict(arguments) if isinstance(arguments, Mapping) else {}},
                }
            payload: dict[str, Any] = {"tool": tool_name}
            if result is not None:
                payload["result"] = result
            if error:
                payload["error"] = error
                payload["success"] = False
            elif status in {"success", "error", "timeout"}:
                payload["success"] = status == "success"
            return {"type": "tool_result", "data": payload}
        if event_type_name == "CompletionEvent":
            visible = str(getattr(event, "visible_content", "") or "")
            thinking = str(getattr(event, "thinking", "") or "") or None
            # ADR-0080: pass through turn_kind so outer CLI knows to auto-continue
            turn_kind = str(getattr(event, "turn_kind", "") or "")
            # 【关键修复】：传递 batch_receipt 以便 orchestrator 在 continuation turns
            # 检测到写工具成功执行时自动将 task_progress 推进到 verifying。
            # 修复了 "LLM 写完后继续 implement 而不验证" 的根因。
            batch_receipt = dict(getattr(event, "batch_receipt", None) or {})
            result_data: dict[str, Any] = {
                "content": visible,
                "thinking": thinking,
                "turn_kind": turn_kind,
            }
            if batch_receipt:
                result_data["batch_receipt"] = batch_receipt
            return {"type": "complete", "data": result_data}
        if event_type_name == "ErrorEvent":
            return {"type": "error", "error": str(getattr(event, "message", "") or "")}
        if event_type_name == "SessionStartedEvent":
            return {"type": "session_started", "data": {"session_id": str(getattr(event, "session_id", "") or "")}}
        if event_type_name == "SessionCompletedEvent":
            return {"type": "session_completed", "data": {"session_id": str(getattr(event, "session_id", "") or "")}}
        if event_type_name == "SessionWaitingHumanEvent":
            return {
                "type": "session_waiting_human",
                "data": {
                    "session_id": str(getattr(event, "session_id", "") or ""),
                    "reason": str(getattr(event, "reason", "") or ""),
                },
            }
        if event_type_name == "RuntimeStartedEvent":
            return {"type": "runtime_started", "data": {"name": str(getattr(event, "name", "") or "")}}
        if event_type_name == "RuntimeCompletedEvent":
            return {"type": "runtime_completed", "data": {}}
        if event_type_name == "TurnPhaseEvent":
            return {
                "type": "turn_phase",
                "data": {
                    "phase": str(getattr(event, "phase", "") or ""),
                    "metadata": dict(getattr(event, "metadata", {}) or {}),
                },
            }
        # Fallback: if event is already a dict, use the existing normalizer
        if isinstance(event, Mapping):
            return RoleConsoleHost._normalize_stream_event(event)
        return None

    def _get_session_event_log_path(self, session_id: str) -> Path:
        """Return the session-isolated event log path."""
        path = Path(self.workspace) / ".polaris" / "runtime" / "events" / f"{session_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _write_session_event(self, session_id: str, event: dict[str, Any]) -> None:
        """Append a normalized event to the session-isolated event log."""
        try:
            log_path = self._get_session_event_log_path(session_id)
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except OSError:
            logger.debug("Failed to write session event log for session=%s", session_id)

    def _resolve_workspace_file_path(self, candidate: str) -> Path:
        token = str(candidate or "").strip()
        path = Path(token)
        if not path.is_absolute():
            path = Path(self.workspace) / token
        return path

    @staticmethod
    def _read_text_file(path: Path) -> str | None:
        try:
            if not path.exists() or not path.is_file():
                return None
            with open(path, encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError:
            return None

    def _snapshot_tool_call(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        tool_name = _extract_tool_name(payload) or str(payload.get("tool") or "").strip()
        path_token = _extract_path_token(payload)
        if not tool_name or not path_token:
            return None
        workspace_path = self._resolve_workspace_file_path(path_token)
        before_text = self._read_text_file(workspace_path)
        return {
            "tool": tool_name,
            "display_path": path_token,
            "workspace_path": workspace_path,
            "before_text": before_text,
            "args": _extract_tool_args(payload),
        }

    def _enrich_tool_call_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        enriched = dict(payload)
        path_token = _extract_path_token(payload)
        if path_token:
            enriched.setdefault("file_path", path_token)
        args = _extract_tool_args(payload)
        if args:
            enriched["args"] = args
        return enriched

    def _enrich_tool_result_payload(
        self,
        payload: Mapping[str, Any],
        snapshot: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        enriched = dict(payload)
        if snapshot is None:
            path_token = _extract_path_token(enriched)
            if path_token:
                enriched.setdefault("file_path", path_token)
            return enriched

        display_path = str(snapshot.get("display_path") or "").strip()
        if display_path:
            enriched.setdefault("file_path", display_path)
        args = snapshot.get("args")
        if isinstance(args, Mapping) and args:
            enriched.setdefault("args", dict(args))

        success = _extract_tool_success(enriched)
        if success is False:
            return enriched

        workspace_path = snapshot.get("workspace_path")
        if not isinstance(workspace_path, Path):
            return enriched

        before_text = snapshot.get("before_text")
        if before_text is not None and not isinstance(before_text, str):
            before_text = str(before_text)
        after_text = self._read_text_file(workspace_path)

        operation = "modify"
        if before_text is None and after_text is not None:
            operation = "create"
        elif before_text is not None and after_text is None:
            operation = "delete"

        patch = ""
        if before_text is not None or after_text is not None:
            try:
                from polaris.kernelone.events.file_event_broadcaster import calculate_patch

                patch = calculate_patch(str(before_text or ""), str(after_text or ""))
            except (RuntimeError, ValueError) as exc:  # pragma: no cover - defensive projection
                logger.debug("Director console patch projection unavailable: %s", exc)

        if patch:
            max_patch_chars = 12000
            if len(patch) > max_patch_chars:
                enriched["patch"] = patch[:max_patch_chars]
                enriched["patch_truncated"] = True
            else:
                enriched["patch"] = patch
        enriched["operation"] = operation
        return enriched

    def _load_session_payload(self, session_id: str, *, message_limit: int | None = None) -> dict[str, Any] | None:
        token = str(session_id or "").strip()
        if not token:
            return None
        with self._session_service() as service:
            session = service.get_session(token)
            if session is None:
                return None
            payload = session.to_dict(
                include_messages=True,
                message_limit=message_limit if message_limit is not None else 1000,
            )
        if not isinstance(payload, dict):
            return None
        return payload

    def _persist_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        thinking: str | None = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        with self._session_service() as service:
            result = service.add_message(
                session_id=session_id,
                role=role,
                content=content,
                thinking=thinking,
                meta=_copy_mapping(meta),
            )
        if result is None:
            raise RoleSessionNotFoundError(f"Session not found: {session_id}")

    def _persist_context_config(
        self,
        *,
        session_id: str,
        context_config: Mapping[str, Any],
    ) -> None:
        with self._session_service() as service:
            result = service.update_session(
                session_id,
                context_config=_copy_mapping(context_config),
            )
        if result is None:
            raise RoleSessionNotFoundError(f"Session not found: {session_id}")

    async def _project_session_continuity(
        self,
        *,
        session_id: str,
        role: str,
        session_payload: Mapping[str, Any],
        session_context_config: Mapping[str, Any] | None,
        incoming_context: Mapping[str, Any] | None,
        history_limit: int | None,
    ) -> SessionContinuityProjection:
        request = {
            "session_id": session_id,
            "role": role,
            "workspace": self.workspace,
            "session_title": str(session_payload.get("title") or "").strip(),
            "messages": _message_history(session_payload),
            "session_context_config": session_context_config,
            "incoming_context": incoming_context,
            "history_limit": history_limit,
        }
        projection = await self._continuity_strategy.project_to_projection(request)
        if projection is None:
            # Fallback: return empty projection to avoid breaking the call chain
            return SessionContinuityProjection(recent_messages=(), prompt_context={}, persisted_context_config={})
        return projection

    @staticmethod
    def _build_runtime_history(messages: list[dict[str, Any]]) -> tuple[tuple[str, str], ...]:
        history: list[tuple[str, str]] = []
        for item in messages:
            if not isinstance(item, Mapping):
                continue
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or item.get("message") or "").strip()
            if role and content:
                history.append((role, content))
        return tuple(history)

    @staticmethod
    def _trim_current_user_from_recent_messages(
        messages: list[dict[str, Any]],
        *,
        current_user_message: str,
        drop_current_user_tail: bool = True,
    ) -> list[dict[str, Any]]:
        """Drop the trailing current user turn from history passed to runtime.

        stream_turn sends current user input via command.user_message, so the same
        message must not be duplicated in command.history.
        """
        if not messages:
            return []
        if not drop_current_user_tail:
            return list(messages)
        token = _normalize_user_message_token(current_user_message)
        if not token:
            return list(messages)
        trimmed = list(messages)
        tail = trimmed[-1] if trimmed else None
        if not isinstance(tail, Mapping):
            return trimmed
        tail_role = str(tail.get("role") or "").strip().lower()
        tail_content = _normalize_user_message_token(tail.get("content") or tail.get("message") or "")
        if tail_role == "user" and tail_content == token:
            trimmed.pop()
        return trimmed

    @staticmethod
    def _append_current_user_message(
        messages: list[dict[str, Any]],
        *,
        current_user_message: str,
    ) -> tuple[list[dict[str, Any]], bool]:
        token = _normalize_user_message_token(current_user_message)
        if not token:
            return list(messages), False
        updated = list(messages)
        while len(updated) >= 2:
            tail = updated[-1]
            prev = updated[-2]
            if not isinstance(tail, Mapping) or not isinstance(prev, Mapping):
                break
            tail_role = str(tail.get("role") or "").strip().lower()
            prev_role = str(prev.get("role") or "").strip().lower()
            tail_content = _normalize_user_message_token(tail.get("content") or tail.get("message") or "")
            prev_content = _normalize_user_message_token(prev.get("content") or prev.get("message") or "")
            if tail_role == "user" and prev_role == "user" and tail_content == prev_content:
                updated.pop()
                continue
            break
        tail_item = updated[-1] if updated else None
        if isinstance(tail_item, Mapping):
            tail_role = str(tail_item.get("role") or "").strip().lower()
            tail_content = _normalize_user_message_token(tail_item.get("content") or tail_item.get("message") or "")
            if tail_role == "user" and tail_content == token:
                return updated, False
        max_sequence = -1
        for index, item in enumerate(updated):
            if not isinstance(item, Mapping):
                continue
            raw_sequence = item.get("sequence")
            try:
                seq = int(raw_sequence) if raw_sequence is not None else index
            except (TypeError, ValueError):
                seq = index
            if seq > max_sequence:
                max_sequence = seq
        updated.append(
            {
                "sequence": max_sequence + 1,
                "role": "user",
                "content": token,
            }
        )
        return updated, True

    @staticmethod
    def _build_projection_messages_with_current_user(
        session_payload: Mapping[str, Any],
        *,
        current_user_message: str,
    ) -> list[dict[str, Any]]:
        messages = _message_history(session_payload)
        projected, _ = RoleConsoleHost._append_current_user_message(
            messages,
            current_user_message=current_user_message,
        )
        return projected

    def create_session(
        self,
        *,
        title: str | None = None,
        context_config: Mapping[str, Any] | None = None,
        capability_profile: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_context = _copy_mapping(context_config)
        role_override = str(normalized_context.get("role") or "").strip() or self.role
        with self._session_service() as service:
            session = service.create_session(
                role=role_override,
                host_kind=self.config.host_kind,
                workspace=self.workspace,
                session_type=self.config.session_type,
                attachment_mode=self.config.attachment_mode,
                title=title or self.config.default_session_title,
                context_config=normalized_context,
                capability_profile=_copy_mapping(capability_profile),
            )
            payload = session.to_dict(include_messages=True)
        if not isinstance(payload, dict):
            raise RoleConsoleHostError("failed to create role session")
        return payload

    def ensure_session(
        self,
        session_id: str | None = None,
        *,
        title: str | None = None,
        context_config: Mapping[str, Any] | None = None,
        capability_profile: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if session_id:
            payload = self.load_session(session_id)
            if payload is None:
                raise RoleSessionNotFoundError(f"Session not found: {session_id}")
            return payload

        normalized_context = _copy_mapping(context_config)
        role_override = str(normalized_context.get("role") or "").strip() or self.role

        with self._session_service() as service:
            query_kwargs = self._session_query_kwargs_for_role(role_override)
            active_sessions = service.get_sessions(state=SessionState.ACTIVE.value, limit=1, **query_kwargs)
            candidate_sessions = active_sessions or service.get_sessions(limit=1, **query_kwargs)
            if candidate_sessions:
                payload = candidate_sessions[0].to_dict(include_messages=True)
                if isinstance(payload, dict):
                    return payload

        return self.create_session(
            title=title,
            context_config=normalized_context,
            capability_profile=capability_profile,
        )

    def list_sessions(
        self,
        *,
        limit: int = 20,
        state: str | None = None,
        role: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._session_service() as service:
            sessions = service.get_sessions(
                limit=limit,
                state=state,
                **self._session_query_kwargs_for_role(role),
            )
            result: list[dict[str, Any]] = []
            for session in sessions:
                payload = session.to_dict(include_messages=False)
                if isinstance(payload, dict):
                    result.append(_session_summary(payload))
        return result

    def load_session(self, session_id: str) -> dict[str, Any] | None:
        payload = self._load_session_payload(session_id)
        if payload is None:
            return None
        return _session_summary(payload) | {"messages": _message_history(payload)}

    def load_session_history(self, session_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        payload = self._load_session_payload(session_id, message_limit=limit)
        if payload is None:
            raise RoleSessionNotFoundError(f"Session not found: {session_id}")
        return _message_history(payload, limit=limit)

    def get_status(
        self,
        *,
        session_id: str | None = None,
        session_limit: int = 20,
        role: str | None = None,
    ) -> dict[str, Any]:
        runtime_role = str(role or "").strip() or self.role
        sessions = self.list_sessions(limit=session_limit, role=runtime_role)
        active_session = None
        if session_id:
            active_session = self.load_session(session_id)
        if active_session is None:
            active_session = next((item for item in sessions if item.get("state") == SessionState.ACTIVE.value), None)
        if active_session is None and sessions:
            active_session = sessions[0]

        tasks = self.list_tasks()
        open_task_count = sum(
            1
            for task in tasks
            if str(task.get("status") or "").strip().lower() not in {"completed", "cancelled", "failed"}
        )
        next_task = self.select_next_task() if tasks else None

        return {
            "workspace": self.workspace,
            "role": runtime_role,
            "session_count": len(sessions),
            "active_session_id": active_session.get("id") if isinstance(active_session, dict) else None,
            "active_session_title": active_session.get("title") if isinstance(active_session, dict) else None,
            "task_count": len(tasks),
            "open_task_count": open_task_count,
            "next_task": next_task,
            "task_runtime_available": self._get_task_service(required=False) is not None,
            "task_runtime_error": self._task_service_error,
        }

    def list_tasks(self, *, include_terminal: bool = True) -> list[dict[str, Any]]:
        service = self._get_task_service(required=False)
        if service is None:
            return []
        return [dict(row) for row in service.list_task_rows(include_terminal=include_terminal)]

    def create_task(
        self,
        *,
        subject: str,
        description: str = "",
        priority: int | str = 1,
        owner: str = "",
        assignee: str = "",
        tags: list[str] | None = None,
        estimated_hours: float = 0.0,
        blocked_by: list[int] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        service = self._get_task_service(required=True)
        if service is None:  # pragma: no cover - required=True raises
            raise RoleConsoleHostError("Task runtime is not available for the role console")
        task = service.create(
            subject=subject,
            description=description,
            priority=priority,
            owner=owner,
            assignee=assignee,
            tags=tags,
            estimated_hours=estimated_hours,
            blocked_by=blocked_by,
            metadata=_copy_mapping(metadata),
        )
        return dict(task.to_dict())

    def select_next_task(
        self,
        *,
        requested_task_id: Any = None,
        prefer_resumable: bool = True,
    ) -> dict[str, Any] | None:
        service = self._get_task_service(required=False)
        if service is None:
            return None
        task = service.select_next_task(
            requested_task_id=requested_task_id,
            prefer_resumable=prefer_resumable,
        )
        if task is None:
            return None
        return dict(task)

    async def stream_turn(
        self,
        session_id: str | None,
        message: str,
        *,
        context: Mapping[str, Any] | None = None,
        prompt_appendix: str | None = None,
        history_limit: int | None = None,
        role: str | None = None,
        debug: bool = False,
        enable_cognitive: bool | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        if not session_id:
            session_payload = self.create_session(context_config=_copy_mapping(context))
            session_id = str(session_payload.get("id") or "")
        else:
            session_payload = self._load_session_payload(session_id, message_limit=history_limit)  # type: ignore[assignment]
            if session_payload is None:
                raise RoleSessionNotFoundError(f"Session not found: {session_id}")

        session_context_config_raw = session_payload.get("context_config")
        session_context_config: dict[str, Any] = (
            _copy_mapping(session_context_config_raw) if isinstance(session_context_config_raw, Mapping) else {}
        )
        request_context = _copy_mapping(context)
        runtime_context = _copy_mapping(session_context_config)
        runtime_context.update(request_context)
        runtime_role = str(role or "").strip() or str(runtime_context.get("role") or "").strip() or self.role
        capability_profile = _copy_mapping(
            session_payload.get("capability_profile")
            if isinstance(session_payload.get("capability_profile"), Mapping)
            else None
        )
        debug_enabled = bool(debug) or _coerce_bool(capability_profile.get("debug"))
        runtime_host_kind = (
            str(runtime_context.get("host_kind") or "").strip() or str(self.config.host_kind or "").strip() or "cli"
        )
        debug_events: list[dict[str, Any]] = []
        user_message_persisted = False

        def _persist_user_message_once() -> None:
            nonlocal user_message_persisted
            if user_message_persisted:
                return
            self._persist_message(
                session_id=session_id,
                role="user",
                content=message,
                meta={
                    "source": "director.console_host",
                    "turn": "user",
                },
            )
            user_message_persisted = True

        def _drain_debug_events() -> list[dict[str, Any]]:
            if not debug_events:
                return []
            drained = list(debug_events)
            debug_events.clear()
            return [{"type": "debug", "data": item} for item in drained]

        with debug_stream_session(
            enabled=debug_enabled,
            sink=debug_events.append,
            tags={
                "workspace": self.workspace,
                "role": runtime_role,
                "session_id": session_id,
                "host_kind": runtime_host_kind,
            },
        ):
            # Persist current user turn before continuity projection so ContextOS
            # run_card.latest_intent tracks the active request in the same turn.
            _persist_user_message_once()
            continuity_payload = dict(session_payload)
            projected_messages, _ = self._append_current_user_message(
                _message_history(session_payload),
                current_user_message=message,
            )
            continuity_payload["messages"] = projected_messages

            continuity = await self._project_session_continuity(
                session_id=session_id,
                role=runtime_role,
                session_payload=continuity_payload,
                session_context_config=session_context_config,
                incoming_context=request_context,
                history_limit=history_limit,
            )
            emit_debug_event(
                category="attention",
                label="projected",
                source="delivery.console_host",
                payload={
                    "history_limit": history_limit,
                    "changed": bool(continuity.changed),
                    "recent_message_count": len(continuity.recent_messages),
                    "prompt_context_keys": sorted(continuity.prompt_context.keys()),
                    "continuity_pack_present": bool(
                        continuity.continuity_pack is not None and bool(continuity.continuity_pack.summary)
                    ),
                    "continuity_summary": (
                        continuity.continuity_pack.summary if continuity.continuity_pack is not None else ""
                    ),
                    "stable_facts": list(
                        continuity.continuity_pack.stable_facts if continuity.continuity_pack is not None else ()
                    ),
                    "open_loops": list(
                        continuity.continuity_pack.open_loops if continuity.continuity_pack is not None else ()
                    ),
                    "context_os": summarize_context_os_payload(
                        continuity.prompt_context.get("state_first_context_os")
                        if isinstance(continuity.prompt_context, Mapping)
                        else None
                    ),
                },
            )
            runtime_history_messages = self._trim_current_user_from_recent_messages(
                list(continuity.recent_messages),
                current_user_message=message,
            )
            runtime_history = self._build_runtime_history(runtime_history_messages)
            if continuity.changed:
                self._persist_context_config(
                    session_id=session_id,
                    context_config=continuity.persisted_context_config,
                )

            # ── Cognitive Processing ───────────────────────────────────────────
            # Process message through cognitive middleware for intent understanding,
            # critical thinking, and meta-cognition before execution.
            cognitive_context: dict[str, Any] | None = None
            middleware = self._get_cognitive_middleware(enable_cognitive)
            if middleware is not None:
                try:
                    cognitive_context = await middleware.process(
                        message=message,
                        role_id=runtime_role,
                        session_id=session_id,
                    )
                    if cognitive_context and cognitive_context.get("enabled"):
                        emit_debug_event(
                            category="cognitive",
                            label="intent_detected",
                            source="delivery.console_host",
                            payload={
                                "intent_type": cognitive_context.get("intent_type"),
                                "confidence": cognitive_context.get("confidence"),
                                "uncertainty_score": cognitive_context.get("uncertainty_score"),
                                "execution_path": cognitive_context.get("execution_path"),
                            },
                        )
                        # Check if message was blocked by cognitive policy
                        if cognitive_context.get("blocked"):
                            emit_debug_event(
                                category="cognitive",
                                label="blocked",
                                source="delivery.console_host",
                                payload={
                                    "block_reason": cognitive_context.get("block_reason"),
                                },
                            )
                            yield {
                                "type": "error",
                                "error": f"[Cognitive Blocked] {cognitive_context.get('block_reason')}",
                                "metadata": {
                                    "cognitive": cognitive_context,
                                    "intent_type": cognitive_context.get("intent_type"),
                                    "confidence": cognitive_context.get("confidence"),
                                },
                            }
                            return
                except (RuntimeError, ValueError) as exc:
                    logger.debug("Cognitive middleware processing failed: %s", exc)

            # FIX-20250422: Director 请求清晰度检查
            # 防止模糊需求（如"进一步完善XXX"）导致 Director 死循环探索
            if runtime_role == "director":
                clarity = _assess_director_request_clarity(message)
                if clarity == RequestClarity.VAGUE:
                    logger.warning(
                        "Director received vague request: %r. Blocking and asking for clarification.",
                        message,
                    )
                    yield {
                        "type": "error",
                        "error": (
                            "[请求不够明确] Director 只接收可执行的具体任务。\n\n"
                            "你的请求太模糊，Director 无法直接执行。请提供：\n"
                            "1. 目标文件路径（如：polaris/.../session_orchestrator.py）\n"
                            "2. 具体修改内容（如：在 _check_intent_mismatch 中添加 PHASE_TIMEOUT 检查）\n\n"
                            "或者先使用 Architect 角色制定蓝图，再让 Director 执行。"
                        ),
                        "metadata": {
                            "request_clarity": "vague",
                            "original_request": message,
                        },
                    }
                    return

            metadata: dict[str, Any] = {}
            prompt_appendix_token = str(prompt_appendix or "").strip()

            # Inject cognitive context and generate prompt appendix
            enhanced_context = continuity.prompt_context
            if cognitive_context and cognitive_context.get("enabled") and middleware is not None:
                # Inject cognitive context into context for downstream processing
                enhanced_context = middleware.inject_into_context(cognitive_context, dict(continuity.prompt_context))
                # Generate cognitive prompt appendix for role guidance
                cognitive_appendix = middleware.get_prompt_appendix(cognitive_context)
                if cognitive_appendix:
                    prompt_appendix_token = (
                        f"{prompt_appendix_token} [{cognitive_appendix}]"
                        if prompt_appendix_token
                        else cognitive_appendix
                    )
                # Add cognitive metadata to command metadata
                metadata["cognitive"] = cognitive_context

            if prompt_appendix_token:
                metadata["prompt_appendix"] = prompt_appendix_token
            metadata["host_kind"] = runtime_host_kind
            metadata["session_id"] = session_id
            metadata["debug"] = debug_enabled
            if capability_profile:
                metadata["capability_profile"] = capability_profile

            command = ExecuteRoleSessionCommandV1(
                role=runtime_role,
                session_id=session_id,
                workspace=self.workspace,
                user_message=message,
                history=runtime_history,
                context=enhanced_context,
                metadata=metadata,
                stream=True,
                host_kind=runtime_host_kind,
            )

            response_parts: list[str] = []
            thinking_parts: list[str] = []
            assistant_saved = False
            pending_tool_snapshots: list[dict[str, Any]] = []

            # ── Phase 4: Session Orchestrator Integration ────────────────────────
            use_orchestrator = self._use_orchestrator(capability_profile)
            if use_orchestrator:
                from typing import cast

                from polaris.cells.roles.runtime.internal.session_orchestrator import RoleSessionOrchestrator
                from polaris.cells.roles.runtime.public.service import RoleRuntimeService

                runtime_service_typed = cast(RoleRuntimeService, self._runtime_service)
                try:
                    tx_controller = runtime_service_typed.create_transaction_controller(command)
                except (RuntimeError, ValueError) as exc:
                    logger.warning("Failed to create transaction controller for orchestrator: %s", exc)
                    use_orchestrator = False

            if use_orchestrator:
                orchestrator = RoleSessionOrchestrator(
                    session_id=session_id,
                    kernel=tx_controller,
                    workspace=self.workspace,
                    role=runtime_role,
                    max_auto_turns=int(capability_profile.get("max_auto_turns", 10)) if capability_profile else 10,
                    shadow_engine=None,
                )
                async for orch_event in orchestrator.execute_stream(message, context=runtime_context):
                    for debug_event_item in _drain_debug_events():
                        yield debug_event_item

                    normalized = self._normalize_orchestrator_event(orch_event)
                    if normalized is None:
                        continue

                    self._write_session_event(session_id, normalized)

                    event_type = normalized["type"]
                    event_data = normalized.get("data")
                    event_payload = event_data if isinstance(event_data, dict) else {}
                    if event_type != "fingerprint":
                        _persist_user_message_once()

                    if event_type == "content_chunk":
                        response_parts.append(str(event_payload.get("content") or ""))
                        yield normalized
                        continue

                    if event_type == "thinking_chunk":
                        thinking_parts.append(str(event_payload.get("content") or ""))
                        yield normalized
                        continue

                    if event_type == "tool_call":
                        normalized["data"] = self._enrich_tool_call_payload(event_payload)
                        snapshot = self._snapshot_tool_call(normalized["data"])
                        if snapshot is not None:
                            pending_tool_snapshots.append(snapshot)
                        yield normalized
                        continue

                    if event_type == "tool_result":
                        snapshot = pending_tool_snapshots.pop(0) if pending_tool_snapshots else None
                        normalized["data"] = self._enrich_tool_result_payload(event_payload, snapshot)
                        yield normalized
                        continue

                    if event_type in {
                        "session_started",
                        "session_waiting_human",
                        "runtime_started",
                        "runtime_completed",
                        "turn_phase",
                    }:
                        yield normalized
                        continue

                    if event_type == "session_completed":
                        yield normalized
                        break

                    if event_type == "error":
                        _persist_user_message_once()
                        yield normalized
                        break

                    if event_type == "complete":
                        _persist_user_message_once()
                        assistant_text = str(event_payload.get("content") or "") or "".join(response_parts)
                        thinking_text = str(event_payload.get("thinking") or "") or "".join(thinking_parts) or None
                        if assistant_text:
                            self._persist_message(
                                session_id=session_id,
                                role="assistant",
                                content=assistant_text,
                                thinking=thinking_text,
                                meta={
                                    "source": "director.console_host",
                                    "turn": "assistant",
                                    "stream": True,
                                    "orchestrator": True,
                                },
                            )
                            assistant_saved = True
                        yield {
                            "type": "complete",
                            "data": {
                                "content": assistant_text,
                                "thinking": thinking_text,
                            },
                        }
                        response_parts.clear()
                        thinking_parts.clear()
                        continue

                    yield normalized
            else:
                async for event in self._runtime_service.stream_chat_turn(command):
                    for debug_event in _drain_debug_events():
                        yield debug_event

                    normalized = self._normalize_stream_event(event)
                    if normalized is None:
                        continue

                    event_type = normalized["type"]
                    event_data = normalized.get("data")
                    event_payload = event_data if isinstance(event_data, dict) else {}
                    if event_type != "fingerprint":
                        _persist_user_message_once()

                    if event_type == "content_chunk":
                        response_parts.append(str(event_payload.get("content") or ""))
                        yield normalized
                        continue

                    if event_type == "thinking_chunk":
                        thinking_parts.append(str(event_payload.get("content") or ""))
                        yield normalized
                        continue

                    if event_type == "tool_call":
                        normalized["data"] = self._enrich_tool_call_payload(event_payload)
                        snapshot = self._snapshot_tool_call(normalized["data"])
                        if snapshot is not None:
                            pending_tool_snapshots.append(snapshot)
                        yield normalized
                        continue

                    if event_type == "tool_result":
                        snapshot = pending_tool_snapshots.pop(0) if pending_tool_snapshots else None
                        normalized["data"] = self._enrich_tool_result_payload(event_payload, snapshot)
                        yield normalized
                        continue

                    if event_type == "fingerprint":
                        yield normalized
                        continue

                    if event_type == "error":
                        _persist_user_message_once()
                        yield normalized
                        break

                    if event_type == "complete":
                        _persist_user_message_once()
                        assistant_text = str(event_payload.get("content") or "") or "".join(response_parts)
                        thinking_text = str(event_payload.get("thinking") or "") or "".join(thinking_parts) or None
                        if assistant_text:
                            self._persist_message(
                                session_id=session_id,
                                role="assistant",
                                content=assistant_text,
                                thinking=thinking_text,
                                meta={
                                    "source": "director.console_host",
                                    "turn": "assistant",
                                    "stream": True,
                                },
                            )
                            assistant_saved = True
                        # Build complete event data preserving model, context_budget, usage
                        complete_data = {
                            "content": assistant_text,
                            "thinking": thinking_text,
                        }
                        # Preserve model, context_budget, usage from normalized event
                        if event_payload.get("model"):
                            complete_data["model"] = event_payload["model"]
                        if event_payload.get("context_budget"):
                            complete_data["context_budget"] = event_payload["context_budget"]
                        if event_payload.get("usage"):
                            complete_data["usage"] = event_payload["usage"]
                        yield {
                            "type": "complete",
                            "data": complete_data,
                        }
                        break

                    if event_type == "done":
                        continue

                    yield normalized

            for debug_event in _drain_debug_events():
                yield debug_event

            _persist_user_message_once()

            if response_parts and not assistant_saved:
                assistant_text = "".join(response_parts)
                thinking_text = "".join(thinking_parts) or None
                if assistant_text:
                    self._persist_message(
                        session_id=session_id,
                        role="assistant",
                        content=assistant_text,
                        thinking=thinking_text,
                        meta={
                            "source": "director.console_host",
                            "turn": "assistant",
                            "stream": True,
                            "synthetic_complete": True,
                        },
                    )
                yield {
                    "type": "complete",
                    "data": {
                        "content": assistant_text,
                        "thinking": thinking_text,
                    },
                }

    async def turn(
        self,
        session_id: str,
        message: str,
        *,
        context: Mapping[str, Any] | None = None,
        prompt_appendix: str | None = None,
        history_limit: int | None = None,
        debug: bool = False,
        enable_cognitive: bool | None = None,
    ) -> dict[str, Any]:
        final_event: dict[str, Any] | None = None
        async for event in self.stream_turn(
            session_id,
            message,
            context=context,
            prompt_appendix=prompt_appendix,
            history_limit=history_limit,
            debug=debug,
            enable_cognitive=enable_cognitive,
        ):
            final_event = event
        return final_event or {"type": "complete", "data": {"content": "", "thinking": None}}

    @staticmethod
    def _normalize_stream_event(event: Any) -> dict[str, Any] | None:
        if not isinstance(event, Mapping):
            return None
        event_type = str(event.get("type") or "").strip()
        if not event_type:
            return None
        normalized: dict[str, Any] = {"type": event_type}
        data = event.get("data")
        payload: dict[str, Any] = dict(data) if isinstance(data, Mapping) else {}

        if event_type in {"content_chunk", "thinking_chunk"}:
            payload.setdefault("content", str(event.get("content") or ""))
        elif event_type == "tool_call":
            tool_name = str(event.get("tool") or "").strip()
            if tool_name:
                payload.setdefault("tool", tool_name)
            args = event.get("args")
            if isinstance(args, Mapping):
                payload.setdefault("args", dict(args))
        elif event_type == "tool_result":
            tool_name = str(event.get("tool") or "").strip()
            if tool_name:
                payload.setdefault("tool", tool_name)
            result = event.get("result")
            if isinstance(result, Mapping):
                payload.setdefault("result", dict(result))
                if "success" in result and isinstance(result["success"], bool):
                    payload.setdefault("success", bool(result["success"]))
            if "result" not in payload and result is not None:
                payload["result"] = result
            if "success" not in payload:
                success = _extract_tool_success(payload)
                if success is not None:
                    payload["success"] = bool(success)
            error_text = _extract_error_text(payload)
            if error_text:
                payload.setdefault("error", error_text)
        elif event_type == "complete":
            result = event.get("result")
            if isinstance(result, Mapping):
                payload.setdefault("content", str(result.get("content") or ""))
                thinking = result.get("thinking")
                if thinking is not None:
                    payload.setdefault("thinking", str(thinking))
            elif result is not None:
                payload.setdefault("content", str(getattr(result, "content", "") or ""))
                thinking = getattr(result, "thinking", None)
                if thinking is not None:
                    payload.setdefault("thinking", str(thinking))
            payload.setdefault("content", str(event.get("content") or ""))
            if "thinking" not in payload:
                thinking = event.get("thinking")
                if thinking is not None:
                    payload["thinking"] = str(thinking)
            # Preserve usage info from original event metadata (inside result for complete events)
            if isinstance(result, Mapping):
                original_metadata = result.get("metadata")
            else:
                original_metadata = getattr(result, "metadata", None)
            if isinstance(original_metadata, Mapping):
                # Extract usage if present
                usage = original_metadata.get("usage")
                if isinstance(usage, Mapping):
                    payload["usage"] = dict(usage)
                # Also preserve model if present
                model = original_metadata.get("model")
                if model and "model" not in payload:
                    payload["model"] = model
                # Extract context budget (model_context_window, current_input_tokens) if present
                context_budget = original_metadata.get("context_budget")
                if isinstance(context_budget, Mapping):
                    payload["context_budget"] = dict(context_budget)
            # Also check top-level model field in event (some events have it here)
            if "model" not in payload:
                model = event.get("model")
                if model:
                    payload["model"] = model
        elif event_type == "error":
            error_text = str(event.get("error") or event.get("message") or "").strip()
            if error_text:
                payload.setdefault("error", error_text)
        elif event_type == "fingerprint":
            fingerprint = event.get("fingerprint")
            if isinstance(fingerprint, Mapping):
                payload.setdefault("fingerprint", dict(fingerprint))
            elif fingerprint is not None:
                full_hash = str(getattr(fingerprint, "full_hash", "") or "").strip()
                payload.setdefault("fingerprint", full_hash or str(fingerprint))

        normalized["data"] = payload
        return normalized


class DirectorConsoleError(RoleConsoleHostError):
    """Backward-compatible Director console error."""


class DirectorSessionNotFoundError(RoleSessionNotFoundError, DirectorConsoleError):
    """Backward-compatible Director session lookup error."""


@dataclass(frozen=True, slots=True)
class DirectorConsoleHostConfig(RoleConsoleHostConfig):
    """Director-specific console host configuration."""

    role: str = "director"
    default_session_title: str = "Director CLI"


class DirectorConsoleHost(RoleConsoleHost):
    """Director-specific alias for the shared role console host."""

    def __init__(
        self,
        workspace: str,
        *,
        role: str = "director",
        session_service_factory: Callable[[], RoleSessionService] | None = None,
        task_service_factory: Callable[[str], TaskRuntimeService] | None = None,
        runtime_service_factory: Callable[[], IRoleRuntime] | None = None,
        config: DirectorConsoleHostConfig | None = None,
    ) -> None:
        workspace_token = str(workspace or "").strip()
        director_config = config or DirectorConsoleHostConfig(workspace=workspace_token, role=role)
        super().__init__(
            workspace=workspace_token,
            role=role,
            session_service_factory=session_service_factory,
            task_service_factory=task_service_factory,
            runtime_service_factory=runtime_service_factory,
            config=director_config,
        )


__all__ = [
    "DirectorConsoleError",
    "DirectorConsoleHost",
    "DirectorConsoleHostConfig",
    "DirectorSessionNotFoundError",
    "RoleConsoleHost",
    "RoleConsoleHostConfig",
    "RoleConsoleHostError",
    "RoleSessionNotFoundError",
]
