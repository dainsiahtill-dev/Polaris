"""Stable invocation, attempt, and turn identities for role transactions.

The outer role flow owns invocation/attempt binding.  Transaction executors
consume that binding and must never manufacture an identity after effects have
started.  Persisting the typed records in ``RoleTurnRequest.metadata`` makes a
crash replay deterministic and keeps transition provenance inspectable.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from polaris.kernelone.storage import resolve_workspace_runtime_identity

if TYPE_CHECKING:
    from polaris.cells.roles.profile.public.service import RoleTurnRequest

_INVOCATION_SCHEMA = "roles.kernel.transaction_invocation_identity.v1"
_ATTEMPT_SCHEMA = "roles.kernel.transaction_attempt_identity.v1"
_INVOCATION_RECORD_KEY = "transaction_invocation_identity"
_ATTEMPT_RECORD_KEY = "transaction_attempt_identity"
_AUTHORITATIVE_EXECUTION_SCOPE_KEYS = (
    "execution_attempt_id",
    "turn_request_id",
    "execution_id",
    "task_runtime_session_id",
)


@dataclass(frozen=True, slots=True)
class _InvocationContext:
    """Canonical request provenance independent of execution-scope binding."""

    workspace: str
    workspace_token: str
    run_id: str
    task_id: str
    role_id: str


class TransactionIdentityError(RuntimeError):
    """Raised when transaction identity is absent, partial, or contradictory."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True, slots=True)
class TransactionInvocationIdentity:
    """Auditable identity for one persistent role-kernel invocation."""

    invocation_id: str
    workspace: str
    workspace_token: str
    run_id: str
    task_id: str
    role_id: str
    execution_scope_kind: str
    execution_scope_id: str
    derivation: Literal["stable_fields_sha256", "persisted_metadata"]
    schema_version: str = _INVOCATION_SCHEMA

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "invocation_id": self.invocation_id,
            "workspace": self.workspace,
            "workspace_token": self.workspace_token,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "role_id": self.role_id,
            "execution_scope_kind": self.execution_scope_kind,
            "execution_scope_id": self.execution_scope_id,
            "derivation": self.derivation,
        }

    @classmethod
    def from_record(cls, value: object) -> TransactionInvocationIdentity:
        if not isinstance(value, Mapping):
            raise TransactionIdentityError(
                "transaction invocation identity record must be a mapping",
                code="transaction_identity_invalid",
            )
        schema_version = _required_text(value, "schema_version")
        if schema_version != _INVOCATION_SCHEMA:
            raise TransactionIdentityError(
                f"unsupported invocation identity schema={schema_version!r}",
                code="transaction_identity_invalid",
            )
        derivation = _required_text(value, "derivation")
        normalized_derivation: Literal["stable_fields_sha256", "persisted_metadata"]
        if derivation == "stable_fields_sha256":
            normalized_derivation = "stable_fields_sha256"
        elif derivation == "persisted_metadata":
            normalized_derivation = "persisted_metadata"
        else:
            raise TransactionIdentityError(
                f"unsupported invocation identity derivation={derivation!r}",
                code="transaction_identity_invalid",
            )
        return cls(
            invocation_id=_require_identity_token(
                _required_text(value, "invocation_id"),
                field="transaction_invocation_id",
            ),
            workspace=_required_text(value, "workspace"),
            workspace_token=_required_text(value, "workspace_token"),
            run_id=_required_text(value, "run_id"),
            task_id=str(value.get("task_id") or "").strip(),
            role_id=_required_text(value, "role_id"),
            execution_scope_kind=_required_text(value, "execution_scope_kind"),
            execution_scope_id=_required_text(value, "execution_scope_id"),
            derivation=normalized_derivation,
            schema_version=schema_version,
        )


