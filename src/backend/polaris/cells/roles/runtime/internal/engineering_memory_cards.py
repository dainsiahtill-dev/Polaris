"""Deterministic long-term memory cards derived from engineering journal facts."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_acceptance import extract_goal_clauses
from polaris.cells.roles.runtime.internal.engineering_file_cards import card_from_observation


def _command_card(event: Mapping[str, Any]) -> dict[str, Any] | None:
    if event["payload"].get("tool") != "execute_command":
        return None
    try:
        payload = json.loads(str(event["payload"].get("content") or "{}"))
    except json.JSONDecodeError:
        return None
    card = payload.get("command_card") if isinstance(payload, dict) else None
    return card if isinstance(card, dict) else None


def _id(kind: str, value: str) -> str:
    return f"{kind}:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:20]}"


def _failure_signatures(text: str) -> list[str]:
    signatures: list[str] = []
    for line in text.splitlines():
        if "[FAIL]" not in line and "✗" not in line and "ReferenceError" not in line and "Error:" not in line:
            continue
        normalized = re.sub(r"\s+", " ", line.strip())
        normalized = normalized.split("——", 1)[0].split(" | ", 1)[0]
        if normalized:
            signatures.append(normalized[:240])
    return signatures


def build_failure_cluster_cards(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    clusters: dict[str, dict[str, Any]] = {}
    for event in events:
        if event["kind"] == "verification_completed":
            for check in event["payload"].get("checks", []):
                text = str(check.get("content") or "")
                for signature in _failure_signatures(text):
                    card = clusters.setdefault(
                        signature,
                        {
                            "kind": "failure_cluster",
                            "id": _id("failure-cluster", signature),
                            "title": signature,
                            "count": 0,
                            "first_seq": event["seq"],
                            "last_seq": event["seq"],
                            "evidence_refs": [],
                            "unresolved": True,
                        },
                    )
                    card["count"] += 1
                    card["last_seq"] = event["seq"]
                    reference = check.get("call_id")
                    if isinstance(reference, str) and reference not in card["evidence_refs"]:
                        card["evidence_refs"].append(reference)
        if event["payload"].get("ok") is False:
            command = _command_card(event)
            if command is not None:
                signature = str(command.get("summary") or "command failed")[:240]
                card = clusters.setdefault(
                    signature,
                    {
                        "kind": "failure_cluster",
                        "id": _id("failure-cluster", signature),
                        "title": signature,
                        "count": 0,
                        "first_seq": event["seq"],
                        "last_seq": event["seq"],
                        "evidence_refs": [],
                        "unresolved": True,
                    },
                )
                card["count"] += 1
                card["last_seq"] = event["seq"]
                reference = command.get("content_ref")
                if isinstance(reference, str) and reference not in card["evidence_refs"]:
                    card["evidence_refs"].append(reference)
    if any(
        event["kind"] == "verification_completed" and event["payload"].get("verification") == "passed"
        for event in events
    ):
        for card in clusters.values():
            card["unresolved"] = False
    for card in clusters.values():
        card["summary"] = f"{card['count']} 次 · {'未解决' if card['unresolved'] else '后续验证已通过'}"
        card["evidence_refs"] = card["evidence_refs"][-12:]
    return sorted(clusters.values(), key=lambda card: (not card["unresolved"], -card["count"], card["id"]))


def build_interface_contract_cards(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for event in events:
        if event["payload"].get("tool") != "read_file" or event["payload"].get("ok") is not True:
            continue
        card = card_from_observation(str(event["payload"].get("content") or ""))
        if card is not None:
            latest[str(card["path"])] = card
    return [
        {
            "kind": "interface_contract",
            "id": _id("interface", path),
            "title": path,
            "summary": f"{len(card.get('symbols') or [])} 个导出符号 · revision {card.get('revision')}",
            "symbols": card.get("symbols") or [],
            "sha256": card.get("sha256"),
            "evidence_refs": [card["content_ref"]] if card.get("content_ref") else [],
        }
        for path, card in sorted(latest.items())
    ]


def build_tool_pattern_cards(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for event in events:
        card = _command_card(event)
        if card is None:
            continue
        command = str(card.get("command") or "")
        match = re.search(
            r"(?:^|&&|;|\|)\s*(node|python3?|npm|npx|git|ls|cat|sed|grep|find|xvfb-run|electron|make|go|cargo)\b",
            command,
        )
        pattern = match.group(1) if match else "shell"
        group = groups.setdefault(
            pattern,
            {"kind": "tool_pattern", "id": _id("tool", pattern), "title": pattern, "success": 0, "failed": 0},
        )
        group["success" if card.get("exit_code") == 0 else "failed"] += 1
        group["last_exit_code"] = card.get("exit_code")
        group["summary"] = str(card.get("summary") or "")[:240]
        if card.get("content_ref"):
            group["evidence_refs"] = [card["content_ref"]]
    for group in groups.values():
        group["summary"] = f"成功 {group['success']} · 失败 {group['failed']} · 最近 {group.get('summary', '')}"
    return sorted(groups.values(), key=lambda card: card["title"])


def build_requirement_trace_cards(run: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    clauses = extract_goal_clauses(str(run.get("instruction") or ""))
    findings: list[dict[str, Any]] = []
    for event in events:
        if event["kind"] == "tool_observed" and event["payload"].get("tool") == "report_review":
            try:
                payload = json.loads(str(event["payload"].get("content") or "{}"))
            except json.JSONDecodeError:
                continue
            findings.extend(payload.get("clause_findings") or [])
    cards: list[dict[str, Any]] = []
    for index, clause in enumerate(clauses[:32]):
        matches = [
            item
            for item in findings
            if str(item.get("clause") or "") in clause or clause in str(item.get("clause") or "")
        ]
        latest = matches[-1] if matches else None
        status = str(latest.get("status")) if latest else "unknown"
        cards.append(
            {
                "kind": "requirement_trace",
                "id": _id("requirement", f"{index}:{clause}"),
                "title": clause[:180],
                "summary": status,
                "status": status,
                "evidence_refs": [str(latest.get("evidence"))] if latest and latest.get("evidence") else [],
            }
        )
    return cards


def build_file_diff_cards(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    versions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event["payload"].get("tool") != "read_file" or event["payload"].get("ok") is not True:
            continue
        card = card_from_observation(str(event["payload"].get("content") or ""))
        if card is not None:
            versions[str(card["path"])].append(card)
    cards: list[dict[str, Any]] = []
    for path, cards_for_path in sorted(versions.items()):
        if len(cards_for_path) < 2:
            continue
        first, last = cards_for_path[0], cards_for_path[-1]
        before = str(first.get("content_preview") or "").splitlines()
        after = str(last.get("content_preview") or "").splitlines()
        diff = "\n".join(difflib.unified_diff(before, after, lineterm="", n=1))[:2400]
        cards.append(
            {
                "kind": "file_diff",
                "id": _id("diff", path),
                "title": path,
                "summary": f"{len(cards_for_path)} 个快照 · revision {first.get('revision')} → {last.get('revision')}",
                "diff_preview": diff,
                "evidence_refs": [ref for ref in (first.get("content_ref"), last.get("content_ref")) if ref],
            }
        )
    return cards


def build_evidence_lease_cards(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    leases: list[dict[str, Any]] = []
    for event in events:
        payload = event["payload"]
        card = card_from_observation(str(payload.get("content") or "")) if payload.get("tool") == "read_file" else None
        command = _command_card(event)
        if card is not None and (card.get("recovered_from") or card.get("pin_reason")):
            leases.append(
                {
                    "kind": "evidence_lease",
                    "id": _id("lease", f"{event['seq']}:{card.get('content_ref')}"),
                    "title": card.get("path"),
                    "summary": card.get("pin_reason") or "rehydrated",
                    "pin_reason": card.get("pin_reason") or "rehydrated",
                    "evidence_refs": [card.get("recovered_from") or card.get("content_ref")],
                }
            )
        if command is not None and command.get("pin_reason"):
            leases.append(
                {
                    "kind": "evidence_lease",
                    "id": _id("lease", f"{event['seq']}:{command.get('content_ref')}"),
                    "title": command.get("command"),
                    "summary": command["pin_reason"],
                    "pin_reason": command["pin_reason"],
                    "evidence_refs": [command.get("content_ref")],
                }
            )
    return leases[-32:]


def build_memory_cards(run: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Build all deterministic memory cards without model inference."""

    return [
        *build_failure_cluster_cards(events),
        *build_interface_contract_cards(events),
        *build_requirement_trace_cards(run, events),
        *build_tool_pattern_cards(events),
        *build_file_diff_cards(events),
        *build_evidence_lease_cards(events),
    ]
