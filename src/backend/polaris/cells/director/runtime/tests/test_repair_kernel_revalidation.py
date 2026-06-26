"""Tests for native Director Runtime repair revalidation closure."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from polaris.cells.director.runtime.internal.repair_kernel.legacy_bridge import build_legacy_repair_kernel_summary
from polaris.cells.director.runtime.public.contracts import (
    AttachDirectorRepairRevalidationEvidenceV1,
    DirectorRepairResultV1,
    DirectorRepairRevalidationInputV1,
    DirectorRepairRevalidationRequestV1,
    RepairReceiptV1,
    RunDirectorRepairCommandV1,
)
from polaris.cells.director.runtime.public.service import (
    project_director_repair_revalidation_evidence,
    run_director_repair,
)

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

_Revalidator = Callable[[DirectorRepairRevalidationRequestV1], DirectorRepairRevalidationInputV1 | None]


def _run_repair(workspace: Path, *, revalidator: _Revalidator | None = None) -> DirectorRepairResultV1:
    target = workspace / _RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_BROKEN_CONTENT, encoding="utf-8")

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = workspace / path
        write_target.parent.mkdir(parents=True, exist_ok=True)
        write_target.write_text(updated, encoding="utf-8")
        return {
            "ok": True,
            "file": path,
            "bytes_written": len(updated.encode("utf-8")),
            "operation": "modify",
        }

    command = RunDirectorRepairCommandV1(
        task_id="task-native-revalidation",
        workspace=str(workspace),
        source_tool=_SOURCE_TOOL,
        base_files={_RELATIVE_PATH: _BROKEN_CONTENT},
        artifact_quality_errors=(_QUALITY_ERROR,),
        allowed_paths=(_RELATIVE_PATH,),
    )
    if revalidator is None:
        return run_director_repair(command, writer=writer)
    return run_director_repair(command, writer=writer, revalidator=revalidator)


def _assert_receipt_evidence_material(receipt: RepairReceiptV1) -> dict[str, Any]:
    evidence = dict(receipt.revalidation_evidence)

    assert receipt.authority_hash
    assert receipt.projection_hash
    assert receipt.evidence_status in {"missing_evidence", "failed_evidence", "resolved_evidence"}
    assert "command" in evidence
    assert "exit_code" in evidence
    assert "evidence_status" in evidence
    assert "raw_output_ref" in evidence
    assert "errors_before" in evidence
    assert "errors_after" in evidence
    assert "net_error_reduction" in evidence
    assert evidence["evidence_status"] == receipt.evidence_status
    return evidence


def test_run_director_repair_revalidator_success_makes_receipt_authoritative(tmp_path: Path) -> None:
    requests: list[DirectorRepairRevalidationRequestV1] = []

    def revalidator(request: DirectorRepairRevalidationRequestV1) -> DirectorRepairRevalidationInputV1:
        requests.append(request)
        return DirectorRepairRevalidationInputV1(
            command=("rtk", "tsc", "--noEmit"),
            exit_code=0,
            raw_output_ref="runtime/verifier/native-success.log",
            metadata={"verifier": "typescript"},
        )

    result = _run_repair(tmp_path, revalidator=revalidator)

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1
    assert len(requests) == 1
    assert requests[0].task_id == "task-native-revalidation"
    assert requests[0].source_tool == _SOURCE_TOOL
    assert requests[0].files_changed == (_RELATIVE_PATH,)
    assert requests[0].diagnostics_before[0]["code"] == "typescript_ts1005"

    receipt = result.receipts[0]
    evidence = _assert_receipt_evidence_material(receipt)
    assert receipt.rule_id
    assert receipt.to_dict()["rule_id"] == receipt.rule_id
    assert receipt.status == "applied"
    assert receipt.authoritative is True
    assert receipt.evidence_status == "resolved_evidence"
    assert receipt.metadata["requires_revalidation"] is False
    assert evidence["command"] == ["rtk", "tsc", "--noEmit"]
    assert evidence["exit_code"] == 0
    assert evidence["evidence_status"] == "resolved_evidence"
    assert evidence["raw_output_ref"] == "runtime/verifier/native-success.log"
    assert evidence["errors_before"] == 1
    assert evidence["errors_after"] == 0
    assert evidence["metadata"] == {"verifier": "typescript"}


def test_run_director_repair_revalidator_failure_marks_failed_revalidation(tmp_path: Path) -> None:
    def revalidator(_: DirectorRepairRevalidationRequestV1) -> DirectorRepairRevalidationInputV1:
        return DirectorRepairRevalidationInputV1(
            residual_artifact_quality_errors=(_RESIDUAL_ERROR,),
            command=("rtk", "npm", "test"),
            exit_code=1,
            raw_output_ref="runtime/verifier/native-failure.log",
            metadata={"verifier": "typescript"},
        )

    result = _run_repair(tmp_path, revalidator=revalidator)

    assert result.ok is False
    assert result.error_code == "repair_revalidation_failed"
    assert result.error_message == "Repair revalidation failed."
    assert len(result.receipts) == 1
    assert len(result.residual_diagnostics) == 1

    receipt = result.receipts[0]
    evidence = _assert_receipt_evidence_material(receipt)
    assert receipt.status == "failed_revalidation"
    assert receipt.authoritative is False
    assert receipt.evidence_status == "failed_evidence"
    assert receipt.metadata["requires_revalidation"] is False
    assert evidence["command"] == ["rtk", "npm", "test"]
    assert evidence["exit_code"] == 1
    assert evidence["evidence_status"] == "failed_evidence"
    assert evidence["raw_output_ref"] == "runtime/verifier/native-failure.log"
    assert evidence["errors_before"] == 1
    assert evidence["errors_after"] == 1
    assert result.residual_diagnostics[0].code == "typescript_ts2304"


def test_run_director_repair_without_revalidator_still_requires_revalidation(tmp_path: Path) -> None:
    result = _run_repair(tmp_path)

    assert result.ok is True
    assert result.error_code is None
    assert len(result.receipts) == 1

    receipt = result.receipts[0]
    assert receipt.status == "applied"
    assert receipt.authoritative is False
    assert receipt.evidence_status == "missing_evidence"
    assert receipt.revalidation_evidence == {}
    assert receipt.metadata["requires_revalidation"] is True
    assert receipt.authority_hash
    assert receipt.projection_hash


def test_run_director_repair_revalidator_exception_fails_closed(tmp_path: Path) -> None:
    def revalidator(_: DirectorRepairRevalidationRequestV1) -> DirectorRepairRevalidationInputV1:
        raise RuntimeError("verifier crashed")

    result = _run_repair(tmp_path, revalidator=revalidator)

    assert result.ok is False
    assert result.error_code == "repair_revalidation_failed"
    assert result.error_message == "Repair revalidator failed: RuntimeError: verifier crashed"
    assert len(result.receipts) == 1

    receipt = result.receipts[0]
    evidence = _assert_receipt_evidence_material(receipt)
    assert receipt.status == "failed_revalidation"
    assert receipt.authoritative is False
    assert receipt.evidence_status == "missing_evidence"
    assert evidence["command"] == []
    assert evidence["exit_code"] == 1
    assert evidence["raw_output_ref"] is None
    assert evidence["errors_before"] == 1
    assert evidence["errors_after"] == 1
    assert evidence["metadata"]["revalidation_failure_reason"] == "revalidator_exception"
    assert evidence["metadata"]["revalidator_error_type"] == "RuntimeError"


def test_run_director_repair_revalidator_none_fails_closed_as_missing_evidence(tmp_path: Path) -> None:
    def revalidator(_: DirectorRepairRevalidationRequestV1) -> None:
        return None

    result = _run_repair(tmp_path, revalidator=revalidator)

    assert result.ok is False
    assert result.error_code == "repair_revalidation_failed"
    assert result.error_message == "Repair revalidator returned no evidence."
    assert len(result.receipts) == 1

    receipt = result.receipts[0]
    evidence = _assert_receipt_evidence_material(receipt)
    assert receipt.status == "failed_revalidation"
    assert receipt.authoritative is False
    assert receipt.evidence_status == "missing_evidence"
    assert receipt.metadata["requires_revalidation"] is False
    assert evidence["exit_code"] == 1
    assert evidence["metadata"]["revalidation_failure_reason"] == "missing_revalidation_evidence"


def test_run_director_repair_revalidator_wrong_return_type_fails_closed(tmp_path: Path) -> None:
    def revalidator(_: DirectorRepairRevalidationRequestV1) -> Any:
        return {"exit_code": 0, "command": ["rtk", "tsc", "--noEmit"]}

    result = _run_repair(tmp_path, revalidator=revalidator)

    assert result.ok is False
    assert result.error_code == "repair_revalidation_failed"
    assert result.error_message == "Repair revalidator returned invalid evidence type."
    assert len(result.receipts) == 1

    receipt = result.receipts[0]
    evidence = _assert_receipt_evidence_material(receipt)
    assert receipt.status == "failed_revalidation"
    assert receipt.authoritative is False
    assert receipt.evidence_status == "missing_evidence"
    assert receipt.metadata["requires_revalidation"] is False
    assert evidence["command"] == []
    assert evidence["exit_code"] == 1
    assert evidence["raw_output_ref"] is None
    assert evidence["metadata"]["revalidation_failure_reason"] == "invalid_revalidation_evidence_type"
    assert evidence["metadata"]["revalidator_result_type"] == "dict"


def test_run_director_repair_revalidator_missing_exit_code_fails_closed(tmp_path: Path) -> None:
    def revalidator(_: DirectorRepairRevalidationRequestV1) -> DirectorRepairRevalidationInputV1:
        return DirectorRepairRevalidationInputV1(
            command=("rtk", "tsc", "--noEmit"),
            raw_output_ref="runtime/verifier/native-missing-exit-code.log",
            metadata={"verifier": "typescript"},
        )

    result = _run_repair(tmp_path, revalidator=revalidator)

    assert result.ok is False
    assert result.error_code == "repair_revalidation_failed"
    assert result.error_message == "Repair revalidation failed: missing verifier exit code."
    assert len(result.receipts) == 1

    receipt = result.receipts[0]
    evidence = _assert_receipt_evidence_material(receipt)
    assert receipt.status == "failed_revalidation"
    assert receipt.authoritative is False
    assert receipt.evidence_status == "missing_evidence"
    assert receipt.metadata["requires_revalidation"] is False
    assert evidence["command"] == ["rtk", "tsc", "--noEmit"]
    assert evidence["exit_code"] == 1
    assert evidence["raw_output_ref"] == "runtime/verifier/native-missing-exit-code.log"
    assert evidence["metadata"]["verifier"] == "typescript"
    assert evidence["metadata"]["revalidation_failure_reason"] == "missing_revalidation_exit_code"
    assert evidence["metadata"]["reported_exit_code"] is None


def test_run_director_repair_revalidation_hash_material_differs_for_success_and_failure(
    tmp_path: Path,
) -> None:
    success_result = _run_repair(
        tmp_path / "success",
        revalidator=lambda _: DirectorRepairRevalidationInputV1(
            command=("rtk", "tsc", "--noEmit"),
            exit_code=0,
            raw_output_ref="runtime/verifier/native-success.log",
        ),
    )
    failure_result = _run_repair(
        tmp_path / "failure",
        revalidator=lambda _: DirectorRepairRevalidationInputV1(
            residual_artifact_quality_errors=(_RESIDUAL_ERROR,),
            command=("rtk", "tsc", "--noEmit"),
            exit_code=1,
            raw_output_ref="runtime/verifier/native-failure.log",
        ),
    )

    success_receipt = success_result.receipts[0]
    failure_receipt = failure_result.receipts[0]

    assert success_receipt.status == "applied"
    assert failure_receipt.status == "failed_revalidation"
    assert success_receipt.evidence_status == "resolved_evidence"
    assert failure_receipt.evidence_status == "failed_evidence"
    assert success_receipt.authority_hash != failure_receipt.authority_hash
    assert success_receipt.projection_hash != failure_receipt.projection_hash
    assert _assert_receipt_evidence_material(success_receipt)["exit_code"] == 0
    assert _assert_receipt_evidence_material(failure_receipt)["exit_code"] == 1


def test_legacy_revalidation_zero_exit_with_residual_ids_counts_failed() -> None:
    summary = build_legacy_repair_kernel_summary(
        stage="materialization_quality",
        tool_results=[
            {
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "source_tool": _SOURCE_TOOL,
                    "file": _RELATIVE_PATH,
                    "before_hash": "before-hash",
                    "after_hash": "after-hash",
                    "revalidation": {
                        "command": ["rtk", "tsc", "--noEmit"],
                        "exit_code": 0,
                        "errors_before": 1,
                        "errors_after": 1,
                        "residual_diagnostic_ids": ["diag-residual"],
                    },
                },
            }
        ],
        artifact_quality_errors=[_QUALITY_ERROR],
        mode="commit",
    )

    receipt = summary["receipts"][0]
    coverage = summary["revalidation_coverage"]

    assert receipt["status"] == "failed_revalidation"
    assert receipt["authoritative"] is False
    assert receipt["evidence_status"] == "failed_evidence"
    assert receipt["revalidation_evidence"]["evidence_status"] == "failed_evidence"
    assert receipt["revalidation_evidence"]["exit_code"] == 0
    assert receipt["revalidation_evidence"]["errors_after"] == 1
    assert receipt["revalidation_evidence"]["residual_diagnostic_ids"] == ["diag-residual"]
    assert coverage["receipts_with_revalidation"] == 1
    assert coverage["receipts_missing_revalidation"] == 0
    assert coverage["failed_revalidation_receipt_count"] == 1
    assert coverage["evidence_status_counts"]["failed_evidence"] == 1
    assert coverage["failed_revalidation_receipt_ids"] == [receipt["receipt_id"]]
    assert summary["authoritative"] is False


def test_public_revalidation_projection_keeps_missing_receipt_errors_before_at_zero() -> None:
    result = project_director_repair_revalidation_evidence(
        AttachDirectorRepairRevalidationEvidenceV1(
            summary={
                "mode": "commit",
                "coverage_report": {"total_diagnostics": 7},
                "receipts": [
                    {
                        "receipt_id": "receipt-projection-1",
                        "plan_id": "plan-projection-1",
                        "rule_id": "typescript.object_literal_missing_comma",
                        "source_tool": _SOURCE_TOOL,
                        "status": "pending_revalidation",
                        "mode": "commit",
                        "authoritative": False,
                        "files_changed": [_RELATIVE_PATH],
                        "operation_ids": ["op-projection-1"],
                        "diagnostics": [],
                        "metadata": {"requires_revalidation": True},
                    }
                ],
            },
            residual_artifact_quality_errors=(_RESIDUAL_ERROR,),
            command=("rtk", "tsc", "--noEmit"),
            exit_code=0,
        )
    )

    receipt = result.summary["receipts"][0]
    evidence = receipt["revalidation_evidence"]
    coverage = result.summary["revalidation_coverage"]

    assert evidence["errors_before"] == 0
    assert evidence["errors_after"] == 1
    assert evidence["net_error_reduction"] == -1
    assert evidence["metadata"]["errors_before_source"] == "missing_receipt_diagnostics"
    assert evidence["metadata"]["coverage_report_total_diagnostics"] == 7
    assert evidence["metadata"]["coverage_report_total_diagnostics_used_for_errors_before"] is False
    assert receipt["errors_before"] == 0
    assert receipt["evidence_status"] == "failed_evidence"
    assert evidence["evidence_status"] == "failed_evidence"
    assert receipt["status"] == "failed_revalidation"
    assert receipt["authoritative"] is False
    assert coverage["failed_revalidation_receipt_count"] == 1
    assert coverage["receipts_missing_revalidation"] == 0
    assert coverage["evidence_status_counts"]["failed_evidence"] == 1
