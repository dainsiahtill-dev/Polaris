"""Tests for the runtime-owned repair convergence envelope."""

from __future__ import annotations

from pathlib import Path

from polaris.cells.director.runtime.internal.repair_kernel.contracts import (
    RepairDiagnostic,
    RepairOperation,
    RepairPlan,
)
from polaris.cells.director.runtime.internal.repair_kernel.runtime_dispatch import (
    run_runtime_repair_convergence,
)
from polaris.cells.director.runtime.internal.repair_kernel.schedule_catalog import (
    run_materialization_quality_repair_schedule_callbacks,
    run_post_execution_repair_schedule_callbacks,
)
from polaris.cells.director.runtime.internal.repair_kernel.scheduler import (
    CONVERGENCE_PIPELINE_ORDER,
    CONVERGENCE_PIPELINE_STAGES,
    RepairVerifierSnapshot,
)
from polaris.cells.director.runtime.public import (
    DirectorRepairCallbackReceiptProjectionV1,
    DirectorRepairMaterializationQualityScheduleRunResultV1,
    DirectorRepairPostExecutionScheduleRunResultV1,
    run_director_materialization_quality_repair_schedule,
    run_director_materialization_quality_repair_schedule_result,
    run_director_post_execution_repair_schedule,
    run_director_post_execution_repair_schedule_result,
)


def test_runtime_repair_convergence_runs_two_rounds_with_typed_receipts(tmp_path: Path) -> None:
    relative_path = "src/app.ts"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text("export const pending = true;\n", encoding="utf-8")
    first_diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="fake_missing_step_one",
        message="step one is missing",
        path=relative_path,
        raw="src/app.ts: step one is missing",
    )
    second_diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="fake_missing_step_two",
        message="step two is missing",
        path=relative_path,
        raw="src/app.ts: step two is missing",
    )

    def verifier(round_number: int, receipts: tuple[object, ...]) -> RepairVerifierSnapshot:
        del receipts
        current = target.read_text(encoding="utf-8")
        diagnostics: tuple[RepairDiagnostic, ...]
        if "step_one" not in current:
            diagnostics = (first_diagnostic,)
        elif "step_two" not in current:
            diagnostics = (second_diagnostic,)
        else:
            diagnostics = ()
        return RepairVerifierSnapshot(
            diagnostics=diagnostics,
            command=("fake-verifier",),
            exit_code=0 if not diagnostics else 1,
            raw_output_ref=f"runtime/verifier/fake-round-{round_number}.log",
        )

    def planner(diagnostics: tuple[RepairDiagnostic, ...], round_number: int) -> tuple[RepairPlan, ...]:
        if not diagnostics:
            return ()
        diagnostic = diagnostics[0]
        if diagnostic.code == "fake_missing_step_one":
            content = "export const step_one = true;\n"
            rule_id = "fake.step_one"
        else:
            content = "export const step_one = true;\nexport const step_two = true;\n"
            rule_id = "fake.step_two"
        return (
            RepairPlan(
                rule_id=rule_id,
                source_tool="deterministic_fake_runtime_convergence_repair",
                diagnostics=diagnostics,
                operations=(RepairOperation(kind="write_file", path=relative_path, content=content),),
                priority=round_number,
            ),
        )

    def base_files_provider(plan: RepairPlan) -> dict[str, str]:
        del plan
        return {relative_path: target.read_text(encoding="utf-8")}

    def writer(path: str, content: str) -> dict[str, object]:
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True, "file": path}

    result = run_runtime_repair_convergence(
        source_tools=("deterministic_fake_runtime_convergence_repair",),
        workspace=tmp_path,
        base_files={relative_path: target.read_text(encoding="utf-8")},
        artifact_quality_errors=(first_diagnostic.raw,),
        verifier=verifier,
        planner=planner,
        base_files_provider=base_files_provider,
        writer=writer,
        allowed_paths=(relative_path,),
        max_rounds=3,
    )

    assert result.status == "converged"
    assert result.converged is True
    assert [round_result.round_number for round_result in result.rounds] == [1, 2]
    assert [receipt.round_number for receipt in result.receipts] == [1, 2]
    assert all(receipt.revalidation_evidence is not None for receipt in result.receipts)
    assert [receipt.evidence_status for receipt in result.receipts] == ["failed_evidence", "resolved_evidence"]
    assert result.receipts[0].revalidation_evidence is not None
    assert result.receipts[0].revalidation_evidence.evidence_status == "failed_evidence"
    assert result.receipts[0].revalidation_evidence.raw_output_ref == "runtime/verifier/fake-round-1.log"
    assert result.receipts[1].revalidation_evidence is not None
    assert result.receipts[1].revalidation_evidence.evidence_status == "resolved_evidence"
    assert result.receipts[1].revalidation_evidence.raw_output_ref == "runtime/verifier/fake-round-2.log"
    assert result.metadata["typed_receipt_path_available"] is True
    assert result.metadata["planner_override"] is True
    assert result.metadata["canonical_convergence_executor"] == "RepairConvergenceScheduler"
    assert result.metadata["typed_convergence_scheduler_active"] is True
    assert result.metadata["pipeline"] == list(CONVERGENCE_PIPELINE_STAGES)
    assert result.metadata["pipeline_order"] == CONVERGENCE_PIPELINE_ORDER
    assert result.metadata["coverage_stage_required"] is True
    assert result.metadata["coverage_before_plan_required"] is True
    assert result.metadata["policy_before_execute_required"] is True
    assert result.metadata["revalidation_receipt_binding_required"] is True
    assert result.metadata["hidden_language_loop_allowed"] is False
    assert result.metadata["language_self_loop_allowed"] is False
    assert target.read_text(encoding="utf-8") == "export const step_one = true;\nexport const step_two = true;\n"


