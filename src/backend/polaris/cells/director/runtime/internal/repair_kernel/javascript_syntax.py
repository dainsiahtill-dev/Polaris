"""Canonical JavaScript/Node repair rules for Director Runtime."""

from __future__ import annotations

import json
import posixpath
import re
import shlex
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from .contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text

JAVASCRIPT_ESM_COMMONJS_ENTRYPOINT_SOURCE_TOOL = "deterministic_javascript_esm_commonjs_entrypoint_repair"
JAVASCRIPT_MISSING_EXPORT_SOURCE_TOOL = "deterministic_javascript_missing_export_repair"
JAVASCRIPT_MISSING_METHOD_RUNTIME_SOURCE_TOOL = "deterministic_javascript_missing_method_runtime_repair"
JAVASCRIPT_DOM_GLOBAL_RUNTIME_SOURCE_TOOL = "deterministic_javascript_dom_global_runtime_guard_repair"
JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL = "deterministic_javascript_test_missing_target_repair"
NODE_TEST_SCRIPT_CONTRACT_SOURCE_TOOL = "deterministic_node_test_script_contract_repair"
NPM_SCRIPT_CONTRACT_SOURCE_TOOL = "deterministic_npm_script_contract_repair"
TYPESCRIPT_LOCAL_JS_IMPORT_SOURCE_TOOL = "deterministic_typescript_local_js_import_repair"

_MISSING_NPM_SCRIPT_ENTRYPOINT_RE = re.compile(
    r"npm package manifest script '([^']+)' references missing local entrypoint '([^']+)'",
    re.IGNORECASE,
)
_MISSING_NPM_SCRIPT_ENTRYPOINT_GATE_RE = re.compile(
    r"script '([^']+)' references missing local entrypoint:\s*(\S+)",
    re.IGNORECASE,
)
_NODE_CANNOT_FIND_MODULE_DIST_RE = re.compile(
    r"Cannot find module ['\"](?P<path>[^'\"]*/dist/[^'\"]+\.js)['\"]",
    re.IGNORECASE,
)
_LOCAL_JS_MODULE_NOT_FOUND_RE = re.compile(
    r"Cannot find module ['\"](?P<specifier>\.{1,2}/[^'\"]+\.js)['\"]",
    re.IGNORECASE,
)
_LOCAL_JS_IMPORT_SPECIFIER_RE = re.compile(
    r"(?P<prefix>\b(?:from|import)\s*(?:\(\s*)?['\"])(?P<specifier>\.{1,2}/[^'\"]+\.js)(?P<suffix>['\"])",
)
_HTTP_SERVER_FIXED_PORT_RE = re.compile(
    r"(?P<flag>\s(?:-p|--port)\s+)(?P<port>\d{2,5})(?=$|\s)",
    re.IGNORECASE,
)
_RECURSIVE_NPM_SCRIPT_RE = re.compile(
    r"npm package manifest script '([^']+)' recursively invokes itself",
    re.IGNORECASE,
)
_PLACEHOLDER_NPM_SCRIPT_RE = re.compile(
    r"npm package manifest script '([^']+)' is a placeholder command",
    re.IGNORECASE,
)
_PYTHON_COMMAND_NPM_SCRIPT_RE = re.compile(
    r"npm package manifest contains Python command in script '([^']+)'",
    re.IGNORECASE,
)
_PYTHON_COMMAND_TOKEN_RE = re.compile(r"(?<![\w.-])python(?:3|[0-9]+(?:\.[0-9]+)?)?(?![\w.-])", re.IGNORECASE)
_UNRESOLVED_IMPORT_SYMBOL_RE = re.compile(
    r"unresolved (?:import )?symbol ['\"](?P<symbol>[^'\"]+)['\"] "
    r"from ['\"](?P<module>[^'\"]+)['\"] in (?P<path>\S+)",
    re.IGNORECASE,
)
_MISSING_NAMED_EXPORT_RE = re.compile(
    r"The requested module\s+['\"]?(?P<module>\.[^'\"\s]+)['\"]?\s+does not provide an export named\s+"
    r"['\"]?(?P<symbol>[A-Za-z_$][\w$]*)['\"]?",
)
_JS_NAMED_IMPORT_RE = re.compile(r"\bimport\s*\{\s*(?P<symbols>[^}]+)\s*\}\s*from\s*['\"](?P<specifier>\.[^'\"]+)['\"]")
_NODE_SCRIPT_SEGMENT_RE = re.compile(r"\s*(?:&&|\|\||[;|])\s*")
_NODE_FLAGS_WITH_VALUE = frozenset(
    {
        "--experimental-loader",
        "--import",
        "--loader",
        "--require",
        "--test-name-pattern",
        "--test-reporter",
        "--test-reporter-destination",
        "-r",
    }
)
_NODE_FLAGS_WITH_VALUE_PREFIXES = ("--experimental-loader=", "--import=", "--loader=", "--require=")
_JS_RUNTIME_FILE_RE = re.compile(r"(?:file://)?(?P<path>/[^\s:]+\.js):(?P<line>\d+)")
_JS_MISSING_METHOD_RUNTIME_RE = re.compile(
    r"(?P<file>(?:file://)?/[^\s:]+\.js):(?P<line>\d+).*?"
    r"TypeError:\s+(?P<object>[A-Za-z_$][\w$]*)\.(?P<member>[A-Za-z_$][\w$]*)\s+is not a function",
    re.DOTALL,
)
_JS_MISSING_METHOD_RUNTIME_STACK_RE = re.compile(
    r"TypeError:\s+(?P<object>[A-Za-z_$][\w$]*)\.(?P<member>[A-Za-z_$][\w$]*)\s+is not a function"
    r".*?\((?:file://)?(?P<file>/[^\s:]+\.js):(?P<line>\d+):\d+\)",
    re.DOTALL,
)
_JS_CONSTRUCTOR_STRING_CONTRACT_RE = re.compile(
    r"(?P<class_name>[A-Za-z_$][\w$]*)\.(?P<field>[A-Za-z_$][\w$]*)\s+must be a non-empty string"
    r".*?\bnew\s+(?P=class_name)\s*\((?:file://)?(?P<file>/[^\s:]+\.js):(?P<line>\d+)",
    re.DOTALL,
)
_JS_CONSTRUCTOR_REQUIRES_FIELD_RE = re.compile(
    r"(?P<class_name>[A-Za-z_$][\w$]*)\s+requires\s+(?:an?\s+)?(?P<field>[A-Za-z_$][\w$]*)"
    r".*?\bnew\s+(?P=class_name)\s*\((?:file://)?(?P<file>/[^\s:]+\.js):(?P<line>\d+)",
    re.DOTALL,
)
_JS_DOM_GLOBAL_RUNTIME_RE = re.compile(
    r"(?P<file>(?:file://)?/[^\s:]+\.js):(?P<line>\d+).*?"
    r"ReferenceError:\s+(?P<global>document|window)\s+is not defined",
    re.IGNORECASE | re.DOTALL,
)
_BROWSER_BOOTSTRAP_CALL_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)(?P<call>(?:whenReady|bootstrap|initApp|startApp)\s*\(\s*\)\s*;)\s*$"
)
_COMMONJS_REQUIRE_BINDING_RE = re.compile(
    r"^(?P<indent>\s*)(?:const|let|var)\s+(?P<binding>[A-Za-z_$][\w$]*)\s*=\s*"
    r"require\((?P<quote>['\"])(?P<specifier>[^'\"]+)(?P=quote)\)\s*;?\s*$"
)
_COMMONJS_REQUIRE_DESTRUCTURING_RE = re.compile(
    r"^(?P<indent>\s*)(?:const|let|var)\s+\{(?P<bindings>[^}]+)\}\s*=\s*"
    r"require\((?P<quote>['\"])(?P<specifier>[^'\"]+)(?P=quote)\)\s*;?\s*$"
)
_COMMONJS_MODULE_EXPORTS_DEFAULT_RE = re.compile(
    r"^(?P<indent>\s*)module\.exports\s*=\s*(?P<value>[A-Za-z_$][\w$]*)\s*;?\s*$"
)
_COMMONJS_MODULE_EXPORTS_OBJECT_RE = re.compile(
    r"^(?P<indent>\s*)module\.exports\s*=\s*\{(?P<bindings>[^}]+)\}\s*;?\s*$"
)
_COMMONJS_MODULE_EXPORTS_PROPERTY_RE = re.compile(
    r"^(?P<indent>\s*)module\.exports\.(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?P<value>.+?)\s*;?\s*$"
)
_COMMONJS_REQUIRE_MAIN_GUARD_RE = re.compile(
    r"^(?P<indent>\s*)if\s*\(\s*require\.main\s*===\s*module\s*\)\s*\{\s*"
    r"(?P<call>[A-Za-z_$][\w$]*\s*\(\s*\)\s*;?)\s*\}\s*$"
)
_COMMONJS_MODULE_EXPORTS_OBJECT_BLOCK_RE = re.compile(
    r"(?m)^(?P<indent>\s*)module\.exports\s*=\s*\{(?P<body>.*?)\}\s*;?\s*$",
    re.DOTALL,
)
_COMMONJS_MODULE_EXPORTS_VALUE_BLOCK_RE = re.compile(
    r"(?m)^(?P<indent>\s*)module\.exports\s*=\s*(?P<value>[A-Za-z_$][\w$]*)\s*;?\s*$"
)
_COMMONJS_MODULE_EXPORTS_PROPERTY_BLOCK_RE = re.compile(
    r"(?m)^(?P<indent>\s*)module\.exports\.(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
    r"(?P<value>[A-Za-z_$][\w$]*|(?P<literal>['\"][^'\"]*['\"]|\d+(?:\.\d+)?|true|false|null))\s*;?\s*$"
)
_COMMONJS_REQUIRE_MAIN_GUARD_BLOCK_RE = re.compile(
    r"(?m)^(?P<indent>\s*)if\s*\(\s*require\.main\s*===\s*module\s*\)\s*\{\s*(?P<body>.*?)\s*\}\s*$",
    re.DOTALL,
)
_ORPHAN_COMMONJS_EXPORTS_LINE_RE = re.compile(r"(?m)^\s*(?:module)?\.exports\s*;\s*$")
_JS_IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][\w$]*$")
_JS_STRING_LITERAL_RE = re.compile(r"(?P<quote>['\"])(?P<value>(?:\\.|(?!(?P=quote)).)*)(?P=quote)")
_JS_DECLARATION_RE_TEMPLATE = (
    r"(?m)^(?P<indent>\s*)(?P<decl>(?:async\s+)?(?:class|function)\s+{symbol}\b|(?:const|let|var)\s+{symbol}\b)"
)
_JS_EXPORTED_CLASS_RE = re.compile(r"(?m)^(?P<indent>\s*)export\s+class\s+(?P<name>[A-Za-z_$][\w$]*)\b[^\n]*\{")
_JS_CLASS_RE_TEMPLATE = r"(?m)^(?P<indent>\s*)(?:export\s+)?class\s+{class_name}\b[^\n]*\{{"
_JS_METHOD_RE = re.compile(r"(?m)^\s{2,}(?P<name>[A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{")
_JS_FUNCTION_START_RE_TEMPLATE = r"(?m)^(?P<prefix>\s*(?:export\s+)?(?:async\s+)?function\s+{symbol}\s*\([^)]*\)\s*)\{{"


