"""Tests for adapter-owned Director repair convergence verifier factories."""

from __future__ import annotations

import ast
import json
import shlex
import sys
from pathlib import Path
from typing import Any, cast

from polaris.cells.director.runtime.public import (
    DirectorRepairConvergenceVerifierRequestV1,
    DirectorRepairEnvironmentPrepPlanV1,
    DirectorRepairVerifierSnapshotInputV1,
)
from polaris.cells.roles.adapters.internal.director import repair_convergence_verifier as verifier_module
from polaris.cells.roles.adapters.internal.director.repair_convergence_verifier import (
    build_artifact_quality_convergence_verifier,
    build_step_verify_convergence_verifier,
)

_EVIDENCE_SOURCE = "adapter_convergence_verifier_factory"
_FORBIDDEN_IMPORT_PREFIX = "polaris.cells.director.runtime.internal.repair_kernel"


def _request(
    workspace: Path,
    *,
    task_id: str = "task-verifier",
    round_number: int = 0,
    environment_prep_plans: tuple[DirectorRepairEnvironmentPrepPlanV1, ...] = (),
) -> (
    DirectorRepairConvergenceVerifierRequestV1
):
    return DirectorRepairConvergenceVerifierRequestV1(
        task_id=task_id,
        workspace=str(workspace),
        round_number=round_number,
        source_tools=("deterministic_patch_residue_cleanup",),
        environment_prep_plans=environment_prep_plans,
    )


def _raw_output_payload(snapshot: DirectorRepairVerifierSnapshotInputV1) -> dict[str, Any]:
    assert snapshot.raw_output_ref is not None
    raw_output_path = Path(snapshot.raw_output_ref)
    assert raw_output_path.is_file()
    raw_text = raw_output_path.read_text(encoding="utf-8")
    assert raw_text.strip()
    payload: object = json.loads(raw_text)
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


def _python_command(source: str) -> str:
    return shlex.join([sys.executable, "-c", source])


def _safe_grep_command(path: str, pattern: str) -> str:
    return " ".join(
        (
            "test",
            "-f",
            shlex.quote(f"./{path}"),
            "&&",
            "grep",
            "-q",
            shlex.quote(pattern),
            shlex.quote(f"./{path}"),
        )
    )


