# ruff: noqa: F403, F405, SIM102
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from ...contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ..common import *
from ..constants import *


def build_typescript_canvas_scale_return_type_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical repair plan for scaleToCanvas return type drift."""

    if not _has_number_to_function_argument_diagnostic(diagnostics):
        return None
    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    operations: list[RepairOperation] = []
    repaired_items: list[dict[str, str]] = []
    for path in sorted(normalized_base_files):
        if path.endswith(".d.ts") or not path.endswith((".ts", ".tsx")):
            continue
        original = str(normalized_base_files.get(path) or "")
        operation = _canvas_scale_return_type_operation(path=path, content=original)
        if operation is None:
            continue
        operations.append(operation)
        repaired_items.append({"file": path, "kind": "scaleToCanvas"})
    if not operations:
        return None
    matched_diagnostics = tuple(diagnostic for diagnostic in diagnostics if _is_number_to_function_argument(diagnostic))
    return RepairPlan(
        rule_id="typescript.canvas_scale_return_type",
        source_tool=TYPESCRIPT_CANVAS_SCALE_RETURN_TYPE_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=matched_diagnostics,
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"return_types": repaired_items},
    )


def _canvas_scale_return_type_operation(*, path: str, content: str) -> RepairOperation | None:
    if "scaleToCanvas" not in content or "sx:" not in content or "sy:" not in content:
        return None
    if not re.search(r"sx\s*:\s*\([^)]*number[^)]*\)\s*=>", content):
        return None
    if not re.search(r"sy\s*:\s*\([^)]*number[^)]*\)\s*=>", content):
        return None
    match = _TS_CANVAS_SCALE_RETURN_TYPE_RE.search(content)
    if not match:
        return None
    replacement = "{ sx: (n: number) => number; sy: (n: number) => number; scale: number }"
    expected = str(match.group("return_type") or "")
    if expected == replacement:
        return None
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=match.start("return_type"),
        span_end=match.end("return_type"),
        expected=expected,
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_canvas_scale_return_type",
            "symbol": "scaleToCanvas",
        },
    )


def build_typescript_implicit_return_type_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Add ``: void`` to interface method signatures reported by TS7010 (R167)."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    for diagnostic in diagnostics:
        raw = str(diagnostic.raw or diagnostic.message or "")
        match = _TS7010_IMPLICIT_RETURN_RE.search(raw)
        code = str(diagnostic.code or "").lower()
        name = ""
        path = _normalize_repair_path(str(diagnostic.path or ""))
        line = int(diagnostic.line or 0)
        if match:
            path = path or _normalize_repair_path(str(match.group("file") or ""))
            line = line or int(match.group("line") or 0)
            name = str(match.group("name") or "")
        elif code == "typescript_ts7010":
            name_match = re.search(r"['\"]([A-Za-z_$][\w$]*)['\"],\s*which lacks return-type", raw, re.I)
            name = str(name_match.group(1) if name_match else "")
        else:
            continue
        content = str(normalized_base.get(path) or "")
        if not path or not content or line <= 0 or not name:
            continue
        op = _typescript_implicit_return_void_operation(path=path, content=content, line=line, name=name)
        if op is None:
            continue
        operations.append(op)
        matched.append(diagnostic)
    return _repair_plan_or_none(
        rule_id="typescript.implicit_return_type",
        source_tool=TYPESCRIPT_IMPLICIT_RETURN_TYPE_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched,
        mode=mode,
        metadata={"implicit_return_type_count": len(operations)},
    )


def _typescript_implicit_return_void_operation(
    *,
    path: str,
    content: str,
    line: int,
    name: str,
) -> RepairOperation | None:
    lines = content.splitlines(keepends=True)
    if line < 1 or line > len(lines):
        return None
    # Prefer interface method declarations (end with ");").
    in_interface = False
    depth = 0
    for idx, text in enumerate(lines, start=1):
        stripped = text.strip()
        if re.match(r"(?:export\s+)?interface\s+\w+", stripped):
            in_interface = True
            depth = 0
        if in_interface:
            depth += text.count("{") - text.count("}")
            if depth <= 0 and "}" in stripped and idx > 1:
                in_interface = False
        if idx != line:
            continue
        pattern = re.compile(rf"^(?P<indent>\s*){re.escape(name)}\s*\((?P<params>[^;]*)\)\s*;\s*$")
        match = pattern.match(text.rstrip("\n") + ("\n" if text.endswith("\n") else ""))
        # allow trailing newline variations
        match = pattern.match(text.rstrip("\r\n"))
        if match is None or re.search(r"\)\s*:", text):
            return None
        if not in_interface and "interface" not in "".join(lines[max(0, idx - 30) : idx]).lower():
            # Only auto-fix pure declaration form ending with semicolon (interface-like).
            if not text.rstrip().endswith(";"):
                return None
        replacement = f"{match.group('indent')}{name}({match.group('params')}): void;"
        if text.endswith("\n"):
            replacement += "\n"
        span_start = sum(len(item) for item in lines[: idx - 1])
        span_end = span_start + len(text)
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=span_start,
            span_end=span_end,
            expected=text,
            replacement=replacement,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_implicit_return_type",
                "method": name,
                "diagnostic_line": line,
            },
        )
    return None


def build_typescript_object_assign_assertion_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Assert ``Object.freeze({...}) as Type`` for TS2322 object→named-type assigns (R167)."""

    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    for diagnostic in diagnostics:
        raw = str(diagnostic.raw or diagnostic.message or "")
        code = str(diagnostic.code or "").lower()
        if code not in {"", "typescript_ts2322"} and "ts2322" not in code:
            if "is not assignable to type" not in raw.lower():
                continue
        match = _TS2322_ASSIGN_TO_NAMED_TYPE_RE.search(raw)
        path = _normalize_repair_path(str(diagnostic.path or ""))
        line = int(diagnostic.line or 0)
        type_name = ""
        if match:
            path = path or _normalize_repair_path(str(match.group("file") or ""))
            line = line or int(match.group("line") or 0)
            type_name = str(match.group("type") or "")
        else:
            type_match = re.search(r"not assignable to type ['\"]([A-Za-z_$][\w$]*)['\"]", raw, re.I)
            type_name = str(type_match.group(1) if type_match else "")
        content = str(normalized_base.get(path) or "")
        if not path or not content or line <= 0 or not type_name:
            continue
        op = _typescript_object_freeze_assert_operation(path=path, content=content, line=line, type_name=type_name)
        if op is None:
            continue
        operations.append(op)
        matched.append(diagnostic)
    return _repair_plan_or_none(
        rule_id="typescript.object_assign_assertion",
        source_tool=TYPESCRIPT_OBJECT_ASSIGN_ASSERTION_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched,
        mode=mode,
        metadata={"object_assign_assertions": len(operations)},
    )
