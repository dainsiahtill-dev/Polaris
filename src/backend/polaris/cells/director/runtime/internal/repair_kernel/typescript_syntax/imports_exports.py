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

"""TypeScript syntax repair module: imports_exports."""

def _build_typescript_import_specifier_keyword_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    repaired_files: list[dict[str, object]] = []
    for path in _typescript_syntax_error_paths(diagnostics):
        original = str(base_files.get(path) or "")
        repaired, replacements = _repair_typescript_import_specifier_keywords(original)
        if not original or repaired == original or not replacements:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={
                    "repair_kind": "typescript_import_specifier_keyword",
                    "replacements": tuple(replacements),
                },
            )
        )
        repaired_files.append({"file": path, "replacements": tuple(replacements)})
    matched_diagnostics = tuple(
        diagnostic for diagnostic in diagnostics if _normalize_repair_path(str(diagnostic.path or "")) in base_files
    )
    return _repair_plan_or_none(
        rule_id="typescript.import_specifier_keyword",
        source_tool=TYPESCRIPT_IMPORT_SPECIFIER_KEYWORD_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"files": repaired_files},
    )

def _build_typescript_missing_export_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    exports: list[dict[str, str]] = []
    updated: dict[str, str] = {}
    exports_by_path: dict[str, list[dict[str, str]]] = {}
    for item in _parse_typescript_missing_export_errors(diagnostics):
        operation, meta = _missing_export_operation(base_files={**base_files, **updated}, item=item)
        if operation is None:
            continue
        updated[operation.path] = _apply_single_text_operation(
            updated.get(operation.path) or base_files[operation.path], operation
        )
        exports.append(meta)
        exports_by_path.setdefault(operation.path, []).append(meta)
    operations: list[RepairOperation] = []
    for path, repaired in sorted(updated.items()):
        original = str(base_files.get(path) or "")
        symbols = [item.get("symbol", "") for item in exports_by_path.get(path, []) if item.get("symbol")]
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={
                    "repair_kind": "typescript_missing_export",
                    "symbols": symbols,
                    "batched_same_file_exports": True,
                },
            )
        )
    return _repair_plan_or_none(
        rule_id="typescript.missing_export",
        source_tool=TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"exports": exports},
    )

def _build_typescript_reexport_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    if not _looks_like_typescript_reexport_signal(diagnostics):
        return None
    operations: list[RepairOperation] = []
    reexports: list[dict[str, str]] = []
    for importer_path, importer_text in base_files.items():
        if not importer_path.endswith((".ts", ".tsx")):
            continue
        for match in _TS_NAMED_IMPORT_RE.finditer(importer_text):
            module_path = _resolve_relative_ts_module_path(importer_path, str(match.group("module") or ""), base_files)
            if not module_path:
                continue
            module_text = str(base_files.get(module_path) or "")
            for symbol in _parse_named_import_symbols(str(match.group("symbols") or "")):
                if _typescript_module_exports_symbol(module_text, symbol):
                    continue
                source_path = _find_unique_runtime_export_source(base_files, module_path, symbol)
                if not source_path:
                    continue
                export_line = _build_typescript_reexport_line(
                    module_path=module_path,
                    source_path=source_path,
                    symbol=symbol,
                    source_text=str(base_files.get(source_path) or ""),
                )
                if export_line in module_text:
                    continue
                start = len(module_text.rstrip())
                replacement = f"\n{export_line}\n" if start else f"{export_line}\n"
                operations.append(
                    RepairOperation(
                        kind="text_replace",
                        path=module_path,
                        span_start=start,
                        span_end=len(module_text),
                        expected=module_text[start:],
                        replacement=replacement,
                        before_hash=sha256_text(module_text),
                        metadata={
                            "repair_kind": "typescript_runtime_reexport",
                            "symbol": symbol,
                            "source": source_path,
                            "expected_context_before": module_text[max(0, start - 240) : start],
                            "expected_context_after": module_text[len(module_text) : len(module_text)],
                        },
                    )
                )
                reexports.append({"file": module_path, "symbol": symbol, "source": source_path})
    return _repair_plan_or_none(
        rule_id="typescript.reexport",
        source_tool=TYPESCRIPT_REEXPORT_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"reexports": reexports},
    )

def _build_typescript_export_ambiguity_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    targets = _typescript_export_ambiguity_targets(diagnostics)
    if not targets:
        return None

    grouped: dict[tuple[str, int, int, str], list[str]] = {}
    grouped_metadata: dict[tuple[str, int, int, str], list[dict[str, object]]] = {}
    matched_diagnostics: list[RepairDiagnostic] = []
    for target in targets:
        path = str(target["path"])
        module = str(target["module"])
        symbol = str(target["symbol"])
        diagnostic = target["diagnostic"]
        if not isinstance(diagnostic, RepairDiagnostic):
            continue
        content = str(base_files.get(path) or "")
        if not content:
            continue
        star_match = next(
            (match for match in _TS_EXPORT_STAR_RE.finditer(content) if str(match.group("module") or "") == module),
            None,
        )
        if star_match is None:
            continue
        if _typescript_file_has_named_reexport(content, module=module, symbol=symbol):
            continue
        source_path = _resolve_relative_ts_module_path(path, module, base_files)
        if not source_path:
            continue
        source_text = str(base_files.get(source_path) or "")
        if not source_text or not _typescript_module_exports_symbol(source_text, symbol):
            continue
        export_keyword = "export type" if _typescript_exported_symbol_is_type_only(source_text, symbol) else "export"
        export_line = (
            f"{star_match.group('indent')}{export_keyword} {{ {symbol} }} "
            f"from {star_match.group('quote')}{module}{star_match.group('quote')};"
        )
        key = (path, star_match.start(), star_match.end(), str(star_match.group(0) or ""))
        lines = grouped.setdefault(key, [])
        if export_line not in lines:
            lines.append(export_line)
            grouped_metadata.setdefault(key, []).append(
                {
                    "file": path,
                    "module": module,
                    "symbol": symbol,
                    "source": source_path,
                    "type_only": export_keyword == "export type",
                }
            )
        matched_diagnostics.append(diagnostic)

    operations: list[RepairOperation] = []
    repaired: list[dict[str, object]] = []
    for (path, start, end, expected), export_lines in sorted(grouped.items()):
        content = str(base_files.get(path) or "")
        key = (path, start, end, expected)
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=start,
                span_end=end,
                expected=expected,
                replacement=f"{expected}\n" + "\n".join(export_lines),
                before_hash=sha256_text(content),
                metadata={
                    "repair_kind": "typescript_export_ambiguity",
                    "exports": tuple(grouped_metadata.get(key, ())),
                },
            )
        )
        repaired.extend(grouped_metadata.get(key, ()))

    return _repair_plan_or_none(
        rule_id="typescript.export_ambiguity",
        source_tool=TYPESCRIPT_EXPORT_AMBIGUITY_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"explicit_reexports": repaired},
    )

