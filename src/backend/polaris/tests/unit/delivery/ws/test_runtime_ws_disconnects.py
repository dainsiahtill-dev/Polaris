from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi import WebSocket
from polaris.delivery.ws.endpoints.models import is_websocket_disconnect_runtime_error
from polaris.delivery.ws.endpoints.websocket_loop import _runtime_event_affects_status, run_main_loop


class _DisconnectingWebSocket:
    async def send_text(self, _data: str) -> None:
        return None

    async def receive_text(self) -> str:
        raise RuntimeError('WebSocket is not connected. Need to call "accept" first.')


async def _build_status() -> tuple[str, dict[str, Any]]:
    return "status-signature", {"type": "status"}


def test_starlette_disconnect_runtime_error_is_classified_as_disconnect() -> None:
    exc = RuntimeError('WebSocket is not connected. Need to call "accept" first.')
    assert is_websocket_disconnect_runtime_error(exc) is True


def test_asgi_send_after_close_runtime_error_is_classified_as_disconnect() -> None:
    exc = RuntimeError("Unexpected ASGI message 'websocket.send', after sending 'websocket.close'.")
    assert is_websocket_disconnect_runtime_error(exc) is True


@pytest.mark.parametrize("event_type", ["claimed", "completed", "failed", "cancelled"])
def test_canonical_task_runtime_lifecycle_event_affects_status(event_type: str) -> None:
    assert _runtime_event_affects_status(
        {
            "channel": "event.factory:factory-run-1",
            "kind": "task_runtime_execution",
            "payload": {"event_type": event_type, "task_id": "TASK-2"},
        }
    )


def test_canonical_factory_stage_completion_affects_status() -> None:
    assert _runtime_event_affects_status(
        {
            "channel": "event.factory:factory-run-1",
            "kind": "stage_completed",
            "payload": {"type": "stage_completed", "stage": "director"},
        }
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"channel": "status.instances", "kind": "snapshot.updated"},
        {"channel": "director", "kind": "task.updated", "payload": {"task_id": "TASK-2"}},
    ],
)
def test_canonical_status_or_role_event_affects_status(payload: dict[str, Any]) -> None:
    assert _runtime_event_affects_status(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "channel": "event.factory:factory-run-1",
            "kind": "content_chunk",
            "payload": {"status": "completed", "content": "token"},
        },
        {
            "channel": "event.factory:factory-run-1",
            "kind": "stage_heartbeat",
            "payload": {"type": "stage_heartbeat", "stage": "director"},
        },
        {
            "channel": "runtime_events",
            "name": "director_task_started",
            "actor": "Director",
            "data": {"task_id": "TASK-2"},
        },
    ],
)
def test_noncanonical_or_high_volume_event_does_not_affect_status(payload: dict[str, Any]) -> None:
    assert not _runtime_event_affects_status(payload)


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
        v2_protocol=None,
        v2_consumer_manager=None,
        v2_client_id="",
        v2_channels=[],
        v2_cursor=0,
        build_status_func=_build_status,
    )

    assert close_code == 1001
    assert close_reason.startswith("client_disconnect:")
