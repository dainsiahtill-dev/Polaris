"""Regression tests for Director realtime file edit events."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.director.execution import DirectorPatchExecutor
from polaris.cells.roles.adapters.internal.director.execution_tools import DirectorToolExecutor
from polaris.kernelone.events.message_bus import MessageType


class RecordingMessageBus:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def broadcast(
        self,
        message_type: MessageType,
        sender: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.messages.append(
            {
                "message_type": message_type,
                "sender": sender,
                "payload": payload or {},
            }
        )


async def _drain_broadcast_tasks() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


def _progress_noop(*args: Any, **kwargs: Any) -> None:
    return None


@pytest.mark.asyncio
async def test_write_file_tool_broadcasts_file_written_event(tmp_path: Path) -> None:
    bus = RecordingMessageBus()
    executor = DirectorToolExecutor(str(tmp_path), message_bus=bus)

    result = executor.execute_tool(
        "write_file",
        {"file": "src/main.ts", "content": "export const value = 1;\n"},
        task_id="task-1",
    )

    await _drain_broadcast_tasks()

    assert result["ok"] is True
    assert result["file"] == "src/main.ts"
    assert result["operation"] == "create"
    assert result["broadcast_ok"] is True
    assert (tmp_path / "src" / "main.ts").read_text(encoding="utf-8") == "export const value = 1;\n"
    assert len(bus.messages) == 1
    message = bus.messages[0]
    assert message["message_type"] is MessageType.FILE_WRITTEN
    assert message["sender"] == "worker-director"
    assert message["payload"]["file_path"] == "src/main.ts"
    assert message["payload"]["operation"] == "create"
    assert message["payload"]["task_id"] == "task-1"
    assert message["payload"]["added_lines"] == 1


@pytest.mark.asyncio
async def test_edit_file_tool_broadcasts_modify_event(tmp_path: Path) -> None:
    target = tmp_path / "src" / "main.ts"
    target.parent.mkdir(parents=True)
    target.write_text("export const value = 1;\n", encoding="utf-8")
    bus = RecordingMessageBus()
    executor = DirectorToolExecutor(str(tmp_path), message_bus=bus)

    result = executor.execute_tool(
        "edit_file",
        {
            "file": "src/main.ts",
            "search": "export const value = 1;",
            "replace": "export const value = 2;",
        },
        task_id="task-2",
    )

    await _drain_broadcast_tasks()

    assert result["ok"] is True
    assert result["file"] == "src/main.ts"
    assert target.read_text(encoding="utf-8") == "export const value = 2;\n"
    assert len(bus.messages) == 1
    payload = bus.messages[0]["payload"]
    assert payload["file_path"] == "src/main.ts"
    assert payload["operation"] == "modify"
    assert payload["task_id"] == "task-2"
    assert "export const value = 2;" in payload["patch"]


@pytest.mark.asyncio
async def test_markdown_patch_file_broadcasts_create_event(tmp_path: Path) -> None:
    bus = RecordingMessageBus()
    executor = DirectorPatchExecutor(str(tmp_path))
    executor.set_message_bus(bus)
    response = "src/patch.ts\n```ts\nexport const patched = true;\n```"

    results = await executor.execute_tools(response, "task-3", _progress_noop)

    await _drain_broadcast_tasks()

    assert results == [
        {
            "tool": "patch_apply",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "write_file",
                "file": "src/patch.ts",
                "bytes_written": len(b"export const patched = true;"),
                "operation": "create",
                "broadcast_ok": True,
            },
        }
    ]
    assert (tmp_path / "src" / "patch.ts").read_text(encoding="utf-8") == "export const patched = true;"
    assert len(bus.messages) == 1
    payload = bus.messages[0]["payload"]
    assert payload["file_path"] == "src/patch.ts"
    assert payload["operation"] == "create"
    assert payload["task_id"] == "task-3"
