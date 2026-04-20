"""Lightweight introspection helpers for prompt-facing Context OS payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models_v2 import ContextSlicePlanV2 as ContextSlicePlan, RunCardV2 as RunCard


def summarize_context_os_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Summarize a prompt-facing `state_first_context_os` payload.

    The returned shape is intentionally compact and debug-oriented. It exposes
    only bounded runtime metadata and never attempts to reconstruct transcript
    truth or archived artifact bodies.
    """

    if not isinstance(payload, Mapping):
        return {}

    payload_dict = dict(payload)
    run_card_payload = payload_dict.get("run_card")
    slice_plan_payload = payload_dict.get("context_slice_plan")
    budget_plan_payload = payload_dict.get("budget_plan")

    run_card = RunCard.from_mapping(run_card_payload) if isinstance(run_card_payload, dict) else None
    slice_plan = ContextSlicePlan.from_mapping(slice_plan_payload) if isinstance(slice_plan_payload, dict) else None

    current_goal = run_card.current_goal if run_card is not None else str(payload_dict.get("head_anchor") or "").strip()
    next_action_hint = (
        run_card.next_action_hint if run_card is not None else str(payload_dict.get("tail_anchor") or "").strip()
    )
    active_entities = (
        list(run_card.active_entities)
        if run_card is not None
        else _normalize_strings(payload_dict.get("active_entities"))
    )
    active_artifacts = (
        list(run_card.active_artifacts)
        if run_card is not None
        else _normalize_strings(payload_dict.get("active_artifacts"))
    )
    hard_constraints = list(run_card.hard_constraints) if run_card is not None else []
    open_loops = list(run_card.open_loops) if run_card is not None else []
    episode_cards = payload_dict.get("episode_cards")

    result: dict[str, Any] = {
        "present": True,
        "adapter_id": str(payload_dict.get("adapter_id") or "").strip(),
        "run_card_present": run_card is not None or bool(current_goal or next_action_hint),
        "context_slice_plan_present": slice_plan is not None,
        "current_goal": current_goal,
        "next_action_hint": next_action_hint,
        "pressure_level": slice_plan.pressure_level if slice_plan is not None else "",
        "hard_constraint_count": len(hard_constraints),
        "open_loop_count": len(open_loops),
        "active_entity_count": len(active_entities),
        "active_artifact_count": len(active_artifacts),
        "episode_count": len(episode_cards) if isinstance(episode_cards, list) else 0,
        "included_count": len(slice_plan.included) if slice_plan is not None else 0,
        "excluded_count": len(slice_plan.excluded) if slice_plan is not None else 0,
    }

    # Include budget plan info if available (model context window and current token usage)
    if isinstance(budget_plan_payload, dict):
        model_context_window = budget_plan_payload.get("model_context_window")
        current_input_tokens = budget_plan_payload.get("current_input_tokens")
        if isinstance(model_context_window, (int, float)) and model_context_window > 0:
            result["model_context_window"] = int(model_context_window)
        if isinstance(current_input_tokens, (int, float)) and current_input_tokens >= 0:
            result["current_input_tokens"] = int(current_input_tokens)

    return result


def _normalize_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        token = str(item or "").strip()
        if token:
            result.append(token)
    return result


__all__ = ["summarize_context_os_payload"]
