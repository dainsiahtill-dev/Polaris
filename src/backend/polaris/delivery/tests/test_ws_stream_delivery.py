from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Any, cast

from polaris.delivery.ws.endpoints.stream import emit_stream_line
from polaris.delivery.ws.endpoints.websocket_loop import _drain_realtime_log_events


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send_text(self, data: str) -> None:
        self.messages.append(json.loads(data))


class FakeRealtimeSubscription:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def consume_dropped(self) -> int:
        return 0


def test_emit_stream_line_routes_canonical_dialogue_source_to_dialogue_event() -> None:
    async def _run() -> dict[str, Any]:
        websocket = FakeWebSocket()
        sent = await emit_stream_line(
            cast(Any, websocket),
            "system",
            json.dumps(
                {
                    "channel": "system",
                    "domain": "system",
                    "source": "dialogue",
                    "message": "ignored canonical wrapper",
                    "raw": {
                        "event_id": "dialogue-1",
                        "speaker": "PM",
                        "type": "say",
                        "text": "hello from dialogue",
                    },
                },
                ensure_ascii=False,
            ),
            from_snapshot=False,
        )
        assert sent is True
        return websocket.messages[-1]

    payload = asyncio.run(_run())

    assert payload["type"] == "dialogue_event"
    assert payload["channel"] == "dialogue"
    assert payload["event"]["text"] == "hello from dialogue"


def test_realtime_fanout_still_pushes_llm_when_v2_protocol_is_active() -> None:
    async def _run() -> dict[str, Any]:
        websocket = FakeWebSocket()
        realtime_subscription = FakeRealtimeSubscription()
        sent, needs_resync = await _drain_realtime_log_events(
            websocket=cast(Any, websocket),
            connection_id="conn-1",
            client="test-client",
            resolved_workspace="C:/workspace",
            v2_protocol="runtime.v2",
            realtime_subscription=cast(Any, realtime_subscription),
            canonical_journal_channels={"llm"},
            stream_signatures=set(),
            stream_signature_order=deque(),
            first_event={
                "event_id": "llm-1",
                "channel": "llm",
                "domain": "llm",
                "message": "live llm",
                "raw": {"stream_event": "content_chunk", "content": "live llm"},
            },
        )
        assert sent is True
        assert needs_resync is False
        return websocket.messages[-1]

    payload = asyncio.run(_run())

    assert payload["type"] == "llm_stream"
    assert payload["channel"] == "llm"
    assert payload["event"]["message"] == "live llm"
