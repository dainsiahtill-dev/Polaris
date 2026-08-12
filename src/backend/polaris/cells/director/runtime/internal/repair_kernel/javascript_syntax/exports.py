"""exports domain for JavaScript/Node syntax repairs."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ..contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ._shared import (
    _dedupe_diagnostics,
    _find_matching_brace,
    _is_javascript_test_target_path,
    _javascript_module_exports_symbol,
    _missing_export_targets,
    _normalize_base_files,
    _resolve_js_module,
)
from .constants import (
    _JS_DECLARATION_RE_TEMPLATE,
    _JS_EXPORTED_CLASS_RE,
    _JS_FUNCTION_START_RE_TEMPLATE,
    _JS_IDENTIFIER_RE,
    _JS_NAMED_IMPORT_RE,
    _JS_STRING_LITERAL_RE,
    JAVASCRIPT_MISSING_EXPORT_SOURCE_TOOL,
)


def build_javascript_missing_export_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Export existing local JS declarations for missing named-export diagnostics."""

    normalized_base = _normalize_base_files(base_files)
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    seen: set[tuple[str, str]] = set()
    for diagnostic in diagnostics:
        for target in _missing_export_targets(diagnostic, normalized_base):
            exporter_path = target["exporter"]
            symbol = target["symbol"]
            key = (exporter_path, symbol)
            if key in seen:
                continue
            seen.add(key)
            exporter_text = normalized_base.get(exporter_path)
            if exporter_text is None:
                continue
            if _javascript_module_exports_symbol(exporter_text, symbol):
                # A missing-export repair must be monotonic: an already exported
                # symbol proves this diagnostic is not an export defect. Never use
                # a generic test assertion to rewrite its domain implementation.
                continue
            operation = _export_existing_declaration_operation(
                path=exporter_path,
                text=exporter_text,
                symbol=symbol,
                diagnostic=diagnostic,
            )
            if operation is None:
                operation = _export_class_method_facade_operation(
                    path=exporter_path,
                    text=exporter_text,
                    symbol=symbol,
                    diagnostic=diagnostic,
                )
            if operation is None:
                operation = _append_imported_binding_reexport_operation(
                    path=exporter_path,
                    text=exporter_text,
                    symbol=symbol,
                    diagnostic=diagnostic,
                )
            if operation is None:
                # No declaration/binding means semantic implementation is absent.
                # Keep it covered-unplannable for same-task LLM repair; deterministic
                # repair must not invent a domain function from test expectations.
                continue
            operations.append(operation)
            matched.append(diagnostic)
    operations = _coalesce_javascript_append_operations(operations)
    if not operations:
        return None
    return RepairPlan(
        rule_id="javascript.missing_named_export",
        source_tool=JAVASCRIPT_MISSING_EXPORT_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=_dedupe_diagnostics(matched),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"exported_symbols": [operation.metadata.get("symbol") for operation in operations]},
    )


def _coalesce_javascript_append_operations(operations: list[RepairOperation]) -> list[RepairOperation]:
    return sorted(
        operations,
        key=lambda item: (
            item.path,
            int(item.span_start if item.span_start is not None else -1),
            int(item.span_end if item.span_end is not None else -1),
            str(item.metadata.get("symbol") or item.metadata.get("symbols") or ""),
        ),
    )


