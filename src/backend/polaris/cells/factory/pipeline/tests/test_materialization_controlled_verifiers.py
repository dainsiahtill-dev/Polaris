"""Controlled collector frontier; unit projections are not Group qualification."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import json
import os
import shutil
import subprocess
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerPortfolioTaskV1,
    VerificationCommandAuthorityV1,
    build_chief_engineer_blueprint_portfolio,
)
from polaris.cells.chief_engineer.blueprint.public.tests.test_public_contractsa import _portfolio_command_authority
from polaris.cells.factory.pipeline.internal import factory_materialization_impl as materialization
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.tests._characterization_helpers import _library_completion_requirements
from polaris.cells.factory.pipeline.tests.test_director_stage_materialization_settle import (
    canonical_settle_owner as canonical_settle_owner,
)
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import authored as authored
from polaris.cells.runtime.execution_broker.internal import project_verification_authority as broker_owner
from polaris.cells.runtime.execution_broker.public import project_verification as broker
from polaris.cells.runtime.execution_broker.tests.test_project_verification_receipts import _AuthorityPort, _bind_runner
from polaris.kernelone.quality.artifact_quality import ArtifactQualityEvidence


def _run() -> FactoryRun:
    return FactoryRun(
        id="pol54-unit",
        config=FactoryConfig(name="pol54"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-08T00:00:00+00:00",
    )


async def _collect(executor: Any, **kwargs: Any) -> Any:
    result = executor._collect_director_stage_materialization_diagnostics(**kwargs)
    return await result if inspect.isawaitable(result) else result


def _canonical_verifiers(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    files: tuple[str, ...],
    commands: tuple[tuple[str, tuple[str, ...], str], ...],
) -> tuple[Any, FactoryRun, Any]:
    """Real CE persistence and Broker seals; only command authority resolution is doubled."""
    monkeypatch.setenv("KERNELONE_HOME", "/home/dains/.polaris/test_authority/r26-controlled")
    task = ChiefEngineerPortfolioTaskV1(task_id="TASK-1", objective="Verify declared inputs", target_files=files)
    authorities = tuple(
        VerificationCommandAuthorityV1(task_id=task.task_id, modality=modality, argv=argv)
        for modality, argv, _profile in commands
    )
    test_path = next((path for path in files if "test" in path), files[-1])
    sources = tuple(path for path in files if path != test_path)
    completion = _library_completion_requirements(
        *sources, owner_task_ids=(task.task_id,) * len(sources), test_path=test_path, test_owner_task_id=task.task_id
    )
    completion["obligations"]["verification"] = [
        {
            "obligation_id": f"verify-{index}",
            "modality": authority.modality,
            "command_authority_hash": authority.authority_hash,
            "applicability": "required",
            "covers_obligation_ids": [row["obligation_id"] for row in completion["obligations"]["artifacts"]],
            "owner_task_id": task.task_id,
        }
        for index, authority in enumerate(authorities)
    ]
    portfolio = build_chief_engineer_blueprint_portfolio(
        BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(workspace),
            run_id="controlled-contract-run",
            tasks=(task,),
            **_portfolio_command_authority(
                tasks=(task,),
                project_kind="library",
                workspace=workspace,
                run_id="controlled-contract-run",
                project_id="unit-project",
                command_authority_override=authorities,
            ),
            llm_blueprint={
                "construction_plan": {"project_interface_contract": {}},
                "project_completion_contract": completion,
                "risk_flags": [],
            },
        )
    )
    contract = portfolio.project_completion_contract
    assert contract is not None

    class Authority:
        calls: list[broker.ResolveProjectVerificationAuthorityQueryV1]

        def __init__(self) -> None:
            self._base = _AuthorityPort()
            self.calls = []

        def resolve_project_artifact_authority(
            self, query: broker.ResolveProjectArtifactAuthorityQueryV1
        ) -> broker.ProjectArtifactExecutionAuthorityV1:
            result: broker.ProjectArtifactExecutionAuthorityV1 = self._base.resolve_project_artifact_authority(query)
            return result

        def consume_project_verification_execution_capability(
            self, command: broker.ConsumeProjectVerificationCapabilityCommandV1
        ) -> broker.ProjectVerificationCapabilityConsumptionV1:
            result: broker.ProjectVerificationCapabilityConsumptionV1 = (
                self._base.consume_project_verification_execution_capability(command)
            )
            return result

        def resolve_project_verification_authority(
            self, query: broker.ResolveProjectVerificationAuthorityQueryV1
        ) -> broker.ProjectVerificationExecutionAuthorityV1:
            self.calls.append(query)
            row = next(item for item in contract.obligations.verification if item.obligation_id == query.obligation_id)
            index = next(
                index
                for index, authority in enumerate(authorities)
                if authority.authority_hash == row.command_authority_hash
            )
            authority = authorities[index]
            executable = shutil.which(authority.argv[0])
            assert executable is not None
            executable_path = Path(executable).absolute()
            realpath = executable_path.resolve(strict=True)
            return replace(
                self._base.resolve_project_verification_authority(query),
                owner_task_id=authority.task_id,
                modality=authority.modality,
                argv=authority.argv,
                cwd=authority.cwd,
                command_authority_hash=authority.authority_hash,
                policy_profile_id=commands[index][2],
                executable_path=str(executable_path),
                executable_realpath=str(realpath),
                executable_hash=hashlib.sha256(realpath.read_bytes()).hexdigest(),
                input_artifacts=tuple(
                    broker.ProjectVerificationArtifactInputV1(obligation_id=item.obligation_id, path=item.path)
                    for item in contract.obligations.artifacts
                ),
            )

    authority = Authority()
    monkeypatch.setattr(broker_owner, "_EXECUTION_AUTHORITY_PORT", authority)
    executor = OrchestrationStageExecutor(workspace)
    run = _run()
    run.id = contract.run_id
    run.metadata["stage_results"] = {
        "chief_engineer_review": {"status": "success", "artifacts": [portfolio.portfolio_path]}
    }
    return executor, run, authority


class _ControlledRunner:
    """Physical-result projection double: Broker, not this fixture, seals receipts."""

    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.commands: list[tuple[str, ...]] = []

    def run(
        self,
        *,
        name: str,
        argv: tuple[str, ...],
        cwd: str,
        timeout_seconds: float,
        log_path: str,
        metadata: dict[str, str],
        on_launched: Any,
        control: Any = None,
    ) -> broker.ProjectVerificationProcessResultV1:
        del name, cwd, timeout_seconds, metadata, control
        self.commands.append(argv)
        outcome = self.outcome(argv) if callable(self.outcome) else self.outcome
        if isinstance(outcome, BaseException):
            raise outcome
        on_launched("controlled-execution", 4242, "controlled-process-start")
        output = str(
            outcome.get("stdout_tail", "") or outcome.get("stderr_tail", "") or outcome.get("error", "")
        ).encode("utf-8")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_bytes(output)
        return broker.ProjectVerificationProcessResultV1(
            exit_code=outcome.get("exit_code"), timed_out=outcome.get("timed_out", False), output_bytes=output
        )


@pytest.mark.asyncio
async def test_dispatch_persists_materialization_blocker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Projection unit: no tasks, grants, Provider, or positive Group execution."""
    executor = OrchestrationStageExecutor(tmp_path)
    monkeypatch.setattr(executor, "_build_orchestration_service", Mock(return_value=object()))
    # No task reaches dispatch/admission, so this unit never issues a grant.
    monkeypatch.setattr(executor, "_factory_role_evidence_cutoff_port", Mock(return_value=None))
    blocked = {"ok": False, "reason": "materialization_verification_blocked", "detail": "unit admission denial"}
    settle = AsyncMock(return_value=blocked)
    monkeypatch.setattr(executor, "_run_director_stage_materialization_quality_settle", settle)
    monkeypatch.setattr(executor, "_seal_director_stage_missing_tool_lifecycles", Mock(return_value={}))
    recovery = Mock(side_effect=AssertionError("recovery erased the verifier blocker"))
    monkeypatch.setattr(executor, "_recover_director_stage_authority_after_delivery_settle", recovery)
    context = {"project_id": "pol54", "factory_run_id": _run().id}
    result = await executor._execute_director_dispatch(_run(), context)
    assert result.status == "failed"
    assert settle.await_args is not None and settle.await_args.kwargs["context"] is context
    assert recovery.call_count == 0
    payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
    assert payload["error_code"] == "director.task_lineage_missing"
    assert payload["materialization_verification"]["reason"] == "materialization_verification_blocked"
    assert payload["materialization_verification_error_code"] == "director.materialization_verification_failed"
    assert payload["prior_dispatch_failure"]["error_code"] == "director.task_lineage_missing"
    assert payload["failure_stage"] == "director_dispatch"
    assert any(
        row["code"] == "director.stage_materialization_quality_settle" and row["ok"] is False
        for row in payload["signals"]
    )


