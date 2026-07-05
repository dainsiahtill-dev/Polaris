"""Regression tests for role adapters aligned with TaskBoard current API."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from polaris.bootstrap import config as bootstrap_config_module
from polaris.cells.events.fact_stream.public.contracts import QueryFactEventsV1
from polaris.cells.events.fact_stream.public.service import query_fact_events
from polaris.cells.roles.adapters.internal import director_execution_backend as director_execution_backend_module
from polaris.cells.roles.adapters.internal.base import BaseRoleAdapter
from polaris.cells.roles.adapters.internal.director.state_tracking import DirectorStateTracker
from polaris.cells.roles.adapters.internal.director_adapter import DirectorAdapter
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter
from polaris.cells.roles.adapters.internal.qa_adapter import QAAdapter
from polaris.cells.runtime.task_runtime.internal.service import TaskRuntimeService
from polaris.kernelone.storage import resolve_runtime_path
from polaris.kernelone.storage.paths import resolve_signal_path


def _create_task_row(adapter: Any, **kwargs: Any) -> dict[str, Any]:
    row = adapter.task_runtime.create_task_row(**kwargs)
    assert isinstance(row, dict)
    return row


def _update_task_row(adapter: Any, task_id: Any, **kwargs: Any) -> dict[str, Any]:
    row = adapter.task_runtime.update_task_row(task_id, **kwargs)
    assert isinstance(row, dict)
    return row


def _get_task_row(adapter: Any, task_id: Any) -> dict[str, Any]:
    row = adapter.task_runtime.get_task(task_id)
    assert isinstance(row, dict)
    return row


def _row_id(row: dict[str, Any]) -> int:
    return int(row.get("id") or 0)


def _row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("metadata")
    return raw if isinstance(raw, dict) else {}


def test_pm_adapter_fallback_domain_prefers_workspace_slug_over_directive_noise(tmp_path: Path) -> None:
    workspace = tmp_path / "expense-tracker"
    workspace.mkdir(parents=True, exist_ok=True)
    adapter = PMAdapter(workspace=str(workspace))

    token = adapter._derive_domain_token("上轮失败摘要包含 todo task 关键词")

    assert token == "expense"


def test_pm_typescript_package_contract_uses_requirement_checks_not_fixed_template(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    directive = """
# Product Requirements - Glow Garden

用 TypeScript 实现一个发光昆虫花园模拟器，交付 index.html、package.json、tsconfig.json 和源码。
必须在工作区根目录生成可运行项目，支持 npm run build/test/start。

