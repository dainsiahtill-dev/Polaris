"""Director engine variants must preserve the canonical role-runtime boundary."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from polaris.cells.roles.adapters.internal.director import adapter as adapter_module
from polaris.cells.roles.adapters.internal.director.adapter import DirectorAdapter
from polaris.cells.roles.adapters.internal.director.adapter_sequential import (
    execute_hybrid,
    execute_sequential,
)
from polaris.cells.roles.runtime.public import SequentialFailureClass


@pytest.mark.asyncio
async def test_adapter_sequential_supplies_bounded_canonical_runtime_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, Any] = {}
    budget = SimpleNamespace(max_wall_time_seconds=37)

    async def invoke_role_dialogue_with_timeout(
        message: str,
        *,
        context: dict[str, Any] | None,
        timeout_seconds: float,
        stage_label: str,
    ) -> dict[str, Any]:
        observed.update(
            message=message,
            context=context,
            timeout_seconds=timeout_seconds,
            stage_label=stage_label,
        )
        return {"content": "ok", "success": True}

    async def fake_execute_sequential(
        workspace: str,
        role_id: str,
        task: dict[str, Any],
        task_id: str,
        run_id: str,
        context: dict[str, Any] | None,
        seq_config: dict[str, Any],
        call_role_llm_with_timeout: Any,
        emit_task_trace_event: Any,
        build_director_message: Any,
    ) -> dict[str, Any]:
        del workspace, role_id, task, task_id, run_id, seq_config
        del emit_task_trace_event, build_director_message
        await call_role_llm_with_timeout("engine prompt", context=context)
        return {"success": True}

    monkeypatch.setattr(adapter_module, "execute_sequential", fake_execute_sequential)
    fake_adapter = SimpleNamespace(
        workspace="/tmp/workspace",
        role_id="director",
        _get_sequential_config=lambda context: {"budget": budget},
        _invoke_role_dialogue_with_timeout=invoke_role_dialogue_with_timeout,
        _emit_task_trace_event=lambda **kwargs: None,
        _build_director_message=lambda task, context=None: "message",
    )

    result = await DirectorAdapter._execute_sequential(
        fake_adapter,
        task={"id": "TASK-2"},
        task_id="TASK-2",
        run_id="run-1",
        context={"task_id": "TASK-2"},
    )

    assert result["success"] is True
    assert observed == {
        "message": "engine prompt",
        "context": {"task_id": "TASK-2"},
        "timeout_seconds": 37.0,
        "stage_label": "sequential",
    }


@pytest.mark.asyncio
async def test_sequential_message_and_llm_calls_share_exact_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.runtime.public import service as runtime_service

    context = {"actual_sibling_exports": {"schema_version": "trusted-v2"}}
    observed: dict[str, Any] = {}

    class FakeSequentialEngine:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs
            self.llm_caller: Any = None

        def set_context(self, **kwargs: Any) -> None:
            del kwargs

        def set_dependencies(self, *, llm_caller: Any, tool_gateway: Any) -> None:
            assert tool_gateway is None
            self.llm_caller = llm_caller

        async def execute(self, *, initial_message: str, profile: Any) -> Any:
            del profile
            observed["initial_message"] = initial_message
            observed["llm_output"] = await self.llm_caller(prompt="follow-up")
            return SimpleNamespace(
                failure_class=SequentialFailureClass.SUCCESS.value,
                termination_reason="completed",
                steps=1,
                tool_calls=0,
            )

    async def canonical_caller(
        message: str,
        *,
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        observed["provider_message"] = message
        observed["provider_context"] = context
        return {"content": "canonical-ok"}

    async def emit(**kwargs: Any) -> None:
        del kwargs

    def build_message(task: dict[str, Any], *, context: dict[str, Any] | None) -> str:
        observed["builder_task"] = task
        observed["builder_context"] = context
        return "trusted parent artifact body"

    monkeypatch.setattr(
        "polaris.cells.roles.adapters.internal.director.adapter_sequential.SequentialEngine",
        FakeSequentialEngine,
    )
    monkeypatch.setattr(runtime_service.registry, "get_profile_or_raise", lambda role: object())

    result = await execute_sequential(
        "/tmp/workspace",
        "director",
        {"id": "TASK-2"},
        "TASK-2",
        "run-1",
        context,
        {
            "budget": object(),
            "trace_level": SimpleNamespace(value="summary"),
        },
        canonical_caller,
        emit,
        build_message,
    )

    assert result["success"] is True
    assert observed["initial_message"] == "trusted parent artifact body"
    assert observed["provider_message"] == "follow-up"
    assert observed["provider_context"]["actual_sibling_exports"] == {
        "schema_version": "trusted-v2"
    }
    assert observed["builder_context"] is context


@pytest.mark.asyncio
async def test_hybrid_uses_canonical_caller_and_full_director_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.engine.public import service as engine_service
    from polaris.cells.roles.runtime.public import service as runtime_service

    context = {"actual_sibling_exports": {"schema_version": "trusted-v2"}}
    observed: dict[str, Any] = {}

    class FakeHybridEngine:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs

        async def run(self, *, task: str, context: Any) -> Any:
            observed["task"] = task
            observed["engine_task"] = context.task
            assert context.llm_caller is not None
            observed["llm_output"] = await context.llm_caller(
                prompt="hybrid follow-up",
                role="director",
                max_tokens=512,
            )
            return SimpleNamespace(
                success=True,
                strategy=SimpleNamespace(value="sequential"),
                total_steps=1,
                total_tool_calls=0,
            )

    async def canonical_caller(
        message: str,
        *,
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        observed["provider_message"] = message
        observed["provider_context"] = context
        return {"content": "canonical-ok"}

    async def emit(**kwargs: Any) -> None:
        del kwargs

    def build_message(task: dict[str, Any], *, context: dict[str, Any] | None) -> str:
        observed["builder_context"] = context
        return "trusted full Director message"

    monkeypatch.setattr(engine_service, "HybridEngine", FakeHybridEngine)
    monkeypatch.setattr(runtime_service.registry, "get_profile_or_raise", lambda role: object())

    result = await execute_hybrid(
        "/tmp/workspace",
        "director",
        {"id": "TASK-2"},
        "TASK-2",
        "run-1",
        context,
        {
            "budget": SimpleNamespace(
                max_steps=2,
                max_tool_calls_total=2,
                max_no_progress_steps=1,
                max_wall_time_seconds=10,
            )
        },
        emit,
        canonical_caller,
        build_message,
    )

    assert result["success"] is True
    assert observed["task"] == "trusted full Director message"
    assert observed["engine_task"] == "trusted full Director message"
    assert observed["provider_message"] == "hybrid follow-up"
    assert observed["provider_context"]["actual_sibling_exports"] == {
        "schema_version": "trusted-v2"
    }
    assert observed["builder_context"] is context
