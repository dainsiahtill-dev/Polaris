"""CLI entry point for audit quick.

This module contains the argument parser and main command dispatcher.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from polaris.delivery.cli.audit.audit.file_ops import bootstrap_backend_import_path, discover_latest_runtime
from polaris.delivery.cli.audit.audit.formatters import parse_relative_time
from polaris.delivery.cli.audit.audit.handlers import (
    handle_corruption,
    handle_diagnose,
    handle_diff,
    handle_events,
    handle_export,
    handle_factory_events,
    handle_failures,
    handle_health,
    handle_query_repl,
    handle_search_errors,
    handle_show,
    handle_stats,
    handle_triage,
    handle_verify,
    handle_watch,
    handle_why,
)

logger = logging.getLogger(__name__)

# Bootstrap import path for direct execution
bootstrap_backend_import_path()


def _create_parser() -> argparse.ArgumentParser:
    """创建 argparse 解析器。

    Returns:
        配置好的 ArgumentParser
    """
    parser = argparse.ArgumentParser(
        description="Audit Quick - 极简审计命令 (增强版)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基础命令
  %(prog)s verify                  # 验证审计链
  %(prog)s stats                   # 查看统计
  %(prog)s events                  # 查看最近事件

  # UEP v2.0 Journal 查询 (标准化日志)
  %(prog)s events --journal --discover              # 查询 Journal 事件
  %(prog)s events --journal --root /path/to/runtime # 指定 runtime 查询 Journal

  # 自动发现
  %(prog)s verify --discover       # 自动发现最新项目

  # 实时监控
  %(prog)s watch --discover        # 自动发现并实时监控事件流
  %(prog)s watch --root X:/.../runtime --interval 2  # 指定 runtime，每2秒刷新

  # 失败定位
  %(prog)s failures --discover     # 自动发现并查看失败事件
  %(prog)s failures --since 1h     # 最近1小时的失败

  # 健康检查
  %(prog)s health                  # 系统健康检查

  # 诊断
  %(prog)s diagnose                # 诊断 runtime 目录结构

  # 导出
  %(prog)s export --discover -o report.csv                 # 自动发现并导出CSV
  %(prog)s export --root X:/.../runtime --export-format json -o data.json

  # 时间范围过滤
  %(prog)s events --since 1h       # 最近1小时
  %(prog)s events --since yesterday --until now

  # 排障
  %(prog)s triage -r factory_xxx   # 生成排障包

  # 工厂运行事件（与审计事件存储位置不同）
  %(prog)s factory-events          # 查看最近的工厂运行事件
  %(prog)s factory-events -r factory_xxx -n 20  # 查看指定运行的20个事件

  # 错误链条追溯（新增）
  %(prog)s search-errors --pattern "Tool returned unsuccessful result"  # 搜索错误
  %(prog)s search-errors --pattern "repo_rg" --link-chains --show-args  # 显示参数
  %(prog)s search-errors --pattern "timeout" --strategy regex --since 1h  # 正则搜索

  # 查看事件详情
  %(prog)s show <event_id> --root X:/.../runtime   # 查看单事件详情

  # 运行对比
  %(prog)s diff --run-a run_001 --run-b run_002    # 对比两个运行的事件差异

  # 失败原因分析
  %(prog)s why --task task_001                     # 分析任务失败原因

  # 交互式查询 REPL
  %(prog)s query-repl --root X:/.../runtime        # 启动交互式查询

环境变量:
  KERNELONE_RUNTIME_BASE    # 自动检测 runtime 目录
  KERNELONE_BACKEND_PORT    # 后端端口 (默认 49977)
        """,
    )

    parser.add_argument(
        "command",
        choices=[
            "verify",
            "stats",
            "events",
            "triage",
            "corruption",
            "watch",
            "failures",
            "health",
            "export",
            "show",
            "discover",
            "diagnose",
            "factory-events",
            "search-errors",
            "diff",
            "why",
            "query-repl",
        ],
        help="要执行的命令",
    )
    # search-errors 参数
    parser.add_argument("--pattern", help="错误匹配模式 (用于 search-errors)")
    parser.add_argument(
        "--strategy", choices=["exact", "substring", "regex", "fuzzy"], default="substring", help="匹配策略"
    )

    # diff 参数
    parser.add_argument("--run-a", dest="run_a", help="First run ID (用于 diff)")
    parser.add_argument("--run-b", dest="run_b", help="Second run ID (用于 diff)")

    # why 参数
    parser.add_argument("--task", dest="task_id", help="Task ID to investigate (用于 why)")
    parser.add_argument("--context", type=int, default=5, help="上下文事件数量")
    parser.add_argument("--link-chains", action="store_true", help="自动关联 action/observation")
    parser.add_argument("--show-args", action="store_true", help="显示调用参数")
    parser.add_argument("--show-output", action="store_true", help="显示完整输出")
    parser.add_argument("-r", "--run-id", help="Run ID (用于 triage)")
    parser.add_argument("-t", "--task-id", help="Task ID (用于 triage)")
    parser.add_argument("-n", "--limit", type=int, default=50, help="限制数量")
    parser.add_argument("--root", help="Runtime 根目录 (可选，自动检测)")
    parser.add_argument("--discover", action="store_true", help="自动发现最新项目")
    parser.add_argument("--mode", choices=["auto", "online", "offline"], default="auto")
    parser.add_argument("-f", "--format", choices=["json", "compact"], default="compact", help="输出格式")

    # 新增参数
    parser.add_argument("--since", help="起始时间 (如: 1h, 30m, yesterday, 2024-01-01T00:00:00)")
    parser.add_argument("--until", help="结束时间 (如: now, today)")
    parser.add_argument("--interval", type=float, default=1.0, help="监控刷新间隔(秒)")
    parser.add_argument("--event-type", help="事件类型过滤")
    parser.add_argument("-o", "--output", help="导出文件路径")
    parser.add_argument("--export-format", choices=["json", "csv"], help="导出格式（仅 export 命令）")
    parser.add_argument("--strict-non-empty", action="store_true", help="verify 严格模式：要求至少存在 1 条审计事件")
    parser.add_argument("event_id", nargs="?", help="事件ID (用于 show 命令)")
    parser.add_argument("--no-relative-time", action="store_true", help="禁用相对时间显示")
    parser.add_argument("--journal", action="store_true", help="查询 Journal 事件 (UEP v2.0 标准化日志)")

    return parser


