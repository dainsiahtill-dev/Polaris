"""Regression coverage for role-turn facts reaching final provider requests."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    QueryFactEventsV1,
    bootstrap_fact_stream_workspace,
    query_fact_events,
)
from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.kernel.transaction_factory import create_transaction_kernel
from polaris.cells.roles.kernel.internal.llm_caller import (
    helpers as llm_caller_helpers_module,
    request_preparer as request_preparer_module,
)
from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
    build_final_provider_request_snapshot,
    build_final_request_context_audit_for_request,
)
from polaris.cells.roles.kernel.internal.llm_caller.factory_dispatch_propagation import (
    FactorySemanticDispatchPropagationPort,
)
from polaris.cells.roles.kernel.internal.llm_caller.factory_role_evidence_binding import (
    FactoryRoleEvidenceBindingV1,
    FactoryRoleEvidenceSourceHeadV1,
    bind_factory_role_evidence,
    get_factory_role_evidence_binding,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_qualification import (
    FINAL_PROVIDER_ATTEMPT_QUALIFICATION_REJECTION_SCHEMA,
    FinalProviderAttemptQualificationError,
    FinalProviderAttemptQualificationRejectionV1,
    append_qualification_rejection,
    bind_final_request_context_audit_to_frozen,
    final_request_snapshot_evidence,
    qualification_rejection_stream,
    qualify_final_provider_request,
    validate_exact_wire_before_reservation,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_request_metrics import canonical_message_chars
from polaris.cells.roles.kernel.internal.llm_caller.invoker import _invoke_executor_with_factory_dispatch
from polaris.cells.roles.kernel.internal.llm_caller.request_facts import project_role_request_facts
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import pin_write_tool_file_param_to_targets
from polaris.cells.roles.kernel.internal.structured_output_transport import (
    STRUCTURED_OUTPUT_TOOL_NAME,
    resolve_structured_output_transport,
)
from polaris.cells.roles.kernel.public import final_request_evidence_cutoff as cutoff_contract
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
    FactoryRoleEvidenceAuthorityBindingV1,
    FactoryRoleEvidenceCutoffAckV1,
    FactoryRoleEvidenceCutoffProofV1,
    FactoryRoleEvidenceCutoffRequestV1,
    bind_factory_role_evidence_authority,
    get_factory_role_evidence_authority_binding,
)
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
    FactoryPhysicalAttemptGrantViewV1,
)
from polaris.cells.roles.kernel.public.structured_output_contracts import (
    STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY,
    RoleStructuredOutputContractV1,
)
from polaris.cells.roles.kernel.tests._physical_attempt_control_test_double import (
    FactoryPhysicalAttemptTestControlPort as FactoryPhysicalAttemptLiveControlPort,
)
from polaris.kernelone.context.context_os.decision_log import build_context_result_id
from polaris.kernelone.context.contracts import TurnEngineContextResult
from polaris.kernelone.events.final_request_evidence import (
    RoleFinalRequestEvidenceAnchorV1,
    RoleFinalRequestEvidenceSlotV1,
    RoleFinalRequestPolicyFactsV1,
    canonical_role_final_request_hash,
    final_request_evidence_ref_for_requirement,
    render_role_final_request_policy_facts,
    role_final_request_policy,
)
from polaris.kernelone.llm.engine.context_store_retention import ContextSnapshotAuditPinRepository
from polaris.kernelone.llm.engine.contracts import AIRequest
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.engine.provider_native_request import project_factory_provider_native_request
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry


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


def _profile(role: str = "chief_engineer") -> SimpleNamespace:
    return SimpleNamespace(
        role_id=role,
        provider_id="provider-a",
        provider_type="openai_compat",
        model="kimi-for-coding",
        max_context_tokens=262_144,
        tool_policy=SimpleNamespace(whitelist=()),
    )


def _factory_binding(*, suffix: str = "1") -> FactoryRoleEvidenceBindingV1:
    role = "chief_engineer"
    hash_char = "a" if suffix == "1" else "f"
    slots = tuple(
        RoleFinalRequestEvidenceSlotV1.create(
            ref_kind=ref_kind,
            state="present" if ref_kind != "workspace_quality" else "absent_at_request_time",
            canonical_source_ref=f"factory/sources/{ref_kind}",
            source_fact_schema="polaris.test_fact.v1",
            source_fact_version="1",
            factory_run_id=f"factory-run-{suffix}",
            run_id=f"run-{suffix}",
            role=role,
            request_freeze_id=f"freeze-{suffix}",
            cutoff_fact_id=f"cutoff-{suffix}",
            cutoff_fact_sequence=7,
            cutoff_fact_hash=hash_char * 64,
            source_head_sequence=4,
            source_head_hash=hash_char * 64,
            execution_authority_hash=hash_char * 64,
            items=(
                RoleFinalRequestEvidenceAnchorV1.create(
                    ref_kind=ref_kind,
                    canonical_source_ref=f"factory/sources/{ref_kind}",
                    canonical_ref=f"runtime/facts/{ref_kind}/item-{suffix}.json",
                    canonical_hash=hash_char * 64,
                    source_fact_schema="polaris.test_fact.v1",
                    source_fact_version="1",
                    factory_run_id=f"factory-run-{suffix}",
                    run_id=f"run-{suffix}",
                    role=role,
                    request_freeze_id=f"freeze-{suffix}",
                    cutoff_fact_id=f"cutoff-{suffix}",
                    cutoff_fact_sequence=7,
                    cutoff_fact_hash=hash_char * 64,
                    source_fact_id=f"fact-{ref_kind}-{suffix}",
                    source_fact_sequence=3,
                    source_fact_hash=hash_char * 64,
                    source_head_sequence=4,
                    source_head_hash=hash_char * 64,
                    execution_authority_hash=hash_char * 64,
                ),
            )
            if ref_kind != "workspace_quality"
            else (),
        )
        for ref_kind in ("pm_contract", "target_files", "workspace_quality")
    )
    facts = RoleFinalRequestPolicyFactsV1.create(role=role, slots=slots)
    source_head_vector = tuple(
        FactoryRoleEvidenceSourceHeadV1(
            canonical_source_ref=slot.canonical_source_ref,
            source_fact_schema="polaris.test_fact.v1",
            source_fact_version="1",
            source_head_fact_id=f"head-{slot.ref_kind}-{suffix}",
            source_head_sequence=slot.source_head_sequence,
            source_head_hash=slot.source_head_hash,
        )
        for slot in slots
    )
    factory_run_id = f"factory-run-{suffix}"
    ack = FactoryRoleEvidenceCutoffAckV1(
        schema_version="polaris.factory_role_evidence_cutoff_ack.v1",
        factory_run_id=factory_run_id,
        run_id=f"run-{suffix}",
        role=role,
        turn_id=f"turn-{suffix}",
        call_id=f"call-{suffix}",
        request_freeze_id=f"freeze-{suffix}",
        semantic_candidate_hash=hash_char * 64,
        attempt_budget=3,
        execution_authority_hash=hash_char * 64,
        authority_stream=(
            "factory.role_evidence_authority." + hashlib.sha256(factory_run_id.encode("utf-8")).hexdigest()
        ),
        cutoff_fact_id=f"cutoff-{suffix}",
        cutoff_fact_sequence=7,
        cutoff_fact_hash=hash_char * 64,
        cutoff_body_hash="b" * 64,
        cutoff_fragment_vector_hash="c" * 64,
        cutoff_fragment_count=2,
    )
    proof = FactoryRoleEvidenceCutoffProofV1.create(
        ack=ack,
        source_head_vector=source_head_vector,
        policy_facts=facts,
    )
    return FactoryRoleEvidenceBindingV1.from_cutoff_proof(proof)


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("canonical_source_ref", 7),
        ("source_fact_schema", 7),
        ("source_head_fact_id", 7),
        ("source_head_hash", int("1" * 64)),
    ],
)
def test_factory_source_head_rejects_non_string_authority_fields(
    field_name: str,
    invalid_value: int,
) -> None:
    source_head = _factory_binding().source_head_vector[0]
    with pytest.raises(TypeError, match=f"{field_name}_type_invalid"):
        replace(source_head, **{field_name: invalid_value})


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("schema_version", 7),
        ("factory_run_id", 7),
        ("role", 7),
        ("cutoff_fact_id", 7),
        ("signed_factory_binding_hash", int("1" * 64)),
        ("cutoff_fact_hash", int("1" * 64)),
        ("source_head_vector_hash", int("1" * 64)),
    ],
)
def test_factory_binding_rejects_non_string_authority_fields(
    field_name: str,
    invalid_value: int,
) -> None:
    malformed = replace(_factory_binding(), **{field_name: invalid_value})
    assert malformed.validation_error(expected_role="chief_engineer") == f"{field_name}_type_invalid"


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
async def test_stream_transaction_kernel_consumes_result_tool_before_tool_lifecycle() -> None:
    payload = {
        "construction_plan": {},
        "scope_for_apply": [],
        "risk_flags": [],
    }
    contract = RoleStructuredOutputContractV1(
        schema_name="chief_engineer_blueprint_portfolio",
        description="Submit the complete blueprint portfolio.",
        json_schema={
            "type": "object",
            "properties": {
                "construction_plan": {"type": "object"},
                "scope_for_apply": {"type": "array"},
                "risk_flags": {"type": "array"},
            },
            "required": ["construction_plan", "scope_for_apply", "risk_flags"],
            "additionalProperties": False,
        },
    )

    async def _fake_call_stream(**_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "chunk", "content": "Submitting result."}
        yield {
            "type": "tool_call",
            "tool": "submit_structured_role_output",
            "args": payload,
            "call_id": "call-ce-result",
        }
        yield {"type": "complete", "metadata": {"provider_id": "deepseek"}}

    request = SimpleNamespace(
        message="Review the PM portfolio.",
        task_id="CE-PORTFOLIO-run-structured",
        run_id="run-structured",
        workspace=".",
        metadata={},
        context_override={
            STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection(),
        },
    )
    kernel = RoleExecutionKernel.create_default(
        workspace=".",
        llm_invoker=SimpleNamespace(call_stream=_fake_call_stream),
    )
    transaction_kernel = create_transaction_kernel(kernel, "chief_engineer", _profile(), request)
    assert transaction_kernel.llm_provider_stream is not None

    events = [
        event
        async for event in transaction_kernel.llm_provider_stream(
            {
                "messages": [
                    {"role": "system", "content": "You are Chief Engineer."},
                    {"role": "user", "content": request.message},
                ],
            }
        )
    ]

    assert [event["type"] for event in events] == ["chunk", "complete"]
    assert json.loads(events[0]["content"]) == payload
    evidence = events[1]["metadata"]["structured_output_transport"]
    assert evidence["tool_lifecycle"] is False
    assert evidence["side_effect"] is False


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.asyncio
async def test_transaction_kernel_wires_exact_structured_result_request_without_mutation_prompt(
    stream: bool,
) -> None:
    """Exercise the real TransactionKernel caller bridge in both transport modes."""

    payload = {
        "construction_plan": {},
        "scope_for_apply": [],
        "risk_flags": [],
    }
    contract = RoleStructuredOutputContractV1(
        schema_name="chief_engineer_blueprint_portfolio",
        description="Submit the complete blueprint portfolio.",
        json_schema={
            "type": "object",
            "properties": {
                "construction_plan": {"type": "object"},
                "scope_for_apply": {"type": "array"},
                "risk_flags": {"type": "array"},
            },
            "required": ["construction_plan", "scope_for_apply", "risk_flags"],
            "additionalProperties": False,
        },
    )
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )
    assert plan is not None
    captured_contexts: list[Any] = []

    async def _fake_call_decision(*, context: Any, **_kwargs: Any) -> dict[str, Any]:
        captured_contexts.append(context)
        native_call = {
            "id": "call-ce-result",
            "type": "function",
            "function": {
                "name": STRUCTURED_OUTPUT_TOOL_NAME,
                "arguments": json.dumps(payload),
            },
        }
        return {
            "content": "",
            "thinking": "",
            "tool_calls": [native_call],
            "native_tool_calls": [native_call],
            "model": "test-model",
            "usage": {},
        }

    async def _fake_call_stream(*, context: Any, **_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        captured_contexts.append(context)
        yield {
            "type": "tool_call",
            "tool": STRUCTURED_OUTPUT_TOOL_NAME,
            "args": payload,
            "call_id": "call-ce-result",
        }
        yield {"type": "complete", "metadata": {"provider_id": "test-provider"}}

    request = SimpleNamespace(
        message="Review the PM portfolio.",
        task_id="",
        run_id="",
        workspace=".",
        metadata={},
        context_override={
            STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection(),
        },
    )
    kernel = RoleExecutionKernel.create_default(
        workspace=".",
        llm_invoker=SimpleNamespace(
            call_decision=_fake_call_decision,
            call_stream=_fake_call_stream,
        ),
    )
    transaction_kernel = create_transaction_kernel(kernel, "chief_engineer", _profile(), request)
    context = [
        {"role": "system", "content": "You are the Chief Engineer."},
        {
            "role": "user",
            "content": (
                "Return the complete portfolio for src/main.rs and tests. The downstream Director owns implementation."
            ),
        },
    ]

    if stream:
        _ = [
            event
            async for event in transaction_kernel.execute_stream(
                "turn-structured-wiring-stream",
                context,
                [plan.tool_definition],
                tool_choice_override=plan.tool_choice,
            )
        ]
    else:
        await transaction_kernel.execute(
            "turn-structured-wiring-nonstream",
            context,
            [plan.tool_definition],
            tool_choice_override=plan.tool_choice,
        )

    assert len(captured_contexts) == 1
    override = captured_contexts[0].context_override
    assert override[request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY] == [plan.tool_definition]
    assert override[request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY] == plan.tool_choice
    prebuilt = override[request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY]
    rendered = "\n".join(str(message.get("content") or "") for message in prebuilt)
    assert "SYSTEM CONSTRAINT (Structured Result)" in rendered
    assert "Call submit_structured_role_output exactly once" in rendered
    assert "SYSTEM CONSTRAINT (Execution)" not in rendered
    assert "TASK CONTRACT (single-batch planning)" not in rendered
    assert "This request requires mutation" not in rendered
    assert "POSITIVE TOOL SEQUENCE TEMPLATES" not in rendered


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
    assert prepared.messages[0]["role"] == "system"
    assert prepared.messages[0]["content"].count("polaris.role_identity.v1:chief_engineer") == 1
    assert prepared.context_result.token_estimate == len(prepared.input_text) // 4
    assert prepared.ai_request.context["chat_messages"] == prepared.messages


@pytest.mark.asyncio
async def test_request_preparer_rejects_wrong_or_nonfirst_role_identity_marker() -> None:
    preparer = LLMRequestPreparer(workspace=".", formatter=None, model_catalog=None)
    for messages in (
        [
            {"role": "system", "content": "polaris.role_identity.v1:director"},
            {"role": "user", "content": "Review."},
        ],
        [
            {"role": "system", "content": "xpolaris.role_identity.v1:chief_engineer"},
            {"role": "user", "content": "Review."},
        ],
        [
            {"role": "system", "content": "polaris.role_identity.v1:chief_engineer trailing"},
            {"role": "user", "content": "Review."},
        ],
        [
            {"role": "SYSTEM", "content": "polaris.role_identity.v1:chief_engineer"},
            {"role": "user", "content": "Review."},
        ],
        [
            {"role": "system", "content": "You are Chief Engineer."},
            {"role": "system", "content": "polaris.role_identity.v1:chief_engineer"},
            {"role": "user", "content": "Review."},
        ],
    ):
        context = SimpleNamespace(
            message="Review.",
            domain="code",
            context_override={
                request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: messages,
                request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: [],
                request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY: "none",
            },
        )
        with pytest.raises(RuntimeError, match="role_identity_marker_invalid"):
            await preparer._prepare_llm_request(
                profile=_profile(),
                system_prompt="You are Chief Engineer.",
                context=context,
                temperature=0.2,
                max_tokens=4000,
                stream=False,
            )


@pytest.mark.asyncio
async def test_request_preparer_keeps_existing_correct_role_identity_once() -> None:
    marker = "polaris.role_identity.v1:chief_engineer"
    context = SimpleNamespace(
        message="Review.",
        domain="code",
        context_override={
            request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
                {"role": "system", "content": f"You are Chief Engineer.\n\n{marker}"},
                {"role": "user", "content": "Review."},
            ],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: [],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY: "none",
        },
    )
    prepared = await LLMRequestPreparer(workspace=".")._prepare_llm_request(
        profile=_profile(),
        system_prompt="You are Chief Engineer.",
        context=context,
        temperature=0.2,
        max_tokens=4000,
        stream=False,
    )
    assert prepared.messages[0]["content"].count(marker) == 1


@pytest.mark.asyncio
async def test_request_preparer_reclamps_director_timeout_after_context_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_values = iter((100.0, 110.0))
    monkeypatch.setattr(llm_caller_helpers_module.time, "time", lambda: next(now_values))
    context = SimpleNamespace(
        message="Materialize the target.",
        domain="code",
        context_override={
            request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
                {"role": "system", "content": "You are director."},
                {"role": "user", "content": "Materialize the target."},
            ],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: [],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY: "none",
            "factory_director_execution_deadline_epoch_seconds": 120.0,
        },
    )

    prepared = await LLMRequestPreparer(workspace=".")._prepare_llm_request(
        profile=_profile("director"),
        system_prompt="You are director.",
        context=context,
        temperature=0.2,
        max_tokens=4000,
        stream=False,
    )

    assert prepared.request_options["timeout"] == 10
    assert prepared.ai_request.options["timeout"] == 10


@pytest.mark.parametrize("role", ["architect", "pm", "chief_engineer", "director", "qa"])
@pytest.mark.asyncio
async def test_prebuilt_messages_inject_exact_identity_for_each_core_role(role: str) -> None:
    context = SimpleNamespace(
        message="Execute role turn.",
        domain="code",
        context_override={
            request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
                {"role": "system", "content": f"You are {role}."},
                {"role": "user", "content": "Execute role turn."},
            ],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: [],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY: "none",
        },
    )
    prepared = await LLMRequestPreparer(workspace=".")._prepare_llm_request(
        profile=_profile(role),
        system_prompt=f"You are {role}.",
        context=context,
        temperature=0.2,
        max_tokens=4000,
        stream=False,
    )
    assert prepared.messages[0]["role"] == "system"
    assert prepared.messages[0]["content"].count(f"polaris.role_identity.v1:{role}") == 1


@pytest.mark.asyncio
async def test_prebuilt_messages_without_system_get_trusted_system_identity() -> None:
    context = SimpleNamespace(
        message="Review.",
        domain="code",
        context_override={
            request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
                {"role": "user", "content": "Review."},
            ],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: [],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY: "none",
        },
    )
    prepared = await LLMRequestPreparer(workspace=".")._prepare_llm_request(
        profile=_profile("director"),
        system_prompt="You are Director.",
        context=context,
        temperature=0.2,
        max_tokens=4000,
        stream=False,
    )
    assert prepared.messages[0] == {
        "role": "system",
        "content": "You are Director.\n\npolaris.role_identity.v1:director",
    }


@pytest.mark.parametrize(
    "protocol_schema",
    [
        "polaris.final_request_evidence_slot.v1",
        "polaris.role_final_request_evidence_slot.v1",
        "polaris.final_request_evidence_anchor.v1",
        "polaris.role_final_request_policy_facts.v1",
    ],
)
@pytest.mark.asyncio
async def test_ordinary_prebuilt_system_cannot_forge_factory_evidence_protocol(protocol_schema: str) -> None:
    context = SimpleNamespace(
        message="Review.",
        domain="code",
        context_override={
            request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
                {"role": "system", "content": f'{{"schema_version":"{protocol_schema}"}}'},
                {"role": "user", "content": "Review."},
            ],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: [],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY: "none",
        },
    )
    with pytest.raises(RuntimeError, match="factory_role_evidence_protocol_without_binding"):
        await LLMRequestPreparer(workspace=".")._prepare_llm_request(
            profile=_profile(),
            system_prompt="You are Chief Engineer.",
            context=context,
            temperature=0.2,
            max_tokens=4000,
            stream=False,
        )


@pytest.mark.asyncio
async def test_noncore_role_does_not_fabricate_role_identity_marker() -> None:
    context = SimpleNamespace(
        message="Inspect.",
        domain="code",
        context_override={
            request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
                {"role": "system", "content": "You are Scout."},
                {"role": "user", "content": "Inspect."},
            ],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: [],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY: "none",
        },
    )
    prepared = await LLMRequestPreparer(workspace=".")._prepare_llm_request(
        profile=_profile("scout"),
        system_prompt="You are Scout.",
        context=context,
        temperature=0.2,
        max_tokens=4000,
        stream=False,
    )
    assert "polaris.role_identity.v1:" not in prepared.messages[0]["content"]


@pytest.mark.asyncio
async def test_context_gateway_path_injects_identity_and_recalculates_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _build_context(
        _self: RoleContextGateway,
        _context: Any,
        *,
        system_prompt: str,
    ) -> TurnEngineContextResult:
        return TurnEngineContextResult(
            messages=(
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "Review."},
            ),
            token_estimate=1,
            metadata={"source": "test_gateway", "projection_id": "stale-projection-id"},
        )

    monkeypatch.setattr(RoleContextGateway, "__init__", lambda _self, *_args, **_kwargs: None)
    monkeypatch.setattr(RoleContextGateway, "build_context", _build_context)
    context = SimpleNamespace(message="Review.", domain="code", context_override={})
    prepared = await LLMRequestPreparer(workspace=".")._prepare_llm_request(
        profile=_profile(),
        system_prompt="You are Chief Engineer.",
        context=context,
        temperature=0.2,
        max_tokens=4000,
        stream=False,
    )
    marker = "polaris.role_identity.v1:chief_engineer"
    assert prepared.messages[0]["content"].count(marker) == 1
    assert prepared.context_result.messages == tuple(prepared.messages)
    assert prepared.context_result.token_estimate == len(prepared.input_text) // 4
    assert prepared.ai_request.input == prepared.input_text
    assert prepared.ai_request.context["chat_messages"] == prepared.messages
    final_digest = prepared.context_os_audit["prompt_digest"]
    assert prepared.ai_request.context["context_projection_id"] == final_digest
    assert prepared.context_result.metadata["projection_id"] == final_digest
    assert prepared.context_result.metadata["source_projection_id"] == "stale-projection-id"
    assert prepared.factory_semantic_request is None
    assert prepared.factory_dispatch_port is None


def _semantic_identity() -> object:
    identity_type = getattr(cutoff_contract, "FactoryRoleSemanticRequestIdentityV1", None)
    assert identity_type is not None, "B3.2 semantic identity contract missing"
    return identity_type(
        run_id="run-1",
        turn_id="run-1:turn:0",
        call_id="a" * 32,
        request_freeze_id="b" * 32,
    )


class _B32CutoffPort:
    def __init__(self) -> None:
        self.acquire_requests: list[FactoryRoleEvidenceCutoffRequestV1] = []
        self.resolve_acks: list[FactoryRoleEvidenceCutoffAckV1] = []

    def reserve(self, command: object) -> object:
        raise AssertionError(command)

    def begin_start(self, command: object) -> object:
        raise AssertionError(command)

    def commit_started(self, command: object) -> object:
        raise AssertionError(command)

    def abort_reservation(self, command: object) -> object:
        raise AssertionError(command)

    def mark_start_ambiguous(self, command: object) -> object:
        raise AssertionError(command)

    def settle(self, command: object) -> object:
        raise AssertionError(command)

    def terminal_persistence_failed(self, command: object) -> object:
        raise AssertionError(command)

    async def acquire_cutoff(
        self,
        request: FactoryRoleEvidenceCutoffRequestV1,
    ) -> FactoryRoleEvidenceCutoffAckV1:
        self.acquire_requests.append(request)
        return FactoryRoleEvidenceCutoffAckV1(
            schema_version=cutoff_contract.FACTORY_ROLE_EVIDENCE_CUTOFF_ACK_SCHEMA,
            factory_run_id="factory-run-1",
            run_id=request.run_id,
            role=request.role,
            turn_id=request.turn_id,
            call_id=request.call_id,
            request_freeze_id=request.request_freeze_id,
            semantic_candidate_hash=request.semantic_candidate_hash,
            attempt_budget=request.attempt_budget,
            execution_authority_hash=request.execution_authority_hash,
            authority_stream=(
                "factory.role_evidence_authority." + __import__("hashlib").sha256(b"factory-run-1").hexdigest()
            ),
            cutoff_fact_id="cutoff-1",
            cutoff_fact_sequence=7,
            cutoff_fact_hash="b" * 64,
            cutoff_body_hash="c" * 64,
            cutoff_fragment_vector_hash="d" * 64,
            cutoff_fragment_count=2,
        )

    async def resolve_cutoff_proof(self, ack: FactoryRoleEvidenceCutoffAckV1) -> object:
        self.resolve_acks.append(ack)
        source_head_type = getattr(cutoff_contract, "FactoryRoleEvidenceCutoffSourceHeadV1", None)
        proof_type = getattr(cutoff_contract, "FactoryRoleEvidenceCutoffProofV1", None)
        assert source_head_type is not None, "B3.2 source-head contract missing"
        assert proof_type is not None, "B3.2 proof contract missing"
        slots: list[RoleFinalRequestEvidenceSlotV1] = []
        heads_list: list[object] = []
        policy = role_final_request_policy(ack.role)
        for index, ref_kind in enumerate(policy.slot_order, start=1):
            present = ref_kind in policy.required_present_slots
            head_hash = canonical_role_final_request_hash([ack.role, ref_kind, index])
            head_sequence = index if present else 0
            canonical_source_ref = f"factory/sources/{ref_kind}"
            items = (
                (
                    RoleFinalRequestEvidenceAnchorV1.create(
                        ref_kind=ref_kind,
                        canonical_source_ref=canonical_source_ref,
                        canonical_ref=f"runtime/facts/{ref_kind}/item.json",
                        canonical_hash=head_hash,
                        source_fact_schema="polaris.test_fact.v1",
                        source_fact_version="1",
                        factory_run_id=ack.factory_run_id,
                        run_id=ack.run_id,
                        role=ack.role,
                        request_freeze_id=ack.request_freeze_id,
                        cutoff_fact_id=ack.cutoff_fact_id,
                        cutoff_fact_sequence=ack.cutoff_fact_sequence,
                        cutoff_fact_hash=ack.cutoff_fact_hash,
                        source_fact_id=f"fact-{ref_kind}",
                        source_fact_sequence=index,
                        source_fact_hash=head_hash,
                        source_head_sequence=head_sequence,
                        source_head_hash=head_hash,
                        execution_authority_hash=ack.execution_authority_hash,
                    ),
                )
                if present
                else ()
            )
            slots.append(
                RoleFinalRequestEvidenceSlotV1.create(
                    ref_kind=ref_kind,
                    state="present" if present else "absent_at_request_time",
                    canonical_source_ref=canonical_source_ref,
                    source_fact_schema="polaris.test_fact.v1",
                    source_fact_version="1",
                    factory_run_id=ack.factory_run_id,
                    run_id=ack.run_id,
                    role=ack.role,
                    request_freeze_id=ack.request_freeze_id,
                    cutoff_fact_id=ack.cutoff_fact_id,
                    cutoff_fact_sequence=ack.cutoff_fact_sequence,
                    cutoff_fact_hash=ack.cutoff_fact_hash,
                    source_head_sequence=head_sequence,
                    source_head_hash=head_hash,
                    execution_authority_hash=ack.execution_authority_hash,
                    items=items,
                )
            )
            heads_list.append(
                source_head_type(
                    canonical_source_ref=canonical_source_ref,
                    source_fact_schema="polaris.test_fact.v1",
                    source_fact_version="1",
                    source_head_fact_id=f"head-{ref_kind}" if head_sequence else "",
                    source_head_sequence=head_sequence,
                    source_head_hash=head_hash,
                )
            )
        facts = RoleFinalRequestPolicyFactsV1.create(role=ack.role, slots=slots)
        heads = tuple(heads_list)
        return proof_type.create(ack=ack, source_head_vector=heads, policy_facts=facts)


class _MalformedB32CutoffPort(_B32CutoffPort):
    async def resolve_cutoff_proof(self, ack: FactoryRoleEvidenceCutoffAckV1) -> object:
        self.resolve_acks.append(ack)
        return {"forged": "mapping"}


class _CancelledB32CutoffPort(_B32CutoffPort):
    async def acquire_cutoff(
        self,
        request: FactoryRoleEvidenceCutoffRequestV1,
    ) -> FactoryRoleEvidenceCutoffAckV1:
        self.acquire_requests.append(request)
        raise asyncio.CancelledError


def _b32_authority(
    port: _B32CutoffPort,
    *,
    role: str = "chief_engineer",
    physical_attempt_control_port: object | None = None,
    execution_authority_hash: str = "a" * 64,
    attempt_budget: int = 3,
) -> FactoryRoleEvidenceAuthorityBindingV1:
    return FactoryRoleEvidenceAuthorityBindingV1(
        schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
        verification_scope="factory",
        factory_run_id="factory-run-1",
        role=role,
        cutoff_port=port,
        physical_attempt_control_port=physical_attempt_control_port or port,
        attempt_budget=attempt_budget,
        execution_authority_hash=execution_authority_hash,
    )


async def _prepare_b33_factory_request(
    role: str,
    *,
    workspace: str = ".",
    physical_attempt_control_port: object | None = None,
    execution_authority_hash: str = "a" * 64,
    attempt_budget: int = 3,
    tool_definitions: list[dict[str, Any]] | None = None,
    tool_choice: object = "none",
    required_tools: list[str] | None = None,
    temperature: float = 0.2,
    max_tokens: int = 4000,
    stream: bool = False,
    structured_output_contract: RoleStructuredOutputContractV1 | None = None,
) -> tuple[_B32CutoffPort, FactoryRoleEvidenceAuthorityBindingV1, SimpleNamespace, Any]:
    port = _B32CutoffPort()
    authority = _b32_authority(
        port,
        role=role,
        physical_attempt_control_port=physical_attempt_control_port,
        execution_authority_hash=execution_authority_hash,
        attempt_budget=attempt_budget,
    )
    context_override: dict[str, Any] = {
        request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
            {"role": "system", "content": f"You are {role}."},
            {"role": "user", "content": "Review."},
        ],
        request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: list(tool_definitions or []),
        request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY: tool_choice,
        "capability_profile_ref": {"sha256": "f" * 64},
        "required_tools": list(required_tools or []),
    }
    if structured_output_contract is not None:
        context_override[STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY] = structured_output_contract.to_context_projection()
    context = SimpleNamespace(
        message="Review.",
        domain="code",
        context_override=context_override,
    )
    with bind_factory_role_evidence_authority(authority):
        prepared = await LLMRequestPreparer(workspace=workspace)._prepare_llm_request(
            profile=_profile(role),
            system_prompt=f"You are {role}.",
            context=context,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
            factory_semantic_identity=_semantic_identity(),
        )
    return port, authority, context, prepared


def _b35_structured_output_contract() -> RoleStructuredOutputContractV1:
    return RoleStructuredOutputContractV1(
        schema_name="chief_engineer_blueprint_portfolio",
        description="Submit the complete Chief Engineer blueprint portfolio.",
        json_schema={
            "type": "object",
            "properties": {
                "construction_plan": {"type": "object"},
                "scope_for_apply": {"type": "array"},
                "risk_flags": {"type": "array"},
            },
            "required": ["construction_plan", "scope_for_apply", "risk_flags"],
            "additionalProperties": False,
        },
    )


@pytest.mark.parametrize("role", ["architect", "pm", "chief_engineer", "director", "qa"])
@pytest.mark.asyncio
async def test_factory_binding_injects_one_exact_evidence_block_and_refreezes_all_projections(
    role: str,
) -> None:
    port, authority, context, prepared = await _prepare_b33_factory_request(role)

    assert len(port.acquire_requests) == 1
    assert len(port.resolve_acks) == 1
    assert port.acquire_requests[0].candidate_refs == ()
    proof = await port.resolve_cutoff_proof(port.resolve_acks[0])
    policy_line = render_role_final_request_policy_facts(proof.policy_facts)
    expected_system = (
        f"You are {role}.\n\n"
        f"polaris.role_identity.v1:{role}\n\n"
        "polaris.final_request_evidence.v1:begin\n"
        f"{policy_line}\n"
        "polaris.final_request_evidence.v1:end"
    )
    assert prepared.messages[0] == {"role": "system", "content": expected_system}
    assert prepared.input_text.count("polaris.final_request_evidence.v1:begin") == 1
    assert prepared.context_result.messages == tuple(prepared.messages)
    assert prepared.context_result.token_estimate == len(prepared.input_text) // 4
    assert prepared.ai_request.input == prepared.input_text
    assert prepared.ai_request.context["chat_messages"] == prepared.messages
    prompt_digest = prepared.context_os_audit["prompt_digest"]
    assert prepared.ai_request.context["context_projection_id"] == prompt_digest
    assert prepared.ai_request.context["context_result_id"] == build_context_result_id(prompt_digest)
    assert prepared.context_result.metadata["projection_id"] == prompt_digest
    assert prepared.context_result.metadata["context_result_id"] == build_context_result_id(prompt_digest)
    assert prepared.context_os_audit["ok"] is True
    assert prepared.context_os_audit["control_plane"] == {
        "isolated": True,
        "metadata_key_hits": [],
        "content_hits": [],
    }
    assert prepared.factory_semantic_request is not None
    assert type(prepared.factory_dispatch_port) is FactorySemanticDispatchPropagationPort
    assert prepared.factory_dispatch_port.frozen_semantic_request is prepared.factory_semantic_request
    prepared.factory_dispatch_port.validate_frozen_identity(prepared.factory_semantic_request)
    send_count = 0
    open_stream_count = 0

    def _send(_wire_request: object) -> object:
        nonlocal send_count
        send_count += 1
        return object()

    async def _send_async(_wire_request: object) -> object:
        nonlocal send_count
        send_count += 1
        return object()

    def _open_stream(_wire_request: object) -> object:
        nonlocal open_stream_count
        open_stream_count += 1
        return object()

    async def _consume(_response: object):
        yield object()

    disabled = "factory_role_semantic_request_frozen_physical_dispatch_not_enabled"
    with pytest.raises(RuntimeError, match=disabled):
        prepared.factory_dispatch_port.dispatch_sync(wire_request={}, send=_send)
    with pytest.raises(RuntimeError, match=disabled):
        await prepared.factory_dispatch_port.dispatch_async(wire_request={}, send=_send_async)
    with pytest.raises(RuntimeError, match=disabled):
        await prepared.factory_dispatch_port.dispatch_blocking_async(wire_request={}, send=_send)
    with pytest.raises(RuntimeError, match=disabled):
        async for _ in prepared.factory_dispatch_port.dispatch_stream_async(
            wire_request={},
            open_stream=_open_stream,
            consume=_consume,
        ):
            pass
    assert send_count == 0
    assert open_stream_count == 0

    assert "factory_run_id" not in repr(prepared.factory_dispatch_port)
    assert authority.execution_authority_hash not in repr(prepared.factory_dispatch_port)
    assert "FactorySemanticDispatchPropagationPort" not in repr(prepared)
    assert "FactorySemanticDispatchPropagationPort" not in json.dumps(prepared, default=str)
    with pytest.raises(TypeError, match="factory_dispatch_port_serialization_forbidden"):
        asdict(prepared)
    assert cutoff_contract.contains_factory_role_evidence_runtime_authority(prepared.factory_dispatch_port) is True

    with pytest.raises(RuntimeError, match="factory_role_semantic_request_dispatch_port_required"):
        replace(prepared, factory_dispatch_port=None)
    with pytest.raises(RuntimeError, match="factory_role_semantic_request_required_for_dispatch_port"):
        replace(prepared, factory_semantic_request=None)
    with pytest.raises(TypeError, match="factory_role_semantic_dispatch_port_exact_type_required"):
        replace(prepared, factory_dispatch_port=object())
    detached_equal_freeze = replace(prepared.factory_semantic_request)
    with pytest.raises(RuntimeError, match="factory_dispatch_frozen_request_identity_mismatch"):
        replace(prepared, factory_semantic_request=detached_equal_freeze)
    frozen_payload = prepared.factory_semantic_request.canonical_final_payload_json
    assert json.loads(frozen_payload)["messages"] == prepared.messages
    assert json.loads(frozen_payload)["capability_profile_id"] != "f" * 64
    assert all(
        not cutoff_contract.contains_factory_role_evidence_runtime_authority(surface)
        for surface in (
            prepared.messages,
            prepared.request_options,
            prepared.context_result.metadata,
            prepared.ai_request.context,
            prepared.ai_request.options,
            prepared.context_os_audit,
            prepared.factory_semantic_request,
        )
    )

    second_port = _B32CutoffPort()
    second_context = SimpleNamespace(
        message=context.message,
        domain=context.domain,
        context_override={
            **context.context_override,
            "capability_profile_ref": {"sha256": "e" * 64},
        },
    )
    with bind_factory_role_evidence_authority(_b32_authority(second_port, role=role)):
        second = await LLMRequestPreparer(workspace=".")._prepare_llm_request(
            profile=_profile(role),
            system_prompt=f"You are {role}.",
            context=second_context,
            temperature=0.2,
            max_tokens=4000,
            stream=False,
            factory_semantic_identity=_semantic_identity(),
        )
    assert second.factory_semantic_request is not None
    assert (
        json.loads(second.factory_semantic_request.canonical_final_payload_json)["capability_profile_id"]
        == json.loads(frozen_payload)["capability_profile_id"]
    )
    assert (
        second.factory_semantic_request.semantic_candidate_hash
        == prepared.factory_semantic_request.semantic_candidate_hash
    )


@pytest.mark.asyncio
async def test_factory_dispatch_rejects_prompt_evidence_not_equal_to_typed_binding() -> None:
    _, authority, _, prepared = await _prepare_b33_factory_request("chief_engineer")
    frozen = prepared.factory_semantic_request
    dispatch_port = prepared.factory_dispatch_port
    assert frozen is not None and dispatch_port is not None
    payload = json.loads(frozen.canonical_final_payload_json)
    first_content = payload["messages"][0]["content"]
    separator = "\n\npolaris.final_request_evidence.v1:begin\n"
    candidate_system, evidence_tail = first_content.split(separator, 1)
    policy_json, end_marker = evidence_tail.rsplit("\n", 1)
    prompt_projection = json.loads(policy_json)
    prompt_projection["slots"][0]["items"][0]["canonical_ref"] = "factory/evidence/forged-but-typed"
    rendered = json.dumps(prompt_projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["messages"][0]["content"] = f"{candidate_system}{separator}{rendered}\n{end_marker}"
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    tampered = replace(
        frozen,
        canonical_final_payload_json=canonical,
        final_semantic_request_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )
    type(tampered).__post_init__(tampered)

    with pytest.raises(RuntimeError, match="factory_dispatch_live_pairing_drift"):
        FactorySemanticDispatchPropagationPort(
            authority=authority,
            binding=dispatch_port._binding,
            frozen=tampered,
            workspace=dispatch_port.workspace,
        )


@pytest.mark.parametrize(
    ("surface", "field_name", "replacement"),
    [
        ("authority", "attempt_budget", 7),
        ("authority", "role", "qa"),
        ("binding", "run_id", "different-controlled-run"),
        ("binding", "signed_factory_binding_hash", "b" * 64),
        ("frozen", "final_semantic_request_hash", "c" * 64),
    ],
)
@pytest.mark.asyncio
async def test_b33_live_authority_binding_and_frozen_hash_drift_fail_closed(
    surface: str,
    field_name: str,
    replacement: object,
) -> None:
    _port, authority, _context, prepared = await _prepare_b33_factory_request("director")
    dispatch_port = prepared.factory_dispatch_port
    assert type(dispatch_port) is FactorySemanticDispatchPropagationPort
    target = {
        "authority": authority,
        "binding": dispatch_port._binding,
        "frozen": prepared.factory_semantic_request,
    }[surface]
    original = getattr(target, field_name)
    object.__setattr__(target, field_name, replacement)
    try:
        with pytest.raises(RuntimeError, match="factory_dispatch_"):
            dispatch_port.validate_frozen_identity(prepared.factory_semantic_request)
    finally:
        object.__setattr__(target, field_name, original)


@pytest.mark.asyncio
async def test_b33_semantic_retry_reacquires_cutoff_refreezes_and_mints_new_exact_port() -> None:
    port, authority, _context, prepared = await _prepare_b33_factory_request("director")
    preparer = LLMRequestPreparer(workspace=".")
    profile = _profile("director")
    old_projection_id = str(prepared.context_result.metadata["projection_id"])
    old_context_result_id = str(prepared.context_result.metadata["context_result_id"])
    prepared.ai_request.context.update(
        {
            "context_snapshot_ref": "a" * 24,
            "context_snapshot_degraded": {"code": "old-snapshot"},
            "context_snapshot_degraded_reason": "old-snapshot",
            "contextSnapshotRef": "b" * 24,
            "contextSnapshotDegraded": {"code": "old-camel-snapshot"},
            "contextSnapshotDegradedReason": "old-camel-snapshot",
            "final_provider_attempt_receipt_ref": "receipt://old-attempt",
            "finalProviderAttemptReceiptRef": "receipt://old-camel-attempt",
        }
    )
    retry_request = preparer._build_reasoning_truncation_retry_request(
        prepared=prepared,
        profile=profile,
    )

    with bind_factory_role_evidence_authority(authority):
        retry_prepared = await preparer._reprepare_factory_semantic_retry_request(
            prepared=prepared,
            request=retry_request,
            profile=profile,
        )

    assert len(port.acquire_requests) == 2
    assert len(port.resolve_acks) == 2
    old_frozen = prepared.factory_semantic_request
    new_frozen = retry_prepared.factory_semantic_request
    assert new_frozen is not old_frozen
    assert new_frozen.identity.request_freeze_id != old_frozen.identity.request_freeze_id
    assert (
        new_frozen.identity.run_id,
        new_frozen.identity.turn_id,
        new_frozen.identity.call_id,
    ) == (
        old_frozen.identity.run_id,
        old_frozen.identity.turn_id,
        old_frozen.identity.call_id,
    )
    assert retry_prepared.ai_request is retry_request
    assert retry_prepared.factory_dispatch_port is not prepared.factory_dispatch_port
    assert type(retry_prepared.factory_dispatch_port) is FactorySemanticDispatchPropagationPort
    assert retry_prepared.factory_dispatch_port.frozen_semantic_request is new_frozen
    retry_prepared.factory_dispatch_port.validate_frozen_identity(new_frozen)
    with pytest.raises(RuntimeError, match="factory_dispatch_frozen_request_identity_mismatch"):
        retry_prepared.factory_dispatch_port.validate_frozen_identity(old_frozen)
    fresh_digest = str(retry_prepared.context_os_audit["prompt_digest"])
    fresh_context_result_id = build_context_result_id(fresh_digest)
    assert fresh_digest != old_projection_id
    assert retry_prepared.context_result.metadata["projection_id"] == fresh_digest
    assert retry_prepared.context_result.metadata["context_result_id"] == fresh_context_result_id
    assert retry_prepared.context_result.metadata["source_projection_id"] == old_projection_id
    assert retry_prepared.context_result.metadata["source_context_result_id"] == old_context_result_id
    assert retry_request.context["context_projection_id"] == fresh_digest
    assert retry_request.context["context_result_id"] == fresh_context_result_id
    assert retry_request.context["context_os_audit"]["prompt_digest"] == fresh_digest
    assert "context_snapshot_ref" not in retry_request.context
    assert "context_snapshot_degraded" not in retry_request.context
    assert "context_snapshot_degraded_reason" not in retry_request.context
    assert "contextSnapshotRef" not in retry_request.context
    assert "contextSnapshotDegraded" not in retry_request.context
    assert "contextSnapshotDegradedReason" not in retry_request.context
    assert "final_provider_attempt_receipt_ref" not in retry_request.context
    assert "finalProviderAttemptReceiptRef" not in retry_request.context


@pytest.mark.parametrize(
    ("variant", "option_updates"),
    [
        (
            "tools",
            {
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ]
            },
        ),
        (
            "tool_choice",
            {
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
                "tool_choice": "required",
            },
        ),
        ("response_format", {"response_format": {"type": "json_object"}}),
        ("max_tokens", {"max_tokens": 8192}),
    ],
)
@pytest.mark.asyncio
async def test_b33_real_semantic_retry_refreezes_provider_visible_option_variants(
    variant: str,
    option_updates: dict[str, Any],
) -> None:
    port, authority, _context, prepared = await _prepare_b33_factory_request("director")
    request_options = dict(prepared.ai_request.options)
    request_options.update(option_updates)
    retry_request = AIRequest(
        task_type=prepared.ai_request.task_type,
        role=prepared.ai_request.role,
        input=prepared.ai_request.input,
        options=request_options,
        context=dict(prepared.ai_request.context),
    )

    with bind_factory_role_evidence_authority(authority):
        retry_prepared = await LLMRequestPreparer(workspace=".")._reprepare_factory_semantic_retry_request(
            prepared=prepared,
            request=retry_request,
            profile=_profile("director"),
        )

    assert len(port.acquire_requests) == 2
    assert len(port.resolve_acks) == 2
    assert retry_prepared.factory_semantic_request is not prepared.factory_semantic_request
    assert retry_prepared.factory_dispatch_port is not prepared.factory_dispatch_port
    frozen_payload = json.loads(retry_prepared.factory_semantic_request.canonical_final_payload_json)
    assert frozen_payload[variant] == request_options[variant]


@pytest.mark.asyncio
async def test_factory_semantic_freeze_uses_provider_visible_formatted_tools() -> None:
    class _Formatter:
        @staticmethod
        def format_tools(tools: list[dict[str, Any]], provider_id: str) -> list[dict[str, Any]]:
            assert tools
            return [{"provider": provider_id, "wire_tool": tools[0]}]

    forced_tool = {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    }
    port = _B32CutoffPort()
    context = SimpleNamespace(
        message="Implement.",
        domain="code",
        context_override={
            request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
                {"role": "system", "content": "You are Director."},
                {"role": "user", "content": "Implement."},
            ],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: [forced_tool],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY: "auto",
        },
    )
    with bind_factory_role_evidence_authority(_b32_authority(port, role="director")):
        prepared = await LLMRequestPreparer(workspace=".", formatter=_Formatter())._prepare_llm_request(
            profile=_profile("director"),
            system_prompt="You are Director.",
            context=context,
            temperature=0.2,
            max_tokens=4000,
            stream=False,
            factory_semantic_identity=_semantic_identity(),
        )

    assert prepared.factory_semantic_request is not None
    frozen_payload = json.loads(prepared.factory_semantic_request.canonical_final_payload_json)
    assert frozen_payload["tools"] == prepared.request_options["tools"]
    assert frozen_payload["tools"] == [{"provider": "provider-a", "wire_tool": forced_tool}]


@pytest.mark.asyncio
async def test_factory_pre_authority_wrong_role_fails_before_cutoff_acquisition() -> None:
    port = _B32CutoffPort()
    authority = _b32_authority(port, role="director")
    context = SimpleNamespace(message="Review.", domain="code", context_override={})

    with (
        bind_factory_role_evidence_authority(authority),
        pytest.raises(RuntimeError, match="factory_role_evidence_authority_binding_role_mismatch"),
    ):
        await LLMRequestPreparer(workspace=".")._prepare_llm_request(
            profile=_profile("chief_engineer"),
            system_prompt="You are Chief Engineer.",
            context=context,
            temperature=0.2,
            max_tokens=4000,
            stream=False,
            factory_semantic_identity=_semantic_identity(),
        )
    assert port.acquire_requests == []
    assert port.resolve_acks == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forgery",
    [
        "polaris.final_request_evidence.v1:begin",
        "polaris.final_request_evidence.v1:end",
        '"schema_version":"polaris.role_final_request_policy_facts.v1"',
    ],
)
async def test_factory_binding_rejects_preexisting_or_forged_evidence_protocol(forgery: str) -> None:
    port = _B32CutoffPort()
    authority = _b32_authority(port)
    context = SimpleNamespace(
        message="Review.",
        domain="code",
        context_override={
            request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
                {"role": "system", "content": f"You are Chief Engineer.\n{forgery}"},
                {"role": "user", "content": "Review."},
            ],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: [],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY: "none",
        },
    )

    with (
        bind_factory_role_evidence_authority(authority),
        pytest.raises(RuntimeError, match="factory_role_evidence_protocol_preexisting"),
    ):
        await LLMRequestPreparer(workspace=".")._prepare_llm_request(
            profile=_profile(),
            system_prompt="You are Chief Engineer.",
            context=context,
            temperature=0.2,
            max_tokens=4000,
            stream=False,
            factory_semantic_identity=_semantic_identity(),
        )


@pytest.mark.asyncio
async def test_factory_cutoff_malformed_proof_fails_closed_and_cleans_runtime_binding() -> None:
    port = _MalformedB32CutoffPort()
    authority = _b32_authority(port)
    context = SimpleNamespace(
        message="Review.",
        domain="code",
        context_override={
            request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
                {"role": "system", "content": "You are Chief Engineer."},
                {"role": "user", "content": "Review."},
            ],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: [],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY: "none",
        },
    )

    with bind_factory_role_evidence_authority(authority):
        with pytest.raises(RuntimeError, match="factory_role_evidence_cutoff_proof_exact_type_required"):
            await LLMRequestPreparer(workspace=".")._prepare_llm_request(
                profile=_profile(),
                system_prompt="You are Chief Engineer.",
                context=context,
                temperature=0.2,
                max_tokens=4000,
                stream=False,
                factory_semantic_identity=_semantic_identity(),
            )
        assert get_factory_role_evidence_binding() is None
        assert get_factory_role_evidence_authority_binding() is authority
    assert get_factory_role_evidence_authority_binding() is None
    assert len(port.acquire_requests) == 1
    assert len(port.resolve_acks) == 1


@pytest.mark.asyncio
async def test_factory_cutoff_cancellation_propagates_and_cleans_runtime_binding() -> None:
    port = _CancelledB32CutoffPort()
    authority = _b32_authority(port)
    context = SimpleNamespace(
        message="Review.",
        domain="code",
        context_override={
            request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
                {"role": "system", "content": "You are Chief Engineer."},
                {"role": "user", "content": "Review."},
            ],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: [],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY: "none",
        },
    )

    with bind_factory_role_evidence_authority(authority):
        with pytest.raises(asyncio.CancelledError):
            await LLMRequestPreparer(workspace=".")._prepare_llm_request(
                profile=_profile(),
                system_prompt="You are Chief Engineer.",
                context=context,
                temperature=0.2,
                max_tokens=4000,
                stream=False,
                factory_semantic_identity=_semantic_identity(),
            )
        assert get_factory_role_evidence_binding() is None
    assert get_factory_role_evidence_authority_binding() is None
    assert len(port.acquire_requests) == 1
    assert port.resolve_acks == []


@pytest.mark.asyncio
async def test_factory_role_evidence_binding_fails_before_request_until_cutoff_is_enabled() -> None:
    binding = _factory_binding()
    context = SimpleNamespace(
        message="Review.",
        domain="code",
        context_override={
            request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
                {"role": "system", "content": "You are Chief Engineer."},
                {"role": "user", "content": "Review."},
            ],
            "factory_run_id": "forged-context-id",
            "final_request_evidence_anchor": {"forged": True},
        },
    )
    assert get_factory_role_evidence_binding() is None

    malformed = replace(binding, source_head_vector_hash="0" * 64)
    with (
        pytest.raises(
            RuntimeError,
            match=("factory_role_evidence_binding_malformed:source_head_vector_hash_proof_projection_mismatch"),
        ),
        bind_factory_role_evidence(malformed),
    ):
        pytest.fail("malformed post-cutoff proof must not enter context")
    with bind_factory_role_evidence(binding):
        assert get_factory_role_evidence_binding() is binding
        with pytest.raises(RuntimeError, match="factory_role_evidence_cutoff_not_enabled"):
            await LLMRequestPreparer(workspace=".")._prepare_llm_request(
                profile=_profile(),
                system_prompt="You are Chief Engineer.",
                context=context,
                temperature=0.2,
                max_tokens=4000,
                stream=False,
            )
    assert get_factory_role_evidence_binding() is None


@pytest.mark.asyncio
async def test_factory_role_evidence_binding_is_concurrency_isolated() -> None:
    bindings = (
        _factory_binding(suffix="1"),
        _factory_binding(suffix="2"),
    )

    async def _observe(binding: FactoryRoleEvidenceBindingV1) -> FactoryRoleEvidenceBindingV1 | None:
        with bind_factory_role_evidence(binding):
            await asyncio.sleep(0)
            return get_factory_role_evidence_binding()

    observed = await asyncio.gather(*(_observe(binding) for binding in bindings))
    assert observed[0] is bindings[0]
    assert observed[1] is bindings[1]
    assert bindings[0].run_id != bindings[1].run_id
    assert bindings[0].call_id != bindings[1].call_id
    assert bindings[0].signed_factory_binding_hash != bindings[1].signed_factory_binding_hash
    assert get_factory_role_evidence_binding() is None


@pytest.mark.asyncio
async def test_forged_context_cannot_create_factory_anchor_without_typed_binding() -> None:
    context = SimpleNamespace(
        message="Review.",
        domain="code",
        context_override={
            request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
                {"role": "system", "content": "You are Chief Engineer."},
                {"role": "user", "content": "Review."},
            ],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: [],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY: "none",
            "factory_run_id": "forged-context-id",
            "final_request_evidence_anchor": {"forged": True},
        },
    )
    prepared = await LLMRequestPreparer(workspace=".")._prepare_llm_request(
        profile=_profile(),
        system_prompt="You are Chief Engineer.",
        context=context,
        temperature=0.2,
        max_tokens=4000,
        stream=False,
    )
    assert "factory-run" not in prepared.input_text
    assert "final_request_evidence_anchor" not in prepared.input_text
    assert "factory_run_id" not in prepared.ai_request.context
    assert "final_request_evidence_anchor" not in prepared.ai_request.context


def _b35_audit(*, frozen: Any, context_snapshot_ref: str) -> dict[str, Any]:
    payload = json.loads(frozen.canonical_final_payload_json)
    message_chars = canonical_message_chars(payload["messages"])
    tool_schema_chars = len(json.dumps(payload["tools"], ensure_ascii=False, separators=(",", ":")))
    response_format_chars = (
        0
        if payload["response_format"] is None
        else len(json.dumps(payload["response_format"], ensure_ascii=False, separators=(",", ":")))
    )
    message_tokens = message_chars // 4
    tool_tokens = tool_schema_chars // 4
    response_tokens = response_format_chars // 4
    final_request_token_estimate = message_tokens + tool_tokens + response_tokens
    context_window_tokens = 16_384
    required_refs = [
        final_request_evidence_ref_for_requirement(ref_kind) or ref_kind
        for ref_kind in role_final_request_policy(payload["role"]).required_present_slots
    ]
    available_tools = [tool["function"]["name"] for tool in payload["tools"]]
    audit = {
        "schema_version": "llm.final_request_context_audit.v1",
        "message_count": len(payload["messages"]),
        "message_chars": message_chars,
        "message_token_estimate": message_tokens,
        "tool_schema_count": len(payload["tools"]),
        "tool_schema_chars": tool_schema_chars,
        "tool_schema_token_estimate": tool_tokens,
        "response_format_chars": response_format_chars,
        "response_format_token_estimate": response_tokens,
        "final_request_token_estimate": final_request_token_estimate,
        "context_window_tokens": context_window_tokens,
        "context_window_utilization": round(final_request_token_estimate / context_window_tokens, 4),
        "available_token_headroom": context_window_tokens - final_request_token_estimate,
        "final_request_evidence_coverage": {
            "schema_version": "polaris.final_request_evidence_coverage.v1",
            "context_snapshot_ref": context_snapshot_ref,
            "role_id": payload["role"],
            "expected_role_id": payload["role"],
            "role_identity_ok": True,
            "required_refs": required_refs,
            "included_refs": required_refs,
            "missing_required_refs": [],
            "required_tools": list(payload["required_tools"]),
            "available_tools": available_tools,
            "missing_required_tools": [tool for tool in payload["required_tools"] if tool not in available_tools],
            "tool_schema_registry_coverage": {
                "registry_source": "polaris.kernelone.tool_execution.ToolSpecRegistry",
                "aliases_present": True,
                "arg_aliases_present": True,
                "schema_hash": (
                    hashlib.sha256(
                        json.dumps(
                            payload["tools"],
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()[:24]
                    if payload["tools"]
                    else ""
                ),
                "missing_schema_tools": [],
            },
            "pass": True,
        },
    }
    return bind_final_request_context_audit_to_frozen(audit=audit, frozen=frozen)


@pytest.mark.parametrize("role", ["architect", "pm", "chief_engineer", "director", "qa"])
@pytest.mark.asyncio
async def test_factory_audit_replaces_observed_refs_with_authoritative_role_slots(
    tmp_path: Path,
    role: str,
) -> None:
    """Heuristic context signals must not impersonate Factory evidence slots."""

    _, _, _, prepared = await _prepare_b33_factory_request(role, workspace=str(tmp_path))
    frozen = prepared.factory_semantic_request
    dispatch_port = prepared.factory_dispatch_port
    assert frozen is not None
    assert dispatch_port is not None

    observed = build_final_request_context_audit_for_request(
        ai_request=prepared.ai_request,
        prepared=prepared,
        profile=_profile(role),
    )
    observed_coverage = observed["final_request_evidence_coverage"]
    assert "final_provider_request" in observed_coverage["included_refs"]

    bound = dispatch_port.bind_final_request_context_audit(observed)
    coverage = bound["final_request_evidence_coverage"]
    expected_required = [
        final_request_evidence_ref_for_requirement(ref_kind) or ref_kind
        for ref_kind in role_final_request_policy(role).required_present_slots
    ]
    expected_present = [
        final_request_evidence_ref_for_requirement(slot.ref_kind) or slot.ref_kind
        for slot in dispatch_port._binding.policy_facts.slots
        if slot.state == "present"
    ]
    assert coverage["required_refs"] == expected_required
    assert coverage["included_refs"] == expected_present
    assert coverage["missing_required_refs"] == []
    assert coverage["coverage_ratio"] == 1.0
    assert coverage["pass"] is True
    assert "final_provider_request" in coverage["observed_included_refs"]
    assert [source["ref_type"] for source in coverage["coverage_sources"]] == [
        final_request_evidence_ref_for_requirement(slot.ref_kind) or slot.ref_kind
        for slot in dispatch_port._binding.policy_facts.slots
    ]
    assert all(source["source"] == "factory_role_evidence_cutoff" for source in coverage["coverage_sources"])
    assert [slot["present"] for slot in coverage["evidence_slots"]] == [
        slot.state == "present" for slot in dispatch_port._binding.policy_facts.slots
    ]
    quality = bound["context_quality"]
    assert quality["final_request_evidence_coverage_pass"] is True
    assert quality["missing_required_refs"] == []
    assert all(finding.get("code") != "missing_required_final_request_evidence" for finding in quality["findings"])

    payload = json.loads(frozen.canonical_final_payload_json)
    provider_request = {
        "schema_version": "llm.provider_request_snapshot.v1",
        "role": payload["role"],
        "provider_id": payload["provider_id"],
        "model": payload["model"],
        "factory_final_request": final_request_snapshot_evidence(frozen),
        "final_request_context_audit": bound,
    }
    context_snapshot_ref = AIExecutor._store_context_messages_sync(
        str(tmp_path),
        payload["messages"],
        frozen.identity.run_id,
        frozen.identity.call_id,
        provider_request,
    )
    coverage["context_snapshot_ref"] = context_snapshot_ref
    qualified = qualify_final_provider_request(
        workspace=str(tmp_path),
        frozen=frozen,
        binding=dispatch_port._binding,
        final_request_context_audit=bound,
        context_snapshot_ref=context_snapshot_ref,
    )
    assert qualified == bound


def test_b35_message_accounting_covers_complete_provider_message_objects() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "",
            "name": "director",
            "tool_call_id": "call-1",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"src/main.py"}'},
                }
            ],
        }
    ]
    expected = len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")))
    assert canonical_message_chars(messages) == expected
    assert canonical_message_chars(messages) > len(messages[0]["content"])


def _b35_provider_request_snapshot(*, frozen: Any) -> dict[str, Any]:
    payload = json.loads(frozen.canonical_final_payload_json)
    snapshot_audit = _b35_audit(frozen=frozen, context_snapshot_ref="")
    return {
        "schema_version": "llm.provider_request_snapshot.v1",
        "role": payload["role"],
        "provider_id": payload["provider_id"],
        "model": payload["model"],
        "factory_final_request": final_request_snapshot_evidence(frozen),
        "final_request_context_audit": snapshot_audit,
    }


def _b35_route_authority(*, frozen: Any) -> dict[str, Any]:
    payload = json.loads(frozen.canonical_final_payload_json)
    mode = "stream" if payload["stream"] else "invoke"
    native = project_factory_provider_native_request(
        provider_type="openai_compat",
        mode=mode,
        final_payload=payload,
        provider_config={"base_url": "https://provider.test/v1"},
    )
    assert native is not None
    return {
        **native.authority(),
        "schema_version": "llm.factory_physical_provider_route.v2",
        "native_request_schema_version": native.schema_version,
        "provider_id": payload["provider_id"],
        "model": payload["model"],
    }


def _b35_wire(*, frozen: Any) -> dict[str, Any]:
    route = _b35_route_authority(frozen=frozen)
    return {
        "endpoint": route["exact_endpoint"],
        "headers": {},
        "body": route["expected_body"],
        "transport": {"kind": route["exact_transport_kind"], "timeout": 60},
    }


def _bind_b35_route(dispatch_port: FactorySemanticDispatchPropagationPort, *, frozen: Any) -> None:
    payload = json.loads(frozen.canonical_final_payload_json)
    dispatch_port.bind_provider_route_authority(
        provider_id=payload["provider_id"],
        provider_type="openai_compat",
        model=payload["model"],
        mode="stream" if payload["stream"] else "invoke",
        provider_config={"base_url": "https://provider.test/v1"},
    )


@pytest.mark.parametrize("role", ["architect", "pm", "chief_engineer", "director", "qa"])
@pytest.mark.asyncio
async def test_b35_exact_snapshot_and_wire_qualify_before_reservation(
    tmp_path: Any,
    role: str,
) -> None:
    _, _, _, prepared = await _prepare_b33_factory_request(role)
    frozen = prepared.factory_semantic_request
    assert frozen is not None
    dispatch_port = prepared.factory_dispatch_port
    assert dispatch_port is not None
    payload = json.loads(frozen.canonical_final_payload_json)
    provider_request = _b35_provider_request_snapshot(frozen=frozen)
    context_snapshot_ref = AIExecutor._store_context_messages_sync(
        str(tmp_path),
        payload["messages"],
        frozen.identity.run_id,
        frozen.identity.call_id,
        provider_request,
    )
    audit = _b35_audit(frozen=frozen, context_snapshot_ref=context_snapshot_ref)
    coverage = audit["final_request_evidence_coverage"]
    policy = role_final_request_policy(role)
    assert coverage["required_refs"] == [
        final_request_evidence_ref_for_requirement(ref_kind) or ref_kind for ref_kind in policy.required_present_slots
    ]
    assert coverage["included_refs"] == [
        final_request_evidence_ref_for_requirement(slot.ref_kind) or slot.ref_kind
        for slot in dispatch_port._binding.policy_facts.slots
        if slot.state == "present"
    ]
    assert coverage["missing_required_refs"] == []
    assert coverage["pass"] is True
    qualified = qualify_final_provider_request(
        workspace=str(tmp_path),
        frozen=frozen,
        binding=dispatch_port._binding,
        final_request_context_audit=audit,
        context_snapshot_ref=context_snapshot_ref,
    )
    assert qualified == audit
    wire = _b35_wire(frozen=frozen)
    route = _b35_route_authority(frozen=frozen)
    validate_exact_wire_before_reservation(
        frozen=frozen,
        wire_request=wire,
        physical_route_authority=route,
    )
    wire["body"]["tool_choice"] = "required"
    with pytest.raises(FinalProviderAttemptQualificationError, match="physical_wire_tool_choice_drift"):
        validate_exact_wire_before_reservation(
            frozen=frozen,
            wire_request=wire,
            physical_route_authority=route,
        )
    wire["body"]["tool_choice"] = payload["tool_choice"]
    wire["body"]["response_format"] = {"type": "json_object"}
    with pytest.raises(FinalProviderAttemptQualificationError, match="physical_wire_response_format_drift"):
        validate_exact_wire_before_reservation(
            frozen=frozen,
            wire_request=wire,
            physical_route_authority=route,
        )


@pytest.mark.asyncio
async def test_b35_required_tools_are_frozen_authority_not_audit_self_report(tmp_path: Path) -> None:
    read_file_schema = ToolSpecRegistry.get_llm_schema(
        "read_file",
        include_arg_aliases=True,
        deterministic=True,
    )
    _, _, _, prepared = await _prepare_b33_factory_request(
        "director",
        tool_definitions=[read_file_schema],
        tool_choice="auto",
        required_tools=["read_file"],
    )
    frozen = prepared.factory_semantic_request
    dispatch_port = prepared.factory_dispatch_port
    assert frozen is not None and dispatch_port is not None
    payload = json.loads(frozen.canonical_final_payload_json)
    assert payload["required_tools"] == ["read_file"]
    context_snapshot_ref = AIExecutor._store_context_messages_sync(
        str(tmp_path),
        payload["messages"],
        frozen.identity.run_id,
        frozen.identity.call_id,
        _b35_provider_request_snapshot(frozen=frozen),
    )
    audit = _b35_audit(frozen=frozen, context_snapshot_ref=context_snapshot_ref)
    audit["final_request_evidence_coverage"]["required_tools"] = []
    audit["final_request_evidence_coverage"]["missing_required_tools"] = []

    with pytest.raises(
        FinalProviderAttemptQualificationError,
        match="final_request_required_tools_authority_drift",
    ):
        qualify_final_provider_request(
            workspace=str(tmp_path),
            frozen=frozen,
            binding=dispatch_port._binding,
            final_request_context_audit=audit,
            context_snapshot_ref=context_snapshot_ref,
        )


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("model", "physical_wire_model_drift"),
        ("endpoint", "physical_wire_endpoint_drift"),
        ("transport", "physical_wire_transport_drift"),
        ("provider", "physical_provider_route_authority_drift"),
        ("mode", "physical_provider_route_authority_drift"),
    ],
)
@pytest.mark.asyncio
async def test_b35_wire_and_route_authority_drift_rejects(
    case: str,
    expected_code: str,
) -> None:
    _, _, _, prepared = await _prepare_b33_factory_request("director")
    frozen = prepared.factory_semantic_request
    assert frozen is not None
    wire = _b35_wire(frozen=frozen)
    route = _b35_route_authority(frozen=frozen)
    if case == "model":
        wire["body"]["model"] = "other-model"
    elif case == "endpoint":
        wire["endpoint"] = "https://other-provider.test/v1/chat/completions"
    elif case == "transport":
        wire["transport"]["kind"] = "urllib.request"
    elif case == "provider":
        route["provider_id"] = "other-provider"
    elif case == "mode":
        route["mode"] = "stream"
    with pytest.raises(FinalProviderAttemptQualificationError, match=expected_code):
        validate_exact_wire_before_reservation(
            frozen=frozen,
            wire_request=wire,
            physical_route_authority=route,
        )


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("messages_only_audit", "final_request_context_audit_schema_invalid"),
        ("token_missing", "final_request_token_estimate_invalid"),
        ("window_missing", "final_request_context_window_invalid"),
        ("clipped", "final_request_context_clipped"),
        ("evidence_slot_missing", "final_request_missing_refs_formula_mismatch"),
        ("required_refs_extra", "final_request_role_required_refs_drift"),
        ("included_refs_extra", "final_request_role_included_refs_drift"),
        ("available_tools_drift", "final_request_available_tools_drift"),
        ("self_reported_pass_drift", "final_request_evidence_coverage_failed"),
        ("wrong_role", "final_request_role_identity_mismatch"),
        ("snapshot_ref_drift", "context_snapshot_ref_audit_mismatch"),
        ("message_count_drift", "final_request_message_count_mismatch"),
        ("tool_count_drift", "final_request_tool_schema_count_mismatch"),
        ("token_sum_drift", "final_request_token_estimate_inconsistent"),
        ("headroom_drift", "final_request_token_headroom_mismatch"),
        ("utilization_drift", "final_request_context_utilization_mismatch"),
    ],
)
@pytest.mark.asyncio
async def test_b35_malformed_final_request_evidence_rejects(
    tmp_path: Any,
    case: str,
    expected_code: str,
) -> None:
    _, _, _, prepared = await _prepare_b33_factory_request("director")
    frozen = prepared.factory_semantic_request
    dispatch_port = prepared.factory_dispatch_port
    assert frozen is not None and dispatch_port is not None
    payload = json.loads(frozen.canonical_final_payload_json)
    context_snapshot_ref = AIExecutor._store_context_messages_sync(
        str(tmp_path),
        payload["messages"],
        frozen.identity.run_id,
        frozen.identity.call_id,
        _b35_provider_request_snapshot(frozen=frozen),
    )
    audit = copy.deepcopy(_b35_audit(frozen=frozen, context_snapshot_ref=context_snapshot_ref))
    coverage = audit["final_request_evidence_coverage"]
    if case == "messages_only_audit":
        audit.pop("schema_version")
    elif case == "token_missing":
        audit["final_request_token_estimate"] = 0
    elif case == "window_missing":
        audit["context_window_tokens"] = 0
    elif case == "clipped":
        audit["context_window_tokens"] = audit["final_request_token_estimate"] - 1
        audit["context_window_utilization"] = 1.1
        audit["available_token_headroom"] = 0
    elif case == "evidence_slot_missing":
        coverage["pass"] = False
        coverage["missing_required_refs"] = ["chief_engineer_blueprint"]
    elif case == "required_refs_extra":
        coverage["required_refs"].append("self_reported_extra")
    elif case == "included_refs_extra":
        coverage["included_refs"].append("self_reported_extra")
    elif case == "available_tools_drift":
        coverage["available_tools"].append("forged_tool")
    elif case == "self_reported_pass_drift":
        coverage["pass"] = False
    elif case == "wrong_role":
        coverage["role_id"] = "pm"
    elif case == "snapshot_ref_drift":
        coverage["context_snapshot_ref"] = "d" * 24
    elif case == "message_count_drift":
        audit["message_count"] += 1
    elif case == "tool_count_drift":
        audit["tool_schema_count"] += 1
    elif case == "token_sum_drift":
        audit["final_request_token_estimate"] += 1
    elif case == "headroom_drift":
        audit["available_token_headroom"] -= 1
    elif case == "utilization_drift":
        audit["context_window_utilization"] = 0.5
    with pytest.raises(FinalProviderAttemptQualificationError, match=expected_code):
        qualify_final_provider_request(
            workspace=str(tmp_path),
            frozen=frozen,
            binding=dispatch_port._binding,
            final_request_context_audit=audit,
            context_snapshot_ref=context_snapshot_ref,
        )


@pytest.mark.asyncio
async def test_b35_context_snapshot_content_hash_tamper_rejects(tmp_path: Any) -> None:
    _, _, _, prepared = await _prepare_b33_factory_request("qa")
    frozen = prepared.factory_semantic_request
    dispatch_port = prepared.factory_dispatch_port
    assert frozen is not None and dispatch_port is not None
    payload = json.loads(frozen.canonical_final_payload_json)
    context_snapshot_ref = AIExecutor._store_context_messages_sync(
        str(tmp_path),
        payload["messages"],
        frozen.identity.run_id,
        frozen.identity.call_id,
        _b35_provider_request_snapshot(frozen=frozen),
    )
    snapshot_path = Path(ContextSnapshotAuditPinRepository(workspace=str(tmp_path)).snapshot_path(context_snapshot_ref))
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["messages"] = [{"role": "system", "content": "tampered"}]
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(FinalProviderAttemptQualificationError, match="context_snapshot_hash_mismatch"):
        qualify_final_provider_request(
            workspace=str(tmp_path),
            frozen=frozen,
            binding=dispatch_port._binding,
            final_request_context_audit=_b35_audit(frozen=frozen, context_snapshot_ref=context_snapshot_ref),
            context_snapshot_ref=context_snapshot_ref,
        )


@pytest.mark.asyncio
async def test_b35_toolspec_registry_alias_contract_is_exact(tmp_path: Any) -> None:
    schema = ToolSpecRegistry.get_llm_schema(
        "write_file",
        include_arg_aliases=True,
        deterministic=True,
    )
    assert isinstance(schema, dict)
    scoped_schema = pin_write_tool_file_param_to_targets([schema], ("src/main.py",))[0]
    _, _, _, prepared = await _prepare_b33_factory_request(
        "director",
        tool_definitions=[scoped_schema],
        tool_choice="auto",
    )
    frozen = prepared.factory_semantic_request
    dispatch_port = prepared.factory_dispatch_port
    assert frozen is not None and dispatch_port is not None
    payload = json.loads(frozen.canonical_final_payload_json)
    assert payload["tools"] == [scoped_schema]
    context_snapshot_ref = AIExecutor._store_context_messages_sync(
        str(tmp_path),
        payload["messages"],
        frozen.identity.run_id,
        frozen.identity.call_id,
        _b35_provider_request_snapshot(frozen=frozen),
    )
    audit = _b35_audit(frozen=frozen, context_snapshot_ref=context_snapshot_ref)
    registry = audit["final_request_evidence_coverage"]["tool_schema_registry_coverage"]
    registry["schema_hash"] = hashlib.sha256(
        json.dumps(payload["tools"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    qualified = qualify_final_provider_request(
        workspace=str(tmp_path),
        frozen=frozen,
        binding=dispatch_port._binding,
        final_request_context_audit=audit,
        context_snapshot_ref=context_snapshot_ref,
    )
    assert qualified["final_request_evidence_coverage"]["tool_schema_registry_coverage"]["schema_hash"]
    drifted_audit = copy.deepcopy(audit)
    drifted_audit["final_request_evidence_coverage"]["tool_schema_registry_coverage"]["schema_hash"] = "0" * 24
    with pytest.raises(FinalProviderAttemptQualificationError, match="tool_registry_schema_hash_mismatch"):
        qualify_final_provider_request(
            workspace=str(tmp_path),
            frozen=frozen,
            binding=dispatch_port._binding,
            final_request_context_audit=drifted_audit,
            context_snapshot_ref=context_snapshot_ref,
        )


@pytest.mark.asyncio
async def test_b35_qualifies_exact_non_executable_provider_result_protocol(tmp_path: Path) -> None:
    contract = _b35_structured_output_contract()
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )
    assert plan is not None
    _, _, _, prepared = await _prepare_b33_factory_request(
        "chief_engineer",
        workspace=str(tmp_path),
        tool_definitions=[plan.tool_definition],
        tool_choice=plan.tool_choice,
        structured_output_contract=contract,
    )
    frozen = prepared.factory_semantic_request
    dispatch_port = prepared.factory_dispatch_port
    assert frozen is not None and dispatch_port is not None
    assert prepared.structured_output_transport is not None
    payload = json.loads(frozen.canonical_final_payload_json)
    assert payload["tools"] == [plan.tool_definition]
    assert payload["tool_choice"] == plan.tool_choice

    observed = build_final_request_context_audit_for_request(
        ai_request=prepared.ai_request,
        prepared=prepared,
        profile=_profile("chief_engineer"),
    )
    bound = dispatch_port.bind_final_request_context_audit(observed)
    provider_request = {
        "schema_version": "llm.provider_request_snapshot.v1",
        "role": payload["role"],
        "provider_id": payload["provider_id"],
        "model": payload["model"],
        "factory_final_request": final_request_snapshot_evidence(frozen),
        "final_request_context_audit": bound,
    }
    context_snapshot_ref = AIExecutor._store_context_messages_sync(
        str(tmp_path),
        payload["messages"],
        frozen.identity.run_id,
        frozen.identity.call_id,
        provider_request,
    )
    bound["final_request_evidence_coverage"]["context_snapshot_ref"] = context_snapshot_ref

    qualified = qualify_final_provider_request(
        workspace=str(tmp_path),
        frozen=frozen,
        binding=dispatch_port._binding,
        final_request_context_audit=bound,
        context_snapshot_ref=context_snapshot_ref,
    )

    coverage = qualified["final_request_evidence_coverage"]
    assert coverage["provider_protocol_schema_coverage"]["valid"] is True
    assert coverage["provider_protocol_schema_coverage"]["executable_tool"] is False
    assert coverage["tool_schema_registry_coverage"]["missing_schema_tools"] == []
    assert ToolSpecRegistry.get_llm_schema(STRUCTURED_OUTPUT_TOOL_NAME) is None


@pytest.mark.asyncio
async def test_b35_rejects_spoofed_provider_result_protocol_schema(tmp_path: Path) -> None:
    contract = _b35_structured_output_contract()
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )
    assert plan is not None
    spoofed_tool = copy.deepcopy(plan.tool_definition)
    spoofed_tool["function"]["description"] = "Spoofed result protocol."
    _, _, _, prepared = await _prepare_b33_factory_request(
        "chief_engineer",
        workspace=str(tmp_path),
        tool_definitions=[spoofed_tool],
        tool_choice=plan.tool_choice,
        structured_output_contract=contract,
    )
    frozen = prepared.factory_semantic_request
    dispatch_port = prepared.factory_dispatch_port
    assert frozen is not None and dispatch_port is not None
    payload = json.loads(frozen.canonical_final_payload_json)
    observed = build_final_request_context_audit_for_request(
        ai_request=prepared.ai_request,
        prepared=prepared,
        profile=_profile("chief_engineer"),
    )
    assert observed["final_request_evidence_coverage"]["provider_protocol_schema_coverage"]["valid"] is False
    bound = dispatch_port.bind_final_request_context_audit(observed)
    context_snapshot_ref = AIExecutor._store_context_messages_sync(
        str(tmp_path),
        payload["messages"],
        frozen.identity.run_id,
        frozen.identity.call_id,
        {
            "schema_version": "llm.provider_request_snapshot.v1",
            "role": payload["role"],
            "provider_id": payload["provider_id"],
            "model": payload["model"],
            "factory_final_request": final_request_snapshot_evidence(frozen),
            "final_request_context_audit": bound,
        },
    )
    bound["final_request_evidence_coverage"]["context_snapshot_ref"] = context_snapshot_ref

    with pytest.raises(FinalProviderAttemptQualificationError, match="provider_protocol_tool_schema_drift"):
        qualify_final_provider_request(
            workspace=str(tmp_path),
            frozen=frozen,
            binding=dispatch_port._binding,
            final_request_context_audit=bound,
            context_snapshot_ref=context_snapshot_ref,
        )


@pytest.mark.asyncio
async def test_b35_rejects_provider_result_protocol_mixed_with_executable_tools(tmp_path: Path) -> None:
    contract = _b35_structured_output_contract()
    plan = resolve_structured_output_transport(
        {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
    )
    assert plan is not None
    executable_tool = ToolSpecRegistry.get_llm_schema(
        "read_file",
        include_arg_aliases=True,
        deterministic=True,
    )
    assert isinstance(executable_tool, dict)
    _, _, _, prepared = await _prepare_b33_factory_request(
        "chief_engineer",
        workspace=str(tmp_path),
        tool_definitions=[plan.tool_definition, executable_tool],
        tool_choice=plan.tool_choice,
        structured_output_contract=contract,
    )
    frozen = prepared.factory_semantic_request
    dispatch_port = prepared.factory_dispatch_port
    assert frozen is not None and dispatch_port is not None
    payload = json.loads(frozen.canonical_final_payload_json)
    observed = build_final_request_context_audit_for_request(
        ai_request=prepared.ai_request,
        prepared=prepared,
        profile=_profile("chief_engineer"),
    )
    protocol = observed["final_request_evidence_coverage"]["provider_protocol_schema_coverage"]
    assert protocol["valid"] is False
    assert protocol["failure_code"] == "provider_protocol_tool_surface_mixed"
    bound = dispatch_port.bind_final_request_context_audit(observed)
    context_snapshot_ref = AIExecutor._store_context_messages_sync(
        str(tmp_path),
        payload["messages"],
        frozen.identity.run_id,
        frozen.identity.call_id,
        {
            "schema_version": "llm.provider_request_snapshot.v1",
            "role": payload["role"],
            "provider_id": payload["provider_id"],
            "model": payload["model"],
            "factory_final_request": final_request_snapshot_evidence(frozen),
            "final_request_context_audit": bound,
        },
    )
    bound["final_request_evidence_coverage"]["context_snapshot_ref"] = context_snapshot_ref

    with pytest.raises(FinalProviderAttemptQualificationError, match="provider_protocol_tool_surface_mixed"):
        qualify_final_provider_request(
            workspace=str(tmp_path),
            frozen=frozen,
            binding=dispatch_port._binding,
            final_request_context_audit=bound,
            context_snapshot_ref=context_snapshot_ref,
        )


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda body: body.__setitem__("stream", True), "physical_wire_stream_drift"),
        (lambda body: body.__setitem__("max_tokens", body["max_tokens"] - 1), "physical_wire_max_tokens_drift"),
        (lambda body: body.__setitem__("top_p", 0.9), "physical_wire_body_drift"),
        (lambda body: body.__setitem__("parallel_tool_calls", True), "physical_wire_body_drift"),
    ],
)
@pytest.mark.asyncio
async def test_b35_native_body_is_closed_set_and_exact(
    mutation: Any,
    expected_code: str,
) -> None:
    _, _, _, prepared = await _prepare_b33_factory_request("director")
    frozen = prepared.factory_semantic_request
    assert frozen is not None
    wire = _b35_wire(frozen=frozen)
    route = _b35_route_authority(frozen=frozen)
    mutation(wire["body"])

    with pytest.raises(FinalProviderAttemptQualificationError, match=expected_code):
        validate_exact_wire_before_reservation(
            frozen=frozen,
            wire_request=wire,
            physical_route_authority=route,
        )


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("extra_argument", "tool_registry_arg_aliases_drift"),
        ("function_description", "tool_registry_function_contract_drift"),
        ("invalid_scoped_enum", "tool_registry_scoped_enum_invalid"),
        ("unauthorized_scoped_enum", "tool_registry_scoped_enum_unauthorized"),
    ],
)
@pytest.mark.asyncio
async def test_b35_toolspec_registry_contract_drift_rejects(
    tmp_path: Any,
    case: str,
    expected_code: str,
) -> None:
    schema = ToolSpecRegistry.get_llm_schema(
        "write_file",
        include_arg_aliases=True,
        deterministic=True,
    )
    assert isinstance(schema, dict)
    drifted_schema = copy.deepcopy(schema)
    function = drifted_schema["function"]
    properties = function["parameters"]["properties"]
    if case == "extra_argument":
        properties["unregistered_optional_argument"] = {"description": "", "type": "string"}
    elif case == "function_description":
        function["description"] = "unregistered provider-visible contract"
    elif case == "invalid_scoped_enum":
        properties["file"]["enum"] = [7]
    elif case == "unauthorized_scoped_enum":
        properties["content"]["enum"] = ["forced content"]
    _, _, _, prepared = await _prepare_b33_factory_request(
        "director",
        tool_definitions=[drifted_schema],
        tool_choice="auto",
    )
    frozen = prepared.factory_semantic_request
    dispatch_port = prepared.factory_dispatch_port
    assert frozen is not None and dispatch_port is not None
    payload = json.loads(frozen.canonical_final_payload_json)
    context_snapshot_ref = AIExecutor._store_context_messages_sync(
        str(tmp_path),
        payload["messages"],
        frozen.identity.run_id,
        frozen.identity.call_id,
        _b35_provider_request_snapshot(frozen=frozen),
    )
    with pytest.raises(FinalProviderAttemptQualificationError, match=expected_code):
        qualify_final_provider_request(
            workspace=str(tmp_path),
            frozen=frozen,
            binding=dispatch_port._binding,
            final_request_context_audit=_b35_audit(
                frozen=frozen,
                context_snapshot_ref=context_snapshot_ref,
            ),
            context_snapshot_ref=context_snapshot_ref,
        )


@pytest.mark.asyncio
async def test_b35_context_snapshot_symlink_cannot_cross_workspace(tmp_path: Any) -> None:
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    _, _, _, prepared = await _prepare_b33_factory_request("director", workspace=str(workspace_b))
    frozen = prepared.factory_semantic_request
    dispatch_port = prepared.factory_dispatch_port
    assert frozen is not None and dispatch_port is not None
    payload = json.loads(frozen.canonical_final_payload_json)
    context_snapshot_ref = AIExecutor._store_context_messages_sync(
        str(workspace_a),
        payload["messages"],
        frozen.identity.run_id,
        frozen.identity.call_id,
        _b35_provider_request_snapshot(frozen=frozen),
    )
    source_repository = ContextSnapshotAuditPinRepository(workspace=str(workspace_a))
    target_repository = ContextSnapshotAuditPinRepository(workspace=str(workspace_b))
    target_path = Path(target_repository.snapshot_path(context_snapshot_ref))
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.symlink_to(Path(source_repository.snapshot_path(context_snapshot_ref)))
    with pytest.raises(FinalProviderAttemptQualificationError, match="context_snapshot_unreadable"):
        qualify_final_provider_request(
            workspace=str(workspace_b),
            frozen=frozen,
            binding=dispatch_port._binding,
            final_request_context_audit=_b35_audit(
                frozen=frozen,
                context_snapshot_ref=context_snapshot_ref,
            ),
            context_snapshot_ref=context_snapshot_ref,
        )


@pytest.mark.asyncio
async def test_b35_context_snapshot_must_bind_exact_frozen_request(tmp_path: Any) -> None:
    _, _, _, prepared = await _prepare_b33_factory_request("director")
    frozen = prepared.factory_semantic_request
    dispatch_port = prepared.factory_dispatch_port
    assert frozen is not None and dispatch_port is not None
    payload = json.loads(frozen.canonical_final_payload_json)
    provider_snapshot = _b35_provider_request_snapshot(frozen=frozen)
    provider_snapshot["factory_final_request"]["request_identity"]["request_freeze_id"] = "forged-freeze"
    context_snapshot_ref = AIExecutor._store_context_messages_sync(
        str(tmp_path),
        payload["messages"],
        frozen.identity.run_id,
        frozen.identity.call_id,
        provider_snapshot,
    )
    with pytest.raises(FinalProviderAttemptQualificationError, match="context_snapshot_frozen_request_mismatch"):
        qualify_final_provider_request(
            workspace=str(tmp_path),
            frozen=frozen,
            binding=dispatch_port._binding,
            final_request_context_audit=_b35_audit(
                frozen=frozen,
                context_snapshot_ref=context_snapshot_ref,
            ),
            context_snapshot_ref=context_snapshot_ref,
        )


@pytest.mark.asyncio
async def test_b35_unreadable_snapshot_rejects_before_physical_control(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, _, _, prepared = await _prepare_b33_factory_request(
        "director",
        workspace=str(tmp_path),
    )
    frozen = prepared.factory_semantic_request
    dispatch_port = prepared.factory_dispatch_port
    assert frozen is not None and dispatch_port is not None
    context_snapshot_ref = "a" * 24
    audit = _b35_audit(frozen=frozen, context_snapshot_ref=context_snapshot_ref)
    reserve_calls = 0

    def _unexpected_reserve(_command: object) -> object:
        nonlocal reserve_calls
        reserve_calls += 1
        raise AssertionError("qualification must reject before reserve")

    monkeypatch.setattr(port, "reserve", _unexpected_reserve)
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            maintenance_reason="test_b35_unreadable_rejection",
            streams=(qualification_rejection_stream("factory-run-1"),),
        )
    )
    with pytest.raises(FinalProviderAttemptQualificationError, match="context_snapshot_unreadable"):
        dispatch_port.qualify(
            final_request_context_audit=audit,
            context_snapshot_ref=context_snapshot_ref,
        )
    assert reserve_calls == 0
    events = query_fact_events(
        QueryFactEventsV1(
            workspace=str(tmp_path),
            stream=qualification_rejection_stream("factory-run-1"),
            limit=10,
        )
    )
    assert events.total == 1
    assert events.events[0]["payload"]["rejection_code"] == "context_snapshot_unreadable"


@pytest.mark.asyncio
async def test_b35_snapshot_removed_after_pass_is_rejected_before_reservation(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, _, _, prepared = await _prepare_b33_factory_request(
        "director",
        workspace=str(tmp_path),
    )
    frozen = prepared.factory_semantic_request
    dispatch_port = prepared.factory_dispatch_port
    assert frozen is not None and dispatch_port is not None
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            maintenance_reason="test_b35_snapshot_toctou",
            streams=(qualification_rejection_stream("factory-run-1"),),
        )
    )
    payload = json.loads(frozen.canonical_final_payload_json)
    context_snapshot_ref = AIExecutor._store_context_messages_sync(
        str(tmp_path),
        payload["messages"],
        frozen.identity.run_id,
        frozen.identity.call_id,
        _b35_provider_request_snapshot(frozen=frozen),
    )
    dispatch_port.qualify(
        final_request_context_audit=_b35_audit(frozen=frozen, context_snapshot_ref=context_snapshot_ref),
        context_snapshot_ref=context_snapshot_ref,
    )
    _bind_b35_route(dispatch_port, frozen=frozen)
    reserve_calls = 0

    def _unexpected_reserve(_command: object) -> object:
        nonlocal reserve_calls
        reserve_calls += 1
        raise AssertionError("removed qualification snapshot must reject before reserve")

    monkeypatch.setattr(port, "reserve", _unexpected_reserve)
    snapshot_path = Path(ContextSnapshotAuditPinRepository(workspace=str(tmp_path)).snapshot_path(context_snapshot_ref))
    snapshot_path.unlink()
    wire = _b35_wire(frozen=frozen)
    with pytest.raises(FinalProviderAttemptQualificationError, match="context_snapshot_unreadable"):
        dispatch_port.dispatch_sync(wire_request=wire, send=lambda _request: "unexpected")
    assert reserve_calls == 0


@pytest.mark.asyncio
async def test_b35_invoker_arms_exact_sidecar_after_readable_snapshot(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, _, prepared = await _prepare_b33_factory_request(
        "chief_engineer",
        workspace=str(tmp_path),
    )
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            maintenance_reason="test_b35_invoker_qualification",
            streams=(qualification_rejection_stream("factory-run-1"),),
        )
    )
    seen_port: object | None = None

    class _Executor:
        async def invoke(self, _request: object, *, physical_dispatch_port: object) -> str:
            nonlocal seen_port
            seen_port = physical_dispatch_port
            return "qualified"

    from polaris.cells.roles.kernel.internal.llm_caller import (
        context_audit as context_audit_module,
        invoker as invoker_module,
    )

    def _qualified_audit(*, ai_request: Any, prepared: Any, profile: Any) -> dict[str, Any]:
        del profile
        frozen = prepared.factory_semantic_request
        assert frozen is not None
        request_context = getattr(ai_request, "context", None)
        context_snapshot_ref = (
            str(request_context.get("context_snapshot_ref") or "") if isinstance(request_context, dict) else ""
        )
        return _b35_audit(frozen=frozen, context_snapshot_ref=context_snapshot_ref)

    monkeypatch.setattr(invoker_module, "build_final_request_context_audit_for_request", _qualified_audit)
    monkeypatch.setattr(context_audit_module, "build_final_request_context_audit_for_request", _qualified_audit)

    result = await _invoke_executor_with_factory_dispatch(
        executor=_Executor(),
        prepared=prepared,
        request=prepared.ai_request,
        profile=_profile("chief_engineer"),
    )
    assert result == "qualified"
    assert seen_port is prepared.factory_dispatch_port
    assert prepared.factory_dispatch_port is not None
    assert prepared.factory_dispatch_port._qualified_audit is not None
    assert len(prepared.factory_dispatch_port._qualified_context_snapshot_ref) == 24
    first_ref = prepared.factory_dispatch_port._qualified_context_snapshot_ref
    await _invoke_executor_with_factory_dispatch(
        executor=_Executor(),
        prepared=prepared,
        request=prepared.ai_request,
        profile=_profile("chief_engineer"),
    )
    assert prepared.factory_dispatch_port._qualified_context_snapshot_ref == first_ref


@pytest.mark.asyncio
async def test_b35_qualified_sidecar_conserves_one_real_physical_attempt(tmp_path: Any) -> None:
    authority_hash = "f" * 64
    control = FactoryPhysicalAttemptLiveControlPort(
        factory_run_id="factory-run-1",
        revalidate_active_stage_claim=lambda _grant: None,
    )
    control.register_grant(
        FactoryPhysicalAttemptGrantViewV1(
            schema_version=FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
            verification_scope="factory",
            factory_run_id="factory-run-1",
            role="chief_engineer",
            stage="chief_engineer_review",
            workspace_fencing_token=1,
            stage_claim_attempt=1,
            stage_claim_nonce="b35-stage-nonce",
            execution_authority_hash=authority_hash,
            attempt_budget=3,
        )
    )
    _, _, _, prepared = await _prepare_b33_factory_request(
        "chief_engineer",
        workspace=str(tmp_path),
        physical_attempt_control_port=control,
        execution_authority_hash=authority_hash,
        attempt_budget=3,
    )
    frozen = prepared.factory_semantic_request
    dispatch_port = prepared.factory_dispatch_port
    assert frozen is not None and dispatch_port is not None
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            maintenance_reason="test_b35_physical_dispatch",
            streams=(
                "task_runtime.execution",
                qualification_rejection_stream("factory-run-1"),
            ),
        )
    )
    payload = json.loads(frozen.canonical_final_payload_json)
    context_snapshot_ref = AIExecutor._store_context_messages_sync(
        str(tmp_path),
        payload["messages"],
        frozen.identity.run_id,
        frozen.identity.call_id,
        _b35_provider_request_snapshot(frozen=frozen),
    )
    dispatch_port.qualify(
        final_request_context_audit=_b35_audit(frozen=frozen, context_snapshot_ref=context_snapshot_ref),
        context_snapshot_ref=context_snapshot_ref,
    )
    _bind_b35_route(dispatch_port, frozen=frozen)
    sends: list[dict[str, Any]] = []
    wire = _b35_wire(frozen=frozen)
    result = dispatch_port.dispatch_sync(
        wire_request=wire,
        send=lambda request: sends.append(dict(request)) or "ok",
    )
    assert result == "ok"
    assert len(sends) == 1
    assert sends[0]["body"].get("tool_choice") == wire["body"].get("tool_choice")
    assert len(sends[0]["body"]["messages"]) == len(wire["body"]["messages"])
    budget = control.budget_state(authority_hash)
    assert budget.committed_count == 1
    assert budget.terminal_count == 1
    assert budget.consumed_attempts == 1
    assert budget.settled is True


def test_b35_rejection_fact_has_no_physical_attempt_identity(tmp_path: Any) -> None:
    rejection = FinalProviderAttemptQualificationRejectionV1(
        schema_version=FINAL_PROVIDER_ATTEMPT_QUALIFICATION_REJECTION_SCHEMA,
        verification_scope="factory",
        scope_id="factory-run-qualification",
        factory_run_id="factory-run-qualification",
        run_id="role-run-qualification",
        role="director",
        turn_id="role-run-qualification:turn:0",
        call_id="b" * 32,
        request_freeze_id="c" * 32,
        rejection_code="context_snapshot_unreadable",
    )
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            maintenance_reason="test_b35_qualification_rejection",
            streams=(qualification_rejection_stream(rejection.scope_id),),
        )
    )
    append_qualification_rejection(workspace=str(tmp_path), rejection=rejection)
    append_qualification_rejection(workspace=str(tmp_path), rejection=rejection)
    events = query_fact_events(
        QueryFactEventsV1(
            workspace=str(tmp_path),
            stream=qualification_rejection_stream(rejection.scope_id),
            limit=10,
        )
    )
    assert events.total == 1
    payload = events.events[0]["payload"]
    assert payload["rejection_code"] == "context_snapshot_unreadable"
    assert "provider_request_id" not in payload
    assert "reservation" not in payload
    assert "attempt_budget" not in payload