def build_npm_script_contract_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build structured JSON operations for safe package script contract repairs."""

    normalized_base = _normalize_base_files(base_files)
    package_text = normalized_base.get("package.json")
    if package_text is None:
        return None
    package_payload = _parse_package_json(package_text)
    if package_payload is None:
        return None
    matched_diagnostics = tuple(
        diagnostic for diagnostic in diagnostics if _is_npm_script_contract_diagnostic(diagnostic)
    )
    if not matched_diagnostics:
        return None

    scripts_raw = package_payload.get("scripts")
    scripts: dict[str, Any] = dict(scripts_raw) if isinstance(scripts_raw, dict) else {}
    updates: dict[tuple[str, ...], str] = {}
    raw_errors = [str(diagnostic.raw or diagnostic.message or "") for diagnostic in matched_diagnostics]
    missing_entrypoints = _missing_entrypoints(raw_errors)
    missing_entrypoints.update(_missing_entrypoints_from_diagnostics(matched_diagnostics))
    has_typescript_context = _has_typescript_context(normalized_base, package_payload)
    has_node_test_runner_contract = _has_node_test_runner_contract_error(raw_errors)

    for script_name in _script_names_for_manifest_issue(
        matched_diagnostics,
        "placeholder_command",
        fallback_names=_placeholder_scripts(raw_errors),
    ):
        replacement = _fallback_script_for_placeholder_script(
            script_name,
            normalized_base,
            package_payload,
            has_typescript_context=has_typescript_context,
        )
        if replacement:
            updates[("scripts", script_name)] = replacement

    for script_name in _python_command_scripts(raw_errors, scripts):
        replacement = _fallback_script_for_python_command_script(
            script_name,
            normalized_base,
            package_payload,
            has_typescript_context=has_typescript_context,
        )
        if replacement:
            updates[("scripts", script_name)] = replacement

    if has_typescript_context:
        for script_name in _script_names_for_manifest_issue(
            matched_diagnostics,
            "recursive_script",
            fallback_names=_recursive_scripts(raw_errors),
        ):
            replacement = _fallback_script_for_recursive_script(script_name, normalized_base, package_payload)
            if replacement:
                updates[("scripts", script_name)] = replacement

    if has_typescript_context and "build" not in scripts:
        compile_script = str(scripts.get("compile") or "").strip()
        updates[("scripts", "build")] = "npm run compile" if compile_script else "tsc"

    for missing_entrypoint in _missing_node_dist_entrypoints(raw_errors):
        replacement_entrypoint = _compiled_typescript_entrypoint_for_missing(
            normalized_base,
            package_payload,
            missing_entrypoint=missing_entrypoint,
        )
        if not replacement_entrypoint or replacement_entrypoint == missing_entrypoint:
            continue
        for script_name, script_value in scripts.items():
            script_text = str(script_value or "")
            if missing_entrypoint in script_text:
                updates[("scripts", str(script_name))] = script_text.replace(missing_entrypoint, replacement_entrypoint)

    if has_node_test_runner_contract:
        updates[("scripts", "test")] = _node_test_runner_script(normalized_base)
    elif has_typescript_context and _has_repairable_test_script_error(raw_errors):
        updates[("scripts", "test")] = (
            _fallback_script_for_recursive_script("test", normalized_base, package_payload) or "npm run build"
        )

    if _has_fixed_port_start_script_error(raw_errors):
        for script_name in ("start", "serve", "dev", "preview"):
            script_text = str(scripts.get(script_name) or "").strip()
            replacement = _http_server_dynamic_port_script(script_text)
            if replacement and replacement != script_text:
                updates[("scripts", script_name)] = replacement

    if has_typescript_context and _has_typescript_source_loader_start_error(raw_errors):
        replacement = _fallback_script_for_recursive_script("start", normalized_base, package_payload)
        if replacement:
            updates[("scripts", "start")] = replacement

    if has_typescript_context and missing_entrypoints.get("verify"):
        updates[("scripts", "verify")] = "npm run build"
        test_script = str(scripts.get("test") or "")
        if "verify" in test_script:
            updates[("scripts", "test")] = "npm run verify"

    if has_typescript_context and missing_entrypoints.get("start"):
        entrypoint = _compiled_typescript_entrypoint(normalized_base, package_payload)
        updates[("scripts", "start")] = f"npm run build && node {entrypoint}" if entrypoint else "npm run build"

    for script_name, _entrypoint in missing_entrypoints.items():
        if script_name in {"test", "start", "verify"}:
            continue
        if has_typescript_context:
            updates[("scripts", script_name)] = _fallback_script_for_missing_entrypoint(script_name)

    if not updates:
        return None

    before_hash = sha256_text(package_text)
    operations = tuple(
        RepairOperation(
            kind="json_set",
            path="package.json",
            json_path=json_path,
            value=value,
            before_hash=before_hash,
            metadata={
                "repair_kind": "npm_script_contract",
                "structured_operation": "json",
                "diagnostic_ids": [diagnostic.diagnostic_id for diagnostic in matched_diagnostics],
            },
        )
        for json_path, value in sorted(updates.items())
    )
    return RepairPlan(
        rule_id="javascript.npm_script_contract",
        source_tool=NPM_SCRIPT_CONTRACT_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={
            "structured_manifest_repair": True,
            "updated_json_paths": [".".join(path) for path in sorted(updates)],
        },
    )


def build_node_test_script_contract_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Replace a narrow over-strict generated Node test script contract."""

    normalized_base = _normalize_base_files(base_files)
    script_path = "scripts/test.mjs"
    script_text = normalized_base.get(script_path)
    if script_text is None or not _is_overstrict_node_test_script_contract(script_text):
        return None
    matched_diagnostics = tuple(
        diagnostic for diagnostic in diagnostics if _is_node_test_script_contract_diagnostic(diagnostic)
    )
    if not matched_diagnostics:
        return None
    repaired = build_substantive_node_test_script()
    if repaired == script_text:
        return None
    return RepairPlan(
        rule_id="javascript.node_test_script_contract",
        source_tool=NODE_TEST_SCRIPT_CONTRACT_SOURCE_TOOL,
        operations=(
            RepairOperation(
                kind="write_file",
                path=script_path,
                content=repaired,
                before_hash=sha256_text(script_text),
                metadata={
                    "repair_kind": "node_test_script_contract",
                    "adapter_transform_migrated": True,
                    "write_file_reason": "node_test_contract_whole_script_replacement",
                    "diagnostic_ids": [diagnostic.diagnostic_id for diagnostic in matched_diagnostics],
                },
            ),
        ),
        diagnostics=matched_diagnostics,
        mode=mode,
        risk_level="medium",
        priority=1,
        metadata={
            "edit_strategy": "whole_file_test_contract_replacement",
            "adapter_transform_migrated": True,
        },
    )


def build_typescript_local_js_import_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Repair local ``.js`` import specifiers that break ts-node/CommonJS source execution."""

    normalized_base = _normalize_base_files(base_files)
    matched_diagnostics = tuple(
        diagnostic for diagnostic in diagnostics if _is_local_js_import_runtime_diagnostic(diagnostic)
    )
    if not matched_diagnostics or not _base_files_use_commonjs_ts_source_runtime(normalized_base):
        return None

    operations: list[RepairOperation] = []
    matched_ids = [diagnostic.diagnostic_id for diagnostic in matched_diagnostics]
    for path, text in sorted(normalized_base.items()):
        if not path.endswith((".ts", ".tsx")) or path.endswith((".d.ts", ".d.tsx")):
            continue
        for match in _LOCAL_JS_IMPORT_SPECIFIER_RE.finditer(text):
            specifier = match.group("specifier")
            repaired_specifier = specifier[:-3]
            if not _local_typescript_import_target_exists(
                importer_path=path,
                specifier_without_js=repaired_specifier,
                base_files=normalized_base,
            ):
                continue
            operations.append(
                RepairOperation(
                    kind="text_replace",
                    path=path,
                    span_start=match.start("specifier"),
                    span_end=match.end("specifier"),
                    expected=specifier,
                    replacement=repaired_specifier,
                    before_hash=sha256_text(text),
                    metadata={
                        "repair_kind": "typescript_local_js_import_extension",
                        "diagnostic_ids": matched_ids,
                        "import_specifier_before": specifier,
                        "import_specifier_after": repaired_specifier,
                        "edit_file_preferred": True,
                    },
                )
            )

    if not operations:
        return None
    return RepairPlan(
        rule_id="typescript.local_js_import_extension",
        source_tool=TYPESCRIPT_LOCAL_JS_IMPORT_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=matched_diagnostics,
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={
            "edit_strategy": "span_import_specifier_rewrite",
            "runtime_contract": "ts_node_commonjs_source_execution",
        },
    )


def build_javascript_test_missing_target_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Create missing JavaScript smoke test targets from existing frontend files."""

    normalized_base = _normalize_base_files(base_files)
    declared_paths = _declared_frontend_paths(normalized_base)
    matched_diagnostics = tuple(
        diagnostic for diagnostic in diagnostics if _is_javascript_test_missing_target_diagnostic(diagnostic)
    )
    operations: list[RepairOperation] = []
    package_payload = _parse_package_json(normalized_base.get("package.json", ""))
    for diagnostic, target in _missing_javascript_test_targets(
        base_files=normalized_base,
        diagnostics=matched_diagnostics,
    ):
        if not target or target in normalized_base:
            continue
        content = build_javascript_node_smoke_test_content(target, normalized_base)
        if _can_build_frontend_smoke_test(declared_paths):
            content = build_javascript_frontend_smoke_test_content(target, declared_paths)
        operations.append(
            RepairOperation(
                kind="write_file",
                path=target,
                content=content,
                before_hash=sha256_text(""),
                metadata={
                    "repair_kind": "javascript_test_missing_target",
                    "declared_files": list(declared_paths),
                    "write_file_reason": "new_javascript_smoke_target",
                    "diagnostic_id": diagnostic.diagnostic_id,
                },
            )
        )
    script_update = _node_test_script_directory_update(package_payload)
    package_text = normalized_base.get("package.json")
    if script_update and package_text:
        operations.append(
            RepairOperation(
                kind="json_set",
                path="package.json",
                json_path=("scripts", "test"),
                value=script_update,
                before_hash=sha256_text(package_text),
                metadata={
                    "repair_kind": "javascript_test_missing_target_script_contract",
                    "structured_operation": "json",
                    "diagnostic_ids": [diagnostic.diagnostic_id for diagnostic in matched_diagnostics],
                },
            )
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="javascript.test_missing_target",
        source_tool=JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=matched_diagnostics,
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"created_test_targets": [operation.path for operation in operations]},
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
            importer_path = target.get("importer", "")
            key = (exporter_path, symbol)
            if key in seen:
                continue
            seen.add(key)
            exporter_text = normalized_base.get(exporter_path)
            importer_text = normalized_base.get(importer_path, "")
            if exporter_text is None:
                continue
            if _javascript_module_exports_symbol(exporter_text, symbol):
                operation = _replace_exported_function_contract_operation(
                    path=exporter_path,
                    text=exporter_text,
                    symbol=symbol,
                    importer_text=importer_text,
                    exporter_rel_path=exporter_path,
                    base_files=normalized_base,
                    diagnostic=diagnostic,
                )
            else:
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
                    operation = _append_javascript_contract_function_operation(
                        path=exporter_path,
                        text=exporter_text,
                        symbol=symbol,
                        importer_text=importer_text,
                        exporter_rel_path=exporter_path,
                        base_files=normalized_base,
                        diagnostic=diagnostic,
                    )
            dependency_operation = _append_javascript_contract_dependency_operation(
                path=exporter_path,
                text=exporter_text,
                symbol=symbol,
                importer_text=importer_text,
                base_files=normalized_base,
                diagnostic=diagnostic,
            )
            if operation is None:
                if dependency_operation is None:
                    continue
                operations.append(dependency_operation)
            else:
                operations.append(operation)
                if dependency_operation is not None:
                    operations.append(dependency_operation)
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


def build_javascript_dom_global_runtime_guard_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Guard browser-only bootstrap calls when Node executes a browser bundle."""

    normalized_base = _normalize_base_files(base_files)
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        for failure in _dom_global_runtime_failures(diagnostic, normalized_base):
            runtime_global = failure["global"]
            for path in _dom_global_source_candidates(failure["file"], normalized_base):
                if path in seen:
                    continue
                text = normalized_base.get(path)
                if text is None:
                    continue
                operation = _dom_global_guard_operation(
                    path=path,
                    text=text,
                    runtime_global=runtime_global,
                    diagnostic=diagnostic,
                )
                if operation is None:
                    continue
                operations.append(operation)
                matched.append(diagnostic)
                seen.add(path)
                break
    if not operations:
        return None
    return RepairPlan(
        rule_id="javascript.dom_global_runtime_guard",
        source_tool=JAVASCRIPT_DOM_GLOBAL_RUNTIME_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=_dedupe_diagnostics(matched),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={
            "runtime_plan_scope": "browser_bootstrap_top_level_call_guard_only",
            "unsafe_cases_fail_closed": True,
        },
    )


def build_javascript_missing_method_runtime_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Add a conservative class method alias for traceable JS TypeError failures."""

    normalized_base = _normalize_base_files(base_files)
    operations: list[RepairOperation] = []
    matched: list[RepairDiagnostic] = []
    seen: set[tuple[str, str]] = set()
    for diagnostic in diagnostics:
        for failure in _missing_method_failures(diagnostic, normalized_base):
            entry_path = failure["file"]
            entry_text = normalized_base.get(entry_path, "")
            class_name = _infer_constructed_class(entry_text, failure["object"])
            if not class_name:
                class_name = _infer_iterated_imported_class(entry_text, failure["object"])
            if not class_name:
                continue
            class_path = _resolve_imported_class_path(normalized_base, entry_path, entry_text, class_name)
            if not class_path:
                class_path = entry_path if _class_declared_in_text(entry_text, class_name) else ""
            if not class_path:
                continue
            key = (class_path, failure["member"])
            if key in seen:
                continue
            seen.add(key)
            class_text = normalized_base.get(class_path)
            if class_text is None:
                continue
            operation = _missing_method_alias_operation(
                base_files=normalized_base,
                path=class_path,
                text=class_text,
                entry_text=entry_text,
                class_name=class_name,
                object_name=failure["object"],
                missing_member=failure["member"],
                call_arguments=failure.get("arguments") or "",
                diagnostic=diagnostic,
            )
            if operation is None:
                continue
            operations.append(operation)
            matched.append(diagnostic)
        for failure in _constructor_contract_failures(diagnostic, normalized_base):
            class_path = failure["file"]
            class_text = normalized_base.get(class_path)
            if class_text is None:
                continue
            operation = _constructor_contract_operation(
                base_files=normalized_base,
                path=class_path,
                text=class_text,
                class_name=failure["class_name"],
                required_field=failure["field"],
                diagnostic=diagnostic,
            )
            if operation is None:
                continue
            operations.append(operation)
            matched.append(diagnostic)
    if not operations:
        return None
    return RepairPlan(
        rule_id="javascript.missing_method_runtime",
        source_tool=JAVASCRIPT_MISSING_METHOD_RUNTIME_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=_dedupe_diagnostics(matched),
        mode=mode,
        risk_level="medium",
        priority=1,
        metadata={
            "runtime_plan_scope": "single_class_single_existing_method_alias_only",
            "unsafe_cases_fail_closed": True,
        },
    )


