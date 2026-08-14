"""Tests for Director adapter repair bridge receipt projection."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import polaris.cells.director.runtime.public as director_runtime_public
from polaris.cells.director.runtime.public.contracts import (
    DirectorRepairResultV1,
    DirectorRepairVerifierSnapshotInputV1,
    RepairAdvisoryV1,
    RepairReceiptV1,
)
from polaris.cells.director.runtime.public.repair_kernel_contracts import (
    RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL,
)
from polaris.cells.roles.adapters.internal.director import (
    materialization_quality_callback_ports,
    materialization_quality_runtime_ports,
    post_execution_repair_bridge,
    runtime_repair_tool_adapter as runtime_bridge_module,
)
from polaris.cells.roles.adapters.public import (
    DirectorMaterializationQualityRepairScheduleResultV1,
    RunDirectorMaterializationQualityRepairScheduleCommandV1,
    service as roles_adapters_public_service,
)
from polaris.kernelone.quality import artifact_quality_issues_from_errors

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


def _runtime_ports_diagnostics(summary: dict[str, Any]) -> dict[str, Any]:
    diagnostics = summary.get("runtime_ports_diagnostics")
    if isinstance(diagnostics, dict):
        return diagnostics
    return summary


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
                        "receipt_authority": "non_authoritative_adapter_projection",
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


def _patch_materialization_facade_from_schedule(monkeypatch: Any, fake_schedule_result: Any) -> None:
    def fake_facade(
        *,
        artifact_quality_errors: tuple[str, ...] | list[str],
        artifact_quality_issues: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
        runner_step_ids: tuple[str, ...],
        runner: Any,
        plan_probe_preaudit: dict[str, Any] | None = None,
        convergence_verifier_present: bool = False,
        max_rounds: int = 1,
    ) -> SimpleNamespace:
        schedule_result = fake_schedule_result(
            runner_step_ids=runner_step_ids,
            runner=runner,
            max_rounds=max_rounds,
        )
        ordered_steps = tuple(schedule_result.ordered_steps)
        runtime_step_ids = [step.step_id for step in ordered_steps]
        step_by_id = {step.step_id: step for step in ordered_steps}
        tool_results: list[dict[str, Any]] = []
        for item in schedule_result.tool_results:
            payload = dict(item)
            result = dict(payload.get("result") or {}) if isinstance(payload.get("result"), dict) else {}
            step_id = str(result.get("bridge_step_id") or payload.get("runtime_step_id") or "").strip()
            step = step_by_id.get(step_id)
            if step is not None:
                payload["runtime_step_id"] = step.step_id
                payload["runtime_step_phase"] = step.phase
                payload["runtime_step_priority"] = step.priority
                payload["runtime_step_depends_on"] = list(step.depends_on)
                result.setdefault("runtime_step_id", step.step_id)
                result.setdefault("phase", step.phase)
                result.setdefault("priority", step.priority)
                result.setdefault("depends_on", list(step.depends_on))
                result.setdefault("evidence_status", "missing_evidence")
                payload["result"] = result
                payload.setdefault("evidence_status", "missing_evidence")
            tool_results.append(payload)
        return SimpleNamespace(
            schema_version="director.materialization_quality_repair_facade_result.v1",
            source="director.runtime.repair_kernel.materialization_quality_facade",
            owner_cell="director.runtime",
            execution_boundary="runtime_materialization_quality_facade_no_direct_writes",
            ordered_steps=ordered_steps,
            tool_results=tuple(tool_results),
            receipt_projections=tuple(dict(item) for item in schedule_result.receipt_projections),
            coverage_preaudit={
                "schema_version": "director.repair_coverage_report.v1",
                "total_diagnostics": len(tuple(artifact_quality_errors)) + len(tuple(artifact_quality_issues)),
                "items": [],
            },
            plan_probe_preaudit=dict(plan_probe_preaudit or {}),
            schedule_summary=dict(schedule_result.summary),
            schedule_reconciliation={
                "schema_version": "director.materialization_quality_schedule_reconciliation.v1",
                "runtime_schedule_owner": "director.runtime",
                "runner_binding_owner": "roles.adapters",
                "runtime_step_ids": runtime_step_ids,
                "runner_step_ids": list(runner_step_ids),
                "schedule_result_step_ids": runtime_step_ids,
                "exact_match": True,
            },
            summary={
                "schema_version": "director.materialization_quality_repair_facade_summary.v1",
                "stage": "deterministic_quality_repair",
                "attempted": bool(schedule_result.tool_results),
                "tool_results": len(schedule_result.tool_results),
                "convergence_verifier_present": bool(convergence_verifier_present),
            },
            max_rounds=max_rounds,
            rounds_run=int(schedule_result.summary.get("rounds_run", 0)),
            convergence_status="completed",
            stopped_reason="schedule_complete",
        )

    monkeypatch.setattr(director_runtime_public, "run_director_materialization_quality_repair_facade", fake_facade)


def _patch_materialization_schedule_result_as_dicts(monkeypatch: Any) -> None:
    def fake_schedule_result(
        *,
        runner_step_ids: tuple[str, ...],
        runner: Any,
        max_rounds: int = 1,
    ) -> SimpleNamespace:
        ordered_steps = tuple(
            materialization_quality_runtime_ports.DirectorRepairMaterializationQualityStepV1(
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

    _patch_materialization_facade_from_schedule(monkeypatch, fake_schedule_result)


def _materialization_runtime_schedule_steps() -> tuple[Any, ...]:
    source_tools_by_step_id = {
        "materialization.hygiene_scaffold": "deterministic_patch_residue_cleanup",
        "materialization.typescript_scaffold": "deterministic_typescript_scaffold_repair",
        "materialization.typescript_compiler": "deterministic_typescript_return_object_semicolon_repair",
        "materialization.html_entrypoint": "deterministic_html_typescript_module_script_repair",
        "materialization.node_manifest": "deterministic_runtime_dependency_repair",
        "materialization.rust_compiler": "deterministic_rust_crate_import_rewrite_repair",
        "materialization.target_runtime": "deterministic_javascript_missing_export_repair",
        "materialization.python_import": "deterministic_python_import_repair",
        "materialization.go_import": "deterministic_go_bare_import_string_repair",
    }
    dependencies_by_step_id = {
        "materialization.typescript_compiler": ("materialization.typescript_scaffold",),
        "materialization.html_entrypoint": ("materialization.typescript_compiler",),
        "materialization.node_manifest": ("materialization.html_entrypoint",),
        "materialization.rust_compiler": ("materialization.node_manifest",),
        "materialization.go_import": ("materialization.target_runtime",),
    }
    return tuple(
        materialization_quality_runtime_ports.DirectorRepairMaterializationQualityStepV1(
            step_id=step_id,
            language=step_id.rsplit(".", 1)[-1],
            phase="materialization_quality",
            priority=index,
            source_tool=source_tools_by_step_id[step_id],
            depends_on=dependencies_by_step_id.get(step_id, ()),
        )
        for index, step_id in enumerate(
            materialization_quality_callback_ports._MATERIALIZATION_QUALITY_REPAIR_RUNNERS,
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
        materialization_quality_callback_ports,
        "query_director_repair_materialization_quality_schedule",
        fake_query,
    )
    monkeypatch.setattr(director_runtime_public, "query_director_repair_materialization_quality_schedule", fake_query)


def _assert_non_authoritative_callback_projection_boundary(
    summary: dict[str, Any],
    *,
    forbidden_receipt_ids: set[str],
) -> None:
    scheduler_bridge = summary["scheduler_bridge"]
    assert scheduler_bridge["adapter_receipts_authoritative"] is False
    assert scheduler_bridge["callback_receipts_authoritative"] is False
    assert scheduler_bridge["typed_receipt_path_available"] is False
    assert (
        scheduler_bridge["migration_blocker"]
        == "adapter schedule runners still return tool_results instead of RepairReceipt"
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
        self.artifact_quality_errors: list[str] = []

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


# DEO-2C removed the synchronous adapter-owned mutation contract. Its
# replacement coverage lives in test_director_repair_writers.py and the
# roles.kernel deferred-repair effect/follow-up suites.


def test_rust_post_execution_bridge_runs_runtime_source_tool_sequence_without_adapter_aggregate(
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
        "error[E0609]: no field `name` on type `Demo`\n"
        "error[E0432]: unresolved import `demo::external`\n"
    )
    lib.parent.mkdir(parents=True)
    sibling.parent.mkdir(parents=True)
    cargo.write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    lib.write_text("pub mod models;\n", encoding="utf-8")
    duplicate.write_text(generated, encoding="utf-8")
    sibling.write_text(real, encoding="utf-8")
    adapter = _FakeAdapter(tmp_path)
    adapter.artifact_quality_errors = [raw_error]
    called_source_tools: list[str] = []
    expected_advisor_notes = (
        RepairAdvisoryV1(
            advisor_source="resident_agi",
            message="Rust post execution recurring diagnostics.",
            confidence=0.5,
        ),
    )

    def sentinel_verifier(request: Any) -> Any:
        return {"request": request}

    def fail_if_adapter_aggregate_called(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError("Rust post-execution must not call legacy aggregate helpers")

    def fake_runtime_repair_with_director_tools(
        adapter_arg: Any,
        *,
        workspace_path: Path,
        task_id: str,
        source_tool: str,
        execution_attempt: Any,
        base_files: dict[str, str],
        artifact_quality_errors: tuple[str, ...] = (),
        allowed_paths: tuple[str, ...] = (),
        advisor_notes: tuple[RepairAdvisoryV1, ...] = (),
        convergence_verifier: Any = None,
        max_rounds: int = 1,
        use_editor: bool = False,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        del execution_attempt, kwargs
        assert adapter_arg is adapter
        assert workspace_path == tmp_path.resolve()
        assert task_id == "task-rust-runtime-sequence"
        assert base_files[duplicate_path] == generated
        assert base_files[sibling_path] == real
        assert raw_error.strip() in artifact_quality_errors
        called_source_tools.append(source_tool)
        if source_tool == RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL:
            assert duplicate_path in allowed_paths
            assert sibling_path in allowed_paths
        if source_tool in {
            "deterministic_rust_missing_fields_repair",
            "deterministic_rust_lib_root_facade_repair",
        }:
            assert use_editor is True
            assert advisor_notes == expected_advisor_notes
            assert convergence_verifier is sentinel_verifier
            assert max_rounds == 1
        return []

    monkeypatch.setattr(
        post_execution_repair_bridge,
        "run_runtime_repair_with_director_tools",
        fake_runtime_repair_with_director_tools,
    )
    results = post_execution_repair_bridge._run_rust_post_repairs(
        adapter,
        tmp_path,
        task_id="task-rust-runtime-sequence",
        advisor_notes=expected_advisor_notes,
        convergence_verifier=sentinel_verifier,
    )

    assert results == []
    assert RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL in called_source_tools
    assert called_source_tools[-2:] == [
        "deterministic_rust_missing_fields_repair",
        "deterministic_rust_lib_root_facade_repair",
    ]
    assert "deterministic_rust_missing_fields_repair" in called_source_tools
    assert "deterministic_rust_lib_root_facade_repair" in called_source_tools


def test_post_execution_scheduler_passes_verifier_and_advisory_to_rust_steps(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")

    class FakeAdapter:
        workspace = str(tmp_path)

    def sentinel_verifier(request: Any) -> Any:
        return {"request": request}

    overlay = {
        "status": "ready",
        "eligible_for_director_injection": True,
        "advisory_only": True,
        "authoritative": False,
        "agi_execution_authority": False,
        "advisor_notes": [
            {
                "advisor_source": "resident_agi",
                "message": "Rust convergence coverage.",
                "confidence": 0.4,
            }
        ],
    }
    captured: dict[str, dict[str, Any]] = {}

    def fake_rust_dependency(
        adapter: Any,
        *,
        task_id: str,
        advisor_notes: tuple[RepairAdvisoryV1, ...] = (),
        convergence_verifier: Any = None,
    ) -> list[dict[str, Any]]:
        captured["dependency"] = {
            "adapter": adapter,
            "task_id": task_id,
            "advisor_notes": advisor_notes,
            "convergence_verifier": convergence_verifier,
        }
        return []

    def fake_rust_post(
        adapter: Any,
        workspace: Path,
        *,
        task_id: str,
        advisor_notes: tuple[RepairAdvisoryV1, ...] = (),
        convergence_verifier: Any = None,
    ) -> list[dict[str, Any]]:
        captured["post"] = {
            "adapter": adapter,
            "workspace": workspace,
            "task_id": task_id,
            "advisor_notes": advisor_notes,
            "convergence_verifier": convergence_verifier,
        }
        return [
            post_execution_repair_bridge._record_to_tool_result(
                {"file": "src/lib.rs", "action": "rust_post_repair"},
                source_tool="deterministic_rust_missing_fields_repair",
                default_action="rust_post_repair",
            )
        ]

    monkeypatch.setattr(post_execution_repair_bridge, "_run_go_post_repairs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        post_execution_repair_bridge, "run_cpp_post_repairs_as_tool_results", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(post_execution_repair_bridge, "_run_java_post_repairs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(post_execution_repair_bridge, "_run_rust_dependency_repair", fake_rust_dependency)
    monkeypatch.setattr(post_execution_repair_bridge, "_run_rust_post_repairs", fake_rust_post)

    tool_results, summary = post_execution_repair_bridge.run_post_execution_language_repairs(
        FakeAdapter(),
        task_id="task-rust-scheduler-verifier",
        resident_agi_repair_advisory_overlay=overlay,
        convergence_verifier=sentinel_verifier,
    )

    assert len(tool_results) == 1
    assert summary is not None
    assert captured["dependency"]["convergence_verifier"] is sentinel_verifier
    assert captured["post"]["convergence_verifier"] is sentinel_verifier
    assert captured["dependency"]["advisor_notes"][0].advisor_source == "resident_agi"
    assert captured["post"]["advisor_notes"][0].advisor_source == "resident_agi"


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
        execution_attempt: Any,
        base_files: dict[str, str],
        convergence_verifier: Any = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        del adapter, execution_attempt, kwargs
        call = {
            "workspace_path": workspace_path,
            "task_id": task_id,
            "source_tool": source_tool,
            "base_files": dict(base_files),
            "convergence_verifier": convergence_verifier,
        }
        captured.setdefault("runtime_bridge_calls", []).append(call)
        if source_tool != "deterministic_go_bare_import_string_repair":
            return []
        captured["runtime_bridge"] = call
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
        "_run_materialization_html_entrypoint",
        "_run_materialization_node_manifest",
        "_run_materialization_rust_compiler",
        "_run_materialization_target_runtime",
        "_run_materialization_python_import",
    ):
        monkeypatch.setattr(materialization_quality_callback_ports, runner_name, lambda *args, **kwargs: [])
    monkeypatch.setattr(
        materialization_quality_callback_ports, "run_runtime_repair_with_director_tools", fake_runtime_bridge
    )
    assert not hasattr(materialization_quality_runtime_ports, "repair_go_nested_import_keyword")
    assert not hasattr(materialization_quality_runtime_ports, "repair_go_import_subpaths")
    assert not hasattr(materialization_quality_runtime_ports, "repair_go_duplicate_declarations")
    _patch_materialization_schedule_result_as_dicts(monkeypatch)

    results, summary = roles_adapters_public_service.run_director_materialization_quality_repair_schedule(
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
    assert [call["source_tool"] for call in captured["runtime_bridge_calls"]] == [
        "deterministic_go_bare_import_string_repair"
    ]
    assert captured["verifier_request"].round_number == 1
    assert summary["convergence_verifier_present"] is True
    assert (
        _runtime_ports_diagnostics(summary)["materialization_quality_runtime_ports"]["convergence_verifier_present"]
        is True
    )
    migration_debt = _runtime_ports_diagnostics(summary)["repair_kernel_migration_debt"]
    assert migration_debt["convergence_verifier_present"] is True
    assert migration_debt["cutover_ready"] is False
    go_debt = {item["step_id"]: item for item in migration_debt["adapter_projection_debt"]}["materialization.go_import"]
    assert go_debt["runtime_executable_source_tools"] == ["deterministic_go_bare_import_string_repair"]
    assert go_debt["adapter_only_source_tools"] == []
    assert go_debt["convergence_path_available"] is True
    assert go_debt["convergence_verifier_present"] is True
    assert go_debt["verifier_evidence_present"] is True
    assert go_debt["cutover_ready"] is False
    assert "missing_revalidation_evidence" not in go_debt["blockers"]
    assert "adapter_schedule_runner" in go_debt["blockers"]
    repair_kernel = results[0]["result"]["repair_kernel"]
    assert repair_kernel["convergence_status"] == "converged"
    assert repair_kernel["revalidation_evidence"]["command"] == ["rtk", "go", "test", "./..."]


def test_materialization_python_import_runs_through_runtime_bridge(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    package_init = tmp_path / "shared" / "__init__.py"
    registry = tmp_path / "shared" / "registry.py"
    package_init.parent.mkdir(parents=True)
    package_init.write_text("from shared.registry import Registry\n", encoding="utf-8")
    registry.write_text("class ServiceRegistry:\n    pass\n", encoding="utf-8")
    artifact_quality_errors = [
        (
            "Artifact quality scan failed: unresolved import symbol "
            "'Registry' from 'shared.registry' in shared/__init__.py"
        )
    ]
    runtime_calls: list[dict[str, Any]] = []

    def fake_runtime_bridge(
        adapter: Any,
        *,
        workspace_path: Path,
        task_id: str,
        source_tool: str,
        execution_attempt: Any,
        base_files: dict[str, str],
        artifact_quality_errors: list[str],
        allowed_paths: tuple[str, ...],
        use_editor: bool,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        del adapter, execution_attempt, kwargs
        runtime_calls.append(
            {
                "workspace_path": workspace_path,
                "task_id": task_id,
                "source_tool": source_tool,
                "base_files": dict(base_files),
                "artifact_quality_errors": list(artifact_quality_errors),
                "allowed_paths": tuple(allowed_paths),
                "use_editor": use_editor,
            }
        )
        if source_tool != "deterministic_unresolved_import_symbol_repair":
            return []
        return [
            {
                "tool": "edit_file",
                "tool_name": "edit_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": source_tool,
                    "file": "shared/registry.py",
                    "bridge_step_id": "materialization.python_import",
                    "repair_kernel": {
                        "owner_cell": "director.runtime",
                        "authoritative": True,
                    },
                },
            }
        ]

    monkeypatch.setattr(
        materialization_quality_callback_ports, "run_runtime_repair_with_director_tools", fake_runtime_bridge
    )

    results = materialization_quality_callback_ports._run_materialization_python_import(
        _FakeAdapter(tmp_path),
        task={"target_files": ["shared/__init__.py", "shared/registry.py"]},
        task_id="task-python-materialization",
        artifact_quality_errors=artifact_quality_errors,
    )

    assert len(results) == 1
    assert [call["source_tool"] for call in runtime_calls] == ["deterministic_unresolved_import_symbol_repair"]
    assert runtime_calls[-1]["base_files"] == {
        "shared/__init__.py": "from shared.registry import Registry\n",
        "shared/registry.py": "class ServiceRegistry:\n    pass\n",
    }
    assert runtime_calls[-1]["artifact_quality_errors"] == artifact_quality_errors
    assert runtime_calls[-1]["allowed_paths"] == ("shared/__init__.py", "shared/registry.py")
    assert runtime_calls[-1]["use_editor"] is True
    assert results[0]["result"]["source_tool"] == "deterministic_unresolved_import_symbol_repair"


def test_materialization_remaining_steps_run_through_runtime_bridge_not_legacy(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.deterministic_repairs import (
        generic_repairs,
        javascript_repairs,
        npm_repairs,
        typeorm_repairs,
        typescript_repairs,
    )

    source = tmp_path / "src" / "app.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const label = 'audit-seed scaffold residue';\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "test.mjs").write_text("console.log('test');\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"node scripts/test.mjs"},"dependencies":{}}\n',
        encoding="utf-8",
    )

    legacy_helpers = (
        (generic_repairs, "_apply_deterministic_scaffold_marker_cleanup"),
        (generic_repairs, "_apply_deterministic_scaffold_marker_error_cleanup"),
        (generic_repairs, "_apply_deterministic_missing_declared_target_repair"),
        (javascript_repairs, "_apply_deterministic_javascript_missing_export_repair"),
        (javascript_repairs, "_apply_deterministic_javascript_esm_commonjs_entrypoint_repair"),
        (typescript_repairs, "_apply_deterministic_typescript_missing_export_repair"),
    )

    def fail_if_legacy_called(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError("materialization migrated step called a legacy direct helper")

    assert not hasattr(npm_repairs, "_apply_deterministic_npm_test_script_repair")
    assert not hasattr(npm_repairs, "_apply_deterministic_runtime_dependency_repair")
    assert not hasattr(npm_repairs, "_apply_deterministic_typescript_scaffold_repair")
    assert not hasattr(generic_repairs, "_apply_deterministic_scaffold_marker_cleanup")
    assert not hasattr(generic_repairs, "_apply_deterministic_scaffold_marker_error_cleanup")
    assert not hasattr(generic_repairs, "_apply_deterministic_pre_materialization_declared_target_repairs")
    assert not hasattr(generic_repairs, "_apply_deterministic_declared_target_contract_repairs")
    assert not hasattr(generic_repairs, "_apply_deterministic_missing_declared_target_repair")
    assert not hasattr(javascript_repairs, "_apply_deterministic_javascript_test_missing_target_repair")
    assert not hasattr(javascript_repairs, "_apply_deterministic_javascript_typescript_annotation_repair")
    assert not hasattr(javascript_repairs, "_apply_deterministic_javascript_missing_export_repair")
    assert not hasattr(javascript_repairs, "_apply_deterministic_javascript_esm_commonjs_entrypoint_repair")
    assert not hasattr(javascript_repairs, "_apply_deterministic_javascript_missing_method_runtime_repair")
    assert not hasattr(typescript_repairs, "_apply_deterministic_html_typescript_module_script_repair")
    assert not hasattr(typescript_repairs, "_apply_deterministic_typescript_entrypoint_repair")
    assert not hasattr(typescript_repairs, "_apply_deterministic_typescript_member_alias_repair")
    assert not hasattr(typescript_repairs, "_apply_deterministic_typescript_relative_import_case_repair")
    assert not hasattr(typescript_repairs, "_apply_deterministic_typescript_missing_member_repair")
    assert not hasattr(typescript_repairs, "_apply_deterministic_typescript_reexport_repair")
    assert not hasattr(typescript_repairs, "_apply_deterministic_typescript_reexported_type_binding_repair")
    assert not hasattr(typescript_repairs, "_looks_like_typescript_reexport_failure")
    assert not hasattr(typescript_repairs, "_apply_deterministic_typescript_missing_export_repair")
    assert not hasattr(typescript_repairs, "_apply_deterministic_typescript_tsconfig_lib_repair")
    assert not hasattr(typescript_repairs, "_apply_deterministic_typescript_unresolved_identifier_repair")
    assert not hasattr(typeorm_repairs, "_apply_deterministic_typeorm_model_normalization_repair")
    for module, helper_name in legacy_helpers:
        if hasattr(module, helper_name):
            monkeypatch.setattr(module, helper_name, fail_if_legacy_called)

    runtime_calls: list[dict[str, Any]] = []

    def sentinel_verifier(request: Any) -> Any:
        raise AssertionError("sentinel verifier must not be invoked")

    def fake_runtime_bridge(
        adapter: Any,
        *,
        workspace_path: Path,
        task_id: str,
        source_tool: str,
        execution_attempt: Any,
        base_files: dict[str, str],
        artifact_quality_errors: list[str],
        allowed_paths: tuple[str, ...],
        use_editor: bool,
        convergence_verifier: Any = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        del adapter, execution_attempt, artifact_quality_errors, kwargs
        runtime_calls.append(
            {
                "workspace_path": workspace_path,
                "task_id": task_id,
                "source_tool": source_tool,
                "base_files": dict(base_files),
                "allowed_paths": tuple(allowed_paths),
                "use_editor": use_editor,
                "convergence_verifier": convergence_verifier,
            }
        )
        return []

    monkeypatch.setattr(
        materialization_quality_callback_ports, "run_runtime_repair_with_director_tools", fake_runtime_bridge
    )
    task = {
        "target_files": ["src/app.ts"],
        "metadata": {"autofix_reason": "deterministic_scaffold_residue_cleanup"},
    }
    artifact_quality_errors = [
        "deterministic scaffold marker 'audit-seed' in src/app.ts",
        "package.json missing",
        "tsconfig.json missing",
        "undeclared runtime import 'express' in src/app.ts",
        "declared target file src/app.model.ts is missing",
        "Node test runner contract failed: scripts/test.mjs",
    ]

    adapter = _FakeAdapter(tmp_path)
    materialization_quality_callback_ports._run_materialization_hygiene_scaffold(
        adapter,
        task=task,
        task_id="task-materialization-hard-cut",
        artifact_quality_errors=artifact_quality_errors,
        convergence_verifier=sentinel_verifier,
    )
    materialization_quality_callback_ports._run_materialization_typescript_scaffold(
        adapter,
        task=task,
        task_id="task-materialization-hard-cut",
        artifact_quality_errors=artifact_quality_errors,
        convergence_verifier=sentinel_verifier,
    )
    materialization_quality_callback_ports._run_materialization_node_manifest(
        adapter,
        task=task,
        task_id="task-materialization-hard-cut",
        artifact_quality_errors=artifact_quality_errors,
        convergence_verifier=sentinel_verifier,
    )
    materialization_quality_callback_ports._run_materialization_target_runtime(
        adapter,
        task=task,
        task_id="task-materialization-hard-cut",
        artifact_quality_errors=artifact_quality_errors,
        convergence_verifier=sentinel_verifier,
    )

    assert [call["source_tool"] for call in runtime_calls] == [
        *next(
            step.runtime_source_tools
            for step in director_runtime_public.query_director_repair_materialization_quality_schedule(
                director_runtime_public.QueryDirectorRepairMaterializationQualityScheduleV1(include_items=True)
            ).items
            if step.step_id == "materialization.hygiene_scaffold"
        ),
        *next(
            step.runtime_source_tools
            for step in director_runtime_public.query_director_repair_materialization_quality_schedule(
                director_runtime_public.QueryDirectorRepairMaterializationQualityScheduleV1(include_items=True)
            ).items
            if step.step_id == "materialization.typescript_scaffold"
        ),
        *next(
            step.runtime_source_tools
            for step in director_runtime_public.query_director_repair_materialization_quality_schedule(
                director_runtime_public.QueryDirectorRepairMaterializationQualityScheduleV1(include_items=True)
            ).items
            if step.step_id == "materialization.node_manifest"
        ),
        *next(
            step.runtime_source_tools
            for step in director_runtime_public.query_director_repair_materialization_quality_schedule(
                director_runtime_public.QueryDirectorRepairMaterializationQualityScheduleV1(include_items=True)
            ).items
            if step.step_id == "materialization.target_runtime"
        ),
    ]
    assert all(call["workspace_path"] == tmp_path.resolve() for call in runtime_calls)
    assert all(call["task_id"] == "task-materialization-hard-cut" for call in runtime_calls)
    assert all(call["use_editor"] is True for call in runtime_calls)
    assert all(call["convergence_verifier"] is sentinel_verifier for call in runtime_calls)


def test_materialization_target_runtime_allowed_paths_include_runtime_planned_new_test_target() -> None:
    base_files = {
        "package.json": (
            "{"
            '"scripts":{'
            '"build":"tsc -p tsconfig.json",'
            '"test":"npm run build && node --test --import tsx ./tests/smoke.test.ts '
            '2>/dev/null || node --test dist-test"'
            "},"
            '"main":"dist/main.js"'
            "}\n"
        ),
        "src/main.ts": "console.log('ok');\n",
    }
    artifact_quality_errors = ["workspace validation command failed (npm test): Could not find 'dist-test'"]

    allowed_paths = director_runtime_public.query_director_repair_materialization_allowed_paths(
        director_runtime_public.QueryDirectorRepairMaterializationAllowedPathsV1(
            source_tool="deterministic_javascript_test_missing_target_repair",
            base_files=base_files,
            artifact_quality_errors=tuple(artifact_quality_errors),
        )
    ).allowed_paths

    assert "package.json" in allowed_paths
    assert "src/main.ts" in allowed_paths
    assert "tests/smoke.test.ts" in allowed_paths


def test_materialization_target_runtime_intersects_allowed_paths_with_current_task_scope(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    runtime_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "_materialization_runtime_source_tools_for_step",
        lambda step_id: (
            ("deterministic_javascript_test_missing_target_repair",)
            if step_id == "materialization.target_runtime"
            else ()
        ),
    )
    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "_collect_materialization_target_runtime_base_files",
        lambda *args, **kwargs: {
            "package.json": '{"scripts":{"test":"node --test tests/product.test.js"}}\n',
            "src/engine/rules.js": "export const rules = [];\n",
        },
    )
    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "_materialization_allowed_paths_from_runtime_public_plan",
        lambda **kwargs: ("tests/product.test.js", "src/engine/rules.js"),
    )

    def fake_runtime_bridge(
        adapter: Any,
        *,
        workspace_path: Path,
        task_id: str,
        source_tool: str,
        execution_attempt: Any,
        base_files: dict[str, str],
        artifact_quality_errors: list[str],
        allowed_paths: tuple[str, ...],
        use_editor: bool,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        del adapter, execution_attempt, base_files, artifact_quality_errors, kwargs
        runtime_calls.append(
            {
                "workspace_path": workspace_path,
                "task_id": task_id,
                "source_tool": source_tool,
                "allowed_paths": allowed_paths,
                "use_editor": use_editor,
            }
        )
        return [{"tool": "edit_file", "success": True}]

    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "run_runtime_repair_with_director_tools",
        fake_runtime_bridge,
    )

    results = materialization_quality_callback_ports._run_materialization_target_runtime(
        _FakeAdapter(tmp_path),
        task={"target_files": ["src/engine/rules.js"]},
        task_id="TASK-1-source-core",
        artifact_quality_errors=["workspace validation command failed: missing tests/product.test.js"],
    )

    assert results == [{"tool": "edit_file", "success": True}]
    assert runtime_calls[0]["allowed_paths"] == ("src/engine/rules.js",)


def test_materialization_target_runtime_skips_out_of_scope_runtime_allowed_paths(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    runtime_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "_materialization_runtime_source_tools_for_step",
        lambda step_id: (
            ("deterministic_javascript_test_missing_target_repair",)
            if step_id == "materialization.target_runtime"
            else ()
        ),
    )
    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "_collect_materialization_target_runtime_base_files",
        lambda *args, **kwargs: {
            "package.json": '{"scripts":{"test":"node --test tests/product.test.js"}}\n',
            "src/engine/rules.js": "export const rules = [];\n",
        },
    )
    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "_materialization_allowed_paths_from_runtime_public_plan",
        lambda **kwargs: ("tests/product.test.js",),
    )

    def fake_runtime_bridge(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args
        runtime_calls.append(dict(kwargs))
        return [{"success": True}]

    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "run_runtime_repair_with_director_tools",
        fake_runtime_bridge,
    )

    results = materialization_quality_callback_ports._run_materialization_target_runtime(
        _FakeAdapter(tmp_path),
        task={"target_files": ["src/engine/rules.js"]},
        task_id="TASK-1-source-core",
        artifact_quality_errors=["workspace validation command failed: missing tests/product.test.js"],
    )

    assert results == []
    assert runtime_calls == []


def test_materialization_target_runtime_workspace_level_invocation_keeps_multi_file_plan(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Factory workspace-quality invocations keep the full runtime-planned multi-file repair."""

    runtime_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "_materialization_runtime_source_tools_for_step",
        lambda step_id: (
            ("deterministic_javascript_esm_commonjs_entrypoint_repair",)
            if step_id == "materialization.target_runtime"
            else ()
        ),
    )
    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "_collect_materialization_target_runtime_base_files",
        lambda *args, **kwargs: {
            "package.json": '{"type":"module","main":"src/index.js"}\n',
            "src/index.js": 'const Note = require("./models/Note");\n',
            "src/models/Note.js": "export class Note {}\n",
        },
    )
    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "_materialization_allowed_paths_from_runtime_public_plan",
        lambda **kwargs: ("package.json", "src/index.js", "src/models/Note.js"),
    )

    def fake_runtime_bridge(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args
        runtime_calls.append(dict(kwargs))
        return [{"tool": "edit_file", "success": True}]

    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "run_runtime_repair_with_director_tools",
        fake_runtime_bridge,
    )

    results = materialization_quality_callback_ports._run_materialization_target_runtime(
        _FakeAdapter(tmp_path),
        task={
            "target_files": ["src/index.js"],
            "metadata": {"target_files": ["src/index.js"], "delivery_mode": "materialize_changes"},
        },
        task_id="factory-quality-gate:run-esm-cjs",
        artifact_quality_errors=["ReferenceError: require is not defined in ES module scope"],
    )

    assert results == [{"tool": "edit_file", "success": True}]
    assert runtime_calls[0]["allowed_paths"] == ("package.json", "src/index.js", "src/models/Note.js")


