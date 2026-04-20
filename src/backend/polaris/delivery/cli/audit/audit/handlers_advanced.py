"""Advanced CLI command handlers for audit quick.

This module contains handlers for diagnostic and analysis commands.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from polaris.delivery.cli.audit.audit.auditor import run_diff, run_why, smart_triage
from polaris.delivery.cli.audit.audit.diagnosis import diagnose_runtime
from polaris.delivery.cli.audit.audit.factory_ops import collect_factory_events
from polaris.delivery.cli.audit.audit.file_ops import discover_latest_runtime
from polaris.delivery.cli.audit.audit.formatters import format_time_window, parse_window
from polaris.delivery.cli.audit.audit.reporters import (
    format_factory_events_compact,
    format_search_errors_compact,
    format_triage_compact,
    print_diagnosis,
)
from polaris.delivery.cli.audit.audit_agent import get_corruption_log

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

try:
    from polaris.cells.audit.diagnosis.public import ErrorChainSearcher
except ImportError:
    from polaris.cells.audit.diagnosis.public import ErrorChainSearcher


def handle_triage(args: argparse.Namespace, runtime_root: Path | None) -> int:
    """处理 triage 命令。"""
    if not runtime_root:
        print("错误: 需要指定 --root", file=sys.stderr)
        return 1

    result = smart_triage(
        runtime_root=runtime_root,
        run_id=args.run_id,
        task_id=args.task_id,
        mode=args.mode,
    )

    if args.format == "compact":
        print(format_triage_compact(result))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def handle_corruption(args: argparse.Namespace, runtime_root: Path | None) -> int:
    """处理 corruption 命令。"""
    from polaris.delivery.cli.audit.audit.formatters import format_relative_time

    result = get_corruption_log(limit=args.limit, runtime_root=runtime_root, mode=args.mode)
    if args.format == "compact":
        records = result.get("records", [])
        mode = result.get("mode", "unknown")
        print(f"Corruption records: {len(records)} (mode: {mode})\n")

        for record in records:
            ts = record.get("timestamp", "")
            ts = format_relative_time(ts) if ts else ""
            error_type = record.get("error_type", "unknown")
            file_path = record.get("file_path", "")
            print(f"{ts:12} [{error_type}] {file_path}")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def handle_diagnose(args: argparse.Namespace, runtime_root: Path | None) -> int:
    """处理 diagnose 命令。"""
    if not runtime_root:
        discovered = discover_latest_runtime()
        if discovered:
            runtime_root = discovered
        else:
            print("错误: 需要指定 --root 或设置 POLARIS_RUNTIME_BASE", file=sys.stderr)
            return 1

    result = diagnose_runtime(runtime_root)
    if args.format == "compact":
        print_diagnosis(result)
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def handle_factory_events(args: argparse.Namespace, runtime_root: Path | None) -> int:
    """处理 factory-events 命令。"""
    if not runtime_root:
        discovered = discover_latest_runtime()
        if discovered:
            runtime_root = discovered
        else:
            print("错误: 需要指定 --root 或设置 POLARIS_RUNTIME_BASE", file=sys.stderr)
            return 1

    collection = collect_factory_events(
        runtime_root,
        run_id=args.run_id,
        limit_per_run=args.limit,
        max_runs=5,
    )

    if collection.get("status") != "ok":
        reason = str(collection.get("reason", "unknown"))
        reason_message = {
            "factory_dir_missing": "未发现 factory 目录",
            "no_factory_runs": "factory 目录存在，但没有 factory_* 运行目录",
            "run_id_not_found": f"未找到指定 run_id: {args.run_id}",
            "events_file_missing": "找到了 factory 运行目录，但缺少 events/events.jsonl",
        }.get(reason, f"未找到工厂事件（{reason}）")

        print(f"未找到工厂运行事件: {reason_message}")

        available_run_ids = collection.get("available_run_ids") or []
        if args.run_id and available_run_ids:
            print("\n可用 run_id:")
            for rid in available_run_ids[:10]:
                print(f"  - {rid}")
            if len(available_run_ids) > 10:
                print(f"  ... 还有 {len(available_run_ids) - 10} 个")

        checked_factory_dirs = collection.get("checked_factory_dirs") or []
        if checked_factory_dirs:
            print("\n已检查目录:")
            for path in checked_factory_dirs[:3]:
                print(f"  - {path}")
            if len(checked_factory_dirs) > 3:
                print(f"  ... 还有 {len(checked_factory_dirs) - 3} 个目录")

        print("\n提示: 工厂事件是可选产物，缺失不会影响 audit_quick 其他命令")
        return 0

    print(
        format_factory_events_compact(
            collection,
            use_relative_time=not args.no_relative_time,
            no_relative_time=args.no_relative_time,
        )
    )
    return 0


def handle_search_errors(
    args: argparse.Namespace, runtime_root: Path | None, since_dt: datetime | None, until_dt: datetime | None
) -> int:
    """处理 search-errors 命令。"""
    if not runtime_root:
        discovered = discover_latest_runtime()
        if discovered:
            runtime_root = discovered
        else:
            print("错误: 需要指定 --root 或设置 POLARIS_RUNTIME_BASE", file=sys.stderr)
            return 1

    if not args.pattern:
        print("错误: search-errors 命令需要 --pattern 参数", file=sys.stderr)
        print("示例: audit_quick.py search-errors --pattern 'Tool returned unsuccessful result'", file=sys.stderr)
        return 1

    try:
        searcher = ErrorChainSearcher(runtime_root)
        chains = searcher.search(
            pattern=args.pattern,
            strategy=args.strategy,
            since=since_dt,
            until=until_dt,
            limit=args.limit,
            context_window=args.context,
            link_chains=args.link_chains,
            include_factory=True,
        )
        time_window = format_time_window(since=since_dt, until=until_dt)

        if not chains:
            print(f"未找到匹配错误: {args.pattern}")
            print(f"时间范围: {time_window}")

            stats = searcher.last_search_stats
            if stats:
                print("\n[诊断信息]")
                print(f"  扫描的事件文件: {len(stats.get('files_scanned', []))}")
                print(f"  扫描的工厂文件: {len(stats.get('factory_files', []))}")
                print(f"  总事件数: {stats.get('total_events', 0)}")
                print(f"    - Action 事件: {stats.get('action_events', 0)}")
                print(f"    - Observation 事件: {stats.get('observation_events', 0)}")
                print(f"    - Factory 事件: {stats.get('factory_events', 0)}")
                if "role_events" in stats:
                    print(f"    - Role 事件: {stats.get('role_events', 0)}")
                if "runtime_events" in stats:
                    print(f"    - Runtime 事件: {stats.get('runtime_events', 0)}")

                if stats.get("files_scanned"):
                    print("\n  扫描的文件:")
                    for f in stats["files_scanned"][:3]:
                        print(f"    - {f}")
                    if len(stats["files_scanned"]) > 3:
                        print(f"    ... 还有 {len(stats['files_scanned']) - 3} 个")

            print("\n提示: 尝试使用不同的匹配策略")
            print("  --strategy substring (默认)")
            print("  --strategy regex")
            print("  --strategy fuzzy")

            if stats and stats.get("total_events", 0) == 0:
                print("\n未找到任何事件! 建议:")
                print(f"  1. 检查 runtime 目录是否正确: {runtime_root}")
                print("  2. 使用 diagnose 命令查看目录结构:")
                print(f"     python scripts/audit_quick.py diagnose --root {runtime_root}")
                print("  3. 使用 factory-events 查看工厂事件:")
                print(f"     python scripts/audit_quick.py factory-events --root {runtime_root}")

            return 0

        if args.format == "json":
            print(
                json.dumps(
                    {
                        "query": {
                            "pattern": args.pattern,
                            "strategy": args.strategy,
                            "matched_count": len(chains),
                            "since": since_dt.isoformat() if since_dt else None,
                            "until": until_dt.isoformat() if until_dt else None,
                            "time_window": time_window,
                        },
                        "error_chains": [chain.to_dict() for chain in chains],
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print(
                format_search_errors_compact(
                    chains,
                    pattern=args.pattern,
                    time_window=time_window,
                    show_args=args.show_args,
                    show_output=args.show_output,
                    link_chains=args.link_chains,
                    context=args.context,
                )
            )

    except (RuntimeError, ValueError) as e:
        print(f"错误: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1
    return 0


def handle_diff(args: argparse.Namespace, runtime_root: Path | None) -> int:
    """处理 diff 命令。"""
    if not args.run_a or not args.run_b:
        print("错误: diff 命令需要 --run-a 和 --run-b 参数", file=sys.stderr)
        print("示例: audit_quick.py diff --run-a run_001 --run-b run_002", file=sys.stderr)
        return 1

    if not runtime_root:
        print("错误: 需要指定 --root 或使用 --discover", file=sys.stderr)
        return 1

    result = run_diff(runtime_root, args.run_a, args.run_b)
    print(json.dumps(result, indent=2, default=str))
    return 0


def handle_why(args: argparse.Namespace, runtime_root: Path | None) -> int:
    """处理 why 命令。"""
    if not args.task_id:
        print("错误: why 命令需要 --task 参数", file=sys.stderr)
        print("示例: audit_quick.py why --task task_001", file=sys.stderr)
        return 1

    if not runtime_root:
        print("错误: 需要指定 --root 或使用 --discover", file=sys.stderr)
        return 1

    result = run_why(runtime_root, args.task_id)
    if result.get("status") == "not_found":
        print(result.get("message", ""))
        return 1
    elif result.get("status") == "no_failure":
        print(result.get("message", ""))
        print(f"Task had {result.get('event_count', 0)} events but none reported failure")
        return 0

    confidence = result.get("confidence", 0.0)
    print(f"\n  Task: {args.task_id}")
    print(f"  Primary failure: {result.get('primary_failure_type', '')}")
    print(f"  Confidence: {confidence:.0%}")
    print("\n  Root cause assessment:")
    primary_cause = result.get("primary_cause")
    if primary_cause:
        print(f"    {primary_cause}")
    else:
        print("    (unknown)")
    print(f"  Resolution hint: {result.get('resolution_hint', '')}")
    upstream = result.get("upstream_events", [])
    print(f"\n  Upstream events ({len(upstream)}):")
    for e_dict in (upstream or [])[:5]:
        etype = e_dict.get("event_type", "") if isinstance(e_dict, dict) else ""
        result_str = e_dict.get("action", {}).get("result", "") if isinstance(e_dict, dict) else ""
        print(f"    - {etype} ({result_str})")

    downstream = result.get("affected_downstream", [])
    print(f"\n  Affected downstream ({len(downstream)}):")
    for e_dict in (downstream or [])[:3]:
        etype = e_dict.get("event_type", "") if isinstance(e_dict, dict) else ""
        print(f"    - {etype}")
    return 0


def handle_query_repl(args: argparse.Namespace, runtime_root: Path | None) -> int:
    """处理 query-repl 命令。"""
    if not runtime_root:
        print("错误: 需要指定 --root 或使用 --discover", file=sys.stderr)
        return 1

    from polaris.kernelone.audit import KernelAuditRuntime

    runtime = KernelAuditRuntime.get_instance(runtime_root)

    print("Audit Query REPL (type 'help', 'quit' to exit)")
    print(f"Connected to: {runtime_root}")

    while True:
        try:
            line = input("audit> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line or line in {"quit", "exit", "q"}:
            break

        if line in {"help", "?"}:
            print("Commands:")
            print("  errors [--last Nh]  - Show failed events")
            print("  trace <id>          - Show events for trace")
            print("  task <id>           - Show events for task")
            print("  types               - Show all event type counts")
            print("  recent [--last Nh]  - Show recent events")
            print("  why <task_id>       - Explain why a task failed")
            print("  quit                - Exit REPL")
            continue

        parts = line.split(maxsplit=1)
        cmd = parts[0]
        arg = parts[1] if len(parts) > 1 else ""

        try:
            if cmd == "errors":
                window_h = parse_window(arg or "1h")
                since = datetime.now(timezone.utc) - timedelta(hours=window_h)
                events = runtime.query_events(start_time=since, limit=50)
                failed = [e for e in events if str(e.action.get("result") or "") == "failure"]
                print(f"  {len(failed)} failure events in last {window_h}h:")
                for e in failed[:20]:
                    print(f"    [{e.event_id[:8]}] {e.event_type.value} | {e.timestamp.isoformat()}")
            elif cmd == "trace":
                if not arg:
                    print("Usage: trace <trace_id>")
                    continue
                events = runtime.query_by_trace_id(arg, limit=100)
                print(f"  {len(events)} events for trace '{arg}':")
                for e in events[:30]:
                    print(f"    [{e.event_id[:8]}] {e.event_type.value} | {e.timestamp.isoformat()}")
            elif cmd == "task":
                if not arg:
                    print("Usage: task <task_id>")
                    continue
                events = runtime.query_by_task_id(arg, limit=100)
                print(f"  {len(events)} events for task '{arg}':")
                for e in events[:30]:
                    print(f"    [{e.event_id[:8]}] {e.event_type.value} | {e.action.get('result', '')}")
            elif cmd == "types":
                events = runtime.query_events(limit=500)
                from collections import Counter

                counts = Counter(e.event_type.value for e in events)
                for etype, count in counts.most_common():
                    print(f"  {etype}: {count}")
            elif cmd == "recent":
                window_h = parse_window(arg or "1h")
                since = datetime.now(timezone.utc) - timedelta(hours=window_h)
                events = runtime.query_events(start_time=since, limit=50)
                print(f"  {len(events)} events in last {window_h}h:")
                for e in events[:20]:
                    print(f"    [{e.event_id[:8]}] {e.event_type.value} | {e.timestamp.isoformat()}")
            elif cmd == "why":
                if not arg:
                    print("Usage: why <task_id>")
                    continue
                from polaris.kernelone.audit.error_correlator import ErrorCorrelator

                events = runtime.query_by_task_id(arg, limit=500)
                if not events:
                    print(f"  No events found for task '{arg}'")
                    continue
                failed_events = [
                    e
                    for e in events
                    if str(e.action.get("result") or "") == "failure"
                    and e.event_type.value in {"task_failed", "tool_execution", "llm_call"}
                ]
                if not failed_events:
                    print(f"  No failure events for '{arg}'")
                    continue
                correlator = ErrorCorrelator()
                result = correlator.correlate(task_id=arg, error_event=failed_events[0], all_events=events)
                from polaris.delivery.cli.audit.audit.formatters import get_result_attr

                confidence = get_result_attr(result, "confidence", 0.0)
                print(f"  [{failed_events[0].event_type.value}] confidence={confidence:.0%}")
                print(f"  {get_result_attr(result, 'resolution_hint', '')}")
            else:
                print(f"Unknown command: {cmd}")
        except (RuntimeError, ValueError) as exc:
            print(f"Error: {exc}")
    return 0
