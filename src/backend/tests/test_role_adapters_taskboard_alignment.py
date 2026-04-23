"""Regression tests for role adapters aligned with TaskBoard current API."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal import director_execution_backend as director_execution_backend_module
from polaris.cells.roles.adapters.internal.director_adapter import DirectorAdapter
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter
from polaris.cells.roles.adapters.internal.qa_adapter import QAAdapter
from polaris.kernelone.storage import resolve_runtime_path


def test_pm_adapter_fallback_domain_prefers_workspace_slug_over_directive_noise(tmp_path: Path) -> None:
    workspace = tmp_path / "expense-tracker"
    workspace.mkdir(parents=True, exist_ok=True)
    adapter = PMAdapter(workspace=str(workspace))

    token = adapter._derive_domain_token("上轮失败摘要包含 todo task 关键词")

    assert token == "expense"


def test_director_ephemeral_task_includes_pending_taskboard_contract(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    adapter.task_board.create(
        subject="实现expense账单实体",
        description="创建账单模型与校验",
        metadata={
            "scope": "src/expense, tests/",
            "steps": ["实现模型", "补充测试"],
        },
    )

    task = adapter._build_ephemeral_task("task-0-director", {"input": "Execute tasks from PM"})

    assert "TaskBoard" in str(task.get("description") or "")
    assert "实现expense账单实体" in str(task.get("description") or "")


def test_director_snapshot_uses_nanosecond_mtime(tmp_path: Path) -> None:
    source_file = tmp_path / "src" / "expense.py"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("value = 1\n", encoding="utf-8")

    adapter = DirectorAdapter(workspace=str(tmp_path))
    baseline = adapter._collect_workspace_code_files()
    source_file.write_text("value = 2\n", encoding="utf-8")
    current = adapter._collect_workspace_code_files()

    rel_path = "src/expense.py"
    assert rel_path in baseline
    assert rel_path in current
    assert baseline[rel_path] != current[rel_path]


def test_director_snapshot_ignores_mtime_only_drift(tmp_path: Path) -> None:
    source_file = tmp_path / "src" / "expense.py"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("value = 1\n", encoding="utf-8")

    adapter = DirectorAdapter(workspace=str(tmp_path))
    baseline = adapter._collect_workspace_code_files()

    stat_info = source_file.stat()
    os.utime(source_file, (stat_info.st_atime + 5, stat_info.st_mtime + 5))
    current = adapter._collect_workspace_code_files()

    rel_path = "src/expense.py"
    assert rel_path in baseline
    assert rel_path in current
    assert baseline[rel_path] == current[rel_path]


def test_director_adapter_removes_emergency_write_fallback_methods(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    assert not hasattr(adapter, "_build_emergency_file_plan")
    assert not hasattr(adapter, "_execute_emergency_write_plan")


@pytest.mark.asyncio
async def test_director_adapter_disables_internal_tool_rounds_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.adapters.internal import director_adapter as director_adapter_module
    adapter = DirectorAdapter(workspace=str(tmp_path))
    captured_context: dict[str, Any] = {}

    async def _fake_generate_role_response(*, context=None, **kwargs):  # noqa: ANN001
        del kwargs
        captured_context.clear()
        if isinstance(context, dict):
            captured_context.update(context)
        return {"response": "ok"}

    monkeypatch.delenv("KERNELONE_DIRECTOR_ENABLE_INTERNAL_TOOL_ROUNDS", raising=False)
    monkeypatch.setattr(director_adapter_module, "generate_role_response", _fake_generate_role_response)

    result = await adapter._call_role_llm("hello", context={})

    assert result["success"] is True
    assert captured_context.get("disable_internal_tool_rounds") is True


@pytest.mark.asyncio
async def test_director_adapter_can_enable_internal_tool_rounds_via_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.adapters.internal import director_adapter as director_adapter_module
    adapter = DirectorAdapter(workspace=str(tmp_path))
    captured_context: dict[str, Any] = {}

    async def _fake_generate_role_response(*, context=None, **kwargs):  # noqa: ANN001
        del kwargs
        captured_context.clear()
        if isinstance(context, dict):
            captured_context.update(context)
        return {"response": "ok"}

    monkeypatch.setenv("KERNELONE_DIRECTOR_ENABLE_INTERNAL_TOOL_ROUNDS", "1")
    monkeypatch.setattr(director_adapter_module, "generate_role_response", _fake_generate_role_response)

    result = await adapter._call_role_llm("hello", context={})

    assert result["success"] is True
    assert captured_context.get("disable_internal_tool_rounds") is not True


def test_director_selects_pending_board_task_when_orchestration_task_missing(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    adapter.task_board.create(subject="任务A", description="A", metadata={})
    adapter.task_board.create(subject="任务B", description="B", metadata={})

    selected = adapter._select_pending_board_task()

    assert selected is not None
    assert str(selected.get("subject") or "") == "任务A"


def test_director_taskboard_snapshot_includes_completed_qa_state(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    done_pending_qa = adapter.task_board.create(subject="任务待QA", description="A", metadata={})
    done_failed_qa = adapter.task_board.create(subject="任务QA未通过", description="B", metadata={})
    done_passed_qa = adapter.task_board.create(subject="任务QA通过", description="C", metadata={})

    adapter.task_board.update(
        done_pending_qa.id,
        status="completed",
        metadata={"adapter_result": {"qa_required_for_final_verdict": True, "qa_passed": None}},
    )
    adapter.task_board.update(
        done_failed_qa.id,
        status="completed",
        metadata={"adapter_result": {"qa_required_for_final_verdict": True, "qa_passed": False}},
    )
    adapter.task_board.update(
        done_passed_qa.id,
        status="completed",
        metadata={"adapter_result": {"qa_required_for_final_verdict": True, "qa_passed": True}},
    )

    snapshot = adapter._build_taskboard_observation_snapshot(sample_limit=10)
    completed_samples = snapshot.get("samples", {}).get("completed", [])
    qa_states = {
        str(item.get("id") or ""): str(item.get("qa_state") or "")
        for item in completed_samples
        if isinstance(item, dict)
    }

    assert qa_states.get(str(done_pending_qa.id)) == "pending"
    assert qa_states.get(str(done_failed_qa.id)) == "failed"
    assert qa_states.get(str(done_passed_qa.id)) == "passed"


def test_director_taskboard_snapshot_surfaces_running_task_without_duplicate_ready_rows(
    tmp_path: Path,
) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    running_task = adapter.task_board.create(
        subject="实现数据模型与本地持久化存储层",
        description="实现模型、仓储与序列化",
        metadata={"execution_backend": "code_edit"},
    )
    ready_task = adapter.task_board.create(
        subject="编写单元测试与集成验证",
        description="补齐测试与回归验证",
        metadata={"execution_backend": "code_edit"},
    )

    claimed = adapter.task_runtime.claim_execution(
        running_task.id,
        worker_id="director",
        role_id="director",
        run_id="run-observer-focus",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True

    snapshot = adapter._build_taskboard_observation_snapshot(sample_limit=10)
    counts = snapshot.get("counts", {})
    samples = snapshot.get("samples", {})

    assert int(counts.get("in_progress") or 0) == 1
    in_progress_samples = samples.get("in_progress", [])
    in_progress_ids = {
        str(item.get("id") or "")
        for item in in_progress_samples
        if isinstance(item, dict)
    }
    assert str(running_task.id) in in_progress_ids

    all_sample_ids = [
        str(item.get("id") or "")
        for bucket in samples.values()
        if isinstance(bucket, list)
        for item in bucket
        if isinstance(item, dict)
    ]
    assert all_sample_ids.count(str(ready_task.id)) == 1


def test_pm_adapter_preserves_execution_backend_metadata_on_board_tasks(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))

    created = adapter._create_board_tasks(
        [
            {
                "id": "TASK-PROJECTION-1",
                "title": "生成受控投影子项目",
                "goal": "通过 projection 生成传统代码结构",
                "description": "使用受控 projection 场景生成项目",
                "scope": "experiments/projection_lab",
                "steps": ["归一化需求", "生成项目", "运行验证"],
                "acceptance": ["生成成功", "验证通过"],
                "phase": "implementation",
                "assigned_to": "Director",
                "metadata": {
                    "execution_backend": "projection_generate",
                    "projection": {
                        "scenario_id": "scenario_alpha",
                        "project_slug": "projection_lab",
                    },
                },
            }
        ]
    )

    assert len(created) == 1
    metadata = created[0].get("metadata") if isinstance(created[0].get("metadata"), dict) else {}
    projection = metadata.get("projection") if isinstance(metadata.get("projection"), dict) else {}
    assert metadata.get("execution_backend") == "projection_generate"
    assert projection.get("scenario_id") == "scenario_alpha"
    assert projection.get("project_slug") == "projection_lab"


@pytest.mark.asyncio
async def test_pm_adapter_pm_stage_creates_tasks_with_current_taskboard_api(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))

    async def _fake_call_role_llm(message: str, context=None):  # noqa: ANN001
        del message, context
        return {
            "content": "- 建立记账模型: 定义交易实体与校验\n- 增加统计接口: 汇总月度支出\n",
            "success": True,
        }

    adapter._call_role_llm = _fake_call_role_llm  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-0-pm",
        input_data={"stage": "pm", "input": "生成任务"},
        context={"run_director": True},
    )

    assert result["success"] is True
    assert result.get("director_dispatched") is False
    assert int(result.get("tasks_created") or 0) >= 2
    artifacts = result.get("artifacts")
    assert isinstance(artifacts, list)
    assert any(
        str(item).replace("\\", "/").endswith("runtime/signals/pm_planning.pm.signals.json")
        for item in artifacts
    )
    pm_signal_file = Path(resolve_runtime_path(str(tmp_path), "runtime/signals/pm_planning.pm.signals.json"))
    payload = json.loads(pm_signal_file.read_text(encoding="utf-8"))
    rows = payload.get("signals") if isinstance(payload, dict) else []
    assert isinstance(rows, list)
    assert any(
        isinstance(item, dict) and str(item.get("code") or "") == "pm.execution.summary"
        for item in rows
    )
    board_tasks = adapter.task_board.list_all()
    assert len(board_tasks) >= 2
    assert all(task.subject for task in board_tasks)


@pytest.mark.asyncio
async def test_pm_adapter_projection_hint_synthesizes_generic_projection_contracts(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))

    async def _fake_call_role_llm(message: str, context=None):  # noqa: ANN001
        del message, context
        return {"content": "[TOOL_CALL]{\"tool_name\":\"noop\"}[/TOOL_CALL]"}

    adapter._call_role_llm = _fake_call_role_llm  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-projection-pm",
        input_data={
            "stage": "pm",
            "input": "生成受控投影计划",
            "metadata": {
                "execution_backend": "projection_generate",
                "projection": {
                    "scenario_id": "scenario_alpha",
                    "project_slug": "projection_lab",
                },
            },
        },
        context={"run_id": "factory-test"},
    )

    assert result["success"] is True
    board_tasks = adapter.task_board.list_all()
    assert len(board_tasks) >= 3
    first_metadata = board_tasks[0].metadata or {}
    projection = first_metadata.get("projection") if isinstance(first_metadata.get("projection"), dict) else {}
    assert first_metadata.get("execution_backend") == "projection_generate"
    assert projection.get("scenario_id") == "scenario_alpha"
    assert projection.get("project_slug") == "projection_lab"


@pytest.mark.asyncio
async def test_pm_adapter_runtime_exception_is_fail_closed(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))

    async def _boom_call_role_llm(message: str, context=None):  # noqa: ANN001
        del message, context
        raise RuntimeError("llm kernel offline")

    adapter._call_role_llm = _boom_call_role_llm  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-1-pm",
        input_data={"stage": "pm", "input": "生成任务"},
        context={"run_director": True},
    )

    assert result["success"] is False
    assert result.get("director_dispatched") is False
    assert "llm kernel offline" in str(result.get("error") or "")


def test_pm_adapter_extracts_embedded_json_contracts(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    response = (
        "下面是任务合同，请执行：\n"
        "{\"tasks\":[{\"id\":\"TASK-1\",\"title\":\"实现 expense 存储\",\"goal\":\"完成持久化\","
        "\"description\":\"实现仓储\",\"scope\":\"src/expense\",\"steps\":[\"设计\",\"实现\"],"
        "\"acceptance\":[\"测试通过\",\"可持久化\"],\"depends_on\":[],\"assigned_to\":\"Director\"}]}\n"
        "以上。"
    )

    contracts = adapter._extract_task_contracts(response, directive="实现记账功能")

    assert len(contracts) == 1
    assert "expense" in str(contracts[0].get("title") or "").lower()
    assert len(contracts[0].get("steps") or []) >= 2


def test_pm_adapter_extracts_numbered_markdown_tasks(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    response = (
        "1. **实现 expense 数据模型**：补齐字段与校验\n"
        "2. **实现 tracker 导入导出**：支持 JSON 导出\n"
        "3. 编写测试：覆盖核心流程\n"
    )

    contracts = adapter._extract_task_contracts(response, directive="实现记账功能")

    assert len(contracts) >= 3
    titles = [str(item.get("title") or "").lower() for item in contracts]
    assert any("expense" in item for item in titles)
    assert any("tracker" in item for item in titles)


def test_pm_adapter_extracts_task_sections_with_key_value_blocks(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    response = """
## Task 1: 实现 expense 模型
goal: 完成账单数据建模
scope: src/expense, tests/
steps:
- 定义模型字段
- 补充校验逻辑
acceptance:
- 模型可通过单元测试
- 校验规则覆盖关键边界

