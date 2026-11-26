"""Deterministic long-term memory cards derived from engineering journal facts."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_acceptance import extract_goal_clauses
from polaris.cells.roles.runtime.internal.engineering_epoch_runtime import (
    EPOCH_CARD_SCHEMA,
    legacy_epoch_memories,
    recall_epoch_sections,
    validate_epoch_exploration,
)
from polaris.cells.roles.runtime.internal.engineering_file_cards import (
    card_from_observation,
    observed_file_card,
    recoverable_ref,
)
from polaris.cells.roles.runtime.internal.engineering_state_projection import current_verification_event
from polaris.cells.roles.runtime.internal.engineering_validation_fingerprint import (
    active_failure_revisions,
    observation_failure_signatures,
    verification_failure_evidence,
)


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


def _epoch_ref_before(ref: object, seq: int | None) -> bool:
    return (
        isinstance(ref, str)
        and re.fullmatch(r"event:[1-9][0-9]{0,17}", ref) is not None
        and (seq is None or int(ref[6:]) < seq)
    )


def build_epoch_cards(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Render host facts and optional advisory data; re-derive unbound historical cards."""

    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    completed = [event for event in events if event["kind"] == "epoch_card_completed"]
    historical = legacy_epoch_memories(
        events,
        {
            str(event["payload"].get("id") or event["payload"].get("node_id")): event["seq"]
            for event in completed
            if event["payload"].get("schema_version") != EPOCH_CARD_SCHEMA and isinstance(event.get("seq"), int)
        },
    )
    for event in completed:
        payload = event["payload"]
        if payload.get("error"):
            continue
        summary = str(payload.get("summary") or "").strip()
        identity = str(payload.get("id") or payload.get("node_id") or payload.get("fingerprint") or "")
        if not summary or not identity or identity in seen:
            continue
        seen.add(identity)
        if payload.get("schema_version") != EPOCH_CARD_SCHEMA:
            if identity in historical:
                cards.append(historical[identity])
            continue
        completion_seq = event.get("seq")

        bounds = payload.get("source_seq_range")
        if not (
            isinstance(bounds, list)
            and len(bounds) == 2
            and all(type(n) is int for n in bounds)
            and 0 < bounds[0] <= bounds[1]
            and (type(completion_seq) is not int or bounds[1] < completion_seq)
        ):
            bounds = None
        card: dict[str, Any] = {
            "kind": "epoch_card",
            "id": identity,
            "summary": summary,
            "evidence_refs": [
                ref for ref in payload.get("evidence_refs") or [] if _epoch_ref_before(ref, completion_seq)
            ],
            "source_seq_range": bounds,
        }
        if payload.get("exploration_status") == "proposed":
            try:
                card["exploration"] = validate_epoch_exploration(payload.get("exploration"), card["evidence_refs"])
            except ValueError:
                card["exploration"] = None
        cards.append(card)
    return cards


