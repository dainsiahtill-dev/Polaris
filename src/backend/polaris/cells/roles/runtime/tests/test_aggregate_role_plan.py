from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.public.contracts import (
    AggregateChatCompletionsCommandV1,
    AggregateChatMessageV1,
    AuditAggregateRuntimeIntegrationsQueryV1,
    BuildAggregateRolePlanQueryV1,
)
from polaris.cells.roles.runtime.public.service import (
    RoleRuntimeService,
    aggregate_chat_completions,
    audit_aggregate_runtime_integrations,
    query_aggregate_role_plan,
)
from polaris.kernelone.storage import resolve_runtime_path


@pytest.mark.asyncio
async def test_default_aggregate_role_plan_exposes_core_lobes(tmp_path) -> None:
    runtime = RoleRuntimeService()

    result = await runtime.build_aggregate_role_plan(
        BuildAggregateRolePlanQueryV1(
            workspace=str(tmp_path),
            objective="Treat Polaris roles as one aggregate coding model.",
            domain="code",
        )
    )

    assert result.ok is True
    assert result.aggregate_model_id == "polaris.aggregate_llm.v1"
    assert result.current_role_ids == ("pm", "architect", "chief_engineer", "director", "qa")
    lobe_ids = {lobe.lobe_id for lobe in result.lobes}
    assert {
        "constraint_boundary_generator",
        "dialectic_self_heal_loop",
        "hippocampus_controller",
        "tool_commit_guard",
        "task_market_allocator",
    } <= lobe_ids
    assert "polaris.kernelone.akashic.semantic_memory.AkashicSemanticMemory" in result.required_capability_refs
    assert "polaris.kernelone.context.context_os.StateFirstContextOS" in result.required_capability_refs
    assert len(result.cognitive_ledger) == len(result.lobes)
    assert len(result.runtime_integrations) == 16
    assert all(integration.entrypoint_checks for integration in result.runtime_integrations)
    assert all(integration.entrypoints_verified for integration in result.runtime_integrations)
    wired_p0 = {
        integration.tech_id
        for integration in result.runtime_integrations
        if integration.status == "wired" and integration.priority == "p0"
    }
    assert {
        "turn_transaction_kernel_ledger",
        "context_plane_isolation",
        "strategy_profile_overlay_fingerprint",
        "cognitive_runtime_receipt_handoff",
    } <= wired_p0
    assert result.cognitive_ledger[0].lobe_id == "constraint_boundary_generator"
    assert result.cognitive_ledger[0].handoff_to == ("dialectic_self_heal_loop",)
    assert result.compute_policy["default_priority"] == "local_self_heal_first_after_compiler_feedback"
    assert "compile_failure" in result.compute_policy["local_priority_conditions"]


@pytest.mark.asyncio
async def test_aggregate_plan_marks_virtual_lobes_without_claiming_role_profiles(tmp_path) -> None:
    runtime = RoleRuntimeService()

    result = await runtime.build_aggregate_role_plan(
        BuildAggregateRolePlanQueryV1(
            workspace=str(tmp_path),
            objective="Add a red-team critic before local execution.",
        )
    )

    self_heal = next(lobe for lobe in result.lobes if lobe.lobe_id == "dialectic_self_heal_loop")
    assert self_heal.role_ids == ("chief_engineer", "qa")
    assert self_heal.virtual_role_ids == ("adversarial_critic",)
    assert "virtual_role_ids_are_not_current_role_profiles" in result.warnings
    assert "virtual lobes" in str(result.metadata["truthful_migration"])


@pytest.mark.asyncio
async def test_aggregate_plan_can_disable_virtual_lobes(tmp_path) -> None:
    result = await query_aggregate_role_plan(
        BuildAggregateRolePlanQueryV1(
            workspace=str(tmp_path),
            objective="Build a strict current-role-only plan.",
            include_virtual_lobes=False,
        )
    )

    assert all(lobe.virtual_role_ids == () for lobe in result.lobes)
    assert "virtual_role_ids_are_not_current_role_profiles" not in result.warnings


