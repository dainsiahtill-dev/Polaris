"""Asynchronous EpochCard compression for closed long-term engineering history."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_acceptance import extract_goal_clauses
from polaris.cells.roles.runtime.internal.engineering_semantic_advice import CandidateRefs
from polaris.cells.roles.runtime.internal.engineering_validation_fingerprint import (
    observation_failure_signatures,
    observed_tool_failure,
    verification_failure_evidence,
)

# How far behind the current activity a closed epoch must sit before it is worth
# summarising. The first epoch closes at activity 10, so a 50-activity lag meant
# the first EpochCard could not appear before turn 60 — measured on every live
# run, all 17 runs with a card produced their first one at turn 61. At 20 the
# first card lands around turn 30, where there is still run left to use it.
EPOCH_MIN_AGE_ACTIVITIES = 20
EPOCH_CARD_SCHEMA = "engineering.epoch_card.v2"


def _activity_events(events: Sequence[Mapping[str, Any]]) -> list[tuple[int, list[Mapping[str, Any]]]]:
    groups: list[tuple[int, list[Mapping[str, Any]]]] = []
    activity = 0
    current: list[Mapping[str, Any]] = []
    for event in events:
        if event["kind"] == "turn_started":
            if current:
                groups.append((activity, current))
            activity += 1
            current = []
        current.append(event)
    if current:
        groups.append((activity, current))
    return groups


def _reported_epoch_ids(events: Sequence[Mapping[str, Any]]) -> set[str]:
    """Epoch ids already emitted as ``epoch_card_completed`` in this journal.

    The selection must advance past reported epochs. Without this, the oldest
    eligible epoch is selected forever: the loop suppresses the duplicate emit
    by fingerprint, but ``facts_for_epoch`` keeps returning the same epoch and
    no later epoch is ever scheduled (observed: one card in 88 activities).
    """

    reported: set[str] = set()
    for event in events:
        if event.get("kind") != "epoch_card_completed":
            continue
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        for key in ("id", "node_id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                reported.add(value)
    return reported


def _epoch_id(start: int, activity: int) -> str:
    return f"epoch-{start:04d}-{activity:04d}"


def _evidence_index(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    index = {row["evidence_ref"]: row for row in rows}
    call_counts = Counter(row.get("call_id") for row in rows)
    for row in rows:
        call_id = row.get("call_id")
        if isinstance(call_id, str) and call_counts[call_id] == 1:
            index.setdefault(call_id, row)
    return index


def _epoch_facts(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pair journal outcomes with their starts. A start alone leaves the effect unknown."""

    operations: list[dict[str, Any]] = []
    pending: dict[tuple[int, str, str, str], tuple[dict[str, Any], bool | None]] = {}
    verification: list[dict[str, Any]] = []
    observed_refs: list[str] = []
    for event in events:
        payload = event["payload"]
        kind = event["kind"]
        reference = f"event:{event['seq']}"
        if kind == "verification_completed":
            verification.append({"verdict": payload.get("verification") or "unknown", "evidence_ref": reference})
            observed_refs.append(reference)
        if kind not in {"tool_started", "tool_observed"}:
            continue
        tool = payload.get("tool")
        call_id = payload.get("call_id")
        turn = payload.get("turn_seq")
        if not isinstance(tool, str) or not isinstance(call_id, str) or not isinstance(turn, int):
            continue
        key = (turn, call_id, tool, str(event.get("role") or ""))
        if kind == "tool_started":
            op: dict[str, Any] = {"tool": tool, "call_id": call_id, "outcome": "unknown", "evidence_ref": reference}
            arguments = payload.get("arguments")
            args = arguments if isinstance(arguments, Mapping) else {}
            if tool in {"write_file", "edit_file", "read_file"} and isinstance(args.get("file"), str):
                op["file"] = args.get("file")
            if tool == "execute_command":
                command = args.get("command")
                if isinstance(command, str):
                    op["command"] = command[:240] + ("…" if len(command) > 240 else "")
            operations.append(op)
            pending[key] = (op, "content" in args if isinstance(arguments, Mapping) else None)
        else:
            observed_refs.append(reference)
            dispatch = pending.pop(key, None)
            observation: dict[str, Any]
            if dispatch is None:
                # An orphan result proves a tool outcome, but cannot identify a path or command.
                observation = {"tool": tool, "call_id": call_id}
                operations.append(observation)
            else:
                observation = dispatch[0]
            ok = payload.get("ok")
            if payload.get("interrupted") or payload.get("timed_out"):
                observation["outcome"] = "unknown"
            elif tool == "execute_command" and type(payload.get("exit_code")) is int and payload["exit_code"] != 0:
                observation["outcome"] = "failed"
            else:
                observation["outcome"] = "succeeded" if ok is True else "failed" if ok is False else "unknown"
            if dispatch is not None and observation["outcome"] == "failed" and event.get("role") in {None, "director"}:
                failure = observed_tool_failure(payload)
                if failure:
                    observation["failure"] = failure
                if tool == "write_file" and dispatch[1] is not None:
                    observation["content_argument_present"] = dispatch[1]
            observation["evidence_ref"] = reference
            if tool == "execute_command":
                observation["exit_code"] = payload.get("exit_code")
    files = [op for op in operations if op["tool"] in {"write_file", "edit_file"}]
    commands = [op for op in operations if op["tool"] == "execute_command"]
    return {
        "file_changes": sorted({op["file"] for op in files if op.get("file") and op["outcome"] == "succeeded"}),
        "file_operations": files,
        "commands": commands,
        "other_tools": [op for op in operations if op["tool"] not in {"write_file", "edit_file", "execute_command"}],
        "verification_history": [check["verdict"] for check in verification],
        "verification": verification,
        "evidence_refs": list(_evidence_index([*operations, *verification])),
        "observed_refs": observed_refs,
    }


