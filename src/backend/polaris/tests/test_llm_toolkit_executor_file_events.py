"""Behavior tests for AgentAccelToolExecutor event emission.

Verifies that tool execution paths emit FILE_WRITTEN events through the
correct kernelone.events pipeline.
"""

from __future__ import annotations

import json
from typing import Any

from polaris.kernelone.llm.toolkit.executor import AgentAccelToolExecutor


def _read_file_edit_events(workspace) -> list[dict[str, Any]]:
    event_log = workspace / ".polaris" / "runtime" / "file-edits" / "events.jsonl"
    if not event_log.exists():
        return []
    return [json.loads(line) for line in event_log.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_write_file_emits_file_written_event(tmp_path) -> None:
    """write_file emits FILE_WRITTEN with operation=create for new files."""
    executor = AgentAccelToolExecutor(str(tmp_path))

    result = executor.execute(
        "write_file",
        {
            "file": "src/hello.py",
            "content": "print('hello')\n",
            "encoding": "utf-8",
        },
    )

    assert result.get("ok") is True
    events = _read_file_edit_events(tmp_path)
    assert len(events) == 1, f"Expected 1 event, got {len(events)}"
    payload = events[0]["payload"]
    assert payload["file_path"] == "src/hello.py"
    assert payload["operation"] == "create"
    assert 'print("hello")' in payload["patch"]


def test_write_file_persists_file_edit_event_without_message_bus(tmp_path) -> None:
    """KernelOne toolkit write paths must leave durable file-edit evidence even without a bus."""
    executor = AgentAccelToolExecutor(str(tmp_path))

    result = executor.execute(
        "write_file",
        {
            "file": "src/persisted.py",
            "content": "print('persisted')\n",
            "encoding": "utf-8",
        },
    )

    assert result.get("ok") is True
    events = _read_file_edit_events(tmp_path)
    assert len(events) == 1
    event = events[0]
    payload = event["payload"]
    assert event["channel"] == "event.file_edit"
    assert payload["file_path"] == "src/persisted.py"
    assert payload["operation"] == "create"
    assert payload["has_patch"] is True
    assert 'print("persisted")' in payload["patch"]


def test_write_file_emits_modify_event_when_file_exists(tmp_path) -> None:
    """write_file emits FILE_WRITTEN with operation=modify when overwriting existing file."""
    # Pre-create the file so write_file detects it as existing
    existing = tmp_path / "src" / "existing.py"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("original\n", encoding="utf-8")

    executor = AgentAccelToolExecutor(str(tmp_path))

    result = executor.execute(
        "write_file",
        {
            "file": "src/existing.py",
            "content": "modified\n",
            "encoding": "utf-8",
        },
    )

    assert result.get("ok") is True
    events = _read_file_edit_events(tmp_path)
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["operation"] == "modify"
    assert "-original" in payload["patch"]
    assert "+modified" in payload["patch"]


def test_search_replace_emits_file_written_event(tmp_path) -> None:
    """search_replace emits FILE_WRITTEN with operation=modify."""
    source = tmp_path / "src" / "target.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("print('old')\n", encoding="utf-8")

    executor = AgentAccelToolExecutor(str(tmp_path))
    read_result = executor.execute("read_file", {"file": "src/target.py"})
    assert read_result.get("ok") is True

    result = executor.execute(
        "search_replace",
        {
            "file": "src/target.py",
            "search": "old",
            "replace": "new",
            "regex": False,
            "replace_all": False,
        },
    )

    assert result.get("ok") is True
    events = _read_file_edit_events(tmp_path)
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["file_path"] == "src/target.py"
    assert payload["operation"] == "modify"
    assert "new" in payload["patch"]


def test_read_file_does_not_emit_file_written(tmp_path) -> None:
    """read_file does not emit FILE_WRITTEN (read-only operation)."""
    source = tmp_path / "src" / "readme.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("read only content\n", encoding="utf-8")

    executor = AgentAccelToolExecutor(str(tmp_path))

    result = executor.execute(
        "read_file",
        {"file": "src/readme.txt"},
    )

    assert result.get("ok") is True
    assert _read_file_edit_events(tmp_path) == [], "read_file should not emit file_written events"


def test_failed_tool_result_preserves_structured_failure_evidence(tmp_path, monkeypatch) -> None:
    executor = AgentAccelToolExecutor(str(tmp_path))

    def _fake_read_file(_executor: AgentAccelToolExecutor, **_kwargs: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "error": "receipt missing",
            "error_type": "missing_effect_receipt",
            "failure_evidence": [
                {
                    "schema_version": "polaris.failure_evidence.v1",
                    "failure_class": "MISSING_EFFECT_RECEIPT",
                    "responsible_layer": "platform",
                    "evidence_refs": ["tool_lifecycle:turn-1"],
                }
            ],
            "failure_evidence_summary": {
                "count": 1,
                "latest_failure_class": "MISSING_EFFECT_RECEIPT",
            },
        }

    monkeypatch.setattr(executor, "_load_handler_modules", lambda: None)
    executor._handler_modules.set("read_file", _fake_read_file)

    result = executor.execute("read_file", {"file": "src/missing.py"})

    assert result["ok"] is False
    assert result["handler_error_type"] == "missing_effect_receipt"
    assert result["failure_evidence"] == [
        {
            "schema_version": "polaris.failure_evidence.v1",
            "failure_class": "MISSING_EFFECT_RECEIPT",
            "responsible_layer": "platform",
            "evidence_refs": ["tool_lifecycle:turn-1"],
        }
    ]
    assert result["failure_evidence_summary"] == {
        "count": 1,
        "latest_failure_class": "MISSING_EFFECT_RECEIPT",
    }


def test_execute_command_emits_no_file_event(tmp_path) -> None:
    """execute_command does not emit FILE_WRITTEN events."""
    executor = AgentAccelToolExecutor(str(tmp_path))

    executor.execute(
        "execute_command",
        {
            "command": "echo hello",
            "cwd": str(tmp_path),
            "timeout": 5,
        },
    )

    # Command may succeed or fail depending on shell environment; we only care
    # that no file-written event was emitted.
    assert _read_file_edit_events(tmp_path) == [], "execute_command should not emit file_written events"