@pytest.mark.asyncio
async def test_aggregate_plan_marks_partial_lobes_for_constrained_role_set(tmp_path) -> None:
    runtime = RoleRuntimeService()

    result = await runtime.build_aggregate_role_plan(
        BuildAggregateRolePlanQueryV1(
            workspace=str(tmp_path),
            objective="Only plan the local execution lobe.",
            role_ids=("director",),
        )
    )

    hippocampus = next(lobe for lobe in result.lobes if lobe.lobe_id == "hippocampus_controller")
    constraint = next(lobe for lobe in result.lobes if lobe.lobe_id == "constraint_boundary_generator")
    assert result.current_role_ids == ("director",)
    assert hippocampus.status == "active"
    assert constraint.status == "partial"
    assert constraint.missing_role_ids == ("architect", "qa")
    assert any(warning.startswith("partial_lobes:") for warning in result.warnings)


@pytest.mark.asyncio
async def test_aggregate_plan_builds_takeover_directive_from_failure_signal(tmp_path) -> None:
    runtime = RoleRuntimeService()

    result = await runtime.build_aggregate_role_plan(
        BuildAggregateRolePlanQueryV1(
            workspace=str(tmp_path),
            objective="Recover after a local compiler failure.",
            failure_signals=("compile_failure",),
        )
    )

    assert result.takeover_directive is not None
    assert result.takeover_directive.trigger == "compile_failure"
    assert result.takeover_directive.lobe_id == "dialectic_self_heal_loop"
    assert result.takeover_directive.compute_tier == "cloud_critic_local_retry"
    assert result.takeover_directive.action_contract == "self_healed_blueprint_v1"
    assert "compiler_output" in result.takeover_directive.evidence_keys
    assert result.metadata["failure_signals"] == ("compile_failure",)
    assert result.metadata["takeover_evidence_status"]["complete"] is False
    assert "compiler_output" in result.metadata["takeover_evidence_status"]["missing_keys"]
    assert any(warning.startswith("missing_takeover_evidence:") for warning in result.warnings)


@pytest.mark.asyncio
async def test_aggregate_plan_records_structured_failure_evidence_for_takeover(tmp_path) -> None:
    runtime = RoleRuntimeService()

    result = await runtime.build_aggregate_role_plan(
        BuildAggregateRolePlanQueryV1(
            workspace=str(tmp_path),
            objective="Recover after a local compiler failure.",
            failure_signals=("compile_failure",),
            failure_evidence={
                "compiler_output": "error TS2322: Type mismatch",
                "changed_files": ["src/app.ts"],
                "test_command": "npm test",
            },
        )
    )

    assert result.metadata["failure_evidence"]["compiler_output"] == "error TS2322: Type mismatch"
    assert result.metadata["takeover_evidence_status"]["complete"] is True
    assert result.metadata["takeover_evidence_status"]["missing_keys"] == ()
    assert not any(warning.startswith("missing_takeover_evidence:") for warning in result.warnings)


@pytest.mark.asyncio
async def test_aggregate_plan_preserves_failure_evidence_list_from_context(tmp_path) -> None:
    runtime = RoleRuntimeService()

    result = await runtime.build_aggregate_role_plan(
        BuildAggregateRolePlanQueryV1(
            workspace=str(tmp_path),
            objective="Recover after a platform lifecycle failure.",
            context={
                "failure_evidence": [
                    {
                        "schema_version": "polaris.failure_evidence.v1",
                        "failure_class": "MISSING_EFFECT_RECEIPT",
                        "responsible_layer": "platform",
                        "evidence_refs": ["tool_lifecycle:turn-1"],
                    }
                ]
            },
        )
    )

    failure_evidence = result.metadata["failure_evidence"]
    assert failure_evidence["items"] == [
        {
            "schema_version": "polaris.failure_evidence.v1",
            "failure_class": "MISSING_EFFECT_RECEIPT",
            "responsible_layer": "platform",
            "evidence_refs": ["tool_lifecycle:turn-1"],
        }
    ]
    assert failure_evidence["failure_classes"] == ("MISSING_EFFECT_RECEIPT",)
    assert failure_evidence["evidence_refs"] == ("tool_lifecycle:turn-1",)


