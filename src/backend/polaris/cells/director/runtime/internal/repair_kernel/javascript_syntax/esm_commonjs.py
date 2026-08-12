"""esm_commonjs domain for JavaScript/Node syntax repairs."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from ..contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ._shared import (
    _first_runtime_file,
    _javascript_module_exports_symbol,
    _missing_export_targets,
    _normalize_base_files,
    _normalize_repair_path,
    _parse_package_json,
    _resolve_js_module,
)
from .constants import (
    _COMMONJS_MODULE_EXPORTS_DEFAULT_RE,
    _COMMONJS_MODULE_EXPORTS_OBJECT_BLOCK_RE,
    _COMMONJS_MODULE_EXPORTS_OBJECT_RE,
    _COMMONJS_MODULE_EXPORTS_PROPERTY_BLOCK_RE,
    _COMMONJS_MODULE_EXPORTS_PROPERTY_RE,
    _COMMONJS_MODULE_EXPORTS_VALUE_BLOCK_RE,
    _COMMONJS_REQUIRE_BINDING_RE,
    _COMMONJS_REQUIRE_DESTRUCTURING_RE,
    _COMMONJS_REQUIRE_MAIN_GUARD_BLOCK_RE,
    _COMMONJS_REQUIRE_MAIN_GUARD_RE,
    _JS_IDENTIFIER_RE,
    _MISSING_NAMED_EXPORT_RE,
    _ORPHAN_COMMONJS_EXPORTS_LINE_RE,
    JAVASCRIPT_ESM_COMMONJS_ENTRYPOINT_SOURCE_TOOL,
)


def build_javascript_esm_commonjs_entrypoint_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Rewrite narrow CommonJS entrypoint lines when package.json is ESM."""

    normalized_base = _normalize_base_files(base_files)
    package_payload = _parse_package_json(normalized_base.get("package.json", ""))
    if not package_payload or package_payload.get("type") != "module":
        return None
    if not any(_is_esm_commonjs_diagnostic(diagnostic) for diagnostic in diagnostics):
        return None
    candidates = _esm_commonjs_entrypoint_candidates(normalized_base, package_payload, diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics = tuple(diagnostic for diagnostic in diagnostics if _is_esm_commonjs_diagnostic(diagnostic))
    for path in candidates:
        text = normalized_base.get(path)
        if text is None:
            continue
        path_operations, repaired = _commonjs_to_esm_operations(
            path=path,
            text=text,
            base_files=normalized_base,
            diagnostics=matched_diagnostics,
        )
        if not path_operations:
            continue
        if "require(" in repaired or "module.exports" in repaired or "require.main" in repaired:
            continue
        operations.extend(path_operations)
    if not operations:
        return None
    return RepairPlan(
        rule_id="javascript.commonjs_esm_entrypoint",
        source_tool=JAVASCRIPT_ESM_COMMONJS_ENTRYPOINT_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=matched_diagnostics,
        mode=mode,
        risk_level="medium",
        priority=1,
        metadata={
            "runtime_plan_scope": "line_based_commonjs_require_and_module_exports_only",
            "unsafe_cases_fail_closed": True,
        },
    )


def _is_esm_commonjs_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    raw = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    return (
        diagnostic.code == "javascript_module_error"
        and (
            "require is not defined" in raw
            or "module is not defined" in raw
            or _is_missing_default_export_diagnostic(diagnostic)
        )
    ) or (
        "commonjs entrypoint in esm package" in raw
        or ("uses commonjs runtime syntax" in raw and "package manifest declares type=module" in raw)
    )


def _is_missing_default_export_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    raw = f"{diagnostic.message}\n{diagnostic.raw}"
    match = _MISSING_NAMED_EXPORT_RE.search(raw)
    return bool(match and str(match.group("symbol") or "").strip() == "default")


def _esm_commonjs_entrypoint_candidates(
    base_files: Mapping[str, str],
    package_payload: Mapping[str, Any],
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[str, ...]:
    candidates: list[str] = []
    for diagnostic in diagnostics:
        if _is_missing_default_export_diagnostic(diagnostic):
            candidates.extend(
                target["exporter"]
                for target in _missing_export_targets(diagnostic, base_files)
                if str(target.get("symbol") or "").strip() == "default"
            )
        runtime_file = _first_runtime_file(str(diagnostic.raw or diagnostic.message or ""), base_files)
        if runtime_file:
            candidates.append(runtime_file)
    main = _normalize_repair_path(str(package_payload.get("main") or ""))
    if main:
        candidates.append(main)
    scripts = package_payload.get("scripts")
    if isinstance(scripts, dict):
        for script_name in ("start", "serve", "dev"):
            script = str(scripts.get(script_name) or "")
            match = re.search(r"(?:^|\s)node\s+(?P<path>[A-Za-z0-9_./-]+\.js)\b", script)
            if match:
                candidates.append(match.group("path"))
    return tuple(path for path in dict.fromkeys(_normalize_repair_path(candidate) for candidate in candidates) if path)


def _commonjs_to_esm_operations(
    *,
    path: str,
    text: str,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[tuple[RepairOperation, ...], str]:
    operations: list[RepairOperation] = []
    repaired_lines: list[str] = []
    namespace_bindings = _commonjs_namespace_require_bindings(text)
    offset = 0
    for line in text.splitlines(keepends=True):
        line_body = line.removesuffix("\n")
        replacement = _commonjs_line_replacement(
            line_body,
            path=path,
            base_files=base_files,
            namespace_bindings=namespace_bindings,
        )
        if replacement is None:
            replacement = _commonjs_namespace_constructor_replacement(line_body, namespace_bindings)
        if replacement is None:
            repaired_lines.append(line)
            offset += len(line)
            continue
        newline = "\n" if line.endswith("\n") else ""
        repaired = replacement + newline
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=offset,
                span_end=offset + len(line),
                expected=line,
                replacement=repaired,
                before_hash=sha256_text(text),
                metadata={
                    "repair_kind": (
                        "javascript_commonjs_namespace_constructor"
                        if line_body != replacement and "new " in line_body
                        else "javascript_commonjs_esm_entrypoint"
                    ),
                    "diagnostic_ids": [diagnostic.diagnostic_id for diagnostic in diagnostics],
                    "edit_file_preferred": True,
                },
            )
        )
        repaired_lines.append(repaired)
        offset += len(line)
    repaired = "".join(repaired_lines)
    if _has_commonjs_runtime_residue(repaired):
        whole_file_operation = _commonjs_to_esm_whole_file_operation(
            path=path,
            text=text,
            base_files=base_files,
            diagnostics=diagnostics,
        )
        if whole_file_operation is not None:
            return (whole_file_operation,), str(whole_file_operation.replacement or "")
    return tuple(operations), repaired


def _has_commonjs_runtime_residue(text: str) -> bool:
    return "require(" in text or "module.exports" in text or "require.main" in text


def _commonjs_to_esm_whole_file_operation(
    *,
    path: str,
    text: str,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> RepairOperation | None:
    repaired = _rewrite_commonjs_to_esm_whole_file(
        path=path,
        text=text,
        base_files=base_files,
    )
    if not repaired or repaired == text or _has_commonjs_runtime_residue(repaired):
        return None
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=0,
        span_end=len(text),
        expected=text,
        replacement=repaired,
        before_hash=sha256_text(text),
        metadata={
            "repair_kind": "javascript_commonjs_esm_entrypoint_whole_file",
            "diagnostic_ids": [diagnostic.diagnostic_id for diagnostic in diagnostics],
            "edit_file_preferred": True,
            "whole_file_fallback": True,
            "runtime_plan_scope": "commonjs_entrypoint_block_rewrite",
        },
    )


def _rewrite_commonjs_to_esm_whole_file(
    *,
    path: str,
    text: str,
    base_files: Mapping[str, str],
) -> str:
    source = str(text or "")
    import_lines: list[str] = []
    body_lines: list[str] = []
    namespace_bindings = _commonjs_namespace_require_bindings(source)

    for line in source.splitlines():
        stripped = line.strip()
        if stripped in {'"use strict";', "'use strict';"}:
            continue
        replacement = _commonjs_line_replacement(
            line,
            path=path,
            base_files=base_files,
            namespace_bindings=namespace_bindings,
        )
        if replacement is not None and replacement.strip().startswith("import "):
            import_lines.append(replacement)
            continue
        body_lines.append(line if replacement is None else replacement)

    body = "\n".join(body_lines)
    for binding in sorted(namespace_bindings, key=len, reverse=True):
        escaped = re.escape(binding)
        body = re.sub(rf"\bnew\s+{escaped}\s*\(", f"new {binding}.{binding}(", body)
    body = _COMMONJS_REQUIRE_MAIN_GUARD_BLOCK_RE.sub(_replace_commonjs_main_guard_block, body)
    body = _rewrite_commonjs_module_exports_blocks(body)
    parts = [part for part in ("\n".join(dict.fromkeys(import_lines)).strip(), body.strip()) if part]
    if not parts:
        return ""
    return "\n\n".join(parts).rstrip() + "\n"


def _replace_commonjs_main_guard_block(match: re.Match[str]) -> str:
    indent = str(match.group("indent") or "")
    body = str(match.group("body") or "").strip()
    if not body:
        return ""
    rendered_body = "\n".join(f"{indent}  {line.strip()}" for line in body.splitlines() if line.strip())
    return f"{indent}if (import.meta.url === `file://${{process.argv[1]}}`) {{\n{rendered_body}\n{indent}}}"


def _rewrite_commonjs_module_exports_blocks(body: str) -> str:
    exported_names: set[str] = set()
    default_exported = False

    def replace_object(match: re.Match[str]) -> str:
        nonlocal default_exported
        names = _parse_commonjs_module_exports_object_names(str(match.group("body") or ""))
        if not names:
            return ""
        exported_names.update(names)
        default_exported = True
        rendered = ", ".join(names)
        return (
            f"{match.group('indent')}export {{ {rendered} }};\n{match.group('indent')}export default {{ {rendered} }};"
        )

    def replace_value(match: re.Match[str]) -> str:
        nonlocal default_exported
        default_exported = True
        return f"{match.group('indent')}export default {match.group('value')};"

    def replace_property(match: re.Match[str]) -> str:
        nonlocal default_exported
        indent = str(match.group("indent") or "")
        name = str(match.group("name") or "")
        value = str(match.group("value") or "")
        if not name or not value:
            return ""
        if name == "default":
            if default_exported:
                return ""
            default_exported = True
        elif name in exported_names:
            return ""
        else:
            exported_names.add(name)
        if match.group("literal") is not None:
            return f"{indent}export const {name} = {value};"
        if name == value:
            return f"{indent}export {{ {name} }};"
        return f"{indent}export {{ {value} as {name} }};"

    repaired = _COMMONJS_MODULE_EXPORTS_OBJECT_BLOCK_RE.sub(replace_object, body)
    repaired = _COMMONJS_MODULE_EXPORTS_VALUE_BLOCK_RE.sub(replace_value, repaired)
    repaired = _COMMONJS_MODULE_EXPORTS_PROPERTY_BLOCK_RE.sub(replace_property, repaired)
    return _ORPHAN_COMMONJS_EXPORTS_LINE_RE.sub("", repaired)


def _parse_commonjs_module_exports_object_names(body: str) -> list[str]:
    names: list[str] = []
    for raw_item in str(body or "").split(","):
        token = raw_item.strip()
        if not token:
            continue
        name = token.split(":", 1)[0].strip()
        if _JS_IDENTIFIER_RE.match(name) and name not in names:
            names.append(name)
    return names


def _commonjs_line_replacement(
    line: str,
    *,
    path: str,
    base_files: Mapping[str, str],
    namespace_bindings: frozenset[str],
) -> str | None:
    stripped = line.strip()
    if stripped in {'"use strict";', "'use strict';"}:
        return ""
    match = _COMMONJS_REQUIRE_BINDING_RE.match(line)
    if match:
        binding = str(match.group("binding") or "").strip()
        raw_specifier = str(match.group("specifier") or "")
        specifier = _esm_import_specifier(raw_specifier)
        if binding in namespace_bindings:
            return f'{match.group("indent")}import * as {binding} from "{specifier}";'
        if _commonjs_binding_has_named_esm_export(
            base_files,
            importer=path,
            module_ref=raw_specifier,
            binding=binding,
        ):
            return f'{match.group("indent")}import {{ {binding} }} from "{specifier}";'
        return f'{match.group("indent")}import {match.group("binding")} from "{specifier}";'
    match = _COMMONJS_REQUIRE_DESTRUCTURING_RE.match(line)
    if match:
        bindings = " ".join(str(match.group("bindings") or "").strip().split())
        if not bindings:
            return None
        specifier = _esm_import_specifier(str(match.group("specifier") or ""))
        return f'{match.group("indent")}import {{ {bindings} }} from "{specifier}";'
    match = _COMMONJS_MODULE_EXPORTS_DEFAULT_RE.match(line)
    if match:
        return f"{match.group('indent')}export default {match.group('value')};"
    match = _COMMONJS_MODULE_EXPORTS_OBJECT_RE.match(line)
    if match:
        bindings = " ".join(str(match.group("bindings") or "").strip().split())
        if not bindings:
            return None
        return f"{match.group('indent')}export {{ {bindings} }};"
    match = _COMMONJS_MODULE_EXPORTS_PROPERTY_RE.match(line)
    if match:
        name = str(match.group("name") or "").strip()
        value = str(match.group("value") or "").strip()
        if not _JS_IDENTIFIER_RE.match(name) or not value:
            return None
        if _JS_IDENTIFIER_RE.match(value):
            if value == name:
                return f"{match.group('indent')}export {{ {name} }};"
            return f"{match.group('indent')}export {{ {value} as {name} }};"
        return f"{match.group('indent')}export const {name} = {value};"
    match = _COMMONJS_REQUIRE_MAIN_GUARD_RE.match(line)
    if match:
        call = str(match.group("call") or "").strip()
        return f"{match.group('indent')}if (import.meta.url === `file://${{process.argv[1]}}`) {{ {call} }}"
    return None


def _commonjs_namespace_require_bindings(text: str) -> frozenset[str]:
    bindings: set[str] = set()
    for line in str(text or "").splitlines():
        match = _COMMONJS_REQUIRE_BINDING_RE.match(line)
        if not match:
            continue
        binding = str(match.group("binding") or "").strip()
        if not _JS_IDENTIFIER_RE.match(binding):
            continue
        if _commonjs_binding_used_as_namespace(text, binding):
            bindings.add(binding)
    return frozenset(bindings)


def _commonjs_binding_used_as_namespace(text: str, binding: str) -> bool:
    escaped = re.escape(binding)
    return bool(
        re.search(rf"(?m)^\s*(?:const|let|var)\s+\{{[^}}]+\}}\s*=\s*{escaped}\s*;?\s*$", text)
        or re.search(rf"\b{escaped}\.[A-Za-z_$][\w$]*\b", text)
    )


def _commonjs_namespace_constructor_replacement(line: str, namespace_bindings: frozenset[str]) -> str | None:
    updated = line
    for binding in sorted(namespace_bindings, key=len, reverse=True):
        escaped = re.escape(binding)
        updated = re.sub(rf"\bnew\s+{escaped}\s*\(", f"new {binding}.{binding}(", updated)
    return updated if updated != line else None


def _esm_import_specifier(specifier: str) -> str:
    normalized = str(specifier or "").strip().replace("\\", "/")
    if not normalized.startswith("."):
        return normalized
    if PurePosixPath(normalized).suffix:
        return normalized
    return f"{normalized}.js"


def _commonjs_binding_has_named_esm_export(
    base_files: Mapping[str, str],
    *,
    importer: str,
    module_ref: str,
    binding: str,
) -> bool:
    if not _JS_IDENTIFIER_RE.match(binding):
        return False
    exporter = _resolve_js_module(base_files, importer, module_ref)
    if not exporter:
        return False
    return _javascript_module_exports_symbol(str(base_files.get(exporter) or ""), binding)
