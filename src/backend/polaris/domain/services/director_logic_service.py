"""Shared director logic utilities.

提取自 director/planning 和 director/execution 中的重复逻辑。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from polaris.domain.entities import DEFAULT_DEFECT_TICKET_FIELDS
from polaris.kernelone.utils.json_utils import parse_json_payload

logger = logging.getLogger(__name__)


def _parse_pass_fail(value: str) -> bool | None:
    """Return True for PASS, False for FAIL, None otherwise."""
    normalized = value.strip().upper()
    if normalized == "PASS":
        return True
    if normalized == "FAIL":
        return False
    return None


def parse_acceptance(qa_text: str) -> bool | None:
    """Parse acceptance decision from QA output with strict format.

    Three-stage parsing:
    1. Structured JSON with "acceptance" key
    2. Explicit marker lines (ACCEPTANCE_DECISION:, ACCEPTANCE:)
    3. Fuzzy fallback: scan for pass/fail keywords
    """
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
    has_pass = "pass" in lower
    has_fail = "fail" in lower

    for line in lower.splitlines():
        if "acceptance decision" in line:
            if "fail" in line:
                return False
            if "pass" in line:
                return True

    if has_fail and not has_pass:
        return False
    if has_pass and not has_fail:
        return True

    return None


def _normalize_ticket_value(value: Any) -> Any:
    """Normalize defect ticket field values for consistent handling."""
    if isinstance(value, list):
        normalized = [str(item).strip() for item in value if str(item).strip()]
        return normalized
    if value is None:
        return ""
    return str(value).strip()


def extract_defect_ticket(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Extract defect ticket from payload using DEFAULT_DEFECT_TICKET_FIELDS.

    Handles both direct field access and nested defect_ticket sub-dict.
    Generates defect_id from summary+findings hash if not present.
    """
    if not isinstance(payload, dict):
        return {}
    source = payload.get("defect_ticket")
    if not isinstance(source, dict):
        source = payload
    ticket: dict[str, Any] = {}
    for field in DEFAULT_DEFECT_TICKET_FIELDS:
        value = _normalize_ticket_value(source.get(field))
        if isinstance(value, list):
            if value:
                ticket[field] = value
        elif isinstance(value, str) and value:
            ticket[field] = value
    if "defect_id" not in ticket:
        summary = str(payload.get("summary") or "").strip()
        findings = payload.get("findings")
        findings_text = ""
        if isinstance(findings, list):
            findings_text = "|".join(str(item).strip() for item in findings if str(item).strip())
        seed = f"{summary}|{findings_text}".strip("|")
        if seed:
            digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]
            ticket["defect_id"] = f"DEFECT-{digest.upper()}"
    return ticket


def validate_defect_ticket(
    payload: dict[str, Any] | None,
    required_fields: list[str] | None = None,
) -> tuple[bool, dict[str, Any], list[str]]:
    """Validate defect ticket has all required fields.

    Returns:
        Tuple of (is_valid, ticket_dict, missing_fields_list)
    """
    ticket = extract_defect_ticket(payload)
    fields = required_fields or DEFAULT_DEFECT_TICKET_FIELDS
    normalized_fields = [str(field).strip() for field in fields if str(field).strip()]
    missing: list[str] = []
    for field in normalized_fields:
        value = ticket.get(field)
        if isinstance(value, list):
            if not value:
                missing.append(field)
            continue
        if not isinstance(value, str) or not value.strip():
            missing.append(field)
    return len(missing) == 0, ticket, missing
