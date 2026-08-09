"""Typed durable cursor port for project-completion orchestration.

This module owns persistence mechanics only.  It deliberately has no imports
from projection, VerificationGuard, TaskMarket, or the convergence coordinator.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, cast, runtime_checkable

_TRANSITION_TYPES = frozenset(
    {
        "project_completion.action_reserved.v1",
        "project_completion.dispatch_claimed.v1",
        "project_completion.action_committed.v1",
        "project_completion.action_dispatch_failed.v1",
        "project_completion.action_abandoned.v1",
        "project_completion.observation_waiting.v1",
        "project_completion.terminal.v1",
    }
)
_REQUIRED_TRANSITION_FIELDS: dict[str, dict[str, type[object]]] = {
    "project_completion.action_reserved.v1": {
        "action_id": str,
        "handoff_id": str,
        "diagnostic_id": str,
        "obligation_id": str,
        "owner_task_id": str,
        "action_kind": str,
        "owner_snapshot_hash": str,
        "owner_bundle_hash": str,
    },
    "project_completion.dispatch_claimed.v1": {
        "action_id": str,
        "claim_id": str,
        "attempt_ordinal": int,
        "lease_expires_at": str,
    },
    "project_completion.action_committed.v1": {
        "action_id": str,
        "handoff_id": str,
        "diagnostic_id": str,
        "owner_task_id": str,
        "owner_snapshot_hash": str,
        "owner_bundle_hash": str,
        "receipt_hash": str,
        "lease_id": str,
        "settlement_id": str,
        "effect_hash": str,
    },
    "project_completion.action_dispatch_failed.v1": {
        "action_id": str,
        "claim_id": str,
        "error_type": str,
    },
    "project_completion.action_abandoned.v1": {"action_id": str, "reason_code": str},
    "project_completion.observation_waiting.v1": {
        "diagnostic_id": str,
        "owner_snapshot_hash": str,
    },
    "project_completion.terminal.v1": {"status": str, "reason_codes": list},
}
_OPTIONAL_TERMINAL_FIELDS = frozenset(
    {"diagnostic_id", "action_id", "owner_binding_hash", "owner_snapshot_hash"}
)


class ProjectCompletionCursorConflictError(RuntimeError):
    """Expected cursor sequence lost a durable compare-and-swap race."""


@dataclass(frozen=True, slots=True)
class ProjectCompletionCursorIdentityV1:
    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str

    def as_payload(self) -> dict[str, str]:
        return {
            "workspace": self.workspace,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "completion_contract_hash": self.completion_contract_hash,
        }


@dataclass(frozen=True, slots=True)
class ProjectCompletionCursorLimitsV1:
    max_actions: int
    max_dispatch_attempts: int
    max_no_progress_observations: int
    dispatch_lease_seconds: int

    def as_payload(self) -> dict[str, int]:
        return {
            "max_actions": self.max_actions,
            "max_dispatch_attempts": self.max_dispatch_attempts,
            "max_no_progress_observations": self.max_no_progress_observations,
            "dispatch_lease_seconds": self.dispatch_lease_seconds,
        }


@dataclass(frozen=True, slots=True)
class ProjectCompletionCursorEventV1:
    seq: int
    event_type: str
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class ProjectCompletionCursorRegistrationV1:
    """One durable nonterminal convergence registration recoverable after restart."""

    workflow_id: str
    identity: ProjectCompletionCursorIdentityV1
    limits: ProjectCompletionCursorLimitsV1


@dataclass(frozen=True, slots=True)
class ProjectCompletionCursorTransitionV1:
    event_type: str
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.event_type not in _TRANSITION_TYPES:
            raise ValueError(f"unsupported project-completion cursor transition: {self.event_type}")
        if "identity" in self.payload:
            raise ValueError("cursor transition identity is injected by the cursor owner")
        required = _REQUIRED_TRANSITION_FIELDS[self.event_type]
        allowed = set(required)
        if self.event_type == "project_completion.terminal.v1":
            allowed.update(_OPTIONAL_TERMINAL_FIELDS)
        if set(self.payload) != allowed.intersection(self.payload) or not set(required).issubset(self.payload):
            raise ValueError("cursor transition fields do not match the exact event schema")
        for name, expected_type in required.items():
            value = self.payload[name]
            if type(value) is not expected_type:
                raise TypeError(f"cursor transition {name} must be an exact {expected_type.__name__}")
        reason_codes = self.payload.get("reason_codes")
        if reason_codes is not None and any(
            type(item) is not str for item in cast(list[object], reason_codes)
        ):
            raise TypeError("cursor terminal reason_codes must contain exact strings")
        for name in _OPTIONAL_TERMINAL_FIELDS.intersection(self.payload):
            if type(self.payload[name]) is not str:
                raise TypeError(f"cursor terminal {name} must be an exact string")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@runtime_checkable
class ProjectCompletionCursorPortV1(Protocol):
    """Private typed CAS surface consumed by workflow orchestration."""

    async def ensure_cursor(
        self,
        workflow_id: str,
        identity: ProjectCompletionCursorIdentityV1,
        limits: ProjectCompletionCursorLimitsV1,
    ) -> None: ...
    async def load_cursor(
        self,
        workflow_id: str,
        identity: ProjectCompletionCursorIdentityV1,
    ) -> tuple[ProjectCompletionCursorEventV1, ...]: ...

    async def list_resumable_cursors(
        self,
    ) -> tuple[ProjectCompletionCursorRegistrationV1, ...]: ...

    async def append_transition(
        self,
        workflow_id: str,
        identity: ProjectCompletionCursorIdentityV1,
        transition: ProjectCompletionCursorTransitionV1,
        *,
        expected_previous_seq: int,
    ) -> ProjectCompletionCursorEventV1: ...

    async def repair_execution_projection(
        self,
        workflow_id: str,
        *,
        status: str,
        result: dict[str, object],
        close_time: str | None,
    ) -> None: ...


def compose_project_completion_cursor(store: object) -> ProjectCompletionCursorPortV1:
    """Compose owner implementation while keeping its class Cell-private."""

    from polaris.cells.orchestration.workflow_runtime.internal.project_completion_cursor import (
        SqliteProjectCompletionCursorV1,
    )

    return SqliteProjectCompletionCursorV1(store)  # type: ignore[arg-type]


__all__ = [
    "ProjectCompletionCursorConflictError",
    "ProjectCompletionCursorEventV1",
    "ProjectCompletionCursorIdentityV1",
    "ProjectCompletionCursorLimitsV1",
    "ProjectCompletionCursorPortV1",
    "ProjectCompletionCursorRegistrationV1",
    "ProjectCompletionCursorTransitionV1",
    "compose_project_completion_cursor",
]
