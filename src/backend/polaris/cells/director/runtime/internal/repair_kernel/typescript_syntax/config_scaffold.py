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

"""TypeScript syntax repair module: config_scaffold."""

def _build_typescript_config_key_split_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    """Repair generated TypeScript config keys split by whitespace."""

    if not any(_is_typescript_config_key_split_diagnostic(diagnostic) for diagnostic in diagnostics):
        return None

    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_items: list[dict[str, object]] = []
    seen_spans: set[tuple[str, int, int]] = set()
    target_paths = {
        _normalize_repair_path(str(diagnostic.path or ""))
        for diagnostic in diagnostics
        if _is_typescript_config_key_split_diagnostic(diagnostic)
    }
    target_paths = {path for path in target_paths if path in base_files}
    candidate_paths = target_paths or {path for path in base_files if _is_typescript_config_file(path)}

    for path in sorted(candidate_paths):
        if not _is_typescript_config_file(path):
            continue
        original = str(base_files.get(path) or "")
        if not original:
            continue
        diagnostic_line_numbers = {
            int(diagnostic.line or 0)
            for diagnostic in diagnostics
            if _is_typescript_config_key_split_diagnostic(diagnostic)
            and _normalize_repair_path(str(diagnostic.path or "")) == path
            and int(diagnostic.line or 0) > 0
        }
        path_operations = _typescript_config_key_split_operations(
            path=path,
            content=original,
            line_numbers=diagnostic_line_numbers,
            seen_spans=seen_spans,
        )
        if not path_operations:
            continue
        operations.extend(path_operations)
        matched_diagnostics.extend(
            diagnostic
            for diagnostic in diagnostics
            if _is_typescript_config_key_split_diagnostic(diagnostic)
            and (not diagnostic.path or _normalize_repair_path(str(diagnostic.path or "")) == path)
        )
        repaired_items.extend(
            {
                "file": path,
                "line": int(operation.metadata.get("line") or 0),
                "original_key": str(operation.metadata.get("original_key") or ""),
                "replacement_key": str(operation.metadata.get("replacement_key") or ""),
            }
            for operation in path_operations
        )

    return _repair_plan_or_none(
        rule_id="typescript.config_key_split",
        source_tool=TYPESCRIPT_CONFIG_KEY_SPLIT_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"config_key_splits": repaired_items},
    )

def _build_typescript_scaffold_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    joined = _diagnostic_text(diagnostics).lower()
    operations: list[RepairOperation] = []
    files: list[dict[str, str]] = []
    needs_package = "package.json" in joined and ("missing" in joined or "not found" in joined)
    needs_tsconfig = "tsconfig.json" in joined and ("missing" in joined or "not found" in joined)
    if needs_package and "package.json" not in base_files:
        content = (
            json.dumps(_typescript_scaffold_package_payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        operations.append(
            RepairOperation(
                kind="write_file",
                path="package.json",
                content=content,
                before_hash=sha256_text(""),
                metadata={"repair_kind": "typescript_scaffold_package", "write_file_reason": "new_package_manifest"},
            )
        )
        files.append({"file": "package.json"})
    if needs_tsconfig and "tsconfig.json" not in base_files:
        content = (
            json.dumps(_typescript_scaffold_tsconfig_payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        operations.append(
            RepairOperation(
                kind="write_file",
                path="tsconfig.json",
                content=content,
                before_hash=sha256_text(""),
                metadata={"repair_kind": "typescript_scaffold_tsconfig", "write_file_reason": "new_tsconfig"},
            )
        )
        files.append({"file": "tsconfig.json"})
    return _repair_plan_or_none(
        rule_id="typescript.scaffold",
        source_tool=TYPESCRIPT_SCAFFOLD_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"files": files},
    )

def _build_typescript_sourcefile_diagnostics_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    repaired_files: list[dict[str, str]] = []
    for path in _parse_typescript_sourcefile_diagnostics_paths(diagnostics):
        original = str(base_files.get(path) or "")
        repaired = _repair_typescript_sourcefile_diagnostics_usage(original)
        if not original or repaired == original:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={"repair_kind": "typescript_sourcefile_diagnostics"},
            )
        )
        repaired_files.append({"file": path})
    return _repair_plan_or_none(
        rule_id="typescript.sourcefile_diagnostics",
        source_tool=TYPESCRIPT_SOURCEFILE_DIAGNOSTICS_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"diagnostics": repaired_files},
    )

