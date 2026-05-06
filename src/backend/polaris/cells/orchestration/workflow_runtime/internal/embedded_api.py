"""Embedded Workflow and Activity API.

提供与 Workflow 风格兼容的装饰器和执行入口，并在内嵌运行时上执行。

IMPORTANT: This is the CELL-level Activity registration mechanism.

This module provides the Cell-level embedded Activity API:

1. ActivityRunner.register_handler() (kernelone level):
   - For general-purpose activities registered at the kernel level (see kernelone/workflow/activity_runner.py)
   - Full lifecycle management: Heartbeat, cancellation propagation, retry logic
   - Used by SagaWorkflowEngine and other kernel-level runtimes

2. EmbeddedActivityAPI.defn() / ActivityRegistry (Cell level):
   - For Cell-specific embedded activities
   - Decorator-based registration: @activity.defn
   - Cell-local registry via get_activity_registry()
   - Activities registered here are looked up by _lookup_activity_handler()

INTENTIONAL SEPARATION:
- ActivityRunner is the KernelOne runtime foundation
- EmbeddedActivityAPI is a convenience wrapper for Cell-level activities
- Cells should use EmbeddedActivityAPI (@activity.defn), not ActivityRunner directly
- Both mechanisms can coexist in the same runtime
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, get_type_hints

from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.runtime import (
    get_activity_registry,
    get_runtime,
    get_workflow_registry,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

from polaris.kernelone.constants import MAX_WORKFLOW_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

_CONTROL_KWARGS = {
    "start_to_close_timeout",
    "schedule_to_close_timeout",
    "schedule_to_start_timeout",
    "heartbeat_timeout",
    "retry_policy",
    "task_queue",
    "id",
    "workflow_id",
    "input",
    "execution_timeout",
    "run_timeout",
}
_WORKFLOW_CONTRACT_MODE_KEY = "_workflow_contract_mode"
_WORKFLOW_CONTRACT_MODE_LEGACY = "legacy"


@dataclass
class WorkflowContext:
    """Workflow execution context bound to one coroutine."""

    workflow_id: str
    payload: dict[str, Any]
    workflow_name: str
    runtime_engine: Any = None
    workflow_instance: Any = None
    queries: dict[str, Callable[..., Any]] = field(default_factory=dict)
    signals: dict[str, Callable[..., Any]] = field(default_factory=dict)
    received_signals: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def set_query(self, name: str, handler: Callable[..., Any]) -> None:
        token = str(name or "").strip()
        if token:
            self.queries[token] = handler

    def set_signal(self, name: str, handler: Callable[..., Any]) -> None:
        token = str(name or "").strip()
        if token:
            self.signals[token] = handler

    def record_signal(self, name: str, payload: dict[str, Any] | None = None) -> None:
        token = str(name or "").strip()
        if not token:
            return
        self.received_signals.setdefault(token, []).append(dict(payload or {}))

    @property
    def info(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "payload": dict(self.payload),
        }


_workflow_context_var: ContextVar[WorkflowContext | None] = ContextVar(
    "embedded_workflow_context",
    default=None,
)


def get_workflow_context() -> WorkflowContext | None:
    """Return the current workflow execution context."""
    return _workflow_context_var.get()


def set_workflow_context(context: WorkflowContext) -> Token[WorkflowContext | None]:
    """Bind context to current coroutine and return reset token."""
    return _workflow_context_var.set(context)


def clear_workflow_context(token: Token[WorkflowContext | None]) -> None:
    """Reset workflow context to previous value."""
    _workflow_context_var.reset(token)


def _to_snake_case(name: str) -> str:
    token = str(name or "").strip()
    if not token:
        return ""
    first = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", token)
    second = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", first)
    return second.lower()


def _normalize_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def _serialize_result(value: Any) -> Any:
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        raw = to_dict()
        if isinstance(raw, dict):
            return _normalize_mapping(raw)
    if is_dataclass(value):
        return asdict(value)  # type: ignore[arg-type]
    if isinstance(value, dict):
        return _normalize_mapping(value)
    return value


def _payload_from_value(value: Any) -> dict[str, Any]:
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            raw = to_dict()
        except (RuntimeError, ValueError):
            raw = {}
        if isinstance(raw, dict):
            return _normalize_mapping(raw)
    if is_dataclass(value):
        raw = asdict(value)  # type: ignore[arg-type]
        return _normalize_mapping(raw)
    if isinstance(value, dict):
        return _normalize_mapping(value)
    return {"value": value}


def _convert_for_annotation(value: Any, annotation: Any) -> Any:
    if annotation is inspect.Signature.empty:
        return value
    if annotation is Any:
        return value
    try:
        if isinstance(annotation, type) and isinstance(value, annotation):
            return value
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Type coercion failed: {e}")
    from_mapping = getattr(annotation, "from_mapping", None)
    if callable(from_mapping) and isinstance(value, dict):
        return from_mapping(value)
    if isinstance(annotation, type) and is_dataclass(annotation) and isinstance(value, dict):
        names = {field_def.name for field_def in fields(annotation)}
        filtered = {key: item for key, item in value.items() if key in names}
        return annotation(**filtered)
    return value


def _resolve_forward_annotation(
    annotation: Any,
    run_method: Callable[..., Any],
) -> Any:
    """Resolve a forward-reference annotation string without eval().

    Uses get_type_hints() which safely evaluates string annotations within
    the proper namespace context (PEP 560 / PEP 563). Falls back to a
    restricted lookup in workflow_models before returning the original string.
    This eliminates the arbitrary-code-execution risk of eval().
    """
    if not isinstance(annotation, str):
        return annotation
    token = str(annotation or "").strip()
    if not token:
        return annotation

    owner = getattr(run_method, "__self__", None)
    localns = vars(owner.__class__) if owner is not None else {}
    globalns = getattr(run_method, "__globals__", {})

    # Safe primary approach: delegate to get_type_hints() which resolves
    # string annotations in the proper namespace (no eval exposure).
    try:
        # Build a synthetic callable that carries the annotation as its
        # return annotation so get_type_hints() processes it safely.
        hints = get_type_hints(run_method, globalns=globalns, localns=localns)
        # If the annotation string refers to a type in the namespace,
        # get_type_hints has already resolved it.  Check whether the
        # original token survived as a resolved type.
        for name, resolved in hints.items():
            if name == token or (isinstance(resolved, type) and resolved.__name__ == token):
                return resolved
    except (NameError, RuntimeError, ValueError) as e:
        logger.debug("get_type_hints could not resolve %s: %s", token, e)

    # Restricted fallback: only accept a name that exists in the
    # whitelisted workflow-models module -- no arbitrary evaluation.
    try:
        from . import models as workflow_models

        candidate = getattr(workflow_models, token, None)
        if candidate is not None:
            return candidate
    except (RuntimeError, ValueError) as e:
        logger.debug("workflow_models lookup for %s failed: %s", token, e)

    return annotation


def _build_run_args(run_method: Callable[..., Any], payload: dict[str, Any]) -> tuple[Any, ...]:
    try:
        signature = inspect.signature(run_method)
    except (TypeError, ValueError):
        return (payload,)
    params = list(signature.parameters.values())
    if not params:
        return ()
    first = params[0]
    if first.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
        return (payload,)
    annotation = first.annotation
    owner = getattr(run_method, "__self__", None)
    localns = vars(owner.__class__) if owner is not None else {}
    globalns = getattr(run_method, "__globals__", {})
    try:
        resolved_hints = get_type_hints(
            run_method,
            globalns=globalns,
            localns=localns,
        )
        annotation = resolved_hints.get(first.name, annotation)
    except (NameError, RuntimeError, ValueError) as e:
        logger.debug(f"Failed to resolve type hints: {e}")
    annotation = _resolve_forward_annotation(annotation, run_method)
    converted = _convert_for_annotation(payload, annotation)
    return (converted,)


def _extract_marker_map(
    cls: type,
    marker_attr: str,
    default_name_attr: str,
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for attr_name, attr in cls.__dict__.items():
        if not callable(attr):
            continue
        marked = getattr(attr, marker_attr, None)
        if marked is None:
            continue
        token = str(marked or getattr(attr, default_name_attr, "") or attr_name).strip()
        if token:
            mapping[token] = attr_name
    return mapping


def _pick_run_method_name(cls: type) -> str:
    for attr_name, attr in cls.__dict__.items():
        if callable(attr) and bool(getattr(attr, "__embedded_workflow_run__", False)):
            return attr_name
    if callable(getattr(cls, "run", None)):
        return "run"
    raise RuntimeError(f"Workflow `{cls.__name__}` does not define a run method")


def _callable_param_names(handler: Callable[..., Any] | None) -> list[str]:
    if handler is None:
        return []
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        return []
    names: list[str] = []
    for item in signature.parameters.values():
        if item.kind in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }:
            names.append(item.name)
    if names and names[0] == "self":
        return names[1:]
    return names


def _accepts_var_kwargs(handler: Callable[..., Any] | None) -> bool:
    if handler is None:
        return True
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        return True
    return any(item.kind == inspect.Parameter.VAR_KEYWORD for item in signature.parameters.values())


def _resolve_activity_name(activity_type: Any) -> str:
    if isinstance(activity_type, str):
        return str(activity_type).strip()
    if callable(activity_type):
        return str(getattr(activity_type, "__name__", "") or "").strip()
    return str(activity_type or "").strip()


def _resolve_child_workflow_name(workflow_type: Any) -> str:
    if isinstance(workflow_type, str):
        return str(workflow_type).strip()
    direct_name = getattr(workflow_type, "__embedded_workflow_name__", None)
    if isinstance(direct_name, str) and direct_name.strip():
        return direct_name.strip()
    origin = getattr(workflow_type, "__func__", workflow_type)
    origin_name = getattr(origin, "__embedded_workflow_name__", None)
    if isinstance(origin_name, str) and origin_name.strip():
        return origin_name.strip()
    qualname = str(getattr(origin, "__qualname__", "") or "").strip()
    if "." in qualname:
        class_name = qualname.split(".", 1)[0]
        token = _to_snake_case(class_name)
        if token:
            return token
    name = str(getattr(origin, "__name__", "") or "").strip()
    return _to_snake_case(name) if name else ""


def _coerce_timeout_seconds(value: Any, *, default: float) -> float:
    if isinstance(value, timedelta):
        parsed = value.total_seconds()
    else:
        try:
            parsed = float(value) if value is not None else default
        except (TypeError, ValueError):
            parsed = default
    return max(0.1, parsed)


def _build_activity_input(
    handler: Callable[..., Any] | None,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    filtered_kwargs = {
        str(key): value for key, value in kwargs.items() if str(key) not in _CONTROL_KWARGS and str(key) != "input"
    }
    explicit_input = kwargs.get("input")
    if isinstance(explicit_input, dict):
        input_payload = _normalize_mapping(explicit_input)
        input_payload.update(filtered_kwargs)
        return input_payload

    param_names = _callable_param_names(handler)
    accepts_kwargs = _accepts_var_kwargs(handler)

    result_payload: dict[str, Any] = {}
    if args:
        if len(args) == 1:
            arg = args[0]
            if isinstance(arg, dict):
                if param_names and param_names[0] not in arg and not accepts_kwargs:
                    result_payload[param_names[0]] = arg
                else:
                    result_payload = _normalize_mapping(arg)
            elif param_names:
                result_payload[param_names[0]] = arg
            else:
                result_payload["value"] = arg
        elif param_names:
            for index, arg in enumerate(args):
                key = param_names[index] if index < len(param_names) else f"arg_{index}"
                result_payload[str(key)] = arg
        else:
            result_payload = {f"arg_{index}": arg for index, arg in enumerate(args)}

    result_payload.update(filtered_kwargs)
    return result_payload


async def _resolve_runtime_engine(context: WorkflowContext | None) -> Any:
    if context is not None and context.runtime_engine is not None:
        return context.runtime_engine

    from .runtime_backend_adapter import get_adapter

    adapter = await get_adapter()
    if not adapter._running:
        await adapter.start()
    if adapter._engine is not None:
        return adapter._engine

    return await get_runtime()


def _lookup_activity_handler(runtime_engine: Any, activity_name: str) -> Callable[..., Any] | None:
    token = str(activity_name or "").strip()
    if not token:
        return None
    runner = getattr(runtime_engine, "_activity_runner", None)
    handlers = getattr(runner, "_handlers", {}) if runner is not None else {}
    handler = handlers.get(token) if isinstance(handlers, dict) else None
    if callable(handler):
        return handler
    definition = get_activity_registry().get(token)
    if definition is not None:
        return definition.handler
    return None


async def _execute_activity_with_engine(
    runtime_engine: Any,
    *,
    workflow_id: str,
    activity_name: str,
    input_payload: dict[str, Any],
    timeout_seconds: float,
) -> Any:
    runner = getattr(runtime_engine, "_activity_runner", None)
    if runner is None:
        raise RuntimeError("Runtime engine does not expose an activity runner")

    activity_id = f"{workflow_id}-{activity_name}-{datetime.now(timezone.utc).timestamp()}"
    config = None
    try:
        from polaris.kernelone.workflow.activity_runner import ActivityConfig

        config = ActivityConfig(timeout_seconds=max(1, int(timeout_seconds)))
    except (ImportError, RuntimeError, ValueError):
        config = None

    await runner.submit_activity(
        activity_id=activity_id,
        activity_name=activity_name,
        workflow_id=workflow_id,
        input=input_payload,
        config=config,
    )

    deadline = asyncio.get_running_loop().time() + max(timeout_seconds, 1.0)
    while True:
        status = await runner.get_activity_status(activity_id)
        if status is not None:
            token = str(status.status or "").strip().lower()
            if token == "completed":
                return status.result
            if token in {"failed", "cancelled"}:
                detail = str(status.error or "").strip()
                if detail:
                    raise RuntimeError(f"Activity `{activity_name}` failed: {detail}")
                raise RuntimeError(f"Activity `{activity_name}` failed with status `{token}`")
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(f"Activity `{activity_name}` timed out after {timeout_seconds:.2f}s")
        await asyncio.sleep(0.05)


def _unwrap_workflow_result(snapshot_result: Any) -> Any:
    if not isinstance(snapshot_result, dict):
        return snapshot_result
    status = str(snapshot_result.get("status") or "").strip().lower()
    if status == "completed" and "result" in snapshot_result:
        return snapshot_result.get("result")
    if snapshot_result.get("mode") == "legacy" and "result" in snapshot_result:
        return snapshot_result.get("result")
    return snapshot_result


class EmbeddedWorkflowAPI:
    """Embedded Workflow API - 兼容 Workflow workflow 模块。"""

    @staticmethod
    def defn(cls: type | None = None, **kwargs: Any) -> Callable[[type], type]:
        """`@workflow.defn` 装饰器。"""

        def decorator(c: type) -> type:
            if not isinstance(c, type):
                raise TypeError("workflow.defn expects a class")

            timeout = int(kwargs.get("timeout", MAX_WORKFLOW_TIMEOUT_SECONDS))
            primary_name = str(kwargs.get("name") or "").strip() or _to_snake_case(c.__name__)
            if not primary_name:
                raise ValueError("workflow name cannot be empty")
            alias_names: list[str] = []
            raw_aliases = kwargs.get("aliases")
            if isinstance(raw_aliases, list):
                alias_names.extend([str(item).strip() for item in raw_aliases if str(item).strip()])
            class_alias = str(c.__name__).strip()
            if class_alias and class_alias != primary_name:
                alias_names.append(class_alias)

            run_method_name = _pick_run_method_name(c)
            query_map = _extract_marker_map(
                c,
                marker_attr="__embedded_workflow_query_name__",
                default_name_attr="__name__",
            )
            signal_map = _extract_marker_map(
                c,
                marker_attr="__embedded_workflow_signal_name__",
                default_name_attr="__name__",
            )

            async def workflow_handler(
                workflow_id: str,
                payload: dict[str, Any] | None = None,
                runtime_engine: Any = None,
            ) -> Any:
                instance = c()
                normalized_payload = _normalize_mapping(payload)
                context = WorkflowContext(
                    workflow_id=str(workflow_id or "").strip(),
                    payload=normalized_payload,
                    workflow_name=primary_name,
                    runtime_engine=runtime_engine,
                    workflow_instance=instance,
                )
                for query_name, method_name in query_map.items():
                    method = getattr(instance, method_name, None)
                    if callable(method):
                        context.set_query(query_name, method)
                for signal_name, method_name in signal_map.items():
                    method = getattr(instance, method_name, None)
                    if callable(method):
                        context.set_signal(signal_name, method)
                if runtime_engine is not None:
                    bind = getattr(runtime_engine, "bind_workflow_context", None)
                    if callable(bind):
                        try:
                            bind(context.workflow_id, context)
                        except (RuntimeError, ValueError):
                            logger.debug(
                                "Failed to bind workflow context for `%s`",
                                context.workflow_id,
                            )

                token = set_workflow_context(context)
                try:
                    run_method = getattr(instance, run_method_name, None)
                    if not callable(run_method):
                        raise RuntimeError(f"Workflow `{c.__name__}` run method is not callable")
                    run_args = _build_run_args(run_method, normalized_payload)
                    result = run_method(*run_args)
                    if inspect.isawaitable(result):
                        result = await result
                    return _serialize_result(result)
                finally:
                    if runtime_engine is not None:
                        cache_snapshot = getattr(runtime_engine, "cache_workflow_snapshot", None)
                        if callable(cache_snapshot):
                            snapshot_handler = context.queries.get("get_runtime_snapshot")
                            if callable(snapshot_handler):
                                try:
                                    snapshot_value = snapshot_handler()
                                    if inspect.isawaitable(snapshot_value):
                                        snapshot_value = await snapshot_value
                                    if isinstance(snapshot_value, dict):
                                        cache_snapshot(context.workflow_id, _normalize_mapping(snapshot_value))
                                except (RuntimeError, ValueError):
                                    logger.debug(
                                        "Failed to cache workflow snapshot for `%s`",
                                        context.workflow_id,
                                    )
                        unbind = getattr(runtime_engine, "unbind_workflow_context", None)
                        if callable(unbind):
                            try:
                                unbind(context.workflow_id)
                            except (RuntimeError, ValueError):
                                logger.debug(
                                    "Failed to unbind workflow context for `%s`",
                                    context.workflow_id,
                                )
                    clear_workflow_context(token)

            workflow_handler.__embedded_workflow_name__ = primary_name  # type: ignore[attr-defined]
            run_method = getattr(c, run_method_name, None)
            if callable(run_method):
                run_method.__embedded_workflow_name__ = primary_name  # type: ignore[attr-defined]
            c.__embedded_workflow_name__ = primary_name  # type: ignore[attr-defined]
            c.__embedded_workflow_handler__ = workflow_handler  # type: ignore[attr-defined]

            registry = get_workflow_registry()
            registry.register(primary_name, workflow_handler, timeout=timeout)
            for alias in alias_names:
                if alias != primary_name:
                    registry.register(alias, workflow_handler, timeout=timeout)
            logger.debug("Registered workflow `%s` with aliases %s", primary_name, alias_names)
            return c

        if cls is None:
            return decorator
        return decorator(cls)

    @staticmethod
    def run(
        fn: Callable[..., Coroutine[Any, Any, Any]] | None = None,
        **kwargs: Any,
    ) -> Callable[[Callable[..., Coroutine[Any, Any, Any]]], Callable[..., Coroutine[Any, Any, Any]]]:
        """`@workflow.run` decorator."""

        def decorator(
            f: Callable[..., Coroutine[Any, Any, Any]],
        ) -> Callable[..., Coroutine[Any, Any, Any]]:
            f.__embedded_workflow_run__ = True  # type: ignore[attr-defined]
            return f

        if fn is None:
            return decorator
        return decorator(fn)  # type: ignore[return-value]

    @staticmethod
    def query(fn: Callable[..., Any] | None = None, **kwargs: Any) -> Callable[..., Any]:
        """`@workflow.query` decorator."""

        def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            name = str(kwargs.get("name") or f.__name__).strip()
            f.__embedded_workflow_query_name__ = name  # type: ignore[attr-defined]
            return f

        if fn is None:
            return decorator
        return decorator(fn)

    @staticmethod
    def signal(fn: Callable[..., Any] | None = None, **kwargs: Any) -> Callable[..., Any]:
        """`@workflow.signal` decorator."""

        def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            name = str(kwargs.get("name") or f.__name__).strip()
            f.__embedded_workflow_signal_name__ = name  # type: ignore[attr-defined]
            return f

        if fn is None:
            return decorator
        return decorator(fn)

    @staticmethod
    async def execute_activity(
        activity_type: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """执行 Activity，并等待完成。"""
        context = get_workflow_context()
        if context is None:
            raise RuntimeError("No workflow context")
        activity_name = _resolve_activity_name(activity_type)
        if not activity_name:
            raise ValueError("activity_type is required")

        runtime_engine = await _resolve_runtime_engine(context)
        if runtime_engine is None:
            raise RuntimeError("Runtime not initialized")

        handler = _lookup_activity_handler(runtime_engine, activity_name)
        input_payload = _build_activity_input(handler, args, kwargs)
        timeout_seconds = _coerce_timeout_seconds(
            kwargs.get("start_to_close_timeout"),
            default=60.0,
        )
        return await _execute_activity_with_engine(
            runtime_engine,
            workflow_id=context.workflow_id,
            activity_name=activity_name,
            input_payload=input_payload,
            timeout_seconds=timeout_seconds,
        )

    @staticmethod
    async def execute_child_workflow(
        workflow_type: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """执行子工作流并等待终态。"""
        context = get_workflow_context()
        if context is None:
            raise RuntimeError("No workflow context")

        workflow_name = _resolve_child_workflow_name(workflow_type)
        if not workflow_name:
            raise ValueError("workflow_type is required")

        runtime_engine = await _resolve_runtime_engine(context)
        if runtime_engine is None:
            raise RuntimeError("Runtime not initialized")

        payload_value = kwargs.get("input", args[0] if args else {})
        payload = _payload_from_value(payload_value)
        payload.setdefault(_WORKFLOW_CONTRACT_MODE_KEY, _WORKFLOW_CONTRACT_MODE_LEGACY)
        child_id = str(kwargs.get("id") or kwargs.get("workflow_id") or "").strip()
        if not child_id:
            child_id = f"{context.workflow_id}-{workflow_name}-{int(datetime.now(timezone.utc).timestamp() * 1000)}"

        submission = await runtime_engine.start_workflow(
            workflow_name=workflow_name,
            workflow_id=child_id,
            payload=payload,
        )
        if not bool(getattr(submission, "submitted", False)):
            error = str(getattr(submission, "error", "") or "child_workflow_submit_failed").strip()
            raise RuntimeError(error)

        timeout_seconds = _coerce_timeout_seconds(
            kwargs.get("execution_timeout", kwargs.get("run_timeout")),
            default=120.0,
        )
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        try:
            while True:
                snapshot = await runtime_engine.describe_workflow(child_id)
                status = str(snapshot.status or "").strip().lower()
                if status in {"completed", "failed", "cancelled"}:
                    if status != "completed":
                        detail = ""
                        if isinstance(snapshot.result, dict):
                            detail = str(snapshot.result.get("error") or "").strip()
                        suffix = f": {detail}" if detail else ""
                        raise RuntimeError(f"Child workflow `{workflow_name}` finished with `{status}`{suffix}")
                    return _unwrap_workflow_result(snapshot.result)
                if asyncio.get_running_loop().time() >= deadline:
                    # 超时时先取消子工作流，避免资源泄漏
                    await runtime_engine.cancel_workflow(child_id, reason="parent_workflow_timeout")
                    raise TimeoutError(f"Child workflow `{workflow_name}` timed out after {timeout_seconds:.2f}s")
                await asyncio.sleep(0.05)
        except (RuntimeError, TimeoutError):
            # 异常时也尝试取消子工作流
            try:
                await runtime_engine.cancel_workflow(child_id, reason="parent_workflow_error")
            except (RuntimeError, ValueError) as e:
                logger.debug(f"Failed to cancel child workflow: {e}")
            raise

    @staticmethod
    def now() -> datetime:
        """返回当前 UTC 时间。"""
        return datetime.now(timezone.utc)

    @staticmethod
    def sleep(seconds: float) -> Coroutine[Any, Any, None]:
        """异步 sleep。"""
        return asyncio.sleep(max(0.0, float(seconds)))


class EmbeddedActivityAPI:
    """Embedded Activity API - 兼容 Workflow activity 模块。

    This is the CELL-level Activity registration API.

    Usage:
        @activity.defn
        async def my_activity(arg1: str, arg2: int) -> dict:
            ...

    NOTE: This registers activities to the Cell-level ActivityRegistry.
    For KernelOne-level Activity registration, use ActivityRunner.register_handler()
    in kernelone/workflow/activity_runner.py.
    """

    @staticmethod
    def defn(fn: Callable[..., Any] | None = None, **kwargs: Any) -> Callable[..., Any]:
        """`@activity.defn` 装饰器。"""

        def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            name = str(kwargs.get("name") or f.__name__).strip()
            if not name:
                raise ValueError("activity name cannot be empty")
            timeout = int(kwargs.get("timeout", 300))
            get_activity_registry().register(name, f, timeout=timeout)
            logger.debug("Registered activity `%s`", name)
            return f

        if fn is None:
            return decorator
        return decorator(fn)


# 全局实例
embedded_workflow = EmbeddedWorkflowAPI()
embedded_activity = EmbeddedActivityAPI()


def get_embedded_workflow_api() -> EmbeddedWorkflowAPI:
    """获取 Embedded Workflow API。"""
    return embedded_workflow


def get_embedded_activity_api() -> EmbeddedActivityAPI:
    """获取 Embedded Activity API。"""
    return embedded_activity


def get_workflow_api() -> Any:
    """获取自研 Workflow API。"""
    return embedded_workflow


def get_activity_api() -> Any:
    """获取自研 Workflow Activity API。"""
    return embedded_activity
