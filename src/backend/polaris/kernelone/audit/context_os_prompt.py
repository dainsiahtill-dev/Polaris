"""Prompt-level audit helpers for ContextOS projections.

These helpers are intentionally read-only: they inspect the messages that are
about to be sent to an LLM and produce compact evidence that the prompt was
derived from ContextOS projection, not from control-plane runtime state.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

CONTROL_PLANE_PROMPT_KEYS = frozenset(
    {
        "_transaction_kernel_forced_tool_choice",
        "_transaction_kernel_forced_tool_definitions",
        "_transaction_kernel_prebuilt_messages",
        "allowed_provider_ids",
        "allowed_provider_types",
        "allowed_scope",
        "backlog_ref",
        "blocked_provider_ids",
        "blocked_provider_types",
        "blueprint_hash",
        "blueprint_id",
        "blueprint_path",
        "budget_status",
        "capability_token",
        "capability_token_id",
        "chief_engineer_deadline_decision",
        "chief_engineer_blueprint_id",
        "chief_engineer_handoff_id",
        "chief_engineer_llm_timeout_seconds",
        "cognitive_guidance",
        "cognitive_runtime_enabled",
        "cognitive_runtime_mode",
        "cognitive_runtime_required",
        "cognitive_strategy_override",
        "construction_step",
        "consumed_interfaces",
        "contract_hash",
        "control_plane_job_token",
        "context_os_expected",
        "context_os_snapshot",
        "current_task_write_boundary",
        "delivery_mode",
        "director_language_identity",
        "director_quality_repair",
        "director_role_call_timeout_budget",
        "director_role_subinvocation",
        "disable_internal_tool_rounds",
        "domain",
        "execution_envelope_hash",
        "external_task_id",
        "factory_bench_level",
        "factory_bench_project_id",
        "factory_bench_project_workspace",
        "factory_bench_title",
        "factory_run_id",
        "handoff_source",
        "host_kind",
        "job_token",
        "job_token_id",
        "last_failure",
        "llm_call_timeout_ceiling_seconds",
        "llm_call_timeout_seconds",
        "llm_max_tokens",
        "llm_provider_policy",
        "max_output_tokens",
        "metadata",
        "metrics",
        "model_allowlist",
        "model_blocklist",
        "pm_contract_hash",
        "pm_task_id",
        "parent_token_id",
        "policy_verdict",
        "pre_state_verify",
        "prompt_profile",
        "prompt_profile_appendix",
        "prompt_profile_audit",
        "prompt_profile_id",
        "prompt_profile_ids",
        "prompt_profiles",
        "provider_allowlist",
        "provider_blocklist",
        "provider_policy",
        "raw_output",
        "request_timeout_ceiling_seconds",
        "request_timeout_seconds",
        "role_runtime_required",
        "run_card",
        "run_id",
        "runtime_session_id",
        "runtime_blueprint_path",
        "selected_prompt_profile_ids",
        "session_context_config",
        "session_id",
        "session_turn_events",
        "source_task_id",
        "state_first_context_os",
        "strategy_override",
        "stream_options",
        "system_warnings",
        "task_id",
        "task_execution_context_budget_policy",
        "task_execution_min_context_utilization",
        "task_execution_prompt_max_chars",
        "task_runtime_execution_attempt",
        "task_runtime_execution_attempt_authority",
        "task_runtime_guard",
        "task_runtime_internal_task_id",
        "task_runtime_session_id",
        "target_task_id",
        "telemetry",
        "telemetry_events",
        "thinking",
        "thinking_content",
        "timeout_ceiling_seconds",
        "timeout_seconds",
        "token_id",
        "turn_request_id",
        "workspace",
        "workspace_root",
    }
)
# Structural projection and natural-language scanning have different evidence
# strength. ``task_id`` is authoritative domain evidence inside PM contracts
# and CE portfolio plans, so prompt-safe structured projections may retain it.
# It remains forbidden in message metadata, where it denotes control-plane
# leakage.
CONTROL_PLANE_PROMPT_PROJECTION_KEYS = CONTROL_PLANE_PROMPT_KEYS - {"task_id"}

# Director operational protocol that must appear as intentional prompt text
# (SESSION_PATCH delivery_mode, [director_quality_repair:…], CE blueprint labels).
# These remain forbidden as message *metadata* keys via CONTROL_PLANE_PROMPT_KEYS.
# Scanning them as content leaks blocked L1-01 quality-repair qualification with
# final_request_context_quality_failed (context_os_prompt_audit_failed) even when
# no real control-plane authority was serialized into the prompt.
_PROMPT_SAFE_OPERATIONAL_CONTENT_KEYS = frozenset(
    {
        "blueprint_hash",
        "blueprint_id",
        "blueprint_path",
        "chief_engineer_blueprint_id",
        "delivery_mode",
        "director_quality_repair",
        # Generic QA data-plane nouns. Workspace validation receipts commonly
        # serialize a project path under ``workspace`` and verifier output may
        # contain headings such as ``implementation depth metrics``. Neither is
        # proof that runtime authority leaked into the prompt. They remain
        # forbidden as message metadata / structured projection keys; only this
        # weaker natural-language content scan excludes them. Strong authority
        # keys such as workspace_root, factory_run_id, and job_token still fail.
        "metrics",
        "workspace",
    }
)

# A generic word such as ``metadata`` is not proof that runtime authority was
# serialized into prompt content. It remains forbidden as an actual message
# metadata key and as a nested structured projection key. Only the weaker
# natural-language signature scan excludes it.
CONTROL_PLANE_PROMPT_CONTENT_KEYS = (
    CONTROL_PLANE_PROMPT_PROJECTION_KEYS - {"metadata"} - _PROMPT_SAFE_OPERATIONAL_CONTENT_KEYS
)
_CONTROL_CONTENT_TOKENS = tuple(
    sorted(
        {
            pattern
            for key in CONTROL_PLANE_PROMPT_CONTENT_KEYS
            for pattern in (
                f'"{key}"',
                f"'{key}'",
                f"{key}:",
                f"{key}=",
                f"<{key}>",
            )
        }
    )
)
CONTROL_PLANE_PROMPT_VALUE_TOKENS = (
    "capabilitytoken(",
    "capability_token(",
    "jobtoken(",
    "job_token(",
    "taskruntimeexecutionattempt(",
    "task_runtime_execution_attempt(",
    "taskruntimeexecutionattemptauthority(",
    "task_runtime_execution_attempt_authority(",
)
_CONTROL_PLANE_QUOTED_KEY_CANDIDATE = re.compile(r"""(?P<quote>['"])(?P<key>[^'"\r\n]{1,128})(?P=quote)\s*[:=]""")
_CONTROL_PLANE_BARE_KEY_CANDIDATE = re.compile(
    r"""(?<![A-Za-z0-9_])(?P<key>_?[A-Za-z][A-Za-z0-9_./\\-]{1,127})\s*[:=]"""
)
_CONTROL_PLANE_SPACED_KEY_CANDIDATE = re.compile(
    r"""(?<![A-Za-z0-9_])(?P<key>_?[A-Za-z][A-Za-z0-9]*(?:[ \t]+[A-Za-z0-9]+){1,7})\s*[:=]"""
)
_CAMEL_ACRONYM_BOUNDARY = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_WORD_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")

_RAW_TOOL_FAILURE_RECEIPT_TOKENS = (
    "director_policy",
    "package_diff",
    "handler_error_type",
    "director_write_policy_denied'}",
    '"director_write_policy_denied"}',
    '"ok": false',
    "'ok': false",
)


def _message_role(message: Mapping[str, Any]) -> str:
    return str(message.get("role") or "").strip().lower()


def _message_content(message: Mapping[str, Any]) -> str:
    return str(message.get("content") or "")


def _message_metadata(message: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = message.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        token = str(value or "").strip()
        if token and token not in result:
            result.append(token)
    return result


def normalize_control_plane_prompt_key(key: Any) -> str:
    """Normalize snake/camel/Pascal/kebab key spellings to one taxonomy."""
    raw = str(key or "").strip()
    if not raw:
        return ""
    private_prefix = raw.startswith("_")
    separated = _CAMEL_ACRONYM_BOUNDARY.sub(r"\1_\2", raw.lstrip("_"))
    separated = _CAMEL_WORD_BOUNDARY.sub(r"\1_\2", separated)
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", separated).strip("_").lower()
    collapsed = re.sub(r"_+", "_", normalized)
    return f"_{collapsed}" if private_prefix and collapsed else collapsed


def find_control_plane_prompt_content_hits(content: Any) -> list[str]:
    """Return canonical control-plane signatures found in prompt content."""
    normalized = str(content or "").lower()
    if not normalized:
        return []
    hits = [token for token in (*_CONTROL_CONTENT_TOKENS, *CONTROL_PLANE_PROMPT_VALUE_TOKENS) if token in normalized]
    raw_content = str(content or "")
    for pattern in (
        _CONTROL_PLANE_QUOTED_KEY_CANDIDATE,
        _CONTROL_PLANE_BARE_KEY_CANDIDATE,
        _CONTROL_PLANE_SPACED_KEY_CANDIDATE,
    ):
        for match in pattern.finditer(raw_content):
            normalized_key = normalize_control_plane_prompt_key(match.group("key"))
            if normalized_key in CONTROL_PLANE_PROMPT_CONTENT_KEYS:
                hits.append(normalized_key)
    return _dedupe(hits)


def _find_metadata_key_hits(messages: Sequence[Mapping[str, Any]]) -> list[str]:
    hits: list[str] = []
    for message in messages:
        metadata = _message_metadata(message)
        for key in metadata:
            key_text = normalize_control_plane_prompt_key(key)
            if key_text in CONTROL_PLANE_PROMPT_KEYS:
                hits.append(key_text)
    return _dedupe(hits)


def _looks_like_current_user_instruction(content: str, current_user_instruction: str) -> bool:
    normalized_content = " ".join(str(content or "").split())
    normalized_instruction = " ".join(str(current_user_instruction or "").split())
    return bool(normalized_instruction and normalized_instruction in normalized_content)


def _find_content_hits(
    messages: Sequence[Mapping[str, Any]],
    *,
    current_user_instruction: str = "",
) -> list[str]:
    hits: list[str] = []
    for message in messages:
        content = _message_content(message)
        if not content:
            continue
        if _message_role(message) == "user" and _looks_like_current_user_instruction(
            content,
            current_user_instruction,
        ):
            continue
        hits.extend(find_control_plane_prompt_content_hits(content))
    return _dedupe(hits)


def _find_raw_tool_failure_receipt_hits(messages: Sequence[Mapping[str, Any]]) -> list[str]:
    hits: list[str] = []
    for message in messages:
        role = _message_role(message)
        if role not in {"assistant", "tool", "tool_result"}:
            continue
        content = _message_content(message).lower()
        if not content or "[tool_failure_summary]" in content:
            continue
        for token in _RAW_TOOL_FAILURE_RECEIPT_TOKENS:
            if str(token).lower() in content:
                hits.append(str(token))
    return _dedupe(hits)


def _count_roles(messages: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for message in messages:
        role = _message_role(message) or "unknown"
        counts[role] = counts.get(role, 0) + 1
    return counts


def _prompt_digest(messages: Sequence[Mapping[str, Any]]) -> str:
    hasher = hashlib.sha256()
    for message in messages:
        hasher.update(_message_role(message).encode("utf-8", errors="replace"))
        hasher.update(b"\0")
        hasher.update(_message_content(message).encode("utf-8", errors="replace"))
        hasher.update(b"\0")
    return hasher.hexdigest()[:16]


def _current_user_instruction_present(
    messages: Sequence[Mapping[str, Any]],
    current_user_instruction: str,
) -> bool:
    instruction = str(current_user_instruction or "").strip()
    if not instruction:
        return True
    return any(
        _message_role(message) == "user"
        and _looks_like_current_user_instruction(_message_content(message), instruction)
        for message in messages
    )


def _mapping_payload(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _int_payload(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def compact_context_os_audit(audit: Mapping[str, Any]) -> dict[str, Any]:
    """Return a metadata-safe audit summary without prompt text."""
    if not isinstance(audit, Mapping):
        return {}
    return {
        "ok": bool(audit.get("ok")),
        "expected": bool(audit.get("expected")),
        "source": str(audit.get("source") or ""),
        "prompt_digest": str(audit.get("prompt_digest") or ""),
        "message_count": _int_payload(audit.get("message_count")),
        "role_counts": _mapping_payload(audit.get("role_counts")),
        "final_role": str(audit.get("final_role") or ""),
        "state_first_context_os": _mapping_payload(audit.get("state_first_context_os")),
        "control_plane": _mapping_payload(audit.get("control_plane")),
        "data_plane": _mapping_payload(audit.get("data_plane")),
        "current_user_instruction": _mapping_payload(audit.get("current_user_instruction")),
        "requirements": _mapping_payload(audit.get("requirements")),
    }


def summarize_context_os_audits(
    audits: Iterable[Mapping[str, Any]],
    *,
    source: str = "kernelone.audit.context_os_prompt",
) -> dict[str, Any]:
    """Aggregate per-request ContextOS prompt audits for return metadata."""
    compacted = [item for audit in audits if (item := compact_context_os_audit(audit))]
    if not compacted:
        return {}
    all_ok = all(bool(item.get("ok")) for item in compacted)
    return {
        "source": source,
        "ok": bool(all_ok),
        "all_ok": bool(all_ok),
        "llm_call_count": len(compacted),
        "latest": compacted[-1],
        "calls": compacted,
    }


def summarize_context_os_audit_from_ledger(ledger: Any) -> dict[str, Any]:
    """Extract compact ContextOS prompt audit summaries from a TurnLedger."""
    llm_calls = getattr(ledger, "llm_calls", ())
    audits: list[Mapping[str, Any]] = []
    for call in llm_calls or ():
        if not isinstance(call, Mapping):
            continue
        metadata = call.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        audit = metadata.get("context_os_audit")
        if isinstance(audit, Mapping):
            audits.append(audit)
    return summarize_context_os_audits(
        audits,
        source="transaction_ledger.llm_calls",
    )


def audit_context_os_prompt_messages(
    *,
    messages: Sequence[Mapping[str, Any]],
    context_sources: Iterable[str] = (),
    metadata: Mapping[str, Any] | None = None,
    current_user_instruction: str = "",
    expected: bool = False,
) -> dict[str, Any]:
    """Audit LLM-ready messages generated from ContextOS.

    Args:
        messages: The exact message list used to build the provider request.
        context_sources: Context assembly source tags.
        metadata: Optional context result metadata.
        expected: Whether this request is expected to be backed by ContextOS.

    Returns:
        A JSON-serializable audit payload.
    """
    normalized_messages = [dict(item) for item in messages if isinstance(item, Mapping)]
    source_tags = _dedupe(str(item) for item in context_sources)
    metadata_payload = dict(metadata or {})
    state_first_from_sources = any("state_first_context_os" in item for item in source_tags)
    state_first_from_metadata = bool(metadata_payload.get("state_first_mode_active"))
    prebuilt_from_transaction_kernel = bool(metadata_payload.get("prebuilt_projection_messages"))
    state_first_projected = state_first_from_sources or state_first_from_metadata or prebuilt_from_transaction_kernel

    metadata_key_hits = _find_metadata_key_hits(normalized_messages)
    content_hits = _find_content_hits(
        normalized_messages,
        current_user_instruction=current_user_instruction,
    )
    raw_tool_failure_receipt_hits = _find_raw_tool_failure_receipt_hits(normalized_messages)
    empty_content_count = sum(1 for item in normalized_messages if not _message_content(item).strip())
    receipt_ref_count = sum(1 for item in normalized_messages if "receipt_refs" in item)
    role_counts = _count_roles(normalized_messages)
    final_role = _message_role(normalized_messages[-1]) if normalized_messages else ""
    current_user_final = final_role == "user"
    current_user_preserved = _current_user_instruction_present(normalized_messages, current_user_instruction)
    control_plane_isolated = not metadata_key_hits and not content_hits
    raw_tool_failure_receipt_absent = not raw_tool_failure_receipt_hits
    data_plane_non_empty = bool(normalized_messages) and empty_content_count < len(normalized_messages)
    truth_source_ok = state_first_projected if expected else True
    ok = bool(
        truth_source_ok
        and control_plane_isolated
        and raw_tool_failure_receipt_absent
        and data_plane_non_empty
        and current_user_final
        and current_user_preserved
    )

    return {
        "ok": ok,
        "expected": bool(expected),
        "source": "kernelone.audit.context_os_prompt",
        "prompt_digest": _prompt_digest(normalized_messages),
        "message_count": len(normalized_messages),
        "role_counts": role_counts,
        "final_role": final_role,
        "state_first_context_os": {
            "projected": bool(state_first_projected),
            "sources": source_tags,
            "from_metadata": bool(state_first_from_metadata),
            "from_transaction_kernel_prebuilt": bool(prebuilt_from_transaction_kernel),
        },
        "control_plane": {
            "isolated": bool(control_plane_isolated),
            "metadata_key_hits": metadata_key_hits,
            "content_hits": content_hits,
        },
        "data_plane": {
            "non_empty": bool(data_plane_non_empty),
            "empty_content_count": int(empty_content_count),
            "receipt_ref_count": int(receipt_ref_count),
            "raw_tool_failure_receipt_absent": bool(raw_tool_failure_receipt_absent),
            "raw_tool_failure_receipt_hits": raw_tool_failure_receipt_hits,
        },
        "current_user_instruction": {
            "provided": bool(str(current_user_instruction or "").strip()),
            "preserved": bool(current_user_preserved),
        },
        "requirements": {
            "truth_source_context_os": bool(truth_source_ok),
            "prompt_projection_read_only": True,
            "control_plane_isolated": bool(control_plane_isolated),
            "raw_tool_failure_receipt_absent": bool(raw_tool_failure_receipt_absent),
            "current_user_final": bool(current_user_final),
            "current_user_instruction_preserved": bool(current_user_preserved),
        },
    }


__all__ = [
    "CONTROL_PLANE_PROMPT_CONTENT_KEYS",
    "CONTROL_PLANE_PROMPT_KEYS",
    "CONTROL_PLANE_PROMPT_PROJECTION_KEYS",
    "CONTROL_PLANE_PROMPT_VALUE_TOKENS",
    "audit_context_os_prompt_messages",
    "compact_context_os_audit",
    "find_control_plane_prompt_content_hits",
    "normalize_control_plane_prompt_key",
    "summarize_context_os_audit_from_ledger",
    "summarize_context_os_audits",
]
