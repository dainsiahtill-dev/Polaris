"""Deterministic TypeScript repair generators.

Re-export, return-object-semicolon, escaped-newline, and relative-import-case
repair clusters, carved verbatim from the original ``deterministic_repairs``
module.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from ..execution_tools import DirectorToolExecutor
from ..task_scope_paths import (
    _dedupe_preserve_order,
    _normalize_declared_task_path,
)
from ._common import (
    _TS_MISSING_CLOSING_BRACE_ERROR_RE,
    _TS_OBJECT_LITERAL_START_RE,
    _TS_OBJECT_PROPERTY_SEMICOLON_LINE_RE,
    _TS_OBJECT_PROPERTY_VALUE_SEMICOLON_LINE_RE,
    _TS_RETURN_OBJECT_START_RE,
    _TS_RUNTIME_EXPORT_TEMPLATE,
    _UNRESOLVED_IMPORT_SYMBOL_ERROR_RE,
    _UNRESOLVED_RELATIVE_IMPORT_ERROR_RE,
    _dedupe_paths,
    _path_inside_workspace,
    _relative_import_repair_target_candidates,
    _relative_import_suffix_order,
)

_TS_MISSING_PROPERTY_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2339:\s*"
    r"Property\s+'(?P<member>[^']+)'\s+does\s+not\s+exist\s+on\s+type\s+'(?P<type>[^']+)'",
    re.IGNORECASE,
)
_TS_NO_EXPORTED_MEMBER_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2305:\s*"
    r"Module\s+(?P<module>.+?)\s+has\s+no\s+exported\s+member\s+['\"](?P<symbol>[^'\"]+)['\"]",
    re.IGNORECASE,
)
_TS_NO_EXPORTED_MEMBER_NAMED_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2724:\s*"
    r"(?P<module>.+?)\s+has\s+no\s+exported\s+member\s+named\s+['\"](?P<symbol>[^'\"]+)['\"]"
    r"(?:\.\s+Did\s+you\s+mean\s+['\"](?P<suggestion>[^'\"]+)['\"])?",
    re.IGNORECASE,
)
_TS_COMMA_EXPECTED_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS1005:\s*"
    r"['\"`],['\"`]\s+expected",
    re.IGNORECASE,
)
_TS_NUMBER_TO_STRING_ARGUMENT_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2345:\s*"
    r"Argument\s+of\s+type\s+'number'\s+is\s+not\s+assignable\s+to\s+parameter\s+of\s+type\s+'string'",
    re.IGNORECASE,
)
_TS_NUMBER_TO_FUNCTION_ARGUMENT_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2345:\s*"
    r"Argument\s+of\s+type\s+'number'\s+is\s+not\s+assignable\s+to\s+parameter\s+of\s+type\s+"
    r"['\"]\(\s*n\s*:\s*number\s*\)\s*=>\s*number['\"]",
    re.IGNORECASE,
)
_TS_CANVAS_SCALE_RETURN_TYPE_RE = re.compile(
    r"(export\s+function\s+scaleToCanvas\s*\([\s\S]*?\)\s*:\s*)"
    r"\{\s*sx\s*:\s*number\s*;\s*sy\s*:\s*number\s*;\s*scale\s*:\s*number\s*;?\s*\}",
    re.MULTILINE,
)
_TS_TOO_FEW_ARGUMENTS_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2554:\s*"
    r"Expected\s+(?P<expected>\d+)\s+arguments?,\s+but\s+got\s+(?P<got>\d+)",
    re.IGNORECASE,
)
_TS_POSSIBLY_NULL_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS18047:\s*"
    r"['\"](?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+is\s+possibly\s+['\"]null['\"]",
    re.IGNORECASE,
)
_TS_DUPLICATE_OBJECT_PROPERTY_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS1117:\s*"
    r"An\s+object\s+literal\s+cannot\s+have\s+multiple\s+properties\s+with\s+the\s+same\s+name",
    re.IGNORECASE,
)
_TS_SOURCEFILE_DIAGNOSTICS_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS(?:2339|2871|7006):\s*"
    r"(?P<message>[^\n]*(?:parseDiagnostics|diagnostics|always\s+nullish|implicitly\s+has\s+an\s+['\"]any['\"]\s+type)[^\n]*)",
    re.IGNORECASE,
)
_HTML_TS_MODULE_SCRIPT_ERROR_RE = re.compile(
    r"HTML\s+module\s+script\s+references\s+TypeScript\s+source\s+['\"](?P<src>[^'\"]+\.tsx?)['\"]\s+"
    r"in\s+(?P<path>\S+);\s+static\s+entrypoints\s+must\s+load\s+JavaScript",
    re.IGNORECASE,
)
_TS_NULLABLE_ARGUMENT_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2345:\s*"
    r"Argument\s+of\s+type\s+['\"](?P<type>[A-Za-z_$][A-Za-z0-9_$]*)\s*\|\s*null['\"]\s+is\s+not\s+assignable\s+"
    r"to\s+parameter\s+of\s+type\s+['\"](?P=type)['\"]",
    re.IGNORECASE,
)
_TS_CANNOT_FIND_TEST_GLOBAL_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS(?:2304|2582):\s*"
    r"Cannot\s+find\s+name\s+['\"](?P<symbol>describe|it|test|expect|beforeEach|afterEach|beforeAll|afterAll)['\"]",
    re.IGNORECASE,
)
_TS_UNINITIALIZED_PROPERTY_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2564:\s*"
    r"Property\s+['\"](?P<member>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+has\s+no\s+initializer",
    re.IGNORECASE,
)
_TS_CANNOT_FIND_NAME_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2304:\s*"
    r"Cannot\s+find\s+name\s+['\"](?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)['\"]",
    re.IGNORECASE,
)
_TS_UNKNOWN_VALUE_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS18046:\s*"
    r"['\"](?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+is\s+of\s+type\s+['\"]unknown['\"]",
    re.IGNORECASE,
)
_TS_REQUIRED_PROPERTY_MISSING_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2741:\s*"
    r"Property\s+['\"](?P<member>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+is\s+missing\s+in\s+type\s+"
    r".+?\s+but\s+required\s+in\s+type\s+['\"](?P<type>[A-Za-z_$][A-Za-z0-9_$]*)['\"]",
    re.IGNORECASE | re.DOTALL,
)
_TS_REQUIRED_PROPERTIES_MISSING_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2739:\s*"
    r"Type\s+.+?\s+is\s+missing\s+the\s+following\s+properties\s+from\s+type\s+"
    r"['\"](?P<type>[A-Za-z_$][A-Za-z0-9_$]*)['\"]:\s*(?P<members>[^\n]+)",
    re.IGNORECASE | re.DOTALL,
)
_TS_ENUM_MEMBER_SEPARATOR_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS1357:\s*"
    r"An\s+enum\s+member\s+name\s+must\s+be\s+followed\s+by\s+a\s+',',\s*'=',\s*or\s*'}'",
    re.IGNORECASE,
)
_TS_ENUM_DECLARATION_LINE_RE = re.compile(r"\benum\s+[A-Za-z_$][A-Za-z0-9_$]*\b[^{}]*{")
_TS_ENUM_MEMBER_LINE_RE = re.compile(
    r"^(?P<prefix>\s*[A-Za-z_$][A-Za-z0-9_$]*(?:\s*=\s*[^,;{}]+?)?)(?P<separator>[;,]?)(?P<space>\s*)(?P<comment>//.*)?$"
)
_TS_FUNCTION_DECLARATION_LINE_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?function\s+[A-Za-z_$][A-Za-z0-9_$]*\s*\((?P<params>[^)]*)\)[^{]*{"
)
_TS_ARROW_FUNCTION_DECLARATION_LINE_RE = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+[A-Za-z_$][A-Za-z0-9_$]*\s*=\s*"
    r"(?:async\s*)?\((?P<params>[^)]*)\)\s*=>\s*{"
)
_TS_CANVAS_CONTEXT_DECLARATION_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?:const|let|var)\s+(?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s*:\s*[^=]+)?\s*=\s*[^;\n]*\.getContext\(\s*['\"]2d['\"]\s*\)\s*;?\s*$"
)
# Generalized: match any function call declaration that may return nullable type
_TS_NULLABLE_FUNCTION_DECLARATION_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?:const|let|var)\s+(?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s*:\s*[^=]+)?\s*=\s*(?P<rhs>[^;\n]*\([^;\n]*\))\s*;?\s*$"
)
_TS_NAMED_REEXPORT_RE = re.compile(
    r"export\s*\{\s*(?P<symbols>[^}]+)\s*\}\s*from\s*['\"](?P<module>[^'\"]+)['\"]\s*;?",
    re.MULTILINE | re.DOTALL,
)
_TS_NULLABLE_DOM_HANDLE_DECLARATION_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?:const|let|var)\s+(?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s*:\s*[^=]+)?\s*=\s*(?P<source>[^;\n]*"
    r"(?:document\.(?:getElementById|querySelector)|"
    r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\.querySelector)"
    r"\s*\([^;\n]*\)[^;\n]*)\s*;?\s*$"
)
_TS_EXPORTED_CLASS_RE_TEMPLATE = r"export\s+(?:abstract\s+)?class\s+{type_name}\b[^{{]*{{"
_TS_STRUCTURAL_TYPE_RE_TEMPLATE = r"(?:export\s+)?(?:interface\s+{type_name}\b[^{{]*{{|type\s+{type_name}\b\s*=\s*{{)"
_TS_IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_TS_STRINGISH_MEMBER_NAMES = {"color", "colour", "id", "key", "label", "name", "title"}
_TS_NUMERIC_MEMBER_NAMES = {
    "amplitude",
    "basex",
    "basey",
    "brightness",
    "count",
    "duration",
    "height",
    "humidity",
    "index",
    "intensity",
    "hue",
    "moonphase",
    "phase",
    "phaseangle",
    "petalcount",
    "r",
    "g",
    "b",
    "alpha",
    "radius",
    "size",
    "speed",
    "time",
    "width",
    "x",
    "y",
    "z",
}


def _apply_deterministic_typescript_entrypoint_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    del artifact_quality_errors
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    package_path = workspace_path / "package.json"
    try:
        package_data = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(package_data, dict):
        return []

    compiled_entrypoint = _detect_typescript_entrypoint_from_package(package_data)
    if not compiled_entrypoint:
        return []
    source_entrypoint = _typescript_source_entrypoint_for_compiled_path(compiled_entrypoint)
    if not source_entrypoint:
        return []

    src_dir = workspace_path / "src"
    if not src_dir.is_dir():
        return []
    entrypoint_path = (workspace_path / source_entrypoint).resolve()
    if not _path_inside_workspace(entrypoint_path, workspace_path) or entrypoint_path.exists():
        return []

    entrypoint_parent = entrypoint_path.parent
    if not _path_inside_workspace(entrypoint_parent, workspace_path):
        return []
    entrypoint_parent.mkdir(parents=True, exist_ok=True)

    modules = [
        item
        for item in _discover_src_modules(src_dir, workspace_path)
        if item != source_entrypoint and not item.endswith(".d.ts")
    ]
    content = _build_typescript_entrypoint_aggregator(modules, entrypoint_parent, workspace_path)
    rel_path = entrypoint_path.relative_to(workspace_path).as_posix()
    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    write_result = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    ).execute_tool(
        "write_file",
        {"file": rel_path, "content": content},
        task_id=task_id,
    )
    if not bool(write_result.get("ok")):
        return []
    with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
        adapter._update_task_progress(task_id, "executing", current_file=rel_path)
    return [
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_typescript_entrypoint_repair",
                "file": rel_path,
                "compiled_entrypoint": compiled_entrypoint,
                "modules": modules,
                "bytes_written": int(write_result.get("bytes_written") or len(content.encode("utf-8"))),
                "operation": str(write_result.get("operation") or "create"),
                "broadcast_ok": bool(write_result.get("broadcast_ok")),
                "director_policy": write_result.get("director_policy"),
            },
        }
    ]


def _apply_deterministic_typescript_missing_export_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    missing_exports = _parse_typescript_missing_export_errors(artifact_quality_errors)
    if not missing_exports:
        return []

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    updated_by_path: dict[Path, str] = {}
    repaired_exports: list[dict[str, str]] = []
    repaired_keys: set[tuple[str, str]] = set()
    for item in missing_exports:
        importer_rel = _normalize_declared_task_path(item["file"])
        module_ref = item["module"]
        symbol = item["symbol"]
        if not importer_rel or not module_ref.startswith(".") or not _TS_IDENTIFIER_RE.fullmatch(symbol):
            continue

        importer_path = (workspace_path / importer_rel).resolve()
        if not _path_inside_workspace(importer_path, workspace_path) or not importer_path.is_file():
            continue
        exporter_path = _resolve_typescript_export_target_for_error(
            importer_path=importer_path,
            module_ref=module_ref,
            workspace_path=workspace_path,
        )
        if exporter_path is None:
            continue
        if exporter_path.suffix.lower() not in {".ts", ".tsx"}:
            continue

        rel_exporter = exporter_path.relative_to(workspace_path).as_posix()
        repair_key = (rel_exporter, symbol)
        if repair_key in repaired_keys:
            continue
        try:
            importer_text = importer_path.read_text(encoding="utf-8")
            module_text = updated_by_path.get(exporter_path) or exporter_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _typescript_module_exports_symbol(module_text, symbol):
            continue

        updated = module_text
        declaration_kind = "export_existing"
        suggestion = str(item.get("suggestion") or "").strip()
        if not suggestion:
            suggestion = _find_typescript_similar_runtime_declaration(module_text, symbol)
        if suggestion:
            declaration_kind, declaration = _build_typescript_suggested_export_alias_declaration(
                symbol=symbol,
                suggestion=suggestion,
                importer_text=importer_text,
                module_text=module_text,
            )
            if declaration:
                updated = module_text.rstrip() + "\n\n" + declaration.rstrip() + "\n"
        if updated == module_text:
            updated = _export_existing_typescript_declaration(module_text, symbol)
            declaration_kind = "export_existing"
        if updated == module_text:
            declaration_kind, declaration = _build_typescript_missing_export_declaration(
                symbol=symbol,
                importer_text=importer_text,
            )
            if not declaration:
                continue
            updated = module_text.rstrip() + "\n\n" + declaration.rstrip() + "\n"

        updated_by_path[exporter_path] = updated
        repaired_keys.add(repair_key)
        repaired_exports.append(
            {
                "file": rel_exporter,
                "importer": importer_rel,
                "module": module_ref,
                "symbol": symbol,
                "kind": declaration_kind,
            }
        )

    return _write_typescript_repair_results(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        updated_by_path=updated_by_path,
        source_tool="deterministic_typescript_missing_export_repair",
        metadata_key="exports",
        metadata_value=repaired_exports,
    )


def _apply_deterministic_typescript_missing_member_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    missing_members = _parse_typescript_missing_member_errors(artifact_quality_errors)
    unknown_values = _parse_typescript_unknown_value_errors(artifact_quality_errors)
    missing_required_properties = _parse_typescript_missing_required_property_errors(artifact_quality_errors)
    if not missing_members and not unknown_values and not missing_required_properties:
        return []

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    writes: list[dict[str, Any]] = []
    updated_by_path: dict[Path, str] = {}
    repaired_members: list[dict[str, str]] = []
    for item in missing_members:
        raw_type_name = item["type"]
        type_name = _typescript_declaration_type_name(raw_type_name)
        member = item["member"]
        declaration_kind = "class"
        declaration_path, declaration_text, class_start, class_end = _find_typescript_class_declaration(
            workspace_path=workspace_path,
            type_name=type_name,
            updated_by_path=updated_by_path,
        )
        if declaration_path is None or declaration_text is None or class_start < 0 or class_end < 0:
            declaration_kind = "structural"
            declaration_path, declaration_text, class_start, class_end = _find_typescript_structural_type_declaration(
                workspace_path=workspace_path,
                type_name=type_name,
                updated_by_path=updated_by_path,
            )
        if declaration_path is None or declaration_text is None or class_start < 0 or class_end < 0:
            continue
        class_text = declaration_text[class_start:class_end]
        usage_line = _typescript_error_usage_line(workspace_path, item)
        static_context = _typescript_static_member_access_context(
            raw_type_name=raw_type_name,
            type_name=type_name,
            member=member,
            usage_line=usage_line,
        )
        if _typescript_class_text_has_member(class_text, member, static_context=static_context):
            continue
        inferred_type = _infer_typescript_missing_member_value_type(
            workspace_path=workspace_path,
            item=item,
            member=member,
            fallback_declaration_text=class_text,
        )
        if declaration_kind == "class":
            member_declaration = _build_typescript_missing_member_declaration(
                member=member,
                usage_line=usage_line,
                class_text=class_text,
                inferred_type=inferred_type,
                static_context=static_context,
            )
        else:
            member_declaration = _build_typescript_missing_member_signature(
                member=member,
                usage_line=usage_line,
                declaration_text=class_text,
                inferred_type=inferred_type,
            )
        if not member_declaration:
            continue
        new_text = (
            declaration_text[:class_end].rstrip() + "\n" + member_declaration + "\n" + declaration_text[class_end:]
        )
        updated_by_path[declaration_path] = new_text
        repaired_members.append(
            {
                "file": declaration_path.relative_to(workspace_path).as_posix(),
                "type": type_name,
                "member": member,
                "declaration_kind": declaration_kind,
            }
        )

    repaired_members.extend(
        _repair_typescript_structural_property_shapes(
            workspace_path=workspace_path,
            missing_members=missing_members,
            unknown_values=unknown_values,
            updated_by_path=updated_by_path,
        )
    )
    repaired_members.extend(
        _repair_typescript_unhostable_member_access_defaults(
            workspace_path=workspace_path,
            missing_members=missing_members,
            updated_by_path=updated_by_path,
        )
    )
    repaired_members.extend(
        _repair_typescript_required_object_literals(
            workspace_path=workspace_path,
            missing_required_properties=missing_required_properties,
            updated_by_path=updated_by_path,
        )
    )
    repaired_members.extend(
        _repair_typescript_return_object_literals_for_repaired_members(
            workspace_path=workspace_path,
            repaired_members=repaired_members,
            updated_by_path=updated_by_path,
        )
    )

    if not updated_by_path:
        return []

    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    executor = DirectorToolExecutor(str(workspace_path), message_bus=message_bus, worker_id="director")
    for path, content in updated_by_path.items():
        rel_path = path.relative_to(workspace_path).as_posix()
        write_result = executor.execute_tool(
            "write_file",
            {"file": rel_path, "content": content},
            task_id=task_id,
        )
        if not bool(write_result.get("ok")):
            continue
        with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
            adapter._update_task_progress(task_id, "executing", current_file=rel_path)
        writes.append(
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_typescript_missing_member_repair",
                    "file": rel_path,
                    "members": [item for item in repaired_members if item["file"] == rel_path],
                    "bytes_written": int(write_result.get("bytes_written") or len(content.encode("utf-8"))),
                    "operation": str(write_result.get("operation") or "modify"),
                    "broadcast_ok": bool(write_result.get("broadcast_ok")),
                    "director_policy": write_result.get("director_policy"),
                },
            }
        )
    return writes


def _apply_deterministic_typescript_tsconfig_lib_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    needs_dom_lib = _typescript_errors_require_dom_lib(artifact_quality_errors)
    needs_import_meta_module = _typescript_errors_require_import_meta_module(artifact_quality_errors)
    if not needs_dom_lib and not needs_import_meta_module:
        return []
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    tsconfig_path = workspace_path / "tsconfig.json"
    if not tsconfig_path.is_file():
        return []
    try:
        payload = json.loads(tsconfig_path.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    compiler_options_raw = payload.get("compilerOptions")
    compiler_options: dict[str, Any] = dict(compiler_options_raw) if isinstance(compiler_options_raw, dict) else {}
    changed = False
    libs_raw = compiler_options.get("lib")
    libs = [str(item) for item in libs_raw] if isinstance(libs_raw, list) else []
    normalized = {item.lower() for item in libs}
    if needs_dom_lib and "dom" not in normalized:
        if not libs:
            libs.append(str(compiler_options.get("target") or "ES2020"))
        libs.append("DOM")
        compiler_options["lib"] = libs
        changed = True
    if needs_import_meta_module and not _typescript_module_allows_import_meta(compiler_options.get("module")):
        compiler_options["module"] = "ES2020"
        changed = True
    if not changed:
        return []
    payload["compilerOptions"] = compiler_options
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    write_result = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    ).execute_tool(
        "write_file",
        {"file": "tsconfig.json", "content": content},
        task_id=task_id,
    )
    if not bool(write_result.get("ok")):
        return []
    with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
        adapter._update_task_progress(task_id, "executing", current_file="tsconfig.json")
    return [
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_typescript_tsconfig_lib_repair",
                "file": "tsconfig.json",
                "libs": libs,
                "module": compiler_options.get("module"),
                "bytes_written": int(write_result.get("bytes_written") or len(content.encode("utf-8"))),
                "operation": str(write_result.get("operation") or "modify"),
                "broadcast_ok": bool(write_result.get("broadcast_ok")),
                "director_policy": write_result.get("director_policy"),
            },
        }
    ]


def _apply_deterministic_html_typescript_module_script_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    script_errors = _parse_html_typescript_module_script_errors(artifact_quality_errors)
    if not script_errors:
        return []
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    updated_by_path: dict[Path, str] = {}
    repaired: list[dict[str, str]] = []
    for item in script_errors:
        rel_file = _normalize_declared_task_path(item["file"])
        source_ref = str(item.get("source") or "").strip()
        if not rel_file or not source_ref:
            continue
        path = (workspace_path / rel_file).resolve()
        if not _path_inside_workspace(path, workspace_path) or not path.is_file():
            continue
        try:
            original = updated_by_path.get(path) or path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        replacement = _html_javascript_entrypoint_for_typescript_source(source_ref)
        if not replacement:
            continue
        repaired_text = original.replace(f'src="{source_ref}"', f'src="{replacement}"')
        repaired_text = repaired_text.replace(f"src='{source_ref}'", f"src='{replacement}'")
        if repaired_text == original:
            continue
        updated_by_path[path] = repaired_text
        repaired.append({"file": rel_file, "source": source_ref, "replacement": replacement})

    return _write_typescript_repair_results(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        updated_by_path=updated_by_path,
        source_tool="deterministic_html_typescript_module_script_repair",
        metadata_key="scripts",
        metadata_value=repaired,
    )


def _apply_deterministic_typescript_member_alias_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    missing_members = _parse_typescript_missing_member_errors(artifact_quality_errors)
    if not missing_members:
        return []
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    updated_by_path: dict[Path, str] = {}
    repaired: list[dict[str, str]] = []
    for item in missing_members:
        rel_file = _normalize_declared_task_path(item["file"])
        member = str(item.get("member") or "").strip()
        type_name = _typescript_declaration_type_name(str(item.get("type") or ""))
        if not rel_file or not _TS_IDENTIFIER_RE.fullmatch(member) or not type_name:
            continue
        path = (workspace_path / rel_file).resolve()
        if not _path_inside_workspace(path, workspace_path) or not path.is_file():
            continue
        usage_line = _typescript_error_usage_line(workspace_path, item)
        receiver = _typescript_receiver_for_member_access(usage_line, member)
        if not receiver:
            continue
        existing_members = _typescript_existing_member_names_for_type(
            workspace_path=workspace_path,
            type_name=type_name,
            updated_by_path=updated_by_path,
        )
        replacement = _typescript_member_alias_replacement(
            receiver=receiver,
            missing_member=member,
            existing_members=existing_members,
        )
        if not replacement:
            continue
        try:
            original = updated_by_path.get(path) or path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        repaired_text = re.sub(
            rf"\b{re.escape(receiver)}\s*\.\s*{re.escape(member)}\b",
            replacement,
            original,
        )
        if repaired_text == original:
            continue
        updated_by_path[path] = repaired_text
        repaired.append(
            {
                "file": rel_file,
                "type": type_name,
                "member": member,
                "receiver": receiver,
                "replacement": replacement,
            }
        )

    return _write_typescript_repair_results(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        updated_by_path=updated_by_path,
        source_tool="deterministic_typescript_member_alias_repair",
        metadata_key="aliases",
        metadata_value=repaired,
    )


def _typescript_errors_require_dom_lib(errors: list[str]) -> bool:
    joined = "\n".join(str(error or "").lower() for error in errors)
    if "include 'dom'" not in joined:
        return False
    return any(
        f"cannot find name '{name}'" in joined for name in ("console", "window", "document", "navigator", "location")
    )


def _typescript_errors_require_import_meta_module(errors: list[str]) -> bool:
    joined = "\n".join(str(error or "").lower() for error in errors)
    return "ts1343" in joined and "import.meta" in joined and "module" in joined


def _typescript_module_allows_import_meta(raw_module: Any) -> bool:
    module = str(raw_module or "").strip().lower()
    return module in {"es2020", "es2022", "esnext", "system", "node16", "node18", "node20", "nodenext"}


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


def _detect_typescript_entrypoint_from_package(package_data: dict[str, Any]) -> str:
    candidates: list[str] = []
    for key in ("main", "module", "browser"):
        value = package_data.get(key)
        if isinstance(value, str):
            candidates.append(value)
    scripts = package_data.get("scripts")
    if isinstance(scripts, dict):
        for key in ("start", "serve", "preview"):
            value = scripts.get(key)
            if isinstance(value, str):
                candidates.extend(_extract_node_entrypoint_paths_from_script(value))

    for candidate in candidates:
        normalized = str(candidate or "").strip().replace("\\", "/")
        if _looks_like_compiled_typescript_entrypoint(normalized):
            return normalized
    return ""


def _extract_node_entrypoint_paths_from_script(script: str) -> list[str]:
    paths: list[str] = []
    for match in re.finditer(r"(?:^|\s)(?:node|tsx|ts-node)\s+(?P<path>[^\s;&|]+)", str(script or "")):
        raw_path = str(match.group("path") or "").strip().strip("'\"")
        if raw_path:
            paths.append(raw_path)
    return _dedupe_preserve_order(paths)


def _looks_like_compiled_typescript_entrypoint(path: str) -> bool:
    token = str(path or "").strip().replace("\\", "/")
    if not token or token.startswith(("/", "../", "./")):
        return False
    parts = [part for part in token.split("/") if part]
    if len(parts) < 2 or parts[0] not in {"dist", "build", "out", "bin"}:
        return False
    return Path(token).suffix.lower() in {".js", ".mjs", ".cjs"}


def _typescript_source_entrypoint_for_compiled_path(compiled_path: str) -> str:
    token = str(compiled_path or "").strip().replace("\\", "/")
    if not _looks_like_compiled_typescript_entrypoint(token):
        return ""
    compiled = Path(token)
    source_name = f"{compiled.stem}.ts"
    return Path("src", *compiled.parts[1:-1], source_name).as_posix()


def _discover_src_modules(src_dir: Path, workspace_path: Path) -> list[str]:
    try:
        src_dir = src_dir.resolve()
        src_dir.relative_to(workspace_path.resolve())
    except (OSError, RuntimeError, ValueError):
        return []
    if not src_dir.is_dir():
        return []

    modules: list[str] = []
    ignored = {"node_modules", "dist", "build", ".vite", ".pytest_cache"}
    for path in sorted(src_dir.rglob("*.ts"), key=lambda item: item.as_posix()):
        rel = path.relative_to(workspace_path).as_posix()
        if any(part in ignored for part in path.relative_to(src_dir).parts):
            continue
        if path.name == "index.ts" or path.name.endswith(".test.ts") or path.name.endswith(".spec.ts"):
            continue
        if path.name.endswith(".d.ts"):
            continue
        modules.append(rel)
    return modules


def _build_typescript_entrypoint_aggregator(
    modules: list[str],
    entrypoint_dir: Path,
    workspace_path: Path,
) -> str:
    imports: list[str] = []
    exports: list[str] = []
    for module in modules:
        module_path = (workspace_path / module).resolve()
        if not _path_inside_workspace(module_path, workspace_path):
            continue
        module_ref = os.path.relpath(module_path.with_suffix(""), entrypoint_dir).replace("\\", "/")
        if not module_ref.startswith("."):
            module_ref = f"./{module_ref}"
        alias = _typescript_entrypoint_module_alias(module)
        imports.append(f"import * as {alias} from '{module_ref}';")
        exports.append(f"export {{ {alias} }};")
    if not imports:
        return "export {};\n"
    return "\n".join([*imports, "", *exports, ""])


def _typescript_entrypoint_module_alias(module: str) -> str:
    stem = str(Path(module).with_suffix("")).removeprefix("src/").replace("/", "_").replace("-", "_")
    alias = re.sub(r"[^A-Za-z0-9_$]", "_", stem)
    if not alias or not re.match(r"[A-Za-z_$]", alias):
        alias = f"module_{alias}"
    return alias


def _parse_typescript_missing_export_errors(errors: list[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for error in errors:
        for match in _UNRESOLVED_IMPORT_SYMBOL_ERROR_RE.finditer(str(error or "")):
            item = {
                "file": _normalize_declared_task_path(match.group("path")),
                "line": "",
                "col": "",
                "module": str(match.group("module") or "").strip().strip("\"'"),
                "symbol": str(match.group("symbol") or "").strip(),
                "suggestion": "",
            }
            key = (item["file"], item["module"], item["symbol"])
            if not item["file"] or not item["module"] or not item["symbol"] or key in seen:
                continue
            seen.add(key)
            parsed.append(item)
        for pattern in (_TS_NO_EXPORTED_MEMBER_ERROR_RE, _TS_NO_EXPORTED_MEMBER_NAMED_ERROR_RE):
            for match in pattern.finditer(str(error or "")):
                item = {
                    "file": str(match.group("file") or "").strip(),
                    "line": str(match.group("line") or "").strip(),
                    "col": str(match.group("col") or "").strip(),
                    "module": _strip_typescript_error_module_ref(str(match.group("module") or "")),
                    "symbol": str(match.group("symbol") or "").strip(),
                    "suggestion": str(match.groupdict().get("suggestion") or "").strip(),
                }
                key = (item["file"], item["module"], item["symbol"])
                if not item["file"] or not item["module"] or not item["symbol"] or key in seen:
                    continue
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


def _parse_html_typescript_module_script_errors(errors: list[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for error in errors:
        for match in _HTML_TS_MODULE_SCRIPT_ERROR_RE.finditer(str(error or "")):
            item = {
                "file": _normalize_declared_task_path(match.group("path")),
                "source": str(match.group("src") or "").strip(),
            }
            key = (item["file"], item["source"])
            if not item["file"] or not item["source"] or key in seen:
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


def _parse_typescript_cannot_find_name_errors(errors: list[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for error in errors:
        for match in _TS_CANNOT_FIND_NAME_ERROR_RE.finditer(str(error or "")):
            item = {
                "file": _normalize_declared_task_path(match.group("file")),
                "line": str(match.group("line") or "").strip(),
                "col": str(match.group("col") or "").strip(),
                "symbol": str(match.group("symbol") or "").strip(),
            }
            key = (item["file"], item["line"], item["symbol"])
            if not item["file"] or not item["line"] or not _TS_IDENTIFIER_RE.fullmatch(item["symbol"]) or key in seen:
                continue
            seen.add(key)
            parsed.append(item)
    return parsed


def _write_typescript_repair_results(
    adapter: Any,
    *,
    workspace_path: Path,
    task_id: str,
    updated_by_path: dict[Path, str],
    source_tool: str,
    metadata_key: str,
    metadata_value: list[dict[str, str]],
) -> list[dict[str, Any]]:
    if not updated_by_path:
        return []
    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    executor = DirectorToolExecutor(str(workspace_path), message_bus=message_bus, worker_id="director")
    writes: list[dict[str, Any]] = []
    for path, content in updated_by_path.items():
        rel_path = path.relative_to(workspace_path).as_posix()
        try:
            before_content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            before_content = ""
        before_hash = hashlib.sha256(before_content.encode("utf-8")).hexdigest()
        after_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        diff_excerpt = _typescript_repair_diff_excerpt(rel_path, before_content, content)
        write_result = executor.execute_tool(
            "write_file",
            {"file": rel_path, "content": content},
            task_id=task_id,
        )
        if not bool(write_result.get("ok")):
            continue
        with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
            adapter._update_task_progress(task_id, "executing", current_file=rel_path)
        writes.append(
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": source_tool,
                    "file": rel_path,
                    metadata_key: [item for item in metadata_value if item.get("file") == rel_path],
                    "bytes_written": int(write_result.get("bytes_written") or len(content.encode("utf-8"))),
                    "operation": str(write_result.get("operation") or "modify"),
                    "before_sha256": before_hash,
                    "after_sha256": after_hash,
                    "diff_excerpt": diff_excerpt,
                    "broadcast_ok": bool(write_result.get("broadcast_ok")),
                    "director_policy": write_result.get("director_policy"),
                },
            }
        )
    return writes


def _typescript_repair_diff_excerpt(rel_path: str, before: str, after: str, *, max_chars: int = 1600) -> str:
    if before == after:
        return ""
    diff = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"a/{rel_path}",
        tofile=f"b/{rel_path}",
        lineterm="",
        n=3,
    )
    return "\n".join(diff)[:max_chars]


def _repair_typescript_nullable_canvas_context_guards(
    text: str,
    symbols: set[str],
) -> tuple[str, list[str]]:
    text, multiline_guarded = _repair_typescript_multiline_dom_handle_declarations(text, symbols)
    lines = text.splitlines()
    repaired_lines: list[str] = []
    guarded: list[str] = list(multiline_guarded)
    for index, line in enumerate(lines):
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
        # Generalized: any function call that may return nullable type
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
            # Add ! after the function call closing paren
            repaired_line = line.rstrip().rstrip(";")
            repaired_line = re.sub(r"\)\s*$", ")!", repaired_line)
            if line.rstrip().endswith(";"):
                repaired_line += ";"
            repaired_lines.append(repaired_line)
            guarded.append(symbol)
            continue
        repaired_lines.append(line)
    if not guarded:
        return text, []
    return "\n".join(repaired_lines) + ("\n" if text.endswith("\n") else ""), _dedupe_preserve_order(guarded)


def _repair_typescript_multiline_dom_handle_declarations(
    text: str,
    symbols: set[str],
) -> tuple[str, list[str]]:
    guarded: list[str] = []
    declaration_re = re.compile(
        r"(?ms)^(?P<indent>\s*)(?P<kind>const|let|var)\s+"
        r"(?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
        r"(?P<source>(?:document\.(?:getElementById|querySelector)|"
        r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\.querySelector)"
        r"\s*\(.*?\)\s+as\s+(?P<type>[^;\n]*\bnull\b[^;\n]*)\s*;)"
    )

    def _replace(match: re.Match[str]) -> str:
        symbol = str(match.group("symbol") or "").strip()
        if symbols and symbol not in symbols:
            return match.group(0)
        source = str(match.group("source") or "")
        narrowed_source = re.sub(r"\s*\|\s*null\b", "", source)
        narrowed_source = re.sub(r"\bnull\s*\|\s*", "", narrowed_source)
        if narrowed_source == source:
            return match.group(0)
        guarded.append(symbol)
        declaration = f"{match.group('indent')}{match.group('kind')} {symbol} = {narrowed_source}"
        following = text[match.end() : match.end() + 240]
        if _typescript_nullable_guard_in_text_window(following, symbol):
            return declaration
        indent = str(match.group("indent") or "")
        return (
            f"{declaration}\n"
            f"{indent}if (!{symbol}) {{\n"
            f'{indent}  throw new Error("DOM element unavailable: {symbol}");\n'
            f"{indent}}}"
        )

    repaired = declaration_re.sub(_replace, text)
    return repaired, _dedupe_preserve_order(guarded)


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


def _typescript_nullable_guard_in_text_window(window: str, symbol: str) -> bool:
    compact = re.sub(r"\s+", "", window)
    return (
        f"if(!{symbol})" in compact
        or f"if({symbol}===null)" in compact
        or f"if({symbol}==null)" in compact
        or f"if(null==={symbol})" in compact
        or f"if(null=={symbol})" in compact
    )


def _repair_typescript_duplicate_object_property_lines(
    text: str,
    line_numbers: set[int],
) -> tuple[str, list[int]]:
    if not line_numbers:
        return text, []
    lines = text.splitlines()
    removed: list[int] = []
    for line_no in sorted(line_numbers, reverse=True):
        index = line_no - 1
        if index < 0 or index >= len(lines):
            continue
        line = lines[index]
        if not _looks_like_single_line_typescript_object_property(line):
            continue
        del lines[index]
        removed.append(line_no)
    if not removed:
        return text, []
    repaired = "\n".join(lines)
    if text.endswith("\n"):
        repaired += "\n"
    return repaired, sorted(removed)


def _looks_like_single_line_typescript_object_property(line: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped or ":" not in stripped:
        return False
    if stripped.startswith(("case ", "default", "return ", "if ", "for ", "while ", "//", "/*", "*")):
        return False
    property_re = re.compile(r"^(?:[A-Za-z_$][A-Za-z0-9_$]*|['\"][^'\"]+['\"]|\[[^\]]+\])\s*:\s*.+,?\s*(?://.*)?$")
    return bool(property_re.match(stripped))


def _html_javascript_entrypoint_for_typescript_source(source_ref: str) -> str:
    source = str(source_ref or "").strip().replace("\\", "/")
    if not source.endswith((".ts", ".tsx")):
        return ""
    source_no_root = source.lstrip("/")
    if source_no_root.startswith("src/"):
        source_no_root = "dist/" + source_no_root[len("src/") :]
    replacement = re.sub(r"\.tsx?$", ".js", source_no_root)
    if not replacement:
        return ""
    return replacement


def _typescript_existing_member_names_for_type(
    *,
    workspace_path: Path,
    type_name: str,
    updated_by_path: dict[Path, str],
) -> set[str]:
    declaration_path, declaration_text, start, end = _find_typescript_structural_type_declaration(
        workspace_path=workspace_path,
        type_name=type_name,
        updated_by_path=updated_by_path,
    )
    if declaration_path is not None and declaration_text is not None and start >= 0 and end >= 0:
        return set(_typescript_structural_member_type_map(declaration_text[start:end]).keys())
    declaration_path, declaration_text, start, end = _find_typescript_class_declaration(
        workspace_path=workspace_path,
        type_name=type_name,
        updated_by_path=updated_by_path,
    )
    if declaration_path is None or declaration_text is None or start < 0 or end < 0:
        return set()
    class_text = declaration_text[start:end]
    members: set[str] = set()
    member_re = re.compile(
        r"^\s*(?:(?:public|private|protected)\s+)?(?:readonly\s+)?"
        r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*(?:[:=]|\()",
        re.MULTILINE,
    )
    for match in member_re.finditer(class_text):
        members.add(str(match.group("name") or "").strip())
    return {member for member in members if member}


def _typescript_member_alias_replacement(
    *,
    receiver: str,
    missing_member: str,
    existing_members: set[str],
) -> str:
    if missing_member in {"x", "y"} and "position" in existing_members:
        return f"{receiver}.position.{missing_member}"
    if missing_member == "brightness" and "intensity" in existing_members:
        return f"{receiver}.intensity"
    if missing_member == "glow" and "brightness" in existing_members:
        return f"{receiver}.brightness"
    if missing_member == "size":
        if "petalRadius" in existing_members:
            return f"{receiver}.petalRadius"
        if "radius" in existing_members:
            return f"{receiver}.radius"
    if missing_member == "color":
        if {"hue", "saturation", "lightness"}.issubset(existing_members):
            return (
                f"`hsl(${{{receiver}.hue}}, "
                f"${{Math.round({receiver}.saturation * 100)}}%, "
                f"${{Math.round({receiver}.lightness * 100)}}%)`"
            )
        if "hue" in existing_members:
            return f"`hsl(${{{receiver}.hue}}, 70%, 62%)`"
    return ""


def _find_typescript_class_declaration(
    *,
    workspace_path: Path,
    type_name: str,
    updated_by_path: dict[Path, str],
) -> tuple[Path | None, str | None, int, int]:
    declaration_type_name = _typescript_declaration_type_name(type_name)
    class_re = re.compile(_TS_EXPORTED_CLASS_RE_TEMPLATE.format(type_name=re.escape(declaration_type_name)))
    for path in _iter_typescript_files(workspace_path):
        try:
            text = updated_by_path.get(path) or path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        match = class_re.search(text)
        if not match:
            continue
        open_brace = text.find("{", match.start())
        close_brace = _find_matching_brace(text, open_brace)
        if close_brace < 0:
            continue
        return path, text, match.start(), close_brace
    return None, None, -1, -1


def _find_typescript_structural_type_declaration(
    *,
    workspace_path: Path,
    type_name: str,
    updated_by_path: dict[Path, str],
) -> tuple[Path | None, str | None, int, int]:
    declaration_type_name = _typescript_declaration_type_name(type_name)
    declaration_re = re.compile(_TS_STRUCTURAL_TYPE_RE_TEMPLATE.format(type_name=re.escape(declaration_type_name)))
    for path in _iter_typescript_files(workspace_path):
        try:
            text = updated_by_path.get(path) or path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        match = declaration_re.search(text)
        if not match:
            continue
        open_brace = text.find("{", match.start())
        close_brace = _find_matching_brace(text, open_brace)
        if close_brace < 0:
            continue
        return path, text, match.start(), close_brace
    return None, None, -1, -1


def _find_matching_brace(text: str, open_brace: int) -> int:
    if open_brace < 0 or open_brace >= len(text) or text[open_brace] != "{":
        return -1
    depth = 0
    for index in range(open_brace, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _typescript_class_text_has_member(class_text: str, member: str, *, static_context: bool = False) -> bool:
    escaped = re.escape(member)
    if static_context:
        return bool(
            re.search(
                rf"^\s*(?:(?:public|private|protected)\s+)?static\s+(?:readonly\s+)?(?:get\s+)?"
                rf"{escaped}\b\s*(?:\(|:|=)",
                class_text,
                re.MULTILINE,
            )
        )
    return bool(
        re.search(
            rf"^\s*(?:(?:public|private|protected)\s+)?(?:readonly\s+)?(?:get\s+)?{escaped}\b\s*(?:\(|:|=)",
            class_text,
            re.MULTILINE,
        )
    )


def _typescript_error_usage_line(workspace_path: Path, item: dict[str, str]) -> str:
    rel_path = str(item.get("file") or "").strip()
    try:
        line_no = int(str(item.get("line") or "0"))
    except ValueError:
        return ""
    source_path = (workspace_path / rel_path).resolve()
    if not _path_inside_workspace(source_path, workspace_path) or not source_path.is_file():
        return ""
    try:
        lines = source_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return ""
    if line_no < 1 or line_no > len(lines):
        return ""
    return lines[line_no - 1]


def _typescript_source_lines_for_error_item(
    workspace_path: Path,
    item: dict[str, Any],
) -> tuple[Path | None, list[str], int]:
    rel_path = str(item.get("file") or "").strip()
    try:
        line_no = int(str(item.get("line") or "0"))
    except ValueError:
        return None, [], 0
    source_path = (workspace_path / rel_path).resolve()
    if not _path_inside_workspace(source_path, workspace_path) or not source_path.is_file():
        return None, [], 0
    try:
        return source_path, source_path.read_text(encoding="utf-8").splitlines(), line_no
    except (OSError, UnicodeDecodeError):
        return None, [], 0


def _infer_typescript_missing_member_value_type(
    *,
    workspace_path: Path,
    item: dict[str, str],
    member: str,
    fallback_declaration_text: str,
) -> str:
    usage_line = _typescript_error_usage_line(workspace_path, item)
    object_shape = _infer_typescript_object_shape_for_symbol(
        workspace_path=workspace_path,
        item=item,
        symbol=member,
    )
    if object_shape:
        return object_shape
    if _typescript_symbol_usage_treats_as_number(workspace_path=workspace_path, item=item, symbol=member):
        return "number"
    return _typescript_missing_member_value_type(member, usage_line, fallback_declaration_text)


def _infer_typescript_object_shape_for_symbol(
    *,
    workspace_path: Path,
    item: dict[str, Any],
    symbol: str,
) -> str:
    _source_path, lines, line_no = _typescript_source_lines_for_error_item(workspace_path, item)
    if not lines or line_no < 1:
        return ""
    children: dict[str, str] = {}
    symbol_re = re.compile(rf"\b{re.escape(symbol)}\s*\.\s*(?P<child>[A-Za-z_$][A-Za-z0-9_$]*)\b")
    start = max(0, line_no - 8)
    end = min(len(lines), line_no + 80)
    for line in lines[start:end]:
        for match in symbol_re.finditer(line):
            child = str(match.group("child") or "").strip()
            if child:
                children.setdefault(child, _infer_typescript_property_child_value_type(child, line))
    if not children:
        return ""
    return _typescript_object_type_from_fields(children)


def _typescript_symbol_usage_treats_as_number(
    *,
    workspace_path: Path,
    item: dict[str, Any],
    symbol: str,
) -> bool:
    _source_path, lines, line_no = _typescript_source_lines_for_error_item(workspace_path, item)
    if not lines or line_no < 1:
        return False
    symbol_token = rf"\b{re.escape(symbol)}\b"
    start = max(0, line_no - 8)
    end = min(len(lines), line_no + 80)
    for line in lines[start:end]:
        if re.search(rf"{symbol_token}\s*(?:[*/%+\-]|[<>]=?)", line) or re.search(
            rf"(?:[*/%+\-]|[<>]=?)\s*[^;\n]*{symbol_token}",
            line,
        ):
            return True
    return False


def _infer_typescript_property_child_value_type(child: str, usage_line: str) -> str:
    if _typescript_usage_line_treats_member_as_string(child, usage_line) or _typescript_member_name_suggests_string(
        child
    ):
        return "string"
    if _typescript_usage_line_treats_member_as_number(child, usage_line) or _typescript_member_name_suggests_number(
        child
    ):
        return "number"
    return "number"


def _typescript_object_type_from_fields(fields: dict[str, str]) -> str:
    parts = [f"{name}: {value_type}" for name, value_type in fields.items() if _TS_IDENTIFIER_RE.fullmatch(name)]
    if not parts:
        return "{}"
    return "{ " + "; ".join(parts) + " }"


def _repair_typescript_structural_property_shapes(
    *,
    workspace_path: Path,
    missing_members: list[dict[str, str]],
    unknown_values: list[dict[str, str]],
    updated_by_path: dict[Path, str],
) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str, Path], dict[str, str]] = {}
    for item in missing_members:
        if str(item.get("type") or "").strip().lower() not in {"string", "number", "boolean", "unknown"}:
            continue
        usage_line = _typescript_error_usage_line(workspace_path, item)
        receiver = _typescript_receiver_for_member_access(usage_line, str(item.get("member") or ""))
        if not receiver:
            continue
        parent_type, property_name = _typescript_parent_type_for_local_symbol(
            workspace_path=workspace_path,
            item=item,
            symbol=receiver,
        )
        if not parent_type or not property_name:
            continue
        declaration_path, _declaration_text, _start, _end = _find_typescript_structural_type_declaration(
            workspace_path=workspace_path,
            type_name=parent_type,
            updated_by_path=updated_by_path,
        )
        if declaration_path is None:
            continue
        grouped.setdefault((parent_type, property_name, declaration_path), {})[str(item.get("member") or "")] = (
            _infer_typescript_property_child_value_type(str(item.get("member") or ""), usage_line)
        )

    for item in unknown_values:
        symbol = str(item.get("symbol") or "").strip()
        if not symbol:
            continue
        parent_type, property_name = _typescript_parent_type_for_local_symbol(
            workspace_path=workspace_path,
            item=item,
            symbol=symbol,
        )
        if not parent_type or not property_name:
            continue
        declaration_path, _declaration_text, _start, _end = _find_typescript_structural_type_declaration(
            workspace_path=workspace_path,
            type_name=parent_type,
            updated_by_path=updated_by_path,
        )
        if declaration_path is None:
            continue
        object_shape = _infer_typescript_object_shape_for_symbol(
            workspace_path=workspace_path,
            item=item,
            symbol=symbol,
        )
        child_types = _typescript_object_type_fields(object_shape)
        if child_types:
            grouped.setdefault((parent_type, property_name, declaration_path), {}).update(child_types)

    repaired: list[dict[str, str]] = []
    for (type_name, property_name, declaration_path), child_types in grouped.items():
        if not child_types:
            continue
        try:
            declaration_text = updated_by_path.get(declaration_path) or declaration_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new_text = _replace_typescript_structural_property_type(
            declaration_text,
            property_name=property_name,
            replacement_type=_typescript_object_type_from_fields(child_types),
        )
        if new_text == declaration_text:
            continue
        updated_by_path[declaration_path] = new_text
        repaired.append(
            {
                "file": declaration_path.relative_to(workspace_path).as_posix(),
                "type": type_name,
                "member": property_name,
                "declaration_kind": "structural_property_shape",
            }
        )
    return repaired


def _typescript_receiver_for_member_access(usage_line: str, member: str) -> str:
    safe_member = re.sub(r"[^A-Za-z0-9_$]", "", member)
    if not safe_member:
        return ""
    match = re.search(rf"\b(?P<receiver>[A-Za-z_$][A-Za-z0-9_$]*)\s*\.\s*{re.escape(safe_member)}\b", usage_line)
    return str(match.group("receiver") or "").strip() if match else ""


def _typescript_parent_type_for_local_symbol(
    *,
    workspace_path: Path,
    item: dict[str, Any],
    symbol: str,
) -> tuple[str, str]:
    _source_path, lines, line_no = _typescript_source_lines_for_error_item(workspace_path, item)
    if not lines or line_no < 1:
        return "", ""
    source_var = _typescript_destructuring_source_for_local_symbol(lines, line_no, symbol)
    if not source_var:
        return "", ""
    parent_type = _typescript_type_for_local_variable(lines, line_no, source_var)
    if not parent_type:
        return "", ""
    return parent_type, symbol


def _typescript_destructuring_source_for_local_symbol(lines: list[str], line_no: int, symbol: str) -> str:
    start = max(0, line_no - 80)
    end = min(len(lines), max(line_no + 1, 1))
    destructuring_re = re.compile(
        r"\b(?:const|let|var)\s*{\s*(?P<members>[^}]+?)\s*}\s*=\s*(?P<source>[A-Za-z_$][A-Za-z0-9_$]*)"
    )
    for line in reversed(lines[start:end]):
        match = destructuring_re.search(line)
        if not match:
            continue
        members = {
            part.split(":", 1)[-1].strip() for part in str(match.group("members") or "").split(",") if part.strip()
        }
        if symbol in members:
            return str(match.group("source") or "").strip()
    return ""


def _typescript_type_for_local_variable(lines: list[str], line_no: int, variable: str) -> str:
    start = max(0, line_no - 120)
    end = min(len(lines), max(line_no + 1, 1))
    typed_re = re.compile(rf"\b{re.escape(variable)}\s*:\s*(?P<type>[A-Za-z_$][A-Za-z0-9_$]*)\b")
    assertion_re = re.compile(rf"\b{re.escape(variable)}\b[^;\n]*\bas\s+(?P<type>[A-Za-z_$][A-Za-z0-9_$]*)\b")
    for line in reversed(lines[start:end]):
        match = typed_re.search(line) or assertion_re.search(line)
        if match:
            return str(match.group("type") or "").strip()
    return ""


def _replace_typescript_structural_property_type(
    declaration_text: str,
    *,
    property_name: str,
    replacement_type: str,
) -> str:
    safe_property = re.sub(r"[^A-Za-z0-9_$]", "", property_name)
    if not safe_property or not replacement_type.strip():
        return declaration_text
    lines = declaration_text.splitlines(keepends=True)
    property_re = re.compile(rf"^(?P<indent>\s*){re.escape(safe_property)}(?P<optional>\??)\s*:\s*")
    for index, line in enumerate(lines):
        match = property_re.match(line)
        if not match:
            continue
        newline = "\n" if line.endswith("\n") else ""
        suffix = ";" if line.rstrip().endswith(";") else ","
        lines[index] = (
            f"{match.group('indent')}{safe_property}{match.group('optional')}: {replacement_type}{suffix}{newline}"
        )
        return "".join(lines)
    return declaration_text


def _typescript_object_type_fields(ts_type: str) -> dict[str, str]:
    token = ts_type.strip()
    if not token.startswith("{") or not token.endswith("}"):
        return {}
    body = token[1:-1]
    fields: dict[str, str] = {}
    for part in body.split(";"):
        if ":" not in part:
            continue
        name, value_type = part.split(":", 1)
        key = name.strip().rstrip("?")
        value = value_type.strip()
        if _TS_IDENTIFIER_RE.fullmatch(key) and value:
            fields[key] = value
    return fields


def _repair_typescript_required_object_literals(
    *,
    workspace_path: Path,
    missing_required_properties: list[dict[str, Any]],
    updated_by_path: dict[Path, str],
) -> list[dict[str, str]]:
    repaired: list[dict[str, str]] = []
    for item in missing_required_properties:
        rel_path = str(item.get("file") or "").strip()
        type_name = str(item.get("type") or "").strip()
        members = [
            str(member).strip() for member in item.get("members", []) if _TS_IDENTIFIER_RE.fullmatch(str(member))
        ]
        if not rel_path or not type_name or not members:
            continue
        source_path = (workspace_path / rel_path).resolve()
        if not _path_inside_workspace(source_path, workspace_path) or not source_path.is_file():
            continue
        declaration_path, declaration_text, declaration_start, declaration_end = (
            _find_typescript_structural_type_declaration(
                workspace_path=workspace_path,
                type_name=type_name,
                updated_by_path=updated_by_path,
            )
        )
        if declaration_path is None or declaration_text is None or declaration_start < 0 or declaration_end < 0:
            continue
        member_types = _typescript_structural_member_type_map(declaration_text[declaration_start:declaration_end])
        additions = {
            member: member_types[member]
            for member in members
            if member in member_types and member_types[member].strip()
        }
        if not additions:
            continue
        try:
            source_text = updated_by_path.get(source_path) or source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            line_no = int(str(item.get("line") or "0"))
        except ValueError:
            continue
        bounds = _find_typescript_object_literal_bounds_for_line(source_text, line_no)
        if bounds is None:
            continue
        open_brace, close_brace = bounds
        new_text = _insert_typescript_object_literal_defaults(
            source_text,
            open_brace=open_brace,
            close_brace=close_brace,
            additions=additions,
        )
        if new_text == source_text:
            continue
        updated_by_path[source_path] = new_text
        repaired.append(
            {
                "file": source_path.relative_to(workspace_path).as_posix(),
                "type": type_name,
                "member": ",".join(additions),
                "declaration_kind": "object_literal_required_defaults",
            }
        )
    return repaired


def _repair_typescript_return_object_literals_for_repaired_members(
    *,
    workspace_path: Path,
    repaired_members: list[dict[str, str]],
    updated_by_path: dict[Path, str],
) -> list[dict[str, str]]:
    members_by_type: dict[str, set[str]] = {}
    for item in repaired_members:
        type_name = str(item.get("type") or "").strip()
        member = str(item.get("member") or "").strip()
        if not type_name or not member or "," in member:
            continue
        if str(item.get("declaration_kind") or "") not in {
            "structural",
            "structural_property_shape",
        }:
            continue
        members_by_type.setdefault(type_name, set()).add(member)
    if not members_by_type:
        return []

    type_member_defaults: dict[str, dict[str, str]] = {}
    for type_name, members in members_by_type.items():
        declaration_path, declaration_text, declaration_start, declaration_end = (
            _find_typescript_structural_type_declaration(
                workspace_path=workspace_path,
                type_name=type_name,
                updated_by_path=updated_by_path,
            )
        )
        if declaration_path is None or declaration_text is None or declaration_start < 0 or declaration_end < 0:
            continue
        member_types = _typescript_structural_member_type_map(declaration_text[declaration_start:declaration_end])
        additions = {member: member_types[member] for member in members if member in member_types}
        if additions:
            type_member_defaults[type_name] = additions
    if not type_member_defaults:
        return []

    repaired: list[dict[str, str]] = []
    for source_path in _iter_typescript_files(workspace_path):
        try:
            source_text = updated_by_path.get(source_path) or source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new_text = source_text
        source_repairs: list[tuple[str, set[str]]] = []
        for type_name, additions in type_member_defaults.items():
            patched, patched_members = _patch_typescript_function_return_object_literals(
                new_text,
                type_name=type_name,
                additions=additions,
            )
            if patched != new_text:
                new_text = patched
                source_repairs.append((type_name, patched_members))
        if new_text == source_text:
            continue
        updated_by_path[source_path] = new_text
        for type_name, members in source_repairs:
            repaired.append(
                {
                    "file": source_path.relative_to(workspace_path).as_posix(),
                    "type": type_name,
                    "member": ",".join(sorted(members)),
                    "declaration_kind": "return_object_literal_defaults",
                }
            )
    return repaired


def _patch_typescript_function_return_object_literals(
    text: str,
    *,
    type_name: str,
    additions: dict[str, str],
) -> tuple[str, set[str]]:
    if not additions:
        return text, set()
    function_re = re.compile(rf"\)\s*:\s*{re.escape(type_name)}\s*{{")
    offset = 0
    patched = text
    patched_members: set[str] = set()
    while True:
        match = function_re.search(patched, offset)
        if not match:
            break
        function_open = patched.find("{", match.start(), match.end())
        function_close = _find_matching_brace(patched, function_open)
        if function_open < 0 or function_close < 0:
            offset = match.end()
            continue
        return_match = re.search(r"\breturn\s*{", patched[function_open:function_close])
        if not return_match:
            offset = function_close + 1
            continue
        object_open = function_open + return_match.end() - 1
        object_close = _find_matching_brace(patched, object_open)
        if object_close < 0 or object_close > function_close:
            offset = function_close + 1
            continue
        before = patched
        patched = _insert_typescript_object_literal_defaults(
            patched,
            open_brace=object_open,
            close_brace=object_close,
            additions=additions,
        )
        if patched != before:
            existing_after = _typescript_object_literal_existing_properties(
                patched[object_open + 1 : _find_matching_brace(patched, object_open)]
            )
            patched_members.update(member for member in additions if member in existing_after)
            offset = object_close + (len(patched) - len(before)) + 1
        else:
            offset = object_close + 1
    return patched, patched_members


def _repair_typescript_unhostable_member_access_defaults(
    *,
    workspace_path: Path,
    missing_members: list[dict[str, str]],
    updated_by_path: dict[Path, str],
) -> list[dict[str, str]]:
    repaired: list[dict[str, str]] = []
    for item in missing_members:
        member = str(item.get("member") or "").strip()
        type_name = str(item.get("type") or "").strip()
        if not member or not type_name:
            continue
        if _typescript_type_has_structural_declaration(
            workspace_path=workspace_path,
            type_name=type_name,
            updated_by_path=updated_by_path,
        ):
            continue
        usage_line = _typescript_error_usage_line(workspace_path, item)
        receiver = _typescript_receiver_for_member_access(usage_line, member)
        if not receiver:
            continue
        parent_type, _property_name = _typescript_parent_type_for_local_symbol(
            workspace_path=workspace_path,
            item=item,
            symbol=receiver,
        )
        if parent_type:
            continue
        source_path, lines, line_no = _typescript_source_lines_for_error_item(workspace_path, item)
        if source_path is None or not lines or line_no < 1 or line_no > len(lines):
            continue
        try:
            source_text = updated_by_path.get(source_path) or source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        line_start, line_end = _typescript_line_bounds(source_text, line_no)
        line = source_text[line_start:line_end]
        default_type = _typescript_member_access_default_type(member, line)
        replacement = _typescript_default_value_for_type(default_type)
        call_default_type = _typescript_unhostable_call_default_type(type_name, default_type)
        call_replacement = _typescript_expression_default_value(call_default_type)
        new_line = _replace_typescript_member_access_with_default(
            line,
            receiver=receiver,
            member=member,
            replacement=replacement,
            call_replacement=call_replacement,
        )
        if new_line == line:
            continue
        updated_by_path[source_path] = source_text[:line_start] + new_line + source_text[line_end:]
        repaired.append(
            {
                "file": source_path.relative_to(workspace_path).as_posix(),
                "type": type_name,
                "member": member,
                "declaration_kind": "unhostable_member_access_default",
            }
        )
    return repaired


def _typescript_declaration_type_name(type_name: str) -> str:
    stripped = str(type_name or "").strip()
    typeof_match = re.match(r"^typeof\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)$", stripped)
    if typeof_match:
        return str(typeof_match.group("name") or "").strip()
    return stripped


def _typescript_static_member_access_context(
    *,
    raw_type_name: str,
    type_name: str,
    member: str,
    usage_line: str,
) -> bool:
    if not str(raw_type_name or "").strip().startswith("typeof "):
        return False
    declaration_type = _typescript_declaration_type_name(type_name)
    safe_member = re.sub(r"[^A-Za-z0-9_$]", "", member)
    if not declaration_type or not safe_member:
        return False
    return bool(re.search(rf"\b{re.escape(declaration_type)}\s*\.\s*{re.escape(safe_member)}\b", usage_line))


def _typescript_type_has_structural_declaration(
    *,
    workspace_path: Path,
    type_name: str,
    updated_by_path: dict[Path, str],
) -> bool:
    declaration_path, _declaration_text, start, end = _find_typescript_class_declaration(
        workspace_path=workspace_path,
        type_name=type_name,
        updated_by_path=updated_by_path,
    )
    if declaration_path is not None and start >= 0 and end >= 0:
        return True
    declaration_path, _declaration_text, start, end = _find_typescript_structural_type_declaration(
        workspace_path=workspace_path,
        type_name=type_name,
        updated_by_path=updated_by_path,
    )
    return declaration_path is not None and start >= 0 and end >= 0


def _typescript_member_access_default_type(member: str, usage_line: str) -> str:
    if _typescript_usage_line_treats_member_as_string(member, usage_line) or _typescript_member_name_suggests_string(
        member
    ):
        return "string"
    if _typescript_usage_line_treats_member_as_number(member, usage_line) or _typescript_member_name_suggests_number(
        member
    ):
        return "number"
    return "unknown"


def _typescript_unhostable_call_default_type(type_name: str, fallback_type: str) -> str:
    declaration_type = _typescript_declaration_type_name(type_name)
    if declaration_type and re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", declaration_type):
        return declaration_type
    return fallback_type.strip() or "unknown"


def _typescript_expression_default_value(ts_type: str) -> str:
    value = _typescript_default_value_for_type(ts_type)
    if " as " in value or value.startswith("{"):
        return f"({value})"
    return value


def _replace_typescript_member_access_with_default(
    line: str,
    *,
    receiver: str,
    member: str,
    replacement: str,
    call_replacement: str,
) -> str:
    access_re = re.compile(rf"\b{re.escape(receiver)}\s*\.\s*{re.escape(member)}\b")
    match = access_re.search(line)
    if not match:
        return line
    cursor = match.end()
    while cursor < len(line) and line[cursor].isspace():
        cursor += 1
    if cursor < len(line) and line[cursor] == "(":
        call_end = _find_matching_paren(line, cursor)
        if call_end >= 0:
            return line[: match.start()] + call_replacement + line[call_end + 1 :]
    return access_re.sub(replacement, line, count=1)


def _find_matching_paren(text: str, open_paren: int) -> int:
    if open_paren < 0 or open_paren >= len(text) or text[open_paren] != "(":
        return -1
    depth = 0
    quote = ""
    escaped = False
    for index in range(open_paren, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in ("'", '"', "`"):
            quote = char
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _typescript_line_bounds(text: str, line_no: int) -> tuple[int, int]:
    starts = _typescript_line_start_offsets(text)
    if line_no < 1 or line_no > len(starts):
        return 0, 0
    start = starts[line_no - 1]
    end = starts[line_no] if line_no < len(starts) else len(text)
    return start, end


def _typescript_structural_member_type_map(declaration_text: str) -> dict[str, str]:
    member_types: dict[str, str] = {}
    for line in declaration_text.splitlines():
        stripped = line.strip()
        method_match = re.match(
            r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\??\s*\((?P<params>[^)]*)\)\s*:\s*(?P<type>.+?)[;,]?$",
            stripped,
        )
        if method_match:
            name = str(method_match.group("name") or "").strip()
            return_type = str(method_match.group("type") or "").strip().rstrip(";,").strip()
            if name and return_type:
                member_types[name] = f"() => {return_type}"
            continue
        if ":" not in line:
            continue
        match = re.match(r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\??\s*:\s*(?P<type>.+?)[;,]?$", stripped)
        if not match:
            continue
        name = str(match.group("name") or "").strip()
        value_type = str(match.group("type") or "").strip().rstrip(";,").strip()
        if name and value_type:
            member_types[name] = value_type
    return member_types


def _find_typescript_object_literal_bounds_for_line(text: str, line_no: int) -> tuple[int, int] | None:
    if line_no < 1:
        return None
    line_starts = _typescript_line_start_offsets(text)
    if line_no > len(line_starts):
        return None
    line_start = line_starts[line_no - 1]
    next_line_start = line_starts[line_no] if line_no < len(line_starts) else len(text)
    search_end = min(len(text), next_line_start + 240)
    candidates: list[int] = []
    inline_open = text.find("{", line_start, search_end)
    if inline_open >= 0:
        candidates.append(inline_open)
    for previous_line_no in range(line_no - 1, max(0, line_no - 8), -1):
        previous_start = line_starts[previous_line_no - 1]
        previous_end = line_starts[previous_line_no] if previous_line_no < len(line_starts) else len(text)
        previous_open = text.find("{", previous_start, previous_end)
        if previous_open >= 0:
            candidates.append(previous_open)
    for open_brace in candidates:
        close_brace = _find_matching_brace(text, open_brace)
        if close_brace > line_start:
            return open_brace, close_brace
    return None


def _typescript_line_start_offsets(text: str) -> list[int]:
    starts = [0]
    for match in re.finditer(r"\n", text):
        starts.append(match.end())
    return starts


def _insert_typescript_object_literal_defaults(
    text: str,
    *,
    open_brace: int,
    close_brace: int,
    additions: dict[str, str],
) -> str:
    body = text[open_brace + 1 : close_brace]
    existing = _typescript_object_literal_existing_properties(body)
    pending = {member: value_type for member, value_type in additions.items() if member not in existing}
    if not pending:
        return text
    indent = _typescript_object_literal_property_indent(text, open_brace, close_brace)
    inserted = "".join(
        f"{indent}{member}: {_typescript_default_value_for_type(value_type)},\n"
        for member, value_type in pending.items()
        if _TS_IDENTIFIER_RE.fullmatch(member)
    )
    if not inserted:
        return text
    return text[:close_brace] + inserted + text[close_brace:]


def _typescript_object_literal_existing_properties(body: str) -> set[str]:
    existing: set[str] = set()
    for line in body.splitlines():
        match = re.match(r"\s*(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*(?::|,)", line)
        if match:
            existing.add(str(match.group("name") or "").strip())
    return existing


def _typescript_object_literal_property_indent(text: str, open_brace: int, close_brace: int) -> str:
    body = text[open_brace + 1 : close_brace]
    for line in body.splitlines():
        if line.strip():
            indent_match = re.match(r"\s*", line)
            return indent_match.group(0) if indent_match else "  "
    line_start = text.rfind("\n", 0, open_brace) + 1
    opening_indent = re.match(r"\s*", text[line_start:open_brace])
    return (opening_indent.group(0) if opening_indent else "") + "  "


def _wrap_typescript_argument_at_column_as_string(line: str, column: int) -> str:
    span = _find_typescript_argument_span_at_column(line, column)
    if span is None:
        return line
    start, end = span
    argument = line[start:end]
    stripped = argument.strip()
    if not stripped or stripped.startswith(("String(", '"', "'", "`")):
        return line
    leading = argument[: len(argument) - len(argument.lstrip())]
    trailing = argument[len(argument.rstrip()) :]
    replacement = f"{leading}String({stripped}){trailing}"
    return line[:start] + replacement + line[end:]


def _find_typescript_argument_span_at_column(line: str, column: int) -> tuple[int, int] | None:
    index = max(0, min(len(line), int(column) - 1))
    open_index = line.rfind("(", 0, index + 1)
    close_index = line.find(")", index)
    if open_index < 0 or close_index < 0 or close_index <= open_index:
        return None
    spans = _split_typescript_argument_spans(line, open_index + 1, close_index)
    for start, end in spans:
        if start <= index <= end:
            arrow_index = line.find("=>", start, end)
            if arrow_index >= 0:
                next_open = line.find("(", arrow_index + 2, end)
                next_close = line.find(")", next_open + 1, end + 1)
                if next_open > arrow_index and next_close > next_open:
                    next_spans = _split_typescript_argument_spans(line, next_open + 1, next_close)
                    return next_spans[0] if next_spans else None
            if line[close_index + 1 :].lstrip().startswith("=>"):
                next_open = line.find("(", close_index + 1)
                next_close = line.find(")", next_open + 1)
                if next_open > close_index and next_close > next_open:
                    next_spans = _split_typescript_argument_spans(line, next_open + 1, next_close)
                    return next_spans[0] if next_spans else None
            return start, end
    return spans[0] if spans else None


def _split_typescript_argument_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    depth = 0
    arg_start = start
    quote = ""
    index = start
    while index < end:
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in {"'", '"', "`"}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            spans.append((arg_start, index))
            arg_start = index + 1
        index += 1
    if arg_start <= end:
        spans.append((arg_start, end))
    return spans


def _typescript_call_name_from_usage_line(usage_line: str, column: int) -> str:
    prefix = usage_line[: max(0, min(len(usage_line), int(column)))]
    matches = list(re.finditer(r"(?:\.|\b)(?P<name>[A-Za-z_$][\w$]*)\s*\(", prefix))
    if not matches:
        matches = list(re.finditer(r"\b(?P<name>[A-Za-z_$][\w$]*)\s*\(", usage_line))
    if not matches:
        return ""
    return str(matches[-1].group("name") or "").strip()


def _repair_typescript_too_few_arguments_callsite(
    *,
    workspace_path: Path,
    item: dict[str, str],
    method_name: str,
    updated_by_path: dict[Path, str],
) -> dict[str, str] | None:
    """Repair conservative callsite arity patterns that should not mutate declarations."""
    if method_name != "clamp" or item.get("expected") != "3" or item.get("got") != "2":
        return None
    raw_file = str(item.get("file") or "").strip()
    if not raw_file:
        return None
    target_path = (workspace_path / raw_file).resolve()
    try:
        target_path.relative_to(workspace_path)
    except ValueError:
        return None
    try:
        text = updated_by_path.get(target_path) or target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    line_index = int(item.get("line") or "0") - 1
    lines = text.splitlines()
    if line_index < 0 or line_index >= len(lines):
        return None
    line = lines[line_index]
    column_index = max(0, int(item.get("col") or "1") - 1)
    for match in re.finditer(r"\bclamp\s*\(", line):
        open_index = line.find("(", match.start())
        close_index = _find_matching_paren(line, open_index)
        if close_index < 0 or not (match.start() <= column_index <= close_index):
            continue
        spans = _split_typescript_argument_spans(line, open_index + 1, close_index)
        if len(spans) != 2:
            continue
        first_arg = line[spans[0][0] : spans[0][1]].strip()
        second_arg = line[spans[1][0] : spans[1][1]].strip()
        if not first_arg or not second_arg:
            continue
        lines[line_index] = line[: open_index + 1] + f"{first_arg}, 0, {second_arg}" + line[close_index:]
        updated_by_path[target_path] = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
        return {
            "file": target_path.relative_to(workspace_path).as_posix(),
            "method": method_name,
            "expected": item["expected"],
            "got": item["got"],
            "repair": "callsite_insert_default_min_bound",
        }
    return None


def _find_unique_typescript_method_declaration(
    *,
    workspace_path: Path,
    method_name: str,
    expected_count: int,
    updated_by_path: dict[Path, str],
) -> tuple[Path, int, str] | None:
    method_re = re.compile(
        rf"^\s*(?:public\s+|private\s+|protected\s+)?(?:async\s+)?{re.escape(method_name)}\s*\((?P<params>[^)]*)\)",
    )
    matches: list[tuple[Path, int, str]] = []
    for path in _iter_typescript_files(workspace_path):
        try:
            text = updated_by_path.get(path) or path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_index, line in enumerate(text.splitlines()):
            match = method_re.search(line)
            if not match:
                continue
            params = _split_typescript_params(match.group("params"))
            if len(params) >= expected_count:
                matches.append((path, line_index, line))
    return matches[0] if len(matches) == 1 else None


def _add_defaults_to_typescript_method_params(line: str, *, got_count: int, expected_count: int) -> str:
    open_index = line.find("(")
    close_index = line.find(")", open_index + 1)
    if open_index < 0 or close_index < 0:
        return line
    params_text = line[open_index + 1 : close_index]
    params = _split_typescript_params(params_text)
    if len(params) < expected_count or got_count >= expected_count:
        return line
    changed = False
    for index in range(got_count, min(expected_count, len(params))):
        repaired = _typescript_param_with_default(params[index])
        if repaired != params[index]:
            params[index] = repaired
            changed = True
    if not changed:
        return line
    return line[: open_index + 1] + ", ".join(params) + line[close_index:]


def _typescript_property_line_with_default(line: str, member: str) -> str:
    safe_member = re.escape(str(member or "").strip())
    if not safe_member or "=" in line or "!" in line:
        return line
    match = re.match(
        rf"^(?P<prefix>\s*(?:(?:public|private|protected)\s+)?(?:readonly\s+)?{safe_member}\s*:\s*)"
        r"(?P<type>[^;=]+)(?P<suffix>;?\s*)$",
        line,
    )
    if not match:
        return line
    field_type = str(match.group("type") or "unknown").strip()
    if not field_type:
        return line
    return (
        f"{match.group('prefix')}{field_type} = {_typescript_default_value_for_type(field_type)}{match.group('suffix')}"
    )


def _split_typescript_params(params_text: str) -> list[str]:
    spans = _split_typescript_argument_spans(params_text, 0, len(params_text))
    return [params_text[start:end].strip() for start, end in spans if params_text[start:end].strip()]


def _typescript_param_with_default(param: str) -> str:
    if "=" in param:
        return param
    if ":" not in param:
        return f"{param} = undefined"
    name, annotation = param.split(":", 1)
    ts_type = annotation.strip()
    return f"{name.strip()}: {ts_type} = {_typescript_default_value_for_type(ts_type)}"


def _typescript_default_value_for_type(ts_type: str) -> str:
    lowered = ts_type.strip().lower()
    function_default = _typescript_default_function_value_for_type(ts_type)
    if function_default:
        return function_default
    if lowered.startswith("{") and lowered.endswith("}"):
        return _typescript_default_object_literal_for_type(ts_type)
    if lowered == "number":
        return "0"
    if lowered == "string":
        return '""'
    if lowered == "boolean":
        return "false"
    if lowered.endswith("[]") or lowered.startswith("array<"):
        return "[]"
    return f"undefined as unknown as {ts_type.strip()}"


def _typescript_default_function_value_for_type(ts_type: str) -> str:
    match = re.match(r"^\s*(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*=>\s*(?P<return>.+?)\s*$", ts_type.strip())
    if not match:
        return ""
    return_type = str(match.group("return") or "unknown").strip()
    return f"() => {_typescript_default_value_for_type(return_type)}"


def _typescript_default_object_literal_for_type(ts_type: str) -> str:
    body = ts_type.strip()[1:-1]
    fields: list[str] = []
    for match in re.finditer(r"\b(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\??\s*:\s*(?P<type>[^;{}]+)", body):
        name = str(match.group("name") or "").strip()
        field_type = str(match.group("type") or "").strip()
        if not name or not field_type:
            continue
        fields.append(f"{name}: {_typescript_default_value_for_type(field_type)}")
    if not fields:
        return "{}"
    return "{ " + ", ".join(fields) + " }"


def _build_typescript_missing_member_declaration(
    *,
    member: str,
    usage_line: str,
    class_text: str,
    inferred_type: str = "",
    static_context: bool = False,
) -> str:
    safe_member = re.sub(r"[^A-Za-z0-9_$]", "", member)
    if not safe_member:
        return ""
    class_name = _typescript_class_name_from_text(class_text)
    if re.search(rf"\.{re.escape(safe_member)}\s*\(", usage_line):
        params = _typescript_method_params_from_usage_line(safe_member, usage_line)
        if static_context and class_name:
            constructor_args = _typescript_constructor_default_arguments(class_text)
            return (
                f"  public static {safe_member}({params}): {class_name} {{\n"
                f"    return new {class_name}({constructor_args});\n"
                "  }"
            )
        return f"  public {safe_member}({params}): number {{\n    return 0;\n  }}"
    if static_context and class_name:
        constructor_args = _typescript_constructor_default_arguments(class_text)
        return f"  public static readonly {safe_member}: {class_name} = new {class_name}({constructor_args});"
    if re.search(rf"\.\s*{re.escape(safe_member)}\s*=(?!=)", usage_line):
        value_type = inferred_type.strip() or _typescript_missing_member_value_type(safe_member, usage_line, class_text)
        return f"  public {safe_member}: {value_type} = {_typescript_default_value_for_type(value_type)};"
    if re.search(rf"\.{re.escape(safe_member)}\s*\.length\b", usage_line):
        fallback = "this.id" if re.search(r"\bid\s*:", class_text) else '""'
        return f"  public get {safe_member}(): string {{\n    return {fallback};\n  }}"
    if _typescript_usage_line_treats_member_as_string(safe_member, usage_line):
        return f'  public get {safe_member}(): string {{\n    return "";\n  }}'
    if _typescript_usage_line_treats_member_as_number(safe_member, usage_line):
        return f"  public get {safe_member}(): number {{\n    return 0;\n  }}"
    value_type = inferred_type.strip() or "unknown"
    return f"  public get {safe_member}(): {value_type} {{\n    return {_typescript_default_value_for_type(value_type)};\n  }}"


def _typescript_class_name_from_text(class_text: str) -> str:
    match = re.search(r"\bclass\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\b", class_text)
    return str(match.group("name") or "").strip() if match else ""


def _typescript_constructor_default_arguments(class_text: str) -> str:
    match = re.search(r"\bconstructor\s*\((?P<params>[^)]*)\)", class_text, re.DOTALL)
    if not match:
        return ""
    defaults: list[str] = []
    for raw_param in str(match.group("params") or "").split(","):
        param = raw_param.strip()
        if not param:
            continue
        type_match = re.search(r":\s*(?P<type>[^=,]+)", param)
        param_type = str(type_match.group("type") or "unknown").strip() if type_match else "unknown"
        defaults.append(_typescript_expression_default_value(param_type))
    return ", ".join(defaults)


def _build_typescript_missing_member_signature(
    *,
    member: str,
    usage_line: str,
    declaration_text: str,
    inferred_type: str = "",
) -> str:
    safe_member = re.sub(r"[^A-Za-z0-9_$]", "", member)
    if not safe_member:
        return ""
    if re.search(rf"\.{re.escape(safe_member)}\s*\(", usage_line):
        params = _typescript_method_params_from_usage_line(safe_member, usage_line)
        return f"  {safe_member}({params}): number;"
    return (
        f"  {safe_member}: "
        f"{_typescript_missing_member_value_type(safe_member, usage_line, declaration_text, inferred_type)};"
    )


def _typescript_missing_member_value_type(
    member: str,
    usage_line: str,
    declaration_text: str,
    inferred_type: str = "",
) -> str:
    del declaration_text
    if inferred_type.strip():
        return inferred_type.strip()
    if re.search(rf"\.{re.escape(member)}\s*\.length\b", usage_line):
        return "string"
    if _typescript_usage_line_treats_member_as_string(member, usage_line):
        return "string"
    if _typescript_member_name_suggests_string(member):
        return "string"
    if _typescript_usage_line_treats_member_as_number(member, usage_line):
        return "number"
    if _typescript_member_name_suggests_number(member):
        return "number"
    return "unknown"


def _typescript_member_name_suggests_string(member: str) -> bool:
    return member.strip().lower() in _TS_STRINGISH_MEMBER_NAMES


def _typescript_member_name_suggests_number(member: str) -> bool:
    lowered = member.strip().lower()
    return (
        lowered in _TS_NUMERIC_MEMBER_NAMES
        or lowered.endswith(("count", "index", "size", "width", "height", "radius", "angle"))
        or lowered.startswith(("x", "y"))
    )


def _typescript_usage_line_treats_member_as_string(member: str, usage_line: str) -> bool:
    member_access = rf"\.{re.escape(member)}\b"
    comparison = r"(?:={2,3}|!==?)"
    string_literal = r"['\"]"
    stringish_identifier = r"[A-Za-z_$][A-Za-z0-9_$]*(?:Id|ID|Name|Key)\b"
    member_name_is_stringish = _typescript_member_name_suggests_string(member) or bool(
        re.search(r"(?:id|name|key)$", member, re.IGNORECASE)
    )
    return bool(
        re.search(rf"{member_access}\s*{comparison}\s*{string_literal}", usage_line)
        or re.search(rf"{string_literal}[^'\"]*{comparison}\s*[^;\n]*{member_access}", usage_line)
        or (
            member_name_is_stringish
            and (
                re.search(rf"{member_access}\s*{comparison}\s*{stringish_identifier}", usage_line)
                or re.search(rf"{stringish_identifier}\s*{comparison}\s*[^;\n]*{member_access}", usage_line)
            )
        )
    )


def _typescript_usage_line_treats_member_as_number(member: str, usage_line: str) -> bool:
    member_access = rf"\.{re.escape(member)}\b"
    return bool(
        re.search(rf"{member_access}\s*(?:[*/%+\-]|[<>]=?)", usage_line)
        or re.search(rf"(?:[*/%+\-]|[<>]=?)\s*[^;\n]*{member_access}", usage_line)
    )


def _typescript_method_params_from_usage_line(member: str, usage_line: str) -> str:
    match = re.search(rf"\.{re.escape(member)}\s*\((?P<args>[^)]*)\)", usage_line)
    if not match:
        return ""
    args = _split_typescript_params(str(match.group("args") or ""))
    return ", ".join(f"_arg{index}: unknown" for index, _arg in enumerate(args))


def _resolve_case_variant_relative_path(root: Path, relative_path: str) -> str:
    current = root
    parts = [part for part in Path(relative_path).parts if part not in {"", "."}]
    resolved_parts: list[str] = []
    for part in parts:
        candidate = current / part
        if candidate.exists():
            current = candidate
            resolved_parts.append(part)
            continue
        if not current.is_dir():
            return ""
        try:
            match = next((item for item in current.iterdir() if item.name.lower() == part.lower()), None)
        except OSError:
            return ""
        if match is None:
            return ""
        current = match
        resolved_parts.append(match.name)
    try:
        resolved = current.resolve()
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return ""
    return "/".join(resolved_parts)


def _relative_import_specifier_for_actual_path(
    *,
    root: Path,
    importer_rel: str,
    original_specifier: str,
    actual_target_rel: str,
) -> str:
    importer_path = (root / importer_rel).resolve()
    actual_target_path = (root / actual_target_rel).resolve()
    relative = os.path.relpath(actual_target_path, importer_path.parent).replace(os.sep, "/")
    if not relative.startswith("."):
        relative = f"./{relative}"
    if not Path(original_specifier).suffix:
        suffix_order = _relative_import_suffix_order(importer_rel)
        for suffix in suffix_order:
            if relative.endswith(suffix):
                relative = relative[: -len(suffix)]
                break
    return relative


_TS_IMPORT_FROM_SPECIFIER_TEMPLATE = (
    r"(?m)^(?P<indent>\s*)import\s+(?P<clause>.*?)\s+from\s+"
    r"(?P<quote>['\"]){specifier}(?P=quote)\s*;?\s*$"
)


def _typescript_import_pairs_for_specifier(content: str, specifier: str) -> list[tuple[str, str]]:
    pattern = re.compile(_TS_IMPORT_FROM_SPECIFIER_TEMPLATE.format(specifier=re.escape(specifier)))
    match = pattern.search(content)
    if not match:
        return []
    clause = str(match.group("clause") or "").strip()
    if clause.startswith("type "):
        clause = clause[5:].strip()
    pairs: list[tuple[str, str]] = []
    namespace_match = re.fullmatch(r"\*\s+as\s+([A-Za-z_$][\w$]*)", clause)
    if namespace_match:
        return []
    if clause.startswith("{") and clause.endswith("}"):
        named_clause = clause[1:-1]
        default_clause = ""
    elif ",{" in clause.replace(" ", ""):
        default_clause, named_clause = clause.split(",", 1)
        named_clause = named_clause.strip()
        named_clause = named_clause[1:-1] if named_clause.startswith("{") and named_clause.endswith("}") else ""
    else:
        default_clause = clause
        named_clause = ""

    default_name = default_clause.strip()
    if _TS_IDENTIFIER_RE.fullmatch(default_name):
        pairs.append(("default", default_name))

    for raw_part in named_clause.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if part.startswith("type "):
            part = part[5:].strip()
        alias_parts = re.split(r"\s+as\s+", part, maxsplit=1, flags=re.IGNORECASE)
        imported = alias_parts[0].strip()
        local = alias_parts[-1].strip()
        if _TS_IDENTIFIER_RE.fullmatch(imported) and _TS_IDENTIFIER_RE.fullmatch(local):
            pairs.append((imported, local))
    return pairs


def _typescript_import_statement_for_specifier(content: str, specifier: str) -> re.Match[str] | None:
    pattern = re.compile(_TS_IMPORT_FROM_SPECIFIER_TEMPLATE.format(specifier=re.escape(specifier)))
    return pattern.search(content)


def _typescript_identifier_used_outside_span(content: str, name: str, span: tuple[int, int]) -> bool:
    if not _TS_IDENTIFIER_RE.fullmatch(name):
        return False
    outside = content[: span[0]] + content[span[1] :]
    return re.search(rf"\b{re.escape(name)}\b", outside) is not None


def _remove_unused_typescript_import(content: str, specifier: str) -> str:
    match = _typescript_import_statement_for_specifier(content, specifier)
    if match is None:
        return content
    pairs = _typescript_import_pairs_for_specifier(content, specifier)
    if not pairs:
        return content
    span = match.span()
    if any(_typescript_identifier_used_outside_span(content, local, span) for _, local in pairs):
        return content
    start, end = span
    if end < len(content) and content[end : end + 1] == "\n":
        end += 1
    return content[:start] + content[end:]


def _find_unique_typescript_export_for_import(
    *,
    workspace_path: Path,
    importer_path: Path,
    content: str,
    specifier: str,
) -> Path | None:
    match = _typescript_import_statement_for_specifier(content, specifier)
    if match is None:
        return None
    pairs = _typescript_import_pairs_for_specifier(content, specifier)
    needed_symbols = [
        imported
        for imported, local in pairs
        if imported != "default" and _typescript_identifier_used_outside_span(content, local, match.span())
    ]
    if not needed_symbols:
        return None

    candidates: list[Path] = []
    for candidate in workspace_path.rglob("*.ts"):
        if candidate == importer_path or candidate.name.endswith(".d.ts"):
            continue
        try:
            candidate.relative_to(workspace_path)
            candidate_text = candidate.read_text(encoding="utf-8")
        except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
            continue
        if all(_typescript_module_runtime_exports_symbol(candidate_text, symbol) for symbol in needed_symbols):
            candidates.append(candidate)
    return candidates[0] if len(candidates) == 1 else None


def _apply_deterministic_typescript_relative_import_case_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    executor = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    )
    results: list[dict[str, Any]] = []
    rewritten_importers: set[str] = set()
    for error in artifact_quality_errors:
        match = _UNRESOLVED_RELATIVE_IMPORT_ERROR_RE.search(str(error or ""))
        if not match:
            continue
        specifier = str(match.group("specifier") or "").strip()
        importer_rel = _normalize_declared_task_path(match.group("path"))
        if not specifier.startswith(".") or not importer_rel or importer_rel in rewritten_importers:
            continue
        importer_path = (workspace_path / importer_rel).resolve()
        try:
            importer_path.relative_to(workspace_path)
            content = importer_path.read_text(encoding="utf-8")
        except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
            continue
        candidates = _relative_import_repair_target_candidates(
            root=workspace_path,
            importer_rel=importer_rel,
            specifier=specifier,
        )
        actual_target_rel = ""
        for candidate in candidates:
            if (workspace_path / candidate).exists():
                actual_target_rel = candidate
                break
            case_variant = _resolve_case_variant_relative_path(workspace_path, candidate)
            if case_variant and case_variant != candidate:
                actual_target_rel = case_variant
                break
        if not actual_target_rel:
            updated = _remove_unused_typescript_import(content, specifier)
            source_tool = "deterministic_typescript_unused_import_repair"
            metadata: dict[str, Any] = {"specifier": specifier}
            if updated == content:
                source_path = _find_unique_typescript_export_for_import(
                    workspace_path=workspace_path,
                    importer_path=importer_path,
                    content=content,
                    specifier=specifier,
                )
                if source_path is None:
                    continue
                actual_target_rel = source_path.relative_to(workspace_path).as_posix()
                corrected_specifier = _relative_import_specifier_for_actual_path(
                    root=workspace_path,
                    importer_rel=importer_rel,
                    original_specifier=specifier,
                    actual_target_rel=actual_target_rel,
                )
                if corrected_specifier == specifier:
                    continue
                updated = content.replace(f"'{specifier}'", f"'{corrected_specifier}'").replace(
                    f'"{specifier}"',
                    f'"{corrected_specifier}"',
                )
                source_tool = "deterministic_typescript_unique_export_import_repair"
                metadata = {
                    "specifier": specifier,
                    "corrected_specifier": corrected_specifier,
                    "target_file": actual_target_rel,
                }
            write_result = executor.execute_tool(
                "write_file",
                {"file": importer_rel, "content": updated},
                task_id=task_id,
            )
            if not bool(write_result.get("ok")):
                continue
            rewritten_importers.add(importer_rel)
            with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
                adapter._update_task_progress(task_id, "executing", current_file=importer_rel)
            results.append(
                {
                    "tool": "write_file",
                    "tool_name": "write_file",
                    "success": True,
                    "result": {
                        "ok": True,
                        "source_tool": source_tool,
                        "file": importer_rel,
                        **metadata,
                        "bytes_written": int(write_result.get("bytes_written") or len(updated.encode("utf-8"))),
                        "operation": str(write_result.get("operation") or "overwrite"),
                        "broadcast_ok": bool(write_result.get("broadcast_ok")),
                        "director_policy": write_result.get("director_policy"),
                    },
                }
            )
            continue
        corrected_specifier = _relative_import_specifier_for_actual_path(
            root=workspace_path,
            importer_rel=importer_rel,
            original_specifier=specifier,
            actual_target_rel=actual_target_rel,
        )
        if corrected_specifier == specifier:
            continue
        updated = content.replace(f"'{specifier}'", f"'{corrected_specifier}'").replace(
            f'"{specifier}"',
            f'"{corrected_specifier}"',
        )
        if updated == content:
            continue
        write_result = executor.execute_tool(
            "write_file",
            {"file": importer_rel, "content": updated},
            task_id=task_id,
        )
        if not bool(write_result.get("ok")):
            continue
        rewritten_importers.add(importer_rel)
        with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
            adapter._update_task_progress(task_id, "executing", current_file=importer_rel)
        results.append(
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_typescript_relative_import_case_repair",
                    "file": importer_rel,
                    "specifier": specifier,
                    "corrected_specifier": corrected_specifier,
                    "target_file": actual_target_rel,
                    "bytes_written": int(write_result.get("bytes_written") or len(updated.encode("utf-8"))),
                    "operation": str(write_result.get("operation") or "overwrite"),
                    "broadcast_ok": bool(write_result.get("broadcast_ok")),
                    "director_policy": write_result.get("director_policy"),
                },
            }
        )
    return results


def _typescript_brace_balance_delta(source: str) -> int:
    depth = 0
    quote: str | None = None
    escaped = False
    line_comment = False
    block_comment = False
    index = 0
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
                index += 2
                continue
            index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char == "/" and next_char == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and next_char == "*":
            block_comment = True
            index += 2
            continue
        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    return depth


def _repair_typescript_missing_closing_braces(source: str) -> str:
    missing_count = _typescript_brace_balance_delta(source)
    if missing_count <= 0 or missing_count > 8:
        return source
    repaired = source.rstrip() + "\n"
    repaired += "\n".join("}" for _ in range(missing_count))
    return repaired + "\n"


def _apply_deterministic_typescript_unresolved_identifier_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    unresolved = _parse_typescript_cannot_find_name_errors(artifact_quality_errors)
    if not unresolved:
        return []

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    updated_by_path: dict[Path, str] = {}
    replacements: list[dict[str, str]] = []
    for item in unresolved:
        relative_path = item["file"]
        full_path = (workspace_path / relative_path).resolve()
        if not _path_inside_workspace(full_path, workspace_path) or not full_path.is_file():
            continue
        try:
            original = updated_by_path.get(full_path) or full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            line_number = int(item["line"])
        except ValueError:
            continue
        repaired, replacement = _repair_typescript_unresolved_identifier_lines(
            original,
            target_line_number=line_number,
            missing_symbol=item["symbol"],
        )
        if repaired == original or not replacement:
            continue
        updated_by_path[full_path] = repaired
        replacements.append(
            {
                "file": relative_path,
                "line": item["line"],
                "col": item["col"],
                "symbol": item["symbol"],
                "replacement": replacement,
            }
        )

    return _write_typescript_repair_results(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        updated_by_path=updated_by_path,
        source_tool="deterministic_typescript_unresolved_identifier_repair",
        metadata_key="identifiers",
        metadata_value=replacements,
    )


def _repair_typescript_unresolved_identifier_lines(
    text: str,
    *,
    target_line_number: int,
    missing_symbol: str,
) -> tuple[str, str]:
    if target_line_number <= 0 or not _TS_IDENTIFIER_RE.fullmatch(missing_symbol):
        return str(text or ""), ""
    lines = str(text or "").splitlines(keepends=True)
    target_index = target_line_number - 1
    if target_index < 0 or target_index >= len(lines):
        return str(text or ""), ""
    replacement = _select_typescript_unresolved_identifier_replacement(lines, target_index, missing_symbol)
    if not replacement:
        return str(text or ""), ""

    line = lines[target_index]
    repaired_line = re.sub(rf"\b{re.escape(missing_symbol)}\b", replacement, line)
    if repaired_line == line:
        return str(text or ""), ""
    lines[target_index] = repaired_line
    return "".join(lines), replacement


def _select_typescript_unresolved_identifier_replacement(
    lines: list[str],
    target_index: int,
    missing_symbol: str,
) -> str:
    params = _typescript_function_param_names_for_line(lines, target_index)
    for param in params:
        if _typescript_identifier_alias_matches(missing_symbol, param):
            return param
    return ""


def _typescript_function_param_names_for_line(lines: list[str], target_index: int) -> list[str]:
    for start_index in range(target_index, -1, -1):
        line_body = lines[start_index].rstrip("\r\n")
        match = _TS_FUNCTION_DECLARATION_LINE_RE.match(line_body) or _TS_ARROW_FUNCTION_DECLARATION_LINE_RE.match(
            line_body
        )
        if not match:
            continue
        if not _typescript_line_is_inside_scope(lines, start_index, target_index):
            continue
        return _parse_typescript_param_names(str(match.group("params") or ""))
    return []


def _typescript_line_is_inside_scope(lines: list[str], start_index: int, target_index: int) -> bool:
    depth = 0
    for index in range(start_index, target_index + 1):
        line_body = lines[index].rstrip("\r\n")
        depth += line_body.count("{")
        depth -= line_body.count("}")
        if index < target_index and depth <= 0:
            return False
    return depth > 0


def _parse_typescript_param_names(params_text: str) -> list[str]:
    names: list[str] = []
    for raw_param in params_text.split(","):
        param = raw_param.strip()
        if not param:
            continue
        param = param.split("=", 1)[0].split(":", 1)[0].strip()
        param = param.removeprefix("...").strip()
        if _TS_IDENTIFIER_RE.fullmatch(param):
            names.append(param)
    return names


def _typescript_identifier_alias_matches(missing_symbol: str, candidate: str) -> bool:
    missing_lower = missing_symbol.lower()
    candidate_lower = candidate.lower()
    if not candidate_lower or missing_lower == candidate_lower:
        return False
    prefixes = ("new", "next", "updated", "current", "previous", "prev")
    return any(missing_lower == f"{prefix}{candidate_lower}" for prefix in prefixes)


def _repair_typescript_enum_member_separator_lines(text: str, target_line_numbers: set[int]) -> str:
    if not target_line_numbers:
        return str(text or "")
    lines = str(text or "").splitlines(keepends=True)
    repaired: list[str] = []
    brace_depth = 0
    enum_depth: int | None = None
    changed = False
    for line_number, line in enumerate(lines, start=1):
        line_body = line.rstrip("\r\n")
        newline = line[len(line_body) :]
        line_to_append = line
        if enum_depth is not None and line_number in target_line_numbers:
            repaired_line = _repair_typescript_enum_member_line(line_body)
            if repaired_line != line_body:
                line_to_append = repaired_line + newline
                changed = True
        repaired.append(line_to_append)

        opens = line_body.count("{")
        closes = line_body.count("}")
        if enum_depth is None and _TS_ENUM_DECLARATION_LINE_RE.search(line_body):
            enum_depth = brace_depth + max(opens, 1)
        brace_depth += opens - closes
        if enum_depth is not None and brace_depth < enum_depth:
            enum_depth = None
    return "".join(repaired) if changed else str(text or "")


def _repair_typescript_enum_member_line(line_body: str) -> str:
    match = _TS_ENUM_MEMBER_LINE_RE.match(line_body)
    if not match:
        return line_body
    if match.group("separator") == ",":
        return line_body
    prefix = str(match.group("prefix") or "").rstrip()
    if not prefix or prefix.lstrip().startswith(("enum ", "export ")):
        return line_body
    comment = str(match.group("comment") or "")
    space = " " if comment else str(match.group("space") or "")
    return f"{prefix},{space}{comment}"


def _repair_typescript_return_object_semicolon_lines(text: str) -> str:
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
        in_object_literal = bool(object_literal_depths) or starts_object_literal
        if in_object_literal:
            match = _TS_OBJECT_PROPERTY_SEMICOLON_LINE_RE.match(line_body)
            if match:
                repaired.append(f"{match.group('indent')}{match.group('name')},{newline}")
                changed = True
            else:
                value_match = _TS_OBJECT_PROPERTY_VALUE_SEMICOLON_LINE_RE.match(line_body)
                if value_match:
                    repaired.append(f"{value_match.group('indent')}{value_match.group('property').rstrip()},{newline}")
                    changed = True
                else:
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


def _typescript_relative_import_without_suffix(
    *,
    importer_relative_path: str,
    imported_relative_path: str,
    workspace_path: Path,
) -> str:
    importer_path = (workspace_path / importer_relative_path).resolve()
    imported_path = (workspace_path / imported_relative_path).resolve().with_suffix("")
    module_ref = os.path.relpath(imported_path, importer_path.parent).replace("\\", "/")
    if not module_ref.startswith("."):
        module_ref = f"./{module_ref}"
    return module_ref


def _iter_typescript_files(workspace_path: Path) -> list[Path]:
    ignored = {".git", ".polaris", "node_modules", "dist", "build", ".vite", ".pytest_cache"}
    results: list[Path] = []
    for path in workspace_path.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".ts", ".tsx"}:
            continue
        rel = path.relative_to(workspace_path)
        if any(part in ignored for part in rel.parts):
            continue
        results.append(path)
    return sorted(results, key=lambda item: item.as_posix())


def _resolve_relative_ts_module(importer: Path, module_ref: str, workspace_path: Path) -> Path | None:
    if not str(module_ref or "").startswith("."):
        return None
    base = (importer.parent / module_ref).resolve()
    candidates: list[Path] = []
    if base.suffix:
        candidates.append(base)
    else:
        candidates.extend(
            [
                base.with_suffix(".ts"),
                base.with_suffix(".tsx"),
                base / "index.ts",
                base / "index.tsx",
            ]
        )
    for candidate in candidates:
        resolved = candidate.resolve()
        if not _path_inside_workspace(resolved, workspace_path):
            continue
        if resolved.exists() and resolved.is_file():
            return resolved
    return None


def _resolve_typescript_export_target_for_error(
    *,
    importer_path: Path,
    module_ref: str,
    workspace_path: Path,
) -> Path | None:
    resolved = _resolve_relative_ts_module(importer_path, module_ref, workspace_path)
    if resolved is not None:
        return resolved
    if not str(module_ref or "").startswith("."):
        return None

    try:
        base = (importer_path.parent / module_ref).resolve()
        base.relative_to(workspace_path)
    except (OSError, RuntimeError, ValueError):
        return None

    candidates: list[Path] = []
    suffix_order = _relative_import_suffix_order(importer_path.relative_to(workspace_path).as_posix())
    if base.suffix:
        candidates.append(base)
        if base.suffix.lower() in {".js", ".jsx", ".mjs", ".cjs"}:
            candidates.extend(base.with_suffix(suffix) for suffix in suffix_order)
    else:
        candidates.extend(base.with_suffix(suffix) for suffix in suffix_order)
        candidates.extend(base / f"index{suffix}" for suffix in suffix_order)

    for candidate in _dedupe_paths(candidates):
        resolved_candidate = candidate.resolve()
        if _path_inside_workspace(resolved_candidate, workspace_path) and resolved_candidate.is_file():
            return resolved_candidate
    return None


def _typescript_module_runtime_exports_symbol(module_text: str, symbol: str) -> bool:
    escaped = re.escape(symbol)
    if re.search(_TS_RUNTIME_EXPORT_TEMPLATE.format(symbol=escaped), module_text):
        return True
    export_block_re = re.compile(r"export\s*\{(?P<symbols>[^}]+)\}", re.DOTALL)
    for match in export_block_re.finditer(module_text):
        if symbol in _parse_named_export_symbols(match.group("symbols")):
            return True
    return False


def _parse_named_export_symbols(symbols_text: str) -> list[str]:
    symbols: list[str] = []
    for raw in str(symbols_text or "").replace("\n", " ").split(","):
        token = raw.strip()
        if token.startswith("type "):
            token = token[5:].strip()
        parts = re.split(r"\s+as\s+", token, maxsplit=1)
        exported = parts[-1].strip()
        if _TS_IDENTIFIER_RE.fullmatch(exported):
            symbols.append(exported)
    return _dedupe_preserve_order(symbols)


def _typescript_module_exports_symbol(module_text: str, symbol: str) -> bool:
    if _typescript_module_runtime_exports_symbol(module_text, symbol):
        return True
    escaped = re.escape(symbol)
    return bool(re.search(rf"export\s+(?:interface|type)\s+{escaped}\b", module_text))


def _typescript_module_declares_symbol(module_text: str, symbol: str) -> bool:
    return bool(_typescript_module_declared_symbol_kind(module_text, symbol))


def _typescript_module_declared_symbol_kind(module_text: str, symbol: str) -> str:
    if not _TS_IDENTIFIER_RE.fullmatch(symbol):
        return ""
    escaped = re.escape(symbol)
    declaration_re = re.compile(
        rf"^(?:export\s+)?(?:abstract\s+)?(?:async\s+)?"
        rf"(?P<kind>enum|class|interface|type|const|let|var|function)\s+{escaped}\b",
        re.MULTILINE,
    )
    match = declaration_re.search(module_text)
    return str(match.group("kind") or "").strip() if match else ""


def _find_typescript_runtime_symbol_source(
    *,
    workspace_path: Path,
    module_path: Path,
    module_text: str,
    symbol: str,
) -> Path | None:
    candidates: list[Path] = []
    for module_ref in _extract_relative_import_refs(module_text):
        candidate = _resolve_relative_ts_module(module_path, module_ref, workspace_path)
        if candidate is not None and candidate != module_path:
            candidates.append(candidate)
    candidates.extend(
        path
        for path in sorted(module_path.parent.glob("*.ts"))
        if path != module_path and path.name != module_path.name and not path.name.endswith(".test.ts")
    )
    candidates.extend(
        path
        for path in sorted(module_path.parent.glob("*.tsx"))
        if path != module_path and path.name != module_path.name and not path.name.endswith(".test.tsx")
    )
    for candidate in _dedupe_paths(candidates):
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _typescript_file_declares_runtime_export(text, symbol):
            return candidate
    return None


def _extract_relative_import_refs(text: str) -> list[str]:
    refs: list[str] = []
    for match in re.finditer(r"from\s*['\"](?P<module>\.{1,2}/[^'\"]+)['\"]", str(text or "")):
        refs.append(match.group("module"))
    for match in re.finditer(r"import\s*['\"](?P<module>\.{1,2}/[^'\"]+)['\"]", str(text or "")):
        refs.append(match.group("module"))
    return _dedupe_preserve_order(refs)


def _typescript_file_declares_runtime_export(text: str, symbol: str) -> bool:
    escaped = re.escape(symbol)
    return bool(re.search(_TS_RUNTIME_EXPORT_TEMPLATE.format(symbol=escaped), text))


def _export_existing_typescript_declaration(text: str, symbol: str) -> str:
    escaped = re.escape(symbol)
    declaration_re = re.compile(
        rf"(?m)^(?P<indent>\s*)(?P<declare>declare\s+)?"
        rf"(?P<kind>(?:abstract\s+)?class|function|interface|type|const|let|var|enum)\s+{escaped}\b"
    )

    def _replace(match: re.Match[str]) -> str:
        declare = str(match.group("declare") or "")
        return f"{match.group('indent')}export {declare}{match.group('kind')} {symbol}"

    return declaration_re.sub(_replace, text, count=1)


def _build_typescript_missing_export_declaration(*, symbol: str, importer_text: str) -> tuple[str, str]:
    if not _TS_IDENTIFIER_RE.fullmatch(symbol):
        return "", ""
    if _typescript_symbol_is_constructed(importer_text, symbol):
        return "class", _build_typescript_missing_export_class_declaration(symbol=symbol, importer_text=importer_text)
    if _typescript_symbol_is_called(importer_text, symbol):
        return "function", (f"export function {symbol}(..._args: unknown[]): any {{\n  return undefined;\n}}")
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


def _find_typescript_similar_runtime_declaration(module_text: str, symbol: str) -> str:
    """Find a narrow alias candidate for generated import/export name drift."""

    wanted = _normalize_typescript_identifier_for_similarity(symbol)
    if not wanted:
        return ""
    declaration_re = re.compile(
        r"^(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var|enum)\s+"
        r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\b",
        re.MULTILINE,
    )
    best = ""
    best_score = 0
    for match in declaration_re.finditer(module_text):
        name = str(match.group("name") or "").strip()
        if name == symbol:
            continue
        candidate = _normalize_typescript_identifier_for_similarity(name)
        if not candidate:
            continue
        score = 0
        if wanted.startswith(candidate):
            score = len(candidate)
        elif candidate.startswith(wanted):
            score = len(wanted)
        if score > best_score and score >= min(4, len(wanted)):
            best = name
            best_score = score
    return best


def _normalize_typescript_identifier_for_similarity(symbol: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "", str(symbol or "")).lower()
    for suffix in ("checks", "check", "results", "result", "items", "item"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix) + 2:
            normalized = normalized[: -len(suffix)]
            break
    return normalized


def _typescript_symbol_is_constructed(text: str, symbol: str) -> bool:
    return bool(re.search(rf"\bnew\s+{re.escape(symbol)}\s*\(", str(text or "")))


def _typescript_symbol_is_called(text: str, symbol: str) -> bool:
    token = str(text or "")
    call_re = re.compile(rf"(?<!new\s)\b{re.escape(symbol)}\s*\(")
    return bool(call_re.search(token))


def _build_typescript_missing_export_class_declaration(*, symbol: str, importer_text: str) -> str:
    methods = _typescript_methods_used_on_constructed_symbol(importer_text, symbol)
    lines = [
        f"export class {symbol} {{",
        "  public constructor(..._args: unknown[]) {}",
    ]
    for method in methods:
        return_type = "string" if method in {"report", "render", "toString"} else "any"
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


def _typescript_methods_used_on_constructed_symbol(text: str, symbol: str) -> list[str]:
    token = str(text or "")
    variables: list[str] = []
    constructed_var_re = re.compile(
        rf"\b(?:const|let|var)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*new\s+{re.escape(symbol)}\s*\("
    )
    for match in constructed_var_re.finditer(token):
        variables.append(str(match.group("name") or ""))

    methods: list[str] = []
    for variable in _dedupe_preserve_order([name for name in variables if name]):
        for match in re.finditer(rf"\b{re.escape(variable)}\.(?P<method>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(", token):
            methods.append(str(match.group("method") or ""))
    direct_re = re.compile(rf"\bnew\s+{re.escape(symbol)}\s*\([^)]*\)\s*\.\s*(?P<method>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(")
    for match in direct_re.finditer(token):
        methods.append(str(match.group("method") or ""))
    return _dedupe_preserve_order([method for method in methods if method and method != "constructor"])


def _build_typescript_reexport_line(*, module_path: Path, source_path: Path, symbol: str) -> str:
    relative = os.path.relpath(source_path.with_suffix(""), module_path.parent).replace("\\", "/")
    if not relative.startswith("."):
        relative = f"./{relative}"
    return f"export {{ {symbol} }} from '{relative}';"
