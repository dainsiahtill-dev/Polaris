"""Tests for FactoryRunService and FactoryStore."""

import asyncio
import hashlib
import json
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    ArtifactObligationV1,
    BlueprintPersistence,
    EntrypointObligationV1,
    ProjectCompletionObligationsV1,
    VerificationCommandAuthorityV1,
    VerificationObligationV1,
    build_project_completion_contract,
    derive_project_kind_authority_from_catalog_snapshot,
    project_completion_catalog_snapshot_hash,
    project_completion_verifier_policy_snapshot_hash,
)
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.factory.pipeline.internal import (
    factory_run_service as factory_service_module,
    factory_stage_executor as factory_stage_module,
)
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    FactoryConfig,
    FactoryRun,
    FactoryRunService,
    FactoryRunStatus,
    OrchestrationStageExecutor,
    StageResult,
)
from polaris.cells.factory.pipeline.internal.factory_store import FactoryStore
from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import CommandResult
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
    FactoryRoleEvidenceAuthorityBindingV1,
    get_factory_role_evidence_authority_binding,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    BindRuntimeTaskToFactoryRunCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.kernelone.storage import resolve_logical_path, resolve_runtime_path, resolve_storage_roots


def _complete_task_row(
    task_runtime: TaskRuntimeService,
    task_id: Any,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    claimed = task_runtime.claim_execution(
        task_id,
        worker_id="test",
        role_id="director",
        selection_source="factory_run_service_test",
    )
    assert claimed["success"] is True
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(claimed["execution_attempt"])
    completed = task_runtime.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            outcome="completed",
            summary="test completed",
            metadata=metadata or {},
        )
    )
    assert completed["success"] is True
    return completed


def _chief_engineer_portfolio_output(
    *,
    task_id: str = "TASK-1",
    scope_path: str = "src/account",
) -> dict[str, Any]:
    """Return one valid project-level CE portfolio response for test runtimes."""

    build_authority = VerificationCommandAuthorityV1(
        task_id=task_id,
        modality="build",
        argv=("python", "-m", "compileall", "."),
    )
    test_authority = VerificationCommandAuthorityV1(
        task_id=task_id,
        modality="test",
        argv=("pytest", "-q"),
    )
    test_path = f"tests/test_{Path(scope_path).stem}.py"

    return {
        "construction_plan": {
            "project_design_intent": "Keep domain behavior behind stable task-owned interfaces.",
            "project_interface_contract": {
                "provider_declarations": [],
                "consumer_declarations": [],
            },
            "task_plans": {
                task_id: {
                    "preparation": ["Confirm the task-owned module boundary"],
                    "implementation": ["Implement the declared domain behavior"],
                    "verification": ["Run the declared acceptance checks"],
                }
            },
        },
        "scope_for_apply": [scope_path],
        "risk_flags": [],
        "project_completion_contract": {
            "obligations": {
                "artifacts": [
                    {
                        "obligation_id": "artifact-task-1",
                        "path": scope_path,
                        "semantic_role": "source",
                        "applicability": "required",
                        "owner_task_id": task_id,
                    },
                    {
                        "obligation_id": "artifact-task-1-test",
                        "path": test_path,
                        "semantic_role": "test",
                        "applicability": "required",
                        "owner_task_id": task_id,
                    },
                ],
                "entrypoints": [
                    {
                        "obligation_id": "entrypoint-library-na",
                        "kind": "library",
                        "applicability": "not_applicable",
                        "owner_task_id": None,
                        "source_path": None,
                        "runtime_path": None,
                        "command": None,
                    }
                ],
                "verification": [
                    {
                        "obligation_id": "verify-build",
                        "modality": "build",
                        "command_authority_hash": build_authority.authority_hash,
                        "applicability": "required",
                        "covers_obligation_ids": ["artifact-task-1"],
                        "owner_task_id": task_id,
                    },
                    {
                        "obligation_id": "verify-test",
                        "modality": "test",
                        "command_authority_hash": test_authority.authority_hash,
                        "applicability": "required",
                        "covers_obligation_ids": ["artifact-task-1-test"],
                        "owner_task_id": task_id,
                    },
                    {
                        "obligation_id": "verify-environment-na",
                        "modality": "environment_prep",
                        "command_authority_hash": None,
                        "applicability": "not_applicable",
                        "covers_obligation_ids": [],
                        "owner_task_id": None,
                    },
                ],
            }
        },
    }


class FakeStageExecutor:
    """Deterministic stage executor for FactoryRunService tests."""

    def __init__(self, fail_stages: set[str] | None = None) -> None:
        self.fail_stages = fail_stages or set()

    async def execute(self, stage: str, run: FactoryRun, context: dict) -> StageResult:
        if stage in self.fail_stages:
            return StageResult(
                stage=stage,
                status="failed",
                output=f"{stage} failed",
                artifacts=[],
            )

        return StageResult(
            stage=stage,
            status="success",
            output=f"{stage} completed",
            artifacts=[f"artifacts/{stage}.json"],
        )


class SlowStageExecutor:
    """Slow executor used to validate cancellation/heartbeat behavior."""

    def __init__(self, sleep_seconds: float = 0.2) -> None:
        self.sleep_seconds = sleep_seconds

    async def execute(self, stage: str, run: FactoryRun, context: dict) -> StageResult:
        await asyncio.sleep(self.sleep_seconds)
        return StageResult(
            stage=stage,
            status="success",
            output=f"{stage} completed slowly",
            artifacts=[],
        )


