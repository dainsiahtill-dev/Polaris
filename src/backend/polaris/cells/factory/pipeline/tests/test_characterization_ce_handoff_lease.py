"""Characterization tests for Chief Engineer lease/heartbeat + director handoff guards (part 2)."""

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
    _assert_no_chief_engineer_lease_keeper_threads,
    _capture_chief_engineer_lease_keepers,
    _executor,
    _factory_stage_context,
    _generate_domain_blueprint,
    _invalid_chief_engineer_stream_result,
    _library_completion_requirements,
    _single_task_chief_engineer_result,
    _write_minimal_chief_engineer_plan,
    _write_review_for_blueprint,
)


class TestChiefEngineerHandoffGuards:
    def test_chief_engineer_schema_repair_is_bounded_to_one_attempt(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        _write_minimal_chief_engineer_plan(executor)
        commands: list[Any] = []

        class _AlwaysInvalidRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                commands.append(command)
                return _invalid_chief_engineer_stream_result()

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _AlwaysInvalidRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-schema-repair-bounded",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "failed"
        assert len(commands) == 2
        assert commands[-1].task_id.endswith("-SCHEMA-REPAIR")
        review = json.loads(
            Path(resolve_logical_path(tmp_path, f"runtime/state/blueprints/{run.id}.review.json")).read_text(
                encoding="utf-8"
            )
        )
        assert review["llm_call_count"] == 2
        assert [signal["code"] for signal in review["signals"]] == [
            "chief_engineer.output_schema_repair_started",
            "chief_engineer.llm_review_failed",
        ]
        assert len(keepers) == 2
        assert all(keeper.is_alive is False for keeper in keepers)
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_execution_attempt_reuses_claim_on_replay_and_rotates_after_requeue(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        objective = "Produce one durable Chief Engineer portfolio."
        lease_budget = executor._chief_engineer_execution_attempt_lease_budget(240)

        task_id, first_attempt = executor._claim_chief_engineer_execution_attempt(
            run_id="factory-run-identity",
            portfolio_task_id="CE-PORTFOLIO-factory-run-identity",
            objective=objective,
            lease_budget=lease_budget,
        )
        replay_task_id, replay_attempt = executor._claim_chief_engineer_execution_attempt(
            run_id="factory-run-identity",
            portfolio_task_id="CE-PORTFOLIO-factory-run-identity",
            objective=objective,
            lease_budget=lease_budget,
        )

        assert replay_task_id == task_id
        assert replay_attempt.session_id == first_attempt.session_id
        assert replay_attempt.attempt == first_attempt.attempt

        task_runtime = TaskRuntimeService(str(tmp_path))
        suspended = task_runtime.settle_execution_attempt(
            SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=str(tmp_path),
                identity=replay_attempt,
                outcome="suspended",
                summary="retry the CE portfolio claim",
            )
        )
        assert suspended["success"] is True

        requeued_task_id, requeued_attempt = executor._claim_chief_engineer_execution_attempt(
            run_id="factory-run-identity",
            portfolio_task_id="CE-PORTFOLIO-factory-run-identity",
            objective=objective,
            lease_budget=lease_budget,
        )

        assert requeued_task_id == task_id
        assert requeued_attempt.session_id != first_attempt.session_id
        assert requeued_attempt.attempt == first_attempt.attempt + 1

    def test_chief_engineer_execution_attempt_lease_covers_admitted_long_call(
        self,
        tmp_path: Path,
    ) -> None:
        executor = _executor(tmp_path)
        pm_tasks = [
            {
                "id": "TASK-LONG-CE",
                "title": "Plan a long Chief Engineer review",
                "goal": "Exercise an admitted CE timeout above the historical 240 second lease.",
            }
        ]
        configured_timeout = executor._chief_engineer_llm_timeout_seconds({"chief_engineer_llm_timeout_seconds": 300})
        admission = executor._chief_engineer_deadline_projection_decision(
            {},
            requested_timeout_seconds=configured_timeout,
            dependency_schedule=build_task_dependency_schedule(pm_tasks),
        )
        assert admission.disposition is FactoryDeadlineDispositionV1.EXECUTE
        assert admission.timeout_seconds == 300
        lease_budget = executor._chief_engineer_execution_attempt_lease_budget(admission.timeout_seconds)

        task_id, attempt = executor._claim_chief_engineer_execution_attempt(
            run_id="factory-run-long-ce",
            portfolio_task_id="CE-PORTFOLIO-factory-run-long-ce",
            objective="Produce a long-running Chief Engineer portfolio.",
            lease_budget=lease_budget,
        )
        session_path = Path(resolve_logical_path(tmp_path, f"runtime/tasks/task_{task_id}.session.json"))
        claimed_session = json.loads(session_path.read_text(encoding="utf-8"))
        lease_seconds = (
            datetime.fromisoformat(claimed_session["lease_expires_at"])
            - datetime.fromisoformat(claimed_session["claimed_at"])
        ).total_seconds()
        assert lease_seconds == 330
        assert lease_seconds > admission.timeout_seconds

        executor._settle_chief_engineer_execution_attempt(
            task_id=task_id,
            execution_attempt=attempt,
            stage_status="success",
            summary="long CE review completed within admitted budget",
        )

        settled_session = json.loads(session_path.read_text(encoding="utf-8"))
        assert settled_session["status"] == "completed"
        assert settled_session["resumable"] is False
        completed_events = query_fact_events(
            QueryFactEventsV1(
                workspace=str(tmp_path),
                stream="task_runtime.execution",
                event_type="completed",
            )
        ).events
        assert len(completed_events) == 1

    def test_chief_engineer_execution_attempt_lease_budget_is_bounded(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for env_key in stage_executor_module._CHIEF_ENGINEER_LLM_TIMEOUT_ENV_KEYS:
            monkeypatch.delenv(env_key, raising=False)

        maximum = stage_executor_module.MAX_LLM_PROVIDER_TIMEOUT_SECONDS
        grace = stage_executor_module._CHIEF_ENGINEER_EXECUTION_ATTEMPT_SETTLEMENT_GRACE_SECONDS
        assert (
            OrchestrationStageExecutor._chief_engineer_llm_timeout_seconds(
                {"chief_engineer_llm_timeout_seconds": maximum + 1}
            )
            == maximum
        )
        assert (
            OrchestrationStageExecutor._chief_engineer_llm_timeout_seconds(
                {"chief_engineer_llm_timeout_seconds": "1e100000"}
            )
            == maximum
        )
        assert (
            OrchestrationStageExecutor._chief_engineer_llm_timeout_seconds(
                {"chief_engineer_llm_timeout_seconds": "inf"}
            )
            == stage_executor_module._DEFAULT_CHIEF_ENGINEER_LLM_TIMEOUT_SECONDS
        )
        maximum_budget = OrchestrationStageExecutor._chief_engineer_execution_attempt_lease_budget(maximum)
        assert maximum_budget.lease_ttl_seconds == maximum + grace
        assert 0 < maximum_budget.heartbeat_interval_seconds < maximum_budget.lease_ttl_seconds
        with pytest.raises(ValueError, match="chief_engineer_execution_timeout_seconds_out_of_bounds"):
            OrchestrationStageExecutor._chief_engineer_execution_attempt_lease_budget(0)
        with pytest.raises(ValueError, match="chief_engineer_execution_timeout_seconds_out_of_bounds"):
            OrchestrationStageExecutor._chief_engineer_execution_attempt_lease_budget(maximum + 1)

    def test_chief_engineer_lease_renews_during_synchronous_post_processing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        _write_minimal_chief_engineer_plan(executor)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        heartbeat_calls: list[dict[str, Any]] = []
        post_processing_heartbeats_ready = threading.Event()
        original_heartbeat = stage_executor_module.heartbeat_task_runtime_execution_attempt
        original_extract = executor._ce_extract_llm_evidence
        fast_budget = stage_executor_module._ChiefEngineerExecutionAttemptLeaseBudget(
            lease_ttl_seconds=1,
            heartbeat_interval_seconds=0.05,
        )
        renewals_to_cross_initial_lease = (
            int(fast_budget.lease_ttl_seconds / fast_budget.heartbeat_interval_seconds) + 2
        )
        monkeypatch.setattr(
            executor,
            "_chief_engineer_execution_attempt_lease_budget",
            lambda _timeout: fast_budget,
        )

        def _record_heartbeat(command: Any) -> Any:
            result = original_heartbeat(command)
            heartbeat_calls.append(
                {
                    "task_id": command.identity.task_id,
                    "session_id": command.identity.session_id,
                    "lease_ttl_seconds": command.lease_ttl_seconds,
                }
            )
            if len(heartbeat_calls) >= renewals_to_cross_initial_lease:
                post_processing_heartbeats_ready.set()
            return result

        def _blocking_extract(ce_result: Any, *, task_id: str, run_id: str) -> dict[str, Any]:
            assert post_processing_heartbeats_ready.wait(timeout=2.0)
            return original_extract(ce_result, task_id=task_id, run_id=run_id)

        class _SuccessfulRoleRuntimeService:
            async def execute_role_task(self, _command: Any) -> Any:
                return _single_task_chief_engineer_result()

        monkeypatch.setattr(stage_executor_module, "heartbeat_task_runtime_execution_attempt", _record_heartbeat)
        monkeypatch.setattr(executor, "_ce_extract_llm_evidence", _blocking_extract)
        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _SuccessfulRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-ce-heartbeat-post-processing",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "success"
        assert len(keepers) == 1
        keeper = keepers[0]
        assert keeper.heartbeat_count >= renewals_to_cross_initial_lease
        assert keeper.is_alive is False
        _assert_no_chief_engineer_lease_keeper_threads()
        assert len(heartbeat_calls) >= renewals_to_cross_initial_lease
        assert len(heartbeat_calls) * fast_budget.heartbeat_interval_seconds > fast_budget.lease_ttl_seconds
        assert all(call["task_id"] == keeper.task_id for call in heartbeat_calls)
        assert all(call["session_id"] == keeper.execution_attempt.session_id for call in heartbeat_calls)
        assert all(call["lease_ttl_seconds"] == fast_budget.lease_ttl_seconds for call in heartbeat_calls)
        task_runtime = TaskRuntimeService(str(tmp_path))
        task = task_runtime.get_task(f"CE-PORTFOLIO-{run.id}")
        assert task is not None
        session_path = Path(resolve_logical_path(tmp_path, f"runtime/tasks/task_{task['id']}.session.json"))
        session = json.loads(session_path.read_text(encoding="utf-8"))
        assert session["status"] == "completed"

    def test_chief_engineer_heartbeat_failure_fails_closed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        executor = _executor(tmp_path)
        _write_minimal_chief_engineer_plan(executor)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        heartbeat_failed = threading.Event()
        heartbeat_recovered = threading.Event()
        heartbeat_calls = 0
        original_heartbeat = stage_executor_module.heartbeat_task_runtime_execution_attempt
        fast_budget = stage_executor_module._ChiefEngineerExecutionAttemptLeaseBudget(
            lease_ttl_seconds=2,
            heartbeat_interval_seconds=0.01,
        )
        monkeypatch.setattr(
            executor,
            "_chief_engineer_execution_attempt_lease_budget",
            lambda _timeout: fast_budget,
        )

        def _fail_then_renew_heartbeat(command: Any) -> Any:
            nonlocal heartbeat_calls
            heartbeat_calls += 1
            if heartbeat_calls == 1:
                heartbeat_failed.set()
                return TaskRuntimeExecutionAttemptHeartbeatVerdictV1(
                    success=False,
                    code="file_lock_timeout",
                    workspace=command.workspace,
                    identity=command.identity,
                    evidence_anchor={"synthetic": True},
                )
            result = original_heartbeat(command)
            heartbeat_recovered.set()
            return result

        class _SuccessfulRoleRuntimeService:
            async def execute_role_task(self, _command: Any) -> Any:
                assert await asyncio.to_thread(heartbeat_failed.wait, 2.0)
                assert await asyncio.to_thread(heartbeat_recovered.wait, 2.0)
                return _single_task_chief_engineer_result()

        monkeypatch.setattr(
            stage_executor_module,
            "heartbeat_task_runtime_execution_attempt",
            _fail_then_renew_heartbeat,
        )
        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _SuccessfulRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-ce-heartbeat-failed",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        with caplog.at_level(logging.ERROR, logger=stage_executor_module.__name__):
            result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "success"
        assert "code=chief_engineer.execution_attempt_heartbeat_failed" in caplog.text
        assert "file_lock_timeout" in caplog.text
        assert len(keepers) == 1
        assert keepers[0].is_alive is False
        _assert_no_chief_engineer_lease_keeper_threads()
        assert keepers[0].heartbeat_count >= 1
        assert heartbeat_calls >= 2
        assert keepers[0].failure is None
        assert keepers[0].incidents[0].reason == "file_lock_timeout"
        task_runtime = TaskRuntimeService(str(tmp_path))
        task = task_runtime.get_task(f"CE-PORTFOLIO-{run.id}")
        assert task is not None
        session_path = Path(resolve_logical_path(tmp_path, f"runtime/tasks/task_{task['id']}.session.json"))
        session = json.loads(session_path.read_text(encoding="utf-8"))
        assert session["status"] == "completed"
        completed_events = query_fact_events(
            QueryFactEventsV1(
                workspace=str(tmp_path),
                stream="task_runtime.execution",
                event_type="completed",
            )
        ).events
        assert len(completed_events) == 1

    def test_chief_engineer_lease_keeper_records_system_exit_as_fail_closed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A BaseException cannot silently terminate the keeper thread."""

        entered = threading.Event()
        identity = TaskRuntimeExecutionAttemptIdentityV1(
            workspace=str(tmp_path),
            task_id=91,
            external_task_id="CE-PORTFOLIO-system-exit",
            session_id="system-exit-session",
            attempt=1,
            role_id="chief_engineer",
            worker_id="factory-chief-engineer",
            run_id="system-exit-run",
            lease_expires_at="2026-07-14T00:05:00+00:00",
        )
        keeper = stage_executor_module._ChiefEngineerExecutionAttemptLeaseKeeper(
            workspace=str(tmp_path),
            task_id=identity.task_id,
            execution_attempt=identity,
            budget=stage_executor_module._ChiefEngineerExecutionAttemptLeaseBudget(
                lease_ttl_seconds=2,
                heartbeat_interval_seconds=0.01,
            ),
        )

        def _raise_system_exit(_command: Any) -> Any:
            entered.set()
            raise SystemExit("synthetic keeper boundary")

        monkeypatch.setattr(stage_executor_module, "heartbeat_task_runtime_execution_attempt", _raise_system_exit)
        keeper.start()
        assert entered.wait(timeout=2)
        stopped = keeper.stop()

        assert stopped.thread_exited is True
        assert keeper.failure is not None
        assert keeper.failure.error_type == "SystemExit"
        assert keeper.incidents[-1].reason == "heartbeat_exception"
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_blocked_heartbeat_blocks_settlement_without_deadlock(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An unresolved heartbeat makes settlement fail closed rather than race it."""

        entered = threading.Event()
        release = threading.Event()
        identity = TaskRuntimeExecutionAttemptIdentityV1(
            workspace=str(tmp_path),
            task_id=92,
            external_task_id="CE-PORTFOLIO-blocked",
            session_id="blocked-session",
            attempt=1,
            role_id="chief_engineer",
            worker_id="factory-chief-engineer",
            run_id="blocked-run",
            lease_expires_at="2026-07-14T00:05:00+00:00",
        )
        keeper = stage_executor_module._ChiefEngineerExecutionAttemptLeaseKeeper(
            workspace=str(tmp_path),
            task_id=identity.task_id,
            execution_attempt=identity,
            budget=stage_executor_module._ChiefEngineerExecutionAttemptLeaseBudget(
                lease_ttl_seconds=2,
                heartbeat_interval_seconds=0.02,
            ),
        )
        scope = stage_executor_module._ChiefEngineerExecutionAttemptLeaseScope()
        scope.bind_claim(task_id=identity.task_id, execution_attempt=identity)

        def _block_heartbeat(command: Any) -> Any:
            entered.set()
            assert release.wait(timeout=2)
            return TaskRuntimeExecutionAttemptHeartbeatVerdictV1(
                success=True,
                code="heartbeat_renewed",
                workspace=command.workspace,
                identity=command.identity,
                renewed_identity=command.identity,
            )

        monkeypatch.setattr(stage_executor_module, "heartbeat_task_runtime_execution_attempt", _block_heartbeat)
        scope.start_keeper(keeper)
        assert entered.wait(timeout=2)
        started_at = time.monotonic()
        should_settle, failure = scope.begin_settlement()
        elapsed_seconds = time.monotonic() - started_at
        assert should_settle is False
        assert failure is not None
        assert failure.reason == "heartbeat_thread_stop_timeout"
        assert elapsed_seconds < 0.5

        release.set()
        assert keeper.stop().thread_exited is True
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_heartbeat_failure_does_not_mask_cancellation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        executor = _executor(tmp_path)
        _write_minimal_chief_engineer_plan(executor)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        heartbeat_failed = threading.Event()
        cancellation = asyncio.CancelledError("canonical cancellation after heartbeat failure")
        fast_budget = stage_executor_module._ChiefEngineerExecutionAttemptLeaseBudget(
            lease_ttl_seconds=2,
            heartbeat_interval_seconds=0.01,
        )
        monkeypatch.setattr(
            executor,
            "_chief_engineer_execution_attempt_lease_budget",
            lambda _timeout: fast_budget,
        )

        def _raise_heartbeat(_command: Any) -> Any:
            heartbeat_failed.set()
            raise RuntimeError("heartbeat_failed_before_cancellation")

        class _CancelledRoleRuntimeService:
            async def execute_role_task(self, _command: Any) -> Any:
                assert await asyncio.to_thread(heartbeat_failed.wait, 2.0)
                raise cancellation

        monkeypatch.setattr(stage_executor_module, "heartbeat_task_runtime_execution_attempt", _raise_heartbeat)
        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _CancelledRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-ce-heartbeat-failed-cancelled",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        with (
            caplog.at_level(logging.ERROR, logger=stage_executor_module.__name__),
            pytest.raises(asyncio.CancelledError) as raised,
        ):
            asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert raised.value is cancellation
        assert "heartbeat_failed_before_cancellation" in caplog.text
        assert len(keepers) == 1
        assert keepers[0].is_alive is False
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_review_uses_one_portfolio_call_for_multiple_tasks(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        delivery_plan_document = {
            "schema_version": "polaris.delivery_plan_document.v1",
            "product_summary": {
                "intent": "Deliver a coherent weather engine and CLI.",
                "core_terms": ["planet", "weather", "cloud", "wind"],
            },
        }
        delivery_depth_contract = {
            "schema_version": "polaris.delivery_depth_contract.v1",
            "product_intent": {
                "subject": "planet weather",
                "primary_entities": ["planet", "weather", "cloud", "wind"],
            },
        }
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "title": "Build weather models",
                        "goal": "Implement weather and wind domain models.",
                        "target_files": ["src/models/weather.py"],
                        "scope_paths": ["src/models/weather.py"],
                        "acceptance_criteria": ["weather model validates cloud and wind"],
                        "execution_checklist": ["Implement immutable weather model"],
                        "delivery_plan_document": delivery_plan_document,
                        "delivery_depth_contract": delivery_depth_contract,
                    },
                    {
                        "id": "TASK-2",
                        "title": "Build forecast engine",
                        "goal": "Use the weather model from a forecast engine.",
                        "depends_on": ["TASK-1"],
                        "target_files": ["src/engine/forecast.py", "tests/test_forecast.py"],
                        "scope_paths": ["src/engine/forecast.py", "tests/test_forecast.py"],
                        "acceptance_criteria": ["forecast consumes the shared weather model"],
                        "execution_checklist": ["Implement forecast rules"],
                        "delivery_plan_document": delivery_plan_document,
                        "delivery_depth_contract": delivery_depth_contract,
                    },
                ]
            },
        )
        calls: list[Any] = []

        class _PortfolioRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                calls.append(command)
                ce_output = {
                    "construction_plan": {
                        "project_design_intent": "Keep domain state independent from forecast orchestration.",
                        "project_interface_contract": {
                            "provider_declarations": [
                                {
                                    "path": "src/models/weather.py",
                                    "name": "WeatherReport",
                                    "symbol_kind": "class",
                                    "signature": "WeatherReport(cloud: float, wind: float)",
                                }
                            ],
                            "consumer_declarations": [
                                {
                                    "path": "src/engine/forecast.py",
                                    "name": "WeatherReport",
                                    "provider_path": "src/models/weather.py",
                                }
                            ],
                        },
                        "task_plans": {
                            "TASK-1": {
                                "implementation": ["Define WeatherReport and validation boundaries"],
                                "verification": ["Validate cloud and wind boundaries"],
                            },
                            "TASK-2": {
                                "implementation": [
                                    "Import WeatherReport and map planet weather, cloud, and wind forecast rules"
                                ],
                                "verification": [
                                    "Exercise the planet weather provider-consumer contract for cloud and wind"
                                ],
                            },
                        },
                    },
                    "scope_for_apply": ["src/models/weather.py", "src/engine/forecast.py"],
                    "risk_flags": [],
                    "project_completion_contract": _library_completion_requirements(
                        "src/models/weather.py",
                        "src/engine/forecast.py",
                        owner_task_ids=("TASK-1", "TASK-2"),
                        test_path="tests/test_forecast.py",
                        test_owner_task_id="TASK-2",
                    ),
                }
                return SimpleNamespace(
                    ok=True,
                    output=json.dumps(ce_output),
                    error_message="",
                    error_code="",
                    metadata={
                        "provider_id": "test-provider",
                        "model": "test-model",
                        "structured_output": ce_output,
                        "final_request_context_audit": {"context_window_utilization": 0.35},
                        "context_snapshot_ref": "abcdef123456abcdef123456",
                    },
                    usage={},
                )

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _PortfolioRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-portfolio",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "success"
        assert len(calls) == 1
        assert calls[0].task_id == "CE-PORTFOLIO-factory-run-portfolio"
        assert calls[0].context["delivery_mode"] == "analyze_only"
        assert calls[0].context["llm_max_tokens"] == 16_384
        assert calls[0].context["task_count"] == 2
        assert len(calls[0].context["pm_task_contract"]["tasks"]) == 2
        review_path = Path(resolve_logical_path(tmp_path, "runtime/state/blueprints/factory-run-portfolio.review.json"))
        review = json.loads(review_path.read_text(encoding="utf-8"))
        assert review["llm_call_count"] == 1
        assert review["generated_blueprints"] == 2
        assert review["portfolio"]["portfolio_hash"]
        assert review["portfolio"]["project_completion_contract_hash"]
        assert review["project_interface_contract"]["project_interface_contract_hash"]
        blueprints = [
            BlueprintPersistence(str(tmp_path), ensure_directory=False).load(row["blueprint_id"])
            for row in review["blueprints"]
        ]
        assert all(isinstance(blueprint, dict) for blueprint in blueprints)
        portfolio_hashes = {str(blueprint["blueprint_portfolio_hash"]) for blueprint in blueprints if blueprint}
        interface_hashes = {str(blueprint["project_interface_contract_hash"]) for blueprint in blueprints if blueprint}
        assert portfolio_hashes == {review["portfolio"]["portfolio_hash"]}
        assert interface_hashes == {review["project_interface_contract"]["project_interface_contract_hash"]}

    def test_chief_engineer_review_fails_closed_after_llm_timeout(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        delivery_plan_document = {
            "schema_version": "polaris.delivery_plan_document.v1",
            "language": "javascript",
            "product_summary": {
                "intent": "Deliver a meteor wish queue.",
                "core_terms": ["meteor", "wish", "queue", "priority"],
            },
        }
        delivery_depth_contract = {
            "schema_version": "polaris.delivery_depth_contract.v1",
            "language": "javascript",
            "product_intent": {
                "subject": "meteor wish queue",
                "primary_entities": ["meteor", "wish", "queue", "priority"],
            },
        }
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-CORE",
                        "title": "实现 流星愿望队列 - core engine/service modules",
                        "goal": "在工作区根交付 流星愿望队列。 Scope this task to core engine/service modules only.",
                        "target_files": ["src/engine/rules.js", "src/engine/runner.js"],
                        "scope_paths": ["src/engine/rules.js", "src/engine/runner.js"],
                        "acceptance_criteria": [
                            "verify src/engine/rules.js exists",
                            "verify src/engine/runner.js exists",
                        ],
                        "execution_checklist": ["Materialize only the listed core engine files."],
                        "delivery_plan_document": delivery_plan_document,
                        "delivery_depth_contract": delivery_depth_contract,
                    }
                ]
            },
        )

        class _TimeoutRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                return SimpleNamespace(
                    ok=False,
                    status="failed",
                    output="",
                    error_code="provider_timeout",
                    error_message="Request timeout (55.0s)",
                    metadata={
                        "provider_id": "kimi",
                        "model": "kimi-for-coding",
                    },
                    usage={},
                )

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _TimeoutRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-timeout-projection",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        result = asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert result.status == "failed"
        review_path = Path(
            resolve_logical_path(tmp_path, "runtime/state/blueprints/factory-run-timeout-projection.review.json")
        )
        review = json.loads(review_path.read_text(encoding="utf-8"))
        assert review["generated_blueprints"] == 0
        assert [signal["severity"] for signal in review["signals"]] == ["error"]
        signal = review["signals"][0]
        assert signal["code"] == "chief_engineer.llm_review_failed"
        assert signal["severity"] == "error"
        assert signal["recoverable"] is False
        assert review["portfolio"] == {}
        assert review["llm_call_count"] == 1
        assert len(keepers) == 1
        assert keepers[0].is_alive is False
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_cancellation_suspends_claimed_attempt_once(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        _write_minimal_chief_engineer_plan(executor)

        class _CancelledRoleRuntimeService:
            async def execute_role_task(self, _command: Any) -> Any:
                raise asyncio.CancelledError("test CE cancellation")

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _CancelledRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-ce-cancelled",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        with pytest.raises(asyncio.CancelledError, match="test CE cancellation"):
            asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        task_runtime = TaskRuntimeService(str(tmp_path))
        task = task_runtime.get_task(f"CE-PORTFOLIO-{run.id}")
        assert task is not None
        session_path = Path(resolve_logical_path(tmp_path, f"runtime/tasks/task_{task['id']}.session.json"))
        session = json.loads(session_path.read_text(encoding="utf-8"))
        assert session["status"] == "suspended"
        assert session["resumable"] is True

        suspended_events = query_fact_events(
            QueryFactEventsV1(
                workspace=str(tmp_path),
                stream="task_runtime.execution",
                event_type="suspended",
            )
        ).events
        terminal_events = query_fact_events(
            QueryFactEventsV1(
                workspace=str(tmp_path),
                stream="task_runtime.execution",
            )
        ).events
        assert len(suspended_events) == 1
        assert [event["event_type"] for event in terminal_events].count("suspended") == 1
        assert all(event["event_type"] not in {"completed", "failed"} for event in terminal_events)
        assert len(keepers) == 1
        assert keepers[0].is_alive is False
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_cancellation_survives_settlement_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        executor = _executor(tmp_path)
        keepers = _capture_chief_engineer_lease_keepers(monkeypatch)
        _write_minimal_chief_engineer_plan(executor)
        cancellation = asyncio.CancelledError("original CE cancellation")
        settlement_calls: list[dict[str, Any]] = []

        class _CancelledRoleRuntimeService:
            async def execute_role_task(self, _command: Any) -> Any:
                raise cancellation

        def _fail_settlement(**kwargs: Any) -> None:
            settlement_calls.append(dict(kwargs))
            raise RuntimeError("synthetic settlement failure")

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _CancelledRoleRuntimeService)
        monkeypatch.setattr(executor, "_settle_chief_engineer_execution_attempt", _fail_settlement)
        run = FactoryRun(
            id="factory-run-ce-cancelled-settlement-failure",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        with (
            caplog.at_level(logging.ERROR, logger=stage_executor_module.__name__),
            pytest.raises(asyncio.CancelledError) as raised,
        ):
            asyncio.run(executor._execute_chief_engineer_review(run, _factory_stage_context()))

        assert raised.value is cancellation
        assert len(settlement_calls) == 1
        assert "Chief Engineer cancellation settlement failed" in caplog.text
        assert "synthetic settlement failure" in caplog.text

        task_runtime = TaskRuntimeService(str(tmp_path))
        task = task_runtime.get_task(f"CE-PORTFOLIO-{run.id}")
        assert task is not None
        session_path = Path(resolve_logical_path(tmp_path, f"runtime/tasks/task_{task['id']}.session.json"))
        session = json.loads(session_path.read_text(encoding="utf-8"))
        assert session["status"] == "active"
        execution_events = query_fact_events(
            QueryFactEventsV1(
                workspace=str(tmp_path),
                stream="task_runtime.execution",
            )
        ).events
        assert all(event["event_type"] not in {"completed", "failed", "suspended"} for event in execution_events)
        assert len(keepers) == 1
        assert keepers[0].is_alive is False
        _assert_no_chief_engineer_lease_keeper_threads()

    def test_chief_engineer_review_blocks_without_deadline_projection_or_llm_call(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        delivery_plan_document = {
            "schema_version": "polaris.delivery_plan_document.v1",
            "product_summary": {
                "intent": "Deliver a meteor wish queue.",
                "core_terms": ["meteor", "wish", "queue", "priority"],
            },
        }
        delivery_depth_contract = {
            "schema_version": "polaris.delivery_depth_contract.v1",
            "product_intent": {
                "subject": "meteor wish queue",
                "primary_entities": ["meteor", "wish", "queue", "priority"],
            },
        }
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "title": "Build meteor wish queue",
                        "goal": "Build a meteor wish queue with prioritization behavior.",
                        "target_files": ["package.json", "src/index.js", "src/engine/rules.js"],
                        "scope_paths": ["package.json", "src/index.js", "src/engine/rules.js"],
                        "acceptance_criteria": ["npm test passes", "npm start prints queue status"],
                        "execution_checklist": ["Implement queue model", "Implement prioritization rules"],
                        "delivery_plan_document": delivery_plan_document,
                        "delivery_depth_contract": delivery_depth_contract,
                    },
                    {
                        "id": "TASK-2",
                        "title": "Add meteor wish queue tests",
                        "goal": "Validate meteor wish queue prioritization behavior.",
                        "target_files": ["tests/product.test.js"],
                        "scope_paths": ["tests/product.test.js"],
                        "acceptance_criteria": ["npm test covers normal and boundary queues"],
                        "execution_checklist": ["Add tests for priority ordering"],
                        "delivery_plan_document": delivery_plan_document,
                        "delivery_depth_contract": delivery_depth_contract,
                    },
                ]
            },
        )

        class _UnexpectedRoleRuntimeService:
            async def execute_role_task(self, command: Any) -> Any:
                raise AssertionError(f"CE LLM should not be called under deadline projection: {command!r}")

        monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", _UnexpectedRoleRuntimeService)
        run = FactoryRun(
            id="factory-run-projection",
            config=FactoryConfig(name="bench-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 155.0

        result = asyncio.run(
            executor._execute_chief_engineer_review(
                run,
                _factory_stage_context(
                    {
                        "factory_run_deadline_epoch_seconds": deadline_epoch,
                        "director_first_materialization_min_budget_seconds": 60,
                        "quality_gate_reserved_budget_seconds": 20,
                    }
                ),
            )
        )

        assert result.status == "failed"
        review_path = Path(
            resolve_logical_path(tmp_path, "runtime/state/blueprints/factory-run-projection.review.json")
        )
        review = json.loads(review_path.read_text(encoding="utf-8"))
        assert [signal["code"] for signal in review["signals"]] == [
            "chief_engineer.deadline_admission_blocked",
        ]
        assert review["blueprints"] == []
        assert review["portfolio"] == {}
        assert review["llm_call_count"] == 0

    def test_director_handoff_guard_allows_ready_blueprint(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = _generate_domain_blueprint(
            tmp_path,
            task_id="TASK-1",
            objective="Build pirate treasure budget planner",
            target_files=["models/capsule.go", "engine/museum.go"],
            acceptance_criteria=[
                "treasure, budget, port, and reef behavior tests pass",
                "go test ./... passes",
            ],
            execution_checklist=[
                "Implement treasure and budget models",
                "Implement port fee and reef risk rules",
            ],
        )
        assert result.ok is True
        _write_review_for_blueprint(
            executor,
            run_id="run-1",
            task_id="TASK-1",
            blueprint_id=result.blueprint_id,
        )

        signals = executor._chief_engineer_handoff_signals_for_director(
            [{"id": "TASK-1", "target_files": ["models/capsule.go"]}],
            run_id="run-1",
        )

        assert signals == []

    def test_director_handoff_guard_blocks_missing_blueprint(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)

        signals = executor._chief_engineer_handoff_signals_for_director(
            [{"id": "TASK-1", "target_files": ["models/capsule.go"]}],
            run_id="run-1",
        )

        assert [signal["code"] for signal in signals] == ["director.chief_engineer_handoff_missing"]
        assert signals[0]["severity"] == "error"

    def test_director_handoff_guard_does_not_use_stale_persisted_blueprint_without_review(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = _generate_domain_blueprint(
            tmp_path,
            task_id="TASK-1",
            objective="Build pirate treasure budget planner",
            target_files=["models/capsule.go", "engine/museum.go"],
            acceptance_criteria=[
                "treasure, budget, port, and reef behavior tests pass",
                "go test ./... passes",
            ],
            execution_checklist=[
                "Implement treasure and budget models",
                "Implement port fee and reef risk rules",
            ],
        )
        assert result.ok is True

        signals = executor._chief_engineer_handoff_signals_for_director(
            [{"id": "TASK-1", "target_files": ["models/capsule.go"]}],
            run_id="different-run-without-review",
        )

        assert [signal["code"] for signal in signals] == ["director.chief_engineer_handoff_missing"]
        assert signals[0]["severity"] == "error"

    def test_director_handoff_guard_blocks_unready_blueprint(self, tmp_path: Path) -> None:
        executor = _executor(tmp_path)
        result = _generate_domain_blueprint(
            tmp_path,
            task_id="TASK-1",
            objective="Build flavor recipe planner",
            target_files=["models/flavor.go", "engine/palette.go"],
            acceptance_criteria=["recipe behavior tests pass", "go test ./... passes"],
            execution_checklist=["Implement flavor model", "Implement palette rules"],
        )
        assert result.ok is True
        _write_review_for_blueprint(
            executor,
            run_id="run-1",
            task_id="TASK-1",
            blueprint_id=result.blueprint_id,
        )

        signals = executor._chief_engineer_handoff_signals_for_director(
            [{"id": "TASK-1", "target_files": ["models/flavor.go"]}],
            run_id="run-1",
        )

        assert [signal["code"] for signal in signals] == ["director.chief_engineer_handoff_blocked"]
        assert signals[0]["severity"] == "error"
        assert signals[0]["blockers"]
