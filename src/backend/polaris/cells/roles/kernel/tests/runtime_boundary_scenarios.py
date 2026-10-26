"""Kernel-owned scenarios for Runtime tests, returning public DTOs and observations.

Synthetic provider replies, receipts and faults are component inputs only.
They grant no engineering authority and supply no platform acceptance evidence.
"""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from polaris.cells.roles.kernel.internal.kernel import stream_event_projection as projection, transaction_factory
from polaris.cells.roles.kernel.internal.kernel.context_gateway_config_builder import build_context_gateway_config
from polaris.cells.roles.kernel.internal.llm_caller.final_request_tool_surface import (
    assert_tool_in_final_request_surface,
)
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import ensure_director_first_call_materialization_scope
from polaris.cells.roles.kernel.internal.transaction.tool_surface import plan_transaction_tool_surface
from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel
from polaris.cells.roles.kernel.public.llm_caller_contracts import build_native_tool_schemas, resolve_role_output_budget
from polaris.cells.roles.kernel.public.service import ContextGatewayConfig
from polaris.cells.roles.kernel.public.transaction_contracts import TransactionConfig
from polaris.cells.roles.kernel.public.turn_contracts import BatchReceipt
from polaris.cells.roles.kernel.public.turn_events import CompletionEvent
from polaris.cells.roles.profile.public.service import RoleTurnRequest, RoleTurnResult
from polaris.infrastructure.llm.providers import anthropic_provider
from polaris.kernelone.llm.engine.provider_native_request import project_factory_provider_native_request
from polaris.kernelone.llm.types import InvokeResult, Usage


def committed_result(
    tool: str = "execute_command", *, turn: str = "committed-1", call_id: str = "call-1"
) -> RoleTurnResult:
    """Use the actual transaction decoder with detached synthetic owner values."""
    # Enter through the transaction module, as production does, before its decoder.
    from polaris.cells.roles.kernel.internal.transaction.decision_pipeline import TurnDecisionDecoder

    if tool == "execute_command":
        arguments = {"command": "npx tsc -p tsconfig.json", "timeout": 60}
    elif tool == "write_file":
        arguments = {"path": "example.ts", "text": "export const timeout_seconds = 60;\n"}
    else:
        arguments = {"file": "example.ts"}
    invocation = TurnDecisionDecoder()._parse_native_tool(
        {"type": "tool_use", "id": call_id, "name": tool, "input": arguments}
    )
    payload: dict[str, Any] = (
        {
            "command": {
                "executable": "npx",
                "args": ["tsc", "-p", "tsconfig.json"],
                "cwd": "/fixture/project",
                "env_policy": "inherit",
                "timeout_seconds": 60,
            },
            "ok": True,
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
        }
        if tool == "execute_command"
        else {"file": "example.ts", "content": "export const timeout_seconds = 60;\n"}
    )
    row = {"call_id": call_id, "tool_name": tool, "status": "success", "result": payload}
    batch = {"batch_id": turn + "-batch", "results": [row], "success_count": 1, "failure_count": 0}
    return RoleTurnResult(
        tool_calls=[{"tool": tool, "call_id": call_id, "args": deepcopy(invocation.arguments)}],
        batch_receipt=deepcopy(batch),
        metadata={
            "transaction_kind": "continue_multi_turn",
            "needs_followup_workflow": True,
            "turn_commit_receipt": {"turn_id": turn, "validation_passed": True},
            "turn_outcome": {
                "turn_id": turn,
                "execution": {"invocations": [invocation.to_dict()], "receipt": deepcopy(batch)},
            },
        },
    )


def project_denied_batch_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RoleTurnResult:
    """Real producer serialization; only publication/boundary fact I/O is detached."""
    from polaris.cells.roles.kernel.internal.kernel import stream_event_projection as projection
    from polaris.cells.roles.kernel.internal.kernel.tests.test_stream_event_projection import (
        _make_stream_completion_projector,
        _Publisher,
    )
    from polaris.cells.roles.kernel.internal.turn_decision_decoder import TurnDecisionDecoder
    from polaris.cells.roles.kernel.public.turn_events import CompletionEvent

    rows = []
    invocations = []
    for call_id, tool, arguments, error in (
        ("tree-call", "repo_tree", {"path": ".", "depth": 3}, "deo_batch_mutation_admission_rejected"),
        ("denied-call", "execute_command", {"command": "pwd && ls -la"}, "deo_command_scope_denied"),
    ):
        invocations.append(
            TurnDecisionDecoder()
            ._parse_native_tool({"type": "tool_use", "id": call_id, "name": tool, "input": arguments})
            .to_dict()
        )
        rows.append(
            {
                "call_id": call_id,
                "tool_name": tool,
                "status": "error",
                "result": {"ok": False, "error": error, "error_type": error},
                "error": error,
                "effect_receipt": None,
                "directed_effect_claim_status": "not_claimed",
            }
        )
    batch = {"turn_id": "denied-turn", "batch_id": "denied-batch", "results": rows, "failure_count": 2}
    # The actual immutable owner serializer supplies defaults omitted by the event.
    committed = BatchReceipt.model_validate(deepcopy(batch), strict=True).model_dump(mode="python")
    monkeypatch.setattr(projection, "append_role_turn_task_boundary_verdict", lambda **_: None)
    projector = _make_stream_completion_projector(tmp_path, _Publisher())
    projected = asyncio.run(
        projector.project(
            CompletionEvent(
                turn_id="denied-turn",
                status="success",
                turn_kind="continue_multi_turn",
                batch_receipt=deepcopy(batch),
                commit_receipt={"turn_id": "denied-turn", "validation_passed": True},
                turn_outcome={
                    "turn_id": "denied-turn",
                    "execution": {"invocations": invocations, "receipt": committed},
                },
            )
        )
    )
    assert projected is not None
    return projected.event["result"]