def test_missing_ce_blocks_before_any_verifier_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A legacy live-cwd subprocess must never run before CE admission."""
    (tmp_path / "package.json").write_text('{"scripts":{"test":"node tests/verify.test.js"}}', encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/verify.test.js").write_text("console.log('unit');\n", encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    spawn = Mock(return_value=type("Exit", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr(broker_owner._ExecutionBrokerProjectVerificationRunner, "run", spawn)
    observation = asyncio.run(_collect(executor, run=_run()))
    assert spawn.call_count == 0
    assert observation.blockers
    assert "requires_committed_ce_stage" in str(observation.blockers)


@pytest.mark.asyncio
@pytest.mark.parametrize("compile_only,build_runs_tests", [(False, False), (True, False), (True, True)])
async def test_collector_builds_before_tests_that_consume_generated_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, compile_only: bool, build_runs_tests: bool
) -> None:
    """Actual Broker-sealed Node commands share generated outputs without changing inputs."""
    workspace = tmp_path / "source"
    workspace.mkdir()
    files = {
        "package.json": json.dumps(
            {
                "scripts": {
                    "build": "node build.js" + (" && node --test dist/tests/*.test.js" if build_runs_tests else ""),
                    "test": "npm run build" if compile_only else "node --test dist/tests/*.test.js",
                }
            }
        ),
        "build.js": (
            "const fs = require('node:fs');\n"
            "fs.mkdirSync('dist/tests', {recursive:true});\n"
            "fs.copyFileSync('tests/verify.test.js', 'dist/tests/verify.test.js');\n"
        ),
        "tests/verify.test.js": "require('node:test')('real test', () => require('node:assert/strict').equal(2 + 2, 4));\n",
    }
    for name, content in files.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    executor, run, _ = _canonical_verifiers(
        workspace,
        monkeypatch,
        files=tuple(files),
        commands=(
            ("build", ("npm", "run", "build"), "node.script_build"),
            ("test", ("npm", "test"), "node.script_test"),
        ),
    )
    monkeypatch.setattr(materialization, "node_dependency_readiness", Mock(return_value=SimpleNamespace(ready=True)))
    result = await _collect(executor, run=run, context={"project_id": "unit-project"})
    assert not result.blockers, result.to_dict()
    assert [row["command"] for row in result.verifier_results] == [["npm", "run", "build"], ["npm", "test"]]
    assert all(row["receipt_ref"] and row["receipt_hash"] for row in result.verifier_results)
    test = result.verifier_results[-1]
    if compile_only and not build_runs_tests:
        assert result
        assert test["exit_code"] == 0 and test["passed"] is False
        assert test["error"] == "project_verification_proof_failed"
        assert result.to_dict()["failed_required_verifiers"] == [["npm", "test"]]
    else:
        assert not result
        assert "# tests 1" in test["diagnostic_excerpt"]
        assert test["passed"] is True
    assert (workspace / "dist/tests/verify.test.js").read_text(encoding="utf-8") == files["tests/verify.test.js"]
    assert all((workspace / name).read_text(encoding="utf-8") == content for name, content in files.items())


def test_collector_consumes_static_inventory_without_second_tree_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Static inventory projection double; missing CE still rejects execution."""
    import polaris.kernelone.quality as quality

    (tmp_path / "package.json").write_text('{"scripts":{"test":"node tests/verify.test.js"}}', encoding="utf-8")
    monkeypatch.setattr(
        quality,
        "scan_workspace_artifact_quality_evidence",
        Mock(
            return_value=ArtifactQualityEvidence(
                scan_mode="pure_static", scanned_relative_paths=("package.json", "tests/verify.test.js")
            )
        ),
    )
    enumeration = Mock(side_effect=AssertionError("raw tree scan bypassed static inventory"))
    monkeypatch.setattr(Path, "rglob", enumeration)
    result = asyncio.run(_collect(OrchestrationStageExecutor(tmp_path), run=_run()))
    assert enumeration.call_count == 0
    assert "requires_committed_ce_stage" in str(result.blockers)
    assert not any("missing test source" in diagnostic for diagnostic in result)


