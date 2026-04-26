from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import polaris.infrastructure.log_pipeline.writer as writer_module
from polaris.delivery.http.routers.logs import log_user_action
from polaris.infrastructure.log_pipeline.query import LogQuery, LogQueryService
from polaris.infrastructure.log_pipeline.writer import LogEventWriter
from polaris.infrastructure.realtime.process_local.log_fanout import LOG_REALTIME_FANOUT
from polaris.kernelone.storage import (
    resolve_runtime_path,
    resolve_storage_roots,
)


def test_log_writer_and_query_use_unified_runtime_root(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime_root = tmp_path / "runtime_root"
    runtime_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")

    writer = LogEventWriter(workspace=str(workspace), run_id="RUN-001")
    writer.write_event(message="pipeline-check", actor="PM")

    roots = resolve_storage_roots(str(workspace))
    expected_runtime_root = Path(roots.runtime_root).resolve()
    expected_log = expected_runtime_root / "runs" / "RUN-001" / "logs" / "journal.norm.jsonl"

    assert Path(writer.runtime_root).resolve() == expected_runtime_root
    assert expected_log.exists()
    assert not (workspace / "runtime" / "runs" / "RUN-001" / "logs" / "journal.norm.jsonl").exists()

    service = LogQueryService(workspace=str(workspace))
    result = service.query(LogQuery(run_id="RUN-001", limit=10))
    assert len(result.events) == 1
    assert result.events[0].message == "pipeline-check"


def test_logs_router_user_action_writes_to_runtime_layout(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime_root = tmp_path / "runtime_root"
    runtime_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")

    payload = asyncio.run(
        log_user_action(
            action="open-settings",
            user="tester",
            metadata={"source": "test"},
            workspace=str(workspace),
        )
    )

    assert payload.get("status") == "logged"

    user_actions_path = Path(
        resolve_runtime_path(
            str(workspace),
            "runtime/logs/user_actions.jsonl",
        )
    )
    assert user_actions_path.exists()
    assert "open-settings" in user_actions_path.read_text(encoding="utf-8")


def test_log_writer_publishes_realtime_fanout(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime_root = tmp_path / "runtime_root"
    runtime_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")

    async def _run() -> None:
        writer = LogEventWriter(workspace=str(workspace), run_id="RUN-RT-001")
        conn_id = f"test-{uuid.uuid4().hex[:8]}"
        subscription = await LOG_REALTIME_FANOUT.register_connection(
            connection_id=conn_id,
            runtime_root=writer.runtime_root,
        )
        try:
            writer.write_event(
                message="realtime-check",
                channel="llm",
                domain="llm",
                actor="PM",
                raw={"stream_event": "thinking_chunk", "content": "step-1"},
            )
            payload = await asyncio.wait_for(subscription.queue.get(), timeout=1.0)
            assert payload.get("message") == "realtime-check"
            assert str(payload.get("channel")) == "llm"
            assert str(payload.get("run_id")) == "RUN-RT-001"
            assert subscription.consume_dropped() == 0
        finally:
            await LOG_REALTIME_FANOUT.unregister_connection(conn_id)

    asyncio.run(_run())


def test_log_writer_extracts_workspace_key_from_storage_roots(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime_root = tmp_path / "runtime_root"
    runtime_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")

    writer = LogEventWriter(workspace=str(workspace), run_id="RUN-KEY-001")
    expected_key = resolve_storage_roots(str(workspace)).workspace_key

    assert writer.workspace_key == expected_key
    assert writer._extract_workspace_key() == expected_key


def test_log_writer_enqueues_jetstream_publish_with_canonical_workspace_key(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    runtime_root = tmp_path / "runtime_root"
    runtime_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")

    captured: list[dict[str, object]] = []

    class _StubPublisher:
        def publish(self, *, subject: str, payload: dict[str, object]) -> bool:
            captured.append({"subject": subject, "payload": payload})
            return True

    monkeypatch.setattr(writer_module, "_jetstream_available", True)
    monkeypatch.setattr(writer_module, "PUBLISH_ENABLED", True)
    monkeypatch.setattr(writer_module, "get_log_jetstream_publisher", lambda: _StubPublisher())

    writer = LogEventWriter(workspace=str(workspace), run_id="RUN-JS-001")
    writer.write_event(
        message="jetstream-check",
        channel="llm",
        domain="llm",
        actor="PM",
        raw={"stream_event": "llm_waiting"},
    )

    assert len(captured) == 1
    payload = captured[0]["payload"]
    assert isinstance(payload, dict)
    assert captured[0]["subject"] == f"hp.runtime.{writer.workspace_key}.llm"
    assert payload["workspace_key"] == writer.workspace_key
    assert payload["channel"] == "llm"
