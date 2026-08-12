# ruff: noqa: F403, F405
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from ...contracts import RepairDiagnostic, RepairOperation, RepairPlan
from ..common import *
from ..constants import *


def _typescript_camel_case_hyphenated_identifier(left: str, right: str) -> str:
    if not left or not right:
        return ""
    return f"{left}{right[0].upper()}{right[1:]}"


def _repair_typescript_hyphenated_identifiers(
    *,
    original: str,
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[str, dict[str, str], tuple[str, ...]]:
    lines = str(original or "").splitlines(keepends=True)
    replacements: dict[str, str] = {}
    diagnostic_ids: list[str] = []
    for diagnostic in diagnostics:
        if not _is_typescript_comma_expected_diagnostic(diagnostic):
            continue
        line_number = _typescript_diagnostic_line(diagnostic)
        if not line_number or line_number < 1 or line_number > len(lines):
            continue
        line = lines[line_number - 1].rstrip("\r\n")
        match = _TS_HYPHENATED_VARIABLE_DECLARATION_RE.search(line)
        if not match:
            continue
        old_name = f"{match.group('left')}-{match.group('right')}"
        new_name = _typescript_camel_case_hyphenated_identifier(match.group("left"), match.group("right"))
        if not old_name or not new_name or old_name == new_name:
            continue
        replacements[old_name] = new_name
        diagnostic_ids.append(diagnostic.diagnostic_id)

    repaired = str(original or "")
    for old_name, new_name in sorted(replacements.items(), key=lambda item: (-len(item[0]), item[0])):
        token_re = re.compile(rf"(?<![A-Za-z0-9_$]){re.escape(old_name)}(?![A-Za-z0-9_$])")
        repaired = token_re.sub(new_name, repaired)
    return repaired, replacements, tuple(diagnostic_ids)


def build_typescript_hyphenated_identifier_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a narrow TS1005 repair plan for illegal hyphenated variable identifiers."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    diagnostics_by_path: dict[str, list[RepairDiagnostic]] = {}
    for diagnostic in diagnostics:
        if not _is_typescript_comma_expected_diagnostic(diagnostic):
            continue
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if not path or path not in normalized_base_files:
            continue
        diagnostics_by_path.setdefault(path, []).append(diagnostic)

    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    for path in sorted(diagnostics_by_path):
        original = str(normalized_base_files.get(path) or "")
        repaired, replacements, diagnostic_ids = _repair_typescript_hyphenated_identifiers(
            original=original,
            diagnostics=diagnostics_by_path[path],
        )
        if repaired == original or not replacements:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={
                    "diagnostic_ids": diagnostic_ids,
                    "repair_kind": "typescript_hyphenated_identifier",
                    "replacements": dict(replacements),
                },
            )
        )
        matched_diagnostics.extend(diagnostics_by_path[path])

    if not operations:
        return None
    return RepairPlan(
        rule_id="typescript.hyphenated_identifier",
        source_tool=TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(matched_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"runtime_plan_scope": "same_file_hyphenated_variable_identifier"},
    )
