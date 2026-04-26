"""Behavior tests for AgentAccelToolExecutor event emission.

Verifies that tool execution paths emit FILE_WRITTEN events through the
correct kernelone.events pipeline.
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.llm.toolkit.executor import AgentAccelToolExecutor


class _EventCapture:
    """Records _emit_file_written_event calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        *,
        file_path: str,
        operation: str,
        old_content: str,
        new_content: str,
    ) -> None:
        self.calls.append(
            {
                "file_path": file_path,
                "operation": operation,
                "old_content": old_content,
                "new_content": new_content,
            }
        )


def test_write_file_emits_file_written_event(tmp_path) -> None:
    """write_file emits FILE_WRITTEN with operation=create for new files."""
    executor = AgentAccelToolExecutor(str(tmp_path))
    capture = _EventCapture()
    executor._emit_file_written_event = capture  # type: ignore[method-assignment]

    result = executor.execute(
        "write_file",
        {
            "file": "src/hello.py",
            "content": "print('hello')\n",
            "encoding": "utf-8",
        },
    )

    assert result.get("ok") is True
    assert len(capture.calls) == 1, f"Expected 1 event, got {len(capture.calls)}"
    call = capture.calls[0]
    assert call["file_path"] == "src/hello.py"
    assert call["operation"] == "create"
    assert "print('hello')" in call["new_content"]


def test_write_file_emits_modify_event_when_file_exists(tmp_path) -> None:
    """write_file emits FILE_WRITTEN with operation=modify when overwriting existing file."""
    # Pre-create the file so write_file detects it as existing
    existing = tmp_path / "src" / "existing.py"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("original\n", encoding="utf-8")

    executor = AgentAccelToolExecutor(str(tmp_path))
    capture = _EventCapture()
    executor._emit_file_written_event = capture  # type: ignore[method-assignment]

    result = executor.execute(
        "write_file",
        {
            "file": "src/existing.py",
            "content": "modified\n",
            "encoding": "utf-8",
        },
    )

    assert result.get("ok") is True
    assert len(capture.calls) == 1
    call = capture.calls[0]
    assert call["operation"] == "modify"
    assert call["old_content"] == "original\n"
    assert call["new_content"] == "modified\n"


def test_search_replace_emits_file_written_event(tmp_path) -> None:
    """search_replace emits FILE_WRITTEN with operation=modify."""
    source = tmp_path / "src" / "target.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("print('old')\n", encoding="utf-8")

    executor = AgentAccelToolExecutor(str(tmp_path))
    capture = _EventCapture()
    executor._emit_file_written_event = capture  # type: ignore[method-assignment]

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
    assert len(capture.calls) == 1
    call = capture.calls[0]
    assert call["file_path"] == "src/target.py"
    assert call["operation"] == "modify"
    assert "new" in call["new_content"]
    assert "old" not in capture.calls[0]["new_content"]


def test_read_file_does_not_emit_file_written(tmp_path) -> None:
    """read_file does not emit FILE_WRITTEN (read-only operation)."""
    source = tmp_path / "src" / "readme.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("read only content\n", encoding="utf-8")

    executor = AgentAccelToolExecutor(str(tmp_path))
    capture = _EventCapture()
    executor._emit_file_written_event = capture  # type: ignore[method-assignment]

    result = executor.execute(
        "read_file",
        {"file": "src/readme.txt"},
    )

    assert result.get("ok") is True
    assert len(capture.calls) == 0, "read_file should not emit file_written events"


def test_execute_command_emits_no_file_event(tmp_path) -> None:
    """execute_command does not emit FILE_WRITTEN events."""
    executor = AgentAccelToolExecutor(str(tmp_path))
    capture = _EventCapture()
    executor._emit_file_written_event = capture  # type: ignore[method-assignment]

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
    assert len(capture.calls) == 0, "execute_command should not emit file_written events"
