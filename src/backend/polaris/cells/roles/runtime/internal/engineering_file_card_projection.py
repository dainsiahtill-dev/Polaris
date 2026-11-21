"""Request-local FileCard folding, deduplication and pinning."""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Mapping, Sequence
from typing import Any


def _inactive_fold_activities() -> int:
    return max(0, int(os.environ.get("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "3")))


def _rehydration_grace_activities() -> int:
    return max(0, int(os.environ.get("KERNELONE_CONTEXTOS_REHYDRATION_GRACE_ACTIVITIES", "2")))


def _write_targets(messages: Sequence[Mapping[str, Any]]) -> set[str]:
    targets: set[str] = set()
    for message in messages[-12:]:
        for call in message.get("tool_calls") or []:
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict) or function.get("name") not in {"write_file", "edit_file"}:
                continue
            try:
                arguments = json.loads(str(function.get("arguments") or "{}"))
            except json.JSONDecodeError:
                continue
            path = arguments.get("file")
            if isinstance(path, str) and path:
                targets.add(path)
    return targets


def fold_file_card_previews(
    messages: Sequence[Mapping[str, Any]], *, semantic_pins: Mapping[str, str] | None = None
) -> list[dict[str, Any]]:
    """Fold duplicate/non-focused FileCards without changing durable observations."""

    latest: dict[str, int] = {}
    for index, message in enumerate(messages):
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(str(message.get("content") or "{}"))
        except json.JSONDecodeError:
            continue
        card = payload.get("file_card") if isinstance(payload, dict) else None
        if isinstance(card, dict) and isinstance(card.get("path"), str):
            latest[card["path"]] = index
    targets = _write_targets(messages)
    instruction_text = json.dumps(
        [
            message
            for message in messages[:8]
            if message.get("role") in {"system", "user"} and not message.get("tool_calls")
        ],
        ensure_ascii=False,
    )
    folded: list[dict[str, Any]] = []
    for index, original in enumerate(messages):
        projected_message: dict[str, Any] = copy.deepcopy(dict(original))
        if projected_message.get("role") == "tool":
            try:
                payload = json.loads(str(projected_message.get("content") or "{}"))
            except json.JSONDecodeError:
                payload = None
            card = payload.get("file_card") if isinstance(payload, dict) else None
            if isinstance(card, dict) and isinstance(card.get("path"), str):
                path = card["path"]
                pin_reason = (
                    "current_edit"
                    if path in targets
                    else "acceptance_clause"
                    if path in instruction_text
                    else (semantic_pins or {}).get(path)
                )
                if pin_reason:
                    card["pin_reason"] = pin_reason
                activity_age = len(messages) - 1 - index
                already_folded = card.get("fold_state") == "FOLDED"
                rehydrated = card.get("recovered_from") is not None
                duplicate = latest[path] != index
                if (
                    duplicate
                    and not already_folded
                    and pin_reason not in {"current_edit", "acceptance_clause", "semantic_relevance"}
                ):
                    card["content_preview"] = ""
                    card["content_bounds"] = {**card.get("content_bounds", {}), "truncated": True}
                    card["fold_state"] = "FOLDED"
                    card["fold_reason"] = "superseded_by_newer_card"
                    card["last_fold_activity_seq"] = index
                elif (rehydrated and activity_age < _rehydration_grace_activities()) or (
                    activity_age < _inactive_fold_activities() and not already_folded
                ):
                    card["fold_state"] = "ACTIVE"
                elif not already_folded:
                    card["content_preview"] = ""
                    card["content_bounds"] = {**card.get("content_bounds", {}), "truncated": True}
                    card["fold_state"] = "FOLDED"
                    card["fold_reason"] = "outside_current_focus"
                    card["last_fold_activity_seq"] = index
                else:
                    card["fold_state"] = "ACTIVE"
                projected_message["content"] = json.dumps(payload, ensure_ascii=False)
        folded.append(projected_message)
    return folded
