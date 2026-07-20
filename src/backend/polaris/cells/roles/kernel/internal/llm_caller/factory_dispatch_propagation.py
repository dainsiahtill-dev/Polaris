"""Runtime-private B3.3 Factory semantic dispatch sidecar.

This object only propagates one exact semantic freeze to physical transport
seams.  B3.3 deliberately authorizes no transport: every dispatch method
fails before invoking its callback.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any, AsyncContextManager, NoReturn, TypeVar, final

from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FactoryRoleEvidenceAuthorityBindingV1,
    FactoryRoleFrozenSemanticRequestV1,
)

from .factory_role_evidence_binding import FactoryRoleEvidenceBindingV1

_DispatchResultT = TypeVar("_DispatchResultT")
_StreamResponseT = TypeVar("_StreamResponseT")
FACTORY_SEMANTIC_DISPATCH_NOT_ENABLED = "factory_role_semantic_request_frozen_physical_dispatch_not_enabled"


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

    __slots__ = ("_authority", "_binding", "_frozen", "_pairing_projection")

    def __init__(
        self,
        *,
        authority: FactoryRoleEvidenceAuthorityBindingV1,
        binding: FactoryRoleEvidenceBindingV1,
        frozen: FactoryRoleFrozenSemanticRequestV1,
    ) -> None:
        pairing_projection = _live_pairing_projection(authority, binding, frozen)
        self._authority = authority
        self._binding = binding
        self._frozen = frozen
        self._pairing_projection = pairing_projection

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
        return "<FactorySemanticDispatchPropagationPort transport=disabled>"

    __str__ = __repr__

    def __deepcopy__(self, memo: dict[int, object]) -> NoReturn:
        del memo
        raise TypeError("factory_dispatch_port_serialization_forbidden")

    def __reduce__(self) -> NoReturn:
        raise TypeError("factory_dispatch_port_serialization_forbidden")

    @staticmethod
    def _disabled() -> NoReturn:
        raise RuntimeError(FACTORY_SEMANTIC_DISPATCH_NOT_ENABLED)

    def dispatch_sync(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], _DispatchResultT],
    ) -> _DispatchResultT:
        del wire_request, send
        self._disabled()

    async def dispatch_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], Awaitable[_DispatchResultT]],
    ) -> _DispatchResultT:
        del wire_request, send
        self._disabled()

    async def dispatch_blocking_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], _DispatchResultT],
    ) -> _DispatchResultT:
        del wire_request, send
        self._disabled()

    def dispatch_stream_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        open_stream: Callable[[Mapping[str, Any]], AsyncContextManager[_StreamResponseT]],
        consume: Callable[[_StreamResponseT], AsyncIterator[_DispatchResultT]],
    ) -> AsyncIterator[_DispatchResultT]:
        del wire_request, open_stream, consume

        async def _disabled_stream() -> AsyncIterator[_DispatchResultT]:
            self._disabled()
            if False:  # pragma: no cover - retain AsyncIterator shape
                yield  # type: ignore[misc]

        return _disabled_stream()


__all__ = [
    "FACTORY_SEMANTIC_DISPATCH_NOT_ENABLED",
    "FactorySemanticDispatchPropagationPort",
]
