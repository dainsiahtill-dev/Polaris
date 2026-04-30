from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from polaris.kernelone.storage import resolve_runtime_path

from .observer.projection import RuntimeProjection
from .observer.renderers import (
    _format_taskboard_execution_backend_label,
    _map_taskboard_status_label,
)
from .observer.state import ObserverState


def _write_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False)
    path.write_text(f"{line}\n", encoding="utf-8")


def test_local_projection_polling_is_disabled(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    events_path = Path(resolve_runtime_path(str(workspace), "runtime/roles/pm/logs/events_20260312.jsonl"))
    _write_jsonl(
        events_path,
        {
            "timestamp": "2026-03-12T03:15:12.018223",
            "role": "pm",
            "type": "turn_completed",
            "data": {
                "has_tool_calls": True,
                "tool_results_count": 1,
                "thinking_preview": "先分析任务，再拆解执行步骤。",
                "content_preview": "已生成任务合同并准备进入执行阶段。",
                "tool_details": [
                    {
                        "tool": "list_directory",
                        "success": True,
                        "error": None,
                    }
                ],
            },
        },
    )

    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="local-token",
        workspace=str(workspace),
        transport="auto",
        focus="all",
    )

    with pytest.raises(RuntimeError, match="Local polling has been removed"):
        projection._poll_local_projection_once()


@pytest.mark.asyncio
async def test_retarget_workspace_refreshes_ws_url(tmp_path: Path) -> None:
    old_workspace = tmp_path / "old_ws"
    new_workspace = tmp_path / "new_ws"
    token = "tok/with+special?chars"

    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token=token,
        workspace=str(old_workspace),
    )
    projection.panels["taskboard_status"].append(
        {
            "timestamp": "2026-03-12T03:20:00+00:00",
            "summary": "total=1 ready=0 pending=0 running=1 completed=0 failed=0 blocked=0",
            "items": [{"id": "1", "subject": "切换前任务", "status": "in_progress"}],
            "source": "status",
        }
    )

    changed = await projection.retarget_workspace(str(new_workspace))
    assert changed is True

    ws_query = parse_qs(urlparse(projection.ws_url).query)
    assert ws_query.get("workspace", [""])[0] == str(new_workspace.resolve())
    assert ws_query.get("token", [""])[0] == token
    assert projection.panels.get("taskboard_status")


@pytest.mark.asyncio
async def test_status_message_populates_taskboard_panel(tmp_path: Path) -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="token",
        workspace=str(tmp_path / "workspace"),
    )

    await projection._on_message(
        {
            "type": "status",
            "timestamp": "2026-03-12T03:30:00+00:00",
            "pm_status": {"running": True},
            "director_status": {
                "running": True,
                "status": {
                    "state": "RUNNING",
                    "tasks": {
                        "total": 2,
                        "ready_queue_size": 1,
                        "by_status": {
                            "RUNNING": 1,
                            "COMPLETED": 0,
                            "FAILED": 0,
                            "BLOCKED": 0,
                        },
                        "task_rows": [
                            {"id": "1", "subject": "实现存储层", "status": "RUNNING"},
                            {"id": "2", "subject": "补充单元测试", "status": "PENDING"},
                        ],
                    },
                },
            },
        }
    )

    panels = projection.get_panels()
    assert panels.get("taskboard_status")
    latest = panels["taskboard_status"][-1]
    assert "total=2" in str(latest.get("summary") or "")
    assert "pending=1" in str(latest.get("summary") or "")
    assert len(latest.get("items") or []) == 2


@pytest.mark.asyncio
async def test_taskboard_keeps_last_non_empty_snapshot_when_empty_status_arrives(tmp_path: Path) -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="token",
        workspace=str(tmp_path / "workspace"),
    )

    await projection._on_message(
        {
            "type": "status",
            "timestamp": "2026-03-12T03:31:00+00:00",
            "director_status": {
                "running": True,
                "tasks": {
                    "total": 1,
                    "ready_queue_size": 0,
                    "by_status": {"RUNNING": 1},
                    "task_rows": [
                        {"id": "1", "subject": "实现存储层", "status": "RUNNING"},
                    ],
                },
            },
        }
    )
    first_panels = projection.get_panels()
    assert len(first_panels.get("taskboard_status") or []) == 1

    await projection._on_message(
        {
            "type": "status",
            "timestamp": "2026-03-12T03:31:02+00:00",
            "director_status": {
                "running": True,
                "tasks": {
                    "total": 0,
                    "ready_queue_size": 0,
                    "by_status": {},
                    "task_rows": [],
                },
            },
        }
    )

    panels = projection.get_panels()
    rows = panels.get("taskboard_status") or []
    assert len(rows) == 1
    assert "total=1" in str(rows[-1].get("summary") or "")


