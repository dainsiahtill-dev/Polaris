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

import ast
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_SEARCH_KEYS = ("search", "search_text", "old", "old_string", "before", "original", "source", "from")
_REPLACE_KEYS = (
    "replace",
    "replace_text",
    "new",
    "new_string",
    "after",
    "updated",
    "replacement",
    "to",
    "target",
)
_FILE_KEYS = (
    "file",
    "filepath",
    "path",
    "filename",
    "file_path",
    "target_file",
    "target_path",
    "filePath",
    "targetFile",
    "targetPath",
)
_START_KEYS = ("start", "start_line", "startLine", "line_start", "lineStart", "from_line", "fromLine")
_END_KEYS = ("end", "end_line", "endLine", "line_end", "lineEnd", "to_line", "toLine")
_LINE_RANGE_REPLACE_KEYS = (
    "replace",
    "new_text",
    "newText",
    "new_content",
    "newContent",
    "new_code",
    "newCode",
    "replacement",
    "replacement_text",
    "replacementText",
    "code",
)
# Keys under which a model sometimes nests the FULL SEARCH/REPLACE text of one block.
_DIRECT_BLOCK_KEYS = ("block", "text", "content", "diff", "code", "value", "edit")
_BLOCK_PARAM_KEYS = ("blocks", "content", "edits")
_NESTED_LINE_RANGE_EDIT_KEYS = ("edit", "edits")
_LABELED_LINE_RANGE_RE = re.compile(
    r"(?is)\b(?:file|filepath|path|filename|file_path|target_file|target_path)\s*:\s*(?P<file>[^\s,;]+).*?"
    r"\b(?:start|start_line|startLine|line_start|lineStart|from_line|fromLine)\s*:\s*(?P<start>\d+).*?"
    r"\b(?:end|end_line|endLine|line_end|lineEnd|to_line|toLine)\s*:\s*(?P<end>\d+).*?"
    r"\b(?:replace|new_text|newText|new_content|newContent|new_code|newCode|replacement|replacement_text|"
    r"replacementText|code)\s*:\s*(?P<replace>.+)\Z"
)
_JSONISH_LINE_RANGE_RE = re.compile(
    r"(?is)^\s*\{\s*[\"']?(?:file|filepath|path|filename|file_path|target_file|target_path)[\"']?\s*:\s*"
    r"[\"']?(?P<file>[^\"',}\s]+)[\"']?\s*,\s*"
    r"[\"']?(?:start|start_line|startLine|line_start|lineStart|from_line|fromLine)[\"']?\s*:\s*"
    r"[\"']?(?P<start>\d+)[\"']?\s*,\s*"
    r"[\"']?(?:end|end_line|endLine|line_end|lineEnd|to_line|toLine)[\"']?\s*:\s*"
    r"[\"']?(?P<end>\d+)[\"']?\s*,\s*"
    r"[\"']?(?:replace|new_text|newText|new_content|newContent|new_code|newCode|replacement|replacement_text|"
    r"replacementText|code)[\"']?\s*:\s*(?P<replace>.+?)\s*\}?\s*$"
)
_WEAK_FILE_MARKER_RE = re.compile(
    r"^(?:file|filepath|filename|file_path|path|target_file)\s*[:=]\s*(?P<path>.*?)\s*$",
    flags=re.IGNORECASE,
)


