"""Regression tests for PM planning backend resolution."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from polaris.cells.orchestration.pm_planning import pipeline
from polaris.cells.orchestration.pm_planning.internal.pipeline_ports import CellPmInvokePort


def test_run_pm_planning_iteration_uses_resolved_backend(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    class _FakePmInvokePort:
        def build_prompt(self, *args: Any, **kwargs: Any) -> str:
            return "build a task contract"

        def invoke(self, state: Any, prompt: str, backend_kind: str, args: Any, usage_ctx: Any) -> str:
            del state, prompt, args, usage_ctx
            seen["backend"] = backend_kind
            return json.dumps(
                {
                    "schema_version": 2,
                    "overall_goal": "Implement task API",
                    "focus": "Task API delivery",
                    "tasks": [
                        {
                            "id": "T01",
                            "title": "Implement task API",
                            "goal": "Create task API endpoints with validation",
                            "description": "Add task routes and service-level validation.",
                            "phase": "implementation",
                            "assigned_to": "director",
                            "depends_on": [],
                            "scope_paths": ["src/server/app.ts", "src/services/task-service.ts"],
                            "execution_checklist": [
                                "Add task API route handlers",
                                "Wire task service validation",
                                "Run npm test",
                            ],
                            "acceptance_criteria": [
                                "`npm test` succeeds",
                                "Task creation returns status code 201",
                            ],
                        }
                    ],
                }
            )

        def extract_json(self, raw_output: str) -> dict[str, Any] | None:
            return json.loads(raw_output)

    monkeypatch.setattr(pipeline, "get_pm_invoke_port", lambda: _FakePmInvokePort())

    state = SimpleNamespace(events_full="", ollama_full="", timeout=0)
    args = SimpleNamespace(
        pm_backend="auto",
        _resolved_pm_backend_kind="codex",
        timeout=0,
    )
    context: dict[str, Any] = {
        "requirements": "Implement a task API.",
        "plan_text": "Build and test task API.",
        "gap_report": "",
        "last_qa": "",
        "last_tasks": None,
        "director_result": None,
        "pm_state": {},
        "docs_stage": {},
        "run_id": "pm-00001",
        "start_timestamp": "2026-05-30 00:00:00",
        "run_events": str(tmp_path / "runtime.events.jsonl"),
        "dialogue_full": str(tmp_path / "dialogue.jsonl"),
        "pm_last_full": str(tmp_path / "pm-last.md"),
        "pm_llm_events_full": str(tmp_path / "pm.llm.events.jsonl"),
        "pm_state_full": str(tmp_path / "pm.state.json"),
    }

    exit_code, payload = pipeline.run_pm_planning_iteration(
        args=args,
        workspace_full=str(tmp_path),
        iteration=1,
        state=state,
        context=context,
    )

    assert exit_code == 0
    assert seen["backend"] == "codex"
    assert len(payload["tasks"]) == 1


def test_cell_pm_invoke_port_builds_codex_env_without_di(monkeypatch) -> None:
    class _FailingAdapter:
        def load_provider_config(self, *, workspace: str, provider_id: str) -> dict[str, Any]:
            raise RuntimeError("DI settings unavailable")

    monkeypatch.setattr(
        "polaris.kernelone.llm.runtime_config.get_role_model",
        lambda role: ("codex_cli", "gpt-5.3-codex"),
    )
    monkeypatch.setattr(
        "polaris.infrastructure.llm.provider_runtime_adapter.AppLLMRuntimeAdapter",
        lambda: _FailingAdapter(),
    )
    monkeypatch.setattr(
        "polaris.kernelone.llm.config_store.load_llm_config",
        lambda workspace, cache_root, settings=None: {
            "providers": {
                "codex_cli": {
                    "type": "codex_cli",
                    "codex_exec": {
                        "sandbox": "workspace-write",
                        "skip_git_repo_check": True,
                        "color": "never",
                    },
                }
            }
        },
    )

    env = CellPmInvokePort._build_codex_env_from_role_config(
        SimpleNamespace(workspace_full="C:/workspace", cache_root_full="C:/runtime")
    )

    assert env["KERNELONE_CODEX_MODEL"] == "gpt-5.3-codex"
    assert env["KERNELONE_CODEX_SANDBOX"] == "workspace-write"
    assert env["KERNELONE_CODEX_SKIP_GIT_CHECK"] == "1"
    assert env["KERNELONE_CODEX_COLOR"] == "never"
