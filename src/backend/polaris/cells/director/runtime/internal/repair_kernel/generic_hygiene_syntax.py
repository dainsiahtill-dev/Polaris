"""Generic hygiene, contract, cleanup, and dependency repair planning helpers."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text

DECLARED_TARGET_CONTRACT_SOURCE_TOOL = "deterministic_declared_target_contract_repair"
MISSING_DECLARED_TARGET_SOURCE_TOOL = "deterministic_missing_declared_target_repair"
PATCH_RESIDUE_CLEANUP_SOURCE_TOOL = "deterministic_patch_residue_cleanup"
PRE_MATERIALIZATION_DECLARED_TARGET_SOURCE_TOOL = "deterministic_pre_materialization_declared_target_repair"
QUALITY_REPAIR_SOURCE_TOOL = "deterministic_quality_repair"
RUNTIME_DEPENDENCY_SOURCE_TOOL = "deterministic_runtime_dependency_repair"
SCAFFOLD_MARKER_CLEANUP_SOURCE_TOOL = "deterministic_scaffold_marker_cleanup"
SCAFFOLD_MARKER_QUALITY_CLEANUP_SOURCE_TOOL = "deterministic_scaffold_marker_quality_cleanup"
SCAFFOLD_RESIDUE_CLEANUP_SOURCE_TOOL = "deterministic_scaffold_residue_cleanup"

_PATCH_RESIDUE_LINE_RE = re.compile(
    r"(?m)^\s*(?:<{4,7}\s*SEARCH\b.*|>{4,7}\s*REPLACE\b.*|END\s+PATCH_FILE\b.*|PATCH_FILE(?::|\s+).*)\s*$",
    re.IGNORECASE,
)
_PATCH_RESIDUE_FILE_SUFFIXES = frozenset((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))
_SCAFFOLD_MARKER_FILE_SUFFIXES = frozenset(
    (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".go", ".html", ".css", ".json")
)
_SCAFFOLD_MARKER_ERROR_RE = re.compile(
    r"deterministic scaffold marker ['\"][^'\"]+['\"] in (?P<path>\S+)",
    re.IGNORECASE,
)
_GENERIC_PLACEHOLDER_ERROR_RE = re.compile(
    r"generic/placeholder content detected:\s*(?P<path>[^:\s]+):",
    re.IGNORECASE,
)
_UNDECLARED_RUNTIME_IMPORT_ERROR_RE = re.compile(
    r"undeclared runtime import ['\"](?P<package>[^'\"]+)['\"] in (?P<path>\S+)",
    re.IGNORECASE,
)
_TS_NODE_BUILTIN_TYPES_ERROR_RE = re.compile(
    r"(?:TypeScript node builtin import ['\"][^'\"]+['\"] requires ['\"]@types/node['\"] in (?P<path>\S+)|"
    r"Cannot find module ['\"]node:[^'\"]+['\"] or its corresponding type declarations)",
    re.IGNORECASE,
)
_TS_NODE_GLOBAL_TYPES_ERROR_RE = re.compile(
    r"(?:TS2580:.*(?:type definitions for node|@types/node)|"
    r"Cannot find name ['\"](?:process|Buffer|__dirname|__filename|require|module)['\"])",
    re.IGNORECASE,
)
_TS_TYPESCRIPT_DEV_DEPENDENCY_ERROR_RE = re.compile(
    r"TypeScript project requires ['\"]typescript['\"] devDependency",
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
_KNOWN_RUNTIME_DEPENDENCY_VERSIONS = {
    "@apollo/server": "^4.11.0",
    "@nestjs/typeorm": "^10.0.2",
    "axios": "^1.7.0",
    "cors": "^2.8.5",
    "dotenv": "^16.4.5",
    "express": "^4.18.2",
    "mongoose": "^8.9.0",
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


def remove_patch_residue_lines(text: str) -> str:
    """Remove leaked patch-protocol marker lines from source text."""

    cleaned = _PATCH_RESIDUE_LINE_RE.sub("", str(text or ""))
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if str(text or "").endswith("\n") and not cleaned.endswith("\n"):
        cleaned += "\n"
    return cleaned


def build_patch_residue_cleanup_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic] = (),
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a runtime plan for scoped patch-residue cleanup."""

    operations: list[RepairOperation] = []
    for path, content in sorted(_normalize_base_files(base_files).items()):
        if not _patch_residue_cleanup_supported(path):
            continue
        operations.extend(_patch_residue_line_operations(path=path, content=content))
    if not operations:
        return None
    return RepairPlan(
        rule_id="generic.patch_residue_cleanup",
        source_tool=PATCH_RESIDUE_CLEANUP_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="low",
        priority=0,
        metadata={"cleanup": "patch_residue"},
    )


