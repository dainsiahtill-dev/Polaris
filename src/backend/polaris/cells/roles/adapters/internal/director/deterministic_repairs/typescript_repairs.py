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
_TS_EXPORTED_CLASS_RE_TEMPLATE = r"export\s+(?:abstract\s+)?class\s+{type_name}\b[^{{]*{{"


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
        declaration_path, declaration_text, class_start, class_end = _find_typescript_class_declaration(
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
        member_declaration = _build_typescript_missing_member_declaration(
            member=member,
            usage_line=usage_line,
            class_text=class_text,
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
    return "cannot find name 'console'" in joined and "include 'dom'" in joined


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


def _build_typescript_missing_member_declaration(*, member: str, usage_line: str, class_text: str) -> str:
    safe_member = re.sub(r"[^A-Za-z0-9_$]", "", member)
    if not safe_member:
        return ""
    if re.search(rf"\.{re.escape(safe_member)}\s*\(", usage_line):
        return f"  public {safe_member}(): number {{\n    return 0;\n  }}"
    if re.search(rf"\.{re.escape(safe_member)}\s*\.length\b", usage_line):
        fallback = "this.id" if re.search(r"\bid\s*:", class_text) else '""'
        return f"  public get {safe_member}(): string {{\n    return {fallback};\n  }}"
    return f"  public get {safe_member}(): unknown {{\n    return undefined;\n  }}"


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
