"""Internal implementation module for quality_gate package (lossless split)."""

# Cross-module free names are injected by package __init__
# (_wire_cross_module_namespace). Static F821 is expected and lossless.
# Imports are intentionally complete for lossless behavior; do not strip.
# ruff: noqa: F401, F821

from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import os
import re
import shlex
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, cast

from polaris.cells.director.runtime.public.contracts import DirectorInterfaceDiscrepancyReceiptV1
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.kernelone.quality import (
    artifact_quality_issue_raw,
    artifact_quality_issues_for_errors,
    artifact_quality_issues_from_errors,
    build_scope_authority_decision,
    partition_paths_by_declared_scope,
    scope_authority_decision_summary,
)

from .. import execute_method as _em
from ..artifact_quality_diagnostics import (
    _UNRESOLVED_IMPORT_SYMBOL_ERROR_RE,
    _UNRESOLVED_RELATIVE_IMPORT_ERROR_RE,
    _build_unresolved_import_symbol_repair_block,
    _missing_unresolved_relative_import_target_files,
    _parse_missing_declared_target_files,
    _relative_import_repair_target_candidates,
)
from ..contract_verify import resolve_contract_step_verify
from ..helpers import has_successful_write_tool
from ..materialization_quality_boundary import run_materialization_quality_public_boundary
from ..materialization_quality_runtime_ports import has_materialization_quality_runtime_repair_coverage
from ..repair_profile_projection import project_repair_kernel_summary
from ..runtime_repair_tool_adapter import (
    defer_director_command_with_director_tools,
    run_runtime_repair_with_director_tools,
)
from ..task_scope_paths import (
    _dedupe_preserve_order,
    _extract_project_declared_target_path_candidates,
    _extract_task_path_candidates,
    _extract_task_target_path_candidates,
    _filter_diff_to_task_declared_paths,
    _normalize_declared_task_path,
    _path_candidate_exists_in_file_set,
    _task_has_declared_target_files,
    _task_text_blob,
    _workspace_path_exists_case_insensitive,
)

# Cross-module symbols (defined in sibling submodules). Bare annotations
# satisfy mypy; package __init__._wire_cross_module_namespace injects
# real values into this module's __dict__ at import time.
_FAILED_TEST_TITLE_RE: Any
_PYTHON_MODULE_NOT_FOUND_RE: Any
_PYTHON_RUNTIME_SMOKE_TARGET_PATTERNS: Any
_PYTHON_TRACEBACK_FILE_RE: Any
_PYTHON_UNITTEST_RESULT_LINE_RE: Any
_QUALITY_REPAIR_TARGET_BATCH_LIMIT: Any
_RUST_COMPILE_PATH_RE: Any
_SEMANTIC_QUALITY_EXPLICIT_PATH_RE: Any
_TAP_FAILED_TEST_RE: Any
_TEST_SUMMARY_FAIL_RE: Any
_is_generated_quality_repair_target: Any
_is_typescript_command_config_path: Any


def _python_runtime_smoke_repair_target_files(
    *,
    artifact_quality_errors: list[str],
    changed_files: list[str],
    workspace_full: str = "",
) -> list[str]:
    """Extract existing Python files that failed Polaris' own runtime smoke.

    This intentionally trusts only quality-gate error strings emitted by
    ``_apply_deterministic_python_runtime_smoke``. The target may come from a
    prior task in the same Director run, so accept it when it is either one of
    the files written in the current repair turn or an existing Python file
    inside the workspace. That keeps arbitrary traceback paths from seeding
    repair scope while still repairing cross-task runtime smoke failures.
    """

    changed_python_files = {
        rel for item in changed_files if (rel := _normalize_declared_task_path(str(item or ""))) and rel.endswith(".py")
    }
    workspace_root = Path(str(workspace_full or "")).resolve() if str(workspace_full or "").strip() else None

    targets: list[str] = []
    for item in artifact_quality_errors:
        text = str(item or "")
        if not _looks_like_python_runtime_smoke_quality_error(text):
            continue
        if workspace_root is not None and workspace_root.is_dir() and _looks_like_python_test_behavior_failure(text):
            symbol_owner_targets = _python_test_failure_symbol_owner_target_files(text, workspace_root)
            targets.extend(_embedded_rust_compile_repair_target_files(text, workspace_root))
            if _looks_like_python_missing_module_failure(text):
                targets.extend(
                    rel
                    for rel in _python_runtime_smoke_traceback_repair_target_files(text, workspace_root)
                    if not _is_test_like_python_path(rel)
                )
            regex_source_failure = _looks_like_python_regex_source_quality_failure(text)
            if regex_source_failure:
                targets.extend(_changed_source_repair_target_files(changed_files, workspace_root))
            cli_subcommand_failure = _looks_like_cli_subcommand_quality_failure(text)
            if cli_subcommand_failure:
                entrypoints = _workspace_cli_entrypoint_repair_target_files(workspace_root)
                targets.extend(entrypoints or _changed_source_repair_target_files(changed_files, workspace_root))
            if not regex_source_failure and not cli_subcommand_failure:
                targets.extend(_python_test_harness_changed_source_target_files(text, changed_files, workspace_root))
            failed_test_targets = _python_unittest_failure_test_target_files(text, workspace_root)
            symbol_owners_cover_failures = bool(symbol_owner_targets) and len(symbol_owner_targets) >= max(
                1, len(failed_test_targets)
            )
            if symbol_owners_cover_failures:
                targets.extend(symbol_owner_targets)
            else:
                for rel in failed_test_targets:
                    targets.extend(
                        _python_runtime_smoke_imported_source_target_files(
                            rel,
                            workspace_root,
                            include_missing_src_imports=True,
                        )
                    )
            targets.extend(_python_runtime_smoke_missing_module_source_targets(text, workspace_root))
            if not symbol_owners_cover_failures:
                targets.extend(failed_test_targets)
        for pattern in _PYTHON_RUNTIME_SMOKE_TARGET_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            rel = _normalize_declared_task_path(match.group("target"))
            workspace_target_exists = (
                workspace_root is not None
                and workspace_root.is_dir()
                and _workspace_path_exists_case_insensitive(workspace_root, rel)
            )
            if rel.endswith(".py") and (rel in changed_python_files or workspace_target_exists):
                if workspace_root is not None and workspace_root.is_dir():
                    if _is_test_like_python_path(rel):
                        targets.extend(
                            item
                            for item in _python_runtime_smoke_traceback_repair_target_files(text, workspace_root)
                            if item != rel and not _is_test_like_python_path(item)
                        )
                        targets.extend(
                            _python_runtime_smoke_imported_source_target_files(
                                rel,
                                workspace_root,
                                include_missing_src_imports=True,
                            )
                        )
                        targets.append(rel)
                    elif _looks_like_python_missing_module_failure(text):
                        targets.append(rel)
                        targets.extend(_python_runtime_smoke_imported_source_target_files(rel, workspace_root))
                    elif _looks_like_python_module_coupling_failure(text):
                        targets.extend(_python_runtime_smoke_imported_source_target_files(rel, workspace_root))
                        targets.append(rel)
                    else:
                        targets.append(rel)
                else:
                    targets.append(rel)
            break
    return _dedupe_preserve_order(targets)


def _embedded_rust_compile_repair_target_files(text: str, workspace_root: Path) -> list[str]:
    if not _looks_like_embedded_rust_compile_failure(text):
        return []
    targets: list[str] = []
    for match in _RUST_COMPILE_PATH_RE.finditer(str(text or "")):
        rel = _workspace_relative_rust_repair_target(str(match.group("path") or ""), workspace_root)
        if rel:
            targets.append(rel)
    lowered = str(text or "").lower()
    if (
        "could not compile" in lowered
        or "previous errors" in lowered
        or "some errors have detailed explanations" in lowered
    ):
        targets.extend(_workspace_rust_source_repair_target_files(workspace_root))
    return _dedupe_preserve_order(targets)[:_QUALITY_REPAIR_TARGET_BATCH_LIMIT]


