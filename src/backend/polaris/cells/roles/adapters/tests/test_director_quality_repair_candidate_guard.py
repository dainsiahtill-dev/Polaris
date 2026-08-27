from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_candidate_guard_restores_rejected_utf8_edit(tmp_path: Path) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate._candidate_guard import (
        DirectorQualityRepairCandidateGuard,
    )

    target = tmp_path / "src" / "patience.rs"
    target.parent.mkdir(parents=True)
    target.write_text("// 已验证版本\nfn patience() {}\n", encoding="utf-8")

    guard = await DirectorQualityRepairCandidateGuard.capture(
        workspace=tmp_path,
        candidate_id="factory-run:round-3",
        target_files=["src/patience.rs"],
    )
    target.write_text("// 回归候选\nfn patience() { panic!() }\n", encoding="utf-8")
    await guard.seal_effect()

    receipt = await guard.rollback(reason="reintroduced_regression_guard")

    assert receipt["status"] == "restored"
    assert receipt["restored_files"] == ["src/patience.rs"]
    assert target.read_text(encoding="utf-8") == "// 已验证版本\nfn patience() {}\n"


@pytest.mark.asyncio
async def test_candidate_guard_aborts_when_post_effect_hash_drifted(tmp_path: Path) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate._candidate_guard import (
        DirectorQualityRepairCandidateGuard,
    )

    target = tmp_path / "src" / "main.rs"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    guard = await DirectorQualityRepairCandidateGuard.capture(
        workspace=tmp_path,
        candidate_id="factory-run:round-4",
        target_files=["src/main.rs"],
    )
    target.write_text("candidate\n", encoding="utf-8")
    await guard.seal_effect()
    target.write_text("concurrent-drift\n", encoding="utf-8")

    receipt = await guard.rollback(reason="verifier_regression")

    assert receipt["status"] == "aborted_state_drift"
    assert receipt["drifted_files"] == ["src/main.rs"]
    assert target.read_text(encoding="utf-8") == "concurrent-drift\n"


@pytest.mark.asyncio
async def test_candidate_guard_accept_keeps_effect_and_closes_snapshot(tmp_path: Path) -> None:
    from polaris.cells.roles.adapters.internal.director.quality_gate._candidate_guard import (
        DirectorQualityRepairCandidateGuard,
    )

    target = tmp_path / "src" / "lib.rs"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    guard = await DirectorQualityRepairCandidateGuard.capture(
        workspace=tmp_path,
        candidate_id="factory-run:round-5",
        target_files=["src/lib.rs"],
    )
    target.write_text("accepted\n", encoding="utf-8")
    await guard.seal_effect()

    receipt = guard.accept(reason="verifier_progress")

    assert receipt["status"] == "accepted"
    assert target.read_text(encoding="utf-8") == "accepted\n"
    second = await guard.rollback(reason="late_rejection")
    assert second["status"] == "closed"


@pytest.mark.asyncio
async def test_public_quality_repair_returns_sealed_guard_for_real_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.adapters.internal.director import quality_gate
    from polaris.cells.roles.adapters.public.service import run_director_materialization_quality_repair

    target = tmp_path / "src" / "patience.rs"
    target.parent.mkdir(parents=True)
    target.write_text("verified\n", encoding="utf-8")

    async def fake_retry(*_args: object, **_kwargs: object):
        target.write_text("candidate\n", encoding="utf-8")
        return (
            [
                {
                    "tool": "edit_file",
                    "success": True,
                    "result": {
                        "file": "src/patience.rs",
                        "operation": "modify",
                        "before_hash": "before",
                        "after_hash": "after",
                    },
                }
            ],
            {"attempted": True, "write_tool_evidence": True},
        )

    monkeypatch.setattr(quality_gate, "_run_materialization_quality_repair_retry", fake_retry)

    _results, summary = await run_director_materialization_quality_repair(
        str(tmp_path),
        # The claimed owner task can have a broad/root facade target while
        # Factory's verifier diagnosis has already narrowed the authorized
        # repair to the causal implementation file.  The public boundary must
        # preserve that explicit authority instead of re-deriving it from the
        # task row (live L3-23 otherwise snapshotted src/lib.rs while the tool
        # physically edited src/patience.rs).
        task={"id": "TASK-1", "target_files": ["src/lib.rs", "src/patience.rs"]},
        target_task_id="TASK-1",
        run_id="factory-guard",
        context={"director_quality_repair": {"repair_target_files": ["src/lib.rs"]}},
        original_message="repair",
        llm_call_timeout=30.0,
        artifact_quality_errors=["named test failed"],
        changed_files=["src/patience.rs"],
        repair_target_files=["src/lib.rs"],
    )

    guard = summary["_candidate_guard"]
    receipt = await guard.rollback(reason="verifier_equal_count_swap")
    assert receipt["status"] == "restored"
    assert receipt["affected_files"] == ["src/patience.rs"]
    assert receipt["restored_files"] == ["src/patience.rs"]
    assert target.read_text(encoding="utf-8") == "verified\n"
