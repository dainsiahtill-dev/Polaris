from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from polaris.kernelone.storage import resolve_storage_roots

from .observer.projection import RuntimeProjection


class _FakeWebSocket:
    """Minimal websocket test double."""

    def __init__(self, incoming_messages: list[dict[str, Any]]) -> None:
        self._incoming = [json.dumps(item, ensure_ascii=False) for item in incoming_messages]
        self.sent_messages: list[str] = []
        self.closed = False

    async def send(self, text: str) -> None:
        self.sent_messages.append(str(text))

    async def recv(self) -> str:
        if self._incoming:
            return self._incoming.pop(0)
        raise RuntimeError("no message available")

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_connect_ws_subscribes_runtime_v2_with_jetstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ws = _FakeWebSocket(
        incoming_messages=[
            {
                "type": "status",
                "timestamp": "2026-03-20T12:00:00Z",
                "pm_status": {"running": True},
                "director_status": {},
            },
            {
                "type": "SUBSCRIBED",
                "protocol": "runtime.v2",
                "payload": {
                    "client_id": "observer-abc",
                    "channels": ["*"],
                    "cursor": 12,
                    "jetstream": True,
                },
            },
        ]
    )

    async def _fake_connect(*args: Any, **kwargs: Any) -> _FakeWebSocket:
        del args, kwargs
        return fake_ws

    monkeypatch.setattr("polaris.tests.agent_stress.observer.projection.websockets.connect", _fake_connect)

    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="token",
        workspace=str(tmp_path / "workspace"),
    )
    connected = await projection._connect_ws()

    assert connected is True
    assert projection._runtime_v2_enabled is True
    assert projection._runtime_v2_jetstream is True
    assert projection.transport_used in {"none", "ws.runtime_v2"}

    assert fake_ws.sent_messages
    subscribe_payload = json.loads(fake_ws.sent_messages[0])
    assert subscribe_payload["type"] == "SUBSCRIBE"
    assert subscribe_payload["protocol"] == "runtime.v2"
    assert subscribe_payload["channels"] == ["*"]
    assert subscribe_payload["workspace"] == resolve_storage_roots(str(tmp_path / "workspace")).workspace_key


@pytest.mark.asyncio
async def test_connect_ws_fails_when_runtime_v2_has_no_jetstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_ws = _FakeWebSocket(
        incoming_messages=[
            {
                "type": "SUBSCRIBED",
                "protocol": "runtime.v2",
                "payload": {
                    "client_id": "observer-abc",
                    "channels": ["*"],
                    "cursor": 0,
                    "jetstream": False,
                },
            }
        ]
    )

    async def _fake_connect(*args: Any, **kwargs: Any) -> _FakeWebSocket:
        del args, kwargs
        return fake_ws

    monkeypatch.setattr("polaris.tests.agent_stress.observer.projection.websockets.connect", _fake_connect)

    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="token",
        workspace=str(tmp_path / "workspace"),
    )
    connected = await projection._connect_ws()

    assert connected is False
    assert projection.connection_error == "runtime_v2_subscribed_without_jetstream"
    assert fake_ws.closed is True


@pytest.mark.asyncio
async def test_runtime_v2_event_is_acked_and_projected_to_reasoning(tmp_path: Path) -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="token",
        workspace=str(tmp_path / "workspace"),
    )
    fake_ws = _FakeWebSocket(incoming_messages=[])
    projection.ws = fake_ws
    projection._runtime_v2_enabled = True

    await projection._on_message(
        {
            "type": "EVENT",
            "protocol": "runtime.v2",
            "cursor": 88,
            "event": {
                "channel": "llm",
                "kind": "llm.content_chunk",
                "run_id": "run-1",
                "ts": "2026-03-20T12:00:01Z",
                "payload": {
                    "actor": "architect",
                    "message": "正在分析迁移边界与依赖图。",
                    "tags": ["projection_event:content_chunk"],
                },
            },
        }
    )

    llm_rows = projection.get_panels()["llm_reasoning"]
    assert llm_rows
    latest = llm_rows[-1]
    assert str(latest.get("role") or "") == "architect"
    assert str(latest.get("event_type") or "") in {"content_chunk", "content_preview"}
    assert "迁移边界" in str(latest.get("content") or "")

    assert fake_ws.sent_messages
    ack_payload = json.loads(fake_ws.sent_messages[-1])
    assert ack_payload["type"] == "ACK"
    assert ack_payload["protocol"] == "runtime.v2"
    assert ack_payload["cursor"] == 88


