"""Deterministic repairs for JSON config files.

Converts JS-object-literal style configs (unquoted keys, trailing commas,
unquoted string values) to standard JSON. This addresses the common pattern
where Director writes tsconfig.json/package.json with JS syntax instead of
valid JSON.
"""

from __future__ import annotations

import json
import re
from typing import Any


def try_repair_js_object_literal_to_json(content: str) -> tuple[str, bool]:
    """Attempt to convert JS-object-literal style content to valid JSON.

    Handles common patterns:
    - Unquoted keys: { target: "ES2020" } -> { "target": "ES2020" }
    - Trailing commas: { "a": 1, } -> { "a": 1 }
    - Unquoted string values: { target: ES2020 } -> { "target": "ES2020" }
    - Single-line comments: // comment -> (removed)
    - Multi-line comments: /* comment */ -> (removed)
    - Bare tokens in arrays: [src/**/*] -> ["src/**/*"]

    Returns:
        Tuple of (repaired_content, was_repaired).
        If repair fails (content is still not valid JSON), returns
        (original_content, False).
    """
    if not content or not content.strip():
        return content, False

    stripped = content.strip()

    # Quick check: if it's already valid JSON, no repair needed
    try:
        json.loads(stripped)
        return content, False
    except (json.JSONDecodeError, ValueError):
        pass

    # Check if this looks like a JSON file (starts with { or [)
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return content, False

    repaired = stripped

    # Remove single-line comments (// ...) but not inside strings
    repaired = _remove_single_line_comments(repaired)

    # Remove multi-line comments (/* ... */) but not inside strings
    repaired = _remove_multi_line_comments(repaired)

    # Remove trailing commas before } or ]
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)

    # Quote unquoted keys: word characters before a colon
    # Match: key: value or key: {
    # But don't match inside strings or already-quoted keys
    repaired = _quote_unquoted_keys(repaired)

    # Quote unquoted string values in object context
    repaired = _quote_unquoted_string_values_in_objects(repaired)

    # Quote unquoted array elements
    repaired = _quote_unquoted_array_elements(repaired)

    # Try to parse the repaired content
    try:
        parsed = json.loads(repaired)
        # Re-serialize to ensure consistent formatting
        result = json.dumps(parsed, indent=2, ensure_ascii=False) + "\n"
        return result, True
    except (json.JSONDecodeError, ValueError):
        return content, False


def _remove_single_line_comments(text: str) -> str:
    """Remove single-line comments while preserving strings."""
    result: list[str] = []
    i = 0
    in_string = False
    string_char = ""

    while i < len(text):
        char = text[i]

        if in_string:
            result.append(char)
            if char == "\\" and i + 1 < len(text):
                result.append(text[i + 1])
                i += 2
                continue
            if char == string_char:
                in_string = False
            i += 1
            continue

        if char in ('"', "'", "`"):
            in_string = True
            string_char = char
            result.append(char)
            i += 1
            continue

        # Check for // comment
        if char == "/" and i + 1 < len(text) and text[i + 1] == "/":
            # Skip until end of line
            while i < len(text) and text[i] != "\n":
                i += 1
            continue

        result.append(char)
        i += 1

    return "".join(result)


def _remove_multi_line_comments(text: str) -> str:
    """Remove multi-line comments while preserving strings and glob patterns."""
    result: list[str] = []
    i = 0
    in_string = False
    string_char = ""

    while i < len(text):
        char = text[i]

        if in_string:
            result.append(char)
            if char == "\\" and i + 1 < len(text):
                result.append(text[i + 1])
                i += 2
                continue
            if char == string_char:
                in_string = False
            i += 1
            continue

        if char in ('"', "'", "`"):
            in_string = True
            string_char = char
            result.append(char)
            i += 1
            continue

        # Check for /* comment */ but not /* (glob pattern)
        # Only treat as comment if preceded by whitespace, {, [, or start of line
        if char == "/" and i + 1 < len(text) and text[i + 1] == "*":
            # Check if this looks like a comment (preceded by whitespace or bracket)
            prev_char = result[-1] if result else ""
            if prev_char in ("", " ", "\t", "\n", "\r", "{", "[", ","):
                # This is likely a comment
                i += 2
                while i < len(text) - 1:
                    if text[i] == "*" and text[i + 1] == "/":
                        i += 2
                        break
                    i += 1
                continue

        result.append(char)
        i += 1

    return "".join(result)


