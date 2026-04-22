from __future__ import annotations

import argparse
from pathlib import Path

from polaris.delivery.cli import router as cli_router
from polaris.delivery.cli.super_mode import (
    SuperModeRouter,
    build_director_handoff_message,
    build_super_readonly_message,
)


def test_super_mode_router_routes_code_delivery_to_pm_then_director() -> None:
    decision = SuperModeRouter().decide(
        "请帮我完善 session orchestrator 相关代码",
        fallback_role="director",
    )
    assert decision.roles == ("pm", "director")
    assert decision.reason == "code_delivery"


def test_super_mode_router_routes_orchestrator_improve_request_to_pm_then_director() -> None:
    decision = SuperModeRouter().decide(
        "进一步完善 Session Orchestrator",
        fallback_role="director",
    )
    assert decision.roles == ("pm", "director")
    assert decision.reason == "code_delivery"


def test_super_mode_router_routes_architecture_request_to_architect() -> None:
    decision = SuperModeRouter().decide(
        "请给我一个 session orchestrator 的架构蓝图",
        fallback_role="director",
    )
    assert decision.roles == ("architect",)
    assert decision.reason == "architecture_design"


def test_super_mode_router_routes_broad_contextos_delivery_to_architect_then_director() -> None:
    decision = SuperModeRouter().decide(
        "进一步完善ContextOS",
        fallback_role="director",
    )
    assert decision.roles == ("architect", "director")
    assert decision.reason == "architect_code_delivery"


def test_super_mode_router_routes_review_request_to_chief_engineer() -> None:
    decision = SuperModeRouter().decide(
        "请分析这个问题的根因并做代码审查",
        fallback_role="director",
    )
    assert decision.roles == ("chief_engineer",)
    assert decision.reason == "technical_analysis"


def test_super_mode_router_falls_back_to_configured_role() -> None:
    decision = SuperModeRouter().decide(
        "hello there",
        fallback_role="pm",
    )
    assert decision.roles == ("pm",)
    assert decision.reason == "fallback"


def test_build_director_handoff_message_contains_original_request_and_pm_plan() -> None:
    handoff = build_director_handoff_message(
        original_request="进一步完善 Session Orchestrator 相关代码",
        pm_output="1. inspect target file\n2. implement fix\n3. run tests",
    )
    assert "[mode:materialize]" in handoff
    assert "[SUPER_MODE_HANDOFF]" in handoff
    assert "进一步完善 Session Orchestrator 相关代码" in handoff
    assert "inspect target file" in handoff
    assert "execution_role: director" in handoff


def test_build_super_readonly_message_forces_analyze_mode() -> None:
    message = build_super_readonly_message(
        role="pm",
        original_request="进一步完善 Session Orchestrator 相关代码",
    )
    assert "[mode:analyze]" in message
    assert "[SUPER_MODE_READONLY_STAGE]" in message
    assert "stage_role: pm" in message
    assert "进一步完善 Session Orchestrator 相关代码" in message


def test_route_console_passes_super_flag(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def _fake_run_role_console(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli_router.WorkspaceGuard, "ensure_workspace", lambda _path: tmp_path)
    monkeypatch.setattr("polaris.delivery.cli.terminal_console.run_role_console", _fake_run_role_console)

    args = argparse.Namespace(
        workspace=str(tmp_path),
        role="director",
        backend="auto",
        session_id="",
        session_title="",
        prompt_style="plain",
        omp_config="",
        json_render="raw",
        debug=False,
        dry_run=False,
        batch=False,
        super=True,
    )

    exit_code = cli_router._route_console(args)
    assert exit_code == 0
    assert captured["super_mode"] is True
