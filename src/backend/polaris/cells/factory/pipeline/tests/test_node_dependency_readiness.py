"""Declared dependency drift must not hide behind an existing node_modules."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import tarfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal import factory_materialization_impl as materialization
from polaris.cells.factory.pipeline.internal.factory_workspace_quality import WorkspaceQualityRunner
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import authored as authored


def _json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _cached(root: Path) -> None:
    _json(root / "package.json", {"name": "fixture", "dependencies": {"alpha": "1.0.0"}})
    _json(
        root / "package-lock.json",
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "fixture", "dependencies": {"alpha": "1.0.0"}},
                "node_modules/alpha": {"version": "1.0.0"},
            },
        },
    )
    _json(root / "node_modules/alpha/package.json", {"name": "alpha", "version": "1.0.0"})


def test_late_manifest_dependency_requires_preparation_with_existing_binary(tmp_path: Path) -> None:
    _cached(tmp_path)
    binary = tmp_path / "node_modules/.bin/compiler"
    binary.parent.mkdir()
    binary.write_text("existing compiler", encoding="utf-8")
    _json(tmp_path / "package.json", {"dependencies": {"alpha": "1.0.0", "beta": "2.0.0"}})
    assert WorkspaceQualityRunner(tmp_path).workspace_quality_prepare_commands([["npm", "test"]], {}) == [
        ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"]
    ]


def test_matching_cached_dependencies_do_not_reinstall(tmp_path: Path) -> None:
    _cached(tmp_path)
    assert WorkspaceQualityRunner(tmp_path).workspace_quality_prepare_commands([["npm", "test"]], {}) == []


@pytest.mark.parametrize("drift", ["missing_package", "wrong_version", "stale_lock"])
def test_incomplete_or_stale_dependency_state_requires_preparation(tmp_path: Path, drift: str) -> None:
    _cached(tmp_path)
    if drift == "missing_package":
        (tmp_path / "node_modules/alpha/package.json").unlink()
    elif drift == "wrong_version":
        _json(tmp_path / "node_modules/alpha/package.json", {"name": "alpha", "version": "0.9.0"})
    else:
        _json(tmp_path / "package-lock.json", {"lockfileVersion": 3, "packages": {"": {}}})
    assert WorkspaceQualityRunner(tmp_path).workspace_quality_prepare_commands([["npm", "test"]], {})


def test_explicit_install_denial_still_applies_to_stale_dependencies(tmp_path: Path) -> None:
    _cached(tmp_path)
    (tmp_path / "node_modules/alpha/package.json").unlink()
    assert (
        WorkspaceQualityRunner(tmp_path).workspace_quality_prepare_commands(
            [["npm", "test"]], {"workspace_validation_install_dependencies": False}
        )
        == []
    )


def test_zero_exit_with_unready_dependencies_is_not_preparation_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cached(tmp_path)
    (tmp_path / "node_modules/alpha/package.json").unlink()
    runner = WorkspaceQualityRunner(tmp_path)
    monkeypatch.setattr(
        runner,
        "_run_resolved_command",
        lambda **kwargs: {
            "command": kwargs["command"],
            "exit_code": 0,
            "passed": True,
            "stdout_tail": "physical output",
            "stderr_tail": "",
        },
    )
    result = runner.run_command(["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"], 5)
    assert result["exit_code"] == 0
    assert result["stdout_tail"] == "physical output"
    assert result["passed"] is False
    assert "dependency" in result["error"]


def test_preparation_rejects_authored_source_drift_without_restoring_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cached(tmp_path)
    source = tmp_path / "source.ts"
    source.write_text("original", encoding="utf-8")
    runner = WorkspaceQualityRunner(tmp_path)

    def mutate(**kwargs: Any) -> dict[str, Any]:
        source.write_text("changed", encoding="utf-8")
        return {
            "command": kwargs["command"],
            "exit_code": 0,
            "passed": True,
            "stdout_tail": "physical output",
            "stderr_tail": "",
        }

    monkeypatch.setattr(runner, "_run_resolved_command", mutate)
    result = runner.run_command(["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"], 5)
    assert result["passed"] is False
    assert result["exit_code"] == 0
    assert "source" in result["error"]
    assert source.read_text(encoding="utf-8") == "changed"


def test_collector_surfaces_preparation_failure_even_with_existing_compiler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    _cached(tmp_path)
    (tmp_path / "node_modules/alpha/package.json").unlink()
    (tmp_path / "node_modules/.bin").mkdir()
    (tmp_path / "node_modules/.bin/tsc").write_text("existing", encoding="utf-8")
    _json(tmp_path / "tsconfig.json", {})
    executor = SimpleNamespace(
        workspace=tmp_path,
        _ensure_director_stage_materialization_typescript_toolchain=lambda: {
            "command": ["npm", "install"],
            "passed": False,
            "exit_code": 17,
            "stderr_tail": "installation failed",
        },
    )
    monkeypatch.setattr("polaris.kernelone.quality.scan_workspace_artifact_quality", lambda _: [])
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""))
    diagnostics = materialization._collect_director_stage_materialization_diagnostics(executor)
    assert any("dependency_preparation" in row and "17" in row and "installation failed" in row for row in diagnostics)


def _package_tarball(root: Path, name: str) -> Path:
    archive = root / (name + ".tgz")
    body = json.dumps({"name": name, "version": "1.0.0", "scripts": {"postinstall": "exit 17"}}).encode("utf-8")
    with tarfile.open(archive, "w:gz") as tar:
        entry = tarfile.TarInfo("package/package.json")
        entry.size = len(body)
        tar.addfile(entry, io.BytesIO(body))
    return archive


def test_real_offline_npm_refreshes_late_generic_dependency_and_preserves_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authored: tuple[Any, Any, Any],
) -> None:
    assert shutil.which("npm"), "native npm is required for this integration case"
    _portfolio, contract, fs = authored
    cache = tmp_path.parent / (tmp_path.name + "-npm-cache")
    monkeypatch.setenv("npm_config_offline", "true")
    monkeypatch.setenv("npm_config_cache", str(cache))
    tarballs = tmp_path.parent / (tmp_path.name + "-packages")
    tarballs.mkdir()
    alpha = _package_tarball(tarballs, "alpha")
    beta = _package_tarball(tarballs, "beta")
    _json(
        tmp_path / "package.json", {"name": "fixture", "version": "1.0.0", "dependencies": {"alpha": f"file:{alpha}"}}
    )
    sources = {a.path: fs.workspace_read_bytes(a.path) for a in contract.obligations.artifacts}
    runner = WorkspaceQualityRunner(tmp_path)
    command = ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"]
    first = runner.run_command(command, 30, preparation_contract=contract)
    assert first["exit_code"] == 0 and first["passed"] is True, first
    assert runner.workspace_quality_prepare_commands([["npm", "test"]], {}) == []
    _json(
        tmp_path / "package.json",
        {"name": "fixture", "version": "1.0.0", "dependencies": {"alpha": f"file:{alpha}", "beta": f"file:{beta}"}},
    )
    manifest = (tmp_path / "package.json").read_bytes()
    assert runner.workspace_quality_prepare_commands([["npm", "test"]], {}) == [command]
    second = runner.run_command(command, 30, preparation_contract=contract)
    assert second["exit_code"] == 0 and second["passed"] is True, second
    assert (tmp_path / "node_modules/beta/package.json").is_file()
    assert (tmp_path / "package.json").read_bytes() == manifest
    assert {path: fs.workspace_read_bytes(path) for path in sources} == sources
    assert runner.workspace_quality_prepare_commands([["npm", "test"]], {}) == []


def test_real_offline_npm_failure_retains_physical_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authored: tuple[Any, Any, Any],
) -> None:
    _portfolio, contract, _fs = authored
    monkeypatch.setenv("npm_config_offline", "true")
    monkeypatch.setenv("npm_config_cache", str(tmp_path.parent / (tmp_path.name + "-cache")))
    _json(tmp_path / "package.json", {"dependencies": {"unavailable": "file:/missing-fixture-package.tgz"}})
    result = WorkspaceQualityRunner(tmp_path).run_command(
        ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"],
        30,
        preparation_contract=contract,
    )
    assert isinstance(result["exit_code"], int) and result["exit_code"] != 0
    assert result["passed"] is False
    assert "ENOENT" in result["stderr_tail"]


def test_cancel_or_unproved_drain_never_becomes_preparation_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.factory.pipeline.internal import factory_workspace_quality as module
    from polaris.kernelone.process import ProcessTreeDrainError

    _cached(tmp_path)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise ProcessTreeDrainError("fixture drain unavailable")

    monkeypatch.setattr(module, "run_process_tree_safe", fail)
    with pytest.raises(ProcessTreeDrainError, match="fixture drain unavailable"):
        WorkspaceQualityRunner(tmp_path).run_command(
            ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"], 1
        )


def test_materialization_uses_explicit_authenticated_run_and_retains_preparation_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authored: tuple[Any, Any, Any],
) -> None:
    from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
    from polaris.cells.factory.pipeline.internal.factory_stage_executor._executor import OrchestrationStageExecutor

    _portfolio, contract, _fs = authored
    tarballs = tmp_path.parent / (tmp_path.name + "-tarballs")
    tarballs.mkdir()
    alpha = _package_tarball(tarballs, "alpha")
    _json(tmp_path / "package.json", {"name": "fixture", "dependencies": {"alpha": f"file:{alpha}"}})
    _json(tmp_path / "tsconfig.json", {})
    monkeypatch.setenv("npm_config_offline", "true")
    monkeypatch.setenv("npm_config_cache", str(tmp_path.parent / (tmp_path.name + "-cache")))
    executor = OrchestrationStageExecutor(tmp_path)
    monkeypatch.setattr(
        executor, "_read_json_artifact_payload", lambda ref: {"project_completion_contract": contract.to_dict()}
    )
    run = FactoryRun(
        id=contract.run_id,
        config=FactoryConfig(name="dependency-fixture"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-04T00:00:00+00:00",
        metadata={
            "stage_results": {
                "chief_engineer_review": {
                    "status": "success",
                    "artifacts": ["runtime/blueprints/ce_portfolio_fixture.json"],
                }
            }
        },
    )
    monkeypatch.setattr("polaris.kernelone.quality.scan_workspace_artifact_quality", lambda _: [])
    results: list[dict[str, Any]] = []
    diagnostics = materialization._collect_director_stage_materialization_diagnostics(
        executor, run=run, preparation_results=results
    )
    assert diagnostics == []
    assert len(results) == 1 and results[0]["passed"] is True
    assert results[0]["exit_code"] == 0
    assert (tmp_path / "node_modules/alpha/package.json").is_file()


def test_unsafe_dependency_symlink_is_rejected_before_installer_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cached(tmp_path)
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    _json(outside / "package.json", {"name": "alpha", "version": "1.0.0"})
    shutil.rmtree(tmp_path / "node_modules/alpha")
    (tmp_path / "node_modules/alpha").symlink_to(outside, target_is_directory=True)
    runner = WorkspaceQualityRunner(tmp_path)
    marker = outside / "installer-ran"

    def unsafe(**kwargs: Any) -> dict[str, Any]:
        marker.write_text("wrong", encoding="utf-8")
        return {"command": kwargs["command"], "passed": True, "exit_code": 0, "stdout_tail": "", "stderr_tail": ""}

    monkeypatch.setattr(runner, "_run_resolved_command", unsafe)
    result = runner.run_command(["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"], 1)
    assert result["passed"] is False
    assert not marker.exists()


def test_absent_optional_peer_and_platform_packages_are_not_required(tmp_path: Path) -> None:
    _cached(tmp_path)
    manifest = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
    manifest.update(
        optionalDependencies={"platform-only": "1.0.0"},
        peerDependencies={"optional-peer": "1.0.0"},
        peerDependenciesMeta={"optional-peer": {"optional": True}},
    )
    _json(tmp_path / "package.json", manifest)
    lock = json.loads((tmp_path / "package-lock.json").read_text(encoding="utf-8"))
    lock["packages"][""] = manifest
    lock["packages"]["node_modules/platform-only"] = {
        "version": "1.0.0",
        "optional": True,
        "os": ["fixture-other-platform"],
    }
    _json(tmp_path / "package-lock.json", lock)
    assert WorkspaceQualityRunner(tmp_path).workspace_quality_prepare_commands([["npm", "test"]], {}) == []


@pytest.mark.parametrize("known_owner", [False, True])
def test_stale_unknown_or_ce_authored_lock_requires_owner_repair_before_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    known_owner: bool,
) -> None:
    from polaris.cells.chief_engineer.blueprint.public import (
        BuildChiefEngineerBlueprintPortfolioCommandV1,
        ChiefEngineerPortfolioTaskV1,
        build_chief_engineer_blueprint_portfolio,
    )
    from polaris.cells.chief_engineer.blueprint.public.tests.test_public_contractsa import (
        _library_completion_requirements,
        _portfolio_command_authority,
    )

    contract = None
    if known_owner:
        tasks = (
            ChiefEngineerPortfolioTaskV1(
                task_id="owner", objective="Own lock", target_files=("package-lock.json", "tests/test_a.py")
            ),
        )
        portfolio = build_chief_engineer_blueprint_portfolio(
            BuildChiefEngineerBlueprintPortfolioCommandV1(
                workspace=str(tmp_path),
                run_id="run",
                tasks=tasks,
                **_portfolio_command_authority(tasks=tasks, project_kind="library", workspace=tmp_path, run_id="run"),
                llm_blueprint={
                    "construction_plan": {"project_interface_contract": {}},
                    "project_completion_contract": _library_completion_requirements(
                        "package-lock.json",
                        owner_task_ids=("owner",),
                        test_path="tests/test_a.py",
                        test_owner_task_id="owner",
                    ),
                    "risk_flags": [],
                },
            )
        )
        contract = portfolio.project_completion_contract
        assert contract is not None
    _cached(tmp_path)
    _json(tmp_path / "package.json", {"dependencies": {"alpha": "1.0.0", "beta": "2.0.0"}})
    before = (tmp_path / "package-lock.json").read_bytes()
    runner = WorkspaceQualityRunner(tmp_path)

    def must_not_execute(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("installer ran without derived-lock authority")

    monkeypatch.setattr(runner, "_run_resolved_command", must_not_execute)
    result = runner.run_command(
        ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"], 1, preparation_contract=contract
    )
    assert result["passed"] is False and result["exit_code"] is None
    assert "requires_owner_repair" in result["error"]
    assert (tmp_path / "package-lock.json").read_bytes() == before


def test_stale_lock_cannot_hide_unlisted_dependency_escape_before_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authored: tuple[Any, Any, Any],
) -> None:
    _portfolio, contract, _fs = authored
    _cached(tmp_path)
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (tmp_path / "node_modules/beta").symlink_to(outside, target_is_directory=True)
    _json(tmp_path / "package.json", {"dependencies": {"alpha": "1.0.0", "beta": "2.0.0"}})
    marker = outside / "installer-ran"
    runner = WorkspaceQualityRunner(tmp_path)

    def unsafe(**kwargs: Any) -> dict[str, Any]:
        marker.write_text("wrong", encoding="utf-8")
        return {"command": kwargs["command"], "passed": True, "exit_code": 0, "stdout_tail": "", "stderr_tail": ""}

    monkeypatch.setattr(runner, "_run_resolved_command", unsafe)
    result = runner.run_command(
        ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"], 1, preparation_contract=contract
    )
    assert result["passed"] is False
    assert not marker.exists()


@pytest.mark.asyncio
async def test_postcommit_preparation_failure_is_not_discarded_when_metadata_becomes_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authored: tuple[Any, Any, Any],
) -> None:
    """A failed installer can leave ready metadata; its physical failure still owns the outcome."""
    from polaris.cells.factory.pipeline.internal import factory_workspace_quality_impl as quality
    from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
    from polaris.cells.factory.pipeline.internal.factory_stage_executor._executor import OrchestrationStageExecutor
    from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import _projection, _real_effect
    from polaris.cells.roles import adapters
    from polaris.cells.roles.kernel.tests.test_directed_effect_lifecycle import _setup_attempt
    from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService

    portfolio, contract, _fs = authored
    _cached(tmp_path)
    _json(tmp_path / "tsconfig.json", {})
    executor = OrchestrationStageExecutor(tmp_path)
    attempt = _setup_attempt(str(tmp_path))
    run = FactoryRun(
        id=contract.run_id,
        config=FactoryConfig(name="postcommit-dependency-fixture"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-04T00:00:00+00:00",
        metadata={
            "stage_results": {
                "chief_engineer_review": {
                    "status": "success",
                    "artifacts": [portfolio.portfolio_path],
                }
            }
        },
    )
    monkeypatch.setattr(executor, "_director_stage_should_run_materialization_quality_settle", lambda **kwargs: True)
    monkeypatch.setattr(executor, "_workspace_quality_repair_diagnostic_target_files", lambda errors: ["src/a.py"])
    monkeypatch.setattr(
        executor,
        "_claim_workspace_quality_repair_attempt",
        lambda **kwargs: (
            attempt.external_task_id,
            int(attempt.task_id),
            attempt,
            {"task_completion_projection": _projection(portfolio, contract)},
        ),
    )
    monkeypatch.setattr(executor, "_director_stage_materialization_settle_commit_context", lambda **kwargs: {})
    plans: Iterator[tuple[list[dict[str, Any]], dict[str, Any]]] = iter(
        [([{"result": {"status": "deferred_repair_effects_pending"}}], {}), ([], {})]
    )
    monkeypatch.setattr(executor, "_apply_workspace_quality_repairs", lambda **kwargs: next(plans))
    # _real_effect returns the physical raw row, not the bridge's BatchReceipt
    # envelope. Adapt only this envelope-reading seam, as the existing partial
    # artifact integration fixture does; the DEO commit and broker stay real.
    monkeypatch.setattr(
        executor,
        "_director_stage_materialization_receipt_succeeded",
        lambda row: row.get("effect_receipt", {}).get("receipt_outcome") == "succeeded",
    )
    monkeypatch.setattr(quality, "_workspace_quality_causal_repair_target_files", lambda *args, **kwargs: [])
    monkeypatch.setattr(materialization, "_director_stage_deferred_repair_owner_targets", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        "polaris.kernelone.quality.scan_workspace_artifact_quality",
        lambda root: (
            ["src/a.py: fixture requires original-owner repair"]
            if (tmp_path / "src/a.py").read_text(encoding="utf-8") == "before\n"
            else []
        ),
    )

    async def commit(**kwargs: Any) -> list[dict[str, Any]]:
        _same_attempt, effects = await _real_effect(tmp_path, attempt=attempt)
        # Private environment fault after a genuine DEO commit, not an authored-source write.
        (tmp_path / "node_modules/alpha/package.json").unlink()
        return effects

    monkeypatch.setattr(adapters.public, "commit_materialization_deferred_repairs", commit)
    calls: list[list[str]] = []

    def failed_physical_preparation(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["command"])
        _json(tmp_path / "node_modules/alpha/package.json", {"name": "alpha", "version": "1.0.0"})
        return {
            "command": kwargs["command"],
            "passed": False,
            "exit_code": 17,
            "stdout_tail": "installed metadata before failure",
            "stderr_tail": "physical preparation failure",
        }

    monkeypatch.setattr(executor._workspace_quality, "_run_resolved_command", failed_physical_preparation)
    result = await materialization._run_director_stage_materialization_quality_settle(
        executor,
        run=run,
        stage_status="failed",
        error_code="director_missing_write_receipt",
    )
    assert result["ok"] is False
    assert len(calls) == 1, result
    observations = result["dependency_preparation_results"]
    assert len(observations) == 1 and observations[0]["exit_code"] == 17
    assert observations[0]["passed"] is False
    assert observations[0]["dependency_preparation"]["protected_lock"] is False
    assert any("dependency_preparation_failed" in row for row in result["post_commit_diagnostics"])
    task = next(
        row
        for row in TaskRuntimeService(str(tmp_path)).list_task_rows(include_terminal=True)
        if str(row["id"]) == str(attempt.task_id)
    )
    assert task["status"] == "failed"
    assert (tmp_path / "src/a.py").read_text(encoding="utf-8") == "after\n"


def test_preparation_observation_binds_utf8_source_hash_map_without_source_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _cached(tmp_path)
    source = tmp_path / "é.ts"
    source.write_text("原始", encoding="utf-8")
    paths = ["package-lock.json", "package.json", "é.ts"]
    before = {path: hashlib.sha256((tmp_path / path).read_bytes()).hexdigest() for path in paths}
    runner = WorkspaceQualityRunner(tmp_path)

    def mutate(**kwargs: Any) -> dict[str, Any]:
        source.write_text("变更", encoding="utf-8")
        return {"command": kwargs["command"], "passed": True, "exit_code": 0, "stdout_tail": "", "stderr_tail": ""}

    monkeypatch.setattr(runner, "_run_resolved_command", mutate)
    result = runner.run_command(["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"], 1)
    observation = result["dependency_preparation"]
    assert observation.get("source_before_paths") == paths
    assert observation.get("source_after_paths") == paths
    assert observation.get("source_before_count") == observation.get("source_after_count") == 3
    after = {path: hashlib.sha256((tmp_path / path).read_bytes()).hexdigest() for path in paths}
    expected_before = hashlib.sha256(
        json.dumps(before, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    expected_after = hashlib.sha256(
        json.dumps(after, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert observation["source_before_hash"] == expected_before
    assert observation["source_after_hash"] == expected_after
    assert expected_before != expected_after
    assert result["passed"] is False and result["exit_code"] == 0
    assert "原始" not in json.dumps(observation, ensure_ascii=False)
    assert "变更" not in json.dumps(observation, ensure_ascii=False)
