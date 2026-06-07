"""Public service exports for `policy.permission` cell."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

from polaris.cells.policy.permission.internal.permission_service import (
    DecisionContext,
    PermissionService,
    get_permission_service,
)
from polaris.cells.policy.permission.public.contracts import (
    EvaluatePermissionCommandV1,
    PermissionDecisionResultV1,
    PermissionDeniedEventV1,
    PermissionPolicyError,
    QueryPermissionMatrixV1,
)
from polaris.cells.roles.profile.public.service import (
    Action,
    Resource,
    ResourceType,
    Subject,
    SubjectType,
)

_T = TypeVar("_T")


def _run_async(factory: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    def _runner() -> _T:
        return asyncio.run(factory())

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(_runner).result()


def _resource_type_from_command(command: EvaluatePermissionCommandV1) -> ResourceType:
    requested = str(command.context.get("resource_type") or "").strip().lower()
    if requested:
        return ResourceType(requested)
    if command.action == Action.EXECUTE.value:
        return ResourceType.API
    if command.action in {Action.WRITE.value, Action.DELETE.value, Action.READ.value}:
        return ResourceType.FILE
    return ResourceType.API


def evaluate_permission(command: EvaluatePermissionCommandV1) -> PermissionDecisionResultV1:
    """Evaluate a permission command through the public policy contract."""
    if not isinstance(command, EvaluatePermissionCommandV1):
        raise TypeError("command must be an EvaluatePermissionCommandV1")

    async def _evaluate() -> PermissionDecisionResultV1:
        service = await get_permission_service(command.workspace)
        result = await service.check_permission(
            subject=Subject(type=SubjectType.ROLE, id=command.role),
            resource=Resource(
                type=_resource_type_from_command(command),
                pattern=command.resource,
                path=command.resource,
            ),
            action=Action(command.action),
            context=DecisionContext(
                task_id=str(command.context.get("task_id") or "") or None,
                session_id=str(command.context.get("session_id") or "") or None,
                request_id=str(command.context.get("request_id") or "") or None,
                workspace=command.workspace,
                metadata=dict(command.context),
            ),
        )
        matched_policy = result.matched_policies[0] if result.matched_policies else None
        return PermissionDecisionResultV1(
            allowed=result.allowed,
            role=command.role,
            action=command.action,
            resource=command.resource,
            reason=result.reason,
            matched_policy=matched_policy,
            context={
                "decision": result.decision,
                "matched_policies": tuple(result.matched_policies),
            },
        )

    try:
        return _run_async(_evaluate)
    except ValueError as exc:
        raise PermissionPolicyError(str(exc), code="invalid_permission_command") from exc


__all__ = [
    "DecisionContext",
    "EvaluatePermissionCommandV1",
    "PermissionDecisionResultV1",
    "PermissionDeniedEventV1",
    "PermissionPolicyError",
    "PermissionService",
    "QueryPermissionMatrixV1",
    "evaluate_permission",
    "get_permission_service",
]
