"""``pm_runtime_projection`` capability handler.

Identity tuple::

    ("project_runtime_status", "runtime.projection", "RuntimeProjectionQueryV1")

This is a verbatim extraction of the ``is_pm_runtime_projection`` dispatcher arm of
``execute_role_capability_invocation`` onto the
:class:`~polaris.cells.roles.runtime.internal.capability.protocol.CapabilityHandler`
surface:

* :meth:`validate` reproduces the single pre-invoke rejection path — the
  :class:`RuntimeProjectionQueryV1` construction guard
  (``invalid_runtime_projection_query``) — raising
  :class:`CapabilityInvocationError` instead of returning a failure result.
* :meth:`invoke` performs the runtime-projection query exactly as the extracted
  branch: it requires the injected ``deps.runtime_projection_service`` port —
  raising ``runtime_projection_service_unavailable`` when the port is ``None``
  (this branch has NO module-level public-function fall-back; the
  host boundary MUST inject the service) — then calls
  ``query_runtime_projection`` and raises ``runtime_projection_query_failed`` on
  any downstream exception.
* :meth:`map_result` builds the success :class:`RoleCapabilityInvocationResultV1`
  verbatim (``status="PROJECTED"`` with the projection payload + scope metadata).

The projection query is a pure function of ``command.payload`` (helper
:func:`_build_projection_query`), so :meth:`validate`, :meth:`invoke`, and
:meth:`map_result` rebuild it identically without sharing mutable state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from polaris.cells.roles.runtime.internal.capability.errors import CapabilityInvocationError
from polaris.cells.roles.runtime.public.capability_commands import _payload_string
from polaris.cells.roles.runtime.public.contracts import RoleCapabilityInvocationResultV1

if TYPE_CHECKING:
    from polaris.cells.roles.runtime.internal.capability.deps import CapabilityDeps
    from polaris.cells.roles.runtime.public.contracts import (
        ExecuteRoleCapabilityInvocationCommandV1,
        RoleCapabilityDescriptor,
    )
    from polaris.cells.runtime.projection.public.contracts import (
        RuntimeProjectionQueryV1,
        RuntimeProjectionResultV1,
    )


def _resolve_capability(command: ExecuteRoleCapabilityInvocationCommandV1) -> RoleCapabilityDescriptor:
    """Re-fetch the mounted capability descriptor for the invoked capability."""
    return command.runtime_object.capability_ports.get(command.invocation.capability_id)


def _build_projection_query(
    command: ExecuteRoleCapabilityInvocationCommandV1,
    capability: RoleCapabilityDescriptor,
) -> RuntimeProjectionQueryV1:
    """Construct the ``RuntimeProjectionQueryV1`` from ``command``.

    Mirrors the extracted branch's query construction statement byte-for-byte.
    Raises :class:`CapabilityInvocationError` with the stable
    ``invalid_runtime_projection_query`` literal on the construction-guard
    rejection path.
    """
    try:
        from polaris.cells.runtime.projection.public.contracts import RuntimeProjectionQueryV1

        projection_query = RuntimeProjectionQueryV1(scope=_payload_string(command.payload, "scope", "runtime"))
    except (TypeError, ValueError) as exc:
        raise CapabilityInvocationError(
            str(exc),
            code="invalid_runtime_projection_query",
            owner_cell=capability.owner_cell,
            capability_available=True,
        ) from exc
    return projection_query


class PmRuntimeProjectionHandler:
    """:class:`CapabilityHandler` for ``project_runtime_status``."""

    def validate(self, command: ExecuteRoleCapabilityInvocationCommandV1) -> None:
        capability = _resolve_capability(command)
        _build_projection_query(command, capability)

    def invoke(
        self,
        command: ExecuteRoleCapabilityInvocationCommandV1,
        deps: CapabilityDeps,
    ) -> object:
        capability = _resolve_capability(command)
        projection_query = _build_projection_query(command, capability)

        runtime_projection_service = deps.runtime_projection_service
        if runtime_projection_service is None:
            raise CapabilityInvocationError(
                "runtime.projection query service must be injected by the host boundary",
                code="runtime_projection_service_unavailable",
                owner_cell=capability.owner_cell,
                capability_available=True,
            )
        try:
            projection_result = runtime_projection_service.query_runtime_projection(projection_query)
        except Exception as exc:
            raise CapabilityInvocationError(
                str(exc),
                code="runtime_projection_query_failed",
                owner_cell=capability.owner_cell,
                capability_available=True,
            ) from exc
        return projection_result

    def map_result(
        self,
        raw: object,
        command: ExecuteRoleCapabilityInvocationCommandV1,
    ) -> RoleCapabilityInvocationResultV1:
        projection_result = cast("RuntimeProjectionResultV1", raw)
        runtime_object = command.runtime_object
        invocation = command.invocation
        role_id = runtime_object.identity.role_id
        capability = _resolve_capability(command)
        projection_query = _build_projection_query(command, capability)

        result_ref = f"runtime.projection:{projection_query.scope}:{invocation.invocation_id}"
        metadata: dict[str, Any] = {
            "projection": dict(projection_result.payload),
            "scope": projection_query.scope,
        }
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
            status="PROJECTED",
            metadata=metadata,
        )
