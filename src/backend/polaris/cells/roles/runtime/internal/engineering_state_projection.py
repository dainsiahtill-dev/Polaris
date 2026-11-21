"""Request-local working-state projection derived from the engineering journal.

Same StateFirst idea as ContextOS: the durable journal is the truth, and each
request carries a bounded projection of the *current* state instead of an
ever-growing message history. The projection is derived only — never persisted,
never authoritative for acceptance, and never a second truth source. It contains
recorded facts, not instructions, so it cannot enlarge authority or fabricate
progress the way a model self-report can.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_evidence_graph import evidence_graph_projection
from polaris.cells.roles.runtime.internal.engineering_file_cards import card_from_observation
from polaris.kernelone.llm.engine.token_estimator import TokenEstimator

STATE_EVENT_KINDS = (
    "message",
    "run_updated",
    "role_started",
    "role_finished",
    "turn_started",
    "turn_finished",
    "tool_started",
    "tool_observed",
    "tool_reconciled",
    "verification_started",
    "verification_completed",
    "loop_detected",
    "loop_enforced",
    "stage_gate_enforced",
)

_MAX_GOAL_CHARS = 400
_MAX_TEXT_CHARS = 600
_MAX_FILES = 12
_MAX_ERROR_CHARS = 400
_MAX_CHECKS = 4
_WRITE_TOOLS = frozenset({"write_file", "edit_file"})
_LOOP_KINDS = frozenset({"loop_detected", "loop_enforced", "stage_gate_enforced"})


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


_ERROR_LINE = re.compile(r"error TS\d+|ERROR|Error|error|FAILED|FAIL|panicked")


def _first_error_line(value: Any, limit: int) -> str:
    """Pick the first actionable line of a failed check, not its `stdout:` banner."""
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    for line in lines:
        if _ERROR_LINE.search(line):
            return line[:limit]
    return lines[0][:limit] if lines else ""


def _current_verification(run: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """A later start, invalidation or revision change retires the prior evidence."""
    for event in reversed(events):
        if event.get("kind") not in {"verification_started", "verification_completed"}:
            continue
        payload = event["payload"]
        revision = payload.get("workspace_revision")
        if (
            event["kind"] == "verification_completed"
            and type(revision) is int
            and revision == run.get("workspace_revision")
            and payload.get("verification") == run.get("verification")
        ):
            return event
        break
    return None


def shadow_decision_pack(
    run: Mapping[str, Any], events: Sequence[Mapping[str, Any]], *, max_tokens: int
) -> dict[str, Any]:
    """Rebuild the shadow input from the run and selected journal facts at preparation.

    Evidence references are session-local journal sequences. This payload stays
    in the auxiliary prepared-request audit and has no acceptance authority.
    """
    pending: dict[tuple[Any, Any], int] = {}
    uncertain: list[int] = []
    reconciled: list[int] = []
    interventions: list[int] = []
    last_verification = None
    for event in events:
        kind, payload, seq = event["kind"], event["payload"], event["seq"]
        key = (payload.get("turn_seq"), payload.get("call_id"))
        if kind == "tool_started":
            pending[key] = seq
        elif kind == "tool_observed":
            pending.pop(key, None)
            if payload.get("interrupted") or payload.get("timed_out"):
                uncertain.append(seq)
        elif kind == "tool_reconciled":
            pending.pop(key, None)
            reconciled.append(seq)
        elif kind in _LOOP_KINDS:
            interventions.append(seq)
        elif kind == "verification_completed":
            last_verification = seq

    current = _current_verification(run, events)
    pack = {
        "contract": {
            key: run.get(key) for key in ("instruction", "required_checks", "verification_commands", "require_qa")
        },
        "workspace_revision": run.get("workspace_revision"),
        "run_row_verification": run.get("verification"),
        "current_verification_seq": current["seq"] if current else None,
        "last_verification_seq": last_verification,
        "acceptance_fidelity_missing": run.get("acceptance_fidelity_missing"),
        "project_outcome": run.get("project_outcome"),
        "qa_verdict": run.get("qa_verdict"),
        "run_status": run.get("status"),
        "saved_limits": run.get("limits"),
        "pending_tool_started_seqs": list(pending.values()),
        "interrupted_or_timed_out_observation_seqs": uncertain,
        "intervention_seqs": interventions,
        "reconciliation_seqs": reconciled,
    }
    serialized = json.dumps(pack, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    tokens = TokenEstimator.estimate(serialized)
    return {
        "projection_version": 1,
        "session_id": run.get("session_id"),
        "as_of_selected_event_seq": max((event["seq"] for event in events), default=0),
        "workspace_revision": run.get("workspace_revision"),
        "estimated_pack_tokens": tokens,
        "max_tokens": max_tokens,
        **({"status": "assembled", "pack": pack} if tokens <= max_tokens else {"status": "overflow"}),
    }


def working_state_facts(
    run: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    role: str,
    frame_seq: int,
    observed_at: float,
    deadline: float | None,
) -> dict[str, Any]:
    """Fold the journal into the current-state facts a weak model keeps losing."""
    scoped = [event for event in events if event.get("role") == role and int(event.get("seq") or 0) >= frame_seq]
    files: list[str] = []
    writes: dict[tuple[Any, Any], str] = {}
    tool_counts: dict[str, int] = {}
    loop_interventions = 0
    last_intent = ""
    last_error = ""
    for event in scoped:
        kind = event.get("kind")
        payload = event.get("payload") or {}
        if kind == "tool_started":
            tool = str(payload.get("tool") or "")
            arguments = payload.get("arguments")
            if tool in _WRITE_TOOLS and isinstance(arguments, dict):
                name = _text(arguments.get("file"), 200)
                if name:
                    writes[(payload.get("turn_seq"), payload.get("call_id"))] = name
        elif kind == "tool_observed":
            tool = str(payload.get("tool") or "")
            if (
                tool in _WRITE_TOOLS
                and payload.get("ok") is True
                and not (payload.get("interrupted") or payload.get("timed_out"))
            ):
                path = writes.get((payload.get("turn_seq"), payload.get("call_id")))
                if path and path not in files:
                    files.append(path)
            if tool:
                tool_counts[tool] = tool_counts.get(tool, 0) + 1
            if payload.get("ok") is not True and not payload.get("interrupted"):
                snippet = _first_error_line(payload.get("content"), _MAX_ERROR_CHARS)
                if snippet:
                    last_error = snippet
        elif kind == "message":
            message = payload.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    last_intent = _text(content, _MAX_TEXT_CHARS)
        elif kind in _LOOP_KINDS:
            loop_interventions += 1
    file_cards: dict[str, dict[str, Any]] = {}
    for event in scoped:
        if event.get("kind") != "tool_observed":
            continue
        payload = event.get("payload") or {}
        if payload.get("tool") != "read_file" or payload.get("ok") is not True:
            continue
        card = card_from_observation(str(payload.get("content") or ""))
        if card is not None:
            path = str(card["path"])
            file_cards[path] = {
                "path": path,
                "revision": card.get("revision"),
                "sha256": card.get("sha256"),
                "summary": card.get("summary"),
                "symbols": card.get("symbols") or [],
                "content_ref": card.get("content_ref"),
                "stale": card.get("stale", False),
                "fold_state": "ACTIVE" if path in files or path in str(run.get("instruction") or "") else "REFERENCE",
                "pin_reason": "current_edit"
                if path in files
                else "acceptance_clause"
                if path in str(run.get("instruction") or "")
                else None,
            }
    verification = str(run.get("verification") or "unknown")
    last_checks: list[dict[str, Any]] = []
    last_verification_error = ""
    current = _current_verification(run, events)
    if current is None and verification in {"failed", "passed"}:
        verification = "unknown"
    if current is not None:
        payload = current["payload"]
        checks = [check for check in (payload.get("checks") or []) if isinstance(check, dict)]
        commands = payload.get("verification_commands") or []
        for index, check in enumerate(checks[:_MAX_CHECKS]):
            command = commands[index] if index < len(commands) else check.get("command") or check.get("tool")
            last_checks.append(
                {"command": _text(command, 200), "ok": check.get("ok"), "exit_code": check.get("exit_code")}
            )
        if verification != "passed":
            for check in checks:
                if check.get("ok") is not True:
                    last_verification_error = _first_error_line(check.get("content"), _MAX_ERROR_CHARS)
                    if last_verification_error:
                        break
    return {
        "goal": _text(run.get("instruction"), _MAX_GOAL_CHARS),
        "required_checks": run.get("required_checks") or [],
        "evidence_graph": evidence_graph_projection(str(run.get("instruction") or ""), events),
        "candidate": {
            "workspace_revision": run.get("workspace_revision"),
            "verification": verification,
        },
        "last_verification": last_checks,
        "last_verification_error": last_verification_error,
        "files_changed": files[:_MAX_FILES],
        "file_cards": list(file_cards.values())[:_MAX_FILES],
        "tool_activity": dict(sorted(tool_counts.items(), key=lambda item: (-item[1], item[0]))),
        "loop_interventions": loop_interventions,
        "last_intent": last_intent,
        "last_error": last_error,
        "observed_at_epoch_seconds": observed_at,
        "original_deadline_epoch_seconds": deadline,
        "authority": "Recorded facts only; not acceptance, not instructions, not a second truth source.",
    }


def working_state_message(
    run: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    role: str,
    frame_seq: int,
    observed_at: float,
    deadline: float | None,
) -> dict[str, Any]:
    """Render the current-state projection as one trailing user message."""
    facts = working_state_facts(run, events, role=role, frame_seq=frame_seq, observed_at=observed_at, deadline=deadline)
    return {
        "role": "user",
        "content": "Host working-state projection (request preparation, recorded facts only):\n"
        + json.dumps(facts, ensure_ascii=False, allow_nan=False),
    }