async def capture_native_provider_body(
    request: RoleTurnRequest,
    profile: Any,
    monkeypatch: pytest.MonkeyPatch,
    *,
    include_history: bool = False,
) -> dict[str, Any]:
    """Run actual Kernel preparation and serializer; network intercept is explicit failure."""
    context = request.context_override or {}
    plan = plan_transaction_tool_surface(
        role="director",
        profile=profile,
        request=request,
        context_result=SimpleNamespace(messages=[], token_estimate=0, metadata={}),
        messages=[{"role": "user", "content": request.message}],
        workspace=request.workspace,
        mode="stream",
    )
    # The production transaction caller forwards the selected schemas to the
    # physical preparer. Use that existing boundary and native serializer.
    physical_context = {
        **context,
        "_transaction_kernel_forced_tool_definitions": plan.tool_definitions,
        "_transaction_kernel_forced_tool_choice": plan.tool_choice_override or "auto",
        "_transaction_kernel_prebuilt_messages": [
            {"role": "system", "content": "You are the Director. Preserve the current task authority."},
            *(context.get("_transaction_kernel_prebuilt_messages", []) if include_history else []),
            {"role": "user", "content": request.message},
        ],
    }
    prepared = await LLMRequestPreparer(workspace=request.workspace)._prepare_llm_request(
        profile=profile,
        system_prompt="You are the Director.",
        context=SimpleNamespace(message=request.message, domain="code", context_override=physical_context),
        temperature=0.0,
        max_tokens=resolve_role_output_budget(context=context, requested=18_000),
        stream=False,
    )
    native = project_factory_provider_native_request(
        provider_type="anthropic_compat",
        mode="invoke",
        final_payload={
            "model": profile.model,
            "messages": prepared.messages,
            "tools": prepared.request_options.get("tools") or [],
            "tool_choice": prepared.request_options.get("tool_choice"),
            "response_format": prepared.request_options.get("response_format"),
            "temperature": prepared.request_options["temperature"],
            "max_tokens": prepared.request_options["max_tokens"],
            "stream": False,
        },
        provider_config={"base_url": "https://provider.test/anthropic"},
    )
    assert native is not None
    expected = json.loads(native.expected_body_json)
    captured: list[dict[str, Any]] = []

    def intercept(_url: str, _headers: Any, payload: dict[str, Any], *_args: Any) -> InvokeResult:
        captured.append(deepcopy(payload))
        return InvokeResult(
            ok=False, output="", latency_ms=0, usage=Usage.estimate("", ""), error="transport_intercepted"
        )

    # Intercept only the network boundary AFTER the production provider builds
    # and sanitizes its actual outbound body. No fabricated successful reply.
    monkeypatch.setattr(anthropic_provider, "invoke_with_retry", intercept)
    anthropic_provider.AnthropicProvider().invoke(
        prepared.input_text,
        profile.model,
        {
            **prepared.request_options,
            "chat_messages": prepared.messages,
            "base_url": "https://provider.test/anthropic",
            "api_key": "fixture",
        },
    )
    assert len(captured) == 1
    assert captured[0]["tools"] == expected["tools"]
    assert captured[0].get("tool_choice") == expected.get("tool_choice")
    assert captured[0]["max_tokens"] == expected["max_tokens"]
    return captured[0]


def resolve_injected_context_fixture(
    factory: Any, role: str, profile: Any, request: Any
) -> ContextGatewayConfig | None:
    """Exercise the real owner adapter, returning its existing public config DTO."""
    return build_context_gateway_config(factory, role, profile, request)


async def execute_readonly_transaction_fixture(
    role: str, request: Any, workspace: Path, provider: Any, tools: Any
) -> dict[str, Any]:
    """Execute real policy/decoder with explicit synthetic provider/tool inputs."""
    transaction = TransactionKernel(
        llm_provider=provider,
        tool_runtime=tools,
        config=TransactionConfig(domain="code", role_id=role, workspace=str(workspace)),
    )
    return await transaction.execute(
        request.metadata["transaction_invocation_id"],
        [{"role": "user", "content": request.message}],
        [{"type": "function", "function": {"name": "read_file"}}],
    )