def _looks_like_embedded_rust_compile_failure(text: str) -> bool:
    lowered = str(text or "").lower()
    if not any(hint in lowered for hint in ("cargo check", "cargo build", "cargo test", "rustc", "could not compile")):
        return False
    return "error[" in lowered or ".rs:" in lowered or "could not compile" in lowered


def _workspace_relative_rust_repair_target(raw_path: str, workspace_root: Path) -> str:
    token = str(raw_path or "").strip().strip("'\"`>").replace("\\", "/")
    if not token:
        return ""
    candidate = Path(token)
    if candidate.is_absolute():
        try:
            rel = candidate.resolve().relative_to(workspace_root.resolve()).as_posix()
        except (OSError, RuntimeError, ValueError):
            return ""
    else:
        rel = _normalize_declared_task_path(token)
    if not rel.endswith(".rs"):
        return ""
    if not _workspace_path_exists_case_insensitive(workspace_root, rel):
        return ""
    return rel


def _workspace_rust_source_repair_target_files(workspace_root: Path) -> list[str]:
    src_dir = workspace_root / "src"
    if not src_dir.is_dir():
        return []
    targets: list[str] = []
    for path in sorted(src_dir.rglob("*.rs"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        try:
            targets.append(path.relative_to(workspace_root).as_posix())
        except ValueError:
            continue
    return _dedupe_preserve_order(targets)[:_QUALITY_REPAIR_TARGET_BATCH_LIMIT]


_GO_RUN_COMMAND_TARGET_RE = re.compile(r"(?:^|[\s(>])go\s+run\s+(?P<target>(?!-)[^\s'\"\n]+)")
_GO_COMPILE_PATH_RE = re.compile(r"(?P<path>(?:[A-Za-z]:)?[^\s:()'\"\n]+?\.go):\d+:\d+")
_GO_MISSING_MEMBER_TYPE_RE = re.compile(
    r"type\s+\*?(?:(?P<package_name>[A-Za-z_][A-Za-z0-9_]*)\.)?"
    r"(?P<type_name>[A-Za-z_][A-Za-z0-9_]*)\s+has\s+no\s+field\s+or\s+method",
    re.IGNORECASE,
)
_GO_IMPORT_SPEC_RE = re.compile(r"(?m)^\s*(?:(?P<alias>[A-Za-z_][A-Za-z0-9_]*|\.)\s+)?\"(?P<import_path>[^\"]+)\"")
_GO_TEST_FAILURE_TITLE_RE = re.compile(
    r"(?:^|[\n:])\s*---\s+FAIL:\s+(?P<title>[A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE | re.MULTILINE,
)


def _go_runtime_smoke_repair_target_files(
    *,
    artifact_quality_errors: list[str],
    changed_files: list[str],
    workspace_full: str = "",
) -> list[str]:
    """Extract Go entrypoint/source files that failed a workspace runtime smoke."""

    changed_go_files = {
        rel for item in changed_files if (rel := _normalize_declared_task_path(str(item or ""))) and rel.endswith(".go")
    }
    workspace_root = Path(str(workspace_full or "")).resolve() if str(workspace_full or "").strip() else None
    targets: list[str] = []
    for item in artifact_quality_errors:
        text = str(item or "")
        if not _looks_like_go_workspace_quality_error(text):
            continue
        if workspace_root is not None and workspace_root.is_dir():
            targets.extend(_go_compile_error_target_files(text, workspace_root))
            targets.extend(_go_runtime_smoke_command_target_files(text, workspace_root))
            targets.extend(
                _go_test_behavior_repair_target_files(
                    text=text,
                    changed_files=changed_go_files,
                    workspace_root=workspace_root,
                )
            )
            if not targets:
                targets.extend(_workspace_go_entrypoint_repair_target_files(workspace_root))
        if not targets:
            targets.extend(sorted(changed_go_files))
    return _dedupe_preserve_order(targets)


def _looks_like_go_workspace_quality_error(text: str) -> bool:
    # ``go test`` commonly emits only the package failure and compiler rows,
    # without echoing the command that produced them.  A concrete
    # ``*.go:line:column`` location is already a high-confidence Go diagnostic
    # and must route into the existing-target repair path; otherwise the
    # repair round receives no target body and can only spend its turn reading.
    if _GO_COMPILE_PATH_RE.search(str(text or "")):
        return True
    lowered = str(text or "").lower()
    if "workspace validation command failed" in lowered and ("go run" in lowered or "go test" in lowered):
        return True
    return ("go test" in lowered or "go compile" in lowered) and ".go:" in lowered


def _go_runtime_smoke_command_target_files(text: str, workspace_root: Path) -> list[str]:
    targets: list[str] = []
    for match in _GO_RUN_COMMAND_TARGET_RE.finditer(text):
        raw_target = str(match.group("target") or "").strip()
        if not raw_target:
            continue
        if raw_target in {".", "./"}:
            targets.extend(_workspace_go_entrypoint_repair_target_files(workspace_root))
            continue
        rel = _normalize_declared_task_path(raw_target)
        if not rel:
            continue
        candidate = workspace_root / rel
        if candidate.is_file() and candidate.suffix.lower() == ".go":
            targets.append(rel)
            continue
        if candidate.is_dir():
            targets.extend(_go_files_in_directory(candidate, workspace_root))
    return _dedupe_preserve_order(targets)


def _go_test_behavior_repair_target_files(
    *,
    text: str,
    changed_files: set[str],
    workspace_root: Path,
) -> list[str]:
    lowered = str(text or "").lower()
    if "go test" not in lowered or "--- fail:" not in lowered:
        return []

    production_files = [
        rel
        for rel in sorted(changed_files)
        if rel.endswith(".go")
        and not Path(rel).name.endswith("_test.go")
        and _workspace_path_exists_case_insensitive(workspace_root, rel)
    ]
    if not production_files:
        return []

    matched: list[str] = []
    for title_match in _GO_TEST_FAILURE_TITLE_RE.finditer(str(text or "")):
        tokens = _go_test_title_tokens(str(title_match.group("title") or ""))
        if tokens:
            matched.extend(_go_production_files_matching_tokens(production_files, tokens))

    return _dedupe_preserve_order([*matched, *production_files])


def _go_test_title_tokens(title: str) -> list[str]:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(title or ""))
    raw_tokens = re.split(r"[^A-Za-z0-9]+", normalized)
    stop_words = {"test", "tests", "and", "or", "the", "flow", "path", "case", "output", "entrypoint"}
    return _dedupe_preserve_order(
        [token.lower() for token in raw_tokens if len(token) >= 3 and token.lower() not in stop_words]
    )


def _go_production_files_matching_tokens(production_files: list[str], tokens: list[str]) -> list[str]:
    scored_matches: list[tuple[int, int, str]] = []
    primary_token = tokens[0] if tokens else ""
    for index, rel in enumerate(production_files):
        path_tokens = [
            token.lower()
            for token in re.split(r"[^A-Za-z0-9]+", f"{Path(rel).stem} {' '.join(Path(rel).parts)}")
            if len(token) >= 3
        ]
        score = 99
        if primary_token and primary_token in path_tokens:
            score = 0
        elif any(token in path_tokens for token in tokens):
            score = 1
        elif any(token in path_token or path_token in token for token in tokens for path_token in path_tokens):
            score = 2
        if score < 99:
            scored_matches.append((score, index, rel))
    if not scored_matches:
        return []
    best_score = min(score for score, _index, _rel in scored_matches)
    return [rel for score, _index, rel in sorted(scored_matches) if score == best_score]


def _go_compile_error_target_files(text: str, workspace_root: Path) -> list[str]:
    targets: list[str] = []
    for match in _GO_COMPILE_PATH_RE.finditer(text):
        raw_path = str(match.group("path") or "").strip()
        rel = _workspace_relative_go_repair_target(raw_path, workspace_root)
        if rel:
            targets.append(rel)
    targets.extend(
        _go_missing_member_type_definition_target_files(
            text=text,
            direct_targets=targets,
            workspace_root=workspace_root,
        )
    )
    return _dedupe_preserve_order(targets)


def _go_missing_member_type_definition_target_files(
    *,
    text: str,
    direct_targets: list[str],
    workspace_root: Path,
) -> list[str]:
    missing_member_refs = _go_missing_member_type_refs(text)
    if not missing_member_refs or not direct_targets:
        return []

    directories = _dedupe_preserve_order(
        [
            str(Path(rel).parent).replace("\\", "/")
            for rel in direct_targets
            if str(Path(rel).parent).replace("\\", "/") not in {"", "."}
        ]
    )
    for package_name, _type_name in missing_member_refs:
        if package_name:
            directories.extend(
                _go_package_qualifier_target_directories(
                    package_name=package_name,
                    direct_targets=direct_targets,
                    workspace_root=workspace_root,
                )
            )
    directories = _dedupe_preserve_order(directories)
    targets: list[str] = []
    type_names = _dedupe_preserve_order([type_name for _package_name, type_name in missing_member_refs])
    for directory in directories:
        package_dir = (workspace_root / directory).resolve()
        with contextlib.suppress(OSError, RuntimeError, ValueError):
            package_dir.relative_to(workspace_root.resolve())
            if not package_dir.is_dir():
                continue
            for path in sorted(package_dir.glob("*.go"), key=lambda item: item.as_posix()):
                if path.name.endswith("_test.go"):
                    continue
                try:
                    content = path.read_text(encoding="utf-8")
                    rel = path.relative_to(workspace_root).as_posix()
                except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
                    continue
                if any(re.search(rf"\btype\s+{re.escape(type_name)}\b", content) for type_name in type_names):
                    targets.append(rel)
    return _dedupe_preserve_order(targets)


def _go_missing_member_type_refs(text: str) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in _GO_MISSING_MEMBER_TYPE_RE.finditer(str(text or "")):
        package_name = str(match.group("package_name") or "").strip()
        type_name = str(match.group("type_name") or "").strip()
        ref = (package_name, type_name)
        if type_name and ref not in seen:
            seen.add(ref)
            refs.append(ref)
    return refs


def _go_package_qualifier_target_directories(
    *,
    package_name: str,
    direct_targets: list[str],
    workspace_root: Path,
) -> list[str]:
    qualifier = str(package_name or "").strip()
    if not qualifier:
        return []

    candidates: list[str] = []
    direct_target_contents: list[str] = []
    for rel in direct_targets:
        normalized = _normalize_declared_task_path(rel)
        if not normalized:
            continue
        with contextlib.suppress(OSError, RuntimeError, UnicodeDecodeError, ValueError):
            path = (workspace_root / normalized).resolve()
            path.relative_to(workspace_root.resolve())
            if path.is_file():
                direct_target_contents.append(path.read_text(encoding="utf-8"))

    for content in direct_target_contents:
        for match in _GO_IMPORT_SPEC_RE.finditer(content):
            alias = str(match.group("alias") or "").strip()
            import_path = str(match.group("import_path") or "").strip()
            if not import_path:
                continue
            default_package = import_path.rstrip("/").rsplit("/", 1)[-1]
            if qualifier not in {alias, default_package}:
                continue
            candidates.extend(_go_import_path_workspace_directories(import_path, workspace_root))

    direct_candidate = workspace_root / qualifier
    if direct_candidate.is_dir():
        candidates.append(qualifier)
    return _dedupe_preserve_order(candidates)


def _go_import_path_workspace_directories(import_path: str, workspace_root: Path) -> list[str]:
    parts = [part for part in str(import_path or "").strip().split("/") if part and part != "."]
    candidates: list[str] = []
    for index in range(len(parts)):
        rel = "/".join(parts[index:])
        if not rel:
            continue
        candidate = (workspace_root / rel).resolve()
        with contextlib.suppress(OSError, RuntimeError, ValueError):
            candidate.relative_to(workspace_root.resolve())
            if candidate.is_dir():
                candidates.append(rel)
    return _dedupe_preserve_order(candidates)


def _workspace_relative_go_repair_target(raw_path: str, workspace_root: Path) -> str:
    token = str(raw_path or "").strip()
    if not token:
        return ""
    candidate = Path(token)
    if candidate.is_absolute():
        try:
            rel = candidate.resolve().relative_to(workspace_root.resolve()).as_posix()
        except (OSError, RuntimeError, ValueError):
            return ""
    else:
        rel = _normalize_declared_task_path(token)
    if not rel.endswith(".go"):
        return ""
    if _workspace_path_exists_case_insensitive(workspace_root, rel):
        return rel
    return ""


def _workspace_go_entrypoint_repair_target_files(workspace_root: Path) -> list[str]:
    targets: list[str] = []
    root_main = workspace_root / "main.go"
    if root_main.is_file():
        targets.append("main.go")
    cmd_root = workspace_root / "cmd"
    if cmd_root.is_dir():
        for path in sorted(cmd_root.glob("*/main.go"), key=lambda item: item.as_posix()):
            try:
                targets.append(path.relative_to(workspace_root).as_posix())
            except ValueError:
                continue
    return _dedupe_preserve_order(targets)


def _go_files_in_directory(directory: Path, workspace_root: Path) -> list[str]:
    targets: list[str] = []
    for path in sorted(directory.glob("*.go"), key=lambda item: item.as_posix()):
        try:
            targets.append(path.relative_to(workspace_root).as_posix())
        except ValueError:
            continue
    return targets


_NODE_STACK_JS_PATH_RE = re.compile(r"(?P<path>(?:[A-Za-z]:)?[/\\][^\s()'\"\n]+?\.js):\d+:\d+")
_NODE_COMMAND_JS_TARGET_RE = re.compile(r"(?:^|[\s(>])node\s+(?P<target>(?!-)[^\s'\"\n]+?\.js)\b")


def _javascript_runtime_smoke_repair_target_files(
    *,
    artifact_quality_errors: list[str],
    changed_files: list[str],
    workspace_full: str = "",
) -> list[str]:
    """Extract workspace JavaScript entrypoints that failed Node/npm smoke."""

    changed_js_files = {
        rel for item in changed_files if (rel := _normalize_declared_task_path(str(item or ""))) and rel.endswith(".js")
    }
    workspace_root = Path(str(workspace_full or "")).resolve() if str(workspace_full or "").strip() else None
    targets: list[str] = []
    for item in artifact_quality_errors:
        text = str(item or "")
        if not _looks_like_javascript_runtime_smoke_quality_error(text):
            continue
        for raw_path in _javascript_runtime_smoke_path_candidates(text):
            rel = _workspace_relative_javascript_repair_target(
                raw_path,
                changed_js_files=changed_js_files,
                workspace_root=workspace_root,
            )
            if rel:
                targets.append(rel)
    authored_targets = [
        _typescript_source_repair_target_for_javascript_output(
            target,
            workspace_root=workspace_root,
        )
        or target
        for target in targets
    ]
    return _dedupe_preserve_order(authored_targets)


def _typescript_source_repair_target_for_javascript_output(
    javascript_target: str,
    *,
    workspace_root: Path | None,
) -> str:
    """Map a compiled JavaScript traceback target back to authored TypeScript.

    Runtime verifiers naturally report ``outDir/*.js`` stack frames.  Those
    generated files are not Director-owned source targets, so applying task
    scope before reversing ``tsconfig`` output topology incorrectly defers an
    otherwise local repair.  Resolve only an explicit ``outDir``/``rootDir``
    pair and only return an existing authored source file; ambiguous or invalid
    configs remain fail-closed on the original JavaScript target.
    """

    if workspace_root is None or not workspace_root.is_dir():
        return ""
    tsconfig_path = workspace_root / "tsconfig.json"
    if not tsconfig_path.is_file():
        return ""
    try:
        payload = json.loads(tsconfig_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    compiler_options = payload.get("compilerOptions") if isinstance(payload, Mapping) else None
    if not isinstance(compiler_options, Mapping):
        return ""
    out_dir = _normalize_declared_task_path(str(compiler_options.get("outDir") or ""))
    root_dir = _normalize_declared_task_path(str(compiler_options.get("rootDir") or ""))
    target = _normalize_declared_task_path(javascript_target)
    if not out_dir or not root_dir or not target.endswith(".js"):
        return ""
    output_prefix = f"{out_dir.rstrip('/')}/"
    if not target.startswith(output_prefix):
        return ""
    relative_stem = target[len(output_prefix) : -len(".js")]
    if not relative_stem or relative_stem.startswith("../") or "/../" in relative_stem:
        return ""
    source_targets: list[str] = []
    for suffix in (".ts", ".tsx", ".mts", ".cts"):
        source_target = _normalize_declared_task_path(f"{root_dir.rstrip('/')}/{relative_stem}{suffix}")
        if source_target and _workspace_path_exists_case_insensitive(workspace_root, source_target):
            source_targets.append(source_target)
    return source_targets[0] if len(source_targets) == 1 else ""


def _looks_like_javascript_runtime_smoke_quality_error(text: str) -> bool:
    token = str(text or "").lower()
    if "workspace validation command failed" not in token:
        return False
    has_node_command = "npm run start" in token or "npm start" in token or re.search(r"\bnode\s+\S+\.js\b", token)
    if not has_node_command:
        return False
    return "node.js v" in token or "typeerror:" in token or "referenceerror:" in token or "syntaxerror:" in token


def _javascript_runtime_smoke_path_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for pattern in (_NODE_COMMAND_JS_TARGET_RE, _NODE_STACK_JS_PATH_RE):
        candidates.extend(
            str(match.group("target" if "target" in match.groupdict() else "path") or "")
            for match in pattern.finditer(text)
        )
    return _dedupe_preserve_order(candidates)


def _workspace_relative_javascript_repair_target(
    raw_path: str,
    *,
    changed_js_files: set[str],
    workspace_root: Path | None,
) -> str:
    token = str(raw_path or "").strip().strip("'\"`")
    if not token:
        return ""
    rel = ""
    candidate = Path(token)
    if candidate.is_absolute():
        if workspace_root is None or not workspace_root.is_dir():
            return ""
        try:
            rel = str(candidate.resolve().relative_to(workspace_root)).replace("\\", "/")
        except (OSError, ValueError):
            return ""
    else:
        rel = _normalize_declared_task_path(token)
    if not rel.endswith(".js") or rel.startswith("../") or "/../" in rel:
        return ""
    workspace_target_exists = (
        workspace_root is not None
        and workspace_root.is_dir()
        and _workspace_path_exists_case_insensitive(workspace_root, rel)
    )
    if rel in changed_js_files or workspace_target_exists:
        return rel
    return ""


def _looks_like_python_runtime_smoke_quality_error(text: str) -> bool:
    """Return true for Polaris-owned Python runtime/test gate failures."""

    token = str(text or "").lower()
    if "python runtime smoke" in token:
        return True
    python_runtime_anchor = bool(
        re.search(r"(?:^|[\s(>])(?:\S*/)?pytest(?:\s|$)", token)
        or re.search(r"(?:^|[\s(>])unittest(?:\s|$)", token)
        or "modulenotfounderror" in token
        or re.search(r"(?:^|[\s(>])(?:\S*/)?python(?:3(?:\.\d+)?)?\s", token)
        or ("traceback (most recent call last)" in token and _PYTHON_TRACEBACK_FILE_RE.search(str(text or "")))
    )
    if not python_runtime_anchor:
        return False
    return (
        "workspace validation command failed" in token
        or "python unittest failed" in token
        or "python tests failed" in token
    ) and _looks_like_python_test_behavior_failure(text)


def _is_test_like_python_path(rel_path: str) -> bool:
    normalized = str(rel_path or "").replace("\\", "/").lower()
    name = Path(normalized).name
    return normalized.endswith(".py") and (
        name.startswith("test_") or name.endswith("_test.py") or "/tests/" in normalized
    )


def _looks_like_python_regex_source_quality_failure(text: str) -> bool:
    token = str(text or "").lower()
    if "assertregex" not in token and "regex didn't match" not in token:
        return False
    return "not found" in token or "read_source(" in token or "src =" in token


def _looks_like_cli_subcommand_quality_failure(text: str) -> bool:
    token = str(text or "").lower()
    return any(
        hint in token
        for hint in (
            "unknown subcommand",
            "unrecognized subcommand",
            "invalid subcommand",
            "no such subcommand",
        )
    )


_SOURCE_REPAIR_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".css",
        ".go",
        ".html",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".rs",
        ".ts",
        ".tsx",
    }
)


