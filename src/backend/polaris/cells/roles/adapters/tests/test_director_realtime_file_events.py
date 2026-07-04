"""Regression tests for Director realtime file edit events."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public import FailureClassV1
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


@pytest.fixture(autouse=True)
def _disable_jetstream_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_JETSTREAM_PUBLISH", "0")


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


def test_write_file_tool_persists_file_edit_event_without_message_bus(tmp_path: Path) -> None:
    executor = DirectorToolExecutor(str(tmp_path))

    result = executor.execute_tool(
        "write_file",
        {"file": "src/main.ts", "content": "export const value = 1;\n"},
        task_id="task-persist-1",
    )

    assert result["ok"] is True
    assert result["broadcast_ok"] is False
    event_log = tmp_path / ".polaris" / "runtime" / "file-edits" / "events.jsonl"
    line = event_log.read_text(encoding="utf-8").strip()
    event = json.loads(line)
    payload = event["payload"]
    assert event["channel"] == "event.file_edit"
    assert payload["file_path"] == "src/main.ts"
    assert payload["operation"] == "create"
    assert payload["task_id"] == "task-persist-1"
    assert payload["has_patch"] is True
    assert "export const value = 1;" in payload["patch"]


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


def test_write_file_tool_blocks_agents_forbidden_path(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("禁止修改 src/generated/schema.ts\n", encoding="utf-8")
    executor = DirectorToolExecutor(str(tmp_path))

    result = executor.execute_tool(
        "write_file",
        {"file": "src/generated/schema.ts", "content": "export const schema = {};\n"},
        task_id="task-policy-1",
    )

    assert result["ok"] is False
    assert result["blocked"] is True
    assert result["error_type"] == "director_write_policy_denied"
    assert "AGENTS.md forbids writing src/generated/schema.ts" in result["error"]
    assert not (tmp_path / "src" / "generated" / "schema.ts").exists()
    assert result["director_policy"]["allowed"] is False


def test_write_file_tool_records_nested_package_manifest_diff(tmp_path: Path) -> None:
    target = tmp_path / "packages" / "web" / "package.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"scripts": {"test": "vitest run"}}, ensure_ascii=False), encoding="utf-8")
    executor = DirectorToolExecutor(str(tmp_path))

    result = executor.execute_tool(
        "write_file",
        {
            "file": "packages/web/package.json",
            "content": json.dumps({"scripts": {"test": "vitest run --coverage"}}, ensure_ascii=False),
            "target_files": ["packages/web/package.json"],
        },
        task_id="task-policy-2",
    )

    assert result["ok"] is True
    package_diff = result["director_policy"]["package_diff"]
    assert package_diff["has_changes"] is True
    assert package_diff["sections"]["scripts"]["changed"]["test"] == {
        "before": "vitest run",
        "after": "vitest run --coverage",
    }


@pytest.mark.asyncio
async def test_markdown_patch_file_is_fail_closed_without_file_event(tmp_path: Path) -> None:
    bus = RecordingMessageBus()
    executor = DirectorPatchExecutor(str(tmp_path))
    executor.set_message_bus(bus)
    response = "src/patch.ts\n```ts\nexport const patched = true;\n```"

    results = await executor.execute_tools(response, "task-3", _progress_noop)

    await _drain_broadcast_tasks()

    assert len(results) == 1
    assert results[0]["tool"] == "patch_apply"
    assert results[0]["success"] is False
    assert results[0]["error"] == "patch_file_protocol_disabled"
    assert results[0]["failure_class"] == FailureClassV1.PATCH_FILE_PROTOCOL_DISABLED.value
    assert results[0]["writes_allowed"] is False
    assert results[0]["result"]["repair_path"] == "native_tools_or_director_runtime_repair_required"
    assert not (tmp_path / "src" / "patch.ts").exists()
    assert bus.messages == []


@pytest.mark.asyncio
async def test_write_only_tool_fallback_rejects_read_calls_and_patch_blocks(tmp_path: Path) -> None:
    executor = DirectorPatchExecutor(str(tmp_path))
    response = (
        '[TOOL_CALL]{"tool":"readFile","arguments":{"path":"src/hooks/useWeather.ts"}}[/TOOL_CALL]\n'
        '[TOOL_CALL]{"tool":"write_file","arguments":{"file":"src/api/weatherApi.ts",'
        '"content":"export const ok = true;\\n"}}[/TOOL_CALL]\n'
        "src/ignored.ts\n```ts\nexport const ignored = true;\n```"
    )

    results = await executor.execute_tools(
        response,
        "task-write-only",
        _progress_noop,
        allowed_tool_names={"write_file"},
        allow_patch_fallback=False,
    )

    assert results == [
        {
            "tool": "text_tool_protocol",
            "success": False,
            "ok": False,
            "error": "text_tool_protocol_disabled",
            "failure_class": FailureClassV1.TEXT_TOOL_PROTOCOL_DISABLED.value,
            "protocol_violation": "text_tool_protocol_disabled",
            "task_id": "task-write-only",
        }
    ]
    assert not (tmp_path / "src" / "api" / "weatherApi.ts").exists()
    assert not (tmp_path / "src" / "ignored.ts").exists()


@pytest.mark.asyncio
async def test_write_only_tool_fallback_does_not_apply_markdown_without_write_tool(tmp_path: Path) -> None:
    executor = DirectorPatchExecutor(str(tmp_path))
    response = "src/ignored.ts\n```ts\nexport const ignored = true;\n```"

    results = await executor.execute_tools(
        response,
        "task-write-only",
        _progress_noop,
        allowed_tool_names={"write_file"},
        allow_patch_fallback=False,
    )

    assert len(results) == 1
    assert results[0]["tool"] == "patch_apply"
    assert results[0]["success"] is False
    assert results[0]["error"] == "patch_file_protocol_disabled"
    assert results[0]["failure_class"] == FailureClassV1.PATCH_FILE_PROTOCOL_DISABLED.value
    assert results[0]["allow_patch_fallback_requested"] is False
    assert not (tmp_path / "src" / "ignored.ts").exists()


@pytest.mark.asyncio
async def test_markdown_patch_file_returns_disabled_receipt_without_persisting_event(tmp_path: Path) -> None:
    executor = DirectorPatchExecutor(str(tmp_path))
    response = "src/patch.ts\n```ts\nexport const patched = true;\n```"

    results = await executor.execute_tools(response, "task-persist-2", _progress_noop)

    assert len(results) == 1
    item = results[0]
    assert item["tool"] == "patch_apply"
    assert item["tool_name"] == "patch_apply"
    assert item["status"] == "blocked"
    assert item["success"] is False
    assert item["error"] == "patch_file_protocol_disabled"
    assert item["failure_class"] == FailureClassV1.PATCH_FILE_PROTOCOL_DISABLED.value
    assert item["result"]["authoritative_receipt"] is False
    assert not (tmp_path / "src" / "patch.ts").exists()

    event_log = tmp_path / ".polaris" / "runtime" / "file-edits" / "events.jsonl"
    assert not event_log.exists()
