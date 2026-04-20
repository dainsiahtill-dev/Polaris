from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from concurrent.futures import (
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    TimeoutError,
    as_completed,
)
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...db.repositories.accel_state_db import (
    FileState,
    compute_hash,
    delete_paths,
    load_state,
    upsert_state,
)
from ..polaris_paths import default_accel_runtime_home
from ..language_profiles import resolve_extension_language_map
from ..storage.cache import ensure_project_dirs, project_paths, write_json
from ..storage.index_cache import (
    append_delta_ops,
    base_path_for_kind,
    clear_delta_file,
    count_jsonl_lines,
    delta_path_for_base,
    flatten_grouped_rows,
    load_grouped_rows_with_delta,
    write_jsonl_atomic,
)
from ..utils import utc_now_iso as _utc_now
from .deps import extract_dependencies
from .discovery import collect_source_files
from .references import extract_references
from .symbols import extract_symbols, normalize_syntax_provider
from .tests_ownership import build_test_ownership

INDEX_KEY_FIELDS = {
    "symbols": "file",
    "references": "file",
    "dependencies": "edge_from",
}
INDEX_PARALLEL_BACKENDS = {"auto", "process", "thread"}
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

# Setup logging for deadlock detection
_deadlock_logger = logging.getLogger("accel_deadlock_detection")
_deadlock_logger.setLevel(logging.DEBUG)

IndexProgressCallback = Callable[[dict[str, Any]], None]


def _setup_deadlock_logging() -> None:
    """Setup deadlock detection logging if not already configured."""
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
    """Log deadlock detection information."""
    if not _deadlock_logger.handlers:
        _setup_deadlock_logging()
    _deadlock_logger.debug(message)


def _emit_index_progress(
    progress_callback: IndexProgressCallback | None,
    *,
    stage: str,
    processed_files: int,
    total_files: int,
    current_path: str = "",
    changed_files: int = 0,
    message: str = "",
) -> None:
    if progress_callback is None:
        return
    total_value = max(0, int(total_files))
    processed_value = max(0, int(processed_files))
    if total_value > 0:
        processed_value = min(processed_value, total_value)
    payload: dict[str, Any] = {
        "stage": str(stage or "indexing"),
        "processed_files": processed_value,
        "total_files": total_value,
        "changed_files": max(0, int(changed_files)),
    }
    if total_value > 0:
        payload["progress_pct"] = round((processed_value / total_value) * 100.0, 2)
    if current_path:
        payload["current_path"] = str(current_path)
    if message:
        payload["message"] = str(message)
    try:
        progress_callback(payload)
    except (TypeError, ValueError):
        # Progress callbacks may be partial or user-supplied; ignore gracefully
        return


def _normalize_rel_path(path: Path) -> str:
    return path.as_posix()


def detect_language(file_path: Path, extension_map: dict[str, str]) -> str:
    return str(extension_map.get(file_path.suffix.lower(), "")).strip()


def _match_any(rel_path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel_path, pattern) for pattern in patterns)


def _default_index_workers() -> int:
    cpu_total = os.cpu_count() or 1
    return max(1, min(96, cpu_total))


def _resolve_index_workers(config: dict[str, Any]) -> int:
    runtime_cfg = config.get("runtime", {})
    value = runtime_cfg.get("index_workers", _default_index_workers())
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = _default_index_workers()
    parsed = max(1, parsed)
    cpu_total = os.cpu_count() or parsed
    return min(parsed, cpu_total)


def _resolve_index_parallel_backend(config: dict[str, Any]) -> str:
    runtime_cfg = config.get("runtime", {})
    raw_value = str(runtime_cfg.get("index_parallel_backend", "auto")).strip().lower()
    backend = raw_value if raw_value in INDEX_PARALLEL_BACKENDS else "auto"
    if backend == "auto":
        return "thread" if os.name == "nt" else "process"
    return backend


def _resolve_compact_threshold(config: dict[str, Any]) -> int:
    runtime_cfg = config.get("runtime", {})
    value = runtime_cfg.get("index_delta_compact_every", 200)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 200
    return max(1, parsed)


def _resolve_syntax_runtime_cfg(config: dict[str, Any]) -> dict[str, Any]:
    runtime_cfg = dict(config.get("runtime", {}))
    return {
        "syntax_parser_enabled": bool(runtime_cfg.get("syntax_parser_enabled", False)),
        "syntax_parser_provider": normalize_syntax_provider(
            runtime_cfg.get("syntax_parser_provider", "off"),
            default_value="off",
        ),
    }


