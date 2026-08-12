"""TypeScript repair helpers: parsers.

Lossless extract from the former ``typescript_repairs`` module.
"""

from __future__ import annotations

import re
from typing import Any

from ...task_scope_paths import (
    _dedupe_preserve_order,
    _normalize_declared_task_path,
)
from .._common import _TS_MISSING_CLOSING_BRACE_ERROR_RE
from ._constants import (
    _TS_COMMA_EXPECTED_ERROR_RE,
    _TS_DUPLICATE_OBJECT_PROPERTY_ERROR_RE,
    _TS_ENUM_MEMBER_SEPARATOR_ERROR_RE,
    _TS_IDENTIFIER_RE,
    _TS_MISSING_PROPERTY_ERROR_RE,
    _TS_NULLABLE_ARGUMENT_ERROR_RE,
    _TS_NUMBER_TO_FUNCTION_ARGUMENT_ERROR_RE,
    _TS_NUMBER_TO_STRING_ARGUMENT_ERROR_RE,
    _TS_POSSIBLY_NULL_ERROR_RE,
    _TS_REQUIRED_PROPERTIES_MISSING_ERROR_RE,
    _TS_REQUIRED_PROPERTY_MISSING_ERROR_RE,
    _TS_TOO_FEW_ARGUMENTS_ERROR_RE,
    _TS_UNINITIALIZED_PROPERTY_ERROR_RE,
    _TS_UNKNOWN_VALUE_ERROR_RE,
)