## Task 2: 实现统计接口
goal: 提供月度统计接口
scope: src/api, tests/
steps:
- 实现统计服务
- 暴露查询接口
acceptance:
- 集成测试覆盖统计路径
- 返回结构满足契约
depends_on: TASK-1
""".strip()

    contracts = adapter._extract_task_contracts(response, directive="实现记账功能")

    assert len(contracts) >= 2
    assert "expense" in str(contracts[0].get("title") or "").lower()
    assert len(contracts[0].get("steps") or []) >= 2
    assert len(contracts[0].get("acceptance") or []) >= 2
    assert "TASK-1" in list(contracts[1].get("depends_on") or [])


def test_pm_adapter_extracts_tasks_from_nested_plan_payload(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    response = json.dumps(
        {
            "plan": {
                "work_items": [
                    {
                        "id": "TASK-1",
                        "title": "实现 chat 房间模型",
                        "goal": "实现房间状态管理",
                        "description": "支持创建/切换房间",
                        "scope": "src/chat, tests/",
                        "steps": ["实现房间状态", "编写单元测试"],
                        "acceptance": ["执行 `pytest -q` 通过", "房间切换可用"],
                        "depends_on": [],
                        "assigned_to": "Director",
                    }
                ]
            }
        },
        ensure_ascii=False,
    )

    contracts = adapter._extract_task_contracts(response, directive="实现聊天室")

    assert len(contracts) == 1
    assert "chat" in str(contracts[0].get("title") or "").lower()
    assert len(contracts[0].get("acceptance") or []) >= 2


def test_pm_adapter_synthesized_contracts_are_execution_ready(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    contracts = adapter._synthesize_task_contracts_from_directive(
        directive="# 实时聊天室\n关键词: chat, room, websocket",
    )
    normalized, quality = adapter._evaluate_contract_quality(contracts)

    assert len(normalized) >= 3
    assert quality.get("score", 0) >= 80
    assert not quality.get("critical_issues")


@pytest.mark.asyncio
async def test_pm_adapter_recovers_with_synthesized_contracts_when_unparseable(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))

    async def _fake_call_role_llm(message: str, context=None):  # noqa: ANN001
        del message, context
        return {"content": "[TOOL_CALL]{\"tool_name\":\"list_directory\",\"path\":\".\"}[/TOOL_CALL]"}

    adapter._call_role_llm = _fake_call_role_llm  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-1-pm",
        input_data={"stage": "pm", "input": "# 实时聊天室\n关键词: chat, room, websocket"},
        context={"run_id": "factory-test"},
    )

    assert result.get("success") is True
    assert int(result.get("tasks_created") or 0) >= 3
    signals = (result.get("quality_gate") or {}).get("signals") or []
    assert any(
        isinstance(item, dict) and str(item.get("code") or "") == "pm.contracts.synthetic_recovery"
        for item in signals
    )


def test_pm_adapter_create_board_tasks_deduplicates_existing_semantic_tasks(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    existing = adapter.task_board.create(
        subject="筛选查询与月度汇总统计实现",
        description="已有任务",
        metadata={"goal": "实现筛选查询与月度汇总统计"},
    )

    contracts = [
        {
            "id": "TASK-3",
            "title": "筛选查询与月度汇总统计实现",
            "goal": "实现筛选查询与月度汇总统计",
            "description": "重复任务，应复用",
            "scope": ["src/reporting"],
            "steps": ["实现查询过滤", "实现月度汇总"],
            "acceptance": ["筛选条件可生效", "月度汇总可输出"],
            "depends_on": [],
            "phase": "implementation",
        },
        {
            "id": "TASK-4",
            "title": "导入导出与单元测试实现",
            "goal": "实现导入导出并补充单元测试",
            "description": "新任务",
            "scope": ["src/io", "tests"],
            "steps": ["实现导入导出", "补充测试"],
            "acceptance": ["可导入导出", "测试通过"],
            "depends_on": ["TASK-3"],
            "phase": "verification",
        },
    ]

    created = adapter._create_board_tasks(contracts)

    board_tasks = adapter.task_board.list_all()
    assert len(board_tasks) == 2
    assert any(int(item.get("id") or 0) == int(existing.id) for item in created)

    reused = adapter.task_board.get(existing.id)
    assert reused is not None
    assert bool((reused.metadata or {}).get("pm_deduplicated")) is True
    dependent = next((task for task in board_tasks if int(task.id) != int(existing.id)), None)
    assert dependent is not None
    resolved_dep = (dependent.metadata or {}).get("resolved_depends_on_task_ids")
    assert isinstance(resolved_dep, list)
    assert int(existing.id) in [int(item) for item in resolved_dep]


def test_pm_adapter_cleans_existing_duplicate_tasks_before_new_plan(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    keep = adapter.task_board.create(
        subject="筛选查询与月度汇总统计实现",
        description="主任务",
        metadata={"goal": "实现筛选查询与月度汇总统计"},
    )
    adapter.task_board.update(int(keep.id), status="in_progress")
    duplicate = adapter.task_board.create(
        subject="筛选查询与月度汇总统计实现",
        description="重复任务",
        metadata={"goal": "实现筛选查询与月度汇总统计"},
    )

    adapter._create_board_tasks(
        [
            {
                "id": "TASK-NEW",
                "title": "导入导出与单元测试实现",
                "goal": "实现导入导出与测试",
                "description": "新任务",
                "scope": ["src/io"],
                "steps": ["实现导入导出", "补充测试"],
                "acceptance": ["可导入导出", "测试通过"],
                "depends_on": [],
                "phase": "verification",
            }
        ]
    )

    duplicate_after = adapter.task_board.get(int(duplicate.id))
    assert duplicate_after is not None
    assert duplicate_after.status.value == "cancelled"
    assert int((duplicate_after.metadata or {}).get("dedup_merged_into") or 0) == int(keep.id)


@pytest.mark.asyncio
async def test_director_adapter_handles_orchestration_task_without_taskboard_row(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    emitted_events: list[dict[str, object]] = []
    llm_call_count = {"value": 0}

    async def _fake_call_role_llm(message: str, context=None):  # noqa: ANN001
        del message, context
        llm_call_count["value"] += 1
        return {"content": "无需工具调用，已完成分析。", "success": True}

    async def _fake_execute_tools(response: str, task_id: str):  # noqa: ANN001
        del response, task_id
        return []

    async def _capture_trace_event(**kwargs):  # noqa: ANN003
        emitted_events.append(kwargs)

    adapter._call_role_llm = _fake_call_role_llm  # type: ignore[method-assign]
    adapter._execute_tools = _fake_execute_tools  # type: ignore[method-assign]
    adapter._emit_task_trace_event = _capture_trace_event  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-0-director",
        input_data={"input": "实现账单导出接口"},
        context={},
    )

    assert result["success"] is True
    assert result["task_id"] != "task-0-director"
    assert llm_call_count["value"] == 2
    assert bool(result.get("qa_required_for_final_verdict")) is True
    decision_signals = result.get("decision_signals")
    assert isinstance(decision_signals, list)
    signal_codes = {
        str(item.get("code") or "")
        for item in decision_signals
        if isinstance(item, dict)
    }
    assert "director.no_writable_output_after_retry" in signal_codes
    assert "director.no_code_modifications" in signal_codes
    artifacts = result.get("artifacts")
    assert isinstance(artifacts, list)
    assert "runtime/signals/director_dispatch.director.signals.json" in artifacts
    event_codes = [str(item.get("code") or "") for item in emitted_events]
    assert "director.taskboard.task_selected" in event_codes
    assert "director.taskboard.waiting_for_ready_task" in event_codes
    assert "director.taskboard.materialized" in event_codes
    assert "director.taskboard.claimed" in event_codes
    assert not (tmp_path / "app.py").exists()


@pytest.mark.asyncio
async def test_director_adapter_updates_selected_taskboard_row_when_fallback_selects_ready_task(
    tmp_path: Path,
) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    board_task = adapter.task_board.create(
        subject="实现账单导出接口",
        description="生成导出模块并补充测试",
        metadata={},
    )
    emitted_events: list[dict[str, object]] = []

    async def _fake_call_role_llm(message: str, context=None):  # noqa: ANN001
        del message, context
        return {"content": "无需工具调用，已完成分析。", "success": True}

    async def _fake_execute_tools(response: str, task_id: str):  # noqa: ANN001
        del response, task_id
        return []

    async def _capture_trace_event(**kwargs):  # noqa: ANN003
        emitted_events.append(kwargs)

    adapter._call_role_llm = _fake_call_role_llm  # type: ignore[method-assign]
    adapter._execute_tools = _fake_execute_tools  # type: ignore[method-assign]
    adapter._emit_task_trace_event = _capture_trace_event  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-0-director",
        input_data={"input": "执行 PM 任务"},
        context={"run_id": "run-select-board-task"},
    )

    assert result["success"] is True
    assert result["task_id"] == str(board_task.id)
    board_row = adapter.task_board.get(board_task.id)
    assert board_row is not None
    assert str(board_row.status.value) == "completed"

    selected_event = next(
        item for item in emitted_events if str(item.get("code") or "") == "director.taskboard.task_selected"
    )
    refs = selected_event.get("refs")
    assert isinstance(refs, dict)
    assert str(refs.get("selection_source") or "") == "ready_queue_fallback"
    assert str(refs.get("selected_task_id") or "") == str(board_task.id)

    event_codes = [str(item.get("code") or "") for item in emitted_events]
    assert "director.taskboard.claimed" in event_codes


@pytest.mark.asyncio
async def test_director_adapter_projection_backend_is_explicit_and_optional(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    emitted_events: list[dict[str, object]] = []

    async def _capture_trace_event(**kwargs):  # noqa: ANN003
        emitted_events.append(kwargs)

    async def _unexpected_call_role_llm(*args, **kwargs):  # noqa: ANN001, ANN003
        raise AssertionError("code-edit LLM path should not run for projection backend")

    def _fake_projection_execute(self, request):  # noqa: ANN001
        assert request.execution_backend == "projection_generate"
        assert request.scenario_id == "scenario_alpha"
        return {
            "success": True,
            "execution_backend": request.execution_backend,
            "projection_result": {
                "experiment_id": "exp-001",
                "scenario_id": request.scenario_id,
                "project_root": str(tmp_path / "experiments" / "projection_lab"),
                "generated_files": ["experiments/projection_lab/tui_runtime.md"],
            },
            "artifacts": ["workspace/factory/projection_lab/exp-001/manifest.json"],
            "summary": "projection completed",
            "experiment_id": "exp-001",
            "project_root": str(tmp_path / "experiments" / "projection_lab"),
            "generated_files": ["experiments/projection_lab/tui_runtime.md"],
        }

    monkeypatch.setattr(
        director_execution_backend_module.DirectorProjectionBackendRunner,
        "execute",
        _fake_projection_execute,
    )
    adapter._emit_task_trace_event = _capture_trace_event  # type: ignore[method-assign]
    adapter._call_role_llm = _unexpected_call_role_llm  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-projection-director",
        input_data={
            "subject": "生成受控投影子项目",
            "description": "通过 projection 生成受控代码基线",
            "execution_backend": "projection_generate",
            "projection_scenario": "scenario_alpha",
            "projection_requirement": "生成一个受控投影实验项目，并完成基础验证。",
            "project_slug": "projection_lab",
        },
        context={"run_id": "run-projection-generate"},
    )

    assert result["success"] is True
    assert result["execution_backend"] == "projection_generate"
    board_row = adapter.task_board.get_task(result["task_id"])
    assert isinstance(board_row, dict)
    metadata = board_row.get("metadata") if isinstance(board_row.get("metadata"), dict) else {}
    projection = metadata.get("projection") if isinstance(metadata.get("projection"), dict) else {}
    assert metadata.get("execution_backend") == "projection_generate"
    assert projection.get("scenario_id") == "scenario_alpha"
    assert projection.get("project_slug") == "projection_lab"

    event_codes = [str(item.get("code") or "") for item in emitted_events]
    assert "director.execution_backend.selected" in event_codes
    assert "director.execution_backend.completed" in event_codes


@pytest.mark.asyncio
async def test_director_adapter_projection_refresh_fails_closed_without_experiment_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))

    async def _capture_trace_event(**kwargs):  # noqa: ANN003
        return None

    async def _unexpected_call_role_llm(*args, **kwargs):  # noqa: ANN001, ANN003
        raise AssertionError("code-edit LLM path should not run for projection backend")

    def _raise_missing_experiment_id(self, request):  # noqa: ANN001
        del request
        raise ValueError("projection_refresh_mapping requires experiment_id")

    monkeypatch.setattr(
        director_execution_backend_module.DirectorProjectionBackendRunner,
        "execute",
        _raise_missing_experiment_id,
    )
    adapter._emit_task_trace_event = _capture_trace_event  # type: ignore[method-assign]
    adapter._call_role_llm = _unexpected_call_role_llm  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-projection-refresh",
        input_data={
            "subject": "刷新 projection 回映射",
            "execution_backend": "projection_refresh_mapping",
        },
        context={"run_id": "run-projection-refresh"},
    )

    assert result["success"] is False
    assert result.get("error_code") == "director.execution_backend.failed"
    assert "experiment_id" in str(result.get("error") or "")

@pytest.mark.asyncio
async def test_director_execute_tools_handles_non_mapping_arguments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import polaris.kernelone.llm.toolkit as llm_toolkit_module

    adapter = DirectorAdapter(workspace=str(tmp_path))

    class _DummyToolCall:
        def __init__(self) -> None:
            self.name = "write_file"
            self.arguments = ["invalid"]

    def _fake_parse_tool_calls(*args, **kwargs):  # noqa: ANN002, ANN003
        del args, kwargs
        return [_DummyToolCall()]

    monkeypatch.setattr(llm_toolkit_module, "parse_tool_calls", _fake_parse_tool_calls)

    results = await adapter._execute_tools("dummy", "task-0-director")

    assert len(results) == 1
    assert results[0]["success"] is False
    assert "Invalid tool arguments type: list" in str(results[0].get("error") or "")


@pytest.mark.asyncio
async def test_director_adapter_retries_when_first_turn_has_no_tool_calls(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    call_count = {"value": 0}

    async def _fake_call_role_llm(message: str, context=None):  # noqa: ANN001
        del message, context
        call_count["value"] += 1
        return {"content": "已完成分析，请继续。", "success": True}

    async def _fake_execute_tools(response: str, task_id: str):  # noqa: ANN001
        del response, task_id
        return []

    adapter._call_role_llm = _fake_call_role_llm  # type: ignore[method-assign]
    adapter._execute_tools = _fake_execute_tools  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-1-director",
        input_data={"input": "实现预算模块"},
        context={},
    )

    assert result["success"] is True
    assert call_count["value"] == 2
    decision_signals = result.get("decision_signals")
    assert isinstance(decision_signals, list)
    details = [
        str(item.get("detail") or "")
        for item in decision_signals
        if isinstance(item, dict)
    ]
    assert any("fast_fail=consecutive_no_tool_calls" in detail for detail in details)


@pytest.mark.asyncio
async def test_director_adapter_force_retry_can_recover_with_write_output(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    call_count = {"value": 0}

    async def _fake_call_role_llm(message: str, context=None):  # noqa: ANN001
        del context
        call_count["value"] += 1
        if "最后机会" in message:
            return {
                "content": "PATCH_FILE: src/expense/core.py\n<<<<<<< SEARCH\n\n=======\nprint('ok')\n>>>>>>> REPLACE\nEND PATCH_FILE",
                "success": True,
            }
        return {
            "content": "[TOOL_CALL] {\"name\": \"list_directory\", \"arguments\": {\"path\": \".\"}} [/TOOL_CALL]",
            "success": True,
        }

    async def _fake_execute_tools(response: str, task_id: str):  # noqa: ANN001
        del task_id
        if "PATCH_FILE:" in response:
            target = tmp_path / "src" / "expense" / "core.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("print('ok')\n", encoding="utf-8")
            return [
                {
                    "tool": "write_file",
                    "success": True,
                    "result": {"file": "src/expense/core.py"},
                }
            ]
        return [
            {
                "tool": "list_directory",
                "success": True,
                "result": {"path": "."},
            }
        ]

    adapter._call_role_llm = _fake_call_role_llm  # type: ignore[method-assign]
    adapter._execute_tools = _fake_execute_tools  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-1-director",
        input_data={"input": "实现 expense 核心模块并补充测试"},
        context={},
    )

    assert result["success"] is True
    assert call_count["value"] == 3
    assert (tmp_path / "src" / "expense" / "core.py").exists()


@pytest.mark.asyncio
async def test_director_adapter_falls_back_when_kernel_tool_results_are_unsuccessful(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    adapter.task_board.create(
        subject="实现expense核心模型",
        description="实现 expense 领域对象与存储访问层",
        metadata={"scope": "src/expense, tests/", "steps": ["实现模型", "添加测试"]},
    )

    async def _fake_call_role_llm(message: str, context=None):  # noqa: ANN001
        del message, context
        return {
            "content": "PATCH_FILE: src/expense/model.py\nEND PATCH_FILE",
            "success": True,
            "raw_response": {
                "tool_calls": [
                    {
                        "tool": "write_file",
                        "success": False,
                        "error": "handler_missing:write_file",
                        "result": {"ok": False, "error": "handler_missing:write_file"},
                    }
                ]
            },
        }

    fallback_calls = {"value": 0}

    async def _fake_execute_tools(response: str, task_id: str):  # noqa: ANN001
        del response, task_id
        fallback_calls["value"] += 1
        target = tmp_path / "src" / "expense" / "model.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "from dataclasses import dataclass\n\n"
            "@dataclass\n"
            "class ExpenseRecord:\n"
            "    amount: float\n"
            "    category: str\n",
            encoding="utf-8",
        )
        return [
            {
                "tool": "write_file",
                "success": True,
                "result": {
                    "file": "src/expense/model.py",
                    "source_tool": "write_file",
                },
            }
        ]

    adapter._call_role_llm = _fake_call_role_llm  # type: ignore[method-assign]
    adapter._execute_tools = _fake_execute_tools  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-1-director",
        input_data={"input": "实现expense核心模块"},
        context={},
    )

    assert result["success"] is True
    assert fallback_calls["value"] >= 1
    assert (tmp_path / "src" / "expense" / "model.py").exists()


@pytest.mark.asyncio
async def test_director_call_role_llm_uses_default_kernel_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import config as config_module
    from polaris.cells.roles.adapters.internal import director_adapter as director_adapter_module

    captured = {"max_retries": None}

    async def _fake_generate_role_response(**kwargs):  # noqa: ANN003
        captured["max_retries"] = kwargs.get("max_retries")
        return {"response": "ok"}

    monkeypatch.setattr(config_module, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(director_adapter_module, "generate_role_response", _fake_generate_role_response)

    adapter = DirectorAdapter(workspace=str(tmp_path))
    result = await adapter._call_role_llm("执行任务")

    assert result["success"] is True
    assert captured["max_retries"] == 1


@pytest.mark.asyncio
async def test_director_call_role_llm_honors_retry_budget_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import config as config_module
    from polaris.cells.roles.adapters.internal import director_adapter as director_adapter_module

    captured = {"max_retries": None}

    async def _fake_generate_role_response(**kwargs):  # noqa: ANN003
        captured["max_retries"] = kwargs.get("max_retries")
        return {"response": "ok"}

    monkeypatch.setattr(config_module, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(director_adapter_module, "generate_role_response", _fake_generate_role_response)
    monkeypatch.setenv("KERNELONE_DIRECTOR_KERNEL_MAX_RETRIES", "0")

    adapter = DirectorAdapter(workspace=str(tmp_path))
    result = await adapter._call_role_llm("执行任务")

    assert result["success"] is True
    assert captured["max_retries"] == 0


@pytest.mark.asyncio
async def test_director_call_role_llm_marks_error_response_as_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import config as config_module
    from polaris.cells.roles.adapters.internal import director_adapter as director_adapter_module

    async def _fake_generate_role_response(**kwargs):  # noqa: ANN003
        del kwargs
        return {"response": "[ROLE_EXECUTION_ERROR] 验证失败", "error": "验证失败"}

    monkeypatch.setattr(config_module, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(director_adapter_module, "generate_role_response", _fake_generate_role_response)

    adapter = DirectorAdapter(workspace=str(tmp_path))
    result = await adapter._call_role_llm("执行任务")

    assert result["success"] is False
    assert str(result.get("error") or "") == "验证失败"


@pytest.mark.asyncio
async def test_director_call_role_llm_with_timeout_returns_recoverable_error(
    tmp_path: Path,
) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))

    async def _slow_call_role_llm(message: str, context=None):  # noqa: ANN001
        del message, context
        await asyncio.sleep(0.25)
        return {"content": "never_reached", "success": True}

    adapter._call_role_llm = _slow_call_role_llm  # type: ignore[method-assign]

    result = await adapter._call_role_llm_with_timeout(
        "执行任务",
        context=None,
        timeout_seconds=0.1,
        stage_label="unit",
    )

    assert result["success"] is False
    assert "llm_timeout" in str(result.get("error") or "")
    raw = result.get("raw_response")
    assert isinstance(raw, dict)
    assert raw.get("timeout") is True


@pytest.mark.asyncio
async def test_director_call_role_llm_with_timeout_normalizes_non_mapping_payload(
    tmp_path: Path,
) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))

    async def _invalid_call_role_llm(message: str, context=None):  # noqa: ANN001
        del message, context
        return ["invalid_payload"]

    adapter._call_role_llm = _invalid_call_role_llm  # type: ignore[method-assign]

    result = await adapter._call_role_llm_with_timeout(
        "执行任务",
        context=None,
        timeout_seconds=1.0,
        stage_label="unit",
    )

    assert result["success"] is False
    assert "invalid_llm_payload" in str(result.get("error") or "")
    assert isinstance(result.get("raw_response"), list)


@pytest.mark.asyncio
async def test_director_adapter_emits_trace_on_first_call_format_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that when the sequential engine terminates with an error, seq.start
    and seq.error events are emitted through the trace pipeline.

    The key insight: patching _call_role_llm_with_timeout on the adapter instance
    only works when the sequential engine actually runs. Since KERNELONE_SEQ_ENABLED
    is false by default, _execute_sequential returns {"success": False} before
    invoking the engine. Therefore we patch _execute_sequential directly to
    simulate the engine's error path and verify the event emission.
    """
    import config as config_module
    monkeypatch.setenv("KERNELONE_SEQ_ENABLED", "true")
    monkeypatch.setattr(config_module, "get_settings", lambda: SimpleNamespace())

    adapter = DirectorAdapter(workspace=str(tmp_path))
    emitted_events: list[dict[str, object]] = []

    def _make_fake_execute_sequential(adapter_obj):
        """Factory that captures adapter in a closure so self is not needed."""
        async def _fake_execute_sequential(
            task,
            task_id,
            run_id,
            context=None,
        ) -> dict[str, Any]:
            # Simulate the sequential engine error path:
            # 1. emit seq.start
            await adapter_obj._emit_task_trace_event(
                task_id=task_id,
                phase="executing",
                step_kind="sequential",
                step_title="Sequential execution started",
                step_detail="Sequential engine initialized",
                status="running",
                run_id=run_id,
                code="seq.start",
                refs={},
            )
            # 2. emit seq.error (simulating a validation failure in the LLM step)
            await adapter_obj._emit_task_trace_event(
                task_id=task_id,
                phase="executing",
                step_kind="sequential",
                step_title="Sequential execution error",
                step_detail="验证失败，已重试1次: 未找到有效的JSON或补丁",
                status="failed",
                run_id=run_id,
                code="seq.error",
                refs={},
            )
            return {
                "success": False,
                "error": "验证失败，已重试1次: 未找到有效的JSON或补丁",
                "mode": "sequential",
            }
        return _fake_execute_sequential

    async def _capture_trace_event(**kwargs):  # noqa: ANN003
        emitted_events.append(kwargs)

    adapter._execute_sequential = _make_fake_execute_sequential(adapter)  # type: ignore[method-assign]
    adapter._emit_task_trace_event = _capture_trace_event  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-format-director",
        input_data={
            "subject": "实现 expense 域模块",
            "description": "测试首轮格式失败事件",
            "input": "实现 expense",
        },
        context={"run_id": "run-format"},
    )

    assert result["success"] is False
    event_codes = [str(item.get("code") or "") for item in emitted_events]
    assert "seq.start" in event_codes, f"seq.start not in {event_codes}"
    assert "seq.error" in event_codes, f"seq.error not in {event_codes}"