def build_failure_cluster_cards(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    clusters: dict[str, dict[str, Any]] = {}
    # A verifier emits both a tool observation and a completion for the same
    # command. Attribute its symptoms to the completed verification once.
    observed_refs: dict[str, str] = {}
    verified_calls = {
        check["call_id"]
        for event in events
        if event["kind"] == "verification_completed"
        for check in event["payload"].get("checks") or []
        if isinstance(check, dict) and isinstance(check.get("call_id"), str)
    }
    for event in events:
        payload = event["payload"]
        if event["kind"] == "verification_completed":
            failures = verification_failure_evidence(
                payload.get("verification"), payload.get("checks") or [], command_fallback=True
            )
            for refs in failures.values():
                refs.extend([observed_refs[ref] for ref in refs if ref in observed_refs])
        elif event["kind"] == "tool_observed":
            command = _command_card(event)
            if command is None:
                continue
            reference = command.get("content_ref")
            call_id = payload.get("call_id")
            if call_id in verified_calls:
                if isinstance(reference, str):
                    observed_refs[call_id] = reference
                continue
            failures = {
                signature: [reference] if isinstance(reference, str) else []
                for signature in observation_failure_signatures(payload, command_fallback=True)[:1]
            }
        else:
            continue
        for signature, refs in failures.items():
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
            card["evidence_refs"].extend(ref for ref in refs if ref not in card["evidence_refs"])
    active = active_failure_revisions(events, verified_call_ids=verified_calls) if clusters else {}
    for card in clusters.values():
        card["unresolved"] = card["title"] in active
        card["summary"] = f"{card['count']} 次 · {'未解决' if card['unresolved'] else '同一检查复测已通过'}"
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


def attach_run_card_section(messages: list[dict[str, Any]], run_card: Mapping[str, Any]) -> None:
    """Append a stage section to an existing legacy ContextOS message."""
    section = run_card_section(run_card)
    if not section:
        return
    for message in messages:
        if message.get("role") != "system":
            continue
        content = message.get("content")
        if not isinstance(content, str) or "ContextOS" not in content:
            continue
        if section in content:
            return
        message["content"] = content.rstrip() + "\n\n" + section
        return


def cartographer_action_proposals(run_card: Mapping[str, Any]) -> list[dict[str, str]]:
    """Select exactly the advisory actions and predictions shown to the Director."""
    narrative = run_card.get("narrative") or {}
    actions = [
        {"action": str(item["action"])[:160], "expected": str(item.get("expected") or "")[:160]}
        for item in (narrative.get("next_actions") or [])
        if isinstance(item, Mapping) and item.get("action")
    ]
    return actions[:3]


def run_card_section(run_card: Mapping[str, Any]) -> str:
    """Render host stage and separately labeled model advice for this role frame."""
    narrative = run_card.get("narrative") or {}
    phase = str(run_card.get("phase") or "")
    intent = str(narrative.get("intent") or run_card.get("intent") or "")
    reason = str(narrative.get("reason") or "")
    actions = [
        str(item.get("action"))
        for item in (run_card.get("next_actions") or [])
        if isinstance(item, dict) and item.get("action")
    ]
    lines = [line for line in (f"当前阶段：{phase}" if phase else "", f"判断：{reason}" if reason else "") if line]
    if actions:
        lines.append("宿主建议下一步：" + "；".join(actions[:3]))
    for proposal in cartographer_action_proposals(run_card):
        expected = proposal["expected"]
        prediction = f"；预期观察：{expected}" if expected else ""
        lines.append(f"制图师建议（待检验）：{proposal['action']}{prediction}")
    if narrative:
        refs = run_card.get("narrative_evidence_refs") or []
        if refs:
            lines.append("制图师引用：" + "、".join(refs[:8]))
        if narrative.get("uncertain", True):
            lines.append("制图师标记：不确定")
    if intent:
        lines.append(f"意图：{intent[:160]}")
    if not lines:
        return ""
    return "ContextOS RunCard（宿主阶段 + 制图师叙述，非验收证据）：\n" + "\n".join(lines)


def build_context_memory_messages(
    run: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    limit_chars: int = 4000,
    run_card: Mapping[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Build bounded evidence sections for the Director's request-local context."""
    sections: list[str] = []
    if run_card:
        section = run_card_section(run_card)
        if section:
            sections.append(section)
    failures = [card for card in build_failure_cluster_cards(events) if card.get("unresolved") is not False]
    failures.sort(key=lambda card: (-int(card.get("count") or 0), str(card.get("title") or "")))
    if failures:
        lines = []
        for card in failures[:5]:
            refs = [ref for ref in (card.get("evidence_refs") or []) if isinstance(ref, str)]
            hint = f" · 证据: {', '.join(refs[-2:])}" if refs else ""
            lines.append(f"- {card.get('title')}: {card.get('summary')}{hint}")
        sections.append("未解决失败聚类：\n" + "\n".join(lines))
    contracts = build_verification_contract_cards(events)
    if contracts:
        latest = contracts[-1]
        refs = [ref for ref in (latest.get("evidence_refs") or []) if isinstance(ref, str)]
        sections.append(
            f"最近验证契约：{latest.get('summary')} · {latest.get('title')}"
            + (f" · 证据: {', '.join(refs[-3:])}" if refs else "")
        )
    decisions = build_decision_journal_cards(events)
    if decisions:
        sections.append("先前选择（模型自述）：\n" + "\n".join(f"- {card.get('title')}" for card in decisions[-5:]))
    header = "ContextOS 长期记忆（摘要，非验收证据）：\n"
    sections.extend(
        recall_epoch_sections(
            run,
            events,
            build_epoch_cards(events),
            {str(card["title"]) for card in failures},
            budget=limit_chars - len(header) - sum(len(text) + 2 for text in sections),
        )
    )
    if not sections:
        return []
    content = (header + "\n\n".join(sections))[: max(0, limit_chars)]
    return [{"role": "system", "content": content}] if content else []


_DECISION_RE = re.compile(r"(?:(?:我|我们)(?:决定|选择|将使用|改用|采用)|决定采用)[^\n。]{0,160}")


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

    failures = [card for card in build_failure_cluster_cards(prior_events) if card.get("unresolved") is not False]
    failures.sort(key=lambda card: -int(card.get("count") or 0))
    if not failures:
        return []
    lines = [f"- {card.get('title')}: {card.get('summary')}" for card in failures[:5]]
    verdict = str(prior_run.get("verification") or "unknown")
    content = (
        f"上一轮工程验证结论 {verdict}，"
        "以下未解决失败已被携带，避免重复探索；可用 read_command_result/read_file 按证据取回原始输出：\n"
        + "\n".join(lines)
    )
    if len(content) > limit_chars:
        content = content[:limit_chars]
    return [{"role": "system", "content": "ContextOS 跨运行记忆（摘要，非验收证据）：\n" + content}]


FILE_FACTS_HEADER = (
    "ContextOS known_facts · 内容未变时直接引用已有 filecard:<sha256>，不要重读整文件。需要细节才用 read_file"
    "（file、content_ref 和 start_line/end_line）。historical 只代表以前的版本；"
    "需要当前源码时先确认版本。candidate_check 是整体验证回执，独立 QA 须取得自己的观察。\n"
)


_TYPE_KEYWORDS = frozenset(
    {
        "void", "bool", "char", "int", "long", "short", "float", "double", "signed",
        "unsigned", "const", "static", "constexpr", "struct", "class", "enum",
        "namespace", "std", "size_t", "string", "auto", "let", "var", "final",
        "public", "private", "protected", "virtual", "override",
    }
)


def build_known_facts_cards(
    run: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    can_read: Callable[[str], bool] | None = None,
) -> list[dict[str, Any]]:
    """Index the most recently observed version of each path at this journal cut."""
    verified = current_verification_event(run, events)
    binding = verified["payload"].get("input_binding") if verified else None
    manifest = (
        binding.get("source_manifest")
        if (
            isinstance(binding, dict)
            and binding.get("workspace_revision") == run.get("workspace_revision")
            and verified
            and verified["payload"].get("checks")
        )
        else None
    )
    workspace = run.get("workspace")
    seen: set[str] = set()
    subsequent_reads: dict[tuple[str, str], bool] = {}
    entries: list[dict[str, Any]] = []
    remaining = 4800
    for event in reversed(events):
        card = observed_file_card(event)
        if card is None:
            continue
        path = card.get("path")
        if not isinstance(path, str) or not path or path in seen:
            continue
        if card.get("recovered_from"):
            subsequent_reads.setdefault(
                (path, str(card.get("content_ref"))),
                card.get("current_sha256") == card.get("sha256") and card.get("stale") is False,
            )
            continue
        seen.add(path)
        ref = recoverable_ref(card)
        parts = Path(path).parts
        if (
            not ref
            or not parts
            or Path(path).is_absolute()
            or ".." in parts
            or parts[0] == ".polaris"
            or (can_read is not None and not can_read(path))
        ):
            continue
        source = (
            manifest.get(str(Path(workspace) / path))
            if isinstance(manifest, dict) and isinstance(workspace, str)
            else None
        )
        contradicted = subsequent_reads.get((path, ref)) is False or card.get("stale") is True
        checked = not contradicted and isinstance(source, dict) and source.get("sha256") == card["sha256"]
        current = checked or (type(card.get("revision")) is int and card["revision"] == run.get("workspace_revision"))
        symbols = card.get("symbols")
        entry: dict[str, Any] = {
            "path": path,
            "content_ref": ref,
            "lines": card.get("line_count"),
            "symbols": [name[:64] for name in symbols[:6] if isinstance(name, str)]
            if isinstance(symbols, list)
            else [],
            "version": "observed" if current and not contradicted else "historical",
            "candidate_check": f"{verified['payload']['verification']}@{verified['seq']}"
            if checked and verified
            else "unknown",
        }
        # 一行"可回忆"的摘要：索引给了地址却没给内容，模型只能重读整文件。
        # 优先用蒸馏器已经产出的摘要（同一份文本，已过 summary_is_informative 闸）；
        # 没有则用真实符号名 + 行数 —— 实测修 _declarations 后 symbols 才是真名。
        summary = card.get("source_summary") or ""
        if not isinstance(summary, str) or not summary.strip():
            real = [name for name in entry["symbols"] if name not in _TYPE_KEYWORDS][:3]
            summary = (
                f"{Path(path).name}：{'、'.join(real) if real else '无符号'}"
                f"（{card.get('line_count')} 行）"
            )
        entry["summary"] = summary.strip()[:200]
        locations = card.get("declaration_lines")
        if isinstance(locations, dict):
            entry["at_lines"] = [locations.get(name) for name in entry["symbols"]]
        size = len(json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
        if size > remaining:
            continue
        entries.append(entry)
        remaining -= size
        if len(entries) >= 16:
            break
    if not entries:
        return []
    return [
        {
            "kind": "known_facts",
            "id": "known-facts",
            "title": "已读文件事实索引",
            "summary": "、".join(entry["path"] for entry in entries),
            "paths": [entry["path"] for entry in entries],
            "evidence_refs": [entry["content_ref"] for entry in entries],
            "entries": entries,
        }
    ]


def build_memory_cards(run: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Build all deterministic memory cards without model inference."""

    return [
        *build_known_facts_cards(run, events),
        *build_epoch_cards(events),
        *build_failure_cluster_cards(events),
        *build_interface_contract_cards(events),
        *build_requirement_trace_cards(run, events),
        *build_tool_pattern_cards(events),
        *build_file_diff_cards(events),
        *build_verification_contract_cards(events),
        *build_decision_journal_cards(events),
        *build_evidence_lease_cards(events),
    ]
