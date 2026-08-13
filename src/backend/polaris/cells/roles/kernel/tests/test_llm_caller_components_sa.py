"""Tests for LLM Caller sub-components without existing coverage.

验证：
1. DecisionCaller 的决策阶段调用
2. FinalizationCaller 的收口阶段调用
3. Error handling 的错误分类
4. StreamEngine 的流式处理
5. EventEmitter 的事件发射
6. ProviderFormatter 的格式化
"""

from __future__ import annotations

import asyncio
import gc
import hashlib
import time
import warnings
from dataclasses import fields
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from polaris.cells.roles.kernel.internal import context_gateway as context_gateway_module
from polaris.cells.roles.kernel.internal.llm_caller import request_preparer as request_preparer_module
from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
    build_final_provider_request_snapshot,
    build_final_request_context_audit,
    build_final_request_context_audit_for_request,
)
from polaris.cells.roles.kernel.internal.llm_caller.decision_caller import DecisionCaller
from polaris.cells.roles.kernel.internal.llm_caller.error_handling import (
    ERROR_CATEGORY_AUTH,
    ERROR_CATEGORY_CANCELLED,
    ERROR_CATEGORY_NETWORK,
    ERROR_CATEGORY_RATE_LIMIT,
    ERROR_CATEGORY_TIMEOUT,
    ERROR_CATEGORY_UNKNOWN,
    build_native_tool_unavailable_error,
    build_text_response_fallback_instruction,
    classify_error,
    is_native_tool_calling_unsupported,
    is_response_format_unsupported,
    is_retryable_error,
)
from polaris.cells.roles.kernel.internal.llm_caller.event_emitter import LLMEventEmitter
from polaris.cells.roles.kernel.internal.llm_caller.finalization_caller import FinalizationCaller
from polaris.cells.roles.kernel.internal.llm_caller.invoker import (
    LLMInvoker,
    _clear_context_snapshot_context,
    _physical_dispatch_port_for_request,
    _profile_lacks_forced_tool_choice,
    _required_tool_not_called_error,
)
from polaris.cells.roles.kernel.internal.llm_caller.provider_formatter import (
    AnnotatedProviderFormatter,
    NativeProviderFormatter,
    create_formatter,
)
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import (
    LLMRequestPreparer,
    _ensure_core_role_identity,
    _ensure_current_user_message_final,
)
from polaris.cells.roles.kernel.internal.llm_caller.response_types import (
    LLMResponse,
    PreparedLLMRequest,
)
from polaris.cells.roles.kernel.internal.llm_caller.stream_engine import (
    StreamEngine,
    _store_context_messages_accepts_provider_request,
)
from polaris.cells.roles.kernel.internal.structured_output_transport import (
    STRUCTURED_OUTPUT_TOOL_NAME,
    resolve_structured_output_transport,
)
from polaris.cells.roles.kernel.public import final_request_evidence_cutoff as cutoff_contract
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
    FACTORY_ROLE_EVIDENCE_CUTOFF_ACK_SCHEMA,
    FactoryRoleEvidenceAuthorityBindingV1,
    FactoryRoleEvidenceCutoffAckV1,
    FactoryRoleEvidenceCutoffRequestV1,
    FactoryRoleSemanticRequestIdentityV1,
    bind_factory_role_evidence_authority,
)
from polaris.cells.roles.kernel.public.structured_output_contracts import (
    STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY,
    RoleStructuredOutputContractV1,
)
from polaris.kernelone.audit.omniscient.dedup import LLMEventDeduplicator, set_global_llm_dedup
from polaris.kernelone.context.contracts import TurnEngineContextResult


@pytest.fixture(autouse=True)
def reset_llm_event_dedup() -> None:
    """Keep global LLM event dedup state from leaking across component tests."""
    set_global_llm_dedup(LLMEventDeduplicator())


def _minimal_director_evidence_context() -> dict[str, object]:
    target_files = ["src/index.ts"]
    return {
        "pm_contract": {
            "schema_version": "pm.task_contract.v1",
            "task_id": "TASK-1",
            "goal": "Implement the product entrypoint.",
            "target_files": target_files,
            "acceptance": ["npm run build succeeds"],
        },
        "chief_engineer_blueprint": {
            "schema_version": "chief_engineer.blueprint.v1",
            "blueprint_id": "ce_TASK-1",
            "target_files": target_files,
            "construction_plan": {"implement": ["src/index.ts"]},
            "scope_for_apply": target_files,
        },
        "target_files": target_files,
        "scope_paths": target_files,
        "file_plan": [{"path": "src/index.ts", "purpose": "application entrypoint"}],
        "module_interface_contract": {
            "schema_version": "chief_engineer.module_interface_contract.v1",
            "modules": [
                {
                    "path": "src/index.ts",
                    "planned_public_symbols": [{"name": "createEntrypoint"}],
                    "actual_public_symbols": [{"name": "createEntrypoint"}],
                    "consumes_symbols": [],
                }
            ],
        },
        "actual_sibling_exports": {
            "schema_version": "actual_sibling_exports.v1",
            "exports": [{"path": "src/index.ts", "name": "createEntrypoint"}],
        },
        "failed_gate_evidence": {
            "schema_version": "polaris.failed_gate_evidence.v1",
            "source": "run_ledger.verifier",
            "command": "npm run build",
            "exit_code": 1,
            "diagnostics": [{"code": "TS1005", "path": "src/index.ts"}],
        },
        "workspace_quality_evidence": {
            "schema_version": "polaris.workspace_quality_evidence.v1",
            "source": "factory_workspace_quality",
            "all_checks_passed": False,
            "quality_errors": [{"code": "typescript_syntax"}],
            "failed_required_modalities": ["command"],
        },
    }


class _B32IdentityCutoffPort:
    async def acquire_cutoff(
        self,
        request: FactoryRoleEvidenceCutoffRequestV1,
    ) -> FactoryRoleEvidenceCutoffAckV1:
        del request
        raise AssertionError("identity tests stop before cutoff acquisition")

    async def resolve_cutoff_proof(self, ack: FactoryRoleEvidenceCutoffAckV1) -> object:
        del ack
        raise AssertionError("identity tests stop before proof resolution")


class _B32PhysicalAttemptControlPort:
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


_B32_PHYSICAL_ATTEMPT_CONTROL_PORT = _B32PhysicalAttemptControlPort()


class _B32CountingCutoffPort:
    def __init__(self) -> None:
        self.acquire_count = 0
        self.resolve_count = 0

    async def acquire_cutoff(
        self,
        request: FactoryRoleEvidenceCutoffRequestV1,
    ) -> FactoryRoleEvidenceCutoffAckV1:
        del request
        self.acquire_count += 1
        raise AssertionError("malformed authority must fail before cutoff acquisition")

    async def resolve_cutoff_proof(self, ack: FactoryRoleEvidenceCutoffAckV1) -> object:
        del ack
        self.resolve_count += 1
        raise AssertionError("malformed authority must fail before proof resolution")


class _B32AcquireBarrierCutoffPort(_B32CountingCutoffPort):
    def __init__(self, *, factory_run_id: str) -> None:
        super().__init__()
        self.factory_run_id = factory_run_id
        self.acquire_started = asyncio.Event()
        self.acquire_release = asyncio.Event()

    async def acquire_cutoff(
        self,
        request: FactoryRoleEvidenceCutoffRequestV1,
    ) -> FactoryRoleEvidenceCutoffAckV1:
        self.acquire_count += 1
        self.acquire_started.set()
        await self.acquire_release.wait()
        return FactoryRoleEvidenceCutoffAckV1(
            schema_version=FACTORY_ROLE_EVIDENCE_CUTOFF_ACK_SCHEMA,
            factory_run_id=self.factory_run_id,
            run_id=request.run_id,
            role=request.role,
            turn_id=request.turn_id,
            call_id=request.call_id,
            request_freeze_id=request.request_freeze_id,
            semantic_candidate_hash=request.semantic_candidate_hash,
            attempt_budget=request.attempt_budget,
            execution_authority_hash=request.execution_authority_hash,
            authority_stream=(
                f"factory.role_evidence_authority.{hashlib.sha256(self.factory_run_id.encode('utf-8')).hexdigest()}"
            ),
            cutoff_fact_id="cutoff-fact-1",
            cutoff_fact_sequence=1,
            cutoff_fact_hash="c" * 64,
            cutoff_body_hash="d" * 64,
            cutoff_fragment_vector_hash="e" * 64,
            cutoff_fragment_count=1,
        )


def _b32_identity_authority() -> FactoryRoleEvidenceAuthorityBindingV1:
    return FactoryRoleEvidenceAuthorityBindingV1(
        schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
        verification_scope="factory",
        factory_run_id="factory-run-identity",
        role="director",
        cutoff_port=_B32IdentityCutoffPort(),
        physical_attempt_control_port=_B32_PHYSICAL_ATTEMPT_CONTROL_PORT,
        attempt_budget=3,
        execution_authority_hash="a" * 64,
    )


def _b32_profile() -> SimpleNamespace:
    return SimpleNamespace(
        role_id="director",
        provider_id="provider-a",
        provider_type="openai_compat",
        model="kimi-for-coding",
        max_context_tokens=262_144,
        tool_policy=SimpleNamespace(whitelist=()),
    )


