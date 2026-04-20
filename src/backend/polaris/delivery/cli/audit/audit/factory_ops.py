"""Factory event operations for audit quick CLI.

This module contains functions for collecting and processing factory run events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from polaris.delivery.cli.audit.audit.file_ops import tail_jsonl_events

if TYPE_CHECKING:
    from pathlib import Path


def infer_workspace_candidates(runtime_root: Path) -> list[Path]:
    """从 runtime 路径推断 workspace 候选目录。

    Args:
        runtime_root: runtime 根目录

    Returns:
        workspace 候选目录列表
    """
    from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

    metadata_dir = get_workspace_metadata_dir_name()
    resolved = runtime_root.resolve()
    candidates: list[Path] = []
    seen: set[str] = set()

    def _append_candidate(path: Path) -> None:
        token = str(path)
        if token in seen:
            return
        seen.add(token)
        candidates.append(path)

    # 标准结构: {workspace}/<metadata_dir>/runtime
    if resolved.name == "runtime" and resolved.parent.name == metadata_dir:
        _append_candidate(resolved.parent.parent)

    # 向后兼容: 查找 legacy ".polaris" 目录
    for parent in resolved.parents:
        if parent.name in (metadata_dir, ".polaris"):
            _append_candidate(parent.parent)

    return candidates


def collect_factory_events(
    runtime_root: Path,
    *,
    run_id: str | None = None,
    limit_per_run: int = 50,
    max_runs: int = 5,
) -> dict[str, Any]:
    """收集工厂事件，缺失时返回可诊断信息而不是抛异常。

    Args:
        runtime_root: runtime 根目录
        run_id: 指定的运行 ID
        limit_per_run: 每次运行的事件限制
        max_runs: 最大运行数量

    Returns:
        包含工厂事件收集结果的字典
    """
    workspace_candidates = infer_workspace_candidates(runtime_root)
    checked_factory_dirs: list[Path] = []
    existing_factory_dirs: list[Path] = []
    seen_dirs: set[str] = set()

    for workspace in workspace_candidates:
        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        metadata_dir = get_workspace_metadata_dir_name()
        factory_dir = workspace / metadata_dir / "factory"
        token = str(factory_dir)
        if token in seen_dirs:
            continue
        seen_dirs.add(token)
        checked_factory_dirs.append(factory_dir)
        if factory_dir.exists() and factory_dir.is_dir():
            existing_factory_dirs.append(factory_dir)

    run_entries: list[tuple[float, Path, Path]] = []
    available_run_ids: set[str] = set()

    for factory_dir in existing_factory_dirs:
        try:
            children = list(factory_dir.iterdir())
        except OSError:
            continue
        for run_dir in children:
            if not run_dir.is_dir() or not run_dir.name.startswith("factory_"):
                continue
            available_run_ids.add(run_dir.name)
            try:
                mtime = run_dir.stat().st_mtime
            except OSError:
                mtime = 0.0
            run_entries.append((mtime, run_dir, factory_dir))

    if run_id:
        run_entries = [entry for entry in run_entries if entry[1].name == run_id]
        if not run_entries:
            return {
                "status": "not_found",
                "reason": "run_id_not_found",
                "workspace_candidates": [str(p) for p in workspace_candidates],
                "checked_factory_dirs": [str(p) for p in checked_factory_dirs],
                "existing_factory_dirs": [str(p) for p in existing_factory_dirs],
                "available_run_ids": sorted(available_run_ids),
                "runs": [],
                "total_events": 0,
                "total_invalid_lines": 0,
                "total_read_errors": 0,
            }

    if not run_entries:
        reason = "factory_dir_missing" if not existing_factory_dirs else "no_factory_runs"
        return {
            "status": "not_found",
            "reason": reason,
            "workspace_candidates": [str(p) for p in workspace_candidates],
            "checked_factory_dirs": [str(p) for p in checked_factory_dirs],
            "existing_factory_dirs": [str(p) for p in existing_factory_dirs],
            "available_run_ids": sorted(available_run_ids),
            "runs": [],
            "total_events": 0,
            "total_invalid_lines": 0,
            "total_read_errors": 0,
        }

    run_entries.sort(key=lambda item: item[0], reverse=True)

    runs: list[dict[str, Any]] = []
    total_events = 0
    total_invalid_lines = 0
    total_read_errors = 0
    capped_runs = run_entries[: max(1, int(max_runs))]

    for _, run_dir, factory_dir in capped_runs:
        events_file = run_dir / "events" / "events.jsonl"
        if not events_file.exists():
            runs.append(
                {
                    "run_id": run_dir.name,
                    "factory_dir": str(factory_dir),
                    "events_file": str(events_file),
                    "missing_events_file": True,
                    "events": [],
                    "total_events": 0,
                    "invalid_lines": 0,
                    "read_errors": 0,
                }
            )
            continue

        tail_stats = tail_jsonl_events(events_file, limit_per_run)
        run_total_events = int(tail_stats.get("total_events", 0))
        run_invalid_lines = int(tail_stats.get("invalid_lines", 0))
        run_read_errors = int(tail_stats.get("read_errors", 0))

        total_events += run_total_events
        total_invalid_lines += run_invalid_lines
        total_read_errors += run_read_errors

        runs.append(
            {
                "run_id": run_dir.name,
                "factory_dir": str(factory_dir),
                "events_file": str(events_file),
                "missing_events_file": False,
                "events": list(tail_stats.get("events", [])),
                "total_events": run_total_events,
                "invalid_lines": run_invalid_lines,
                "read_errors": run_read_errors,
            }
        )

    has_event_file = any(not run.get("missing_events_file", False) for run in runs)
    if not has_event_file:
        return {
            "status": "not_found",
            "reason": "events_file_missing",
            "workspace_candidates": [str(p) for p in workspace_candidates],
            "checked_factory_dirs": [str(p) for p in checked_factory_dirs],
            "existing_factory_dirs": [str(p) for p in existing_factory_dirs],
            "available_run_ids": sorted(available_run_ids),
            "runs": runs,
            "total_events": 0,
            "total_invalid_lines": 0,
            "total_read_errors": 0,
        }

    return {
        "status": "ok",
        "reason": "ok",
        "workspace_candidates": [str(p) for p in workspace_candidates],
        "checked_factory_dirs": [str(p) for p in checked_factory_dirs],
        "existing_factory_dirs": [str(p) for p in existing_factory_dirs],
        "available_run_ids": sorted(available_run_ids),
        "runs": runs,
        "total_events": total_events,
        "total_invalid_lines": total_invalid_lines,
        "total_read_errors": total_read_errors,
    }