def test_runtime_repair_convergence_reports_no_plans_as_stuck(tmp_path: Path) -> None:
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="fake_unplanned",
        message="no runtime plan exists",
        path="src/app.ts",
        raw="src/app.ts: no runtime plan exists",
    )

    def verifier(round_number: int, receipts: tuple[object, ...]) -> RepairVerifierSnapshot:
        del round_number, receipts
        return RepairVerifierSnapshot(diagnostics=(diagnostic,), command=("fake-verifier",), exit_code=1)

    result = run_runtime_repair_convergence(
        source_tools=("deterministic_fake_unplanned_repair",),
        workspace=tmp_path,
        base_files={"src/app.ts": "broken\n"},
        artifact_quality_errors=(diagnostic.raw,),
        verifier=verifier,
        planner=lambda _diagnostics, _round_number: (),
        base_files_provider=lambda _plan: {"src/app.ts": "broken\n"},
        max_rounds=2,
    )

    assert result.status == "stuck_no_plans"
    assert result.converged is False
    assert result.receipts == ()
    assert result.metadata["stopped_reason"] == "planner_returned_no_plans"
    assert result.metadata["convergence_scheduler_required"] is True


def test_runtime_repair_convergence_marks_failed_revalidation_and_max_rounds(tmp_path: Path) -> None:
    relative_path = "src/app.ts"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text("export const pending = true;\n", encoding="utf-8")
    before_diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="fake_before",
        message="initial failure",
        path=relative_path,
        raw="src/app.ts: initial failure",
    )
    residual_diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="fake_residual",
        message="residual failure",
        path=relative_path,
        raw="src/app.ts: residual failure",
    )

    def verifier(round_number: int, receipts: tuple[object, ...]) -> RepairVerifierSnapshot:
        del receipts
        diagnostics = (before_diagnostic,) if round_number == 0 else (residual_diagnostic,)
        return RepairVerifierSnapshot(diagnostics=diagnostics, command=("fake-verifier",), exit_code=1)

    def planner(diagnostics: tuple[RepairDiagnostic, ...], round_number: int) -> tuple[RepairPlan, ...]:
        del round_number
        return (
            RepairPlan(
                rule_id="fake.incomplete",
                source_tool="deterministic_fake_incomplete_repair",
                diagnostics=diagnostics,
                operations=(
                    RepairOperation(
                        kind="write_file",
                        path=relative_path,
                        content="export const still_pending = true;\n",
                    ),
                ),
            ),
        )

    def writer(path: str, content: str) -> dict[str, object]:
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True, "file": path}

    result = run_runtime_repair_convergence(
        source_tools=("deterministic_fake_incomplete_repair",),
        workspace=tmp_path,
        base_files={relative_path: target.read_text(encoding="utf-8")},
        artifact_quality_errors=(before_diagnostic.raw,),
        verifier=verifier,
        planner=planner,
        base_files_provider=lambda _plan: {relative_path: target.read_text(encoding="utf-8")},
        writer=writer,
        allowed_paths=(relative_path,),
        max_rounds=1,
    )

    assert result.status == "max_rounds_exhausted"
    assert result.converged is False
    assert result.metadata["failed_revalidation_receipt_count"] == 1
    assert result.metadata["unconverged"] is True
    assert result.receipts[0].status == "failed_revalidation"
    assert result.receipts[0].authoritative is False
    assert result.receipts[0].evidence_status == "failed_evidence"
    assert result.receipts[0].revalidation_evidence is not None
    assert result.receipts[0].revalidation_evidence.evidence_status == "failed_evidence"
    assert result.receipts[0].revalidation_evidence.residual_diagnostic_ids == ()
    assert result.final_diagnostics == (residual_diagnostic,)


