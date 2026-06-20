"""``boundary_validation`` capability handler.

Identity tuple::

    ("validate_cell_boundary_change", "architect.design", "GenerateArchitectureDesignCommandV1")

This is a VERBATIM re-shaping of the legacy ``is_architect_boundary_validation``
arm of ``execute_role_capability_invocation`` onto the
:class:`~polaris.cells.roles.runtime.internal.capability.protocol.CapabilityHandler`
surface. It is the LARGEST and most NON-UNIFORM branch: it chains three owner-cell
stages — ``policy.permission`` evaluation → ``policy.workspace_guard`` batch check
→ ``architect.design`` generation (under a wall-clock timeout) — and emits
thirteen distinct ``error_code`` literals.

The three :class:`CapabilityHandler` methods reproduce the legacy control flow
without re-ordering any guard:

* :meth:`validate` reproduces the *pure*, pre-RPC payload rejections that the
  legacy branch performs BEFORE the first owner-cell call (permission
  evaluation): the ``context`` / ``constraints`` mapping guards
  (``invalid_architect_boundary_context`` / ``invalid_architect_boundary_constraints``),
  the ``changed_paths`` null + empty guards (``invalid_architect_boundary_changed_paths``),
  the ``target_cell`` guard (``invalid_architect_boundary_target_cell``), and the
  ``EvaluatePermissionCommandV1`` construction guard (``invalid_permission_command``).
* :meth:`invoke` performs the three side-effectful owner-cell stages in legacy
  order, raising :class:`CapabilityInvocationError` with the matching legacy
  ``error_code`` (``permission_evaluation_failed`` / ``permission_denied`` /
  ``workspace_guard_failed`` / ``workspace_guard_denied`` /
  ``invalid_architect_design_command`` / ``architect_design_timeout`` /
  ``architect_design_failed``). The ``permission_denied`` /
  ``workspace_guard_denied`` / ``architect_design_timeout`` rejections carry the
  exact ``_capability_available_metadata`` payload the legacy branch attached, so
  the dispatcher's single ``CapabilityInvocationError`` catch renders the failure
  result byte-identically.
* :meth:`map_result` builds the not-ok (``architect_design_rejected``) and the
  success :class:`RoleCapabilityInvocationResultV1` verbatim from the legacy tail
  (lines ~2129-2166), reading the design result + accumulated ``guard_metadata``
  carried out of :meth:`invoke` via :class:`_BoundaryInvokeResult`.

The pre-RPC payload validation + permission-command construction is a pure
function of ``command`` (helper :func:`_validate_boundary_payload`), so
:meth:`validate` and :meth:`invoke` reproduce it identically without sharing
mutable state. The ``boundary_context`` mutation (legacy lines ~2068-2079) is
performed inside :meth:`invoke` only, after the guard stages pass, exactly as the
legacy branch does.
"""

from __future__ import annotations

from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from polaris.cells.roles.runtime.internal.capability.errors import CapabilityInvocationError
from polaris.cells.roles.runtime.public.capability_commands import (
    _capability_available_metadata,
    _check_workspace_guard_paths,
    _payload_mapping,
    _payload_string,
    _payload_string_tuple,
    _run_with_timeout,
)
from polaris.cells.roles.runtime.public.contracts import RoleCapabilityInvocationResultV1

if TYPE_CHECKING:
    from collections.abc import Callable

    from polaris.cells.architect.design.public.contracts import ArchitectureDesignResultV1
    from polaris.cells.policy.permission.public.contracts import (
        EvaluatePermissionCommandV1,
        PermissionDecisionResultV1,
    )
    from polaris.cells.roles.runtime.internal.capability.deps import CapabilityDeps
    from polaris.cells.roles.runtime.public.contracts import (
        ExecuteRoleCapabilityInvocationCommandV1,
        RoleCapabilityDescriptor,
    )


@dataclass(frozen=True)
class _BoundaryValidatedPayload:
    """Pure pre-RPC validation product of the boundary-validation branch.

    Carries the validated payload values plus the resolved module-level
    ``evaluate_permission`` callable (legacy lines ~1989-1998 bundle the
    contract + service import with the command construction inside the
    ``invalid_permission_command`` try; this dataclass preserves that scope while
    keeping a single source of the construction logic for ``validate``/``invoke``).
    """

    boundary_context: dict[str, Any]
    boundary_constraints: dict[str, Any]
    changed_paths: tuple[str, ...]
    target_cell: str
    permission_command: EvaluatePermissionCommandV1
    evaluate_permission_fn: Callable[[EvaluatePermissionCommandV1], PermissionDecisionResultV1]


@dataclass(frozen=True)
class _BoundaryInvokeResult:
    """Owner-cell product of :meth:`BoundaryValidationHandler.invoke`.

    ``design_result`` is the raw ``architect.design`` result; ``guard_metadata``
    is the accumulated permission + workspace-guard metadata block the legacy
    branch folds into both the rejected and the success result (legacy lines
    ~2051-2057 / ~2130-2135).
    """

    design_result: ArchitectureDesignResultV1
    guard_metadata: dict[str, Any]


