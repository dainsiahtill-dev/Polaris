from __future__ import annotations

import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.cells.roles.kernel.internal.kernel import core as kernel_core
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import DeliveryContract, DeliveryMode
from polaris.cells.roles.kernel.internal.transaction.finalization import FinalizationHandler
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.turn_state_machine import TurnState, TurnStateMachine
from polaris.cells.roles.kernel.public.turn_contracts import FinalizeMode, TurnDecisionKind
from polaris.cells.roles.profile.public.service import RoleTurnResult
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

    def test_transaction_kernel_cannot_be_disabled_by_removed_env_escape_hatches(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LEGACY_FALLBACK": "true",
                "USE_TRANSACTION_KERNEL_PRIMARY": "false",
            },
        ):
            assert RoleExecutionKernel._use_transaction_kernel() is True

    def test_request_forces_no_transaction_tools_for_proposal_bridge(self) -> None:
        request = _MockRequest(
            message="[mode:propose] Return fenced file sections. Do not call tools.",
            context_override={
                "disable_internal_tool_rounds": True,
                "delivery_mode": "propose_patch",
                "_transaction_kernel_forced_tool_definitions": [],
                "_transaction_kernel_forced_tool_choice": "none",
            },
        )

        assert RoleExecutionKernel._request_forces_no_transaction_tools(request) is True

    def test_request_allows_transaction_tools_by_default(self) -> None:
        request = _MockRequest(message="Please inspect the repository.")

        assert RoleExecutionKernel._request_forces_no_transaction_tools(request) is False


class TestContextDeliveryModeMarker:
    def test_materialize_context_restores_marker_on_latest_user_message(self) -> None:
        ensure_marker = getattr(kernel_core, "_ensure_context_delivery_mode_marker", None)
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "Create worker_1.txt"},
        ]

        assert ensure_marker is not None
        result = ensure_marker(messages, {"delivery_mode": "materialize_changes"})

        assert result is not messages
        assert result[-1]["content"].startswith("[mode:materialize]\n")
        assert "Create worker_1.txt" in result[-1]["content"]
        assert messages[-1]["content"] == "Create worker_1.txt"

    def test_propose_context_leaves_messages_unchanged(self) -> None:
        ensure_marker = getattr(kernel_core, "_ensure_context_delivery_mode_marker", None)
        messages = [{"role": "user", "content": "Return fenced file sections"}]

        assert ensure_marker is not None
        assert ensure_marker(messages, {"delivery_mode": "propose_patch"}) == messages