def test_materialization_target_runtime_scope_intersection_normalizes_path_forms(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """``./`` prefixes and backslash separators must not defeat the task-scope intersection."""

    runtime_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "_materialization_runtime_source_tools_for_step",
        lambda step_id: (
            ("deterministic_javascript_test_missing_target_repair",)
            if step_id == "materialization.target_runtime"
            else ()
        ),
    )
    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "_collect_materialization_target_runtime_base_files",
        lambda *args, **kwargs: {
            "package.json": '{"scripts":{"test":"node --test tests/product.test.js"}}\n',
            "src/engine/rules.js": "export const rules = [];\n",
        },
    )
    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "_materialization_allowed_paths_from_runtime_public_plan",
        lambda **kwargs: ("./src/engine/rules.js", "tests\\product.test.js"),
    )

    def fake_runtime_bridge(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        del args
        runtime_calls.append(dict(kwargs))
        return [{"tool": "edit_file", "success": True}]

    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "run_runtime_repair_with_director_tools",
        fake_runtime_bridge,
    )

    results = materialization_quality_callback_ports._run_materialization_target_runtime(
        _FakeAdapter(tmp_path),
        task={"target_files": ["src\\engine\\rules.js"]},
        task_id="TASK-1-source-core",
        artifact_quality_errors=["workspace validation command failed: missing tests/product.test.js"],
    )

    assert results == [{"tool": "edit_file", "success": True}]
    assert runtime_calls[0]["allowed_paths"] == ("src/engine/rules.js",)