def _b32_context() -> SimpleNamespace:
    messages = [
        {"role": "system", "content": "You are Director."},
        {"role": "user", "content": "Implement."},
    ]
    return SimpleNamespace(
        message="Implement.",
        domain="code",
        task_id=None,
        context_override={
            "run_id": "forged-run",
            "turn_id": "forged-turn",
            "call_id": "f" * 32,
            "request_freeze_id": "e" * 32,
            request_preparer_module._TRANSACTION_KERNEL_PREBUILT_MESSAGES_KEY: messages,
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY: [],
            request_preparer_module._TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY: "none",
        },
    )


def _b32_semantic_identity() -> FactoryRoleSemanticRequestIdentityV1:
    return FactoryRoleSemanticRequestIdentityV1(
        run_id="role-run-controlled",
        turn_id="role-run-controlled:turn:0",
        call_id="a" * 32,
        request_freeze_id="b" * 32,
    )


@pytest.mark.parametrize(
    ("field_name", "corrupted_value", "expected_exception", "expected_error"),
    [
        (
            "schema_version",
            "polaris.forged.v1",
            ValueError,
            "factory_role_evidence_authority_binding_schema_mismatch",
        ),
        ("verification_scope", "other", ValueError, "verification_scope_mismatch"),
        ("factory_run_id", "", ValueError, "factory_run_id_missing"),
        ("cutoff_port", object(), TypeError, "factory_role_evidence_cutoff_port_required"),
        (
            "physical_attempt_control_port",
            object(),
            TypeError,
            "factory_physical_attempt_control_port_required",
        ),
        ("attempt_budget", 0, ValueError, "attempt_budget_invalid"),
        ("execution_authority_hash", "0", ValueError, "execution_authority_hash_invalid"),
    ],
)
@pytest.mark.asyncio
async def test_factory_authority_mutation_after_bind_fails_before_cutoff(
    field_name: str,
    corrupted_value: object,
    expected_exception: type[Exception],
    expected_error: str,
) -> None:
    port = _B32CountingCutoffPort()
    authority = FactoryRoleEvidenceAuthorityBindingV1(
        schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
        verification_scope="factory",
        factory_run_id="factory-run-mutation",
        role="director",
        cutoff_port=port,
        physical_attempt_control_port=_B32_PHYSICAL_ATTEMPT_CONTROL_PORT,
        attempt_budget=3,
        execution_authority_hash="a" * 64,
    )

    with bind_factory_role_evidence_authority(authority):
        object.__setattr__(authority, field_name, corrupted_value)
        with pytest.raises(expected_exception, match=expected_error):
            await LLMRequestPreparer(workspace=".")._prepare_llm_request(
                profile=_b32_profile(),
                system_prompt="You are Director.",
                context=_b32_context(),
                temperature=0.2,
                max_tokens=4000,
                stream=False,
                factory_semantic_identity=_b32_semantic_identity(),
            )

    assert port.acquire_count == 0
    assert port.resolve_count == 0


@pytest.mark.asyncio
async def test_factory_authority_subclass_fails_before_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _AuthoritySubclass(FactoryRoleEvidenceAuthorityBindingV1):
        pass

    port = _B32CountingCutoffPort()
    authority = _AuthoritySubclass(
        schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
        verification_scope="factory",
        factory_run_id="factory-run-subclass",
        role="director",
        cutoff_port=port,
        physical_attempt_control_port=_B32_PHYSICAL_ATTEMPT_CONTROL_PORT,
        attempt_budget=3,
        execution_authority_hash="a" * 64,
    )
    monkeypatch.setattr(
        request_preparer_module,
        "get_factory_role_evidence_authority_binding",
        lambda: authority,
    )

    with pytest.raises(TypeError, match="factory_role_evidence_authority_binding_exact_type_required"):
        await LLMRequestPreparer(workspace=".")._prepare_llm_request(
            profile=_b32_profile(),
            system_prompt="You are Director.",
            context=_b32_context(),
            temperature=0.2,
            max_tokens=4000,
            stream=False,
            factory_semantic_identity=_b32_semantic_identity(),
        )

    assert port.acquire_count == 0
    assert port.resolve_count == 0


@pytest.mark.asyncio
async def test_factory_authority_valid_drift_during_context_await_fails_before_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_started = asyncio.Event()
    context_release = asyncio.Event()

    class _AwaitBarrierGateway:
        def __init__(self, _profile: object, _workspace: object) -> None:
            pass

        async def build_context(
            self,
            _context: object,
            *,
            system_prompt: str,
        ) -> TurnEngineContextResult:
            context_started.set()
            await context_release.wait()
            return TurnEngineContextResult(
                messages=(
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "Implement."},
                ),
                token_estimate=10,
            )

    monkeypatch.setattr(context_gateway_module, "RoleContextGateway", _AwaitBarrierGateway)
    old_port = _B32CountingCutoffPort()
    new_port = _B32CountingCutoffPort()
    authority = FactoryRoleEvidenceAuthorityBindingV1(
        schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
        verification_scope="factory",
        factory_run_id="factory-run-before-await",
        role="director",
        cutoff_port=old_port,
        physical_attempt_control_port=_B32_PHYSICAL_ATTEMPT_CONTROL_PORT,
        attempt_budget=3,
        execution_authority_hash="a" * 64,
    )
    context = SimpleNamespace(message="Implement.", domain="code", context_override={})

    with bind_factory_role_evidence_authority(authority):
        prepare_task = asyncio.create_task(
            LLMRequestPreparer(workspace=".")._prepare_llm_request(
                profile=_b32_profile(),
                system_prompt="You are Director.",
                context=context,
                temperature=0.2,
                max_tokens=4000,
                stream=False,
                factory_semantic_identity=_b32_semantic_identity(),
            )
        )
        await context_started.wait()
        object.__setattr__(authority, "factory_run_id", "factory-run-after-await")
        object.__setattr__(authority, "cutoff_port", new_port)
        object.__setattr__(authority, "attempt_budget", 4)
        object.__setattr__(authority, "execution_authority_hash", "b" * 64)
        context_release.set()
        with pytest.raises(RuntimeError, match="factory_role_evidence_authority_binding_drift"):
            await prepare_task

    assert old_port.acquire_count == 0
    assert old_port.resolve_count == 0
    assert new_port.acquire_count == 0
    assert new_port.resolve_count == 0


@pytest.mark.asyncio
async def test_factory_authority_port_drift_during_acquire_fails_before_resolve() -> None:
    factory_run_id = "factory-run-acquire-await"
    old_port = _B32AcquireBarrierCutoffPort(factory_run_id=factory_run_id)
    new_port = _B32CountingCutoffPort()
    authority = FactoryRoleEvidenceAuthorityBindingV1(
        schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
        verification_scope="factory",
        factory_run_id=factory_run_id,
        role="director",
        cutoff_port=old_port,
        physical_attempt_control_port=_B32_PHYSICAL_ATTEMPT_CONTROL_PORT,
        attempt_budget=3,
        execution_authority_hash="a" * 64,
    )

    with bind_factory_role_evidence_authority(authority):
        prepare_task = asyncio.create_task(
            LLMRequestPreparer(workspace=".")._prepare_llm_request(
                profile=_b32_profile(),
                system_prompt="You are Director.",
                context=_b32_context(),
                temperature=0.2,
                max_tokens=4000,
                stream=False,
                factory_semantic_identity=_b32_semantic_identity(),
            )
        )
        await old_port.acquire_started.wait()
        object.__setattr__(authority, "cutoff_port", new_port)
        old_port.acquire_release.set()
        with pytest.raises(RuntimeError, match="factory_role_evidence_authority_binding_drift"):
            await prepare_task

    assert old_port.acquire_count == 1
    assert old_port.resolve_count == 0
    assert new_port.acquire_count == 0
    assert new_port.resolve_count == 0


@pytest.mark.asyncio
async def test_factory_physical_attempt_control_port_drift_during_acquire_fails_before_resolve() -> None:
    factory_run_id = "factory-run-physical-port-await"
    cutoff_port = _B32AcquireBarrierCutoffPort(factory_run_id=factory_run_id)
    original_physical_port = _B32PhysicalAttemptControlPort()
    replacement_physical_port = _B32PhysicalAttemptControlPort()
    authority = FactoryRoleEvidenceAuthorityBindingV1(
        schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
        verification_scope="factory",
        factory_run_id=factory_run_id,
        role="director",
        cutoff_port=cutoff_port,
        physical_attempt_control_port=original_physical_port,
        attempt_budget=3,
        execution_authority_hash="a" * 64,
    )

    with bind_factory_role_evidence_authority(authority):
        prepare_task = asyncio.create_task(
            LLMRequestPreparer(workspace=".")._prepare_llm_request(
                profile=_b32_profile(),
                system_prompt="You are Director.",
                context=_b32_context(),
                temperature=0.2,
                max_tokens=4000,
                stream=False,
                factory_semantic_identity=_b32_semantic_identity(),
            )
        )
        await cutoff_port.acquire_started.wait()
        object.__setattr__(authority, "physical_attempt_control_port", replacement_physical_port)
        cutoff_port.acquire_release.set()
        with pytest.raises(RuntimeError, match="factory_role_evidence_authority_binding_drift"):
            await prepare_task

    assert cutoff_port.acquire_count == 1
    assert cutoff_port.resolve_count == 0




