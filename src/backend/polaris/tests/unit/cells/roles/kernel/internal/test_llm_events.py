from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from polaris.cells.roles.kernel.internal import events


def test_emit_llm_event_to_disk_redacts_prompt_payloads(monkeypatch: Any, tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"

    def _fake_roots(_workspace: str) -> SimpleNamespace:
        return SimpleNamespace(runtime_root=str(runtime_root))

    monkeypatch.setattr("polaris.cells.storage.layout.resolve_polaris_roots", _fake_roots)

    event = events.LLMCallEvent(
        event_type=events.LLMEventType.CALL_END,
        role="director",
        run_id="run-1",
        model="qwen3.6-27b-gpu1",
        prompt_tokens=123,
        completion_tokens=45,
        metadata={
            "workspace": str(tmp_path),
            "call_id": "call-1",
            "prompt_fingerprint": "abc123",
            "messages": [
                {"role": "system", "content": "secret system prompt"},
                {"role": "user", "content": "secret user request"},
            ],
            "response_content": "secret assistant answer",
            "nested": {"content": "secret nested content", "safe_count": 2},
        },
    )

    events._emit_llm_event_to_disk(event)

    event_path = runtime_root / "events" / "director.llm.events.jsonl"
    payload = json.loads(event_path.read_text(encoding="utf-8").strip())
    data = payload["data"]
    metadata = data["metadata"]

    assert data["model"] == "qwen3.6-27b-gpu1"
    assert data["prompt_tokens"] == 123
    assert metadata["call_id"] == "call-1"
    assert metadata["prompt_fingerprint"] == "abc123"
    assert metadata["messages"] == {"redacted": True, "type": "list", "count": 2}
    assert metadata["response_content"] == {"redacted": True, "type": "str", "chars": 23}
    assert metadata["nested"]["content"] == {"redacted": True, "type": "str", "chars": 21}
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "secret system prompt" not in serialized
    assert "secret assistant answer" not in serialized
    assert "secret nested content" not in serialized


def test_emit_llm_event_to_disk_preserves_final_request_context_audit(monkeypatch: Any, tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"

    def _fake_roots(_workspace: str) -> SimpleNamespace:
        return SimpleNamespace(runtime_root=str(runtime_root))

    monkeypatch.setattr("polaris.cells.storage.layout.resolve_polaris_roots", _fake_roots)

    audit = {
        "schema_version": "llm.final_request_context_audit.v1",
        "message_count": 3,
        "message_token_estimate": 2048,
        "tool_schema_count": 7,
        "tool_schema_token_estimate": 512,
        "final_request_token_estimate": 2560,
        "context_window_tokens": 32768,
        "context_underutilized": True,
        "coverage": {
            "has_pm_contract": True,
            "has_chief_engineer_blueprint": True,
            "has_target_files": True,
            "has_failure_feedback": True,
        },
    }
    event = events.LLMCallEvent(
        event_type=events.LLMEventType.CALL_START,
        role="director",
        run_id="run-context-audit",
        model="qwen3.6-27b-gpu1",
        metadata={
            "workspace": str(tmp_path),
            "call_id": "call-context-audit",
            "messages": [{"role": "user", "content": "secret prompt"}],
            "final_request_context_audit": audit,
            "context_tokens_after": 2560,
            "contextTokens": 2560,
        },
    )

    events._emit_llm_event_to_disk(event)

    event_path = runtime_root / "events" / "director.llm.events.jsonl"
    payload = json.loads(event_path.read_text(encoding="utf-8").strip())
    metadata = payload["data"]["metadata"]

    assert metadata["messages"] == {"redacted": True, "type": "list", "count": 1}
    assert metadata["final_request_context_audit"] == audit
    assert metadata["context_tokens_after"] == 2560
    assert metadata["contextTokens"] == 2560


def test_publish_to_realtime_bridge_redacts_prompt_payloads(monkeypatch: Any, tmp_path: Path) -> None:
    captured: list[Any] = []
    monkeypatch.setenv("KERNELONE_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(events, "publish_llm_realtime_event", captured.append)

    event = events.LLMCallEvent(
        event_type=events.LLMEventType.CALL_END,
        role="director",
        run_id="run-rt",
        model="qwen3.6-27b-gpu1",
        metadata={
            "workspace": str(tmp_path),
            "call_id": "call-rt",
            "messages": [{"role": "user", "content": "secret realtime prompt"}],
            "response_content": "secret realtime answer",
        },
    )

    events._publish_to_realtime_bridge(event)

    assert len(captured) == 1
    data = captured[0].data
    serialized = json.dumps(data, ensure_ascii=False)
    assert data["metadata"]["call_id"] == "call-rt"
    assert data["metadata"]["messages"] == {"redacted": True, "type": "list", "count": 1}
    assert data["metadata"]["response_content"] == {"redacted": True, "type": "str", "chars": 22}
    assert "secret realtime prompt" not in serialized
    assert "secret realtime answer" not in serialized