_CLI_ENTRYPOINT_REPAIR_CANDIDATES: tuple[str, ...] = (
    "src/main.rs",
    "main.rs",
    "src/main.py",
    "main.py",
    "src/cli.py",
    "cli.py",
    "src/index.js",
    "index.js",
    "src/index.ts",
    "index.ts",
    "src/main.ts",
    "main.ts",
    "cmd/main.go",
    "main.go",
)


def _workspace_cli_entrypoint_repair_target_files(workspace_root: Path) -> list[str]:
    try:
        root = workspace_root.resolve()
    except (OSError, RuntimeError, ValueError):
        return []
    targets: list[str] = []
    for rel in _CLI_ENTRYPOINT_REPAIR_CANDIDATES:
        if _workspace_path_exists_case_insensitive(root, rel):
            targets.append(rel)
    return _dedupe_preserve_order(targets)


def _changed_source_repair_target_files(changed_files: list[str], workspace_root: Path) -> list[str]:
    targets: list[str] = []
    try:
        root = workspace_root.resolve()
    except (OSError, RuntimeError, ValueError):
        return []
    for item in changed_files:
        rel = _normalize_declared_task_path(str(item or ""))
        if not rel:
            continue
        if _is_test_like_python_path(rel):
            continue
        if Path(rel).suffix.lower() not in _SOURCE_REPAIR_EXTENSIONS:
            continue
        if _workspace_path_exists_case_insensitive(root, rel):
            targets.append(rel)
    return _dedupe_preserve_order(targets)