@dataclass(frozen=True, slots=True)
class TransactionAttemptIdentity:
    """Authoritative transition identity for one invocation attempt."""

    invocation_id: str
    attempt: int
    transition_id: str
    schema_version: str = _ATTEMPT_SCHEMA

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "invocation_id": self.invocation_id,
            "attempt": self.attempt,
            "transition_id": self.transition_id,
        }

    @classmethod
    def from_record(cls, value: object) -> TransactionAttemptIdentity:
        if not isinstance(value, Mapping):
            raise TransactionIdentityError(
                "transaction attempt identity record must be a mapping",
                code="transaction_identity_invalid",
            )
        schema_version = _required_text(value, "schema_version")
        if schema_version != _ATTEMPT_SCHEMA:
            raise TransactionIdentityError(
                f"unsupported attempt identity schema={schema_version!r}",
                code="transaction_identity_invalid",
            )
        invocation_id = _require_identity_token(
            _required_text(value, "invocation_id"),
            field="transaction_invocation_id",
        )
        attempt = _require_attempt(value.get("attempt"))
        transition_id = _require_identity_token(
            _required_text(value, "transition_id"),
            field="transaction_attempt_id",
        )
        expected_transition_id = _attempt_transition_id(invocation_id, attempt)
        if transition_id != expected_transition_id:
            raise TransactionIdentityError(
                "attempt record transition does not match invocation and attempt "
                f"expected={expected_transition_id!r} actual={transition_id!r}",
                code="transaction_identity_mismatch",
            )
        return cls(
            invocation_id=invocation_id,
            attempt=attempt,
            transition_id=transition_id,
            schema_version=schema_version,
        )


def _turn_id_component(value: Any) -> str:
    raw = str(value or "").strip()
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in raw)[:120]


def _start_transaction_invocation(
    request: RoleTurnRequest,
    *,
    role: str,
    workspace: str,
) -> str:
    """Bind or restore one deterministic persistent invocation identity.

    A new identity is derived from stable workspace/run/task/role and the
    strongest available upstream execution scope.  An already persisted
    identity is reused after validating that its provenance still describes
    the request.  No random fallback exists at this boundary.
    """

    metadata = _metadata_copy(request)
    persisted_id_raw = str(metadata.get("transaction_invocation_id") or "").strip()
    persisted_record_raw = metadata.get(_INVOCATION_RECORD_KEY)
    if persisted_record_raw is not None and not persisted_id_raw:
        raise TransactionIdentityError(
            "invocation identity record exists without transaction_invocation_id",
            code="transaction_identity_partial",
        )

    if persisted_id_raw:
        persisted_id = _require_identity_token(
            persisted_id_raw,
            field="transaction_invocation_id",
        )
        if persisted_record_raw is None:
            current = _derive_invocation_identity(
                request,
                metadata=metadata,
                role=role,
                workspace=workspace,
            )
            identity = replace(
                current,
                invocation_id=persisted_id,
                derivation="persisted_metadata",
            )
        else:
            identity = TransactionInvocationIdentity.from_record(persisted_record_raw)
            if identity.invocation_id != persisted_id:
                raise TransactionIdentityError(
                    "transaction_invocation_id does not match its typed identity record",
                    code="transaction_identity_mismatch",
                )
            _assert_persisted_invocation_matches_request(
                identity,
                request=request,
                metadata=metadata,
                role=role,
                workspace=workspace,
            )
    else:
        identity = _derive_invocation_identity(
            request,
            metadata=metadata,
            role=role,
            workspace=workspace,
        )

    _validate_existing_attempt_binding(metadata, invocation_id=identity.invocation_id)
    metadata["transaction_invocation_id"] = identity.invocation_id
    metadata[_INVOCATION_RECORD_KEY] = identity.to_record()
    request.metadata = metadata
    return identity.invocation_id


