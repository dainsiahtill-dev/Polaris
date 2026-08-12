"""Timeout, budget, and role-dialogue context helpers for DirectorAdapter."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from polaris.kernelone.llm.budget_policy import (
    FORCED_WRITE_CONTEXT_KEYS,
    FORCED_WRITE_OUTPUT_TOKEN_FLOOR,
    FORCED_WRITE_STAGE_MARKERS,
    OUTPUT_BUDGET_CONTEXT_KEYS,
    TIMEOUT_CEILING_CONTEXT_KEYS,
    TIMEOUT_OVERRIDE_CONTEXT_KEYS,
    forced_write_output_token_ceiling,
    forced_write_retry_timeout_seconds,
)

from ..execution import DirectorPatchExecutor
from ..helpers import _DEFAULT_LLM_CALL_TIMEOUT_SECONDS
from ._payload import (
    _string_list_payload,
)

logger = logging.getLogger("polaris.cells.roles.adapters.internal.director.adapter")

# Budget/timeout constants, context-key lists and env parsing are
# single-sourced in polaris.kernelone.llm.budget_policy (blueprint Phase 1);
# the local names below are kept as compatibility aliases for this module.
_ROLE_CALL_TIMEOUT_CEILING_KEYS = TIMEOUT_CEILING_CONTEXT_KEYS
_ROLE_CALL_TIMEOUT_KEYS = TIMEOUT_OVERRIDE_CONTEXT_KEYS

# The runtime command owns the actual provider/tool execution timeout.  The
# adapter's outer watchdog must outlive that budget briefly so roles.runtime can
# project the already-finished tool batch, DEO receipt, and terminal session
# result.  Using the exact same timeout at both layers created a race: a write
# completed near the deadline, then the outer ``wait_for`` cancelled receipt
# projection and reported ``tool_results=[]``.  Factory subsequently treated a
# real mutation as a no-op and validated stale diagnostics.  This grace is
# settlement-only; it is not copied into the provider request and cannot buy a
# second LLM attempt.
_ROLE_DIALOGUE_SETTLEMENT_GRACE_SECONDS = 15.0
_FORCED_WRITE_STAGE_MARKERS = FORCED_WRITE_STAGE_MARKERS
_FORCED_WRITE_CONTEXT_KEYS = FORCED_WRITE_CONTEXT_KEYS
_OUTPUT_BUDGET_CONTEXT_KEYS = OUTPUT_BUDGET_CONTEXT_KEYS
_TRANSACTION_EXECUTION_SCOPE_KEYS = (
    "execution_attempt_id",
    "turn_request_id",
    "execution_id",
    "task_runtime_session_id",
)
_DIRECTOR_ROLE_SUBINVOCATION_SCHEMA = "director.role_subinvocation.v1"


def _coerce_positive_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _coerce_positive_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _role_call_timeout_from_context(context: dict[str, Any]) -> float | None:
    for key in _ROLE_CALL_TIMEOUT_KEYS:
        parsed = _coerce_positive_float(context.get(key))
        if parsed is not None:
            return parsed
    return None


def _role_call_timeout_ceiling_from_context(context: dict[str, Any]) -> float | None:
    for key in _ROLE_CALL_TIMEOUT_CEILING_KEYS:
        parsed = _coerce_positive_float(context.get(key))
        if parsed is not None:
            return parsed
    return None


def _resolve_role_call_timeout(
    *,
    context: dict[str, Any],
    stage_label: str,
    requested_timeout_seconds: float,
) -> float:
    timeout = max(0.1, float(requested_timeout_seconds or _DEFAULT_LLM_CALL_TIMEOUT_SECONDS))
    context_timeout = _role_call_timeout_from_context(context)
    if context_timeout is not None:
        timeout = min(timeout, context_timeout)
    context_timeout_ceiling = _role_call_timeout_ceiling_from_context(context)
    if context_timeout_ceiling is not None:
        timeout = min(timeout, context_timeout_ceiling)
    normalized_stage = str(stage_label or "").strip().lower()
    if any(marker in normalized_stage for marker in _FORCED_WRITE_STAGE_MARKERS):
        retry_timeout = forced_write_retry_timeout_seconds(upper=timeout)
        timeout = min(timeout, retry_timeout)
    return max(0.1, timeout)


def _context_has_forced_write_retry(context: dict[str, Any], *, stage_label: str) -> bool:
    normalized_stage = str(stage_label or "").strip().lower()
    if any(marker in normalized_stage for marker in _FORCED_WRITE_STAGE_MARKERS):
        return True
    if any(key in context for key in _FORCED_WRITE_CONTEXT_KEYS):
        return True
    if normalized_stage != "first_call" or not _string_list_payload(context.get("target_files"), limit=64):
        return False
    for key in (
        "task_execution_profile",
        "director_execution_profile",
        "task_execution_contract",
        "director_execution_contract",
    ):
        payload = context.get(key)
        if not isinstance(payload, dict):
            continue
        if str(payload.get("task_type") or "").strip().lower() == "write_code":
            return True
        if str(payload.get("output_contract_id") or "").strip().lower() == "director.patch_file.v1":
            return True
    return False


def _forced_write_effective_output_budget(context: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    ceiling = forced_write_output_token_ceiling()
    existing_values: dict[str, Any] = {key: context[key] for key in _OUTPUT_BUDGET_CONTEXT_KEYS if key in context}
    parsed_existing = [
        parsed for value in existing_values.values() if (parsed := _coerce_positive_int(value)) is not None
    ]
    if parsed_existing:
        return max(FORCED_WRITE_OUTPUT_TOKEN_FLOOR, min(ceiling, *parsed_existing)), existing_values
    return ceiling, existing_values


def _current_task_write_boundary_context(context: dict[str, Any]) -> dict[str, Any] | None:
    target_files = _string_list_payload(context.get("target_files"), limit=64)
    if not target_files:
        return None
    target_set = set(target_files)
    project_targets = _string_list_payload(context.get("project_declared_target_files"), limit=96)
    downstream_or_read_only = [path for path in project_targets if path not in target_set]
    non_test_targets = [path for path in target_files if not _path_looks_like_test_target(path)]
    test_targets = [path for path in target_files if _path_looks_like_test_target(path)]
    return {
        "schema_version": "director.current_task_write_boundary.v1",
        "source": "director_adapter_context_boundary",
        "current_target_files": target_files,
        "project_declared_target_files_are_inventory_only": True,
        "project_files_absent_from_current_target_are_downstream_or_read_only": downstream_or_read_only,
        "non_test_current_targets": non_test_targets,
        "test_current_targets": test_targets,
        "rules": [
            "Write only current_target_files for this Director task.",
            "Do not write project-declared files that are absent from current_target_files; they are downstream or read-only context.",
            "Do not embed tests/spec content into non-test source files to satisfy project-level test requirements.",
        ],
    }


def _bind_director_role_subinvocation(
    context: dict[str, Any],
    *,
    stage_label: str,
) -> None:
    """Bind one stable logical role call beneath a parent execution attempt.

    TaskRuntime owns the parent authorization. Director orchestration may issue
    several independent RoleRuntime calls inside that attempt, so each stage
    needs its own replay-stable ``turn_request_id``. The parent scope remains
    evidence and never becomes a second authorization source.
    """

    metadata_raw = context.get("metadata")
    metadata_source: dict[str, Any] = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
    # ``runtime_execution`` is a TaskRuntime-owned execution-state projection.
    # Director may inspect it to bind the parent identity, but a child role
    # request must not carry a rewritten or partial copy.  The typed
    # ``director_role_subinvocation`` evidence below is the sole parent/child
    # projection for the outbound role call.
    metadata: dict[str, Any] = {key: value for key, value in metadata_source.items() if key != "runtime_execution"}
    candidates: list[tuple[str, str]] = []

    metadata_evidence_raw = metadata.get("director_role_subinvocation")
    context_evidence_raw = context.get("director_role_subinvocation")
    if (
        metadata_evidence_raw is not None
        and context_evidence_raw is not None
        and metadata_evidence_raw != context_evidence_raw
    ):
        raise RuntimeError("director_role_invocation_prior_evidence_mismatch")
    prior_evidence_raw = metadata_evidence_raw
    if prior_evidence_raw is None:
        prior_evidence_raw = context_evidence_raw
    if prior_evidence_raw is not None:
        if not isinstance(prior_evidence_raw, dict):
            raise RuntimeError("director_role_invocation_prior_evidence_invalid")
        prior_schema = str(prior_evidence_raw.get("schema_version") or "").strip()
        parent_scope_kind = str(prior_evidence_raw.get("parent_execution_scope_kind") or "").strip()
        parent_scope_id = str(prior_evidence_raw.get("parent_execution_scope_id") or "").strip()
        prior_turn_request_id = str(prior_evidence_raw.get("turn_request_id") or "").strip()
        if (
            prior_schema != _DIRECTOR_ROLE_SUBINVOCATION_SCHEMA
            or parent_scope_kind not in _TRANSACTION_EXECUTION_SCOPE_KEYS
            or not parent_scope_id
            or not prior_turn_request_id
        ):
            raise RuntimeError("director_role_invocation_prior_evidence_invalid")
        for projected_turn_id in (
            str(metadata.get("turn_request_id") or "").strip(),
            str(context.get("turn_request_id") or "").strip(),
        ):
            if projected_turn_id and projected_turn_id != prior_turn_request_id:
                raise RuntimeError("director_role_invocation_prior_evidence_mismatch")
        for key in _TRANSACTION_EXECUTION_SCOPE_KEYS:
            token = str(metadata.get(key) or "").strip()
            expected = prior_turn_request_id if key == "turn_request_id" else parent_scope_id
            if token and token != expected:
                raise RuntimeError("director_role_invocation_prior_evidence_mismatch")
        candidates.append((parent_scope_kind, parent_scope_id))
    else:
        for key in _TRANSACTION_EXECUTION_SCOPE_KEYS:
            token = str(metadata.get(key) or "").strip()
            if token:
                candidates.append((key, token))

    runtime_execution_raw = metadata_source.get("runtime_execution")
    runtime_execution = dict(runtime_execution_raw) if isinstance(runtime_execution_raw, dict) else None
    if runtime_execution is not None and prior_evidence_raw is not None:
        for key in (*_TRANSACTION_EXECUTION_SCOPE_KEYS, "session_id"):
            token = str(runtime_execution.get(key) or "").strip()
            expected = prior_turn_request_id if key == "turn_request_id" else parent_scope_id
            if token and token != expected:
                raise RuntimeError("director_role_invocation_prior_evidence_mismatch")
    if runtime_execution is not None and prior_evidence_raw is None:
        for key in (*_TRANSACTION_EXECUTION_SCOPE_KEYS, "session_id"):
            token = str(runtime_execution.get(key) or "").strip()
            if not token:
                continue
            canonical_key = "task_runtime_session_id" if key == "session_id" else key
            candidates.append((canonical_key, token))

    if not candidates:
        return
    distinct_parent_ids = {scope_id for _, scope_id in candidates}
    if len(distinct_parent_ids) != 1:
        fields = ",".join(scope_kind for scope_kind, _ in candidates)
        raise RuntimeError(f"director_role_invocation_parent_identity_mismatch fields={fields}")

    normalized_stage = str(stage_label or "").strip().lower()
    if not normalized_stage:
        raise RuntimeError("director_role_invocation_stage_missing")
    parent_scope_kind, parent_scope_id = candidates[0]
    stage_token = re.sub(r"[^a-z0-9._-]+", "-", normalized_stage).strip("-._")[:32] or "stage"
    identity_material = "\n".join(
        (
            _DIRECTOR_ROLE_SUBINVOCATION_SCHEMA,
            parent_scope_kind,
            parent_scope_id,
            normalized_stage,
        )
    ).encode("utf-8")
    turn_request_id = f"director-{stage_token}-{hashlib.sha256(identity_material).hexdigest()[:24]}"

    for key in _TRANSACTION_EXECUTION_SCOPE_KEYS:
        metadata.pop(key, None)
    evidence = {
        "schema_version": _DIRECTOR_ROLE_SUBINVOCATION_SCHEMA,
        "parent_execution_scope_kind": parent_scope_kind,
        "parent_execution_scope_id": parent_scope_id,
        "stage_label": normalized_stage,
        "turn_request_id": turn_request_id,
    }
    metadata["turn_request_id"] = turn_request_id
    metadata["director_role_subinvocation"] = evidence
    context["turn_request_id"] = turn_request_id
    context["director_role_subinvocation"] = dict(evidence)
    context["metadata"] = metadata


def _prepare_role_dialogue_context(
    context: dict[str, Any] | None,
    *,
    timeout_seconds: float,
    stage_label: str,
) -> tuple[dict[str, Any], float]:
    context_payload = dict(context) if isinstance(context, dict) else {}
    _bind_director_role_subinvocation(context_payload, stage_label=stage_label)
    timeout = _resolve_role_call_timeout(
        context=context_payload,
        stage_label=stage_label,
        requested_timeout_seconds=timeout_seconds,
    )
    timeout = DirectorPatchExecutor.clamp_llm_call_timeout_to_factory_deadline(
        context_payload,
        timeout,
    )
    for key in _ROLE_CALL_TIMEOUT_CEILING_KEYS:
        context_payload[key] = timeout
    context_payload["director_role_call_timeout_budget"] = {
        "schema_version": "director.role_call_timeout_budget.v1",
        "stage_label": str(stage_label or ""),
        "timeout_seconds": timeout,
        "source": "director_adapter_role_dialogue_boundary",
    }
    write_boundary = _current_task_write_boundary_context(context_payload)
    if write_boundary:
        context_payload["current_task_write_boundary"] = write_boundary
    if _context_has_forced_write_retry(context_payload, stage_label=stage_label):
        output_tokens, previous_budget_values = _forced_write_effective_output_budget(context_payload)
        context_payload["llm_max_tokens"] = output_tokens
        context_payload["director_forced_write_output_budget"] = {
            "schema_version": "director.forced_write_output_budget.v1",
            "stage_label": str(stage_label or ""),
            "max_tokens": output_tokens,
            "ceiling_tokens": forced_write_output_token_ceiling(),
            "previous_budget_values": previous_budget_values,
            "source": "director_adapter_forced_write_retry",
        }
    return context_payload, timeout


def _context_timeout_seconds_for_runtime_command(context: dict[str, Any]) -> int | None:
    timeout = _role_call_timeout_from_context(context)
    if timeout is None:
        return None
    return max(1, int(timeout))


def _role_dialogue_watchdog_timeout_seconds(
    context: dict[str, Any] | None,
    *,
    provider_timeout_seconds: float,
) -> float:
    """Return the outer transaction watchdog without expanding provider time.

    ``request_timeout_seconds`` / ``timeout_seconds`` are the enclosing
    TaskRuntime execution budget.  The narrower provider budget is projected
    separately by ``_prepare_role_dialogue_context``.  Cancelling the whole
    RoleRuntime transaction at provider-timeout + a fixed grace can discard a
    DEO receipt that is already settling (L1-01 r42).  Preserve the enclosing
    execution budget for receipt/session projection, while the Factory
    execution deadline remains the absolute non-expanding ceiling.
    """

    provider_timeout = max(0.1, float(provider_timeout_seconds))
    watchdog_timeout = provider_timeout + _ROLE_DIALOGUE_SETTLEMENT_GRACE_SECONDS
    if isinstance(context, dict):
        execution_budget_candidates = (
            _coerce_positive_float(context.get("request_timeout_seconds")),
            _coerce_positive_float(context.get("timeout_seconds")),
        )
        execution_budgets = [value for value in execution_budget_candidates if value is not None]
        if execution_budgets:
            watchdog_timeout = max(watchdog_timeout, max(execution_budgets))
    return DirectorPatchExecutor.clamp_llm_call_timeout_to_factory_deadline(
        context,
        watchdog_timeout,
    )


def _join_limited_values(label: str, values: list[str]) -> str:
    return f"- {label}: {', '.join(values)}" if values else ""


def _path_looks_like_test_target(path: str) -> bool:
    normalized = str(path or "").replace("\\", "/").strip().lower()
    filename = normalized.rsplit("/", 1)[-1]
    return bool(
        normalized.startswith("tests/")
        or "/tests/" in normalized
        or ".test." in filename
        or ".spec." in filename
        or filename.startswith("test_")
        or "_test." in filename
        or filename.endswith("test.java")
    )