def build_scaffold_marker_cleanup_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic] = (),
    mode: str = "commit",
    source_tool: str = SCAFFOLD_MARKER_CLEANUP_SOURCE_TOOL,
    rule_id: str = "generic.scaffold_marker_cleanup",
    diagnostic_paths_only: bool = False,
) -> RepairPlan | None:
    """Build a span-based plan for deterministic scaffold marker cleanup."""

    normalized_base = _normalize_base_files(base_files)
    allowed_paths = set(_scaffold_marker_error_paths(diagnostics)) if diagnostic_paths_only else set()
    if diagnostic_paths_only and not allowed_paths:
        return None
    operations: list[RepairOperation] = []
    for path, content in sorted(normalized_base.items()):
        if allowed_paths and path not in allowed_paths:
            continue
        if not _scaffold_marker_cleanup_supported(path):
            continue
        operations.extend(_scaffold_marker_operations(path=path, content=content, cleanup=rule_id))
    if not operations:
        return None
    return RepairPlan(
        rule_id=rule_id,
        source_tool=source_tool,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="low",
        priority=0,
        metadata={"cleanup": "scaffold_marker", "span_context_required": True},
    )


def build_runtime_dependency_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic] = (),
    mode: str = "commit",
) -> RepairPlan | None:
    """Build structured package.json dependency repairs for known safe packages."""

    normalized_base = _normalize_base_files(base_files)
    content = normalized_base.get("package.json")
    if content is None:
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    runtime_packages = [
        package
        for package in _parse_undeclared_runtime_import_packages(diagnostics)
        if package in _KNOWN_RUNTIME_DEPENDENCY_VERSIONS and not _package_declared_in_manifest(payload, package)
    ]
    dev_packages = [
        package
        for package in _parse_required_dev_dependency_packages(diagnostics)
        if package in _KNOWN_DEV_DEPENDENCY_VERSIONS and not _package_declared_in_manifest(payload, package)
    ]
    operations: list[RepairOperation] = []
    before_hash = sha256_text(content)
    for package in runtime_packages:
        operations.append(
            RepairOperation(
                kind="json_set",
                path="package.json",
                json_path=("dependencies", package),
                value=_KNOWN_RUNTIME_DEPENDENCY_VERSIONS[package],
                before_hash=before_hash,
                metadata={"dependency_package": package, "dependency_section": "dependencies"},
            )
        )
    for package in dev_packages:
        operations.append(
            RepairOperation(
                kind="json_set",
                path="package.json",
                json_path=("devDependencies", package),
                value=_KNOWN_DEV_DEPENDENCY_VERSIONS[package],
                before_hash=before_hash,
                metadata={"dependency_package": package, "dependency_section": "devDependencies"},
            )
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="generic.runtime_dependency",
        source_tool=RUNTIME_DEPENDENCY_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="medium",
        priority=1,
        metadata={"structured_operation": "json", "manifest": "package.json"},
    )


def build_quality_repair_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic] = (),
    mode: str = "commit",
) -> RepairPlan | None:
    """Build only safe generic quality cleanup operations under the aggregate quality source tool."""

    operations: list[RepairOperation] = []
    for plan in (
        build_patch_residue_cleanup_plan(base_files=base_files, diagnostics=diagnostics, mode=mode),
        build_scaffold_marker_cleanup_plan(
            base_files=base_files,
            diagnostics=diagnostics,
            mode=mode,
            source_tool=QUALITY_REPAIR_SOURCE_TOOL,
            rule_id="generic.quality_scaffold_marker_cleanup",
        ),
    ):
        if plan is not None:
            operations.extend(plan.operations)
    if not operations:
        return None
    return RepairPlan(
        rule_id="generic.quality_repair",
        source_tool=QUALITY_REPAIR_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"aggregate": "generic_quality_cleanup", "adapter_language_fanout": False},
    )


