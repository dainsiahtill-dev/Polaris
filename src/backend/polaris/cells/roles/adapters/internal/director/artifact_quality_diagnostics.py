"""Artifact-quality diagnostic parsing helpers.

These helpers are not deterministic repair strategies. They parse quality-gate
diagnostics and suppress stale missing-file findings after a later verifier or
repair side effect has materialized the declared file.
"""

from __future__ import annotations

import re
from pathlib import Path

from .task_scope_paths import (
    _dedupe_preserve_order,
    _normalize_declared_task_path,
    _workspace_path_exists_case_insensitive,
)

_DECLARED_TARGET_FILE_MISSING_ERROR_RE = re.compile(
    r"declared target file(?:\s+missing)?\s+['\"](?P<path>[^'\"]+)['\"](?:\s+is\s+missing)?",
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
    """Drop stale declared-target-missing errors after later side effects."""

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
    importer_suffix = importer_path.suffix.lower()
    if base.suffix:
        raw_candidates = []
        if importer_suffix in {".ts", ".tsx"} and base.suffix.lower() in {".cjs", ".js", ".jsx", ".mjs"}:
            source_suffixes = (".tsx", ".ts") if importer_suffix == ".tsx" else (".ts", ".tsx")
            raw_candidates.extend(base.with_suffix(suffix) for suffix in source_suffixes)
        raw_candidates.append(base)
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


def _missing_unresolved_relative_import_target_files(
    artifact_quality_errors: list[str],
    workspace: str,
) -> list[str]:
    root = Path(str(workspace or "")).resolve()
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


def _build_unresolved_import_symbol_repair_block(artifact_quality_errors: list[str]) -> str:
    symbol_errors: list[tuple[str, str, str]] = []
    for item in artifact_quality_errors:
        match = _UNRESOLVED_IMPORT_SYMBOL_ERROR_RE.search(str(item or ""))
        if not match:
            continue
        symbol = str(match.group("symbol") or "").strip()
        module = str(match.group("module") or "").strip()
        importer = _normalize_declared_task_path(match.group("path"))
        if symbol and module and importer:
            symbol_errors.append((symbol, module, importer))

    if not symbol_errors:
        return ""

    symbol_lines = "\n".join(
        f"- Module '{module}' must define/export symbol '{symbol}' for importer '{importer}'."
        for symbol, module, importer in symbol_errors[:12]
    )
    return (
        "CROSS-FILE SYMBOL REPAIR: an importing file already exists, but the "
        "sibling/exporting module does not define a symbol that importer needs. "
        "Do not edit the importing file. Do not remove or weaken the import. "
        "For the symbol errors below, update the exporting module named after "
        "`from ...` and make the exporting module define or export exactly the "
        "missing symbol(s). If this repair prompt also names package or typecheck "
        "targets, repair those named targets in the same batch. Do not create "
        "unrelated files. Do not read files first. Do not list directories. Do "
        "not explore. Do not explain.\n"
        f"{symbol_lines}\n"
    )