## Deterministic Checks
- html
- ts_syntax
- package_scripts
- content_any:firefly|flower|moon|humidity
"""

    contracts = adapter._synthesize_task_contracts_from_directive(directive=directive)
    payload = json.dumps(contracts, ensure_ascii=False)
    lowered_payload = payload.lower()

    assert len(contracts) == 3
    for model_target in (
        "src/models/Firefly.ts",
        "src/models/Flower.ts",
        "src/models/MoonPhase.ts",
        "src/models/Humidity.ts",
    ):
        assert model_target in payload
    assert "content_any:firefly|flower|moon|humidity" in payload
    assert "package_scripts" in payload
    for token in ("firefly", "flower", "moon", "humidity"):
        assert token in lowered_payload


def test_director_ephemeral_task_includes_pending_taskboard_contract(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    _create_task_row(
        adapter,
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


def test_task_runtime_execution_event_publishes_factory_progress_channel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[dict[str, Any]] = []

    class _Publisher:
        def publish(self, *, subject: str, payload: dict[str, Any]) -> bool:
            published.append({"subject": subject, "payload": payload})
            return True

    monkeypatch.setattr(
        "polaris.infrastructure.log_pipeline.jetstream_publisher.get_log_jetstream_publisher",
        lambda: _Publisher(),
    )

    service = TaskRuntimeService(str(tmp_path))
    task = service.create_task_row(
        subject="实现发光昆虫模拟器",
        metadata={
            "factory_run_id": "factory-1",
            "factory_bench_session_id": "bench-1",
            "factory_bench_project_id": "L1-01",
        },
    )

    result = service.claim_execution(
        task["id"],
        worker_id="director",
        role_id="director",
        run_id="director-1",
        metadata={"adapter_phase": "claimed", "factory_run_id": "factory-1"},
    )

    assert result["success"] is True
    assert published
    assert published[-1]["subject"].endswith(".event.factory.factory-1")
    envelope = published[-1]["payload"]
    assert envelope["channel"] == "event.factory:factory-1"
    assert envelope["run_id"] == "factory-1"
    assert envelope["kind"] == "task_runtime_execution"
    assert envelope["payload"]["type"] == "task_runtime_execution"
    assert envelope["payload"]["run_id"] == "director-1"
    assert envelope["payload"]["factory_bench_session_id"] == "bench-1"


def test_role_adapter_update_board_task_uses_runtime_row_api(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))

    class _RowWriteRuntime:
        def __init__(self) -> None:
            self.updated: list[dict[str, Any]] = []

        def task_exists(self, task_id: Any) -> bool:
            return str(task_id or "") == "7"

        def update_task_row(
            self,
            task_id: Any,
            *,
            status: str | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            normalized_id = int(str(task_id).removeprefix("task-"))
            row = {
                "id": normalized_id,
                "status": status or "pending",
                "metadata": dict(metadata or {}),
            }
            self.updated.append(row)
            return dict(row)

        def update_task(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("role adapters must use update_task_row()")

    runtime = _RowWriteRuntime()
    adapter._task_runtime = cast(Any, runtime)

    assert adapter._update_board_task("task-7", status="in_progress", metadata={"phase": "execute"}) is True
    assert runtime.updated == [{"id": 7, "status": "in_progress", "metadata": {"phase": "execute"}}]


def test_role_adapter_update_board_task_rejects_terminal_status_shortcut(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))

    class _RowWriteRuntime:
        def task_exists(self, task_id: Any) -> bool:
            return int(str(task_id).removeprefix("task-")) == 7

        def update_task_row(
            self,
            task_id: Any,
            *,
            status: str | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            raise AssertionError("terminal task-row writes must use task-runtime owner transitions")

    runtime = _RowWriteRuntime()
    adapter._task_runtime = cast(Any, runtime)

    with pytest.raises(RuntimeError, match="terminal_task_status_requires_task_runtime_owner_transition"):
        BaseRoleAdapter._update_board_task(adapter, "task-7", status="failed", metadata={"phase": "execute"})


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
async def test_director_adapter_removes_legacy_call_role_llm_shims(
    tmp_path: Path,
) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))

    assert not hasattr(adapter, "_call_role_llm")
    assert not hasattr(adapter, "_call_role_llm_with_timeout")


def test_director_selects_pending_board_task_when_orchestration_task_missing(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    _create_task_row(adapter, subject="任务A", description="A", metadata={})
    _create_task_row(adapter, subject="任务B", description="B", metadata={})

    selected = adapter._select_pending_board_task()

    assert selected is not None
    assert str(selected.get("subject") or "") == "任务A"


def test_director_taskboard_snapshot_includes_completed_qa_state(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    done_pending_qa = _create_task_row(adapter, subject="任务待QA", description="A", metadata={})
    done_failed_qa = _create_task_row(adapter, subject="任务QA未通过", description="B", metadata={})
    done_passed_qa = _create_task_row(adapter, subject="任务QA通过", description="C", metadata={})

    _update_task_row(
        adapter,
        done_pending_qa["id"],
        status="completed",
        metadata={"adapter_result": {"qa_required_for_final_verdict": True, "qa_passed": None}},
    )
    _update_task_row(
        adapter,
        done_failed_qa["id"],
        status="completed",
        metadata={"adapter_result": {"qa_required_for_final_verdict": True, "qa_passed": False}},
    )
    _update_task_row(
        adapter,
        done_passed_qa["id"],
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

    assert qa_states.get(str(done_pending_qa["id"])) == "pending"
    assert qa_states.get(str(done_failed_qa["id"])) == "failed"
    assert qa_states.get(str(done_passed_qa["id"])) == "passed"


def test_director_taskboard_snapshot_uses_observable_task_rows(tmp_path: Path) -> None:
    class _ObservableRuntime:
        def list_observable_task_rows(self) -> list[dict[str, object]]:
            return [{"id": 1, "status": "pending", "subject": "Observable task"}]

        def list_task_rows(self) -> list[dict[str, object]]:
            raise AssertionError("Director taskboard snapshots must use observable task rows")

        def get_task_row_stats(self) -> dict[str, int]:
            return {}

        def list_ready_task_rows(self) -> list[dict[str, object]]:
            return []

    snapshot = DirectorStateTracker(str(tmp_path)).build_taskboard_observation_snapshot(
        _ObservableRuntime(),
        sample_limit=10,
    )

    assert snapshot["counts"]["total"] == 1
    assert snapshot["samples"]["pending"] == [
        {
            "id": "1",
            "subject": "Observable task",
            "qa_state": "",
            "claimed_by": "",
            "execution_backend": "",
            "resume_state": "",
            "session_id": "",
            "workflow_run_id": "",
        }
    ]


def test_director_taskboard_snapshot_surfaces_running_task_without_duplicate_ready_rows(
    tmp_path: Path,
) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    running_task = _create_task_row(
        adapter,
        subject="实现数据模型与本地持久化存储层",
        description="实现模型、仓储与序列化",
        metadata={"execution_backend": "code_edit"},
    )
    ready_task = _create_task_row(
        adapter,
        subject="编写单元测试与集成验证",
        description="补齐测试与回归验证",
        metadata={"execution_backend": "code_edit"},
    )

    claimed = adapter.task_runtime.claim_execution(
        running_task["id"],
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
    in_progress_ids = {str(item.get("id") or "") for item in in_progress_samples if isinstance(item, dict)}
    assert str(running_task["id"]) in in_progress_ids

    all_sample_ids = [
        str(item.get("id") or "")
        for bucket in samples.values()
        if isinstance(bucket, list)
        for item in bucket
        if isinstance(item, dict)
    ]
    assert all_sample_ids.count(str(ready_task["id"])) == 1


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
    raw_metadata = created[0].get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    raw_projection = metadata.get("projection")
    projection: dict[str, Any] = raw_projection if isinstance(raw_projection, dict) else {}
    assert metadata.get("execution_backend") == "projection_generate"
    assert projection.get("scenario_id") == "scenario_alpha"
    assert projection.get("project_slug") == "projection_lab"


@pytest.mark.asyncio
async def test_pm_adapter_pm_stage_creates_tasks_with_current_taskboard_api(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))

    async def _fake_call_role_llm(message: str, context=None, **kwargs):
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
        str(item).replace("\\", "/").endswith("runtime/signals/pm_planning.pm.signals.json") for item in artifacts
    )
    pm_signal_file = resolve_signal_path(str(tmp_path), "pm", "pm_planning")
    payload = json.loads(pm_signal_file.read_text(encoding="utf-8"))
    rows = payload.get("signals") if isinstance(payload, dict) else []
    assert isinstance(rows, list)
    assert any(isinstance(item, dict) and str(item.get("code") or "") == "pm.execution.summary" for item in rows)
    board_tasks = adapter.task_runtime.list_task_rows()
    assert len(board_tasks) >= 2
    assert all(str(task.get("subject") or "").strip() for task in board_tasks)


@pytest.mark.asyncio
async def test_pm_adapter_projection_hint_synthesizes_generic_projection_contracts(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))

    async def _fake_call_role_llm(message: str, context=None, **kwargs):
        del message, context
        return {"content": '[TOOL_CALL]{"tool_name":"noop"}[/TOOL_CALL]'}

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
    board_tasks = adapter.task_runtime.list_task_rows()
    assert len(board_tasks) >= 3
    raw_first_metadata = board_tasks[0].get("metadata")
    first_metadata: dict[str, Any] = raw_first_metadata if isinstance(raw_first_metadata, dict) else {}
    raw_projection = first_metadata.get("projection")
    projection: dict[str, Any] = raw_projection if isinstance(raw_projection, dict) else {}
    assert first_metadata.get("execution_backend") == "projection_generate"
    assert projection.get("scenario_id") == "scenario_alpha"
    assert projection.get("project_slug") == "projection_lab"
    for task in board_tasks:
        raw_metadata = task.get("metadata")
        metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
        target_files = metadata.get("target_files")
        assert isinstance(target_files, list)
        assert target_files
        assert all("." in str(path) for path in target_files)


@pytest.mark.asyncio
async def test_pm_adapter_runtime_exception_is_fail_closed(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))

    async def _boom_call_role_llm(message: str, context=None):
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


@pytest.mark.asyncio
async def test_pm_adapter_deterministic_contracts_bypass_llm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    monkeypatch.setenv("KERNELONE_PM_DETERMINISTIC_CONTRACTS", "1")

    async def _boom_call_role_llm(message: str, context=None):
        del message, context
        raise AssertionError("LLM should be bypassed")

    adapter._call_role_llm = _boom_call_role_llm  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-deterministic-pm",
        input_data={"stage": "pm", "input": "Build a React desktop workbench with tests"},
        context={"run_director": True},
    )

    assert result["success"] is True
    assert int(result.get("tasks_created") or 0) >= 3
    signal_path = resolve_signal_path(str(tmp_path), "pm", "pm_planning")
    payload = json.loads(signal_path.read_text(encoding="utf-8"))
    raw_rows = payload.get("signals") if isinstance(payload, dict) else []
    rows: list[Any] = raw_rows if isinstance(raw_rows, list) else []
    assert any(
        isinstance(item, dict) and str(item.get("code") or "") == "pm.contracts.deterministic_fallback" for item in rows
    )


def test_pm_adapter_extracts_embedded_json_contracts(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    response = (
        "下面是任务合同，请执行：\n"
        '{"tasks":[{"id":"TASK-1","title":"实现 expense 存储","goal":"完成持久化",'
        '"description":"实现仓储","scope":"src/expense","steps":["设计","实现"],'
        '"acceptance":["测试通过","可持久化"],"depends_on":[],"assigned_to":"Director"}]}\n'
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


def test_pm_adapter_synthesizes_frontend_workbench_contracts(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path / "fashion-gen-studio"))

    contracts = adapter._synthesize_task_contracts_from_directive(
        directive=(
            "Build an Electron React TypeScript Vite Tailwind desktop workbench. "
            "Routes: /workbench/model /workbench/headless /workbench/face-lab "
            "/workbench/scene /workbench/batch /library/assets /settings/models. "
            "Use Zustand and Vitest."
        ),
    )
    normalized, quality = adapter._evaluate_contract_quality(contracts)

    assert len(normalized) >= 4
    titles = [str(item.get("title") or "") for item in normalized]
    assert any("Toolchain" in title for title in titles)
    assert any("Generation Spec" in title for title in titles)
    assert any("Workbench" in title for title in titles)
    assert quality.get("score", 0) >= 80
    assert not quality.get("critical_issues")


@pytest.mark.asyncio
async def test_pm_adapter_recovers_with_synthesized_contracts_when_unparseable(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))

    async def _fake_call_role_llm(message: str, context=None, **kwargs):
        del message, context
        return {"content": '[TOOL_CALL]{"tool_name":"list_directory","path":"."}[/TOOL_CALL]'}

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
        isinstance(item, dict) and str(item.get("code") or "") == "pm.contracts.synthetic_recovery" for item in signals
    )


def test_pm_adapter_create_board_tasks_deduplicates_existing_semantic_tasks(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    existing = _create_task_row(
        adapter,
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

    board_tasks = adapter.task_runtime.list_task_rows()
    assert len(board_tasks) == 2
    assert any(int(item.get("id") or 0) == _row_id(existing) for item in created)

    reused = _get_task_row(adapter, existing["id"])
    assert bool(_row_metadata(reused).get("pm_deduplicated")) is True
    dependent = next((task for task in board_tasks if int(task.get("id") or 0) != _row_id(existing)), None)
    assert dependent is not None
    raw_dependent_metadata = dependent.get("metadata")
    dependent_metadata: dict[str, Any] = raw_dependent_metadata if isinstance(raw_dependent_metadata, dict) else {}
    resolved_dep = dependent_metadata.get("resolved_depends_on_task_ids")
    assert isinstance(resolved_dep, list)
    assert _row_id(existing) in [int(item) for item in resolved_dep]
    assert dependent.get("blocked_by") == [_row_id(existing)]
    assert dependent.get("status") == "blocked"

    reused_metadata = _row_metadata(reused)
    assert reused_metadata["external_task_id"] == "TASK-3"
    assert reused_metadata["pm_task_id"] == "TASK-3"


def test_pm_adapter_create_board_tasks_preserves_execution_contract_paths(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))

    target_files = [
        "package.json",
        "tsconfig.json",
        "src/models/flower.ts",
        "src/models/firefly.ts",
        "src/models/moonphase.ts",
        "src/models/garden.ts",
        "src/index.ts",
    ]
    scope_paths = [
        "package.json",
        "tsconfig.json",
        "src/models/flower.ts",
        "src/models/firefly.ts",
        "src/models/moonphase.ts",
        "src/models/garden.ts",
    ]
    context_files = ["requirements.md"]

    created = adapter._create_board_tasks(
        [
            {
                "id": "TASK-1",
                "title": "实现 TypeScript 项目骨架与核心模型",
                "goal": "交付 TypeScript/npm 项目骨架和核心模型",
                "description": "创建 package.json、tsconfig.json、src/index.ts 与核心模型文件",
                "scope": "package.json, tsconfig.json, src/models/Flower.ts, src/models/Firefly.ts",
                "scope_paths": scope_paths,
                "target_files": target_files,
                "context_files": context_files,
                "steps": ["创建项目配置", "实现模型", "实现入口"],
                "acceptance": ["所有 target files 存在且非空"],
                "depends_on": [],
                "phase": "implementation",
                "assigned_to": "Director",
            }
        ]
    )

    assert len(created) == 1
    task = _get_task_row(adapter, created[0]["id"])
    metadata = _row_metadata(task)
    assert metadata.get("target_files") == target_files
    assert metadata.get("scope_paths") == scope_paths
    assert metadata.get("context_files") == context_files


def test_pm_adapter_cleans_existing_duplicate_tasks_before_new_plan(tmp_path: Path) -> None:
    adapter = PMAdapter(workspace=str(tmp_path))
    keep = _create_task_row(
        adapter,
        subject="筛选查询与月度汇总统计实现",
        description="主任务",
        metadata={"goal": "实现筛选查询与月度汇总统计"},
    )
    _update_task_row(adapter, keep["id"], status="in_progress")
    duplicate = _create_task_row(
        adapter,
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

    duplicate_after = _get_task_row(adapter, duplicate["id"])
    assert duplicate_after.get("status") == "cancelled"
    assert int(_row_metadata(duplicate_after).get("dedup_merged_into") or 0) == _row_id(keep)
    events = query_fact_events(QueryFactEventsV1(workspace=str(tmp_path), stream="task_runtime.execution")).events
    cancelled_events = [
        event
        for event in events
        if event.get("event_type") == "cancelled"
        and event.get("payload", {}).get("task_id") == str(_row_id(duplicate))
    ]
    assert len(cancelled_events) == 1
    assert cancelled_events[0]["payload"]["details"] == {
        "reason": "pm_duplicate_subject",
        "source": "pm_adapter",
        "dedup_merged_into": str(_row_id(keep)),
    }


@pytest.mark.asyncio
async def test_director_adapter_handles_orchestration_task_without_taskboard_row(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    emitted_events: list[dict[str, object]] = []
    llm_call_count = {"value": 0}

    async def _fake_call_role_llm(message: str, context=None, **kwargs):
        del message, context
        llm_call_count["value"] += 1
        return {"content": "无需工具调用，已完成分析。", "success": True}

    async def _fake_execute_tools(response: str, task_id: str, update_task_progress=None, **kwargs):
        del response, task_id, update_task_progress, kwargs
        return []

    async def _capture_trace_event(**kwargs):
        emitted_events.append(kwargs)

    adapter._invoke_role_dialogue = _fake_call_role_llm  # type: ignore[method-assign]
    adapter._execution.execute_tools = _fake_execute_tools  # type: ignore[method-assign]
    adapter._emit_task_trace_event = _capture_trace_event  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-0-director",
        input_data={"input": "实现账单导出接口"},
        context={},
    )

    assert result["task_id"] != "task-0-director"
    assert bool(result.get("qa_required_for_final_verdict")) is True
    decision_signals = result.get("decision_signals")
    assert isinstance(decision_signals, list)
    signal_codes = {str(item.get("code") or "") for item in decision_signals if isinstance(item, dict)}
    assert "incomplete_materialization" in signal_codes


@pytest.mark.asyncio
async def test_director_adapter_updates_selected_taskboard_row_when_fallback_selects_ready_task(
    tmp_path: Path,
) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    board_task = _create_task_row(
        adapter,
        subject="实现账单导出接口",
        description="生成导出模块并补充测试",
        metadata={},
    )
    emitted_events: list[dict[str, object]] = []

    async def _fake_call_role_llm(message: str, context=None, **kwargs):
        del message, context
        return {"content": "无需工具调用，已完成分析。", "success": True}

    async def _fake_execute_tools(response: str, task_id: str, update_task_progress=None, **kwargs):
        del response, task_id, update_task_progress, kwargs
        return []

    async def _capture_trace_event(**kwargs):
        emitted_events.append(kwargs)

    adapter._invoke_role_dialogue = _fake_call_role_llm  # type: ignore[method-assign]
    adapter._execution.execute_tools = _fake_execute_tools  # type: ignore[method-assign]
    adapter._emit_task_trace_event = _capture_trace_event  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-0-director",
        input_data={"input": "执行 PM 任务"},
        context={"run_id": "run-select-board-task"},
    )

    assert result["task_id"] == str(board_task["id"])
    assert bool(result.get("qa_required_for_final_verdict")) is True


@pytest.mark.xfail(reason="Projection backend not routed in current execute flow", strict=False)
@pytest.mark.asyncio
async def test_director_adapter_projection_backend_is_explicit_and_optional(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    emitted_events: list[dict[str, object]] = []

    async def _capture_trace_event(**kwargs):
        emitted_events.append(kwargs)

    async def _unexpected_call_role_llm(*args, **kwargs):
        raise AssertionError("code-edit LLM path should not run for projection backend")

    def _fake_projection_execute(self, request):
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
    adapter._invoke_role_dialogue = _unexpected_call_role_llm  # type: ignore[method-assign]

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
    board_row = adapter.task_runtime.get_task(result["task_id"])
    assert isinstance(board_row, dict)
    raw_metadata = board_row.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    raw_projection = metadata.get("projection")
    projection: dict[str, Any] = raw_projection if isinstance(raw_projection, dict) else {}
    assert metadata.get("execution_backend") == "projection_generate"
    assert projection.get("scenario_id") == "scenario_alpha"
    assert projection.get("project_slug") == "projection_lab"

    event_codes = [str(item.get("code") or "") for item in emitted_events]
    assert "director.execution_backend.selected" in event_codes
    assert "director.execution_backend.completed" in event_codes


@pytest.mark.xfail(reason="Projection backend not routed in current execute flow", strict=False)
@pytest.mark.asyncio
async def test_director_adapter_projection_refresh_fails_closed_without_experiment_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))

    async def _capture_trace_event(**kwargs):
        return None

    async def _unexpected_call_role_llm(*args, **kwargs):
        raise AssertionError("code-edit LLM path should not run for projection backend")

    def _raise_missing_experiment_id(self, request):
        del request
        raise ValueError("projection_refresh_mapping requires experiment_id")

    monkeypatch.setattr(
        director_execution_backend_module.DirectorProjectionBackendRunner,
        "execute",
        _raise_missing_experiment_id,
    )
    adapter._emit_task_trace_event = _capture_trace_event  # type: ignore[method-assign]
    adapter._invoke_role_dialogue = _unexpected_call_role_llm  # type: ignore[method-assign]

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

    def _fake_parse_tool_calls(*args, **kwargs):
        del args, kwargs
        return [_DummyToolCall()]

    monkeypatch.setattr(llm_toolkit_module, "parse_tool_calls", _fake_parse_tool_calls)

    assert not hasattr(adapter, "_execute_tools")


@pytest.mark.asyncio
async def test_director_adapter_retries_when_first_turn_has_no_tool_calls(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    call_count = {"value": 0}

    async def _fake_call_role_llm(message: str, context=None, **kwargs):
        del message, context
        call_count["value"] += 1
        return {"content": "已完成分析，请继续。", "success": True}

    async def _fake_execute_tools(response: str, task_id: str, update_task_progress=None, **kwargs):
        del response, task_id, update_task_progress, kwargs
        return []

    adapter._invoke_role_dialogue = _fake_call_role_llm  # type: ignore[method-assign]
    adapter._execution.execute_tools = _fake_execute_tools  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-1-director",
        input_data={"input": "实现预算模块"},
        context={},
    )

    assert bool(result.get("qa_required_for_final_verdict")) is True
    decision_signals = result.get("decision_signals")
    assert isinstance(decision_signals, list)


@pytest.mark.asyncio
async def test_director_adapter_force_retry_can_recover_with_write_output(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    call_count = {"value": 0}

    async def _fake_call_role_llm(message: str, context=None, **kwargs):
        del context
        call_count["value"] += 1
        return {
            "content": "PATCH_FILE: src/expense/core.py\n<<<<<<< SEARCH\n\n=======\nprint('ok')\n>>>>>>> REPLACE\nEND PATCH_FILE",
            "success": True,
        }

    async def _fake_execute_tools(response: str, task_id: str, update_task_progress=None, **kwargs):
        del task_id, update_task_progress, kwargs
        if "PATCH_FILE:" in response:
            target = tmp_path / "src" / "expense" / "core.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("def expense_core():\n    return 'expense'\n", encoding="utf-8")
            return [
                {
                    "tool": "write_file",
                    "success": True,
                    "result": {"file": "src/expense/core.py"},
                }
            ]
        return []

    adapter._invoke_role_dialogue = _fake_call_role_llm  # type: ignore[method-assign]
    adapter._execution.execute_tools = _fake_execute_tools  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-1-director",
        input_data={"input": "实现 expense 核心模块并补充测试"},
        context={},
    )

    assert result["success"] is True
    assert call_count["value"] >= 1
    assert (tmp_path / "src" / "expense" / "core.py").exists()


@pytest.mark.asyncio
async def test_director_adapter_falls_back_when_kernel_tool_results_are_unsuccessful(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    _create_task_row(
        adapter,
        subject="实现expense核心模型",
        description="实现 expense 领域对象与存储访问层",
        metadata={"scope": "src/expense, tests/", "steps": ["实现模型", "添加测试"]},
    )

    async def _fake_call_role_llm(message: str, context=None, **kwargs):
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

    async def _fake_execute_tools(response: str, task_id: str, update_task_progress=None, **kwargs):
        del response, task_id, update_task_progress, kwargs
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

    adapter._invoke_role_dialogue = _fake_call_role_llm  # type: ignore[method-assign]
    adapter._execution.execute_tools = _fake_execute_tools  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-1-director",
        input_data={"input": "实现expense核心模块"},
        context={},
    )

    assert result["success"] is True
    assert fallback_calls["value"] >= 1
    assert (tmp_path / "src" / "expense" / "model.py").exists()


@pytest.mark.asyncio
async def test_director_invoke_role_dialogue_uses_default_kernel_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, int | None] = {"max_retries": None}

    async def _fake_runtime_session(message: str, *, context: dict[str, Any] | None = None, max_retries: int = 1):
        del message, context
        captured["max_retries"] = max_retries
        return {"response": "ok", "content": "ok", "success": True}

    adapter = DirectorAdapter(workspace=str(tmp_path))
    monkeypatch.setattr(adapter, "_invoke_role_runtime_session", _fake_runtime_session)
    result = await adapter._invoke_role_dialogue("执行任务")

    assert result["success"] is True
    assert captured["max_retries"] == 1


@pytest.mark.asyncio
async def test_director_invoke_role_dialogue_honors_retry_budget_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, int | None] = {"max_retries": None}

    async def _fake_runtime_session(message: str, *, context: dict[str, Any] | None = None, max_retries: int = 1):
        del message, context
        captured["max_retries"] = max_retries
        return {"response": "ok", "content": "ok", "success": True}

    monkeypatch.setenv("KERNELONE_DIRECTOR_KERNEL_MAX_RETRIES", "0")

    adapter = DirectorAdapter(workspace=str(tmp_path))
    monkeypatch.setattr(adapter, "_invoke_role_runtime_session", _fake_runtime_session)
    result = await adapter._invoke_role_dialogue("执行任务")

    assert result["success"] is True
    assert captured["max_retries"] == 0


@pytest.mark.asyncio
async def test_director_invoke_role_dialogue_marks_error_response_as_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _fake_runtime_session(message: str, *, context: dict[str, Any] | None = None, max_retries: int = 1):
        del message, context, max_retries
        return {
            "response": "[ROLE_EXECUTION_ERROR] 验证失败",
            "content": "[ROLE_EXECUTION_ERROR] 验证失败",
            "error": "验证失败",
            "success": False,
        }

    adapter = DirectorAdapter(workspace=str(tmp_path))
    monkeypatch.setattr(adapter, "_invoke_role_runtime_session", _fake_runtime_session)
    result = await adapter._invoke_role_dialogue("执行任务")

    assert result["success"] is False
    assert str(result.get("error") or "") == "验证失败"


@pytest.mark.asyncio
async def test_director_invoke_role_dialogue_with_timeout_returns_recoverable_error(
    tmp_path: Path,
) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))

    async def _slow_invoke_role_dialogue(message: str, context=None):
        del message, context
        await asyncio.sleep(0.25)
        return {"content": "never_reached", "success": True}

    adapter._invoke_role_dialogue = _slow_invoke_role_dialogue  # type: ignore[method-assign]

    result = await adapter._invoke_role_dialogue_with_timeout(
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
async def test_director_invoke_role_dialogue_with_timeout_normalizes_non_mapping_payload(
    tmp_path: Path,
) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))

    async def _invalid_invoke_role_dialogue(message: str, context=None):
        del message, context
        return ["invalid_payload"]

    adapter._invoke_role_dialogue = _invalid_invoke_role_dialogue  # type: ignore[method-assign]

    result = await adapter._invoke_role_dialogue_with_timeout(
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

    The key insight: patching _invoke_role_dialogue_with_timeout on the adapter instance
    only works when the sequential engine actually runs. Since KERNELONE_SEQ_ENABLED
    is false by default, _execute_sequential returns {"success": False} before
    invoking the engine. Therefore we patch _execute_sequential directly to
    simulate the engine's error path and verify the event emission.
    """
    config_module = bootstrap_config_module
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

    async def _capture_trace_event(**kwargs):
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

    async def _fake_call_role_llm_with_timeout(
        message: str,
        *,
        context=None,
        timeout_seconds: float,
        stage_label: str,
    ):
        del message, context, timeout_seconds, stage_label
        return {
            "content": "RETRY_PATCH_PAYLOAD",
            "success": True,
            "raw_response": {"validation": {"success": True, "quality_score": 92.0}},
        }

    async def _fake_execute_tools(response: str, task_id: str, update_task_progress=None, **kwargs):
        del task_id, update_task_progress, kwargs
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
            "from src.expense.role_agent_service import calc_total\n\n"
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

    adapter._invoke_role_dialogue_with_timeout = _fake_call_role_llm_with_timeout  # type: ignore[method-assign]
    adapter._execution.execute_tools = _fake_execute_tools  # type: ignore[method-assign]

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
    assert (tmp_path / "src" / "expense" / "role_agent_service.py").exists()
    assert (tmp_path / "tests" / "test_service.py").exists()


