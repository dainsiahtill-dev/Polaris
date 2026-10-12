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
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from polaris.cells.factory.pipeline.internal import factory_materialization_impl as materialization
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.tests.test_director_stage_materialization_settle import (
    canonical_settle_owner as canonical_settle_owner,
)
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import authored as authored
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
    monkeypatch.setattr(materialization.subprocess, "run", spawn)
    observation = asyncio.run(_collect(executor, run=_run()))
    assert spawn.call_count == 0
    assert observation.blockers
    assert "requires_committed_ce_stage" in str(observation.blockers)


@pytest.mark.asyncio
@pytest.mark.parametrize("compile_only,build_runs_tests", [(False, False), (True, False), (True, True)])
async def test_collector_builds_before_tests_that_consume_generated_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, compile_only: bool, build_runs_tests: bool
) -> None:
    """Real isolated commands in a self-owned fixture; Factory admission is doubled."""
    from polaris.cells.factory.pipeline.internal import factory_node_dependencies as dependencies
    from polaris.cells.factory.pipeline.internal.native_validation_group import verification_group

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
    monkeypatch.setattr(
        dependencies,
        "authenticated_preparation_contract",
        Mock(return_value=SimpleNamespace(project_id="unit-project")),
    )
    monkeypatch.setattr(materialization, "node_dependency_readiness", Mock(return_value=SimpleNamespace(ready=True)))
    node = shutil.which("node")
    assert node is not None
    tool_root = Path(node).resolve().parent.parent
    # System roots are already mounted; avoid hashing all of /usr for a fixture.
    tool_roots = () if tool_root in {Path("/usr"), Path("/usr/local")} else (tool_root,)
    with verification_group(
        workspace=workspace,
        candidate_id="build-test-fixture",
        input_hashes={name: hashlib.sha256(content.encode("utf-8")).hexdigest() for name, content in files.items()},
        toolchain_roots=tool_roots,
        staging_parent=tmp_path,
    ) as group:

        async def command(argv: list[str], timeout: float) -> dict[str, Any]:
            result = group.execute_command(group.prepare_command(argv), timeout=timeout)
            return {
                "command": argv,
                "exit_code": result.returncode,
                "passed": result.returncode == 0,
                "stdout_tail": result.stdout,
                "stderr_tail": result.stderr,
            }

        monkeypatch.setattr(
            materialization.NativeValidationSession,
            "from_factory",
            Mock(return_value=SimpleNamespace(run_command=command, close=lambda: None)),
        )
        result = await _collect(
            OrchestrationStageExecutor(workspace), run=_run(), context={"project_id": "unit-project"}
        )
    assert not result.blockers
    test = next(item for item in result.verifier_results if item["command"] == ["npm", "test"])
    if compile_only and not build_runs_tests:
        assert result
        assert "npm manifest-only test script" in str(result)
        assert test["exit_code"] == 0
        assert test["passed"] is False
    else:
        assert not result
        assert "# tests 1" in test["stdout_tail"]
        assert test["passed"] is True
    assert not (workspace / "dist").exists()


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
    """Unknown required verifier intents stay deferred and block repair."""
    import polaris.kernelone.quality as quality
    from polaris.kernelone.quality.artifact_quality import ArtifactQualityDeferredCheck

    monkeypatch.setattr(
        quality,
        "scan_workspace_artifact_quality_evidence",
        Mock(
            return_value=ArtifactQualityEvidence(
                scan_mode="pure_static",
                deferred_checks=(ArtifactQualityDeferredCheck("unknown-required", ("main.x",)),),
            )
        ),
    )
    observation = asyncio.run(_collect(OrchestrationStageExecutor(tmp_path), run=_run()))
    assert observation.blockers
    assert observation.static_evidence["deferred_checks"][0]["check_id"] == "unknown-required"


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
    tmp_path: Path, authored: tuple[Any, Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src/authored.js").write_text("export function value() { return 1; }\n", encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    run, context = _factory_owner(executor, authored)
    project_id = context.pop("project_id")
    captured: dict[str, Any] = {}

    def observe_admission(_executor: Any, _run: FactoryRun, bound_context: dict[str, Any], **_kwargs: Any) -> Any:
        captured.update(bound_context)
        raise RuntimeError("stop_after_project_binding")

    monkeypatch.setattr(materialization.NativeValidationSession, "from_factory", observe_admission)
    observation = asyncio.run(_collect(executor, run=run, context=context))

    assert captured["project_id"] == project_id
    assert "stop_after_project_binding" in str(observation.blockers)
    assert "context_project_mismatch" not in str(observation.blockers)
    assert "project_id" not in context


def test_dependency_change_reopens_validation_group_after_owned_preparation(
    tmp_path: Path, authored: tuple[Any, Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"node --test tests/verify.test.js"}}', encoding="utf-8")
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests/verify.test.js").write_text("require('node:test')('value', () => {});\n", encoding="utf-8")
    executor = OrchestrationStageExecutor(tmp_path)
    run, context = _factory_owner(executor, authored)
    old = SimpleNamespace(close=Mock())
    sessions: list[Any] = [old]
    monkeypatch.setattr(materialization, "node_dependency_readiness", Mock(return_value=SimpleNamespace(ready=False)))

    async def prepare(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        old.close.assert_called_once()
        assert sessions == []
        return {"passed": True, "exit_code": 0}

    preparation = AsyncMock(side_effect=prepare)
    monkeypatch.setattr(materialization, "_await_materialization_preparation", preparation)

    async def command(argv: list[str], _timeout: float) -> dict[str, Any]:
        return {"command": argv, "exit_code": 0, "passed": True}

    renewed = SimpleNamespace(run_command=command)
    admission = Mock(return_value=renewed)
    monkeypatch.setattr(materialization.NativeValidationSession, "from_factory", admission)

    observation = asyncio.run(_collect(executor, run=run, context=context, sessions=sessions))

    assert observation.blockers == []
    preparation.assert_awaited_once()
    admission.assert_called_once()
    assert sessions == [renewed]
    assert observation.preparation_results == [{"passed": True, "exit_code": 0}]


def test_real_untrusted_group_rejects_before_effect_or_repair(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    authored: tuple[Any, Any, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real CE persistence and original bwrap gate; no trusted callback/UID double."""
    from polaris.cells.factory.pipeline.internal import native_validation_group as group

    # Select an actual untrusted PATH executable instead of assuming the host
    # lacks a trusted system installation. Keep the original binary/UID gate.
    fake_tools = tmp_path_factory.mktemp("untrusted-tools")
    fake_bwrap = fake_tools / "bwrap"
    fake_bwrap.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    fake_bwrap.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_tools), prepend=os.pathsep)
    executor = OrchestrationStageExecutor(tmp_path)
    run, context = _factory_owner(executor, authored)
    spawn = Mock(side_effect=AssertionError("untrusted Group spawned"))
    monkeypatch.setattr(group, "_owned_process_runner", spawn)
    claim = Mock(side_effect=AssertionError("untrusted Group claimed repair"))
    schedule = Mock(side_effect=AssertionError("untrusted Group entered schedule"))
    monkeypatch.setattr(executor, "_claim_workspace_quality_repair_attempt", claim)
    monkeypatch.setattr(executor, "_apply_workspace_quality_repairs", schedule)
    before = {
        p.relative_to(tmp_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }
    result = asyncio.run(
        executor._run_director_stage_materialization_quality_settle(
            run=run, context=context, stage_status="failed", error_code="director.dispatch_timeout"
        )
    )
    assert result["ok"] is False
    assert result["reason"] == "materialization_verification_blocked"
    assert "verification_isolation_binary_untrusted" in result["detail"]
    assert result["validation_observations"][0]["missing_required_verifiers"] == [
        ["node", "--check", "src/authored.js"]
    ]
    assert result["validation_observations"][0]["failed_required_verifiers"] == []
    assert spawn.call_count == claim.call_count == schedule.call_count == 0
    assert before == {
        p.relative_to(tmp_path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }


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
    admission = Mock(side_effect=AssertionError("mismatched identity opened Group"))
    monkeypatch.setattr(materialization.NativeValidationSession, "from_factory", admission)
    observation = asyncio.run(_collect(executor, run=run, context=context))
    assert error in str(observation.blockers)
    assert admission.call_count == 0


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
        (PermissionError("denied"), "startup", True),
        (subprocess.TimeoutExpired(["npm", "test"], 0.01, output="partial"), "timeout", True),
        ({"exit_code": None, "passed": False, "error": "timeout after 0.1s"}, "timeout", True),
    ],
)
def test_controlled_failures_keep_command_exit_error_and_classification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: Any,
    kind: str,
    blocker: bool,
) -> None:
    """Interface/result projection doubles; none prove physical Group execution."""
    import polaris.kernelone.quality as quality
    from polaris.cells.factory.pipeline.internal import factory_node_dependencies as dependencies

    (tmp_path / "package.json").write_text('{"scripts":{"test":"node tests/verify.test.js"}}', encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/verify.test.js").write_text("console.log('unit');", encoding="utf-8")
    monkeypatch.setattr(
        quality,
        "scan_workspace_artifact_quality_evidence",
        Mock(return_value=ArtifactQualityEvidence(scan_mode="pure_static")),
    )
    monkeypatch.setattr(
        dependencies,
        "authenticated_preparation_contract",
        Mock(return_value=SimpleNamespace(project_id="unit-project")),
    )
    monkeypatch.setattr(materialization, "node_dependency_readiness", Mock(return_value=SimpleNamespace(ready=True)))

    async def command(argv: list[str], _timeout: float) -> dict[str, Any]:
        assert argv == ["npm", "test"]
        if isinstance(outcome, BaseException):
            raise outcome
        return {"command": argv, "stdout_tail": "", "stderr_tail": "", **outcome}

    close = Mock()
    monkeypatch.setattr(
        materialization.NativeValidationSession,
        "from_factory",
        Mock(return_value=SimpleNamespace(run_command=command, close=close)),
    )
    result = asyncio.run(
        _collect(OrchestrationStageExecutor(tmp_path), run=_run(), context={"project_id": "unit-project"})
    )
    assert bool(result.blockers) is blocker
    assert result.verifier_results[0]["command"] == ["npm", "test"]
    assert result.verifier_results[0]["failure_kind"] == kind
    assert "npm test failed" in str(result)
    assert result.verifier_results[0]["exit_code"] == (
        9 if kind == "nonzero" else 0 if kind == "verifier_failure" else None
    )
    projection = result.to_dict()
    assert projection["failed_required_verifiers"] == ([] if kind == "startup" else [["npm", "test"]])
    assert projection["missing_required_verifiers"] == ([["npm", "test"]] if kind == "startup" else [])
    assert close.call_count == 1


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


def test_project_typecheck_does_not_erase_required_file_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unit dispatch: a tsconfig may exclude the required parser's source."""
    import polaris.kernelone.quality as quality
    from polaris.cells.factory.pipeline.internal import factory_node_dependencies as dependencies
    from polaris.kernelone.quality.artifact_quality import ArtifactQualityDeferredCheck

    (tmp_path / "tsconfig.json").write_text('{"include":["src/main.ts"]}', encoding="utf-8")
    evidence = ArtifactQualityEvidence(
        scan_mode="pure_static",
        deferred_checks=(
            ArtifactQualityDeferredCheck("typescript_project_typecheck", ("src/main.ts",), project_relative_path="."),
            ArtifactQualityDeferredCheck("typescript_parser", ("tests/excluded.ts",)),
        ),
    )
    monkeypatch.setattr(quality, "scan_workspace_artifact_quality_evidence", Mock(return_value=evidence))
    monkeypatch.setattr(
        dependencies,
        "authenticated_preparation_contract",
        Mock(return_value=SimpleNamespace(project_id="unit-project")),
    )
    monkeypatch.setattr(materialization, "node_dependency_readiness", Mock(return_value=SimpleNamespace(ready=True)))
    commands: list[list[str]] = []

    async def command(argv: list[str], _timeout: float) -> dict[str, Any]:
        commands.append(argv)
        parser = "tests/excluded.ts" in argv
        return {
            "command": argv,
            "exit_code": 1 if parser else 0,
            "passed": not parser,
            "stdout_tail": "tests/excluded.ts: error TS1005: expected ';'" if parser else "",
            "stderr_tail": "",
        }

    monkeypatch.setattr(
        materialization.NativeValidationSession,
        "from_factory",
        Mock(return_value=SimpleNamespace(run_command=command, close=lambda: None)),
    )
    observation = asyncio.run(
        _collect(OrchestrationStageExecutor(tmp_path), run=_run(), context={"project_id": "unit-project"})
    )
    assert any("tests/excluded.ts" in argv for argv in commands)
    assert any("TS1005" in diagnostic for diagnostic in observation)
    assert len(observation.verifier_results) == 2


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
    from polaris.cells.factory.pipeline.internal import (
        factory_node_dependencies as dependencies,
        native_validation_group as group,
    )

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
    admission = Mock(side_effect=AssertionError("manifest race reached CE/Group"))
    authentication = Mock(side_effect=AssertionError("manifest race reached CE authentication"))
    spawn = Mock(side_effect=AssertionError("manifest race spawned a verifier"))
    preparation = Mock(side_effect=AssertionError("manifest race started preparation"))
    claim = Mock(side_effect=AssertionError("manifest race claimed repair"))
    schedule = Mock(side_effect=AssertionError("manifest race entered repair schedule"))
    monkeypatch.setattr(materialization.NativeValidationSession, "from_factory", admission)
    monkeypatch.setattr(dependencies, "authenticated_preparation_contract", authentication)
    monkeypatch.setattr(group, "_owned_process_runner", spawn)
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