@pytest.mark.asyncio
async def test_runtime_v2_projection_event_tags_drive_waiting_state(tmp_path: Path) -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="token",
        workspace=str(tmp_path / "workspace"),
    )
    fake_ws = _FakeWebSocket(incoming_messages=[])
    projection.ws = fake_ws
    projection._runtime_v2_enabled = True

    await projection._on_message(
        {
            "type": "EVENT",
            "protocol": "runtime.v2",
            "cursor": 101,
            "event": {
                "channel": "llm",
                "kind": "llm.state",
                "run_id": "run-1",
                "ts": "2026-03-20T12:00:02Z",
                "payload": {
                    "actor": "pm",
                    "message": "waiting for LLM response | model=gpt-5.4",
                    "tags": ["projection_event:llm_waiting", "llm_event:llm_call_start"],
                },
            },
        }
    )

    llm_rows = projection.get_panels()["llm_reasoning"]
    assert llm_rows
    latest = llm_rows[-1]
    assert latest["role"] == "pm"
    assert latest["event_type"] == "llm_waiting"
    assert "waiting for LLM response" in str(latest.get("content") or "")


@pytest.mark.asyncio
async def test_runtime_v2_structured_tool_event_uses_raw_payload_fields(tmp_path: Path) -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="token",
        workspace=str(tmp_path / "workspace"),
    )
    fake_ws = _FakeWebSocket(incoming_messages=[])
    projection.ws = fake_ws
    projection._runtime_v2_enabled = True

    await projection._on_message(
        {
            "type": "EVENT",
            "protocol": "runtime.v2",
            "cursor": 118,
            "event": {
                "channel": "llm",
                "kind": "llm.output",
                "run_id": "run-structured",
                "ts": "2026-03-20T12:00:04Z",
                "payload": {
                    "actor": "director",
                    "message": "tool_result:write_file:ok",
                    "tags": ["projection_event:tool_result"],
                    "raw": {
                        "stream_event": "tool_result",
                        "data": {
                            "task_id": "task-7",
                            "attempt": 2,
                            "metadata": {
                                "tool_name": "write_file",
                                "args": {"file": "src/app.py", "content": "print('ok')"},
                                "success": True,
                                "result_payload": {
                                    "success": True,
                                    "result": {"path": "src/app.py"},
                                },
                            },
                        },
                    },
                },
            },
        }
    )

    llm_rows = projection.get_panels()["llm_reasoning"]
    assert llm_rows
    latest = llm_rows[-1]
    assert latest["event_type"] == "tool_result"
    assert latest["tool_name"] == "write_file"
    assert latest["tool_success"] is True
    assert latest["tool_status"] == "ok"
    assert latest["task_id"] == "task-7"
    assert latest["attempt"] == 2
    assert latest["tool_args"] == {"file": "src/app.py", "content": "print('ok')"}
    assert latest["tool_result_raw"] == {"success": True, "result": {"path": "src/app.py"}}


@pytest.mark.asyncio
async def test_runtime_v2_content_preview_preserves_preview_event_type(tmp_path: Path) -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="token",
        workspace=str(tmp_path / "workspace"),
    )
    fake_ws = _FakeWebSocket(incoming_messages=[])
    projection.ws = fake_ws
    projection._runtime_v2_enabled = True

    await projection._on_message(
        {
            "type": "EVENT",
            "protocol": "runtime.v2",
            "cursor": 119,
            "event": {
                "channel": "llm",
                "kind": "llm.output",
                "run_id": "run-preview",
                "ts": "2026-03-20T12:00:05Z",
                "payload": {
                    "actor": "architect",
                    "message": "fallback preview message",
                    "tags": ["projection_event:content_preview"],
                    "raw": {
                        "stream_event": "content_preview",
                        "data": {
                            "metadata": {
                                "preview": "架构摘要：已完成边界梳理与依赖收敛。",
                            },
                        },
                    },
                },
            },
        }
    )

    llm_rows = projection.get_panels()["llm_reasoning"]
    assert llm_rows
    latest = llm_rows[-1]
    assert latest["event_type"] == "content_preview"
    assert "架构摘要" in str(latest.get("content") or "")


@pytest.mark.asyncio
async def test_legacy_llm_stream_is_ignored_once_runtime_v2_is_enabled(tmp_path: Path) -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="token",
        workspace=str(tmp_path / "workspace"),
    )
    projection._runtime_v2_enabled = True

    await projection._on_message(
        {
            "type": "llm_stream",
            "timestamp": "2026-03-20T12:00:03Z",
            "channel": "llm",
            "event": {
                "raw": {
                    "stream_event": "content_chunk",
                    "content": "legacy event should be ignored",
                },
                "actor": "director",
            },
        }
    )

    assert projection.get_panels()["llm_reasoning"] == []