def _bind_transaction_attempt(
    request: RoleTurnRequest,
    *,
    invocation_id: str,
    attempt: int,
) -> str:
    """Bind one deterministic attempt and return its transition identity."""

    normalized_invocation = _require_identity_token(
        invocation_id,
        field="transaction_invocation_id",
    )
    normalized_attempt = _require_attempt(attempt)
    metadata = _metadata_copy(request)
    persisted_invocation = str(metadata.get("transaction_invocation_id") or "").strip()
    if not persisted_invocation:
        raise TransactionIdentityError(
            "transaction invocation must be started before binding an attempt",
            code="transaction_identity_unbound",
        )
    if persisted_invocation != normalized_invocation:
        raise TransactionIdentityError(
            "attempt invocation does not match request invocation "
            f"request={persisted_invocation!r} supplied={normalized_invocation!r}",
            code="transaction_identity_mismatch",
        )
    if metadata.get(_INVOCATION_RECORD_KEY) is None:
        raise TransactionIdentityError(
            "typed transaction invocation identity is required before attempt binding",
            code="transaction_identity_unbound",
        )
    invocation_identity = TransactionInvocationIdentity.from_record(metadata[_INVOCATION_RECORD_KEY])
    if invocation_identity.invocation_id != normalized_invocation:
        raise TransactionIdentityError(
            "typed invocation identity does not match request invocation",
            code="transaction_identity_mismatch",
        )

    _validate_existing_attempt_binding(metadata, invocation_id=normalized_invocation)
    transition_id = _attempt_transition_id(normalized_invocation, normalized_attempt)
    compatibility_transition = str(metadata.get("turn_transition_id") or "").strip()
    if compatibility_transition and compatibility_transition != transition_id:
        raise TransactionIdentityError(
            "legacy turn_transition_id disagrees with the bound attempt transition "
            f"attempt={transition_id!r} compatibility={compatibility_transition!r}",
            code="transaction_identity_mismatch",
        )

    identity = TransactionAttemptIdentity(
        invocation_id=normalized_invocation,
        attempt=normalized_attempt,
        transition_id=transition_id,
    )
    metadata["transaction_attempt"] = normalized_attempt
    metadata["transaction_attempt_id"] = transition_id
    metadata[_ATTEMPT_RECORD_KEY] = identity.to_record()
    request.metadata = metadata
    return transition_id


def _require_bound_transaction_attempt(
    request: RoleTurnRequest,
    *,
    role: str | None = None,
    workspace: str | None = None,
) -> TransactionAttemptIdentity:
    """Validate and return the attempt identity before any transaction effect."""

    metadata = _metadata_copy(request)
    invocation_id = str(metadata.get("transaction_invocation_id") or "").strip()
    if not invocation_id:
        raise TransactionIdentityError(
            "transaction_invocation_id is required before transaction execution",
            code="transaction_identity_unbound",
        )
    invocation_id = _require_identity_token(invocation_id, field="transaction_invocation_id")
    attempt_identity = _validate_existing_attempt_binding(
        metadata,
        invocation_id=invocation_id,
        require_bound=True,
    )
    if attempt_identity is None:
        raise TransactionIdentityError(
            "transaction attempt identity is required before transaction execution",
            code="transaction_identity_unbound",
        )

    invocation_record = metadata.get(_INVOCATION_RECORD_KEY)
    if invocation_record is None:
        if role is None or workspace is None:
            raise TransactionIdentityError(
                "typed invocation identity is required for transaction execution",
                code="transaction_identity_unbound",
            )
        migrated = _derive_invocation_identity(
            request,
            metadata=metadata,
            role=role,
            workspace=workspace,
        )
        migrated = replace(
            migrated,
            invocation_id=invocation_id,
            derivation="persisted_metadata",
        )
        metadata[_INVOCATION_RECORD_KEY] = migrated.to_record()
        metadata[_ATTEMPT_RECORD_KEY] = attempt_identity.to_record()
        request.metadata = metadata
        invocation_identity = migrated
    else:
        invocation_identity = TransactionInvocationIdentity.from_record(invocation_record)
        if invocation_identity.invocation_id != invocation_id:
            raise TransactionIdentityError(
                "typed invocation identity does not match transaction_invocation_id",
                code="transaction_identity_mismatch",
            )
        if role is not None and workspace is not None:
            _assert_persisted_invocation_matches_request(
                invocation_identity,
                request=request,
                metadata=metadata,
                role=role,
                workspace=workspace,
            )

    if metadata.get(_ATTEMPT_RECORD_KEY) is None:
        metadata[_ATTEMPT_RECORD_KEY] = attempt_identity.to_record()
        request.metadata = metadata

    return attempt_identity


