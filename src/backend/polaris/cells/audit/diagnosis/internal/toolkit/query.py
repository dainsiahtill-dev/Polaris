"""Query functions for audit events.

CRITICAL: 所有文本文件 I/O 必须使用 UTF-8 编码。
"""

import json
import logging
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.cells.audit.diagnosis.internal.usecases import AuditUseCaseFacade
from polaris.kernelone.audit import KernelAuditEventType
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.fs.text_ops import open_text_log_append

logger = logging.getLogger(__name__)


def _safe_parse_timestamp(ts: str) -> datetime | None:
    """安全解析时间戳，处理多种格式和坏值。

    Args:
        ts: 时间戳字符串

    Returns:
        datetime 对象或 None
    """
    if not ts:
        return None

    try:
        # 处理 ISO8601 格式
        ts_normalized = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_normalized)
    except (ValueError, TypeError):
        try:
            # 尝试解析时间戳
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except (ValueError, TypeError):
            return None


def _record_corruption(
    audit_dir: Path,
    file_path: str,
    line_num: int,
    error_type: str,
    error_message: str,
) -> None:
    """记录查询过程中发现的损坏。"""
    corruption_file = audit_dir / "corruption.events.jsonl"
    record = {
        "schema_version": "2.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "file_path": file_path,
        "offset": line_num,
        "error_type": error_type,
        "error_message": error_message[:500],
        "line_preview": "",
        "recovered": True,
        "source_op": "query_events",
    }

    try:
        with open_text_log_append(str(corruption_file)) as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (RuntimeError, ValueError) as e:
        logger.warning(f"Failed to record corruption: {e}")


