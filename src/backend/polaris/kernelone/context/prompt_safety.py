"""Prompt-facing sanitizers for ContextOS projection messages."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import weakref
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from typing import Any

from polaris.kernelone.context.contracts import ContextSource

TOOL_OBSERVATION_SOURCES_KEY = "_transaction_kernel_tool_observation_sources"
_TOOL_OBSERVATION_ISSUER = object()

# These are authority-bearing result slots already excluded by native history.
# Literal source/diagnostic strings are data, including strings naming these slots.
_TOOL_RESULT_AUTHORITY_KEYS = frozenset(
    {
        "effect_receipt",
        "effect_receipt_commit",
        "director_policy",
        "authorization",
        "execution_envelope",
        "execution_attempt_authority",
        "job_token",
        "job_token_id",
        "job_token_set",
        "job_token_set_hash",
        "claim_grant",
        "claim_grant_hash",
        "capability_token",
        "control_plane_job_token",
    }
)


def _observation_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def project_tool_result_data(value: Any) -> Any:
    """Remove structured authority recursively without rewriting literal tool data."""
    from polaris.kernelone.audit.context_os_prompt import normalize_control_plane_prompt_key

    if isinstance(value, Mapping):
        return {
            str(key): project_tool_result_data(item)
            for key, item in value.items()
            if normalize_control_plane_prompt_key(key) not in _TOOL_RESULT_AUTHORITY_KEYS
        }
    if isinstance(value, list | tuple):
        return [project_tool_result_data(item) for item in value]
    return value


def _contains_tool_authority(value: Any) -> bool:
    from polaris.kernelone.audit.context_os_prompt import normalize_control_plane_prompt_key

    if isinstance(value, Mapping):
        return any(
            normalize_control_plane_prompt_key(key) in _TOOL_RESULT_AUTHORITY_KEYS or _contains_tool_authority(item)
            for key, item in value.items()
        )
    return isinstance(value, list | tuple) and any(_contains_tool_authority(item) for item in value)


def bind_tool_observation_source(
    *,
    source_id: str,
    role: str,
    tool_name: str,
    call_id: str,
    arguments: Mapping[str, Any],
    native_arguments: Mapping[str, Any],
    result: Any,
    content: str,
    snapshot_hash: str,
    raw_call_hash: str | None = None,
) -> ContextSource:
    """Seal one owner-joined native observation; this grants no execution authority.

    The private seal binds the exact ContextSource object and detached values.
    A caller-created data-plane flag, copied metadata, or serialized source is not
    an attestation. Cold recovery regenerates sources from committed invocations.
    """
    observation = json.loads(content)
    if observation != {"status": "success", "result": project_tool_result_data(result)}:
        raise ValueError("tool_observation_result_mismatch")
    if _contains_tool_authority(observation):
        raise ValueError("tool_observation_authority_leak")
    if _payload_indicates_failure(observation):
        raise ValueError("tool_observation_failed_result")
    binding = {
        "source_id": source_id,
        "role": role,
        "tool_name": tool_name,
        "call_id": call_id,
        "arguments_hash": _observation_digest(dict(arguments)),
        "arguments_json": json.dumps(dict(arguments), ensure_ascii=False, sort_keys=True, allow_nan=False),
        "native_arguments_hash": _observation_digest(dict(native_arguments)),
        "native_arguments_json": json.dumps(
            dict(native_arguments), ensure_ascii=False, sort_keys=True, allow_nan=False
        ),
        "result_hash": _observation_digest(result),
        "content_hash": _observation_digest(content),
        "result_json": json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False),
        "snapshot_hash": snapshot_hash,
        "raw_call_hash": raw_call_hash,
    }
    source = ContextSource(
        source_type="committed_tool_observation",
        source_id="tool-observation:" + _observation_digest(binding),
        role=role,
        text=content,
        tokens=max(1, len(content) // 4),
        importance=1.0,
        metadata={**binding, "_issuer": _TOOL_OBSERVATION_ISSUER},
    )
    source.metadata["_owner_ref"] = weakref.ref(source)
    return source


def validate_tool_observation_sources(
    messages: Sequence[Mapping[str, Any]],
    sources: Sequence[ContextSource],
    *,
    role: str,
) -> tuple[set[int], list[str]]:
    """Match each private source to one actual native call/result occurrence."""
    if not sources:
        return set(), []
    available: dict[tuple[str, str, str, str], deque[ContextSource]] = defaultdict(deque)
    errors: list[str] = []
    for source in sources:
        if (
            type(source) is not ContextSource
            or type(source.metadata) is not dict
            or source.metadata.get("_issuer") is not _TOOL_OBSERVATION_ISSUER
        ):
            errors.append("tool_observation_source_unowned")
            continue
        owner = source.metadata.get("_owner_ref")
        if type(owner) is not weakref.ReferenceType or owner() is not source or source.role != role:
            errors.append("tool_observation_source_drift")
            continue
        binding = source.metadata
        sealed = {key: value for key, value in binding.items() if key not in {"_issuer", "_owner_ref"}}
        try:
            valid = (
                source.source_type == "committed_tool_observation"
                and source.source_id == "tool-observation:" + _observation_digest(sealed)
                and source.role == binding.get("role")
                and _observation_digest(source.text) == binding.get("content_hash")
                and all(
                    _observation_digest(json.loads(binding[f"{kind}_json"])) == binding[f"{kind}_hash"]
                    for kind in ("arguments", "native_arguments", "result")
                )
            )
        except (TypeError, ValueError, KeyError):
            valid = False
        if not valid:
            errors.append("tool_observation_source_drift")
            continue
        key = (binding["call_id"], binding["tool_name"], binding["arguments_hash"], binding["content_hash"])
        available[key].append(source)
    pending: dict[str, deque[tuple[str, str]]] = defaultdict(deque)
    source_keys = set(available)
    matched: set[int] = set()
    for index, message in enumerate(messages):
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or ():
                if not isinstance(call, Mapping) or not isinstance(call.get("function"), Mapping):
                    errors.append("tool_observation_native_call_malformed")
                    continue
                function = call.get("function") or {}
                try:
                    arguments = json.loads(function.get("arguments", "{}"))
                    digest = _observation_digest(arguments)
                except (TypeError, ValueError):
                    continue
                pending[str(call.get("id") or "")].append((str(function.get("name") or ""), digest))
        elif message.get("role") == "tool":
            call_id = str(message.get("tool_call_id") or "")
            if not pending[call_id]:
                continue
            name, digest = pending[call_id].popleft()
            if name != message.get("name"):
                continue
            content = str(message.get("content") or "")
            key = (call_id, name, digest, _observation_digest(content))
            if available.get(key):
                source = available[key].popleft()
                if _contains_tool_authority(json.loads(source.text)):
                    errors.append("tool_observation_authority_leak")
                else:
                    matched.add(index)
            elif key in source_keys:
                errors.append("tool_observation_occurrence_mismatch")
    if any(available.values()):
        errors.append("tool_observation_final_slot_mismatch")
    return matched, errors


_TOOL_FAILURE_PROMPT_TOKENS = (
    "director_write_policy_denied",
    "handler_error_type",
    "**write_file**: error",
    "**edit_file**: error",
    '"status": "error"',
    "'status': 'error'",
    '"status": "failed"',
    "'status': 'failed'",
    '"success": false',
    "'success': false",
    "'success': False",
    '"ok": false',
    "'ok': false",
    "'ok': False",
)
_TOOL_FAILURE_SUMMARY_PREFIX = "[tool_failure_summary]\n"
_TOOL_OUTPUT_SUMMARY_PREFIX = "[tool_output_summary]\n"


def _trim_prompt_safe_text(text: str, *, max_chars: int = 220) -> str:
    value = " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split())
    if len(value) <= max_chars:
        return value
    return f"{value[: max_chars - 3].rstrip()}..."


def _extract_tool_failure_field(text: str, field: str) -> str:
    patterns = (
        rf'"{re.escape(field)}"\s*:\s*"(?P<value>[^"]+)"',
        rf"'{re.escape(field)}'\s*:\s*'(?P<value>[^']+)'",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return str(match.group("value") or "").strip()
    return ""


def _extract_braced_payload(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    candidate = text[start : end + 1]
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(candidate)
        except (SyntaxError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return dict(parsed)
    return {}


def _deep_get_text(value: Any, keys: tuple[str, ...], *, depth: int = 0) -> str:
    if depth > 5:
        return ""
    if isinstance(value, dict):
        for key in keys:
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
            if isinstance(raw, (int, float, bool)):
                return str(raw)
            if isinstance(raw, list):
                joined = ", ".join(str(item).strip() for item in raw if str(item).strip())
                if joined:
                    return joined
        for nested in value.values():
            result = _deep_get_text(nested, keys, depth=depth + 1)
            if result:
                return result
    if isinstance(value, list):
        for item in value:
            result = _deep_get_text(item, keys, depth=depth + 1)
            if result:
                return result
    return ""


def _deep_get_value(value: Any, keys: tuple[str, ...], *, depth: int = 0) -> Any:
    if depth > 5:
        return None
    if isinstance(value, dict):
        for key in keys:
            if key in value:
                return value.get(key)
        for nested in value.values():
            result = _deep_get_value(nested, keys, depth=depth + 1)
            if result is not None:
                return result
    if isinstance(value, list):
        for item in value:
            result = _deep_get_value(item, keys, depth=depth + 1)
            if result is not None:
                return result
    return None


def _is_explicit_false(value: Any) -> bool:
    if value is False:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"false", "0", "no", "failed", "error"}
    return False


def _is_explicit_true(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "success", "succeeded", "ok"}
    return False


def _status_indicates_failure(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return value.strip().lower() in {"error", "failed", "failure", "denied", "blocked"}


def _status_indicates_success(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return value.strip().lower() in {"ok", "success", "succeeded", "completed"}


def _payload_indicates_failure(payload: dict[str, Any]) -> bool:
    if not payload:
        return False
    if _is_explicit_false(_deep_get_value(payload, ("ok", "success"))):
        return True
    if _status_indicates_failure(_deep_get_value(payload, ("status",))):
        return True
    if _is_explicit_false(_deep_get_value(payload, ("allowed",))):
        return True
    if _deep_get_text(payload, ("error_type", "handler_error_type")):
        return True
    return bool(_deep_get_text(payload, ("error", "blocked_reason")))


def _payload_indicates_success(payload: dict[str, Any]) -> bool:
    if not payload or _payload_indicates_failure(payload):
        return False
    if _is_explicit_true(_deep_get_value(payload, ("ok", "success"))):
        return True
    if _status_indicates_success(_deep_get_value(payload, ("status",))):
        return True
    return _is_explicit_true(_deep_get_value(payload, ("allowed",)))


def _infer_tool_failure_tool(text: str) -> str:
    explicit = _extract_tool_failure_field(text, "tool") or _extract_tool_failure_field(text, "tool_name")
    if explicit:
        return explicit
    markdown_tool = re.search(r"\*\*(?P<tool>[A-Za-z_][A-Za-z0-9_.-]*)\*\*\s*:\s*error", text, re.IGNORECASE)
    if markdown_tool:
        return str(markdown_tool.group("tool") or "").strip()
    lowered = text.lower()
    for candidate in ("write_file", "edit_file", "read_file", "execute_command", "delete_file"):
        if candidate in lowered:
            return candidate
    return "unknown"


def tool_failure_summary_payload(role: str, content: str) -> dict[str, Any] | None:
    """Return a prompt-safe failure payload when raw tool receipts would leak."""

    text = str(content or "")
    lowered = text.lower()
    if str(role or "").strip().lower() not in {"assistant", "tool", "tool_result"}:
        return None

    tool = _infer_tool_failure_tool(text)
    payload = _extract_braced_payload(text)
    if _payload_indicates_success(payload):
        return None
    has_failure_token = any(token.lower() in lowered for token in _TOOL_FAILURE_PROMPT_TOKENS)
    if not has_failure_token and not _payload_indicates_failure(payload):
        return None

    error_type = (
        _extract_tool_failure_field(text, "error_type")
        or _extract_tool_failure_field(text, "handler_error_type")
        or _deep_get_text(payload, ("error_type", "handler_error_type", "code"))
    )
    if not error_type and "director_write_policy_denied" in lowered:
        error_type = "director_write_policy_denied"
    error = _extract_tool_failure_field(text, "error") or _deep_get_text(
        payload,
        (
            "error",
            "message",
            "reasons",
            "blocked_reason",
            "detail",
            "suggestion",
        ),
    )
    if not error:
        reason = _extract_tool_failure_field(text, "reason") or _deep_get_text(payload, ("reason",))
        if reason.strip().lower() not in {"write scope validated", "scope validated"}:
            error = reason
    if not error:
        gate_reason = _deep_get_text(payload, ("write_gate_reason",))
        if gate_reason.strip().lower() not in {"write scope validated", "scope validated"}:
            error = gate_reason
    if not error and "write_file" in lowered and "error" in lowered:
        error = "write_file failed"
    scope = _deep_get_text(payload, ("allowed_scope", "allowed_write_paths", "changed_files", "file", "path"))
    return {
        "tool": tool,
        "error_type": error_type or "tool_failure",
        "reason": _trim_prompt_safe_text(error or "tool execution failed"),
        **({"scope": _trim_prompt_safe_text(scope)} if scope else {}),
        "prompt_safe": True,
        "observation_only": True,
        "non_deliverable": True,
        "receipt_detail": "omitted; see runtime tool_result event for audit evidence",
    }


def parse_tool_failure_summary(content: Any) -> dict[str, Any] | None:
    """Parse an already-sanitized tool failure summary."""

    text = str(content or "")
    if not text.startswith(_TOOL_FAILURE_SUMMARY_PREFIX):
        return None
    body = text[len(_TOOL_FAILURE_SUMMARY_PREFIX) :].strip()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = _extract_braced_payload(body)
    if not isinstance(payload, dict):
        return None
    return dict(payload)


def format_tool_failure_summary(payload: dict[str, Any]) -> str:
    return _TOOL_FAILURE_SUMMARY_PREFIX + json.dumps(payload, ensure_ascii=False)


def tool_output_summary_payload(role: str, content: str) -> dict[str, Any] | None:
    """Return a prompt-safe summary for oversized non-failure tool output."""

    text = str(content or "")
    if str(role or "").strip().lower() not in {"assistant", "tool", "tool_result"}:
        return None
    if not text.strip():
        return None

    payload = _extract_braced_payload(text)
    status = "offloaded"
    if _payload_indicates_success(payload):
        status = "succeeded"
    elif _payload_indicates_failure(payload):
        status = "failed"

    inferred_tool = _infer_tool_failure_tool(text)
    if inferred_tool == "unknown":
        file_value = _deep_get_text(payload, ("file", "path", "target_path"))
        content_value = _deep_get_value(payload, ("content",))
        if file_value and isinstance(content_value, str):
            # Director read receipts commonly omit the redundant ``tool`` field
            # and contain only {file, content}.  Calling those receipts unknown
            # prevents ContextOS from applying read-result visibility policy.
            inferred_tool = "read_file"

    summary: dict[str, Any] = {
        "tool": inferred_tool,
        "status": status,
        "content_chars": len(text),
        "prompt_safe": True,
        "observation_only": True,
        "receipt_detail": "full output omitted; see receipt_refs for audit evidence",
    }

    for output_key, source_keys in (
        ("file", ("file", "path", "target_path")),
        ("operation", ("operation",)),
        ("changed_files", ("changed_files", "changed_file", "files")),
        ("bytes_written", ("bytes_written", "bytes")),
        ("syntax_check", ("syntax_check",)),
        ("exit_code", ("exit_code", "returncode")),
    ):
        value = _deep_get_text(payload, source_keys)
        if value:
            summary[output_key] = _trim_prompt_safe_text(value)

    return summary


def format_tool_output_summary(payload: dict[str, Any]) -> str:
    return _TOOL_OUTPUT_SUMMARY_PREFIX + json.dumps(payload, ensure_ascii=False)


def prompt_safe_tool_failure_summary(role: str, content: str) -> str | None:
    """Return a compact failure summary when raw tool receipts would leak."""

    summary = tool_failure_summary_payload(role, content)
    if summary is None:
        return None
    return format_tool_failure_summary(summary)


def prompt_safe_tool_output_summary(role: str, content: str) -> str | None:
    """Return a compact summary for prompt-visible oversized tool receipts."""

    summary = tool_output_summary_payload(role, content)
    if summary is None:
        return None
    return format_tool_output_summary(summary)


def prompt_safe_message_content(role: str, content: Any) -> str:
    """Return message content safe to include in LLM prompts."""

    text = str(content or "")
    return prompt_safe_tool_failure_summary(role, text) or text
