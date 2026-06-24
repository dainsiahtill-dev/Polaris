"""Public contracts for platform run-ledger projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReadRunLedgerProjectionQueryV1:
    """Read a platform control-plane projection from run ledger evidence."""

    workspace: str
    run_id: str = ""
    max_runs: int = 50
    include_compat_ledgers: bool = False

    def __post_init__(self) -> None:
        workspace = str(self.workspace or "").strip()
        if not workspace:
            raise ValueError("workspace must be a non-empty string")
        run_id = str(self.run_id or "").strip()
        max_runs = max(1, min(500, int(self.max_runs or 50)))
        include_compat_ledgers = bool(self.include_compat_ledgers)
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "max_runs", max_runs)
        object.__setattr__(self, "include_compat_ledgers", include_compat_ledgers)


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
class RunLedgerProjectionResultV1:
    """Platform read model returned by the control-plane ledger service."""

    projection: dict[str, Any]


@dataclass(frozen=True)
class RunLedgerAppendResultV1:
    """Append receipt returned by the platform run ledger."""

    receipt: dict[str, Any]


class ControlPlaneRunLedgerV1Error(Exception):
    """Raised when the control-plane run ledger cannot be projected."""


__all__ = [
    "AppendRunLedgerEventCommandV1",
    "ControlPlaneRunLedgerV1Error",
    "ReadRunLedgerProjectionQueryV1",
    "RunLedgerAppendResultV1",
    "RunLedgerProjectionResultV1",
]
