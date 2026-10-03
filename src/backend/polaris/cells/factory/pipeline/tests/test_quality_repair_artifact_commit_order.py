"""A failed artifact registration must not close the candidate rollback guard."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.factory.pipeline.internal import factory_workspace_quality_impl as quality
from polaris.cells.roles.adapters.internal.director.quality_gate._candidate_guard import (
    DirectorQualityRepairCandidateGuard,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_projection", [False, True])
async def test_artifact_failure_restores_candidate_before_failed_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_projection: bool
) -> None:
    """Accept-before-record used to leave changed bytes with no artifact receipt."""
    path = tmp_path / "source.txt"
    path.write_text("before\n", encoding="utf-8")
    guard = await DirectorQualityRepairCandidateGuard.capture(
        workspace=tmp_path, candidate_id="registration-failure", target_files=["source.txt"]
    )
    path.write_text("candidate\n", encoding="utf-8")
    await guard.seal_effect()
    settlements: list[str] = []

    def settle(**kwargs: object) -> dict[str, bool]:
        settlements.append(str(kwargs["stage_status"]))
        return {"success": True}

    if not missing_projection:

        def failed_record(_pending: object) -> tuple[dict[str, str], ...]:
            raise RuntimeError("artifact_owner_commit_failed")

        monkeypatch.setattr(quality, "_record_workspace_quality_repair_artifact_receipts", failed_record)
    pending = {
        "task_id": "TASK-1",
        "task_row_id": "runtime-row-1",
        "execution_attempt": SimpleNamespace(session_id="owned-fixture-attempt"),
        "candidate_guard": guard,
        "mutation_committed": True,
    }
    result = await quality._settle_pending_workspace_quality_repair_attempt(
        SimpleNamespace(_settle_director_stage_materialization_attempt=settle),
        pending,
        accepted=True,
        reason="verified_candidate",
    )
    assert result is not None
    assert result["outcome"] == "failed"
    assert result["artifact_receipt_error"]
    assert settlements == ["failed"]
    assert result["candidate_guard_receipt"]["status"] == "restored"
    assert path.read_text(encoding="utf-8") == "before\n"