_PYTHON_TEST_HARNESS_PATH_RE = re.compile(
    r"(?:^|[\s'\"`(])(?P<path>(?:[A-Za-z]:)?[^\s'\"`()]*?(?:tests?/[^\s'\"`()]*test[^\s'\"`()]*\.py|test_[^\s'\"`()]*\.py))",
    re.IGNORECASE,
)


def _looks_like_python_test_harness_quality_failure(text: str) -> bool:
    token = str(text or "").lower()
    if any(
        hint in token
        for hint in (
            "python runtime smoke",
            "python -m unittest",
            "pytest",
            "unittest discover",
        )
    ):
        return True
    return bool(_PYTHON_TEST_HARNESS_PATH_RE.search(str(text or "")))


def _python_test_harness_changed_source_target_files(
    text: str,
    changed_files: list[str],
    workspace_root: Path,
) -> list[str]:
    if not _looks_like_python_test_behavior_failure(text):
        return []
    if not _looks_like_python_test_harness_quality_failure(text):
        return []
    targets = _changed_source_repair_target_files(changed_files, workspace_root)
    if not any(not target.endswith(".py") for target in targets):
        return []
    return targets


def _python_runtime_smoke_traceback_repair_target_files(text: str, workspace_root: Path) -> list[str]:
    targets: list[str] = []
    try:
        root = workspace_root.resolve()
    except (OSError, RuntimeError, ValueError):
        return []

    for match in _PYTHON_TRACEBACK_FILE_RE.finditer(str(text or "")):
        raw_path = str(match.group("path") or "").strip()
        if not raw_path:
            continue
        candidate = Path(raw_path)
        if candidate.is_absolute():
            try:
                rel = candidate.resolve().relative_to(root).as_posix()
            except (OSError, RuntimeError, ValueError):
                continue
        else:
            rel = _normalize_declared_task_path(raw_path)
        if not rel.endswith(".py"):
            continue
        if _workspace_path_exists_case_insensitive(root, rel):
            targets.append(rel)
    return _dedupe_preserve_order(targets)