def _first_str(entry: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = entry.get(key)
        if value is not None:
            return str(value)
    return None


def _first_value(entry: dict[str, Any], keys: tuple[str, ...]) -> Any | None:
    for key in keys:
        if key in entry and entry[key] is not None:
            return entry[key]
    return None


def _dict_to_block(entry: dict[str, Any], default_file: str | None) -> str | None:
    search = _first_str(entry, _SEARCH_KEYS)
    replace = _first_str(entry, _REPLACE_KEYS)
    if search is not None and replace is not None:
        file = _first_str(entry, _FILE_KEYS) or default_file
        head = f"<<<< SEARCH:{file}" if file else "<<<< SEARCH"
        return f"{head}\n{search}\n====\n{replace}\n>>>> REPLACE"
    # Some models nest the whole block text under a single key (block/text/diff/...).
    direct = _first_str(entry, _DIRECT_BLOCK_KEYS)
    if direct is not None and direct.strip():
        return direct
    return None


def _pair_to_block(pair: list[Any], default_file: str | None) -> str | None:
    """Coerce a 2-element [search, replace] list into one block."""
    if len(pair) != 2 or not all(isinstance(x, str) for x in pair):
        return None
    head = f"<<<< SEARCH:{default_file}" if default_file else "<<<< SEARCH"
    return f"{head}\n{pair[0]}\n====\n{pair[1]}\n>>>> REPLACE"


def _coerce_positive_int(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 1 else None


def _coerce_line_range_mapping(entry: dict[str, Any]) -> dict[str, Any] | None:
    start = _coerce_positive_int(_first_value(entry, _START_KEYS))
    end = _coerce_positive_int(_first_value(entry, _END_KEYS))
    replace_value = _first_value(entry, _LINE_RANGE_REPLACE_KEYS)
    if start is None or end is None or start > end:
        return None
    if not isinstance(replace_value, str) or not replace_value.strip():
        return None
    result: dict[str, Any] = {
        "start": start,
        "end": end,
        "replace": replace_value,
    }
    file_value = _first_value(entry, _FILE_KEYS)
    if isinstance(file_value, str) and file_value.strip():
        result["file"] = file_value.strip()
    return result


def _coerce_nested_line_range_mapping(entry: dict[str, Any]) -> dict[str, Any] | None:
    for key in _NESTED_LINE_RANGE_EDIT_KEYS:
        value = entry.get(key)
        if isinstance(value, dict):
            return _coerce_line_range_mapping(value)
        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
            return _coerce_line_range_mapping(value[0])
    return None


def _coerce_blocks_line_range_args(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return _coerce_line_range_mapping(value) or _coerce_nested_line_range_mapping(value)
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        return _coerce_line_range_mapping(value[0])
    return None


def _strip_jsonish_replace(value: str) -> str:
    token = value.strip(" \t\r")
    if token.endswith("}"):
        token = token[:-1].rstrip()
    if token.startswith(("'", '"')):
        quote = token[0]
        token = token[1:]
        if token.endswith(quote):
            token = token[:-1]
    return token


def _strip_optional_code_fence(value: str) -> str:
    token = str(value or "").strip()
    if not token.startswith("```"):
        return str(value or "")
    lines = token.splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def _clean_weak_file_marker_path(value: str) -> str:
    path = str(value or "").strip().strip("`'\"")
    path = re.sub(r"\s+(?://|#).*$", "", path).strip()
    return path.strip("`'\"")


def _coerce_file_marker_blocks(value: str, default_file: str | None) -> dict[str, Any] | None:
    lines = _strip_optional_code_fence(value).splitlines(keepends=True)
    if not lines:
        return None
    header = lines[0].rstrip("\r\n").strip()
    match = _WEAK_FILE_MARKER_RE.match(header)
    if match is None:
        return None
    target_file = _clean_weak_file_marker_path(match.group("path"))
    body_start = 1
    if not target_file and len(lines) > 1:
        target_file = _clean_weak_file_marker_path(lines[1].rstrip("\r\n").strip())
        body_start = 2
    target_file = target_file or str(default_file or "").strip()
    body = "".join(lines[body_start:])
    if not target_file or not body.strip():
        return None
    return {
        "file": target_file,
        "blocks": body,
        "normalized_from_file_marker": True,
    }


def _coerce_tuple_line_range_args(value: str) -> dict[str, Any] | None:
    """Coerce weak-model ``(file, start, end, replace)`` strings.

    Qwen-style retries sometimes serialize a line-range edit as one Python
    tuple string, JSON/Python object string, or label-style text inside
    ``blocks``. Only unambiguous single line-range shapes are accepted;
    everything else stays on the existing fail-closed path.
    """

    raw_token = str(value or "")
    token = raw_token.strip()
    if not token:
        return None
    if token[0] == "{":
        try:
            parsed_mapping = ast.literal_eval(token)
        except (ValueError, SyntaxError):
            parsed_mapping = None
        if isinstance(parsed_mapping, dict):
            return _coerce_line_range_mapping(parsed_mapping) or _coerce_nested_line_range_mapping(parsed_mapping)
        jsonish_match = _JSONISH_LINE_RANGE_RE.match(raw_token)
        if jsonish_match is not None:
            groups = jsonish_match.groupdict()
            groups["replace"] = _strip_jsonish_replace(groups["replace"])
            return _coerce_line_range_mapping(groups)
        return None
    if token[0] not in "([":
        labeled_match = _LABELED_LINE_RANGE_RE.search(raw_token.strip(" \t\r"))
        if labeled_match is None:
            return None
        return _coerce_line_range_mapping(labeled_match.groupdict())
    try:
        parsed = ast.literal_eval(token)
    except (ValueError, SyntaxError):
        return None
    if not isinstance(parsed, (tuple, list)) or len(parsed) != 4:
        return None
    file_value, start_value, end_value, replace_value = parsed
    if not isinstance(file_value, str) or not file_value.strip():
        return None
    start = _coerce_positive_int(start_value)
    end = _coerce_positive_int(end_value)
    if start is None or end is None or start > end:
        return None
    if not isinstance(replace_value, str) or not replace_value.strip():
        return None
    return {
        "file": file_value.strip(),
        "start": start,
        "end": end,
        "replace": replace_value,
    }


def _coerce_blocks_to_text(value: Any, default_file: str | None) -> str | None:
    if isinstance(value, str):
        return value
    # A single dict (not wrapped in a list) — common weak-model shape.
    if isinstance(value, dict):
        return _dict_to_block(value, default_file)
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
        elif isinstance(item, list):
            block = _pair_to_block(item, default_file)
            if block is None:
                return None
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

    if default_file and not any(key in args for key in _BLOCK_PARAM_KEYS):
        coerced_top_level = _dict_to_block(args, default_file)
        if coerced_top_level is not None:
            args["blocks"] = coerced_top_level
            for key in (*_SEARCH_KEYS, *_REPLACE_KEYS):
                args.pop(key, None)

    for key in _BLOCK_PARAM_KEYS:
        value = args.get(key)
        if isinstance(value, str):
            line_range_args = _coerce_tuple_line_range_args(value)
            if line_range_args is not None:
                args.update(line_range_args)
                args.pop(key, None)
                continue
            file_marker_args = _coerce_file_marker_blocks(value, default_file)
            if file_marker_args is not None:
                args.update(file_marker_args)
                continue
        if isinstance(value, (list, dict)):
            line_range_args = _coerce_blocks_line_range_args(value)
            if line_range_args is not None:
                if default_file and "file" not in line_range_args:
                    line_range_args["file"] = default_file
                args.update(line_range_args)
                args.pop(key, None)
                continue
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

    # Diagnostic: if blocks is STILL a non-string container after coercion, the
    # model used a shape we don't yet recognize — log it so it can be supported
    # (the schema validator will reject it downstream, so this never silently passes).
    blocks_value = args.get("blocks")
    if isinstance(blocks_value, (list, dict)):
        sample = blocks_value[0] if isinstance(blocks_value, list) and blocks_value else blocks_value
        keys = sorted(sample.keys()) if isinstance(sample, dict) else type(sample).__name__
        logger.warning(
            "edit_blocks: uncoerced blocks shape container=%s entry_keys=%s — extend _edit_blocks normalizer",
            type(blocks_value).__name__,
            keys,
        )

    return args
