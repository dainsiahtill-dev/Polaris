"""Runtime-private Factory semantic dispatch and B3.5 qualification sidecar."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any, AsyncContextManager, NoReturn, TypeVar, cast, final

from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FactoryRoleEvidenceAuthorityBindingV1,
    FactoryRoleFrozenSemanticRequestV1,
    validate_factory_role_frozen_semantic_evidence_policy,
)
from polaris.kernelone.events.final_request_evidence import redact_provider_transport
from polaris.kernelone.llm.engine.context_store_retention import (
    ContextSnapshotAuditPinError,
    ContextSnapshotAuditPinRepository,
)
from polaris.kernelone.llm.engine.provider_native_request import (
    FactoryProviderDispatchMode,
    project_factory_provider_native_request,
)

from .factory_role_evidence_binding import FactoryRoleEvidenceBindingV1
from .final_provider_attempt_gate import FinalProviderAttemptGate
from .final_provider_attempt_qualification import (
    FINAL_PROVIDER_ATTEMPT_QUALIFICATION_REJECTION_SCHEMA,
    FinalProviderAttemptQualificationError,
    FinalProviderAttemptQualificationRejectionV1,
    _FinalProviderAttemptQualificationProofV1,
    _mint_final_provider_attempt_qualification_proof,
    append_qualification_rejection,
    bind_final_request_context_audit_to_frozen,
    final_gate_semantic_request,
    final_request_snapshot_evidence,
    qualify_final_provider_request,
    validate_exact_wire_before_reservation,
)
from .final_request_metrics import provider_native_request_metrics

_DispatchResultT = TypeVar("_DispatchResultT")
_StreamResponseT = TypeVar("_StreamResponseT")
FACTORY_SEMANTIC_DISPATCH_NOT_ENABLED = "factory_role_semantic_request_frozen_physical_dispatch_not_enabled"
_FINAL_PHYSICAL_SNAPSHOT_PERSIST_ATTEMPTS = 3
_RETRYABLE_SNAPSHOT_PERSIST_MARKERS = (
    "lock unavailable",
    "lock_acquisition_timeout",
    "busy",
    "durable reread mismatch",
)
logger = logging.getLogger(__name__)


def _live_pairing_projection(
    authority: FactoryRoleEvidenceAuthorityBindingV1,
    binding: FactoryRoleEvidenceBindingV1,
    frozen: FactoryRoleFrozenSemanticRequestV1,
) -> tuple[object, ...]:
    """Revalidate and project every live object used by one exact pairing."""

    if type(authority) is not FactoryRoleEvidenceAuthorityBindingV1:
        raise TypeError("factory_dispatch_authority_exact_type_required")
    if type(binding) is not FactoryRoleEvidenceBindingV1:
        raise TypeError("factory_dispatch_binding_exact_type_required")
    if type(frozen) is not FactoryRoleFrozenSemanticRequestV1:
        raise TypeError("factory_dispatch_frozen_request_exact_type_required")
    try:
        FactoryRoleEvidenceAuthorityBindingV1.__post_init__(authority)
        binding_error = binding.validation_error(expected_role=authority.role)
        if binding_error:
            raise RuntimeError(f"factory_dispatch_binding_malformed:{binding_error}")
        FactoryRoleFrozenSemanticRequestV1.__post_init__(frozen)
        validate_factory_role_frozen_semantic_evidence_policy(
            frozen,
            policy_facts=binding.policy_facts,
        )
        payload = json.loads(frozen.canonical_final_payload_json)
        ack = binding.cutoff_proof.ack
        actual = (
            authority.factory_run_id,
            authority.role,
            authority.attempt_budget,
            authority.execution_authority_hash,
            id(authority.physical_attempt_control_port),
            binding.run_id,
            binding.turn_id,
            binding.call_id,
            binding.request_freeze_id,
            binding.signed_factory_binding_ref,
            binding.signed_factory_binding_hash,
            ack.semantic_candidate_hash,
        )
        expected = (
            binding.factory_run_id,
            payload["role"],
            ack.attempt_budget,
            ack.execution_authority_hash,
            id(authority.physical_attempt_control_port),
            frozen.identity.run_id,
            frozen.identity.turn_id,
            frozen.identity.call_id,
            frozen.identity.request_freeze_id,
            frozen.signed_factory_binding_ref,
            frozen.signed_factory_binding_hash,
            frozen.semantic_candidate_hash,
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("factory_dispatch_live_pairing_drift") from exc
    if actual != expected:
        raise RuntimeError("factory_dispatch_authority_freeze_pairing_mismatch")
    return (id(authority), id(binding), id(frozen), actual, expected)


@final
class FactorySemanticDispatchPropagationPort:
    """Exact live Factory authority sidecar; never provider payload data."""

    __slots__ = (
        "_authority",
        "_binding",
        "_frozen",
        "_pairing_projection",
        "_physical_route_authority",
        "_qualification_proof",
        "_qualified_audit",
        "_qualified_context_snapshot_ref",
        "_workspace",
    )

    def __init__(
        self,
        *,
        authority: FactoryRoleEvidenceAuthorityBindingV1,
        binding: FactoryRoleEvidenceBindingV1,
        frozen: FactoryRoleFrozenSemanticRequestV1,
        workspace: str,
    ) -> None:
        pairing_projection = _live_pairing_projection(authority, binding, frozen)
        normalized_workspace = str(workspace or "").strip()
        if not normalized_workspace:
            raise ValueError("factory_dispatch_workspace_required")
        self._authority = authority
        self._binding = binding
        self._frozen = frozen
        self._pairing_projection = pairing_projection
        self._workspace = normalized_workspace
        self._qualification_proof: _FinalProviderAttemptQualificationProofV1 | None = None
        self._qualified_audit: dict[str, Any] | None = None
        self._qualified_context_snapshot_ref = ""
        self._physical_route_authority: dict[str, Any] | None = None

    @property
    def frozen_semantic_request(self) -> FactoryRoleFrozenSemanticRequestV1:
        return self._frozen

    def validate_frozen_identity(self, frozen: FactoryRoleFrozenSemanticRequestV1) -> None:
        if type(frozen) is not FactoryRoleFrozenSemanticRequestV1:
            raise TypeError("factory_dispatch_frozen_request_exact_type_required")
        if frozen is not self._frozen:
            raise RuntimeError("factory_dispatch_frozen_request_identity_mismatch")
        if _live_pairing_projection(self._authority, self._binding, self._frozen) != self._pairing_projection:
            raise RuntimeError("factory_dispatch_live_pairing_drift")

    def __repr__(self) -> str:
        state = "qualified" if self._qualified_audit is not None else "unqualified"
        return f"<FactorySemanticDispatchPropagationPort transport={state}>"

    __str__ = __repr__

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        del memo
        raise TypeError("factory_dispatch_port_serialization_forbidden")

    def __reduce__(self) -> NoReturn:
        raise TypeError("factory_dispatch_port_serialization_forbidden")

    @staticmethod
    def _disabled() -> NoReturn:
        raise RuntimeError(FACTORY_SEMANTIC_DISPATCH_NOT_ENABLED)

    @property
    def workspace(self) -> str:
        return self._workspace

    def final_context_evidence(self) -> tuple[str, dict[str, Any]] | None:
        """Return final physical ContextOS evidence after proof mint only."""

        if not self._qualified_context_snapshot_ref or self._qualification_proof is None:
            return None
        return self._qualified_context_snapshot_ref, copy.deepcopy(self._qualified_audit or {})

    def bind_final_request_context_audit(self, audit: Mapping[str, Any]) -> dict[str, Any]:
        """Project observed coverage through this request's cutoff authority."""

        self.validate_frozen_identity(self._frozen)
        return bind_final_request_context_audit_to_frozen(
            audit=audit,
            frozen=self._frozen,
            binding=self._binding,
        )

    def qualify(
        self,
        *,
        final_request_context_audit: Mapping[str, Any],
        context_snapshot_ref: str,
    ) -> None:
        """Arm this exact freeze only after the complete B3.5 proof passes."""

        # A failed re-qualification must never leave a prior PASS armed.
        self._qualification_proof = None
        self._qualified_audit = None
        self._qualified_context_snapshot_ref = ""
        self.validate_frozen_identity(self._frozen)
        try:
            authoritative_audit = self.bind_final_request_context_audit(final_request_context_audit)
            audit = qualify_final_provider_request(
                workspace=self._workspace,
                frozen=self._frozen,
                binding=self._binding,
                final_request_context_audit=authoritative_audit,
                context_snapshot_ref=context_snapshot_ref,
            )
        except FinalProviderAttemptQualificationError as exc:
            self._reject(exc.code)
            raise
        self._qualified_audit = audit
        self._qualified_context_snapshot_ref = str(context_snapshot_ref)

    def enforce_final_request_evidence_coverage(
        self,
        *,
        ai_request: Any,
        audit: dict[str, Any],
    ) -> None:
        """Fail closed through the port-owned durable rejection boundary.

        Factory coverage enforcement occurs before qualification can arm the
        physical gate.  Routing the failure through this live sidecar is what
        guarantees one non-physical rejection fact without creating a
        reservation, lifecycle event, or attempt-budget effect.
        """

        self._qualification_proof = None
        self._qualified_audit = None
        self._qualified_context_snapshot_ref = ""
        self.validate_frozen_identity(self._frozen)
        # Local import avoids the context_audit -> response_types -> sidecar
        # initialization cycle while preserving one authoritative enforcer.
        from .context_audit import (
            FinalRequestEvidenceCoverageError,
            enforce_final_request_evidence_coverage,
        )

        try:
            enforce_final_request_evidence_coverage(ai_request=ai_request, audit=audit)
        except FinalRequestEvidenceCoverageError as exc:
            rejection_code = (
                "final_request_role_identity_mismatch"
                if exc.violation.get("role_identity_ok") is False
                else "final_request_evidence_coverage_failed"
            )
            self._reject(rejection_code)
            raise

    def bind_provider_route_authority(
        self,
        *,
        provider_id: str,
        provider_type: str,
        model: str,
        mode: str,
        provider_config: Mapping[str, Any],
    ) -> None:
        """Bind the trusted Engine-resolved route before provider code runs."""

        payload = json.loads(self._frozen.canonical_final_payload_json)
        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode not in {"invoke", "stream"}:
            raise FinalProviderAttemptQualificationError("physical_provider_route_mode_invalid")
        typed_mode = cast(FactoryProviderDispatchMode, normalized_mode)
        projection = project_factory_provider_native_request(
            provider_type=str(provider_type or "").strip().lower(),
            mode=typed_mode,
            final_payload=payload,
            provider_config=provider_config,
        )
        if projection is None:
            raise FinalProviderAttemptQualificationError("physical_provider_native_projection_missing")
        native_authority = projection.authority()
        authority = {
            **native_authority,
            "schema_version": "llm.factory_physical_provider_route.v2",
            "native_request_schema_version": native_authority["schema_version"],
            "provider_id": str(provider_id or "").strip(),
            "model": str(model or "").strip(),
        }
        expected = {
            "provider_id": str(payload.get("provider_id") or ""),
            "model": str(payload.get("model") or ""),
            "mode": "stream" if payload.get("stream") is True else "invoke",
        }
        if any(authority[key] != value for key, value in expected.items()):
            raise FinalProviderAttemptQualificationError("physical_provider_route_authority_drift")
        if self._physical_route_authority is not None and self._physical_route_authority != authority:
            raise FinalProviderAttemptQualificationError("physical_provider_route_authority_rebind")
        self._physical_route_authority = authority

    def _reject(self, code: str) -> None:
        identity = self._frozen.identity
        rejection = FinalProviderAttemptQualificationRejectionV1(
            schema_version=FINAL_PROVIDER_ATTEMPT_QUALIFICATION_REJECTION_SCHEMA,
            verification_scope="factory",
            scope_id=self._authority.factory_run_id,
            factory_run_id=self._authority.factory_run_id,
            run_id=identity.run_id,
            role=self._authority.role,
            turn_id=identity.turn_id,
            call_id=identity.call_id,
            request_freeze_id=identity.request_freeze_id,
            rejection_code=code,
        )
        append_qualification_rejection(workspace=self._workspace, rejection=rejection)

    def _persist_final_physical_request_context(
        self,
        *,
        wire_request: Mapping[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Persist the exact provider-native request immediately before proof mint.

        The earlier semantic snapshot remains useful preparation evidence, but it
        cannot be the final ContextOS truth because the Engine and provider route
        have not yet produced endpoint/body/transport.  This immutable snapshot is
        the one referenced by the physical-attempt qualification proof.
        """

        if self._qualified_audit is None or self._physical_route_authority is None:
            raise FinalProviderAttemptQualificationError("final_physical_request_context_not_armed")
        # Re-read the currently armed snapshot at the last possible moment.
        # A PASS followed by deletion/replacement must never be converted into a
        # fresh physical snapshot from stale in-memory audit data.
        qualify_final_provider_request(
            workspace=self._workspace,
            frozen=self._frozen,
            binding=self._binding,
            final_request_context_audit=self._qualified_audit,
            context_snapshot_ref=self._qualified_context_snapshot_ref,
        )
        validate_exact_wire_before_reservation(
            frozen=self._frozen,
            wire_request=wire_request,
            physical_route_authority=self._physical_route_authority,
        )
        payload = json.loads(self._frozen.canonical_final_payload_json)
        redacted_wire = redact_provider_transport(dict(wire_request))
        redacted_route = redact_provider_transport(dict(self._physical_route_authority))
        wire_json = json.dumps(
            redacted_wire,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        wire_hash = hashlib.sha256(wire_json.encode("utf-8")).hexdigest()
        semantic_audit = copy.deepcopy(self._qualified_audit)
        snapshot_audit = copy.deepcopy(semantic_audit)
        body = redacted_wire.get("body")
        if not isinstance(body, Mapping):
            raise FinalProviderAttemptQualificationError("final_physical_request_body_missing")
        try:
            native_metrics = provider_native_request_metrics(
                body=body,
                native_protocol=str(self._physical_route_authority.get("native_protocol") or ""),
                context_window_tokens=int(semantic_audit.get("context_window_tokens") or 0),
            )
        except (TypeError, ValueError) as exc:
            raise FinalProviderAttemptQualificationError("final_physical_request_metrics_invalid") from exc
        snapshot_audit.update(native_metrics)
        snapshot_coverage = snapshot_audit.get("final_request_evidence_coverage")
        if isinstance(snapshot_coverage, dict):
            # The content hash creates the ref, so the ref cannot be embedded in
            # its own content.  Qualification compares the stable audit projection
            # and binds the newly issued ref after persistence.
            snapshot_coverage["context_snapshot_ref"] = ""
        snapshot_audit["final_physical_wire_hash"] = wire_hash
        snapshot_audit["provider_type"] = str(self._physical_route_authority.get("provider_type") or "")
        snapshot_audit["provider_mode"] = str(self._physical_route_authority.get("mode") or "")
        identity = self._frozen.identity
        snapshot = {
            "schema_version": "llm.final_physical_provider_request_context.v1",
            "trace_id": identity.run_id,
            "call_id": identity.call_id,
            "messages": payload["messages"],
            "provider_request": {
                "role": payload["role"],
                "provider_id": payload["provider_id"],
                "provider_type": str(self._physical_route_authority.get("provider_type") or ""),
                "model": payload["model"],
                "factory_final_request": final_request_snapshot_evidence(self._frozen),
                "semantic_request_context_audit": semantic_audit,
                "final_request_context_audit": snapshot_audit,
                "final_physical_request": redacted_wire,
                "final_physical_wire_hash": wire_hash,
                "physical_route_authority": redacted_route,
            },
        }
        provider_request_id = f"{identity.call_id}-{wire_hash[:16]}"
        persist_exc: Exception | None = None
        pin = None
        for persist_attempt in range(1, _FINAL_PHYSICAL_SNAPSHOT_PERSIST_ATTEMPTS + 1):
            try:
                pin = ContextSnapshotAuditPinRepository(workspace=self._workspace).persist_snapshot_and_pin(
                    snapshot=snapshot,
                    factory_run_id=self._authority.factory_run_id,
                    role=self._authority.role,
                    verification_scope="factory",
                    request_freeze_id=identity.request_freeze_id,
                    provider_request_id=provider_request_id,
                    composite_request_hash=wire_hash,
                    snapshot_source="roles.kernel.final_physical_provider_request",
                )
                persist_exc = None
                break
            except (ContextSnapshotAuditPinError, OSError, RuntimeError, TypeError, ValueError) as exc:
                persist_exc = exc
                retryable = any(marker in str(exc).lower() for marker in _RETRYABLE_SNAPSHOT_PERSIST_MARKERS)
                logger.warning(
                    "final physical context snapshot persist failed attempt=%s/%s retryable=%s: %s",
                    persist_attempt,
                    _FINAL_PHYSICAL_SNAPSHOT_PERSIST_ATTEMPTS,
                    retryable,
                    exc,
                    exc_info=True,
                )
                if not retryable or persist_attempt >= _FINAL_PHYSICAL_SNAPSHOT_PERSIST_ATTEMPTS:
                    break
                time.sleep(0.05 * persist_attempt)
        if persist_exc is not None or pin is None:
            raise FinalProviderAttemptQualificationError(
                "final_physical_context_snapshot_persist_failed"
            ) from persist_exc
        final_audit = copy.deepcopy(snapshot_audit)
        final_coverage = final_audit.get("final_request_evidence_coverage")
        if not isinstance(final_coverage, dict):
            raise FinalProviderAttemptQualificationError("final_request_evidence_coverage_missing")
        final_coverage["context_snapshot_ref"] = pin.context_snapshot_ref
        final_audit["final_physical_wire_hash"] = wire_hash
        final_audit["provider_type"] = str(self._physical_route_authority.get("provider_type") or "")
        final_audit["provider_mode"] = str(self._physical_route_authority.get("mode") or "")
        return pin.context_snapshot_ref, final_audit

    def _qualified_gate(
        self,
        wire_request: Mapping[str, Any],
        *,
        expected_stream: bool,
    ) -> FinalProviderAttemptGate:
        self.validate_frozen_identity(self._frozen)
        if self._qualified_audit is None or not self._qualified_context_snapshot_ref:
            self._disabled()
        if self._physical_route_authority is None:
            self._reject("physical_provider_route_authority_missing")
            raise FinalProviderAttemptQualificationError("physical_provider_route_authority_missing")
        try:
            final_context_ref, final_audit = self._persist_final_physical_request_context(
                wire_request=wire_request,
            )
            proof = _mint_final_provider_attempt_qualification_proof(
                workspace=self._workspace,
                frozen=self._frozen,
                binding=self._binding,
                final_request_context_audit=final_audit,
                context_snapshot_ref=final_context_ref,
                wire_request=wire_request,
                physical_route_authority=self._physical_route_authority,
            )
        except FinalProviderAttemptQualificationError as exc:
            self._reject(exc.code)
            raise
        self._qualification_proof = proof
        self._qualified_audit = proof.audit()
        self._qualified_context_snapshot_ref = proof.context_snapshot_ref
        payload = json.loads(self._frozen.canonical_final_payload_json)
        if payload.get("stream") is not expected_stream:
            self._reject("physical_dispatch_stream_mode_drift")
            raise FinalProviderAttemptQualificationError("physical_dispatch_stream_mode_drift")
        identity = self._frozen.identity
        return FinalProviderAttemptGate.for_factory_run(
            workspace=self._workspace,
            factory_run_id=self._authority.factory_run_id,
            run_id=identity.run_id,
            role=self._authority.role,
            turn_id=identity.turn_id,
            call_id=identity.call_id,
            request_freeze_id=identity.request_freeze_id,
            provider=str(payload["provider_id"]),
            model=str(payload["model"]),
            semantic_request=final_gate_semantic_request(self._frozen),
            physical_attempt_control_port=self._authority.physical_attempt_control_port,
            execution_authority_hash=self._authority.execution_authority_hash,
            attempt_budget=self._authority.attempt_budget,
            qualification_proof=proof,
        )

    def dispatch_sync(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], _DispatchResultT],
    ) -> _DispatchResultT:
        return self._qualified_gate(wire_request, expected_stream=False).dispatch_sync(
            wire_request=wire_request,
            send=send,
        )

    async def dispatch_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], Awaitable[_DispatchResultT]],
    ) -> _DispatchResultT:
        return await self._qualified_gate(wire_request, expected_stream=False).dispatch_async(
            wire_request=wire_request,
            send=send,
        )

    async def dispatch_blocking_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], _DispatchResultT],
    ) -> _DispatchResultT:
        return await self._qualified_gate(wire_request, expected_stream=False).dispatch_blocking_async(
            wire_request=wire_request,
            send=send,
        )

    def dispatch_stream_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        open_stream: Callable[[Mapping[str, Any]], AsyncContextManager[_StreamResponseT]],
        consume: Callable[[_StreamResponseT], AsyncIterator[_DispatchResultT]],
    ) -> AsyncIterator[_DispatchResultT]:
        return self._qualified_gate(wire_request, expected_stream=True).dispatch_stream_async(
            wire_request=wire_request,
            open_stream=open_stream,
            consume=consume,
        )


def enforce_factory_aware_final_request_evidence_coverage(
    *,
    port: object | None,
    ai_request: Any,
    audit: dict[str, Any],
) -> None:
    """Use the Factory rejection ledger when an exact live sidecar exists."""

    if type(port) is FactorySemanticDispatchPropagationPort:
        port.enforce_final_request_evidence_coverage(ai_request=ai_request, audit=audit)
        return
    from .context_audit import enforce_final_request_evidence_coverage

    enforce_final_request_evidence_coverage(ai_request=ai_request, audit=audit)


__all__ = [
    "FACTORY_SEMANTIC_DISPATCH_NOT_ENABLED",
    "FactorySemanticDispatchPropagationPort",
    "enforce_factory_aware_final_request_evidence_coverage",
]
