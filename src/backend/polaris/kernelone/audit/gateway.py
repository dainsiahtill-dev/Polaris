"""Compatibility gateway delegating to Polaris KernelAuditRuntime."""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeAlias

from polaris.kernelone._runtime_config import get_runtime_base, get_workspace
from polaris.kernelone.audit import (
    SYSTEM_ROLE,
    KernelAuditEvent,
    KernelAuditEventType,
    KernelAuditRole,
    KernelAuditRuntime,
    validate_run_id,
)

# Type aliases using TypeAlias for explicit type aliasing
AuditEvent: TypeAlias = KernelAuditEvent
AuditEventType: TypeAlias = KernelAuditEventType
AuditRole: TypeAlias = KernelAuditRole


def _to_kernel_event_type(value: AuditEventType | str) -> KernelAuditEventType:
    if isinstance(value, KernelAuditEventType):
        return value
    token = str(getattr(value, "value", value) or "").strip()
    return KernelAuditEventType(token)


def _to_kernel_role(value: AuditRole | str) -> str:
    """Convert role to plain string.

    .. deprecated::
        Polaris-specific role constants have been migrated out of KernelOne.
        Pass plain ``str`` role values directly instead of using AuditRole enum.
    """
    if isinstance(value, KernelAuditRole):
        return value.value
    return str(getattr(value, "value", value) or "").strip()


def _to_legacy_event(event: KernelAuditEvent) -> AuditEvent:
    return event


class AuditGateway:
    """Legacy-compatible gateway facade over KernelAuditRuntime."""

    _instances: dict[str, AuditGateway] = {}
    _instances_lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls, runtime_root: Path, *args, **kwargs) -> AuditGateway:
        key = str(Path(runtime_root).resolve())
        with cls._instances_lock:
            instance = cls._instances.get(key)
            if instance is None:
                instance = super().__new__(cls)
                cls._instances[key] = instance
                instance._initialized = False
            return instance

    def __init__(self, runtime_root: Path) -> None:
        if getattr(self, "_initialized", False):
            return
        self._runtime_root = Path(runtime_root).resolve()
        self._runtime = KernelAuditRuntime.get_instance(self._runtime_root)
        self._initialized = True

    @classmethod
    def get_instance(cls, runtime_root: Path) -> AuditGateway:
        return cls(runtime_root)

    @classmethod
    def shutdown_all(cls, timeout: float = 5.0) -> None:
        del timeout
        with cls._instances_lock:
            cls._instances.clear()
        KernelAuditRuntime.shutdown_all()

    @classmethod
    def reset_instance(cls, runtime_root: Path) -> None:
        key = str(Path(runtime_root).resolve())
        with cls._instances_lock:
            cls._instances.pop(key, None)

    def shutdown(self, timeout: float = 5.0) -> None:
        del timeout

    def emit_event(
        self,
        event_type: AuditEventType,
        role: str,
        workspace: str,
        task_id: str = "",
        run_id: str = "",
        trace_id: str = "",
        resource: dict[str, Any] | None = None,
        action: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self._runtime.emit_event(
            event_type=_to_kernel_event_type(event_type),
            role=_to_kernel_role(role),
            workspace=workspace,
            task_id=task_id,
            run_id=run_id,
            trace_id=trace_id,
            resource=resource,
            action=action,
            data=data,
            context=context,
        )
        return {
            "success": result.success,
            "event_id": result.event_id,
            "warnings": list(result.warnings),
            "error": result.error,
        }

    def emit_llm_event(
        self,
        role: str,
        workspace: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        duration_ms: float = 0.0,
        task_id: str = "",
        run_id: str = "",
        trace_id: str = "",
        success: bool = True,
        error: str | None = None,
    ) -> dict[str, Any]:
        result = self._runtime.emit_llm_event(
            role=_to_kernel_role(role),
            workspace=workspace,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=duration_ms,
            task_id=task_id,
            run_id=run_id,
            trace_id=trace_id,
            success=success,
            error=error,
        )
        return {
            "success": result.success,
            "event_id": result.event_id,
            "warnings": list(result.warnings),
            "error": result.error,
        }

    def emit_dialogue(
        self,
        role: str,
        workspace: str,
        dialogue_type: str,
        message_summary: str,
        task_id: str = "",
        run_id: str = "",
        trace_id: str = "",
    ) -> dict[str, Any]:
        result = self._runtime.emit_dialogue(
            role=_to_kernel_role(role),
            workspace=workspace,
            dialogue_type=dialogue_type,
            message_summary=message_summary,
            task_id=task_id,
            run_id=run_id,
            trace_id=trace_id,
        )
        return {
            "success": result.success,
            "event_id": result.event_id,
            "warnings": list(result.warnings),
            "error": result.error,
        }

    def query_by_run_id(self, run_id: str, limit: int = 1000) -> list[AuditEvent]:
        return [_to_legacy_event(item) for item in self._runtime.query_by_run_id(run_id, limit=limit)]

    def query_by_task_id(self, task_id: str, limit: int = 1000) -> list[AuditEvent]:
        return [_to_legacy_event(item) for item in self._runtime.query_by_task_id(task_id, limit=limit)]

    def query_by_trace_id(self, trace_id: str, limit: int = 1000) -> list[AuditEvent]:
        return [_to_legacy_event(item) for item in self._runtime.query_by_trace_id(trace_id, limit=limit)]

    def get_corruption_log(self, limit: int = 100) -> list[dict[str, Any]]:
        # Priority: KERNELONE_WORKSPACE env var (via _runtime_config), then KERNELONE_WORKSPACE fallback
        ws = get_workspace()
        workspace = ws if ws else os.getcwd()
        return self._runtime.get_corruption_log(workspace=workspace, limit=limit)

    def verify_chain(self) -> dict[str, Any]:
        result = self._runtime.verify_chain()
        return {
            "chain_valid": result.is_valid,
            "first_event_hash": result.first_hash,
            "last_event_hash": result.last_hash,
            "total_events": result.total_events,
            "gap_count": result.gap_count,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "invalid_events": result.invalid_events,
        }

    @property
    def store(self) -> Any:
        return self._runtime.raw_store

    @property
    def runtime_root(self) -> Path:
        return self._runtime_root


def get_gateway(runtime_root: Path | None = None) -> AuditGateway:
    if runtime_root is None:
        # Priority: KERNELONE_RUNTIME_BASE (via _runtime_config), then KERNELONE_RUNTIME_BASE fallback
        base = get_runtime_base() or "runtime"
        runtime_root = Path(base).resolve()
    return AuditGateway.get_instance(runtime_root)


def emit_audit_event(
    event_type: AuditEventType,
    role: str,
    workspace: str,
    **kwargs: Any,
) -> dict[str, Any]:
    gateway = get_gateway()
    return gateway.emit_event(event_type, role, workspace, **kwargs)


__all__ = [
    "SYSTEM_ROLE",
    "AuditEvent",
    "AuditEventType",
    "AuditGateway",
    "AuditRole",
    "emit_audit_event",
    "get_gateway",
    "validate_run_id",
]
