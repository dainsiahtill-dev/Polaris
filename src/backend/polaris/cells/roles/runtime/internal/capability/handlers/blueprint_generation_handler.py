"""``blueprint_generation`` capability handler.

Identity tuples::

    ("generate_diff_specification", "chief_engineer.blueprint", "GenerateTaskBlueprintCommandV1")
    ("record_arch_memo", "chief_engineer.blueprint", "GenerateTaskBlueprintCommandV1")

This is a VERBATIM re-shaping of the legacy ``is_blueprint_generation`` arm of
``execute_role_capability_invocation`` onto the
:class:`~polaris.cells.roles.runtime.internal.capability.protocol.CapabilityHandler`
surface. The branch admits two capability ids (``generate_diff_specification``
and ``record_arch_memo``) for the same ``chief_engineer.blueprint`` owner cell and
``GenerateTaskBlueprintCommandV1`` contract, so a single handler answers BOTH
identity tuples:

* :meth:`validate` reproduces the three pre-invoke rejection paths — the
  ``context`` mapping check (``invalid_blueprint_context``), the ``constraints``
  mapping check (``invalid_blueprint_constraints``) and the
  :class:`GenerateTaskBlueprintCommandV1` construction guard
  (``invalid_blueprint_command``) — raising :class:`CapabilityInvocationError`
  instead of returning a failure result.
* :meth:`invoke` performs the blueprint generation exactly as the legacy branch:
  ``deps.blueprint_service`` when set, else the ``chief_engineer.blueprint``
  module-level public function when the port is ``None``; it raises
  ``blueprint_generation_failed`` on any downstream exception.
* :meth:`map_result` builds the success / not-ok :class:`RoleCapabilityInvocationResultV1`
  verbatim, surfacing ``blueprint_generation_rejected`` on the not-ok path.

The command/asset-ref construction is a pure function of ``command`` (helper
:func:`_build_blueprint_command`), so :meth:`validate` and :meth:`invoke` rebuild
it identically without sharing mutable state; :meth:`map_result` likewise
recomputes the asset refs deterministically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from polaris.cells.roles.runtime.internal.capability.errors import CapabilityInvocationError
from polaris.cells.roles.runtime.public.capability_commands import (
    _asset_mount_ref,
    _chief_engineer_asset_refs,
    _payload_mapping,
    _payload_string,
)
from polaris.cells.roles.runtime.public.contracts import RoleCapabilityInvocationResultV1

if TYPE_CHECKING:
    from polaris.cells.chief_engineer.blueprint.public.contracts import (
        GenerateTaskBlueprintCommandV1,
        TaskBlueprintResultV1,
    )
    from polaris.cells.roles.runtime.internal.capability.deps import CapabilityDeps
    from polaris.cells.roles.runtime.public.contracts import (
        ExecuteRoleCapabilityInvocationCommandV1,
        RoleCapabilityDescriptor,
    )


def _resolve_capability(command: ExecuteRoleCapabilityInvocationCommandV1) -> RoleCapabilityDescriptor:
    """Re-fetch the mounted capability descriptor for the invoked capability."""
    return command.runtime_object.capability_ports.get(command.invocation.capability_id)


def _build_blueprint_command(
    command: ExecuteRoleCapabilityInvocationCommandV1,
    capability: RoleCapabilityDescriptor,
) -> tuple[GenerateTaskBlueprintCommandV1, dict[str, str], str, str]:
    """Construct the ``GenerateTaskBlueprintCommandV1`` + asset refs from ``command``.

    Mirrors the legacy branch's context-mutation + command construction
    statements byte-for-byte. Raises :class:`CapabilityInvocationError` with the
    legacy ``error_code`` literals on the three pre-invoke rejection paths.

    Returns the constructed command together with the Chief Engineer asset-ref
    bundle and the resolved ``target_asset_mount`` / ``target_asset_ref`` so the
    caller can reuse them without recomputing.
    """
    runtime_object = command.runtime_object
    invocation = command.invocation

    blueprint_context = _payload_mapping(command.payload, "context")
    if blueprint_context is None:
        raise CapabilityInvocationError(
            "payload.context must be a mapping when provided",
            code="invalid_blueprint_context",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    blueprint_constraints = _payload_mapping(command.payload, "constraints")
    if blueprint_constraints is None:
        raise CapabilityInvocationError(
            "payload.constraints must be a mapping when provided",
            code="invalid_blueprint_constraints",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    ce_asset_refs = _chief_engineer_asset_refs(runtime_object)
    target_asset_mount = str(capability.metadata.get("asset_mount") or "").strip()
    target_asset_ref = _asset_mount_ref(runtime_object, target_asset_mount) if target_asset_mount else ""
    blueprint_context.update(
        {
            "role_invocation_id": invocation.invocation_id,
            "role_payload_ref": invocation.payload_ref,
            "role_fingerprint_ref": invocation.fingerprint_ref,
            "role_capability_id": capability.capability_id,
            "asset_refs": ce_asset_refs,
            "diff_map_archive_requires_blueprint_ref": True,
        }
    )
    if target_asset_mount:
        blueprint_context["target_asset_mount"] = target_asset_mount
        blueprint_context["target_asset_ref"] = target_asset_ref
    try:
        from polaris.cells.chief_engineer.blueprint.public.contracts import GenerateTaskBlueprintCommandV1

        blueprint_command = GenerateTaskBlueprintCommandV1(
            task_id=_payload_string(command.payload, "task_id", runtime_object.identity.task_id or ""),
            workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
            objective=_payload_string(command.payload, "objective"),
            run_id=_payload_string(command.payload, "run_id", runtime_object.identity.run_id or "") or None,
            constraints=blueprint_constraints,
            context=blueprint_context,
        )
    except (TypeError, ValueError) as exc:
        raise CapabilityInvocationError(
            str(exc),
            code="invalid_blueprint_command",
            owner_cell=capability.owner_cell,
            capability_available=True,
        ) from exc
    return blueprint_command, ce_asset_refs, target_asset_mount, target_asset_ref


def _build_blueprint_metadata(
    blueprint_result: TaskBlueprintResultV1,
    ce_asset_refs: dict[str, str],
    target_asset_mount: str,
    target_asset_ref: str,
) -> tuple[str, dict[str, Any]]:
    """Build the ``(blueprint_ref, metadata)`` pair verbatim from the legacy branch."""
    blueprint_ref_id = blueprint_result.blueprint_id or blueprint_result.task_id
    blueprint_ref = f"chief_engineer.blueprint:blueprint:{blueprint_ref_id}"
    metadata: dict[str, Any] = {
        "blueprint_id": blueprint_result.blueprint_id or "",
        "blueprint_path": blueprint_result.blueprint_path or "",
        "summary": blueprint_result.summary,
        "recommendations": tuple(blueprint_result.recommendations),
        "risks": tuple(blueprint_result.risks),
        "asset_refs": ce_asset_refs,
        "diff_map_archive_ref": f"{ce_asset_refs['diff_map_archive']}:{blueprint_ref_id}"
        if ce_asset_refs["diff_map_archive"]
        else "",
        "arch_memo_ref": f"{ce_asset_refs['arch_constraint_memo']}:{blueprint_ref_id}"
        if ce_asset_refs["arch_constraint_memo"]
        else "",
    }
    if target_asset_mount:
        metadata["target_asset_mount"] = target_asset_mount
        metadata["target_asset_ref"] = target_asset_ref
    return blueprint_ref, metadata


class BlueprintGenerationHandler:
    """:class:`CapabilityHandler` for ``generate_diff_specification`` / ``record_arch_memo``."""

    def validate(self, command: ExecuteRoleCapabilityInvocationCommandV1) -> None:
        capability = _resolve_capability(command)
        _build_blueprint_command(command, capability)

    def invoke(
        self,
        command: ExecuteRoleCapabilityInvocationCommandV1,
        deps: CapabilityDeps,
    ) -> object:
        capability = _resolve_capability(command)
        blueprint_command, _, _, _ = _build_blueprint_command(command, capability)

        blueprint_service = deps.blueprint_service
        try:
            if blueprint_service is None:
                from polaris.cells.chief_engineer.blueprint.public.service import generate_task_blueprint

                blueprint_result: TaskBlueprintResultV1 = generate_task_blueprint(blueprint_command)
            else:
                blueprint_result = cast(
                    "TaskBlueprintResultV1",
                    blueprint_service.generate_task_blueprint(blueprint_command),
                )
        except Exception as exc:
            raise CapabilityInvocationError(
                str(exc),
                code="blueprint_generation_failed",
                owner_cell=capability.owner_cell,
                capability_available=True,
            ) from exc
        return blueprint_result

    def map_result(
        self,
        raw: object,
        command: ExecuteRoleCapabilityInvocationCommandV1,
    ) -> RoleCapabilityInvocationResultV1:
        blueprint_result = cast("TaskBlueprintResultV1", raw)
        runtime_object = command.runtime_object
        invocation = command.invocation
        role_id = runtime_object.identity.role_id
        capability = _resolve_capability(command)

        ce_asset_refs = _chief_engineer_asset_refs(runtime_object)
        target_asset_mount = str(capability.metadata.get("asset_mount") or "").strip()
        target_asset_ref = _asset_mount_ref(runtime_object, target_asset_mount) if target_asset_mount else ""

        blueprint_ref, metadata = _build_blueprint_metadata(
            blueprint_result,
            ce_asset_refs,
            target_asset_mount,
            target_asset_ref,
        )
        if not blueprint_result.ok:
            return RoleCapabilityInvocationResultV1(
                ok=False,
                invocation_id=invocation.invocation_id,
                role_id=role_id,
                capability_id=capability.capability_id,
                command_contract=capability.contract_name,
                allowed=False,
                owner_cell=capability.owner_cell,
                payload_ref=blueprint_ref,
                result_ref=blueprint_ref,
                task_id=blueprint_result.task_id,
                status=blueprint_result.status,
                metadata=metadata,
                error_code="blueprint_generation_rejected",
                error_message=blueprint_result.summary or "blueprint generation was rejected",
            )
        return RoleCapabilityInvocationResultV1(
            ok=True,
            invocation_id=invocation.invocation_id,
            role_id=role_id,
            capability_id=capability.capability_id,
            command_contract=capability.contract_name,
            allowed=True,
            owner_cell=capability.owner_cell,
            payload_ref=blueprint_ref,
            result_ref=blueprint_ref,
            task_id=blueprint_result.task_id,
            status=blueprint_result.status,
            metadata=metadata,
        )
