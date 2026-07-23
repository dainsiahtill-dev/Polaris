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


def _b32_prepared_factory_request() -> Any:
    messages = [
        {
            "role": "system",
            "content": "You are Director.\n\npolaris.role_identity.v1:director",
        },
        {"role": "user", "content": "Implement."},
    ]
    ai_request = SimpleNamespace(
        input="Implement.",
        context={"chat_messages": messages},
        options={},
        provider_id="provider-a",
        model="kimi-for-coding",
    )
    return SimpleNamespace(
        messages=messages,
        input_text="Implement.",
        context_result=SimpleNamespace(
            token_estimate=3,
            compression_strategy=None,
            compression_applied=False,
            metadata={},
        ),
        context_summary="summary",
        request_options={},
        ai_request=ai_request,
        factory_semantic_request=Mock(),
        factory_dispatch_port=Mock(),
        __post_init__=lambda: None,
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


def _b33_propagating_prepared(*, native_response_format: bool = False) -> SimpleNamespace:
    request = SimpleNamespace(provider_id="provider-a", model="model-a", context={}, options={})
    return SimpleNamespace(
        ai_request=request,
        messages=[],
        request_options={},
        context_summary="summary",
        context_os_audit={},
        context_result=SimpleNamespace(
            token_estimate=1,
            compression_applied=False,
            compression_strategy=None,
        ),
        factory_semantic_request=object(),
        factory_dispatch_port=Mock(),
        native_response_format=native_response_format,
        native_tool_mode="disabled",
        native_tool_schemas=[],
        response_format_mode="plain_text",
        __post_init__=Mock(),
    )


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


def _assert_invoker_owned_identity(identity: object, *, run_id: str, turn_round: int) -> None:
    identity_type = getattr(cutoff_contract, "FactoryRoleSemanticRequestIdentityV1", None)
    assert identity_type is not None, "B3.2 semantic identity contract missing"
    assert type(identity) is identity_type
    assert identity.run_id == run_id
    assert identity.turn_id == f"{run_id}:turn:{turn_round}"
    assert len(identity.call_id) == 32
    assert len(identity.request_freeze_id) == 32
    assert identity.call_id != "f" * 32
    assert identity.request_freeze_id != "e" * 32


@pytest.mark.asyncio
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


def _zero_tool_prepared_request(
    options: dict[str, Any],
    context: dict[str, Any],
    messages: list[dict[str, Any]],
) -> tuple[Any, PreparedLLMRequest]:
    ai_request = Mock()
    ai_request.context = context
    ai_request.options = options
    ai_request.input = ""
    prepared = PreparedLLMRequest(
        messages=messages,
        input_text="",
        context_result=Mock(),
        context_summary="summary",
        request_options=dict(options),
        ai_request=ai_request,
        native_tool_schemas=[],
    )
    return ai_request, prepared


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


def test_final_request_context_audit_prefers_bound_model_window_over_role_default() -> None:
    profile = Mock()
    profile.max_context_tokens = 8000
    profile.context_policy = Mock(max_context_tokens=8000)
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
        capability_profile={"model_window_tokens": 24576},
    )

    audit = build_final_request_context_audit(prepared=prepared, profile=profile)

    assert audit["context_window_tokens"] == 24576
    assert audit["available_token_headroom"] > 8000


# ============ DecisionCaller Tests ============


@pytest.mark.asyncio
class TestDecisionCaller:
    """测试 DecisionCaller."""

    async def test_call_returns_dict(self) -> None:
        """call 应返回兼容 TransactionKernel 的字典."""
        invoker = Mock()
        invoker.call = AsyncMock(
            return_value=LLMResponse(
                content="decision",
                tool_calls=[{"id": "call_1", "function": {"name": "read_file", "arguments": "{}"}}],
                tool_call_provider="openai",
                metadata={"model": "claude"},
            )
        )
        caller = DecisionCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "read main.py"
        context.history = ()
        context.task_id = None
        context.context_override = None

        result = await caller.call(
            profile=profile,
            system_prompt="sys",
            context=context,
            tool_definitions=[{"name": "read_file"}],
        )

        assert result["content"] == "decision"
        assert len(result["tool_calls"]) == 1
        assert result["native_tool_calls"] == result["tool_calls"]
        assert result["usage"]["native_tool_calls_count"] == 1
        assert result["usage"]["decision_caller_native_tool_calls_count"] == 1
        assert result["usage"]["native_tool_call_names"] == ["read_file"]
        assert result["usage"]["tool_call_provider"] == "openai"
        assert result["model"] == "unknown"

    async def test_call_derives_tool_count_and_names_from_envelopes(self) -> None:
        """DecisionCaller should preserve invoker envelope facts as the count SSOT."""
        invoker = Mock()
        invoker.call = AsyncMock(
            return_value=LLMResponse(
                content="decision",
                tool_calls=[{"id": "raw_call", "function": {"name": "read_file", "arguments": "{}"}}],
                tool_call_provider="openai",
                metadata={
                    "native_tool_call_envelopes": [
                        {
                            "schema_version": "native_tool_call_envelope.v1",
                            "tool_name": "repo_rg",
                            "call_id": "env_call_1",
                        },
                        {
                            "schema_version": "native_tool_call_envelope.v1",
                            "tool_name": "read_file",
                            "call_id": "env_call_2",
                        },
                    ]
                },
            )
        )
        caller = DecisionCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "inspect files"
        context.history = ()
        context.task_id = None
        context.context_override = None

        result = await caller.call(
            profile=profile,
            system_prompt="sys",
            context=context,
            tool_definitions=[{"name": "repo_rg"}, {"name": "read_file"}],
        )

        assert result["usage"]["native_tool_calls_count"] == 2
        assert result["usage"]["decision_caller_native_tool_calls_count"] == 2
        assert result["usage"]["native_tool_call_names"] == ["repo_rg", "read_file"]

    async def test_call_error_preserves_response_metadata_on_exception(self) -> None:
        """DecisionCaller errors must keep final request evidence for TransactionKernel."""
        invoker = Mock()
        invoker.call = AsyncMock(
            return_value=LLMResponse(
                content="",
                error="rate limited",
                error_category="rate_limit",
                metadata={
                    "provider": "openai_compat-local",
                    "provider_id": "openai_compat-local",
                    "model": "gemma-local",
                    "context_snapshot_ref": "ctx-gemma",
                    "final_request_context_audit": {"final_request_token_estimate": 42},
                },
            )
        )
        caller = DecisionCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "write files"
        context.history = ()
        context.task_id = None
        context.context_override = None

        with pytest.raises(RuntimeError) as exc_info:
            await caller.call(
                profile=profile,
                system_prompt="sys",
                context=context,
                tool_definitions=[{"name": "write_file"}],
            )

        metadata = vars(exc_info.value).get("llm_response_metadata")
        assert isinstance(metadata, dict)
        assert metadata["provider_id"] == "openai_compat-local"
        assert metadata["model"] == "gemma-local"
        assert metadata["context_snapshot_ref"] == "ctx-gemma"
        assert metadata["error_category"] == "rate_limit"

    async def test_call_preserves_native_tool_calls_alias_without_tool_calls_field(self) -> None:
        """DecisionCaller should consume the shared response alias normalizer."""
        invoker = Mock()
        invoker.call = AsyncMock(
            return_value=SimpleNamespace(
                content="decision",
                native_tool_calls=[
                    {
                        "id": "toolu_native",
                        "type": "tool_use",
                        "name": "read_file",
                        "input": {"path": "main.py"},
                    }
                ],
                tool_call_provider="anthropic",
                metadata={},
                model="claude",
            )
        )
        caller = DecisionCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "read main.py"
        context.history = ()
        context.task_id = None
        context.context_override = None

        result = await caller.call(
            profile=profile,
            system_prompt="sys",
            context=context,
            tool_definitions=[{"name": "read_file"}],
        )

        assert result["tool_calls"] == result["native_tool_calls"]
        assert result["tool_calls"][0]["id"] == "toolu_native"
        assert result["usage"]["native_tool_calls_count"] == 1
        assert result["usage"]["decision_caller_native_tool_calls_count"] == 1
        assert result["usage"]["native_tool_call_names"] == ["read_file"]

    async def test_call_raises_on_error(self) -> None:
        """LLM 返回 error 时应抛出 RuntimeError."""
        invoker = Mock()
        invoker.call = AsyncMock(return_value=LLMResponse(content="", error="LLM failed", error_category="provider"))
        caller = DecisionCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "hello"
        context.history = ()
        context.task_id = None
        context.context_override = None

        with pytest.raises(RuntimeError, match="LLM failed"):
            await caller.call(profile=profile, system_prompt="sys", context=context)

    async def test_call_stream_delegates(self) -> None:
        """call_stream 应委托给 invoker.call_stream."""
        invoker = Mock()

        async def _mock_stream():
            yield {"chunk": "1"}

        invoker.call_stream = Mock(return_value=_mock_stream())
        caller = DecisionCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "hello"
        context.history = ()
        context.task_id = None
        context.context_override = None

        stream = await caller.call_stream(profile=profile, system_prompt="sys", context=context)
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)

        assert len(chunks) == 1
        invoker.call_stream.assert_called_once()