def validate_epoch_exploration(value: object, allowed: Sequence[str]) -> dict[str, Any]:
    """Validate one optional proposal against the exact observed rows supplied to the model."""
    if not isinstance(value, Mapping) or set(value) != {"open_question", "discriminating_check", "evidence_refs"}:
        raise ValueError("exploration requires open_question, discriminating_check and evidence_refs")
    texts: dict[str, Any] = {}
    for key, limit in (("open_question", 180), ("discriminating_check", 240)):
        text = value[key]
        if not isinstance(text, str) or not text.strip() or len(text) > limit or not text.isprintable():
            raise ValueError(f"exploration {key} requires bounded single-line text")
        texts[key] = text.strip()
    refs = value["evidence_refs"]
    if (
        not isinstance(refs, list)
        or not 1 <= len(refs) <= 3
        or not all(isinstance(ref, str) and ref in allowed for ref in refs)
    ):
        raise ValueError("exploration references must identify one to three supplied observed journal events")
    if len(set(refs)) != len(refs):
        raise ValueError("exploration requires distinct evidence references")
    return {**texts, "evidence_refs": list(refs)}


def _recall_line(observation: Mapping[str, Any]) -> str:
    if "verdict" in observation:
        return f"verification {observation['verdict']}"
    subject = observation.get("file") or observation.get("command") or ""
    exit_code = observation.get("exit_code")
    exit_detail = f" (exit {exit_code})" if exit_code is not None else ""
    return f"{observation['tool']} {subject} [{observation['outcome']}]{exit_detail}"


