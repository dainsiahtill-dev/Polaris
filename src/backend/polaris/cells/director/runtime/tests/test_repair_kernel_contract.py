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
    build_typescript_object_literal_comma_plan,
    normalize_artifact_quality_errors,
    repair_typescript_object_literal_commas,
)
from polaris.cells.director.runtime.internal.repair_kernel.advisory_policy import (
    FORBIDDEN_REPAIR_ADVISORY_METADATA_FIELDS,
)
from polaris.cells.director.runtime.internal.repair_kernel.contracts import sha256_text
from polaris.cells.director.runtime.public import (
    DirectorRepairPlanningResultV1,
    QueryDirectorRepairStrategyCatalogV1,
    RepairAdvisoryV1,
    build_director_repair_kernel_summary,
    plan_director_typescript_object_literal_comma_repair,
    query_director_repair_strategy_catalog,
    run_director_typescript_object_literal_comma_repair,
)


def test_normalizer_builds_typed_typescript_diagnostic() -> None:
    diagnostics = normalize_artifact_quality_errors(["src/app.ts(3,14): error TS2304: Cannot find name 'Widget'."])

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.source == "artifact_quality"
    assert diagnostic.code == "typescript_ts2304"
    assert diagnostic.path == "src/app.ts"
    assert diagnostic.line == 3
    assert diagnostic.column == 14


def test_patch_composer_applies_text_spans_descending() -> None:
    base = {"src/app.ts": "alpha beta gamma"}
    operations = [
        RepairOperation(
            kind="text_replace",
            path="src/app.ts",
            span_start=0,
            span_end=5,
            expected="alpha",
            replacement="one",
        ),
        RepairOperation(
            kind="text_replace",
            path="src/app.ts",
            span_start=11,
            span_end=16,
            expected="gamma",
            replacement="three",
        ),
    ]

    result = PatchComposer().compose(base, operations)

    assert result.ok
    assert len(result.patches) == 1
    assert result.patches[0].content_after == "one beta three"


def test_patch_composer_fails_closed_on_overlapping_text_spans() -> None:
    base = {"src/app.ts": "abcdef"}
    operations = [
        RepairOperation(kind="text_replace", path="src/app.ts", span_start=1, span_end=4, replacement="X"),
        RepairOperation(kind="text_replace", path="src/app.ts", span_start=3, span_end=5, replacement="Y"),
    ]

    result = PatchComposer().compose(base, operations)

    assert not result.ok
    assert result.issues[0].code == "overlapping_text_spans"


def test_patch_composer_merges_json_operations() -> None:
    base = {"package.json": '{"scripts":{"test":"echo fail"}}\n'}
    operations = [
        RepairOperation(kind="json_set", path="package.json", json_path=("scripts", "test"), value="npm run build"),
        RepairOperation(kind="json_set", path="package.json", json_path=("scripts", "build"), value="tsc"),
    ]

    result = PatchComposer().compose(base, operations)

    assert result.ok
    assert '"build": "tsc"' in result.patches[0].content_after
    assert '"test": "npm run build"' in result.patches[0].content_after


def test_typescript_object_literal_comma_rule_builds_canonical_plan() -> None:
    content = (
        "export function runFlight() {\n"
        "  const samples = [];\n"
        "  const range = 10;\n"
        "  const maxAltitude = 2;\n"
        "  const flightTime = 3;\n"
        "  return { samples, range, maxAltitude, flightTime  landed: undefined as unknown as boolean };\n"
        "}\n"
    )
    diagnostics = normalize_artifact_quality_errors(
        ["TypeScript syntax check failed: src/models/Flight.ts(6,47): error TS1005: ',' expected."]
    )

    plan = build_typescript_object_literal_comma_plan(
        base_files={"src/models/Flight.ts": content},
        diagnostics=diagnostics,
    )

    assert plan is not None
    assert plan.rule_id == "typescript.object_literal_missing_comma"
    assert plan.source_tool == "deterministic_typescript_return_object_semicolon_repair"
    assert plan.operations[0].kind == "write_file"
    assert "flightTime, landed:" in str(plan.operations[0].content)
    composition = PatchComposer().compose({"src/models/Flight.ts": content}, plan.operations)
    assert composition.ok
    assert "flightTime, landed:" in composition.patches[0].content_after


def test_typescript_object_literal_comma_plan_dedupes_same_file_diagnostics() -> None:
    content = (
        "export function summarizeFlight() {\n  return {\n    range\n    maxAltitude: 2\n    landed: true,\n  };\n}\n"
    )
    diagnostics = normalize_artifact_quality_errors(
        [
            "TypeScript syntax check failed: src/models/Flight.ts(4,5): error TS1005: ',' expected.",
            "TypeScript syntax check failed: src/models/Flight.ts(5,5): error TS1005: ',' expected.",
        ]
    )

    plan = build_typescript_object_literal_comma_plan(
        base_files={"src/models/Flight.ts": content},
        diagnostics=diagnostics,
    )

    assert plan is not None
    assert len(plan.operations) == 1
    assert len(plan.diagnostics) == 2
    composition = PatchComposer().compose({"src/models/Flight.ts": content}, plan.operations)
    assert composition.ok
    assert "    range,\n" in composition.patches[0].content_after
    assert "    maxAltitude: 2,\n" in composition.patches[0].content_after


def test_typescript_object_literal_comma_rule_repairs_previous_property_line() -> None:
    content = "export function summarizeFlight() {\n  return {\n    range\n    maxAltitude: 2,\n  };\n}\n"

    repaired = repair_typescript_object_literal_commas(content)

    assert "    range,\n" in repaired
    assert "    maxAltitude: 2,\n" in repaired