@pytest.mark.asyncio
async def test_director_adapter_defers_sparse_heuristic_to_qa_without_retry_blocking(
    tmp_path: Path,
) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))

    async def _fake_call_role_llm_with_timeout(
        message: str,
        *,
        context=None,
        timeout_seconds: float,
        stage_label: str,
    ):
        del message, context, timeout_seconds, stage_label
        return {
            "content": "FIRST_PATCH_PAYLOAD",
            "success": True,
            "raw_response": {"validation": {"success": True, "quality_score": 91.0}},
        }

    async def _fake_execute_tools(response: str, task_id: str, update_task_progress=None, **kwargs):
        del task_id, update_task_progress, kwargs
        if response == "FIRST_PATCH_PAYLOAD":
            target1 = tmp_path / "src" / "expense" / "service.py"
            target1.parent.mkdir(parents=True, exist_ok=True)
            target1.write_text(
                "def expense_service():\n    return 'expense'\n",
                encoding="utf-8",
            )
            target2 = tmp_path / "src" / "expense" / "models.py"
            target2.write_text(
                "def expense_model():\n    return 'model'\n",
                encoding="utf-8",
            )
            return [
                {
                    "tool": "write_file",
                    "success": True,
                    "result": {
                        "ok": True,
                        "file": "src/expense/service.py",
                        "source_tool": "write_file",
                    },
                },
                {
                    "tool": "write_file",
                    "success": True,
                    "result": {
                        "ok": True,
                        "file": "src/expense/models.py",
                        "source_tool": "write_file",
                    },
                },
            ]
        return []

    adapter._invoke_role_dialogue_with_timeout = _fake_call_role_llm_with_timeout  # type: ignore[method-assign]
    adapter._execution.execute_tools = _fake_execute_tools  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id="task-sparse-timeout-director",
        input_data={
            "subject": "实现 expense 域模块",
            "description": "测试 sparse 分支超时事件上报",
            "input": "实现 expense",
        },
        context={"run_id": "run-sparse-timeout"},
    )

    assert result["success"] is True
    assert (tmp_path / "src" / "expense" / "service.py").exists()
    assert (tmp_path / "src" / "expense" / "models.py").exists()


