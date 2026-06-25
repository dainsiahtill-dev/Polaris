"""Canonical TypeScript syntax repair rules for Director Runtime."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text

TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL = "deterministic_typescript_return_object_semicolon_repair"

_TS_INLINE_OBJECT_MISSING_COMMA_RE = re.compile(
    r"(?P<value>\b[A-Za-z_$][A-Za-z0-9_$]*\b|\)|\]|\}|['\"][^'\"]*['\"]|-?\d+(?:\.\d+)?)"
    r"(?P<gap>[ \t]{2,})"
    r"(?P<key>[A-Za-z_$][A-Za-z0-9_$]*\s*:)"
)
_TS_OBJECT_PROPERTY_KEY_LINE_RE = re.compile(r"^\s*[A-Za-z_$][A-Za-z0-9_$]*\s*:")
_TS_RETURN_OBJECT_START_RE = re.compile(r"\breturn\s*\{\s*$")
_TS_OBJECT_LITERAL_START_RE = re.compile(r"(?:\breturn\s*|=\s*)\{\s*$")


def repair_typescript_object_literal_commas(text: str) -> str:
    """Repair narrow object-literal comma omissions reported as TS1005."""

    lines = str(text or "").splitlines(keepends=True)
    repaired: list[str] = []
    object_literal_depths: list[int] = []
    brace_depth = 0
    changed = False
    for line in lines:
        line_body = line.rstrip("\r\n")
        newline = line[len(line_body) :]
        starts_object_literal = bool(
            _TS_RETURN_OBJECT_START_RE.search(line_body) or _TS_OBJECT_LITERAL_START_RE.search(line_body)
        )
        has_inline_missing_object_comma = bool(
            "{" in line_body and ":" in line_body and _TS_INLINE_OBJECT_MISSING_COMMA_RE.search(line_body)
        )
        in_object_literal = bool(object_literal_depths) or starts_object_literal or has_inline_missing_object_comma
        if in_object_literal:
            repaired_line = _repair_missing_object_property_comma_line(line_body)
            if repaired_line != line_body:
                repaired.append(f"{repaired_line}{newline}")
                changed = True
            else:
                if _object_property_line_needs_previous_comma(line_body, repaired):
                    repaired[-1] = _append_object_property_comma(repaired[-1])
                    changed = True
                repaired.append(line)
        else:
            repaired.append(line)

        opens = line_body.count("{")
        closes = line_body.count("}")
        if starts_object_literal:
            object_literal_depths.append(brace_depth + max(opens, 1))
        brace_depth += opens - closes
        while object_literal_depths and brace_depth < object_literal_depths[-1]:
            object_literal_depths.pop()
    return "".join(repaired) if changed else str(text or "")


def build_typescript_object_literal_comma_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical repair plan for TS1005 object literal comma omissions."""

    diagnostics_by_path: dict[str, list[RepairDiagnostic]] = {}
    for diagnostic in diagnostics:
        if not _is_typescript_comma_expected_diagnostic(diagnostic):
            continue
        path = str(diagnostic.path or "").strip().replace("\\", "/")
        if not path or path not in base_files:
            continue
        diagnostics_by_path.setdefault(path, []).append(diagnostic)

    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    for path in sorted(diagnostics_by_path):
        original = str(base_files.get(path) or "")
        repaired = repair_typescript_object_literal_commas(original)
        if repaired == original:
            continue
        path_diagnostics = diagnostics_by_path[path]
        matched_diagnostics.extend(path_diagnostics)
        first_diagnostic = path_diagnostics[0]
        operations.append(
            RepairOperation(
                kind="write_file",
                path=path,
                content=repaired,
                before_hash=sha256_text(original),
                metadata={
                    "line": first_diagnostic.line,
                    "column": first_diagnostic.column,
                    "diagnostic_ids": [diagnostic.diagnostic_id for diagnostic in path_diagnostics],
                    "repair_kind": "typescript_object_literal_missing_comma",
                },
            )
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="typescript.object_literal_missing_comma",
        source_tool=TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(matched_diagnostics),
        mode=mode,
        risk_level="low",
    )


def _repair_missing_object_property_comma_line(line_body: str) -> str:
    return _TS_INLINE_OBJECT_MISSING_COMMA_RE.sub(r"\g<value>, \g<key>", line_body)


def _object_property_line_needs_previous_comma(
    line_body: str,
    repaired_lines: list[str],
) -> bool:
    if not repaired_lines or not _TS_OBJECT_PROPERTY_KEY_LINE_RE.match(line_body):
        return False
    previous = repaired_lines[-1].rstrip("\r\n")
    previous_stripped = previous.rstrip()
    if not previous_stripped or previous_stripped.endswith((",", "{", "[", "(", ":", ";")):
        return False
    return not previous_stripped.lstrip().startswith(("//", "*", "/*"))


def _append_object_property_comma(line: str) -> str:
    line_body = line.rstrip("\r\n")
    newline = line[len(line_body) :]
    return f"{line_body.rstrip()},{newline}"


def _is_typescript_comma_expected_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    if diagnostic.code.lower() != "typescript_ts1005":
        return False
    message = diagnostic.message.lower()
    raw = diagnostic.raw.lower()
    return "expected" in message and "," in (message + raw)


__all__ = [
    "TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL",
    "build_typescript_object_literal_comma_plan",
    "repair_typescript_object_literal_commas",
]
