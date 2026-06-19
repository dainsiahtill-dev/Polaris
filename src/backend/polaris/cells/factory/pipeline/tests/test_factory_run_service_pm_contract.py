from __future__ import annotations

import json
from typing import Any

from polaris.cells.factory.pipeline.internal.factory_run_service import OrchestrationStageExecutor


def _write_plan(executor: OrchestrationStageExecutor, tasks: list[dict[str, Any]]) -> None:
    target = executor._artifact_path("tasks/plan.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"tasks": tasks}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_pm_plan_contract_rejects_meta_diagnostic_tasks(tmp_path) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    _write_plan(
        executor,
        [
            {
                "id": "TASK-1",
                "title": "实现重复签名",
                "goal": "多个任务标题/goal 重复，导致 TASK-18/27 被标记为 duplicated。",
                "description": "多个任务标题/goal 重复，导致 TASK-18/27 被标记为 duplicated。",
                "scope": "src/, tests/",
                "steps": ["分析并定位", "实现并补充测试"],
                "acceptance": ["相关测试命令执行通过"],
            },
            {
                "id": "TASK-2",
                "title": "实现格式违规",
                "goal": "输出了非 JSON 内容（Markdown 说明、分隔线、加粗文字等）。",
                "description": "输出了非 JSON 内容（Markdown 说明、分隔线、加粗文字等）。",
                "scope": "src/, tests/",
                "steps": ["分析并定位", "实现并补充测试"],
                "acceptance": ["相关测试命令执行通过"],
            },
        ],
    )

    assert executor._validate_pm_plan_contract() == "tasks_plan_meta_diagnostic_tasks:2"


def test_pm_plan_contract_accepts_product_implementation_tasks(tmp_path) -> None:
    executor = OrchestrationStageExecutor(tmp_path)
    _write_plan(
        executor,
        [
            {
                "id": "TASK-1",
                "title": "实现城市天气搜索界面",
                "goal": "构建城市输入、搜索按钮和当前天气展示区。",
                "description": "实现天气应用的城市查询交互和基础结果展示。",
                "scope": "src/, tests/",
                "steps": ["创建页面结构", "接入天气查询服务", "补充界面测试"],
                "acceptance": ["可搜索城市天气", "相关测试命令执行通过"],
            }
        ],
    )

    assert executor._validate_pm_plan_contract() == ""
