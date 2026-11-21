"""Request-local FileCard folding, deduplication and pinning."""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Mapping, Sequence
from typing import Any


def _inactive_fold_activities() -> int:
    return max(0, int(os.environ.get("KERNELONE_CONTEXTOS_INACTIVE_FOLD_ACTIVITIES", "10")))


def _rehydration_grace_activities() -> int:
    return max(0, int(os.environ.get("KERNELONE_CONTEXTOS_REHYDRATION_GRACE_ACTIVITIES", "2")))


def _fold_min_bytes() -> int:
    return max(0, int(os.environ.get("KERNELONE_CONTEXTOS_FOLD_MIN_BYTES", "512")))


def _card_payload(payload: object) -> tuple[dict[str, Any] | None, bool]:
    """Unwrap tool envelopes whose card JSON is nested in content."""

    if not isinstance(payload, dict):
        return None, False
    if isinstance(payload.get("file_card"), dict) or isinstance(payload.get("command_card"), dict):
        return payload, False
    content = payload.get("content")
    if isinstance(content, str):
        try:
            nested = json.loads(content)
        except json.JSONDecodeError:
            return None, False
        if isinstance(nested, dict) and (
            isinstance(nested.get("file_card"), dict) or isinstance(nested.get("command_card"), dict)
        ):
            return nested, True
    return None, False


def _activity_indices(messages: Sequence[Mapping[str, Any]]) -> list[int]:
    """Map messages to completed logical activities, not transport retries."""

    indices: list[int] = []
    activity = -1
    for _index, message in enumerate(messages):
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


def folded_card_projections(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Describe folded snapshots for realtime UI events and idempotent provenance."""

    projections: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(str(message.get("content") or "{}"))
        except json.JSONDecodeError:
            continue
        card_payload, _nested = _card_payload(payload)
        if card_payload is None:
            continue
        for node_kind in ("file_card", "command_card"):
            card = card_payload.get(node_kind)
            if not isinstance(card, dict) or card.get("fold_state") != "FOLDED":
                continue
            reference = card.get("content_ref") or card.get("snapshot_ref")
            if not isinstance(reference, str) or not reference:
                continue
            reason = str(card.get("fold_reason") or "outside_current_focus")
            projections.append(
                {
                    "node_kind": node_kind,
                    "path": card.get("path") or card.get("command") or reference,
                    "projection_ref": reference,
                    "fold_state": "FOLDED",
                    "fold_reason_code": "DUPLICATE_PAYLOAD"
                    if reason == "superseded_by_newer_card"
                    else "INACTIVE_PAYLOAD",
                    "fold_reason": reason,
                    "activity_seq": card.get("last_fold_activity_seq"),
                }
            )
    return projections


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
        card_payload, _nested = _card_payload(payload)
        card = card_payload.get("file_card") if card_payload is not None else None
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
            card_payload, nested = _card_payload(payload)
            command_card = card_payload.get("command_card") if card_payload is not None else None
            if isinstance(command_card, dict):
                reference = command_card.get("content_ref")
                advisor_view = (semantic_pins or {}).get(str(reference)) if reference else None
                if command_card.get("exit_code") != 0:
                    command_card["pin_reason"] = "unresolved_failure"
                elif advisor_view:
                    command_card["pin_reason"] = advisor_view
                activity_age = current_activity - activity_indices[index]
                already_folded = command_card.get("fold_state") == "FOLDED"
                large_payload = (
                    len(str(command_card.get("stdout_preview") or "").encode("utf-8"))
                    + len(str(command_card.get("stderr_preview") or "").encode("utf-8"))
                    >= _fold_min_bytes()
                )
                if already_folded:
                    command_card["fold_state"] = "FOLDED"
                elif (
                    activity_age >= _inactive_fold_activities()
                    and advisor_view in {"semantic_card", "semantic_reference"}
                    and command_card.get("pin_reason") != "unresolved_failure"
                    and large_payload
                ):
                    command_card["stdout_preview"] = ""
                    command_card["stderr_preview"] = ""
                    command_card["fold_state"] = "FOLDED"
                    command_card["fold_reason"] = "outside_current_focus"
                    command_card["last_fold_activity_seq"] = activity_indices[index]
                else:
                    command_card["fold_state"] = "ACTIVE"
                if nested and isinstance(payload, dict):
                    payload["content"] = json.dumps(card_payload, ensure_ascii=False)
                projected_message["content"] = json.dumps(payload, ensure_ascii=False)
                folded.append(projected_message)
                continue
            card = card_payload.get("file_card") if card_payload is not None else None
            if isinstance(card, dict) and isinstance(card.get("path"), str):
                path = card["path"]
                advisor_view = (semantic_pins or {}).get(path)
                pin_reason = (
                    "current_edit"
                    if path in targets
                    else "acceptance_clause"
                    if path in instruction_text
                    else advisor_view
                )
                if pin_reason:
                    card["pin_reason"] = pin_reason
                activity_age = current_activity - activity_indices[index]
                already_folded = card.get("fold_state") == "FOLDED"
                rehydrated = card.get("recovered_from") is not None
                duplicate = latest[path] != index
                small_payload = len(str(card.get("content_preview") or "").encode("utf-8")) < _fold_min_bytes()
                advisor_fold = advisor_view in {"semantic_card", "semantic_reference"}
                if already_folded:
                    card["fold_state"] = "FOLDED"
                elif small_payload:
                    card["fold_state"] = "ACTIVE"
                elif (
                    duplicate
                    and advisor_fold
                    and pin_reason not in {"current_edit", "acceptance_clause", "semantic_relevance"}
                ):
                    card["content_preview"] = ""
                    card["content_bounds"] = {**card.get("content_bounds", {}), "truncated": True}
                    card["fold_state"] = "FOLDED"
                    card["fold_reason"] = "superseded_by_newer_card"
                    card["last_fold_activity_seq"] = activity_indices[index]
                elif (rehydrated and activity_age < _rehydration_grace_activities()) or (
                    activity_age < _inactive_fold_activities()
                ):
                    card["fold_state"] = "ACTIVE"
                elif advisor_fold and pin_reason not in {"current_edit", "acceptance_clause", "semantic_relevance"}:
                    card["content_preview"] = ""
                    card["content_bounds"] = {**card.get("content_bounds", {}), "truncated": True}
                    card["fold_state"] = "FOLDED"
                    card["fold_reason"] = "outside_current_focus"
                    card["last_fold_activity_seq"] = activity_indices[index]
                else:
                    card["fold_state"] = "ACTIVE"
                if nested and isinstance(payload, dict):
                    payload["content"] = json.dumps(card_payload, ensure_ascii=False)
                projected_message["content"] = json.dumps(payload, ensure_ascii=False)
        folded.append(projected_message)
    return folded
