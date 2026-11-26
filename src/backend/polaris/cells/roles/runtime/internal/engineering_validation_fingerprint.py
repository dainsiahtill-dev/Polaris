"""Separate validation reuse keys from failure signatures.

``validation_key`` decides whether a previous execution can be reused for the
same complete input. ``failure_signature`` only groups similar symptoms and must
never be used as a cache key.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from polaris.cells.roles.runtime.public.contracts.engineering_run import ToolObservation, tool_observation_payload

VERIFICATION_RULE_VERSION = "engineering_v2"

_FAILURE_MARK = re.compile(
    r"(?:\bfail(?:ed|ure)?\b|\berror\b|referenceerror|assertionerror|typeerror|syntaxerror|runtimeerror"
    r"|\bexception\b|\btraceback\b|\bassert(?:ion)?\b|\[FAIL\]|^not ok\b|✗)",
    re.IGNORECASE,
)


def validation_key(
    *,
    commands: Sequence[str],
    workspace_revision: int,
    input_binding: Mapping[str, Any],
) -> str:
    """Fingerprint one complete verification input, independent of symptoms."""

    payload = {
        "rule_version": VERIFICATION_RULE_VERSION,
        "workspace_revision": int(workspace_revision),
        "commands": [str(command) for command in commands],
        "binding": _canonical(input_binding),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def observation_failure_signatures(
    check: Mapping[str, Any],
    *,
    legacy_failed: bool = False,
    command_fallback: bool = False,
    command_card: Mapping[str, Any] | None = None,
) -> list[str]:
    """Describe a failed observation once, using the command outcome before its text."""

    if check.get("interrupted") is True:
        return []
    content = check.get("content")
    text = content if isinstance(content, str) else ""
    card = _check_card(check) if command_card is None else command_card
    exit_code = check.get("exit_code", card.get("exit_code"))
    ok = check.get("ok", card.get("ok"))
    if check.get("timed_out") is not True:
        if type(exit_code) is int:
            if exit_code == 0 and ok is not False:
                return []
        elif "exit_code" in check or "exit_code" in card:
            return []  # No observed process exit.
        elif ok is not False and ("ok" in check or "ok" in card or not legacy_failed):
            return []

    if card:
        text = "\n".join(str(card.get(key) or "") for key in ("stdout_preview", "stderr_preview", "summary"))
    signatures: dict[str, None] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[PASS]") or re.match(r"^(?:ok\b|#\s*fail\s+0$)", stripped, re.IGNORECASE):
            continue
        if _FAILURE_MARK.search(stripped):
            signature = re.sub(r"\s+", " ", stripped).split("——", 1)[0].split(" | ", 1)[0].strip()[:240]
            if signature:
                signatures[signature] = None
    return list(signatures) or (["command failed"] if command_fallback else [])


def observed_tool_failure(payload: Mapping[str, Any], *, limit: int = 160) -> str | None:
    """Bound one diagnostic actually returned by a failed tool; command exits and tool exceptions differ."""
    if payload.get("interrupted") or payload.get("timed_out"):
        return None
    code = payload.get("exit_code")
    command = payload.get("tool") == "execute_command"
    if payload.get("ok") is not False and not (command and type(code) is int and code != 0):
        return None
    content = payload.get("content")
    if not command and isinstance(content, str):
        # Native errors report the class and argument diagnosis on the first line.
        # Later lines may contain the complete file or a traceback.
        line = content[:limit].partition("\n")[0].strip()
        if line.partition(":")[0].endswith(("Error", "Exception")):
            return line
    observation = payload if command else {k: v for k, v in payload.items() if k != "exit_code"}
    signatures = observation_failure_signatures(observation)
    return signatures[0][:limit] if signatures else None


def verification_failure_evidence(
    verification: object, checks: Sequence[object], *, command_fallback: bool = False
) -> dict[str, list[str]]:
    """Group signatures and their check references for one completed verification."""

    evidence: dict[str, list[str]] = {}
    if verification != "failed":
        return evidence
    for item in checks:
        check = tool_observation_payload(item) if isinstance(item, ToolObservation) else item
        if not isinstance(check, Mapping):
            continue
        # A legacy check without individual outcome fields can inherit an
        # aggregate failure only if it was the sole checked command.
        signatures = observation_failure_signatures(
            check, legacy_failed=len(checks) == 1, command_fallback=command_fallback
        )
        for signature in signatures:
            refs = evidence.setdefault(signature, [])
            call_id = check.get("call_id")
            if isinstance(call_id, str) and call_id and call_id not in refs:
                refs.append(call_id)
    return evidence


def _bound_check_commands(payload: Mapping[str, Any], count: int) -> list[str]:
    """Positional binding is usable only for a complete, consistent batch."""
    binding = payload.get("input_binding")
    sources = [payload.get("verification_commands")]
    if isinstance(binding, Mapping):
        sources.append(binding.get("commands"))
    present = [value for value in sources if value is not None]
    if not present:
        return []
    commands = present[0]
    if (
        not isinstance(commands, list)
        or len(commands) != count
        or not all(isinstance(command, str) and command.strip() for command in commands)
        or any(value != commands for value in present)
    ):
        return []
    return commands


def _check_card(check: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        content = json.loads(check.get("content") or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    card = content.get("command_card") if isinstance(content, dict) else None
    return card if isinstance(card, dict) else {}


def active_failure_revisions(
    events: Sequence[Mapping[str, Any]], *, verified_call_ids: set[str] | None = None
) -> dict[str, list[int | None]]:
    """Keep each symptom until a later observed pass of its own check.

    The memory view also includes ordinary command runs. Their execution
    environment differs from the read-only verifier's, so track them separately.
    An unidentified legacy failure remains visible without an attributable retest.
    """
    by_check: dict[tuple[str, str | None], dict[str, dict[int, int | None]]] = {}
    for index, event in enumerate(events):
        payload = event["payload"]
        kind = event.get("kind")
        if kind == "verification_completed" and payload.get("verification") in {"passed", "failed"}:
            checks = payload.get("checks") or []
            commands = _bound_check_commands(payload, len(checks))
            failed = payload["verification"] == "failed"
        elif (
            verified_call_ids is not None
            and kind == "tool_observed"
            and payload.get("tool") == "execute_command"
            and payload.get("call_id") not in verified_call_ids
        ):
            checks, commands, failed = [payload], [], True
        else:
            continue
        passed: set[str] = set()
        other: set[str] = set()
        revision = payload.get("workspace_revision")
        for position, check in enumerate(checks):
            if not isinstance(check, Mapping):
                continue
            card = _check_card(check)
            if kind == "tool_observed" and not card:
                continue
            command = card.get("command")
            if not isinstance(command, str) or not command.strip():
                command = None
            if commands:
                bound = commands[position]
                command = bound if command is None or command == bound else None
            exit_code = check.get("exit_code", card.get("exit_code"))
            ok = check.get("ok", card.get("ok"))
            if (
                type(exit_code) is int
                and exit_code == 0
                and ok is True
                and not check.get("timed_out")
                and not check.get("interrupted")
            ):
                if command is not None:
                    passed.add(command)
                continue
            if command is not None:
                other.add(command)
            if not failed:
                continue
            signatures = observation_failure_signatures(
                check,
                legacy_failed=kind == "verification_completed" and len(checks) == 1,
                command_fallback=verified_call_ids is not None,
                command_card=card,
            )
            if kind == "tool_observed":
                signatures = signatures[:1]
            for signature in signatures:
                attempts = by_check.setdefault((kind, command), {}).setdefault(signature, {})
                attempts[index] = revision if type(revision) is int else None
        # Conflicting receipts in one batch cannot establish that the command passed.
        for command in passed - other:
            by_check.pop((kind, command), None)
    active: dict[str, dict[int, int | None]] = {}
    for symptoms in by_check.values():
        for signature, attempts in symptoms.items():
            active.setdefault(signature, {}).update(attempts)
    return {signature: [attempts[i] for i in sorted(attempts)] for signature, attempts in active.items()}


def failure_signatures(checks: Sequence[ToolObservation]) -> list[str]:
    """Extract symptom signatures for clustering, never for reuse."""

    return list(verification_failure_evidence("failed", checks))[:8]


def build_validation_execution(
    *,
    validation_key: str,
    commands: Sequence[str],
    workspace_revision: int,
    verification: str,
    checks: Sequence[ToolObservation],
) -> dict[str, Any]:
    """Durable, auditable record of one real verification execution."""

    return {
        "validation_key": validation_key,
        "commands": [str(command) for command in commands],
        "workspace_revision": int(workspace_revision),
        "verification": verification,
        "executed_now": True,
        "origin": "executed",
        "failure_signatures": list(verification_failure_evidence(verification, checks))[:8],
    }