async def test_factory_authority_mutation_after_bind_fails_before_cutoff(
    field_name: str,
    corrupted_value: object,
    expected_exception: type[Exception],
    expected_error: str,
) -> None:
    port = _B32CountingCutoffPort()
    authority = FactoryRoleEvidenceAuthorityBindingV1(
        schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
        verification_scope="factory",
        factory_run_id="factory-run-mutation",
        role="director",
        cutoff_port=port,
        physical_attempt_control_port=_B32_PHYSICAL_ATTEMPT_CONTROL_PORT,
        attempt_budget=3,
        execution_authority_hash="a" * 64,
    )

    with bind_factory_role_evidence_authority(authority):
        object.__setattr__(authority, field_name, corrupted_value)
        with pytest.raises(expected_exception, match=expected_error):
            await LLMRequestPreparer(workspace=".")._prepare_llm_request(
                profile=_b32_profile(),
                system_prompt="You are Director.",
                context=_b32_context(),
                temperature=0.2,
                max_tokens=4000,
                stream=False,
                factory_semantic_identity=_b32_semantic_identity(),
            )

    assert port.acquire_count == 0
    assert port.resolve_count == 0


@pytest.mark.asyncio
async def test_factory_authority_subclass_fails_before_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _AuthoritySubclass(FactoryRoleEvidenceAuthorityBindingV1):
        pass

    port = _B32CountingCutoffPort()
    authority = _AuthoritySubclass(
        schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
        verification_scope="factory",
        factory_run_id="factory-run-subclass",
        role="director",
        cutoff_port=port,
        physical_attempt_control_port=_B32_PHYSICAL_ATTEMPT_CONTROL_PORT,
        attempt_budget=3,
        execution_authority_hash="a" * 64,
    )
    monkeypatch.setattr(
        request_preparer_module,
        "get_factory_role_evidence_authority_binding",
        lambda: authority,
    )

    with pytest.raises(TypeError, match="factory_role_evidence_authority_binding_exact_type_required"):
        await LLMRequestPreparer(workspace=".")._prepare_llm_request(
            profile=_b32_profile(),
            system_prompt="You are Director.",
            context=_b32_context(),
            temperature=0.2,
            max_tokens=4000,
            stream=False,
            factory_semantic_identity=_b32_semantic_identity(),
        )

    assert port.acquire_count == 0
    assert port.resolve_count == 0


@pytest.mark.asyncio
async def test_factory_authority_valid_drift_during_context_await_fails_before_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_started = asyncio.Event()
    context_release = asyncio.Event()

    class _AwaitBarrierGateway:
        def __init__(self, _profile: object, _workspace: object) -> None:
            pass

        async def build_context(
            self,
            _context: object,
            *,
            system_prompt: str,
        ) -> TurnEngineContextResult:
            context_started.set()
            await context_release.wait()
            return TurnEngineContextResult(
                messages=(
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "Implement."},
                ),
                token_estimate=10,
            )

    monkeypatch.setattr(context_gateway_module, "RoleContextGateway", _AwaitBarrierGateway)
    old_port = _B32CountingCutoffPort()
    new_port = _B32CountingCutoffPort()
    authority = FactoryRoleEvidenceAuthorityBindingV1(
        schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
        verification_scope="factory",
        factory_run_id="factory-run-before-await",
        role="director",
        cutoff_port=old_port,
        physical_attempt_control_port=_B32_PHYSICAL_ATTEMPT_CONTROL_PORT,
        attempt_budget=3,
        execution_authority_hash="a" * 64,
    )
    context = SimpleNamespace(message="Implement.", domain="code", context_override={})

    with bind_factory_role_evidence_authority(authority):
        prepare_task = asyncio.create_task(
            LLMRequestPreparer(workspace=".")._prepare_llm_request(
                profile=_b32_profile(),
                system_prompt="You are Director.",
                context=context,
                temperature=0.2,
                max_tokens=4000,
                stream=False,
                factory_semantic_identity=_b32_semantic_identity(),
            )
        )
        await context_started.wait()
        object.__setattr__(authority, "factory_run_id", "factory-run-after-await")
        object.__setattr__(authority, "cutoff_port", new_port)
        object.__setattr__(authority, "attempt_budget", 4)
        object.__setattr__(authority, "execution_authority_hash", "b" * 64)
        context_release.set()
        with pytest.raises(RuntimeError, match="factory_role_evidence_authority_binding_drift"):
            await prepare_task

    assert old_port.acquire_count == 0
    assert old_port.resolve_count == 0
    assert new_port.acquire_count == 0
    assert new_port.resolve_count == 0


@pytest.mark.asyncio
async def test_factory_authority_port_drift_during_acquire_fails_before_resolve() -> None:
    factory_run_id = "factory-run-acquire-await"
    old_port = _B32AcquireBarrierCutoffPort(factory_run_id=factory_run_id)
    new_port = _B32CountingCutoffPort()
    authority = FactoryRoleEvidenceAuthorityBindingV1(
        schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
        verification_scope="factory",
        factory_run_id=factory_run_id,
        role="director",
        cutoff_port=old_port,
        physical_attempt_control_port=_B32_PHYSICAL_ATTEMPT_CONTROL_PORT,
        attempt_budget=3,
        execution_authority_hash="a" * 64,
    )

    with bind_factory_role_evidence_authority(authority):
        prepare_task = asyncio.create_task(
            LLMRequestPreparer(workspace=".")._prepare_llm_request(
                profile=_b32_profile(),
                system_prompt="You are Director.",
                context=_b32_context(),
                temperature=0.2,
                max_tokens=4000,
                stream=False,
                factory_semantic_identity=_b32_semantic_identity(),
            )
        )
        await old_port.acquire_started.wait()
        object.__setattr__(authority, "cutoff_port", new_port)
        old_port.acquire_release.set()
        with pytest.raises(RuntimeError, match="factory_role_evidence_authority_binding_drift"):
            await prepare_task

    assert old_port.acquire_count == 1
    assert old_port.resolve_count == 0
    assert new_port.acquire_count == 0
    assert new_port.resolve_count == 0


@pytest.mark.asyncio
async def test_factory_physical_attempt_control_port_drift_during_acquire_fails_before_resolve() -> None:
    factory_run_id = "factory-run-physical-port-await"
    cutoff_port = _B32AcquireBarrierCutoffPort(factory_run_id=factory_run_id)
    original_physical_port = _B32PhysicalAttemptControlPort()
    replacement_physical_port = _B32PhysicalAttemptControlPort()
    authority = FactoryRoleEvidenceAuthorityBindingV1(
        schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
        verification_scope="factory",
        factory_run_id=factory_run_id,
        role="director",
        cutoff_port=cutoff_port,
        physical_attempt_control_port=original_physical_port,
        attempt_budget=3,
        execution_authority_hash="a" * 64,
    )

    with bind_factory_role_evidence_authority(authority):
        prepare_task = asyncio.create_task(
            LLMRequestPreparer(workspace=".")._prepare_llm_request(
                profile=_b32_profile(),
                system_prompt="You are Director.",
                context=_b32_context(),
                temperature=0.2,
                max_tokens=4000,
                stream=False,
                factory_semantic_identity=_b32_semantic_identity(),
            )
        )
        await cutoff_port.acquire_started.wait()
        object.__setattr__(authority, "physical_attempt_control_port", replacement_physical_port)
        cutoff_port.acquire_release.set()
        with pytest.raises(RuntimeError, match="factory_role_evidence_authority_binding_drift"):
            await prepare_task

    assert cutoff_port.acquire_count == 1
    assert cutoff_port.resolve_count == 0




async def test_factory_authority_mutation_after_bind_fails_before_cutoff(
    field_name: str,
    corrupted_value: object,
    expected_exception: type[Exception],
    expected_error: str,
) -> None:
    port = _B32CountingCutoffPort()
    authority = FactoryRoleEvidenceAuthorityBindingV1(
        schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
        verification_scope="factory",
        factory_run_id="factory-run-mutation",
        role="director",
        cutoff_port=port,
        physical_attempt_control_port=_B32_PHYSICAL_ATTEMPT_CONTROL_PORT,
        attempt_budget=3,
        execution_authority_hash="a" * 64,
    )

    with bind_factory_role_evidence_authority(authority):
        object.__setattr__(authority, field_name, corrupted_value)
        with pytest.raises(expected_exception, match=expected_error):
            await LLMRequestPreparer(workspace=".")._prepare_llm_request(
                profile=_b32_profile(),
                system_prompt="You are Director.",
                context=_b32_context(),
                temperature=0.2,
                max_tokens=4000,
                stream=False,
                factory_semantic_identity=_b32_semantic_identity(),
            )

    assert port.acquire_count == 0
    assert port.resolve_count == 0


