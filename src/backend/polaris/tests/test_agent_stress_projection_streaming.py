"""Observer projection streaming behavior tests."""

from __future__ import annotations

import asyncio
from datetime import datetime
import io
import json
import sys
import types
import urllib.parse
from pathlib import Path

from rich.console import Console

TESTS_ROOT = Path(__file__).resolve().parent
tests_pkg = sys.modules.get("tests")
if tests_pkg is None:
    tests_pkg = types.ModuleType("tests")
    sys.modules["tests"] = tests_pkg
tests_pkg.__path__ = [str(TESTS_ROOT)]

import tests.agent_stress.observer as observer_module
from tests.agent_stress.observer import (
    ObserverState,
    RuntimeProjection,
    _extract_runtime_root_from_settings_line,
    _extract_workspace_from_settings_line,
    _stream_lines,
)


def test_runtime_projection_merges_content_chunks() -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="test-token",
        workspace=str(Path.cwd()),
    )

    async def _feed() -> None:
        await projection._on_message(
            {
                "type": "llm_stream",
                "channel": "llm",
                "event": {
                    "ts": "2026-03-09T12:00:00Z",
                    "actor": "pm",
                    "raw": {"stream_event": "content_chunk", "content": "Hel"},
                },
            }
        )
        await projection._on_message(
            {
                "type": "llm_stream",
                "channel": "llm",
                "event": {
                    "ts": "2026-03-09T12:00:01Z",
                    "actor": "pm",
                    "raw": {"stream_event": "content_chunk", "content": "lo"},
                },
            }
        )

    asyncio.run(_feed())

    llm_rows = projection.get_panels()["llm_reasoning"]
    assert len(llm_rows) == 1
    assert llm_rows[0]["event_type"] == "content_chunk"
    assert llm_rows[0]["content"] == "Hello"


def test_runtime_projection_renders_tool_call_and_result() -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="test-token",
        workspace=str(Path.cwd()),
    )

    async def _feed() -> None:
        await projection._on_message(
            {
                "type": "llm_stream",
                "channel": "llm",
                "event": {
                    "ts": "2026-03-09T12:01:00Z",
                    "actor": "director",
                    "raw": {
                        "stream_event": "tool_call",
                        "tool": "write_file",
                        "args": {"path": "README.md"},
                    },
                },
            }
        )
        await projection._on_message(
            {
                "type": "llm_stream",
                "channel": "llm",
                "event": {
                    "ts": "2026-03-09T12:01:01Z",
                    "actor": "director",
                    "raw": {
                        "stream_event": "tool_result",
                        "tool": "write_file",
                        "success": True,
                        "result": {"success": True},
                    },
                },
            }
        )

    asyncio.run(_feed())

    llm_rows = projection.get_panels()["llm_reasoning"]
    assert len(llm_rows) == 2
    assert llm_rows[0]["event_type"] == "tool_call"
    assert "write_file" in llm_rows[0]["content"]
    assert llm_rows[1]["event_type"] == "tool_result"
    assert "write_file -> ok" in llm_rows[1]["content"]

    state = ObserverState(
        workspace=str(Path.cwd()),
        rounds=1,
        strategy="full",
        backend_url="http://127.0.0.1:49977",
        output_dir=str(Path.cwd()),
        projection_enabled=True,
        projection_transport="ws",
        projection_focus="all",
    )
    state.update_projection(
        connected=True,
        transport_used="ws",
        error="",
        panels=projection.get_panels(),
    )
    assert any(item.get("event_type") == "tool_call" for item in state.projection_llm)
    assert any(item.get("event_type") == "tool_result" for item in state.projection_llm)
    assert any("[director tool_call]" in line for line in state.projection_tools)
    assert any("[director tool_result]" in line for line in state.projection_tools)


def test_runtime_projection_parses_json_from_line_when_event_missing() -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="test-token",
        workspace=str(Path.cwd()),
    )

    async def _feed() -> None:
        await projection._on_message(
            {
                "type": "llm_stream",
                "channel": "llm",
                "line": (
                    '{"ts":"2026-03-09T12:02:00Z","actor":"pm",'
                    '"raw":{"stream_event":"thinking_chunk","content":"step-1"}}'
                ),
            }
        )

    asyncio.run(_feed())

    llm_rows = projection.get_panels()["llm_reasoning"]
    assert len(llm_rows) == 1
    assert llm_rows[0]["event_type"] == "thinking_chunk"
    assert llm_rows[0]["content"] == "step-1"