def _resolve_capability(command: ExecuteRoleCapabilityInvocationCommandV1) -> RoleCapabilityDescriptor:
    """Re-fetch the mounted capability descriptor for the invoked capability."""
    return command.runtime_object.capability_ports.get(command.invocation.capability_id)


def _validate_boundary_payload(
    command: ExecuteRoleCapabilityInvocationCommandV1,
    capability: RoleCapabilityDescriptor,
) -> _BoundaryValidatedPayload:
    """Reproduce the pure, pre-RPC payload rejections + permission command build.

    Mirrors legacy lines ~1929-1998 byte-for-byte, raising
    :class:`CapabilityInvocationError` with the legacy ``error_code`` literals on
    each pre-invoke rejection path. Performs NO owner-cell RPC and does NOT mutate
    ``boundary_context`` (that mutation is done by :meth:`invoke` after the guard
    stages pass).
    """
    runtime_object = command.runtime_object
    invocation = command.invocation
    role_id = runtime_object.identity.role_id

    boundary_context = _payload_mapping(command.payload, "context")
    if boundary_context is None:
        raise CapabilityInvocationError(
            "payload.context must be a mapping when provided",
            code="invalid_architect_boundary_context",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    boundary_constraints = _payload_mapping(command.payload, "constraints")
    if boundary_constraints is None:
        raise CapabilityInvocationError(
            "payload.constraints must be a mapping when provided",
            code="invalid_architect_boundary_constraints",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    changed_paths = _payload_string_tuple(command.payload, "changed_paths")
    if changed_paths is None:
        raise CapabilityInvocationError(
            "payload.changed_paths must be a sequence of strings when provided",
            code="invalid_architect_boundary_changed_paths",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )
    if not changed_paths:
        raise CapabilityInvocationError(
            "payload.changed_paths must include at least one changed path",
            code="invalid_architect_boundary_changed_paths",
            owner_cell=capability.owner_cell,
            capability_available=False,
            metadata=_capability_available_metadata(
                capability.capability_id,
                {"required_field": "changed_paths"},
            ),
        )
    target_cell = _payload_string(command.payload, "target_cell")
    if not target_cell:
        raise CapabilityInvocationError(
            "payload.target_cell must be a non-empty string",
            code="invalid_architect_boundary_target_cell",
            owner_cell=capability.owner_cell,
            capability_available=True,
        )

    permission_context = {
        "resource_type": "api",
        "task_id": runtime_object.identity.task_id or "",
        "session_id": runtime_object.identity.session_id or "",
        "request_id": invocation.invocation_id,
        "capability_id": capability.capability_id,
        "target_cell": target_cell,
        "role_payload_ref": invocation.payload_ref,
        "role_fingerprint_ref": invocation.fingerprint_ref,
    }
    try:
        from polaris.cells.policy.permission.public.contracts import EvaluatePermissionCommandV1
        from polaris.cells.policy.permission.public.service import evaluate_permission

        permission_command = EvaluatePermissionCommandV1(
            role=role_id,
            action="execute",
            resource="architect.design:validate_cell_boundary_change",
            workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
            context=permission_context,
        )
    except (TypeError, ValueError) as exc:
        raise CapabilityInvocationError(
            str(exc),
            code="invalid_permission_command",
            owner_cell=capability.owner_cell,
            capability_available=True,
        ) from exc
    return _BoundaryValidatedPayload(
        boundary_context=boundary_context,
        boundary_constraints=boundary_constraints,
        changed_paths=changed_paths,
        target_cell=target_cell,
        permission_command=permission_command,
        evaluate_permission_fn=evaluate_permission,
    )


class BoundaryValidationHandler:
    """:class:`CapabilityHandler` for ``validate_cell_boundary_change``."""

    def validate(self, command: ExecuteRoleCapabilityInvocationCommandV1) -> None:
        capability = _resolve_capability(command)
        _validate_boundary_payload(command, capability)

    def invoke(
        self,
        command: ExecuteRoleCapabilityInvocationCommandV1,
        deps: CapabilityDeps,
    ) -> object:
        capability = _resolve_capability(command)
        validated = _validate_boundary_payload(command, capability)

        boundary_context = validated.boundary_context
        boundary_constraints = validated.boundary_constraints
        changed_paths = validated.changed_paths
        target_cell = validated.target_cell
        permission_command = validated.permission_command

        runtime_object = command.runtime_object
        invocation = command.invocation

        permission_service = deps.permission_service
        try:
            if permission_service is None:
                permission_result = validated.evaluate_permission_fn(permission_command)
            else:
                permission_result = cast(
                    "PermissionDecisionResultV1",
                    permission_service.evaluate_permission(permission_command),
                )
        except Exception as exc:
            raise CapabilityInvocationError(
                str(exc),
                code="permission_evaluation_failed",
                owner_cell=capability.owner_cell,
                capability_available=True,
            ) from exc

        permission_metadata: dict[str, Any] = {
            "permission_allowed": permission_result.allowed,
            "permission_reason": permission_result.reason,
            "permission_matched_policy": permission_result.matched_policy or "",
        }
        if not permission_result.allowed:
            raise CapabilityInvocationError(
                permission_result.reason or "permission denied",
                code="permission_denied",
                owner_cell=capability.owner_cell,
                capability_available=False,
                metadata=_capability_available_metadata(capability.capability_id, permission_metadata),
            )

        try:
            guard_allowed, checked_paths, denied_path, guard_reason = _check_workspace_guard_paths(
                paths=changed_paths,
                operation=_payload_string(command.payload, "operation", "write"),
                workspace_guard_service=deps.workspace_guard_service,
            )
        except Exception as exc:
            raise CapabilityInvocationError(
                str(exc),
                code="workspace_guard_failed",
                owner_cell=capability.owner_cell,
                capability_available=True,
            ) from exc
        guard_metadata: dict[str, Any] = {
            **permission_metadata,
            "workspace_guard_allowed": guard_allowed,
            "checked_paths": checked_paths,
            "denied_path": denied_path,
            "guard_reason": guard_reason,
        }
        if not guard_allowed:
            raise CapabilityInvocationError(
                guard_reason or "workspace guard denied mutation",
                code="workspace_guard_denied",
                owner_cell=capability.owner_cell,
                capability_available=False,
                metadata=_capability_available_metadata(capability.capability_id, guard_metadata),
            )

        boundary_context.update(
            {
                "target_cell": target_cell,
                "changed_paths": changed_paths,
                "role_invocation_id": invocation.invocation_id,
                "role_payload_ref": invocation.payload_ref,
                "role_fingerprint_ref": invocation.fingerprint_ref,
                "role_capability_id": capability.capability_id,
                "permission_ref": "policy.permission:decision",
                "workspace_guard_ref": "policy.workspace_guard:decision",
            }
        )
        # The ``architect.design`` boundary invoker is supplied through the typed
        # ``CapabilityDeps`` seam by the composition root. The handler owns NO
        # runtime-scope ``architect.design`` import: command construction +
        # invocation are both encapsulated behind ``run_boundary_design`` so the
        # ``roles.runtime`` → ``architect.design`` cell edge stays absent. When the
        # invoker is unwired the capability cannot run, so we fail closed.
        architect_design_service = deps.architect_design_service
        if architect_design_service is None:
            raise CapabilityInvocationError(
                "architect.design boundary invoker is not wired",
                code="architect_design_failed",
                owner_cell=capability.owner_cell,
                capability_available=False,
                metadata=_capability_available_metadata(capability.capability_id, guard_metadata),
            )

        workspace = _payload_string(command.payload, "workspace", runtime_object.identity.workspace)
        objective = _payload_string(command.payload, "objective")
        timeout_seconds = float(command.payload.get("timeout_seconds", 30.0))
        try:
            design_result = cast(
                "ArchitectureDesignResultV1",
                _run_with_timeout(
                    lambda: architect_design_service.run_boundary_design(
                        workspace=workspace,
                        objective=objective,
                        constraints=boundary_constraints,
                        context=boundary_context,
                    ),
                    timeout_seconds,
                ),
            )
        except FutureTimeoutError as exc:
            raise CapabilityInvocationError(
                f"architect design timed out after {timeout_seconds:g}s",
                code="architect_design_timeout",
                owner_cell=capability.owner_cell,
                capability_available=False,
                metadata=_capability_available_metadata(capability.capability_id, guard_metadata),
            ) from exc
        except (TypeError, ValueError) as exc:
            raise CapabilityInvocationError(
                str(exc),
                code="invalid_architect_design_command",
                owner_cell=capability.owner_cell,
                capability_available=True,
            ) from exc
        except Exception as exc:
            raise CapabilityInvocationError(
                str(exc),
                code="architect_design_failed",
                owner_cell=capability.owner_cell,
                capability_available=True,
            ) from exc

        return _BoundaryInvokeResult(design_result=design_result, guard_metadata=guard_metadata)

    def map_result(
        self,
        raw: object,
        command: ExecuteRoleCapabilityInvocationCommandV1,
    ) -> RoleCapabilityInvocationResultV1:
        invoke_result = cast("_BoundaryInvokeResult", raw)
        design_result = invoke_result.design_result
        guard_metadata = invoke_result.guard_metadata

        runtime_object = command.runtime_object
        invocation = command.invocation
        role_id = runtime_object.identity.role_id
        capability = _resolve_capability(command)

        result_ref = f"architect.design:boundary-validation:{design_result.design_id}"
        metadata: dict[str, Any] = {
            **guard_metadata,
            "design_id": design_result.design_id,
            "summary": design_result.summary,
            "recommendation_paths": tuple(design_result.recommendation_paths),
        }
        if not design_result.ok:
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
                status=design_result.status,
                metadata=metadata,
                error_code="architect_design_rejected",
                error_message=design_result.summary or "architect design rejected boundary change",
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
            status=design_result.status,
            metadata=metadata,
        )