@pytest.mark.asyncio
async def test_director_adapter_allows_retry_after_first_call_format_failure(
    tmp_path: Path,
) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))

    async def _fake_call_role_llm_with_timeout(  # noqa: ANN001
        message: str,
        *,
        context=None,
        timeout_seconds: float,
        stage_label: str,
    ):
        del message, context, timeout_seconds
        if stage_label == "first_call":
            return {
                "content": "",
                "success": False,
                "error": "验证失败，已重试1次: 未找到有效的JSON或补丁",
                "raw_response": {
                    "error": "验证失败，已重试1次: 未找到有效的JSON或补丁",
                    "validation": {"success": False, "quality_score": 50.0},
                },
            }
        if stage_label == "retry_call":
            return {
                "content": "RETRY_PATCH_PAYLOAD",
                "success": True,
                "raw_response": {"validation": {"success": True, "quality_score": 92.0}},
            }
        return {
            "content": "",
            "success": False,
            "error": f"unexpected_stage:{stage_label}",
            "raw_response": {"error": f"unexpected_stage:{stage_label}"},
        }

    async def _fake_execute_tools(response: str, task_id: str):  # noqa: ANN001
        del task_id
        if response != "RETRY_PATCH_PAYLOAD":
            return []
        target = tmp_path / "src" / "expense" / "role_agent_service.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "def calc_total(values: list[float]) -> float:\n    return float(sum(values))\n",
            encoding="utf-8",
        )
        test_target = tmp_path / "tests" / "test_service.py"
        test_target.parent.mkdir(parents=True, exist_ok=True)
        test_target.write_text(
            "from src.expense.service import calc_total\n\n"
            "def test_calc_total() -> None:\n"
            "    assert calc_total([1.0, 2.0]) == 3.0\n",
            encoding="utf-8",
        )
        return [
            {
                "tool": "write_file",
                "success": True,
                "result": {"ok": True, "file": "src/expense/role_agent_service.py", "source_tool": "write_file"},
            },
            {
                "tool": "write_file",
                "success": True,
                "result": {"ok": True, "file": "tests/test_service.py", "source_tool": "write_file"},
            },
        ]

    adapter._call_role_llm_with_timeout = _fake_call_role_llm_with_timeout  # type: ignore[method-assign]
    adapter._execute_tools = _fake_execute_tools  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-format-retry-director",
        input_data={
            "subject": "实现 expense 域模块",
            "description": "测试首轮格式失败后可继续补救",
            "input": "实现 expense",
        },
        context={"run_id": "run-format-retry"},
    )

    assert result["success"] is True
    decision_signals = result.get("decision_signals")
    assert isinstance(decision_signals, list)
    signal_codes = {
        str(item.get("code") or "")
        for item in decision_signals
        if isinstance(item, dict)
    }
    assert "director.first_call.format_validation_failed" in signal_codes
    assert "director.runtime.exception" not in signal_codes
    assert (tmp_path / "src" / "expense" / "role_agent_service.py").exists()
    assert (tmp_path / "tests" / "test_service.py").exists()


