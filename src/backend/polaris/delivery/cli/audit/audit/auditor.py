"""Core auditor functions for audit quick CLI.

This module contains the core audit logic including health checks,
failure analysis, export, and event retrieval.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from polaris.delivery.cli.audit.audit.formatters import get_result_attr

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


def health_check(runtime_root: Path) -> dict[str, Any]:
    """执行健康检查。

    Args:
        runtime_root: runtime 根目录

    Returns:
        包含健康检查结果的字典
    """
    results: dict[str, Any] = {
        "checks": {},
        "overall": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # 检查1: runtime 目录可读写
    try:
        test_file = runtime_root / ".health_test"
        test_file.write_text("test", encoding="utf-8")
        test_file.read_text(encoding="utf-8")
        test_file.unlink()
        results["checks"]["runtime_accessible"] = {"status": "ok", "message": "目录可读写"}
    except (RuntimeError, ValueError) as e:
        results["checks"]["runtime_accessible"] = {"status": "error", "message": str(e)}
        results["overall"] = "unhealthy"

    # 检查2: 索引文件完整性
    try:
        index_files = list(runtime_root.glob("audit/index.*.json"))
        if index_files:
            corrupted = 0
            for idx_file in index_files:
                try:
                    json.loads(idx_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    corrupted += 1
            if corrupted:
                results["checks"]["index_integrity"] = {
                    "status": "warning",
                    "message": f"{corrupted}/{len(index_files)} 索引文件损坏",
                }
            else:
                results["checks"]["index_integrity"] = {"status": "ok", "message": f"{len(index_files)} 个索引文件正常"}
        else:
            results["checks"]["index_integrity"] = {"status": "info", "message": "无索引文件"}
    except (RuntimeError, ValueError) as e:
        results["checks"]["index_integrity"] = {"status": "error", "message": str(e)}

    # 检查3: 事件数据新鲜度
    try:
        from polaris.infrastructure.audit.stores.audit_store import AuditStore

        store = AuditStore(runtime_root=runtime_root)
        recent_events = store.query(limit=1)
        if recent_events:
            last_event = recent_events[0]
            last_time = last_event.timestamp
            now = datetime.now(timezone.utc)
            if last_time.tzinfo is None:
                last_time = last_time.replace(tzinfo=timezone.utc)
            age_hours = (now - last_time).total_seconds() / 3600

            if age_hours < 1:
                freshness = "ok"
                msg = f"最近事件 {int(age_hours * 60)} 分钟前"
            elif age_hours < 24:
                freshness = "ok"
                msg = f"最近事件 {int(age_hours)} 小时前"
            else:
                freshness = "warning"
                msg = f"最近事件 {int(age_hours / 24)} 天前，可能写入链路中断"
            results["checks"]["event_freshness"] = {"status": freshness, "message": msg}
        else:
            results["checks"]["event_freshness"] = {"status": "warning", "message": "无事件数据"}
    except (RuntimeError, ValueError) as e:
        results["checks"]["event_freshness"] = {"status": "error", "message": str(e)}

    # 检查4: 事件总数
    try:
        all_events = list(store.query(limit=100000))
        results["checks"]["event_count"] = {"status": "ok", "count": len(all_events)}
    except (RuntimeError, ValueError) as e:
        results["checks"]["event_count"] = {"status": "error", "message": str(e)}

    # 综合判断
    error_count = sum(1 for c in results["checks"].values() if c.get("status") == "error")
    warning_count = sum(1 for c in results["checks"].values() if c.get("status") == "warning")

    if error_count > 0:
        results["overall"] = "unhealthy"
    elif warning_count > 0:
        results["overall"] = "degraded"

    return results


def get_failures(
    runtime_root: Path,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """获取失败事件。

    Args:
        runtime_root: runtime 根目录
        since: 起始时间
        until: 结束时间
        limit: 事件数量限制

    Returns:
        失败事件列表
    """
    from polaris.infrastructure.audit.stores.audit_store import AuditStore

    store = AuditStore(runtime_root=runtime_root)
    events = store.query(start_time=since, end_time=until, limit=limit * 3)

    failures: list[dict[str, Any]] = []
    for evt in events:
        action = evt.action if isinstance(evt.action, dict) else {}
        if action.get("result") == "failure" or evt.event_type.value == "task_failed":
            failures.append(evt.to_dict())
        if len(failures) >= limit:
            break

    return failures


def export_data(
    runtime_root: Path,
    output_path: Path,
    export_format: str = "json",
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """导出审计数据。

    Args:
        runtime_root: runtime 根目录
        output_path: 输出文件路径
        export_format: 导出格式 ("json" 或 "csv")
        since: 起始时间
        until: 结束时间

    Returns:
        导出结果信息

    Raises:
        ValueError: 不支持的格式
    """
    from polaris.infrastructure.audit.stores.audit_store import AuditStore

    store = AuditStore(runtime_root=runtime_root)

    if export_format == "json":
        payload = store.export_json(
            start_time=since,
            end_time=until,
            include_data=True,
        )
        content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
        output_path.write_text(content, encoding="utf-8")
        record_count: int | None = None
        if isinstance(payload, dict):
            metadata = payload.get("export_metadata") if isinstance(payload.get("export_metadata"), dict) else {}
            if metadata:
                record_count_val = metadata.get("record_count")
                try:
                    record_count = int(record_count_val) if record_count_val is not None else None
                except (TypeError, ValueError):
                    record_count = None
        result: dict[str, Any] = {
            "format": "json",
            "path": str(output_path),
            "size": len(content.encode("utf-8")),
        }
        if record_count is not None:
            result["records"] = record_count
        return result

    elif export_format == "csv":
        events = list(store.query(start_time=since, end_time=until, limit=100000))

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            ["event_id", "timestamp", "event_type", "role", "task_id", "run_id", "operation", "result", "error"]
        )

        for evt in events:
            source = evt.source if isinstance(evt.source, dict) else {}
            task = evt.task if isinstance(evt.task, dict) else {}
            action = evt.action if isinstance(evt.action, dict) else {}

            writer.writerow(
                [
                    evt.event_id,
                    evt.timestamp.isoformat(),
                    evt.event_type.value,
                    source.get("role", ""),
                    task.get("task_id", ""),
                    task.get("run_id", ""),
                    action.get("name", ""),
                    action.get("result", ""),
                    action.get("error", ""),
                ]
            )

        content = buffer.getvalue()
        output_path.write_text(content, encoding="utf-8")
        return {"format": "csv", "path": str(output_path), "size": len(content), "records": len(events)}

    else:
        raise ValueError(f"Unsupported format: {export_format}")


def show_event(runtime_root: Path, event_id: str) -> dict[str, Any] | None:
    """查看单事件详情。

    Args:
        runtime_root: runtime 根目录
        event_id: 事件 ID

    Returns:
        事件详情字典，或 None 如果未找到
    """
    from polaris.infrastructure.audit.stores.audit_store import AuditStore

    store = AuditStore(runtime_root=runtime_root)
    events = store.query(limit=100000)

    for evt in events:
        if evt.event_id == event_id:
            return evt.to_dict()

    return None


def smart_triage(
    runtime_root: Path,
    run_id: str | None = None,
    task_id: str | None = None,
    mode: str = "offline",
) -> dict[str, Any]:
    """智能排障，增强无结果时的引导。

    Args:
        runtime_root: runtime 根目录
        run_id: 运行 ID
        task_id: 任务 ID
        mode: 运行模式

    Returns:
        排障结果字典
    """
    from polaris.delivery.cli.audit.audit_agent import triage

    result = triage(
        run_id=run_id,
        task_id=task_id,
        runtime_root=runtime_root,
        mode=mode,
    )

    # 检查是否有实际事件数据
    tool_audit = result.get("director_tool_audit", {})
    has_events = result.get("status") == "success" and tool_audit.get("total", 0) > 0

    # 如果无结果，提供智能建议
    if not has_events:
        suggestions: list[str] = []

        if run_id:
            suggestions.append(f"尝试查看所有事件: audit_quick.py events --root {runtime_root}")

        try:
            from polaris.infrastructure.audit.stores.audit_store import AuditStore

            store = AuditStore(runtime_root=runtime_root)
            all_events = store.query(limit=100)
            run_ids: set[str] = set()
            for evt in all_events:
                task = evt.task if isinstance(evt.task, dict) else {}
                rid = task.get("run_id")
                if rid:
                    run_ids.add(rid)

            if run_ids:
                suggestions.append(f"可用的 run_id: {', '.join(list(run_ids)[:5])}")
        except (RuntimeError, ValueError):
            logger.debug("DEBUG: audit_quick.py:{834} {exc} (swallowed)")

        suggestions.append(f"检查系统健康: audit_quick.py health --root {runtime_root}")
        suggestions.append(f"检查损坏日志: audit_quick.py corruption --root {runtime_root}")

        result["suggestions"] = suggestions
        result["help_message"] = "未找到匹配事件，请尝试以上建议"

    return result


def run_diff(
    runtime_root: Path,
    run_a: str,
    run_b: str,
) -> dict[str, Any]:
    """比较两个运行的事件差异。

    Args:
        runtime_root: runtime 根目录
        run_a: 第一个运行 ID
        run_b: 第二个运行 ID

    Returns:
        比较结果字典
    """
    from collections import Counter

    from polaris.kernelone.audit import KernelAuditRuntime

    runtime = KernelAuditRuntime.get_instance(runtime_root)

    events_a = runtime.query_by_run_id(run_a, limit=5000)
    events_b = runtime.query_by_run_id(run_b, limit=5000)

    ids_a = {e.event_id for e in events_a}
    ids_b = {e.event_id for e in events_b}

    added = ids_b - ids_a
    removed = ids_a - ids_b

    types_a = Counter(e.event_type.value for e in events_a)
    types_b = Counter(e.event_type.value for e in events_b)
    all_types = set(types_a) | set(types_b)
    changed_types: dict[str, dict[str, int]] = {}
    for t in all_types:
        delta = types_b.get(t, 0) - types_a.get(t, 0)
        if delta != 0:
            changed_types[t] = {
                "run_a": types_a.get(t, 0),
                "run_b": types_b.get(t, 0),
                "delta": delta,
            }

    failures_a = sum(1 for e in events_a if str(e.action.get("result") or "") == "failure")
    failures_b = sum(1 for e in events_b if str(e.action.get("result") or "") == "failure")

    return {
        "run_a": run_a,
        "run_b": run_b,
        "run_a_event_count": len(events_a),
        "run_b_event_count": len(events_b),
        "added_count": len(added),
        "removed_count": len(removed),
        "added_event_ids": list(added)[:50],
        "removed_event_ids": list(removed)[:50],
        "changed_event_types": changed_types,
        "failure_delta": {
            "run_a": failures_a,
            "run_b": failures_b,
            "delta": failures_b - failures_a,
        },
    }


def run_why(
    runtime_root: Path,
    task_id: str,
) -> dict[str, Any]:
    """分析任务失败原因。

    Args:
        runtime_root: runtime 根目录
        task_id: 任务 ID

    Returns:
        分析结果字典
    """
    from polaris.kernelone.audit import KernelAuditRuntime
    from polaris.kernelone.audit.error_correlator import ErrorCorrelator

    runtime = KernelAuditRuntime.get_instance(runtime_root)

    events = runtime.query_by_task_id(task_id, limit=500)
    if not events:
        return {
            "status": "not_found",
            "message": f"No events found for task '{task_id}'",
        }

    failed_events = [
        e
        for e in events
        if str(e.action.get("result") or "") == "failure"
        and e.event_type.value in {"task_failed", "tool_execution", "llm_call"}
    ]

    if not failed_events:
        return {
            "status": "no_failure",
            "message": f"No failure events found for task '{task_id}'",
            "event_count": len(events),
        }

    primary_failure = failed_events[0]

    correlator = ErrorCorrelator()
    result = correlator.correlate(task_id=task_id, error_event=primary_failure, all_events=events)

    confidence = get_result_attr(result, "confidence", 0.0)
    output: dict[str, Any] = {
        "status": "found",
        "task_id": task_id,
        "primary_failure_type": primary_failure.event_type.value,
        "confidence": confidence,
        "primary_cause": get_result_attr(result, "primary_cause"),
        "resolution_hint": get_result_attr(result, "resolution_hint", ""),
        "upstream_events": get_result_attr(result, "upstream_events", []),
        "affected_downstream": get_result_attr(result, "affected_downstream", []),
    }
    return output
