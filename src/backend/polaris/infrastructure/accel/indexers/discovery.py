from __future__ import annotations

import fnmatch
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ..polaris_paths import default_accel_runtime_home
from ..language_profiles import resolve_extension_language_map

LEGACY_DEFAULT_INDEX_INCLUDE = ["src/**", "accel/**", "tests/**"]
DEFAULT_INDEX_EXCLUDES = [
    ".git/**",
    "node_modules/**",
    "dist/**",
    "build/**",
    "target/**",
    ".venv/**",
    "venv/**",
    ".polaris/projects/**",
    ".polaris/logs/**",
    ".polaris/snapshots/**",
    ".polaris/projects/**",
    ".polaris/logs/**",
    ".polaris/snapshots/**",
    ".mypy_cache/**",
    ".pytest_cache/**",
    ".ruff_cache/**",
    ".next/**",
    ".turbo/**",
]
AUTO_INCLUDE_FALLBACK_MAX_DEPTH = 2

_deadlock_logger = logging.getLogger("accel_deadlock_detection")
_deadlock_logger.setLevel(logging.DEBUG)


def _setup_deadlock_logging() -> None:
    if not _deadlock_logger.handlers:
        log_dir = default_accel_runtime_home(Path(os.path.abspath("."))) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"deadlock_detection_{int(time.time())}.log"
        handler = logging.FileHandler(log_file, encoding="utf-8")
        formatter = logging.Formatter(
            "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        _deadlock_logger.addHandler(handler)


def _log_deadlock_info(message: str) -> None:
    if not _deadlock_logger.handlers:
        _setup_deadlock_logging()
    _deadlock_logger.debug(message)


def _normalize_rel_path(path: Path) -> str:
    return path.as_posix()


def detect_language(file_path: Path, extension_map: dict[str, str]) -> str:
    return str(extension_map.get(file_path.suffix.lower(), "")).strip()


def _match_any(rel_path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel_path, pattern) for pattern in patterns)


def _normalize_scope_mode(value: Any) -> str:
    token = str(value or "auto").strip().lower()
    if token in {"auto", "configured", "git", "git_tracked", "all"}:
        return "git" if token == "git_tracked" else token
    return "auto"


def _normalize_patterns(value: Any, fallback: list[str]) -> list[str]:
    if isinstance(value, list):
        normalized = [str(item).strip() for item in value if str(item).strip()]
        return normalized if normalized else list(fallback)
    text = str(value or "").strip()
    if not text:
        return list(fallback)
    return [text]


def _is_legacy_default_include(includes: list[str]) -> bool:
    lowered = [item.strip().lower() for item in includes if str(item).strip()]
    return sorted(lowered) == sorted(LEGACY_DEFAULT_INDEX_INCLUDE)


