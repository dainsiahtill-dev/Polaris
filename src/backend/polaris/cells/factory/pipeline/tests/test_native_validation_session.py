"""One candidate's actual command group must share a closed source snapshot."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.factory.pipeline.internal.native_validation_session import NativeValidationSession
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import (
    _projection,
    _real_effect,
    authored as authored,
)

pytestmark = pytest.mark.skipif(os.name != "posix" or shutil.which("bwrap") is None, reason="real bwrap required")


@pytest.mark.asyncio
async def test_session_runs_shared_group_from_real_owner_baseline(tmp_path: Path, authored: tuple) -> None:
    _, contract, _ = authored
    session = NativeValidationSession(
        workspace=tmp_path,
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
    )
    try:
        first = await session.run_command(["/bin/sh", "-c", "mkdir -p dist; cp src/a.py dist/result.txt"], 3)
        second = await session.run_command(["/bin/cat", "dist/result.txt"], 3)
        assert first["exit_code"] == second["exit_code"] == 0
        assert second["stdout_tail"] == "before\n"
        assert not (tmp_path / "dist").exists()
        assert second["verification_group"]["input_hash"] == first["verification_group"]["input_hash"]
    finally:
        session.close()


def test_session_never_copies_unknown_compiler_files(tmp_path: Path, authored: tuple) -> None:
    _, contract, fs = authored
    fs.workspace_write_text("tests/unknown.js", "not authored", encoding="utf-8")
    session = NativeValidationSession(
        workspace=tmp_path,
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
    )
    try:
        assert not (session.group.staging_workspace / "tests/unknown.js").exists()
        assert (session.group.staging_workspace / "src/authored.js").is_file()
    finally:
        session.close()


@pytest.mark.asyncio
async def test_candidate_uses_new_copy_after_actual_committed_effect(tmp_path: Path, authored: tuple) -> None:
    portfolio, contract, _ = authored
    session = NativeValidationSession(
        workspace=tmp_path,
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
    )
    try:
        old = await session.run_command(["/bin/sh", "-c", "mkdir -p dist; cp src/a.py dist/stale.txt"], 3)
        assert old["passed"] is True
        session.before_repair()
        attempt, rows = await _real_effect(tmp_path)
        session.candidate(
            pending={
                "task_id": attempt.external_task_id,
                "task_row_id": attempt.task_id,
                "execution_attempt": attempt,
                "task_completion_projection": _projection(portfolio, contract),
            },
            results=rows,
        )
        assert not (session.group.staging_workspace / "dist/stale.txt").exists()
        result = await session.run_command(["/bin/cat", "src/a.py"], 3)
        assert result["passed"] is True
        assert result["stdout_tail"] == "after\n"
        assert result["verification_group"]["input_hash"] != old["verification_group"]["input_hash"]
    finally:
        session.close()


def test_no_mutation_round_does_not_leave_a_closed_group_as_next_baseline(tmp_path: Path, authored: tuple) -> None:
    _, contract, _ = authored
    session = NativeValidationSession(
        workspace=tmp_path,
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
    )
    try:
        session.before_repair()
        session.before_repair()
    finally:
        session.close()


def test_factory_session_uses_committed_ce_portfolio_identity(tmp_path: Path, authored: tuple) -> None:
    portfolio, contract, fs = authored
    run = SimpleNamespace(
        id=contract.run_id,
        metadata={
            "stage_results": {
                "chief_engineer_review": {
                    "status": "success",
                    "artifacts": [portfolio.portfolio_path],
                }
            }
        },
    )
    executor = SimpleNamespace(
        workspace=tmp_path,
        _canonical_project_id=lambda _context: contract.project_id,
        _read_json_artifact_payload=lambda path: fs.read_json(path),
    )
    session = NativeValidationSession.from_factory(executor, run, {})
    try:
        assert session.baseline.contract == contract
    finally:
        session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("automatic", [True, False])
async def test_new_group_dependency_discovery_respects_explicit_deny_and_known_roots(
    tmp_path: Path, authored: tuple, automatic: bool
) -> None:
    portfolio, contract, _ = authored
    session = NativeValidationSession(
        workspace=tmp_path,
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
        dependency_roots=None if automatic else {},
    )
    try:
        session.before_repair()
        attempt, rows = await _real_effect(tmp_path)
        for name in ("node_modules", "arbitrary-private-root"):
            (tmp_path / name).mkdir()
            (tmp_path / name / "payload").write_text("prepared", encoding="utf-8")
        session.candidate(
            pending={
                "task_id": attempt.external_task_id,
                "task_row_id": attempt.task_id,
                "execution_attempt": attempt,
                "task_completion_projection": _projection(portfolio, contract),
            },
            results=rows,
        )
        command = [
            "/bin/sh",
            "-c",
            "test ! -e arbitrary-private-root && "
            + ("test $(cat node_modules/payload) = prepared" if automatic else "test ! -e node_modules"),
        ]
        result = await session.run_command(command, 5)
        assert result["exit_code"] == 0, result.get("stderr_tail")
    finally:
        session.close()