def test_runtime_projection_captures_dialogue_stream() -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="test-token",
        workspace=str(Path.cwd()),
    )

    async def _feed() -> None:
        await projection._on_message(
            {
                "type": "dialogue_event",
                "channel": "dialogue",
                "event": {
                    "ts": "2026-03-09T12:03:00Z",
                    "speaker": "pm",
                    "type": "assistant",
                    "text": "先检查任务合同，再执行工具。",
                },
            }
        )

    asyncio.run(_feed())

    dialogue_rows = projection.get_panels()["dialogue_stream"]
    assert len(dialogue_rows) == 1
    assert dialogue_rows[0]["speaker"] == "pm"
    assert dialogue_rows[0]["dialogue_type"] == "assistant"
    assert "任务合同" in dialogue_rows[0]["content"]

    state = ObserverState(
        workspace=str(Path.cwd()),
        rounds=1,
        strategy="full",
        backend_url="http://127.0.0.1:49977",
        output_dir=str(Path.cwd()),
        projection_enabled=True,
        projection_transport="ws",
        projection_focus="all",
    )
    state.update_projection(
        connected=True,
        transport_used="ws",
        error="",
        panels=projection.get_panels(),
    )
    assert any("[pm/assistant]" in line for line in state.projection_dialogue)


def test_observer_projection_render_matches_codex_like_sections() -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="test-token",
        workspace=str(Path.cwd()),
    )

    async def _feed() -> None:
        await projection._on_message(
            {
                "type": "status",
                "timestamp": "2026-03-09T12:04:00Z",
                "pm_status": {"running": True, "mode": "service"},
                "director_status": {"running": False, "mode": "v2_service"},
            }
        )
        await projection._on_message(
            {
                "type": "llm_stream",
                "channel": "llm",
                "event": {
                    "ts": "2026-03-09T12:04:01Z",
                    "actor": "pm",
                    "raw": {"stream_event": "thinking_chunk", "content": "先确认执行策略"},
                },
            }
        )
        await projection._on_message(
            {
                "type": "llm_stream",
                "channel": "llm",
                "event": {
                    "ts": "2026-03-09T12:04:02Z",
                    "actor": "pm",
                    "raw": {"stream_event": "tool_call", "tool": "read_file", "args": {"path": "README.md"}},
                },
            }
        )
        await projection._on_message(
            {
                "type": "dialogue_event",
                "channel": "dialogue",
                "event": {
                    "ts": "2026-03-09T12:04:03Z",
                    "speaker": "pm",
                    "type": "assistant",
                    "text": "已读取文档，准备生成执行计划。",
                },
            }
        )

    asyncio.run(_feed())

    state = ObserverState(
        workspace=str(Path.cwd()),
        rounds=1,
        strategy="full",
        backend_url="http://127.0.0.1:49977",
        output_dir=str(Path.cwd()),
        projection_enabled=True,
        projection_transport="ws",
        projection_focus="all",
    )
    state.update_projection(
        connected=True,
        transport_used="ws",
        error="",
        panels=projection.get_panels(),
    )

    panel = state._render_projection()
    console = Console(record=True, width=180)
    console.print(panel)
    rendered = console.export_text()

    assert "status=connected transport=ws focus=all" in rendered
    assert "◈ Chain Status  ·  角色链路状态" in rendered
    assert "◈ Runtime Events  ·  运行时事件流" in rendered
    assert "[thinking_chunk]" not in rendered
    assert "[tool_call]" not in rendered

    reasoning_panel = state._render_reasoning()
    console = Console(record=True, width=180)
    console.print(reasoning_panel)
    reasoning_rendered = console.export_text()
    assert "◈ Reasoning  ·  推理思考过程" in reasoning_rendered
    assert "💭 🧭 PM 思考中" in reasoning_rendered
    assert "🛠 🧭 PM 工具调用" in reasoning_rendered


