"""Focused tests for materialization-quality repair bridge cutover evidence."""

from __future__ import annotations

from typing import Any

from polaris.cells.roles.adapters.internal.director import materialization_quality_repair_bridge

_STEP_ID = "materialization.hygiene_scaffold"
_SOURCE_TOOL = "deterministic_materialization_hygiene_repair"


def _selected_step() -> materialization_quality_repair_bridge.DirectorRepairMaterializationQualityStepV1:
    return materialization_quality_repair_bridge.DirectorRepairMaterializationQualityStepV1(
        step_id=_STEP_ID,
        language="multi",
        phase="hygiene",
        priority=0,
        source_tool=_SOURCE_TOOL,
    )


def _native_receipt(*, exit_code: int, errors_after: int = 0) -> dict[str, Any]:
    return {
        "receipt_id": f"native-hygiene-{exit_code}-{errors_after}",
        "plan_id": "plan-native-hygiene",
        "source_tool": _SOURCE_TOOL,
        "status": "applied",
        "authoritative": True,
        "revalidation_evidence": {
            "command": ["rtk", "pytest", "tests/test_hygiene.py"],
            "exit_code": exit_code,
            "errors_after": errors_after,
        },
    }


def _tool_result_with_native_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool": "run_director_repair",
        "ok": True,
        "result": {
            "ok": True,
            "source_tool": _SOURCE_TOOL,
            "bridge_step_id": _STEP_ID,
            "repair_kernel": {"receipts": [receipt]},
        },
    }


def _scheduler_bridge(
    *,
    native_receipts: list[dict[str, Any]],
    callback_projections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    step = _selected_step()
    tool_results = [_tool_result_with_native_receipt(receipt) for receipt in native_receipts]
    migration_debt = {
        "legacy_callback_debt": [
            {
                "step_id": _STEP_ID,
                "blockers": [],
                "callback_projection_present": bool(callback_projections),
                "native_receipt_present": bool(native_receipts),
            }
        ],
        "remaining_callback_only_step_ids": [],
        "callback_only_step_count": 0,
        "native_receipt_step_ids": [_STEP_ID] if native_receipts else [],
        "callback_projection_step_ids": [_STEP_ID] if callback_projections else [],
    }
    return materialization_quality_repair_bridge._build_materialization_scheduler_bridge_summary(
        tool_results=tool_results,
        repair_kernel={"receipts": native_receipts},
        ordered_steps=(step,),
        migration_debt=migration_debt,
        receipt_projections=callback_projections or [],
        schedule_reconciliation={"runner_step_ids": [_STEP_ID]},
    )


def test_selected_materialization_step_reports_native_cutover_ready_without_global_success() -> None:
    scheduler_bridge = _scheduler_bridge(native_receipts=[_native_receipt(exit_code=0)])
    evidence = scheduler_bridge["selected_step_native_cutover_evidence"][_STEP_ID]

    assert evidence["cutover_ready"] is True
    assert evidence["native_revalidation_evidence_status"] == "resolved_evidence"
    assert evidence["native_revalidation_evidence_resolved"] is True
    assert evidence["missing_required_evidence"] == []
    assert evidence["cutover_blockers"] == []
    assert scheduler_bridge["selected_step_native_cutover_ready"] is True
    assert scheduler_bridge["selected_step_native_cutover_ready_step_ids"] == [_STEP_ID]
    assert scheduler_bridge["repair_kernel_migration_debt"]["legacy_callback_debt"][0]["step_id"] == _STEP_ID


def test_selected_materialization_step_blocks_cutover_when_callback_projection_still_present() -> None:
    callback_projection = {
        "projection_id": "callback-hygiene",
        "step_id": _STEP_ID,
        "source_tool": _SOURCE_TOOL,
        "revalidation_evidence": {
            "command": ["rtk", "pytest", "tests/test_hygiene.py"],
            "exit_code": 0,
        },
    }

    scheduler_bridge = _scheduler_bridge(
        native_receipts=[_native_receipt(exit_code=0)],
        callback_projections=[callback_projection],
    )
    evidence = scheduler_bridge["selected_step_native_cutover_evidence"][_STEP_ID]

    assert evidence["native_revalidation_evidence_status"] == "resolved_evidence"
    assert evidence["callback_projection_present"] is True
    assert evidence["cutover_ready"] is False
    assert evidence["missing_required_evidence"] == ["adapter_projection_absent"]
    assert evidence["cutover_blockers"] == ["adapter_projection_still_present"]
    assert scheduler_bridge["selected_step_native_cutover_ready"] is False
    assert scheduler_bridge["selected_step_native_cutover_blockers"] == ["adapter_projection_still_present"]


def test_selected_materialization_step_distinguishes_failed_native_revalidation_evidence() -> None:
    scheduler_bridge = _scheduler_bridge(native_receipts=[_native_receipt(exit_code=1)])
    evidence = scheduler_bridge["selected_step_native_cutover_evidence"][_STEP_ID]

    assert evidence["native_revalidation_evidence_status"] == "failed_evidence"
    assert evidence["native_revalidation_evidence_failed"] is True
    assert evidence["native_revalidation_evidence_resolved"] is False
    assert evidence["missing_required_evidence"] == ["resolved_native_evidence_status"]
    assert evidence["cutover_blockers"] == ["failed_revalidation_evidence"]
    assert "missing_native_revalidation_evidence" not in evidence["cutover_blockers"]
    assert scheduler_bridge["selected_step_native_cutover_ready"] is False


def test_selected_materialization_step_distinguishes_missing_native_receipt_and_revalidation() -> None:
    missing_receipt_bridge = _scheduler_bridge(native_receipts=[])
    missing_receipt_evidence = missing_receipt_bridge["selected_step_native_cutover_evidence"][_STEP_ID]

    assert missing_receipt_evidence["native_path_available"] is False
    assert missing_receipt_evidence["cutover_ready"] is False
    assert "missing_native_repair_receipt" in missing_receipt_evidence["cutover_blockers"]
    assert "missing_native_revalidation_evidence" in missing_receipt_evidence["cutover_blockers"]
    assert "missing_revalidation_evidence" not in missing_receipt_evidence["cutover_blockers"]

    incomplete_receipt = {
        "receipt_id": "native-hygiene-incomplete",
        "plan_id": "plan-native-hygiene",
        "source_tool": _SOURCE_TOOL,
        "status": "applied",
        "authoritative": True,
    }
    missing_revalidation_bridge = _scheduler_bridge(native_receipts=[incomplete_receipt])
    missing_revalidation_evidence = missing_revalidation_bridge["selected_step_native_cutover_evidence"][_STEP_ID]

    assert missing_revalidation_evidence["native_path_available"] is True
    assert missing_revalidation_evidence["native_revalidation_evidence_status"] == "missing_evidence"
    assert missing_revalidation_evidence["native_revalidation_evidence_missing"] is True
    assert missing_revalidation_evidence["cutover_blockers"] == ["missing_native_revalidation_evidence"]