def test_runtime_repair_convergence_breaks_repeated_diagnostic_cycles(tmp_path: Path) -> None:
    relative_path = "src/app.ts"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text("export const pending = true;\n", encoding="utf-8")
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="fake_cycle",
        message="same failure remains",
        path=relative_path,
        raw="src/app.ts: same failure remains",
    )

    def verifier(round_number: int, receipts: tuple[object, ...]) -> RepairVerifierSnapshot:
        del round_number, receipts
        return RepairVerifierSnapshot(diagnostics=(diagnostic,), command=("fake-verifier",), exit_code=1)

    def planner(diagnostics: tuple[RepairDiagnostic, ...], round_number: int) -> tuple[RepairPlan, ...]:
        del round_number
        return (
            RepairPlan(
                rule_id="fake.cycle",
                source_tool="deterministic_fake_cycle_repair",
                diagnostics=diagnostics,
                operations=(
                    RepairOperation(
                        kind="write_file",
                        path=relative_path,
                        content="export const still_pending = true;\n",
                    ),
                ),
            ),
        )

    def writer(path: str, content: str) -> dict[str, object]:
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True, "file": path}

    result = run_runtime_repair_convergence(
        source_tools=("deterministic_fake_cycle_repair",),
        workspace=tmp_path,
        base_files={relative_path: target.read_text(encoding="utf-8")},
        artifact_quality_errors=(diagnostic.raw,),
        verifier=verifier,
        planner=planner,
        base_files_provider=lambda _plan: {relative_path: target.read_text(encoding="utf-8")},
        writer=writer,
        allowed_paths=(relative_path,),
        max_rounds=3,
    )

    assert result.status == "cycle_detected"
    assert result.metadata["stopped_reason"] == "repeated_diagnostic_signature"
    assert result.metadata["failed_revalidation_receipt_count"] == 1
    assert result.receipts[0].status == "failed_revalidation"
    assert result.receipts[0].evidence_status == "failed_evidence"


