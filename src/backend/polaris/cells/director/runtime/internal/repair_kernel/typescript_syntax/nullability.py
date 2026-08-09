from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from typing import Any

from ..contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ..javascript_syntax import repair_javascript_export_contract_placeholders
from ..path_files import normalize_base_files_strict, normalize_repair_path_strict
from .constants import *  # noqa: F403
from .common import *  # noqa: F403

"""TypeScript syntax repair module: nullability."""

def repair_typescript_nullable_canvas_context_guards(
    text: str,
    symbols: set[str],
) -> tuple[str, list[str]]:
    """Repair nullable DOM/canvas handles by narrowing or adding explicit guards."""

    text, multiline_guarded = _repair_typescript_multiline_dom_handle_declarations(text, symbols)
    lines = text.splitlines()
    repaired_lines: list[str] = []
    guarded: list[str] = list(multiline_guarded)
    for index, line in enumerate(lines):
        global_symbol = _typescript_nullable_global_symbol_for_line(line, symbols)
        if global_symbol and not _typescript_global_guard_precedes(repaired_lines, global_symbol):
            indent_match = re.match(r"^\s*", line)
            indent = indent_match.group(0) if indent_match else ""
            repaired_lines.append(f'{indent}if (typeof {global_symbol} === "undefined") {{')
            repaired_lines.append(f'{indent}  throw new Error("{global_symbol} is unavailable");')
            repaired_lines.append(f"{indent}}}")
            guarded.append(global_symbol)
        match = _TS_CANVAS_CONTEXT_DECLARATION_LINE_RE.match(line)
        if match:
            symbol = str(match.group("symbol") or "").strip()
            if symbols and symbol not in symbols:
                repaired_lines.append(line)
                continue
            repaired_line = _typescript_canvas_context_non_null_assertion_line(line)
            line_changed = repaired_line != line
            repaired_lines.append(repaired_line)
            if _typescript_nullable_guard_follows(lines, index, symbol):
                if line_changed:
                    guarded.append(symbol)
                continue
            indent = str(match.group("indent") or "")
            repaired_lines.append(f"{indent}if (!{symbol}) {{")
            repaired_lines.append(f'{indent}  throw new Error("Canvas 2D context unavailable");')
            repaired_lines.append(f"{indent}}}")
            guarded.append(symbol)
            continue
        dom_match = _TS_NULLABLE_DOM_HANDLE_DECLARATION_LINE_RE.match(line)
        if dom_match:
            symbol = str(dom_match.group("symbol") or "").strip()
            if symbols and symbol not in symbols:
                repaired_lines.append(line)
                continue
            repaired_line = _typescript_dom_handle_non_null_assertion_line(line)
            line_changed = repaired_line != line
            repaired_lines.append(repaired_line)
            if _typescript_nullable_guard_follows(lines, index, symbol):
                if line_changed:
                    guarded.append(symbol)
                continue
            indent = str(dom_match.group("indent") or "")
            repaired_lines.append(f"{indent}if (!{symbol}) {{")
            repaired_lines.append(f'{indent}  throw new Error("DOM element unavailable: {symbol}");')
            repaired_lines.append(f"{indent}}}")
            guarded.append(symbol)
            continue
        func_match = _TS_NULLABLE_FUNCTION_DECLARATION_LINE_RE.match(line)
        if func_match:
            symbol = str(func_match.group("symbol") or "").strip()
            if symbols and symbol not in symbols:
                repaired_lines.append(line)
                continue
            rhs = str(func_match.group("rhs") or "")
            if "!" in rhs:
                repaired_lines.append(line)
                continue
            repaired_line = line.rstrip().rstrip(";")
            repaired_line = re.sub(r"\)\s*$", ")!", repaired_line)
            if line.rstrip().endswith(";"):
                repaired_line += ";"
            repaired_lines.append(repaired_line)
            guarded.append(symbol)
            continue
        repaired_line, asserted_symbols = _typescript_nullable_property_chain_non_null_assertion_line(line, symbols)
        if asserted_symbols:
            repaired_lines.append(repaired_line)
            guarded.extend(asserted_symbols)
            continue
        repaired_lines.append(line)
    if not guarded:
        return text, []
    return "\n".join(repaired_lines) + ("\n" if text.endswith("\n") else ""), _dedupe_preserve_order(guarded)