@pytest.mark.asyncio
async def test_file_edit_message_populates_code_diff_panel(tmp_path: Path) -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="token",
        workspace=str(tmp_path / "workspace"),
    )

    await projection._on_message(
        {
            "type": "file_edit",
            "timestamp": "2026-03-12T04:00:00+00:00",
            "event": {
                "file_path": "src/fastapi_entrypoint.py",
                "operation": "modify",
                "patch": (
                    "--- a/src/fastapi_entrypoint.py\n"
                    "+++ b/src/fastapi_entrypoint.py\n"
                    "@@ -1,2 +1,2 @@\n"
                    "-print('old')\n"
                    "+print('new')\n"
                ),
                "added_lines": 1,
                "deleted_lines": 1,
                "modified_lines": 1,
            },
        }
    )

    panels = projection.get_panels()
    assert panels.get("code_diff")
    latest = panels["code_diff"][-1]
    assert latest.get("file_path") == "src/fastapi_entrypoint.py"
    assert latest.get("operation") == "modify"
    assert "+print('new')" in str(latest.get("patch") or "")
    assert latest.get("added_lines") == 1
    assert latest.get("deleted_lines") == 1


def test_observer_state_consumes_taskboard_rows() -> None:
    state = ObserverState(
        workspace="C:/Temp/ws",
        rounds=1,
        strategy="complexity_asc",
        backend_url="http://127.0.0.1:49977",
        output_dir="C:/Temp/ws/stress_reports",
        projection_enabled=True,
    )

    state.update_projection(
        connected=True,
        transport_used="ws",
        error="",
        panels={
            "chain_status": [],
            "llm_reasoning": [],
            "dialogue_stream": [],
            "tool_activity": [],
            "code_diff": [],
            "realtime_events": [],
            "taskboard_status": [
                {
                    "timestamp": "2026-03-12T03:30:00+00:00",
                    "summary": "total=2 ready=0 pending=1 running=1 completed=0 failed=0 blocked=0",
                    "items": [
                        {"id": "1", "subject": "实现存储层", "status": "RUNNING", "qa_state": ""},
                        {"id": "2", "subject": "补充单元测试", "status": "PENDING", "qa_state": "rework"},
                    ],
                }
            ],
        },
    )

    assert state.projection_taskboard_summary
    assert len(state.projection_taskboard_items) == 2
    assert len(state.projection_taskboard_todos) == 2
    assert any("执行中" in row for row in state.projection_taskboard_items)
    assert any("未开始（QA打回）" in row for row in state.projection_taskboard_items)
    assert any(todo.get("status") in {"running", "in_progress"} for todo in state.projection_taskboard_todos)


def test_taskboard_render_highlights_running_director_task() -> None:
    state = ObserverState(
        workspace="C:/Temp/ws",
        rounds=1,
        strategy="complexity_asc",
        backend_url="http://127.0.0.1:49977",
        output_dir="C:/Temp/ws/stress_reports",
        projection_enabled=True,
    )

    state.update_projection(
        connected=True,
        transport_used="ws",
        error="",
        panels={
            "chain_status": [],
            "llm_reasoning": [],
            "dialogue_stream": [],
            "tool_activity": [],
            "code_diff": [],
            "realtime_events": [],
            "taskboard_status": [
                {
                    "timestamp": "2026-03-12T03:30:00+00:00",
                    "summary": "total=2 ready=0 pending=1 running=1 completed=0 failed=0 blocked=0",
                    "items": [
                        {"id": "1", "subject": "实现存储层", "status": "RUNNING", "qa_state": ""},
                        {"id": "2", "subject": "补充单元测试", "status": "PENDING", "qa_state": ""},
                    ],
                }
            ],
        },
    )

    projection_panel = state._render_projection()
    rendered_lines = []
    renderable = projection_panel.renderable
    for item in getattr(renderable, "renderables", []):
        rendered_lines.append(getattr(item, "plain", str(item)))
    rendered_text = "\n".join(rendered_lines)
    assert re.search(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]\s+实现存储层（执行中）", rendered_text)


