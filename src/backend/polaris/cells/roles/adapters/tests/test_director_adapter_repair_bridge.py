"""Tests for Director adapter repair bridge receipt projection."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.director.runtime.internal.repair_kernel.contracts import FILE_ABSENT_HASH, sha256_text
from polaris.cells.director.runtime.internal.repair_kernel.rust_syntax import (
    RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL,
)
from polaris.cells.director.runtime.public.contracts import (
    DirectorRepairConvergenceVerifierRequestV1,
    DirectorRepairResultV1,
    DirectorRepairRevalidationInputV1,
    DirectorRepairRevalidationRequestV1,
    DirectorRepairVerifierSnapshotInputV1,
    RepairReceiptV1,
)
from polaris.cells.roles.adapters.internal.director import (
    materialization_quality_repair_bridge,
    post_execution_repair_bridge,
)
from polaris.cells.roles.adapters.internal.director.deterministic_repairs import (
    _runtime_bridge as runtime_bridge_module,
    generic_repairs,
)
from polaris.cells.roles.adapters.internal.director.deterministic_repairs._runtime_bridge import (
    run_runtime_repair_with_director_tools,
)
from polaris.cells.roles.adapters.internal.director.execution_tools import DirectorToolExecutor
from polaris.cells.roles.adapters.public import service as roles_adapters_public_service

_RELATIVE_PATH = "src/models/Flight.ts"
_SOURCE_TOOL = "deterministic_typescript_return_object_semicolon_repair"
_BROKEN_CONTENT = (
    "export function runFlight() {\n"
    "  const samples = [];\n"
    "  const range = 10;\n"
    "  const maxAltitude = 2;\n"
    "  const flightTime = 3;\n"
    "  return { samples, range, maxAltitude, flightTime  landed: undefined as unknown as boolean };\n"
    "}\n"
)
_QUALITY_ERROR = "TypeScript syntax check failed: src/models/Flight.ts(6,47): error TS1005: ',' expected."
_RESIDUAL_ERROR = "TypeScript syntax check failed: src/models/Flight.ts(6,47): error TS2304: Cannot find name 'Widget'."
_UNCOVERED_ERROR = "declared target file missing app/models/widget.rb is missing"
_DELETE_SOURCE_TOOL = "deterministic_test_delete_file_repair"


def _patch_post_execution_schedule_result_as_dicts(monkeypatch: Any) -> None:
    source_tools_by_step_id = {
        "go.module_import": "deterministic_go_module_import_repair",
        "rust.post_execution_convergence": "deterministic_rust_post_execution_repair",
        "cpp.post_execution": "deterministic_cpp_include_path_repair",
        "java.post_execution": "deterministic_java_accessor_alias_repair",
    }

    def fake_schedule_result(
        *,
        runner_step_ids: tuple[str, ...],
        runner: Any,
        max_rounds: int = 1,
    ) -> SimpleNamespace:
        ordered_steps = tuple(
            post_execution_repair_bridge.DirectorRepairPostExecutionStepV1(
                step_id=step_id,
                language=step_id.split(".", 1)[0],
                phase="post_materialization",
                priority=index,
                source_tool=source_tools_by_step_id.get(step_id, f"{step_id}.source_tool"),
            )
            for index, step_id in enumerate(runner_step_ids, start=1)
        )
        tool_results: list[dict[str, Any]] = []
        receipt_projections: list[dict[str, Any]] = []
        for step in ordered_steps:
            step_results = []
            for item in runner(step):
                copied = dict(item)
                payload = copied.get("result")
                if isinstance(payload, dict):
                    copied["result"] = {**payload, "bridge_step_id": payload.get("bridge_step_id") or step.step_id}
                step_results.append(copied)
            for item in step_results:
                payload = item.get("result") if isinstance(item, dict) else None
                if not isinstance(payload, dict):
                    continue
                receipt_projections.append(
                    {
                        "projection_id": f"projection-{len(receipt_projections) + 1}",
                        "receipt_authority": "non_authoritative_callback_projection",
                        "schedule_kind": "post_execution",
                        "step_id": step.step_id,
                        "source_tool": payload.get("source_tool"),
                        "round_number": payload.get("round_number") or 0,
                        "max_rounds": max_rounds,
                        "projection_only": True,
                        "authoritative": False,
                        "typed_receipt_path_available": False,
                        "revalidation_evidence_present": len(receipt_projections) == 0,
                    }
                )
            tool_results.extend(step_results)
        return SimpleNamespace(
            ordered_steps=ordered_steps,
            tool_results=tuple(tool_results),
            receipt_projections=tuple(receipt_projections),
            summary={
                "schedule_kind": "post_execution",
                "max_rounds": max_rounds,
                "rounds_run": 1 if tool_results else 0,
                "receipt_projection_count": len(receipt_projections),
            },
        )

    monkeypatch.setattr(
        post_execution_repair_bridge,
        "run_director_post_execution_repair_schedule_result",
        fake_schedule_result,
    )


def _patch_materialization_schedule_result_as_dicts(monkeypatch: Any) -> None:
    def fake_schedule_result(
        *,
        runner_step_ids: tuple[str, ...],
        runner: Any,
        max_rounds: int = 1,
    ) -> SimpleNamespace:
        ordered_steps = tuple(
            materialization_quality_repair_bridge.DirectorRepairMaterializationQualityStepV1(
                step_id=step_id,
                language=step_id.split(".", 1)[-1],
                phase="materialization_quality",
                priority=index,
                source_tool=f"{step_id}.source_tool",
            )
            for index, step_id in enumerate(runner_step_ids)
        )
        tool_results: list[dict[str, Any]] = []
        for step in ordered_steps:
            tool_results.extend(dict(item) for item in runner(step))
        return SimpleNamespace(
            ordered_steps=ordered_steps,
            tool_results=tuple(tool_results),
            receipt_projections=(),
            summary={
                "schedule_kind": "materialization_quality",
                "max_rounds": max_rounds,
                "rounds_run": 1 if tool_results else 0,
                "receipt_projection_count": 0,
            },
        )

    monkeypatch.setattr(
        materialization_quality_repair_bridge,
        "run_director_materialization_quality_repair_schedule_result",
        fake_schedule_result,
    )


def _materialization_runtime_schedule_steps() -> tuple[Any, ...]:
    source_tools_by_step_id = {
        "materialization.hygiene_scaffold": "deterministic_patch_residue_cleanup",
        "materialization.typescript_scaffold": "deterministic_typescript_scaffold_repair",
        "materialization.typescript_compiler": "deterministic_typescript_return_object_semicolon_repair",
        "materialization.node_manifest": "deterministic_runtime_dependency_repair",
        "materialization.rust_compiler": "deterministic_rust_crate_import_rewrite_repair",
        "materialization.target_runtime": "deterministic_javascript_missing_export_repair",
        "materialization.python_import": "deterministic_python_import_repair",
        "materialization.go_import": "deterministic_go_bare_import_string_repair",
    }
    dependencies_by_step_id = {
        "materialization.typescript_compiler": ("materialization.typescript_scaffold",),
        "materialization.rust_compiler": ("materialization.node_manifest",),
        "materialization.go_import": ("materialization.target_runtime",),
    }
    return tuple(
        materialization_quality_repair_bridge.DirectorRepairMaterializationQualityStepV1(
            step_id=step_id,
            language=step_id.rsplit(".", 1)[-1],
            phase="materialization_quality",
            priority=index,
            source_tool=source_tools_by_step_id[step_id],
            depends_on=dependencies_by_step_id.get(step_id, ()),
        )
        for index, step_id in enumerate(
            materialization_quality_repair_bridge._MATERIALIZATION_QUALITY_REPAIR_RUNNERS,
            start=1,
        )
    )


def _patch_materialization_runtime_schedule_query(
    monkeypatch: Any,
    steps: tuple[Any, ...],
) -> None:
    def fake_query(_: Any = None) -> SimpleNamespace:
        return SimpleNamespace(
            items=steps,
            summary={
                "step_count": len(steps),
                "ordered_step_ids": [step.step_id for step in steps],
                "runtime_schedule_authoritative": True,
            },
        )

    monkeypatch.setattr(
        materialization_quality_repair_bridge,
        "query_director_repair_materialization_quality_schedule",
        fake_query,
    )


def _assert_non_authoritative_callback_projection_boundary(
    summary: dict[str, Any],
    *,
    forbidden_receipt_ids: set[str],
) -> None:
    scheduler_bridge = summary["scheduler_bridge"]
    assert scheduler_bridge["callback_receipts_authoritative"] is False
    assert scheduler_bridge["typed_receipt_path_available"] is False
    assert (
        scheduler_bridge["migration_blocker"] == "callback runners still return tool_results instead of RepairReceipt"
    )
    repair_kernel_receipts = [
        receipt for receipt in summary["repair_kernel"].get("receipts", []) if isinstance(receipt, dict)
    ]
    assert {receipt.get("receipt_id") for receipt in repair_kernel_receipts}.isdisjoint(forbidden_receipt_ids)
    assert all("callback_receipt_projection" not in receipt for receipt in repair_kernel_receipts)
    assert all("callback_receipt_projections" not in receipt for receipt in repair_kernel_receipts)


def _trusted_verifier_metadata(verifier: str) -> dict[str, Any]:
    return {
        "verifier": verifier,
        "evidence_source": "adapter_convergence_verifier_factory",
        "raw_output_ref_verified": True,
    }


class _FakeAdapter:
    def __init__(self, workspace: Path) -> None:
        self.workspace = str(workspace)
        self._execution = SimpleNamespace(_message_bus=None)
        self.progress: list[tuple[str, str, str | None]] = []

    def _update_task_progress(self, task_id: str, state: str, *, current_file: str | None = None) -> None:
        self.progress.append((task_id, state, current_file))


class _FakeDirectorToolExecutor:
    def __init__(self, workspace: str, *, message_bus: Any = None, worker_id: str = "") -> None:
        self.workspace = Path(workspace)
        self.message_bus = message_bus
        self.worker_id = worker_id

    def execute_tool(self, tool_name: str, payload: dict[str, str], *, task_id: str) -> dict[str, Any]:
        del task_id
        file_path = payload["file"]
        target = self.workspace / file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if tool_name == "edit_file":
            before = target.read_text(encoding="utf-8")
            search = payload["search"]
            if search not in before:
                return {"ok": False, "file": file_path, "error": "search text not found"}
            content = before.replace(search, payload["replace"], 1)
            operation = "edit_file"
        else:
            content = payload["content"]
            operation = "write_file"
        target.write_text(content, encoding="utf-8")
        return {
            "ok": True,
            "file": file_path,
            "bytes_written": len(content.encode("utf-8")),
            "operation": operation,
            "broadcast_ok": True,
            "director_policy": {"allowed": True},
        }


class _FakeDirectorToolExecutorWithDelete(_FakeDirectorToolExecutor):
    available_tools = ("write_file", "edit_file", "delete_file")

    def execute_tool(self, tool_name: str, payload: dict[str, str], *, task_id: str) -> dict[str, Any]:
        if tool_name != "delete_file":
            return super().execute_tool(tool_name, payload, task_id=task_id)
        del task_id
        file_path = payload["file"]
        target = self.workspace / file_path
        if not target.is_file():
            return {"ok": False, "file": file_path, "error": "file not found"}
        target.unlink()
        return {
            "ok": True,
            "file": file_path,
            "bytes_written": 0,
            "operation": "delete_file",
            "broadcast_ok": True,
            "director_policy": {"allowed": True},
        }


def _run_runtime_bridge(
    workspace: Path,
    *,
    revalidator: Any = None,
    convergence_verifier: Any = None,
    artifact_quality_errors: tuple[str, ...] = (_QUALITY_ERROR,),
    max_rounds: int = 3,
) -> list[dict[str, Any]]:
    target = workspace / _RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_BROKEN_CONTENT, encoding="utf-8")

    return run_runtime_repair_with_director_tools(
        _FakeAdapter(workspace),
        workspace_path=workspace,
        task_id="task-adapter-revalidation",
        source_tool=_SOURCE_TOOL,
        executor_factory=_FakeDirectorToolExecutor,
        base_files={_RELATIVE_PATH: _BROKEN_CONTENT},
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=(_RELATIVE_PATH,),
        revalidator=revalidator,
        convergence_verifier=convergence_verifier,
        max_rounds=max_rounds,
    )


def test_runtime_bridge_projects_native_revalidation_receipt_evidence(tmp_path: Path) -> None:
    requests: list[DirectorRepairRevalidationRequestV1] = []

    def revalidator(request: DirectorRepairRevalidationRequestV1) -> DirectorRepairRevalidationInputV1:
        requests.append(request)
        return DirectorRepairRevalidationInputV1(
            command=("rtk", "tsc", "--noEmit"),
            exit_code=0,
            raw_output_ref="runtime/verifier/adapter-success.log",
            metadata=_trusted_verifier_metadata("typescript"),
        )

    results = _run_runtime_bridge(tmp_path, revalidator=revalidator)

    assert len(results) == 1
    assert results[0]["success"] is True
    assert len(requests) == 1
    assert requests[0].source_tool == _SOURCE_TOOL
    repair_kernel = results[0]["result"]["repair_kernel"]
    evidence = repair_kernel["revalidation_evidence"]
    assert repair_kernel["authoritative"] is True
    assert repair_kernel["requires_revalidation"] is False
    assert repair_kernel["authority_hash"]
    assert repair_kernel["projection_hash"]
    assert "round_number" in repair_kernel
    assert repair_kernel["errors_before"] == 1
    assert repair_kernel["errors_after"] == 0
    assert repair_kernel["net_error_reduction"] == 1
    assert evidence["command"] == ["rtk", "tsc", "--noEmit"]
    assert evidence["exit_code"] == 0
    assert evidence["raw_output_ref"] == "runtime/verifier/adapter-success.log"


def test_runtime_bridge_failed_revalidation_keeps_receipt_evidence_and_hashes(tmp_path: Path) -> None:
    def revalidator(_: DirectorRepairRevalidationRequestV1) -> DirectorRepairRevalidationInputV1:
        return DirectorRepairRevalidationInputV1(
            residual_artifact_quality_errors=(_RESIDUAL_ERROR,),
            command=("rtk", "npm", "test"),
            exit_code=1,
            raw_output_ref="runtime/verifier/adapter-failure.log",
            metadata=_trusted_verifier_metadata("typescript"),
        )

    results = _run_runtime_bridge(tmp_path, revalidator=revalidator)

    assert len(results) == 1
    assert results[0]["success"] is False
    result = results[0]["result"]
    assert result["error_code"] == "repair_revalidation_failed"
    receipts = result["repair_kernel"]["receipts"]
    assert len(receipts) == 1
    receipt = receipts[0]
    evidence = receipt["revalidation_evidence"]
    assert receipt["status"] == "failed_revalidation"
    assert receipt["authoritative"] is False
    assert receipt["metadata"]["requires_revalidation"] is False
    assert receipt["authority_hash"]
    assert receipt["projection_hash"]
    assert "round_number" in receipt
    assert receipt["errors_before"] == 1
    assert receipt["errors_after"] == 1
    assert receipt["net_error_reduction"] == 0
    assert evidence["command"] == ["rtk", "npm", "test"]
    assert evidence["exit_code"] == 1
    assert evidence["raw_output_ref"] == "runtime/verifier/adapter-failure.log"


def test_runtime_bridge_without_revalidator_keeps_requires_revalidation_visible(tmp_path: Path) -> None:
    results = _run_runtime_bridge(tmp_path)

    assert len(results) == 1
    assert results[0]["success"] is True
    repair_kernel = results[0]["result"]["repair_kernel"]
    assert repair_kernel["authoritative"] is False
    assert repair_kernel["requires_revalidation"] is True
    assert repair_kernel["revalidation_evidence"] == {}
    assert repair_kernel["authority_hash"]
    assert repair_kernel["projection_hash"]


def test_runtime_bridge_missing_progress_callback_is_fail_soft(tmp_path: Path) -> None:
    target = tmp_path / _RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_BROKEN_CONTENT, encoding="utf-8")
    adapter = SimpleNamespace(_execution=SimpleNamespace(_message_bus=None))

    results = run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=tmp_path,
        task_id="task-no-progress-callback",
        source_tool=_SOURCE_TOOL,
        executor_factory=_FakeDirectorToolExecutor,
        base_files={_RELATIVE_PATH: _BROKEN_CONTENT},
        artifact_quality_errors=(_QUALITY_ERROR,),
        allowed_paths=(_RELATIVE_PATH,),
    )

    assert len(results) == 1
    assert results[0]["success"] is True
    repair_kernel = results[0]["result"]["repair_kernel"]
    assert repair_kernel["owner_cell"] == "director.runtime"
    assert repair_kernel["requires_revalidation"] is True


def test_runtime_bridge_projects_delete_file_tool_result_when_deleter_available(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    relative_path = "src/stale.ts"
    original = "export const stale = true;\n"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text(original, encoding="utf-8")

    def fake_plan_director_repair(_: Any) -> SimpleNamespace:
        return SimpleNamespace(
            ok=True,
            planned=True,
            error_code=None,
            to_dict=lambda: {"ok": True, "planned": True, "source_tool": _DELETE_SOURCE_TOOL},
        )

    def fake_run_director_repair(
        command: Any,
        *,
        writer: Any,
        editor: Any = None,
        deleter: Any = None,
        revalidator: Any = None,
    ) -> DirectorRepairResultV1:
        del command, writer, editor, revalidator
        assert deleter is not None
        delete_result = deleter(relative_path)
        assert delete_result["ok"] is True
        return DirectorRepairResultV1(
            ok=True,
            receipts=(
                RepairReceiptV1(
                    receipt_id="receipt-delete-file",
                    plan_id="plan-delete-file",
                    source_tool=_DELETE_SOURCE_TOOL,
                    status="applied",
                    authoritative=True,
                    files_changed=(relative_path,),
                    before_hashes={relative_path: "before-delete"},
                    after_hashes={relative_path: "absent-after-delete"},
                    metadata={"execution_records": [{"operation": "delete_file", "path": relative_path}]},
                ),
            ),
            metadata={"planning": {"ok": True}},
        )

    monkeypatch.setattr(runtime_bridge_module, "plan_director_repair", fake_plan_director_repair)
    monkeypatch.setattr(runtime_bridge_module, "run_director_repair", fake_run_director_repair)

    results = run_runtime_repair_with_director_tools(
        _FakeAdapter(tmp_path),
        workspace_path=tmp_path,
        task_id="task-delete-available",
        source_tool=_DELETE_SOURCE_TOOL,
        executor_factory=_FakeDirectorToolExecutorWithDelete,
        base_files={relative_path: original},
        artifact_quality_errors=("test delete stale file",),
        allowed_paths=(relative_path,),
    )

    assert len(results) == 1
    assert results[0]["tool"] == "delete_file"
    assert results[0]["success"] is True
    assert not target.exists()
    payload = results[0]["result"]
    assert payload["operation"] == "delete_file"
    assert payload["bytes_written"] == 0
    assert payload["repair_kernel"]["metadata"]["execution_records"][0]["operation"] == "delete_file"


def test_runtime_bridge_uses_director_tool_executor_delete_file(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    relative_path = "src/stale.ts"
    original = "export const stale = true;\n"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text(original, encoding="utf-8")

    def fake_plan_director_repair(_: Any) -> SimpleNamespace:
        return SimpleNamespace(
            ok=True,
            planned=True,
            error_code=None,
            to_dict=lambda: {"ok": True, "planned": True, "source_tool": _DELETE_SOURCE_TOOL},
        )

    def fake_run_director_repair(
        command: Any,
        *,
        writer: Any,
        editor: Any = None,
        deleter: Any = None,
        revalidator: Any = None,
    ) -> DirectorRepairResultV1:
        del command, writer, editor, revalidator
        assert deleter is not None
        delete_result = deleter(relative_path)
        assert delete_result["ok"] is True
        assert delete_result["deleted"] is True
        assert delete_result["operation"] == "delete_file"
        return DirectorRepairResultV1(
            ok=True,
            receipts=(
                RepairReceiptV1(
                    receipt_id="receipt-delete-file-production-executor",
                    plan_id="plan-delete-file-production-executor",
                    source_tool=_DELETE_SOURCE_TOOL,
                    status="applied",
                    authoritative=True,
                    files_changed=(relative_path,),
                    before_hashes={relative_path: "before-delete"},
                    after_hashes={relative_path: "absent-after-delete"},
                    metadata={"execution_records": [{"operation": "delete_file", "path": relative_path}]},
                ),
            ),
            metadata={"planning": {"ok": True}},
        )

    monkeypatch.setattr(runtime_bridge_module, "plan_director_repair", fake_plan_director_repair)
    monkeypatch.setattr(runtime_bridge_module, "run_director_repair", fake_run_director_repair)

    results = run_runtime_repair_with_director_tools(
        _FakeAdapter(tmp_path),
        workspace_path=tmp_path,
        task_id="task-delete-production-executor",
        source_tool=_DELETE_SOURCE_TOOL,
        executor_factory=DirectorToolExecutor,
        base_files={relative_path: original},
        artifact_quality_errors=("test delete stale file",),
        allowed_paths=(relative_path,),
    )

    assert len(results) == 1
    assert results[0]["tool"] == "delete_file"
    assert results[0]["success"] is True
    assert not target.exists()
    payload = results[0]["result"]
    assert payload["operation"] == "delete_file"
    assert payload["bytes_written"] == 0
    assert payload["director_policy"]["allowed"] is True


def test_runtime_bridge_executes_rust_duplicate_module_delete_with_real_director_tool_executor(
    tmp_path: Path,
) -> None:
    duplicate_path = "src/models.rs"
    sibling_path = "src/models/mod.rs"
    generated = "// Polaris generated module stub\n"
    real = "pub struct Model;\n"
    raw_error = (
        f'error[E0761]: file for module `models` found at both "{duplicate_path}" and "{sibling_path}"\n'
        " --> src/lib.rs:1:1\n"
        "  |\n"
        "1 | pub mod models;\n"
        "  | ^^^^^^^^^^^^^^^\n"
    )
    duplicate_file = tmp_path / duplicate_path
    sibling_file = tmp_path / sibling_path
    duplicate_file.parent.mkdir(parents=True)
    sibling_file.parent.mkdir(parents=True)
    duplicate_file.write_text(generated, encoding="utf-8")
    sibling_file.write_text(real, encoding="utf-8")
    revalidation_requests: list[DirectorRepairRevalidationRequestV1] = []

    def revalidator(request: DirectorRepairRevalidationRequestV1) -> DirectorRepairRevalidationInputV1:
        revalidation_requests.append(request)
        return DirectorRepairRevalidationInputV1(
            command=("rtk", "cargo", "check"),
            exit_code=0,
            raw_output_ref="runtime/verifier/rust-duplicate-module-delete.log",
            metadata=_trusted_verifier_metadata("rust"),
        )

    results = run_runtime_repair_with_director_tools(
        _FakeAdapter(tmp_path),
        workspace_path=tmp_path,
        task_id="task-rust-duplicate-module-delete",
        source_tool=RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL,
        executor_factory=DirectorToolExecutor,
        base_files={
            duplicate_path: generated,
            sibling_path: real,
        },
        artifact_quality_errors=(raw_error,),
        allowed_paths=(duplicate_path, sibling_path),
        revalidator=revalidator,
    )

    assert len(results) == 1
    assert len(revalidation_requests) == 1
    assert revalidation_requests[0].source_tool == RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL
    assert results[0]["tool"] == "delete_file"
    assert results[0]["success"] is True
    assert not duplicate_file.exists()
    assert sibling_file.read_text(encoding="utf-8") == real
    payload = results[0]["result"]
    assert payload["operation"] == "delete_file"
    assert payload["bytes_written"] == 0
    assert payload["before_hash"] == sha256_text(generated)
    assert payload["after_hash"] == FILE_ABSENT_HASH
    assert payload["director_policy"]["allowed"] is True
    repair_kernel = payload["repair_kernel"]
    assert repair_kernel["authoritative"] is True
    assert repair_kernel["before_hashes"][duplicate_path] == sha256_text(generated)
    assert repair_kernel["after_hashes"][duplicate_path] == FILE_ABSENT_HASH
    record = repair_kernel["metadata"]["execution_records"][0]
    assert record["operation"] == "delete_file"
    assert record["rollback_strategy"] == "write_file_full_restore"


def test_runtime_bridge_missing_delete_tool_fails_closed_with_delete_reason(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    relative_path = "src/stale.ts"
    original = "export const stale = true;\n"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text(original, encoding="utf-8")

    def fake_plan_director_repair(_: Any) -> SimpleNamespace:
        return SimpleNamespace(
            ok=True,
            planned=True,
            error_code=None,
            to_dict=lambda: {"ok": True, "planned": True, "source_tool": _DELETE_SOURCE_TOOL},
        )

    def fake_run_director_repair(
        command: Any,
        *,
        writer: Any,
        editor: Any = None,
        deleter: Any = None,
        revalidator: Any = None,
    ) -> DirectorRepairResultV1:
        del command, writer, editor, revalidator
        assert deleter is None
        error = f"repair delete_file requires policy-gated deleter for {relative_path}"
        return DirectorRepairResultV1(
            ok=False,
            receipts=(
                RepairReceiptV1(
                    receipt_id="receipt-delete-missing-tool",
                    plan_id="plan-delete-missing-tool",
                    source_tool=_DELETE_SOURCE_TOOL,
                    status="failed",
                    authoritative=False,
                    files_changed=(relative_path,),
                    metadata={"error": error},
                ),
            ),
            error_code="repair_execution_failed",
            error_message=error,
            metadata={
                "planning": {"ok": True},
                "execution_error": error,
                "execution_error_code": "delete_file_requires_policy_gated_deleter",
                "rolled_back": False,
            },
        )

    monkeypatch.setattr(runtime_bridge_module, "plan_director_repair", fake_plan_director_repair)
    monkeypatch.setattr(runtime_bridge_module, "run_director_repair", fake_run_director_repair)

    results = run_runtime_repair_with_director_tools(
        _FakeAdapter(tmp_path),
        workspace_path=tmp_path,
        task_id="task-delete-missing-tool",
        source_tool=_DELETE_SOURCE_TOOL,
        executor_factory=_FakeDirectorToolExecutor,
        base_files={relative_path: original},
        artifact_quality_errors=("test delete stale file",),
        allowed_paths=(relative_path,),
    )

    assert len(results) == 1
    assert results[0]["success"] is False
    assert target.read_text(encoding="utf-8") == original
    repair_kernel = results[0]["result"]["repair_kernel"]
    assert repair_kernel["execution_error_code"] == "delete_file_requires_policy_gated_deleter"
    assert repair_kernel["delete_tool_available"] is False
    assert repair_kernel["receipts"][0]["metadata"]["error"].endswith(f"policy-gated deleter for {relative_path}")


def test_runtime_bridge_convergence_verifier_projects_authoritative_receipt_evidence(tmp_path: Path) -> None:
    requests: list[DirectorRepairConvergenceVerifierRequestV1] = []

    def convergence_verifier(
        request: DirectorRepairConvergenceVerifierRequestV1,
    ) -> DirectorRepairVerifierSnapshotInputV1:
        requests.append(request)
        current = (Path(request.workspace) / _RELATIVE_PATH).read_text(encoding="utf-8")
        residual_errors = () if "flightTime, landed:" in current else (_QUALITY_ERROR,)
        return DirectorRepairVerifierSnapshotInputV1(
            residual_artifact_quality_errors=residual_errors,
            command=("rtk", "tsc", "--noEmit"),
            exit_code=0 if not residual_errors else 1,
            raw_output_ref=f"runtime/verifier/adapter-convergence-round-{request.round_number}.log",
            metadata=_trusted_verifier_metadata("typescript"),
        )

    results = _run_runtime_bridge(tmp_path, convergence_verifier=convergence_verifier)

    assert len(results) == 1
    assert results[0]["success"] is True
    assert [request.round_number for request in requests] == [0, 1]
    assert requests[0].receipts == ()
    assert len(requests[1].receipts) == 1
    repair_kernel = results[0]["result"]["repair_kernel"]
    evidence = repair_kernel["revalidation_evidence"]
    assert repair_kernel["authoritative"] is True
    assert repair_kernel["requires_revalidation"] is False
    assert repair_kernel["convergence_status"] == "converged"
    assert repair_kernel["converged"] is True
    assert repair_kernel["convergence_round_count"] == 1
    assert repair_kernel["final_diagnostics"] == []
    assert repair_kernel["coverage_report"]["total_diagnostics"] == 1
    assert repair_kernel["authority_hash"]
    assert repair_kernel["projection_hash"]
    assert repair_kernel["errors_before"] == 1
    assert repair_kernel["errors_after"] == 0
    assert repair_kernel["net_error_reduction"] == 1
    assert evidence["command"] == ["rtk", "tsc", "--noEmit"]
    assert evidence["exit_code"] == 0
    assert evidence["raw_output_ref"] == "runtime/verifier/adapter-convergence-round-1.log"


def test_runtime_bridge_convergence_failure_retains_public_payload(tmp_path: Path) -> None:
    def convergence_verifier(
        request: DirectorRepairConvergenceVerifierRequestV1,
    ) -> DirectorRepairVerifierSnapshotInputV1:
        residual_errors = (_QUALITY_ERROR,) if request.round_number == 0 else (_RESIDUAL_ERROR,)
        return DirectorRepairVerifierSnapshotInputV1(
            residual_artifact_quality_errors=residual_errors,
            command=("rtk", "tsc", "--noEmit"),
            exit_code=1,
            raw_output_ref=f"runtime/verifier/adapter-failure-round-{request.round_number}.log",
            metadata=_trusted_verifier_metadata("typescript"),
        )

    results = _run_runtime_bridge(
        tmp_path,
        convergence_verifier=convergence_verifier,
        max_rounds=1,
    )

    assert len(results) == 1
    assert results[0]["success"] is False
    result = results[0]["result"]
    assert result["error_code"] == "max_rounds_exhausted"
    assert len(result["receipts"]) == 1
    assert len(result["rounds"]) == 1
    assert result["final_diagnostics"][0]["code"] == "typescript_ts2304"
    repair_kernel = result["repair_kernel"]
    assert repair_kernel["convergence_status"] == "max_rounds_exhausted"
    assert repair_kernel["convergence_round_count"] == 1
    assert len(repair_kernel["receipts"]) == 1
    receipt = repair_kernel["receipts"][0]
    assert receipt["authority_hash"]
    assert receipt["projection_hash"]
    assert receipt["requires_revalidation"] is False
    assert receipt["revalidation_evidence"]["exit_code"] == 1
    assert receipt["errors_before"] == 1
    assert receipt["errors_after"] == 1
    assert repair_kernel["coverage_report"]["total_diagnostics"] == 1
    assert repair_kernel["final_diagnostics"][0]["code"] == "typescript_ts2304"
    assert repair_kernel["metadata"]["status"] == "max_rounds_exhausted"


def test_runtime_bridge_convergence_coverage_gap_fails_without_verifier(tmp_path: Path) -> None:
    verifier_called = False

    def convergence_verifier(
        _: DirectorRepairConvergenceVerifierRequestV1,
    ) -> DirectorRepairVerifierSnapshotInputV1:
        nonlocal verifier_called
        verifier_called = True
        raise AssertionError("coverage gap should fail before verifier")

    results = _run_runtime_bridge(
        tmp_path,
        convergence_verifier=convergence_verifier,
        artifact_quality_errors=(_UNCOVERED_ERROR,),
    )

    assert verifier_called is False
    assert len(results) == 1
    assert results[0]["success"] is False
    result = results[0]["result"]
    assert result["error_code"] == "coverage_gap_uncovered_diagnostics"
    assert result["receipts"] == []
    assert result["rounds"] == []
    assert result["final_diagnostics"][0]["code"] == "declared_target_missing"
    repair_kernel = result["repair_kernel"]
    assert repair_kernel["convergence_status"] == "coverage_gap_uncovered_diagnostics"
    assert repair_kernel["coverage_report"]["total_diagnostics"] == 1
    assert repair_kernel["metadata"]["coverage_gap_count"] == 1


def test_rust_post_execution_bridge_runs_dedicated_method_self_runtime_binding(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    cargo = tmp_path / "Cargo.toml"
    source = tmp_path / "src" / "lib.rs"
    source.parent.mkdir(parents=True)
    cargo.write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    broken = "pub struct Demo;\nimpl Demo {\n    pub fn foo(&) -> i32 { 1 }\n    pub fn bar(&mut) { }\n}\n"
    source.write_text(broken, encoding="utf-8")
    adapter = _FakeAdapter(tmp_path)
    adapter.artifact_quality_errors = [
        "error: expected parameter name, found `)`\n --> src/lib.rs:3:17\n  |\n3 |     pub fn foo(&) -> i32 { 1 }",
        "error: expected parameter name, found `)`\n --> src/lib.rs:4:20\n  |\n4 |     pub fn bar(&mut) { }",
    ]

    from polaris.cells.roles.adapters.internal.director.deterministic_repairs import rust_repairs

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", _FakeDirectorToolExecutor)
    monkeypatch.setattr(rust_repairs, "run_all_rust_post_repairs", lambda _: [])

    results = post_execution_repair_bridge._run_rust_post_repairs(
        adapter,
        tmp_path,
        task_id="task-rust-method-self",
    )

    assert len(results) == 1
    assert results[0]["tool_name"] == "edit_file"
    payload = results[0]["result"]
    assert payload["source_tool"] == "deterministic_rust_method_self_signature_repair"
    assert payload["file"] == "src/lib.rs"
    assert payload["repair_kernel"]["owner_cell"] == "director.runtime"
    assert payload["repair_kernel"]["planning"]["plan_summary"]["rule_id"] == "rust.method_self_signature"
    assert payload["repair_kernel"]["planning"]["plan_summary"]["source_tool"] == (
        "deterministic_rust_method_self_signature_repair"
    )
    repaired = source.read_text(encoding="utf-8")
    assert "pub fn foo(&self)" in repaired
    assert "pub fn bar(&mut self)" in repaired


def test_rust_post_execution_bridge_runs_dedicated_wrong_crate_path_runtime_binding_with_edit_file(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    cargo = tmp_path / "Cargo.toml"
    source = tmp_path / "src" / "lib.rs"
    source.parent.mkdir(parents=True)
    cargo.write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    source.write_text("    use crate::recipe::Recipe;\npub fn keep() {}\n", encoding="utf-8")
    adapter = _FakeAdapter(tmp_path)
    adapter.artifact_quality_errors = [
        "error[E0432]: unresolved import `crate::recipe`\n"
        " --> src/lib.rs:1:16\n"
        "  |\n"
        "1 |     use crate::recipe::Recipe;\n"
        "  |                ^^^^^^ help: a similar path exists: `models::recipe`\n"
        "help: a similar path exists\n"
        "  |\n"
        "1 | use crate::models::recipe::Recipe;\n",
    ]

    from polaris.cells.roles.adapters.internal.director.deterministic_repairs import rust_repairs

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", _FakeDirectorToolExecutor)
    monkeypatch.setattr(rust_repairs, "run_all_rust_post_repairs", lambda _: [])

    results = post_execution_repair_bridge._run_rust_post_repairs(
        adapter,
        tmp_path,
        task_id="task-rust-wrong-crate-path",
    )

    assert len(results) == 1
    assert results[0]["tool_name"] == "edit_file"
    payload = results[0]["result"]
    assert payload["source_tool"] == "deterministic_rust_wrong_crate_path_repair"
    assert payload["source_tool"] != "deterministic_rust_post_repair"
    assert payload["file"] == "src/lib.rs"
    assert payload["operation"] == "edit_file"
    assert payload["repair_kernel"]["owner_cell"] == "director.runtime"
    assert payload["repair_kernel"]["planning"]["plan_summary"]["rule_id"] == "rust.wrong_crate_path"
    assert payload["repair_kernel"]["planning"]["plan_summary"]["source_tool"] == (
        "deterministic_rust_wrong_crate_path_repair"
    )
    repaired = source.read_text(encoding="utf-8")
    assert "    use crate::models::recipe::Recipe;" in repaired
    assert "use crate::recipe::Recipe;" not in repaired


def test_rust_post_execution_bridge_runs_dedicated_copy_derive_runtime_binding_with_edit_file(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    cargo = tmp_path / "Cargo.toml"
    source = tmp_path / "src" / "lib.rs"
    source.parent.mkdir(parents=True)
    cargo.write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    source.write_text(
        "#[derive(Debug, Clone, Copy)]\npub struct Demo { value: String }\n",
        encoding="utf-8",
    )
    adapter = _FakeAdapter(tmp_path)
    adapter.artifact_quality_errors = [
        "error[E0204]: the trait `Copy` cannot be implemented for this type\n"
        " --> src/lib.rs:2:10\n"
        "  |\n"
        "2 | pub struct Demo { value: String }\n"
        "  |          ^^^^",
    ]

    from polaris.cells.roles.adapters.internal.director.deterministic_repairs import rust_repairs

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", _FakeDirectorToolExecutor)
    monkeypatch.setattr(rust_repairs, "run_all_rust_post_repairs", lambda _: [])

    results = post_execution_repair_bridge._run_rust_post_repairs(
        adapter,
        tmp_path,
        task_id="task-rust-copy-derive",
    )

    assert len(results) == 1
    assert results[0]["tool_name"] == "edit_file"
    payload = results[0]["result"]
    assert payload["source_tool"] == "deterministic_rust_incompatible_copy_derive_repair"
    assert payload["source_tool"] != "deterministic_rust_post_repair"
    assert payload["source_tool"] != "deterministic_rust_derive_repair"
    assert payload["file"] == "src/lib.rs"
    assert payload["operation"] == "edit_file"
    assert payload["repair_kernel"]["owner_cell"] == "director.runtime"
    assert payload["repair_kernel"]["planning"]["plan_summary"]["rule_id"] == "rust.incompatible_copy_derive"
    assert payload["repair_kernel"]["planning"]["plan_summary"]["source_tool"] == (
        "deterministic_rust_incompatible_copy_derive_repair"
    )
    repaired = source.read_text(encoding="utf-8")
    assert "#[derive(Debug, Clone)]" in repaired
    assert "Copy" not in repaired


def test_rust_post_execution_bridge_runs_dedicated_unused_import_runtime_binding_with_edit_file(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    cargo = tmp_path / "Cargo.toml"
    source = tmp_path / "src" / "lib.rs"
    source.parent.mkdir(parents=True)
    cargo.write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    source.write_text("use foo::{A, B};\npub fn keep() {}\n", encoding="utf-8")
    adapter = _FakeAdapter(tmp_path)
    adapter.artifact_quality_errors = [
        "warning: unused import: `B`\n --> src/lib.rs:1:14\n  |\n1 | use foo::{A, B};\n  |              ^\n",
    ]

    from polaris.cells.roles.adapters.internal.director.deterministic_repairs import rust_repairs

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", _FakeDirectorToolExecutor)
    monkeypatch.setattr(rust_repairs, "run_all_rust_post_repairs", lambda _: [])

    results = post_execution_repair_bridge._run_rust_post_repairs(
        adapter,
        tmp_path,
        task_id="task-rust-unused-import",
    )

    assert len(results) == 1
    assert results[0]["tool_name"] == "edit_file"
    payload = results[0]["result"]
    assert payload["source_tool"] == "deterministic_rust_unused_import_repair"
    assert payload["source_tool"] != "deterministic_rust_post_repair"
    assert payload["file"] == "src/lib.rs"
    assert payload["operation"] == "edit_file"
    assert payload["repair_kernel"]["owner_cell"] == "director.runtime"
    assert payload["repair_kernel"]["planning"]["plan_summary"]["rule_id"] == "rust.unused_import"
    assert payload["repair_kernel"]["planning"]["plan_summary"]["source_tool"] == (
        "deterministic_rust_unused_import_repair"
    )
    repaired = source.read_text(encoding="utf-8")
    assert "use foo::{A};" in repaired
    assert "use foo::{A, B};" not in repaired


def test_rust_post_execution_bridge_runs_dedicated_unresolved_pub_use_runtime_binding(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    cargo = tmp_path / "Cargo.toml"
    source = tmp_path / "src" / "lib.rs"
    source.parent.mkdir(parents=True)
    cargo.write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    source.write_text("pub use foo::{A, Missing, B};\npub fn keep() {}\n", encoding="utf-8")
    adapter = _FakeAdapter(tmp_path)
    adapter.artifact_quality_errors = [
        "error[E0432]: unresolved import `foo::Missing`\n"
        " --> src/lib.rs:1:18\n"
        "  |\n"
        "1 | pub use foo::{A, Missing, B};\n"
        "  |                  ^^^^^^^ no `Missing` in `foo`",
    ]

    from polaris.cells.roles.adapters.internal.director.deterministic_repairs import rust_repairs

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", _FakeDirectorToolExecutor)
    monkeypatch.setattr(rust_repairs, "run_all_rust_post_repairs", lambda _: [])

    results = post_execution_repair_bridge._run_rust_post_repairs(
        adapter,
        tmp_path,
        task_id="task-rust-pub-use",
    )

    assert len(results) == 1
    assert results[0]["tool_name"] == "edit_file"
    payload = results[0]["result"]
    assert payload["source_tool"] == "deterministic_rust_unresolved_pub_use_repair"
    assert payload["file"] == "src/lib.rs"
    assert payload["repair_kernel"]["owner_cell"] == "director.runtime"
    assert payload["repair_kernel"]["planning"]["plan_summary"]["rule_id"] == "rust.unresolved_pub_use"
    assert payload["repair_kernel"]["planning"]["plan_summary"]["source_tool"] == (
        "deterministic_rust_unresolved_pub_use_repair"
    )
    repaired = source.read_text(encoding="utf-8")
    assert "pub use foo::{A, B};" in repaired
    assert "Missing" not in repaired


def test_rust_post_execution_bridge_runs_dedicated_trait_import_runtime_binding_with_edit_file(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    cargo = tmp_path / "Cargo.toml"
    source = tmp_path / "src" / "lib.rs"
    source.parent.mkdir(parents=True)
    cargo.write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    broken = "use crate::bar::Bar;\n\npub fn run() {}\n"
    source.write_text(broken, encoding="utf-8")
    adapter = _FakeAdapter(tmp_path)
    adapter.artifact_quality_errors = [
        "error[E0599]: no method named `render` found for struct `Widget` in the current scope\n"
        " --> src/lib.rs:3:12\n"
        "  |\n"
        "help: trait `Renderable` which provides `render` is implemented but not in scope; "
        "perhaps add a use for it:\n"
        "  |\n"
        "1 + use crate::render::Renderable;\n",
    ]

    from polaris.cells.roles.adapters.internal.director.deterministic_repairs import rust_repairs

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", _FakeDirectorToolExecutor)
    monkeypatch.setattr(rust_repairs, "run_all_rust_post_repairs", lambda _: [])

    results = post_execution_repair_bridge._run_rust_post_repairs(
        adapter,
        tmp_path,
        task_id="task-rust-trait-import",
    )

    assert len(results) == 1
    assert results[0]["tool_name"] == "edit_file"
    payload = results[0]["result"]
    assert payload["source_tool"] == "deterministic_rust_trait_import_repair"
    assert payload["file"] == "src/lib.rs"
    assert payload["operation"] == "edit_file"
    assert payload["repair_kernel"]["owner_cell"] == "director.runtime"
    assert payload["repair_kernel"]["planning"]["plan_summary"]["rule_id"] == "rust.trait_import"
    assert payload["repair_kernel"]["planning"]["plan_summary"]["source_tool"] == (
        "deterministic_rust_trait_import_repair"
    )
    repaired = source.read_text(encoding="utf-8")
    assert "use crate::render::Renderable;" in repaired
    assert payload["source_tool"] != "deterministic_rust_post_repair"


def test_rust_post_execution_bridge_runs_dedicated_line_suggestion_runtime_binding_with_edit_file(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    cargo = tmp_path / "Cargo.toml"
    source = tmp_path / "src" / "lib.rs"
    source.parent.mkdir(parents=True)
    cargo.write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    broken = "fn takes(_: &String) {}\nfn main() {\n    takes(value)\n}\n"
    source.write_text(broken, encoding="utf-8")
    adapter = _FakeAdapter(tmp_path)
    adapter.artifact_quality_errors = [
        "error[E0308]: mismatched types\n"
        " --> src/lib.rs:3:11\n"
        "  |\n"
        "3 |     takes(value)\n"
        "  |           ^^^^^ expected `&String`, found `String`\n"
        "help: consider borrowing here\n"
        "  |\n"
        "3 |     takes(&value)",
    ]

    from polaris.cells.roles.adapters.internal.director.deterministic_repairs import rust_repairs

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", _FakeDirectorToolExecutor)
    monkeypatch.setattr(rust_repairs, "run_all_rust_post_repairs", lambda _: [])

    results = post_execution_repair_bridge._run_rust_post_repairs(
        adapter,
        tmp_path,
        task_id="task-rust-line-suggestion",
    )

    assert len(results) == 1
    assert results[0]["tool_name"] == "edit_file"
    payload = results[0]["result"]
    assert payload["source_tool"] == "deterministic_rust_line_suggestion_repair"
    assert payload["file"] == "src/lib.rs"
    assert payload["operation"] == "edit_file"
    assert payload["repair_kernel"]["owner_cell"] == "director.runtime"
    assert payload["repair_kernel"]["planning"]["plan_summary"]["rule_id"] == "rust.line_suggestion"
    assert payload["repair_kernel"]["planning"]["plan_summary"]["source_tool"] == (
        "deterministic_rust_line_suggestion_repair"
    )
    repaired = source.read_text(encoding="utf-8")
    assert "takes(&value)" in repaired
    assert "takes(value)" not in repaired


def test_rust_post_execution_bridge_runs_missing_module_runtime_binding_with_write_file(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    cargo = tmp_path / "Cargo.toml"
    source = tmp_path / "src" / "lib.rs"
    source.parent.mkdir(parents=True)
    cargo.write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    source.write_text("pub mod models;\n", encoding="utf-8")
    adapter = _FakeAdapter(tmp_path)
    adapter.artifact_quality_errors = [
        "error[E0583]: file not found for module `models`\n"
        " --> src/lib.rs:1:1\n"
        "  |\n"
        "1 | pub mod models;\n"
        "  | ^^^^^^^^^^^^^^^\n"
        "  |\n"
        '  = help: to create the module `models`, create file "src/models.rs" or "src/models/mod.rs"\n',
    ]

    from polaris.cells.roles.adapters.internal.director.deterministic_repairs import rust_repairs

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", _FakeDirectorToolExecutor)
    monkeypatch.setattr(rust_repairs, "run_all_rust_post_repairs", lambda _: [])

    results = post_execution_repair_bridge._run_rust_post_repairs(
        adapter,
        tmp_path,
        task_id="task-rust-missing-module",
    )

    assert len(results) == 1
    assert results[0]["tool_name"] == "write_file"
    payload = results[0]["result"]
    assert payload["source_tool"] == "deterministic_rust_missing_module_file_repair"
    assert payload["source_tool"] != "deterministic_rust_post_repair"
    assert payload["file"] == "src/models.rs"
    assert payload["operation"] == "write_file"
    assert payload["repair_kernel"]["owner_cell"] == "director.runtime"
    assert payload["repair_kernel"]["planning"]["plan_summary"]["rule_id"] == "rust.missing_module_file"
    assert payload["repair_kernel"]["planning"]["plan_summary"]["source_tool"] == (
        "deterministic_rust_missing_module_file_repair"
    )
    created = (tmp_path / "src" / "models.rs").read_text(encoding="utf-8")
    assert "Polaris marker: rust.missing_module_file" in created
    assert "pub struct" not in created


def test_rust_post_execution_bridge_runs_duplicate_module_runtime_before_legacy_aggregate(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    cargo = tmp_path / "Cargo.toml"
    lib = tmp_path / "src" / "lib.rs"
    duplicate = tmp_path / "src" / "models.rs"
    sibling = tmp_path / "src" / "models" / "mod.rs"
    duplicate_path = "src/models.rs"
    sibling_path = "src/models/mod.rs"
    generated = "// Polaris generated module stub\n"
    real = "pub struct Model;\n"
    raw_error = (
        f'error[E0761]: file for module `models` found at both "{duplicate_path}" and "{sibling_path}"\n'
        " --> src/lib.rs:1:1\n"
        "  |\n"
        "1 | pub mod models;\n"
        "  | ^^^^^^^^^^^^^^^\n"
    )
    lib.parent.mkdir(parents=True)
    sibling.parent.mkdir(parents=True)
    cargo.write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    lib.write_text("pub mod models;\n", encoding="utf-8")
    duplicate.write_text(generated, encoding="utf-8")
    sibling.write_text(real, encoding="utf-8")
    adapter = _FakeAdapter(tmp_path)
    adapter.artifact_quality_errors = [raw_error]
    calls: list[tuple[str, str]] = []

    from polaris.cells.roles.adapters.internal.director.deterministic_repairs import rust_repairs

    def fake_runtime_repair_with_director_tools(
        adapter_arg: Any,
        *,
        workspace_path: Path,
        task_id: str,
        source_tool: str,
        executor_factory: Any,
        base_files: dict[str, str],
        artifact_quality_errors: tuple[str, ...] = (),
        allowed_paths: tuple[str, ...] = (),
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        del adapter_arg, workspace_path, task_id, executor_factory, kwargs
        calls.append(("runtime", source_tool))
        if source_tool != RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL:
            return []
        assert base_files[duplicate_path] == generated
        assert base_files[sibling_path] == real
        assert duplicate_path in allowed_paths
        assert sibling_path in allowed_paths
        assert raw_error.strip() in artifact_quality_errors
        return [
            {
                "tool": "delete_file",
                "tool_name": "delete_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": source_tool,
                    "file": duplicate_path,
                    "operation": "delete_file",
                    "bytes_written": 0,
                    "repair_kernel": {"owner_cell": "director.runtime"},
                },
            }
        ]

    def fail_legacy_duplicate_module_batch(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError("duplicate module files must run through runtime bridge, not legacy aggregate")

    def fake_cargo_check_stderr(_: Path) -> str:
        calls.append(("aggregate", "cargo_check"))
        return raw_error

    monkeypatch.setattr(
        post_execution_repair_bridge,
        "run_runtime_repair_with_director_tools",
        fake_runtime_repair_with_director_tools,
    )
    monkeypatch.setattr(rust_repairs, "repair_rust_duplicate_module_files", fail_legacy_duplicate_module_batch)
    monkeypatch.setattr(rust_repairs, "repair_rust_missing_fields", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(rust_repairs, "repair_rust_lib_root_facade", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(rust_repairs, "_run_cargo_check_stderr", fake_cargo_check_stderr)

    results = post_execution_repair_bridge._run_rust_post_repairs(
        adapter,
        tmp_path,
        task_id="task-rust-duplicate-module",
    )

    assert calls.index(("runtime", RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL)) < calls.index(("aggregate", "cargo_check"))
    payloads = [item["result"] for item in results]
    assert [payload["source_tool"] for payload in payloads] == [RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL]
    assert payloads[0]["repair_kernel"]["owner_cell"] == "director.runtime"


def test_rust_post_execution_shadow_replay_projects_non_authoritative_receipt_metadata(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    cargo = tmp_path / "Cargo.toml"
    source = tmp_path / "src" / "lib.rs"
    source.parent.mkdir(parents=True)
    cargo.write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    original = "pub fn demo() -> i32 { 1 }\n"
    updated = "pub fn demo() -> i32 { 2 }\n"
    source.write_text(original, encoding="utf-8")
    adapter = _FakeAdapter(tmp_path)
    adapter.artifact_quality_errors = []

    from polaris.cells.roles.adapters.internal.director.deterministic_repairs import rust_repairs

    shadow_workspaces: list[Path] = []

    def fake_run_all_rust_post_repairs(shadow_workspace: Path) -> list[dict[str, Any]]:
        shadow_path = Path(shadow_workspace).resolve()
        shadow_workspaces.append(shadow_path)
        assert shadow_path != tmp_path.resolve()
        shadow_source = shadow_path / "src" / "lib.rs"
        assert shadow_source.read_text(encoding="utf-8") == original
        shadow_source.write_text(updated, encoding="utf-8")
        return [
            {
                "file": "src/lib.rs",
                "source_tool": "deterministic_rust_missing_fields_repair",
                "action": "rust_missing_fields",
                "phase": "post_execution",
                "priority": 7,
                "round_number": 1,
            }
        ]

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", _FakeDirectorToolExecutor)
    monkeypatch.setattr(rust_repairs, "run_all_rust_post_repairs", fake_run_all_rust_post_repairs)
    monkeypatch.setattr(
        post_execution_repair_bridge,
        "_runtime_executable_source_tools",
        lambda: frozenset(
            {
                "deterministic_rust_dependency_repair",
                "deterministic_rust_lib_root_facade_repair",
            }
        ),
    )

    results = post_execution_repair_bridge._run_rust_post_repairs(
        adapter,
        tmp_path,
        task_id="task-rust-shadow-replay",
    )

    assert len(shadow_workspaces) == 1
    assert len(results) == 1
    assert results[0]["tool_name"] == "edit_file"
    assert results[0]["success"] is True
    assert source.read_text(encoding="utf-8") == updated

    payload = results[0]["result"]
    assert payload["source_tool"] == "deterministic_rust_missing_fields_repair"
    assert payload["source_tools"] == ["deterministic_rust_missing_fields_repair"]
    assert payload["legacy_shadow_source_tools"] == ["deterministic_rust_missing_fields_repair"]
    assert payload["legacy_aggregate_remaining_source_tools"] == [
        "deterministic_rust_missing_fields_repair",
    ]
    assert payload["legacy_aggregate_shadow_replay_allowed_source_tools"] == [
        "deterministic_rust_missing_fields_repair",
    ]
    assert payload["remaining_legacy_subcases"] == [
        "deterministic_rust_missing_fields_repair:field_declaration",
    ]
    assert payload["runtime_migrated_subcases"] == [
        "deterministic_rust_lib_root_facade_repair:export_or_module_declaration",
        "deterministic_rust_lib_root_facade_repair:path_rewrite",
    ]
    assert payload["legacy_shadow_workspace"] is True
    assert payload["legacy_shadow_replay"] is True
    assert payload["runtime_authoritative_plan"] is False
    assert payload["receipt_authority"] == "non_authoritative_shadow_replay_projection"
    assert payload["applied_tool_name"] == "edit_file"
    assert payload["before_hash"] == sha256_text(original)
    assert payload["after_hash"] == sha256_text(updated)
    assert payload["evidence_status"] == "missing_evidence"
    assert payload["receipt_status"] == "pending_revalidation"
    assert payload["evidence_missing"] is True
    assert payload["verifier_evidence_present"] is False
    assert payload["repair_success_verdict"] is False

    repair_kernel = payload["repair_kernel"]
    assert repair_kernel["owner_cell"] == "roles.adapters.legacy_strategy_host"
    assert repair_kernel["authoritative"] is False
    assert repair_kernel["runtime_authoritative_plan"] is False
    assert repair_kernel["requires_revalidation"] is True
    assert repair_kernel["receipt_authority"] == "non_authoritative_shadow_replay_projection"
    assert repair_kernel["source_tools"] == ["deterministic_rust_missing_fields_repair"]
    assert repair_kernel["legacy_aggregate_remaining_source_tools"] == [
        "deterministic_rust_missing_fields_repair",
    ]
    assert repair_kernel["remaining_legacy_subcases"] == [
        "deterministic_rust_missing_fields_repair:field_declaration",
    ]
    assert repair_kernel["runtime_migrated_subcases"] == [
        "deterministic_rust_lib_root_facade_repair:export_or_module_declaration",
        "deterministic_rust_lib_root_facade_repair:path_rewrite",
    ]
    assert repair_kernel["applied_tool_name"] == "edit_file"
    assert repair_kernel["files_changed"] == ["src/lib.rs"]
    assert repair_kernel["before_hashes"] == {"src/lib.rs": sha256_text(original)}
    assert repair_kernel["after_hashes"] == {"src/lib.rs": sha256_text(updated)}
    assert repair_kernel["evidence_status"] == "missing_evidence"
    assert repair_kernel["status"] == "pending_revalidation"
    assert repair_kernel["evidence_missing_reason"] == "missing_revalidation_evidence"
    assert repair_kernel["repair_success_verdict"] is False


@pytest.mark.parametrize(
    ("source_tool", "relative_path"),
    [
        ("deterministic_rust_dependency_repair", "Cargo.toml"),
        ("deterministic_rust_missing_module_file_repair", "src/generated.rs"),
    ],
)
def test_rust_post_execution_shadow_replay_blocks_runtime_migrated_source_tools(
    tmp_path: Path,
    monkeypatch: Any,
    source_tool: str,
    relative_path: str,
) -> None:
    cargo = tmp_path / "Cargo.toml"
    source = tmp_path / "src" / "lib.rs"
    source.parent.mkdir(parents=True)
    original_cargo = '[package]\nname = "demo"\nversion = "0.1.0"\n'
    original_source = "pub fn demo() -> i32 { 1 }\n"
    cargo.write_text(original_cargo, encoding="utf-8")
    source.write_text(original_source, encoding="utf-8")
    adapter = _FakeAdapter(tmp_path)
    adapter.artifact_quality_errors = []

    from polaris.cells.roles.adapters.internal.director.deterministic_repairs import rust_repairs

    def fake_run_all_rust_post_repairs(shadow_workspace: Path) -> list[dict[str, Any]]:
        shadow_path = Path(shadow_workspace)
        if relative_path == "Cargo.toml":
            target = shadow_path / relative_path
            target.write_text(original_cargo + '\n[dependencies]\nserde = "1"\n', encoding="utf-8")
        elif relative_path == "src/generated.rs":
            target = shadow_path / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("pub fn generated() {}\n", encoding="utf-8")
        else:
            target = shadow_path / relative_path
            target.write_text("pub fn demo() -> i32 { 2 }\n", encoding="utf-8")
        return [
            {
                "file": relative_path,
                "source_tool": source_tool,
                "action": "runtime_migrated_shadow_replay",
            }
        ]

    class FailingDirectorToolExecutor:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("migrated Rust aggregate shadow replay must not reach DirectorToolExecutor")

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", FailingDirectorToolExecutor)
    monkeypatch.setattr(rust_repairs, "run_all_rust_post_repairs", fake_run_all_rust_post_repairs)
    monkeypatch.setattr(
        post_execution_repair_bridge,
        "_runtime_executable_source_tools",
        lambda: frozenset({source_tool}),
    )

    results = post_execution_repair_bridge._run_rust_post_repairs(
        adapter,
        tmp_path,
        task_id="task-rust-shadow-migrated-blocked",
    )

    assert len(results) == 1
    assert results[0]["success"] is False
    assert cargo.read_text(encoding="utf-8") == original_cargo
    assert source.read_text(encoding="utf-8") == original_source
    assert not (tmp_path / "src" / "generated.rs").exists()

    payload = results[0]["result"]
    assert payload["blocked"] is True
    assert payload["applied_tool_name"] == "blocked_legacy_shadow_replay"
    assert payload["legacy_shadow_applied_via_director_tools"] is False
    assert payload["legacy_aggregate_blocked_source_tools"] == [source_tool]
    assert payload["legacy_aggregate_blocked_migrated_source_tools"] == [source_tool]
    assert payload["legacy_aggregate_blocked_source_tool_count"] == 1
    assert payload["legacy_aggregate_blocked_source_tool_counts"] == {source_tool: 1}
    assert payload["legacy_aggregate_blocked_migrated_source_tool_count"] == 1
    assert payload["legacy_aggregate_blocked_migrated_source_tool_counts"] == {source_tool: 1}
    assert payload["legacy_aggregate_shadow_replay_authoritative"] is False
    assert payload["legacy_aggregate_cutover_ready"] is False
    assert f"blocked_migrated_source_tool:{source_tool}" in payload["legacy_aggregate_cutover_blockers"]
    assert source_tool not in payload["legacy_aggregate_shadow_replay_allowed_source_tools"]
    assert payload["migration_blocker"] == "legacy_aggregate_shadow_replay_source_tool_not_remaining"
    assert payload["evidence_failure_reason"] == "legacy_aggregate_shadow_replay_source_tool_not_remaining"

    repair_kernel = payload["repair_kernel"]
    assert repair_kernel["status"] == "blocked"
    assert repair_kernel["blocked"] is True
    assert repair_kernel["legacy_aggregate_blocked_source_tools"] == [source_tool]
    assert repair_kernel["legacy_aggregate_blocked_migrated_source_tools"] == [source_tool]
    assert repair_kernel["legacy_aggregate_blocked_migrated_source_tool_count"] == 1
    assert repair_kernel["legacy_aggregate_shadow_replay_authoritative"] is False
    assert repair_kernel["legacy_aggregate_cutover_ready"] is False
    assert repair_kernel["migration_blocker"] == "legacy_aggregate_shadow_replay_source_tool_not_remaining"


def test_rust_post_execution_shadow_replay_blocks_missing_fields_subcase_after_runtime(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    cargo = tmp_path / "Cargo.toml"
    source = tmp_path / "src" / "lib.rs"
    source.parent.mkdir(parents=True)
    cargo.write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    original = "pub struct Demo;\n"
    updated = "pub struct Demo {\n    pub name: String,\n}\n"
    source.write_text(original, encoding="utf-8")
    adapter = _FakeAdapter(tmp_path)
    adapter.artifact_quality_errors = []
    missing_fields_source_tool = "deterministic_rust_missing_fields_repair"
    missing_fields_subcase = f"{missing_fields_source_tool}:field_declaration"
    lib_root_source_tool = "deterministic_rust_lib_root_facade_repair"

    from polaris.cells.roles.adapters.internal.director.deterministic_repairs import rust_repairs

    def fake_run_all_rust_post_repairs(shadow_workspace: Path) -> list[dict[str, Any]]:
        shadow_source = Path(shadow_workspace) / "src" / "lib.rs"
        shadow_source.write_text(updated, encoding="utf-8")
        return [
            {
                "file": "src/lib.rs",
                "source_tool": missing_fields_source_tool,
                "action": "legacy_missing_fields_field_declaration",
            }
        ]

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", _FakeDirectorToolExecutor)
    monkeypatch.setattr(rust_repairs, "run_all_rust_post_repairs", fake_run_all_rust_post_repairs)
    monkeypatch.setattr(
        post_execution_repair_bridge,
        "_runtime_executable_source_tools",
        lambda: frozenset({missing_fields_source_tool}),
    )

    results = post_execution_repair_bridge._run_rust_post_repairs(
        adapter,
        tmp_path,
        task_id="task-rust-missing-fields-subcase",
    )

    assert len(results) == 1
    assert results[0]["success"] is False
    assert results[0]["tool_name"] == "write_file"
    assert source.read_text(encoding="utf-8") == original

    payload = results[0]["result"]
    assert payload["blocked"] is True
    assert payload["source_tool"] == missing_fields_source_tool
    assert payload["legacy_shadow_source_tools"] == [missing_fields_source_tool]
    assert payload["legacy_shadow_applied_via_director_tools"] is False
    assert payload["applied_tool_name"] == "blocked_legacy_shadow_replay"
    assert payload["legacy_aggregate_shadow_replay_non_authoritative"] is True
    assert payload["legacy_aggregate_shadow_replay_authoritative"] is False
    assert payload["legacy_aggregate_remaining_source_tools"] == [lib_root_source_tool]
    assert payload["legacy_aggregate_shadow_replay_allowed_source_tools"] == [lib_root_source_tool]
    assert missing_fields_subcase not in payload["remaining_legacy_subcases"]
    assert missing_fields_subcase in payload["runtime_migrated_subcases"]
    assert missing_fields_subcase not in payload["legacy_aggregate_remaining_legacy_subcases"]
    assert missing_fields_subcase in payload["legacy_aggregate_runtime_migrated_subcases"]
    assert payload["legacy_aggregate_blocked_migrated_subcases"] == [missing_fields_subcase]
    assert f"blocked_migrated_subcase:{missing_fields_subcase}" in payload["legacy_aggregate_cutover_blockers"]
    assert payload["legacy_aggregate_cutover_ready"] is False
    assert payload["receipt_authority"] == "non_authoritative_shadow_replay_projection"
    assert payload["runtime_authoritative_plan"] is False
    assert payload["repair_success_verdict"] is False

    evidence = payload["legacy_aggregate_cutover_readiness_evidence"]
    assert evidence["shadow_replay_non_authoritative"] is True
    assert evidence["shadow_replay_authority_boundary"] == ("legacy_shadow_replay_projection_only_not_runtime_receipt")
    assert evidence["remaining_source_tools"] == [lib_root_source_tool]
    assert missing_fields_subcase not in evidence["remaining_legacy_subcases"]
    assert missing_fields_subcase in evidence["runtime_migrated_subcases"]
    assert evidence["blocked_migrated_subcases"] == [missing_fields_subcase]

    repair_kernel = payload["repair_kernel"]
    assert repair_kernel["blocked"] is True
    assert repair_kernel["authoritative"] is False
    assert repair_kernel["legacy_aggregate_shadow_replay_non_authoritative"] is True
    assert missing_fields_subcase not in repair_kernel["legacy_aggregate_remaining_legacy_subcases"]
    assert missing_fields_subcase in repair_kernel["legacy_aggregate_runtime_migrated_subcases"]
    assert repair_kernel["legacy_aggregate_blocked_migrated_subcases"] == [missing_fields_subcase]
    assert repair_kernel["legacy_aggregate_cutover_ready"] is False


def test_rust_post_execution_shadow_replay_blocks_lib_root_export_subcase_after_runtime(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    cargo = tmp_path / "Cargo.toml"
    source = tmp_path / "src" / "lib.rs"
    source.parent.mkdir(parents=True)
    cargo.write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    original = "pub fn demo() -> i32 { 1 }\n"
    updated = "pub mod external;\npub fn demo() -> i32 { 1 }\n"
    source.write_text(original, encoding="utf-8")
    adapter = _FakeAdapter(tmp_path)
    adapter.artifact_quality_errors = []
    lib_root_source_tool = "deterministic_rust_lib_root_facade_repair"
    export_subcase = f"{lib_root_source_tool}:export_or_module_declaration"
    path_rewrite_subcase = f"{lib_root_source_tool}:path_rewrite"

    from polaris.cells.roles.adapters.internal.director.deterministic_repairs import rust_repairs

    def fake_run_all_rust_post_repairs(shadow_workspace: Path) -> list[dict[str, Any]]:
        shadow_source = Path(shadow_workspace) / "src" / "lib.rs"
        shadow_source.write_text(updated, encoding="utf-8")
        return [
            {
                "file": "src/lib.rs",
                "source_tool": lib_root_source_tool,
                "action": "legacy_export_or_module_declaration",
                "module_exports": ["external"],
                "path_rewrites": [],
            }
        ]

    class FailingDirectorToolExecutor:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("runtime-migrated lib root export must not reach DirectorToolExecutor")

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", FailingDirectorToolExecutor)
    monkeypatch.setattr(rust_repairs, "run_all_rust_post_repairs", fake_run_all_rust_post_repairs)
    monkeypatch.setattr(
        post_execution_repair_bridge,
        "_runtime_executable_source_tools",
        lambda: frozenset({lib_root_source_tool}),
    )

    results = post_execution_repair_bridge._run_rust_post_repairs(
        adapter,
        tmp_path,
        task_id="task-rust-lib-root-export-subcase",
    )

    assert len(results) == 1
    assert results[0]["success"] is False
    assert source.read_text(encoding="utf-8") == original

    payload = results[0]["result"]
    assert payload["blocked"] is True
    assert payload["source_tool"] == lib_root_source_tool
    assert payload["legacy_shadow_applied_via_director_tools"] is False
    assert payload["applied_tool_name"] == "blocked_legacy_shadow_replay"
    assert payload["legacy_aggregate_blocked_source_tools"] == [lib_root_source_tool]
    assert payload["legacy_aggregate_blocked_migrated_source_tools"] == [lib_root_source_tool]
    assert payload["legacy_aggregate_remaining_source_tools"] == [
        "deterministic_rust_missing_fields_repair",
    ]
    assert payload["legacy_aggregate_shadow_replay_allowed_source_tools"] == [
        "deterministic_rust_missing_fields_repair",
    ]
    assert export_subcase not in payload["remaining_legacy_subcases"]
    assert path_rewrite_subcase not in payload["remaining_legacy_subcases"]
    assert payload["runtime_migrated_subcases"] == [export_subcase, path_rewrite_subcase]
    assert payload["legacy_aggregate_remaining_legacy_subcases"] == payload["remaining_legacy_subcases"]
    assert payload["legacy_aggregate_runtime_migrated_subcases"] == [export_subcase, path_rewrite_subcase]
    assert payload["legacy_aggregate_blocked_migrated_subcases"] == [export_subcase]
    assert f"blocked_migrated_subcase:{export_subcase}" in payload["legacy_aggregate_cutover_blockers"]
    assert payload["receipt_authority"] == "non_authoritative_shadow_replay_projection"
    assert payload["runtime_authoritative_plan"] is False
    assert payload["repair_success_verdict"] is False

    repair_kernel = payload["repair_kernel"]
    assert repair_kernel["blocked"] is True
    assert repair_kernel["remaining_legacy_subcases"] == payload["remaining_legacy_subcases"]
    assert repair_kernel["runtime_migrated_subcases"] == [export_subcase, path_rewrite_subcase]
    assert repair_kernel["legacy_aggregate_remaining_legacy_subcases"] == payload["remaining_legacy_subcases"]
    assert repair_kernel["legacy_aggregate_runtime_migrated_subcases"] == [export_subcase, path_rewrite_subcase]
    assert repair_kernel["legacy_aggregate_blocked_migrated_subcases"] == [export_subcase]
    assert repair_kernel["authoritative"] is False


def test_rust_post_execution_shadow_replay_blocks_lib_root_path_rewrite_subcase_after_runtime(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    cargo = tmp_path / "Cargo.toml"
    source = tmp_path / "src" / "lib.rs"
    source.parent.mkdir(parents=True)
    cargo.write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    original = "use demo::Thing;\npub struct Thing;\n"
    updated = "use crate::Thing;\npub struct Thing;\n"
    source.write_text(original, encoding="utf-8")
    adapter = _FakeAdapter(tmp_path)
    adapter.artifact_quality_errors = []
    lib_root_source_tool = "deterministic_rust_lib_root_facade_repair"
    export_subcase = f"{lib_root_source_tool}:export_or_module_declaration"
    path_rewrite_subcase = f"{lib_root_source_tool}:path_rewrite"

    from polaris.cells.roles.adapters.internal.director.deterministic_repairs import rust_repairs

    def fake_run_all_rust_post_repairs(shadow_workspace: Path) -> list[dict[str, Any]]:
        shadow_source = Path(shadow_workspace) / "src" / "lib.rs"
        shadow_source.write_text(updated, encoding="utf-8")
        return [
            {
                "file": "src/lib.rs",
                "source_tool": lib_root_source_tool,
                "action": "runtime_migrated_path_rewrite",
                "path_rewrites": ["demo::Thing"],
                "module_exports": [],
            }
        ]

    class FailingDirectorToolExecutor:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("runtime-migrated lib root path rewrite must not reach DirectorToolExecutor")

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", FailingDirectorToolExecutor)
    monkeypatch.setattr(rust_repairs, "run_all_rust_post_repairs", fake_run_all_rust_post_repairs)
    monkeypatch.setattr(
        post_execution_repair_bridge,
        "_runtime_executable_source_tools",
        lambda: frozenset({lib_root_source_tool}),
    )

    results = post_execution_repair_bridge._run_rust_post_repairs(
        adapter,
        tmp_path,
        task_id="task-rust-lib-root-path-rewrite-blocked",
    )

    assert len(results) == 1
    assert results[0]["success"] is False
    assert source.read_text(encoding="utf-8") == original

    payload = results[0]["result"]
    assert payload["blocked"] is True
    assert payload["legacy_aggregate_blocked_source_tools"] == [lib_root_source_tool]
    assert payload["legacy_aggregate_blocked_migrated_source_tools"] == [lib_root_source_tool]
    assert payload["legacy_aggregate_blocked_migrated_subcases"] == [path_rewrite_subcase]
    assert export_subcase not in payload["remaining_legacy_subcases"]
    assert path_rewrite_subcase not in payload["remaining_legacy_subcases"]
    assert payload["runtime_migrated_subcases"] == [export_subcase, path_rewrite_subcase]
    assert f"blocked_migrated_subcase:{path_rewrite_subcase}" in payload["legacy_aggregate_cutover_blockers"]
    assert payload["applied_tool_name"] == "blocked_legacy_shadow_replay"
    assert payload["legacy_shadow_applied_via_director_tools"] is False

    repair_kernel = payload["repair_kernel"]
    assert repair_kernel["blocked"] is True
    assert repair_kernel["legacy_aggregate_blocked_migrated_subcases"] == [path_rewrite_subcase]
    assert repair_kernel["runtime_migrated_subcases"] == [export_subcase, path_rewrite_subcase]
    assert repair_kernel["authoritative"] is False


def test_rust_post_execution_shadow_delete_diff_is_blocked_with_receipt_metadata(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    cargo = tmp_path / "Cargo.toml"
    source = tmp_path / "src" / "stale.rs"
    source.parent.mkdir(parents=True)
    cargo.write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    original = "pub fn stale() {}\n"
    source.write_text(original, encoding="utf-8")
    adapter = _FakeAdapter(tmp_path)
    adapter.artifact_quality_errors = []

    from polaris.cells.roles.adapters.internal.director.deterministic_repairs import rust_repairs

    def fake_run_all_rust_post_repairs(shadow_workspace: Path) -> list[dict[str, Any]]:
        shadow_source = Path(shadow_workspace) / "src" / "stale.rs"
        shadow_source.unlink()
        return [
            {
                "file": "src/stale.rs",
                "source_tool": "deterministic_rust_missing_fields_repair",
                "action": "remove_stale_module",
            }
        ]

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", _FakeDirectorToolExecutor)
    monkeypatch.setattr(rust_repairs, "run_all_rust_post_repairs", fake_run_all_rust_post_repairs)
    monkeypatch.setattr(
        post_execution_repair_bridge,
        "_runtime_executable_source_tools",
        lambda: frozenset({"deterministic_rust_dependency_repair"}),
    )

    results = post_execution_repair_bridge._run_rust_post_repairs(
        adapter,
        tmp_path,
        task_id="task-rust-shadow-delete",
    )

    assert len(results) == 1
    assert results[0]["tool_name"] == "write_file"
    assert results[0]["success"] is False
    assert source.exists()
    assert source.read_text(encoding="utf-8") == original

    payload = results[0]["result"]
    assert payload["ok"] is False
    assert payload["blocked"] is True
    assert payload["legacy_shadow_workspace"] is True
    assert payload["legacy_shadow_replay"] is True
    assert payload["legacy_shadow_delete_blocked"] is True
    assert payload["requested_tool_name"] == "delete_file"
    assert payload["applied_tool_name"] == "blocked_delete_file"
    assert payload["source_tools"] == ["deterministic_rust_missing_fields_repair"]
    assert payload["legacy_aggregate_remaining_source_tools"] == [
        "deterministic_rust_lib_root_facade_repair",
        "deterministic_rust_missing_fields_repair",
    ]
    assert payload["before_hash"] == sha256_text(original)
    assert payload["after_hash"] == "file_absent"
    assert payload["evidence_status"] == "failed_evidence"
    assert payload["receipt_status"] == "blocked"
    assert payload["evidence_failure_reason"] == "shadow_replay_delete_blocked"
    assert payload["repair_success_verdict"] is False

    repair_kernel = payload["repair_kernel"]
    assert repair_kernel["status"] == "blocked"
    assert repair_kernel["blocked"] is True
    assert repair_kernel["legacy_shadow_delete_blocked"] is True
    assert repair_kernel["receipt_authority"] == "non_authoritative_shadow_replay_projection"
    assert repair_kernel["runtime_authoritative_plan"] is False
    assert repair_kernel["source_tools"] == ["deterministic_rust_missing_fields_repair"]
    assert repair_kernel["legacy_aggregate_remaining_source_tools"] == [
        "deterministic_rust_lib_root_facade_repair",
        "deterministic_rust_missing_fields_repair",
    ]
    assert repair_kernel["applied_tool_name"] == "blocked_delete_file"
    assert repair_kernel["before_hashes"] == {"src/stale.rs": sha256_text(original)}
    assert repair_kernel["after_hashes"] == {"src/stale.rs": "file_absent"}
    assert repair_kernel["evidence_status"] == "failed_evidence"
    assert repair_kernel["evidence_failure_reason"] == "shadow_replay_delete_blocked"


def test_materialization_bridge_passes_verifier_to_runtime_bound_go_bare_import(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    target = tmp_path / "main.go"
    target.write_text('package main\n\n"fmt"\n\nfunc main() {}\n', encoding="utf-8")
    captured: dict[str, Any] = {}

    def sentinel_verifier(request: Any) -> DirectorRepairVerifierSnapshotInputV1:
        captured["verifier_request"] = request
        return DirectorRepairVerifierSnapshotInputV1(
            residual_artifact_quality_errors=(),
            command=("rtk", "go", "test", "./..."),
            exit_code=0,
            raw_output_ref="runtime/verifier/materialization-go.log",
            metadata=_trusted_verifier_metadata("artifact_quality"),
        )

    def fake_runtime_bridge(
        adapter: Any,
        *,
        workspace_path: Path,
        task_id: str,
        source_tool: str,
        executor_factory: Any,
        base_files: dict[str, str],
        convergence_verifier: Any = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        del adapter, executor_factory, kwargs
        captured["runtime_bridge"] = {
            "workspace_path": workspace_path,
            "task_id": task_id,
            "source_tool": source_tool,
            "base_files": dict(base_files),
            "convergence_verifier": convergence_verifier,
        }
        assert source_tool == "deterministic_go_bare_import_string_repair"
        assert convergence_verifier is sentinel_verifier
        verifier_snapshot = convergence_verifier(
            SimpleNamespace(workspace=str(workspace_path), round_number=1, receipts=())
        )
        return [
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": source_tool,
                    "file": "main.go",
                    "bridge_step_id": "materialization.go_import",
                    "bytes_written": len((workspace_path / "main.go").read_bytes()),
                    "repair_kernel": {
                        "owner_cell": "director.runtime",
                        "authoritative": True,
                        "requires_revalidation": False,
                        "convergence_status": "converged",
                        "converged": True,
                        "revalidation_evidence": {
                            "command": list(verifier_snapshot.command),
                            "exit_code": verifier_snapshot.exit_code,
                            "raw_output_ref": verifier_snapshot.raw_output_ref,
                        },
                    },
                },
            }
        ]

    for runner_name in (
        "_run_materialization_hygiene_scaffold",
        "_run_materialization_typescript_scaffold",
        "_run_materialization_typescript_compiler",
        "_run_materialization_node_manifest",
        "_run_materialization_rust_compiler",
        "_run_materialization_target_runtime",
        "_run_materialization_python_import",
    ):
        monkeypatch.setattr(materialization_quality_repair_bridge, runner_name, lambda *args, **kwargs: [])
    monkeypatch.setattr(generic_repairs, "run_runtime_repair_with_director_tools", fake_runtime_bridge)
    monkeypatch.setattr(generic_repairs, "repair_go_nested_import_keyword", lambda _: [])
    monkeypatch.setattr(generic_repairs, "repair_go_module_imports", lambda _: [])
    monkeypatch.setattr(generic_repairs, "repair_go_bare_local_imports", lambda _: [])
    monkeypatch.setattr(generic_repairs, "repair_go_import_subpaths", lambda _: [])
    monkeypatch.setattr(generic_repairs, "repair_go_duplicate_declarations", lambda _: [])
    _patch_materialization_schedule_result_as_dicts(monkeypatch)

    results, summary = materialization_quality_repair_bridge.run_materialization_quality_repairs(
        _FakeAdapter(tmp_path),
        task={"target_files": ["main.go"]},
        task_id="task-go-materialization",
        artifact_quality_errors=['Go syntax check failed: main.go:3:1: expected declaration, found "fmt"'],
        convergence_verifier=sentinel_verifier,
    )

    assert len(results) == 1
    assert captured["runtime_bridge"]["task_id"] == "task-go-materialization"
    assert captured["runtime_bridge"]["base_files"] == {"main.go": target.read_text(encoding="utf-8")}
    assert captured["runtime_bridge"]["convergence_verifier"] is sentinel_verifier
    assert captured["verifier_request"].round_number == 1
    assert summary["convergence_verifier_present"] is True
    assert summary["materialization_quality_bridge"]["convergence_verifier_present"] is True
    migration_debt = summary["repair_kernel_migration_debt"]
    assert migration_debt["convergence_verifier_present"] is True
    assert migration_debt["cutover_ready"] is False
    go_debt = {item["step_id"]: item for item in migration_debt["legacy_callback_debt"]}["materialization.go_import"]
    assert go_debt["runtime_executable_source_tools"] == ["deterministic_go_bare_import_string_repair"]
    assert go_debt["legacy_only_source_tools"] == []
    assert go_debt["convergence_path_available"] is True
    assert go_debt["convergence_verifier_present"] is True
    assert go_debt["verifier_evidence_present"] is True
    assert go_debt["cutover_ready"] is False
    assert "missing_revalidation_evidence" not in go_debt["blockers"]
    assert "legacy_callback_runner" in go_debt["blockers"]
    repair_kernel = results[0]["result"]["repair_kernel"]
    assert repair_kernel["convergence_status"] == "converged"
    assert repair_kernel["revalidation_evidence"]["command"] == ["rtk", "go", "test", "./..."]


def test_materialization_rust_migrated_bindings_run_through_runtime_bridge(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs import _runtime_bridge, rust_repairs

    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    source = tmp_path / "src" / "lib.rs"
    source.parent.mkdir(parents=True)
    source.write_text("use serde::Serialize;\npub struct Demo;\n", encoding="utf-8")

    migrated_legacy_helpers = (
        "_apply_deterministic_rust_crate_import_repair",
        "_apply_deterministic_rust_dependency_repair",
        "_apply_deterministic_rust_line_suggestion_repair",
        "_apply_deterministic_rust_unresolved_pub_use_repair",
        "_apply_deterministic_rust_trait_import_repair",
    )

    def fail_if_legacy_called(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError("materialization Rust migrated helper used legacy direct wrapper")

    for helper_name in migrated_legacy_helpers:
        monkeypatch.setattr(rust_repairs, helper_name, fail_if_legacy_called)

    retained_legacy_calls: list[str] = []

    def retained_legacy_runner(name: str) -> Any:
        def _runner(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            retained_legacy_calls.append(name)
            return []

        return _runner

    monkeypatch.setattr(
        rust_repairs,
        "_apply_deterministic_rust_missing_lib_target_repair",
        retained_legacy_runner("missing_lib_target"),
    )
    monkeypatch.setattr(
        rust_repairs,
        "_apply_deterministic_rust_lib_root_facade_repair",
        retained_legacy_runner("lib_root_facade"),
    )

    runtime_calls: list[dict[str, Any]] = []

    def fake_runtime_bridge(
        adapter: Any,
        *,
        workspace_path: Path,
        task_id: str,
        source_tool: str,
        executor_factory: Any,
        base_files: dict[str, str],
        artifact_quality_errors: list[str],
        allowed_paths: tuple[str, ...],
        use_editor: bool,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        del adapter, executor_factory, artifact_quality_errors, kwargs
        runtime_calls.append(
            {
                "workspace_path": workspace_path,
                "task_id": task_id,
                "source_tool": source_tool,
                "base_files": dict(base_files),
                "allowed_paths": tuple(allowed_paths),
                "use_editor": use_editor,
            }
        )
        return [
            {
                "tool": "edit_file",
                "tool_name": "edit_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": source_tool,
                    "file": "src/lib.rs",
                    "bridge_step_id": "materialization.rust_compiler",
                    "repair_kernel": {
                        "owner_cell": "director.runtime",
                        "revalidation_evidence": {
                            "command": ["rtk", "cargo", "check"],
                            "exit_code": 0,
                        },
                    },
                },
            }
        ]

    monkeypatch.setattr(_runtime_bridge, "run_runtime_repair_with_director_tools", fake_runtime_bridge)
    for runner_name in (
        "_run_materialization_hygiene_scaffold",
        "_run_materialization_typescript_scaffold",
        "_run_materialization_typescript_compiler",
        "_run_materialization_node_manifest",
        "_run_materialization_target_runtime",
        "_run_materialization_python_import",
        "_run_materialization_go_import",
    ):
        monkeypatch.setattr(materialization_quality_repair_bridge, runner_name, lambda *args, **kwargs: [])
    _patch_materialization_schedule_result_as_dicts(monkeypatch)

    results, summary = materialization_quality_repair_bridge.run_materialization_quality_repairs(
        _FakeAdapter(tmp_path),
        task={"target_files": ["src/lib.rs"]},
        task_id="task-rust-materialization",
        artifact_quality_errors=["error[E0432]: unresolved import `serde`"],
    )

    expected_source_tools = [
        "deterministic_rust_crate_import_rewrite_repair",
        "deterministic_rust_dependency_repair",
        "deterministic_rust_serde_derive_repair",
        "deterministic_rust_line_suggestion_repair",
        "deterministic_rust_unresolved_pub_use_repair",
        "deterministic_rust_trait_import_repair",
    ]
    assert [item["source_tool"] for item in runtime_calls] == expected_source_tools
    assert all(item["use_editor"] is True for item in runtime_calls)
    assert all(item["task_id"] == "task-rust-materialization" for item in runtime_calls)
    assert all(item["workspace_path"] == tmp_path.resolve() for item in runtime_calls)
    assert all(item["base_files"]["Cargo.toml"].startswith("[package]") for item in runtime_calls)
    assert all(item["base_files"]["src/lib.rs"] == source.read_text(encoding="utf-8") for item in runtime_calls)
    assert all(set(item["allowed_paths"]) == {"Cargo.toml", "src/lib.rs"} for item in runtime_calls)
    assert retained_legacy_calls == ["missing_lib_target", "lib_root_facade"]
    assert [item["result"]["source_tool"] for item in results] == expected_source_tools
    rust_debt = {item["step_id"]: item for item in summary["repair_kernel_migration_debt"]["legacy_callback_debt"]}[
        "materialization.rust_compiler"
    ]
    assert rust_debt["runtime_executable_source_tools"] == expected_source_tools
    assert rust_debt["legacy_only_source_tools"] == []


def test_materialization_bridge_schedule_drift_fails_closed(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    runtime_steps = _materialization_runtime_schedule_steps()
    _patch_materialization_runtime_schedule_query(monkeypatch, runtime_steps)
    drifted_runners = dict(materialization_quality_repair_bridge._MATERIALIZATION_QUALITY_REPAIR_RUNNERS)
    drifted_runners.pop("materialization.go_import")
    monkeypatch.setattr(
        materialization_quality_repair_bridge,
        "_MATERIALIZATION_QUALITY_REPAIR_RUNNERS",
        drifted_runners,
    )

    with pytest.raises(RuntimeError, match="runner bindings drift from runtime schedule") as exc_info:
        materialization_quality_repair_bridge.run_materialization_quality_repairs(
            _FakeAdapter(tmp_path),
            task={"target_files": ["main.go"]},
            task_id="task-materialization-drift",
            artifact_quality_errors=["Go syntax check failed: main.go:3:1: expected declaration"],
        )

    message = str(exc_info.value)
    assert "materialization.go_import" in message
    assert "missing_runner_step_ids" in message


def test_materialization_bridge_projects_runtime_step_metadata_and_missing_evidence(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    runtime_steps = _materialization_runtime_schedule_steps()
    _patch_materialization_runtime_schedule_query(monkeypatch, runtime_steps)
    go_step = {step.step_id: step for step in runtime_steps}["materialization.go_import"]
    tool_result = {
        "tool": "write_file",
        "tool_name": "write_file",
        "success": True,
        "result": {
            "ok": True,
            "source_tool": "deterministic_go_bare_import_string_repair",
            "file": "main.go",
            "bytes_written": 42,
            "operation": "write_file",
            "bridge_step_id": "materialization.go_import",
        },
    }

    def fake_schedule_result(
        *,
        runner_step_ids: tuple[str, ...],
        runner: Any,
        max_rounds: int = 1,
    ) -> SimpleNamespace:
        del runner
        assert runner_step_ids == tuple(step.step_id for step in runtime_steps)
        return SimpleNamespace(
            ordered_steps=runtime_steps,
            tool_results=(tool_result,),
            receipt_projections=(),
            summary={
                "schedule_kind": "materialization_quality",
                "max_rounds": max_rounds,
                "rounds_run": 1,
                "receipt_projection_count": 0,
            },
        )

    monkeypatch.setattr(
        materialization_quality_repair_bridge,
        "run_director_materialization_quality_repair_schedule_result",
        fake_schedule_result,
    )

    results, summary = materialization_quality_repair_bridge.run_materialization_quality_repairs(
        _FakeAdapter(tmp_path),
        task={"target_files": ["main.go"]},
        task_id="task-materialization-metadata",
        artifact_quality_errors=["Go syntax check failed: main.go:3:1: expected declaration"],
    )

    payload = results[0]["result"]
    assert results[0]["runtime_step_id"] == "materialization.go_import"
    assert results[0]["evidence_status"] == "missing_evidence"
    assert payload["runtime_step_id"] == "materialization.go_import"
    assert payload["phase"] == go_step.phase
    assert payload["priority"] == go_step.priority
    assert payload["depends_on"] == list(go_step.depends_on)
    assert payload["evidence_status"] == "missing_evidence"

    step_summary = summary["materialization_quality_step_summaries"]["materialization.go_import"]
    assert step_summary["runtime_step_id"] == "materialization.go_import"
    assert step_summary["phase"] == go_step.phase
    assert step_summary["priority"] == go_step.priority
    assert step_summary["depends_on"] == list(go_step.depends_on)
    assert step_summary["evidence_status"] == "missing_evidence"
    assert step_summary["evidence_status_counts"] == {"missing_evidence": 1}
    assert step_summary["typed_receipt_path_available"] is False
    assert step_summary["authoritative_receipts_allowed"] is False
    assert step_summary["native_repair_kernel_receipt_count"] == 0
    assert step_summary["callback_receipt_projection_count"] == 0
    assert step_summary["receipt_lifecycle_evidence_status_counts"] == {"missing_evidence": 1}
    assert step_summary["receipt_lifecycle_evidence_status"] == "missing_evidence"
    assert "missing_native_repair_receipt" in step_summary["cutover_blockers"]
    assert "missing_revalidation_evidence" in step_summary["cutover_blockers"]

    scheduler_bridge = summary["scheduler_bridge"]
    assert scheduler_bridge["runner_binding_reconciliation"]["exact_match"] is True
    assert scheduler_bridge["step_evidence_statuses"]["materialization.go_import"] == "missing_evidence"
    assert "materialization.go_import" in scheduler_bridge["missing_evidence_step_ids"]
    assert scheduler_bridge["evidence_status_counts"]["missing_evidence"] >= 1
    assert "resolved_evidence" not in scheduler_bridge["evidence_status_counts"]
    go_lifecycle = scheduler_bridge["receipt_lifecycle_by_step"]["materialization.go_import"]
    assert go_lifecycle["typed_receipt_path_available"] is False
    assert go_lifecycle["authoritative_receipts_allowed"] is False
    assert go_lifecycle["native_repair_kernel_receipt_count"] == 0
    assert go_lifecycle["callback_receipt_projection_count"] == 0
    assert go_lifecycle["receipt_lifecycle_evidence_status"] == "missing_evidence"


def test_materialization_public_legacy_facade_only_forwards_bridge(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_bridge(adapter: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        calls.append({"adapter": adapter, **kwargs})
        return (
            [{"tool": "write_file", "success": True, "result": {"ok": True}}],
            {"scheduler_bridge": {"schema_version": "director.materialization_quality_scheduler_bridge.v1"}},
        )

    monkeypatch.setattr(
        materialization_quality_repair_bridge,
        "run_materialization_quality_repairs",
        fake_bridge,
    )

    adapter = _FakeAdapter(tmp_path)
    results, summary = roles_adapters_public_service.apply_deterministic_materialization_quality_repairs(
        adapter,
        task={"target_files": ["main.go"]},
        task_id="task-materialization-facade",
        artifact_quality_errors=["Go syntax check failed"],
    )

    assert len(calls) == 1
    assert calls[0]["adapter"] is adapter
    assert calls[0]["task_id"] == "task-materialization-facade"
    assert results[0]["tool"] == "write_file"
    public_boundary = summary["public_boundary"]
    assert public_boundary["mode"] == "runtime_owned_schedule_public_boundary"
    assert public_boundary["migration_only_compatibility_shim"] == (
        "apply_deterministic_materialization_quality_repairs"
    )
    assert public_boundary["preferred_entrypoint"] == "run_director_materialization_quality_repair_schedule"


def test_runtime_bridge_imports_only_public_director_runtime_surface() -> None:
    bridge_path = (
        Path(__file__).parent.parent / "internal" / "director" / "deterministic_repairs" / "_runtime_bridge.py"
    )
    source = bridge_path.read_text(encoding="utf-8")

    assert "polaris.cells.director.runtime.public import" in source
    assert "polaris.cells.director.runtime.public.service" not in source
    assert "polaris.cells.director.runtime.internal" not in source


def test_post_execution_canonical_projection_preserves_receipt_evidence_and_hashes(tmp_path: Path) -> None:
    relative_path = "src/engine/generator.cpp"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("#include <cstdint>\n", encoding="utf-8")
    receipt = RepairReceiptV1(
        receipt_id="receipt-post-1",
        plan_id="plan-post-1",
        source_tool="deterministic_cpp_standard_include_repair",
        status="applied",
        authoritative=True,
        files_changed=(relative_path,),
        before_hashes={relative_path: "before-hash"},
        after_hashes={relative_path: "after-hash"},
        round_number=2,
        errors_before=3,
        errors_after=1,
        net_error_reduction=2,
        authority_hash="authority-hash",
        projection_hash="projection-hash",
        revalidation_evidence={
            "command": ["rtk", "cmake", "--build", "build"],
            "exit_code": 0,
            "raw_output_ref": "runtime/verifier/post-execution.log",
            "errors_before": 3,
            "errors_after": 1,
            "net_error_reduction": 2,
        },
        metadata={"requires_revalidation": False},
    )
    canonical_result = DirectorRepairResultV1(
        ok=True,
        receipts=(receipt,),
        metadata={
            "planning": {"planned": True},
            "plan_policy": {"allowed": True},
            "composition_policy": {"allowed": True},
        },
    )

    results = post_execution_repair_bridge._canonical_repair_result_to_tool_results(
        canonical_result,
        write_results={
            relative_path: {
                "ok": True,
                "bytes_written": len(target.read_bytes()),
                "operation": "write_file",
                "broadcast_ok": True,
                "director_policy": {"allowed": True},
            }
        },
        workspace=tmp_path,
    )

    assert len(results) == 1
    repair_kernel = results[0]["result"]["repair_kernel"]
    assert repair_kernel["authoritative"] is True
    assert repair_kernel["requires_revalidation"] is False
    assert repair_kernel["authority_hash"] == "authority-hash"
    assert repair_kernel["projection_hash"] == "projection-hash"
    assert repair_kernel["round_number"] == 2
    assert repair_kernel["errors_before"] == 3
    assert repair_kernel["errors_after"] == 1
    assert repair_kernel["net_error_reduction"] == 2
    assert repair_kernel["revalidation_evidence"]["command"] == ["rtk", "cmake", "--build", "build"]
    assert repair_kernel["revalidation_evidence"]["exit_code"] == 0


def test_post_execution_migration_debt_ledger_distinguishes_runtime_and_legacy(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    class FakeAdapter:
        def __init__(self) -> None:
            self.workspace = str(tmp_path)

    def fake_cpp_repairs(
        workspace: str | Path,
        *,
        adapter: Any | None = None,
        task_id: str = "director-cpp-post-repair",
        advisor_notes: Any = (),
        convergence_verifier: Any = None,
    ) -> list[dict[str, Any]]:
        del workspace, adapter, task_id, advisor_notes, convergence_verifier
        return [
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_cpp_include_path_repair",
                    "file": "src/engine/generator.cpp",
                    "bytes_written": 42,
                    "operation": "write_file",
                    "repair_kernel": {
                        "owner_cell": "director.runtime",
                        "authoritative": False,
                        "requires_revalidation": True,
                        "revalidation_evidence": {},
                    },
                },
            }
        ]

    def fake_go_repairs(
        adapter: Any,
        *,
        task_id: str,
        advisor_notes: Any = (),
        convergence_verifier: Any = None,
    ) -> list[dict[str, Any]]:
        del adapter, task_id, advisor_notes, convergence_verifier
        return []

    def fake_java_repairs(
        adapter: Any,
        workspace: Path,
        *,
        task_id: str,
        advisor_notes: Any = (),
        convergence_verifier: Any = None,
    ) -> list[dict[str, Any]]:
        del adapter, workspace, task_id, advisor_notes, convergence_verifier
        return [
            post_execution_repair_bridge._record_to_tool_result(
                {"file": "src/test/java/AppTest.java", "action": "java_test_dependency"},
                source_tool="deterministic_java_post_repair",
                default_action="java_post_repair",
            )
        ]

    monkeypatch.setattr(post_execution_repair_bridge, "run_cpp_post_repairs_as_tool_results", fake_cpp_repairs)
    monkeypatch.setattr(post_execution_repair_bridge, "_run_go_post_repairs", fake_go_repairs)
    monkeypatch.setattr(post_execution_repair_bridge, "_run_java_post_repairs", fake_java_repairs)
    _patch_post_execution_schedule_result_as_dicts(monkeypatch)

    tool_results, summary = post_execution_repair_bridge.run_post_execution_language_repairs(
        FakeAdapter(),
        task_id="task-post-debt",
    )

    assert summary is not None
    assert len(tool_results) == 2
    migration_debt = summary["repair_kernel"]["repair_kernel_migration_debt"]
    assert migration_debt["legacy_aggregate_remaining_source_tools"] == []
    assert summary["legacy_callback_debt"]["legacy_aggregate_remaining_source_tools"] == []
    assert migration_debt["remaining_legacy_subcases"] == []
    assert migration_debt["runtime_migrated_subcases"] == [
        "deterministic_rust_lib_root_facade_repair:export_or_module_declaration",
        "deterministic_rust_lib_root_facade_repair:path_rewrite",
        "deterministic_rust_missing_fields_repair:field_declaration",
    ]
    assert migration_debt["legacy_aggregate_blocked_source_tools"] == []
    assert migration_debt["legacy_aggregate_blocked_migrated_source_tools"] == []
    assert migration_debt["legacy_aggregate_remaining_source_tool_count"] == 0
    assert migration_debt["legacy_aggregate_remaining_legacy_subcase_count"] == 0
    assert migration_debt["legacy_aggregate_runtime_migrated_subcase_count"] == 3
    assert migration_debt["legacy_aggregate_blocked_migrated_source_tool_count"] == 0
    assert migration_debt["legacy_aggregate_shadow_replay_authoritative"] is False
    assert migration_debt["legacy_aggregate_cutover_ready"] is False
    assert not any(
        str(blocker).startswith(("remaining_legacy_subcase:", "remaining_source_tool:"))
        for blocker in migration_debt["legacy_aggregate_cutover_blockers"]
    )
    steps = {step["step_id"]: step for step in migration_debt["steps"]}
    cpp_step = steps["cpp.post_execution"]
    assert cpp_step["runtime_executable_source_tools"] == ["deterministic_cpp_include_path_repair"]
    assert cpp_step["legacy_only_source_tools"] == []
    assert cpp_step["write_tool_evidence"] is True
    assert cpp_step["verifier_evidence_required"] is True
    assert cpp_step["verifier_evidence_present"] is False
    assert "missing_verifier_evidence" in cpp_step["blockers"]
    assert "convergence_verifier_not_provided" in cpp_step["blockers"]

    java_step = steps["java.post_execution"]
    assert java_step["runtime_executable_source_tools"] == ["deterministic_java_post_repair"]
    assert java_step["legacy_only_source_tools"] == []
    assert "legacy_callback_record_projection" in java_step["blockers"]
    legacy_payload = next(
        item["result"] for item in tool_results if item["result"]["source_tool"] == "deterministic_java_post_repair"
    )
    assert legacy_payload["repair_kernel"]["owner_cell"] == "roles.adapters.legacy_strategy_host"
    assert legacy_payload["repair_kernel"]["authoritative"] is False
    assert legacy_payload["repair_kernel"]["requires_revalidation"] is True
    assert summary["legacy_callback_debt"]["legacy_only_step_count"] == 0


def test_go_post_execution_uses_runtime_source_tool_sequence_without_legacy_aggregate(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    (tmp_path / "go.mod").write_text("module example.com/demo\n", encoding="utf-8")
    source = tmp_path / "cmd" / "app" / "main.go"
    source.parent.mkdir(parents=True)
    source.write_text('package main\n"fmt"\nfunc main() {}\n', encoding="utf-8")

    class FakeAdapter:
        workspace = str(tmp_path)

    def sentinel_verifier(request: Any) -> Any:
        return {"request": request}

    fake_adapter = FakeAdapter()
    called_source_tools: list[str] = []
    base_file_snapshots: list[dict[str, str]] = []

    def fail_if_legacy_go_aggregate_called(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError("post-execution Go repair must not call legacy aggregate helper")

    def fake_runtime_bridge(
        adapter_arg: Any,
        *,
        workspace_path: Path,
        task_id: str,
        source_tool: str,
        base_files: dict[str, str],
        allowed_paths: tuple[str, ...],
        use_editor: bool,
        convergence_verifier: Any = None,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        assert adapter_arg is fake_adapter
        assert workspace_path == tmp_path.resolve()
        assert task_id == "task-go-post"
        assert "go.mod" in base_files
        assert "cmd/app/main.go" in base_files
        assert "go.mod" in allowed_paths
        assert use_editor is True
        assert convergence_verifier is sentinel_verifier
        called_source_tools.append(source_tool)
        base_file_snapshots.append(dict(base_files))
        if source_tool == "deterministic_go_bare_import_string_repair":
            source.write_text('package main\nimport "fmt"\nfunc main() {}\n', encoding="utf-8")
        return []

    monkeypatch.setattr(
        generic_repairs,
        "_apply_deterministic_go_module_import_repair",
        fail_if_legacy_go_aggregate_called,
    )
    monkeypatch.setattr(
        post_execution_repair_bridge,
        "run_runtime_repair_with_director_tools",
        fake_runtime_bridge,
    )

    results = post_execution_repair_bridge._run_go_post_repairs(
        fake_adapter,
        task_id="task-go-post",
        convergence_verifier=sentinel_verifier,
    )

    assert results == []
    assert called_source_tools == list(post_execution_repair_bridge._GO_POST_EXECUTION_RUNTIME_SOURCE_TOOLS)
    assert 'import "fmt"' in base_file_snapshots[1]["cmd/app/main.go"]


def test_post_execution_allowed_rust_shadow_replay_stays_non_cutover_ready(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    remaining_source_tools = [
        "deterministic_rust_missing_fields_repair",
    ]
    remaining_legacy_subcases = [
        "deterministic_rust_missing_fields_repair:field_declaration",
    ]
    runtime_migrated_subcases = [
        "deterministic_rust_lib_root_facade_repair:export_or_module_declaration",
        "deterministic_rust_lib_root_facade_repair:path_rewrite",
    ]

    class FakeAdapter:
        def __init__(self) -> None:
            self.workspace = str(tmp_path)

    def fake_rust_post_repairs(adapter: Any, workspace: Path, *, task_id: str) -> list[dict[str, Any]]:
        del adapter, workspace, task_id
        return [
            post_execution_repair_bridge._rust_record_to_tool_result(
                {
                    "file": "src/lib.rs",
                    "source_tool": "deterministic_rust_missing_fields_repair",
                    "action": "rust_missing_fields",
                    "revalidation": {
                        "command": ["rtk", "cargo", "check"],
                        "exit_code": 0,
                    },
                },
                write_result={
                    "ok": True,
                    "file": "src/lib.rs",
                    "operation": "edit_file",
                    "replacements": 1,
                },
                shadow_metadata={
                    "applied_tool_name": "edit_file",
                    "before_content": "pub fn demo() -> i32 { 1 }\n",
                    "after_content": "pub fn demo() -> i32 { 2 }\n",
                    "record_count": 1,
                    "legacy_aggregate_remaining_source_tools": remaining_source_tools,
                    "legacy_aggregate_shadow_replay_allowed_source_tools": remaining_source_tools,
                    "remaining_legacy_subcases": remaining_legacy_subcases,
                    "runtime_migrated_subcases": runtime_migrated_subcases,
                    "source_tools": ["deterministic_rust_missing_fields_repair"],
                },
            )
        ]

    def no_repairs(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(post_execution_repair_bridge, "_run_go_post_repairs", no_repairs)
    monkeypatch.setattr(post_execution_repair_bridge, "_run_rust_dependency_repair", no_repairs)
    monkeypatch.setattr(post_execution_repair_bridge, "_run_rust_post_repairs", fake_rust_post_repairs)
    monkeypatch.setattr(post_execution_repair_bridge, "run_cpp_post_repairs_as_tool_results", no_repairs)
    monkeypatch.setattr(post_execution_repair_bridge, "_run_java_post_repairs", no_repairs)
    monkeypatch.setattr(
        post_execution_repair_bridge,
        "_runtime_executable_source_tools",
        lambda: frozenset(
            {
                "deterministic_rust_dependency_repair",
                "deterministic_rust_lib_root_facade_repair",
            }
        ),
    )
    _patch_post_execution_schedule_result_as_dicts(monkeypatch)

    tool_results, summary = post_execution_repair_bridge.run_post_execution_language_repairs(
        FakeAdapter(),
        task_id="task-rust-shadow-allowed-summary",
    )

    assert summary is not None
    assert len(tool_results) == 1
    evidence = summary["legacy_aggregate_cutover_readiness_evidence"]
    assert evidence["shadow_replay_authoritative"] is False
    assert evidence["cutover_ready"] is False
    assert evidence["remaining_source_tool_count"] == 1
    assert evidence["remaining_source_tool_counts"] == {
        "deterministic_rust_missing_fields_repair": 1,
    }
    assert evidence["remaining_legacy_subcases"] == remaining_legacy_subcases
    assert evidence["runtime_migrated_subcases"] == runtime_migrated_subcases
    assert evidence["remaining_legacy_subcase_count"] == 1
    assert evidence["runtime_migrated_subcase_count"] == 2
    assert evidence["blocked_migrated_source_tool_count"] == 0
    assert evidence["blocked_migrated_source_tool_counts"] == {}
    assert "remaining_source_tool:deterministic_rust_missing_fields_repair" in evidence["cutover_blockers"]
    assert (
        "remaining_legacy_subcase:deterministic_rust_missing_fields_repair:field_declaration"
        in evidence["cutover_blockers"]
    )

    repair_kernel = summary["repair_kernel"]
    assert repair_kernel["legacy_aggregate_shadow_replay_authoritative"] is False
    assert repair_kernel["legacy_aggregate_cutover_ready"] is False
    assert repair_kernel["legacy_aggregate_remaining_source_tool_count"] == 1
    assert repair_kernel["legacy_aggregate_remaining_legacy_subcase_count"] == 1
    assert repair_kernel["legacy_aggregate_runtime_migrated_subcase_count"] == 2
    assert repair_kernel["legacy_aggregate_blocked_migrated_source_tool_count"] == 0
    assert repair_kernel["legacy_aggregate_cutover_blockers"] == evidence["cutover_blockers"]

    migration_debt = summary["repair_kernel_migration_debt"]
    assert migration_debt["legacy_aggregate_cutover_ready"] is False
    assert migration_debt["legacy_aggregate_remaining_source_tool_count"] == 1
    assert migration_debt["legacy_aggregate_remaining_legacy_subcase_count"] == 1
    assert migration_debt["legacy_aggregate_runtime_migrated_subcase_count"] == 2
    assert migration_debt["legacy_aggregate_blocked_migrated_source_tool_count"] == 0
    assert migration_debt["legacy_callback_debt"]["legacy_aggregate_cutover_ready"] is False
    rust_step = {item["step_id"]: item for item in migration_debt["steps"]}["rust.post_execution_convergence"]
    assert rust_step["legacy_aggregate_cutover_ready"] is False
    assert rust_step["legacy_aggregate_remaining_source_tool_count"] == 1
    assert rust_step["legacy_aggregate_remaining_legacy_subcase_count"] == 1
    assert rust_step["legacy_aggregate_runtime_migrated_subcase_count"] == 2
    assert rust_step["legacy_aggregate_blocked_migrated_source_tool_count"] == 0

    scheduler_bridge = summary["scheduler_bridge"]
    assert scheduler_bridge["legacy_aggregate_shadow_replay_authoritative"] is False
    assert scheduler_bridge["legacy_aggregate_cutover_ready"] is False
    assert scheduler_bridge["legacy_aggregate_cutover_blockers"] == evidence["cutover_blockers"]


def test_post_execution_migration_debt_marks_runtime_verifier_evidence_without_legacy_cutover(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    class FakeAdapter:
        def __init__(self) -> None:
            self.workspace = str(tmp_path)

    def fake_cpp_repairs(
        workspace: str | Path,
        *,
        adapter: Any | None = None,
        task_id: str = "director-cpp-post-repair",
        advisor_notes: Any = (),
        convergence_verifier: Any = None,
    ) -> list[dict[str, Any]]:
        del workspace, adapter, task_id, advisor_notes
        assert callable(convergence_verifier)
        return [
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_cpp_include_path_repair",
                    "file": "src/engine/generator.cpp",
                    "bytes_written": 42,
                    "operation": "write_file",
                    "repair_kernel": {
                        "owner_cell": "director.runtime",
                        "authoritative": True,
                        "requires_revalidation": False,
                        "revalidation_evidence": {
                            "command": ["rtk", "artifact-quality"],
                            "exit_code": 0,
                            "raw_output_ref": str(tmp_path / "verifier.json"),
                        },
                    },
                },
            }
        ]

    def fake_go_repairs(
        adapter: Any,
        *,
        task_id: str,
        advisor_notes: Any = (),
        convergence_verifier: Any = None,
    ) -> list[dict[str, Any]]:
        del adapter, task_id, advisor_notes, convergence_verifier
        return []

    def fake_java_repairs(
        adapter: Any,
        workspace: Path,
        *,
        task_id: str,
        advisor_notes: Any = (),
        convergence_verifier: Any = None,
    ) -> list[dict[str, Any]]:
        del adapter, workspace, task_id, advisor_notes, convergence_verifier
        return [
            post_execution_repair_bridge._record_to_tool_result(
                {"file": "src/test/java/AppTest.java", "action": "java_test_dependency"},
                source_tool="deterministic_java_post_repair",
                default_action="java_post_repair",
            )
        ]

    monkeypatch.setattr(post_execution_repair_bridge, "run_cpp_post_repairs_as_tool_results", fake_cpp_repairs)
    monkeypatch.setattr(post_execution_repair_bridge, "_run_go_post_repairs", fake_go_repairs)
    monkeypatch.setattr(post_execution_repair_bridge, "_run_java_post_repairs", fake_java_repairs)
    _patch_post_execution_schedule_result_as_dicts(monkeypatch)

    def convergence_verifier(request: Any) -> Any:
        return request

    tool_results, summary = post_execution_repair_bridge.run_post_execution_language_repairs(
        FakeAdapter(),
        task_id="task-post-verifier",
        convergence_verifier=convergence_verifier,
    )

    assert summary is not None
    assert len(tool_results) == 2
    migration_debt = summary["repair_kernel"]["repair_kernel_migration_debt"]
    steps = {step["step_id"]: step for step in migration_debt["steps"]}
    cpp_step = steps["cpp.post_execution"]
    assert cpp_step["runtime_executable_source_tools"] == ["deterministic_cpp_include_path_repair"]
    assert cpp_step["convergence_path_available"] is True
    assert cpp_step["convergence_verifier_present"] is True
    assert cpp_step["verifier_evidence_present"] is True
    assert "missing_verifier_evidence" not in cpp_step["blockers"]
    assert "convergence_verifier_not_provided" not in cpp_step["blockers"]

    java_step = steps["java.post_execution"]
    assert java_step["runtime_executable_source_tools"] == ["deterministic_java_post_repair"]
    assert java_step["legacy_only_source_tools"] == []
    assert java_step["verifier_evidence_present"] is False
    assert java_step["cutover_ready"] is False
    assert "legacy_only_source_tools_present" not in java_step["blockers"]
    assert "legacy_callback_record_projection" in java_step["blockers"]


def test_java_post_execution_junit_dependency_runs_runtime_binding(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    relative_path = "src/test/java/AppTest.java"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text(
        "import org.junit.jupiter.api.Test;\n"
        "import static org.junit.jupiter.api.Assertions.assertEquals;\n\n"
        "public class AppTest {\n"
        "    @Test\n"
        "    public void addsNumbers() {\n"
        "        assertEquals(4, 2 + 2);\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", _FakeDirectorToolExecutor)

    results = post_execution_repair_bridge._run_java_post_repairs(
        _FakeAdapter(tmp_path),
        tmp_path,
        task_id="task-java-junit-runtime",
    )

    payloads = [item["result"] for item in results]
    source_tools = [payload["source_tool"] for payload in payloads]
    assert source_tools == ["deterministic_java_test_dependency_repair"]
    assert "deterministic_java_post_repair" not in source_tools
    updated = target.read_text(encoding="utf-8")
    assert "org.junit" not in updated
    assert "assertEquals" not in updated
    assert "public static void main" in updated
    repair_kernel = payloads[0]["repair_kernel"]
    assert repair_kernel["owner_cell"] == "director.runtime"


def test_post_execution_runtime_bound_repair_passes_convergence_verifier(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    header = tmp_path / "src" / "models" / "postcard.hpp"
    target = tmp_path / "src" / "engine" / "generator.cpp"
    header.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    header.write_text("#pragma once\n", encoding="utf-8")
    target.write_text('#include "src/models/postcard.hpp"\n#include <string>\n', encoding="utf-8")

    class FakeAdapter:
        def __init__(self) -> None:
            self.workspace = str(tmp_path)
            self._execution = SimpleNamespace(_message_bus=None)
            self.progress: list[tuple[str, str, str | None]] = []

        def _update_task_progress(self, task_id: str, state: str, *, current_file: str | None = None) -> None:
            self.progress.append((task_id, state, current_file))

    monkeypatch.setattr(post_execution_repair_bridge, "DirectorToolExecutor", _FakeDirectorToolExecutor)
    requests: list[DirectorRepairConvergenceVerifierRequestV1] = []

    def convergence_verifier(
        request: DirectorRepairConvergenceVerifierRequestV1,
    ) -> DirectorRepairVerifierSnapshotInputV1:
        requests.append(request)
        current = target.read_text(encoding="utf-8")
        residual_errors = (
            ()
            if '#include "../models/postcard.hpp"' in current
            else ("src/engine/generator.cpp:1:10: fatal error: 'src/models/postcard.hpp' file not found",)
        )
        return DirectorRepairVerifierSnapshotInputV1(
            residual_artifact_quality_errors=residual_errors,
            command=("rtk", "cmake", "--build", "build"),
            exit_code=0 if not residual_errors else 1,
            raw_output_ref=f"runtime/verifier/cpp-post-round-{request.round_number}.log",
            metadata=_trusted_verifier_metadata("cpp"),
        )

    results = post_execution_repair_bridge._run_cpp_include_path_runtime_repair(
        FakeAdapter(),
        tmp_path,
        task_id="task-cpp-convergence",
        convergence_verifier=convergence_verifier,
    )

    assert len(results) == 1
    assert [request.round_number for request in requests] == [0, 1]
    assert requests[0].receipts == ()
    assert len(requests[1].receipts) == 1
    assert '#include "../models/postcard.hpp"' in target.read_text(encoding="utf-8")
    repair_kernel = results[0]["result"]["repair_kernel"]
    assert repair_kernel["owner_cell"] == "director.runtime"
    assert repair_kernel["authoritative"] is True
    assert repair_kernel["requires_revalidation"] is False
    assert repair_kernel["convergence_status"] == "converged"
    assert repair_kernel["convergence_round_count"] == 1
    assert repair_kernel["revalidation_evidence"]["command"] == ["rtk", "cmake", "--build", "build"]
    assert repair_kernel["revalidation_evidence"]["exit_code"] == 0


def test_post_execution_scheduler_bridge_counts_callback_receipt_projections_without_authority(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    class FakeAdapter:
        def __init__(self) -> None:
            self.workspace = str(tmp_path)

    def fake_cpp_repairs(
        workspace: str | Path,
        *,
        adapter: Any | None = None,
        task_id: str = "director-cpp-post-repair",
        advisor_notes: Any = (),
        convergence_verifier: Any = None,
    ) -> list[dict[str, Any]]:
        del workspace, adapter, task_id, advisor_notes, convergence_verifier
        return [
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_cpp_include_path_repair",
                    "file": "src/engine/generator.cpp",
                    "bytes_written": 42,
                    "operation": "write_file",
                    "callback_receipt_projection": {
                        "receipt_id": "callback-receipt-singular",
                        "receipt_authority": "non_authoritative_callback_receipt_projection",
                        "authoritative": False,
                        "typed_receipt_path_available": True,
                        "revalidation": {
                            "command": ["rtk", "cmake", "--build", "build"],
                            "exit_code": 0,
                        },
                    },
                },
            },
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_cpp_standard_include_repair",
                    "file": "src/engine/generator.cpp",
                    "bytes_written": 42,
                    "operation": "write_file",
                    "callback_receipt_projections": [
                        {
                            "receipt_id": "callback-receipt-plural",
                            "receipt_authority": "non_authoritative_callback_receipt_projection",
                            "authoritative": False,
                            "typed_receipt_path_available": False,
                            "revalidation_evidence": {
                                "command": ["rtk", "cmake", "--build", "build"],
                                "exit_code": 0,
                            },
                        }
                    ],
                },
            },
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_cpp_placeholder_declaration_repair",
                    "file": "src/engine/generator.cpp",
                    "bytes_written": 42,
                    "operation": "write_file",
                    "legacy_callback_bridge": True,
                    "produces_tool_results_only": True,
                    "typed_receipt_path_available": False,
                    "revalidation": {
                        "command": ["rtk", "cmake", "--build", "build"],
                        "exit_code": 0,
                    },
                },
            },
        ]

    def fake_go_repairs(
        adapter: Any,
        *,
        task_id: str,
        advisor_notes: Any = (),
        convergence_verifier: Any = None,
    ) -> list[dict[str, Any]]:
        del adapter, task_id, advisor_notes, convergence_verifier
        return []

    def fake_java_repairs(
        adapter: Any,
        workspace: Path,
        *,
        task_id: str,
        advisor_notes: Any = (),
        convergence_verifier: Any = None,
    ) -> list[dict[str, Any]]:
        del adapter, workspace, task_id, advisor_notes, convergence_verifier
        return []

    monkeypatch.setattr(post_execution_repair_bridge, "run_cpp_post_repairs_as_tool_results", fake_cpp_repairs)
    monkeypatch.setattr(post_execution_repair_bridge, "_run_go_post_repairs", fake_go_repairs)
    monkeypatch.setattr(post_execution_repair_bridge, "_run_java_post_repairs", fake_java_repairs)
    _patch_post_execution_schedule_result_as_dicts(monkeypatch)

    _, summary = post_execution_repair_bridge.run_post_execution_language_repairs(
        FakeAdapter(),
        task_id="task-callback-receipt-projection",
    )

    assert summary is not None
    scheduler_bridge = summary["scheduler_bridge"]
    assert scheduler_bridge["callback_receipt_projection_count"] == 3
    assert scheduler_bridge["callback_receipts_authoritative"] is False
    assert scheduler_bridge["callback_receipt_authority_values"] == ["non_authoritative_callback_projection"]
    assert scheduler_bridge["callback_receipts_with_revalidation"] == 1
    assert scheduler_bridge["typed_receipt_path_available"] is False
    assert scheduler_bridge["callback_projection_claimed_typed_receipt_path_count"] == 0
    assert (
        scheduler_bridge["migration_blocker"] == "callback runners still return tool_results instead of RepairReceipt"
    )
    repair_kernel_receipts = [
        receipt for receipt in summary["repair_kernel"].get("receipts", []) if isinstance(receipt, dict)
    ]
    assert all(
        receipt.get("receipt_id") not in {"callback-receipt-singular", "callback-receipt-plural"}
        for receipt in repair_kernel_receipts
    )
    assert all("callback_receipt_projection" not in receipt for receipt in repair_kernel_receipts)
    assert all("callback_receipt_projections" not in receipt for receipt in repair_kernel_receipts)


def test_post_execution_scheduler_bridge_prefers_public_result_receipt_projections(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    class FakeAdapter:
        def __init__(self) -> None:
            self.workspace = str(tmp_path)

    step = post_execution_repair_bridge.DirectorRepairPostExecutionStepV1(
        step_id="cpp.post_execution",
        language="cpp",
        phase="post_materialization",
        priority=1,
        source_tool="deterministic_cpp_include_path_repair",
    )
    tool_result = {
        "tool": "write_file",
        "tool_name": "write_file",
        "success": True,
        "result": {
            "ok": True,
            "source_tool": "deterministic_cpp_include_path_repair",
            "file": "src/engine/generator.cpp",
            "bytes_written": 42,
            "operation": "write_file",
            "bridge_step_id": "cpp.post_execution",
            "round_number": 1,
            "callback_receipt_projections": [
                {
                    "receipt_id": "malicious-payload-projection",
                    "receipt_authority": "payload_should_not_win",
                    "authoritative": True,
                    "typed_receipt_path_available": False,
                },
                {
                    "receipt_id": "conflicting-payload-projection",
                    "receipt_authority": "conflicting_payload_should_not_win",
                    "authoritative": True,
                    "typed_receipt_path_available": True,
                },
            ],
        },
    }
    public_projection = {
        "projection_id": "post-public-projection",
        "receipt_id": "post-public-receipt",
        "receipt_authority": "authoritative",
        "schedule_kind": "post_execution",
        "step_id": "cpp.post_execution",
        "source_tool": "deterministic_cpp_include_path_repair",
        "round_number": 2,
        "max_rounds": 3,
        "projection_only": False,
        "authoritative": True,
        "typed_receipt_path_available": True,
        "revalidation_evidence_present": True,
    }

    def fake_schedule_result(
        *,
        runner_step_ids: tuple[str, ...],
        runner: Any,
        max_rounds: int = 1,
    ) -> SimpleNamespace:
        del runner
        assert "cpp.post_execution" in runner_step_ids
        assert max_rounds == 3
        return SimpleNamespace(
            ordered_steps=(step,),
            tool_results=(tool_result,),
            receipt_projections=(public_projection,),
            summary={
                "schedule_kind": "post_execution",
                "max_rounds": 3,
                "rounds_run": 2,
                "receipt_projection_count": 1,
            },
            max_rounds=3,
            rounds_run=2,
            convergence_status="cycle_broken",
            stopped_reason="test_public_projection_precedence",
        )

    monkeypatch.setattr(
        post_execution_repair_bridge,
        "run_director_post_execution_repair_schedule_result",
        fake_schedule_result,
    )

    _, summary = post_execution_repair_bridge.run_post_execution_language_repairs(
        FakeAdapter(),
        task_id="task-public-post-projection",
    )

    assert summary is not None
    scheduler_bridge = summary["scheduler_bridge"]
    assert scheduler_bridge["callback_receipt_projection_count"] == 1
    assert scheduler_bridge["callback_receipts_authoritative"] is False
    assert scheduler_bridge["callback_receipts_with_revalidation"] == 1
    assert scheduler_bridge["typed_receipt_path_available"] is False
    assert scheduler_bridge["callback_projection_claimed_typed_receipt_path_count"] == 1
    assert scheduler_bridge["observed_max_round"] == 2
    assert scheduler_bridge["configured_max_rounds"] == 3
    assert scheduler_bridge.get("projection_only", True) is True
    repair_kernel_receipts = [
        receipt for receipt in summary["repair_kernel"].get("receipts", []) if isinstance(receipt, dict)
    ]
    assert {receipt.get("receipt_id") for receipt in repair_kernel_receipts}.isdisjoint(
        {
            "post-public-receipt",
            "malicious-payload-projection",
            "conflicting-payload-projection",
        }
    )


def test_materialization_scheduler_bridge_keeps_callback_projection_non_authoritative(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    class FakeAdapter:
        def __init__(self) -> None:
            self.workspace = str(tmp_path)

    step = materialization_quality_repair_bridge.DirectorRepairMaterializationQualityStepV1(
        step_id="materialization.go_import",
        language="go",
        phase="materialization_quality",
        priority=7,
        source_tool="deterministic_go_bare_import_string_repair",
    )
    tool_result = {
        "tool": "write_file",
        "tool_name": "write_file",
        "success": True,
        "result": {
            "ok": True,
            "source_tool": "deterministic_go_bare_import_string_repair",
            "file": "main.go",
            "bytes_written": 42,
            "operation": "write_file",
            "bridge_step_id": "materialization.go_import",
            "round_number": 1,
            "repair_kernel": {
                "callback_receipt_projections": [
                    {
                        "receipt_id": "malicious-materialization-payload-projection",
                        "receipt_authority": "authoritative",
                        "authoritative": True,
                        "projection_only": False,
                        "typed_receipt_path_available": True,
                    }
                ]
            },
        },
    }
    public_projection = {
        "projection_id": "materialization-public-projection",
        "receipt_id": "malicious-materialization-public-projection",
        "receipt_authority": "authoritative",
        "schedule_kind": "materialization_quality",
        "step_id": "materialization.go_import",
        "source_tool": "deterministic_go_bare_import_string_repair",
        "round_number": 2,
        "max_rounds": 3,
        "projection_only": False,
        "authoritative": True,
        "typed_receipt_path_available": True,
        "revalidation_evidence_present": True,
    }

    def fake_schedule_result(
        *,
        runner_step_ids: tuple[str, ...],
        runner: Any,
        max_rounds: int = 1,
    ) -> SimpleNamespace:
        del runner
        assert "materialization.go_import" in runner_step_ids
        ordered_steps = tuple(
            step
            if step_id == "materialization.go_import"
            else materialization_quality_repair_bridge.DirectorRepairMaterializationQualityStepV1(
                step_id=step_id,
                language=step_id.split(".", 1)[-1],
                phase="materialization_quality",
                priority=index,
                source_tool=f"{step_id}.source_tool",
            )
            for index, step_id in enumerate(runner_step_ids)
        )
        return SimpleNamespace(
            ordered_steps=ordered_steps,
            tool_results=(tool_result,),
            receipt_projections=(public_projection,),
            summary={
                "schedule_kind": "materialization_quality",
                "max_rounds": max_rounds,
                "rounds_run": 2,
                "receipt_projection_count": 1,
            },
        )

    monkeypatch.setattr(
        materialization_quality_repair_bridge,
        "run_director_materialization_quality_repair_schedule_result",
        fake_schedule_result,
    )

    _, summary = materialization_quality_repair_bridge.run_materialization_quality_repairs(
        FakeAdapter(),
        task={"target_files": ["main.go"]},
        task_id="task-materialization-public-projection",
        artifact_quality_errors=["Go syntax check failed: main.go:3:1: expected declaration"],
    )

    scheduler_bridge = summary["scheduler_bridge"]
    assert scheduler_bridge["callback_receipt_projection_count"] == 1
    assert scheduler_bridge["callback_receipts_authoritative"] is False
    assert scheduler_bridge["callback_receipts_with_revalidation"] == 1
    assert scheduler_bridge["typed_receipt_path_available"] is False
    assert scheduler_bridge["authoritative_receipts_allowed"] is False
    assert scheduler_bridge["native_repair_kernel_receipt_count"] == 0
    assert scheduler_bridge["callback_projection_only_count"] == 1
    assert scheduler_bridge["callback_authoritative_receipt_count"] == 0
    assert scheduler_bridge["callback_receipt_authority_values"] == ["non_authoritative_callback_projection"]
    assert scheduler_bridge["callback_projection_claimed_typed_receipt_path_count"] == 1
    assert scheduler_bridge.get("projection_only", True) is True
    assert scheduler_bridge["remaining_callback_only_step_ids"] == ["materialization.go_import"]
    assert scheduler_bridge["callback_only_step_count"] == 1
    go_lifecycle = scheduler_bridge["receipt_lifecycle_by_step"]["materialization.go_import"]
    assert go_lifecycle["typed_receipt_path_available"] is False
    assert go_lifecycle["authoritative_receipts_allowed"] is False
    assert go_lifecycle["native_receipt_present"] is False
    assert go_lifecycle["callback_projection_present"] is True
    assert go_lifecycle["callback_only"] is True
    assert go_lifecycle["projection_only"] is True
    assert go_lifecycle["verifier_evidence_present"] is False
    assert go_lifecycle["native_verifier_evidence_present"] is False
    assert go_lifecycle["callback_verifier_evidence_present"] is False
    assert go_lifecycle["native_repair_kernel_receipt_count"] == 0
    assert go_lifecycle["callback_receipt_projection_count"] == 1
    assert go_lifecycle["callback_projection_only_count"] == 1
    assert go_lifecycle["callback_receipt_evidence_status_counts"] == {"missing_evidence": 1}
    assert go_lifecycle["receipt_lifecycle_evidence_status"] == "missing_evidence"
    assert "callback_projection_only" in go_lifecycle["cutover_blockers"]
    assert "missing_native_repair_receipt" in go_lifecycle["cutover_blockers"]
    migration_debt = summary["repair_kernel_migration_debt"]
    assert migration_debt["remaining_callback_only_step_ids"] == ["materialization.go_import"]
    assert migration_debt["callback_only_step_count"] == 1
    go_debt = {item["step_id"]: item for item in migration_debt["legacy_callback_debt"]}["materialization.go_import"]
    assert go_debt["native_receipt_present"] is False
    assert go_debt["callback_projection_present"] is True
    assert go_debt["callback_only"] is True
    assert go_debt["projection_only"] is True
    assert go_debt["authoritative_receipts_allowed"] is False
    assert go_debt["cutover_ready"] is False
    assert go_debt["verifier_evidence_present"] is False
    assert go_debt["native_verifier_evidence_present"] is False
    assert go_debt["callback_verifier_evidence_present"] is False
    assert "callback_projection_only" in go_debt["cutover_blockers"]
    assert "missing_native_repair_receipt" in go_debt["cutover_blockers"]
    _assert_non_authoritative_callback_projection_boundary(
        summary,
        forbidden_receipt_ids={
            "malicious-materialization-payload-projection",
            "malicious-materialization-public-projection",
        },
    )


def test_materialization_scheduler_bridge_separates_native_receipts_from_callback_projections(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    class FakeAdapter:
        def __init__(self) -> None:
            self.workspace = str(tmp_path)

    native_receipt_id = "native-materialization-receipt"
    callback_receipt_id = "callback-materialization-projection"
    tool_result = {
        "tool": "write_file",
        "tool_name": "write_file",
        "success": True,
        "result": {
            "ok": True,
            "source_tool": "deterministic_go_bare_import_string_repair",
            "file": "main.go",
            "bytes_written": 42,
            "operation": "write_file",
            "bridge_step_id": "materialization.go_import",
            "round_number": 1,
            "repair_kernel": {
                "receipts": [
                    {
                        "receipt_id": native_receipt_id,
                        "plan_id": "native-materialization-plan",
                        "source_tool": "deterministic_go_bare_import_string_repair",
                        "status": "applied",
                        "authoritative": True,
                        "files_changed": ["main.go"],
                        "before_hashes": {"main.go": "before-hash"},
                        "after_hashes": {"main.go": "after-hash"},
                        "round_number": 1,
                        "revalidation_evidence": {
                            "command": ["rtk", "go", "test", "./..."],
                            "exit_code": 0,
                            "errors_after": 0,
                            "net_error_reduction": 1,
                        },
                    }
                ]
            },
        },
    }
    public_projection = {
        "projection_id": "materialization-callback-projection",
        "receipt_id": callback_receipt_id,
        "receipt_authority": "authoritative",
        "schedule_kind": "materialization_quality",
        "step_id": "materialization.go_import",
        "source_tool": "deterministic_go_bare_import_string_repair",
        "round_number": 1,
        "max_rounds": 1,
        "projection_only": False,
        "authoritative": True,
        "typed_receipt_path_available": True,
        "revalidation_evidence_present": False,
    }

    def fake_schedule_result(
        *,
        runner_step_ids: tuple[str, ...],
        runner: Any,
        max_rounds: int = 1,
    ) -> SimpleNamespace:
        del runner
        ordered_steps = tuple(
            materialization_quality_repair_bridge.DirectorRepairMaterializationQualityStepV1(
                step_id=step_id,
                language=step_id.split(".", 1)[-1],
                phase="materialization_quality",
                priority=index,
                source_tool=(
                    "deterministic_go_bare_import_string_repair"
                    if step_id == "materialization.go_import"
                    else f"{step_id}.source_tool"
                ),
            )
            for index, step_id in enumerate(runner_step_ids)
        )
        return SimpleNamespace(
            ordered_steps=ordered_steps,
            tool_results=(tool_result,),
            receipt_projections=(public_projection,),
            summary={
                "schedule_kind": "materialization_quality",
                "max_rounds": max_rounds,
                "rounds_run": 1,
                "receipt_projection_count": 1,
            },
        )

    monkeypatch.setattr(
        materialization_quality_repair_bridge,
        "run_director_materialization_quality_repair_schedule_result",
        fake_schedule_result,
    )

    _, summary = materialization_quality_repair_bridge.run_materialization_quality_repairs(
        FakeAdapter(),
        task={"target_files": ["main.go"]},
        task_id="task-materialization-native-vs-callback",
        artifact_quality_errors=["Go syntax check failed: main.go:3:1: expected declaration"],
    )

    repair_kernel_receipts = [
        receipt for receipt in summary["repair_kernel"].get("receipts", []) if isinstance(receipt, dict)
    ]
    assert summary["repair_kernel"]["receipt_count"] == 1
    assert {receipt.get("receipt_id") for receipt in repair_kernel_receipts} == {native_receipt_id}
    assert {receipt.get("receipt_id") for receipt in repair_kernel_receipts}.isdisjoint({callback_receipt_id})

    scheduler_bridge = summary["scheduler_bridge"]
    assert scheduler_bridge["repair_kernel_receipt_count"] == 1
    assert scheduler_bridge["native_repair_kernel_receipt_count"] == 1
    assert scheduler_bridge["callback_receipt_projection_count"] == 1
    assert scheduler_bridge["callback_projection_only_count"] == 1
    assert scheduler_bridge["callback_authoritative_receipt_count"] == 0
    assert scheduler_bridge["callback_receipts_authoritative"] is False
    assert scheduler_bridge["authoritative_receipts_allowed"] is False
    assert scheduler_bridge["remaining_callback_only_step_ids"] == []
    assert scheduler_bridge["callback_only_step_count"] == 0
    assert scheduler_bridge["native_receipt_step_ids"] == ["materialization.go_import"]
    assert scheduler_bridge["callback_projection_step_ids"] == ["materialization.go_import"]
    assert scheduler_bridge["native_receipt_evidence_status_counts"] == {"resolved_evidence": 1}
    assert scheduler_bridge["callback_receipt_evidence_status_counts"] == {"missing_evidence": 1}

    go_lifecycle = scheduler_bridge["receipt_lifecycle_by_step"]["materialization.go_import"]
    assert go_lifecycle["typed_receipt_path_available"] is True
    assert go_lifecycle["authoritative_receipts_allowed"] is False
    assert go_lifecycle["native_receipt_present"] is True
    assert go_lifecycle["callback_projection_present"] is True
    assert go_lifecycle["callback_only"] is False
    assert go_lifecycle["projection_only"] is False
    assert go_lifecycle["verifier_evidence_present"] is True
    assert go_lifecycle["native_verifier_evidence_present"] is True
    assert go_lifecycle["callback_verifier_evidence_present"] is False
    assert go_lifecycle["native_repair_kernel_receipt_count"] == 1
    assert go_lifecycle["callback_receipt_projection_count"] == 1
    assert go_lifecycle["native_receipt_evidence_status_counts"] == {"resolved_evidence": 1}
    assert go_lifecycle["callback_receipt_evidence_status_counts"] == {"missing_evidence": 1}
    assert go_lifecycle["receipt_lifecycle_evidence_status_counts"] == {
        "missing_evidence": 1,
        "resolved_evidence": 1,
    }
    assert go_lifecycle["receipt_lifecycle_evidence_status"] == "missing_evidence"
    assert "callback_projection_only" in go_lifecycle["cutover_blockers"]

    step_summary = summary["materialization_quality_step_summaries"]["materialization.go_import"]
    assert step_summary["native_repair_kernel_receipt_count"] == 1
    assert step_summary["callback_receipt_projection_count"] == 1
    assert step_summary["receipt_lifecycle_evidence_status"] == "missing_evidence"

    migration_debt = summary["repair_kernel_migration_debt"]
    assert migration_debt["native_receipt_step_ids"] == ["materialization.go_import"]
    assert migration_debt["callback_projection_step_ids"] == ["materialization.go_import"]
    assert migration_debt["remaining_callback_only_step_ids"] == []
    assert migration_debt["callback_only_step_count"] == 0
    go_debt = {item["step_id"]: item for item in migration_debt["legacy_callback_debt"]}["materialization.go_import"]
    assert go_debt["native_receipt_present"] is True
    assert go_debt["callback_projection_present"] is True
    assert go_debt["callback_only"] is False
    assert go_debt["projection_only"] is False
    assert go_debt["authoritative_receipts_allowed"] is False
    assert go_debt["verifier_evidence_present"] is True
    assert go_debt["native_verifier_evidence_present"] is True
    assert go_debt["callback_verifier_evidence_present"] is False
    assert go_debt["native_repair_kernel_receipt_count"] == 1
    assert go_debt["callback_receipt_projection_count"] == 1


def test_materialization_hygiene_native_receipt_cutover_evidence_projects_ready_summary(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    class FakeAdapter:
        def __init__(self) -> None:
            self.workspace = str(tmp_path)

    selected_step_id = "materialization.hygiene_scaffold"
    native_receipt = {
        "receipt_id": "native-hygiene-receipt",
        "plan_id": "native-hygiene-plan",
        "source_tool": "deterministic_materialization_hygiene_repair",
        "status": "applied",
        "authoritative": True,
        "files_changed": ["README.md"],
        "before_hashes": {"README.md": "before-hash"},
        "after_hashes": {"README.md": "after-hash"},
        "round_number": 1,
        "revalidation_evidence": {
            "command": ["rtk", "pytest", "tests/test_hygiene.py"],
            "exit_code": 0,
            "errors_after": 0,
            "net_error_reduction": 1,
        },
    }
    tool_result = {
        "tool": "write_file",
        "tool_name": "write_file",
        "success": True,
        "result": {
            "ok": True,
            "source_tool": "deterministic_materialization_hygiene_repair",
            "file": "README.md",
            "bytes_written": 12,
            "operation": "write_file",
            "bridge_step_id": selected_step_id,
            "round_number": 1,
            "repair_kernel": {"receipts": [native_receipt]},
        },
    }

    def fake_schedule_result(
        *,
        runner_step_ids: tuple[str, ...],
        runner: Any,
        max_rounds: int = 1,
    ) -> SimpleNamespace:
        del runner
        assert selected_step_id in runner_step_ids
        ordered_steps = tuple(
            materialization_quality_repair_bridge.DirectorRepairMaterializationQualityStepV1(
                step_id=step_id,
                language="multi" if step_id == selected_step_id else step_id.split(".", 1)[-1],
                phase="hygiene" if step_id == selected_step_id else "materialization_quality",
                priority=index,
                source_tool=(
                    "deterministic_materialization_hygiene_repair"
                    if step_id == selected_step_id
                    else f"{step_id}.source_tool"
                ),
            )
            for index, step_id in enumerate(runner_step_ids)
        )
        return SimpleNamespace(
            ordered_steps=ordered_steps,
            tool_results=(tool_result,),
            receipt_projections=(),
            summary={
                "schedule_kind": "materialization_quality",
                "max_rounds": max_rounds,
                "rounds_run": 1,
                "receipt_projection_count": 0,
            },
        )

    monkeypatch.setattr(
        materialization_quality_repair_bridge,
        "run_director_materialization_quality_repair_schedule_result",
        fake_schedule_result,
    )

    _, summary = materialization_quality_repair_bridge.run_materialization_quality_repairs(
        FakeAdapter(),
        task={"target_files": ["README.md"]},
        task_id="task-materialization-hygiene-native-ready",
        artifact_quality_errors=["scaffold marker found in README.md"],
    )

    scheduler_bridge = summary["scheduler_bridge"]
    evidence = scheduler_bridge["selected_step_native_cutover_evidence"][selected_step_id]
    assert scheduler_bridge["native_receipt_standardization_step_ids"] == [selected_step_id]
    assert scheduler_bridge["selected_step_native_path_available_step_ids"] == [selected_step_id]
    assert scheduler_bridge["selected_step_native_cutover_ready_step_ids"] == [selected_step_id]
    assert scheduler_bridge["selected_step_native_cutover_blockers_by_step"] == {selected_step_id: []}
    assert evidence["selected_for_standardization"] is True
    assert evidence["native_path_available"] is True
    assert evidence["native_repair_kernel_receipt_count"] == 1
    assert evidence["callback_receipt_projection_count"] == 0
    assert evidence["native_verifier_evidence_present"] is True
    assert evidence["native_evidence_resolved"] is True
    assert evidence["missing_required_evidence"] == []
    assert evidence["cutover_ready"] is True

    lifecycle = scheduler_bridge["receipt_lifecycle_by_step"][selected_step_id]
    assert lifecycle["native_path_available"] is True
    assert lifecycle["selected_for_native_receipt_standardization"] is True
    assert lifecycle["native_cutover_ready"] is True
    assert lifecycle["native_cutover_evidence"] == evidence
    assert lifecycle["cutover_ready"] is False

    step_summary = summary["materialization_quality_step_summaries"][selected_step_id]
    assert step_summary["native_cutover_ready"] is True
    assert step_summary["native_cutover_evidence"]["cutover_ready"] is True
    assert summary["repair_kernel_migration_debt"]["cutover_ready"] is False
