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




def test_final_provider_request_snapshot_summarizes_tools_and_choice() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    profile.role_id = "director"
    profile.provider_id = "qwen-director"
    profile.model = "qwen3.6-27b-q6-code-gpu1"
    tool_schema = {
        "type": "function",
        "function": {
            "name": "repo_tree",
            "description": "List repository files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "depth": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    }
    messages = [{"role": "user", "content": "TASK-1 target_files src/index.ts Chief Engineer blueprint"}]
    ai_request = Mock()
    ai_request.role = "director"
    ai_request.provider_id = "qwen-director"
    ai_request.model = "qwen3.6-27b-q6-code-gpu1"
    ai_request.context = {
        "chat_messages": messages,
        "prompt_profile_audit": {
            "selected_prompt_profile_ids": ["builtin.language.typescript", "builtin.task.implement"],
            "inferred_language": "typescript",
            "inferred_task_type": "implement",
            "redline_clipped": [],
        },
        "selected_prompt_profile_ids": ["builtin.language.typescript", "builtin.task.implement"],
    }
    ai_request.options = {"tools": [tool_schema], "tool_choice": "auto"}
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={"tools": [tool_schema], "tool_choice": "auto"},
        ai_request=ai_request,
        native_tool_schemas=[],
    )

    snapshot = build_final_provider_request_snapshot(ai_request=ai_request, prepared=prepared, profile=profile)

    assert snapshot["schema_version"] == "llm.provider_request_snapshot.v1"
    assert snapshot["source"] == "roles.kernel.llm_caller.context_audit"
    assert snapshot["role"] == "director"
    assert snapshot["provider_id"] == "qwen-director"
    assert snapshot["model"] == "qwen3.6-27b-q6-code-gpu1"
    assert snapshot["message_count"] == 1
    assert snapshot["tool_schema_count"] == 1
    assert snapshot["tool_choice"] == "auto"
    assert snapshot["selected_prompt_profile_ids"] == ["builtin.language.typescript", "builtin.task.implement"]
    assert snapshot["prompt_profile_selection"]["inferred_language"] == "typescript"
    assert snapshot["tools"] == [
        {
            "type": "function",
            "name": "repo_tree",
            "argument_keys": ["depth", "path"],
            "required": ["path"],
        }
    ]
    assert snapshot["final_request_context_audit"]["tool_schema_count"] == 1
    assert snapshot["final_request_context_audit"]["selected_prompt_profile_ids"] == [
        "builtin.language.typescript",
        "builtin.task.implement",
    ]


def test_required_tool_not_called_error_when_final_request_requires_tool_and_response_is_prose() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    profile.role_id = "director"
    profile.provider_id = "openai"
    profile.model = "gpt-4.1"
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
    messages = [{"role": "user", "content": "TASK-1 target_files package.json Chief Engineer blueprint"}]
    ai_request = Mock()
    ai_request.context = {
        "chat_messages": messages,
        "required_tools": ["write_file"],
        "tool_contract": {"required_tools": ["write_file"]},
    }
    ai_request.options = {"tools": [tool_schema], "tool_choice": "auto"}
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={"tools": [tool_schema], "tool_choice": "auto"},
        ai_request=ai_request,
        native_tool_schemas=[tool_schema],
    )
    response = SimpleNamespace(
        raw={"model": "gpt-4.1", "provider_id": "openai"},
        output="I will inspect the workspace first.",
        model="gpt-4.1",
        provider_id="openai",
    )

    error = _required_tool_not_called_error(
        prepared=prepared,
        active_request=ai_request,
        response=response,
        profile=profile,
    )

    assert error == "required_tool_not_called: required_tools=write_file"


def test_minimax_uses_required_tool_text_fallback_after_missing_tool_call() -> None:
    minimax_profile = SimpleNamespace(
        provider_id="anthropic_compat-1782212251463",
        model="MiniMax-M3",
        provider_type="anthropic_compat",
        name="Director MiniMax",
    )
    openai_profile = SimpleNamespace(
        provider_id="openai-main",
        model="gpt-4.1",
        provider_type="openai",
        name="Director OpenAI",
    )

    assert _profile_lacks_forced_tool_choice(minimax_profile) is True
    assert _profile_lacks_forced_tool_choice(openai_profile) is False


