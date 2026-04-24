from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from polaris.kernelone.constants import (
    MAX_LINE_CHARS,
    MAX_SEARCH_FILE_SIZE_BYTES,
    MAX_SNIPPET_CHARS,
)

_MAX_FILE_SIZE_BYTES = MAX_SEARCH_FILE_SIZE_BYTES
_MAX_SNIPPET_CHARS = MAX_SNIPPET_CHARS
_MAX_LINE_CHARS = MAX_LINE_CHARS

_DEFAULT_EXCLUDED_DIRS: set[str] = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".polaris",
    "dist",
    "build",
    ".egg-info",
    ".tox",
    ".venv",
    "venv",
}


def _normalize_patterns(patterns: list[str] | None) -> list[str]:
    if not patterns:
        return []
    normalized: list[str] = []
    for pattern in patterns:
        token = str(pattern or "").strip().replace("\\", "/")
        if token:
            normalized.append(token)
    return normalized


def _normalize_rel_path(project_dir: Path, file_path: Path) -> str:
    try:
        rel_path = file_path.relative_to(project_dir)
    except ValueError:
        rel_path = file_path
    return rel_path.as_posix()


def _matches_any(path_text: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    base_name = path_text.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(path_text, pattern) or fnmatch.fnmatch(base_name, pattern) for pattern in patterns)


def _should_skip_default(path_text: str) -> bool:
    path_parts = Path(path_text).parts
    return any(part in _DEFAULT_EXCLUDED_DIRS for part in path_parts)


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return f"{text[: max_chars - 3]}..."


def _build_snippet(
    lines: list[str],
    line_idx: int,
    *,
    context_lines: int,
) -> tuple[str, int, int]:
    snippet_start = max(0, line_idx - context_lines)
    snippet_end = min(len(lines), line_idx + context_lines + 1)
    snippet = "\n".join(lines[snippet_start:snippet_end])
    return _truncate_text(snippet, _MAX_SNIPPET_CHARS), snippet_start + 1, snippet_end


def _iter_candidate_files(
    project_dir: Path,
    *,
    file_patterns: list[str],
    include_patterns: list[str],
    exclude_patterns: list[str],
) -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = []
    for file_path in project_dir.rglob("*"):
        if not file_path.is_file():
            continue
        rel_path = _normalize_rel_path(project_dir, file_path)
        if _should_skip_default(rel_path):
            continue
        if include_patterns and not _matches_any(rel_path, include_patterns):
            continue
        if exclude_patterns and _matches_any(rel_path, exclude_patterns):
            continue
        if file_patterns and not _matches_any(rel_path, file_patterns):
            continue
        candidates.append((file_path, rel_path))
    candidates.sort(key=lambda item: item[1])
    return candidates


def _compile_pattern(
    pattern: str,
    *,
    case_sensitive: bool,
    use_regex: bool,
) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    source = pattern if use_regex else re.escape(pattern)
    return re.compile(source, flags)


def search_code_content(
    *,
    project_dir: Path,
    pattern: str,
    file_patterns: list[str] | None = None,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    context_lines: int = 2,
    max_results: int = 50,
    case_sensitive: bool = False,
    use_regex: bool = True,
) -> dict[str, Any]:
    """Search file contents with optional path filters and line context."""
    pattern_text = str(pattern or "").strip()
    if not pattern_text:
        return {
            "status": "error",
            "error": "empty_pattern",
            "message": "pattern must be a non-empty string",
            "matches": [],
        }

    if not project_dir.exists() or not project_dir.is_dir():
        return {
            "status": "error",
            "error": "project_not_found",
            "message": f"Project directory not found: {project_dir}",
            "matches": [],
        }

    normalized_file_patterns = _normalize_patterns(file_patterns)
    normalized_include_patterns = _normalize_patterns(include_patterns)
    normalized_exclude_patterns = _normalize_patterns(exclude_patterns)

    bounded_context_lines = max(0, min(20, int(context_lines)))
    bounded_max_results = max(1, min(500, int(max_results)))

    try:
        compiled_pattern = _compile_pattern(pattern_text, case_sensitive=case_sensitive, use_regex=use_regex)
    except re.error as exc:
        return {
            "status": "error",
            "error": "invalid_pattern",
            "message": f"Invalid pattern: {exc}",
            "matches": [],
        }

    matches: list[dict[str, Any]] = []
    files_scanned = 0
    files_with_matches: set[str] = set()
    truncated = False

    for file_path, rel_path in _iter_candidate_files(
        project_dir,
        file_patterns=normalized_file_patterns,
        include_patterns=normalized_include_patterns,
        exclude_patterns=normalized_exclude_patterns,
    ):
        files_scanned += 1
        try:
            if file_path.stat().st_size > _MAX_FILE_SIZE_BYTES:
                continue
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        for line_idx, line in enumerate(lines):
            for match in compiled_pattern.finditer(line):
                snippet, snippet_start, snippet_end = _build_snippet(
                    lines,
                    line_idx,
                    context_lines=bounded_context_lines,
                )
                files_with_matches.add(rel_path)
                matches.append(
                    {
                        "file": rel_path,
                        "line": line_idx + 1,
                        "column": match.start() + 1,
                        "match": _truncate_text(match.group(0), _MAX_LINE_CHARS),
                        "line_text": _truncate_text(line, _MAX_LINE_CHARS),
                        "snippet": snippet,
                        "snippet_start_line": snippet_start,
                        "snippet_end_line": snippet_end,
                    }
                )
                if len(matches) >= bounded_max_results:
                    truncated = True
                    break
            if truncated:
                break
        if truncated:
            break

    return {
        "status": "ok",
        "pattern": pattern_text,
        "regex": bool(use_regex),
        "case_sensitive": bool(case_sensitive),
        "context_lines": bounded_context_lines,
        "max_results": bounded_max_results,
        "result_count": len(matches),
        "truncated": truncated,
        "files_scanned": files_scanned,
        "files_with_matches": len(files_with_matches),
        "matches": matches,
        "filters": {
            "file_patterns": normalized_file_patterns,
            "include_patterns": normalized_include_patterns,
            "exclude_patterns": normalized_exclude_patterns,
        },
    }