def test_incomplete_static_evidence_blocks_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreadable scan cannot become empty-error success or a repair claim."""
    import polaris.kernelone.quality as quality

    (tmp_path / "package.json").write_text('{"name":"pol54"}', encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    scan = Mock(
        return_value=ArtifactQualityEvidence(
            scan_mode="pure_static", scan_complete=False, incomplete_reasons=("file_unreadable:src/main.ts",)
        )
    )
    monkeypatch.setattr(quality, "scan_workspace_artifact_quality_evidence", scan)
    claim = Mock(side_effect=AssertionError("repair claim reached before coverage admission"))
    monkeypatch.setattr(executor, "_claim_workspace_quality_repair_attempt", claim)
    result = asyncio.run(
        executor._run_director_stage_materialization_quality_settle(
            run=_run(), stage_status="failed", error_code="director.dispatch_timeout"
        )
    )
    assert claim.call_count == 0
    assert result["reason"] == "materialization_verification_blocked"
    assert result["validation_observations"][0]["static_evidence"]["scan_complete"] is False
    assert scan.call_args.kwargs["scan_mode"] == "pure_static"


def test_unknown_deferred_check_cannot_be_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Static unknown discovery cannot erase an actual failed owner-sealed verifier."""
    import polaris.kernelone.quality as quality
    from polaris.kernelone.quality.artifact_quality import ArtifactQualityDeferredCheck

    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.js").write_text("export const value = 1;\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/verify.test.js").write_text("require('node:test')('value', () => {});\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"scripts":{"test":"node tests/verify.test.js"}}', encoding="utf-8")
    executor, run, _ = _canonical_verifiers(
        tmp_path,
        monkeypatch,
        files=("src/main.js", "tests/verify.test.js", "package.json"),
        commands=(("test", ("npm", "test"), "node.script_test"),),
    )
    monkeypatch.setattr(
        quality,
        "scan_workspace_artifact_quality_evidence",
        Mock(
            return_value=ArtifactQualityEvidence(
                scan_mode="pure_static",
                deferred_checks=(ArtifactQualityDeferredCheck("unknown-required", ("src/main.js",)),),
            )
        ),
    )
    monkeypatch.setattr(materialization, "node_dependency_readiness", Mock(return_value=SimpleNamespace(ready=True)))
    _bind_runner(monkeypatch, _ControlledRunner({"exit_code": 1, "stderr_tail": "assertion failed"}))
    observation = asyncio.run(_collect(executor, run=run))
    assert observation and not observation.blockers
    assert observation.static_evidence["deferred_checks"][0]["check_id"] == "unknown-required"
    assert observation.verifier_results[0]["passed"] is False
    assert observation.verifier_results[0]["receipt_ref"]
    assert observation.to_dict()["failed_required_verifiers"] == [["npm", "test"]]
    assert observation.to_dict()["missing_required_verifiers"] == []
    assert materialization._materialization_observation_with_coverage(observation) is observation