def _resolve_transaction_turn_id(request: RoleTurnRequest, observer_run_id: str) -> str:
    attempt_identity = _require_bound_transaction_attempt(request)
    base = _turn_id_component(getattr(request, "run_id", None) or observer_run_id)
    if not base:
        raise TransactionIdentityError(
            "run_id is required to resolve a transaction turn id",
            code="transaction_identity_unbound",
        )
    task_id = _turn_id_component(getattr(request, "task_id", None))
    if not task_id:
        metadata = _metadata_copy(request)
        task_id = _turn_id_component(metadata.get("task_id") or metadata.get("pm_task_id"))
    logical_turn_id = f"{base}--{task_id}" if task_id and task_id not in base else base
    return f"{logical_turn_id}--attempt-{attempt_identity.transition_id}"


def _derive_invocation_identity(
    request: RoleTurnRequest,
    *,
    metadata: Mapping[str, Any],
    role: str,
    workspace: str,
) -> TransactionInvocationIdentity:
    context = _resolve_invocation_context(
        request,
        metadata=metadata,
        role=role,
        workspace=workspace,
    )
    scope = _resolve_execution_scope(metadata, required=True)
    if scope is None:  # Defensive narrowing; required=True always raises.
        raise TransactionIdentityError(
            "execution identity resolution returned no identity",
            code="transaction_identity_unbound",
        )
    scope_kind, scope_id = scope
    invocation_id = _stable_invocation_id(
        workspace=context.workspace,
        workspace_token=context.workspace_token,
        run_id=context.run_id,
        task_id=context.task_id,
        role_id=context.role_id,
        execution_scope_kind=scope_kind,
        execution_scope_id=scope_id,
    )
    return TransactionInvocationIdentity(
        invocation_id=invocation_id,
        workspace=context.workspace,
        workspace_token=context.workspace_token,
        run_id=context.run_id,
        task_id=context.task_id,
        role_id=context.role_id,
        execution_scope_kind=scope_kind,
        execution_scope_id=scope_id,
        derivation="stable_fields_sha256",
    )


def _resolve_invocation_context(
    request: RoleTurnRequest,
    *,
    metadata: Mapping[str, Any],
    role: str,
    workspace: str,
) -> _InvocationContext:
    role_id = str(role or "").strip().lower()
    if not role_id:
        raise TransactionIdentityError(
            "role is required to derive transaction invocation identity",
            code="transaction_identity_unbound",
        )
    run_id = str(getattr(request, "run_id", None) or "").strip()
    if not run_id:
        raise TransactionIdentityError(
            "a stable upstream run_id is required; random transaction identity fallback is forbidden",
            code="transaction_identity_unbound",
        )
    task_id = str(getattr(request, "task_id", None) or "").strip()
    if not task_id:
        task_id = str(metadata.get("task_id") or metadata.get("pm_task_id") or "").strip()

    request_workspace = str(getattr(request, "workspace", "") or "").strip()
    owner_workspace = str(workspace or "").strip()
    workspace_value = request_workspace or owner_workspace
    if not workspace_value:
        raise TransactionIdentityError(
            "workspace is required to derive transaction invocation identity",
            code="transaction_identity_unbound",
        )
    storage_identity = resolve_workspace_runtime_identity(workspace_value)
    if request_workspace and owner_workspace:
        owner_identity = resolve_workspace_runtime_identity(owner_workspace)
        if owner_identity.workspace_abs != storage_identity.workspace_abs:
            raise TransactionIdentityError(
                "request workspace differs from the role-kernel workspace "
                f"request={storage_identity.workspace_abs!r} owner={owner_identity.workspace_abs!r}",
                code="transaction_identity_mismatch",
            )
    return _InvocationContext(
        workspace=storage_identity.workspace_abs,
        workspace_token=storage_identity.token,
        run_id=run_id,
        task_id=task_id,
        role_id=role_id,
    )


