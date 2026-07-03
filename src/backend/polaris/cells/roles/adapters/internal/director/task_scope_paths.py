"""Director task-scope / declared-path extraction (pure LEAF).

Pure, dependency-free helpers extracted verbatim from ``execute_method.py``
during the lossless decomposition of that god-module. Covers task-scope and
declared-path extraction, normalization, glob matching, and label stripping.
No filesystem effects, no LLM, no sibling-module imports.

The canonical import path remains ``execute_method`` (which re-exports every
symbol here); this module exists only to host the leaf logic.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from polaris.kernelone.quality.scope_authority import (
    glob_declared_scope_path_matches,
    normalize_declared_scope_path,
    path_matches_any_declared_scope_candidate,
    path_matches_declared_scope_candidate,
)


def _filter_diff_to_task_declared_paths(
    *,
    task: dict[str, Any],
    new_files: list[str],
    modified_files: list[str],
    workspace_name: str = "",
) -> tuple[list[str], list[str]]:
    """Keep only diff files that belong to the task's declared target paths.

    Director tasks may execute in parallel. A process-wide workspace diff can
    contain files changed by sibling tasks, so accepting any changed file as
    evidence lets unrelated work complete the current task. Exact
    ``target_files`` are strongest and are used before broader scope paths.
    """

    candidates = _extract_task_target_path_candidates(task)
    if not candidates:
        candidates = _extract_task_path_candidates(task)
    normalized_candidates = [
        _normalize_declared_task_path(candidate, workspace_name=workspace_name) for candidate in candidates
    ]
    normalized_candidates = _dedupe_preserve_order([candidate for candidate in normalized_candidates if candidate])
    if not normalized_candidates:
        return new_files, modified_files

    return (
        [path for path in new_files if _path_matches_any_declared_candidate(path, normalized_candidates)],
        [path for path in modified_files if _path_matches_any_declared_candidate(path, normalized_candidates)],
    )


def _extract_task_target_path_candidates(task: dict[str, Any]) -> list[str]:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    candidates: list[str] = []
    scope_candidates: list[str] = []
    for record in (task, metadata):
        if not isinstance(record, dict):
            continue
        for key in ("target_files", "target_file", "targets"):
            candidates.extend(_coerce_path_candidate_list(record.get(key)))
        for key in ("scope_paths", "scope", "file_paths", "files", "paths"):
            for candidate in _coerce_path_candidate_list(record.get(key)):
                if _looks_like_task_path_candidate(candidate) or _looks_like_task_scope_directory_candidate(candidate):
                    scope_candidates.append(candidate)
    if not candidates:
        candidates.extend(scope_candidates)
    else:
        explicit_targets = [
            normalized
            for candidate in candidates
            if (normalized := _normalize_declared_task_path(candidate)) and Path(normalized).suffix
        ]
        candidates.extend(
            candidate
            for candidate in scope_candidates
            if (
                _looks_like_task_scope_directory_candidate(candidate)
                and not _scope_directory_covers_explicit_target(candidate, explicit_targets)
            )
            or Path(_normalize_declared_task_path(candidate)).suffix
        )
    return _dedupe_preserve_order(
        [
            candidate
            for candidate in candidates
            if _looks_like_task_path_candidate(candidate) or _looks_like_task_scope_directory_candidate(candidate)
        ]
    )


def _path_matches_any_declared_candidate(path: str, candidates: list[str]) -> bool:
    return path_matches_any_declared_scope_candidate(path, candidates)


def _path_matches_declared_candidate(path: str, candidate: str) -> bool:
    return path_matches_declared_scope_candidate(path, candidate)


def _workspace_path_exists_case_insensitive(root: Path, rel_path: str) -> bool:
    """Check workspace-relative existence, tolerating path-case drift."""
    candidate = root / rel_path
    if candidate.exists():
        return True
    current = root
    for part in rel_path.split("/"):
        if not current.is_dir():
            return False
        matched = next(
            (entry for entry in current.iterdir() if entry.name.casefold() == part.casefold()),
            None,
        )
        if matched is None:
            return False
        current = matched
    return True


def _task_text_blob(task: dict[str, Any]) -> str:
    rows: list[str] = []
    raw_metadata = task.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    for record in (task, metadata):
        if not isinstance(record, dict):
            continue
        for key in (
            "subject",
            "title",
            "description",
            "goal",
            "scope",
            "scope_paths",
            "target_files",
            "steps",
            "execution_checklist",
            "acceptance",
            "acceptance_criteria",
            "backlog_ref",
        ):
            value = record.get(key)
            if isinstance(value, list):
                rows.extend(str(item) for item in value)
            elif value is not None:
                rows.append(str(value))
    return "\n".join(rows)


def _task_has_declared_target_files(task: dict[str, Any]) -> bool:
    return bool(_extract_task_target_path_candidates(task))


def _extract_task_path_candidates(task: dict[str, Any]) -> list[str]:
    """Extract path-like values from PM/Director task contracts."""
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    sources: list[Any] = []
    for record in (task, metadata):
        if not isinstance(record, dict):
            continue
        for key in (
            "scope",
            "scope_paths",
            "target_files",
            "files",
            "file_paths",
            "paths",
            "artifacts",
        ):
            sources.append(record.get(key))
        for key in ("subject", "description", "goal"):
            value = record.get(key)
            if isinstance(value, str):
                sources.extend(_extract_scope_markers_from_text(value))

    candidates: list[str] = []
    for value in sources:
        candidates.extend(_coerce_path_candidate_list(value))
    return _dedupe_preserve_order([candidate for candidate in candidates if _looks_like_task_path_candidate(candidate)])


_BRACKETED_SCOPE_RE = re.compile(r"\[(?:scope|范围)\s*[:：]\s*(?P<value>[^\]]+)\]", re.IGNORECASE)


_LINE_SCOPE_RE = re.compile(r"(?im)^\s*(?:scope|范围)\s*[:：]\s*(?P<value>.+?)\s*$")


def _extract_scope_markers_from_text(value: str) -> list[str]:
    """Extract scope values embedded in orchestration task prose."""
    text = str(value or "")
    rows = [match.group("value").strip() for match in _BRACKETED_SCOPE_RE.finditer(text)]
    rows.extend(match.group("value").strip() for match in _LINE_SCOPE_RE.finditer(text))
    return [row for row in rows if row]


def _coerce_path_candidate_list(value: Any) -> list[str]:
    if isinstance(value, list):
        rows: list[str] = []
        for item in value:
            rows.extend(_coerce_path_candidate_list(item))
        return rows
    if isinstance(value, dict):
        rows = []
        for key in ("path", "file", "name", "target"):
            item = value.get(key)
            if isinstance(item, str):
                rows.append(item)
        return rows
    if isinstance(value, str):
        return [
            _strip_path_candidate_label(part.strip())
            for part in value.replace(";", "\n").replace(",", "\n").splitlines()
            if part.strip()
        ]
    return []


def _strip_path_candidate_label(value: str) -> str:
    """Strip human-readable scope labels from path candidate fragments."""
    token = str(value or "").strip()
    if not token or re.match(r"^[a-zA-Z]:[\\/]", token):
        return token
    separator = "：" if "：" in token else ":"
    if separator not in token:
        return token
    prefix, suffix = token.split(separator, 1)
    suffix = suffix.strip()
    if not suffix:
        return token
    normalized_suffix = suffix.replace("\\", "/")
    suffix_looks_like_path = "/" in normalized_suffix or bool(Path(normalized_suffix).suffix)
    if suffix_looks_like_path and not _looks_like_task_path_candidate(prefix.strip()):
        return suffix
    return token


def _looks_like_task_path_candidate(value: str) -> bool:
    token = _normalize_declared_task_path(value)
    if not token or token.startswith("-"):
        return False
    if any(ch in token for ch in ("<", ">", "|")):
        return False
    if token in {".", "./"}:
        return False
    if any(ch in token for ch in ("*", "?")):
        return "/" in token
    if "/" in token:
        return True
    return bool(Path(token).suffix)


_COMMON_TASK_SCOPE_DIRECTORIES = {
    "app",
    "apps",
    "assets",
    "bin",
    "client",
    "cmd",
    "components",
    "core",
    "docs",
    "engine",
    "lib",
    "models",
    "pages",
    "public",
    "scripts",
    "server",
    "src",
    "test",
    "tests",
    "utils",
    "web",
}


def _looks_like_task_scope_directory_candidate(value: str) -> bool:
    token = _normalize_declared_task_path(value)
    if not token or token.startswith("-"):
        return False
    if any(ch in token for ch in ("<", ">", "|", "*", "?")):
        return False
    if Path(token).suffix:
        return False
    parts = [part for part in token.split("/") if part]
    if not parts:
        return False
    if len(parts) > 1:
        return all(re.match(r"^[A-Za-z0-9._-]+$", part) for part in parts)
    return parts[0].casefold() in _COMMON_TASK_SCOPE_DIRECTORIES


def _scope_directory_covers_explicit_target(scope_candidate: str, explicit_targets: list[str]) -> bool:
    scope = _normalize_declared_task_path(scope_candidate).rstrip("/")
    if not scope:
        return False
    scope_folded = scope.casefold()
    return any(
        target.casefold() == scope_folded or target.casefold().startswith(f"{scope_folded}/")
        for target in explicit_targets
    )


def _normalize_declared_task_path(value: str, *, workspace_name: str = "") -> str:
    return normalize_declared_scope_path(value, workspace_name=workspace_name)


def _path_candidate_exists_in_file_set(candidate: str, current_files: set[str]) -> bool:
    candidate = candidate.rstrip("/")
    if not candidate:
        return False
    if any(ch in candidate for ch in ("*", "?")):
        return any(_glob_path_matches(path, candidate) for path in current_files)
    if candidate in current_files:
        return True
    directory_prefix = f"{candidate}/"
    if any(path.startswith(directory_prefix) for path in current_files):
        return True
    # Small tolerance for PM contracts that use singular/plural workbench dirs.
    return any(path.startswith(candidate) and "/" in path[len(candidate) :] for path in current_files)


def _glob_path_matches(path: str, pattern: str) -> bool:
    return glob_declared_scope_path_matches(path, pattern)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = str(value or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        rows.append(token)
    return rows
