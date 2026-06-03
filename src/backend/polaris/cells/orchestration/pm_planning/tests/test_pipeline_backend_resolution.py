"""Regression tests for PM planning backend resolution."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from polaris.cells.orchestration.pm_planning import pipeline
from polaris.cells.orchestration.pm_planning.internal.pipeline_ports import CellPmInvokePort
from polaris.cells.orchestration.pm_planning.internal.task_quality_gate import (
    _CARD3D_PM_DOMAIN_SCOPE_PATHS,
    _CARD3D_PM_DOMAIN_TARGET_FILES,
    _CARD3D_PM_REQUIRED_DOMAINS,
)


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


def test_run_pm_planning_iteration_prefers_real_card3d_retry_over_autofix_bulk(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def _real_card3d_payload() -> dict[str, Any]:
        tasks: list[dict[str, Any]] = []
        previous_id = ""
        for index, domain in enumerate(_CARD3D_PM_REQUIRED_DOMAINS, start=1):
            task_id = f"PM-CARD3D-{domain.upper()}-{index:02d}"
            target_files = list(_CARD3D_PM_DOMAIN_TARGET_FILES.get(domain, ()))
            if not target_files:
                target_files = [_CARD3D_PM_DOMAIN_SCOPE_PATHS[domain]]
            acceptance = [
                "Run `npm run build` exits 0.",
                "Run `npm run test -- --watch=false` exits 0.",
                f"Target files are implemented for Card3D {domain}.",
            ]
            checklist = [
                "Read the declared target files and adjacent contracts.",
                f"Implement the Card3D {domain} behavior with non-placeholder logic.",
                "Run npm run build and npm run test -- --watch=false.",
            ]
            if domain == "tests":
                acceptance.append("All trivial arithmetic placeholder tests are removed or replaced.")
                checklist.append("Replace existing arithmetic placeholder tests with meaningful flow coverage.")
            tasks.append(
                {
                    "id": task_id,
                    "title": f"Implement Card3D {domain}",
                    "goal": f"Deliver the Card3D {domain} domain with executable TypeScript behavior.",
                    "description": f"Implement {domain} files for the multiplayer 3D card game.",
                    "metadata": {"domain": domain},
                    "target_files": target_files,
                    "scope_paths": [_CARD3D_PM_DOMAIN_SCOPE_PATHS[domain]],
                    "acceptance_criteria": acceptance,
                    "assigned_to": "director",
                    "phase": "implementation" if domain != "tests" else "verification",
                    "depends_on": [previous_id] if previous_id else [],
                    "execution_checklist": checklist,
                }
            )
            previous_id = task_id
        return {
            "schema_version": 2,
            "overall_goal": "Build a multiplayer online creative card game with Three.js 3D table and Node.js realtime backend.",
            "focus": "Card3D implementation-ready domain task contract",
            "tasks": tasks,
            "notes": "Real PM-authored Card3D decomposition.",
        }

    class _FakePmInvokePort:
        def build_prompt(self, *args: Any, **kwargs: Any) -> str:
            return "build a Card3D task contract"

        def invoke(self, state: Any, prompt: str, backend_kind: str, args: Any, usage_ctx: Any) -> str:
            del state, prompt, backend_kind, args, usage_ctx
            calls.append("invoke")
            if len(calls) == 1:
                return json.dumps(
                    {
                        "schema_version": 2,
                        "overall_goal": (
                            "Build a multiplayer online creative card game with Three.js 3D table, "
                            "Node.js realtime backend, matchmaking, rooms, cards, rules, and tests."
                        ),
                        "focus": "Generic fallback tasks",
                        "tasks": [
                            {
                                "id": "PM-0001-F1",
                                "title": "Requirements bootstrap",
                                "goal": "Prepare generated project requirements.",
                                "target_files": ["package.json"],
                                "scope_paths": ["."],
                                "acceptance_criteria": ["Generated project structure exists."],
                                "assigned_to": "Director",
                                "phase": "planning",
                                "depends_on": [],
                                "execution_checklist": ["Create scaffold"],
                            }
                        ],
                    }
                )
            return json.dumps(_real_card3d_payload())

        def extract_json(self, raw_output: str) -> dict[str, Any] | None:
            return json.loads(raw_output)

    monkeypatch.setenv("KERNELONE_PM_TASK_QUALITY_RETRIES", "1")
    monkeypatch.setattr(pipeline, "get_pm_invoke_port", lambda: _FakePmInvokePort())

    state = SimpleNamespace(events_full="", ollama_full="", timeout=0)
    args = SimpleNamespace(pm_backend="auto", _resolved_pm_backend_kind="codex", timeout=0)
    context: dict[str, Any] = {
        "requirements": "Build a multiplayer online creative Card3D game.",
        "plan_text": "Implement Card3D client, server, realtime sync, game rules, persistence, moderation, and tests.",
        "gap_report": "",
        "last_qa": "",
        "last_tasks": None,
        "director_result": None,
        "pm_state": {},
        "docs_stage": {},
        "run_id": "pm-card3d",
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

    task_ids = [str(task.get("id") or "") for task in payload.get("tasks", []) if isinstance(task, dict)]
    assert exit_code == 0
    assert len(calls) == 2
    assert len(task_ids) == len(_CARD3D_PM_REQUIRED_DOMAINS)
    assert all(task_id.startswith("PM-CARD3D-") for task_id in task_ids)
    assert not any(task_id.startswith("PM-AUTO-") for task_id in task_ids)
    assert payload["quality_gate"]["passed"] is True


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
