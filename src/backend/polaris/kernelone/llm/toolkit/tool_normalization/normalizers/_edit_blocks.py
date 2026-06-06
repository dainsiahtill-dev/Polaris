"""edit_blocks argument normalizer — heals weak-model list-shaped blocks.

Weak local models (e.g. gemma) frequently emit the ``blocks`` argument of
``edit_blocks`` as a JSON list — either a list of block strings, or a list of
``{search, replace[, file]}`` dicts — instead of the canonical Aider-style
SEARCH/REPLACE text string that the schema and handler require. Without
coercion the call is rejected at schema validation ("Expected string, got
list"), so the model's intended edit never lands.

This Stage-2 normalizer converts those list shapes into the canonical
SEARCH/REPLACE block string::

    <<<< SEARCH:filepath
    <original>
    ====
    <replacement>
    >>>> REPLACE

If a list entry has an unrecognized shape, the value is left untouched so the
existing validator surfaces the error rather than silently mangling input.
"""

from __future__ import annotations

from typing import Any

_SEARCH_KEYS = ("search", "search_text", "old", "old_string", "before", "original")
_REPLACE_KEYS = ("replace", "replace_text", "new", "new_string", "after", "updated", "replacement")
_FILE_KEYS = ("file", "filepath", "path", "filename")
_BLOCK_PARAM_KEYS = ("blocks", "content", "edits")


def _first_str(entry: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = entry.get(key)
        if value is not None:
            return str(value)
    return None


def _dict_to_block(entry: dict[str, Any], default_file: str | None) -> str | None:
    search = _first_str(entry, _SEARCH_KEYS)
    replace = _first_str(entry, _REPLACE_KEYS)
    if search is None or replace is None:
        return None
    file = _first_str(entry, _FILE_KEYS) or default_file
    head = f"<<<< SEARCH:{file}" if file else "<<<< SEARCH"
    return f"{head}\n{search}\n====\n{replace}\n>>>> REPLACE"


def _coerce_blocks_to_text(value: Any, default_file: str | None) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return None
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            block = _dict_to_block(item, default_file)
            if block is None:
                return None  # unknown dict shape -> defer to validator
            parts.append(block)
        else:
            return None
    return "\n\n".join(part for part in parts if part.strip())


def normalize_edit_blocks_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Coerce list-shaped edit_blocks ``blocks`` arguments into SEARCH/REPLACE text."""
    args = dict(tool_args)

    default_file: str | None = None
    for file_key in _FILE_KEYS:
        candidate = args.get(file_key)
        if isinstance(candidate, str) and candidate.strip():
            default_file = candidate.strip()
            break

    for key in _BLOCK_PARAM_KEYS:
        value = args.get(key)
        if isinstance(value, list):
            coerced = _coerce_blocks_to_text(value, default_file)
            if coerced is not None:
                args[key] = coerced

    # Ensure the canonical `blocks` param carries the text even if the model
    # only supplied content/edits (the handler accepts any, but schema
    # validation keys on `blocks`).
    blocks_value = args.get("blocks")
    if not (isinstance(blocks_value, str) and blocks_value.strip()):
        for fallback_key in ("content", "edits"):
            fallback_value = args.get(fallback_key)
            if isinstance(fallback_value, str) and fallback_value.strip():
                args["blocks"] = fallback_value
                break

    return args
