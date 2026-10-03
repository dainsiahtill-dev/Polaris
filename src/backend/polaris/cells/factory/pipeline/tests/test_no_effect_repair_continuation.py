"""Empty owner attempts must reach no-progress handling, not candidate rejection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.internal.factory_run_service import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.tests._characterization_helpers import _executor
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import (
    _projection,
    authored as authored,
    production,
)
from polaris.cells.roles.adapters.internal.director.quality_gate._candidate_guard import (
    DirectorQualityRepairCandidateGuard,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims_mutation,claims_progress,silent_drift",
    [(False, False, False), (True, True, False), (True, False, False), (False, True, False), (False, False, True)],
)
async def test_empty_owner_attempt_is_noop_unless_it_claims_mutation(
    tmp_path: Path,
    authored: tuple,
    monkeypatch: pytest.MonkeyPatch,
    claims_mutation: bool,
    claims_progress: bool,
    silent_drift: bool,
) -> None:
    portfolio, contract, fs = authored
    executor = _executor(tmp_path)
    run = FactoryRun(
        id=contract.run_id,
        config=FactoryConfig(name="no-effect-continuation"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-03T00:00:00+00:00",
    )
    run.metadata["stage_results"] = {
        "chief_engineer_review": {"status": "success", "artifacts": [portfolio.portfolio_path]}
    }
    monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda _run, _context: None)
    monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda _context: None)
    provider_attempts = []

    async def empty_owner_repair(**_kwargs: object) -> tuple:
        guard = await DirectorQualityRepairCandidateGuard.capture(
            workspace=tmp_path, candidate_id=f"empty-{len(provider_attempts)}", target_files=["src/a.py"]
        )
        attempt = production._setup_attempt(str(tmp_path))
        provider_attempts.append(attempt)
        if silent_drift:
            fs.workspace_write_text("src/a.py", "unreceipted mutation\n", encoding="utf-8")
        return [], {
            "stage": "quality_repair",
            "attempted": True,
            "success": False,
            "task_id": attempt.external_task_id,
            "write_tool_evidence": claims_progress,
            "_pending_task_runtime_repair_attempt": {
                "task_id": attempt.external_task_id,
                "task_row_id": attempt.task_id,
                "execution_attempt": attempt,
                "candidate_guard": guard,
                "mutation_committed": claims_mutation,
                "task_completion_projection": _projection(portfolio, contract),
            },
        }

    monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", empty_owner_repair)
    monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", empty_owner_repair)
    before = {item.path: fs.workspace_read_bytes(item.path) for item in contract.obligations.artifacts}
    passed, artifact = await executor._run_workspace_quality_checks(
        run,
        {
            "project_id": contract.project_id,
            "workspace_quality_repair_max_rounds": 2,
            "quality_commands": [["/bin/sh", "-c", "echo 'src/a.py: needs repair' >&2; exit 1"]],
            "workspace_validation_install_dependencies": False,
        },
    )
    payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
    assert passed is False
    if silent_drift:
        # Unknown physical bytes are evidence, not authority to overwrite them.
        assert fs.workspace_read_bytes("src/a.py") == b"unreceipted mutation\n"
    else:
        assert {path: fs.workspace_read_bytes(path) for path in before} == before
    assert len([row for row in payload["commands"] if row["phase"] == "check"]) == 1
    if claims_mutation or claims_progress or silent_drift:
        assert payload["warnings"] == ["factory_quality_gate_verification_candidate_not_qualified"]
        if claims_mutation or claims_progress:
            assert "native_validation_candidate_effects_missing" in payload["error"]
        else:
            assert payload["error"] == "verification_input_hash_mismatch:src/a.py"
    else:
        assert "factory_quality_gate_verification_candidate_not_qualified" not in payload.get("warnings", [])
        assert payload["repair"]["attempted"] is True
        rounds = payload["repair"]["rounds"]
        assert len(rounds) == 2
        for round_result in rounds:
            assert round_result["verifier_effect"] == "no_op"
            settled = round_result["repair_summary"]["task_runtime_repair_attempt"]
            assert settled["outcome"] == "failed"
            assert settled["task_id"] == "DEO-LIFECYCLE"
        assert {attempt.external_task_id for attempt in provider_attempts} == {"DEO-LIFECYCLE"}
