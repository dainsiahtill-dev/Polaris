"""Tests for the Director Runtime Repair Kernel contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.director.runtime.internal.repair_kernel import (
    PatchComposer,
    RepairAdvisorNote,
    RepairDiagnostic,
    RepairOperation,
    RepairPlan,
    RepairPolicyContext,
    RepairPolicyGate,
    TransactionalRepairExecutor,
    build_repair_receipt_context,
    default_repair_rule_registry,
    run_post_execution_repair_schedule_callbacks,
)
from polaris.cells.director.runtime.internal.repair_kernel.advisory_policy import (
    FORBIDDEN_REPAIR_ADVISORY_METADATA_FIELDS,
    FORBIDDEN_REPAIR_ADVISORY_SUGGESTED_RULE_FIELDS,
)
from polaris.cells.director.runtime.internal.repair_kernel.contracts import (
    sha256_text,
)
from polaris.cells.director.runtime.public import (
    AttachDirectorRepairRevalidationEvidenceV1,
    CompareDirectorRepairShadowRunV1,
    DirectorRepairAdvisoryPolicyResultV1,
    DirectorRepairAdvisoryValidationResultV1,
    DirectorRepairCoverageReportV1,
    DirectorRepairKernelSummaryProjectionResultV1,
    DirectorRepairLanguageSlotsResultV1,
    DirectorRepairLanguageSlotV1,
    DirectorRepairMaterializationQualityFacadeResultV1,
    DirectorRepairMaterializationQualityScheduleResultV1,
    DirectorRepairMetricsResultV1,
    DirectorRepairPostExecutionScheduleResultV1,
    DirectorRepairRevalidationProjectionResultV1,
    DirectorRepairShadowComparisonResultV1,
    EvaluateDirectorRepairCutoverReadinessV1,
    PlanDirectorRepairCommandV1,
    ProjectDirectorRepairKernelSummaryV1,
    ProjectDirectorRepairMaterializationBridgeMetadataV1,
    ProjectDirectorRepairMetricsV1,
    QueryDirectorRepairAdvisoryPolicyV1,
    QueryDirectorRepairAdvisoryValidationV1,
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairLanguageSlotsV1,
    QueryDirectorRepairMaterializationQualityScheduleV1,
    QueryDirectorRepairPostExecutionScheduleV1,
    RepairAdvisoryV1,
    RepairReceiptV1,
    RunDirectorRepairCommandV1,
    attach_director_repair_revalidation_evidence,
    build_director_repair_kernel_summary,
    compare_director_repair_shadow_run,
    evaluate_director_repair_cutover_readiness,
    plan_director_repair,
    project_director_repair_kernel_summary,
    project_director_repair_materialization_bridge_metadata,
    project_director_repair_metrics,
    project_director_repair_revalidation_evidence,
    query_director_repair_advisory_policy,
    query_director_repair_coverage,
    query_director_repair_language_slots,
    query_director_repair_materialization_quality_schedule,
    query_director_repair_post_execution_schedule,
    run_director_materialization_quality_repair_facade,
    run_director_repair,
    validate_director_repair_advisory,
)
from polaris.cells.director.runtime.public.service import (
    _execution as runtime_public_execution,
)
from polaris.cells.director.runtime.tests._repair_kernel_contract_support import (
    _assert_direct_runtime_receipt_pending_revalidation,
    _ready_shadow_comparison,
)
from polaris.kernelone.tools.tool_kinds import DEPRECATED_WRITE_TOOLS


def test_public_cpp_placeholder_declaration_run_executes_with_receipt(tmp_path: Path) -> None:
    relative_path = "src/engine/generator.hpp"
    target = tmp_path / relative_path
    content = (
        "#pragma once\n"
        "namespace demo {\n"
        "class Generator {\n"
        "public:\n"
        "    std::render_return_type /* placeholder */ render_html() const = delete;\n"
        "};\n"
        "}\n"
    )
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-cpp-placeholder",
            workspace=str(tmp_path),
            source_tool="deterministic_cpp_placeholder_declaration_repair",
            base_files={relative_path: content},
            allowed_paths=(relative_path,),
        ),
        writer=writer,
    )

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.source_tool == "deterministic_cpp_placeholder_declaration_repair"
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.files_changed == (relative_path,)
    assert receipt.before_hashes[relative_path] == sha256_text(content)
    assert receipt.after_hashes[relative_path] == sha256_text(target.read_text(encoding="utf-8"))
    assert "std::render_return_type" not in target.read_text(encoding="utf-8")
    assert result.metadata["planning"]["planned"] is True
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None


def test_public_cpp_struct_getter_field_access_run_executes_with_receipt(tmp_path: Path) -> None:
    header_path = "src/models/postcard.hpp"
    source_path = "src/main.cpp"
    header = "#pragma once\nnamespace demo {\nstruct Postcard {\n    int poem;\n};\n}\n"
    source = (
        '#include "models/postcard.hpp"\nint main() {\n    demo::Postcard card{};\n    return card.get_poem();\n}\n'
    )
    target = tmp_path / source_path
    (tmp_path / header_path).parent.mkdir(parents=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / header_path).write_text(header, encoding="utf-8")
    target.write_text(source, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-cpp-struct-getter",
            workspace=str(tmp_path),
            source_tool="deterministic_cpp_struct_getter_field_access_repair",
            base_files={header_path: header, source_path: source},
            allowed_paths=(header_path, source_path),
        ),
        writer=writer,
    )

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.source_tool == "deterministic_cpp_struct_getter_field_access_repair"
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.files_changed == (source_path,)
    assert receipt.before_hashes[source_path] == sha256_text(source)
    assert receipt.after_hashes[source_path] == sha256_text(target.read_text(encoding="utf-8"))
    assert "card.poem" in target.read_text(encoding="utf-8")
    assert result.metadata["planning"]["planned"] is True
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None


def test_public_java_accessor_alias_run_executes_with_receipt(tmp_path: Path) -> None:
    relative_path = "src/main/java/demo/RhythmMonster.java"
    target = tmp_path / relative_path
    content = (
        "package demo;\n"
        "public final class RhythmMonster {\n"
        "    public int getTemperament() {\n"
        "        return 4;\n"
        "    }\n"
        "}\n"
    )
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-java-accessor",
            workspace=str(tmp_path),
            source_tool="deterministic_java_accessor_alias_repair",
            base_files={relative_path: content},
            allowed_paths=(relative_path,),
        ),
        writer=writer,
    )

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.source_tool == "deterministic_java_accessor_alias_repair"
    _assert_direct_runtime_receipt_pending_revalidation(receipt)
    assert receipt.files_changed == (relative_path,)
    assert receipt.before_hashes[relative_path] == sha256_text(content)
    assert receipt.after_hashes[relative_path] == sha256_text(target.read_text(encoding="utf-8"))
    assert "public int temperament()" in target.read_text(encoding="utf-8")
    assert result.metadata["planning"]["planned"] is True
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None


def test_public_repair_run_normalizes_dot_slash_paths_for_policy_and_planning(tmp_path: Path) -> None:
    relative_path = "src/models/Flight.ts"
    target = tmp_path / relative_path
    content = "export function summarizeFlight() {\n  return {\n    range\n    maxAltitude: 2,\n  };\n}\n"
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    write_paths: list[str] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        write_paths.append(path)
        (tmp_path / path).write_text(updated, encoding="utf-8")
        return {"ok": True, "file": path, "operation": "modify"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-ts1005-dot-path",
            workspace=str(tmp_path),
            source_tool="deterministic_typescript_return_object_semicolon_repair",
            base_files={f"./{relative_path}": content},
            artifact_quality_errors=(
                f"TypeScript syntax check failed: ./{relative_path}(4,5): error TS1005: ',' expected.",
            ),
            allowed_paths=(relative_path,),
        ),
        writer=writer,
    )

    assert result.ok is True
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.receipts[0].files_changed == (relative_path,)
    assert write_paths == [relative_path]
    assert "    range,\n" in target.read_text(encoding="utf-8")


def test_public_repair_run_preserves_execution_failure_receipt_metadata(tmp_path: Path) -> None:
    relative_path = "src/models/Flight.ts"
    target = tmp_path / relative_path
    content = "export function summarizeFlight() {\n  return {\n    range\n    maxAltitude: 2,\n  };\n}\n"
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")

    def rejecting_writer(path: str, updated: str) -> dict[str, object]:
        del path, updated
        return {"ok": False, "operation": "modify"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-ts1005-writer-rejected",
            workspace=str(tmp_path),
            source_tool="deterministic_typescript_return_object_semicolon_repair",
            base_files={relative_path: content},
            artifact_quality_errors=(
                f"TypeScript syntax check failed: {relative_path}(4,5): error TS1005: ',' expected.",
            ),
            allowed_paths=(relative_path,),
        ),
        writer=rejecting_writer,
    )

    assert result.ok is False
    assert result.error_code == "repair_execution_failed"
    assert len(result.receipts) == 1
    assert result.receipts[0].status == "failed"
    assert result.metadata["execution_error"] == f"repair writer rejected {relative_path}"
    assert target.read_text(encoding="utf-8") == content


def test_public_repair_run_projects_non_authoritative_advisory_suggested_rules(tmp_path: Path) -> None:
    relative_path = "src/models/Flight.ts"
    target = tmp_path / relative_path
    content = (
        "export function runFlight() {\n"
        "  const samples = [];\n"
        "  const range = 10;\n"
        "  const maxAltitude = 2;\n"
        "  const flightTime = 3;\n"
        "  return { samples, range, maxAltitude, flightTime  landed: undefined as unknown as boolean };\n"
        "}\n"
    )
    suggested_rule = {
        "name": "rust_borrow_marker_self",
        "language": "rust",
        "pattern": r"found `&\\)` near method receiver",
        "fix_template": "replace `(&)` with `(&self)` only inside Rust impl method receivers",
        "confidence": 0.83,
        "evidence": ["src/lib.rs:12: expected one of `self`, `&self`, or `&mut self`"],
    }
    advisory = RepairAdvisoryV1(
        advisor_source="agi",
        message="suggest a future repair rule; do not execute it",
        suggested_rules=(suggested_rule,),
        metadata={"evidence_ref": "runtime/repair-coverage/rust.json"},
    )
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        (tmp_path / path).write_text(updated, encoding="utf-8")
        return {"ok": True, "file": path, "operation": "modify"}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="task-ts1005-advisory",
            workspace=str(tmp_path),
            source_tool="deterministic_typescript_return_object_semicolon_repair",
            base_files={relative_path: content},
            artifact_quality_errors=(
                "TypeScript syntax check failed: src/models/Flight.ts(6,47): error TS1005: ',' expected.",
            ),
            allowed_paths=(relative_path,),
            advisor_notes=(advisory,),
        ),
        writer=writer,
    )
    receipt_note = result.receipts[0].advisor_notes[0].to_dict()

    assert result.ok is True
    assert receipt_note["authoritative"] is False
    assert receipt_note["suggested_rules"][0]["name"] == "rust_borrow_marker_self"
    assert receipt_note["metadata"] == {"evidence_ref": "runtime/repair-coverage/rust.json"}
    assert result.metadata["planning"]["advisor_notes"][0]["suggested_rules"][0]["language"] == "rust"


def test_patch_composer_fails_closed_on_json_scalar_parent() -> None:
    base = {"package.json": '{"scripts":"npm test"}\n'}
    operations = [
        RepairOperation(kind="json_set", path="package.json", json_path=("scripts", "test"), value="vitest"),
    ]

    result = PatchComposer().compose(base, operations)

    assert not result.ok
    assert result.patches == ()
    assert result.issues[0].code == "json_path_parent_not_object"


def test_patch_composer_fails_closed_on_json_delete_missing_path() -> None:
    base = {"package.json": '{"scripts":{"test":"vitest"}}\n'}
    operations = [
        RepairOperation(kind="json_delete", path="package.json", json_path=("scripts", "build")),
    ]

    result = PatchComposer().compose(base, operations)

    assert not result.ok
    assert result.patches == ()
    assert result.issues[0].code == "json_path_not_found"


def test_patch_composer_fails_closed_without_partial_json_patch() -> None:
    base = {"package.json": '{"scripts":{"test":"vitest"}}\n'}
    operations = [
        RepairOperation(kind="json_set", path="package.json", json_path=("scripts", "build"), value="vite build"),
        RepairOperation(kind="json_delete", path="package.json", json_path=("scripts", "missing")),
    ]

    result = PatchComposer().compose(base, operations)

    assert not result.ok
    assert result.patches == ()
    assert [issue.code for issue in result.issues] == ["json_path_not_found"]


def test_policy_gate_blocks_advisor_authority_and_cycles() -> None:
    diagnostic = RepairDiagnostic(source="artifact_quality", code="ts", message="x", path="src/app.ts")
    first_plan = RepairPlan(
        rule_id="rule.ts",
        source_tool="deterministic_typescript_missing_export_repair",
        diagnostics=(diagnostic,),
        operations=(RepairOperation(kind="write_file", path="src/app.ts", content="export const x = 1;\n"),),
    )
    previous = (
        TransactionalRepairExecutor()
        .execute(
            workspace=Path("."),
            plan=RepairPlan(
                rule_id=first_plan.rule_id,
                source_tool=first_plan.source_tool,
                diagnostics=first_plan.diagnostics,
                operations=first_plan.operations,
                mode="shadow",
            ),
            composition=PatchComposer().compose({"src/app.ts": ""}, first_plan.operations),
        )
        .receipt
    )
    blocked_plan = RepairPlan(
        rule_id=first_plan.rule_id,
        source_tool=first_plan.source_tool,
        diagnostics=first_plan.diagnostics,
        operations=first_plan.operations,
        advisor_notes=(RepairAdvisorNote(source="agi", message="override policy", authoritative=True),),
    )

    assert blocked_plan.advisor_notes[0].authoritative is False
    assert blocked_plan.advisor_notes[0].metadata["requested_authoritative"] is True
    assert blocked_plan.advisor_notes[0].to_dict()["authoritative"] is False

    decision = RepairPolicyGate().evaluate_plan(
        blocked_plan,
        RepairPolicyContext(previous_receipts=(previous,), max_rule_activations=1),
    )

    assert not decision.allowed
    assert "advisor_note_marked_authoritative" in decision.reasons
    assert "rule_activation_cycle_breaker" in decision.reasons


def test_policy_gate_blocks_forbidden_composed_content() -> None:
    plan = RepairPlan(
        rule_id="rule.py",
        source_tool="deterministic_python_unittest_runtime_failure_repair",
        operations=(
            RepairOperation(kind="write_file", path="tests/test_app.py", content="import pytest\npytest.skip()\n"),
        ),
    )
    composition = PatchComposer().compose({"tests/test_app.py": ""}, plan.operations)

    decision = RepairPolicyGate().evaluate_composition(plan, composition)

    assert not decision.allowed
    assert decision.reasons == ("forbidden_repair_content:tests/test_app.py",)


def test_executor_shadow_mode_does_not_write(tmp_path: Path) -> None:
    target = tmp_path / "src" / "app.ts"
    target.parent.mkdir()
    target.write_text("old\n", encoding="utf-8")
    before = target.read_text(encoding="utf-8")
    plan = RepairPlan(
        rule_id="rule.ts",
        source_tool="deterministic_typescript_missing_export_repair",
        mode="shadow",
        operations=(RepairOperation(kind="write_file", path="src/app.ts", content="new\n"),),
    )
    composition = PatchComposer().compose({"src/app.ts": before}, plan.operations)

    result = TransactionalRepairExecutor().execute(workspace=tmp_path, plan=plan, composition=composition)

    assert result.ok
    assert result.receipt.status == "shadow_observed"
    assert result.receipt.authoritative is False
    assert target.read_text(encoding="utf-8") == before


def test_executor_commit_requires_policy_gated_writer_or_editor(tmp_path: Path) -> None:
    plan = RepairPlan(
        rule_id="rule.ts",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(RepairOperation(kind="write_file", path="src/app.ts", content="new\n"),),
    )
    composition = PatchComposer().compose({"src/app.ts": ""}, plan.operations)

    result = TransactionalRepairExecutor().execute(workspace=tmp_path, plan=plan, composition=composition)

    assert not result.ok
    assert result.error == "commit_requires_policy_gated_writer_or_editor"
    assert result.receipt.authoritative is False


def test_executor_commit_uses_writer_and_records_hashes(tmp_path: Path) -> None:
    target = tmp_path / "src" / "app.ts"
    target.parent.mkdir()
    target.write_text("old\n", encoding="utf-8")
    plan = RepairPlan(
        rule_id="rule.ts",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(RepairOperation(kind="write_file", path="src/app.ts", content="new\n"),),
    )
    composition = PatchComposer().compose({"src/app.ts": "old\n"}, plan.operations)

    def writer(path: str, content: str) -> dict[str, bool]:
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    result = TransactionalRepairExecutor().execute(
        workspace=tmp_path,
        plan=plan,
        composition=composition,
        writer=writer,
    )

    assert result.ok
    assert target.read_text(encoding="utf-8") == "new\n"
    _assert_direct_runtime_receipt_pending_revalidation(result.receipt)
    assert result.receipt.before_hashes["src/app.ts"] == sha256_text("old\n")
    assert result.receipt.after_hashes["src/app.ts"] == sha256_text("new\n")


def test_executor_falls_back_to_writer_when_first_editor_operation_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "src" / "app.ts"
    target.parent.mkdir()
    target.write_text("old\n", encoding="utf-8")
    operation = RepairOperation(
        kind="text_replace",
        path="src/app.ts",
        span_start=0,
        span_end=4,
        expected="old\n",
        replacement="new\nold\n",
        before_hash=sha256_text("old\n"),
        metadata={"repair_kind": "insert_prefix", "precision_strategy": "span_context_text_patch"},
    )
    plan = RepairPlan(
        rule_id="rule.ts",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(operation,),
    )
    composition = PatchComposer().compose({"src/app.ts": "old\n"}, plan.operations)
    writer_calls: list[tuple[str, str]] = []
    editor_calls: list[str] = []

    def editor(edit_operation: RepairOperation) -> dict[str, bool]:
        editor_calls.append(edit_operation.operation_id)
        return {"ok": False}

    def writer(path: str, content: str) -> dict[str, bool]:
        writer_calls.append((path, content))
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    result = TransactionalRepairExecutor().execute(
        workspace=tmp_path,
        plan=plan,
        composition=composition,
        writer=writer,
        editor=editor,
    )

    assert result.ok
    assert editor_calls == [operation.operation_id]
    assert writer_calls == [("src/app.ts", "new\nold\n")]
    assert target.read_text(encoding="utf-8") == "new\nold\n"
    assert result.receipt.metadata["execution_records"][0]["operation"] == "write_file"


def test_executor_reports_rollback_failed_when_restore_is_rejected(tmp_path: Path) -> None:
    app = tmp_path / "src" / "app.ts"
    config = tmp_path / "src" / "config.ts"
    app.parent.mkdir()
    app.write_text("old app\n", encoding="utf-8")
    config.write_text("old config\n", encoding="utf-8")
    plan = RepairPlan(
        rule_id="rule.ts",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(
            RepairOperation(kind="write_file", path="src/app.ts", content="new app\n"),
            RepairOperation(kind="write_file", path="src/config.ts", content="new config\n"),
        ),
    )
    composition = PatchComposer().compose(
        {"src/app.ts": "old app\n", "src/config.ts": "old config\n"},
        plan.operations,
    )
    write_calls: list[tuple[str, str]] = []

    def writer(path: str, content: str) -> dict[str, bool]:
        write_calls.append((path, content))
        if path == "src/config.ts":
            return {"ok": False}
        if content == "old app\n":
            return {"ok": False}
        return {"ok": True}

    result = TransactionalRepairExecutor().execute(
        workspace=tmp_path,
        plan=plan,
        composition=composition,
        writer=writer,
    )

    assert not result.ok
    assert result.rolled_back is False
    assert result.receipt.status == "rollback_failed"
    assert result.receipt.metadata["rollback_attempted"] is True
    assert result.receipt.metadata["rollback_failed_paths"] == ["src/app.ts"]
    assert write_calls == [("src/app.ts", "new app\n"), ("src/config.ts", "new config\n"), ("src/app.ts", "old app\n")]


def test_receipt_context_marks_agi_advisory_non_authoritative(tmp_path: Path) -> None:
    plan = RepairPlan(
        rule_id="rule.ts",
        source_tool="deterministic_typescript_missing_export_repair",
        mode="shadow",
        operations=(RepairOperation(kind="write_file", path="src/app.ts", content="new\n"),),
        advisor_notes=(RepairAdvisorNote(source="agi", message="consider import boundary", confidence=0.7),),
    )
    composition = PatchComposer().compose({"src/app.ts": ""}, plan.operations)
    result = TransactionalRepairExecutor().execute(workspace=tmp_path, plan=plan, composition=composition)

    context = build_repair_receipt_context([result.receipt])

    assert context["agi_advisory_supported"] is True
    assert context["agi_advisory_active"] is False
    assert context["agi_advisory_authoritative"] is False
    assert context["agi_advisory_writes_allowed"] is False
    assert context["agi_advisory_registration_allowed"] is False
    assert context["agi_advisory_authoritative_receipts_allowed"] is False
    assert context["receipts"][0]["advisor_notes"] == [
        {
            "source": "agi",
            "confidence": 0.7,
            "advisory_only": True,
            "authoritative": False,
            "director_runtime_remains_authoritative": True,
            "agi_execution_authority": False,
            "writes_allowed": False,
            "registration_allowed": False,
            "authoritative_receipts_allowed": False,
            "suggested_rules_are_advisory_only": True,
        }
    ]


def test_public_repair_metrics_project_success_rounds_coverage_and_agi_boundaries() -> None:
    coverage = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=("futurelang compiler: error FX9999: unknown repair gap",),
        )
    )
    result = project_director_repair_metrics(
        ProjectDirectorRepairMetricsV1(
            receipts=(
                RepairReceiptV1(
                    receipt_id="receipt_ok",
                    plan_id="plan_ok",
                    source_tool="deterministic_typescript_missing_export_repair",
                    status="applied",
                    authoritative=True,
                    errors_before=3,
                    errors_after=1,
                    net_error_reduction=2,
                ),
                RepairReceiptV1(
                    receipt_id="receipt_ineffective",
                    plan_id="plan_ineffective",
                    source_tool="deterministic_typescript_missing_export_repair",
                    status="applied",
                    authoritative=True,
                    errors_before=1,
                    errors_after=1,
                    net_error_reduction=0,
                ),
                RepairReceiptV1(
                    receipt_id="receipt_failed",
                    plan_id="plan_failed",
                    source_tool="deterministic_go_module_import_repair",
                    status="failed_revalidation",
                    authoritative=True,
                    errors_before=2,
                    errors_after=2,
                    net_error_reduction=0,
                ),
            ),
            coverage_reports=(coverage,),
            schedule_run_summaries=({"rounds_run": 2}, {"rounds_run": 4}),
        )
    )
    payload = result.to_dict()

    assert isinstance(result, DirectorRepairMetricsResultV1)
    assert payload["schema_version"] == "director.repair_metrics.v1"
    assert payload["access"] == "read_only"
    assert payload["advisory_only"] is True
    assert payload["agi_execution_authority"] is False
    assert payload["writes_allowed"] is False
    assert payload["registration_allowed"] is False
    assert payload["receipt_count"] == 3
    assert payload["applied_receipt_count"] == 2
    assert payload["failed_receipt_count"] == 1
    assert payload["ineffective_receipt_count"] == 2
    assert payload["success_rate"] == pytest.approx(2 / 3)
    assert payload["average_convergence_rounds"] == pytest.approx(3.0)
    assert payload["uncovered_diagnostic_count"] == 1
    assert payload["coverage_gap_count"] == 1
    assert payload["metadata"]["failed_receipt_ids"] == ["receipt_failed"]
    assert payload["metadata"]["ineffective_receipt_ids"] == ["receipt_ineffective", "receipt_failed"]
    assert payload["metadata"]["schedule_rounds"] == [2, 4]


def test_advisor_overlay_does_not_change_authority_hash(tmp_path: Path) -> None:
    base_plan = RepairPlan(
        rule_id="rule.ts",
        source_tool="deterministic_typescript_missing_export_repair",
        mode="shadow",
        operations=(RepairOperation(kind="write_file", path="src/app.ts", content="new\n"),),
    )
    advised_plan = RepairPlan(
        rule_id=base_plan.rule_id,
        source_tool=base_plan.source_tool,
        mode=base_plan.mode,
        operations=base_plan.operations,
        advisor_notes=(RepairAdvisorNote(source="agi", message="read-only hint", confidence=0.5),),
    )
    composer = PatchComposer()
    executor = TransactionalRepairExecutor()
    base_receipt = executor.execute(
        workspace=tmp_path,
        plan=base_plan,
        composition=composer.compose({"src/app.ts": ""}, base_plan.operations),
    ).receipt
    advised_receipt = executor.execute(
        workspace=tmp_path,
        plan=advised_plan,
        composition=composer.compose({"src/app.ts": ""}, advised_plan.operations),
    ).receipt

    assert base_receipt.authority_hash() == advised_receipt.authority_hash()
    assert base_receipt.projection_hash() != advised_receipt.projection_hash()


@pytest.mark.parametrize(
    "field",
    sorted(FORBIDDEN_REPAIR_ADVISORY_METADATA_FIELDS),
)
def test_public_advisory_contract_rejects_authoritative_fields(field: str) -> None:
    with pytest.raises(ValueError, match="forbidden authoritative fields"):
        RepairAdvisoryV1(
            advisor_source="agi",
            message="try this",
            metadata={field: "not allowed"},
        )


@pytest.mark.parametrize(
    "field",
    sorted(FORBIDDEN_REPAIR_ADVISORY_METADATA_FIELDS),
)
def test_internal_advisor_note_rejects_authoritative_fields(field: str) -> None:
    with pytest.raises(ValueError, match="forbidden authoritative fields"):
        RepairAdvisorNote(
            source="agi",
            message="read-only evidence only",
            metadata={field: "not allowed"},
        )


def test_advisory_contracts_allow_non_authoritative_evidence_refs() -> None:
    public_note = RepairAdvisoryV1(
        advisor_source="agi",
        message="inspect latest receipt",
        metadata={"evidence_ref": "runtime/receipts/repair.json"},
    )
    internal_note = RepairAdvisorNote(
        source="agi",
        message="inspect latest receipt",
        metadata={"evidence_ref": "runtime/receipts/repair.json"},
    )

    assert public_note.metadata == {"evidence_ref": "runtime/receipts/repair.json"}
    assert internal_note.metadata == {"evidence_ref": "runtime/receipts/repair.json"}


def test_advisory_contracts_allow_non_authoritative_suggested_rules() -> None:
    suggested_rule = {
        "name": "rust_borrow_marker_self",
        "language": "rust",
        "pattern": r"found `&\\)` near method receiver",
        "fix_template": "replace `(&)` with `(&self)` only inside Rust impl method receivers",
        "confidence": 0.83,
        "evidence": ["src/lib.rs:12: expected one of `self`, `&self`, or `&mut self`"],
        "rationale": "LLMs often omit `self` in Rust receiver syntax.",
    }

    public_note = RepairAdvisoryV1(
        advisor_source="agi",
        message="suggest a future Rust syntax repair rule",
        suggested_rules=(suggested_rule,),
    )
    internal_note = RepairAdvisorNote(
        source="agi",
        message="suggest a future Rust syntax repair rule",
        suggested_rules=(suggested_rule,),
    )

    assert public_note.to_dict()["authoritative"] is False
    assert public_note.to_dict()["suggested_rules"][0]["pattern"] == suggested_rule["pattern"]
    internal_payload = internal_note.to_dict()
    assert internal_payload["advisory_only"] is True
    assert internal_payload["authoritative"] is False
    assert internal_payload["director_runtime_remains_authoritative"] is True
    assert internal_payload["agi_execution_authority"] is False
    assert internal_payload["writes_allowed"] is False
    assert internal_payload["registration_allowed"] is False
    assert internal_payload["authoritative_receipts_allowed"] is False
    assert internal_payload["suggested_rules_are_advisory_only"] is True
    assert internal_payload["suggested_rules"][0]["fix_template"] == suggested_rule["fix_template"]


@pytest.mark.parametrize(
    "field",
    sorted(FORBIDDEN_REPAIR_ADVISORY_SUGGESTED_RULE_FIELDS),
)
def test_advisory_suggested_rules_reject_authoritative_fields(field: str) -> None:
    with pytest.raises(ValueError, match="forbidden authoritative fields"):
        RepairAdvisoryV1(
            advisor_source="agi",
            message="malicious rule",
            suggested_rules=(
                {
                    "pattern": "x",
                    "fix_template": "y",
                    field: "not allowed",
                },
            ),
        )


def test_public_repair_advisory_policy_exposes_read_only_agi_boundaries() -> None:
    result = query_director_repair_advisory_policy(QueryDirectorRepairAdvisoryPolicyV1())
    payload = result.to_dict()

    assert isinstance(result, DirectorRepairAdvisoryPolicyResultV1)
    assert payload["schema_version"] == "director.repair_advisory_policy.v1"
    assert payload["source"] == "director.runtime.repair_kernel.advisory_policy"
    assert payload["access"] == "read_only"
    assert payload["owner_cell"] == "director.runtime"
    assert payload["execution_boundary"] == "read_only_advisory_no_writes_no_registration"
    assert payload["agi_execution_authority"] is False
    assert payload["writes_allowed"] is False
    assert payload["registration_allowed"] is False
    assert payload["authoritative_receipts_allowed"] is False
    assert "pattern" in payload["allowed_suggested_rule_fields"]
    assert "fix_template" in payload["allowed_suggested_rule_fields"]
    assert "repair_plan" in payload["forbidden_metadata_fields"]
    assert "write_file" in payload["forbidden_suggested_rule_fields"]
    assert payload["summary"]["suggested_rules_allowed"] is True
    assert payload["summary"]["director_runtime_remains_authoritative"] is True


def test_public_repair_advisory_validation_normalizes_non_authoritative_suggestions() -> None:
    result = validate_director_repair_advisory(
        QueryDirectorRepairAdvisoryValidationV1(
            advisor_source="resident_agi",
            message="Potential recurring shell quoting diagnostic.",
            confidence=0.9,
            suggested_rules=(
                {
                    "pattern": "shellcheck SC2086",
                    "fix_template": "quote variable expansions",
                    "language": "shell",
                    "confidence": 0.75,
                    "evidence": ["scripts/deploy.sh:12"],
                },
            ),
            metadata={"run_id": "run-1"},
        )
    )
    payload = result.to_dict()

    assert isinstance(result, DirectorRepairAdvisoryValidationResultV1)
    assert payload["schema_version"] == "director.repair_advisory_validation.v1"
    assert payload["access"] == "read_only"
    assert payload["ok"] is True
    assert payload["agi_execution_authority"] is False
    assert payload["writes_allowed"] is False
    assert payload["registration_allowed"] is False
    assert payload["authoritative_receipts_allowed"] is False
    assert payload["normalized_advisory"]["authoritative"] is False
    assert payload["normalized_advisory"]["suggested_rules"][0]["language"] == "shell"
    forbidden_capability_fields = {
        "mode",
        "operation",
        "operations",
        "patch",
        "patches",
        "policy_override",
        "receipt",
        "receipts",
        "registered",
        "repair_plan",
        "rule_id",
        "source_tool",
        "success_verdict",
        "write_file",
    }
    assert forbidden_capability_fields.isdisjoint(payload)
    assert forbidden_capability_fields.isdisjoint(payload["normalized_advisory"])
    assert forbidden_capability_fields.isdisjoint(payload["normalized_advisory"]["suggested_rules"][0])
    assert payload["summary"]["accepted_suggested_rule_count"] == 1
    assert payload["summary"]["advisory_only"] is True
    assert payload["summary"]["suggested_rules_are_advisory_only"] is True
    assert payload["summary"]["agi_execution_authority"] is False
    assert payload["summary"]["writes_allowed"] is False
    assert payload["summary"]["registration_allowed"] is False
    assert payload["summary"]["authoritative_receipts_allowed"] is False
    assert payload["summary"]["director_runtime_remains_authoritative"] is True


def test_public_repair_advisory_validation_rejects_authoritative_fields() -> None:
    result = validate_director_repair_advisory(
        QueryDirectorRepairAdvisoryValidationV1(
            advisor_source="resident_agi",
            message="Attempt to smuggle authoritative fields.",
            suggested_rules=(
                {
                    "pattern": "x",
                    "fix_template": "y",
                    "source_tool": "deterministic_future_repair",
                    "patch": "*** Begin Patch",
                },
            ),
            metadata={"success_verdict": True},
        )
    )
    payload = result.to_dict()

    assert payload["ok"] is False
    assert payload["normalized_advisory"] is None
    assert payload["agi_execution_authority"] is False
    assert payload["writes_allowed"] is False
    assert payload["registration_allowed"] is False
    assert payload["authoritative_receipts_allowed"] is False
    forbidden_capability_fields = {
        "operation",
        "operations",
        "patch",
        "patches",
        "receipt",
        "receipts",
        "registered",
        "repair_plan",
        "source_tool",
        "success_verdict",
        "write_file",
    }
    assert forbidden_capability_fields.isdisjoint(payload)
    assert payload["summary"]["accepted_suggested_rule_count"] == 0
    assert payload["summary"]["advisory_only"] is True
    assert payload["summary"]["suggested_rules_are_advisory_only"] is True
    assert payload["summary"]["agi_execution_authority"] is False
    assert payload["summary"]["writes_allowed"] is False
    assert payload["summary"]["registration_allowed"] is False
    assert payload["summary"]["authoritative_receipts_allowed"] is False
    assert payload["summary"]["director_runtime_remains_authoritative"] is True
    assert "forbidden authoritative fields" in payload["errors"][0]


def test_public_shadow_comparison_is_read_only_and_reports_scope_match() -> None:
    result = compare_director_repair_shadow_run(
        CompareDirectorRepairShadowRunV1(
            baseline_tool_results=(
                {
                    "tool_name": "write_file",
                    "success": True,
                    "result": {
                        "source_tool": "deterministic_typescript_missing_export_repair",
                        "file": "src/app.ts",
                    },
                },
            ),
            kernel_receipts=(
                RepairReceiptV1(
                    receipt_id="receipt_1",
                    plan_id="plan_1",
                    source_tool="deterministic_typescript_missing_export_repair",
                    status="shadow_observed",
                    authoritative=False,
                    files_changed=("src/app.ts",),
                    metadata={"mode": "shadow"},
                ),
            ),
        )
    )
    payload = result.to_dict()

    assert payload["schema_version"] == "director.repair_shadow_comparison.v1"
    assert payload["source"] == "director.runtime.repair_kernel.shadow"
    assert payload["access"] == "read_only"
    assert payload["execution_boundary"] == "read_only_shadow_comparison_no_writes"
    assert payload["agi_execution_authority"] is False
    assert payload["writes_allowed"] is False
    assert payload["comparison_mode"] == "independent_shadow_run"
    assert payload["independent_shadow_required"] is True
    assert payload["independent_shadow_satisfied"] is True
    assert payload["matched"] is True
    assert payload["cutover_ready"] is False
    assert payload["cutover_blockers"] == [
        "missing_before_after_hash_evidence",
        "missing_revalidation_evidence",
        "non_authoritative_kernel_receipt",
    ]
    assert payload["missing_paths_in_kernel"] == []
    assert payload["extra_paths_in_kernel"] == []
    assert payload["metadata"]["writes_performed"] is False
    assert payload["metadata"]["cutover_readiness"]["comparison_mode"] == "independent_shadow_run"
    assert payload["metadata"]["cutover_readiness"]["independent_shadow_required"] is True
    assert payload["metadata"]["cutover_readiness"]["independent_shadow_satisfied"] is True


def test_public_shadow_comparison_requires_hash_and_revalidation_for_cutover() -> None:
    result = compare_director_repair_shadow_run(
        CompareDirectorRepairShadowRunV1(
            baseline_tool_results=(
                {
                    "tool_name": "write_file",
                    "success": True,
                    "result": {
                        "source_tool": "deterministic_typescript_missing_export_repair",
                        "file": "src/app.ts",
                        "before_hash": "before123",
                        "after_hash": "after456",
                    },
                },
            ),
            kernel_receipts=(
                RepairReceiptV1(
                    receipt_id="receipt_1",
                    plan_id="plan_1",
                    source_tool="deterministic_typescript_missing_export_repair",
                    status="applied",
                    authoritative=True,
                    files_changed=("src/app.ts",),
                    before_hashes={"src/app.ts": "before123"},
                    after_hashes={"src/app.ts": "after456"},
                    revalidation_evidence={
                        "command": ["tsc", "--noEmit"],
                        "exit_code": 0,
                        "errors_before": 1,
                        "errors_after": 0,
                    },
                    metadata={"mode": "commit"},
                ),
            ),
        )
    )
    payload = result.to_dict()

    assert payload["matched"] is True
    assert payload["comparison_mode"] == "independent_shadow_run"
    assert payload["independent_shadow_satisfied"] is True
    assert payload["cutover_ready"] is True
    assert payload["cutover_blockers"] == []
    assert payload["metadata"]["cutover_readiness"]["hashes_matched"] is True
    assert payload["metadata"]["cutover_readiness"]["revalidation_evidence_complete"] is True
    assert payload["metadata"]["cutover_readiness"]["revalidation_evidence_passed"] is True
    assert payload["metadata"]["cutover_readiness"]["authoritative_receipts"] is True
    assert payload["metadata"]["cutover_readiness"]["independent_shadow_satisfied"] is True


def test_public_cutover_readiness_requires_repeated_independent_shadow_success() -> None:
    result = evaluate_director_repair_cutover_readiness(
        EvaluateDirectorRepairCutoverReadinessV1(
            comparisons=(_ready_shadow_comparison(), _ready_shadow_comparison()),
            required_successful_runs=2,
        )
    )
    payload = result.to_dict()

    assert payload["schema_version"] == "director.repair_cutover_readiness.v1"
    assert payload["source"] == "director.runtime.repair_kernel.cutover_gate"
    assert payload["access"] == "read_only"
    assert payload["writes_allowed"] is False
    assert payload["cutover_ready"] is True
    assert payload["required_successful_runs"] == 2
    assert payload["successful_comparison_count"] == 2
    assert payload["cutover_blockers"] == []
    assert payload["metadata"]["successful_comparison_indices"] == [0, 1]
    assert payload["metadata"]["multi_run_cutover_gate"] is True


def test_public_cutover_readiness_blocks_self_check_shortfall_and_scope_drift() -> None:
    self_check = DirectorRepairShadowComparisonResultV1(
        schema_version="director.repair_shadow_comparison.v1",
        source="director.runtime.repair_kernel.shadow",
        access="read_only",
        matched=True,
        baseline_source_tools=("deterministic_typescript_missing_export_repair",),
        kernel_source_tools=("deterministic_typescript_missing_export_repair",),
        baseline_paths=("src/app.ts",),
        kernel_paths=("src/app.ts",),
        comparison_mode="receipt_projection_self_check",
        independent_shadow_satisfied=False,
        cutover_ready=False,
        cutover_blockers=("independent_shadow_required",),
    )
    shortfall = evaluate_director_repair_cutover_readiness(
        EvaluateDirectorRepairCutoverReadinessV1(
            comparisons=(_ready_shadow_comparison(), self_check),
            required_successful_runs=2,
        )
    ).to_dict()
    drift = evaluate_director_repair_cutover_readiness(
        EvaluateDirectorRepairCutoverReadinessV1(
            comparisons=(_ready_shadow_comparison("src/app.ts"), _ready_shadow_comparison("src/other.ts")),
            required_successful_runs=2,
        )
    ).to_dict()

    assert shortfall["cutover_ready"] is False
    assert shortfall["cutover_blockers"] == [
        "insufficient_successful_independent_shadow_runs",
        "shadow_comparison_not_cutover_ready",
    ]
    assert shortfall["metadata"]["failed_comparison_indices"] == [1]
    assert drift["cutover_ready"] is False
    assert drift["cutover_blockers"] == ["shadow_comparison_scope_drift"]


def test_public_shadow_comparison_self_check_cannot_cutover_even_when_scope_matches() -> None:
    result = compare_director_repair_shadow_run(
        CompareDirectorRepairShadowRunV1(
            comparison_mode="receipt_projection_self_check",
            baseline_tool_results=(
                {
                    "tool_name": "write_file",
                    "success": True,
                    "result": {
                        "source_tool": "deterministic_typescript_missing_export_repair",
                        "file": "src/app.ts",
                        "before_hash": "before123",
                        "after_hash": "after456",
                    },
                },
            ),
            kernel_receipts=(
                RepairReceiptV1(
                    receipt_id="receipt_1",
                    plan_id="plan_1",
                    source_tool="deterministic_typescript_missing_export_repair",
                    status="applied",
                    authoritative=True,
                    files_changed=("src/app.ts",),
                    before_hashes={"src/app.ts": "before123"},
                    after_hashes={"src/app.ts": "after456"},
                    revalidation_evidence={
                        "command": ["tsc", "--noEmit"],
                        "exit_code": 0,
                        "errors_before": 1,
                        "errors_after": 0,
                    },
                    metadata={"mode": "commit"},
                ),
            ),
        )
    )
    payload = result.to_dict()

    assert payload["matched"] is True
    assert payload["comparison_mode"] == "receipt_projection_self_check"
    assert payload["independent_shadow_required"] is True
    assert payload["independent_shadow_satisfied"] is False
    assert payload["cutover_ready"] is False
    assert payload["cutover_blockers"] == ["independent_shadow_required"]
    readiness = payload["metadata"]["cutover_readiness"]
    assert readiness["comparison_mode"] == "receipt_projection_self_check"
    assert readiness["hashes_matched"] is True
    assert readiness["revalidation_evidence_complete"] is True
    assert readiness["revalidation_evidence_passed"] is True
    assert readiness["authoritative_receipts"] is True
    assert readiness["independent_shadow_satisfied"] is False


def test_public_shadow_comparison_blocks_non_authoritative_shadow_receipts() -> None:
    result = compare_director_repair_shadow_run(
        CompareDirectorRepairShadowRunV1(
            baseline_tool_results=(
                {
                    "tool_name": "write_file",
                    "success": True,
                    "result": {
                        "source_tool": "deterministic_typescript_missing_export_repair",
                        "file": "src/app.ts",
                        "before_hash": "before123",
                        "after_hash": "after456",
                    },
                },
            ),
            kernel_receipts=(
                RepairReceiptV1(
                    receipt_id="receipt_1",
                    plan_id="plan_1",
                    source_tool="deterministic_typescript_missing_export_repair",
                    status="shadow_observed",
                    authoritative=False,
                    files_changed=("src/app.ts",),
                    before_hashes={"src/app.ts": "before123"},
                    after_hashes={"src/app.ts": "after456"},
                    revalidation_evidence={
                        "command": ["tsc", "--noEmit"],
                        "exit_code": 0,
                        "errors_before": 1,
                        "errors_after": 0,
                    },
                    metadata={"mode": "shadow"},
                ),
            ),
        )
    )
    payload = result.to_dict()

    assert payload["matched"] is True
    assert payload["cutover_ready"] is False
    assert payload["cutover_blockers"] == ["non_authoritative_kernel_receipt"]
    assert payload["metadata"]["cutover_readiness"]["hashes_matched"] is True
    assert payload["metadata"]["cutover_readiness"]["revalidation_evidence_complete"] is True
    assert payload["metadata"]["cutover_readiness"]["revalidation_evidence_passed"] is True
    assert payload["metadata"]["cutover_readiness"]["authoritative_receipts"] is False


def test_public_shadow_comparison_blocks_failed_revalidation_evidence_for_cutover() -> None:
    result = compare_director_repair_shadow_run(
        CompareDirectorRepairShadowRunV1(
            baseline_tool_results=(
                {
                    "tool_name": "write_file",
                    "success": True,
                    "result": {
                        "source_tool": "deterministic_typescript_missing_export_repair",
                        "file": "src/app.ts",
                        "before_hash": "before123",
                        "after_hash": "after456",
                    },
                },
            ),
            kernel_receipts=(
                RepairReceiptV1(
                    receipt_id="receipt_1",
                    plan_id="plan_1",
                    source_tool="deterministic_typescript_missing_export_repair",
                    status="applied",
                    authoritative=True,
                    files_changed=("src/app.ts",),
                    before_hashes={"src/app.ts": "before123"},
                    after_hashes={"src/app.ts": "after456"},
                    revalidation_evidence={
                        "command": ["tsc", "--noEmit"],
                        "exit_code": 1,
                        "errors_before": 1,
                        "errors_after": 1,
                    },
                    metadata={"mode": "commit"},
                ),
            ),
        )
    )
    payload = result.to_dict()

    assert payload["matched"] is True
    assert payload["cutover_ready"] is False
    assert payload["cutover_blockers"] == ["failed_revalidation_evidence"]
    readiness = payload["metadata"]["cutover_readiness"]
    assert readiness["hashes_matched"] is True
    assert readiness["revalidation_evidence_complete"] is True
    assert readiness["revalidation_evidence_passed"] is False
    assert readiness["authoritative_receipts"] is True
    assert readiness["revalidation_coverage"]["failed_revalidation_receipt_count"] == 1
    assert readiness["revalidation_coverage"]["failed_revalidation_receipt_ids"] == ["receipt_1"]
    assert readiness["revalidation_coverage"]["failed_revalidation_source_tools"] == [
        "deterministic_typescript_missing_export_repair"
    ]


def test_public_kernel_summary_projection_is_typed_and_read_only() -> None:
    result = project_director_repair_kernel_summary(
        ProjectDirectorRepairKernelSummaryV1(
            stage="materialization_quality_repairs",
            mode="commit",
            artifact_quality_errors=(
                "TypeScript syntax check failed: src/app.ts(1,14): error TS2304: Cannot find name 'Widget'.",
            ),
            tool_results=(
                {
                    "tool": "write_file",
                    "tool_name": "write_file",
                    "success": True,
                    "result": {
                        "ok": True,
                        "source_tool": "deterministic_typescript_missing_export_repair",
                        "file": "src/app.ts",
                        "operation": "modify",
                    },
                },
            ),
        )
    )
    payload = result.to_dict()
    summary = result.summary

    assert isinstance(result, DirectorRepairKernelSummaryProjectionResultV1)
    assert payload["schema_version"] == "director.repair_kernel_summary_projection.v1"
    assert payload["source"] == "director.runtime.repair_kernel.receipt_projection"
    assert payload["access"] == "read_only"
    assert payload["execution_boundary"] == "repair_kernel_summary_projection_no_writes_no_registration"
    assert payload["writes_allowed"] is False
    assert payload["registration_allowed"] is False
    assert payload["agi_execution_authority"] is False
    assert summary["authoritative"] is False
    assert summary["requires_revalidation"] is True
    assert summary["pending_revalidation_count"] == 1
    assert summary["receipts_with_revalidation"] == 0
    assert summary["revalidation_coverage"]["receipt_count"] == 1
    assert summary["revalidation_coverage"]["receipts_missing_revalidation"] == 1
    assert summary["revalidation_coverage"]["missing_revalidation_source_tools"] == [
        "deterministic_typescript_missing_export_repair"
    ]
    assert summary["revalidation_coverage"]["post_check_evidence_complete"] is False
    assert summary["receipt_count"] == 1
    assert summary["receipts"][0]["source_tool"] == "deterministic_typescript_missing_export_repair"
    assert summary["receipts"][0]["status"] == "pending_revalidation"
    assert summary["receipts"][0]["authoritative"] is False
    assert summary["receipts"][0]["metadata"]["requires_revalidation"] is True
    assert summary["dark_launch_comparison"]["metadata"]["read_only"] is True
    assert summary["dark_launch_comparison"]["metadata"]["writes_performed"] is False
    assert summary["dark_launch_comparison"]["metadata"]["comparison_mode"] == "receipt_projection_self_check"
    assert summary["dark_launch_comparison"]["comparison_mode"] == "receipt_projection_self_check"
    assert summary["dark_launch_comparison"]["cutover_ready"] is False
    assert summary["dark_launch_comparison"]["cutover_blockers"] == ["independent_shadow_required"]
    assert summary["dark_launch_comparison"]["independent_shadow_required"] is True
    assert summary["dark_launch_comparison"]["independent_shadow_satisfied"] is False
    assert summary["coverage_report"]["total_diagnostics"] == 1


def test_receipt_summary_without_receipts_is_not_authoritative() -> None:
    summary = build_director_repair_kernel_summary(stage="quality", tool_results=[], mode="commit")

    assert summary["receipt_count"] == 0
    assert summary["authoritative"] is False
    assert summary["coverage_report"]["total_diagnostics"] == 0
    assert summary["dark_launch_comparison"]["matched"] is True
    assert summary["dark_launch_comparison"]["metadata"]["read_only"] is True
    assert summary["dark_launch_comparison"]["metadata"]["writes_performed"] is False
    assert summary["dark_launch_comparison"]["metadata"]["comparison_mode"] == "receipt_projection_self_check"
    assert summary["dark_launch_comparison"]["cutover_ready"] is False
    assert summary["dark_launch_comparison"]["cutover_blockers"] == ["independent_shadow_required"]


def test_receipt_summary_includes_uncovered_diagnostic_report() -> None:
    summary = build_director_repair_kernel_summary(
        stage="quality",
        tool_results=[],
        mode="shadow",
        artifact_quality_errors=["Mystery compiler failure XYZ999: no deterministic rule covers this yet"],
    )

    coverage_report = summary["coverage_report"]
    assert coverage_report["total_diagnostics"] == 1
    assert coverage_report["uncovered_diagnostic_count"] == 1
    assert coverage_report["coverage_gap_count"] == 1
    assert coverage_report["rule_discovery_required"] is True
    assert coverage_report["items"][0]["known_rule_matched"] is False
    assert coverage_report["uncovered_diagnostics"][0]["message"]
    assert coverage_report["coverage_gaps"][0]["audit_reason"] == "known_rule_matched=false"


def test_receipt_summary_accepts_typed_artifact_quality_issues() -> None:
    typed_issue = {
        "source": "artifact_quality",
        "code": "typescript_ts2304",
        "message": "Cannot find name 'Widget'.",
        "path": "src/app.ts",
        "metadata": {"raw": "src/app.ts(1,14): error TS2304: Cannot find name 'Widget'.", "line": 1, "column": 14},
    }
    summary = build_director_repair_kernel_summary(
        stage="quality",
        mode="commit",
        artifact_quality_errors=[],
        artifact_quality_issues=(typed_issue,),
        tool_results=[
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_typescript_missing_export_repair",
                    "file": "src/app.ts",
                    "operation": "modify",
                },
            }
        ],
    )

    diagnostic = summary["receipts"][0]["diagnostics"][0]
    assert diagnostic["code"] == "typescript_ts2304"
    assert diagnostic["path"] == "src/app.ts"
    assert diagnostic["metadata"]["line"] == 1
    assert summary["coverage_report"]["items"][0]["diagnostic_code"] == "typescript_ts2304"


def test_public_summary_passes_typed_issues_as_repair_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    typed_issue = {
        "source": "artifact_quality",
        "code": "typescript_ts2304",
        "message": "Cannot find name 'Widget'.",
        "path": "src/app.ts",
        "metadata": {
            "raw": "src/app.ts(1,14): error TS2304: Cannot find name 'Widget'.",
            "line": 1,
            "column": 14,
        },
    }
    captured: dict[str, object] = {}

    def fake_build_repair_kernel_result_summary(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        raw_diagnostics = kwargs.get("repair_diagnostics")
        assert isinstance(raw_diagnostics, tuple)
        return {
            "version": 1,
            "stage": kwargs.get("stage"),
            "mode": kwargs.get("mode"),
            "authoritative": False,
            "requires_revalidation": False,
            "pending_revalidation_count": 0,
            "receipts_with_revalidation": 0,
            "revalidation_coverage": {},
            "receipt_count": 0,
            "receipts": [],
            "receipt_context": {},
            "coverage_report": {
                "total_diagnostics": len(raw_diagnostics),
            },
            "dark_launch_comparison": {},
        }

    monkeypatch.setattr(
        runtime_public_execution,
        "_build_repair_kernel_result_summary",
        fake_build_repair_kernel_result_summary,
    )

    result = project_director_repair_kernel_summary(
        ProjectDirectorRepairKernelSummaryV1(
            stage="quality",
            mode="commit",
            artifact_quality_errors=(),
            artifact_quality_issues=(typed_issue,),
            tool_results=(),
        )
    )

    assert result.summary["coverage_report"]["total_diagnostics"] == 1
    assert captured["artifact_quality_errors"] == []
    repair_diagnostics = captured["repair_diagnostics"]
    assert isinstance(repair_diagnostics, tuple)
    assert repair_diagnostics[0].code == "typescript_ts2304"
    assert repair_diagnostics[0].path == "src/app.ts"
    assert repair_diagnostics[0].metadata["line"] == 1


def test_receipt_summary_preserves_embedded_runtime_kernel_receipt_identity() -> None:
    summary = build_director_repair_kernel_summary(
        stage="quality",
        mode="commit",
        tool_results=[
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_go_bare_import_string_repair",
                    "file": "cmd/app/main.go",
                    "bytes_written": 42,
                    "before_hash": "before",
                    "after_hash": "after",
                    "repair_kernel": {
                        "owner_cell": "director.runtime",
                        "receipt_id": "repair_receipt_kernel_go",
                        "plan_id": "plan_kernel_go",
                        "status": "applied",
                        "authoritative": True,
                        "before_hashes": {"cmd/app/main.go": "before"},
                        "after_hashes": {"cmd/app/main.go": "after"},
                        "metadata": {"rule_runtime": "go"},
                    },
                },
            }
        ],
    )

    assert summary["authoritative"] is False
    assert summary["requires_revalidation"] is True
    assert summary["pending_revalidation_count"] == 1
    assert summary["revalidation_coverage"]["requires_revalidation"] is True
    assert summary["revalidation_coverage"]["receipts_missing_revalidation"] == 1
    assert summary["revalidation_coverage"]["source_tools_missing_revalidation"] == [
        "deterministic_go_bare_import_string_repair"
    ]
    receipt = summary["receipts"][0]
    assert receipt["receipt_id"] == "repair_receipt_kernel_go"
    assert receipt["plan_id"] == "plan_kernel_go"
    assert receipt["status"] == "pending_revalidation"
    assert receipt["metadata"]["execution_status"] == "applied"
    assert receipt["metadata"]["projection_source"] == "embedded_repair_kernel"
    assert receipt["metadata"]["requires_revalidation"] is True
    assert receipt["before_hashes"] == {"cmd/app/main.go": "before"}
    assert receipt["after_hashes"] == {"cmd/app/main.go": "after"}
    assert summary["dark_launch_comparison"]["kernel_paths"] == ["cmd/app/main.go"]
    assert summary["dark_launch_comparison"]["kernel_source_tools"] == ["deterministic_go_bare_import_string_repair"]


def test_receipt_summary_preserves_revalidation_evidence_counts() -> None:
    summary = build_director_repair_kernel_summary(
        stage="post_execution_language_repairs",
        mode="commit",
        tool_results=[
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_rust_dependency_repair",
                    "file": "Cargo.toml",
                    "action": "add_dependency",
                    "round_number": 2,
                    "revalidation": {
                        "command": ["cargo", "check", "--quiet"],
                        "exit_code": 0,
                        "round_number": 2,
                        "errors_before": 3,
                        "errors_after": 1,
                        "net_error_reduction": 2,
                        "max_rounds": 3,
                    },
                },
            }
        ],
    )

    assert summary["authoritative"] is False
    assert summary["requires_revalidation"] is False
    assert summary["pending_revalidation_count"] == 0
    assert summary["receipts_with_revalidation"] == 1
    assert summary["revalidation_coverage"]["receipt_count"] == 1
    assert summary["revalidation_coverage"]["receipts_missing_revalidation"] == 0
    assert summary["revalidation_coverage"]["failed_revalidation_receipt_count"] == 1
    assert summary["revalidation_coverage"]["post_check_evidence_available"] is True
    assert summary["revalidation_coverage"]["post_check_evidence_complete"] is True
    assert summary["revalidation_coverage"]["source_tools_with_revalidation"] == [
        "deterministic_rust_dependency_repair"
    ]
    assert summary["receipt_count"] == 1
    receipt = summary["receipts"][0]
    assert receipt["status"] == "failed_revalidation"
    assert receipt["authoritative"] is False
    assert receipt["round_number"] == 2
    assert receipt["errors_before"] == 3
    assert receipt["errors_after"] == 1
    assert receipt["net_error_reduction"] == 2
    assert receipt["revalidation_evidence"]["command"] == ["cargo", "check", "--quiet"]
    assert receipt["revalidation_evidence"]["metadata"]["max_rounds"] == 3
    shadow = summary["dark_launch_comparison"]
    assert shadow["matched"] is True
    assert shadow["baseline_source_tools"] == ["deterministic_rust_dependency_repair"]
    assert shadow["kernel_source_tools"] == ["deterministic_rust_dependency_repair"]
    assert shadow["baseline_paths"] == ["Cargo.toml"]
    assert shadow["kernel_paths"] == ["Cargo.toml"]
    assert shadow["metadata"]["read_only"] is True
    assert shadow["metadata"]["writes_performed"] is False


def test_receipt_summary_with_failed_revalidation_is_not_authoritative() -> None:
    summary = build_director_repair_kernel_summary(
        stage="post_execution_language_repairs",
        mode="commit",
        tool_results=[
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_rust_dependency_repair",
                    "file": "Cargo.toml",
                    "action": "add_dependency",
                    "revalidation": {
                        "command": ["cargo", "check", "--quiet"],
                        "exit_code": 1,
                        "errors_before": 3,
                        "errors_after": 2,
                    },
                },
            }
        ],
    )

    assert summary["authoritative"] is False
    assert summary["requires_revalidation"] is False
    assert summary["receipts_with_revalidation"] == 1
    assert summary["revalidation_coverage"]["failed_revalidation_receipt_count"] == 1
    assert summary["revalidation_coverage"]["failed_revalidation_receipt_ids"] == [summary["receipts"][0]["receipt_id"]]
    assert summary["revalidation_coverage"]["failed_revalidation_source_tools"] == [
        "deterministic_rust_dependency_repair"
    ]
    assert summary["revalidation_coverage"]["post_check_evidence_complete"] is True
    assert summary["receipts"][0]["status"] == "failed_revalidation"
    assert summary["receipts"][0]["authoritative"] is False


def test_public_revalidation_projection_updates_receipts_and_context() -> None:
    summary = build_director_repair_kernel_summary(
        stage="materialization_quality_repairs",
        mode="commit",
        artifact_quality_errors=[
            "TypeScript syntax check failed: src/app.ts(1,14): error TS2304: Cannot find name 'Widget'."
        ],
        tool_results=[
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_typescript_missing_export_repair",
                    "file": "src/app.ts",
                    "operation": "modify",
                },
            }
        ],
    )
    before_authority_hash = summary["receipts"][0]["authority_hash"]
    assert summary["authoritative"] is False
    assert summary["pending_revalidation_count"] == 1
    assert summary["revalidation_coverage"]["receipts_missing_revalidation"] == 1
    assert summary["receipts"][0]["status"] == "pending_revalidation"

    result = project_director_repair_revalidation_evidence(
        AttachDirectorRepairRevalidationEvidenceV1(
            summary={"repair_kernel": summary, "stage": "deterministic_quality_repair"},
            residual_artifact_quality_errors=(),
            command=("tsc", "--noEmit"),
            metadata={"stage": "director_materialization_quality"},
        )
    )
    assert isinstance(result, DirectorRepairRevalidationProjectionResultV1)
    assert result.access == "read_only"
    assert result.writes_allowed is False
    assert result.registration_allowed is False
    assert result.agi_execution_authority is False
    updated = result.summary
    repair_kernel = updated["repair_kernel"]
    receipt = repair_kernel["receipts"][0]

    assert repair_kernel["authoritative"] is True
    assert repair_kernel["requires_revalidation"] is False
    assert repair_kernel["pending_revalidation_count"] == 0
    assert repair_kernel["receipts_with_revalidation"] == 1
    assert repair_kernel["revalidation_coverage"]["receipts_missing_revalidation"] == 0
    assert repair_kernel["revalidation_coverage"]["post_check_evidence_available"] is True
    assert repair_kernel["revalidation_coverage"]["post_check_evidence_complete"] is True
    assert repair_kernel["revalidation_coverage"]["status_counts"] == {"applied": 1}
    assert receipt["status"] == "applied"
    assert receipt["authoritative"] is True
    assert receipt["revalidation_evidence"]["command"] == ["tsc", "--noEmit"]
    assert receipt["revalidation_evidence"]["exit_code"] == 0
    assert receipt["errors_before"] == 1
    assert receipt["errors_after"] == 0
    assert receipt["net_error_reduction"] == 1
    assert receipt["revalidation_evidence"]["metadata"]["resolved_diagnostic_signatures"]
    assert receipt["revalidation_evidence"]["metadata"]["residual_diagnostic_signatures"] == []
    assert receipt["authority_hash"] != before_authority_hash
    assert repair_kernel["revalidation"]["post_check_evidence_attached"] is True
    assert repair_kernel["revalidation"]["coverage"]["receipts_with_revalidation"] == 1
    context_receipt = repair_kernel["receipt_context"]["receipts"][0]
    assert context_receipt["post_check_evidence"]["available"] is True
    assert context_receipt["errors_after"] == 0

    compatibility_summary = attach_director_repair_revalidation_evidence(
        {"repair_kernel": summary, "stage": "deterministic_quality_repair"},
        residual_artifact_quality_errors=[],
        command=("tsc", "--noEmit"),
        metadata={"stage": "director_materialization_quality"},
    )
    assert compatibility_summary["repair_kernel"]["receipts"][0]["errors_after"] == 0


def test_public_revalidation_projection_marks_failed_post_check_as_non_authoritative() -> None:
    diagnostic = "TypeScript syntax check failed: src/app.ts(1,14): error TS2304: Cannot find name 'Widget'."
    summary = build_director_repair_kernel_summary(
        stage="materialization_quality_repairs",
        mode="commit",
        artifact_quality_errors=[diagnostic],
        tool_results=[
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_typescript_missing_export_repair",
                    "file": "src/app.ts",
                    "operation": "modify",
                },
            }
        ],
    )

    result = project_director_repair_revalidation_evidence(
        AttachDirectorRepairRevalidationEvidenceV1(
            summary={"repair_kernel": summary, "stage": "deterministic_quality_repair"},
            residual_artifact_quality_errors=(diagnostic,),
            command=("tsc", "--noEmit"),
        )
    )
    repair_kernel = result.summary["repair_kernel"]
    receipt = repair_kernel["receipts"][0]

    assert repair_kernel["authoritative"] is False
    assert repair_kernel["requires_revalidation"] is False
    assert repair_kernel["pending_revalidation_count"] == 0
    assert repair_kernel["revalidation"]["exit_code"] == 1
    assert repair_kernel["revalidation_coverage"]["receipts_missing_revalidation"] == 0
    assert repair_kernel["revalidation_coverage"]["failed_revalidation_receipt_count"] == 1
    assert repair_kernel["revalidation_coverage"]["post_check_evidence_complete"] is True
    assert receipt["status"] == "failed_revalidation"
    assert receipt["authoritative"] is False
    assert receipt["revalidation_evidence"]["exit_code"] == 1
    assert receipt["errors_after"] == 1
    assert receipt["net_error_reduction"] == 0


def test_public_revalidation_projection_accepts_typed_residual_issues() -> None:
    diagnostic = "TypeScript syntax check failed: src/app.ts(1,14): error TS2304: Cannot find name 'Widget'."
    typed_issue = {
        "source": "artifact_quality",
        "code": "typescript_ts2304",
        "message": "Cannot find name 'Widget'.",
        "path": "src/app.ts",
        "metadata": {"raw": diagnostic, "line": 1, "column": 14},
    }
    summary = build_director_repair_kernel_summary(
        stage="materialization_quality_repairs",
        mode="commit",
        artifact_quality_errors=[diagnostic],
        tool_results=[
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_typescript_missing_export_repair",
                    "file": "src/app.ts",
                    "operation": "modify",
                },
            }
        ],
    )

    result = project_director_repair_revalidation_evidence(
        AttachDirectorRepairRevalidationEvidenceV1(
            summary={"repair_kernel": summary, "stage": "deterministic_quality_repair"},
            residual_artifact_quality_errors=(),
            residual_artifact_quality_issues=(typed_issue,),
            command=("tsc", "--noEmit"),
        )
    )
    receipt = result.summary["repair_kernel"]["receipts"][0]
    diagnostics_after = receipt["revalidation_evidence"]["diagnostics_after"]

    assert receipt["status"] == "failed_revalidation"
    assert receipt["errors_after"] == 1
    assert diagnostics_after[0]["code"] == "typescript_ts2304"
    assert diagnostics_after[0]["path"] == "src/app.ts"
    assert diagnostics_after[0]["metadata"]["raw"] == diagnostic


def test_public_revalidation_projection_uses_diagnostic_signatures_when_ids_are_missing() -> None:
    diagnostic = "TypeScript syntax check failed: src/app.ts(1,14): error TS2304: Cannot find name 'Widget'."
    summary = build_director_repair_kernel_summary(
        stage="materialization_quality_repairs",
        mode="commit",
        artifact_quality_errors=[diagnostic],
        tool_results=[
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_typescript_missing_export_repair",
                    "file": "src/app.ts",
                    "operation": "modify",
                },
            }
        ],
    )
    summary["receipts"][0]["diagnostics"][0]["diagnostic_id"] = ""

    result = project_director_repair_revalidation_evidence(
        AttachDirectorRepairRevalidationEvidenceV1(
            summary={"repair_kernel": summary, "stage": "deterministic_quality_repair"},
            residual_artifact_quality_errors=(diagnostic,),
            command=("tsc", "--noEmit"),
        )
    )
    repair_kernel = result.summary["repair_kernel"]
    receipt = repair_kernel["receipts"][0]
    evidence = receipt["revalidation_evidence"]

    assert evidence["residual_diagnostic_ids"] == []
    assert evidence["resolved_diagnostic_ids"] == []
    assert evidence["metadata"]["diagnostic_match_strategy"] == "stable_signature"
    assert evidence["metadata"]["residual_diagnostic_signatures"]
    assert evidence["metadata"]["resolved_diagnostic_signatures"] == []
    assert repair_kernel["revalidation_coverage"]["residual_diagnostic_receipt_count"] == 1
    assert receipt["status"] == "failed_revalidation"


def test_public_typescript_comma_planner_returns_composed_patch_projection() -> None:
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_typescript_return_object_semicolon_repair",
            base_files={
                "src/models/Flight.ts": (
                    "export function summarizeFlight() {\n"
                    "  const range = 10;\n"
                    "  const maxAltitude = 2;\n"
                    "  return {\n"
                    "    range\n"
                    "    maxAltitude: maxAltitude,\n"
                    "  };\n"
                    "}\n"
                )
            },
            artifact_quality_errors=(
                "TypeScript syntax check failed: src/models/Flight.ts(6,5): error TS1005: ',' expected.",
            ),
        )
    )

    payload = result.to_dict()

    assert result.ok is True
    assert result.planned is True
    assert result.source_tool == "deterministic_typescript_return_object_semicolon_repair"
    assert result.agi_execution_authority is False
    assert result.advisory_authoritative is False
    assert payload["composition_summary"]["patches"][0]["path"] == "src/models/Flight.ts"
    assert "    range,\n" in payload["composition_summary"]["patches"][0]["content_after"]
    assert payload["composition_summary"]["patches"][0]["before_hash"]
    assert payload["composition_summary"]["patches"][0]["after_hash"]


def test_public_repair_coverage_report_exposes_uncovered_diagnostics() -> None:
    result = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=(
                "TypeScript syntax check failed: src/models/Flight.ts(6,5): error TS1005: ',' expected.",
                "src/app.ts(3,14): error TS9999: Unknown future compiler error.",
            )
        )
    )
    payload = result.to_dict()

    assert isinstance(result, DirectorRepairCoverageReportV1)
    assert payload["schema_version"] == "director.repair_coverage_report.v1"
    assert payload["source"] == "director.runtime.repair_kernel.registry"
    assert payload["access"] == "read_only"
    assert payload["owner_cell"] == "director.runtime"
    assert payload["execution_boundary"] == "read_only_coverage_no_writes"
    assert payload["agi_execution_authority"] is False
    assert payload["director_tool_execution_required"] is False
    assert payload["total_diagnostics"] == 2
    assert payload["covered_diagnostic_count"] == 1
    assert payload["uncovered_diagnostic_count"] == 1
    assert payload["coverage_gap_count"] == 1
    assert payload["rule_discovery_required"] is True
    assert payload["coverage_gap_languages"] == ["typescript"]
    assert payload["coverage_gap_recommended_routes"] == ["runtime_rule"]
    assert payload["coverage_gap_slot_statuses"] == ["reserved_slot_available"]
    assert payload["executable_runtime_plan_diagnostic_count"] == 1
    assert payload["metadata_only_diagnostic_count"] == 0
    assert payload["items"][0]["known_rule_matched"] is True
    assert payload["items"][0]["executable_runtime_plan_matched"] is True
    assert payload["items"][0]["metadata_only_match"] is False
    assert payload["items"][0]["recommended_route"] == "runtime_rule"
    assert payload["items"][0]["coverage_status"] == "executable_runtime"
    assert payload["items"][0]["matched_source_tools"] == [
        "deterministic_typescript_hyphenated_identifier_repair",
        "deterministic_typescript_return_object_semicolon_repair",
    ]
    assert payload["items"][1]["known_rule_matched"] is False
    assert payload["items"][1]["metadata_only_match"] is False
    assert payload["items"][1]["executable_runtime_plan_matched"] is False
    assert payload["items"][1]["language"] == "typescript"
    assert payload["items"][1]["diagnostic_language"] == "typescript"
    assert payload["items"][1]["diagnostic_code"] == "typescript_ts9999"
    assert payload["items"][1]["diagnostic_phase"] == "quality_repair"
    assert payload["items"][1]["phase_suggestion"] == "quality_repair"
    assert payload["items"][1]["diagnostic_archetype"] == "object_literal_syntax"
    assert payload["items"][1]["archetype_suggestion"] == "object_literal_syntax"
    assert payload["items"][1]["reserved_slot_available"] is True
    assert payload["items"][1]["slot_status"] == "reserved_slot_available"
    assert payload["items"][1]["recommended_route"] == "runtime_rule"
    assert payload["items"][1]["coverage_status"] == "coverage_gap"
    assert payload["uncovered_diagnostics"][0]["code"] == "typescript_ts9999"
    assert payload["coverage_gaps"][0]["diagnostic"]["code"] == "typescript_ts9999"
    assert payload["coverage_gaps"][0]["language"] == "typescript"
    assert payload["coverage_gaps"][0]["diagnostic_code"] == "typescript_ts9999"
    assert payload["coverage_gaps"][0]["phase_suggestion"] == "quality_repair"
    assert payload["coverage_gaps"][0]["archetype_suggestion"] == "object_literal_syntax"
    assert payload["coverage_gaps"][0]["reserved_slot_available"] is True
    assert payload["coverage_gaps"][0]["slot_status"] == "reserved_slot_available"
    assert payload["coverage_gaps"][0]["recommended_route"] == "runtime_rule"
    assert payload["coverage_gaps"][0]["coverage_status"] == "coverage_gap"
    assert payload["coverage_gaps"][0]["audit_reason"] == "known_rule_matched=false"
    assert payload["coverage_gaps"][0]["missing_capability"] == "deterministic_repair_rule"


def test_public_repair_coverage_accepts_typed_artifact_quality_issues() -> None:
    result = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=(),
            artifact_quality_issues=(
                {
                    "source": "artifact_quality",
                    "code": "typescript_ts9999",
                    "message": "Unknown future compiler error.",
                    "path": "src/app.ts",
                    "severity": "error",
                    "metadata": {"line": 3, "column": 14},
                },
            ),
        )
    )
    payload = result.to_dict()

    assert payload["total_diagnostics"] == 1
    assert payload["items"][0]["diagnostic"]["code"] == "typescript_ts9999"
    assert payload["items"][0]["diagnostic"]["path"] == "src/app.ts"
    assert payload["items"][0]["diagnostic_code"] == "typescript_ts9999"
    assert payload["uncovered_diagnostics"][0]["code"] == "typescript_ts9999"


def test_public_repair_coverage_suggests_rust_missing_method_self_family() -> None:
    result = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=(
                "Rust syntax check failed: src/lib.rs:12: expected one of `self`, `&self`, or `&mut self`; found `&)`.",
            )
        )
    )
    payload = result.to_dict()
    item = payload["items"][0]

    assert payload["covered_diagnostic_count"] == 0
    assert payload["uncovered_diagnostic_count"] == 1
    assert payload["executable_runtime_plan_diagnostic_count"] == 0
    assert payload["metadata_only_diagnostic_count"] == 0
    assert item["known_rule_matched"] is False
    assert item["diagnostic_language"] == "rust"
    assert item["diagnostic_archetype"] == "missing_method_self"
    assert item["diagnostic_phase"] == "quality_repair"
    assert item["suggested_rule_family"] == "missing_method_self"


def test_public_repair_language_slots_reserve_future_languages_without_registering_rules() -> None:
    result = query_director_repair_language_slots(QueryDirectorRepairLanguageSlotsV1())
    payload = result.to_dict()

    assert isinstance(result, DirectorRepairLanguageSlotsResultV1)
    assert payload["schema_version"] == "director.repair_language_slots.v1"
    assert payload["source"] == "director.runtime.repair_kernel.registry"
    assert payload["access"] == "read_only"
    assert payload["authoritative_rule_registration"] is False
    assert payload["agi_execution_authority"] is False
    assert payload["writes_allowed"] is False
    direct_slot_payload = DirectorRepairLanguageSlotV1(language="new-lang").to_dict()
    assert direct_slot_payload["repairer_module"].endswith(".new_lang_runtime")
    assert direct_slot_payload["implementation_status"] == "reserved_only"
    assert direct_slot_payload["slot_owner_cell"] == "director.runtime"
    assert direct_slot_payload["bench_evidence_required"] is True
    assert direct_slot_payload["rule_authoring_status"] == "reserved_only"
    assert direct_slot_payload["next_action"] == "add_bench_verified_rule_metadata_then_runtime_binding"
    languages = {item["language"] for item in payload["items"]}
    assert {
        "typescript",
        "go",
        "rust",
        "cpp",
        "java",
        "python",
        "shell",
        "sql",
        "csharp",
        "php",
        "ruby",
        "swift",
        "kotlin",
        "dart",
        "lua",
        "r",
        "vue",
        "svelte",
        "scala",
        "groovy",
        "elixir",
        "erlang",
        "haskell",
        "ocaml",
        "fsharp",
        "zig",
        "nim",
        "crystal",
        "perl",
        "powershell",
        "julia",
        "objective_c",
        "fortran",
        "matlab",
        "terraform",
        "dockerfile",
        "make",
        "yaml",
        "json",
        "toml",
        "nix",
        "starlark",
        "clojure",
        "elm",
        "rescript",
        "gleam",
        "solidity",
        "vyper",
        "qml",
        "proto",
        "graphql",
    }.issubset(languages)
    slots_by_language = {item["language"]: item for item in payload["items"]}
    assert slots_by_language["dockerfile"]["file_names"] == ["dockerfile", "containerfile"]
    assert slots_by_language["dockerfile"]["repairer_module"].endswith(".dockerfile_runtime")
    assert slots_by_language["dockerfile"]["implementation_status"] == "reserved_only"
    assert slots_by_language["dockerfile"]["registration_policy"] == "bench_verified_rule_required"
    assert slots_by_language["dockerfile"]["bench_evidence_required"] is True
    assert slots_by_language["dockerfile"]["rule_authoring_status"] == "reserved_only"
    assert slots_by_language["dockerfile"]["next_action"] == "add_bench_verified_rule_metadata_then_runtime_binding"
    assert slots_by_language["dockerfile"]["authoritative_source_tools"] == []
    assert slots_by_language["dockerfile"]["executable_runtime_source_tools"] == []
    assert slots_by_language["cpp"]["implementation_status"] == "executable_runtime"
    assert slots_by_language["cpp"]["next_action"] == "extend_existing_runtime_rule_with_bench_evidence"
    assert slots_by_language["rust"]["implementation_status"] == "executable_runtime"
    assert slots_by_language["rust"]["rule_authoring_status"] == "executable_runtime"
    assert slots_by_language["rust"]["executable_runtime_source_tools"] == [
        "deterministic_rust_crate_import_repair",
        "deterministic_rust_crate_import_rewrite_repair",
        "deterministic_rust_dependency_repair",
        "deterministic_rust_derive_repair",
        "deterministic_rust_duplicate_module_file_repair",
        "deterministic_rust_field_rename_suggestion_repair",
        "deterministic_rust_incompatible_copy_derive_repair",
        "deterministic_rust_lib_root_facade_repair",
        "deterministic_rust_line_suggestion_repair",
        "deterministic_rust_method_self_signature_repair",
        "deterministic_rust_missing_binary_entrypoint_repair",
        "deterministic_rust_missing_fields_repair",
        "deterministic_rust_missing_lib_target_repair",
        "deterministic_rust_missing_module_file_repair",
        "deterministic_rust_serde_derive_repair",
        "deterministic_rust_struct_literal_missing_field_repair",
        "deterministic_rust_trait_import_repair",
        "deterministic_rust_unresolved_pub_use_repair",
        "deterministic_rust_unused_import_repair",
        "deterministic_rust_wrong_crate_path_repair",
    ]
    assert "deterministic_rust_derive_repair" in slots_by_language["rust"]["executable_runtime_source_tools"]
    assert set(payload["summary"]["authoritative_rule_languages"]).issubset(languages)
    assert payload["summary"]["language_count"] >= 45
    assert payload["summary"]["next_actions_by_language"]["dockerfile"] == (
        "add_bench_verified_rule_metadata_then_runtime_binding"
    )
    assert payload["summary"]["next_actions_by_language"]["rust"] == "extend_existing_runtime_rule_with_bench_evidence"
    assert payload["summary"]["authoritative_rule_languages"] == [
        "cpp",
        "go",
        "html",
        "java",
        "javascript",
        "python",
        "rust",
        "typescript",
    ]
    assert payload["summary"]["executable_runtime_languages"] == [
        "cpp",
        "go",
        "html",
        "java",
        "javascript",
        "python",
        "rust",
        "typescript",
    ]
    assert "scala" in payload["summary"]["reserved_only_languages"]
    assert "php" in payload["summary"]["reserved_only_languages"]
    assert "kotlin" in payload["summary"]["reserved_only_languages"]
    assert "fortran" in payload["summary"]["reserved_only_languages"]
    assert "terraform" in payload["summary"]["reserved_only_languages"]
    assert "dockerfile" in payload["summary"]["reserved_only_languages"]
    assert "graphql" in payload["summary"]["reserved_only_languages"]
    assert payload["summary"]["reserved_only_language_count"] >= 40
    assert payload["summary"]["implementation_status_counts"]["executable_runtime"] == 8
    assert payload["summary"]["implementation_status_counts"].get("metadata_rule_registered", 0) == 0
    assert payload["summary"]["implementation_status_counts"]["reserved_only"] >= 40
    assert payload["summary"]["implementation_status_by_language"]["dockerfile"] == "reserved_only"
    assert payload["summary"]["implementation_status_by_language"]["cpp"] == "executable_runtime"
    assert payload["summary"]["implementation_status_by_language"]["rust"] == "executable_runtime"
    assert payload["summary"]["repairer_modules"]["starlark"].endswith(".starlark_runtime")
    assert payload["summary"]["reserved_only_repairer_modules"]["graphql"].endswith(".graphql_runtime")
    assert payload["summary"]["bench_driven_rule_addition_required"] is True

    diagnostic = RepairDiagnostic(
        source="shellcheck",
        code="shell_sc1009",
        message="The mentioned syntax error was in this if expression.",
        path="scripts/deploy.sh",
    )
    coverage_item = default_repair_rule_registry().coverage([diagnostic]).to_dict()["items"][0]
    assert coverage_item["diagnostic_language"] == "shell"
    assert coverage_item["known_rule_matched"] is False
    assert coverage_item["matched_source_tools"] == []

    dockerfile_diagnostic = RepairDiagnostic(
        source="hadolint",
        code="hadolint_dl3008",
        message="Pin versions in apt get install.",
        path="Dockerfile",
    )
    dockerfile_coverage = default_repair_rule_registry().coverage([dockerfile_diagnostic]).to_dict()["items"][0]
    assert dockerfile_coverage["diagnostic_language"] == "dockerfile"
    assert dockerfile_coverage["known_rule_matched"] is False

    starlark_diagnostic = RepairDiagnostic(
        source="bazel",
        code="bazel_missing_load",
        message="name 'py_library' is not defined",
        path="services/api/BUILD.bazel",
    )
    starlark_coverage = default_repair_rule_registry().coverage([starlark_diagnostic]).to_dict()["items"][0]
    assert starlark_coverage["diagnostic_language"] == "starlark"
    assert starlark_coverage["known_rule_matched"] is False


def test_public_post_execution_schedule_is_runtime_owned_and_read_only() -> None:
    result = query_director_repair_post_execution_schedule(QueryDirectorRepairPostExecutionScheduleV1())
    payload = result.to_dict()

    assert isinstance(result, DirectorRepairPostExecutionScheduleResultV1)
    assert payload["schema_version"] == "director.repair_post_execution_schedule.v1"
    assert payload["source"] == "director.runtime.repair_kernel.scheduler"
    assert payload["access"] == "read_only"
    assert payload["owner_cell"] == "director.runtime"
    assert payload["execution_boundary"] == "read_only_post_execution_schedule_no_runner_binding"
    assert payload["runner_binding_owner"] == "roles.adapters"
    assert payload["writes_allowed"] is False
    assert payload["registration_allowed"] is False
    assert payload["agi_execution_authority"] is False
    assert [item["step_id"] for item in payload["items"]] == [
        "go.module_import",
        "rust.dependency_resolution",
        "rust.post_execution_convergence",
        "cpp.post_execution",
        "java.post_execution",
    ]
    assert payload["items"][0]["phase"] == "dependency_resolution"
    assert payload["items"][1]["phase"] == "dependency_resolution"
    assert payload["items"][2]["phase"] == "multi_phase_convergence"
    assert payload["items"][1]["source_tool_kind"] == "executable_runtime"
    assert payload["items"][1]["executable_runtime_source_tool"] is True
    assert payload["items"][2]["source_tool"] == "deterministic_rust_post_repair"
    assert payload["items"][2]["source_tool_kind"] == "callback_schedule_label"
    assert payload["items"][2]["executable_runtime_source_tool"] is False
    assert payload["items"][3]["priority"] == 1
    assert payload["summary"]["source_tool_kind_counts"] == {
        "callback_schedule_label": 1,
        "executable_runtime": 4,
    }
    assert payload["summary"]["callback_schedule_label_source_tools"] == ["deterministic_rust_post_repair"]
    assert "deterministic_rust_post_repair" not in payload["summary"]["executable_runtime_source_tools"]
    assert payload["summary"]["runtime_schedule_authoritative"] is True
    assert payload["summary"]["runner_binding_owner"] == "roles.adapters"
    assert payload["summary"]["target_scheduler"] == "director.runtime.repair_kernel.scheduler"
    assert payload["summary"]["default_max_rounds"] == 1
    assert payload["summary"]["convergence_loop_owned_by"] == "director.runtime.repair_kernel.scheduler"
    assert payload["summary"]["cycle_breaker"] == "repeated_round_fingerprint"


def test_runtime_post_execution_schedule_runs_callbacks_and_injects_step_metadata() -> None:
    observed_step_ids: list[str] = []

    def runner(step) -> list[dict[str, object]]:
        observed_step_ids.append(step.step_id)
        if step.step_id != "rust.dependency_resolution":
            return []
        return [
            {
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "source_tool": "deterministic_rust_dependency_repair",
                    "file": "Cargo.toml",
                },
            }
        ]

    run = run_post_execution_repair_schedule_callbacks(
        runner_step_ids=(
            "go.module_import",
            "rust.dependency_resolution",
            "rust.post_execution_convergence",
            "cpp.post_execution",
            "java.post_execution",
        ),
        runner=runner,
    )

    assert observed_step_ids == [
        "go.module_import",
        "rust.dependency_resolution",
        "rust.post_execution_convergence",
        "cpp.post_execution",
        "java.post_execution",
    ]
    assert [step.step_id for step in run.ordered_steps] == observed_step_ids
    assert len(run.tool_results) == 1
    payload = run.tool_results[0]["result"]
    assert payload["bridge_step_id"] == "rust.dependency_resolution"
    assert payload["language"] == "rust"
    assert payload["phase"] == "dependency_resolution"
    assert payload["priority"] == 0
    assert payload["scheduled_source_tool"] == "deterministic_rust_dependency_repair"
    assert payload["scheduled_source_tool_kind"] == "executable_runtime"
    assert payload["scheduled_source_tool_executable_runtime"] is True
    assert payload["schedule_source_tool_kind"] == "executable_runtime"
    assert payload["schedule_source_tool_is_runtime_executable"] is True
    assert payload["round_number"] == 1
    assert payload["max_rounds"] == 1
    assert payload["scheduler_round_number"] == 1
    assert payload["scheduler_max_rounds"] == 1
    assert payload["scheduler_rounds_run"] == 1
    assert payload["convergence_status"] == "max_rounds_reached"
    assert payload["convergence_stopped_reason"] == "max_rounds_reached"
    assert run.max_rounds == 1
    assert run.rounds_run == 1
    assert run.convergence_status == "max_rounds_reached"

    with pytest.raises(RuntimeError, match="runner is not declared"):
        run_post_execution_repair_schedule_callbacks(
            runner_step_ids=(
                "go.module_import",
                "rust.dependency_resolution",
                "rust.post_execution_convergence",
                "cpp.post_execution",
                "java.post_execution",
                "python.unregistered",
            ),
            runner=runner,
        )


def test_runtime_post_execution_schedule_runs_bounded_convergence_rounds() -> None:
    rust_rounds = 0

    def runner(step) -> list[dict[str, object]]:
        nonlocal rust_rounds
        if step.step_id != "rust.post_execution_convergence":
            return []
        rust_rounds += 1
        if rust_rounds >= 3:
            return []
        return [
            {
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "source_tool": "deterministic_rust_post_repair",
                    "file": "Cargo.toml",
                    "after_hash": f"hash-{rust_rounds}",
                },
            }
        ]

    run = run_post_execution_repair_schedule_callbacks(
        runner_step_ids=(
            "go.module_import",
            "rust.dependency_resolution",
            "rust.post_execution_convergence",
            "cpp.post_execution",
            "java.post_execution",
        ),
        runner=runner,
        max_rounds=3,
    )

    assert len(run.tool_results) == 2
    assert run.max_rounds == 3
    assert run.rounds_run == 3
    assert run.convergence_status == "converged"
    assert run.stopped_reason == "converged_no_repairs_applied"
    assert [item["result"]["scheduler_round_number"] for item in run.tool_results] == [1, 2]
    assert all(item["result"]["scheduler_rounds_run"] == 3 for item in run.tool_results)
    assert all(item["result"]["convergence_status"] == "converged" for item in run.tool_results)


def test_runtime_post_execution_schedule_breaks_repeated_round_cycles() -> None:
    def runner(step) -> list[dict[str, object]]:
        if step.step_id != "rust.post_execution_convergence":
            return []
        return [
            {
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "source_tool": "deterministic_rust_post_repair",
                    "file": "Cargo.toml",
                    "after_hash": "same-hash",
                },
            }
        ]

    run = run_post_execution_repair_schedule_callbacks(
        runner_step_ids=(
            "go.module_import",
            "rust.dependency_resolution",
            "rust.post_execution_convergence",
            "cpp.post_execution",
            "java.post_execution",
        ),
        runner=runner,
        max_rounds=3,
    )

    assert len(run.tool_results) == 1
    assert run.max_rounds == 3
    assert run.rounds_run == 2
    assert run.convergence_status == "cycle_broken"
    assert run.stopped_reason == "repeated_round_fingerprint"
    payload = run.tool_results[0]["result"]
    assert payload["scheduler_round_number"] == 1
    assert payload["scheduler_rounds_run"] == 2
    assert payload["convergence_status"] == "cycle_broken"
    assert payload["convergence_stopped_reason"] == "repeated_round_fingerprint"


def test_public_materialization_quality_schedule_is_runtime_owned_and_read_only() -> None:
    result = query_director_repair_materialization_quality_schedule(
        QueryDirectorRepairMaterializationQualityScheduleV1()
    )
    payload = result.to_dict()
    expected_step_ids = [
        "materialization.hygiene_scaffold",
        "materialization.typescript_scaffold",
        "materialization.typescript_compiler",
        "materialization.html_entrypoint",
        "materialization.node_manifest",
        "materialization.rust_compiler",
        "materialization.target_runtime",
        "materialization.python_import",
        "materialization.go_import",
    ]

    assert isinstance(result, DirectorRepairMaterializationQualityScheduleResultV1)
    assert payload["schema_version"] == "director.repair_materialization_quality_schedule.v1"
    assert payload["source"] == "director.runtime.repair_kernel.scheduler"
    assert payload["access"] == "read_only"
    assert payload["owner_cell"] == "director.runtime"
    assert payload["execution_boundary"] == "read_only_materialization_quality_schedule_no_runner_binding"
    assert payload["runner_binding_owner"] == "roles.adapters"
    assert payload["writes_allowed"] is False
    assert payload["registration_allowed"] is False
    assert payload["agi_execution_authority"] is False
    assert [item["step_id"] for item in payload["items"]] == expected_step_ids
    assert payload["items"][0]["phase"] == "hygiene"
    assert payload["items"][0]["source_tool_kind"] == "callback_schedule_label"
    assert payload["items"][0]["executable_runtime_source_tool"] is False
    assert payload["items"][0]["runtime_source_tools"] == [
        "deterministic_scaffold_marker_cleanup",
        "deterministic_scaffold_marker_quality_cleanup",
    ]
    assert "deterministic_javascript_esm_commonjs_entrypoint_repair" in payload["items"][2]["runtime_source_tools"]
    assert payload["items"][3]["source_tool"] == "deterministic_html_typescript_module_script_repair"
    assert payload["items"][3]["source_tool_kind"] == "callback_schedule_label"
    assert payload["items"][3]["runtime_source_tools"] == ["deterministic_html_typescript_module_script_repair"]
    assert "deterministic_python_package_shadow_bridge_repair" in payload["items"][7]["runtime_source_tools"]
    assert "deterministic_go_module_import_repair" in payload["items"][8]["runtime_source_tools"]
    assert payload["items"][3]["executable_runtime_source_tool"] is False
    assert payload["items"][4]["depends_on"] == ["materialization.html_entrypoint"]
    assert payload["items"][-1]["depends_on"] == ["materialization.python_import"]
    assert payload["summary"]["step_count"] == len(expected_step_ids)
    assert payload["summary"]["ordered_step_ids"] == expected_step_ids
    assert payload["summary"]["languages"] == ["go", "html", "javascript", "multi", "python", "rust", "typescript"]
    assert payload["summary"]["source_tool_kind_counts"] == {
        "callback_schedule_label": len(expected_step_ids),
        "executable_runtime": 0,
    }
    assert payload["summary"]["executable_runtime_source_tools"] == []
    assert payload["summary"]["callback_schedule_label_source_tools"] == payload["summary"]["source_tools"]
    assert "deterministic_javascript_esm_commonjs_entrypoint_repair" in payload["summary"]["runtime_source_tools"]
    assert payload["summary"]["runtime_source_tool_count"] == len(payload["summary"]["runtime_source_tools"])
    assert payload["summary"]["runtime_schedule_authoritative"] is True
    assert payload["summary"]["runner_binding_owner"] == "roles.adapters"
    assert payload["summary"]["target_scheduler"] == "director.runtime.repair_kernel.scheduler"
    assert payload["summary"]["default_max_rounds"] == 1
    assert payload["summary"]["convergence_loop_owned_by"] == "director.runtime.repair_kernel.scheduler"
    assert payload["summary"]["cycle_breaker"] == "repeated_round_fingerprint"


def test_public_materialization_bridge_metadata_projection_is_runtime_owned() -> None:
    schedule = query_director_repair_materialization_quality_schedule(
        QueryDirectorRepairMaterializationQualityScheduleV1()
    )

    result = project_director_repair_materialization_bridge_metadata(
        ProjectDirectorRepairMaterializationBridgeMetadataV1(
            ordered_steps=schedule.items,
            repair_kernel={
                "receipt_count": 2,
                "coverage_report": {"uncovered_diagnostic_count": 1},
            },
            schedule_reconciliation={"runner_step_ids": ["materialization.hygiene_scaffold"]},
            scheduler_bridge_evidence={
                "schema_version": "director.materialization_quality_scheduler_bridge.v1",
                "adapter_projection_bridge": True,
                "receipt_lifecycle_status_counts": {"missing_evidence": 1},
            },
            coverage_preaudit={"uncovered_diagnostic_count": 1, "rule_discovery_required": True},
            plan_probe_preaudit={
                "status": "covered_plannable",
                "covered_unplannable_diagnostic_count": 0,
                "plannable_source_tools": ["deterministic_scaffold_marker_cleanup"],
            },
            repair_kernel_migration_debt={
                "schema_version": "director.materialization_quality_repair_migration_debt.v1",
                "adapter_projection_debt": [{"step_id": "materialization.hygiene_scaffold"}],
            },
            receipt_lifecycle_by_step={
                "materialization.hygiene_scaffold": {"receipt_lifecycle_evidence_status": "missing_evidence"},
            },
            dark_launch_comparison={
                "cutover_ready": False,
                "cutover_blockers": ["adapter_projection_bridge"],
            },
            convergence_verifier_present=True,
        )
    )
    payload = result.to_dict()
    summary = payload["summary"]

    assert payload["schema_version"] == "director.materialization_quality_runtime_ports_metadata_projection.v1"
    assert payload["owner_cell"] == "director.runtime"
    assert payload["execution_boundary"] == "read_only_materialization_runtime_ports_metadata_no_writes"
    assert payload["agi_execution_authority"] is False
    assert payload["director_tool_execution_required"] is False
    assert summary["schema_version"] == "director.materialization_quality_runtime_ports.v1"
    assert summary["runtime_schedule_owner"] == "director.runtime"
    assert summary["runner_binding_owner"] == "roles.adapters"
    assert summary["director_runtime_public_summary_entrypoint"] == (
        "project_director_repair_materialization_bridge_metadata"
    )
    assert summary["ordered_step_ids"][0] == "materialization.hygiene_scaffold"
    assert summary["runner_step_ids"] == ["materialization.hygiene_scaffold"]
    assert summary["receipt_count"] == 2
    assert summary["coverage_uncovered_diagnostic_count"] == 1
    assert summary["scheduler_bridge_summary_owner"] == "director.runtime"
    assert summary["scheduler_bridge"]["adapter_projection_bridge"] is True
    assert summary["repair_kernel_migration_debt"]["schema_version"] == (
        "director.materialization_quality_repair_migration_debt.v1"
    )
    assert summary["adapter_projection_debt"] == [{"step_id": "materialization.hygiene_scaffold"}]
    assert (
        summary["receipt_lifecycle_by_step"]["materialization.hygiene_scaffold"]["receipt_lifecycle_evidence_status"]
        == "missing_evidence"
    )
    assert summary["dark_launch_cutover_blockers"] == ["adapter_projection_bridge"]


def test_runtime_materialization_quality_facade_runs_schedule_and_projects_evidence() -> None:
    schedule = query_director_repair_materialization_quality_schedule(
        QueryDirectorRepairMaterializationQualityScheduleV1()
    )
    runner_step_ids = tuple(item.step_id for item in schedule.items)
    deprecated_write_tool_name = sorted(DEPRECATED_WRITE_TOOLS)[0]

    def runner(step) -> list[dict[str, object]]:
        if step.step_id != "materialization.hygiene_scaffold":
            return []
        return [
            {
                "tool": deprecated_write_tool_name,
                "ok": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_scaffold_marker_cleanup",
                    "bridge_step_id": step.step_id,
                    "revalidation_evidence": {
                        "command": ["rtk", "pytest", "tests/test_hygiene.py"],
                        "exit_code": 0,
                        "errors_after": 0,
                    },
                },
            }
        ]

    result = run_director_materialization_quality_repair_facade(
        artifact_quality_errors=("Artifact quality scan failed: placeholder marker found",),
        runner_step_ids=runner_step_ids,
        runner=runner,
        plan_probe_preaudit={"status": "covered_plannable"},
        convergence_verifier_present=True,
    )
    payload = result.to_dict()

    assert isinstance(result, DirectorRepairMaterializationQualityFacadeResultV1)
    assert payload["schema_version"] == "director.materialization_quality_repair_facade_result.v1"
    assert payload["owner_cell"] == "director.runtime"
    assert payload["runner_binding_owner"] == "roles.adapters"
    assert payload["writes_allowed"] is False
    assert payload["director_tool_execution_required"] is True
    assert payload["schedule_reconciliation"]["exact_match"] is True
    assert payload["schedule_reconciliation"]["runner_order_matches_runtime"] is True
    assert payload["tool_results"][0]["runtime_step_id"] == "materialization.hygiene_scaffold"
    assert payload["tool_results"][0]["evidence_status"] == "resolved_evidence"
    assert payload["summary"]["runtime_facade_owner"] == "director.runtime"
    assert payload["summary"]["write_tool_evidence"] is True
    assert payload["summary"]["convergence_verifier_present"] is True
    assert payload["coverage_preaudit"]["schema_version"] == "director.repair_coverage_report.v1"