def test_projection_runtime_events_collapse_repeated_role_events() -> None:
    state = ObserverState(
        workspace=str(Path.cwd()),
        rounds=1,
        strategy="full",
        backend_url="http://127.0.0.1:49977",
        output_dir=str(Path.cwd()),
        projection_enabled=True,
        projection_transport="ws",
        projection_focus="all",
    )
    repeated_events = [
        {
            "timestamp": "2026-03-09T19:44:11Z",
            "type": "role_event:qa",
            "content": "turn_completed (events_20260311.jsonl)",
        }
        for _ in range(4)
    ]
    state.update_projection(
        connected=True,
        transport_used="ws",
        error="",
        panels={
            "chain_status": [],
            "llm_reasoning": [],
            "dialogue_stream": [],
            "tool_activity": [],
            "realtime_events": repeated_events,
        },
    )

    role_events = [entry for entry in state.projection_events if entry.get("kind") == "role_event:qa"]
    assert len(role_events) == 1
    assert role_events[0]["count"] == 4
    assert role_events[0]["label"] == "QA 角色事件"
    assert '"' not in role_events[0]["detail"]

    panel = state._render_projection()
    console = Console(record=True, width=180)
    console.print(panel)
    rendered = console.export_text()
    assert "QA 角色事件: turn_completed (events_20260311.jsonl) ×4" in rendered


def test_projection_reasoning_formats_embedded_tool_call_json() -> None:
    state = ObserverState(
        workspace=str(Path.cwd()),
        rounds=1,
        strategy="full",
        backend_url="http://127.0.0.1:49977",
        output_dir=str(Path.cwd()),
        projection_enabled=True,
        projection_transport="ws",
        projection_focus="all",
    )
    state.update_projection(
        connected=True,
        transport_used="ws",
        error="",
        panels={
            "chain_status": [],
            "llm_reasoning": [
                {
                    "timestamp": "2026-03-09T19:43:54Z",
                    "role": "architect",
                    "event_type": "content_preview",
                    "content": '[TOOL_CALL]{"tool":"glob","pattern":"**/*","path":"."}[/TOOL_CALL]',
                }
            ],
            "dialogue_stream": [],
            "tool_activity": [],
            "realtime_events": [],
        },
    )

    assert len(state.projection_llm) == 1
    entry = list(state.projection_llm)[0]
    assert entry.get("event_type") == "tool_call"
    display = str(entry.get("display") or "")
    assert "glob(" in display
    assert "pattern=**/*" in display
    assert "path=." in display
    assert "[TOOL_CALL]" not in display

    panel = state._render_projection()
    console = Console(record=True, width=180)
    console.print(panel)
    rendered = console.export_text()
    reasoning_panel = state._render_reasoning()
    console = Console(record=True, width=180)
    console.print(reasoning_panel)
    reasoning_rendered = console.export_text()
    assert "🛠 🏛 ARCHITECT 工具调用" in reasoning_rendered
    assert "glob(path=., pattern=**/*)" in reasoning_rendered


def test_projection_reasoning_formats_truncated_tool_call_json() -> None:
    state = ObserverState(
        workspace=str(Path.cwd()),
        rounds=1,
        strategy="full",
        backend_url="http://127.0.0.1:49977",
        output_dir=str(Path.cwd()),
        projection_enabled=True,
        projection_transport="ws",
        projection_focus="all",
    )
    state.update_projection(
        connected=True,
        transport_used="ws",
        error="",
        panels={
            "chain_status": [],
            "llm_reasoning": [
                {
                    "timestamp": "2026-03-09T19:43:55Z",
                    "role": "architect",
                    "event_type": "content_preview",
                    "content": '[TOOL_CALL]{"tool":"glob","pattern":"**/*","path":"."',
                }
            ],
            "dialogue_stream": [],
            "tool_activity": [],
            "realtime_events": [],
        },
    )

    entry = list(state.projection_llm)[0]
    assert entry.get("event_type") == "tool_call"
    display = str(entry.get("display") or "")
    assert "glob(" in display
    assert "pattern=**/*" in display
    assert "path=." in display


def test_projection_reasoning_window_bottom_aligns_latest_lines() -> None:
    state = ObserverState(
        workspace=str(Path.cwd()),
        rounds=1,
        strategy="full",
        backend_url="http://127.0.0.1:49977",
        output_dir=str(Path.cwd()),
        projection_enabled=True,
        projection_transport="ws",
        projection_focus="all",
    )
    viewport = state._build_tail_window(
        [
            "12:00:01 💭 🧭 PM 思考中: line-1",
            "12:00:02 💭 🧭 PM 思考中: line-2",
        ],
        viewport_lines=observer_module.PROJECTION_REASONING_VIEWPORT_LINES,
        max_chars=observer_module.PROJECTION_REASONING_LINE_MAX_CHARS,
    )

    assert len(viewport) == observer_module.PROJECTION_REASONING_VIEWPORT_LINES
    assert all(not line for line in viewport[:-2])
    assert viewport[-2].endswith("line-1")
    assert viewport[-1].endswith("line-2")