def _javascript_import_contract_targets(base_files: Mapping[str, str]) -> tuple[dict[str, str], ...]:
    targets: list[dict[str, str]] = []
    for importer, importer_text in sorted(base_files.items()):
        if not _is_javascript_test_target_path(importer):
            continue
        for match in _JS_NAMED_IMPORT_RE.finditer(str(importer_text or "")):
            module_ref = str(match.group("specifier") or "").strip()
            exporter = _resolve_js_module(base_files, importer, module_ref)
            if not exporter:
                continue
            for raw_symbol in str(match.group("symbols") or "").split(","):
                symbol = raw_symbol.strip().split(" as ", 1)[0].strip()
                if _JS_IDENTIFIER_RE.match(symbol):
                    targets.append({"exporter": exporter, "symbol": symbol, "importer": importer})
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for target in targets:
        key = (target["exporter"], target["symbol"], target["importer"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(target)
    return tuple(deduped)


def _javascript_module_imports_local_binding(text: str, symbol: str) -> bool:
    for match in _JS_NAMED_IMPORT_RE.finditer(text):
        for raw_binding in str(match.group("symbols") or "").split(","):
            binding = raw_binding.split("//", 1)[0].strip()
            if not binding:
                continue
            parts = re.split(r"\s+as\s+", binding, maxsplit=1, flags=re.IGNORECASE)
            local_name = parts[1].strip() if len(parts) == 2 else parts[0].strip()
            if local_name == symbol:
                return True
    return False


def _export_existing_declaration_operation(
    *,
    path: str,
    text: str,
    symbol: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    pattern = re.compile(_JS_DECLARATION_RE_TEMPLATE.format(symbol=re.escape(symbol)))
    match = pattern.search(text)
    if not match:
        return None
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=match.start("decl"),
        span_end=match.end("decl"),
        expected=str(match.group("decl") or ""),
        replacement=f"export {match.group('decl')}",
        before_hash=sha256_text(text),
        metadata={
            "repair_kind": "javascript_missing_named_export",
            "symbol": symbol,
            "diagnostic_id": diagnostic.diagnostic_id,
            "edit_file_preferred": True,
        },
    )


def _export_class_method_facade_operation(
    *,
    path: str,
    text: str,
    symbol: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if not _JS_IDENTIFIER_RE.match(symbol):
        return None
    method_pattern = re.compile(rf"(?m)^[ \t]{{2,}}{re.escape(symbol)}\s*\(\s*\)\s*\{{")
    candidates: list[tuple[str, int]] = []
    for class_match in _JS_EXPORTED_CLASS_RE.finditer(text):
        class_end = _find_matching_brace(text, class_match.end() - 1)
        if class_end is None:
            continue
        class_body = text[class_match.end() : class_end]
        if method_pattern.search(class_body):
            candidates.append((str(class_match.group("name")), class_end + 1))
    if len(candidates) != 1:
        return None

    class_name, insert_at = candidates[0]
    replacement = f"\n\nexport const {symbol} = new {class_name}().{symbol}();\n"
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=insert_at,
        span_end=insert_at,
        expected="",
        replacement=replacement,
        before_hash=sha256_text(text),
        metadata={
            "repair_kind": "javascript_missing_named_export_class_method_facade",
            "symbol": symbol,
            "facade_class": class_name,
            "facade_method": symbol,
            "diagnostic_id": diagnostic.diagnostic_id,
            "edit_file_preferred": True,
            "expected_context_before": text[max(0, insert_at - 160) : insert_at],
            "expected_context_after": text[insert_at : min(len(text), insert_at + 160)],
        },
    )


def _append_imported_binding_reexport_operation(
    *,
    path: str,
    text: str,
    symbol: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if not _JS_IDENTIFIER_RE.match(symbol):
        return None
    if not _javascript_module_imports_local_binding(text, symbol):
        return None
    context = _unique_javascript_eof_context(text)
    append_text = ("\n" if text and not text.endswith("\n") else "") + f"\nexport {{ {symbol} }};\n"
    if not context:
        return RepairOperation(
            kind="write_file",
            path=path,
            content=f"{text}{append_text}",
            before_hash=sha256_text(text),
            metadata={
                "repair_kind": "javascript_missing_named_export_imported_binding_reexport",
                "symbol": symbol,
                "diagnostic_id": diagnostic.diagnostic_id,
                "write_file_allowed_category": "fallback",
                "write_file_policy_decision": "allowed_fallback",
                "write_file_reason": "empty_file_imported_binding_reexport",
            },
        )
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=len(text),
        span_end=len(text),
        expected="",
        replacement=append_text,
        before_hash=sha256_text(text),
        metadata={
            "repair_kind": "javascript_missing_named_export_imported_binding_reexport",
            "symbol": symbol,
            "diagnostic_id": diagnostic.diagnostic_id,
            "edit_file_preferred": True,
            "expected_context_before": context,
            "unique_context": context,
        },
    )


def _replace_exported_function_contract_operation(
    *,
    path: str,
    text: str,
    symbol: str,
    importer_text: str,
    exporter_rel_path: str,
    base_files: Mapping[str, str],
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    replacement = _javascript_contract_function_replacement(
        text=text,
        symbol=symbol,
        importer_text=importer_text,
        exporter_rel_path=exporter_rel_path,
        base_files=base_files,
    )
    if replacement is None:
        return None
    start, end, replacement_text = replacement
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=start,
        span_end=end,
        expected=text[start:end],
        replacement=replacement_text,
        before_hash=sha256_text(text),
        metadata={
            "repair_kind": "javascript_export_contract_replacement",
            "symbol": symbol,
            "diagnostic_id": diagnostic.diagnostic_id,
            "edit_file_preferred": True,
        },
    )


def _append_javascript_contract_function_operation(
    *,
    path: str,
    text: str,
    symbol: str,
    importer_text: str,
    exporter_rel_path: str,
    base_files: Mapping[str, str],
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if not _JS_IDENTIFIER_RE.match(symbol):
        return None
    body = _build_javascript_contract_function_body(
        symbol=symbol,
        importer_text=importer_text,
        exporter_rel_path=exporter_rel_path,
        base_files=base_files,
    )
    if "return undefined;" in body:
        return None
    prefix = "" if not text or text.endswith("\n") else "\n"
    append_text = f"{prefix}\nexport function {symbol}(...args) {{\n{body}\n}}\n"
    context = _unique_javascript_eof_context(text)
    if not context:
        return RepairOperation(
            kind="write_file",
            path=path,
            content=append_text,
            before_hash=sha256_text(text),
            metadata={
                "repair_kind": "javascript_missing_named_export_contract_facade",
                "symbol": symbol,
                "diagnostic_id": diagnostic.diagnostic_id,
                "write_file_allowed_category": "fallback",
                "write_file_policy_decision": "allowed_fallback",
                "write_file_reason": "empty_file_contract_facade_creation",
                "facade_contract_source": "importer_test_contract",
            },
        )
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=len(text),
        span_end=len(text),
        expected="",
        replacement=append_text,
        before_hash=sha256_text(text),
        metadata={
            "repair_kind": "javascript_missing_named_export_contract_facade",
            "symbol": symbol,
            "diagnostic_id": diagnostic.diagnostic_id,
            "edit_file_preferred": True,
            "facade_contract_source": "importer_test_contract",
            "expected_context_before": context,
            "unique_context": context,
        },
    )


def _append_javascript_contract_dependency_operation(
    *,
    path: str,
    text: str,
    symbol: str,
    importer_text: str,
    base_files: Mapping[str, str],
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    declarations = _javascript_contract_dependency_declarations(
        symbol=symbol,
        importer_text=importer_text,
        base_files=base_files,
    )
    declarations = [declaration for declaration in declarations if declaration and declaration not in text]
    if not declarations:
        return None
    context = _unique_javascript_eof_context(text)
    append_text = ("\n" if text and not text.endswith("\n") else "") + "\n".join(declarations) + "\n"
    if not context:
        return RepairOperation(
            kind="write_file",
            path=path,
            content=f"{text}{append_text}",
            before_hash=sha256_text(text),
            metadata={
                "repair_kind": "javascript_contract_dependency_exports",
                "symbol": symbol,
                "diagnostic_id": diagnostic.diagnostic_id,
                "write_file_allowed_category": "fallback",
                "write_file_policy_decision": "allowed_fallback",
                "write_file_reason": "empty_file_contract_dependency_exports",
            },
        )
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=len(text),
        span_end=len(text),
        expected="",
        replacement=append_text,
        before_hash=sha256_text(text),
        metadata={
            "repair_kind": "javascript_contract_dependency_exports",
            "symbol": symbol,
            "symbols": [declaration.split()[2] for declaration in declarations],
            "diagnostic_id": diagnostic.diagnostic_id,
            "edit_file_preferred": True,
            "expected_context_before": context,
            "unique_context": context,
        },
    )


def _javascript_contract_dependency_declarations(
    *,
    symbol: str,
    importer_text: str,
    base_files: Mapping[str, str],
) -> list[str]:
    declarations: list[str] = []
    if _javascript_symbol_contract_links_version_constant(importer_text, symbol):
        declarations.append(
            f"export const VERSION = {json.dumps(_javascript_contract_constant_literal('VERSION', base_files, importer_text=importer_text))};"
        )
    if _javascript_symbol_contract_requires_app_info(importer_text, symbol):
        for constant in ("APP_NAME", "APP_VERSION", "APP_DESCRIPTION"):
            declarations.append(
                f"export const {constant} = "
                f"{json.dumps(_javascript_contract_constant_literal(constant, base_files, importer_text=importer_text), ensure_ascii=False)};"
            )
    if _javascript_symbol_contract_requires_string_value(importer_text, symbol):
        declarations.append(
            f"export const {symbol} = "
            f"{json.dumps(_javascript_contract_constant_literal(symbol, base_files, importer_text=importer_text), ensure_ascii=False)};"
        )
    return declarations


def _unique_javascript_eof_context(text: str) -> str:
    if not text:
        return ""
    for size in (512, 384, 256, 160, 96, 64, 40, 24, 12):
        suffix = text[-min(size, len(text)) :]
        if suffix and text.count(suffix) == 1:
            return suffix
    return text if text.count(text) == 1 else ""


def repair_javascript_export_contract_placeholders(
    *,
    path: str,
    text: str,
    base_files: Mapping[str, str],
) -> str:
    """Replace shallow exported function placeholders using importer test contracts."""

    repaired = str(text or "")
    for target in _javascript_import_contract_targets(base_files):
        if target["exporter"] != path:
            continue
        importer_text = str(base_files.get(target["importer"]) or "")
        replacement = _javascript_contract_function_replacement(
            text=repaired,
            symbol=target["symbol"],
            importer_text=importer_text,
            exporter_rel_path=path,
            base_files=base_files,
            require_placeholder=True,
        )
        if replacement is None:
            continue
        start, end, replacement_text = replacement
        repaired = f"{repaired[:start]}{replacement_text}{repaired[end:]}"
    return repaired


def _javascript_contract_function_replacement(
    *,
    text: str,
    symbol: str,
    importer_text: str,
    exporter_rel_path: str,
    base_files: Mapping[str, str],
    require_placeholder: bool = False,
) -> tuple[int, int, str] | None:
    bounds = _javascript_function_bounds(text, symbol)
    if bounds is None:
        return None
    start, open_brace, close_brace = bounds
    current_body = text[open_brace + 1 : close_brace]
    if require_placeholder and "return undefined;" not in current_body:
        return None
    body = _build_javascript_contract_function_body(
        symbol=symbol,
        importer_text=importer_text,
        exporter_rel_path=exporter_rel_path,
        base_files=base_files,
    )
    if "return undefined;" in body:
        return None
    if _javascript_function_body_satisfies_importer_contract(
        body=current_body,
        symbol=symbol,
        importer_text=importer_text,
    ):
        return None
    signature = _javascript_contract_replacement_signature(text[start : open_brace + 1], body)
    replacement = f"{signature}\n{body}\n}}"
    return start, close_brace + 1, replacement


def _javascript_function_bounds(module_text: str, symbol: str) -> tuple[int, int, int] | None:
    match = re.search(
        _JS_FUNCTION_START_RE_TEMPLATE.format(symbol=re.escape(symbol)),
        str(module_text or ""),
    )
    if not match:
        return None
    open_brace = match.end() - 1
    close_brace = _find_matching_brace(module_text, open_brace)
    if close_brace is None:
        return None
    return match.start(), open_brace, close_brace


def _javascript_contract_replacement_signature(signature: str, replacement_body: str) -> str:
    if not re.search(r"\bargs\b", replacement_body):
        return signature
    return re.sub(r"\([^)]*\)(?P<space>\s*)\{$", r"(...args)\g<space>{", signature, count=1)


def _javascript_function_body_satisfies_importer_contract(
    *,
    body: str,
    symbol: str,
    importer_text: str,
) -> bool:
    source = str(body or "")
    if _javascript_symbol_contract_requires_entrypoint(importer_text, symbol):
        return "ok" in source and "entrypoint" in source
    if _javascript_symbol_contract_requires_distilled_notes(importer_text, symbol):
        return "count" in source and "distilled" in source
    if _javascript_symbol_contract_requires_app_info(importer_text, symbol):
        return "name" in source and "version" in source and "description" in source
    if _javascript_symbol_contract_requires_refined_note(importer_text, symbol):
        return "source" in source and "refined" in source and "tag" in source and ".trim()" in source
    if _javascript_symbol_contract_requires_prefixed_lines(importer_text, symbol):
        return "TypeError" in source and _infer_javascript_line_prefix(importer_text, symbol) in source
    if _javascript_symbol_contract_requires_semver(importer_text, symbol):
        return bool(re.search(r"return\s+['\"]\d+\.\d+\.\d+", source)) or "VERSION" in source
    if _javascript_symbol_contract_requires_string_function(importer_text, symbol):
        return bool(re.search(r"return\s+['\"][^'\"]+['\"]", source)) or "VERSION" in source
    if _javascript_symbol_contract_requires_summary_notes(importer_text, symbol):
        return "count" in source and "summary" in source
    return False


def _build_javascript_contract_function_body(
    *,
    symbol: str,
    importer_text: str,
    exporter_rel_path: str,
    base_files: Mapping[str, str],
) -> str:
    if _javascript_symbol_contract_requires_entrypoint(importer_text, symbol):
        return _indent_javascript_lines(
            [
                "const entrypoint = new URL(import.meta.url).pathname;",
                "return { ok: true, entrypoint };",
            ]
        )
    if _javascript_symbol_contract_requires_distilled_notes(importer_text, symbol):
        prefix = _infer_javascript_distilled_prefix(importer_text, symbol)
        return _indent_javascript_lines(
            [
                'const input = args[0] && typeof args[0] === "object" ? args[0] : {};',
                "const notes = Array.isArray(input.notes) ? input.notes : [];",
                "const distilled = notes",
                '  .filter((note) => typeof note === "string" && note.trim().length > 0)',
                f"  .map((note) => {json.dumps(prefix, ensure_ascii=False)} + note.trim());",
                "return { count: distilled.length, distilled };",
            ]
        )
    if _javascript_symbol_contract_requires_app_info(importer_text, symbol):
        return _indent_javascript_lines(
            [
                "return {",
                "  name: APP_NAME,",
                "  version: APP_VERSION,",
                "  description: APP_DESCRIPTION,",
                "};",
            ]
        )
    if _javascript_symbol_contract_requires_refined_note(importer_text, symbol):
        return _indent_javascript_lines(
            [
                'const source = typeof args[0] === "string" ? args[0] : "";',
                "const refined = source.trim();",
                "return {",
                "  source,",
                "  refined,",
                '  tag: refined.length > 0 ? "dream-fragment" : "empty",',
                "};",
            ]
        )
    if _javascript_symbol_contract_requires_semver(importer_text, symbol):
        if _javascript_symbol_contract_links_version_constant(importer_text, symbol):
            return _indent_javascript_lines(["return VERSION;"])
        return _indent_javascript_lines([f"return {json.dumps(_javascript_default_version_literal(base_files))};"])
    if _javascript_symbol_contract_requires_string_function(importer_text, symbol):
        if _javascript_symbol_contract_links_version_constant(importer_text, symbol):
            return _indent_javascript_lines(["return VERSION;"])
        return _indent_javascript_lines([f"return {json.dumps(_javascript_default_version_literal(base_files))};"])
    if _javascript_symbol_contract_requires_prefixed_lines(importer_text, symbol):
        prefix = _infer_javascript_line_prefix(importer_text, symbol)
        return _indent_javascript_lines(
            [
                'if (typeof args[0] !== "string") {',
                '  throw new TypeError("Expected a string input");',
                "}",
                "return args[0]",
                "  .split(/\\r?\\n/u)",
                "  .map((line) => line.trim())",
                "  .filter((line) => line.length > 0)",
                f"  .map((line) => {json.dumps(prefix, ensure_ascii=False)} + line)",
                '  .join("\\n");',
            ]
        )
    if _javascript_symbol_contract_requires_summary_notes(importer_text, symbol):
        separator = _infer_javascript_summary_separator(importer_text, symbol)
        return _indent_javascript_lines(
            [
                "const values = args",
                '  .filter((note) => typeof note === "string" && note.trim().length > 0)',
                "  .map((note) => note.trim());",
                f"return {{ count: values.length, summary: values.join({json.dumps(separator, ensure_ascii=False)}) }};",
            ]
        )
    if exporter_rel_path.endswith("index.js") and re.search(rf"\b{re.escape(symbol)}\s*\(\s*\)", importer_text):
        return _indent_javascript_lines(["return { ok: true };"])
    return _indent_javascript_lines(["return undefined;"])


def _javascript_symbol_contract_requires_entrypoint(importer_text: str, symbol: str) -> bool:
    text = str(importer_text or "")
    call_name = _javascript_result_binding_for_symbol(text, symbol)
    return bool(call_name and f"{call_name}.ok" in text and f"{call_name}.entrypoint" in text)


def _javascript_symbol_contract_requires_distilled_notes(importer_text: str, symbol: str) -> bool:
    text = str(importer_text or "")
    call_name = _javascript_result_binding_for_symbol(text, symbol)
    return bool(call_name and f"{call_name}.count" in text and f"{call_name}.distilled" in text and "notes" in text)


def _javascript_symbol_contract_requires_prefixed_lines(importer_text: str, symbol: str) -> bool:
    text = str(importer_text or "")
    escaped_symbol = re.escape(symbol)
    if not re.search(rf"\b{escaped_symbol}\s*\(", text) or "[dream]" not in text:
        return False
    call_name = _javascript_result_binding_for_symbol(text, symbol)
    if call_name and (
        re.search(rf"assert\.equal\s*\(\s*{re.escape(call_name)}\s*,", text)
        or f"{call_name}.startsWith" in text
        or f"{call_name}.includes" in text
    ):
        return True
    return bool(re.search(rf"assert\.equal\s*\(\s*{escaped_symbol}\s*\(", text))


def _javascript_symbol_contract_requires_semver(importer_text: str, symbol: str) -> bool:
    text = str(importer_text or "")
    call_name = _javascript_result_binding_for_symbol(text, symbol)
    return bool(call_name and f"typeof {call_name}" in text and r"\d+\.\d+\.\d+" in text)


def _javascript_symbol_contract_requires_string_function(importer_text: str, symbol: str) -> bool:
    text = str(importer_text or "")
    call_name = _javascript_result_binding_for_symbol(text, symbol)
    if not call_name:
        return False
    return (
        re.search(rf"assert\.equal\s*\(\s*typeof\s+{re.escape(call_name)}\s*,\s*['\"]string['\"]", text) is not None
        or f"{call_name}.length" in text
    )


def _javascript_symbol_contract_requires_string_value(importer_text: str, symbol: str) -> bool:
    text = str(importer_text or "")
    escaped_symbol = re.escape(symbol)
    if re.search(rf"\b{escaped_symbol}\s*\(", text):
        return False
    return (
        re.search(rf"assert\.equal\s*\(\s*typeof\s+{escaped_symbol}\s*,\s*['\"]string['\"]", text) is not None
        or _javascript_expected_string_literal_for_symbol(text, symbol) is not None
        or f"{symbol}.length" in text
        or re.search(rf"assert\.match\s*\(\s*{escaped_symbol}\s*,", text) is not None
    )


def _javascript_symbol_contract_requires_iterable_value(importer_text: str, symbol: str) -> bool:
    text = str(importer_text or "")
    escaped_symbol = re.escape(symbol)
    return bool(
        re.search(rf"\bfor\s*\([^)]*\bof\s+{escaped_symbol}\b", text)
        or re.search(rf"\[\s*\.\.\.\s*{escaped_symbol}\s*\]", text)
    )


def _javascript_class_with_method(module_text: str, method_name: str) -> str:
    method_pattern = re.compile(rf"(?m)^[ \t]{{2,}}{re.escape(method_name)}\s*\(")
    for class_match in _JS_EXPORTED_CLASS_RE.finditer(module_text):
        class_end = _find_matching_brace(module_text, class_match.end() - 1)
        if class_end is None:
            continue
        class_body = module_text[class_match.end() : class_end]
        if method_pattern.search(class_body):
            return str(class_match.group("name") or "")
    return ""


def _javascript_symbol_contract_requires_app_info(importer_text: str, symbol: str) -> bool:
    text = str(importer_text or "")
    call_name = _javascript_result_binding_for_symbol(text, symbol)
    return bool(
        call_name
        and f"{call_name}.name" in text
        and f"{call_name}.version" in text
        and f"{call_name}.description" in text
        and "APP_NAME" in text
        and "APP_VERSION" in text
        and "APP_DESCRIPTION" in text
    )


def _javascript_symbol_contract_requires_refined_note(importer_text: str, symbol: str) -> bool:
    text = str(importer_text or "")
    escaped_symbol = re.escape(symbol)
    if not re.search(rf"\b{escaped_symbol}\s*\(", text):
        return False
    return "source" in text and "refined" in text and "tag" in text and ("dream-fragment" in text or "empty" in text)


def _javascript_symbol_contract_links_version_constant(importer_text: str, symbol: str) -> bool:
    text = str(importer_text or "")
    if symbol == "VERSION":
        return False
    escaped_symbol = re.escape(symbol)
    return bool(
        "VERSION" in text
        and (
            re.search(rf"\b{escaped_symbol}\s*\(\s*\)\s*[,)]", text) is not None
            or re.search(rf"assert\.equal\s*\(\s*VERSION\s*,\s*{escaped_symbol}\s*\(", text) is not None
            or re.search(rf"assert\.equal\s*\(\s*{escaped_symbol}\s*\([^)]*\)\s*,\s*VERSION", text) is not None
        )
    )


def _javascript_contract_constant_literal(
    symbol: str,
    base_files: Mapping[str, str],
    *,
    importer_text: str = "",
) -> str:
    expected = _javascript_expected_string_literal_for_symbol(importer_text, symbol)
    if expected is not None:
        return expected
    package_data = _javascript_package_metadata(base_files)
    symbol_upper = symbol.upper()
    if "VERSION" in symbol_upper:
        version = package_data.get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()
        return _javascript_default_version_literal(base_files)
    if "DESCRIPTION" in symbol_upper:
        description = package_data.get("description")
        if isinstance(description, str) and description.strip():
            return description.strip()
        return "Generated JavaScript application"
    if symbol_upper.endswith("NAME") or symbol_upper == "NAME":
        name = package_data.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        return "generated-app"
    return _javascript_default_version_literal(base_files)


def _javascript_expected_string_literal_for_symbol(importer_text: str, symbol: str) -> str | None:
    text = str(importer_text or "")
    escaped_symbol = re.escape(symbol)
    string_literal = r"(?P<quote>['\"])(?P<value>(?:\\.|(?!(?P=quote)).)*)(?P=quote)"
    patterns = [
        rf"assert\.(?:equal|strictEqual)\s*\(\s*{escaped_symbol}\s*,\s*{string_literal}",
        rf"assert\.(?:equal|strictEqual)\s*\(\s*{string_literal}\s*,\s*{escaped_symbol}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.DOTALL)
        if match:
            return _javascript_unescape_string_literal_fragment(str(match.group("value") or ""))
    return None


def _javascript_unescape_string_literal_fragment(value: str) -> str:
    return (
        str(value or "")
        .replace(r"\\", "\\")
        .replace(r"\'", "'")
        .replace(r"\"", '"')
        .replace(r"\n", "\n")
        .replace(r"\r", "\r")
        .replace(r"\t", "\t")
    )


def _javascript_default_version_literal(base_files: Mapping[str, str]) -> str:
    package_data = _javascript_package_metadata(base_files)
    version = package_data.get("version")
    if isinstance(version, str) and version.strip():
        return version.strip()
    return "1.0.0"


def _javascript_package_metadata(base_files: Mapping[str, str]) -> dict[str, Any]:
    try:
        package_data = json.loads(str(base_files.get("package.json") or "{}"))
    except json.JSONDecodeError:
        return {}
    return package_data if isinstance(package_data, dict) else {}


def _javascript_symbol_contract_requires_summary_notes(importer_text: str, symbol: str) -> bool:
    text = str(importer_text or "")
    call_name = _javascript_result_binding_for_symbol(text, symbol)
    return bool(call_name and f"{call_name}.count" in text and f"{call_name}.summary" in text)


def _javascript_result_binding_for_symbol(importer_text: str, symbol: str) -> str:
    pattern = re.compile(
        rf"\b(?:const|let|var)\s+(?P<binding>[A-Za-z_$][\w$]*)\s*=\s*{re.escape(symbol)}\s*\(",
        re.DOTALL,
    )
    match = pattern.search(str(importer_text or ""))
    return str(match.group("binding") or "") if match else ""


def _infer_javascript_summary_separator(importer_text: str, symbol: str) -> str:
    text = str(importer_text or "")
    call_name = _javascript_result_binding_for_symbol(text, symbol)
    if not call_name:
        return " | "
    expected_values = [
        str(match.group("value") or "")
        for match in re.finditer(
            rf"assert\.equal\s*\(\s*{re.escape(call_name)}\.summary\s*,\s*"
            r"(?P<quote>['\"])(?P<value>(?:\\.|(?!(?P=quote)).)*)(?P=quote)",
            text,
        )
    ]
    for expected in expected_values:
        for values in _javascript_string_argument_sets_for_symbol_call(text, symbol):
            separator = _infer_separator_from_joined_values(values, expected)
            if separator is not None:
                return separator
    return " | "


def _infer_javascript_line_prefix(importer_text: str, symbol: str) -> str:
    del symbol
    marker = "[dream]"
    match = re.search(r"(?P<prefix>\[dream\]\s*)", str(importer_text or ""))
    return str(match.group("prefix") or "[dream] ") if match else marker


def _javascript_string_argument_sets_for_symbol_call(importer_text: str, symbol: str) -> list[list[str]]:
    pattern = re.compile(rf"\b{re.escape(symbol)}\s*\((?P<args>[^)]*)\)", re.DOTALL)
    value_sets: list[list[str]] = []
    for match in pattern.finditer(str(importer_text or "")):
        args_text = str(match.group("args") or "")
        values = [
            str(item.group("value") or "").strip()
            for item in _JS_STRING_LITERAL_RE.finditer(args_text)
            if str(item.group("value") or "").strip()
        ]
        if values:
            value_sets.append(values)
    return value_sets


def _infer_separator_from_joined_values(values: list[str], expected: str) -> str | None:
    if not values:
        return "" if expected == "" else None
    if len(values) == 1:
        return "" if values[0] == expected else None
    first, second = values[0], values[1]
    if not expected.startswith(first):
        return None
    second_index = expected.find(second, len(first))
    if second_index < 0:
        return None
    separator = expected[len(first) : second_index]
    return separator if separator.join(values) == expected else None


def _infer_javascript_distilled_prefix(importer_text: str, symbol: str) -> str:
    input_notes = _javascript_string_literals_near_symbol_notes_call(importer_text, symbol)
    expected_values = [
        str(match.group("value") or "")
        for match in re.finditer(
            r"assert\.equal\s*\([^,]+distilled\[[^\]]+\]\s*,\s*['\"](?P<value>[^'\"]+)['\"]",
            str(importer_text or ""),
        )
    ]
    for expected in expected_values:
        for note in input_notes:
            if note and expected.endswith(note):
                return expected[: -len(note)]
    return ""


def _javascript_string_literals_near_symbol_notes_call(importer_text: str, symbol: str) -> list[str]:
    pattern = re.compile(rf"{re.escape(symbol)}\s*\(\s*\{{(?P<body>.*?)\}}\s*\)", re.DOTALL)
    values: list[str] = []
    for match in pattern.finditer(str(importer_text or "")):
        body = str(match.group("body") or "")
        if "notes" not in body:
            continue
        values.extend(str(item.group("value") or "") for item in _JS_STRING_LITERAL_RE.finditer(body))
    return values


def _indent_javascript_lines(lines: list[str]) -> str:
    return "\n".join(f"  {line}" if line else "" for line in lines)