@pytest.mark.asyncio
async def test_aggregate_runtime_consumes_list_shaped_failure_evidence_context_rows(
    monkeypatch,
    tmp_path,
) -> None:
    runtime = RoleRuntimeService()
    row = {
        "schema_version": "polaris.failure_evidence.v1",
        "failure_class": "MISSING_EFFECT_RECEIPT",
        "responsible_layer": "platform",
        "evidence_refs": ["tool_lifecycle:turn-1"],
    }
    captured: list[Any] = []
    memory_calls: list[dict[str, Any]] = []

    plan = await runtime.build_aggregate_role_plan(
        BuildAggregateRolePlanQueryV1(
            workspace=str(tmp_path),
            objective="Recover after aggregate failure-evidence projection.",
            failure_signals=("compile_failure",),
            context={"failure_evidence": [row]},
        )
    )

    failure_evidence = plan.metadata["failure_evidence"]
    assert failure_evidence["items"] == [row]
    assert failure_evidence["failure_classes"] == ("MISSING_EFFECT_RECEIPT",)
    assert failure_evidence["evidence_refs"] == ("tool_lifecycle:turn-1",)

    class FakeMemoryProjection:
        def to_dict(self) -> dict[str, Any]:
            return {
                "memory": {
                    "memory_id": "mem-failure-evidence-1",
                    "content": "Previous missing effect receipt was fixed by preserving row evidence.",
                },
                "injection_allowed": True,
                "injection_reason": "failure evidence reference match",
            }

    class FakeMemoryManager:
        def __init__(self, workspace: str) -> None:
            self.workspace = workspace

        def process(
            self,
            query: str,
            current_facts: list[str],
            limit: int = 5,
            min_relevance: float = 0.3,
        ):
            memory_calls.append(
                {
                    "workspace": self.workspace,
                    "query": query,
                    "current_facts": current_facts,
                    "limit": limit,
                    "min_relevance": min_relevance,
                }
            )
            return [FakeMemoryProjection()]

    import polaris.kernelone.context.context_os.memory as context_memory

    monkeypatch.setattr(context_memory, "MemoryManager", FakeMemoryManager)

    async def fake_stream_chat_turn(command):
        captured.append(command)
        yield {
            "type": "fingerprint",
            "profile_id": f"code.{command.role}",
            "profile_hash": f"hash-{len(captured)}",
            "bundle_id": "bundle-code",
            "bundle_version": "v1",
            "run_id": f"strategy-run-{len(captured)}",
            "turn_index": len(captured),
            "cognitive_strategy_override_applied": False,
        }
        yield {"type": "content_chunk", "content": f"{command.role} consumed row-shaped evidence"}

    monkeypatch.setattr(runtime, "stream_chat_turn", fake_stream_chat_turn)

    result = await runtime.chat_completions(
        AggregateChatCompletionsCommandV1(
            workspace=str(tmp_path),
            messages=(
                AggregateChatMessageV1(
                    role="user",
                    content="Run aggregate lobes with list-shaped failure evidence.",
                ),
            ),
            domain="code",
            execution_mode="lobe_chain",
            failure_signals=("compile_failure",),
            context={"failure_evidence": [row]},
            metadata={"max_lobe_turns": 2},
        )
    )

    assert result.aggregate_plan is not None
    assert result.aggregate_plan.metadata["failure_evidence"]["items"] == [row]
    assert result.aggregate_plan.metadata["failure_evidence"]["failure_classes"] == ("MISSING_EFFECT_RECEIPT",)
    assert result.aggregate_plan.metadata["failure_evidence"]["evidence_refs"] == ("tool_lifecycle:turn-1",)
    assert len(captured) == 2

    first_context = captured[0].context["aggregate_runtime_context"]
    assert first_context["failure_evidence"]["items"] == [row]
    assert first_context["failure_evidence"]["failure_classes"] == ("MISSING_EFFECT_RECEIPT",)
    assert first_context["failure_evidence"]["evidence_refs"] == ("tool_lifecycle:turn-1",)
    first_envelope = json.loads(captured[0].user_message)
    assert first_envelope["failure"]["evidence"]["items"] == [row]
    assert first_envelope["failure"]["evidence"]["failure_classes"] == ["MISSING_EFFECT_RECEIPT"]
    assert first_envelope["failure"]["evidence"]["evidence_refs"] == ["tool_lifecycle:turn-1"]

    assert memory_calls
    assert memory_calls[0]["workspace"] == str(tmp_path)
    assert "MISSING_EFFECT_RECEIPT" in memory_calls[0]["query"]
    assert "tool_lifecycle:turn-1" in memory_calls[0]["query"]
    assert any(
        fact.startswith("failure_evidence.items=") and "MISSING_EFFECT_RECEIPT" in fact
        for fact in memory_calls[0]["current_facts"]
    )
    assert any(
        fact == "failure_evidence.failure_classes=('MISSING_EFFECT_RECEIPT',)"
        for fact in memory_calls[0]["current_facts"]
    )
    assert any(
        fact == "failure_evidence.evidence_refs=('tool_lifecycle:turn-1',)" for fact in memory_calls[0]["current_facts"]
    )

    second_context = captured[1].context["aggregate_runtime_context"]
    assert second_context["akashic_recall_pack"]["status"] == "ok"
    assert second_context["akashic_recall_pack"]["current_facts"] == memory_calls[0]["current_facts"]
    second_envelope = json.loads(captured[1].user_message)
    assert second_envelope["failure"]["evidence"]["items"] == [row]
    assert second_envelope["memory_recall"]["projection_count"] == 1