def query_events(
    runtime_root: str,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    event_type: str | None = None,
    role: str | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Query audit events with filters.

    增强坏行与坏时间戳容错，错误写入 corruption 账本，不中断整批查询。

    Args:
        runtime_root: Runtime 根目录
        start_time: 开始时间
        end_time: 结束时间
        event_type: 事件类型
        role: 角色
        task_id: 任务 ID
        run_id: 运行 ID
        limit: 返回数量限制
        offset: 偏移量

    Returns:
        事件列表
    """
    runtime_path = Path(runtime_root)
    audit_dir = runtime_path / "audit"

    # Prefer KernelOne/Polaris query path.
    try:
        event_type_enum: KernelAuditEventType | None = None
        if event_type:
            event_type_enum = KernelAuditEventType(event_type)

        facade = AuditUseCaseFacade(runtime_root=runtime_path.resolve())
        query_limit = max(limit + offset, 1000)
        if run_id:
            query_limit = max(query_limit * 3, 3000)

        records = [
            event.to_dict()
            for event in facade.query_logs(
                start_time=start_time,
                end_time=end_time,
                event_type=event_type_enum,
                role=role,
                task_id=task_id,
                limit=query_limit,
                offset=0,
            )
        ]

        if run_id:
            records = [
                row for row in records if isinstance(row.get("task"), dict) and row["task"].get("run_id") == run_id
            ]

        records.sort(key=lambda e: e.get("timestamp") or "")
        return records[offset : offset + limit]
    except ValueError:
        # Preserve legacy behavior: unknown event type yields empty result.
        return []
    except RuntimeError as exc:
        logger.warning("Kernel audit query failed, fallback to raw scan: %s", exc)

    if not audit_dir.exists():
        return []

    events = []

    # 查找所有审计日志文件
    log_files = sorted(audit_dir.glob("audit-*.jsonl"))

    for log_file in log_files:
        try:
            fs = KernelFileSystem(str(log_file.parent), get_default_adapter())
            content = fs.workspace_read_text(log_file.name, encoding="utf-8")
            for line_num, line in enumerate(content.split("\n"), 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError as e:
                    # 记录坏行但不中断
                    _record_corruption(
                        audit_dir,
                        str(log_file),
                        line_num,
                        "json_decode_error",
                        str(e),
                    )
                    continue

                # 安全解析时间戳
                event_time = _safe_parse_timestamp(event.get("timestamp", ""))

                # Apply filters
                if start_time and event_time and event_time < start_time:
                    continue

                if end_time and event_time and event_time > end_time:
                    continue

                if event_type and event.get("event_type") != event_type:
                    continue

                if role:
                    source = event.get("source", {})
                    if isinstance(source, dict) and source.get("role") != role:
                        continue

                if task_id:
                    task = event.get("task", {})
                    if isinstance(task, dict) and task.get("task_id") != task_id:
                        continue

                if run_id:
                    task = event.get("task", {})
                    if isinstance(task, dict) and task.get("run_id") != run_id:
                        continue

                events.append(event)

        except (RuntimeError, ValueError) as e:
            logger.error(f"Error reading {log_file}: {e}")
            _record_corruption(
                audit_dir,
                str(log_file),
                0,
                "file_read_error",
                str(e),
            )

    # Sort by timestamp (handle empty timestamps)
    events.sort(key=lambda e: e.get("timestamp") or "")

    # Apply pagination
    return events[offset : offset + limit]


def _resolve_journal_path(runtime_root: Path, run_id: str) -> Path | None:
    """Resolve journal file path for a given run_id.

    按优先级查找: norm > enriched > raw

    Args:
        runtime_root: Runtime 根目录
        run_id: 运行 ID

    Returns:
        Journal 文件路径或 None
    """
    run_dir = runtime_root / "runs" / run_id / "logs"
    if not run_dir.is_dir():
        return None

    candidates = [
        run_dir / "journal.norm.jsonl",
        run_dir / "journal.enriched.jsonl",
        run_dir / "journal.raw.jsonl",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _query_journal_events(runtime_root: Path, run_id: str, limit: int = 1000) -> list[dict[str, Any]]:
    """直接从 journal 文件读取事件。

    Args:
        runtime_root: Runtime 根目录
        run_id: 运行 ID
        limit: 返回数量限制

    Returns:
        事件列表
    """
    journal_path = _resolve_journal_path(runtime_root, run_id)
    if journal_path is None:
        return []

    events: list[dict[str, Any]] = []
    try:
        with open(journal_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    event = json.loads(line)
                    # journal 事件格式转换: 将 run_id 字段映射到 task.run_id
                    if "run_id" in event and "task" not in event:
                        event["task"] = {"run_id": event["run_id"]}
                    elif "run_id" in event and isinstance(event.get("task"), dict):
                        event["task"]["run_id"] = event["run_id"]
                    events.append(event)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []

    # 按时间排序
    events.sort(key=lambda e: e.get("ts") or e.get("timestamp") or "")
    return events[: max(1, int(limit))]


def query_by_run_id(
    runtime_root: str,
    run_id: str,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Query events by run_id.

    优先从 audit 总线查询，若无结果则 fallback 到 journal 文件。

    Args:
        runtime_root: Runtime 根目录
        run_id: 运行 ID
        limit: 返回数量限制

    Returns:
        事件列表
    """
    run_id_token = str(run_id or "").strip()
    if not run_id_token:
        return []

    runtime_path = Path(runtime_root).resolve()

    # 1. 尝试从 audit 总线查询
    try:
        facade = AuditUseCaseFacade(runtime_root=runtime_path)
        query_limit = max(1000, max(1, int(limit)) * 3)
        records = [event.to_dict() for event in facade.query_logs(limit=query_limit, offset=0)]
        filtered = [
            row
            for row in records
            if isinstance(row.get("task"), dict) and str(row["task"].get("run_id") or "").strip() == run_id_token
        ]
        if filtered:
            filtered.sort(key=lambda e: e.get("timestamp") or "")
            return filtered[: max(1, int(limit))]
    except (RuntimeError, ValueError) as exc:
        logger.debug("Audit facade run_id query failed: %s", exc)

    # 2. Fallback: 从 journal 文件读取
    journal_events = _query_journal_events(runtime_path, run_id_token, limit)
    if journal_events:
        logger.info("Loaded %d events from journal file for run_id=%s", len(journal_events), run_id_token)
        return journal_events

    # 3. 最后 fallback: 扫描 audit 目录
    logger.debug("Fallback to audit directory scan for run_id=%s", run_id_token)
    return query_events(
        runtime_root=runtime_root,
        run_id=run_id_token,
        limit=max(1, int(limit)),
    )


def query_by_task_id(
    runtime_root: str,
    task_id: str,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Query events by task_id.

    Args:
        runtime_root: Runtime 根目录
        task_id: 任务 ID
        limit: 返回数量限制

    Returns:
        事件列表
    """
    task_token = str(task_id or "").strip()
    if not task_token:
        return []

    try:
        facade = AuditUseCaseFacade(runtime_root=Path(runtime_root).resolve())
        records = [
            event.to_dict()
            for event in facade.query_logs(
                task_id=task_token,
                limit=max(1, int(limit)),
                offset=0,
            )
        ]
        records.sort(key=lambda e: e.get("timestamp") or "")
        return records[: max(1, int(limit))]
    except (RuntimeError, ValueError) as exc:
        logger.warning("Audit facade task_id query failed, fallback to scan: %s", exc)
        return query_events(
            runtime_root=runtime_root,
            task_id=task_token,
            limit=max(1, int(limit)),
        )


def query_by_trace_id(
    runtime_root: str,
    trace_id: str,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Query events by trace_id.

    Args:
        runtime_root: Runtime 根目录
        trace_id: 跟踪 ID
        limit: 返回数量限制

    Returns:
        事件列表
    """
    trace_token = str(trace_id or "").strip()
    if not trace_token:
        return []

    try:
        facade = AuditUseCaseFacade(runtime_root=Path(runtime_root).resolve())
        query_limit = max(1000, max(1, int(limit)) * 4)
        records = [
            event.to_dict()
            for event in facade.query_logs(
                limit=query_limit,
                offset=0,
            )
        ]
        filtered = []
        for row in records:
            context = row.get("context")
            if isinstance(context, dict) and str(context.get("trace_id") or "").strip() == trace_token:
                filtered.append(row)
        filtered.sort(key=lambda e: e.get("timestamp") or "")
        return filtered[: max(1, int(limit))]
    except (RuntimeError, ValueError) as exc:
        logger.warning("Audit facade trace_id query failed, fallback to scan: %s", exc)

    # 降级到全表扫描
    runtime_path = Path(runtime_root)
    audit_dir = runtime_path / "audit"

    if not audit_dir.exists():
        return []

    events = []

    # 查找所有审计日志文件
    log_files = sorted(audit_dir.glob("audit-*.jsonl"))

    for log_file in log_files:
        try:
            fs = KernelFileSystem(str(log_file.parent), get_default_adapter())
            content = fs.workspace_read_text(log_file.name, encoding="utf-8")
            for line_num, line in enumerate(content.split("\n"), 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as e:
                    # 记录坏行
                    _record_corruption(
                        audit_dir,
                        str(log_file),
                        line_num,
                        "json_decode_error",
                        str(e),
                    )
                    continue

                # Check trace_id in context
                context = event.get("context", {})
                if isinstance(context, dict) and context.get("trace_id") == trace_token:
                    events.append(event)

                if len(events) >= limit:
                    break
        except (RuntimeError, ValueError) as e:
            logger.error(f"Error reading {log_file}: {e}")
            _record_corruption(
                audit_dir,
                str(log_file),
                0,
                "file_read_error",
                str(e),
            )

    # Sort by timestamp (handle empty timestamps)
    events.sort(key=lambda e: e.get("timestamp") or "")

    return events[:limit]


def iter_events(
    runtime_root: str,
    batch_size: int = 100,
) -> Iterator[list[dict[str, Any]]]:
    """Iterate over audit events in batches.

    Args:
        runtime_root: Runtime 根目录
        batch_size: 批次大小

    Yields:
        批次事件列表
    """
    runtime_path = Path(runtime_root)
    audit_dir = runtime_path / "audit"

    if not audit_dir.exists():
        return

    # 查找所有审计日志文件
    log_files = sorted(audit_dir.glob("audit-*.jsonl"))

    batch = []

    for log_file in log_files:
        fs = KernelFileSystem(str(log_file.parent), get_default_adapter())
        content = fs.workspace_read_text(log_file.name, encoding="utf-8")
        for line in content.split("\n"):
            line = line.strip()
            # Skip empty lines and comment lines (consistent with other query functions)
            if not line or line.startswith("#"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            batch.append(event)

            if len(batch) >= batch_size:
                yield batch
                batch = []

    # Yield remaining
    if batch:
        yield batch
