"""JSON config validation for Director write/edit tools.

This module is validation infrastructure, not a deterministic repair strategy
host. It intentionally lives outside ``deterministic_repairs`` so tool execution
does not depend on retired repair packages.
"""

from __future__ import annotations

import json
import re
from typing import Any


def validate_json_config_file(
    content: str,
    file_path: str,
    *,
    allow_repair: bool = True,
) -> dict[str, Any]:
    """Validate a JSON config file, optionally repairing common JS-literal drift."""

    if not content or not content.strip():
        return {"ok": True, "content": content, "repaired": False}

    try:
        json.loads(content)
        return {"ok": True, "content": content, "repaired": False}
    except (json.JSONDecodeError, ValueError):
        pass

    if allow_repair:
        repaired_content, was_repaired = try_repair_js_object_literal_to_json(content)
        if was_repaired:
            return {"ok": True, "content": repaired_content, "repaired": True}

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


def try_repair_js_object_literal_to_json(content: str) -> tuple[str, bool]:
    """Convert common JS-object-literal config drift into valid JSON."""

    if not content or not content.strip():
        return content, False
    stripped = content.strip()
    try:
        json.loads(stripped)
        return content, False
    except (json.JSONDecodeError, ValueError):
        pass
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return content, False

    repaired = _remove_single_line_comments(stripped)
    repaired = _remove_multi_line_comments(repaired)
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    repaired = _quote_unquoted_keys(repaired)
    repaired = _quote_unquoted_string_values_in_objects(repaired)
    repaired = _quote_unquoted_array_elements(repaired)

    try:
        parsed = json.loads(repaired)
    except (json.JSONDecodeError, ValueError):
        return content, False
    return json.dumps(parsed, indent=2, ensure_ascii=False) + "\n", True


def _remove_single_line_comments(text: str) -> str:
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
        if char == "/" and i + 1 < len(text) and text[i + 1] == "/":
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        result.append(char)
        i += 1
    return "".join(result)


def _remove_multi_line_comments(text: str) -> str:
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
        if char == "/" and i + 1 < len(text) and text[i + 1] == "/":
            previous = result[-1] if result else ""
            if previous in ("", " ", "\t", "\n", "\r", "{", "[", ","):
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
        if char.isalpha() or char == "_":
            start = i
            while i < len(text) and (text[i].isalnum() or text[i] in ("_", "-")):
                i += 1
            word = text[start:i]
            while i < len(text) and text[i] in (" ", "\t"):
                i += 1
            if i < len(text) and text[i] == ":":
                result.append(f'"{word}"')
            else:
                result.append(word)
            continue
        result.append(char)
        i += 1
    return "".join(result)


def _quote_unquoted_string_values_in_objects(text: str) -> str:
    def _replace_value(match: re.Match[str]) -> str:
        colon = match.group(1)
        value = match.group(2)
        if value in {"true", "false", "null"}:
            return f"{colon}{value}"
        try:
            float(value)
            return f"{colon}{value}"
        except ValueError:
            return f'{colon}"{value}"'

    return re.sub(r"(:\s+)([a-zA-Z_][a-zA-Z0-9_-]*)", _replace_value, text)


def _quote_unquoted_array_elements(text: str) -> str:
    result: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char != "[":
            result.append(char)
            i += 1
            continue
        result.append(char)
        i += 1
        while i < len(text) and text[i] != "]":
            if text[i] in (" ", "\t", "\n", "\r", ","):
                result.append(text[i])
                i += 1
                continue
            if text[i] in ('"', "'", "`"):
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
            if text[i].isdigit() or text[i] == "-" or text[i : i + 4] in ("true", "null") or text[i : i + 5] == "false":
                start = i
                while i < len(text) and text[i] not in (",", "]", " ", "\t", "\n", "\r"):
                    i += 1
                result.append(text[start:i])
                continue
            start = i
            while i < len(text) and text[i] not in (",", "]", " ", "\t", "\n", "\r"):
                i += 1
            word = text[start:i]
            if word:
                result.append(f'"{word}"')
        if i < len(text):
            result.append(text[i])
            i += 1
    return "".join(result)


__all__ = ["try_repair_js_object_literal_to_json", "validate_json_config_file"]
