from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi import WebSocket
from polaris.delivery.ws.endpoints.models import is_websocket_disconnect_runtime_error
from polaris.delivery.ws.endpoints.websocket_loop import _runtime_event_affects_status, run_main_loop


class _DisconnectingWebSocket:
    async def receive_text(self) -> str:
        raise RuntimeError('WebSocket is not connected. Need to call "accept" first.')


async def _send_status(*, force: bool = False, last_sig: str = "") -> tuple[str, dict[str, Any]]:
    del force, last_sig
    return "status-signature", {"type": "status"}


async def _send_false() -> bool:
    return False


def test_starlette_disconnect_runtime_error_is_classified_as_disconnect() -> None:
    exc = RuntimeError('WebSocket is not connected. Need to call "accept" first.')
    assert is_websocket_disconnect_runtime_error(exc) is True


def test_asgi_send_after_close_runtime_error_is_classified_as_disconnect() -> None:
    exc = RuntimeError("Unexpected ASGI message 'websocket.send', after sending 'websocket.close'.")
    assert is_websocket_disconnect_runtime_error(exc) is True


def test_runtime_task_lifecycle_event_affects_status() -> None:
    assert _runtime_event_affects_status(
        {
            "channel": "runtime_events",
            "name": "director_task_started",
            "actor": "Director",
            "data": {"task_id": "TASK-2"},
        }
    )


def test_runtime_llm_token_event_does_not_affect_status() -> None:
    assert not _runtime_event_affects_status(
        {
            "channel": "llm",
            "kind": "content_chunk",
            "payload": {"content": "partial"},
        }
    )


@pytest.mark.asyncio
async def test_runtime_loop_treats_starlette_disconnect_race_as_normal_close() -> None:
    close_code, close_reason = await run_main_loop(
        websocket=cast(WebSocket, _DisconnectingWebSocket()),
        state=object(),
        resolved_workspace="C:/Temp/workspace",
        cache_root="C:/Temp/runtime",
        roles_filter=set(),
        connection_id="ws-test",
        client="127.0.0.1:50000",
        tail_lines=200,
        legacy_subscriptions=set(),
        v2_protocol=None,
        v2_consumer_manager=None,
        v2_client_id="",
        v2_channels=[],
        v2_cursor=0,
        canonical_journal_channels=set(),
        channel_states={},
        journal_state={"pos": 0},
        legacy_channel_states={},
        stream_signatures=set(),
        stream_signature_order=[],
        realtime_subscription=None,
        send_status_func=_send_status,
        send_all_snapshots_func=_send_false,
        send_incrementals_func=_send_false,
    )

    assert close_code == 1001
    assert close_reason.startswith("client_disconnect:")
