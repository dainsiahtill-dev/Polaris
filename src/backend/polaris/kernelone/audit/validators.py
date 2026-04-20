"""Validation helpers for KernelOne audit runtime."""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import KernelAuditEventType, KernelAuditRole

#: Canonical system-role sentinel for Cell-layer callers that need a safe default.
#: This replaces direct usage of KernelAuditRole.SYSTEM in Cell code.
SYSTEM_ROLE: str = KernelAuditRole.SYSTEM.value

_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")


def validate_run_id(run_id: str) -> bool:
    """Validate run id using KernelOne-owned deterministic rules."""
    token = str(run_id or "").strip()
    if not token:
        return False
    return _RUN_ID_PATTERN.fullmatch(token) is not None


def require_valid_run_id(run_id: str) -> str:
    """Require a valid run id, otherwise raise ValueError."""
    token = str(run_id or "").strip()
    if token and not validate_run_id(token):
        raise ValueError(f"invalid run_id: {run_id}")
    return token


def normalize_workspace_path(workspace: str) -> str:
    """Normalize workspace path to an absolute path."""
    token = str(workspace or "").strip()
    if not token:
        token = os.getcwd()
    return str(Path(token).resolve())


def normalize_optional_mapping(value: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize optional mapping input."""
    if not isinstance(value, dict):
        return {}
    return {str(key): val for key, val in value.items()}


def derive_task_id(run_id: str, now: datetime | None = None) -> str:
    """Derive task id when caller did not provide one."""
    token = str(run_id or "").strip()
    if token:
        return f"task-{token}"
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M%S")
    return f"derived-{stamp}"


def derive_trace_id() -> str:
    """Generate a compact trace id."""
    return uuid.uuid4().hex[:16]


def normalize_event_type(value: KernelAuditEventType | str) -> KernelAuditEventType:
    """Normalize event type input into enum."""
    if isinstance(value, KernelAuditEventType):
        return value
    return KernelAuditEventType(str(value or "").strip())


def normalize_role(value: KernelAuditRole | str) -> str:
    """Normalize role input into a plain string.

    Polaris-specific role constants have been migrated out of KernelOne.
    This function accepts both the deprecated KernelAuditRole enum and plain
    strings for backward compatibility, but always returns ``str``.
    """
    if isinstance(value, KernelAuditRole):
        return value.value
    return str(value or "").strip()