async def async_generator(items: dict[str, Any]) -> Any:
    """Helper to create an async generator from a single item."""
    yield items


# ============ FinalizationCaller Tests ============


class TestFinalizationCaller:
    """测试 FinalizationCaller."""

    @pytest.mark.asyncio
    async def test_call_returns_dict(self) -> None:
        """call 应返回兼容 TransactionKernel 的字典."""
        invoker = Mock()
        invoker.call = AsyncMock(return_value=LLMResponse(content="final answer", metadata={"model": "claude"}))
        caller = FinalizationCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "hello"
        context.history = ()
        context.task_id = None
        context.context_override = None

        result = await caller.call(profile=profile, system_prompt="sys", context=context)

        assert result["content"] == "final answer"
        assert result["tool_calls"] == []
        assert result["native_tool_calls"] == []
        assert result["model"] == "unknown"

    @pytest.mark.asyncio
    async def test_call_preserves_native_tool_calls_alias_without_tool_calls_field(self) -> None:
        """FinalizationCaller should use the shared response alias normalizer."""
        invoker = Mock()
        invoker.call = AsyncMock(
            return_value=SimpleNamespace(
                content="final answer",
                native_tool_calls=[
                    {
                        "id": "toolu_final",
                        "type": "tool_use",
                        "name": "write_file",
                        "input": {"path": "x.py", "content": "1"},
                    }
                ],
                metadata={},
                model="claude",
            )
        )
        caller = FinalizationCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "hello"
        context.history = ()
        context.task_id = None
        context.context_override = None

        result = await caller.call(profile=profile, system_prompt="sys", context=context)

        assert result["tool_calls"] == result["native_tool_calls"]
        assert result["tool_calls"][0]["id"] == "toolu_final"
        assert result["tool_calls"][0]["name"] == "write_file"

    @pytest.mark.asyncio
    async def test_call_raises_on_error(self) -> None:
        """LLM 返回 error 时应抛出 RuntimeError."""
        invoker = Mock()
        invoker.call = AsyncMock(return_value=LLMResponse(content="", error="finalization failed"))
        caller = FinalizationCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "hello"
        context.history = ()
        context.task_id = None
        context.context_override = None

        with pytest.raises(RuntimeError, match="finalization failed"):
            await caller.call(profile=profile, system_prompt="sys", context=context)

    def test_override_prebuilt_system_prompt(self) -> None:
        """应替换 prebuilt messages 中的 system prompt."""
        invoker = Mock()
        caller = FinalizationCaller(invoker)

        context = Mock()
        context.message = "hello"
        context.history = ()
        context.task_id = None
        context.context_override = {
            "_transaction_kernel_prebuilt_messages": [
                {"role": "system", "content": "old"},
                {"role": "user", "content": "hi"},
            ]
        }

        new_context = caller._override_prebuilt_system_prompt(context, "new prompt")

        override = new_context.context_override or {}
        messages = override["_transaction_kernel_prebuilt_messages"]
        assert messages[0]["content"] == "new prompt"
        assert messages[1]["content"] == "hi"

    def test_override_prebuilt_system_prompt_disables_transaction_tools(self) -> None:
        """Finalization must clear decision-phase forced tools from the final request."""
        invoker = Mock()
        caller = FinalizationCaller(invoker)

        context = Mock()
        context.message = "hello"
        context.history = ()
        context.task_id = None
        context.context_override = {
            "_transaction_kernel_prebuilt_messages": [
                {"role": "system", "content": "old"},
                {"role": "user", "content": "hi"},
            ],
            "_transaction_kernel_forced_tool_definitions": [{"type": "function", "function": {"name": "read_file"}}],
            "_transaction_kernel_forced_tool_choice": "auto",
        }

        new_context = caller._override_prebuilt_system_prompt(context, "finalization prompt")

        override = new_context.context_override or {}
        assert override["_transaction_kernel_prebuilt_messages"][0]["content"] == "finalization prompt"
        assert override["_transaction_kernel_forced_tool_definitions"] == []
        assert override["_transaction_kernel_forced_tool_choice"] == "none"

    def test_build_finalization_prompt_for_execution(self) -> None:
        """执行类请求应生成执行型提示词."""
        invoker = Mock()
        caller = FinalizationCaller(invoker)

        profile = Mock()
        profile.role_id = "director"
        context = Mock()
        context.message = "write a file"
        context.history = ()
        context.task_id = None
        context.context_override = {"domain": "code"}

        prompt = caller._build_finalization_system_prompt(profile=profile, context=context)
        assert "FINAL ANSWER" in prompt
        assert "落地" in prompt or "执行" in prompt

    def test_build_finalization_prompt_for_analysis(self) -> None:
        """分析类请求应生成分析型提示词."""
        invoker = Mock()
        caller = FinalizationCaller(invoker)

        profile = Mock()
        profile.role_id = "architect"
        context = Mock()
        context.message = "explain this code"
        context.history = ()
        context.task_id = None
        context.context_override = {"domain": "code"}

        prompt = caller._build_finalization_system_prompt(profile=profile, context=context)
        assert "FINAL ANSWER" in prompt


