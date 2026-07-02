"""``pm_critical_path`` capability handler.

Identity tuple::

    ("evaluate_critical_path", "runtime.task_market", "QueryTaskMarketStatusV1")

This is a verbatim extraction of the ``is_pm_critical_path`` dispatcher arm of
``execute_role_capability_invocation`` onto the
:class:`~polaris.cells.roles.runtime.internal.capability.protocol.CapabilityHandler`
surface:

* :meth:`validate` reproduces the single pre-invoke rejection path — the
  :class:`QueryTaskMarketStatusV1` construction guard
  (``invalid_task_market_status_query``) — raising
  :class:`CapabilityInvocationError` instead of returning a failure result.
* :meth:`invoke` performs the task-market status query exactly as the extracted
  branch: ``deps.task_market_service`` when provided, else the
  ``runtime.task_market`` module-level public ``get_task_market_service()``
  service when the port is ``None``; it raises ``task_market_status_query_failed``
  on any downstream exception from ``query_status``.
* :meth:`map_result` derives the open / blocked / dependency / failed-stage /
  projection bundles from the raw status result and builds the ``EVALUATED``
  success :class:`RoleCapabilityInvocationResultV1` verbatim.

The status query is a pure function of ``command`` (helper
:func:`_build_status_query`), so :meth:`validate` and :meth:`invoke` rebuild it
identically without sharing mutable state; :meth:`map_result` likewise recomputes
the derived bundles deterministically from the raw result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from polaris.cells.roles.runtime.internal.capability.errors import CapabilityInvocationError
from polaris.cells.roles.runtime.public.capability_commands import (
    _asset_mount_ref,
    _mapping_string_tuple,
    _payload_string,
)
from polaris.cells.roles.runtime.public.contracts import RoleCapabilityInvocationResultV1

if TYPE_CHECKING:
    from polaris.cells.roles.runtime.internal.capability.deps import CapabilityDeps
    from polaris.cells.roles.runtime.public.contracts import (
        ExecuteRoleCapabilityInvocationCommandV1,
        RoleCapabilityDescriptor,
    )
    from polaris.cells.runtime.task_market.public import (
        QueryTaskMarketStatusV1,
        TaskMarketStatusResultV1,
    )


def _resolve_capability(command: ExecuteRoleCapabilityInvocationCommandV1) -> RoleCapabilityDescriptor:
    """Re-fetch the mounted capability descriptor for the invoked capability."""
    return command.runtime_object.capability_ports.get(command.invocation.capability_id)


def _build_status_query(
    command: ExecuteRoleCapabilityInvocationCommandV1,
    capability: RoleCapabilityDescriptor,
) -> QueryTaskMarketStatusV1:
    """Construct the ``QueryTaskMarketStatusV1`` from ``command``.

    Mirrors the extracted branch's query construction byte-for-byte. Raises
    :class:`CapabilityInvocationError` with the stable ``error_code`` literal on
    the construction rejection path.
    """
    runtime_object = command.runtime_object
    try:
        from polaris.cells.runtime.task_market.public import QueryTaskMarketStatusV1

        query = QueryTaskMarketStatusV1(
            workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
            stage=_payload_string(command.payload, "stage") or None,
            status=_payload_string(command.payload, "status") or None,
            limit=int(command.payload.get("limit", 200)),
            include_payload=bool(command.payload.get("include_payload", True)),
        )
    except (TypeError, ValueError) as exc:
        raise CapabilityInvocationError(
            str(exc),
            code="invalid_task_market_status_query",
            owner_cell=capability.owner_cell,
            capability_available=True,
        ) from exc
    return query


class PmCriticalPathHandler:
    """:class:`CapabilityHandler` for ``evaluate_critical_path``."""

    def validate(self, command: ExecuteRoleCapabilityInvocationCommandV1) -> None:
        capability = _resolve_capability(command)
        _build_status_query(command, capability)

    def invoke(
        self,
        command: ExecuteRoleCapabilityInvocationCommandV1,
        deps: CapabilityDeps,
    ) -> object:
        capability = _resolve_capability(command)
        query = _build_status_query(command, capability)

        task_market_service = deps.task_market_service
        try:
            if task_market_service is None:
                from polaris.cells.runtime.task_market.public import get_task_market_service

                status_result: TaskMarketStatusResultV1 = get_task_market_service().query_status(query)
            else:
                status_result = cast("TaskMarketStatusResultV1", task_market_service.query_status(query))
        except Exception as exc:
            raise CapabilityInvocationError(
                str(exc),
                code="task_market_status_query_failed",
                owner_cell=capability.owner_cell,
                capability_available=True,
            ) from exc
        return status_result

    def map_result(
        self,
        raw: object,
        command: ExecuteRoleCapabilityInvocationCommandV1,
    ) -> RoleCapabilityInvocationResultV1:
        status_result = cast("TaskMarketStatusResultV1", raw)
        runtime_object = command.runtime_object
        invocation = command.invocation
        role_id = runtime_object.identity.role_id
        capability = _resolve_capability(command)

        terminal_statuses = {"resolved", "completed", "acknowledged", "cancelled", "superseded"}
        blocked_statuses = {"failed", "dead_letter", "blocked", "cancel_requested", "needs_revalidation"}
        open_items = tuple(
            item for item in status_result.items if str(item.get("status") or "").lower() not in terminal_statuses
        )
        blocked_task_ids = tuple(
            str(item.get("task_id") or "").strip()
            for item in open_items
            if str(item.get("status") or "").lower() in blocked_statuses and str(item.get("task_id") or "").strip()
        )
        open_task_ids = tuple(
            str(item.get("task_id") or "").strip() for item in open_items if str(item.get("task_id") or "").strip()
        )
        dependency_edges = tuple(
            {"task_id": task_id, "depends_on": depends_on}
            for item in status_result.items
            if (task_id := str(item.get("task_id") or "").strip())
            if (depends_on := _mapping_string_tuple(item, "depends_on"))
        )
        failed_stages = tuple(
            {
                "task_id": task_id,
                "stage": failed_stage,
                "reason": str(item.get("failure_reason") or item.get("reason") or "").strip(),
            }
            for item in status_result.items
            if (task_id := str(item.get("task_id") or "").strip())
            if (failed_stage := str(item.get("failed_stage") or item.get("stage") or "").strip())
            if str(item.get("status") or "").lower() in blocked_statuses
        )
        projection_refs = tuple(
            ref
            for item in status_result.items
            if (ref := str(item.get("projection_ref") or item.get("runtime_projection_ref") or "").strip())
        )
        asset_refs = {
            "task_graph": _asset_mount_ref(runtime_object, "TaskGraph"),
            "runtime_projection_state": _asset_mount_ref(runtime_object, "RuntimeProjectionState"),
            "open_loop_registry": _asset_mount_ref(runtime_object, "OpenLoopRegistry"),
        }
        result_ref = f"runtime.task_market:critical-path:{invocation.invocation_id}"
        metadata: dict[str, Any] = {
            "total_tasks": status_result.total,
            "counts": dict(status_result.counts),
            "open_task_ids": open_task_ids,
            "blocked_task_ids": blocked_task_ids,
            "open_task_count": len(open_task_ids),
            "blocked_task_count": len(blocked_task_ids),
            "dependency_edges": dependency_edges,
            "failed_stages": failed_stages,
            "projection_refs": projection_refs,
            "asset_refs": asset_refs,
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
            status="EVALUATED",
            metadata=metadata,
        )
