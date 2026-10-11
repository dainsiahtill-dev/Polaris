"""Native finish reasons survive successful invoker projection, never raw secrets."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal import events
from polaris.cells.roles.kernel.internal.llm_caller.event_emitter import LLMEventEmitter
from polaris.cells.roles.kernel.internal.llm_caller.invoker import LLMInvoker
from polaris.cells.roles.kernel.tests.test_llm_invoker_decomposition_characterization import (
    _ctx,
    _patch_prepare,
    _prepared,
    _profile,
    _ScriptedExecutor,
)
from polaris.kernelone.llm.engine.contracts import AIResponse, Usage


@pytest.mark.parametrize(
    "native,expected",
    [
        ({"stop_reason": "end_turn"}, "end_turn"),
        ({"stop_reason": "max_tokens"}, "max_tokens"),
        ({"stop_reason": "stop_sequence"}, "stop_sequence"),
        ({"choices": [{"finish_reason": "stop"}]}, "stop"),
        ({"choices": [{"finish_reason": "length"}]}, "length"),
        ({"choices": [{"finish_reason": " STOP "}]}, "stop"),
        ({}, "unknown"),
        ({"stop_reason": None}, "unknown"),
        ({"stop_reason": ""}, "unknown"),
        ({"stop_reason": "   "}, "unknown"),
        ({"stop_reason": {"token": "RAW_SECRET"}}, "unknown"),
        ({"stop_reason": ["max_tokens"]}, "unknown"),
        ({"stop_reason": 42}, "unknown"),
        ({"choices": [{"finish_reason": []}]}, "unknown"),
        ({"stop_reason": "x" * 1000}, "unknown"),
        ({"stop_reason": '{"key":"RAW_SECRET"}'}, "unknown"),
    ],
)
async def test_success_native_reason_matches_return_and_real_emitter_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: dict[str, Any], expected: str
) -> None:
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "kernel-home"))
    canonical: list[dict[str, Any]] = []
    lifecycle: list[dict[str, Any]] = []

    def deliver(**kwargs: Any) -> None:
        canonical.append(kwargs)

    def publish(_self: LLMEventEmitter, **kwargs: Any) -> None:
        lifecycle.append(kwargs)

    # Keep invoker and real event-emitter projection/dedup; replace delivery only.
    monkeypatch.setattr(events, "emit_llm_event", deliver)
    monkeypatch.setattr(LLMEventEmitter, "publish_uep_lifecycle_event", publish)
    profile = _profile(role_id="pm")
    prepared = _prepared(profile)
    prepared.request_options["max_tokens"] = 4000
    prepared.ai_request.options["max_tokens"] = 4000
    _patch_prepare(monkeypatch, prepared)
    raw = {**native, "headers": {"Authorization": "RAW_SECRET"}, "api_key": "RAW_SECRET"}
    executor = _ScriptedExecutor(
        [
            AIResponse.success(
                output='{"tasks": []}', raw=raw, usage=Usage(prompt_tokens=17, completion_tokens=9, total_tokens=26)
            )
        ]
    )
    invoker = LLMInvoker(workspace=str(tmp_path / "workspace"), enable_cache=False, executor=executor)
    response = await invoker.call(profile=profile, system_prompt="PM", context=_ctx(None), run_id="pm-stop-reason")
    await asyncio.sleep(0)
    assert response.error is None
    assert response.content == '{"tasks": []}'
    assert response.metadata["finish_reason"] == expected
    ended = [event for event in canonical if event["event_type"] == events.LLMEventType.CALL_END]
    assert len(ended) == 1
    assert ended[0]["metadata"]["finish_reason"] == expected
    lifecycle_end = [event for event in lifecycle if event["event_type"] == "call_end"]
    assert len(lifecycle_end) == 1
    assert lifecycle_end[0]["metadata"]["finish_reason"] == expected
    assert len(executor.requests) == 1
    assert executor.requests[0].options["max_tokens"] == 4000
    assert not [
        event
        for event in canonical
        if event["event_type"] in {events.LLMEventType.CALL_RETRY, events.LLMEventType.CALL_ERROR}
    ]
    assert response.metadata["usage"]["completion_tokens"] == 9
    serialized = json.dumps({"metadata": response.metadata, "events": canonical, "lifecycle": lifecycle}, default=str)
    assert "RAW_SECRET" not in serialized
    assert "Authorization" not in serialized
    assert "api_key" not in serialized
    assert '"headers"' not in serialized
    (tmp_path / "success-stop-reason-evidence.json").write_text(serialized + "\n", encoding="utf-8")
