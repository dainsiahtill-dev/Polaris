"""Real controller recovery after a real private mutation and lost final append."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.events.fact_stream.public import (
    FactStreamError,
    QueryPayloadFactEventsV1,
    query_payload_fact_events,
)
from polaris.cells.roles.kernel.internal.transaction import outcome_commit as owner
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig, TurnLedger
from polaris.cells.roles.kernel.internal.turn_transaction_controller import TurnTransactionController
from polaris.cells.roles.kernel.public.turn_contracts import FinalizeMode, TurnDecision, TurnDecisionKind, TurnId
from polaris.cells.roles.kernel.public.turn_events import CompletionEvent
from polaris.cells.roles.kernel.tests.test_directed_effect_lifecycle import _setup_attempt
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor


def _controller_fixture(tmp_path: Path) -> tuple[TurnTransactionController, dict[str, Any], KernelFileSystem]:
    attempt = _setup_attempt(str(tmp_path))
    fs = KernelFileSystem(str(tmp_path), get_default_adapter())
    fs.workspace_write_text("source.txt", "before\n", encoding="utf-8")
    state: dict[str, Any] = {"provider_calls": 0, "tool_calls": 0, "attempt": attempt}
    executor = AgentAccelToolExecutor(
        str(tmp_path), allowed_tools=frozenset({"write_file"}), capability_scope=("source.txt",)
    )

    async def provider(request: dict[str, Any]) -> dict[str, Any]:
        state["provider_calls"] += 1
        if not request.get("tools"):
            return {"content": "Source is durably written.", "tool_calls": [], "model": "private-provider"}
        return {
            "content": "Write source.txt with the specified content.",
            "tool_calls": [
                {
                    "id": "actual-private-write",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({"file": "source.txt", "content": "after\n"}),
                    },
                }
            ],
            "model": "private-provider",
        }

    async def tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert tool_name == "write_file"
        state["tool_calls"] += 1
        return executor.execute(tool_name, arguments)

    config = TransactionConfig(
        role_id="director",
        run_id=attempt.run_id,
        task_id=attempt.external_task_id,
        transition_id="owned-controller-transition",
        workspace=str(tmp_path),
        session_id=attempt.session_id,
        source_targets=("source.txt",),
        durable_commit_required=True,
        domain="code",
        slm_enabled=False,
        intent_embedding_enabled=False,
    )
    controller = TurnTransactionController(llm_provider=provider, tool_runtime=tool, config=config)
    return controller, state, fs


def _fail_final(monkeypatch: pytest.MonkeyPatch, *, once: bool) -> dict[str, int]:
    actual = owner.append_payload_fact_event
    state = {"final_calls": 0}

    def append(command: Any) -> Any:
        if command.event_type == owner.TURN_OUTCOME_EVENT_TYPE:
            state["final_calls"] += 1
            if not once or state["final_calls"] == 1:
                raise FactStreamError("private final append failure", code="private_append_failed")
        return actual(command)

    monkeypatch.setattr(owner, "append_payload_fact_event", append)
    return state


_CONTEXT = [{"role": "user", "content": "Write source.txt with exactly after and a newline."}]
_TOOLS = [{"name": "write_file", "description": "Write the explicitly requested file"}]
_TURN = "controller-owned-write-turn"


@pytest.mark.asyncio
async def test_live_controller_completes_same_prepared_outcome_without_second_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, state, fs = _controller_fixture(tmp_path)
    append = _fail_final(monkeypatch, once=True)
    result = await controller.execute(_TURN, _CONTEXT, _TOOLS)
    assert state["tool_calls"] == 1
    assert fs.workspace_read_text("source.txt", encoding="utf-8") == "after\n"
    assert append["final_calls"] == 2
    assert result["commit_receipt"]["fact_event_seq"] == 2
    assert result["turn_outcome"]["execution"]["receipt"]["effect_receipts"][0]["file"] == "source.txt"
    rows = query_payload_fact_events(
        QueryPayloadFactEventsV1(str(tmp_path), owner.TURN_OUTCOME_PAYLOAD_STREAM, limit=8)
    ).events
    assert [r["event_type"] for r in rows] == ["turn_outcome_prepared", owner.TURN_OUTCOME_EVENT_TYPE]
    payload = rows[-1]["expanded_payload"]
    assert (
        payload["session_binding"]["source_state_before"]["source.txt"]
        != payload["session_binding"]["source_state_after"]["source.txt"]
    )


_REENTER = r"""
import asyncio,json,sys
from polaris.cells.roles.kernel.internal.turn_transaction_controller import TurnTransactionController
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
from polaris.cells.roles.kernel.public.turn_events import CompletionEvent
data=json.loads(sys.argv[1]);calls={"provider":0,"tool":0}
async def provider(request):
    calls["provider"]+=1
    raise AssertionError("prepared recovery must not invoke provider")
async def tool(name,args):
    calls["tool"]+=1
    raise AssertionError("prepared recovery must not dispatch tools")