def test_required_tool_not_called_error_allows_native_tool_call() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    profile.role_id = "director"
    profile.provider_id = "openai"
    profile.model = "gpt-4.1"
    tool_schema = {"type": "function", "function": {"name": "write_file"}}
    messages = [{"role": "user", "content": "TASK-1 target_files package.json Chief Engineer blueprint"}]
    ai_request = Mock()
    ai_request.context = {
        "chat_messages": messages,
        "required_tools": ["write_file"],
        "tool_contract": {"required_tools": ["write_file"]},
    }
    ai_request.options = {"tools": [tool_schema], "tool_choice": "auto"}
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={"tools": [tool_schema], "tool_choice": "auto"},
        ai_request=ai_request,
        native_tool_schemas=[tool_schema],
    )
    response = SimpleNamespace(
        raw={
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_write",
                                "type": "function",
                                "function": {
                                    "name": "write_file",
                                    "arguments": '{"path": "package.json", "content": "{}"}',
                                },
                            }
                        ]
                    }
                }
            ],
            "model": "gpt-4.1",
            "provider_id": "openai",
        },
        output="",
        model="gpt-4.1",
        provider_id="openai",
    )

    error = _required_tool_not_called_error(
        prepared=prepared,
        active_request=ai_request,
        response=response,
        profile=profile,
    )

    assert error == ""


def test_required_tool_not_called_error_allows_native_tool_name_alias() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    profile.role_id = "director"
    profile.provider_id = "openai"
    profile.model = "gpt-4.1"
    tool_schema = {"type": "function", "function": {"name": "write_file"}}
    messages = [{"role": "user", "content": "TASK-1 target_files package.json Chief Engineer blueprint"}]
    ai_request = Mock()
    ai_request.context = {
        "chat_messages": messages,
        "required_tools": ["write_file"],
        "tool_contract": {"required_tools": ["write_file"]},
    }
    ai_request.options = {"tools": [tool_schema], "tool_choice": "auto"}
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={"tools": [tool_schema], "tool_choice": "auto"},
        ai_request=ai_request,
        native_tool_schemas=[tool_schema],
    )
    response = SimpleNamespace(
        raw={
            "tool_calls": [
                {
                    "id": "call_write",
                    "type": "function",
                    "tool_name": "write_file",
                    "arguments": {"path": "package.json", "content": "{}"},
                }
            ],
            "model": "gpt-4.1",
            "provider_id": "openai",
        },
        output="",
        model="gpt-4.1",
        provider_id="openai",
    )

    error = _required_tool_not_called_error(
        prepared=prepared,
        active_request=ai_request,
        response=response,
        profile=profile,
    )

    assert error == ""


def test_required_tool_not_called_error_rejects_wrong_native_tool_call() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    profile.role_id = "director"
    profile.provider_id = "openai"
    profile.model = "gpt-4.1"
    tool_schema = {"type": "function", "function": {"name": "write_file"}}
    messages = [{"role": "user", "content": "TASK-1 target_files package.json Chief Engineer blueprint"}]
    ai_request = Mock()
    ai_request.context = {
        "chat_messages": messages,
        "required_tools": ["write_file"],
        "tool_contract": {"required_tools": ["write_file"]},
    }
    ai_request.options = {"tools": [tool_schema], "tool_choice": "auto"}
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={"tools": [tool_schema], "tool_choice": "auto"},
        ai_request=ai_request,
        native_tool_schemas=[tool_schema],
    )
    response = SimpleNamespace(
        raw={
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_read",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path": "package.json"}',
                                },
                            }
                        ]
                    }
                }
            ],
            "model": "gpt-4.1",
            "provider_id": "openai",
        },
        output="",
        model="gpt-4.1",
        provider_id="openai",
    )

    error = _required_tool_not_called_error(
        prepared=prepared,
        active_request=ai_request,
        response=response,
        profile=profile,
    )

    assert error == "required_tool_not_called: required_tools=write_file"