@pytest.mark.asyncio
async def test_status_snapshot_tasks_populate_taskboard_panel(tmp_path: Path) -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="token",
        workspace=str(tmp_path / "workspace"),
    )

    await projection._on_message(
        {
            "type": "status",
            "timestamp": "2026-03-20T13:00:00+00:00",
            "pm_status": {"running": True},
            "director_status": {},
            "snapshot": {
                "tasks": [
                    {
                        "id": "task-1",
                        "subject": "实现 task runtime service",
                        "status": "in_progress",
                        "metadata": {
                            "execution_backend": "projection_generate",
                            "projection": {"scenario_id": "scenario_alpha"},
                        },
                    },
                    {"id": "task-2", "subject": "补充 observer 回归测试", "status": "pending"},
                ]
            },
        }
    )

    taskboard_rows = projection.get_panels()["taskboard_status"]
    assert taskboard_rows
    latest = taskboard_rows[-1]
    assert latest["source"] == "status.snapshot"
    assert "total=2" in str(latest.get("summary") or "")
    items = latest.get("items")
    assert isinstance(items, list)
    matched = next(item for item in items if isinstance(item, dict) and str(item.get("id") or "") == "task-1")
    assert matched["execution_backend"] == "projection_generate"
    assert matched["projection_scenario"] == "scenario_alpha"


@pytest.mark.asyncio
async def test_runtime_v2_system_event_refs_taskboard_populates_panel(tmp_path: Path) -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="token",
        workspace=str(tmp_path / "workspace"),
    )
    fake_ws = _FakeWebSocket(incoming_messages=[])
    projection.ws = fake_ws
    projection._runtime_v2_enabled = True

    await projection._on_message(
        {
            "type": "EVENT",
            "protocol": "runtime.v2",
            "cursor": 131,
            "event": {
                "channel": "system",
                "kind": "system.observation",
                "run_id": "run-taskboard",
                "ts": "2026-03-20T13:10:00Z",
                "payload": {
                    "actor": "director",
                    "message": "director.taskboard.claimed",
                    "raw": {
                        "refs": {
                            "taskboard": {
                                "counts": {
                                    "total": 1,
                                    "ready": 0,
                                    "pending": 0,
                                    "in_progress": 1,
                                    "completed": 0,
                                    "failed": 0,
                                    "blocked": 0,
                                },
                                "samples": {
                                    "in_progress": [
                                        {
                                            "id": "task-7",
                                            "subject": "执行任务合同",
                                            "qa_state": "",
                                            "claimed_by": "director",
                                            "resume_state": "resumed",
                                            "session_id": "tx-123",
                                        },
                                    ]
                                },
                            }
                        }
                    },
                },
            },
        }
    )

    rows = projection.get_panels()["taskboard_status"]
    assert rows
    latest = rows[-1]
    assert "runtime.v2" in str(latest.get("source") or "")
    assert "taskboard" in str(latest.get("source") or "")
    assert "total=1" in str(latest.get("summary") or "")
    items = latest.get("items")
    assert isinstance(items, list)
    matched = next(item for item in items if isinstance(item, dict) and str(item.get("id") or "") == "task-7")
    assert matched["resume_state"] == "resumed"


@pytest.mark.asyncio
async def test_task_trace_focus_task_keeps_running_task_visible_across_stale_status_snapshots(
    tmp_path: Path,
) -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="token",
        workspace=str(tmp_path / "workspace"),
    )

    await projection._on_message(
        {
            "type": "task_trace",
            "timestamp": "2026-03-20T13:11:00+00:00",
            "event": {
                "event": {
                    "status": "running",
                    "code": "director.execution_backend.selected",
                    "step_title": "Director execution backend selected",
                    "refs": {
                        "taskboard": {
                            "counts": {
                                "total": 2,
                                "ready": 1,
                                "pending": 1,
                                "in_progress": 0,
                                "completed": 0,
                                "failed": 0,
                                "blocked": 0,
                            },
                            "samples": {
                                "pending": [
                                    {"id": "task-2", "subject": "编写单元测试与集成验证"},
                                ]
                            },
                        },
                        "taskboard_task": {
                            "id": "task-1",
                            "subject": "实现数据模型与本地持久化存储层",
                            "status": "pending",
                            "execution_backend": "code_edit",
                        },
                    },
                }
            },
        }
    )

    taskboard_rows = projection.get_panels()["taskboard_status"]
    assert taskboard_rows
    latest = taskboard_rows[-1]
    assert latest["source"] == "task_trace.focus_task"
    assert "running=1" in str(latest.get("summary") or "")
    first_item = latest.get("items")[0]
    assert first_item["id"] == "task-1"
    assert first_item["status"] == "in_progress"
    assert first_item["execution_backend"] == "code_edit"

    await projection._on_message(
        {
            "type": "status",
            "timestamp": "2026-03-20T13:11:05+00:00",
            "pm_status": {"running": True},
            "director_status": {"running": True},
            "snapshot": {
                "tasks": [
                    {"id": "task-1", "subject": "实现数据模型与本地持久化存储层", "status": "pending"},
                    {"id": "task-2", "subject": "编写单元测试与集成验证", "status": "pending"},
                ]
            },
        }
    )

    latest_after_status = projection.get_panels()["taskboard_status"][-1]
    assert latest_after_status["source"] == "status.active_task"
    assert "running=1" in str(latest_after_status.get("summary") or "")
    first_after_status = latest_after_status.get("items")[0]
    assert first_after_status["id"] == "task-1"
    assert first_after_status["status"] == "in_progress"