# ============ Error Handling Tests ============


class TestClassifyError:
    """测试 classify_error."""

    def test_timeout_classification(self) -> None:
        """超时错误应分类为 timeout."""
        assert classify_error("Request timeout") == ERROR_CATEGORY_TIMEOUT
        assert classify_error("timed out") == ERROR_CATEGORY_TIMEOUT

    def test_rate_limit_classification(self) -> None:
        """429 错误应分类为 rate_limit."""
        assert classify_error("429 Too Many Requests") == ERROR_CATEGORY_RATE_LIMIT
        assert classify_error("rate limit exceeded") == ERROR_CATEGORY_RATE_LIMIT

    def test_network_classification(self) -> None:
        """网络错误应分类为 network."""
        assert classify_error("Connection refused") == ERROR_CATEGORY_NETWORK
        assert classify_error("DNS resolution failed") == ERROR_CATEGORY_NETWORK

    def test_circuit_open_classification(self) -> None:
        """Provider circuit breaker open is a runtime/provider availability failure."""
        assert classify_error("circuit_open:57s_remaining") == ERROR_CATEGORY_NETWORK

    def test_cancelled_classification(self) -> None:
        """Cancellation must not fall through to unknown."""
        assert classify_error("call_cancelled") == ERROR_CATEGORY_CANCELLED

    def test_auth_classification(self) -> None:
        """认证错误应分类为 auth."""
        assert classify_error("Unauthorized: invalid api key") == ERROR_CATEGORY_AUTH

    def test_unknown_fallback(self) -> None:
        """未知错误应分类为 unknown."""
        assert classify_error("something weird") == ERROR_CATEGORY_UNKNOWN

    def test_empty_string(self) -> None:
        """空字符串应分类为 unknown."""
        assert classify_error("") == ERROR_CATEGORY_UNKNOWN


class TestIsRetryableError:
    """测试 is_retryable_error."""

    def test_timeout_is_retryable(self) -> None:
        assert is_retryable_error(ERROR_CATEGORY_TIMEOUT) is True

    def test_network_is_retryable(self) -> None:
        assert is_retryable_error(ERROR_CATEGORY_NETWORK) is True

    def test_rate_limit_is_retryable(self) -> None:
        assert is_retryable_error(ERROR_CATEGORY_RATE_LIMIT) is True

    def test_auth_is_not_retryable(self) -> None:
        assert is_retryable_error(ERROR_CATEGORY_AUTH) is False

    def test_unknown_is_not_retryable(self) -> None:
        assert is_retryable_error(ERROR_CATEGORY_UNKNOWN) is False


class TestIsNativeToolCallingUnsupported:
    """测试 is_native_tool_calling_unsupported."""

    def test_tools_not_allowed(self) -> None:
        """tools not allowed 应被识别."""
        assert is_native_tool_calling_unsupported("tools is not allowed") is True

    def test_unknown_field(self) -> None:
        """unknown field 应被识别."""
        assert is_native_tool_calling_unsupported("unknown field: tools") is True

    def test_function_calling_not_supported(self) -> None:
        """function calling not supported 应被识别."""
        assert is_native_tool_calling_unsupported("function calling not supported") is True

    def test_normal_error(self) -> None:
        """普通错误不应被识别."""
        assert is_native_tool_calling_unsupported("model overloaded") is False

    def test_empty_string(self) -> None:
        """空字符串不应被识别."""
        assert is_native_tool_calling_unsupported("") is False


class TestIsResponseFormatUnsupported:
    """测试 is_response_format_unsupported."""

    def test_response_format_keyword(self) -> None:
        """response_format 关键字应被识别."""
        assert is_response_format_unsupported("unsupported parameter: response_format") is True

    def test_json_schema_keyword(self) -> None:
        """json_schema 关键字应被识别."""
        assert is_response_format_unsupported("does not support json schema") is True

    def test_normal_error(self) -> None:
        """普通错误不应被识别."""
        assert is_response_format_unsupported("model overloaded") is False


class TestBuildNativeToolUnavailableError:
    """测试 build_native_tool_unavailable_error."""

    def test_builds_error_message(self) -> None:
        """应构建包含 provider/model/tools 信息的错误消息."""
        profile = Mock()
        profile.provider_id = "test-provider"
        profile.model = "test-model"
        tp = Mock()
        tp.whitelist = ["read_file", "write_file"]
        profile.tool_policy = tp

        msg = build_native_tool_unavailable_error(profile)
        assert "native_tool_calling_unavailable" in msg
        assert "test-provider" in msg
        assert "test-model" in msg
        assert "read_file" in msg

    def test_empty_whitelist(self) -> None:
        """空白名单时应使用默认文本."""
        profile = Mock()
        profile.provider_id = "p"
        profile.model = "m"
        tp = Mock()
        tp.whitelist = []
        profile.tool_policy = tp

        msg = build_native_tool_unavailable_error(profile)
        assert "authorized_tools" in msg


class TestBuildTextResponseFallbackInstruction:
    """测试 build_text_response_fallback_instruction."""

    def test_includes_schema_name(self) -> None:
        """应包含 schema 名称."""

        class FakeModel:
            __name__ = "TestSchema"

            @classmethod
            def model_json_schema(cls) -> dict[str, Any]:
                return {"type": "object"}

        instruction = build_text_response_fallback_instruction(FakeModel)
        assert "TestSchema" in instruction or "FakeModel" in instruction


# ============ LLMEventEmitter Tests ============


class TestLLMEventEmitterInit:
    """测试 LLMEventEmitter 初始化."""

    def test_init(self) -> None:
        """基本初始化."""
        emitter = LLMEventEmitter(workspace="/ws")
        assert emitter.workspace == "/ws"

    def test_publish_uep_lifecycle_event_without_loop_does_not_leak_warning(self) -> None:
        """无运行中 event loop 时不应创建未 await 的 coroutine."""
        emitter = LLMEventEmitter(workspace="/ws")

        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            emitter.publish_uep_lifecycle_event(
                role="director",
                run_id="run_1",
                event_type="call_start",
            )
            gc.collect()

        warning_messages = [str(item.message) for item in captured]
        assert not any("was never awaited" in message for message in warning_messages)