class TestTransactionTurnId:
    def test_task_scoped_turn_id_distinguishes_concurrent_tasks(self) -> None:
        resolve_turn_id = getattr(kernel_core, "_resolve_transaction_turn_id", None)

        assert resolve_turn_id is not None
        first = resolve_turn_id(_MockRequest(run_id="run-1", task_id="D4-SAT-1"), "run-1")
        second = resolve_turn_id(_MockRequest(run_id="run-1", task_id="D4-SAT-2"), "run-1")

        assert first != second
        assert first.startswith("run-1")
        assert second.startswith("run-1")
        assert "D4-SAT-1" in first
        assert "D4-SAT-2" in second


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

    def test_create_transaction_kernel_carries_request_workspace(self, tmp_path: Any) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = _MockProfile(role_id="director")
        request = _MockRequest(
            message="implement the target project",
            run_id="run_123",
            workspace=str(tmp_path),
        )

        tk = kernel._create_transaction_kernel("director", profile, request)

        assert tk.config.workspace == str(tmp_path)

    def test_create_transaction_kernel_carries_role_id(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = _MockProfile(role_id="chief_engineer")
        request = _MockRequest(
            message="Chief Engineer output contract: return exactly one JSON object.",
            run_id="run_123",
            workspace=".",
        )

        tk = kernel._create_transaction_kernel("chief_engineer", profile, request)

        assert tk.config.role_id == "chief_engineer"
        assert tk.config.domain == "code"
        assert tk.config.mutation_guard_mode == "warn"

    @pytest.mark.asyncio
    async def test_provider_preserves_explicit_empty_forced_tools_override(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = _MockProfile(role_id="director", model="base-model")
        request = _MockRequest(
            message="[mode:propose] Do not call tools.",
            run_id="run_123",
            context_override={
                "_transaction_kernel_forced_tool_definitions": [],
                "_transaction_kernel_forced_tool_choice": "none",
            },
        )

        captured_contexts: list[Any] = []

        async def _fake_call_decision(*, context: Any, **_kwargs: Any) -> dict[str, Any]:
            captured_contexts.append(context)
            return {"content": "```file: README.md\nok\n```", "tool_calls": []}

        kernel.inject_llm_caller(SimpleNamespace(call_decision=_fake_call_decision))
        tk = kernel._create_transaction_kernel("director", profile, request)

        response = await tk.llm_provider(
            {
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": request.message},
                ],
                "tools": [{"type": "function", "function": {"name": "read_file"}}],
                "tool_choice": "auto",
            }
        )

        assert response["content"].startswith("```file:")
        assert len(captured_contexts) == 1
        context_override = getattr(captured_contexts[0], "context_override", None)
        assert isinstance(context_override, dict)
        assert context_override["_transaction_kernel_forced_tool_definitions"] == []
        assert context_override["_transaction_kernel_forced_tool_choice"] == "none"

    @pytest.mark.asyncio
    async def test_provider_preserves_existing_forced_write_tool_choice_over_auto(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = _MockProfile(role_id="director", model="base-model")
        forced_write_tool = {
            "type": "function",
            "function": {
                "name": "write_file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"],
                },
            },
        }
        request = _MockRequest(
            message="[mode:materialize]\nCreate the missing target file.",
            run_id="run_123",
            context_override={
                "context_os_snapshot": {},
                "_transaction_kernel_forced_tool_definitions": [forced_write_tool],
                "_transaction_kernel_forced_tool_choice": {
                    "type": "function",
                    "function": {"name": "write_file"},
                },
            },
        )

        captured_contexts: list[Any] = []

        async def _fake_call_decision(*, context: Any, **_kwargs: Any) -> dict[str, Any]:
            captured_contexts.append(context)
            return {"content": "", "tool_calls": []}

        kernel.inject_llm_caller(SimpleNamespace(call_decision=_fake_call_decision))
        tk = kernel._create_transaction_kernel("director", profile, request)

        response = await tk.llm_provider(
            {
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": request.message},
                ],
                "tools": [
                    forced_write_tool,
                    {"type": "function", "function": {"name": "read_file"}},
                ],
                "tool_choice": "auto",
            }
        )

        assert response["tool_calls"] == []
        assert len(captured_contexts) == 1
        context_override = getattr(captured_contexts[0], "context_override", None)
        assert isinstance(context_override, dict)
        assert context_override["_transaction_kernel_forced_tool_definitions"] == [forced_write_tool]
        assert context_override["_transaction_kernel_forced_tool_choice"] == {
            "type": "function",
            "function": {"name": "write_file"},
        }