@pytest.mark.asyncio
async def test_factory_authority_subclass_fails_before_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _AuthoritySubclass(FactoryRoleEvidenceAuthorityBindingV1):
        pass

    port = _B32CountingCutoffPort()
    authority = _AuthoritySubclass(
        schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
        verification_scope="factory",
        factory_run_id="factory-run-subclass",
        role="director",
        cutoff_port=port,
        physical_attempt_control_port=_B32_PHYSICAL_ATTEMPT_CONTROL_PORT,
        attempt_budget=3,
        execution_authority_hash="a" * 64,
    )
    monkeypatch.setattr(
        request_preparer_module,
        "get_factory_role_evidence_authority_binding",
        lambda: authority,
    )

    with pytest.raises(TypeError, match="factory_role_evidence_authority_binding_exact_type_required"):
        await LLMRequestPreparer(workspace=".")._prepare_llm_request(
            profile=_b32_profile(),
            system_prompt="You are Director.",
            context=_b32_context(),
            temperature=0.2,
            max_tokens=4000,
            stream=False,
            factory_semantic_identity=_b32_semantic_identity(),
        )

    assert port.acquire_count == 0
    assert port.resolve_count == 0


@pytest.mark.asyncio
async def test_factory_authority_valid_drift_during_context_await_fails_before_cutoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_started = asyncio.Event()
    context_release = asyncio.Event()

    class _AwaitBarrierGateway:
        def __init__(self, _profile: object, _workspace: object) -> None:
            pass

        async def build_context(
            self,
            _context: object,
            *,
            system_prompt: str,
        ) -> TurnEngineContextResult:
            context_started.set()
            await context_release.wait()
            return TurnEngineContextResult(
                messages=(
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "Implement."},
                ),
                token_estimate=10,
            )

    monkeypatch.setattr(context_gateway_module, "RoleContextGateway", _AwaitBarrierGateway)
    old_port = _B32CountingCutoffPort()
    new_port = _B32CountingCutoffPort()
    authority = FactoryRoleEvidenceAuthorityBindingV1(
        schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
        verification_scope="factory",
        factory_run_id="factory-run-before-await",
        role="director",
        cutoff_port=old_port,
        physical_attempt_control_port=_B32_PHYSICAL_ATTEMPT_CONTROL_PORT,
        attempt_budget=3,
        execution_authority_hash="a" * 64,
    )
    context = SimpleNamespace(message="Implement.", domain="code", context_override={})

    with bind_factory_role_evidence_authority(authority):
        prepare_task = asyncio.create_task(
            LLMRequestPreparer(workspace=".")._prepare_llm_request(
                profile=_b32_profile(),
                system_prompt="You are Director.",
                context=context,
                temperature=0.2,
                max_tokens=4000,
                stream=False,
                factory_semantic_identity=_b32_semantic_identity(),
            )
        )
        await context_started.wait()
        object.__setattr__(authority, "factory_run_id", "factory-run-after-await")
        object.__setattr__(authority, "cutoff_port", new_port)
        object.__setattr__(authority, "attempt_budget", 4)
        object.__setattr__(authority, "execution_authority_hash", "b" * 64)
        context_release.set()
        with pytest.raises(RuntimeError, match="factory_role_evidence_authority_binding_drift"):
            await prepare_task

    assert old_port.acquire_count == 0
    assert old_port.resolve_count == 0
    assert new_port.acquire_count == 0
    assert new_port.resolve_count == 0


@pytest.mark.asyncio
async def test_factory_authority_port_drift_during_acquire_fails_before_resolve() -> None:
    factory_run_id = "factory-run-acquire-await"
    old_port = _B32AcquireBarrierCutoffPort(factory_run_id=factory_run_id)
    new_port = _B32CountingCutoffPort()
    authority = FactoryRoleEvidenceAuthorityBindingV1(
        schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
        verification_scope="factory",
        factory_run_id=factory_run_id,
        role="director",
        cutoff_port=old_port,
        physical_attempt_control_port=_B32_PHYSICAL_ATTEMPT_CONTROL_PORT,
        attempt_budget=3,
        execution_authority_hash="a" * 64,
    )

    with bind_factory_role_evidence_authority(authority):
        prepare_task = asyncio.create_task(
            LLMRequestPreparer(workspace=".")._prepare_llm_request(
                profile=_b32_profile(),
                system_prompt="You are Director.",
                context=_b32_context(),
                temperature=0.2,
                max_tokens=4000,
                stream=False,
                factory_semantic_identity=_b32_semantic_identity(),
            )
        )
        await old_port.acquire_started.wait()
        object.__setattr__(authority, "cutoff_port", new_port)
        old_port.acquire_release.set()
        with pytest.raises(RuntimeError, match="factory_role_evidence_authority_binding_drift"):
            await prepare_task

    assert old_port.acquire_count == 1
    assert old_port.resolve_count == 0
    assert new_port.acquire_count == 0
    assert new_port.resolve_count == 0


@pytest.mark.asyncio
async def test_factory_physical_attempt_control_port_drift_during_acquire_fails_before_resolve() -> None:
    factory_run_id = "factory-run-physical-port-await"
    cutoff_port = _B32AcquireBarrierCutoffPort(factory_run_id=factory_run_id)
    original_physical_port = _B32PhysicalAttemptControlPort()
    replacement_physical_port = _B32PhysicalAttemptControlPort()
    authority = FactoryRoleEvidenceAuthorityBindingV1(
        schema_version=FACTORY_ROLE_EVIDENCE_AUTHORITY_BINDING_SCHEMA,
        verification_scope="factory",
        factory_run_id=factory_run_id,
        role="director",
        cutoff_port=cutoff_port,
        physical_attempt_control_port=original_physical_port,
        attempt_budget=3,
        execution_authority_hash="a" * 64,
    )

    with bind_factory_role_evidence_authority(authority):
        prepare_task = asyncio.create_task(
            LLMRequestPreparer(workspace=".")._prepare_llm_request(
                profile=_b32_profile(),
                system_prompt="You are Director.",
                context=_b32_context(),
                temperature=0.2,
                max_tokens=4000,
                stream=False,
                factory_semantic_identity=_b32_semantic_identity(),
            )
        )
        await cutoff_port.acquire_started.wait()
        object.__setattr__(authority, "physical_attempt_control_port", replacement_physical_port)
        cutoff_port.acquire_release.set()
        with pytest.raises(RuntimeError, match="factory_role_evidence_authority_binding_drift"):
            await prepare_task

    assert cutoff_port.acquire_count == 1
    assert cutoff_port.resolve_count == 0


def test_existing_factory_role_marker_must_be_terminal_and_canonical() -> None:
    messages = [
        {
            "role": "system",
            "content": "You are Director.\n\npolaris.role_identity.v1:director\nintervening text",
        },
        {"role": "user", "content": "Implement."},
    ]

    with pytest.raises(RuntimeError, match="role_identity_marker_invalid:marker_must_be_terminal"):
        _ensure_core_role_identity(
            messages,
            "director",
            system_prompt="You are Director.",
        )


def test_context_snapshot_clear_removes_snake_and_camel_projection_keys() -> None:
    request = SimpleNamespace(
        context={
            "context_snapshot_ref": "a" * 24,
            "contextSnapshotRef": "b" * 24,
            "context_snapshot_degraded": {"code": "old"},
            "contextSnapshotDegraded": {"code": "old-camel"},
            "context_snapshot_degraded_reason": "old",
            "contextSnapshotDegradedReason": "old-camel",
            "keep": "value",
        }
    )

    result = _clear_context_snapshot_context(request)

    assert result == {"keep": "value"}
    assert request.context == {"keep": "value"}


def test_b33_prepared_factory_freeze_requires_exact_dispatch_sidecar() -> None:
    """A Factory semantic freeze without its runtime sidecar must fail closed."""

    with pytest.raises(
        RuntimeError,
        match="factory_role_semantic_request_dispatch_port_required",
    ):
        PreparedLLMRequest(
            messages=[],
            input_text="",
            context_result=None,
            context_summary="",
            request_options={},
            ai_request=SimpleNamespace(),
            factory_semantic_request=Mock(),
        )


def test_b33_prepared_bundle_declares_runtime_private_dispatch_sidecar() -> None:
    """Sync seams need one explicit sidecar slot beside, never inside, AIRequest."""

    assert "factory_dispatch_port" in {item.name for item in fields(PreparedLLMRequest)}


@pytest.mark.asyncio
async def test_b33_sync_profile_binding_passes_exact_port_identity() -> None:
    request = SimpleNamespace(provider_id="provider-a", model="model-a")
    frozen = object()
    port = Mock()
    prepared = SimpleNamespace(
        ai_request=request,
        factory_semantic_request=frozen,
        factory_dispatch_port=port,
        __post_init__=Mock(),
    )
    executor = SimpleNamespace(invoke=AsyncMock(return_value=SimpleNamespace(ok=True)))
    profile = SimpleNamespace(provider_id="provider-a", model="model-a")

    result = await LLMInvoker(workspace="/ws")._invoke_with_profile_binding(
        executor=executor,
        prepared=prepared,
        request=request,
        profile=profile,
        role_id="director",
    )

    assert result.ok is True
    assert executor.invoke.await_args.kwargs["physical_dispatch_port"] is port
    prepared.__post_init__.assert_called_once_with()
    port.validate_frozen_identity.assert_called_once_with(frozen)


