"""TypeScript, HTML-module, and import-coherence scans."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from polaris.kernelone.quality.artifact_quality._constants import (
    _ARTIFACT_QUALITY_ERROR_PREFIX,
    _HTML_INLINE_MODULE_SCRIPT_RE,
    _HTML_INLINE_TYPESCRIPT_IMPORT_RE,
    _HTML_JAVASCRIPT_MODULE_SCRIPT_RE,
    _HTML_TYPESCRIPT_MODULE_SCRIPT_RE,
    _IMPORT_SPECIFIER_RE,
    _NODE_BUILTIN_IMPORTS,
    _REMOVED_TYPESCRIPT_COMPILER_OPTIONS,
    _TEST_FRAMEWORK_IMPORTS,
    _TS_DYNAMIC_EXPORT_RE,
    _TS_EXPORT_CLAUSE_RE,
    _TS_EXPORT_DECL_RE,
    _TS_EXPORT_DEFAULT_RE,
    _TS_JS_SOURCE_EXTS,
    _TS_LINE_COMMENT_ESCAPED_NEWLINE_CODE_RE,
    _TS_NAMED_IMPORT_RE,
    _TS_OBJECT_PROPERTY_SEMICOLON_RE,
    _TS_SOURCE_EXTS,
    _TS_SYMBOL_COHERENCE_FLAG,
    _TS_TYPE_DECL_RE,
    _TS_ZOD_INFERRED_TYPE_RE,
    _TSC_PROJECT_CHECK_FLAG,
)
from polaris.kernelone.quality.artifact_quality._helpers import (
    _is_test_like_artifact_path,
    _iter_workspace_source_files,
    _package_root_name,
)
from polaris.kernelone.quality.artifact_quality._issues import (
    _file_artifact_quality_issue,
)
from polaris.kernelone.quality.artifact_quality._models import (
    ArtifactQualityIssue,
    _FileArtifactQualityEvidence,
)
from polaris.kernelone.quality.artifact_quality._syntax import (
    _iter_typescript_return_object_bodies,
)


def _scan_typescript_tsconfig_evidence(text: str, relative_path: str) -> _FileArtifactQualityEvidence:
    """Return typed tsconfig findings that can be repaired without parsing tsc prose."""

    try:
        payload = json.loads(str(text or "{}"))
    except json.JSONDecodeError:
        return _FileArtifactQualityEvidence()
    if not isinstance(payload, Mapping):
        return _FileArtifactQualityEvidence()
    compiler_options = payload.get("compilerOptions")
    if not isinstance(compiler_options, Mapping):
        return _FileArtifactQualityEvidence()

    errors: list[str] = []
    issues: list[ArtifactQualityIssue] = []
    for option_name, diagnostic_code in _REMOVED_TYPESCRIPT_COMPILER_OPTIONS.items():
        if option_name not in compiler_options:
            continue
        error = (
            "Artifact quality scan failed: tsconfig "
            f"compilerOptions.{option_name} is removed by TypeScript 5 ({diagnostic_code}); "
            f"remove it from {relative_path}"
        )
        errors.append(error)
        issues.append(
            _file_artifact_quality_issue(
                error,
                relative_path,
                code="tsconfig_removed_compiler_option",
                source="typescript_tsconfig_scanner",
                metadata={
                    "diagnostic_kind": "tsconfig_removed_compiler_option",
                    "diagnostic_code": diagnostic_code,
                    "config_path": relative_path,
                    "compiler_option": option_name,
                    "json_path": ("compilerOptions", option_name),
                },
            )
        )
    return _FileArtifactQualityEvidence(errors=tuple(errors), issues=tuple(issues))


def _typescript_syntax_red_flag_issue(
    *,
    error: str,
    code: str,
    relative_path: str,
    metadata: Mapping[str, Any] | None = None,
) -> ArtifactQualityIssue:
    message = str(error or "").strip()
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    return ArtifactQualityIssue(
        code=code,
        message=message,
        path=relative_path,
        source="typescript_syntax_red_flag_scanner",
        metadata={
            "raw": str(error or "").strip(),
            "path": relative_path,
            **dict(metadata or {}),
        },
    )


def _scan_typescript_syntax_red_flag_evidence(
    root_full: Path,
    full_path: Path,
    text: str,
    relative_path: str,
) -> _FileArtifactQualityEvidence:
    """Return TypeScript syntax red flags as strings and direct typed issues."""

    suffix = full_path.suffix.lower()
    if suffix not in _TS_JS_SOURCE_EXTS:
        return _FileArtifactQualityEvidence()
    if _typescript_line_comment_contains_escaped_newline_code(text):
        error = (
            f"Artifact quality scan failed: TypeScript escaped newline in line comment before code in {relative_path}"
        )
        return _FileArtifactQualityEvidence(
            errors=(error,),
            issues=(
                _typescript_syntax_red_flag_issue(
                    error=error,
                    code="typescript_escaped_newline_line_comment",
                    relative_path=relative_path,
                    metadata={
                        "diagnostic_kind": "typescript_escaped_newline_line_comment",
                    },
                ),
            ),
        )
    if suffix not in _TS_SOURCE_EXTS:
        return _FileArtifactQualityEvidence()
    collision_name = _typescript_zod_inferred_type_class_collision_name(text)
    if collision_name:
        error = (
            "Artifact quality scan failed: TypeScript zod inferred type collides "
            f"with class {collision_name} in {relative_path}"
        )
        return _FileArtifactQualityEvidence(
            errors=(error,),
            issues=(
                _typescript_syntax_red_flag_issue(
                    error=error,
                    code="typescript_zod_type_class_collision",
                    relative_path=relative_path,
                    metadata={
                        "collision_name": collision_name,
                        "diagnostic_kind": "typescript_zod_type_class_collision",
                    },
                ),
            ),
        )
    for body in _iter_typescript_return_object_bodies(text):
        if _TS_OBJECT_PROPERTY_SEMICOLON_RE.search(body):
            error = (
                "Artifact quality scan failed: TypeScript return object contains "
                f"semicolon-terminated property in {relative_path}"
            )
            return _FileArtifactQualityEvidence(
                errors=(error,),
                issues=(
                    _typescript_syntax_red_flag_issue(
                        error=error,
                        code="typescript_return_object_semicolon_property",
                        relative_path=relative_path,
                        metadata={
                            "diagnostic_kind": "typescript_return_object_semicolon_property",
                        },
                    ),
                ),
            )
    type_export_error = _typescript_isolated_modules_type_reexport_error(root_full, text)
    if type_export_error:
        error = (
            "Artifact quality scan failed: TypeScript isolatedModules requires "
            f"`export type` for {type_export_error} in {relative_path}"
        )
        return _FileArtifactQualityEvidence(
            errors=(error,),
            issues=(
                _typescript_syntax_red_flag_issue(
                    error=error,
                    code="typescript_isolated_modules_type_reexport",
                    relative_path=relative_path,
                    metadata={
                        "export_name": type_export_error,
                        "diagnostic_kind": "typescript_isolated_modules_type_reexport",
                    },
                ),
            ),
        )
    return _FileArtifactQualityEvidence()


def _html_module_script_quality_issue(error: str, relative_path: str, *, src: str) -> ArtifactQualityIssue:
    message = str(error or "").strip()
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    return ArtifactQualityIssue(
        code="html_module_script_typescript_source",
        message=message,
        path=relative_path,
        source="html_module_script_scanner",
        metadata={
            "raw": str(error or "").strip(),
            "html_path": relative_path,
            "script_src": src,
            "diagnostic_kind": "html_module_script_typescript_source",
        },
    )


def _html_local_script_path(relative_path: str, src: str) -> str:
    raw_src = str(src or "").strip().split("#", 1)[0].split("?", 1)[0].replace("\\", "/")
    if not raw_src or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", raw_src) or raw_src.startswith("//"):
        return ""
    html_parent = PurePosixPath(str(relative_path or "").replace("\\", "/")).parent
    normalized = PurePosixPath(raw_src.lstrip("/")) if raw_src.startswith("/") else html_parent / raw_src
    normalized_text = str(normalized).replace("\\", "/")
    while normalized_text.startswith("./"):
        normalized_text = normalized_text[2:]
    if not normalized_text or normalized_text.startswith("../") or "/../" in normalized_text:
        return ""
    return normalized_text.strip("/")


def _html_script_source_sibling(root_full: Path, script_path: str) -> str:
    normalized = str(script_path or "").strip().replace("\\", "/")
    if not normalized.endswith(".js"):
        return ""
    stem = normalized[:-3]
    for suffix in (".ts", ".tsx"):
        candidate = f"{stem}{suffix}"
        if (root_full / candidate).is_file():
            return candidate
    return ""


def _typescript_compiled_output_path(root_full: Path, source_path: str) -> str:
    out_dir = "dist"
    root_dir = ""
    config_path = root_full / "tsconfig.json"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        payload = {}
    if isinstance(payload, dict):
        compiler_options = payload.get("compilerOptions")
        if isinstance(compiler_options, dict):
            out_dir_value = compiler_options.get("outDir")
            root_dir_value = compiler_options.get("rootDir")
            if isinstance(out_dir_value, str) and out_dir_value.strip():
                out_dir = out_dir_value.strip().replace("\\", "/").strip("/") or out_dir
            if isinstance(root_dir_value, str) and root_dir_value.strip():
                root_dir = root_dir_value.strip().replace("\\", "/").strip("/")

    normalized_source = str(source_path or "").strip().replace("\\", "/").strip("/")
    source_without_ext = re.sub(r"\.tsx?$", "", normalized_source)
    if root_dir and source_without_ext.startswith(f"{root_dir}/"):
        emitted_relative = source_without_ext[len(root_dir) + 1 :]
    elif not root_dir and source_without_ext.startswith("src/"):
        emitted_relative = source_without_ext[4:]
    else:
        emitted_relative = source_without_ext
    return f"{out_dir}/{emitted_relative}.js".replace("//", "/").strip("/")


def _html_script_prefixed_path(original_src: str, normalized_path: str) -> str:
    raw = str(original_src or "").strip()
    normalized = str(normalized_path or "").strip().replace("\\", "/").strip("/")
    if not normalized:
        return ""
    if raw.startswith("/"):
        return f"/{normalized}"
    if raw.startswith("./"):
        return f"./{normalized}"
    return normalized


def _html_module_script_compiled_javascript_issue(
    *,
    root_full: Path,
    relative_path: str,
    src: str,
) -> tuple[str, ArtifactQualityIssue] | None:
    script_path = _html_local_script_path(relative_path, src)
    if not script_path or not script_path.endswith(".js") or (root_full / script_path).is_file():
        return None
    source_path = _html_script_source_sibling(root_full, script_path)
    if not source_path:
        return None
    emitted_path = _typescript_compiled_output_path(root_full, source_path)
    emitted_ref = _html_script_prefixed_path(src, emitted_path)
    source_ref = _html_script_prefixed_path(src, script_path)
    error = (
        "Artifact quality scan failed: HTML module script references missing compiled JavaScript "
        f"{source_ref!r} in {relative_path}; TypeScript build emitted {emitted_ref!r}"
    )
    message = error[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    return (
        error,
        ArtifactQualityIssue(
            code="html_module_script_compiled_javascript_missing",
            message=message,
            path=relative_path,
            source="html_module_script_scanner",
            metadata={
                "raw": error,
                "html_path": relative_path,
                "script_src": src,
                "compiled_script_src": source_ref,
                "typescript_source": source_path,
                "emitted_script_src": emitted_ref,
                "diagnostic_kind": "html_module_script_compiled_javascript_missing",
            },
        ),
    )


def _scan_html_typescript_module_script_evidence(
    root_full: Path,
    full_path: Path,
    text: str,
    relative_path: str,
) -> _FileArtifactQualityEvidence:
    """Return HTML module-script findings as strings and direct typed issues."""

    if full_path.suffix.lower() not in {".html", ".htm"}:
        return _FileArtifactQualityEvidence()
    errors: list[str] = []
    issues: list[ArtifactQualityIssue] = []
    for match in _HTML_TYPESCRIPT_MODULE_SCRIPT_RE.finditer(text):
        src = str(match.group("src") or "").strip()
        if src:
            error = (
                "Artifact quality scan failed: HTML module script references TypeScript source "
                f"{src!r} in {relative_path}; static entrypoints must load JavaScript"
            )
            errors.append(error)
            issues.append(_html_module_script_quality_issue(error, relative_path, src=src))
    for script_match in _HTML_INLINE_MODULE_SCRIPT_RE.finditer(text):
        body = str(script_match.group("body") or "")
        for import_match in _HTML_INLINE_TYPESCRIPT_IMPORT_RE.finditer(body):
            src = str(import_match.group("src") or "").strip()
            if not src:
                continue
            error = (
                "Artifact quality scan failed: HTML module script references TypeScript source "
                f"{src!r} in {relative_path}; static entrypoints must load JavaScript"
            )
            errors.append(error)
            issues.append(_html_module_script_quality_issue(error, relative_path, src=src))
    for match in _HTML_JAVASCRIPT_MODULE_SCRIPT_RE.finditer(text):
        src = str(match.group("src") or "").strip()
        if not src:
            continue
        compiled_issue = _html_module_script_compiled_javascript_issue(
            root_full=root_full,
            relative_path=relative_path,
            src=src,
        )
        if compiled_issue is None:
            continue
        error, issue = compiled_issue
        errors.append(error)
        issues.append(issue)
    return _FileArtifactQualityEvidence(errors=tuple(errors), issues=tuple(issues))


def _typescript_isolated_modules_type_reexport_error(root_full: Path, text: str) -> str:
    if not _typescript_project_uses_isolated_modules(root_full):
        return ""
    type_names = {str(match.group("name") or "") for match in _TS_TYPE_DECL_RE.finditer(text)}
    if not type_names:
        return ""
    for match in _TS_EXPORT_CLAUSE_RE.finditer(text):
        inner = str(match.group("inner") or "")
        for raw in inner.split(","):
            token = raw.strip()
            if not token or token.startswith("type "):
                continue
            exported_name = re.split(r"\s+as\s+", token)[0].strip()
            if exported_name in type_names:
                return exported_name
    return ""


def _typescript_project_uses_isolated_modules(root_full: Path) -> bool:
    try:
        payload = json.loads((root_full / "tsconfig.json").read_text(encoding="utf-8"))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    compiler_options = payload.get("compilerOptions")
    return isinstance(compiler_options, dict) and compiler_options.get("isolatedModules") is True


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


_TYPESCRIPT_TYPECHECK_DETAIL_MAX_LINES = 40

_TYPESCRIPT_TYPECHECK_DETAIL_MAX_CHARS = 4000

_TS_DIAGNOSTIC_LINE_RE = re.compile(r"^(?P<path>(?:[A-Za-z]:)?[^:\n]+?)\((?P<line>\d+),(?P<col>\d+)\):")


def _typescript_typecheck_diagnostic_detail(raw_output: str, returncode: int) -> str:
    """Capture enough tsc output to preserve TS error codes for repair matching.

    L1-01 m03-r23: the previous single-line + 400-char truncation stripped the
    TS error codes (TS2584/TS2304) and DOM-global messages that the repair
    coverage needs to match the ``tsconfig_lib`` / ``tsconfig_dom_*`` rules.
    With only a generic ``typescript_project_typecheck_failed`` code reaching
    coverage, no DOM-lib repair fired and the build died on ~19000 missing-lib
    errors. Preserve the first N non-empty lines (bounded) so the existing
    DOM-lib repair can match and add ``lib: ['DOM']``.
    """
    lines = [line for line in str(raw_output or "").splitlines() if line.strip()]
    detail = "\n".join(lines[:_TYPESCRIPT_TYPECHECK_DETAIL_MAX_LINES])
    if not detail:
        detail = f"tsc --noEmit exited with code {returncode}"
    return detail[:_TYPESCRIPT_TYPECHECK_DETAIL_MAX_CHARS]


def _normalize_typecheck_scope_path(path: str) -> str:
    token = str(path or "").strip().replace("\\", "/")
    while token.startswith("./"):
        token = token[2:]
    return token


def _typescript_typecheck_output_for_paths(raw_output: str, relative_paths: list[str]) -> str:
    """Keep tsc diagnostic lines that name a file in the current scan scope.

    Live L2-17: a TASK-2 scan of `src/web.ts` still ran project `tsc` and
    fail-closed on `src/models/index.ts` TS2307 owned by TASK-1-entrypoints.
    """

    allowed = {
        _normalize_typecheck_scope_path(path) for path in relative_paths if _normalize_typecheck_scope_path(path)
    }
    if not allowed:
        return ""
    kept: list[str] = []
    for line in str(raw_output or "").splitlines():
        match = _TS_DIAGNOSTIC_LINE_RE.match(line.strip())
        if match is None:
            continue
        path = _normalize_typecheck_scope_path(str(match.group("path") or ""))
        if path in allowed:
            kept.append(line)
    return "\n".join(kept)


def _typescript_project_typecheck_issue(
    *,
    error: str,
    detail: str,
    exit_code: int,
) -> ArtifactQualityIssue:
    message = str(error or "").strip()
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    return ArtifactQualityIssue(
        code="typescript_project_typecheck_failed",
        message=message,
        source="typescript_project_typecheck",
        metadata={
            "raw": str(error or "").strip(),
            "command": "tsc --noEmit --pretty false",
            "exit_code": exit_code,
            "detail": detail,
            "diagnostic_kind": "typescript_project_typecheck_failed",
        },
    )


def _scan_typescript_project_typecheck_evidence(
    root_full: Path, relative_paths: list[str]
) -> _FileArtifactQualityEvidence:
    """Return TypeScript project typecheck findings as strings and typed issues."""

    if os.environ.get(_TSC_PROJECT_CHECK_FLAG, "1").strip().lower() in {"0", "false", "no", "off"}:
        return _FileArtifactQualityEvidence()
    if not (root_full / "tsconfig.json").is_file():
        return _FileArtifactQualityEvidence()
    if not any(
        Path(path).suffix.lower() in {".ts", ".tsx"} or Path(path).name == "tsconfig.json" for path in relative_paths
    ):
        return _FileArtifactQualityEvidence()
    scoped_has_typescript_source = any(Path(path).suffix.lower() in {".ts", ".tsx"} for path in relative_paths)
    scoped_has_tsconfig = any(Path(path).name == "tsconfig.json" for path in relative_paths)
    if scoped_has_tsconfig and not scoped_has_typescript_source and not _workspace_has_typescript_source(root_full):
        return _FileArtifactQualityEvidence()
    tsc = sys.modules[__package__]._typescript_project_typecheck_command(root_full)
    if not tsc:
        return _FileArtifactQualityEvidence()
    try:
        proc = subprocess.run(
            [tsc, "--noEmit", "--pretty", "false"],
            cwd=str(root_full),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return _FileArtifactQualityEvidence()
    if proc.returncode == 0:
        return _FileArtifactQualityEvidence()
    scoped_output = _typescript_typecheck_output_for_paths(
        f"{proc.stdout}\n{proc.stderr}",
        relative_paths,
    )
    if not scoped_output.strip():
        return _FileArtifactQualityEvidence()
    detail = _typescript_typecheck_diagnostic_detail(scoped_output, proc.returncode)
    error = f"Artifact quality scan failed: TypeScript project typecheck failed: {detail}"
    return _FileArtifactQualityEvidence(
        errors=(error,),
        issues=(
            _typescript_project_typecheck_issue(
                error=error,
                detail=detail,
                exit_code=proc.returncode,
            ),
        ),
    )


def _workspace_has_typescript_source(root_full: Path) -> bool:
    """Return whether the workspace already contains TypeScript sources."""

    try:
        for full_path in _iter_workspace_source_files(root_full):
            if full_path.suffix.lower() in {".ts", ".tsx"}:
                return True
    except (OSError, RuntimeError, ValueError):
        return False
    return False


def _typescript_project_typecheck_command(root_full: Path) -> str:
    local_name = "tsc.cmd" if os.name == "nt" else "tsc"
    local_tsc = root_full / "node_modules" / ".bin" / local_name
    if local_tsc.is_file():
        return str(local_tsc)
    if (root_full / "package.json").is_file():
        return ""
    return shutil.which("tsc") or ""


def _typescript_project_requires_local_tsc(root_full: Path) -> bool:
    try:
        payload = json.loads((root_full / "tsconfig.json").read_text(encoding="utf-8"))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    compiler_options = payload.get("compilerOptions")
    if not isinstance(compiler_options, dict):
        return False
    module_resolution = str(compiler_options.get("moduleResolution") or "").strip().lower()
    return module_resolution == "bundler"


def _typescript_import_quality_issue(
    *,
    error: str,
    code: str,
    relative_path: str,
    metadata: Mapping[str, Any],
) -> ArtifactQualityIssue:
    message = str(error or "").strip()
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    return ArtifactQualityIssue(
        code=code,
        message=message,
        path=relative_path,
        source="typescript_import_scanner",
        metadata={
            "raw": str(error or "").strip(),
            "importer_path": relative_path,
            **dict(metadata),
        },
    )


def _scan_typescript_import_evidence(
    root_full: Path,
    full_path: Path,
    text: str,
    relative_path: str,
) -> _FileArtifactQualityEvidence:
    """Return TypeScript/JavaScript import findings as strings and typed issues."""

    if full_path.suffix.lower() not in _TS_JS_SOURCE_EXTS:
        return _FileArtifactQualityEvidence()
    declared_dependencies = _declared_package_dependencies(root_full)
    errors: list[str] = []
    issues: list[ArtifactQualityIssue] = []
    code_mask = _ts_js_code_mask(text)
    is_typescript = full_path.suffix.lower() in _TS_SOURCE_EXTS
    has_package_manifest = (root_full / "package.json").is_file()
    node_types_error_added = False
    for match in _IMPORT_SPECIFIER_RE.finditer(text):
        if not _match_starts_in_ts_js_code(code_mask, match.start()):
            continue
        specifier = str(match.group(1) or "").strip()
        if not specifier:
            continue
        if specifier.startswith((".", "/")):
            if not _relative_import_exists(root_full, full_path, specifier):
                error = f"Artifact quality scan failed: unresolved relative import {specifier!r} in {relative_path}"
                errors.append(error)
                issues.append(
                    _typescript_import_quality_issue(
                        error=error,
                        code="unresolved_relative_import",
                        relative_path=relative_path,
                        metadata={
                            "specifier": specifier,
                            "diagnostic_kind": "unresolved_relative_import",
                        },
                    )
                )
            continue
        if _is_test_like_artifact_path(relative_path) and _package_root_name(specifier) in _TEST_FRAMEWORK_IMPORTS:
            continue
        root_name = _package_root_name(specifier)
        builtin_name = _node_builtin_root_name(specifier)
        if specifier.startswith("node:") or builtin_name in _NODE_BUILTIN_IMPORTS:
            if (
                is_typescript
                and has_package_manifest
                and not node_types_error_added
                and not _node_types_declared(declared_dependencies)
            ):
                error = (
                    "Artifact quality scan failed: TypeScript node builtin import "
                    f"{specifier!r} requires '@types/node' in {relative_path}"
                )
                errors.append(error)
                issues.append(
                    _typescript_import_quality_issue(
                        error=error,
                        code="typescript_node_types_missing",
                        relative_path=relative_path,
                        metadata={
                            "specifier": specifier,
                            "required_dependency": "@types/node",
                            "diagnostic_kind": "typescript_node_types_missing",
                        },
                    )
                )
                node_types_error_added = True
            continue
        if root_name in declared_dependencies:
            continue
        if not _is_test_like_artifact_path(relative_path):
            error = f"Artifact quality scan failed: undeclared runtime import {specifier!r} in {relative_path}"
            errors.append(error)
            issues.append(
                _typescript_import_quality_issue(
                    error=error,
                    code="undeclared_runtime_import",
                    relative_path=relative_path,
                    metadata={
                        "specifier": specifier,
                        "package_root": root_name,
                        "diagnostic_kind": "undeclared_runtime_import",
                    },
                )
            )
    if _ts_symbol_coherence_enabled():
        symbol_coherence_evidence = _scan_typescript_symbol_coherence_evidence(
            root_full,
            full_path,
            text,
            relative_path,
            code_mask=code_mask,
        )
        errors.extend(symbol_coherence_evidence.errors)
        issues.extend(symbol_coherence_evidence.issues)
    return _FileArtifactQualityEvidence(errors=tuple(errors), issues=tuple(issues))


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


_TS_MODULE_READ_CAP_BYTES = 512 * 1024


def _ts_symbol_coherence_enabled() -> bool:
    """TS/JS cross-file symbol coherence is ON unless explicitly disabled."""
    return os.environ.get(_TS_SYMBOL_COHERENCE_FLAG, "1").strip().lower() not in {"0", "false", "no", "off"}


def _ts_js_code_mask(text: str) -> list[bool]:
    """Mark TS/JS source positions that are executable code.

    The artifact scanner is intentionally regex-based and conservative. This
    mask prevents fixture strings, template literals, and comments from being
    interpreted as real imports. Template literal expressions are skipped too:
    that can miss a rare dynamic case, but it avoids false positives in tests
    that embed generated source snippets.
    """

    source = str(text or "")
    mask = [True] * len(source)
    i = 0
    n = len(source)
    while i < n:
        char = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if char == "/" and nxt == "/":
            start = i
            i += 2
            while i < n and source[i] not in "\r\n":
                i += 1
            for pos in range(start, i):
                mask[pos] = False
            continue
        if char == "/" and nxt == "*":
            start = i
            i += 2
            while i + 1 < n and not (source[i] == "*" and source[i + 1] == "/"):
                i += 1
            i = min(n, i + 2)
            for pos in range(start, i):
                mask[pos] = False
            continue
        if char in {"'", '"', "`"}:
            quote = char
            start = i
            i += 1
            escaped = False
            while i < n:
                current = source[i]
                if escaped:
                    escaped = False
                    i += 1
                    continue
                if current == "\\":
                    escaped = True
                    i += 1
                    continue
                i += 1
                if current == quote:
                    break
            for pos in range(start, i):
                mask[pos] = False
            continue
        i += 1
    return mask


def _match_starts_in_ts_js_code(mask: list[bool], start: int) -> bool:
    return 0 <= start < len(mask) and mask[start]


def _parse_ts_clause_names(inner: str, *, for_export: bool) -> set[str]:
    """Parse the identifiers in an `import {…}` or `export {…}` clause.

    For exports the bound name is the alias (`A as B` exports ``B``); for imports
    the name that must exist in the sibling is the original (`A as B` imports
    ``A``). Inline type-only members (`type X`) are skipped — they are erased at
    runtime and carry ambient/declaration-merging risk we will not adjudicate.
    """
    names: set[str] = set()
    clause = str(inner or "")
    mask = _ts_js_code_mask(clause)
    cleaned_clause = "".join(char if mask[index] else " " for index, char in enumerate(clause))
    for raw in cleaned_clause.split(","):
        token = raw.strip()
        if not token or token == "type" or token.startswith("type "):
            continue
        parts = re.split(r"\s+as\s+", token)
        chosen = (parts[-1] if for_export else parts[0]).strip()
        if re.fullmatch(r"[A-Za-z_$][\w$]*", chosen):
            names.add(chosen)
    return names


def _typescript_module_exports(text: str) -> set[str] | None:
    """Best-effort export surface of a TS/JS module, or ``None`` (fail-open).

    Returns ``None`` whenever the surface cannot be safely determined (any
    surface-unknowable construct: ``export *``, ``export =``, CommonJS
    ``module.exports``/``exports.x``, ambient ``declare module``, destructured
    export). Capture is otherwise generous — missing a real export form would be
    a FALSE POSITIVE (a runnable product wrongly failed), whereas over-capturing
    only yields a benign false negative — so every plausible declaration and
    clause form is collected.
    """
    if not text:
        return None
    if _TS_DYNAMIC_EXPORT_RE.search(text):
        return None
    exports: set[str] = set()
    for match in _TS_EXPORT_DECL_RE.finditer(text):
        name = (
            match.group("fn") or match.group("cls") or match.group("ty") or match.group("cenum") or match.group("var")
        )
        if name:
            exports.add(name)
    for match in _TS_EXPORT_CLAUSE_RE.finditer(text):
        exports.update(_parse_ts_clause_names(match.group("inner"), for_export=True))
    if _TS_EXPORT_DEFAULT_RE.search(text):
        exports.add("default")
    return exports


def _resolve_typescript_module_file(root_full: Path, importer_path: Path, specifier: str) -> Path | None:
    """Resolve a RELATIVE TS/JS import specifier to its single sibling file."""
    if not specifier.startswith("."):
        return None
    base = (importer_path.parent / specifier).resolve()
    try:
        base.relative_to(root_full)
    except ValueError:
        return None
    for candidate in _relative_import_candidates(base):
        try:
            candidate.relative_to(root_full)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


def _read_typescript_module_exports(module_file: Path) -> set[str] | None:
    try:
        if module_file.stat().st_size > _TS_MODULE_READ_CAP_BYTES:
            return None
        content = module_file.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    return _typescript_module_exports(content)


def _typescript_symbol_coherence_quality_issue(
    *,
    error: str,
    relative_path: str,
    specifier: str,
    imported_symbol: str,
    module_file: Path,
    root_full: Path,
) -> ArtifactQualityIssue:
    message = str(error or "").strip()
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    try:
        exporter_path = module_file.relative_to(root_full).as_posix()
    except ValueError:
        exporter_path = module_file.as_posix()
    return ArtifactQualityIssue(
        code="typescript_import_unresolved_symbol",
        message=message,
        path=relative_path,
        source="typescript_symbol_coherence_scanner",
        metadata={
            "raw": str(error or "").strip(),
            "importer_path": relative_path,
            "exporter_path": exporter_path,
            "specifier": specifier,
            "imported_symbol": imported_symbol,
            "diagnostic_kind": "typescript_import_unresolved_symbol",
        },
    )


def _scan_typescript_symbol_coherence_evidence(
    root_full: Path,
    full_path: Path,
    text: str,
    relative_path: str,
    *,
    code_mask: list[bool] | None = None,
) -> _FileArtifactQualityEvidence:
    """Flag named imports of a resolvable relative sibling that the sibling never
    exports — the TS/JS analogue of the Python symbol-coherence check. Conservative
    by construction: only plain named imports of relative specifiers are checked,
    and any ambiguity (type-only import, unresolved specifier, unknowable export
    surface) is skipped, never flagged.
    """
    errors: list[str] = []
    issues: list[ArtifactQualityIssue] = []
    seen: set[tuple[str, str]] = set()
    exports_cache: dict[Path, set[str] | None] = {}
    mask = code_mask if code_mask is not None else _ts_js_code_mask(text)
    for match in _TS_NAMED_IMPORT_RE.finditer(text):
        if not _match_starts_in_ts_js_code(mask, match.start()):
            continue
        if match.group("typeonly"):
            continue
        specifier = str(match.group("spec") or "").strip()
        if not specifier.startswith("."):
            continue
        imported = _parse_ts_clause_names(match.group("names"), for_export=False)
        if not imported:
            continue
        module_file = _resolve_typescript_module_file(root_full, full_path, specifier)
        if module_file is None:
            continue
        if module_file not in exports_cache:
            exports_cache[module_file] = _read_typescript_module_exports(module_file)
        surface = exports_cache[module_file]
        if surface is None:
            continue
        for name in sorted(imported):
            if name in surface:
                continue
            key = (name, specifier)
            if key in seen:
                continue
            seen.add(key)
            error = (
                f"Artifact quality scan failed: unresolved import symbol {name!r} "
                f"from {specifier!r} in {relative_path} (sibling module does not define it)"
            )
            errors.append(error)
            issues.append(
                _typescript_symbol_coherence_quality_issue(
                    error=error,
                    relative_path=relative_path,
                    specifier=specifier,
                    imported_symbol=name,
                    module_file=module_file,
                    root_full=root_full,
                )
            )
    return _FileArtifactQualityEvidence(errors=tuple(errors), issues=tuple(issues))