def test_callback_schedule_to_dict_and_annotations_mark_migration_envelope() -> None:
    runner_step_ids = (
        "go.module_import",
        "rust.dependency_resolution",
        "rust.post_execution_convergence",
        "cpp.post_execution",
        "java.post_execution",
    )

    def runner(step) -> list[dict[str, object]]:
        if step.step_id != "rust.dependency_resolution":
            return []
        return [
            {
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "source_tool": "deterministic_rust_dependency_repair",
                    "file": "Cargo.toml",
                    "after_hash": "hash-one",
                    "revalidation": {
                        "exit_code": 0,
                        "residual_diagnostic_ids": [],
                    },
                },
            }
        ]

    run = run_post_execution_repair_schedule_callbacks(
        runner_step_ids=runner_step_ids,
        runner=runner,
    )
    payload = run.tool_results[0]["result"]
    run_dict = run.to_dict()
    summary = run_dict["summary"]
    receipt_projection = run_dict["receipt_projections"][0]

    assert summary["callback_migration_envelope"] is True
    assert summary["typed_receipt_path_available"] is False
    assert summary["convergence_scheduler_required"] is True
    assert summary["canonical_convergence_executor"] == "RepairConvergenceScheduler"
    assert summary["typed_convergence_scheduler_active"] is False
    assert summary["pipeline_order"] == CONVERGENCE_PIPELINE_ORDER
    assert summary["hidden_language_loop_allowed"] is False
    assert summary["language_self_loop_allowed"] is False
    assert summary["callback_bridge_uses_repair_convergence_scheduler"] is False
    assert summary["typed_convergence_scheduler_cutover_required"] is True
    assert summary["callback_runner_self_loop_allowed"] is False
    assert summary["bounded_round_accounting_visible"] is True
    assert summary["round_accounting_fields"] == [
        "max_rounds",
        "rounds_run",
        "convergence_status",
        "stopped_reason",
    ]
    assert summary["produces_tool_results_only"] is True
    assert summary["final_typed_receipt_path"] == "run_runtime_repair_convergence"
    assert summary["receipt_projection_count"] == 1
    assert summary["native_receipt_count"] == 0
    assert summary["post_check_evidence_complete"] is False
    assert summary["native_post_check_evidence_complete"] is False
    assert summary["missing_native_revalidation_evidence"] is True
    assert summary["non_authoritative_projection"] is True
    assert summary["cutover_ready"] is False
    assert summary["cutover_blockers"] == [
        "missing_native_revalidation_evidence",
        "callback_projection_not_authoritative_receipt",
    ]
    assert summary["evidence_status_counts"]["resolved_evidence"] == 1
    assert summary["evidence_status_counts"]["missing_evidence"] == 0
    assert summary["resolved_evidence_projection_source_tools"] == ["deterministic_rust_dependency_repair"]
    assert summary["resolved_evidence_receipt_ids"] == []
    assert payload["callback_migration_envelope"] is True
    assert payload["typed_receipt_path_available"] is False
    assert payload["canonical_convergence_executor"] == "RepairConvergenceScheduler"
    assert payload["pipeline_order"] == CONVERGENCE_PIPELINE_ORDER
    assert payload["hidden_language_loop_allowed"] is False
    assert payload["language_self_loop_allowed"] is False
    assert payload["callback_runner_self_loop_allowed"] is False
    assert payload["typed_convergence_scheduler_cutover_required"] is True
    assert payload["bounded_round_accounting_visible"] is True
    assert payload["preferred_typed_receipt_entrypoint"] == "run_runtime_repair_convergence"
    assert payload["callback_receipt_projection_available"] is True
    assert receipt_projection["schema_version"] == "director.repair_callback_receipt_projection.v1"
    assert receipt_projection["receipt_authority"] == "non_authoritative_callback_projection"
    assert receipt_projection["schedule_kind"] == "post_execution"
    assert receipt_projection["step_id"] == "rust.dependency_resolution"
    assert receipt_projection["source_tool"] == "deterministic_rust_dependency_repair"
    assert receipt_projection["round_number"] == 1
    assert receipt_projection["tool_name"] == "write_file"
    assert receipt_projection["touched_path"] == "Cargo.toml"
    assert receipt_projection["convergence_status"] == "max_rounds_reached"
    assert receipt_projection["convergence_stopped_reason"] == "max_rounds_reached"
    assert receipt_projection["scheduler_rounds_run"] == 1
    assert receipt_projection["max_rounds"] == 1
    assert receipt_projection["projection_only"] is True
    assert receipt_projection["typed_receipt_path_available"] is False
    assert receipt_projection["authoritative"] is False
    assert receipt_projection["canonical_convergence_executor"] == "RepairConvergenceScheduler"
    assert receipt_projection["pipeline_order"] == CONVERGENCE_PIPELINE_ORDER
    assert receipt_projection["hidden_language_loop_allowed"] is False
    assert receipt_projection["language_self_loop_allowed"] is False
    assert receipt_projection["callback_runner_self_loop_allowed"] is False
    assert receipt_projection["typed_convergence_scheduler_cutover_required"] is True
    assert receipt_projection["preferred_typed_receipt_entrypoint"] == "run_runtime_repair_convergence"
    assert (
        receipt_projection["migration_blocker"] == "callback runners still return tool_results instead of RepairReceipt"
    )
    assert receipt_projection["revalidation_evidence_present"] is True
    assert receipt_projection["revalidation_exit_code"] == 0
    assert receipt_projection["revalidation_residual_count"] == 0


