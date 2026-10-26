"""Test-only Kernel scenarios; callers receive observations or public values.

Request inputs, decoded tool calls and DEO contract descriptors are synthetic.
Gateway checks, request assembly, policy/fence effects, physical execution and
durable receipts run through their existing owners. No provider is contacted.
"""

from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.context_gateway.context_override_processor import ContextOverrideProcessor
from polaris.cells.roles.kernel.internal.context_gateway.token_estimator import TokenEstimator
from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor
from polaris.cells.roles.kernel.internal.llm_caller.context_audit import build_final_request_context_audit_for_request
from polaris.cells.roles.kernel.internal.llm_caller.context_audit._payloads import _looks_like_actual_sibling_exports
from polaris.cells.roles.kernel.internal.llm_caller.error_handling import classify_error, is_retryable_error
from polaris.cells.roles.kernel.internal.llm_caller.invoker import _binding
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import (
    LLMRequestPreparer,
    _ensure_current_source_evidence_message_bound,
)
from polaris.cells.roles.kernel.internal.llm_caller.response_types import PreparedLLMRequest
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import build_single_batch_task_contract_hint
from polaris.cells.roles.kernel.public import (
    DeferredDirectorRepairEffectBindingV1,
    DirectedEffectExecutionContextV1,
    DirectedEffectFencePortsV1,
    RoleExecutionKernel,
)
from polaris.cells.roles.kernel.public.directed_effect_service import create_directed_effect_fence_ports
from polaris.cells.roles.kernel.tests.test_directed_effect_contracts import (
    _attempt,
    _claim_grant,
    _current_policy_evidence,
    _inventory,
    _member,
    _prepared_batch,
    _prepared_member,
)
from polaris.cells.roles.profile.public import RoleProfile, RoleTurnRequest
from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType
from polaris.kernelone.llm.runtime_config import RoleBindingSlot

SYNTHETIC_ARGUMENTS = (("path", "src/a.py"),)


def observe_context_error_retry(error: str) -> dict[str, Any]:
    """Observe the LLM caller's classification and its paired retry decision."""
    category = classify_error(error)
    return {"category": category, "retryable": is_retryable_error(category)}


