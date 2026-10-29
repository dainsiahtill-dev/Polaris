"""Workspace-level artifact quality scan orchestration."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Mapping, cast

from polaris.kernelone.quality.artifact_quality._constants import (
    _ARTIFACT_QUALITY_SKIP_DIRS,
    _ARTIFACT_QUALITY_SOURCE_EXTS,
    _DETERMINISTIC_SCAFFOLD_MARKERS,
    _GENERIC_STORE_MAP_RE,
    _GENERIC_STORE_RECORD_RE,
    _NUMERIC_HELPER_FILLER_RE,
    _PATCH_RESIDUE_RE,
    _SOURCE_NARRATION_LEAK_RE,
    _TOOL_RECEIPT_CONTAMINATION_TOKENS,
    _TRIVIAL_ARITHMETIC_EXPECT_RE,
)
from polaris.kernelone.quality.artifact_quality._helpers import (
    _is_test_like_artifact_path,
    _iter_target_files,
    _looks_like_code_symbol,
    _merge_quality_names,
    _quality_string_list,
)
from polaris.kernelone.quality.artifact_quality._issues import (
    _artifact_quality_evidence,
    _artifact_quality_scan_failure_issue,
    _file_artifact_quality_issue,
)
from polaris.kernelone.quality.artifact_quality._models import (
    ArtifactQualityDeferredCheck,
    ArtifactQualityEvidence,
    ArtifactQualityIssue,
    _FileArtifactQualityEvidence,
)
from polaris.kernelone.quality.artifact_quality._scan_go import (
    _scan_go_project_compile_evidence,
    _scan_go_project_test_evidence,
)
from polaris.kernelone.quality.artifact_quality._scan_javascript import (
    _scan_javascript_named_export_evidence,
)
from polaris.kernelone.quality.artifact_quality._scan_package import (
    _scan_cargo_manifest_missing_binary_evidence,
    _scan_package_manifest_evidence,
)
from polaris.kernelone.quality.artifact_quality._scan_python import (
    _scan_python_acceptance_npm_node_alias_evidence,
    _scan_python_acceptance_term_pair_evidence,
)
from polaris.kernelone.quality.artifact_quality._scan_typescript import (
    _scan_html_typescript_module_script_evidence,
    _scan_typescript_import_evidence,
    _scan_typescript_project_typecheck_evidence,
    _scan_typescript_syntax_red_flag_evidence,
    _scan_typescript_tsconfig_evidence,
)
from polaris.kernelone.quality.artifact_quality._syntax import (
    _check_source_file_syntax_static,
    check_source_file_syntax,
)
from polaris.kernelone.quality.cross_artifact_interfaces import (
    CrossArtifactConsistencyIssue,
    CrossArtifactInterfaceContract,
    SymbolIndexSnapshot,
    build_contract_amendment_request,
    build_symbol_index_snapshot,
    plan_cross_artifact_repairs,
    scan_cross_artifact_consistency,
)
from polaris.kernelone.quality.interface_ledger import (
    DeclaredInterfaceLedgerSnapshot,
    WorkspaceReadSnapshot,
    read_all_declared_interfaces,
    read_declared_interface_ledger_snapshot,
    read_declared_interface_ledger_status,
    read_declared_interfaces,
    validate_declared_interface_issues_against_snapshot,
)


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

    return list(scan_workspace_artifact_quality_evidence(workspace_full, relative_paths=relative_paths).errors)


def scan_workspace_artifact_quality_evidence(
    workspace_full: str,
    *,
    relative_paths: Iterable[str] | None = None,
    interface_contract: CrossArtifactInterfaceContract | Mapping[str, Any] | None = None,
    task_id: str = "",
    scan_mode: str = "legacy",
) -> ArtifactQualityEvidence:
    """Scan artifacts; ``pure_static`` returns required process checks as intents.

    Empty errors in static mode do not establish compiler or verifier success.
    Default legacy mode retains the existing process/bytecode behavior.
    """

    if scan_mode not in {"legacy", "pure_static"}:
        raise ValueError(f"unsupported scan_mode: {scan_mode!r}")
    pure_static = scan_mode == "pure_static"

    try:
        root_full = Path(workspace_full).resolve()
    except (OSError, RuntimeError, ValueError):
        message = "Artifact quality scan failed: workspace path cannot be resolved"
        return _artifact_quality_evidence(
            scan_mode=scan_mode,
            scan_complete=False,
            incomplete_reasons=("workspace_path_unresolved",),
            errors=(message,),
            issues=(
                ArtifactQualityIssue(
                    code="workspace_path_unresolved",
                    message=message,
                    source="artifact_quality_scanner",
                    metadata={
                        "raw": message,
                        "diagnostic_kind": "workspace_path_unresolved",
                    },
                ),
            ),
        )
    if not root_full.exists() or not root_full.is_dir():
        message = "Artifact quality scan failed: workspace path does not exist"
        return _artifact_quality_evidence(
            scan_mode=scan_mode,
            scan_complete=False,
            incomplete_reasons=("workspace_path_missing",),
            errors=(message,),
            issues=(
                ArtifactQualityIssue(
                    code="workspace_path_missing",
                    message=message,
                    source="artifact_quality_scanner",
                    metadata={
                        "raw": message,
                        "diagnostic_kind": "workspace_path_missing",
                    },
                ),
            ),
        )

    errors: list[str] = []
    typed_issues: list[Any] = []
    scanned_relative_paths: list[str] = []
    cross_artifact_issues: tuple[CrossArtifactConsistencyIssue, ...] = ()
    deferred_checks: list[ArtifactQualityDeferredCheck] = []
    incomplete_reasons: list[str] = []
    reader = WorkspaceReadSnapshot(root_full) if pure_static else None
    ledger_snapshots: dict[str, DeclaredInterfaceLedgerSnapshot] = {}
    source_snapshot: SymbolIndexSnapshot | None = None
    aggregate_paths: tuple[str, ...] | None = None
    static_ledger_options: dict[str, Any] = (
        {"pure_static": True, "incomplete_reasons": incomplete_reasons, "ledger_snapshots": ledger_snapshots}
        if pure_static
        else {}
    )

    def record_source_incomplete(reasons: Iterable[str]) -> None:
        # A cached refusal already has one public per-file coverage identity
        # and its full detail in errors/issues. Keep new sibling/package faults.
        for reason in reasons:
            if any(
                reason.startswith(
                    (
                        f"symbol_source_unknown:{path}:",
                        f"javascript_specialist_source_unknown:{path}:",
                        f"python_specialist_source_unknown:{path}:",
                    )
                )
                and f"file_unreadable:{path}" in incomplete_reasons
                for path in scanned_relative_paths
            ):
                continue
            incomplete_reasons.append(reason)

    def coverage(*, stopped: str = "") -> dict[str, Any]:
        project_checks = (
            _static_project_checks(
                root_full, scanned_relative_paths, reader=reader, incomplete_reasons=incomplete_reasons
            )
            if pure_static
            else ()
        )
        reasons = [*incomplete_reasons, *([stopped] if stopped else [])]
        if reader is not None:
            reasons.extend(reader.drift_reasons())
        return {
            "scan_mode": scan_mode,
            "deferred_checks": (
                *deferred_checks,
                *project_checks,
            ),
            "scan_complete": not reasons,
            "incomplete_reasons": reasons,
        }

    try:
        # Materialize single-use iterables once; ledger and scanner share scope.
        if pure_static and relative_paths is not None:
            relative_paths = tuple(relative_paths)
        aggregates_safe = True
        if pure_static:
            for cache_root in _interface_ledger_roots(root_full, pure_static=True):
                ledger_snapshots[cache_root] = read_declared_interface_ledger_snapshot(
                    root_full.as_posix(), cache_root, reader=reader
                )
            # Aggregate helpers inspect sibling exports beyond explicit entries.
            # Refuse these reads if traversal detects unvisited ancestry.
            aggregate_unknown: list[str] = []
            aggregate_paths = tuple(
                path.relative_to(root_full).as_posix()
                for path in _iter_static_files(root_full, None, aggregate_unknown)
            )
            if aggregate_unknown:
                aggregates_safe = False
                incomplete_reasons.extend(aggregate_unknown)
        interface_contract = interface_contract or _declared_interface_contract(
            root_full=root_full,
            relative_paths=relative_paths,
            task_id=task_id,
            **static_ledger_options,
        )
        paths = (
            _iter_static_files(root_full, relative_paths, incomplete_reasons)
            if pure_static
            else _iter_target_files(root_full, relative_paths)
            if relative_paths is not None
            else sys.modules[__package__]._iter_workspace_source_files(root_full)
        )
        for full_path in paths:
            if len(errors) >= 50:
                return _artifact_quality_evidence(
                    **coverage(stopped="error_limit"),
                    errors=errors,
                    issues=typed_issues,
                    scanned_relative_paths=tuple(scanned_relative_paths),
                )
            relative_path = full_path.relative_to(root_full).as_posix()
            scanned_relative_paths.append(relative_path)
            if pure_static:
                # Discover parser coverage before any per-file helper can fail.
                deferred_checks.extend(_static_file_checks(relative_path))
            file_evidence = (
                _scan_file_evidence(root_full, full_path, relative_path, pure_static=True, reader=reader)
                if pure_static
                else _scan_file_evidence(root_full, full_path, relative_path)
            )
            errors.extend(file_evidence.errors)
            typed_issues.extend(file_evidence.issues)
            deferred_checks.extend(file_evidence.deferred_checks)
            incomplete_reasons.extend(file_evidence.incomplete_reasons)
        if len(errors) < 50 and not pure_static:
            typecheck_evidence = _scan_typescript_project_typecheck_evidence(root_full, scanned_relative_paths)
            errors.extend(typecheck_evidence.errors)
            typed_issues.extend(typecheck_evidence.issues)
        if len(errors) < 50 and not pure_static:
            go_compile_evidence = _scan_go_project_compile_evidence(root_full, scanned_relative_paths)
            errors.extend(go_compile_evidence.errors)
            typed_issues.extend(go_compile_evidence.issues)
            if len(errors) < 50 and not go_compile_evidence.errors:
                go_test_evidence = _scan_go_project_test_evidence(root_full, scanned_relative_paths)
                errors.extend(go_test_evidence.errors)
                typed_issues.extend(go_test_evidence.issues)
        if pure_static and aggregates_safe:
            source_snapshot = build_symbol_index_snapshot(
                root_full,
                relative_paths=scanned_relative_paths if relative_paths is not None else None,
                reader=reader,
                source_paths=aggregate_paths,
            )
            # A per-file refusal already has a stable public coverage reason.
            # Keep that identity while adding previously unvisited owner faults.
            record_source_incomplete(source_snapshot.incomplete_reasons)
            static_ledger_options["source_snapshot"] = source_snapshot
        # Specialists share the original reader/inventory. Cached diagnostics
        # remain valid evidence even when final metadata reports source drift.
        if len(errors) < 50 and (pure_static or aggregates_safe):
            js_export_evidence = _scan_javascript_named_export_evidence(
                root_full,
                scanned_relative_paths,
                reader=reader if pure_static else None,
                source_paths=aggregate_paths if pure_static else None,
                source_inventory_complete=aggregates_safe if pure_static else True,
            )
            errors.extend(js_export_evidence.errors)
            typed_issues.extend(js_export_evidence.issues)
            record_source_incomplete(js_export_evidence.incomplete_reasons)
        if len(errors) < 50 and (pure_static or aggregates_safe):
            python_term_evidence = _scan_python_acceptance_term_pair_evidence(
                root_full, scanned_relative_paths, **({"reader": reader} if pure_static else {})
            )
            errors.extend(python_term_evidence.errors)
            typed_issues.extend(python_term_evidence.issues)
            record_source_incomplete(python_term_evidence.incomplete_reasons)
        if len(errors) < 50 and (pure_static or aggregates_safe):
            python_alias_evidence = _scan_python_acceptance_npm_node_alias_evidence(
                root_full,
                scanned_relative_paths,
                **({"reader": reader} if pure_static else {}),
            )
            errors.extend(python_alias_evidence.errors)
            typed_issues.extend(python_alias_evidence.issues)
            record_source_incomplete(python_alias_evidence.incomplete_reasons)
        if len(errors) < 50 and aggregates_safe:
            cross_artifact_issues = tuple(
                scan_cross_artifact_consistency(
                    root_full,
                    relative_paths=scanned_relative_paths if relative_paths is not None else None,
                    contract=interface_contract,
                    source_snapshot=source_snapshot if pure_static else None,
                    reader=reader if pure_static else None,
                )
            )
            errors.extend(
                issue.to_error_message() for issue in cross_artifact_issues if not issue.code.startswith("contract_")
            )
        if len(errors) < 50 and aggregates_safe:
            declared_interface_issues = _scan_declared_interface_ledger_issues(
                root_full,
                scanned_relative_paths if relative_paths is not None else None,
                **static_ledger_options,
            )
            typed_issues.extend(declared_interface_issues)
            errors.extend(
                issue["metadata"]["raw"]
                for issue in declared_interface_issues
                if isinstance(issue.get("metadata"), Mapping) and str(issue["metadata"].get("raw") or "").strip()
            )
    except (OSError, RuntimeError, ValueError) as exc:
        message = f"Artifact quality scan failed: {exc}"
        return _artifact_quality_evidence(
            **coverage(stopped="scan_exception"),
            errors=(*errors, message) if pure_static else (message,),
            issues=(*typed_issues, _artifact_quality_scan_failure_issue(message, exc=exc))
            if pure_static
            else (_artifact_quality_scan_failure_issue(message, exc=exc),),
            scanned_relative_paths=scanned_relative_paths,
            cross_artifact_issues=cross_artifact_issues,
        )
    return _artifact_quality_evidence(
        **coverage(stopped="error_limit" if len(errors) >= 50 else ""),
        errors=errors,
        issues=typed_issues,
        scanned_relative_paths=scanned_relative_paths,
        cross_artifact_issues=cross_artifact_issues,
        cross_artifact_repair_plans=plan_cross_artifact_repairs(cross_artifact_issues),
        contract_amendment_request=build_contract_amendment_request(
            task_id=_artifact_quality_task_id(task_id=task_id, interface_contract=interface_contract),
            issues=cross_artifact_issues,
        ),
    )


def _static_project_checks(
    root_full: Path,
    paths: list[str],
    *,
    reader: WorkspaceReadSnapshot | None = None,
    incomplete_reasons: list[str] | None = None,
) -> tuple[ArtifactQualityDeferredCheck, ...]:
    """Describe project gates without resolving tools or running commands.

    Group nested projects by their nearest manifest; missing manifests still
    require validation and cannot silently erase a compiler requirement.
    """
    groups: dict[tuple[str, str], list[str]] = {}
    for relative in paths:
        path = Path(relative)
        suffix = path.suffix.lower()
        kinds: tuple[str, ...]
        if suffix in {".ts", ".tsx"} or path.name == "tsconfig.json":
            kinds, manifest = ("typescript_project_typecheck",), "tsconfig.json"
        elif suffix == ".go" or path.name == "go.mod":
            kinds = (
                ("go_project_compile", "go_project_test") if relative.endswith("_test.go") else ("go_project_compile",)
            )
            manifest = "go.mod"
        else:
            continue
        project = ""
        for parent in path.parents:
            candidate = root_full / parent / manifest
            if reader is not None:
                try:
                    _, witness = reader.confined_path(candidate)
                    exists = witness is not None and stat.S_ISREG(witness.st_mode)
                except (OSError, RuntimeError, ValueError) as exc:
                    if incomplete_reasons is not None:
                        incomplete_reasons.append(f"project_manifest_unknown:{candidate}:{exc}")
                    exists = False
            else:
                exists = candidate.is_file()
            if exists:
                project = parent.as_posix()
                break
        for kind in kinds:
            groups.setdefault((kind, project), []).append(relative)
    return tuple(
        ArtifactQualityDeferredCheck(kind, tuple(scope), project_relative_path=project or ".")
        for (kind, project), scope in groups.items()
    )


def _static_file_checks(relative_path: str) -> tuple[ArtifactQualityDeferredCheck, ...]:
    suffix = Path(relative_path).suffix.lower()
    if suffix in {".js", ".mjs", ".cjs", ".jsx"}:
        return (ArtifactQualityDeferredCheck("javascript_parser", (relative_path,)),)
    if suffix in {".ts", ".tsx"}:
        return (ArtifactQualityDeferredCheck("typescript_parser", (relative_path,)),)
    return ()


def _iter_static_files(
    root_full: Path, relative_paths: Iterable[str] | None, incomplete_reasons: list[str]
) -> Iterable[Path]:
    """Traverse the requested scope without silently filtering unknown files."""
    seen: set[Path] = set()

    def raise_walk_error(error: OSError) -> None:
        raise error

    def directory_files(directory: Path) -> Iterable[Path]:
        for current, dirs, names in os.walk(directory, onerror=raise_walk_error, followlinks=False):
            dirs[:] = [name for name in dirs if name not in _ARTIFACT_QUALITY_SKIP_DIRS]
            for name in dirs[:]:
                path = Path(current) / name
                if path.is_symlink():
                    incomplete_reasons.append(f"scope_symlink_unvisited:{path.relative_to(root_full).as_posix()}")
                    dirs.remove(name)
            for name in names:
                yield Path(current) / name

    for raw in relative_paths if relative_paths is not None else (".",):
        requested = root_full / str(raw).replace("\\", "/")
        full = Path(os.path.abspath(requested))
        if not full.is_relative_to(root_full):
            incomplete_reasons.append(f"scope_outside_workspace:{raw}")
            continue
        if any(part in _ARTIFACT_QUALITY_SKIP_DIRS for part in full.relative_to(root_full).parts):
            incomplete_reasons.append(f"scope_excluded:{raw}")
            continue
        # Inspect parents before descendants, and before normalizing away '..'.
        # A linked ancestor must never lead even metadata reads outside scope.
        if any(
            path.is_symlink() for path in (*reversed(requested.parents), requested) if path.is_relative_to(root_full)
        ):
            incomplete_reasons.append(f"scope_symlink_unvisited:{raw}")
            continue
        if not full.exists():
            incomplete_reasons.append(f"scope_missing:{raw}")
            continue
        candidates = directory_files(full) if full.is_dir() else (full,)
        for candidate in candidates:
            if candidate.is_symlink():
                incomplete_reasons.append(f"scope_symlink_unvisited:{candidate.relative_to(root_full).as_posix()}")
                continue
            resolved = candidate.resolve()
            if not resolved.is_relative_to(root_full):
                incomplete_reasons.append(f"scope_outside_workspace:{candidate.relative_to(root_full).as_posix()}")
                continue
            if resolved not in seen and resolved.is_file():
                seen.add(resolved)
                yield resolved


def _declared_interface_contract(
    *,
    root_full: Path,
    relative_paths: Iterable[str] | None,
    task_id: str,
    pure_static: bool = False,
    incomplete_reasons: list[str] | None = None,
    ledger_snapshots: dict[str, DeclaredInterfaceLedgerSnapshot] | None = None,
) -> CrossArtifactInterfaceContract | None:
    declared: dict[str, dict[str, Any]] = {}
    target_files = list(relative_paths) if relative_paths is not None else None
    for cache_root in _interface_ledger_roots(root_full, pure_static=pure_static):
        try:
            if pure_static:
                snapshot = (
                    ledger_snapshots[cache_root]
                    if ledger_snapshots is not None
                    else read_declared_interface_ledger_snapshot(root_full.as_posix(), cache_root)
                )
                status, detail = read_declared_interface_ledger_status(
                    root_full.as_posix(), cache_root, snapshot=snapshot
                )
                if status == "unknown" and incomplete_reasons is not None:
                    incomplete_reasons.append(f"interface_ledger_unknown:{cache_root}:{detail}")
            entries = (
                read_declared_interfaces(
                    root_full.as_posix(), cache_root, target_files, snapshot=snapshot if pure_static else None
                )
                if target_files is not None
                else read_all_declared_interfaces(
                    root_full.as_posix(), cache_root, snapshot=snapshot if pure_static else None
                )
            )
        except (OSError, RuntimeError, ValueError) as exc:
            if pure_static and incomplete_reasons is not None:
                incomplete_reasons.append(f"interface_ledger_read_failed:{cache_root}:{type(exc).__name__}:{exc}")
            continue
        for target, entry in entries.items():
            current = declared.setdefault(target, {"identifiers": [], "public_symbols": [], "signatures": []})
            current["identifiers"] = _merge_quality_names(current.get("identifiers"), entry.get("identifiers"))
            current["public_symbols"] = _merge_quality_names(current.get("public_symbols"), entry.get("public_symbols"))
            current["signatures"] = _merge_quality_names(current.get("signatures"), entry.get("signatures"))
    if not declared:
        return None
    interfaces = []
    for owner_path, entry in sorted(declared.items()):
        code_symbols = _quality_string_list(entry.get("public_symbols")) or [
            identifier
            for identifier in _quality_string_list(entry.get("identifiers"))
            if _looks_like_code_symbol(identifier)
        ]
        for identifier in code_symbols:
            interfaces.append(
                {
                    "domain": "declared_interface_ledger",
                    "owner_path": owner_path,
                    "name": identifier,
                    "kind": "code_symbol",
                }
            )
    if not interfaces:
        return None
    return CrossArtifactInterfaceContract.from_mapping(
        {
            "task_id": str(task_id or "").strip(),
            "language": "",
            "interfaces": interfaces,
        }
    )


def _artifact_quality_task_id(
    *,
    task_id: str,
    interface_contract: CrossArtifactInterfaceContract | Mapping[str, Any] | None,
) -> str:
    explicit = str(task_id or "").strip()
    if explicit:
        return explicit
    if isinstance(interface_contract, CrossArtifactInterfaceContract):
        return cast("str", (interface_contract.task_id))
    if isinstance(interface_contract, Mapping):
        return str(interface_contract.get("task_id") or "").strip()
    return ""


def _scan_declared_interface_ledger_issues(
    root_full: Path,
    relative_paths: Iterable[str] | None,
    *,
    pure_static: bool = False,
    incomplete_reasons: list[str] | None = None,
    ledger_snapshots: dict[str, DeclaredInterfaceLedgerSnapshot] | None = None,
    source_snapshot: SymbolIndexSnapshot | None = None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    target_files = list(relative_paths) if relative_paths is not None else None
    for cache_root in _interface_ledger_roots(root_full, pure_static=pure_static):
        try:
            if pure_static:
                snapshot = (
                    ledger_snapshots[cache_root]
                    if ledger_snapshots is not None
                    else read_declared_interface_ledger_snapshot(root_full.as_posix(), cache_root)
                )
                status, detail = read_declared_interface_ledger_status(
                    root_full.as_posix(), cache_root, snapshot=snapshot
                )
                if status == "unknown" and incomplete_reasons is not None:
                    incomplete_reasons.append(f"interface_ledger_unknown:{cache_root}:{detail}")
            issues.extend(
                issue.to_artifact_quality_issue()
                for issue in validate_declared_interface_issues_against_snapshot(
                    root_full.as_posix(),
                    cache_root,
                    target_files,
                    ledger_snapshot=snapshot if pure_static else None,
                    source_snapshot=source_snapshot if pure_static else None,
                )
            )
        except (OSError, RuntimeError, ValueError) as exc:
            if pure_static and incomplete_reasons is not None:
                incomplete_reasons.append(f"interface_ledger_validation_failed:{cache_root}:{type(exc).__name__}:{exc}")
            continue
    deduped: dict[str, dict[str, Any]] = {}
    for issue in issues:
        metadata_raw = issue.get("metadata")
        metadata = metadata_raw if isinstance(metadata_raw, Mapping) else {}
        raw = str(metadata.get("raw") or issue.get("message") or "").strip()
        if raw:
            deduped.setdefault(raw, issue)
    return list(deduped.values())


def _interface_ledger_roots(root_full: Path, *, pure_static: bool) -> tuple[str, ...]:
    if not pure_static:
        return ("", root_full.as_posix())
    # Supplying a nonempty root prevents the ledger reader's write-probing
    # resolver. Keep the existing workspace-root legacy read candidate.
    return ((root_full / ".polaris/runtime").as_posix(), root_full.as_posix())


def _tool_receipt_contamination_error(relative_path: str, text: str) -> str:
    lowered = str(text or "").lower()
    if not any(token in lowered for token in _TOOL_RECEIPT_CONTAMINATION_TOKENS):
        return ""
    return (
        "Artifact quality scan failed: tool execution receipt contamination in "
        f"{relative_path}; file contains a Polaris tool failure receipt instead of source code. "
        "Rewrite this artifact with real UTF-8 project code and do not copy tool error text."
    )


def _source_narration_contamination_error(relative_path: str, text: str) -> str:
    suffix = Path(relative_path).suffix.lower()
    if suffix not in _ARTIFACT_QUALITY_SOURCE_EXTS:
        return ""
    stripped = str(text or "").lstrip()
    if not stripped:
        return ""
    first_line = stripped.splitlines()[0].strip()
    if first_line.startswith(("#", "//", "/*", "*", '"""', "'''")):
        return ""
    if not _SOURCE_NARRATION_LEAK_RE.search(stripped[:500]):
        return ""
    return (
        "Artifact quality scan failed: source narration contamination in "
        f"{relative_path}; file starts with assistant prose instead of project source code. "
        "Rewrite this artifact with real UTF-8 source only."
    )