@pytest.mark.asyncio
async def test_director_adapter_defers_sparse_heuristic_to_qa_without_retry_blocking(
    tmp_path: Path,
) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    emitted_events: list[dict[str, object]] = []

    async def _fake_call_role_llm_with_timeout(  # noqa: ANN001
        message: str,
        *,
        context=None,
        timeout_seconds: float,
        stage_label: str,
    ):
        del message, context, timeout_seconds
        if stage_label == "first_call":
            return {
                "content": "FIRST_PATCH_PAYLOAD",
                "success": True,
                "raw_response": {"validation": {"success": True, "quality_score": 91.0}},
            }
        if stage_label == "sparse_retry_call":
            return {
                "content": "",
                "success": False,
                "error": "director_sparse_retry_call_llm_timeout: call timed out after 30s",
                "raw_response": {
                    "error": "director_sparse_retry_call_llm_timeout: call timed out after 30s",
                    "timeout": True,
                },
            }
        return {
            "content": "",
            "success": False,
            "error": f"unexpected_stage:{stage_label}",
            "raw_response": {"error": f"unexpected_stage:{stage_label}"},
        }

    async def _fake_execute_tools(response: str, task_id: str):  # noqa: ANN001
        del task_id
        if response == "FIRST_PATCH_PAYLOAD":
            target = tmp_path / "src" / "expense" / "single_file.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
            return [
                {
                    "tool": "write_file",
                    "success": True,
                    "result": {
                        "ok": True,
                        "file": "src/expense/single_file.py",
                        "source_tool": "write_file",
                    },
                }
            ]
        return []

    async def _capture_trace_event(**kwargs):  # noqa: ANN003
        emitted_events.append(kwargs)

    adapter._call_role_llm_with_timeout = _fake_call_role_llm_with_timeout  # type: ignore[method-assign]
    adapter._execute_tools = _fake_execute_tools  # type: ignore[method-assign]
    adapter._emit_task_trace_event = _capture_trace_event  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-sparse-timeout-director",
        input_data={
            "subject": "实现 expense 域模块，至少 2 个代码文件，不少于 80 行",
            "description": "测试 sparse 分支超时事件上报",
            "input": "实现 expense",
        },
        context={"run_id": "run-sparse-timeout"},
    )

    assert result["success"] is True
    event_codes = [str(item.get("code") or "") for item in emitted_events]
    assert "director.sparse_output.detected" in event_codes
    assert "director.sparse_output.deferred_to_qa" in event_codes
    assert "director.execute.completed" in event_codes
    assert "director.sparse_retry.started" not in event_codes