def test_b33_semantic_changing_retry_requires_new_freeze_and_port() -> None:
    original_request = object()
    prepared = SimpleNamespace(
        ai_request=original_request,
        factory_semantic_request=object(),
        factory_dispatch_port=Mock(),
        __post_init__=Mock(),
    )

    with pytest.raises(RuntimeError, match="factory_role_semantic_retry_refreeze_required"):
        _physical_dispatch_port_for_request(prepared, object())


def test_b33_factory_cache_cannot_satisfy_governed_call() -> None:
    prepared = _b33_propagating_prepared()
    invoker = LLMInvoker(workspace="/ws", enable_cache=True)
    cache = Mock()
    cache.get.return_value = "stale cached response"

    assert LLMInvoker._is_cache_eligible(prepared=prepared, response_model=None) is False
    hit = invoker._try_cache_hit(
        cache=cache,
        prepared=prepared,
        context_result=prepared.context_result,
        cache_eligible=True,
        prompt_fingerprint="fingerprint",
        temperature=0.2,
        model="model-a",
        profile=SimpleNamespace(provider_id="provider-a", model="model-a"),
        role_id="director",
        run_id="run-1",
        task_id=None,
        attempt=0,
        turn_round=0,
        call_id="call-1",
        event_emitter=None,
        start_time=time.perf_counter(),
    )

    assert hit is None
    cache.get.assert_not_called()


@pytest.mark.parametrize(
    ("case_id", "native_response_format", "response_error", "builder_name"),
    [
        (
            "response_format",
            True,
            "unsupported parameter: response_format",
            "_build_structured_fallback_request",
        ),
        (
            "reasoning_truncation",
            False,
            "empty visible output: reasoning truncated finish_reason=length",
            "_build_reasoning_truncation_retry_request",
        ),
    ],
)
@pytest.mark.asyncio
async def test_b33_fallback_ladder_semantic_change_refreezes_with_new_port(
    case_id: str,
    native_response_format: bool,
    response_error: str,
    builder_name: str,
) -> None:
    del case_id
    prepared = _b33_propagating_prepared(native_response_format=native_response_format)
    changed_request = object()
    retry_prepared = _b33_propagating_prepared()
    retry_prepared.ai_request = changed_request
    retry_prepared.factory_dispatch_port = Mock()
    order: list[str] = []

    async def _reprepare(**_kwargs):
        order.append("reprepare")
        return retry_prepared

    async def _invoke(*_args, **_kwargs):
        order.append("invoke")
        return SimpleNamespace(ok=True, error=None)

    async def _snapshot(**_kwargs):
        order.append("snapshot")

    def _audit(payload, **_kwargs):
        order.append("audit")
        return payload

    request_preparer = SimpleNamespace(
        _build_structured_fallback_request=Mock(return_value=changed_request),
        _build_reasoning_truncation_retry_request=Mock(return_value=changed_request),
        _reprepare_factory_semantic_retry_request=AsyncMock(side_effect=_reprepare),
    )
    executor = SimpleNamespace(invoke=AsyncMock(side_effect=_invoke))
    invoker = LLMInvoker(workspace="/ws")
    profile = SimpleNamespace(provider_id="provider-a", model="model-a")

    with (
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker._store_active_request_context_snapshot",
            AsyncMock(side_effect=_snapshot),
        ),
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker._with_final_request_context_audit",
            side_effect=_audit,
        ),
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker._required_tool_not_called_error",
            return_value="",
        ),
    ):
        result = await invoker._run_fallback_ladder(
            request_preparer=request_preparer,
            executor=executor,
            prepared=prepared,
            profile=profile,
            context=SimpleNamespace(),
            response=SimpleNamespace(ok=False, error=response_error),
            active_request=prepared.ai_request,
            response_model=dict,
            response_error=response_error,
            is_response_ok=False,
            allow_native_tool_text_fallback=False,
            native_tool_fallback=False,
            native_response_fallback=False,
            system_prompt="system",
            temperature=0.2,
            effective_max_tokens=128,
            platform_retry_max=0,
            role_id="director",
            run_id="run-1",
            task_id=None,
            attempt=0,
            model="model-a",
            call_id="call-1",
            event_emitter=None,
            factory_semantic_identity=_b32_semantic_identity(),
        )

    getattr(request_preparer, builder_name).assert_called_once()
    request_preparer._reprepare_factory_semantic_retry_request.assert_awaited_once_with(
        prepared=prepared,
        request=changed_request,
        profile=profile,
    )
    assert result.prepared is retry_prepared
    assert executor.invoke.await_args.kwargs["physical_dispatch_port"] is retry_prepared.factory_dispatch_port
    assert order == ["reprepare", "snapshot", "audit", "invoke"]


@pytest.mark.parametrize("text_fallback", [False, True], ids=["native", "text"])
@pytest.mark.asyncio
async def test_b33_required_tool_retry_semantic_change_refreezes_with_new_port(
    text_fallback: bool,
) -> None:
    prepared = _b33_propagating_prepared()
    changed_request = object()
    retry_prepared = _b33_propagating_prepared()
    retry_prepared.ai_request = changed_request
    retry_prepared.factory_dispatch_port = Mock()
    order: list[str] = []

    async def _reprepare(**_kwargs):
        order.append("reprepare")
        return retry_prepared

    async def _invoke(*_args, **_kwargs):
        order.append("invoke")
        return SimpleNamespace(ok=True, error=None)

    async def _snapshot(**_kwargs):
        order.append("snapshot")

    def _audit(payload, **_kwargs):
        order.append("audit")
        return payload

    request_preparer = SimpleNamespace(
        _build_required_tool_retry_request=Mock(return_value=changed_request),
        _build_required_tool_text_fallback_request=Mock(return_value=changed_request),
        _reprepare_factory_semantic_retry_request=AsyncMock(side_effect=_reprepare),
    )
    executor = SimpleNamespace(invoke=AsyncMock(side_effect=_invoke))
    invoker = LLMInvoker(workspace="/ws")
    profile = SimpleNamespace(provider_id="provider-a", model="model-a")

    with (
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker._profile_lacks_forced_tool_choice",
            return_value=text_fallback,
        ),
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker._store_active_request_context_snapshot",
            AsyncMock(side_effect=_snapshot),
        ),
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker._with_final_request_context_audit",
            side_effect=_audit,
        ),
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker._required_tool_not_called_error",
            return_value="",
        ),
    ):
        result = await invoker._retry_required_tool_if_missing(
            request_preparer=request_preparer,
            executor=executor,
            prepared=prepared,
            profile=profile,
            response=SimpleNamespace(ok=False),
            active_request=prepared.ai_request,
            response_error="required_tool_not_called: required_tools=write_file",
            is_response_ok=False,
            native_tool_fallback=False,
            role_id="director",
            run_id="run-1",
            task_id=None,
            attempt=0,
            model="model-a",
            call_id="call-1",
            event_emitter=None,
        )

    expected_builder = (
        request_preparer._build_required_tool_text_fallback_request
        if text_fallback
        else request_preparer._build_required_tool_retry_request
    )
    expected_builder.assert_called_once()
    request_preparer._reprepare_factory_semantic_retry_request.assert_awaited_once_with(
        prepared=prepared,
        request=changed_request,
        profile=profile,
    )
    assert result[0] is retry_prepared
    assert executor.invoke.await_args.kwargs["physical_dispatch_port"] is retry_prepared.factory_dispatch_port
    assert order == ["reprepare", "snapshot", "audit", "invoke"]


@pytest.mark.asyncio
async def test_b33_structured_native_passes_exact_port_identity() -> None:
    prepared = _b33_propagating_prepared(native_response_format=True)
    executor = SimpleNamespace(
        invoke=AsyncMock(
            return_value=SimpleNamespace(
                ok=False,
                error="unsupported parameter: response_format",
            )
        )
    )
    invoker = LLMInvoker(workspace="/ws", executor=executor)
    request_preparer = SimpleNamespace(_build_structured_fallback_request=Mock(return_value=object()))
    profile = SimpleNamespace(provider_id="provider-a", model="model-a")

    with (
        patch.object(LLMInvoker, "_emit_call_retry_event"),
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker._with_final_request_context_audit",
            side_effect=lambda payload, **_kwargs: payload,
        ),
    ):
        result = await invoker._try_native_response_format_structured(
            request_preparer=request_preparer,
            prepared=prepared,
            profile=profile,
            response_model=dict,
            model="model-a",
            prompt_tokens=1,
            turn_round=0,
            role_id="director",
            run_id="run-1",
            task_id=None,
            attempt=0,
            call_id="call-1",
            event_emitter=None,
            start_time=time.perf_counter(),
        )

    assert result is None
    assert executor.invoke.await_args.kwargs["physical_dispatch_port"] is prepared.factory_dispatch_port
    prepared.factory_dispatch_port.validate_frozen_identity.assert_called_once_with(prepared.factory_semantic_request)


