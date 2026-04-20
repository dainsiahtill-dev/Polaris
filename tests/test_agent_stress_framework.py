from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
from pathlib import Path

import httpx
import pytest
from rich.console import Console

import tests.agent_stress as agent_stress_package
import tests.agent_stress.observer as agent_stress_observer_module
import tests.agent_stress.runner as agent_stress_runner_module

# Skip this module if core.polaris_loop is not available (migrated to polaris)
try:
    from core.polaris_loop.storage_layout import (
        resolve_runtime_path,
        resolve_workspace_persistent_path,
    )
    from core.stress_path_policy import (
        default_stress_runtime_root,
        default_stress_workspace_base,
        stress_workspace_root,
    )
except ImportError:
    pytest.importorskip("core.polaris_loop.storage_layout")
from tests.agent_stress.engine import RoundResult, StageExecution, StageResult, StressEngine
from tests.agent_stress.backend_bootstrap import ManagedBackendSession
from tests.agent_stress.backend_context import BackendContext, get_desktop_backend_info_path, resolve_backend_context
from tests.agent_stress.observer import ObserverState
from tests.agent_stress.observability import (
    DiagnosticReport,
    FailureCategory,
    ObservabilityCollector,
)
from tests.agent_stress.preflight import BackendPreflightProbe, BackendPreflightStatus
from tests.agent_stress.probe import ProbeStatus, RoleAvailabilityProbe, RoleProbeResult
from tests.agent_stress.project_pool import PROJECT_POOL, build_rotation_order, select_stress_rounds
from tests.agent_stress.runner import AgentStressRunner
from tests.agent_stress.tracer import QAConclusion, RoundTrace, RuntimeTracer, TaskLineage

DEFAULT_STRESS_WORKSPACE = default_stress_workspace_base("tests-agent-stress")
DEFAULT_STRESS_RAMDISK = default_stress_runtime_root("tests-agent-stress-runtime")


def test_agent_stress_package_exports_current_public_api() -> None:
    for name in agent_stress_package.__all__:
        assert hasattr(agent_stress_package, name), name


def test_project_pool_matches_daily_practice_catalog() -> None:
    assert [project.name for project in PROJECT_POOL] == [
        "个人记账簿 (账单管理)",
        "待办事项清单 (To-Do List)",
        "简易 Markdown 编辑器",
        "实时聊天室 (WebSocket)",
        "博客系统 (CMS)",
        "天气预报展示器",
        "个人简历生成器",
        "抽奖/随机点名工具",
        "番茄钟 (专注计时器)",
        "密码管理器 (加密存储)",
        "图片占位符生成器",
        "在线剪贴板 (跨端传词)",
        "聚合搜索工具 (一键搜多站)",
        "简易单位转换器 (汇率/度量)",
        "文件断点续传器",
        "静态网站生成器 (SSG)",
        "RSS 阅读器",
        "自动化签到脚本",
        "屏幕截图/录屏工具",
        "贪吃蛇/俄罗斯方块小游戏",
    ]
    assert len(PROJECT_POOL) == 20


def test_rotation_strategy_covers_full_catalog_once_before_repeat() -> None:
    first_pass = select_stress_rounds(20, strategy="rotation")
    second_pass = select_stress_rounds(40, strategy="rotation")
    rotation_order = build_rotation_order()

    assert len(first_pass) == 20
    assert [project.id for project in first_pass] == [project.id for project in rotation_order]
    assert len({project.id for project in first_pass}) == 20
    assert [project.id for project in second_pass[:20]] == [project.id for project in rotation_order]
    assert [project.id for project in second_pass[20:40]] == [project.id for project in rotation_order]


def test_select_stress_rounds_respects_empty_filtered_pool() -> None:
    assert select_stress_rounds(5, strategy="rotation", pool=[]) == []


def test_backend_context_prefers_desktop_backend_info(tmp_path: Path) -> None:
    env = {"APPDATA": str(tmp_path)}
    desktop_info = get_desktop_backend_info_path(env=env, platform="win32")
    desktop_info.parent.mkdir(parents=True, exist_ok=True)
    desktop_info.write_text(
        json.dumps(
            {
                "state": "running",
                "ready": True,
                "backend": {
                    "baseUrl": "http://127.0.0.1:51234",
                    "token": "secret-token",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    context = resolve_backend_context(env=env, platform="win32")

    assert context.backend_url == "http://127.0.0.1:51234"
    assert context.token == "secret-token"
    assert context.source == "desktop-backend-info"


def test_backend_context_does_not_guess_default_port(tmp_path: Path) -> None:
    env = {"APPDATA": str(tmp_path)}

    context = resolve_backend_context(env=env, platform="win32")

    assert context.backend_url == ""
    assert context.token == ""
    assert context.source == "unresolved"


def test_retry_policy_routes_project_output_failures_to_pm() -> None:
    result = RoundResult(
        round_number=1,
        project=PROJECT_POOL[0],
        start_time="2026-03-12T00:00:00+00:00",
        failure_point="project_output_not_project_specific",
        root_cause="project output did not match domain",
    )

    next_stage = AgentStressRunner._select_retry_start_from(
        result,
        architect_ready=True,
        pm_ready=True,
    )

    assert next_stage == "pm"


def test_retry_policy_routes_qa_gate_failures_to_director() -> None:
    result = RoundResult(
        round_number=1,
        project=PROJECT_POOL[0],
        start_time="2026-03-12T00:00:00+00:00",
        failure_point="quality_gate",
        root_cause="qa gate rejected implementation quality",
    )

    next_stage = AgentStressRunner._select_retry_start_from(
        result,
        architect_ready=True,
        pm_ready=True,
    )

    assert next_stage == "director"


def test_retry_policy_routes_sparse_quality_gate_failures_to_pm() -> None:
    result = RoundResult(
        round_number=1,
        project=PROJECT_POOL[0],
        start_time="2026-03-12T00:00:00+00:00",
        failure_point="quality_gate",
        root_cause="qa gate rejected implementation quality",
        workspace_artifacts={
            "new_code_file_count": 1,
            "new_code_line_count": 320,
            "quality_gate": {
                "min_new_code_files": 2,
                "min_new_code_lines": 80,
                "min_generic_scaffold_markers": 2,
            },
        },
    )

    next_stage = AgentStressRunner._select_retry_start_from(
        result,
        architect_ready=True,
        pm_ready=True,
    )

    assert next_stage == "pm"


def test_retry_guidance_includes_quality_gate_thresholds() -> None:
    result = RoundResult(
        round_number=1,
        project=PROJECT_POOL[0],
        start_time="2026-03-12T00:00:00+00:00",
        failure_point="quality_gate",
        root_cause="qa gate rejected implementation quality",
        workspace_artifacts={
            "new_code_file_count": 1,
            "new_code_line_count": 120,
            "quality_gate": {
                "min_new_code_files": 2,
                "min_new_code_lines": 80,
                "min_generic_scaffold_markers": 2,
            },
        },
    )

    guidance = AgentStressRunner._build_retry_guidance(result)

    assert "new_or_modified_code_files >= 2" in guidance
    assert "new_or_modified_code_files=1" in guidance


@pytest.mark.asyncio
async def test_engine_stage_tracking_maps_current_factory_phase_names(tmp_path: Path) -> None:
    engine = StressEngine(workspace=tmp_path, backend_url="http://unit.test")
    try:
        result = RoundResult(
            round_number=1,
            project=PROJECT_POOL[0],
            start_time="2026-03-08T00:00:00+00:00",
        )

        engine._update_stage_executions(
            {
                "phase": "planning",
                "current_stage": "pm_planning",
                "status": "running",
                "created_at": "2026-03-08T00:00:00+00:00",
                "roles": {},
            },
            result,
        )
        assert result.architect_stage is not None
        assert result.architect_stage.result == StageResult.SUCCESS
        assert result.pm_stage is not None
        assert result.pm_stage.result == StageResult.PENDING

        engine._update_stage_executions(
            {
                "phase": "qa_gate",
                "current_stage": "quality_gate",
                "status": "completed",
                "created_at": "2026-03-08T00:00:00+00:00",
                "completed_at": "2026-03-08T00:10:00+00:00",
                "roles": {"director": {"status": "completed"}},
                "gates": [{"gate_name": "quality_gate", "status": "passed"}],
            },
            result,
        )

        assert result.pm_stage is not None and result.pm_stage.result == StageResult.SUCCESS
        assert result.director_stage is not None and result.director_stage.result == StageResult.SUCCESS
        assert result.qa_stage is not None and result.qa_stage.result == StageResult.SUCCESS
    finally:
        await engine.client.aclose()


@pytest.mark.asyncio
async def test_engine_resolves_round_workspace_per_project() -> None:
    workspace_root = DEFAULT_STRESS_WORKSPACE / "round-workspace-layout"
    engine = StressEngine(workspace=workspace_root, backend_url="http://unit.test")
    try:
        round_workspace = engine._resolve_round_workspace(1, PROJECT_POOL[0])
    finally:
        await engine.client.aclose()

    assert str(round_workspace).startswith(str(workspace_root.resolve()))
    assert "projects" in round_workspace.parts
    assert round_workspace.name == PROJECT_POOL[0].id


@pytest.mark.asyncio
async def test_engine_resolves_round_workspace_per_round_mode() -> None:
    workspace_root = DEFAULT_STRESS_WORKSPACE / "round-workspace-layout-per-round"
    engine = StressEngine(
        workspace=workspace_root,
        backend_url="http://unit.test",
        workspace_mode="per_round",
    )
    try:
        round_workspace = engine._resolve_round_workspace(7, PROJECT_POOL[0])
    finally:
        await engine.client.aclose()

    assert str(round_workspace).startswith(str(workspace_root.resolve()))
    assert "projects" in round_workspace.parts
    assert round_workspace.name.startswith("round-007-")
    assert PROJECT_POOL[0].id in round_workspace.name


@pytest.mark.asyncio
async def test_engine_poll_factory_run_uses_failure_detail_from_contract(tmp_path: Path) -> None:
    engine = StressEngine(workspace=tmp_path, backend_url="http://unit.test", factory_timeout=5, poll_interval=0.01)
    await engine.client.aclose()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "run_id": "run-1",
                "phase": "planning",
                "status": "failed",
                "failure": {
                    "code": "PM_FAILED",
                    "detail": "PM contract normalization failed",
                    "phase": "planning",
                },
            },
        )

    engine.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"Content-Type": "application/json"})
    result = RoundResult(round_number=1, project=PROJECT_POOL[0], start_time="2026-03-08T00:00:00+00:00")

    try:
        lifecycle = await engine._poll_factory_run("run-1", result)
    finally:
        await engine.client.aclose()

    assert lifecycle == "failed"
    assert result.failure_point == "pm_planning"
    assert result.failure_evidence == "PM contract normalization failed"
    assert result.root_cause == "PM contract normalization failed"