@pytest.mark.asyncio
async def test_aggregate_chat_completions_returns_model_shaped_plan(tmp_path) -> None:
    runtime = RoleRuntimeService()

    result = await runtime.chat_completions(
        AggregateChatCompletionsCommandV1(
            workspace=str(tmp_path),
            messages=(
                AggregateChatMessageV1(role="system", content="You are Polaris Aggregate LLM."),
                AggregateChatMessageV1(role="user", content="Fix a code regression with ContextOS memory fallback."),
            ),
            failure_signals=("typecheck_failure",),
            domain="code",
        )
    )

    assert result.id.startswith("aggcmpl-")
    assert result.object == "chat.completion"
    assert result.model == "polaris.aggregate_llm.v1"
    assert result.metadata["execution_mode"] == "plan_only"
    assert result.aggregate_plan is not None
    payload = json.loads(result.choices[0].message.content)
    assert payload["execution_mode"] == "plan_only"
    assert "hippocampus_controller" in payload["execution_order"]
    assert payload["compute_policy"]["policy_id"] == "aggregate_compute_swap.v1"
    assert any(item["lobe_id"] == "tool_commit_guard" for item in payload["cognitive_ledger"])
    assert payload["takeover_directive"]["trigger"] == "typecheck_failure"
    assert "polaris.kernelone.akashic.semantic_memory.AkashicSemanticMemory" in payload["required_capability_refs"]
    assert result.usage["input_messages"] == 2
    assert result.usage["runtime_integrations"] == 16


@pytest.mark.asyncio
async def test_aggregate_runtime_audit_distinguishes_wired_available_and_planned(tmp_path) -> None:
    result = await audit_aggregate_runtime_integrations(
        AuditAggregateRuntimeIntegrationsQueryV1(workspace=str(tmp_path))
    )

    assert result.ok is True
    assert result.wired_count == 16
    assert result.available_count == 0
    assert result.planned_bridge_count == 0
    assert result.verified_entrypoint_count >= 53
    assert result.missing_entrypoint_count == 0
    assert len(result.integrations) == 16
    assert result.warnings == ()
    assert {
        "turn_transaction_kernel_ledger",
        "context_plane_isolation",
        "strategy_profile_overlay_fingerprint",
        "cognitive_runtime_receipt_handoff",
    } <= set(result.priority_wired)
    assert all(integration.production_entrypoints for integration in result.integrations)
    assert all(integration.entrypoint_checks for integration in result.integrations)
    assert all(integration.entrypoints_verified for integration in result.integrations)
    assert any(
        check.check_type == "http_route" and check.ok
        for integration in result.integrations
        for check in integration.entrypoint_checks
    )


