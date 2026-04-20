from __future__ import annotations

import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.domain.cognitive_runtime.models import ContextHandoffPack, TurnEnvelope


@dataclass
class _MockProfile:
    role_id: str = "director"
    version: str = "1.0"
    model: str = "test-model"
    provider_id: str = "openai"
    tool_policy: Any = field(default_factory=lambda: MagicMock(policy_id="tp1", whitelist=["read_file"]))


@dataclass
class _MockFingerprint:
    full_hash: str = "abc123"


@dataclass
class _MockRequest:
    message: str = "hello"
    history: list[tuple[str, str]] = field(default_factory=list)
    max_retries: int = 0
    validate_output: bool = False
    task_id: str | None = None
    run_id: str | None = "run_123"
    workspace: str = "."
    prompt_appendix: str = ""
    system_prompt: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    context_override: dict[str, Any] | None = field(default_factory=lambda: {"context_os_snapshot": {}})
    tool_results: list[dict[str, Any]] = field(default_factory=list)


class TestTransactionKernelFeatureFlag:
    def test_use_transaction_kernel_default_true(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert RoleExecutionKernel._use_transaction_kernel() is True

    def test_use_transaction_kernel_true_env(self) -> None:
        with patch.dict(os.environ, {"USE_TRANSACTION_KERNEL_PRIMARY": "true"}):
            assert RoleExecutionKernel._use_transaction_kernel() is True
        with patch.dict(os.environ, {"USE_TRANSACTION_KERNEL_PRIMARY": "1"}):
            assert RoleExecutionKernel._use_transaction_kernel() is True
        with patch.dict(os.environ, {"USE_TRANSACTION_KERNEL_PRIMARY": "yes"}):
            assert RoleExecutionKernel._use_transaction_kernel() is True

    def test_legacy_fallback_escape_hatch(self) -> None:
        with patch.dict(os.environ, {"LEGACY_FALLBACK": "true"}):
            assert RoleExecutionKernel._use_transaction_kernel() is False


class TestContextHandoffPackMapping:
    def test_build_context_handoff_pack_maps_workflow_context(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        request = _MockRequest(run_id="run_123", task_id="task_456")

        turn_result = {
            "turn_id": "turn_789",
            "kind": "handoff_workflow",
            "visible_content": "handoff",
            "workflow_context": {
                "handoff_reason": "async_operation",
                "recoverable_context": {
                    "decision": {
                        "metadata": {
                            "current_goal": "explore codebase",
                            "run_card": {"priority": "high"},
                        }
                    },
                    "batch_receipts": [
                        {"batch_id": "batch_1"},
                        {"batch_id": "batch_2"},
                    ],
                },
            },
        }

        pack = kernel._build_context_handoff_pack(turn_result, "director", request)

        assert isinstance(pack, ContextHandoffPack)
        assert pack.workspace == "."
        assert pack.session_id == "task_456"
        assert pack.run_id == "run_123"
        assert pack.reason == "async_operation"
        assert pack.current_goal == "explore codebase"
        assert pack.run_card == {"priority": "high"}
        assert pack.receipt_refs == ("batch_1", "batch_2")
        assert isinstance(pack.turn_envelope, TurnEnvelope)
        assert pack.turn_envelope.turn_id == "turn_789"
        assert pack.turn_envelope.role == "director"


class TestTransactionKernelPrebuiltContextPassThrough:
    @pytest.mark.asyncio
    async def test_stream_provider_passes_prebuilt_messages_to_context_override(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = _MockProfile(role_id="director")
        request = _MockRequest(message="hello", run_id="run_123")

        captured_contexts: list[Any] = []

        async def _fake_call(*_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(content="", tool_calls=[], error=None, metadata={})

        async def _fake_call_stream(*, context: Any, **_kwargs: Any):
            captured_contexts.append(context)
            if False:
                yield {}  # pragma: no cover

        kernel.inject_llm_caller(
            SimpleNamespace(
                call=_fake_call,
                call_stream=_fake_call_stream,
            )
        )

        tk = kernel._create_transaction_kernel("director", profile, request)
        assert tk.llm_provider_stream is not None

        async for _ in tk.llm_provider_stream(
            {
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "hello"},
                ]
            }
        ):
            pass

        assert len(captured_contexts) == 1
        context_override = getattr(captured_contexts[0], "context_override", None)
        assert isinstance(context_override, dict)
        prebuilt = context_override.get("_transaction_kernel_prebuilt_messages")
        assert isinstance(prebuilt, list)
        assert prebuilt[0] == {"role": "system", "content": "sys"}
        assert prebuilt[1] == {"role": "user", "content": "hello"}

    @pytest.mark.asyncio
    async def test_provider_passes_model_override_into_effective_profile(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = _MockProfile(role_id="director", model="base-model")
        request = _MockRequest(message="hello", run_id="run_123")

        captured_models: list[str] = []

        async def _fake_call(*, profile: Any, **_kwargs: Any) -> Any:
            captured_models.append(str(getattr(profile, "model", "") or ""))
            return SimpleNamespace(content="ok", tool_calls=[], error=None, metadata={})

        kernel.inject_llm_caller(SimpleNamespace(call=_fake_call))
        tk = kernel._create_transaction_kernel("director", profile, request)

        response = await tk.llm_provider(
            {
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "hello"},
                ],
                "model_override": "override-model",
            }
        )

        assert isinstance(response, dict)
        assert captured_models == ["override-model"]


class TestExecuteTransactionKernelTurn:
    @pytest.mark.asyncio
    async def test_execute_transaction_kernel_turn_returns_role_turn_result(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = _MockProfile(role_id="pm")
        request = _MockRequest(run_id="run_123")
        fingerprint = _MockFingerprint()

        mock_tk_result = {
            "turn_id": "turn_abc",
            "kind": "final_answer",
            "visible_content": "Hello from TK",
            "metrics": {"duration_ms": 100, "llm_calls": 1, "tool_calls": 0},
        }

        with (
            patch.object(
                kernel,
                "_create_transaction_kernel",
                return_value=MagicMock(execute=AsyncMock(return_value=mock_tk_result)),
            ) as mock_create_tk,
            patch(
                "polaris.cells.roles.kernel.public.service.RoleContextGateway",
                return_value=MagicMock(
                    build_context=AsyncMock(return_value=MagicMock(messages=[{"role": "user", "content": "hi"}]))
                ),
            ),
        ):
            result = await kernel._execute_transaction_kernel_turn(
                role="pm",
                profile=profile,
                request=request,
                system_prompt="You are a PM",
                fingerprint=fingerprint,
                observer_run_id="run_123",
                response_schema=None,
            )

        mock_create_tk.assert_called_once()
        assert result.content == "Hello from TK"
        assert result.is_complete is True
        assert result.execution_stats.get("transaction_kernel") is True

    @pytest.mark.asyncio
    async def test_execute_transaction_kernel_turn_handoff_populates_metadata(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = _MockProfile(role_id="director")
        request = _MockRequest(run_id="run_123")
        fingerprint = _MockFingerprint()

        mock_tk_result = {
            "turn_id": "turn_handoff",
            "kind": "handoff_workflow",
            "visible_content": "[HANDOFF]",
            "workflow_context": {
                "handoff_reason": "exploration",
                "recoverable_context": {
                    "decision": {"metadata": {}},
                    "batch_receipts": [],
                },
            },
            "metrics": {"duration_ms": 50, "llm_calls": 1, "tool_calls": 0},
        }

        with (
            patch.object(
                kernel,
                "_create_transaction_kernel",
                return_value=MagicMock(execute=AsyncMock(return_value=mock_tk_result)),
            ),
            patch(
                "polaris.cells.roles.kernel.public.service.RoleContextGateway",
                return_value=MagicMock(build_context=AsyncMock(return_value=MagicMock(messages=[]))),
            ),
        ):
            result = await kernel._execute_transaction_kernel_turn(
                role="director",
                profile=profile,
                request=request,
                system_prompt="sys",
                fingerprint=fingerprint,
                observer_run_id="run_123",
                response_schema=None,
            )

        assert result.metadata.get("transaction_kind") == "handoff_workflow"
        assert "handoff_pack" in result.metadata
        handoff_pack = ContextHandoffPack.from_mapping(result.metadata["handoff_pack"])
        assert handoff_pack is not None
        assert handoff_pack.reason == "exploration"

    @pytest.mark.asyncio
    async def test_execute_transaction_kernel_turn_maps_tool_results(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = _MockProfile(role_id="director")
        request = _MockRequest(run_id="run_123")
        fingerprint = _MockFingerprint()

        mock_tk_result = {
            "turn_id": "turn_tools",
            "kind": "tool_batch_with_receipt",
            "visible_content": "Tool results",
            "batch_receipt": {
                "results": [
                    {"tool_name": "read_file", "call_id": "c1", "status": "success", "result": "file content"},
                    {"tool_name": "grep", "call_id": "c2", "status": "error", "result": None},
                ],
            },
            "metrics": {"duration_ms": 200, "llm_calls": 1, "tool_calls": 2},
        }

        with (
            patch.object(
                kernel,
                "_create_transaction_kernel",
                return_value=MagicMock(execute=AsyncMock(return_value=mock_tk_result)),
            ),
            patch(
                "polaris.cells.roles.kernel.public.service.RoleContextGateway",
                return_value=MagicMock(build_context=AsyncMock(return_value=MagicMock(messages=[]))),
            ),
        ):
            result = await kernel._execute_transaction_kernel_turn(
                role="director",
                profile=profile,
                request=request,
                system_prompt="sys",
                fingerprint=fingerprint,
                observer_run_id="run_123",
                response_schema=None,
            )

        assert len(result.tool_calls) == 2
        assert len(result.tool_results) == 2
        assert result.tool_results[0]["success"] is True
        assert result.tool_results[1]["success"] is False

    @pytest.mark.asyncio
    async def test_execute_transaction_kernel_turn_failure_returns_error_result(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = _MockProfile(role_id="pm")
        request = _MockRequest(run_id="run_123")
        fingerprint = _MockFingerprint()

        with (
            patch.object(
                kernel,
                "_create_transaction_kernel",
                return_value=MagicMock(execute=AsyncMock(side_effect=RuntimeError("TK boom"))),
            ),
            patch(
                "polaris.cells.roles.kernel.public.service.RoleContextGateway",
                return_value=MagicMock(build_context=AsyncMock(return_value=MagicMock(messages=[]))),
            ),
        ):
            result = await kernel._execute_transaction_kernel_turn(
                role="pm",
                profile=profile,
                request=request,
                system_prompt="sys",
                fingerprint=fingerprint,
                observer_run_id="run_123",
                response_schema=None,
            )

        assert result.error is not None
        assert "TransactionKernel execution failed" in result.error
        assert result.is_complete is False
