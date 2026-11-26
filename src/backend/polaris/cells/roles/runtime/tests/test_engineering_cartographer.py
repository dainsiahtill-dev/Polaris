from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal.engineering_cartographer import (
    CartographerPool,
    CartographerRuntime,
    NarrativeRejectedError,
    current_facts,
    deterministic_run_card,
    facts_for_run_card,
    validate_run_card_narrative,
)
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)


def _run() -> dict[str, object]:
    return {"verification": "failed", "workspace_revision": 7, "status": "running"}


def _events() -> list[dict[str, object]]:
    return [
        {
            "kind": "request_prepared",
            "payload": {"request": {"context": {"phase": "EDIT"}}},
        },
        {
            "kind": "verification_completed",
            "payload": {
                "verification": "failed",
                "checks": [{"content": json.dumps({"command_card": {"stdout_preview": "[FAIL] 交互：刹车无效"}})}],
            },
        },
    ]


def test_next_actions_carry_run_specific_facts_not_the_phase_template() -> None:
    """实测：三条确定性卡的 next_actions 逐字相同（阶段模板），零信息量，
    模型没有任何可照做的具体动作。有持久事实时必须给出 run 专属的一步。
    """

    card = deterministic_run_card(_run(), _events(), phase="")
    action = str(card["next_actions"][0]["action"])
    assert action != "读取相关源码与现有测试，定位实现与目标的差距"
    assert "刹车无效" in action


def test_next_actions_tell_the_model_to_verify_an_unverified_candidate() -> None:
    run = {"verification": "not_run", "workspace_revision": 3, "status": "running"}
    events = [
        {
            "kind": "tool_observed",
            "payload": {"tool": "write_file", "call_id": "call_1", "ok": True, "arguments": {"file": "src/a.ts"}},
        },
        {
            "kind": "tool_started",
            "payload": {"tool": "write_file", "call_id": "call_1", "arguments": {"file": "src/a.ts"}},
        },
    ]
    card = deterministic_run_card(run, events, phase="")
    assert "verify_project" in str(card["next_actions"][0]["action"])


def test_facts_read_phase_and_failure_signature() -> None:
    facts = current_facts(_run(), _events())
    assert facts["phase"] == "EDIT"
    assert "[FAIL] 交互：刹车无效" in facts["failure_signatures"]


def test_narrative_accepted_when_state_matches() -> None:
    facts = current_facts(_run(), _events())
    payload = {
        "schema_version": "run_card_narrative.v1",
        "phase": "EDIT",
        "intent": "修复交互",
        "completed": ["已确认接口"],
        "blocker": {"signature": "[FAIL] 交互：刹车无效", "occurrences": 1, "candidate_revisions": 1},
        "next_actions": [{"action": "检查控制绑定", "why": "定位", "expected": "车道变化"}],
        "evidence_refs": [],
        "uncertain": True,
        "reason": "证据有限",
    }
    validated = validate_run_card_narrative(payload, facts, allowed_refs=set())
    assert validated is not None and validated["phase"] == "EDIT"


def test_narrative_rejected_when_phase_outside_allowed_set() -> None:
    run = dict(_run(), verification="not_run")
    facts = current_facts(run, _events())
    payload = {
        "schema_version": "run_card_narrative.v1",
        "phase": "RETEST",
        "intent": "",
        "completed": [],
        "blocker": None,
        "next_actions": [],
        "evidence_refs": [],
        "uncertain": True,
        "reason": "",
    }
    with pytest.raises(NarrativeRejectedError):
        validate_run_card_narrative(payload, facts, allowed_refs=set())


def test_retest_phase_allowed_after_real_verification() -> None:
    facts = current_facts(_run(), _events())
    assert "RETEST" in facts["allowed_phases"]
    payload = {
        "schema_version": "run_card_narrative.v1",
        "phase": "RETEST",
        "intent": "",
        "completed": [],
        "blocker": None,
        "next_actions": [],
        "evidence_refs": [],
        "uncertain": True,
        "reason": "",
    }
    assert validate_run_card_narrative(payload, facts, allowed_refs=set()) is not None


