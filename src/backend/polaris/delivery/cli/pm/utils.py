"""General utility functions for loop-pm."""

import json
import os
from typing import Any

from polaris.kernelone.runtime.shared_types import (
    is_docs_path as _is_docs_path_impl,
    normalize_path as _normalize_path_impl,
    normalize_path_list as _normalize_path_list_impl,
    normalize_policy_decision as _normalize_policy_decision_impl,
    normalize_str_list as _normalize_str_list_impl,
)


def truncate_text_block(text: str, max_chars: int = 4000) -> str:
    """Truncate text block to max characters."""
    if not text:
        return ""
    text = text.strip("\n")
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars].rstrip() + "\n...[truncated]"
    return text


def _is_interactive_session() -> bool:
    """Check if running in an interactive terminal session."""
    try:
        stdin_tty = bool(__import__("sys").stdin and __import__("sys").stdin.isatty())
    except (RuntimeError, ValueError):
        stdin_tty = False
    try:
        stdout_tty = bool(__import__("sys").stdout and __import__("sys").stdout.isatty())
    except (RuntimeError, ValueError):
        stdout_tty = False
    return stdin_tty and stdout_tty


def read_json_file(path: str) -> Any:
    """Read JSON file."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (RuntimeError, ValueError):
        return None


def read_tail_lines(path: str, max_lines: int = 200) -> list[str]:
    """Read last N lines from a file."""
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            pos = handle.tell()
            block = 4096
            data = b""
            while pos > 0 and data.count(b"\n") <= max_lines:
                read_size = block if pos >= block else pos
                pos -= read_size
                handle.seek(pos)
                data = handle.read(read_size) + data
    except (RuntimeError, ValueError):
        return []
    text = data.decode("utf-8", errors="ignore")
    lines = text.splitlines()
    if max_lines > 0 and len(lines) > max_lines:
        return lines[-max_lines:]
    return lines


def append_text(path: str, text: str) -> None:
    """Append text to file."""
    if not path:
        return
    from polaris.infrastructure.compat.io_utils import ensure_parent_dir

    ensure_parent_dir(path)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text or "")


def format_json_for_prompt(payload: Any, max_chars: int = 2000) -> str:
    """Format JSON payload for prompt with truncation."""
    if payload is None:
        return "none"
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    except (RuntimeError, ValueError):
        text = str(payload)
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars] + "..."
    return text


def compact_text(text: str, max_len: int = 360) -> str:
    """Compact text by removing extra whitespace and truncating."""
    if not text:
        return ""
    text = " ".join(str(text).split())
    if max_len > 0 and len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _slug_token(value: Any, fallback: str = "task") -> str:
    """Create a slug token from a value."""
    raw = str(value or "").strip().replace("\\", "-").replace("/", "-").replace(" ", "-")
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in ("-", "_", "."))
    cleaned = cleaned.strip("._-")
    return cleaned or fallback


def _use_context_engine_v2() -> bool:
    """Check if context engine v2 should be used."""
    value = str(os.environ.get("POLARIS_CONTEXT_ENGINE", "")).strip().lower()
    return value in ("v2", "context_v2", "engine_v2", "context-engine-v2")


def auto_plan_enabled() -> bool:
    """Check if auto-plan is enabled."""
    value = os.environ.get("POLARIS_AUTO_PLAN", "1").strip().lower()
    return value not in ("0", "false", "no", "off")


def is_qa_enabled() -> bool:
    """Check if QA is enabled."""
    raw = str(os.environ.get("POLARIS_QA_ENABLED", "1")).strip().lower()
    return raw not in ("0", "false", "no", "off")


def normalize_str_list(value: Any) -> list[str]:
    """Normalize value to list of strings."""
    return _normalize_str_list_impl(value)


def normalize_path_list(value: Any) -> list[str]:
    """Normalize value to canonical relative path list."""
    return _normalize_path_list_impl(value)


def normalize_path(value: Any) -> str:
    """Normalize a single path to canonical relative form."""
    return _normalize_path_impl(str(value or ""))


def _is_docs_path(path: str) -> bool:
    """Check if path is within docs directory."""
    return _is_docs_path_impl(path)


def _normalize_scope_list(value: Any) -> list[str]:
    """Normalize scope list value."""
    if isinstance(value, str) and value.strip():
        return normalize_path_list([seg.strip() for seg in value.split(",") if seg.strip()])
    return normalize_path_list(value)


def _normalize_policy_decision(value: Any) -> str:
    """Normalize policy decision value."""
    return _normalize_policy_decision_impl(value)


def _normalize_audit_result(value: Any) -> str:
    """Normalize audit result value."""
    if isinstance(value, bool):
        return "pass" if value else "fail"
    token = str(value or "").strip().lower()
    if token in ("pass", "passed", "ok", "success"):
        return "pass"
    if token in ("fail", "failed", "reject", "rejected"):
        return "fail"
    return ""


def should_pause_for_manual_intervention(error_code: str) -> bool:
    """Check if error code requires manual intervention pause."""
    code = str(error_code or "").strip().upper()
    if not code:
        return False
    if code == "DIRECTOR_NO_RESULT":
        return True
    if code.startswith("DIRECTOR_EXIT_"):
        return True
    return code in {"DIRECTOR_ENTRY_MISSING", "DIRECTOR_START_FAILED"}


def requires_manual_intervention_for_error(
    error_code: str,
    director_started: bool,
    execution_started: bool | None = None,
) -> bool:
    """Determine if manual intervention is required for an error."""
    if execution_started is True:
        return False
    if execution_started is False:
        return should_pause_for_manual_intervention(error_code)
    if director_started:
        return False
    return should_pause_for_manual_intervention(error_code)


__all__ = [
    "_is_docs_path",
    "_is_interactive_session",
    "_normalize_audit_result",
    "_normalize_policy_decision",
    "_normalize_scope_list",
    "_slug_token",
    "_use_context_engine_v2",
    "append_text",
    "auto_plan_enabled",
    "compact_text",
    "format_json_for_prompt",
    "is_qa_enabled",
    "normalize_path",
    "normalize_path_list",
    "normalize_str_list",
    "read_json_file",
    "read_tail_lines",
    "requires_manual_intervention_for_error",
    "should_pause_for_manual_intervention",
    "truncate_text_block",
]
