"""Candidate retention never authorizes a different owner terminal outcome."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal import (
    factory_workspace_quality_impl as quality,
    native_validation_inputs as inputs,
)
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import (
    _freeze,
    _projection,
    _real_effect,
    authored as authored,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        "settlement_directed_effect_unresolved",
        "settlement_parent_registry_unavailable",
        "settlement_terminal_intent_conflict",
        "session_mismatch",
        "settlement_effect_outcome_conflict",
    ],
)
async def test_only_owner_outcome_conflict_permits_real_failure_close(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    calls: list[str] = []

    def close(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["stage_status"])
        return (
            {"success": False, "code": code, "evidence": {"physical_proof": code}}
            if len(calls) == 1
            else {
                "success": True,
                "code": "settled",
                "evidence": {"actual_outcome": "failed"},
            }
        )

    monkeypatch.setattr(quality, "_record_workspace_quality_repair_artifact_receipts", lambda _pending: ())
    result = await quality._settle_pending_workspace_quality_repair_attempt(
        SimpleNamespace(_settle_director_stage_materialization_attempt=close),
        {
            "task_id": "original",
            "task_row_id": 1,
            "execution_attempt": SimpleNamespace(session_id="same"),
            "mutation_committed": True,
        },
        accepted=True,
        verifier_resolved=True,
        reason="current verifier passed",
    )
    assert result is not None and result["candidate_accepted"] is True
    fallback = code == "settlement_effect_outcome_conflict"
    assert calls == (["success", "failed"] if fallback else ["success"])
    assert result["settled"] is fallback
    assert result["settlement_code"] == ("settled" if fallback else code)
    assert result["completion_rejection"] == (
        {"success": False, "code": code, "evidence": {"physical_proof": code}} if fallback else {}
    )
    assert result["requested_outcome"] == "completed"
    assert result["outcome"] == ("failed" if fallback else "completed")


@pytest.mark.asyncio
async def test_verified_progress_retains_candidate_but_requests_failed_terminal_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Guard:
        def accept(self, *, reason: str) -> dict[str, str]:
            return {"status": "accepted", "reason": reason}

        async def rollback(self, **_kwargs: Any) -> None:
            raise AssertionError("verified progress must remain on disk")

    def close(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["stage_status"])
        return {"success": True, "code": "settled"}

    monkeypatch.setattr(quality, "_record_workspace_quality_repair_artifact_receipts", lambda _pending: ())
    result = await quality._settle_pending_workspace_quality_repair_attempt(
        SimpleNamespace(_settle_director_stage_materialization_attempt=close),
        {
            "task_id": "original",
            "task_row_id": 1,
            "execution_attempt": SimpleNamespace(session_id="same"),
            "mutation_committed": True,
            "candidate_guard": Guard(),
        },
        accepted=True,
        verifier_resolved=False,
        reason="progress",
    )
    assert calls == ["failed"]
    assert result is not None and result["settled"] is True and result["outcome"] == "failed"
    assert result["candidate_accepted"] is True and result["verifier_resolved"] is False
    assert result["candidate_guard_receipt"]["status"] == "accepted"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attack", [None, "foreign_close", "reason", "rollover_hash", "parent_binding", "after_hash", "commit_hash"]
)
async def test_real_rollover_keeps_immutable_commit_binding_and_rejects_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authored: tuple,
    attack: str | None,
) -> None:
    portfolio, contract, _fs = authored
    baseline = _freeze(tmp_path, contract)
    attempt, first = await _real_effect(tmp_path)
    attempt, second = await _real_effect(
        tmp_path, attempt=attempt, target_path="src/authored.js", turn_id="next-native-batch"
    )
    rows = first + second
    original_query = inputs.query_fact_events

    def query(command: Any) -> Any:
        result = original_query(command)
        events = deepcopy(result.events)
        for event in events:
            payload = event.get("payload", {})
            descriptor = payload.get("replay_descriptor", {})
            if descriptor.get("command") != "close_by_parent":
                continue
            if attack == "foreign_close":
                descriptor["actor"] = "foreign"
            elif attack == "reason":
                descriptor["reason"] = "unrelated terminal close"
            elif attack == "rollover_hash":
                descriptor["batch_rollover_hash"] = "a" * 64
            elif attack == "parent_binding":
                payload["operation"]["parent_binding_id"] = "foreign"
        return replace(result, events=events)

    monkeypatch.setattr(inputs, "query_fact_events", query)
    if attack == "after_hash":
        (tmp_path / "src/a.py").write_text("uncommitted drift\n", encoding="utf-8")
    elif attack == "commit_hash":
        rows = deepcopy(rows)
        rows[0]["effect_receipt_commit"]["receipt_hash"] = "a" * 64
    def operation() -> Any:
        return inputs.overlay_native_validation_candidate(
            baseline,
            pending={"task_id": attempt.external_task_id, "task_row_id": attempt.task_id, "execution_attempt": attempt},
            task_completion_projection=_projection(portfolio, contract),
            round_repair_results=rows,
        )
    if attack is None:
        candidate = operation()
        assert candidate.input_bytes["src/a.py"] == b"after\n"
        assert candidate.input_bytes["src/authored.js"] == b"after\n"
    else:
        with pytest.raises(inputs.NativeValidationInputError):
            operation()