class TestExecuteTransactionKernelTurn:
    @pytest.mark.asyncio
    async def test_execute_transaction_kernel_turn_uses_forced_tool_definitions(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = _MockProfile(
            role_id="director",
            tool_policy=MagicMock(policy_id="tp1", whitelist=["read_file", "write_file"]),
        )
        forced_write_tool = {
            "type": "function",
            "function": {
                "name": "write_file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"],
                },
            },
        }
        request = _MockRequest(
            message="[mode:materialize]\nCreate the missing target file.",
            run_id="run_123",
            context_override={
                "context_os_snapshot": {},
                "_transaction_kernel_forced_tool_definitions": [forced_write_tool],
                "_transaction_kernel_forced_tool_choice": {
                    "type": "function",
                    "function": {"name": "write_file"},
                },
            },
        )
        fingerprint = _MockFingerprint()
        mock_execute = AsyncMock(
            return_value={
                "turn_id": "turn_abc",
                "kind": "final_answer",
                "visible_content": "ok",
                "metrics": {"duration_ms": 100, "llm_calls": 1, "tool_calls": 0},
            }
        )
        context_gateway = MagicMock(
            build_context=AsyncMock(
                return_value=SimpleNamespace(
                    messages=[{"role": "user", "content": request.message}],
                    token_estimate=37,
                    metadata={},
                )
            ),
            record_projection_outcome=MagicMock(return_value={"route_weight": 0.31}),
        )

        with (
            patch.object(kernel, "_create_transaction_kernel", return_value=MagicMock(execute=mock_execute)),
            patch(
                "polaris.cells.roles.kernel.public.service.RoleContextGateway",
                return_value=context_gateway,
            ),
        ):
            result = await kernel._execute_transaction_kernel_turn(
                role="director",
                profile=profile,
                request=request,
                system_prompt="You are a Director",
                fingerprint=fingerprint,
                observer_run_id="run_123",
                response_schema=None,
            )

        assert result.content == "ok"
        assert mock_execute.await_args is not None
        assert mock_execute.await_args.args[2] == [forced_write_tool]

    @pytest.mark.asyncio
    async def test_execute_transaction_kernel_turn_slims_tools_for_qwen_director_materialize(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = _MockProfile(
            role_id="director",
            model="qwen3.6-27b-code-gpu0",
            tool_policy=MagicMock(
                policy_id="tp1",
                whitelist=["read_file", "repo_rg", "write_file", "edit_file", "execute_command"],
            ),
        )
        request = _MockRequest(
            message="[mode:materialize]\nCreate the missing target file.",
            run_id="run_123",
            context_override={
                "context_os_snapshot": {},
                "delivery_mode": "materialize_changes",
            },
        )
        fingerprint = _MockFingerprint()
        mock_execute = AsyncMock(
            return_value={
                "turn_id": "turn_abc",
                "kind": "final_answer",
                "visible_content": "ok",
                "metrics": {"duration_ms": 100, "llm_calls": 1, "tool_calls": 0},
            }
        )
        context_gateway = MagicMock(
            build_context=AsyncMock(
                return_value=SimpleNamespace(
                    messages=[{"role": "user", "content": request.message}],
                    token_estimate=37,
                    metadata={},
                )
            ),
            record_projection_outcome=MagicMock(return_value={"route_weight": 0.31}),
        )
        full_tool_definitions = [
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "repo_rg"}},
            {"type": "function", "function": {"name": "write_file"}},
            {"type": "function", "function": {"name": "edit_file"}},
            {"type": "function", "function": {"name": "execute_command"}},
        ]

        with (
            patch.object(kernel, "_create_transaction_kernel", return_value=MagicMock(execute=mock_execute)),
            patch(
                "polaris.cells.roles.kernel.public.service.RoleContextGateway",
                return_value=context_gateway,
            ),
            patch(
                "polaris.cells.roles.kernel.internal.llm_caller.tool_helpers.build_native_tool_schemas",
                return_value=full_tool_definitions,
            ),
        ):
            result = await kernel._execute_transaction_kernel_turn(
                role="director",
                profile=profile,
                request=request,
                system_prompt="You are a Director",
                fingerprint=fingerprint,
                observer_run_id="run_123",
                response_schema=None,
            )

        assert result.content == "ok"
        assert mock_execute.await_args is not None
        tool_names = {definition["function"]["name"] for definition in mock_execute.await_args.args[2]}
        assert tool_names == {"write_file", "edit_file", "execute_command"}

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
        context_gateway = MagicMock(
            build_context=AsyncMock(
                return_value=SimpleNamespace(
                    messages=[{"role": "user", "content": "hi"}],
                    token_estimate=37,
                    metadata={},
                )
            ),
            record_projection_outcome=MagicMock(return_value={"route_weight": 0.31}),
        )

        with (
            patch.object(
                kernel,
                "_create_transaction_kernel",
                return_value=MagicMock(execute=AsyncMock(return_value=mock_tk_result)),
            ) as mock_create_tk,
            patch(
                "polaris.cells.roles.kernel.public.service.RoleContextGateway",
                return_value=context_gateway,
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
        context_gateway.record_projection_outcome.assert_called_once_with(success=True, tokens_used=37)
        assert result.metadata["projection_adaptive_weights_after_turn"] == {"route_weight": 0.31}

    @pytest.mark.asyncio
    async def test_execute_transaction_kernel_turn_exposes_context_os_audit_metadata(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = _MockProfile(role_id="pm")
        request = _MockRequest(run_id="run_123")
        fingerprint = _MockFingerprint()
        audit = {
            "ok": True,
            "expected": True,
            "source": "state_first_context_os.project_messages",
            "prompt_digest": "abc123",
            "message_count": 2,
            "role_counts": {"system": 1, "user": 1},
            "final_role": "user",
            "control_plane": {
                "isolated": True,
                "content_hits": [],
                "metadata_key_hits": [],
            },
            "requirements": {
                "context_os_expected": True,
                "current_user_instruction_preserved": True,
            },
        }
        ledger = TurnLedger("turn_abc")
        ledger.record_llm_call(
            phase="decision",
            model="test-model",
            tokens_in=10,
            tokens_out=3,
            metadata={"context_os_audit": audit},
        )
        mock_tk_result = {
            "turn_id": "turn_abc",
            "kind": "final_answer",
            "visible_content": "Hello from TK",
            "ledger": ledger,
            "metrics": {"duration_ms": 100, "llm_calls": 1, "tool_calls": 0},
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
                role="pm",
                profile=profile,
                request=request,
                system_prompt="sys",
                fingerprint=fingerprint,
                observer_run_id="run_123",
                response_schema=None,
            )

        context_os_audit = result.metadata.get("context_os_audit")
        assert context_os_audit is not None
        assert context_os_audit["ok"] is True
        assert context_os_audit["llm_call_count"] == 1
        assert context_os_audit["latest"]["prompt_digest"] == "abc123"
        assert context_os_audit["latest"]["control_plane"]["isolated"] is True

    @pytest.mark.asyncio
    async def test_run_preserves_transaction_context_os_audit_metadata(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = _MockProfile(role_id="pm")
        kernel.registry = MagicMock(get_profile_or_raise=MagicMock(return_value=profile))
        prompt_builder = SimpleNamespace(
            build_system_prompt=lambda _profile, _appendix, **_kwargs: "system-prompt",
            build_fingerprint=lambda _profile, _appendix: _MockFingerprint(),
            build_retry_prompt=lambda _system_prompt, _quality_result, _attempt: "retry-prompt",
        )
        kernel._prompt_builder = prompt_builder  # type: ignore[assignment]
        kernel._get_prompt_builder = lambda: prompt_builder  # type: ignore[method-assign]
        expected_metadata = {
            "context_os_audit": {
                "ok": True,
                "llm_call_count": 1,
                "latest": {
                    "prompt_digest": "abc123",
                    "control_plane": {"isolated": True},
                },
            }
        }
        transaction_result = RoleTurnResult(
            content="done",
            is_complete=True,
            execution_stats={"transaction_kernel": True},
            metadata=expected_metadata,
        )

        with (
            patch.object(kernel, "_build_context", return_value=MagicMock()),
            patch.object(
                kernel,
                "_execute_transaction_kernel_turn",
                new=AsyncMock(return_value=transaction_result),
            ),
        ):
            result = await kernel.run("pm", _MockRequest(run_id="run_123", validate_output=False))

        assert result.content == "done"
        assert result.metadata == expected_metadata

    @pytest.mark.asyncio
    async def test_execute_transaction_kernel_turn_hides_tools_for_proposal_bridge(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = _MockProfile(
            role_id="director",
            tool_policy=MagicMock(policy_id="tp1", whitelist=["write_file", "append_to_file"]),
        )
        request = _MockRequest(
            message="[mode:propose] Return fenced file sections. Do not call tools.",
            run_id="run_123",
            context_override={
                "context_os_snapshot": {},
                "disable_internal_tool_rounds": True,
                "delivery_mode": "propose_patch",
                "_transaction_kernel_forced_tool_definitions": [],
                "_transaction_kernel_forced_tool_choice": "none",
            },
        )
        fingerprint = _MockFingerprint()
        mock_execute = AsyncMock(
            return_value={
                "turn_id": "turn_abc",
                "kind": "final_answer",
                "visible_content": "```file: package.json\n{}\n```",
                "metrics": {"duration_ms": 100, "llm_calls": 1, "tool_calls": 0},
            }
        )

        with (
            patch.object(kernel, "_create_transaction_kernel", return_value=MagicMock(execute=mock_execute)),
            patch(
                "polaris.cells.roles.kernel.public.service.RoleContextGateway",
                return_value=MagicMock(
                    build_context=AsyncMock(return_value=MagicMock(messages=[{"role": "user", "content": "hi"}]))
                ),
            ),
        ):
            result = await kernel._execute_transaction_kernel_turn(
                role="director",
                profile=profile,
                request=request,
                system_prompt="You are a Director",
                fingerprint=fingerprint,
                observer_run_id="run_123",
                response_schema=None,
            )

        assert result.content.startswith("```file:")
        assert mock_execute.await_args is not None
        assert mock_execute.await_args.args[2] == []

    @pytest.mark.asyncio
    async def test_execute_transaction_kernel_turn_restores_materialize_marker_from_request_message(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = _MockProfile(
            role_id="director",
            tool_policy=MagicMock(policy_id="tp1", whitelist=["write_file", "read_file"]),
        )
        request = _MockRequest(
            message="[mode:materialize]\nCreate worker_1.txt with D4-SAT-1",
            run_id="run_123",
            context_override={"context_os_snapshot": {}},
        )
        fingerprint = _MockFingerprint()
        mock_execute = AsyncMock(
            return_value={
                "turn_id": "turn_abc",
                "kind": "final_answer",
                "visible_content": "ok",
                "metrics": {"duration_ms": 100, "llm_calls": 1, "tool_calls": 0},
            }
        )

        with (
            patch.object(kernel, "_create_transaction_kernel", return_value=MagicMock(execute=mock_execute)),
            patch(
                "polaris.cells.roles.kernel.public.service.RoleContextGateway",
                return_value=MagicMock(
                    build_context=AsyncMock(
                        return_value=MagicMock(messages=[{"role": "user", "content": "Create worker_1.txt"}])
                    )
                ),
            ),
        ):
            await kernel._execute_transaction_kernel_turn(
                role="director",
                profile=profile,
                request=request,
                system_prompt="You are a Director",
                fingerprint=fingerprint,
                observer_run_id="run_123",
                response_schema=None,
            )

        assert mock_execute.await_args is not None
        passed_messages = mock_execute.await_args.args[1]
        assert passed_messages[-1]["content"].startswith("[mode:materialize]\n")

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
        assert result.batch_receipt == mock_tk_result["batch_receipt"]

    @pytest.mark.asyncio
    async def test_execute_transaction_kernel_turn_preserves_followup_workflow(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = _MockProfile(role_id="director")
        request = _MockRequest(run_id="run_123")
        fingerprint = _MockFingerprint()

        mock_tk_result = {
            "turn_id": "turn_tools",
            "kind": "mutation_bypass_blocked",
            "visible_content": "[MUTATION_CONTINUE] no write receipt",
            "batch_receipt": {
                "results": [
                    {"tool_name": "read_file", "call_id": "c1", "status": "success", "result": "file content"},
                ],
            },
            "finalization": {
                "mode": "blocked",
                "blocked_reason": "no_write_tool_available",
                "blocked_detail": "MATERIALIZE_CHANGES requires write receipts.",
                "needs_followup_workflow": True,
                "workflow_reason": "mutation_bypass_blocked",
            },
            "metrics": {"duration_ms": 200, "llm_calls": 1, "tool_calls": 1},
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

        assert result.is_complete is False
        assert result.error == "no_write_tool_available"
        assert result.metadata["needs_followup_workflow"] is True
        assert result.metadata["workflow_reason"] == "mutation_bypass_blocked"
        assert len(result.tool_calls) == 1

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


class TestFinalizationMaterializationGate:
    @pytest.mark.asyncio
    async def test_llm_once_blocks_materialize_without_write_receipt(self) -> None:
        async def _llm_provider(_payload: dict[str, Any]) -> dict[str, Any]:
            return {
                "content": "",
                "thinking": None,
                "tool_calls": [],
                "model": "stub-model",
                "usage": {"prompt_tokens": 12, "completion_tokens": 0},
            }

        handler = FinalizationHandler(
            llm_provider=_llm_provider,
            decoder=SimpleNamespace(
                decode_for_finalization=lambda *_args, **_kwargs: {
                    "kind": TurnDecisionKind.FINAL_ANSWER,
                }
            ),
            emit_event=lambda _event: None,
            guard_assert_no_finalization_tool_calls=lambda **_kwargs: None,
        )
        ledger = TurnLedger(turn_id="turn_no_write")
        ledger.set_delivery_contract(
            DeliveryContract(
                mode=DeliveryMode.MATERIALIZE_CHANGES,
                requires_mutation=True,
                allow_inline_code=False,
                allow_patch_proposal=False,
            )
        )
        state_machine = TurnStateMachine(turn_id="turn_no_write")
        for state in (
            TurnState.CONTEXT_BUILT,
            TurnState.DECISION_REQUESTED,
            TurnState.DECISION_RECEIVED,
            TurnState.DECISION_DECODED,
            TurnState.TOOL_BATCH_EXECUTING,
            TurnState.TOOL_BATCH_EXECUTED,
        ):
            state_machine.transition_to(state)

        result = await handler.execute_llm_once(
            {
                "turn_id": "turn_no_write",
                "kind": TurnDecisionKind.TOOL_BATCH,
                "finalize_mode": FinalizeMode.LLM_ONCE,
            },
            [{"results": [{"tool_name": "read_file", "status": "success", "result": "content"}]}],
            state_machine,
            ledger,
            [{"role": "user", "content": "实现 app.py 并写入代码"}],
        )

        assert result["kind"] == "mutation_bypass_blocked"
        assert result["finalization"]["needs_followup_workflow"] is True
        assert result["finalization"]["blocked_reason"] == "no_write_tool_available"
