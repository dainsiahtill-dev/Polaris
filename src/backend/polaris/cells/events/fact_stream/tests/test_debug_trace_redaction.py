from __future__ import annotations

import json
from typing import Any

from polaris.cells.events.fact_stream.internal import debug_trace


def test_debug_trace_redacts_llm_request_payloads() -> None:
    payload = {
        "model": "qwen3.6",
        "messages": [{"role": "user", "content": "secret debug prompt"}],
        "temperature": 0.2,
    }

    preview = debug_trace._to_preview(payload, key_hint="json")
    serialized = json.dumps(preview, ensure_ascii=False)

    assert isinstance(preview, dict)
    assert preview["model"] == "qwen3.6"
    assert preview["temperature"] == 0.2
    assert preview["messages"] == {"redacted": True, "type": "list", "count": 1}
    assert "secret debug prompt" not in serialized


def test_debug_trace_redacts_llm_response_body_preview() -> None:
    raw_body = '{"choices":[{"message":{"content":"secret debug answer"}}]}'

    preview: Any = debug_trace._to_preview(raw_body, key_hint="body_preview")
    serialized = json.dumps(preview, ensure_ascii=False)

    assert preview == {"redacted": True, "type": "str", "chars": len(raw_body)}
    assert "secret debug answer" not in serialized