def _resolve_execution_scope(
    metadata: Mapping[str, Any],
    *,
    required: bool,
) -> tuple[str, str] | None:
    candidates: list[tuple[str, str]] = []
    for key in _AUTHORITATIVE_EXECUTION_SCOPE_KEYS:
        token = str(metadata.get(key) or "").strip()
        if token:
            candidates.append((key, token))
    runtime_execution = metadata.get("runtime_execution")
    if isinstance(runtime_execution, Mapping):
        for key in (*_AUTHORITATIVE_EXECUTION_SCOPE_KEYS, "session_id"):
            token = str(runtime_execution.get(key) or "").strip()
            if token:
                canonical_key = "task_runtime_session_id" if key == "session_id" else key
                candidates.append((canonical_key, token))

    if not candidates:
        if not required:
            return None
        expected = ", ".join(_AUTHORITATIVE_EXECUTION_SCOPE_KEYS)
        raise TransactionIdentityError(
            "a first-class stable execution identity is required to start a transaction invocation; "
            f"expected one of {expected}; run/task/session fallback is forbidden",
            code="transaction_identity_unbound",
        )

    distinct_ids = {scope_id for _, scope_id in candidates}
    if len(distinct_ids) != 1:
        fields = ",".join(scope_kind for scope_kind, _ in candidates)
        raise TransactionIdentityError(
            f"multiple execution identity fields disagree fields={fields}",
            code="transaction_identity_mismatch",
        )

    scope_kind, scope_id = candidates[0]
    compatibility_session = str(metadata.get("session_id") or "").strip()
    if "task_runtime_session_id" in scope_kind and compatibility_session and compatibility_session != scope_id:
        raise TransactionIdentityError(
            "session_id disagrees with authoritative task runtime execution identity",
            code="transaction_identity_mismatch",
        )
    return scope_kind, scope_id


def _validate_existing_attempt_binding(
    metadata: Mapping[str, Any],
    *,
    invocation_id: str,
    require_bound: bool = False,
) -> TransactionAttemptIdentity | None:
    has_attempt = "transaction_attempt" in metadata
    has_attempt_id = bool(str(metadata.get("transaction_attempt_id") or "").strip())
    has_attempt_record = metadata.get(_ATTEMPT_RECORD_KEY) is not None
    has_compatibility_transition = bool(str(metadata.get("turn_transition_id") or "").strip())
    if not any((has_attempt, has_attempt_id, has_attempt_record, has_compatibility_transition)):
        if require_bound:
            raise TransactionIdentityError(
                "transaction attempt fields are not bound",
                code="transaction_identity_unbound",
            )
        return None
    if not has_attempt or not has_attempt_id:
        raise TransactionIdentityError(
            "transaction attempt binding is partial; attempt and attempt_id are both required",
            code="transaction_identity_partial",
        )

    attempt = _require_attempt(metadata.get("transaction_attempt"))
    transition_id = _require_identity_token(
        str(metadata.get("transaction_attempt_id") or "").strip(),
        field="transaction_attempt_id",
    )
    expected_transition_id = _attempt_transition_id(invocation_id, attempt)
    if transition_id != expected_transition_id:
        raise TransactionIdentityError(
            "transaction_attempt_id does not match invocation and attempt "
            f"expected={expected_transition_id!r} actual={transition_id!r}",
            code="transaction_identity_mismatch",
        )
    compatibility_transition = str(metadata.get("turn_transition_id") or "").strip()
    if compatibility_transition and compatibility_transition != transition_id:
        raise TransactionIdentityError(
            "turn_transition_id cannot override the bound transaction attempt "
            f"attempt={transition_id!r} compatibility={compatibility_transition!r}",
            code="transaction_identity_mismatch",
        )

    identity = TransactionAttemptIdentity(
        invocation_id=invocation_id,
        attempt=attempt,
        transition_id=transition_id,
    )
    if has_attempt_record:
        persisted = TransactionAttemptIdentity.from_record(metadata[_ATTEMPT_RECORD_KEY])
        if persisted != identity:
            raise TransactionIdentityError(
                "transaction attempt fields do not match their typed identity record",
                code="transaction_identity_mismatch",
            )
    return identity


