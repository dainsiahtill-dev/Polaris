"""``budget_reservation`` capability handler.

Identity tuple::

    ("allocate_context_token_budget", "finops.budget_guard", "ReserveBudgetCommandV1")

This is a verbatim extraction of the ``is_architect_budget_reservation``
dispatcher arm of ``execute_role_capability_invocation`` onto the
:class:`~polaris.cells.roles.runtime.internal.capability.protocol.CapabilityHandler`
surface. The branch is **non-uniform**: it chains a multi-stage validation
(``invalid_budget_metadata`` -> ``invalid_budget_command`` ->
``budget_guard_failed`` -> ``budget_denied``). The chain is absorbed faithfully:

* :meth:`validate` reproduces the two pre-invoke rejection paths — the
  ``metadata`` mapping check (``invalid_budget_metadata``) and the
  :class:`ReserveBudgetCommandV1` construction guard
  (``invalid_budget_command``, raised by the ``int(...)`` token-budget coercion
  or the contract ``__post_init__`` on ``TypeError``/``ValueError``) — raising
  :class:`CapabilityInvocationError` instead of returning a failure result.
* :meth:`invoke` performs the reservation exactly as the extracted branch:
  ``deps.budget_guard_service.reserve_budget`` when the port is set, else the
  ``finops.budget_guard`` module-level public function when the port is ``None``;
  it raises ``budget_guard_failed`` on any downstream exception.
* :meth:`map_result` builds the ``RESERVED`` success / ``DENIED``
  (``budget_denied``) :class:`RoleCapabilityInvocationResultV1` verbatim.

The command construction is a pure function of ``command`` (helper
:func:`_build_reserve_command`), so :meth:`validate`, :meth:`invoke`, and
:meth:`map_result` rebuild it identically without sharing mutable state
(``_payload_mapping`` returns a fresh ``dict`` per call, so the in-place
metadata ``update`` is side-effect-free across rebuilds).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from polaris.cells.roles.runtime.internal.capability.errors import CapabilityInvocationError
from polaris.cells.roles.runtime.public.capability_commands import (
    _capability_available_metadata,
    _payload_mapping,
    _payload_string,
)
from polaris.cells.roles.runtime.public.contracts import RoleCapabilityInvocationResultV1

if TYPE_CHECKING:
    from polaris.cells.finops.budget_guard.public.contracts import (
        BudgetDecisionResultV1,
        ReserveBudgetCommandV1,
    )
    from polaris.cells.roles.runtime.internal.capability.deps import CapabilityDeps
    from polaris.cells.roles.runtime.public.contracts import (
        ExecuteRoleCapabilityInvocationCommandV1,
        RoleCapabilityDescriptor,
    )


def _resolve_capability(command: ExecuteRoleCapabilityInvocationCommandV1) -> RoleCapabilityDescriptor:
    """Re-fetch the mounted capability descriptor for the invoked capability."""
    return command.runtime_object.capability_ports.get(command.invocation.capability_id)


def _build_reserve_command(
    command: ExecuteRoleCapabilityInvocationCommandV1,
    capability: RoleCapabilityDescriptor,
) -> ReserveBudgetCommandV1:
    """Construct the ``ReserveBudgetCommandV1`` from ``command``.

    Mirrors the extracted branch's metadata-mutation + command construction
    statements byte-for-byte. Raises :class:`CapabilityInvocationError` with the
    stable ``error_code`` literals on the two pre-invoke rejection paths.
    """
    runtime_object = command.runtime_object
    invocation = command.invocation
    role_id = runtime_object.identity.role_id

    budget_metadata = _payload_mapping(command.payload, "metadata")
    if budget_metadata is None:
        raise CapabilityInvocationError(
            "payload.metadata must be a mapping when provided",
            code="invalid_budget_metadata",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    budget_metadata.update(
        {
            "role_invocation_id": invocation.invocation_id,
            "role_payload_ref": invocation.payload_ref,
            "role_fingerprint_ref": invocation.fingerprint_ref,
            "role_capability_id": capability.capability_id,
        }
    )
    try:
        token_budget = int(command.payload.get("token_budget", command.payload.get("context_token_budget", 0)))
        from polaris.cells.finops.budget_guard.public.contracts import ReserveBudgetCommandV1

        reserve_command = ReserveBudgetCommandV1(
            scope_id=_payload_string(command.payload, "scope_id", invocation.invocation_id),
            workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
            role=role_id,
            token_budget=token_budget,
            metadata=budget_metadata,
        )
    except (TypeError, ValueError) as exc:
        raise CapabilityInvocationError(
            str(exc),
            code="invalid_budget_command",
            owner_cell=capability.owner_cell,
            capability_available=True,
        ) from exc
    return reserve_command


class BudgetReservationHandler:
    """:class:`CapabilityHandler` for ``allocate_context_token_budget``."""

    def validate(self, command: ExecuteRoleCapabilityInvocationCommandV1) -> None:
        capability = _resolve_capability(command)
        _build_reserve_command(command, capability)

    def invoke(
        self,
        command: ExecuteRoleCapabilityInvocationCommandV1,
        deps: CapabilityDeps,
    ) -> object:
        capability = _resolve_capability(command)
        reserve_command = _build_reserve_command(command, capability)

        budget_guard_service = deps.budget_guard_service
        try:
            if budget_guard_service is None:
                from polaris.cells.finops.budget_guard.public.service import reserve_budget

                budget_result: BudgetDecisionResultV1 = reserve_budget(reserve_command)
            else:
                budget_result = cast(
                    "BudgetDecisionResultV1",
                    budget_guard_service.reserve_budget(reserve_command),
                )
        except Exception as exc:
            raise CapabilityInvocationError(
                str(exc),
                code="budget_guard_failed",
                owner_cell=capability.owner_cell,
                capability_available=True,
            ) from exc
        return budget_result

    def map_result(
        self,
        raw: object,
        command: ExecuteRoleCapabilityInvocationCommandV1,
    ) -> RoleCapabilityInvocationResultV1:
        budget_result = cast("BudgetDecisionResultV1", raw)
        runtime_object = command.runtime_object
        invocation = command.invocation
        role_id = runtime_object.identity.role_id
        capability = _resolve_capability(command)
        reserve_command = _build_reserve_command(command, capability)

        result_ref = f"finops.budget_guard:budget:{reserve_command.scope_id}"
        metadata: dict[str, Any] = _capability_available_metadata(
            capability.capability_id,
            {
                "budget_allowed": budget_result.allowed,
                "remaining_tokens": budget_result.remaining_tokens,
                "estimated_cost_usd": budget_result.estimated_cost_usd,
                "reason": budget_result.reason,
            },
        )
        if not budget_result.allowed:
            return RoleCapabilityInvocationResultV1(
                ok=False,
                invocation_id=invocation.invocation_id,
                role_id=role_id,
                capability_id=capability.capability_id,
                command_contract=capability.contract_name,
                allowed=False,
                owner_cell=capability.owner_cell,
                payload_ref=result_ref,
                result_ref=result_ref,
                task_id=runtime_object.identity.task_id or "",
                status="DENIED",
                metadata=metadata,
                error_code="budget_denied",
                error_message=budget_result.reason or "budget reservation denied",
            )
        return RoleCapabilityInvocationResultV1(
            ok=True,
            invocation_id=invocation.invocation_id,
            role_id=role_id,
            capability_id=capability.capability_id,
            command_contract=capability.contract_name,
            allowed=True,
            owner_cell=capability.owner_cell,
            payload_ref=result_ref,
            result_ref=result_ref,
            task_id=runtime_object.identity.task_id or "",
            status="RESERVED",
            metadata=metadata,
        )