class TestLLMEventEmitterEmitCallStartEvent:
    """测试 emit_call_start_event."""

    def test_emits_with_basic_params(self) -> None:
        """基本参数应能发射事件."""
        emitter = LLMEventEmitter(workspace="/ws")
        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_start_event(
                event_emitter=None,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                call_id="call_1",
            )
            mock_emit.assert_called_once()
            kwargs = mock_emit.call_args.kwargs
            assert kwargs["role"] == "director"
            assert kwargs["run_id"] == "run_1"

    def test_emits_with_event_emitter_override(self) -> None:
        """传入 event_emitter 时应使用其方法并补写 canonical 事件."""
        emitter = LLMEventEmitter(workspace="/ws")
        custom_emitter = Mock()
        custom_emitter._emit_call_start_event = Mock()

        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_start_event(
                event_emitter=custom_emitter,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                call_id="call_1",
            )
        custom_emitter._emit_call_start_event.assert_called_once()
        mock_emit.assert_called_once()

    def test_canonical_event_emitter_override_does_not_double_emit(self) -> None:
        """已声明写 canonical 事件的 emitter 不应被重复补写."""
        emitter = LLMEventEmitter(workspace="/ws")
        custom_emitter = Mock()
        custom_emitter._emits_canonical_llm_events = True
        custom_emitter._emit_call_start_event = Mock()

        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_start_event(
                event_emitter=custom_emitter,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                call_id="call_1",
            )
        custom_emitter._emit_call_start_event.assert_called_once()
        mock_emit.assert_not_called()


class TestLLMEventEmitterEmitCallErrorEvent:
    """测试 emit_call_error_event."""

    def test_emits_error_event(self) -> None:
        """错误事件应被发射."""
        emitter = LLMEventEmitter(workspace="/ws")
        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_error_event(
                event_emitter=None,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                error_category="timeout",
                error_message="timed out",
                call_id="call_1",
                elapsed_ms=1000.0,
            )
            mock_emit.assert_called_once()
            kwargs = mock_emit.call_args.kwargs
            assert kwargs["error_category"] == "timeout"
            assert kwargs["error_message"] == "timed out"

    def test_custom_error_emitter_still_writes_canonical_event(self) -> None:
        """错误 override 不能吞掉 canonical 事件."""
        emitter = LLMEventEmitter(workspace="/ws")
        custom_emitter = Mock()
        custom_emitter._emit_call_error_event = Mock()

        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_error_event(
                event_emitter=custom_emitter,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                error_category="timeout",
                error_message="timed out",
                call_id="call_1",
                elapsed_ms=1000.0,
            )
            custom_emitter._emit_call_error_event.assert_called_once()
            mock_emit.assert_called_once()


class TestLLMEventEmitterEmitCallEndEvent:
    """测试 emit_call_end_event."""

    def test_emits_end_event(self) -> None:
        """结束事件应被发射."""
        emitter = LLMEventEmitter(workspace="/ws")
        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_end_event(
                event_emitter=None,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                call_id="call_1",
                completion_tokens=50,
            )
            mock_emit.assert_called_once()
            kwargs = mock_emit.call_args.kwargs
            assert kwargs["completion_tokens"] == 50

    def test_call_end_tool_count_uses_native_lifecycle_metadata(self) -> None:
        """call_end 工具计数由 Run Ledger native metadata 投影，而非调用方 fallback."""
        emitter = LLMEventEmitter(workspace="/ws")
        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_end_event(
                event_emitter=None,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                call_id="call_1",
                completion_tokens=50,
                tool_calls_count=1,
                metadata={
                    "native_tool_call_envelope_refs": [
                        {"schema_version": "native_tool_call_envelope.v1", "tool_name": "write_file"},
                        {"schema_version": "native_tool_call_envelope.v1", "tool_name": "execute_command"},
                    ]
                },
            )

            kwargs = mock_emit.call_args.kwargs
            assert kwargs["tool_calls_count"] == 2
            assert kwargs["metadata"]["tool_calls_count"] == 2

    def test_response_content_emits_content_preview_before_end_event(self) -> None:
        """response_content 应进入实时内容预览，而不是只留在 call_end metadata."""
        emitter = LLMEventEmitter(workspace="/ws")
        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_end_event(
                event_emitter=None,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                call_id="call_1",
                completion_tokens=50,
                response_content="公开模型输出片段",
            )

            event_types = [call.kwargs["event_type"] for call in mock_emit.call_args_list]
            assert event_types == ["content_preview", "llm_call_end"]
            preview_kwargs = mock_emit.call_args_list[0].kwargs
            assert preview_kwargs["metadata"]["content"] == "公开模型输出片段"
            assert preview_kwargs["metadata"]["call_id"] == "call_1"
            assert preview_kwargs["metadata"]["content_length"] == len("公开模型输出片段")
            assert preview_kwargs["metadata"]["truncated"] is False
            assert "response_content" not in preview_kwargs["metadata"]
            assert preview_kwargs["completion_tokens"] == 50
            end_kwargs = mock_emit.call_args_list[1].kwargs
            assert end_kwargs["metadata"]["response_content"] == "公开模型输出片段"

    def test_response_content_preview_is_truncated_without_full_response_duplication(self) -> None:
        """CONTENT_PREVIEW must be bounded and must not duplicate full response_content."""
        emitter = LLMEventEmitter(workspace="/ws")
        long_content = "x" * 2505
        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_end_event(
                event_emitter=None,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                call_id="call_1",
                completion_tokens=50,
                response_content=long_content,
            )

            preview_metadata = mock_emit.call_args_list[0].kwargs["metadata"]
            assert len(preview_metadata["content"]) == 2000
            assert preview_metadata["content_length"] == len(long_content)
            assert preview_metadata["truncated"] is True
            assert "response_content" not in preview_metadata

    def test_custom_end_emitter_still_writes_canonical_event(self) -> None:
        """结束 override 不能吞掉 canonical 事件."""
        emitter = LLMEventEmitter(workspace="/ws")
        custom_emitter = Mock()
        custom_emitter._emit_call_end_event = Mock()

        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_end_event(
                event_emitter=custom_emitter,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                call_id="call_1",
                completion_tokens=50,
            )
            custom_emitter._emit_call_end_event.assert_called_once()
            mock_emit.assert_called_once()