def main() -> None:
    """CLI 主入口。"""
    parser = _create_parser()
    args = parser.parse_args()

    # 自动发现模式
    runtime_root: Path | None = None
    if args.discover or (args.command == "discover"):
        discovered = discover_latest_runtime()
        if discovered:
            logger.info("发现 runtime 目录: %s", discovered)
            if args.command == "discover":
                logger.info("%s", discovered)
                return
            runtime_root = discovered
        else:
            logger.error("错误: 未能自动发现 runtime 目录")
            logger.error("提示: 请手动指定 --root 或设置 KERNELONE_RUNTIME_BASE")
            sys.exit(1)

    if args.root:
        runtime_root = Path(args.root)

    # 关键修复：用户显式指定 --root 时，强制使用 offline 模式
    if args.root and args.mode == "auto":
        args.mode = "offline"

    # 解析时间范围
    since_dt = parse_relative_time(args.since) if args.since else None
    until_dt = parse_relative_time(args.until) if args.until else None

    # 执行命令
    exit_code = 0
    if args.command == "verify":
        exit_code = handle_verify(args, runtime_root)
    elif args.command == "stats":
        exit_code = handle_stats(args, runtime_root)
    elif args.command == "events":
        exit_code = handle_events(args, runtime_root)
    elif args.command == "watch":
        exit_code = handle_watch(args, runtime_root)
    elif args.command == "failures":
        exit_code = handle_failures(args, runtime_root, since_dt, until_dt)
    elif args.command == "health":
        exit_code = handle_health(args, runtime_root)
    elif args.command == "export":
        exit_code = handle_export(args, runtime_root, since_dt, until_dt)
    elif args.command == "show":
        exit_code = handle_show(args, runtime_root)
    elif args.command == "triage":
        exit_code = handle_triage(args, runtime_root)
    elif args.command == "corruption":
        exit_code = handle_corruption(args, runtime_root)
    elif args.command == "diagnose":
        exit_code = handle_diagnose(args, runtime_root)
    elif args.command == "factory-events":
        exit_code = handle_factory_events(args, runtime_root)
    elif args.command == "search-errors":
        exit_code = handle_search_errors(args, runtime_root, since_dt, until_dt)
    elif args.command == "diff":
        exit_code = handle_diff(args, runtime_root)
    elif args.command == "why":
        exit_code = handle_why(args, runtime_root)
    elif args.command == "query-repl":
        exit_code = handle_query_repl(args, runtime_root)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