def test_narrative_normalizes_model_drift() -> None:
    facts = current_facts(_run(), _events())
    payload = {
        "schema_version": "1.0",
        "phase": "EDIT",
        "intent": "修复",
        "completed": ["读取接口"],
        "blocker": "刹车无效",
        "next_actions": ["检查控制绑定"],
        "evidence_refs": [],
        "uncertain": "unknown",
        "reason": "证据有限",
    }
    validated = validate_run_card_narrative(payload, facts, allowed_refs=set())
    assert validated is not None
    assert validated["schema_version"] == "run_card_narrative.v1"
    assert validated["blocker"]["signature"] == "[FAIL] 交互：刹车无效"
    assert validated["next_actions"][0]["action"] == "检查控制绑定"
    assert validated["uncertain"] is True


def test_narrative_rejects_invented_blocker() -> None:
    facts = current_facts(_run(), _events())
    payload = {
        "schema_version": "run_card_narrative.v1",
        "phase": "EDIT",
        "intent": "",
        "completed": [],
        "blocker": {"signature": "虚构症状", "occurrences": 1, "candidate_revisions": 1},
        "next_actions": [],
        "evidence_refs": [],
        "uncertain": True,
        "reason": "",
    }
    with pytest.raises(NarrativeRejectedError):
        validate_run_card_narrative(payload, facts, allowed_refs=set())


def test_schedule_does_not_cancel_in_flight_draw() -> None:
    import asyncio

    from polaris.cells.roles.runtime.internal.engineering_cartographer import CartographerRuntime

    class _SlowPool:
        async def draw_many(self, requests):
            await asyncio.sleep(0.05)
            return ["drawing"]

    async def main() -> None:
        runtime = CartographerRuntime(_SlowPool())
        runtime.schedule({"a": 1}, fingerprint="one")
        runtime.schedule({"a": 2}, fingerprint="two")
        await asyncio.sleep(0.08)
        assert runtime.poll() == "drawing"

    asyncio.run(main())


def test_fingerprint_changes_with_stage() -> None:
    first, fingerprint_one = facts_for_run_card(_run(), _events())
    changed = dict(_run(), verification="passed")
    _, fingerprint_two = facts_for_run_card(changed, _events())
    assert fingerprint_one != fingerprint_two
    assert first["working_state"]["phase"] == "EDIT"


def _facts(verification: str = "failed") -> dict[str, Any]:
    return current_facts(dict(_run(), verification=verification), _events())


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "run_card_narrative.v1",
        "phase": "EDIT",
        "intent": "",
        "completed": [],
        "blocker": None,
        "next_actions": [],
        "evidence_refs": [],
        "uncertain": True,
        "reason": "",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("payload", "verification", "fragment"),
    [
        ("not json", "failed", "did not normalize to a JSON object"),
        (_payload(phase="NOPE"), "failed", "phase="),
        (_payload(phase="RETEST"), "not_run", "allowed_phases"),
        (_payload(completed=["done"] * 9), "failed", "completed has 9 items"),
        (_payload(next_actions=[{"action": "act"}] * 4), "failed", "next_actions has 4 items"),
        (_payload(blocker={"signature": "虚构症状"}), "failed", "blocker.signature="),
        (_payload(reason="x" * 161), "failed", "reason has 161 characters"),
    ],
)
def test_rejection_carries_auditable_detail(payload: object, verification: str, fragment: str) -> None:
    facts = _facts(verification)
    with pytest.raises(NarrativeRejectedError) as excinfo:
        validate_run_card_narrative(payload, facts, allowed_refs=set())
    assert excinfo.value.code
    assert excinfo.value.detail
    assert fragment in excinfo.value.detail