def test_taskboard_render_shows_running_fallback_when_details_missing() -> None:
    state = ObserverState(
        workspace="C:/Temp/ws",
        rounds=1,
        strategy="complexity_asc",
        backend_url="http://127.0.0.1:49977",
        output_dir="C:/Temp/ws/stress_reports",
        projection_enabled=True,
    )

    state.update_projection(
        connected=True,
        transport_used="ws",
        error="",
        panels={
            "chain_status": [],
            "llm_reasoning": [],
            "dialogue_stream": [],
            "tool_activity": [],
            "code_diff": [],
            "realtime_events": [],
            "taskboard_status": [
                {
                    "timestamp": "2026-03-12T03:30:00+00:00",
                    "summary": "total=1 ready=0 pending=0 running=1 completed=0 failed=0 blocked=0",
                    "items": [
                        {"id": "1", "subject": "补充单元测试", "status": "PENDING", "qa_state": ""},
                    ],
                }
            ],
        },
    )

    projection_panel = state._render_projection()
    rendered_lines = []
    renderable = projection_panel.renderable
    for item in getattr(renderable, "renderables", []):
        rendered_lines.append(getattr(item, "plain", str(item)))
    rendered_text = "\n".join(rendered_lines)
    assert re.search(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]\s+Director 正在执行任务（详情同步中）", rendered_text)


def test_observer_state_consumes_code_diff_rows() -> None:
    state = ObserverState(
        workspace="C:/Temp/ws",
        rounds=1,
        strategy="complexity_asc",
        backend_url="http://127.0.0.1:49977",
        output_dir="C:/Temp/ws/stress_reports",
        projection_enabled=True,
    )

    state.update_projection(
        connected=True,
        transport_used="ws",
        error="",
        panels={
            "chain_status": [],
            "llm_reasoning": [],
            "dialogue_stream": [],
            "tool_activity": [],
            "taskboard_status": [],
            "realtime_events": [],
            "code_diff": [
                {
                    "timestamp": "2026-03-12T04:05:00+00:00",
                    "file_path": "src/fastapi_entrypoint.py",
                    "operation": "modify",
                    "patch": (
                        "--- a/src/fastapi_entrypoint.py\n"
                        "+++ b/src/fastapi_entrypoint.py\n"
                        "@@ -1,2 +1,2 @@\n"
                        "-print('old')\n"
                        "+print('new')\n"
                    ),
                    "added_lines": 1,
                    "deleted_lines": 1,
                    "modified_lines": 1,
                }
            ],
        },
    )

    assert len(state.projection_code_diffs) == 1
    latest = state.projection_code_diffs[0]
    preview_lines = latest.get("preview_lines") if isinstance(latest, dict) else []
    assert isinstance(preview_lines, list)
    assert any(str(line).startswith("-print('old')") for line in preview_lines)
    assert any(str(line).startswith("+print('new')") for line in preview_lines)


def test_llm_completed_is_not_reopened_by_content_preview() -> None:
    state = ObserverState(
        workspace="C:/Temp/ws",
        rounds=1,
        strategy="complexity_asc",
        backend_url="http://127.0.0.1:49977",
        output_dir="C:/Temp/ws/stress_reports",
        projection_enabled=True,
    )

    state._update_llm_request_state(
        [
            {"event_type": "llm_waiting", "role": "architect"},
            {"event_type": "llm_completed", "role": "architect"},
            {"event_type": "content_preview", "role": "architect"},
        ]
    )

    assert state.llm_request_pending is False
    assert state.llm_request_last_outcome == "completed"


def test_taskboard_status_label_mapping_matches_todo_board_contract() -> None:
    assert _map_taskboard_status_label("pending", "") == "未开始"
    assert _map_taskboard_status_label("pending", "", "resumable") == "待恢复"
    assert _map_taskboard_status_label("in_progress", "") == "执行中"
    assert _map_taskboard_status_label("in_progress", "", "resumed") == "恢复执行中"
    assert _map_taskboard_status_label("completed", "pending") == "已完成，等待QA验证"
    assert _map_taskboard_status_label("failed", "") == "失败"
    assert _map_taskboard_status_label("blocked", "") == "阻塞"
    assert (
        _format_taskboard_execution_backend_label("projection_generate", "scenario_alpha") == "投影生成:scenario_alpha"
    )
    assert _format_taskboard_execution_backend_label("projection_refresh_mapping") == "回映刷新"
    assert _format_taskboard_execution_backend_label("projection_reproject", "scenario_beta") == "重投影:scenario_beta"
    assert _format_taskboard_execution_backend_label("code_edit") == "代码编辑"