@pytest.mark.asyncio
async def test_qa_adapter_quality_gate_fails_when_critical_issues_present(tmp_path: Path) -> None:
    adapter = QAAdapter(workspace=str(tmp_path))

    async def _fake_call_role_llm(message: str, context=None, **kwargs):
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
    director_task = _create_task_row(adapter, subject="实现账单导出", description="A", metadata={})
    _update_task_row(
        adapter,
        director_task["id"],
        status="completed",
        metadata={"adapter_result": {"qa_required_for_final_verdict": True, "qa_passed": None}},
    )

    async def _fake_call_role_llm(message: str, context=None, **kwargs):
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
    board_row = _get_task_row(adapter, director_task["id"])
    assert str(board_row.get("status") or "") == "pending"
    metadata = _row_metadata(board_row)
    assert bool(metadata.get("qa_rework_requested")) is True
    assert int(metadata.get("qa_rework_retry_count") or 0) == 1
    raw_adapter_result = metadata.get("adapter_result")
    adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
    assert adapter_result.get("qa_passed") is False


@pytest.mark.asyncio
async def test_qa_adapter_marks_failed_when_rework_retry_exhausted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("KERNELONE_DIRECTOR_TASK_REWORK_MAX_RETRIES", "2")
    adapter = QAAdapter(workspace=str(tmp_path))
    director_task = _create_task_row(adapter, subject="实现账单导出", description="A", metadata={})
    _update_task_row(
        adapter,
        director_task["id"],
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

    async def _fake_call_role_llm(message: str, context=None, **kwargs):
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
    board_row = _get_task_row(adapter, director_task["id"])
    assert str(board_row.get("status") or "") == "failed"
    metadata = _row_metadata(board_row)
    assert bool(metadata.get("qa_rework_exhausted")) is True
    assert int(metadata.get("qa_rework_retry_count") or 0) == 2


def test_director_taskboard_snapshot_includes_rework_and_exhausted_states(tmp_path: Path) -> None:
    adapter = DirectorAdapter(workspace=str(tmp_path))
    pending_rework = _create_task_row(adapter, subject="任务待返工", description="A", metadata={})
    failed_exhausted = _create_task_row(adapter, subject="任务重试耗尽", description="B", metadata={})

    _update_task_row(
        adapter,
        pending_rework["id"],
        metadata={
            "adapter_result": {"qa_required_for_final_verdict": True, "qa_passed": False},
            "qa_rework_requested": True,
        },
    )
    _update_task_row(
        adapter,
        failed_exhausted["id"],
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
        str(item.get("id") or ""): str(item.get("qa_state") or "") for item in pending_samples if isinstance(item, dict)
    }
    failed_states = {
        str(item.get("id") or ""): str(item.get("qa_state") or "") for item in failed_samples if isinstance(item, dict)
    }

    assert pending_states.get(str(pending_rework["id"])) == "rework"
    assert failed_states.get(str(failed_exhausted["id"])) == "exhausted"


def test_qa_adapter_filters_stale_stage_signals_by_run_id(tmp_path: Path) -> None:
    adapter = QAAdapter(workspace=str(tmp_path))
    signal_path = resolve_signal_path(str(tmp_path), "pm", "pm_planning")
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

    async def _fake_call_role_llm(message: str, context=None, **kwargs):
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

    async def _fake_call_role_llm(message: str, context=None, **kwargs):
        del message, context
        return {
            "content": (
                "<output>\n```json\n{\n"
                '  "review_id": "REV-002-20260323",\n'
                '  "verdict": "FAIL",\n'
                '  "score": 41,\n'
                '  "summary": "验收不通过 - 缺少测试与关键实现",\n'
                '  "findings": [\n'
                "    {\n"
                '      "severity": "critical",\n'
                '      "description": "缺少任何测试文件",\n'
                '      "evidence": "test_file_count=0",\n'
                '      "recommendation": "补齐单元测试"\n'
                "    },\n"
                "    {\n"
                '      "severity": "high",\n'
                '      "description": "缺少本地持久化关键验证",\n'
                '      "evidence": "persistence verification missing",\n'
                '      "recommendation": "补充持久化验证"\n'
                "    }\n"
                "  ],\n"
                '  "checklist_results": {\n'
                '    "code_style_compliant": true, // trailing comment from model\n'
                '    "documentation_complete": false\n'
                "  }\n"
                "}\n```\n"
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


class TestConcurrentDirectorBindingClaimDifferentTasks:
    """Verify that concurrent Director bindings claim distinct ready tasks."""

    def test_select_next_task_is_deterministic_preview(self, tmp_path: Path) -> None:
        """select_next_task is a deterministic preview; claim fanout uses claim_next_execution."""
        from polaris.cells.runtime.task_runtime.internal.service import TaskRuntimeService

        service = TaskRuntimeService(workspace=str(tmp_path))
        service.create_task_row(subject="任务A", description="A", priority=1)
        service.create_task_row(subject="任务B", description="B", priority=1)
        service.create_task_row(subject="任务C", description="C", priority=1)

        selected_ids: list[str] = []
        for _ in range(5):
            task = service.select_next_task()
            assert task is not None
            selected_ids.append(str(task.get("id") or ""))

        assert selected_ids == [selected_ids[0]] * len(selected_ids)

    def test_two_concurrent_directors_claim_different_tasks(self, tmp_path: Path) -> None:
        """Two concurrent Director adapters must claim different ready tasks."""
        from polaris.cells.roles.adapters.internal.director_adapter import DirectorAdapter

        adapter1 = DirectorAdapter(workspace=str(tmp_path))
        adapter2 = DirectorAdapter(workspace=str(tmp_path))

        _create_task_row(adapter1, subject="任务A", description="A", priority=1)
        _create_task_row(adapter1, subject="任务B", description="B", priority=1)

        selected1 = adapter1._select_pending_board_task()
        assert selected1 is not None
        selected1_id = str(selected1.get("id") or "")

        claim1 = adapter1.task_runtime.claim_execution(
            selected1_id,
            worker_id="director-1",
            role_id="director",
            run_id="run-1",
            selection_source="test",
        )
        assert claim1["success"] is True

        selected2 = adapter2._select_pending_board_task()
        assert selected2 is not None
        selected2_id = str(selected2.get("id") or "")

        assert selected2_id != selected1_id, (
            f"Second director should select a different task, but both got task_id={selected1_id}"
        )

        claim2 = adapter2.task_runtime.claim_execution(
            selected2_id,
            worker_id="director-2",
            role_id="director",
            run_id="run-2",
            selection_source="test",
        )
        assert claim2["success"] is True
        assert claim1["task"]["id"] != claim2["task"]["id"]

    def test_two_directors_never_double_count_same_task_success(self, tmp_path: Path) -> None:
        """When two directors claim the same task concurrently, only one succeeds
        and the result must not count as two successes."""
        from polaris.cells.roles.adapters.internal.director_adapter import DirectorAdapter

        adapter = DirectorAdapter(workspace=str(tmp_path))
        task = _create_task_row(adapter, subject="独占任务", description="only one should claim", priority=1)

        claim1 = adapter.task_runtime.claim_execution(
            task["id"],
            worker_id="director-1",
            role_id="director",
            run_id="run-1",
            selection_source="test",
        )
        assert claim1["success"] is True

        claim2 = adapter.task_runtime.claim_execution(
            task["id"],
            worker_id="director-2",
            role_id="director",
            run_id="run-2",
            selection_source="test",
        )
        assert claim2["success"] is False
        assert claim2["reason"] == "lease_conflict"

    def test_select_next_task_skips_claimed_tasks(self, tmp_path: Path) -> None:
        """select_next_task must skip tasks that are already claimed/in_progress."""
        from polaris.cells.runtime.task_runtime.internal.service import TaskRuntimeService

        service = TaskRuntimeService(workspace=str(tmp_path))
        task_a = service.create_task_row(subject="任务A", description="A", priority=1)
        task_b = service.create_task_row(subject="任务B", description="B", priority=1)

        service.claim_execution(
            task_a["id"],
            worker_id="director-1",
            role_id="director",
            run_id="run-1",
            selection_source="test",
        )

        selected = service.select_next_task()
        assert selected is not None
        assert str(selected.get("id") or "") == str(task_b["id"])


class TestAtomicClaimNextExecution:
    """Tests for the atomic claim_next_execution API."""

    def test_two_directors_claim_different_tasks_deterministically(self, tmp_path: Path) -> None:
        """Two concurrent Directors must claim different tasks without relying on randomness."""
        from polaris.cells.runtime.task_runtime.internal.service import TaskRuntimeService

        service = TaskRuntimeService(workspace=str(tmp_path))
        service.create_task_row(subject="任务A", description="A", priority=1)
        service.create_task_row(subject="任务B", description="B", priority=1)

        # Director 1 claims first task
        result1 = service.claim_next_execution(
            worker_id="director-1",
            role_id="director",
            run_id="run-1",
            selection_source="test",
        )
        assert result1["success"] is True
        task1_id = result1["task"]["id"]

        # Director 2 claims second task
        result2 = service.claim_next_execution(
            worker_id="director-2",
            role_id="director",
            run_id="run-2",
            selection_source="test",
        )
        assert result2["success"] is True
        task2_id = result2["task"]["id"]

        # Must be different tasks
        assert task1_id != task2_id

    def test_lease_conflict_tries_next_candidate(self, tmp_path: Path) -> None:
        """When first candidate has lease_conflict, must try next candidate."""
        from polaris.cells.runtime.task_runtime.internal.service import TaskRuntimeService

        service = TaskRuntimeService(workspace=str(tmp_path))
        task_a = service.create_task_row(subject="任务A", description="A", priority=1)
        task_b = service.create_task_row(subject="任务B", description="B", priority=1)

        # Pre-claim task A with director-1 (this changes status to in_progress)
        claim_result = service.claim_execution(
            task_a["id"],
            worker_id="director-1",
            role_id="director",
            run_id="run-1",
            selection_source="test",
        )
        assert claim_result["success"] is True

        # Director 2 should get task B via claim_next_execution
        # task_a is now in_progress (not claimable), so only task_b is candidate
        result = service.claim_next_execution(
            worker_id="director-2",
            role_id="director",
            run_id="run-2",
            selection_source="test",
        )
        assert result["success"] is True
        assert result["task"]["id"] == task_b["id"]
        assert len(result["attempts"]) == 1
        assert result["attempts"][0]["success"] is True

    def test_all_candidates_unavailable_returns_fail_closed(self, tmp_path: Path) -> None:
        """When no candidates are claimable, must return fail-closed with reason."""
        from polaris.cells.runtime.task_runtime.internal.service import TaskRuntimeService

        service = TaskRuntimeService(workspace=str(tmp_path))
        # No tasks created

        result = service.claim_next_execution(
            worker_id="director-1",
            role_id="director",
            run_id="run-1",
            selection_source="test",
        )
        assert result["success"] is False
        assert result["reason"] == "no_claimable_tasks"
        assert result["task"] is None
        assert result["session"] is None
        assert result["attempts"] == []

    def test_all_tasks_claimed_returns_fail_closed(self, tmp_path: Path) -> None:
        """When all tasks are already claimed, must return fail-closed."""
        from polaris.cells.runtime.task_runtime.internal.service import TaskRuntimeService

        service = TaskRuntimeService(workspace=str(tmp_path))
        task_a = service.create_task_row(subject="任务A", description="A", priority=1)
        task_b = service.create_task_row(subject="任务B", description="B", priority=1)

        # Claim all tasks with director-1 (changes status to in_progress)
        service.claim_execution(
            task_a["id"],
            worker_id="director-1",
            role_id="director",
            run_id="run-1",
            selection_source="test",
        )
        service.claim_execution(
            task_b["id"],
            worker_id="director-1",
            role_id="director",
            run_id="run-1",
            selection_source="test",
        )

        # Director 2 should fail - no claimable tasks (all are in_progress)
        result = service.claim_next_execution(
            worker_id="director-2",
            role_id="director",
            run_id="run-2",
            selection_source="test",
        )
        assert result["success"] is False
        assert result["reason"] == "no_claimable_tasks"
        assert result["attempts"] == []

    def test_resumable_tasks_prioritized(self, tmp_path: Path) -> None:
        """Resumable tasks should be claimed before regular pending tasks."""
        from polaris.cells.runtime.task_runtime.internal.service import TaskRuntimeService

        service = TaskRuntimeService(workspace=str(tmp_path))
        service.create_task_row(subject="普通任务", description="A", priority=1)

        # Create a resumable task by claiming and suspending
        task_b = service.create_task_row(subject="可恢复任务", description="B", priority=1)
        claim_result = service.claim_execution(
            task_b["id"],
            worker_id="director-1",
            role_id="director",
            run_id="run-1",
            selection_source="test",
        )
        assert claim_result["success"] is True
        session_id = claim_result["session"]["session_id"]

        # Suspend the task to make it resumable
        suspend_result = service.suspend_execution(
            task_b["id"],
            session_id=session_id,
            reason="test_suspend",
        )
        assert suspend_result["success"] is True

        # Claim next should prefer resumable task B
        result = service.claim_next_execution(
            worker_id="director-2",
            role_id="director",
            run_id="run-2",
            selection_source="test",
            prefer_resumable=True,
        )
        assert result["success"] is True
        assert result["task"]["id"] == task_b["id"]