def observe_final_request_metadata(
    context: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    role_id: str = "director",
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the final request auditor over explicit local request input."""
    options = {} if tools is None else {"tools": deepcopy(tools)}
    request = AIRequest(task_type=TaskType.DIALOGUE, role=role_id, input="", options=options, context=deepcopy(context))
    request.context["chat_messages"] = deepcopy(messages)
    prepared = PreparedLLMRequest(
        messages=deepcopy(messages),
        input_text="",
        context_result=None,
        context_summary="synthetic boundary input",
        request_options=dict(options),
        ai_request=request,
    )
    return build_final_request_context_audit_for_request(
        ai_request=request,
        prepared=prepared,
        profile=SimpleNamespace(role_id=role_id, max_context_tokens=128000),
    )


def observe_sibling_request_evidence(value: Any, *, messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Compare structural validation with the actual final request audit.

    A structural-only observation intentionally does not assert message coverage.
    For bound evidence, both owner validation and final audit must agree.
    """
    structural = _looks_like_actual_sibling_exports(value, messages=None)
    audit = observe_final_request_metadata({"actual_sibling_exports": value}, messages or [])
    bound = bool(audit["has_actual_sibling_exports"])
    if messages is not None:
        assert bound == _looks_like_actual_sibling_exports(value, messages=messages)
    return {
        "accepted": structural if messages is None else bound,
        "structurally_valid": structural,
        "message_bound": bound,
        "final_request_audit": audit,
    }


def observe_review_source_request(messages: list[dict[str, Any]], context: Any) -> dict[str, Any]:
    """Bind QA source observations and audit the resulting request projection."""
    bound = _ensure_current_source_evidence_message_bound(messages, context)
    audit = observe_final_request_metadata(context if isinstance(context, dict) else {}, bound, role_id="qa")
    return {
        "messages": bound,
        "final_request_audit": audit,
        "gateway_message_token_estimate": TokenEstimator().estimate(messages),
    }


def observe_write_guidance_request(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    """Observe scope hints and final request audit for the same synthetic tools."""
    hint, budget = build_single_batch_task_contract_hint(messages, tools)
    audit = observe_final_request_metadata({}, messages, tools=tools)
    return {"hint": hint, "budget": budget, "final_request_audit": audit}


def observe_safe_context_guidance(context: dict[str, Any]) -> dict[str, Any]:
    """Project safe guidance through the real context processor and request audit."""
    message = ContextOverrideProcessor(detect_prompt_injection=True).process_context_override(context)
    audit = observe_final_request_metadata(context, [] if message is None else [message])
    return {"message": message, "final_request_audit": audit}


def observe_command_projection_permission(context: dict[str, Any], workspace: Path) -> tuple[bool, Any]:
    """Build the actual Kernel gateway and return its execute-command decision."""
    from dataclasses import replace

    profile = RoleProfile(role_id="director", display_name="Director", description="Command projection")
    profile = replace(
        profile, tool_policy=replace(profile.tool_policy, whitelist=["execute_command"], allow_command_execution=True)
    )
    gateway = KernelToolExecutor(RoleExecutionKernel(workspace=str(workspace)), str(workspace)).create_gateway(
        profile,
        RoleTurnRequest(
            workspace=str(workspace), message="Implement requested behavior", task_id="TASK-1", context_override=context
        ),
    )
    return gateway.check_tool_permission("execute_command", {"command": "python verify.py"})


def install_local_pm_request_capture(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Install explicit local binding input and observe real request freezes.

    The caller installs its synthetic HTTP transport. This scenario never returns
    a private preparer, binding module or mutable owner implementation.
    """
    slot = RoleBindingSlot(role_id="pm", provider_id="local-provider", model="local-model", binding_id="local-binding")
    monkeypatch.setattr(_binding, "get_role_binding_candidates", lambda _role: (slot,))
    monkeypatch.setattr(_binding, "get_role_binding_override", lambda _role: None)
    monkeypatch.setattr(_binding, "is_role_binding_healthy", lambda *_args, **_kwargs: True)
    freezes: list[Any] = []
    original_prepare = LLMRequestPreparer._prepare_llm_request

    async def prepare(self: Any, **kwargs: Any) -> Any:
        prepared = await original_prepare(self, **kwargs)
        freezes.append(prepared.factory_semantic_request)
        return prepared

    monkeypatch.setattr(LLMRequestPreparer, "_prepare_llm_request", prepare)
    return freezes


async def observe_line_range_execution(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    calls: list[tuple[str, str, dict[str, Any]]],
    *,
    scope: list[str] | None = None,
    tolerate_runtime_error: bool = False,
) -> dict[str, Any]:
    """Execute decoded synthetic input through real owners; detach observations."""
    from polaris.cells.roles.kernel.tests.test_same_path_partial_jit_closure import (
        ORIGINAL,
        _commits,
        _decision,
        _execute,
        _operations,
        _real_chain,
    )

    chain = _real_chain(workspace, monkeypatch, scope=scope)
    try:
        result = await _execute(chain, _decision(chain, calls))
    except RuntimeError:
        if not tolerate_runtime_error:
            raise
        result = None
    return {
        "content": (chain.workspace / "owned.txt").read_text(encoding="utf-8"),
        "original_content": ORIGINAL,
        "foreign_exists": (chain.workspace / "foreign.txt").exists(),
        "operations": _operations(chain),
        "commits": _commits(chain),
        "result": result,
    }


async def run_persistent_director_http_projection(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the existing sealed HTTP/effect scenario, retaining every owner check."""
    from polaris.cells.roles.kernel.tests.test_director_persistent_execution_http import (
        test_canonical_director_session_reads_writes_repairs_and_verifies_over_http,
    )

    await test_canonical_director_session_reads_writes_repairs_and_verifies_over_http(workspace, monkeypatch, "repair")


def build_synthetic_mutation_context(
    *,
    workspace: str | None = None,
    tool_name: str = "write_file",
    arguments: tuple[tuple[str, object], ...] = SYNTHETIC_ARGUMENTS,
) -> tuple[DirectedEffectExecutionContextV1, DirectedEffectFencePortsV1, str]:
    attempt = _attempt()
    if workspace is not None:
        attempt = replace(attempt, workspace=workspace)
    members = tuple(_member(index, normalized_tool_name=tool_name) for index in range(2))
    batch = _prepared_batch(
        execution_attempt=attempt,
        inventory=_inventory(attempt, members),
        normalized_arguments=arguments,
    )
    prepared = batch.prepared_members[0]
    member = prepared.member
    evidence = batch.authorization_evidence_by_call_id[0][1]
    bound_snapshot = prepared.policy_binding.bound_snapshot
    assert bound_snapshot is not None
    claim_grant = _claim_grant(batch.execution_attempt, batch.parent_binding, member)
    context = DirectedEffectExecutionContextV1(
        context_id="context-1",
        batch_id=batch.parent_binding.correlation.batch_id,
        creator_pid=os.getpid(),
        tool_call_id=member.tool_call_id,
        normalized_tool_name=member.normalized_tool_name,
        arguments_hash=evidence.arguments_hash,
        authorization_evidence=evidence,
        claim_grant=claim_grant,
        bound_snapshot=bound_snapshot,
        current_policy_evidence=_current_policy_evidence(batch, member, claim_grant),
        current_job_token_restriction_evidence=(),
    )
    fence = create_directed_effect_fence_ports()
    assert fence.admin.register(context).ok
    return context, fence, batch.execution_attempt.workspace


def build_synthetic_repair_context(
    binding: DeferredDirectorRepairEffectBindingV1,
    *,
    workspace: str | None = None,
) -> tuple[
    DirectedEffectExecutionContextV1,
    DirectedEffectFencePortsV1,
    str,
    tuple[tuple[str, object], ...],
]:
    attempt = _attempt()
    if workspace is not None:
        attempt = replace(attempt, workspace=workspace)
    member = _member(
        0,
        tool_call_id=binding.tool_call_id,
        normalized_tool_name=binding.effect.tool_name,
    )
    inventory = _inventory(attempt, (member,))
    prepared_member = _prepared_member(
        member,
        stream_head=3,
        execution_attempt=attempt,
        normalized_arguments=binding.effect.arguments,
    )
    batch = _prepared_batch(
        execution_attempt=attempt,
        inventory=inventory,
        prepared_members=(prepared_member,),
        call_id_index=((binding.tool_call_id, 0),),
        normalized_arguments=binding.effect.arguments,
    )
    evidence = batch.authorization_evidence_by_call_id[0][1]
    bound_snapshot = prepared_member.policy_binding.bound_snapshot
    assert bound_snapshot is not None
    claim_grant = _claim_grant(attempt, batch.parent_binding, member)
    context = DirectedEffectExecutionContextV1(
        context_id="repair-context",
        batch_id=batch.parent_binding.correlation.batch_id,
        creator_pid=os.getpid(),
        tool_call_id=member.tool_call_id,
        normalized_tool_name=member.normalized_tool_name,
        arguments_hash=evidence.arguments_hash,
        authorization_evidence=evidence,
        claim_grant=claim_grant,
        bound_snapshot=bound_snapshot,
        current_policy_evidence=_current_policy_evidence(batch, member, claim_grant),
        current_job_token_restriction_evidence=(),
    )
    fence = create_directed_effect_fence_ports()
    assert fence.admin.register(context).ok
    return context, fence, attempt.workspace, binding.effect.arguments