def _build_typescript_tsconfig_lib_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    tsconfig_text = str(base_files.get("tsconfig.json") or "")
    if not tsconfig_text:
        return None
    needs_dom_lib = _typescript_errors_require_dom_lib(diagnostics)
    needs_import_meta_module = _typescript_errors_require_import_meta_module(diagnostics)
    needs_es2021_lib = _typescript_errors_require_es2021_lib(diagnostics)
    removed_options = _typescript_removed_compiler_options(diagnostics)
    if not needs_dom_lib and not needs_import_meta_module and not needs_es2021_lib and not removed_options:
        return None
    payload = _json_object(tsconfig_text)
    compiler_options = payload.get("compilerOptions")
    if not isinstance(compiler_options, dict):
        compiler_options = {}
    operations: list[RepairOperation] = []
    for option_name in removed_options:
        if option_name not in compiler_options:
            continue
        operations.append(
            RepairOperation(
                kind="json_delete",
                path="tsconfig.json",
                json_path=("compilerOptions", option_name),
                before_hash=sha256_text(tsconfig_text),
                metadata={
                    "repair_kind": "typescript_tsconfig_removed_compiler_option",
                    "removed_option": option_name,
                },
            )
        )
    libs_raw = compiler_options.get("lib")
    libs = [str(item) for item in libs_raw] if isinstance(libs_raw, list) else []
    normalized_libs = {item.lower() for item in libs}
    if needs_es2021_lib and not _typescript_libs_allow_es2021(libs):
        libs = _typescript_promote_libs_to_es2021(libs, compiler_options.get("target"))
        operations.append(
            RepairOperation(
                kind="json_set",
                path="tsconfig.json",
                json_path=("compilerOptions", "lib"),
                value=libs,
                before_hash=sha256_text(tsconfig_text),
                metadata={"repair_kind": "typescript_tsconfig_es2021_lib"},
            )
        )
        target_value = str(compiler_options.get("target") or "").strip()
        if target_value and target_value.lower() not in {"es2021", "es2022", "esnext"}:
            operations.append(
                RepairOperation(
                    kind="json_set",
                    path="tsconfig.json",
                    json_path=("compilerOptions", "target"),
                    value="ES2021",
                    before_hash=sha256_text(tsconfig_text),
                    metadata={"repair_kind": "typescript_tsconfig_es2021_target"},
                )
            )
    if needs_dom_lib and "dom" not in normalized_libs:
        if not libs:
            libs.append(str(compiler_options.get("target") or "ES2020"))
        libs.append("DOM")
        operations.append(
            RepairOperation(
                kind="json_set",
                path="tsconfig.json",
                json_path=("compilerOptions", "lib"),
                value=libs,
                before_hash=sha256_text(tsconfig_text),
                metadata={"repair_kind": "typescript_tsconfig_dom_lib"},
            )
        )
    module_value = compiler_options.get("module")
    if needs_import_meta_module and not _typescript_module_allows_import_meta(module_value):
        operations.append(
            RepairOperation(
                kind="json_set",
                path="tsconfig.json",
                json_path=("compilerOptions", "module"),
                value="ES2020",
                before_hash=sha256_text(tsconfig_text),
                metadata={"repair_kind": "typescript_tsconfig_import_meta_module"},
            )
        )
    return _repair_plan_or_none(
        rule_id="typescript.tsconfig_lib",
        source_tool=TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        risk_level="medium",
        metadata={
            "libs": libs,
            "module": "ES2020" if needs_import_meta_module else module_value,
            "target": "ES2021" if needs_es2021_lib else compiler_options.get("target"),
            "removed_options": removed_options,
        },
    )

