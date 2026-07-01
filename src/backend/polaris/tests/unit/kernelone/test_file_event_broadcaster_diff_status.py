import asyncio
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.events.file_event_broadcaster import (
    broadcast_file_written,
    configure_file_edit_event_publisher,
)


class _FakeMessageBus:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def broadcast(self, _message_type: Any, _source: str, payload: dict[str, Any]) -> None:
        self.payloads.append(payload)


class _FakePublisher:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def publish(self, *, subject: str, payload: dict[str, Any]) -> bool:
        self.requests.append({"subject": subject, "payload": payload})
        return True


@pytest.fixture(autouse=True)
def _clear_file_edit_publisher() -> Any:
    configure_file_edit_event_publisher(None)
    yield
    configure_file_edit_event_publisher(None)


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


def test_broadcast_file_written_uses_configured_publisher(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("KERNELONE_JETSTREAM_PUBLISH", "1")
    publisher = _FakePublisher()
    configure_file_edit_event_publisher(publisher)

    ok = broadcast_file_written(
        "src/app.ts",
        "create",
        3,
        patch="new",
        event_log_workspace=str(tmp_path),
    )

    assert ok is True
    assert len(publisher.requests) == 1
    request = publisher.requests[0]
    assert request["subject"].startswith("hp.runtime.")
    assert request["subject"].endswith(".event.file_edit")
    assert request["payload"]["payload"]["file_path"] == "src/app.ts"


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
