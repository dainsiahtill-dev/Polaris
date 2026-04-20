from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_CONSTRAINT_MODES = {"off", "warn", "strict"}
_CONSTRAINT_MODE_ALIASES = {
    "enforce": "strict",
    "error": "strict",
    "errors": "strict",
    "on": "warn",
    "default": "warn",
}
_SCHEMA_DIR = Path(__file__).resolve().parent


def normalize_constraint_mode(value: Any, default_mode: str = "warn") -> str:
    token = str(value or default_mode).strip().lower()
    token = _CONSTRAINT_MODE_ALIASES.get(token, token)
    if token in _CONSTRAINT_MODES:
        return token
    fallback = str(default_mode or "warn").strip().lower()
    fallback = _CONSTRAINT_MODE_ALIASES.get(fallback, fallback)
    return fallback if fallback in _CONSTRAINT_MODES else "warn"


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _ensure_list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            out.append(dict(item))
    return out


def _apply_or_raise(
    *,
    mode: str,
    warnings: list[str],
    repair_count: int,
    message: str,
) -> tuple[list[str], int]:
    if mode == "strict":
        raise ValueError(message)
    warnings.append(message)
    return warnings, int(repair_count) + 1


@lru_cache(maxsize=16)
def _load_schema(schema_file: str) -> dict[str, Any]:
    path = (_SCHEMA_DIR / str(schema_file)).resolve()
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"schema root must be object: {path}")
    return payload


def _type_matches(value: Any, expected: str) -> bool:
    token = str(expected).strip().lower()
    if token == "object":
        return isinstance(value, dict)
    if token == "array":
        return isinstance(value, list)
    if token == "string":
        return isinstance(value, str)
    if token == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if token == "number":
        return (isinstance(value, int) and not isinstance(value, bool)) or isinstance(value, float)
    if token == "boolean":
        return isinstance(value, bool)
    if token == "null":
        return value is None
    return True


def _schema_type_ok(value: Any, expected_type: Any) -> bool:
    if isinstance(expected_type, str):
        return _type_matches(value, expected_type)
    if isinstance(expected_type, list):
        return any(isinstance(item, str) and _type_matches(value, item) for item in expected_type)
    return True


