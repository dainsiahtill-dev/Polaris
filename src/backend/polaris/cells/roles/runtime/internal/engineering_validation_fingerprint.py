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

from polaris.cells.roles.runtime.public.contracts.engineering_run import ToolObservation

VERIFICATION_RULE_VERSION = "engineering_v2"

_FAILURE_MARK = re.compile(
    r"(?:\bfail(?:ed|ure)?\b|\berror\b|referenceerror|assertionerror|typeerror|syntaxerror|runtimeerror"
    r"|\bexception\b|\btraceback\b|\[FAIL\]|✗)",
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


def _check_text(content: object) -> str:
    """Unwrap a CommandCard JSON body before scanning for symptoms."""

    text = str(content or "")
    if not text.strip():
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(payload, dict):
        return text
    card = payload.get("command_card")
    if not isinstance(card, dict):
        return text
    return "\n".join(str(card.get(key) or "") for key in ("stdout_preview", "stderr_preview", "summary"))


def failure_signatures(checks: Sequence[ToolObservation]) -> list[str]:
    """Extract symptom signatures for clustering, never for reuse."""

    signatures: list[str] = []
    for check in checks:
        text = _check_text(check.content)
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("[PASS]") or not _FAILURE_MARK.search(stripped):
                continue
            normalized = re.sub(r"\s+", " ", stripped).split("——", 1)[0].strip()[:240]
            if normalized and normalized not in signatures:
                signatures.append(normalized)
    return signatures[:8]


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
        "failure_signatures": failure_signatures(checks),
    }