def _quote_unquoted_keys(text: str) -> str:
    """Quote unquoted object keys while preserving strings."""
    # Pattern: word characters (not starting with digit) followed by optional
    # whitespace and a colon, but not already quoted
    # This is a simplified approach that works for typical config files
    result: list[str] = []
    i = 0
    in_string = False
    string_char = ""

    while i < len(text):
        char = text[i]

        if in_string:
            result.append(char)
            if char == "\\" and i + 1 < len(text):
                result.append(text[i + 1])
                i += 2
                continue
            if char == string_char:
                in_string = False
            i += 1
            continue

        if char in ('"', "'", "`"):
            in_string = True
            string_char = char
            result.append(char)
            i += 1
            continue

        # Check for unquoted key pattern: word chars followed by optional whitespace and colon
        if char.isalpha() or char == "_":
            # Collect the word
            start = i
            while i < len(text) and (text[i].isalnum() or text[i] in ("_", "-")):
                i += 1
            word = text[start:i]

            # Skip whitespace
            while i < len(text) and text[i] in (" ", "\t"):
                i += 1

            # Check if followed by colon
            if i < len(text) and text[i] == ":":
                # This is an unquoted key - quote it
                result.append(f'"{word}"')
            else:
                # Not a key, keep as-is
                result.append(word)
            continue

        result.append(char)
        i += 1

    return "".join(result)


def _quote_unquoted_string_values(text: str) -> str:
    """Quote unquoted string values while preserving already-quoted values.

    Handles patterns like: "target": ES2020 -> "target": "ES2020"
    But preserves: "target": "ES2020", "strict": true, "outDir": "dist"
    """
    result: list[str] = []
    i = 0
    in_string = False
    string_char = ""

    while i < len(text):
        char = text[i]

        if in_string:
            result.append(char)
            if char == "\\" and i + 1 < len(text):
                result.append(text[i + 1])
                i += 2
                continue
            if char == string_char:
                in_string = False
            i += 1
            continue

        if char in ('"', "'", "`"):
            in_string = True
            string_char = char
            result.append(char)
            i += 1
            continue

        # Check for colon followed by unquoted value
        if char == ":":
            result.append(char)
            i += 1

            # Skip whitespace
            while i < len(text) and text[i] in (" ", "\t"):
                result.append(text[i])
                i += 1

            # Check if value is already quoted or a special value
            if i < len(text):
                next_char = text[i]
                if next_char in ('"', "'", "`", "{", "["):
                    # Already quoted or object/array - keep as-is
                    pass
                elif text[i : i + 4] in ("true", "null"):
                    # Boolean/null - keep as-is
                    pass
                elif text[i : i + 5] == "false":
                    # Boolean - keep as-is
                    pass
                elif next_char.isdigit() or next_char == "-":
                    # Number - keep as-is
                    pass
                elif next_char.isalpha() or next_char == "_":
                    # Unquoted string value - quote it
                    start = i
                    while i < len(text) and (text[i].isalnum() or text[i] in ("_", "-")):
                        i += 1
                    word = text[start:i]
                    result.append(f'"{word}"')
                    continue
            continue

        result.append(char)
        i += 1

    return "".join(result)


def _quote_unquoted_string_values_in_objects(text: str) -> str:
    """Quote unquoted string values in object context only.

    This is a simpler version that handles the common case:
    "target": ES2020 -> "target": "ES2020"
    """

    # Use regex to find patterns like: : word (not followed by [ or {)
    # where word is not a number, boolean, or null
    def _replace_value(match: re.Match) -> str:
        colon = match.group(1)
        value = match.group(2)

        # Check if value is a special value
        if value in ("true", "false", "null"):
            return f"{colon}{value}"

        # Check if value is a number
        try:
            float(value)
            return f"{colon}{value}"
        except ValueError:
            pass

        # Quote the string value
        return f'{colon}"{value}"'

    # Match: colon + optional whitespace + word chars (not starting with digit)
    # But not followed by [ or { (which would indicate array/object)
    pattern = r"(:\s+)([a-zA-Z_][a-zA-Z0-9_-]*)"
    return re.sub(pattern, _replace_value, text)


