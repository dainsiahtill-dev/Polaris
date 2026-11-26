"""Remote advice failures stay observable without interrupting engineering."""

from __future__ import annotations

import asyncio
from http.client import IncompleteRead

import pytest
from polaris.cells.roles.runtime.internal.engineering_epoch_runtime import EpochRuntime, facts_for_epoch
from polaris.cells.roles.runtime.internal.engineering_semantic_advice import CandidateRefs
from polaris.cells.roles.runtime.internal.engineering_semantic_runtime import SemanticRuntime


class DroppedResponse:
    async def summarize_many(self, requests):
        raise IncompleteRead(b"", 20)


async def test_distiller_records_interrupted_transport_once_and_keeps_cached_results_empty() -> None:
    source = {
        "node_id": "file:a.py",
        "snapshot_id": "filecard:a",
        "content": "def inc(n): return n+1",
        "kind": "file_card",
        "coverage": {"observed_line_ranges": [[1, 1]]},
    }
    refs = CandidateRefs(node_ids={"file:a.py"}, slice_ids=set(), evidence_refs={"filecard:a"})
    runtime = SemanticRuntime(DroppedResponse(), "assist")
    runtime.schedule({"candidates": [source]}, refs, fingerprint="one")
    await asyncio.sleep(0)
    result = runtime.poll()
    assert result and result["validation_result"] == "REJECTED"
    assert "IncompleteRead" in result["summaries"][0]["error"]
    assert runtime.fold_summaries() == {}
    runtime.schedule({"candidates": [source]}, refs, fingerprint="later")
    await asyncio.sleep(0)
    assert runtime.poll() is None


async def test_devourer_keeps_observed_epoch_when_remote_response_drops() -> None:
    events = [{"seq": 1, "kind": "run_started", "role": "director", "payload": {}}]
    for n in range(2, 34):
        events.append({"seq": n, "kind": "turn_started", "role": "director", "payload": {}})
    request, refs, fp = facts_for_epoch({"instruction": "test"}, events)
    runtime = EpochRuntime(DroppedResponse())
    runtime.schedule(request, refs, fingerprint=fp)
    await asyncio.sleep(0)
    card = runtime.poll()
    assert card and card["advice_status"] == "rejected"
    assert "IncompleteRead" in card["advice_error"]
    assert card["summary_kind"] == "journal_facts"
    assert "exploration" not in card


@pytest.mark.parametrize("response", [[None], [{"validation_result": "VALID", "result": {}}], []])
async def test_distiller_rejects_an_unusable_pool_receipt_without_failing_the_role(response) -> None:
    class Pool:
        async def summarize_many(self, requests):
            return response

    runtime = SemanticRuntime(Pool(), "assist")
    item = {
        "node_id": "file:x",
        "snapshot_id": "filecard:x",
        "kind": "file_card",
        "content": "return source",
        "coverage": {"observed_line_ranges": [[1, 1]]},
    }
    runtime.schedule(
        {"candidates": [item]},
        CandidateRefs(node_ids={"file:x"}, evidence_refs=set(), slice_ids=set()),
        fingerprint="test",
    )
    await asyncio.sleep(0)
    result = runtime.poll()
    assert result and result["validation_result"] == "REJECTED"
    assert result["summaries"][0]["error"]
    assert not runtime.fold_summaries()
