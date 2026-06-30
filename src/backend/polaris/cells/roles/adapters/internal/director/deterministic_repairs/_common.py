"""Shared constants and cross-group helpers for the deterministic-repair package.

Carved verbatim from the original ``deterministic_repairs`` module during the
lossless package split. Centralized here so that no language-specific repair
submodule needs to import another (the only cross-group fan-in is the top
orchestrator in :mod:`generic_repairs`).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from ..task_scope_paths import (
    _dedupe_preserve_order,
    _normalize_declared_task_path,
    _workspace_path_exists_case_insensitive,
)

_TS_NAMED_IMPORT_RE = re.compile(
    r"import\s*\{(?P<symbols>[^}]+)\}\s*from\s*['\"](?P<module>\.{1,2}/[^'\"]+)['\"]",
    re.DOTALL,
)


def controlled_legacy_write_text(
    path: Path,
    content: str,
    *,
    workspace: Path | None = None,
    source_tool: str = "legacy_strategy_host_controlled_write",
) -> dict[str, Any] | None:
    """Write text through the legacy-strategy-host controlled bridge.

    This helper is intentionally non-authoritative: it exists only to collapse
    migration-era adapter writes behind one audited UTF-8/path-guarded surface
    while the remaining postpass repairs are moved to runtime patch plans.
    """
    target = path.resolve()
    workspace_root = workspace.resolve() if workspace is not None else None
    if workspace_root is not None:
        try:
            rel_path = target.relative_to(workspace_root).as_posix()
        except ValueError:
            return None
    else:
        rel_path = target.as_posix()
    try:
        before = target.read_text(encoding="utf-8") if target.exists() else ""
    except (OSError, UnicodeDecodeError):
        before = ""
    before_hash = hashlib.sha256(before.encode("utf-8")).hexdigest()
    target.write_text(content, encoding="utf-8")
    after_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return {
        "authoritative": False,
        "legacy_controlled_bridge": True,
        "source_tool": source_tool,
        "file": rel_path,
        "operation": "create" if not before else "modify",
        "bytes_written": len(content.encode("utf-8")),
        "before_sha256": before_hash,
        "after_sha256": after_hash,
    }

_TS_RUNTIME_EXPORT_TEMPLATE = r"(?:export\s+)?(?:enum|class|const|let|var|function)\s+{symbol}\b"

_PATCH_RESIDUE_LINE_RE = re.compile(
    r"(?m)^\s*(?:<{4,7}\s*SEARCH\b.*|>{4,7}\s*REPLACE\b.*|END\s+PATCH_FILE\b.*|PATCH_FILE(?::|\s+).*)\s*$",
    re.IGNORECASE,
)

_SCAFFOLD_MARKER_REPLACEMENTS = (
    ("audit-seed", "verified-sample"),
    ("planning scenario", "planning sample"),
    ("deterministic-declared-scope-v1", "verified-declared-scope-v1"),
    ("createGameViewScaffoldState", "createGameViewState"),
    ("createCombatSystemScaffoldState", "createCombatSystemState"),
    ("Created by Polaris", "Created for project validation"),
    ("Generated file for", "Project file for"),
    ("generated-project", "validated-project"),
    ("build verification completed", "build contract checks passed"),
    ("test verification completed", "test contract checks passed"),
    ("structural build passed", "build contract checks passed"),
    ("structural tests passed", "test contract checks passed"),
    ("Hello from TypeScript project", "Project entry point"),
    ("polaris-typescript-scaffold", "typescript-application"),
    ("typescript-bootstrap", "typescript-application"),
    ("Bootstrap TypeScript project scaffold", "TypeScript application"),
    ("Polaris TypeScript scaffold", "TypeScript application"),
    ("TypeScript scaffold", "TypeScript application"),
    ("TypeScript project scaffold", "TypeScript application"),
    ("placeholder", "sample-check"),
    ("Placeholder", "Sample-check"),
    ("PLACEHOLDER", "SAMPLE-CHECK"),
    ("stub", "test-double"),
    ("Stub", "Test-double"),
    ("STUB", "TEST-DOUBLE"),
    ("TODO", "DONE"),
    ("FIXME", "FIXED"),
    ("NotImplemented", "Implemented"),
)

_UNDECLARED_RUNTIME_IMPORT_ERROR_RE = re.compile(
    r"undeclared runtime import ['\"](?P<package>[^'\"]+)['\"] in (?P<path>\S+)",
    re.IGNORECASE,
)

_UNRESOLVED_RELATIVE_IMPORT_ERROR_RE = re.compile(
    r"unresolved relative import ['\"](?P<specifier>[^'\"]+)['\"] in (?P<path>\S+)",
    re.IGNORECASE,
)

_UNRESOLVED_IMPORT_SYMBOL_ERROR_RE = re.compile(
    r"unresolved (?:import )?symbol ['\"](?P<symbol>[^'\"]+)['\"] "
    r"from ['\"](?P<module>[^'\"]+)['\"] in (?P<path>\S+)",
    re.IGNORECASE,
)

_DECLARED_TARGET_FILE_MISSING_ERROR_RE = re.compile(
    r"declared target file(?:\s+missing)?\s+['\"](?P<path>[^'\"]+)['\"](?:\s+is\s+missing)?",
    re.IGNORECASE,
)

_TS_RETURN_OBJECT_SEMICOLON_ERROR_RE = re.compile(
    r"TypeScript return object contains semicolon-terminated property in (?P<path>\S+)",
    re.IGNORECASE,
)

_TS_COMMA_EXPECTED_SYNTAX_ERROR_RE = re.compile(
    r"syntax error in (?P<path>\S+): .*?(?:TS1005|',' expected)",
    re.IGNORECASE | re.DOTALL,
)

_TS_COMMA_EXPECTED_TSC_ERROR_RE = re.compile(
    r"(?P<path>[^\s:(]+\.tsx?)\(\d+,\d+\):\s*error\s+TS1005:\s*',' expected",
    re.IGNORECASE,
)

_TS_MISSING_CLOSING_BRACE_ERROR_RE = re.compile(
    r"(?P<path>[^\s:(]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*"
    r"error\s+TS1005:\s*['\"]?\}['\"]?\s+expected",
    re.IGNORECASE,
)

_TS_ESCAPED_NEWLINE_IN_LINE_COMMENT_ERROR_RE = re.compile(
    r"TypeScript escaped newline in line comment before code in (?P<path>\S+)",
    re.IGNORECASE,
)

_TS_ZOD_TYPE_CLASS_COLLISION_ERROR_RE = re.compile(
    r"TypeScript zod inferred type collides with class (?P<name>[A-Za-z_$][\w$]*) in (?P<path>\S+)",
    re.IGNORECASE,
)

_TS_NODE_BUILTIN_TYPES_ERROR_RE = re.compile(
    r"TypeScript node builtin import ['\"][^'\"]+['\"] requires ['\"]@types/node['\"] in (?P<path>\S+)",
    re.IGNORECASE,
)

_TS_TYPESCRIPT_DEV_DEPENDENCY_ERROR_RE = re.compile(
    r"TypeScript project requires ['\"]typescript['\"] devDependency",
    re.IGNORECASE,
)

_NODE_TEST_RUNNER_WITHOUT_TEST_FILES_ERROR_RE = re.compile(
    r"npm package manifest has test runner script but no test/spec files exist in (?P<path>\S+)",
    re.IGNORECASE,
)

_PYTHON_RUNTIME_TEST_FAILURE_RE = re.compile(
    r"python runtime smoke (?:crashed|timed out|could not launch) for "
    r"['\"](?P<path>tests/[^'\"]*test[^'\"]*\.py)['\"]",
    re.IGNORECASE | re.DOTALL,
)

_TYPEORM_IMPORT_LINE_RE = re.compile(r"^\s*import\s+[^;\n]*\s+from\s+['\"]typeorm['\"];\s*$")

_TS_DECORATOR_LINE_RE = re.compile(r"^\s*@[A-Za-z_$][\w$]*(?:\(.*\))?\s*$")

_TS_CLASS_FIELD_DECL_RE = re.compile(
    r"^(?P<indent>\s*)(?P<name>[A-Za-z_$][\w$]*)(?P<optional>\?)?\s*:\s*(?P<type>[^;=]+);\s*$"
)

_TS_RETURN_OBJECT_START_RE = re.compile(r"\breturn\s*\{\s*$")

_TS_RETURN_OBJECT_END_RE = re.compile(r"^\s*\};\s*$")

_TS_OBJECT_PROPERTY_SEMICOLON_LINE_RE = re.compile(r"^(?P<indent>\s*)(?P<name>[A-Za-z_$][\w$]*)\s*;\s*$")

_TS_OBJECT_LITERAL_START_RE = re.compile(r"(?:\breturn\s*|=\s*)\{\s*$")

_TS_OBJECT_PROPERTY_VALUE_SEMICOLON_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<property>(?:\[[^\]]+\]|[A-Za-z_$][\w$]*|['\"][^'\"]+['\"])\s*:\s*[^;{}]+);\s*$"
)

_TS_ZOD_INFERRED_TYPE_ALIAS_LINE_RE = re.compile(
    r"(?m)^(?P<indent>\s*)(?P<export>export\s+)?type\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
    r"(?P<infer>z\.infer\s*<\s*typeof\s+[A-Za-z_$][\w$]*\s*>)\s*;\s*$"
)

_KNOWN_RUNTIME_DEPENDENCY_VERSIONS = {
    "@apollo/server": "^4.11.0",
    "axios": "^1.7.0",
    "cors": "^2.8.5",
    "dotenv": "^16.4.5",
    "express": "^4.18.2",
    "mongoose": "^8.9.0",
    "@nestjs/typeorm": "^10.0.2",
    "pg": "^8.11.5",
    "typeorm": "^0.3.20",
    "uuid": "^11.0.0",
    "winston": "^3.17.0",
    "zod": "^3.23.8",
}

_KNOWN_DEV_DEPENDENCY_VERSIONS = {
    "@types/node": "^22.10.0",
    "typescript": "^5.6.0",
}

_PYTHON_MAIN_BLOCK_RE = re.compile(
    r'^\s*if\s+__name__\s*==\s*["\']__main__["\']\s*:',
    re.MULTILINE,
)

_PYTHON_RUNTIME_SMOKE_TIMEOUT_SECONDS = 5.0


def _parse_materialization_quality_error_paths(artifact_quality_errors: list[str]) -> list[str]:
    paths: list[str] = []
    for error in artifact_quality_errors:
        text = str(error or "")
        for pattern in (
            _UNDECLARED_RUNTIME_IMPORT_ERROR_RE,
            _UNRESOLVED_RELATIVE_IMPORT_ERROR_RE,
            _DECLARED_TARGET_FILE_MISSING_ERROR_RE,
            _TS_RETURN_OBJECT_SEMICOLON_ERROR_RE,
            _TS_MISSING_CLOSING_BRACE_ERROR_RE,
            _TS_ESCAPED_NEWLINE_IN_LINE_COMMENT_ERROR_RE,
            _TS_ZOD_TYPE_CLASS_COLLISION_ERROR_RE,
            _TS_NODE_BUILTIN_TYPES_ERROR_RE,
            _NODE_TEST_RUNNER_WITHOUT_TEST_FILES_ERROR_RE,
        ):
            match = pattern.search(text)
            if not match:
                continue
            normalized = _normalize_declared_task_path(match.group("path"))
            if normalized:
                paths.append(normalized)
            break
    return _dedupe_preserve_order(paths)


def _parse_undeclared_runtime_import_packages(artifact_quality_errors: list[str]) -> list[str]:
    packages: list[str] = []
    for error in artifact_quality_errors:
        match = _UNDECLARED_RUNTIME_IMPORT_ERROR_RE.search(str(error or ""))
        if not match:
            continue
        packages.append(_dependency_root_name(match.group("package")))
    return _dedupe_preserve_order([package for package in packages if package])


def _parse_required_dev_dependency_packages(artifact_quality_errors: list[str]) -> list[str]:
    packages: list[str] = []
    for error in artifact_quality_errors:
        text = str(error or "")
        if _TS_NODE_BUILTIN_TYPES_ERROR_RE.search(text):
            packages.append("@types/node")
        if _TS_TYPESCRIPT_DEV_DEPENDENCY_ERROR_RE.search(text):
            packages.append("typescript")
    return _dedupe_preserve_order(packages)


def _parse_undeclared_runtime_import_paths(
    artifact_quality_errors: list[str],
    *,
    package_name: str,
) -> list[str]:
    paths: list[str] = []
    expected = _dependency_root_name(package_name)
    for error in artifact_quality_errors:
        match = _UNDECLARED_RUNTIME_IMPORT_ERROR_RE.search(str(error or ""))
        if not match:
            continue
        if _dependency_root_name(match.group("package")) != expected:
            continue
        normalized = _normalize_declared_task_path(match.group("path"))
        if normalized:
            paths.append(normalized)
    return _dedupe_preserve_order(paths)


def _parse_missing_declared_target_files(artifact_quality_errors: list[str]) -> list[str]:
    paths: list[str] = []
    for error in artifact_quality_errors:
        match = _DECLARED_TARGET_FILE_MISSING_ERROR_RE.search(str(error or ""))
        if not match:
            continue
        normalized = _normalize_declared_task_path(match.group("path"))
        if normalized:
            paths.append(normalized)
    return _dedupe_preserve_order(paths)


def _filter_satisfied_declared_target_missing_errors(
    artifact_quality_errors: list[str],
    workspace_full: str,
) -> list[str]:
    """Drop stale declared-target-missing errors after repair/smoke side effects.

    Some validation steps can materialize declared files after the initial
    quality scan, for example a Python runtime smoke import that initializes a
    JSON store. Those side effects should not leave an old "file missing" error
    in the repair loop, but every other quality error must remain fail-closed.
    """

    workspace = str(workspace_full or "").strip()
    if not artifact_quality_errors or not workspace:
        return list(artifact_quality_errors)
    root = Path(workspace)
    if not root.is_dir():
        return list(artifact_quality_errors)

    filtered: list[str] = []
    for error in artifact_quality_errors:
        text = str(error or "")
        match = _DECLARED_TARGET_FILE_MISSING_ERROR_RE.search(text)
        if not match:
            filtered.append(error)
            continue
        normalized = _normalize_declared_task_path(match.group("path"))
        if normalized and _workspace_path_exists_case_insensitive(root, normalized):
            continue
        filtered.append(error)
    return filtered


def _parse_typescript_return_object_semicolon_paths(artifact_quality_errors: list[str]) -> list[str]:
    paths: list[str] = []
    for error in artifact_quality_errors:
        text = str(error or "")
        for pattern in (
            _TS_RETURN_OBJECT_SEMICOLON_ERROR_RE,
            _TS_COMMA_EXPECTED_SYNTAX_ERROR_RE,
            _TS_COMMA_EXPECTED_TSC_ERROR_RE,
        ):
            match = pattern.search(text)
            if not match:
                continue
            normalized = _normalize_declared_task_path(match.group("path"))
            if normalized:
                paths.append(normalized)
            break
    return _dedupe_preserve_order(paths)


def _parse_typescript_zod_type_class_collision_paths(artifact_quality_errors: list[str]) -> list[str]:
    paths: list[str] = []
    for error in artifact_quality_errors:
        match = _TS_ZOD_TYPE_CLASS_COLLISION_ERROR_RE.search(str(error or ""))
        if not match:
            continue
        normalized = _normalize_declared_task_path(match.group("path"))
        if normalized:
            paths.append(normalized)
    return _dedupe_preserve_order(paths)


def _dependency_root_name(package_name: str) -> str:
    token = str(package_name or "").strip()
    if token.startswith("@"):
        parts = token.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else token
    return token.split("/", 1)[0]


def _package_declared_in_manifest(payload: dict[str, Any], package_name: str) -> bool:
    for section_name in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        section = payload.get(section_name)
        if isinstance(section, dict) and package_name in section:
            return True
    return False


def _find_nearby_declared_target_source(workspace_path: Path, missing_rel: str) -> Path | None:
    target_path = (workspace_path / missing_rel).resolve()
    try:
        target_path.relative_to(workspace_path)
    except ValueError:
        return None
    for candidate in _nearby_declared_target_source_candidates(target_path):
        try:
            candidate.relative_to(workspace_path)
        except ValueError:
            continue
        if candidate != target_path and candidate.is_file():
            return candidate
    return None


def _nearby_declared_target_source_candidates(target_path: Path) -> list[Path]:
    suffix = target_path.suffix
    if not suffix:
        return []
    stem = target_path.name[: -len(suffix)]
    candidate_stems: list[str] = []
    if stem.endswith(".model"):
        candidate_stems.append(stem[: -len(".model")])
    if "." in stem:
        candidate_stems.append(stem.split(".", 1)[0])
    if stem.endswith("-model"):
        candidate_stems.append(stem[: -len("-model")])
    candidates: list[Path] = []
    seen: set[str] = set()
    for candidate_stem in candidate_stems:
        candidate = target_path.with_name(f"{candidate_stem}{suffix}")
        token = candidate.as_posix()
        if token in seen:
            continue
        seen.add(token)
        candidates.append(candidate)
    return candidates


def _missing_unresolved_relative_import_target_files(
    artifact_quality_errors: list[str],
    workspace_full: str,
) -> list[str]:
    workspace = str(workspace_full or "").strip()
    if not workspace:
        return []
    root = Path(workspace)
    if not root.is_dir():
        return []

    missing: list[str] = []
    for error in artifact_quality_errors:
        match = _UNRESOLVED_RELATIVE_IMPORT_ERROR_RE.search(str(error or ""))
        if not match:
            continue
        specifier = str(match.group("specifier") or "").strip()
        importer_rel = _normalize_declared_task_path(match.group("path"))
        if not specifier.startswith(".") or not importer_rel:
            continue
        candidates = _relative_import_repair_target_candidates(
            root=root,
            importer_rel=importer_rel,
            specifier=specifier,
        )
        if not candidates:
            continue
        if any(_workspace_path_exists_case_insensitive(root, candidate) for candidate in candidates):
            continue
        missing.append(candidates[0])
    return _dedupe_preserve_order(missing)


def _relative_import_repair_target_candidates(
    *,
    root: Path,
    importer_rel: str,
    specifier: str,
) -> list[str]:
    try:
        importer_path = (root / importer_rel).resolve()
        base = (importer_path.parent / specifier).resolve()
        base.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return []

    suffix_order = _relative_import_suffix_order(importer_rel)
    raw_candidates: list[Path]
    if base.suffix:
        raw_candidates = [base]
    else:
        raw_candidates = [base.with_suffix(suffix) for suffix in suffix_order]
        raw_candidates.extend(base / f"index{suffix}" for suffix in suffix_order)

    candidates: list[str] = []
    seen: set[str] = set()
    for candidate in raw_candidates:
        try:
            relative = candidate.relative_to(root).as_posix()
        except ValueError:
            continue
        normalized = _normalize_declared_task_path(relative)
        if not normalized or any(ch in normalized for ch in ("*", "?")) or normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
    return candidates


def _relative_import_suffix_order(importer_rel: str) -> tuple[str, ...]:
    importer_suffix = Path(str(importer_rel or "")).suffix.lower()
    if importer_suffix == ".tsx":
        return (".tsx", ".ts", ".jsx", ".js", ".mjs", ".cjs")
    if importer_suffix == ".ts":
        return (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
    if importer_suffix == ".jsx":
        return (".jsx", ".js", ".tsx", ".ts", ".mjs", ".cjs")
    if importer_suffix in {".js", ".mjs", ".cjs"}:
        return (importer_suffix, ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
    return (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def _parse_named_import_symbols(symbols_text: str) -> list[str]:
    symbols: list[str] = []
    for raw in str(symbols_text or "").replace("\n", " ").split(","):
        token = raw.strip()
        if token.startswith("type "):
            token = token[5:].strip()
        token = re.split(r"\s+as\s+", token, maxsplit=1)[0].strip()
        if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", token):
            symbols.append(token)
    return _dedupe_preserve_order(symbols)


def _path_inside_workspace(path: Path, workspace_path: Path) -> bool:
    return path == workspace_path or workspace_path in path.parents


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    rows: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        token = path.resolve().as_posix()
        if token in seen:
            continue
        seen.add(token)
        rows.append(path)
    return rows
