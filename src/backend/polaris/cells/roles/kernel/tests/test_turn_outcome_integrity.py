"""Authoritative outcome streams must remain strict-readable across role sessions."""

from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    QueryFactEventsV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
    query_fact_events,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.transaction.outcome_commit import (
    TURN_OUTCOME_EVENT_TYPE,
    TURN_OUTCOME_PREPARED_EVENT_TYPE,
    TURN_OUTCOME_STREAM,
    commit_turn_result,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    ToolBatch,
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
)


def _commit_ce_outcomes(workspace: Path) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="mixed_role_integrity_regression",
        )
    )
    # This suite preserves mixed-role legacy V2 compatibility explicitly. The
    # fresh V3 protocol has separate producer/consumer and blob-integrity tests.
    from polaris.cells.events.fact_stream.public import QueryFactStreamHeadV1, query_fact_stream_head
    from polaris.kernelone.fs import KernelFileSystem, get_default_adapter

    head = query_fact_stream_head(QueryFactStreamHeadV1(workspace=str(workspace), stream=TURN_OUTCOME_STREAM))
    KernelFileSystem(str(workspace), get_default_adapter()).write_text(head.storage_path, "", encoding="utf-8")
    commit_turn_result(
        workspace=str(workspace),
        run_id="factory-mixed",
        task_id="CE-PLAN",
        transition_id="ce-rejected",
        ledger=TurnLedger(turn_id="ce-rejected"),
        result=None,
        completion_status="failed",
        failure_reason="schema mismatch: 字段无效",
    )
    repaired = TurnLedger(turn_id="ce-repaired")
    repaired.record_decision(
        TurnDecision(
            turn_id=TurnId("ce-repaired"),
            kind=TurnDecisionKind.FINAL_ANSWER,
            visible_message="蓝图修复完成",
            finalize_mode=FinalizeMode.NONE,
            domain="code",
            metadata={"repair_evidence": {"base_hash": "a" * 64, "accepted": True}},
        )
    )
    commit_turn_result(
        workspace=str(workspace),
        run_id="factory-mixed",
        task_id="CE-REPAIR",
        transition_id="ce-repaired",
        ledger=repaired,
        result={"kind": "final_answer", "visible_content": "蓝图修复完成"},
        completion_status="success",
    )