def test_required_tool_not_called_error_does_not_fire_on_zero_tool_request() -> None:
    """Regression: stale required_tools from the shared turn context must not
    fire the required_tool_not_called retry on a request that physically cannot
    call tools (finalization: zero tool schemas, tool_choice=none)."""

    profile = Mock()
    profile.max_context_tokens = 32768
    profile.role_id = "director"
    profile.provider_id = "openai"
    profile.model = "gpt-4.1"
    messages = [{"role": "user", "content": "TASK-1 target_files package.json Chief Engineer blueprint"}]
    stale_context = {
        "chat_messages": messages,
        "required_tools": ["write_file"],
        "tool_contract": {"required_tools": ["write_file"]},
    }
    response = SimpleNamespace(
        raw={"model": "gpt-4.1", "provider_id": "openai"},
        output="Final summary of the completed write.",
        model="gpt-4.1",
        provider_id="openai",
    )

    # Explicit tool_choice=none finalization request.
    ai_request, prepared = _zero_tool_prepared_request(
        {"tools": [], "tool_choice": "none"},
        dict(stale_context),
        messages,
    )
    assert (
        _required_tool_not_called_error(
            prepared=prepared,
            active_request=ai_request,
            response=response,
            profile=profile,
        )
        == ""
    )

    # Request whose provider options carry no tool surface at all.
    ai_request, prepared = _zero_tool_prepared_request(
        {"temperature": 0.2, "max_tokens": 2000},
        dict(stale_context),
        messages,
    )
    assert (
        _required_tool_not_called_error(
            prepared=prepared,
            active_request=ai_request,
            response=response,
            profile=profile,
        )
        == ""
    )


def test_final_request_coverage_passes_for_finalization_request_after_forced_write_turn() -> None:
    """Regression: a same-turn finalization-style request (zero tool schemas,
    tool_choice=none) must pass evidence coverage instead of reporting
    missing_required_tools for tools that are not exposed by design."""

    profile = Mock()
    profile.max_context_tokens = 32768
    profile.role_id = "director"
    messages = [
        {
            "role": "user",
            "content": (
                "TASK-1 acceptance criteria target_files src/index.js "
                "Chief Engineer blueprint construction_plan scope_for_apply "
                "stderr exit_code failed retry factory_workspace_quality npm run build "
                "public_symbols: createEntrypoint consumes_symbols: src/index.js"
            ),
        },
    ]
    ai_request = Mock()
    ai_request.role = "director"
    # Stale turn-context contamination: required_tools survived into this call.
    ai_request.context = {
        **_minimal_director_evidence_context(),
        "chat_messages": messages,
        "required_tools": ["write_file"],
        "tool_contract": {"required_tools": ["write_file"]},
        "_transaction_kernel_forced_tool_definitions": [],
        "_transaction_kernel_forced_tool_choice": "none",
    }
    ai_request.options = {"tools": [], "tool_choice": "none"}
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={"tools": [], "tool_choice": "none"},
        ai_request=ai_request,
        native_tool_schemas=[],
    )

    audit = build_final_request_context_audit_for_request(
        ai_request=ai_request,
        prepared=prepared,
        profile=SimpleNamespace(role_id="director", max_context_tokens=32768),
    )

    coverage = audit["final_request_evidence_coverage"]
    assert coverage["required_tools"] == []
    assert coverage["missing_required_tools"] == []
    assert coverage["tool_surface"]["required_tools_exempt"] == ["write_file"]
    assert coverage["tool_surface"]["required_tools_exempt_reason"] == "tool_choice_disabled_by_design"
    assert coverage["pass"] is True
    finding_codes = {item["code"] for item in audit["context_quality"]["findings"]}
    assert audit["context_quality"]["context_needs_review"] is False
    assert audit["context_quality"]["missing_coverage"] == []
    assert "missing_context_coverage" not in finding_codes
    assert "underutilized_with_missing_context" not in finding_codes
    assert "missing_required_final_request_tools" not in finding_codes


