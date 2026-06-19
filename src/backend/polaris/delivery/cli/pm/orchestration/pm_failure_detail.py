"""PM provider failure detection and desktop-facing failure text.

Extracted from ``orchestration_engine``. These helpers decide whether a
zero-task recovery would mask a PM provider invocation failure, annotate the
PM contract so failures fail closed in UIs/tests, and render concise failure
detail strings from persisted evidence.

Bodies are byte-for-byte identical to the original ``orchestration_engine``
definitions and are re-exported from that module to preserve the canonical
import path.
"""

from __future__ import annotations

from typing import Any


def _downgrade_recovered_pm_invoke_error(
    *,
    pm_state: dict[str, Any],
    pm_state_full: str,
    timestamp: str,
) -> bool:
    """Deprecated guard: PM invoke failures must remain visible after fallback attempts.

    A deterministic requirements fallback can be useful when the PM response is
    empty or malformed, but it is not evidence that the configured runtime
    provider is usable. Clearing ``PM_LLM_INVOKE_FAILED`` caused real provider
    failures to appear as healthy fallback task generation in the UI.
    """
    error_code = str(pm_state.get("last_pm_error_code") or "").strip()
    if error_code != "PM_LLM_INVOKE_FAILED":
        return False

    _ = (pm_state_full, timestamp)
    return False


def _pm_invoke_failed(pm_state: dict[str, Any], normalized: dict[str, Any]) -> bool:
    """Return True when zero-task recovery would mask a PM provider failure."""
    state_code = str(pm_state.get("last_pm_error_code") or "").strip()
    if state_code == "PM_LLM_INVOKE_FAILED":
        return True
    notes = str(normalized.get("notes") or "").strip().lower()
    warnings = normalized.get("schema_warnings")
    warning_text = "\n".join(str(item) for item in warnings) if isinstance(warnings, list) else ""
    combined = f"{notes}\n{warning_text}".lower()
    return "pm invoke failed" in combined or "provider invocation failed" in combined


def _append_unique_schema_warning(normalized: dict[str, Any], warning: str) -> None:
    text = str(warning or "").strip()
    if not text:
        return
    raw_warnings = normalized.get("schema_warnings")
    schema_warnings = (
        [str(item) for item in raw_warnings if str(item).strip()] if isinstance(raw_warnings, list) else []
    )
    if text not in schema_warnings:
        schema_warnings.append(text)
    normalized["schema_warnings"] = schema_warnings
    normalized["schema_warning_count"] = len(schema_warnings)


def _compact_pm_failure_text(value: Any, *, max_chars: int = 260) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)].rstrip() + "..."


def _first_schema_warning(normalized: dict[str, Any]) -> str:
    raw_warnings = normalized.get("schema_warnings")
    if not isinstance(raw_warnings, list):
        return ""
    for warning in raw_warnings:
        text = _compact_pm_failure_text(warning)
        if text:
            return text
    return ""


def _build_pm_failure_detail(
    *,
    pm_state: dict[str, Any],
    normalized: dict[str, Any],
    fallback: str,
) -> str:
    """Build a concise desktop-facing PM failure detail from persisted evidence."""

    code = _compact_pm_failure_text(
        normalized.get("terminal_error_code") or pm_state.get("last_pm_error_code"),
        max_chars=80,
    )
    detail = _compact_pm_failure_text(
        normalized.get("terminal_error")
        or pm_state.get("last_pm_error_detail")
        or _first_schema_warning(normalized)
        or normalized.get("notes")
        or fallback
    )
    if code and detail:
        return f"{code}: {detail}"
    return detail or code or fallback


def _mark_pm_invoke_terminal_failure(
    pm_state: dict[str, Any],
    normalized: dict[str, Any],
    *,
    warning: str = "",
) -> None:
    """Annotate the PM contract so provider failures fail closed in UIs/tests."""

    detail = str(
        pm_state.get("last_pm_error_detail")
        or normalized.get("terminal_error")
        or normalized.get("notes")
        or "PM runtime provider invocation failed"
    ).strip()
    normalized["terminal_error_code"] = "PM_LLM_INVOKE_FAILED"
    normalized["terminal_error"] = detail
    _append_unique_schema_warning(normalized, warning)
