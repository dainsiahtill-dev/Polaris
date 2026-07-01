from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from polaris.cells.roles.kernel.internal.kernel import stream_run_id


def test_resolve_stream_run_id_prefers_request_value(tmp_path: Any) -> None:
    assert stream_run_id.resolve_stream_run_id("run-explicit", str(tmp_path)) == "run-explicit"


def test_resolve_stream_run_id_reads_latest_run_file(monkeypatch: Any, tmp_path: Any) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "latest_run.json").write_text('{"run_id": "run-latest"}\n', encoding="utf-8")
    monkeypatch.setattr(
        stream_run_id,
        "resolve_storage_roots",
        lambda _workspace: SimpleNamespace(runtime_root=str(runtime_root)),
    )

    assert stream_run_id.resolve_stream_run_id(None, str(tmp_path)) == "run-latest"


def test_resolve_stream_run_id_generates_fallback(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setattr(
        stream_run_id,
        "resolve_storage_roots",
        lambda _workspace: SimpleNamespace(runtime_root=str(tmp_path / "missing-runtime")),
    )
    monkeypatch.setattr(stream_run_id.uuid, "uuid4", lambda: SimpleNamespace(hex="abcdef1234567890"))

    assert stream_run_id.resolve_stream_run_id(None, str(tmp_path)) == "auto_abcdef123456"