def test_materialization_callback_schedule_to_dict_projects_non_authoritative_receipts() -> None:
    runner_step_ids = (
        "materialization.hygiene_scaffold",
        "materialization.typescript_scaffold",
        "materialization.typescript_compiler",
        "materialization.node_manifest",
        "materialization.rust_compiler",
        "materialization.target_runtime",
        "materialization.python_import",
        "materialization.go_import",
    )

    def runner(step) -> list[dict[str, object]]:
        if step.step_id != "materialization.typescript_compiler":
            return []
        return [
            {
                "tool_name": "edit_file",
                "success": True,
                "result": {
                    "source_tool": "deterministic_typescript_materialization_repair",
                    "file": "src/main.ts",
                    "after_hash": "hash-two",
                },
            }
        ]

    run = run_materialization_quality_repair_schedule_callbacks(
        runner_step_ids=runner_step_ids,
        runner=runner,
    )
    run_dict = run.to_dict()
    summary = run_dict["summary"]
    receipt_projection = run_dict["receipt_projections"][0]

    assert summary["schedule_kind"] == "materialization_quality"
    assert summary["canonical_convergence_executor"] == "RepairConvergenceScheduler"
    assert summary["typed_convergence_scheduler_active"] is False
    assert summary["pipeline_order"] == CONVERGENCE_PIPELINE_ORDER
    assert summary["hidden_language_loop_allowed"] is False
    assert summary["callback_bridge_uses_repair_convergence_scheduler"] is False
    assert summary["typed_convergence_scheduler_cutover_required"] is True
    assert summary["callback_runner_self_loop_allowed"] is False
    assert summary["receipt_projection_count"] == 1
    assert summary["post_check_evidence_complete"] is False
    assert summary["missing_native_revalidation_evidence"] is True
    assert summary["non_authoritative_projection"] is True
    assert summary["cutover_ready"] is False
    assert summary["evidence_status_counts"]["missing_evidence"] == 1
    assert summary["evidence_status_counts"]["failed_evidence"] == 0
    assert summary["missing_evidence_projection_source_tools"] == ["deterministic_typescript_materialization_repair"]
    assert summary["missing_evidence_receipt_ids"] == []
    assert receipt_projection["schema_version"] == "director.repair_callback_receipt_projection.v1"
    assert receipt_projection["receipt_authority"] == "non_authoritative_callback_projection"
    assert receipt_projection["schedule_kind"] == "materialization_quality"
    assert receipt_projection["step_id"] == "materialization.typescript_compiler"
    assert receipt_projection["source_tool"] == "deterministic_typescript_materialization_repair"
    assert receipt_projection["round_number"] == 1
    assert receipt_projection["tool_name"] == "edit_file"
    assert receipt_projection["touched_path"] == "src/main.ts"
    assert receipt_projection["convergence_status"] == "max_rounds_reached"
    assert receipt_projection["convergence_stopped_reason"] == "max_rounds_reached"
    assert receipt_projection["projection_only"] is True
    assert receipt_projection["typed_receipt_path_available"] is False
    assert receipt_projection["authoritative"] is False
    assert receipt_projection["canonical_convergence_executor"] == "RepairConvergenceScheduler"
    assert receipt_projection["hidden_language_loop_allowed"] is False
    assert receipt_projection["callback_runner_self_loop_allowed"] is False
    assert receipt_projection["typed_convergence_scheduler_cutover_required"] is True
    assert (
        receipt_projection["migration_blocker"] == "callback runners still return tool_results instead of RepairReceipt"
    )


def test_callback_schedule_summary_keeps_failed_projection_evidence_distinct_from_missing() -> None:
    runner_step_ids = (
        "go.module_import",
        "rust.dependency_resolution",
        "rust.post_execution_convergence",
        "cpp.post_execution",
        "java.post_execution",
    )

    def runner(step) -> list[dict[str, object]]:
        if step.step_id != "rust.dependency_resolution":
            return []
        return [
            {
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "source_tool": "deterministic_rust_dependency_repair",
                    "file": "Cargo.toml",
                    "after_hash": "hash-failed",
                    "revalidation": {
                        "command": ["cargo", "check"],
                        "exit_code": 1,
                        "errors_before": 2,
                        "errors_after": 1,
                    },
                },
            }
        ]

    run = run_post_execution_repair_schedule_callbacks(
        runner_step_ids=runner_step_ids,
        runner=runner,
    )
    summary = run.to_dict()["summary"]

    assert summary["post_check_evidence_complete"] is False
    assert summary["missing_native_revalidation_evidence"] is True
    assert summary["non_authoritative_projection"] is True
    assert summary["cutover_ready"] is False
    assert summary["evidence_status_counts"]["failed_evidence"] == 1
    assert summary["evidence_status_counts"]["missing_evidence"] == 0
    assert summary["failed_evidence_projection_source_tools"] == ["deterministic_rust_dependency_repair"]
    assert summary["failed_evidence_receipt_ids"] == []