def test_projection_reasoning_window_truncates_long_line() -> None:
    state = ObserverState(
        workspace=str(Path.cwd()),
        rounds=1,
        strategy="full",
        backend_url="http://127.0.0.1:49977",
        output_dir=str(Path.cwd()),
        projection_enabled=True,
        projection_transport="ws",
        projection_focus="all",
    )
    max_chars = observer_module.PROJECTION_REASONING_LINE_MAX_CHARS
    long_line = f"12:00:03 💭 🧭 PM 思考中: {'x' * (max_chars + 64)}"
    viewport = state._build_tail_window(
        [long_line],
        viewport_lines=1,
        max_chars=max_chars,
    )

    assert len(viewport) == 1
    assert len(viewport[0]) == max_chars
    assert viewport[0].endswith("...")


def test_extract_workspace_from_settings_line_parses_runner_output() -> None:
    target_workspace = str((Path.cwd() / "tmp-observer-workspace").resolve())
    line = f"[settings] Workspace 已配置: {target_workspace} | Ramdisk: X:/tests-agent-stress-runtime | Runtime Root: X:/runtime"

    extracted = _extract_workspace_from_settings_line(line)

    assert extracted == target_workspace


def test_extract_runtime_root_from_settings_line_parses_runner_output() -> None:
    target_runtime_root = str((Path.cwd() / "tmp-observer-runtime").resolve())
    line = (
        f"[settings] Workspace 已配置: C:/Temp/demo "
        f"| Ramdisk: X:/tests-agent-stress-runtime | Runtime Root: {target_runtime_root}"
    )

    extracted = _extract_runtime_root_from_settings_line(line)

    assert extracted == target_runtime_root


def test_runtime_projection_retarget_workspace_refreshes_urls() -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="test-token",
        workspace=str(Path.cwd()),
    )
    old_ws_url = projection.ws_url
    new_workspace = str((Path.cwd() / "workspace-target").resolve())

    async def _retarget() -> None:
        changed = await projection.retarget_workspace(new_workspace)
        assert changed is True

    asyncio.run(_retarget())

    assert projection.workspace == new_workspace
    assert projection.ws_url != old_ws_url
    assert "workspace=" in projection.ws_url
    assert urllib.parse.quote(new_workspace) in projection.ws_url


def test_runtime_projection_retarget_runtime_root_updates_local_poll_path() -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="test-token",
        workspace=str(Path.cwd()),
    )
    new_runtime_root = str((Path.cwd() / "runtime-target").resolve())

    changed = projection.retarget_runtime_root(new_runtime_root)

    assert changed is True
    assert projection.runtime_root == Path(new_runtime_root)


def test_stream_lines_retargets_projection_workspace_from_settings_line() -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="test-token",
        workspace=str(Path.cwd()),
    )
    target_workspace = str((Path.cwd() / "workspace-stream-target").resolve())
    line = f"[settings] Workspace 已配置: {target_workspace} | Ramdisk: X:/tests-agent-stress-runtime\n"

    sink = io.StringIO()
    state = ObserverState(
        workspace=str(Path.cwd()),
        rounds=1,
        strategy="rotation",
        backend_url="http://127.0.0.1:49977",
        output_dir=str(Path.cwd()),
    )

    async def _consume() -> None:
        stream = asyncio.StreamReader()
        stream.feed_data(line.encode("utf-8"))
        stream.feed_eof()
        await _stream_lines(
            stream,
            state=state,
            sink=sink,
            projection=projection,
        )

    asyncio.run(_consume())

    assert projection.workspace == target_workspace
    assert "[settings] Workspace 已配置" in sink.getvalue()


