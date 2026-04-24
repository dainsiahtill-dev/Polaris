#!/usr/bin/env python3
"""Audit Agent Toolkit 使用示例

展示如何在 Agent 中简洁地使用审计功能。
"""

import logging
import sys
from pathlib import Path


def _bootstrap_backend_import_path():
    """Lazy import of polaris modules after path bootstrap."""
    if __package__:
        # Already in a package, imports should work
        pass
    else:
        # Running as script - ensure backend is in path
        backend_root = Path(__file__).resolve().parents[4]
        backend_root_str = str(backend_root)
        if backend_root_str not in sys.path:
            sys.path.insert(0, backend_root_str)

    from polaris.delivery.cli.audit.audit_agent import (
        AuditContext,
        get_events,
        get_stats,
        triage,
        verify,
    )

    return AuditContext, get_events, get_stats, triage, verify


logger = logging.getLogger(__name__)


def example_basic_usage() -> None:
    """基本用法示例"""
    AuditContext, _, _, _, verify = _bootstrap_backend_import_path()  # noqa: N806
    logger.info("=== 基本用法示例 ===\n")

    # 1. 自动发现 runtime 目录
    ctx = AuditContext(workspace=".")
    logger.info(f"自动发现的 runtime 目录: {ctx.runtime_root}")
    logger.info(f"离线模式可用: {ctx.is_offline_available()}\n")

    # 2. 验证审计链 - 一行代码
    result = verify()
    logger.info(f"链验证结果: {result['status']}")
    logger.info(f"链有效: {result['chain_valid']}")
    logger.info(f"总事件数: {result['total_events']}")
    logger.info(f"运行模式: {result['mode']}\n")


def example_triage() -> None:
    """排障包生成示例"""
    _AuditContext, _, _, triage, _ = _bootstrap_backend_import_path()  # noqa: N806
    logger.info("=== 排障包生成示例 ===\n")

    # 指定 run_id 生成排障包
    result = triage(run_id="factory_123")

    if result["status"] in {"success", "partial"}:
        logger.info("排障包生成成功")
        logger.info(f"Run ID: {result.get('run_id')}")
        logger.info(f"Task ID: {result.get('task_id')}")
        logger.info(f"生成时间: {result.get('generated_at')}")

        # 查看 PM 质量历史
        pm_history = result.get("pm_quality_history", [])
        logger.info(f"\nPM 质量历史事件数: {len(pm_history)}")

        # 查看工具审计
        tool_audit = result.get("director_tool_audit", {})
        logger.info(f"工具调用总数: {tool_audit.get('total', 0)}")
        logger.info(f"工具调用失败: {tool_audit.get('failed', 0)}")
    else:
        logger.info(f"排障包生成失败: {result.get('error')}")


def example_query_events() -> None:
    """事件查询示例"""
    _, get_events, _, _, _ = _bootstrap_backend_import_path()
    logger.info("\n=== 事件查询示例 ===\n")

    # 获取最近 10 个事件
    result = get_events(limit=10)
    logger.info(f"查询状态: {result['status']}")
    logger.info(f"获取事件数: {result['count']}")
    logger.info(f"运行模式: {result['mode']}")

    # 遍历事件
    for event in result.get("events", []):
        ts = event.get("timestamp", "")[11:19] if event.get("timestamp") else ""
        event_type = event.get("event_type", "unknown")
        source = event.get("source", {})
        role = source.get("role", "unknown") if isinstance(source, dict) else "unknown"
        logger.info(f"  {ts} [{role}] {event_type}")


def example_stats() -> None:
    """统计信息示例"""
    _, _, get_stats, _, _ = _bootstrap_backend_import_path()
    logger.info("\n=== 统计信息示例 ===\n")

    result = get_stats()
    logger.info(f"查询状态: {result['status']}")

    stats = result.get("stats", {})
    logger.info(f"总事件数: {stats.get('total_events', 0)}")

    # 事件类型分布
    event_types = stats.get("event_types", {})
    if event_types:
        logger.info("\n事件类型分布:")
        for event_type, count in sorted(event_types.items(), key=lambda x: -x[1]):
            logger.info(f"  {event_type}: {count}")

    # 来源分布
    sources = stats.get("sources", {})
    if sources:
        logger.info("\n来源分布:")
        for source, count in sorted(sources.items(), key=lambda x: -x[1]):
            logger.info(f"  {source}: {count}")


def example_error_handling() -> None:
    """错误处理示例"""
    _, _, _, _, verify = _bootstrap_backend_import_path()
    logger.info("\n=== 错误处理示例 ===\n")

    # 使用不存在的 runtime 目录
    result = verify(runtime_root="/nonexistent/path")

    if result["status"] == "error":
        logger.info(f"预期内的错误: {result.get('error')}")
        logger.info(f"运行模式: {result.get('mode')}")
    else:
        logger.info(f"结果: {result}")


if __name__ == "__main__":
    example_basic_usage()
    example_triage()
    example_query_events()
    example_stats()
    example_error_handling()