@pytest.mark.asyncio
async def test_engine_poll_factory_run_aborts_when_control_plane_stalls(tmp_path: Path) -> None:
    engine = StressEngine(
        workspace=tmp_path,
        backend_url="http://unit.test",
        factory_timeout=30,
        poll_interval=0.01,
        request_timeout=0.01,
        control_plane_stall_timeout=0.05,
    )
    await engine.client.aclose()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("status endpoint stalled", request=request)

    timeout = httpx.Timeout(0.01, connect=0.01)
    engine.client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=timeout,
        headers={"Content-Type": "application/json"},
    )
    result = RoundResult(round_number=1, project=PROJECT_POOL[0], start_time="2026-03-08T00:00:00+00:00")

    try:
        lifecycle = await engine._poll_factory_run("run-1", result)
    finally:
        await engine.client.aclose()

    assert lifecycle == "blocked"
    assert result.failure_point == "factory_status_observation_blocked"
    assert "non-LLM control-plane budget" in result.root_cause


def test_engine_project_output_gate_fails_when_no_code_files_generated(tmp_path: Path) -> None:
    engine = StressEngine(workspace=tmp_path, backend_url="http://unit.test")
    result = RoundResult(
        round_number=1,
        project=PROJECT_POOL[0],
        start_time="2026-03-08T00:00:00+00:00",
        overall_result="PASS",
    )

    engine._enforce_project_output_gate(result, baseline_snapshot={})

    assert result.overall_result == "FAIL"
    assert result.failure_point == "project_output_missing"
    assert result.workspace_artifacts["code_file_count"] == 0


def test_engine_project_output_gate_fails_when_round_has_no_new_code_files(tmp_path: Path) -> None:
    code_file = tmp_path / "main.py"
    code_file.write_text("print('baseline')\n", encoding="utf-8")
    engine = StressEngine(workspace=tmp_path, backend_url="http://unit.test")
    result = RoundResult(
        round_number=1,
        project=PROJECT_POOL[0],
        start_time="2026-03-08T00:00:00+00:00",
        overall_result="PASS",
    )
    baseline = engine._collect_workspace_code_files()

    engine._enforce_project_output_gate(result, baseline_snapshot=baseline)

    assert result.overall_result == "FAIL"
    assert result.failure_point == "project_output_stagnant"
    assert result.workspace_artifacts["new_code_file_count"] == 0


def test_engine_project_output_gate_fails_when_fallback_scaffold_detected(tmp_path: Path) -> None:
    app_file = tmp_path / "app.py"
    app_file.write_text(
        '"""Auto-generated starter entrypoint for Polaris stress workflow."""\n',
        encoding="utf-8",
    )
    engine = StressEngine(
        workspace=tmp_path,
        backend_url="http://unit.test",
        min_new_code_files=1,
        min_new_code_lines=1,
    )
    result = RoundResult(
        round_number=1,
        project=PROJECT_POOL[0],
        start_time="2026-03-08T00:00:00+00:00",
        overall_result="PASS",
    )

    engine._enforce_project_output_gate(result, baseline_snapshot={})

    assert result.overall_result == "FAIL"
    assert result.failure_point == "project_output_fallback_scaffold"
    assert result.workspace_artifacts["fallback_scaffold_detected"] is True