def _build_typescript_tsconfig_rootdir_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    tsconfig_text = str(base_files.get("tsconfig.json") or "")
    if not tsconfig_text or not _typescript_errors_require_rootdir_widening(diagnostics):
        return None
    payload = _json_object(tsconfig_text)
    compiler_options = payload.get("compilerOptions")
    if not isinstance(compiler_options, dict):
        return None
    root_dir = _normalize_repair_path(str(compiler_options.get("rootDir") or ""))
    if root_dir not in {"src", "src/"}:
        return None
    outside_paths = _typescript_rootdir_outside_paths(diagnostics, root_dir=root_dir)
    include_entries = _typescript_tsconfig_include_entries(payload)
    include_has_outside_root = any(
        _typescript_glob_points_outside_root(entry, root_dir=root_dir) for entry in include_entries
    )
    if not outside_paths and not include_has_outside_root:
        return None
    operation = RepairOperation(
        kind="json_set",
        path="tsconfig.json",
        json_path=("compilerOptions", "rootDir"),
        value=".",
        before_hash=sha256_text(tsconfig_text),
        metadata={
            "repair_kind": "typescript_tsconfig_rootdir_outside_source",
            "previous_rootDir": root_dir,
            "outside_paths": tuple(outside_paths),
        },
    )
    return _repair_plan_or_none(
        rule_id="typescript.tsconfig_rootdir",
        source_tool=TYPESCRIPT_TSCONFIG_ROOTDIR_SOURCE_TOOL,
        operations=[operation],
        diagnostics=diagnostics,
        mode=mode,
        risk_level="medium",
        metadata={"previous_rootDir": root_dir, "rootDir": ".", "outside_paths": outside_paths},
    )

def _build_typescript_vitest_globals_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    globals_repaired: list[dict[str, str]] = []
    by_file: dict[str, set[str]] = {}
    for item in _parse_typescript_missing_test_global_errors(diagnostics):
        by_file.setdefault(item["file"], set()).add(item["symbol"])
    if not by_file:
        return None
    for path, symbols in sorted(by_file.items()):
        original = str(base_files.get(path) or "")
        repaired = _add_vitest_import_to_typescript_test(original, symbols)
        if not original or repaired == original:
            continue
        metadata = {"repair_kind": "typescript_vitest_global_import", "symbols": tuple(sorted(symbols))}
        if _TS_VITEST_IMPORT_RE.search(original):
            operations.extend(
                _text_replace_operations_from_repair(
                    path=path,
                    original=original,
                    repaired=repaired,
                    metadata=metadata,
                )
            )
        else:
            operation = _prepend_typescript_vitest_import_operation(path=path, original=original, symbols=symbols)
            if operation is not None:
                operations.append(operation)
        globals_repaired.extend({"file": path, "symbol": symbol} for symbol in sorted(symbols))
    package_text = str(base_files.get("package.json") or "")
    if package_text:
        package_ops = _typescript_vitest_manifest_operations(package_text)
        operations.extend(package_ops)
        if package_ops:
            globals_repaired.append({"file": "package.json", "symbol": "vitest"})
    return _repair_plan_or_none(
        rule_id="typescript.vitest_globals",
        source_tool=TYPESCRIPT_VITEST_GLOBALS_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        risk_level="medium",
        metadata={"test_globals": globals_repaired},
    )

def _build_typescript_zod_type_class_collision_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    files: list[dict[str, str]] = []
    for path in _parse_typescript_zod_type_class_collision_paths(diagnostics):
        original = str(base_files.get(path) or "")
        repaired = _repair_typescript_zod_type_class_collision(original)
        if not original or repaired == original:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={"repair_kind": "typescript_zod_type_class_collision"},
            )
        )
        files.append({"file": path})
    return _repair_plan_or_none(
        rule_id="typescript.zod_type_class_collision",
        source_tool=TYPESCRIPT_ZOD_TYPE_CLASS_COLLISION_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"files": files},
    )

