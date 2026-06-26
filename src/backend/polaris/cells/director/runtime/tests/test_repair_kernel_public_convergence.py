"""Public Director Runtime repair convergence API tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from polaris.cells.director.runtime.internal.repair_kernel import (
    PatchComposer,
    RepairOperation,
    RepairPlan,
    RepairPolicyContext,
    RepairPolicyGate,
    TransactionalRepairExecutor,
    runtime_dispatch as runtime_dispatch_module,
)
from polaris.cells.director.runtime.internal.repair_kernel.contracts import FILE_ABSENT_HASH, sha256_text
from polaris.cells.director.runtime.public.contracts import (
    DirectorRepairConvergenceResultV1,
    DirectorRepairConvergenceVerifierRequestV1,
    DirectorRepairVerifierSnapshotInputV1,
    RunDirectorRepairConvergenceCommandV1,
)
from polaris.cells.director.runtime.public.service import run_director_repair_convergence

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
_UNCOVERED_ERROR = "declared target file missing app/models/widget.rb is missing"
_DELETE_SOURCE_TOOL = "deterministic_test_delete_file_repair"
_DELETE_ERROR = "test delete stale file"


def _install_delete_convergence_planner(
    monkeypatch: pytest.MonkeyPatch,
    *,
    relative_path: str,
    original: str,
) -> None:
    diagnostics = ()
    operation = RepairOperation(
        kind="delete_file",
        path=relative_path,
        before_hash=sha256_text(original),
    )
    plan = RepairPlan(
        rule_id="test.delete_file",
        source_tool=_DELETE_SOURCE_TOOL,
        operations=(operation,),
        diagnostics=diagnostics,
    )

    def planner(current_diagnostics: tuple[Any, ...], round_number: int) -> tuple[RepairPlan, ...]:
        del round_number
        return (plan,) if current_diagnostics else ()

    def binding_planner(
        base_files: dict[str, str],
        artifact_quality_errors: tuple[str, ...],
        advisor_notes: tuple[Any, ...] | None,
        mode: str,
    ) -> runtime_dispatch_module.RuntimeRepairPlanning:
        del artifact_quality_errors, advisor_notes, mode
        return runtime_dispatch_module.RuntimeRepairPlanning(
            source_tool=_DELETE_SOURCE_TOOL,
            diagnostics=diagnostics,
            plan=plan,
            composition=PatchComposer().compose(base_files, plan.operations),
        )

    def binding_runner(
        workspace: str | Path,
        base_files: dict[str, str],
        artifact_quality_errors: tuple[str, ...],
        writer: object,
        editor: object | None,
        deleter: object | None,
        allowed_paths: tuple[str, ...] | None,
        advisor_notes: tuple[Any, ...] | None,
        mode: str,
    ) -> runtime_dispatch_module.RuntimeRepairRun:
        del artifact_quality_errors, editor, advisor_notes, mode
        planning = binding_planner(base_files, (), None, "commit")
        assert planning.plan is not None
        assert planning.composition is not None
        policy = RepairPolicyGate()
        context = RepairPolicyContext(allowed_paths=tuple(allowed_paths or base_files.keys()))
        plan_decision = policy.evaluate_plan(planning.plan, context)
        composition_decision = policy.evaluate_composition(planning.plan, planning.composition)
        execution_result = TransactionalRepairExecutor().execute(
            workspace=Path(workspace),
            plan=planning.plan,
            composition=planning.composition,
            writer=writer,  # type: ignore[arg-type]
            deleter=deleter,  # type: ignore[arg-type]
        )
        return runtime_dispatch_module.RuntimeRepairRun(
            planning=planning,
            ok=execution_result.ok,
            execution_result=execution_result,
            plan_decision=plan_decision,
            composition_decision=composition_decision,
            error_code=None if execution_result.ok else "repair_execution_failed",
            error_message=None if execution_result.ok else execution_result.error,
        )

    bindings = dict(runtime_dispatch_module._RUNTIME_REPAIR_BINDINGS)
    bindings[_DELETE_SOURCE_TOOL] = runtime_dispatch_module.RuntimeRepairBinding(
        source_tool=_DELETE_SOURCE_TOOL,
        language="test",
        rule_id="test.delete_file",
        planner=binding_planner,  # type: ignore[arg-type]
        runner=binding_runner,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(runtime_dispatch_module, "_RUNTIME_REPAIR_BINDINGS", bindings)
    monkeypatch.setattr(runtime_dispatch_module, "_native_coverage_gate_status", lambda _report, _tools: None)
    monkeypatch.setattr(
        runtime_dispatch_module,
        "build_runtime_repair_convergence_planner",
        lambda **_kwargs: planner,
    )


def _valid_verifier_metadata(
    *,
    evidence_source: str | None = "adapter_convergence_verifier_factory",
    raw_output_ref_verified: bool = True,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "verifier": "typescript",
        "raw_output_ref_verified": raw_output_ref_verified,
    }
    if evidence_source is not None:
        metadata["evidence_source"] = evidence_source
    return metadata


def _write_initial_file(workspace: Path) -> Path:
    target = workspace / _RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_BROKEN_CONTENT, encoding="utf-8")
    return target


def _writer(workspace: Path):
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

    return writer


def _command(workspace: Path, *, errors: tuple[str, ...] = (_QUALITY_ERROR,)) -> RunDirectorRepairConvergenceCommandV1:
    return RunDirectorRepairConvergenceCommandV1(
        task_id="task-public-convergence",
        workspace=str(workspace),
        source_tools=(_SOURCE_TOOL,),
        artifact_quality_errors=errors,
        base_files={_RELATIVE_PATH: _BROKEN_CONTENT},
        allowed_paths=(_RELATIVE_PATH,),
        max_rounds=3,
        metadata={"caller": "runtime_public_test"},
    )


def test_public_convergence_success_uses_typed_receipts_and_revalidation_evidence(tmp_path: Path) -> None:
    target = _write_initial_file(tmp_path)
    requests: list[DirectorRepairConvergenceVerifierRequestV1] = []

    def verifier(request: DirectorRepairConvergenceVerifierRequestV1) -> DirectorRepairVerifierSnapshotInputV1:
        requests.append(request)
        current = target.read_text(encoding="utf-8")
        residual_errors = () if "flightTime, landed:" in current else (_QUALITY_ERROR,)
        return DirectorRepairVerifierSnapshotInputV1(
            residual_artifact_quality_errors=residual_errors,
            command=("rtk", "tsc", "--noEmit"),
            exit_code=0 if not residual_errors else 1,
            raw_output_ref=f"runtime/verifier/public-convergence-round-{request.round_number}.log",
            metadata=_valid_verifier_metadata(),
        )

    result = run_director_repair_convergence(
        _command(tmp_path),
        writer=_writer(tmp_path),
        verifier=verifier,
    )

    assert isinstance(result, DirectorRepairConvergenceResultV1)
    assert result.ok is True
    assert result.converged is True
    assert result.status == "converged"
    assert result.final_diagnostics == ()
    assert len(result.rounds) == 1
    assert len(result.receipts) == 1
    assert [request.round_number for request in requests] == [0, 1]
    assert requests[0].receipts == ()
    assert len(requests[1].receipts) == 1

    receipt = result.receipts[0]
    evidence = dict(receipt.revalidation_evidence)
    assert receipt.source_tool == _SOURCE_TOOL
    assert receipt.status == "applied"
    assert receipt.authoritative is True
    assert receipt.authority_hash
    assert receipt.projection_hash
    assert evidence["command"] == ["rtk", "tsc", "--noEmit"]
    assert evidence["exit_code"] == 0
    assert evidence["errors_before"] == 1
    assert evidence["errors_after"] == 0
    assert evidence["raw_output_ref"] == "runtime/verifier/public-convergence-round-1.log"
    assert receipt.metadata["requires_revalidation"] is False

    assert result.rounds[0].status == "converged"
    assert result.rounds[0].revalidation_evidence["net_error_reduction"] == 1
    assert result.metadata["owner_cell"] == "director.runtime"
    assert result.metadata["internal_convergence_metadata"]["status"] == "converged"
    assert result.metadata["coverage_report"]["total_diagnostics"] == 1
    assert result.metadata["callback_effect_boundary"] == "adapter_supplied_verifier_callback_no_command_execution"
    assert result.metadata["verifier_command_execution"] == "not_performed_by_public_runtime"
    assert "flightTime, landed:" in target.read_text(encoding="utf-8")


def test_public_convergence_delete_file_uses_policy_gated_deleter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative_path = "src/stale.ts"
    original = "export const stale = true;\n"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text(original, encoding="utf-8")
    _install_delete_convergence_planner(monkeypatch, relative_path=relative_path, original=original)
    delete_calls: list[str] = []

    def writer(path: str, updated: str) -> dict[str, object]:
        write_target = tmp_path / path
        write_target.parent.mkdir(parents=True, exist_ok=True)
        write_target.write_text(updated, encoding="utf-8")
        return {"ok": True, "file": path, "bytes_written": len(updated.encode("utf-8"))}

    def deleter(path: str) -> dict[str, object]:
        delete_calls.append(path)
        (tmp_path / path).unlink()
        return {"ok": True, "file": path, "operation": "delete_file"}

    def verifier(request: DirectorRepairConvergenceVerifierRequestV1) -> DirectorRepairVerifierSnapshotInputV1:
        residual_errors = () if not target.exists() else (_DELETE_ERROR,)
        return DirectorRepairVerifierSnapshotInputV1(
            residual_artifact_quality_errors=residual_errors,
            command=("rtk", "test", "delete-convergence"),
            exit_code=0 if not residual_errors else 1,
            raw_output_ref=f"runtime/verifier/delete-convergence-round-{request.round_number}.log",
            metadata=_valid_verifier_metadata(),
        )

    result = run_director_repair_convergence(
        RunDirectorRepairConvergenceCommandV1(
            task_id="task-public-delete-convergence",
            workspace=str(tmp_path),
            source_tools=(_DELETE_SOURCE_TOOL,),
            artifact_quality_errors=(_DELETE_ERROR,),
            base_files={relative_path: original},
            allowed_paths=(relative_path,),
            max_rounds=3,
            metadata={"caller": "runtime_public_delete_test"},
        ),
        writer=writer,
        deleter=deleter,
        verifier=verifier,
    )

    assert result.ok is True
    assert result.converged is True
    assert delete_calls == [relative_path]
    assert not target.exists()
    receipt = result.receipts[0]
    assert receipt.before_hashes[relative_path] == sha256_text(original)
    assert receipt.after_hashes[relative_path] == FILE_ABSENT_HASH
    assert receipt.metadata["execution_records"][0]["operation"] == "delete_file"
    assert result.metadata["deleter_effect_boundary"] == "adapter_supplied_director_authorized_deleter"


def test_public_convergence_coverage_gap_fails_before_verifier(tmp_path: Path) -> None:
    _write_initial_file(tmp_path)

    def verifier(_: DirectorRepairConvergenceVerifierRequestV1) -> DirectorRepairVerifierSnapshotInputV1:
        raise AssertionError("coverage gate should stop before public verifier")

    result = run_director_repair_convergence(
        _command(tmp_path, errors=(_UNCOVERED_ERROR,)),
        writer=_writer(tmp_path),
        verifier=verifier,
    )

    assert result.ok is False
    assert result.converged is False
    assert result.status == "coverage_gap_uncovered_diagnostics"
    assert result.error_code == "coverage_gap_uncovered_diagnostics"
    assert result.receipts == ()
    assert result.metadata["owner_cell"] == "director.runtime"
    assert result.metadata["coverage_gap_count"] == 1
    assert result.metadata["coverage_gaps"][0]["diagnostic"]["code"] == "declared_target_missing"
    assert result.metadata["coverage_gaps"][0]["recommended_next_owner"] == "runtime_rule"


@pytest.mark.parametrize("failure_mode", ["exception", "wrong_type"])
def test_public_convergence_verifier_boundary_fails_closed(tmp_path: Path, failure_mode: str) -> None:
    target = _write_initial_file(tmp_path)

    def verifier(request: DirectorRepairConvergenceVerifierRequestV1) -> Any:
        assert request.round_number == 0
        if failure_mode == "exception":
            raise RuntimeError("verifier unavailable")
        return {"not": "a verifier snapshot"}

    result = run_director_repair_convergence(
        _command(tmp_path),
        writer=_writer(tmp_path),
        verifier=verifier,
    )

    assert result.ok is False
    assert result.converged is False
    assert result.status == "verifier_callback_failed"
    assert result.error_code == "verifier_callback_failed"
    assert result.receipts == ()
    assert result.final_diagnostics[0].code == "typescript_ts1005"
    assert result.metadata["owner_cell"] == "director.runtime"
    assert result.metadata["coverage_report"]["total_diagnostics"] == 1
    assert result.metadata["verifier_failure_reason"] in {
        "verifier_exception",
        "invalid_verifier_snapshot_type",
    }
    assert target.read_text(encoding="utf-8") == _BROKEN_CONTENT


@pytest.mark.parametrize(
    ("failure_mode", "expected_blocker"),
    [
        ("missing_command", "missing_command"),
        ("missing_exit_code", "missing_exit_code"),
        ("missing_raw_output_ref", "missing_raw_output_ref"),
        ("raw_output_ref_unverified", "raw_output_ref_not_verified"),
        ("missing_evidence_source", "missing_evidence_source"),
    ],
)
def test_public_convergence_verifier_evidence_gate_fails_closed(
    tmp_path: Path,
    failure_mode: str,
    expected_blocker: str,
) -> None:
    target = _write_initial_file(tmp_path)

    def verifier(_: DirectorRepairConvergenceVerifierRequestV1) -> DirectorRepairVerifierSnapshotInputV1:
        snapshot: dict[str, Any] = {
            "command": ("rtk", "tsc", "--noEmit"),
            "exit_code": 1,
            "raw_output_ref": "runtime/verifier/public-convergence-invalid.log",
            "metadata": _valid_verifier_metadata(),
        }
        if failure_mode == "missing_command":
            snapshot["command"] = ()
        elif failure_mode == "missing_exit_code":
            snapshot["exit_code"] = None
        elif failure_mode == "missing_raw_output_ref":
            snapshot["raw_output_ref"] = None
        elif failure_mode == "raw_output_ref_unverified":
            snapshot["metadata"] = _valid_verifier_metadata(raw_output_ref_verified=False)
        elif failure_mode == "missing_evidence_source":
            snapshot["metadata"] = _valid_verifier_metadata(evidence_source=None)
        return DirectorRepairVerifierSnapshotInputV1(**snapshot)

    result = run_director_repair_convergence(
        _command(tmp_path),
        writer=_writer(tmp_path),
        verifier=verifier,
    )

    assert result.ok is False
    assert result.converged is False
    assert result.status == "verifier_evidence_invalid"
    assert result.error_code == "verifier_evidence_invalid"
    assert result.receipts == ()
    assert result.rounds == ()
    assert result.final_diagnostics[0].code == "typescript_ts1005"
    assert result.metadata["owner_cell"] == "director.runtime"
    assert result.metadata["verifier_failure_reason"] == "verifier_evidence_invalid"
    assert result.metadata["evidence_blocker"] == expected_blocker
    assert expected_blocker in result.metadata["evidence_blockers"]
    assert result.metadata["round_number"] == 0
    assert target.read_text(encoding="utf-8") == _BROKEN_CONTENT