def test_taskboard_render_shows_execution_backend_label() -> None:
    state = ObserverState(
        workspace="C:/Temp/ws",
        rounds=1,
        strategy="complexity_asc",
        backend_url="http://127.0.0.1:49977",
        output_dir="C:/Temp/ws/stress_reports",
        projection_enabled=True,
    )

    state.update_projection(
        connected=True,
        transport_used="ws",
        error="",
        panels={
            "chain_status": [],
            "llm_reasoning": [],
            "dialogue_stream": [],
            "tool_activity": [],
            "code_diff": [],
            "realtime_events": [],
            "taskboard_status": [
                {
                    "timestamp": "2026-03-12T03:30:00+00:00",
                    "summary": "total=1 ready=0 pending=0 running=1 completed=0 failed=0 blocked=0",
                    "items": [
                        {
                            "id": "task-1",
                            "subject": "生成受控投影子项目",
                            "status": "RUNNING",
                            "qa_state": "",
                            "execution_backend": "projection_generate",
                            "projection_scenario": "scenario_alpha",
                        },
                    ],
                }
            ],
        },
    )

    projection_panel = state._render_projection()
    rendered_lines = []
    renderable = projection_panel.renderable
    for item in getattr(renderable, "renderables", []):
        rendered_lines.append(getattr(item, "plain", str(item)))
    rendered_text = "\n".join(rendered_lines)
    assert "投影生成:scenario_alpha" in rendered_text


def test_observer_state_builds_create_diff_preview_from_raw_content() -> None:
    state = ObserverState(
        workspace="C:/Temp/ws",
        rounds=1,
        strategy="complexity_asc",
        backend_url="http://127.0.0.1:49977",
        output_dir="C:/Temp/ws/stress_reports",
        projection_enabled=True,
    )

    preview = state._build_patch_preview_lines(
        patch="line_a\nline_b",
        operation="create",
        max_lines=6,
    )
    assert preview
    assert all(str(line).startswith("+") for line in preview if str(line))


def test_observer_state_humanizes_tool_call_markers_in_reasoning_panel() -> None:
    state = ObserverState(
        workspace="C:/Temp/ws",
        rounds=1,
        strategy="complexity_asc",
        backend_url="http://127.0.0.1:49977",
        output_dir="C:/Temp/ws/stress_reports",
        projection_enabled=True,
    )

    state.update_projection(
        connected=True,
        transport_used="ws",
        error="",
        panels={
            "chain_status": [],
            "llm_reasoning": [
                {
                    "timestamp": "2026-03-12T04:20:00+00:00",
                    "role": "pm",
                    "event_type": "content_preview",
                    "content": ('[TOOL_CALL]{"tool":"list_directory","args":{"path":"."}}[/TOOL_CALL]'),
                }
            ],
            "dialogue_stream": [],
            "tool_activity": [],
            "taskboard_status": [],
            "code_diff": [],
            "realtime_events": [],
        },
    )

    assert state.projection_llm
    display = str(state.projection_llm[-1].get("display") or "")
    assert "计划调用工具" in display
    assert "[TOOL_CALL]" not in display


def test_observer_state_humanizes_output_json_preview() -> None:
    state = ObserverState(
        workspace="C:/Temp/ws",
        rounds=1,
        strategy="complexity_asc",
        backend_url="http://127.0.0.1:49977",
        output_dir="C:/Temp/ws/stress_reports",
        projection_enabled=True,
    )

    state.update_projection(
        connected=True,
        transport_used="ws",
        error="",
        panels={
            "chain_status": [],
            "llm_reasoning": [
                {
                    "timestamp": "2026-03-12T04:21:00+00:00",
                    "role": "architect",
                    "event_type": "content_preview",
                    "content": '<output>{"tasks":[{"id":"TASK-1"}]}</output>',
                }
            ],
            "dialogue_stream": [],
            "tool_activity": [],
            "taskboard_status": [],
            "code_diff": [],
            "realtime_events": [],
        },
    )

    display = str(state.projection_llm[-1].get("display") or "")
    assert "任务清单草案" in display
    assert "<output>" not in display


