"""Adapter-only bridge from deterministic repair plans to Director tools."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from polaris.cells.director.runtime.public import (
    RunDirectorRepairCommandV1,
    run_director_repair,
)


def run_runtime_repair_with_director_tools(
    adapter: Any,
    *,
    workspace_path: Path,
    task_id: str,
    source_tool: str,
    executor_factory: Callable[..., Any],
    base_files: Mapping[str, str],
    artifact_quality_errors: Sequence[str] = (),
    allowed_paths: Sequence[str] | None = None,
    use_editor: bool = True,
) -> list[dict[str, Any]]:
    """Execute a runtime repair while preserving Director as the effect owner."""

    if not base_files:
        return []

    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    executor = executor_factory(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    )
    write_results: dict[str, dict[str, Any]] = {}
    edit_results: dict[str, dict[str, Any]] = {}

    def _mark_progress(path: str) -> None:
        with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
            adapter._update_task_progress(task_id, "executing", current_file=path)

    def _policy_gated_writer(path: str, content: str) -> dict[str, Any]:
        write_result = executor.execute_tool(
            "write_file",
            {"file": path, "content": content},
            task_id=task_id,
        )
        write_results[path] = dict(write_result)
        if bool(write_result.get("ok")):
            _mark_progress(path)
        return dict(write_result)

    def _policy_gated_editor(operation: Any) -> dict[str, Any]:
        path = str(getattr(operation, "path", "") or "")
        edit_result = executor.execute_tool(
            "edit_file",
            {
                "file": path,
                "search": str(getattr(operation, "expected", "") or ""),
                "replace": str(getattr(operation, "replacement", "") or ""),
            },
            task_id=task_id,
        )
        edit_results[path] = dict(edit_result)
        if bool(edit_result.get("ok")):
            _mark_progress(path)
        return dict(edit_result)

    canonical_result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id=task_id,
            workspace=str(workspace_path),
            source_tool=source_tool,
            artifact_quality_errors=tuple(str(item) for item in artifact_quality_errors),
            base_files=dict(base_files),
            allowed_paths=tuple(allowed_paths or base_files.keys()),
        ),
        writer=_policy_gated_writer,
        editor=_policy_gated_editor if use_editor else None,
    )
    if not canonical_result.ok:
        if canonical_result.error_code == "repair_not_planned":
            return []
        return [_project_failed_repair(source_tool=source_tool, canonical_result=canonical_result)]

    results: list[dict[str, Any]] = []
    for receipt in canonical_result.receipts:
        for patch_path in receipt.files_changed:
            tool_result = edit_results.get(patch_path) or write_results.get(patch_path, {})
            tool_name = "edit_file" if patch_path in edit_results else "write_file"
            if not bool(tool_result.get("ok")) and receipt.authoritative:
                continue
            bytes_written = tool_result.get("bytes_written")
            if bytes_written is None:
                full_path = (workspace_path / patch_path).resolve()
                with contextlib.suppress(OSError, ValueError):
                    bytes_written = len(full_path.read_text(encoding="utf-8").encode("utf-8"))
            _mark_progress(patch_path)
            results.append(
                {
                    "tool": tool_name,
                    "tool_name": tool_name,
                    "success": True,
                    "result": {
                        "ok": True,
                        "source_tool": receipt.source_tool,
                        "file": patch_path,
                        "bytes_written": int(bytes_written or 0),
                        "operation": str(tool_result.get("operation") or tool_name),
                        "before_hash": str(receipt.before_hashes.get(patch_path) or ""),
                        "after_hash": str(receipt.after_hashes.get(patch_path) or ""),
                        "broadcast_ok": bool(tool_result.get("broadcast_ok")),
                        "director_policy": tool_result.get("director_policy"),
                        "repair_kernel": _project_receipt_kernel(receipt=receipt, canonical_result=canonical_result),
                    },
                }
            )
    return results


def _project_failed_repair(*, source_tool: str, canonical_result: Any) -> dict[str, Any]:
    return {
        "tool": "director_repair_kernel",
        "tool_name": "director_repair_kernel",
        "success": False,
        "result": {
            "ok": False,
            "source_tool": source_tool,
            "error_code": canonical_result.error_code,
            "error_message": canonical_result.error_message,
            "repair_kernel": {
                "owner_cell": "director.runtime",
                "receipts": [receipt.to_dict() for receipt in canonical_result.receipts],
                "planning": dict(canonical_result.metadata.get("planning") or {}),
                "planning_error": dict(canonical_result.metadata.get("planning_error") or {}),
                "plan_policy": dict(canonical_result.metadata.get("plan_policy") or {}),
                "composition_policy": dict(canonical_result.metadata.get("composition_policy") or {}),
                "execution_error": canonical_result.metadata.get("execution_error"),
                "rolled_back": bool(canonical_result.metadata.get("rolled_back")),
            },
        },
    }


def _project_receipt_kernel(*, receipt: Any, canonical_result: Any) -> dict[str, Any]:
    return {
        "owner_cell": "director.runtime",
        "receipt_id": receipt.receipt_id,
        "plan_id": receipt.plan_id,
        "status": receipt.status,
        "authoritative": receipt.authoritative,
        "authority_hash": receipt.authority_hash,
        "projection_hash": receipt.projection_hash,
        "before_hashes": dict(receipt.before_hashes),
        "after_hashes": dict(receipt.after_hashes),
        "round_number": receipt.round_number,
        "errors_before": receipt.errors_before,
        "errors_after": receipt.errors_after,
        "net_error_reduction": receipt.net_error_reduction,
        "revalidation_evidence": dict(receipt.revalidation_evidence),
        "metadata": dict(receipt.metadata),
        "planning": dict(canonical_result.metadata.get("planning") or {}),
        "plan_policy": dict(canonical_result.metadata.get("plan_policy") or {}),
        "composition_policy": dict(canonical_result.metadata.get("composition_policy") or {}),
    }
