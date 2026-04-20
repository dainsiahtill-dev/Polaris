from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.fs.jsonl import ops


class _NoopTimer:
    def __init__(self, interval: float, callback: Any) -> None:
        self.interval = interval
        self.callback = callback
        self.daemon = False

    def start(self) -> None:
        return


@pytest.fixture(autouse=True)
def _reset_jsonl_module_state(monkeypatch: pytest.MonkeyPatch) -> None:
    ops._JSONL_BUFFER.clear()
    ops._JSONL_LAST_ACCESS.clear()
    ops._JSONL_CLEANUP_TIMER = None
    monkeypatch.setattr(ops, "Timer", _NoopTimer)
    yield
    ops._JSONL_BUFFER.clear()
    ops._JSONL_LAST_ACCESS.clear()
    ops._JSONL_CLEANUP_TIMER = None


def test_append_jsonl_atomic_raises_when_lock_cannot_be_acquired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "events.jsonl"
    monkeypatch.setattr(ops, "acquire_lock_fd", lambda *_args, **_kwargs: None)

    with pytest.raises(TimeoutError, match="Timed out acquiring JSONL lock"):
        ops.append_jsonl_atomic(
            str(target),
            {"kind": "atomic"},
            lock_timeout_sec=0.01,
        )


def test_flush_jsonl_buffers_preserves_lines_appended_during_flush(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = str(tmp_path / "events.jsonl")
    first_line = json.dumps({"seq": 1}, ensure_ascii=False) + "\n"
    second_line = json.dumps({"seq": 2}, ensure_ascii=False) + "\n"
    ops._JSONL_BUFFER[target] = {
        "lines": [first_line],
        "last_flush": 0.0,
    }

    def _fake_flush(path: str, lines: list[str], lock_timeout_sec: float) -> bool:
        assert path == target
        assert lines == [first_line]
        assert lock_timeout_sec == 5.0
        with ops._JSONL_BUFFER_LOCK:
            ops._JSONL_BUFFER[target]["lines"].append(second_line)
        return True

    monkeypatch.setattr(ops, "_flush_jsonl_path", _fake_flush)

    ops.flush_jsonl_buffers(force=True)

    assert ops._JSONL_BUFFER[target]["lines"] == [second_line]


def test_cleanup_flushes_targeted_paths_without_dropping_failed_buffer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained_path = str(tmp_path / "retained.jsonl")
    removable_path = str(tmp_path / "removable.jsonl")
    now = time.time()

    ops._JSONL_BUFFER[retained_path] = {
        "lines": [json.dumps({"seq": 1}, ensure_ascii=False) + "\n"],
        "last_flush": 0.0,
    }
    ops._JSONL_BUFFER[removable_path] = {
        "lines": [],
        "last_flush": 0.0,
    }
    ops._JSONL_LAST_ACCESS[retained_path] = now - 10_000
    ops._JSONL_LAST_ACCESS[removable_path] = now - 10_000
    monkeypatch.setattr(ops, "_JSONL_BUFFER_TTL_SEC", 1.0)

    flushed_paths: list[str] = []

    def _fake_flush_buffered_path(
        path: str,
        *,
        force: bool,
        lock_timeout_sec: float,
    ) -> bool:
        flushed_paths.append(path)
        assert force is True
        assert lock_timeout_sec == 5.0
        return False

    monkeypatch.setattr(ops, "_flush_jsonl_buffered_path", _fake_flush_buffered_path)

    ops._cleanup_jsonl_buffer()

    assert flushed_paths == [retained_path]
    assert retained_path in ops._JSONL_BUFFER
    assert removable_path not in ops._JSONL_BUFFER