def test_step_verify_convergence_verifier_success_writes_verified_raw_output(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    (tmp_path / "verify.txt").write_text("verify ok\n", encoding="utf-8")
    command = _safe_grep_command("verify.txt", "verify ok")
    policy_calls: list[tuple[str, str]] = []

    def policy(*, command: str, cwd: str) -> dict[str, Any]:
        policy_calls.append((command, cwd))
        return {
            "allowed": True,
            "reason": "kernelone policy allowed test-grep verify",
            "blocked_clauses": (),
            "blocked_tokens": (),
            "policy_source": "kernelone_policy_safe_fixture",
        }

    monkeypatch.setattr(
        verifier_module._step_verify_module,
        "assess_step_verify_command_safety",
        policy,
        raising=False,
    )
    verifier = build_step_verify_convergence_verifier(
        tmp_path,
        task_id="task-verifier",
        verify_command=command,
        log_root=tmp_path / "logs",
    )

    snapshot = verifier(_request(tmp_path))

    assert policy_calls == [(command, str(tmp_path.resolve()))]
    assert snapshot.exit_code == 1
    assert "missing authoritative execute_command effect receipt" in snapshot.residual_artifact_quality_errors[0]
    assert snapshot.command == ("/bin/sh", "-c", command)
    assert snapshot.metadata["evidence_source"] == _EVIDENCE_SOURCE
    assert snapshot.metadata["command_kind"] == "shell_step_verify"
    assert snapshot.metadata["raw_output_ref_verified"] is True
    assert snapshot.metadata["command_safety_allowed"] is True
    assert snapshot.metadata["command_safety_reason"] == "kernelone policy allowed test-grep verify"
    assert snapshot.metadata["step_verify_safety_policy_source"] == "kernelone_policy_safe_fixture"
    payload = _raw_output_payload(snapshot)
    assert payload["exit_code"] == 1
    assert "roles.kernel directed-effect authority" in payload["output"]
    assert payload["residual_artifact_quality_errors"]
    assert payload["metadata"]["command_safety_allowed"] is True
    assert payload["metadata"]["step_verify_safety_policy_source"] == "kernelone_policy_safe_fixture"


def test_step_verify_convergence_verifier_runs_environment_prep_before_verify(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    (tmp_path / "package.json").write_text('{"dependencies":{"left-pad":"1.3.0"}}\n', encoding="utf-8")
    command = "test -f ./package.json"
    plan = DirectorRepairEnvironmentPrepPlanV1(
        plan_id="environment-prep-plan",
        ecosystem="node",
        package_manager="npm",
        manifest="package.json",
        command=("npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"),
        freshness_key="fresh-node-key",
        source_receipt_id="repair-receipt",
        policy={
            "command_source": "director.runtime.environment_prep_catalog",
            "command_template_version": "director.environment_prep.command_templates.v1",
            "llm_generated_command_allowed": False,
            "agi_execution_authority": False,
            "authoritative_repair": False,
            "global_writes_allowed": False,
        },
        requirement={"manifest": "package.json"},
    )
    verifier = build_step_verify_convergence_verifier(
        tmp_path,
        task_id="task-verifier",
        verify_command=command,
        log_root=tmp_path / "logs",
    )

    snapshot = verifier(_request(tmp_path, round_number=1, environment_prep_plans=(plan,)))

    assert snapshot.exit_code == 1
    assert "environment_prep_directed_effect_required" in snapshot.residual_artifact_quality_errors[0]
    assert snapshot.metadata["environment_prep_required"] is True
    assert snapshot.metadata["environment_prep_plan_count"] == 1
    assert snapshot.metadata["environment_prep_failed"] is True
    prep_receipt = snapshot.metadata["environment_prep_receipts"][0]
    assert prep_receipt["schema_version"] == "director.environment_prep_receipt.v1"
    assert prep_receipt["status"] == "failed"
    assert prep_receipt["error_code"] == "environment_prep_directed_effect_required"
    assert prep_receipt["command"] == list(plan.command)
    assert prep_receipt["freshness_key"] == "fresh-node-key"
    payload = _raw_output_payload(snapshot)
    assert payload["environment_prep_receipts"][0]["status"] == "failed"


def test_step_verify_convergence_verifier_missing_raw_output_marks_missing_evidence(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    (tmp_path / "verify.txt").write_text("verify ok\n", encoding="utf-8")
    command = _safe_grep_command("verify.txt", "verify ok")

    def fake_write_raw_output_log(*_: Any, **__: Any) -> tuple[None, dict[str, Any]]:
        return (
            None,
            {
                "raw_output_ref_verified": False,
                "raw_output_write_failed": True,
                "raw_output_log_attempts": [str(tmp_path / "logs")],
            },
        )

    monkeypatch.setattr(verifier_module, "_write_raw_output_log", fake_write_raw_output_log)
    verifier = build_step_verify_convergence_verifier(
        tmp_path,
        task_id="task-verifier",
        verify_command=command,
        log_root=tmp_path / "logs",
    )

    snapshot = verifier(_request(tmp_path))

    assert snapshot.raw_output_ref is None
    assert snapshot.exit_code == 1
    assert "raw output log could not be written" in snapshot.residual_artifact_quality_errors[-1]
    assert snapshot.metadata["raw_output_ref_verified"] is False
    assert snapshot.metadata["raw_output_evidence_missing"] is True
    assert snapshot.metadata["revalidation_failure_reason"] == "missing_revalidation_evidence"
    assert snapshot.metadata["evidence_status"] == "missing_evidence"


def test_step_verify_convergence_verifier_failure_reports_residual_and_raw_output(tmp_path: Path) -> None:
    command = "test -f ./missing-verify.txt"
    verifier = build_step_verify_convergence_verifier(
        tmp_path,
        task_id="task-verifier",
        verify_command=command,
        log_root=tmp_path / "logs",
    )

    snapshot = verifier(_request(tmp_path))

    assert snapshot.exit_code != 0
    assert snapshot.residual_artifact_quality_errors
    assert "Step verify pending" in snapshot.residual_artifact_quality_errors[0]
    assert snapshot.metadata["evidence_source"] == _EVIDENCE_SOURCE
    assert snapshot.metadata["command_kind"] == "shell_step_verify"
    assert snapshot.metadata["raw_output_ref_verified"] is True
    assert snapshot.metadata["command_safety_allowed"] is True
    payload = _raw_output_payload(snapshot)
    assert payload["exit_code"] != 0
    assert payload["residual_artifact_quality_errors"]
    assert payload["metadata"]["command_safety_allowed"] is True


def test_step_verify_convergence_verifier_rejects_unsafe_command_without_execution(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    command = _python_command(
        "from pathlib import Path; Path('unsafe-executed.txt').write_text('bad', encoding='utf-8')"
    )
    policy_calls: list[tuple[str, str]] = []

    def policy(*, command: str, cwd: str) -> dict[str, Any]:
        policy_calls.append((command, cwd))
        return {
            "allowed": False,
            "reason": "kernelone policy blocked inline python",
            "blocked_clauses": (command,),
            "blocked_tokens": ("kernel-policy-python", "kernel-policy-inline"),
            "policy_source": "kernelone_policy_unsafe_fixture",
        }

    monkeypatch.setattr(
        verifier_module._step_verify_module,
        "assess_step_verify_command_safety",
        policy,
        raising=False,
    )
    verifier = build_step_verify_convergence_verifier(
        tmp_path,
        task_id="task-verifier",
        verify_command=command,
        log_root=tmp_path / "logs",
    )

    snapshot = verifier(_request(tmp_path))

    assert policy_calls == [(command, str(tmp_path.resolve()))]
    assert not (tmp_path / "unsafe-executed.txt").exists()
    assert snapshot.exit_code != 0
    assert snapshot.raw_output_ref
    assert snapshot.metadata["evidence_source"] == _EVIDENCE_SOURCE
    assert snapshot.metadata["raw_output_ref_verified"] is True
    assert snapshot.metadata["command_safety_allowed"] is False
    assert snapshot.metadata["command_safety_reason"] == "kernelone policy blocked inline python"
    assert snapshot.metadata["step_verify_safety_policy_source"] == "kernelone_policy_unsafe_fixture"
    assert snapshot.metadata["blocked_tokens"] == ["kernel-policy-python", "kernel-policy-inline"]
    assert snapshot.metadata["blocked_clauses"] == [command]
    assert "rejected by safety policy" in snapshot.residual_artifact_quality_errors[0]
    payload = _raw_output_payload(snapshot)
    assert payload["exit_code"] != 0
    assert payload["metadata"]["command_safety_allowed"] is False
    assert payload["metadata"]["command_safety_reason"] == snapshot.metadata["command_safety_reason"]
    assert payload["metadata"]["blocked_tokens"] == snapshot.metadata["blocked_tokens"]
    assert payload["metadata"]["step_verify_safety_policy_source"] == "kernelone_policy_unsafe_fixture"
    assert "rejected by safety policy" in payload["output"]


def test_artifact_quality_convergence_verifier_failure_reports_real_scan_output(tmp_path: Path) -> None:
    bad_source = tmp_path / "bad.py"
    bad_source.write_text("def broken(:\n    pass\n", encoding="utf-8")
    verifier = build_artifact_quality_convergence_verifier(
        tmp_path,
        task_id="task-verifier",
        relative_paths=("bad.py",),
        log_root=tmp_path / "logs",
    )

    snapshot = verifier(_request(tmp_path))

    assert snapshot.exit_code != 0
    assert snapshot.residual_artifact_quality_errors
    assert snapshot.residual_artifact_quality_issues
    issue = snapshot.residual_artifact_quality_issues[0]
    assert issue["code"] == "syntax_error"
    assert issue["path"] == "bad.py"
    assert issue["metadata"]["raw"] == snapshot.residual_artifact_quality_errors[0]
    assert snapshot.metadata["evidence_source"] == _EVIDENCE_SOURCE
    assert snapshot.metadata["command_kind"] == "in_process_artifact_quality_scan"
    assert snapshot.metadata["raw_output_ref_verified"] is True
    assert snapshot.command[:2] == (
        "polaris.kernelone.quality.artifact_quality.scan_workspace_artifact_quality_evidence",
        str(tmp_path.resolve()),
    )
    assert snapshot.metadata["typed_artifact_quality_issue_count"] == len(snapshot.residual_artifact_quality_issues)
    payload = _raw_output_payload(snapshot)
    assert payload["exit_code"] != 0
    assert payload["residual_artifact_quality_errors"]
    assert payload["residual_artifact_quality_issues"] == [dict(item) for item in snapshot.residual_artifact_quality_issues]
    assert payload["metadata"]["command_kind"] == "in_process_artifact_quality_scan"


def test_repair_convergence_verifier_factory_does_not_import_runtime_internal_repair_kernel() -> None:
    module_path = (
        Path(__file__).resolve().parent.parent / "internal" / "director" / "repair_convergence_verifier.py"
    )
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.append(node.module or "")

    assert _FORBIDDEN_IMPORT_PREFIX not in source
    assert all(
        module != _FORBIDDEN_IMPORT_PREFIX and not module.startswith(f"{_FORBIDDEN_IMPORT_PREFIX}.")
        for module in imported_modules
    )
    assert "polaris.cells.director.runtime.public" in imported_modules