def _parse_typescript_missing_member_errors(errors: list[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for error in errors:
        for match in _TS_MISSING_PROPERTY_ERROR_RE.finditer(str(error or "")):
            item = {
                "file": str(match.group("file") or "").strip(),
                "line": str(match.group("line") or "").strip(),
                "member": str(match.group("member") or "").strip(),
                "type": str(match.group("type") or "").strip(),
            }
            key = (item["file"], item["line"], item["type"], item["member"])
            if not item["file"] or not item["member"] or not item["type"] or key in seen:
                continue
            seen.add(key)
            parsed.append(item)
    return parsed


def _parse_typescript_uninitialized_property_errors(errors: list[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for error in errors:
        for match in _TS_UNINITIALIZED_PROPERTY_ERROR_RE.finditer(str(error or "")):
            item = {
                "file": str(match.group("file") or "").strip(),
                "line": str(match.group("line") or "").strip(),
                "member": str(match.group("member") or "").strip(),
            }
            key = (item["file"], item["line"], item["member"])
            if not item["file"] or not item["line"] or not item["member"] or key in seen:
                continue
            seen.add(key)
            parsed.append(item)
    return parsed


def _parse_typescript_unknown_value_errors(errors: list[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for error in errors:
        for match in _TS_UNKNOWN_VALUE_ERROR_RE.finditer(str(error or "")):
            item = {
                "file": _normalize_declared_task_path(match.group("file")),
                "line": str(match.group("line") or "").strip(),
                "symbol": str(match.group("symbol") or "").strip(),
            }
            key = (item["file"], item["line"], item["symbol"])
            if not item["file"] or not item["line"] or not _TS_IDENTIFIER_RE.fullmatch(item["symbol"]) or key in seen:
                continue
            seen.add(key)
            parsed.append(item)
    return parsed


def _parse_typescript_missing_required_property_errors(errors: list[str]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for error in errors:
        text = str(error or "")
        for match in _TS_REQUIRED_PROPERTY_MISSING_ERROR_RE.finditer(text):
            member = str(match.group("member") or "").strip()
            file_name = _normalize_declared_task_path(match.group("file"))
            line = str(match.group("line") or "").strip()
            type_name = str(match.group("type") or "").strip()
            members = [member] if _TS_IDENTIFIER_RE.fullmatch(member) else []
            key = (file_name, line, type_name, ",".join(members))
            if not file_name or not line or not type_name or not members or key in seen:
                continue
            item = {
                "file": file_name,
                "line": line,
                "type": type_name,
                "members": members,
            }
            seen.add(key)
            parsed.append(item)
        for match in _TS_REQUIRED_PROPERTIES_MISSING_ERROR_RE.finditer(text):
            members = [
                token.strip()
                for token in re.split(r",|\band\b", str(match.group("members") or ""))
                if _TS_IDENTIFIER_RE.fullmatch(token.strip())
            ]
            members = _dedupe_preserve_order(members)
            file_name = _normalize_declared_task_path(match.group("file"))
            line = str(match.group("line") or "").strip()
            type_name = str(match.group("type") or "").strip()
            key = (file_name, line, type_name, ",".join(members))
            if not file_name or not line or not type_name or not members or key in seen:
                continue
            item = {
                "file": file_name,
                "line": line,
                "type": type_name,
                "members": members,
            }
            seen.add(key)
            parsed.append(item)
    return parsed


def _strip_typescript_error_module_ref(raw_module: str) -> str:
    token = str(raw_module or "").strip().rstrip(".")
    while len(token) >= 2 and token[0] in {"'", '"', "`"} and token[-1] == token[0]:
        token = token[1:-1].strip()
    return token


def _parse_typescript_comma_expected_errors(errors: list[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for error in errors:
        for match in _TS_COMMA_EXPECTED_ERROR_RE.finditer(str(error or "")):
            item = {
                "file": str(match.group("file") or "").strip(),
                "line": str(match.group("line") or "").strip(),
                "col": str(match.group("col") or "").strip(),
            }
            key = (item["file"], item["line"], item["col"])
            if not item["file"] or not item["line"] or not item["col"] or key in seen:
                continue
            seen.add(key)
            parsed.append(item)
    return parsed


def _parse_typescript_number_to_string_argument_errors(errors: list[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for error in errors:
        for match in _TS_NUMBER_TO_STRING_ARGUMENT_ERROR_RE.finditer(str(error or "")):
            item = {
                "file": str(match.group("file") or "").strip(),
                "line": str(match.group("line") or "").strip(),
                "col": str(match.group("col") or "").strip(),
            }
            key = (item["file"], item["line"], item["col"])
            if not item["file"] or not item["line"] or not item["col"] or key in seen:
                continue
            seen.add(key)
            parsed.append(item)
    return parsed


def _parse_typescript_number_to_function_argument_errors(errors: list[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for error in errors:
        for match in _TS_NUMBER_TO_FUNCTION_ARGUMENT_ERROR_RE.finditer(str(error or "")):
            item = {
                "file": str(match.group("file") or "").strip(),
                "line": str(match.group("line") or "").strip(),
                "col": str(match.group("col") or "").strip(),
            }
            key = (item["file"], item["line"], item["col"])
            if not item["file"] or not item["line"] or not item["col"] or key in seen:
                continue
            seen.add(key)
            parsed.append(item)
    return parsed


def _parse_typescript_too_few_arguments_errors(errors: list[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for error in errors:
        for match in _TS_TOO_FEW_ARGUMENTS_ERROR_RE.finditer(str(error or "")):
            item = {
                "file": str(match.group("file") or "").strip(),
                "line": str(match.group("line") or "").strip(),
                "col": str(match.group("col") or "").strip(),
                "expected": str(match.group("expected") or "").strip(),
                "got": str(match.group("got") or "").strip(),
            }
            key = (item["file"], item["line"], item["col"], item["expected"], item["got"])
            if (
                not item["file"]
                or not item["line"]
                or not item["col"]
                or not item["expected"]
                or not item["got"]
                or key in seen
            ):
                continue
            seen.add(key)
            parsed.append(item)
    return parsed


def _parse_typescript_nullable_canvas_context_errors(errors: list[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for error in errors:
        text = str(error or "")
        for match in _TS_POSSIBLY_NULL_ERROR_RE.finditer(text):
            item = {
                "file": _normalize_declared_task_path(match.group("file")),
                "symbol": str(match.group("symbol") or "").strip(),
            }
            key = (item["file"], item["symbol"])
            if not item["file"] or not _TS_IDENTIFIER_RE.fullmatch(item["symbol"]) or key in seen:
                continue
            seen.add(key)
            parsed.append(item)
        for match in _TS_NULLABLE_ARGUMENT_ERROR_RE.finditer(text):
            item = {"file": _normalize_declared_task_path(match.group("file")), "symbol": ""}
            key = (item["file"], item["symbol"])
            if not item["file"] or key in seen:
                continue
            seen.add(key)
            parsed.append(item)
    return parsed


def _parse_typescript_duplicate_object_property_errors(errors: list[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for error in errors:
        for match in _TS_DUPLICATE_OBJECT_PROPERTY_ERROR_RE.finditer(str(error or "")):
            item = {
                "file": _normalize_declared_task_path(match.group("file")),
                "line": str(match.group("line") or "").strip(),
            }
            key = (item["file"], item["line"])
            if not item["file"] or not item["line"] or key in seen:
                continue
            seen.add(key)
            parsed.append(item)
    return parsed


def _parse_typescript_enum_member_separator_errors(errors: list[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for error in errors:
        for match in _TS_ENUM_MEMBER_SEPARATOR_ERROR_RE.finditer(str(error or "")):
            item = {
                "file": _normalize_declared_task_path(match.group("file")),
                "line": str(match.group("line") or "").strip(),
                "col": str(match.group("col") or "").strip(),
            }
            key = (item["file"], item["line"], item["col"])
            if not item["file"] or not item["line"] or not item["col"] or key in seen:
                continue
            seen.add(key)
            parsed.append(item)
    return parsed


def _parse_typescript_missing_closing_brace_errors(errors: list[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for error in errors:
        for match in _TS_MISSING_CLOSING_BRACE_ERROR_RE.finditer(str(error or "")):
            item = {
                "file": _normalize_declared_task_path(match.group("path")),
                "line": str(match.group("line") or "").strip(),
                "col": str(match.group("col") or "").strip(),
            }
            key = (item["file"], item["line"], item["col"])
            if not item["file"] or not item["line"] or not item["col"] or key in seen:
                continue
            seen.add(key)
            parsed.append(item)
    return parsed
