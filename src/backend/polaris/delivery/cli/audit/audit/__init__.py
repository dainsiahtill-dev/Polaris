"""Audit Quick - 极简命令行接口 (增强版)

专为 Agent 设计，无需记忆复杂参数。

模块结构:
- cli.py: CLI 入口与参数解析
- handlers.py: 基础命令处理函数
- handlers_advanced.py: 高级诊断命令处理函数
- auditor.py: 核心审计逻辑
- diagnosis.py: 运行时诊断
- reporters.py: 报告生成与打印
- formatters.py: 格式化工具
- file_ops.py: 文件操作
- factory_ops.py: 工厂事件操作

用法:
    # 快速验证审计链
    python -m polaris.delivery.cli.audit.audit verify

    # 快速统计
    python -m polaris.delivery.cli.audit.audit stats

    # 快速查看事件
    python -m polaris.delivery.cli.audit.audit events

    # 指定 runtime 目录
    python -m polaris.delivery.cli.audit.audit verify --root X:/path/to/runtime

    # 自动发现最新项目
    python -m polaris.delivery.cli.audit.audit verify --discover
"""

from __future__ import annotations

from polaris.delivery.cli.audit.audit.auditor import (
    export_data,
    get_failures,
    health_check,
    run_diff,
    run_why,
    show_event,
    smart_triage,
)
from polaris.delivery.cli.audit.audit.cli import main
from polaris.delivery.cli.audit.audit.diagnosis import diagnose_runtime
from polaris.delivery.cli.audit.audit.factory_ops import collect_factory_events
from polaris.delivery.cli.audit.audit.file_ops import (
    collect_runtime_event_inventory,
    discover_latest_runtime,
    tail_jsonl_events,
    watch_events,
)
from polaris.delivery.cli.audit.audit.formatters import (
    format_event_compact,
    format_relative_time,
    format_time_window,
    parse_relative_time,
    parse_window,
    resolve_export_format,
)

__all__ = [
    # File operations
    "collect_factory_events",
    "collect_runtime_event_inventory",
    # Auditor functions
    "diagnose_runtime",
    "discover_latest_runtime",
    "export_data",
    # Formatters
    "format_event_compact",
    "format_relative_time",
    "format_time_window",
    "get_failures",
    "health_check",
    # CLI entry
    "main",
    "parse_relative_time",
    "parse_window",
    "resolve_export_format",
    "run_diff",
    "run_why",
    "show_event",
    "smart_triage",
    "tail_jsonl_events",
    "watch_events",
]