def _quote_unquoted_array_elements(text: str) -> str:
    """Quote unquoted array elements.

    Handles: [src/**/*] -> ["src/**/*"]
    """
    # Find array contents and quote unquoted elements
    result: list[str] = []
    i = 0

    while i < len(text):
        char = text[i]

        # Check for start of array
        if char == "[":
            # Collect array contents
            result.append(char)
            i += 1

            # Process array contents
            while i < len(text) and text[i] != "]":
                # Skip whitespace
                if text[i] in (" ", "\t", "\n", "\r"):
                    result.append(text[i])
                    i += 1
                    continue

                # Skip commas (trailing commas already removed)
                if text[i] == ",":
                    result.append(text[i])
                    i += 1
                    continue

                # Check if element is already quoted
                if text[i] in ('"', "'", "`"):
                    # Copy the quoted string
                    quote_char = text[i]
                    result.append(text[i])
                    i += 1
                    while i < len(text) and text[i] != quote_char:
                        if text[i] == "\\" and i + 1 < len(text):
                            result.append(text[i])
                            result.append(text[i + 1])
                            i += 2
                            continue
                        result.append(text[i])
                        i += 1
                    if i < len(text):
                        result.append(text[i])
                        i += 1
                    continue

                # Check if element is a number or boolean
                if (
                    text[i].isdigit()
                    or text[i] == "-"
                    or text[i : i + 4] in ("true", "null")
                    or text[i : i + 5] == "false"
                ):
                    # Copy as-is
                    start = i
                    while i < len(text) and text[i] not in (",", "]", " ", "\t", "\n", "\r"):
                        i += 1
                    result.append(text[start:i])
                    continue

                # Unquoted string element - quote it
                # Collect everything until comma, bracket, or whitespace
                # Include glob characters like *, ?, {, }
                start = i
                while i < len(text) and text[i] not in (",", "]", " ", "\t", "\n", "\r"):
                    i += 1
                word = text[start:i]
                if word:
                    result.append(f'"{word}"')
                continue

            if i < len(text):
                result.append(text[i])
                i += 1
            continue

        result.append(char)
        i += 1

    return "".join(result)


def validate_json_content(content: str, file_path: str) -> dict[str, Any]:
    """Validate that content is valid JSON.

    Returns:
        dict with 'ok' key and optional 'error' and 'repaired' keys.
    """
    try:
        json.loads(content)
        return {"ok": True}
    except (json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "error": f"Invalid JSON in {file_path}: {exc}"}


def validate_json_config_file(
    content: str,
    file_path: str,
    *,
    allow_repair: bool = True,
) -> dict[str, Any]:
    """Validate a JSON config file, attempting repair if content is invalid.

    This is the main entry point for Director write_file/edit_file validation.

    Args:
        content: The file content to validate.
        file_path: The file path (for error messages).
        allow_repair: Whether to attempt JS-object-literal repair.

    Returns:
        dict with keys:
            - ok (bool): Whether content is valid JSON.
            - content (str): The validated/repaired content.
            - repaired (bool): Whether content was repaired.
            - error (str, optional): Error message if validation failed.
    """
    # Quick check for empty content
    if not content or not content.strip():
        return {"ok": True, "content": content, "repaired": False}

    # Try parsing as-is first
    try:
        json.loads(content)
        return {"ok": True, "content": content, "repaired": False}
    except (json.JSONDecodeError, ValueError):
        pass

    # Attempt repair if allowed
    if allow_repair:
        repaired_content, was_repaired = try_repair_js_object_literal_to_json(content)
        if was_repaired:
            # Verify the repaired content is valid
            try:
                json.loads(repaired_content)
                return {
                    "ok": True,
                    "content": repaired_content,
                    "repaired": True,
                }
            except (json.JSONDecodeError, ValueError):
                # Repair failed validation
                pass

    # Validation failed
    try:
        json.loads(content)
    except (json.JSONDecodeError, ValueError) as exc:
        return {
            "ok": False,
            "content": content,
            "repaired": False,
            "error": f"Invalid JSON in {file_path}: {exc}",
        }

    return {"ok": True, "content": content, "repaired": False}