def test_final_request_context_audit_marks_complete_context_as_reasonable() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    messages = [
        {
            "role": "user",
            "content": (
                "TASK-1 acceptance criteria target_files src/index.ts "
                "Chief Engineer blueprint construction_plan scope_for_apply "
                "stderr exit_code failed retry factory_workspace_quality npm run build "
                "resident_agi_decision_trace resident.agi_decision_trace_signal.v1 "
                "workspace/meta/resident/decision_trace.jsonl "
                "resident_agi_capability_surface resident.agi_capability_surface.v1 "
                "runtime_foundation: roles.runtime + ContextOS + TransactionKernel "
                "resident.agi_decision_boundary.v1 decision_boundaries platform_hard_rule agi_decision_scope "
                "public_symbols: buildPlanetWeatherReport consumes_symbols: src/models/weather.ts"
            ),
        },
    ]
    ai_request = Mock()
    ai_request.context = {**_minimal_director_evidence_context(), "chat_messages": messages}
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

    assert audit["context_underutilized"] is True
    assert audit["context_quality"]["missing_coverage"] == []
    assert audit["context_quality"]["context_needs_review"] is False
    finding_codes = {item["code"] for item in audit["context_quality"]["findings"]}
    assert "missing_context_coverage" not in finding_codes
    assert "underutilized_with_missing_context" not in finding_codes


