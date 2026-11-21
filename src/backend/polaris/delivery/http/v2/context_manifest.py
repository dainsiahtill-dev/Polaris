"""Authoritative ContextOS projection manifest for one stored provider request."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any


def _text(value: object, limit: int = 180) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _tokens(value: object) -> int:
    return max(1, (len(value) + 3) // 4) if isinstance(value, str) and value else 0


def _ref(event: Mapping[str, Any]) -> str | None:
    payload = event["payload"]
    for key in ("call_id", "context_snapshot_ref", "provider_request_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _kind(event: Mapping[str, Any]) -> str:
    kind = event["kind"]
    if kind == "tool_started":
        return "tool_call"
    if kind == "tool_observed":
        return "tool_log"
    if kind == "verification_completed":
        return "verification"
    if kind in {"run_updated", "request_prepared", "turn_started"}:
        return "run_card"
    if kind == "tool_output_capture_completed":
        return "artifact"
    return "message"


def _title(event: Mapping[str, Any]) -> str:
    payload = event["payload"]
    tool = payload.get("tool")
    if isinstance(tool, str) and tool:
        return tool
    if event["kind"] == "run_updated":
        return "RunCard / run_updated"
    if event["kind"] == "request_prepared":
        return "RunCard / provider request"
    if event["kind"] == "turn_started":
        return "RunCard / turn"
    return str(event["kind"])


def _summary(event: Mapping[str, Any]) -> str:
    payload = event["payload"]
    if event["kind"] == "run_updated":
        return f"rev={payload.get('workspace_revision', '—')} · {payload.get('verification', 'unknown')} · {payload.get('status', '')}"
    if event["kind"] == "request_prepared":
        request = payload.get("request") or {}
        if not isinstance(request, dict):
            request = {}
        context = request.get("context") or {}
        if not isinstance(context, dict):
            context = {}
        return f"phase={context.get('phase', 'unknown')} · {payload.get('evidence_kind', '')}"
    if event["kind"] == "verification_completed":
        return f"{payload.get('verification', 'unknown')} · {_text(payload.get('feedback'), 90)}"
    return _text(payload.get("content") or payload.get("feedback") or payload.get("error") or payload.get("message"))


def _request_index(messages: Sequence[Mapping[str, Any]], ref: str | None) -> int | None:
    if not ref:
        return None
    for index, message in enumerate(messages):
        if ref in json.dumps(message, ensure_ascii=False):
            return index
    return None


def build_projection_manifest(
    run: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive fold provenance from journal facts and the actual request snapshot."""

    raw_messages = snapshot.get("messages")
    messages: list[Mapping[str, Any]] = (
        [message for message in raw_messages if isinstance(message, Mapping)] if isinstance(raw_messages, list) else []
    )
    active_wire = json.dumps(snapshot, ensure_ascii=False)
    current_revision = run.get("workspace_revision")
    current_ref = next(
        (
            event["payload"].get("context_snapshot_ref")
            for event in events
            if event["kind"] == "llm_request_observed" and event["payload"].get("call_id") == snapshot.get("call_id")
        ),
        None,
    )
    nodes: list[dict[str, Any]] = []
    for event in events:
        kind = event["kind"]
        if kind == "llm_request_observed":
            continue
        payload = event["payload"]
        ref = _ref(event)
        index = _request_index(messages, ref)
        if index is not None or (ref and ref in active_wire):
            state, reason, projection_ref = "ACTIVE", "当前最终请求仍包含该节点或证据引用", f"request:{index}"
            projected = _tokens(payload.get("content") or payload.get("feedback") or payload.get("message"))
        elif kind == "run_updated":
            if payload.get("workspace_revision") == current_revision:
                state, reason, projection_ref = (
                    "FOLDED",
                    "已折叠为当前 WorkingState / RunCard 状态投影",
                    f"working-state:{current_revision}",
                )
                projected = 24
            else:
                state, reason, projection_ref = (
                    "EVICTED",
                    "该历史 RunCard 已被后续 revision 取代",
                    f"journal:{run['session_id']}:{event['seq']}",
                )
                projected = 0
        elif kind == "request_prepared":
            state, reason, projection_ref = (
                "REFERENCE",
                "该轮请求已退出当前窗口，快照引用仍保留",
                f"journal:{run['session_id']}:{event['seq']}",
            )
            projected = 8
        elif kind in {"tool_observed", "verification_completed", "tool_output_capture_completed"}:
            state, reason, projection_ref = (
                "REFERENCE",
                "原始观察已退出当前请求，但 observation / artifact 引用仍保留",
                f"journal:{run['session_id']}:{event['seq']}",
            )
            projected = 8
        else:
            content = payload.get("content") or payload.get("message")
            if isinstance(content, str) and content and content[:160] in active_wire:
                state, reason, projection_ref = "ACTIVE", "节点正文仍存在于当前请求", "request:text"
                projected = _tokens(content)
            else:
                state, reason, projection_ref = (
                    "EVICTED",
                    "该历史节点不在当前最终请求中",
                    f"journal:{run['session_id']}:{event['seq']}",
                )
                projected = 0
        nodes.append(
            {
                "id": f"event-{event['seq']}",
                "parent_id": None,
                "seq": event["seq"],
                "turn": payload.get("turn_seq") if isinstance(payload.get("turn_seq"), int) else None,
                "kind": _kind(event),
                "title": _title(event),
                "summary": _summary(event),
                "role": event["role"],
                "fold_state": state,
                "fold_reason": reason,
                "evidence_ref": ref,
                "projection_ref": projection_ref,
                "request_message_index": index,
                "phase": None,
                "ok": payload.get("ok") if isinstance(payload.get("ok"), bool) else None,
                "original_tokens": _tokens(payload.get("content") or payload.get("feedback") or payload.get("message")),
                "projected_tokens": projected,
            }
        )

    turns = [event for event in events if event["kind"] == "turn_started"]
    cards: list[dict[str, Any]] = []
    for position, turn in enumerate(turns):
        end = turns[position + 1]["seq"] if position + 1 < len(turns) else 2**63 - 1
        children = [node for node in nodes if turn["seq"] <= node["seq"] < end]
        prepared = next(
            (event for event in events if turn["seq"] <= event["seq"] < end and event["kind"] == "request_prepared"),
            None,
        )
        phase = None
        if prepared:
            request = prepared["payload"].get("request") or {}
            if not isinstance(request, dict):
                request = {}
            context = request.get("context") or {}
            if not isinstance(context, dict):
                context = {}
            phase = str(context.get("phase") or "unknown")
        card = {
            "id": f"turn-{turn['seq']}",
            "seq": turn["seq"],
            "role": turn["role"],
            "phase": phase,
            "verification": run.get("verification"),
            "workspace_revision": current_revision,
            "child_ids": [node["id"] for node in children],
        }
        cards.append(card)
        for node in children:
            node["parent_id"] = card["id"]
            if phase and node["kind"] == "run_card":
                node["phase"] = phase

    for index, message in enumerate(messages):
        content = message.get("content") if isinstance(message.get("content"), str) else ""
        nodes.append(
            {
                "id": f"request-{index}",
                "parent_id": cards[-1]["id"] if cards else None,
                "seq": None,
                "turn": None,
                "kind": "request",
                "title": f"{message.get('role', 'message')} · 当前请求",
                "summary": _text(content, 150),
                "role": str(message.get("role") or "unknown"),
                "fold_state": "ACTIVE",
                "fold_reason": "该消息存在于当前最终请求快照",
                "evidence_ref": None,
                "projection_ref": f"request:{index}",
                "request_message_index": index,
                "phase": None,
                "ok": None,
                "original_tokens": _tokens(content),
                "projected_tokens": _tokens(content),
            }
        )
        if cards:
            cards[-1]["child_ids"].append(f"request-{index}")

    fold_counts = Counter(node["fold_state"] for node in nodes)
    kind_counts = Counter(node["kind"] for node in nodes)
    original = sum(node["original_tokens"] for node in nodes)
    projected = sum(node["projected_tokens"] for node in nodes)
    return {
        "schema_version": "context.projection_manifest.v1",
        "context_snapshot_ref": current_ref,
        "workspace": run["workspace"],
        "run_id": run["session_id"],
        "call_id": snapshot.get("call_id"),
        "nodes": nodes,
        "run_cards": cards,
        "summary": {
            "original_tokens": original,
            "projected_tokens": projected,
            "compression_ratio": (projected / original) if original else None,
            "fold_counts": dict(fold_counts),
            "kind_counts": dict(kind_counts),
            "run_card_count": len(cards),
            "scope": "Token values are approximate character/4 estimates; fold provenance is derived from the same journal and current snapshot, never inferred from node color.",
        },
    }