def _validate_schema_subset(
    value: Any,
    schema: dict[str, Any],
    *,
    path: str = "$",
) -> list[str]:
    errors: list[str] = []

    expected_type = schema.get("type")
    if expected_type is not None and not _schema_type_ok(value, expected_type):
        errors.append(f"{path} type mismatch: expected {expected_type}, got {type(value).__name__}")
        return errors

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        errors.append(f"{path} enum mismatch: {value!r} not in {enum_values!r}")

    if isinstance(value, str):
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            errors.append(f"{path} minLength violation: {len(value)} < {min_length}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and float(value) < float(minimum):
            errors.append(f"{path} minimum violation: {value} < {minimum}")

    if isinstance(value, list):
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            errors.append(f"{path} minItems violation: {len(value)} < {min_items}")
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for idx, item in enumerate(value):
                errors.extend(
                    _validate_schema_subset(
                        item,
                        items_schema,
                        path=f"{path}[{idx}]",
                    )
                )

    if isinstance(value, dict):
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    errors.append(f"{path}.{key} missing required field")

        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, sub_schema in properties.items():
                if key in value and isinstance(sub_schema, dict):
                    errors.extend(
                        _validate_schema_subset(
                            value[key],
                            sub_schema,
                            path=f"{path}.{key}",
                        )
                    )

            if schema.get("additionalProperties") is False:
                allowed = {key for key in properties if isinstance(key, str)}
                for key in value:
                    if str(key) not in allowed:
                        errors.append(f"{path}.{key} additionalProperties not allowed")

    return errors


def _apply_schema_contract(
    *,
    payload: dict[str, Any],
    schema_file: str,
    label: str,
    mode: str,
    warnings: list[str],
    repair_count: int,
) -> tuple[list[str], int]:
    try:
        schema = _load_schema(schema_file)
    except (RuntimeError, ValueError) as exc:  # pragma: no cover - schema file integrity guard
        return _apply_or_raise(
            mode=mode,
            warnings=warnings,
            repair_count=repair_count,
            message=f"{label} schema unavailable: {exc}",
        )

    violations = _validate_schema_subset(payload, schema, path="$")
    if not violations:
        return warnings, repair_count

    limit = 20
    for violation in violations[:limit]:
        warnings, repair_count = _apply_or_raise(
            mode=mode,
            warnings=warnings,
            repair_count=repair_count,
            message=f"{label} schema violation: {violation}",
        )
    if len(violations) > limit:
        warnings, repair_count = _apply_or_raise(
            mode=mode,
            warnings=warnings,
            repair_count=repair_count,
            message=f"{label} schema violation: +{len(violations) - limit} more",
        )
    return warnings, repair_count


def enforce_context_pack_contract(pack: dict[str, Any], mode: str) -> tuple[dict[str, Any], list[str], int]:
    normalized_mode = normalize_constraint_mode(mode, default_mode="warn")
    if normalized_mode == "off":
        return dict(pack), [], 0

    payload = dict(pack)
    warnings: list[str] = []
    repair_count = 0

    if _coerce_int(payload.get("version", 0), 0) <= 0:
        warnings, repair_count = _apply_or_raise(
            mode=normalized_mode,
            warnings=warnings,
            repair_count=repair_count,
            message="context pack version missing/invalid",
        )
        payload["version"] = 1

    if not str(payload.get("task", "")).strip():
        warnings, repair_count = _apply_or_raise(
            mode=normalized_mode,
            warnings=warnings,
            repair_count=repair_count,
            message="context pack task missing/invalid",
        )
        payload["task"] = str(payload.get("task", "")).strip() or "unknown_task"

    if not isinstance(payload.get("top_files"), list):
        warnings, repair_count = _apply_or_raise(
            mode=normalized_mode,
            warnings=warnings,
            repair_count=repair_count,
            message="context pack top_files must be list",
        )
        payload["top_files"] = []

    if not isinstance(payload.get("snippets"), list):
        warnings, repair_count = _apply_or_raise(
            mode=normalized_mode,
            warnings=warnings,
            repair_count=repair_count,
            message="context pack snippets must be list",
        )
        payload["snippets"] = []

    verify_plan = payload.get("verify_plan")
    if not isinstance(verify_plan, dict):
        warnings, repair_count = _apply_or_raise(
            mode=normalized_mode,
            warnings=warnings,
            repair_count=repair_count,
            message="context pack verify_plan must be object",
        )
        verify_plan = {}
        payload["verify_plan"] = verify_plan

    target_tests = verify_plan.get("target_tests", [])
    if not isinstance(target_tests, list):
        warnings, repair_count = _apply_or_raise(
            mode=normalized_mode,
            warnings=warnings,
            repair_count=repair_count,
            message="context pack verify_plan.target_tests must be list",
        )
        verify_plan["target_tests"] = []
    else:
        verify_plan["target_tests"] = [str(item) for item in target_tests if str(item).strip()]

    target_checks = verify_plan.get("target_checks", [])
    if not isinstance(target_checks, list):
        warnings, repair_count = _apply_or_raise(
            mode=normalized_mode,
            warnings=warnings,
            repair_count=repair_count,
            message="context pack verify_plan.target_checks must be list",
        )
        verify_plan["target_checks"] = []
    else:
        verify_plan["target_checks"] = [str(item) for item in target_checks if str(item).strip()]

    snippets = _ensure_list_of_dicts(payload.get("snippets"))
    repaired_snippets: list[dict[str, Any]] = []
    for snippet in snippets:
        fixed = dict(snippet)
        for key in ("path", "content", "reason", "symbol"):
            fixed[key] = str(fixed.get(key, ""))
        fixed["start_line"] = max(1, _coerce_int(fixed.get("start_line", 1), 1))
        fixed["end_line"] = max(
            fixed["start_line"], _coerce_int(fixed.get("end_line", fixed["start_line"]), fixed["start_line"])
        )
        repaired_snippets.append(fixed)
    payload["snippets"] = repaired_snippets

    warnings, repair_count = _apply_schema_contract(
        payload=payload,
        schema_file="context_pack.schema.json",
        label="context_pack",
        mode=normalized_mode,
        warnings=warnings,
        repair_count=repair_count,
    )

    return payload, warnings, repair_count


def enforce_context_payload_contract(payload: dict[str, Any], mode: str) -> tuple[dict[str, Any], list[str], int]:
    normalized_mode = normalize_constraint_mode(mode, default_mode="warn")
    if normalized_mode == "off":
        return dict(payload), [], 0

    out = dict(payload)
    warnings: list[str] = []
    repair_count = 0

    int_fields = (
        "estimated_tokens",
        "estimated_source_tokens",
        "estimated_changed_files_tokens",
        "estimated_snippets_only_tokens",
        "selected_tests_count",
        "selected_checks_count",
    )
    for field in int_fields:
        value = _coerce_int(out.get(field, 0), 0)
        value = max(value, 0)
        if out.get(field) != value:
            warnings, repair_count = _apply_or_raise(
                mode=normalized_mode,
                warnings=warnings,
                repair_count=repair_count,
                message=f"context payload field {field} repaired",
            )
        out[field] = int(value)

    if "status" in out and not isinstance(out.get("status"), str):
        warnings, repair_count = _apply_or_raise(
            mode=normalized_mode,
            warnings=warnings,
            repair_count=repair_count,
            message="context payload status must be string",
        )
        out["status"] = str(out.get("status", "ok"))
    if "out" in out and not isinstance(out.get("out"), str):
        warnings, repair_count = _apply_or_raise(
            mode=normalized_mode,
            warnings=warnings,
            repair_count=repair_count,
            message="context payload out must be string",
        )
        out["out"] = str(out.get("out", ""))

    for field in ("top_files", "snippets", "selected_tests_count", "selected_checks_count"):
        if field in out:
            out[field] = max(0, _coerce_int(out.get(field), 0))

    verify_plan = out.get("verify_plan")
    if verify_plan is not None and not isinstance(verify_plan, dict):
        warnings, repair_count = _apply_or_raise(
            mode=normalized_mode,
            warnings=warnings,
            repair_count=repair_count,
            message="context payload verify_plan must be object",
        )
        verify_plan = {}
    if isinstance(verify_plan, dict):
        target_tests = verify_plan.get("target_tests", [])
        if not isinstance(target_tests, list):
            warnings, repair_count = _apply_or_raise(
                mode=normalized_mode,
                warnings=warnings,
                repair_count=repair_count,
                message="context payload verify_plan.target_tests must be list",
            )
            verify_plan["target_tests"] = []
        else:
            verify_plan["target_tests"] = [str(item) for item in target_tests if str(item).strip()]
        target_checks = verify_plan.get("target_checks", [])
        if not isinstance(target_checks, list):
            warnings, repair_count = _apply_or_raise(
                mode=normalized_mode,
                warnings=warnings,
                repair_count=repair_count,
                message="context payload verify_plan.target_checks must be list",
            )
            verify_plan["target_checks"] = []
        else:
            verify_plan["target_checks"] = [str(item) for item in target_checks if str(item).strip()]
        out["verify_plan"] = verify_plan

    warnings_value = out.get("warnings")
    if warnings_value is None:
        out["warnings"] = []
    elif not isinstance(warnings_value, list):
        warnings, repair_count = _apply_or_raise(
            mode=normalized_mode,
            warnings=warnings,
            repair_count=repair_count,
            message="context payload warnings must be list",
        )
        out["warnings"] = []
    else:
        out["warnings"] = [str(item) for item in warnings_value]

    if any(field in out for field in ("status", "out", "verify_plan", "token_reduction")):
        warnings, repair_count = _apply_schema_contract(
            payload=out,
            schema_file="mcp_context_response.schema.json",
            label="mcp_context_response",
            mode=normalized_mode,
            warnings=warnings,
            repair_count=repair_count,
        )

    return out, warnings, repair_count


def enforce_verify_summary_contract(
    summary: dict[str, Any],
    *,
    status: dict[str, Any],
    mode: str,
) -> tuple[dict[str, Any], list[str], int]:
    normalized_mode = normalize_constraint_mode(mode, default_mode="warn")
    if normalized_mode == "off":
        out = dict(summary)
        out.setdefault("state_source", "raw")
        out.setdefault("constraint_repair_count", 0)
        return out, [], 0

    payload = dict(summary)
    warnings: list[str] = []
    repair_count = 0

    latest_state = str(payload.get("latest_state", "")).strip().lower()
    status_state = str(status.get("state", "")).strip().lower()
    terminal_states = {"completed", "failed", "cancelled"}
    state_source = str(payload.get("state_source", "events")).strip().lower() or "events"

    if status_state in terminal_states:
        if latest_state != status_state:
            warnings, repair_count = _apply_or_raise(
                mode=normalized_mode,
                warnings=warnings,
                repair_count=repair_count,
                message=f"verify summary latest_state repaired from {latest_state or 'empty'} to {status_state}",
            )
            payload["latest_state"] = status_state
        else:
            payload["latest_state"] = status_state
        state_source = "status_terminal"
    elif latest_state:
        payload["latest_state"] = latest_state
    else:
        warnings, repair_count = _apply_or_raise(
            mode=normalized_mode,
            warnings=warnings,
            repair_count=repair_count,
            message="verify summary latest_state missing, using status state",
        )
        payload["latest_state"] = status_state or "unknown"
        state_source = "status_fallback"

    payload["state_source"] = state_source
    payload["constraint_repair_count"] = int(repair_count)
    return payload, warnings, repair_count


def enforce_verify_events_payload_contract(
    payload: dict[str, Any],
    mode: str,
) -> tuple[dict[str, Any], list[str], int]:
    normalized_mode = normalize_constraint_mode(mode, default_mode="warn")
    if normalized_mode == "off":
        return dict(payload), [], 0

    out = dict(payload)
    warnings: list[str] = []
    repair_count = 0

    if not isinstance(out.get("job_id"), str):
        warnings, repair_count = _apply_or_raise(
            mode=normalized_mode,
            warnings=warnings,
            repair_count=repair_count,
            message="verify events payload job_id must be string",
        )
        out["job_id"] = str(out.get("job_id", ""))

    events_value = out.get("events")
    if not isinstance(events_value, list):
        warnings, repair_count = _apply_or_raise(
            mode=normalized_mode,
            warnings=warnings,
            repair_count=repair_count,
            message="verify events payload events must be list",
        )
        events_value = []
    normalized_events: list[dict[str, Any]] = []
    for idx, raw_event in enumerate(events_value):
        if not isinstance(raw_event, dict):
            warnings, repair_count = _apply_or_raise(
                mode=normalized_mode,
                warnings=warnings,
                repair_count=repair_count,
                message=f"verify events payload events[{idx}] must be object",
            )
            continue
        event = dict(raw_event)
        event_name = str(event.get("event", "")).strip()
        if not event_name:
            warnings, repair_count = _apply_or_raise(
                mode=normalized_mode,
                warnings=warnings,
                repair_count=repair_count,
                message=f"verify events payload events[{idx}].event missing/invalid",
            )
            event["event"] = "unknown"
        else:
            event["event"] = event_name
        seq = _coerce_int(event.get("seq"), idx + 1)
        event["seq"] = max(1, seq)
        if "ts" in event:
            event["ts"] = str(event.get("ts", ""))
        normalized_events.append(event)
    out["events"] = normalized_events

    out["count"] = max(0, _coerce_int(out.get("count", len(normalized_events)), len(normalized_events)))
    if out["count"] != len(normalized_events):
        warnings, repair_count = _apply_or_raise(
            mode=normalized_mode,
            warnings=warnings,
            repair_count=repair_count,
            message="verify events payload count repaired to match events length",
        )
        out["count"] = len(normalized_events)

    out["total_available"] = max(
        0,
        _coerce_int(out.get("total_available", out["count"]), out["count"]),
    )
    out["max_events"] = max(1, _coerce_int(out.get("max_events", 30), 30))
    out["since_seq"] = max(0, _coerce_int(out.get("since_seq", 0), 0))
    out["truncated"] = bool(out.get("truncated", False))

    summary = out.get("summary")
    if summary is not None and not isinstance(summary, dict):
        warnings, repair_count = _apply_or_raise(
            mode=normalized_mode,
            warnings=warnings,
            repair_count=repair_count,
            message="verify events payload summary must be object",
        )
        summary = {}
    if isinstance(summary, dict):
        summary_payload = dict(summary)
        if "latest_state" in summary_payload:
            summary_payload["latest_state"] = str(summary_payload.get("latest_state", ""))
        if "latest_stage" in summary_payload:
            summary_payload["latest_stage"] = str(summary_payload.get("latest_stage", ""))
        if "state_source" in summary_payload:
            summary_payload["state_source"] = str(summary_payload.get("state_source", "events"))
        summary_payload["constraint_repair_count"] = max(
            0,
            _coerce_int(summary_payload.get("constraint_repair_count", 0), 0),
        )
        event_type_counts = summary_payload.get("event_type_counts")
        if event_type_counts is not None and not isinstance(event_type_counts, dict):
            warnings, repair_count = _apply_or_raise(
                mode=normalized_mode,
                warnings=warnings,
                repair_count=repair_count,
                message="verify events payload summary.event_type_counts must be object",
            )
            summary_payload["event_type_counts"] = {}
        out["summary"] = summary_payload

    warnings, repair_count = _apply_schema_contract(
        payload=out,
        schema_file="mcp_verify_events.schema.json",
        label="mcp_verify_events",
        mode=normalized_mode,
        warnings=warnings,
        repair_count=repair_count,
    )
    return out, warnings, repair_count
