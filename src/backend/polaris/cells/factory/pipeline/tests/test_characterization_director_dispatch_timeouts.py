"""Characterization tests for the Director dispatch loop (part 2): timeouts + settlement barriers."""

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
    _authoritative_task_projection,
    _factory_stage_context,
    _with_task_runtime_authority,
    _write_handoff_ready_review_for_tasks,
)


class TestDirectorDispatchLoop:
    @pytest.mark.asyncio
    async def test_single_binding_materialization_failure_stops_before_no_claim_retry(
        self,
        tmp_path: Path,
    ) -> None:
        class _SingleBindingQualityHandoffExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.stats = [
                    {
                        "total": 3,
                        "pending": 1,
                        "ready": 1,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 2,
                    },
                    {
                        "total": 3,
                        "pending": 1,
                        "ready": 1,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 2,
                    },
                    {
                        "total": 3,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 1,
                        "blocked": 2,
                    },
                    {
                        "total": 3,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 1,
                        "blocked": 2,
                    },
                ]
                self.execute_calls = 0

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            def _read_claimable_director_task_ids(self, *, limit: int, factory_run_id: str = "") -> list[str]:
                del limit, factory_run_id
                return ["TASK-1"] if self.execute_calls == 0 else []

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                executor = self

                class _Service:
                    async def execute_director_run(self, **kwargs: object) -> CommandResult:
                        del kwargs
                        executor.execute_calls += 1
                        return CommandResult(run_id="director-quality-single", status="running", message="submitted")

                return _Service()

            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, timeout_seconds, cancel_event, abort_checker, cancel_on_timeout
                return CommandResult(
                    run_id=initial_result.run_id,
                    status="failed",
                    message=(
                        "Run status: failed | failed_task=task-0-director "
                        "| error=director_materialization_quality_failed"
                    ),
                    metadata={},
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()
        (tmp_path / "package.json").write_text(
            '{"scripts":{"build":"tsc"},"devDependencies":{"typescript":"latest"}}',
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")
        (tmp_path / "src" / "engine.ts").write_text("export const engine = true;\n", encoding="utf-8")
        (tmp_path / "tests" / "verify.test.ts").write_text("import '../src/index';\n", encoding="utf-8")

        executor = _SingleBindingQualityHandoffExecutor(tmp_path)
        tasks = [
            {"id": "TASK-1", "target_files": ["package.json", "src/index.ts"]},
            {"id": "TASK-2", "target_files": ["src/engine.ts"], "depends_on": ["TASK-1"]},
            {"id": "TASK-3", "target_files": ["tests/verify.test.ts"], "depends_on": ["TASK-2"]},
        ]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        executor._write_json_artifact(
            "tasks/task_1.json",
            {
                "id": "TASK-1",
                "status": "failed",
                "metadata": {
                    "last_execution_error": "director_materialization_quality_failed",
                    "adapter_result": {
                        "materialization_error": "director_materialization_quality_failed",
                        "materialization_mode": "write_tool_and_workspace_diff",
                    },
                },
            },
        )
        run = FactoryRun(
            id="factory-single-quality-handoff",
            config=FactoryConfig(name="quality-handoff"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-23T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 3, "timeout": 120, "execution_mode": "serial", "max_workers": 1}
            ),
        )

        assert result.status == "failed"
        assert executor.execute_calls == 1
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["attempts"]
        assert payload["attempts"][0]["run_id"] == "director-quality-single"
        assert payload["quality_gate_handoff"] is False
        assert payload["failure_stage"] == "director_dispatch"
        assert payload["error_code"] == "director.canonical_task_boundary_missing"
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.materialization_quality_handoff_ready" not in codes
        assert "director.materialization_quality_handoff" not in codes
        assert "director.canonical_task_boundary_missing" in codes

    @pytest.mark.asyncio
    async def test_idle_blocked_materialization_quality_failure_with_missing_targets_stays_failed(
        self,
        tmp_path: Path,
    ) -> None:
        class _BlockedQualityFailureExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.stats = [
                    {
                        "total": 2,
                        "pending": 1,
                        "ready": 1,
                        "in_progress": 0,
                        "in_design": 0,
                        "in_execution": 0,
                        "in_qa": 0,
                        "waiting_human": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 1,
                    },
                    {
                        "total": 2,
                        "pending": 1,
                        "ready": 1,
                        "in_progress": 0,
                        "in_design": 0,
                        "in_execution": 0,
                        "in_qa": 0,
                        "waiting_human": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 1,
                    },
                    {
                        "total": 2,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "in_design": 0,
                        "in_execution": 0,
                        "in_qa": 0,
                        "waiting_human": 0,
                        "completed": 0,
                        "failed": 1,
                        "blocked": 1,
                    },
                    {
                        "total": 2,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "in_design": 0,
                        "in_execution": 0,
                        "in_qa": 0,
                        "waiting_human": 0,
                        "completed": 0,
                        "failed": 1,
                        "blocked": 1,
                    },
                ]
                self.execute_calls = 0

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            def _read_claimable_director_task_ids(self, *, limit: int, factory_run_id: str = "") -> list[str]:
                del limit, factory_run_id
                return ["TASK-1"] if self.execute_calls == 0 else []

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                executor = self

                class _Service:
                    async def execute_director_run(self, **kwargs: object) -> CommandResult:
                        del kwargs
                        executor.execute_calls += 1
                        return CommandResult(run_id="director-blocked-quality", status="running", message="submitted")

                return _Service()

            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, timeout_seconds, cancel_event, abort_checker, cancel_on_timeout
                return CommandResult(
                    run_id=initial_result.run_id,
                    status="failed",
                    message=(
                        "Run status: failed | failed_task=task-0-director "
                        "| error=director_materialization_quality_failed"
                    ),
                    metadata={
                        "failed_task_count": 1,
                        "failed_tasks": [
                            {
                                "task_id": "task-0-director",
                                "error_message": "director_materialization_quality_failed",
                            }
                        ],
                    },
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        (tmp_path / "src").mkdir()
        (tmp_path / "package.json").write_text(
            '{"scripts":{"build":"tsc"},"devDependencies":{"typescript":"latest"}}',
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")

        executor = _BlockedQualityFailureExecutor(tmp_path)
        tasks = [
            {"id": "TASK-1", "target_files": ["package.json", "src/index.ts"]},
            {"id": "TASK-2", "target_files": ["tests/verify.test.ts"], "depends_on": ["TASK-1"]},
        ]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-blocked-quality-handoff",
            config=FactoryConfig(name="blocked-quality-handoff"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-23T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 1, "timeout": 120, "execution_mode": "serial", "max_workers": 1}
            ),
        )

        assert result.status == "failed"
        assert executor.execute_calls == 1
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["quality_gate_handoff"] is False
        assert payload["failure_stage"] == "director_dispatch"
        assert payload["taskboard"]["converged"] is False
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.materialization_quality_handoff_ready" not in codes
        assert "director.materialization_quality_handoff" not in codes
        assert "director.canonical_task_boundary_missing" in codes

    @pytest.mark.asyncio
    async def test_no_claimable_tasks_after_attempt_does_not_replay_requested_pm_tasks(self, tmp_path: Path) -> None:
        class _NoClaimableAfterProgressExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.stats = [
                    {
                        "total": 2,
                        "pending": 1,
                        "ready": 1,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 1,
                    },
                    {
                        "total": 2,
                        "pending": 1,
                        "ready": 1,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 1,
                    },
                    {
                        "total": 2,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 1,
                        "failed": 0,
                        "blocked": 1,
                    },
                    {
                        "total": 2,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 1,
                        "failed": 0,
                        "blocked": 1,
                    },
                ]
                self.execute_calls = 0
                self.captured_tasks: list[list[str]] = []

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            def _read_claimable_director_task_ids(self, *, limit: int, factory_run_id: str = "") -> list[str]:
                del limit, factory_run_id
                return ["TASK-1"] if self.execute_calls == 0 else []

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                executor = self

                class _Service:
                    async def execute_director_run(self, **kwargs: object) -> CommandResult:
                        tasks = kwargs.get("tasks")
                        if isinstance(tasks, list):
                            executor.captured_tasks.append([str(item) for item in tasks])
                        executor.execute_calls += 1
                        return CommandResult(
                            run_id=f"director-{executor.execute_calls}", status="running", message="ok"
                        )

                return _Service()

            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, timeout_seconds, cancel_event, abort_checker, cancel_on_timeout
                return CommandResult(
                    run_id=initial_result.run_id,
                    status="completed",
                    message="Run status: completed",
                    metadata={"task_status_counts": {"completed": 1}},
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _NoClaimableAfterProgressExecutor(tmp_path)
        tasks = [
            {"id": "TASK-1", "target_files": ["src/one.rs"]},
            {"id": "TASK-2", "target_files": ["src/two.rs"], "depends_on": ["TASK-1"]},
        ]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-no-claimable-after-progress",
            config=FactoryConfig(name="no-claimable-after-progress"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-23T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 3, "timeout": 120, "execution_mode": "serial", "max_workers": 1}
            ),
        )

        assert result.status == "failed"
        assert executor.execute_calls == 1
        assert executor.captured_tasks == [["TASK-1"]]
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.no_claimable_tasks_after_progress" in codes
        assert "director.taskboard_not_converged" in codes
        assert "director.run_status_non_success" not in codes

    @pytest.mark.asyncio
    async def test_no_claimable_followup_settlement_consumes_previous_execution_lease(
        self,
        tmp_path: Path,
    ) -> None:
        """An active child keeps the unused prior execution lease, not only 5s settle."""

        class _ActiveChildAfterLifecycleFailureExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.stats = [
                    {
                        "total": 1,
                        "pending": 1,
                        "ready": 1,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 1,
                        "pending": 1,
                        "ready": 1,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 1,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 1,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 1,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 1,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 1,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 1,
                        "failed": 0,
                        "blocked": 0,
                    },
                ]
                self.execute_calls = 0
                self.settlement_grace_seconds: list[int] = []

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            def _read_claimable_director_task_ids(self, *, limit: int, factory_run_id: str = "") -> list[str]:
                del limit, factory_run_id
                return ["TASK-1"] if self.execute_calls == 0 else []

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                executor = self

                class _Service:
                    async def execute_director_run(self, **_kwargs: object) -> CommandResult:
                        executor.execute_calls += 1
                        return CommandResult(run_id="director-active-child", status="running", message="submitted")

                return _Service()

            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, timeout_seconds, cancel_event, abort_checker, cancel_on_timeout
                return CommandResult(
                    run_id=initial_result.run_id,
                    status="failed",
                    message="orchestration lifecycle ended before TaskRuntime child",
                )

            async def _settle_inflight_director_run_after_timeout(
                self,
                service: Any,
                *,
                run_id: str,
                grace_seconds: int,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
            ) -> CommandResult:
                del service, cancel_event, abort_checker
                self.settlement_grace_seconds.append(grace_seconds)
                return CommandResult(
                    run_id=run_id,
                    status="completed",
                    message="TaskRuntime child settled inside carried execution lease",
                    metadata={"canonical_authoritative": True},
                )

            def _active_director_execution_progress_marker(
                self,
                *,
                run_id: str,
            ) -> tuple[tuple[str, str, str, str], ...]:
                assert run_id == "director-active-child"
                return (("TASK-1", "7", "heartbeat-7", "in_progress"),)

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _ActiveChildAfterLifecycleFailureExecutor(tmp_path)
        tasks = [{"id": "TASK-1", "target_files": ["src/main.rs"]}]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-carry-previous-director-lease",
            config=FactoryConfig(name="carry-previous-director-lease"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-07-27T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {
                    "director_max_rounds": 2,
                    "director_dispatch_timeout_seconds": 60,
                    "director_first_materialization_min_budget_seconds": 10,
                    "director_timeout_settle_grace_seconds": 5,
                    "execution_mode": "serial",
                    "max_workers": 1,
                }
            ),
        )

        assert executor.execute_calls == 1
        assert len(executor.settlement_grace_seconds) == 1
        assert 55 <= executor.settlement_grace_seconds[0] <= 60

    def test_taskboard_active_execution_is_authoritative_when_lifecycle_marker_lags(self) -> None:
        assert OrchestrationStageExecutor._taskboard_has_active_execution(
            {"in_progress": 1, "completed": 2, "blocked": 1}
        )
        assert OrchestrationStageExecutor._taskboard_has_active_execution({"in_execution": 1, "completed": 2})
        assert not OrchestrationStageExecutor._taskboard_has_active_execution(
            {"in_progress": 0, "in_execution": 0, "completed": 3, "blocked": 1}
        )

    @pytest.mark.asyncio
    async def test_missing_write_receipt_with_artifacts_stays_failed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _MissingWriteReceiptHandoffExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.stats = [
                    {
                        "total": 3,
                        "pending": 3,
                        "ready": 3,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 3,
                        "pending": 3,
                        "ready": 3,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 3,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 3,
                        "blocked": 0,
                    },
                    {
                        "total": 3,
                        "pending": 0,
                        "ready": 0,
                        "in_progress": 0,
                        "completed": 0,
                        "failed": 3,
                        "blocked": 0,
                    },
                ]

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                return object()

            def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
                del context
                return [
                    {"provider_id": "p1", "model": "qwen-q6-a", "binding_id": "b1"},
                    {"provider_id": "p2", "model": "qwen-q6-b", "binding_id": "b2"},
                ]

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(
                    run_id="director-receipt-failed",
                    status="failed",
                    message="Director binding fanout: 2 bindings, 0 succeeded, 2 failed",
                    metadata={
                        "binding_fanout": True,
                        "active_binding_count": 2,
                        "per_binding": [
                            {
                                "provider_id": "p1",
                                "model": "qwen-q6-a",
                                "binding_id": "b1",
                                "run_id": "r1",
                                "status": "failed",
                                "message": "Run status: failed | failed_task=task-1",
                            },
                            {
                                "provider_id": "p2",
                                "model": "qwen-q6-b",
                                "binding_id": "b2",
                                "run_id": "r2",
                                "status": "failed",
                                "message": "Run status: failed | failed_task=task-2",
                            },
                        ],
                    },
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        (tmp_path / "src").mkdir()
        (tmp_path / "package.json").write_text(
            '{"scripts":{"build":"tsc"},"devDependencies":{"typescript":"latest"}}',
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")

        executor = _MissingWriteReceiptHandoffExecutor(tmp_path)
        monkeypatch.setattr(
            TaskRuntimeService,
            "query_observable_task_rows_projection",
            lambda runtime: _authoritative_task_projection(
                Path(runtime.workspace),
                (
                    {
                        "id": 1,
                        "status": "pending",
                        "metadata": {"external_task_id": "TASK-1"},
                    },
                ),
            ),
        )
        tasks = [
            {
                "id": "TASK-1",
                "target_files": ["package.json", "src/index.ts"],
            }
        ]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        executor._write_json_artifact(
            "tasks/task_1.json",
            {
                "id": "TASK-1",
                "status": "failed",
                "last_execution_error": "director_missing_write_receipt",
                "metadata": {
                    "adapter_result": {
                        "materialization_mode": "workspace_diff_without_write_tool",
                        "new_files": ["package.json", "src/index.ts"],
                    }
                },
            },
        )
        executor._write_json_artifact(
            "tasks/task_2.json",
            {
                "id": "TASK-2",
                "status": "failed",
                "metadata": {
                    "adapter_result": {
                        "materialization_mode": "no_materialized_changes",
                        "materialization_error": "director_no_materialized_changes",
                    }
                },
            },
        )
        run = FactoryRun(
            id="factory-receipt-handoff",
            config=FactoryConfig(name="receipt-handoff"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-23T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(
            executor,
            run_id=run.id,
            tasks=[
                *tasks,
                {
                    "id": "TASK-2",
                    "target_files": ["package.json", "src/index.ts"],
                },
            ],
        )

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 1, "timeout": 120, "execution_mode": "parallel", "max_workers": 2}
            ),
        )

        assert result.status == "failed"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["quality_gate_handoff"] is False
        assert payload["failure_stage"] == "director_dispatch"
        assert payload["error_code"] == "director.canonical_task_boundary_missing"
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.materialization_quality_handoff" not in codes
        assert "director.canonical_task_boundary_missing" in codes

    @pytest.mark.asyncio
    async def test_idle_claimable_unresolved_artifacts_do_not_enter_quality_gate_handoff(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        class _IdleUnresolvedHandoffExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.stats = [
                    {
                        "total": 4,
                        "pending": 4,
                        "ready": 4,
                        "in_progress": 0,
                        "in_design": 0,
                        "in_execution": 0,
                        "in_qa": 0,
                        "running": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 4,
                        "pending": 4,
                        "ready": 4,
                        "in_progress": 0,
                        "in_design": 0,
                        "in_execution": 0,
                        "in_qa": 0,
                        "running": 0,
                        "completed": 0,
                        "failed": 0,
                        "blocked": 0,
                    },
                    {
                        "total": 4,
                        "pending": 1,
                        "ready": 1,
                        "in_progress": 0,
                        "in_design": 0,
                        "in_execution": 0,
                        "in_qa": 0,
                        "running": 0,
                        "completed": 1,
                        "failed": 2,
                        "blocked": 0,
                    },
                    {
                        "total": 4,
                        "pending": 1,
                        "ready": 1,
                        "in_progress": 0,
                        "in_design": 0,
                        "in_execution": 0,
                        "in_qa": 0,
                        "running": 0,
                        "completed": 1,
                        "failed": 2,
                        "blocked": 0,
                    },
                ]

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                return object()

            def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
                del context
                return [
                    {"provider_id": "p1", "model": "qwen-q6-a", "binding_id": "b1"},
                    {"provider_id": "p2", "model": "qwen-q6-b", "binding_id": "b2"},
                ]

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(
                    run_id="director-idle-unresolved",
                    status="failed",
                    message="Director binding fanout: 2 bindings, 0 succeeded, 2 failed",
                    metadata={
                        "binding_fanout": True,
                        "active_binding_count": 2,
                        "per_binding": [
                            {
                                "provider_id": "p1",
                                "model": "qwen-q6-a",
                                "binding_id": "b1",
                                "run_id": "r1",
                                "status": "cancelled",
                                "message": "Run cancelled: factory-bench event wait timeout after 2400s",
                            },
                            {
                                "provider_id": "p2",
                                "model": "qwen-q6-b",
                                "binding_id": "b2",
                                "run_id": "r2",
                                "status": "cancelled",
                                "message": "Run cancelled: factory-bench event wait timeout after 2400s",
                            },
                        ],
                    },
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        (tmp_path / "src").mkdir()
        (tmp_path / "package.json").write_text(
            '{"scripts":{"build":"tsc"},"devDependencies":{"typescript":"latest"}}',
            encoding="utf-8",
        )
        (tmp_path / "src" / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")

        executor = _IdleUnresolvedHandoffExecutor(tmp_path)
        monkeypatch.setattr(
            TaskRuntimeService,
            "query_observable_task_rows_projection",
            lambda runtime: _authoritative_task_projection(
                Path(runtime.workspace),
                (
                    {
                        "id": 1,
                        "status": "pending",
                        "metadata": {"external_task_id": "TASK-1"},
                    },
                ),
            ),
        )
        tasks = [
            {
                "id": "TASK-1",
                "target_files": ["package.json", "src/index.ts", "README.md"],
            }
        ]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        executor._write_json_artifact(
            "tasks/task_1.json",
            {
                "id": "TASK-1",
                "status": "failed",
                "metadata": {
                    "adapter_result": {
                        "materialization_mode": "workspace_diff_without_write_tool",
                        "materialization_error": "director_missing_write_receipt",
                    }
                },
            },
        )
        executor._write_json_artifact(
            "tasks/task_2.json",
            {
                "id": "TASK-2",
                "status": "failed",
                "metadata": {
                    "runtime_execution": {"last_error": "director_materialization_quality_failed"},
                    "adapter_result": {
                        "materialization_error": "director_materialization_quality_failed",
                    },
                },
            },
        )
        run = FactoryRun(
            id="factory-idle-unresolved-handoff",
            config=FactoryConfig(name="idle-unresolved-handoff"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-23T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(
            executor,
            run_id=run.id,
            tasks=[
                *tasks,
                {
                    "id": "TASK-2",
                    "target_files": ["package.json", "src/index.ts"],
                },
            ],
        )

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 1, "timeout": 120, "execution_mode": "parallel", "max_workers": 2}
            ),
        )

        assert result.status == "failed"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["quality_gate_handoff"] is False
        assert payload["failure_stage"] == "director_dispatch"
        assert payload["error_code"] == "director.canonical_task_boundary_missing"
        assert payload["taskboard"]["converged"] is False
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.materialization_quality_handoff" not in codes
        assert "director.taskboard_unresolved_quality_handoff" not in codes
        assert "director.canonical_task_boundary_missing" in codes

    @pytest.mark.asyncio
    async def test_fails_when_taskboard_not_converged_after_max_rounds(self, tmp_path: Path) -> None:
        """第一轮有进展但最终未收敛仍失败。"""

        class _NoConvergenceProgressExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.results = [
                    CommandResult(
                        run_id="director-round-1",
                        status="completed",
                        message="Run status: completed",
                        metadata={
                            "binding_fanout": True,
                            "per_binding": [
                                {"provider_id": "p1", "model": "m1", "run_id": "r1", "status": "completed"},
                                {"provider_id": "p2", "model": "m2", "run_id": "r2", "status": "completed"},
                            ],
                        },
                    ),
                    CommandResult(
                        run_id="director-round-2",
                        status="completed",
                        message="Run status: completed",
                        metadata={
                            "binding_fanout": True,
                            "per_binding": [
                                {"provider_id": "p1", "model": "m1", "run_id": "r3", "status": "completed"},
                                {"provider_id": "p2", "model": "m2", "run_id": "r4", "status": "completed"},
                            ],
                        },
                    ),
                ]
                # 第一轮后 pending 从 2 降到 1，第二轮后保持不变
                self.stats = [
                    {"total": 2, "pending": 2, "ready": 0, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
                    {"total": 2, "pending": 2, "ready": 0, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
                    {"total": 2, "pending": 1, "ready": 0, "in_progress": 0, "completed": 1, "failed": 0, "blocked": 0},
                    {"total": 2, "pending": 1, "ready": 0, "in_progress": 0, "completed": 1, "failed": 0, "blocked": 0},
                ]

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                return object()

            def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
                del context
                return [
                    {"provider_id": "p1", "model": "m1"},
                    {"provider_id": "p2", "model": "m2"},
                ]

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
                del kwargs
                return self.results.pop(0)

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _NoConvergenceProgressExecutor(tmp_path)
        tasks = [
            {"id": "TASK-1", "target_files": ["package.json"]},
            {"id": "TASK-2", "target_files": ["src/index.ts"]},
        ]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-no-convergence",
            config=FactoryConfig(name="no-convergence"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 2, "timeout": 120, "execution_mode": "parallel", "max_workers": 2}
            ),
        )

        assert result.status == "failed"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert len(payload["attempts"]) == 2
        assert payload["taskboard"]["converged"] is False
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.taskboard_not_converged" in codes
        assert "director.canonical_task_boundary_missing" in codes

    @pytest.mark.asyncio
    async def test_dynamic_director_rounds_cover_blocked_taskboard_total(self, tmp_path: Path) -> None:
        class _BlockedUnrollExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.rounds = 0
                self.stats = [
                    {"total": 5, "pending": 1, "ready": 1, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 4},
                    {"total": 5, "pending": 1, "ready": 1, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 4},
                    {"total": 5, "pending": 1, "ready": 1, "in_progress": 0, "completed": 1, "failed": 0, "blocked": 3},
                    {"total": 5, "pending": 1, "ready": 1, "in_progress": 0, "completed": 1, "failed": 0, "blocked": 3},
                    {"total": 5, "pending": 1, "ready": 1, "in_progress": 0, "completed": 2, "failed": 0, "blocked": 2},
                    {"total": 5, "pending": 1, "ready": 1, "in_progress": 0, "completed": 2, "failed": 0, "blocked": 2},
                    {"total": 5, "pending": 1, "ready": 1, "in_progress": 0, "completed": 3, "failed": 0, "blocked": 1},
                    {"total": 5, "pending": 1, "ready": 1, "in_progress": 0, "completed": 3, "failed": 0, "blocked": 1},
                    {"total": 5, "pending": 1, "ready": 1, "in_progress": 0, "completed": 4, "failed": 0, "blocked": 0},
                    {"total": 5, "pending": 1, "ready": 1, "in_progress": 0, "completed": 4, "failed": 0, "blocked": 0},
                    {"total": 5, "pending": 0, "ready": 0, "in_progress": 0, "completed": 5, "failed": 0, "blocked": 0},
                ]

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                return object()

            def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
                del context
                return [{"provider_id": "p1", "model": "m1"}]

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            def _canonical_factory_projection(
                self,
                _run: FactoryRun,
                _context: dict[str, Any],
            ) -> dict[str, Any]:
                completed = self.rounds >= 5
                task_ids = tuple(f"TASK-{index}" for index in range(1, 6))
                return _with_task_runtime_authority(
                    {
                        "source": "run_ledger",
                        "task_boundary": {
                            "latest_by_task": {
                                f"TASK-{index}": {
                                    "task_id": f"TASK-{index}",
                                    "status": "completed_verified" if completed else "in_execution",
                                    "ok": completed,
                                }
                                for index in range(1, 6)
                            }
                        },
                    },
                    task_ids=task_ids,
                    incomplete_task_ids=() if completed else task_ids,
                )

            async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
                del kwargs
                self.rounds += 1
                return CommandResult(
                    run_id=f"director-round-{self.rounds}",
                    status="completed",
                    message="Run status: completed",
                    metadata={"task_status_counts": {"completed": self.rounds}},
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _BlockedUnrollExecutor(tmp_path)
        tasks = [{"id": f"TASK-{idx}", "target_files": [f"src/{idx}.rs"]} for idx in range(1, 6)]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-blocked-unroll",
            config=FactoryConfig(name="blocked-unroll"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-24T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context({"timeout": 120, "execution_mode": "parallel", "max_workers": 1}),
        )

        assert result.status == "success"
        assert executor.rounds == 5
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["taskboard"]["converged"] is True

    @pytest.mark.asyncio
    async def test_timeout_produces_terminal_status_with_diagnostic(self, tmp_path: Path) -> None:
        """超时应产生终端失败状态和明确的超时诊断信号。"""

        class _MockService:
            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(run_id="run-1", status="timeout", message="Run timed out after 1 seconds")

            async def query_run_status(self, run_id: str) -> CommandResult:
                del run_id
                return CommandResult(run_id="run-1", status="timeout", message="Run timed out after 1 seconds")

        class _TimeoutExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.stats = [
                    {"total": 1, "pending": 1, "ready": 0, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
                    {"total": 1, "pending": 1, "ready": 0, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
                ]

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                return _MockService()

            def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
                del context
                return []

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(run_id="run-1", status="timeout", message="Run timed out after 1 seconds")

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _TimeoutExecutor(tmp_path)
        tasks = [{"id": "TASK-1", "target_files": ["src/index.ts"]}]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-timeout",
            config=FactoryConfig(name="timeout"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 1, "timeout": 120, "execution_mode": "parallel", "max_workers": 1}
            ),
        )

        assert result.status == "failed"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.dispatch_timeout" in codes
        assert payload.get("error_code") == "director.dispatch_timeout"
        assert "timed out" in (payload.get("root_cause_hint") or "").lower()

    @pytest.mark.asyncio
    async def test_timeout_after_workspace_delta_keeps_delta_diagnostic_only(self, tmp_path: Path) -> None:
        class _MockService:
            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(run_id="run-1", status="running", message="submitted")

        class _DeltaTimeoutExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.stats = [
                    {"total": 1, "pending": 1, "ready": 0, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
                    {"total": 1, "pending": 1, "ready": 0, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
                    {"total": 1, "pending": 1, "ready": 0, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
                ]

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                return _MockService()

            def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
                del context
                return []

            def _read_taskboard_stats(self) -> dict[str, int]:
                if len(self.stats) > 1:
                    return dict(self.stats.pop(0))
                return dict(self.stats[0])

            async def _wait_run_completion(
                self,
                service: Any,
                initial_result: CommandResult,
                timeout_seconds: int = 300,
                *,
                cancel_event: asyncio.Event | None = None,
                abort_checker: Any = None,
                cancel_on_timeout: bool = True,
            ) -> CommandResult:
                del service, initial_result, timeout_seconds, cancel_event, abort_checker, cancel_on_timeout
                (self.workspace / "src").mkdir(parents=True, exist_ok=True)
                (self.workspace / "src" / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")
                return CommandResult(run_id="run-1", status="timeout", message="Run timed out after 1 seconds")

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _DeltaTimeoutExecutor(tmp_path)
        tasks = [{"id": "TASK-1", "target_files": ["src/index.ts"]}]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-timeout-delta",
            config=FactoryConfig(name="timeout-delta"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {"director_max_rounds": 1, "timeout": 120, "execution_mode": "parallel", "max_workers": 1}
            ),
        )

        assert result.status == "failed"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.workspace_delta_progress_detected" in codes
        assert "director.dispatch_timeout" in codes
        assert payload["attempts"][0]["progress_made"] is False
        assert payload["attempts"][0]["workspace_delta_progress"] is True
        assert payload["attempts"][0]["workspace_delta"]["added_sample"] == ["src/index.ts"]

    @pytest.mark.asyncio
    async def test_timeout_with_inflight_task_settles_late_director_success(self, tmp_path: Path) -> None:
        """A Director run that finishes during timeout grace should not leave TaskBoard partial."""

        class _MockService:
            def __init__(self, executor: _LateSuccessExecutor) -> None:
                self.executor = executor

            async def query_run_status(self, run_id: str) -> CommandResult:
                self.executor.taskboard_state = {
                    "total": 1,
                    "pending": 0,
                    "ready": 0,
                    "in_progress": 0,
                    "completed": 1,
                    "failed": 0,
                    "blocked": 0,
                }
                return CommandResult(
                    run_id=run_id,
                    status="completed",
                    message="Director completed 1/1 tasks",
                    metadata={"task_status_counts": dict(self.executor.taskboard_state)},
                )

        class _LateSuccessExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.claim_count = 0
                self.taskboard_state = {
                    "total": 1,
                    "pending": 1,
                    "ready": 1,
                    "in_progress": 0,
                    "completed": 0,
                    "failed": 0,
                    "blocked": 0,
                }

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                return _MockService(self)

            def _resolve_director_binding_fanout(self, context: dict[str, Any] | None = None) -> list[dict[str, str]]:
                del context
                return [{"binding_id": "director:test", "provider_id": "test", "model": "test"}]

            def _read_claimable_director_task_ids(self, *, limit: int, factory_run_id: str = "") -> list[str]:
                del limit, factory_run_id
                self.claim_count += 1
                return ["TASK-1"] if self.claim_count == 1 else []

            def _read_taskboard_stats(self) -> dict[str, int]:
                return dict(self.taskboard_state)

            def _canonical_factory_projection(
                self,
                _run: FactoryRun,
                _context: dict[str, Any],
            ) -> dict[str, Any]:
                completed = int(self.taskboard_state.get("completed") or 0) == 1
                return _with_task_runtime_authority(
                    {
                        "source": "run_ledger",
                        "task_boundary": {
                            "latest_by_task": {
                                "TASK-1": {
                                    "task_id": "TASK-1",
                                    "status": "completed_verified" if completed else "in_execution",
                                    "ok": completed,
                                }
                            }
                        },
                    },
                    incomplete_task_ids=() if completed else ("TASK-1",),
                )

            async def _execute_director_binding_fanout(self, **kwargs: object) -> CommandResult:
                del kwargs
                self.taskboard_state = {
                    "total": 1,
                    "pending": 0,
                    "ready": 0,
                    "in_progress": 1,
                    "completed": 0,
                    "failed": 0,
                    "blocked": 0,
                }
                return CommandResult(
                    run_id="director-late-success",
                    status="timeout",
                    message="Run timed out after 1 seconds",
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _LateSuccessExecutor(tmp_path)

        class _CommittedOutcomeWaiter:
            def canonical_terminal_result(
                self,
                *,
                run_id: str,
                process_terminal: bool = False,
            ) -> CommandResult | None:
                del process_terminal
                if int(executor.taskboard_state.get("completed") or 0) != 1:
                    return None
                return CommandResult(
                    run_id=run_id,
                    status="completed",
                    message="committed outcome visible",
                    metadata={
                        "canonical_authoritative": True,
                        "fact_event_seq": 35,
                    },
                )

            def active_execution_progress_marker(
                self,
                *,
                run_id: str,
            ) -> tuple[tuple[str, str, str, str], ...]:
                del run_id
                return ()

            async def cancel_active_run(self, run_id: str, *, reason: str) -> None:
                del run_id, reason

        executor._run_completion_waiter = _CommittedOutcomeWaiter()  # type: ignore[assignment]
        tasks = [{"id": "TASK-1", "target_files": ["src/index.ts"]}]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-late-success",
            config=FactoryConfig(name="late-success"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-21T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {
                    "director_max_rounds": 2,
                    "timeout": 120,
                    "execution_mode": "parallel",
                    "max_workers": 1,
                    "director_dispatch_timeout_settle_grace_seconds": 1,
                }
            ),
        )

        assert result.status == "success"
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        codes = [item.get("code") for item in payload["signals"]]
        assert "director.inflight_timeout_settled" in codes
        assert "director.taskboard_not_converged" not in codes
        assert payload["attempts"][-1]["settled_after_timeout"] is True
        assert payload["taskboard"]["converged"] is True

    @pytest.mark.asyncio
    async def test_soft_timeout_settles_before_another_director_round(self, tmp_path: Path) -> None:
        submitted_deadlines: list[float] = []

        class _BarrierService:
            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                options = kwargs.get("options")
                assert isinstance(options, dict)
                metadata = options.get("metadata")
                assert isinstance(metadata, dict)
                submitted_deadlines.append(float(metadata["factory_director_execution_deadline_epoch_seconds"]))
                return CommandResult(run_id="director-inflight", status="running", message="submitted")

        class _BarrierExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.execute_calls = 0
                self.claim_calls = 0
                self.settle_calls = 0
                self.execution_timeout_seconds = 0
                self.settlement_timeout_seconds = 0
                self.taskboard_state = {
                    "total": 1,
                    "pending": 1,
                    "ready": 1,
                    "in_progress": 0,
                    "completed": 0,
                    "failed": 0,
                    "blocked": 0,
                }

            def _build_orchestration_service(self, context: dict[str, Any]) -> _BarrierService:
                del context
                return _BarrierService()

            def _resolve_director_binding_fanout(
                self,
                context: dict[str, Any] | None = None,
            ) -> list[dict[str, str]]:
                del context
                return []

            def _read_claimable_director_task_ids(self, *, limit: int, factory_run_id: str = "") -> list[str]:
                del limit, factory_run_id
                self.claim_calls += 1
                if self.claim_calls > 1:
                    raise AssertionError("a second Director round started before the inflight child settled")
                return ["TASK-1"]

            def _read_taskboard_stats(self) -> dict[str, int]:
                return dict(self.taskboard_state)

            def _canonical_factory_projection(
                self,
                _run: FactoryRun,
                _context: dict[str, Any],
            ) -> dict[str, Any]:
                completed = int(self.taskboard_state.get("completed") or 0) == 1
                return _with_task_runtime_authority(
                    {
                        "source": "run_ledger",
                        "task_boundary": {
                            "latest_by_task": {
                                "TASK-1": {
                                    "task_id": "TASK-1",
                                    "status": "completed_verified" if completed else "in_execution",
                                    "ok": completed,
                                }
                            }
                        },
                    },
                    incomplete_task_ids=() if completed else ("TASK-1",),
                )

            async def _wait_run_completion(self, *args: object, **kwargs: object) -> CommandResult:
                del args
                self.execute_calls += 1
                timeout_seconds = kwargs["timeout_seconds"]
                assert isinstance(timeout_seconds, int)
                self.execution_timeout_seconds = timeout_seconds
                self.taskboard_state.update({"pending": 0, "ready": 0, "in_progress": 1})
                return CommandResult(
                    run_id="director-inflight",
                    status="timeout",
                    message="soft timeout",
                    metadata={
                        "cancel_signal_sent": False,
                        "cancel_reason": "factory_stage_timeout",
                        "inflight_run_continues": True,
                    },
                )

            async def _settle_inflight_director_run_after_timeout(
                self, *args: object, **kwargs: object
            ) -> CommandResult:
                del args
                self.settle_calls += 1
                grace_seconds = kwargs["grace_seconds"]
                assert isinstance(grace_seconds, int)
                self.settlement_timeout_seconds = grace_seconds
                self.taskboard_state.update({"in_progress": 0, "completed": 1})
                target = self.workspace / "src/index.ts"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("export const ready = true;\n", encoding="utf-8")
                return CommandResult(
                    run_id="director-inflight",
                    status="completed",
                    message="settled",
                    metadata={
                        "canonical_authoritative": True,
                        "fact_event_seq": 36,
                        "task_status_counts": dict(self.taskboard_state),
                    },
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _BarrierExecutor(tmp_path)
        tasks = [{"id": "TASK-1", "target_files": ["src/index.ts"]}]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-immediate-barrier",
            config=FactoryConfig(name="immediate-barrier"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-07-10T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {
                    "director_max_rounds": 2,
                    "execution_mode": "serial",
                    "max_workers": 1,
                    "director_dispatch_timeout_settle_grace_seconds": 5,
                }
            ),
        )

        assert result.status == "success"
        assert executor.execute_calls == 1
        assert executor.settle_calls == 1
        assert executor.claim_calls == 1
        assert len(submitted_deadlines) == 1
        assert submitted_deadlines[0] > time.time()
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["attempts"][0]["settlement_attempted"] is True
        assert payload["attempts"][0]["settled_after_timeout"] is True
        assert executor.execution_timeout_seconds > 0
        # An early lifecycle timeout does not prove that the admitted execution
        # budget was consumed.  The parent barrier must therefore spend the
        # remaining execution lease before its configured five-second
        # settlement reserve; otherwise Factory can close the stage authority
        # while the Director child is only just reaching Provider transport.
        assert executor.settlement_timeout_seconds > 5
        assert executor.execution_timeout_seconds <= payload["attempts"][0]["execution_timeout_seconds"]
        assert (
            payload["attempts"][0]["execution_timeout_seconds"] + payload["attempts"][0]["settlement_timeout_seconds"]
            == payload["attempts"][0]["timeout_seconds"]
        )
        assert payload["attempts"][0]["settlement_timeout_seconds"] == 5
        assert executor.settlement_timeout_seconds <= payload["attempts"][0]["timeout_seconds"]
        assert "director.inflight_timeout_settled" in {str(item.get("code") or "") for item in payload["signals"]}

    @pytest.mark.asyncio
    async def test_soft_timeout_barrier_expiry_fails_without_replaying_director(self, tmp_path: Path) -> None:
        class _BarrierService:
            async def execute_director_run(self, **kwargs: object) -> CommandResult:
                del kwargs
                return CommandResult(run_id="director-inflight", status="running", message="submitted")

        class _BarrierTimeoutExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.execute_calls = 0
                self.taskboard_state = {
                    "total": 1,
                    "pending": 1,
                    "ready": 1,
                    "in_progress": 0,
                    "completed": 0,
                    "failed": 0,
                    "blocked": 0,
                }

            def _build_orchestration_service(self, context: dict[str, Any]) -> _BarrierService:
                del context
                return _BarrierService()

            def _resolve_director_binding_fanout(
                self,
                context: dict[str, Any] | None = None,
            ) -> list[dict[str, str]]:
                del context
                return []

            def _read_claimable_director_task_ids(self, *, limit: int, factory_run_id: str = "") -> list[str]:
                del limit, factory_run_id
                return ["TASK-1"]

            def _read_taskboard_stats(self) -> dict[str, int]:
                return dict(self.taskboard_state)

            async def _wait_run_completion(self, *args: object, **kwargs: object) -> CommandResult:
                del args, kwargs
                self.execute_calls += 1
                if self.execute_calls > 1:
                    raise AssertionError("barrier timeout must not replay the Director")
                self.taskboard_state.update({"pending": 0, "ready": 0, "in_progress": 1})
                return CommandResult(
                    run_id="director-inflight",
                    status="timeout",
                    message="soft timeout",
                    metadata={"inflight_run_continues": True, "cancel_signal_sent": False},
                )

            async def _settle_inflight_director_run_after_timeout(
                self, *args: object, **kwargs: object
            ) -> CommandResult:
                del args, kwargs
                return CommandResult(
                    run_id="director-inflight",
                    status="timeout",
                    message="barrier timeout",
                    metadata={
                        "inflight_run_continues": True,
                        "cancel_signal_sent": False,
                        "barrier_state": "timeout",
                        "barrier_timeout": True,
                    },
                )

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _BarrierTimeoutExecutor(tmp_path)
        tasks = [{"id": "TASK-1", "target_files": ["src/index.ts"]}]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="factory-barrier-timeout",
            config=FactoryConfig(name="barrier-timeout"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-07-10T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {
                    "director_max_rounds": 2,
                    "execution_mode": "serial",
                    "max_workers": 1,
                    "director_dispatch_timeout_settle_grace_seconds": 5,
                }
            ),
        )

        assert result.status == "failed"
        assert executor.execute_calls == 1
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        codes = {str(item.get("code") or "") for item in payload["signals"]}
        assert "director.execution_barrier_timeout" in codes
        assert "director.taskboard_not_converged" not in codes
        assert payload["attempts"][0]["settlement_attempted"] is True
        assert payload["attempts"][0]["settled_after_timeout"] is False
