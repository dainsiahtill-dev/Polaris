"""``workspace_guard`` capability handler.

Identity tuple::

    ("intercept_illegal_mutations", "policy.workspace_guard", "WorkspaceWriteGuardQueryV1")

This is a verbatim extraction of the ``is_architect_workspace_guard`` dispatcher
arm of ``execute_role_capability_invocation`` onto the
:class:`~polaris.cells.roles.runtime.internal.capability.protocol.CapabilityHandler`
surface. This dispatcher arm is non-uniform: it first validates the payload path,
then constructs the single-path guard query, then performs the guard check;
``validate``/``invoke`` absorb that per-branch divergence while keeping the
three-method surface stable.

* :meth:`validate` reproduces the two pre-invoke rejection paths — the empty
  ``payload.path`` check (``invalid_workspace_guard_path``) and the
  :class:`WorkspaceWriteGuardQueryV1` construction guard
  (``invalid_workspace_guard_query``) — raising
  :class:`CapabilityInvocationError` instead of returning a failure result. Both
  original rejections passed ``capability_available=True`` with no extra
  metadata, so the raised errors carry ``capability_available=True`` and leave
  ``metadata`` at its default; the dispatcher then renders the stable
  ``{capability_available: True, capability_id: <id>}`` payload.
* :meth:`invoke` performs the workspace-guard check exactly as the extracted arm:
  ``deps.workspace_guard_service`` when set, else the ``policy.workspace_guard``
  module-level public function ``check_workspace_write_guard`` via a
  function-local import; it raises ``workspace_guard_failed`` on any downstream
  exception.
* :meth:`map_result` builds the success (``ALLOWED``) / denied (``DENIED``)
  :class:`RoleCapabilityInvocationResultV1` verbatim, including the stable plain
  ``metadata`` dict literal (``capability_available`` / ``mutation_allowed`` /
  ``guard_reason`` / ``path`` / ``operation``) — NOT the
  ``_capability_available_metadata`` envelope used by other families.

The guard-query construction is a pure function of ``command`` (helper
:func:`_build_workspace_guard_query`), so :meth:`validate` and :meth:`invoke`
rebuild it identically without sharing mutable state; :meth:`map_result` likewise
recomputes the query deterministically to recover the canonical
``path``/``operation`` values for the result metadata.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from polaris.cells.roles.runtime.internal.capability.errors import CapabilityInvocationError
from polaris.cells.roles.runtime.public.capability_commands import _payload_string
from polaris.cells.roles.runtime.public.contracts import RoleCapabilityInvocationResultV1

if TYPE_CHECKING:
    from polaris.cells.policy.workspace_guard.public.contracts import (
        WorkspaceGuardDecisionV1,
        WorkspaceWriteGuardQueryV1,
    )
    from polaris.cells.roles.runtime.internal.capability.deps import CapabilityDeps
    from polaris.cells.roles.runtime.public.contracts import (
        ExecuteRoleCapabilityInvocationCommandV1,
        RoleCapabilityDescriptor,
    )


def _build_workspace_guard_query(
    command: ExecuteRoleCapabilityInvocationCommandV1,
    capability: RoleCapabilityDescriptor,
) -> WorkspaceWriteGuardQueryV1:
    """Construct the ``WorkspaceWriteGuardQueryV1`` from ``command``.

    Mirrors the extracted branch's path validation + query construction
    statements byte-for-byte. Raises :class:`CapabilityInvocationError` with the
    stable ``error_code`` literals on the two pre-invoke rejection paths.
    """
    target_path = _payload_string(command.payload, "path")
    operation = _payload_string(command.payload, "operation", "write")
    if not target_path:
        raise CapabilityInvocationError(
            "payload.path must be a non-empty string",
            code="invalid_workspace_guard_path",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    try:
        from polaris.cells.policy.workspace_guard.public.contracts import WorkspaceWriteGuardQueryV1

        guard_query = WorkspaceWriteGuardQueryV1(
            path=target_path,
            operation=operation,
        )
    except (TypeError, ValueError) as exc:
        raise CapabilityInvocationError(
            str(exc),
            code="invalid_workspace_guard_query",
            owner_cell=capability.owner_cell,
            capability_available=True,
        ) from exc
    return guard_query


def _resolve_capability(command: ExecuteRoleCapabilityInvocationCommandV1) -> RoleCapabilityDescriptor:
    """Re-fetch the mounted capability descriptor for the invoked capability."""
    return command.runtime_object.capability_ports.get(command.invocation.capability_id)


class WorkspaceGuardHandler:
    """:class:`CapabilityHandler` for ``intercept_illegal_mutations``."""

    def validate(self, command: ExecuteRoleCapabilityInvocationCommandV1) -> None:
        capability = _resolve_capability(command)
        _build_workspace_guard_query(command, capability)

    def invoke(
        self,
        command: ExecuteRoleCapabilityInvocationCommandV1,
        deps: CapabilityDeps,
    ) -> object:
        capability = _resolve_capability(command)
        guard_query = _build_workspace_guard_query(command, capability)

        workspace_guard_service = deps.workspace_guard_service
        try:
            if workspace_guard_service is None:
                from polaris.cells.policy.workspace_guard.public.service import check_workspace_write_guard

                guard_result: WorkspaceGuardDecisionV1 = check_workspace_write_guard(guard_query)
            else:
                guard_result = cast(
                    "WorkspaceGuardDecisionV1",
                    workspace_guard_service.check_workspace_write_guard(guard_query),
                )
        except Exception as exc:
            raise CapabilityInvocationError(
                str(exc),
                code="workspace_guard_failed",
                owner_cell=capability.owner_cell,
                capability_available=True,
            ) from exc
        return guard_result

    def map_result(
        self,
        raw: object,
        command: ExecuteRoleCapabilityInvocationCommandV1,
    ) -> RoleCapabilityInvocationResultV1:
        guard_result = cast("WorkspaceGuardDecisionV1", raw)
        runtime_object = command.runtime_object
        invocation = command.invocation
        role_id = runtime_object.identity.role_id
        capability = _resolve_capability(command)
        guard_query = _build_workspace_guard_query(command, capability)

        result_ref = f"policy.workspace_guard:decision:{invocation.invocation_id}"
        metadata: dict[str, Any] = {
            "capability_available": True,
            "mutation_allowed": guard_result.allowed,
            "guard_reason": guard_result.reason,
            "path": guard_query.path,
            "operation": guard_query.operation,
        }
        if not guard_result.allowed:
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
                error_code="workspace_guard_denied",
                error_message=guard_result.reason or "workspace guard denied mutation",
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
            status="ALLOWED",
            metadata=metadata,
        )