def test_materialization_target_runtime_base_files_include_rust_sources(tmp_path: Path) -> None:
    """Cargo test residuals live on tests/*.rs; the gate lives in src/*.rs.

    Live L1-09: collector used TS-only suffixes, so plan probe only saw
    tests/product.rs and reported covered_unplannable for a fixable gate.
    """

    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "alchemy_rules.rs").write_text(
        "pub fn is_valid_input() -> bool { true }\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "product.rs").write_text(
        "fn zero_mass_reagents_are_rejected_by_input_gate() {}\n",
        encoding="utf-8",
    )
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    residual = (
        "---- zero_mass_reagents_are_rejected_by_input_gate stdout ----\n"
        "thread 'zero_mass_reagents_are_rejected_by_input_gate' panicked at tests/product.rs:226:5:\n"
        "zero-mass bag must be rejected by gate\n"
        "test result: FAILED. 12 passed; 1 failed\n"
    )
    base_files = materialization_quality_callback_ports._collect_materialization_target_runtime_base_files(
        tmp_path,
        task={"target_files": ["src/alchemy_rules.rs", "tests/product.rs", "Cargo.toml"]},
        artifact_quality_errors=[residual],
        source_tool="deterministic_rust_line_suggestion_repair",
    )
    assert "src/alchemy_rules.rs" in base_files
    assert "tests/product.rs" in base_files
    assert "Cargo.toml" in base_files


