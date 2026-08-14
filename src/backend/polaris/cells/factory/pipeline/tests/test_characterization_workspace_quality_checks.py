"""Characterization tests for the workspace-quality repair loop (kept whole: intra-class test-order coupling)."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import hashlib
import inspect
import json
import logging
import os
import shutil
import sys
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    BlueprintPersistence,
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerPortfolioTaskV1,
    GenerateTaskBlueprintCommandV1,
    VerificationCommandAuthorityV1,
    build_chief_engineer_blueprint_portfolio,
    derive_project_kind_authority_from_catalog_snapshot,
    generate_task_blueprint,
    project_chief_engineer_task_blueprint,
    project_completion_catalog_snapshot_hash,
    project_completion_verifier_policy_snapshot_hash,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    TaskBlueprintResultV1,
    _issue_chief_engineer_portfolio_authority_carrier,
)
from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.events.fact_stream.public.service import (
    QueryFactEventsV1,
    query_fact_events,
)
from polaris.cells.factory.pipeline.internal import (
    factory_stage_executor as stage_executor_module,
    factory_workspace_quality as workspace_quality_module,
)
from polaris.cells.factory.pipeline.internal.factory_deadline_policy import (
    FactoryDeadlineBudgetPolicyV1,
    FactoryDeadlineDispositionV1,
    build_task_dependency_schedule,
)
from polaris.cells.factory.pipeline.internal.factory_role_evidence_authority import (
    FactoryRoleEvidenceAuthorityPort,
)
from polaris.cells.factory.pipeline.internal.factory_run_completion import RunCompletionWaiter
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    CommandResult,
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    OrchestrationStageExecutor,
)
from polaris.cells.factory.pipeline.internal.factory_settlement_consumer import _fencing_token
from polaris.cells.factory.pipeline.internal.factory_stage_helpers import (
    evaluate_canonical_factory_authority,
)
from polaris.cells.factory.pipeline.internal.run_ledger import load_run_ledger_projection
from polaris.cells.roles.adapters.public import (
    build_director_materialization_quality_repair_message,
    extract_workspace_quality_summary,
    resolve_director_semantic_quality_repair_target_files,
)
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
    FactoryRoleEvidenceAuthorityBindingV1,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    ObservableTaskRowsProjectionV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptHeartbeatVerdictV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.storage import resolve_logical_path


from polaris.cells.factory.pipeline.tests._characterization_helpers import (  # noqa: F401
    _executor,
    _with_task_runtime_authority,
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
        (tmp_path / ".polaris").mkdir(parents=True)
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
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            assert run.id == "factory-quality-llm-repair"
            assert context["workspace_quality_repair_max_rounds"] == 1
            assert artifact_quality_errors
            assert repair_attempt == 1
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
            == "regression"
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
            del kwargs
            llm_calls += 1
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
        assert deterministic_calls == 2
        assert llm_calls == 2
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        repair = payload["repair"]
        assert repair["revalidated"] is False
        assert repair["consecutive_stagnant_rounds"] == 2
        assert repair["convergence_stop_reason"] == "two_consecutive_no_mutation_repairs"
        assert [item["verifier_effect"] for item in repair["rounds"]] == ["no_op", "no_op"]
        assert all(item["write_tool_evidence"] is False for item in repair["rounds"])

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
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            nonlocal llm_repair_calls
            assert run.id == "factory-quality-deterministic-no-write"
            assert context["workspace_quality_repair_max_rounds"] == 1
            assert artifact_quality_errors
            assert repair_attempt == 1
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
            "repair_write:tool=director_materialization_quality_repair;file=tests/run-tests.js;operation=create"
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
        ) -> tuple[list[dict[str, object]], dict[str, object]]:
            del run, context, artifact_quality_errors, repair_attempt
            assert interface_discrepancy_evidence is not None
            assert interface_discrepancy_evidence["recommended_owner"] == "director"
            assert interface_discrepancy_evidence["director_retry_allowed"] is True
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