async def project_boundary_completion_fixture(
    workspace: Path,
    role: str,
    request: Any,
    index: int,
    receipt: dict[str, Any],
    invocations: list[dict[str, Any]],
    append_boundary: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Project synthetic event inputs with the real boundary evaluation callback."""
    monkeypatch.setattr(projection, "append_role_turn_task_boundary_verdict", append_boundary)

    class Publisher:
        async def publish_stream_event(self, **kwargs: Any) -> None:
            pass

    projector = projection.StreamEventProjector(
        kernel=SimpleNamespace(workspace=str(workspace)),
        role=role,
        profile=SimpleNamespace(role_id=role, model="fixture", provider_id="fixture"),
        request=request,
        fingerprint=SimpleNamespace(full_hash="fixture"),
        context_gateway=SimpleNamespace(record_projection_outcome=lambda **_: None),
        context_result=SimpleNamespace(token_estimate=12),
        stream_run_id="projector-run",
        uep_publisher=Publisher(),
        runtime_tool_policy_audit={},
        tool_filter_audit=None,
    )
    projected = await projector.project(
        CompletionEvent(
            turn_id=f"projector-{index}",
            status="success",
            turn_kind="tool_batch_with_receipt",
            batch_receipt=receipt,
            turn_outcome={"execution": {"invocations": invocations, "receipt": receipt}},
            commit_receipt={"transition_id": f"committed-{index}"},
        )
    )
    assert projected is not None
    return projected.event


def install_final_answer_projection_fault(
    monkeypatch: pytest.MonkeyPatch, observations: dict[str, object], mode: str
) -> None:
    """Inject the existing explicit missing-commit/nonrepairable event fault."""
    actual_project = projection.StreamEventProjector.project

    async def project_with_fault(self: Any, event: Any) -> Any:
        if isinstance(event, CompletionEvent) and event.turn_kind == "final_answer" and not event.batch_receipt:
            observations[mode] = int(cast("int", observations.get(mode, 0))) + 1
            # Explicit boundary fault injection, not a replacement receipt.
            event = (
                replace(event, commit_receipt={})
                if mode == "missing_commit"
                else replace(event, status="failed", error="Private nonrepairable execution failure")
            )
        return await actual_project(self, event)

    monkeypatch.setattr(projection.StreamEventProjector, "project", project_with_fault)


async def invoke_factory_native_fixture(
    host: Any, profile: Any, request: Any, messages: list[dict[str, Any]], invoker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run real factory closure; constructor capture creates no execution facts."""
    monkeypatch.setattr(transaction_factory, "get_llm_invoker", lambda _kernel: invoker)
    monkeypatch.setattr(transaction_factory, "TransactionKernel", lambda **kwargs: SimpleNamespace(**kwargs))
    assembly = transaction_factory.create_transaction_kernel(host, "director", profile, request)
    await assembly.llm_provider({"messages": messages, "tools": build_native_tool_schemas(profile)})


def assert_native_tools_available(body: dict[str, Any], tools: tuple[str, ...]) -> None:
    """Check actual final-request tool assertions against captured native body."""
    final_request = SimpleNamespace(options=body)
    for tool in tools:
        assert_tool_in_final_request_surface(tool_name=tool, active_request=final_request, prepared=None)


def assert_native_tool_denial(body: dict[str, Any]) -> None:
    """Absent execute_command must fail the actual final-request assertion."""
    with pytest.raises(RuntimeError, match="provider_tool_surface_violation"):
        assert_tool_in_final_request_surface(
            tool_name="execute_command", active_request=SimpleNamespace(options=body), prepared=None
        )


def stamp_initial_materialization_fixture(context: dict[str, Any], profile: Any, workspace: Path) -> None:
    """Prepare real owner policy state without exporting its private helper or plan."""
    ensure_director_first_call_materialization_scope(
        role="director",
        context_override=context,
        workspace=str(workspace),
        tool_definitions=build_native_tool_schemas(profile),
        from_scratch_target="answer.py",
        materialize_requested=True,
        transaction_tools_disabled=False,
    )


def assert_exact_denied_planning_conflict(profile: Any, request: Any, tmp_path: Path) -> None:
    """Keep the three original plan assertions with their real Kernel owner."""
    plan = plan_transaction_tool_surface(
        role="director",
        profile=profile,
        request=request,
        context_result=SimpleNamespace(metadata={}),
        messages=[],
        workspace=str(tmp_path),
        mode="stream",
    )
    assert plan.tool_definitions == []
    assert plan.tool_choice_override is None
    assert "Forced tool scope conflicts with role/runtime policy" in str(plan.conflict_error)
