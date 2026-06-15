"""edit_file argument normalization."""

from __future__ import annotations

from typing import Any

from ._file_path import normalize_file_path_args

_LINE_RANGE_CONTENT_KEYS: tuple[str, ...] = (
    "new_text",
    "replacement",
    "code",
    "source",
    "body",
)


def _first_non_empty(args: dict[str, Any], keys: tuple[str, ...]) -> tuple[str, Any] | None:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value:
            return key, value
    return None


def normalize_edit_file_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Normalize edit_file body aliases without changing edit intent.

    `replacement` is context-sensitive:
    - line-range mode: replacement/new_text/code/source/body are the new line content.
    - search mode: replacement is the search/replace replacement value.
    """

    normalized = normalize_file_path_args(tool_args)
    has_line_range = normalized.get("start_line") is not None or normalized.get("end_line") is not None
    has_search = normalized.get("search") is not None

    if has_line_range and not normalized.get("content"):
        match = _first_non_empty(normalized, _LINE_RANGE_CONTENT_KEYS)
        if match is not None:
            key, value = match
            normalized["content"] = value
            normalized.pop(key, None)
    elif has_search and not normalized.get("replace"):
        replacement = normalized.get("replacement")
        if isinstance(replacement, str):
            normalized["replace"] = replacement
            normalized.pop("replacement", None)

    return normalized