def _python_runtime_smoke_imported_source_target_files(
    rel_path: str,
    workspace_root: Path,
    *,
    include_missing_src_imports: bool = False,
) -> list[str]:
    """Infer local source modules imported by a failing Python test script."""

    rel = _normalize_declared_task_path(rel_path)
    if not rel.endswith(".py"):
        return []
    try:
        root = workspace_root.resolve()
        source_path = (root / rel).resolve()
        source_path.relative_to(root)
        text = source_path.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, RuntimeError, SyntaxError, UnicodeDecodeError, ValueError):
        return []

    src_root_exists = include_missing_src_imports and _workspace_path_exists_case_insensitive(root, "src")
    candidates: list[str] = []
    for node in ast.walk(tree):
        module_names: list[str] = []
        if isinstance(node, ast.Import):
            module_names.extend(str(alias.name or "").strip() for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            module_names.append(str(node.module or "").strip())
        for module_name in module_names:
            if not module_name:
                continue
            module_parts = [part for part in module_name.split(".") if part]
            if not module_parts:
                continue
            allow_missing_src_import = bool(src_root_exists and module_parts[0] == "src" and len(module_parts) > 1)
            module_path = "/".join(module_parts)
            module_candidates = (f"{module_path}.py", f"{module_path}/__init__.py")
            existing_candidate = next(
                (
                    normalized
                    for candidate in module_candidates
                    if (normalized := _normalize_declared_task_path(candidate))
                    and _workspace_path_exists_case_insensitive(root, normalized)
                ),
                "",
            )
            if existing_candidate:
                if not _is_test_like_python_path(existing_candidate):
                    candidates.append(existing_candidate)
                continue
            for candidate in module_candidates:
                normalized = _normalize_declared_task_path(candidate)
                if _is_test_like_python_path(normalized):
                    continue
                if allow_missing_src_import and normalized.endswith(".py"):
                    candidates.append(normalized)
                    break
    return _dedupe_preserve_order(candidates)


_PYTHON_TEST_TRACEBACK_FRAME_RE = re.compile(
    r'File "(?P<path>[^"]+)", line (?P<line>\d+)',
    re.IGNORECASE,
)
_PYTHON_ATTRIBUTE_OWNER_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"type object ['\"](?P<symbol>[A-Za-z_][A-Za-z0-9_]*)['\"] has no attribute", re.IGNORECASE),
    re.compile(r"['\"](?P<symbol>[A-Za-z_][A-Za-z0-9_]*)['\"] object has no attribute", re.IGNORECASE),
    re.compile(r"(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)\.__init__\(\)", re.IGNORECASE),
)


