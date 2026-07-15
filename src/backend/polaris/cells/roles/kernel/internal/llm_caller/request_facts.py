"""Canonical structured facts for final LLM request preparation.

This module owns the narrow projection from a role-turn request into the
call-scoped control-plane facts consumed by ``LLMRequestPreparer``. Prompt text
is deliberately excluded: final-request evidence must come from structured
``context`` or ``metadata`` fields, never from prose re-parsing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

REQUEST_FACT_PROVENANCE_KEY = "request_fact_provenance"

# Facts copied into ``AIRequest.context`` for final-request audit. Keep this
# list at the request-preparation boundary so upstream callers do not need
# Factory-, role-, or provider-specific projection branches.
FINAL_REQUEST_EVIDENCE_CONTEXT_KEYS: tuple[str, ...] = (
    "pm_contract",
    "pm_task_contract",
    "pm_task_contracts",
    "ce_blueprint",
    "chief_engineer_blueprint",
    "task_contract",
    "target_files",
    "scope_paths",
    "module_interface_contract",
    "actual_sibling_exports",
    "interface_discrepancy_context",
    "architecture_or_file_plan",
    "architecture_plan",
    "file_plan",
    "construction_plan",
    "delivery_plan_document",
    "delivery_depth_contract",
    "behavior_contract",
    "acceptance_contract",
    "manifest_entrypoint_contract",
    "execution_contract",
    "task_execution_contract",
    "director_execution_contract",
    "task_execution_profile",
    "director_execution_profile",
    "execution_profile",
    "task_execution_strategy",
    "director_execution_strategy",
    "execution_strategy",
    "task_execution_envelope",
    "director_execution_envelope",
    "execution_envelope",
    "execution_envelope_hash",
    "failed_gate_evidence",
    "workspace_quality_evidence",
    "context_evidence_slots",
    "run_ledger_projection",
    "task_metadata",
    "metadata",
    REQUEST_FACT_PROVENANCE_KEY,
)

# Only these command metadata fields may enter provider-request context. This
# prevents unrelated runtime metadata from becoming an implicit data plane.
ROLE_REQUEST_METADATA_FACT_KEYS: tuple[str, ...] = (
    *FINAL_REQUEST_EVIDENCE_CONTEXT_KEYS,
    "temperature",
    "request_sampling",
    "timeout_seconds",
    "request_timeout_seconds",
    "llm_call_timeout_seconds",
    "llm_max_tokens",
    "max_output_tokens",
)


@dataclass(frozen=True, slots=True)
class RoleRequestFactProjection:
    """Immutable projection result for one role-turn provider call.

    Attributes:
        context_override: Copied call-scoped context consumed by the request
            preparer.
        sources: Fact-level provenance. Explicit context wins over metadata.
        conflict_keys: Keys supplied by both sources with different values.
    """

    context_override: dict[str, Any]
    sources: dict[str, str]
    conflict_keys: tuple[str, ...]


def _is_present(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (Mapping, list, tuple, set, frozenset)):
        return bool(value)
    return True


def _copy_fact(value: Any) -> Any:
    """Copy JSON-like evidence without retaining caller-owned containers."""

    if isinstance(value, Mapping):
        return {str(key): _copy_fact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy_fact(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_copy_fact(item) for item in sorted(value, key=lambda item: str(item))]
    return value


def _fact_digest(value: Any) -> str:
    payload = json.dumps(
        _copy_fact(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=repr,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def project_role_request_facts(
    *,
    context_override: Mapping[str, Any] | None,
    metadata: Mapping[str, Any] | None,
) -> RoleRequestFactProjection:
    """Merge structured role-turn facts with deterministic precedence.

    Explicit request context is authoritative. Metadata only fills absent
    allowlisted facts. Divergent duplicates remain visible through provenance
    instead of silently changing the final provider request.

    Complexity:
        O(n + s) time and O(n) memory, where ``n`` is copied context size and
        ``s`` is the fixed allowlist size.
    """

    context_payload = dict(context_override) if isinstance(context_override, Mapping) else {}
    metadata_payload = dict(metadata) if isinstance(metadata, Mapping) else {}
    projected = {str(key): _copy_fact(value) for key, value in context_payload.items()}
    sources: dict[str, str] = {
        key: f"role_turn.context.{key}" for key in ROLE_REQUEST_METADATA_FACT_KEYS if _is_present(projected.get(key))
    }
    conflicts: list[str] = []

    for key in ROLE_REQUEST_METADATA_FACT_KEYS:
        metadata_value = metadata_payload.get(key)
        if not _is_present(metadata_value):
            continue
        context_value = projected.get(key)
        if not _is_present(context_value):
            projected[key] = _copy_fact(metadata_value)
            sources[key] = f"role_turn.metadata.{key}"
            continue
        if _fact_digest(context_value) != _fact_digest(metadata_value):
            conflicts.append(key)

    projected_sources = dict(sorted(sources.items()))
    projected_conflicts = tuple(sorted(set(conflicts)))
    provenance = {
        "schema_version": "roles.kernel.request_fact_provenance.v1",
        "precedence": "context_over_metadata",
        "sources": projected_sources,
        "conflict_keys": list(projected_conflicts),
    }
    projected[REQUEST_FACT_PROVENANCE_KEY] = provenance
    return RoleRequestFactProjection(
        context_override=projected,
        sources=projected_sources,
        conflict_keys=projected_conflicts,
    )


def copy_final_request_evidence_context_fields(context_override: Any) -> dict[str, Any]:
    """Copy structured evidence fields into the final ``AIRequest.context``."""

    if not isinstance(context_override, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key in FINAL_REQUEST_EVIDENCE_CONTEXT_KEYS:
        value = context_override.get(key)
        if _is_present(value):
            result[key] = _copy_fact(value)
    return result


def request_fact_source(context_override: Any, key: str, fallback: str) -> str:
    """Return stable provenance for one projected request fact."""

    if not isinstance(context_override, Mapping):
        return fallback
    provenance = context_override.get(REQUEST_FACT_PROVENANCE_KEY)
    if not isinstance(provenance, Mapping):
        return fallback
    sources = provenance.get("sources")
    if not isinstance(sources, Mapping):
        return fallback
    source = str(sources.get(key) or "").strip()
    return source or fallback