def _source_map_structure_error(value: Any, *, depth: int = 0) -> str:
    """Check debug metadata shape without resolving or reading its references."""
    if depth > 8:
        return "indexed_map_depth_limit"
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 3:
        return "invalid_version_or_object"
    for field in ("file", "sourceRoot"):
        if field in value and value[field] is not None and not isinstance(value[field], str):
            return f"invalid_{field}"
    if "sections" in value:
        sections = value["sections"]
        if not isinstance(sections, list):
            return "invalid_sections"
        previous = (-1, -1)
        for section in sections:
            if not isinstance(section, dict) or not isinstance(section.get("offset"), dict):
                return "invalid_section_offset"
            offset = section["offset"]
            if any(type(offset.get(key)) is not int or offset[key] < 0 for key in ("line", "column")):
                return "invalid_section_offset"
            position = (offset["line"], offset["column"])
            if position <= previous:
                return "unordered_section_offset"
            previous = position
            if ("map" in section) == ("url" in section):
                return "invalid_section_map"
            if "map" in section:
                error = _source_map_structure_error(section["map"], depth=depth + 1)
                if error:
                    return error
            elif not isinstance(section["url"], str):
                return "invalid_section_url"
        return ""
    sources = value.get("sources")
    mappings = value.get("mappings")
    if not isinstance(sources, list) or any(source is not None and not isinstance(source, str) for source in sources):
        return "invalid_sources"
    if not isinstance(mappings, str):
        return "invalid_mappings"
    for field in ("names", "sourcesContent"):
        if field in value and (
            not isinstance(value[field], list)
            or any(
                not isinstance(item, str) and not (field == "sourcesContent" and item is None) for item in value[field]
            )
        ):
            return f"invalid_{field}"
    if "ignoreList" in value and (
        not isinstance(value["ignoreList"], list)
        or any(type(index) is not int or not 0 <= index < len(sources) for index in value["ignoreList"])
    ):
        return "invalid_ignoreList"
    # Each comma-delimited mapping has one, four, or five complete base64 VLQ
    # fields. This validates encoding only; it does not qualify generated code.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    for line in mappings.split(";"):
        if not line:
            continue
        for segment in line.split(","):
            fields = 0
            continuation = False
            for character in segment:
                digit = alphabet.find(character)
                if digit < 0:
                    return "invalid_mappings_character"
                continuation = bool(digit & 32)
                if not continuation:
                    fields += 1
            if continuation or fields not in {1, 4, 5}:
                return "invalid_mappings_segment"
    return ""


