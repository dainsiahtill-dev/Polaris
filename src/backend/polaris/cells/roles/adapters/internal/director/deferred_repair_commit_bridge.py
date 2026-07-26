"""Adapter bridge: bind DEO runtime deps and commit deferred materialization repairs.

Physical mutation ports are composed on the adapter boundary; the commit itself
is owned by roles.kernel public DEO followup.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from polaris.cells.roles.adapters.public import (
    create_director_directed_effect_mutation_port,
    create_director_effect_policy_snapshot_port,
)
from polaris.cells.roles.kernel.public import DirectedEffectRuntimeDependenciesV1
from polaris.cells.roles.kernel.public.deferred_repair_commit_service import (
    commit_deferred_director_repair_tool_results,
)
from polaris.cells.roles.kernel.public.directed_effect_service import create_directed_effect_fence_ports
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)

logger = logging.getLogger(__name__)

_CAPABILITY_TOKEN_KEYS: tuple[str, ...] = (
    "job_token",
    "control_plane_job_token",
    "capability_token",
)


def _has_deferred_repair_payload(tool_results: Sequence[Mapping[str, Any]]) -> bool:
    for item in tool_results:
        if not isinstance(item, Mapping):
            continue
        result = item.get("result")
        if not isinstance(result, Mapping):
            continue
        if result.get("deferred_request") is not None:
            return True
        status = str(result.get("status") or "").strip()
        if status in {"deferred_repair_effects_pending", "deferred_command_effect_pending"}:
            return True
    return False


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _capability_token_from_context(context: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(context, Mapping):
        return None
    containers: list[Mapping[str, Any]] = [context]
    metadata = _mapping(context.get("metadata"))
    if metadata is not None:
        containers.append(metadata)
    override = _mapping(context.get("context_override"))
    if override is not None:
        containers.append(override)
    for container in containers:
        for key in _CAPABILITY_TOKEN_KEYS:
            token = _mapping(container.get(key))
            if token is not None and str(token.get("token_id") or "").strip():
                return dict(token)
        envelope = _mapping(container.get("execution_envelope")) or _mapping(
            container.get("director_execution_envelope")
        )
        if envelope is not None:
            authorization = _mapping(envelope.get("authorization")) or {}
            envelope_hash = str(envelope.get("envelope_hash") or "").strip()
            token_id = str(authorization.get("capability_token_ref") or envelope.get("envelope_id") or "").strip()
            if token_id or envelope_hash:
                return {
                    "token_id": token_id or f"execution-envelope:{(envelope_hash or 'materialization')[:16]}",
                    "execution_envelope_hash": envelope_hash,
                    "allowed_commands": authorization.get("allowed_commands") or (),
                    "allowed_paths": authorization.get("allowed_write_paths")
                    or authorization.get("target_files")
                    or (),
                    "capability_audit": {"ok": True},
                    "source": "director.execution_envelope.authorization",
                }
    return None


def _capability_scope_from_context(context: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not isinstance(context, Mapping):
        return ()
    candidates: list[str] = []
    containers: list[Mapping[str, Any]] = [context]
    metadata = _mapping(context.get("metadata"))
    if metadata is not None:
        containers.append(metadata)
    for container in containers:
        for key in (
            "allowed_paths",
            "allowed_write_paths",
            "target_files",
            "scope_paths",
            "capability_scope",
        ):
            raw = container.get(key)
            if isinstance(raw, (list, tuple)):
                candidates.extend(str(item) for item in raw if str(item or "").strip())
        token = _capability_token_from_context(container)
        if token:
            for key in ("allowed_paths", "allowed_write_paths", "target_files", "allowed_scope"):
                raw = token.get(key)
                if isinstance(raw, (list, tuple)):
                    candidates.extend(str(item) for item in raw if str(item or "").strip())
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        path = str(item or "").replace("\\", "/").strip().strip("/")
        if not path or path in seen:
            continue
        seen.add(path)
        cleaned.append(path)
    return tuple(cleaned)


async def commit_materialization_deferred_repairs(
    *,
    workspace: str,
    tool_results: Sequence[Mapping[str, Any]],
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None,
    execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None,
    turn_id: str | None = None,
    context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Commit deferred materialization repair results when attempt authority exists.

    Returns physical write receipts, or [] when nothing deferred / no authority.
    Failures are logged and returned as empty so Director can continue fail-closed
    without bypass writers.
    """

    if not tool_results or not _has_deferred_repair_payload(tool_results):
        return []
    if type(execution_attempt) is not TaskRuntimeExecutionAttemptIdentityV1:
        logger.info(
            "Skipping deferred materialization commit: execution_attempt missing workspace=%s",
            workspace,
        )
        return []
    if type(execution_attempt_authority) is not TaskRuntimeExecutionAttemptAuthorityV1:
        logger.info(
            "Skipping deferred materialization commit: execution_attempt_authority missing workspace=%s",
            workspace,
        )
        return []

    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        return []

    try:
        policy_port = create_director_effect_policy_snapshot_port(workspace_token)
        fence_ports = create_directed_effect_fence_ports()
        mutation_port = create_director_directed_effect_mutation_port(
            workspace=workspace_token,
            policy_snapshot_port=policy_port,
            fence_consume_port=fence_ports.consume,
        )
        directed_effect_runtime = DirectedEffectRuntimeDependenciesV1(
            policy_snapshot_port=policy_port,
            fence_admin_port=fence_ports.admin,
            mutation_port=mutation_port,
        )
        return await commit_deferred_director_repair_tool_results(
            workspace=workspace_token,
            tool_results=tool_results,
            execution_attempt=execution_attempt,
            execution_attempt_authority=execution_attempt_authority,
            directed_effect_runtime=directed_effect_runtime,
            turn_id=turn_id,
            capability_scope=_capability_scope_from_context(context),
            capability_token=_capability_token_from_context(context),
        )
    except Exception as exc:  # noqa: BLE001 - materialization commit is best-effort fail-closed
        logger.error(
            "Deferred materialization DEO commit failed: workspace=%s error_type=%s error=%s",
            workspace_token,
            type(exc).__name__,
            exc,
        )
        return []


__all__ = ["commit_materialization_deferred_repairs"]