@pytest.mark.asyncio
async def test_aggregate_runtime_audit_fails_when_runtime_entrypoint_is_not_verifiable(tmp_path) -> None:
    missing_workspace = tmp_path / "missing-workspace"

    result = await audit_aggregate_runtime_integrations(
        AuditAggregateRuntimeIntegrationsQueryV1(workspace=str(missing_workspace))
    )

    strategy = next(item for item in result.integrations if item.tech_id == "strategy_profile_overlay_fingerprint")
    assert result.ok is False
    assert result.missing_entrypoint_count >= 1
    assert "runtime/strategy_runs" in strategy.missing_entrypoints
    assert strategy.entrypoints_verified is False
    assert any(warning.startswith("missing_entrypoints:") for warning in result.warnings)


@pytest.mark.asyncio
async def test_aggregate_single_turn_executes_selected_concrete_role_with_runtime_guards(
    monkeypatch,
    tmp_path,
) -> None:
    runtime = RoleRuntimeService()
    captured: dict[str, Any] = {}

    async def fake_stream_chat_turn(command):
        captured["command"] = command
        yield {
            "type": "fingerprint",
            "profile_id": "code.director",
            "profile_hash": "hash-1",
            "bundle_id": "bundle-code",
            "bundle_version": "v1",
            "run_id": "strategy-run-1",
            "turn_index": 1,
            "cognitive_strategy_override_applied": False,
        }
        yield {"type": "content_chunk", "content": "director executed aggregate turn"}

    monkeypatch.setattr(runtime, "stream_chat_turn", fake_stream_chat_turn)

    result = await runtime.chat_completions(
        AggregateChatCompletionsCommandV1(
            workspace=str(tmp_path),
            messages=(AggregateChatMessageV1(role="user", content="Recover code localization."),),
            domain="code",
            failure_signals=("localization_uncertain",),
            execution_mode="single_turn",
        )
    )

    assert result.execution_result is not None
    assert result.execution_result.role == "director"
    assert result.execution_result.output == "director executed aggregate turn"
    assert result.metadata["stateful"] is True
    assert result.metadata["selected_lobe_id"] == "hippocampus_controller"
    assert result.execution_result.metadata["strategy_fingerprint"]["profile_id"] == "code.director"
    payload = json.loads(result.choices[0].message.content)
    assert payload["execution_mode"] == "single_turn"
    assert payload["selected_role_id"] == "director"
    command = captured["command"]
    assert command.role == "director"
    assert command.metadata["context_os_expected"] is True
    assert command.metadata["cognitive_runtime_required"] is True
    assert command.metadata["aggregate_execution"]["selected_lobe_id"] == "hippocampus_controller"
    assert command.context["aggregate_runtime_context"]["selected_role_id"] == "director"
    attention_pack = command.context["aggregate_runtime_context"]["contextos_attention_budget_pack"]
    assert attention_pack["status"] == "ok"
    assert attention_pack["phase"] == "exploration"
    assert len(attention_pack["attention_scores"]) >= 2
    task_projection_pack = command.context["aggregate_runtime_context"]["task_market_projection_pack"]
    assert task_projection_pack["status"] == "ok"
    assert task_projection_pack["summary"]["workspace"] == str(tmp_path)
    context_governance_pack = command.context["aggregate_runtime_context"]["context_governance_pack"]
    assert context_governance_pack["status"] == "ok"
    assert context_governance_pack["graph_constrained_retrieval"]["candidate_count"] >= 0
    distilled_knowledge_pack = command.context["aggregate_runtime_context"]["distilled_knowledge_pack"]
    assert distilled_knowledge_pack["status"] == "ok"
    assert command.metadata["aggregate_execution"]["contextos_attention_budget_status"]["phase"] == "exploration"
    assert command.metadata["aggregate_execution"]["task_market_projection_status"]["status"] == "ok"
    assert command.metadata["aggregate_execution"]["context_governance_status"]["status"] == "ok"
    assert command.metadata["aggregate_execution"]["distilled_knowledge_status"]["status"] == "ok"
    assert result.execution_result.metadata["knowledge_distillation"]["status"] == "ok"
    envelope = json.loads(command.user_message)
    assert envelope["schema"] == "polaris.aggregate_lobe_turn.v1"
    assert envelope["lobe_directive"]["lobe_id"] == "hippocampus_controller"
    assert envelope["contextos_attention_budget"]["phase"] == "exploration"
    assert envelope["context_governance"]["status"] == "ok"
    assert envelope["distilled_knowledge"]["status"] == "ok"
    assert envelope["runtime_projection"]["status"] == "ok"
    assert envelope["response_contract"] == "context_projection_v1"