class ProviderHttpErrorStageExecutor:
    """Raises a provider-shaped HTTP error (mirrors aiohttp.ClientResponseError)."""

    def __init__(self, message: str) -> None:
        self.message = message

    async def execute(self, stage: str, run: FactoryRun, context: dict) -> StageResult:
        del stage, run, context
        # Do not import aiohttp here: the production path must catch any Exception
        # subclass, not only aiohttp's type. A plain Exception with the real
        # production message is the regression fixture for the R49 hang.
        raise Exception(self.message)


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace"""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir).resolve()
        bootstrap_fact_stream_workspace(
            BootstrapFactStreamWorkspaceCommandV1(
                workspace=str(workspace),
                streams=fact_stream_bootstrap_streams(),
                maintenance_reason="factory_run_service_test_bootstrap",
            )
        )
        yield workspace


@pytest.fixture
async def service(temp_workspace):
    """Create a FactoryRunService instance"""
    svc = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
    yield svc


@pytest.fixture
def store(temp_workspace):
    """Create a FactoryStore instance"""
    return FactoryStore(temp_workspace / ".polaris" / "factory")




class TestOrchestrationStageExecutor:
    def test_declared_delivery_targets_filter_directory_scope_paths(self, temp_workspace):
        executor = _TestStageExecutor(temp_workspace, _ImmediateFailureCommandService())

        targets = executor._collect_declared_delivery_targets(
            [
                {
                    "target_files": ["calculator.py", "tests/test_calculator.py"],
                    "scope_paths": ["calculator.py", "tests/test_calculator.py", "tests"],
                    "scope": "calculator.py, tests/",
                }
            ]
        )

        assert targets == ["calculator.py", "tests/test_calculator.py"]

    @pytest.mark.asyncio
    async def test_wait_run_completion_short_circuits_immediate_failure(self, temp_workspace):
        command_service = _ImmediateFailureCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        initial = CommandResult(
            run_id="pm-test-001",
            status="failed",
            message="No module named 'pytz'",
            reason_code="PM_RUN_FAILED",
        )

        result = await executor._wait_run_completion(command_service, initial, timeout_seconds=60)

        assert result is initial
        assert command_service.queried is False

    @pytest.mark.asyncio
    async def test_wait_run_completion_honors_initial_abort_checker(self, temp_workspace):
        command_service = _NeverTerminalCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        initial = CommandResult(
            run_id="pm-test-cancelled",
            status="running",
            message="PM run started",
        )

        async def _abort_checker() -> str | None:
            return "operator stop"

        result = await executor._wait_run_completion(
            command_service,
            initial,
            timeout_seconds=60,
            abort_checker=_abort_checker,
        )

        assert result.status == "cancelled"
        assert "operator stop" in str(result.message)
        assert command_service.query_calls == 0

    @pytest.mark.asyncio
    async def test_docs_stage_uses_extended_default_timeout_budget(self, temp_workspace, monkeypatch):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_docs_timeout_budget",
            config=FactoryConfig(name="test-run", stages=["docs_generation"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        captured: dict[str, int] = {}

        async def _fake_wait(service, command_result, timeout_seconds, cancel_event=None, abort_checker=None):
            del service, cancel_event, abort_checker
            captured["timeout_seconds"] = int(timeout_seconds)
            return CommandResult(
                run_id=command_result.run_id,
                status="completed",
                message="Run status: completed",
                metadata={},
            )

        monkeypatch.setattr(executor, "_wait_run_completion", _fake_wait)
        monkeypatch.setattr(executor, "_ensure_docs_artifacts", lambda directive, summary: [])
        monkeypatch.setattr(executor, "_artifact_exists", lambda relative_path, min_chars=1: True)

        result = await executor._execute_docs_generation(run, context={"directive": "Generate docs"})

        assert result.status == "success"
        assert captured["timeout_seconds"] == 600
        assert command_service.observed_bindings[-1] is executor._test_role_evidence_port.bindings[-1]
        assert command_service.observed_bindings[-1].role == "architect"
        assert get_factory_role_evidence_authority_binding() is None

    @pytest.mark.asyncio
    async def test_pm_stage_uses_extended_default_timeout_budget(self, temp_workspace, monkeypatch):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_pm_timeout_budget",
            config=FactoryConfig(name="test-run", stages=["pm_planning"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        captured: dict[str, int] = {}

        async def _fake_wait(service, command_result, timeout_seconds, cancel_event=None, abort_checker=None):
            del service, cancel_event, abort_checker
            captured["timeout_seconds"] = int(timeout_seconds)
            return CommandResult(
                run_id=command_result.run_id,
                status="completed",
                message="Run status: completed",
                metadata={},
            )

        monkeypatch.setattr(executor, "_wait_run_completion", _fake_wait)
        monkeypatch.setattr(executor, "_validate_pm_plan_contract", lambda relative_path: None)
        monkeypatch.setattr(executor, "_artifact_exists", lambda relative_path, min_chars=1: True)

        result = await executor._execute_pm_planning(run, context={"directive": "Plan implementation tasks"})

        assert result.status == "success"
        assert captured["timeout_seconds"] == 600
        assert command_service.observed_bindings[-1] is executor._test_role_evidence_port.bindings[-1]
        assert command_service.observed_bindings[-1].role == "pm"
        assert get_factory_role_evidence_authority_binding() is None

    @pytest.mark.asyncio
    async def test_pm_stage_builds_directive_from_architect_artifacts(self, temp_workspace, monkeypatch):
        command_service = _CapturingCompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_pm_directive_artifacts",
            config=FactoryConfig(name="test-run", stages=["pm_planning"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        for rel_path, content in {
            "docs/plan.md": "# Plan\n\nArchitect plan section " * 12,
            "docs/architecture.md": "# Architecture\n\nArchitect architecture section " * 12,
        }.items():
            target = Path(resolve_logical_path(str(temp_workspace), f"workspace/{rel_path}"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        monkeypatch.setattr(executor, "_validate_pm_plan_contract", lambda relative_path: None)
        monkeypatch.setattr(executor, "_artifact_exists", lambda relative_path, min_chars=1: True)

        result = await executor._execute_pm_planning(
            run,
            context={"directive": "Original user requirement\nsystem prompt should not pass through"},
        )

        assert result.status == "success"
        assert command_service.pm_calls
        options = command_service.pm_calls[0]["options"]
        assert isinstance(options, dict)
        directive = str(options.get("directive") or "")
        assert "Architect Plan" in directive
        assert "Architect Architecture" in directive
        assert "Original Requirement Excerpt" in directive
        assert "system prompt" not in directive.lower()

    @pytest.mark.asyncio
    async def test_pm_stage_recovers_timeout_with_deterministic_contracts(self, temp_workspace, monkeypatch):
        command_service = _CapturingCompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_pm_timeout_recovery",
            config=FactoryConfig(name="test-run", stages=["pm_planning"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        wait_calls = 0

        async def _fake_wait(service, command_result, timeout_seconds, cancel_event=None, abort_checker=None):
            nonlocal wait_calls
            del service, timeout_seconds, cancel_event, abort_checker
            wait_calls += 1
            if wait_calls == 1:
                return CommandResult(
                    run_id=command_result.run_id,
                    status="timeout",
                    message="Run timed out after 600 seconds",
                )
            plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "TASK-1",
                                "title": "实现可运行项目基础",
                                "goal": "完成项目基础并可验证",
                                "scope": "src",
                                "steps": ["实现基础", "补充测试"],
                                "acceptance": ["执行 `npm test` 返回 PASS"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return CommandResult(
                run_id=command_result.run_id,
                status="completed",
                message="Run status: completed",
            )

        monkeypatch.setattr(executor, "_wait_run_completion", _fake_wait)

        result = await executor._execute_pm_planning(run, context={"directive": "Plan implementation tasks"})

        assert result.status == "success"
        assert wait_calls == 2
        assert len(command_service.pm_calls) == 2
        assert len(command_service.observed_bindings) == 2
        assert all(binding is not None and binding.role == "pm" for binding in command_service.observed_bindings)
        assert (
            command_service.observed_bindings[0].execution_authority_hash
            != command_service.observed_bindings[1].execution_authority_hash
        )
        assert get_factory_role_evidence_authority_binding() is None
        second_options = command_service.pm_calls[1]["options"]
        assert isinstance(second_options, dict)
        metadata = second_options.get("metadata")
        assert isinstance(metadata, dict)
        assert metadata["deterministic_pm_contracts"] is True
        assert "workspace/roles/pm/factory_test_pm_timeout_recovery/plan.json" in result.artifacts
        signal_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/signals/pm_planning.signals.json"))
        payload = json.loads(signal_path.read_text(encoding="utf-8"))
        rows = payload.get("signals")
        assert isinstance(rows, list)
        assert any(
            isinstance(item, dict) and item.get("code") == "pm.timeout_recovered_by_deterministic_contracts"
            for item in rows
        )

    @pytest.mark.asyncio
    async def test_pm_stage_failure_output_includes_failed_task_details(self, temp_workspace):
        command_service = _DetailedFailureCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_pm_failure",
            config=FactoryConfig(name="test-run", stages=["pm_planning"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        result = await executor._execute_pm_planning(
            run,
            context={"directive": "Plan implementation tasks"},
        )

        assert result.status == "failed"
        assert "failed_task=task-0-pm (pm)" in str(result.output)
        assert "PM contract normalization failed" in str(result.output)
        assert "signals=" in str(result.output)
        assert "runtime/signals/pm_planning.signals.json" in result.artifacts
        signal_path = Path(
            resolve_runtime_path(
                str(temp_workspace),
                "runtime/signals/pm_planning.signals.json",
            )
        )
        payload = json.loads(signal_path.read_text(encoding="utf-8"))
        rows = payload.get("signals") if isinstance(payload, dict) else []
        assert isinstance(rows, list)
        codes = {str(item.get("code") or "") for item in rows if isinstance(item, dict)}
        assert "pm.run_status_non_success" in codes
        assert "pm.contract_issue_detected" in codes
        assert command_service.query_calls >= 1

    @pytest.mark.asyncio
    async def test_pm_stage_requires_materialized_plan_artifact(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_pm_missing_plan",
            config=FactoryConfig(name="test-run", stages=["pm_planning"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        result = await executor._execute_pm_planning(
            run,
            context={"directive": "Plan implementation tasks"},
        )

        assert result.status == "failed"
        assert "signals=" in str(result.output)
        assert "runtime/signals/pm_planning.signals.json" in result.artifacts
        signal_path = Path(
            resolve_runtime_path(
                str(temp_workspace),
                "runtime/signals/pm_planning.signals.json",
            )
        )
        payload = json.loads(signal_path.read_text(encoding="utf-8"))
        rows = payload.get("signals") if isinstance(payload, dict) else []
        assert isinstance(rows, list)
        assert any(
            isinstance(item, dict)
            and str(item.get("code") or "") == "pm.contract_issue_detected"
            and "missing_tasks_plan" in str(item.get("detail") or "")
            for item in rows
        )

    @pytest.mark.asyncio
    async def test_pm_stage_accepts_valid_plan_artifact(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_pm_valid_plan",
            config=FactoryConfig(name="test-run", stages=["pm_planning"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        runtime = TaskRuntimeService(str(temp_workspace))
        stale = runtime.create_task_row(subject="Stale task", description="completed in a previous run")
        _complete_task_row(runtime, stale["id"], metadata={"previous_run": "old"})
        plan_path.write_text(
            """{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "实现账户实体",
      "goal": "完成账单核心实体与校验",
      "scope": "src/account",
      "scope_paths": ["src/account"],
      "target_files": ["src/account"],
      "steps": ["实现实体", "补充测试"],
      "execution_checklist": ["实现实体", "补充测试"],
      "acceptance": ["`pytest` 通过", "接口返回字段正确"],
      "acceptance_criteria": ["`pytest` 通过", "接口返回字段正确"]
    }
  ]
}
""",
            encoding="utf-8",
        )

        result = await executor._execute_pm_planning(
            run,
            context={"directive": "Plan implementation tasks"},
        )

        assert result.status == "success"
        assert "tasks/plan.json" in result.artifacts
        assert f"workspace/roles/pm/{run.id}/plan.json" in result.artifacts
        assert "workspace/plans/latest.plan.json" in result.artifacts
        mirrored_plan = Path(resolve_logical_path(str(temp_workspace), f"workspace/roles/pm/{run.id}/plan.json"))
        assert json.loads(mirrored_plan.read_text(encoding="utf-8"))["tasks"][0]["id"] == "TASK-1"
        assert TaskRuntimeService(str(temp_workspace)).get_task(stale["id"]) is None
        task_row = TaskRuntimeService(str(temp_workspace)).get_task("TASK-1")
        assert task_row is not None
        assert task_row["status"] == "pending"
        assert task_row["metadata"]["pm_task_id"] == "TASK-1"

    @pytest.mark.asyncio
    async def test_chief_engineer_stage_requires_pm_plan_artifact(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_ce_missing_plan",
            config=FactoryConfig(name="test-run", stages=["chief_engineer_review"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        result = await executor._execute_chief_engineer_review(run, context={})

        assert result.status == "failed"
        assert "error_code=chief_engineer.plan_missing" in str(result.output)
        assert "runtime/signals/chief_engineer_review.signals.json" in result.artifacts

    @pytest.mark.asyncio
    async def test_chief_engineer_stage_generates_blueprint_artifacts(self, temp_workspace, monkeypatch):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        _authorize_test_chief_engineer_portfolio(executor)
        run = FactoryRun(
            id="factory_test_ce_blueprints",
            config=FactoryConfig(name="test-run", stages=["chief_engineer_review"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            """{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "实现账户实体",
      "goal": "完成账单核心实体与校验",
      "scope": "src/account.py, tests/test_account.py",
      "scope_paths": ["src/account.py", "tests/test_account.py"],
      "target_files": ["src/account.py", "tests/test_account.py"],
      "steps": ["实现实体", "补充测试"],
      "execution_checklist": ["实现实体", "补充测试"],
      "acceptance": ["`pytest` 通过", "接口返回字段正确"],
      "acceptance_criteria": ["`pytest` 通过", "接口返回字段正确"]
    }
  ]
}
""",
            encoding="utf-8",
        )

        # Mock RoleRuntimeService to return successful result
        from polaris.cells.roles.runtime.public.contracts._execution_contracts import RoleExecutionResultV1

        captured_commands = []
        captured_bindings: list[FactoryRoleEvidenceAuthorityBindingV1 | None] = []

        class FakeRoleRuntimeService:
            async def execute_role_task(self, command):
                captured_commands.append(command)
                captured_bindings.append(get_factory_role_evidence_authority_binding())
                return RoleExecutionResultV1(
                    ok=True,
                    status="success",
                    role="chief_engineer",
                    workspace=str(temp_workspace),
                    task_id=command.task_id,
                    run_id=command.run_id,
                    output=json.dumps(
                        _chief_engineer_portfolio_output(scope_path="src/account.py"),
                        ensure_ascii=False,
                    ),
                    metadata={
                        "provider": "test-provider",
                        "model": "test-model",
                        "cache_hit": False,
                        "final_request_context_audit": {
                            "final_request_token_estimate": 2048,
                            "context_window_utilization": 0.08,
                        },
                        "context_snapshot_ref": "0123456789abcdef01234567",
                    },
                )

        # Patch RoleRuntimeService in the module - it's instantiated in the method
        # so we need to patch the class itself
        original_role_runtime_service = factory_stage_module.RoleRuntimeService
        factory_stage_module.RoleRuntimeService = FakeRoleRuntimeService

        try:
            result = await executor._execute_chief_engineer_review(run, context={})
        finally:
            # Restore original
            factory_stage_module.RoleRuntimeService = original_role_runtime_service

        # Verify the result
        assert result.status == "success", result.output
        assert any(path.startswith("runtime/blueprints/ce_TASK-1_") for path in result.artifacts)
        assert f"runtime/state/blueprints/{run.id}.review.json" in result.artifacts
        assert f"workspace/roles/chief_engineer/{run.id}/review.json" in result.artifacts
        assert "workspace/blueprints/latest.review.json" in result.artifacts
        review_path = Path(
            resolve_runtime_path(
                str(temp_workspace),
                f"runtime/state/blueprints/{run.id}.review.json",
            )
        )
        payload = json.loads(review_path.read_text(encoding="utf-8"))
        assert payload["generated_blueprints"] == 1
        assert payload["blueprints"][0]["task_id"] == "TASK-1"
        assert len(captured_commands) == 1
        assert captured_bindings == [executor._test_role_evidence_port.bindings[-1]]
        assert captured_bindings[0] is not None and captured_bindings[0].role == "chief_engineer"
        assert get_factory_role_evidence_authority_binding() is None
        ce_command = captured_commands[0]
        assert ce_command.context["cognitive_runtime_mode"] == "off"
        assert ce_command.context["cognitive_runtime_enabled"] is False
        assert ce_command.context["cognitive_runtime_required"] is False
        assert ce_command.timeout_seconds == 600
        assert ce_command.context["chief_engineer_llm_timeout_seconds"] == 600
        assert ce_command.context["llm_call_timeout_seconds"] == 600
        assert ce_command.context["request_timeout_seconds"] == 600
        assert ce_command.metadata["cognitive_runtime_mode"] == "off"
        assert ce_command.metadata["cognitive_runtime_enabled"] is False
        assert ce_command.metadata["cognitive_runtime_required"] is False
        assert ce_command.metadata["llm_call_timeout_seconds"] == 600
        blueprint_path = Path(resolve_runtime_path(str(temp_workspace), payload["blueprints"][0]["blueprint_path"]))
        assert blueprint_path.is_file()
        mirrored_review = Path(
            resolve_logical_path(str(temp_workspace), f"workspace/roles/chief_engineer/{run.id}/review.json")
        )
        assert json.loads(mirrored_review.read_text(encoding="utf-8"))["generated_blueprints"] == 1

    def test_chief_engineer_stage_timeout_prefers_context_override(self, temp_workspace):
        executor = _TestStageExecutor(temp_workspace, _CompletedCommandService())

        timeout = executor._chief_engineer_llm_timeout_seconds({"chief_engineer_llm_timeout_seconds": "123"})

        assert timeout == 123

    @pytest.mark.asyncio
    async def test_director_stage_fails_when_plan_lineage_missing(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_director_missing_plan",
            config=FactoryConfig(name="test-run", stages=["director_dispatch"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        result = await executor._execute_director_dispatch(
            run,
            context={},
        )

        assert result.status == "failed"
        assert "error_code=director.task_lineage_missing" in str(result.output)
        assert "dispatch/log.json" in result.artifacts

    def test_pre_director_snapshot_restore_removes_director_delivery_files(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        seed_file = temp_workspace / "requirements.md"
        seed_file.write_text("Original requirement\n", encoding="utf-8")
        platform_evidence = temp_workspace / ".polaris" / "blueprints" / "latest.review.json"
        platform_evidence.parent.mkdir(parents=True, exist_ok=True)
        platform_evidence.write_text('{"generated_blueprints":1}\n', encoding="utf-8")

        manifest = executor._create_pre_director_snapshot(run_id="factory_snapshot_test")
        assert manifest["snapshot_kind"] == "pre_director_workspace"

        generated_source = temp_workspace / "src" / "index.ts"
        generated_source.parent.mkdir(parents=True, exist_ok=True)
        generated_source.write_text("export const generated = true;\n", encoding="utf-8")
        generated_package = temp_workspace / "package.json"
        generated_package.write_text('{"scripts":{"build":"tsc"}}\n', encoding="utf-8")
        seed_file.write_text("Director polluted requirement\n", encoding="utf-8")

        restored = executor._restore_pre_director_snapshot()

        assert "src/index.ts" in restored["removed_files"]
        assert "package.json" in restored["removed_files"]
        assert seed_file.read_text(encoding="utf-8") == "Original requirement\n"
        assert not generated_source.exists()
        assert not generated_package.exists()
        assert platform_evidence.is_file()

    @pytest.mark.asyncio
    async def test_director_stage_fails_when_upstream_run_non_success(self, temp_workspace):
        command_service = _DirectorFailedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_director_failed_run",
            config=FactoryConfig(name="test-run", stages=["director_dispatch"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        _authorize_director_fact_projection(executor, factory_run_id=run.id)

        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            """{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "实现账户实体",
      "goal": "完成账单核心实体与校验",
      "scope": "src/account",
      "steps": ["实现实体", "补充测试"],
      "acceptance": ["`pytest` 通过", "接口返回字段正确"]
    }
  ]
}
""",
            encoding="utf-8",
        )
        _write_handoff_ready_review_for_tasks(
            executor,
            run_id=run.id,
            tasks=[
                {
                    "id": "TASK-1",
                    "target_files": ["src/account.py"],
                }
            ],
        )
        task_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/task_1.json"))
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            """{
  "id": 1,
  "subject": "实现账户实体",
  "description": "实现与测试",
  "status": "pending",
  "created_at": 1735689600.0,
  "updated_at": 1735689600.0,
  "blocked_by": [],
  "blocks": [],
  "owner": "",
  "assignee": "",
  "tags": [],
  "priority": 1,
  "estimated_hours": 2.0,
  "result_summary": "",
  "metadata": {}
}
""",
            encoding="utf-8",
        )

        result = await executor._execute_director_dispatch(
            run,
            context={"director_max_rounds": 1},
        )

        assert result.status == "failed"
        assert "error_code=director.run_status_non_success" in str(result.output)
        assert "dispatch/log.json" in result.artifacts
        assert command_service.observed_bindings[-1] is executor._test_role_evidence_port.bindings[-1]
        assert command_service.observed_bindings[-1].role == "director"
        assert get_factory_role_evidence_authority_binding() is None

    @pytest.mark.asyncio
    async def test_director_stage_rejects_file_only_taskboard_even_with_metadata_progress(
        self,
        temp_workspace,
    ):
        command_service = _DirectorCompletedMetadataProgressService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_director_metadata_progress",
            config=FactoryConfig(name="test-run", stages=["director_dispatch"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            """{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "实现账户实体",
      "goal": "完成账单核心实体与校验",
      "scope": "src/account",
      "steps": ["实现实体", "补充测试"],
      "acceptance": ["`pytest` 通过", "接口返回字段正确"]
    }
  ]
}
""",
            encoding="utf-8",
        )
        _write_handoff_ready_review_for_tasks(
            executor,
            run_id=run.id,
            tasks=[
                {
                    "id": "TASK-1",
                    "target_files": ["src/account.js"],
                }
            ],
        )
        task_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/task_1.json"))
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            """{
  "id": 1,
  "subject": "实现账户实体",
  "description": "实现与测试",
  "status": "pending",
  "created_at": 1735689600.0,
  "updated_at": 1735689600.0,
  "blocked_by": [],
  "blocks": [],
  "owner": "",
  "assignee": "",
  "tags": [],
  "priority": 1,
  "estimated_hours": 2.0,
  "result_summary": "",
  "metadata": {}
}
""",
            encoding="utf-8",
        )

        result = await executor._execute_director_dispatch(
            run,
            context={"director_max_rounds": 2},
        )

        assert result.status == "failed"
        assert "error_code=director.task_runtime_fact_projection_not_ready" in str(result.output)
        assert "TaskRuntime fact-only observable projection is not ready" in str(result.output)
        assert "dispatch/log.json" in result.artifacts

    @pytest.mark.asyncio
    async def test_director_stage_does_not_handoff_file_only_materialization_after_timeout(
        self,
        temp_workspace,
    ):
        command_service = _DirectorCompletedThenTimeoutWithMaterializedTargetService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_director_timeout_handoff",
            config=FactoryConfig(name="test-run", stages=["director_dispatch"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            """{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "实现账户实体",
      "goal": "完成账单核心实体与校验",
      "scope": "src/account.js",
      "target_files": ["src/account.js"],
      "steps": ["实现实体"],
      "acceptance": ["`npm test` 通过"]
    }
  ]
}
""",
            encoding="utf-8",
        )
        _write_handoff_ready_review_for_tasks(
            executor,
            run_id=run.id,
            tasks=[
                {
                    "id": "TASK-1",
                    "target_files": ["src/account.js"],
                }
            ],
        )
        task_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/task_1.json"))
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            """{
  "id": 1,
  "subject": "实现账户实体",
  "description": "实现与测试",
  "status": "pending",
  "created_at": 1735689600.0,
  "updated_at": 1735689600.0,
  "blocked_by": [],
  "blocks": [],
  "owner": "",
  "assignee": "",
  "tags": [],
  "priority": 1,
  "estimated_hours": 2.0,
  "result_summary": "",
  "metadata": {}
}
""",
            encoding="utf-8",
        )

        result = await executor._execute_director_dispatch(
            run,
            context={"director_max_rounds": 2},
        )

        assert result.status == "failed"
        assert "handed off to workspace quality" not in str(result.output)
        assert "director.dispatch_timeout" not in str(result.output)
        assert "dispatch/log.json" in result.artifacts
        dispatch_log = Path(resolve_logical_path(str(temp_workspace), "workspace/dispatch/latest.log.json"))
        payload = json.loads(dispatch_log.read_text(encoding="utf-8"))
        assert "director.task_runtime_fact_projection_not_ready" in {
            signal.get("code") for signal in payload.get("signals", []) if isinstance(signal, dict)
        }

    @pytest.mark.asyncio
    async def test_director_stage_rejects_file_only_progress_before_target_diagnosis(
        self,
        temp_workspace,
    ):
        command_service = _DirectorNoMaterializedChangesAfterProgressService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_director_idempotent_no_changes",
            config=FactoryConfig(name="test-run", stages=["director_dispatch"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        _authorize_director_fact_projection(executor, factory_run_id=run.id)

        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            """{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "实现账户实体",
      "goal": "完成账单核心实体与校验",
      "scope": "src/account",
      "scope_paths": ["src/account.py", "tests/test_account.py"],
      "target_files": ["src/account.py", "tests/test_account.py"],
      "steps": ["实现实体", "补充测试"],
      "acceptance": ["`pytest` 通过", "接口返回字段正确"]
    }
  ]
}
""",
            encoding="utf-8",
        )
        _write_handoff_ready_review_for_tasks(
            executor,
            run_id=run.id,
            tasks=[
                {
                    "id": "TASK-1",
                    "target_files": ["src/account.py", "tests/test_account.py"],
                }
            ],
        )
        task_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/task_1.json"))
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            """{
  "id": 1,
  "subject": "实现账户实体",
  "description": "实现与测试",
  "status": "pending",
  "created_at": 1735689600.0,
  "updated_at": 1735689600.0,
  "blocked_by": [],
  "blocks": [],
  "owner": "",
  "assignee": "",
  "tags": [],
  "priority": 1,
  "estimated_hours": 2.0,
  "result_summary": "",
  "metadata": {}
}
""",
            encoding="utf-8",
        )

        result = await executor._execute_director_dispatch(
            run,
            context={"director_max_rounds": 2},
        )

        assert result.status == "failed"
        assert "error_code=director.run_status_non_success" in str(result.output)
        signal_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/signals/director_dispatch.signals.json"))
        payload = json.loads(signal_path.read_text(encoding="utf-8"))
        rows = payload.get("signals") if isinstance(payload, dict) else []
        assert any(isinstance(item, dict) and item.get("code") == "director.run_status_non_success" for item in rows)

    @pytest.mark.asyncio
    async def test_director_stage_covered_targets_do_not_bypass_execution_facts(
        self,
        temp_workspace,
    ):
        command_service = _DirectorNoMaterializedChangesAfterProgressService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_director_idempotent_covered_targets",
            config=FactoryConfig(name="test-run", stages=["director_dispatch"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        _authorize_director_fact_projection(executor, factory_run_id=run.id)

        delivered = temp_workspace / "src" / "account.py"
        delivered.parent.mkdir(parents=True, exist_ok=True)
        delivered.write_text("class Account:\n    pass\n", encoding="utf-8")

        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            """{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "实现账户实体",
      "goal": "完成账单核心实体与校验",
      "scope": "src/account.py",
      "scope_paths": ["src/account.py"],
      "target_files": ["src/account.py"],
      "steps": ["实现实体"],
      "acceptance": ["`src/account.py` 存在"]
    }
  ]
}
""",
            encoding="utf-8",
        )
        _write_handoff_ready_review_for_tasks(
            executor,
            run_id=run.id,
            tasks=[
                {
                    "id": "TASK-1",
                    "target_files": ["src/account.py"],
                }
            ],
        )
        task_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/task_1.json"))
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            """{
  "id": 1,
  "subject": "实现账户实体",
  "description": "实现与测试",
  "status": "pending",
  "created_at": 1735689600.0,
  "updated_at": 1735689600.0,
  "blocked_by": [],
  "blocks": [],
  "owner": "",
  "assignee": "",
  "tags": [],
  "priority": 1,
  "estimated_hours": 2.0,
  "result_summary": "",
  "metadata": {}
}
""",
            encoding="utf-8",
        )

        result = await executor._execute_director_dispatch(
            run,
            context={"director_max_rounds": 2},
        )

        assert result.status == "failed"
        assert "error_code=director.run_status_non_success" in str(result.output)
        signal_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/signals/director_dispatch.signals.json"))
        payload = json.loads(signal_path.read_text(encoding="utf-8"))
        rows = payload.get("signals") if isinstance(payload, dict) else []
        assert not any(
            isinstance(item, dict) and item.get("code") == "director.idempotent_no_materialized_changes"
            for item in rows
        )

    @pytest.mark.asyncio
    async def test_director_stage_does_not_treat_first_no_materialized_failure_as_idempotent(
        self,
        temp_workspace,
    ):
        command_service = _DirectorNoMaterializedChangesOnlyService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        run = FactoryRun(
            id="factory_test_director_first_no_changes_fails",
            config=FactoryConfig(name="test-run", stages=["director_dispatch"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        _authorize_director_fact_projection(executor, factory_run_id=run.id)

        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            """{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "修复前端测试失败",
      "goal": "修复 Vitest TypeScript failure",
      "scope": "src/types/generation.ts",
      "steps": ["Apply minimal fix"],
      "acceptance": ["npm test returns PASS"]
    }
  ]
}
""",
            encoding="utf-8",
        )
        _write_handoff_ready_review_for_tasks(
            executor,
            run_id=run.id,
            tasks=[
                {
                    "id": "TASK-1",
                    "target_files": ["src/types/generation.ts"],
                }
            ],
        )
        task_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/task_1.json"))
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            """{
  "id": 1,
  "subject": "修复前端测试失败",
  "description": "修复 Vitest TypeScript failure",
  "status": "pending",
  "created_at": 1735689600.0,
  "updated_at": 1735689600.0,
  "blocked_by": [],
  "blocks": [],
  "owner": "",
  "assignee": "",
  "tags": [],
  "priority": 1,
  "estimated_hours": 2.0,
  "result_summary": "",
  "metadata": {}
}
""",
            encoding="utf-8",
        )

        result = await executor._execute_director_dispatch(
            run,
            context={"director_max_rounds": 2},
        )

        assert result.status == "failed"
        assert "error_code=director.run_status_non_success" in str(result.output)

    @pytest.mark.asyncio
    async def test_quality_gate_uses_report_verdict(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        _authorize_workspace_quality_checks(executor)
        _isolate_qa_verdict_from_workspace_checks(executor)
        run = FactoryRun(
            id="factory_test_quality_gate",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            """{
  "passed": false,
  "score": 52,
  "critical_issue_count": 2
}
""",
            encoding="utf-8",
        )

        result = await executor._execute_quality_gate(
            run,
            context={"qa_target": "Quality gate"},
        )

        assert result.status == "failed"
        assert "qa_verdict_passed=False" in str(result.output)
        assert "runtime/qa/report.json" in result.artifacts
        assert f"workspace/roles/qa/{run.id}/report.json" in result.artifacts

    @pytest.mark.asyncio
    async def test_quality_gate_fails_when_report_passed_but_score_is_low(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        _authorize_workspace_quality_checks(executor)
        _isolate_qa_verdict_from_workspace_checks(executor)
        run = FactoryRun(
            id="factory_test_quality_gate_low_score",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "passed": True,
                    "score": 52,
                    "critical_issue_count": 0,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = await executor._execute_quality_gate(run, context={"qa_target": "Quality gate"})

        assert result.status == "failed"
        assert "qa_verdict_passed=False" in str(result.output)
        assert "report_consistent=False" in str(result.output)

    @pytest.mark.asyncio
    async def test_quality_gate_offloads_report_read_off_event_loop(self, temp_workspace, monkeypatch):
        # Arrange: a finished QA run plus an on-disk report. Spy on asyncio.to_thread
        # so we can assert the (blocking) report read is dispatched off the event loop.
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        _authorize_workspace_quality_checks(executor)
        _isolate_qa_verdict_from_workspace_checks(executor)
        run = FactoryRun(
            id="factory_test_quality_gate_offload",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps({"passed": True, "score": 91, "critical_issue_count": 0}, ensure_ascii=False),
            encoding="utf-8",
        )

        real_to_thread = asyncio.to_thread
        offloaded_report_reads: list[dict[str, object]] = []

        async def _spy_to_thread(func, /, *args, **kwargs):
            bound_self = getattr(func, "__self__", None)
            if getattr(func, "__name__", "") == "read_text" and bound_self == report_path:
                offloaded_report_reads.append({"args": args, "kwargs": dict(kwargs)})
            return await real_to_thread(func, *args, **kwargs)

        monkeypatch.setattr(factory_stage_module.asyncio, "to_thread", _spy_to_thread)

        # Act
        result = await executor._execute_quality_gate(run, context={"qa_target": "Quality gate"})

        # Assert: the report read was offloaded exactly once, with explicit UTF-8, and the
        # verdict still reflects the on-disk payload.
        assert len(offloaded_report_reads) == 1
        assert offloaded_report_reads[0]["kwargs"] == {"encoding": "utf-8"}
        assert "qa_verdict_passed=False" in str(result.output)
        assert "report_consistent=False" in str(result.output)

    @pytest.mark.asyncio
    async def test_quality_gate_fails_when_llm_judgement_unavailable_by_default(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        _authorize_workspace_quality_checks(executor)
        _isolate_qa_verdict_from_workspace_checks(executor)
        run = FactoryRun(
            id="factory_test_quality_gate_llm_unavailable",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "passed": True,
                    "score": 96,
                    "critical_issue_count": 0,
                    "warnings": ["qa_llm_judgement_unavailable"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = await executor._execute_quality_gate(run, context={"qa_target": "Quality gate"})

        assert result.status == "failed"
        assert "qa_verdict_passed=False" in str(result.output)

    @pytest.mark.asyncio
    async def test_quality_gate_fails_when_llm_judgement_unavailable_and_explicitly_required(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        _authorize_workspace_quality_checks(executor)
        _isolate_qa_verdict_from_workspace_checks(executor)
        run = FactoryRun(
            id="factory_test_quality_gate_llm_required_unavailable",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "passed": True,
                    "score": 96,
                    "critical_issue_count": 0,
                    "warnings": ["qa_llm_judgement_unavailable"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = await executor._execute_quality_gate(
            run,
            context={"qa_target": "Quality gate", "qa_require_llm_judgement": True},
        )

        assert result.status == "failed"
        assert "qa_verdict_passed=False" in str(result.output)

    @pytest.mark.asyncio
    async def test_quality_gate_can_explicitly_allow_llm_judgement_fallback(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        _authorize_workspace_quality_checks(executor)
        _isolate_qa_verdict_from_workspace_checks(executor)
        run = FactoryRun(
            id="factory_test_quality_gate_llm_fallback",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "passed": True,
                    "score": 96,
                    "critical_issue_count": 0,
                    "warnings": ["qa_llm_judgement_unavailable"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = await executor._execute_quality_gate(
            run,
            context={"qa_target": "Quality gate", "qa_require_llm_judgement": False},
        )

        assert result.status == "failed"
        assert "qa_verdict_passed=False" in str(result.output)

    @pytest.mark.asyncio
    async def test_quality_gate_runs_workspace_node_scripts(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _WorkspaceValidationStageExecutor(temp_workspace, command_service, exit_codes=[0, 0])
        _authorize_workspace_quality_checks(executor)
        run = FactoryRun(
            id="factory_test_quality_gate_workspace_checks",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        package_path = temp_workspace / "package.json"
        package_path.write_text(
            json.dumps({"scripts": {"test": "vitest --run", "build": "vite build"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps({"passed": True, "score": 92, "critical_issue_count": 0}, ensure_ascii=False),
            encoding="utf-8",
        )

        result = await executor._execute_quality_gate(run, context={"qa_target": "Quality gate"})

        assert result.status == "failed"
        validation_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/workspace-validation.json"))
        validation_payload = json.loads(validation_path.read_text(encoding="utf-8"))
        assert "canonical_reason=qa_verdict_missing" in str(result.output), json.dumps(
            validation_payload,
            ensure_ascii=False,
            indent=2,
        )
        assert command_service.observed_bindings[-1] is executor._test_role_evidence_port.bindings[-1]
        assert command_service.observed_bindings[-1].role == "qa"
        assert get_factory_role_evidence_authority_binding() is None
        assert executor.commands_seen == [["npm", "run", "build"], ["npm", "test"]]
        assert "workspace_checks_diagnostic=True" in str(result.output)
        assert "runtime/qa/workspace-validation.json" in result.artifacts
        assert f"workspace/roles/qa/{run.id}/workspace-validation.json" in result.artifacts
        assert "workspace/qa/latest.workspace-validation.json" in result.artifacts

    @pytest.mark.asyncio
    async def test_quality_gate_injects_workspace_evidence_before_qa_llm(self, temp_workspace):
        command_service = _CapturingQaCommandService()
        executor = _WorkspaceValidationStageExecutor(temp_workspace, command_service, exit_codes=[0])
        _authorize_workspace_quality_checks(executor)
        run = FactoryRun(
            id="factory_test_quality_gate_workspace_evidence",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        (temp_workspace / "package.json").write_text(
            json.dumps({"scripts": {"build": "tsc"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps({"passed": True, "score": 92, "critical_issue_count": 0}, ensure_ascii=False),
            encoding="utf-8",
        )
        executor._write_json_artifact(
            f"runtime/state/blueprints/{run.id}.review.json",
            {
                "schema_version": "factory.chief_engineer_review.v1",
                "generated_blueprints": 1,
                "blueprints": [
                    {
                        "task_id": "TASK-1",
                        "summary": "Create the runtime entrypoint and tests.",
                    }
                ],
            },
        )
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "schema_version": "pm.task_contract.v1",
                        "goal": "Create the runtime entrypoint and tests.",
                        "target_files": ["src/main.ts", "tests/main.test.ts"],
                    }
                ]
            },
        )

        result = await executor._execute_quality_gate(
            run,
            context={"qa_target": "Quality gate", "qa_input": "original qa context"},
        )

        assert result.status == "failed"
        assert "canonical_reason=qa_verdict_missing" in str(result.output)
        assert command_service.validation_exists_at_qa is True
        assert len(command_service.qa_calls) == 1
        qa_input = str(command_service.qa_calls[0]["options"]["input"])
        assert "original qa context" in qa_input
        assert "structured PM contract" in qa_input
        assert "runtime/qa/workspace-validation.json" not in qa_input
        assert run.id not in qa_input
        metadata = command_service.qa_calls[0]["options"]["metadata"]
        assert metadata["pm_task_contract"]["id"] == "TASK-1"
        assert metadata["target_files"] == ["src/main.ts", "tests/main.test.ts"]
        assert metadata["chief_engineer_blueprint"]["generated_blueprints"] == 1
        assert metadata["workspace_quality_evidence"]["passed"] is True
        assert metadata["verifier_receipts"][0]["exit_code"] == 0

    def test_qa_metadata_uses_chief_engineer_latest_review_fallback_without_prompt_duplication(self, temp_workspace):
        executor = _TestStageExecutor(temp_workspace, _CompletedCommandService())
        executor._write_json_artifact(
            "workspace/.polaris/blueprints/latest.review.json",
            {
                "schema_version": "factory.chief_engineer_review.v1",
                "generated_blueprints": 2,
                "blueprints": [{"task_id": "TASK-2", "summary": "Mirror fallback blueprint"}],
            },
        )

        qa_input = executor._build_qa_input_with_workspace_quality_evidence(
            "original qa context",
            "",
            run_id="factory_missing_state_review",
        )
        metadata = executor._build_qa_final_request_metadata(
            run_id="factory_missing_state_review",
            workspace_checks_artifact="",
        )

        assert "original qa context" in qa_input
        assert "structured PM contract" in qa_input
        assert "workspace/.polaris/blueprints/latest.review.json" not in qa_input
        assert metadata["chief_engineer_blueprint"]["generated_blueprints"] == 2

    def test_qa_execution_metadata_uses_one_deadline_derived_budget(self, temp_workspace):
        executor = _TestStageExecutor(temp_workspace, _CompletedCommandService())

        metadata, wait_timeout = executor._build_qa_execution_metadata(
            run_id="factory-qa-budget",
            workspace_checks_artifact="",
            context={"timeout": 600},
        )

        assert wait_timeout == 600
        assert metadata["request_timeout_seconds"] == 595
        assert metadata["timeout_seconds"] == 595

    def test_workspace_quality_repairs_pass_declared_target_files_to_director_repairs(
        self,
        temp_workspace,
        monkeypatch,
    ):
        from polaris.cells.roles.adapters.public import service as role_service

        executor = _TestStageExecutor(temp_workspace, _CompletedCommandService())
        executor._write_json_artifact(
            "tasks/plan.json",
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "target_files": ["src/main.ts", "README.md"],
                    }
                ]
            },
        )
        captured: dict[str, Any] = {}

        def _capture_repair(
            adapter: Any,
            *,
            task: dict[str, Any],
            task_id: str,
            artifact_quality_errors: list[str],
            execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
        ):
            del adapter, task_id, artifact_quality_errors, execution_attempt
            captured["task"] = task
            return [], {"attempted": False}

        monkeypatch.setattr(role_service, "run_director_materialization_quality_repair_schedule", _capture_repair)

        executor._apply_workspace_quality_repairs(
            run_id="run-target-files",
            artifact_quality_errors=["Artifact quality scan failed: declared target file missing 'src/main.ts'"],
        )

        assert captured["task"]["target_files"] == ["src/main.ts", "README.md"]
        assert captured["task"]["metadata"]["target_files"] == ["src/main.ts", "README.md"]

    @pytest.mark.asyncio
    async def test_quality_gate_installs_node_dependencies_before_scripts(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _WorkspaceValidationStageExecutor(temp_workspace, command_service, exit_codes=[0, 0])
        _authorize_workspace_quality_checks(executor)
        run = FactoryRun(
            id="factory_test_quality_gate_npm_install",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        (temp_workspace / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {"build": "vite build"},
                    "dependencies": {"marked": "^12.0.0"},
                    "devDependencies": {"vite": "^5.4.0"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps({"passed": True, "score": 92, "critical_issue_count": 0}, ensure_ascii=False),
            encoding="utf-8",
        )

        result = await executor._execute_quality_gate(run, context={"qa_target": "Quality gate"})

        assert result.status == "failed"
        assert "canonical_reason=qa_verdict_missing" in str(result.output)
        assert executor.commands_seen == [
            ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"],
            ["npm", "run", "build"],
        ]
        validation_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/workspace-validation.json"))
        payload = json.loads(validation_path.read_text(encoding="utf-8"))
        assert payload["commands"][0]["phase"] == "prepare"
        assert payload["commands"][1]["phase"] == "check"

    def test_workspace_quality_command_resolves_windows_cmd_shims(self, temp_workspace, monkeypatch):
        command_service = _CompletedCommandService()
        executor = _TestStageExecutor(temp_workspace, command_service)
        monkeypatch.setattr(
            "polaris.cells.factory.pipeline.internal.factory_run_service.shutil.which",
            lambda value: "C:/node/npm.cmd" if value == "npm.cmd" else None,
        )
        monkeypatch.setattr(
            "polaris.cells.factory.pipeline.internal.factory_run_service.os.name",
            "nt",
        )

        resolved = executor._resolve_workspace_quality_command(["npm", "test"])

        assert resolved == ["C:/node/npm.cmd", "test"]

    @pytest.mark.asyncio
    async def test_quality_gate_fails_on_workspace_node_script_failure(self, temp_workspace):
        command_service = _CompletedCommandService()
        executor = _WorkspaceValidationStageExecutor(temp_workspace, command_service, exit_codes=[1, 1])
        _authorize_workspace_quality_checks(executor)
        run = FactoryRun(
            id="factory_test_quality_gate_workspace_failure",
            config=FactoryConfig(name="test-run", stages=["quality_gate"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        (temp_workspace / "package.json").write_text(
            json.dumps({"scripts": {"test": "vitest --run"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        report_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/report.json"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps({"passed": True, "score": 95, "critical_issue_count": 0}, ensure_ascii=False),
            encoding="utf-8",
        )

        async def _no_llm_repairs(
            *,
            run_id: str,
            context: dict[str, Any],
            artifact_quality_errors: list[str],
            repair_attempt: int,
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
            del run_id, context, artifact_quality_errors, repair_attempt
            return [], {
                "attempted": False,
                "repair_mode": "director_llm",
                "reason": "unit_test_workspace_gate_failure",
                "source_tools": [],
                "tool_results": 0,
            }

        executor._apply_workspace_quality_llm_repairs = _no_llm_repairs

        result = await executor._execute_quality_gate(run, context={"qa_target": "Quality gate"})

        assert result.status == "failed"
        assert "factory_quality_gate_workspace_validation_failed" in str(result.output)
        validation_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/qa/workspace-validation.json"))
        payload = json.loads(validation_path.read_text(encoding="utf-8"))
        assert payload["passed"] is False
        assert payload["commands"][0]["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_default_stage_handler(self, temp_workspace):
        """Test unknown stage uses default handler"""
        service = FactoryRunService(temp_workspace, executor=FakeStageExecutor())
        config = FactoryConfig(name="test-run")
        run = await service.create_run(config)
        await service.start_run(run.id)

        result = await service.execute_stage(run.id, "unknown_stage")

        assert result.status == "skipped"
        assert "No handler" in result.output

    @pytest.mark.asyncio
    async def test_execute_stage_failure_sets_failure_metadata(self, temp_workspace):
        service = FactoryRunService(
            temp_workspace,
            executor=FakeStageExecutor(fail_stages={"quality_gate"}),
        )
        run = await service.create_run(FactoryConfig(name="test-run"))
        await service.start_run(run.id)

        result = await service.execute_stage(run.id, "quality_gate")

        assert result.status == "failed"
        assert "factory_terminal_drain_deferred" not in result.metadata
        updated_run = await service.get_run(run.id)
        assert updated_run.status == FactoryRunStatus.FAILED
        assert updated_run.metadata["last_failed_stage"] == "quality_gate"
        assert updated_run.metadata["failure"]["code"] == "FACTORY_STAGE_FAILED"
        terminal_lease = service._admission.current()
        assert terminal_lease is not None
        assert terminal_lease.state.value == "released"

        await service.complete_run(run.id, success=False)

        released_lease = service._admission.current()
        assert released_lease is not None
        assert released_lease.state.value == "released"


class TestFactoryRunStatus:
    """Test FactoryRunStatus enum"""

    def test_status_values(self):
        assert FactoryRunStatus.PENDING == "pending"
        assert FactoryRunStatus.RUNNING == "running"
        assert FactoryRunStatus.PAUSED == "paused"
        assert FactoryRunStatus.COMPLETED == "completed"
        assert FactoryRunStatus.FAILED == "failed"
        assert FactoryRunStatus.RECOVERING == "recovering"

    def test_status_from_string(self):
        assert FactoryRunStatus("pending") == FactoryRunStatus.PENDING
        assert FactoryRunStatus("running") == FactoryRunStatus.RUNNING


class TestDirectorFanoutRouteAuditR15B:
    """R15-B regression tests: Director fanout per_binding terminal route evidence
    and timeout attribution.

    Reproduces R14 live dispatch scenario: 3 bindings, 2 completed, 1 timeout.
    """

    def test_build_per_binding_route_events_generates_terminal_events(self):
        per_binding = [
            {"provider_id": "p0", "model": "m0", "binding_id": "d0", "run_id": "r0", "status": "completed"},
            {"provider_id": "p1", "model": "m1", "binding_id": "d1", "run_id": "r1", "status": "timeout"},
            {"provider_id": "p2", "model": "m2", "binding_id": "d2", "run_id": "r2", "status": "completed"},
        ]
        events = OrchestrationStageExecutor._build_per_binding_route_events(per_binding)
        assert len(events) == 3
        assert all(e["event"] == "llm_route_terminal" for e in events)
        assert all(e["terminal"] is True for e in events)
        assert all(e["source"] == "llm" for e in events)
        statuses = {e["provider_id"]: e["status"] for e in events}
        assert statuses["p0"] == "completed"
        assert statuses["p1"] == "timeout"
        assert statuses["p2"] == "completed"

    def test_build_per_binding_route_events_skips_invalid(self):
        per_binding = [
            {"provider_id": "", "model": "m1", "binding_id": "b1", "run_id": "r1", "status": "completed"},
            {"provider_id": "p1", "model": "", "binding_id": "b2", "run_id": "r2", "status": "completed"},
            {"provider_id": "p3", "model": "m3", "binding_id": "b3", "run_id": "r3", "status": "timeout"},
        ]
        events = OrchestrationStageExecutor._build_per_binding_route_events(per_binding)
        assert len(events) == 1
        assert events[0]["provider_id"] == "p3"

    def test_fail_closed_excludes_per_binding_evidence(self):
        from unittest.mock import patch

        configured = [
            {"role": "director", "provider_id": "p0", "model": "m0", "binding_id": "d0"},
            {"role": "director", "provider_id": "p1", "model": "m1", "binding_id": "d1"},
        ]
        route_events = [
            {
                "provider_id": "p0",
                "model": "m0",
                "binding_id": "d0",
                "status": "completed",
                "terminal": True,
                "invocation": True,
            },
            {
                "provider_id": "p1",
                "model": "m1",
                "binding_id": "d1",
                "status": "timeout",
                "terminal": True,
                "invocation": True,
            },
        ]
        with patch(
            "polaris.cells.factory.pipeline.internal.bench_gates.resolve_expected_llm_bindings",
            return_value={"director": configured},
        ):
            events = OrchestrationStageExecutor._build_fail_closed_director_route_events(
                attempts=[],
                stage_signals=[],
                per_binding_route_events=route_events,
            )
        assert len(events) == 0

    def test_fail_closed_only_for_truly_missing(self):
        from unittest.mock import patch

        configured = [
            {"role": "director", "provider_id": "p0", "model": "m0", "binding_id": "d0"},
            {"role": "director", "provider_id": "p1", "model": "m1", "binding_id": "d1"},
            {"role": "director", "provider_id": "p2", "model": "m2", "binding_id": "d2"},
        ]
        route_events = [
            {
                "provider_id": "p0",
                "model": "m0",
                "binding_id": "d0",
                "status": "completed",
                "terminal": True,
                "invocation": True,
            },
            {
                "provider_id": "p1",
                "model": "m1",
                "binding_id": "d1",
                "status": "timeout",
                "terminal": True,
                "invocation": True,
            },
        ]
        with patch(
            "polaris.cells.factory.pipeline.internal.bench_gates.resolve_expected_llm_bindings",
            return_value={"director": configured},
        ):
            events = OrchestrationStageExecutor._build_fail_closed_director_route_events(
                attempts=[],
                stage_signals=[],
                per_binding_route_events=route_events,
            )
        assert len(events) == 1
        assert events[0]["provider_id"] == "p2"
        assert events[0]["fail_closed"] is True

    def test_reclassify_converts_to_timeout(self):
        from unittest.mock import patch

        configured = [
            {"role": "director", "provider_id": "p0", "model": "m0", "binding_id": "d0"},
            {"role": "director", "provider_id": "p1", "model": "m1", "binding_id": "d1"},
            {"role": "director", "provider_id": "p2", "model": "m2", "binding_id": "d2"},
        ]
        route_events = [
            {"provider_id": "p0", "model": "m0", "binding_id": "d0", "status": "completed"},
            {"provider_id": "p1", "model": "m1", "binding_id": "d1", "status": "timeout"},
            {"provider_id": "p2", "model": "m2", "binding_id": "d2", "status": "completed"},
        ]
        signals = [{"code": "director.binding_coverage_incomplete", "severity": "error", "detail": "..."}]
        with patch(
            "polaris.cells.factory.pipeline.internal.bench_gates.resolve_expected_llm_bindings",
            return_value={"director": configured},
        ):
            OrchestrationStageExecutor._reclassify_binding_coverage_signals(signals, route_events)
        assert signals[0]["code"] == "director.binding_timeout"
        assert "timed out" in signals[0]["detail"].lower()

    def test_reclassify_noop_when_missing_bindings(self):
        from unittest.mock import patch

        configured = [
            {"role": "director", "provider_id": "p0", "model": "m0", "binding_id": "d0"},
            {"role": "director", "provider_id": "p1", "model": "m1", "binding_id": "d1"},
            {"role": "director", "provider_id": "p2", "model": "m2", "binding_id": "d2"},
        ]
        route_events = [
            {"provider_id": "p0", "model": "m0", "binding_id": "d0", "status": "completed"},
            {"provider_id": "p1", "model": "m1", "binding_id": "d1", "status": "timeout"},
        ]
        signals = [{"code": "director.binding_coverage_incomplete", "severity": "error", "detail": "..."}]
        with patch(
            "polaris.cells.factory.pipeline.internal.bench_gates.resolve_expected_llm_bindings",
            return_value={"director": configured},
        ):
            OrchestrationStageExecutor._reclassify_binding_coverage_signals(signals, route_events)
        assert signals[0]["code"] == "director.binding_coverage_incomplete"

    def test_reclassify_noop_when_no_timeout(self):
        from unittest.mock import patch

        configured = [
            {"role": "director", "provider_id": "p0", "model": "m0", "binding_id": "d0"},
            {"role": "director", "provider_id": "p1", "model": "m1", "binding_id": "d1"},
        ]
        route_events = [
            {"provider_id": "p0", "model": "m0", "binding_id": "d0", "status": "completed"},
            {"provider_id": "p1", "model": "m1", "binding_id": "d1", "status": "completed"},
        ]
        signals = [{"code": "director.binding_coverage_incomplete", "severity": "error", "detail": "..."}]
        with patch(
            "polaris.cells.factory.pipeline.internal.bench_gates.resolve_expected_llm_bindings",
            return_value={"director": configured},
        ):
            OrchestrationStageExecutor._reclassify_binding_coverage_signals(signals, route_events)
        assert signals[0]["code"] == "director.binding_coverage_incomplete"

    def test_r14_route_audit_missing_empty(self):
        from polaris.cells.factory.pipeline.internal.bench_gates import build_llm_route_audit

        configured = [
            {"role": "director", "provider_id": "p0", "model": "m0", "binding_id": "d0"},
            {"role": "director", "provider_id": "p1", "model": "m1", "binding_id": "d1"},
            {"role": "director", "provider_id": "p2", "model": "m2", "binding_id": "d2"},
        ]
        per_binding = [
            {"provider_id": "p0", "model": "m0", "binding_id": "d0", "run_id": "r0", "status": "completed"},
            {"provider_id": "p1", "model": "m1", "binding_id": "d1", "run_id": "r1", "status": "timeout"},
            {"provider_id": "p2", "model": "m2", "binding_id": "d2", "run_id": "r2", "status": "completed"},
        ]
        events = OrchestrationStageExecutor._build_per_binding_route_events(per_binding)
        audit = build_llm_route_audit(
            events,
            expected_bindings={"director": configured},
            required_roles=("director",),
            require_all_director_routes=True,
        )
        director_result = audit["roles"]["director"]
        assert director_result["missing_bindings"] == []
        assert director_result["observed_count"] == 3

    def test_r14_fail_closed_not_hit_completed(self):
        from unittest.mock import patch

        configured = [
            {"role": "director", "provider_id": "p0", "model": "m0", "binding_id": "d0"},
            {"role": "director", "provider_id": "p1", "model": "m1", "binding_id": "d1"},
            {"role": "director", "provider_id": "p2", "model": "m2", "binding_id": "d2"},
        ]
        route_events = [
            {
                "provider_id": "p0",
                "model": "m0",
                "binding_id": "d0",
                "status": "completed",
                "terminal": True,
                "invocation": True,
            },
            {
                "provider_id": "p1",
                "model": "m1",
                "binding_id": "d1",
                "status": "timeout",
                "terminal": True,
                "invocation": True,
            },
            {
                "provider_id": "p2",
                "model": "m2",
                "binding_id": "d2",
                "status": "completed",
                "terminal": True,
                "invocation": True,
            },
        ]
        with patch(
            "polaris.cells.factory.pipeline.internal.bench_gates.resolve_expected_llm_bindings",
            return_value={"director": configured},
        ):
            events = OrchestrationStageExecutor._build_fail_closed_director_route_events(
                attempts=[],
                stage_signals=[],
                per_binding_route_events=route_events,
            )
        assert len(events) == 0
        fail_closed_providers = {e.get("provider_id") for e in events}
        assert "p0" not in fail_closed_providers
        assert "p2" not in fail_closed_providers

    def test_r14_root_cause_is_timeout(self):
        from unittest.mock import patch

        configured = [
            {"role": "director", "provider_id": "p0", "model": "m0", "binding_id": "d0"},
            {"role": "director", "provider_id": "p1", "model": "m1", "binding_id": "d1"},
            {"role": "director", "provider_id": "p2", "model": "m2", "binding_id": "d2"},
        ]
        route_events = [
            {"provider_id": "p0", "model": "m0", "binding_id": "d0", "status": "completed"},
            {"provider_id": "p1", "model": "m1", "binding_id": "d1", "status": "timeout"},
            {"provider_id": "p2", "model": "m2", "binding_id": "d2", "status": "completed"},
        ]
        signals = [{"code": "director.binding_coverage_incomplete", "severity": "error", "detail": "..."}]
        with patch(
            "polaris.cells.factory.pipeline.internal.bench_gates.resolve_expected_llm_bindings",
            return_value={"director": configured},
        ):
            OrchestrationStageExecutor._reclassify_binding_coverage_signals(signals, route_events)
        error_code = ""
        for s in signals:
            if isinstance(s, dict) and s.get("severity") == "error":
                error_code = str(s.get("code") or "")
                break
        assert error_code == "director.binding_timeout"


class TestCEProviderModelPropagationR15A:
    """R15-A regression tests: provider/model must propagate from
    RoleExecutionKernel metadata into CE evidence and audit artifacts."""

    @staticmethod
    def _write_plan(temp_workspace: Path, task_id: str = "TASK-1") -> None:
        plan_path = Path(resolve_runtime_path(str(temp_workspace), "runtime/tasks/plan.json"))
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": task_id,
                            "title": "Implement feature",
                            "goal": "Complete the feature",
                            "scope": "src/feature.py, tests/test_feature.py",
                            "scope_paths": ["src/feature.py", "tests/test_feature.py"],
                            "target_files": ["src/feature.py", "tests/test_feature.py"],
                            "steps": ["implement", "test"],
                            "execution_checklist": ["implement", "test"],
                            "acceptance": ["tests pass"],
                            "acceptance_criteria": ["tests pass"],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    @pytest.mark.asyncio
    async def test_ce_evidence_success_result_extracts_kimi_provider_model(self, temp_workspace: Path) -> None:
        """R15-A: A successful CE result carrying provider_id/model in metadata
        must extract real Kimi provider/model -- not 'unknown unknown'."""
        from polaris.cells.roles.runtime.public.contracts._execution_contracts import (
            RoleExecutionResultV1,
        )

        self._write_plan(temp_workspace)
        executor = _TestStageExecutor(temp_workspace, _CompletedCommandService())
        _authorize_test_chief_engineer_portfolio(executor)
        run = FactoryRun(
            id="factory_test_r15a_kimi_success",
            config=FactoryConfig(name="test-run", stages=["chief_engineer_review"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        class _KimiSuccessRuntimeService:
            async def execute_role_task(self, command: object) -> RoleExecutionResultV1:
                return RoleExecutionResultV1(
                    ok=True,
                    status="ok",
                    role="chief_engineer",
                    workspace=str(temp_workspace),
                    task_id="TASK-1",
                    run_id="factory_test_r15a_kimi_success",
                    output=json.dumps(
                        _chief_engineer_portfolio_output(scope_path="src/feature.py"),
                        ensure_ascii=False,
                    ),
                    usage={},
                    metadata={
                        "provider_id": "kimi",
                        "model": "kimi-k2-thinking-turbo",
                        "final_request_context_audit": {
                            "final_request_token_estimate": 2048,
                            "context_window_utilization": 0.08,
                        },
                        "context_snapshot_ref": "0123456789abcdef01234567",
                    },
                )

        original_service = factory_stage_module.RoleRuntimeService
        factory_stage_module.RoleRuntimeService = _KimiSuccessRuntimeService  # type: ignore
        try:
            result = await executor._execute_chief_engineer_review(run, context={})
        finally:
            factory_stage_module.RoleRuntimeService = original_service  # type: ignore

        assert result.status == "success"

        review_path = Path(
            resolve_runtime_path(
                str(temp_workspace),
                f"runtime/state/blueprints/{run.id}.review.json",
            )
        )
        assert review_path.is_file()
        review_payload = json.loads(review_path.read_text(encoding="utf-8"))
        assert review_payload["generated_blueprints"] == 1

        blueprint_rows = review_payload.get("blueprints", [])
        assert len(blueprint_rows) == 1
        evidence = blueprint_rows[0].get("llm_evidence", {})
        assert evidence.get("provider") == "kimi", f"Expected provider='kimi', got '{evidence.get('provider')}'"
        assert evidence.get("model") == "kimi-k2-thinking-turbo", (
            f"Expected model='kimi-k2-thinking-turbo', got '{evidence.get('model')}'"
        )
        assert evidence.get("provider_model_unknown") is not True

    @pytest.mark.asyncio
    async def test_ce_review_schema_failure_records_warning_and_continues_blueprint(
        self,
        temp_workspace: Path,
    ) -> None:
        from polaris.cells.roles.runtime.public.contracts._execution_contracts import (
            RoleExecutionResultV1,
        )

        self._write_plan(temp_workspace)
        executor = _TestStageExecutor(temp_workspace, _CompletedCommandService())
        _authorize_test_chief_engineer_portfolio(executor)
        run = FactoryRun(
            id="factory_test_ce_schema_recoverable",
            config=FactoryConfig(name="test-run", stages=["chief_engineer_review"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        class _SchemaFailedRuntimeService:
            async def execute_role_task(self, command: object) -> RoleExecutionResultV1:
                return RoleExecutionResultV1(
                    ok=False,
                    status="failed",
                    role="chief_engineer",
                    workspace=str(temp_workspace),
                    task_id="TASK-1",
                    run_id="factory_test_ce_schema_recoverable",
                    output='{"plan": "analysis only"',
                    error_code="role_runtime_error",
                    error_message=(
                        "验证失败，已重试1次: No JSON object matched chief_engineer blueprint keys: "
                        "construction_plan, scope_for_apply, risk_flags"
                    ),
                    usage={
                        "kernel_repair_reasons": ["attempt_0: No JSON object matched chief_engineer blueprint keys"]
                    },
                    metadata={
                        "provider_id": "kimi",
                        "model": "kimi-k2-thinking-turbo",
                        "final_request_context_audit": {
                            "final_request_token_estimate": 2708,
                            "context_underutilized": False,
                        },
                        "context_os_audit": {"ok": True},
                        "context_snapshot_ref": "fedcba9876543210fedcba98",
                    },
                )

        original_service = factory_stage_module.RoleRuntimeService
        factory_stage_module.RoleRuntimeService = _SchemaFailedRuntimeService  # type: ignore
        try:
            result = await executor._execute_chief_engineer_review(run, context={})
        finally:
            factory_stage_module.RoleRuntimeService = original_service  # type: ignore

        assert result.status == "failed"

        review_path = Path(
            resolve_runtime_path(
                str(temp_workspace),
                f"runtime/state/blueprints/{run.id}.review.json",
            )
        )
        review_payload = json.loads(review_path.read_text(encoding="utf-8"))
        assert review_payload["generated_blueprints"] == 0
        signals = review_payload["signals"]
        assert len(signals) == 1
        signal = signals[0]
        assert signal["code"] == "chief_engineer.llm_review_failed"
        assert signal["severity"] == "error"
        assert signal["recoverable"] is False
        assert signal["provider"] == "kimi"
        assert signal["model"] == "kimi-k2-thinking-turbo"
        assert signal["context_snapshot_ref"] == "fedcba9876543210fedcba98"
        assert signal["final_request_context_audit"]["final_request_token_estimate"] == 2708
        assert signal["context_os_audit"]["ok"] is True

    @pytest.mark.asyncio
    async def test_ce_evidence_failed_result_preserves_provider_model(self, temp_workspace: Path) -> None:
        """R15-A: A failed CE result must still carry provider/model from
        metadata so the audit trail is complete."""
        from polaris.cells.roles.runtime.public.contracts._execution_contracts import (
            RoleExecutionResultV1,
        )

        self._write_plan(temp_workspace)
        executor = _TestStageExecutor(temp_workspace, _CompletedCommandService())
        _authorize_test_chief_engineer_portfolio(executor)
        run = FactoryRun(
            id="factory_test_r15a_failed_preserves",
            config=FactoryConfig(name="test-run", stages=["chief_engineer_review"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        class _FailedKimiRuntimeService:
            async def execute_role_task(self, command: object) -> RoleExecutionResultV1:
                return RoleExecutionResultV1(
                    ok=False,
                    status="failed",
                    role="chief_engineer",
                    workspace=str(temp_workspace),
                    task_id="TASK-1",
                    run_id="factory_test_r15a_failed_preserves",
                    output="Partial analysis",
                    error_code="provider_timeout",
                    error_message="LLM call timed out",
                    usage={},
                    metadata={
                        "provider_id": "kimi",
                        "model": "kimi-v1",
                    },
                )

        original_service = factory_stage_module.RoleRuntimeService
        factory_stage_module.RoleRuntimeService = _FailedKimiRuntimeService  # type: ignore
        try:
            result = await executor._execute_chief_engineer_review(run, context={})
        finally:
            factory_stage_module.RoleRuntimeService = original_service  # type: ignore

        assert result.status == "failed"

        signal_path = Path(
            resolve_runtime_path(
                str(temp_workspace),
                "runtime/signals/chief_engineer_review.signals.json",
            )
        )
        payload = json.loads(signal_path.read_text(encoding="utf-8"))
        signals = (payload.get("signals") or []) if isinstance(payload, dict) else []
        llm_signal = next(
            (s for s in signals if isinstance(s, dict) and s.get("code") == "chief_engineer.llm_review_failed"),
            None,
        )
        assert llm_signal is not None, "missing chief_engineer.llm_review_failed signal"
        assert llm_signal.get("provider") == "kimi"
        assert llm_signal.get("model") == "kimi-v1"

    @pytest.mark.asyncio
    async def test_ce_empty_response_failure_uses_blueprint_projection(self, temp_workspace: Path) -> None:
        """A CE request with complete context can recover from a post-retry empty response."""
        from polaris.cells.roles.runtime.public.contracts._execution_contracts import (
            RoleExecutionResultV1,
        )

        self._write_plan(temp_workspace)
        executor = _TestStageExecutor(temp_workspace, _CompletedCommandService())
        _authorize_test_chief_engineer_portfolio(executor)
        run = FactoryRun(
            id="factory_test_ce_empty_projection",
            config=FactoryConfig(name="test-run", stages=["chief_engineer_review"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        class _EmptyResponseRuntimeService:
            async def execute_role_task(self, command: object) -> RoleExecutionResultV1:
                return RoleExecutionResultV1(
                    ok=False,
                    status="failed",
                    role="chief_engineer",
                    workspace=str(temp_workspace),
                    task_id="TASK-1",
                    run_id="factory_test_ce_empty_projection",
                    output="",
                    error_code="clarification_needed",
                    error_message="model returned no visible output or tool calls; awaiting user clarification",
                    usage={},
                    metadata={
                        "provider_id": "kimi",
                        "model": "kimi-for-coding",
                        "final_request_context_audit": {
                            "final_request_token_estimate": 2412,
                            "context_window_utilization": 0.0092,
                        },
                        "context_snapshot_ref": "abcdef0123456789abcdef01",
                    },
                )

        original_service = factory_stage_module.RoleRuntimeService
        factory_stage_module.RoleRuntimeService = _EmptyResponseRuntimeService  # type: ignore
        try:
            result = await executor._execute_chief_engineer_review(run, context={})
        finally:
            factory_stage_module.RoleRuntimeService = original_service  # type: ignore

        assert result.status == "failed"

        review_path = Path(
            resolve_runtime_path(
                str(temp_workspace),
                f"runtime/state/blueprints/{run.id}.review.json",
            )
        )
        review_payload = json.loads(review_path.read_text(encoding="utf-8"))
        assert review_payload["generated_blueprints"] == 0
        signal = next(
            item
            for item in review_payload["signals"]
            if isinstance(item, dict) and item.get("code") == "chief_engineer.llm_review_failed"
        )
        assert signal["severity"] == "error"
        assert signal["recoverable"] is False
        assert "recovery_strategy" not in signal
        assert signal["provider"] == "kimi"
        assert signal["model"] == "kimi-for-coding"
        assert signal["context_snapshot_ref"] == "abcdef0123456789abcdef01"

    @pytest.mark.asyncio
    async def test_ce_evidence_unknown_provider_model_marks_unknown_flag(self, temp_workspace: Path) -> None:
        """R15-A: When provider/model are genuinely unknown (metadata has no
        provider_id/model keys), the CE evidence must set
        provider_model_unknown=True with a clear root cause description."""
        from polaris.cells.roles.runtime.public.contracts._execution_contracts import (
            RoleExecutionResultV1,
        )

        self._write_plan(temp_workspace)
        executor = _TestStageExecutor(temp_workspace, _CompletedCommandService())
        _authorize_test_chief_engineer_portfolio(executor)
        run = FactoryRun(
            id="factory_test_r15a_unknown_flag",
            config=FactoryConfig(name="test-run", stages=["chief_engineer_review"]),
            status=FactoryRunStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        class _UnknownProviderRuntimeService:
            async def execute_role_task(self, command: object) -> RoleExecutionResultV1:
                return RoleExecutionResultV1(
                    ok=False,
                    status="failed",
                    role="chief_engineer",
                    workspace=str(temp_workspace),
                    task_id="TASK-1",
                    run_id="factory_test_r15a_unknown_flag",
                    output="analysis",
                    error_code="some_error",
                    error_message="some error",
                    usage={},
                    metadata={},  # no provider_id/model
                )

        original_service = factory_stage_module.RoleRuntimeService
        factory_stage_module.RoleRuntimeService = _UnknownProviderRuntimeService  # type: ignore
        try:
            result = await executor._execute_chief_engineer_review(run, context={})
        finally:
            factory_stage_module.RoleRuntimeService = original_service  # type: ignore

        assert result.status == "failed"

        signal_path = Path(
            resolve_runtime_path(
                str(temp_workspace),
                "runtime/signals/chief_engineer_review.signals.json",
            )
        )
        payload = json.loads(signal_path.read_text(encoding="utf-8"))
        signals = (payload.get("signals") or []) if isinstance(payload, dict) else []
        llm_signal = next(
            (s for s in signals if isinstance(s, dict) and s.get("code") == "chief_engineer.llm_review_failed"),
            None,
        )
        assert llm_signal is not None
        assert llm_signal.get("provider") == "unknown"
        assert llm_signal.get("model") == "unknown"
        assert llm_signal.get("provider_model_unknown") is True
        assert isinstance(llm_signal.get("provider_model_unknown_reason"), str)
        assert len(llm_signal["provider_model_unknown_reason"]) > 0