@pytest.mark.asyncio
async def test_qa_adapter_quality_gate_fails_when_critical_issues_present(tmp_path: Path) -> None:
    adapter = QAAdapter(workspace=str(tmp_path))

    async def _fake_call_role_llm(message: str, context=None):  # noqa: ANN001
        del message, context
        return {
            "content": (
                '{"verdict":"FAIL","score":55,"critical_issues":["runtime crash"],'
                '"major_issues":[],"warnings":[],"evidence":["tests failed"],"suggestions":["fix"]}'
            ),
            "success": True,
        }

    adapter._call_role_llm = _fake_call_role_llm  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-0-qa",
        input_data={"review_type": "quality_gate", "review_target": "demo project"},
        context={},
    )

    assert result["success"] is False
    report_path = Path(resolve_runtime_path(str(tmp_path), "runtime/qa/report.json"))
    assert report_path.exists()


@pytest.mark.asyncio
async def test_qa_adapter_reopens_completed_director_task_on_fail(tmp_path: Path) -> None:
    adapter = QAAdapter(workspace=str(tmp_path))
    director_task = adapter.task_board.create(subject="实现账单导出", description="A", metadata={})
    adapter.task_board.update(
        director_task.id,
        status="completed",
        metadata={"adapter_result": {"qa_required_for_final_verdict": True, "qa_passed": None}},
    )

    async def _fake_call_role_llm(message: str, context=None):  # noqa: ANN001
        del message, context
        return {
            "content": (
                '{"verdict":"FAIL","score":58,"critical_issues":["integration failed"],'
                '"major_issues":[],"warnings":[],"evidence":["qa"],"suggestions":["fix"]}'
            ),
            "success": True,
        }

    adapter._call_role_llm = _fake_call_role_llm  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-qa-reopen",
        input_data={"review_type": "quality_gate", "review_target": "demo project"},
        context={"run_id": "qa-run-1"},
    )

    assert result["success"] is False
    board_row = adapter.task_board.get(director_task.id)
    assert board_row is not None
    assert str(board_row.status.value) == "pending"
    metadata = board_row.metadata if isinstance(board_row.metadata, dict) else {}
    assert bool(metadata.get("qa_rework_requested")) is True
    assert int(metadata.get("qa_rework_retry_count") or 0) == 1
    adapter_result = metadata.get("adapter_result") if isinstance(metadata.get("adapter_result"), dict) else {}
    assert adapter_result.get("qa_passed") is False


