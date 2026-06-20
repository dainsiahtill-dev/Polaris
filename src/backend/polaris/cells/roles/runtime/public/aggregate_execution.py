"""Aggregate-execution methods for `RoleRuntimeService`.

Lossless split: this module holds ``_AggregateExecutionMixin`` — the
aggregate-role planning, audit, lobe-turn execution, and chat-completions
methods that were previously defined directly on ``RoleRuntimeService``. They
are factored into a mixin so the concrete class keeps every method as a real
class attribute (preserving monkeypatch / attribute-identity behavior) while
their bodies live here.

The methods only reach the rest of the service through ``self`` (resolved via
the MRO at runtime) plus stateless helpers imported from sibling modules, so no
behavior changes.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.profile.public.service import RoleTurnResult, load_core_roles, registry
from polaris.cells.roles.runtime.public.aggregate_chat import (
    _AGGREGATE_LOBE_SPECS,
    _AGGREGATE_MODEL_ID,
    _aggregate_execution_context,
    _aggregate_execution_metadata,
    _aggregate_handoff_from_result,
    _aggregate_history_from_messages,
    _aggregate_objective_from_messages,
    _build_aggregate_context_governance_pack,
    _build_aggregate_contextos_attention_budget_pack,
    _build_aggregate_distilled_knowledge_pack,
    _build_aggregate_lobe,
    _build_aggregate_lobe_turn_envelope,
    _build_aggregate_memory_recall_pack,
    _build_aggregate_task_market_projection_pack,
    _build_cognitive_ledger,
    _build_compute_policy,
    _build_runtime_audit_result,
    _build_runtime_integrations,
    _build_takeover_directive,
    _build_takeover_evidence_status,
    _dedupe_tokens,
    _distill_aggregate_lobe_result,
    _extract_failure_evidence,
    _extract_failure_signals,
    _render_aggregate_chain_content,
    _render_aggregate_execution_content,
    _render_aggregate_plan_content,
    _select_aggregate_execution_lobe,
    _select_aggregate_execution_role,
    _select_aggregate_lobe_chain,
    _select_aggregate_role_ids,
    _stable_completion_id,
)
from polaris.cells.roles.runtime.public.contracts import (
    AggregateChatChoiceV1,
    AggregateChatCompletionsCommandV1,
    AggregateChatCompletionsResultV1,
    AggregateChatMessageV1,
    AggregateRoleLobeV1,
    AggregateRolePlanResultV1,
    AggregateRuntimeAuditResultV1,
    AuditAggregateRuntimeIntegrationsQueryV1,
    BuildAggregateRolePlanQueryV1,
    ExecuteRoleSessionCommandV1,
    RoleExecutionResultV1,
)
from polaris.cells.roles.runtime.public.result_mapping import (
    _to_contract_result,
    _with_result_metadata_patch,
)


class _AggregateExecutionMixin:
    """Aggregate-role planning and execution behavior for ``RoleRuntimeService``."""

    if TYPE_CHECKING:
        # Provided by ``RoleRuntimeService`` via the MRO; declared here so the
        # ``self.stream_chat_turn`` call typechecks without importing the
        # concrete class (which would create an import cycle).
        def stream_chat_turn(
            self,
            command: ExecuteRoleSessionCommandV1,
        ) -> AsyncGenerator[dict[str, Any], None]: ...

    async def build_aggregate_role_plan(
        self,
        query: BuildAggregateRolePlanQueryV1,
    ) -> AggregateRolePlanResultV1:
        """Build a query-only plan for treating role composition as one model.

        The result is intentionally structural: it names current role profiles,
        virtual lobes, KernelOne/ContextOS/Akashic capabilities, and phase order.
        It does not execute role turns or mutate runtime state.
        """
        if not registry.list_roles():
            load_core_roles()
        available_role_ids = {str(role_id).strip() for role_id in registry.list_roles() if str(role_id).strip()}
        current_role_ids = _select_aggregate_role_ids(query.role_ids, available_role_ids)
        selected_role_ids = set(current_role_ids)
        lobes = tuple(
            _build_aggregate_lobe(
                spec,
                selected_role_ids=selected_role_ids,
                available_role_ids=available_role_ids,
                include_virtual_lobes=query.include_virtual_lobes,
            )
            for spec in _AGGREGATE_LOBE_SPECS
        )
        required_capability_refs = _dedupe_tokens(
            capability_ref for lobe in lobes for capability_ref in lobe.capability_refs
        )
        runtime_integrations = _build_runtime_integrations(query.workspace)
        cognitive_ledger = _build_cognitive_ledger(lobes)
        compute_policy = _build_compute_policy(lobes)
        failure_signals = _extract_failure_signals(query)
        takeover_directive = _build_takeover_directive(
            lobes=lobes,
            cognitive_ledger=cognitive_ledger,
            failure_signals=failure_signals,
        )
        warnings: list[str] = []
        failure_evidence = _extract_failure_evidence(query)
        takeover_evidence_status = _build_takeover_evidence_status(
            takeover_directive=takeover_directive,
            failure_evidence=failure_evidence,
        )
        if takeover_directive is not None and takeover_evidence_status.get("missing_keys"):
            warnings.append(
                "missing_takeover_evidence:"
                + ",".join(str(key) for key in takeover_evidence_status.get("missing_keys") or ())
            )
        unknown_requested_roles = tuple(role_id for role_id in query.role_ids if role_id not in available_role_ids)
        if unknown_requested_roles:
            warnings.append(f"unknown_role_ids:{','.join(unknown_requested_roles)}")
        if query.include_virtual_lobes and any(lobe.virtual_role_ids for lobe in lobes):
            warnings.append("virtual_role_ids_are_not_current_role_profiles")
        partial_lobes = tuple(lobe.lobe_id for lobe in lobes if lobe.status != "active")
        if partial_lobes:
            warnings.append(f"partial_lobes:{','.join(partial_lobes)}")
        metadata: dict[str, Any] = {
            "planner": "roles.runtime.aggregate_role_plan.v1",
            "domain": query.domain,
            "failure_signals": failure_signals,
            "failure_evidence": failure_evidence,
            "takeover_evidence_status": takeover_evidence_status,
            "external_interface": "chat_completions_compatible_wrapper",
            "stateful": False,
            "current_fact_scope": "roles.profile entries plus KernelOne capability references",
            "truthful_migration": (
                "This result is a composition plan. It does not claim virtual lobes are "
                "standalone role profiles or that aggregate execution has already run."
            ),
        }
        if query.context:
            metadata["context_keys"] = tuple(sorted(str(key) for key in query.context))
        if query.metadata:
            metadata["metadata_keys"] = tuple(sorted(str(key) for key in query.metadata))
        return AggregateRolePlanResultV1(
            ok=bool(lobes),
            workspace=query.workspace,
            objective=query.objective,
            aggregate_model_id=_AGGREGATE_MODEL_ID,
            lobes=lobes,
            execution_order=tuple(lobe.lobe_id for lobe in lobes),
            current_role_ids=current_role_ids,
            required_capability_refs=required_capability_refs,
            runtime_integrations=runtime_integrations,
            cognitive_ledger=cognitive_ledger,
            compute_policy=compute_policy,
            takeover_directive=takeover_directive,
            warnings=tuple(warnings),
            metadata=metadata,
        )

    async def audit_aggregate_runtime_integrations(
        self,
        query: AuditAggregateRuntimeIntegrationsQueryV1,
    ) -> AggregateRuntimeAuditResultV1:
        """Return a machine-readable audit of aggregate runtime integrations."""
        integrations = _build_runtime_integrations(query.workspace)
        return _build_runtime_audit_result(
            workspace=query.workspace,
            integrations=integrations,
            metadata={
                "role_ids": query.role_ids,
                "include_virtual_lobes": query.include_virtual_lobes,
                "context_keys": tuple(sorted(str(key) for key in query.context)),
                "metadata_keys": tuple(sorted(str(key) for key in query.metadata)),
            },
        )

    async def _execute_aggregate_lobe_turn(
        self,
        *,
        command: AggregateChatCompletionsCommandV1,
        plan: AggregateRolePlanResultV1,
        completion_id: str,
        objective: str,
        selected_lobe: AggregateRoleLobeV1,
        chain_turn_index: int = 0,
        prior_handoffs: tuple[Mapping[str, Any], ...] = (),
    ) -> tuple[RoleExecutionResultV1, str]:
        selected_role = _select_aggregate_execution_role(
            plan=plan,
            command=command,
            selected_lobe=selected_lobe,
        )
        session_id = command.session_id or f"{completion_id}-session"
        run_id = command.run_id or completion_id
        task_suffix = "single_turn" if command.execution_mode == "single_turn" else f"lobe_chain:{chain_turn_index}"
        memory_recall_pack = _build_aggregate_memory_recall_pack(
            command=command,
            plan=plan,
            selected_lobe=selected_lobe,
            prior_handoffs=prior_handoffs,
        )
        contextos_attention_budget_pack = _build_aggregate_contextos_attention_budget_pack(
            command=command,
            plan=plan,
            selected_lobe=selected_lobe,
            prior_handoffs=prior_handoffs,
        )
        task_market_projection_pack = _build_aggregate_task_market_projection_pack(command=command)
        context_governance_pack = _build_aggregate_context_governance_pack(
            command=command,
            selected_lobe=selected_lobe,
            selected_role=selected_role,
            chain_turn_index=chain_turn_index,
        )
        distilled_knowledge_pack = _build_aggregate_distilled_knowledge_pack(
            command=command,
            plan=plan,
            selected_lobe=selected_lobe,
            selected_role=selected_role,
        )
        lobe_turn_envelope = _build_aggregate_lobe_turn_envelope(
            command=command,
            plan=plan,
            selected_lobe=selected_lobe,
            selected_role=selected_role,
            chain_turn_index=chain_turn_index,
            prior_handoffs=prior_handoffs,
            memory_recall_pack=memory_recall_pack,
            contextos_attention_budget_pack=contextos_attention_budget_pack,
            task_market_projection_pack=task_market_projection_pack,
            context_governance_pack=context_governance_pack,
            distilled_knowledge_pack=distilled_knowledge_pack,
        )
        role_command = ExecuteRoleSessionCommandV1(
            role=selected_role,
            session_id=session_id,
            workspace=command.workspace,
            user_message=lobe_turn_envelope,
            run_id=run_id,
            task_id=f"{completion_id}:{task_suffix}",
            domain=command.domain,
            history=_aggregate_history_from_messages(command.messages),
            context=_aggregate_execution_context(
                command=command,
                plan=plan,
                selected_lobe=selected_lobe,
                selected_role=selected_role,
                chain_turn_index=chain_turn_index,
                prior_handoffs=prior_handoffs,
                memory_recall_pack=memory_recall_pack,
                contextos_attention_budget_pack=contextos_attention_budget_pack,
                task_market_projection_pack=task_market_projection_pack,
                context_governance_pack=context_governance_pack,
                distilled_knowledge_pack=distilled_knowledge_pack,
            ),
            metadata=_aggregate_execution_metadata(
                command=command,
                plan=plan,
                selected_lobe=selected_lobe,
                selected_role=selected_role,
                chain_turn_index=chain_turn_index,
                prior_handoffs=prior_handoffs,
                memory_recall_pack=memory_recall_pack,
                contextos_attention_budget_pack=contextos_attention_budget_pack,
                task_market_projection_pack=task_market_projection_pack,
                context_governance_pack=context_governance_pack,
                distilled_knowledge_pack=distilled_knowledge_pack,
            ),
            stream=True,
        )
        content_chunks: list[str] = []
        thinking_chunks: list[str] = []
        tool_calls: list[str] = []
        error_messages: list[str] = []
        fingerprint: dict[str, Any] | None = None
        final_result: RoleExecutionResultV1 | None = None
        async for event in self.stream_chat_turn(role_command):
            event_type = str(event.get("type") or "")
            if event_type == "fingerprint":
                fingerprint = {
                    "profile_id": event.get("profile_id"),
                    "profile_hash": event.get("profile_hash"),
                    "bundle_id": event.get("bundle_id"),
                    "bundle_version": event.get("bundle_version"),
                    "run_id": event.get("run_id"),
                    "turn_index": event.get("turn_index"),
                    "cognitive_strategy_override_applied": bool(event.get("cognitive_strategy_override_applied")),
                }
            elif event_type == "content_chunk":
                content_chunks.append(str(event.get("content") or ""))
            elif event_type == "thinking_chunk":
                thinking_chunks.append(str(event.get("content") or ""))
            elif event_type == "tool_call":
                tool_name = str(event.get("tool") or "").strip()
                if tool_name:
                    tool_calls.append(tool_name)
            elif event_type == "error":
                error_messages.append(str(event.get("error") or "role runtime stream error"))
            elif event_type == "complete":
                maybe_result = event.get("result")
                if isinstance(maybe_result, RoleTurnResult):
                    final_result = _to_contract_result(
                        role=selected_role,
                        workspace=command.workspace,
                        task_id=f"{completion_id}:{task_suffix}",
                        session_id=session_id,
                        run_id=run_id,
                        result=maybe_result,
                    )
                    event_metadata = event.get("metadata")
                    if isinstance(event_metadata, Mapping):
                        final_result = _with_result_metadata_patch(final_result, dict(event_metadata))

        aggregate_patch = {
            "aggregate_runtime": {
                "aggregate_model_id": plan.aggregate_model_id,
                "execution_mode": command.execution_mode,
                "selected_lobe_id": selected_lobe.lobe_id,
                "selected_role_id": selected_role,
                "chain_turn_index": chain_turn_index,
                "runtime_integrations_wired": [
                    item.tech_id for item in plan.runtime_integrations if item.status == "wired"
                ],
                "context_governance_status": context_governance_pack.get("status") or "skipped",
                "distilled_knowledge_status": distilled_knowledge_pack.get("status") or "skipped",
            },
            "context_governance": {
                "status": context_governance_pack.get("status") or "skipped",
                "retrieval_candidate_count": (context_governance_pack.get("graph_constrained_retrieval") or {}).get(
                    "candidate_count", 0
                ),
            },
            "distilled_knowledge": {
                "status": distilled_knowledge_pack.get("status") or "skipped",
                "total_available": distilled_knowledge_pack.get("total_available", 0),
                "knowledge_unit_count": len(distilled_knowledge_pack.get("knowledge_units") or ()),
            },
        }
        if fingerprint is not None:
            aggregate_patch["strategy_fingerprint"] = fingerprint
        if final_result is not None:
            distillation_pack = _distill_aggregate_lobe_result(
                command=command,
                plan=plan,
                selected_lobe=selected_lobe,
                selected_role=selected_role,
                chain_turn_index=chain_turn_index,
                result=final_result,
            )
            aggregate_patch["knowledge_distillation"] = distillation_pack
            aggregate_patch["aggregate_runtime"]["knowledge_distillation_status"] = (
                distillation_pack.get("status") or "skipped"
            )
            return _with_result_metadata_patch(final_result, aggregate_patch), selected_role

        error_text = "; ".join(message for message in error_messages if message)
        ok = not error_text
        fallback_result = RoleExecutionResultV1(
            ok=ok,
            status="ok" if ok else "failed",
            role=selected_role,
            workspace=command.workspace,
            task_id=f"{completion_id}:{task_suffix}",
            session_id=session_id,
            run_id=run_id,
            output="".join(content_chunks),
            thinking="".join(thinking_chunks) or None,
            tool_calls=tuple(tool_calls),
            usage={"stream_collected": True, "tool_calls_count": len(tool_calls)},
            metadata={},
            error_code=None if ok else "aggregate_single_turn_failed",
            error_message=error_text or None,
        )
        distillation_pack = _distill_aggregate_lobe_result(
            command=command,
            plan=plan,
            selected_lobe=selected_lobe,
            selected_role=selected_role,
            chain_turn_index=chain_turn_index,
            result=fallback_result,
        )
        aggregate_patch["knowledge_distillation"] = distillation_pack
        aggregate_patch["aggregate_runtime"]["knowledge_distillation_status"] = (
            distillation_pack.get("status") or "skipped"
        )
        return _with_result_metadata_patch(fallback_result, aggregate_patch), selected_role

    async def _execute_aggregate_single_turn(
        self,
        *,
        command: AggregateChatCompletionsCommandV1,
        plan: AggregateRolePlanResultV1,
        completion_id: str,
        objective: str,
    ) -> tuple[RoleExecutionResultV1, AggregateRoleLobeV1, str]:
        selected_lobe = _select_aggregate_execution_lobe(plan)
        execution_result, selected_role = await self._execute_aggregate_lobe_turn(
            command=command,
            plan=plan,
            completion_id=completion_id,
            objective=objective,
            selected_lobe=selected_lobe,
        )
        return execution_result, selected_lobe, selected_role

    async def _execute_aggregate_lobe_chain(
        self,
        *,
        command: AggregateChatCompletionsCommandV1,
        plan: AggregateRolePlanResultV1,
        completion_id: str,
        objective: str,
    ) -> tuple[
        tuple[RoleExecutionResultV1, ...],
        tuple[AggregateRoleLobeV1, ...],
        tuple[str, ...],
        tuple[Mapping[str, Any], ...],
    ]:
        chain_lobes = _select_aggregate_lobe_chain(plan=plan, command=command)
        if not chain_lobes:
            raise ValueError("aggregate lobe_chain requires at least one executable lobe")
        results: list[RoleExecutionResultV1] = []
        roles: list[str] = []
        handoffs: list[Mapping[str, Any]] = []
        for index, lobe in enumerate(chain_lobes):
            result, role = await self._execute_aggregate_lobe_turn(
                command=command,
                plan=plan,
                completion_id=completion_id,
                objective=objective,
                selected_lobe=lobe,
                chain_turn_index=index,
                prior_handoffs=tuple(handoffs),
            )
            results.append(result)
            roles.append(role)
            handoffs.append(
                _aggregate_handoff_from_result(
                    sequence=index,
                    selected_lobe=lobe,
                    selected_role=role,
                    result=result,
                )
            )
            if not result.ok:
                break
        return tuple(results), chain_lobes[: len(results)], tuple(roles), tuple(handoffs)

    async def chat_completions(
        self,
        command: AggregateChatCompletionsCommandV1,
    ) -> AggregateChatCompletionsResultV1:
        """Expose Polaris role composition behind a model-shaped interface.

        `plan_only` returns a deterministic lobe plan. `single_turn` selects one
        concrete role lobe. `lobe_chain` executes a bounded sequence of concrete
        roles through the normal streamed role runtime so strategy fingerprints,
        ContextOS preflight, Turn Ledger, and Cognitive Runtime receipts remain
        active.
        """
        objective = _aggregate_objective_from_messages(command.messages)
        plan = await self.build_aggregate_role_plan(
            BuildAggregateRolePlanQueryV1(
                workspace=command.workspace,
                objective=objective,
                role_ids=command.role_ids,
                failure_signals=command.failure_signals,
                failure_evidence=command.failure_evidence,
                domain=command.domain,
                include_virtual_lobes=command.include_virtual_lobes,
                context=command.context,
                metadata=command.metadata,
            )
        )
        completion_id = _stable_completion_id(command, objective)
        execution_result: RoleExecutionResultV1 | None = None
        execution_results: tuple[RoleExecutionResultV1, ...] = ()
        selected_lobe: AggregateRoleLobeV1 | None = None
        selected_role: str | None = None
        selected_lobes: tuple[AggregateRoleLobeV1, ...] = ()
        selected_roles: tuple[str, ...] = ()
        handoffs: tuple[Mapping[str, Any], ...] = ()
        if command.execution_mode == "single_turn":
            execution_result, selected_lobe, selected_role = await self._execute_aggregate_single_turn(
                command=command,
                plan=plan,
                completion_id=completion_id,
                objective=objective,
            )
            content = _render_aggregate_execution_content(
                plan=plan,
                selected_lobe=selected_lobe,
                selected_role=selected_role,
                execution_result=execution_result,
            )
            execution_results = (execution_result,)
        elif command.execution_mode == "lobe_chain":
            execution_results, selected_lobes, selected_roles, handoffs = await self._execute_aggregate_lobe_chain(
                command=command,
                plan=plan,
                completion_id=completion_id,
                objective=objective,
            )
            execution_result = execution_results[-1] if execution_results else None
            selected_lobe = selected_lobes[-1] if selected_lobes else None
            selected_role = selected_roles[-1] if selected_roles else None
            content = _render_aggregate_chain_content(
                plan=plan,
                chain_lobes=selected_lobes,
                chain_roles=selected_roles,
                execution_results=execution_results,
                handoffs=handoffs,
            )
        else:
            content = _render_aggregate_plan_content(plan)
        metadata: dict[str, Any] = {
            "planner": "roles.runtime.aggregate_chat_completions.v1",
            "execution_mode": command.execution_mode,
            "session_id": command.session_id,
            "run_id": command.run_id,
            "aggregate_plan_model_id": plan.aggregate_model_id,
            "stateful": command.execution_mode != "plan_only",
            "runtime_integrations_wired": [
                item.tech_id for item in plan.runtime_integrations if item.status == "wired"
            ],
        }
        if selected_lobe is not None and selected_role is not None:
            metadata["selected_lobe_id"] = selected_lobe.lobe_id
            metadata["selected_role_id"] = selected_role
        if selected_lobes:
            metadata["selected_lobe_ids"] = tuple(lobe.lobe_id for lobe in selected_lobes)
        if selected_roles:
            metadata["selected_role_ids"] = selected_roles
        if handoffs:
            metadata["handoff_count"] = len(handoffs)
        if command.execution_mode == "plan_only":
            metadata["truthful_migration"] = (
                "plan_only chat_completions builds the aggregate lobe plan but does "
                "not claim multi-role execution has run."
            )
        elif command.execution_mode == "lobe_chain":
            metadata["truthful_migration"] = (
                "lobe_chain executes a bounded sequence of concrete current roles selected "
                "from the aggregate plan; virtual lobes remain planning constructs."
            )
        else:
            metadata["truthful_migration"] = (
                "single_turn executes one concrete current role selected from the aggregate "
                "lobe plan; virtual lobes remain planning constructs."
            )
        return AggregateChatCompletionsResultV1(
            id=completion_id,
            object="chat.completion",
            model=command.model,
            choices=(
                AggregateChatChoiceV1(
                    index=0,
                    message=AggregateChatMessageV1(
                        role="assistant",
                        content=content,
                    ),
                    finish_reason="stop",
                ),
            ),
            usage={
                "input_messages": len(command.messages),
                "output_lobes": len(plan.lobes),
                "role_count": len(plan.current_role_ids),
                "execution_mode": command.execution_mode,
                "runtime_integrations": len(plan.runtime_integrations),
                "executed": execution_result is not None,
                "tool_calls_count": len(execution_result.tool_calls) if execution_result is not None else 0,
                "executed_turns": len(execution_results),
                "handoff_count": len(handoffs),
            },
            aggregate_plan=plan,
            execution_result=execution_result,
            execution_results=execution_results,
            metadata=metadata,
        )
