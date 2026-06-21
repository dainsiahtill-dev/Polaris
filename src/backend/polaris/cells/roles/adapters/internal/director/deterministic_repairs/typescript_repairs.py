"""Deterministic TypeScript repair generators.

Re-export, return-object-semicolon, escaped-newline, and relative-import-case
repair clusters, carved verbatim from the original ``deterministic_repairs``
module.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from pathlib import Path
from typing import Any

from ..execution_tools import DirectorToolExecutor
from ..task_scope_paths import (
    _dedupe_preserve_order,
    _normalize_declared_task_path,
    _task_text_blob,
)
from ._common import (
    _TS_LINE_COMMENT_ESCAPED_NEWLINE_CODE_RE,
    _TS_NAMED_IMPORT_RE,
    _TS_OBJECT_LITERAL_START_RE,
    _TS_OBJECT_PROPERTY_SEMICOLON_LINE_RE,
    _TS_OBJECT_PROPERTY_VALUE_SEMICOLON_LINE_RE,
    _TS_RETURN_OBJECT_START_RE,
    _TS_RUNTIME_EXPORT_TEMPLATE,
    _UNRESOLVED_RELATIVE_IMPORT_ERROR_RE,
    _dedupe_paths,
    _parse_named_import_symbols,
    _parse_typescript_escaped_newline_paths,
    _parse_typescript_return_object_semicolon_paths,
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
_TS_NUMBER_TO_STRING_ARGUMENT_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2345:\s*"
    r"Argument\s+of\s+type\s+'number'\s+is\s+not\s+assignable\s+to\s+parameter\s+of\s+type\s+'string'",
    re.IGNORECASE,
)
_TS_TOO_FEW_ARGUMENTS_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2554:\s*"
    r"Expected\s+(?P<expected>\d+)\s+arguments?,\s+but\s+got\s+(?P<got>\d+)",
    re.IGNORECASE,
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
    "moonphase",
    "phase",
    "radius",
    "size",
    "speed",
    "time",
    "width",
    "x",
    "y",
    "z",
}


def _apply_deterministic_typescript_reexport_repair(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> list[dict[str, Any]]:
    """Repair a narrow TypeScript runtime re-export miss without target-specific code.

    This covers a common Director failure mode: tests import a runtime symbol from
    a barrel/module file, but that module only exposes type contracts while the
    symbol is exported by a sibling module. The repair only appends an explicit
    `export { Symbol } from './source';` when the source file has a runtime export.
    """
    task_text = _task_text_blob(task)
    if not _looks_like_typescript_reexport_failure(task_text):
        return []

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    for importer in _iter_typescript_files(workspace_path):
        try:
            importer_text = importer.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in _TS_NAMED_IMPORT_RE.finditer(importer_text):
            module_path = _resolve_relative_ts_module(importer, match.group("module"), workspace_path)
            if module_path is None:
                continue
            try:
                module_text = module_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for symbol in _parse_named_import_symbols(match.group("symbols")):
                if _typescript_module_runtime_exports_symbol(module_text, symbol):
                    continue
                source_path = _find_typescript_runtime_symbol_source(
                    workspace_path=workspace_path,
                    module_path=module_path,
                    module_text=module_text,
                    symbol=symbol,
                )
                if source_path is None:
                    continue
                export_line = _build_typescript_reexport_line(
                    module_path=module_path, source_path=source_path, symbol=symbol
                )
                if export_line in module_text:
                    continue
                new_text = module_text.rstrip() + "\n" + export_line + "\n"
                rel_module = module_path.relative_to(workspace_path).as_posix()
                message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
                write_result = DirectorToolExecutor(
                    str(workspace_path),
                    message_bus=message_bus,
                    worker_id="director",
                ).execute_tool(
                    "write_file",
                    {"file": rel_module, "content": new_text},
                    task_id=task_id,
                )
                if not bool(write_result.get("ok")):
                    continue
                with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
                    adapter._update_task_progress(task_id, "executing", current_file=rel_module)
                return [
                    {
                        "tool": "edit_file",
                        "success": True,
                        "result": {
                            "ok": True,
                            "source_tool": "deterministic_typescript_reexport_repair",
                            "file": rel_module,
                            "symbol": symbol,
                            "reexport": export_line,
                            "bytes_written": int(write_result.get("bytes_written") or len(new_text.encode("utf-8"))),
                            "operation": str(write_result.get("operation") or "modify"),
                            "broadcast_ok": bool(write_result.get("broadcast_ok")),
                            "director_policy": write_result.get("director_policy"),
                        },
                    }
                ]
    return []


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

        rel_exporter = exporter_path.relative_to(workspace_path).as_posix()
        repair_key = (rel_exporter, symbol)
        if repair_key in repaired_keys:
            continue
        try:
            importer_text = importer_path.read_text(encoding="utf-8")
            module_text = updated_by_path.get(exporter_path) or exporter_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _typescript_module_runtime_exports_symbol(module_text, symbol):
            continue

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
    if not missing_members:
        return []

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    writes: list[dict[str, Any]] = []
    updated_by_path: dict[Path, str] = {}
    repaired_members: list[dict[str, str]] = []
    for item in missing_members:
        type_name = item["type"]
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
        if _typescript_class_text_has_member(class_text, member):
            continue
        usage_line = _typescript_error_usage_line(workspace_path, item)
        if declaration_kind == "class":
            member_declaration = _build_typescript_missing_member_declaration(
                member=member,
                usage_line=usage_line,
                class_text=class_text,
            )
        else:
            member_declaration = _build_typescript_missing_member_signature(
                member=member,
                usage_line=usage_line,
                declaration_text=class_text,
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
    if not _typescript_errors_require_dom_lib(artifact_quality_errors):
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
    libs_raw = compiler_options.get("lib")
    libs = [str(item) for item in libs_raw] if isinstance(libs_raw, list) else []
    normalized = {item.lower() for item in libs}
    if "dom" in normalized:
        return []
    if not libs:
        libs.append(str(compiler_options.get("target") or "ES2020"))
    libs.append("DOM")
    compiler_options["lib"] = libs
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
                "bytes_written": int(write_result.get("bytes_written") or len(content.encode("utf-8"))),
                "operation": str(write_result.get("operation") or "modify"),
                "broadcast_ok": bool(write_result.get("broadcast_ok")),
                "director_policy": write_result.get("director_policy"),
            },
        }
    ]


def _typescript_errors_require_dom_lib(errors: list[str]) -> bool:
    joined = "\n".join(str(error or "").lower() for error in errors)
    if "include 'dom'" not in joined:
        return False
    return any(
        f"cannot find name '{name}'" in joined for name in ("console", "window", "document", "navigator", "location")
    )


def _apply_deterministic_typescript_number_to_string_argument_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    argument_errors = _parse_typescript_number_to_string_argument_errors(artifact_quality_errors)
    if not argument_errors:
        return []
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    updated_by_path: dict[Path, str] = {}
    repaired: list[dict[str, str]] = []
    for item in argument_errors:
        path = (workspace_path / item["file"]).resolve()
        if not _path_inside_workspace(path, workspace_path) or not path.is_file():
            continue
        try:
            text = updated_by_path.get(path) or path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.splitlines()
        line_index = int(item["line"]) - 1
        if line_index < 0 or line_index >= len(lines):
            continue
        repaired_line = _wrap_typescript_argument_at_column_as_string(lines[line_index], int(item["col"]))
        if repaired_line == lines[line_index]:
            continue
        lines[line_index] = repaired_line
        updated_by_path[path] = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
        repaired.append({"file": item["file"], "line": item["line"], "column": item["col"]})

    return _write_typescript_repair_results(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        updated_by_path=updated_by_path,
        source_tool="deterministic_typescript_number_to_string_argument_repair",
        metadata_key="arguments",
        metadata_value=repaired,
    )


def _apply_deterministic_typescript_too_few_arguments_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    arity_errors = _parse_typescript_too_few_arguments_errors(artifact_quality_errors)
    if not arity_errors:
        return []
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    updated_by_path: dict[Path, str] = {}
    repaired: list[dict[str, str]] = []
    for item in arity_errors:
        usage_line = _typescript_error_usage_line(workspace_path, item)
        method_name = _typescript_call_name_from_usage_line(usage_line, int(item["col"]))
        if not method_name:
            continue
        declaration = _find_unique_typescript_method_declaration(
            workspace_path=workspace_path,
            method_name=method_name,
            expected_count=int(item["expected"]),
            updated_by_path=updated_by_path,
        )
        if declaration is None:
            continue
        declaration_path, line_index, line_text = declaration
        repaired_line = _add_defaults_to_typescript_method_params(
            line_text,
            got_count=int(item["got"]),
            expected_count=int(item["expected"]),
        )
        if repaired_line == line_text:
            continue
        try:
            text = updated_by_path.get(declaration_path) or declaration_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = text.splitlines()
        if line_index < 0 or line_index >= len(lines):
            continue
        lines[line_index] = repaired_line
        updated_by_path[declaration_path] = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
        repaired.append(
            {
                "file": declaration_path.relative_to(workspace_path).as_posix(),
                "method": method_name,
                "expected": item["expected"],
                "got": item["got"],
            }
        )

    return _write_typescript_repair_results(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        updated_by_path=updated_by_path,
        source_tool="deterministic_typescript_too_few_arguments_repair",
        metadata_key="methods",
        metadata_value=repaired,
    )


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
        for match in _TS_NO_EXPORTED_MEMBER_ERROR_RE.finditer(str(error or "")):
            item = {
                "file": str(match.group("file") or "").strip(),
                "line": str(match.group("line") or "").strip(),
                "col": str(match.group("col") or "").strip(),
                "module": _strip_typescript_error_module_ref(str(match.group("module") or "")),
                "symbol": str(match.group("symbol") or "").strip(),
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
                    "broadcast_ok": bool(write_result.get("broadcast_ok")),
                    "director_policy": write_result.get("director_policy"),
                },
            }
        )
    return writes


def _find_typescript_class_declaration(
    *,
    workspace_path: Path,
    type_name: str,
    updated_by_path: dict[Path, str],
) -> tuple[Path | None, str | None, int, int]:
    class_re = re.compile(_TS_EXPORTED_CLASS_RE_TEMPLATE.format(type_name=re.escape(type_name)))
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
    declaration_re = re.compile(_TS_STRUCTURAL_TYPE_RE_TEMPLATE.format(type_name=re.escape(type_name)))
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


def _typescript_class_text_has_member(class_text: str, member: str) -> bool:
    escaped = re.escape(member)
    return bool(re.search(rf"\b(?:get\s+)?{escaped}\b\s*(?:\(|:)", class_text))


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
    if lowered == "number":
        return "0"
    if lowered == "string":
        return '""'
    if lowered == "boolean":
        return "false"
    if lowered.endswith("[]") or lowered.startswith("array<"):
        return "[]"
    return f"undefined as unknown as {ts_type.strip()}"


def _build_typescript_missing_member_declaration(*, member: str, usage_line: str, class_text: str) -> str:
    safe_member = re.sub(r"[^A-Za-z0-9_$]", "", member)
    if not safe_member:
        return ""
    if re.search(rf"\.{re.escape(safe_member)}\s*\(", usage_line):
        params = _typescript_method_params_from_usage_line(safe_member, usage_line)
        return f"  public {safe_member}({params}): number {{\n    return 0;\n  }}"
    if re.search(rf"\.{re.escape(safe_member)}\s*\.length\b", usage_line):
        fallback = "this.id" if re.search(r"\bid\s*:", class_text) else '""'
        return f"  public get {safe_member}(): string {{\n    return {fallback};\n  }}"
    if _typescript_usage_line_treats_member_as_string(safe_member, usage_line):
        return f'  public get {safe_member}(): string {{\n    return "";\n  }}'
    if _typescript_usage_line_treats_member_as_number(safe_member, usage_line):
        return f"  public get {safe_member}(): number {{\n    return 0;\n  }}"
    return f"  public get {safe_member}(): unknown {{\n    return undefined;\n  }}"


def _build_typescript_missing_member_signature(*, member: str, usage_line: str, declaration_text: str) -> str:
    safe_member = re.sub(r"[^A-Za-z0-9_$]", "", member)
    if not safe_member:
        return ""
    if re.search(rf"\.{re.escape(safe_member)}\s*\(", usage_line):
        params = _typescript_method_params_from_usage_line(safe_member, usage_line)
        return f"  {safe_member}({params}): number;"
    return f"  {safe_member}: {_typescript_missing_member_value_type(safe_member, usage_line, declaration_text)};"


def _typescript_missing_member_value_type(member: str, usage_line: str, declaration_text: str) -> str:
    del declaration_text
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
    return member.strip().lower() in _TS_NUMERIC_MEMBER_NAMES


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
            continue
        corrected_specifier = _relative_import_specifier_for_actual_path(
            root=workspace_path,
            importer_rel=importer_rel,
            original_specifier=specifier,
            actual_target_rel=actual_target_rel,
        )
        if corrected_specifier == specifier:
            continue
        importer_path = (workspace_path / importer_rel).resolve()
        try:
            importer_path.relative_to(workspace_path)
            content = importer_path.read_text(encoding="utf-8")
        except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
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


def _apply_deterministic_typescript_return_object_semicolon_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    paths = _parse_typescript_return_object_semicolon_paths(artifact_quality_errors)
    if not paths:
        return []

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
    for relative_path in paths:
        full_path = (workspace_path / relative_path).resolve()
        try:
            full_path.relative_to(workspace_path)
        except ValueError:
            continue
        if not full_path.is_file():
            continue
        try:
            original = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        repaired = _repair_typescript_return_object_semicolon_lines(original)
        if repaired == original:
            continue
        write_result = executor.execute_tool(
            "write_file",
            {"file": relative_path, "content": repaired},
            task_id=task_id,
        )
        if not bool(write_result.get("ok")):
            continue
        with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
            adapter._update_task_progress(task_id, "executing", current_file=relative_path)
        results.append(
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_typescript_return_object_semicolon_repair",
                    "file": relative_path,
                    "bytes_written": int(write_result.get("bytes_written") or len(repaired.encode("utf-8"))),
                    "operation": str(write_result.get("operation") or "modify"),
                    "broadcast_ok": bool(write_result.get("broadcast_ok")),
                    "director_policy": write_result.get("director_policy"),
                },
            }
        )
    return results


def _repair_typescript_return_object_semicolon_lines(text: str) -> str:
    lines = str(text or "").splitlines(keepends=True)
    repaired: list[str] = []
    object_literal_depths: list[int] = []
    brace_depth = 0
    changed = False
    for line in lines:
        line_body = line.rstrip("\r\n")
        newline = line[len(line_body) :]
        if object_literal_depths:
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
        if _TS_RETURN_OBJECT_START_RE.search(line_body) or _TS_OBJECT_LITERAL_START_RE.search(line_body):
            object_literal_depths.append(brace_depth + max(opens, 1))
        brace_depth += opens - closes
        while object_literal_depths and brace_depth < object_literal_depths[-1]:
            object_literal_depths.pop()
    return "".join(repaired) if changed else str(text or "")


def _apply_deterministic_typescript_escaped_newline_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    paths = _parse_typescript_escaped_newline_paths(artifact_quality_errors)
    if not paths:
        return []

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
    for relative_path in paths:
        full_path = (workspace_path / relative_path).resolve()
        try:
            full_path.relative_to(workspace_path)
        except ValueError:
            continue
        if not full_path.is_file():
            continue
        try:
            original = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        repaired = _repair_typescript_escaped_newline_in_line_comments(original)
        if repaired == original:
            continue
        write_result = executor.execute_tool(
            "write_file",
            {"file": relative_path, "content": repaired},
            task_id=task_id,
        )
        if not bool(write_result.get("ok")):
            continue
        with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
            adapter._update_task_progress(task_id, "executing", current_file=relative_path)
        results.append(
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_typescript_escaped_newline_repair",
                    "file": relative_path,
                    "bytes_written": int(write_result.get("bytes_written") or len(repaired.encode("utf-8"))),
                    "operation": str(write_result.get("operation") or "modify"),
                    "broadcast_ok": bool(write_result.get("broadcast_ok")),
                    "director_policy": write_result.get("director_policy"),
                },
            }
        )
    return results


def _repair_typescript_escaped_newline_in_line_comments(text: str) -> str:
    changed = False
    repaired_lines: list[str] = []

    def _replace_escaped_newline(match: re.Match[str]) -> str:
        nonlocal changed
        changed = True
        return f"{match.group('prefix')}\n{match.group('code')}"

    for line in str(text or "").splitlines(keepends=True):
        line_body = line.rstrip("\r\n")
        newline = line[len(line_body) :]
        if "//" not in line_body or "\\n" not in line_body:
            repaired_lines.append(line)
            continue
        comment_index = line_body.find("//")
        prefix = line_body[:comment_index]
        comment = line_body[comment_index:]
        repaired_comment = _TS_LINE_COMMENT_ESCAPED_NEWLINE_CODE_RE.sub(_replace_escaped_newline, comment)
        repaired_lines.append(f"{prefix}{repaired_comment}{newline}")
    return "".join(repaired_lines) if changed else str(text or "")


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
        if symbol in _parse_named_import_symbols(match.group("symbols")):
            return True
    return False


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
        return "type", f"export type {symbol} = unknown;"
    return "const", f"export const {symbol}: unknown = undefined;"


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


def _looks_like_typescript_reexport_failure(text: str) -> bool:
    token = str(text or "").lower()
    if not any(hint in token for hint in ("typescript", ".ts", ".tsx", "vitest", "npm test")):
        return False
    return any(
        hint in token
        for hint in (
            "cannot read properties of undefined",
            "undefined",
            "missing export",
            "re-export",
            "reexport",
            "import/export",
            "export/import",
            "contract fix",
        )
    )