def validate_entrypoint_consistency(
    content: str,
    file_path: str,
    *,
    workspace_path: str | None = None,
) -> dict[str, Any]:
    """Validate entry-point consistency for package.json and tsconfig.json.

    Checks:
    - package.json scripts reference existing local entry files
    - tsconfig.json include is not empty or bare token

    Args:
        content: The JSON content to validate.
        file_path: The file path (for determining file type).
        workspace_path: Optional workspace root path for checking file existence.

    Returns:
        dict with keys:
            - ok (bool): Whether validation passed.
            - errors (list[str]): List of validation errors.
            - warnings (list[str]): List of validation warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []

    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        # JSON syntax error will be caught by validate_json_config_file
        return {"ok": True, "errors": [], "warnings": []}

    if not isinstance(payload, dict):
        return {"ok": True, "errors": [], "warnings": []}

    normalized_path = str(file_path or "").replace("\\", "/").strip("/").lower()

    # Validate package.json scripts
    if normalized_path == "package.json" or normalized_path.endswith("/package.json"):
        errors.extend(_validate_package_scripts(payload, workspace_path))

    # Validate tsconfig.json include
    if normalized_path == "tsconfig.json" or normalized_path.endswith("/tsconfig.json"):
        errors.extend(_validate_tsconfig_include(payload))

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


def _validate_package_scripts(
    payload: dict[str, Any],
    workspace_path: str | None = None,
) -> list[str]:
    """Validate that package.json scripts reference existing local files."""
    errors: list[str] = []
    scripts = payload.get("scripts")
    if not isinstance(scripts, dict):
        return errors

    # Check main entry point
    main_entry = str(payload.get("main") or "").strip()
    if main_entry and workspace_path:
        _check_entry_exists(main_entry, "main", workspace_path, errors)

    # Check script entry points
    for script_name in ("start", "test", "build"):
        script_value = str(scripts.get(script_name) or "")
        if not script_value:
            continue

        # Extract file references from common patterns
        # Pattern: node <file> or npx <file>
        file_refs = _extract_file_references_from_script(script_value)
        for ref in file_refs:
            if workspace_path:
                _check_entry_exists(ref, f"scripts.{script_name}", workspace_path, errors)

    return errors


def _validate_tsconfig_include(payload: dict[str, Any]) -> list[str]:
    """Validate that tsconfig.json include is not empty or bare token."""
    errors: list[str] = []
    include = payload.get("include")

    if include is None:
        # include is optional, no error
        return errors

    if not isinstance(include, list):
        errors.append("tsconfig.json: 'include' must be an array")
        return errors

    if len(include) == 0:
        errors.append("tsconfig.json: 'include' must not be empty")
        return errors

    # Check for bare tokens (unquoted strings that look like identifiers)
    for item in include:
        if not isinstance(item, str):
            errors.append(f"tsconfig.json: 'include' items must be strings, got {type(item).__name__}")
            continue

        # Check for bare tokens (no quotes, no glob patterns)
        if (
            item.strip()
            and not any(c in item for c in ("*", "/", "\\", ".", "**"))
            and not item.endswith(".ts")
            and not item.endswith(".tsx")
            and not item.endswith(".js")
        ):
            # This is a warning, not an error, since it could be valid
            # but often indicates missing quotes in JS-object-literal
            pass

    return errors


def _extract_file_references_from_script(script_value: str) -> list[str]:
    """Extract file references from npm script values."""
    refs: list[str] = []
    import shlex

    try:
        parts = shlex.split(script_value)
    except ValueError:
        return refs

    # Look for patterns like: node <file>, npx <file>
    i = 0
    while i < len(parts):
        part = parts[i]
        if part in ("node", "npx") and i + 1 < len(parts):
            next_part = parts[i + 1]
            # Skip flags
            if not next_part.startswith("-"):
                refs.append(next_part)
        i += 1

    return refs


def _check_entry_exists(
    entry_path: str,
    field_name: str,
    workspace_path: str,
    errors: list[str],
) -> None:
    """Check if an entry file exists relative to workspace."""
    from pathlib import Path

    if not entry_path or not workspace_path:
        return

    # Normalize path
    normalized = entry_path.replace("\\", "/").strip()
    if not normalized:
        return

    # Skip non-local references (npm packages, URLs)
    if normalized.startswith(("http://", "https://", "@", "npm:")):
        return

    # Check if file exists
    workspace = Path(workspace_path)
    target = (workspace / normalized).resolve()

    try:
        target.relative_to(workspace)
    except ValueError:
        # Outside workspace, skip
        return

    if not target.exists():
        errors.append(f"package.json: '{field_name}' references missing local entrypoint '{entry_path}'")