def test_bare_diagnostics_do_not_authorize_settle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Old callbacks cannot omit coverage and gain a successful repair attempt."""
    (tmp_path / "package.json").write_text('{"name":"unit"}', encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    monkeypatch.setattr(executor, "_collect_director_stage_materialization_diagnostics", AsyncMock(return_value=[]))
    claim = Mock(side_effect=AssertionError("coverage-free collector claimed repair"))
    monkeypatch.setattr(executor, "_claim_workspace_quality_repair_attempt", claim)
    result = asyncio.run(
        executor._run_director_stage_materialization_quality_settle(
            run=_run(), stage_status="failed", error_code="director.dispatch_timeout"
        )
    )
    assert result["reason"] == "materialization_verification_blocked"
    assert "materialization_collector_coverage_required" in result["detail"]
    assert claim.call_count == 0


def test_incomplete_projection_cannot_bypass_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Settle rechecks scanner coverage even if a callback forgot its blocker."""
    (tmp_path / "package.json").write_text('{"name":"unit"}', encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    observation = materialization.MaterializationDiagnostics(
        [],
        static_evidence={
            "scan_mode": "pure_static",
            "scan_complete": False,
            "deferred_checks": [],
        },
    )
    monkeypatch.setattr(
        executor, "_collect_director_stage_materialization_diagnostics", AsyncMock(return_value=observation)
    )
    claim = Mock(side_effect=AssertionError("incomplete projection reached claim"))
    monkeypatch.setattr(executor, "_claim_workspace_quality_repair_attempt", claim)
    result = asyncio.run(
        executor._run_director_stage_materialization_quality_settle(
            run=_run(), stage_status="failed", error_code="director.dispatch_timeout"
        )
    )
    assert result["reason"] == "materialization_verification_blocked"
    assert claim.call_count == 0


def _factory_owner(executor: Any, authored: tuple[Any, Any, Any]) -> tuple[FactoryRun, dict[str, Any]]:
    portfolio, contract, fs = authored
    run = _run()
    run.id = contract.run_id
    run.metadata["stage_results"] = {
        "chief_engineer_review": {
            "status": "success",
            "artifacts": [portfolio.portfolio_path],
        }
    }
    executor._read_json_artifact_payload = fs.read_json
    return run, {"project_id": contract.project_id, "factory_run_id": run.id, "workspace": str(executor.workspace)}


def test_missing_context_project_id_uses_authenticated_ce_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing presentation identity uses the persisted CE contract, never a guessed project."""
    from polaris.cells.factory.pipeline.tests.test_materialization_contract_verifiers import _fixture

    executor, run, _ = _fixture(tmp_path, monkeypatch)
    captured: list[str] = []
    authorize = broker.authorize_project_verification_command

    def observe(query: broker.ResolveProjectVerificationAuthorityQueryV1) -> broker.RunProjectVerificationCommandV1:
        captured.append(query.project_id)
        return authorize(query)

    monkeypatch.setattr(broker, "authorize_project_verification_command", observe)
    context = {"factory_run_id": run.id, "workspace": str(executor.workspace)}
    observation = asyncio.run(_collect(executor, run=run, context=context))
    assert not observation.blockers, observation.to_dict()
    assert captured and set(captured) == {"unit-project"}
    assert "project_id" not in context
    assert all(row["receipt_ref"] for row in observation.verifier_results)


def test_dependency_change_reopens_validation_group_after_owned_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old groups close before preparation; canonical Broker execution does not fabricate a new group."""
    workspace = tmp_path / "preparation-project"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src/main.js").write_text("export const value = 1;\n", encoding="utf-8")
    (workspace / "tests").mkdir()
    (workspace / "tests/verify.test.js").write_text("require('node:test')('value', () => {});\n", encoding="utf-8")
    (workspace / "package.json").write_text('{"scripts":{"test":"node --test tests/verify.test.js"}}', encoding="utf-8")
    executor, run, _ = _canonical_verifiers(
        workspace,
        monkeypatch,
        files=("src/main.js", "tests/verify.test.js", "package.json"),
        commands=(("test", ("npm", "test"), "node.script_test"),),
    )
    old = SimpleNamespace(close=Mock())
    sessions: list[Any] = [old]
    monkeypatch.setattr(materialization, "node_dependency_readiness", Mock(return_value=SimpleNamespace(ready=False)))

    async def prepare(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        old.close.assert_called_once()
        assert sessions == []
        (workspace / "node_modules").mkdir()
        return {"passed": True, "exit_code": 0}

    preparation = AsyncMock(side_effect=prepare)
    monkeypatch.setattr(materialization, "_await_materialization_preparation", preparation)
    admission = Mock(side_effect=AssertionError("a disposable observation group became receipt authority"))
    monkeypatch.setattr(materialization.NativeValidationSession, "from_factory", admission)
    runner = _ControlledRunner({"exit_code": 0, "stdout_tail": "# tests 1\n# pass 1\n"})
    _bind_runner(monkeypatch, runner)
    observation = asyncio.run(_collect(executor, run=run, sessions=sessions))
    assert not observation.blockers, observation.to_dict()
    preparation.assert_awaited_once()
    admission.assert_not_called()
    assert sessions == []
    assert observation.preparation_results == [{"passed": True, "exit_code": 0}]
    assert observation.verifier_results[0]["receipt_ref"]


def test_real_untrusted_group_rejects_before_effect_or_repair(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller-owned isolation executable cannot become a trusted verification boundary."""
    from polaris.cells.factory.pipeline.tests.test_materialization_contract_verifiers import _fixture

    executor, run, _ = _fixture(tmp_path, monkeypatch)
    fake_tools = tmp_path_factory.mktemp("untrusted-tools")
    fake_bwrap = fake_tools / "bwrap"
    fake_bwrap.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    fake_bwrap.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_tools), prepend=os.pathsep)
    spawn = Mock(side_effect=AssertionError("untrusted sandbox reached physical runner"))
    monkeypatch.setattr(broker_owner._ExecutionBrokerProjectVerificationRunner, "run", spawn)
    claim = Mock(side_effect=AssertionError("untrusted sandbox claimed repair"))
    schedule = Mock(side_effect=AssertionError("untrusted sandbox entered schedule"))
    monkeypatch.setattr(executor, "_claim_workspace_quality_repair_attempt", claim)
    monkeypatch.setattr(executor, "_apply_workspace_quality_repairs", schedule)
    before = {
        path: (executor.workspace / path).read_bytes()
        for path in ("src/module.py", "tests/test_output.py", "package.json")
    }
    result = asyncio.run(
        executor._run_director_stage_materialization_quality_settle(
            run=run,
            stage_status="failed",
            error_code="director.dispatch_timeout",
        )
    )
    assert result["ok"] is False
    assert result["reason"] == "materialization_verification_blocked"
    assert "untrusted" in result["detail"]
    assert result["validation_observations"][0]["missing_required_verifiers"] == [
        ["python", "-m", "compileall", "-b", "src"],
        ["python", "-m", "pytest", "-q", "tests/test_output.py"],
    ]
    assert result["validation_observations"][0]["failed_required_verifiers"] == []
    assert spawn.call_count == claim.call_count == schedule.call_count == 0
    assert all((executor.workspace / path).read_bytes() == body for path, body in before.items())


@pytest.mark.parametrize(
    "kind, error",
    [
        ("wrong_run", "ce_run_mismatch"),
        ("mixed_ce", "ce_identity_ambiguous"),
        ("wrong_project", "context_project_mismatch"),
        ("wrong_workspace", "context_workspace_mismatch"),
        ("wrong_context_run", "context_run_mismatch"),
    ],
)
def test_identity_mismatch_never_opens_group(
    tmp_path: Path,
    authored: tuple[Any, Any, Any],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    error: str,
) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    run, context = _factory_owner(executor, authored)
    if kind == "wrong_run":
        run.id = "other-run"
        context["factory_run_id"] = run.id
    elif kind == "mixed_ce":
        run.metadata["stage_results"]["chief_engineer_review"]["artifacts"] *= 2
    elif kind == "wrong_project":
        context["project_id"] = "other-project"
    elif kind == "wrong_workspace":
        context["workspace"] = str(tmp_path / "foreign")
    else:
        context["run_id"] = "other-run"
    admission = Mock(side_effect=AssertionError("mismatched identity reached Broker command authority"))
    spawn = Mock(side_effect=AssertionError("mismatched identity spawned a verifier"))
    monkeypatch.setattr(broker, "authorize_project_verification_command", admission)
    monkeypatch.setattr(broker_owner._ExecutionBrokerProjectVerificationRunner, "run", spawn)
    observation = asyncio.run(_collect(executor, run=run, context=context))
    assert error in str(observation.blockers)
    assert admission.call_count == spawn.call_count == 0


@pytest.mark.parametrize(
    "outcome, kind, blocker",
    [
        ({"exit_code": 9, "passed": False, "stderr_tail": "unstructured failure"}, "nonzero", False),
        (
            {"exit_code": 0, "passed": False, "error": "command exited 0 but output contains compiler errors"},
            "verifier_failure",
            False,
        ),
        (
            {"exit_code": 0, "passed": True, "stdout_tail": "TAP version 13\n1..0\n# tests 0\n"},
            "verifier_failure",
            False,
        ),
        (PermissionError("denied"), "admission", True),
        (subprocess.TimeoutExpired(["npm", "test"], 0.01, output="partial"), "timeout", False),
        ({"exit_code": None, "passed": False, "error": "timeout after 0.1s"}, "timeout", False),
    ],
)
def test_controlled_failures_keep_command_exit_error_and_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: Any, kind: str, blocker: bool
) -> None:
    """Component runner outcomes go through the real Broker seal and current receipt query."""
    import polaris.kernelone.quality as quality

    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.js").write_text("export const value = 1;\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/verify.test.js").write_text("require('node:test')('value', () => {});\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"scripts":{"test":"node tests/verify.test.js"}}', encoding="utf-8")
    monkeypatch.setattr(
        quality,
        "scan_workspace_artifact_quality_evidence",
        Mock(return_value=ArtifactQualityEvidence(scan_mode="pure_static")),
    )
    executor, run, _ = _canonical_verifiers(
        tmp_path,
        monkeypatch,
        files=("src/main.js", "tests/verify.test.js", "package.json"),
        commands=(("test", ("npm", "test"), "node.script_test"),),
    )
    monkeypatch.setattr(materialization, "node_dependency_readiness", Mock(return_value=SimpleNamespace(ready=True)))
    if isinstance(outcome, subprocess.TimeoutExpired):
        outcome = {"exit_code": None, "timed_out": True, "stdout_tail": str(outcome.output or "")}
    elif isinstance(outcome, dict) and outcome.get("exit_code") is None:
        outcome = {**outcome, "timed_out": True}
    runner = _ControlledRunner(outcome)
    _bind_runner(monkeypatch, runner)
    result = asyncio.run(_collect(executor, run=run, context={"project_id": "unit-project"}))
    assert bool(result.blockers) is blocker, result.to_dict()
    row = result.verifier_results[0]
    assert row["command"] == ["npm", "test"]
    assert row["failure_kind"] == kind
    assert row["exit_code"] == (9 if kind == "nonzero" else 0 if kind == "verifier_failure" else None)
    projection = result.to_dict()
    if kind == "admission":
        assert not row.get("receipt_ref")
        assert projection["failed_required_verifiers"] == []
        assert projection["missing_required_verifiers"] == [["npm", "test"]]
        assert not result._owner_receipts
    else:
        assert "npm test failed" in str(result)
        assert row["receipt_ref"] and row["receipt_hash"]
        assert projection["failed_required_verifiers"] == [["npm", "test"]]
        assert projection["missing_required_verifiers"] == []
        assert type(result._owner_receipts[0]) is broker.ProjectVerificationReceiptV1


@pytest.mark.asyncio
async def test_cancellation_and_drain_error_escape_settle_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collector-interface double checks propagation, not physical drain."""
    from polaris.kernelone.process import ProcessTreeDrainError

    (tmp_path / "package.json").write_text('{"name":"unit"}', encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    for error in (asyncio.CancelledError("cancelled"), ProcessTreeDrainError("physical drain unknown")):
        monkeypatch.setattr(
            executor, "_collect_director_stage_materialization_diagnostics", AsyncMock(side_effect=error)
        )
        with pytest.raises(type(error)) as caught:
            await executor._run_director_stage_materialization_quality_settle(
                run=_run(), stage_status="failed", error_code="director.dispatch_timeout"
            )
        assert caught.value is error


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [True, False])
async def test_cleanup_error_cannot_replace_primary_lifecycle_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, cancelled: bool
) -> None:
    """Interface double: cleanup errors remain visible without inventing drain."""
    from polaris.kernelone.process import ProcessTreeDrainError

    (tmp_path / "package.json").write_text('{"name":"unit"}', encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    primary = asyncio.CancelledError("unit cancel") if cancelled else ProcessTreeDrainError("unit drain unknown")
    close = Mock(side_effect=OSError("unit close failed"))

    async def collect(**kwargs: Any) -> Any:
        kwargs["sessions"].append(SimpleNamespace(close=close))
        raise primary

    monkeypatch.setattr(executor, "_collect_director_stage_materialization_diagnostics", collect)
    with pytest.raises(type(primary)) as caught:
        await executor._run_director_stage_materialization_quality_settle(
            run=_run(), stage_status="failed", error_code="director.dispatch_timeout"
        )
    assert caught.value is primary
    assert close.call_count == 1
    assert "validation_cleanup_failed" in caplog.text
    assert "unit close failed" in caplog.text


def test_project_typecheck_does_not_erase_required_file_parser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both explicit CE lint obligations survive, including a source excluded from the project."""
    import polaris.kernelone.quality as quality
    from polaris.kernelone.quality.artifact_quality import ArtifactQualityDeferredCheck

    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src/main.ts").write_text("export const value = 1;\n", encoding="utf-8")
    (tmp_path / "tests/excluded.ts").write_text("export const broken: = 1;\n", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text('{"include":["src/main.ts"]}', encoding="utf-8")
    evidence = ArtifactQualityEvidence(
        scan_mode="pure_static",
        deferred_checks=(
            ArtifactQualityDeferredCheck("typescript_project_typecheck", ("src/main.ts",), project_relative_path="."),
            ArtifactQualityDeferredCheck("typescript_parser", ("tests/excluded.ts",)),
        ),
    )
    monkeypatch.setattr(quality, "scan_workspace_artifact_quality_evidence", Mock(return_value=evidence))
    executor, run, _ = _canonical_verifiers(
        tmp_path,
        monkeypatch,
        files=("src/main.ts", "tsconfig.json", "tests/excluded.ts"),
        commands=(
            ("lint", ("npx", "tsc", "-p", "tsconfig.json", "--noEmit"), "node.typescript_check"),
            ("lint", ("npx", "tsc", "--noEmit", "tests/excluded.ts"), "node.typescript_check"),
            ("test", ("python", "-m", "pytest", "-q"), "python.pytest"),
        ),
    )
    monkeypatch.setattr(materialization, "node_dependency_readiness", Mock(return_value=SimpleNamespace(ready=True)))

    def outcome(argv: tuple[str, ...]) -> dict[str, Any]:
        parser = "tests/excluded.ts" in argv
        return {
            "exit_code": 1 if parser else 0,
            "stdout_tail": "tests/excluded.ts: error TS1005: expected ';'" if parser else "",
        }

    runner = _ControlledRunner(outcome)
    _bind_runner(monkeypatch, runner)
    observation = asyncio.run(_collect(executor, run=run, context={"project_id": "unit-project"}))
    assert not observation.blockers, observation.to_dict()
    assert any("tests/excluded.ts" in argv for argv in runner.commands)
    assert any("TS1005" in diagnostic for diagnostic in observation)
    assert len(observation.verifier_results) == 3
    assert all(row["receipt_ref"] for row in observation.verifier_results[:2])
    assert observation.verifier_results[0]["passed"] is True
    assert observation.verifier_results[1]["passed"] is False
    assert not observation.verifier_results[2].get("receipt_ref")
    assert observation.to_dict()["missing_required_verifiers"] == [["python", "-m", "pytest", "-q"]]


class _LifecycleSession:
    """Unit lifecycle double only; byte hashes do not create Group qualification."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.before_hash = self.hash()
        self.phase = "baseline"
        self.events: list[tuple[str, str]] = []

    def hash(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def before_repair(self) -> None:
        self.before_hash = self.hash()
        self.events.append(("before", self.before_hash))
        self.phase = "baseline"

    def candidate(self, *, pending: Any, results: Any) -> None:
        assert pending["task_id"] == "TASK-1"
        assert pending["task_completion_projection"]["task_id"] == "TASK-1"
        assert results[0]["result"]["before_sha256"] == self.before_hash
        assert results[0]["result"]["after_sha256"] == self.hash()
        self.events.append(("candidate", self.hash()))
        self.phase = "candidate"

    def restored(self) -> None:
        assert self.hash() == self.before_hash
        self.events.append(("restored", self.hash()))
        self.phase = "restored"

    def close(self) -> None:
        self.events.append(("close", self.hash()))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["three_rounds", "restored", "drain_failure"])
async def test_candidate_restored_and_repeated_baselines_are_owned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    canonical_settle_owner: dict[str, Any],
    mode: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Interface-only lifecycle test; owner/effect doubles grant no physical positive."""
    import polaris.cells.roles.adapters.public as adapters
    import polaris.cells.runtime.task_runtime.public as runtime
    from polaris.kernelone.process import ProcessTreeDrainError

    path = tmp_path / "package.json"
    path.write_text('{"name":"unit","round":0}', encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    run = _run()
    run.id = "factory_test_m06_settle"
    session = _LifecycleSession(path)
    scans = 0
    commits = 0
    failure = ProcessTreeDrainError("unit physical owner drain unknown")

    async def collector(**kwargs: Any) -> materialization.MaterializationDiagnostics:
        nonlocal scans
        scans += 1
        if not kwargs["sessions"]:
            kwargs["sessions"].append(session)
        if scans > 1:
            assert session.phase in {"candidate", "restored"}
            session.events.append(("verify:" + session.phase, session.hash()))
        if mode == "drain_failure" and scans == 2:
            raise failure
        clean = scans == (4 if mode == "three_rounds" else 3)
        return materialization.MaterializationDiagnostics(
            [] if clean else [f"error-round-{scans}"],
            static_evidence={"scan_mode": "pure_static", "scan_complete": True},
        )

    candidate = {
        "tool": "repair",
        "result": {"status": "deferred_repair_effects_pending", "deferred_request": object()},
    }

    async def commit(**_kwargs: Any) -> list[dict[str, Any]]:
        nonlocal commits
        commits += 1
        if mode == "restored" and commits == 1:
            return []  # No effect committed; original baseline must be revalidated.
        before = session.hash()
        path.write_text('{"name":"unit","round":' + str(commits) + "}", encoding="utf-8")
        return [
            {
                "success_count": 1,
                "failure_count": 0,
                "results": [
                    {
                        "status": "success",
                        "effect_receipt": {"unit_only": True},
                        "result": {"before_sha256": before, "after_sha256": session.hash()},
                    }
                ],
            }
        ]

    monkeypatch.setattr(executor, "_collect_director_stage_materialization_diagnostics", collector)
    monkeypatch.setattr(
        executor,
        "_claim_workspace_quality_repair_attempt",
        Mock(return_value=("TASK-1", 1, SimpleNamespace(workspace=str(tmp_path)), canonical_settle_owner)),
    )
    monkeypatch.setattr(
        executor,
        "_apply_workspace_quality_repairs",
        Mock(return_value=([candidate, candidate] if mode == "restored" else [candidate], {})),
    )
    monkeypatch.setattr(
        executor, "_settle_director_stage_materialization_attempt", Mock(return_value={"success": True})
    )
    monkeypatch.setattr(adapters, "commit_materialization_deferred_repairs", commit)
    monkeypatch.setattr(runtime, "create_task_runtime_execution_attempt_authority", Mock(return_value=object()))
    if mode == "drain_failure":
        with pytest.raises(ProcessTreeDrainError) as caught:
            await executor._run_director_stage_materialization_quality_settle(
                run=run, stage_status="failed", error_code="director.dispatch_timeout"
            )
        assert caught.value is failure
        assert "validation_lifecycle" in caplog.text
        assert "unit physical owner drain unknown" in caplog.text
    else:
        result = await executor._run_director_stage_materialization_quality_settle(
            run=run, stage_status="failed", error_code="director.dispatch_timeout"
        )
        assert result["ok"] is True
        assert scans == (4 if mode == "three_rounds" else 3)
        assert commits == (3 if mode == "three_rounds" else 2)
        phases = [phase for phase, _digest in session.events]
        assert phases.count("candidate") == (3 if mode == "three_rounds" else 1)
        if mode == "restored":
            assert "verify:restored" in phases
    assert session.events[-1][0] == "close"


@pytest.mark.parametrize("replacement", ["manifest_symlink", "workspace_symlink", "manifest_replace"])
def test_manifest_scan_race_never_reads_external_content_or_admits_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    """Real scanner/read boundary; filesystem identities and trust gate stay real."""
    import polaris.kernelone.quality as quality
    from polaris.cells.factory.pipeline.internal import factory_node_dependencies as dependencies

    manifest = tmp_path / "package.json"
    manifest.write_text('{"scripts":{"test":"node tests/verify.test.js"}}', encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/verify.test.js").write_text("console.log('fixture');", encoding="utf-8")
    outside = tmp_path.parent / (tmp_path.name + "-external")
    outside.mkdir()
    sentinel = "external-content-must-not-be-read"
    (outside / "package.json").write_text(json.dumps({"name": sentinel}), encoding="utf-8")
    saved = tmp_path.parent / (tmp_path.name + "-saved")
    scanner = quality.scan_workspace_artifact_quality_evidence
    original_read = Path.read_text
    external_reads: list[str] = []

    def observe_read(path: Path, *args: Any, **kwargs: Any) -> str:
        text = original_read(path, *args, **kwargs)
        if sentinel in text:
            external_reads.append(str(path))
        return text

    def scan_then_replace(*args: Any, **kwargs: Any) -> Any:
        evidence = scanner(*args, **kwargs)
        assert evidence.scan_complete, evidence.incomplete_reasons
        if replacement == "workspace_symlink":
            tmp_path.rename(saved)
            tmp_path.symlink_to(outside, target_is_directory=True)
        else:
            manifest.unlink()
            if replacement == "manifest_symlink":
                manifest.symlink_to(outside / "package.json")
            else:
                manifest.write_text('{"name":"replacement"}', encoding="utf-8")
        return evidence

    monkeypatch.setattr(Path, "read_text", observe_read)
    monkeypatch.setattr(quality, "scan_workspace_artifact_quality_evidence", scan_then_replace)
    executor = OrchestrationStageExecutor(tmp_path)
    admission = Mock(side_effect=AssertionError("manifest race reached Broker command authority"))
    authentication = Mock(side_effect=AssertionError("manifest race reached CE authentication"))
    spawn = Mock(side_effect=AssertionError("manifest race spawned a verifier"))
    preparation = Mock(side_effect=AssertionError("manifest race started preparation"))
    claim = Mock(side_effect=AssertionError("manifest race claimed repair"))
    schedule = Mock(side_effect=AssertionError("manifest race entered repair schedule"))
    monkeypatch.setattr(broker, "authorize_project_verification_command", admission)
    monkeypatch.setattr(dependencies, "authenticated_preparation_contract", authentication)
    monkeypatch.setattr(broker_owner._ExecutionBrokerProjectVerificationRunner, "run", spawn)
    monkeypatch.setattr(executor, "_ensure_director_stage_materialization_typescript_toolchain", preparation)
    monkeypatch.setattr(executor, "_claim_workspace_quality_repair_attempt", claim)
    monkeypatch.setattr(executor, "_apply_workspace_quality_repairs", schedule)
    try:
        result = asyncio.run(
            executor._run_director_stage_materialization_quality_settle(
                run=_run(), stage_status="failed", error_code="director.dispatch_timeout"
            )
        )
        assert external_reads == []
        assert result["reason"] == "materialization_verification_blocked"
        assert "snapshot" in result["detail"]
        assert admission.call_count == claim.call_count == schedule.call_count == 0
        assert authentication.call_count == spawn.call_count == preparation.call_count == 0
    finally:
        if replacement == "workspace_symlink":
            tmp_path.unlink()
            saved.rename(tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat_cancel", [False, True])
async def test_preparation_failure_after_cancel_preserves_original_cancel_and_waits_for_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, repeat_cancel: bool
) -> None:
    """Real preparation thread completion; this is not namespace drain qualification."""
    started = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()
    loop = asyncio.get_running_loop()
    original_shield = asyncio.shield
    cancellations: list[asyncio.CancelledError] = []

    async def observed_shield(owner: Any) -> Any:
        try:
            return await original_shield(owner)
        except asyncio.CancelledError as exc:
            cancellations.append(exc)
            raise

    def preparation(*, run: FactoryRun) -> Any:
        assert run.id == "pol54-unit"
        loop.call_soon_threadsafe(started.set)
        try:
            assert release.wait(5), "test did not release preparation owner"
            raise OSError("preparation failed after caller cancel")
        finally:
            finished.set()

    executor = SimpleNamespace(_ensure_director_stage_materialization_typescript_toolchain=preparation)
    monkeypatch.setattr(asyncio, "shield", observed_shield)
    preparations: list[dict[str, Any]] = []
    caller = asyncio.create_task(
        materialization._await_materialization_preparation(executor, _run(), preparation_results=preparations)
    )
    try:
        await asyncio.wait_for(started.wait(), 2)
        caller.cancel("original caller cancel")
        await asyncio.sleep(0)
        assert not caller.done() and not finished.is_set()
        if repeat_cancel:
            caller.cancel("second caller cancel")
            await asyncio.sleep(0)
            assert not caller.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError) as caught:
            await caller
        assert caught.value is cancellations[0]
        assert str(caught.value) == "original caller cancel"
        assert finished.is_set()
        assert len(preparations) == 1
        assert preparations[0]["error_type"] == "OSError" and preparations[0]["passed"] is False
        assert preparations[0]["authoritative_receipt"] is False
        assert "materialization_preparation_failed_after_cancel" in caplog.text
        assert "preparation failed after caller cancel" in caplog.text
        assert '"drain_proven": false' in caplog.text
    finally:
        release.set()
        if not caller.done():
            with contextlib.suppress(asyncio.CancelledError, OSError):
                await caller
