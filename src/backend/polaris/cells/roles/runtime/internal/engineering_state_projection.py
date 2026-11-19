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

STATE_EVENT_KINDS = (
    "message",
    "run_updated",
    "role_started",
    "role_finished",
    "turn_started",
    "turn_finished",
    "tool_started",
    "tool_observed",
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
                if name and name not in files:
                    files.append(name)
        elif kind == "tool_observed":
            tool = str(payload.get("tool") or "")
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
    verification = str(run.get("verification") or "unknown")
    last_checks: list[dict[str, Any]] = []
    last_verification = "unknown"
    last_verification_error = ""
    for event in events:
        if event.get("kind") != "verification_completed":
            continue
        payload = event.get("payload") or {}
        last_verification = str(payload.get("verification") or "unknown")
        checks = [check for check in (payload.get("checks") or []) if isinstance(check, dict)]
        for check in checks[:_MAX_CHECKS]:
            last_checks.append(
                {
                    "command": _text(check.get("command") or check.get("tool"), 200),
                    "ok": check.get("ok"),
                    "exit_code": check.get("exit_code"),
                }
            )
        if last_verification != "passed" and not last_verification_error:
            # Keep the first actionable line of the latest failed check: when the
            # working set drops the verify_project turn, the reason must survive.
            for check in checks:
                if check.get("ok") is not True:
                    snippet = _first_error_line(check.get("content"), _MAX_ERROR_CHARS)
                    if snippet:
                        last_verification_error = snippet
                        break
    if last_checks:
        verification = last_verification
    return {
        "goal": _text(run.get("instruction"), _MAX_GOAL_CHARS),
        "candidate": {
            "workspace_revision": run.get("workspace_revision"),
            "verification": verification,
        },
        "last_verification": last_checks,
        "last_verification_error": last_verification_error,
        "files_changed": files[:_MAX_FILES],
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