def test_engine_project_output_gate_rejects_placeholder_generic_scaffold(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "utils").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "main.py").write_text(
        "\n".join(
            [
                '"""项目主入口模块"""',
                "",
                "from utils.helpers import parse_arguments",
                "",
                "def main() -> None:",
                "    args = parse_arguments()",
                "    # TODO: 实现核心业务逻辑",
                "    print(args)",
                "",
                'if __name__ == "__main__":',
                "    main()",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "utils" / "helpers.py").write_text(
        "\n".join(
            [
                '"""通用工具函数模块"""',
                "",
                "import argparse",
                "",
                "def parse_arguments() -> argparse.Namespace:",
                '    parser = argparse.ArgumentParser(description="应用程序")',
                "    return parser.parse_args()",
                "",
                "def safe_divide(a: float, b: float) -> float:",
                "    return a / b if b else 0.0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_helpers.py").write_text(
        "\n".join(
            [
                '"""helpers 模块的单元测试"""',
                "",
                "def test_placeholder() -> None:",
                "    assert True",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    engine = StressEngine(
        workspace=tmp_path,
        backend_url="http://unit.test",
        min_new_code_files=1,
        min_new_code_lines=1,
    )
    result = RoundResult(
        round_number=1,
        project=PROJECT_POOL[1],  # todo-advanced
        start_time="2026-03-08T00:00:00+00:00",
        overall_result="PASS",
    )

    engine._enforce_project_output_gate(result, baseline_snapshot={})

    assert result.overall_result == "FAIL"
    assert result.failure_point in {"project_output_generic_scaffold", "project_output_placeholder_code"}
    assert result.workspace_artifacts["placeholder_markers"]
    assert result.workspace_artifacts["generic_scaffold_markers"]


@pytest.mark.asyncio
async def test_engine_create_factory_run_always_starts_from_architect(tmp_path: Path) -> None:
    engine = StressEngine(workspace=tmp_path, backend_url="http://unit.test")
    await engine.client.aclose()

    captured_payload = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payload.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"run_id": "run-architect"})

    engine.client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Content-Type": "application/json"},
    )

    try:
        payload = await engine._create_factory_run(PROJECT_POOL[0])
    finally:
        await engine.client.aclose()

    assert payload is not None
    assert payload.get("run_id") == "run-architect"
    assert captured_payload.get("start_from") == "architect"
    assert captured_payload.get("run_chief_engineer") is False


@pytest.mark.asyncio
async def test_engine_create_factory_run_supports_pm_start_and_optional_chief_engineer(tmp_path: Path) -> None:
    engine = StressEngine(
        workspace=tmp_path,
        backend_url="http://unit.test",
        chain_profile="compat",
        run_architect_stage=False,
        run_chief_engineer_stage=True,
    )
    await engine.client.aclose()

    captured_payload = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payload.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"run_id": "run-pm-start"})

    engine.client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Content-Type": "application/json"},
    )

    try:
        payload = await engine._create_factory_run(PROJECT_POOL[0])
    finally:
        await engine.client.aclose()

    assert payload is not None
    assert captured_payload.get("start_from") == "pm"
    assert captured_payload.get("run_chief_engineer") is True


def test_engine_chain_evidence_gate_fails_when_trace_has_zero_tasks(tmp_path: Path) -> None:
    engine = StressEngine(
        workspace=tmp_path,
        backend_url="http://unit.test",
        min_new_code_files=1,
        min_new_code_lines=1,
        require_full_chain_evidence=True,
    )
    result = RoundResult(
        round_number=1,
        project=PROJECT_POOL[0],
        start_time="2026-03-08T00:00:00+00:00",
        overall_result="PASS",
    )
    result.architect_stage = StageExecution(
        stage_name="architect",
        result=StageResult.SUCCESS,
        start_time="2026-03-08T00:00:00+00:00",
        end_time="2026-03-08T00:00:01+00:00",
        duration_ms=1000,
    )
    result.pm_stage = StageExecution(
        stage_name="pm",
        result=StageResult.SUCCESS,
        start_time="2026-03-08T00:00:01+00:00",
        end_time="2026-03-08T00:00:02+00:00",
        duration_ms=1000,
    )
    result.director_stage = StageExecution(
        stage_name="director",
        result=StageResult.SUCCESS,
        start_time="2026-03-08T00:00:02+00:00",
        end_time="2026-03-08T00:00:03+00:00",
        duration_ms=1000,
    )
    result.qa_stage = StageExecution(
        stage_name="qa",
        result=StageResult.SUCCESS,
        start_time="2026-03-08T00:00:03+00:00",
        end_time="2026-03-08T00:00:04+00:00",
        duration_ms=1000,
    )
    result.trace = RoundTrace(
        round_number=1,
        project_id=PROJECT_POOL[0].id,
        project_name=PROJECT_POOL[0].name,
        start_time="2026-03-08T00:00:00+00:00",
        total_tasks=0,
    )
    docs_plan = tmp_path / "docs" / "plan.md"
    docs_plan.parent.mkdir(parents=True, exist_ok=True)
    docs_plan.write_text("# plan\nexpense tracker\n", encoding="utf-8")
    pm_plan = tmp_path / "tasks" / "plan.json"
    pm_plan.parent.mkdir(parents=True, exist_ok=True)
    pm_plan.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "goal": "implement core flow",
                        "scope": "src and tests",
                        "steps": ["create module"],
                        "acceptance": ["tests pass"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    dispatch_log = tmp_path / "dispatch" / "log.json"
    dispatch_log.parent.mkdir(parents=True, exist_ok=True)
    dispatch_log.write_text('{"events":[{"name":"apply_patch"}]}', encoding="utf-8")
    qa_report = tmp_path / "runtime" / "qa" / "report.json"
    qa_report.parent.mkdir(parents=True, exist_ok=True)
    qa_report.write_text('{"status":"PASS"}', encoding="utf-8")
    result.workspace_artifacts = {
        "new_code_file_count": 2,
        "new_code_line_count": 120,
        "chain_stage_evidence": {
            "expected_role_order": ["architect", "pm", "director", "qa"],
            "observed_role_order": ["architect", "pm", "director", "qa"],
            "stages": {
                "architect": {
                    "declared_artifacts": ["docs/plan.md"],
                    "existing_artifacts": [str(docs_plan)],
                    "missing_artifacts": [],
                },
                "pm": {
                    "declared_artifacts": ["tasks/plan.json"],
                    "existing_artifacts": [str(pm_plan)],
                    "missing_artifacts": [],
                },
                "director": {
                    "declared_artifacts": ["dispatch/log.json"],
                    "existing_artifacts": [str(dispatch_log)],
                    "missing_artifacts": [],
                },
                "qa": {
                    "declared_artifacts": ["runtime/qa/report.json"],
                    "existing_artifacts": [str(qa_report)],
                    "missing_artifacts": [],
                },
            },
        },
    }
    result.observability_data = {"statistics": {"total_tool_executions": 3}}

    engine._enforce_chain_evidence_gate(result)

    assert result.overall_result == "FAIL"
    assert result.failure_point == "chain_trace_missing_tasks"


def test_engine_chain_evidence_gate_backfills_trace_and_tool_evidence(tmp_path: Path) -> None:
    engine = StressEngine(
        workspace=tmp_path,
        backend_url="http://unit.test",
        require_full_chain_evidence=True,
    )
    result = RoundResult(
        round_number=1,
        project=PROJECT_POOL[0],
        start_time="2026-03-08T00:00:00+00:00",
        overall_result="PASS",
    )
    result.architect_stage = StageExecution("architect", StageResult.SUCCESS, "2026-03-08T00:00:00+00:00", "2026-03-08T00:00:01+00:00", 1000)
    result.pm_stage = StageExecution("pm", StageResult.SUCCESS, "2026-03-08T00:00:01+00:00", "2026-03-08T00:00:02+00:00", 1000)
    result.director_stage = StageExecution("director", StageResult.SUCCESS, "2026-03-08T00:00:02+00:00", "2026-03-08T00:00:03+00:00", 1000)
    result.qa_stage = StageExecution("qa", StageResult.SUCCESS, "2026-03-08T00:00:03+00:00", "2026-03-08T00:00:04+00:00", 1000)
    result.trace = RoundTrace(
        round_number=1,
        project_id=PROJECT_POOL[0].id,
        project_name=PROJECT_POOL[0].name,
        start_time="2026-03-08T00:00:00+00:00",
        total_tasks=0,
    )

    docs_plan = tmp_path / "docs" / "plan.md"
    docs_plan.parent.mkdir(parents=True, exist_ok=True)
    docs_plan.write_text("# plan\nexpense tracker\n", encoding="utf-8")
    pm_plan = tmp_path / "tasks" / "plan.json"
    pm_plan.parent.mkdir(parents=True, exist_ok=True)
    pm_plan.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "goal": "implement core flow",
                        "scope": "src and tests",
                        "steps": ["create module"],
                        "acceptance": ["tests pass"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    dispatch_log = tmp_path / "dispatch" / "log.json"
    dispatch_log.parent.mkdir(parents=True, exist_ok=True)
    dispatch_log.write_text(
        json.dumps(
            {
                "status": "completed",
                "metadata": {
                    "task_count": 1,
                    "task_status_counts": {"completed": 1},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    qa_report = tmp_path / "runtime" / "qa" / "report.json"
    qa_report.parent.mkdir(parents=True, exist_ok=True)
    qa_report.write_text('{"status":"PASS"}', encoding="utf-8")
    adapter_debug = (
        tmp_path
        / ".polaris"
        / "runtime"
        / "roles"
        / "director"
        / "logs"
        / "adapter_debug_20260308.jsonl"
    )
    adapter_debug.parent.mkdir(parents=True, exist_ok=True)
    adapter_debug.write_text(
        json.dumps(
            {
                "timestamp": "2026-03-08T00:00:03+00:00",
                "event": "retry_tool_results",
                "payload": {
                    "count": 1,
                    "items": [
                        {"tool": "patch_apply", "success": True, "error": None, "file": "src/main.py"},
                    ],
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    result.workspace_artifacts = {
        "new_code_file_count": 2,
        "new_code_line_count": 120,
        "chain_stage_evidence": {
            "expected_role_order": ["architect", "pm", "director", "qa"],
            "observed_role_order": ["architect", "pm", "director", "qa"],
            "stages": {
                "architect": {
                    "declared_artifacts": ["docs/plan.md"],
                    "existing_artifacts": [str(docs_plan)],
                    "missing_artifacts": [],
                },
                "pm": {
                    "declared_artifacts": ["tasks/plan.json"],
                    "existing_artifacts": [str(pm_plan)],
                    "missing_artifacts": [],
                },
                "director": {
                    "declared_artifacts": ["dispatch/log.json"],
                    "existing_artifacts": [str(dispatch_log)],
                    "missing_artifacts": [],
                },
                "qa": {
                    "declared_artifacts": ["runtime/qa/report.json"],
                    "existing_artifacts": [str(qa_report)],
                    "missing_artifacts": [],
                },
            },
        },
    }
    result.observability_data = {"statistics": {"total_tool_executions": 0}, "tool_executions": []}

    engine._enforce_chain_evidence_gate(result)

    assert result.overall_result == "PASS"
    assert result.failure_point == ""
    assert result.trace is not None
    assert result.trace.total_tasks >= 1
    assert isinstance(result.observability_data, dict)
    assert (result.observability_data.get("statistics") or {}).get("total_tool_executions", 0) >= 1


def test_engine_chain_evidence_gate_fails_when_stage_sequence_invalid(tmp_path: Path) -> None:
    engine = StressEngine(
        workspace=tmp_path,
        backend_url="http://unit.test",
        require_full_chain_evidence=True,
    )
    result = RoundResult(
        round_number=1,
        project=PROJECT_POOL[0],
        start_time="2026-03-08T00:00:00+00:00",
        overall_result="PASS",
    )
    result.architect_stage = StageExecution("architect", StageResult.SUCCESS, "2026-03-08T00:00:00+00:00", "2026-03-08T00:00:01+00:00", 1000)
    result.pm_stage = StageExecution("pm", StageResult.SUCCESS, "2026-03-08T00:00:01+00:00", "2026-03-08T00:00:02+00:00", 1000)
    result.director_stage = StageExecution("director", StageResult.SUCCESS, "2026-03-08T00:00:02+00:00", "2026-03-08T00:00:03+00:00", 1000)
    result.qa_stage = StageExecution("qa", StageResult.SUCCESS, "2026-03-08T00:00:03+00:00", "2026-03-08T00:00:04+00:00", 1000)
    result.trace = RoundTrace(
        round_number=1,
        project_id=PROJECT_POOL[0].id,
        project_name=PROJECT_POOL[0].name,
        start_time="2026-03-08T00:00:00+00:00",
        total_tasks=1,
    )
    result.workspace_artifacts = {
        "new_code_file_count": 3,
        "new_code_line_count": 200,
        "chain_stage_evidence": {
            "expected_role_order": ["architect", "pm", "director", "qa"],
            "observed_role_order": ["pm", "architect", "director", "qa"],
            "stages": {},
        },
    }
    result.observability_data = {"statistics": {"total_tool_executions": 1}}

    engine._enforce_chain_evidence_gate(result)

    assert result.overall_result == "FAIL"
    assert result.failure_point == "chain_stage_sequence_invalid"


def test_engine_chain_evidence_gate_fails_when_pm_contract_incomplete(tmp_path: Path) -> None:
    engine = StressEngine(
        workspace=tmp_path,
        backend_url="http://unit.test",
        require_full_chain_evidence=True,
    )
    result = RoundResult(
        round_number=1,
        project=PROJECT_POOL[0],
        start_time="2026-03-08T00:00:00+00:00",
        overall_result="PASS",
    )
    result.architect_stage = StageExecution("architect", StageResult.SUCCESS, "2026-03-08T00:00:00+00:00", "2026-03-08T00:00:01+00:00", 1000)
    result.pm_stage = StageExecution("pm", StageResult.SUCCESS, "2026-03-08T00:00:01+00:00", "2026-03-08T00:00:02+00:00", 1000)
    result.director_stage = StageExecution("director", StageResult.SUCCESS, "2026-03-08T00:00:02+00:00", "2026-03-08T00:00:03+00:00", 1000)
    result.qa_stage = StageExecution("qa", StageResult.SUCCESS, "2026-03-08T00:00:03+00:00", "2026-03-08T00:00:04+00:00", 1000)
    result.trace = RoundTrace(
        round_number=1,
        project_id=PROJECT_POOL[0].id,
        project_name=PROJECT_POOL[0].name,
        start_time="2026-03-08T00:00:00+00:00",
        total_tasks=1,
    )

    docs_plan = tmp_path / "docs" / "plan.md"
    docs_plan.parent.mkdir(parents=True, exist_ok=True)
    docs_plan.write_text("# plan\n", encoding="utf-8")
    pm_plan = tmp_path / "tasks" / "plan.json"
    pm_plan.parent.mkdir(parents=True, exist_ok=True)
    pm_plan.write_text(
        json.dumps(
            {"tasks": [{"goal": "implement", "scope": "src", "steps": ["do work"]}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    dispatch_log = tmp_path / "dispatch" / "log.json"
    dispatch_log.parent.mkdir(parents=True, exist_ok=True)
    dispatch_log.write_text('{"events":[{"name":"tool_call"}]}', encoding="utf-8")
    qa_report = tmp_path / "runtime" / "qa" / "report.json"
    qa_report.parent.mkdir(parents=True, exist_ok=True)
    qa_report.write_text('{"status":"PASS"}', encoding="utf-8")
    result.workspace_artifacts = {
        "new_code_file_count": 3,
        "new_code_line_count": 200,
        "chain_stage_evidence": {
            "expected_role_order": ["architect", "pm", "director", "qa"],
            "observed_role_order": ["architect", "pm", "director", "qa"],
            "stages": {
                "architect": {
                    "declared_artifacts": ["docs/plan.md"],
                    "existing_artifacts": [str(docs_plan)],
                    "missing_artifacts": [],
                },
                "pm": {
                    "declared_artifacts": ["tasks/plan.json"],
                    "existing_artifacts": [str(pm_plan)],
                    "missing_artifacts": [],
                },
                "director": {
                    "declared_artifacts": ["dispatch/log.json"],
                    "existing_artifacts": [str(dispatch_log)],
                    "missing_artifacts": [],
                },
                "qa": {
                    "declared_artifacts": ["runtime/qa/report.json"],
                    "existing_artifacts": [str(qa_report)],
                    "missing_artifacts": [],
                },
            },
        },
    }
    result.observability_data = {"statistics": {"total_tool_executions": 1}}

    engine._enforce_chain_evidence_gate(result)

    assert result.overall_result == "FAIL"
    assert result.failure_point == "pm_contract_incomplete"


def test_engine_stage_artifact_resolution_aligns_with_storage_layout(tmp_path: Path) -> None:
    engine = StressEngine(
        workspace=tmp_path,
        backend_url="http://unit.test",
    )
    docs_path = Path(resolve_workspace_persistent_path(str(tmp_path), "workspace/docs/plan.md"))
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    docs_path.write_text("# plan\n", encoding="utf-8")

    tasks_path = Path(
        resolve_runtime_path(
            str(tmp_path),
            "runtime/tasks/plan.json",
            ramdisk_root=str(engine.ramdisk_root),
        )
    )
    tasks_path.parent.mkdir(parents=True, exist_ok=True)
    tasks_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "goal": "g",
                        "scope": "s",
                        "steps": ["x"],
                        "acceptance": ["y"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    dispatch_path = Path(
        resolve_runtime_path(
            str(tmp_path),
            "runtime/dispatch/log.json",
            ramdisk_root=str(engine.ramdisk_root),
        )
    )
    dispatch_path.parent.mkdir(parents=True, exist_ok=True)
    dispatch_path.write_text('{"events":[{"name":"tool_call"}]}', encoding="utf-8")

    resolved_docs = engine._resolve_stage_artifact_path("factory_test", "docs/plan.md")
    resolved_tasks = engine._resolve_stage_artifact_path("factory_test", "tasks/plan.json")
    resolved_dispatch = engine._resolve_stage_artifact_path("factory_test", "dispatch/log.json")

    assert resolved_docs is not None
    assert resolved_tasks is not None
    assert resolved_dispatch is not None

    # _resolve_stage_artifact_path now returns a metadata dict
    docs_path_resolved = Path(resolved_docs["path"])
    tasks_path_resolved = Path(resolved_tasks["path"])
    dispatch_path_resolved = Path(resolved_dispatch["path"])

    assert docs_path_resolved.resolve() == docs_path.resolve()
    assert tasks_path_resolved.resolve() == tasks_path.resolve()
    assert dispatch_path_resolved.resolve() == dispatch_path.resolve()


def test_engine_stage_artifact_resolution_respects_engine_ramdisk_root(tmp_path: Path) -> None:
    if os.name == "nt":
        custom_ramdisk_root = Path("X:/pytest-custom-ramdisk") / tmp_path.name
    else:
        custom_ramdisk_root = tmp_path / "custom-ramdisk"
    engine = StressEngine(
        workspace=tmp_path,
        backend_url="http://unit.test",
        ramdisk_root=custom_ramdisk_root,
    )

    tasks_path = Path(
        resolve_runtime_path(
            str(tmp_path),
            "runtime/tasks/plan.json",
            ramdisk_root=str(custom_ramdisk_root),
        )
    )
    tasks_path.parent.mkdir(parents=True, exist_ok=True)
    tasks_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "goal": "g",
                        "scope": "s",
                        "steps": ["x"],
                        "acceptance": ["y"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    resolved_tasks = engine._resolve_stage_artifact_path("factory_test", "tasks/plan.json")

    assert resolved_tasks is not None
    assert Path(resolved_tasks["path"]).resolve() == tasks_path.resolve()


def test_runner_probe_policy_downgrades_optional_architect_when_unhealthy(tmp_path: Path) -> None:
    runner = AgentStressRunner(
        workspace=tmp_path,
        output_dir=tmp_path / "reports",
        chain_profile="compat",
        run_architect_stage=True,
        require_architect_stage=False,
    )
    report = {
        "roles": [
            {"role": "pm", "status": "healthy", "configured": True, "ready": True},
            {"role": "director", "status": "healthy", "configured": True, "ready": True},
            {"role": "qa", "status": "healthy", "configured": True, "ready": True},
            {"role": "architect", "status": "unhealthy", "configured": False, "ready": False},
        ]
    }

    ok, messages = runner._apply_chain_probe_policy(report)

    assert ok is True
    assert runner.run_architect_stage is False
    assert any("architect 未就绪" in message for message in messages)


def test_runner_probe_policy_rejects_missing_required_chief_engineer(tmp_path: Path) -> None:
    runner = AgentStressRunner(
        workspace=tmp_path,
        output_dir=tmp_path / "reports",
        run_chief_engineer_stage=True,
        require_chief_engineer_stage=True,
    )
    report = {
        "roles": [
            {"role": "pm", "status": "healthy", "configured": True, "ready": True},
            {"role": "director", "status": "healthy", "configured": True, "ready": True},
            {"role": "qa", "status": "healthy", "configured": True, "ready": True},
            {"role": "chief_engineer", "status": "degraded", "configured": True, "ready": False},
        ]
    }

    ok, messages = runner._apply_chain_probe_policy(report)

    assert ok is False
    assert any("chief_engineer" in message for message in messages)


@pytest.mark.asyncio
async def test_backend_preflight_distinguishes_auth_invalid() -> None:
    probe = BackendPreflightProbe(backend_url="http://unit.test", token="bad-token")
    await probe.client.aclose()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/settings":
            return httpx.Response(401, json={"detail": "Unauthorized"})
        raise AssertionError(f"unexpected request path: {request.url.path}")

    probe.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        report = await probe.run()
    finally:
        await probe.client.aclose()

    assert report.status == BackendPreflightStatus.AUTH_INVALID
    assert report.backend_reachable is True
    assert report.auth_valid is False


@pytest.mark.asyncio
async def test_backend_preflight_distinguishes_backend_unavailable() -> None:
    probe = BackendPreflightProbe(backend_url="http://unit.test")
    await probe.client.aclose()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    probe.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        report = await probe.run()
    finally:
        await probe.client.aclose()

    assert report.status == BackendPreflightStatus.BACKEND_UNAVAILABLE
    assert report.backend_reachable is False


@pytest.mark.asyncio
async def test_backend_preflight_distinguishes_missing_context() -> None:
    async with BackendPreflightProbe(backend_url="", token="", timeout=1.0) as probe:
        report = await probe.run()

    assert report.status == BackendPreflightStatus.BACKEND_CONTEXT_MISSING
    assert report.backend_reachable is False
    assert report.auth_valid is False
    assert report.settings_accessible is False


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Windows stress-path policy is enforced under C:/Temp")
async def test_runner_rejects_workspace_outside_stress_policy(tmp_path: Path) -> None:
    runner = AgentStressRunner(workspace=tmp_path)

    exit_code = await runner.run()

    assert exit_code == 2
    assert runner.abort_reason is not None
    assert runner.abort_reason["category"] == "workspace_policy_violation"
    assert str(stress_workspace_root()) in runner.abort_reason["summary"]
    assert str(runner.output_dir).startswith(str(stress_workspace_root()))


@pytest.mark.asyncio
async def test_engine_configure_workspace_sets_ramdisk_and_validates_runtime_layout() -> None:
    engine = StressEngine(
        workspace=DEFAULT_STRESS_WORKSPACE / "round-workspace",
        backend_url="http://unit.test",
        ramdisk_root=DEFAULT_STRESS_RAMDISK,
    )
    await engine.client.aclose()
    seen_post_payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/settings":
            return httpx.Response(200, json={"workspace": "C:/Temp/previous"})
        if request.method == "POST" and request.url.path == "/settings":
            seen_post_payload.update(json.loads(request.content.decode("utf-8")))
            return httpx.Response(200, json={"workspace": str(engine.workspace)})
        if request.method == "GET" and request.url.path == "/runtime/storage-layout":
            return httpx.Response(
                200,
                json={
                    "runtime_base": str(DEFAULT_STRESS_RAMDISK / "projects"),
                    "runtime_root": str(DEFAULT_STRESS_RAMDISK / "projects" / "demo" / "runtime"),
                    "runtime_project_root": str(DEFAULT_STRESS_RAMDISK / "projects" / "demo"),
                },
            )
        raise AssertionError(f"unexpected request path: {request.method} {request.url.path}")

    engine.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"Content-Type": "application/json"})
    try:
        configured = await engine._configure_workspace()
    finally:
        await engine.client.aclose()

    assert configured is True
    assert seen_post_payload == {
        "workspace": str((DEFAULT_STRESS_WORKSPACE / "round-workspace").resolve()),
        "ramdisk_root": str(DEFAULT_STRESS_RAMDISK),
    }


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "nt", reason="Windows stress-path policy enforces X:/ runtime roots")
async def test_engine_configure_workspace_rejects_runtime_layout_outside_ramdisk_policy() -> None:
    engine = StressEngine(
        workspace=DEFAULT_STRESS_WORKSPACE / "round-workspace",
        backend_url="http://unit.test",
        ramdisk_root=DEFAULT_STRESS_RAMDISK,
    )
    await engine.client.aclose()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/settings" and request.method == "GET":
            return httpx.Response(200, json={"workspace": "C:/Temp/previous"})
        if request.url.path == "/settings" and request.method == "POST":
            return httpx.Response(200, json={"workspace": str(engine.workspace)})
        if request.url.path == "/runtime/storage-layout" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "runtime_base": "C:/Temp/not-ramdisk/runtime-base",
                    "runtime_root": "C:/Temp/not-ramdisk/runtime-root",
                    "runtime_project_root": "C:/Temp/not-ramdisk/project-root",
                },
            )
        raise AssertionError(f"unexpected request path: {request.method} {request.url.path}")

    engine.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"Content-Type": "application/json"})
    try:
        configured = await engine._configure_workspace()
    finally:
        await engine.client.aclose()

    assert configured is False


def test_runner_classifies_unconfigured_probe_separately(tmp_path: Path) -> None:
    runner = AgentStressRunner(workspace=tmp_path, output_dir=tmp_path / "reports")
    category = runner._classify_probe_failure(
        {
            "roles": [
                {"role": "pm", "configured": False, "ready": False, "error": "Role not configured"},
                {"role": "director", "configured": False, "ready": False, "error": "Role not configured"},
            ]
        }
    )
    assert category["category"] == "roles_unconfigured"


def test_runner_markdown_report_uses_probe_status_emoji(tmp_path: Path) -> None:
    runner = AgentStressRunner(workspace=tmp_path, output_dir=tmp_path / "reports")
    runner.start_time = "2026-03-08T00:00:00+00:00"
    runner.end_time = "2026-03-08T00:10:00+00:00"
    runner.backend_preflight_report = {
        "status": "healthy",
        "backend_reachable": True,
        "auth_valid": True,
        "settings_accessible": True,
    }
    runner.probe_report = {
        "summary": {"total_roles": 2, "healthy": 1, "degraded": 1, "unhealthy": 0},
        "roles": [
            {"role": "pm", "status": "healthy", "provider": "p", "model": "m", "latency_ms": 11},
            {"role": "qa", "status": "degraded", "provider": "p2", "model": "m2", "latency_ms": 12},
        ],
    }
    markdown = runner._generate_markdown_report()
    assert "| pm | 🟢 healthy |" in markdown
    assert "| qa | 🟡 degraded |" in markdown


def test_runner_project_selection_uses_shared_rotation_catalog(tmp_path: Path) -> None:
    runner = AgentStressRunner(
        workspace=DEFAULT_STRESS_WORKSPACE,
        rounds=20,
        strategy="rotation",
        output_dir=tmp_path / "reports",
    )

    selected = runner._select_projects()

    assert [project.id for project in selected] == [project.id for project in build_rotation_order()]


def test_runner_project_serial_selection_does_not_repeat_catalog(tmp_path: Path) -> None:
    runner = AgentStressRunner(
        workspace=DEFAULT_STRESS_WORKSPACE,
        rounds=25,
        strategy="rotation",
        execution_mode="project_serial",
        output_dir=tmp_path / "reports-serial",
    )

    selected = runner._select_projects()

    assert len(selected) == len(PROJECT_POOL)
    assert len({project.id for project in selected}) == len(PROJECT_POOL)


def test_runner_round_robin_selection_can_repeat_catalog(tmp_path: Path) -> None:
    runner = AgentStressRunner(
        workspace=DEFAULT_STRESS_WORKSPACE,
        rounds=25,
        strategy="rotation",
        execution_mode="round_robin",
        output_dir=tmp_path / "reports-round-robin",
    )

    selected = runner._select_projects()

    assert len(selected) == 25
    assert [project.id for project in selected[:20]] == [project.id for project in build_rotation_order()]
    assert [project.id for project in selected[20:25]] == [project.id for project in build_rotation_order()[:5]]


def test_runner_non_llm_timeout_budget_is_clamped(tmp_path: Path) -> None:
    high = AgentStressRunner(
        workspace=tmp_path,
        output_dir=tmp_path / "reports-high",
        non_llm_timeout_seconds=999,
    )
    low = AgentStressRunner(
        workspace=tmp_path,
        output_dir=tmp_path / "reports-low",
        non_llm_timeout_seconds=0,
    )

    assert high.non_llm_timeout_seconds == 120.0
    assert low.non_llm_timeout_seconds == 5.0


@pytest.mark.asyncio
async def test_observability_collector_deduplicates_events_and_marks_llm_error_failed() -> None:
    collector = ObservabilityCollector(backend_url="http://unit.test")
    await collector.client.aclose()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/factory/runs/run-1":
            return httpx.Response(200, json={"run_id": "run-1", "status": "running"})
        if path == "/v2/factory/runs/run-1/events":
            return httpx.Response(
                200,
                json=[
                    {
                        "event_id": "evt-error",
                        "type": "error",
                        "level": "error",
                        "timestamp": "2026-03-08T00:00:01+00:00",
                        "message": "tool execution failed",
                    },
                    {
                        "event_id": "evt-tool",
                        "type": "tool_complete",
                        "level": "info",
                        "timestamp": "2026-03-08T00:00:02+00:00",
                        "payload": {
                            "tool_name": "apply_patch",
                            "arguments": {"path": "a.py"},
                            "result": "ok",
                            "duration_ms": 12,
                        },
                    },
                    {
                        "event_id": "evt-stage-1",
                        "type": "stage_started",
                        "stage": "docs_generation",
                        "timestamp": "2026-03-08T00:00:03+00:00",
                    },
                    {
                        "event_id": "evt-stage-2",
                        "type": "stage_started",
                        "stage": "pm_planning",
                        "timestamp": "2026-03-08T00:00:04+00:00",
                    },
                ],
            )
        if path == "/v2/director/tasks":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "D1",
                        "subject": "Implement feature",
                        "status": "RUNNING",
                        "metadata": {
                            "pm_task_id": "PM-1",
                            "workflow_run_id": "wf-1",
                        },
                    }
                ],
            )
        if path == "/v2/director/tasks/D1/llm-events":
            assert request.url.params.get("run_id") == "wf-1"
            return httpx.Response(
                200,
                json={
                    "events": [
                        {
                            "call_id": "call-1",
                            "event_type": "llm_error",
                            "timestamp": "2026-03-08T00:00:05+00:00",
                            "role": "director",
                            "model": "gpt-test",
                            "provider": "mock",
                            "latency_ms": 120,
                            "error": "provider timeout",
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected request path: {path}")

    collector.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector.start_collection(1, "run-1")
    try:
        await collector.capture_full_snapshot()
        await collector.capture_full_snapshot()
    finally:
        await collector.client.aclose()

    assert len(collector.error_events) == 1
    assert len(collector.tool_executions) == 1
    assert len(collector.stage_transitions) == 1
    assert len(collector.llm_calls) == 1
    assert collector.llm_calls[0].success is False


@pytest.mark.asyncio
async def test_observability_snapshot_timeout_returns_partial_snapshot() -> None:
    collector = ObservabilityCollector(
        backend_url="http://unit.test",
        snapshot_timeout=1.0,
    )
    await collector.client.aclose()

    async def slow_get_json(*args, **kwargs) -> dict[str, object]:
        await asyncio.sleep(1.2)
        return {"ok": False, "error": "slow"}

    collector._safe_get_json = slow_get_json  # type: ignore[method-assign]
    collector.start_collection(1, "run-1")
    snapshot = await collector.capture_full_snapshot()

    assert "capture_timeout" in snapshot
    assert collector.collection_warnings


@pytest.mark.asyncio
async def test_observability_collector_extracts_tool_execution_from_llm_events() -> None:
    collector = ObservabilityCollector(backend_url="http://unit.test")
    await collector.client.aclose()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/factory/runs/run-llm":
            return httpx.Response(200, json={"run_id": "run-llm", "status": "running"})
        if path == "/v2/factory/runs/run-llm/events":
            return httpx.Response(200, json=[])
        if path == "/v2/director/tasks":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "D1",
                        "subject": "Run command",
                        "status": "RUNNING",
                        "metadata": {"pm_task_id": "PM-1", "workflow_run_id": "wf-1"},
                    }
                ],
            )
        if path == "/v2/director/tasks/D1/llm-events":
            return httpx.Response(
                200,
                json={
                    "events": [
                        {
                            "call_id": "call-tool-1",
                            "event_type": "tool_result",
                            "timestamp": "2026-03-08T00:00:05+00:00",
                            "role": "director",
                            "metadata": {
                                "tool_name": "execute_command",
                                "args": {"command": "dir /b"},
                                "success": True,
                                "result": {"ok": True},
                            },
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected request path: {path}")

    collector.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    collector.start_collection(1, "run-llm")
    try:
        await collector.capture_full_snapshot()
    finally:
        await collector.client.aclose()

    assert any(item.tool_name == "execute_command" for item in collector.tool_executions)


@pytest.mark.asyncio
async def test_runtime_tracer_normalizes_statuses_and_qa_gate_names(tmp_path: Path) -> None:
    tracer = RuntimeTracer(backend_url="http://unit.test", workspace=str(tmp_path))
    await tracer.client.aclose()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v2/director/tasks":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "D1",
                        "subject": "done task",
                        "status": "COMPLETED",
                        "claimed_by": "worker-1",
                        "metadata": {"pm_task_id": "PM-1"},
                    },
                    {
                        "id": "D2",
                        "subject": "failed task",
                        "status": "FAILED",
                        "claimed_by": "worker-2",
                        "metadata": {"pm_task_id": "PM-2"},
                    },
                ],
            )
        if path == "/v2/factory/runs/run-1":
            return httpx.Response(
                200,
                json={
                    "run_id": "run-1",
                    "status": "completed",
                    "gates": [
                        {
                            "gate_name": "quality_gate",
                            "status": "passed",
                            "message": "ok",
                        }
                    ],
                },
            )
        raise AssertionError(f"unexpected request path: {path}")

    tracer.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tracer.current_round = RoundTrace(
        round_number=1,
        project_id="p1",
        project_name="demo",
        start_time="2026-03-08T00:00:00+00:00",
        factory_run_id="run-1",
    )
    try:
        await tracer._sync_director_tasks()
        await tracer._sync_qa_conclusions()
    finally:
        await tracer.client.aclose()

    assert tracer.current_round.completed_tasks == 1
    assert tracer.current_round.failed_tasks == 1
    assert tracer.current_round.qa_conclusions[0].checklist_results["quality_gate"] is True


@pytest.mark.asyncio
async def test_runtime_tracer_complete_round_is_bounded_on_slow_final_sync(tmp_path: Path) -> None:
    tracer = RuntimeTracer(
        backend_url="http://unit.test",
        workspace=str(tmp_path),
        final_sync_timeout=0.05,
    )
    tracer.current_round = RoundTrace(
        round_number=1,
        project_id="p1",
        project_name="demo",
        start_time="2026-03-08T00:00:00+00:00",
        factory_run_id="run-1",
    )

    async def fake_stop() -> None:
        return None

    async def slow_sync() -> None:
        await asyncio.sleep(0.2)

    tracer.stop = fake_stop  # type: ignore[method-assign]
    tracer._sync_all = slow_sync  # type: ignore[method-assign]
    try:
        trace = await tracer.complete_round("completed")
    finally:
        await tracer.client.aclose()

    assert trace is tracer.current_round
    assert trace is not None
    assert trace.status == "completed"


@pytest.mark.asyncio
async def test_runner_load_previous_results_restores_trace_and_diagnostic(tmp_path: Path) -> None:
    runner = AgentStressRunner(workspace=tmp_path, output_dir=tmp_path / "reports")
    trace = RoundTrace(
        round_number=1,
        project_id=PROJECT_POOL[0].id,
        project_name=PROJECT_POOL[0].name,
        start_time="2026-03-08T00:00:00+00:00",
        status="failed",
        factory_run_id="run-1",
        tasks={
            "D1": TaskLineage(
                task_id="D1",
                subject="Implement demo",
                status="failed",
                pm_task_id="PM-1",
            )
        },
        qa_conclusions=[
            QAConclusion(
                review_id="qa-run-1",
                timestamp="2026-03-08T00:01:00+00:00",
                verdict="FAIL",
                confidence="high",
                summary="gate failed",
            )
        ],
        total_tasks=1,
        failed_tasks=1,
    )
    result = RoundResult(
        round_number=1,
        project=PROJECT_POOL[0],
        start_time="2026-03-08T00:00:00+00:00",
        overall_result="FAIL",
        factory_run_id="run-1",
        pm_stage=StageExecution(
            stage_name="pm",
            result=StageResult.FAILURE,
            start_time="2026-03-08T00:00:00+00:00",
            end_time="2026-03-08T00:01:00+00:00",
            duration_ms=60000,
        ),
        trace=trace,
        failure_point="planning",
        failure_evidence="bad plan",
        root_cause="bad plan",
        diagnostic_report=DiagnosticReport(
            round_number=1,
            factory_run_id="run-1",
            failure_category=FailureCategory.RUNTIME_CRASH,
            failure_point="planning",
            timestamp="2026-03-08T00:01:00+00:00",
            summary="failed",
        ),
        observability_data={"tool_executions": [{"tool_name": "apply_patch"}]},
    )
    payload = {"results": [result.to_dict()]}
    results_path = runner.output_dir / "stress_results.json"
    runner.output_dir.mkdir(parents=True, exist_ok=True)
    results_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    await runner._load_previous_results()

    restored = runner.results[0]
    assert restored.trace is not None
    assert restored.trace.tasks["D1"].pm_task_id == "PM-1"
    assert restored.diagnostic_report is not None
    assert restored.diagnostic_report.failure_category == FailureCategory.RUNTIME_CRASH
    assert restored.observability_data == {"tool_executions": [{"tool_name": "apply_patch"}]}


def test_runner_json_report_uses_derived_tool_audit_and_truthful_pm_quality(tmp_path: Path) -> None:
    runner = AgentStressRunner(workspace=tmp_path, output_dir=tmp_path / "reports")
    runner.start_time = "2026-03-08T00:00:00+00:00"
    runner.end_time = "2026-03-08T00:10:00+00:00"
    runner.results = [
        RoundResult(
            round_number=1,
            project=PROJECT_POOL[0],
            start_time="2026-03-08T00:00:00+00:00",
            end_time="2026-03-08T00:10:00+00:00",
            overall_result="PASS",
            pm_stage=StageExecution(
                stage_name="pm",
                result=StageResult.SUCCESS,
                start_time="2026-03-08T00:00:00+00:00",
                end_time="2026-03-08T00:01:00+00:00",
                duration_ms=60000,
            ),
            architect_stage=StageExecution(
                stage_name="architect",
                result=StageResult.SUCCESS,
                start_time="2026-03-08T00:00:00+00:00",
                end_time="2026-03-08T00:00:30+00:00",
                duration_ms=30000,
            ),
            director_stage=StageExecution(
                stage_name="director",
                result=StageResult.SUCCESS,
                start_time="2026-03-08T00:01:00+00:00",
                end_time="2026-03-08T00:09:00+00:00",
                duration_ms=480000,
            ),
            qa_stage=StageExecution(
                stage_name="qa",
                result=StageResult.SUCCESS,
                start_time="2026-03-08T00:09:00+00:00",
                end_time="2026-03-08T00:10:00+00:00",
                duration_ms=60000,
            ),
            observability_data={
                "tool_executions": [
                    {
                        "tool_name": "shell_command",
                        "error_message": "unauthorized by policy",
                        "arguments": {"command": "git reset --hard"},
                    }
                ],
                "error_events": [
                    {
                        "level": "error",
                        "message": "unauthorized command blocked",
                    }
                ],
            },
        )
    ]

    report = runner._generate_json_report()

    assert report["pm_quality_history"][0]["score"] is None
    assert report["pm_quality_history"][0]["source"] == "public_api_only"
    assert report["director_tool_audit"]["total_calls"] == 1
    assert report["director_tool_audit"]["unauthorized_blocked"] == 1
    assert report["director_tool_audit"]["dangerous_commands"] == 1
    assert report["chain_profile_effective"]["profile"] == "court_strict"
    assert report["path_contract_check"]["path_fallback_count"] == 0
    assert report["path_contract_check"]["pass"] is True
    assert "post_batch_code_audit" in report


@pytest.mark.asyncio
async def test_engine_finalize_round_uses_partial_trace_when_tracer_is_slow(tmp_path: Path) -> None:
    engine = StressEngine(workspace=tmp_path, backend_url="http://unit.test", trace_finalize_timeout=0.05)
    await engine.client.aclose()

    class SlowTracer:
        def __init__(self) -> None:
            self.current_round = RoundTrace(
                round_number=1,
                project_id="p1",
                project_name="demo",
                start_time="2026-03-08T00:00:00+00:00",
                factory_run_id="run-1",
            )

        async def complete_round(self, status: str) -> RoundTrace:
            await asyncio.sleep(0.2)
            self.current_round.status = status
            return self.current_round

    class StaticCollector:
        def to_dict(self) -> dict[str, object]:
            return {"ok": True}

    engine.tracer = SlowTracer()  # type: ignore[assignment]
    engine.collector = StaticCollector()  # type: ignore[assignment]
    result = RoundResult(
        round_number=1,
        project=PROJECT_POOL[0],
        start_time="2026-03-08T00:00:00+00:00",
        overall_result="PASS",
    )

    finalized = await engine._finalize_round(result)

    assert finalized.trace is engine.tracer.current_round
    assert finalized.observability_data == {"ok": True}


@pytest.mark.asyncio
async def test_probe_preserves_role_identity_on_exception() -> None:
    probe = RoleAvailabilityProbe(backend_url="http://unit.test")

    async def fake_probe_role(role: str) -> RoleProbeResult:
        if role == "pm":
            raise RuntimeError("boom")
        return RoleProbeResult(role=role, status=ProbeStatus.HEALTHY)

    probe._probe_role = fake_probe_role  # type: ignore[method-assign]
    try:
        report = await probe.probe_all()
    finally:
        await probe.client.aclose()

    pm_result = next(item for item in report.role_results if item.role == "pm")
    assert pm_result.status == ProbeStatus.UNHEALTHY
    assert "boom" in pm_result.error


@pytest.mark.asyncio
async def test_probe_generation_retry_recovers_from_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = RoleAvailabilityProbe(backend_url="http://unit.test")
    await probe.client.aclose()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v2/role/director/chat/status":
            return httpx.Response(
                200,
                json={
                    "ready": True,
                    "configured": True,
                    "role_config": {"provider_id": "mock-provider", "model": "mock-model"},
                    "llm_test_ready": True,
                    "provider_type": "mock",
                },
            )
        return httpx.Response(404)

    probe.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=probe.probe_timeout)
    attempts = {"count": 0}

    async def fake_probe_generation(role: str) -> tuple[bool, str, int]:
        del role
        attempts["count"] += 1
        if attempts["count"] == 1:
            return False, "Generation check error: ReadTimeout: ", 15000
        return True, "", 350

    monkeypatch.setattr(probe, "_probe_role_generation", fake_probe_generation)
    try:
        result = await probe._probe_role("director")
    finally:
        await probe.client.aclose()

    assert result.status == ProbeStatus.HEALTHY
    assert attempts["count"] == 2
    assert int(result.details.get("generation_attempts") or 0) == 2


def test_probe_generation_timeout_uses_probe_timeout_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POLARIS_STRESS_PROBE_GENERATION_TIMEOUT_SECONDS", raising=False)
    probe = RoleAvailabilityProbe(backend_url="http://unit.test", probe_timeout=30)
    assert probe._generation_probe_timeout_seconds() == 30.0


def test_probe_generation_timeout_honors_env_with_clamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLARIS_STRESS_PROBE_GENERATION_TIMEOUT_SECONDS", "120")
    probe = RoleAvailabilityProbe(backend_url="http://unit.test", probe_timeout=30)
    assert probe._generation_probe_timeout_seconds() == 60.0


@pytest.mark.asyncio
async def test_probe_generation_retry_stops_on_non_retryable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = RoleAvailabilityProbe(backend_url="http://unit.test")
    await probe.client.aclose()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v2/role/director/chat/status":
            return httpx.Response(
                200,
                json={
                    "ready": True,
                    "configured": True,
                    "role_config": {"provider_id": "mock-provider", "model": "mock-model"},
                    "llm_test_ready": True,
                    "provider_type": "mock",
                },
            )
        return httpx.Response(404)

    probe.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=probe.probe_timeout)
    attempts = {"count": 0}

    async def fake_probe_generation(role: str) -> tuple[bool, str, int]:
        del role
        attempts["count"] += 1
        return False, "Generation HTTP 400", 120

    monkeypatch.setattr(probe, "_probe_role_generation", fake_probe_generation)
    try:
        result = await probe._probe_role("director")
    finally:
        await probe.client.aclose()

    assert result.status == ProbeStatus.UNHEALTHY
    assert attempts["count"] == 1
    assert int(result.details.get("generation_attempts") or 0) == 1
    assert "Generation HTTP 400" in result.error


@pytest.mark.asyncio
async def test_runner_probe_only_prints_migration_and_exits(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = await agent_stress_runner_module.main(
        [
            "--probe-only",
            "--json",
        ]
    )

    assert exit_code == 2
    captured_err = capsys.readouterr().err
    assert "tests.agent_stress.probe" in captured_err
    assert "no longer supports" in captured_err


@pytest.mark.asyncio
async def test_probe_main_stops_immediately_when_backend_context_is_unresolved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import tests.agent_stress.probe as probe_module

    async def fake_ensure_backend_session(**kwargs):
        return ManagedBackendSession(
            context=BackendContext(
            backend_url="",
            token="",
            source="unresolved",
            desktop_info_path="C:/Users/test/.polaris/runtime/desktop-backend.json",
            )
        )

    monkeypatch.setattr(probe_module, "ensure_backend_session", fake_ensure_backend_session)

    async def fail_provider(self):
        raise AssertionError("provider probe should not run when backend context is unresolved")

    async def fail_probe_all(self):
        raise AssertionError("role probe should not run when backend context is unresolved")

    monkeypatch.setattr(probe_module.RoleAvailabilityProbe, "probe_provider_health", fail_provider)
    monkeypatch.setattr(probe_module.RoleAvailabilityProbe, "probe_all", fail_probe_all)

    output_path = tmp_path / "probe.json"
    exit_code = await probe_module.main(["--json", "--output", str(output_path), "--no-auto-bootstrap"])

    assert exit_code == 2
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["blocked"] is True
    assert payload["blocking_reason"] == "backend_context_unresolved"
    captured = capsys.readouterr().out
    assert "Backend Context: unresolved" in captured
    assert "官方 auto-bootstrap 后仍然无法解析" in captured or "official auto-bootstrap" in captured


@pytest.mark.asyncio
async def test_probe_main_auto_bootstraps_backend_context(monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import tests.agent_stress.probe as probe_module

    captured_kwargs: dict[str, object] = {}

    async def fake_ensure_backend_session(**kwargs):
        captured_kwargs.update(kwargs)
        return ManagedBackendSession(
            context=BackendContext(
                backend_url="http://127.0.0.1:51234",
                token="bootstrap-token",
                source="terminal-auto-bootstrap",
                desktop_info_path="C:/Users/test/.polaris/runtime/desktop-backend.json",
            ),
            auto_bootstrapped=True,
            startup_workspace="C:/Temp/tests-agent-stress-backend",
            ramdisk_root="X:/tests-agent-stress-runtime",
            desktop_info_path="C:/Users/test/.polaris/runtime/desktop-backend.json",
        )

    async def fake_provider_health(self):
        return {"providers": {}}

    async def fake_probe_all(self):
        return probe_module.ProbeReport(
            timestamp="2026-03-08T00:00:00",
            overall_status=ProbeStatus.HEALTHY,
            role_results=[
                RoleProbeResult(
                    role=role,
                    status=ProbeStatus.HEALTHY,
                    provider="mock",
                    model="gpt-test",
                    ready=True,
                    configured=True,
                )
                for role in probe_module.RoleAvailabilityProbe.ROLES
            ],
        )

    monkeypatch.setattr(probe_module, "ensure_backend_session", fake_ensure_backend_session)
    monkeypatch.setattr(probe_module.RoleAvailabilityProbe, "probe_provider_health", fake_provider_health)
    monkeypatch.setattr(probe_module.RoleAvailabilityProbe, "probe_all", fake_probe_all)

    exit_code = await probe_module.main(["--json"])

    assert exit_code == 0
    assert captured_kwargs["auto_bootstrap"] is True
    captured = capsys.readouterr().out
    assert "Backend Context: terminal-auto-bootstrap" in captured
    assert "Backend Bootstrap Workspace: C:/Temp/tests-agent-stress-backend" in captured


@pytest.mark.asyncio
async def test_runner_ensure_backend_session_updates_context(monkeypatch, tmp_path: Path) -> None:
    async def fake_ensure_backend_session(**kwargs):
        return ManagedBackendSession(
            context=BackendContext(
                backend_url="http://127.0.0.1:51234",
                token="bootstrap-token",
                source="terminal-auto-bootstrap",
                desktop_info_path="C:/Users/test/.polaris/runtime/desktop-backend.json",
            ),
            auto_bootstrapped=True,
            startup_workspace="C:/Temp/tests-agent-stress-backend",
            ramdisk_root="X:/tests-agent-stress-runtime",
            desktop_info_path="C:/Users/test/.polaris/runtime/desktop-backend.json",
        )

    monkeypatch.setattr(agent_stress_runner_module, "ensure_backend_session", fake_ensure_backend_session)
    runner = AgentStressRunner(workspace=DEFAULT_STRESS_WORKSPACE, output_dir=tmp_path / "reports")

    await runner._ensure_backend_session()

    assert runner.backend_url == "http://127.0.0.1:51234"
    assert runner.token == "bootstrap-token"
    assert runner.backend_context_source == "terminal-auto-bootstrap"
    assert runner.managed_backend_session is not None

    await runner.managed_backend_session.aclose()


@pytest.mark.asyncio
async def test_runner_default_delegates_to_observer_with_forced_window(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    async def fake_observe_runner(args, *, spawn_window: bool = False) -> int:
        captured["workspace"] = args.workspace
        captured["rounds"] = args.rounds
        captured["spawn_window"] = spawn_window
        return 0

    monkeypatch.setattr(agent_stress_observer_module, "observe_runner", fake_observe_runner)

    exit_code = await agent_stress_runner_module.main(
        [
            "--workspace",
            str(tmp_path / "stress-workspace"),
            "--rounds",
            "3",
        ]
    )

    assert exit_code == 0
    assert captured == {
        "workspace": str(tmp_path / "stress-workspace"),
        "rounds": 3,
        "spawn_window": True,
    }


@pytest.mark.asyncio
async def test_runner_help_hides_observe_and_probe_flags(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        await agent_stress_runner_module.main(["--help"])

    captured = capsys.readouterr().out
    assert "--observe" not in captured
    assert "--observe-window" not in captured
    assert "--probe-only" not in captured


def test_observer_state_tracks_steps_rounds_and_results() -> None:
    state = ObserverState(
        workspace="C:/Temp/stress",
        rounds=3,
        strategy="rotation",
        backend_url="http://127.0.0.1:51234",
        output_dir="C:/Temp/stress/stress_reports",
    )

    state.consume_line("## Step 3: 选择压测项目")
    state.consume_line("压测轮次 #2: 博客系统")
    state.consume_line("⚠️ Round #2 失败，记录失败分析...")
    state.consume_line("[Result] Round #2: FAIL")
    state.consume_line("✅ 探针完成")
    state.attach_exit_code(2)

    assert state.current_step == "3: 选择压测项目"
    assert state.current_round == 2
    assert state.failed_rounds == 1
    assert state.warnings == 1
    assert state.last_status == "failed with exit code 2"


def test_observer_state_render_has_valid_rich_styles() -> None:
    state = ObserverState(
        workspace="C:/Temp/stress",
        rounds=2,
        strategy="rotation",
        backend_url="http://127.0.0.1:51234",
        output_dir="C:/Temp/stress/stress_reports",
        projection_enabled=True,
        projection_transport="auto",
        projection_focus="all",
    )
    state.consume_line("## Step 1: probe")
    state.consume_line("[Result] Round #1: PASS")
    state.update_projection(
        connected=False,
        transport_used="none",
        error="",
        panels={
            "chain_status": [],
            "llm_reasoning": [],
            "dialogue_stream": [],
            "tool_activity": [],
            "realtime_events": [],
        },
    )

    console = Console(
        file=io.StringIO(),
        force_terminal=True,
        color_system="truecolor",
        width=120,
        record=True,
    )
    console.print(state.render())
    rendered = console.export_text()
    assert "Workspace" in rendered
    assert "Projection" in rendered


@pytest.mark.asyncio
async def test_observer_wrapper_preserves_runner_exit_code(monkeypatch, tmp_path: Path) -> None:
    async def fake_run_observer(args) -> int:
        assert args.workspace == str(tmp_path / "stress-workspace")
        return 2

    monkeypatch.setattr(agent_stress_observer_module, "_run_observer", fake_run_observer)
    args = argparse.Namespace(
        workspace=str(tmp_path / "stress-workspace"),
        rounds=2,
        strategy="rotation",
        backend_url="",
        output_dir=None,
        category=None,
        resume_from=0,
        probe_only=False,
        json=False,
        token="",
        spawn_window=False,
    )

    exit_code = await agent_stress_observer_module.observe_runner(args, spawn_window=False)

    assert exit_code == 2


@pytest.mark.asyncio
async def test_observer_spawn_window_delegates_to_new_console(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    async def fake_run_observer(args) -> int:
        raise AssertionError("_run_observer should not be called when spawn_window=True")

    def fake_spawn_new_console(args) -> int:  # noqa: ANN001
        captured["workspace"] = args.workspace
        return 7

    monkeypatch.setattr(agent_stress_observer_module, "_run_observer", fake_run_observer)
    monkeypatch.setattr(agent_stress_observer_module, "_spawn_new_console", fake_spawn_new_console)

    args = argparse.Namespace(
        workspace=str(tmp_path / "stress-workspace"),
        rounds=1,
        strategy="rotation",
        backend_url="",
        output_dir=None,
        category=None,
        resume_from=0,
        probe_only=False,
        json=False,
        token="",
        spawn_window=True,
        no_auto_bootstrap=False,
        non_llm_timeout_seconds=120.0,
    )

    exit_code = await agent_stress_observer_module.observe_runner(args, spawn_window=True)

    assert exit_code == 7
    assert captured["workspace"] == str(tmp_path / "stress-workspace")


def test_observer_runner_command_does_not_recurse_into_observer_flags(tmp_path: Path) -> None:
    args = argparse.Namespace(
        workspace=str(tmp_path / "stress-workspace"),
        rounds=2,
        strategy="rotation",
        backend_url="http://127.0.0.1:51234",
        output_dir=str(tmp_path / "reports"),
        category="crud",
        resume_from=1,
        probe_only=False,
        json=False,
        token="secret-token",
        observe=True,
        observe_window=False,
        spawn_window=False,
        no_auto_bootstrap=False,
    )

    command = agent_stress_observer_module._build_runner_command(args)

    assert command[:5] == [sys.executable, "-u", "-B", "-m", "tests.agent_stress.runner"]
    assert "--observe" not in command
    assert "--observe-window" not in command


def test_observer_runner_command_preserves_no_auto_bootstrap_flag(tmp_path: Path) -> None:
    args = argparse.Namespace(
        workspace=str(tmp_path / "stress-workspace"),
        rounds=2,
        strategy="rotation",
        backend_url="",
        output_dir=str(tmp_path / "reports"),
        category=None,
        resume_from=0,
        probe_only=False,
        json=False,
        token="",
        observe=False,
        observe_window=False,
        spawn_window=False,
        no_auto_bootstrap=True,
    )

    command = agent_stress_observer_module._build_runner_command(args)

    assert "--no-auto-bootstrap" in command


def test_observer_runner_command_forwards_execution_options(tmp_path: Path) -> None:
    args = argparse.Namespace(
        workspace=str(tmp_path / "stress-workspace"),
        rounds=20,
        strategy="rotation",
        backend_url="http://127.0.0.1:51234",
        output_dir=str(tmp_path / "reports"),
        category="crud,realtime",
        resume_from=0,
        probe_only=False,
        json=False,
        token="secret-token",
        no_auto_bootstrap=True,
        non_llm_timeout_seconds=90.0,
        execution_mode="round_robin",
        attempts_per_project=5,
        workspace_mode="per_round",
        min_new_code_files=4,
        min_new_code_lines=120,
        disable_chain_evidence_gate=False,
        max_failed_projects=2,
        skip_architect_stage=False,
        run_chief_engineer_stage=False,
        require_architect_stage=False,
        require_chief_engineer_stage=False,
        chain_profile="compat",
        round_batch_limit=5,
        post_batch_audit=True,
        no_post_batch_audit=False,
        audit_sample_size=6,
        audit_seed=99,
        projection_enabled=True,
        no_projection=False,
        projection_transport="ws",
        projection_focus="llm",
    )

    command = agent_stress_observer_module._build_runner_command(args)

    assert "--execution-mode" in command and "round_robin" in command
    assert "--attempts-per-project" in command and "5" in command
    assert "--workspace-mode" in command and "per_round" in command
    assert "--min-new-code-files" in command and "4" in command
    assert "--min-new-code-lines" in command and "120" in command
    assert "--max-failed-projects" in command and "2" in command
    assert "--non-llm-timeout-seconds" in command and "90.0" in command
    assert "--chain-profile" in command and "compat" in command
    assert "--round-batch-limit" in command and "5" in command
    assert "--post-batch-audit" not in command
    assert "--audit-sample-size" in command and "6" in command
    assert "--audit-seed" in command and "99" in command
    assert "--projection-enabled" not in command
    assert "--projection-transport" in command and "ws" in command
    assert "--projection-focus" in command and "llm" in command
    assert "--observer-child" in command


def test_observer_window_command_forwards_projection_and_batch_options(tmp_path: Path) -> None:
    args = argparse.Namespace(
        workspace=str(tmp_path / "stress-workspace"),
        rounds=3,
        strategy="rotation",
        backend_url="http://127.0.0.1:51234",
        output_dir=None,
        category=None,
        resume_from=0,
        probe_only=False,
        json=False,
        token="secret-token",
        no_auto_bootstrap=False,
        non_llm_timeout_seconds=120.0,
        execution_mode="project_serial",
        attempts_per_project=3,
        workspace_mode="per_project",
        min_new_code_files=2,
        min_new_code_lines=80,
        max_failed_projects=0,
        disable_chain_evidence_gate=False,
        skip_architect_stage=False,
        run_chief_engineer_stage=False,
        require_architect_stage=False,
        require_chief_engineer_stage=False,
        chain_profile="compat",
        round_batch_limit=4,
        post_batch_audit=True,
        no_post_batch_audit=False,
        audit_sample_size=4,
        audit_seed=7,
        projection_enabled=True,
        no_projection=False,
        projection_transport="ws",
        projection_focus="llm",
    )

    command = agent_stress_observer_module._build_observer_command(args)

    assert "--chain-profile" in command and "compat" in command
    assert "--round-batch-limit" in command and "4" in command
    assert "--post-batch-audit" not in command
    assert "--audit-sample-size" in command and "4" in command
    assert "--audit-seed" in command and "7" in command
    assert "--projection-enabled" not in command
    assert "--projection-transport" in command and "ws" in command
    assert "--projection-focus" in command and "llm" in command


def test_observer_redacts_token_in_log_command() -> None:
    command = [
        sys.executable,
        "-m",
        "tests.agent_stress.runner",
        "--token",
        "secret-token",
        "--backend-url",
        "http://127.0.0.1:51234",
    ]

    redacted = agent_stress_observer_module._redact_command_for_log(command)

    assert "secret-token" not in redacted
    assert "--token ***" in redacted


@pytest.mark.skipif(os.name != "nt", reason="Windows stress-path policy redirects observer logs into C:/Temp")
def test_observer_uses_safe_policy_error_output_dir_for_invalid_workspace(tmp_path: Path) -> None:
    args = argparse.Namespace(
        workspace=str(tmp_path / "stress-workspace"),
        rounds=1,
        strategy="rotation",
        backend_url="",
        output_dir=None,
        category=None,
        resume_from=0,
        probe_only=False,
        json=False,
        token="",
        spawn_window=False,
    )

    output_dir = agent_stress_observer_module._resolve_observer_output_dir(args)

    assert str(output_dir).startswith(str(stress_workspace_root()))


# Task A4: Path resolution tests

def test_logical_path_docs_resolves(tmp_path: Path) -> None:
    """验证 docs/tasks/dispatch 逻辑别名解析到 .polaris 目录"""
    engine = StressEngine(workspace=tmp_path, backend_url="http://unit.test")

    # 创建 docs 目录（通过逻辑路径解析后应该在 .polaris 下）
    docs_path = tmp_path / ".polaris" / "docs" / "plan.md"
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    docs_path.write_text("# plan\n", encoding="utf-8")

    # 解析 docs/plan.md 应该找到该文件
    # 优先通过 logical_path 解析（映射到 workspace/persistent），如果不存在则回退到 .polaris
    resolved = engine._resolve_stage_artifact_path("run-123", "docs/plan.md")

    assert resolved is not None
    # 解析可能通过 logical_path 或 .polaris，取决于文件实际存在位置
    assert resolved["resolved_by"] in {".polaris", "logical_path"}
    # 无论哪种方式解析，都应该能找到文件
    assert Path(resolved["resolved_path"]).exists()


def test_absolute_path_outside_trusted_rejected(tmp_path: Path) -> None:
    """验证非受信绝对路径（如 /tmp/foo, C:/other/path）被拒绝"""
    engine = StressEngine(workspace=tmp_path, backend_url="http://unit.test")

    # 绝对路径不在受信根目录下，应该返回 None
    result_untrusted = engine._resolve_stage_artifact_path("run-123", "C:/other/path/file.txt")
    assert result_untrusted is None

    result_tmp = engine._resolve_stage_artifact_path("run-123", "/tmp/foo/bar.txt")
    assert result_tmp is None


def test_no_workspace_fallback(tmp_path: Path) -> None:
    """验证不再回退到 workspace/<rel> 路径"""
    engine = StressEngine(workspace=tmp_path, backend_url="http://unit.test")

    # 创建 workspace 下的文件（但不应该被解析到）
    workspace_file = tmp_path / "somefile.txt"
    workspace_file.parent.mkdir(parents=True, exist_ok=True)
    workspace_file.write_text("content", encoding="utf-8")

    # 解析 somefile.txt 不应该找到 workspace 下的文件
    resolved = engine._resolve_stage_artifact_path("run-123", "somefile.txt")

    # 应该返回 None，因为不再有 workspace 回退
    assert resolved is None


def test_resolve_stage_artifact_path_returns_metadata_dict(tmp_path: Path) -> None:
    """验证 _resolve_stage_artifact_path 返回元数据字典而非直接 Path"""
    engine = StressEngine(workspace=tmp_path, backend_url="http://unit.test")

    # 创建文件在 .polaris/factory/run-id 下
    factory_dir = tmp_path / ".polaris" / "factory" / "run-999"
    factory_dir.mkdir(parents=True, exist_ok=True)
    factory_file = factory_dir / "artifacts" / "test.json"
    factory_file.parent.mkdir(parents=True, exist_ok=True)
    factory_file.write_text('{"key": "value"}', encoding="utf-8")

    # 解析路径
    result = engine._resolve_stage_artifact_path("run-999", "artifacts/test.json")

    # 验证返回的是包含元数据的字典
    assert result is not None
    assert isinstance(result, dict)
    assert "path" in result
    assert "resolved_by" in result
    assert "resolved_path" in result
    assert isinstance(result["path"], Path)
    assert result["resolved_by"] in {".polaris_factory", ".polaris_artifacts"}


def test_chain_profile_court_strict_mode(tmp_path: Path) -> None:
    """验证 court_strict 模式下 chief_engineer 默认不参与"""
    # court_strict 模式（默认）
    engine_strict = StressEngine(
        workspace=tmp_path,
        backend_url="http://unit.test",
        chain_profile="court_strict",
        run_architect_stage=True,
        run_chief_engineer_stage=False,
    )
    roles_strict = engine_strict._expected_chain_roles()
    assert "chief_engineer" not in roles_strict
    assert roles_strict == ["architect", "pm", "director", "qa"]


def test_chain_profile_compat_mode(tmp_path: Path) -> None:
    """验证 compat 模式下允许 chief_engineer 参与"""
    # compat 模式
    engine_compat = StressEngine(
        workspace=tmp_path,
        backend_url="http://unit.test",
        chain_profile="compat",
        run_architect_stage=True,
        run_chief_engineer_stage=True,
    )
    roles_compat = engine_compat._expected_chain_roles()
    assert "chief_engineer" in roles_compat
    assert roles_compat == ["architect", "pm", "chief_engineer", "director", "qa"]


def test_chain_profile_default_is_court_strict(tmp_path: Path) -> None:
    """验证默认 chain_profile 为 court_strict"""
    engine = StressEngine(workspace=tmp_path, backend_url="http://unit.test")
    assert engine.chain_profile == "court_strict"
    roles = engine._expected_chain_roles()
    assert "chief_engineer" not in roles