def _unit_path(index_units_dir: Path, rel_path: str) -> Path:
    digest = hashlib.sha256(rel_path.encode("utf-8")).hexdigest()[:24]
    safe_name = rel_path.replace("/", "_").replace("\\", "_")
    return index_units_dir / f"{digest}_{safe_name}.json"


def _write_unit(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_units(index_units_dir: Path) -> list[dict[str, Any]]:
    if not index_units_dir.exists():
        return []
    units: list[dict[str, Any]] = []
    for file_path in sorted(index_units_dir.glob("*.json")):
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            units.append(payload)
    return units


def _base_indexes_exist(index_dir: Path) -> bool:
    return all(base_path_for_kind(index_dir, kind).exists() for kind in INDEX_KEY_FIELDS)


def _process_file_for_index(
    job: tuple[str, str, str, dict[str, Any]],
) -> dict[str, Any]:
    abs_path_str, rel_path, lang, syntax_runtime_cfg = job
    file_path = Path(abs_path_str)
    try:
        symbols = extract_symbols(
            file_path,
            rel_path,
            lang,
            runtime_cfg=syntax_runtime_cfg,
        )
        dependencies = extract_dependencies(file_path, rel_path, lang)
        references = extract_references(file_path, rel_path, lang)
    except (OSError, ValueError, TypeError):
        # File read or parsing errors should not fail the entire batch
        symbols = []
        dependencies = []
        references = []
    return {
        "file": rel_path,
        "lang": lang,
        "symbols": symbols,
        "dependencies": dependencies,
        "references": references,
    }


def _build_payloads_for_changed(
    changed_paths: list[str],
    current_paths: dict[str, Path],
    current_states: dict[str, FileState],
    index_workers: int,
    index_parallel_backend: str,
    syntax_runtime_cfg: dict[str, Any],
    progress_callback: IndexProgressCallback | None = None,
) -> dict[str, dict[str, Any]]:
    if not changed_paths:
        return {}

    jobs = [
        (
            str(current_paths[rel_path]),
            rel_path,
            current_states[rel_path].lang,
            dict(syntax_runtime_cfg),
        )
        for rel_path in changed_paths
    ]

    _log_deadlock_info(
        f"Starting parallel processing: {len(jobs)} jobs, {index_workers} workers, {index_parallel_backend} backend"
    )
    total_jobs = len(jobs)
    completed_jobs = 0

    def _progress(file_path: str = "") -> None:
        nonlocal completed_jobs
        completed_jobs += 1
        if completed_jobs in (1, total_jobs) or completed_jobs % 10 == 0:
            _emit_index_progress(
                progress_callback,
                stage="index_payloads",
                processed_files=completed_jobs,
                total_files=total_jobs,
                current_path=file_path,
                changed_files=total_jobs,
                message="Building symbol/reference/dependency payloads",
            )

    if len(jobs) <= 1 or index_workers <= 1:
        _log_deadlock_info("Using sequential processing (single job or single worker)")
        results = []
        for job in jobs:
            result = _process_file_for_index(job)
            results.append(result)
            _progress(str(result.get("file", job[1])))
    else:
        workers = min(index_workers, len(jobs))
        _log_deadlock_info(f"Using parallel processing: {workers} workers")

        start_time = time.perf_counter()
        timeout_per_file = 60  # 60 seconds per file
        overall_timeout = timeout_per_file * len(jobs) * 2  # generous buffer

        try:
            if index_parallel_backend == "thread":
                _log_deadlock_info("Using ThreadPoolExecutor")
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    if hasattr(pool, "submit"):
                        future_map = {pool.submit(_process_file_for_index, job): job for job in jobs}

                        results = []
                        for future in as_completed(future_map, timeout=overall_timeout):
                            job = future_map[future]
                            try:
                                result = future.result(timeout=timeout_per_file)
                                results.append(result)
                                _log_deadlock_info(f"Completed job: {job[1]}")
                                _progress(str(result.get("file", job[1])))
                            except TimeoutError:
                                _log_deadlock_info(f"Timeout for job: {job[1]}")
                                # Add a failure result for timed out jobs
                                results.append(
                                    {
                                        "file": job[1],
                                        "lang": job[2],
                                        "symbols": [],
                                        "dependencies": [],
                                        "references": [],
                                        "error": "timeout",
                                    }
                                )
                                _progress(job[1])
                            except (RuntimeError, ValueError) as exc:
                                _log_deadlock_info(f"Error for job {job[1]}: {exc!r}")
                                results.append(
                                    {
                                        "file": job[1],
                                        "lang": job[2],
                                        "symbols": [],
                                        "dependencies": [],
                                        "references": [],
                                        "error": str(exc),
                                    }
                                )
                                _progress(job[1])
                    else:
                        # Compatibility path for lightweight test doubles exposing only map().
                        _log_deadlock_info("Executor missing submit(), falling back to map()")
                        results = []
                        for job, result in zip(jobs, pool.map(_process_file_for_index, jobs), strict=False):
                            results.append(result)
                            _log_deadlock_info(f"Completed job: {job[1]}")
                            _progress(str(result.get("file", job[1])))
            else:
                _log_deadlock_info("Using ProcessPoolExecutor")
                with ProcessPoolExecutor(max_workers=workers) as process_pool:
                    if hasattr(process_pool, "submit"):
                        future_map = {process_pool.submit(_process_file_for_index, job): job for job in jobs}

                        results = []
                        for future in as_completed(future_map, timeout=overall_timeout):
                            job = future_map[future]
                            try:
                                result = future.result(timeout=timeout_per_file)
                                results.append(result)
                                _log_deadlock_info(f"Completed job: {job[1]}")
                                _progress(str(result.get("file", job[1])))
                            except TimeoutError:
                                _log_deadlock_info(f"Timeout for job: {job[1]}")
                                results.append(
                                    {
                                        "file": job[1],
                                        "lang": job[2],
                                        "symbols": [],
                                        "dependencies": [],
                                        "references": [],
                                        "error": "timeout",
                                    }
                                )
                                _progress(job[1])
                            except (RuntimeError, ValueError) as exc:
                                _log_deadlock_info(f"Error for job {job[1]}: {exc!r}")
                                results.append(
                                    {
                                        "file": job[1],
                                        "lang": job[2],
                                        "symbols": [],
                                        "dependencies": [],
                                        "references": [],
                                        "error": str(exc),
                                    }
                                )
                                _progress(job[1])
                    else:
                        # Compatibility path for lightweight test doubles exposing only map().
                        _log_deadlock_info("Executor missing submit(), falling back to map()")
                        results = []
                        for job, result in zip(
                            jobs,
                            process_pool.map(_process_file_for_index, jobs),
                            strict=False,
                        ):
                            results.append(result)
                            _log_deadlock_info(f"Completed job: {job[1]}")
                            _progress(str(result.get("file", job[1])))

        except TimeoutError:
            _log_deadlock_info(f"Overall timeout after {overall_timeout}s, falling back to sequential")
            # Fallback to sequential processing
            results = []
            for job in jobs:
                try:
                    result = _process_file_for_index(job)
                    results.append(result)
                    _progress(str(result.get("file", job[1])))
                except (RuntimeError, ValueError) as exc:
                    _log_deadlock_info(f"Sequential processing error for {job[1]}: {exc!r}")
                    results.append(
                        {
                            "file": job[1],
                            "lang": job[2],
                            "symbols": [],
                            "dependencies": [],
                            "references": [],
                            "error": str(exc),
                        }
                    )
                    _progress(job[1])
        except (RuntimeError, ValueError) as exc:
            _log_deadlock_info(f"Parallel execution failed: {exc!r}, falling back to sequential")
            # Fallback to sequential processing
            results = []
            for job in jobs:
                try:
                    result = _process_file_for_index(job)
                    results.append(result)
                    _progress(str(result.get("file", job[1])))
                except (RuntimeError, ValueError) as exc:
                    _log_deadlock_info(f"Sequential processing error for {job[1]}: {exc!r}")
                    results.append(
                        {
                            "file": job[1],
                            "lang": job[2],
                            "symbols": [],
                            "dependencies": [],
                            "references": [],
                            "error": str(exc),
                        }
                    )
                    _progress(job[1])

        elapsed = time.perf_counter() - start_time
        _log_deadlock_info(f"Parallel processing completed in {elapsed:.3f}s")

    return {str(item.get("file", "")): item for item in results if str(item.get("file", ""))}


def _collect_grouped_maps_from_units(
    index_units_dir: Path,
    allowed_paths: set[str],
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
]:
    units = _read_units(index_units_dir)

    symbols_grouped: dict[str, list[dict[str, Any]]] = {}
    references_grouped: dict[str, list[dict[str, Any]]] = {}
    dependencies_grouped: dict[str, list[dict[str, Any]]] = {}

    for unit in units:
        file_path = str(unit.get("file", ""))
        if not file_path or file_path not in allowed_paths:
            continue
        symbols_grouped[file_path] = list(unit.get("symbols", []))
        references_grouped[file_path] = list(unit.get("references", []))
        dependencies_grouped[file_path] = list(unit.get("dependencies", []))

    return symbols_grouped, references_grouped, dependencies_grouped


def _build_manifest_from_previous(
    paths: dict[str, Path],
    config: dict[str, Any],
    mode: str,
    current_files: list[str],
    index_parallel_backend: str,
) -> dict[str, Any] | None:
    manifest_path = paths["index"] / "manifest.json"
    if not manifest_path.exists():
        return None

    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    pending_delta_ops = {
        kind: count_jsonl_lines(delta_path_for_base(base_path_for_kind(paths["index"], kind)))
        for kind in INDEX_KEY_FIELDS
    }
    index_workers = _resolve_index_workers(config)
    compact_threshold = _resolve_compact_threshold(config)

    return {
        "version": 1,
        "schema_version": 1,
        "indexed_at": _utc_now(),
        "mode": mode,
        "full_rebuild": False,
        "project_dir": previous.get("project_dir", ""),
        "indexed_files": sorted(current_files),
        "changed_files": [],
        "removed_files": [],
        "counts": previous.get("counts", {}),
        "performance": {
            "index_workers": index_workers,
            "parallel_backend": index_parallel_backend,
            "parallelized": False,
            "processed_files": 0,
        },
        "delta": {
            "pending_ops": pending_delta_ops,
            "compacted": False,
            "compact_threshold": compact_threshold,
        },
    }


def build_or_update_indexes(
    project_dir: Path,
    config: dict[str, Any],
    mode: str = "build",
    full: bool = False,
    progress_callback: IndexProgressCallback | None = None,
) -> dict[str, Any]:
    accel_home = Path(config["runtime"]["accel_home"]).resolve()
    paths = project_paths(accel_home, project_dir)
    ensure_project_dirs(paths)

    state_db_path = paths["state"] / "index_state.db"
    previous_state = load_state(state_db_path)
    source_files = collect_source_files(project_dir, config)
    extension_map = resolve_extension_language_map(config)
    current_paths = {_normalize_rel_path(path.relative_to(project_dir)): path for path in source_files}
    removed_paths = sorted(set(previous_state.keys()) - set(current_paths.keys()))

    current_states: dict[str, FileState] = {}
    changed_paths: list[str] = []
    total_scan_files = len(current_paths)
    _emit_index_progress(
        progress_callback,
        stage="scan_files",
        processed_files=0,
        total_files=total_scan_files,
        changed_files=0,
        message="Scanning workspace files for index deltas",
    )

    for scan_index, (rel_path, abs_path) in enumerate(current_paths.items(), start=1):
        stat = abs_path.stat()
        lang = detect_language(abs_path, extension_map)
        old = previous_state.get(rel_path)
        is_changed = full or old is None or old.mtime_ns != int(stat.st_mtime_ns) or old.size != int(stat.st_size)
        if is_changed:
            content_hash = compute_hash(abs_path)
            changed_paths.append(rel_path)
        else:
            assert old is not None
            content_hash = old.content_hash

        current_states[rel_path] = FileState(
            path=rel_path,
            mtime_ns=int(stat.st_mtime_ns),
            size=int(stat.st_size),
            content_hash=content_hash,
            lang=lang,
        )
        if scan_index in (1, total_scan_files) or scan_index % 25 == 0:
            _emit_index_progress(
                progress_callback,
                stage="scan_files",
                processed_files=scan_index,
                total_files=total_scan_files,
                current_path=rel_path,
                changed_files=len(changed_paths),
                message="Scanning workspace files for index deltas",
            )

    for rel_path in removed_paths:
        unit_path = _unit_path(paths["index_units"], rel_path)
        if unit_path.exists():
            unit_path.unlink()

    index_workers = _resolve_index_workers(config)
    index_parallel_backend = _resolve_index_parallel_backend(config)
    compact_threshold = _resolve_compact_threshold(config)
    syntax_runtime_cfg = _resolve_syntax_runtime_cfg(config)

    if not full and not changed_paths and not removed_paths:
        manifest = _build_manifest_from_previous(
            paths=paths,
            config=config,
            mode=mode,
            current_files=sorted(current_paths.keys()),
            index_parallel_backend=index_parallel_backend,
        )
        if manifest is not None:
            write_json(paths["index"] / "manifest.json", manifest)
            return manifest

    changed_payloads = _build_payloads_for_changed(
        changed_paths=changed_paths,
        current_paths=current_paths,
        current_states=current_states,
        index_workers=index_workers,
        index_parallel_backend=index_parallel_backend,
        syntax_runtime_cfg=syntax_runtime_cfg,
        progress_callback=progress_callback,
    )

    for rel_path, payload in changed_payloads.items():
        _write_unit(_unit_path(paths["index_units"], rel_path), payload)

    allowed_paths = set(current_paths.keys())
    full_rebuild = bool(full) or not _base_indexes_exist(paths["index"])

    symbols_grouped: dict[str, list[dict[str, Any]]] = {}
    references_grouped: dict[str, list[dict[str, Any]]] = {}
    dependencies_grouped: dict[str, list[dict[str, Any]]] = {}
    pending_delta_ops = dict.fromkeys(INDEX_KEY_FIELDS, 0)
    compacted = False

    if full_rebuild:
        if full:
            symbols_grouped = {
                rel_path: list(payload.get("symbols", []))
                for rel_path, payload in changed_payloads.items()
                if rel_path in allowed_paths
            }
            references_grouped = {
                rel_path: list(payload.get("references", []))
                for rel_path, payload in changed_payloads.items()
                if rel_path in allowed_paths
            }
            dependencies_grouped = {
                rel_path: list(payload.get("dependencies", []))
                for rel_path, payload in changed_payloads.items()
                if rel_path in allowed_paths
            }
        else:
            (
                symbols_grouped,
                references_grouped,
                dependencies_grouped,
            ) = _collect_grouped_maps_from_units(paths["index_units"], allowed_paths)

        write_jsonl_atomic(
            base_path_for_kind(paths["index"], "symbols"),
            flatten_grouped_rows(symbols_grouped),
        )
        write_jsonl_atomic(
            base_path_for_kind(paths["index"], "references"),
            flatten_grouped_rows(references_grouped),
        )
        write_jsonl_atomic(
            base_path_for_kind(paths["index"], "dependencies"),
            flatten_grouped_rows(dependencies_grouped),
        )
        for kind in INDEX_KEY_FIELDS:
            clear_delta_file(paths["index"], kind)
        compacted = True
    else:
        symbols_grouped, symbols_delta_count = load_grouped_rows_with_delta(
            index_dir=paths["index"],
            kind="symbols",
            key_field=INDEX_KEY_FIELDS["symbols"],
        )
        references_grouped, references_delta_count = load_grouped_rows_with_delta(
            index_dir=paths["index"],
            kind="references",
            key_field=INDEX_KEY_FIELDS["references"],
        )
        dependencies_grouped, dependencies_delta_count = load_grouped_rows_with_delta(
            index_dir=paths["index"],
            kind="dependencies",
            key_field=INDEX_KEY_FIELDS["dependencies"],
        )

        for rel_path in list(symbols_grouped.keys()):
            if rel_path not in allowed_paths:
                symbols_grouped.pop(rel_path, None)
        for rel_path in list(references_grouped.keys()):
            if rel_path not in allowed_paths:
                references_grouped.pop(rel_path, None)
        for rel_path in list(dependencies_grouped.keys()):
            if rel_path not in allowed_paths:
                dependencies_grouped.pop(rel_path, None)

        symbol_ops: list[dict[str, Any]] = []
        reference_ops: list[dict[str, Any]] = []
        dependency_ops: list[dict[str, Any]] = []

        for rel_path in removed_paths:
            if rel_path in symbols_grouped:
                symbols_grouped.pop(rel_path, None)
                symbol_ops.append({"op": "delete", "key": rel_path})
            if rel_path in references_grouped:
                references_grouped.pop(rel_path, None)
                reference_ops.append({"op": "delete", "key": rel_path})
            if rel_path in dependencies_grouped:
                dependencies_grouped.pop(rel_path, None)
                dependency_ops.append({"op": "delete", "key": rel_path})

        for rel_path, payload in changed_payloads.items():
            symbols_rows = list(payload.get("symbols", []))
            references_rows = list(payload.get("references", []))
            dependencies_rows = list(payload.get("dependencies", []))

            symbols_grouped[rel_path] = symbols_rows
            references_grouped[rel_path] = references_rows
            dependencies_grouped[rel_path] = dependencies_rows

            symbol_ops.append({"op": "set", "key": rel_path, "rows": symbols_rows})
            reference_ops.append({"op": "set", "key": rel_path, "rows": references_rows})
            dependency_ops.append({"op": "set", "key": rel_path, "rows": dependencies_rows})

        symbols_added = append_delta_ops(paths["index"], "symbols", symbol_ops)
        references_added = append_delta_ops(paths["index"], "references", reference_ops)
        dependencies_added = append_delta_ops(paths["index"], "dependencies", dependency_ops)

        pending_delta_ops = {
            "symbols": int(symbols_delta_count) + int(symbols_added),
            "references": int(references_delta_count) + int(references_added),
            "dependencies": int(dependencies_delta_count) + int(dependencies_added),
        }

        if max(pending_delta_ops.values()) >= compact_threshold:
            write_jsonl_atomic(
                base_path_for_kind(paths["index"], "symbols"),
                flatten_grouped_rows(symbols_grouped),
            )
            write_jsonl_atomic(
                base_path_for_kind(paths["index"], "references"),
                flatten_grouped_rows(references_grouped),
            )
            write_jsonl_atomic(
                base_path_for_kind(paths["index"], "dependencies"),
                flatten_grouped_rows(dependencies_grouped),
            )
            for kind in INDEX_KEY_FIELDS:
                clear_delta_file(paths["index"], kind)
            pending_delta_ops = dict.fromkeys(INDEX_KEY_FIELDS, 0)
            compacted = True

    symbols_index = flatten_grouped_rows(symbols_grouped)
    references_index = flatten_grouped_rows(references_grouped)
    dependencies_index = flatten_grouped_rows(dependencies_grouped)

    indexed_files = sorted(current_paths.keys())
    source_bytes_total = int(sum(int(state.size) for state in current_states.values()))
    test_ownership = build_test_ownership(indexed_files, dependencies_index)
    write_jsonl_atomic(base_path_for_kind(paths["index"], "test_ownership"), test_ownership)

    now = _utc_now()
    upsert_state(state_db_path, current_states.values(), updated_utc=now)
    delete_paths(state_db_path, removed_paths)

    manifest = {
        "version": 1,
        "schema_version": 1,
        "indexed_at": now,
        "mode": mode,
        "full_rebuild": bool(full_rebuild),
        "project_dir": str(project_dir.resolve()),
        "indexed_files": indexed_files,
        "changed_files": sorted(changed_paths),
        "removed_files": removed_paths,
        "counts": {
            "symbols": len(symbols_index),
            "references": len(references_index),
            "dependencies": len(dependencies_index),
            "test_ownership": len(test_ownership),
            "files": len(indexed_files),
            "source_bytes": source_bytes_total,
            "source_chars_est": source_bytes_total,
        },
        "performance": {
            "index_workers": index_workers,
            "parallel_backend": index_parallel_backend,
            "parallelized": bool(len(changed_paths) > 1 and index_workers > 1),
            "processed_files": len(changed_paths),
        },
        "delta": {
            "pending_ops": pending_delta_ops,
            "compacted": compacted,
            "compact_threshold": compact_threshold,
        },
    }
    _emit_index_progress(
        progress_callback,
        stage="finalize_manifest",
        processed_files=len(indexed_files),
        total_files=len(indexed_files),
        changed_files=len(changed_paths),
        current_path="",
        message="Writing index manifest and finishing",
    )
    write_json(paths["index"] / "manifest.json", manifest)
    _emit_index_progress(
        progress_callback,
        stage="completed",
        processed_files=len(indexed_files),
        total_files=len(indexed_files),
        changed_files=len(changed_paths),
        current_path="",
        message="Indexing completed",
    )
    return manifest