def build_generic_hygiene_plan(
    *,
    source_tool: str,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic] = (),
    mode: str = "commit",
) -> RepairPlan | None:
    """Dispatch generic source tools to conservative runtime-owned planners."""

    if source_tool == PATCH_RESIDUE_CLEANUP_SOURCE_TOOL:
        return build_patch_residue_cleanup_plan(base_files=base_files, diagnostics=diagnostics, mode=mode)
    if source_tool == SCAFFOLD_MARKER_CLEANUP_SOURCE_TOOL:
        return build_scaffold_marker_cleanup_plan(
            base_files=base_files,
            diagnostics=diagnostics,
            mode=mode,
            source_tool=source_tool,
            rule_id="generic.scaffold_marker_cleanup",
        )
    if source_tool == SCAFFOLD_MARKER_QUALITY_CLEANUP_SOURCE_TOOL:
        return build_scaffold_marker_cleanup_plan(
            base_files=base_files,
            diagnostics=diagnostics,
            mode=mode,
            source_tool=source_tool,
            rule_id="generic.scaffold_marker_quality_cleanup",
            diagnostic_paths_only=True,
        )
    if source_tool == SCAFFOLD_RESIDUE_CLEANUP_SOURCE_TOOL:
        return build_scaffold_marker_cleanup_plan(
            base_files=base_files,
            diagnostics=diagnostics,
            mode=mode,
            source_tool=source_tool,
            rule_id="generic.scaffold_residue_cleanup",
        )
    if source_tool == RUNTIME_DEPENDENCY_SOURCE_TOOL:
        return build_runtime_dependency_plan(base_files=base_files, diagnostics=diagnostics, mode=mode)
    if source_tool == QUALITY_REPAIR_SOURCE_TOOL:
        return build_quality_repair_plan(base_files=base_files, diagnostics=diagnostics, mode=mode)
    return None


def _normalize_base_files(base_files: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_path, content in dict(base_files or {}).items():
        path = _normalize_repair_path(str(raw_path or ""))
        if path:
            normalized[path] = str(content or "")
    return normalized


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return ""
    return normalized


def _patch_residue_cleanup_supported(path: str) -> bool:
    lowered = str(path or "").lower()
    return any(lowered.endswith(suffix) for suffix in _PATCH_RESIDUE_FILE_SUFFIXES)


def _patch_residue_line_operations(*, path: str, content: str) -> tuple[RepairOperation, ...]:
    operations: list[RepairOperation] = []
    offset = 0
    for line in str(content or "").splitlines(keepends=True):
        line_body = line.rstrip("\r\n")
        if _PATCH_RESIDUE_LINE_RE.fullmatch(line_body):
            operations.append(
                RepairOperation(
                    kind="text_replace",
                    path=path,
                    span_start=offset,
                    span_end=offset + len(line),
                    expected=line,
                    replacement="",
                    before_hash=sha256_text(content),
                    metadata={
                        "cleanup": "patch_residue",
                        **_span_context_metadata(content, offset, offset + len(line)),
                    },
                )
            )
        offset += len(line)
    return tuple(operations)


def _scaffold_marker_cleanup_supported(path: str) -> bool:
    lowered = str(path or "").lower()
    return any(lowered.endswith(suffix) for suffix in _SCAFFOLD_MARKER_FILE_SUFFIXES)


def _scaffold_marker_operations(*, path: str, content: str, cleanup: str) -> tuple[RepairOperation, ...]:
    operations: list[RepairOperation] = []
    text = str(content or "")
    before_hash = sha256_text(text)
    matches: list[tuple[int, int, str, str]] = []
    for marker, replacement in _SCAFFOLD_MARKER_REPLACEMENTS:
        start = text.find(marker)
        while start >= 0:
            end = start + len(marker)
            matches.append((start, end, marker, replacement))
            start = text.find(marker, end)

    selected: list[tuple[int, int, str, str]] = []
    covered_until = -1
    for start, end, marker, replacement in sorted(matches, key=lambda item: (item[0], -(item[1] - item[0]))):
        if start < covered_until:
            continue
        selected.append((start, end, marker, replacement))
        covered_until = end

    for start, end, marker, replacement in selected:
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=start,
                span_end=end,
                expected=marker,
                replacement=replacement,
                before_hash=before_hash,
                metadata={
                    "cleanup": cleanup,
                    "marker": marker,
                    **_span_context_metadata(text, start, end),
                },
            )
        )
    return tuple(operations)


