"""Regression coverage for role-turn facts reaching final provider requests."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import asdict, replace
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.kernel.transaction_factory import create_transaction_kernel
from polaris.cells.roles.kernel.internal.llm_caller import request_preparer as request_preparer_module
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
from polaris.cells.roles.kernel.internal.llm_caller.request_facts import project_role_request_facts
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer
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
from polaris.kernelone.context.context_os.decision_log import build_context_result_id
from polaris.kernelone.context.contracts import TurnEngineContextResult
from polaris.kernelone.events.final_request_evidence import (
    RoleFinalRequestEvidenceAnchorV1,
    RoleFinalRequestEvidenceSlotV1,
    RoleFinalRequestPolicyFactsV1,
    canonical_role_final_request_hash,
    render_role_final_request_policy_facts,
    role_final_request_policy,
)
from polaris.kernelone.llm.engine.contracts import AIRequest


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


def _b32_authority(port: _B32CutoffPort, *, role: str = "chief_engineer") -> FactoryRoleEvidenceAuthorityBindingV1:
    return FactoryRoleEvidenceAuthorityBindingV1(
        schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
        verification_scope="factory",
        factory_run_id="factory-run-1",
        role=role,
        cutoff_port=port,
        physical_attempt_control_port=port,
        attempt_budget=3,
        execution_authority_hash="a" * 64,
    )


async def _prepare_b33_factory_request(
    role: str,
) -> tuple[_B32CutoffPort, FactoryRoleEvidenceAuthorityBindingV1, SimpleNamespace, Any]:
    port = _B32CutoffPort()
    authority = _b32_authority(port, role=role)
    context = SimpleNamespace(
        message="Review.",
        domain="code",
        context_override={
            request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: [
                {"role": "system", "content": f"You are {role}."},
                {"role": "user", "content": "Review."},
            ],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: [],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY: "none",
            "capability_profile_ref": {"sha256": "f" * 64},
        },
    )
    with bind_factory_role_evidence_authority(authority):
        prepared = await LLMRequestPreparer(workspace=".")._prepare_llm_request(
            profile=_profile(role),
            system_prompt=f"You are {role}.",
            context=context,
            temperature=0.2,
            max_tokens=4000,
            stream=False,
            factory_semantic_identity=_semantic_identity(),
        )
    return port, authority, context, prepared


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