def _merge_exclude_patterns(excludes: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in list(excludes) + list(DEFAULT_INDEX_EXCLUDES):
        key = str(item).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(key)
    return merged


def _collect_git_candidate_files(
    project_dir: Path,
    *,
    max_candidates: int,
    timeout_seconds: int,
) -> list[Path]:
    if max_candidates <= 0:
        return []
    git_bin = shutil.which("git")
    if git_bin is None:
        return []
    try:
        proc = subprocess.run(
            [
                git_bin,
                "-C",
                str(project_dir),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            timeout=max(1, int(timeout_seconds)),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if int(proc.returncode) != 0:
        return []
    payload = bytes(proc.stdout or b"")
    if not payload:
        return []
    candidates: list[Path] = []
    for raw_item in payload.split(b"\x00"):
        if not raw_item:
            continue
        rel_text = raw_item.decode("utf-8", errors="replace").strip()
        if not rel_text:
            continue
        candidate = (project_dir / rel_text).resolve()
        if not candidate.exists() or not candidate.is_file():
            continue
        candidates.append(candidate)
        if len(candidates) >= max_candidates:
            break
    return candidates


def _filter_source_candidates(
    *,
    project_dir: Path,
    candidates: list[Path],
    includes: list[str],
    excludes: list[str],
    max_size: int,
    extension_map: dict[str, str],
) -> list[Path]:
    files: list[Path] = []
    for candidate in candidates:
        if detect_language(candidate, extension_map) == "":
            continue
        try:
            rel_path = _normalize_rel_path(candidate.relative_to(project_dir))
        except ValueError:
            continue
        if includes and not _match_any(rel_path, includes):
            continue
        if excludes and _match_any(rel_path, excludes):
            continue
        try:
            if candidate.stat().st_size > max_size:
                continue
        except OSError:
            continue
        files.append(candidate)
    return files


def collect_source_files(
    project_dir: Path,
    config: dict[str, Any],
    *,
    _auto_include_retry_depth: int = 0,
) -> list[Path]:
    index_cfg = config.get("index", {})
    includes = _normalize_patterns(index_cfg.get("include", ["**/*"]), ["**/*"])
    scope_mode = _normalize_scope_mode(index_cfg.get("scope_mode", "auto"))
    if scope_mode == "auto" and _is_legacy_default_include(includes):
        includes = ["**/*"]
    excludes = _merge_exclude_patterns(_normalize_patterns(index_cfg.get("exclude", []), []))
    max_file_mb = int(index_cfg.get("max_file_mb", 2))
    max_size = max_file_mb * 1024 * 1024
    max_files_to_scan = int(index_cfg.get("max_files_to_scan", 10000))
    extension_map = resolve_extension_language_map(config)

    files: list[Path] = []
    files_scanned = 0
    start_time = time.perf_counter()
    scan_timeout_seconds = int(index_cfg.get("scan_timeout_seconds", 60))

    if scope_mode in {"auto", "git"}:
        git_candidates = _collect_git_candidate_files(
            project_dir,
            max_candidates=max_files_to_scan,
            timeout_seconds=scan_timeout_seconds,
        )
        if git_candidates:
            filtered = _filter_source_candidates(
                project_dir=project_dir,
                candidates=git_candidates,
                includes=includes,
                excludes=excludes,
                max_size=max_size,
                extension_map=extension_map,
            )
            if not filtered and scope_mode == "auto" and includes != ["**/*"]:
                # In auto mode, never let a project-specific include pattern collapse scope to zero.
                filtered = _filter_source_candidates(
                    project_dir=project_dir,
                    candidates=git_candidates,
                    includes=["**/*"],
                    excludes=excludes,
                    max_size=max_size,
                    extension_map=extension_map,
                )
            elapsed = time.perf_counter() - start_time
            _log_deadlock_info(
                f"collect_source_files used git scope ({scope_mode}); "
                f"candidates={len(git_candidates)} selected={len(filtered)} elapsed={elapsed:.1f}s"
            )
            return sorted(
                filtered,
                key=lambda item: _normalize_rel_path(item.relative_to(project_dir)),
            )
        if scope_mode == "git":
            _log_deadlock_info("collect_source_files scope_mode=git returned empty result (no git candidates)")
            return []

    try:
        for path in project_dir.rglob("*"):
            # Timeout protection
            if time.perf_counter() - start_time > scan_timeout_seconds:
                _log_deadlock_info(
                    f"File scan timeout after {scan_timeout_seconds}s, stopping at {files_scanned} files"
                )
                break

            # File count protection
            if files_scanned >= max_files_to_scan:
                _log_deadlock_info(f"File scan limit reached ({max_files_to_scan}), stopping scan")
                break

            files_scanned += 1

            if not path.is_file():
                continue
            if detect_language(path, extension_map) == "":
                continue
            rel_path = _normalize_rel_path(path.relative_to(project_dir))
            if includes and not _match_any(rel_path, includes):
                continue
            if excludes and _match_any(rel_path, excludes):
                continue
            try:
                if path.stat().st_size > max_size:
                    continue
            except OSError:
                # Skip files we can't stat
                continue
            files.append(path)

            # Progress logging for large scans
            if files_scanned % 1000 == 0:
                elapsed = time.perf_counter() - start_time
                _log_deadlock_info(
                    f"Scanned {files_scanned} files, found {len(files)} matching files in {elapsed:.1f}s"
                )

    except (RuntimeError, ValueError) as exc:
        _log_deadlock_info(f"Error during file scan: {exc}")
        # Return whatever we found so far
        pass

    elapsed = time.perf_counter() - start_time
    _log_deadlock_info(
        f"File scan completed: {files_scanned} files scanned, {len(files)} files selected in {elapsed:.1f}s"
    )
    if not files and scope_mode == "auto" and includes != ["**/*"]:
        if _auto_include_retry_depth >= AUTO_INCLUDE_FALLBACK_MAX_DEPTH:
            _log_deadlock_info("Auto include fallback retry depth exceeded; returning empty result")
            return []
        _log_deadlock_info(
            "File scan selected zero files under configured include; retrying with include='**/*' for auto mode"
        )
        return collect_source_files(
            project_dir,
            {
                **config,
                "index": {
                    **index_cfg,
                    "include": ["**/*"],
                },
            },
            _auto_include_retry_depth=_auto_include_retry_depth + 1,
        )

    return sorted(files, key=lambda item: _normalize_rel_path(item.relative_to(project_dir)))
