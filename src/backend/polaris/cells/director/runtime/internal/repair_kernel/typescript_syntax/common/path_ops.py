from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from typing import Any

from ...contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ...javascript_syntax import repair_javascript_export_contract_placeholders
from ...path_files import normalize_base_files_strict, normalize_repair_path_strict
from ..constants import *  # noqa: F403

"""Shared TypeScript repair helpers: path_ops."""

def _typescript_diagnostic_line(diagnostic: RepairDiagnostic) -> int | None:
    if diagnostic.line:
        return diagnostic.line
    raw = str(diagnostic.raw or diagnostic.message or "")
    path = _normalize_repair_path(str(diagnostic.path or ""))
    if not raw or not path:
        return None
    match = re.search(rf"{re.escape(path)}\((?P<line>\d+),(?P<col>\d+)\)", raw)
    if not match:
        return None
    try:
        return int(match.group("line"))
    except (TypeError, ValueError):
        return None

def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return "\n"

def _normalized_base_files(base_files: Mapping[str, str]) -> dict[str, str]:
    return normalize_base_files_strict(base_files)

def _common_prefix_len(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index

def _diagnostic_targets_path(diagnostic: RepairDiagnostic, path: str) -> bool:
    normalized_path = _normalize_repair_path(str(diagnostic.path or ""))
    if normalized_path == path:
        return True
    return path in {
        _normalize_repair_path(str(match.group("file") or ""))
        for pattern in (
            _TS_MISSING_CLOSING_BRACE_RAW_RE,
            _TS_NUMBER_PROPERTY_CALL_RAW_RE,
            _TS_NUMBER_TO_STRING_ARGUMENT_RAW_RE,
            _TS_NUMBER_TO_FUNCTION_ARGUMENT_RAW_RE,
            _TS_READONLY_ASSIGNMENT_RAW_RE,
            _TS_SHORTHAND_PROPERTY_SCOPE_RAW_RE,
            _TS_STRING_LITERAL_SUGGESTION_RAW_RE,
            _TS_UNKNOWN_MEMBER_ACCESS_RAW_RE,
        )
        for match in pattern.finditer(str(diagnostic.raw or diagnostic.message or ""))
    }


__all__ = (
    "_typescript_diagnostic_line",
    "_line_ending",
    "_normalized_base_files",
    "_common_prefix_len",
    "_diagnostic_targets_path",
)
