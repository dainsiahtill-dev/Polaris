"""Regression coverage for role-turn facts reaching final provider requests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.kernel.transaction_factory import create_transaction_kernel
from polaris.cells.roles.kernel.internal.llm_caller import request_preparer as request_preparer_module
from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
    build_final_provider_request_snapshot,
    build_final_request_context_audit_for_request,
)
from polaris.cells.roles.kernel.internal.llm_caller.request_facts import project_role_request_facts
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer


def _pm_contract_set() -> dict[str, Any]:
    return {
        "schema_version": "polaris.validated_pm_contract_set.v1",
        "source_artifact": "tasks/plan.json",
        "tasks": [
            {
                "schema_version": "pm.task_contract.v1",
                "task_id": "TASK-1",
                "target_files": ["src/models/weather.py"],
                "scope_paths": ["src/models/weather.py"],
                "steps": ["Implement the weather model"],
                "acceptance": ["The model imports successfully"],
            }
        ],
    }


def _profile() -> SimpleNamespace:
    return SimpleNamespace(
        role_id="chief_engineer",
        provider_id="provider-a",
        provider_type="openai_compat",
        model="kimi-for-coding",
        max_context_tokens=262_144,
        tool_policy=SimpleNamespace(whitelist=()),
    )


def test_role_request_fact_projection_prefers_context_and_records_conflicts() -> None:
    context_contract = _pm_contract_set()
    metadata_contract = _pm_contract_set()
    metadata_contract["source_artifact"] = "stale-plan.json"

    projection = project_role_request_facts(
        context_override={
            "pm_task_contract": context_contract,
            "target_files": ["src/models/weather.py"],
            "temperature": 0.2,
        },
        metadata={
            "pm_task_contract": metadata_contract,
            "scope_paths": ["src/models/weather.py"],
            "temperature": 0.7,
            "unrelated_runtime_state": "must-not-leak",
        },
    )

    assert projection.context_override["pm_task_contract"] == context_contract
    assert projection.context_override["scope_paths"] == ["src/models/weather.py"]
    assert projection.context_override["temperature"] == 0.2
    assert "unrelated_runtime_state" not in projection.context_override
    assert projection.sources["temperature"] == "role_turn.context.temperature"
    assert projection.sources["scope_paths"] == "role_turn.metadata.scope_paths"
    assert projection.conflict_keys == ("pm_task_contract", "temperature")


@pytest.mark.asyncio
async def test_transaction_kernel_projects_metadata_facts_into_provider_context() -> None:
    captured_contexts: list[Any] = []

    async def _fake_call(*, context: Any, **_kwargs: Any) -> Any:
        captured_contexts.append(context)
        return SimpleNamespace(
            content='{"construction_plan":{},"scope_for_apply":[],"risk_flags":[]}',
            tool_calls=[],
            error=None,
            metadata={},
            model="kimi-for-coding",
        )

    request = SimpleNamespace(
        message="Create one project blueprint.",
        task_id="CE-PORTFOLIO-run-1",
        run_id="run-1",
        workspace=".",
        metadata={
            "pm_task_contract": _pm_contract_set(),
            "target_files": ["src/models/weather.py"],
            "scope_paths": ["src/models/weather.py"],
            "temperature": 0.2,
        },
        context_override={
            "context_os_snapshot": {},
            "_transaction_kernel_forced_tool_definitions": [],
            "_transaction_kernel_forced_tool_choice": "none",
        },
    )
    kernel = RoleExecutionKernel.create_default(
        workspace=".",
        llm_invoker=SimpleNamespace(call=_fake_call),
    )
    transaction_kernel = create_transaction_kernel(kernel, "chief_engineer", _profile(), request)

    response = await transaction_kernel.llm_provider(
        {
            "messages": [
                {"role": "system", "content": "You are Chief Engineer."},
                {"role": "user", "content": request.message},
            ],
            "tools": None,
            "tool_choice": "none",
        }
    )

    assert response["content"].startswith("{")
    assert len(captured_contexts) == 1
    projected = captured_contexts[0].context_override
    assert projected["pm_task_contract"] == request.metadata["pm_task_contract"]
    assert projected["target_files"] == ["src/models/weather.py"]
    assert projected["scope_paths"] == ["src/models/weather.py"]
    assert projected["temperature"] == 0.2
    assert projected["request_fact_provenance"]["sources"]["temperature"] == ("role_turn.metadata.temperature")


@pytest.mark.asyncio
async def test_stream_transaction_kernel_uses_same_request_fact_projection() -> None:
    captured_contexts: list[Any] = []

    async def _fake_call_stream(*, context: Any, **_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        captured_contexts.append(context)
        if False:
            yield {}

    request = SimpleNamespace(
        message="Review the PM portfolio.",
        task_id="CE-PORTFOLIO-run-stream",
        run_id="run-stream",
        workspace=".",
        metadata={
            "pm_task_contract": _pm_contract_set(),
            "target_files": ["src/models/weather.py"],
            "scope_paths": ["src/models/weather.py"],
            "temperature": 0.2,
        },
        context_override={"context_os_snapshot": {}},
    )
    kernel = RoleExecutionKernel.create_default(
        workspace=".",
        llm_invoker=SimpleNamespace(call_stream=_fake_call_stream),
    )
    transaction_kernel = create_transaction_kernel(kernel, "chief_engineer", _profile(), request)
    assert transaction_kernel.llm_provider_stream is not None

    async for _event in transaction_kernel.llm_provider_stream(
        {
            "messages": [
                {"role": "system", "content": "You are Chief Engineer."},
                {"role": "user", "content": request.message},
            ]
        }
    ):
        pass

    assert len(captured_contexts) == 1
    projected = captured_contexts[0].context_override
    assert projected["pm_task_contract"] == request.metadata["pm_task_contract"]
    assert projected["target_files"] == ["src/models/weather.py"]
    assert projected["scope_paths"] == ["src/models/weather.py"]
    assert projected["temperature"] == 0.2


@pytest.mark.asyncio
async def test_request_preparer_and_audit_share_structured_facts_and_temperature() -> None:
    context = SimpleNamespace(
        message="Create one project blueprint.",
        domain="code",
        context_override={
            request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
                {"role": "system", "content": "You are Chief Engineer."},
                {"role": "user", "content": "Create one project blueprint."},
            ],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: [],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY: "none",
            "pm_task_contract": _pm_contract_set(),
            "target_files": ["src/models/weather.py"],
            "scope_paths": ["src/models/weather.py"],
            "temperature": 0.2,
            "request_sampling": {
                "schema_version": "roles.kernel.request_sampling.v1",
                "temperature": 0.7,
                "temperature_source": "stale.continuation.audit",
            },
            "request_fact_provenance": {
                "schema_version": "roles.kernel.request_fact_provenance.v1",
                "precedence": "context_over_metadata",
                "sources": {
                    "pm_task_contract": "role_turn.context.pm_task_contract",
                    "scope_paths": "role_turn.context.scope_paths",
                    "target_files": "role_turn.context.target_files",
                    "temperature": "role_turn.context.temperature",
                },
                "conflict_keys": [],
            },
        },
    )
    preparer = LLMRequestPreparer(workspace=".", formatter=None, model_catalog=None)

    prepared = await preparer._prepare_llm_request(
        profile=_profile(),
        system_prompt="You are Chief Engineer.",
        context=context,
        temperature=0.7,
        max_tokens=4000,
        stream=False,
    )
    audit = build_final_request_context_audit_for_request(
        ai_request=prepared.ai_request,
        prepared=prepared,
        profile=_profile(),
    )
    snapshot = build_final_provider_request_snapshot(
        ai_request=prepared.ai_request,
        prepared=prepared,
        profile=_profile(),
    )

    assert prepared.request_options["temperature"] == 0.2
    assert prepared.ai_request.context["pm_task_contract"] == _pm_contract_set()
    assert prepared.ai_request.context["target_files"] == ["src/models/weather.py"]
    assert prepared.ai_request.context["scope_paths"] == ["src/models/weather.py"]
    assert prepared.ai_request.context["request_sampling"] == {
        "schema_version": "roles.kernel.request_sampling.v1",
        "temperature": 0.2,
        "temperature_source": "role_turn.context.temperature",
    }
    assert audit["coverage"]["has_pm_contract"] is True
    assert audit["coverage"]["has_target_files"] is True
    assert audit["request_metadata_summary"]["has_pm_contract"] is True
    assert audit["request_metadata_summary"]["has_target_scope"] is True
    assert audit["sampling"]["temperature"] == 0.2
    assert audit["sampling"]["temperature_source"] == "role_turn.context.temperature"
    assert snapshot["sampling"] == audit["sampling"]