def test_public_post_execution_schedule_run_result_exposes_summary_and_receipts() -> None:
    runner_step_ids = (
        "go.module_import",
        "rust.dependency_resolution",
        "rust.post_execution_convergence",
        "cpp.post_execution",
        "java.post_execution",
    )

    def runner(step) -> list[dict[str, object]]:
        if step.step_id != "rust.dependency_resolution":
            return []
        return [
            {
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "source_tool": "deterministic_rust_dependency_repair",
                    "file": "Cargo.toml",
                    "after_hash": "hash-public",
                },
            }
        ]

    result = run_director_post_execution_repair_schedule_result(
        runner_step_ids=runner_step_ids,
        runner=runner,
    )
    payload = result.to_dict()
    legacy_tool_results, legacy_ordered_steps = run_director_post_execution_repair_schedule(
        runner_step_ids=runner_step_ids,
        runner=runner,
    )

    assert isinstance(result, DirectorRepairPostExecutionScheduleRunResultV1)
    assert payload["schema_version"] == "director.repair_post_execution_schedule_run_result.v1"
    assert payload["owner_cell"] == "director.runtime"
    assert payload["runner_binding_owner"] == "roles.adapters"
    assert payload["legacy_callback_bridge"] is True
    assert payload["projection_only"] is True
    assert payload["typed_receipt_path_available"] is False
    assert payload["authoritative_receipts_allowed"] is False
    assert payload["summary"]["receipt_projection_count"] == 1
    assert payload["summary"]["schedule_kind"] == "post_execution"
    assert payload["summary"]["callback_bridge_uses_repair_convergence_scheduler"] is False
    assert payload["summary"]["typed_convergence_scheduler_cutover_required"] is True
    assert payload["summary"]["callback_runner_self_loop_allowed"] is False
    assert payload["summary"]["post_check_evidence_complete"] is False
    assert payload["summary"]["missing_native_revalidation_evidence"] is True
    assert payload["summary"]["non_authoritative_projection"] is True
    assert payload["summary"]["cutover_ready"] is False
    assert payload["summary"]["evidence_status_counts"]["missing_evidence"] == 1
    assert payload["receipt_projections"][0]["projection_only"] is True
    assert payload["receipt_projections"][0]["typed_receipt_path_available"] is False
    assert payload["receipt_projections"][0]["authoritative"] is False
    assert payload["receipt_projections"][0]["step_id"] == "rust.dependency_resolution"
    assert payload["tool_results"][0]["result"]["source_tool"] == "deterministic_rust_dependency_repair"
    assert [step["step_id"] for step in payload["ordered_steps"]] == list(runner_step_ids)
    assert isinstance(legacy_tool_results, list)
    assert isinstance(legacy_ordered_steps, tuple)
    assert legacy_tool_results[0]["result"]["source_tool"] == "deterministic_rust_dependency_repair"
    assert [step.step_id for step in legacy_ordered_steps] == list(runner_step_ids)


def test_public_callback_receipt_projection_forces_non_authoritative_payload() -> None:
    result = DirectorRepairPostExecutionScheduleRunResultV1(
        schema_version="director.repair_post_execution_schedule_run_result.v1",
        source="test",
        receipt_projections=(
            {
                "schema_version": "director.repair_callback_receipt_projection.v1",
                "projection_id": "malicious-projection",
                "receipt_authority": "authoritative_callback_receipt",
                "schedule_kind": "post_execution",
                "step_id": "rust.post_execution_convergence",
                "source_tool": "deterministic_rust_dependency_repair",
                "round_number": 1,
                "tool_name": "write_file",
                "touched_path": "Cargo.toml",
                "touched_paths": ["Cargo.toml"],
                "projection_only": False,
                "typed_receipt_path_available": True,
                "authoritative": True,
                "revalidation_evidence_present": True,
                "revalidation_command": ["cargo", "test"],
                "revalidation_exit_code": 0,
                "revalidation_residual_count": 0,
            },
        ),
    )

    assert isinstance(result.receipt_projections[0], DirectorRepairCallbackReceiptProjectionV1)
    projection = result.to_dict()["receipt_projections"][0]
    assert projection["receipt_authority"] == "non_authoritative_callback_receipt_projection"
    assert projection["projection_only"] is True
    assert projection["typed_receipt_path_available"] is False
    assert projection["authoritative"] is False
    assert projection["migration_blocker"] == "callback_projection_not_authoritative_receipt"
    assert projection["revalidation_command"] == ["cargo", "test"]
    assert projection["metadata"]["claimed_typed_receipt_path_available"] is True


