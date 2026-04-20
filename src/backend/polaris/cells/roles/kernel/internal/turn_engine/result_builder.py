"""Result builder - Construct RoleTurnResult and turn metadata.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

职责：
    封装 TurnEngine 中结果构造逻辑，包括：
    - turn 元数据（model、provider、context_budget 等）
    - RoleTurnResult 的最终组装
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.conversation_state import ConversationState
    from polaris.cells.roles.profile.internal.schema import RoleTurnRequest
    from polaris.cells.roles.profile.public.service import RoleProfile


class ResultBuilder:
    """Builds turn metadata and final RoleTurnResult objects."""

    @staticmethod
    def build_turn_result_metadata(
        *,
        state: ConversationState,
        request: RoleTurnRequest,
        role: str,
        profile: RoleProfile | None = None,
    ) -> dict[str, Any]:
        """Build metadata dict for turn result (model, provider, context_budget).

        Args:
            state: ConversationState instance.
            request: RoleTurnRequest instance.
            role: Role identifier.
            profile: Optional RoleProfile for model binding resolution.

        Returns:
            Metadata dict ready to embed in RoleTurnResult.
        """
        request_metadata = dict(request.metadata) if isinstance(getattr(request, "metadata", None), dict) else {}
        context_override: dict[str, Any] = (
            dict(request.context_override) if request.context_override is not None else {}
        )
        raw_projection_version = request_metadata.get("projection_version")
        if raw_projection_version in (None, ""):
            state_first_context_os = context_override.get("context_os_snapshot")
            if isinstance(state_first_context_os, dict):
                version = state_first_context_os.get("version")
                if version not in (None, ""):
                    raw_projection_version = f"state_first_context_os.v{version}"

        turn_envelope: dict[str, Any] = {
            "turn_id": str(getattr(state, "turn_id", "") or "").strip(),
            "projection_version": str(raw_projection_version or "").strip() or None,
            "lease_id": str(request_metadata.get("lease_id") or "").strip() or None,
            "validation_id": str(request_metadata.get("validation_id") or "").strip() or None,
            "receipt_ids": (),
            "session_id": str(request_metadata.get("session_id") or "").strip() or None,
            "run_id": str(getattr(request, "run_id", "") or "").strip() or None,
            "role": str(role or "").strip() or None,
            "task_id": str(getattr(request, "task_id", "") or "").strip() or None,
            "state_version": int(getattr(state, "version", 0) or 0),
        }

        result: dict[str, Any] = {
            "turn_id": turn_envelope["turn_id"],
            "turn_envelope": turn_envelope,
        }

        if profile is not None:
            model = str(getattr(profile, "model", "") or "").strip()
            provider_id = str(getattr(profile, "provider_id", "") or "").strip()

            if not model:
                raise ValueError(
                    f"Role '{role}': profile.model is empty. "
                    "Please configure roles.{role}.model in config/llm/llm_config.json"
                )
            if not provider_id:
                raise ValueError(
                    f"Role '{role}': profile.provider_id is empty. "
                    "Please configure roles.{role}.provider_id in config/llm/llm_config.json"
                )

            result["model"] = model
            result["provider_id"] = provider_id

            if model and provider_id:
                from polaris.kernelone.llm.engine.model_catalog import ModelCatalog

                catalog = ModelCatalog(workspace=".")
                spec = catalog.resolve(provider_id=provider_id, model=model)
                context_budget = {"model_context_window": spec.max_context_tokens}

                state_first_context_os = context_override.get("context_os_snapshot")
                if isinstance(state_first_context_os, dict):
                    budget_plan = state_first_context_os.get("budget_plan")
                    if isinstance(budget_plan, dict):
                        current_input_tokens = budget_plan.get("current_input_tokens")
                        if isinstance(current_input_tokens, (int, float)) and current_input_tokens >= 0:
                            context_budget["current_input_tokens"] = int(current_input_tokens)

                result["context_budget"] = context_budget
        else:
            raise ValueError(f"Role '{role}': profile is None - cannot determine model binding")

        return result

    @staticmethod
    def build_run_result(
        *,
        final_content: str,
        final_thinking: str | None,
        all_tool_calls: list[dict[str, Any]],
        all_tool_results: list[dict[str, Any]],
        all_blocked_calls: list[dict[str, Any]],
        profile: RoleProfile,
        fingerprint: Any,
        tool_run_index: int,
        state: ConversationState,
        controller: Any,
        result_metadata: dict[str, Any],
        error: str | None = None,
        is_complete: bool | None = None,
        last_policy_result: Any | None = None,
        recovery_state: dict[str, Any] | None = None,
    ) -> Any:
        """Build final RoleTurnResult from accumulated turn data.

        Args:
            final_content: Final assistant content.
            final_thinking: Final thinking content.
            all_tool_calls: Accumulated tool call records.
            all_tool_results: Accumulated tool result records.
            all_blocked_calls: Accumulated blocked call records.
            profile: RoleProfile instance.
            fingerprint: Prompt fingerprint.
            tool_run_index: Number of tool loop rounds executed.
            state: ConversationState instance.
            controller: ToolLoopController instance.
            result_metadata: Pre-built metadata dict.
            error: Optional error message.
            is_complete: Optional completion flag.
            last_policy_result: Optional last PolicyLayer evaluation result.
            recovery_state: Optional recovery state machine status.

        Returns:
            RoleTurnResult instance.
        """
        from polaris.cells.roles.profile.internal.schema import RoleTurnResult

        computed_complete = (error is None) if is_complete is None else bool(is_complete)

        turn_history: list[tuple[str, str]] = [e.to_tuple() for e in controller._history]

        turn_events_metadata: list[dict[str, Any]] = [
            {
                "event_id": e.event_id,
                "role": e.role,
                "content": e.content,
                "sequence": e.sequence,
                "metadata": dict(e.metadata),
            }
            for e in controller._history
        ]

        if final_content and (not turn_history or turn_history[-1][0] != "assistant"):
            turn_history.append(("assistant", final_content))
            turn_events_metadata.append(
                {
                    "event_id": f"assistant_{len(controller._history)}",
                    "role": "assistant",
                    "content": final_content,
                    "sequence": len(controller._history),
                    "metadata": {"kind": "assistant_turn"},
                }
            )

        tool_execution_error: str | None = None
        should_retry = False
        for tr in all_tool_results:
            if isinstance(tr, dict):
                ok_flag = tr.get("ok")
                success_flag = tr.get("success")
                is_failure = ok_flag is False or success_flag is False
                if is_failure:
                    et = tr.get("error_type")
                    if not et:
                        payload = tr.get("payload", {})
                        if isinstance(payload, dict):
                            et = payload.get("error_type")
                    if et:
                        tool_execution_error = et
                    retryable = tr.get("retryable", False)
                    if not retryable:
                        payload = tr.get("payload", {})
                        if isinstance(payload, dict):
                            retryable = payload.get("retryable", False)
                    if retryable:
                        should_retry = True

        return RoleTurnResult(
            content=final_content,
            thinking=final_thinking,
            tool_calls=list(all_tool_calls),
            tool_results=list(all_tool_results),
            profile_version=profile.version,
            prompt_fingerprint=fingerprint,
            tool_policy_id=getattr(profile, "tool_policy_id", "") or getattr(profile.tool_policy, "policy_id", "")
            if profile
            else "",
            execution_stats={
                "tool_loop_rounds": tool_run_index,
                "tool_calls_count": len(all_tool_calls),
                "tool_results_count": len(all_tool_results),
                "blocked_calls_count": len(all_blocked_calls),
                "blocked_calls": list(all_blocked_calls),
                "turn_count": state.budgets.turn_count,
            },
            error=error,
            is_complete=computed_complete,
            needs_confirmation=bool(last_policy_result and last_policy_result.has_approval_required),
            tool_execution_error=tool_execution_error,
            should_retry=should_retry,
            turn_history=turn_history,
            turn_events_metadata=turn_events_metadata,
            metadata={
                **result_metadata,
                "recovery_state": recovery_state,
            },
        )


__all__ = ["ResultBuilder"]