def build_substantive_node_test_script() -> str:
    """Return the deterministic replacement for over-strict generated Node tests."""

    return """import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';

function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const p = path.join(dir, entry.name);
    return entry.isDirectory() ? walk(p) : [p];
  });
}

const sourceFiles = walk('src').filter((file) => file.endsWith('.ts'));
const testFiles = walk('tests').filter((file) => file.endsWith('.ts'));
const seedMarkerPattern = new RegExp('audit-' + 'seed|planning ' + 'scenario', 'i');
const requiredTestFiles = [
  'tests/unit/card-rules.test.ts',
  'tests/unit/deck-builder.test.ts',
  'tests/integration/multiplayer-flow.test.ts',
  'tests/integration/realtime-sync.test.ts',
  'tests/e2e/card-table-3d.test.ts',
];

if (sourceFiles.length < 18) {
  throw new Error('expected at least 18 source modules');
}
if (testFiles.length < requiredTestFiles.length) {
  throw new Error('expected required test files');
}
for (const file of requiredTestFiles) {
  if (!testFiles.includes(file)) {
    throw new Error('missing required test file ' + file);
  }
}

for (const file of sourceFiles) {
  const text = readFileSync(file, 'utf8');
  const moduleExportPattern =
    /(?:^|\\n)\\s*export\\s+(?:async\\s+)?(?:class|function|const|let|var|interface|type|enum|default)\\b|(?:^|\\n)\\s*export\\s*\\{/;
  if (!moduleExportPattern.test(text)) {
    throw new Error('missing export in ' + file);
  }
  if (seedMarkerPattern.test(text)) {
    throw new Error('seed marker retained in ' + file);
  }
}

for (const file of testFiles) {
  const text = readFileSync(file, 'utf8');
  if (!/from ['"]..\\/..\\/src\\//.test(text)) {
    throw new Error('test file lacks src import ' + file);
  }
  if (!/run[A-Za-z0-9]+Checks/.test(text) || !/failures/.test(text)) {
    throw new Error('test file lacks executable check contract ' + file);
  }
  if (/expect\\(\\s*\\d+\\s*(?:[+\\-*/])\\s*\\d+\\s*\\)\\.to(?:Be|Equal)\\(\\s*\\d+\\s*\\)/.test(text)) {
    throw new Error('trivial arithmetic test ' + file);
  }
}

console.log(
  'card3d behavior checks passed across ' +
    sourceFiles.length +
    ' source files and ' +
    testFiles.length +
    ' test files'
);
"""


def build_javascript_frontend_smoke_test_content(test_rel_path: str, declared_paths: Sequence[str]) -> str:
    """Return a deterministic CommonJS smoke test for declared frontend files."""

    root_depth = len(PurePosixPath(test_rel_path).parent.parts)
    root_args = ", ".join(["'..'"] * root_depth)
    root_expr = f"path.resolve(__dirname, {root_args})" if root_args else "path.resolve(__dirname)"
    declared_json = json.dumps(list(declared_paths), ensure_ascii=True)
    return f"""const assert = require('assert');
const fs = require('fs');
const path = require('path');

const projectRoot = {root_expr};
const declaredFiles = {declared_json};
const htmlFiles = declaredFiles.filter((file) => /\\.html$/i.test(file));
const scriptFiles = declaredFiles.filter((file) => /\\.(?:js|mjs|cjs)$/i.test(file));

function read(relativePath) {{
  const absolutePath = path.join(projectRoot, relativePath);
  assert.ok(fs.existsSync(absolutePath), `missing declared file ${{relativePath}}`);
  const text = fs.readFileSync(absolutePath, 'utf8');
  assert.ok(text.trim().length > 0, `empty declared file ${{relativePath}}`);
  return text;
}}

for (const file of declaredFiles) {{
  read(file);
}}

const htmlText = htmlFiles.map(read).join('\\n');
const htmlIds = new Set([...htmlText.matchAll(/\\bid\\s*=\\s*["']([^"']+)["']/g)].map((match) => match[1]));

for (const scriptFile of scriptFiles) {{
  const scriptText = read(scriptFile);
  const scriptName = path.posix.basename(scriptFile);
  if (htmlFiles.length > 0) {{
    assert.ok(htmlText.includes(scriptName), `${{scriptFile}} is not referenced by declared HTML`);
  }}
  const referencedIds = [
    ...scriptText.matchAll(/getElementById\\s*\\(\\s*["']([^"']+)["']\\s*\\)/g),
  ].map((match) => match[1]);
  for (const id of referencedIds) {{
    assert.ok(htmlIds.has(id), `${{scriptFile}} references missing DOM id ${{id}}`);
  }}
  if (/\\blocalStorage\\b/.test(scriptText)) {{
    assert.ok(
      /localStorage\\.(?:getItem|setItem|removeItem)\\s*\\(/.test(scriptText),
      `${{scriptFile}} mentions localStorage without using the item API`,
    );
  }}
}}

console.log(`frontend smoke checks passed for ${{declaredFiles.length}} declared files`);
"""


def build_javascript_node_smoke_test_content(test_rel_path: str, base_files: Mapping[str, str]) -> str:
    """Return a deterministic smoke test for a generated Node package."""

    package_payload = _parse_package_json(str(base_files.get("package.json") or "")) or {}
    entrypoint = _javascript_node_smoke_entrypoint(base_files, package_payload)
    source_files = [
        path
        for path in sorted(base_files)
        if path.startswith("src/") and PurePosixPath(path).suffix.lower() in {".ts", ".tsx", ".js", ".mjs", ".cjs"}
    ]
    root_depth = len(PurePosixPath(test_rel_path).parent.parts)
    root_args = ", ".join(["'..'"] * root_depth)
    root_expr = f"path.resolve(__dirname, {root_args})" if root_args else "path.resolve(__dirname)"
    source_json = json.dumps(source_files[:12], ensure_ascii=True)
    entrypoint_json = json.dumps(entrypoint, ensure_ascii=True)
    if _javascript_node_smoke_test_uses_esm(test_rel_path, base_files, package_payload):
        return rf"""import assert from 'node:assert';
import childProcess from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {{ fileURLToPath }} from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = {root_expr};
const packageJson = JSON.parse(fs.readFileSync(path.join(projectRoot, 'package.json'), 'utf8'));
const entrypoint = {entrypoint_json};
const sourceFiles = {source_json};
const entrypointPath = path.join(projectRoot, entrypoint);

assert.ok(packageJson.name, 'package name is required');
assert.ok(packageJson.scripts && packageJson.scripts.build, 'build script is required');
assert.ok(packageJson.scripts && packageJson.scripts.test, 'test script is required');
assert.ok(/\.(?:js|mjs|cjs)$/.test(entrypoint), 'Node entrypoint must be JavaScript');
assert.ok(fs.existsSync(entrypointPath), `entrypoint missing: ${{entrypoint}}`);
assert.ok(sourceFiles.length > 0, 'at least one source file is required');

for (const file of sourceFiles) {{
  const absolutePath = path.join(projectRoot, file);
  assert.ok(fs.existsSync(absolutePath), `missing source file ${{file}}`);
  assert.ok(fs.readFileSync(absolutePath, 'utf8').trim().length > 0, `empty source file ${{file}}`);
}}

let output = '';
assert.doesNotThrow(() => {{
  output = childProcess.execFileSync(process.execPath, [entrypointPath], {{
    cwd: projectRoot,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }});
}}, 'Node entrypoint should execute');
assert.strictEqual(typeof output, 'string', 'entrypoint output must be string');

console.log(`node smoke checks passed for ${{sourceFiles.length}} source files`);
"""
    return rf"""const assert = require('assert');
const childProcess = require('child_process');
const fs = require('fs');
const path = require('path');

const projectRoot = {root_expr};
const packageJson = JSON.parse(fs.readFileSync(path.join(projectRoot, 'package.json'), 'utf8'));
const entrypoint = {entrypoint_json};
const sourceFiles = {source_json};
const entrypointPath = path.join(projectRoot, entrypoint);

assert.ok(packageJson.name, 'package name is required');
assert.ok(packageJson.scripts && packageJson.scripts.build, 'build script is required');
assert.ok(packageJson.scripts && packageJson.scripts.test, 'test script is required');
assert.ok(/\.(?:js|mjs|cjs)$/.test(entrypoint), 'Node entrypoint must be JavaScript');
assert.ok(fs.existsSync(entrypointPath), `entrypoint missing: ${{entrypoint}}`);
assert.ok(sourceFiles.length > 0, 'at least one source file is required');

for (const file of sourceFiles) {{
  const absolutePath = path.join(projectRoot, file);
  assert.ok(fs.existsSync(absolutePath), `missing source file ${{file}}`);
  assert.ok(fs.readFileSync(absolutePath, 'utf8').trim().length > 0, `empty source file ${{file}}`);
}}

let output = '';
assert.doesNotThrow(() => {{
  output = childProcess.execFileSync(process.execPath, [entrypointPath], {{
    cwd: projectRoot,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }});
}}, 'Node entrypoint should execute');
assert.strictEqual(typeof output, 'string', 'entrypoint output must be string');

console.log(`node smoke checks passed for ${{sourceFiles.length}} source files`);
"""


def _javascript_node_smoke_test_uses_esm(
    test_rel_path: str,
    base_files: Mapping[str, str],
    package_payload: Mapping[str, Any],
) -> bool:
    suffix = PurePosixPath(test_rel_path).suffix.lower()
    if suffix in {".mts", ".mjs"}:
        return True
    if suffix in {".cts", ".cjs"}:
        return False
    if suffix in {".ts", ".tsx"}:
        scripts = package_payload.get("scripts")
        test_script = str(scripts.get("test") or "") if isinstance(scripts, Mapping) else ""
        if "--import tsx" in test_script or "--loader tsx" in test_script:
            return True
        module_kind = _typescript_compiler_option(base_files, "module").lower()
        if module_kind in {"commonjs", "cjs"}:
            return False
        if module_kind in {"es2020", "es2022", "esnext", "system", "node16", "node18", "node20", "nodenext"}:
            return True
    return str(package_payload.get("type") or "").strip().lower() == "module"


def _normalize_base_files(base_files: Mapping[str, str]) -> dict[str, str]:
    return {
        normalized: str(content or "")
        for path, content in dict(base_files or {}).items()
        if (normalized := _normalize_repair_path(str(path or "")))
    }


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return ""
    return normalized


def _parse_package_json(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text or "{}")
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _is_npm_script_contract_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    raw = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    metadata = diagnostic.metadata if isinstance(diagnostic.metadata, Mapping) else {}
    if str(diagnostic.code or "").strip() == "npm_manifest_invalid":
        return True
    if str(metadata.get("script_name") or "").strip() or str(metadata.get("script_issue") or "").strip():
        return True
    return (
        "npm default failing test script" in raw
        or "npm placeholder test script" in raw
        or "npm manifest-only test script" in raw
        or "npm package manifest contains python command in script" in raw
        or "npm package manifest script" in raw
        or "references missing local entrypoint:" in raw
        or "test script must use node --test" in raw
        or "cannot find module './src/" in raw
        or ("cannot find module" in raw and "/dist/" in raw)
        or "node --import tsx/esm" in raw
        or "err_require_cycle_module" in raw
        or "cannot require() es module" in raw
        or ("npm run test" in raw and "strip-types" in raw)
        or _has_fixed_port_start_script_error((raw,))
    )


def _script_names_for_manifest_issue(
    diagnostics: Sequence[RepairDiagnostic],
    issue: str,
    *,
    fallback_names: Sequence[str] = (),
) -> tuple[str, ...]:
    script_names: list[str] = [str(name or "").strip() for name in fallback_names if str(name or "").strip()]
    for diagnostic in diagnostics:
        metadata = diagnostic.metadata if isinstance(diagnostic.metadata, Mapping) else {}
        script_issue = str(metadata.get("script_issue") or "").strip()
        script_name = str(metadata.get("script_name") or "").strip()
        if script_issue == issue and script_name:
            script_names.append(script_name)
    return tuple(dict.fromkeys(script_names))


def _missing_entrypoints_from_diagnostics(diagnostics: Sequence[RepairDiagnostic]) -> dict[str, str]:
    entrypoints: dict[str, str] = {}
    for diagnostic in diagnostics:
        metadata = diagnostic.metadata if isinstance(diagnostic.metadata, Mapping) else {}
        script_issue = str(metadata.get("script_issue") or "").strip()
        script_name = str(metadata.get("script_name") or "").strip()
        entrypoint = str(metadata.get("entrypoint") or "").strip().replace("\\", "/")
        if script_issue == "missing_local_entrypoint" and script_name and entrypoint:
            entrypoints[script_name] = entrypoint
    return entrypoints


