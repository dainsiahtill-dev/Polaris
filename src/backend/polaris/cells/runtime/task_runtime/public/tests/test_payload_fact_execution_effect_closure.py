"""Actual private CLI/DEO/owner proof; V3 uses the real public payload fact producer.

The existing fixture's owner-outcome appender is replaced only for its role
stream: TaskRuntime/DEO facts and real command execution remain unchanged.
No old facts are rewritten or projected into synthetic V2 events.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.events.fact_stream import public as facts
from polaris.cells.runtime.task_runtime.public.execution_effect_closure import (
    QueryExecutionEffectClosureV1,
    query_execution_effect_closure,
)
from polaris.cells.runtime.task_runtime.public.tests.test_dead_letter_execution_effect_closure import _real_failed_cli


async def _real_v3_failed_cli(root: Path, monkeypatch: pytest.MonkeyPatch):
    old_append = facts.append_fact_event

    def append(command):
        if command.stream != "roles.kernel.turn_outcomes":
            return old_append(command)
        payload = dict(command.payload)
        payload["schema_version"] = "roles.kernel.turn_outcome_fact.v3"
        ref = facts.store_fact_payload(
            facts.StoreFactPayloadCommandV1(
                workspace=str(root),
                payload=payload,
                payload_schema=payload["schema_version"],
                maintenance_reason="private real negative closure regression",
            )
        )
        facts.ensure_segmented_fact_ledger(
            facts.EnsureSegmentedFactLedgerCommandV1(
                workspace=str(root),
                logical_stream="roles.kernel.turn_outcomes.v3",
                maintenance_reason="private real negative closure initial namespace",
            )
        )
        return facts.append_payload_fact_event(
            facts.AppendPayloadFactEventCommandV1(
                workspace=str(root),
                logical_stream="roles.kernel.turn_outcomes.v3",
                event_type=command.event_type,
                source=command.source,
                payload_ref=ref,
                idempotency_key=command.idempotency_key.replace(
                    "roles.kernel.turn_outcomes:", "roles.kernel.turn_outcomes.v3:", 1
                ),
                expected_global_seq=1,
            )
        )

    with monkeypatch.context() as patch:
        patch.setattr(facts, "append_fact_event", append)
        attempt, batch = await _real_failed_cli(root, empty_error=True)
    assert not (root / ".polaris/runtime/events/roles.kernel.turn_outcomes.jsonl").exists()
    return attempt, batch


@pytest.mark.asyncio
async def test_real_v3_cli_rc2_has_closed_negative_proof_without_success_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    attempt, batch = await _real_v3_failed_cli(tmp_path, monkeypatch)
    assert batch["raw_results"][0]["result"]["exit_code"] == 2
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    observed = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempt))
    assert observed.status == "closed", observed
    assert observed.receipts == ()
    assert len(observed.terminal_failures) == 1
    assert observed.terminal_failures[0].exit_code == 2
    assert observed.execution_authority is False
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["corrupt_blob", "foreign_owner", "malformed_raw"])
async def test_v3_negative_closure_rejects_corrupt_foreign_or_unsealed_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str
):
    from dataclasses import replace

    from polaris.cells.runtime.task_runtime.internal import execution_effect_closure as closure

    attempt, _batch = await _real_v3_failed_cli(tmp_path, monkeypatch)
    if attack == "corrupt_blob":
        blobs = list((tmp_path / ".polaris/runtime/events/.payloads").glob("*/*.json"))
        assert len(blobs) == 1
        blobs[0].write_bytes(b"{}")
    elif attack == "foreign_owner":
        attempt = replace(attempt, session_id="foreign-session")
    else:
        original = closure.query_payload_fact_events

        def read(query):
            result = original(query)
            rows = [dict(row) for row in result.events]
            rows[0]["storage_record"] = {**rows[0]["storage_record"], "event_hash": "0" * 64}
            rows[0]["event_hash"] = "0" * 64
            return replace(result, events=tuple(rows))

        monkeypatch.setattr(closure, "query_payload_fact_events", read)
    observed = query_execution_effect_closure(QueryExecutionEffectClosureV1(str(tmp_path), attempt))
    assert observed.status != "closed", observed
    assert observed.receipts == observed.terminal_failures == ()
    assert observed.execution_authority is False