@pytest.mark.asyncio
async def test_qa_adapter_marks_failed_when_rework_retry_exhausted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("KERNELONE_DIRECTOR_TASK_REWORK_MAX_RETRIES", "2")
    adapter = QAAdapter(workspace=str(tmp_path))
    director_task = adapter.task_board.create(subject="实现账单导出", description="A", metadata={})
    adapter.task_board.update(
        director_task.id,
        status="completed",
        metadata={
            "adapter_result": {
                "qa_required_for_final_verdict": True,
                "qa_passed": None,
                "qa_rework_retry_count": 1,
                "qa_rework_max_retries": 2,
            },
            "qa_rework_retry_count": 1,
            "qa_rework_max_retries": 2,
        },
    )

    async def _fake_call_role_llm(message: str, context=None):  # noqa: ANN001
        del message, context
        return {
            "content": (
                '{"verdict":"FAIL","score":45,"critical_issues":["still failing"],'
                '"major_issues":[],"warnings":[],"evidence":["qa"],"suggestions":["fix"]}'
            ),
            "success": True,
        }

    adapter._call_role_llm = _fake_call_role_llm  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-qa-exhausted",
        input_data={"review_type": "quality_gate", "review_target": "demo project"},
        context={"run_id": "qa-run-2"},
    )

    assert result["success"] is False
    board_row = adapter.task_board.get(director_task.id)
    assert board_row is not None
    assert str(board_row.status.value) == "failed"
    metadata = board_row.metadata if isinstance(board_row.metadata, dict) else {}
    assert bool(metadata.get("qa_rework_exhausted")) is True
    assert int(metadata.get("qa_rework_retry_count") or 0) == 2


