"""Real command failures remain negative when a later legal owner effect finishes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public.ledger import stable_hash
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import _real_effect
from polaris.cells.runtime.task_runtime.public import (
    DirectedEffectParentRegistryIdentityV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    settle_task_runtime_execution_attempt_typed,
)
from polaris.cells.runtime.task_runtime.public.execution_effect_closure import (
    QueryExecutionEffectClosureV1,
    query_execution_effect_closure,
)
from polaris.cells.runtime.task_runtime.public.tests import test_dead_letter_execution_effect_closure as cli_fixture
from polaris.cells.runtime.task_runtime.public.tests.test_payload_fact_execution_effect_closure import (
    _real_v3_failed_cli,
)
from polaris.kernelone.events.sourcing.models import EventEnvelope


async def _failed_then_legal_effect(
    root: Path, monkeypatch: pytest.MonkeyPatch, *, outcome: str | None = "completed", v3: bool = False
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    if v3:
        # Defer only the test helper's final settle. Real V3 payload producer,
        # command, DEO facts and the later public TaskRuntime settlement remain real.
        with monkeypatch.context() as patch:
            patch.setattr(cli_fixture, "_settle", lambda *_args: None)
            attempt, failed = await _real_v3_failed_cli(root, patch)
    else:
        attempt, failed = await cli_fixture._real_failed_cli(root, settle=False)
    (root / "src").mkdir()
    (root / "src/a.txt").write_text("before\n", encoding="utf-8")
    continued, rows = await _real_effect(root, attempt=attempt, target_path="src/a.txt", turn_id="legal-after-failure")
    assert continued.session_id == attempt.session_id
    assert (root / "src/a.txt").read_text(encoding="utf-8") == "after\n"
    assert rows[0]["effect_receipt_commit"]["code"] == "receipt_committed"
    if outcome is not None:
        settled = settle_task_runtime_execution_attempt_typed(
            SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=str(root),
                identity=continued,
                outcome=outcome,
                summary="private owner continuation settled",
            )
        )
        assert settled.success, settled
    return continued, failed, rows[0]


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "failed"])
@pytest.mark.parametrize("v3", [False, True], ids=["v2", "v3"])
async def test_terminal_owner_keeps_real_failed_command_and_later_legal_effect_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str, v3: bool
) -> None:
    attempt, failed, legal = await _failed_then_legal_effect(tmp_path, monkeypatch, outcome=outcome, v3=v3)
    before = _snapshot(tmp_path)
    observed = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempt))
    assert observed.status == "closed", observed
    assert len(observed.receipts) == 1
    assert observed.receipts[0].receipt_hash == legal["effect_receipt"]["receipt_hash"]
    assert len(observed.terminal_failures) == 1
    negative = observed.terminal_failures[0]
    physical = failed["raw_results"][0]["result"]
    assert negative.failure_kind == "physical_result_failed"
    assert negative.exit_code == physical["exit_code"] == (2 if v3 else 1)
    assert negative.operation_id == physical["effect_recovery"]["operation_id"]
    assert negative.resolution_evidence_hash == physical["effect_recovery"]["resolution_evidence_hash"]
    assert negative.operation_id not in {receipt.operation_id for receipt in observed.receipts}
    assert observed.execution_authority is False
    assert before == _snapshot(tmp_path)


@pytest.mark.asyncio
async def test_finished_command_does_not_close_an_active_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    attempt, _failed, _legal = await _failed_then_legal_effect(tmp_path, monkeypatch, outcome=None)
    observed = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempt))
    assert observed.status == "active", observed
    assert observed.receipts == observed.terminal_failures == ()
    assert observed.execution_authority is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attack", ["recovery_pending", "timeout_shaped", "foreign_provenance", "stale_terminal_proof", "generic_false"]
)
async def test_completed_owner_cannot_replace_missing_or_invalid_negative_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str
) -> None:
    attempt, _failed, _legal = await _failed_then_legal_effect(tmp_path, monkeypatch)
    if attack == "recovery_pending":
        from polaris.cells.runtime.task_runtime.internal.directed_effect_operation import (
            DirectedEffectOperationRepository,
        )

        registry = DirectedEffectOperationRepository()._load_registry(
            str(tmp_path), DirectedEffectParentRegistryIdentityV1.from_execution_attempt(attempt)
        )
        binding = next(
            value for value in registry.bindings_by_id.values() if value.correlation.turn_id == "turn-failed-cli"
        )
        path = tmp_path / f".polaris/runtime/events/{binding.operation_stream_token}.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        path.write_text("".join(lines[:-1]), encoding="utf-8")
    elif attack == "stale_terminal_proof":
        path = tmp_path / f".polaris/runtime/tasks/task_{attempt.task_id}.session.json"
        session = json.loads(path.read_text(encoding="utf-8"))
        session["metadata"]["terminal_settlement_proof"]["terminal_intent_hash"] = "0" * 64
        path.write_text(json.dumps(session), encoding="utf-8")
    else:
        path = tmp_path / ".polaris/runtime/events/roles.kernel.turn_outcomes.jsonl"
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        event = events[-1]
        outcome = event["payload"]["outcome"]
        if attack == "foreign_provenance":
            event["payload"]["provenance"]["run_id"] = "foreign-owner"
        else:
            for key in ("results", "raw_results"):
                row = outcome["execution"]["receipt"][key][0]
                if attack == "timeout_shaped":
                    row["result"]["exit_code"] = None
                    row["result"]["physical_error_type"] = "TimeoutError"
                else:
                    row["result"] = {"ok": False, "exit_code": 1, "error": "caller claims failure"}
        event["payload"]["outcome_hash"] = stable_hash(outcome)
        event["integrity_digest"] = EventEnvelope.integrity_digest_for_record(event)
        path.write_text("".join(json.dumps(value) + "\n" for value in events), encoding="utf-8")
    before = _snapshot(tmp_path)
    observed = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempt))
    assert observed.status != "closed", observed
    assert observed.receipts == observed.terminal_failures == ()
    assert observed.execution_authority is False
    assert before == _snapshot(tmp_path)