def _scan_file_evidence(
    root_full: Path,
    full_path: Path,
    relative_path: str,
    *,
    pure_static: bool = False,
    reader: WorkspaceReadSnapshot | None = None,
) -> _FileArtifactQualityEvidence:
    """Return legacy and typed artifact-quality findings for one file."""

    suffix = full_path.suffix.lower()
    repository_metadata = full_path.name == ".gitignore"
    source_map = full_path.name.lower().endswith(
        (".js.map", ".mjs.map", ".cjs.map", ".jsx.map", ".css.map", ".d.ts.map", ".d.mts.map", ".d.cts.map")
    )
    deferred_checks: list[ArtifactQualityDeferredCheck] = []
    incomplete_reasons: list[str] = []
    if pure_static:
        deferred_checks.extend(_static_file_checks(relative_path))
        if (
            not deferred_checks
            and not source_map
            and not repository_metadata
            and suffix not in {".py", ".json", ".html", ".htm", ".go", ".md", ".markdown"}
            and full_path.name.lower() not in {"cargo.toml", "go.mod"}
        ):
            incomplete_reasons.append(f"unsupported_source:{relative_path}")
    try:
        if pure_static:
            reader = reader or WorkspaceReadSnapshot(root_full)
            read = reader.read(full_path)
            if read.status != "readable":
                raise OSError(f"{read.status}:{read.detail}")
            text = read.text
        else:
            text = full_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, RuntimeError, ValueError) as exc:
        if pure_static:
            message = f"Artifact quality scan failed: unreadable {relative_path}: {exc}"
            return _FileArtifactQualityEvidence(
                errors=(message,),
                issues=(_artifact_quality_scan_failure_issue(message, exc=exc),),
                deferred_checks=tuple(deferred_checks),
                incomplete_reasons=(f"file_unreadable:{relative_path}",),
            )
        return _FileArtifactQualityEvidence()
    if pure_static and len(text) > 1_000_000:
        incomplete_reasons.append(f"file_truncated:{relative_path}")
    syntax_text = text
    text = text[:1_000_000]

    receipt_error = _tool_receipt_contamination_error(relative_path, text)
    if receipt_error:
        return _FileArtifactQualityEvidence(
            deferred_checks=tuple(deferred_checks),
            incomplete_reasons=(*incomplete_reasons, f"file_checks_skipped:{relative_path}") if pure_static else (),
            errors=(receipt_error,),
            issues=(
                _file_artifact_quality_issue(
                    receipt_error,
                    relative_path,
                    code="tool_receipt_contamination",
                ),
            ),
        )

    narration_error = _source_narration_contamination_error(relative_path, text)
    if narration_error:
        return _FileArtifactQualityEvidence(
            deferred_checks=tuple(deferred_checks),
            incomplete_reasons=(*incomplete_reasons, f"file_checks_skipped:{relative_path}") if pure_static else (),
            errors=(narration_error,),
            issues=(
                _file_artifact_quality_issue(
                    narration_error,
                    relative_path,
                    code="source_narration_contamination",
                ),
            ),
        )

    if pure_static and source_map:
        try:
            error = _source_map_structure_error(json.loads(syntax_text))
        except (ValueError, RecursionError):
            error = "invalid_json"
        if error:
            message = f"Artifact quality scan failed: invalid source map in {relative_path}: {error}"
            return _FileArtifactQualityEvidence(
                errors=(message,),
                issues=(
                    _file_artifact_quality_issue(
                        message, relative_path, code="invalid_source_map", metadata={"structure_error": error}
                    ),
                ),
                incomplete_reasons=(*incomplete_reasons, f"invalid_source_map:{relative_path}"),
            )
        return _FileArtifactQualityEvidence(
            issues=(
                ArtifactQualityIssue(
                    code="non_executable_source_map",
                    message="Source-map debug metadata covered by snapshot and structure checks; no executable verdict.",
                    path=relative_path,
                    severity="info",
                    source="file_artifact_scanner",
                    metadata={
                        "artifact_kind": "source_map",
                        "execution_qualified": False,
                        "references_followed": False,
                    },
                ),
            ),
            incomplete_reasons=tuple(incomplete_reasons),
        )

    errors: list[str] = []
    issues: list[ArtifactQualityIssue] = []
    if pure_static and repository_metadata:
        issues.append(
            ArtifactQualityIssue(
                code="non_executable_repository_metadata",
                message="Repository metadata covered by snapshot and contamination checks; no executable syntax verdict.",
                path=relative_path,
                severity="info",
                source="file_artifact_scanner",
                metadata={"artifact_kind": "repository_metadata", "execution_qualified": False},
            )
        )
    if pure_static and suffix in {".md", ".markdown"}:
        issues.append(
            ArtifactQualityIssue(
                code="non_executable_document",
                message="Documentation covered by snapshot and contamination checks; no executable syntax verdict.",
                path=relative_path,
                severity="info",
                source="file_artifact_scanner",
                metadata={"artifact_kind": "documentation", "execution_qualified": False},
            )
        )

    def append_file_issue(
        error: str,
        *,
        code: str,
        source: str = "file_artifact_scanner",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        normalized_error = str(error or "").strip()
        if not normalized_error:
            return
        errors.append(normalized_error)
        issues.append(
            _file_artifact_quality_issue(
                normalized_error,
                relative_path,
                code=code,
                source=source,
                metadata=metadata,
            )
        )

    syntax = (
        _check_source_file_syntax_static(str(full_path), text=syntax_text)
        if pure_static
        else check_source_file_syntax(str(full_path))
    )
    if syntax is not None and syntax.get("ok") is False:
        syntax_detail = str(syntax.get("error"))
        # The absolute fixture/workspace prefix must not consume the bounded
        # display budget before an actionable compiler diagnostic is shown.
        syntax_detail = syntax_detail.replace(str(full_path), relative_path)
        syntax_detail = syntax_detail.replace(str(root_full) + os.sep, "")
        diagnostic = re.search(r"\b(?:TS\d+|[A-Za-z]*Error):[^\n]*", syntax_detail)
        if diagnostic is not None and diagnostic.end() > 200:
            syntax_detail = f"{diagnostic.group(0)}\n{syntax_detail[: diagnostic.start()].rstrip()}"
        syntax_detail = syntax_detail[:200]
        syntax_error = f"Artifact quality scan failed: syntax error in {relative_path}: {syntax_detail}"
        errors.append(syntax_error)
        issues.append(
            ArtifactQualityIssue(
                code="syntax_error",
                message=f"syntax error in {relative_path}: {syntax_detail}",
                path=relative_path,
                source="source_syntax_checker",
                metadata={
                    "raw": syntax_error,
                    "syntax_error": syntax_detail,
                    "diagnostic_kind": "syntax_error",
                },
            )
        )
    if os.path.basename(relative_path).lower() == "package.json":
        manifest_evidence = (
            _scan_package_manifest_evidence(root_full, text, relative_path, pure_static=True)
            if pure_static
            else _scan_package_manifest_evidence(root_full, text, relative_path)
        )
        errors.extend(manifest_evidence.errors)
        issues.extend(manifest_evidence.issues)
        deferred_checks.extend(manifest_evidence.deferred_checks)
        incomplete_reasons.extend(manifest_evidence.incomplete_reasons)
    if os.path.basename(relative_path).lower() == "cargo.toml":
        cargo_evidence = _scan_cargo_manifest_missing_binary_evidence(root_full, text, relative_path)
        errors.extend(cargo_evidence.errors)
        issues.extend(cargo_evidence.issues)
    if os.path.basename(relative_path).lower() == "tsconfig.json":
        tsconfig_evidence = _scan_typescript_tsconfig_evidence(text, relative_path)
        errors.extend(tsconfig_evidence.errors)
        issues.extend(tsconfig_evidence.issues)
    typescript_import_evidence = _scan_typescript_import_evidence(
        root_full, full_path, text, relative_path, **({"reader": reader} if pure_static else {})
    )
    errors.extend(typescript_import_evidence.errors)
    issues.extend(typescript_import_evidence.issues)
    incomplete_reasons.extend(typescript_import_evidence.incomplete_reasons)
    typescript_red_flag_evidence = _scan_typescript_syntax_red_flag_evidence(
        root_full, full_path, text, relative_path, **({"reader": reader} if pure_static else {})
    )
    if typescript_red_flag_evidence.issues:
        # Prefer the typed, directly repairable TS red-flag contract over the
        # environment-dependent generic syntax checker projection for the same
        # file. Keeping both duplicates one defect and can schedule competing
        # repairs; the typed issue retains the precise repair archetype.
        errors = [error for error in errors if not error.startswith("Artifact quality scan failed: syntax error in ")]
        issues = [issue for issue in issues if issue.code != "syntax_error"]
    errors.extend(typescript_red_flag_evidence.errors)
    issues.extend(typescript_red_flag_evidence.issues)
    incomplete_reasons.extend(typescript_red_flag_evidence.incomplete_reasons)
    html_module_script_evidence = _scan_html_typescript_module_script_evidence(
        root_full, full_path, text, relative_path
    )
    errors.extend(html_module_script_evidence.errors)
    issues.extend(html_module_script_evidence.issues)
    for marker in _DETERMINISTIC_SCAFFOLD_MARKERS:
        if marker in text:
            append_file_issue(
                f"Artifact quality scan failed: deterministic scaffold marker {marker!r} in {relative_path}",
                code="deterministic_scaffold_marker",
                metadata={
                    "marker_kind": "deterministic_scaffold",
                    "marker_value": marker,
                },
            )
            break
    helper_count = len(_NUMERIC_HELPER_FILLER_RE.findall(text))
    if helper_count >= 5:
        append_file_issue(
            f"Artifact quality scan failed: repeated numeric helper filler in {relative_path} (count={helper_count})",
            code="repeated_numeric_helper_filler",
            metadata={"helper_count": helper_count},
        )
    if helper_count >= 3 and _GENERIC_STORE_RECORD_RE.search(text) and _GENERIC_STORE_MAP_RE.search(text):
        append_file_issue(
            f"Artifact quality scan failed: generic payload/index store scaffold in {relative_path}",
            code="generic_payload_index_store_scaffold",
            metadata={
                "helper_count": helper_count,
                "scaffold_kind": "generic_payload_index_store",
            },
        )
    patch_residue_match = _PATCH_RESIDUE_RE.search(text)
    if patch_residue_match:
        append_file_issue(
            f"Artifact quality scan failed: patch residue marker in {relative_path}",
            code="patch_residue_marker",
            metadata={
                "marker_kind": "patch_residue",
                "marker_value": patch_residue_match.group(0).strip(),
            },
        )
    if _is_test_like_artifact_path(relative_path):
        trivial_count = len(_TRIVIAL_ARITHMETIC_EXPECT_RE.findall(text))
        if trivial_count >= 3:
            append_file_issue(
                "Artifact quality scan failed: repeated trivial arithmetic placeholder "
                f"tests in {relative_path} (count={trivial_count})",
                code="repeated_trivial_arithmetic_tests",
                metadata={
                    "assertion_count": trivial_count,
                },
            )
    direct_issue_messages = {str((issue.metadata or {}).get("raw") or issue.message).strip() for issue in issues}
    residual_errors = tuple(error for error in errors if str(error or "").strip() not in direct_issue_messages)
    string_projected_issues = sys.modules[__package__]._artifact_quality_issues_from_errors(residual_errors)
    return _FileArtifactQualityEvidence(
        deferred_checks=tuple(deferred_checks),
        incomplete_reasons=tuple(incomplete_reasons),
        errors=tuple(errors),
        issues=(*issues, *string_projected_issues),
    )
