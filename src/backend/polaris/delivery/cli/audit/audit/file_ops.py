"""File operations for audit quick CLI.

This module contains file reading, discovery, and collection utilities.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator


def discover_latest_runtime(base_path: str | None = None) -> Path | None:
    """自动发现最新的 runtime 目录。

    Args:
        base_path: 基础路径，若未提供则从环境变量或常见位置搜索

    Returns:
        发现的 runtime 目录路径，或 None
    """
    base = base_path or os.environ.get("POLARIS_RUNTIME_BASE")

    if not base:
        candidates = [
            Path("X:/") / "hp_stress_workspace" / "runtime",
            Path("X:/") / "tests-agent-stress-runtime" / "runtime",
            Path("C:/Temp/hp_stress_workspace") / "runtime",
            Path("C:/Temp/tests-agent-stress-backend") / "runtime",
            Path("C:/Temp/tests-agent-stress-runtime") / "runtime",
            Path.cwd() / "runtime",
        ]
    else:
        candidates = [Path(base)]

    for candidate in candidates:
        if candidate.exists():
            if (candidate / "audit").exists() or (candidate / "events").exists():
                return candidate.resolve()

            projects_dir = candidate.parent / "projects"
            if projects_dir.exists():
                project_runtimes: list[tuple[float, Path]] = []
                for project_dir in projects_dir.iterdir():
                    if project_dir.is_dir():
                        runtime_dir = project_dir / "runtime"
                        if runtime_dir.exists():
                            try:
                                mtime = runtime_dir.stat().st_mtime
                                project_runtimes.append((mtime, runtime_dir))
                            except OSError:
                                continue

                if project_runtimes:
                    project_runtimes.sort(reverse=True)
                    return project_runtimes[0][1].resolve()

            return candidate.resolve()

    return None


def get_all_runtimes(base_path: str | None = None) -> list[Path]:
    """获取所有可用的 runtime 目录。

    Args:
        base_path: 基础路径

    Returns:
        可用 runtime 目录列表
    """
    runtimes: list[Path] = []
    env_base = os.environ.get("POLARIS_RUNTIME_BASE")

    if base_path:
        runtime_path = Path(base_path)
        if runtime_path.exists():
            runtimes.append(runtime_path)
    elif env_base:
        runtime_path = Path(env_base)
        if runtime_path.exists():
            runtimes.append(runtime_path)
    else:
        candidates = [
            Path("X:/") / "hp_stress_workspace" / ".polaris" / "projects",
            Path("C:/Temp/hp_stress_workspace") / ".polaris" / "projects",
        ]

        for projects_dir in candidates:
            if projects_dir.exists():
                for project_dir in projects_dir.iterdir():
                    runtime_dir = project_dir / "runtime"
                    if runtime_dir.exists():
                        runtimes.append(runtime_dir)

    return runtimes


def count_jsonl_events(file_path: Path) -> dict[str, int]:
    """统计 JSONL 文件中的有效事件条数。

    Args:
        file_path: JSONL 文件路径

    Returns:
        包含 events, invalid_lines, read_errors 的统计字典
    """
    stats = {
        "events": 0,
        "invalid_lines": 0,
        "read_errors": 0,
    }

    try:
        with open(file_path, encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    stats["invalid_lines"] += 1
                    continue
                if isinstance(payload, dict):
                    stats["events"] += 1
                else:
                    stats["invalid_lines"] += 1
    except OSError:
        stats["read_errors"] += 1

    return stats


def tail_jsonl_events(file_path: Path, limit: int) -> dict[str, Any]:
    """读取 JSONL 文件尾部事件，同时给出总计统计。

    Args:
        file_path: JSONL 文件路径
        limit: 尾部事件数量限制

    Returns:
        包含 events, total_events, invalid_lines, read_errors 的字典
    """
    bounded_limit = max(1, int(limit))
    tail_events: deque[dict[str, Any]] = deque(maxlen=bounded_limit)
    stats: dict[str, Any] = {
        "events": [],
        "total_events": 0,
        "invalid_lines": 0,
        "read_errors": 0,
    }

    try:
        with open(file_path, encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    stats["invalid_lines"] += 1
                    continue
                if isinstance(payload, dict):
                    stats["total_events"] += 1
                    tail_events.append(payload)
                else:
                    stats["invalid_lines"] += 1
    except OSError:
        stats["read_errors"] += 1

    stats["events"] = list(tail_events)
    return stats


def collect_runtime_event_inventory(runtime_root: Path) -> dict[str, Any]:
    """收集 runtime 下所有可识别事件文件与统计。

    Args:
        runtime_root: runtime 根目录

    Returns:
        包含所有事件文件统计的字典
    """
    resolved_root = runtime_root.resolve()
    source_files: dict[str, list[Path]] = {
        "audit": [],
        "runtime": [],
        "role": [],
        "journal": [],
        "strategy_receipts": [],
    }

    audit_dir = resolved_root / "audit"
    if audit_dir.exists():
        source_files["audit"] = sorted(
            [p for p in audit_dir.glob("audit-*.jsonl") if p.is_file()],
            key=str,
        )

    events_dir = resolved_root / "events"
    if events_dir.exists():
        source_files["runtime"] = sorted(
            [p for p in events_dir.glob("*.jsonl") if p.is_file()],
            key=str,
        )

    roles_dir = resolved_root / "roles"
    if roles_dir.exists():
        role_files: list[Path] = []
        for role_dir in sorted([p for p in roles_dir.iterdir() if p.is_dir()], key=lambda p: p.name):
            logs_dir = role_dir / "logs"
            if not logs_dir.exists():
                continue
            role_files.extend([p for p in logs_dir.glob("events_*.jsonl") if p.is_file()])
        source_files["role"] = sorted(role_files, key=str)

    runs_root = resolved_root / "runs"
    if runs_root.is_dir():
        journal_files: list[Path] = []
        for run_dir in runs_root.iterdir():
            if not run_dir.is_dir():
                continue
            logs_dir = run_dir / "logs"
            if not logs_dir.is_dir():
                continue
            candidates = [
                logs_dir / "journal.norm.jsonl",
                logs_dir / "journal.enriched.jsonl",
                logs_dir / "journal.raw.jsonl",
            ]
            for c in candidates:
                if c.exists():
                    journal_files.append(c)
                    break
        source_files["journal"] = sorted(journal_files, key=str)

    receipts_root = resolved_root / "strategy_runs"
    if receipts_root.is_dir():
        source_files["strategy_receipts"] = sorted(
            [p for p in receipts_root.glob("*.json") if p.is_file()],
            key=str,
        )

    by_source: dict[str, dict[str, Any]] = {}
    total_events = 0
    total_invalid_lines = 0
    total_read_errors = 0
    all_event_files: list[str] = []

    for source_name, files in source_files.items():
        source_event_count = 0
        source_invalid_lines = 0
        source_read_errors = 0
        for event_file in files:
            file_stats = count_jsonl_events(event_file)
            source_event_count += int(file_stats.get("events", 0))
            source_invalid_lines += int(file_stats.get("invalid_lines", 0))
            source_read_errors += int(file_stats.get("read_errors", 0))
            all_event_files.append(str(event_file))

        by_source[source_name] = {
            "files": len(files),
            "events": source_event_count,
            "invalid_lines": source_invalid_lines,
            "read_errors": source_read_errors,
            "paths": [str(p) for p in files],
        }

        total_events += source_event_count
        total_invalid_lines += source_invalid_lines
        total_read_errors += source_read_errors

    return {
        "runtime_root": str(resolved_root),
        "total_files": sum(len(files) for files in source_files.values()),
        "all_event_files": all_event_files,
        "total_events": total_events,
        "invalid_lines": total_invalid_lines,
        "read_errors": total_read_errors,
        "by_source": by_source,
    }


def watch_events(
    runtime_root: Path,
    interval: float = 1.0,
    event_type: str | None = None,
    show_relative_time: bool = True,
) -> Generator[dict[str, Any], None, None]:
    """实时监控事件流。

    Args:
        runtime_root: runtime 根目录
        interval: 刷新间隔（秒）
        event_type: 事件类型过滤
        show_relative_time: 是否显示相对时间

    Yields:
        事件字典
    """
    from polaris.infrastructure.audit.stores.audit_store import AuditStore

    store = AuditStore(runtime_root=runtime_root)
    seen_event_ids: set[str] = set()

    while True:
        try:
            from polaris.infrastructure.audit.stores.audit_store import AuditEventType

            etype = AuditEventType(event_type) if event_type else None
            events = store.query(event_type=etype, limit=100)

            new_events = []
            for evt in events:
                if evt.event_id not in seen_event_ids:
                    seen_event_ids.add(evt.event_id)
                    new_events.append(evt)

            new_events.sort(key=lambda e: e.timestamp)
            for evt in new_events:
                yield evt.to_dict()

        except (RuntimeError, ValueError) as e:
            yield {"_error": str(e), "_timestamp": datetime.now(timezone.utc).isoformat()}

        time.sleep(interval)


def bootstrap_backend_import_path() -> None:
    """确保直接运行文件时 backend 包路径可用。"""
    if __package__:
        return
    backend_root = Path(__file__).resolve().parents[4]
    backend_root_str = str(backend_root)
    if backend_root_str not in sys.path:
        sys.path.insert(0, backend_root_str)