def test_repeated_rejection_is_recorded_once_per_fingerprint() -> None:
    async def main() -> None:
        class _Pool:
            async def draw_many(self, requests: list[dict[str, Any]]) -> list[str]:
                await asyncio.sleep(0)
                return ["{}"]

        runtime = CartographerRuntime(_Pool())
        runtime.schedule({"a": 1}, fingerprint="fp1")
        await asyncio.sleep(0.01)
        assert runtime.note_rejection("phase_not_allowed", "detail") is True
        assert runtime.note_rejection("phase_not_allowed", "detail") is False
        assert runtime.note_rejection("stale_working_state", "later revision") is False
        assert runtime.poll() is None
        runtime.schedule({"a": 2}, fingerprint="fp2")
        await asyncio.sleep(0.01)
        assert runtime.note_rejection("phase_not_allowed", "detail") is True

    asyncio.run(main())


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._data = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._data


def test_system_prompt_states_narrative_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request: urllib.request.Request, timeout: float | None = None) -> _FakeResponse:
        captured["request"] = request
        return _FakeResponse({"content": [{"type": "text", "text": "{}"}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    pool = CartographerPool(base_url="http://cartographer.test", api_key="key", model="model")
    pool._post({"working_state": {}})
    request = captured["request"]
    assert isinstance(request, urllib.request.Request)
    data = request.data
    assert isinstance(data, bytes)
    body = json.loads(data.decode("utf-8"))
    messages = body["messages"]
    assert isinstance(messages, list) and messages
    system = str(messages[0]["content"])
    assert "at most 8" in system
    assert "80 characters" in system
    assert "at most 3" in system
    assert "failure_signatures" in system
    assert "array" in system


def test_draw_many_retries_once_when_the_model_answers_in_prose() -> None:
    """One corrective ask keeps a prose answer from losing the whole draw."""

    pool = CartographerPool(base_url="https://cartographer.example", api_key="secret", model="small-model")
    calls = {"count": 0}

    def _post(messages: list[dict[str, str]]) -> str:
        calls["count"] += 1
        return "sure, here is the map" if calls["count"] == 1 else json.dumps({"phase": "DIAGNOSE"})

    pool._post = _post  # type: ignore[method-assign]
    results = asyncio.run(pool.draw_many([{"working_state": {"phase": "DIAGNOSE"}}]))
    assert calls["count"] == 2
    assert json.loads(results[0])["phase"] == "DIAGNOSE"


def test_decision_provider_retries_with_the_four_field_contract_and_preserves_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body_log: list[dict[str, Any]] = []
    first = '```json\n{"next_actions":[{"action":"Re-run write_file"},{"action":"Run tests"}]}\n```'
    second = '```json\n{"check_tool":"read_file","question":"Is the source already present?","expected":"The path is absent or readable.","evidence_ref":"event:2"}\n```'

    def post(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        assert request.data
        body_log.append(json.loads(request.data))
        return _FakeResponse({"content": [{"type": "text", "text": [first, second][len(body_log) - 1]}]})

    monkeypatch.setattr(urllib.request, "urlopen", post)
    pool = CartographerPool(base_url="http://cartographer.test", api_key="test", model="test")
    response = asyncio.run(
        pool.draw_many([{"decision": {"available_checks": ["read_file"], "evidence_refs": ["event:2"]}}])
    )
    assert response == [second] and pool.prior_raw_responses == [first]
    assert len(body_log) == 2 and all("completed" not in b["messages"][0]["content"] for b in body_log)
    assert all("check_tool" in b["messages"][0]["content"] and b["max_tokens"] < 1000 for b in body_log)


def test_thinking_only_response_is_not_a_draw_and_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def invisible(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        calls.append(request)
        return _FakeResponse(
            {"stop_reason": "max_tokens", "content": [{"type": "thinking", "thinking": '{"phase":"EDIT"}'}]}
        )

    monkeypatch.setattr(urllib.request, "urlopen", invisible)
    pool = CartographerPool(base_url="http://cartographer.test", api_key="test", model="test")
    assert asyncio.run(pool.draw_many([{"working_state": {}}])) == [""]
    assert len(calls) == 1 and "no_visible_answer" in pool.last_error and "max_tokens" in pool.last_error
    assert not pool.prior_raw_responses


def test_deterministic_run_card_is_always_available_and_fact_bound() -> None:
    """The narrative is rejected 94% of the time, so the stage card cannot depend on it."""

    events = [
        {
            "kind": "tool_started",
            "seq": 1,
            "payload": {"tool": "write_file", "ok": True, "arguments": {"file": "src/a.ts"}},
        },
        {
            "kind": "tool_observed",
            "seq": 2,
            "payload": {"tool": "write_file", "ok": True, "call_id": "c1", "arguments": {"file": "src/a.ts"}},
        },
        {
            "kind": "verification_completed",
            "seq": 3,
            "payload": {
                "verification": "failed",
                "checks": [{"content": "FAIL src/a.ts —— 断言失败：期望 1 实际 2", "ok": False}],
            },
        },
    ]
    card = deterministic_run_card(_run(), events, phase="")
    assert card["schema_version"] == "run_card_deterministic.v1"
    assert card["phase"] in {"DIAGNOSE", "EDIT", "RETEST"}
    assert card["uncertain"] is False
    assert any("src/a.ts" in item for item in card["completed"])
    assert any("failed" in item for item in card["completed"])
    assert card["blocker"] and card["blocker"]["signature"]
    assert card["blocker"]["occurrences"] >= 1
    assert card["next_actions"] and card["next_actions"][0]["action"]


def test_deterministic_run_card_reports_no_blocker_without_a_failure() -> None:
    events = [
        {
            "kind": "tool_started",
            "seq": 1,
            "payload": {"tool": "write_file", "ok": True, "arguments": {"file": "src/b.ts"}},
        },
        {
            "kind": "tool_observed",
            "seq": 2,
            "payload": {"tool": "write_file", "ok": True, "call_id": "c2", "arguments": {"file": "src/b.ts"}},
        },
    ]
    card = deterministic_run_card({"verification": "not_run", "workspace_revision": 1}, events, phase="EDIT")
    assert card["phase"] == "EDIT"
    assert card["blocker"] is None


async def test_host_run_card_is_published_without_a_cartographer(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stage card must not depend on the cartographer's 5.6% acceptance rate."""

    from polaris.cells.roles.runtime.internal.engineering_loop import run_engineering_loop
    from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
    from polaris.cells.roles.runtime.public.contracts.engineering_run import (
        PermissionScope,
        RoleRunCommand,
        RunLimits,
    )

    (isolated_workspace / "a.py").write_text("x = 1\n", encoding="utf-8")
    scope = PermissionScope((isolated_workspace,), (isolated_workspace,), frozenset({"read_file", "write_file"}), False)
    limits = RunLimits(time.time() + 30, 100_000, 16_000)
    command = RoleRunCommand("run-card", "director", "Fix the addition.", isolated_workspace)
    store = EngineeringSessionStore(isolated_workspace)
    store.create_run(command, scope=scope, limits=limits, engine_version="engineering_v2", request_key=None)
    scripted_provider.client("openai", isolated_workspace, monkeypatch)
    scripted_provider.enqueue_json(
        {"choices": [{"message": {"role": "assistant", "content": "Inspected the source."}, "finish_reason": "stop"}]}
    )
    result = await run_engineering_loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
    )
    assert result.status == "completed", result.text
    cards = [event for event in store.load_events(command.session_id) if event["kind"] == "run_card_active"]
    events = store.load_events(command.session_id)
    prepared = next(event for event in events if event["kind"] == "request_prepared")
    cutoff = prepared["payload"]["context_input_seq"]
    assert prepared["payload"]["turn_seq"] <= cutoff < cards[-1]["seq"] < prepared["seq"]
    assert cards, "the host must publish a stage card even when the cartographer is absent"
    payload = cards[-1]["payload"]
    assert payload["source"] == "host"
    assert payload["phase"] == prepared["payload"]["request"]["context"]["phase"] == "build"
    assert payload["card"]["completed"] or payload["card"]["next_actions"]


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("stale", [False, True])
@pytest.mark.parametrize("has_memory", [True, False])
async def test_cartographer_advice_sent_only_for_the_candidate_that_was_drawn(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    protocol: str,
    stale: bool,
    has_memory: bool,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from polaris.cells.roles.runtime.internal import engineering_cartographer
    from polaris.cells.roles.runtime.tests.test_engineering_loop import _native, _setup

    loop, store, _tools, command, scope, limits = _setup(isolated_workspace)
    run = store.load_run(command.session_id, workspace=isolated_workspace)
    store.append_event(
        command.session_id,
        role="director",
        kind="tool_observed",
        payload={"call_id": "read-A", "tool": "read_file", "ok": True},
    )
    if has_memory:
        store.append_event(
            command.session_id,
            role="director",
            kind="verification_completed",
            payload={
                "verification": "failed",
                "workspace_revision": 0,
                "checks": [{"call_id": "check-A", "ok": False, "exit_code": 1, "content": "Error: sum mismatch"}],
            },
        )
    draw, fingerprint = facts_for_run_card({**run, "execution_phase": "build"}, store.load_events(command.session_id))
    action = {
        "action": "Inspect the failing addition case",
        "why": "Distinguish parsing from summation",
        "expected": "Observe a reproducible input mismatch",
    }
    narrative = {
        "phase": "DIAGNOSE",
        "intent": "Locate the relevant expression",
        "completed": [],
        "next_actions": [action],
        "evidence_refs": ["read-A"],
        "uncertain": True,
        "reason": "",
    }
    raw = "```json\n" + json.dumps(narrative) + "\n```"
    if has_memory:
        question = "Which input reaches the addition expression?"
        action = {**action, "action": f"read_file: {question}"}
        draw["decision"] = {"available_checks": ["read_file"], "evidence_refs": ["read-A"]}
        raw = (
            "```json\n"
            + json.dumps(
                {
                    "check_tool": "read_file",
                    "question": question,
                    "expected": action["expected"],
                    "evidence_ref": "read-A",
                }
            )
            + "\n```"
        )
    pool = SimpleNamespace(draw_many=AsyncMock(return_value=[raw]), last_error="")
    runtime = CartographerRuntime(pool)
    assert runtime.schedule(draw, fingerprint=fingerprint)
    await asyncio.sleep(0)
    assert runtime.poll()
    if stale:
        store.update_run(command.session_id, workspace_revision=run["workspace_revision"] + 1)
    monkeypatch.setenv("KERNELONE_CONTEXTOS_CARTOGRAPHER", "1")
    monkeypatch.setattr(engineering_cartographer, "cartographer_pool_from_environment", lambda: pool)
    monkeypatch.setattr(engineering_cartographer, "CartographerRuntime", lambda _pool: runtime)
    scripted_provider.enqueue_json(_native(text="Observed.", protocol=protocol))
    result = await loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=scripted_provider.client(protocol, isolated_workspace, monkeypatch),
    )
    assert result.status == "completed"
    wire = json.dumps(scripted_provider.requests[0]["body"], ensure_ascii=False)
    events = store.load_events(command.session_id, kinds=("run_card_injected",))
    if stale:
        assert narrative["intent"] not in wire
        assert action["action"] not in wire
        assert bool(events) == has_memory
        if events:
            assert events[-1]["payload"]["source"] == "host"
        rejection = store.load_events(command.session_id, kinds=("cartographer_rejected",))[-1]["payload"]
        assert rejection["reason"] == "stale_working_state"
    else:
        assert "当前阶段：build" in wire and "当前阶段：DIAGNOSE" not in wire
        completed = store.load_events(command.session_id, kinds=("cartographer_completed",))[-1]["payload"]
        assert completed["raw_response"] == raw
        if has_memory:
            assert completed["schema_version"] == "cartographer.probe.v1"
            assert "phase" not in completed and "completed" not in completed
        for text in (action["action"], action["expected"]):
            assert text in wire
        body = scripted_provider.requests[0]["body"]
        tail = body["messages"][-1]
        assert tail["role"] == "user"
        assert action["action"] in json.dumps(tail["content"], ensure_ascii=False)
        system = [message for message in body["messages"] if message["role"] == "system"]
        assert action["action"] not in json.dumps([body.get("system"), system], ensure_ascii=False)
        assert wire.count(action["action"]) == 1
        tail_text = json.dumps(tail["content"], ensure_ascii=False)
        assert "制图师引用：read-A" in tail_text
        if not has_memory:
            assert "制图师标记：不确定" in tail_text
        if not has_memory:
            assert not store.load_events(command.session_id, kinds=("memory_card_injected",))
        event = events[-1]["payload"]
        assert event["source"] == "cartographer"
        assert event["next_actions"] == [action["action"]]
        assert event["advisory_next_actions"] == [{"action": action["action"], "expected": action["expected"]}]
        assert all(step in wire for step in event["host_next_actions"])


async def test_cartographer_rejects_evidence_that_arrived_after_the_draw() -> None:
    from unittest.mock import AsyncMock

    facts = current_facts(_run(), _events())
    request, fingerprint = facts_for_run_card(_run(), _events())
    runtime = CartographerRuntime(AsyncMock())
    assert runtime.schedule(request, fingerprint=fingerprint)
    await asyncio.sleep(0)
    facts["evidence_refs"].add("new-evidence")
    with pytest.raises(NarrativeRejectedError) as rejected:
        runtime.validate_current(
            {"phase": facts["phase"], "completed": [], "next_actions": [], "evidence_refs": ["new-evidence"]},
            facts,
        )
    assert rejected.value.code == "unbound_evidence_ref"


async def test_physical_director_requests_show_same_candidate_build_then_verify(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace
    from types import SimpleNamespace

    from polaris.cells.roles.runtime.internal import engineering_loop
    from polaris.cells.roles.runtime.public.contracts.engineering_run import ToolObservation
    from polaris.cells.roles.runtime.tests.test_engineering_loop import _ALL_TOOLS, _native, _setup

    monkeypatch.setenv("KERNELONE_ENGINEERING_FIRST_VERIFICATION_FRACTION", "0.5")
    loop, store, _, command, scope, limits = _setup(isolated_workspace, enabled=_ALL_TOOLS | {"verify_project"})
    (isolated_workspace / "a.py").write_text("x = 1\n", encoding="utf-8")
    created = store.load_run(command.session_id, workspace=isolated_workspace)["created_at"]
    limits = replace(limits, deadline=created + 100)
    monkeypatch.setattr(
        engineering_loop,
        "time",
        SimpleNamespace(time=lambda: created + (60 if scripted_provider.requests else 10), monotonic=time.monotonic),
    )
    store.append_event(
        command.session_id,
        role="director",
        kind="message",
        payload={"message": {"role": "assistant", "content": "我们决定保留既有接口。"}},
    )
    scripted_provider.enqueue_json(_native(("inspect", "read_file", {"file": "a.py"})))
    scripted_provider.enqueue_json(_native(text="Inspected."))

    async def verify(commands: tuple[str, ...], turn_seq: int, call_id: str) -> ToolObservation:
        raise AssertionError("the read-only provider script never dispatched verification")

    result = await loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
        verify=verify,
    )
    assert result.status == "completed", result.text
    assert store.load_run(command.session_id, workspace=isolated_workspace)["workspace_revision"] == 0
    bodies = [json.dumps(request["body"], ensure_ascii=False) for request in scripted_provider.requests]
    assert len(bodies) == 2 and "当前阶段：build" in bodies[0] and "当前阶段：verify" in bodies[1]
    assert all("当前阶段：DIAGNOSE" not in body for body in bodies)


async def test_director_offers_diagnostic_draw_synchronously_with_its_evidence(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    from polaris.cells.roles.runtime.internal import engineering_cartographer
    from polaris.cells.roles.runtime.tests.test_engineering_loop import _native, _setup

    loop, store, _, command, scope, limits = _setup(isolated_workspace)
    identity = {"turn_seq": 1, "call_id": "read-error", "tool": "read_file"}
    store.append_event(
        command.session_id,
        role="director",
        kind="tool_started",
        payload={**identity, "arguments": {"file": "schema.json"}},
    )
    store.append_event(
        command.session_id,
        role="director",
        kind="tool_observed",
        payload={**identity, "ok": False, "content": "ValueError: missing source"},
    )
    pool = AsyncMock()
    pool.draw_many.return_value = ["{}"]
    monkeypatch.setenv("KERNELONE_CONTEXTOS_CARTOGRAPHER", "1")
    monkeypatch.setattr(engineering_cartographer, "cartographer_pool_from_environment", lambda: pool)
    scripted_provider.enqueue_json(_native(text="Inspected."))
    result = await loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
    )
    assert result.status == "completed"
    receipts = store.load_events(command.session_id, kinds=("cartographer_scheduled",))
    assert len(receipts) == 1
    receipt = receipts[0]["payload"]
    assert receipt["turn_seq"] % 12 != 0 and receipt["decision_reason"] == "failed_observation"
    assert "read-error" in receipt["evidence_refs"] and receipt["decision_key"]


async def test_valid_abstention_records_a_single_neutral_draw_without_visible_advice(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from polaris.cells.roles.runtime.internal import engineering_cartographer
    from polaris.cells.roles.runtime.tests.test_engineering_loop import _native, _setup

    loop, store, _, command, scope, limits = _setup(isolated_workspace)
    run = store.load_run(command.session_id, workspace=isolated_workspace)
    (isolated_workspace / "fixture.py").write_text("value=1\n", encoding="utf-8")
    store.append_event(
        command.session_id,
        role="director",
        kind="tool_observed",
        payload={"tool": "read_file", "call_id": "read-A", "ok": True},
    )
    draw, fingerprint = facts_for_run_card({**run, "execution_phase": "build"}, store.load_events(command.session_id))
    draw["decision"] = {"evidence_refs": ["read-A"], "available_checks": ["read_file"]}
    raw = json.dumps(
        {"abstain": True, "reason": "The retained source already answers this question.", "evidence_ref": "read-A"}
    )
    pool = SimpleNamespace(draw_many=AsyncMock(return_value=[raw]), last_error="")
    runtime = CartographerRuntime(pool)
    assert runtime.schedule(draw, fingerprint=fingerprint)
    await asyncio.sleep(0)
    monkeypatch.setenv("KERNELONE_CONTEXTOS_CARTOGRAPHER", "1")
    monkeypatch.setattr(engineering_cartographer, "cartographer_pool_from_environment", lambda: pool)
    monkeypatch.setattr(engineering_cartographer, "CartographerRuntime", lambda _pool: runtime)
    scripted_provider.enqueue_json(_native(("read-fixture", "read_file", {"file": "fixture.py"})))
    scripted_provider.enqueue_json(_native(text="Inspected."))
    result = await loop(
        command,
        scope=scope,
        limits=limits,
        cancel=asyncio.Event(),
        store=store,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
    )
    assert result.status == "completed"
    neutral = store.load_events(command.session_id, kinds=("cartographer_abstained",))
    assert len(neutral) == 1 and neutral[0]["payload"]["raw_response"] == raw
    assert not store.load_events(command.session_id, kinds=("cartographer_completed", "cartographer_rejected"))
    assert not any(
        e["payload"].get("source") == "cartographer"
        for e in store.load_events(command.session_id, kinds=("run_card_injected",))
    )
    assert all("The retained source already answers" not in json.dumps(r["body"]) for r in scripted_provider.requests)