def _build_typescript_reexported_type_binding_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    imports: list[dict[str, str]] = []
    updated = dict(base_files)
    for item in _parse_typescript_cannot_find_name_errors(diagnostics):
        path = item["file"]
        original = str(updated.get(path) or "")
        if not original or not _typescript_missing_identifier_usage_is_type_position(original, item):
            continue
        repaired, import_meta = _add_typescript_reexported_type_binding(original, missing_symbol=item["symbol"])
        if repaired == original or not import_meta:
            continue
        path_operations = _text_replace_operations_from_repair(
            path=path,
            original=original,
            repaired=repaired,
            metadata={"repair_kind": "typescript_reexported_type_binding", **import_meta},
        )
        operations.extend(path_operations)
        updated[path] = repaired
        imports.append({"file": path, **import_meta})
    return _repair_plan_or_none(
        rule_id="typescript.reexported_type_binding",
        source_tool=TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"imports": imports},
    )

def _build_typescript_relative_import_case_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    return _build_relative_import_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
        source_tool=TYPESCRIPT_RELATIVE_IMPORT_CASE_SOURCE_TOOL,
        rule_id="typescript.relative_import_case",
        mode_filter="case",
    )

def _build_typescript_unique_export_import_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    # R164: type Flower + value Flower in one import block (TS2300/TS1361) before
    # barrel re-export uniqueness rewrites.
    conflict_plan = _build_typescript_import_type_value_conflict_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
    )
    if conflict_plan is not None:
        return conflict_plan
    duplicate_plan = _build_typescript_duplicate_export_import_binding_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
    )
    if duplicate_plan is not None:
        return duplicate_plan
    return _build_relative_import_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
        source_tool=TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
        rule_id="typescript.unique_export_import",
        mode_filter="unique_export",
    )

def _import_type_value_conflict_symbols(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[str]]:
    """Map path → symbols that collide as type-only + value imports (R164)."""

    targets: dict[str, set[str]] = {}
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        code = str(diagnostic.code or "").lower()
        path = _normalize_repair_path(str(diagnostic.path or ""))
        symbol = ""
        if code == "typescript_ts1361" or "TS1361" in text:
            match = _TS_IMPORT_TYPE_AS_VALUE_RAW_RE.search(text) or _TS_IMPORT_TYPE_AS_VALUE_MESSAGE_RE.search(text)
            if match:
                path = path or _normalize_repair_path(str(match.groupdict().get("file") or ""))
                symbol = str(match.group("symbol") or "")
        elif code == "typescript_ts2300" or "TS2300" in text or "Duplicate identifier" in text:
            match = _TS_DUPLICATE_IDENTIFIER_MESSAGE_RE.search(text)
            if match:
                symbol = str(match.group("name") or "")
            if not path:
                file_match = re.match(r"(?P<file>[^:\n]+\.tsx?)\(", text)
                if file_match:
                    path = _normalize_repair_path(str(file_match.group("file") or ""))
        if path and _TS_IDENTIFIER_RE.fullmatch(symbol):
            targets.setdefault(path, set()).add(symbol)
    return targets

def _rewrite_import_type_value_conflict_content(
    *,
    content: str,
    symbols: set[str],
) -> tuple[str, list[dict[str, str]]]:
    """Drop type-only bindings when a value import of the same name exists.

    Live L1-01 r164 simulation.ts imported both ``type Flower`` and ``Flower``
    from ``../models``, then constructed ``new Flower(...)`` → TS2300 + TS1361.
    Prefer the value binding; if only a type binding exists for a TS1361 symbol,
    promote it to a value binding.
    """

    if not symbols or not content:
        return content, []
    rewrites: list[dict[str, str]] = []
    lines = content.splitlines(keepends=True)
    # Track multi-line ``import { … } from`` / ``import type { … } from`` groups.
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        is_type_import_stmt = bool(re.match(r"import\s+type\s*\{", stripped))
        is_value_import_stmt = bool(re.match(r"import\s*(?:type\s+)?\{", stripped)) and not is_type_import_stmt
        if not (is_type_import_stmt or is_value_import_stmt or re.match(r"import\s*\{", stripped)):
            # Single-line type import without brace form is rare; skip.
            if re.match(r"import\s+type\s+[A-Za-z_$]", stripped):
                i += 1
                continue
            i += 1
            continue
        # Collect the full import statement span.
        start = i
        block = line
        while "}" not in block and i + 1 < len(lines):
            i += 1
            block += lines[i]
        end = i
        # Detect whether this is a pure import-type statement.
        pure_type = bool(re.match(r"\s*import\s+type\s*\{", block))
        # Value names already present without type keyword in this block.
        value_names = {
            m.group(1)
            for m in re.finditer(
                r"(?<![.\w$])(?<!type\s)(?<!type\s\s)([A-Za-z_$][\w$]*)\s*(?:,|\}|as\s)",
                block,
            )
            if m.group(1) not in {"type", "from", "import", "as"}
        }
        # More precise: split specifier list.
        brace = re.search(r"\{([^}]*)\}", block, re.DOTALL)
        if not brace:
            i += 1
            continue
        specs = [s.strip() for s in brace.group(1).split(",") if s.strip()]
        type_specs: set[str] = set()
        value_specs: set[str] = set()
        for spec in specs:
            # ``type Flower`` or ``type Flower as F``
            type_match = re.match(r"type\s+([A-Za-z_$][\w$]*)(?:\s+as\s+[A-Za-z_$][\w$]*)?$", spec)
            if type_match:
                type_specs.add(type_match.group(1))
                continue
            value_match = re.match(r"([A-Za-z_$][\w$]*)(?:\s+as\s+[A-Za-z_$][\w$]*)?$", spec)
            if value_match:
                value_specs.add(value_match.group(1))
        if pure_type:
            # ``import type { Flower }`` puts names without a ``type`` keyword
            # inside the braces — treat every named specifier as type-only.
            pure_names: set[str] = set()
            for spec in specs:
                m = re.match(r"(?:type\s+)?([A-Za-z_$][\w$]*)(?:\s+as\s+[A-Za-z_$][\w$]*)?$", spec)
                if m:
                    pure_names.add(m.group(1))
            promote = symbols & pure_names
            if not promote:
                i += 1
                continue
            # Rewrite import type { … } → import { … } for the whole statement
            # when any diagnosed symbol is present (TS1361 needs value binding).
            new_block = re.sub(r"import\s+type\s*\{", "import {", block, count=1)
            if new_block == block:
                i += 1
                continue
            for name in sorted(promote):
                rewrites.append({"symbol": name, "action": "promote_import_type_to_value"})
            lines[start : end + 1] = [new_block if new_block.endswith("\n") else new_block + "\n"]
            i = start + 1
            continue
        # Mixed: drop type bindings when a value binding exists for the same symbol
        # or when symbol is diagnosed (TS2300/TS1361).
        drop = (type_specs & value_specs) | (type_specs & symbols)
        if not drop:
            i += 1
            continue
        new_specs = []
        for spec in specs:
            type_match = re.match(r"type\s+([A-Za-z_$][\w$]*)(?:\s+as\s+[A-Za-z_$][\w$]*)?$", spec)
            if type_match and type_match.group(1) in drop:
                # If no value binding, promote instead of drop.
                if type_match.group(1) not in value_specs:
                    new_specs.append(type_match.group(1))
                    rewrites.append({"symbol": type_match.group(1), "action": "promote_inline_type_to_value"})
                else:
                    rewrites.append({"symbol": type_match.group(1), "action": "drop_type_binding"})
                continue
            new_specs.append(spec)
        if not new_specs:
            # Entire import became empty — remove the statement.
            del lines[start : end + 1]
            i = start
            continue
        # Rebuild brace contents preserving multi-line style when original was multi-line.
        if start == end:
            new_line = line[: brace.start()] + "{ " + ", ".join(new_specs) + " }" + line[brace.end() :]
            lines[start] = new_line
        else:
            indent = re.match(r"(\s*)", lines[start + 1] if start + 1 <= end else lines[start])
            pad = indent.group(1) if indent else "  "
            rebuilt = [lines[start][: lines[start].find("{") + 1] + "\n"]
            for idx, spec in enumerate(new_specs):
                comma = "," if idx < len(new_specs) - 1 else ""
                rebuilt.append(f"{pad}{spec}{comma}\n")
            # closing from last original line
            close_line = lines[end]
            close_suffix = close_line[close_line.find("}") :] if "}" in close_line else "}\n"
            rebuilt.append(close_suffix if close_suffix.endswith("\n") else close_suffix + "\n")
            lines[start : end + 1] = rebuilt
            i = start + len(rebuilt)
            continue
        i += 1
    return "".join(lines), rewrites