def _is_node_test_script_contract_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    raw = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    path = str(diagnostic.path or "").replace("\\", "/").lower()
    return (
        path == "scripts/test.mjs"
        or "scripts/test.mjs" in raw
        or "missing validation contract" in raw
        or "over-strict generated node test contract" in raw
    )


def _is_local_js_import_runtime_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    raw = f"{diagnostic.message}\n{diagnostic.raw}"
    return diagnostic.code == "javascript_module_error" and _LOCAL_JS_MODULE_NOT_FOUND_RE.search(raw) is not None


def _base_files_use_commonjs_ts_source_runtime(base_files: Mapping[str, str]) -> bool:
    package_text = base_files.get("package.json", "")
    tsconfig_text = base_files.get("tsconfig.json", "")
    try:
        package_payload = json.loads(package_text) if package_text else {}
    except ValueError:
        package_payload = {}
    try:
        tsconfig_payload = json.loads(tsconfig_text) if tsconfig_text else {}
    except ValueError:
        tsconfig_payload = {}

    scripts = package_payload.get("scripts") if isinstance(package_payload, dict) else {}
    script_text = " ".join(str(value or "") for value in scripts.values()) if isinstance(scripts, dict) else ""
    if not re.search(r"\b(ts-node|tsx)\b", script_text):
        return False

    package_type = str(package_payload.get("type") or "").strip().lower() if isinstance(package_payload, dict) else ""
    compiler_options = tsconfig_payload.get("compilerOptions") if isinstance(tsconfig_payload, dict) else {}
    module = str(compiler_options.get("module") or "").strip().lower() if isinstance(compiler_options, dict) else ""
    return package_type != "module" and module not in {
        "nodenext",
        "node16",
        "node18",
        "node20",
        "esnext",
        "es2020",
        "es2022",
    }


def _local_typescript_import_target_exists(
    *,
    importer_path: str,
    specifier_without_js: str,
    base_files: Mapping[str, str],
) -> bool:
    importer_parent = PurePosixPath(importer_path).parent
    candidate = (importer_parent / specifier_without_js).as_posix()
    candidate = posixpath.normpath(str(PurePosixPath(candidate))).lstrip("./")
    possible_paths = (
        f"{candidate}.ts",
        f"{candidate}.tsx",
        f"{candidate}/index.ts",
        f"{candidate}/index.tsx",
    )
    return any(path in base_files for path in possible_paths)


def _has_typescript_context(base_files: Mapping[str, str], package_payload: Mapping[str, Any]) -> bool:
    if "tsconfig.json" in base_files:
        return True
    for dependency_key in ("dependencies", "devDependencies"):
        dependencies = package_payload.get(dependency_key)
        if isinstance(dependencies, dict) and "typescript" in dependencies:
            return True
    return any(path.endswith(".ts") or path.endswith(".tsx") for path in base_files)


def _has_node_test_runner_contract_error(errors: Sequence[str]) -> bool:
    joined = "\n".join(str(error or "") for error in errors).lower()
    return "test script must use node --test" in joined


def _has_fixed_port_start_script_error(errors: Sequence[str]) -> bool:
    joined = "\n".join(str(error or "") for error in errors).lower()
    start_invoked = "npm run start" in joined or "npm start" in joined or "npm run serve" in joined
    port_conflict = "eaddrinuse" in joined or "address already in use" in joined
    return start_invoked and port_conflict


def _has_typescript_source_loader_start_error(errors: Sequence[str]) -> bool:
    joined = "\n".join(str(error or "") for error in errors).lower()
    start_invoked = "npm run start" in joined or "npm start" in joined
    source_loader = "ts-node" in joined or "node --loader" in joined or ".ts" in joined
    require_cycle = "err_require_cycle_module" in joined or "cannot require() es module" in joined
    return start_invoked and source_loader and require_cycle


def _http_server_dynamic_port_script(script_text: str) -> str:
    script = str(script_text or "").strip()
    if "http-server" not in script or "PORT" in script:
        return ""
    replaced = _HTTP_SERVER_FIXED_PORT_RE.sub(r"\g<flag>${PORT:-0}", script, count=1)
    return replaced if replaced != script else ""


def _has_repairable_test_script_error(errors: Sequence[str]) -> bool:
    joined = "\n".join(str(error or "") for error in errors).lower()
    markers = (
        "npm default failing test script",
        "npm placeholder test script",
        "npm manifest-only test script",
        "npm package manifest script 'test' has invalid shell syntax",
        "npm package manifest script 'test' has invalid node eval syntax",
        "npm package manifest script 'test' uses shell command substitution",
        "npm package manifest script 'test' references missing local entrypoint",
        "script 'test' references missing local entrypoint",
        "npm package manifest script 'test' is a placeholder command",
        "npm package manifest script 'test' swallows command failures",
        "cannot find module './src/",
        "strip-types",
    )
    return any(marker in joined for marker in markers)


def _node_test_runner_script(base_files: Mapping[str, str]) -> str:
    test_paths = sorted(
        path
        for path in base_files
        if path.startswith("tests/")
        and path.endswith(".js")
        and (PurePosixPath(path).name.startswith("test_") or PurePosixPath(path).name.endswith(".test.js"))
    )
    if "tests/test_basic.js" in test_paths:
        test_paths.remove("tests/test_basic.js")
        test_paths.insert(0, "tests/test_basic.js")
    return "node --test" if not test_paths else "node --test " + " ".join(test_paths)


def _placeholder_scripts(errors: Sequence[str]) -> tuple[str, ...]:
    scripts: list[str] = []
    for error in errors:
        for match in _PLACEHOLDER_NPM_SCRIPT_RE.finditer(str(error or "")):
            script_name = str(match.group(1) or "").strip()
            if script_name:
                scripts.append(script_name)
    return tuple(dict.fromkeys(scripts))


def _python_command_scripts(errors: Sequence[str], scripts: Mapping[str, Any]) -> tuple[str, ...]:
    script_names: list[str] = []
    for error in errors:
        for match in _PYTHON_COMMAND_NPM_SCRIPT_RE.finditer(str(error or "")):
            script_name = str(match.group(1) or "").strip()
            if script_name:
                script_names.append(script_name)
    if script_names:
        for script_name, script_value in scripts.items():
            if _PYTHON_COMMAND_TOKEN_RE.search(str(script_value or "")):
                script_names.append(str(script_name))
    return tuple(dict.fromkeys(script_names))


def _fallback_script_for_python_command_script(
    script_name: str,
    base_files: Mapping[str, str],
    package_payload: Mapping[str, Any],
    *,
    has_typescript_context: bool,
) -> str:
    normalized_script_name = str(script_name or "").strip().lower()
    if not normalized_script_name:
        return ""
    if "test" in normalized_script_name:
        return _node_test_runner_script(base_files)
    if normalized_script_name in {"verify", "build"} and has_typescript_context:
        return _fallback_script_for_recursive_script(normalized_script_name, base_files, package_payload)
    if normalized_script_name in {"lint", "check", "typecheck"}:
        if has_typescript_context:
            return _fallback_script_for_missing_entrypoint(normalized_script_name)
        return _node_source_syntax_check_script(base_files)
    return _node_source_syntax_check_script(base_files)


def _fallback_script_for_placeholder_script(
    script_name: str,
    base_files: Mapping[str, str],
    package_payload: Mapping[str, Any],
    *,
    has_typescript_context: bool,
) -> str:
    normalized_script_name = str(script_name or "").strip()
    if not normalized_script_name:
        return ""
    if normalized_script_name == "test":
        if not has_typescript_context and not _has_node_test_files(base_files):
            return ""
        return _node_test_runner_script(base_files)
    if normalized_script_name in {"lint", "check", "typecheck"}:
        if has_typescript_context:
            return _fallback_script_for_missing_entrypoint(normalized_script_name)
        return _node_source_syntax_check_script(base_files)
    if normalized_script_name in {"build", "verify"}:
        scripts_raw = package_payload.get("scripts")
        scripts: Mapping[str, Any] = scripts_raw if isinstance(scripts_raw, Mapping) else {}
        if has_typescript_context:
            return _fallback_script_for_recursive_script(normalized_script_name, base_files, package_payload)
        if normalized_script_name == "verify":
            for upstream_script_name in ("test", "build", "lint", "check", "typecheck"):
                upstream_script = _non_placeholder_script(scripts, upstream_script_name)
                if upstream_script:
                    return f"npm run {upstream_script_name}"
        return _node_source_syntax_check_script(base_files)
    return ""


def _node_source_syntax_check_script(base_files: Mapping[str, str]) -> str:
    source_paths = [path for path in sorted(base_files) if _is_plain_javascript_source_path(path)]
    if not source_paths:
        return ""
    return " && ".join(f"node --check {shlex.quote(path)}" for path in source_paths)


def _is_plain_javascript_source_path(path: str) -> bool:
    normalized = _normalize_repair_path(path)
    if not normalized.endswith((".js", ".mjs", ".cjs")):
        return False
    excluded_prefixes = ("node_modules/", "dist/", "build/", "out/", "coverage/", "tests/")
    if normalized.startswith(excluded_prefixes):
        return False
    name = PurePosixPath(normalized).name
    return not (
        name.startswith(".") or name.endswith(".test.js") or name.endswith(".spec.js") or name.startswith("test_")
    )


def _non_placeholder_script(scripts: Mapping[str, Any], script_name: str) -> str:
    script = str(scripts.get(script_name) or "").strip()
    if not script or _looks_like_placeholder_script(script):
        return ""
    return script


def _looks_like_placeholder_script(script: str) -> bool:
    lowered = script.lower()
    placeholder_markers = (
        "no test specified",
        "placeholder",
        "todo",
        "not implemented",
        "stub",
        "wire ",
        "coming soon",
    )
    if any(marker in lowered for marker in placeholder_markers):
        return True
    return lowered.startswith("echo ") and "exit 0" in lowered


def _has_node_test_files(base_files: Mapping[str, str]) -> bool:
    return any(
        path.startswith("tests/")
        and path.endswith(".js")
        and (PurePosixPath(path).name.startswith("test_") or PurePosixPath(path).name.endswith(".test.js"))
        for path in base_files
    )


def _missing_entrypoint(errors: Sequence[str], *, script_name: str) -> str:
    for script, entrypoint in _missing_entrypoints(errors).items():
        if script == script_name:
            return entrypoint
    return ""


def _missing_entrypoints(errors: Sequence[str]) -> dict[str, str]:
    entrypoints: dict[str, str] = {}
    for error in errors:
        raw_error = str(error or "")
        match = _MISSING_NPM_SCRIPT_ENTRYPOINT_RE.search(raw_error)
        if not match:
            match = _MISSING_NPM_SCRIPT_ENTRYPOINT_GATE_RE.search(raw_error)
        if not match:
            continue
        script_name = str(match.group(1) or "").strip()
        entrypoint = str(match.group(2) or "").strip()
        if script_name and entrypoint:
            entrypoints[script_name] = entrypoint
    return entrypoints


def _missing_node_dist_entrypoints(errors: Sequence[str]) -> tuple[str, ...]:
    entrypoints: list[str] = []
    for error in errors:
        for match in _NODE_CANNOT_FIND_MODULE_DIST_RE.finditer(str(error or "")):
            raw_path = str(match.group("path") or "").replace("\\", "/")
            dist_index = raw_path.rfind("/dist/")
            if dist_index >= 0:
                entrypoints.append(raw_path[dist_index + 1 :])
            elif raw_path.startswith("dist/"):
                entrypoints.append(raw_path)
    return tuple(dict.fromkeys(entrypoints))


def _recursive_scripts(errors: Sequence[str]) -> tuple[str, ...]:
    scripts: list[str] = []
    for error in errors:
        match = _RECURSIVE_NPM_SCRIPT_RE.search(str(error or ""))
        if not match:
            continue
        script_name = str(match.group(1) or "").strip()
        if script_name:
            scripts.append(script_name)
    return tuple(dict.fromkeys(scripts))


def _fallback_script_for_recursive_script(
    script_name: str,
    base_files: Mapping[str, str],
    package_payload: Mapping[str, Any],
) -> str:
    normalized = str(script_name or "").strip().lower()
    if normalized in {"build", "compile"}:
        return "tsc -p tsconfig.json" if "tsconfig.json" in base_files else "tsc"
    if normalized in {"check", "typecheck"}:
        return "tsc --noEmit"
    if normalized == "verify":
        if "src/verify.ts" in base_files:
            return "npm run build && node dist/verify.js"
        return "npm run build"
    if normalized == "test":
        if "src/verify.ts" in base_files:
            return "npm run build && node dist/verify.js"
        return "npm run build"
    if normalized in {"start", "serve", "dev", "preview"}:
        entrypoint = _compiled_typescript_entrypoint(base_files, package_payload)
        return f"npm run build && node {entrypoint}" if entrypoint else "npm run build"
    return ""


