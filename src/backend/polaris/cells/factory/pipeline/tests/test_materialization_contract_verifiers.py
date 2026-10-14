"""Static uncertainty cannot replace source-bound, owner-sealed verifier execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    failing: bool = False,
    entrypoint: bool = False,
    compiled_entrypoint: bool = False,
    build_script_name: str = "build.py",
    stdlib_verifier: bool = False,
    task_id: str = "TASK-1",
    run_id: str = "run-contract-verifiers",
    additional_authored_files: dict[str, str] | None = None,
    authored_contents: dict[str, str] | None = None,
    additional_applicability: str = "required",
    additional_semantic_role: str = "source",
    required_lint: bool = False,
    lint_cwds: tuple[str, ...] = (".",),
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
    if compiled_entrypoint:
        assert entrypoint
        files["src/entrypoint.py"] = "print('ready', flush=True)\n"
        files["build.json"] = '{"output":"dist/src/entrypoint.pyc"}'
        files[build_script_name] = (
            "import json, py_compile\nfrom pathlib import Path\n"
            "config = json.loads(Path('build.json').read_text(encoding='utf-8'))\n"
            "output = Path(config['output'])\noutput.parent.mkdir(parents=True, exist_ok=True)\n"
            "py_compile.compile('src/entrypoint.py', cfile=str(output), doraise=True)\n"
            "py_compile.compile('src/module.py', cfile='dist/module.pyc', doraise=True)\n"
            "print('build complete')\n"
        )
        files["tests/test_output.py"] = files["tests/test_output.py"].replace("src/module.pyc", "dist/module.pyc")
    if stdlib_verifier:
        files["tests/test_output.py"] += (
            "\nimport unittest\n"
            "class GeneratedOutputTest(unittest.TestCase):\n"
            "    def test_output(self):\n"
            "        test_generated_output()\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n"
        )
    files.update(additional_authored_files or {})
    assert set(authored_contents or {}) <= set(files)
    files.update(authored_contents or {})
    for name, text in files.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    task = ChiefEngineerPortfolioTaskV1(
        task_id=task_id,
        objective="Build and test module",
        target_files=tuple(
            path
            for path in files
            if additional_applicability == "required" or path not in (additional_authored_files or {})
        ),
        entrypoint_targets=("src/entrypoint.py",) if entrypoint else (),
    )
    authorities: tuple[VerificationCommandAuthorityV1, ...] = (
        VerificationCommandAuthorityV1(
            task_id=task.task_id,
            modality="build",
            argv=("python", build_script_name) if compiled_entrypoint else ("python", "-m", "compileall", "-b", "src"),
        ),
        VerificationCommandAuthorityV1(
            task_id=task.task_id,
            modality="test",
            argv=("python", "-m", "unittest", "discover", "-s", "tests")
            if stdlib_verifier
            else ("python", "-m", "pytest", "-q", "tests/test_output.py"),
        ),
    )
    if entrypoint:
        authorities += (
            VerificationCommandAuthorityV1(
                task_id=task.task_id,
                modality="entrypoint",
                argv=("python", "dist/entrypoint.pyc") if compiled_entrypoint else ("python", "src/entrypoint.py"),
            ),
            VerificationCommandAuthorityV1(
                task_id=task.task_id,
                modality="environment_prep",
                argv=("python", build_script_name)
                if compiled_entrypoint
                else ("python", "-m", "compileall", "-b", "src"),
            ),
        )
    completion = _library_completion_requirements(
        "src/module.py",
        owner_task_ids=(task.task_id,),
        test_path="tests/test_output.py",
        test_owner_task_id=task.task_id,
    )
    completion["obligations"]["artifacts"].append(
        {
            "obligation_id": "artifact-manifest",
            "path": "package.json",
            "semantic_role": "manifest",
            "applicability": "required",
            "owner_task_id": task.task_id,
        }
    )
    for index, path in enumerate(additional_authored_files or {}):
        completion["obligations"]["artifacts"].append(
            {
                "obligation_id": f"artifact-additional-{index}",
                "path": path,
                "semantic_role": additional_semantic_role,
                "applicability": additional_applicability,
                "owner_task_id": task.task_id,
            }
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
                "runtime_path": "dist/entrypoint.pyc" if compiled_entrypoint else None,
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
    if compiled_entrypoint:
        completion["obligations"]["artifacts"].extend(
            [
                {
                    "obligation_id": "artifact-build-config",
                    "path": "build.json",
                    "semantic_role": "config",
                    "applicability": "required",
                    "owner_task_id": task.task_id,
                },
                {
                    "obligation_id": "artifact-build-source",
                    "path": build_script_name,
                    "semantic_role": "source",
                    "applicability": "required",
                    "owner_task_id": task.task_id,
                },
            ]
        )
    if required_lint:
        for ordinal, lint_cwd in enumerate(lint_cwds):
            lint_authority = VerificationCommandAuthorityV1(
                task_id=task.task_id,
                modality="lint",
                argv=("python", "-m", "compileall", "-q", "src"),
                cwd=lint_cwd,
            )
            authorities += (lint_authority,)
            completion["obligations"]["verification"].append(
                {
                    "obligation_id": f"verify-lint-{ordinal}",
                    "modality": "lint",
                    "command_authority_hash": lint_authority.authority_hash,
                    "applicability": "required",
                    "covers_obligation_ids": ["artifact-1"],
                    "owner_task_id": task.task_id,
                }
            )
    portfolio = build_chief_engineer_blueprint_portfolio(
        BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(workspace),
            run_id=run_id,
            tasks=(task,),
            **_portfolio_command_authority(
                tasks=(task,),
                project_kind="application" if entrypoint else "library",
                catalog_snapshot_override={"project_kind": "application"} if entrypoint else None,
                workspace=workspace,
                run_id=run_id,
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
        completion_contract = contract
        blueprint_portfolio = portfolio

        def resolve_project_artifact_authority(
            self, query: broker.ResolveProjectArtifactAuthorityQueryV1
        ) -> broker.ProjectArtifactExecutionAuthorityV1:
            artifact = next(
                item for item in contract.obligations.artifacts if item.obligation_id == query.obligation_id
            )
            return replace(
                super().resolve_project_artifact_authority(query),
                owner_task_id=artifact.owner_task_id,
                path=artifact.path,
            )

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
                    "test": "python.unittest_discover" if stdlib_verifier else "python.pytest",
                    "entrypoint": "python.script_entrypoint",
                    "environment_prep": "python.compileall",
                    "lint": "python.compileall",
                }[authority.modality],
                input_artifacts=tuple(
                    broker.ProjectVerificationArtifactInputV1(obligation_id=item.obligation_id, path=item.path)
                    for item in contract.obligations.artifacts
                    if item.applicability == "required" or (workspace / item.path).is_file()
                )
                + (
                    (
                        broker.ProjectVerificationArtifactInputV1(
                            obligation_id="entrypoint-cli", path="dist/entrypoint.pyc"
                        ),
                    )
                    if compiled_entrypoint and authority.modality == "entrypoint"
                    else ()
                ),
            )

    port = Authority()
    monkeypatch.setattr(broker_owner, "_EXECUTION_AUTHORITY_PORT", port)
    return executor, run, port


def test_missing_compiled_entrypoint_is_repair_feedback_without_fabricated_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, _ = _fixture(tmp_path, monkeypatch, entrypoint=True, compiled_entrypoint=True)
    observation = asyncio.run(materialization._collect_director_stage_materialization_diagnostics(executor, run=run))
    assert not observation.blockers, observation.to_dict()
    assert all(receipt.succeeded for receipt in observation._owner_receipts)
    assert [receipt.modality for receipt in observation._owner_receipts] == ["environment_prep", "build", "test"]
    assert (executor.workspace / "dist/src/entrypoint.pyc").is_file()
    assert not (executor.workspace / "dist/entrypoint.pyc").exists()
    entry = observation.verifier_results[-1]
    assert entry["exit_code"] is None and entry["receipt_ref"] is None and entry["passed"] is False
    diagnostic = entry["repair_diagnostic"]
    assert diagnostic["code"] == "failed_required_entrypoint"
    assert diagnostic["metadata"]["runtime_path"] == "dist/entrypoint.pyc"
    assert diagnostic["metadata"]["execution_started"] is False
    assert diagnostic["metadata"]["command_receipt_missing"] is True
    assert set(diagnostic["metadata"]["repair_possible_scope"]) == {"build.json", "package.json", "src/entrypoint.py"}
    assert observation.to_dict()["missing_required_verifiers"] == [["python", "dist/entrypoint.pyc"]]
    assert observation.to_dict()["failed_required_verifiers"] == []
    assert "dist/entrypoint.pyc" in "\n".join(observation)
    assert "build.json" in executor._workspace_quality_repair_diagnostic_target_files(list(observation))
    assert "dist/entrypoint.pyc" not in executor._workspace_quality_repair_diagnostic_target_files(list(observation))


@pytest.mark.parametrize("failure", ["source_missing", "symlink", "authority_denied", "source_drift"])
def test_missing_entrypoint_does_not_hide_source_or_authority_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    executor, run, port = _fixture(tmp_path, monkeypatch, entrypoint=True, compiled_entrypoint=True)
    resolve = port.resolve_project_verification_authority

    def changed(
        query: broker.ResolveProjectVerificationAuthorityQueryV1,
    ) -> broker.ProjectVerificationExecutionAuthorityV1:
        authority = resolve(query)
        if authority.modality == "entrypoint":
            if failure == "authority_denied":
                raise ValueError("project_verification_command_policy_rejected")
            if failure == "source_missing":
                (executor.workspace / "src/entrypoint.py").unlink(missing_ok=True)
            elif failure == "source_drift":
                (executor.workspace / "src/entrypoint.py").write_text("print('unverified drift')\n", encoding="utf-8")
            elif failure == "symlink" and not (executor.workspace / "dist/entrypoint.pyc").is_symlink():
                (executor.workspace / "dist/entrypoint.pyc").symlink_to("absent.pyc")
        return authority

    monkeypatch.setattr(port, "resolve_project_verification_authority", changed)
    observation = asyncio.run(materialization._collect_director_stage_materialization_diagnostics(executor, run=run))
    assert observation.blockers
    assert all("repair_diagnostic" not in row for row in observation.verifier_results)
    assert all(receipt.modality != "entrypoint" for receipt in observation._owner_receipts)
    assert observation.verifier_results[-1]["exit_code"] is None


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


def test_source_map_metadata_keeps_real_failed_owner_verifier_feedback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, _ = _fixture(
        tmp_path,
        monkeypatch,
        failing=True,
        additional_authored_files={
            "bundle.js.map": json.dumps({"version": 3, "sources": ["webpack://virtual.ts"], "mappings": "AAAA"})
        },
    )
    source_map = executor.workspace / "bundle.js.map"
    source_map.write_text(
        json.dumps({"version": 3, "sources": ["webpack://virtual.ts"], "mappings": "AAAA"}), encoding="utf-8"
    )
    before = hashlib.sha256(source_map.read_bytes()).hexdigest()
    observation = asyncio.run(materialization._collect_director_stage_materialization_diagnostics(executor, run=run))
    assert not observation.blockers, observation.to_dict()
    assert any(
        issue["code"] == "non_executable_source_map" and issue["metadata"]["execution_qualified"] is False
        for issue in observation.static_evidence["issues"]
    )
    failed = observation.verifier_results[-1]
    assert failed["receipt_ref"] and failed["passed"] is False and failed["exit_code"] == 1
    assert observation.to_dict()["failed_required_verifiers"] == [failed["command"]]
    assert "AssertionError" in "\n".join(observation)
    assert hashlib.sha256(source_map.read_bytes()).hexdigest() == before


def test_materialization_authored_scope_keeps_generated_js_residue_out_of_source_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, port = _fixture(tmp_path, monkeypatch)
    residue = executor.workspace / "tests/test_output.js"
    residue.write_text("import { value } from '../src/module.js';\n", encoding="utf-8")
    before = hashlib.sha256(residue.read_bytes()).hexdigest()
    observation = asyncio.run(materialization._collect_director_stage_materialization_diagnostics(executor, run=run))
    assert not observation.blockers, observation.to_dict()
    assert not observation, observation.to_dict()
    assert all(row["passed"] and row["receipt_ref"] for row in observation.verifier_results)
    scope = observation.static_evidence["materialization_source_scope"]
    assert scope["completion_contract_hash"] == port.completion_contract.contract_hash
    assert set(scope["relative_paths"]) == {
        item.path for item in port.completion_contract.obligations.artifacts if item.applicability != "not_applicable"
    }
    assert "tests/test_output.js" not in observation.static_evidence["scanned_relative_paths"]
    assert hashlib.sha256(residue.read_bytes()).hexdigest() == before


def test_materialization_explicit_authored_js_missing_runtime_stays_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, _ = _fixture(
        tmp_path, monkeypatch, additional_authored_files={"src/entry.js": "import { value } from './module.js';\n"}
    )
    observation = asyncio.run(materialization._collect_director_stage_materialization_diagnostics(executor, run=run))
    assert any("src/entry.js" in error and "ERR_MODULE_NOT_FOUND" in error for error in observation)
    assert "src/entry.js" in observation.static_evidence["scanned_relative_paths"]


def test_materialization_missing_declared_source_keeps_coverage_blocker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, _ = _fixture(tmp_path, monkeypatch)
    (executor.workspace / "src/module.py").unlink()
    observation = asyncio.run(materialization._collect_director_stage_materialization_diagnostics(executor, run=run))
    assert "scope_missing:src/module.py" in observation.static_evidence["incomplete_reasons"]
    assert any("materialization_static_coverage_incomplete" in error for error in observation.blockers)
    assert not observation.verifier_results


def test_workspace_quality_committed_scope_retains_missing_source_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, _ = _fixture(tmp_path, monkeypatch)
    residue = executor.workspace / "tests/test_output.js"
    residue.write_text("import { value } from '../src/module.js';\n", encoding="utf-8")
    assert not executor._workspace_quality_repair_errors([], run=run)
    (executor.workspace / "src/module.py").unlink()
    errors = executor._workspace_quality_repair_errors([], run=run)
    assert any("source coverage incomplete" in error and "scope_missing:src/module.py" in error for error in errors)


def test_workspace_quality_invalid_committed_contract_never_falls_back_to_partial_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, _ = _fixture(tmp_path, monkeypatch)
    run.metadata["stage_results"] = {}
    errors = executor._workspace_quality_repair_errors([], run=run)
    assert any("dependency_preparation_requires_committed_ce_stage" in error for error in errors)
    observation = asyncio.run(materialization._collect_director_stage_materialization_diagnostics(executor, run=run))
    assert observation.blockers and not observation.verifier_results


@pytest.mark.parametrize("present", [False, True])
def test_optional_source_presence_does_not_change_required_obligations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, present: bool
) -> None:
    from polaris.kernelone.quality import scan_workspace_artifact_quality_evidence
    from polaris.kernelone.quality.interface_ledger import WorkspaceReadSnapshot

    executor, _, port = _fixture(
        tmp_path, monkeypatch, additional_authored_files={"src/optional.js": "import { value } from './missing.js';\n"}
    )
    # The compiler intentionally drops creative optional extras outside explicit
    # PM targets. This case tests the typed selection boundary, not persistence
    # of a new contract or a qualified owner verifier under a changed hash.
    original = port.completion_contract
    artifacts = tuple(
        replace(item, applicability="optional") if item.path == "src/optional.js" else item
        for item in original.obligations.artifacts
    )
    contract = replace(original, obligations=replace(original.obligations, artifacts=artifacts))
    assert contract.contract_hash != original.contract_hash
    path = executor.workspace / "src/optional.js"
    if not present:
        path.unlink()
    reader = WorkspaceReadSnapshot(executor.workspace)
    scope = materialization._materialization_source_scope(contract, reader=reader)
    assert ("src/optional.js" in scope) is present
    evidence = scan_workspace_artifact_quality_evidence(
        str(executor.workspace), relative_paths=scope, scan_mode="pure_static"
    )
    assert not reader.drift_reasons()
    if present:
        assert any("optional.js" in error and "ERR_MODULE_NOT_FOUND" in error for error in evidence.errors)
    else:
        assert "scope_missing:src/optional.js" not in evidence.incomplete_reasons
        assert not evidence.errors


def test_required_asset_keeps_owner_verification_without_source_parser_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, _ = _fixture(
        tmp_path,
        monkeypatch,
        additional_authored_files={"image.png": "opaque asset"},
        additional_semantic_role="assets",
    )
    asset = executor.workspace / "image.png"
    asset.write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff")
    observation = asyncio.run(materialization._collect_director_stage_materialization_diagnostics(executor, run=run))
    assert not observation.blockers, observation.to_dict()
    assert not observation, observation.to_dict()
    assert "image.png" in observation.static_evidence["materialization_source_scope"]["asset_paths"]
    assert all(row["passed"] and row["receipt_ref"] for row in observation.verifier_results)
    asset.unlink()
    missing = asyncio.run(materialization._collect_director_stage_materialization_diagnostics(executor, run=run))
    assert any("asset" in error and "image.png" in error for error in missing.blockers)


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
