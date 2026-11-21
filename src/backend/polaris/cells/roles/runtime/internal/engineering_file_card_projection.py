"""Request-local FileCard folding, deduplication and pinning."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from typing import Any


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


def fold_file_card_previews(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
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
                    "current_edit" if path in targets else "acceptance_clause" if path in instruction_text else None
                )
                if pin_reason:
                    card["pin_reason"] = pin_reason
                if latest[path] != index and not pin_reason:
                    card["content_preview"] = ""
                    card["content_bounds"] = {**card.get("content_bounds", {}), "truncated": True}
                    card["fold_state"] = "FOLDED"
                    card["fold_reason"] = (
                        "superseded_by_newer_card" if index < latest[path] else "outside_current_focus"
                    )
                else:
                    card["fold_state"] = "ACTIVE"
                projected_message["content"] = json.dumps(payload, ensure_ascii=False)
        folded.append(projected_message)
    return folded