def test_public_callback_receipt_projection_preserves_only_strict_typed_receipt_claims() -> None:
    false_projection = DirectorRepairCallbackReceiptProjectionV1(
        projection_id="string-false-projection",
        typed_receipt_path_available="false",  # type: ignore[arg-type]
    ).to_dict()
    true_projection = DirectorRepairCallbackReceiptProjectionV1(
        projection_id="string-true-projection",
        typed_receipt_path_available="true",  # type: ignore[arg-type]
    ).to_dict()

    assert false_projection["typed_receipt_path_available"] is False
    assert "claimed_typed_receipt_path_available" not in false_projection["metadata"]
    assert true_projection["typed_receipt_path_available"] is False
    assert true_projection["metadata"]["claimed_typed_receipt_path_available"] is True


def test_public_materialization_schedule_run_result_exposes_summary_and_receipts() -> None:
    runner_step_ids = (
        "materialization.hygiene_scaffold",
        "materialization.typescript_scaffold",
        "materialization.typescript_compiler",
        "materialization.node_manifest",
        "materialization.rust_compiler",
        "materialization.target_runtime",
        "materialization.python_import",
        "materialization.go_import",
    )

    def runner(step) -> list[dict[str, object]]:
        if step.step_id != "materialization.typescript_compiler":
            return []
        return [
            {
                "tool_name": "edit_file",
                "success": True,
                "result": {
                    "source_tool": "deterministic_typescript_materialization_repair",
                    "file": "src/main.ts",
                    "after_hash": "hash-public-materialization",
                },
            }
        ]

    result = run_director_materialization_quality_repair_schedule_result(
        runner_step_ids=runner_step_ids,
        runner=runner,
    )
    payload = result.to_dict()
    legacy_tool_results, legacy_ordered_steps = run_director_materialization_quality_repair_schedule(
        runner_step_ids=runner_step_ids,
        runner=runner,
    )

    assert isinstance(result, DirectorRepairMaterializationQualityScheduleRunResultV1)
    assert payload["schema_version"] == "director.repair_materialization_quality_schedule_run_result.v1"
    assert payload["owner_cell"] == "director.runtime"
    assert payload["runner_binding_owner"] == "roles.adapters"
    assert payload["legacy_callback_bridge"] is True
    assert payload["projection_only"] is True
    assert payload["typed_receipt_path_available"] is False
    assert payload["authoritative_receipts_allowed"] is False
    assert payload["summary"]["receipt_projection_count"] == 1
    assert payload["summary"]["schedule_kind"] == "materialization_quality"
    assert payload["summary"]["post_check_evidence_complete"] is False
    assert payload["summary"]["missing_native_revalidation_evidence"] is True
    assert payload["summary"]["non_authoritative_projection"] is True
    assert payload["summary"]["cutover_ready"] is False
    assert payload["summary"]["evidence_status_counts"]["missing_evidence"] == 1
    assert payload["receipt_projections"][0]["projection_only"] is True
    assert payload["receipt_projections"][0]["typed_receipt_path_available"] is False
    assert payload["receipt_projections"][0]["authoritative"] is False
    assert payload["receipt_projections"][0]["step_id"] == "materialization.typescript_compiler"
    assert payload["tool_results"][0]["result"]["source_tool"] == "deterministic_typescript_materialization_repair"
    assert [step["step_id"] for step in payload["ordered_steps"]] == list(runner_step_ids)
    assert isinstance(legacy_tool_results, list)
    assert isinstance(legacy_ordered_steps, tuple)
    assert legacy_tool_results[0]["result"]["source_tool"] == "deterministic_typescript_materialization_repair"
    assert [step.step_id for step in legacy_ordered_steps] == list(runner_step_ids)