@pytest.mark.asyncio
async def test_status_message_infers_qa_state_from_metadata(tmp_path: Path) -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="token",
        workspace=str(tmp_path / "workspace"),
    )

    await projection._on_message(
        {
            "type": "status",
            "timestamp": "2026-03-12T03:40:00+00:00",
            "pm_status": {"running": False},
            "director_status": {
                "running": True,
                "tasks": {
                    "total": 2,
                    "ready_queue_size": 0,
                    "by_status": {"COMPLETED": 1, "FAILED": 1},
                    "task_rows": [
                        {
                            "id": "11",
                            "subject": "修复认证链路",
                            "status": "COMPLETED",
                            "metadata": {
                                "adapter_result": {
                                    "qa_required_for_final_verdict": True,
                                    "qa_passed": None,
                                }
                            },
                        },
                        {
                            "id": "12",
                            "subject": "补齐异常测试",
                            "status": "FAILED",
                            "metadata": {"qa_rework_exhausted": True},
                        },
                    ],
                },
            },
        }
    )

    latest = projection.get_panels()["taskboard_status"][-1]
    items = latest.get("items") or []
    assert len(items) == 2
    qa_by_id = {str(item.get("id") or ""): str(item.get("qa_state") or "") for item in items}
    assert qa_by_id.get("11") == "pending"
    assert qa_by_id.get("12") == "exhausted"


@pytest.mark.asyncio
async def test_task_trace_refs_taskboard_populates_taskboard_panel(tmp_path: Path) -> None:
    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="token",
        workspace=str(tmp_path / "workspace"),
    )

    await projection._on_message(
        {
            "type": "task_trace",
            "timestamp": "2026-03-12T03:42:00+00:00",
            "event": {
                "type": "task_trace",
                "event": {
                    "task_id": "task-9",
                    "step_title": "Director claimed TaskBoard task",
                    "refs": {
                        "taskboard": {
                            "counts": {
                                "total": 2,
                                "ready": 1,
                                "pending": 0,
                                "in_progress": 1,
                                "completed": 0,
                                "failed": 0,
                                "blocked": 0,
                            },
                            "samples": {
                                "in_progress": [
                                    {"id": "task-9", "subject": "执行迁移任务"},
                                ],
                                "ready": [
                                    {"id": "task-10", "subject": "补充看板回归"},
                                ],
                            },
                        }
                    },
                },
            },
        }
    )

    rows = projection.get_panels().get("taskboard_status") or []
    assert rows
    latest = rows[-1]
    assert "task_trace" in str(latest.get("source") or "")
    assert "total=2" in str(latest.get("summary") or "")
    items = latest.get("items")
    assert isinstance(items, list)
    assert any(str(item.get("id") or "") == "task-9" for item in items if isinstance(item, dict))


def test_local_adapter_debug_polling_is_disabled(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    log_path = Path(resolve_runtime_path(str(workspace), "runtime/roles/director/logs/adapter_debug_20260312.jsonl"))
    _write_jsonl(
        log_path,
        {
            "timestamp": "2026-03-12T03:45:00+00:00",
            "task_id": "task-1",
            "event": "taskboard_completed",
            "payload": {
                "taskboard": {
                    "counts": {
                        "total": 2,
                        "ready": 0,
                        "pending": 0,
                        "in_progress": 0,
                        "completed": 2,
                        "failed": 0,
                        "blocked": 0,
                    },
                    "samples": {
                        "completed": [
                            {"id": "1", "subject": "任务A", "qa_state": "pending"},
                            {"id": "2", "subject": "任务B", "qa_state": "passed"},
                        ]
                    },
                }
            },
        },
    )

    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="token",
        workspace=str(workspace),
    )
    with pytest.raises(RuntimeError, match="Local polling has been removed"):
        projection._poll_local_projection_once()


def test_local_role_output_polling_is_disabled(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    output_path = (
        workspace
        / ".polaris"
        / "runtime"
        / "roles"
        / "architect"
        / "outputs"
        / "task-0-architect_initial_20260312T083601246230Z.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "role": "architect",
                "task_id": "task-0-architect",
                "success": True,
                "content": '```json\\n{\\n  \\"plan_markdown\\": \\"# 计划\\"\\n}\\n```',
                "result_error": "",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    projection = RuntimeProjection(
        backend_url="http://127.0.0.1:49977",
        token="token",
        workspace=str(workspace),
    )
    with pytest.raises(RuntimeError, match="Local polling has been removed"):
        projection._poll_local_projection_once()
