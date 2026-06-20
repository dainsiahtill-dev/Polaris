"""``task_market_dispatch`` capability handler.

Identity tuple::

    ("dispatch_task_to_market", "runtime.task_market", "PublishTaskWorkItemCommandV1")

This is a VERBATIM re-shaping of the legacy ``is_not_task_market_dispatch`` arm of
``execute_role_capability_invocation`` onto the
:class:`~polaris.cells.roles.runtime.internal.capability.protocol.CapabilityHandler`
surface:

* :meth:`validate` reproduces the four pre-invoke rejection paths — the
  unsupported-contract identity guard (``unsupported_capability_contract``), the
  ``payload.payload`` non-empty-mapping check (``invalid_task_market_payload``),
  the ``payload.metadata`` mapping check (``invalid_task_market_metadata``), and
  the :class:`PublishTaskWorkItemCommandV1` construction guard
  (``invalid_task_market_command``) — raising :class:`CapabilityInvocationError`
  instead of returning a failure result.
* :meth:`invoke` performs the work-item publish exactly as the legacy branch:
  ``deps.task_market_service`` when set, else the ``runtime.task_market``
  module-level ``get_task_market_service()`` public function when the port is
  ``None``; it raises ``task_market_publish_failed`` on any downstream exception.
* :meth:`map_result` builds the success / not-ok :class:`RoleCapabilityInvocationResultV1`
  verbatim, surfacing ``task_market_publish_rejected`` on the not-ok path.

The unsupported-contract guard and the command/metadata construction are pure
functions of ``command`` (helpers :func:`_check_supported_contract` /
:func:`_build_publish_command`), so :meth:`validate` and :meth:`invoke` rebuild
them identically without sharing mutable state; :meth:`map_result` recomputes the
work-item ref deterministically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from polaris.cells.roles.runtime.internal.capability.errors import CapabilityInvocationError
from polaris.cells.roles.runtime.public.capability_commands import (
    _payload_mapping,
    _payload_string,
    _pm_asset_refs,
    _unique_string_tuple,
)
from polaris.cells.roles.runtime.public.contracts import RoleCapabilityInvocationResultV1

if TYPE_CHECKING:
    from polaris.cells.roles.runtime.internal.capability.deps import CapabilityDeps
    from polaris.cells.roles.runtime.public.contracts import (
        ExecuteRoleCapabilityInvocationCommandV1,
        RoleCapabilityDescriptor,
    )
    from polaris.cells.runtime.task_market.public import (
        PublishTaskWorkItemCommandV1,
        TaskWorkItemResultV1,
    )


def _resolve_capability(command: ExecuteRoleCapabilityInvocationCommandV1) -> RoleCapabilityDescriptor:
    """Re-fetch the mounted capability descriptor for the invoked capability."""
    return command.runtime_object.capability_ports.get(command.invocation.capability_id)


def _check_supported_contract(capability: RoleCapabilityDescriptor) -> None:
    """Reproduce the legacy ``is_not_task_market_dispatch`` identity guard.

    Raises :class:`CapabilityInvocationError` with ``unsupported_capability_contract``
    when the resolved capability is not the task-market dispatch identity tuple,
    byte-identically to the legacy ``if is_not_task_market_dispatch:`` arm.
    """
    is_not_task_market_dispatch = (
        capability.capability_id != "dispatch_task_to_market"
        or capability.owner_cell != "runtime.task_market"
        or capability.contract_name != "PublishTaskWorkItemCommandV1"
    )
    if is_not_task_market_dispatch:
        raise CapabilityInvocationError(
            f"capability {capability.capability_id!r} has no latest-only public invocation adapter",
            code="unsupported_capability_contract",
            owner_cell=capability.owner_cell,
        )


def _build_publish_command(
    command: ExecuteRoleCapabilityInvocationCommandV1,
    capability: RoleCapabilityDescriptor,
) -> PublishTaskWorkItemCommandV1:
    """Construct the ``PublishTaskWorkItemCommandV1`` from ``command``.

    Mirrors the legacy branch's payload/metadata guards, metadata-mutation, and
    command construction statements byte-for-byte. Raises
    :class:`CapabilityInvocationError` with the legacy ``error_code`` literals on
    the three pre-invoke rejection paths.
    """
    runtime_object = command.runtime_object
    invocation = command.invocation
    role_id = runtime_object.identity.role_id

    task_payload = _payload_mapping(command.payload, "payload")
    if task_payload is None or not task_payload:
        raise CapabilityInvocationError(
            "payload.payload must be a non-empty mapping",
            code="invalid_task_market_payload",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    task_metadata = _payload_mapping(command.payload, "metadata")
    if task_metadata is None:
        raise CapabilityInvocationError(
            "payload.metadata must be a mapping when provided",
            code="invalid_task_market_metadata",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )

    task_metadata.update(
        {
            "role_invocation_id": invocation.invocation_id,
            "role_payload_ref": invocation.payload_ref,
            "role_fingerprint_ref": invocation.fingerprint_ref,
            "role_capability_id": capability.capability_id,
            "capability_fingerprint_ref": (
                f"roles.runtime:capability-fingerprint:{runtime_object.capability_fingerprint.fingerprint}"
            ),
            "turn_ledger_ref": runtime_object.ledger_binding.turn_ledger_ref,
            "commit_receipt_ref": runtime_object.ledger_binding.commit_receipt_ref or "",
            "runtime_receipt_refs": tuple(runtime_object.ledger_binding.receipt_refs),
            "typed_input_ref": runtime_object.turn_context.typed_input_ref,
            "context_snapshot_ref": runtime_object.turn_context.context_snapshot_ref,
            "turn_task_refs": tuple(runtime_object.turn_context.task_refs),
            "handoff_refs": tuple(runtime_object.turn_context.handoff_refs),
            "profile_ref": runtime_object.profile_binding.profile_ref,
            "tool_policy_ref": runtime_object.profile_binding.tool_policy_ref,
            "prompt_policy_ref": runtime_object.profile_binding.prompt_policy_ref,
            "data_policy_ref": runtime_object.profile_binding.data_policy_ref,
            "task_market_binding_refs": _unique_string_tuple(
                (
                    runtime_object.task_market_binding.work_item_ref,
                    runtime_object.task_market_binding.lease_token_ref,
                )
            ),
            "asset_refs": _pm_asset_refs(runtime_object),
        }
    )

    try:
        from polaris.cells.runtime.task_market.public import PublishTaskWorkItemCommandV1

        publish_command = PublishTaskWorkItemCommandV1(
            workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
            trace_id=_payload_string(
                command.payload,
                "trace_id",
                runtime_object.identity.run_id or invocation.invocation_id,
            ),
            run_id=_payload_string(
                command.payload,
                "run_id",
                runtime_object.identity.run_id or invocation.invocation_id,
            ),
            task_id=_payload_string(command.payload, "task_id", runtime_object.identity.task_id or ""),
            stage=_payload_string(command.payload, "stage", str(capability.metadata.get("target_stage") or "")),
            source_role=role_id,
            payload=task_payload,
            priority=_payload_string(command.payload, "priority", "medium"),
            max_attempts=int(command.payload.get("max_attempts", 3)),
            metadata=task_metadata,
            plan_id=_payload_string(command.payload, "plan_id"),
            plan_revision_id=_payload_string(command.payload, "plan_revision_id"),
            root_task_id=_payload_string(command.payload, "root_task_id"),
            parent_task_id=_payload_string(command.payload, "parent_task_id"),
            is_leaf=bool(command.payload.get("is_leaf", True)),
            depends_on=tuple(command.payload.get("depends_on", ())),
            requirement_digest=_payload_string(command.payload, "requirement_digest"),
            constraint_digest=_payload_string(command.payload, "constraint_digest"),
            summary_ref=_payload_string(command.payload, "summary_ref"),
            superseded_by_revision=_payload_string(command.payload, "superseded_by_revision"),
            change_policy=_payload_string(command.payload, "change_policy", "strict"),
            compensation_group_id=_payload_string(command.payload, "compensation_group_id"),
        )
    except (TypeError, ValueError) as exc:
        raise CapabilityInvocationError(
            str(exc),
            code="invalid_task_market_command",
            owner_cell=capability.owner_cell,
            capability_available=True,
        ) from exc
    return publish_command


class TaskMarketDispatchHandler:
    """:class:`CapabilityHandler` for ``dispatch_task_to_market``."""

    def validate(self, command: ExecuteRoleCapabilityInvocationCommandV1) -> None:
        capability = _resolve_capability(command)
        _check_supported_contract(capability)
        _build_publish_command(command, capability)

    def invoke(
        self,
        command: ExecuteRoleCapabilityInvocationCommandV1,
        deps: CapabilityDeps,
    ) -> object:
        capability = _resolve_capability(command)
        _check_supported_contract(capability)
        publish_command = _build_publish_command(command, capability)

        task_market_service = deps.task_market_service
        try:
            if task_market_service is None:
                from polaris.cells.runtime.task_market.public import get_task_market_service

                task_result: TaskWorkItemResultV1 = get_task_market_service().publish_work_item(publish_command)
            else:
                task_result = cast("TaskWorkItemResultV1", task_market_service.publish_work_item(publish_command))
        except Exception as exc:
            raise CapabilityInvocationError(
                str(exc),
                code="task_market_publish_failed",
                owner_cell=capability.owner_cell,
                capability_available=True,
            ) from exc
        return task_result

    def map_result(
        self,
        raw: object,
        command: ExecuteRoleCapabilityInvocationCommandV1,
    ) -> RoleCapabilityInvocationResultV1:
        task_result = cast("TaskWorkItemResultV1", raw)
        runtime_object = command.runtime_object
        invocation = command.invocation
        role_id = runtime_object.identity.role_id
        capability = _resolve_capability(command)

        task_ref = f"runtime.task_market:work-item:{task_result.task_id}"
        if not task_result.ok:
            rejected_metadata: dict[str, Any] = {
                "task_market_version": task_result.version,
                "task_market_reason": task_result.reason,
            }
            return RoleCapabilityInvocationResultV1(
                ok=False,
                invocation_id=invocation.invocation_id,
                role_id=role_id,
                capability_id=capability.capability_id,
                command_contract=capability.contract_name,
                allowed=False,
                owner_cell=capability.owner_cell,
                payload_ref=task_ref,
                result_ref=task_ref,
                task_id=task_result.task_id,
                status=task_result.status,
                metadata=rejected_metadata,
                error_code="task_market_publish_rejected",
                error_message=task_result.reason or "task market publish was rejected",
            )

        success_metadata: dict[str, Any] = {"task_market_version": task_result.version}
        return RoleCapabilityInvocationResultV1(
            ok=True,
            invocation_id=invocation.invocation_id,
            role_id=role_id,
            capability_id=capability.capability_id,
            command_contract=capability.contract_name,
            allowed=True,
            owner_cell=capability.owner_cell,
            payload_ref=task_ref,
            result_ref=task_ref,
            task_id=task_result.task_id,
            status=task_result.status,
            metadata=success_metadata,
        )