class TestLLMEventEmitterEmitCallRetryEvent:
    """测试 emit_call_retry_event."""

    def test_emits_retry_event(self) -> None:
        """重试事件应被发射."""
        emitter = LLMEventEmitter(workspace="/ws")
        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_retry_event(
                event_emitter=None,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=1,
                model="claude",
                call_id="call_1",
                retry_decision="backoff",
                backoff_seconds=2.0,
            )
            mock_emit.assert_called_once()
            kwargs = mock_emit.call_args.kwargs
            assert kwargs["retry_decision"] == "backoff"
            assert kwargs["backoff_seconds"] == 2.0

    def test_custom_retry_emitter_still_writes_canonical_event(self) -> None:
        """重试 override 不能吞掉 canonical 事件."""
        emitter = LLMEventEmitter(workspace="/ws")
        custom_emitter = Mock()
        custom_emitter._emit_call_retry_event = Mock()

        with patch("polaris.cells.roles.kernel.internal.events.emit_llm_event") as mock_emit:
            emitter.emit_call_retry_event(
                event_emitter=custom_emitter,
                role="director",
                run_id="run_1",
                task_id="task_1",
                attempt=1,
                model="claude",
                call_id="call_1",
                retry_decision="backoff",
                backoff_seconds=2.0,
            )
            custom_emitter._emit_call_retry_event.assert_called_once()
            mock_emit.assert_called_once()


# ============ ProviderFormatter Tests ============


class TestCreateFormatter:
    """测试 create_formatter."""

    def test_openai_formatter(self) -> None:
        """openai 应返回 NativeProviderFormatter."""
        fmt = create_formatter("openai")
        assert isinstance(fmt, NativeProviderFormatter)

    def test_anthropic_formatter(self) -> None:
        """anthropic 应返回 NativeProviderFormatter."""
        fmt = create_formatter("anthropic")
        assert isinstance(fmt, NativeProviderFormatter)

    def test_annotated_formatter(self) -> None:
        """annotated 应返回 AnnotatedProviderFormatter."""
        fmt = create_formatter("annotated")
        assert isinstance(fmt, AnnotatedProviderFormatter)

    def test_unknown_defaults_to_annotated(self) -> None:
        """未知 provider 应默认返回 AnnotatedProviderFormatter."""
        fmt = create_formatter("unknown")
        assert isinstance(fmt, AnnotatedProviderFormatter)


class TestNativeProviderFormatter:
    """测试 NativeProviderFormatter."""

    def test_format_tools_passes_through(self) -> None:
        """原生格式化应直接透传."""
        fmt = NativeProviderFormatter()
        tools = [{"name": "read_file"}]
        assert fmt.format_tools(tools, "openai") == tools

    def test_format_messages_passes_through(self) -> None:
        """原生格式化应直接透传消息."""
        fmt = NativeProviderFormatter()
        from unittest.mock import Mock

        event = Mock()
        event.role = "user"
        event.content = "hello"
        messages: list[Any] = [event]
        assert fmt.format_messages(messages) == [{"role": "user", "content": "hello"}]


class TestAnnotatedProviderFormatter:
    """测试 AnnotatedProviderFormatter."""

    def test_format_tools_passes_through(self) -> None:
        """应直接透传工具 schema."""
        fmt = AnnotatedProviderFormatter()
        tools = [{"name": "read_file", "description": "Read a file"}]
        result = fmt.format_tools(tools, "openai")
        assert len(result) == 1
        assert result[0]["name"] == "read_file"

    def test_format_messages_passes_through(self) -> None:
        """应直接透传消息."""
        fmt = AnnotatedProviderFormatter()
        from unittest.mock import Mock

        event = Mock()
        event.role = "user"
        event.content = "hello"
        messages: list[Any] = [event]
        assert fmt.format_messages(messages) == [{"role": "user", "content": "hello"}]


# ============ StreamEngine Tests ============


class TestStreamEngineInit:
    """测试 StreamEngine 初始化."""

    def test_init(self) -> None:
        """基本初始化."""
        engine = StreamEngine(
            workspace="/ws",
            get_executor=Mock(),
            allow_native_tool_text_fallback_fn=Mock(),
            emit_call_start_event=Mock(),
            emit_call_error_event=Mock(),
            emit_call_end_event=Mock(),
            emit_call_retry_event=Mock(),
        )
        assert engine.workspace == "/ws"

    def test_llm_invoker_stream_store_wiring_accepts_provider_request(self) -> None:
        """Default streamed context snapshots must preserve provider requests."""
        invoker = LLMInvoker(workspace="/ws")

        assert _store_context_messages_accepts_provider_request(invoker._stream_engine._store_context_messages)


