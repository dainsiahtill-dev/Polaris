from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.kernel.tests._final_provider_attempt_gate_helpers import (
    _bootstrap,
    _gate,
    _wire_request,
)


class _Response:
    def __init__(self, *, status_code: int = 200, text: str = "", headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = text
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": "ok"}}]}


class ClientResponseError(RuntimeError):
    """Retry-shaped aiohttp response error for physical stream tests."""


@pytest.mark.asyncio
async def test_async_stream_cleanup_system_exit_is_cancelled_and_preserves_both_errors(
    tmp_path: Path,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    events: list[str] = []

    class _ResponseContext:
        async def __aenter__(self) -> object:
            events.append("post_enter")
            return object()

        async def __aexit__(self, *_args: object) -> None:
            events.append("response_exit")
            raise SystemExit("cleanup aborted")

    async def _consume(_response: object) -> AsyncIterator[str]:
        if False:
            yield "unreachable"
        raise ValueError("consume failed")

    with pytest.raises(ValueError, match="consume failed"):
        async for _item in gate.dispatch_stream_async(
            wire_request=_wire_request(gate),
            open_stream=lambda _wire: _ResponseContext(),
            consume=_consume,
        ):
            pass

    assert events == ["post_enter", "response_exit"]
    terminal = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert len(terminal) == 1
    assert terminal[0]["payload"]["status"] == "cancelled"
    assert "ValueError: consume failed" in terminal[0]["payload"]["error"]
    assert "SystemExit: cleanup aborted" in terminal[0]["payload"]["error"]
