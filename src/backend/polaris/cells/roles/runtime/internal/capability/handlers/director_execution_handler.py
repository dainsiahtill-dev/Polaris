"""``director_task_execution`` capability handler.

Identity tuple::

    ("execute_director_task", "director.execution", "ExecuteDirectorTaskCommandV1")

This is a VERBATIM re-shaping of the legacy ``is_director_task_execution`` arm of
``execute_role_capability_invocation`` onto the
:class:`~polaris.cells.roles.runtime.internal.capability.protocol.CapabilityHandler`
surface:

* :meth:`validate` reproduces the two pre-invoke rejection paths — the
  ``metadata`` mapping check (``invalid_director_execution_metadata``) and the
  :class:`ExecuteDirectorTaskCommandV1` construction guard
  (``invalid_director_execution_command``) — raising
  :class:`CapabilityInvocationError` instead of returning a failure result.
* :meth:`invoke` performs the director execution exactly as the legacy branch:
  ``deps.director_execution_service`` (service-object or callable form) or the
  ``director.execution`` module-level public function when the port is ``None``;
  it raises ``director_execution_failed`` on any downstream exception.
* :meth:`map_result` builds the success / not-ok :class:`RoleCapabilityInvocationResultV1`
  verbatim, surfacing the director cell's own ``error_code`` on the not-ok path.

The command/asset-ref construction is a pure function of ``command`` (helper
:func:`_build_director_command`), so :meth:`validate` and :meth:`invoke` rebuild
it identically without sharing mutable state; :meth:`map_result` likewise
recomputes the asset refs deterministically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from polaris.cells.roles.runtime.internal.capability.errors import CapabilityInvocationError
from polaris.cells.roles.runtime.public.capability_commands import (
    _asset_mount_ref,
    _audit_evidence_refs,
    _capability_available_metadata,
    _payload_mapping,
    _payload_string,
)
from polaris.cells.roles.runtime.public.contracts import RoleCapabilityInvocationResultV1

if TYPE_CHECKING:
    from polaris.cells.director.execution.public.contracts import (
        DirectorExecutionResultV1,
        ExecuteDirectorTaskCommandV1,
    )
    from polaris.cells.roles.runtime.internal.capability.deps import CapabilityDeps
    from polaris.cells.roles.runtime.public.contracts import (
        ExecuteRoleCapabilityInvocationCommandV1,
        RoleCapabilityDescriptor,
        RoleRuntimeObject,
    )


def _director_asset_refs(runtime_object: RoleRuntimeObject) -> dict[str, str]:
    """Build the director asset-ref bundle (verbatim from the legacy branch)."""
    return {
        "execution_task": _asset_mount_ref(runtime_object, "ExecutionTask"),
        "director_execution_state": _asset_mount_ref(runtime_object, "DirectorExecutionState"),
        "director_evidence_trail": _asset_mount_ref(runtime_object, "DirectorEvidenceTrail"),
    }


def _build_director_command(
    command: ExecuteRoleCapabilityInvocationCommandV1,
    capability: RoleCapabilityDescriptor,
) -> tuple[ExecuteDirectorTaskCommandV1, dict[str, str]]:
    """Construct the ``ExecuteDirectorTaskCommandV1`` + asset refs from ``command``.

    Mirrors the legacy branch's metadata-mutation + command construction
    statements byte-for-byte. Raises :class:`CapabilityInvocationError` with the
    legacy ``error_code`` literals on the two pre-invoke rejection paths.
    """
    runtime_object = command.runtime_object
    invocation = command.invocation

    director_metadata = _payload_mapping(command.payload, "metadata")
    if director_metadata is None:
        raise CapabilityInvocationError(
            "payload.metadata must be a mapping when provided",
            code="invalid_director_execution_metadata",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    director_asset_refs = _director_asset_refs(runtime_object)
    director_metadata.update(
        {
            "role_invocation_id": invocation.invocation_id,
            "role_payload_ref": invocation.payload_ref,
            "role_fingerprint_ref": invocation.fingerprint_ref,
            "role_capability_id": capability.capability_id,
            "asset_refs": director_asset_refs,
        }
    )
    instruction = (
        _payload_string(command.payload, "instruction")
        or _payload_string(command.payload, "objective")
        or _payload_string(command.payload, "summary")
    )
    try:
        from polaris.cells.director.execution.public.contracts import ExecuteDirectorTaskCommandV1

        director_command = ExecuteDirectorTaskCommandV1(
            task_id=_payload_string(command.payload, "task_id", runtime_object.identity.task_id or ""),
            workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
            instruction=instruction,
            run_id=_payload_string(command.payload, "run_id", runtime_object.identity.run_id or "") or None,
            attempt=int(command.payload.get("attempt", 1)),
            metadata=director_metadata,
        )
    except (TypeError, ValueError) as exc:
        raise CapabilityInvocationError(
            str(exc),
            code="invalid_director_execution_command",
            owner_cell=capability.owner_cell,
            capability_available=True,
        ) from exc
    return director_command, director_asset_refs


def _resolve_capability(command: ExecuteRoleCapabilityInvocationCommandV1) -> RoleCapabilityDescriptor:
    """Re-fetch the mounted capability descriptor for the invoked capability."""
    return command.runtime_object.capability_ports.get(command.invocation.capability_id)


class DirectorExecutionHandler:
    """:class:`CapabilityHandler` for ``execute_director_task``."""

    def validate(self, command: ExecuteRoleCapabilityInvocationCommandV1) -> None:
        capability = _resolve_capability(command)
        _build_director_command(command, capability)

    def invoke(
        self,
        command: ExecuteRoleCapabilityInvocationCommandV1,
        deps: CapabilityDeps,
    ) -> object:
        capability = _resolve_capability(command)
        director_command, _ = _build_director_command(command, capability)

        director_execution_service = deps.director_execution_service
        try:
            if director_execution_service is None:
                from polaris.cells.director.execution.public.service import execute_director_task

                director_result: DirectorExecutionResultV1 = execute_director_task(director_command)
            elif callable(director_execution_service):
                director_result = cast("DirectorExecutionResultV1", director_execution_service(director_command))
            else:
                director_result = cast(
                    "DirectorExecutionResultV1",
                    director_execution_service.execute_director_task(director_command),
                )
        except Exception as exc:
            raise CapabilityInvocationError(
                str(exc),
                code="director_execution_failed",
                owner_cell=capability.owner_cell,
                capability_available=True,
            ) from exc
        return director_result

    def map_result(
        self,
        raw: object,
        command: ExecuteRoleCapabilityInvocationCommandV1,
    ) -> RoleCapabilityInvocationResultV1:
        director_result = cast("DirectorExecutionResultV1", raw)
        runtime_object = command.runtime_object
        invocation = command.invocation
        role_id = runtime_object.identity.role_id
        capability = _resolve_capability(command)
        director_asset_refs = _director_asset_refs(runtime_object)

        result_ref = f"director.execution:task:{director_result.task_id}"
        evidence_paths = tuple(director_result.evidence_paths)
        audit_evidence_refs = _audit_evidence_refs(evidence_paths)
        metadata: dict[str, Any] = _capability_available_metadata(
            capability.capability_id,
            {
                "director_status": director_result.status,
                "output_summary": director_result.output_summary,
                "evidence_paths": evidence_paths,
                "audit_evidence_refs": audit_evidence_refs,
                "asset_refs": director_asset_refs,
            },
        )
        if not director_result.ok:
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
                task_id=director_result.task_id,
                status=director_result.status,
                evidence_refs=audit_evidence_refs,
                metadata=metadata,
                error_code=director_result.error_code or "director_execution_rejected",
                error_message=director_result.error_message or "director execution rejected the task",
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
            task_id=director_result.task_id,
            status=director_result.status,
            evidence_refs=audit_evidence_refs,
            metadata=metadata,
        )
