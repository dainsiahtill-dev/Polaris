"""Basic CLI command handlers for audit quick.

This module contains handlers for basic query and display commands.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.cells.audit.diagnosis.internal.toolkit.service import (
    _discover_journal_run_dirs,
)
from polaris.cells.audit.diagnosis.public.service import (
    load_journal_events,
)
from polaris.delivery.cli.audit.audit.auditor import export_data, get_failures, health_check, show_event
from polaris.delivery.cli.audit.audit.diagnosis import diagnose_runtime
from polaris.delivery.cli.audit.audit.file_ops import (
    collect_runtime_event_inventory,
    discover_latest_runtime,
    tail_jsonl_events,
    watch_events,
)
from polaris.delivery.cli.audit.audit.formatters import (
    format_event_compact,
    format_relative_time,
    resolve_export_format,
)
from polaris.delivery.cli.audit.audit.handlers_advanced import (
    handle_corruption,
    handle_diagnose,
    handle_diff,
    handle_factory_events,
    handle_query_repl,
    handle_search_errors,
    handle_triage,
    handle_why,
)
from polaris.delivery.cli.audit.audit.reporters import format_health_compact, format_journal_events_compact
from polaris.delivery.cli.audit.audit_agent import get_events, get_stats, verify

if TYPE_CHECKING:
    import argparse
    from datetime import datetime

logger = logging.getLogger(__name__)


def handle_verify(args: argparse.Namespace, runtime_root: Path | None) -> int:
    """处理 verify 命令。"""
    workspace = str(runtime_root) if runtime_root else "."
    result = verify(workspace=workspace)  # type: ignore[call-arg]
    if args.format == "compact":
        status = "✓" if result.get("chain_valid") else "✗"
        mode = result.get("mode", "unknown")
        total = int(result.get("total_events", 0) or 0)
        has_events = bool(result.get("has_events")) if "has_events" in result else total > 0
        empty_chain = bool(result.get("empty_chain")) if "empty_chain" in result else not has_events
        logger.info("%s Chain valid: %s (mode: %s, events: %d)", status, result.get("chain_valid"), mode, total)
        logger.info("  审计数据状态: %s", "empty" if empty_chain else "available")

        if args.strict_non_empty and empty_chain:
            logger.info("  [严格模式] 审计数据不足：total_events=0，判定失败")
            errors = result.get("errors")
            if isinstance(errors, list) and errors:
                first = errors[0] if isinstance(errors[0], dict) else {"message": str(errors[0])}
                logger.info("  失败原因: %s", first.get("message"))

        if total == 0:
            logger.info("\n  [诊断] 未找到审计事件")
            if runtime_root:
                diag = diagnose_runtime(runtime_root)
                if not diag["runtime_exists"]:
                    logger.info("  • Runtime 目录不存在: %s", runtime_root)
                elif not diag["audit_dir"] or not diag["audit_dir"]["exists"]:
                    logger.info("  • Audit 目录不存在")
                elif not diag["log_files"]:
                    logger.info("  • 未找到日志文件 (audit-*.jsonl)")
                else:
                    logger.info("  • 找到 %d 个日志文件，但无事件记录", len(diag["log_files"]))
                logger.info("\n  建议运行: audit_quick.py diagnose --root %s", runtime_root)
            else:
                logger.info("  • 未指定 runtime 目录，尝试使用 --discover 或 --root")
    else:
        logger.info("%s", json.dumps(result, indent=2, ensure_ascii=False))

    if bool(args.strict_non_empty) and int(result.get("total_events", 0) or 0) == 0:
        return 1
    return 0


def handle_stats(args: argparse.Namespace, runtime_root: Path | None) -> int:
    """处理 stats 命令。"""
    workspace = str(runtime_root) if runtime_root else "."
    result = get_stats(workspace=workspace)  # type: ignore[call-arg]
    stats = result.get("stats", {})
    total = int(stats.get("total_events", 0) or 0)
    runtime_inventory: dict[str, Any] | None = None

    if total == 0 and runtime_root and runtime_root.exists():
        runtime_inventory = collect_runtime_event_inventory(runtime_root)
        if args.format == "json":
            result["runtime_event_inventory"] = runtime_inventory

    if args.format == "compact":
        mode = result.get("mode", "unknown")
        print(f"Total events: {total} (mode: {mode})")

        event_types = stats.get("event_types", {}) or stats.get("by_type", {})
        if event_types:
            print("\nEvent types:")
            for et, count in sorted(event_types.items(), key=lambda x: -x[1])[:5]:
                print(f"  {et}: {count}")

        if total == 0:
            print("\n  [提示] 未找到审计事件")
            fallback_total = int((runtime_inventory or {}).get("total_events", 0))
            if fallback_total > 0:
                print(f"  但发现 {fallback_total} 条 runtime 原始事件（未进入 audit 总线）")
                by_source = (runtime_inventory or {}).get("by_source", {})
                for source_name in ("role", "runtime", "audit"):
                    source_stats = by_source.get(source_name, {}) if isinstance(by_source, dict) else {}
                    source_events = int(source_stats.get("events", 0) or 0)
                    source_files = int(source_stats.get("files", 0) or 0)
                    if source_events > 0 or source_files > 0:
                        print(f"    - {source_name}: files={source_files}, events={source_events}")
                print("  结论: runtime 正在产生日志，但 audit 聚合链路可能未生效")
            if runtime_root:
                print(f"  建议运行: audit_quick.py diagnose --root {runtime_root}")
            else:
                print("  建议运行: audit_quick.py diagnose --discover")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def handle_events(args: argparse.Namespace, runtime_root: Path | None) -> int:
    """处理 events 命令。"""
    # UEP v2.0: Journal mode - query journal files directly
    if getattr(args, "journal", False):
        return _handle_journal_events(args, runtime_root)

    result: dict[str, Any] = get_events(  # type: ignore[assignment]
        workspace=str(runtime_root) if runtime_root else ".",
        limit=args.limit,
    )
    if args.format == "compact":
        events = result.get("events", [])  # type: ignore[typeddict-item]
        mode = result.get("mode", "unknown")  # type: ignore[typeddict-item]
        print(f"Events: {len(events)} (mode: {mode})\n")

        for event in events:  # type: ignore[assignment]
            use_relative = not args.no_relative_time
            print(format_event_compact(event, use_relative_time=use_relative))

        if len(events) == 0:
            print("\n[诊断] 未找到审计事件")
            if runtime_root:
                inventory = collect_runtime_event_inventory(runtime_root)
                fallback_total = int(inventory.get("total_events", 0))
                if fallback_total > 0:
                    print(
                        f"\n检测到 {fallback_total} 条 runtime 原始事件（role/runtime 文件），但 audit API 未返回记录。"
                    )
                    by_source = inventory.get("by_source", {})
                    for source_name in ("role", "runtime", "audit", "journal", "strategy_receipts"):
                        source_stats = by_source.get(source_name, {}) if isinstance(by_source, dict) else {}
                        source_events = int(source_stats.get("events", 0) or 0)
                        source_files = int(source_stats.get("files", 0) or 0)
                        if source_events > 0 or source_files > 0:
                            print(f"  - {source_name}: files={source_files}, events={source_events}")

                    journal_stats = by_source.get("journal", {}) if isinstance(by_source, dict) else {}
                    if journal_stats.get("events", 0):
                        journal_paths = journal_stats.get("paths", [])
                        if journal_paths:
                            print("\n--- Journal 事件 (fallback 模式) ---")
                            for jp in journal_paths:
                                journal_path = Path(jp)
                                tail_result = tail_jsonl_events(journal_path, limit=args.limit or 20)
                                print(
                                    format_journal_events_compact(
                                        tail_result.get("events", []),
                                        use_relative_time=not args.no_relative_time,
                                    )
                                )

                print("\n可能原因和解决方案:")
                print(f"  1. 审计事件存储在: {runtime_root}/audit/audit-*.jsonl")
                print("     但该目录可能为空或不存在")
                print("\n  2. 尝试使用 factory-events 命令查看工厂运行事件:")
                print(f"     python scripts/audit_quick.py factory-events --root {runtime_root}")
                print("\n  3. 运行诊断检查:")
                print(f"     python scripts/audit_quick.py diagnose --root {runtime_root}")
                print("\n  4. 如果刚运行完压测，尝试自动发现:")
                print("     python scripts/audit_quick.py events --discover")
            else:
                print("\n建议: 指定 --root 或使用 --discover 自动发现")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _handle_journal_events(args: argparse.Namespace, runtime_root: Path | None) -> int:
    """UEP v2.0: Query journal files directly (norm/enriched/raw)."""
    if not runtime_root:
        print("错误: --journal 模式需要指定 --root 或使用 --discover", file=sys.stderr)
        print("下一步: python scripts/audit_quick.py events --journal --discover", file=sys.stderr)
        return 1

    # Discover all journal run directories
    run_dirs = _discover_journal_run_dirs(runtime_root)
    if not run_dirs:
        print(f"未找到 Journal 运行目录: {runtime_root}/runs/")
        return 0

    limit: int = 50 if args.limit is None else args.limit
    all_events: list[dict[str, Any]] = []

    # Collect events from recent runs with smart limiting
    # Strategy: collect from each run, then merge and trim
    for run_dir in run_dirs:
        # Load events from this run (defensive copy to avoid mutation side-effects)
        events = load_journal_events(run_dir, limit=limit)
        for event in events:
            # Create a copy to avoid modifying the original event dict
            event_copy = dict(event)
            event_copy["_run_id"] = run_dir.name
            all_events.append(event_copy)

        # Early termination if we have enough events from recent runs
        # This is an optimization to avoid loading all runs when limit is small
        if len(all_events) >= limit * 2:  # Load 2x to ensure good merge results
            break

    # Sort by timestamp (handle missing/None timestamps gracefully)
    def _get_timestamp(e: dict[str, Any]) -> str:
        ts = e.get("timestamp")
        return str(ts) if ts is not None else ""

    all_events.sort(key=_get_timestamp)

    # Apply limit after sorting to get most recent events
    if len(all_events) > limit:
        all_events = all_events[-limit:]

    if args.format == "compact":
        print(f"Journal Events (UEP v2.0): {len(all_events)} from {len(run_dirs)} runs\n")
        print(format_journal_events_compact(all_events, use_relative_time=not args.no_relative_time))

        if not all_events:
            print("\n[提示] 未找到 Journal 事件")
            print(f"  检查路径: {runtime_root}/runs/*/logs/journal.*.jsonl")
    else:
        # JSON format - include metadata
        output = {
            "source": "journal",
            "version": "2.0",
            "runtime_root": str(runtime_root),
            "runs_discovered": len(run_dirs),
            "events": all_events,
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))

    return 0


def handle_watch(args: argparse.Namespace, runtime_root: Path | None) -> int:
    """处理 watch 命令。"""
    if not runtime_root:
        print("错误: watch 模式需要指定 --root 或使用 --discover", file=sys.stderr)
        print("下一步: python scripts/audit_quick.py watch --discover", file=sys.stderr)
        return 1

    print(f"开始监控 {runtime_root} (间隔: {args.interval}s)")
    print("按 Ctrl+C 停止\n")

    try:
        for event in watch_events(
            runtime_root=runtime_root,
            interval=args.interval,
            event_type=args.event_type,
        ):
            if "_error" in event:
                print(f"[错误] {event['_error']}")
                continue

            ts = format_relative_time(event.get("timestamp", ""))
            event_type = event.get("event_type", "unknown")
            source = event.get("source", {})
            role = source.get("role", "unknown") if isinstance(source, dict) else "unknown"
            action = event.get("action", {})
            name = action.get("name", "") if isinstance(action, dict) else ""
            result_str = action.get("result", "") if isinstance(action, dict) else ""

            print(f"[{ts:12}] [{role:12}] {event_type:20} {name[:30]:30} {result_str}")
            sys.stdout.flush()
    except KeyboardInterrupt:
        print("\n监控已停止")
    return 0


def handle_failures(
    args: argparse.Namespace, runtime_root: Path | None, since_dt: datetime | None, until_dt: datetime | None
) -> int:
    """处理 failures 命令。"""
    if not runtime_root:
        print("错误: 需要指定 --root 或使用 --discover", file=sys.stderr)
        print("下一步: python scripts/audit_quick.py failures --discover", file=sys.stderr)
        return 1

    failures = get_failures(
        runtime_root=runtime_root,
        since=since_dt,
        until=until_dt,
        limit=args.limit,
    )

    if args.format == "compact":
        print(f"失败事件: {len(failures)}\n")
        for event in failures[: args.limit]:
            ts = format_relative_time(event.get("timestamp", ""))
            event_type = event.get("event_type", "unknown")
            source = event.get("source", {})
            role = source.get("role", "unknown") if isinstance(source, dict) else "unknown"
            action = event.get("action", {}) or {}
            name = action.get("name", "")
            error = action.get("error", "")

            print(f"[{ts:12}] [{role:12}] {event_type}")
            print(f"  操作: {name}")
            if error:
                print(f"  错误: {error}")
            print()
    else:
        print(json.dumps({"failures": failures}, indent=2, ensure_ascii=False))
    return 0


def handle_health(args: argparse.Namespace, runtime_root: Path | None) -> int:
    """处理 health 命令。"""
    if not runtime_root:
        discovered = discover_latest_runtime()
        if discovered:
            runtime_root = discovered
        else:
            print("错误: 需要指定 --root 或使用 --discover", file=sys.stderr)
            return 1

    result = health_check(runtime_root)

    if args.format == "compact":
        print(format_health_compact(result, runtime_root))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def handle_export(
    args: argparse.Namespace, runtime_root: Path | None, since_dt: datetime | None, until_dt: datetime | None
) -> int:
    """处理 export 命令。"""
    if not runtime_root:
        print("错误: 需要指定 --root 或使用 --discover", file=sys.stderr)
        print("下一步: python scripts/audit_quick.py export --discover -o report.json", file=sys.stderr)
        return 1

    if not args.output:
        print("错误: 需要指定 -o/--output 导出文件路径", file=sys.stderr)
        print("下一步: python scripts/audit_quick.py export --discover -o report.json", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    export_format = resolve_export_format(
        export_format_arg=args.export_format,
        output_path=output_path,
    )

    try:
        result = export_data(
            runtime_root=runtime_root,
            output_path=output_path,
            export_format=export_format,
            since=since_dt,
            until=until_dt,
        )
        print(f"导出成功: {result['path']}")
        print(f"格式: {result['format']}, 大小: {result['size']} bytes")
        if "records" in result:
            print(f"记录数: {result['records']}")
    except (RuntimeError, ValueError) as e:
        print(f"导出失败: {e}", file=sys.stderr)
        return 1
    return 0


def handle_show(args: argparse.Namespace, runtime_root: Path | None) -> int:
    """处理 show 命令。"""
    if not runtime_root:
        print("错误: 需要指定 --root", file=sys.stderr)
        print("下一步: python scripts/audit_quick.py show <event_id> --discover", file=sys.stderr)
        return 1

    if not args.event_id:
        print("错误: 需要提供 event_id", file=sys.stderr)
        return 1

    event = show_event(runtime_root, args.event_id)
    if event:
        print(json.dumps(event, indent=2, ensure_ascii=False))
    else:
        print(f"未找到事件: {args.event_id}")
        print("\n提示: 使用以下命令查看最近的事件ID")
        print(f"  python scripts/audit_quick.py events --root {runtime_root} -f json | grep event_id")
    return 0


__all__ = [
    "_handle_journal_events",  # UEP v2.0: Journal query handler
    "handle_corruption",
    "handle_diagnose",
    "handle_diff",
    "handle_events",
    "handle_export",
    "handle_factory_events",
    "handle_failures",
    "handle_health",
    "handle_query_repl",
    "handle_search_errors",
    "handle_show",
    "handle_stats",
    "handle_triage",
    "handle_verify",
    "handle_watch",
    "handle_why",
]