def _build_typescript_import_type_value_conflict_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Resolve type-only vs value import conflicts (TS2300 / TS1361, R164)."""

    targets = _import_type_value_conflict_symbols(diagnostics)
    if not targets:
        return None
    normalized_base = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    operations: list[RepairOperation] = []
    rewrites: list[dict[str, str]] = []
    matched: list[RepairDiagnostic] = []
    for path, symbols in sorted(targets.items()):
        content = str(normalized_base.get(path) or "")
        if not content:
            continue
        repaired, file_rewrites = _rewrite_import_type_value_conflict_content(
            content=content,
            symbols=symbols,
        )
        if repaired == content or not file_rewrites:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=content,
                repaired=repaired,
                metadata={
                    "repair_kind": "typescript_import_type_value_conflict",
                    "symbols": sorted(symbols),
                },
            )
        )
        for item in file_rewrites:
            rewrites.append({"file": path, **item})
        for diagnostic in diagnostics:
            diag_path = _normalize_repair_path(str(diagnostic.path or ""))
            text = str(diagnostic.raw or diagnostic.message or "")
            if diag_path == path or path in text:
                matched.append(diagnostic)
    return _repair_plan_or_none(
        rule_id="typescript.import_type_value_conflict",
        source_tool=TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched,
        mode=mode,
        metadata={"import_type_value_conflicts": rewrites},
    )

def _build_typescript_duplicate_export_import_binding_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    targets = _typescript_duplicate_identifier_targets(diagnostics)
    if not targets:
        return None

    operations: list[RepairOperation] = []
    repaired: list[dict[str, object]] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    for path, names in sorted(targets.items()):
        content = str(base_files.get(path) or "")
        if not content:
            continue
        path_operations = _typescript_duplicate_export_import_operations(
            path=path,
            content=content,
            duplicate_names=names,
        )
        if not path_operations:
            continue
        operations.extend(path_operations)
        matched_diagnostics.extend(
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.path == path and diagnostic.code == "typescript_ts2300"
        )
        repaired.append({"file": path, "symbols": tuple(sorted(names)), "operation_count": len(path_operations)})

    return _repair_plan_or_none(
        rule_id="typescript.duplicate_export_import_binding",
        source_tool=TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"duplicate_export_import_bindings": repaired},
    )

def _build_typescript_unused_import_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    import_plan = _build_relative_import_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
        source_tool=TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL,
        rule_id="typescript.unused_import",
        mode_filter="unused",
    )
    operations: list[RepairOperation] = list(import_plan.operations if import_plan is not None else ())
    import_repairs = list(import_plan.metadata.get("imports", ()) if import_plan is not None else ())
    parameter_operations, parameter_repairs = _typescript_unused_parameter_operations(
        base_files=base_files,
        diagnostics=diagnostics,
    )
    operations.extend(parameter_operations)
    rule_id = "typescript.unused_import"
    if parameter_operations and not import_repairs:
        rule_id = "typescript.unused_parameter"
    elif parameter_operations:
        rule_id = "typescript.unused_declaration"
    return _repair_plan_or_none(
        rule_id=rule_id,
        source_tool=TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"imports": import_repairs, "parameters": parameter_repairs},
    )

def _repair_typescript_embedded_import_type_declarations(
    original: str,
) -> tuple[str, tuple[dict[str, str], ...]]:
    lines = str(original or "").splitlines(keepends=True)
    if not lines:
        return str(original or ""), ()

    output: list[str] = []
    replacements: list[dict[str, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not _TS_NAMED_IMPORT_BLOCK_START_LINE_RE.match(line.rstrip("\r\n")):
            output.append(line)
            index += 1
            continue

        original_segment = [line]
        repaired_segment = [line]
        moved_imports: list[tuple[str, dict[str, str]]] = []
        index += 1
        block_closed = False
        while index < len(lines):
            inner_line = lines[index]
            original_segment.append(inner_line)
            stripped_inner = inner_line.rstrip("\r\n")
            embedded_match = _TS_EMBEDDED_IMPORT_TYPE_LINE_RE.match(stripped_inner)
            if embedded_match is not None:
                symbols = " ".join(str(embedded_match.group("symbols") or "").strip().split())
                module = str(embedded_match.group("module") or "").strip()
                quote = str(embedded_match.group("quote") or '"')
                import_line = f"import type {{ {symbols} }} from {quote}{module}{quote};{_line_ending(inner_line)}"
                moved_imports.append(
                    (
                        import_line,
                        {
                            "keyword": "import type",
                            "symbol": symbols,
                            "module": module,
                            "repair_kind": "embedded_import_type_declaration",
                        },
                    )
                )
                index += 1
                continue

            repaired_segment.append(inner_line)
            if _TS_NAMED_IMPORT_BLOCK_END_LINE_RE.match(stripped_inner):
                block_closed = True
                index += 1
                break
            index += 1

        if not block_closed or not moved_imports:
            output.extend(original_segment)
            continue

        seen_moved: set[str] = set()
        for import_line, metadata in moved_imports:
            normalized = " ".join(import_line.strip().rstrip(";").split())
            if normalized in seen_moved:
                continue
            seen_moved.add(normalized)
            output.append(import_line)
            replacements.append(metadata)
        output.extend(repaired_segment)

    if not replacements:
        return str(original or ""), ()
    return "".join(output), tuple(replacements)

def _repair_typescript_import_specifier_keywords(original: str) -> tuple[str, tuple[dict[str, str], ...]]:
    text, embedded_replacements = _repair_typescript_embedded_import_type_declarations(str(original or ""))
    replacements: list[dict[str, str]] = []
    replacements.extend(embedded_replacements)
    pieces: list[str] = []
    cursor = 0
    for match in _TS_NAMED_IMPORT_RE.finditer(text):
        symbols = str(match.group("symbols") or "")
        repaired_symbols = _TS_IMPORT_SPECIFIER_KEYWORD_RE.sub(r"\g<prefix>type \g<symbol>", symbols)
        if repaired_symbols == symbols:
            continue
        pieces.append(text[cursor : match.start("symbols")])
        pieces.append(repaired_symbols)
        cursor = match.end("symbols")
        for keyword_match in _TS_IMPORT_SPECIFIER_KEYWORD_RE.finditer(symbols):
            replacements.append(
                {
                    "keyword": str(keyword_match.group("keyword") or ""),
                    "symbol": str(keyword_match.group("symbol") or ""),
                    "module": str(match.group("module") or ""),
                }
            )
    if not pieces:
        return text, tuple(replacements)
    pieces.append(text[cursor:])
    return "".join(pieces), tuple(replacements)

def _typescript_remove_named_import_binding_anywhere(
    *,
    path: str,
    content: str,
    name: str,
) -> RepairOperation | None:
    """Remove a named import binding without requiring a diagnostic line number."""

    if not path or not content or not _TS_IDENTIFIER_RE.fullmatch(name):
        return None
    for match in _TS_REEXPORTABLE_NAMED_IMPORT_RE.finditer(content):
        pairs = _typescript_import_pairs_from_clause("{" + str(match.group("names") or "") + "}")
        if not any(local == name for _, local in pairs):
            continue
        import_text = content[match.start() : match.end()]
        if len(pairs) == 1:
            end = match.end()
            if end < len(content) and content[end : end + 1] == "\n":
                end += 1
            return RepairOperation(
                kind="text_replace",
                path=path,
                span_start=match.start(),
                span_end=end,
                expected=content[match.start() : end],
                replacement="",
                before_hash=sha256_text(content),
                metadata={
                    "repair_kind": "typescript_unused_import",
                    "compiler_reported_unused_binding": True,
                    "binding": name,
                    "module_specifier": str(match.group("module") or ""),
                },
            )
        replacement = _remove_typescript_named_import_binding(import_text=import_text, name=name)
        if not replacement or replacement == import_text:
            continue
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=match.start(),
            span_end=match.end(),
            expected=import_text,
            replacement=replacement,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_unused_import_specifier",
                "compiler_reported_unused_binding": True,
                "binding": name,
                "module_specifier": str(match.group("module") or ""),
            },
        )
    return None

def _parse_typescript_missing_export_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for pattern in (
            _UNRESOLVED_IMPORT_SYMBOL_ERROR_RE,
            _TS_NO_EXPORTED_MEMBER_ERROR_RE,
            _TS_NO_EXPORTED_MEMBER_NAMED_ERROR_RE,
            _TS_DECLARES_LOCALLY_NOT_EXPORTED_ERROR_RE,
        ):
            for match in pattern.finditer(text):
                module = _normalize_typescript_module_ref(match.group("module"))
                groups = match.groupdict()
                parsed.append(
                    {
                        "file": _normalize_repair_path(
                            str(groups.get("path") or groups.get("file") or "")
                        ),
                        "module": module,
                        "symbol": str(match.group("symbol") or "").strip(),
                        "suggestion": str(groups.get("suggestion") or "").strip(),
                        "line": str(groups.get("line") or "").strip(),
                    }
                )
    return [item for item in parsed if item["file"] and item["module"] and item["symbol"]]

def _missing_export_operation(
    *,
    base_files: Mapping[str, str],
    item: Mapping[str, str],
) -> tuple[RepairOperation | None, dict[str, str]]:
    importer = str(item.get("file") or "")
    module_ref = str(item.get("module") or "")
    symbol = str(item.get("symbol") or "")
    exporter = _resolve_relative_ts_module_path(importer, module_ref, base_files)
    if not exporter or not _TS_IDENTIFIER_RE.fullmatch(symbol):
        return None, {}
    original = str(base_files.get(exporter) or "")
    if _typescript_module_exports_symbol(original, symbol):
        return None, {}
    importer_text = str(base_files.get(importer) or "")
    suggestion = str(item.get("suggestion") or "").strip()
    if not suggestion:
        suggestion = _find_typescript_similar_runtime_declaration(original, symbol)
    # R176/M10: unused_local mis-prefixed import (`_HumidityBand`) → TS2724 with
    # suggestion `HumidityBand` that already exports. Remove phantom import binding
    # on the importer instead of forging a underscore export alias.
    if (
        suggestion
        and symbol == f"_{suggestion}"
        and _typescript_module_exports_symbol(original, suggestion)
        and importer_text
    ):
        line_number = _to_positive_int(item.get("line"))
        remove_op = None
        if line_number > 0:
            remove_op = _typescript_unused_import_declaration_operation(
                path=importer,
                content=importer_text,
                name=symbol,
                line_number=line_number,
            )
        if remove_op is None:
            remove_op = _typescript_remove_named_import_binding_anywhere(
                path=importer,
                content=importer_text,
                name=symbol,
            )
        if remove_op is not None:
            return remove_op, {
                "file": importer,
                "symbol": symbol,
                "kind": "remove_phantom_underscore_import",
                "suggestion": suggestion,
            }
    if suggestion:
        declaration_kind, declaration = _build_typescript_suggested_export_alias_declaration(
            symbol=symbol,
            suggestion=suggestion,
            importer_text=importer_text,
            module_text=original,
        )
        operation = _append_typescript_missing_export_declaration_operation(
            path=exporter,
            original=original,
            declaration=declaration,
            symbol=symbol,
            declaration_kind=declaration_kind,
        )
        if operation is not None:
            return operation, {"file": exporter, "symbol": symbol, "kind": declaration_kind}
        return None, {
            "file": exporter,
            "symbol": symbol,
            "kind": "unsafe_alias_rejected",
            "blocked_reason": "missing_export_alias_candidate_not_type_compatible",
        }
    # R180/M10: prefer unique sibling reexport before inventing empty stubs.
    source_path = _find_unique_runtime_export_source(base_files, exporter, symbol)
    if source_path and not _typescript_reexport_would_cycle(exporter, source_path, base_files):
        export_line = _build_typescript_reexport_line(
            module_path=exporter,
            source_path=source_path,
            symbol=symbol,
            source_text=str(base_files.get(source_path) or ""),
        )
        operation = _append_typescript_missing_export_declaration_operation(
            path=exporter,
            original=original,
            declaration=export_line,
            symbol=symbol,
            declaration_kind="unique_source_reexport",
        )
        if operation is not None:
            return operation, {
                "file": exporter,
                "symbol": symbol,
                "kind": "unique_source_reexport",
                "source": source_path,
            }
    exported, declaration_kind = _reexport_imported_typescript_symbol(original, symbol)
    if exported == original:
        exported = _export_existing_typescript_declaration(original, symbol)
        declaration_kind = "export_existing"
    if exported == original:
        declaration_kind, declaration = _build_typescript_missing_export_declaration(
            symbol=symbol,
            importer_text=importer_text,
            base_files=base_files,
        )
        operation = _append_typescript_missing_export_declaration_operation(
            path=exporter,
            original=original,
            declaration=declaration,
            symbol=symbol,
            declaration_kind=declaration_kind,
        )
        if operation is not None:
            return operation, {"file": exporter, "symbol": symbol, "kind": declaration_kind}
        return None, {
            "file": exporter,
            "symbol": symbol,
            "kind": "interface_contract_required",
            "blocked_reason": "missing_export_declaration_not_found",
        }
    ops = _text_replace_operations_from_repair(
        path=exporter,
        original=original,
        repaired=exported,
        metadata={
            "repair_kind": "typescript_missing_export",
            "symbol": symbol,
            "declaration_kind": declaration_kind,
        },
    )
    return (ops[0], {"file": exporter, "symbol": symbol, "kind": declaration_kind}) if len(ops) == 1 else (None, {})

def _build_typescript_missing_export_declaration(
    *,
    symbol: str,
    importer_text: str,
    base_files: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    if not _TS_IDENTIFIER_RE.fullmatch(symbol):
        return "", ""
    corpus = importer_text
    if base_files:
        # R180: scan whole workspace so field-assigned constructors (this.scene = new X)
        # and cross-file method usage enrich the stub class.
        corpus = "\n".join(str(text or "") for text in base_files.values())
    if _typescript_symbol_is_constructed(importer_text, symbol) or _typescript_symbol_is_constructed(corpus, symbol):
        if not (
            _typescript_symbol_has_named_constructor_binding(importer_text, symbol)
            or _typescript_symbol_has_named_constructor_binding(corpus, symbol)
            or _typescript_symbol_has_field_constructor_binding(corpus, symbol)
        ):
            # Still invent a class when constructed; field assignment counts as binding.
            if not _typescript_symbol_is_constructed(corpus, symbol):
                return "", ""
        return "class", _build_typescript_missing_export_class_declaration(
            symbol=symbol,
            importer_text=importer_text,
            base_files=base_files,
        )
    if _typescript_symbol_is_called(importer_text, symbol) or _typescript_symbol_is_called(corpus, symbol):
        return "function", f"export function {symbol}(..._args: unknown[]): any {{\n  return undefined;\n}}"
    if symbol[:1].isupper():
        return "type", f"export type {symbol} = any;"
    return "const", f"export const {symbol}: unknown = undefined;"

def _build_typescript_suggested_export_alias_declaration(
    *,
    symbol: str,
    suggestion: str,
    importer_text: str,
    module_text: str,
) -> tuple[str, str]:
    if not _TS_IDENTIFIER_RE.fullmatch(symbol) or not _TS_IDENTIFIER_RE.fullmatch(suggestion):
        return "", ""
    if symbol == suggestion or not _typescript_module_declares_symbol(module_text, suggestion):
        return "", ""
    suggestion_kind = _typescript_module_declared_symbol_kind(module_text, suggestion)
    if _typescript_symbol_is_constructed(importer_text, symbol):
        if suggestion_kind == "class":
            return "runtime_alias", f"export {{ {suggestion} as {symbol} }};"
        return "", ""
    if _typescript_symbol_is_called(importer_text, symbol):
        if suggestion_kind in {"const", "function", "let", "var"}:
            return "runtime_alias", f"export {{ {suggestion} as {symbol} }};"
        return "", ""
    if suggestion_kind in {"class", "enum", "interface", "type"}:
        return "type_alias", f"export type {symbol} = {suggestion};"
    if suggestion_kind in {"const", "let", "var", "function"}:
        return "runtime_alias", f"export {{ {suggestion} as {symbol} }};"
    return "", ""

def _build_typescript_missing_export_class_declaration(
    *,
    symbol: str,
    importer_text: str,
    base_files: Mapping[str, str] | None = None,
) -> str:
    methods = list(_typescript_methods_used_on_constructed_symbol(importer_text, symbol))
    if base_files:
        for text in base_files.values():
            methods.extend(_typescript_methods_used_on_constructed_symbol(str(text or ""), symbol))
    methods = _dedupe_preserve_order(methods)
    lines = [
        f"export class {symbol} {{",
        "  public constructor(..._args: unknown[]) {}",
    ]
    for method in methods:
        return_type = "string" if method in {"report", "render", "toString"} else "any"
        if method == "snapshot":
            return_value = (
                '{ fireflies: [], flowers: [], environment: { moon: "full", '
                "moonIllumination: 1, humidityRatio: 0.5, humidityBand: "
                '"comfortable", tick: 0 } }'
            )
        else:
            return_value = f'"{symbol} ready"' if return_type == "string" else "undefined"
        lines.extend(
            [
                f"  public {method}(..._args: unknown[]): {return_type} {{",
                f"    return {return_value};",
                "  }",
            ]
        )
    lines.append("}")
    return "\n".join(lines)

def _reexport_imported_typescript_symbol(text: str, symbol: str) -> tuple[str, str]:
    if not _TS_IDENTIFIER_RE.fullmatch(symbol):
        return text, "interface_contract_required"
    for match in _TS_REEXPORTABLE_NAMED_IMPORT_RE.finditer(str(text or "")):
        specifiers = _typescript_import_specifiers(match.group("names"))
        imported = specifiers.get(symbol)
        if imported is None:
            continue
        declaration_kind = (
            "export_type_reexport" if match.group("type_only") or imported == "type" else "export_reexport"
        )
        export_prefix = "export type" if declaration_kind == "export_type_reexport" else "export"
        quote = str(match.group("quote") or '"')
        module_ref = str(match.group("module") or "")
        declaration = f"{export_prefix} {{ {symbol} }} from {quote}{module_ref}{quote};"
        if declaration in text:
            return text, declaration_kind
        insert_at = match.end()
        separator = "\n" if text[insert_at : insert_at + 1] == "\n" else "\n\n"
        return f"{text[:insert_at]}{separator}{declaration}{text[insert_at:]}", declaration_kind
    return text, "interface_contract_required"

def _typescript_import_specifiers(raw: str) -> dict[str, str]:
    specifiers: dict[str, str] = {}
    for item in str(raw or "").replace("\n", " ").split(","):
        token = item.strip()
        if not token:
            continue
        kind = "value"
        if token.startswith("type "):
            kind = "type"
            token = token[5:].strip()
        imported = token.split(" as ", 1)[0].strip()
        if _TS_IDENTIFIER_RE.fullmatch(imported):
            specifiers[imported] = kind
    return specifiers

def _append_typescript_missing_export_declaration_operation(
    *,
    path: str,
    original: str,
    declaration: str,
    symbol: str,
    declaration_kind: str,
) -> RepairOperation | None:
    token = str(original or "")
    declaration_text = "\n\n" + str(declaration or "").rstrip() + "\n"
    if not declaration_text.strip():
        return None
    if token.endswith("\n"):
        span_start = len(token) - 1
        span_end = len(token)
        expected = "\n"
        replacement = declaration_text
    elif token:
        span_start = len(token) - 1
        span_end = len(token)
        expected = token[-1]
        replacement = f"{token[-1]}{declaration_text}"
    else:
        return None
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=span_start,
        span_end=span_end,
        expected=expected,
        replacement=replacement,
        before_hash=sha256_text(token),
        metadata={
            "repair_kind": "typescript_missing_export",
            "symbol": symbol,
            "declaration_kind": declaration_kind,
            "append_declaration": True,
        },
    )

def _typescript_export_ambiguity_targets(diagnostics: Sequence[RepairDiagnostic]) -> tuple[dict[str, object], ...]:
    targets: list[dict[str, object]] = []
    for diagnostic in diagnostics:
        if diagnostic.code != "typescript_ts2308":
            continue
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if not path:
            continue
        match = _TS_EXPORT_AMBIGUITY_MESSAGE_RE.search(str(diagnostic.message or diagnostic.raw or ""))
        if not match:
            continue
        module = str(match.group("module") or "").strip()
        symbol = str(match.group("symbol") or "").strip()
        if not module.startswith(".") or not _TS_IDENTIFIER_RE.fullmatch(symbol):
            continue
        targets.append({"path": path, "module": module, "symbol": symbol, "diagnostic": diagnostic})
    return tuple(targets)

def _typescript_duplicate_export_import_operations(
    *,
    path: str,
    content: str,
    duplicate_names: set[str],
) -> tuple[RepairOperation, ...]:
    imported_by_module = _typescript_named_imports_by_module(content)
    locally_exported_names = _typescript_local_named_export_names(content)
    locally_type_exported_names = _typescript_local_type_named_export_names(content)
    value_reexports_by_module = _typescript_named_reexports_by_module(content, type_only=False)
    type_reexports_by_module = _typescript_named_reexports_by_module(content, type_only=True)
    type_reexported_names = set().union(*type_reexports_by_module.values()) if type_reexports_by_module else set()
    if not (
        (imported_by_module and locally_exported_names)
        or (value_reexports_by_module and type_reexports_by_module)
        or (locally_type_exported_names and type_reexported_names)
    ):
        return ()

    operations: list[RepairOperation] = []
    before_hash = sha256_text(content)
    for match in _TS_NAMED_REEXPORT_RE.finditer(content):
        module = str(match.group("module") or "")
        symbols = str(match.group("symbols") or "")
        is_type_reexport = str(match.group(0) or "").lstrip().startswith("export type")
        if is_type_reexport:
            removable = (
                duplicate_names
                & (value_reexports_by_module.get(module, set()) | locally_exported_names)
                & _typescript_named_value_specifier_names(symbols)
            )
        else:
            removable = duplicate_names & imported_by_module.get(module, set()) & locally_exported_names
        if not removable:
            continue
        replacement_symbols, removed = _remove_typescript_named_export_symbols(symbols, removable)
        if not removed:
            continue
        if replacement_symbols.strip():
            operations.append(
                RepairOperation(
                    kind="text_replace",
                    path=path,
                    span_start=match.start("symbols"),
                    span_end=match.end("symbols"),
                    expected=str(match.group("symbols") or ""),
                    replacement=replacement_symbols,
                    before_hash=before_hash,
                    metadata={
                        "repair_kind": "typescript_duplicate_export_import_binding",
                        "module": module,
                        "removed_symbols": tuple(sorted(removed)),
                    },
                )
            )
            continue
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=match.start(),
                span_end=match.end(),
                expected=str(match.group(0) or ""),
                replacement="",
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_duplicate_export_import_binding",
                    "module": module,
                    "removed_symbols": tuple(sorted(removed)),
                    "removed_empty_export_statement": True,
                },
            )
        )
    for match in _TS_LOCAL_TYPE_NAMED_EXPORT_RE.finditer(content):
        symbols = str(match.group("symbols") or "")
        removable = duplicate_names & type_reexported_names & _typescript_named_export_specifier_names(symbols)
        if not removable:
            continue
        replacement_symbols, removed = _remove_typescript_named_export_symbols(symbols, removable)
        if not removed:
            continue
        if replacement_symbols.strip():
            operations.append(
                RepairOperation(
                    kind="text_replace",
                    path=path,
                    span_start=match.start("symbols"),
                    span_end=match.end("symbols"),
                    expected=symbols,
                    replacement=replacement_symbols,
                    before_hash=before_hash,
                    metadata={
                        "repair_kind": "typescript_duplicate_type_reexport_binding",
                        "removed_symbols": tuple(sorted(removed)),
                    },
                )
            )
            continue
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=match.start(),
                span_end=match.end(),
                expected=str(match.group(0) or ""),
                replacement="",
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_duplicate_type_reexport_binding",
                    "removed_symbols": tuple(sorted(removed)),
                    "removed_empty_export_statement": True,
                },
            )
        )
    return tuple(operations)

def _typescript_named_imports_by_module(content: str) -> dict[str, set[str]]:
    imported: dict[str, set[str]] = {}
    for match in _TS_NAMED_IMPORT_RE.finditer(content):
        module = str(match.group("module") or "")
        symbols = set(_parse_named_import_symbols(str(match.group("symbols") or "")))
        if module and symbols:
            imported.setdefault(module, set()).update(symbols)
    return imported

def _typescript_named_reexports_by_module(content: str, *, type_only: bool) -> dict[str, set[str]]:
    exported: dict[str, set[str]] = {}
    for match in _TS_NAMED_REEXPORT_RE.finditer(content):
        raw = str(match.group(0) or "")
        symbols = str(match.group("symbols") or "")
        is_type_reexport = raw.lstrip().startswith("export type")
        module = str(match.group("module") or "")
        if type_only:
            names = (
                _typescript_named_export_specifier_names(symbols)
                if is_type_reexport
                else _typescript_named_type_specifier_names(symbols)
            )
        else:
            if is_type_reexport:
                continue
            names = _typescript_named_value_specifier_names(symbols)
        if module and names:
            exported.setdefault(module, set()).update(names)
    return exported

def _typescript_local_named_export_names(content: str) -> set[str]:
    names: set[str] = set()
    for match in _TS_LOCAL_NAMED_EXPORT_RE.finditer(content):
        names.update(_typescript_named_value_specifier_names(str(match.group("symbols") or "")))
    return names

def _typescript_local_type_named_export_names(content: str) -> set[str]:
    names: set[str] = set()
    for match in _TS_LOCAL_TYPE_NAMED_EXPORT_RE.finditer(content):
        names.update(_typescript_named_export_specifier_names(str(match.group("symbols") or "")))
    return names

def _typescript_named_export_specifier_names(symbols: str) -> set[str]:
    names: set[str] = set()
    for raw in str(symbols or "").split(","):
        name = _typescript_named_export_specifier_name(raw)
        if name:
            names.add(name)
    return names

def _remove_typescript_named_export_symbols(symbols: str, removable: set[str]) -> tuple[str, set[str]]:
    parts = [part.strip() for part in str(symbols or "").split(",")]
    kept: list[str] = []
    removed: set[str] = set()
    for part in parts:
        if not part:
            continue
        name = _typescript_named_value_specifier_name(part)
        if name and name in removable:
            removed.add(name)
            continue
        kept.append(part)
    if not removed:
        return symbols, set()
    if "\n" not in symbols:
        return f"{', '.join(kept)} " if kept else "", removed
    indent = _typescript_named_specifier_indent(symbols)
    return "".join(f"{part},\n{indent}" for part in kept).removesuffix(indent), removed

def _typescript_exported_symbol_is_type_only(module_text: str, symbol: str) -> bool:
    escaped = re.escape(symbol)
    return bool(re.search(rf"\bexport\s+(?:interface|type)\s+{escaped}\b", module_text))

def _typescript_file_has_named_reexport(content: str, *, module: str, symbol: str) -> bool:
    value_reexports = _typescript_named_reexports_by_module(content, type_only=False)
    type_reexports = _typescript_named_reexports_by_module(content, type_only=True)
    return symbol in value_reexports.get(module, set()) or symbol in type_reexports.get(module, set())

def _find_unique_runtime_export_source(base_files: Mapping[str, str], module_path: str, symbol: str) -> str:
    matches = [
        path
        for path, text in base_files.items()
        if path != module_path and path.endswith((".ts", ".tsx")) and _typescript_module_exports_symbol(text, symbol)
    ]
    return matches[0] if len(matches) == 1 else ""

def _typescript_reexport_would_cycle(
    module_path: str,
    source_path: str,
    base_files: Mapping[str, str],
) -> bool:
    """True when ``source`` already reexports/imports from ``module`` (barrel cycle)."""

    source_text = str(base_files.get(source_path) or "")
    if not source_text:
        return False
    module_dir = posixpath.dirname(module_path)
    source_dir = posixpath.dirname(source_path)
    # Compare normalized relative refs both directions.
    try:
        rel_from_source = posixpath.relpath(
            module_path.removesuffix(".ts").removesuffix(".tsx"), source_dir or "."
        )
    except ValueError:
        rel_from_source = ""
    candidates = {rel_from_source, f"./{rel_from_source}" if rel_from_source and not rel_from_source.startswith(".") else rel_from_source}
    candidates = {c for c in candidates if c}
    for match in re.finditer(
        r"""(?:export|import)\s+(?:\*\s+from\s+|\{[^}]*\}\s+from\s+)?['"](?P<mod>[^'"]+)['"]""",
        source_text,
    ):
        mod = str(match.group("mod") or "").removesuffix(".js").removesuffix(".ts").removesuffix(".tsx")
        if mod in candidates or mod.lstrip("./") == posixpath.basename(
            module_path.removesuffix(".ts").removesuffix(".tsx")
        ):
            # Also accept path equality via resolve.
            resolved = _resolve_relative_ts_module_path(source_path, str(match.group("mod") or ""), base_files)
            if resolved == module_path:
                return True
            if mod in candidates:
                return True
    _ = module_dir  # reserved for future relative-path hardening
    return False

def _build_typescript_reexport_line(
    *,
    module_path: str,
    source_path: str,
    symbol: str,
    source_text: str = "",
) -> str:
    module_dir = posixpath.dirname(module_path)
    rel = posixpath.relpath(source_path.removesuffix(".ts").removesuffix(".tsx"), module_dir or ".")
    if not rel.startswith("."):
        rel = f"./{rel}"
    prefix = "export type" if _typescript_exported_symbol_is_type_only(source_text, symbol) else "export"
    return f"{prefix} {{ {symbol} }} from '{rel}';"

def _looks_like_typescript_reexport_signal(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    text = _diagnostic_text(diagnostics).lower()
    if not any(hint in text for hint in ("typescript", ".ts", ".tsx", "vitest", "npm test", "ts2305", "ts2459", "ts2724")):
        # Still accept pure tsc diagnostics that already carry path+.ts markers.
        if not any(token in text for token in (".ts(", ".tsx(", "error ts")):
            return False
    return any(
        hint in text
        for hint in (
            "cannot read properties of undefined",
            "undefined",
            "missing export",
            "re-export",
            "reexport",
            "import/export",
            "export/import",
            "contract fix",
            "has no exported member",
            "no exported member",
            "declares",
            "not exported",
            "ts2305",
            "ts2459",
            "ts2724",
        )
    )

def _add_typescript_reexported_type_binding(text: str, *, missing_symbol: str) -> tuple[str, dict[str, str]]:
    symbol = str(missing_symbol or "").strip()
    if not _TS_IDENTIFIER_RE.fullmatch(symbol):
        return text, {}
    for match in _TS_NAMED_REEXPORT_RE.finditer(str(text or "")):
        module = str(match.group("module") or "")
        if symbol not in _parse_named_import_symbols(str(match.group("symbols") or "")):
            continue
        import_line = f'import type {{ {symbol} }} from "{module}";\n'
        if import_line in text:
            return text, {}
        return import_line + text, {"symbol": symbol, "module": module}
    return text, {}

def _build_relative_import_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
    source_tool: str,
    rule_id: str,
    mode_filter: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    repairs: list[dict[str, str]] = []
    updated = dict(base_files)
    for item in _parse_unresolved_relative_import_errors(diagnostics):
        importer = item["file"]
        specifier = item["specifier"]
        content = str(updated.get(importer) or "")
        if not specifier.startswith(".") or not content:
            continue
        operation: RepairOperation | None = None
        metadata: dict[str, str] = {"specifier": specifier}
        actual_target = _resolve_relative_import_target(
            base_files=updated,
            importer_rel=importer,
            specifier=specifier,
            allow_case_variant=True,
        )
        if mode_filter == "case":
            if not actual_target:
                continue
            corrected = _relative_import_specifier_for_actual_path(
                importer_rel=importer,
                original_specifier=specifier,
                actual_target_rel=actual_target,
            )
            if corrected == specifier:
                continue
            operation = _replace_import_specifier_operation(
                path=importer,
                content=content,
                specifier=specifier,
                replacement=corrected,
                metadata={
                    "repair_kind": "typescript_relative_import_case",
                    "specifier": specifier,
                    "corrected_specifier": corrected,
                    "target_file": actual_target,
                },
            )
            metadata = {"specifier": specifier, "corrected_specifier": corrected, "target_file": actual_target}
        elif mode_filter == "unused":
            if actual_target:
                continue
            operation = _remove_unused_typescript_import_operation(path=importer, content=content, specifier=specifier)
        elif mode_filter == "unique_export":
            if actual_target:
                continue
            actual_target = _find_unique_typescript_export_for_import(
                base_files=updated,
                importer_path=importer,
                content=content,
                specifier=specifier,
            )
            if not actual_target:
                continue
            corrected = _relative_import_specifier_for_actual_path(
                importer_rel=importer,
                original_specifier=specifier,
                actual_target_rel=actual_target,
            )
            if corrected == specifier:
                continue
            operation = _replace_import_specifier_operation(
                path=importer,
                content=content,
                specifier=specifier,
                replacement=corrected,
                metadata={
                    "repair_kind": "typescript_unique_export_import",
                    "specifier": specifier,
                    "corrected_specifier": corrected,
                    "target_file": actual_target,
                },
            )
            metadata = {"specifier": specifier, "corrected_specifier": corrected, "target_file": actual_target}
        if operation is None:
            continue
        updated[operation.path] = _apply_single_text_operation(content, operation)
        operations.append(operation)
        repairs.append({"file": importer, **metadata})
    return _repair_plan_or_none(
        rule_id=rule_id,
        source_tool=source_tool,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"imports": repairs},
    )

def _parse_unresolved_relative_import_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for diagnostic in diagnostics:
        for match in _UNRESOLVED_RELATIVE_IMPORT_ERROR_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            item = {
                "file": _normalize_repair_path(str(match.group("path") or "")),
                "specifier": str(match.group("specifier") or "").strip(),
            }
            key = (item["file"], item["specifier"])
            if item["file"] and item["specifier"].startswith(".") and key not in seen:
                seen.add(key)
                parsed.append(item)
    return parsed

def _resolve_relative_import_target(
    *,
    base_files: Mapping[str, str],
    importer_rel: str,
    specifier: str,
    allow_case_variant: bool,
) -> str:
    for candidate in _relative_import_repair_target_candidates(importer_rel=importer_rel, specifier=specifier):
        if candidate in base_files:
            return candidate
        if allow_case_variant:
            case_variant = _resolve_case_variant_base_file(base_files=base_files, relative_path=candidate)
            if case_variant:
                return case_variant
    return ""

def _relative_import_repair_target_candidates(*, importer_rel: str, specifier: str) -> list[str]:
    base_dir = posixpath.dirname(importer_rel)
    raw = _normalize_repair_path(posixpath.normpath(posixpath.join(base_dir, specifier)))
    if not raw:
        return []
    suffix = posixpath.splitext(raw)[1]
    if suffix:
        return [raw]
    return [
        raw,
        f"{raw}.ts",
        f"{raw}.tsx",
        f"{raw}.js",
        f"{raw}.jsx",
        posixpath.join(raw, "index.ts"),
        posixpath.join(raw, "index.tsx"),
        posixpath.join(raw, "index.js"),
        posixpath.join(raw, "index.jsx"),
    ]

def _replace_import_specifier_operation(
    *,
    path: str,
    content: str,
    specifier: str,
    replacement: str,
    metadata: Mapping[str, object],
) -> RepairOperation | None:
    match = _typescript_import_statement_for_specifier(content, specifier)
    if match is None:
        return None
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=match.start("specifier"),
        span_end=match.end("specifier"),
        expected=specifier,
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata=dict(metadata),
    )

def _typescript_import_statement_for_specifier(content: str, specifier: str) -> re.Match[str] | None:
    pattern = re.compile(_TS_IMPORT_FROM_SPECIFIER_TEMPLATE.format(specifier=re.escape(specifier)))
    return pattern.search(content)

def _typescript_import_pairs_for_specifier(content: str, specifier: str) -> list[tuple[str, str]]:
    match = _typescript_import_statement_for_specifier(content, specifier)
    if match is None:
        return []
    return _typescript_import_pairs_from_clause(str(match.group("clause") or ""))

def _remove_unused_typescript_import_operation(
    *,
    path: str,
    content: str,
    specifier: str,
) -> RepairOperation | None:
    match = _typescript_import_statement_for_specifier(content, specifier)
    if match is None:
        return None
    pairs = _typescript_import_pairs_for_specifier(content, specifier)
    if not pairs:
        return None
    span = match.span()
    if any(_typescript_identifier_used_outside_span(content, local, span) for _, local in pairs):
        return None
    start, end = span
    if end < len(content) and content[end : end + 1] == "\n":
        end += 1
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=start,
        span_end=end,
        expected=content[start:end],
        replacement="",
        before_hash=sha256_text(content),
        metadata={"repair_kind": "typescript_unused_import", "specifier": specifier},
    )

def _find_unique_typescript_export_for_import(
    *,
    base_files: Mapping[str, str],
    importer_path: str,
    content: str,
    specifier: str,
) -> str:
    match = _typescript_import_statement_for_specifier(content, specifier)
    if match is None:
        return ""
    needed_symbols = [
        imported
        for imported, local in _typescript_import_pairs_for_specifier(content, specifier)
        if imported != "default" and _typescript_identifier_used_outside_span(content, local, match.span())
    ]
    if not needed_symbols:
        return ""
    candidates = [
        path
        for path, text in base_files.items()
        if path != importer_path
        and path.endswith((".ts", ".tsx"))
        and not path.endswith(".d.ts")
        and all(_typescript_module_exports_symbol(text, symbol) for symbol in needed_symbols)
    ]
    return candidates[0] if len(candidates) == 1 else ""

def _export_existing_typescript_declaration(text: str, symbol: str) -> str:
    escaped = re.escape(symbol)
    declaration_re = re.compile(
        rf"(?m)^(?P<indent>\s*)(?P<declare>declare\s+)?(?P<kind>(?:abstract\s+)?class|function|interface|type|const|let|var|enum)\s+{escaped}\b"
    )

    def replace(match: re.Match[str]) -> str:
        return f"{match.group('indent')}export {match.group('declare') or ''}{match.group('kind')} {symbol}"

    return declaration_re.sub(replace, text, count=1)

__all__ = (
    "_build_typescript_import_specifier_keyword_plan",
    "_build_typescript_missing_export_plan",
    "_build_typescript_reexport_plan",
    "_build_typescript_export_ambiguity_plan",
    "_build_typescript_reexported_type_binding_plan",
    "_build_typescript_relative_import_case_plan",
    "_build_typescript_unique_export_import_plan",
    "_import_type_value_conflict_symbols",
    "_rewrite_import_type_value_conflict_content",
    "_build_typescript_import_type_value_conflict_plan",
    "_build_typescript_duplicate_export_import_binding_plan",
    "_build_typescript_unused_import_plan",
    "_repair_typescript_embedded_import_type_declarations",
    "_repair_typescript_import_specifier_keywords",
    "_typescript_remove_named_import_binding_anywhere",
    "_parse_typescript_missing_export_errors",
    "_missing_export_operation",
    "_build_typescript_missing_export_declaration",
    "_build_typescript_suggested_export_alias_declaration",
    "_build_typescript_missing_export_class_declaration",
    "_reexport_imported_typescript_symbol",
    "_typescript_import_specifiers",
    "_append_typescript_missing_export_declaration_operation",
    "_typescript_export_ambiguity_targets",
    "_typescript_duplicate_export_import_operations",
    "_typescript_named_imports_by_module",
    "_typescript_named_reexports_by_module",
    "_typescript_local_named_export_names",
    "_typescript_local_type_named_export_names",
    "_typescript_named_export_specifier_names",
    "_remove_typescript_named_export_symbols",
    "_typescript_exported_symbol_is_type_only",
    "_typescript_file_has_named_reexport",
    "_find_unique_runtime_export_source",
    "_typescript_reexport_would_cycle",
    "_build_typescript_reexport_line",
    "_looks_like_typescript_reexport_signal",
    "_add_typescript_reexported_type_binding",
    "_build_relative_import_plan",
    "_parse_unresolved_relative_import_errors",
    "_resolve_relative_import_target",
    "_relative_import_repair_target_candidates",
    "_replace_import_specifier_operation",
    "_typescript_import_statement_for_specifier",
    "_typescript_import_pairs_for_specifier",
    "_remove_unused_typescript_import_operation",
    "_find_unique_typescript_export_for_import",
    "_export_existing_typescript_declaration",
)
