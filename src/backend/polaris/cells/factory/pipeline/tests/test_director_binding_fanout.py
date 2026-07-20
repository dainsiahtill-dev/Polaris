"""Tests for Director multi-binding fanout execution.

Verifies that all reachable Director bindings produce real LLM evidence
through per-binding dispatch fanout.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.cells.factory.pipeline.internal.factory_role_evidence_authority import (
    FactoryRoleEvidenceAuthorityPort,
)
from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult


def _fanout_authority_port() -> FactoryRoleEvidenceAuthorityPort:
    """Return the exact runtime port type needed by direct fanout unit calls."""

    port = object.__new__(FactoryRoleEvidenceAuthorityPort)
    capacity_calls: list[tuple[str, int]] = []

    def require_grant_capacity(role: str, count: int) -> None:
        assert role == "director"
        assert count >= 0
        capacity_calls.append((role, count))

    port.require_grant_capacity = require_grant_capacity  # type: ignore[method-assign]
    port._test_capacity_calls = capacity_calls
    return port


async def _canonical_wait_result(
    _service: Any,
    initial_result: CommandResult,
    **_kwargs: Any,
) -> CommandResult:
    """Project a committed outcome for fanout tests not exercising wait logic."""

    metadata = dict(initial_result.metadata or {})
    metadata.update(
        {
            "canonical_authoritative": True,
            "fact_event_seq": 1,
            "terminal_source": "test.committed_outcome",
        }
    )
    return CommandResult(
        run_id=initial_result.run_id,
        status=initial_result.status,
        message=initial_result.message,
        reason_code=initial_result.reason_code,
        metadata=metadata,
    )


def _attach_canonical_wait(executor: Any) -> Any:
    async def _call_with_test_authority(
        _authority_port: object,
        _role: str,
        operation: Any,
    ) -> Any:
        return await operation()

    executor._wait_run_completion = _canonical_wait_result
    executor._call_with_factory_role_evidence_authority = _call_with_test_authority
    executor._test_authority_port = _fanout_authority_port()
    return executor


@pytest.mark.asyncio
async def test_direct_fanout_consumes_explicit_exact_authority_port(tmp_path: Path) -> None:
    """Exercise the production signature directly, without an instance wrapper."""

    from polaris.cells.factory.pipeline.internal.factory_run_service import (
        OrchestrationStageExecutor,
    )

    executor = _attach_canonical_wait(OrchestrationStageExecutor(tmp_path))
    authority_port = executor._test_authority_port

    result = await OrchestrationStageExecutor._execute_director_binding_fanout(
        executor,
        service=object(),
        workspace=str(tmp_path),
        tasks=[],
        base_options={},
        bindings=[],
        authority_port=authority_port,
    )

    assert type(authority_port) is FactoryRoleEvidenceAuthorityPort
    assert authority_port._test_capacity_calls == [("director", 0)]
    assert result.status == "failed"


class TestResolveDirectorBindingFanout:
    """Tests for _resolve_director_binding_fanout."""

    def _make_executor(self, workspace: Any = None) -> Any:
        from pathlib import Path

        from polaris.cells.factory.pipeline.internal.factory_run_service import (
            OrchestrationStageExecutor,
        )

        executor = OrchestrationStageExecutor.__new__(OrchestrationStageExecutor)
        executor.workspace = Path(workspace) if workspace else Path(".")
        return executor

    def test_returns_empty_when_single_binding(self) -> None:
        executor = self._make_executor()
        slot = MagicMock()
        slot.provider_id = "openai"
        slot.model = "gpt-4"
        slot.binding_id = "director:0:openai:gpt-4"
        with patch(
            "polaris.kernelone.llm.runtime_config.get_role_binding_slots",
            return_value=[slot],
        ):
            result = executor._resolve_director_binding_fanout()
        assert result == []

    def test_returns_empty_when_slots_unavailable(self) -> None:
        executor = self._make_executor()
        with patch(
            "polaris.kernelone.llm.runtime_config.get_role_binding_slots",
            side_effect=RuntimeError("unavailable"),
        ):
            result = executor._resolve_director_binding_fanout()
        assert result == []

    def test_returns_bindings_for_multiple_reachable_providers(self) -> None:
        executor = self._make_executor()
        slots = []
        for i, (pid, model) in enumerate([("openai", "gpt-4"), ("anthropic", "claude-3"), ("gemini", "gemini-pro")]):
            slot = MagicMock()
            slot.provider_id = pid
            slot.model = model
            slot.binding_id = f"director:{i}:{pid}:{model}"
            slots.append(slot)

        with (
            patch(
                "polaris.kernelone.llm.runtime_config.get_role_binding_slots",
                return_value=slots,
            ),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._reachable_provider_pool",
                return_value=["openai", "anthropic", "gemini"],
            ),
        ):
            result = executor._resolve_director_binding_fanout()

        assert len(result) == 3
        provider_ids = [b["provider_id"] for b in result]
        assert "openai" in provider_ids
        assert "anthropic" in provider_ids
        assert "gemini" in provider_ids

    def test_filters_unreachable_providers(self) -> None:
        executor = self._make_executor()
        slots = []
        for i, (pid, model) in enumerate([("openai", "gpt-4"), ("anthropic", "claude-3"), ("offline", "local-7b")]):
            slot = MagicMock()
            slot.provider_id = pid
            slot.model = model
            slot.binding_id = f"director:{i}:{pid}:{model}"
            slots.append(slot)

        with (
            patch(
                "polaris.kernelone.llm.runtime_config.get_role_binding_slots",
                return_value=slots,
            ),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._reachable_provider_pool",
                return_value=["openai", "anthropic"],
            ),
        ):
            result = executor._resolve_director_binding_fanout()

        assert len(result) == 2
        provider_ids = [b["provider_id"] for b in result]
        assert "offline" not in provider_ids

    def test_filters_readiness_skipped_binding_but_keeps_remaining_route(self) -> None:
        executor = self._make_executor()
        slots = []
        for i, (pid, model) in enumerate([("dead", "qwen-offline"), ("live", "qwen-live")]):
            slot = MagicMock()
            slot.provider_id = pid
            slot.model = model
            slot.binding_id = f"director:{i}:{pid}:{model}"
            slots.append(slot)

        with (
            patch(
                "polaris.kernelone.llm.runtime_config.get_role_binding_slots",
                return_value=slots,
            ),
            patch(
                "polaris.kernelone.llm.runtime_config.is_role_binding_healthy",
                return_value=True,
            ),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._reachable_provider_pool",
                return_value=["dead", "live"],
            ),
            patch.object(
                executor,
                "_director_readiness_skip_reasons",
                return_value={
                    executor._director_binding_identity("dead", "qwen-offline", "director:0:dead:qwen-offline"): (
                        "provider_connectivity_unavailable"
                    )
                },
            ),
        ):
            result = executor._resolve_director_binding_fanout({})

        assert result == [
            {
                "provider_id": "live",
                "model": "qwen-live",
                "binding_id": "director:1:live:qwen-live",
            }
        ]
        assert executor._last_director_binding_skips == [
            {
                "provider_id": "dead",
                "model": "qwen-offline",
                "binding_id": "director:0:dead:qwen-offline",
                "reason": "provider_connectivity_unavailable",
            }
        ]

    def test_runtime_dispatch_skips_do_not_poison_future_recovered_bindings(self) -> None:
        executor = self._make_executor()

        with patch(
            "polaris.cells.runtime.projection.public.build_llm_status",
            return_value={
                "roles": {
                    "director": {
                        "skipped_bindings": [
                            {
                                "provider_id": "qwen-a",
                                "model": "qwen3.6-27b-q6-code-gpu0",
                                "binding_id": "director:0:qwen-a:qwen3.6-27b-q6-code-gpu0",
                                "reason": "provider_unreachable",
                                "readiness_source": "runtime_dispatch",
                            },
                            {
                                "provider_id": "qwen-b",
                                "model": "qwen3.6-27b-q6-code-gpu1",
                                "binding_id": "director:1:qwen-b:qwen3.6-27b-q6-code-gpu1",
                                "reason": "provider_connectivity_unavailable",
                                "readiness_source": "provider_index",
                            },
                        ]
                    }
                }
            },
        ):
            reasons = executor._director_readiness_skip_reasons({})

        assert executor._director_binding_identity("qwen-a", "qwen3.6-27b-q6-code-gpu0", "") not in reasons
        assert (
            reasons[
                executor._director_binding_identity(
                    "qwen-b",
                    "qwen3.6-27b-q6-code-gpu1",
                    "director:1:qwen-b:qwen3.6-27b-q6-code-gpu1",
                )
            ]
            == "provider_connectivity_unavailable"
        )

    def test_cooldown_cannot_starve_all_ready_bindings(self) -> None:
        executor = self._make_executor()
        slots = []
        for i, (pid, model) in enumerate([("qwen-a", "qwen-q6-a"), ("qwen-b", "qwen-q6-b")]):
            slot = MagicMock()
            slot.provider_id = pid
            slot.model = model
            slot.binding_id = f"director:{i}:{pid}:{model}"
            slots.append(slot)

        with (
            patch(
                "polaris.kernelone.llm.runtime_config.get_role_binding_slots",
                return_value=slots,
            ),
            patch(
                "polaris.kernelone.llm.runtime_config.is_role_binding_healthy",
                return_value=False,
            ),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._reachable_provider_pool",
                return_value=["qwen-a", "qwen-b"],
            ),
            patch.object(executor, "_director_readiness_skip_reasons", return_value={}),
        ):
            result = executor._resolve_director_binding_fanout({})

        assert result == [
            {
                "provider_id": "qwen-a",
                "model": "qwen-q6-a",
                "binding_id": "director:0:qwen-a:qwen-q6-a",
            },
            {
                "provider_id": "qwen-b",
                "model": "qwen-q6-b",
                "binding_id": "director:1:qwen-b:qwen-q6-b",
            },
        ]
        assert executor._last_director_binding_skips == []

    def test_readiness_cooldown_cannot_starve_all_ready_bindings(self) -> None:
        executor = self._make_executor()
        slots = []
        for i, (pid, model) in enumerate([("qwen-a", "qwen-q6-a"), ("qwen-b", "qwen-q6-b")]):
            slot = MagicMock()
            slot.provider_id = pid
            slot.model = model
            slot.binding_id = f"director:{i}:{pid}:{model}"
            slots.append(slot)

        readiness_skips = {
            executor._director_binding_identity(
                "qwen-a",
                "qwen-q6-a",
                "director:0:qwen-a:qwen-q6-a",
            ): "role_binding_cooldown",
            executor._director_binding_identity(
                "qwen-b",
                "qwen-q6-b",
                "director:1:qwen-b:qwen-q6-b",
            ): "role_binding_cooldown",
        }

        with (
            patch(
                "polaris.kernelone.llm.runtime_config.get_role_binding_slots",
                return_value=slots,
            ),
            patch(
                "polaris.kernelone.llm.runtime_config.is_role_binding_healthy",
                return_value=True,
            ),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._reachable_provider_pool",
                return_value=["qwen-a", "qwen-b"],
            ),
            patch.object(executor, "_director_readiness_skip_reasons", return_value=readiness_skips),
        ):
            result = executor._resolve_director_binding_fanout({})

        assert result == [
            {
                "provider_id": "qwen-a",
                "model": "qwen-q6-a",
                "binding_id": "director:0:qwen-a:qwen-q6-a",
            },
            {
                "provider_id": "qwen-b",
                "model": "qwen-q6-b",
                "binding_id": "director:1:qwen-b:qwen-q6-b",
            },
        ]
        assert executor._last_director_binding_skips == []

    def test_cooldown_skips_only_when_another_binding_is_active(self) -> None:
        executor = self._make_executor()
        slots = []
        for i, (pid, model) in enumerate([("qwen-a", "qwen-q6-a"), ("qwen-b", "qwen-q6-b")]):
            slot = MagicMock()
            slot.provider_id = pid
            slot.model = model
            slot.binding_id = f"director:{i}:{pid}:{model}"
            slots.append(slot)

        def is_healthy(
            role_id: str,
            *,
            provider_id: str,
            model: str,
            binding_id: str | None = None,
        ) -> bool:
            del role_id, model, binding_id
            return provider_id != "qwen-a"

        with (
            patch(
                "polaris.kernelone.llm.runtime_config.get_role_binding_slots",
                return_value=slots,
            ),
            patch(
                "polaris.kernelone.llm.runtime_config.is_role_binding_healthy",
                side_effect=is_healthy,
            ),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._reachable_provider_pool",
                return_value=["qwen-a", "qwen-b"],
            ),
            patch.object(executor, "_director_readiness_skip_reasons", return_value={}),
        ):
            result = executor._resolve_director_binding_fanout({})

        assert result == [
            {
                "provider_id": "qwen-b",
                "model": "qwen-q6-b",
                "binding_id": "director:1:qwen-b:qwen-q6-b",
            }
        ]
        assert executor._last_director_binding_skips == [
            {
                "provider_id": "qwen-a",
                "model": "qwen-q6-a",
                "binding_id": "director:0:qwen-a:qwen-q6-a",
                "reason": "role_binding_cooldown",
            }
        ]

    def test_deduplicates_same_provider_model(self) -> None:
        executor = self._make_executor()
        slots = []
        for i in range(3):
            slot = MagicMock()
            slot.provider_id = "openai"
            slot.model = "gpt-4"
            slot.binding_id = f"director:{i}:openai:gpt-4"
            slots.append(slot)

        with (
            patch(
                "polaris.kernelone.llm.runtime_config.get_role_binding_slots",
                return_value=slots,
            ),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._reachable_provider_pool",
                return_value=["openai"],
            ),
        ):
            result = executor._resolve_director_binding_fanout()

        assert result == []


class TestExecuteDirectorBindingFanout:
    """Tests for _execute_director_binding_fanout."""

    def _make_executor(self) -> Any:
        from pathlib import Path

        from polaris.cells.factory.pipeline.internal.factory_run_service import (
            OrchestrationStageExecutor,
        )

        executor = OrchestrationStageExecutor.__new__(OrchestrationStageExecutor)
        executor.workspace = Path(".")
        executor._binding_timeout_counts = {}
        executor._quarantined_bindings = set()
        return _attach_canonical_wait(executor)

    @pytest.mark.asyncio
    async def test_submission_and_completion_wait_share_one_absolute_lease(self) -> None:
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
        ]

        async def mock_execute(**_kwargs: Any) -> CommandResult:
            await asyncio.sleep(0.35)
            return CommandResult(run_id="run-active", status="running", message="submitted")

        async def slow_wait(
            _service: Any,
            initial_result: CommandResult,
            **_kwargs: Any,
        ) -> CommandResult:
            await asyncio.sleep(0.8)
            return CommandResult(
                run_id=initial_result.run_id,
                status="completed",
                message="late",
            )

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)
        executor._wait_run_completion = slow_wait  # type: ignore[assignment]
        loop = asyncio.get_running_loop()
        started_at = loop.time()

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=["TASK-1", "TASK-2"],
            base_options={},
            bindings=bindings,
            timeout_seconds=1,
        )

        elapsed_seconds = loop.time() - started_at
        assert elapsed_seconds < 1.25
        assert result.status == "failed"
        assert result.metadata is not None
        per_binding = result.metadata["per_binding"]
        assert {entry["status"] for entry in per_binding} == {"timeout"}
        assert all(entry["inflight_run_continues"] is True for entry in per_binding)

    @pytest.mark.asyncio
    async def test_creates_per_binding_runs(self) -> None:
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
            {"provider_id": "gemini", "model": "gemini-pro", "binding_id": "b2"},
        ]
        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        call_count = 0
        captured_options: list[dict[str, Any]] = []
        captured_tasks: list[list[str]] = []

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            nonlocal call_count
            call_count += 1
            captured_options.append(dict(options))
            captured_tasks.append(list(tasks or []))
            return CommandResult(
                run_id=f"run-{call_count}",
                status="completed",
                message="ok",
            )

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=["task-1", "task-2", "task-3"],
            base_options={"execution_mode": "parallel"},
            bindings=bindings,
            timeout_seconds=1800,
        )

        assert call_count == 3
        assert result.status == "completed"
        assert result.metadata is not None
        assert result.metadata["binding_fanout"] is True
        assert result.metadata["binding_count"] == 3
        assert len(result.metadata["per_binding"]) == 3
        assert captured_tasks == [["task-1"], ["task-2"], ["task-3"]]
        assert result.metadata["task_assignment_mode"] == "partitioned"

        for opts in captured_options:
            assert "binding_override" in opts.get("metadata", {})
            assert opts["llm_call_timeout_seconds"] == 1800
            assert opts["director_llm_timeout_seconds"] == 1800

    @pytest.mark.asyncio
    async def test_readiness_skipped_binding_is_metadata_only(self) -> None:
        executor = self._make_executor()
        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            del workspace, tasks, options
            return CommandResult(run_id="run-live", status="completed", message="ok")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=["TASK-1"],
            base_options={"execution_mode": "parallel", "max_workers": 2},
            bindings=[{"provider_id": "live", "model": "qwen-live", "binding_id": "b-live"}],
            skipped_bindings=[
                {
                    "provider_id": "dead",
                    "model": "qwen-dead",
                    "binding_id": "b-dead",
                    "reason": "provider_connectivity_unavailable",
                }
            ],
        )

        assert result.status == "completed"
        assert mock_service.execute_director_run.await_count == 1
        assert result.metadata["readiness_skipped_count"] == 1
        per_binding = result.metadata["per_binding"]
        assert [entry["status"] for entry in per_binding] == ["completed", "skipped"]
        assert per_binding[1]["skip_reason"] == "provider_connectivity_unavailable"

    @pytest.mark.asyncio
    async def test_partial_failure_returns_failed_status(self) -> None:
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
        ]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        call_idx = 0

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return CommandResult(run_id="run-1", status="completed", message="ok")
            return CommandResult(run_id="run-2", status="failed", message="error")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=None,
            base_options={},
            bindings=bindings,
        )

        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_all_failures_returns_failed(self) -> None:
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
        ]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            return CommandResult(run_id="run-x", status="failed", message="error")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=None,
            base_options={},
            bindings=bindings,
        )

        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_readiness_skipped_binding_is_not_counted_as_failed(self) -> None:
        executor = self._make_executor()
        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            del workspace, tasks, options
            return CommandResult(
                run_id="run-live",
                status="failed",
                message="Run status: failed | error=director_materialization_quality_failed",
            )

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=["TASK-1"],
            base_options={"execution_mode": "parallel", "max_workers": 2},
            bindings=[{"provider_id": "live", "model": "qwen-live", "binding_id": "b-live"}],
            skipped_bindings=[
                {
                    "provider_id": "dead",
                    "model": "qwen-dead",
                    "binding_id": "b-dead",
                    "reason": "provider_connectivity_unavailable",
                }
            ],
        )

        assert result.status == "failed"
        assert "1 failed" in result.message
        assert "1 readiness-skipped" in result.message
        assert mock_service.execute_director_run.await_count == 1
        assert result.metadata["active_binding_count"] == 1
        assert result.metadata["readiness_skipped_count"] == 1

    @pytest.mark.asyncio
    async def test_running_task_counts_do_not_cancel_active_binding(self) -> None:
        executor = self._make_executor()
        executor._binding_status_probe_seconds = 0.001
        bindings = [{"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"}]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            return CommandResult(run_id="run-1", status="running", message="submitted")

        async def mock_wait(
            service: Any,
            initial: Any,
            timeout_seconds: int = 300,
            *,
            cancel_event: Any = None,
            abort_checker: Any = None,
        ) -> CommandResult:
            await asyncio.sleep(0.02)
            return CommandResult(run_id=initial.run_id, status="completed", message="done")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)
        mock_service.query_run_status = AsyncMock(
            return_value=CommandResult(
                run_id="run-1",
                status="running",
                message="still active",
                metadata={"task_status_counts": {"completed": 1, "failed": 2, "running": 2}},
            )
        )
        executor._wait_run_completion = mock_wait  # type: ignore[assignment]

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=None,
            base_options={},
            bindings=bindings,
        )

        assert result.status == "completed"
        mock_service.query_run_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_terminal_failed_task_counts_do_not_end_binding_wait(self) -> None:
        executor = self._make_executor()
        executor._binding_status_probe_seconds = 0.001
        bindings = [{"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"}]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            return CommandResult(run_id="run-1", status="running", message="submitted")

        async def mock_wait(
            service: Any,
            initial: Any,
            timeout_seconds: int = 300,
            *,
            cancel_event: Any = None,
            abort_checker: Any = None,
        ) -> CommandResult:
            return CommandResult(run_id=initial.run_id, status="completed", message="canonical")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)
        mock_service.query_run_status = AsyncMock(
            return_value=CommandResult(
                run_id="run-1",
                status="running",
                message="run row has not converged yet",
                metadata={"task_status_counts": {"total": 3, "completed": 1, "failed": 2}},
            )
        )
        executor._wait_run_completion = mock_wait  # type: ignore[assignment]

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=None,
            base_options={},
            bindings=bindings,
        )

        assert result.status == "completed"
        mock_service.query_run_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_workspace_taskboard_counts_do_not_end_binding_wait(self) -> None:
        executor = self._make_executor()
        executor._binding_status_probe_seconds = 0.001
        bindings = [{"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"}]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            del workspace, tasks, options
            return CommandResult(run_id="run-1", status="running", message="submitted")

        async def mock_wait(
            service: Any,
            initial: Any,
            timeout_seconds: int = 300,
            *,
            cancel_event: Any = None,
            abort_checker: Any = None,
        ) -> CommandResult:
            del service, initial, timeout_seconds, cancel_event, abort_checker
            return CommandResult(run_id="run-1", status="completed", message="canonical")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)
        mock_service.query_run_status = AsyncMock(
            return_value=CommandResult(
                run_id="run-1",
                status="running",
                message="run row has not converged yet",
                metadata={},
            )
        )
        executor._wait_run_completion = mock_wait  # type: ignore[assignment]
        executor._read_taskboard_stats = MagicMock(return_value={"total": 7, "completed": 2, "failed": 1, "pending": 4})

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=None,
            base_options={},
            bindings=bindings,
        )

        assert result.status == "completed"
        mock_service.query_run_status.assert_not_awaited()

    def test_director_dispatch_timeout_uses_stage_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        executor = self._make_executor()
        for key in (
            "KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS",
            "KERNELONE_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS",
            "KERNELONE_DIRECTOR_LLM_TIMEOUT_MAX_SECONDS",
        ):
            monkeypatch.delenv(key, raising=False)

        timeout = executor._director_dispatch_timeout_seconds({"timeout": 300}, task_count=5)

        assert timeout == 300

    def test_director_dispatch_timeout_covers_context_llm_budget(self) -> None:
        executor = self._make_executor()

        timeout = executor._director_dispatch_timeout_seconds(
            {"timeout": 300, "llm_call_timeout_seconds": 1800},
            task_count=5,
        )

        assert timeout == 1860

    def test_director_dispatch_timeout_does_not_expand_to_batch_budget(self) -> None:
        executor = self._make_executor()

        timeout = executor._director_dispatch_timeout_seconds(
            {"timeout": 7200, "llm_call_timeout_seconds": 1800},
            task_count=5,
        )

        assert timeout == 1860

    def test_director_dispatch_timeout_covers_env_llm_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        executor = self._make_executor()
        monkeypatch.setenv("KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS", "1800")

        timeout = executor._director_dispatch_timeout_seconds({"timeout": 300}, task_count=5)

        assert timeout == 1860


class TestBindingOverrideInWorkerThread:
    """Tests for binding override application in _run_role_adapter_in_worker."""

    def test_applies_binding_override(self) -> None:
        from polaris.cells.orchestration.workflow_runtime.public import (
            UnifiedOrchestrationService,
        )

        mock_adapter = MagicMock()
        mock_adapter.execute = AsyncMock(return_value={"success": True})
        binding_override = {
            "provider_id": "test-provider",
            "model": "test-model",
            "binding_id": "test-binding",
        }

        with (
            patch("polaris.kernelone.llm.runtime_config.set_role_binding_override") as mock_set,
            patch("polaris.kernelone.llm.runtime_config.clear_role_provider_override") as mock_clear,
        ):
            UnifiedOrchestrationService._run_role_adapter_in_worker(
                mock_adapter, "task-1", {"input": "test"}, {}, binding_override
            )

        mock_set.assert_called_once_with(
            "director",
            provider_id="test-provider",
            model="test-model",
            binding_id="test-binding",
            fanout_locked=True,
        )
        mock_clear.assert_called_once_with("director")

    def test_no_override_when_binding_is_none(self) -> None:
        from polaris.cells.orchestration.workflow_runtime.public import (
            UnifiedOrchestrationService,
        )

        mock_adapter = MagicMock()
        mock_adapter.execute = AsyncMock(return_value={"success": True})

        with patch("polaris.kernelone.llm.runtime_config.set_role_binding_override") as mock_set:
            UnifiedOrchestrationService._run_role_adapter_in_worker(mock_adapter, "task-1", {"input": "test"}, {}, None)

        mock_set.assert_not_called()


class TestThreeReachableBindingsAllExecuted:
    """Integration-style test: three reachable bindings all produce evidence."""

    @pytest.mark.asyncio
    async def test_three_bindings_three_runs(self) -> None:
        from pathlib import Path

        from polaris.cells.factory.pipeline.internal.factory_run_service import (
            OrchestrationStageExecutor,
        )
        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        executor = OrchestrationStageExecutor.__new__(OrchestrationStageExecutor)
        executor.workspace = Path(".")
        executor._binding_timeout_counts = {}
        executor._quarantined_bindings = set()
        _attach_canonical_wait(executor)

        bindings = [{"provider_id": f"provider-{i}", "model": f"model-{i}", "binding_id": f"b{i}"} for i in range(3)]

        run_counter = 0

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            nonlocal run_counter
            run_counter += 1
            return CommandResult(
                run_id=f"run-{run_counter}",
                status="completed",
                message="ok",
            )

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=["task-1", "task-2", "task-3"],
            base_options={"execution_mode": "parallel"},
            bindings=bindings,
        )

        assert run_counter == 3
        assert result.status == "completed"
        assert result.metadata is not None
        per_binding = result.metadata["per_binding"]
        assert len(per_binding) == 3
        for idx, entry in enumerate(per_binding, start=1):
            assert entry["status"] == "completed"
            assert entry["run_id"].startswith("run-")
            assert entry["assigned_tasks"] == [f"task-{idx}"]


class TestOneExecutedTwoNotExecutedFails:
    """Test that 1 executed + 2 not executed must FAIL."""

    @pytest.mark.asyncio
    async def test_partial_execution_is_failure(self) -> None:
        from pathlib import Path

        from polaris.cells.factory.pipeline.internal.factory_run_service import (
            OrchestrationStageExecutor,
        )
        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        executor = OrchestrationStageExecutor.__new__(OrchestrationStageExecutor)
        executor.workspace = Path(".")
        executor._binding_timeout_counts = {}
        executor._quarantined_bindings = set()
        _attach_canonical_wait(executor)

        bindings = [{"provider_id": f"provider-{i}", "model": f"model-{i}", "binding_id": f"b{i}"} for i in range(3)]

        call_count = 0

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return CommandResult(run_id="run-1", status="completed", message="ok")
            return CommandResult(run_id=f"run-{call_count}", status="failed", message="timeout")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=["task-1", "task-2", "task-3"],
            base_options={},
            bindings=bindings,
        )

        assert call_count == 3
        assert result.status == "failed"
        assert result.metadata is not None
        failed_count = sum(1 for pb in result.metadata["per_binding"] if pb["status"] == "failed")
        assert failed_count == 2


class TestUnreachableProviderFailClosed:
    """Test that unreachable providers are filtered out before fanout."""

    def test_unreachable_provider_excluded_but_reachable_binding_kept(self) -> None:
        from pathlib import Path

        executor_any = MagicMock()
        executor_any.workspace = Path(".")

        slots = []
        for pid, model in [("openai", "gpt-4"), ("dead-end", "local-7b")]:
            slot = MagicMock()
            slot.provider_id = pid
            slot.model = model
            slot.binding_id = f"director:0:{pid}:{model}"
            slots.append(slot)

        with (
            patch(
                "polaris.kernelone.llm.runtime_config.get_role_binding_slots",
                return_value=slots,
            ),
            patch(
                "polaris.cells.orchestration.pm_dispatch.internal.dispatch_pipeline._reachable_provider_pool",
                return_value=["openai"],
            ),
        ):
            from polaris.cells.factory.pipeline.internal.factory_run_service import (
                OrchestrationStageExecutor,
            )

            executor = OrchestrationStageExecutor.__new__(OrchestrationStageExecutor)
            executor.workspace = Path(".")
            executor._binding_timeout_counts = {}
            executor._quarantined_bindings = set()
            result = executor._resolve_director_binding_fanout()

        assert result == [
            {
                "provider_id": "openai",
                "model": "gpt-4",
                "binding_id": "director:0:openai:gpt-4",
            }
        ]


class TestFanoutWaitForAllRuns:
    """Verify fanout waits for every submitted run before returning."""

    def _make_executor(self) -> Any:
        from pathlib import Path

        from polaris.cells.factory.pipeline.internal.factory_run_service import (
            OrchestrationStageExecutor,
        )

        executor = OrchestrationStageExecutor.__new__(OrchestrationStageExecutor)
        executor.workspace = Path(".")
        executor._binding_timeout_counts = {}
        executor._quarantined_bindings = set()
        return _attach_canonical_wait(executor)

    @pytest.mark.asyncio
    async def test_terminal_lifecycle_submissions_still_require_canonical_wait(self) -> None:
        """A terminal CommandResult is lifecycle state, not completion authority."""
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
        ]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            return CommandResult(run_id="run-1", status="completed", message="ok")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        with patch.object(
            executor,
            "_wait_run_completion",
            new_callable=AsyncMock,
            side_effect=_canonical_wait_result,
        ) as mock_wait:
            result = await executor._execute_director_binding_fanout(
                authority_port=executor._test_authority_port,
                service=mock_service,
                workspace=".",
                tasks=None,
                base_options={},
                bindings=bindings,
            )
            assert mock_wait.await_count == 2

        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_non_terminal_runs_waited_individually(self) -> None:
        """Non-terminal runs should be waited via _wait_run_completion."""
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
            {"provider_id": "gemini", "model": "gemini-pro", "binding_id": "b2"},
        ]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        call_count = 0

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            nonlocal call_count
            call_count += 1
            return CommandResult(run_id=f"run-{call_count}", status="pending", message="submitted")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        wait_count = 0

        async def mock_wait(
            service: Any,
            initial: Any,
            timeout_seconds: int = 300,
            *,
            cancel_event: Any = None,
            abort_checker: Any = None,
        ) -> CommandResult:
            nonlocal wait_count
            wait_count += 1
            return CommandResult(
                run_id=initial.run_id,
                status="completed",
                message="done",
            )

        executor._wait_run_completion = mock_wait  # type: ignore[assignment]

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=None,
            base_options={},
            bindings=bindings,
        )

        assert wait_count == 3
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_one_cancelled_rest_completed_yields_failed(self) -> None:
        """If any run is cancelled, merged status must be failed."""
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
        ]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        call_count = 0

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            nonlocal call_count
            call_count += 1
            return CommandResult(run_id=f"run-{call_count}", status="pending", message="submitted")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        async def mock_wait(
            service: Any,
            initial: Any,
            timeout_seconds: int = 300,
            *,
            cancel_event: Any = None,
            abort_checker: Any = None,
        ) -> CommandResult:
            if initial.run_id == "run-1":
                return CommandResult(run_id="run-1", status="completed", message="ok")
            return CommandResult(run_id="run-2", status="cancelled", message="factory_cancelled")

        executor._wait_run_completion = mock_wait  # type: ignore[assignment]

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=None,
            base_options={},
            bindings=bindings,
        )

        assert result.status == "failed"


class TestFanoutBindingMetadata:
    """Verify per_binding metadata includes all required fields."""

    def _make_executor(self) -> Any:
        from pathlib import Path

        from polaris.cells.factory.pipeline.internal.factory_run_service import (
            OrchestrationStageExecutor,
        )

        executor = OrchestrationStageExecutor.__new__(OrchestrationStageExecutor)
        executor.workspace = Path(".")
        executor._binding_timeout_counts = {}
        executor._quarantined_bindings = set()
        return _attach_canonical_wait(executor)

    @pytest.mark.asyncio
    async def test_per_binding_has_all_required_fields(self) -> None:
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
            {"provider_id": "gemini", "model": "gemini-pro", "binding_id": "b2"},
        ]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        call_count = 0

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            nonlocal call_count
            call_count += 1
            return CommandResult(
                run_id=f"run-{call_count}",
                status="completed",
                message=f"ok-{call_count}",
            )

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=["task-1", "task-2", "task-3"],
            base_options={},
            bindings=bindings,
        )

        assert result.metadata is not None
        per_binding = result.metadata["per_binding"]
        assert len(per_binding) == 3

        required_keys = {"provider_id", "model", "binding_id", "run_id", "status", "message"}
        for idx, entry in enumerate(per_binding):
            assert required_keys.issubset(entry.keys()), f"Missing keys in per_binding[{idx}]"
            assert entry["provider_id"] == bindings[idx]["provider_id"]
            assert entry["model"] == bindings[idx]["model"]
            assert entry["binding_id"] == bindings[idx]["binding_id"]
            assert entry["run_id"] == f"run-{idx + 1}"
            assert entry["status"] == "completed"
            assert entry["message"] == f"ok-{idx + 1}"
            assert entry["assigned_tasks"] == [f"task-{idx + 1}"]

    @pytest.mark.asyncio
    async def test_per_binding_records_failure_details(self) -> None:
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
        ]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        call_idx = 0

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return CommandResult(run_id="run-1", status="completed", message="ok")
            return CommandResult(run_id="run-2", status="failed", message="provider_timeout")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=None,
            base_options={},
            bindings=bindings,
        )

        assert result.status == "failed"
        assert result.metadata is not None
        per_binding = result.metadata["per_binding"]
        assert per_binding[0]["status"] == "completed"
        assert per_binding[1]["status"] == "failed"
        assert per_binding[1]["message"] == "provider_timeout"


class TestBindingCoverageValidation:
    """Tests for _validate_director_binding_coverage gate."""

    def _make_executor(self) -> Any:
        from pathlib import Path

        from polaris.cells.factory.pipeline.internal.factory_run_service import (
            OrchestrationStageExecutor,
        )

        executor = OrchestrationStageExecutor.__new__(OrchestrationStageExecutor)
        executor.workspace = Path(".")
        return executor

    def test_all_bindings_covered_returns_ok(self) -> None:
        executor = self._make_executor()

        with (
            patch(
                "polaris.cells.factory.pipeline.internal.bench_gates.resolve_expected_llm_bindings",
                return_value={"director": ["openai:gpt-4", "anthropic:claude-3"]},
            ),
            patch(
                "polaris.cells.factory.pipeline.internal.bench_gates.collect_llm_events",
                return_value=[
                    MagicMock(provider="openai", model="gpt-4"),
                    MagicMock(provider="anthropic", model="claude-3"),
                ],
            ),
            patch(
                "polaris.cells.factory.pipeline.internal.bench_gates.build_llm_route_audit",
                return_value={"ok": True},
            ),
        ):
            ok, signals = executor._validate_director_binding_coverage()

        assert ok is True
        assert signals == []

    def test_missing_binding_returns_failure(self) -> None:
        executor = self._make_executor()

        with (
            patch(
                "polaris.cells.factory.pipeline.internal.bench_gates.resolve_expected_llm_bindings",
                return_value={"director": ["openai:gpt-4", "anthropic:claude-3", "gemini:gemini-pro"]},
            ),
            patch(
                "polaris.cells.factory.pipeline.internal.bench_gates.collect_llm_events",
                return_value=[MagicMock(provider="openai", model="gpt-4")],
            ),
            patch(
                "polaris.cells.factory.pipeline.internal.bench_gates.build_llm_route_audit",
                return_value={
                    "ok": False,
                    "roles": {
                        "director": {
                            "missing_bindings": ["anthropic:claude-3", "gemini:gemini-pro"],
                            "observed_count": 1,
                            "fail_closed_count": 0,
                        }
                    },
                },
            ),
        ):
            ok, signals = executor._validate_director_binding_coverage()

        assert ok is False
        assert len(signals) == 1
        assert signals[0]["code"] == "director.binding_coverage_incomplete"
        assert "anthropic:claude-3" in signals[0]["missing_bindings"]
        assert "gemini:gemini-pro" in signals[0]["missing_bindings"]

    def test_no_evidence_at_all_returns_failure(self) -> None:
        executor = self._make_executor()

        with (
            patch(
                "polaris.cells.factory.pipeline.internal.bench_gates.resolve_expected_llm_bindings",
                return_value={"director": ["openai:gpt-4"]},
            ),
            patch(
                "polaris.cells.factory.pipeline.internal.bench_gates.collect_llm_events",
                return_value=[],
            ),
            patch(
                "polaris.cells.factory.pipeline.internal.bench_gates.build_llm_route_audit",
                return_value={
                    "ok": False,
                    "roles": {"director": {"missing_bindings": [], "observed_count": 0, "fail_closed_count": 0}},
                },
            ),
        ):
            ok, signals = executor._validate_director_binding_coverage()

        assert ok is False
        assert signals[0]["code"] == "director.no_real_llm_evidence"

    def test_no_configured_bindings_skips_gate(self) -> None:
        executor = self._make_executor()

        with patch(
            "polaris.cells.factory.pipeline.internal.bench_gates.resolve_expected_llm_bindings",
            return_value={"director": []},
        ):
            ok, signals = executor._validate_director_binding_coverage()

        assert ok is True
        assert signals == []


class TestFanoutPartialFailureNotCompleted:
    """Behavioral test: fanout with partial/failed child must NOT be overall completed."""

    def _make_executor(self) -> Any:
        from pathlib import Path

        from polaris.cells.factory.pipeline.internal.factory_run_service import (
            OrchestrationStageExecutor,
        )

        executor = OrchestrationStageExecutor.__new__(OrchestrationStageExecutor)
        executor.workspace = Path(".")
        executor._binding_timeout_counts = {}
        executor._quarantined_bindings = set()
        return _attach_canonical_wait(executor)

    @pytest.mark.asyncio
    async def test_partial_failure_must_not_be_completed(self) -> None:
        """When 1 of 3 bindings fails, merged status must be 'failed', not 'completed'."""
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
            {"provider_id": "gemini", "model": "gemini-pro", "binding_id": "b2"},
        ]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        call_count = 0

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return CommandResult(run_id=f"run-{call_count}", status="completed", message="ok")
            return CommandResult(run_id="run-3", status="failed", message="provider_timeout")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=["task-1", "task-2", "task-3"],
            base_options={"execution_mode": "parallel"},
            bindings=bindings,
        )

        # Critical: partial failure must NOT yield 'completed'
        assert result.status == "failed"
        assert result.status != "completed"

        # Verify per-binding metadata
        assert result.metadata is not None
        per_binding = result.metadata["per_binding"]
        assert len(per_binding) == 3

        # Count successes and failures
        success_count = sum(1 for pb in per_binding if pb["status"] in {"completed", "success"})
        fail_count = sum(1 for pb in per_binding if pb["status"] not in {"completed", "success"})
        assert success_count == 2
        assert fail_count == 1

    @pytest.mark.asyncio
    async def test_single_failure_must_not_be_completed(self) -> None:
        """When 1 of 2 bindings fails, merged status must be 'failed'."""
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
        ]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            return CommandResult(run_id="run-1", status="completed", message="ok")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        # Override one binding to fail
        call_count = 0

        async def failing_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return CommandResult(run_id="run-1", status="completed", message="ok")
            return CommandResult(run_id="run-2", status="failed", message="error")

        mock_service.execute_director_run = AsyncMock(side_effect=failing_execute)

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=None,
            base_options={},
            bindings=bindings,
        )

        # Must be failed, not completed
        assert result.status == "failed"
        assert result.status != "completed"

    @pytest.mark.asyncio
    async def test_all_success_yields_completed(self) -> None:
        """When all bindings succeed, merged status must be 'completed'."""
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
        ]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            return CommandResult(run_id="run-1", status="completed", message="ok")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=None,
            base_options={},
            bindings=bindings,
        )

        # All success must yield completed
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_exception_in_binding_must_not_be_completed(self) -> None:
        """When one binding raises exception, merged status must be 'failed'."""
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
        ]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        call_count = 0

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return CommandResult(run_id="run-1", status="completed", message="ok")
            raise RuntimeError("Connection timeout")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=None,
            base_options={},
            bindings=bindings,
        )

        # Exception must yield failed
        assert result.status == "failed"
        assert result.status != "completed"


class TestRunRoleAdapterInWorkerFanoutLock:
    """Tests that _run_role_adapter_in_worker sets _fanout_locked marker."""

    @pytest.mark.asyncio
    async def test_binding_override_sets_fanout_locked_marker(self) -> None:
        """When binding_override is provided, _fanout_locked must be True."""

        from polaris.cells.orchestration.workflow_runtime.public import (
            UnifiedOrchestrationService,
        )
        from polaris.kernelone.llm.runtime_config import (
            get_role_binding_override,
        )

        captured_overrides: dict[str, Any] = {}

        class _StubAdapter:
            async def execute(self, task_id: str, input_data: Any, context: Any) -> dict[str, Any]:
                # Capture the binding override state inside the worker
                binding = get_role_binding_override("director")
                captured_overrides["binding"] = dict(binding) if binding else None
                captured_overrides["fanout_locked"] = (binding or {}).get("_fanout_locked", "")
                return {"status": "ok"}

        adapter = _StubAdapter()
        binding_override = {
            "provider_id": "openai",
            "model": "gpt-4",
            "binding_id": "director:0:openai:gpt-4",
        }

        # _run_role_adapter_in_worker uses asyncio.run() internally,
        # which cannot be called from a running event loop. Run in a thread.
        def _run_in_thread() -> dict[str, Any]:
            return UnifiedOrchestrationService._run_role_adapter_in_worker(
                adapter=adapter,
                task_id="test-task",
                input_data={},
                context={},
                binding_override=binding_override,
            )

        result = await asyncio.to_thread(_run_in_thread)
        assert result == {"status": "ok"}
        assert captured_overrides["fanout_locked"] == "true"
        assert captured_overrides["binding"]["provider_id"] == "openai"
        assert captured_overrides["binding"]["model"] == "gpt-4"

    @pytest.mark.asyncio
    async def test_no_binding_override_no_fanout_locked(self) -> None:
        """When binding_override is None, no _fanout_locked marker should be set."""
        from polaris.cells.orchestration.workflow_runtime.public import (
            UnifiedOrchestrationService,
        )
        from polaris.kernelone.llm.runtime_config import (
            get_role_binding_override,
        )

        captured_overrides: dict[str, Any] = {}

        class _StubAdapter:
            async def execute(self, task_id: str, input_data: Any, context: Any) -> dict[str, Any]:
                binding = get_role_binding_override("director")
                captured_overrides["binding"] = dict(binding) if binding else None
                return {"status": "ok"}

        adapter = _StubAdapter()

        def _run_in_thread() -> dict[str, Any]:
            return UnifiedOrchestrationService._run_role_adapter_in_worker(
                adapter=adapter,
                task_id="test-task",
                input_data={},
                context={},
                binding_override=None,
            )

        result = await asyncio.to_thread(_run_in_thread)
        assert result == {"status": "ok"}
        assert captured_overrides["binding"] is None


class TestThreeWayMockBindingPropagation:
    """Verify that each Director fanout binding produces its own terminal LLM evidence.

    Tests the full propagation chain: factory fanout -> worker thread -> contextvar
    -> LLMInvoker -> profile provider/model application.
    """

    @pytest.mark.asyncio
    async def test_three_bindings_invoke_three_distinct_provider_models(self) -> None:
        """Each binding must result in a distinct provider/model being passed to the LLM invoker."""
        from polaris.cells.orchestration.workflow_runtime.public import (
            UnifiedOrchestrationService,
        )
        from polaris.kernelone.llm.runtime_config import (
            get_role_binding_override,
        )

        bindings = [
            {"provider_id": "openai_compat-1781448928833", "model": "qwen3.6-27b-gpu1", "binding_id": "b-gpu1"},
            {"provider_id": "openai_compat-1781448928834", "model": "qwen3.6-27b-gpu0", "binding_id": "b-gpu0"},
            {"provider_id": "openai_compat-1781448928835", "model": "qwen3.6-27b-int4", "binding_id": "b-int4"},
        ]

        captured_invocations: list[dict[str, str]] = []

        class _RecordingAdapter:
            async def execute(self, task_id: str, input_data: Any, context: Any) -> dict[str, Any]:
                binding = get_role_binding_override("director")
                if binding:
                    captured_invocations.append(
                        {
                            "provider_id": binding.get("provider_id", ""),
                            "model": binding.get("model", ""),
                            "binding_id": binding.get("binding_id", ""),
                            "fanout_locked": binding.get("_fanout_locked", ""),
                        }
                    )
                return {"success": True, "provider": binding.get("provider_id") if binding else None}

        for binding in bindings:

            def _run_in_thread(b: dict[str, str] = binding) -> dict[str, Any]:
                return UnifiedOrchestrationService._run_role_adapter_in_worker(
                    adapter=_RecordingAdapter(),
                    task_id=f"task-{b['binding_id']}",
                    input_data={},
                    context={},
                    binding_override=b,
                )

            await asyncio.to_thread(_run_in_thread)

        # All 3 bindings must have been captured
        assert len(captured_invocations) == 3

        # Each invocation must have distinct provider_id
        provider_ids = [inv["provider_id"] for inv in captured_invocations]
        assert len(set(provider_ids)) == 3, f"Expected 3 distinct providers, got: {provider_ids}"

        # Each invocation must have distinct model
        models = [inv["model"] for inv in captured_invocations]
        assert len(set(models)) == 3, f"Expected 3 distinct models, got: {models}"

        # Each invocation must be fanout_locked
        for inv in captured_invocations:
            assert inv["fanout_locked"] == "true", f"Expected fanout_locked=true, got: {inv['fanout_locked']}"

        # Verify specific bindings
        assert captured_invocations[0]["provider_id"] == "openai_compat-1781448928833"
        assert captured_invocations[0]["model"] == "qwen3.6-27b-gpu1"
        assert captured_invocations[1]["provider_id"] == "openai_compat-1781448928834"
        assert captured_invocations[1]["model"] == "qwen3.6-27b-gpu0"
        assert captured_invocations[2]["provider_id"] == "openai_compat-1781448928835"
        assert captured_invocations[2]["model"] == "qwen3.6-27b-int4"

    def test_profile_for_healthy_binding_applies_override(self) -> None:
        """_profile_for_healthy_binding must apply binding override provider/model to profile."""
        from types import SimpleNamespace

        from polaris.cells.roles.kernel.public import LLMInvoker
        from polaris.kernelone.llm.runtime_config import set_role_binding_override

        profile = SimpleNamespace(
            role_id="director",
            provider_id="original_provider",
            model="original_model",
        )

        # Set binding override
        set_role_binding_override(
            "director",
            provider_id="openai_compat-1781448928833",
            model="qwen3.6-27b-gpu1",
            binding_id="b-gpu1",
            fanout_locked=True,
        )

        result = LLMInvoker._profile_for_healthy_binding("director", profile)

        # Profile must have the overridden provider/model
        assert result.provider_id == "openai_compat-1781448928833"
        assert result.model == "qwen3.6-27b-gpu1"

    def test_profile_for_healthy_binding_applies_all_three_bindings(self) -> None:
        """Each of the 3 bindings must be correctly applied to the profile."""
        from types import SimpleNamespace

        from polaris.cells.roles.kernel.public import LLMInvoker
        from polaris.kernelone.llm.runtime_config import set_role_binding_override

        bindings = [
            ("openai_compat-1781448928833", "qwen3.6-27b-gpu1", "b-gpu1"),
            ("openai_compat-1781448928834", "qwen3.6-27b-gpu0", "b-gpu0"),
            ("openai_compat-1781448928835", "qwen3.6-27b-int4", "b-int4"),
        ]

        for provider_id, model, binding_id in bindings:
            profile = SimpleNamespace(
                role_id="director",
                provider_id="original_provider",
                model="original_model",
            )

            set_role_binding_override(
                "director",
                provider_id=provider_id,
                model=model,
                binding_id=binding_id,
                fanout_locked=True,
            )

            result = LLMInvoker._profile_for_healthy_binding("director", profile)

            assert result.provider_id == provider_id, f"Expected {provider_id}, got {result.provider_id}"
            assert result.model == model, f"Expected {model}, got {result.model}"

    def test_missing_binding_fails_closed(self) -> None:
        """When a binding is missing/unreachable, the call must fail-closed."""
        from types import SimpleNamespace

        from polaris.cells.roles.kernel.public import LLMInvoker
        from polaris.kernelone.llm.runtime_config import mark_role_binding_unhealthy, set_role_binding_override

        profile = SimpleNamespace(
            role_id="director",
            provider_id="original_provider",
            model="original_model",
        )

        # Set binding override for gpu0
        set_role_binding_override(
            "director",
            provider_id="openai_compat-1781448928834",
            model="qwen3.6-27b-gpu0",
            binding_id="b-gpu0",
            fanout_locked=True,
        )

        # Mark gpu0 as unhealthy
        mark_role_binding_unhealthy(
            "director",
            provider_id="openai_compat-1781448928834",
            model="qwen3.6-27b-gpu0",
            binding_id="b-gpu0",
        )

        # _profile_for_healthy_binding should still return the override profile
        # (fail-closed, not fallback to another binding)
        result = LLMInvoker._profile_for_healthy_binding("director", profile)
        assert result.provider_id == "openai_compat-1781448928834"
        assert result.model == "qwen3.6-27b-gpu0"

    @pytest.mark.asyncio
    async def test_three_bindings_route_audit_passes(self) -> None:
        """Route audit must pass when all 3 bindings produce terminal evidence."""
        from polaris.cells.factory.pipeline.internal.bench_gates import build_llm_route_audit

        # Simulate 3 terminal LLM events, one per binding
        events = [
            {
                "event": "llm_call_end",
                "role": "director",
                "provider_id": "openai_compat-1781448928833",
                "model": "qwen3.6-27b-gpu1",
                "binding_id": "b-gpu1",
                "terminal": True,
                "invocation": True,
                "source": "llm",
            },
            {
                "event": "llm_call_end",
                "role": "director",
                "provider_id": "openai_compat-1781448928834",
                "model": "qwen3.6-27b-gpu0",
                "binding_id": "b-gpu0",
                "terminal": True,
                "invocation": True,
                "source": "llm",
            },
            {
                "event": "llm_call_end",
                "role": "director",
                "provider_id": "openai_compat-1781448928835",
                "model": "qwen3.6-27b-int4",
                "binding_id": "b-int4",
                "terminal": True,
                "invocation": True,
                "source": "llm",
            },
        ]

        expected_bindings = {
            "director": [
                {"provider_id": "openai_compat-1781448928833", "model": "qwen3.6-27b-gpu1", "binding_id": "b-gpu1"},
                {"provider_id": "openai_compat-1781448928834", "model": "qwen3.6-27b-gpu0", "binding_id": "b-gpu0"},
                {"provider_id": "openai_compat-1781448928835", "model": "qwen3.6-27b-int4", "binding_id": "b-int4"},
            ]
        }

        # Only check director role (other roles have no events)
        audit = build_llm_route_audit(
            events,
            expected_bindings=expected_bindings,
            required_roles=("director",),
        )

        assert audit["ok"] is True, f"Route audit failed: {audit.get('summary')}"
        director_result = audit["roles"]["director"]
        assert director_result["ok"] is True
        assert director_result["observed_count"] == 3
        assert len(director_result["missing_bindings"]) == 0

    @pytest.mark.asyncio
    async def test_missing_one_binding_route_audit_fails(self) -> None:
        """Route audit must fail when one binding is missing."""
        from polaris.cells.factory.pipeline.internal.bench_gates import build_llm_route_audit

        # Only 2 events (missing int4)
        events = [
            {
                "event": "llm_call_end",
                "role": "director",
                "provider_id": "openai_compat-1781448928833",
                "model": "qwen3.6-27b-gpu1",
                "binding_id": "b-gpu1",
                "terminal": True,
                "invocation": True,
                "source": "llm",
            },
            {
                "event": "llm_call_end",
                "role": "director",
                "provider_id": "openai_compat-1781448928834",
                "model": "qwen3.6-27b-gpu0",
                "binding_id": "b-gpu0",
                "terminal": True,
                "invocation": True,
                "source": "llm",
            },
        ]

        expected_bindings = {
            "director": [
                {"provider_id": "openai_compat-1781448928833", "model": "qwen3.6-27b-gpu1", "binding_id": "b-gpu1"},
                {"provider_id": "openai_compat-1781448928834", "model": "qwen3.6-27b-gpu0", "binding_id": "b-gpu0"},
                {"provider_id": "openai_compat-1781448928835", "model": "qwen3.6-27b-int4", "binding_id": "b-int4"},
            ]
        }

        # Only check director role
        audit = build_llm_route_audit(
            events,
            expected_bindings=expected_bindings,
            required_roles=("director",),
        )

        assert audit["ok"] is False, "Route audit should fail when int4 binding is missing"
        director_result = audit["roles"]["director"]
        assert director_result["ok"] is False
        assert len(director_result["missing_bindings"]) > 0


class TestBindingTimeoutQuarantine:
    """Tests for timeout binding quarantine strategy."""

    def _make_executor(self) -> Any:
        from pathlib import Path

        from polaris.cells.factory.pipeline.internal.factory_run_service import (
            OrchestrationStageExecutor,
        )

        executor = OrchestrationStageExecutor.__new__(OrchestrationStageExecutor)
        executor.workspace = Path(".")
        executor._binding_timeout_counts = {}
        executor._quarantined_bindings = set()
        return _attach_canonical_wait(executor)

    @pytest.mark.asyncio
    async def test_first_timeout_does_not_quarantine(self) -> None:
        """First timeout should not quarantine the binding."""
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
        ]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        call_count = 0

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return CommandResult(run_id="run-1", status="timeout", message="timed out")
            return CommandResult(run_id="run-2", status="completed", message="ok")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=None,
            base_options={},
            bindings=bindings,
        )

        assert result.status == "failed"
        assert "openai:gpt-4:b0" not in executor._quarantined_bindings
        assert executor._binding_timeout_counts.get("openai:gpt-4:b0", 0) == 1

    @pytest.mark.asyncio
    async def test_second_timeout_does_not_quarantine_by_default(self) -> None:
        """Slow local models should not be quarantined after only two timeouts."""
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
        ]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        executor._binding_timeout_counts["openai:gpt-4:b0"] = 1

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            return CommandResult(run_id="run-1", status="timeout", message="timed out")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=None,
            base_options={},
            bindings=bindings,
        )

        assert result.status == "failed"
        assert "openai:gpt-4:b0" not in executor._quarantined_bindings
        assert result.metadata is not None
        assert result.metadata["timeout_quarantine_threshold"] == 4

    @pytest.mark.asyncio
    async def test_timeout_quarantine_threshold_can_be_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Operators can still skip truly bad bindings after a configured count."""
        monkeypatch.setenv("KERNELONE_FACTORY_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_COUNT", "2")
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
        ]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        executor._binding_timeout_counts["openai:gpt-4:b0"] = 1

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            return CommandResult(run_id="run-1", status="timeout", message="timed out")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=None,
            base_options={},
            bindings=bindings,
        )

        assert result.status == "failed"
        assert "openai:gpt-4:b0" in executor._quarantined_bindings
        assert result.metadata is not None
        assert result.metadata["timeout_quarantine_threshold"] == 2

    @pytest.mark.asyncio
    async def test_quarantined_binding_skipped_in_subsequent_rounds(self) -> None:
        """Quarantined bindings should be skipped in subsequent rounds."""
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
        ]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        executor._quarantined_bindings.add("openai:gpt-4:b0")

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            return CommandResult(run_id="run-1", status="completed", message="ok")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=None,
            base_options={},
            bindings=bindings,
        )

        assert result.status == "completed"
        assert mock_service.execute_director_run.call_count == 1

    @pytest.mark.asyncio
    async def test_all_quarantined_bindings_fail_closed(self) -> None:
        """Fanout must not report completion when every binding is quarantined."""
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
        ]

        for binding in bindings:
            key = f"{binding['provider_id']}:{binding['model']}:{binding['binding_id']}"
            executor._quarantined_bindings.add(key)
            executor._binding_timeout_counts[key] = 2

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock()

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=None,
            base_options={},
            bindings=bindings,
        )

        assert result.status == "failed"
        assert mock_service.execute_director_run.call_count == 0
        assert result.metadata is not None
        assert result.metadata["active_binding_count"] == 0
        assert result.metadata["quarantined_binding_count"] == 2
        assert len(result.metadata["per_binding"]) == 2

    @pytest.mark.asyncio
    async def test_quarantined_binding_appears_in_per_binding_metadata(self) -> None:
        """Quarantined bindings should appear in per_binding metadata with quarantine status."""
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
        ]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        executor._quarantined_bindings.add("openai:gpt-4:b0")
        executor._binding_timeout_counts["openai:gpt-4:b0"] = 2

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            return CommandResult(run_id="run-1", status="completed", message="ok")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=None,
            base_options={},
            bindings=bindings,
        )

        assert result.metadata is not None
        per_binding = result.metadata["per_binding"]
        assert len(per_binding) == 2

        quarantined_entry = next(e for e in per_binding if e["status"] == "quarantined")
        assert quarantined_entry["provider_id"] == "openai"
        assert quarantined_entry["model"] == "gpt-4"
        assert quarantined_entry["quarantined"] is True
        assert quarantined_entry["quarantine_reason"] == "consecutive_timeout"
        assert quarantined_entry["timeout_count"] == 2

    @pytest.mark.asyncio
    async def test_timeout_count_resets_on_success(self) -> None:
        """Timeout count should reset when binding succeeds."""
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
        ]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        executor._binding_timeout_counts["openai:gpt-4:b0"] = 1

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            return CommandResult(run_id="run-1", status="completed", message="ok")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=None,
            base_options={},
            bindings=bindings,
        )

        assert result.status == "completed"
        assert executor._binding_timeout_counts.get("openai:gpt-4:b0", 0) == 0
        assert "openai:gpt-4:b0" not in executor._quarantined_bindings

    @pytest.mark.asyncio
    async def test_route_events_include_timeout_signal(self) -> None:
        """Route events should include timeout count for timed-out bindings."""
        from polaris.cells.factory.pipeline.internal.factory_run_service import (
            OrchestrationStageExecutor,
        )

        per_binding = [
            {
                "provider_id": "openai",
                "model": "gpt-4",
                "binding_id": "b0",
                "run_id": "run-1",
                "status": "timeout",
                "message": "timed out",
                "timeout_count": 1,
            },
            {
                "provider_id": "anthropic",
                "model": "claude-3",
                "binding_id": "b1",
                "run_id": "run-2",
                "status": "completed",
                "message": "ok",
            },
        ]

        events = OrchestrationStageExecutor._build_per_binding_route_events(per_binding)

        assert len(events) == 2
        timeout_event = events[0]
        assert timeout_event["status"] == "timeout"
        assert timeout_event["timeout_count"] == 1
        assert "quarantined" not in timeout_event

        completed_event = events[1]
        assert completed_event["status"] == "completed"
        assert "timeout_count" not in completed_event

    @pytest.mark.asyncio
    async def test_route_events_include_quarantine_signal(self) -> None:
        """Route events should include quarantine signal for quarantined bindings."""
        from polaris.cells.factory.pipeline.internal.factory_run_service import (
            OrchestrationStageExecutor,
        )

        per_binding = [
            {
                "provider_id": "openai",
                "model": "gpt-4",
                "binding_id": "b0",
                "run_id": "",
                "status": "quarantined",
                "message": "Skipped due to consecutive timeouts",
                "quarantined": True,
                "quarantine_reason": "consecutive_timeout",
                "timeout_count": 2,
            },
        ]

        events = OrchestrationStageExecutor._build_per_binding_route_events(per_binding)

        assert len(events) == 1
        event = events[0]
        assert event["status"] == "quarantined"
        assert event["quarantined"] is True
        assert event["quarantine_reason"] == "consecutive_timeout"
        assert event["timeout_count"] == 2

    @pytest.mark.asyncio
    async def test_consecutive_timeouts_do_not_block_other_bindings(self) -> None:
        """Consecutive timeouts on one binding should not block other bindings."""
        executor = self._make_executor()
        bindings = [
            {"provider_id": "openai", "model": "gpt-4", "binding_id": "b0"},
            {"provider_id": "anthropic", "model": "claude-3", "binding_id": "b1"},
            {"provider_id": "gemini", "model": "gemini-pro", "binding_id": "b2"},
        ]

        from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult

        call_count = 0

        async def mock_execute(workspace: str, tasks: Any, options: Any) -> CommandResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return CommandResult(run_id="run-1", status="timeout", message="timed out")
            return CommandResult(run_id=f"run-{call_count}", status="completed", message="ok")

        mock_service = MagicMock()
        mock_service.execute_director_run = AsyncMock(side_effect=mock_execute)

        result = await executor._execute_director_binding_fanout(
            authority_port=executor._test_authority_port,
            service=mock_service,
            workspace=".",
            tasks=None,
            base_options={},
            bindings=bindings,
        )

        assert result.status == "failed"
        assert call_count == 3

        assert result.metadata is not None
        per_binding = result.metadata["per_binding"]
        assert len(per_binding) == 3

        statuses = [e["status"] for e in per_binding]
        assert "timeout" in statuses
        assert statuses.count("completed") == 2