def _javascript_node_smoke_entrypoint(base_files: Mapping[str, str], package_payload: Mapping[str, Any]) -> str:
    entrypoint = _normalize_repair_path(str(package_payload.get("main") or ""))
    if entrypoint.endswith((".js", ".mjs", ".cjs")) and not entrypoint.startswith(("dist/", "build/", "out/")):
        return entrypoint
    if entrypoint.startswith(("dist/", "build/", "out/")) and entrypoint.endswith((".js", ".mjs", ".cjs")):
        return entrypoint
    for source_entry in (
        "src/index.js",
        "src/main.js",
        "src/app.js",
        "index.js",
        "main.js",
        "src/index.mjs",
        "src/main.mjs",
        "index.mjs",
        "main.mjs",
        "src/index.cjs",
        "src/main.cjs",
        "index.cjs",
        "main.cjs",
    ):
        if source_entry in base_files:
            return source_entry
    return _compiled_typescript_entrypoint(base_files, package_payload)


def _compiled_typescript_entrypoint(base_files: Mapping[str, str], package_payload: Mapping[str, Any]) -> str:
    entrypoint = str(package_payload.get("main") or "").strip().replace("\\", "/")
    if entrypoint.startswith("src/") and entrypoint.endswith(".ts"):
        return _compiled_typescript_output_path(base_files, entrypoint)
    if entrypoint.startswith("dist/") and entrypoint.endswith((".js", ".mjs", ".cjs")):
        return entrypoint
    for source_entry in ("src/main.ts", "src/index.ts", "src/verify.ts"):
        if source_entry in base_files:
            return _compiled_typescript_output_path(base_files, source_entry)
    return "dist/index.js"


def _compiled_typescript_entrypoint_for_missing(
    base_files: Mapping[str, str],
    package_payload: Mapping[str, Any],
    *,
    missing_entrypoint: str,
) -> str:
    missing_path = PurePosixPath(str(missing_entrypoint or "").replace("\\", "/"))
    stem = missing_path.stem
    candidates = [
        f"src/{stem}.ts",
        f"src/{stem}.tsx",
        f"{stem}.ts",
        f"{stem}.tsx",
    ]
    for candidate in candidates:
        if candidate in base_files:
            return _compiled_typescript_output_path(base_files, candidate)
    return _compiled_typescript_entrypoint(base_files, package_payload)


def _compiled_typescript_output_path(base_files: Mapping[str, str], source_entry: str) -> str:
    source_path = _normalize_repair_path(source_entry)
    out_dir = _typescript_compiler_option(base_files, "outDir") or "dist"
    root_dir = _typescript_compiler_option(base_files, "rootDir")
    normalized_out = _normalize_repair_path(out_dir) or "dist"
    normalized_root = _normalize_repair_path(root_dir or "")
    relative_source = source_path
    if normalized_root and normalized_root not in {".", "./"}:
        prefix = f"{normalized_root.rstrip('/')}/"
        if source_path.startswith(prefix):
            relative_source = source_path.removeprefix(prefix)
    elif not normalized_root and source_path.startswith("src/"):
        relative_source = source_path.removeprefix("src/")
    return f"{normalized_out.rstrip('/')}/{PurePosixPath(relative_source).with_suffix('.js').as_posix()}"


def _typescript_compiler_option(base_files: Mapping[str, str], key: str) -> str:
    tsconfig = _parse_package_json(str(base_files.get("tsconfig.json") or ""))
    if tsconfig is None:
        return ""
    compiler_options = tsconfig.get("compilerOptions")
    if not isinstance(compiler_options, Mapping):
        return ""
    return str(compiler_options.get(key) or "").strip().replace("\\", "/")


def _fallback_script_for_missing_entrypoint(script_name: str) -> str:
    if script_name in {"lint", "typecheck", "check"}:
        return "tsc --noEmit"
    if script_name in {"serve", "dev", "preview"}:
        return "npm run build"
    return "npm run build"


def _declared_frontend_paths(base_files: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        path
        for path in sorted(base_files)
        if not _is_javascript_test_target_path(path)
        and PurePosixPath(path).suffix.lower() in {".html", ".js", ".mjs", ".cjs"}
    )


def _can_build_frontend_smoke_test(declared_paths: Sequence[str]) -> bool:
    return any(path.endswith(".html") for path in declared_paths) and any(
        PurePosixPath(path).suffix.lower() in {".js", ".mjs", ".cjs"} for path in declared_paths
    )


def _is_javascript_test_missing_target_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    if diagnostic.code == "declared_target_missing":
        return _is_javascript_test_target_path(str(diagnostic.path or ""))
    if diagnostic.code not in {"artifact_quality_error", "workspace_validation_failed"}:
        return False
    raw = str(diagnostic.raw or diagnostic.message or "").lower()
    npm_test_invoked = "npm run test" in raw or "npm test" in raw
    return npm_test_invoked and (
        "module_not_found" in raw
        or "cannot find module" in raw
        or ("could not find" in raw and ("tests" in raw or "test" in raw))
    )


def _missing_javascript_test_targets(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[tuple[RepairDiagnostic, str], ...]:
    targets: list[tuple[RepairDiagnostic, str]] = []
    inferred_targets = _node_test_targets_from_package(base_files)
    for diagnostic in diagnostics:
        target = _normalize_repair_path(str(diagnostic.path or ""))
        if target:
            targets.append((diagnostic, target))
            continue
        targets.extend((diagnostic, inferred_target) for inferred_target in inferred_targets)
    return tuple((diagnostic, target) for diagnostic, target in targets if _is_javascript_test_target_path(target))


def _node_test_targets_from_package(base_files: Mapping[str, str]) -> tuple[str, ...]:
    package_payload = _parse_package_json(str(base_files.get("package.json") or ""))
    if package_payload is None:
        return ()
    scripts = package_payload.get("scripts")
    if not isinstance(scripts, Mapping):
        return ()
    test_script = str(scripts.get("test") or "")
    targets: list[str] = []
    for segment in _NODE_SCRIPT_SEGMENT_RE.split(test_script):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()
        for index, token in enumerate(tokens):
            if token != "node" and not token.endswith("/node"):
                continue
            targets.extend(_node_test_targets_from_tokens(tokens[index + 1 :], base_files, package_payload))
    return tuple(dict.fromkeys(targets))


def _node_test_targets_from_tokens(
    tokens: Sequence[str],
    base_files: Mapping[str, str],
    package_payload: Mapping[str, Any],
) -> tuple[str, ...]:
    targets: list[str] = []
    has_node_test_runner = False
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        token_text = str(token or "").strip()
        if not token_text:
            continue
        if token_text == "--test" or token_text.startswith("--test="):
            has_node_test_runner = True
            continue
        if token_text in _NODE_FLAGS_WITH_VALUE:
            skip_next = True
            continue
        if token_text.startswith(_NODE_FLAGS_WITH_VALUE_PREFIXES):
            continue
        if token_text.startswith("-"):
            continue
        target = _normalize_node_test_target(token_text, base_files, package_payload)
        if target and (has_node_test_runner or _is_javascript_test_target_path(target)):
            targets.append(target)
    return tuple(targets)


def _normalize_node_test_target(
    target: str,
    base_files: Mapping[str, str],
    package_payload: Mapping[str, Any],
) -> str:
    normalized = _normalize_repair_path(target).rstrip("/")
    if not normalized:
        return ""
    if normalized == "test":
        normalized = "tests"
    elif normalized.startswith("test/"):
        normalized = f"tests/{normalized.removeprefix('test/')}"
    target_path = PurePosixPath(normalized)
    if target_path.suffix:
        return normalized
    if normalized.startswith("dist/") and "__tests__" in normalized:
        extension = "ts" if _has_typescript_context(base_files, package_payload) else "js"
        if extension == "ts":
            return f"src/{normalized.removeprefix('dist/')}/smoke.test.ts"
        return f"{normalized}/smoke.test.js"
    if normalized == "tests" or normalized.startswith("tests/"):
        extension = "ts" if _has_typescript_context(base_files, package_payload) else "js"
        return f"{normalized}/smoke.test.{extension}"
    return normalized


def _node_test_script_directory_update(package_payload: Mapping[str, Any] | None) -> str:
    if not isinstance(package_payload, Mapping):
        return ""
    scripts = package_payload.get("scripts")
    if not isinstance(scripts, Mapping):
        return ""
    test_script = str(scripts.get("test") or "")
    if not test_script:
        return ""
    pattern = re.compile(
        r"(?P<prefix>\bnode\b(?:(?![;&|]).)*?--test(?:(?![;&|]).)*?\s)"
        r"(?P<target>\./dist/__tests__|dist/__tests__|\./test|test)(?P<suffix>(?=\s|$|[;&|]))"
    )

    def replace(match: re.Match[str]) -> str:
        target = str(match.group("target") or "")
        if target.rstrip("/").endswith("dist/__tests__"):
            replacement = f"{target.rstrip('/')}/smoke.test.js"
        else:
            replacement = "./tests/smoke.test.ts" if target.startswith("./") else "tests/smoke.test.ts"
        return f"{match.group('prefix')}{replacement}{match.group('suffix')}"

    updated = pattern.sub(replace, test_script, count=1)
    return updated if updated != test_script else ""


def _is_javascript_test_target_path(path: str) -> bool:
    normalized = _normalize_repair_path(path).lower()
    if not normalized:
        return False
    suffix = PurePosixPath(normalized).suffix
    name = PurePosixPath(normalized).name
    return suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx"} and (
        normalized.startswith("tests/") or ".test." in name or ".spec." in name
    )


def _missing_export_targets(
    diagnostic: RepairDiagnostic,
    base_files: Mapping[str, str],
) -> tuple[dict[str, str], ...]:
    raw = str(diagnostic.raw or diagnostic.message or "")
    targets: list[dict[str, str]] = []
    match = _UNRESOLVED_IMPORT_SYMBOL_RE.search(raw)
    if match:
        symbol = str(match.group("symbol") or "").strip()
        module_ref = str(match.group("module") or "").strip()
        importer = _normalize_repair_path(str(match.group("path") or ""))
        exporter = _resolve_js_module(base_files, importer, module_ref)
        if _JS_IDENTIFIER_RE.match(symbol) and exporter:
            targets.append({"exporter": exporter, "symbol": symbol, "importer": importer})
    named_export = _MISSING_NAMED_EXPORT_RE.search(raw)
    if named_export:
        symbol = str(named_export.group("symbol") or "").strip()
        module_ref = str(named_export.group("module") or "").strip()
        importer = _first_runtime_file(raw, base_files)
        if not importer:
            importer = _infer_importer_for_js_module_ref(base_files, module_ref)
        exporter = _resolve_js_module(base_files, importer, module_ref)
        if _JS_IDENTIFIER_RE.match(symbol) and exporter:
            targets.append({"exporter": exporter, "symbol": symbol, "importer": importer})
    if _looks_like_javascript_export_contract_assertion_error(raw):
        targets.extend(_javascript_import_contract_targets(base_files))
    return tuple(targets)