@pytest.mark.asyncio
async def test_b33_structured_instructor_direct_sdk_is_denied_before_client_creation() -> None:
    prepared = _b33_propagating_prepared()
    invoker = LLMInvoker(workspace="/ws")

    with patch("polaris.cells.roles.kernel.internal.llm_caller.invoker.create_structured_client") as create_client:
        result = await invoker._try_instructor_structured(
            prepared=prepared,
            profile=SimpleNamespace(provider_id="provider-a"),
            messages=[],
            response_model=dict,
            model="model-a",
            temperature=0.2,
            max_tokens=128,
            max_retries=3,
            prompt_tokens=1,
            turn_round=0,
            role_id="director",
            run_id="run-1",
            task_id=None,
            attempt=0,
            call_id="call-1",
            event_emitter=None,
            start_time=time.perf_counter(),
        )

    assert result is None
    create_client.assert_not_called()


@pytest.mark.asyncio
async def test_b33_structured_manual_fallback_refreezes_with_new_port() -> None:
    prepared = _b33_propagating_prepared()
    changed_request = object()
    retry_prepared = _b33_propagating_prepared()
    retry_prepared.ai_request = changed_request
    retry_prepared.factory_dispatch_port = Mock()
    order: list[str] = []

    async def _reprepare(**_kwargs):
        order.append("reprepare")
        return retry_prepared

    async def _invoke(*_args, **_kwargs):
        order.append("invoke")
        raise RuntimeError("stop-after-new-port")

    async def _snapshot(**_kwargs):
        order.append("snapshot")

    def _audit(payload, **_kwargs):
        order.append("audit")
        return payload

    executor = SimpleNamespace(invoke=AsyncMock(side_effect=_invoke))
    invoker = LLMInvoker(workspace="/ws", executor=executor)
    request_preparer = SimpleNamespace(
        _build_structured_fallback_request=Mock(return_value=changed_request),
        _reprepare_factory_semantic_retry_request=AsyncMock(side_effect=_reprepare),
    )

    with (
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker._store_active_request_context_snapshot",
            AsyncMock(side_effect=_snapshot),
        ),
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker._with_final_request_context_audit",
            side_effect=_audit,
        ),
        pytest.raises(RuntimeError, match="stop-after-new-port"),
    ):
        await invoker._run_structured_fallback(
            request_preparer=request_preparer,
            prepared=prepared,
            profile=SimpleNamespace(provider_id="provider-a", model="model-a"),
            response_model=dict,
            model="model-a",
            prompt_tokens=1,
            turn_round=0,
            role_id="director",
            run_id="run-1",
            task_id=None,
            attempt=0,
            call_id="call-1",
            event_emitter=None,
            start_time=time.perf_counter(),
        )

    request_preparer._reprepare_factory_semantic_retry_request.assert_awaited_once_with(
        prepared=prepared,
        request=changed_request,
        profile=SimpleNamespace(provider_id="provider-a", model="model-a"),
    )
    assert executor.invoke.await_args.kwargs["physical_dispatch_port"] is retry_prepared.factory_dispatch_port
    assert order == ["reprepare", "snapshot", "audit", "invoke"]


@pytest.mark.asyncio
async def test_b33_role_binding_fallback_refreezes_and_uses_new_exact_port() -> None:
    original_identity = _b32_semantic_identity()
    new_prepared = _b33_propagating_prepared()
    new_prepared.factory_dispatch_port = Mock()
    request_preparer = SimpleNamespace(_prepare_llm_request=AsyncMock(return_value=new_prepared))
    executor = SimpleNamespace(invoke=AsyncMock(return_value=SimpleNamespace(ok=True, error=None)))
    invoker = LLMInvoker(workspace="/ws")
    fallback_profile = SimpleNamespace(provider_id="provider-b", model="model-b")

    with (
        patch.object(LLMInvoker, "_mark_profile_binding_unhealthy"),
        patch.object(
            LLMInvoker,
            "_fallback_slots_for_role",
            return_value=[SimpleNamespace(provider_id="provider-b", model="model-b", binding_id="binding-b")],
        ),
        patch.object(LLMInvoker, "_profile_for_binding", return_value=fallback_profile),
        patch.object(LLMInvoker, "_emit_call_retry_event"),
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker.build_final_request_context_audit_for_request",
            return_value={},
        ),
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker._store_active_request_context_snapshot",
            AsyncMock(),
        ),
        patch("polaris.cells.roles.kernel.internal.llm_caller.invoker.get_role_binding_override", return_value=None),
        patch("polaris.cells.roles.kernel.internal.llm_caller.invoker.get_role_provider_override", return_value=None),
        patch("polaris.cells.roles.kernel.internal.llm_caller.invoker.set_role_binding_override"),
        patch("polaris.cells.roles.kernel.internal.llm_caller.invoker.set_role_provider_override"),
        patch("polaris.cells.roles.kernel.internal.llm_caller.invoker.clear_role_provider_override"),
    ):
        result = await invoker._try_role_binding_fallback(
            request_preparer=request_preparer,
            profile=SimpleNamespace(provider_id="provider-a", model="model-a"),
            system_prompt="system",
            context=SimpleNamespace(),
            temperature=0.2,
            max_tokens=128,
            response_model=None,
            platform_retry_max=0,
            executor=executor,
            role_id="director",
            run_id="run-1",
            task_id=None,
            attempt=0,
            model="model-a",
            call_id="call-1",
            event_emitter=None,
            original_error="429 rate limited",
            factory_semantic_identity=original_identity,
        )

    assert result is not None
    refrozen = request_preparer._prepare_llm_request.await_args.kwargs["factory_semantic_identity"]
    assert refrozen is not original_identity
    assert refrozen.request_freeze_id != original_identity.request_freeze_id
    assert (refrozen.run_id, refrozen.turn_id, refrozen.call_id) == (
        original_identity.run_id,
        original_identity.turn_id,
        original_identity.call_id,
    )
    assert executor.invoke.await_args.kwargs["physical_dispatch_port"] is new_prepared.factory_dispatch_port
    new_prepared.factory_dispatch_port.validate_frozen_identity.assert_called_once_with(
        new_prepared.factory_semantic_request
    )


@pytest.mark.asyncio
async def test_b33_retryable_exception_routes_only_through_refreezing_role_fallback() -> None:
    invoker = LLMInvoker(workspace="/ws")
    identity = _b32_semantic_identity()
    fallback = AsyncMock(return_value=None)

    with patch.object(LLMInvoker, "_try_role_binding_fallback", fallback):
        result = await invoker._try_retryable_exception_role_binding_fallback(
            exc=RuntimeError("429 rate limited"),
            request_preparer=Mock(),
            executor=Mock(),
            prepared=_b33_propagating_prepared(),
            active_request=object(),
            profile=SimpleNamespace(provider_id="provider-a", model="model-a"),
            context=SimpleNamespace(),
            system_prompt="system",
            temperature=0.2,
            effective_max_tokens=128,
            response_model=None,
            platform_retry_max=0,
            model="model-a",
            role_id="director",
            run_id="run-1",
            task_id=None,
            attempt=0,
            turn_round=0,
            call_id="call-1",
            event_emitter=None,
            prompt_tokens=1,
            start_time=time.perf_counter(),
            factory_semantic_identity=identity,
        )

    assert result is None
    assert fallback.await_args.kwargs["factory_semantic_identity"] is identity


