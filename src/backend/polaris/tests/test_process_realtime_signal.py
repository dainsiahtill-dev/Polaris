from __future__ import annotations

import sys
import time

from polaris.cells.roles.runtime.internal import process_service as process_service


def test_spawn_process_emits_realtime_signal_on_exit(tmp_path, monkeypatch) -> None:
    calls: list[dict[str, str]] = []

    class _FakeHub:
        def notify_from_thread(self, **kwargs) -> None:
            calls.append({k: str(v) for k, v in kwargs.items()})

    monkeypatch.setattr(process_service, "REALTIME_SIGNAL_HUB", _FakeHub())

    log_path = str(tmp_path / "proc.log")
    handle = process_service.spawn_process(
        [sys.executable, "-c", "print('done')"],
        str(tmp_path),
        log_path,
    )

    deadline = time.time() + 5.0
    while time.time() < deadline and not calls:
        time.sleep(0.05)

    if handle.process is not None:
        handle.process.wait(timeout=5)
    if handle.log_handle is not None:
        handle.log_handle.close()

    assert calls
    assert calls[0]["source"] == "process_exit"
    assert calls[0]["path"] == log_path