def _typescript_errors_require_rootdir_widening(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    return any(
        diagnostic.code.lower() == "typescript_ts6059"
        or "is not under 'rootDir'" in str(diagnostic.raw or diagnostic.message)
        or 'is not under "rootDir"' in str(diagnostic.raw or diagnostic.message)
        for diagnostic in diagnostics
    )

def _typescript_removed_compiler_options(diagnostics: Sequence[RepairDiagnostic]) -> tuple[str, ...]:
    """Return removed tsconfig compiler options explicitly present in diagnostics."""

    removed: list[str] = []
    for diagnostic in diagnostics:
        metadata = diagnostic.metadata if isinstance(diagnostic.metadata, Mapping) else {}
        metadata_option = str(metadata.get("compiler_option") or "").strip().lower()
        diagnostic_kind = str(metadata.get("diagnostic_kind") or diagnostic.code or "").strip().lower()
        diagnostic_code = str(metadata.get("diagnostic_code") or diagnostic.code or "").strip().upper()
        text = f"{diagnostic.message}\n{diagnostic.raw}".lower()
        for option_name in _REMOVED_TYPESCRIPT_COMPILER_OPTIONS:
            if (metadata_option == option_name and diagnostic_kind == "tsconfig_removed_compiler_option") or (
                diagnostic_code == "TS5102"
                and (
                    f"compileroptions.{option_name}" in text
                    or f"option '{option_name}' has been removed" in text
                    or f'option "{option_name}" has been removed' in text
                )
            ):
                removed.append(option_name)
    return tuple(dict.fromkeys(removed))

def _typescript_rootdir_outside_paths(diagnostics: Sequence[RepairDiagnostic], *, root_dir: str) -> list[str]:
    normalized_root = _normalize_repair_path(root_dir).rstrip("/")
    outside: list[str] = []
    for diagnostic in diagnostics:
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if not path:
            path = _normalize_repair_path(str(diagnostic.metadata.get("raw_path") or ""))
        if not path:
            continue
        if normalized_root and not path.startswith(f"{normalized_root}/"):
            outside.append(path)
    return _dedupe_preserve_order(outside)

def _typescript_tsconfig_include_entries(payload: Mapping[str, Any]) -> list[str]:
    include = payload.get("include")
    if not isinstance(include, list):
        return []
    return [str(item or "").strip().replace("\\", "/") for item in include if str(item or "").strip()]

def _typescript_scaffold_package_payload() -> dict[str, object]:
    return {
        "name": "typescript-application",
        "version": "1.0.0",
        "main": "dist/index.js",
        "scripts": {"build": "tsc", "test": "npm run build", "start": "node dist/index.js"},
        "devDependencies": {"typescript": "^5.0.0"},
    }

def _typescript_scaffold_tsconfig_payload() -> dict[str, object]:
    return {
        "compilerOptions": {
            "target": "ES2020",
            "module": "ESNext",
            "moduleResolution": "node",
            "outDir": "dist",
            "rootDir": "src",
            "strict": True,
            "esModuleInterop": True,
            "skipLibCheck": True,
        },
        "include": ["src/**/*.ts"],
        "exclude": ["node_modules", "dist"],
    }

def _parse_typescript_sourcefile_diagnostics_paths(diagnostics: Sequence[RepairDiagnostic]) -> list[str]:
    paths: list[str] = []
    for diagnostic in diagnostics:
        for match in _TS_SOURCEFILE_DIAGNOSTICS_RAW_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            path = _normalize_repair_path(str(match.group("file") or ""))
            if path:
                paths.append(path)
    return _dedupe_preserve_order(paths)

def _repair_typescript_sourcefile_diagnostics_usage(text: str) -> str:
    source = str(text or "")
    if "ts.createSourceFile" not in source:
        return source
    create_match = re.search(
        r"ts\.createSourceFile\(\s*(?P<file>[A-Za-z_$][A-Za-z0-9_$]*)\s*,\s*"
        r"(?P<source>[A-Za-z_$][A-Za-z0-9_$]*)",
        source,
        re.DOTALL,
    )
    source_var = str(create_match.group("source") if create_match else "text")
    file_var = str(create_match.group("file") if create_match else "file")
    diagnostics_re = re.compile(
        r"(?m)^(?P<indent>\s*)const\s+diagnostics(?:\s*:[^=]+)?\s*=\s*"
        r"(?P<expr>[^\n;]*(?:parseDiagnostics|undefined\s+as\s+unknown|unknown\s*\?\?\s*\[\])[^\n;]*);?\s*$"
    )

    def _replace(match: re.Match[str]) -> str:
        indent = str(match.group("indent") or "")
        inner = indent + "  "
        return (
            f"{indent}const diagnostics: readonly ts.Diagnostic[] =\n"
            f"{inner}ts.transpileModule({source_var}, {{\n"
            f"{inner}  compilerOptions: {{\n"
            f"{inner}    module: ts.ModuleKind.ES2020,\n"
            f"{inner}    target: ts.ScriptTarget.ES2020,\n"
            f"{inner}  }},\n"
            f"{inner}  fileName: {file_var},\n"
            f"{inner}  reportDiagnostics: true,\n"
            f"{inner}}}).diagnostics ?? [];"
        )

    repaired, replacements = diagnostics_re.subn(_replace, source, count=1)
    if replacements == 0:
        return source
    return re.sub(r"if\s*\(\s*(?:0\s*>\s*0|false)\s*\)", "if (diagnostics.length > 0)", repaired)

def _typescript_vitest_manifest_operations(package_text: str) -> tuple[RepairOperation, ...]:
    payload = _json_object(package_text)
    operations: list[RepairOperation] = []
    scripts_raw = payload.get("scripts")
    scripts = dict(scripts_raw) if isinstance(scripts_raw, Mapping) else {}
    if "vitest" not in str(scripts.get("test") or ""):
        scripts["test"] = "vitest run"
        operations.append(
            RepairOperation(
                kind="json_set",
                path="package.json",
                json_path=("scripts",),
                value=dict(sorted(scripts.items())),
                before_hash=sha256_text(package_text),
                metadata={"repair_kind": "typescript_vitest_test_script"},
            )
        )
    dev_deps_raw = payload.get("devDependencies")
    dev_deps = dict(dev_deps_raw) if isinstance(dev_deps_raw, Mapping) else {}
    dependencies_raw = payload.get("dependencies")
    dependencies = dict(dependencies_raw) if isinstance(dependencies_raw, Mapping) else {}
    if "vitest" not in dev_deps and "vitest" not in dependencies:
        dev_deps["vitest"] = "^2.1.8"
        operations.append(
            RepairOperation(
                kind="json_set",
                path="package.json",
                json_path=("devDependencies",),
                value=dict(sorted(dev_deps.items())),
                before_hash=sha256_text(package_text),
                metadata={"repair_kind": "typescript_vitest_dev_dependency"},
            )
        )
    return tuple(operations)

def _parse_typescript_zod_type_class_collision_paths(diagnostics: Sequence[RepairDiagnostic]) -> list[str]:
    paths: list[str] = []
    for diagnostic in diagnostics:
        match = _TS_ZOD_TYPE_CLASS_COLLISION_ERROR_RE.search(str(diagnostic.raw or diagnostic.message or ""))
        if match:
            path = _normalize_repair_path(str(match.group("path") or ""))
            if path:
                paths.append(path)
    return _dedupe_preserve_order(paths)

def _repair_typescript_zod_type_class_collision(text: str) -> str:
    token = str(text or "")
    changed = False

    def class_exists(name: str) -> bool:
        return bool(re.search(rf"(?:^|\n)\s*(?:export\s+)?class\s+{re.escape(name)}\b", token))

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        name = str(match.group("name") or "")
        if not class_exists(name):
            return match.group(0)
        changed = True
        return f"{match.group('indent')}{match.group('export') or ''}type {name}Data = {match.group('infer')};"

    repaired = _TS_ZOD_INFERRED_TYPE_ALIAS_LINE_RE.sub(replace, token)
    if not changed:
        return token

    for match in _TS_ZOD_INFERRED_TYPE_ALIAS_LINE_RE.finditer(token):
        name = str(match.group("name") or "").strip()
        if not name or not class_exists(name):
            continue
        new_name = f"{name}Data"
        repaired = re.sub(
            rf"(\bconstructor\s*\([^)]*\bdata\s*:\s*){re.escape(name)}\b",
            rf"\g<1>{new_name}",
            repaired,
        )
        repaired = re.sub(
            rf"(\b(?:public|private|protected|readonly\s+)*data\s*:\s*){re.escape(name)}\b",
            rf"\g<1>{new_name}",
            repaired,
        )
    return repaired

def _typescript_config_key_split_operations(
    *,
    path: str,
    content: str,
    line_numbers: set[int],
    seen_spans: set[tuple[str, int, int]],
) -> tuple[RepairOperation, ...]:
    lines = str(content or "").splitlines(keepends=True)
    offsets = _line_start_offsets(lines)
    before_hash = sha256_text(content)
    operations: list[RepairOperation] = []
    candidate_indexes = (
        {line_number - 1 for line_number in line_numbers if 0 < line_number <= len(lines)}
        if line_numbers
        else set(range(len(lines)))
    )
    for index in sorted(candidate_indexes):
        line = lines[index]
        line_without_newline = line.rstrip("\r\n")
        match = _TS_CONFIG_SPLIT_KEY_LINE_RE.match(line_without_newline)
        if match is None:
            continue
        left = str(match.group("left") or "")
        right = str(match.group("right") or "")
        replacement_key = f"{left}{right}"
        if replacement_key not in _TS_CONFIG_JOINABLE_KEYS:
            continue
        start = offsets[index] + match.start("left")
        end = offsets[index] + match.end("right")
        span_key = (path, start, end)
        if span_key in seen_spans:
            continue
        seen_spans.add(span_key)
        expected = content[start:end]
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=start,
                span_end=end,
                expected=expected,
                replacement=replacement_key,
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_config_key_split",
                    "edit_strategy": "text_replace",
                    "line": index + 1,
                    "original_key": expected,
                    "replacement_key": replacement_key,
                },
            )
        )
    return tuple(operations)

