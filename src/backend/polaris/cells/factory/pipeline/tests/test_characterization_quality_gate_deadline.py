"""Characterization tests for quality-gate deadline budget handling."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal import (
    factory_stage_executor as stage_executor_module,
)
from polaris.cells.factory.pipeline.internal.factory_deadline_policy import (
    FactoryDeadlineDispositionV1,
    build_task_dependency_schedule,
)
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    CommandResult,
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    OrchestrationStageExecutor,
)
from polaris.cells.factory.pipeline.tests._characterization_helpers import (
    _executor,
    _factory_stage_context,
    _with_task_runtime_authority,
    _write_handoff_ready_review_for_tasks,
)
from polaris.kernelone.storage import resolve_logical_path


class TestQualityGateDeadlineHandling:
    def test_default_deadline_policy_blocks_ce_when_clipped_budget_below_generation_floor(self) -> None:
        # A 508s horizon over a 5-task serial chain leaves only ~105-108s for the CE
        # stage after reserving the full Director critical path (400s) + QA + safety.
        # That is below the modeled physical floor (~205s) to stream the 16384-token
        # portfolio, so admission must fail closed instead of EXECUTE a doomed call.
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 508.0
        tasks = [
            {
                "id": f"TASK-{index}",
                "depends_on": [] if index == 1 else [f"TASK-{index - 1}"],
            }
            for index in range(1, 6)
        ]

        decision = OrchestrationStageExecutor._chief_engineer_deadline_projection_decision(
            {"factory_run_deadline_epoch_seconds": deadline_epoch},
            requested_timeout_seconds=240,
            dependency_schedule=build_task_dependency_schedule(tasks),
        )

        assert decision.disposition is FactoryDeadlineDispositionV1.BLOCK
        assert decision.reason == "insufficient_factory_deadline_for_chief_engineer_portfolio"
        assert decision.reserved_downstream_seconds == 400.0
        assert decision.timeout_seconds == 0

    def test_chief_engineer_deadline_projection_not_used_without_factory_deadline(self) -> None:
        decision = OrchestrationStageExecutor._chief_engineer_deadline_projection_decision(
            {},
            requested_timeout_seconds=123,
            dependency_schedule=build_task_dependency_schedule([{"id": "TASK-1"}]),
        )

        assert decision.disposition is FactoryDeadlineDispositionV1.EXECUTE
        assert decision.remaining_seconds is None
        assert decision.timeout_seconds == 123

    def test_chief_engineer_deadline_projection_blocks_when_available_budget_below_generation_floor(self) -> None:
        # 180s horizon with a reduced downstream reserve (125s) leaves ~50-55s for CE.
        # That exceeds min_start (40s) but is far below the ~205s physical floor to
        # stream the full portfolio, so admission must fail closed.
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 180.0
        decision = OrchestrationStageExecutor._chief_engineer_deadline_projection_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 60,
                "quality_gate_reserved_budget_seconds": 30,
            },
            requested_timeout_seconds=240,
            dependency_schedule=build_task_dependency_schedule([{"id": "TASK-1"}]),
        )

        assert decision.disposition is FactoryDeadlineDispositionV1.BLOCK
        assert decision.reason == "insufficient_factory_deadline_for_chief_engineer_portfolio"
        assert decision.reserved_downstream_seconds == 125.0
        assert decision.timeout_seconds == 0

    def test_chief_engineer_schema_repair_uses_smaller_output_token_floor(self) -> None:
        # The bounded output-schema repair requests only 8192 tokens (floor ~102s),
        # far below the full-portfolio floor (~205s). A budget that is below the
        # portfolio floor but above the repair floor must still admit the repair.
        # 400s horizon, reduced downstream reserve (125s) -> ~272-275s available.
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 400.0
        schedule = build_task_dependency_schedule([{"id": "TASK-1"}])
        portfolio_decision = OrchestrationStageExecutor._chief_engineer_deadline_projection_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 60,
                "quality_gate_reserved_budget_seconds": 30,
            },
            requested_timeout_seconds=240,
            dependency_schedule=schedule,
        )
        repair_decision = OrchestrationStageExecutor._chief_engineer_deadline_projection_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 60,
                "quality_gate_reserved_budget_seconds": 30,
            },
            requested_timeout_seconds=240,
            dependency_schedule=schedule,
            output_tokens=8_192,
        )

        # ~272-275s available: above the portfolio floor -> both EXECUTE; repair floor is smaller.
        assert portfolio_decision.disposition is FactoryDeadlineDispositionV1.EXECUTE
        assert repair_decision.disposition is FactoryDeadlineDispositionV1.EXECUTE

    def test_chief_engineer_schema_repair_floor_admits_where_portfolio_floor_blocks(self) -> None:
        # 230s horizon, reduced downstream reserve (125s) -> ~102-105s available.
        # Below the portfolio floor (~205s) but at/above the repair floor (~102.4s):
        # portfolio must BLOCK, repair must be admitted at the boundary.
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 230.0
        schedule = build_task_dependency_schedule([{"id": "TASK-1"}])
        portfolio_decision = OrchestrationStageExecutor._chief_engineer_deadline_projection_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 60,
                "quality_gate_reserved_budget_seconds": 30,
            },
            requested_timeout_seconds=240,
            dependency_schedule=schedule,
        )
        repair_decision = OrchestrationStageExecutor._chief_engineer_deadline_projection_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 60,
                "quality_gate_reserved_budget_seconds": 30,
            },
            requested_timeout_seconds=240,
            dependency_schedule=schedule,
            output_tokens=8_192,
        )

        assert portfolio_decision.disposition is FactoryDeadlineDispositionV1.BLOCK
        assert portfolio_decision.reason == "insufficient_factory_deadline_for_chief_engineer_portfolio"
        assert repair_decision.disposition is FactoryDeadlineDispositionV1.EXECUTE

    def test_chief_engineer_deadline_projection_skips_llm_when_downstream_budget_is_at_risk(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 120.0
        decision = OrchestrationStageExecutor._chief_engineer_deadline_projection_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 60,
                "quality_gate_reserved_budget_seconds": 30,
            },
            requested_timeout_seconds=240,
            dependency_schedule=build_task_dependency_schedule([{"id": "TASK-1"}]),
        )

        assert decision.disposition is FactoryDeadlineDispositionV1.BLOCK
        assert decision.reason == "insufficient_factory_deadline_for_chief_engineer_portfolio"
        assert decision.timeout_seconds == 0

    def test_chief_engineer_deadline_projection_accounts_for_remaining_ce_fanout(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 360.0
        tasks = [
            {
                "id": f"TASK-{index}",
                "depends_on": [] if index == 1 else [f"TASK-{index - 1}"],
            }
            for index in range(1, 9)
        ]
        decision = OrchestrationStageExecutor._chief_engineer_deadline_projection_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 150,
                "quality_gate_reserved_budget_seconds": 120,
            },
            requested_timeout_seconds=240,
            dependency_schedule=build_task_dependency_schedule(tasks),
        )

        assert decision.disposition is FactoryDeadlineDispositionV1.BLOCK
        assert decision.critical_path_task_count == 8
        assert -237 <= float(decision.available_for_stage_seconds or 0.0) <= -235

    def test_director_dispatch_timeout_caps_to_factory_deadline(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 12.0
        timeout = OrchestrationStageExecutor._director_dispatch_timeout_seconds(
            {
                "director_dispatch_timeout_seconds": 1800,
                "llm_call_timeout_seconds": 1800,
                "factory_run_deadline_epoch_seconds": deadline_epoch,
            },
            task_count=2,
        )

        assert 1 <= timeout <= 12

    def test_director_dispatch_timeout_reserves_quality_gate_budget(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 300.0
        timeout = OrchestrationStageExecutor._director_dispatch_timeout_seconds(
            {
                "director_dispatch_timeout_seconds": 1800,
                "llm_call_timeout_seconds": 1800,
                "factory_run_deadline_epoch_seconds": deadline_epoch,
            },
            task_count=2,
        )

        assert 150 <= timeout <= 180

    def test_director_dispatch_timeout_preserves_quality_budget_during_materialization(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 190.0
        timeout = OrchestrationStageExecutor._director_dispatch_timeout_seconds(
            {
                "director_dispatch_timeout_seconds": 1800,
                "llm_call_timeout_seconds": 1800,
                "factory_run_deadline_epoch_seconds": deadline_epoch,
            },
            task_count=2,
            materialization_pending=True,
        )

        assert 130 <= timeout <= 136

    def test_director_dispatch_timeout_keeps_quality_reserve_after_materialization(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 190.0
        timeout = OrchestrationStageExecutor._director_dispatch_timeout_seconds(
            {
                "director_dispatch_timeout_seconds": 1800,
                "llm_call_timeout_seconds": 1800,
                "factory_run_deadline_epoch_seconds": deadline_epoch,
            },
            task_count=2,
            materialization_pending=False,
        )

        assert 65 <= timeout <= 70

    def test_director_dispatch_timeout_uses_quality_gate_reserve_override(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 300.0
        timeout = OrchestrationStageExecutor._director_dispatch_timeout_seconds(
            {
                "director_dispatch_timeout_seconds": 1800,
                "llm_call_timeout_seconds": 1800,
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "quality_gate_reserved_budget_seconds": 60,
            },
            task_count=2,
        )

        assert 210 <= timeout <= 240

    def test_director_dispatch_deadline_admission_blocks_short_materialization_budget(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 120.0
        decision = OrchestrationStageExecutor._director_dispatch_deadline_admission_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 90,
                "quality_gate_reserved_budget_seconds": 60,
            },
            requested_timeout_seconds=1800,
            first_materialization_pending=True,
            materialization_pending=True,
            dependency_schedule=build_task_dependency_schedule([{"id": "TASK-1"}]),
        )

        assert decision.disposition is FactoryDeadlineDispositionV1.BLOCK
        assert decision.reason == "insufficient_factory_deadline_for_director_dispatch"
        assert decision.timeout_seconds == 0
        assert decision.minimum_start_budget_seconds == 90.0
        assert 50 <= float(decision.available_for_stage_seconds or 0.0) <= 55

    def test_director_dispatch_deadline_admission_allows_sufficient_budget(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 300.0
        decision = OrchestrationStageExecutor._director_dispatch_deadline_admission_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 90,
                "quality_gate_reserved_budget_seconds": 60,
            },
            requested_timeout_seconds=1800,
            first_materialization_pending=True,
            materialization_pending=True,
            dependency_schedule=build_task_dependency_schedule([{"id": "TASK-1"}]),
        )

        assert decision.disposition is FactoryDeadlineDispositionV1.EXECUTE
        assert decision.reason == ""
        assert decision.minimum_start_budget_seconds == 90.0
        assert 230 <= decision.timeout_seconds <= 235

    def test_director_invalid_dependency_schedule_is_not_reported_as_deadline_exhaustion(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 300.0
        decision = OrchestrationStageExecutor._director_dispatch_deadline_admission_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 90,
                "quality_gate_reserved_budget_seconds": 60,
            },
            requested_timeout_seconds=180,
            first_materialization_pending=True,
            materialization_pending=True,
            dependency_schedule=build_task_dependency_schedule(
                [{"id": "TASK-1"}],
                active_task_ids=("TASK-1", "CE-PORTFOLIO-factory-run"),
            ),
        )

        code, detail, status, message = OrchestrationStageExecutor._director_admission_failure_projection(decision)

        assert decision.reason == "invalid_pm_task_dependency_schedule"
        assert code == "director.dispatch_dependency_schedule_blocker"
        assert "unknown_active_task_ids:CE-PORTFOLIO-factory-run" in detail
        assert status == "failed"
        assert "dependency schedule is invalid" in message

    def test_director_dispatch_deadline_admission_uses_standard_budget_after_first_round(self) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 213.0
        decision = OrchestrationStageExecutor._director_dispatch_deadline_admission_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 150,
                "quality_gate_reserved_budget_seconds": 120,
            },
            requested_timeout_seconds=1800,
            first_materialization_pending=False,
            materialization_pending=False,
            dependency_schedule=build_task_dependency_schedule([{"id": "TASK-1"}]),
        )

        assert decision.disposition is FactoryDeadlineDispositionV1.EXECUTE
        assert decision.minimum_start_budget_seconds == stage_executor_module.FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS
        assert 87 <= decision.timeout_seconds <= 88

    def test_r43_later_materialization_wave_uses_same_minimum_qa_reserve_as_timeout_projection(
        self,
    ) -> None:
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 239.0
        decision = OrchestrationStageExecutor._director_dispatch_deadline_admission_decision(
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "director_first_materialization_min_budget_seconds": 90,
                "quality_gate_reserved_budget_seconds": 120,
            },
            requested_timeout_seconds=600,
            first_materialization_pending=False,
            materialization_pending=True,
            dependency_schedule=build_task_dependency_schedule(
                [
                    {"id": "TASK-2", "depends_on": []},
                    {"id": "TASK-3", "depends_on": ["TASK-2"]},
                ]
            ),
        )

        assert decision.disposition is FactoryDeadlineDispositionV1.EXECUTE
        assert decision.minimum_start_budget_seconds == stage_executor_module.FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS
        assert decision.reserved_downstream_seconds == 105
        assert 128 <= decision.execution_timeout_seconds <= 129
        assert decision.settlement_timeout_seconds == 5
        assert decision.reservation_breakdown["qa_finalization"] == 55
        assert decision.reservation_breakdown["qa_finalization_minimum_reserve_active"] == 1

    @pytest.mark.asyncio
    async def test_director_dispatch_deadline_admission_stops_before_llm_turn(self, tmp_path: Path) -> None:
        class _DeadlineAdmissionExecutor(OrchestrationStageExecutor):
            def __init__(self, workspace: Path) -> None:
                super().__init__(workspace)
                self.execute_calls = 0

            def _read_taskboard_stats(self) -> dict[str, int]:
                return {
                    "total": 1,
                    "pending": 1,
                    "ready": 1,
                    "in_progress": 0,
                    "completed": 0,
                    "failed": 0,
                    "blocked": 0,
                }

            def _read_claimable_director_task_ids(self, *, limit: int, factory_run_id: str = "") -> list[str]:
                del limit, factory_run_id
                return ["TASK-1"]

            def _build_orchestration_service(self, context: dict) -> object:
                del context
                executor = self

                class _Service:
                    async def execute_director_run(self, **kwargs: object) -> CommandResult:
                        del kwargs
                        executor.execute_calls += 1
                        return CommandResult(run_id="director-started", status="running", message="submitted")

                return _Service()

            def _validate_director_binding_coverage(self, additional_events=None):  # type: ignore[no-untyped-def]
                del additional_events
                return True, []

        executor = _DeadlineAdmissionExecutor(tmp_path)
        tasks = [{"id": "TASK-1", "target_files": ["src/index.ts"]}]
        executor._write_json_artifact("tasks/plan.json", {"tasks": tasks})
        run = FactoryRun(
            id="run-director-deadline",
            config=FactoryConfig(name="director-deadline"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-22T00:00:00+00:00",
        )
        _write_handoff_ready_review_for_tasks(executor, run_id=run.id, tasks=tasks)
        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 120.0

        result = await executor._execute_director_dispatch(
            run,
            _factory_stage_context(
                {
                    "director_max_rounds": 1,
                    "execution_mode": "parallel",
                    "max_workers": 1,
                    "factory_run_deadline_epoch_seconds": deadline_epoch,
                    "director_first_materialization_min_budget_seconds": 90,
                    "quality_gate_reserved_budget_seconds": 60,
                }
            ),
        )

        assert result.status == "failed"
        assert executor.execute_calls == 0
        payload = json.loads(executor._artifact_path("dispatch/log.json").read_text(encoding="utf-8"))
        assert payload["error_code"] == "director.dispatch_deadline_blocker"
        signal = next(item for item in payload["signals"] if item.get("code") == "director.dispatch_deadline_blocker")
        assert signal["responsible_layer"] == "execution_control_plane"
        assert signal["disposition"] == FactoryDeadlineDispositionV1.BLOCK.value
        assert signal["timeout_seconds"] == 0

    @pytest.mark.asyncio
    async def test_quality_gate_deadline_insufficient_writes_fail_report(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-deadline",
            config=FactoryConfig(name="deadline-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )
        workspace_checks_called = False

        async def fake_workspace_checks(_run: FactoryRun, _context: dict[str, Any]) -> tuple[bool, str]:
            nonlocal workspace_checks_called
            workspace_checks_called = True
            return True, ""

        monkeypatch.setattr(executor, "_run_workspace_quality_checks", fake_workspace_checks)

        def fail_if_qa_started(_context: dict[str, Any]) -> object:
            raise AssertionError("QA orchestration should not start when the factory deadline is exhausted")

        monkeypatch.setattr(executor, "_build_orchestration_service", fail_if_qa_started)

        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 1.0
        result = await executor._execute_quality_gate(
            run,
            _factory_stage_context(
                {
                    "qa_target": "Quality gate",
                    "factory_run_deadline_epoch_seconds": deadline_epoch,
                    "factory_run_timeout_seconds": 540.0,
                    "factory_run_deadline_source": "test",
                }
            ),
        )

        assert result.status == "failed"
        assert workspace_checks_called is False
        assert "factory_quality_gate_deadline_insufficient_before_checks" in result.output
        report_path = Path(resolve_logical_path(tmp_path, "runtime/qa/report.json"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["passed"] is False
        assert report["verdict"] == "NOT_RUN"
        assert report["qa_invoked"] is False
        assert "factory_quality_gate_deadline_insufficient_before_checks" in report["warnings"]
        assert Path(resolve_logical_path(tmp_path, "workspace/qa/latest.report.json")).is_file()

    @pytest.mark.asyncio
    async def test_quality_gate_uses_dynamic_qa_timeout_for_short_but_viable_deadline(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-short-viable-deadline",
            config=FactoryConfig(name="short-viable-deadline-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )
        workspace_checks_called = False
        qa_started = False

        async def fake_workspace_checks(_run: FactoryRun, _context: dict[str, Any]) -> tuple[bool, str]:
            nonlocal workspace_checks_called
            workspace_checks_called = True
            return True, ""

        class _FakeQaService:
            async def execute_qa_run(self, **_kwargs: Any) -> object:
                nonlocal qa_started
                qa_started = True
                return SimpleNamespace(status="running", message="started")

        async def fake_wait_run_completion(*_args: Any, **kwargs: Any) -> object:
            assert 1 <= int(kwargs["timeout_seconds"]) <= 44
            report_path = Path(resolve_logical_path(tmp_path, "runtime/qa/report.json"))
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "passed": True,
                        "score": 95,
                        "critical_issue_count": 0,
                        "warnings": [],
                    }
                ),
                encoding="utf-8",
            )
            return CommandResult(
                run_id="qa-run",
                status="completed",
                message="qa complete",
                metadata={
                    "canonical_authoritative": True,
                    "terminal_source": "task_runtime.execution_fact",
                    "fact_event_seq": 23,
                },
            )

        canonical_projection = _with_task_runtime_authority(
            {
                "source": "run_ledger",
                "integrity_ok": True,
                "outcome_ok": True,
                "task_boundary": {
                    "latest_by_task": {
                        "TASK-1": {
                            "task_id": "TASK-1",
                            "status": "completed_verified",
                            "ok": True,
                            "failure_class": "PASSED",
                            "responsible_layer": "execution_control_plane",
                        }
                    }
                },
                "gates": [
                    {
                        "name": "qa_verdict",
                        "ok": True,
                        "append_id": "qa-append-3",
                        "content_id": "qa-content-3",
                    }
                ],
                "evidence_policy": {
                    "integrity_ok": True,
                    "outcome_ok": True,
                    "missing_required_modalities": [],
                    "failed_required_modalities": [],
                },
            }
        )

        monkeypatch.setattr(executor, "_run_workspace_quality_checks", fake_workspace_checks)
        monkeypatch.setattr(executor, "_build_orchestration_service", lambda _context: _FakeQaService())
        monkeypatch.setattr(executor, "_wait_run_completion", fake_wait_run_completion)
        monkeypatch.setattr(
            executor,
            "_canonical_factory_projection",
            lambda _run, _context: canonical_projection,
        )

        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 44.4
        result = await executor._execute_quality_gate(
            run,
            _factory_stage_context(
                {
                    "qa_target": "Quality gate",
                    "factory_run_deadline_epoch_seconds": deadline_epoch,
                    "factory_run_timeout_seconds": 540.0,
                    "factory_run_deadline_source": "test",
                }
            ),
        )

        assert result.status == "success"
        assert workspace_checks_called is True
        assert qa_started is True
        assert "deadline_insufficient" not in str(result.output)

    @pytest.mark.asyncio
    async def test_quality_gate_report_missing_does_not_replace_canonical_verdict(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-report-missing",
            config=FactoryConfig(name="missing-report-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        async def fake_workspace_checks(_run: FactoryRun, _context: dict[str, Any]) -> tuple[bool, str]:
            executor._write_json_artifact("runtime/qa/workspace-validation.json", {"passed": True})
            return True, "runtime/qa/workspace-validation.json"

        class FakeQAService:
            async def execute_qa_run(self, **_kwargs: Any) -> CommandResult:
                return CommandResult(run_id="qa-run", status="running", message="started")

        async def fake_wait_run_completion(*_args: Any, **_kwargs: Any) -> CommandResult:
            return CommandResult(run_id="qa-run", status="completed", message="done")

        monkeypatch.setattr(executor, "_run_workspace_quality_checks", fake_workspace_checks)
        monkeypatch.setattr(executor, "_build_orchestration_service", lambda _context: FakeQAService())
        monkeypatch.setattr(executor, "_wait_run_completion", fake_wait_run_completion)

        result = await executor._execute_quality_gate(
            run,
            _factory_stage_context({"qa_target": "Quality gate"}),
        )

        assert result.status == "failed"
        assert "canonical_reason=task_runtime_contract_scope_mismatch" in result.output
        assert "report_ready=False" in result.output
        report_path = Path(resolve_logical_path(tmp_path, "runtime/qa/report.json"))
        assert report_path.exists() is False
        assert Path(resolve_logical_path(tmp_path, "workspace/qa/latest.report.json")).exists() is False

    @pytest.mark.asyncio
    async def test_quality_gate_physical_pass_commits_without_qa_llm(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-physical-qa",
            config=FactoryConfig(name="physical-qa-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-11T00:00:00+00:00",
        )

        async def fake_workspace_checks(_run: FactoryRun, _context: dict[str, Any]) -> tuple[bool, str]:
            executor._write_json_artifact("runtime/qa/workspace-validation.json", {"passed": True})
            return True, "runtime/qa/workspace-validation.json"

        committed_payloads: list[dict[str, Any]] = []

        async def fake_commit(**kwargs: Any) -> dict[str, Any]:
            committed_payloads.append(dict(kwargs["qa_payload"]))
            return {"success": True, "task_id": "TASK-1", "run_id": "director-1"}

        authority = SimpleNamespace(
            quality_stage_authorized=True,
            task_boundary_completed_verified=True,
            qa_verdict_present=True,
            qa_verdict_passed=True,
            sequence_barrier_satisfied=True,
            evidence_policy_passed=True,
            recovered_runtime_task_ids=(),
            reason_code="completed_verified",
        )

        async def fake_wait_for_authority(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
            return authority

        monkeypatch.setattr(executor, "_run_workspace_quality_checks", fake_workspace_checks)
        monkeypatch.setattr(executor, "_canonical_qa_commit_identity", lambda **_kwargs: ("TASK-1", "director-1"))
        monkeypatch.setattr(executor, "_commit_qa_role_report_authority", fake_commit)
        monkeypatch.setattr(
            executor,
            "_build_orchestration_service",
            lambda _context: pytest.fail("physical QA must not start an LLM role run"),
        )
        monkeypatch.setattr(
            executor,
            "_canonical_factory_projection",
            lambda _run, _context: {"task_boundary": {"latest_by_task": {}}},
        )
        monkeypatch.setattr(
            stage_executor_module.helpers,
            "evaluate_canonical_factory_authority",
            lambda _projection: SimpleNamespace(qa_verdict_present=False),
        )
        monkeypatch.setattr(executor, "_wait_for_canonical_quality_authority", fake_wait_for_authority)
        monkeypatch.setattr(
            executor,
            "_reconcile_verified_runtime_delivery",
            lambda **_kwargs: {"success": True, "reconciled_task_ids": []},
        )

        result = await executor._execute_quality_gate(run, _factory_stage_context({"qa_target": "Quality gate"}))

        assert result.status == "success"
        assert committed_payloads[0]["source"] == "factory_physical_verifier"
        assert committed_payloads[0]["llm_invoked"] is False
        assert "advisory QA LLM not required" in result.output
        report = json.loads(Path(resolve_logical_path(tmp_path, "runtime/qa/report.json")).read_text(encoding="utf-8"))
        assert report["verdict"] == "PASS"
        assert report["qa_invoked"] is False
        assert report["llm_invoked"] is False

    @pytest.mark.asyncio
    async def test_quality_gate_workspace_validation_failure_skips_advisory_qa_judgement(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-workspace-fail",
            config=FactoryConfig(name="workspace-fail-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        async def fake_workspace_checks(_run: FactoryRun, _context: dict[str, Any]) -> tuple[bool, str]:
            executor._write_json_artifact(
                "runtime/qa/workspace-validation.json",
                {
                    "passed": False,
                    "commands": [
                        {
                            "command": ["npm", "run", "start"],
                            "passed": False,
                            "stderr_tail": "ReferenceError: exports is not defined in ES module scope",
                        }
                    ],
                    "repair": {
                        "residual_errors": [
                            "Artifact quality scan failed: workspace validation command failed (npm run start)"
                        ]
                    },
                },
            )
            return False, "runtime/qa/workspace-validation.json"

        qa_calls: list[dict[str, Any]] = []

        class _CapturingQaService:
            async def execute_qa_run(self, **kwargs: Any) -> CommandResult:
                qa_calls.append(dict(kwargs))
                executor._write_json_artifact(
                    "runtime/qa/report.json",
                    {
                        "passed": False,
                        "verdict": "FAIL",
                        "score": 0,
                        "critical_issue_count": 1,
                        "critical_issues": ["workspace_quality_gate_failed"],
                        "warnings": [],
                    },
                )
                return CommandResult(
                    run_id="qa-workspace-failure",
                    status="running",
                    message="QA run started",
                )

            async def query_run_status(self, run_id: str) -> CommandResult:
                return CommandResult(run_id=run_id, status="completed", message="QA completed")

        monkeypatch.setattr(executor, "_run_workspace_quality_checks", fake_workspace_checks)
        monkeypatch.setattr(executor, "_build_orchestration_service", lambda _context: _CapturingQaService())

        result = await executor._execute_quality_gate(
            run,
            _factory_stage_context({"qa_target": "Quality gate"}),
        )

        assert result.status == "failed"
        assert "factory_quality_gate_workspace_validation_failed" in result.output
        assert "ReferenceError: exports is not defined in ES module scope" in result.output
        assert qa_calls == []
        report_path = Path(resolve_logical_path(tmp_path, "runtime/qa/report.json"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["passed"] is False
        assert report["verdict"] == "NOT_RUN"
        assert report["qa_invoked"] is False
        assert report["workspace_checks_passed"] is False
        assert Path(resolve_logical_path(tmp_path, "workspace/qa/latest.report.json")).is_file()

    @pytest.mark.asyncio
    async def test_quality_gate_workspace_failure_without_artifact_fails_closed_before_qa(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-workspace-fail-no-artifact",
            config=FactoryConfig(name="workspace-fail-no-artifact"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        async def fake_workspace_checks(_run: FactoryRun, _context: dict[str, Any]) -> tuple[bool, str]:
            return False, ""

        def fail_if_qa_started(_context: dict[str, Any]) -> object:
            raise AssertionError("QA must not start when hard-verifier evidence is missing")

        monkeypatch.setattr(executor, "_run_workspace_quality_checks", fake_workspace_checks)
        monkeypatch.setattr(executor, "_build_orchestration_service", fail_if_qa_started)

        result = await executor._execute_quality_gate(
            run,
            _factory_stage_context({"qa_target": "Quality gate"}),
        )

        assert result.status == "failed"
        assert "without an authoritative evidence artifact" in result.output
        report = json.loads(Path(resolve_logical_path(tmp_path, "runtime/qa/report.json")).read_text(encoding="utf-8"))
        assert report["passed"] is False
        assert report["workspace_checks_passed"] is False

    @pytest.mark.asyncio
    async def test_workspace_quality_deadline_insufficient_writes_validation_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-workspace-repair-deadline",
            config=FactoryConfig(name="workspace-repair-deadline-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda _context: [["npm", "run", "build"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda _commands, _context: [])
        monkeypatch.setattr(
            executor,
            "_run_workspace_quality_command",
            lambda _command, _timeout: {
                "command": ["npm", "run", "build"],
                "exit_code": 2,
                "passed": False,
                "stdout_tail": "src/main.ts(1,1): error TS2353: Object literal may only specify known properties.",
                "stderr_tail": "",
            },
        )

        async def fail_if_deterministic_repair_started(
            **_kwargs: Any,
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            raise AssertionError("workspace quality deterministic repair must not claim after deadline admission fails")

        monkeypatch.setattr(
            executor,
            "_apply_workspace_quality_deterministic_repairs",
            fail_if_deterministic_repair_started,
        )
        monkeypatch.setattr(
            executor,
            "_canonical_factory_projection",
            lambda _run, _context: _with_task_runtime_authority(
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

        async def fail_if_llm_repair_started(**_kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            raise AssertionError("workspace quality LLM repair should not start when deadline is insufficient")

        monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", fail_if_llm_repair_started)

        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 20.0
        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "factory_run_timeout_seconds": 540.0,
                "factory_run_deadline_source": "test",
            },
        )

        assert passed is False
        assert artifact == "runtime/qa/workspace-validation.json"
        payload = json.loads(Path(resolve_logical_path(tmp_path, artifact)).read_text(encoding="utf-8"))
        assert payload["passed"] is False
        assert "factory_quality_gate_workspace_checks_deadline_insufficient" in payload["warnings"]
        assert "remaining" in payload["error"]

    @pytest.mark.asyncio
    async def test_workspace_quality_command_timeout_preserves_qa_budget(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-workspace-command-deadline",
            config=FactoryConfig(name="workspace-command-deadline-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )
        observed_timeouts: list[float] = []

        monkeypatch.setattr(executor, "_workspace_quality_commands", lambda _context: [["npm", "run", "build"]])
        monkeypatch.setattr(executor, "_workspace_quality_prepare_commands", lambda _commands, _context: [])
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", lambda _context: None)
        monkeypatch.setattr(
            executor,
            "_canonical_factory_projection",
            lambda _run, _context: _with_task_runtime_authority(
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

        def fake_run_workspace_quality_command(command: list[str], timeout_seconds: float) -> dict[str, object]:
            observed_timeouts.append(timeout_seconds)
            return {
                "command": command,
                "exit_code": 0,
                "passed": True,
                "stdout_tail": "build passed",
                "stderr_tail": "",
                "error": "",
            }

        monkeypatch.setattr(executor, "_run_workspace_quality_command", fake_run_workspace_quality_command)

        deadline_epoch = stage_executor_module.datetime.now(stage_executor_module.timezone.utc).timestamp() + 70.0
        passed, artifact = await executor._run_workspace_quality_checks(
            run,
            {
                "factory_run_deadline_epoch_seconds": deadline_epoch,
                "factory_run_timeout_seconds": 540.0,
                "factory_run_deadline_source": "test",
            },
        )

        assert passed is True
        assert observed_timeouts
        assert 1.0 <= observed_timeouts[0] <= 26.0
        payload = json.loads(Path(resolve_logical_path(tmp_path, artifact)).read_text(encoding="utf-8"))
        command = payload["commands"][0]
        assert command["deadline_capped_timeout_seconds"] <= 26.0
        assert command["configured_timeout_seconds"] == 240.0

    def test_workspace_quality_allows_durably_prepared_same_task_rework_owner(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-prepared-local-rework",
            config=FactoryConfig(name="prepared-local-rework-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-14T00:00:00+00:00",
        )
        projection = _with_task_runtime_authority(
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
            },
            incomplete_task_ids=("TASK-1",),
        )
        action = {
            "schema_version": "task-runtime.same-task-local-rework-record/1",
            "factory_run_id": run.id,
            "external_task_id": "TASK-1",
            "action_id": "a" * 64,
            "action_kind": "run_required_verifier",
            "dispatch_claim_id": "b" * 64,
            "effect_hash": "c" * 64,
            "diagnostic": {
                "owner_task_id": "TASK-1",
                "allowed_next_action": "run_required_verifier",
            },
        }
        projection["task_runtime_projection"]["rows"][0]["metadata"] = {
            "factory_local_rework": action,
            "same_task_local_rework_authorizations": [dict(action)],
        }
        monkeypatch.setattr(executor, "_canonical_factory_projection", lambda _run, _context: projection)

        assert executor._workspace_quality_task_boundary_blocker(run, {}) is None

    def test_workspace_quality_allows_quality_residual_failed_owners_on_qa_retry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Failed workspace_quality_* rows must not deadlock a same-run qa_gate retry."""

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-quality-residual-retry",
            config=FactoryConfig(name="quality-residual-retry-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-14T00:00:00+00:00",
        )
        projection = _with_task_runtime_authority(
            {
                "source": "run_ledger",
                "task_boundary": {
                    "latest_by_task": {
                        "TASK-1": {"task_id": "TASK-1", "status": "completed_verified", "ok": True},
                        "TASK-2": {"task_id": "TASK-2", "status": "completed_verified", "ok": True},
                        "TASK-3": {"task_id": "TASK-3", "status": "completed_verified", "ok": True},
                    }
                },
            },
            task_ids=("TASK-1", "TASK-2", "TASK-3"),
            incomplete_task_ids=("TASK-1", "TASK-2", "TASK-3"),
        )
        action = {
            "schema_version": "task-runtime.same-task-local-rework-record/1",
            "factory_run_id": run.id,
            "external_task_id": "TASK-1",
            "action_id": "a" * 64,
            "action_kind": "run_required_verifier",
            "dispatch_claim_id": "b" * 64,
            "effect_hash": "c" * 64,
            "diagnostic": {
                "owner_task_id": "TASK-1",
                "allowed_next_action": "run_required_verifier",
            },
        }
        rows = projection["task_runtime_projection"]["rows"]
        rows[0]["metadata"] = {
            "factory_local_rework": action,
            "same_task_local_rework_authorizations": [dict(action)],
        }
        rows[0]["claimed_by"] = "director"
        for row in (rows[1], rows[2]):
            row["status"] = "failed"
            row["execution_state"] = "failed"
            row["claimed_by"] = "director"
        monkeypatch.setattr(executor, "_canonical_factory_projection", lambda _run, _context: projection)

        assert executor._workspace_quality_task_boundary_blocker(run, {}) is None

    def test_workspace_quality_still_blocks_failed_owners_without_completed_boundary(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-director-failed-owner",
            config=FactoryConfig(name="director-failed-owner-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-14T00:00:00+00:00",
        )
        projection = _with_task_runtime_authority(
            {
                "source": "run_ledger",
                "task_boundary": {},
            },
            incomplete_task_ids=("TASK-1",),
        )
        row = projection["task_runtime_projection"]["rows"][0]
        row["status"] = "failed"
        row["execution_state"] = "failed"
        row["claimed_by"] = "director"
        monkeypatch.setattr(executor, "_canonical_factory_projection", lambda _run, _context: projection)

        blocker = executor._workspace_quality_task_boundary_blocker(run, {})
        assert blocker is not None
        assert blocker["reason_code"] in {
            "task_runtime_not_converged",
            "task_boundary_verdict_missing",
        }

    def test_workspace_quality_admits_director_dispatch_residuals_without_completed_boundary(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Same-run qa_gate must measure after Director residuals, not skip commands.

        Live L2-15 remint-8: director_dispatch already succeeded; quality then
        settled engine/models as ``workspace_quality_repair_*`` and left
        ``src/main.cpp`` blocked on ``factory_restart_recovery_expired_child_session``.
        Compact rows omit last_execution_error and TaskBoundary is not
        completed_verified, so treating those residuals as incomplete
        materialization deadlocks the verifier (ncmd=0).
        """

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-l215-quality-residual",
            config=FactoryConfig(name="l215-quality-residual-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-16T00:00:00+00:00",
        )
        run.metadata["last_successful_stage"] = "director_dispatch"
        run.stages_completed = ["pm_planning", "chief_engineer_review", "director_dispatch"]
        projection = _with_task_runtime_authority(
            {
                "source": "run_ledger",
                "task_boundary": {
                    "latest_by_task": {
                        "TASK-5": {
                            "task_id": "TASK-5",
                            "status": "completed_verified",
                            "ok": True,
                        }
                    }
                },
            },
            task_ids=("TASK-1", "TASK-2", "TASK-3", "TASK-4", "TASK-5"),
            incomplete_task_ids=("TASK-1", "TASK-2", "TASK-3", "TASK-4"),
        )
        rows = projection["task_runtime_projection"]["rows"]
        for row, state, error in (
            (rows[0], "failed", "workspace_quality_repair_equal_count_swap"),
            (rows[1], "failed", "workspace_quality_repair_equal_count_swap"),
            (rows[2], "failed", "workspace_quality_repair_forward_unmask"),
            (rows[3], "blocked", "factory_restart_recovery_expired_child_session"),
        ):
            row["status"] = state
            row["execution_state"] = state
            row["claimed_by"] = "director"
            row["last_execution_error"] = error
        monkeypatch.setattr(executor, "_canonical_factory_projection", lambda _run, _context: projection)

        assert executor._workspace_quality_task_boundary_blocker(run, {}) is None

    def test_workspace_quality_still_blocks_unprepared_pending_after_director_dispatch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """director_dispatch success does not unlock never-started source rows."""

        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-pending-after-director",
            config=FactoryConfig(name="pending-after-director-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-16T00:00:00+00:00",
        )
        run.metadata["last_successful_stage"] = "director_dispatch"
        projection = _with_task_runtime_authority(
            {
                "source": "run_ledger",
                "task_boundary": {},
            },
            incomplete_task_ids=("TASK-1",),
        )
        monkeypatch.setattr(executor, "_canonical_factory_projection", lambda _run, _context: projection)

        blocker = executor._workspace_quality_task_boundary_blocker(run, {})
        assert blocker is not None
        assert blocker["reason_code"] in {
            "task_runtime_not_converged",
            "task_boundary_verdict_missing",
        }

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("factory_run_id", "other-run"),
            ("action_kind", "wait_for_dependencies"),
            ("effect_hash", "not-a-hash"),
        ],
    )
    def test_workspace_quality_rejects_unbound_or_nonexecutable_local_rework_owner(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        field: str,
        value: str,
    ) -> None:
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-invalid-local-rework",
            config=FactoryConfig(name="invalid-local-rework-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-08-14T00:00:00+00:00",
        )
        projection = _with_task_runtime_authority(
            {
                "source": "run_ledger",
                "task_boundary": {
                    "latest_by_task": {
                        "TASK-2": {
                            "task_id": "TASK-2",
                            "status": "completed_verified",
                            "ok": True,
                        }
                    }
                },
            },
            task_ids=("TASK-1", "TASK-2"),
            incomplete_task_ids=("TASK-1",),
        )
        action = {
            "schema_version": "task-runtime.same-task-local-rework-record/1",
            "factory_run_id": run.id,
            "external_task_id": "TASK-1",
            "action_id": "a" * 64,
            "action_kind": "run_required_verifier",
            "dispatch_claim_id": "b" * 64,
            "effect_hash": "c" * 64,
            "diagnostic": {
                "owner_task_id": "TASK-1",
                "allowed_next_action": "run_required_verifier",
            },
        }
        action[field] = value
        if field == "action_kind":
            action["diagnostic"]["allowed_next_action"] = value
        projection["task_runtime_projection"]["rows"][0]["metadata"] = {
            "factory_local_rework": action,
            "same_task_local_rework_authorizations": [dict(action)],
        }
        monkeypatch.setattr(executor, "_canonical_factory_projection", lambda _run, _context: projection)

        blocker = executor._workspace_quality_task_boundary_blocker(run, {})

        assert blocker is not None
        assert blocker["reason_code"] == "task_runtime_not_converged"

    @pytest.mark.asyncio
    async def test_workspace_quality_skips_full_project_checks_when_source_tasks_not_unlocked(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {
                        "build": "tsc -p tsconfig.json",
                        "test": "vitest run",
                        "start": "npm run build && node dist/main.js",
                    },
                    "devDependencies": {"typescript": "^5.4.5", "vitest": "^1.6.0"},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (tmp_path / "tsconfig.json").write_text(
            json.dumps({"include": ["src/**/*.ts", "tests/**/*.ts"]}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-source-task-blocked",
            config=FactoryConfig(name="source-task-blocked-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        def fail_if_command_runs(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del command, timeout_seconds
            raise AssertionError("workspace quality commands must not run before source tasks unlock")

        def fail_if_depth_runs(_context: dict[str, Any]) -> dict[str, Any] | None:
            raise AssertionError("delivery depth must not run before source tasks unlock")

        def fail_if_runtime_repair_runs(**_kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            raise AssertionError("runtime repair must not run before source tasks unlock")

        async def fail_if_llm_repair_runs(**_kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            raise AssertionError("LLM repair must not run before source tasks unlock")

        monkeypatch.setattr(
            executor,
            "_canonical_factory_projection",
            lambda _run, _context: _with_task_runtime_authority(
                {
                    "source": "run_ledger",
                    "task_boundary": {
                        "latest_by_task": {
                            "TASK-1": {
                                "task_id": "TASK-1",
                                "status": "completed_verified",
                                "ok": True,
                            },
                            "TASK-2": {
                                "task_id": "TASK-2",
                                "status": "dependency_not_unlocked",
                                "ok": False,
                                "failure_class": "DEPENDENCY_NOT_UNLOCKED",
                                "responsible_layer": "task_boundary",
                            },
                        }
                    },
                },
                task_ids=("TASK-1", "TASK-2"),
                incomplete_task_ids=("TASK-2",),
            ),
        )
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", fail_if_depth_runs)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fail_if_command_runs)
        monkeypatch.setattr(executor, "_apply_workspace_quality_repairs", fail_if_runtime_repair_runs)
        monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", fail_if_llm_repair_runs)

        passed, artifact = await executor._run_workspace_quality_checks(run, {})

        assert passed is False
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["commands"] == []
        assert payload["commands_skipped"] is True
        assert payload["failure_class"] == "DEPENDENCY_NOT_UNLOCKED"
        assert payload["responsible_layer"] == "task_boundary"
        assert payload["repair"]["attempted"] is False
        assert payload["repair"]["reason"] == "task_boundary_not_ready"
        assert payload["task_boundary_blocker"]["incomplete_task_ids"] == ["TASK-2"]
        assert "task_boundary_not_completed_verified" in payload["warnings"]

    @pytest.mark.asyncio
    async def test_workspace_quality_skips_checks_when_declared_test_targets_not_unlocked(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "index.ts").write_text("export const ready = true;\n", encoding="utf-8")
        (tmp_path / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {
                        "build": "tsc -p tsconfig.json",
                        "test": "vitest run tests/simulation.test.ts tests/verify.test.ts",
                        "start": "vite --host 127.0.0.1",
                    },
                    "devDependencies": {"typescript": "^5.4.5", "vitest": "^1.6.0", "vite": "^5.4.0"},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (tmp_path / "tsconfig.json").write_text(
            json.dumps({"include": ["src/**/*.ts", "tests/**/*.ts"]}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        executor = _executor(tmp_path)
        run = FactoryRun(
            id="run-tests-blocked",
            config=FactoryConfig(name="tests-blocked-run"),
            status=FactoryRunStatus.RUNNING,
            created_at="2026-06-25T00:00:00+00:00",
        )

        def fail_if_command_runs(command: list[str], timeout_seconds: float) -> dict[str, object]:
            del command, timeout_seconds
            raise AssertionError("workspace quality commands must not run before declared test targets unlock")

        def fail_if_depth_runs(_context: dict[str, Any]) -> dict[str, Any] | None:
            raise AssertionError("delivery depth must not run before declared test targets unlock")

        def fail_if_runtime_repair_runs(**_kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            raise AssertionError("runtime repair must not run before declared test targets unlock")

        async def fail_if_llm_repair_runs(**_kwargs: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            raise AssertionError("LLM repair must not run before declared test targets unlock")

        monkeypatch.setattr(
            executor,
            "_canonical_factory_projection",
            lambda _run, _context: _with_task_runtime_authority(
                {
                    "source": "run_ledger",
                    "task_boundary": {
                        "latest_by_task": {
                            "TASK-1": {
                                "task_id": "TASK-1",
                                "status": "completed_verified",
                                "ok": True,
                            },
                            "TASK-2": {
                                "task_id": "TASK-2",
                                "status": "dependency_not_unlocked",
                                "ok": False,
                                "failure_class": "DEPENDENCY_NOT_UNLOCKED",
                                "responsible_layer": "task_boundary",
                            },
                            "TASK-3": {
                                "task_id": "TASK-3",
                                "status": "dependency_not_unlocked",
                                "ok": False,
                                "failure_class": "DEPENDENCY_NOT_UNLOCKED",
                                "responsible_layer": "task_boundary",
                            },
                        }
                    },
                },
                task_ids=("TASK-1", "TASK-2", "TASK-3"),
                incomplete_task_ids=("TASK-2", "TASK-3"),
            ),
        )
        monkeypatch.setattr(executor._workspace_quality, "delivery_depth_contract_result", fail_if_depth_runs)
        monkeypatch.setattr(executor, "_run_workspace_quality_command", fail_if_command_runs)
        monkeypatch.setattr(executor, "_apply_workspace_quality_repairs", fail_if_runtime_repair_runs)
        monkeypatch.setattr(executor, "_apply_workspace_quality_llm_repairs", fail_if_llm_repair_runs)

        passed, artifact = await executor._run_workspace_quality_checks(run, {})

        assert passed is False
        payload = json.loads(executor._artifact_path(artifact).read_text(encoding="utf-8"))
        assert payload["commands"] == []
        assert payload["commands_skipped"] is True
        assert payload["failure_class"] == "DEPENDENCY_NOT_UNLOCKED"
        assert payload["responsible_layer"] == "task_boundary"
        assert payload["task_boundary_blocker"]["incomplete_task_ids"] == ["TASK-2", "TASK-3"]
        assert "task_boundary_not_completed_verified" in payload["warnings"]


# ---------------------------------------------------------------------------
# package.json parsing
# ---------------------------------------------------------------------------