def test_final_request_context_audit_skips_resident_agi_coverage_when_disabled() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    messages = [
        {
            "role": "user",
            "content": (
                "TASK-1 acceptance criteria target_files src/index.ts "
                "Chief Engineer blueprint construction_plan scope_for_apply "
                "stderr exit_code failed retry factory_workspace_quality npm run build"
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
        "chief_engineer_blueprint": {
            "schema_version": "chief_engineer.blueprint.v1",
            "blueprint_id": "bp-L1-01-4",
            "target_files": ["src/engine/SimulationEngine.ts"],
            "construction_plan": {"phase": "implement"},
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

    assert "has_resident_agi_decision_trace" not in audit["coverage"]
    assert "has_resident_agi_capability_surface" not in audit["coverage"]
    assert "has_resident_agi_decision_boundary" not in audit["coverage"]
    assert "has_resident_agi_decision_trace" not in audit["context_quality"]["missing_coverage"]
    assert "has_resident_agi_capability_surface" not in audit["context_quality"]["missing_coverage"]
    assert "has_resident_agi_decision_boundary" not in audit["context_quality"]["missing_coverage"]


def test_final_request_context_audit_reports_missing_resident_agi_when_participation_enabled() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    messages = [
        {
            "role": "user",
            "content": (
                "TASK-1 acceptance criteria target_files src/index.ts "
                "Chief Engineer blueprint construction_plan scope_for_apply "
                "stderr exit_code failed retry factory_workspace_quality npm run build"
            ),
        },
    ]
    ai_request = Mock()
    ai_request.context = {
        "chat_messages": messages,
        "resident_agi_audit_context": {
            "schema_version": "resident.agi_audit_context.v1",
            "enabled": True,
            "participation": {"final_request_audit": True},
            "participation_scopes": ["final_request_audit"],
        },
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

    assert audit["coverage"]["has_resident_agi_decision_trace"] is False
    assert audit["coverage"]["has_resident_agi_capability_surface"] is False
    assert audit["coverage"]["has_resident_agi_decision_boundary"] is False
    assert "has_resident_agi_decision_trace" in audit["context_quality"]["missing_coverage"]
    assert "has_resident_agi_capability_surface" in audit["context_quality"]["missing_coverage"]
    assert "has_resident_agi_decision_boundary" in audit["context_quality"]["missing_coverage"]


def test_final_request_context_audit_requires_structured_resident_agi_context() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    messages = [
        {
            "role": "user",
            "content": (
                "resident_agi_decision_trace resident.agi_decision_trace_signal.v1 "
                "workspace/meta/resident/decision_trace.jsonl "
                "resident_agi_capability_surface resident.agi_capability_surface.v1 "
                "runtime_foundation: roles.runtime + ContextOS + TurnEngine "
                "resident.agi_decision_boundary.v1 decision_boundaries platform_hard_rule "
                "agi_decision_scope agi_governed_execution"
            ),
        },
    ]
    ai_request = Mock()
    ai_request.context = {
        "chat_messages": messages,
        "resident_agi_audit_context": {
            "schema_version": "resident.agi_audit_context.v1",
            "enabled": True,
            "participation": {"final_request_audit": True},
            "participation_scopes": ["final_request_audit"],
        },
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

    assert audit["coverage"]["has_resident_agi_decision_trace"] is False
    assert audit["coverage"]["has_resident_agi_capability_surface"] is False
    assert audit["coverage"]["has_resident_agi_decision_boundary"] is False
    assert "has_resident_agi_decision_trace" in audit["context_quality"]["missing_coverage"]
    assert "has_resident_agi_capability_surface" in audit["context_quality"]["missing_coverage"]
    assert "has_resident_agi_decision_boundary" in audit["context_quality"]["missing_coverage"]


def test_final_request_context_audit_reads_structured_resident_agi_context() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    messages = [
        {
            "role": "user",
            "content": (
                "TASK-1 acceptance criteria target_files src/index.ts "
                "Chief Engineer blueprint construction_plan scope_for_apply "
                "stderr exit_code failed retry factory_workspace_quality npm run build"
            ),
        },
    ]
    ai_request = Mock()
    ai_request.context = {
        "chat_messages": messages,
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

    assert audit["coverage"]["has_resident_agi_decision_trace"] is True
    assert audit["coverage"]["has_resident_agi_capability_surface"] is True
    assert audit["coverage"]["has_resident_agi_decision_boundary"] is True
    assert audit["request_metadata_summary"]["has_resident_agi_audit_context"] is True
    assert audit["request_metadata_summary"]["resident_agi_audit_context"]["enabled"] is True


def test_llm_caller_keeps_current_user_instruction_as_final_message() -> None:
    messages = [
        {"role": "system", "content": "Role contract."},
        {"role": "user", "content": "Implement the task."},
        {"role": "system", "content": "Projected context appended late."},
    ]

    normalized = _ensure_current_user_message_final(messages, "Implement the task.")

    assert normalized[-1] == {"role": "user", "content": "Implement the task."}
    assert normalized[1]["role"] == "system"


def test_llm_caller_does_not_shrink_director_message_with_sibling_exports() -> None:
    """R143: short context.message must not strip actual_sibling_exports bodies.

    Live r142c TASK-2 failed with missing_required_refs=actual_sibling_exports
    after substring match replaced the full director turn with a short token.
    """

    rich_director_turn = (
        "任务: 实现 发光昆虫花园模拟器 模拟流程\n"
        "已提交父任务的真实依赖产物 / Committed parent-task dependency artifacts:\n"
        "polaris.actual_sibling_exports.evidence.v2 snapshot_sha256=" + ("ab" * 32) + "\n"
        "--- parent_task_id=1 receipt_id=director-physical-effect-abc path=src/models/Firefly.ts "
        "sha256=" + ("cd" * 32) + " ---\n"
        "export class Firefly {}\n"
        "禁止输出 TODO/FIXME/NotImplemented 等占位实现。\n"
    )
    short_instruction = "实现 发光昆虫花园模拟器 模拟流程"
    assert short_instruction in rich_director_turn

    messages = [
        {"role": "system", "content": "Director role contract."},
        {"role": "user", "content": rich_director_turn},
        {"role": "system", "content": "Late projected context."},
    ]
    normalized = _ensure_current_user_message_final(messages, short_instruction)

    assert normalized[-1]["role"] == "user"
    assert normalized[-1]["content"] == rich_director_turn
    assert "polaris.actual_sibling_exports.evidence.v2 snapshot_sha256=" in normalized[-1]["content"]
    assert "export class Firefly {}" in normalized[-1]["content"]


def test_r150_re_pins_actual_sibling_exports_after_tool_loop_history() -> None:
    """R150: multi-turn tool history must not drop sibling-export message binding.

    Live r149 TASK-2: first LLM call had has_actual_sibling_exports=true; after
    write tools, follow-up coverage failed closed with
    missing_required_refs=actual_sibling_exports while structured payload still
    lived in context_override.
    """
    import hashlib
    import json

    from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
        _actual_sibling_exports_message_bound,
        _looks_like_actual_sibling_exports,
    )
    from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import (
        _ensure_actual_sibling_exports_message_bound,
    )

    body = "export class Firefly { glow(): number { return 1; } }\n"
    body_bytes = body.encode("utf-8")
    module = {
        "parent_task_id": "1",
        "parent_runtime_task_id": "1",
        "parent_external_task_id": "TASK-1",
        "source_fact_ref": "task_runtime.observable_task:1",
        "source_fact_hash": "a" * 64,
        "effect_receipt_id": "director-physical-effect-abc",
        "effect_receipt_hash": "b" * 64,
        "effect_receipt_binding_hash": "c" * 64,
        "physical_result_hash": "d" * 64,
        "target_state_hash": "e" * 64,
        "path": "src/models/Firefly.ts",
        "sha256": hashlib.sha256(body_bytes).hexdigest(),
        "byte_count": len(body_bytes),
        "body": body,
        "guarded_snapshot": {
            "device": 1,
            "inode": 2,
            "mtime_ns": 3,
            "ctime_ns": 4,
            "root_device": 5,
            "root_inode": 6,
        },
    }
    payload: dict[str, object] = {
        "schema_version": "polaris.actual_sibling_exports.evidence.v2",
        "source": "roles.adapters.director.task_runtime_dependency_artifact_snapshot",
        "dependency_task_ids": ["1"],
        "covered_parent_task_ids": ["1"],
        "modules": [module],
        "module_count": 1,
        "total_byte_count": len(body_bytes),
    }
    payload["snapshot_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert _looks_like_actual_sibling_exports(payload, messages=None)

    # Tool-loop history without the original rich user turn (bodies dropped).
    tool_loop_messages = [
        {"role": "system", "content": "Director role contract."},
        {"role": "user", "content": "实现 发光昆虫花园模拟器 模拟流程"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "write_file"}}]},
        {"role": "tool", "content": '{"ok": true, "path": "src/engine/simulation.ts"}'},
    ]
    assert not _actual_sibling_exports_message_bound(payload, tool_loop_messages)

    rebound = _ensure_actual_sibling_exports_message_bound(
        tool_loop_messages,
        {"actual_sibling_exports": payload},
    )
    assert _actual_sibling_exports_message_bound(payload, rebound)
    # R152: pin among leading system messages; trailing role must stay the
    # tool/user turn so ContextOS current_user_final stays true.
    assert rebound[0]["role"] == "system"
    assert rebound[1]["role"] == "system"
    assert f"snapshot_sha256={payload['snapshot_sha256']}" in rebound[1]["content"]
    assert body in rebound[1]["content"]
    assert rebound[-1]["role"] == "tool"
    assert [m["role"] for m in rebound] == [
        "system",
        "system",
        "user",
        "assistant",
        "tool",
    ]
    # Already-bound messages must not grow another pin.
    again = _ensure_actual_sibling_exports_message_bound(rebound, {"actual_sibling_exports": payload})
    assert again == rebound


def test_zero_artifact_parent_snapshot_counts_as_actual_sibling_exports() -> None:
    """Live L2-13: TASK-1 sealed as zero-artifact parent (modules=[]).

    Quality-repair coverage required actual_sibling_exports, then rejected the
    honest empty snapshot because ``_looks_like`` demanded a non-empty module
    list. LLM never started despite factory_run deadline remaining.
    """
    import hashlib
    import json

    from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
        _looks_like_actual_sibling_exports,
    )
    from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import (
        _ensure_actual_sibling_exports_message_bound,
    )

    payload: dict[str, object] = {
        "schema_version": "polaris.actual_sibling_exports.evidence.v2",
        "source": "roles.adapters.director.task_runtime_dependency_artifact_snapshot",
        "dependency_task_ids": ["TASK-1"],
        "covered_parent_task_ids": ["TASK-1"],
        "zero_artifact_parent_task_ids": ["TASK-1"],
        "modules": [],
        "module_count": 0,
        "total_byte_count": 0,
        "receipt_coverage_complete": True,
        "uncovered_artifacts": [],
    }
    payload["snapshot_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    assert _looks_like_actual_sibling_exports(payload, messages=None) is True

    messages = [
        {"role": "system", "content": "Director role contract."},
        {"role": "user", "content": "Repair go stack overflow in engine/service.go"},
    ]
    rebound = _ensure_actual_sibling_exports_message_bound(
        messages,
        {"actual_sibling_exports": payload},
    )
    assert _looks_like_actual_sibling_exports(payload, messages=rebound) is True
    assert f"snapshot_sha256={payload['snapshot_sha256']}" in rebound[1]["content"]


@pytest.mark.module_final_request_context
def test_r152_sibling_export_pin_preserves_current_user_final_role() -> None:
    """R152: re-pin must not make final_role=system after tool-loop history.

    Live r151: after successful writes, follow-up qualify failed with
    final_request_context_quality_failed because context_os_prompt_audit had
    current_user_final=false (final_role=system from trailing R150 pin).
    """
    import hashlib
    import json

    from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
        _actual_sibling_exports_message_bound,
    )
    from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import (
        _ensure_actual_sibling_exports_message_bound,
    )
    from polaris.kernelone.audit.context_os_prompt import audit_context_os_prompt_messages

    body = "export class Flower {}\n"
    body_bytes = body.encode("utf-8")
    module = {
        "parent_task_id": "1",
        "parent_runtime_task_id": "1",
        "parent_external_task_id": "TASK-1",
        "source_fact_ref": "task_runtime.observable_task:1",
        "source_fact_hash": "a" * 64,
        "effect_receipt_id": "director-physical-effect-abc",
        "effect_receipt_hash": "b" * 64,
        "effect_receipt_binding_hash": "c" * 64,
        "physical_result_hash": "d" * 64,
        "target_state_hash": "e" * 64,
        "path": "src/models/Flower.ts",
        "sha256": hashlib.sha256(body_bytes).hexdigest(),
        "byte_count": len(body_bytes),
        "body": body,
        "guarded_snapshot": {
            "device": 1,
            "inode": 2,
            "mtime_ns": 3,
            "ctime_ns": 4,
            "root_device": 5,
            "root_inode": 6,
        },
    }
    payload: dict[str, object] = {
        "schema_version": "polaris.actual_sibling_exports.evidence.v2",
        "source": "roles.adapters.director.task_runtime_dependency_artifact_snapshot",
        "dependency_task_ids": ["1"],
        "covered_parent_task_ids": ["1"],
        "modules": [module],
        "module_count": 1,
        "total_byte_count": len(body_bytes),
    }
    payload["snapshot_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    user_instruction = "实现 发光昆虫花园模拟器 模拟流程与 Web 入口"
    history = [
        {"role": "system", "content": "Director role contract."},
        {"role": "user", "content": user_instruction},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "write_file"}}]},
        {"role": "tool", "content": '{"ok": true}'},
        {"role": "user", "content": user_instruction},
    ]
    rebound = _ensure_actual_sibling_exports_message_bound(
        history,
        {"actual_sibling_exports": payload},
    )
    assert _actual_sibling_exports_message_bound(payload, rebound)
    assert rebound[-1]["role"] == "user"
    assert rebound[-1]["content"] == user_instruction
    audit = audit_context_os_prompt_messages(
        messages=rebound,
        expected=True,
        current_user_instruction=user_instruction,
        context_sources=["state_first_context_os"],
        metadata={"state_first_mode_active": True},
    )
    assert audit["ok"] is True
    assert audit["final_role"] == "user"
    assert audit["requirements"]["current_user_final"] is True
    assert audit["control_plane"]["isolated"] is True