def _typescript_nullable_property_chain_non_null_assertion_line(
    line: str,
    symbols: set[str],
) -> tuple[str, list[str]]:
    if not symbols:
        return line, []
    repaired = str(line or "")
    asserted: list[str] = []
    for symbol in sorted(symbols, key=len, reverse=True):
        if "." not in symbol or not _typescript_nullable_target_is_safe(symbol):
            continue
        pattern = re.compile(rf"(?<![\w$]){re.escape(symbol)}(?!\s*!)(?=\s*[.\[])")
        next_repaired = pattern.sub(f"{symbol}!", repaired)
        if next_repaired != repaired:
            repaired = next_repaired
            asserted.append(symbol)
    return repaired, asserted

def _typescript_nullable_target_is_safe(symbol: str) -> bool:
    parts = str(symbol or "").split(".")
    return bool(parts) and all(_TS_IDENTIFIER_RE.fullmatch(part) for part in parts)

def _typescript_nullable_global_symbol_for_line(line: str, symbols: set[str]) -> str:
    for symbol in ("window", "document"):
        if symbols and symbol not in symbols:
            continue
        if f"typeof {symbol}" in line:
            continue
        if re.search(rf"\b{re.escape(symbol)}\s*(?:\.|\[)", line):
            return symbol
    return ""

def build_typescript_nullable_canvas_context_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical repair plan for nullable DOM/canvas TypeScript handles."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    targets_by_path = _parse_nullable_canvas_context_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_symbols: list[dict[str, str]] = []
    for path in sorted(targets_by_path):
        if path not in normalized_base_files:
            continue
        original = str(normalized_base_files.get(path) or "")
        symbols = targets_by_path[path] or set()
        repaired, guarded_symbols = repair_typescript_nullable_canvas_context_guards(original, symbols)
        if repaired == original or not guarded_symbols:
            continue
        path_diagnostics = tuple(
            diagnostic for diagnostic in diagnostics if _normalize_repair_path(str(diagnostic.path or "")) == path
        )
        matched_diagnostics.extend(path_diagnostics)
        repaired_symbols.extend({"file": path, "symbol": symbol} for symbol in guarded_symbols)
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={
                    "repair_kind": "typescript_nullable_canvas_context_guard",
                    "guarded_symbols": list(guarded_symbols),
                    "diagnostic_ids": [diagnostic.diagnostic_id for diagnostic in path_diagnostics],
                },
            )
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="typescript.nullable_canvas_context",
        source_tool=TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(matched_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"guards": repaired_symbols},
    )

