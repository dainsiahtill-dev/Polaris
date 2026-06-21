"""Evidence tests for Director multi-binding fanout metadata propagation.

Verifies that parallel/max_workers/binding_id are propagated into:
1. CommandResult metadata from _execute_director_binding_fanout
2. Per-binding route events from _build_per_binding_route_events
3. Dispatch log payload from _execute_director_dispatch
4. Audit events from _emit_audit_event

These tests freeze the *current* behavior to ensure observability of
fanout configuration in production telemetry and audit trails.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.cells.factory.pipeline.internal.factory_run_service import (
    CommandResult,
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    OrchestrationStageExecutor,
)


def _make_executor(workspace: Path) -> OrchestrationStageExecutor:
    """Create an executor instance for testing."""
    executor = OrchestrationStageExecutor.__new__(OrchestrationStageExecutor)
    executor.workspace = workspace
    executor._binding_timeout_counts = {}
    executor._quarantined_bindings = set()
    return executor


def _make_factory_run(run_id: str = "test-run-1") -> FactoryRun:
    """Create a FactoryRun instance for testing."""
    return FactoryRun(
        id=run_id,
        config=FactoryConfig(name="test-factory"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-06-21T00:00:00+00:00",
    )


def _make_full_executor(workspace: Path) -> OrchestrationStageExecutor:
    """Create a fully initialized executor with required directories."""
    from polaris.cells.factory.pipeline.internal.factory_artifact_store import ArtifactStore
    from polaris.cells.factory.pipeline.internal.factory_run_completion import RunCompletionWaiter
    from polaris.cells.factory.pipeline.internal.factory_workspace_quality import WorkspaceQualityRunner
    from polaris.kernelone.fs import KernelFileSystem, get_default_adapter

    executor = OrchestrationStageExecutor.__new__(OrchestrationStageExecutor)
    executor.workspace = workspace
    executor._fs = KernelFileSystem(str(workspace), get_default_adapter())
    executor._artifact_store = ArtifactStore(workspace, executor._fs)
    executor._workspace_quality = WorkspaceQualityRunner(workspace)
    executor._run_completion_waiter = RunCompletionWaiter(workspace)
    executor._binding_timeout_counts = {}
    executor._quarantined_bindings = set()

    # Create required directories
    (workspace / ".polaris" / "audit").mkdir(parents=True, exist_ok=True)
    return executor


class TestFanoutMetadataPropagation:
    """Verify that parallel/max_workers/binding_id propagate into metadata."""

    @pytest.mark.asyncio
    async def test_parallel_mode_in_fanout_metadata(self, tmp_path: Path) -> None:
        """execution_mode='parallel' is preserved in fanout result metadata."""
        executor = _make_executor(tmp_path)
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
        ]

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(
            side_effect=[
                CommandResult(run_id="r1", status="completed", message="ok"),
                CommandResult(run_id="r2", status="completed", message="ok"),
            ]
        )

        result = await executor._execute_director_binding_fanout(
            service=mock_service,
            workspace=str(tmp_path),
            tasks=["task-1"],
            base_options={"execution_mode": "parallel", "max_workers": 2},
            bindings=bindings,
        )

        assert result.metadata is not None
        assert result.metadata["binding_fanout"] is True
        assert result.metadata["binding_count"] == 2
        assert result.metadata["execution_mode"] == "parallel"
        assert result.metadata["max_workers"] == 2

    @pytest.mark.asyncio
    async def test_max_workers_in_fanout_base_options(self, tmp_path: Path) -> None:
        """max_workers is passed through to each binding execution."""
        executor = _make_executor(tmp_path)
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
        ]

        captured_options: list[dict[str, Any]] = []

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            captured_options.append(dict(options))
            return CommandResult(run_id=f"run-{len(captured_options)}", status="completed", message="ok")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        await executor._execute_director_binding_fanout(
            service=mock_service,
            workspace=str(tmp_path),
            tasks=["task-1"],
            base_options={"execution_mode": "parallel", "max_workers": 3},
            bindings=bindings,
        )

        assert len(captured_options) == 2
        for opts in captured_options:
            assert opts["execution_mode"] == "parallel"
            assert opts["max_workers"] == 3

    @pytest.mark.asyncio
    async def test_binding_id_in_per_binding_metadata(self, tmp_path: Path) -> None:
        """binding_id appears in per_binding metadata for each fanout result."""
        executor = _make_executor(tmp_path)
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "director:0:openai:gpt-4"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "director:1:anthropic:claude-3"},
        ]

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(
            side_effect=[
                CommandResult(run_id="r1", status="completed", message="ok"),
                CommandResult(run_id="r2", status="completed", message="ok"),
            ]
        )

        result = await executor._execute_director_binding_fanout(
            service=mock_service,
            workspace=str(tmp_path),
            tasks=["task-1"],
            base_options={"execution_mode": "parallel", "max_workers": 2},
            bindings=bindings,
        )

        assert result.metadata is not None
        per_binding = result.metadata["per_binding"]
        assert len(per_binding) == 2

        binding_ids = {entry["binding_id"] for entry in per_binding}
        assert "director:0:openai:gpt-4" in binding_ids
        assert "director:1:anthropic:claude-3" in binding_ids

    @pytest.mark.asyncio
    async def test_binding_override_in_execute_options(self, tmp_path: Path) -> None:
        """Each binding execution includes binding_override in metadata."""
        executor = _make_executor(tmp_path)
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
        ]

        captured_options: list[dict[str, Any]] = []

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            captured_options.append(dict(options))
            return CommandResult(run_id=f"run-{len(captured_options)}", status="completed", message="ok")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        await executor._execute_director_binding_fanout(
            service=mock_service,
            workspace=str(tmp_path),
            tasks=["task-1"],
            base_options={"execution_mode": "parallel", "max_workers": 2},
            bindings=bindings,
        )

        assert len(captured_options) == 2
        binding_overrides = [opts["metadata"]["binding_override"] for opts in captured_options]
        assert binding_overrides[0]["provider_id"] == "openai"
        assert binding_overrides[0]["model"] == "gpt-4"
        assert binding_overrides[0]["binding_id"] == "b0"
        assert binding_overrides[1]["provider_id"] == "anthropic"
        assert binding_overrides[1]["model"] == "claude-3"
        assert binding_overrides[1]["binding_id"] == "b1"


class TestPerBindingRouteEvents:
    """Verify that binding_id propagates into route events."""

    def test_binding_id_in_route_events(self) -> None:
        """binding_id appears in each generated route event."""
        per_binding = [
            {"provider_id": "p0", "model": "m0", "binding_id": "d0", "run_id": "r0", "status": "completed"},
            {"provider_id": "p1", "model": "m1", "binding_id": "d1", "run_id": "r1", "status": "timeout"},
            {"provider_id": "p2", "model": "m2", "binding_id": "d2", "run_id": "r2", "status": "completed"},
        ]
        events = OrchestrationStageExecutor._build_per_binding_route_events(per_binding)

        assert len(events) == 3
        binding_ids = {e["binding_id"] for e in events}
        assert binding_ids == {"d0", "d1", "d2"}

    def test_route_events_include_provider_and_model(self) -> None:
        """provider_id and model are preserved in route events."""
        per_binding = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0", "run_id": "r0", "status": "completed"},
        ]
        events = OrchestrationStageExecutor._build_per_binding_route_events(per_binding)

        assert len(events) == 1
        assert events[0]["provider_id"] == "openai"
        assert events[0]["model"] == "gpt-4"
        assert events[0]["role"] == "director"
        assert events[0]["terminal"] is True

    def test_timeout_events_include_timeout_count(self) -> None:
        """Timeout events include timeout_count field."""
        per_binding = [
            {
                "provider_id": "p0",
                "model": "m0",
                "binding_id": "b0",
                "run_id": "r0",
                "status": "timeout",
                "timeout_count": 2,
            },
        ]
        events = OrchestrationStageExecutor._build_per_binding_route_events(per_binding)

        assert len(events) == 1
        assert events[0]["timeout_count"] == 2

    def test_quarantined_events_include_quarantine_fields(self) -> None:
        """Quarantined events include quarantined and quarantine_reason fields."""
        per_binding = [
            {
                "provider_id": "p0",
                "model": "m0",
                "binding_id": "b0",
                "run_id": "",
                "status": "quarantined",
                "quarantined": True,
                "quarantine_reason": "consecutive_timeout",
                "timeout_count": 3,
            },
        ]
        events = OrchestrationStageExecutor._build_per_binding_route_events(per_binding)

        assert len(events) == 1
        assert events[0]["quarantined"] is True
        assert events[0]["quarantine_reason"] == "consecutive_timeout"
        assert events[0]["timeout_count"] == 3


class TestDispatchLogPayload:
    """Verify that fanout metadata propagates into dispatch log payload."""

    @pytest.mark.asyncio
    async def test_per_binding_route_events_in_dispatch_log(self, tmp_path: Path) -> None:
        """per_binding_route_events are written to dispatch/log.json."""
        executor = _make_full_executor(tmp_path)

        # Write tasks/plan.json using artifact_path to get correct location
        plan_path = executor._artifact_path("tasks/plan.json")
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "TASK-1",
                            "title": "Test task",
                            "scope": "src/",
                            "goal": "Test",
                            "steps": ["step1"],
                            "acceptance": ["acc1"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        run = _make_factory_run()

        # Mock the orchestration service and binding fanout
        mock_service = MagicMock()

        fanout_result = CommandResult(
            run_id="fanout-run-1",
            status="completed",
            message="Director binding fanout: 2 bindings, 2 succeeded, 0 failed, 0 quarantined",
            metadata={
                "binding_fanout": True,
                "binding_count": 2,
                "active_binding_count": 2,
                "quarantined_binding_count": 0,
                "per_binding": [
                    {"provider_id": "p0", "model": "m0", "binding_id": "b0", "run_id": "r0", "status": "completed"},
                    {"provider_id": "p1", "model": "m1", "binding_id": "b1", "run_id": "r1", "status": "completed"},
                ],
                "execution_mode": "parallel",
                "max_workers": 2,
            },
        )

        # Need enough stats for all calls: initial, before round 1, after round 1, final
        stats_sequence = [
            {"total": 1, "pending": 1, "ready": 0, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
            {"total": 1, "pending": 1, "ready": 0, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
            {"total": 1, "pending": 0, "ready": 0, "in_progress": 0, "completed": 1, "failed": 0, "blocked": 0},
            {"total": 1, "pending": 0, "ready": 0, "in_progress": 0, "completed": 1, "failed": 0, "blocked": 0},
        ]

        with (
            patch.object(executor, "_build_orchestration_service", return_value=mock_service),
            patch.object(
                executor,
                "_resolve_director_binding_fanout",
                return_value=[
                    {"provider_id": "p0", "model": "m0", "binding_id": "b0"},
                    {"provider_id": "p1", "model": "m1", "binding_id": "b1"},
                ],
            ),
            patch.object(executor, "_read_taskboard_stats", side_effect=stats_sequence),
            patch.object(executor, "_execute_director_binding_fanout", return_value=fanout_result),
            patch.object(executor, "_validate_director_binding_coverage", return_value=(True, [])),
            patch.object(executor, "_wait_run_completion", return_value=fanout_result),
            patch.object(executor, "_resolve_cancel_event", return_value=None),
            patch.object(executor, "_resolve_abort_checker", return_value=None),
        ):
            await executor._execute_director_dispatch(
                run,
                {"director_max_rounds": 1, "timeout": 10, "execution_mode": "parallel", "max_workers": 2},
            )

        # Read dispatch log - artifact_path maps dispatch/ to runtime/dispatch/
        log_path = executor._artifact_path("dispatch/log.json")
        assert log_path.exists(), f"dispatch/log.json should be written at {log_path}"
        log_payload = json.loads(log_path.read_text(encoding="utf-8"))

        # Verify per_binding_route_events in log
        assert "per_binding_route_events" in log_payload
        route_events = log_payload["per_binding_route_events"]
        assert len(route_events) == 2

        binding_ids = {e["binding_id"] for e in route_events}
        assert "b0" in binding_ids
        assert "b1" in binding_ids

        # Verify execution_mode/max_workers in fanout metadata within attempts
        assert len(log_payload["attempts"]) >= 1
        fanout_attempt = log_payload["attempts"][0]
        assert fanout_attempt["metadata"]["execution_mode"] == "parallel"
        assert fanout_attempt["metadata"]["max_workers"] == 2

    @pytest.mark.asyncio
    async def test_execution_mode_in_dispatch_log_context(self, tmp_path: Path) -> None:
        """execution_mode is preserved in dispatch log signals."""
        executor = _make_full_executor(tmp_path)

        # Write tasks/plan.json using artifact_path to get correct location
        plan_path = executor._artifact_path("tasks/plan.json")
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "TASK-1",
                            "title": "Test task",
                            "scope": "src/",
                            "goal": "Test",
                            "steps": ["step1"],
                            "acceptance": ["acc1"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        run = _make_factory_run()

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(
            return_value=CommandResult(
                run_id="single-run-1",
                status="completed",
                message="Run status: completed",
                metadata={"task_status_counts": {"completed": 1}},
            )
        )

        single_result = CommandResult(
            run_id="single-run-1",
            status="completed",
            message="Run status: completed",
            metadata={"task_status_counts": {"completed": 1}},
        )

        # Need enough stats for all calls: initial, before round 1, after round 1, final
        stats_sequence = [
            {"total": 1, "pending": 1, "ready": 0, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
            {"total": 1, "pending": 1, "ready": 0, "in_progress": 0, "completed": 0, "failed": 0, "blocked": 0},
            {"total": 1, "pending": 0, "ready": 0, "in_progress": 0, "completed": 1, "failed": 0, "blocked": 0},
            {"total": 1, "pending": 0, "ready": 0, "in_progress": 0, "completed": 1, "failed": 0, "blocked": 0},
        ]

        with (
            patch.object(executor, "_build_orchestration_service", return_value=mock_service),
            patch.object(executor, "_resolve_director_binding_fanout", return_value=[]),
            patch.object(executor, "_read_taskboard_stats", side_effect=stats_sequence),
            patch.object(executor, "_wait_run_completion", return_value=single_result),
            patch.object(executor, "_resolve_cancel_event", return_value=None),
            patch.object(executor, "_resolve_abort_checker", return_value=None),
            patch.object(executor, "_validate_director_binding_coverage", return_value=(True, [])),
        ):
            await executor._execute_director_dispatch(
                run,
                {"director_max_rounds": 1, "timeout": 10, "execution_mode": "serial", "max_workers": 1},
            )

        # Read dispatch log - artifact_path maps dispatch/ to runtime/dispatch/
        log_path = executor._artifact_path("dispatch/log.json")
        assert log_path.exists(), f"dispatch/log.json should be written at {log_path}"
        log_payload = json.loads(log_path.read_text(encoding="utf-8"))

        # Verify dispatch log contains attempts with metadata
        assert "attempts" in log_payload
        assert len(log_payload["attempts"]) == 1

        attempt = log_payload["attempts"][0]
        assert "metadata" in attempt
        assert attempt["metadata"]["task_status_counts"]["completed"] == 1


class TestDirectorOrchestratorFanoutEvidence:
    """Verify that DirectorOrchestrator propagates config into decision metadata."""

    @pytest.mark.asyncio
    async def test_execution_mode_in_decision_strategy_tags(self, tmp_path: Path) -> None:
        """execution_mode appears in resident decision strategy_tags."""
        from polaris.application.orchestration.director_orchestrator import (
            DirectorExecutionConfig,
            DirectorOrchestrator,
        )

        config = DirectorExecutionConfig(
            workspace=str(tmp_path),
            execution_mode="parallel",
            max_workers=3,
        )
        orchestrator = DirectorOrchestrator(config=config)

        fake_task = {"id": "task-1", "subject": "Test task"}
        fake_adapter_result = {
            "success": True,
            "task_id": "task-1",
            "changed_files": ["src/test.py"],
            "qa_required_for_final_verdict": True,
        }

        mock_adapter = MagicMock()
        mock_adapter.execute = AsyncMock(return_value=fake_adapter_result)

        mock_board = MagicMock()
        mock_board.get_ready_tasks.return_value = []
        mock_board.update.return_value = None

        recorded_payloads: list[dict[str, Any]] = []

        with (
            patch.object(orchestrator, "_get_task_board", return_value=mock_board),
            patch(
                "polaris.cells.roles.adapters.public.service.create_role_adapter",
                return_value=mock_adapter,
            ),
            patch(
                "polaris.application.orchestration.director_orchestrator._record_director_decision_safe",
                side_effect=lambda ws, payload: recorded_payloads.append(dict(payload)),
            ),
        ):
            result = await orchestrator.execute_task(fake_task)

        assert result.success is True
        assert len(recorded_payloads) == 1

        payload = recorded_payloads[0]
        assert "strategy_tags" in payload
        assert "parallel_dispatch" in payload["strategy_tags"]

    @pytest.mark.asyncio
    async def test_serial_mode_in_decision_strategy_tags(self, tmp_path: Path) -> None:
        """execution_mode='serial' appears in resident decision strategy_tags."""
        from polaris.application.orchestration.director_orchestrator import (
            DirectorExecutionConfig,
            DirectorOrchestrator,
        )

        config = DirectorExecutionConfig(
            workspace=str(tmp_path),
            execution_mode="serial",
            max_workers=1,
        )
        orchestrator = DirectorOrchestrator(config=config)

        fake_task = {"id": "task-1", "subject": "Test task"}
        fake_adapter_result = {
            "success": True,
            "task_id": "task-1",
            "changed_files": ["src/test.py"],
            "qa_required_for_final_verdict": True,
        }

        mock_adapter = MagicMock()
        mock_adapter.execute = AsyncMock(return_value=fake_adapter_result)

        mock_board = MagicMock()
        mock_board.get_ready_tasks.return_value = []
        mock_board.update.return_value = None

        recorded_payloads: list[dict[str, Any]] = []

        with (
            patch.object(orchestrator, "_get_task_board", return_value=mock_board),
            patch(
                "polaris.cells.roles.adapters.public.service.create_role_adapter",
                return_value=mock_adapter,
            ),
            patch(
                "polaris.application.orchestration.director_orchestrator._record_director_decision_safe",
                side_effect=lambda ws, payload: recorded_payloads.append(dict(payload)),
            ),
        ):
            result = await orchestrator.execute_task(fake_task)

        assert result.success is True
        assert len(recorded_payloads) == 1

        payload = recorded_payloads[0]
        assert "strategy_tags" in payload
        assert "serial_dispatch" in payload["strategy_tags"]


class TestFanoutQuarantineEvidence:
    """Verify that quarantine state propagates into metadata and events."""

    @pytest.mark.asyncio
    async def test_quarantined_binding_in_per_binding_metadata(self, tmp_path: Path) -> None:
        """Quarantined bindings appear in per_binding with quarantine fields."""
        executor = _make_executor(tmp_path)
        # Set quarantine key matching the binding_key format: "provider_id:model:binding_id"
        executor._quarantined_bindings = {"p1:m1:b1"}

        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "p1", "model": "m1", "binding_id": "b1"},
        ]

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(
            return_value=CommandResult(run_id="r1", status="completed", message="ok")
        )

        result = await executor._execute_director_binding_fanout(
            service=mock_service,
            workspace=str(tmp_path),
            tasks=["task-1"],
            base_options={"execution_mode": "parallel", "max_workers": 2},
            bindings=bindings,
        )

        assert result.metadata is not None
        per_binding = result.metadata["per_binding"]
        assert len(per_binding) == 2

        quarantined_entries = [e for e in per_binding if e["status"] == "quarantined"]
        assert len(quarantined_entries) == 1

        quarantined_entry = quarantined_entries[0]
        assert quarantined_entry["quarantined"] is True
        assert quarantined_entry["quarantine_reason"] == "consecutive_timeout"
        assert quarantined_entry["provider_id"] == "p1"
        assert quarantined_entry["model"] == "m1"

    @pytest.mark.asyncio
    async def test_timeout_count_accumulation_in_metadata(self, tmp_path: Path) -> None:
        """Timeout count accumulates in executor state across fanout calls."""
        executor = _make_executor(tmp_path)

        bindings = [
            {"provider_id": "p0", "model": "m0", "binding_id": "b0"},
        ]

        # First call: timeout
        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(
            return_value=CommandResult(run_id="r1", status="timeout", message="timed out")
        )

        result1 = await executor._execute_director_binding_fanout(
            service=mock_service,
            workspace=str(tmp_path),
            tasks=["task-1"],
            base_options={"execution_mode": "parallel", "max_workers": 1},
            bindings=bindings,
        )

        assert result1.metadata is not None
        assert result1.metadata["per_binding"][0]["timeout_count"] == 1
        assert "quarantined" not in result1.metadata["per_binding"][0]

        # Second call: timeout again -> quarantined
        result2 = await executor._execute_director_binding_fanout(
            service=mock_service,
            workspace=str(tmp_path),
            tasks=["task-1"],
            base_options={"execution_mode": "parallel", "max_workers": 1},
            bindings=bindings,
        )

        assert result2.metadata is not None
        assert result2.metadata["per_binding"][0]["timeout_count"] == 2
        assert result2.metadata["per_binding"][0]["quarantined"] is True


class TestAuditEventEmission:
    """Verify that _emit_audit_event writes director fanout config to audit trail."""

    def test_emit_audit_event_writes_fanout_config(self, tmp_path: Path) -> None:
        """_emit_audit_event writes binding_fanout_dispatch event with execution_mode/max_workers."""
        executor = _make_full_executor(tmp_path)

        executor._emit_audit_event(
            "director.binding_fanout_dispatch",
            execution_mode="parallel",
            max_workers=3,
            binding_count=2,
            active_binding_count=2,
            quarantined_binding_count=0,
        )

        audit_path = tmp_path / ".polaris" / "audit" / "director.binding_fanout_dispatch.json"
        assert audit_path.exists(), f"Audit event should be written at {audit_path}"

        entries = json.loads(audit_path.read_text(encoding="utf-8"))
        assert len(entries) == 1
        entry = entries[0]
        assert entry["event_type"] == "director.binding_fanout_dispatch"
        assert entry["execution_mode"] == "parallel"
        assert entry["max_workers"] == 3
        assert entry["binding_count"] == 2
        assert "timestamp" in entry

    def test_emit_audit_event_appends_multiple_entries(self, tmp_path: Path) -> None:
        """Multiple _emit_audit_event calls append to the same event file."""
        executor = _make_full_executor(tmp_path)

        executor._emit_audit_event(
            "director.binding_fanout_dispatch",
            execution_mode="parallel",
            max_workers=2,
            binding_count=1,
        )
        executor._emit_audit_event(
            "director.binding_fanout_dispatch",
            execution_mode="serial",
            max_workers=1,
            binding_count=1,
        )

        audit_path = tmp_path / ".polaris" / "audit" / "director.binding_fanout_dispatch.json"
        entries = json.loads(audit_path.read_text(encoding="utf-8"))
        assert len(entries) == 2
        assert entries[0]["execution_mode"] == "parallel"
        assert entries[1]["execution_mode"] == "serial"
