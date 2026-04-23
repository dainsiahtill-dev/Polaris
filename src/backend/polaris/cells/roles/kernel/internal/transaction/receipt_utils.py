"""Receipt 工具函数 — 跨模块共享的 receipt 处理逻辑。

本模块独立于具体执行器，供 tool_batch_executor 和 retry_orchestrator 共同使用，
避免子模块之间的循环/交叉依赖。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from polaris.cells.roles.kernel.internal.transaction.constants import (
    READ_TOOLS as _READ_TOOLS,
    WRITE_TOOLS as _WRITE_TOOLS,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.write_authority import (
    is_authoritative_write_result,
)

_DIRECT_READ_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "repo_read_head",
        "repo_read_slice",
        "repo_read_tail",
        "repo_read_around",
        "repo_read_range",
    }
)


def _to_plain_mapping(payload: Any) -> dict[str, Any]:
    """Convert mapping-like receipt payloads into plain dict objects."""
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return dict(payload)
    if isinstance(payload, Mapping):
        return {str(key): value for key, value in payload.items()}

    model_dump = getattr(payload, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(mode="python")
        except TypeError:
            dumped = model_dump()
        if isinstance(dumped, dict):
            return {str(key): value for key, value in dumped.items()}

    to_dict = getattr(payload, "to_dict", None)
    if callable(to_dict):
        dumped = to_dict()
        if isinstance(dumped, dict):
            return {str(key): value for key, value in dumped.items()}

    return {}


def _normalize_result_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    normalized_items: list[dict[str, Any]] = []
    for item in payload:
        normalized = _to_plain_mapping(item)
        if normalized:
            normalized_items.append(normalized)
    return normalized_items


def normalize_batch_receipt(receipt: Any) -> dict[str, Any]:
    """Normalize a BatchReceipt-like payload into a canonical plain dict."""
    normalized = _to_plain_mapping(receipt)
    if not normalized:
        return {}

    canonical = dict(normalized)
    canonical["results"] = _normalize_result_items(canonical.get("results"))
    canonical["raw_results"] = _normalize_result_items(canonical.get("raw_results"))
    canonical["batch_id"] = str(canonical.get("batch_id", "") or "")
    canonical["turn_id"] = str(canonical.get("turn_id", "") or "")
    canonical["success_count"] = int(canonical.get("success_count", 0) or 0)
    canonical["failure_count"] = int(canonical.get("failure_count", 0) or 0)
    canonical["pending_async_count"] = int(canonical.get("pending_async_count", 0) or 0)
    canonical["has_pending_async"] = bool(canonical.get("has_pending_async", False))
    return canonical


def normalize_batch_receipts(receipts: Iterable[Any]) -> list[dict[str, Any]]:
    """Normalize an iterable of receipts while dropping empty/invalid payloads."""
    normalized_receipts: list[dict[str, Any]] = []
    for receipt in receipts:
        normalized = normalize_batch_receipt(receipt)
        if normalized:
            normalized_receipts.append(normalized)
    return normalized_receipts


def merge_batch_receipts(receipts: Iterable[Any]) -> dict[str, Any] | None:
    """Merge multiple per-tool receipts into a single canonical batch receipt."""
    normalized_receipts = normalize_batch_receipts(receipts)
    if not normalized_receipts:
        return None

    merged_results: list[dict[str, Any]] = []
    merged_raw_results: list[dict[str, Any]] = []
    success_count = 0
    failure_count = 0
    pending_async_count = 0
    has_pending_async = False
    first_batch_id = ""
    turn_id = ""

    for receipt in normalized_receipts:
        if not first_batch_id:
            first_batch_id = str(receipt.get("batch_id", "") or "")
        if not turn_id:
            turn_id = str(receipt.get("turn_id", "") or "")

        merged_results.extend(_normalize_result_items(receipt.get("results")))
        merged_raw_results.extend(_normalize_result_items(receipt.get("raw_results")))
        success_count += int(receipt.get("success_count", 0) or 0)
        failure_count += int(receipt.get("failure_count", 0) or 0)
        pending_async_count += int(receipt.get("pending_async_count", 0) or 0)
        has_pending_async = has_pending_async or bool(receipt.get("has_pending_async", False))

    batch_id = first_batch_id or (f"{turn_id}_merged_batch" if turn_id else "merged_batch")
    return {
        "batch_id": batch_id,
        "turn_id": turn_id,
        "results": merged_results,
        "raw_results": merged_raw_results,
        "success_count": success_count,
        "failure_count": failure_count,
        "pending_async_count": pending_async_count,
        "has_pending_async": has_pending_async or pending_async_count > 0,
    }


def record_receipts_to_ledger(receipts: list[Any], ledger: TurnLedger) -> None:
    """将批执行收据写回 ledger，确保指标统计与真实执行一致。"""
    for receipt in normalize_batch_receipts(receipts):
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
            if status == "success" and _is_direct_read_tool(tool_name):
                ledger.mutation_obligation.record_read_receipt()
            # 记录 write receipt 到 mutation obligation
            if status == "success" and _is_write_tool(tool_name) and is_authoritative_write_result(item):
                ledger.mutation_obligation.record_write_receipt()


def _is_direct_read_tool(tool_name: str) -> bool:
    """判定工具是否为直接文件读取工具。"""
    normalized = tool_name.lower().replace("-", "_")
    return normalized in _READ_TOOLS and normalized in _DIRECT_READ_TOOLS


def _is_write_tool(tool_name: str) -> bool:
    """判定工具是否为写工具（委托给 constants.WRITE_TOOLS，避免重复维护）。"""
    normalized = tool_name.lower().replace("-", "_")
    return normalized in _WRITE_TOOLS