def test_materialization_rust_migrated_bindings_run_through_runtime_bridge(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    source = tmp_path / "src" / "lib.rs"
    source.parent.mkdir(parents=True)
    source.write_text("use serde::Serialize;\npub struct Demo;\n", encoding="utf-8")

    runtime_calls: list[dict[str, Any]] = []

    def fake_runtime_bridge(
        adapter: Any,
        *,
        workspace_path: Path,
        task_id: str,
        source_tool: str,
        execution_attempt: Any,
        base_files: dict[str, str],
        artifact_quality_errors: list[str],
        allowed_paths: tuple[str, ...],
        use_editor: bool,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        del adapter, execution_attempt, artifact_quality_errors, kwargs
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

    expected_source_tools = [
        "deterministic_rust_crate_import_rewrite_repair",
        "deterministic_rust_dependency_repair",
        "deterministic_rust_missing_lib_target_repair",
        "deterministic_rust_missing_module_file_repair",
        "deterministic_rust_duplicate_module_file_repair",
        "deterministic_rust_lib_root_facade_repair",
        "deterministic_rust_serde_derive_repair",
        "deterministic_rust_line_suggestion_repair",
        "deterministic_rust_unresolved_pub_use_repair",
        "deterministic_rust_trait_import_repair",
    ]
    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "run_runtime_repair_with_director_tools",
        fake_runtime_bridge,
    )
    monkeypatch.setattr(runtime_bridge_module, "run_runtime_repair_with_director_tools", fake_runtime_bridge)
    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "_materialization_plannable_runtime_source_tools_from_base_files",
        lambda **_kwargs: tuple(expected_source_tools),
    )
    for runner_name in (
        "_run_materialization_hygiene_scaffold",
        "_run_materialization_typescript_scaffold",
        "_run_materialization_typescript_compiler",
        "_run_materialization_html_entrypoint",
        "_run_materialization_node_manifest",
        "_run_materialization_target_runtime",
        "_run_materialization_python_import",
        "_run_materialization_go_import",
    ):
        monkeypatch.setattr(materialization_quality_callback_ports, runner_name, lambda *args, **kwargs: [])
    _patch_materialization_schedule_result_as_dicts(monkeypatch)

    results, summary = roles_adapters_public_service.run_director_materialization_quality_repair_schedule(
        _FakeAdapter(tmp_path),
        task={"target_files": ["src/lib.rs"]},
        task_id="task-rust-materialization",
        artifact_quality_errors=["error[E0432]: unresolved import `serde`"],
    )

    assert [item["source_tool"] for item in runtime_calls] == expected_source_tools
    assert all(item["use_editor"] is True for item in runtime_calls)
    assert all(item["task_id"] == "task-rust-materialization" for item in runtime_calls)
    assert all(item["workspace_path"] == tmp_path.resolve() for item in runtime_calls)
    assert all(item["base_files"]["Cargo.toml"].startswith("[package]") for item in runtime_calls)
    assert all(item["base_files"]["src/lib.rs"] == source.read_text(encoding="utf-8") for item in runtime_calls)
    assert all(set(item["allowed_paths"]) == {"Cargo.toml", "src/lib.rs"} for item in runtime_calls)
    assert [item["result"]["source_tool"] for item in results] == expected_source_tools
    rust_debt = {
        item["step_id"]: item
        for item in _runtime_ports_diagnostics(summary)["repair_kernel_migration_debt"]["adapter_projection_debt"]
    }["materialization.rust_compiler"]
    assert "deterministic_rust_missing_lib_target_repair" in rust_debt["runtime_executable_source_tools"]
    assert "deterministic_rust_lib_root_facade_repair" in rust_debt["runtime_executable_source_tools"]
    assert rust_debt["adapter_only_source_tools"] == []


