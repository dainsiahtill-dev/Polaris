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


def _failure_text(content: object) -> str:
    """Unwrap a CommandCard JSON check body before scanning for failures."""

    if not isinstance(content, str) or not content.strip():
        return ""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return content
    if not isinstance(payload, dict):
        return content
    command = payload.get("command_card")
    if not isinstance(command, dict):
        return content
    return "\n".join(str(command.get(key) or "") for key in ("stdout_preview", "stderr_preview", "summary"))


_FAILURE_MARK = re.compile(
    r"(?:\bfail(?:ed|ure)?\b|\berror\b|referenceerror|assertionerror|typeerror|syntaxerror|runtimeerror"
    r"|\bexception\b|\btraceback\b|\bassert(?:ion)?\b|\[FAIL\]|✗)",
    re.IGNORECASE,
)


def _failure_signatures(text: str) -> list[str]:
    signatures: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("[PASS]"):
            continue
        if not _FAILURE_MARK.search(stripped):
            continue
        normalized = re.sub(r"\s+", " ", stripped)
        normalized = normalized.split("——", 1)[0].split(" | ", 1)[0]
        if normalized:
            signatures.append(normalized[:240])
    return signatures


def build_failure_cluster_cards(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    clusters: dict[str, dict[str, Any]] = {}
    for event in events:
        if event["kind"] == "verification_completed":
            for check in event["payload"].get("checks", []):
                for signature in _failure_signatures(_failure_text(check.get("content"))):
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
                raw_summary = str(command.get("summary") or "").strip()
                signature = (raw_summary if raw_summary and _FAILURE_MARK.search(raw_summary) else "command failed")[
                    :240
                ]
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
    last_passed_seq = max(
        (
            event["seq"]
            for event in events
            if event["kind"] == "verification_completed" and event["payload"].get("verification") == "passed"
        ),
        default=None,
    )
    for card in clusters.values():
        card["unresolved"] = last_passed_seq is None or card["last_seq"] >= last_passed_seq
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


def build_verification_contract_cards(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One reusable contract per verification, bound to its candidate revision."""

    cards: list[dict[str, Any]] = []
    for event in events:
        if event["kind"] != "verification_completed":
            continue
        payload = event["payload"]
        checks = payload.get("checks")
        if not isinstance(checks, list) or not checks:
            continue
        commands: list[str] = []
        evidence: list[str] = []
        exit_codes: list[int] = []
        for check in checks:
            command = str(check.get("tool") or "")
            if command:
                commands.append(command)
            content = str(check.get("content") or "")
            try:
                body = json.loads(content)
            except json.JSONDecodeError:
                body = None
            command_card = body.get("command_card") if isinstance(body, dict) else None
            if isinstance(command_card, dict):
                reference = command_card.get("content_ref")
                if isinstance(reference, str) and reference:
                    evidence.append(reference)
                if isinstance(command_card.get("exit_code"), int):
                    exit_codes.append(int(command_card["exit_code"]))
            elif isinstance(check.get("call_id"), str):
                evidence.append(str(check["call_id"]))
        verdict = str(payload.get("verification") or "unknown")
        revision = payload.get("workspace_revision")
        summary = f"{verdict} · exit={exit_codes or '—'}"
        if revision is not None:
            summary += f" · rev={revision}"
        cards.append(
            {
                "kind": "verification_contract",
                "id": _id("verification", f"{event['seq']}:{verdict}:{revision}"),
                "title": " · ".join(commands)[:180] or "verification",
                "summary": summary,
                "status": verdict,
                "workspace_revision": revision,
                "evidence_refs": evidence[-12:],
                "uncertain": verdict != "passed",
            }
        )
    return cards[-16:]


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


def build_context_memory_messages(
    run: Mapping[str, Any], events: Sequence[Mapping[str, Any]], *, limit_chars: int = 4000
) -> list[dict[str, str]]:
    """Inject the highest-value long-term memory into the provider request.

    Only unresolved failures, the latest verification contract and recent epoch
    summaries are injected, so the model keeps the facts that prevent repeated
    exploration without re-reading the full history.
    """

    cards = build_memory_cards(run, events)
    sections: list[str] = []
    failures = [card for card in cards if card.get("kind") == "failure_cluster" and card.get("unresolved") is not False]
    failures.sort(key=lambda card: (-int(card.get("count") or 0), str(card.get("title") or "")))
    if failures:
        lines = []
        for card in failures[:5]:
            refs = [ref for ref in (card.get("evidence_refs") or []) if isinstance(ref, str)]
            hint = f" · 证据: {', '.join(refs[-2:])}" if refs else ""
            lines.append(f"- {card.get('title')}: {card.get('summary')}{hint}")
        sections.append(
            "未解决失败聚类（避免重复探索；可用 read_command_result/read_file 按证据引用取回原始输出）：\n"
            + "\n".join(lines)
        )
    contracts = [card for card in cards if card.get("kind") == "verification_contract"]
    if contracts:
        latest = contracts[-1]
        refs = [ref for ref in (latest.get("evidence_refs") or []) if isinstance(ref, str)]
        sections.append(
            f"最近验证契约：{latest.get('summary')} · {latest.get('title')}"
            + (f" · 证据: {', '.join(refs[-3:])}" if refs else "")
        )
    decisions = [card for card in cards if card.get("kind") == "decision_journal"]
    if decisions:
        sections.append(
            "已确定的设计决策（除非有新的反证，否则保持）：\n"
            + "\n".join(f"- {card.get('title')}" for card in decisions[-5:])
        )
    epochs = [card for card in cards if card.get("kind") == "epoch_card" and card.get("summary")]
    for card in epochs[-2:]:
        sections.append(f"Epoch 记忆 {card.get('id')}：{card.get('summary')}")
    if not sections:
        return []
    content = "\n\n".join(sections)
    if len(content) > limit_chars:
        content = content[:limit_chars]
    return [{"role": "system", "content": "ContextOS 长期记忆（摘要，非验收证据）：\n" + content}]


_DECISION_RE = re.compile(r"(?:我决定|决定采用|决定|采用|改为|选择|将使用|不再使用)[^\n。]{0,160}")


def build_decision_journal_cards(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Capture explicit design decisions so later turns do not silently revert them."""

    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        if event.get("kind") != "message":
            continue
        message = event["payload"].get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        text = str(message.get("content") or "")
        for match in _DECISION_RE.finditer(text):
            statement = re.sub(r"\s+", " ", match.group(0).strip())
            if len(statement) < 8 or statement in seen:
                continue
            seen.add(statement)
            cards.append(
                {
                    "kind": "decision_journal",
                    "id": _id("decision", f"{event['seq']}:{statement}"),
                    "title": statement[:160],
                    "summary": statement[:240],
                    "evidence_refs": [],
                }
            )
    return cards[-24:]


def build_prior_run_memory(
    prior_run: Mapping[str, Any],
    prior_events: Sequence[Mapping[str, Any]],
    *,
    limit_chars: int = 2400,
) -> list[dict[str, str]]:
    """Carry the previous run's unresolved failures into the next run.

    A repeated environment or verification trap should not be rediscovered from
    scratch. Only unresolved failures and the previous verdict are carried.
    """

    cards = build_memory_cards(prior_run, prior_events)
    failures = [card for card in cards if card.get("kind") == "failure_cluster" and card.get("unresolved") is not False]
    failures.sort(key=lambda card: -int(card.get("count") or 0))
    if not failures:
        return []
    lines = [f"- {card.get('title')}: {card.get('summary')}" for card in failures[:5]]
    verdict = str(prior_run.get("verification") or "unknown")
    content = (
        f"上一轮工程（{prior_run.get('session_id', '')[:12]}）验证结论 {verdict}，"
        "以下未解决失败已被携带，避免重复探索；可用 read_command_result/read_file 按证据取回原始输出：\n"
        + "\n".join(lines)
    )
    if len(content) > limit_chars:
        content = content[:limit_chars]
    return [{"role": "system", "content": "ContextOS 跨运行记忆（摘要，非验收证据）：\n" + content}]


def build_memory_cards(run: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Build all deterministic memory cards without model inference."""

    return [
        *build_failure_cluster_cards(events),
        *build_interface_contract_cards(events),
        *build_requirement_trace_cards(run, events),
        *build_tool_pattern_cards(events),
        *build_file_diff_cards(events),
        *build_verification_contract_cards(events),
        *build_decision_journal_cards(events),
        *build_evidence_lease_cards(events),
    ]