def test_director_taskboard_snapshot_includes_rework_and_exhausted_states(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    pending_rework = adapter.task_board.create(subject="任务待返工", description="A", metadata={})
    failed_exhausted = adapter.task_board.create(subject="任务重试耗尽", description="B", metadata={})

    adapter.task_board.update(
        pending_rework.id,
        metadata={
            "adapter_result": {"qa_required_for_final_verdict": True, "qa_passed": False},
            "qa_rework_requested": True,
        },
    )
    adapter.task_board.update(
        failed_exhausted.id,
        status="failed",
        metadata={
            "adapter_result": {"qa_required_for_final_verdict": True, "qa_passed": False},
            "qa_rework_exhausted": True,
        },
    )

    snapshot = adapter._build_taskboard_observation_snapshot(sample_limit=10)
    pending_samples = snapshot.get("samples", {}).get("pending", [])
    failed_samples = snapshot.get("samples", {}).get("failed", [])
    pending_states = {
        str(item.get("id") or ""): str(item.get("qa_state") or "")
        for item in pending_samples
        if isinstance(item, dict)
    }
    failed_states = {
        str(item.get("id") or ""): str(item.get("qa_state") or "")
        for item in failed_samples
        if isinstance(item, dict)
    }

    assert pending_states.get(str(pending_rework.id)) == "rework"
    assert failed_states.get(str(failed_exhausted.id)) == "exhausted"


def test_qa_adapter_filters_stale_stage_signals_by_run_id(tmp_path: Path) -> None:
    adapter = QAAdapter(workspace=str(tmp_path))
    signal_path = Path(resolve_runtime_path(str(tmp_path), "runtime/signals/pm_planning.pm.signals.json"))
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    signal_path.write_text(
        json.dumps(
            {
                "run_id": "pm-run-new",
                "signals": [
                    {"code": "pm.contracts.unparseable_after_retry", "run_id": "pm-run-old"},
                    {"code": "pm.execution.summary", "run_id": "pm-run-new"},
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    rows = adapter._load_runtime_stage_signals()
    codes = [str(item.get("code") or "") for item in rows]

    assert "pm.execution.summary" in codes
    assert "pm.contracts.unparseable_after_retry" not in codes


@pytest.mark.asyncio
async def test_qa_adapter_warns_when_llm_output_is_not_json(tmp_path: Path) -> None:
    adapter = QAAdapter(workspace=str(tmp_path))

    async def _fake_call_role_llm(message: str, context=None):  # noqa: ANN001
        del message, context
        return {
            "content": "结论：看起来还可以，但我不返回 JSON。",
            "success": True,
        }

    adapter._call_role_llm = _fake_call_role_llm  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-qa-non-json",
        input_data={"review_type": "quality_gate", "review_target": "demo project"},
        context={},
    )

    assert result["success"] is True
    warnings = result.get("warnings")
    assert isinstance(warnings, list)
    assert "qa_llm_judgement_unavailable" in warnings
    report_path = Path(resolve_runtime_path(str(tmp_path), "runtime/qa/report.json"))
    assert report_path.exists()


@pytest.mark.asyncio
async def test_qa_adapter_recovers_fail_verdict_from_commented_json_findings(tmp_path: Path) -> None:
    adapter = QAAdapter(workspace=str(tmp_path))

    async def _fake_call_role_llm(message: str, context=None):  # noqa: ANN001
        del message, context
        return {
            "content": (
                '<output>\n```json\n{\n'
                '  "review_id": "REV-002-20260323",\n'
                '  "verdict": "FAIL",\n'
                '  "score": 41,\n'
                '  "summary": "验收不通过 - 缺少测试与关键实现",\n'
                '  "findings": [\n'
                '    {\n'
                '      "severity": "critical",\n'
                '      "description": "缺少任何测试文件",\n'
                '      "evidence": "test_file_count=0",\n'
                '      "recommendation": "补齐单元测试"\n'
                '    },\n'
                '    {\n'
                '      "severity": "high",\n'
                '      "description": "缺少本地持久化关键验证",\n'
                '      "evidence": "persistence verification missing",\n'
                '      "recommendation": "补充持久化验证"\n'
                '    }\n'
                '  ],\n'
                '  "checklist_results": {\n'
                '    "code_style_compliant": true, // trailing comment from model\n'
                '    "documentation_complete": false\n'
                '  }\n'
                '}\n```\n'
            ),
            "success": True,
        }

    adapter._call_role_llm = _fake_call_role_llm  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-qa-commented-json",
        input_data={"review_type": "quality_gate", "review_target": "demo project"},
        context={},
    )

    assert result["success"] is False
    critical = result.get("critical_issues")
    major = result.get("major_issues")
    warnings = result.get("warnings")
    assert isinstance(critical, list)
    assert isinstance(major, list)
    assert "缺少任何测试文件" in critical
    assert "缺少本地持久化关键验证" in major
    assert isinstance(warnings, list)
    assert "qa_llm_judgement_unavailable" not in warnings
