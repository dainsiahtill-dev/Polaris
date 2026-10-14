"""Current signed CE checks must run from authored inputs in a fresh group."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal import factory_workspace_quality_impl as quality
from polaris.cells.factory.pipeline.internal.native_validation_session import (
    NativeValidationSession,
    required_clean_verifier_commands,
)
from polaris.cells.factory.pipeline.tests.test_materialization_contract_verifiers import _fixture
from polaris.cells.runtime.execution_broker.public import project_verification as broker


def _record_sources(executor: Any, run: Any, port: Any) -> None:
    contract = port.completion_contract
    for artifact in contract.obligations.artifacts:
        broker.record_project_artifact(
            broker.RecordProjectArtifactCommandV1(
                workspace=str(executor.workspace),
                project_id=contract.project_id,
                run_id=run.id,
                completion_contract_hash=contract.contract_hash,
                obligation_id=artifact.obligation_id,
                owner_task_id=artifact.owner_task_id,
                path=artifact.path,
            )
        )


@pytest.mark.asyncio
async def test_inferred_working_entry_cannot_replace_signed_missing_entry_in_clean_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, port = _fixture(
        tmp_path,
        monkeypatch,
        entrypoint=True,
        compiled_entrypoint=True,
        build_script_name="compile_project.py",
        stdlib_verifier=True,
    )
    _record_sources(executor, run, port)
    # An old emitted host entry must never seed the clean verification group.
    (executor.workspace / "dist").mkdir()
    (executor.workspace / "dist/entrypoint.pyc").write_bytes(b"old dirty output")
    context = {
        "quality_commands": [
            ["python", "compile_project.py"],
            ["python", "-m", "unittest", "discover", "-s", "tests"],
            ["python", "dist/src/entrypoint.pyc"],
        ]
    }
    monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda *_args: None)
    monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda *_args: [])

    async def no_candidate(**kwargs: Any) -> Any:
        return [], {"attempted": False, "success": False}

    monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", no_candidate)
    monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", no_candidate)
    passed, report_ref = await quality._run_workspace_quality_checks(executor, run, context)
    import json

    from polaris.kernelone.storage import resolve_storage_roots

    report = json.loads(
        (
            Path(resolve_storage_roots(str(executor.workspace)).runtime_root) / report_ref.removeprefix("runtime/")
        ).read_text(encoding="utf-8")
    )
    assert passed is False, report
    rows = report.get("effective_commands") or report["commands"]
    assert any(row["command"] == ["python", "dist/entrypoint.pyc"] and row.get("passed") is False for row in rows)
    assert all("dist/entrypoint.pyc" not in row.get("verification_group", {}).get("input_hashes", {}) for row in rows)
    assert (executor.workspace / "dist/entrypoint.pyc").read_bytes() == b"old dirty output"


def test_required_typecheck_recipe_is_selected_even_when_scripts_and_context_omit_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, port = _fixture(tmp_path, monkeypatch, required_lint=True)
    # Real public owner contract and exact authorized lint hash, absent hints.
    commands = executor._workspace_quality_commands(
        {"quality_commands": [["python", "-m", "compileall", "-b", "src"]]}, run=run
    )
    assert ["python", "-m", "pytest", "-q", "tests/test_output.py"] in commands
    assert ["python", "-m", "compileall", "-q", "src"] in commands
    assert len(commands) == len(
        [
            v
            for v in port.completion_contract.obligations.verification
            if v.applicability == "required" and v.modality != "environment_prep"
        ]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["command", "foreign_contract", "false_grant"])
async def test_current_ce_authority_never_comes_from_copied_or_foreign_carrier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    executor, run, port = _fixture(tmp_path, monkeypatch)
    _record_sources(executor, run, port)
    commands = required_clean_verifier_commands(executor, run)
    session = NativeValidationSession.from_factory(executor, run, {}, commands=commands)
    before = {str(p): p.read_bytes() for p in executor.workspace.rglob("*") if p.is_file()}
    try:
        if defect == "command":
            argv = ["python", "-c", "raise SystemExit(0)"]
        elif defect == "foreign_contract":
            session._factory_run = replace(run, id="foreign")
            argv = list(commands[0])
        else:
            port.denied = True
            argv = list(commands[0])
        with pytest.raises((ValueError, RuntimeError)):
            await session.run_command(argv, 10)
    finally:
        session.close()
    assert before == {str(p): p.read_bytes() for p in executor.workspace.rglob("*") if p.is_file()}


@pytest.mark.asyncio
async def test_same_exact_argv_runs_both_declared_unicode_cwd_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, port = _fixture(
        tmp_path,
        monkeypatch,
        required_lint=True,
        lint_cwds=("目录一", "目录二"),
        additional_authored_files={"目录一/src/a.py": "value = 1\n", "目录二/src/b.py": "value = 2\n"},
    )
    _record_sources(executor, run, port)
    commands = required_clean_verifier_commands(executor, run)
    selected = [command for command in commands if command.authority.modality == "lint"]
    assert len(selected) == 2 and selected[0] == selected[1]
    session = NativeValidationSession.from_factory(executor, run, {}, commands=commands)
    try:
        # A JSON/list copy loses the obligation/cwd distinction; never guess it.
        with pytest.raises(ValueError, match="ambiguous"):
            await session.run_command(list(selected[0]), 10)
        results = [await session.run_command(command, 10) for command in selected]
        assert all(result["passed"] is True and result["exit_code"] == 0 for result in results)
        assert {result["ce_verification_binding"]["cwd"] for result in results} == {"目录一", "目录二"}
        for result in results:
            record = result["verification_group"]["commands"][0]
            argv = record["wrapped_argv"]
            assert argv[argv.index("--chdir") + 1] == "/workspace/" + result["ce_verification_binding"]["cwd"]
    finally:
        session.close()


@pytest.mark.asyncio
async def test_exact_entry_failure_reaches_same_owner_source_repair_and_fresh_clean_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from polaris.cells.factory.pipeline.tests.test_materialization_llm_continuation import _entrypoint_source_effect
    from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import _projection
    from polaris.cells.roles.adapters.internal.director.quality_gate._candidate_guard import (
        DirectorQualityRepairCandidateGuard,
    )

    executor, run, port = _fixture(
        tmp_path,
        monkeypatch,
        entrypoint=True,
        compiled_entrypoint=True,
        build_script_name="compile_project.py",
        stdlib_verifier=True,
        task_id="DEO-LIFECYCLE",
        run_id="run",
    )
    _record_sources(executor, run, port)
    monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda *_args: None)
    monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda *_args: [])
    calls = []

    async def repair(**kwargs: Any) -> tuple:
        feedback = "\n".join(kwargs["artifact_quality_errors"])
        assert "dist/entrypoint.pyc" in feedback and "compile_project.py" not in feedback
        assert "build.json" in feedback and "python dist/entrypoint.pyc" in feedback
        guard = await DirectorQualityRepairCandidateGuard.capture(
            workspace=executor.workspace, candidate_id="exact-entry-config", target_files=["build.json"]
        )
        attempt, rows = await _entrypoint_source_effect(
            executor.workspace, "build.json", '{"output":"dist/entrypoint.pyc"}\n', "exact-entry-config"
        )
        await guard.seal_effect()
        calls.append(attempt)
        return rows, {
            "attempted": True,
            "success": True,
            "write_tool_evidence": True,
            "_pending_task_runtime_repair_attempt": {
                "task_id": attempt.external_task_id,
                "task_row_id": attempt.task_id,
                "execution_attempt": attempt,
                "candidate_guard": guard,
                "mutation_committed": True,
                "task_completion_projection": _projection(port.blueprint_portfolio, port.completion_contract),
            },
        }

    monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", repair)
    passed, reference = await quality._run_workspace_quality_checks(
        executor, run, {"workspace_quality_repair_max_rounds": 1}
    )
    report = json.loads(executor._artifact_path(reference).read_text(encoding="utf-8"))
    assert passed is True, report
    entries = [r for r in report["commands"] if r.get("command") == ["python", "dist/entrypoint.pyc"]]
    assert [r["exit_code"] for r in entries] == [2, 0]
    identities = [
        {
            key: row["ce_verification_binding"][key]
            for key in (
                "obligation_id",
                "command_authority_hash",
                "completion_contract_hash",
                "argv",
                "cwd",
                "owner_task_id",
            )
        }
        for row in entries
    ]
    assert identities[0] == identities[1]
    assert identities[0]["owner_task_id"] == "DEO-LIFECYCLE" and identities[0]["cwd"] == "."
    for row in entries:
        record = row["verification_group"]["commands"][0]
        assert record["process_owner_terminal"] is True and record["exit_code"] == row["exit_code"]
        assert record["input_hash"] == row["verification_group"]["input_hash"]
        assert record["argv"][1:] == ["dist/entrypoint.pyc"]
    assert entries[0]["repair_diagnostic"]["metadata"]["expected_command"] == ["python", "dist/entrypoint.pyc"]
    assert entries[0]["verification_group"]["input_hash"] != entries[1]["verification_group"]["input_hash"]
    assert len(calls) == 1 and calls[0].external_task_id == "DEO-LIFECYCLE"
    assert all("dist/entrypoint.pyc" not in row["verification_group"]["input_hashes"] for row in entries)
    assert "dist/entrypoint.pyc" in entries[-1]["verification_group"]["output_hashes"]


@pytest.mark.asyncio
async def test_env_owner_receipt_must_revalidate_before_any_clean_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, port = _fixture(
        tmp_path,
        monkeypatch,
        entrypoint=True,
        compiled_entrypoint=True,
        build_script_name="compile_project.py",
        stdlib_verifier=True,
    )
    _record_sources(executor, run, port)
    monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda *_args: None)
    query = broker.query_project_verification_receipt
    monkeypatch.setattr(
        broker,
        "query_project_verification_receipt",
        lambda identity: None if identity.modality == "environment_prep" else query(identity),
    )
    calls = []

    async def no_clean_verifier(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        raise AssertionError("an unqualified ENV receipt admitted a clean command")

    monkeypatch.setattr(NativeValidationSession, "run_command", no_clean_verifier)
    with pytest.raises(ValueError, match="preparation_owner_receipt_not_current"):
        await quality._run_workspace_quality_checks(executor, run, {})
    assert calls == []


@pytest.mark.asyncio
async def test_entry_admission_failure_cannot_borrow_prior_build_process_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, port = _fixture(
        tmp_path,
        monkeypatch,
        entrypoint=True,
        compiled_entrypoint=True,
        build_script_name="compile_project.py",
        stdlib_verifier=True,
    )
    _record_sources(executor, run, port)
    commands = required_clean_verifier_commands(executor, run)
    session = NativeValidationSession.from_factory(executor, run, {}, commands=commands)
    try:
        build = next(c for c in commands if c.authority.modality == "build")
        assert (await session.run_command(build, 10))["passed"] is True
        # A real dependency/source admission check rejects launch before the
        # entry process; the group's previous build remains immutable history.
        (executor.workspace / "build.json").write_text('{"output":"dist/foreign.pyc"}', encoding="utf-8")
        entry = next(c for c in commands if c.authority.modality == "entrypoint")
        result = await session.run_command(entry, 10)
        assert result["passed"] is False
        assert result.get("verification_group", {}).get("commands", []) == []
        assert len(session.group.effects_summary()["commands"]) == 1
        diagnostic = result["repair_diagnostic"]["metadata"]
        assert diagnostic["execution_started"] is False
        assert diagnostic["command_receipt_missing"] is True
        assert result["ce_verification_binding"]["authoritative_receipt"] is False
    finally:
        session.close()
