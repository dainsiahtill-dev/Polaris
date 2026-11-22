"""Run-level ContextOS effect report for evaluation and debugging."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


def _count_folded_cards(events: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    """Return (folded_cards, total_cards) from the newest prepared request."""

    best = (0, 0)
    for event in events:
        if event.get("kind") != "request_prepared":
            continue
        request = event["payload"].get("request") or {}
        options = request.get("options") or {}
        messages = options.get("messages") or []
        folded = 0
        total = 0
        for message in messages:
            content = message.get("content")
            if not isinstance(content, str):
                continue
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            nested = payload.get("content")
            if isinstance(nested, str):
                try:
                    payload = json.loads(nested)
                except json.JSONDecodeError:
                    payload = None
            if not isinstance(payload, dict):
                continue
            for key in ("file_card", "command_card"):
                card = payload.get(key)
                if not isinstance(card, dict):
                    continue
                total += 1
                if card.get("fold_state") == "FOLDED":
                    folded += 1
        if total >= best[1]:
            best = (folded, total)
    return best


def build_effect_report(run: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize the real context effect of one run from its journal facts."""

    turns = sum(1 for event in events if event.get("kind") == "turn_started")
    folded, total_cards = _count_folded_cards(events)
    injections = sum(
        1
        for event in events
        if event.get("kind") == "request_prepared"
        and "ContextOS 长期记忆" in json.dumps(event["payload"], ensure_ascii=False)
    )
    cross_run = sum(
        1
        for event in events
        if event.get("kind") == "request_prepared"
        and "ContextOS 跨运行记忆" in json.dumps(event["payload"], ensure_ascii=False)
    )
    recoveries = sum(
        1
        for event in events
        if event.get("kind") == "tool_observed" and event["payload"].get("tool") == "read_command_result"
    )
    transport_failures = sum(
        1
        for event in events
        if event.get("kind") == "message"
        and "provider_transport_failure" in json.dumps(event["payload"], ensure_ascii=False)
    )
    verifications = [event for event in events if event.get("kind") == "verification_completed"]
    passed = sum(1 for event in verifications if event["payload"].get("verification") == "passed")
    executions = [
        event["payload"]
        for event in events
        if event.get("kind") == "validation_execution" and isinstance(event["payload"].get("validation_key"), str)
    ]
    unique_keys = {payload["validation_key"] for payload in executions}
    replayable_requests = max(0, len(executions) - len(unique_keys))
    return {
        "schema_version": "contextos.effect_report.v1",
        "session_id": run.get("session_id"),
        "workspace": run.get("workspace"),
        "status": run.get("status"),
        "verification": run.get("verification"),
        "turns": turns,
        "cards_total": total_cards,
        "cards_folded": folded,
        "fold_rate": (folded / total_cards) if total_cards else None,
        "memory_injected_requests": injections,
        "cross_run_injected_requests": cross_run,
        "evidence_recoveries": recoveries,
        "provider_transport_failures": transport_failures,
        "verification_attempts": len(verifications),
        "verification_passed": passed,
        "validation_executions": len(executions),
        "unique_validation_keys": len(unique_keys),
        "replayable_validation_requests": replayable_requests,
    }
