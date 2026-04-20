"""Internal models for ``runtime.task_market``."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

QUEUE_STAGES = frozenset(
    {
        "pending_design",
        "pending_exec",
        "pending_qa",
        "waiting_human",
    }
)
TERMINAL_STATUSES = frozenset({"resolved", "rejected", "dead_letter"})
IN_PROGRESS_BY_STAGE: dict[str, str] = {
    "pending_design": "in_design",
    "pending_exec": "in_execution",
    "pending_qa": "in_qa",
    "waiting_human": "waiting_human",
}
PRIORITY_WEIGHT: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def now_epoch() -> float:
    return datetime.now(timezone.utc).timestamp()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw_items = list(value)
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def _coerce_bool(value: object, *, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"1", "true", "yes", "y", "on"}:
            return True
        if token in {"0", "false", "no", "n", "off"}:
            return False
    return default


@dataclass(slots=True)
class TaskWorkItemRecord:
    task_id: str
    trace_id: str
    run_id: str
    workspace: str
    stage: str
    status: str
    priority: str
    plan_id: str = ""
    plan_revision_id: str = ""
    root_task_id: str = ""
    parent_task_id: str = ""
    is_leaf: bool = True
    depends_on: list[str] = field(default_factory=list)
    requirement_digest: str = ""
    constraint_digest: str = ""
    summary_ref: str = ""
    superseded_by_revision: str = ""
    change_policy: str = "strict"
    compensation_group_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    attempts: int = 0
    max_attempts: int = 3
    lease_token: str = ""
    lease_expires_at: float = 0.0
    claimed_by: str = ""
    claimed_role: str = ""
    last_error: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def active_status(self) -> str:
        return IN_PROGRESS_BY_STAGE.get(self.stage, "")

    def is_claimable(self, stage: str, at_epoch: float) -> bool:
        """Return True if this task can be claimed at the given stage/epoch.

        A task is claimable when:
        1. Its stage matches the requested stage; AND
        2. Either:
           a. It is sitting in the queue (status == stage, no active lease); OR
           b. It has an expired lease (lease_expires_at <= at_epoch) so it is
              visible again after visibility-timeout.
        """
        if self.stage != stage:
            return False
        # If there is a non-expired lease, it is NOT claimable.
        if self.lease_token and self.lease_expires_at > at_epoch:
            return False
        # If status == stage, the task is in the queue without an active lease.
        if self.status == stage:
            return True
        # If status is the in-progress form and the lease has expired, it is
        # visible again.
        active = self.active_status()
        return bool(active and self.status == active)

    def clear_lease(self) -> None:
        self.lease_token = ""
        self.lease_expires_at = 0.0
        self.claimed_by = ""
        self.claimed_role = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "workspace": self.workspace,
            "stage": self.stage,
            "status": self.status,
            "priority": self.priority,
            "plan_id": self.plan_id,
            "plan_revision_id": self.plan_revision_id,
            "root_task_id": self.root_task_id or self.task_id,
            "parent_task_id": self.parent_task_id,
            "is_leaf": bool(self.is_leaf),
            "depends_on": list(self.depends_on),
            "requirement_digest": self.requirement_digest,
            "constraint_digest": self.constraint_digest,
            "summary_ref": self.summary_ref,
            "superseded_by_revision": self.superseded_by_revision,
            "change_policy": self.change_policy,
            "compensation_group_id": self.compensation_group_id,
            "payload": dict(self.payload),
            "metadata": dict(self.metadata),
            "version": int(self.version),
            "attempts": int(self.attempts),
            "max_attempts": int(self.max_attempts),
            "lease_token": self.lease_token,
            "lease_expires_at": float(self.lease_expires_at),
            "claimed_by": self.claimed_by,
            "claimed_role": self.claimed_role,
            "last_error": dict(self.last_error),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskWorkItemRecord:
        payload_raw = data.get("payload")
        payload: dict[str, Any] = payload_raw if isinstance(payload_raw, dict) else {}
        metadata_raw = data.get("metadata")
        metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
        last_error_raw = data.get("last_error")
        last_error: dict[str, Any] = last_error_raw if isinstance(last_error_raw, dict) else {}
        return cls(
            task_id=str(data.get("task_id") or "").strip(),
            trace_id=str(data.get("trace_id") or "").strip(),
            run_id=str(data.get("run_id") or "").strip(),
            workspace=str(data.get("workspace") or "").strip(),
            stage=str(data.get("stage") or "").strip().lower(),
            status=str(data.get("status") or "").strip().lower(),
            priority=str(data.get("priority") or "medium").strip().lower(),
            plan_id=str(data.get("plan_id") or "").strip(),
            plan_revision_id=str(data.get("plan_revision_id") or "").strip(),
            root_task_id=str(data.get("root_task_id") or data.get("task_id") or "").strip(),
            parent_task_id=str(data.get("parent_task_id") or "").strip(),
            is_leaf=_coerce_bool(data.get("is_leaf"), default=True),
            depends_on=_normalize_string_list(data.get("depends_on")),
            requirement_digest=str(data.get("requirement_digest") or "").strip(),
            constraint_digest=str(data.get("constraint_digest") or "").strip(),
            summary_ref=str(data.get("summary_ref") or "").strip(),
            superseded_by_revision=str(data.get("superseded_by_revision") or "").strip(),
            change_policy=str(data.get("change_policy") or "strict").strip().lower() or "strict",
            compensation_group_id=str(data.get("compensation_group_id") or "").strip(),
            payload=payload,
            metadata=metadata,
            version=max(1, int(data.get("version") or 1)),
            attempts=max(0, int(data.get("attempts") or 0)),
            max_attempts=max(1, int(data.get("max_attempts") or 3)),
            lease_token=str(data.get("lease_token") or "").strip(),
            lease_expires_at=float(data.get("lease_expires_at") or 0.0),
            claimed_by=str(data.get("claimed_by") or "").strip(),
            claimed_role=str(data.get("claimed_role") or "").strip(),
            last_error=last_error,
            created_at=str(data.get("created_at") or "").strip() or now_iso(),
            updated_at=str(data.get("updated_at") or "").strip() or now_iso(),
        )
