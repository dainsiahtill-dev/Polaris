"""Durable project-completion convergence coordinator contracts.

``workflow_runtime`` owns only cursor persistence and transition CAS.
It consumes the owner-sealed outcome from ``runtime.projection`` and the
owner-sealed residual set from ``factory.verification_guard``.  No caller may
submit a completion status or evidence bundle through this surface.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import InitVar, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from polaris.cells.factory.verification_guard.public.contracts import (
    ProjectCompletionDiagnosticsV1,
    ProjectCompletionDiagnosticV1,
)
from polaris.cells.orchestration.workflow_runtime.public.model_ceiling import (
    ModelCeilingTerminalResultV1,
)
from polaris.cells.runtime.projection.public.contracts import ProjectOutcomeAuthorityBindingV1

_LOWER_HEX = frozenset("0123456789abcdef")
_PROJECT_COMPLETION_RESULT_AUTHORITY_TOKEN = object()
_ACTION_KINDS = frozenset(
    {
        "publish_owner_rework",
        "refresh_owner_evidence",
        "run_deterministic_repair",
        "run_required_verifier",
        "wait_for_dependencies",
    }
)
_ACTION_RECEIPT_STATUSES = frozenset({"accepted", "already_applied"})
_TERMINAL_STATUSES = frozenset({"completed_verified", "model_ceiling"})


def _exact_text(name: str, value: object, *, max_length: int = 512) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be an exact string")
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise ValueError(f"{name} must be 1..{max_length} characters")
    if any(unicodedata.category(char).startswith("C") for char in normalized):
        raise ValueError(f"{name} must not contain control characters")
    return normalized


def _workspace(value: object) -> str:
    return str(Path(_exact_text("workspace", value, max_length=4096)).expanduser().resolve())


def _sha256(name: str, value: object) -> str:
    token = _exact_text(name, value, max_length=64)
    if len(token) != 64 or any(char not in _LOWER_HEX for char in token):
        raise ValueError(f"{name} must be a 64-character lowercase hex value")
    return token


def _exact_tuple(name: str, value: object, *, allow_empty: bool = True) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{name} must be an exact tuple")
    normalized = tuple(_exact_text(f"{name}[{index}]", item) for index, item in enumerate(value))
    if not allow_empty and not normalized:
        raise ValueError(f"{name} must not be empty")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{name} must not contain duplicates")
    return normalized


def _bounded_int(name: str, value: object, *, minimum: int = 1, maximum: int = 3600) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an exact integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be in {minimum}..{maximum}")
    return value


def _canonical_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _iso8601_utc(name: str, value: object) -> str:
    token = _exact_text(name, value, max_length=64)
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return token


@dataclass(frozen=True, slots=True)
class ProjectCompletionIdentityV1:
    """Exact project/run/contract identity for one convergence workflow."""

    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str
    schema_version: str = field(
        default="orchestration.workflow_orchestration.project_completion_identity.v1", init=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _workspace(self.workspace))
        object.__setattr__(self, "project_id", _exact_text("project_id", self.project_id))
        object.__setattr__(self, "run_id", _exact_text("run_id", self.run_id))
        object.__setattr__(
            self,
            "completion_contract_hash",
            _sha256("completion_contract_hash", self.completion_contract_hash),
        )

    def as_payload(self) -> dict[str, str]:
        """Return canonical JSON-safe identity fields."""

        return {
            "workspace": self.workspace,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "completion_contract_hash": self.completion_contract_hash,
        }


@dataclass(frozen=True, slots=True)
class AdvanceProjectCompletionCommandV1:
    """Advance one exact convergence workflow by at most one external effect."""

    identity: ProjectCompletionIdentityV1
    max_actions: int = 8
    max_dispatch_attempts: int = 3
    max_no_progress_observations: int = 3
    dispatch_lease_seconds: int = 120
    schema_version: str = field(
        default="orchestration.workflow_orchestration.advance_project_completion.v1", init=False
    )

    def __post_init__(self) -> None:
        if type(self.identity) is not ProjectCompletionIdentityV1:
            raise TypeError("identity must be an exact ProjectCompletionIdentityV1")
        for name in (
            "max_actions",
            "max_dispatch_attempts",
            "max_no_progress_observations",
            "dispatch_lease_seconds",
        ):
            object.__setattr__(self, name, _bounded_int(name, getattr(self, name)))


@dataclass(frozen=True, slots=True)
class ProjectCompletionActionCommandV1:
    """One owner action with a stable production idempotency/handoff key."""

    identity: ProjectCompletionIdentityV1
    action_id: str
    diagnostic_id: str
    obligation_id: str
    owner_task_id: str
    action_kind: str
    owner_snapshot_hash: str
    owner_bundle_hash: str
    diagnostic: ProjectCompletionDiagnosticV1
    schema_version: str = field(default="orchestration.workflow_orchestration.project_completion_action.v1", init=False)
    handoff_id: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.identity) is not ProjectCompletionIdentityV1:
            raise TypeError("identity must be an exact ProjectCompletionIdentityV1")
        if type(self.diagnostic) is not ProjectCompletionDiagnosticV1:
            raise TypeError("diagnostic must be an exact ProjectCompletionDiagnosticV1")
        action_id = _sha256("action_id", self.action_id)
        object.__setattr__(self, "action_id", action_id)
        object.__setattr__(self, "handoff_id", action_id)
        for name in ("diagnostic_id", "obligation_id", "owner_task_id"):
            object.__setattr__(self, name, _exact_text(name, getattr(self, name), max_length=256))
        action_kind = _exact_text("action_kind", self.action_kind, max_length=128)
        if action_kind not in _ACTION_KINDS:
            raise ValueError(f"unsupported action_kind: {action_kind}")
        object.__setattr__(self, "action_kind", action_kind)
        object.__setattr__(self, "owner_snapshot_hash", _sha256("owner_snapshot_hash", self.owner_snapshot_hash))
        object.__setattr__(self, "owner_bundle_hash", _sha256("owner_bundle_hash", self.owner_bundle_hash))
        if (
            self.diagnostic.diagnostic_id != self.diagnostic_id
            or self.diagnostic.obligation_id != self.obligation_id
            or self.diagnostic.owner_task_id != self.owner_task_id
            or self.diagnostic.allowed_next_action != self.action_kind
        ):
            raise ValueError("action identity must match the complete owner diagnostic")


@dataclass(frozen=True, slots=True)
class ProjectCompletionDispatchClaimV1:
    """CAS-won dispatch lease persisted before an action leaves workflow runtime."""

    identity: ProjectCompletionIdentityV1
    action_id: str
    claim_id: str
    attempt_ordinal: int
    lease_expires_at: str
    schema_version: str = field(
        default="orchestration.workflow_orchestration.project_completion_dispatch_claim.v1", init=False
    )

    def __post_init__(self) -> None:
        if type(self.identity) is not ProjectCompletionIdentityV1:
            raise TypeError("identity must be an exact ProjectCompletionIdentityV1")
        object.__setattr__(self, "action_id", _sha256("action_id", self.action_id))
        object.__setattr__(self, "claim_id", _sha256("claim_id", self.claim_id))
        object.__setattr__(self, "attempt_ordinal", _bounded_int("attempt_ordinal", self.attempt_ordinal))
        object.__setattr__(self, "lease_expires_at", _iso8601_utc("lease_expires_at", self.lease_expires_at))


@dataclass(frozen=True, slots=True)
class ProjectCompletionActionReceiptV1:
    """Owner receipt proving one action-id-bound handoff was durably accepted."""

    identity: ProjectCompletionIdentityV1
    action_id: str
    handoff_id: str
    diagnostic_id: str
    owner_task_id: str
    status: str
    lease_id: str
    settlement_id: str
    effect_hash: str
    receipt_hash: str
    schema_version: str = field(
        default="orchestration.workflow_orchestration.project_completion_action_receipt.v1", init=False
    )

    def __post_init__(self) -> None:
        if type(self.identity) is not ProjectCompletionIdentityV1:
            raise TypeError("identity must be an exact ProjectCompletionIdentityV1")
        action_id = _sha256("action_id", self.action_id)
        object.__setattr__(self, "action_id", action_id)
        handoff_id = _sha256("handoff_id", self.handoff_id)
        if handoff_id != action_id:
            raise ValueError("handoff_id must equal action_id")
        object.__setattr__(self, "handoff_id", handoff_id)
        object.__setattr__(self, "diagnostic_id", _exact_text("diagnostic_id", self.diagnostic_id))
        object.__setattr__(self, "owner_task_id", _exact_text("owner_task_id", self.owner_task_id))
        status = _exact_text("status", self.status, max_length=64)
        if status not in _ACTION_RECEIPT_STATUSES:
            raise ValueError(f"unsupported action receipt status: {status}")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "lease_id", _exact_text("lease_id", self.lease_id, max_length=256))
        object.__setattr__(self, "settlement_id", _exact_text("settlement_id", self.settlement_id, max_length=256))
        object.__setattr__(self, "effect_hash", _sha256("effect_hash", self.effect_hash))
        object.__setattr__(self, "receipt_hash", _sha256("receipt_hash", self.receipt_hash))


def project_completion_action_receipt_hash(
    *,
    identity: ProjectCompletionIdentityV1,
    action_id: str,
    handoff_id: str,
    diagnostic_id: str,
    owner_task_id: str,
    status: str,
    lease_id: str,
    settlement_id: str,
    effect_hash: str,
) -> str:
    """Return the canonical digest an authoritative action owner must seal."""

    return _canonical_hash(
        {
            "identity": identity.as_payload(),
            "action_id": action_id,
            "handoff_id": handoff_id,
            "diagnostic_id": diagnostic_id,
            "owner_task_id": owner_task_id,
            "status": status,
            "lease_id": lease_id,
            "settlement_id": settlement_id,
            "effect_hash": effect_hash,
        }
    )


@runtime_checkable
class ProjectCompletionOutcomePortV1(Protocol):
    """Read the sole owner-sealed runtime.projection outcome."""

    async def query_project_completion_outcome(
        self,
        identity: ProjectCompletionIdentityV1,
    ) -> ProjectOutcomeAuthorityBindingV1: ...


@runtime_checkable
class ProjectCompletionDiagnosticsPortV1(Protocol):
    """Read the owner-sealed VerificationGuard residual set."""

    async def query_project_completion_diagnostics(
        self,
        identity: ProjectCompletionIdentityV1,
    ) -> ProjectCompletionDiagnosticsV1: ...


@runtime_checkable
class ProjectCompletionModelCeilingPortV1(Protocol):
    """Read workflow_runtime's sealed terminal decision for one diagnostic.

    The convergence coordinator supplies identity locators only.  A mapping,
    raw status, or locally constructed lookalike is never authority.
    """

    async def query_project_completion_model_ceiling(
        self,
        identity: ProjectCompletionIdentityV1,
        diagnostic_id: str,
    ) -> ModelCeilingTerminalResultV1 | None: ...


@runtime_checkable
class ProjectCompletionActionPortV1(Protocol):
    """Durable idempotent handoff port.

    Implementations MUST bind the production handoff/idempotency key to
    ``command.action_id``.  Lookup is required before every dispatch/replay;
    dispatching an already-applied action must return the same owner receipt.
    """

    async def query_project_completion_action_receipt(
        self,
        command: ProjectCompletionActionCommandV1,
    ) -> ProjectCompletionActionReceiptV1 | None: ...

    async def dispatch_project_completion_action(
        self,
        command: ProjectCompletionActionCommandV1,
        claim: ProjectCompletionDispatchClaimV1,
    ) -> ProjectCompletionActionReceiptV1: ...


@dataclass(frozen=True, slots=True)
class ProjectCompletionAdvanceResultV1:
    """Sealed convergence result; only workflow_orchestration may construct it."""

    identity: ProjectCompletionIdentityV1
    workflow_id: str
    status: str
    reason_codes: tuple[str, ...]
    event_seq: int
    diagnostic_id: str | None = None
    action_id: str | None = None
    owner_snapshot_hash: str | None = None
    next_action: str | None = None
    _authority_token: InitVar[object | None] = None
    schema_version: str = field(
        default="orchestration.workflow_orchestration.project_completion_advance_result.v1", init=False
    )
    terminal: bool = field(init=False)

    def __post_init__(self, _authority_token: object | None) -> None:
        if _authority_token is not _PROJECT_COMPLETION_RESULT_AUTHORITY_TOKEN:
            raise TypeError("ProjectCompletionAdvanceResultV1 is sealed to workflow_orchestration authority")
        if type(self.identity) is not ProjectCompletionIdentityV1:
            raise TypeError("identity must be an exact ProjectCompletionIdentityV1")
        object.__setattr__(self, "workflow_id", _exact_text("workflow_id", self.workflow_id, max_length=256))
        status = _exact_text("status", self.status, max_length=64)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "reason_codes", _exact_tuple("reason_codes", self.reason_codes))
        if type(self.event_seq) is not int or isinstance(self.event_seq, bool) or self.event_seq < 0:
            raise ValueError("event_seq must be a non-negative exact integer")
        for name in ("diagnostic_id", "action_id", "owner_snapshot_hash"):
            value = getattr(self, name)
            if value is not None:
                normalized = _sha256(name, value) if name != "diagnostic_id" else _exact_text(name, value)
                object.__setattr__(self, name, normalized)
        if self.next_action is not None:
            next_action = _exact_text("next_action", self.next_action, max_length=128)
            if next_action not in _ACTION_KINDS:
                raise ValueError(f"unsupported next_action: {next_action}")
            object.__setattr__(self, "next_action", next_action)
        object.__setattr__(self, "terminal", status in _TERMINAL_STATUSES)


async def advance_project_completion(
    command: AdvanceProjectCompletionCommandV1,
) -> ProjectCompletionAdvanceResultV1:
    """Advance through the bootstrap-bound workflow-runtime authority."""

    if type(command) is not AdvanceProjectCompletionCommandV1:
        raise TypeError("command must be an exact AdvanceProjectCompletionCommandV1")
    from polaris.cells.orchestration.workflow_orchestration.internal.project_completion_convergence import (
        advance_project_completion_authoritatively,
    )

    return await advance_project_completion_authoritatively(command)


async def notify_project_completion(
    command: AdvanceProjectCompletionCommandV1,
) -> None:
    """Submit an exact identity to the event-driven production supervisor."""

    if type(command) is not AdvanceProjectCompletionCommandV1:
        raise TypeError("command must be an exact AdvanceProjectCompletionCommandV1")
    from polaris.cells.orchestration.workflow_orchestration.internal.project_completion_supervisor import (
        submit_project_completion_command,
    )

    await submit_project_completion_command(command)


__all__ = [
    "AdvanceProjectCompletionCommandV1",
    "ProjectCompletionActionCommandV1",
    "ProjectCompletionActionPortV1",
    "ProjectCompletionActionReceiptV1",
    "ProjectCompletionAdvanceResultV1",
    "ProjectCompletionDiagnosticsPortV1",
    "ProjectCompletionDispatchClaimV1",
    "ProjectCompletionIdentityV1",
    "ProjectCompletionModelCeilingPortV1",
    "ProjectCompletionOutcomePortV1",
    "advance_project_completion",
    "notify_project_completion",
    "project_completion_action_receipt_hash",
]