@pytest.mark.asyncio
async def test_aggregate_lobe_chain_executes_bounded_role_sequence_with_structured_handoffs(
    monkeypatch,
    tmp_path,
) -> None:
    runtime = RoleRuntimeService()
    captured: list[Any] = []
    memory_calls: list[dict[str, Any]] = []

    class FakeMemoryProjection:
        def to_dict(self) -> dict[str, Any]:
            return {
                "memory": {
                    "memory_id": "mem-compile-1",
                    "content": "Previous TS2322 repair used stricter type narrowing.",
                },
                "injection_allowed": True,
                "injection_reason": "high relevance",
            }

    class FakeMemoryManager:
        def __init__(self, workspace: str) -> None:
            self.workspace = workspace

        def process(self, query: str, current_facts: list[str], limit: int = 5, min_relevance: float = 0.3):
            memory_calls.append(
                {
                    "workspace": self.workspace,
                    "query": query,
                    "current_facts": current_facts,
                    "limit": limit,
                    "min_relevance": min_relevance,
                }
            )
            return [FakeMemoryProjection()]

    import polaris.kernelone.context.context_os.memory as context_memory

    monkeypatch.setattr(context_memory, "MemoryManager", FakeMemoryManager)

    async def fake_stream_chat_turn(command):
        captured.append(command)
        yield {
            "type": "fingerprint",
            "profile_id": f"code.{command.role}",
            "profile_hash": f"hash-{len(captured)}",
            "bundle_id": "bundle-code",
            "bundle_version": "v1",
            "run_id": f"strategy-run-{len(captured)}",
            "turn_index": len(captured),
            "cognitive_strategy_override_applied": False,
        }
        yield {"type": "content_chunk", "content": f"{command.role} lobe output"}

    monkeypatch.setattr(runtime, "stream_chat_turn", fake_stream_chat_turn)

    result = await runtime.chat_completions(
        AggregateChatCompletionsCommandV1(
            workspace=str(tmp_path),
            messages=(AggregateChatMessageV1(role="user", content="Run aggregate lobes."),),
            domain="code",
            execution_mode="lobe_chain",
            failure_signals=("compile_failure",),
            failure_evidence={
                "compiler_output": "error TS2322",
                "changed_files": ["src/app.ts"],
                "test_command": "npm test",
            },
            metadata={"max_lobe_turns": 2},
        )
    )

    assert len(captured) == 2
    assert [command.role for command in captured] == ["chief_engineer", "director"]
    assert len(result.execution_results) == 2
    assert result.execution_result is result.execution_results[-1]
    assert result.metadata["selected_lobe_ids"] == (
        "dialectic_self_heal_loop",
        "hippocampus_controller",
    )
    assert result.metadata["selected_role_ids"] == ("chief_engineer", "director")
    assert result.metadata["handoff_count"] == 2
    assert result.usage["executed_turns"] == 2
    assert result.usage["handoff_count"] == 2
    first_command = captured[0]
    second_command = captured[1]
    assert first_command.metadata["aggregate_execution"]["chain_turn_index"] == 0
    assert first_command.context["aggregate_runtime_context"]["prior_handoffs"] == []
    assert first_command.context["aggregate_runtime_context"]["failure_evidence"]["compiler_output"] == "error TS2322"
    assert first_command.metadata["aggregate_execution"]["takeover_evidence_status"]["complete"] is True
    assert first_command.context["aggregate_runtime_context"]["akashic_recall_pack"]["enabled"] is False
    assert first_command.context["aggregate_runtime_context"]["contextos_attention_budget_pack"]["phase"] == "debugging"
    assert first_command.context["aggregate_runtime_context"]["task_market_projection_pack"]["status"] == "ok"
    assert first_command.context["aggregate_runtime_context"]["context_governance_pack"]["status"] == "ok"
    assert first_command.context["aggregate_runtime_context"]["distilled_knowledge_pack"]["status"] == "ok"
    first_envelope = json.loads(first_command.user_message)
    assert first_envelope["schema"] == "polaris.aggregate_lobe_turn.v1"
    assert first_envelope["lobe_directive"]["lobe_id"] == "dialectic_self_heal_loop"
    assert first_envelope["lobe_directive"]["attention_masks"] == [
        "edge_case_attack",
        "rollback_required",
        "test_gate_required",
    ]
    assert first_envelope["contextos_attention_budget"]["phase"] == "debugging"
    assert first_envelope["context_governance"]["status"] == "ok"
    assert first_envelope["distilled_knowledge"]["status"] == "ok"
    assert first_envelope["runtime_projection"]["status"] == "ok"
    assert first_envelope["failure"]["evidence"]["compiler_output"] == "error TS2322"
    assert second_command.metadata["aggregate_execution"]["chain_turn_index"] == 1
    assert second_command.metadata["aggregate_execution"]["prior_handoff_count"] == 1
    assert second_command.context["aggregate_runtime_context"]["prior_handoffs"][0]["role_id"] == "chief_engineer"
    assert second_command.context["aggregate_runtime_context"]["failure_evidence"]["test_command"] == "npm test"
    recall_pack = second_command.context["aggregate_runtime_context"]["akashic_recall_pack"]
    assert recall_pack["enabled"] is True
    assert recall_pack["status"] == "ok"
    assert recall_pack["projection_count"] == 1
    assert recall_pack["injection_allowed_count"] == 1
    assert "error TS2322" in recall_pack["query"]
    assert second_command.metadata["aggregate_execution"]["akashic_recall_status"]["projection_count"] == 1
    assert second_command.metadata["aggregate_execution"]["context_governance_status"]["status"] == "ok"
    assert second_command.metadata["aggregate_execution"]["distilled_knowledge_status"]["status"] == "ok"
    assert result.execution_results[0].metadata["knowledge_distillation"]["status"] == "ok"
    assert result.execution_results[1].metadata["knowledge_distillation"]["status"] == "ok"
    assert memory_calls[0]["workspace"] == str(tmp_path)
    assert any("failure_evidence.compiler_output=error TS2322" in fact for fact in memory_calls[0]["current_facts"])
    second_envelope = json.loads(second_command.user_message)
    assert second_envelope["lobe_directive"]["lobe_id"] == "hippocampus_controller"
    assert second_envelope["prior_handoffs"][0]["role_id"] == "chief_engineer"
    assert second_envelope["memory_recall"]["projection_count"] == 1
    assert second_envelope["memory_recall"]["projections"][0]["memory_id"] == "mem-compile-1"
    payload = json.loads(result.choices[0].message.content)
    assert payload["execution_mode"] == "lobe_chain"
    assert payload["executed_lobes"] == [
        "dialectic_self_heal_loop",
        "hippocampus_controller",
    ]
    assert payload["handoffs"][0]["output_contract"] == "self_healed_blueprint_v1"