def _python_test_failure_symbol_owner_target_files(text: str, workspace_root: Path) -> list[str]:
    """Resolve failing unittest symbols to their unique source-owner files.

    A traceback test path is observation evidence, not mutation authority.  The
    old fallback authorized every directly imported module and then preferred
    the most recently changed importer.  That could force a repair into an
    unrelated file (for example an enum failure in ``models/weather.py`` was
    sent exclusively to ``radio.py``).  Resolve names from the failing source
    line and exception type against actual Python definitions instead.  Only a
    unique owner is authoritative; ambiguous names remain fail-closed.
    """

    try:
        root = workspace_root.resolve()
    except (OSError, RuntimeError, ValueError):
        return []

    symbols: list[str] = []
    for pattern in _PYTHON_ATTRIBUTE_OWNER_RES:
        symbols.extend(str(match.group("symbol") or "") for match in pattern.finditer(str(text or "")))

    for match in _PYTHON_TEST_TRACEBACK_FRAME_RE.finditer(str(text or "")):
        raw_path = str(match.group("path") or "").strip()
        try:
            line_number = int(match.group("line"))
        except (TypeError, ValueError):
            continue
        try:
            candidate = Path(raw_path)
            source_path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
            rel = source_path.relative_to(root).as_posix()
        except (OSError, RuntimeError, ValueError):
            continue
        if not _is_test_like_python_path(rel):
            continue
        try:
            source_lines = source_path.read_text(encoding="utf-8").splitlines()
            source_line = source_lines[line_number - 1].strip()
            tree = ast.parse(source_line)
        except (IndexError, OSError, RuntimeError, SyntaxError, UnicodeDecodeError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    symbols.append(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    symbols.append(node.func.attr)
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                symbols.append(node.value.id)

    owner_index: dict[str, set[str]] = {}
    source_files_seen = 0
    for source_path in sorted(root.rglob("*.py")):
        if source_files_seen >= 512:
            break
        try:
            rel = source_path.resolve().relative_to(root).as_posix()
        except (OSError, RuntimeError, ValueError):
            continue
        rel_parts = set(Path(rel).parts)
        if _is_test_like_python_path(rel) or rel_parts.intersection(
            {".git", ".polaris", ".venv", "__pycache__", "build", "dist", "site-packages"}
        ):
            continue
        try:
            if source_path.stat().st_size > 512_000:
                continue
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
        except (OSError, RuntimeError, SyntaxError, UnicodeDecodeError, ValueError):
            continue
        source_files_seen += 1
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                owner_index.setdefault(node.name, set()).add(rel)
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        owner_index.setdefault(child.name, set()).add(rel)

    targets: list[str] = []
    for symbol in _dedupe_preserve_order(symbols):
        owners = owner_index.get(symbol, set())
        if len(owners) == 1:
            targets.append(next(iter(owners)))
    return _dedupe_preserve_order(targets)


def _python_runtime_smoke_missing_module_source_targets(text: str, workspace_root: Path) -> list[str]:
    """Infer missing import-root Python module targets from traceback text."""

    try:
        root = workspace_root.resolve()
    except (OSError, RuntimeError, ValueError):
        return []
    if not _workspace_path_exists_case_insensitive(root, "src"):
        return []

    targets: list[str] = []
    for match in _PYTHON_MODULE_NOT_FOUND_RE.finditer(str(text or "")):
        module_name = str(match.group("module") or "").strip()
        if not module_name or module_name.startswith(("pytest", "unittest")):
            continue
        module_parts = [part for part in module_name.split(".") if part]
        if not module_parts:
            continue
        if module_parts[0] == "src":
            if len(module_parts) == 1:
                continue
            module_parts = module_parts[1:]
        module_path = "/".join(module_parts)
        candidate_paths = (f"src/{module_path}.py", f"src/{module_path}/__init__.py")
        existing_candidate = next(
            (candidate for candidate in candidate_paths if _workspace_path_exists_case_insensitive(root, candidate)),
            "",
        )
        targets.append(existing_candidate or candidate_paths[0])
    return _dedupe_preserve_order(targets)


def _python_unittest_failure_test_target_files(text: str, workspace_root: Path) -> list[str]:
    """Map unittest result lines back to physical test files in the workspace."""

    try:
        root = workspace_root.resolve()
    except (OSError, RuntimeError, ValueError):
        return []

    targets: list[str] = []
    for match in _PYTHON_UNITTEST_RESULT_LINE_RE.finditer(str(text or "")):
        module_name = str(match.group("module") or "").strip()
        if not module_name:
            continue
        for candidate in _python_unittest_module_candidate_paths(module_name):
            if _workspace_path_exists_case_insensitive(root, candidate):
                targets.append(candidate)
                break
    return _dedupe_preserve_order(targets)


def _python_unittest_module_candidate_paths(module_name: str) -> list[str]:
    parts = [item for item in str(module_name or "").split(".") if item]
    candidates: list[str] = []
    for index, part in enumerate(parts):
        if not part.startswith("test_"):
            continue
        if index > 0 and parts[0] == "tests":
            candidates.append(f"{'/'.join(parts[: index + 1])}.py")
        candidates.append(f"tests/{part}.py")
        candidates.append(f"{part}.py")
        break
    return _dedupe_preserve_order(candidates)


_SEMANTIC_QUALITY_SINGLE_TARGET_HINTS: tuple[str, ...] = (
    "no project-domain signal found in changed files",
    "deterministic scaffold marker",
    "generic/placeholder content detected",
    "placeholder-only",
    "structural-only",
    "repeated trivial arithmetic placeholder tests",
    "generic payload/index store scaffold",
    "npm default failing test script",
    "npm package manifest has test runner script",
    "npm package manifest script",
    "npm package manifest contains",
    "npm package manifest declares",
    "typescript project typecheck failed",
    "unresolved import symbol",
    "error ts",
    "step verify target mismatch",
)

_SEMANTIC_QUALITY_REPAIR_SOURCE_SUFFIXES: frozenset[str] = frozenset(
    {
        ".css",
        ".c",
        ".cc",
        ".cpp",
        ".cxx",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".java",
        ".js",
        ".jsx",
        ".json",
        ".md",
        ".py",
        ".rs",
        ".ts",
        ".tsx",
    }
)

_EXPLICIT_ARTIFACT_QUALITY_TARGET_HINTS: tuple[str, ...] = (
    "html module script references typescript source",
    "static entrypoints must load javascript",
    "assertionerror",
    "failed tests",
    "step verify failed",
    "test failed",
    "not ok ",
    "# fail ",
    "vitest",
    "jest",
    "prettier",
    "syntaxerror",
    "syntax error",
    "parse error",
    "referenceerror",
    "is not defined in es module scope",
    "cannot use import statement outside a module",
    "unterminated string literal",
    "unexpected token",
    "unexpected end of input",
    "was never closed",
    "py_compile failed",
    "pytest",
    "unittest",
    "typeerror",
    "traceback (most recent call last)",
    "ruff failed",
    "format failed",
)


def _explicit_artifact_quality_repair_target_files(
    *,
    artifact_quality_errors: list[str],
    changed_files: list[str],
    workspace_full: str,
) -> list[str]:
    """Return explicit failing artifact paths from syntax/format quality errors."""

    joined_errors = "\n".join(str(item or "").lower() for item in artifact_quality_errors)
    if not any(hint in joined_errors for hint in _EXPLICIT_ARTIFACT_QUALITY_TARGET_HINTS):
        return []

    workspace_root = Path(str(workspace_full or "")).resolve() if str(workspace_full or "").strip() else None
    if workspace_root is None or not workspace_root.is_dir():
        return []

    changed_source_files = _dedupe_preserve_order(
        [
            rel
            for item in changed_files
            if (rel := _normalize_declared_task_path(str(item or "")))
            and Path(rel).suffix.lower() in _SEMANTIC_QUALITY_REPAIR_SOURCE_SUFFIXES
            and not _is_generated_quality_repair_target(rel, workspace_root)
        ]
    )
    changed_source_set = set(changed_source_files)
    changed_by_lower = {item.lower(): item for item in changed_source_files}
    candidates: list[str] = []
    priority_candidates: list[str] = []
    traceback_source_candidates: list[str] = []
    imported_source_candidates: list[str] = []
    for item in artifact_quality_errors:
        text = str(item or "")
        if not any(hint in text.lower() for hint in _EXPLICIT_ARTIFACT_QUALITY_TARGET_HINTS):
            continue
        failed_title_targets = _failed_test_title_target_files(text, workspace_root, changed_source_files)
        candidates.extend(failed_title_targets)
        priority_candidates.extend(
            rel
            for rel in failed_title_targets
            if not _is_test_like_python_path(rel) and not _is_test_like_javascript_path(rel)
        )
        if _looks_like_javascript_module_system_failure(text) and "package.json" in changed_source_set:
            candidates.append("package.json")
            priority_candidates.append("package.json")
        explicit_paths = [match.group("path") for match in _SEMANTIC_QUALITY_EXPLICIT_PATH_RE.finditer(text)]
        if _looks_like_python_test_behavior_failure(text):
            symbol_owner_targets = _python_test_failure_symbol_owner_target_files(text, workspace_root)
            failed_test_targets = _python_unittest_failure_test_target_files(text, workspace_root)
            symbol_owners_cover_failures = bool(symbol_owner_targets) and len(symbol_owner_targets) >= max(
                1, len(failed_test_targets)
            )
            if symbol_owners_cover_failures:
                candidates.extend(symbol_owner_targets)
                priority_candidates.extend(symbol_owner_targets)
                # Unique AST owners are stronger mutation authority than the
                # failing test path or its broad import surface.  Do not append
                # importers/tests after ownership has been resolved, otherwise
                # the single-batch prompt can authorize unrelated files and
                # waste the repair turn.
                continue
            for rel in failed_test_targets:
                imported_sources = _python_runtime_smoke_imported_source_target_files(
                    rel,
                    workspace_root,
                    include_missing_src_imports=True,
                )
                candidates.extend(imported_sources)
                imported_source_candidates.extend(imported_sources)
                candidates.append(rel)
            traceback_targets = _python_runtime_smoke_traceback_repair_target_files(text, workspace_root)
            if _looks_like_python_missing_module_failure(text):
                traceback_sources = [rel for rel in traceback_targets if not _is_test_like_python_path(rel)]
                candidates.extend(traceback_sources)
                traceback_source_candidates.extend(traceback_sources)
            missing_module_sources = _python_runtime_smoke_missing_module_source_targets(text, workspace_root)
            candidates.extend(missing_module_sources)
            if not _looks_like_python_missing_module_failure(text):
                imported_source_candidates.extend(missing_module_sources)
            harness_sources = _python_test_harness_changed_source_target_files(text, changed_files, workspace_root)
            candidates.extend(harness_sources)
            imported_source_candidates.extend(harness_sources)
            explicit_paths.extend(traceback_targets)
        if _looks_like_javascript_test_behavior_failure(text) and not explicit_paths and not failed_title_targets:
            for rel in changed_source_files:
                if not _is_test_like_javascript_path(rel):
                    continue
                imported_sources = _javascript_test_imported_source_target_files(rel, workspace_root)
                candidates.extend(imported_sources)
                imported_source_candidates.extend(imported_sources)
                candidates.append(rel)
        for raw_path in explicit_paths:
            rel = _map_quality_error_path_to_changed_file(raw_path, changed_by_lower)
            if not rel:
                continue
            if Path(rel).suffix.lower() not in _SEMANTIC_QUALITY_REPAIR_SOURCE_SUFFIXES:
                continue
            if changed_source_set and rel not in changed_source_set:
                continue
            if _workspace_path_exists_case_insensitive(workspace_root, rel):
                appended_rel = False
                if _is_test_like_javascript_path(rel) and _looks_like_javascript_test_behavior_failure(text):
                    imported_sources = _javascript_test_imported_source_target_files(rel, workspace_root)
                    candidates.extend(imported_sources)
                    imported_source_candidates.extend(imported_sources)
                if (_is_test_like_python_path(rel) and _looks_like_python_test_behavior_failure(text)) or (
                    rel.endswith(".py") and _looks_like_python_module_coupling_failure(text)
                ):
                    if rel.endswith(".py") and _looks_like_python_missing_module_failure(text):
                        candidates.append(rel)
                        appended_rel = True
                        if rel.lower() in {item.lower() for item in traceback_source_candidates}:
                            traceback_source_candidates.append(rel)
                    imported_sources = _python_runtime_smoke_imported_source_target_files(
                        rel,
                        workspace_root,
                        include_missing_src_imports=_is_test_like_python_path(rel),
                    )
                    candidates.extend(imported_sources)
                    imported_source_candidates.extend(imported_sources)
                if not appended_rel:
                    candidates.append(rel)
    deduped_candidates = [
        rel
        for rel in _dedupe_preserve_order(candidates)
        if not _is_generated_quality_repair_target(rel, workspace_root)
    ]
    if imported_source_candidates:
        non_config_candidates = [rel for rel in deduped_candidates if not _is_typescript_command_config_path(rel)]
        if non_config_candidates:
            deduped_candidates = non_config_candidates
    if not changed_source_files:
        return deduped_candidates
    changed_order = {item.lower(): index for index, item in enumerate(changed_source_files)}
    original_order = {item.lower(): index for index, item in enumerate(deduped_candidates)}
    imported_source_order = {
        item.lower(): index for index, item in enumerate(_dedupe_preserve_order(imported_source_candidates))
    }
    traceback_source_order = {
        item.lower(): index for index, item in enumerate(_dedupe_preserve_order(traceback_source_candidates))
    }
    priority_order = {item.lower(): index for index, item in enumerate(_dedupe_preserve_order(priority_candidates))}
    facade_reexport_penalty = {
        item.lower(): 1 if _javascript_facade_related_source_target_files(item, workspace_root) else 0
        for item in deduped_candidates
    }
    return sorted(
        deduped_candidates,
        key=lambda item: (
            (
                0
                if item.lower() in priority_order
                else 1
                if item.lower() in traceback_source_order
                else 2
                if item.lower() in imported_source_order
                else 3
            ),
            facade_reexport_penalty.get(item.lower(), 0),
            priority_order.get(item.lower(), len(priority_order)),
            traceback_source_order.get(item.lower(), len(traceback_source_order)),
            imported_source_order.get(item.lower(), len(imported_source_order)),
            changed_order.get(item.lower(), len(changed_order)),
            original_order.get(item.lower(), len(original_order)),
        ),
    )


def _failed_test_title_target_files(text: str, workspace_root: Path, changed_source_files: list[str]) -> list[str]:
    """Prefer artifacts named by the failed test title over stack frames."""

    changed_by_lower = {item.lower(): item for item in changed_source_files}
    targets: list[str] = []
    for match in _FAILED_TEST_TITLE_RE.finditer(str(text or "")):
        title = str(match.group("title") or "")
        for path_match in _SEMANTIC_QUALITY_EXPLICIT_PATH_RE.finditer(title):
            rel = _map_quality_error_path_to_changed_file(path_match.group("path"), changed_by_lower)
            if not rel:
                continue
            if changed_by_lower and rel.lower() not in changed_by_lower:
                continue
            if Path(rel).suffix.lower() not in _SEMANTIC_QUALITY_REPAIR_SOURCE_SUFFIXES:
                continue
            if _workspace_path_exists_case_insensitive(workspace_root, rel):
                targets.append(rel)
    return _dedupe_preserve_order(targets)


def _map_quality_error_path_to_changed_file(raw_path: str, changed_by_lower: dict[str, str]) -> str:
    """Map relative or workspace-absolute quality-log paths to changed files."""

    rel = _normalize_declared_task_path(raw_path)
    if rel and (not changed_by_lower or rel.lower() in changed_by_lower):
        return changed_by_lower.get(rel.lower(), rel)

    normalized = str(raw_path or "").strip().strip("'\"`").replace("\\", "/").strip("/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized_lower = normalized.lower()
    for candidate_lower, candidate in changed_by_lower.items():
        if normalized_lower == candidate_lower or normalized_lower.endswith(f"/{candidate_lower}"):
            return candidate
    for compiled_candidate in _compiled_javascript_stack_source_candidates(normalized_lower):
        if compiled_candidate in changed_by_lower:
            return changed_by_lower[compiled_candidate]
    return rel


def _compiled_javascript_stack_source_candidates(normalized_lower_path: str) -> tuple[str, ...]:
    normalized = str(normalized_lower_path or "").strip("/")
    if ".js" not in normalized:
        return ()
    dist_marker = "/dist/"
    if normalized.startswith("dist/"):
        relative = normalized.removeprefix("dist/")
    elif dist_marker in normalized:
        relative = normalized.rsplit(dist_marker, 1)[1]
    else:
        return ()
    stem = re.sub(r"\.(?:mjs|cjs|js)(?:\.map)?$", "", relative)
    if not stem or stem == relative:
        return ()
    candidates = []
    for suffix in (".ts", ".tsx", ".js", ".jsx"):
        candidates.append(f"src/{stem}{suffix}")
        candidates.append(f"{stem}{suffix}")
    return tuple(candidates)


def _artifact_quality_failed_test_count(artifact_quality_errors: list[str]) -> int:
    """Return the physical test failure count reported by common CLI formats."""

    text = "\n".join(str(item or "") for item in artifact_quality_errors)
    failed_count = len(_TAP_FAILED_TEST_RE.findall(text))
    failed_count = max(failed_count, len(_PYTHON_UNITTEST_RESULT_LINE_RE.findall(text)))
    for match in _TEST_SUMMARY_FAIL_RE.finditer(text):
        try:
            failed_count = max(failed_count, int(match.group("count")))
        except (TypeError, ValueError):
            continue
    return failed_count


def _looks_like_javascript_test_behavior_failure(text: str) -> bool:
    token = str(text or "").lower()
    return any(
        hint in token
        for hint in (
            "assertionerror",
            "err_assertion",
            "failed tests",
            "step verify failed",
            "test failed",
            "not ok ",
            "# fail ",
            "vitest",
            "jest",
        )
    )


def _looks_like_javascript_module_system_failure(text: str) -> bool:
    token = str(text or "").lower()
    return (
        "referenceerror: require is not defined" in token
        or "module is not defined in es module scope" in token
        or "is not defined in es module scope" in token
        or "cannot use import statement outside a module" in token
    )


def _looks_like_python_test_behavior_failure(text: str) -> bool:
    token = str(text or "").lower()
    return any(
        hint in token
        for hint in (
            "python -m unittest",
            "unittest",
            "pytest",
            "traceback (most recent call last)",
            "assertionerror",
            "typeerror",
            "failed tests",
            "test failed",
        )
    )


def _looks_like_python_module_coupling_failure(text: str) -> bool:
    token = str(text or "").lower()
    return any(
        hint in token
        for hint in (
            "importerror",
            "cannot import name",
            "modulenotfounderror",
            "attributeerror",
            "typeerror",
            "unexpected keyword argument",
            "has no attribute",
        )
    )


def _looks_like_python_missing_module_failure(text: str) -> bool:
    token = str(text or "").lower()
    return "modulenotfounderror" in token or "no module named" in token


def _has_non_test_python_traceback_source(text: str) -> bool:
    for match in _PYTHON_TRACEBACK_FILE_RE.finditer(str(text or "")):
        raw_path = str(match.group("path") or "").strip()
        if raw_path.endswith(".py") and not _is_test_like_python_path(raw_path):
            return True
    return False


def _is_test_like_javascript_path(rel_path: str) -> bool:
    normalized = str(rel_path or "").replace("\\", "/").lower()
    name = Path(normalized).name
    return normalized.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")) and (
        ".test." in name or ".spec." in name or "/tests/" in normalized
    )


def _javascript_test_imported_source_target_files(rel_path: str, workspace_root: Path) -> list[str]:
    rel = _normalize_declared_task_path(rel_path)
    if not _is_test_like_javascript_path(rel):
        return []
    try:
        root = workspace_root.resolve()
        test_path = (root / rel).resolve()
        test_path.relative_to(root)
        text = test_path.read_text(encoding="utf-8")
    except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
        return []

    candidates: list[str] = []
    for import_ref in _javascript_relative_import_refs(text):
        target = _resolve_javascript_relative_import_target(test_path.parent, import_ref, root)
        if target is None:
            continue
        target_rel = target.relative_to(root).as_posix()
        if _is_test_like_javascript_path(target_rel):
            continue
        facade_targets = _javascript_facade_related_source_target_files(target_rel, root)
        if facade_targets:
            candidates.extend(facade_targets)
            candidates.append(target_rel)
        else:
            candidates.append(target_rel)
    return _dedupe_preserve_order(candidates)


def _javascript_facade_related_source_target_files(rel_path: str, workspace_root: Path) -> list[str]:
    rel = _normalize_declared_task_path(rel_path)
    if not rel or _is_test_like_javascript_path(rel):
        return []
    if Path(rel).suffix.lower() not in _SEMANTIC_QUALITY_REPAIR_SOURCE_SUFFIXES:
        return []
    try:
        root = workspace_root.resolve()
        source_path = (root / rel).resolve()
        source_path.relative_to(root)
        text = source_path.read_text(encoding="utf-8")
    except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
        return []

    candidates: list[str] = []
    for import_ref in _javascript_relative_reexport_refs(text):
        target = _resolve_javascript_relative_import_target(source_path.parent, import_ref, root)
        if target is None:
            continue
        target_rel = target.relative_to(root).as_posix()
        if _is_test_like_javascript_path(target_rel):
            continue
        if Path(target_rel).suffix.lower() in _SEMANTIC_QUALITY_REPAIR_SOURCE_SUFFIXES:
            candidates.append(target_rel)
    return _dedupe_preserve_order(candidates)


def _javascript_relative_reexport_refs(text: str) -> list[str]:
    refs: list[str] = []
    patterns = (re.compile(r"\bexport\s+(?:type\s+)?(?:\{[^}]*\}|\*)\s+from\s+['\"](?P<ref>\.{1,2}/[^'\"]+)['\"]"),)
    for pattern in patterns:
        for match in pattern.finditer(str(text or "")):
            refs.append(str(match.group("ref") or "").strip())
    return _dedupe_preserve_order(refs)


def _javascript_relative_import_refs(text: str) -> list[str]:
    refs: list[str] = []
    patterns = (
        re.compile(r"\bfrom\s+['\"](?P<ref>\.{1,2}/[^'\"]+)['\"]"),
        re.compile(r"\brequire\(\s*['\"](?P<ref>\.{1,2}/[^'\"]+)['\"]\s*\)"),
    )
    for pattern in patterns:
        for match in pattern.finditer(str(text or "")):
            refs.append(str(match.group("ref") or "").strip())
    return _dedupe_preserve_order([ref for ref in refs if ref])


def _resolve_javascript_relative_import_target(
    importer_dir: Path, import_ref: str, workspace_root: Path
) -> Path | None:
    raw = str(import_ref or "").strip()
    if not raw.startswith(("./", "../")):
        return None
    try:
        base = (importer_dir / raw).resolve()
        base.relative_to(workspace_root)
    except (OSError, RuntimeError, ValueError):
        return None
    candidates: list[Path] = []
    if base.suffix:
        candidates.append(base)
    else:
        candidates.extend(base.with_suffix(suffix) for suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))
        candidates.extend(base / f"index{suffix}" for suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(workspace_root)
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved.is_file():
            return resolved
    return None


__all__ = [
    "_CLI_ENTRYPOINT_REPAIR_CANDIDATES",
    "_EXPLICIT_ARTIFACT_QUALITY_TARGET_HINTS",
    "_GO_COMPILE_PATH_RE",
    "_GO_IMPORT_SPEC_RE",
    "_GO_MISSING_MEMBER_TYPE_RE",
    "_GO_RUN_COMMAND_TARGET_RE",
    "_GO_TEST_FAILURE_TITLE_RE",
    "_NODE_COMMAND_JS_TARGET_RE",
    "_NODE_STACK_JS_PATH_RE",
    "_PYTHON_TEST_HARNESS_PATH_RE",
    "_SEMANTIC_QUALITY_REPAIR_SOURCE_SUFFIXES",
    "_SEMANTIC_QUALITY_SINGLE_TARGET_HINTS",
    "_SOURCE_REPAIR_EXTENSIONS",
    "_artifact_quality_failed_test_count",
    "_changed_source_repair_target_files",
    "_compiled_javascript_stack_source_candidates",
    "_embedded_rust_compile_repair_target_files",
    "_explicit_artifact_quality_repair_target_files",
    "_failed_test_title_target_files",
    "_go_compile_error_target_files",
    "_go_files_in_directory",
    "_go_import_path_workspace_directories",
    "_go_missing_member_type_definition_target_files",
    "_go_missing_member_type_refs",
    "_go_package_qualifier_target_directories",
    "_go_production_files_matching_tokens",
    "_go_runtime_smoke_command_target_files",
    "_go_runtime_smoke_repair_target_files",
    "_go_test_behavior_repair_target_files",
    "_go_test_title_tokens",
    "_has_non_test_python_traceback_source",
    "_is_test_like_javascript_path",
    "_is_test_like_python_path",
    "_javascript_facade_related_source_target_files",
    "_javascript_relative_import_refs",
    "_javascript_relative_reexport_refs",
    "_javascript_runtime_smoke_path_candidates",
    "_javascript_runtime_smoke_repair_target_files",
    "_javascript_test_imported_source_target_files",
    "_looks_like_cli_subcommand_quality_failure",
    "_looks_like_embedded_rust_compile_failure",
    "_looks_like_go_workspace_quality_error",
    "_looks_like_javascript_module_system_failure",
    "_looks_like_javascript_runtime_smoke_quality_error",
    "_looks_like_javascript_test_behavior_failure",
    "_looks_like_python_missing_module_failure",
    "_looks_like_python_module_coupling_failure",
    "_looks_like_python_regex_source_quality_failure",
    "_looks_like_python_runtime_smoke_quality_error",
    "_looks_like_python_test_behavior_failure",
    "_looks_like_python_test_harness_quality_failure",
    "_map_quality_error_path_to_changed_file",
    "_python_runtime_smoke_imported_source_target_files",
    "_python_runtime_smoke_missing_module_source_targets",
    "_python_runtime_smoke_repair_target_files",
    "_python_runtime_smoke_traceback_repair_target_files",
    "_python_test_harness_changed_source_target_files",
    "_python_unittest_failure_test_target_files",
    "_python_unittest_module_candidate_paths",
    "_resolve_javascript_relative_import_target",
    "_typescript_source_repair_target_for_javascript_output",
    "_workspace_cli_entrypoint_repair_target_files",
    "_workspace_go_entrypoint_repair_target_files",
    "_workspace_relative_go_repair_target",
    "_workspace_relative_javascript_repair_target",
    "_workspace_relative_rust_repair_target",
    "_workspace_rust_source_repair_target_files",
]
