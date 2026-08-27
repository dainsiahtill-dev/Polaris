"""Shared helpers for tool argument normalizers.

This module provides utility functions used across different normalizers.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


def _clean_scalar_text(value: Any) -> str:
    """Clean markdown formatting and whitespace from scalar text."""
    token = str(value or "").strip()
    if not token:
        return ""
    token = token.replace("\r\n", "\n").replace("\r", "\n")
    token = re.sub(r"\*\*(.*?)\*\*", r"\1", token, flags=re.DOTALL)
    token = re.sub(r"`([^`]*)`", r"\1", token)
    token = re.sub(r"\s+#.*$", "", token, flags=re.MULTILINE)
    token = re.sub(r"\n+\*+\s*$", "", token).strip()
    token = re.sub(r"\s+\*+\s*$", "", token).strip()
    return token


def _coerce_int(value: Any) -> int | None:
    """Coerce a value to int if possible."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        token = _clean_scalar_text(value)
        if not token:
            return None
        try:
            return int(token)
        except (TypeError, ValueError):
            match = re.search(r"[-+]?\d+", token)
            if match is not None:
                try:
                    return int(match.group(0))
                except (TypeError, ValueError):
                    return None
            return None
    return None


def _coerce_bool(value: Any) -> bool | None:
    """Coerce a value to bool if possible."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        token = _clean_scalar_text(value).lower()
        if token in {"1", "true", "yes", "on", "y"}:
            return True
        if token in {"0", "false", "no", "off", "n"}:
            return False
        match = re.search(r"\b(true|false|yes|no|on|off|1|0)\b", token)
        if match is not None:
            return match.group(1) in {"1", "true", "yes", "on"}
    return None


def _decode_scalar_token(value: Any) -> Any:
    """Decode a scalar token, handling JSON, quotes, etc."""
    if not isinstance(value, str):
        return value

    token = _clean_scalar_text(value)
    if not token:
        return ""

    try:
        return json.loads(token)
    except json.JSONDecodeError:
        pass

    if len(token) >= 2 and ((token[0] == '"' and token[-1] == '"') or (token[0] == "'" and token[-1] == "'")):
        return token[1:-1]
    return token


def _normalize_workspace_alias_path(value: Any) -> str:
    """Normalize workspace path aliases."""

    decoded = _decode_scalar_token(value)
    token = str(decoded or "").strip()
    if not token:
        return ""

    normalized = token.replace("\\", "/")
    lowered = normalized.lower()

    # Polaris path alias mappings
    polaris_exact_aliases = {
        "/workspace": ".",
        "/workspace/": ".",
        "workspace": ".",
        "workspace/": ".",
        "/projects": ".",
        "/projects/": ".",
        "/project": ".",
        "/project/": ".",
        "/home/user/project": ".",
        "/home/user/project/": ".",
        "/home/user/repo": ".",
        "/home/user/repo/": ".",
        "/home/sandbox/project": ".",
        "/home/sandbox/project/": ".",
        "/home/sandbox/repo": ".",
        "/home/sandbox/repo/": ".",
        "/repo": ".",
        "/repo/": ".",
        "/app": ".",
        "/app/": ".",
        "/stress_reports": "./stress_reports",
        "/stress_reports/": "./stress_reports",
    }
    if lowered in polaris_exact_aliases:
        return polaris_exact_aliases[lowered]

    # Prefix mapping
    if lowered.startswith("file:///workspace/"):
        suffix = normalized[len("file:///workspace/") :].strip("/")
        return suffix or "."
    if lowered.startswith("/workspace/"):
        suffix = normalized[len("/workspace/") :].strip("/")
        return suffix or "."
    if lowered.startswith("/projects/"):
        suffix = normalized[len("/projects/") :].strip("/")
        return suffix or "."
    if lowered.startswith("file:///project/"):
        suffix = normalized[len("file:///project/") :].strip("/")
        return suffix or "."
    if lowered.startswith("/project/"):
        suffix = normalized[len("/project/") :].strip("/")
        return suffix or "."
    if lowered.startswith("file:///repo/"):
        suffix = normalized[len("file:///repo/") :].strip("/")
        return suffix or "."
    if lowered.startswith("/repo/"):
        suffix = normalized[len("/repo/") :].strip("/")
        return suffix or "."
    if lowered.startswith("file:///app/"):
        suffix = normalized[len("file:///app/") :].strip("/")
        return suffix or "."
    if lowered.startswith("/app/"):
        suffix = normalized[len("/app/") :].strip("/")
        return suffix or "."
    if lowered.startswith("file:///home/user/project/"):
        suffix = normalized[len("file:///home/user/project/") :].strip("/")
        return suffix or "."
    if lowered.startswith("/home/user/project/"):
        suffix = normalized[len("/home/user/project/") :].strip("/")
        return suffix or "."
    if lowered.startswith("file:///home/user/repo/"):
        suffix = normalized[len("file:///home/user/repo/") :].strip("/")
        return suffix or "."
    if lowered.startswith("/home/user/repo/"):
        suffix = normalized[len("/home/user/repo/") :].strip("/")
        return suffix or "."
    if lowered.startswith("file:///home/sandbox/project/"):
        suffix = normalized[len("file:///home/sandbox/project/") :].strip("/")
        return suffix or "."
    if lowered.startswith("/home/sandbox/project/"):
        suffix = normalized[len("/home/sandbox/project/") :].strip("/")
        return suffix or "."
    if lowered.startswith("file:///home/sandbox/repo/"):
        suffix = normalized[len("file:///home/sandbox/repo/") :].strip("/")
        return suffix or "."
    if lowered.startswith("/home/sandbox/repo/"):
        suffix = normalized[len("/home/sandbox/repo/") :].strip("/")
        return suffix or "."
    if lowered.startswith("/stress_reports/"):
        suffix = normalized[len("/stress_reports/") :].strip("/")
        return f"./stress_reports/{suffix}" if suffix else "./stress_reports"

    # R158: collapse leading ``./`` so DEO path-scope evidence compares equal to
    # resolved workspace-relative paths (``./package.json`` → ``package.json``).
    # Keep non-leading ``./`` segments and do not invent absolute roots.
    while token.startswith("./"):
        token = token[2:]
    return token


# ============================================================================
# Common Normalization Helpers (for reducing per-tool normalizer complexity)
# ============================================================================


def _extract_first_from_aliases(
    normalized: dict[str, Any],
    target_key: str,
    aliases: tuple[str, ...],
    transform: Any = None,
) -> None:
    """Extract first non-empty value from aliases into target key.

    Args:
        normalized: Dictionary to modify in-place
        target_key: Key to set in normalized dict
        aliases: Tuple of alias keys to check
        transform: Optional transform function to apply to value
    """
    if normalized.get(target_key):
        return  # Already set
    for alias in aliases:
        candidate = normalized.get(alias)
        if isinstance(candidate, str) and candidate.strip():
            # Do NOT strip whitespace here - trailing/leading spaces in patterns are
            # semantically significant (e.g. "^def " matches "def " not "def").
            # Only apply transform if provided.
            value = candidate
            normalized[target_key] = transform(value) if transform else value
            break


def _normalize_bool_option(
    normalized: dict[str, Any],
    option_key: str,
    aliases: tuple[str, ...],
) -> None:
    """Extract boolean option from common aliases.

    Args:
        normalized: Dictionary to modify in-place
        option_key: Key to set in normalized dict
        aliases: Tuple of alias keys to check
    """
    if option_key in normalized:
        return  # Already set
    for alias in aliases:
        if alias in normalized:
            bool_value = _coerce_bool(normalized.get(alias))
            if bool_value is not None:
                normalized[option_key] = bool_value
                break


def _normalize_int_option(
    normalized: dict[str, Any],
    option_key: str,
    aliases: tuple[str, ...],
) -> None:
    """Extract integer option from common aliases.

    Args:
        normalized: Dictionary to modify in-place
        option_key: Key to set in normalized dict
        aliases: Tuple of alias keys to check
    """
    if option_key in normalized:
        return  # Already set
    for alias in aliases:
        if alias in normalized:
            int_value = _coerce_int(normalized.get(alias))
            if int_value is not None:
                normalized[option_key] = int_value
                break


def _extract_list_candidate_to_value(
    normalized: dict[str, Any],
    target_key: str,
    source_keys: tuple[str, ...],
) -> None:
    """Extract single value from list candidates in multiple source keys.

    Args:
        normalized: Dictionary to modify in-place
        target_key: Key to set in normalized dict
        source_keys: Keys that might contain list candidates
    """
    if normalized.get(target_key):
        return  # Already set
    for source_key in source_keys:
        candidates = normalized.get(source_key)
        if isinstance(candidates, list):
            for item in candidates:
                if isinstance(item, str) and item.strip():
                    normalized[target_key] = item.strip()
                    return
        elif isinstance(candidates, str) and candidates.strip():
            normalized[target_key] = candidates.strip()
            return


def _normalize_path_aliases(normalized: dict[str, Any], path_key: str = "path") -> None:
    """Extract and normalize path from common aliases.

    Args:
        normalized: Dictionary to modify in-place
        path_key: Key name for the path (default: "path")
    """
    if not normalized.get(path_key):
        # Try to find path in common aliases
        path_aliases = ("directory", "dir", "root", "cwd", "base_path", "base", "scope")
        for alias in path_aliases:
            candidate = normalized.get(alias)
            if isinstance(candidate, str) and candidate.strip():
                normalized[path_key] = _normalize_workspace_alias_path(candidate.strip())
                break
    else:
        # Normalize existing path
        candidate_path = normalized.get(path_key)
        if isinstance(candidate_path, str) and candidate_path.strip():
            normalized[path_key] = _normalize_workspace_alias_path(candidate_path.strip())


def _remove_aliases(normalized: dict[str, Any], aliases: tuple[str, ...]) -> None:
    """Remove alias keys from normalized dict.

    Args:
        normalized: Dictionary to modify in-place
        aliases: Tuple of alias keys to remove
    """
    for alias in aliases:
        normalized.pop(alias, None)


# ============================================================================
# Structured write-body recovery (R138)
# ============================================================================


_HTML_TAG_NAME = re.compile(r"^[A-Za-z][\w:-]*$")
_NESTED_MARKUP_WRITE_SUFFIXES = frozenset({".htm", ".html", ".svg", ".xhtml", ".xml"})


def _has_nested_write_body_mapping(value: Mapping[str, Any]) -> bool:
    """Return whether a structured body contains a nested mapping node."""

    for child in value.values():
        if isinstance(child, Mapping):
            return True
        if isinstance(child, (list, tuple)) and any(isinstance(item, Mapping) for item in child):
            return True
    return False


def _allows_nested_markup_recovery(file_path: str) -> bool:
    normalized = str(file_path or "").replace("\\", "/").lower()
    return any(normalized.endswith(suffix) for suffix in _NESTED_MARKUP_WRITE_SUFFIXES)


def recover_write_body_string(value: Any, *, file_path: str = "") -> str | None:
    """Recover a plain string body from structured write_file content artifacts.

    R138: some providers/models emit ``content`` as a ``$text`` map with sibling
    keys that are either mid-token continuations (e.g. ``$text`` ends with
    ``\"is not a \"`` and sibling ``canvas`` continues ``\" element; ...\"``) or
    nested HTML-ish fragments. DEO freezes non-string bodies fail-closed as
    ``deo_tool_normalization_failed`` and drops the whole write batch.

    Returns:
        A recovered non-empty string body, or ``None`` when recovery is unsafe.
    """

    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        if value and all(isinstance(item, str) for item in value):
            return "\n".join(value)
        return None
    if not isinstance(value, Mapping):
        return None
    # L3-24 r82: MiniMax emitted C++ write bodies as nested JSON mappings whose
    # keys were angle-bracket tokens (``vector``, ``string_view``, ...).  The
    # HTML recovery path serialized those nodes as XML and physically landed
    # trailing ``</string_view></string>`` in C++ source.  Nested mappings are
    # losslessly interpretable only for declared markup targets.  Other source
    # files must remain fail-closed so Director can re-emit a canonical string.
    if _has_nested_write_body_mapping(value) and not _allows_nested_markup_recovery(file_path):
        return None
    return _flatten_structured_write_body(value)


def _flatten_structured_write_body(value: Mapping[str, Any]) -> str | None:
    parts: list[str] = []
    text = value.get("$text")
    if text is not None and not isinstance(text, str):
        return None
    if isinstance(text, str):
        parts.append(text)

    children = [(str(key), child) for key, child in value.items() if str(key) != "$text"]
    htmlish_context = any(isinstance(child, Mapping) for _, child in children) or (
        isinstance(text, str) and ("<!doctype" in text.lower() or "<html" in text.lower())
    )

    for key, child in children:
        if isinstance(child, str):
            if htmlish_context and _HTML_TAG_NAME.fullmatch(key):
                parts.append(f"<{key}>{child}</{key}>")
            else:
                # Word/token continuation: the key was split out of the source stream.
                parts.append(key)
                parts.append(child)
            continue
        if isinstance(child, Mapping):
            inner = _flatten_structured_write_body(child)
            if inner is None:
                return None
            if _HTML_TAG_NAME.fullmatch(key):
                parts.append(f"<{key}>{inner}</{key}>")
            else:
                parts.append(inner)
            continue
        if isinstance(child, (list, tuple)):
            for item in child:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, Mapping):
                    inner = _flatten_structured_write_body(item)
                    if inner is None:
                        return None
                    if _HTML_TAG_NAME.fullmatch(key):
                        parts.append(f"<{key}>{inner}</{key}>")
                    else:
                        parts.append(inner)
                else:
                    return None
            continue
        return None

    joined = "".join(parts)
    return joined if joined else None


# ============================================================================
# Patch-like content helpers
# ============================================================================


@dataclass(frozen=True)
class WriteContentNormalization:
    """Normalized write_file payload content."""

    content: str | None
    error: str | None = None
    normalized_patch_like: bool = False


def looks_like_patch_like_write_content(value: Any) -> bool:
    """Check if content looks like patch format."""
    import re

    body = str(value or "").strip()
    if not body:
        return False
    lowered = body.lower()
    if "<<<<<<< search" in lowered and "=======" in body:
        return True
    if "patch_file" in lowered or "end patch_file" in lowered:
        return True
    if ">>>>>>> replace" in lowered:
        return True
    return bool(re.match(r"^\s*(?:search:?\s*\n|patch_file\b|file:|create:)\s*", body, flags=re.IGNORECASE))


def _strip_json_markdown_fence(body: str) -> tuple[str, bool]:
    stripped = body.strip()
    fence_match = re.match(r"^```(?:json|jsonc|javascript|js)?\s*\n(?P<body>.*?)(?:\n)?```\s*$", stripped, re.DOTALL)
    if fence_match is not None:
        return fence_match.group("body").strip(), True
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json|jsonc|javascript|js)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```\s*$", "", stripped)
        return stripped.strip(), True
    if stripped.endswith("```"):
        stripped_without_tail = re.sub(r"\s*```\s*$", "", stripped)
        if stripped_without_tail != stripped:
            return stripped_without_tail.strip(), True
    return body, False


def _json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _json_object_text(value: str) -> str | None:
    parsed = _json_object(value)
    if parsed is None:
        return None
    return json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"


def _normalize_json_write_content(file_path: str, body: str) -> tuple[str, bool] | None:
    if not str(file_path or "").lower().endswith(".json"):
        return None

    stripped, changed = _strip_json_markdown_fence(body)
    candidates: list[tuple[str, bool]] = [(stripped.strip(), False)]
    lead = candidates[0][0].lstrip()
    if lead and not lead.startswith("{"):
        if lead.startswith('"'):
            candidates.append(("{" + lead, False))
        elif re.match(r'^[A-Za-z_][A-Za-z0-9_-]*"\s*:', lead):
            candidates.append(('{"' + lead, False))
    candidates.extend(
        (candidate.rstrip() + "}", True) for candidate, _ in list(candidates) if not candidate.rstrip().endswith("}")
    )

    seen: set[str] = set()
    for candidate, appended_closer in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        parsed = _json_object(candidate)
        if parsed is None:
            continue
        if appended_closer and not parsed:
            continue
        normalized = json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"
        return normalized, changed or normalized != body
    return None


def normalize_patch_like_write_content(
    file_path: str,
    content: Any,
    *,
    existing_content: str | None = None,
) -> WriteContentNormalization:
    """Normalize common LLM patch payloads that were incorrectly sent to write_file."""
    import re

    body = str(content or "")
    cleaned_body, stripped_trailing_residue = _strip_trailing_patch_residue(body)
    if stripped_trailing_residue and not looks_like_patch_like_write_content(cleaned_body):
        return WriteContentNormalization(
            content=cleaned_body,
            normalized_patch_like=True,
        )
    body = cleaned_body
    if not looks_like_patch_like_write_content(body):
        json_normalized = _normalize_json_write_content(file_path, body)
        if json_normalized is not None:
            normalized_body, changed = json_normalized
            return WriteContentNormalization(
                content=normalized_body,
                normalized_patch_like=changed,
            )
        return WriteContentNormalization(content=body)

    compare_path = _normalize_compare_path(str(file_path or ""))
    candidate = body
    if not re.match(r"^\s*(?:patch_file|file|create|delete(?:_file)?)\b", body, flags=re.IGNORECASE):
        candidate = f"{compare_path or file_path}\n{body}"

    try:
        from polaris.kernelone.llm.toolkit.protocol import EditType, parse_protocol_output

        edit_type_enum = EditType
    except ImportError:
        edit_type_enum = None  # type: ignore[misc,assignment]
        parse_protocol_output = None  # type: ignore[misc,assignment]

    if parse_protocol_output is not None and edit_type_enum is not None:
        operations = parse_protocol_output(candidate)
        if len(operations) == 1:
            operation = operations[0]
            operation_path = _normalize_compare_path(str(getattr(operation, "path", "") or ""))
            if operation_path and compare_path and operation_path != compare_path:
                return WriteContentNormalization(
                    content=None,
                    error=(
                        "write_file content contains patch protocol for a different path: "
                        f"{operation_path} != {compare_path}"
                    ),
                    normalized_patch_like=True,
                )

            edit_type = getattr(operation, "edit_type", None)
            if edit_type in {edit_type_enum.FULL_FILE, edit_type_enum.CREATE}:
                return WriteContentNormalization(
                    content=str(getattr(operation, "replace", "") or ""),
                    normalized_patch_like=True,
                )

            if edit_type == edit_type_enum.SEARCH_REPLACE:
                search_text = str(getattr(operation, "search", "") or "")
                replace_text = str(getattr(operation, "replace", "") or "")
                if search_text.strip().lower() in _PATCH_LIKE_EMPTY_SEARCH_MARKERS:
                    return WriteContentNormalization(
                        content=replace_text,
                        normalized_patch_like=True,
                    )
                if existing_content is None:
                    return WriteContentNormalization(
                        content=None,
                        error="write_file content contains SEARCH/REPLACE patch but target file does not exist yet",
                        normalized_patch_like=True,
                    )
                if search_text not in existing_content:
                    return WriteContentNormalization(
                        content=None,
                        error="write_file content contains SEARCH/REPLACE patch but SEARCH block was not found in target file",
                        normalized_patch_like=True,
                    )
                return WriteContentNormalization(
                    content=existing_content.replace(search_text, replace_text, 1),
                    normalized_patch_like=True,
                )

    relaxed_sections = _extract_relaxed_search_replace_sections(body)
    if relaxed_sections is None:
        return WriteContentNormalization(
            content=None,
            error="write_file content appears to be patch text but could not be safely normalized",
            normalized_patch_like=True,
        )

    search_text, replace_text = relaxed_sections
    if search_text.strip().lower() in _PATCH_LIKE_EMPTY_SEARCH_MARKERS:
        return WriteContentNormalization(
            content=replace_text,
            normalized_patch_like=True,
        )

    if existing_content is None:
        return WriteContentNormalization(
            content=None,
            error="write_file content contains SEARCH/REPLACE patch but target file does not exist yet",
            normalized_patch_like=True,
        )
    if search_text not in existing_content:
        return WriteContentNormalization(
            content=None,
            error="write_file content contains SEARCH/REPLACE patch but SEARCH block was not found in target file",
            normalized_patch_like=True,
        )
    return WriteContentNormalization(
        content=existing_content.replace(search_text, replace_text, 1),
        normalized_patch_like=True,
    )


_PATCH_LIKE_EMPTY_SEARCH_MARKERS = frozenset(
    {
        "",
        "<empty>",
        "<empty or missing>",
        "empty",
        "empty or missing",
    }
)
_TRAILING_PATCH_RESIDUE_LINES = frozenset(
    {
        "=======",
        ">>>>>>> REPLACE",
        "END PATCH_FILE",
        "END FILE",
        "END CREATE",
    }
)


def _normalize_compare_path(path: str) -> str:
    token = _normalize_workspace_alias_path(path).replace("\\", "/").strip()
    while token.startswith("./"):
        token = token[2:]
    return token


def _extract_relaxed_search_replace_sections(value: str) -> tuple[str, str] | None:
    """Extract search/replace sections from relaxed format."""
    body = str(value or "")
    if not body.strip():
        return None

    git_style_match = re.search(
        r"<<<<<<<\s*SEARCH\s*\n(?P<search>.*?)\n=======\s*\n(?P<replace>.*?)(?:\n>>>>>>\s*REPLACE\s*|\Z)",
        body,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if git_style_match is not None:
        return (
            str(git_style_match.group("search") or "").rstrip("\n"),
            str(git_style_match.group("replace") or "").rstrip("\n"),
        )

    simple_match = re.search(
        r"(?:^|\n)SEARCH:?\s*\n(?P<search>.*?)\nREPLACE:?\s*\n(?P<replace>.*?)(?:\nEND\s+PATCH_FILE\s*|\Z)",
        body,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if simple_match is not None:
        return (
            str(simple_match.group("search") or "").rstrip("\n"),
            str(simple_match.group("replace") or "").rstrip("\n"),
        )
    return None


def _strip_trailing_patch_residue(value: str) -> tuple[str, bool]:
    """Strip trailing patch residue markers."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return text, False

    lines = text.split("\n")
    last_content_index = len(lines) - 1
    while last_content_index >= 0 and not lines[last_content_index].strip():
        last_content_index -= 1

    stripped_any = False
    while last_content_index >= 0:
        marker = lines[last_content_index].strip().upper()
        if marker not in _TRAILING_PATCH_RESIDUE_LINES:
            break
        stripped_any = True
        last_content_index -= 1
        while last_content_index >= 0 and not lines[last_content_index].strip():
            last_content_index -= 1

    if not stripped_any:
        return text, False
    cleaned = "\n".join(lines[: last_content_index + 1]).rstrip("\n")
    return cleaned, True
