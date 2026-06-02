import asyncio
from typing import Any

import pytest
from polaris.kernelone.events.file_event_broadcaster import broadcast_file_written


class _FakeMessageBus:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def broadcast(self, _message_type: Any, _source: str, payload: dict[str, Any]) -> None:
        self.payloads.append(payload)


@pytest.mark.asyncio
async def test_broadcast_file_written_marks_empty_patch_as_no_content_change() -> None:
    bus = _FakeMessageBus()

    ok = broadcast_file_written(
        "src/app.ts",
        "modify",
        16,
        patch="",
        message_bus=bus,
        worker_id="test-worker",
    )
    await asyncio.sleep(0)

    assert ok is True
    assert len(bus.payloads) == 1
    payload = bus.payloads[0]
    assert payload["diff_status"] == "unavailable"
    assert payload["patch_unavailable_reason"] == "no_content_change"
    assert payload["has_patch"] is False
    assert "patch" not in payload


@pytest.mark.asyncio
async def test_broadcast_file_written_marks_available_patch() -> None:
    bus = _FakeMessageBus()

    ok = broadcast_file_written(
        "src/app.ts",
        "modify",
        16,
        patch="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new",
        message_bus=bus,
        worker_id="test-worker",
    )
    await asyncio.sleep(0)

    assert ok is True
    payload = bus.payloads[0]
    assert payload["diff_status"] == "available"
    assert payload["has_patch"] is True
    assert payload["patch"].endswith("+new")