def _assert_persisted_invocation_matches_request(
    persisted: TransactionInvocationIdentity,
    *,
    request: RoleTurnRequest,
    metadata: Mapping[str, Any],
    role: str,
    workspace: str,
) -> None:
    context = _resolve_invocation_context(
        request,
        metadata=metadata,
        role=role,
        workspace=workspace,
    )
    expected_fields = {
        "workspace": context.workspace,
        "workspace_token": context.workspace_token,
        "run_id": context.run_id,
        "task_id": context.task_id,
        "role_id": context.role_id,
    }
    drift = [field for field, expected in expected_fields.items() if getattr(persisted, field) != expected]
    current_scope = _resolve_execution_scope(metadata, required=False)
    if current_scope is not None:
        scope_kind, scope_id = current_scope
        if _canonical_execution_scope_kind(persisted.execution_scope_kind) != scope_kind:
            drift.append("execution_scope_kind")
        if persisted.execution_scope_id != scope_id:
            drift.append("execution_scope_id")
    if persisted.derivation == "stable_fields_sha256":
        expected_invocation_id = _stable_invocation_id(
            workspace=persisted.workspace,
            workspace_token=persisted.workspace_token,
            run_id=persisted.run_id,
            task_id=persisted.task_id,
            role_id=persisted.role_id,
            execution_scope_kind=persisted.execution_scope_kind,
            execution_scope_id=persisted.execution_scope_id,
        )
        if persisted.invocation_id != expected_invocation_id:
            drift.append("invocation_id")
    if drift:
        raise TransactionIdentityError(
            f"persisted invocation provenance differs from the current request fields={','.join(drift)}",
            code="transaction_identity_mismatch",
        )


def _canonical_execution_scope_kind(value: str) -> str:
    if value == "runtime_execution.session_id":
        return "task_runtime_session_id"
    prefix = "runtime_execution."
    if value.startswith(prefix):
        nested = value.removeprefix(prefix)
        if nested in _AUTHORITATIVE_EXECUTION_SCOPE_KEYS:
            return nested
    return value


def _stable_invocation_id(
    *,
    workspace: str,
    workspace_token: str,
    run_id: str,
    task_id: str,
    role_id: str,
    execution_scope_kind: str,
    execution_scope_id: str,
) -> str:
    seed = {
        "schema_version": _INVOCATION_SCHEMA,
        "workspace": workspace,
        "workspace_token": workspace_token,
        "run_id": run_id,
        "task_id": task_id,
        "role_id": role_id,
        "execution_scope_kind": execution_scope_kind,
        "execution_scope_id": execution_scope_id,
    }
    encoded = json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"txi_{hashlib.sha256(encoded).hexdigest()[:24]}"


def _metadata_copy(request: RoleTurnRequest) -> dict[str, Any]:
    metadata = getattr(request, "metadata", None)
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def _attempt_transition_id(invocation_id: str, attempt: int) -> str:
    return f"{invocation_id}-{attempt}"


def _require_attempt(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TransactionIdentityError(
            f"transaction attempt must be a non-negative integer, got {value!r}",
            code="transaction_identity_invalid",
        )
    return value


def _require_identity_token(value: str, *, field: str) -> str:
    token = str(value or "").strip()
    if not token or len(token) > 120 or _turn_id_component(token) != token:
        raise TransactionIdentityError(
            f"{field} must be a non-empty canonical token",
            code="transaction_identity_invalid",
        )
    return token


def _required_text(value: Mapping[str, Any], field: str) -> str:
    token = str(value.get(field) or "").strip()
    if not token:
        raise TransactionIdentityError(
            f"{field} is required in transaction identity record",
            code="transaction_identity_invalid",
        )
    return token


__all__ = [
    "TransactionAttemptIdentity",
    "TransactionIdentityError",
    "TransactionInvocationIdentity",
    "_bind_transaction_attempt",
    "_require_bound_transaction_attempt",
    "_resolve_transaction_turn_id",
    "_start_transaction_invocation",
]
