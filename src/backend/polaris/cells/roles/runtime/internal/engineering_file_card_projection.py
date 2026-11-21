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


def _activity_indices(messages: Sequence[Mapping[str, Any]]) -> list[int]:
    """Map messages to completed logical activities, not transport retries."""

    indices: list[int] = []
    activity = -1
    for index, message in enumerate(messages):
        if message.get("role") == "assistant":
            activity += 1
        indices.append(max(0, activity))
    return indices


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
    activity_indices = _activity_indices(messages)
    current_activity = activity_indices[-1] if activity_indices else 0
    for index, original in enumerate(messages):
        projected_message: dict[str, Any] = copy.deepcopy(dict(original))
        if projected_message.get("role") == "tool":
            try:
                payload = json.loads(str(projected_message.get("content") or "{}"))
            except json.JSONDecodeError:
                payload = None
            command_card = payload.get("command_card") if isinstance(payload, dict) else None
            if isinstance(command_card, dict):
                if command_card.get("exit_code") != 0:
                    command_card["pin_reason"] = "unresolved_failure"
                activity_age = current_activity - activity_indices[index]
                if activity_age >= _inactive_fold_activities() and not command_card.get("pin_reason"):
                    command_card["stdout_preview"] = ""
                    command_card["stderr_preview"] = ""
                    command_card["fold_state"] = "FOLDED"
                    command_card["fold_reason"] = "outside_current_focus"
                    command_card["last_fold_activity_seq"] = index
                else:
                    command_card["fold_state"] = "ACTIVE"
                projected_message["content"] = json.dumps(payload, ensure_ascii=False)
                folded.append(projected_message)
                continue
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
                activity_age = current_activity - activity_indices[index]
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
                    card["last_fold_activity_seq"] = activity_indices[index]
                elif (rehydrated and activity_age < _rehydration_grace_activities()) or (
                    activity_age < _inactive_fold_activities() and not already_folded
                ):
                    card["fold_state"] = "ACTIVE"
                elif not already_folded:
                    card["content_preview"] = ""
                    card["content_bounds"] = {**card.get("content_bounds", {}), "truncated": True}
                    card["fold_state"] = "FOLDED"
                    card["fold_reason"] = "outside_current_focus"
                    card["last_fold_activity_seq"] = activity_indices[index]
                else:
                    card["fold_state"] = "ACTIVE"
                projected_message["content"] = json.dumps(payload, ensure_ascii=False)
        folded.append(projected_message)
    return folded