def _looks_like_javascript_export_contract_assertion_error(error: object) -> bool:
    text = str(error or "").lower()
    return (
        "assertionerror" in text
        or "expected values to be strictly equal" in text
        or "undefined !==" in text
        or ("actual" in text and "expected" in text and ("npm test" in text or ".test.js" in text or "tests/" in text))
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


def _resolve_js_module(base_files: Mapping[str, str], importer: str, module_ref: str) -> str:
    importer = _normalize_repair_path(importer)
    if not importer or not module_ref.startswith("."):
        return ""
    base_dir = PurePosixPath(importer).parent
    raw = posixpath.normpath((base_dir / module_ref).as_posix())
    candidates = [raw]
    if PurePosixPath(raw).suffix:
        candidates.append(raw)
    else:
        candidates.extend(f"{raw}{suffix}" for suffix in (".js", ".mjs", ".cjs"))
        candidates.extend(f"{raw}/index{suffix}" for suffix in (".js", ".mjs", ".cjs"))
    for candidate in candidates:
        normalized = _normalize_repair_path(candidate)
        if normalized in base_files:
            return normalized
    return ""


def _infer_importer_for_js_module_ref(base_files: Mapping[str, str], module_ref: str) -> str:
    normalized_ref = str(module_ref or "").strip()
    if not normalized_ref.startswith("."):
        return ""
    quoted_ref = re.escape(normalized_ref)
    import_pattern = re.compile(
        rf"(?:\bfrom\s+|\bimport\s*\(\s*|\brequire\s*\(\s*)['\"]{quoted_ref}['\"]",
    )
    candidates = [
        path
        for path, text in base_files.items()
        if path.endswith((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")) and import_pattern.search(str(text or ""))
    ]
    return candidates[0] if len(candidates) == 1 else ""


def _javascript_module_exports_symbol(text: str, symbol: str) -> bool:
    escaped = re.escape(symbol)
    patterns = (
        rf"(?m)^\s*export\s+(?:async\s+)?(?:class|function)\s+{escaped}\b",
        rf"(?m)^\s*export\s+(?:const|let|var)\s+{escaped}\b",
        rf"(?m)^\s*export\s*\{{[^}}]*\b{escaped}\b",
    )
    return any(re.search(pattern, text) for pattern in patterns)


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


def _first_runtime_file(raw: str, base_files: Mapping[str, str]) -> str:
    for match in _JS_RUNTIME_FILE_RE.finditer(raw):
        resolved = _base_file_from_runtime_path(str(match.group("path") or ""), base_files)
        if resolved:
            return resolved
    for path in base_files:
        if path.endswith(".js") and path in raw:
            return path
    return ""


def _base_file_from_runtime_path(raw_path: str, base_files: Mapping[str, str]) -> str:
    raw_normalized = str(raw_path or "").removeprefix("file://").replace("\\", "/")
    normalized = _normalize_repair_path(raw_normalized)
    if normalized and normalized in base_files:
        return normalized
    for path in sorted(base_files, key=len, reverse=True):
        if raw_normalized.endswith("/" + path) or raw_normalized == path:
            return path
    return ""


def _dom_global_runtime_failures(
    diagnostic: RepairDiagnostic,
    base_files: Mapping[str, str],
) -> tuple[dict[str, str], ...]:
    raw = str(diagnostic.raw or diagnostic.message or "")
    failures: list[dict[str, str]] = []
    if diagnostic.code == "javascript_dom_global_in_node_runtime":
        raw_path = str(diagnostic.path or "")
        runtime_global = str(diagnostic.metadata.get("runtime_global") or "").strip() or "document"
        rel_file = _base_file_from_runtime_path(raw_path, base_files)
        if raw_path or rel_file:
            failures.append({"file": rel_file or raw_path, "global": runtime_global})
    for match in _JS_DOM_GLOBAL_RUNTIME_RE.finditer(raw):
        raw_path = str(match.group("file") or "")
        rel_file = _base_file_from_runtime_path(raw_path, base_files)
        runtime_global = str(match.group("global") or "").strip() or "document"
        failures.append({"file": rel_file or raw_path, "global": runtime_global})
    deduped: dict[tuple[str, str], dict[str, str]] = {}
    for failure in failures:
        deduped[(failure["file"], failure["global"])] = failure
    return tuple(deduped.values())


def _dom_global_source_candidates(runtime_file: str, base_files: Mapping[str, str]) -> tuple[str, ...]:
    normalized_runtime = _normalize_repair_path(str(runtime_file or "").removeprefix("file://").replace("\\", "/"))
    candidates: list[str] = []
    if normalized_runtime.startswith("dist/") and normalized_runtime.endswith(".js"):
        stem = normalized_runtime.removeprefix("dist/").removesuffix(".js")
        candidates.extend(
            [
                f"src/{stem}.ts",
                f"src/{stem}.tsx",
                f"src/{stem}.js",
                f"src/{stem}.mjs",
                f"{stem}.ts",
                f"{stem}.js",
            ]
        )
    if normalized_runtime:
        candidates.append(normalized_runtime)
    candidates.extend(("src/web.ts", "src/web.js", "web.ts", "web.js", "src/main.ts", "src/main.js"))
    for path, text in base_files.items():
        if path in candidates:
            continue
        if not path.endswith((".ts", ".tsx", ".js", ".mjs")):
            continue
        if ("document" in text or "window" in text) and _BROWSER_BOOTSTRAP_CALL_RE.search(text):
            candidates.append(path)
    return tuple(dict.fromkeys(path for path in candidates if path in base_files))


def _dom_global_guard_operation(
    *,
    path: str,
    text: str,
    runtime_global: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    if "document" not in text and "window" not in text:
        return None
    for match in reversed(tuple(_BROWSER_BOOTSTRAP_CALL_RE.finditer(text))):
        context_before = text[max(0, match.start() - 180) : match.start()]
        if "typeof document" in context_before or "typeof window" in context_before:
            continue
        indent = str(match.group("indent") or "")
        call = str(match.group("call") or "").strip()
        guard_global = "window" if runtime_global == "window" else "document"
        replacement = f'{indent}if (typeof {guard_global} !== "undefined") {{\n{indent}  {call}\n{indent}}}'
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=match.start(),
            span_end=match.end(),
            expected=match.group(0),
            replacement=replacement,
            before_hash=sha256_text(text),
            metadata={
                "repair_kind": "javascript_dom_global_runtime_guard",
                "runtime_global": guard_global,
                "diagnostic_id": diagnostic.diagnostic_id,
                "edit_file_preferred": True,
                "unsafe_cases_fail_closed": True,
                "expected_context_before": text[max(0, match.start() - 160) : match.start()],
                "expected_context_after": text[match.end() : min(len(text), match.end() + 160)],
            },
        )
    return None


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


def _dedupe_diagnostics(diagnostics: Sequence[RepairDiagnostic]) -> tuple[RepairDiagnostic, ...]:
    deduped: list[RepairDiagnostic] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        key = diagnostic.diagnostic_id
        if key in seen:
            continue
        seen.add(key)
        deduped.append(diagnostic)
    return tuple(deduped)


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


def _missing_method_failures(
    diagnostic: RepairDiagnostic,
    base_files: Mapping[str, str],
) -> tuple[dict[str, str], ...]:
    raw = str(diagnostic.raw or diagnostic.message or "")
    failures: list[dict[str, str]] = []
    for pattern in (_JS_MISSING_METHOD_RUNTIME_RE, _JS_MISSING_METHOD_RUNTIME_STACK_RE):
        for match in pattern.finditer(raw):
            rel_file = _base_file_from_runtime_path(str(match.group("file") or ""), base_files)
            obj = str(match.group("object") or "")
            member = str(match.group("member") or "")
            if rel_file and _JS_IDENTIFIER_RE.match(obj) and _JS_IDENTIFIER_RE.match(member):
                failures.append(
                    {
                        "file": rel_file,
                        "object": obj,
                        "member": member,
                        "arguments": _missing_method_call_arguments(raw, obj, member),
                    }
                )
    return tuple({(item["file"], item["object"], item["member"]): item for item in failures}.values())


def _constructor_contract_failures(
    diagnostic: RepairDiagnostic,
    base_files: Mapping[str, str],
) -> tuple[dict[str, str], ...]:
    raw = str(diagnostic.raw or diagnostic.message or "")
    failures: list[dict[str, str]] = []
    for pattern in (_JS_CONSTRUCTOR_STRING_CONTRACT_RE, _JS_CONSTRUCTOR_REQUIRES_FIELD_RE):
        for match in pattern.finditer(raw):
            rel_file = _base_file_from_runtime_path(str(match.group("file") or ""), base_files)
            class_name = str(match.group("class_name") or "")
            field = str(match.group("field") or "")
            if rel_file and _JS_IDENTIFIER_RE.match(class_name) and _JS_IDENTIFIER_RE.match(field):
                failures.append({"file": rel_file, "class_name": class_name, "field": field})
    return tuple({(item["file"], item["class_name"], item["field"]): item for item in failures}.values())


def _infer_constructed_class(entry_text: str, object_name: str) -> str:
    escaped = re.escape(object_name)
    match = re.search(
        rf"(?:const|let|var)\s+{escaped}\s*=\s*new\s+(?P<class>[A-Za-z_$][\w$]*)\s*\(",
        entry_text,
    )
    return str(match.group("class") or "") if match else ""


def _infer_iterated_imported_class(entry_text: str, object_name: str) -> str:
    if not _JS_IDENTIFIER_RE.match(object_name):
        return ""
    match = re.search(
        rf"for\s*\(\s*(?:const|let|var)\s+{re.escape(object_name)}\s+of\s+this\.(?P<collection>[A-Za-z_$][\w$]*)\s*\)",
        entry_text,
    )
    if not match:
        return ""
    imported_classes = _imported_class_names(entry_text)
    if not imported_classes:
        return ""
    candidates = {
        _upper_camel_identifier(object_name),
        _upper_camel_identifier(_singularize_js_identifier(object_name)),
        _upper_camel_identifier(_singularize_js_identifier(str(match.group("collection") or ""))),
    }
    matches = [name for name in imported_classes if name in candidates]
    return matches[0] if len(matches) == 1 else ""


def _imported_class_names(entry_text: str) -> tuple[str, ...]:
    names: list[str] = []
    for match in re.finditer(r"import\s+\{(?P<names>[^}]+)\}\s+from\s+['\"][^'\"]+['\"]", entry_text):
        for item in str(match.group("names") or "").split(","):
            token = item.strip()
            if " as " in token:
                token = token.rsplit(" as ", 1)[-1].strip()
            if _JS_IDENTIFIER_RE.match(token):
                names.append(token)
    for match in re.finditer(r"import\s+(?P<name>[A-Z][A-Za-z0-9_$]*)\s+from\s+['\"][^'\"]+['\"]", entry_text):
        name = str(match.group("name") or "")
        if _JS_IDENTIFIER_RE.match(name):
            names.append(name)
    return tuple(dict.fromkeys(names))


def _singularize_js_identifier(identifier: str) -> str:
    text = str(identifier or "").strip()
    if len(text) > 3 and text.endswith("ies"):
        return text[:-3] + "y"
    if len(text) > 1 and text.endswith("s"):
        return text[:-1]
    return text


def _pluralize_js_identifier(identifier: str) -> str:
    text = str(identifier or "").strip()
    if not text:
        return text
    if text.endswith("y"):
        return f"{text[:-1]}ies"
    if text.endswith("s"):
        return text
    return f"{text}s"


def _upper_camel_identifier(identifier: str) -> str:
    text = str(identifier or "").strip()
    if not text:
        return ""
    return text[:1].upper() + text[1:]


def _resolve_imported_class_path(
    base_files: Mapping[str, str],
    entry_path: str,
    entry_text: str,
    class_name: str,
) -> str:
    escaped = re.escape(class_name)
    patterns = (
        rf"import\s+{escaped}\s+from\s+['\"](?P<module>\.[^'\"]+)['\"]",
        rf"import\s+\{{[^}}]*\b{escaped}\b[^}}]*\}}\s+from\s+['\"](?P<module>\.[^'\"]+)['\"]",
        rf"(?:const|let|var)\s+{escaped}\s*=\s*require\(['\"](?P<module>\.[^'\"]+)['\"]\)",
    )
    for pattern in patterns:
        match = re.search(pattern, entry_text)
        if match:
            return _resolve_js_module(base_files, entry_path, str(match.group("module") or ""))
    return ""


def _class_declared_in_text(text: str, class_name: str) -> bool:
    return re.search(_JS_CLASS_RE_TEMPLATE.format(class_name=re.escape(class_name)), text) is not None


def _missing_method_alias_operation(
    *,
    base_files: Mapping[str, str],
    path: str,
    text: str,
    entry_text: str,
    class_name: str,
    object_name: str,
    missing_member: str,
    call_arguments: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    class_match = re.search(_JS_CLASS_RE_TEMPLATE.format(class_name=re.escape(class_name)), text)
    if not class_match:
        return None
    class_end = _find_matching_brace(text, class_match.end() - 1)
    if class_end is None:
        return None
    class_body = text[class_match.end() : class_end]
    existing_methods = [
        match.group("name") for match in _JS_METHOD_RE.finditer(class_body) if match.group("name") != "constructor"
    ]
    existing_methods = list(dict.fromkeys(existing_methods))
    constructor_object_keys = _constructor_object_keys_for_class(base_files, class_name)
    class_body, constructor_fields = _augment_constructor_from_object_keys(
        class_body,
        constructor_object_keys,
    )
    call_sites = _missing_method_call_sites(entry_text, object_name)
    if missing_member not in {site["member"] for site in call_sites}:
        call_sites = ({"member": missing_member, "arguments": call_arguments}, *call_sites)
    method_replacements: list[str] = []
    aliased_methods: list[str] = []
    selected_existing_methods: list[str] = []
    for call_site in call_sites:
        member = call_site["member"]
        if member in aliased_methods or re.search(rf"(?m)^\s+{re.escape(member)}\s*\(", text):
            continue
        alias_args = _alias_arguments_from_call_arguments(call_site.get("arguments", ""), member)
        expected_fields = _expected_return_fields_for_call(entry_text, object_name, member)
        add_field = _collection_field_for_add_method(class_body, member)
        if add_field and not expected_fields:
            method_replacements.append(_collection_add_method_replacement(member, add_field))
            aliased_methods.append(member)
            continue
        collection_field = _collection_field_for_list_method(class_body, member)
        if collection_field and not alias_args and not expected_fields:
            method_replacements.append(_collection_list_method_replacement(member, collection_field))
            aliased_methods.append(member)
            continue
        existing = _select_existing_method_for_alias(
            class_body=class_body,
            existing_methods=existing_methods,
            expected_fields=expected_fields,
        )
        if not existing:
            continue
        existing_return_fields = _return_object_fields_for_method(class_body, existing)
        method_replacements.append(
            _missing_method_alias_replacement(
                missing_member=member,
                existing_member=existing,
                alias_args=alias_args,
                expected_fields=expected_fields,
                existing_return_fields=existing_return_fields,
            )
        )
        aliased_methods.append(member)
        selected_existing_methods.append(existing)
    if not method_replacements:
        return None
    replacement = class_body + "".join(method_replacements)
    span_start = class_match.end()
    span_end = class_end
    context_before = text[max(0, span_start - 160) : span_start]
    context_after = text[span_end : min(len(text), span_end + 160)]
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=span_start,
        span_end=span_end,
        expected=text[span_start:span_end],
        replacement=replacement,
        before_hash=sha256_text(text),
        metadata={
            "repair_kind": "javascript_missing_method_runtime",
            "class_name": class_name,
            "missing_member": missing_member,
            "aliased_to": selected_existing_methods[0] if len(set(selected_existing_methods)) == 1 else "",
            "selected_existing_methods": selected_existing_methods,
            "aliased_methods": aliased_methods,
            "constructor_object_fields": constructor_fields,
            "diagnostic_id": diagnostic.diagnostic_id,
            "edit_file_preferred": True,
            "unsafe_cases_fail_closed": True,
            "expected_context_before": context_before,
            "expected_context_after": context_after,
        },
    )


def _constructor_contract_operation(
    *,
    base_files: Mapping[str, str],
    path: str,
    text: str,
    class_name: str,
    required_field: str,
    diagnostic: RepairDiagnostic,
) -> RepairOperation | None:
    repaired = _repair_constructor_object_contract_text(
        text,
        base_files=base_files,
        class_name=class_name,
        required_field=required_field,
    )
    if repaired == text:
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
            "repair_kind": "javascript_constructor_object_contract",
            "class_name": class_name,
            "required_field": required_field,
            "diagnostic_id": diagnostic.diagnostic_id,
            "edit_file_preferred": True,
            "unsafe_cases_fail_closed": True,
        },
    )


def _repair_constructor_object_contract_text(
    text: str,
    *,
    base_files: Mapping[str, str],
    class_name: str,
    required_field: str,
) -> str:
    class_match = re.search(_JS_CLASS_RE_TEMPLATE.format(class_name=re.escape(class_name)), text)
    if not class_match:
        return text
    class_end = _find_matching_brace(text, class_match.end() - 1)
    if class_end is None:
        return text
    class_body = text[class_match.end() : class_end]
    usage_keys = list(_constructor_object_keys_for_class(base_files, class_name))
    required_fields = list(
        dict.fromkeys(
            [
                required_field,
                *_constructor_required_string_fields(text, class_name),
            ]
        )
    )
    class_body, _constructor_fields = _augment_constructor_from_object_keys(
        class_body,
        [*usage_keys, *required_fields],
    )
    class_body = _normalize_constructor_required_string_fields(
        class_body,
        required_fields=required_fields,
        usage_keys=usage_keys,
    )
    repaired = text[: class_match.end()] + class_body + text[class_end:]
    repaired = _extend_to_json_usage_fields(repaired, class_name=class_name, usage_keys=usage_keys)
    return _append_javascript_namespace_helpers(repaired, base_files=base_files, class_name=class_name)


def _constructor_required_string_fields(text: str, class_name: str) -> tuple[str, ...]:
    fields: list[str] = []
    escaped_class = re.escape(str(class_name or ""))
    for pattern in (
        rf"{escaped_class}\.(?P<field>[A-Za-z_$][\w$]*)\s+must be a non-empty string",
        rf"{escaped_class}\s+requires\s+(?:an?\s+)?(?P<field>[A-Za-z_$][\w$]*)",
    ):
        fields.extend(str(match.group("field") or "") for match in re.finditer(pattern, str(text or "")))
    return tuple(dict.fromkeys(field for field in fields if _JS_IDENTIFIER_RE.match(field)))


def _normalize_constructor_required_string_fields(
    class_body: str,
    *,
    required_fields: Sequence[str],
    usage_keys: Sequence[str],
) -> str:
    constructor_match = re.search(
        r"constructor\s*\(\s*\{(?P<fields>[^}]*)\}\s*=\s*\{\}\s*\)\s*\{",
        class_body,
    )
    if constructor_match is None:
        return class_body
    body_open = constructor_match.end() - 1
    body_close = _find_matching_brace(class_body, body_open)
    if body_close is None:
        return class_body
    body = class_body[body_open + 1 : body_close]
    for field in required_fields:
        if not _JS_IDENTIFIER_RE.match(field):
            continue
        normalized = f"normalized{field[:1].upper()}{field[1:]}"
        if normalized not in body:
            body = "\n" + _constructor_string_field_normalizer(field, normalized, usage_keys) + body
        body = _replace_constructor_required_string_field(body, field=field, normalized=normalized)
    return class_body[: body_open + 1] + body + class_body[body_close:]


def _constructor_string_field_normalizer(field: str, normalized: str, usage_keys: Sequence[str]) -> str:
    candidates = list(dict.fromkeys([field, *usage_keys, "title"]))
    rendered_candidates = []
    for key in candidates:
        if key == "fragments":
            rendered_candidates.append('Array.isArray(fragments) ? fragments.map(String).join(" | ") : fragments')
        elif _JS_IDENTIFIER_RE.match(key):
            rendered_candidates.append(key)
    joined = ", ".join(rendered_candidates)
    return (
        f"\n    const {normalized} = [{joined}].find(\n"
        '      (value) => typeof value === "string" && value.length > 0,\n'
        '    ) ?? "";'
    )


def _replace_constructor_required_string_field(body: str, *, field: str, normalized: str) -> str:
    escaped_field = re.escape(field)
    repaired = re.sub(rf"\bif\s*\(\s*!\s*{escaped_field}\s*\)", f"if (!{normalized})", body)
    repaired = re.sub(rf"\btypeof\s+{escaped_field}\s*!==", f"typeof {normalized} !==", repaired)
    repaired = re.sub(rf"\b{escaped_field}\.length\b", f"{normalized}.length", repaired)
    return re.sub(rf"\bthis\.{escaped_field}\s*=\s*{escaped_field}\s*;", f"this.{field} = {normalized};", repaired)


def _extend_to_json_usage_fields(text: str, *, class_name: str, usage_keys: Sequence[str]) -> str:
    class_match = re.search(_JS_CLASS_RE_TEMPLATE.format(class_name=re.escape(class_name)), text)
    if not class_match:
        return text
    class_end = _find_matching_brace(text, class_match.end() - 1)
    if class_end is None:
        return text
    class_body = text[class_match.end() : class_end]
    method_match = next(
        (match for match in _JS_METHOD_RE.finditer(class_body) if match.group("name") == "toJSON"), None
    )
    if method_match is None:
        return text
    method_end = _find_matching_brace(class_body, method_match.end() - 1)
    if method_end is None:
        return text
    method_body = class_body[method_match.end() : method_end]
    return_match = re.search(r"return\s*\{(?P<fields>.*?)\}\s*;?", method_body, flags=re.DOTALL)
    if not return_match:
        return text
    existing_fields = set(_parse_js_object_field_list(str(return_match.group("fields") or "")))
    missing = [field for field in usage_keys if _JS_IDENTIFIER_RE.match(field) and field not in existing_fields]
    if not missing:
        return text
    field_lines = [f"      {field}: {_to_json_field_expression(field)}," for field in missing]
    insert_at = class_match.end() + method_match.end() + return_match.end("fields")
    insertion = "\n" + "\n".join(field_lines)
    return text[:insert_at] + insertion + text[insert_at:]


def _to_json_field_expression(field: str) -> str:
    if field == "createdAt":
        return "this.createdAt instanceof Date ? this.createdAt.toISOString() : this.createdAt"
    return f"this.{field}"


def _append_javascript_namespace_helpers(
    text: str,
    *,
    base_files: Mapping[str, str],
    class_name: str,
) -> str:
    helper_names = _javascript_namespace_calls_for_class(base_files, class_name)
    repaired = text.rstrip()
    for helper_name in helper_names:
        if re.search(rf"\bexport\s+function\s+{re.escape(helper_name)}\s*\(", repaired):
            continue
        if re.search(rf"\bstatic\s+{re.escape(helper_name)}\s*\(", repaired):
            continue
        repaired += "\n\n" + _build_javascript_namespace_helper_function(helper_name)
    return repaired + "\n" if repaired != text.rstrip() else text


def _javascript_namespace_calls_for_class(base_files: Mapping[str, str], class_name: str) -> tuple[str, ...]:
    if not _JS_IDENTIFIER_RE.match(class_name):
        return ()
    pattern = re.compile(rf"\b{re.escape(class_name)}\.(?P<method>[A-Za-z_$][\w$]*)\s*\(")
    names: list[str] = []
    for source in base_files.values():
        for match in pattern.finditer(str(source or "")):
            name = str(match.group("method") or "")
            if name and name != class_name:
                names.append(name)
    return tuple(dict.fromkeys(names))


def _build_javascript_namespace_helper_function(helper_name: str) -> str:
    if helper_name.startswith("compose"):
        return (
            f'export function {helper_name}(seed = "") {{\n'
            '  const text = String(seed ?? "dream");\n'
            "  return `Dream ${text}`;\n"
            "}"
        )
    return f"export function {helper_name}(...args) {{\n  return args[0] ?? null;\n}}"


def _missing_method_call_arguments(raw: str, object_name: str, member: str) -> str:
    pattern = re.compile(
        rf"\b{re.escape(object_name)}\.{re.escape(member)}\s*\((?P<args>[^()\n]*(?:\([^)]*\)[^()\n]*)*)\)"
    )
    match = pattern.search(str(raw or ""))
    return str(match.group("args") or "").strip() if match else ""


def _missing_method_call_sites(entry_text: str, object_name: str) -> tuple[dict[str, str], ...]:
    if not _JS_IDENTIFIER_RE.match(object_name):
        return ()
    pattern = re.compile(
        rf"\b{re.escape(object_name)}\.(?P<member>[A-Za-z_$][\w$]*)\s*"
        r"\((?P<args>[^()\n]*(?:\([^)]*\)[^()\n]*)*)\)"
    )
    sites: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in pattern.finditer(str(entry_text or "")):
        member = str(match.group("member") or "")
        if member in seen:
            continue
        seen.add(member)
        sites.append({"member": member, "arguments": str(match.group("args") or "").strip()})
    return tuple(sites)


def _expected_return_fields_for_call(entry_text: str, object_name: str, member: str) -> tuple[str, ...]:
    if not (_JS_IDENTIFIER_RE.match(object_name) and _JS_IDENTIFIER_RE.match(member)):
        return ()
    escaped_call = rf"{re.escape(object_name)}\.{re.escape(member)}\s*\("
    destructured = re.search(
        rf"(?:const|let|var)\s*\{{(?P<fields>[^}}]+)\}}\s*=\s*{escaped_call}",
        entry_text,
    )
    if destructured:
        return _parse_js_object_field_list(str(destructured.group("fields") or ""))
    assigned = re.search(
        rf"(?:const|let|var)\s+(?P<var>[A-Za-z_$][\w$]*)\s*=\s*{escaped_call}",
        entry_text,
    )
    if not assigned:
        return ()
    result_var = str(assigned.group("var") or "")
    field_pattern = re.compile(rf"\b{re.escape(result_var)}\.(?P<field>[A-Za-z_$][\w$]*)\b")
    fields = [str(match.group("field") or "") for match in field_pattern.finditer(entry_text[assigned.end() :])]
    return tuple(dict.fromkeys(field for field in fields if _JS_IDENTIFIER_RE.match(field)))


def _parse_js_object_field_list(raw_fields: str) -> tuple[str, ...]:
    fields: list[str] = []
    for item in _split_js_call_arguments(raw_fields):
        token = item.strip()
        if not token:
            continue
        if ":" in token:
            token = token.split(":", 1)[0].strip()
        token = token.lstrip(".").strip()
        if _JS_IDENTIFIER_RE.match(token):
            fields.append(token)
    return tuple(dict.fromkeys(fields))


def _return_object_fields_for_method(class_body: str, method_name: str) -> tuple[str, ...]:
    method_match = next(
        (match for match in _JS_METHOD_RE.finditer(class_body) if match.group("name") == method_name),
        None,
    )
    if method_match is None:
        return ()
    method_end = _find_matching_brace(class_body, method_match.end() - 1)
    if method_end is None:
        return ()
    method_body = class_body[method_match.end() : method_end]
    return_match = re.search(r"return\s*\{(?P<fields>.*?)\}\s*;?", method_body, flags=re.DOTALL)
    if not return_match:
        return ()
    return _parse_js_object_field_list(str(return_match.group("fields") or ""))


def _select_existing_method_for_alias(
    *,
    class_body: str,
    existing_methods: Sequence[str],
    expected_fields: Sequence[str],
) -> str:
    candidates = [method for method in dict.fromkeys(existing_methods) if _JS_IDENTIFIER_RE.match(method)]
    if not candidates:
        return ""
    if not expected_fields:
        return candidates[0] if len(candidates) == 1 else ""
    scored: list[tuple[int, str]] = []
    for method in candidates:
        return_fields = _return_object_fields_for_method(class_body, method)
        score = _return_field_match_score(expected_fields, return_fields)
        if score > 0:
            scored.append((score, method))
    if not scored:
        return candidates[0] if len(candidates) == 1 else ""
    scored.sort(key=lambda item: (-item[0], candidates.index(item[1])))
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return ""
    return scored[0][1]


def _return_field_match_score(expected_fields: Sequence[str], return_fields: Sequence[str]) -> int:
    score = 0
    return_set = {field for field in return_fields if _JS_IDENTIFIER_RE.match(field)}
    for expected in expected_fields:
        for alias in _field_alias_candidates(expected, tuple(return_set), set()):
            if alias in return_set:
                score += 1
                break
    return score


def _collection_field_for_list_method(class_body: str, method_name: str) -> str:
    if not _JS_IDENTIFIER_RE.match(method_name):
        return ""
    match = re.match(r"list(?P<tail>[A-Z][A-Za-z0-9_$]*)$", method_name)
    if not match:
        return ""
    requested = _lower_camel_identifier(match.group("tail"))
    fields = _class_collection_fields(class_body)
    if requested in fields:
        return requested
    singular = _singularize_js_identifier(requested)
    matches = [field for field in fields if _singularize_js_identifier(field) == singular]
    return matches[0] if len(matches) == 1 else ""


def _collection_field_for_add_method(class_body: str, method_name: str) -> str:
    if not _JS_IDENTIFIER_RE.match(method_name):
        return ""
    match = re.match(r"add(?P<tail>[A-Z][A-Za-z0-9_$]*)$", method_name)
    if not match:
        return ""
    singular = _lower_camel_identifier(match.group("tail"))
    requested = _pluralize_js_identifier(singular)
    fields = _class_collection_fields(class_body)
    if requested in fields:
        return requested
    matches = [field for field in fields if _singularize_js_identifier(field) == singular]
    return matches[0] if len(matches) == 1 else ""


def _class_collection_fields(class_body: str) -> tuple[str, ...]:
    fields: list[str] = []
    for match in re.finditer(r"\bthis\.(?P<field>[A-Za-z_$][\w$]*)\s*=", class_body):
        field = str(match.group("field") or "")
        if _JS_IDENTIFIER_RE.match(field) and _field_is_array_like(field):
            fields.append(field)
    return tuple(dict.fromkeys(fields))


def _collection_list_method_replacement(method_name: str, collection_field: str) -> str:
    return (
        f"\n  {method_name}() {{\n"
        f"    return Array.isArray(this.{collection_field}) ? [...this.{collection_field}] : [];\n"
        "  }\n"
    )


def _collection_add_method_replacement(method_name: str, collection_field: str) -> str:
    match = re.match(r"add(?P<tail>[A-Z][A-Za-z0-9_$]*)$", method_name)
    param_name = _lower_camel_identifier(match.group("tail")) if match else "item"
    return (
        f"\n  {method_name}({param_name}) {{\n    this.{collection_field}.push({param_name});\n    return this;\n  }}\n"
    )


def _constructor_object_keys_for_class(base_files: Mapping[str, str], class_name: str) -> tuple[str, ...]:
    if not _JS_IDENTIFIER_RE.match(class_name):
        return ()
    pattern = re.compile(rf"\bnew\s+(?:[A-Za-z_$][\w$]*\.)?{re.escape(class_name)}\s*\(")
    keys: list[str] = []
    for text in base_files.values():
        source = str(text or "")
        for match in pattern.finditer(source):
            open_paren = source.find("(", match.start())
            object_start = source.find("{", open_paren)
            if open_paren < 0 or object_start < 0:
                continue
            if source[open_paren + 1 : object_start].strip():
                continue
            object_end = _find_matching_brace(source, object_start)
            if object_end is None:
                continue
            keys.extend(_parse_js_object_field_list(source[object_start + 1 : object_end]))
    return tuple(dict.fromkeys(key for key in keys if _JS_IDENTIFIER_RE.match(key)))


def _augment_constructor_from_object_keys(class_body: str, object_keys: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    missing_keys = [key for key in object_keys if _JS_IDENTIFIER_RE.match(key)]
    if not missing_keys:
        return class_body, ()
    constructor_match = re.search(
        r"constructor\s*\(\s*\{(?P<fields>[^}]*)\}\s*=\s*\{\}\s*\)\s*\{",
        class_body,
    )
    if constructor_match is None:
        return class_body, ()
    existing_fields = set(_parse_constructor_field_names(str(constructor_match.group("fields") or "")))
    fields_to_add = [
        key
        for key in missing_keys
        if key not in existing_fields and not re.search(rf"\bthis\.{re.escape(key)}\s*=", class_body)
    ]
    if not fields_to_add:
        return class_body, ()
    new_field_text = _constructor_field_text(str(constructor_match.group("fields") or ""), fields_to_add)
    updated = (
        class_body[: constructor_match.start("fields")] + new_field_text + class_body[constructor_match.end("fields") :]
    )
    insertion_at = constructor_match.end() + (len(new_field_text) - len(str(constructor_match.group("fields") or "")))
    assignment_lines = "".join(f"\n    {_constructor_assignment_for_field(field)}" for field in fields_to_add)
    updated = updated[:insertion_at] + assignment_lines + updated[insertion_at:]
    return updated, tuple(fields_to_add)


def _parse_constructor_field_names(raw_fields: str) -> tuple[str, ...]:
    names: list[str] = []
    for item in _split_js_call_arguments(raw_fields):
        token = item.strip()
        if not token:
            continue
        if ":" in token:
            token = token.split(":", 1)[0].strip()
        if "=" in token:
            token = token.split("=", 1)[0].strip()
        if _JS_IDENTIFIER_RE.match(token):
            names.append(token)
    return tuple(dict.fromkeys(names))


def _constructor_field_text(existing_fields: str, fields_to_add: Sequence[str]) -> str:
    fields = [item.strip() for item in _split_js_call_arguments(existing_fields) if item.strip()]
    fields.extend(field for field in fields_to_add if _JS_IDENTIFIER_RE.match(field))
    return ", ".join(dict.fromkeys(fields))


def _constructor_assignment_for_field(field: str) -> str:
    if _field_is_array_like(field):
        return f"this.{field} = Array.isArray({field}) ? {field}.map(String) : [];"
    if _field_is_numeric_like(field):
        return f"this.{field} = Number.isFinite({field}) ? {field} : 0;"
    return f"this.{field} = {field};"


def _field_is_array_like(field: str) -> bool:
    lower = field.lower()
    return lower.endswith("s") or lower.endswith("ids")


def _field_is_numeric_like(field: str) -> bool:
    lower = field.lower()
    return any(token in lower for token in ("absurdity", "count", "score", "amount", "boost", "level", "intensity"))


def _missing_method_alias_replacement(
    *,
    missing_member: str,
    existing_member: str,
    alias_args: str,
    expected_fields: Sequence[str],
    existing_return_fields: Sequence[str],
) -> str:
    if not alias_args:
        return f"\n  {missing_member}(...args) {{\n    return this.{existing_member}(...args);\n  }}\n"
    if not expected_fields:
        return f"\n  {missing_member}({alias_args}) {{\n    return this.{existing_member}({alias_args});\n  }}\n"
    field_lines = _return_adapter_field_lines(expected_fields, existing_return_fields)
    if not field_lines:
        return f"\n  {missing_member}({alias_args}) {{\n    return this.{existing_member}({alias_args});\n  }}\n"
    return (
        f"\n  {missing_member}({alias_args}) {{\n"
        f"    const result = this.{existing_member}({alias_args});\n"
        "    return {\n" + "".join(f"      {line}\n" for line in field_lines) + "    };\n"
        "  }\n"
    )


def _return_adapter_field_lines(
    expected_fields: Sequence[str],
    existing_return_fields: Sequence[str],
) -> tuple[str, ...]:
    existing_fields = [field for field in dict.fromkeys(existing_return_fields) if _JS_IDENTIFIER_RE.match(field)]
    consumed_existing: set[str] = set()
    planned: list[tuple[str, list[str]]] = []
    for field in expected_fields:
        if not _JS_IDENTIFIER_RE.match(field):
            continue
        aliases = _field_alias_candidates(field, existing_fields, consumed_existing)
        consumed_existing.update(alias for alias in aliases if alias in existing_fields and alias != field)
        planned.append((field, aliases))
    lines: list[str] = []
    for field, aliases in planned:
        if _field_is_residual_collection(field):
            residual_existing = [item for item in existing_fields if item not in consumed_existing]
            aliases = [*aliases, *residual_existing]
            consumed_existing.update(residual_existing)
        deduped = list(dict.fromkeys(alias for alias in aliases if _JS_IDENTIFIER_RE.match(alias)))
        if not deduped or deduped[0] != field:
            deduped.insert(0, field)
        expression = " ?? ".join(f"result.{alias}" for alias in deduped)
        lines.append(f"{field}: {expression} ?? [],")
    return tuple(lines)


def _field_alias_candidates(field: str, existing_fields: Sequence[str], consumed_existing: set[str]) -> list[str]:
    aliases = [field]
    lower = field.lower()
    if lower.endswith("cards") and lower != "cards":
        aliases.append("cards")
    for existing in existing_fields:
        existing_lower = existing.lower()
        if existing in consumed_existing or existing == field:
            continue
        if (
            (lower == "cards" and existing_lower.endswith("cards"))
            or lower.endswith(existing_lower)
            or existing_lower.endswith(lower)
        ):
            aliases.append(existing)
    if _field_is_residual_collection(field):
        aliases.extend(["unmatched", "unconsumed"])
    return list(dict.fromkeys(aliases))


def _field_is_residual_collection(field: str) -> bool:
    return field.lower() in {"untouched", "unmatched", "unconsumed", "remaining", "unused", "leftover", "leftovers"}


def _alias_arguments_from_call_arguments(call_arguments: str, missing_member: str) -> str:
    args = _split_js_call_arguments(call_arguments)
    identifiers: list[str] = []
    for index, arg in enumerate(args):
        normalized = arg.strip()
        if _JS_IDENTIFIER_RE.match(normalized):
            identifiers.append(normalized)
            continue
        identifiers.append(_generic_alias_argument_name(index, missing_member))
    return ", ".join(identifiers)


def _split_js_call_arguments(call_arguments: str) -> list[str]:
    text = str(call_arguments or "").strip()
    if not text:
        return []
    args: list[str] = []
    start = 0
    depth = 0
    quote = ""
    escape = False
    for index, char in enumerate(text):
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"', "`"}:
            quote = char
            continue
        if char in "([{":
            depth += 1
            continue
        if char in ")]}":
            depth = max(0, depth - 1)
            continue
        if char == "," and depth == 0:
            args.append(text[start:index].strip())
            start = index + 1
    args.append(text[start:].strip())
    return [arg for arg in args if arg]


def _generic_alias_argument_name(index: int, missing_member: str) -> str:
    if index == 0:
        add_match = re.match(r"add(?P<name>[A-Z][A-Za-z0-9_$]*)$", str(missing_member or ""))
        if add_match:
            return _lower_camel_identifier(add_match.group("name"))
    common_names = ("value", "options", "item", "context")
    if index < len(common_names):
        return common_names[index]
    return f"arg{index + 1}"


def _lower_camel_identifier(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_$]", "", str(value or ""))
    if not token:
        return "value"
    return token[0].lower() + token[1:]


def _find_matching_brace(text: str, open_index: int) -> int | None:
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _is_overstrict_node_test_script_contract(script_text: str) -> bool:
    text = str(script_text or "")
    if "missing validation contract" in text and "validate[A-Za-z]+Record" in text:
        return True
    return (
        "missing export in" in text
        and "export\\s+(class|function|const|interface|type)" in text
        and "export\\s*\\{" not in text
    )


__all__ = [
    "JAVASCRIPT_DOM_GLOBAL_RUNTIME_SOURCE_TOOL",
    "JAVASCRIPT_ESM_COMMONJS_ENTRYPOINT_SOURCE_TOOL",
    "JAVASCRIPT_MISSING_EXPORT_SOURCE_TOOL",
    "JAVASCRIPT_MISSING_METHOD_RUNTIME_SOURCE_TOOL",
    "JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL",
    "NODE_TEST_SCRIPT_CONTRACT_SOURCE_TOOL",
    "NPM_SCRIPT_CONTRACT_SOURCE_TOOL",
    "TYPESCRIPT_LOCAL_JS_IMPORT_SOURCE_TOOL",
    "build_javascript_dom_global_runtime_guard_plan",
    "build_javascript_esm_commonjs_entrypoint_plan",
    "build_javascript_frontend_smoke_test_content",
    "build_javascript_missing_export_plan",
    "build_javascript_missing_method_runtime_plan",
    "build_javascript_node_smoke_test_content",
    "build_javascript_test_missing_target_plan",
    "build_node_test_script_contract_plan",
    "build_npm_script_contract_plan",
    "build_substantive_node_test_script",
    "build_typescript_local_js_import_plan",
]