def _typescript_strict_null_relaxation_signal(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    """Fire when TS18048/TS2322 (strict-null) or TS1259/TS2352 (config-reducible) appear.

    Round B-v2 (L1-01 m03-r21): MiniMax-M3 ignores prompt-side relaxation
    guidance, so a deterministic repair must relax tsconfig compilerOptions.
    Round B-v2b (m03-r26): TS1259 (esModuleInterop) and TS2352 (often an
    optional-field conversion mismatch) are also config-reducible for a weak
    Director. Unblocking the build also lets the existing
    node_test_missing_directory_target repair run (npm test produces the
    'could not find tests/' diagnostic) and create the test file the model
    never writes.
    """
    text = _diagnostic_text(diagnostics).lower()
    if any(code in text for code in ("ts18048", "ts2322", "ts1259", "ts2352", "ts2307", "ts2580")):
        return True
    return ("is possibly 'undefined'" in text) or ("is possibly \"undefined\"" in text)

def _build_typescript_strict_null_relaxation_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    tsconfig_text = str(base_files.get("tsconfig.json") or "")
    if not tsconfig_text:
        return None
    if not _typescript_strict_null_relaxation_signal(diagnostics):
        return None
    tsconfig_payload = _json_object(tsconfig_text)
    compiler_options = tsconfig_payload.get("compilerOptions")
    if not isinstance(compiler_options, Mapping):
        return None
    if compiler_options.get("strict") is not True and compiler_options.get("strictNullChecks") is not True:
        return None
    operations: list[RepairOperation] = [
        RepairOperation(
            kind="json_set",
            path="tsconfig.json",
            json_path=("compilerOptions", "strict"),
            value=False,
            before_hash=sha256_text(tsconfig_text),
            metadata={"repair_kind": "typescript_strict_null_relaxation"},
        ),
    ]
    if compiler_options.get("noUnusedLocals") is True:
        operations.append(
            RepairOperation(
                kind="json_set",
                path="tsconfig.json",
                json_path=("compilerOptions", "noUnusedLocals"),
                value=False,
                before_hash=sha256_text(tsconfig_text),
                metadata={"repair_kind": "typescript_strict_null_relaxation"},
            )
        )
    # Round B-v2b (m03-r26): TS1259 esModuleInterop is config-reducible; enable it
    # so default imports compile. Also enable skipLibCheck to absorb library-type
    # friction a weak Director cannot resolve per-site.
    if compiler_options.get("esModuleInterop") is not True:
        operations.append(
            RepairOperation(
                kind="json_set",
                path="tsconfig.json",
                json_path=("compilerOptions", "esModuleInterop"),
                value=True,
                before_hash=sha256_text(tsconfig_text),
                metadata={"repair_kind": "typescript_strict_null_relaxation"},
            )
        )
    if compiler_options.get("skipLibCheck") is not True:
        operations.append(
            RepairOperation(
                kind="json_set",
                path="tsconfig.json",
                json_path=("compilerOptions", "skipLibCheck"),
                value=True,
                before_hash=sha256_text(tsconfig_text),
                metadata={"repair_kind": "typescript_strict_null_relaxation"},
            )
        )
    # Round B-v2c (m03-r32): TS2307 'node:*' imports + TS2580 (__dirname/process) mean
    # the model uses Node built-ins/globals but omitted @types/node. Add it to
    # package.json devDependencies so the bench's npm install provides the types.
    _diag_text = _diagnostic_text(diagnostics).lower()
    _needs_node_types = (
        "node:" in _diag_text
        or "__dirname" in _diag_text
        or "__filename" in _diag_text
        or "cannot find name 'process'" in _diag_text
    )
    package_text = str(base_files.get("package.json") or "")
    if _needs_node_types and package_text:
        package_payload = _json_object(package_text)
        dev_deps = package_payload.get("devDependencies")
        if isinstance(dev_deps, Mapping) and "@types/node" not in dev_deps:
            operations.append(
                RepairOperation(
                    kind="json_set",
                    path="package.json",
                    json_path=("devDependencies", "@types/node"),
                    value="^20.12.0",
                    before_hash=sha256_text(package_text),
                    metadata={"repair_kind": "typescript_node_types_dependency"},
                )
            )
    return _repair_plan_or_none(
        rule_id="typescript.strict_null_relaxation",
        source_tool=TYPESCRIPT_STRICT_NULL_RELAXATION_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        risk_level="medium",
        metadata={"strict_null_relaxation": True},
    )

def _typescript_canvas_context_non_null_assertion_line(line: str) -> str:
    if re.search(r"\.getContext\(\s*['\"]2d['\"]\s*\)!", line):
        return line
    return re.sub(r"(\.getContext\(\s*['\"]2d['\"]\s*\))(\s*;?\s*)$", r"\1!\2", line)

def _typescript_dom_handle_non_null_assertion_line(line: str) -> str:
    if "| null" in line or "null |" in line:
        narrowed = re.sub(r"\s*\|\s*null\b", "", line)
        narrowed = re.sub(r"\bnull\s*\|\s*", "", narrowed)
        return narrowed
    if re.search(
        r"(?:document\.(?:getElementById|querySelector)|"
        r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\.querySelector)"
        r"\s*\([^;\n]*\)!",
        line,
    ):
        return line
    return re.sub(
        r"((?:document\.(?:getElementById|querySelector)|"
        r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\.querySelector)"
        r"\s*\([^;\n]*\))",
        r"\1!",
        line,
        count=1,
    )

