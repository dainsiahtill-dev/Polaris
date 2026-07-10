"""Public contracts for platform run-ledger projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReadRunLedgerProjectionQueryV1:
    """Read a platform control-plane projection from run ledger evidence."""

    workspace: str
    run_id: str = ""
    factory_run_id: str = ""
    project_id: str = ""
    max_runs: int = 50
    include_migration_ledgers: bool = False

    def __post_init__(self) -> None:
        workspace = str(self.workspace or "").strip()
        if not workspace:
            raise ValueError("workspace must be a non-empty string")
        run_id = str(self.run_id or "").strip()
        factory_run_id = str(self.factory_run_id or "").strip()
        project_id = str(self.project_id or "").strip()
        if project_id and not factory_run_id:
            raise ValueError("project_id requires factory_run_id to prevent cross-run projection leakage")
        max_runs = max(1, min(500, int(self.max_runs or 50)))
        include_migration_ledgers = bool(self.include_migration_ledgers)
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "factory_run_id", factory_run_id)
        object.__setattr__(self, "project_id", project_id)
        object.__setattr__(self, "max_runs", max_runs)
        object.__setattr__(self, "include_migration_ledgers", include_migration_ledgers)


@dataclass(frozen=True)
class ReadRunLedgerProjectionBarrierQueryV1:
    """Read a run-ledger projection only after required events are visible."""

    workspace: str
    run_id: str
    min_append_id: str = ""
    min_event_hash: str = ""
    timeout_ms: int = 0
    include_migration_ledgers: bool = False

    def __post_init__(self) -> None:
        workspace = str(self.workspace or "").strip()
        if not workspace:
            raise ValueError("workspace must be a non-empty string")
        run_id = str(self.run_id or "").strip()
        if not run_id:
            raise ValueError("run_id must be a non-empty string")
        timeout_ms = max(0, min(30000, int(self.timeout_ms or 0)))
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "min_append_id", str(self.min_append_id or "").strip())
        object.__setattr__(self, "min_event_hash", str(self.min_event_hash or "").strip())
        object.__setattr__(self, "timeout_ms", timeout_ms)
        object.__setattr__(self, "include_migration_ledgers", bool(self.include_migration_ledgers))


@dataclass(frozen=True)
class ReadRunProvenanceBundleQueryV1:
    """Read the end-to-end provenance bundle for one run."""

    workspace: str
    run_id: str
    include_migration_ledgers: bool = False

    def __post_init__(self) -> None:
        workspace = str(self.workspace or "").strip()
        if not workspace:
            raise ValueError("workspace must be a non-empty string")
        run_id = str(self.run_id or "").strip()
        if not run_id:
            raise ValueError("run_id must be a non-empty string")
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "include_migration_ledgers", bool(self.include_migration_ledgers))


@dataclass(frozen=True)
class AppendRunLedgerEventCommandV1:
    """Append one immutable platform run-ledger event."""

    workspace: str
    run_id: str
    event: dict[str, Any]

    def __post_init__(self) -> None:
        workspace = str(self.workspace or "").strip()
        if not workspace:
            raise ValueError("workspace must be a non-empty string")
        if not isinstance(self.event, dict) or not self.event:
            raise ValueError("event must be a non-empty mapping")
        run_id = str(self.run_id or "").strip()
        if not run_id:
            token = self.event.get("job_token")
            token_map = token if isinstance(token, dict) else {}
            run_id = str(token_map.get("run_id") or self.event.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("run_id must be a non-empty string")
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "event", dict(self.event))


@dataclass(frozen=True)
class AppendToolCallLifecycleEventCommandV1:
    """Append one canonical tool-call lifecycle event.

    Callers own when a lifecycle receipt is emitted. The Run Ledger public
    service owns the event shape and receipt normalization so normal batches,
    dropped dispatches, and completion-path evidence cannot drift.
    """

    workspace: str
    run_id: str
    task_id: str
    turn_id: str
    role: str
    lifecycle_receipt: dict[str, Any]
    stage: str = "director_tool_dispatch"
    project_id: str = ""
    capability_audit: dict[str, Any] | None = None
    gate_policy: dict[str, Any] | None = None
    job_token: dict[str, Any] | None = None
    ok: bool | None = None

    def __post_init__(self) -> None:
        workspace = str(self.workspace or "").strip()
        if not workspace:
            raise ValueError("workspace must be a non-empty string")
        run_id = str(self.run_id or self.turn_id or "").strip()
        if not run_id:
            raise ValueError("run_id must be a non-empty string")
        if not isinstance(self.lifecycle_receipt, dict) or not self.lifecycle_receipt:
            raise ValueError("lifecycle_receipt must be a non-empty mapping")
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "task_id", str(self.task_id or "").strip())
        object.__setattr__(self, "turn_id", str(self.turn_id or "").strip())
        object.__setattr__(self, "role", str(self.role or "").strip())
        object.__setattr__(self, "lifecycle_receipt", dict(self.lifecycle_receipt))
        object.__setattr__(self, "stage", str(self.stage or "director_tool_dispatch").strip())
        object.__setattr__(self, "project_id", str(self.project_id or "").strip())
        object.__setattr__(
            self,
            "capability_audit",
            dict(self.capability_audit) if isinstance(self.capability_audit, dict) else None,
        )
        object.__setattr__(
            self,
            "gate_policy",
            dict(self.gate_policy) if isinstance(self.gate_policy, dict) else None,
        )
        object.__setattr__(
            self,
            "job_token",
            dict(self.job_token) if isinstance(self.job_token, dict) else None,
        )


@dataclass(frozen=True)
class RunLedgerProjectionResultV1:
    """Platform read model returned by the control-plane ledger service."""

    projection: dict[str, Any]


@dataclass(frozen=True)
class RunLedgerProjectionBarrierResultV1:
    """Projection read model plus barrier-satisfaction metadata."""

    projection: dict[str, Any]
    barrier: dict[str, Any]


@dataclass(frozen=True)
class RunProvenanceBundleResultV1:
    """Run provenance bundle returned by the control-plane ledger service."""

    bundle: dict[str, Any]


@dataclass(frozen=True)
class RunLedgerAppendResultV1:
    """Append receipt returned by the platform run ledger."""

    receipt: dict[str, Any]


class ControlPlaneRunLedgerV1Error(Exception):
    """Raised when the control-plane run ledger cannot be projected."""


__all__ = [
    "AppendRunLedgerEventCommandV1",
    "AppendToolCallLifecycleEventCommandV1",
    "ControlPlaneRunLedgerV1Error",
    "ReadRunLedgerProjectionBarrierQueryV1",
    "ReadRunLedgerProjectionQueryV1",
    "ReadRunProvenanceBundleQueryV1",
    "RunLedgerAppendResultV1",
    "RunLedgerProjectionBarrierResultV1",
    "RunLedgerProjectionResultV1",
    "RunProvenanceBundleResultV1",
]