config=TransactionConfig(role_id="director",run_id=data["run_id"],task_id=data["task_id"],workspace=data["workspace"],session_id=data["session_id"],transition_id="owned-controller-transition",source_targets=("source.txt",),durable_commit_required=True,domain="code",slm_enabled=False,intent_embedding_enabled=False)
controller=TurnTransactionController(llm_provider=provider,tool_runtime=tool,config=config)
async def run():
    if data["stream"]:
        events=[e async for e in controller.execute_stream("controller-owned-write-turn",[],[],turn_request_id="recovery-request")]
        assert len(events)==1 and isinstance(events[0],CompletionEvent)
        result={"commit_receipt":events[0].commit_receipt,"turn_outcome":events[0].turn_outcome,"visible_content":events[0].visible_content,"status":events[0].status}
    else:
        result=await controller.execute("controller-owned-write-turn",[],[])
    print(json.dumps({"calls":calls,"receipt":result["commit_receipt"],"outcome":result["turn_outcome"],"visible":result["visible_content"]},ensure_ascii=False,default=str))
asyncio.run(run())
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_new_process_reentry_replays_real_prepared_mutation_with_zero_provider_and_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stream: bool,
) -> None:
    controller, state, fs = _controller_fixture(tmp_path)
    append = _fail_final(monkeypatch, once=False)
    with pytest.raises(owner.TurnOutcomeCommitError):
        await controller.execute(_TURN, _CONTEXT, _TOOLS)
    assert state["tool_calls"] == 1
    assert fs.workspace_read_text("source.txt", encoding="utf-8") == "after\n"
    assert append["final_calls"] == 2  # One failed live append + one bounded failed replay.
    prepared = query_payload_fact_events(
        QueryPayloadFactEventsV1(str(tmp_path), owner.TURN_OUTCOME_PAYLOAD_STREAM, limit=8)
    ).events
    assert [r["event_type"] for r in prepared] == ["turn_outcome_prepared"]
    request = {
        "workspace": str(tmp_path),
        "run_id": state["attempt"].run_id,
        "task_id": state["attempt"].external_task_id,
        "session_id": state["attempt"].session_id,
        "stream": stream,
    }
    child = subprocess.run(
        [sys.executable, "-c", _REENTER, json.dumps(request)],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[5])},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert child.returncode == 0, child.stderr
    recovered = json.loads(child.stdout.strip().splitlines()[-1])
    assert recovered["calls"] == {"provider": 0, "tool": 0}
    assert recovered["receipt"]["fact_event_seq"] == 2
    assert recovered["outcome"]["execution"]["receipt"]["effect_receipts"][0]["file"] == "source.txt"
    assert fs.workspace_read_text("source.txt", encoding="utf-8") == "after\n"
    replay = subprocess.run(
        [sys.executable, "-c", _REENTER, json.dumps(request)],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[5])},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert replay.returncode == 0, replay.stderr
    second = json.loads(replay.stdout.strip().splitlines()[-1])
    assert second["calls"] == {"provider": 0, "tool": 0}
    assert second["receipt"] == recovered["receipt"]
    rows = query_payload_fact_events(
        QueryPayloadFactEventsV1(str(tmp_path), owner.TURN_OUTCOME_PAYLOAD_STREAM, limit=8)
    ).events
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_prepared_reentry_rejects_foreign_session_without_provider_or_tool_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, state, fs = _controller_fixture(tmp_path)
    _fail_final(monkeypatch, once=False)
    with pytest.raises(owner.TurnOutcomeCommitError):
        await controller.execute(_TURN, _CONTEXT, _TOOLS)
    before = (state["provider_calls"], state["tool_calls"])
    foreign = TurnTransactionController(
        llm_provider=controller._llm_provider,
        tool_runtime=controller.tool_runtime,
        config=replace(controller.config, session_id="foreign-session"),
    )
    with pytest.raises(owner.TurnOutcomeCommitError, match="session"):
        await foreign.execute(_TURN, [], [])
    assert (state["provider_calls"], state["tool_calls"]) == before
    rows = query_payload_fact_events(
        QueryPayloadFactEventsV1(str(tmp_path), owner.TURN_OUTCOME_PAYLOAD_STREAM, limit=8)
    ).events
    assert [row["event_type"] for row in rows] == ["turn_outcome_prepared"]
    assert fs.workspace_read_text("source.txt", encoding="utf-8") == "after\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,status", [("tool_failure", "failed"), ("ask_user", "handoff")])
async def test_recovered_stream_preserves_original_failure_or_handoff_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    status: str,
) -> None:
    controller, state, _fs = _controller_fixture(tmp_path)
    ledger = TurnLedger(turn_id=_TURN)
    decision = TurnDecision(
        turn_id=TurnId(_TURN),
        kind=TurnDecisionKind.FINAL_ANSWER if status == "failed" else TurnDecisionKind.ASK_USER,
        visible_message="original terminal message",
        finalize_mode=FinalizeMode.NONE,
        domain="code",
    )
    ledger.record_decision(decision)
    actual = owner.append_payload_fact_event
    _fail_final(monkeypatch, once=False)
    with pytest.raises(owner.TurnOutcomeCommitError):
        owner.commit_turn_result(
            workspace=str(tmp_path),
            run_id=controller.config.run_id,
            task_id=controller.config.task_id,
            transition_id=controller.config.transition_id,
            ledger=ledger,
            result={"kind": kind, "visible_content": "original terminal message"},
            completion_status=status,
            session_id=controller.config.session_id,
        )
    monkeypatch.setattr(owner, "append_payload_fact_event", actual)
    events = [e async for e in controller.execute_stream(_TURN, [], [])]
    assert len(events) == 1 and isinstance(events[0], CompletionEvent)
    assert events[0].status == status
    assert events[0].turn_kind == kind
    assert events[0].visible_content == "original terminal message"
    assert events[0].turn_outcome["outcome_status"] in {"failed", "handed_off"}
    assert state["provider_calls"] == state["tool_calls"] == 0
