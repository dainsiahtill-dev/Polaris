"""Adapter-only bridge from deterministic repair plans to Director tools."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from polaris.cells.director.runtime.public import (
    PlanDirectorRepairCommandV1,
    RepairAdvisoryV1,
    RunDirectorRepairCommandV1,
    RunDirectorRepairConvergenceCommandV1,
    plan_director_repair,
    run_director_repair,
    run_director_repair_convergence,
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
    advisor_notes: Sequence[RepairAdvisoryV1] = (),
    use_editor: bool = True,
    revalidator: Callable[[Any], Any] | None = None,
    convergence_verifier: Callable[[Any], Any] | None = None,
    max_rounds: int = 3,
) -> list[dict[str, Any]]:
    """Execute a runtime repair while preserving Director as the effect owner."""

    if not base_files:
        return []

    planning_preflight_payload: dict[str, Any] = {}
    if convergence_verifier is None:
        planning_preflight = plan_director_repair(
            PlanDirectorRepairCommandV1(
                source_tool=source_tool,
                artifact_quality_errors=tuple(str(item) for item in artifact_quality_errors),
                base_files=dict(base_files),
                advisor_notes=tuple(advisor_notes),
            )
        )
        planning_preflight_payload = planning_preflight.to_dict()
        if not planning_preflight.ok:
            if planning_preflight.error_code == "repair_not_planned" or (
                not planning_preflight.planned and not planning_preflight.error_code
            ):
                return []
            return [
                _project_failed_planning_preflight(
                    source_tool=source_tool,
                    planning_preflight=planning_preflight_payload,
                )
            ]

    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    executor = executor_factory(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    )
    write_results: dict[str, dict[str, Any]] = {}
    edit_results: dict[str, dict[str, Any]] = {}
    delete_results: dict[str, dict[str, Any]] = {}

    def _mark_progress(path: str) -> None:
        progress_update = getattr(adapter, "_update_task_progress", None)
        if not callable(progress_update):
            return
        with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
            progress_update(task_id, "executing", current_file=path)

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

    def _policy_gated_deleter(path: str) -> dict[str, Any]:
        try:
            delete_result = executor.execute_tool(
                "delete_file",
                {"file": path},
                task_id=task_id,
            )
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            delete_result = {
                "ok": False,
                "file": path,
                "error_code": "delete_file_requires_policy_gated_deleter",
                "error": f"delete_file requires policy-gated Director deleter: {exc}",
            }
        delete_results[path] = dict(delete_result)
        if bool(delete_result.get("ok")):
            _mark_progress(path)
        return dict(delete_result)

    policy_gated_deleter = _policy_gated_deleter if _supports_policy_gated_delete_tool(executor) else None

    if convergence_verifier is not None:
        convergence_result = run_director_repair_convergence(
            RunDirectorRepairConvergenceCommandV1(
                task_id=task_id,
                workspace=str(workspace_path),
                source_tools=(source_tool,),
                artifact_quality_errors=tuple(str(item) for item in artifact_quality_errors),
                base_files=dict(base_files),
                allowed_paths=tuple(allowed_paths or base_files.keys()),
                advisor_notes=tuple(advisor_notes),
                max_rounds=max_rounds,
                metadata={"adapter_bridge": "runtime_repair_tool_adapter"},
            ),
            writer=_policy_gated_writer,
            editor=_policy_gated_editor if use_editor else None,
            deleter=policy_gated_deleter,
            verifier=convergence_verifier,
        )
        if not convergence_result.ok:
            return [
                _project_failed_convergence_repair(
                    source_tool=source_tool,
                    convergence_result=convergence_result,
                    delete_results=delete_results,
                    delete_tool_available=policy_gated_deleter is not None,
                )
            ]
        return _project_successful_repair_results(
            repair_result=convergence_result,
            planning_preflight=planning_preflight_payload,
            write_results=write_results,
            edit_results=edit_results,
            delete_results=delete_results,
            workspace_path=workspace_path,
            mark_progress=_mark_progress,
            convergence_result=convergence_result,
        )

    canonical_result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id=task_id,
            workspace=str(workspace_path),
            source_tool=source_tool,
            artifact_quality_errors=tuple(str(item) for item in artifact_quality_errors),
            base_files=dict(base_files),
            allowed_paths=tuple(allowed_paths or base_files.keys()),
            advisor_notes=tuple(advisor_notes),
        ),
        writer=_policy_gated_writer,
        editor=_policy_gated_editor if use_editor else None,
        deleter=policy_gated_deleter,
        revalidator=revalidator,
    )
    if not canonical_result.ok:
        if canonical_result.error_code == "repair_not_planned":
            return []
        return [
            _project_failed_repair(
                source_tool=source_tool,
                canonical_result=canonical_result,
                planning_preflight=planning_preflight_payload,
                delete_results=delete_results,
                delete_tool_available=policy_gated_deleter is not None,
            )
        ]

    return _project_successful_repair_results(
        repair_result=canonical_result,
        planning_preflight=planning_preflight_payload,
        write_results=write_results,
        edit_results=edit_results,
        delete_results=delete_results,
        workspace_path=workspace_path,
        mark_progress=_mark_progress,
    )


def _supports_policy_gated_delete_tool(executor: Any) -> bool:
    supports_tool = getattr(executor, "supports_tool", None)
    if callable(supports_tool):
        with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
            return bool(supports_tool("delete_file"))

    for attr_name in ("available_tools", "tools", "tool_names"):
        raw_tools = getattr(executor, attr_name, None)
        if raw_tools is None:
            continue
        if callable(raw_tools):
            with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
                raw_tools = raw_tools()
        if isinstance(raw_tools, Mapping):
            return "delete_file" in {str(key) for key in raw_tools}
        with contextlib.suppress(TypeError):
            return "delete_file" in {str(item) for item in raw_tools}

    return False


def _project_successful_repair_results(
    *,
    repair_result: Any,
    planning_preflight: dict[str, Any],
    write_results: Mapping[str, dict[str, Any]],
    edit_results: Mapping[str, dict[str, Any]],
    delete_results: Mapping[str, dict[str, Any]],
    workspace_path: Path,
    mark_progress: Callable[[str], None],
    convergence_result: Any | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for receipt in repair_result.receipts:
        for patch_path in receipt.files_changed:
            tool_result = (
                delete_results.get(patch_path) or edit_results.get(patch_path) or write_results.get(patch_path, {})
            )
            if patch_path in delete_results:
                tool_name = "delete_file"
            elif patch_path in edit_results:
                tool_name = "edit_file"
            else:
                tool_name = "write_file"
            if not bool(tool_result.get("ok")) and receipt.authoritative:
                continue
            bytes_written = tool_result.get("bytes_written")
            if bytes_written is None and tool_name != "delete_file":
                full_path = (workspace_path / patch_path).resolve()
                with contextlib.suppress(OSError, ValueError):
                    bytes_written = len(full_path.read_text(encoding="utf-8").encode("utf-8"))
            mark_progress(patch_path)
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
                        "repair_kernel": _project_receipt_kernel(
                            receipt=receipt,
                            canonical_result=repair_result,
                            planning_preflight=planning_preflight,
                            convergence_result=convergence_result,
                        ),
                    },
                }
            )
    return results


def _project_failed_planning_preflight(
    *,
    source_tool: str,
    planning_preflight: dict[str, Any],
) -> dict[str, Any]:
    return {
        "tool": "director_repair_kernel",
        "tool_name": "director_repair_kernel",
        "success": False,
        "result": {
            "ok": False,
            "source_tool": source_tool,
            "error_code": planning_preflight.get("error_code"),
            "error_message": planning_preflight.get("error_message"),
            "repair_kernel": {
                "owner_cell": "director.runtime",
                "planning_preflight": dict(planning_preflight),
                "planning": dict(planning_preflight),
                "execution_skipped": True,
                "execution_skip_reason": "planning_preflight_failed",
            },
        },
    }


def _project_failed_repair(
    *,
    source_tool: str,
    canonical_result: Any,
    planning_preflight: dict[str, Any],
    delete_results: Mapping[str, dict[str, Any]],
    delete_tool_available: bool,
) -> dict[str, Any]:
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
                "planning_preflight": dict(planning_preflight),
                "planning": dict(canonical_result.metadata.get("planning") or {}),
                "planning_error": dict(canonical_result.metadata.get("planning_error") or {}),
                "plan_policy": dict(canonical_result.metadata.get("plan_policy") or {}),
                "composition_policy": dict(canonical_result.metadata.get("composition_policy") or {}),
                "execution_error": canonical_result.metadata.get("execution_error"),
                "execution_error_code": canonical_result.metadata.get("execution_error_code"),
                "delete_results": dict(delete_results),
                "delete_tool_available": bool(delete_tool_available),
                "rolled_back": bool(canonical_result.metadata.get("rolled_back")),
            },
        },
    }


def _project_failed_convergence_repair(
    *,
    source_tool: str,
    convergence_result: Any,
    delete_results: Mapping[str, dict[str, Any]],
    delete_tool_available: bool,
) -> dict[str, Any]:
    metadata = dict(getattr(convergence_result, "metadata", {}) or {})
    rounds = [_object_to_dict(round_result) for round_result in getattr(convergence_result, "rounds", ())]
    receipts = [_project_receipt_payload(receipt) for receipt in getattr(convergence_result, "receipts", ())]
    final_diagnostics = _project_diagnostics(getattr(convergence_result, "final_diagnostics", ()))
    return {
        "tool": "director_repair_kernel",
        "tool_name": "director_repair_kernel",
        "success": False,
        "result": {
            "ok": False,
            "source_tool": source_tool,
            "status": getattr(convergence_result, "status", None),
            "converged": bool(getattr(convergence_result, "converged", False)),
            "error_code": getattr(convergence_result, "error_code", None),
            "error_message": getattr(convergence_result, "error_message", None),
            "receipts": receipts,
            "rounds": rounds,
            "final_diagnostics": final_diagnostics,
            "metadata": metadata,
            "delete_results": dict(delete_results),
            "repair_kernel": {
                "owner_cell": "director.runtime",
                "convergence_status": getattr(convergence_result, "status", None),
                "converged": bool(getattr(convergence_result, "converged", False)),
                "convergence_round_count": len(rounds),
                "convergence_rounds": rounds,
                "rounds": rounds,
                "receipts": receipts,
                "final_diagnostics": final_diagnostics,
                "metadata": metadata,
                "delete_results": dict(delete_results),
                "delete_tool_available": bool(delete_tool_available),
                "coverage_report": dict(metadata.get("coverage_report") or {}),
                "error_code": getattr(convergence_result, "error_code", None),
                "error_message": getattr(convergence_result, "error_message", None),
            },
        },
    }


def _project_receipt_kernel(
    *,
    receipt: Any,
    canonical_result: Any,
    planning_preflight: dict[str, Any],
    convergence_result: Any | None = None,
) -> dict[str, Any]:
    receipt_metadata = dict(receipt.metadata)
    payload = {
        "owner_cell": "director.runtime",
        "receipt_id": receipt.receipt_id,
        "plan_id": receipt.plan_id,
        "status": receipt.status,
        "authoritative": receipt.authoritative,
        "requires_revalidation": _receipt_requires_revalidation(receipt),
        "authority_hash": receipt.authority_hash,
        "projection_hash": receipt.projection_hash,
        "before_hashes": dict(receipt.before_hashes),
        "after_hashes": dict(receipt.after_hashes),
        "round_number": receipt.round_number,
        "errors_before": receipt.errors_before,
        "errors_after": receipt.errors_after,
        "net_error_reduction": receipt.net_error_reduction,
        "revalidation_evidence": dict(receipt.revalidation_evidence),
        "metadata": receipt_metadata,
        "planning_preflight": dict(planning_preflight),
        "planning": dict(canonical_result.metadata.get("planning") or {}),
        "plan_policy": dict(canonical_result.metadata.get("plan_policy") or {}),
        "composition_policy": dict(canonical_result.metadata.get("composition_policy") or {}),
    }
    if convergence_result is not None:
        metadata = dict(getattr(convergence_result, "metadata", {}) or {})
        rounds = [_object_to_dict(round_result) for round_result in getattr(convergence_result, "rounds", ())]
        payload.update(
            {
                "convergence_status": getattr(convergence_result, "status", None),
                "converged": bool(getattr(convergence_result, "converged", False)),
                "convergence_round_count": len(rounds),
                "convergence_rounds": rounds,
                "final_diagnostics": _project_diagnostics(getattr(convergence_result, "final_diagnostics", ())),
                "coverage_report": dict(metadata.get("coverage_report") or {}),
                "convergence_metadata": metadata,
            }
        )
    return payload


def _receipt_requires_revalidation(receipt: Any) -> bool:
    metadata = dict(getattr(receipt, "metadata", {}) or {})
    if "requires_revalidation" in metadata:
        return bool(metadata.get("requires_revalidation"))
    return not bool(getattr(receipt, "revalidation_evidence", {}) or {})


def _project_receipt_payload(receipt: Any) -> dict[str, Any]:
    payload = _object_to_dict(receipt)
    payload["requires_revalidation"] = _receipt_requires_revalidation(receipt)
    return payload


def _project_diagnostics(diagnostics: Sequence[Any]) -> list[dict[str, Any]]:
    return [_object_to_dict(diagnostic) for diagnostic in diagnostics]


def _object_to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {"value": value}
