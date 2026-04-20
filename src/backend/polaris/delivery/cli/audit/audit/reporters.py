"""Report generation and printing for audit quick CLI.

This module contains functions for generating and printing audit reports.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from polaris.delivery.cli.audit.audit.formatters import format_relative_time

logger = logging.getLogger(__name__)


def print_diagnosis(result: dict[str, Any]) -> None:
    """打印诊断结果。

    Args:
        result: 诊断结果字典
    """
    logger.info("\n诊断报告: %s", result["runtime_root"])
    logger.info("=" * 60)

    logger.info("\n[目录状态]")
    logger.info("  Runtime 根目录: %s", "✓ 存在" if result["runtime_exists"] else "✗ 不存在")
    if result["audit_dir"]:
        logger.info("  Audit 目录: %s", "✓ 存在" if result["audit_dir"]["exists"] else "✗ 不存在")
    if result["events_dir"]:
        logger.info("  Events 目录: %s", "✓ 存在" if result["events_dir"]["exists"] else "✗ 不存在")

    # 显示所有找到的事件文件
    if result["all_event_files"]:
        logger.info("\n[所有事件文件] (%d 个)", len(result["all_event_files"]))
        for f in result["all_event_files"][:10]:
            try:
                rel = Path(f).relative_to(Path(result["runtime_root"]))
                logger.info("  - %s", rel)
            except (RuntimeError, ValueError):
                logger.info("  - %s", f)
        if len(result["all_event_files"]) > 10:
            logger.info("  ... 还有 %d 个", len(result["all_event_files"]) - 10)
    else:
        logger.info("\n[所有事件文件] 无")

    if result["log_files"]:
        logger.info("\n[Audit 日志文件] (%d 个)", len(result["log_files"]))
        for f in result["log_files"][:5]:
            logger.info("  - %s", Path(f).name)
        if len(result["log_files"]) > 5:
            logger.info("  ... 还有 %d 个", len(result["log_files"]) - 5)
        logger.info("  Audit 事件数: %s", result.get("audit_event_count", result["total_events"]))
    else:
        logger.info("\n[Audit 日志文件] 无")

    all_events_total = int(result.get("all_events_total", 0))
    event_inventory = result.get("event_inventory", {})
    if all_events_total > 0:
        logger.info("\n[事件总览]")
        logger.info("  总事件数(含 role/runtime/audit): %d", all_events_total)
        for source_name in ("audit", "runtime", "role"):
            source_stats = event_inventory.get(source_name, {}) if isinstance(event_inventory, dict) else {}
            files = int(source_stats.get("files", 0) or 0)
            events = int(source_stats.get("events", 0) or 0)
            if files > 0 or events > 0:
                logger.info("  - %-7s files=%3d events=%d", source_name, files, events)
        invalid_lines = int(result.get("invalid_event_lines", 0) or 0)
        read_errors = int(result.get("read_errors", 0) or 0)
        if invalid_lines > 0:
            logger.info("  - invalid_lines=%d", invalid_lines)
        if read_errors > 0:
            logger.info("  - read_errors=%d", read_errors)

    if result["canonical_files"]:
        logger.info("\n[Canonical 文件] (%d 个)", len(result["canonical_files"]))
        for f in result["canonical_files"][:3]:
            logger.info("  - %s", Path(f).name)

    if result["index_files"]:
        logger.info("\n[索引文件] (%d 个)", len(result["index_files"]))
        for f in result["index_files"]:
            logger.info("  - %s", Path(f).name)

    if result["corruption_file"] and result["corruption_file"]["exists"]:
        logger.info("\n[损坏记录] ✗ 存在损坏事件记录")

    # 显示工厂事件信息
    if result.get("factory_events_found"):
        logger.info("\n[工厂运行事件] ✓ 找到")
        logger.info("  最新运行: %s", result.get("latest_run_id", "unknown"))
        logger.info("  事件数量: %s", result.get("factory_event_count", 0))
        logger.info("  事件路径: %s", result.get("factory_events_path", ""))
        logger.info("\n  提示: 工厂运行事件存储在 factory/{run_id}/events/")
        logger.info("       这不是审计事件（runtime/audit/），查询方式不同")
    elif result.get("factory_checked_dirs"):
        logger.info("\n[工厂运行事件] 未找到 (%s)", result.get("factory_lookup_reason", "unknown"))
        for factory_dir in result["factory_checked_dirs"][:3]:
            logger.info("  - 已检查: %s", factory_dir)
        if len(result["factory_checked_dirs"]) > 3:
            logger.info("  ... 还有 %d 个目录", len(result["factory_checked_dirs"]) - 3)

    # 显示其他候选 runtime 目录
    if result.get("alternative_runtimes"):
        logger.info("\n[其他候选 Runtime 目录]")
        for alt in result["alternative_runtimes"]:
            logger.info("  • %s", alt)
        logger.info("\n  提示: 尝试使用 --root 指定其他目录")

    if result["recommendations"]:
        logger.info("\n[建议]")
        for rec in result["recommendations"]:
            logger.info("  • %s", rec)

    logger.info("")


def format_failure_compact(event: dict[str, Any], use_relative_time: bool = True) -> str:
    """格式化失败事件为紧凑显示格式。

    Args:
        event: 事件字典
        use_relative_time: 是否使用相对时间

    Returns:
        格式化的字符串
    """
    ts = format_relative_time(event.get("timestamp", "")) if use_relative_time else event.get("timestamp", "")[:19]
    event_type = event.get("event_type", "unknown")
    source = event.get("source", {})
    role = source.get("role", "unknown") if isinstance(source, dict) else "unknown"
    action = event.get("action", {}) or {}
    name = action.get("name", "")
    error = action.get("error", "")

    lines = [
        f"[{ts:12}] [{role:12}] {event_type}",
        f"  操作: {name}",
    ]
    if error:
        lines.append(f"  错误: {error}")
    lines.append("")
    return "\n".join(lines)


def format_health_compact(result: dict[str, Any], runtime_root: Path) -> str:
    """格式化健康检查结果为紧凑显示格式。

    Args:
        result: 健康检查结果字典
        runtime_root: runtime 根目录

    Returns:
        格式化的字符串
    """
    status_emoji = {"healthy": "✓", "degraded": "⚠", "unhealthy": "✗"}
    overall = result.get("overall", "unknown")
    lines = [
        f"{status_emoji.get(overall, '?')} Health: {overall}",
        f"Runtime: {runtime_root}\n",
    ]

    for check_name, check_result in result.get("checks", {}).items():
        status = check_result.get("status", "unknown")
        emoji = {"ok": "✓", "error": "✗", "warning": "⚠", "info": "ℹ"}.get(status, "?")
        message = check_result.get("message", "")
        lines.append(f"  {emoji} {check_name}: {message}")

    return "\n".join(lines)


def format_triage_compact(result: dict[str, Any]) -> str:
    """格式化排障结果为紧凑显示格式。

    Args:
        result: 排障结果字典

    Returns:
        格式化的字符串
    """
    status = result.get("status")
    mode = result.get("mode", "unknown")
    lines: list[str] = []

    if status in {"success", "partial"}:
        lines.append(f"Triage report (mode: {mode})")
        lines.append(f"  Run ID: {result.get('run_id')}")
        lines.append(f"  Task ID: {result.get('task_id')}")
        lines.append(f"  Generated: {result.get('generated_at')}")

        pm_history = result.get("pm_quality_history", [])
        lines.append(f"\n  PM events: {len(pm_history)}")

        tool_audit = result.get("director_tool_audit", {})
        lines.append(f"  Tool calls: {tool_audit.get('total', 0)}")
        lines.append(f"  Tool failures: {tool_audit.get('failed', 0)}")

        hops = result.get("failure_hops") or {}
        if hops.get("has_failure"):
            lines.append("\n  Failure detected!")
            lines.append(f"    Code: {hops.get('failure_code')}")
            if hop1 := hops.get("hop1_phase"):
                lines.append(f"    Phase: {hop1.get('phase')} ({hop1.get('actor')})")

    elif status == "not_found":
        lines.append(f"No events found for specified key (mode: {mode})\n")

        if "help_message" in result:
            lines.append(f"{result['help_message']}\n")

        if "suggestions" in result:
            lines.append("建议操作:")
            for i, suggestion in enumerate(result["suggestions"], 1):
                lines.append(f"  {i}. {suggestion}")
    else:
        lines.append(f"Error: {result.get('error')} (mode: {mode})")

    return "\n".join(lines)


def format_search_errors_compact(
    chains: list[Any],
    *,
    pattern: str,
    time_window: str,
    show_args: bool = False,
    show_output: bool = False,
    link_chains: bool = False,
    context: int = 0,
) -> str:
    """格式化错误搜索结果为紧凑显示格式。

    Args:
        chains: 错误链条列表
        pattern: 搜索模式
        time_window: 时间范围
        show_args: 是否显示参数
        show_output: 是否显示完整输出
        link_chains: 是否显示事件链条
        context: 上下文事件数量

    Returns:
        格式化的字符串
    """
    lines: list[str] = [
        f"\n找到 {len(chains)} 个错误链条\n",
        f"时间范围: {time_window}\n",
    ]

    for i, chain in enumerate(chains, 1):
        lines.append(f"=== Error Chain {i}: {chain.chain_id[:20]}... ===")

        # Time
        ts = chain.failure_event.ts
        if ts:
            ts_display = format_relative_time(ts)
            lines.append(f"Time: {ts} ({ts_display})")

        # Tool info
        if chain.tool_name:
            lines.append(f"Tool: {chain.tool_name}")

        # Phase
        phase = chain.failure_event.refs.get("phase", "")
        if phase:
            lines.append(f"Phase: {phase}")

        # Run/Task
        run_id = chain.failure_event.refs.get("run_id", "")
        task_id = chain.failure_event.refs.get("task_id", "")
        if run_id or task_id:
            lines.append(f"Run: {run_id or 'N/A'} | Task: {task_id or 'N/A'}")

        lines.append("")

        # Input (tool arguments)
        if show_args and chain.tool_args:
            lines.append("[INPUT] 调用参数:")
            args_str = " ".join(str(a) for a in chain.tool_args)
            lines.append(f"  $ {chain.tool_name} {args_str}")
            lines.append("")

        # Output (error)
        lines.append("[OUTPUT] 错误输出:")
        if chain.failure_reason:
            lines.append(f"  ✗ {chain.failure_reason}")

        if chain.failure_event.output:
            output = chain.failure_event.output
            if isinstance(output, dict):
                exit_code = output.get("exit_code")
                if exit_code is not None:
                    lines.append(f"  Exit code: {exit_code}")

                stderr = output.get("stderr", "")
                if stderr and show_output:
                    lines.append("\n  Stderr:")
                    for line in stderr.split("\n")[:5]:
                        lines.append(f"    {line}")
                    if len(stderr.split("\n")) > 5:
                        lines.append("    ... (truncated)")
        lines.append("")

        # Chain timeline
        if link_chains and chain.timeline:
            lines.append("[CHAIN] 事件链条:")
            for link in chain.timeline[-5:]:  # Show last 5
                ts_short = link.ts[11:19] if link.ts else ""
                kind_icon = "▶" if link.kind == "action" else "◀"
                status_icon = "✓" if link.ok else "✗" if link.ok is False else "•"
                lines.append(f"  {ts_short} [{kind_icon}] {link.actor:12} {link.name:20} {status_icon}")
            lines.append("")

        # Context events
        if context > 0 and chain.context_events:
            lines.append(f"[CONTEXT] 相关事件 (显示最近 {min(context, len(chain.context_events))} 个):")
            context_to_show = sorted(chain.context_events, key=lambda x: x.ts_epoch, reverse=True)[:context]

            for ctx in context_to_show:
                ts_short = ctx.ts[11:19] if ctx.ts else ""
                lines.append(f"  {ts_short} [{ctx.kind:12}] {ctx.actor:12} {ctx.name}")
            lines.append("")

        lines.append("-" * 60)
        lines.append("")

    return "\n".join(lines)


def format_factory_events_compact(
    collection: dict[str, Any],
    *,
    use_relative_time: bool = True,
    no_relative_time: bool = False,
) -> str:
    """格式化工厂事件为紧凑显示格式。

    Args:
        collection: 工厂事件收集结果
        use_relative_time: 是否使用相对时间
        no_relative_time: 是否禁用相对时间

    Returns:
        格式化的字符串
    """
    lines: list[str] = []
    total_events = 0
    runs = collection.get("runs", [])

    for run in runs:
        run_id = str(run.get("run_id", "unknown"))
        total_run_events = int(run.get("total_events", 0) or 0)
        display_events = run.get("events", [])
        total_events += total_run_events

        lines.append(f"\n=== {run_id} ===")
        lines.append(f"events_file: {run.get('events_file', '')}")

        for evt in display_events:
            ts = str(evt.get("timestamp", ""))
            if ts and use_relative_time and not no_relative_time:
                ts_display = format_relative_time(ts)
            else:
                ts_display = ts[:19] if ts else ""

            evt_type = str(evt.get("type", "unknown"))
            stage = str(evt.get("stage", ""))
            message = str(evt.get("message", ""))[:80]
            lines.append(f"{ts_display:12} [{evt_type:20}] {stage:15} {message}")

        if total_run_events > len(display_events):
            lines.append(f"  ... 还有 {total_run_events - len(display_events)} 个事件")

        invalid_lines = int(run.get("invalid_lines", 0) or 0)
        read_errors = int(run.get("read_errors", 0) or 0)
        if invalid_lines > 0:
            lines.append(f"  [警告] 忽略损坏行: {invalid_lines}")
        if read_errors > 0:
            lines.append(f"  [警告] 读取错误: {read_errors}")

    lines.append(f"\n总计: {total_events} 个事件")
    return "\n".join(lines)


def format_journal_events_compact(
    events: list[dict[str, Any]],
    *,
    use_relative_time: bool = True,
) -> str:
    """格式化 journal 事件为紧凑显示格式。

    Args:
        events: 事件列表
        use_relative_time: 是否使用相对时间

    Returns:
        格式化的字符串
    """
    lines: list[str] = ["\n--- Journal 事件 (fallback 模式) ---"]

    for evt in events:
        ts = evt.get("ts", evt.get("timestamp", ""))
        ts = format_relative_time(ts) if use_relative_time else (ts[11:19] if ts else "")
        evt_type = evt.get("kind", "unknown")
        lines.append(f"{ts:12} [journal    ] {evt_type:20}")

    return "\n".join(lines)


def format_json_output(data: Any) -> str:
    """格式化数据为 JSON 输出。

    Args:
        data: 要格式化的数据

    Returns:
        JSON 格式字符串
    """
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)