@pytest.mark.asyncio
async def test_aggregate_lobe_chain_materializes_all_unique_technology_evidence(
    monkeypatch,
    tmp_path,
) -> None:
    runtime = RoleRuntimeService()
    captured: list[Any] = []

    async def fake_stream_chat_turn(command):
        captured.append(command)
        yield {
            "type": "fingerprint",
            "profile_id": f"code.{command.role}",
            "profile_hash": f"hash-{len(captured)}",
            "bundle_id": "bundle-code",
            "bundle_version": "v1",
            "run_id": f"strategy-run-{len(captured)}",
            "turn_index": len(captured),
            "cognitive_strategy_override_applied": False,
        }
        yield {"type": "content_chunk", "content": f"{command.role} aggregate materialized"}

    monkeypatch.setattr(runtime, "stream_chat_turn", fake_stream_chat_turn)

    result = await runtime.chat_completions(
        AggregateChatCompletionsCommandV1(
            workspace=str(tmp_path),
            messages=(AggregateChatMessageV1(role="user", content="Materialize every unique runtime technology."),),
            domain="code",
            execution_mode="lobe_chain",
            failure_signals=("compile_failure",),
            failure_evidence={
                "compiler_output": "error TS2322",
                "changed_files": ["src/app.ts"],
                "test_command": "npm test",
            },
            metadata={"max_lobe_turns": 1},
        )
    )

    assert result.execution_result is not None
    runtime_metadata = result.execution_result.metadata["aggregate_runtime"]
    assert set(runtime_metadata["runtime_integrations_wired"]) == {
        "acga_graph_cell_governance",
        "kernelone_agent_os",
        "turn_transaction_kernel_ledger",
        "context_plane_isolation",
        "descriptor_context_verify_packs",
        "strategy_profile_overlay_fingerprint",
        "cognitive_runtime_receipt_handoff",
        "session_continuity_engine",
        "context_catalog_graph_semantic_retrieval",
        "repo_intelligence_localizer",
        "akashic_knowledge_pipeline",
        "tool_normalization_edit_blocks",
        "change_set_validation_rollback",
        "task_market_runtime_projection",
        "cognitive_knowledge_distiller",
        "contextos_attention_phase_budgeting",
    }
    assert runtime_metadata["context_governance_status"] == "ok"
    assert runtime_metadata["distilled_knowledge_status"] == "ok"
    assert runtime_metadata["knowledge_distillation_status"] == "ok"

    command = captured[0]
    aggregate_context = command.context["aggregate_runtime_context"]
    assert aggregate_context["context_governance_pack"]["status"] == "ok"
    assert aggregate_context["context_governance_pack"]["context_pack"]["item_count"] >= 0
    assert aggregate_context["context_governance_pack"]["verify_pack"]["retrieval_candidate_count"] >= 0
    assert aggregate_context["contextos_attention_budget_pack"]["status"] == "ok"
    assert aggregate_context["task_market_projection_pack"]["status"] == "ok"
    assert aggregate_context["distilled_knowledge_pack"]["status"] == "ok"

    distillation = result.execution_result.metadata["knowledge_distillation"]
    assert distillation["knowledge_units_created"] >= 1
    knowledge_file = Path(resolve_runtime_path(str(tmp_path), "runtime/knowledge/distilled_knowledge.jsonl"))
    assert knowledge_file.exists()
    stored = [json.loads(line) for line in knowledge_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert stored
    assert any(item["metadata"]["lobe_id"] == "dialectic_self_heal_loop" for item in stored)


@pytest.mark.asyncio
async def test_aggregate_chat_completions_facade_is_deterministic(tmp_path) -> None:
    command = AggregateChatCompletionsCommandV1(
        workspace=str(tmp_path),
        messages=(AggregateChatMessageV1(role="user", content="Plan an Architect QA boundary lobe."),),
        include_virtual_lobes=False,
    )

    first = await aggregate_chat_completions(command)
    second = await aggregate_chat_completions(command)

    assert first.id == second.id
    assert first.choices[0].message.content == second.choices[0].message.content
    assert first.aggregate_plan is not None
    assert all(lobe.virtual_role_ids == () for lobe in first.aggregate_plan.lobes)