def test_llm_caller_restores_missing_current_user_instruction_at_tail() -> None:
    messages = [{"role": "system", "content": "Projected context only."}]

    normalized = _ensure_current_user_message_final(messages, "Run quality repair.")

    assert normalized[-1] == {"role": "user", "content": "Run quality repair."}


def test_final_request_context_audit_recognizes_director_contract_and_blueprint_anchors() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    messages = [
        {
            "role": "user",
            "content": (
                "PM Task Contract / 任务合同:\n"
                "任务: Implement firefly garden simulator\n"
                "目标文件: src/engine/SimulationEngine.ts\n"
                "Acceptance criteria / 验收标准:\n"
                "- npm run build\n"
                "Chief Engineer Blueprint / CE 蓝图交接:\n"
                "- blueprint_id: bp-L1-01-4\n"
                "- construction target: src/engine/SimulationEngine.ts\n"
                "- construction signatures: class SimulationEngine\n"
                "- construction verify: npm run build\n"
            ),
        }
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
        "chief_engineer_blueprint": {
            "schema_version": "chief_engineer.blueprint.v1",
            "blueprint_id": "bp-L1-01-4",
            "target_files": ["src/engine/SimulationEngine.ts"],
            "construction_plan": {"phase": "implement"},
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
    assert audit["coverage"]["has_chief_engineer_blueprint"] is True
    assert audit["coverage"]["has_target_files"] is True
    assert audit["coverage"]["has_workspace_quality_evidence"] is True


def test_final_request_context_audit_uses_active_fallback_request_options() -> None:
    profile = Mock()
    profile.max_context_tokens = 32768
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "user",
                "content": "TASK-1 target_files src/index.ts Chief Engineer blueprint",
            },
        ],
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={
            "tools": [{"type": "function", "function": {"name": "write_file"}}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "Plan"}},
        },
        ai_request=Mock(),
        native_tool_schemas=[{"type": "function", "function": {"name": "write_file"}}],
        native_response_format={"type": "json_schema", "json_schema": {"name": "Plan"}},
    )
    fallback_request = Mock()
    fallback_request.options = {}
    fallback_request.context = {
        "pm_contract": {
            "schema_version": "pm.task_contract.v1",
            "task_id": "TASK-1",
            "target_files": ["src/index.ts"],
        },
        "chat_messages": [
            {
                "role": "user",
                "content": "Fallback plain text request with TASK-1 target_files src/index.ts",
            }
        ],
    }
    fallback_request.input = ""

    audit = build_final_request_context_audit_for_request(
        ai_request=fallback_request,
        prepared=prepared,
        profile=profile,
    )

    assert audit["message_count"] == 1
    assert audit["tool_schema_count"] == 0
    assert audit["tool_schema_token_estimate"] == 0
    assert audit["response_format_token_estimate"] == 0
    assert audit["final_request_token_estimate"] == audit["message_token_estimate"]
    assert audit["coverage"]["has_pm_contract"] is True


def test_final_request_context_audit_reads_role_context_policy_window() -> None:
    profile = Mock()
    profile.max_context_tokens = None
    profile.context_policy = Mock(max_context_tokens=32768)
    prepared = PreparedLLMRequest(
        messages=[
            {
                "role": "user",
                "content": "TASK-1 target_files src/index.ts Chief Engineer blueprint",
            },
        ],
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options={},
        ai_request=Mock(),
        native_tool_schemas=[],
    )

    audit = build_final_request_context_audit(prepared=prepared, profile=profile)

    assert audit["context_window_tokens"] == 32768
    assert audit["context_window_utilization"] is not None