def test_stream_lines_retargets_projection_runtime_root_from_settings_line() -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="test-token",
        workspace=str(Path.cwd()),
    )
    target_runtime_root = str((Path.cwd() / "runtime-stream-target").resolve())
    line = (
        f"[settings] Workspace 已配置: C:/Temp/demo "
        f"| Ramdisk: X:/tests-agent-stress-runtime | Runtime Root: {target_runtime_root}\n"
    )

    sink = io.StringIO()
    state = ObserverState(
        workspace=str(Path.cwd()),
        rounds=1,
        strategy="rotation",
        backend_url="http://127.0.0.1:49977",
        output_dir=str(Path.cwd()),
    )

    async def _consume() -> None:
        stream = asyncio.StreamReader()
        stream.feed_data(line.encode("utf-8"))
        stream.feed_eof()
        await _stream_lines(
            stream,
            state=state,
            sink=sink,
            projection=projection,
        )

    asyncio.run(_consume())

    assert projection.runtime_root == Path(target_runtime_root)
    assert "runtime root retargeted" in sink.getvalue()


def test_observer_state_normalize_timestamp_converts_zulu_to_local_time() -> None:
    raw = "2026-03-11T18:28:05Z"
    expected = datetime.fromisoformat("2026-03-11T18:28:05+00:00").astimezone().strftime("%H:%M:%S")

    assert ObserverState._normalize_timestamp(raw) == expected


def test_runtime_projection_connect_ws_does_not_send_legacy_subscribe(monkeypatch) -> None:
    class _DummyWebSocket:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, payload: str) -> None:
            self.sent.append(payload)

        async def close(self) -> None:
            return None

    dummy_ws = _DummyWebSocket()

    async def _fake_connect(url: str, ping_interval: int = 30):  # noqa: ANN202
        assert "v2/ws/runtime" in url
        assert ping_interval == 30
        return dummy_ws

    monkeypatch.setattr(observer_module.websockets, "connect", _fake_connect)

    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="test-token",
        workspace=str(Path.cwd()),
    )

    async def _connect() -> None:
        ok = await projection._connect_ws()
        assert ok is True

    asyncio.run(_connect())

    assert dummy_ws.sent == []


def test_runtime_projection_polls_local_adapter_debug_logs(tmp_path: Path) -> None:
    workspace = tmp_path / "stress-workspace"
    log_path = workspace / ".polaris" / "runtime" / "roles" / "director" / "logs" / "adapter_debug_20260310.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "timestamp": "2026-03-10T10:00:00Z",
            "task_id": "task-1",
            "event": "first_llm_response",
            "payload": {"success": True, "content_len": 1024, "validation_score": 88.0},
        },
        {
            "timestamp": "2026-03-10T10:00:01Z",
            "task_id": "task-1",
            "event": "first_tool_results",
            "payload": {
                "items": [
                    {"tool": "write_file", "success": True},
                    {"tool": "search_code", "success": True},
                ]
            },
        },
    ]
    log_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
        encoding="utf-8",
    )

    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="test-token",
        workspace=str(workspace),
    )

    projection._poll_local_projection_once()
    panels = projection.get_panels()

    assert any("first_llm_response" in row.get("content", "") for row in panels["llm_reasoning"])
    assert any("write_file -> ok" in row.get("content", "") for row in panels["tool_activity"])
    assert any("write_file -> ok" in row.get("content", "") for row in panels["llm_reasoning"])


def test_runtime_projection_polls_workspace_fallback_when_runtime_root_has_no_logs(tmp_path: Path) -> None:
    workspace = tmp_path / "stress-workspace"
    workspace_log = (
        workspace
        / ".polaris"
        / "runtime"
        / "roles"
        / "director"
        / "logs"
        / "adapter_debug_20260310.jsonl"
    )
    workspace_log.parent.mkdir(parents=True, exist_ok=True)
    workspace_log.write_text(
        json.dumps(
            {
                "timestamp": "2026-03-10T10:00:00Z",
                "task_id": "task-1",
                "event": "first_tool_results",
                "payload": {"items": [{"tool": "write_file", "success": True}]},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    external_runtime_root = tmp_path / "ramdisk-runtime" / "runtime"
    external_runtime_root.mkdir(parents=True, exist_ok=True)

    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="test-token",
        workspace=str(workspace),
    )
    projection.retarget_runtime_root(str(external_runtime_root))

    projection._poll_local_projection_once()
    panels = projection.get_panels()

    assert any("write_file -> ok" in row.get("content", "") for row in panels["tool_activity"])