def _journal_summary(facts: Mapping[str, Any], selected_refs: Sequence[str]) -> tuple[str, list[str]]:
    rows = [*facts["file_operations"], *facts["commands"], *facts["other_tools"], *facts["verification"]]
    by_ref = _evidence_index(rows)
    selected = list(dict.fromkeys(by_ref[ref]["evidence_ref"] for ref in selected_refs if ref in by_ref))[:3]
    parts = []
    if selected:
        parts.append("Devourer recall: " + "; ".join(f"{_recall_line(by_ref[ref])} ({ref})" for ref in selected))
    # Keep the request compact. Exact counts and receipt pointers survive even when details are abbreviated.
    parts.append(
        f"Successful file operations on {len(facts['file_changes'])} paths: {', '.join(facts['file_changes'][:8]) or 'none recorded'}"
    )
    for label, observations in (("Commands", facts["commands"]), ("Verification", facts["verification"])):
        parts.append(
            f"{label} ({len(observations)}): "
            + ("; ".join(_recall_line(row) for row in observations[-3:]) or "none recorded")
        )
    incomplete = [row for row in rows if row.get("outcome") in {"failed", "unknown"}]
    if incomplete:
        parts.append(
            f"Failed/unknown tool outcomes ({len(incomplete)}): "
            + "; ".join(_recall_line(row) for row in incomplete[-3:])
        )
    return " | ".join(parts), selected


def _epoch_selection(goal: str, facts: Mapping[str, Any]) -> dict[str, Any]:
    """Offer a few distinct observed receipts. The host owns the complete interval and outcome counts."""
    observed = set(facts["observed_refs"])
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in [*facts["file_operations"], *facts["commands"], *facts["other_tools"], *facts["verification"]]:
        ref = row["evidence_ref"]
        if ref not in observed:
            continue
        item: dict[str, Any] = {
            "tool": row.get("tool", "verification"),
            "outcome": row.get("outcome") or row.get("verdict") or "unknown",
        }
        if row.get("file"):
            item["file"] = row["file"][:160]
        if row.get("command"):
            item["command"] = row["command"].split("\n", 1)[0][:120]
        for field in ("exit_code", "failure", "content_argument_present"):
            if field in row:
                item[field] = row[field]
        key = tuple(item.items())
        previous = groups.get(key)
        groups[key] = {**item, "ref": ref, "count": 1 if previous is None else previous["count"] + 1}
    rows = sorted(
        groups.values(),
        key=lambda r: (r["outcome"] not in {"failed", "unknown"}, r["tool"] != "verification", -int(r["ref"][6:])),
    )
    selected = rows[:12]
    for item in selected:
        if item["count"] == 1:
            del item["count"]
    clauses = extract_goal_clauses(goal)
    cue = clauses[0] if clauses else goal
    return {"goal_cue": " ".join(cue[:400].split())[:240], "observations": selected}


