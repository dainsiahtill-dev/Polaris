"""Characterization tests for the workspace-quality repair loop (kept whole: intra-class test-order coupling)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal import factory_workspace_quality_impl as workspace_quality_impl
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    OrchestrationStageExecutor,
)
from polaris.cells.factory.pipeline.internal.factory_workspace_quality_impl import (
    _workspace_quality_authoritative_owner_paths,
    _workspace_quality_causal_repair_target_files,
    _workspace_quality_llm_claim_target_files,
    _workspace_quality_test_shortfall_owner_targets,
)
from polaris.cells.factory.pipeline.tests._characterization_helpers import (
    _executor,
    _with_task_runtime_authority,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    TaskRuntimeExecutionAttemptIdentityV1,
)


class TestRunWorkspaceQualityChecks:
    @pytest.fixture(autouse=True)
    def canonical_task_boundary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Start workspace-quality tests after the canonical task boundary."""

        monkeypatch.setattr(
            OrchestrationStageExecutor,
            "_canonical_factory_projection",
            lambda _executor, _run, _context: _with_task_runtime_authority(
                {
                    "source": "run_ledger",
                    "task_boundary": {
                        "latest_by_task": {
                            "TASK-1": {
                                "task_id": "TASK-1",
                                "status": "completed_verified",
                                "ok": True,
                            }
                        }
                    },
                }
            ),
        )

    @pytest.mark.asyncio
    async def test_typescript_repairs_require_canonical_director_execution_before_rerun(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "render.ts").write_text(
            "import { SimulationState, updateSimulation } from './simulation';\n"
            "type Snapshot = SimulationState;\n"
            "const current: Snapshot = updateSimulation({ speed: 1 });\n"
            "export { current };\n",
            encoding="utf-8",
        )
        (tmp_path / "src" / "simulation.ts").write_text(
            "export class GardenSimulation {\n"
            "  public start(): void {\n"
            "    window.setInterval(() => undefined, 1000);\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        (tmp_path / "tsconfig.json").write_text(
            json.dumps(
                {
                    "compilerOptions": {
                        "target": "ES2020",
                        "module": "ES2020",
                        "lib": ["ES2020"],
                    },
                    "include": ["src/**/*.ts"],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        run = FactoryRun(
            id="factory-quality-repair",
            config=FactoryConfig(name="quality-repair"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        calls: list[list[str]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            calls.append(command)
            repaired_source = (tmp_path / "src" / "simulation.ts").read_text(encoding="utf-8")
            repaired_tsconfig = json.loads((tmp_path / "tsconfig.json").read_text(encoding="utf-8"))
            repaired = (
                "export type SimulationState = any;" in repaired_source
                and "export function updateSimulation(..._args: unknown[]): any" in repaired_source
                and "DOM" in repaired_tsconfig["compilerOptions"]["lib"]
            )
            if repaired:
                return {
                    "command": command,
                    "exit_code": 0,
                    "passed": True,
                    "stdout_tail": "build passed",
                    "stderr_tail": "",
                    "error": "",
                }
            return {
                "command": command,
                "exit_code": 2,
                "passed": False,
                "stdout_tail": (
                    "src/render.ts(1,10): error TS2305: Module '\"./simulation\"' has no exported member "
                    "'SimulationState'.\n"
                    "src/render.ts(1,27): error TS2305: Module '\"./simulation\"' has no exported member "
                    "'updateSimulation'.\n"
                    "src/simulation.ts(3,5): error TS2304: Cannot find name 'window'. "
                    "Do you need to change your target library? Try changing the 'lib' compiler option to include "
                    "'dom'."
                ),
                "stderr_tail": "",
                "error": "",
            }

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "run", "build"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)

        passed, artifact = await executor._run_workspace_quality_checks(run, {})

        assert passed is False
        assert calls == [["npm", "run", "build"]]
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["passed"] is False
        assert [item["phase"] for item in payload["commands"]] == ["check"]
        assert payload["repair"]["write_tool_evidence"] is False
        assert payload["repair"]["tool_results"] == 0
        assert "export type SimulationState = any;" not in (tmp_path / "src" / "simulation.ts").read_text(
            encoding="utf-8"
        )

    @pytest.mark.asyncio
    async def test_repair_summary_success_requires_rerun_to_pass(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-repair-still-failing",
            config=FactoryConfig(name="quality-repair-still-failing"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        calls: list[list[str]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            calls.append(command)
            return {
                "command": command,
                "exit_code": 2,
                "passed": False,
                "stdout_tail": "src/index.ts(1,10): error TS2305: missing export",
                "stderr_tail": "",
                "error": "",
            }

        async def fake_apply_workspace_quality_deterministic_repairs(
            *,
            run: FactoryRun,
            artifact_quality_errors: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            assert run.id == "factory-quality-repair-still-failing"
            assert repair_attempt == 1
            assert artifact_quality_errors
            return (
                [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {
                            "source_tool": "deterministic_typescript_missing_export_repair",
                            "file": "src/index.ts",
                            "operation": "modify",
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "source_tools": ["deterministic_typescript_missing_export_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": True,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "run", "build"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_deterministic_repairs",
            fake_apply_workspace_quality_deterministic_repairs,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 1},
        )

        assert passed is False
        assert calls == [["npm", "run", "build"], ["npm", "run", "build"]]
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["passed"] is False
        assert payload["repair"]["attempted"] is True
        assert payload["repair"]["success"] is False
        assert payload["repair"]["revalidated"] is True
        assert payload["repair"]["residual_error_count"] == 1
        assert "TS2305" in payload["repair"]["residual_errors"][0]

    @pytest.mark.asyncio
    async def test_workspace_quality_delivery_depth_contract_enters_repair_loop(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        (tmp_path / ".polaris").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".polaris" / "catalog_contract.json").write_text(
            json.dumps(
                {
                    "project_id": "depth-contract",
                    "level": 2,
                    "level_contract": {
                        "schema_version": "factory-bench.level_contract.v1",
                        "level": 2,
                        "minimums": {
                            "min_prod_files": 1,
                            "min_prod_lines": 3,
                            "min_behavior_symbols": 1,
                            "min_branch_count": 0,
                            "min_test_files": 0,
                            "min_test_assertions": 0,
                        },
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (tmp_path / "src").mkdir()
        source_path = tmp_path / "src" / "index.ts"
        source_path.write_text("export function run() { return 1; }\n", encoding="utf-8")
        run = FactoryRun(
            id="factory-quality-depth-contract",
            config=FactoryConfig(name="quality-depth-contract"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        calls: list[list[str]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            calls.append(command)
            return {
                "command": command,
                "exit_code": 0,
                "passed": True,
                "stdout_tail": "test passed",
                "stderr_tail": "",
                "error": "",
            }

        async def fake_apply_workspace_quality_deterministic_repairs(
            *,
            run: FactoryRun,
            artifact_quality_errors: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            assert run.id == "factory-quality-depth-contract"
            assert repair_attempt == 1
            assert any("delivery_depth_contract_failed" in item for item in artifact_quality_errors)
            return (
                [],
                {
                    "attempted": True,
                    "success": False,
                    "source_tools": [],
                    "tool_results": 0,
                    "write_tool_evidence": False,
                },
            )

        async def fake_apply_workspace_quality_llm_repairs(
            *,
            run: FactoryRun,
            context: dict[str, Any],
            artifact_quality_errors: list[str],
            repair_attempt: int,
            owner_target_files: list[str] | None = None,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            del context
            assert run.id == "factory-quality-depth-contract"
            assert repair_attempt == 1
            assert any("production_source_lines=1 < 3" in item for item in artifact_quality_errors)
            source_path.write_text(
                "\n".join(
                    [
                        "export function run() {",
                        "  const value = 1;",
                        "  return value;",
                        "}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            return (
                [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {
                            "source_tool": "director_llm_workspace_quality_repair",
                            "file": "src/index.ts",
                            "operation": "modify",
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "source_tools": ["director_llm_workspace_quality_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": True,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "test"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_deterministic_repairs",
            fake_apply_workspace_quality_deterministic_repairs,
        )
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_llm_repairs",
            fake_apply_workspace_quality_llm_repairs,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 1},
        )

        assert passed is True
        assert calls == [["npm", "test"], ["npm", "test"]]
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["passed"] is True
        command_phases = [(item["command"], item["phase"], item["passed"]) for item in payload["commands"]]
        assert (["delivery_depth_contract"], "check", False) in command_phases
        assert (["delivery_depth_contract"], "check_after_repair", True) in command_phases
        effective_command_phases = [
            (item["command"], item["phase"], item["passed"]) for item in payload["effective_commands"]
        ]
        assert (["delivery_depth_contract"], "check", False) not in effective_command_phases
        assert (["delivery_depth_contract"], "check_after_repair", True) in effective_command_phases
        assert payload["repair"]["attempted"] is True
        assert payload["repair"]["success"] is True

    @pytest.mark.asyncio
    async def test_workspace_quality_escalates_to_director_llm_repair_after_deterministic_noop(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-llm-repair",
            config=FactoryConfig(name="quality-llm-repair"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        state = {"repaired": False}
        calls: list[list[str]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            calls.append(command)
            if state["repaired"]:
                return {
                    "command": command,
                    "exit_code": 0,
                    "passed": True,
                    "stdout_tail": "build passed",
                    "stderr_tail": "",
                    "error": "",
                }
            return {
                "command": command,
                "exit_code": 1,
                "passed": False,
                "stdout_tail": (
                    "FAIL tests/index.test.ts > updateFirefly > should bounce\n"
                    "AssertionError: expected 3 to be less than 0\n"
                    " ❯ tests/index.test.ts:80:26"
                ),
                "stderr_tail": "",
                "error": "",
            }

        async def fake_apply_workspace_quality_deterministic_repairs(
            *,
            run: FactoryRun,
            artifact_quality_errors: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            assert run.id == "factory-quality-llm-repair"
            assert repair_attempt == 1
            assert artifact_quality_errors
            return (
                [],
                {
                    "attempted": False,
                    "success": False,
                    "source_tools": [],
                    "tool_results": 0,
                },
            )

        async def fake_apply_workspace_quality_llm_repairs(
            *,
            run: FactoryRun,
            context: dict[str, Any],
            artifact_quality_errors: list[str],
            repair_attempt: int,
            owner_target_files: list[str] | None = None,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            assert run.id == "factory-quality-llm-repair"
            assert context["workspace_quality_repair_max_rounds"] == 1
            assert artifact_quality_errors
            assert repair_attempt == 1
            assert owner_target_files is None
            state["repaired"] = True
            return (
                [
                    {
                        "tool": "write_file",
                        "tool_name": "write_file",
                        "success": True,
                        "result": {
                            "source_tool": "director_materialization_quality_repair",
                            "file": "src/index.ts",
                            "operation": "modify",
                            "before_sha256": "a" * 64,
                            "after_sha256": "b" * 64,
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "repair_mode": "director_llm",
                    "source_tools": ["director_materialization_quality_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": True,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "test"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda run, context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_deterministic_repairs",
            fake_apply_workspace_quality_deterministic_repairs,
        )
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_llm_repairs",
            fake_apply_workspace_quality_llm_repairs,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 1},
        )

        assert passed is True
        assert calls == [["npm", "test"], ["npm", "test"]]
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["passed"] is True
        assert payload["repair"]["success"] is True
        assert payload["repair"]["source_tools"] == ["director_materialization_quality_repair"]
        assert payload["repair"]["rounds"][0]["source_tools"] == ["director_materialization_quality_repair"]

    @pytest.mark.asyncio
    async def test_workspace_quality_rebinds_deferred_repair_to_owning_director_task(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-owner-rebind",
            config=FactoryConfig(name="quality-owner-rebind"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-10T00:00:00+00:00",
        )
        state = {"repaired": False}
        owner_target_calls: list[list[str] | None] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            return {
                "command": command,
                "exit_code": 0 if state["repaired"] else 1,
                "passed": state["repaired"],
                "stdout_tail": (
                    "test passed"
                    if state["repaired"]
                    else "AssertionError: browser entrypoint is not referenced by declared HTML"
                ),
                "stderr_tail": "",
                "error": "",
            }

        async def fake_apply_workspace_quality_deterministic_repairs(
            *,
            run: FactoryRun,
            artifact_quality_errors: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            assert run.id == "factory-quality-owner-rebind"
            assert repair_attempt == 1
            assert artifact_quality_errors
            return [], {"attempted": False, "success": False, "source_tools": [], "tool_results": 0}

        async def fake_apply_workspace_quality_llm_repairs(
            *,
            run: FactoryRun,
            context: dict[str, Any],
            artifact_quality_errors: list[str],
            repair_attempt: int,
            interface_discrepancy_evidence: dict[str, Any] | None = None,
            owner_target_files: list[str] | None = None,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            del context, artifact_quality_errors, interface_discrepancy_evidence
            assert run.id == "factory-quality-owner-rebind"
            assert repair_attempt == 1
            owner_target_calls.append(owner_target_files)
            if owner_target_files is None:
                return [], {
                    "stage": "task_boundary_repair_targets_deferred",
                    "attempted": True,
                    "success": False,
                    "success_reason": "repair_targets_outside_current_task_target_files",
                    "source_tools": [],
                    "tool_results": 0,
                    "task_boundary_scope_filter": {
                        "reason": "quality_repair_targets_outside_current_task_target_files",
                        "out_of_scope_repair_target_files": ["index.html"],
                    },
                }
            assert owner_target_files == ["index.html"]
            state["repaired"] = True
            return (
                [{"success": True, "tool": "edit_file", "file": "index.html", "operation": "modify"}],
                {
                    "stage": "quality_repair",
                    "attempted": True,
                    "success": True,
                    "source_tools": ["director_materialization_quality_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": True,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "test"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda run, context: None)
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_deterministic_repairs",
            fake_apply_workspace_quality_deterministic_repairs,
        )
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_llm_repairs",
            fake_apply_workspace_quality_llm_repairs,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 1},
        )

        assert passed is True
        assert owner_target_calls == [None, ["index.html"]]
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        rebind = payload["repair"]["rounds"][0]["repair_summary"]["deferred_owner_rebind"]
        assert rebind["attempted"] is True
        assert rebind["target_files"] == ["index.html"]
        assert rebind["previous_repair"]["stage"] == "task_boundary_repair_targets_deferred"

    @pytest.mark.asyncio
    async def test_workspace_quality_grants_bounded_extra_round_for_residual_owner_handoff(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-residual-owner-handoff",
            config=FactoryConfig(name="quality-residual-owner-handoff"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-23T00:00:00+00:00",
        )
        state = {"phase": 0}
        owner_target_calls: list[list[str] | None] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            phase = state["phase"]
            errors = (
                "main.go:12: undefined: VelocityForTest"
                if phase == 0
                else "physics/gravity_test.go:22:8: scene.Add undefined"
                if phase == 1
                else ""
            )
            return {
                "command": command,
                "exit_code": 0 if phase == 2 else 1,
                "passed": phase == 2,
                "stdout_tail": errors,
                "stderr_tail": "",
                "error": "",
            }

        async def fake_apply_workspace_quality_deterministic_repairs(
            *,
            run: FactoryRun,
            artifact_quality_errors: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            assert run.id == "factory-quality-residual-owner-handoff"
            assert repair_attempt in {1, 2}
            assert artifact_quality_errors
            return [], {"attempted": False, "success": False, "source_tools": [], "tool_results": 0}

        async def fake_apply_workspace_quality_llm_repairs(
            *,
            run: FactoryRun,
            context: dict[str, Any],
            artifact_quality_errors: list[str],
            repair_attempt: int,
            interface_discrepancy_evidence: dict[str, Any] | None = None,
            owner_target_files: list[str] | None = None,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            del context, artifact_quality_errors, interface_discrepancy_evidence
            assert run.id == "factory-quality-residual-owner-handoff"
            owner_target_calls.append(owner_target_files)
            if repair_attempt == 1:
                assert owner_target_files is None
                state["phase"] = 1
                return (
                    [{"success": True, "tool": "edit_file", "file": "main.go", "operation": "modify"}],
                    {
                        "stage": "quality_repair",
                        "task_id": "TASK-2",
                        "repair_target_files": ["main.go"],
                        "source_tools": ["director_materialization_quality_repair"],
                        "tool_results": 1,
                        "write_tool_evidence": True,
                        "task_boundary_scope_filter": {
                            "deferred": True,
                            "owner_task_retry_handoff_requests": [
                                {
                                    "target_file": "physics/gravity_test.go",
                                    "owner_step_id": "TASK-3",
                                    "owner_found": True,
                                    "status": "owner_found",
                                    "recommended_route": "owner_task_retry",
                                }
                            ],
                        },
                    },
                )
            assert repair_attempt == 2
            assert owner_target_files == ["physics/gravity_test.go"]
            state["phase"] = 2
            return (
                [
                    {
                        "success": True,
                        "tool": "edit_file",
                        "file": "physics/gravity_test.go",
                        "operation": "modify",
                    }
                ],
                {
                    "stage": "quality_repair",
                    "task_id": "TASK-3",
                    "repair_target_files": ["physics/gravity_test.go"],
                    "source_tools": ["director_materialization_quality_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": True,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["go", "test", "./..."]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda run, context: None)
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_deterministic_repairs",
            fake_apply_workspace_quality_deterministic_repairs,
        )
        monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", fake_apply_workspace_quality_llm_repairs)

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 1},
        )

        assert passed is True
        assert owner_target_calls == [None, ["physics/gravity_test.go"]]
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        rounds = payload["repair"]["rounds"]
        assert len(rounds) == 2
        assert rounds[0]["verifier_effect"] == "equal_count_swap"
        assert rounds[0]["residual_owner_handoff_extra_round_granted"] is True
        assert rounds[0]["residual_owner_handoff_targets"] == ["physics/gravity_test.go"]
        assert rounds[1]["verifier_effect"] == "resolved"

    def test_workspace_quality_owner_score_ignores_project_wide_target_inventory(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        project_targets = ["src/main.ts", "src/engine/simulation.ts"]
        failed_non_owner = {
            "status": "failed",
            "metadata": {
                "factory_run_id": "factory-owner-score",
                "external_task_id": "TASK-1",
                "target_files": ["src/main.ts"],
                "scope_paths": ["src/main.ts"],
                "project_declared_target_files": project_targets,
            },
        }
        completed_owner = {
            "status": "completed",
            "metadata": {
                "factory_run_id": "factory-owner-score",
                "external_task_id": "TASK-2",
                "target_files": ["src/engine/simulation.ts"],
                "scope_paths": ["src/engine/simulation.ts"],
                "project_declared_target_files": project_targets,
            },
        }

        non_owner_score = executor._workspace_quality_repair_owner_score(
            failed_non_owner,
            run_id="factory-owner-score",
            normalized_targets={"src/engine/simulation.ts"},
        )
        owner_score = executor._workspace_quality_repair_owner_score(
            completed_owner,
            run_id="factory-owner-score",
            normalized_targets={"src/engine/simulation.ts"},
        )

        assert non_owner_score == (0, 1)
        assert owner_score == (2, 0)
        assert owner_score > non_owner_score

    def test_workspace_quality_owner_score_prefers_imported_source_over_test_path(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        run_id = "factory-source-owner-score"
        test_owner = {
            "status": "failed",
            "metadata": {
                "factory_run_id": run_id,
                "external_task_id": "TASK-test",
                "target_files": ["tests/product.test.js"],
            },
        }
        source_owner = {
            "status": "completed",
            "metadata": {
                "factory_run_id": run_id,
                "external_task_id": "TASK-source",
                "target_files": ["src/dream.js"],
            },
        }
        targets = {"tests/product.test.js", "src/dream.js"}

        assert executor._workspace_quality_repair_owner_score(
            source_owner, run_id=run_id, normalized_targets=targets
        ) > executor._workspace_quality_repair_owner_score(test_owner, run_id=run_id, normalized_targets=targets)

    def test_workspace_quality_llm_claim_prefers_causal_source_over_test_wrapper(self) -> None:
        assert _workspace_quality_llm_claim_target_files(
            owner_target_files=None,
            diagnostic_target_files=["tests/test_product.py", "src/dream_subway/__init__.py"],
            fallback_target_files=["src/main.py"],
        ) == ["src/dream_subway/__init__.py"]

    def test_workspace_quality_llm_claim_treats_go_test_suffix_as_test_wrapper(self) -> None:
        """Go package-local test diagnostics must not outrank source errors."""

        assert _workspace_quality_llm_claim_target_files(
            owner_target_files=None,
            diagnostic_target_files=["engine/engine_test.go", "main.go"],
            fallback_target_files=[],
        ) == ["main.go"]

    def test_workspace_quality_go_residual_claims_causal_implementation_owner(self, tmp_path: Path) -> None:
        """L3-22: Go test wrappers must not lease before causal sources are known."""

        (tmp_path / "engine").mkdir()
        (tmp_path / "models").mkdir()
        (tmp_path / "engine" / "engine_test.go").write_text("package engine\n", encoding="utf-8")
        (tmp_path / "engine" / "engine.go").write_text("package engine\ntype Engine struct{}\n", encoding="utf-8")
        (tmp_path / "models" / "model_test.go").write_text("package models\n", encoding="utf-8")
        (tmp_path / "models" / "model.go").write_text("package models\ntype Bubble struct{}\n", encoding="utf-8")
        (tmp_path / "main_test.go").write_text("package main\n", encoding="utf-8")
        (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
        executor = _executor(tmp_path)
        errors = [
            "Workspace validation command failed (go test ./...):\n"
            "--- FAIL: TestBubbleValidMethod (0.00s)\n"
            "models/model_test.go:32: expected ErrInvalidRadius\n"
            "engine/engine_test.go:98: eng.Floor undefined "
            "(type *engine.Engine has no field or method Floor)\n"
            "main_test.go:113: cannot convert totalTime/dt+0.5 (untyped float constant) to type int"
        ]

        targets = _workspace_quality_causal_repair_target_files(
            executor,
            artifact_quality_errors=errors,
        )
        claim_targets = _workspace_quality_llm_claim_target_files(
            owner_target_files=None,
            diagnostic_target_files=targets,
            fallback_target_files=[],
        )

        assert "models/model_test.go" in targets
        assert "engine/engine.go" in targets
        assert "models/model.go" in targets
        assert "main.go" in targets
        assert claim_targets == ["engine/engine.go"]

    def test_workspace_quality_go_behavior_failure_excludes_test_owner(self, tmp_path: Path) -> None:
        """L3-22: runnable assertions must lease implementation, not tests."""

        (tmp_path / "engine").mkdir()
        (tmp_path / "models").mkdir()
        (tmp_path / "engine" / "engine_test.go").write_text("package engine\n", encoding="utf-8")
        (tmp_path / "engine" / "engine.go").write_text("package engine\ntype Engine struct{}\n", encoding="utf-8")
        (tmp_path / "models" / "model_test.go").write_text("package models\n", encoding="utf-8")
        (tmp_path / "models" / "errors.go").write_text("package models\nvar ErrInvalidRadius error\n", encoding="utf-8")
        (tmp_path / "main_test.go").write_text("package main\n", encoding="utf-8")
        (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
        executor = _executor(tmp_path)
        errors = [
            "Workspace validation command failed (go test ./...):\n"
            "--- FAIL: TestStepClampsOnFloor (0.00s)\n"
            "    engine_test.go:112: bubble still moving downward\n"
            "--- FAIL: TestBubbleValidMethod (0.00s)\n"
            "    model_test.go:124: want ErrInvalidRadius\n"
        ]

        targets = _workspace_quality_causal_repair_target_files(
            executor,
            artifact_quality_errors=errors,
        )
        claim_targets = _workspace_quality_llm_claim_target_files(
            owner_target_files=["engine/engine_test.go", "models/model_test.go", "main_test.go"],
            diagnostic_target_files=targets,
            fallback_target_files=[],
        )

        assert targets
        assert all(not path.endswith("_test.go") for path in targets)
        assert claim_targets
        assert not claim_targets[0].endswith("_test.go")

    def test_workspace_quality_go_behavior_ranks_observed_package_sources(self, tmp_path: Path) -> None:
        """L3-22: assertion sites rank same-package production before CLI files."""

        for rel in (
            "cmd/sandboxd/main.go",
            "internal/bubbletea/note.go",
            "internal/physics/step.go",
            "internal/physics/world.go",
            "internal/physics/step_test.go",
            "internal/sandbox/sandbox.go",
            "internal/sandbox/seed.go",
            "internal/sandbox/sandbox_test.go",
        ):
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("package fixture\n", encoding="utf-8")
        executor = _executor(tmp_path)

        targets = _workspace_quality_causal_repair_target_files(
            executor,
            artifact_quality_errors=[
                "Workspace validation command failed (go test ./...):\n"
                "--- FAIL: TestApplyGravity (0.00s)\n"
                "    step_test.go:165: expected y to advance\n"
                "FAIL\nFAIL\texample/internal/physics\t0.005s\n"
                "--- FAIL: TestAttachNoteValid (0.00s)\n"
                "    sandbox_test.go:52: unknown seed scenario: quiet\n"
                "FAIL\nFAIL\texample/internal/sandbox\t0.005s\nFAIL"
            ],
        )

        assert targets[:4] == [
            "internal/physics/step.go",
            "internal/physics/world.go",
            "internal/sandbox/sandbox.go",
            "internal/sandbox/seed.go",
        ]

    def test_workspace_quality_owner_rebind_rotates_only_current_causal_candidates(self) -> None:
        """A current sandbox failure must not rotate through an unrelated CLI owner file."""

        ranked = workspace_quality_impl._workspace_quality_rank_owner_rebind_candidates(
            owner_candidates=[
                "internal/bubbletea/bubble.go",
                "cmd/sandboxd/main.go",
                "internal/sandbox/sandbox.go",
                "internal/sandbox/seed.go",
            ],
            diagnostic_targets=[
                "internal/physics/step.go",
                "internal/sandbox/sandbox.go",
                "internal/sandbox/seed.go",
            ],
        )

        assert ranked == [
            "internal/sandbox/sandbox.go",
            "internal/sandbox/seed.go",
        ]

    def test_workspace_quality_go_compile_failure_keeps_direct_test_owner(self, tmp_path: Path) -> None:
        """Compiler diagnostics in an authored test still belong to that test."""

        (tmp_path / "engine").mkdir()
        (tmp_path / "engine" / "engine_test.go").write_text("package engine\n", encoding="utf-8")
        (tmp_path / "engine" / "engine.go").write_text("package engine\ntype Engine struct{}\n", encoding="utf-8")
        executor = _executor(tmp_path)
        errors = [
            "Workspace validation command failed (go test ./...):\n"
            "--- FAIL: TestEngine\n"
            "engine/engine_test.go:12:4: eng.Floor undefined"
        ]

        targets = _workspace_quality_causal_repair_target_files(
            executor,
            artifact_quality_errors=errors,
        )

        assert "engine/engine_test.go" in targets

    def test_workspace_quality_go_assignment_mismatch_keeps_direct_test_owner(self, tmp_path: Path) -> None:
        """Go result-arity compilation errors must stay on the authored test owner."""

        (tmp_path / "internal" / "bubbletea").mkdir(parents=True)
        (tmp_path / "internal" / "bubbletea" / "note_test.go").write_text(
            "package bubbletea\n",
            encoding="utf-8",
        )
        (tmp_path / "internal" / "bubbletea" / "note.go").write_text(
            "package bubbletea\nfunc (b *Bubble) Assign(n Note) (*Note, error) { return nil, nil }\n",
            encoding="utf-8",
        )
        executor = _executor(tmp_path)

        targets = _workspace_quality_causal_repair_target_files(
            executor,
            artifact_quality_errors=[
                "internal/bubbletea/note_test.go:115:9: assignment mismatch: "
                "1 variable but b.Assign returns 2 values"
            ],
        )

        assert "internal/bubbletea/note_test.go" in targets

    def test_workspace_quality_owner_paths_prefer_completion_projection_over_shared_token(self) -> None:
        """Capability scope may be shared; CE owned_artifacts is unique ownership."""

        metadata = {
            "task_completion_projection": {
                "run_id": "factory-owner",
                "task_id": "TASK-2",
                "owned_artifacts": [
                    {"path": "engine/physics.go", "owner_task_id": "TASK-2"},
                ],
            },
            "control_plane_job_token": {
                "factory_run_id": "factory-owner",
                "allowed_write_paths": ["main.go", "engine/physics.go"],
            },
        }

        assert _workspace_quality_authoritative_owner_paths(metadata, run_id="factory-owner") == [
            "engine/physics.go"
        ]

    def test_workspace_quality_resolves_unique_package_local_go_test_path(self, tmp_path: Path) -> None:
        """Go emits engine_test.go while the project owner path includes engine/."""

        engine = tmp_path / "engine"
        engine.mkdir()
        (engine / "engine_test.go").write_text("package engine\n", encoding="utf-8")
        (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
        executor = _executor(tmp_path)

        targets = executor._workspace_quality_repair_diagnostic_target_files(
            [
                "--- FAIL: TestStepAppliesGravity (0.00s)\n"
                "    engine_test.go:61: bubble did not fall",
                "# musicbubble\n./main.go:55:15: cannot convert totalTime / step",
            ]
        )

        assert targets == ["engine/engine_test.go", "main.go"]

    def test_workspace_quality_llm_claim_rejects_stale_owner_when_current_cause_moved(self) -> None:
        """Prior TASK-1 scope cannot hide a new TASK-2 verifier failure."""

        assert _workspace_quality_llm_claim_target_files(
            owner_target_files=[
                "requirements.txt",
                "src/dream_subway/domain.py",
                "src/dream_subway/__init__.py",
            ],
            diagnostic_target_files=[
                "src/dream_subway/line_editor.py",
                "src/dream_subway/__init__.py",
                "tests/test_product.py",
            ],
            fallback_target_files=["src/main.py"],
        ) == ["src/dream_subway/line_editor.py"]

    def test_workspace_quality_pathless_test_shortfall_uses_frozen_ce_test_owner(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """L3-21 test counts must not fall back to a production task.

        The verifier residual has no file location, but the same-run CE
        JobToken already delegates both required test artifacts to TASK-3.
        """

        executor = _executor(tmp_path)
        blueprint_hash = "b" * 64
        canonical_task = {
            "id": "TASK-3",
            "target_files": ["tests/test_product.py", "README.md"],
        }
        blueprint = {
            "task_id": "TASK-3",
            "blueprint_id": "ce-task-3",
            "blueprint_hash": blueprint_hash,
            "status": "generated",
            "handoff_ready": True,
            "target_files": [
                "requirements.txt",
                "tests/test_product.py",
                "README.md",
                "tests/test_behavior.py",
            ],
            "job_token": {
                "factory_run_id": "factory-test-depth-owner",
                "token_id": "token-task-3",
                "blueprint_hash": blueprint_hash,
                "target_files": [
                    "requirements.txt",
                    "tests/test_product.py",
                    "README.md",
                    "tests/test_behavior.py",
                ],
                "allowed_write_paths": [
                    "requirements.txt",
                    "tests/test_product.py",
                    "README.md",
                    "tests/test_behavior.py",
                ],
            },
            "capability_token": {
                "factory_run_id": "factory-test-depth-owner",
                "token_id": "token-task-3",
                "blueprint_hash": blueprint_hash,
            },
        }
        monkeypatch.setattr(executor, "_load_pm_plan_tasks", lambda _path: [canonical_task])
        monkeypatch.setattr(
            executor,
            "_load_chief_engineer_review_payload",
            lambda **_kwargs: {
                "blueprints": [
                    {
                        "task_id": "TASK-3",
                        "status": "generated",
                        "handoff_ready": True,
                        "blueprint_id": "ce-task-3",
                        "blueprint_path": "runtime/blueprints/ce-task-3.json",
                    }
                ]
            },
        )
        monkeypatch.setattr(executor, "_read_json_artifact_payload", lambda _path: blueprint)

        assert _workspace_quality_test_shortfall_owner_targets(
            executor,
            run_id="factory-test-depth-owner",
            artifact_quality_errors=[
                "delivery_depth_contract_failed: test_source_files=1 < 2; "
                "test_assertion_count=1 < 10",
                "Ran 0 tests in 0.000s",
            ],
        ) == ["tests/test_product.py", "tests/test_behavior.py"]

    def test_workspace_quality_owner_uses_run_bound_job_token_topology(self) -> None:
        metadata = {
            "factory_run_id": "factory-python-owner",
            "external_task_id": "TASK-1",
            "target_files": ["requirements.txt"],
            "scope_paths": ["requirements.txt"],
            "control_plane_job_token": {
                "factory_run_id": "factory-python-owner",
                "allowed_write_paths": [
                    "requirements.txt",
                    "src/dream_subway/__init__.py",
                    "src/dream_subway/domain/station.py",
                ],
            },
        }

        assert _workspace_quality_authoritative_owner_paths(
            metadata,
            run_id="factory-python-owner",
        ) == [
            "requirements.txt",
            "src/dream_subway/__init__.py",
            "src/dream_subway/domain/station.py",
        ]
        assert (
            OrchestrationStageExecutor._workspace_quality_repair_owner_score(
                {"status": "completed", "metadata": metadata},
                run_id="factory-python-owner",
                normalized_targets={"tests/test_product.py", "src/dream_subway/__init__.py"},
            )[0]
            == 2
        )

    def test_workspace_quality_owner_includes_authoritative_materialized_effect(self) -> None:
        """A task may repair a file it physically created with a durable receipt."""

        metadata = {
            "factory_run_id": "factory-effect-owner",
            "external_task_id": "TASK-2",
            "target_files": ["src/package/__main__.py"],
            "adapter_result": {
                "batch_receipt": {
                    "raw_results": [
                        {
                            "status": "success",
                            "result": {
                                "file": "src/main.py",
                                "before_sha256": "file_absent",
                                "after_sha256": "a" * 64,
                            },
                            "effect_receipt": {
                                "authoritative": True,
                                "receipt_outcome": "succeeded",
                            },
                        },
                        {
                            "status": "success",
                            "result": {
                                "file": "requirements.txt",
                                "before_sha256": "b" * 64,
                                "after_sha256": "b" * 64,
                            },
                            "effect_receipt": {
                                "authoritative": True,
                                "receipt_outcome": "succeeded",
                            },
                        },
                        {
                            "status": "success",
                            "result": {
                                "file": "src/untrusted.py",
                                "before_sha256": "file_absent",
                                "after_sha256": "c" * 64,
                            },
                            "effect_receipt": {
                                "authoritative": False,
                                "receipt_outcome": "succeeded",
                            },
                        },
                    ]
                }
            },
        }

        assert _workspace_quality_authoritative_owner_paths(
            metadata,
            run_id="factory-effect-owner",
        ) == ["src/main.py"]
        assert (
            OrchestrationStageExecutor._workspace_quality_repair_owner_score(
                {"status": "failed", "metadata": metadata},
                run_id="factory-effect-owner",
                normalized_targets={"src/main.py"},
            )[0]
            == 2
        )

    def test_workspace_quality_repair_extracts_python_traceback_project_paths(
        self,
        tmp_path: Path,
    ) -> None:
        """Python traceback frames and ImportError suffixes retain both owners."""

        tests = tmp_path / "tests"
        package = tmp_path / "src" / "dream_subway"
        tests.mkdir(parents=True)
        package.mkdir(parents=True)
        test_path = tests / "test_product.py"
        package_init = package / "__init__.py"
        test_path.write_text("from dream_subway import DreamSubwayEditor\n", encoding="utf-8")
        package_init.write_text('"""Dream subway package."""\n', encoding="utf-8")
        executor = _executor(tmp_path)
        diagnostic = "\n".join(
            [
                '  File "/usr/lib/python3.12/unittest/loader.py", line 419, in _find_test_path',
                f'  File "{test_path}", line 3, in <module>',
                "    from dream_subway import DreamSubwayEditor",
                (f"ImportError: cannot import name 'DreamSubwayEditor' from 'dream_subway' ({package_init})"),
            ]
        )

        targets = executor._workspace_quality_repair_diagnostic_target_files([diagnostic])

        assert targets == ["src/dream_subway/__init__.py", "tests/test_product.py"]

    def test_workspace_quality_repair_ranks_deepest_python_traceback_frame_first(
        self,
        tmp_path: Path,
    ) -> None:
        """Exact L3-21 shape routes dataclass NameError to TASK-2 source."""

        package = tmp_path / "src" / "dream_subway"
        tests = tmp_path / "tests"
        package.mkdir(parents=True)
        tests.mkdir(parents=True)
        test_path = tests / "test_product.py"
        package_init = package / "__init__.py"
        failing_source = package / "line_editor.py"
        test_path.write_text("from dream_subway import SubwayEditor\n", encoding="utf-8")
        package_init.write_text("from .line_editor import SubwayEditor\n", encoding="utf-8")
        failing_source.write_text("@dataclass(frozen=True)\nclass SubwayEditor: ...\n", encoding="utf-8")
        executor = _executor(tmp_path)
        diagnostic = "\n".join(
            [
                f'  File "{test_path}", line 3, in <module>',
                f'  File "{package_init}", line 1, in <module>',
                f'  File "{failing_source}", line 50, in <module>',
                "NameError: name 'dataclass' is not defined",
            ]
        )

        targets = executor._workspace_quality_repair_diagnostic_target_files([diagnostic])
        claim_targets = _workspace_quality_llm_claim_target_files(
            owner_target_files=["src/dream_subway/domain.py", "src/dream_subway/__init__.py"],
            diagnostic_target_files=targets,
            fallback_target_files=["src/main.py"],
        )

        assert targets[:3] == [
            "src/dream_subway/line_editor.py",
            "src/dream_subway/__init__.py",
            "tests/test_product.py",
        ]
        assert claim_targets == ["src/dream_subway/line_editor.py"]

    def test_workspace_quality_claims_exact_owner_of_current_causal_source(
        self,
        tmp_path: Path,
    ) -> None:
        """L3-21 regression: stale TASK-1 scope must rebind to TASK-2."""

        from polaris.cells.runtime.task_runtime.public import TaskRuntimeService

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-python-causal-owner",
            config=FactoryConfig(name="python-causal-owner"),
            status=FactoryRunStatus.RECOVERING,
            created_at="2026-08-20T00:00:00+00:00",
        )
        runtime = TaskRuntimeService(str(tmp_path))
        runtime.ensure_task_row(
            external_task_id="TASK-1",
            subject="Domain owner",
            description="Own package domain and exports",
            metadata={
                "external_task_id": "TASK-1",
                "factory_run_id": run.id,
                "target_files": ["requirements.txt"],
                "control_plane_job_token": {
                    "factory_run_id": run.id,
                    "allowed_write_paths": [
                        "requirements.txt",
                        "src/dream_subway/domain.py",
                        "src/dream_subway/__init__.py",
                    ],
                },
            },
        )
        runtime.ensure_task_row(
            external_task_id="TASK-2",
            subject="Editor owner",
            description="Own line editor and CLI",
            metadata={
                "external_task_id": "TASK-2",
                "factory_run_id": run.id,
                "target_files": ["requirements.txt", "src/main.py"],
                "control_plane_job_token": {
                    "factory_run_id": run.id,
                    "allowed_write_paths": [
                        "requirements.txt",
                        "src/dream_subway/line_editor.py",
                        "src/dream_subway/memory.py",
                        "src/dream_subway/seed.py",
                        "src/dream_subway/__main__.py",
                    ],
                },
            },
        )
        claim_targets = _workspace_quality_llm_claim_target_files(
            owner_target_files=[
                "requirements.txt",
                "src/dream_subway/domain.py",
                "src/dream_subway/__init__.py",
            ],
            diagnostic_target_files=[
                "src/dream_subway/line_editor.py",
                "src/dream_subway/__init__.py",
                "tests/test_product.py",
            ],
            fallback_target_files=["src/main.py"],
        )

        external_id, _task_row_id, attempt, repair_task = executor._claim_workspace_quality_repair_attempt(
            run=run,
            repair_attempt=1,
            target_files=claim_targets,
        )

        assert external_id == "TASK-2"
        assert attempt.run_id == run.id
        assert "src/dream_subway/line_editor.py" in repair_task["target_files"]
        assert "src/dream_subway/domain.py" not in repair_task["target_files"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("repair_attempt", "expected_target"),
        [
            (1, "internal/sandbox/sandbox_test.go"),
            (2, "internal/bubbletea/note_test.go"),
            (3, "main_test.go"),
        ],
    )
    async def test_workspace_quality_deferred_rebind_claims_real_owner_and_filters_provider_targets(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        repair_attempt: int,
        expected_target: str,
    ) -> None:
        """L3-22: deferred test targets must re-lease TASK-3, not retry TASK-1.

        The first quality turn correctly deferred test files outside TASK-1.
        Factory previously passed those paths back only as a target hint; the
        generic causal selector then leased TASK-1 again and the adapter
        failed before any Provider call.  Exercise the real TaskRuntime claim
        and prove the final repair request is narrowed to TASK-3 authority.
        """

        from polaris.cells.runtime.task_runtime.public import TaskRuntimeService

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-go-deferred-owner",
            config=FactoryConfig(name="go-deferred-owner", stages=["quality_gate"]),
            status=FactoryRunStatus.RECOVERING,
            created_at="2026-08-25T00:00:00+00:00",
        )
        for rel in (
            "main.go",
            "internal/physics/step.go",
            "internal/physics/step_test.go",
            "internal/sandbox/sandbox_test.go",
            "internal/bubbletea/note_test.go",
            "main_test.go",
        ):
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("package main\n", encoding="utf-8")

        runtime = TaskRuntimeService(str(tmp_path))
        runtime.ensure_task_row(
            external_task_id="TASK-1",
            subject="Production owner",
            description="Own Go entrypoint and physics implementation",
            metadata={
                "external_task_id": "TASK-1",
                "factory_run_id": run.id,
                "target_files": ["main.go", "internal/physics/step.go"],
                "control_plane_job_token": {
                    "factory_run_id": run.id,
                    "allowed_write_paths": ["main.go", "internal/physics/step.go"],
                },
            },
        )
        runtime.ensure_task_row(
            external_task_id="TASK-3",
            subject="Test owner",
            description="Own Go verifier sources",
            metadata={
                "external_task_id": "TASK-3",
                "factory_run_id": run.id,
                "target_files": [
                    "internal/physics/step_test.go",
                    "internal/sandbox/sandbox_test.go",
                    "internal/bubbletea/note_test.go",
                    "main_test.go",
                ],
                "control_plane_job_token": {
                    "factory_run_id": run.id,
                    "allowed_write_paths": [
                        "internal/physics/step_test.go",
                        "internal/sandbox/sandbox_test.go",
                        "internal/bubbletea/note_test.go",
                        "main_test.go",
                    ],
                },
            },
        )
        captured: dict[str, Any] = {}

        async def fake_run_director_materialization_quality_repair(
            workspace: str,
            *,
            task: dict[str, Any],
            target_task_id: str,
            run_id: str,
            context: dict[str, Any],
            original_message: str,
            llm_call_timeout: float,
            artifact_quality_errors: list[str],
            changed_files: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            del workspace, run_id, original_message, llm_call_timeout, artifact_quality_errors, changed_files
            captured.update(
                {
                    "task": task,
                    "target_task_id": target_task_id,
                    "context": context,
                    "repair_attempt": repair_attempt,
                }
            )
            return [], {"attempted": True, "success": False, "tool_results": 0}

        monkeypatch.setattr(
            "polaris.cells.roles.adapters.public.service.run_director_materialization_quality_repair",
            fake_run_director_materialization_quality_repair,
        )
        monkeypatch.setattr(
            workspace_quality_impl,
            "_workspace_quality_causal_repair_target_files",
            lambda _executor, *, artifact_quality_errors: [
                "internal/physics/step_test.go",
                "internal/bubbletea/note_test.go",
                "internal/sandbox/sandbox_test.go",
                "main_test.go",
                "internal/physics/step.go",
                "internal/bubbletea/note.go",
                "internal/sandbox/sandbox.go",
                "main.go",
            ],
        )

        await executor._apply_workspace_quality_llm_repairs(
            run=run,
            context={
                "factory_workspace_quality_owner_rebind": {
                    "required": True,
                    "source": "task_boundary_scope_filter",
                }
            },
            artifact_quality_errors=[
                "step_test.go:137: Y = 0, want 1",
                "internal/sandbox/sandbox_test.go:208:30: undefined: bubbleType",
                "internal/bubbletea/note_test.go:30:16: assignment mismatch",
                "main_test.go:19:12: undefined: Run",
            ],
            repair_attempt=repair_attempt,
            owner_target_files=[
                "step_test.go",
                "internal/sandbox/sandbox_test.go",
                "internal/bubbletea/note_test.go",
                "main_test.go",
            ],
        )

        assert captured["target_task_id"] == "TASK-3"
        # Owner selection needs the complete candidate set, but one provider
        # turn must receive one exact failing target. Otherwise the model can
        # keep choosing the first file while sibling compiler failures never
        # receive an edit (live L3-22: main_test.go repeated for three rounds).
        assert captured["context"]["target_files"] == [
            expected_target,
        ]
        assert captured["context"]["director_quality_repair"]["repair_target_files"] == [
            expected_target,
        ]
        assert captured["context"]["director_quality_repair"]["write_only_single_target"] == {
            "target_file": expected_target
        }
        assert "step_test.go" not in captured["context"]["target_files"]

    def test_workspace_quality_owner_score_matches_cmake_lists_case_aliases(
        self,
        tmp_path: Path,
    ) -> None:
        """Leftover cmake include remint must claim the docs owner of cmakelists.txt.

        Live L2-20 remint-8 targeted official CMakeLists.txt while TASK-1-docs
        only listed lowercase cmakelists.txt, so claim failed
        workspace_quality_repair_canonical_owner_missing for 8 rounds.
        """

        executor = _executor(tmp_path)
        docs_owner = {
            "status": "completed",
            "metadata": {
                "factory_run_id": "factory-cmake-owner",
                "external_task_id": "TASK-1-docs",
                "target_files": ["cmakelists.txt", "readme.md"],
            },
        }
        score = executor._workspace_quality_repair_owner_score(
            docs_owner,
            run_id="factory-cmake-owner",
            normalized_targets={"CMakeLists.txt"},
        )
        assert score[0] > 0

    def test_workspace_quality_rehydrates_frozen_owner_for_qa_only_retry(
        self,
        tmp_path: Path,
    ) -> None:
        """Terminal drain removes live rows, but QA retry must reclaim the same PM task."""

        from polaris.cells.factory.pipeline.public.contracts import (
            FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY,
            FactoryTerminalTaskRuntimeProjectionV1,
        )
        from polaris.cells.runtime.task_runtime.public import TaskRuntimeService

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-frozen-quality-owner",
            config=FactoryConfig(name="frozen-quality-owner", stages=["director_dispatch", "quality_gate"]),
            status=FactoryRunStatus.RECOVERING,
            created_at="2026-08-13T00:00:00+00:00",
        )
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "goal": "Own the model exports",
                        "scope": "src/models",
                        "scope_paths": ["src/models/__init__.py", "src/models/mood.py"],
                        "target_files": ["src/models/__init__.py", "src/models/mood.py"],
                        "acceptance_criteria": ["model imports pass"],
                    },
                    {
                        "id": "TASK-2",
                        "goal": "Own the CLI",
                        "scope": "src/main.py",
                        "scope_paths": ["src/main.py"],
                        "target_files": ["src/main.py"],
                        "acceptance_criteria": ["CLI starts"],
                    },
                ]
            },
        )
        run.metadata[FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY] = FactoryTerminalTaskRuntimeProjectionV1(
            workspace=str(tmp_path),
            factory_run_id=run.id,
            captured_at="2026-08-13T00:05:00+00:00",
            projection={
                "schema_version": "task_runtime.observable_task_rows_authority.v1",
                "source": "task_runtime.execution_fact",
                "workspace": str(tmp_path),
                "requested_factory_run_id": run.id,
                "authoritative": True,
                "degraded": False,
                "row_count": 2,
                "total_row_count": 2,
                "rows": [
                    {
                        "task_id": "6",
                        "external_task_id": "TASK-1",
                        "factory_run_id": run.id,
                        "status": "failed",
                        "execution_state": "failed",
                        "source": "task_runtime.execution_fact",
                        "status_source": "task_runtime.execution_fact",
                        "fact_event_seq": 10,
                    },
                    {
                        "task_id": "7",
                        "external_task_id": "TASK-2",
                        "factory_run_id": run.id,
                        "status": "completed",
                        "execution_state": "completed",
                        "source": "task_runtime.execution_fact",
                        "status_source": "task_runtime.execution_fact",
                        "fact_event_seq": 11,
                    },
                ],
                "readiness": {"ready": True, "blocking_reasons": []},
            },
        ).to_dict()

        external_id, task_row_id, attempt, repair_task = executor._claim_workspace_quality_repair_attempt(
            run=run,
            repair_attempt=1,
            target_files=["src/models/__init__.py"],
        )

        assert external_id == "TASK-1"
        assert attempt.run_id == run.id
        assert repair_task["target_files"] == ["src/models/__init__.py", "src/models/mood.py"]
        restored = TaskRuntimeService(str(tmp_path)).get_task(task_row_id)
        assert restored is not None
        assert restored["metadata"]["external_task_id"] == "TASK-1"
        assert restored["metadata"]["factory_stage"] == "quality_gate"

    def test_workspace_quality_rehydrates_frozen_owner_from_same_run_ce_job_token(
        self,
        tmp_path: Path,
    ) -> None:
        """Coarse PM targets must not erase CE topology after terminal drain."""

        from polaris.cells.factory.pipeline.public.contracts import (
            FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY,
            FactoryTerminalTaskRuntimeProjectionV1,
        )

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-frozen-ce-owner",
            config=FactoryConfig(name="frozen-ce-owner", stages=["director_dispatch", "quality_gate"]),
            status=FactoryRunStatus.RECOVERING,
            created_at="2026-08-20T00:00:00+00:00",
        )
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {"id": "TASK-1", "goal": "Foundation", "target_files": ["requirements.txt"]},
                    {"id": "TASK-2", "goal": "Editor", "target_files": ["requirements.txt"]},
                ]
            },
        )
        blueprint_rows: list[dict[str, Any]] = []
        for task_id, suffix, target in (
            ("TASK-1", "one", "src/dream_subway/domain.py"),
            ("TASK-2", "two", "src/dream_subway/line_editor.py"),
        ):
            blueprint_id = f"ce_{task_id}_{suffix}"
            blueprint_path = f"runtime/blueprints/{blueprint_id}.json"
            blueprint_hash = ("1" if task_id == "TASK-1" else "2") * 64
            token = {
                "token_id": f"job-{suffix}",
                "run_id": run.id,
                "factory_run_id": run.id,
                "blueprint_hash": blueprint_hash,
                "target_files": ["requirements.txt", target],
                "allowed_write_paths": ["requirements.txt", target],
            }
            executor._write_json_artifact(
                blueprint_path,
                {
                    "schema_version": "chief_engineer.blueprint.v1",
                    "task_id": task_id,
                    "blueprint_id": blueprint_id,
                    "blueprint_hash": blueprint_hash,
                    "status": "generated",
                    "handoff_ready": True,
                    "target_files": ["requirements.txt", target],
                    "job_token": token,
                    "capability_token": token,
                },
            )
            blueprint_rows.append(
                {
                    "task_id": task_id,
                    "status": "generated",
                    "handoff_ready": True,
                    "blueprint_id": blueprint_id,
                    "blueprint_path": blueprint_path,
                }
            )
        executor._write_json_artifact(
            f"runtime/state/blueprints/{run.id}.review.json",
            {
                "schema_version": "factory.chief_engineer_review.v2",
                "factory_run_id": run.id,
                "blueprints": blueprint_rows,
            },
        )
        run.metadata[FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY] = FactoryTerminalTaskRuntimeProjectionV1(
            workspace=str(tmp_path),
            factory_run_id=run.id,
            captured_at="2026-08-20T00:05:00+00:00",
            projection={
                "schema_version": "task_runtime.observable_task_rows_authority.v1",
                "source": "task_runtime.execution_fact",
                "workspace": str(tmp_path),
                "requested_factory_run_id": run.id,
                "authoritative": True,
                "degraded": False,
                "row_count": 2,
                "total_row_count": 2,
                "rows": [
                    {
                        "task_id": "8",
                        "external_task_id": "TASK-1",
                        "workflow_run_id": run.id,
                        "factory_run_id": run.id,
                        "status": "failed",
                        "execution_state": "failed",
                        "source": "task_runtime.execution_fact",
                        "status_source": "task_runtime.execution_fact",
                        "fact_event_seq": 18,
                    },
                    {
                        "task_id": "9",
                        "external_task_id": "TASK-2",
                        "workflow_run_id": run.id,
                        "factory_run_id": run.id,
                        "status": "completed",
                        "execution_state": "completed",
                        "source": "task_runtime.execution_fact",
                        "status_source": "task_runtime.execution_fact",
                        "fact_event_seq": 19,
                    },
                ],
                "readiness": {"ready": True, "blocking_reasons": []},
            },
        ).to_dict()

        external_id, _task_row_id, attempt, repair_task = executor._claim_workspace_quality_repair_attempt(
            run=run,
            repair_attempt=1,
            target_files=["src/dream_subway/line_editor.py"],
        )

        assert external_id == "TASK-2"
        assert attempt.run_id == run.id
        assert repair_task["target_files"] == ["requirements.txt", "src/dream_subway/line_editor.py"]
        assert repair_task["metadata"]["control_plane_job_token"]["token_id"] == "job-two"
        authority = repair_task["metadata"]["workspace_quality_frozen_owner_authority"]
        assert authority["task_id"] == "TASK-2"
        assert authority["factory_run_id"] == run.id

    @pytest.mark.parametrize(
        ("handoff_ready", "token_run_id"),
        [(False, "factory-frozen-ce-owner-invalid"), (True, "another-factory-run")],
    )
    def test_workspace_quality_frozen_ce_owner_rejects_invalid_authority(
        self,
        tmp_path: Path,
        handoff_ready: bool,
        token_run_id: str,
    ) -> None:
        """Unready or cross-run CE evidence cannot mint a repair claim."""

        from polaris.cells.factory.pipeline.public.contracts import (
            FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY,
            FactoryTerminalTaskRuntimeProjectionV1,
        )

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-frozen-ce-owner-invalid",
            config=FactoryConfig(name="invalid-frozen-ce-owner", stages=["quality_gate"]),
            status=FactoryRunStatus.RECOVERING,
            created_at="2026-08-20T00:00:00+00:00",
        )
        executor._write_json_artifact(
            "tasks/plan.json",
            {"tasks": [{"id": "TASK-1", "goal": "Foundation", "target_files": ["requirements.txt"]}]},
        )
        blueprint_id = "ce_TASK-1_invalid"
        blueprint_path = f"runtime/blueprints/{blueprint_id}.json"
        blueprint_hash = "3" * 64
        token = {
            "token_id": "job-invalid",
            "run_id": token_run_id,
            "factory_run_id": token_run_id,
            "blueprint_hash": blueprint_hash,
            "target_files": ["requirements.txt", "src/dream_subway/domain.py"],
            "allowed_write_paths": ["requirements.txt", "src/dream_subway/domain.py"],
        }
        executor._write_json_artifact(
            blueprint_path,
            {
                "task_id": "TASK-1",
                "blueprint_id": blueprint_id,
                "blueprint_hash": blueprint_hash,
                "status": "generated",
                "handoff_ready": handoff_ready,
                "target_files": ["requirements.txt", "src/dream_subway/domain.py"],
                "job_token": token,
                "capability_token": token,
            },
        )
        executor._write_json_artifact(
            f"runtime/state/blueprints/{run.id}.review.json",
            {
                "factory_run_id": run.id,
                "blueprints": [
                    {
                        "task_id": "TASK-1",
                        "status": "generated",
                        "handoff_ready": handoff_ready,
                        "blueprint_id": blueprint_id,
                        "blueprint_path": blueprint_path,
                    }
                ],
            },
        )
        run.metadata[FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY] = FactoryTerminalTaskRuntimeProjectionV1(
            workspace=str(tmp_path),
            factory_run_id=run.id,
            captured_at="2026-08-20T00:05:00+00:00",
            projection={
                "schema_version": "task_runtime.observable_task_rows_authority.v1",
                "source": "task_runtime.execution_fact",
                "workspace": str(tmp_path),
                "requested_factory_run_id": run.id,
                "authoritative": True,
                "degraded": False,
                "row_count": 1,
                "total_row_count": 1,
                "rows": [
                    {
                        "task_id": "8",
                        "external_task_id": "TASK-1",
                        "workflow_run_id": run.id,
                        "factory_run_id": run.id,
                        "status": "failed",
                        "execution_state": "failed",
                        "source": "task_runtime.execution_fact",
                        "status_source": "task_runtime.execution_fact",
                        "fact_event_seq": 18,
                    }
                ],
                "readiness": {"ready": True, "blocking_reasons": []},
            },
        ).to_dict()

        with pytest.raises(RuntimeError, match="workspace_quality_repair_canonical_owner_missing"):
            executor._claim_workspace_quality_repair_attempt(
                run=run,
                repair_attempt=1,
                target_files=["src/dream_subway/domain.py"],
            )

    def test_workspace_quality_reopens_blocked_restart_fence_owner(
        self,
        tmp_path: Path,
    ) -> None:
        """Isolated backend restart fences must not permanently lock the owner."""

        from polaris.cells.runtime.task_runtime.internal.task_board import TaskStatus
        from polaris.cells.runtime.task_runtime.public import TaskRuntimeService

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-blocked-quality-owner",
            config=FactoryConfig(name="blocked-quality-owner", stages=["director_dispatch", "quality_gate"]),
            status=FactoryRunStatus.RECOVERING,
            created_at="2026-08-16T00:00:00+00:00",
        )
        runtime = TaskRuntimeService(str(tmp_path))
        created = runtime.ensure_task_row(
            external_task_id="TASK-1-source-modules",
            subject="Own the CLI entrypoint",
            description="src/main.cpp owner after restart fence",
            metadata={
                "external_task_id": "TASK-1-source-modules",
                "factory_run_id": run.id,
                "target_files": ["src/main.cpp"],
                "role": "director",
            },
        )
        task_id = runtime.normalize_task_id(created.get("id") if isinstance(created, dict) else created)
        assert task_id is not None
        runtime._board.update(
            task_id,
            status=TaskStatus.BLOCKED,
            metadata={
                "last_execution_error": "factory_restart_recovery_expired_child_session",
            },
            allow_dependency_status=True,
        )

        external_id, task_row_id, attempt, repair_task = executor._claim_workspace_quality_repair_attempt(
            run=run,
            repair_attempt=1,
            target_files=["src/main.cpp"],
        )

        assert external_id == "TASK-1-source-modules"
        assert attempt.run_id == run.id
        assert repair_task["target_files"] == ["src/main.cpp"]
        claimed = TaskRuntimeService(str(tmp_path)).get_task(task_row_id)
        assert claimed is not None
        assert str(claimed.get("status") or "").lower() in {"pending", "ready", "claimed", "in_progress"}
        assert claimed["metadata"]["workspace_quality_repair"] is True

    def test_workspace_quality_repair_effect_requires_post_repair_verifier_progress(self) -> None:
        classify = OrchestrationStageExecutor._workspace_quality_repair_effect

        assert (
            classify(
                before_signature=("ts7015:a",),
                after_signature=(),
                verifier_passed=True,
                write_tool_evidence=True,
            )
            == "resolved"
        )
        assert (
            classify(
                before_signature=("ts7015:a",),
                after_signature=("ts2551:b",),
                verifier_passed=False,
                write_tool_evidence=True,
            )
            == "equal_count_swap"
        )
        assert (
            classify(
                before_signature=("ts7015:a",),
                after_signature=("ts7015:a", "ts2339:b"),
                verifier_passed=False,
                write_tool_evidence=True,
            )
            == "regression"
        )
        assert (
            classify(
                before_signature=("old:a", "old:b"),
                after_signature=("new:hard",),
                verifier_passed=False,
                write_tool_evidence=True,
            )
            == "progress"
        )
        assert (
            classify(
                before_signature=("old:a", "old:b"),
                after_signature=("old:a",),
                verifier_passed=False,
                write_tool_evidence=True,
            )
            == "progress"
        )
        assert (
            classify(
                before_signature=("ts7015:a",),
                after_signature=("ts7015:a",),
                verifier_passed=False,
                write_tool_evidence=False,
            )
            == "no_op"
        )

    def test_workspace_quality_same_go_failures_ignore_runner_timing_jitter(self) -> None:
        """The same named tests stay stagnant when only Go runner noise changes."""

        before = OrchestrationStageExecutor._workspace_quality_diagnostic_signature(
            [
                "--- FAIL: TestRunEndToEndViaLibraryAPI (0.00s)\n"
                "    main_test.go:137: bubble still moving downward\n"
                "FAIL\nFAIL\tmusicbubble\t0.359s",
                "--- FAIL: TestStepClampsOnFloor (0.00s)\n"
                "    engine_test.go:112: bubble still moving downward\n"
                "FAIL\nFAIL\tmusicbubble/engine\t0.006s\n"
                "ok  \tmusicbubble/models\t(cached)",
            ]
        )
        after = OrchestrationStageExecutor._workspace_quality_diagnostic_signature(
            [
                "--- FAIL: TestRunEndToEndViaLibraryAPI (0.00s)\n"
                "    main_test.go:137: bubble still moving downward\n"
                "FAIL\nFAIL\tmusicbubble\t0.330s",
                "--- FAIL: TestStepClampsOnFloor (0.00s)\n"
                "    engine_test.go:112: bubble still moving downward\n"
                "FAIL\nFAIL\tmusicbubble/engine\t0.005s\n"
                "ok  \tmusicbubble/models\t(cached)",
            ]
        )

        assert before != after
        assert (
            OrchestrationStageExecutor._workspace_quality_repair_effect(
                before_signature=before,
                after_signature=after,
                verifier_passed=False,
                write_tool_evidence=True,
            )
            == "stagnant"
        )

    def test_workspace_quality_named_test_identity_does_not_hide_compile_forward_unmask(self) -> None:
        """A stable failing test cannot hide removal of a compiler barrier."""

        before = OrchestrationStageExecutor._workspace_quality_diagnostic_signature(
            [
                "--- FAIL: TestStepClampsOnFloor (0.00s)\n"
                "    engine_test.go:112: bubble still moving downward",
                "engine/engine.go:71:19: cannot convert gravity to float64",
            ]
        )
        after = OrchestrationStageExecutor._workspace_quality_diagnostic_signature(
            [
                "--- FAIL: TestStepClampsOnFloor (0.00s)\n"
                "    engine_test.go:112: bubble still moving downward"
            ]
        )

        assert (
            OrchestrationStageExecutor._workspace_quality_repair_effect(
                before_signature=before,
                after_signature=after,
                verifier_passed=False,
                write_tool_evidence=True,
            )
            == "forward_unmask"
        )

    def test_workspace_quality_go_assignment_mismatch_unmasks_runnable_test(self) -> None:
        """L3-22: clearing result arity compilation is verifier progress."""

        before = OrchestrationStageExecutor._workspace_quality_diagnostic_signature(
            [
                "--- FAIL: TestStepSettlesAtFloor (0.00s)\n"
                "    step_test.go:137: Y = 0, want 1",
                "internal/bubbletea/note_test.go:115:9: assignment mismatch: "
                "1 variable but b.Assign returns 2 values",
            ]
        )
        after = OrchestrationStageExecutor._workspace_quality_diagnostic_signature(
            [
                "--- FAIL: TestStepSettlesAtFloor (0.00s)\n"
                "    step_test.go:137: Y = 0, want 1",
                "--- FAIL: TestNotesForScale (0.00s)\n"
                "    note_test.go:159: unknown note name",
            ]
        )

        assert (
            OrchestrationStageExecutor._workspace_quality_repair_effect(
                before_signature=before,
                after_signature=after,
                verifier_passed=False,
                write_tool_evidence=True,
            )
            == "forward_unmask"
        )

    def test_workspace_quality_repair_effect_rejects_test_failure_fanout_compression(self) -> None:
        """One common exception cannot masquerade as fewer diagnostics.

        Live L3-21 changed ``4 failures`` into ``1 failure + 21 errors``.
        Deduped traceback signatures shrank, but unittest's own authoritative
        summary proved a severe regression.
        """

        classify = OrchestrationStageExecutor._workspace_quality_repair_effect
        command = ["python", "-m", "unittest", "discover", "-s", "tests"]

        assert (
            classify(
                before_signature=("failure:a", "failure:b", "failure:c", "failure:d"),
                after_signature=("nameerror: total is not defined",),
                verifier_passed=False,
                write_tool_evidence=True,
                before_results=(
                    {
                        "command": command,
                        "passed": False,
                        "stderr_tail": "Ran 34 tests\n\nFAILED (failures=4)",
                    },
                ),
                after_results=(
                    {
                        "command": command,
                        "passed": False,
                        "stderr_tail": "Ran 34 tests\n\nFAILED (failures=1, errors=21)",
                    },
                ),
            )
            == "regression"
        )

    def test_workspace_quality_repair_effect_accepts_real_test_failure_reduction(self) -> None:
        classify = OrchestrationStageExecutor._workspace_quality_repair_effect
        command = ["python", "-m", "unittest", "discover", "-s", "tests"]

        assert (
            classify(
                before_signature=("failure:a", "failure:b", "failure:c", "failure:d"),
                after_signature=("failure:a", "failure:b", "failure:c"),
                verifier_passed=False,
                write_tool_evidence=True,
                before_results=({"command": command, "passed": False, "stderr_tail": "FAILED (failures=4)"},),
                after_results=({"command": command, "passed": False, "stderr_tail": "FAILED (failures=3)"},),
            )
            == "progress"
        )

    def test_workspace_quality_repair_effect_rejects_test_discovery_collapse(self) -> None:
        """A syntactically valid edit cannot erase every discovered test.

        Live L3-21 changed ``Ran 34 tests`` into ``Ran 0 tests`` without a
        unittest FAILED summary.  The old parser returned ``None`` for the
        post-edit count and let diagnostic-cardinality heuristics call the
        destructive edit progress.
        """

        classify = OrchestrationStageExecutor._workspace_quality_repair_effect
        command = ["python", "-m", "unittest", "discover", "-s", "tests"]

        assert (
            classify(
                before_signature=("failure:a", "failure:b"),
                after_signature=("unittest ran zero tests",),
                verifier_passed=False,
                write_tool_evidence=True,
                before_results=(
                    {
                        "command": command,
                        "passed": False,
                        "stderr_tail": "Ran 34 tests\n\nFAILED (failures=2)",
                    },
                ),
                after_results=(
                    {
                        "command": command,
                        "passed": False,
                        "stderr_tail": "Ran 0 tests\n\nNO TESTS RAN",
                    },
                ),
            )
            == "regression"
        )

    def test_workspace_quality_repair_effect_forward_unmask_rules(self) -> None:
        """Sequentially revealed compiler diagnostics are forward progress.

        rustc/tsc surface errors phase by phase: fixing ``E0432`` unmasks
        ``E0277``; fixing that unmasks ``E0507`` — often at the same line.
        Live L1-05 (factory_d842dba2e017) rounds 1-2 both carried real
        ``edit_file`` mutations with changed fingerprints, yet each was
        classified ``equal_count_swap`` and the loop stopped after two rounds
        with compilation strictly advanced. Disjoint compiler error codes with
        write evidence must classify as ``forward_unmask``; same-code text
        churn must stay ``equal_count_swap``.
        """

        classify = OrchestrationStageExecutor._workspace_quality_repair_effect

        # Live round 1: E0432@lib.rs -> E0277@flavor_rules.rs (different phase,
        # different file). Real hash-changing edit; equal count is an artifact
        # of phase-gated compilation.
        assert (
            classify(
                before_signature=(
                    "error[e0432]: unresolved imports `engine::recipe_from_ingredients` --> src/lib.rs:23:50",
                ),
                after_signature=(
                    "error[e0277]: the trait bound `palettefixture: copy` is not satisfied --> src/engine/flavor_rules.rs:162:15",
                ),
                verifier_passed=False,
                write_tool_evidence=True,
            )
            == "forward_unmask"
        )
        # Live round 2: E0277 -> E0507 at the SAME line — borrow-check phase
        # revealed after the trait-bound phase cleared.
        assert (
            classify(
                before_signature=(
                    "error[e0277]: the trait bound `palettefixture: copy` is not satisfied --> src/engine/flavor_rules.rs:162:15",
                ),
                after_signature=(
                    "error[e0507]: cannot move out of a shared reference --> src/engine/flavor_rules.rs:162:15",
                ),
                verifier_passed=False,
                write_tool_evidence=True,
            )
            == "forward_unmask"
        )
        # Same-code churn (renamed one missing symbol to another) is the true
        # equal-count swap and must keep tripping the stagnation breaker.
        assert (
            classify(
                before_signature=("error[e0432]: unresolved import `a` --> src/lib.rs:5:5",),
                after_signature=("error[e0432]: unresolved import `b` --> src/lib.rs:5:5",),
                verifier_passed=False,
                write_tool_evidence=True,
            )
            == "equal_count_swap"
        )
        # tsc reports all type errors in one pass, so disjoint TS codes at the
        # same spot are churn, not phase unmasking (pinned by the TS7015 ->
        # TS2551 stagnation characterization).
        assert (
            classify(
                before_signature=("src/index.ts(1,1): error ts7015: first",),
                after_signature=("src/index.ts(1,1): error ts2551: swapped",),
                verifier_passed=False,
                write_tool_evidence=True,
            )
            == "equal_count_swap"
        )
        # Non-code diagnostics (plain assertion text) cannot prove a phase
        # advance; keep the conservative swap classification.
        assert (
            classify(
                before_signature=("assertion failed: palette rows",),
                after_signature=("assertion failed: swatch order",),
                verifier_passed=False,
                write_tool_evidence=True,
            )
            == "equal_count_swap"
        )
        # Live L1-06: g++ is one residual blob. Resolving MoonError unmasked
        # missing-member diagnostics; that is phase advancement, not a swap.
        assert (
            classify(
                before_signature=("src/engine/generator.hpp:56:27: error: ‘MoonError’ has not been declared\n",),
                after_signature=(
                    "src/engine/generator.cpp:159:22: error: ‘const struct moonpost::Moon’ "
                    "has no member named ‘last_error’\n"
                    "src/engine/generator.cpp:201:16: error: ‘struct moonpost::Stamp’ "
                    "has no member named ‘is_valid’\n",
                ),
                verifier_passed=False,
                write_tool_evidence=True,
            )
            == "forward_unmask"
        )
        # Same missing-member names with only wording churn stay a swap.
        assert (
            classify(
                before_signature=("src/x.cpp:1:1: error: ‘struct Moon’ has no member named ‘last_error’\n",),
                after_signature=("src/x.cpp:2:1: error: ‘struct Moon’ has no member named ‘last_error’\n",),
                verifier_passed=False,
                write_tool_evidence=True,
            )
            == "equal_count_swap"
        )
        # Live L2-13: overflow crash -> runnable go test assertions is
        # unmasking, even when the assertion count is larger than the
        # compact crash blobs. Treating that as regression stopped repair
        # after the owner file was actually edited.
        assert (
            classify(
                before_signature=(
                    "workspace validation command failed (go test ./...): fatal error: stack overflow\n"
                    "frames=timecapsulemuseum/engine.(*Service).exhibitionIDs",
                    "workspace validation command failed (go run .): fatal error: stack overflow",
                    "delivery_depth_contract_failed: production_source_files=5 < 6",
                ),
                after_signature=(
                    "--- FAIL: TestRun_StatsAndSnapshot (0.00s)\n"
                    '    main_test.go:515: snapshot: want capsule[ entries, got ""',
                    "--- FAIL: TestRun_ListExhibition (0.00s)\n    main_test.go:523: list: want exitOK, got 1",
                    "delivery_depth_contract_failed: production_source_files=5 < 6",
                ),
                verifier_passed=False,
                write_tool_evidence=True,
            )
            == "forward_unmask"
        )
        # Live L3-22: packages engine/models already exposed assertions while
        # root-package main_test.go still failed compilation. Fixing the root
        # compile barrier revealed runnable root tests; total diagnostic count
        # stayed equal, but verifier phase advanced.
        assert (
            classify(
                before_signature=(
                    "--- FAIL: TestStepClampsOnFloor (0.00s)\n"
                    "engine_test.go:112: bubble still moving downward",
                    "main_test.go:113:15: cannot convert totalTime / dt + 0.5 "
                    "(untyped float constant 30.5) to type int",
                ),
                after_signature=(
                    "--- FAIL: TestStepClampsOnFloor (0.00s)\n"
                    "engine_test.go:112: bubble still moving downward",
                    "--- FAIL: TestRunEndToEndViaLibraryAPI (0.00s)\n"
                    "main_test.go:148: bubble still moving downward",
                ),
                verifier_passed=False,
                write_tool_evidence=True,
            )
            == "forward_unmask"
        )
        # Compile-to-compile churn is not phase advancement.
        assert (
            classify(
                before_signature=("main.go:10:2: undefined: first",),
                after_signature=("main.go:11:2: undefined: second",),
                verifier_passed=False,
                write_tool_evidence=True,
            )
            == "equal_count_swap"
        )
        # Live L3-21: fixing a missing dataclass import moved unittest from a
        # module-level _FailedTest/NameError into 34 executed tests.  The newly
        # visible assertion failures increased the residual count, but the
        # import barrier was gone; that is forward-unmasking, not regression.
        assert (
            classify(
                before_signature=(
                    "ERROR: test_product (unittest.loader._FailedTest.test_product)\n"
                    "ImportError: Failed to import test module: test_product\n"
                    "NameError: name 'dataclass' is not defined",
                    "delivery_depth_contract_failed: test_source_files=1 < 2",
                ),
                after_signature=(
                    "FAIL: test_rule4_preview_journey_returns_serializable_report\n"
                    "AssertionError: 0.0 not greater than 0.0\n"
                    "Ran 34 tests in 0.184s",
                    "ERROR: test_lucid_loop_seed_is_closed\n"
                    "InvalidLineError: duplicate station\n"
                    "Ran 34 tests in 0.184s",
                    "delivery_depth_contract_failed: test_source_files=1 < 2",
                ),
                verifier_passed=False,
                write_tool_evidence=True,
            )
            == "forward_unmask"
        )
        # A new Python exception without proof that collection became a real
        # test run remains a regression.
        assert (
            classify(
                before_signature=(
                    "ImportError: Failed to import test module: test_product\n"
                    "NameError: name 'dataclass' is not defined",
                ),
                after_signature=("TypeError: main() takes 0 positional arguments", "ValueError: invalid seed"),
                verifier_passed=False,
                write_tool_evidence=True,
            )
            == "regression"
        )
        # A read-only turn never earns forward progress regardless of codes.
        assert (
            classify(
                before_signature=("error[e0432]: unresolved import `a` --> src/lib.rs:5:5",),
                after_signature=("error[e0277]: the trait bound `x: copy` is not satisfied --> src/lib.rs:9:9",),
                verifier_passed=False,
                write_tool_evidence=False,
            )
            == "no_op"
        )

    def test_workspace_quality_diagnostic_error_codes_extraction(self) -> None:
        extract = OrchestrationStageExecutor._workspace_quality_diagnostic_error_codes

        codes = extract(
            (
                "error[e0432]: unresolved imports --> src/lib.rs:23:50",
                "Error[E0507]: cannot move out of a shared reference --> src/x.rs:1:1",
                "error ts2551 in module.ts(12,5)",
                "assertion failed without any code",
            )
        )
        assert codes == {"e0432", "e0507", "ts2551"}

        assert extract(("plain text only",)) == set()
        assert extract(()) == set()

    @pytest.mark.asyncio
    async def test_workspace_quality_attempt_settles_success_only_after_verifier_passes(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-post-verifier-settlement",
            config=FactoryConfig(name="quality-post-verifier-settlement"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-14T00:00:00+00:00",
        )
        identity = TaskRuntimeExecutionAttemptIdentityV1(
            workspace=str(tmp_path),
            task_id=7,
            external_task_id="TASK-1",
            session_id="quality-repair-session",
            attempt=1,
            role_id="director",
            worker_id="director",
            run_id=run.id,
            lease_expires_at="2026-08-14T00:05:00+00:00",
        )
        timeline: list[str] = []
        command_calls = 0

        def run_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            nonlocal command_calls
            del command, timeout_seconds
            command_calls += 1
            timeline.append(f"verifier:{command_calls}")
            return {
                "command": ["go", "test", "./..."],
                "exit_code": 1 if command_calls == 1 else 0,
                "passed": command_calls > 1,
                "stdout_tail": "rules_test.go: failed" if command_calls == 1 else "ok",
                "stderr_tail": "",
                "error": "",
            }

        async def apply_repair(**_kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            timeline.append("mutation")
            return (
                [
                    {
                        "tool": "edit_file",
                        "success": True,
                        "result": {
                            "file": "engine/rules.go",
                            "operation": "modify",
                            "before_sha256": "a" * 64,
                            "after_sha256": "b" * 64,
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "write_tool_evidence": True,
                    "_pending_task_runtime_repair_attempt": {
                        "task_id": "TASK-1",
                        "task_row_id": "7",
                        "execution_attempt": identity,
                    },
                    "task_runtime_repair_attempt": {
                        "task_id": "TASK-1",
                        "session_id": identity.session_id,
                        "settled": False,
                        "outcome": "pending_revalidation",
                    },
                },
            )

        def settle(**kwargs: object) -> dict[str, object]:
            timeline.append(f"settle:{kwargs['stage_status']}")
            return {"success": True}

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda _context: [["go", "test", "./..."]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda _commands, _context: [])
        monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda _run, _context: None)
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda _context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", run_command)
        monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", apply_repair)
        monkeypatch.setattr(executor, "_settle_director_stage_materialization_attempt", settle)

        passed, _artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 1},
        )

        assert passed is True
        assert timeline == ["verifier:1", "mutation", "verifier:2", "settle:success"]

    @pytest.mark.asyncio
    async def test_workspace_quality_llm_fallback_preserves_current_claimed_test_owner(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A mixed verifier blob must not move a TASK-3 test repair to TASK-2.

        Live L3-21 emitted ``Ran 0 tests`` plus a later ``src/main.py`` import
        traceback.  The deterministic pass correctly claimed TASK-3, but the
        LLM fallback discarded that current claim and preferred the first
        non-test diagnostic path.  Three TASK-3 no-commit settlements then
        rotated the repair across TASK-2/TASK-1, where unrelated edits created
        fresh regressions.  Same-round fallback must consume the exact claimed
        owner's in-scope diagnostic path.
        """

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-preserve-test-owner",
            config=FactoryConfig(name="quality-preserve-test-owner"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-21T00:00:00+00:00",
        )
        state = {"repaired": False}
        owner_targets_seen: list[list[str] | None] = []

        def run_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            return {
                "command": command,
                "exit_code": 0 if state["repaired"] else 5,
                "passed": state["repaired"],
                "stdout_tail": (
                    "ok"
                    if state["repaired"]
                    else (
                        "tests/test_product.py: Ran 0 tests\n"
                        "src/main.py:16: ImportError: cannot import name 'main'"
                    )
                ),
                "stderr_tail": "",
                "error": "",
            }

        async def deterministic_no_commit(**_kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            return (
                [],
                {
                    "attempted": True,
                    "success": False,
                    "task_id": "TASK-3",
                    "write_tool_evidence": False,
                    "task_boundary_owner_evidence": {
                        "schema_version": "factory.workspace_quality_task_owner.v1",
                        "source": "task_runtime_execution_attempt",
                        "task_id": "TASK-3",
                        "owner_target_files": ["tests/test_product.py", "README.md"],
                        "diagnostic_target_files": ["tests/test_product.py", "src/main.py"],
                        "in_scope_diagnostic_target_files": ["tests/test_product.py"],
                        "out_of_scope_diagnostic_target_files": ["src/main.py"],
                        "director_local_repair_allowed": True,
                    },
                },
            )

        async def llm_repair(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            owner_targets = kwargs.get("owner_target_files")
            owner_targets_seen.append(list(owner_targets) if isinstance(owner_targets, list) else None)
            state["repaired"] = True
            return (
                [
                    {
                        "tool": "edit_file",
                        "success": True,
                        "result": {
                            "file": "tests/test_product.py",
                            "operation": "modify",
                            "before_sha256": "a" * 64,
                            "after_sha256": "b" * 64,
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "task_id": "TASK-3",
                    "repair_target_files": ["tests/test_product.py"],
                    "write_tool_evidence": True,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda _context: [["python", "-m", "unittest"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda _commands, _context: [])
        monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda _run, _context: None)
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda _context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", run_command)
        monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", deterministic_no_commit)
        monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", llm_repair)

        passed, _artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 1},
        )

        assert passed is True
        assert owner_targets_seen == [["tests/test_product.py"]]

    @pytest.mark.asyncio
    async def test_workspace_quality_no_mutation_retries_director_without_rerunning_verifier(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-no-mutation",
            config=FactoryConfig(name="quality-no-mutation"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-14T00:00:00+00:00",
        )
        command_calls = 0
        deterministic_calls = 0
        llm_calls = 0
        llm_contexts: list[dict[str, object]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            nonlocal command_calls
            del timeout_seconds
            command_calls += 1
            return {
                "command": command,
                "exit_code": 1,
                "passed": False,
                "stdout_tail": "main_test.go:85: assertion failed",
                "stderr_tail": "",
                "error": "",
            }

        async def fake_deterministic_repairs(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal deterministic_calls
            del kwargs
            deterministic_calls += 1
            return [], {"attempted": True, "success": False, "write_tool_evidence": False}

        async def fake_llm_repairs(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal llm_calls
            llm_calls += 1
            context = kwargs.get("context")
            assert isinstance(context, dict)
            llm_contexts.append(context)
            return (
                [
                    {
                        "tool": "edit_file",
                        "success": False,
                        "result": {
                            "error_code": "deo_physical_execution_failed",
                            "physical_error": "No replacements made",
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": False,
                    "source_tools": ["director_materialization_quality_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": False,
                    "error": (
                        "TransactionKernel execution failed: tool_dispatch_failed: decoded tool batch "
                        "produced only failed tool results; error_types=source_compile_regression; "
                        "failure_details=Edit rejected before commit: undefined: DefaultDT"
                    ),
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["go", "test", "./..."]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda run, context: None)
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", fake_deterministic_repairs)
        monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", fake_llm_repairs)

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 3},
        )

        assert passed is False
        assert command_calls == 1
        # The first unchanged signature may probe the materialization
        # callback schedule once.  After that probe returns no authoritative
        # commit, the second round must go directly to the same-owner LLM
        # repair instead of reopening/settling another deterministic attempt.
        assert deterministic_calls == 1
        assert llm_calls == 2
        first_quality = llm_contexts[0].get("director_quality_repair")
        assert not isinstance(first_quality, dict) or not first_quality.get("candidate_rejection_errors")
        second_quality = llm_contexts[1]["director_quality_repair"]
        assert isinstance(second_quality, dict)
        candidate_rejections = second_quality["candidate_rejection_errors"]
        assert isinstance(candidate_rejections, list)
        assert any("undefined: DefaultDT" in str(item) for item in candidate_rejections)
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        repair = payload["repair"]
        assert repair["revalidated"] is False
        assert repair["consecutive_stagnant_rounds"] == 2
        assert repair["convergence_stop_reason"] == "two_consecutive_no_mutation_repairs"
        assert [item["verifier_effect"] for item in repair["rounds"]] == ["no_op", "no_op"]
        assert all(item["write_tool_evidence"] is False for item in repair["rounds"])
        assert any(
            "undefined: DefaultDT" in str(item)
            for item in repair["rounds"][0]["candidate_rejection_errors_for_next_round"]
        )
        assert "deterministic_no_commit_signature_cache_hit" in repair["rounds"][1]["evidence"]

    @pytest.mark.asyncio
    async def test_workspace_quality_global_nonprogress_cap_precedes_owner_rotation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-owner-rotation-cap",
            config=FactoryConfig(name="quality-owner-rotation-cap"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-20T00:00:00+00:00",
        )
        deterministic_calls = 0
        llm_calls = 0

        def failed_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            return {
                "command": command,
                "exit_code": 1,
                "passed": False,
                "stdout_tail": "src/main.py: repair still required",
                "stderr_tail": "",
                "error": "",
            }

        async def no_deterministic_effect(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal deterministic_calls
            del kwargs
            deterministic_calls += 1
            return [], {"attempted": True, "success": False, "write_tool_evidence": False}

        async def no_llm_effect(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal llm_calls
            del kwargs
            llm_calls += 1
            return [], {
                "attempted": True,
                "success": False,
                "task_id": "TASK-2",
                "repair_target_files": ["src/main.py"],
                "write_tool_evidence": False,
            }

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda _context: [["python", "src/main.py"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda _commands, _context: [])
        monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda _run, _context: None)
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda _context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", failed_command)
        monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", no_deterministic_effect)
        monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", no_llm_effect)
        monkeypatch.setattr(
            workspace_quality_impl,
            "workspace_quality_unclaimed_residual_targets",
            lambda *_args, **_kwargs: ["tests/test_product.py"],
        )
        monkeypatch.setattr(
            workspace_quality_impl,
            "leftover_targets_should_force_owner_rotate",
            lambda *_args, **_kwargs: True,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 8},
        )

        assert passed is False
        assert deterministic_calls == 1
        assert llm_calls == 3
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        repair = payload["repair"]
        assert repair["nonprogress_rounds_since_last_progress"] == 3
        assert repair["convergence_stop_reason"] == "three_nonprogress_repairs_without_verified_progress"
        assert len(repair["rounds"]) == 3

    @pytest.mark.asyncio
    async def test_workspace_quality_mutation_nonprogress_cap_masks_volatile_lines_before_owner_rotation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Line-number churn cannot buy more probes or bypass the global fuse."""

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-mutation-owner-rotation-cap",
            config=FactoryConfig(name="quality-mutation-owner-rotation-cap"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-20T00:00:00+00:00",
        )
        command_calls = 0
        deterministic_calls = 0
        llm_calls = 0

        def failed_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            nonlocal command_calls
            del timeout_seconds
            command_calls += 1
            # The diagnostic meaning is unchanged; edits only move traceback
            # locations, matching the live L3-21 failure shape.
            line = 100 + command_calls * 2
            return {
                "command": command,
                "exit_code": 1,
                "passed": False,
                "stdout_tail": f'File "src/main.py", line {line}, in run\nAssertionError: still red',
                "stderr_tail": "",
                "error": "",
            }

        async def no_deterministic_effect(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal deterministic_calls
            del kwargs
            deterministic_calls += 1
            return [], {"attempted": True, "success": False, "write_tool_evidence": False}

        async def mutating_llm_effect(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal llm_calls
            del kwargs
            llm_calls += 1
            return (
                [
                    {
                        "tool": "edit_file",
                        "success": True,
                        "result": {
                            "file": "src/main.py",
                            "operation": "modify",
                            "before_hash": f"before-{llm_calls}",
                            "after_hash": f"after-{llm_calls}",
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "task_id": "TASK-2",
                    "repair_target_files": ["src/main.py"],
                    "write_tool_evidence": True,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda _context: [["python", "src/main.py"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda _commands, _context: [])
        monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda _run, _context: None)
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda _context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", failed_command)
        monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", no_deterministic_effect)
        monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", mutating_llm_effect)
        monkeypatch.setattr(
            workspace_quality_impl,
            "workspace_quality_unclaimed_residual_targets",
            lambda *_args, **_kwargs: ["tests/test_product.py"],
        )
        monkeypatch.setattr(
            workspace_quality_impl,
            "leftover_targets_should_force_owner_rotate",
            lambda *_args, **_kwargs: True,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 8},
        )

        assert passed is False
        assert command_calls == 4
        assert deterministic_calls == 1
        assert llm_calls == 3
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        repair = payload["repair"]
        assert repair["nonprogress_rounds_since_last_progress"] == 3
        assert repair["convergence_stop_reason"] == "three_nonprogress_repairs_without_verified_progress"
        assert len(repair["rounds"]) == 3
        assert all(item["verifier_effect"] == "equal_count_swap" for item in repair["rounds"])
        assert "deterministic_no_commit_signature_cache_hit" in repair["rounds"][1]["evidence"]
        assert "deterministic_no_commit_signature_cache_hit" in repair["rounds"][2]["evidence"]

    @pytest.mark.asyncio
    async def test_workspace_quality_stops_after_two_equal_count_diagnostic_swaps(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-stagnation",
            config=FactoryConfig(name="quality-stagnation"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-10T00:00:00+00:00",
        )
        diagnostics = (
            "src/index.ts(1,1): error TS7015: first",
            "src/index.ts(1,1): error TS2551: swapped",
            "src/index.ts(1,1): error TS2339: swapped again",
        )
        command_calls = 0
        repair_calls = 0

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            nonlocal command_calls
            del timeout_seconds
            diagnostic = diagnostics[min(command_calls, len(diagnostics) - 1)]
            command_calls += 1
            return {
                "command": command,
                "exit_code": 2,
                "passed": False,
                "stdout_tail": diagnostic,
                "stderr_tail": "",
                "error": "",
            }

        async def fake_apply_workspace_quality_deterministic_repairs(
            *,
            run: FactoryRun,
            artifact_quality_errors: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal repair_calls
            assert run.id == "factory-quality-stagnation"
            assert artifact_quality_errors
            repair_calls += 1
            return (
                [
                    {
                        "tool": "edit_file",
                        "success": True,
                        "result": {
                            "source_tool": "deterministic_typescript_test_repair",
                            "file": "src/index.ts",
                            "operation": "modify",
                        },
                    }
                ],
                {
                    "attempted": True,
                    # A writer/provider claim is non-authoritative until the
                    # affected verifier proves the defect was reduced.
                    "success": True,
                    "source_tools": ["deterministic_typescript_test_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": True,
                    "attempt": repair_attempt,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "run", "build"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda run, context: None)
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_deterministic_repairs",
            fake_apply_workspace_quality_deterministic_repairs,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 3},
        )

        assert passed is False
        assert command_calls == 3
        assert repair_calls == 2
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        repair = payload["repair"]
        assert repair["success"] is False
        assert repair["consecutive_stagnant_rounds"] == 2
        assert repair["convergence_stop_reason"] == "two_consecutive_stagnant_repairs"
        assert [item["verifier_effect"] for item in repair["rounds"]] == [
            "equal_count_swap",
            "equal_count_swap",
        ]
        for item in repair["rounds"]:
            assert item["verifier_authoritative_success"] is False
            assert item["repair_summary"]["claimed_success_before_revalidation"] is True
            assert item["repair_summary"]["success"] is False
            assert item["repair_summary"]["success_authority"] == "post_repair_verifier"

    @pytest.mark.asyncio
    async def test_workspace_quality_same_named_test_timing_jitter_stops_without_regression_guards(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Timing-only verifier noise is stagnant and never becomes a guard."""

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-go-timing-jitter",
            config=FactoryConfig(name="quality-go-timing-jitter"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-22T00:00:00+00:00",
        )
        diagnostics = (
            "--- FAIL: TestStepClampsOnFloor (0.00s)\n"
            "    engine_test.go:112: bubble still moving downward\n"
            "FAIL\nFAIL\tmusicbubble/engine\t0.359s",
            "--- FAIL: TestStepClampsOnFloor (0.00s)\n"
            "    engine_test.go:112: bubble still moving downward\n"
            "FAIL\nFAIL\tmusicbubble/engine\t0.339s",
            "--- FAIL: TestStepClampsOnFloor (0.00s)\n"
            "    engine_test.go:112: bubble still moving downward\n"
            "FAIL\nFAIL\tmusicbubble/engine\t0.330s",
        )
        command_calls = 0
        repair_calls = 0

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            nonlocal command_calls
            del timeout_seconds
            diagnostic = diagnostics[min(command_calls, len(diagnostics) - 1)]
            command_calls += 1
            return {
                "command": command,
                "exit_code": 1,
                "passed": False,
                "stdout_tail": diagnostic,
                "stderr_tail": "",
                "error": "",
            }

        async def mutating_repair(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal repair_calls
            del kwargs
            repair_calls += 1
            return (
                [
                    {
                        "tool": "edit_file",
                        "success": True,
                        "result": {
                            "file": "engine/engine.go",
                            "operation": "modify",
                            "before_hash": f"before-{repair_calls}",
                            "after_hash": f"after-{repair_calls}",
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "task_id": "TASK-3",
                    "repair_target_files": ["engine/engine.go"],
                    "write_tool_evidence": True,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda _context: [["go", "test", "./..."]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda _commands, _context: [])
        monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda _run, _context: None)
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda _context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", mutating_repair)

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 3},
        )

        assert passed is False
        assert command_calls == 4
        assert repair_calls == 3
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        repair = payload["repair"]
        assert repair["convergence_stop_reason"] == "named_test_semantic_contract_conflict_candidate"
        assert [item["verifier_effect"] for item in repair["rounds"]] == [
            "stagnant",
            "stagnant",
            "stagnant",
        ]
        assert repair["rounds"][1]["causal_reanalysis_round_granted"] is True
        assert repair["rounds"][2]["causal_reanalysis_required"] is True
        conflict = repair["semantic_contract_conflict_candidate"]
        assert conflict["reason"] == "bounded_causal_reanalysis_did_not_reduce_named_test_set"
        assert conflict["owner_task_id"] == "TASK-3"
        assert conflict["synthesis_union_test_identities"] == ["go:teststepclampsonfloor"]
        assert conflict["residual_test_identities"] == ["go:teststepclampsonfloor"]
        assert conflict["pm_ce_restart_allowed"] is False
        assert conflict["recommended_route"] == "same_ce_stage_contract_feasibility_review"
        assert all(item["regression_guard_errors"] == [] for item in repair["rounds"])
        assert all(not item.get("regression_guard_errors_for_next_round") for item in repair["rounds"])

    @pytest.mark.asyncio
    async def test_workspace_quality_equal_count_swap_carries_prior_failure_as_llm_regression_guard(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Current residuals stay authoritative while prior fixed tests guard against ping-pong."""

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-regression-guard",
            config=FactoryConfig(name="quality-regression-guard"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-21T00:00:00+00:00",
        )
        diagnostics = (
            "engine_test.go:112: TestStepClampsOnFloor still moving downward",
            "engine_test.go:69: TestStepAppliesGravity velocity=-4.905 want 4.905",
            "engine_test.go:112: TestStepClampsOnFloor still moving downward",
        )
        command_calls = 0
        llm_contexts: list[dict[str, object]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            nonlocal command_calls
            del timeout_seconds
            diagnostic = diagnostics[min(command_calls, len(diagnostics) - 1)]
            command_calls += 1
            return {
                "command": command,
                "exit_code": 1,
                "passed": False,
                "stdout_tail": diagnostic,
                "stderr_tail": "",
                "error": "",
            }

        async def no_deterministic_effect(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            del kwargs
            return [], {"attempted": True, "success": False, "write_tool_evidence": False}

        async def mutating_llm_effect(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            llm_contexts.append(dict(kwargs["context"]))
            attempt = len(llm_contexts)
            return (
                [
                    {
                        "tool": "edit_file",
                        "success": True,
                        "result": {
                            "file": "engine/engine.go",
                            "operation": "modify",
                            "before_hash": f"before-{attempt}",
                            "after_hash": f"after-{attempt}",
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "task_id": "TASK-3",
                    "repair_target_files": ["engine/engine.go"],
                    "write_tool_evidence": True,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda _context: [["go", "test", "./..."]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda _commands, _context: [])
        monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda _run, _context: None)
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda _context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", no_deterministic_effect)
        monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", mutating_llm_effect)
        monkeypatch.setattr(
            workspace_quality_impl,
            "workspace_quality_unclaimed_failing_tu_targets",
            lambda *_args, **_kwargs: [],
        )
        monkeypatch.setattr(
            workspace_quality_impl,
            "workspace_quality_unclaimed_residual_targets",
            lambda *_args, **_kwargs: [],
        )
        monkeypatch.setattr(
            workspace_quality_impl,
            "leftover_targets_should_force_owner_rotate",
            lambda *_args, **_kwargs: False,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 3},
        )

        assert passed is False
        assert len(llm_contexts) == 3
        first_quality = llm_contexts[0].get("director_quality_repair")
        assert not isinstance(first_quality, dict) or not first_quality.get("regression_guard_errors")
        second_quality = llm_contexts[1]["director_quality_repair"]
        assert isinstance(second_quality, dict)
        guards = second_quality["regression_guard_errors"]
        assert isinstance(guards, list)
        assert any("TestStepClampsOnFloor" in str(item) for item in guards)
        assert all("TestStepAppliesGravity" not in str(item) for item in guards)
        synthesis_quality = llm_contexts[2]["director_quality_repair"]
        assert isinstance(synthesis_quality, dict)
        synthesis_guards = synthesis_quality["regression_guard_errors"]
        assert isinstance(synthesis_guards, list)
        assert any("TestStepAppliesGravity" in str(item) for item in synthesis_guards)
        assert all("TestStepClampsOnFloor" not in str(item) for item in synthesis_guards)
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        rounds = payload["repair"]["rounds"]
        assert rounds[0]["regression_guard_errors"] == []
        assert any(
            "TestStepClampsOnFloor" in str(item)
            for item in rounds[0]["regression_guard_errors_for_next_round"]
        )
        assert any("TestStepClampsOnFloor" in str(item) for item in rounds[1]["regression_guard_errors"])
        assert rounds[1]["regression_synthesis_round_granted"] is True

    @pytest.mark.asyncio
    async def test_workspace_quality_aba_oscillation_gets_one_regression_synthesis_round(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A -> B -> A earns one bounded round with current A plus prior B guards.

        Live L3-22 first consumed one no-effect Provider attempt, then two
        physical edits ping-ponged the Go verifier from failure set A to B and
        back to A.  The global non-progress fuse stopped immediately after the
        third round, exactly when the next Director request would first carry
        both the current A residual and the prior B regression guards.  That
        synthesis round must run once, without resetting or renewing the hard
        non-progress budget.
        """

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-aba-regression-synthesis",
            config=FactoryConfig(name="quality-aba-regression-synthesis"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-22T00:00:00+00:00",
        )
        failure_a = (
            "--- FAIL: TestStepClampsOnFloor (0.00s)\n"
            "    engine_test.go:112: still moving downward"
        )
        failure_b = (
            "--- FAIL: TestStepAppliesGravity (0.00s)\n"
            "    engine_test.go:69: velocity=-4.905 want 4.905"
        )
        failure_c = (
            "--- FAIL: TestStepWithRestitutionBounces (0.00s)\n"
            "    engine_test.go:87: velocity=-1 want positive"
        )
        command_calls = 0
        llm_contexts: list[dict[str, object]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            nonlocal command_calls
            del timeout_seconds
            diagnostics_by_repair_attempt = (failure_a, failure_a, failure_b, failure_a, failure_c)
            diagnostic = diagnostics_by_repair_attempt[min(len(llm_contexts), 4)]
            command_calls += 1
            return {
                "command": command,
                "exit_code": 1,
                "passed": False,
                "stdout_tail": diagnostic,
                "stderr_tail": "",
                "error": "",
            }

        async def no_deterministic_effect(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            del kwargs
            return [], {"attempted": True, "success": False, "write_tool_evidence": False}

        async def staged_llm_effect(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            llm_contexts.append(dict(kwargs["context"]))
            attempt = len(llm_contexts)
            if attempt == 1:
                return [], {
                    "attempted": True,
                    "success": False,
                    "task_id": "TASK-3",
                    "repair_target_files": ["engine/engine.go"],
                    "write_tool_evidence": False,
                }
            return (
                [
                    {
                        "tool": "edit_file",
                        "success": True,
                        "result": {
                            "file": "engine/engine.go",
                            "operation": "modify",
                            "before_hash": f"before-{attempt}",
                            "after_hash": f"after-{attempt}",
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "task_id": "TASK-3",
                    "repair_target_files": ["engine/engine.go"],
                    "write_tool_evidence": True,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda _context: [["go", "test", "./..."]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda _commands, _context: [])
        monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda _run, _context: None)
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda _context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", no_deterministic_effect)
        monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", staged_llm_effect)
        monkeypatch.setattr(
            workspace_quality_impl,
            "workspace_quality_unclaimed_failing_tu_targets",
            lambda *_args, **_kwargs: [],
        )
        monkeypatch.setattr(
            workspace_quality_impl,
            "workspace_quality_unclaimed_residual_targets",
            lambda *_args, **_kwargs: [],
        )
        monkeypatch.setattr(
            workspace_quality_impl,
            "leftover_targets_should_force_owner_rotate",
            lambda *_args, **_kwargs: False,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 6},
        )

        assert passed is False
        assert len(llm_contexts) == 4
        synthesis_quality = llm_contexts[3]["director_quality_repair"]
        assert isinstance(synthesis_quality, dict)
        synthesis_guards = synthesis_quality["regression_guard_errors"]
        assert isinstance(synthesis_guards, list)
        assert any("TestStepAppliesGravity" in str(item) for item in synthesis_guards)
        assert all("TestStepClampsOnFloor" not in str(item) for item in synthesis_guards)
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        repair = payload["repair"]
        assert len(repair["rounds"]) == 4
        assert repair["rounds"][2]["regression_synthesis_round_granted"] is True
        assert any(
            "TestStepClampsOnFloor" in str(item)
            for item in repair["rounds"][2]["reintroduced_regression_guard_errors"]
        )
        assert (
            repair["convergence_stop_reason"] == "named_test_semantic_contract_conflict_candidate"
        ), repair
        conflict = repair["semantic_contract_conflict_candidate"]
        assert conflict["owner_task_id"] == "TASK-3"
        assert conflict["synthesis_union_test_identities"] == [
            "go:teststepappliesgravity",
            "go:teststepclampsonfloor",
        ]
        assert conflict["residual_test_identities"] == ["go:teststepwithrestitutionbounces"]

    @pytest.mark.asyncio
    async def test_workspace_quality_count_changing_aba_carries_regression_guards(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A 1 -> 2 -> 1 verifier trade must preserve every resolved named test.

        Live L3-22 alternated one gravity failure with two floor failures.  The
        old guard projection only handled equal-count swaps, so each Director
        request forgot the tests fixed by the immediately preceding edit.
        """

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-count-changing-aba",
            config=FactoryConfig(name="quality-count-changing-aba"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-22T00:00:00+00:00",
        )
        failure_a = "engine_test.go:69: TestStepAppliesGravity velocity=-4.905 want 4.905"
        failure_b1 = "engine_test.go:112: TestStepClampsOnFloor still moving downward"
        failure_b2 = "main_test.go:137: TestRunEndToEndViaLibraryAPI still moving downward"
        llm_contexts: list[dict[str, object]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            if len(llm_contexts) >= 3:
                return {
                    "command": command,
                    "exit_code": 0,
                    "passed": True,
                    "stdout_tail": "ok",
                    "stderr_tail": "",
                    "error": "",
                }
            diagnostic = failure_a if len(llm_contexts) in {0, 2} else f"{failure_b1}\n{failure_b2}"
            return {
                "command": command,
                "exit_code": 1,
                "passed": False,
                "stdout_tail": diagnostic,
                "stderr_tail": "",
                "error": "",
            }

        async def no_deterministic_effect(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            del kwargs
            return [], {
                "attempted": True,
                "success": False,
                "write_tool_evidence": False,
                "stage": "runtime_plan_probe_unplannable",
                "success_reason": "task_boundary_interface_discrepancy_required",
                "task_id": "TASK-1",
                "task_boundary_owner_evidence": {
                    "schema_version": "factory.workspace_quality_task_owner.v1",
                    "source": "task_runtime_execution_attempt",
                    "task_id": "TASK-1",
                    "owner_target_files": ["engine/engine.go"],
                    "diagnostic_target_files": ["engine/engine.go"],
                    "in_scope_diagnostic_target_files": ["engine/engine.go"],
                    "out_of_scope_diagnostic_target_files": [],
                    "director_local_repair_allowed": True,
                },
                "plan_probe_preaudit": {
                    "status": "coverage_matched_but_unplannable",
                    "plannable_source_tools": [],
                    "covered_unplannable_source_tools": ["deterministic_go_test_assertion_align_repair"],
                    "covered_unplannable_diagnostic_count": 1,
                },
            }

        async def mutating_llm_effect(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            llm_contexts.append(dict(kwargs["context"]))
            attempt = len(llm_contexts)
            return (
                [
                    {
                        "tool": "edit_file",
                        "success": True,
                        "result": {
                            "file": "engine/engine.go",
                            "operation": "modify",
                            "before_hash": f"before-{attempt}",
                            "after_hash": f"after-{attempt}",
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "task_id": "TASK-1",
                    "repair_target_files": ["engine/engine.go"],
                    "write_tool_evidence": True,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda _context: [["go", "test", "./..."]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda _commands, _context: [])
        monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda _run, _context: None)
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda _context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", no_deterministic_effect)
        monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", mutating_llm_effect)
        monkeypatch.setattr(
            workspace_quality_impl,
            "workspace_quality_unclaimed_failing_tu_targets",
            lambda *_args, **_kwargs: [],
        )
        monkeypatch.setattr(
            workspace_quality_impl,
            "workspace_quality_unclaimed_residual_targets",
            lambda *_args, **_kwargs: [],
        )
        monkeypatch.setattr(
            workspace_quality_impl,
            "leftover_targets_should_force_owner_rotate",
            lambda *_args, **_kwargs: False,
        )

        passed, _artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 4},
        )

        assert passed is True
        assert len(llm_contexts) == 3
        second_quality = llm_contexts[1]["director_quality_repair"]
        assert isinstance(second_quality, dict)
        assert any("TestStepAppliesGravity" in str(item) for item in second_quality["regression_guard_errors"])
        third_quality = llm_contexts[2]["director_quality_repair"]
        assert isinstance(third_quality, dict)
        assert any("TestStepClampsOnFloor" in str(item) for item in third_quality["regression_guard_errors"])
        assert any("TestRunEndToEndViaLibraryAPI" in str(item) for item in third_quality["regression_guard_errors"])
        assert all("TestStepAppliesGravity" not in str(item) for item in third_quality["regression_guard_errors"])

    @pytest.mark.asyncio
    async def test_workspace_quality_new_plannable_repair_gets_next_bounded_round(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An equal-count swap may unmask one executable deterministic repair.

        Live L3-22 changed a Go constant-conversion failure into
        ``undefined: math``. Go diagnostics had no stable extracted error code,
        so the second equal-count swap tripped the stagnation breaker even
        though plan-probe had just exposed the executable stdlib-import repair.
        The newly plannable source_tool earns one more bounded round; repeated
        exposure of the same tool still remains subject to the normal breaker.
        """

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-go-plannable-unmask",
            config=FactoryConfig(name="quality-go-plannable-unmask"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-21T00:00:00+00:00",
        )
        diagnostics = (
            "./main.go:55:15: cannot convert totalTime / step + 0.5 to type int",
            "./main.go:55:15: cannot convert totalTime / step + 0.5 to type int (rephrased)",
            "./main.go:55:15: undefined: math",
        )
        command_calls = 0
        repair_calls = 0

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            nonlocal command_calls
            del timeout_seconds
            if command_calls >= len(diagnostics):
                command_calls += 1
                return {
                    "command": command,
                    "exit_code": 0,
                    "passed": True,
                    "stdout_tail": "ok\n",
                    "stderr_tail": "",
                    "error": "",
                }
            diagnostic = diagnostics[command_calls]
            command_calls += 1
            return {
                "command": command,
                "exit_code": 1,
                "passed": False,
                "stdout_tail": "",
                "stderr_tail": diagnostic,
                "error": "",
            }

        async def fake_apply_workspace_quality_deterministic_repairs(
            *,
            run: FactoryRun,
            artifact_quality_errors: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal repair_calls
            assert run.id == "factory-quality-go-plannable-unmask"
            assert artifact_quality_errors
            repair_calls += 1
            return (
                [
                    {
                        "tool": "edit_file",
                        "success": True,
                        "result": {
                            "source_tool": "deterministic_go_missing_stdlib_import_repair",
                            "file": "main.go",
                            "operation": "modify",
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "source_tools": ["deterministic_go_missing_stdlib_import_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": True,
                    "attempt": repair_attempt,
                },
            )

        def fake_plan_probe(errors: list[str]) -> dict[str, object]:
            if any("undefined: math" in item for item in errors):
                return {
                    "status": "covered_plannable",
                    "plannable_source_tools": ["deterministic_go_missing_stdlib_import_repair"],
                }
            return {"status": "coverage_gap", "plannable_source_tools": []}

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["go", "test", "./..."]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda run, context: None)
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(executor, "_workspace_quality_repair_plan_probe_report", fake_plan_probe)
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_deterministic_repairs",
            fake_apply_workspace_quality_deterministic_repairs,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 3},
        )

        assert passed is True
        assert command_calls == 4
        assert repair_calls == 3
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        repair = payload["repair"]
        assert repair["success"] is True
        assert repair["convergence_stop_reason"] == "verifier_passed"
        assert repair["rounds"][1]["verifier_effect"] == "equal_count_swap"
        assert repair["rounds"][1]["newly_plannable_source_tools"] == [
            "deterministic_go_missing_stdlib_import_repair"
        ]

    @pytest.mark.asyncio
    async def test_workspace_quality_rust_forward_unmask_chain_runs_to_verifier(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sequentially unmasked rustc phases must not trip the stagnation breaker.

        Live L1-05 shape: E0432 (resolution) -> E0277 (trait bound) -> E0507
        (borrow check), each round carrying a real ``edit_file`` mutation.
        Every transition is a disjoint rustc code set, so rounds keep their
        budget and only the hard round ceiling bounds the loop.
        """

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-rust-unmask",
            config=FactoryConfig(name="quality-rust-unmask"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-14T00:00:00+00:00",
        )
        diagnostics = (
            "error[E0432]: unresolved imports `engine::RecipeDraft` --> src/lib.rs:23:50",
            "error[E0277]: the trait bound `PaletteFixture: Copy` is not satisfied --> src/engine/flavor_rules.rs:162:15",
            "error[E0507]: cannot move out of a shared reference --> src/engine/flavor_rules.rs:162:15",
            "error[E0308]: mismatched types --> src/engine/flavor_rules.rs:170:22",
        )
        command_calls = 0
        repair_calls = 0

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            nonlocal command_calls
            del timeout_seconds
            diagnostic = diagnostics[min(command_calls, len(diagnostics) - 1)]
            command_calls += 1
            return {
                "command": command,
                "exit_code": 2,
                "passed": False,
                "stdout_tail": diagnostic,
                "stderr_tail": "",
                "error": "",
            }

        async def fake_apply_workspace_quality_deterministic_repairs(
            *,
            run: FactoryRun,
            artifact_quality_errors: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal repair_calls
            assert run.id == "factory-quality-rust-unmask"
            assert artifact_quality_errors
            repair_calls += 1
            repaired_file = "src/lib.rs" if "src/lib.rs" in artifact_quality_errors[0] else "src/engine/flavor_rules.rs"
            return (
                [
                    {
                        "tool": "edit_file",
                        "success": True,
                        "result": {
                            "source_tool": "deterministic_rust_post_repair",
                            "file": repaired_file,
                            "operation": "modify",
                            "before_sha256": "a" * 64,
                            "after_sha256": "b" * 64,
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "source_tools": ["deterministic_rust_post_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": True,
                    "attempt": repair_attempt,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["cargo", "test", "--quiet"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda run, context: None)
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_deterministic_repairs",
            fake_apply_workspace_quality_deterministic_repairs,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 3},
        )

        assert passed is False
        # Three repair rounds consumed the full budget instead of stopping
        # after two miscounted "swaps".
        assert repair_calls == 3
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        repair = payload["repair"]
        assert repair["consecutive_stagnant_rounds"] == 0
        assert repair["convergence_stop_reason"] != "two_consecutive_stagnant_repairs"
        assert [item["verifier_effect"] for item in repair["rounds"]] == [
            "forward_unmask",
            "forward_unmask",
            "forward_unmask",
        ]

    @pytest.mark.asyncio
    async def test_workspace_quality_rust_unmask_oscillation_still_stops(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A -> B -> A code ping-pong must keep tripping the stagnation breaker.

        Without the seen-code guard, alternating disjoint rustc codes would
        reset the counter forever and burn the whole round budget on churn.
        """

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-rust-oscillation",
            config=FactoryConfig(name="quality-rust-oscillation"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-14T00:00:00+00:00",
        )
        diagnostics = (
            "error[E0277]: the trait bound `PaletteFixture: Copy` is not satisfied --> src/engine/flavor_rules.rs:162:15",
            "error[E0507]: cannot move out of a shared reference --> src/engine/flavor_rules.rs:162:15",
        )
        command_calls = 0
        repair_calls = 0

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            nonlocal command_calls
            del timeout_seconds
            # Alternate A <-> B across every check (including the initial one)
            # so consecutive revisits are true oscillations.
            diagnostic = diagnostics[command_calls % len(diagnostics)]
            command_calls += 1
            return {
                "command": command,
                "exit_code": 2,
                "passed": False,
                "stdout_tail": diagnostic,
                "stderr_tail": "",
                "error": "",
            }

        async def fake_apply_workspace_quality_deterministic_repairs(
            *,
            run: FactoryRun,
            artifact_quality_errors: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal repair_calls
            del run, artifact_quality_errors
            repair_calls += 1
            return (
                [
                    {
                        "tool": "edit_file",
                        "success": True,
                        "result": {
                            "source_tool": "deterministic_rust_post_repair",
                            "file": "src/engine/flavor_rules.rs",
                            "operation": "modify",
                            "before_sha256": "a" * 64,
                            "after_sha256": "b" * 64,
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "source_tools": ["deterministic_rust_post_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": True,
                    "attempt": repair_attempt,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["cargo", "test", "--quiet"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda run, context: None)
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_deterministic_repairs",
            fake_apply_workspace_quality_deterministic_repairs,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 5},
        )

        assert passed is False
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        repair = payload["repair"]
        assert repair["convergence_stop_reason"] == "two_consecutive_stagnant_repairs"
        assert repair["consecutive_stagnant_rounds"] == 2
        # Round 1 exposes a novel diagnostic. The owner-aware residual guard
        # preserves one extra local round before the repeated A/B signature
        # trips the same-owner stagnation breaker.
        assert len(repair["rounds"]) == 4

    @pytest.mark.asyncio
    async def test_workspace_quality_new_diagnostic_signature_reenables_deterministic_probe(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Different non-progress classes must not masquerade as one repeated stall.

        A real LLM mutation may expose a different verifier signature.  The
        no-commit cache is scoped to the old signature, so the new diagnostic
        still gets one deterministic materialization-schedule probe.
        """

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-mixed-nonprogress",
            config=FactoryConfig(name="quality-mixed-nonprogress"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-14T00:00:00+00:00",
        )
        command_calls = 0
        deterministic_calls = 0
        llm_calls = 0

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            nonlocal command_calls
            del timeout_seconds
            command_calls += 1
            if command_calls == 1:
                return {
                    "command": command,
                    "exit_code": 1,
                    "passed": False,
                    "stdout_tail": "main_test.go:324: behavior assertion failed",
                    "stderr_tail": "",
                    "error": "",
                }
            if command_calls == 2:
                return {
                    "command": command,
                    "exit_code": 1,
                    "passed": False,
                    "stdout_tail": "./main_test.go:46:9: undefined: osStdout",
                    "stderr_tail": "",
                    "error": "",
                }
            return {
                "command": command,
                "exit_code": 0,
                "passed": True,
                "stdout_tail": "ok\tascii-pet-terminal",
                "stderr_tail": "",
                "error": "",
            }

        async def fake_deterministic_repairs(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal deterministic_calls
            del kwargs
            deterministic_calls += 1
            if deterministic_calls == 1:
                return [], {"attempted": True, "success": False, "write_tool_evidence": False}
            return (
                [
                    {
                        "tool": "edit_file",
                        "success": True,
                        "result": {"file": "main_test.go", "operation": "modify"},
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "source_tools": ["deterministic_go_test_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": True,
                },
            )

        async def fake_llm_repairs(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal llm_calls
            del kwargs
            llm_calls += 1
            return (
                [
                    {
                        "tool": "edit_file",
                        "success": True,
                        "result": {"file": "main_test.go", "operation": "modify"},
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "source_tools": ["director_materialization_quality_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": True,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["go", "test", "./..."]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda run, context: None)
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", fake_deterministic_repairs)
        monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", fake_llm_repairs)

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 3},
        )

        assert passed is True
        assert command_calls == 3
        assert deterministic_calls == 2
        assert llm_calls == 1
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        repair = payload["repair"]
        assert repair["success"] is True
        assert repair["consecutive_stagnant_rounds"] == 0
        assert repair["convergence_stop_reason"] == "verifier_passed"
        assert [item["verifier_effect"] for item in repair["rounds"]] == [
            "equal_count_swap",
            "resolved",
        ]

    @pytest.mark.asyncio
    async def test_workspace_quality_same_signature_no_commit_stops_without_reclaiming_deterministic(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An unchanged signature cannot reopen deterministic TaskRuntime attempts."""

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-mixed-nonprogress-cap",
            config=FactoryConfig(name="quality-mixed-nonprogress-cap"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-20T00:00:00+00:00",
        )
        command_calls = 0
        deterministic_calls = 0
        llm_calls = 0

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            nonlocal command_calls
            del timeout_seconds
            command_calls += 1
            diagnostic = (
                "tests/test_product.py:10: assertion failed"
                if command_calls == 1
                else "src/product.py:20: ValueError not raised"
            )
            return {
                "command": command,
                "exit_code": 1,
                "passed": False,
                "stdout_tail": diagnostic,
                "stderr_tail": "",
                "error": "",
            }

        async def fake_deterministic_repairs(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal deterministic_calls
            del kwargs
            deterministic_calls += 1
            if deterministic_calls == 2:
                return (
                    [{"tool": "edit_file", "success": True, "result": {"file": "src/product.py"}}],
                    {
                        "attempted": True,
                        "success": True,
                        "task_id": "TASK-2",
                        "write_tool_evidence": True,
                    },
                )
            return [], {
                "attempted": True,
                "success": False,
                "task_id": "TASK-2",
                "write_tool_evidence": False,
            }

        async def fake_llm_repairs(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal llm_calls
            del kwargs
            llm_calls += 1
            return (
                [{"tool": "edit_file", "success": False, "result": {"error_code": "stale_edit"}}],
                {
                    "attempted": True,
                    "success": False,
                    "task_id": "TASK-2",
                    "write_tool_evidence": False,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["python", "-m", "unittest"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda run, context: None)
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", fake_deterministic_repairs)
        monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", fake_llm_repairs)

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 8},
        )

        assert passed is False
        assert command_calls == 1
        assert deterministic_calls == 1
        assert llm_calls == 2
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        repair = payload["repair"]
        assert repair["nonprogress_rounds_since_last_progress"] == 2
        assert repair["convergence_stop_reason"] == "two_consecutive_no_mutation_repairs"
        assert [item["verifier_effect"] for item in repair["rounds"]] == [
            "no_op",
            "no_op",
        ]
        assert "deterministic_no_commit_signature_cache_hit" in repair["rounds"][1]["evidence"]

    @pytest.mark.asyncio
    async def test_workspace_quality_provider_timeout_gets_one_transport_retry_without_spending_semantic_budget(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A transient provider timeout is not a semantic no-op repair."""

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-provider-timeout-retry",
            config=FactoryConfig(name="quality-provider-timeout-retry"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-22T00:00:00+00:00",
        )
        command_calls = 0
        llm_calls = 0

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            nonlocal command_calls
            del timeout_seconds
            command_calls += 1
            passed = command_calls >= 2
            return {
                "command": command,
                "exit_code": 0 if passed else 1,
                "passed": passed,
                "stdout_tail": "" if passed else "--- FAIL: TestPhysicsContract (0.00s)",
                "stderr_tail": "",
                "error": "",
            }

        async def fake_deterministic_repairs(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            del kwargs
            return [], {
                "attempted": True,
                "success": False,
                "task_id": "TASK-1",
                "write_tool_evidence": False,
            }

        async def fake_llm_repairs(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal llm_calls
            del kwargs
            llm_calls += 1
            if llm_calls == 1:
                return [], {
                    "attempted": True,
                    "success": False,
                    "task_id": "TASK-1",
                    "error": "TransactionKernel execution failed: Request timeout (300.0s)",
                    "write_tool_evidence": False,
                }
            return (
                [{"tool": "edit_file", "success": True, "result": {"file": "src/product.go"}}],
                {
                    "attempted": True,
                    "success": True,
                    "task_id": "TASK-1",
                    "write_tool_evidence": True,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["go", "test", "./..."]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda run, context: None)
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", fake_deterministic_repairs)
        monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", fake_llm_repairs)

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 8},
        )

        assert passed is True
        assert command_calls == 2
        assert llm_calls == 2
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        repair = payload["repair"]
        assert repair["success"] is True
        assert repair["provider_transport_retry_granted"] is True
        assert repair["nonprogress_rounds_since_last_progress"] == 0
        assert [item["verifier_effect"] for item in repair["rounds"]] == [
            "provider_timeout",
            "resolved",
        ]

    @pytest.mark.asyncio
    async def test_workspace_quality_second_provider_timeout_stops_without_semantic_nonprogress(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The transport retry is bounded and cannot burn semantic repair rounds forever."""

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-provider-timeout-exhausted",
            config=FactoryConfig(name="quality-provider-timeout-exhausted"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-22T00:00:00+00:00",
        )
        llm_calls = 0

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            return {
                "command": command,
                "exit_code": 1,
                "passed": False,
                "stdout_tail": "--- FAIL: TestPhysicsContract (0.00s)",
                "stderr_tail": "",
                "error": "",
            }

        async def fake_deterministic_repairs(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            del kwargs
            return [], {
                "attempted": True,
                "success": False,
                "task_id": "TASK-1",
                "write_tool_evidence": False,
            }

        async def fake_llm_repairs(**kwargs: object) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal llm_calls
            del kwargs
            llm_calls += 1
            error = (
                "TransactionKernel execution failed: Request timeout (300.0s)"
                if llm_calls == 1
                else "director_quality_repair_2_llm_timeout"
            )
            return [], {
                "attempted": True,
                "success": False,
                "task_id": "TASK-1",
                "error": error,
                "write_tool_evidence": False,
            }

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["go", "test", "./..."]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_workspace_quality_task_boundary_blocker", lambda run, context: None)
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(executor, "_apply_workspace_quality_deterministic_repairs", fake_deterministic_repairs)
        monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", fake_llm_repairs)

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 8},
        )

        assert passed is False
        assert llm_calls == 2
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        repair = payload["repair"]
        assert repair["provider_transport_retry_granted"] is True
        assert repair["nonprogress_rounds_since_last_progress"] == 0
        assert repair["convergence_stop_reason"] == "quality_repair_provider_timeout_exhausted"
        assert [item["verifier_effect"] for item in repair["rounds"]] == [
            "provider_timeout",
            "provider_timeout",
        ]

    @pytest.mark.asyncio
    async def test_workspace_quality_llm_repair_context_includes_ce_blueprint_and_catalog(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from polaris.cells.factory.pipeline.internal import factory_workspace_quality_impl
        from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptAuthorityV1

        executor = _executor(tmp_path)
        heartbeat_calls: list[dict[str, Any]] = []
        original_heartbeat = TaskRuntimeExecutionAttemptAuthorityV1.heartbeat

        def tracking_heartbeat(
            authority: TaskRuntimeExecutionAttemptAuthorityV1,
            **kwargs: Any,
        ) -> Any:
            heartbeat_calls.append(kwargs)
            return original_heartbeat(authority, **kwargs)

        monkeypatch.setattr(
            factory_workspace_quality_impl,
            "_WORKSPACE_QUALITY_REPAIR_HEARTBEAT_INTERVAL_SECONDS",
            0.001,
        )
        monkeypatch.setattr(TaskRuntimeExecutionAttemptAuthorityV1, "heartbeat", tracking_heartbeat)
        (tmp_path / ".polaris").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".polaris" / "catalog_contract.json").write_text(
            json.dumps(
                {
                    "project_id": "L2-08",
                    "primary_language": "javascript",
                    "project_type": "collaboration_toy",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "engine" / "rules.js").write_text("export const meteor = 1;\n", encoding="utf-8")
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "goal": "Create source and entrypoint",
                        "target_files": ["package.json", "src/engine/rules.js", "src/index.js"],
                    }
                ]
            },
        )
        executor._write_json_artifact(
            "runtime/state/blueprints/factory-context.review.json",
            {
                "generated_blueprints": 1,
                "total_tasks": 1,
                "blueprints": [
                    {
                        "task_id": "TASK-1",
                        "status": "generated",
                        "blueprint_id": "ce_TASK-1",
                        "summary": "Chief Engineer blueprint defines source and entrypoint contracts.",
                    }
                ],
            },
        )
        from polaris.cells.runtime.task_runtime.public import TaskRuntimeService

        TaskRuntimeService(str(tmp_path)).ensure_task_row(
            external_task_id="TASK-1",
            subject="Create source and entrypoint",
            description="Own the JavaScript source repaired by workspace verification",
            metadata={
                "external_task_id": "TASK-1",
                "factory_run_id": "factory-context",
                "goal": "Create source and entrypoint",
                "scope": "Own the JavaScript source repaired by workspace verification",
                "target_files": ["package.json", "src/engine/rules.js", "src/index.js"],
                "acceptance_criteria": ["npm test passes"],
                "blueprint_id": "ce_TASK-1",
                "runtime_blueprint_path": ".polaris/blueprints/ce_TASK-1.json",
                "role": "director",
            },
        )
        captured: dict[str, Any] = {}

        async def fake_run_director_materialization_quality_repair(
            workspace: str,
            *,
            task: dict[str, Any],
            target_task_id: str,
            run_id: str,
            context: dict[str, Any],
            original_message: str,
            llm_call_timeout: float,
            artifact_quality_errors: list[str],
            changed_files: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            await asyncio.sleep(0.01)
            from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
            from polaris.cells.roles.runtime.public.service import RoleRuntimeService

            command = ExecuteRoleSessionCommandV1(
                role="director",
                session_id=str(context["session_id"]),
                workspace=workspace,
                user_message="repair current verifier failure",
                run_id=run_id,
                task_id=target_task_id,
                context=context,
                metadata=dict(context.get("metadata") or {}),
            )
            attempt_validation = RoleRuntimeService()._validate_directed_effect_session_attempt(command)
            captured.update(
                {
                    "workspace": workspace,
                    "task": task,
                    "target_task_id": target_task_id,
                    "run_id": run_id,
                    "context": context,
                    "original_message": original_message,
                    "llm_call_timeout": llm_call_timeout,
                    "artifact_quality_errors": artifact_quality_errors,
                    "changed_files": changed_files,
                    "repair_attempt": repair_attempt,
                    "attempt_validation": attempt_validation,
                }
            )
            return (
                [
                    {
                        "tool": "edit_file",
                        "success": True,
                        "result": {
                            "file": "src/engine/rules.js",
                            "operation": "modify",
                            "before_sha256": "a" * 64,
                            "after_sha256": "b" * 64,
                            "source_tool": "director_materialization_quality_repair",
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "source_tools": ["director_materialization_quality_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": True,
                },
            )

        monkeypatch.setattr(
            "polaris.cells.roles.adapters.public.service.run_director_materialization_quality_repair",
            fake_run_director_materialization_quality_repair,
        )

        run = FactoryRun(
            id="factory-context",
            config=FactoryConfig(name="quality-repair-context", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-13T00:00:00+00:00",
        )
        _, summary = await executor._apply_workspace_quality_llm_repairs(
            run=run,
            context={
                "factory_run_deadline_epoch_seconds": 4_102_444_800.0,
                "factory_run_deadline_source": "unit_test",
                "factory_run_timeout_seconds": 5400,
                "factory_director_execution_deadline_epoch_seconds": 4_102_444_700.0,
                "request_timeout_seconds": 600,
                "director_quality_repair": {
                    "regression_guard_errors": [f"prior verifier guard {index}" for index in range(8)],
                    "causal_reanalysis_required": True,
                    "untrusted_target_override": ["outside/scope.go"],
                },
            },
            artifact_quality_errors=["npm run build failed"],
            repair_attempt=1,
        )

        assert summary["repair_mode"] == "director_llm"
        assert heartbeat_calls
        assert heartbeat_calls[0]["lease_ttl_seconds"] == 300
        assert heartbeat_calls[0]["context_summary"] == "director_workspace_quality_llm_repair"
        repair_context = captured["context"]
        assert repair_context["language"] == "javascript"
        assert repair_context["programming_language"] == "javascript"
        assert repair_context["project_type"] == "collaboration_toy"
        assert repair_context["factory_run_deadline_epoch_seconds"] == 4_102_444_800.0
        assert repair_context["factory_run_deadline_source"] == "unit_test"
        assert repair_context["factory_run_timeout_seconds"] == 5400
        assert repair_context["factory_director_execution_deadline_epoch_seconds"] == 4_102_444_700.0
        assert repair_context["request_timeout_seconds"] == 600
        assert repair_context["_transaction_kernel_forced_tool_choice"] == "required"
        assert repair_context["director_quality_repair"] == {
            "repair_target_files": ["src/engine/rules.js"],
            "write_only_single_target": {"target_file": "src/engine/rules.js"},
            "regression_guard_errors": [f"prior verifier guard {index}" for index in range(6)],
            "causal_reanalysis_required": True,
        }
        assert repair_context["ce_blueprint"]["artifact"] == "runtime/state/blueprints/factory-context.review.json"
        assert "Chief Engineer blueprint" in repair_context["chief_engineer_blueprint_evidence"]
        # The QA retry must preserve the original TaskRuntime owner contract.
        # roles.adapters promotes PM/CE final-request evidence from these fields;
        # replacing them with a target-files-only shell makes the physical
        # provider request fail closed before Director can repair anything.
        assert captured["task"]["goal"] == "Create source and entrypoint"
        assert captured["task"]["scope"] == "Own the JavaScript source repaired by workspace verification"
        assert captured["task"]["acceptance_criteria"] == ["npm test passes"]
        assert captured["task"]["metadata"]["blueprint_id"] == "ce_TASK-1"
        assert captured["task"]["metadata"]["runtime_blueprint_path"] == ".polaris/blueprints/ce_TASK-1.json"
        from polaris.kernelone.events.final_request_evidence import looks_like_pm_contract_payload

        assert looks_like_pm_contract_payload(captured["task"]) is True
        assert captured["target_task_id"] == "TASK-1"
        execution_attempt = repair_context["task_runtime_execution_attempt"]
        authority = repair_context["task_runtime_execution_attempt_authority"]
        assert execution_attempt.external_task_id == captured["target_task_id"]
        assert execution_attempt.run_id == "factory-context"
        assert execution_attempt.role_id == "director"
        assert repair_context["session_id"] == execution_attempt.session_id
        assert captured["attempt_validation"].status == "valid"
        validated_attempt = captured["attempt_validation"].execution_attempt
        assert validated_attempt is not None
        assert {key: value for key, value in validated_attempt.to_record().items() if key != "lease_expires_at"} == {
            key: value for key, value in execution_attempt.to_record().items() if key != "lease_expires_at"
        }
        authority_snapshot = authority.snapshot(lock_timeout_seconds=5.0)
        assert authority_snapshot.success is True
        assert authority_snapshot.identity is not None
        assert {
            key: value for key, value in authority_snapshot.identity.to_record().items() if key != "lease_expires_at"
        } == {key: value for key, value in execution_attempt.to_record().items() if key != "lease_expires_at"}
        assert summary["task_runtime_repair_attempt"] == {
            "task_id": captured["target_task_id"],
            "session_id": execution_attempt.session_id,
            "settled": False,
            "outcome": "pending_revalidation",
        }
        settled = await factory_workspace_quality_impl._settle_pending_workspace_quality_repair_attempt(
            executor,
            summary.pop("_pending_task_runtime_repair_attempt"),
            accepted=True,
            reason="test_post_repair_verifier_passed",
        )
        assert settled is not None
        assert settled["outcome"] == "completed"
        task_rows = TaskRuntimeService(str(tmp_path)).list_task_rows(include_terminal=True)
        owner_row = next(row for row in task_rows if row["metadata"].get("external_task_id") == "TASK-1")
        assert owner_row["status"] == "completed"

    @pytest.mark.asyncio
    async def test_workspace_quality_ignores_deterministic_results_without_write_evidence(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-deterministic-no-write",
            config=FactoryConfig(name="quality-deterministic-no-write"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-23T00:00:00+00:00",
        )
        state = {"repaired": False}
        llm_repair_calls = 0

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            if state["repaired"]:
                return {
                    "command": command,
                    "exit_code": 0,
                    "passed": True,
                    "stdout_tail": "test passed",
                    "stderr_tail": "",
                    "error": "",
                }
            return {
                "command": command,
                "exit_code": 1,
                "passed": False,
                "stdout_tail": "> node tests/run-tests.js",
                "stderr_tail": "Error: Cannot find module 'tests/run-tests.js'",
                "error": "",
            }

        async def fake_apply_workspace_quality_deterministic_repairs(
            *,
            run: FactoryRun,
            artifact_quality_errors: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            assert run.id == "factory-quality-deterministic-no-write"
            assert repair_attempt == 1
            assert artifact_quality_errors
            return (
                [
                    {
                        "tool": "inspect_package_script",
                        "success": False,
                        "result": {
                            "source_tool": "director_materialization_quality_repair",
                            "reason": "missing target remains unresolved",
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": False,
                    "source_tools": ["director_materialization_quality_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": False,
                    # Attempt evidence is not mutation evidence and must not
                    # suppress the same-task LLM edit fallback.
                    "evidence": ["coverage matched but deterministic repair made no mutation"],
                },
            )

        async def fake_apply_workspace_quality_llm_repairs(
            *,
            run: FactoryRun,
            context: dict[str, Any],
            artifact_quality_errors: list[str],
            repair_attempt: int,
            owner_target_files: list[str] | None = None,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal llm_repair_calls
            assert run.id == "factory-quality-deterministic-no-write"
            assert context["workspace_quality_repair_max_rounds"] == 1
            assert artifact_quality_errors
            assert repair_attempt == 1
            assert owner_target_files is None
            llm_repair_calls += 1
            state["repaired"] = True
            return (
                [
                    {
                        "tool": "write_file",
                        "tool_name": "write_file",
                        "success": True,
                        "result": {
                            "source_tool": "director_materialization_quality_repair",
                            "file": "tests/run-tests.js",
                            "operation": "create",
                            "before_sha256": "file_absent",
                            "after_sha256": "b" * 64,
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "repair_mode": "director_llm",
                    "source_tools": ["director_materialization_quality_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": True,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "test"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_deterministic_repairs",
            fake_apply_workspace_quality_deterministic_repairs,
        )
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_llm_repairs",
            fake_apply_workspace_quality_llm_repairs,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 1},
        )

        assert passed is True
        assert llm_repair_calls == 1
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["repair"]["success"] is True
        assert payload["repair"]["write_tool_evidence"] is True
        assert payload["repair"]["rounds"][0]["evidence"] == [
            "repair_write:tool=director_materialization_quality_repair;file=tests/run-tests.js;operation=create",
            "repair_hash:file=tests/run-tests.js;before=file_absent;after=bbbbbbbbbbbbbbbb",
        ]

    @pytest.mark.asyncio
    async def test_workspace_quality_projects_task_boundary_triage_without_llm_fallback(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-task-boundary-triage",
            config=FactoryConfig(name="quality-task-boundary-triage"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-28T00:00:00+00:00",
        )
        llm_repair_calls = 0
        post_repair_calls = 0

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            return {
                "command": command,
                "exit_code": 2,
                "passed": False,
                "stdout_tail": "tests/behavior.test.ts(3,10): error TS2305: Module has no exported member 'openMarket'.",
                "stderr_tail": "",
                "error": "",
            }

        async def fake_apply_workspace_quality_deterministic_repairs(
            *,
            run: FactoryRun,
            artifact_quality_errors: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            assert run.id == "factory-quality-task-boundary-triage"
            assert repair_attempt == 1
            assert artifact_quality_errors
            return (
                [],
                {
                    "stage": "runtime_plan_probe_unplannable",
                    "attempted": True,
                    "success": False,
                    "success_reason": "task_boundary_interface_discrepancy_required",
                    "tool_results": 0,
                    "source_tools": [],
                    "plan_probe_preaudit": {
                        "status": "coverage_matched_but_unplannable",
                        "plannable_source_tools": [],
                        "covered_unplannable_source_tools": ["deterministic_typescript_missing_export_repair"],
                    },
                    "interface_discrepancy_evidence": {
                        "schema_version": "director.interface_discrepancy_receipt.v1",
                        "reason": "coverage_matched_but_unplannable",
                        "recommended_owner": "chief_engineer",
                        "recommended_route": "pending_design_interface_contract",
                    },
                },
            )

        async def fake_apply_workspace_quality_llm_repairs(
            *,
            run: FactoryRun,
            context: dict[str, Any],
            artifact_quality_errors: list[str],
            repair_attempt: int,
            interface_discrepancy_evidence: dict[str, Any] | None = None,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal llm_repair_calls
            del run, context, artifact_quality_errors, repair_attempt, interface_discrepancy_evidence
            llm_repair_calls += 1
            return [], {}

        def fake_apply_workspace_quality_cpp_post_repairs() -> list[dict[str, object]]:
            nonlocal post_repair_calls
            post_repair_calls += 1
            return []

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "run", "build"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_deterministic_repairs",
            fake_apply_workspace_quality_deterministic_repairs,
        )
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_llm_repairs",
            fake_apply_workspace_quality_llm_repairs,
        )
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_cpp_post_repairs",
            fake_apply_workspace_quality_cpp_post_repairs,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 1},
        )

        assert passed is False
        assert llm_repair_calls == 0
        assert post_repair_calls == 0
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["warnings"] == ["task_boundary_interface_discrepancy_required"]
        assert payload["repair"]["task_boundary_triage_required"] is True
        assert payload["repair"]["success_reason"] == "task_boundary_interface_discrepancy_required"
        assert payload["repair"]["plan_probe_preaudit"]["status"] == "coverage_matched_but_unplannable"
        assert payload["repair"]["interface_discrepancy_evidence"]["reason"] == ("coverage_matched_but_unplannable")
        assert payload["repair"]["rounds"][0]["task_boundary_triage_required"] is True
        assert payload["repair"]["rounds"][0]["repair_summary"]["stage"] == "runtime_plan_probe_unplannable"

    @pytest.mark.asyncio
    async def test_workspace_quality_routes_local_task_boundary_triage_to_director_repair(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-local-task-boundary-triage",
            config=FactoryConfig(name="quality-local-task-boundary-triage"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-28T00:00:00+00:00",
        )
        state = {"repaired": False}
        llm_repair_contexts: list[dict[str, Any]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            if state["repaired"]:
                return {
                    "command": command,
                    "exit_code": 0,
                    "passed": True,
                    "stdout_tail": "build passed",
                    "stderr_tail": "",
                    "error": "",
                }
            return {
                "command": command,
                "exit_code": 2,
                "passed": False,
                "stdout_tail": (
                    "src/main.ts(105,55): error TS2339: Property 'revenue' does not exist on type 'TransactionResult'."
                ),
                "stderr_tail": "",
                "error": "",
            }

        async def fake_apply_workspace_quality_deterministic_repairs(
            *,
            run: FactoryRun,
            artifact_quality_errors: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            assert run.id == "factory-quality-local-task-boundary-triage"
            assert repair_attempt == 1
            assert artifact_quality_errors
            return (
                [],
                {
                    "stage": "runtime_plan_probe_unplannable",
                    "attempted": True,
                    "success": False,
                    "success_reason": "task_boundary_interface_discrepancy_required",
                    "tool_results": 0,
                    "source_tools": [],
                    "task_id": "TASK-1",
                    "task_boundary_owner_evidence": {
                        "schema_version": "factory.workspace_quality_task_owner.v1",
                        "source": "task_runtime_execution_attempt",
                        "task_id": "TASK-1",
                        "owner_target_files": ["src/main.ts"],
                        "diagnostic_target_files": ["src/main.ts", "src/engine/rules.ts"],
                        "in_scope_diagnostic_target_files": ["src/main.ts"],
                        "out_of_scope_diagnostic_target_files": ["src/engine/rules.ts"],
                        "director_local_repair_allowed": True,
                    },
                    "plan_probe_preaudit": {
                        "status": "coverage_matched_but_unplannable",
                        "plannable_source_tools": [],
                        "covered_unplannable_source_tools": ["deterministic_typescript_missing_member_repair"],
                    },
                },
            )

        async def fake_apply_workspace_quality_llm_repairs(
            *,
            run: FactoryRun,
            context: dict[str, Any],
            artifact_quality_errors: list[str],
            repair_attempt: int,
            interface_discrepancy_evidence: dict[str, Any] | None = None,
            owner_target_files: list[str] | None = None,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            del run, context, artifact_quality_errors, repair_attempt
            assert interface_discrepancy_evidence is not None
            assert interface_discrepancy_evidence["recommended_owner"] == "director"
            assert interface_discrepancy_evidence["director_retry_allowed"] is True
            assert owner_target_files == ["src/main.ts"]
            llm_repair_contexts.append(interface_discrepancy_evidence)
            state["repaired"] = True
            return (
                [
                    {
                        "success": True,
                        "tool": "write_file",
                        "result": {
                            "file": "src/main.ts",
                            "operation": "update",
                            "before_sha256": "a" * 64,
                            "after_sha256": "b" * 64,
                        },
                    }
                ],
                {
                    "stage": "quality_repair",
                    "attempted": True,
                    "success": False,
                    "tool_results": 1,
                    "write_tool_evidence": True,
                    "source_tools": ["director_materialization_quality_repair"],
                    "interface_discrepancy_evidence": interface_discrepancy_evidence,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "run", "build"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_deterministic_repairs",
            fake_apply_workspace_quality_deterministic_repairs,
        )
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_llm_repairs",
            fake_apply_workspace_quality_llm_repairs,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 1},
        )

        assert passed is True
        assert len(llm_repair_contexts) == 1
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["passed"] is True
        assert payload["repair"]["success"] is True
        assert payload["repair"]["rounds"][0]["repair_summary"]["stage"] == "quality_repair"

    @pytest.mark.asyncio
    async def test_workspace_quality_replays_interface_probe_after_deterministic_no_commit_cache_hit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A cached deterministic no-commit must not erase Director retry authority."""

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-cached-interface-probe",
            config=FactoryConfig(name="quality-cached-interface-probe"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-22T00:00:00+00:00",
        )
        state = {"llm_calls": 0, "deterministic_calls": 0}
        interface_evidence: list[dict[str, Any]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            if state["llm_calls"] >= 2:
                return {
                    "command": command,
                    "exit_code": 0,
                    "passed": True,
                    "stdout_tail": "ok",
                    "stderr_tail": "",
                    "error": "",
                }
            return {
                "command": command,
                "exit_code": 1,
                "passed": False,
                "stdout_tail": (
                    "--- FAIL: TestStepAppliesGravity (0.00s)\n"
                    "    engine_test.go:69: velocity=-4.905 want 4.905\n"
                    "FAIL"
                ),
                "stderr_tail": "",
                "error": "",
            }

        async def fake_apply_workspace_quality_deterministic_repairs(
            *,
            run: FactoryRun,
            artifact_quality_errors: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            del artifact_quality_errors, repair_attempt
            assert run.id == "factory-quality-cached-interface-probe"
            state["deterministic_calls"] += 1
            return (
                [],
                {
                    "stage": "runtime_plan_probe_unplannable",
                    "attempted": True,
                    "success": False,
                    "success_reason": "task_boundary_interface_discrepancy_required",
                    "tool_results": 0,
                    "source_tools": [],
                    "task_id": "TASK-1",
                    "task_boundary_owner_evidence": {
                        "schema_version": "factory.workspace_quality_task_owner.v1",
                        "source": "task_runtime_execution_attempt",
                        "task_id": "TASK-1",
                        "owner_target_files": ["engine/engine.go"],
                        "diagnostic_target_files": ["engine/engine.go"],
                        "in_scope_diagnostic_target_files": ["engine/engine.go"],
                        "out_of_scope_diagnostic_target_files": [],
                        "director_local_repair_allowed": True,
                    },
                    "plan_probe_preaudit": {
                        "status": "coverage_matched_but_unplannable",
                        "plannable_source_tools": [],
                        "covered_unplannable_source_tools": [
                            "deterministic_go_test_assertion_align_repair"
                        ],
                        "covered_unplannable_diagnostic_count": 1,
                    },
                },
            )

        async def fake_apply_workspace_quality_llm_repairs(
            *,
            run: FactoryRun,
            context: dict[str, Any],
            artifact_quality_errors: list[str],
            repair_attempt: int,
            interface_discrepancy_evidence: dict[str, Any] | None = None,
            owner_target_files: list[str] | None = None,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            del run, context, artifact_quality_errors, repair_attempt
            assert interface_discrepancy_evidence is not None
            assert interface_discrepancy_evidence["recommended_owner"] == "director"
            assert interface_discrepancy_evidence["director_retry_allowed"] is True
            assert owner_target_files == ["engine/engine.go"]
            interface_evidence.append(interface_discrepancy_evidence)
            state["llm_calls"] += 1
            return (
                [
                    {
                        "success": True,
                        "tool": "edit_file",
                        "result": {
                            "file": "engine/engine.go",
                            "operation": "update",
                            "before_sha256": f"{state['llm_calls']:064x}",
                            "after_sha256": f"{state['llm_calls'] + 1:064x}",
                        },
                    }
                ],
                {
                    "stage": "quality_repair",
                    "attempted": True,
                    "success": False,
                    "tool_results": 1,
                    "write_tool_evidence": True,
                    "source_tools": ["director_materialization_quality_repair"],
                    "interface_discrepancy_evidence": interface_discrepancy_evidence,
                },
            )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["go", "test", "./..."]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda context: None)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_deterministic_repairs",
            fake_apply_workspace_quality_deterministic_repairs,
        )
        monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", fake_apply_workspace_quality_llm_repairs)

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 2},
        )

        assert passed is True
        assert state["deterministic_calls"] == 1
        assert state["llm_calls"] == 2
        assert len(interface_evidence) == 2
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["repair"]["rounds"][1]["repair_summary"]["stage"] == "quality_repair"

    def test_workspace_quality_keeps_claimed_rust_diagnostic_owner_on_director(self) -> None:
        diagnostic = "cargo check :: error[E0432]: unresolved import `crate::engine::RecipeDraft` in src/lib.rs"
        owner_evidence = {
            "schema_version": "factory.workspace_quality_task_owner.v1",
            "source": "task_runtime_execution_attempt",
            "task_id": "TASK-1",
            "owner_target_files": ["Cargo.toml", "src/lib.rs", "src/models/mod.rs"],
            "diagnostic_target_files": ["src/lib.rs", "src/engine/flavor_rules.rs"],
            "in_scope_diagnostic_target_files": ["src/lib.rs"],
            "out_of_scope_diagnostic_target_files": ["src/engine/flavor_rules.rs"],
            "director_local_repair_allowed": True,
        }
        summary = {
            "task_id": "TASK-1",
            "task_boundary_owner_evidence": owner_evidence,
            "plan_probe_preaudit": {
                "status": "coverage_matched_but_unplannable",
                "plannable_source_tools": [],
                "covered_unplannable_source_tools": ["deterministic_rust_line_suggestion_repair"],
                "covered_unplannable_diagnostic_count": 1,
            },
        }

        evidence = OrchestrationStageExecutor._workspace_quality_interface_discrepancy_evidence(
            summary,
            [diagnostic],
        )
        assert evidence["recommended_owner"] == "director"
        assert evidence["recommended_route"] == "director_retry_with_interface_discrepancy_context"
        assert evidence["cross_artifact_route"] == "director_repair_within_claimed_task"
        assert evidence["director_retry_allowed"] is True
        assert evidence["llm_fallback_blocked"] is False
        assert evidence["metadata"]["task_boundary_owner_evidence"] == owner_evidence
        assert OrchestrationStageExecutor._workspace_quality_claimed_owner_repair_targets(evidence) == ["src/lib.rs"]

        # A claimed task must never authorize a diagnostic path it does not own.
        out_of_scope = dict(owner_evidence)
        out_of_scope["diagnostic_target_files"] = ["src/engine/flavor_rules.rs"]
        out_of_scope["in_scope_diagnostic_target_files"] = []
        out_of_scope["out_of_scope_diagnostic_target_files"] = ["src/engine/flavor_rules.rs"]
        out_of_scope["director_local_repair_allowed"] = False
        summary["task_boundary_owner_evidence"] = out_of_scope
        blocked = OrchestrationStageExecutor._workspace_quality_interface_discrepancy_evidence(
            summary,
            [diagnostic],
        )
        assert blocked["recommended_owner"] == "chief_engineer"
        assert blocked["director_retry_allowed"] is False

    @pytest.mark.asyncio
    async def test_workspace_quality_reruns_prepare_after_successful_repair(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="factory-quality-prepare-after-repair",
            config=FactoryConfig(name="quality-prepare-after-repair"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        state = {"repaired": False, "prepared_after_repair": False}
        phases_seen: list[str] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            is_prepare = command == ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"]
            if is_prepare and state["repaired"]:
                state["prepared_after_repair"] = True
            if is_prepare:
                return {
                    "command": command,
                    "exit_code": 0,
                    "passed": True,
                    "stdout_tail": "installed",
                    "stderr_tail": "",
                    "error": "",
                }
            if not state["repaired"]:
                return {
                    "command": command,
                    "exit_code": 2,
                    "passed": False,
                    "stdout_tail": "src/index.ts(1,10): error TS2305: missing export",
                    "stderr_tail": "",
                    "error": "",
                }
            return {
                "command": command,
                "exit_code": 0 if state["prepared_after_repair"] else 1,
                "passed": bool(state["prepared_after_repair"]),
                "stdout_tail": "build passed" if state["prepared_after_repair"] else "",
                "stderr_tail": "" if state["prepared_after_repair"] else "missing dependency",
                "error": "" if state["prepared_after_repair"] else "missing dependency",
            }

        async def fake_apply_workspace_quality_deterministic_repairs(
            *,
            run: FactoryRun,
            artifact_quality_errors: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            assert run.id == "factory-quality-prepare-after-repair"
            assert repair_attempt == 1
            assert artifact_quality_errors
            state["repaired"] = True
            return (
                [
                    {
                        "tool": "write_file",
                        "success": True,
                        "result": {
                            "source_tool": "deterministic_typescript_missing_export_repair",
                            "file": "src/index.ts",
                            "operation": "modify",
                        },
                    }
                ],
                {
                    "attempted": True,
                    "success": True,
                    "source_tools": ["deterministic_typescript_missing_export_repair"],
                    "tool_results": 1,
                    "write_tool_evidence": True,
                },
            )

        def record_phases(payload: dict[str, Any]) -> None:
            phases_seen.append(str(payload.get("phase") or ""))

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "run", "build"]])
        monkeypatch.setattr(
            executor,
            "_workspace_quality_prepare_commands",
            lambda commands, context: [["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"]],
        )
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)
        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_deterministic_repairs",
            fake_apply_workspace_quality_deterministic_repairs,
        )

        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {"workspace_quality_repair_max_rounds": 1},
        )

        assert passed is True
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        for command_result in payload["commands"]:
            record_phases(command_result)
        assert phases_seen == ["prepare", "check", "prepare_after_repair", "check_after_repair"]
        assert payload["passed"] is True

    @pytest.mark.asyncio
    async def test_unplannable_cross_file_typescript_missing_export_routes_to_task_boundary_triage(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        model_dir = tmp_path / "src" / "models"
        model_dir.mkdir(parents=True)
        (tmp_path / "src" / "index.ts").write_text(
            "import { MoonPhaseModel } from './models/moonphase';\n"
            "export class Garden {\n"
            "  private moon = new MoonPhaseModel();\n"
            "  public snapshot(): unknown {\n"
            "    return this.moon.getState();\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        (model_dir / "moonphase.ts").write_text(
            "export enum MoonPhase {\n  New,\n  Full,\n}\n",
            encoding="utf-8",
        )
        run = FactoryRun(
            id="factory-quality-multiround-repair",
            config=FactoryConfig(name="quality-multiround-repair"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        calls: list[list[str]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            calls.append(command)
            model_text = (model_dir / "moonphase.ts").read_text(encoding="utf-8")
            if "export class MoonPhaseModel" not in model_text:
                return {
                    "command": command,
                    "exit_code": 2,
                    "passed": False,
                    "stdout_tail": (
                        "src/index.ts(1,10): error TS2305: Module '\"./models/moonphase\"' "
                        "has no exported member 'MoonPhaseModel'."
                    ),
                    "stderr_tail": "",
                    "error": "",
                }
            if "getState(" not in model_text:
                return {
                    "command": command,
                    "exit_code": 2,
                    "passed": False,
                    "stdout_tail": (
                        "src/index.ts(5,22): error TS2339: Property 'getState' does not exist on type 'MoonPhaseModel'."
                    ),
                    "stderr_tail": "",
                    "error": "",
                }
            return {
                "command": command,
                "exit_code": 0,
                "passed": True,
                "stdout_tail": "build passed",
                "stderr_tail": "",
                "error": "",
            }

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "run", "build"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)

        async def fake_apply_workspace_quality_deterministic_repairs(
            *,
            run: FactoryRun,
            artifact_quality_errors: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            del repair_attempt
            return executor._apply_workspace_quality_repairs(
                run_id=run.id,
                artifact_quality_errors=artifact_quality_errors,
            )

        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_deterministic_repairs",
            fake_apply_workspace_quality_deterministic_repairs,
        )

        passed, artifact = await executor._run_workspace_quality_checks(run, {})

        assert passed is False
        assert calls == [["npm", "run", "build"]]
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["passed"] is False
        assert payload["warnings"] == ["task_boundary_interface_discrepancy_required"]
        assert [item["phase"] for item in payload["commands"]] == ["check"]
        repair = payload["repair"]
        assert repair["task_boundary_triage_required"] is True
        assert repair["success_reason"] == "task_boundary_interface_discrepancy_required"
        assert repair["plan_probe_preaudit"]["status"] == "coverage_matched_but_unplannable"
        assert repair["plan_probe_preaudit"]["covered_unplannable_diagnostic_count"] == 2
        assert repair["write_tool_evidence"] is False
        assert repair["tool_results"] == 0

    @pytest.mark.asyncio
    async def test_typescript_enum_repair_requires_canonical_director_execution_before_rerun(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        model_dir = tmp_path / "src" / "models"
        model_dir.mkdir(parents=True)
        moonphase = model_dir / "moonphase.ts"
        moonphase.write_text(
            "\n".join(
                [
                    "export enum MoonPhase {",
                    "  New,",
                    "  Full,",
                    "  WaningCrescent;",
                    "}",
                    "",
                    "export interface MoonState {",
                    "  phase: MoonPhase;",
                    "}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        run = FactoryRun(
            id="factory-enum-repair",
            config=FactoryConfig(name="enum-repair"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        calls: list[list[str]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            calls.append(command)
            repaired_source = moonphase.read_text(encoding="utf-8")
            repaired = "  WaningCrescent," in repaired_source and "  phase: MoonPhase;" in repaired_source
            if repaired:
                return {
                    "command": command,
                    "exit_code": 0,
                    "passed": True,
                    "stdout_tail": "build passed",
                    "stderr_tail": "",
                    "error": "",
                }
            return {
                "command": command,
                "exit_code": 2,
                "passed": False,
                "stdout_tail": (
                    "src/models/moonphase.ts(4,18): error TS1357: "
                    "An enum member name must be followed by a ',', '=', or '}'."
                ),
                "stderr_tail": "",
                "error": "",
            }

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "run", "build"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)

        passed, artifact = await executor._run_workspace_quality_checks(run, {})

        assert passed is False
        assert calls == [["npm", "run", "build"]]
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["passed"] is False
        assert [item["phase"] for item in payload["commands"]] == ["check"]
        assert payload["repair"]["write_tool_evidence"] is False
        assert payload["repair"]["tool_results"] == 0
        assert "  WaningCrescent;" in moonphase.read_text(encoding="utf-8")
        assert "  phase: MoonPhase;" in moonphase.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_typescript_identifier_repair_requires_canonical_director_execution_before_rerun(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        engine_dir = tmp_path / "src" / "engine"
        engine_dir.mkdir(parents=True)
        simulation = engine_dir / "simulation.ts"
        simulation.write_text(
            "\n".join(
                [
                    "export interface GardenState { moonPhase: number; humidity: number; tick: number; }",
                    "",
                    "export function tickGarden(state: GardenState): GardenState {",
                    "  const newState = { ...state, tick: state.tick + 1 };",
                    "  return newState;",
                    "}",
                    "",
                    "export function getGardenSummary(state: GardenState): string {",
                    "  return [",
                    "    `${newState.moonPhase}`;",
                    "    `${newState.humidity}`;",
                    "    `${newState.tick}`;",
                    "  ].join('\\n');",
                    "}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        run = FactoryRun(
            id="factory-unresolved-identifier-repair",
            config=FactoryConfig(name="unresolved-identifier-repair"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        calls: list[list[str]] = []

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            calls.append(command)
            repaired_source = simulation.read_text(encoding="utf-8")
            repaired = (
                "`${state.moonPhase}`;" in repaired_source
                and "`${state.humidity}`;" in repaired_source
                and "`${state.tick}`;" in repaired_source
                and "const newState = { ...state" in repaired_source
            )
            if repaired:
                return {
                    "command": command,
                    "exit_code": 0,
                    "passed": True,
                    "stdout_tail": "build passed",
                    "stderr_tail": "",
                    "error": "",
                }
            return {
                "command": command,
                "exit_code": 2,
                "passed": False,
                "stdout_tail": (
                    "src/engine/simulation.ts(10,8): error TS2304: Cannot find name 'newState'.\n"
                    "src/engine/simulation.ts(11,8): error TS2304: Cannot find name 'newState'.\n"
                    "src/engine/simulation.ts(12,8): error TS2304: Cannot find name 'newState'."
                ),
                "stderr_tail": "",
                "error": "",
            }

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda context: [["npm", "run", "build"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda commands, context: [])
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)

        passed, artifact = await executor._run_workspace_quality_checks(run, {})

        assert passed is False
        assert calls == [["npm", "run", "build"]]
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["passed"] is False
        assert [item["phase"] for item in payload["commands"]] == ["check"]
        assert payload["repair"]["write_tool_evidence"] is False
        assert payload["repair"]["tool_results"] == 0
        repaired_source = simulation.read_text(encoding="utf-8")
        assert "return newState;" in repaired_source
        assert "`${newState.moonPhase}`;" in repaired_source
        assert "`${newState.humidity}`;" in repaired_source
        assert "`${newState.tick}`;" in repaired_source


# ---------------------------------------------------------------------------
# Director-evidence truth tables
# ---------------------------------------------------------------------------


def test_latest_task_boundary_scope_filter_lifts_from_repair_rounds() -> None:
    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
        workspace_quality_latest_task_boundary_scope_filter,
    )

    scope = {
        "schema_version": "director.task_boundary.repair_scope_filter.v1",
        "ownership_handoff_requests": [{"target_file": "src/models/mood.py", "recommended_route": "owner_task_retry"}],
    }
    lifted = workspace_quality_latest_task_boundary_scope_filter(
        {
            "rounds": [
                {"repair_summary": {"task_boundary_scope_filter": {"ownership_handoff_requests": []}}},
                {"repair_summary": {"task_boundary_scope_filter": scope}},
            ]
        }
    )

    assert lifted == scope


def test_deferred_owner_targets_drop_runtime_and_dotfile_noise() -> None:
    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
        workspace_quality_deferred_owner_targets,
    )

    targets = workspace_quality_deferred_owner_targets(
        {
            "stage": "task_boundary_repair_targets_deferred",
            "task_boundary_scope_filter": {
                "out_of_scope_repair_target_files": [
                    ".catalog_meta.json",
                    ".polaris.kernelone.tags.cache.v1/b40656d3e6561377.json",
                    "readme.md",
                    "runtime/signals/pm_planning.pm.signals.json",
                    "src/models/moon.hpp",
                    "src/models/stamp.hpp",
                ]
            },
        }
    )

    assert targets == ["readme.md", "src/models/moon.hpp", "src/models/stamp.hpp"]


def test_residual_owner_handoff_targets_require_explicit_owner_and_live_diagnostic() -> None:
    from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
        workspace_quality_residual_owner_handoff_targets,
    )

    targets = workspace_quality_residual_owner_handoff_targets(
        {
            "stage": "quality_repair",
            "task_boundary_scope_filter": {
                "owner_task_retry_handoff_requests": [
                    {
                        "target_file": "physics/gravity_test.go",
                        "owner_found": True,
                        "status": "owner_found",
                        "recommended_route": "owner_task_retry",
                    },
                    {
                        "target_file": "note/frequency.go",
                        "owner_found": True,
                        "status": "owner_found",
                        "recommended_route": "owner_task_retry",
                    },
                    {
                        "target_file": "engine/sandbox.go",
                        "owner_found": False,
                        "status": "owner_unknown",
                        "recommended_route": "scope_authority_resolution",
                    },
                ]
            },
        },
        ["physics/gravity_test.go:22:8: scene.Add undefined"],
    )

    assert targets == ["physics/gravity_test.go"]
