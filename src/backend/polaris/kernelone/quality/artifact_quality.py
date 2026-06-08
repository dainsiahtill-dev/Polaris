"""Artifact quality checks shared by Director and integration QA."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from pathlib import Path

_ARTIFACT_QUALITY_SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".polaris",
    ".pytest_cache",
    ".ruff_cache",
    ".vite",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
}
_ARTIFACT_QUALITY_SOURCE_EXTS = {
    ".cjs",
    ".css",
    ".html",
    ".js",
    ".jsx",
    ".mjs",
    ".py",
    ".ts",
    ".tsx",
}
_DETERMINISTIC_SCAFFOLD_MARKERS = (
    "audit-seed",
    "planning scenario",
    "deterministic-declared-scope-v1",
    "createGameViewScaffoldState",
    "createCombatSystemScaffoldState",
    "Created by Polaris",
    "Generated file for",
    "generated-project",
    "build verification completed",
    "test verification completed",
    "structural build passed",
    "structural tests passed",
)
_NUMERIC_HELPER_FILLER_RE = re.compile(
    r"export\s+function\s+\w+Helper\d+\s*"
    r"\(\s*value\s*:\s*number\s*\)\s*:\s*number\s*"
    r"\{\s*return\s+value\s*\+\s*\d+\s*;\s*\}",
    re.IGNORECASE,
)
_GENERIC_STORE_RECORD_RE = re.compile(
    r"export\s+interface\s+\w+Record\s*\{[^}]*"
    r"payload\s*:\s*string\s*;[^}]*"
    r"index\s*:\s*number\s*;[^}]*\}",
    re.IGNORECASE | re.DOTALL,
)
_GENERIC_STORE_MAP_RE = re.compile(
    r"private\s+readonly\s+items\s*=\s*new\s+Map\s*<\s*string\s*,\s*\w+Record\s*>",
    re.IGNORECASE,
)
_TRIVIAL_ARITHMETIC_EXPECT_RE = re.compile(
    r"expect\s*\(\s*\d+\s*(?:[+\-*/])\s*\d+\s*\)\s*\.\s*to(?:Be|Equal)\s*\(\s*\d+\s*\)",
    re.IGNORECASE,
)
_PATCH_RESIDUE_RE = re.compile(
    r"(?m)^\s*(?:<{4,7}\s*SEARCH\b|>{4,7}\s*REPLACE\b|END\s+PATCH_FILE\b|PATCH_FILE(?::|\s+))",
    re.IGNORECASE,
)
_TS_RETURN_OBJECT_BLOCK_RE = re.compile(r"return\s*\{(?P<body>.*?)^\s*\};", re.DOTALL | re.MULTILINE)
_TS_OBJECT_PROPERTY_SEMICOLON_RE = re.compile(r"(?m)^\s*[A-Za-z_$][\w$]*\s*;\s*$")
_TS_LINE_COMMENT_ESCAPED_NEWLINE_CODE_RE = re.compile(
    r"//[^\r\n]*\\n\s*(?:export|import|const|let|var|class|function|interface|type|enum)\b",
    re.IGNORECASE,
)
_TS_ZOD_INFERRED_TYPE_RE = re.compile(
    r"(?:^|\n)\s*(?:export\s+)?type\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
    r"z\.infer\s*<\s*typeof\s+[A-Za-z_$][\w$]*\s*>\s*;",
    re.MULTILINE,
)
_IMPORT_SPECIFIER_RE = re.compile(
    r"(?:^|\n)\s*(?:import\s+(?:type\s+)?(?:[^'\"\n]*?\s+from\s+)?|export\s+[^'\"\n]*?\s+from\s+)"
    r"[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_TS_JS_SOURCE_EXTS = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
_TS_SOURCE_EXTS = {".ts", ".tsx"}
_NODE_BUILTIN_IMPORTS = {
    "assert",
    "async_hooks",
    "buffer",
    "child_process",
    "crypto",
    "events",
    "fs",
    "http",
    "https",
    "os",
    "path",
    "process",
    "stream",
    "timers",
    "url",
    "util",
    "zlib",
}
_TEST_FRAMEWORK_IMPORTS = {"@jest/globals", "jest", "vitest", "mocha"}
_NPM_TEST_RUNNER_SCRIPT_RE = re.compile(r"(?:^|[\s;&|])(vitest|jest|mocha|ava)(?:$|[\s;&|])", re.IGNORECASE)
_PYTHON_COMMAND_IN_NPM_SCRIPT_RE = re.compile(r"(?:^|[\s;&|])(python3?|pytest|pip3?)(?:$|[\s;&|])", re.IGNORECASE)
_PYTHON_PACKAGE_MANIFEST_DEPENDENCIES = {
    "django",
    "fastapi",
    "flask",
    "pandas",
    "pydantic",
    "pytest",
    "sqlalchemy",
    "uvicorn",
}


def scan_workspace_artifact_quality(
    workspace_full: str,
    *,
    relative_paths: Iterable[str] | None = None,
) -> list[str]:
    """Reject known worthless generated artifacts.

    When ``relative_paths`` is provided, only those workspace-relative files are
    scanned. This lets Director validate the files it just changed without
    failing unrelated seed files that later tasks are expected to repair. QA
    calls this without ``relative_paths`` to scan the complete final workspace.
    """

    try:
        root_full = Path(workspace_full).resolve()
    except (OSError, RuntimeError, ValueError):
        return ["Artifact quality scan failed: workspace path cannot be resolved"]
    if not root_full.exists() or not root_full.is_dir():
        return ["Artifact quality scan failed: workspace path does not exist"]

    errors: list[str] = []
    try:
        paths = (
            _iter_target_files(root_full, relative_paths)
            if relative_paths is not None
            else _iter_workspace_source_files(root_full)
        )
        for full_path in paths:
            if len(errors) >= 50:
                return errors
            relative_path = full_path.relative_to(root_full).as_posix()
            errors.extend(_scan_file(root_full, full_path, relative_path))
    except (OSError, RuntimeError, ValueError) as exc:
        return [f"Artifact quality scan failed: {exc}"]
    return errors


def _iter_workspace_source_files(root_full: Path) -> Iterable[Path]:
    for current_root, dir_names, file_names in os.walk(root_full):
        dir_names[:] = [name for name in dir_names if name not in _ARTIFACT_QUALITY_SKIP_DIRS]
        current = Path(current_root)
        for name in file_names:
            full_path = current / name
            if _is_source_artifact(full_path):
                yield full_path


def _iter_target_files(root_full: Path, relative_paths: Iterable[str] | None) -> Iterable[Path]:
    seen: set[Path] = set()
    for raw_path in relative_paths or ():
        normalized = str(raw_path or "").strip().replace("\\", "/")
        if not normalized:
            continue
        full_path = (root_full / normalized).resolve()
        try:
            full_path.relative_to(root_full)
        except ValueError:
            continue
        if full_path in seen:
            continue
        if any(part in _ARTIFACT_QUALITY_SKIP_DIRS for part in full_path.relative_to(root_full).parts):
            continue
        if full_path.is_dir():
            for nested_path in _iter_workspace_source_files(full_path):
                nested_resolved = nested_path.resolve()
                try:
                    nested_relative_parts = nested_resolved.relative_to(root_full).parts
                except ValueError:
                    continue
                if (
                    nested_resolved in seen
                    or any(part in _ARTIFACT_QUALITY_SKIP_DIRS for part in nested_relative_parts)
                    or not _is_source_artifact(nested_resolved)
                ):
                    continue
                seen.add(nested_resolved)
                yield nested_resolved
            continue
        if not full_path.is_file() or not _is_source_artifact(full_path):
            continue
        seen.add(full_path)
        yield full_path


def _is_source_artifact(path: Path) -> bool:
    return path.name.lower() == "package.json" or path.suffix.lower() in _ARTIFACT_QUALITY_SOURCE_EXTS


def _scan_file(root_full: Path, full_path: Path, relative_path: str) -> list[str]:
    try:
        text = full_path.read_text(encoding="utf-8", errors="replace")[:1_000_000]
    except (OSError, RuntimeError, ValueError):
        return []

    errors: list[str] = []
    if os.path.basename(relative_path).lower() == "package.json":
        errors.extend(_scan_package_manifest(root_full, text, relative_path))
    errors.extend(_scan_typescript_imports(root_full, full_path, text, relative_path))
    errors.extend(_scan_typescript_syntax_red_flags(full_path, text, relative_path))
    for marker in _DETERMINISTIC_SCAFFOLD_MARKERS:
        if marker in text:
            errors.append(f"Artifact quality scan failed: deterministic scaffold marker {marker!r} in {relative_path}")
            break
    helper_count = len(_NUMERIC_HELPER_FILLER_RE.findall(text))
    if helper_count >= 5:
        errors.append(
            f"Artifact quality scan failed: repeated numeric helper filler in {relative_path} (count={helper_count})"
        )
    if helper_count >= 3 and _GENERIC_STORE_RECORD_RE.search(text) and _GENERIC_STORE_MAP_RE.search(text):
        errors.append(f"Artifact quality scan failed: generic payload/index store scaffold in {relative_path}")
    if _PATCH_RESIDUE_RE.search(text):
        errors.append(f"Artifact quality scan failed: patch residue marker in {relative_path}")
    if _is_test_like_artifact_path(relative_path):
        trivial_count = len(_TRIVIAL_ARITHMETIC_EXPECT_RE.findall(text))
        if trivial_count >= 3:
            errors.append(
                "Artifact quality scan failed: repeated trivial arithmetic placeholder "
                f"tests in {relative_path} (count={trivial_count})"
            )
    return errors


def _scan_typescript_syntax_red_flags(full_path: Path, text: str, relative_path: str) -> list[str]:
    if full_path.suffix.lower() not in _TS_JS_SOURCE_EXTS:
        return []
    if _typescript_line_comment_contains_escaped_newline_code(text):
        return [
            f"Artifact quality scan failed: TypeScript escaped newline in line comment before code in {relative_path}"
        ]
    collision_name = _typescript_zod_inferred_type_class_collision_name(text)
    if collision_name:
        return [
            "Artifact quality scan failed: TypeScript zod inferred type collides "
            f"with class {collision_name} in {relative_path}"
        ]
    for match in _TS_RETURN_OBJECT_BLOCK_RE.finditer(text):
        if _TS_OBJECT_PROPERTY_SEMICOLON_RE.search(match.group("body")):
            return [
                "Artifact quality scan failed: TypeScript return object contains "
                f"semicolon-terminated property in {relative_path}"
            ]
    return []


def _typescript_zod_inferred_type_class_collision_name(text: str) -> str:
    for match in _TS_ZOD_INFERRED_TYPE_RE.finditer(str(text or "")):
        name = str(match.group("name") or "").strip()
        if not name:
            continue
        class_re = re.compile(rf"(?:^|\n)\s*(?:export\s+)?class\s+{re.escape(name)}\b", re.MULTILINE)
        if class_re.search(text):
            return name
    return ""


def _typescript_line_comment_contains_escaped_newline_code(text: str) -> bool:
    for raw_line in str(text or "").splitlines():
        if "//" not in raw_line or "\\n" not in raw_line:
            continue
        comment_index = raw_line.find("//")
        if comment_index < 0:
            continue
        if _TS_LINE_COMMENT_ESCAPED_NEWLINE_CODE_RE.search(raw_line[comment_index:]):
            return True
    return False


def _scan_package_manifest(root_full: Path, text: str, relative_path: str) -> list[str]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    errors: list[str] = []
    scripts = payload.get("scripts")
    if isinstance(scripts, dict):
        test_script = str(scripts.get("test") or "")
        lowered = test_script.lower()
        if "no test specified" in lowered or "no tests specified" in lowered:
            errors.append(f"Artifact quality scan failed: npm default failing test script in {relative_path}")
        if (
            _NPM_TEST_RUNNER_SCRIPT_RE.search(test_script)
            and _workspace_has_node_source_files(root_full)
            and not _workspace_has_node_test_files(root_full)
        ):
            errors.append(
                "Artifact quality scan failed: npm package manifest has test runner script "
                f"but no test/spec files exist in {relative_path}"
            )
        for script_name, script_value in scripts.items():
            script_text = str(script_value or "")
            if _PYTHON_COMMAND_IN_NPM_SCRIPT_RE.search(script_text):
                errors.append(
                    "Artifact quality scan failed: npm package manifest contains "
                    f"Python command in script {str(script_name)!r} in {relative_path}"
                )
                break
    main_entry = str(payload.get("main") or "").strip().replace("\\", "/").lower()
    if main_entry.endswith(".py"):
        errors.append(
            f"Artifact quality scan failed: npm package manifest contains Python runtime entrypoint in {relative_path}"
        )
    for section_name in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        section = payload.get(section_name)
        if not isinstance(section, dict):
            continue
        for package_name in section:
            normalized = str(package_name or "").strip().lower()
            if normalized in _PYTHON_PACKAGE_MANIFEST_DEPENDENCIES:
                errors.append(
                    "Artifact quality scan failed: npm package manifest declares "
                    f"Python package dependency {package_name!r} in {relative_path}"
                )
                return errors
    return errors


def _workspace_has_node_source_files(root_full: Path) -> bool:
    for relative_path in _iter_workspace_relative_files(root_full):
        if _is_test_like_artifact_path(relative_path):
            continue
        if Path(relative_path).suffix.lower() in _TS_JS_SOURCE_EXTS:
            return True
    return False


def _workspace_has_node_test_files(root_full: Path) -> bool:
    return any(
        _is_test_like_artifact_path(relative_path) for relative_path in _iter_workspace_relative_files(root_full)
    )


def _iter_workspace_relative_files(root_full: Path) -> Iterable[str]:
    for current_root, dir_names, file_names in os.walk(root_full):
        dir_names[:] = [name for name in dir_names if name not in _ARTIFACT_QUALITY_SKIP_DIRS]
        current = Path(current_root)
        for name in file_names:
            full_path = current / name
            try:
                relative_path = full_path.relative_to(root_full).as_posix()
            except ValueError:
                continue
            yield relative_path


def _scan_typescript_imports(root_full: Path, full_path: Path, text: str, relative_path: str) -> list[str]:
    if full_path.suffix.lower() not in _TS_JS_SOURCE_EXTS:
        return []
    declared_dependencies = _declared_package_dependencies(root_full)
    errors: list[str] = []
    is_typescript = full_path.suffix.lower() in _TS_SOURCE_EXTS
    has_package_manifest = (root_full / "package.json").is_file()
    node_types_error_added = False
    for match in _IMPORT_SPECIFIER_RE.finditer(text):
        specifier = str(match.group(1) or "").strip()
        if not specifier:
            continue
        if specifier.startswith((".", "/")):
            if not _relative_import_exists(root_full, full_path, specifier):
                errors.append(
                    f"Artifact quality scan failed: unresolved relative import {specifier!r} in {relative_path}"
                )
            continue
        if _is_test_like_artifact_path(relative_path) and _package_root_name(specifier) in _TEST_FRAMEWORK_IMPORTS:
            continue
        root_name = _package_root_name(specifier)
        builtin_name = _node_builtin_root_name(specifier)
        if builtin_name in _NODE_BUILTIN_IMPORTS:
            if (
                is_typescript
                and has_package_manifest
                and not node_types_error_added
                and not _node_types_declared(declared_dependencies)
            ):
                errors.append(
                    "Artifact quality scan failed: TypeScript node builtin import "
                    f"{specifier!r} requires '@types/node' in {relative_path}"
                )
                node_types_error_added = True
            continue
        if root_name in declared_dependencies:
            continue
        if not _is_test_like_artifact_path(relative_path):
            errors.append(f"Artifact quality scan failed: undeclared runtime import {specifier!r} in {relative_path}")
    return errors


def _declared_package_dependencies(root_full: Path) -> set[str]:
    package_path = root_full / "package.json"
    try:
        payload = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return set()
    if not isinstance(payload, dict):
        return set()
    declared: set[str] = set()
    for section_name in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        section = payload.get(section_name)
        if isinstance(section, dict):
            declared.update(str(name).strip() for name in section if str(name).strip())
    return declared


def _package_root_name(specifier: str) -> str:
    token = str(specifier or "").strip()
    if token.startswith("@"):
        parts = token.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else token
    return token.split("/", 1)[0]


def _node_builtin_root_name(specifier: str) -> str:
    token = str(specifier or "").strip()
    if token.startswith("node:"):
        token = token.removeprefix("node:")
    return token.split("/", 1)[0]


def _node_types_declared(declared_dependencies: set[str]) -> bool:
    return "@types/node" in declared_dependencies


def _relative_import_exists(root_full: Path, importer_path: Path, specifier: str) -> bool:
    base = (
        (importer_path.parent / specifier).resolve() if specifier.startswith(".") else (root_full / specifier).resolve()
    )
    try:
        base.relative_to(root_full)
    except ValueError:
        return False
    for candidate in _relative_import_candidates(base):
        try:
            candidate.relative_to(root_full)
        except ValueError:
            continue
        if candidate.is_file():
            return True
    return False


def _relative_import_candidates(base: Path) -> list[Path]:
    suffixes = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".d.ts")
    raw_candidates: list[Path] = [base]
    if base.suffix:
        if base.suffix.lower() in suffixes:
            raw_candidates.extend(base.with_suffix(suffix) for suffix in suffixes)
        else:
            raw_candidates.extend(Path(f"{base}{suffix}") for suffix in suffixes)
            raw_candidates.extend(base.with_suffix(suffix) for suffix in suffixes)
    else:
        raw_candidates.extend(base.with_suffix(suffix) for suffix in suffixes)
        raw_candidates.extend(base / f"index{suffix}" for suffix in suffixes)

    candidates: list[Path] = []
    seen: set[Path] = set()
    for candidate in raw_candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(candidate)
    return candidates


def _is_test_like_artifact_path(relative_path: str) -> bool:
    normalized = str(relative_path or "").replace("\\", "/").lower()
    name = os.path.basename(normalized)
    return (
        "/test/" in normalized
        or "/tests/" in normalized
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
        or name.endswith("_test.py")
    )