def _is_typescript_config_key_split_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    if diagnostic.code.lower() == "typescript_config_key_syntax":
        return True
    text = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    return bool(
        _is_typescript_config_file(str(diagnostic.path or ""))
        and "expected" in text
        and "found" in text
        and "config" in text
    )

__all__ = (
    "_build_typescript_config_key_split_plan",
    "_build_typescript_scaffold_plan",
    "_build_typescript_sourcefile_diagnostics_plan",
    "_build_typescript_tsconfig_lib_plan",
    "_build_typescript_tsconfig_rootdir_plan",
    "_build_typescript_vitest_globals_plan",
    "_build_typescript_zod_type_class_collision_plan",
    "_typescript_errors_require_rootdir_widening",
    "_typescript_removed_compiler_options",
    "_typescript_rootdir_outside_paths",
    "_typescript_tsconfig_include_entries",
    "_typescript_scaffold_package_payload",
    "_typescript_scaffold_tsconfig_payload",
    "_parse_typescript_sourcefile_diagnostics_paths",
    "_repair_typescript_sourcefile_diagnostics_usage",
    "_typescript_vitest_manifest_operations",
    "_parse_typescript_zod_type_class_collision_paths",
    "_repair_typescript_zod_type_class_collision",
    "_typescript_config_key_split_operations",
    "_is_typescript_config_key_split_diagnostic",
)
