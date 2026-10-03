"""Exercise actual setup/factory ordering, not a pre-filled budget fixture."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.kernel.transaction_turn_executor import TransactionTurnExecutor
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptHeartbeatVerdictV1,
    TaskRuntimeExecutionAttemptSettlementVerdictV1,
)
from polaris.kernelone.llm.engine.provider_native_request import project_factory_provider_native_request

from .test_directed_effect_runtime_wiring import _attempt, _dependencies
from .test_role_kernel_transaction_wiring import _execute_bound_turn, _MockFingerprint, _MockRequest
from .test_role_turn_request_fact_projection import _profile


class _CaptureCompleteError(RuntimeError):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_actual_setup_budget_reaches_llm_snapshot_and_request_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stream: bool
) -> None:
    monkeypatch.setenv("KERNELONE_DIRECTOR_FORCED_WRITE_OUTPUT_TOKENS", "7000")
    captured = []
    profile = _profile("director")
    profile.tool_policy = SimpleNamespace(whitelist=("write_file",))

    class _Invoker:
        async def call_decision(self, *, context, **kwargs):
            prepared = await LLMRequestPreparer(workspace=str(tmp_path))._prepare_llm_request(
                profile=profile,
                system_prompt=kwargs["system_prompt"],
                context=context,
                temperature=0.2,
                max_tokens=128000,
                stream=stream,
            )
            captured.append(prepared)
            raise _CaptureCompleteError("no model or tool effect in ordering test")

        async def call_stream(self, *, context, **kwargs):
            await self.call_decision(context=context, **kwargs)
            if False:
                yield {}

    kernel = RoleExecutionKernel.create_default(workspace=str(tmp_path), llm_invoker=_Invoker())
    request = _MockRequest(
        message="[mode:materialize] Create main.go using the supplied blueprint.",
        workspace=str(tmp_path),
        context_override={
            "context_os_snapshot": {},
            "target_files": ["main.go"],
            "delivery_mode": "materialize_changes",
            "llm_max_tokens": 128000,
            "max_output_tokens": 128000,
        },
    )
    gateway = MagicMock(
        build_context=AsyncMock(
            return_value=SimpleNamespace(
                messages=[{"role": "user", "content": request.message}],
                token_estimate=30,
                metadata={},
            )
        ),
        record_projection_outcome=MagicMock(return_value={}),
    )
    with patch("polaris.cells.roles.kernel.public.service.RoleContextGateway", return_value=gateway):
        if stream:
            with pytest.raises(_CaptureCompleteError):
                _ = [
                    event
                    async for event in TransactionTurnExecutor(kernel).execute_stream(
                        role="director",
                        profile=profile,
                        request=request,
                        system_prompt="You are Director.",
                        fingerprint=_MockFingerprint(),
                        stream_run_id="run_123",
                        uep_publisher=AsyncMock(),
                    )
                ]
        else:
            await _execute_bound_turn(
                TransactionTurnExecutor(kernel),
                role="director",
                profile=profile,
                request=request,
                system_prompt="You are Director.",
                fingerprint=_MockFingerprint(),
                observer_run_id="run_123",
                response_schema=None,
            )

    assert request.context_override["llm_max_tokens"] == 7000
    assert captured
    assert captured[0].request_options["max_tokens"] == 7000
    assert captured[0].ai_request.options["max_tokens"] == 7000
    prepared = captured[0]
    native = project_factory_provider_native_request(
        provider_type="openai_compat",
        mode="stream" if stream else "invoke",
        provider_config={"base_url": "https://provider.test/v1"},
        final_payload={
            "model": profile.model,
            "messages": prepared.messages,
            "max_tokens": prepared.ai_request.options["max_tokens"],
            "temperature": prepared.ai_request.options["temperature"],
            "tools": prepared.native_tool_schemas,
            "tool_choice": prepared.request_options.get("tool_choice"),
            "response_format": prepared.native_response_format,
            "stream": stream,
        },
    )
    assert native is not None
    assert native.authority()["expected_body"]["max_tokens"] == 7000


@pytest.mark.asyncio
async def test_required_deo_rejection_precedes_context_assembly(tmp_path: Path) -> None:
    kernel = RoleExecutionKernel.create_default(
        workspace=str(tmp_path),
        directed_effect_runtime=_dependencies(),
        directed_effect_required=True,
    )
    gateway_factory = MagicMock()
    request = _MockRequest(workspace=str(tmp_path))

    with (
        patch("polaris.cells.roles.kernel.public.service.RoleContextGateway", gateway_factory),
        pytest.raises(RuntimeError, match="directed_effect_execution_attempt_authority_required"),
    ):
        await _execute_bound_turn(
            TransactionTurnExecutor(kernel),
            role="director",
            profile=_profile("director"),
            request=request,
            system_prompt="You are Director.",
            fingerprint=_MockFingerprint(),
            observer_run_id="run_123",
            response_schema=None,
        )
    gateway_factory.assert_not_called()


@pytest.mark.asyncio
async def test_authority_closed_during_context_is_rejected_before_provider_dependency(tmp_path: Path) -> None:
    attempt = replace(_attempt(), workspace=str(tmp_path), run_id="run_123")
    authority = TaskRuntimeExecutionAttemptAuthorityV1(
        attempt,
        heartbeat=lambda command: TaskRuntimeExecutionAttemptHeartbeatVerdictV1(
            success=True,
            code="heartbeat_renewed",
            workspace=command.workspace,
            identity=command.identity,
            renewed_identity=command.identity,
        ),
        settle=lambda command: TaskRuntimeExecutionAttemptSettlementVerdictV1(
            success=True,
            code="settled",
            workspace=command.workspace,
            identity=command.identity,
            outcome=command.outcome,
        ),
    )
    request = _MockRequest(
        workspace=str(tmp_path),
        context_override={
            "context_os_snapshot": {},
            "task_runtime_execution_attempt_authority": authority,
        },
    )
    kernel = RoleExecutionKernel.create_default(
        workspace=str(tmp_path),
        directed_effect_runtime=_dependencies(),
        directed_effect_required=True,
    )

    async def prepare_context(*_args, **_kwargs):
        assert authority.settle(outcome="failed", summary="concurrent terminalization").success
        return SimpleNamespace(messages=[{"role": "user", "content": request.message}], token_estimate=1, metadata={})

    gateway = MagicMock(build_context=prepare_context)
    provider_dependency = MagicMock(side_effect=AssertionError("provider dependency must remain untouched"))
    with (
        patch("polaris.cells.roles.kernel.public.service.RoleContextGateway", return_value=gateway),
        patch("polaris.cells.roles.kernel.internal.kernel.transaction_factory.get_llm_invoker", provider_dependency),
        pytest.raises(RuntimeError, match="authority_closed"),
    ):
        await _execute_bound_turn(
            TransactionTurnExecutor(kernel),
            role="director",
            profile=_profile("director"),
            request=request,
            system_prompt="You are Director.",
            fingerprint=_MockFingerprint(),
            observer_run_id="run_123",
            response_schema=None,
        )
    provider_dependency.assert_not_called()