def test_materialization_rust_compiler_executes_only_plan_probe_plannable_tools(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    source = tmp_path / "src" / "lib.rs"
    source.parent.mkdir(parents=True)
    source.write_text("use serde::Serialize;\npub struct Demo;\n", encoding="utf-8")
    artifact_quality_errors = ["error[E0432]: unresolved import `serde`"]
    plannable_source_tools = ("deterministic_rust_dependency_repair",)
    probe_calls: list[dict[str, Any]] = []
    runtime_calls: list[str] = []

    def fake_plannable_tools(**kwargs: Any) -> tuple[str, ...]:
        probe_calls.append(dict(kwargs))
        return plannable_source_tools

    def fake_runtime_repair(*_args: Any, source_tool: str, **_kwargs: Any) -> list[dict[str, Any]]:
        runtime_calls.append(source_tool)
        return [
            {
                "tool": "edit_file",
                "tool_name": "edit_file",
                "success": True,
                "result": {"ok": True, "source_tool": source_tool},
            }
        ]

    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "_materialization_plannable_runtime_source_tools_from_base_files",
        fake_plannable_tools,
    )
    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "_run_materialization_rust_runtime_repair",
        fake_runtime_repair,
    )

    results = materialization_quality_callback_ports._run_materialization_rust_compiler(
        _FakeAdapter(tmp_path),
        task={"target_files": ["src/lib.rs"]},
        task_id="task-rust-materialization",
        artifact_quality_errors=artifact_quality_errors,
    )

    assert runtime_calls == ["deterministic_rust_dependency_repair"]
    assert [item["result"]["source_tool"] for item in results] == ["deterministic_rust_dependency_repair"]
    assert probe_calls == [
        {
            "artifact_quality_errors": artifact_quality_errors,
            "artifact_quality_issues": (),
            "materialization_step_id": "materialization.rust_compiler",
            "base_files": {
                "Cargo.toml": '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n',
                "src/lib.rs": "use serde::Serialize;\npub struct Demo;\n",
            },
            "caller": "materialization_rust_compiler",
        }
    ]


def test_materialization_rust_compiler_uses_runtime_schedule_source_tools() -> None:
    schedule = director_runtime_public.query_director_repair_materialization_quality_schedule(
        director_runtime_public.QueryDirectorRepairMaterializationQualityScheduleV1(include_items=True)
    )
    rust_step = next(step for step in schedule.items if step.step_id == "materialization.rust_compiler")

    assert "deterministic_rust_missing_module_file_repair" in rust_step.runtime_source_tools
    assert "deterministic_rust_dependency_repair" in rust_step.runtime_source_tools
    assert "deterministic_rust_post_repair" not in rust_step.runtime_source_tools


