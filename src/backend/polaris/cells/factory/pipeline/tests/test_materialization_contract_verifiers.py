"""Static uncertainty cannot replace source-bound, owner-sealed verifier execution."""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerPortfolioTaskV1,
    ProjectCompletionContractV1,
    VerificationCommandAuthorityV1,
    build_chief_engineer_blueprint_portfolio,
)
from polaris.cells.chief_engineer.blueprint.public.tests.test_public_contractsa import _portfolio_command_authority
from polaris.cells.factory.pipeline.internal import factory_materialization_impl as materialization
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.internal.factory_run_service import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.tests._characterization_helpers import _library_completion_requirements
from polaris.cells.runtime.execution_broker.internal import project_verification_authority as broker_owner
from polaris.cells.runtime.execution_broker.public import project_verification as broker
from polaris.cells.runtime.execution_broker.tests.test_project_verification_receipts import _AuthorityPort


def _fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, failing: bool = False, entrypoint: bool = False
) -> tuple[Any, FactoryRun, Any]:
    monkeypatch.setenv("KERNELONE_HOME", "/home/dains/.polaris/test_authority/r26-verifier")
    workspace = tmp_path / "project"
    workspace.mkdir()
    files = {
        "src/module.py": "value = 7\n",
        "tests/test_output.py": (
            "from pathlib import Path\n"
            "import importlib.machinery\n"
            "def test_generated_output():\n"
            "    path = Path('src/module.pyc')\n"
            "    assert path.is_file(), 'build output missing'\n"
            "    module = importlib.machinery.SourcelessFileLoader('built_module', str(path)).load_module()\n"
            f"    assert module.value == {8 if failing else 7}\n"
        ),
        "package.json": '{"scripts":{"advisory":"npx --version","inspect":"node -e \\"void 0\\""}}',
    }
    if entrypoint:
        files["src/entrypoint.py"] = "import time\nprint('ready', flush=True)\ntime.sleep(60)\n"
    for name, text in files.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    task = ChiefEngineerPortfolioTaskV1(
        task_id="TASK-1",
        objective="Build and test module",
        target_files=tuple(files),
        entrypoint_targets=("src/entrypoint.py",) if entrypoint else (),
    )
    authorities: tuple[VerificationCommandAuthorityV1, ...] = (
        VerificationCommandAuthorityV1(
            task_id=task.task_id, modality="build", argv=("python", "-m", "compileall", "-b", "src")
        ),
        VerificationCommandAuthorityV1(
            task_id=task.task_id, modality="test", argv=("python", "-m", "pytest", "-q", "tests/test_output.py")
        ),
    )
    if entrypoint:
        authorities += (
            VerificationCommandAuthorityV1(
                task_id=task.task_id, modality="entrypoint", argv=("python", "src/entrypoint.py")
            ),
            VerificationCommandAuthorityV1(
                task_id=task.task_id, modality="environment_prep", argv=("python", "-m", "compileall", "-b", "src")
            ),
        )
    completion = _library_completion_requirements(
        "src/module.py",
        owner_task_ids=(task.task_id,),
        test_path="tests/test_output.py",
        test_owner_task_id=task.task_id,
    )
    for row, authority in zip(completion["obligations"]["verification"][:2], authorities[:2], strict=True):
        row["command_authority_hash"] = authority.authority_hash
    if entrypoint:
        completion["obligations"]["artifacts"].append(
            {
                "obligation_id": "artifact-entrypoint",
                "path": "src/entrypoint.py",
                "semantic_role": "entrypoint",
                "applicability": "required",
                "owner_task_id": task.task_id,
            }
        )
        completion["obligations"]["entrypoints"] = [
            {
                "obligation_id": "entrypoint-cli",
                "kind": "cli",
                "applicability": "required",
                "owner_task_id": task.task_id,
                "source_path": "src/entrypoint.py",
                "runtime_path": None,
                "command": authorities[2].command,
            }
        ]
        completion["obligations"]["verification"].append(
            {
                "obligation_id": "verify-entrypoint",
                "modality": "entrypoint",
                "command_authority_hash": authorities[2].authority_hash,
                "applicability": "required",
                "covers_obligation_ids": ["artifact-1"],
                "owner_task_id": task.task_id,
            }
        )
    portfolio = build_chief_engineer_blueprint_portfolio(
        BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(workspace),
            run_id="run-contract-verifiers",
            tasks=(task,),
            **_portfolio_command_authority(
                tasks=(task,),
                project_kind="application" if entrypoint else "library",
                catalog_snapshot_override={"project_kind": "application"} if entrypoint else None,
                workspace=workspace,
                run_id="run-contract-verifiers",
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
    assert type(portfolio.project_completion_contract) is ProjectCompletionContractV1
    contract = portfolio.project_completion_contract
    executor = OrchestrationStageExecutor(workspace)
    run = FactoryRun(
        id=contract.run_id,
        config=FactoryConfig(name=contract.project_id),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-13T00:00:00+00:00",
    )
    run.metadata["stage_results"] = {
        "chief_engineer_review": {"status": "success", "artifacts": [portfolio.portfolio_path]}
    }

    class Authority(_AuthorityPort):
        denied = False

        def resolve_project_verification_authority(
            self, query: broker.ResolveProjectVerificationAuthorityQueryV1
        ) -> broker.ProjectVerificationExecutionAuthorityV1:
            if self.denied:
                raise ValueError("project_verification_command_policy_rejected")
            row = next(item for item in contract.obligations.verification if item.obligation_id == query.obligation_id)
            authority = next(item for item in authorities if item.authority_hash == row.command_authority_hash)
            base = super().resolve_project_verification_authority(query)
            return replace(
                base,
                owner_task_id=authority.task_id,
                modality=authority.modality,
                argv=authority.argv,
                cwd=authority.cwd,
                command_authority_hash=authority.authority_hash,
                policy_profile_id={
                    "build": "python.compileall",
                    "test": "python.pytest",
                    "entrypoint": "python.script_entrypoint",
                    "environment_prep": "python.compileall",
                }[authority.modality],
                input_artifacts=tuple(
                    broker.ProjectVerificationArtifactInputV1(obligation_id=item.obligation_id, path=item.path)
                    for item in contract.obligations.artifacts
                ),
            )

    port = Authority()
    monkeypatch.setattr(broker_owner, "_EXECUTION_AUTHORITY_PORT", port)
    return executor, run, port


def test_static_unknown_runs_exact_ce_commands_and_keeps_generated_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, _ = _fixture(tmp_path, monkeypatch)
    before = hashlib.sha256((executor.workspace / "src/module.py").read_bytes()).hexdigest()
    observation = asyncio.run(materialization._collect_director_stage_materialization_diagnostics(executor, run=run))
    assert not observation.blockers, observation.to_dict()
    assert not observation, observation.to_dict()
    assert [row["command"] for row in observation.verifier_results] == [
        ["python", "-m", "compileall", "-b", "src"],
        ["python", "-m", "pytest", "-q", "tests/test_output.py"],
    ]
    assert all(row["receipt_ref"] and row["passed"] for row in observation.verifier_results)
    assert (executor.workspace / "src/module.pyc").is_file()
    assert hashlib.sha256((executor.workspace / "src/module.py").read_bytes()).hexdigest() == before
    assert observation.static_evidence["scan_complete"] is False
    assert not materialization._materialization_observation_with_coverage(observation).blockers


def test_real_failed_receipt_supplies_current_diagnostic_feedback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, _ = _fixture(tmp_path, monkeypatch, failing=True)
    observation = asyncio.run(materialization._collect_director_stage_materialization_diagnostics(executor, run=run))
    assert not observation.blockers, observation.to_dict()
    failed = observation.verifier_results[-1]
    assert failed["receipt_ref"]
    assert failed["exit_code"] == 1 and failed["passed"] is False
    assert "tests/test_output.py" in "\n".join(observation)
    assert "assert 7 == 8" in "\n".join(observation)
    assert "AssertionError" in "\n".join(observation)
    assert observation.to_dict()["failed_required_verifiers"] == [failed["command"]]


def test_explicit_authority_denial_remains_no_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executor, run, port = _fixture(tmp_path, monkeypatch)
    port.denied = True
    spawn = Mock(side_effect=AssertionError("denied command was spawned"))
    monkeypatch.setattr(broker_owner._ExecutionBrokerProjectVerificationRunner, "run", spawn)
    observation = asyncio.run(materialization._collect_director_stage_materialization_diagnostics(executor, run=run))
    assert observation.blockers
    assert "command_policy_rejected" in str(observation.blockers)
    assert spawn.call_count == 0


def test_execution_success_without_owner_receipt_cannot_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executor, run, _ = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        broker,
        "run_project_verification",
        lambda _command, **_kwargs: broker.ProjectVerificationExecutionResultV1(
            code="project_verification_executed", spawned=False, receipt=None
        ),
    )
    observation = asyncio.run(materialization._collect_director_stage_materialization_diagnostics(executor, run=run))
    assert observation.blockers
    assert "receipt" in str(observation.blockers)
    assert not any(row.get("passed") is True for row in observation.verifier_results)


def test_original_deadline_cannot_be_extended_by_verifier_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, _ = _fixture(tmp_path, monkeypatch)
    spawn = Mock(side_effect=AssertionError("expired command was spawned"))
    monkeypatch.setattr(broker_owner._ExecutionBrokerProjectVerificationRunner, "run", spawn)
    observation = asyncio.run(
        materialization._collect_director_stage_materialization_diagnostics(
            executor, run=run, context={"factory_run_deadline_epoch_seconds": time.time() - 1}
        )
    )
    assert "deadline_expired" in str(observation.blockers)
    assert spawn.call_count == 0


def test_owner_sealed_entrypoint_readiness_is_not_rejudged_as_failed_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, _ = _fixture(tmp_path, monkeypatch, entrypoint=True)
    observation = asyncio.run(materialization._collect_director_stage_materialization_diagnostics(executor, run=run))
    assert not observation.blockers, observation.to_dict()
    assert not observation, observation.to_dict()
    ready = observation.verifier_results[-1]
    assert ready["exit_code"] != 0 and ready["passed"] is True
    assert ready["receipt_ref"]


def test_failed_build_retains_negative_receipt_and_defers_test_as_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, _ = _fixture(tmp_path, monkeypatch)
    (executor.workspace / "src/module.py").write_text("this is invalid Python !\n", encoding="utf-8")
    observation = asyncio.run(materialization._collect_director_stage_materialization_diagnostics(executor, run=run))
    build, deferred = observation.verifier_results
    assert build["receipt_ref"] and build["exit_code"] != 0
    assert "SyntaxError" in "\n".join(observation)
    assert deferred["receipt_ref"] is None and deferred["phase"] == "dependency"
    assert observation.to_dict()["missing_required_verifiers"] == [deferred["command"]]
    assert observation.to_dict()["failed_required_verifiers"] == [build["command"]]


def test_raw_success_and_receipt_ref_cannot_replace_owner_sealed_coverage() -> None:
    observation = materialization.MaterializationDiagnostics(
        static_evidence={"scan_mode": "pure_static", "scan_complete": False},
        verifier_results=({"command": ["python", "-m", "pytest"], "passed": True, "receipt_ref": "invented"},),
    )
    assert (
        "materialization_collector_coverage_incomplete"
        in materialization._materialization_observation_with_coverage(observation).blockers
    )


def test_short_positive_deadline_allows_real_fast_verifiers_without_resealing_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, _ = _fixture(tmp_path, monkeypatch)
    observation = asyncio.run(
        materialization._collect_director_stage_materialization_diagnostics(
            executor, run=run, context={"factory_run_deadline_epoch_seconds": time.time() + 5}
        )
    )
    assert not observation.blockers, observation.to_dict()
    assert all(receipt.timeout_seconds == 30 and receipt.succeeded for receipt in observation._owner_receipts)


def test_expiring_original_deadline_cancels_real_test_and_drains_before_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, _ = _fixture(tmp_path, monkeypatch)
    (executor.workspace / "tests/test_output.py").write_text(
        "from pathlib import Path\nimport time\ndef test_slow():\n"
        "    Path('started').write_text('yes', encoding='utf-8')\n"
        "    time.sleep(3)\n    Path('late_marker').write_text('bad', encoding='utf-8')\n",
        encoding="utf-8",
    )
    observation = asyncio.run(
        materialization._collect_director_stage_materialization_diagnostics(
            executor, run=run, context={"factory_run_deadline_epoch_seconds": time.time() + 2}
        )
    )
    assert (executor.workspace / "started").exists()
    assert "TimeoutError" in str(observation.blockers)
    time.sleep(2)
    assert not (executor.workspace / "late_marker").exists()
    assert not any(receipt.modality == "test" and receipt.succeeded for receipt in observation._owner_receipts)


@pytest.mark.parametrize("after_build", [False, True])
def test_before_launch_failure_keeps_known_verifiers_missing_and_prior_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_build: bool
) -> None:
    executor, run, _ = _fixture(tmp_path, monkeypatch, entrypoint=True)
    original_run = broker_owner._ExecutionBrokerProjectVerificationRunner.run
    attempted: list[str] = []
    launched: list[str] = []

    def fail_before_launch(self: Any, **kwargs: Any) -> broker.ProjectVerificationProcessResultV1:
        modality = kwargs["metadata"]["modality"]
        attempted.append(modality)
        if not after_build or modality not in {"environment_prep", "build"}:
            raise PermissionError("execution denied before on_launched")
        on_launched = kwargs["on_launched"]

        def record_launch(*args: Any) -> None:
            launched.append(modality)
            on_launched(*args)

        kwargs["on_launched"] = record_launch
        return original_run(self, **kwargs)

    monkeypatch.setattr(broker_owner._ExecutionBrokerProjectVerificationRunner, "run", fail_before_launch)
    observation = asyncio.run(materialization._collect_director_stage_materialization_diagnostics(executor, run=run))
    assert "PermissionError:execution denied before on_launched" in str(observation.blockers)
    assert attempted == (["environment_prep", "build", "test"] if after_build else ["environment_prep"])
    assert launched == (["environment_prep", "build"] if after_build else [])
    assert len(observation.verifier_results) == 4
    missing = observation.verifier_results[2:] if after_build else observation.verifier_results
    assert all(row["phase"] == "admission" and row["failure_kind"] == "admission" for row in missing)
    assert all(row["exit_code"] is None and row["receipt_ref"] is None and row["passed"] is False for row in missing)
    assert all("PermissionError" in row["error"] for row in missing)
    assert observation.to_dict()["missing_required_verifiers"] == [row["command"] for row in missing]
    assert observation.to_dict()["failed_required_verifiers"] == []
    assert len(observation._owner_receipts) == (2 if after_build else 0)
    if after_build:
        for actual, result in zip(observation._owner_receipts, observation.verifier_results[:2], strict=True):
            assert actual.succeeded
            assert result["receipt_ref"] == actual.receipt_ref
            assert result["receipt_hash"] == actual.receipt_hash
        assert observation._owner_receipts[-1].modality == "build"


def test_lookalike_contract_cannot_invent_planned_verifier_obligations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from polaris.cells.factory.pipeline.internal import factory_node_dependencies

    executor, run, _ = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        factory_node_dependencies,
        "authenticated_preparation_contract",
        lambda *_args: SimpleNamespace(project_id="unit-project", verification_command_authority=()),
    )
    spawn = Mock(side_effect=AssertionError("lookalike contract spawned a verifier"))
    monkeypatch.setattr(broker_owner._ExecutionBrokerProjectVerificationRunner, "run", spawn)
    observation = asyncio.run(materialization._collect_director_stage_materialization_diagnostics(executor, run=run))
    assert "materialization_completion_contract_invalid" in str(observation.blockers)
    assert spawn.call_count == 0
    assert observation.verifier_results == []
    assert observation._owner_receipts == ()
    assert observation.to_dict()["missing_required_verifiers"] == []
