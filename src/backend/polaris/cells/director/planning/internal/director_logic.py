"""Director logic utilities (Cell Implementation).

Migrated from ``polaris.cells.director.execution.logic``.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from polaris.domain.entities import DEFAULT_DEFECT_TICKET_FIELDS
from polaris.kernelone.utils.json_utils import parse_json_payload

logger = logging.getLogger(__name__)


def _parse_pass_fail(value: str) -> bool | None:
    normalized = value.strip().upper()
    if normalized == "PASS":
        return True
    if normalized == "FAIL":
        return False
    return None


def parse_acceptance(qa_text: str) -> bool | None:
    if not qa_text:
        return None
    payload = parse_json_payload(qa_text)
    if isinstance(payload, dict) and "acceptance" in payload:
        value = payload["acceptance"]
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            result = _parse_pass_fail(value)
            if result is not None:
                return result
    for line in qa_text.splitlines():
        stripped = line.strip()
        for prefix in ("ACCEPTANCE_DECISION:", "ACCEPTANCE:"):
            if stripped.startswith(prefix):
                result = _parse_pass_fail(stripped.split(":", 1)[1])
                if result is not None:
                    return result
    lower = qa_text.lower()
    if "pass" in lower and "fail" not in lower:
        return True
    if "fail" in lower and "pass" not in lower:
        return False
    return None


def extract_defect_ticket(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    source = payload.get("defect_ticket") or payload
    ticket: dict[str, Any] = {}
    for field in DEFAULT_DEFECT_TICKET_FIELDS:
        value = source.get(field)
        if value:
            ticket[field] = value
    if "defect_id" not in ticket:
        seed = str(payload.get("summary") or "")
        if seed:
            digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]
            ticket["defect_id"] = f"DEFECT-{digest.upper()}"
    return ticket


def write_gate_check(
    changed_files: list[str],
    act_files: list[str],
    pm_target_files: list[str] | None = None,
    *,
    require_change: bool = False,
) -> tuple[bool, str]:
    # Simplified version for cell logic
    if require_change and not changed_files:
        return False, "No files changed"
    return True, ""