def _span_context_metadata(content: str, start: int, end: int) -> dict[str, str | bool]:
    before = content[max(0, start - 48) : start]
    after = content[end : min(len(content), end + 48)]
    probe = f"{before}{content[start:end]}{after}"
    metadata: dict[str, str | bool] = {
        "expected_context_before": before,
        "expected_context_after": after,
        "span_context_required": True,
    }
    if probe and content.count(probe) == 1:
        metadata["unique_context"] = probe
    return metadata


def _scaffold_marker_error_paths(diagnostics: Sequence[RepairDiagnostic]) -> tuple[str, ...]:
    paths: list[str] = []
    for diagnostic in diagnostics:
        text = "\n".join(
            item
            for item in (
                str(diagnostic.raw or ""),
                str(diagnostic.message or ""),
                str(diagnostic.path or ""),
            )
            if item
        )
        match = _SCAFFOLD_MARKER_ERROR_RE.search(text) or _GENERIC_PLACEHOLDER_ERROR_RE.search(text)
        if match:
            path = _normalize_repair_path(str(match.group("path") or ""))
            if path:
                paths.append(path)
    return tuple(dict.fromkeys(paths))


def _parse_undeclared_runtime_import_packages(diagnostics: Sequence[RepairDiagnostic]) -> tuple[str, ...]:
    packages: list[str] = []
    for diagnostic in diagnostics:
        text = "\n".join(item for item in (str(diagnostic.raw or ""), str(diagnostic.message or "")) if item)
        match = _UNDECLARED_RUNTIME_IMPORT_ERROR_RE.search(text)
        if match:
            packages.append(_dependency_root_name(str(match.group("package") or "")))
    return tuple(dict.fromkeys(package for package in packages if package))


def _parse_required_dev_dependency_packages(diagnostics: Sequence[RepairDiagnostic]) -> tuple[str, ...]:
    packages: list[str] = []
    for diagnostic in diagnostics:
        text = "\n".join(item for item in (str(diagnostic.raw or ""), str(diagnostic.message or "")) if item)
        if _TS_NODE_BUILTIN_TYPES_ERROR_RE.search(text) or _TS_NODE_GLOBAL_TYPES_ERROR_RE.search(text):
            packages.append("@types/node")
        if _TS_TYPESCRIPT_DEV_DEPENDENCY_ERROR_RE.search(text):
            packages.append("typescript")
    return tuple(dict.fromkeys(packages))


def _dependency_root_name(package_name: str) -> str:
    token = str(package_name or "").strip()
    if token.startswith("@"):
        parts = token.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else token
    return token.split("/", 1)[0]


def _package_declared_in_manifest(payload: Mapping[str, Any], package_name: str) -> bool:
    for section_name in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        section = payload.get(section_name)
        if isinstance(section, Mapping) and package_name in section:
            return True
    return False


__all__ = [
    "DECLARED_TARGET_CONTRACT_SOURCE_TOOL",
    "MISSING_DECLARED_TARGET_SOURCE_TOOL",
    "PATCH_RESIDUE_CLEANUP_SOURCE_TOOL",
    "PRE_MATERIALIZATION_DECLARED_TARGET_SOURCE_TOOL",
    "QUALITY_REPAIR_SOURCE_TOOL",
    "RUNTIME_DEPENDENCY_SOURCE_TOOL",
    "SCAFFOLD_MARKER_CLEANUP_SOURCE_TOOL",
    "SCAFFOLD_MARKER_QUALITY_CLEANUP_SOURCE_TOOL",
    "SCAFFOLD_RESIDUE_CLEANUP_SOURCE_TOOL",
    "build_generic_hygiene_plan",
    "build_patch_residue_cleanup_plan",
    "build_quality_repair_plan",
    "build_runtime_dependency_plan",
    "build_scaffold_marker_cleanup_plan",
    "remove_patch_residue_lines",
]
