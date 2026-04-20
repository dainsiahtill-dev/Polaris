"""Runtime diagnosis functions for audit quick CLI.

This module contains functions for diagnosing runtime directory structure
and providing troubleshooting guidance.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from polaris.delivery.cli.audit.audit.factory_ops import collect_factory_events
from polaris.delivery.cli.audit.audit.file_ops import collect_runtime_event_inventory

logger = logging.getLogger(__name__)


def diagnose_runtime(runtime_root: Path) -> dict[str, Any]:
    """详细诊断 runtime 目录结构和状态。

    Args:
        runtime_root: runtime 根目录

    Returns:
        诊断结果字典
    """
    resolved_root = runtime_root.resolve()
    result: dict[str, Any] = {
        "runtime_root": str(resolved_root),
        "runtime_exists": resolved_root.exists(),
        "runtime_is_dir": resolved_root.is_dir() if resolved_root.exists() else False,
        "audit_dir": None,
        "events_dir": None,
        "log_files": [],
        "canonical_files": [],
        "index_files": [],
        "corruption_file": None,
        "total_events": 0,
        "audit_event_count": 0,
        "all_events_total": 0,
        "invalid_event_lines": 0,
        "read_errors": 0,
        "event_inventory": {},
        "recommendations": [],
        "factory_events_found": False,
        "factory_events_path": None,
        "all_event_files": [],
        "alternative_runtimes": [],
    }

    # 搜索其他可能的 runtime 目录
    alternative_candidates = [
        Path("X:/") / "hp_stress_workspace" / "runtime",
        Path("X:/") / "tests-agent-stress-runtime" / "runtime",
        Path("C:/Temp/tests-agent-stress-backend") / "runtime",
        Path("C:/Temp/tests-agent-stress-runtime") / "runtime",
    ]
    for candidate in alternative_candidates:
        if candidate.resolve() != resolved_root and candidate.exists():
            result["alternative_runtimes"].append(str(candidate))

    if not resolved_root.exists():
        result["recommendations"].append(f"Runtime 目录不存在: {resolved_root}")
        result["recommendations"].append("检查路径是否正确，或运行压测生成事件")
        return result

    if not resolved_root.is_dir():
        result["recommendations"].append(f"路径不是目录: {resolved_root}")
        return result

    inventory = collect_runtime_event_inventory(resolved_root)
    result["all_event_files"] = list(inventory.get("all_event_files", []))
    result["all_events_total"] = int(inventory.get("total_events", 0))
    result["invalid_event_lines"] = int(inventory.get("invalid_lines", 0))
    result["read_errors"] = int(inventory.get("read_errors", 0))
    result["event_inventory"] = dict(inventory.get("by_source", {}))

    # 检查 audit 目录
    audit_dir = resolved_root / "audit"
    result["audit_dir"] = {
        "path": str(audit_dir),
        "exists": audit_dir.exists(),
    }

    if not audit_dir.exists():
        result["recommendations"].append("audit 目录不存在，可能尚未生成任何审计事件")
        result["recommendations"].append("确认 Polaris 压测或运行已正确配置审计存储")
    else:
        log_files = list(result["event_inventory"].get("audit", {}).get("paths", []))
        result["log_files"] = log_files

        canonical_files = list(audit_dir.glob("canonical.*.jsonl"))
        result["canonical_files"] = [str(f) for f in canonical_files]

        index_files = list(audit_dir.glob("index.*.json"))
        result["index_files"] = [str(f) for f in index_files]

        corruption_file = audit_dir / "corruption.events.jsonl"
        result["corruption_file"] = {
            "path": str(corruption_file),
            "exists": corruption_file.exists(),
        }

        audit_stats = result["event_inventory"].get("audit", {})
        audit_events = int(audit_stats.get("events", 0))
        result["total_events"] = audit_events
        result["audit_event_count"] = audit_events

        if not log_files and result["all_events_total"] > 0:
            result["recommendations"].append(
                "发现 runtime 事件但未落入 audit-YYYY-MM.jsonl（可能是审计存储链路未接通）"
            )
            result["recommendations"].append("建议检查审计写入配置与 runtime/audit 目录权限，确保审计事件可持久化")
        elif not log_files:
            result["recommendations"].append("未找到 audit-YYYY-MM.jsonl 日志文件")
            result["recommendations"].append("可能原因: 1) 尚未运行压测 2) 审计存储配置错误 3) 事件写入失败")
        elif audit_events == 0:
            result["recommendations"].append("找到日志文件但没有事件记录")
            result["recommendations"].append("可能原因: 1) 事件写入被禁用 2) 所有事件写入失败")

    # 检查 events 目录（旧格式兼容）
    events_dir = resolved_root / "events"
    result["events_dir"] = {
        "path": str(events_dir),
        "exists": events_dir.exists(),
    }

    factory_probe = collect_factory_events(
        resolved_root,
        limit_per_run=1,
        max_runs=1,
    )
    result["factory_lookup_reason"] = str(factory_probe.get("reason", "unknown"))
    result["factory_checked_dirs"] = list(factory_probe.get("checked_factory_dirs", []))
    if factory_probe.get("status") == "ok":
        first_run = (factory_probe.get("runs") or [{}])[0]
        result["factory_events_found"] = True
        result["factory_events_path"] = first_run.get("events_file")
        result["latest_run_id"] = first_run.get("run_id")
        result["factory_event_count"] = int(first_run.get("total_events", 0))

    if result["read_errors"] > 0:
        result["recommendations"].append("部分事件文件读取失败，建议检查路径权限与文件占用状态")
    if result["invalid_event_lines"] > 0:
        result["recommendations"].append("发现损坏 JSONL 行，建议排查写入中断或并发写冲突")

    return result
