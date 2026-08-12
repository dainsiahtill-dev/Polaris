"""local_imports domain for JavaScript/Node syntax repairs."""

from __future__ import annotations

import json
import posixpath
import re
import shlex
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath

from ..contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ._shared import (
    _normalize_base_files,
)
from .constants import (
    _LOCAL_JS_IMPORT_SPECIFIER_RE,
    _LOCAL_JS_MODULE_NOT_FOUND_RE,
    TYPESCRIPT_LOCAL_JS_IMPORT_SOURCE_TOOL,
)


def build_typescript_local_js_import_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Repair local ``.js`` imports for proven TypeScript source-runtime contracts."""

    normalized_base = _normalize_base_files(base_files)
    matched_diagnostics = tuple(
        diagnostic for diagnostic in diagnostics if _is_local_js_import_runtime_diagnostic(diagnostic)
    )
    commonjs_source_runtime = _base_files_use_commonjs_ts_source_runtime(normalized_base)
    direct_node_importers = _direct_node_typescript_importers(
        base_files=normalized_base,
        diagnostics=matched_diagnostics,
    )
    if not matched_diagnostics or (not commonjs_source_runtime and not direct_node_importers):
        return None

    operations: list[RepairOperation] = []
    matched_ids = [diagnostic.diagnostic_id for diagnostic in matched_diagnostics]
    for path, text in sorted(normalized_base.items()):
        if not path.endswith((".ts", ".tsx")) or path.endswith((".d.ts", ".d.tsx")):
            continue
        direct_node_source_runtime = path in direct_node_importers
        if not commonjs_source_runtime and not direct_node_source_runtime:
            continue
        for match in _LOCAL_JS_IMPORT_SPECIFIER_RE.finditer(text):
            specifier = match.group("specifier")
            target_extension = _local_typescript_import_target_extension(
                importer_path=path,
                specifier_without_js=specifier[:-3],
                base_files=normalized_base,
            )
            if target_extension is None:
                continue
            if direct_node_source_runtime and not target_extension:
                continue
            repaired_specifier = f"{specifier[:-3]}{target_extension}" if direct_node_source_runtime else specifier[:-3]
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
                        "runtime_contract": (
                            "node_direct_typescript_source_execution"
                            if direct_node_source_runtime
                            else "ts_node_commonjs_source_execution"
                        ),
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
            "runtime_contract": (
                "node_direct_typescript_source_execution"
                if direct_node_importers and not commonjs_source_runtime
                else "ts_node_commonjs_source_execution"
            ),
        },
    )


def _is_local_js_import_runtime_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    raw = f"{diagnostic.message}\n{diagnostic.raw}"
    metadata = diagnostic.metadata if isinstance(diagnostic.metadata, Mapping) else {}
    return diagnostic.code == "javascript_module_error" and (
        _LOCAL_JS_MODULE_NOT_FOUND_RE.search(raw) is not None
        or str(metadata.get("module_error_kind") or "") == "node_esm_typescript_source_import"
    )


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


def _direct_node_typescript_importers(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> frozenset[str]:
    package_text = base_files.get("package.json", "")
    try:
        package_payload = json.loads(package_text) if package_text else {}
    except ValueError:
        return frozenset()
    scripts = package_payload.get("scripts") if isinstance(package_payload, dict) else {}
    if not isinstance(scripts, dict):
        return frozenset()

    importers: set[str] = set()
    script_values = tuple(str(value or "") for value in scripts.values())
    for diagnostic in diagnostics:
        metadata = diagnostic.metadata if isinstance(diagnostic.metadata, Mapping) else {}
        if str(metadata.get("module_error_kind") or "") != "node_esm_typescript_source_import":
            continue
        importer = _workspace_relative_path_for_absolute(
            str(metadata.get("importer_path") or diagnostic.path or ""),
            base_files=base_files,
        )
        if not importer or not any(_script_executes_typescript_with_node(script, importer) for script in script_values):
            continue
        importers.add(importer)
    return frozenset(importers)


def _script_executes_typescript_with_node(script: str, importer_path: str) -> bool:
    try:
        tokens = shlex.split(str(script or ""))
    except ValueError:
        return False
    normalized_importer = importer_path.removeprefix("./")
    for index, token in enumerate(tokens):
        executable = token.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
        if executable not in {"node", "node.exe"}:
            continue
        return any(
            candidate.removeprefix("./") == normalized_importer
            for candidate in (item.replace("\\", "/") for item in tokens[index + 1 :])
        )
    return False


def _workspace_relative_path_for_absolute(path: str, *, base_files: Mapping[str, str]) -> str:
    normalized = str(path or "").strip().removeprefix("file://").replace("\\", "/")
    matches = [candidate for candidate in base_files if normalized == candidate or normalized.endswith(f"/{candidate}")]
    return max(matches, key=len) if matches else ""


def _local_typescript_import_target_extension(
    *,
    importer_path: str,
    specifier_without_js: str,
    base_files: Mapping[str, str],
) -> str | None:
    importer_parent = PurePosixPath(importer_path).parent
    candidate = (importer_parent / specifier_without_js).as_posix()
    candidate = posixpath.normpath(str(PurePosixPath(candidate))).lstrip("./")
    possible_paths = ((f"{candidate}.ts", ".ts"), (f"{candidate}.tsx", ".tsx"))
    for path, extension in possible_paths:
        if path in base_files:
            return extension
    # Extensionless imports can resolve directory indexes, but direct Node TypeScript
    # source execution cannot safely rewrite ``./dir.js`` to an index path implicitly.
    if f"{candidate}/index.ts" in base_files or f"{candidate}/index.tsx" in base_files:
        return ""
    return None
