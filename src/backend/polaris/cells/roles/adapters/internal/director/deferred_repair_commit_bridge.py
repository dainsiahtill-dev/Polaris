"""Adapter bridge: bind DEO runtime deps and commit deferred materialization repairs.

Physical mutation ports are composed on the adapter boundary; the commit itself
is owned by roles.kernel public DEO followup.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from polaris.cells.control_plane.run_ledger.public import stable_hash
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
_EXECUTION_ENVELOPE_KEYS: tuple[str, ...] = (
    "execution_envelope",
    "director_execution_envelope",
    "task_execution_envelope",
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


def _is_lower_sha256(value: Any) -> bool:
    token = str(value or "").strip()
    return len(token) == 64 and all(character in "0123456789abcdef" for character in token)


def _capability_token_from_context(context: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(context, Mapping):
        return None
    root_token_hash = str(context.get("capability_token_hash") or "").strip()
    if not _is_lower_sha256(root_token_hash):
        return None
    containers: list[Mapping[str, Any]] = [context]
    for container_key in ("metadata", "context_override"):
        if container_key not in context:
            continue
        nested = _mapping(context.get(container_key))
        if nested is None:
            return None
        containers.append(nested)

    reserved_keys = (*_CAPABILITY_TOKEN_KEYS, *_EXECUTION_ENVELOPE_KEYS)
    for container in containers:
        if any(key in container and _mapping(container.get(key)) is None for key in reserved_keys):
            return None

    for nested in containers[1:]:
        nested_declares_authority = any(key in nested for key in reserved_keys)
        raw_nested_hash = nested.get("capability_token_hash")
        if raw_nested_hash is None and nested_declares_authority:
            return None
        if raw_nested_hash is not None:
            nested_hash = str(raw_nested_hash or "").strip()
            if not _is_lower_sha256(nested_hash) or nested_hash != root_token_hash:
                return None

    tokens: list[dict[str, Any]] = []
    declared_token_hashes: list[str] = []
    for container in containers:
        aliases = [_mapping(container.get(key)) for key in _CAPABILITY_TOKEN_KEYS]
        if not any(alias is not None for alias in aliases):
            continue
        if any(alias is None for alias in aliases):
            return None
        alias_tokens = [dict(alias) for alias in aliases if alias is not None]
        if any(alias != alias_tokens[0] for alias in alias_tokens[1:]):
            return None
        declared_hash = str(container.get("capability_token_hash") or "").strip()
        if not _is_lower_sha256(declared_hash):
            return None
        tokens.append(alias_tokens[0])
        declared_token_hashes.append(declared_hash)
    if not tokens:
        return None
    if any(token != tokens[0] for token in tokens[1:]):
        return None
    token = tokens[0]
    token_id = str(token.get("token_id") or "").strip()
    if not token_id:
        return None
    token_hash = stable_hash(token)
    if root_token_hash != token_hash or any(declared_hash != token_hash for declared_hash in declared_token_hashes):
        return None
    if token.get("schema_version") != 1 or isinstance(token.get("schema_version"), bool):
        return None
    capability_audit = _mapping(token.get("capability_audit"))
    if capability_audit is None or capability_audit.get("ok") is not True or list(capability_audit.get("issues") or []):
        return None
    allowed_write_paths = token.get("allowed_write_paths")
    if type(allowed_write_paths) is not list or not allowed_write_paths:
        return None
    allowed_read_paths = token.get("allowed_read_paths")
    if type(allowed_read_paths) is not list or not allowed_read_paths:
        return None
    if any(not isinstance(path, str) or not path.strip() for path in [*allowed_write_paths, *allowed_read_paths]):
        return None
    if len(set(allowed_write_paths)) != len(allowed_write_paths) or len(set(allowed_read_paths)) != len(
        allowed_read_paths
    ):
        return None
    if not set(allowed_write_paths).issubset(allowed_read_paths):
        return None

    envelopes: list[Mapping[str, Any]] = []
    for container in containers:
        for key in _EXECUTION_ENVELOPE_KEYS:
            envelope = _mapping(container.get(key))
            if envelope is not None:
                envelopes.append(envelope)

    embedded_hash = str(token.get("execution_envelope_hash") or "").strip()
    if embedded_hash and not _is_lower_sha256(embedded_hash):
        return None
    if not envelopes:
        return None
    for envelope in envelopes:
        envelope_hash = str(envelope.get("envelope_hash") or "").strip()
        authorization = _mapping(envelope.get("authorization"))
        if not _is_lower_sha256(envelope_hash) or (embedded_hash and envelope_hash != embedded_hash):
            return None
        if authorization is None:
            return None
        if str(authorization.get("capability_token_ref") or "").strip() != token_id:
            return None
        if str(authorization.get("capability_token_hash") or "").strip() != token_hash:
            return None
        if list(authorization.get("allowed_write_paths") or []) != allowed_write_paths:
            return None
    merged = dict(token)
    merged["execution_envelope_hash"] = str(envelopes[0].get("envelope_hash") or "").strip()
    return merged


def _capability_scope_from_context(context: Mapping[str, Any] | None) -> tuple[str, ...]:
    token = _capability_token_from_context(context)
    if token is None:
        return ()
    candidates = token.get("allowed_write_paths")
    if not isinstance(candidates, list):
        return ()
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
    capability_token = _capability_token_from_context(context)
    capability_scope = _capability_scope_from_context(context)
    if capability_token is None or not capability_scope:
        logger.info("Skipping deferred materialization commit: authoritative write capability missing")
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
            capability_scope=capability_scope,
            capability_token=capability_token,
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
