"""Director role-response normalization and verification-command helpers."""

from __future__ import annotations

import re
from typing import Any

from ._payload import (
    _copy_mapping_payload,
    _first_dict_list_payload,
    _first_mapping_payload,
)

_VERIFICATION_COMMAND_MARKERS = (
    "go test",
    "go run",
    "go vet",
    "go build",
    "npm test",
    "npm run",
    "cargo check",
    "cargo test",
    "python -m unittest",
    "pytest",
    "ruff check",
    "mypy",
)
_BACKTICK_VERIFICATION_COMMAND_RE = re.compile(
    r"`([^`\n]*(?:" + "|".join(re.escape(item) for item in _VERIFICATION_COMMAND_MARKERS) + r")[^`\n]*)`", re.IGNORECASE
)


def _flatten_verification_command_sources(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        token = value.strip()
        return [token] if token else []
    if isinstance(value, (list, tuple, set)):
        flattened: list[str] = []
        for item in value:
            flattened.extend(_flatten_verification_command_sources(item))
        return flattened
    if isinstance(value, dict):
        flattened = []
        for key in (
            "verification_commands",
            "verify_commands",
            "quality_commands",
            "workspace_quality_commands",
            "acceptance",
            "acceptance_criteria",
            "steps",
            "execution_checklist",
            "verify",
        ):
            if key in value:
                flattened.extend(_flatten_verification_command_sources(value.get(key)))
        return flattened
    return []


def _extract_director_verification_commands(*values: Any) -> list[str]:
    commands: list[str] = []
    seen: set[str] = set()
    for source in values:
        for text in _flatten_verification_command_sources(source):
            candidates = [match.group(1).strip() for match in _BACKTICK_VERIFICATION_COMMAND_RE.finditer(text)]
            stripped = text.strip().strip("`")
            if not candidates and any(stripped.lower().startswith(marker) for marker in _VERIFICATION_COMMAND_MARKERS):
                candidates.append(stripped)
            for candidate in candidates:
                normalized = " ".join(candidate.strip().strip("`").split())
                if not normalized:
                    continue
                lowered = normalized.lower()
                if lowered not in seen:
                    seen.add(lowered)
                    commands.append(normalized)
    return commands


def _normalize_director_role_response(role_response: Any) -> dict[str, Any]:
    """Normalize role-kernel output without hiding provider/runtime failures."""

    response_payload: dict[str, Any] = role_response if isinstance(role_response, dict) else {}
    content = (
        str(response_payload.get("response") or response_payload.get("reply") or response_payload.get("content") or "")
        if response_payload
        else str(role_response or "")
    )
    content = content.strip()
    explicit_error = str(response_payload.get("error") or "").strip() if response_payload else ""
    runtime_error = _extract_director_role_runtime_error(response_payload, content)
    error = explicit_error or runtime_error
    if not error and response_payload.get("success") is False:
        error = "role_response_unsuccessful"
    provider = str(response_payload.get("provider") or response_payload.get("provider_id") or "").strip()
    model = str(response_payload.get("model") or "").strip()
    metadata_raw = response_payload.get("metadata")
    metadata = _copy_mapping_payload(metadata_raw) or {}
    execution_stats_raw = response_payload.get("execution_stats")
    execution_stats = _copy_mapping_payload(execution_stats_raw) or {}
    raw_response = (
        response_payload.get("raw_response") if isinstance(response_payload.get("raw_response"), dict) else {}
    )
    raw_metadata = raw_response.get("metadata") if isinstance(raw_response, dict) else {}
    raw_usage = raw_response.get("usage") if isinstance(raw_response, dict) else {}
    batch_receipt = _first_mapping_payload(
        response_payload.get("batch_receipt"),
        metadata.get("batch_receipt"),
        execution_stats.get("batch_receipt"),
        raw_response.get("batch_receipt") if isinstance(raw_response, dict) else None,
        raw_metadata.get("batch_receipt") if isinstance(raw_metadata, dict) else None,
        raw_usage.get("batch_receipt") if isinstance(raw_usage, dict) else None,
    )
    tool_results = _first_dict_list_payload(
        response_payload.get("tool_results"),
        metadata.get("tool_results"),
        execution_stats.get("tool_results"),
        raw_response.get("tool_results") if isinstance(raw_response, dict) else None,
        raw_metadata.get("tool_results") if isinstance(raw_metadata, dict) else None,
        raw_usage.get("tool_results") if isinstance(raw_usage, dict) else None,
    )
    tool_calls_raw = response_payload.get("tool_calls")
    tool_calls = (
        [dict(item) for item in tool_calls_raw if isinstance(item, dict)] if isinstance(tool_calls_raw, list) else []
    )
    artifacts_raw = response_payload.get("artifacts")
    artifacts = [str(item) for item in artifacts_raw if str(item).strip()] if isinstance(artifacts_raw, list) else []
    if not provider:
        provider = str(metadata.get("provider_id") or metadata.get("provider") or "").strip()
    if not model:
        model = str(metadata.get("model") or execution_stats.get("model") or "").strip()
    return {
        "content": content,
        "success": not bool(error),
        "error": error,
        "raw_response": role_response,
        "provider": provider,
        "model": model,
        "metadata": dict(metadata),
        "execution_stats": dict(execution_stats),
        "batch_receipt": dict(batch_receipt) if batch_receipt else None,
        "tool_results": tool_results,
        "tool_calls": tool_calls,
        "artifacts": artifacts,
    }


def _extract_director_role_runtime_error(response_payload: dict[str, Any], content: str) -> str:
    """Return a non-empty error when role output is a runtime failure wrapper."""

    if content.startswith("[ROLE_EXECUTION_ERROR]"):
        return content
    if content.startswith("[Cognitive Blocked]"):
        return content
    metadata = response_payload.get("metadata") if isinstance(response_payload, dict) else {}
    if isinstance(metadata, dict):
        metadata_error = str(metadata.get("error") or metadata.get("error_message") or "").strip()
        if metadata_error:
            return metadata_error
    validation = response_payload.get("validation") if isinstance(response_payload, dict) else {}
    if isinstance(validation, dict) and validation.get("success") is False:
        errors = validation.get("errors")
        if isinstance(errors, list) and errors:
            return "; ".join(str(item) for item in errors[:3] if str(item).strip())
    return ""
