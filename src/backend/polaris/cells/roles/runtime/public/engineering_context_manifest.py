"""Authoritative ContextOS projection manifest for one stored provider request."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_file_cards import card_from_observation
from polaris.cells.roles.runtime.internal.engineering_memory_cards import FILE_FACTS_HEADER, build_memory_cards
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore, existing_journal


def _text(value: object, limit: int = 180) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _tokens(value: object) -> int:
    return max(1, (len(value) + 3) // 4) if isinstance(value, str) and value else 0


def _ref(event: Mapping[str, Any], card: Mapping[str, Any] | None, command: Mapping[str, Any] | None) -> str | None:
    if command is not None:
        reference = command.get("content_ref")
        if isinstance(reference, str):
            return reference
    if card is not None:
        reference = card.get("content_ref")
        if isinstance(reference, str):
            return reference
    payload = event["payload"]
    for key in ("call_id", "context_snapshot_ref", "provider_request_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _command_card(event: Mapping[str, Any]) -> dict[str, Any] | None:
    if event["kind"] != "tool_observed" or event["payload"].get("tool") != "execute_command":
        return None
    try:
        payload = json.loads(str(event["payload"].get("content") or "{}"))
    except json.JSONDecodeError:
        return None
    card = payload.get("command_card") if isinstance(payload, dict) else None
    return card if isinstance(card, dict) and card.get("schema_version") == "engineering.command_card.v1" else None


def _file_card(event: Mapping[str, Any]) -> dict[str, Any] | None:
    if event["kind"] != "tool_observed" or event["payload"].get("tool") != "read_file":
        return None
    return card_from_observation(event["payload"].get("content", ""))


def _kind(event: Mapping[str, Any], card: Mapping[str, Any] | None, command: Mapping[str, Any] | None) -> str:
    if card is not None:
        return "file_card"
    if command is not None:
        return "command_card"
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


def _title(event: Mapping[str, Any], card: Mapping[str, Any] | None, command: Mapping[str, Any] | None) -> str:
    if command is not None:
        return str(command.get("command") or "execute_command")[:100]
    if card is not None:
        path = card.get("path")
        return path if isinstance(path, str) else ""
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


def _summary(event: Mapping[str, Any], card: Mapping[str, Any] | None, command: Mapping[str, Any] | None) -> str:
    if command is not None:
        return f"{command.get('summary', '')} · exit={command.get('exit_code')}"
    if card is not None:
        return f"{card.get('summary', '')} · stale={card.get('stale', False)}"
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


def _request_index(messages: Sequence[str], ref: str | None) -> int | None:
    if not ref:
        return None
    for index, message in enumerate(messages):
        if ref in message:
            return index
    return None


def query_projection_manifest(workspace: Path, reference: str, snapshot: Mapping[str, Any]) -> dict[str, Any] | None:
    """Read one capture and its historical input watermark in a single read transaction."""
    call_id = snapshot.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        return None
    root = workspace.resolve(strict=True)
    database = root / ".polaris" / "runtime" / "engineering" / "sessions.sqlite3"
    try:
        with existing_journal(root, database) as (connection, _identities):
            matches = connection.execute(
                "SELECT DISTINCT r.session_id, r.workspace, r.instruction, e.role, "
                "json_extract(e.payload,'$.turn_seq') AS turn_seq FROM events e JOIN runs r USING(session_id) "
                "WHERE e.kind='llm_request_observed' AND r.engine_version='engineering_v2' AND r.workspace=? "
                "AND json_extract(e.payload,'$.context_snapshot_ref')=? AND json_extract(e.payload,'$.call_id')=? "
                "AND json_extract(e.payload,'$.snapshot_status')='available' LIMIT 2",
                (str(root), reference, call_id),
            ).fetchall()
            if len(matches) != 1:
                return None
            run = dict(matches[0])
            session, turn = run["session_id"], run["turn_seq"]
            if EngineeringSessionStore._context_request_call_id(connection, session, run["role"], turn) != call_id:
                return None
            prepared = connection.execute(
                "SELECT seq, json_extract(payload,'$.context_input_seq') AS cutoff FROM events "
                "WHERE session_id=? AND kind='request_prepared' AND role=? AND seq>? "
                "AND json_extract(payload,'$.turn_seq')=? ORDER BY seq LIMIT 1",
                (session, run["role"], turn, turn),
            ).fetchone()
            if prepared is None:
                return None
            # Old captures lack a precise input watermark. Only the physical
            # messages can establish what was present in such a request.
            cutoff = prepared["cutoff"]
            if cutoff is None:
                cutoff = turn - 1
            if type(cutoff) is not int or cutoff < 0 or cutoff >= prepared["seq"]:
                return None
            events = [
                {**dict(row), "payload": json.loads(row["payload"])}
                for row in connection.execute(
                    "SELECT seq, kind, role, payload FROM events WHERE session_id=? AND seq<=? ORDER BY seq",
                    (session, cutoff),
                )
            ]
    except FileNotFoundError:
        return None
    state: Mapping[str, Any] = next(
        (event["payload"] for event in reversed(events) if event["kind"] == "run_updated"), {}
    )
    run.update(workspace_revision=state.get("workspace_revision"), verification=state.get("verification", "unknown"))
    result = build_projection_manifest(run, events, snapshot, context_snapshot_ref=reference)
    result["journal_input_seq"] = cutoff
    result["journal_input_scope"] = "captured" if prepared["cutoff"] is not None else "legacy_lower_bound"
    return result


def build_projection_manifest(
    run: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    snapshot: Mapping[str, Any],
    *,
    context_snapshot_ref: str | None = None,
) -> dict[str, Any]:
    """Derive fold provenance from journal facts and the actual request snapshot."""

    provider = snapshot.get("provider_request")
    physical = provider.get("final_physical_request") if isinstance(provider, Mapping) else None
    body = physical.get("body") if isinstance(physical, Mapping) else None
    raw_messages = (
        body.get("messages") if isinstance(body, Mapping) and "messages" in body else snapshot.get("messages")
    )
    messages: list[Mapping[str, Any]] = (
        [message for message in raw_messages if isinstance(message, Mapping)] if isinstance(raw_messages, list) else []
    )
    message_texts = [json.dumps(message, ensure_ascii=False) for message in messages]
    current_revision = run.get("workspace_revision")
    current_ref = context_snapshot_ref or next(
        (
            event["payload"].get("context_snapshot_ref")
            for event in events
            if event["kind"] == "llm_request_observed" and event["payload"].get("call_id") == snapshot.get("call_id")
        ),
        None,
    )
    file_cards = {event["seq"]: card for event in events if (card := _file_card(event)) is not None}
    latest_by_path = {card["path"]: (seq, card) for seq, card in file_cards.items() if not card.get("recovered_from")}
    nodes: list[dict[str, Any]] = []
    evidence_recoveries = 0
    for event in events:
        kind = event["kind"]
        if kind == "llm_request_observed":
            continue
        payload = event["payload"]
        card = file_cards.get(event["seq"])
        command = _command_card(event)
        if (
            kind == "tool_observed"
            and payload.get("ok") is True
            and not (payload.get("interrupted") or payload.get("timed_out"))
            and (payload.get("tool") == "read_command_result" or (card and card.get("recovered_from")))
        ):
            evidence_recoveries += 1
        ref = _ref(event, card, command)
        index = _request_index(message_texts, ref)
        reason_code = "CURRENT_REQUEST"
        if index is not None:
            state, reason, projection_ref = "ACTIVE", "当前最终请求仍包含该节点或证据引用", f"request:{index}"
            projected = _tokens(payload.get("content") or payload.get("feedback") or payload.get("message"))
        elif kind == "run_updated":
            if payload.get("workspace_revision") == current_revision:
                state, reason, projection_ref = (
                    "FOLDED",
                    "已折叠为当前 WorkingState / RunCard 状态投影",
                    f"working-state:{current_revision}",
                )
                reason_code = "CHECKPOINT_REPLACEMENT"
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
            if isinstance(content, str) and content and any(content[:160] in message for message in message_texts):
                state, reason, projection_ref = "ACTIVE", "节点正文仍存在于当前请求", "request:text"
                projected = _tokens(content)
            else:
                state, reason, projection_ref = (
                    "EVICTED",
                    "该历史节点不在当前最终请求中",
                    f"journal:{run['session_id']}:{event['seq']}",
                )
                projected = 0
        node_pin = None
        if card is not None:
            path = str(card["path"])
            pin_reason = (
                "current_edit"
                if any(
                    event["kind"] == "tool_started"
                    and event["payload"].get("tool") in {"write_file", "edit_file"}
                    and event["payload"].get("arguments", {}).get("file") == path
                    for event in events[-12:]
                )
                else "acceptance_clause"
                if path in str(run.get("instruction") or "")
                else None
            )
            node_pin = pin_reason
            if state == "ACTIVE":
                projected = max(1, _tokens(str(card.get("content_preview") or "")))
            elif pin_reason:
                state = "FOLDED"
                reason = f"FileCard pinned by {pin_reason}; full source remains recoverable"
                reason_code = pin_reason
                projection_ref = str(card["content_ref"])
                projected = 24
            elif state == "REFERENCE":
                projection_ref = str(card["content_ref"])
                projected = 24
            latest_seq, latest_card = latest_by_path.get(path, (event["seq"], card))
            if state != "ACTIVE" and latest_seq > event["seq"]:
                state = "FOLDED"
                reason = "superseded by a newer FileCard for the same path"
                reason_code = "DUPLICATE_PAYLOAD"
                projection_ref = str(latest_card["content_ref"])
                projected = 24
        if state in {"REFERENCE", "EVICTED"}:
            reason_code = "INACTIVE_PAYLOAD"
        original_tokens = _tokens(payload.get("content") or payload.get("feedback") or payload.get("message"))
        if command is not None:
            original_tokens = max(
                1, (int(command.get("stdout_bytes", 0)) + int(command.get("stderr_bytes", 0)) + 3) // 4
            )
            if state == "ACTIVE":
                projected = max(
                    1,
                    _tokens(str(command.get("stdout_preview") or ""))
                    + _tokens(str(command.get("stderr_preview") or "")),
                )
            elif state == "REFERENCE":
                projection_ref = str(command["content_ref"])
                projected = 24
        original_tokens = max(1, (int(card["size_bytes"]) + 3) // 4) if card is not None else original_tokens
        nodes.append(
            {
                "id": f"event-{event['seq']}",
                "parent_id": None,
                "seq": event["seq"],
                "turn": payload.get("turn_seq") if isinstance(payload.get("turn_seq"), int) else None,
                "kind": _kind(event, card, command),
                "title": _title(event, card, command),
                "summary": _summary(event, card, command),
                "role": event["role"],
                "fold_state": state,
                "fold_reason": reason,
                "fold_reason_code": reason_code,
                "evidence_ref": ref,
                "projection_ref": projection_ref,
                "request_message_index": index,
                "phase": None,
                "pin_reason": node_pin,
                "superseded_by": projection_ref
                if card is not None and reason == "superseded by a newer FileCard for the same path"
                else None,
                "ok": payload.get("ok") if isinstance(payload.get("ok"), bool) else None,
                "original_tokens": original_tokens,
                "projected_tokens": projected,
            }
        )

    by_turn: dict[int, list[dict[str, Any]]] = defaultdict(list)
    preparations: dict[int, Mapping[str, Any]] = {}
    turns = []
    turn_seq = None
    node_by_seq = {node["seq"]: node for node in nodes}
    for event in events:
        if event["kind"] == "turn_started":
            turn_seq = event["seq"]
            turns.append(event)
        if turn_seq is not None:
            if event["seq"] in node_by_seq:
                by_turn[turn_seq].append(node_by_seq[event["seq"]])
            if event["kind"] == "request_prepared":
                preparations.setdefault(turn_seq, event)
    cards: list[dict[str, Any]] = []
    for turn in turns:
        children = by_turn[turn["seq"]]
        prepared = preparations.get(turn["seq"])
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
    total_nodes = len(nodes)
    unknown_nodes = sum(1 for node in nodes if node["fold_state"] == "UNKNOWN")
    memory_cards = build_memory_cards(run, events)
    for memory in memory_cards:
        if memory["kind"] != "known_facts":
            continue
        count = None
        if isinstance(body, Mapping) and isinstance(body.get("messages"), list):
            visible = []
            for message in messages:
                if message.get("role") != "user":
                    continue
                content = message.get("content")
                texts = (
                    [block.get("text") for block in content if isinstance(block, Mapping)]
                    if isinstance(content, list)
                    else [content]
                )
                for part in texts:
                    if isinstance(part, str) and FILE_FACTS_HEADER in part:
                        try:
                            rows, _ = json.JSONDecoder().raw_decode(part.partition(FILE_FACTS_HEADER)[2])
                        except ValueError:
                            continue
                        if isinstance(rows, list):
                            visible.extend(rows)
            count = sum(entry in visible for entry in memory["entries"])
        memory["wire_entry_count"] = count
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
        "memory_cards": memory_cards,
        "summary": {
            "original_tokens": original,
            "projected_tokens": projected,
            "compression_ratio": (projected / original) if original else None,
            "fold_counts": dict(fold_counts),
            "kind_counts": dict(kind_counts),
            "run_card_count": len(cards),
            "context_health": {
                "fold_rate": (fold_counts.get("FOLDED", 0) / total_nodes) if total_nodes else None,
                "unknown_ratio": (unknown_nodes / total_nodes) if total_nodes else None,
                "evidence_recoveries": evidence_recoveries,
                "memory_card_count": len(memory_cards),
                "epoch_card_count": sum(1 for card in memory_cards if card.get("kind") == "epoch_card"),
                "verification_contract_count": sum(
                    1 for card in memory_cards if card.get("kind") == "verification_contract"
                ),
            },
            "scope": "Token values are approximate character/4 estimates; fold provenance is derived from the same journal and current snapshot, never inferred from node color.",
        },
    }
