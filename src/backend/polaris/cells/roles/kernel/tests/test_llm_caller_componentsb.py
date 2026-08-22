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
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from polaris.cells.roles.kernel.internal import context_gateway as context_gateway_module
from polaris.cells.roles.kernel.internal.llm_caller import request_preparer as request_preparer_module
from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
    build_final_request_context_audit,
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
)
from polaris.cells.roles.kernel.internal.llm_caller.provider_formatter import (
    AnnotatedProviderFormatter,
    NativeProviderFormatter,
    create_formatter,
)
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import (
    LLMRequestPreparer,
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

    async def test_invalid_structured_result_emits_terminal_call_error_before_consumer_projection(self) -> None:
        """Provider result-schema drift must close the physical LLM attempt.

        R107 returned a forced result tool that violated the caller schema.
        Validation occurred only in the downstream TransactionKernel projector,
        which raised after the LLM stream had yielded the tool call. The
        physical attempt therefore retained only ``llm_call_start`` and the
        bounded CE schema-repair path could not classify the invalid payload.

        Empty required arrays are now safely defaulted by the transport, so use
        a non-JSON scalar for an object field to preserve the original
        fail-closed lifecycle assertion.
        """

        contract = RoleStructuredOutputContractV1(
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
        plan = resolve_structured_output_transport(
            {STRUCTURED_OUTPUT_CONTRACT_CONTEXT_KEY: contract.to_context_projection()}
        )
        assert plan is not None
        prepared = PreparedLLMRequest(
            messages=[{"role": "user", "content": "Build the CE portfolio."}],
            input_text="Build the CE portfolio.",
            context_result=SimpleNamespace(
                token_estimate=24,
                compression_strategy="none",
                compression_applied=False,
            ),
            context_summary="summary",
            request_options={},
            ai_request=SimpleNamespace(context={}, options={}, input=""),
            native_tool_schemas=[plan.tool_definition],
            native_tool_mode="native_tools_streaming",
            response_format_mode="provider_tool_json_schema",
        )
        prepared.structured_output_transport = plan
        context = SimpleNamespace(
            context_override={"stream_max_reconnects": 0},
            stream_cancelled=False,
            temperature=0.2,
            max_tokens=16_384,
        )
        emit_error = Mock()

        class _Executor:
            async def invoke_stream(self, _request: object):
                yield {
                    "type": "tool_call",
                    "tool_call": {
                        "id": "call-invalid-structured-result",
                        "name": STRUCTURED_OUTPUT_TOOL_NAME,
                        "arguments": {"construction_plan": "not-an-object", "risk_flags": []},
                        "provider_meta": {
                            "provider": "anthropic_compat",
                            "content_block_index": 0,
                            "assembly": {
                                "argument_source": "complete_snapshot",
                                "delta_count": 1,
                            },
                        },
                    },
                }
                yield {"type": "complete", "content": ""}

        engine = StreamEngine(
            workspace="/ws",
            get_executor=lambda: _Executor(),
            allow_native_tool_text_fallback_fn=Mock(return_value=False),
            emit_call_start_event=Mock(),
            emit_call_error_event=emit_error,
            emit_call_end_event=Mock(),
            emit_call_retry_event=Mock(),
        )

        with (
            patch(
                "polaris.cells.roles.kernel.internal.llm_caller.stream_engine."
                "build_final_request_context_audit_for_request",
                return_value={"final_request_token_estimate": 1},
            ),
            patch(
                "polaris.cells.roles.kernel.internal.llm_caller.stream_engine."
                "enforce_factory_aware_final_request_evidence_coverage"
            ),
            patch(
                "polaris.cells.roles.kernel.internal.llm_caller.stream_engine."
                "assert_tool_in_final_request_surface"
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
                    run_id="run-invalid-structured-result",
                    task_id="CE-PORTFOLIO-run-invalid-structured-result",
                    attempt=0,
                    model="model-a",
                    call_id="call-a",
                    event_emitter=None,
                    turn_round=0,
                )
            ]

        assert [event["type"] for event in events] == ["error"]
        assert events[0]["error"].startswith("structured_output_payload_schema_mismatch:construction_plan:")
        assert "is not of type 'object'" in events[0]["error"]
        assert events[0]["metadata"]["tool_call_assembly"] == {
            "provider": "anthropic_compat",
            "content_block_index": 0,
            "assembly": {
                "argument_source": "complete_snapshot",
                "delta_count": 1,
            },
        }
        emit_error.assert_called_once()
        assert emit_error.call_args.kwargs["error_message"] == events[0]["error"]
        assert emit_error.call_args.kwargs["metadata"] == events[0]["metadata"]

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
        prepared.ai_request.options = {
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "write_file", "parameters": {"type": "object"}},
                }
            ]
        }
        prepared.request_options = prepared.ai_request.options
        prepared.native_tool_schemas = prepared.ai_request.options["tools"]
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