@pytest.mark.asyncio
async def test_b33_public_factory_call_zero_transport_stops_before_executor_and_retry_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public Factory calls cannot enter the private dispatch seam until B3.4/B3.5."""

    invoker = LLMInvoker(workspace="/ws")
    profile = _b32_profile()
    prepared = _b32_prepared_factory_request()
    prepare = AsyncMock(return_value=prepared)
    snapshot = AsyncMock()
    executor_invoke = AsyncMock(side_effect=AssertionError("physical executor must not run"))
    get_executor = Mock(return_value=SimpleNamespace(invoke=executor_invoke))
    retry_fallback = AsyncMock(side_effect=AssertionError("retry/fallback must not run"))
    monkeypatch.setattr(
        LLMInvoker,
        "_profile_for_healthy_binding",
        staticmethod(lambda _role, _profile: profile),
    )
    monkeypatch.setattr(LLMInvoker, "_get_executor", lambda _self: get_executor())
    monkeypatch.setattr(LLMInvoker, "_try_retryable_exception_role_binding_fallback", retry_fallback)
    monkeypatch.setattr(LLMInvoker, "_is_cache_eligible", lambda _self, **_kwargs: False)

    with (
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker.LLMRequestPreparer._prepare_llm_request",
            prepare,
        ),
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker._store_call_start_context_snapshot",
            snapshot,
        ),
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker.build_final_request_context_audit_for_request",
            return_value={"final_request_token_estimate": 3},
        ),
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker."
            "enforce_factory_aware_final_request_evidence_coverage"
        ),
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker.classify_error",
            return_value=ERROR_CATEGORY_RATE_LIMIT,
        ),
        bind_factory_role_evidence_authority(_b32_identity_authority()),
    ):
        response = await invoker.call(
            profile=profile,
            system_prompt="You are Director.",
            context=_b32_context(),
            run_id="role-run-zero-transport",
        )

    assert response.error == ("LLM call failed: factory_role_semantic_request_frozen_physical_dispatch_not_enabled")
    prepare.assert_awaited_once()
    snapshot.assert_not_awaited()
    get_executor.assert_not_called()
    executor_invoke.assert_not_awaited()
    retry_fallback.assert_not_awaited()


@pytest.mark.asyncio
async def test_factory_semantic_structured_call_stops_before_all_dispatch_ladders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoker = LLMInvoker(workspace="/ws")
    profile = _b32_profile()
    prepare = AsyncMock(return_value=_b32_prepared_factory_request())
    snapshot = AsyncMock()
    native_dispatch = AsyncMock(side_effect=AssertionError("native structured dispatch must not run"))
    instructor_dispatch = AsyncMock(side_effect=AssertionError("instructor dispatch must not run"))
    fallback_dispatch = AsyncMock(side_effect=AssertionError("structured fallback must not run"))
    monkeypatch.setattr(LLMInvoker, "_try_native_response_format_structured", native_dispatch)
    monkeypatch.setattr(LLMInvoker, "_try_instructor_structured", instructor_dispatch)
    monkeypatch.setattr(LLMInvoker, "_run_structured_fallback", fallback_dispatch)

    with (
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker.LLMRequestPreparer._prepare_llm_request",
            prepare,
        ),
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker._store_call_start_context_snapshot",
            snapshot,
        ),
        bind_factory_role_evidence_authority(_b32_identity_authority()),
    ):
        response = await invoker.call_structured(
            profile=profile,
            system_prompt="You are Director.",
            context=_b32_context(),
            response_model=dict,
            run_id="role-run-structured-zero-transport",
        )

    assert response.error == (
        "Structured LLM call failed: factory_role_semantic_request_frozen_physical_dispatch_not_enabled"
    )
    prepare.assert_awaited_once()
    snapshot.assert_not_awaited()
    native_dispatch.assert_not_awaited()
    instructor_dispatch.assert_not_awaited()
    fallback_dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_factory_semantic_stream_call_stops_before_stream_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoker = LLMInvoker(workspace="/ws")
    profile = _b32_profile()
    prepare = AsyncMock(return_value=_b32_prepared_factory_request())
    stream_dispatch = Mock(side_effect=AssertionError("stream engine must not run"))
    monkeypatch.setattr(invoker, "_stream_engine", SimpleNamespace(run_stream=stream_dispatch))

    with (
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker.LLMRequestPreparer._prepare_llm_request",
            prepare,
        ),
        bind_factory_role_evidence_authority(_b32_identity_authority()),
    ):
        events = [
            event
            async for event in invoker.call_stream(
                profile=profile,
                system_prompt="You are Director.",
                context=_b32_context(),
                run_id="role-run-stream-zero-transport",
            )
        ]

    assert [event["error"] for event in events] == [
        "factory_role_semantic_request_frozen_physical_dispatch_not_enabled"
    ]
    prepare.assert_awaited_once()
    stream_dispatch.assert_not_called()


async def test_factory_call_requires_controlled_child_run_id_before_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoker = LLMInvoker(workspace="/ws")
    profile = _b32_profile()
    monkeypatch.setattr(
        LLMInvoker,
        "_profile_for_healthy_binding",
        staticmethod(lambda _role, _profile: profile),
    )
    prepare = AsyncMock(side_effect=AssertionError("candidate preparation must not run"))

    with (
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker.LLMRequestPreparer._prepare_llm_request",
            prepare,
        ),
        bind_factory_role_evidence_authority(_b32_identity_authority()),
        pytest.raises(RuntimeError, match="factory_role_controlled_run_id_required"),
    ):
        await invoker.call(
            profile=profile,
            system_prompt="You are Director.",
            context=_b32_context(),
            run_id=None,
            turn_round=2,
        )
    prepare.assert_not_awaited()


@pytest.mark.asyncio
async def test_factory_call_mints_full_uuid_identity_and_ignores_context_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoker = LLMInvoker(workspace="/ws")
    profile = _b32_profile()
    monkeypatch.setattr(
        LLMInvoker,
        "_profile_for_healthy_binding",
        staticmethod(lambda _role, _profile: profile),
    )
    prepare = AsyncMock(side_effect=ValueError("stop-after-identity-capture"))

    with (
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker.LLMRequestPreparer._prepare_llm_request",
            prepare,
        ),
        bind_factory_role_evidence_authority(_b32_identity_authority()),
    ):
        await invoker.call(
            profile=profile,
            system_prompt="You are Director.",
            context=_b32_context(),
            run_id="role-run-controlled",
            turn_round=3,
        )

    prepare.assert_awaited_once()
    _assert_invoker_owned_identity(
        prepare.await_args.kwargs["factory_semantic_identity"],
        run_id="role-run-controlled",
        turn_round=3,
    )


@pytest.mark.asyncio
async def test_factory_structured_call_mints_full_uuid_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoker = LLMInvoker(workspace="/ws")
    profile = _b32_profile()
    monkeypatch.setattr(
        LLMInvoker,
        "_profile_for_healthy_binding",
        staticmethod(lambda _role, _profile: profile),
    )
    prepare = AsyncMock(side_effect=ValueError("stop-after-identity-capture"))

    with (
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker.LLMRequestPreparer._prepare_llm_request",
            prepare,
        ),
        bind_factory_role_evidence_authority(_b32_identity_authority()),
        pytest.raises(ValueError, match="stop-after-identity-capture"),
    ):
        await invoker.call_structured(
            profile=profile,
            system_prompt="You are Director.",
            context=_b32_context(),
            response_model=dict,
            run_id="role-run-structured",
            turn_round=4,
        )

    prepare.assert_awaited_once()
    _assert_invoker_owned_identity(
        prepare.await_args.kwargs["factory_semantic_identity"],
        run_id="role-run-structured",
        turn_round=4,
    )


@pytest.mark.asyncio
async def test_factory_stream_call_mints_full_uuid_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoker = LLMInvoker(workspace="/ws")
    profile = _b32_profile()
    monkeypatch.setattr(
        LLMInvoker,
        "_profile_for_healthy_binding",
        staticmethod(lambda _role, _profile: profile),
    )
    prepare = AsyncMock(side_effect=ValueError("stop-after-identity-capture"))

    with (
        patch(
            "polaris.cells.roles.kernel.internal.llm_caller.invoker.LLMRequestPreparer._prepare_llm_request",
            prepare,
        ),
        bind_factory_role_evidence_authority(_b32_identity_authority()),
    ):
        events = [
            event
            async for event in invoker.call_stream(
                profile=profile,
                system_prompt="You are Director.",
                context=_b32_context(),
                run_id="role-run-stream",
                turn_round=5,
            )
        ]

    assert events
    prepare.assert_awaited_once()
    _assert_invoker_owned_identity(
        prepare.await_args.kwargs["factory_semantic_identity"],
        run_id="role-run-stream",
        turn_round=5,
    )


@pytest.mark.asyncio
async def test_role_binding_fallback_preserves_run_turn_call_and_refreezes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity_type = getattr(cutoff_contract, "FactoryRoleSemanticRequestIdentityV1", None)
    assert identity_type is not None, "B3.2 semantic identity contract missing"
    original_identity = identity_type(
        run_id="role-run-fallback",
        turn_id="role-run-fallback:turn:6",
        call_id="a" * 32,
        request_freeze_id="b" * 32,
    )
    invoker = LLMInvoker(workspace="/ws")
    profile = _b32_profile()
    slot = SimpleNamespace(provider_id="provider-b", model="fallback-model", binding_id="binding-b")
    monkeypatch.setattr(LLMInvoker, "_mark_profile_binding_unhealthy", staticmethod(lambda *_args: None))
    monkeypatch.setattr(LLMInvoker, "_fallback_slots_for_role", staticmethod(lambda *_args: (slot,)))
    monkeypatch.setattr(
        LLMInvoker,
        "_profile_for_binding",
        staticmethod(lambda _profile, _slot: profile),
    )
    preparer = SimpleNamespace(
        _prepare_llm_request=AsyncMock(side_effect=ValueError("stop-after-fallback-identity-capture"))
    )

    result = await invoker._try_role_binding_fallback(
        request_preparer=preparer,
        profile=profile,
        system_prompt="You are Director.",
        context=_b32_context(),
        temperature=0.2,
        max_tokens=4000,
        response_model=None,
        platform_retry_max=1,
        executor=Mock(),
        role_id="director",
        run_id="role-run-fallback",
        task_id=None,
        attempt=0,
        model="kimi-for-coding",
        call_id="a" * 32,
        event_emitter=None,
        original_error="rate limit",
        factory_semantic_identity=original_identity,
    )

    assert result is None
    fallback_identity = preparer._prepare_llm_request.await_args.kwargs["factory_semantic_identity"]
    assert fallback_identity.run_id == original_identity.run_id
    assert fallback_identity.turn_id == original_identity.turn_id
    assert fallback_identity.call_id == original_identity.call_id
    assert fallback_identity.request_freeze_id != original_identity.request_freeze_id
    assert len(fallback_identity.request_freeze_id) == 32


def test_final_request_context_audit_counts_tools_and_coverage() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    tool_schema = {
        "type": "function",
        "function": {
            "name": "write_file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        },
    }
    messages = [
        {
            "role": "system",
            "content": (
                "Chief Engineer blueprint with construction_plan and scope_for_apply. "
                "Resident AGI 决策交接 schema_version: resident.agi_decision_trace_signal.v1 "
                "source_of_truth: workspace/meta/resident/decision_trace.jsonl"
            ),
        },
        {
            "role": "user",
            "content": (
                "TASK-1 target_files src/index.ts tests/verify.test.ts retry after stderr exit_code failure "
                "Resident AGI 能力面 schema_version: resident.agi_capability_surface.v1 "
                "runtime_foundation: roles.runtime + ContextOS + TransactionKernel "
                "decision_boundary_schema: resident.agi_decision_boundary.v1 "
                "platform_hard_rule agi_decision_scope agi_governed_execution"
            ),
        },
    ]
    ai_request = Mock()
    ai_request.context = {
        "chat_messages": messages,
        "pm_contract": {
            "schema_version": "pm.task_contract.v1",
            "task_id": "TASK-1",
            "target_files": ["src/index.ts"],
            "acceptance": ["npm test"],
        },
        "chief_engineer_blueprint": {
            "schema_version": "chief_engineer.blueprint.v1",
            "blueprint_id": "ce_TASK-1",
            "target_files": ["src/index.ts"],
            "construction_plan": {"phase": "implement"},
        },
        "target_files": ["src/index.ts"],
        "scope_paths": ["src/index.ts"],
        "failed_gate_evidence": {
            "schema_version": "polaris.failed_gate_evidence.v1",
            "source": "run_ledger.verifier",
            "command": "npm test",
            "exit_code": 1,
            "diagnostics": [{"code": "E_ASSERT", "path": "tests/verify.test.ts"}],
        },
        "resident_agi_audit_context": {
            "schema_version": "resident.agi_audit_context.v1",
            "enabled": True,
            "participation": {"final_request_audit": True},
            "participation_scopes": ["final_request_audit"],
            "audit_pack_schema_version": "resident.agi_audit_pack.v1",
            "decision_contract_schema_version": "resident.agi_decision_contract.v1",
            "capability_surface_schema_version": "resident.agi_capability_surface.v1",
            "decision_capability_registry_schema_version": "resident.agi_decision_capability_registry.v1",
            "decision_boundary_schema": "resident.agi_decision_boundary.v1",
            "decision_boundary_count": 3,
        },
    }
    ai_request.options = {"tools": [tool_schema]}
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={"tools": [tool_schema]},
        ai_request=ai_request,
        native_tool_schemas=[],
    )

    audit = build_final_request_context_audit(prepared=prepared, profile=profile)

    assert audit["schema_version"] == "llm.final_request_context_audit.v1"
    assert audit["message_count"] == 2
    assert audit["tool_schema_count"] == 1
    assert audit["tool_schema_token_estimate"] > 0
    assert audit["final_request_token_estimate"] > audit["message_token_estimate"]
    assert audit["context_window_tokens"] == 32768
    assert audit["context_underutilized"] is True
    assert audit["coverage"]["has_chief_engineer_blueprint"] is True
    assert audit["coverage"]["has_pm_contract"] is True
    assert audit["coverage"]["has_target_files"] is True
    assert audit["coverage"]["has_failure_feedback"] is True
    assert audit["coverage"]["has_resident_agi_decision_trace"] is True
    assert audit["coverage"]["has_resident_agi_capability_surface"] is True
    assert audit["coverage"]["has_resident_agi_decision_boundary"] is True
    assert audit["available_token_headroom"] > 0
    assert "has_workspace_quality_evidence" in audit["context_quality"]["missing_coverage"]
    assert audit["context_quality"]["context_needs_review"] is True


def test_final_request_context_audit_requires_structured_failure_feedback() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    messages = [
        {
            "role": "user",
            "content": "stderr exit_code failed retry error quality errors: artifact quality",
        },
    ]
    ai_request = Mock()
    ai_request.context = {
        "chat_messages": messages,
        "target_files": ["src/index.ts"],
        "scope_paths": ["src/index.ts"],
    }
    ai_request.options = {"tools": []}
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={"tools": []},
        ai_request=ai_request,
        native_tool_schemas=[],
    )

    audit = build_final_request_context_audit(prepared=prepared, profile=profile)

    assert audit["coverage"]["has_failure_feedback"] is False
    assert "has_failure_feedback" in audit["context_quality"]["missing_coverage"]


def test_final_request_context_audit_requires_structured_workspace_quality_evidence() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    messages = [
        {
            "role": "user",
            "content": (
                "factory_workspace_quality workspace quality npm test step verify failed "
                "quality errors: artifact quality real_run_gate"
            ),
        },
    ]
    ai_request = Mock()
    ai_request.context = {
        "chat_messages": messages,
        "target_files": ["src/index.ts"],
        "scope_paths": ["src/index.ts"],
    }
    ai_request.options = {"tools": []}
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={"tools": []},
        ai_request=ai_request,
        native_tool_schemas=[],
    )

    audit = build_final_request_context_audit(prepared=prepared, profile=profile)

    assert audit["coverage"]["has_workspace_quality_evidence"] is False
    assert "has_workspace_quality_evidence" in audit["context_quality"]["missing_coverage"]


def test_final_request_context_audit_accepts_metadata_failure_feedback() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    messages = [{"role": "user", "content": "continue after failed gate"}]
    ai_request = Mock()
    ai_request.context = {
        "chat_messages": messages,
        "target_files": ["src/index.ts"],
        "scope_paths": ["src/index.ts"],
    }
    ai_request.metadata = {
        "failure_feedback": {
            "schema_version": "polaris.failure_evidence.context_slot.v1",
            "failure_class": "DEPENDENCY_NOT_UNLOCKED",
            "responsible_layer": "execution_control_plane",
            "evidence_refs": ["task-boundary:run-1:TASK-2"],
        }
    }
    ai_request.options = {"tools": []}
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={"tools": []},
        ai_request=ai_request,
        native_tool_schemas=[],
    )

    audit = build_final_request_context_audit(prepared=prepared, profile=profile)

    assert audit["coverage"]["has_failure_feedback"] is True
    assert "has_failure_feedback" not in audit["context_quality"]["missing_coverage"]


def test_final_request_context_audit_accepts_metadata_workspace_quality_evidence() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    messages = [{"role": "user", "content": "continue after workspace quality failure"}]
    ai_request = Mock()
    ai_request.context = {
        "chat_messages": messages,
        "target_files": ["src/index.ts"],
        "scope_paths": ["src/index.ts"],
    }
    ai_request.metadata = {
        "workspace_quality_evidence": {
            "schema_version": "polaris.workspace_quality_evidence.v1",
            "all_checks_passed": False,
            "quality_errors": ["npm test passed with 0 tests"],
            "deterministic_checks": ["package_scripts", "source_target_coverage"],
        }
    }
    ai_request.options = {"tools": []}
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={"tools": []},
        ai_request=ai_request,
        native_tool_schemas=[],
    )

    audit = build_final_request_context_audit(prepared=prepared, profile=profile)

    assert audit["coverage"]["has_workspace_quality_evidence"] is True
    assert "has_workspace_quality_evidence" not in audit["context_quality"]["missing_coverage"]


def test_final_request_context_audit_does_not_count_degraded_blueprint_fallback() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    messages = [
        {
            "role": "system",
            "content": (
                "【蓝图/技术架构（降级）】\n"
                "无 CE 蓝图可用。基于任务描述和项目结构推断。\n"
                "注意: 此为降级推断，非 CE 权威蓝图。"
            ),
        },
        {
            "role": "user",
            "content": (
                "TASK-1 target_files src/web.ts acceptance npm run build failed "
                "stderr src/web.ts(63,20): error TS2345 workspace quality"
            ),
        },
    ]
    ai_request = Mock()
    ai_request.context = {
        "chat_messages": messages,
        "pm_contract": {
            "schema_version": "pm.task_contract.v1",
            "task_id": "TASK-1",
            "target_files": ["src/engine/SimulationEngine.ts"],
            "acceptance": ["npm run build"],
        },
        "target_files": ["src/engine/SimulationEngine.ts"],
        "scope_paths": ["src/engine/SimulationEngine.ts"],
        "workspace_quality_evidence": {
            "schema_version": "polaris.workspace_quality_evidence.v1",
            "source": "factory_workspace_quality",
            "all_checks_passed": False,
            "quality_errors": [{"code": "build_failed"}],
        },
    }
    ai_request.options = {}
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={},
        ai_request=ai_request,
        native_tool_schemas=[],
    )

    audit = build_final_request_context_audit(prepared=prepared, profile=profile)

    assert audit["coverage"]["has_pm_contract"] is True
    assert audit["coverage"]["has_chief_engineer_blueprint"] is False
    assert "has_chief_engineer_blueprint" in audit["context_quality"]["missing_coverage"]