def facts_for_epoch(
    run: Mapping[str, Any], events: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], CandidateRefs, str]:
    """Select the oldest closed epoch far enough behind current to be stable."""

    groups = _activity_events(events)
    current = groups[-1][0] if groups else 0
    reported = _reported_epoch_ids(events)
    eligible = [
        activity
        for activity, _ in groups
        if activity >= 10
        and activity % 10 == 0
        and activity <= current - EPOCH_MIN_AGE_ACTIVITIES
        and _epoch_id(activity - 9, activity) not in reported
    ]
    if not eligible:
        empty = CandidateRefs(node_ids=set(), slice_ids=set(), evidence_refs=set())
        return {"candidates": []}, empty, ""
    activity = eligible[0]
    start = activity - 9
    epoch_id = _epoch_id(start, activity)
    closed = [event for number, group in groups if start <= number <= activity for event in group]
    facts = _epoch_facts(closed)
    evidence_refs = set(facts["evidence_refs"])
    refs = CandidateRefs(node_ids={epoch_id}, slice_ids={"slice_epoch"}, evidence_refs=evidence_refs)
    request = {
        "candidates": [
            {
                "node_id": epoch_id,
                "snapshot_id": epoch_id,
                "slice_ids": ["slice_epoch"],
                "host_facts": facts,
                "selection": _epoch_selection(str(run.get("instruction") or ""), facts),
                "source_seq_range": [closed[0]["seq"], closed[-1]["seq"]],
                "content": json.dumps(
                    {
                        "goal": run.get("instruction") or "",
                        "activity_range": [start, activity],
                        **facts,
                    },
                    ensure_ascii=False,
                ),
                "coverage": {"observed_line_ranges": [[1, len(closed)]]},
            }
        ]
    }
    fingerprint = hashlib.sha256(
        json.dumps({"epoch_id": epoch_id, "facts": facts}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return request, refs, fingerprint


def legacy_epoch_memories(
    events: Sequence[Mapping[str, Any]], requested: Mapping[str, int]
) -> dict[str, dict[str, Any]]:
    """Rebuild old card facts from the contiguous journal preceding each original card."""
    if not requested:
        return {}
    contiguous = 0
    for expected, event in enumerate(events, 1):
        if event.get("seq") != expected:
            break
        contiguous = expected
    groups = _activity_events(events[:contiguous])
    if not groups or groups[0][0] != 0 or not any(e["kind"] == "run_started" for e in groups[0][1]):
        return {}
    memories = {}
    for end in range(10, len(groups) - 1, 10):
        start = end - 9
        identity = _epoch_id(start, end)
        if identity not in requested or groups[end + 1][1][0]["seq"] > requested[identity]:
            continue
        closed = [event for _, group in groups[start : end + 1] for event in group]
        facts = _epoch_facts(closed)
        summary, _ = _journal_summary(facts, [])
        memories[identity] = {
            "id": identity,
            "kind": "epoch_card",
            "summary": summary,
            "source_seq_range": [closed[0]["seq"], closed[-1]["seq"]],
            "evidence_refs": facts["evidence_refs"][:6],
        }
    return memories


@dataclass(frozen=True)
class _RecallObservation:
    seq: int
    revision: int | None
    subjects: frozenset[tuple[str, str]]
    failures: frozenset[str]
    verification: bool


def _recall_observations(
    events: Sequence[Mapping[str, Any]],
) -> tuple[dict[int, _RecallObservation], set[tuple[str, str]], str]:
    """Index journal identities for recall; the resulting map exists for one compilation only."""
    rows: dict[int, _RecallObservation] = {}
    pending: dict[tuple[int, str, str], tuple[str, str]] = {}
    focus: set[tuple[str, str]] = set()
    revision: int | None = None
    last_turn = None
    phase = ""
    for event in events:
        p = event["payload"]
        kind = event["kind"]
        seq = event.get("seq")
        if type(seq) is not int:
            continue
        if kind == "run_updated" and type(p.get("workspace_revision")) is int:
            revision = p["workspace_revision"]
        if event.get("role") not in {None, "director"}:
            continue
        if kind == "request_prepared":
            request = p.get("request")
            context = request.get("context") if isinstance(request, Mapping) else None
            phase = str(context.get("phase") or "") if isinstance(context, Mapping) else ""
        if kind == "verification_completed":
            binding = p.get("input_binding")
            commands = p.get("verification_commands")
            if commands is None and isinstance(binding, Mapping):
                commands = binding.get("commands")
            commands = commands if isinstance(commands, list) else []
            subjects = frozenset(("check", cmd) for cmd in commands if isinstance(cmd, str) and cmd)
            failures = verification_failure_evidence(p.get("verification"), p.get("checks") or [])
            rows[seq] = _RecallObservation(seq, p.get("workspace_revision"), subjects, frozenset(failures), True)
            continue
        if kind not in {"tool_started", "tool_observed"}:
            continue
        turn, tool, call = p.get("turn_seq"), p.get("tool"), p.get("call_id")
        if type(turn) is not int or not isinstance(tool, str) or not isinstance(call, str):
            continue
        key = (turn, tool, call)
        if kind == "tool_started":
            args = p.get("arguments")
            if not isinstance(args, Mapping):
                continue
            if tool in {"read_file", "write_file", "edit_file"}:
                subject_kind, value = "file", args.get("file")
            elif tool == "execute_command":
                subject_kind, value = "command", args.get("command")
            elif tool in {"repo_rg", "browser_observe"}:
                # Search filters and browser viewports are part of the probe identity.
                # Never place the arguments or raw captures into the retrieval index.
                encoded = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                subject_kind, value = tool, hashlib.sha256(encoded).hexdigest()
            else:
                continue
            if isinstance(value, str) and value:
                subject = (subject_kind, value)
                pending[key] = subject
                if turn != last_turn:
                    focus.clear()
                    last_turn = turn
                focus.add(subject)
            continue
        observed = pending.pop(key, None)
        if observed is not None:
            symptoms = observation_failure_signatures(p) if p.get("ok") is False else []
            rows[seq] = _RecallObservation(seq, revision, frozenset({observed}), frozenset(symptoms), False)
    return rows, focus, phase


def recall_epoch_sections(
    run: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    cards: Sequence[Mapping[str, Any]],
    active_failures: set[str],
    *,
    budget: int,
) -> list[str]:
    """Select at most two host histories and revalidate an optional question against this Director decision."""
    if not cards or budget <= 0 or run.get("role") not in {None, "director"}:
        return []
    rows, focus, phase = _recall_observations(events)
    ordered_seqs = sorted(rows)
    latest = {subject: row.seq for row in rows.values() for subject in row.subjects}
    goal = str(run.get("instruction") or "")
    for subject in latest:
        # Exact path/command tokens avoid matching e.g. a.py inside data.py or src/a.py.
        value = subject[1]
        if (
            subject[0] in {"file", "command", "check"}
            and len(value) <= 240
            and value in goal
            and re.search(r"(?<![\w./-])" + re.escape(value) + r"(?![\w./-])", goal)
        ):
            focus.add(subject)
    handoff = run.get("verification") == "passed" and not active_failures
    revision = run.get("workspace_revision")
    ranked = []
    for order, card in enumerate(cards):
        bounds = card.get("source_seq_range")
        if isinstance(bounds, list) and len(bounds) == 2 and all(type(n) is int for n in bounds):
            start = bisect_left(ordered_seqs, bounds[0])
            end = bisect_right(ordered_seqs, bounds[1])
            source = [rows[seq] for seq in ordered_seqs[start:end]]
        else:
            # Older v2 cards may only expose event references; missing referents contribute no signals.
            source = []
            for ref in card.get("evidence_refs") or []:
                if re.fullmatch(r"event:[1-9][0-9]{0,17}", ref):
                    row = rows.get(int(ref[6:]))
                    if row is not None:
                        source.append(row)
        matching = [r for r in source if focus & r.subjects or active_failures & r.failures]
        verification = [r for r in source if r.verification]
        stage_match = bool(verification and (phase.lower() in {"verify", "retest"} or handoff))
        probe = card.get("exploration")
        admitted = None
        if isinstance(probe, Mapping) and type(revision) is int:
            cited = set(probe.get("evidence_refs") or [])
            grounded = [r for r in source if f"event:{r.seq}" in cited]
            if (
                grounded
                and len(grounded) == len(cited)
                and all(
                    r.revision == revision and r.subjects and all(latest[s] == r.seq for s in r.subjects)
                    for r in grounded
                )
                and any(r in matching or (r.verification and stage_match) for r in grounded)
            ):
                admitted = probe
        score = (bool(admitted), any(active_failures & r.failures for r in matching), bool(matching), stage_match)
        hints = []
        for r in (matching or (verification if stage_match else []))[-3:]:
            matches = sorted(r.failures & active_failures)
            if not matches:
                matches = [
                    kind if kind in {"repo_rg", "browser_observe"} else value
                    for kind, value in sorted(r.subjects & focus)
                ]
            detail = " " + json.dumps(matches[0][:64], ensure_ascii=False) if matches else ""
            hints.append(f"event:{r.seq}{detail}")
        ranked.append((score, order, card, admitted, hints))

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    chosen = [item for item in ranked if any(item[0])][:2]
    if not chosen and not (focus or active_failures or handoff):
        chosen = ranked[:1]
    result = []
    remaining = min(budget, 1200)
    for _, _, card, proposal, hints in chosen:
        label = f"Epoch 记忆 {card['id']}（历史观察）："
        if hints:
            label += "[" + ", ".join(hints) + "] "
        advice = " | Untrusted Devourer exploration: " + json.dumps(proposal, ensure_ascii=False) if proposal else ""
        room = min(remaining, 900) - len(label)
        if room < 64:
            break
        if len(advice) + 64 > room:
            advice = ""
        section = label + str(card["summary"])[: room - len(advice)] + advice
        result.append(section)
        remaining -= len(section) + 2
    return result


class EpochRuntime:
    """Coalesce one asynchronous EpochCard summary job per epoch."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool
        self._job: asyncio.Task[list[dict[str, Any]]] | None = None
        self._fingerprint = ""
        self._request: dict[str, Any] = {}

    def schedule(self, request: dict[str, Any], candidates: CandidateRefs, *, fingerprint: str) -> None:
        if self._job is not None and not self._job.done():
            # Never cancel an in-flight job. The loop re-selects every turn, so
            # cancelling on a changed fingerprint guarantees a slow remote
            # summary never completes. A superseded request waits instead.
            return
        self._fingerprint = fingerprint
        self._request = request
        candidate = request["candidates"][0]
        summary_request = {
            "node_id": candidate["node_id"],
            "snapshot_id": candidate["snapshot_id"],
            "content": candidate["content"],
            "coverage": candidate["coverage"],
            "allowed_evidence_refs": sorted(candidates.evidence_refs),
            "observed_evidence_refs": candidate["host_facts"]["observed_refs"],
            "max_summary_tokens": 640,
            "recall_instruction": "Select the most useful journal event references in evidence_refs. These prioritize observed results for the next engineering turn.",
            "selection": candidate["selection"],
        }
        from polaris.cells.roles.runtime.internal.engineering_plugin_slot import request_node_summaries

        self._job = asyncio.create_task(request_node_summaries([summary_request], self.pool.summarize_many))

    def poll(self) -> dict[str, Any] | None:
        if self._job is None or not self._job.done():
            return None
        try:
            result = self._job.result()[0]
        except asyncio.CancelledError:
            return None
        candidate = self._request["candidates"][0]
        card = {
            "schema_version": EPOCH_CARD_SCHEMA,
            "kind": "epoch_card",
            "id": candidate["node_id"],
            "title": candidate["node_id"],
            "fingerprint": self._fingerprint,
            "validation_result": result.get("validation_result"),
        }
        facts = candidate["host_facts"]
        if "provider_attempts" in result:
            card["provider_attempts"] = result["provider_attempts"]
        advice = result.get("result") if result.get("validation_result") == "VALID" else None
        summary, selected = _journal_summary(facts, advice["evidence_refs"] if advice else [])
        card.update(
            summary=summary,
            summary_kind="journal_facts",
            source_seq_range=candidate["source_seq_range"],
            coverage=candidate["coverage"],
            recall_refs=selected,
        )
        if advice:
            card["model_advice"] = advice
            card["advice_status"] = (
                "abstained" if advice.get("abstained") else "evidence_selected" if selected else "uncited"
            )
            card["uncertain"] = advice.get("uncertain", True)
            if advice.get("exploration_error"):
                card["exploration_status"] = "rejected"
                card["exploration_error"] = advice["exploration_error"]
            if "exploration" in advice:
                try:
                    card["exploration"] = validate_epoch_exploration(advice["exploration"], facts["observed_refs"])
                except ValueError as error:
                    card["exploration_status"] = "rejected"
                    card["exploration_error"] = str(error)
                else:
                    card["exploration_status"] = "proposed"
                    card["advice_status"] = "probe_proposed"
        elif result.get("skip_reason"):
            card["advice_status"] = result["skip_reason"]
        else:
            card["advice_status"] = "rejected"
            card["advice_error"] = result.get("error")
        # Store a few usable pointers with the summary. The exact epoch can be
        # rebuilt from source_seq_range; its operation list stays in the journal.
        probe_refs = (card.get("exploration") or {}).get("evidence_refs", [])
        card["evidence_refs"] = list(dict.fromkeys([*probe_refs, *selected, *facts["observed_refs"]]))[:12]
        return card