def test_materialization_runtime_coverage_detects_rust_line_suggestion() -> None:
    errors = [
        "Artifact quality scan failed: workspace validation command failed (cargo check):\n"
        "error[E0599]: the method `or_insert` exists for enum "
        "`std::collections::btree_map::Entry<'_, FlavorKind, u8>`, but its trait bounds were not satisfied\n"
        "  --> src/models/palette.rs:33:53\n"
        "   |\n"
        "33 |             let entry = intensities.entry(f.kind()).or_insert(0);\n"
        "   |                                                     ^^^^^^^^^ method cannot be called due to "
        "unsatisfied trait bounds\n"
        "   |\n"
        "  ::: src/models/flavor.rs:16:1\n"
        "   |\n"
        "16 | pub enum FlavorKind {\n"
        "   | ------------------- doesn't satisfy `FlavorKind: Ord`\n"
        "help: consider annotating `FlavorKind` with `#[derive(Eq, Ord, PartialEq, PartialOrd)]`\n"
        "  --> src/models/flavor.rs:16:1\n"
        "   |\n"
        "16 + #[derive(Eq, Ord, PartialEq, PartialOrd)]\n"
        "17 | pub enum FlavorKind {\n"
    ]

    assert materialization_quality_runtime_ports.has_materialization_quality_runtime_repair_coverage(errors) is True
    assert (
        materialization_quality_runtime_ports.has_materialization_quality_runtime_repair_coverage(
            ["Artifact quality scan failed: python runtime smoke crashed for 'tests/test_product.py'"]
        )
        is True
    )
    assert (
        materialization_quality_runtime_ports.has_materialization_quality_runtime_repair_coverage(
            ["Artifact quality scan failed: future verifier error without a runtime repair"]
        )
        is False
    )
    javascript_module_error = (
        "Artifact quality scan failed: workspace validation command failed (npm run start): "
        "file:///tmp/project/src/index.js:1\n"
        "SyntaxError: The requested module ./engine/AlchemyEngine.js "
        "does not provide an export named default"
    )
    assert (
        materialization_quality_runtime_ports.has_materialization_quality_runtime_repair_coverage(
            [],
            artifact_quality_issues=artifact_quality_issues_from_errors([javascript_module_error]),
        )
        is True
    )


