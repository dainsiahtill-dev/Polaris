"""Receipt 工具函数 — 跨模块共享的 receipt 处理逻辑。

本模块独立于具体执行器，供 tool_batch_executor 和 retry_orchestrator 共同使用，
避免子模块之间的循环/交叉依赖。
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.transaction.constants import (
    WRITE_TOOLS as _WRITE_TOOLS,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger


def record_receipts_to_ledger(receipts: list[dict], ledger: TurnLedger) -> None:
    """将批执行收据写回 ledger，确保指标统计与真实执行一致。"""
    for receipt in receipts:
        results = receipt.get("results", [])
        if not isinstance(results, list):
            continue
        for item in results:
            if not hasattr(item, "get"):
                continue
            tool_name = str(item.get("tool_name") or "unknown")
            call_id = str(item.get("call_id") or "")
            status = str(item.get("status") or "unknown")
            duration_ms = int(item.get("execution_time_ms") or 0)
            ledger.record_tool_execution(tool_name, call_id, status, duration_ms)
            # 记录 write receipt 到 mutation obligation
            if status == "success" and _is_write_tool(tool_name):
                ledger.mutation_obligation.record_write_receipt()


def _is_write_tool(tool_name: str) -> bool:
    """判定工具是否为写工具（委托给 constants.WRITE_TOOLS，避免重复维护）。"""
    normalized = tool_name.lower().replace("-", "_")
    return normalized in _WRITE_TOOLS