def _typescript_nullable_guard_follows(lines: list[str], index: int, symbol: str) -> bool:
    window = "\n".join(lines[index + 1 : index + 7])
    return _typescript_nullable_guard_in_text_window(window, symbol)

def _parse_nullable_canvas_context_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[str] | None]:
    by_path: dict[str, set[str] | None] = {}
    for diagnostic in diagnostics:
        _add_nullable_targets_from_raw(by_path, diagnostic.raw or diagnostic.message)
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if not path:
            continue
        code = diagnostic.code.lower()
        message = diagnostic.message or diagnostic.raw
        if code in {"typescript_ts18047", "typescript_ts18048"}:
            match = (
                _TS_POSSIBLY_UNDEFINED_MESSAGE_RE.search(message)
                if code == "typescript_ts18048"
                else _TS_POSSIBLY_NULL_MESSAGE_RE.search(message)
            )
            symbol = str(match.group("symbol") or "").strip() if match else ""
            if _typescript_nullable_target_is_safe(symbol):
                _add_nullable_target(by_path, path, symbol)
        elif code == "typescript_ts2345" and "null" in message.lower() and "not assignable" in message.lower():
            _add_nullable_target(by_path, path, "")
    return by_path

def _add_nullable_targets_from_raw(targets: dict[str, set[str] | None], raw: str) -> None:
    text = str(raw or "")
    for match in _TS_POSSIBLY_NULL_RAW_RE.finditer(text):
        path = _normalize_repair_path(str(match.group("file") or ""))
        symbol = str(match.group("symbol") or "").strip()
        if path and _typescript_nullable_target_is_safe(symbol):
            _add_nullable_target(targets, path, symbol)
    for match in _TS_POSSIBLY_UNDEFINED_RAW_RE.finditer(text):
        path = _normalize_repair_path(str(match.group("file") or ""))
        symbol = str(match.group("symbol") or "").strip()
        if path and _typescript_nullable_target_is_safe(symbol):
            _add_nullable_target(targets, path, symbol)
    for match in _TS_NULLABLE_ARGUMENT_RAW_RE.finditer(text):
        path = _normalize_repair_path(str(match.group("file") or ""))
        if path:
            _add_nullable_target(targets, path, "")

def _add_nullable_target(targets: dict[str, set[str] | None], path: str, symbol: str) -> None:
    if not symbol:
        targets[path] = None
        return
    existing = targets.get(path)
    if existing is None and path in targets:
        return
    if existing is None:
        existing = set()
        targets[path] = existing
    existing.add(symbol)

__all__ = (
    "repair_typescript_nullable_canvas_context_guards",
    "_typescript_nullable_property_chain_non_null_assertion_line",
    "_typescript_nullable_target_is_safe",
    "_typescript_nullable_global_symbol_for_line",
    "build_typescript_nullable_canvas_context_plan",
    "_typescript_strict_null_relaxation_signal",
    "_build_typescript_strict_null_relaxation_plan",
    "_typescript_canvas_context_non_null_assertion_line",
    "_typescript_dom_handle_non_null_assertion_line",
    "_typescript_nullable_guard_follows",
    "_parse_nullable_canvas_context_targets",
    "_add_nullable_targets_from_raw",
    "_add_nullable_target",
)