def test_public_typescript_object_literal_comma_planning_surface_builds_summary() -> None:
    content = (
        "export function runFlight() {\n"
        "  const samples = [];\n"
        "  const range = 10;\n"
        "  const maxAltitude = 2;\n"
        "  const flightTime = 3;\n"
        "  return { samples, range, maxAltitude, flightTime  landed: undefined as unknown as boolean };\n"
        "}\n"
    )
    advisory = RepairAdvisoryV1(
        advisor_source="agi",
        message="check the receipt after deterministic planning",
        confidence=0.4,
        metadata={"evidence_ref": "runtime/receipts/latest.json"},
    )

    result = plan_director_typescript_object_literal_comma_repair(
        base_files={"src/models/Flight.ts": content},
        artifact_quality_errors=[
            "TypeScript syntax check failed: src/models/Flight.ts(6,47): error TS1005: ',' expected."
        ],
        advisor_notes=(advisory,),
        mode="shadow",
    )
    payload = result.to_dict()

    assert isinstance(result, DirectorRepairPlanningResultV1)
    assert payload["schema_version"] == "director.repair_planning_result.v1"
    assert payload["ok"] is True
    assert payload["planned"] is True
    assert payload["agi_execution_authority"] is False
    assert payload["advisory_authoritative"] is False
    assert payload["director_tool_execution_required"] is True
    assert payload["plan_summary"]["rule_id"] == "typescript.object_literal_missing_comma"
    assert payload["plan_summary"]["source_tool"] == "deterministic_typescript_return_object_semicolon_repair"
    assert payload["plan_summary"]["mode"] == "shadow"
    assert payload["plan_summary"]["operation_count"] == 1
    assert payload["plan_summary"]["advisor_note_count"] == 1
    assert payload["plan_summary"]["agi_execution_authority"] is False
    assert payload["plan_summary"]["advisory_authoritative"] is False
    assert payload["composition_summary"]["ok"] is True
    assert payload["composition_summary"]["patch_count"] == 1
    assert payload["composition_summary"]["issue_count"] == 0
    patch = payload["composition_summary"]["patches"][0]
    assert patch["path"] == "src/models/Flight.ts"
    assert patch["changed"] is True
    assert "flightTime, landed:" in patch["content_after"]
    assert payload["advisor_notes"] == [
        {
            "advisor_source": "agi",
            "message": "check the receipt after deterministic planning",
            "confidence": 0.4,
            "authoritative": False,
            "metadata": {"evidence_ref": "runtime/receipts/latest.json"},
        }
    ]


def test_public_typescript_object_literal_comma_run_executes_with_receipt(tmp_path: Path) -> None:
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

    result = run_director_typescript_object_literal_comma_repair(
        workspace=tmp_path,
        base_files={relative_path: content},
        artifact_quality_errors=[
            "TypeScript syntax check failed: src/models/Flight.ts(6,47): error TS1005: ',' expected."
        ],
        writer=writer,
        allowed_paths=(relative_path,),
    )

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    receipt = result.receipts[0]
    assert receipt.source_tool == "deterministic_typescript_return_object_semicolon_repair"
    assert receipt.status == "applied"
    assert receipt.authoritative is True
    assert receipt.files_changed == (relative_path,)
    assert receipt.before_hashes[relative_path] == sha256_text(content)
    assert receipt.after_hashes[relative_path] == sha256_text(target.read_text(encoding="utf-8"))
    assert "flightTime, landed:" in target.read_text(encoding="utf-8")
    assert result.metadata["planning"]["planned"] is True
    assert result.metadata["plan_policy"]["allowed"] is True
    assert result.metadata["composition_policy"]["allowed"] is True
    assert result.metadata["execution_error"] is None


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


def test_executor_commit_requires_policy_gated_writer(tmp_path: Path) -> None:
    plan = RepairPlan(
        rule_id="rule.ts",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(RepairOperation(kind="write_file", path="src/app.ts", content="new\n"),),
    )
    composition = PatchComposer().compose({"src/app.ts": ""}, plan.operations)

    result = TransactionalRepairExecutor().execute(workspace=tmp_path, plan=plan, composition=composition)

    assert not result.ok
    assert result.error == "commit_requires_policy_gated_writer"
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
    assert result.receipt.authoritative is True
    assert result.receipt.before_hashes["src/app.ts"] == sha256_text("old\n")
    assert result.receipt.after_hashes["src/app.ts"] == sha256_text("new\n")


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
    assert context["receipts"][0]["advisor_notes"] == [{"source": "agi", "confidence": 0.7, "authoritative": False}]


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


def test_legacy_summary_without_receipts_is_not_authoritative() -> None:
    summary = build_director_repair_kernel_summary(stage="quality", tool_results=[], mode="commit")

    assert summary["receipt_count"] == 0
    assert summary["authoritative"] is False


def test_public_typescript_comma_planner_returns_composed_patch_projection() -> None:
    result = plan_director_typescript_object_literal_comma_repair(
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
        artifact_quality_errors=[
            "TypeScript syntax check failed: src/models/Flight.ts(6,5): error TS1005: ',' expected."
        ],
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


def test_public_strategy_catalog_is_read_only_and_non_agi_authoritative() -> None:
    result = query_director_repair_strategy_catalog(QueryDirectorRepairStrategyCatalogV1(max_items=3))
    payload = result.to_dict()

    assert payload["source"] == "director.runtime.repair_kernel.strategy_catalog"
    assert payload["access"] == "read_only"
    assert payload["agi_execution_authority"] is False
    assert payload["director_tool_execution_required"] is True
    assert payload["owner_cell"] == "director.runtime"
    assert len(payload["items"]) == 3