def _assert_legacy_prepare_commit_pairs(workspace: Path, outcome_count: int) -> list[dict[str, Any]]:
    """Check raw durable phases separately from authoritative outcome reads."""
    raw = query_fact_events(
        QueryFactEventsV1(workspace=str(workspace), stream=TURN_OUTCOME_STREAM, strict_integrity=True, limit=20)
    )
    assert raw.total == outcome_count * 2
    assert [event["seq"] for event in raw.events] == list(range(1, outcome_count * 2 + 1))
    committed = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream=TURN_OUTCOME_STREAM,
            event_type=TURN_OUTCOME_EVENT_TYPE,
            strict_integrity=True,
            limit=20,
        )
    )
    assert committed.total == outcome_count
    assert list(committed.events) == list(raw.events[1::2])
    for prepared, terminal in zip(raw.events[::2], committed.events, strict=True):
        assert prepared["event_type"] == TURN_OUTCOME_PREPARED_EVENT_TYPE
        assert terminal["event_type"] == TURN_OUTCOME_EVENT_TYPE
        assert prepared["seq"] + 1 == terminal["seq"]
        assert prepared["source"] == terminal["source"] == "roles.kernel.transaction"
        assert prepared["metadata"]["idempotency_key"] == terminal["metadata"]["idempotency_key"] + ":prepared"
        for identity in ("run_id", "task_id", "correlation_id", "provenance"):
            assert prepared["metadata"][identity] == terminal["metadata"][identity]
        preparation = prepared["payload"]
        payload = terminal["payload"]
        assert preparation["schema_version"] == "roles.kernel.prepared_turn_outcome.v1"
        assert preparation["provenance"] == payload["provenance"]
        assert payload["schema_version"] == "roles.kernel.turn_outcome_fact.v2"
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        assert preparation["payload_ref"]["sha256"] == sha256(encoded).hexdigest()
        assert preparation["payload_ref"]["byte_size"] == len(encoded)
        assert preparation["payload_ref"]["payload_schema"] == payload["schema_version"]
        assert preparation["payload_ref"]["storage_identity_token"] == payload["storage_identity"]["token"]
        outcome_bytes = json.dumps(
            payload["outcome"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        assert payload["outcome_hash"] == sha256(outcome_bytes).hexdigest()
    return list(committed.events)


def test_sessionless_ce_outcomes_allow_strict_director_commit_without_payload_loss(tmp_path: Path) -> None:
    _commit_ce_outcomes(tmp_path)
    before = query_fact_events(
        QueryFactEventsV1(workspace=str(tmp_path), stream=TURN_OUTCOME_STREAM, strict_integrity=True)
    ).events
    preserved_raw_events = deepcopy(list(before))
    preserved_payloads = deepcopy(
        [event["payload"] for event in before if event["event_type"] == TURN_OUTCOME_EVENT_TYPE]
    )
    turn_id = "director-denied-reads"
    invocations = [
        ToolInvocation(
            call_id=ToolCallId("tree-call"),
            tool_name="repo_tree",
            arguments={"path": ".", "depth": 3},
            effect_type=ToolEffectType.READ,
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        ),
        ToolInvocation(
            call_id=ToolCallId("read-call"),
            tool_name="read_file",
            arguments={"file": "input.txt"},
            effect_type=ToolEffectType.READ,
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        ),
    ]
    ledger = TurnLedger(turn_id=turn_id)
    ledger.record_decision(
        TurnDecision(
            turn_id=TurnId(turn_id),
            kind=TurnDecisionKind.TOOL_BATCH,
            visible_message="",
            tool_batch=ToolBatch(
                batch_id=BatchId("denied-batch"), invocations=invocations, parallel_readonly=invocations
            ),
            finalize_mode=FinalizeMode.NONE,
            domain="code",
        )
    )
    denied_results = [
        {
            "call_id": invocation.call_id,
            "tool_name": invocation.tool_name,
            "status": "error",
            "result": None,
            "error": "路径不在 committed read scope 内",
            "execution_time_ms": 0,
            "effect_receipt": None,
            "effect_receipt_commit": None,
            "directed_effect_mutation_status": None,
            "directed_effect_claim_status": None,
        }
        for invocation in invocations
    ]
    batch = {
        "batch_id": "denied-batch",
        "turn_id": turn_id,
        "results": denied_results,
        "raw_results": deepcopy(denied_results),
        "success_count": 0,
        "failure_count": 2,
        "pending_async_count": 0,
        "has_pending_async": False,
        "effect_receipts": [],
    }
    original_batch = deepcopy(batch)
    committed = commit_turn_result(
        workspace=str(tmp_path),
        run_id="director-run",
        task_id="TASK-1",
        transition_id="director-read-turn",
        ledger=ledger,
        result={"kind": "tool_batch_with_receipt", "batch_receipt": batch},
        completion_status="success",
        session_id="director-session",
        source_state_before={"answer.py": "b" * 64},
        source_state_after={"answer.py": "b" * 64},
    )
    events = _assert_legacy_prepare_commit_pairs(tmp_path, outcome_count=3)
    raw_after = query_fact_events(
        QueryFactEventsV1(workspace=str(tmp_path), stream=TURN_OUTCOME_STREAM, strict_integrity=True)
    )
    assert list(raw_after.events[: len(preserved_raw_events)]) == preserved_raw_events
    assert [event["payload"] for event in events[:2]] == preserved_payloads
    assert preserved_payloads[0]["outcome"]["failure_reason"] == "schema mismatch: 字段无效"
    assert preserved_payloads[1]["outcome"]["decision"]["metadata"]["repair_evidence"]["accepted"] is True
    payload = events[2]["payload"]
    assert payload["outcome"]["execution"]["receipt"] == original_batch
    assert payload["outcome"]["execution"]["side_effect_class"] == "readonly"
    assert payload["session_binding"] == {
        "session_id": "director-session",
        "source_state_before": {"answer.py": "b" * 64},
        "source_state_after": {"answer.py": "b" * 64},
    }
    assert payload["provenance"]["task_id"] == "TASK-1"
    assert payload["outcome_hash"] == committed.receipt.outcome_hash
    assert events[2]["seq"] == committed.receipt.fact_event_seq
    assert events[2]["event_id"] == committed.receipt.fact_event_id
    assert batch == original_batch


@pytest.mark.asyncio
async def test_real_director_runtime_continues_denied_read_to_write_and_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only deterministic provider response changes; tools and receipts remain real."""
    from polaris.cells.roles.kernel.tests.test_director_persistent_execution_http import (
        test_canonical_director_session_reads_writes_repairs_and_verifies_over_http as execute_fixture,
    )
    from polaris.infrastructure.llm.providers.anthropic_provider import AnthropicProvider

    _commit_ce_outcomes(tmp_path)
    (tmp_path / "private.txt").write_text("PRIVATE_READ_MUST_REMAIN_DENIED", encoding="utf-8")
    original_invoke = AnthropicProvider.invoke
    call_count = 0

    def invoke_denied_first_read(self: AnthropicProvider, *args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        response = original_invoke(self, *args, **kwargs)
        if call_count == 0:
            assert response.ok
            response.raw["content"][0]["input"] = {"file": "private.txt"}
        call_count += 1
        return response

    monkeypatch.setattr(AnthropicProvider, "invoke", invoke_denied_first_read)
    await execute_fixture(tmp_path, monkeypatch, "repair")
    assert call_count == 6
    assert (tmp_path / "private.txt").read_text(encoding="utf-8") == "PRIVATE_READ_MUST_REMAIN_DENIED"
    requests = (tmp_path / "http-requests.json").read_text(encoding="utf-8")
    assert "PRIVATE_READ_MUST_REMAIN_DENIED" not in requests
    assert "committed read scope" in requests
    assert "VERIFIED_42" in requests
    events = _assert_legacy_prepare_commit_pairs(tmp_path, outcome_count=8)
    denied = events[2]["payload"]["outcome"]["execution"]["receipt"]
    assert denied["failure_count"] == 1
    assert denied["results"][0]["status"] == "error"
    assert denied["results"][0]["tool_name"] == "read_file"
    assert denied["effect_receipts"] == []
    assert events[-1]["payload"]["outcome"]["outcome_status"] == "completed"