@pytest.mark.asyncio
class TestStreamEngineRunStream:
    """测试 StreamEngine.run_stream."""

    async def test_b33_stream_initial_and_reconnect_keep_same_exact_port(self) -> None:
        seen_ports: list[object] = []
        closed_attempts: list[int] = []
        prepared = _b33_propagating_prepared()
        context = SimpleNamespace(
            context_override={
                "stream_max_reconnects": 1,
                "stream_retry_backoff_seconds": 0,
            },
            stream_cancelled=False,
            temperature=0.2,
            max_tokens=128,
        )

        class _Executor:
            async def invoke_stream(self, _request: object, *, physical_dispatch_port: object):
                seen_ports.append(physical_dispatch_port)
                current_attempt = len(seen_ports)
                try:
                    if current_attempt == 1:
                        yield {"type": "error", "error": "429 rate limited"}
                    else:
                        yield {"type": "chunk", "content": "ok"}
                finally:
                    closed_attempts.append(current_attempt)

        engine = StreamEngine(
            workspace="/ws",
            get_executor=lambda: _Executor(),
            allow_native_tool_text_fallback_fn=Mock(return_value=False),
            emit_call_start_event=Mock(),
            emit_call_error_event=Mock(),
            emit_call_end_event=Mock(),
            emit_call_retry_event=Mock(),
        )

        with (
            patch(
                "polaris.cells.roles.kernel.internal.llm_caller.stream_engine.build_final_request_context_audit_for_request",
                return_value={"final_request_token_estimate": 1},
            ),
            patch(
                "polaris.cells.roles.kernel.internal.llm_caller.stream_engine."
                "enforce_factory_aware_final_request_evidence_coverage"
            ),
        ):
            events = [
                event
                async for event in engine.run_stream(
                    profile=SimpleNamespace(provider_id="provider-a", role_id="director"),
                    prepared=prepared,
                    context=context,
                    start_time=time.perf_counter(),
                    role_id="director",
                    run_id="run-1",
                    task_id=None,
                    attempt=0,
                    model="model-a",
                    call_id="call-1",
                    event_emitter=None,
                    turn_round=0,
                )
            ]

        assert any(event.get("content") == "ok" for event in events)
        assert seen_ports == [prepared.factory_dispatch_port, prepared.factory_dispatch_port]
        assert closed_attempts == [1, 2]
        assert prepared.factory_dispatch_port.validate_frozen_identity.call_count == 2
        assert all(
            call.args == (prepared.factory_semantic_request,)
            for call in prepared.factory_dispatch_port.validate_frozen_identity.call_args_list
        )

    async def test_provider_stream_absolute_timeout_is_not_reconnected(self) -> None:
        """A consumed request deadline is terminal for the role stream.

        The provider helper owns one absolute deadline across its wire retries.
        Reconnecting that timed-out request here would mint a fresh deadline and
        replay the same physical request, multiplying the declared timeout.
        """
        invoke_count = 0
        emit_retry = Mock()
        prepared = _b33_propagating_prepared()
        context = SimpleNamespace(
            context_override={
                "stream_max_reconnects": 1,
                "stream_retry_backoff_seconds": 0,
            },
            stream_cancelled=False,
            temperature=0.2,
            max_tokens=128,
        )

        class _Executor:
            async def invoke_stream(self, _request: object, *, physical_dispatch_port: object):
                nonlocal invoke_count
                invoke_count += 1
                assert physical_dispatch_port is prepared.factory_dispatch_port
                yield {"type": "error", "error": "provider_stream_timeout:193s"}

        engine = StreamEngine(
            workspace="/ws",
            get_executor=lambda: _Executor(),
            allow_native_tool_text_fallback_fn=Mock(return_value=False),
            emit_call_start_event=Mock(),
            emit_call_error_event=Mock(),
            emit_call_end_event=Mock(),
            emit_call_retry_event=emit_retry,
        )

        with (
            patch(
                "polaris.cells.roles.kernel.internal.llm_caller.stream_engine.build_final_request_context_audit_for_request",
                return_value={"final_request_token_estimate": 1},
            ),
            patch(
                "polaris.cells.roles.kernel.internal.llm_caller.stream_engine."
                "enforce_factory_aware_final_request_evidence_coverage"
            ),
        ):
            events = [
                event
                async for event in engine.run_stream(
                    profile=SimpleNamespace(provider_id="provider-a", role_id="chief_engineer"),
                    prepared=prepared,
                    context=context,
                    start_time=time.perf_counter(),
                    role_id="chief_engineer",
                    run_id="run-timeout",
                    task_id=None,
                    attempt=0,
                    model="model-a",
                    call_id="call-timeout",
                    event_emitter=None,
                    turn_round=0,
                )
            ]

        assert invoke_count == 1
        assert emit_retry.call_count == 0
        assert [event.get("error") for event in events if event.get("type") == "error"] == [
            "provider_stream_timeout:193s"
        ]

    async def test_cancel_before_invoke(self) -> None:
        """取消标志设置时应立即抛出 CancelledError."""
        engine = StreamEngine(
            workspace="/ws",
            get_executor=Mock(),
            allow_native_tool_text_fallback_fn=Mock(),
            emit_call_start_event=Mock(),
            emit_call_error_event=Mock(),
            emit_call_end_event=Mock(),
            emit_call_retry_event=Mock(),
        )

        context = Mock()
        context.context_override = {"stream_cancelled": True}

        profile = Mock()
        profile.role_id = "director"

        prepared = Mock()
        prepared.factory_dispatch_port = None
        prepared.__post_init__ = Mock()
        prepared.messages = []
        prepared.ai_request = Mock()
        prepared.native_tool_mode = "disabled"
        prepared.response_format_mode = "none"
        prepared.context_result = None

        with pytest.raises(asyncio.CancelledError):
            async for _event in engine.run_stream(
                profile=profile,
                prepared=prepared,
                context=context,
                start_time=0.0,
                role_id="director",
                run_id="run_1",
                task_id="task_1",
                attempt=0,
                model="claude",
                call_id="call_1",
                event_emitter=None,
                turn_round=0,
            ):
                pass

    async def test_empty_stream(self) -> None:
        """空流应正常完成."""
        engine = StreamEngine(
            workspace="/ws",
            get_executor=Mock(),
            allow_native_tool_text_fallback_fn=Mock(return_value=False),
            emit_call_start_event=Mock(),
            emit_call_error_event=Mock(),
            emit_call_end_event=Mock(),
            emit_call_retry_event=Mock(),
        )

        context = Mock()
        context.context_override = {}
        context.stream_cancelled = False

        profile = Mock()
        profile.role_id = "director"

        prepared = Mock()
        prepared.factory_dispatch_port = None
        prepared.__post_init__ = Mock()
        prepared.messages = []
        prepared.ai_request = Mock()
        prepared.native_tool_mode = "disabled"
        prepared.response_format_mode = "none"
        prepared.context_result = None

        # Mock executor to return empty stream
        mock_executor = Mock()

        async def _empty_stream(_request):
            return
            yield

        mock_executor.invoke_stream = _empty_stream
        engine._get_executor = lambda: mock_executor

        events = []
        async for event in engine.run_stream(
            profile=profile,
            prepared=prepared,
            context=context,
            start_time=0.0,
            role_id="director",
            run_id="run_1",
            task_id="task_1",
            attempt=0,
            model="claude",
            call_id="call_1",
            event_emitter=None,
            turn_round=0,
        ):
            events.append(event)

        # Should have at least context_metadata event
        assert any(e.get("type") == "context_metadata" for e in events)

    async def test_stream_call_end_projects_native_tool_envelopes_to_metadata(self) -> None:
        """Stream call-end tool counts must come from Run Ledger native metadata."""

        emit_end = Mock()
        engine = StreamEngine(
            workspace="/ws",
            get_executor=Mock(),
            allow_native_tool_text_fallback_fn=Mock(return_value=False),
            emit_call_start_event=Mock(),
            emit_call_error_event=Mock(),
            emit_call_end_event=emit_end,
            emit_call_retry_event=Mock(),
        )

        context = Mock()
        context.context_override = {}
        context.stream_cancelled = False
        context.temperature = 0.2
        context.max_tokens = 256

        profile = Mock()
        profile.role_id = "director"
        profile.provider_id = "provider-stream"

        prepared = Mock()
        prepared.factory_dispatch_port = None
        prepared.__post_init__ = Mock()
        prepared.messages = [{"role": "user", "content": "create files"}]
        prepared.ai_request = Mock()
        prepared.native_tool_mode = "native_tools_streaming"
        prepared.response_format_mode = "none"
        prepared.context_result = None

        mock_executor = Mock()

        async def _tool_stream(_request, *, physical_dispatch_port=None):
            assert physical_dispatch_port is None
            yield {
                "type": "tool_call",
                "tool_call": {
                    "id": "call-1",
                    "name": "write_file",
                    "arguments": {"file": "src/a.py", "content": "a"},
                },
            }
            yield {
                "type": "tool_call",
                "tool_call": {
                    "id": "call-2",
                    "name": "write_file",
                    "arguments": {"file": "src/b.py", "content": "b"},
                },
            }

        mock_executor.invoke_stream = _tool_stream
        engine._get_executor = lambda: mock_executor

        events = []
        async for event in engine.run_stream(
            profile=profile,
            prepared=prepared,
            context=context,
            start_time=0.0,
            role_id="director",
            run_id="run_1",
            task_id="task_1",
            attempt=0,
            model="claude",
            call_id="call_1",
            event_emitter=None,
            turn_round=0,
        ):
            events.append(event)

        assert [event.get("type") for event in events].count("tool_call") == 2
        end_kwargs = emit_end.call_args.kwargs
        end_metadata = end_kwargs["metadata"]
        assert end_kwargs["tool_calls_count"] == 2
        assert end_metadata["native_tool_calls_count"] == 2
        assert end_metadata["native_tool_call_names"] == ["write_file", "write_file"]
        assert [item["call_id"] for item in end_metadata["native_tool_call_envelopes"]] == [
            "call-1",
            "call-2",
        ]

    async def test_stream_call_start_emits_context_snapshot_ref(self) -> None:
        """Phase 1 critical fix: StreamEngine must call store_context_messages
        BEFORE the call_start event so the event metadata carries a non-empty
        context_snapshot_ref (Director multi-worker streams were producing
        empty refs and the per-LLM context viewer stayed blank).
        """
        emit_start = Mock()
        emit_end = Mock()
        captured_hash = "deadbeef" + "cafef00d" * 2  # 24-char hash sentinel

        # Performance hardening (HIGH #2): store_context_messages is awaited
        # so the disk write runs in a thread — the stub MUST be a coroutine
        # that returns the captured hash, mirroring the real
        # ``AIExecutor._store_context_messages`` signature.
        fake_store = AsyncMock(return_value=captured_hash)

        engine = StreamEngine(
            workspace="/ws",
            get_executor=Mock(),
            allow_native_tool_text_fallback_fn=Mock(return_value=False),
            emit_call_start_event=emit_start,
            emit_call_error_event=Mock(),
            emit_call_end_event=emit_end,
            emit_call_retry_event=Mock(),
            store_context_messages=fake_store,
        )

        context = Mock()
        context.context_override = {}
        context.stream_cancelled = False
        context.temperature = 0.2
        context.max_tokens = 256

        context_result = Mock()
        context_result.token_estimate = 12
        context_result.compression_strategy = "none"
        context_result.compression_applied = False

        prepared_messages = [
            {"role": "system", "content": "you are a director"},
            {"role": "user", "content": "build a thing"},
        ]
        prepared = Mock()
        prepared.factory_dispatch_port = None
        prepared.__post_init__ = Mock()
        prepared.messages = prepared_messages
        # Use a real dict-backed context so we can prove the hash is written
        # into prepared.ai_request.context AND read back via
        # _extract_context_snapshot_ref.
        prepared.ai_request = Mock()
        prepared.ai_request.context = {"mode": "chat"}
        prepared.native_tool_mode = "disabled"
        prepared.response_format_mode = "none"
        prepared.context_result = context_result
        prepared.context_os_audit = {}

        mock_executor = Mock()

        async def _empty_stream(_request, *, physical_dispatch_port=None):
            assert physical_dispatch_port is None
            return
            yield  # pragma: no cover -- intentional empty generator

        mock_executor.invoke_stream = _empty_stream
        engine._get_executor = lambda: mock_executor

        events: list[dict[str, Any]] = []
        async for event in engine.run_stream(
            profile=Mock(role_id="director"),
            prepared=prepared,
            context=context,
            start_time=0.0,
            role_id="director",
            run_id="run_stream_42",
            task_id="task_42",
            attempt=0,
            model="claude",
            call_id="call_42",
            event_emitter=None,
            turn_round=0,
        ):
            events.append(event)

        # 1. The fake store was invoked with the prepared messages and the
        # same run_id/call_id we passed into run_stream.
        assert any(
            list(call_args.args[1]) == prepared_messages
            and call_args.args[2] == "run_stream_42"
            and call_args.args[3] == "call_42"
            and isinstance(call_args.args[4], dict)
            and call_args.args[4].get("source") == "roles.kernel.llm_caller.context_audit"
            for call_args in fake_store.call_args_list
        ), fake_store.call_args_list

        # 2. The hash was written into prepared.ai_request.context so the
        # sync-style extractor can read it back.
        assert prepared.ai_request.context["context_snapshot_ref"] == captured_hash

        # 3. The call_start event metadata carries a non-empty
        # context_snapshot_ref. THIS is what the per-LLM context viewer reads.
        start_metadata = emit_start.call_args.kwargs["metadata"]
        assert start_metadata["context_snapshot_ref"] == captured_hash

        # 4. The call_end event metadata also carries the same hash so
        # downstream consumers can correlate.
        end_metadata = emit_end.call_args.kwargs["metadata"]
        assert end_metadata["context_snapshot_ref"] == captured_hash

    async def test_stream_call_start_missing_store_does_not_block(self) -> None:
        """Failing-closed guarantee: if store_context_messages is None (legacy
        wiring) or raises, the stream must still emit a call_start with an
        empty context_snapshot_ref instead of crashing the LLM call.
        """
        emit_start = Mock()

        # Performance hardening (HIGH #2): the stream engine now awaits
        # ``store_context_messages`` so the disk write runs in a worker
        # thread. The store stub must be a coroutine that raises so the
        # except clause in ``run_stream`` sees a real exception and falls
        # through to the empty-ref path.
        async def _raising_store(
            workspace: str,
            messages: list[Any],
            trace_id: str,
            call_id_value: str,
        ) -> str:
            raise RuntimeError("disk_full_simulated")

        engine = StreamEngine(
            workspace="/ws",
            get_executor=Mock(),
            allow_native_tool_text_fallback_fn=Mock(return_value=False),
            emit_call_start_event=emit_start,
            emit_call_error_event=Mock(),
            emit_call_end_event=Mock(),
            emit_call_retry_event=Mock(),
            store_context_messages=_raising_store,
        )

        context = Mock()
        context.context_override = {}
        context.stream_cancelled = False
        context.temperature = 0.2
        context.max_tokens = 256

        context_result = Mock()
        context_result.token_estimate = 4
        context_result.compression_strategy = "none"
        context_result.compression_applied = False

        prepared = Mock()
        prepared.factory_dispatch_port = None
        prepared.__post_init__ = Mock()
        prepared.messages = [{"role": "user", "content": "hi"}]
        prepared.ai_request = Mock()
        prepared.ai_request.context = {
            "mode": "chat",
            "context_snapshot_ref": "stale-ref-that-must-not-leak",
            "context_snapshot_degraded": {"code": "STALE"},
        }
        prepared.native_tool_mode = "disabled"
        prepared.response_format_mode = "none"
        prepared.context_result = context_result
        prepared.context_os_audit = {}

        async def _empty_stream(_request, *, physical_dispatch_port=None):
            assert physical_dispatch_port is None
            return
            yield  # pragma: no cover -- intentional empty generator

        mock_executor = Mock()
        mock_executor.invoke_stream = _empty_stream
        engine._get_executor = lambda: mock_executor

        events: list[dict[str, Any]] = []
        async for event in engine.run_stream(
            profile=Mock(role_id="director"),
            prepared=prepared,
            context=context,
            start_time=0.0,
            role_id="director",
            run_id="run_x",
            task_id="task_x",
            attempt=0,
            model="claude",
            call_id="call_x",
            event_emitter=None,
            turn_round=0,
        ):
            events.append(event)

        # Stream completed without raising.
        assert any(e.get("type") == "context_metadata" for e in events)
        # No hash was injected because the store failed.
        start_metadata = emit_start.call_args.kwargs["metadata"]
        assert "context_snapshot_ref" not in start_metadata or not start_metadata["context_snapshot_ref"]
        assert prepared.ai_request.context.get("context_snapshot_ref") is None
        degraded = start_metadata["context_snapshot_degraded"]
        assert degraded["code"] == "CONTEXT_STORE_WRITE_FAILED"
        assert degraded["reason"] == "context_snapshot_store_failure"
        assert degraded["exception_type"] == "RuntimeError"
        assert start_metadata["context_snapshot_degraded_reason"] == "context_snapshot_store_failure"

        end_metadata = engine._emit_call_end.call_args.kwargs["metadata"]
        assert end_metadata["context_snapshot_degraded"]["exception_type"] == "RuntimeError"
        assert end_metadata["context_snapshot_degraded_reason"] == "context_snapshot_store_failure"

    async def test_context_os_audit_is_emitted_with_stream_metadata(self) -> None:
        """ContextOS audit should travel with stream lifecycle metadata."""
        emit_start = Mock()
        emit_end = Mock()
        engine = StreamEngine(
            workspace="/ws",
            get_executor=Mock(),
            allow_native_tool_text_fallback_fn=Mock(return_value=False),
            emit_call_start_event=emit_start,
            emit_call_error_event=Mock(),
            emit_call_end_event=emit_end,
            emit_call_retry_event=Mock(),
        )

        context = Mock()
        context.context_override = {}
        context.stream_cancelled = False
        context.temperature = 0.2
        context.max_tokens = 256

        context_result = Mock()
        context_result.token_estimate = 12
        context_result.compression_strategy = "none"
        context_result.compression_applied = False

        audit = {"ok": True, "prompt_digest": "audit1234"}
        prepared = Mock()
        prepared.factory_dispatch_port = None
        prepared.__post_init__ = Mock()
        prepared.messages = [{"role": "user", "content": "hello"}]
        prepared.ai_request = Mock()
        prepared.native_tool_mode = "disabled"
        prepared.response_format_mode = "none"
        prepared.context_result = context_result
        prepared.context_os_audit = audit

        mock_executor = Mock()

        async def _empty_stream(_request, *, physical_dispatch_port=None):
            assert physical_dispatch_port is None
            return
            yield

        mock_executor.invoke_stream = _empty_stream
        engine._get_executor = lambda: mock_executor

        events = []
        async for event in engine.run_stream(
            profile=Mock(role_id="director"),
            prepared=prepared,
            context=context,
            start_time=0.0,
            role_id="director",
            run_id="run_1",
            task_id="task_1",
            attempt=0,
            model="claude",
            call_id="call_1",
            event_emitter=None,
            turn_round=0,
        ):
            events.append(event)

        context_metadata = next(event for event in events if event.get("type") == "context_metadata")
        assert context_metadata["context_os_audit"] == audit
        assert emit_start.call_args.kwargs["metadata"]["context_os_audit"] == audit
        assert emit_end.call_args.kwargs["metadata"]["context_os_audit"] == audit

    async def test_native_tool_stream_unavailable_emits_final_request_audit(self) -> None:
        emit_error = Mock()
        engine = StreamEngine(
            workspace="/ws",
            get_executor=Mock(),
            allow_native_tool_text_fallback_fn=Mock(return_value=True),
            emit_call_start_event=Mock(),
            emit_call_error_event=emit_error,
            emit_call_end_event=Mock(),
            emit_call_retry_event=Mock(),
        )

        context = Mock()
        context.context_override = {}
        context.stream_cancelled = False
        context.temperature = 0.2
        context.max_tokens = 256

        context_result = Mock()
        context_result.token_estimate = 24
        context_result.compression_strategy = "none"
        context_result.compression_applied = False

        tool_schema = {"type": "function", "function": {"name": "write_file"}}
        ai_request = Mock()
        ai_request.context = {"chat_messages": [{"role": "user", "content": "TASK-1 target_files src/app.ts"}]}
        ai_request.options = {"tools": [tool_schema]}
        ai_request.input = ""
        prepared = PreparedLLMRequest(
            messages=[{"role": "user", "content": "TASK-1 target_files src/app.ts"}],
            input_text="TASK-1 target_files src/app.ts",
            context_result=context_result,
            context_summary="summary",
            request_options={"tools": [tool_schema], "tool_choice": "auto"},
            ai_request=ai_request,
            native_tool_schemas=[tool_schema],
            native_tool_mode="native_tools_unavailable",
            response_format_mode="plain_text",
        )

        mock_executor = Mock()

        async def _fallback_stream(_request):
            yield {"type": "chunk", "content": "fallback ok"}
            yield {"type": "complete", "content": ""}

        mock_executor.invoke_stream = _fallback_stream
        engine._get_executor = lambda: mock_executor

        profile = Mock()
        profile.role_id = "director"
        profile.max_context_tokens = 32768
        profile.tool_policy.whitelist = []

        events = []
        async for event in engine.run_stream(
            profile=profile,
            prepared=prepared,
            context=context,
            start_time=time.monotonic(),
            role_id="director",
            run_id="run_1",
            task_id="task_1",
            attempt=0,
            model="claude",
            call_id="call_1",
            event_emitter=None,
            turn_round=0,
        ):
            events.append(event)

        error_event = next(event for event in events if event["type"] == "error")
        error_audit = error_event["metadata"]["final_request_context_audit"]
        assert error_audit["tool_schema_count"] == 1
        assert error_event["metadata"]["contextTokens"] == error_audit["final_request_token_estimate"]
        error_metadata = emit_error.call_args.kwargs["metadata"]
        assert error_metadata["native_tool_calling_fallback"] is False
        assert error_metadata["final_request_context_audit"]["tool_schema_count"] == 1


async def async_empty_generator() -> Any:
    """Helper: empty async generator."""
    if False:
        yield  # Make it a generator