def test_materialization_public_boundary_ignores_bridge_runner_map_drift(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    runtime_steps = _materialization_runtime_schedule_steps()
    _patch_materialization_runtime_schedule_query(monkeypatch, runtime_steps)
    drifted_runners = dict(materialization_quality_callback_ports._MATERIALIZATION_QUALITY_REPAIR_RUNNERS)
    drifted_runners.pop("materialization.go_import")
    monkeypatch.setattr(
        materialization_quality_callback_ports,
        "_MATERIALIZATION_QUALITY_REPAIR_RUNNERS",
        drifted_runners,
    )

    results, summary = roles_adapters_public_service.run_director_materialization_quality_repair_schedule(
        _FakeAdapter(tmp_path),
        task={"target_files": ["main.go"]},
        task_id="task-materialization-drift",
        artifact_quality_errors=["Go syntax check failed: main.go:3:1: expected declaration"],
    )

    assert results == []
    reconciliation = summary["schedule_reconciliation"]
    assert reconciliation["runtime_step_ids"] == [step.step_id for step in runtime_steps]
    assert reconciliation["runner_step_ids"] == [step.step_id for step in runtime_steps]
    assert reconciliation["exact_match"] is True


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

    _patch_materialization_facade_from_schedule(monkeypatch, fake_schedule_result)

    results, summary = roles_adapters_public_service.run_director_materialization_quality_repair_schedule(
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


def test_materialization_public_schedule_entrypoint_forwards_bridge(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    calls: list[dict[str, Any]] = []
    typed_issue = {
        "source": "artifact_quality",
        "code": "go_syntax_check_failed",
        "message": "Go syntax check failed",
        "path": "main.go",
        "severity": "error",
        "metadata": {"source": "typed_artifact_quality_issue"},
    }

    def fake_plan_probe(adapter: Any, **kwargs: Any) -> dict[str, Any]:
        assert adapter is not None
        assert kwargs["task"] == {"target_files": ["main.go"]}
        assert kwargs["artifact_quality_errors"] == ("Go syntax check failed",)
        assert kwargs["artifact_quality_issues"] == (typed_issue,)
        return {
            "schema_version": "director.materialization_quality_plan_probe_preaudit.v1",
            "status": "already_clean",
            "read_only": True,
            "runtime_public_entrypoint": "query_director_repair_materialization_plan_probe",
        }

    def fake_runner_builder(adapter: Any, **kwargs: Any) -> Any:
        calls.append({"adapter": adapter, **kwargs})

        def runner(step: Any) -> list[dict[str, Any]]:
            if step.step_id != "materialization.hygiene_scaffold":
                return []
            return [
                {
                    "tool": "write_file",
                    "success": True,
                    "result": {
                        "ok": True,
                        "source_tool": "deterministic_scaffold_marker_cleanup",
                        "bridge_step_id": step.step_id,
                    },
                }
            ]

        return runner

    monkeypatch.setattr(
        materialization_quality_runtime_ports,
        "build_materialization_quality_step_runner",
        fake_runner_builder,
    )
    monkeypatch.setattr(
        materialization_quality_runtime_ports,
        "project_materialization_quality_plan_probe_preaudit",
        fake_plan_probe,
    )

    adapter = _FakeAdapter(tmp_path)
    typed_result = roles_adapters_public_service.run_director_materialization_quality_repair_schedule_result(
        RunDirectorMaterializationQualityRepairScheduleCommandV1(
            adapter_port=adapter,
            task={"target_files": ["main.go"]},
            task_id="task-materialization-schedule",
            artifact_quality_errors=("Go syntax check failed",),
            artifact_quality_issues=(typed_issue,),
        )
    )
    results = [dict(item) for item in typed_result.tool_results]
    summary = dict(typed_result.summary)

    assert isinstance(typed_result, DirectorMaterializationQualityRepairScheduleResultV1)
    assert len(calls) == 1
    assert calls[0]["adapter"] is adapter
    assert calls[0]["task_id"] == "task-materialization-schedule"
    assert results[0]["tool"] == "write_file"
    public_boundary = summary["public_boundary"]
    assert public_boundary["mode"] == "runtime_owned_schedule_public_boundary"
    assert public_boundary["runtime_facade_entrypoint"] == "run_director_materialization_quality_repair_facade"
    assert public_boundary["typed_contract"] == "RunDirectorMaterializationQualityRepairScheduleCommandV1"
    assert public_boundary["typed_result"] == "DirectorMaterializationQualityRepairScheduleResultV1"
    assert summary["runtime_materialization_facade"]["owner_cell"] == "director.runtime"

    tuple_results, tuple_summary = roles_adapters_public_service.run_director_materialization_quality_repair_schedule(
        adapter,
        task={"target_files": ["main.go"]},
        task_id="task-materialization-schedule",
        artifact_quality_errors=["Go syntax check failed"],
        artifact_quality_issues=(typed_issue,),
    )

    assert len(calls) == 2
    assert tuple_results == results
    assert tuple_summary["public_boundary"] == public_boundary
    assert "migration_only_compatibility_layer" not in public_boundary
    assert not hasattr(roles_adapters_public_service, "apply_deterministic_materialization_quality_repairs")


def test_runtime_bridge_imports_only_public_director_runtime_surface() -> None:
    bridge_path = Path(__file__).parent.parent / "internal" / "director" / "runtime_repair_tool_adapter.py"
    source = bridge_path.read_text(encoding="utf-8")

    assert "polaris.cells.director.runtime.public import" in source
    assert "polaris.cells.director.runtime.public.service" not in source
    assert "polaris.cells.director.runtime.internal" not in source


def test_deterministic_repairs_directory_no_longer_hosts_runtime_bridge() -> None:
    director_dir = Path(__file__).parent.parent / "internal" / "director"
    deterministic_repairs_dir = director_dir / "deterministic_repairs"

    assert not (deterministic_repairs_dir / "_runtime_bridge.py").exists()
    for path in deterministic_repairs_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "._runtime_bridge" not in source
        assert ".deterministic_repairs._runtime_bridge" not in source


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
    assert "legacy_callback_debt" not in repr(summary)
    assert "legacy_aggregate" not in repr(summary)
    assert "adapter_projection_debt" not in summary
    assert "adapter_projection_debt" not in summary["repair_kernel"]
    assert "adapter_projection_debt" in summary["scheduler_bridge"]
    assert migration_debt["rust_typed_receipt_remaining_source_tools"] == []
    assert migration_debt["adapter_projection_debt"]["rust_typed_receipt_remaining_source_tools"] == []
    assert migration_debt["rust_typed_receipt_remaining_subcases"] == []
    assert migration_debt["rust_typed_receipt_runtime_migrated_subcases"] == [
        "deterministic_rust_lib_root_facade_repair:export_or_module_declaration",
        "deterministic_rust_lib_root_facade_repair:path_rewrite",
        "deterministic_rust_missing_fields_repair:field_declaration",
    ]
    assert migration_debt["rust_typed_receipt_blocked_source_tools"] == []
    assert migration_debt["rust_typed_receipt_blocked_migrated_source_tools"] == []
    assert migration_debt["rust_typed_receipt_remaining_source_tool_count"] == 0
    assert migration_debt["rust_typed_receipt_remaining_subcase_count"] == 0
    assert migration_debt["rust_typed_receipt_runtime_migrated_subcase_count"] == 3
    assert migration_debt["rust_typed_receipt_blocked_migrated_source_tool_count"] == 0
    assert migration_debt["rust_typed_receipt_cutover_authoritative"] is True
    assert migration_debt["rust_typed_receipt_cutover_ready"] is True
    assert migration_debt["rust_typed_receipt_cutover_blockers"] == []
    steps = {step["step_id"]: step for step in migration_debt["steps"]}
    cpp_step = steps["cpp.post_execution"]
    assert cpp_step["runtime_executable_source_tools"] == ["deterministic_cpp_include_path_repair"]
    assert cpp_step["adapter_only_source_tools"] == []
    assert cpp_step["write_tool_evidence"] is True
    assert cpp_step["verifier_evidence_required"] is True
    assert cpp_step["verifier_evidence_present"] is False
    assert "missing_verifier_evidence" in cpp_step["blockers"]
    assert "convergence_verifier_not_provided" in cpp_step["blockers"]

    java_step = steps["java.post_execution"]
    assert java_step["runtime_executable_source_tools"] == ["deterministic_java_post_repair"]
    assert java_step["adapter_only_source_tools"] == []
    assert "adapter_projection_record_requires_revalidation" in java_step["blockers"]
    adapter_projection_payload = next(
        item["result"] for item in tool_results if item["result"]["source_tool"] == "deterministic_java_post_repair"
    )
    assert adapter_projection_payload["repair_kernel"]["owner_cell"] == "roles.adapters.strategy_host"
    assert adapter_projection_payload["repair_kernel"]["authoritative"] is False
    assert adapter_projection_payload["repair_kernel"]["requires_revalidation"] is True
    assert migration_debt["adapter_projection_debt"]["adapter_only_step_count"] == 0


def test_go_post_execution_uses_runtime_source_tool_sequence_without_adapter_aggregate(
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


def test_post_execution_rust_migration_debt_uses_typed_receipt_gap_names(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    class FakeAdapter:
        def __init__(self) -> None:
            self.workspace = str(tmp_path)

    def no_repairs(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    def fake_rust_post_repairs(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            post_execution_repair_bridge._record_to_tool_result(
                {"file": "src/lib.rs", "action": "rust_missing_fields"},
                source_tool="deterministic_rust_missing_fields_repair",
                default_action="rust_missing_fields",
            )
        ]

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
    summary_text = repr(summary)
    assert "shadow_replay" not in summary_text
    assert "legacy_shadow" not in summary_text

    evidence = summary["rust_typed_receipt_cutover_evidence"]
    assert evidence["typed_receipt_cutover_authoritative"] is False
    assert evidence["cutover_ready"] is False
    assert evidence["remaining_source_tool_count"] == 1
    assert evidence["remaining_source_tools_without_runtime_receipt"] == ["deterministic_rust_missing_fields_repair"]
    assert evidence["remaining_adapter_subcase_count"] == 1
    assert evidence["runtime_migrated_subcase_count"] == 2
    assert "typed_receipt_cutover_not_authoritative" in evidence["cutover_blockers"]

    repair_kernel = summary["repair_kernel"]
    assert repair_kernel["rust_typed_receipt_cutover_authoritative"] is False
    assert repair_kernel["rust_typed_receipt_cutover_ready"] is False
    assert repair_kernel["rust_typed_receipt_remaining_source_tool_count"] == 1
    assert repair_kernel["rust_typed_receipt_remaining_subcase_count"] == 1
    assert repair_kernel["rust_typed_receipt_runtime_migrated_subcase_count"] == 2
    assert repair_kernel["rust_typed_receipt_blocked_migrated_source_tool_count"] == 0
    assert repair_kernel["rust_typed_receipt_cutover_blockers"] == evidence["cutover_blockers"]

    migration_debt = _runtime_ports_diagnostics(summary)["repair_kernel_migration_debt"]
    assert migration_debt["rust_typed_receipt_cutover_ready"] is False
    assert migration_debt["rust_typed_receipt_remaining_source_tool_count"] == 1
    assert migration_debt["rust_typed_receipt_source_tools_without_runtime_receipt"] == [
        "deterministic_rust_missing_fields_repair"
    ]
    assert migration_debt["rust_typed_receipt_remaining_subcase_count"] == 1
    assert migration_debt["rust_typed_receipt_runtime_migrated_subcase_count"] == 2
    assert migration_debt["rust_typed_receipt_blocked_migrated_source_tool_count"] == 0
    assert migration_debt["adapter_projection_debt"]["rust_typed_receipt_cutover_ready"] is False
    rust_step = {item["step_id"]: item for item in migration_debt["steps"]}["rust.post_execution_convergence"]
    assert rust_step["rust_typed_receipt_cutover_ready"] is False
    assert rust_step["rust_typed_receipt_remaining_source_tool_count"] == 1
    assert rust_step["rust_typed_receipt_remaining_subcase_count"] == 1
    assert rust_step["rust_typed_receipt_runtime_migrated_subcase_count"] == 2
    assert rust_step["rust_typed_receipt_blocked_migrated_source_tool_count"] == 0

    scheduler_bridge = summary["scheduler_bridge"]
    assert scheduler_bridge["rust_typed_receipt_cutover_authoritative"] is False
    assert scheduler_bridge["rust_typed_receipt_cutover_ready"] is False
    assert scheduler_bridge["rust_typed_receipt_cutover_blockers"] == evidence["cutover_blockers"]


def test_post_execution_migration_debt_marks_runtime_verifier_evidence_without_adapter_cutover(
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
    assert java_step["adapter_only_source_tools"] == []
    assert java_step["verifier_evidence_present"] is False
    assert java_step["cutover_ready"] is False
    assert "adapter_only_source_tools_present" not in java_step["blockers"]
    assert "adapter_projection_record_requires_revalidation" in java_step["blockers"]


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
                        "receipt_authority": "non_authoritative_adapter_projection",
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
                            "receipt_authority": "non_authoritative_adapter_projection",
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
                    "adapter_projection_bridge": True,
                    "adapter_callback_bridge": False,
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
    assert scheduler_bridge["adapter_receipt_projection_count"] == 3
    assert scheduler_bridge["callback_receipt_projection_count"] == 3
    assert scheduler_bridge["adapter_receipts_authoritative"] is False
    assert scheduler_bridge["callback_receipts_authoritative"] is False
    assert scheduler_bridge["adapter_receipt_authority_values"] == ["non_authoritative_adapter_projection"]
    assert scheduler_bridge["callback_receipt_authority_values"] == ["non_authoritative_adapter_projection"]
    assert scheduler_bridge["adapter_receipts_with_revalidation"] == 1
    assert scheduler_bridge["callback_receipts_with_revalidation"] == 1
    assert scheduler_bridge["typed_receipt_path_available"] is False
    assert scheduler_bridge["adapter_projection_claimed_typed_receipt_path_count"] == 0
    assert scheduler_bridge["callback_projection_claimed_typed_receipt_path_count"] == 0
    assert (
        scheduler_bridge["migration_blocker"]
        == "adapter schedule runners still return tool_results instead of RepairReceipt"
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
        assert max_rounds == 1
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
    assert scheduler_bridge["adapter_receipt_projection_count"] == 1
    assert scheduler_bridge["callback_receipt_projection_count"] == 1
    assert scheduler_bridge["adapter_receipts_authoritative"] is False
    assert scheduler_bridge["callback_receipts_authoritative"] is False
    assert scheduler_bridge["adapter_receipts_with_revalidation"] == 1
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

    step = materialization_quality_runtime_ports.DirectorRepairMaterializationQualityStepV1(
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
            else materialization_quality_runtime_ports.DirectorRepairMaterializationQualityStepV1(
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

    _patch_materialization_facade_from_schedule(monkeypatch, fake_schedule_result)

    _, summary = roles_adapters_public_service.run_director_materialization_quality_repair_schedule(
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
    assert scheduler_bridge["adapter_projection_only_count"] == 1
    assert scheduler_bridge["callback_projection_only_count"] == 1
    assert scheduler_bridge["adapter_authoritative_receipt_count"] == 0
    assert scheduler_bridge["callback_authoritative_receipt_count"] == 0
    assert scheduler_bridge["adapter_receipt_authority_values"] == ["non_authoritative_adapter_projection"]
    assert scheduler_bridge["callback_receipt_authority_values"] == ["non_authoritative_adapter_projection"]
    assert scheduler_bridge["adapter_projection_claimed_typed_receipt_path_count"] == 1
    assert scheduler_bridge["callback_projection_claimed_typed_receipt_path_count"] == 1
    assert scheduler_bridge.get("projection_only", True) is True
    assert scheduler_bridge["remaining_adapter_projection_only_step_ids"] == ["materialization.go_import"]
    assert scheduler_bridge["remaining_callback_only_step_ids"] == ["materialization.go_import"]
    assert scheduler_bridge["adapter_projection_only_step_count"] == 1
    assert scheduler_bridge["callback_only_step_count"] == 1
    go_lifecycle = scheduler_bridge["receipt_lifecycle_by_step"]["materialization.go_import"]
    assert go_lifecycle["typed_receipt_path_available"] is False
    assert go_lifecycle["authoritative_receipts_allowed"] is False
    assert go_lifecycle["native_receipt_present"] is False
    assert go_lifecycle["adapter_projection_present"] is True
    assert go_lifecycle["callback_projection_present"] is True
    assert go_lifecycle["adapter_projection_only"] is True
    assert go_lifecycle["callback_only"] is True
    assert go_lifecycle["projection_only"] is True
    assert go_lifecycle["verifier_evidence_present"] is False
    assert go_lifecycle["native_verifier_evidence_present"] is False
    assert go_lifecycle["adapter_verifier_evidence_present"] is False
    assert go_lifecycle["callback_verifier_evidence_present"] is False
    assert go_lifecycle["native_repair_kernel_receipt_count"] == 0
    assert go_lifecycle["adapter_receipt_projection_count"] == 1
    assert go_lifecycle["callback_receipt_projection_count"] == 1
    assert go_lifecycle["adapter_projection_only_count"] == 1
    assert go_lifecycle["callback_projection_only_count"] == 1
    assert go_lifecycle["adapter_receipt_evidence_status_counts"] == {"missing_evidence": 1}
    assert go_lifecycle["callback_receipt_evidence_status_counts"] == {"missing_evidence": 1}
    assert go_lifecycle["receipt_lifecycle_evidence_status"] == "missing_evidence"
    assert "adapter_projection_only" in go_lifecycle["cutover_blockers"]
    assert "missing_native_repair_receipt" in go_lifecycle["cutover_blockers"]
    migration_debt = _runtime_ports_diagnostics(summary)["repair_kernel_migration_debt"]
    assert migration_debt["remaining_adapter_projection_only_step_ids"] == ["materialization.go_import"]
    assert migration_debt["remaining_callback_only_step_ids"] == ["materialization.go_import"]
    assert migration_debt["adapter_projection_only_step_count"] == 1
    assert migration_debt["callback_only_step_count"] == 1
    go_debt = {item["step_id"]: item for item in migration_debt["adapter_projection_debt"]}["materialization.go_import"]
    assert go_debt["native_receipt_present"] is False
    assert go_debt["adapter_projection_present"] is True
    assert go_debt["callback_projection_present"] is True
    assert go_debt["adapter_projection_only"] is True
    assert go_debt["callback_only"] is True
    assert go_debt["projection_only"] is True
    assert go_debt["authoritative_receipts_allowed"] is False
    assert go_debt["cutover_ready"] is False
    assert go_debt["verifier_evidence_present"] is False
    assert go_debt["native_verifier_evidence_present"] is False
    assert go_debt["adapter_verifier_evidence_present"] is False
    assert go_debt["callback_verifier_evidence_present"] is False
    assert "adapter_projection_only" in go_debt["cutover_blockers"]
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
            materialization_quality_runtime_ports.DirectorRepairMaterializationQualityStepV1(
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

    _patch_materialization_facade_from_schedule(monkeypatch, fake_schedule_result)

    _, summary = roles_adapters_public_service.run_director_materialization_quality_repair_schedule(
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
    assert scheduler_bridge["adapter_receipt_projection_count"] == 1
    assert scheduler_bridge["callback_receipt_projection_count"] == 1
    assert scheduler_bridge["adapter_projection_only_count"] == 1
    assert scheduler_bridge["callback_projection_only_count"] == 1
    assert scheduler_bridge["adapter_authoritative_receipt_count"] == 0
    assert scheduler_bridge["callback_authoritative_receipt_count"] == 0
    assert scheduler_bridge["adapter_receipts_authoritative"] is False
    assert scheduler_bridge["callback_receipts_authoritative"] is False
    assert scheduler_bridge["authoritative_receipts_allowed"] is False
    assert scheduler_bridge["remaining_callback_only_step_ids"] == []
    assert scheduler_bridge["callback_only_step_count"] == 0
    assert scheduler_bridge["native_receipt_step_ids"] == ["materialization.go_import"]
    assert scheduler_bridge["adapter_projection_step_ids"] == ["materialization.go_import"]
    assert scheduler_bridge["callback_projection_step_ids"] == ["materialization.go_import"]
    assert scheduler_bridge["native_receipt_evidence_status_counts"] == {"resolved_evidence": 1}
    assert scheduler_bridge["adapter_receipt_evidence_status_counts"] == {"missing_evidence": 1}
    assert scheduler_bridge["callback_receipt_evidence_status_counts"] == {"missing_evidence": 1}

    go_lifecycle = scheduler_bridge["receipt_lifecycle_by_step"]["materialization.go_import"]
    assert go_lifecycle["typed_receipt_path_available"] is True
    assert go_lifecycle["authoritative_receipts_allowed"] is False
    assert go_lifecycle["native_receipt_present"] is True
    assert go_lifecycle["adapter_projection_present"] is True
    assert go_lifecycle["callback_projection_present"] is True
    assert go_lifecycle["adapter_projection_only"] is False
    assert go_lifecycle["callback_only"] is False
    assert go_lifecycle["projection_only"] is False
    assert go_lifecycle["verifier_evidence_present"] is True
    assert go_lifecycle["native_verifier_evidence_present"] is True
    assert go_lifecycle["adapter_verifier_evidence_present"] is False
    assert go_lifecycle["callback_verifier_evidence_present"] is False
    assert go_lifecycle["native_repair_kernel_receipt_count"] == 1
    assert go_lifecycle["adapter_receipt_projection_count"] == 1
    assert go_lifecycle["callback_receipt_projection_count"] == 1
    assert go_lifecycle["native_receipt_evidence_status_counts"] == {"resolved_evidence": 1}
    assert go_lifecycle["adapter_receipt_evidence_status_counts"] == {"missing_evidence": 1}
    assert go_lifecycle["callback_receipt_evidence_status_counts"] == {"missing_evidence": 1}
    assert go_lifecycle["receipt_lifecycle_evidence_status_counts"] == {
        "missing_evidence": 1,
        "resolved_evidence": 1,
    }
    assert go_lifecycle["receipt_lifecycle_evidence_status"] == "missing_evidence"
    assert "adapter_projection_only" in go_lifecycle["cutover_blockers"]

    step_summary = summary["materialization_quality_step_summaries"]["materialization.go_import"]
    assert step_summary["native_repair_kernel_receipt_count"] == 1
    assert step_summary["callback_receipt_projection_count"] == 1
    assert step_summary["receipt_lifecycle_evidence_status"] == "missing_evidence"

    migration_debt = _runtime_ports_diagnostics(summary)["repair_kernel_migration_debt"]
    assert migration_debt["native_receipt_step_ids"] == ["materialization.go_import"]
    assert migration_debt["adapter_projection_step_ids"] == ["materialization.go_import"]
    assert migration_debt["callback_projection_step_ids"] == ["materialization.go_import"]
    assert migration_debt["remaining_adapter_projection_only_step_ids"] == []
    assert migration_debt["remaining_callback_only_step_ids"] == []
    assert migration_debt["callback_only_step_count"] == 0
    go_debt = {item["step_id"]: item for item in migration_debt["adapter_projection_debt"]}["materialization.go_import"]
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
            materialization_quality_runtime_ports.DirectorRepairMaterializationQualityStepV1(
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

    _patch_materialization_facade_from_schedule(monkeypatch, fake_schedule_result)

    _, summary = roles_adapters_public_service.run_director_materialization_quality_repair_schedule(
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
    assert _runtime_ports_diagnostics(summary)["repair_kernel_migration_debt"]["cutover_ready"] is False
