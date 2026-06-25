"""Shared policy for non-authoritative Director repair advisory metadata."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

FORBIDDEN_REPAIR_ADVISORY_METADATA_FIELDS: frozenset[str] = frozenset(
    {
        "after_hashes",
        "authoritative",
        "before_hashes",
        "file_content",
        "files_changed",
        "mode",
        "operation_ids",
        "patch_intents",
        "policy_override",
        "registered",
        "repair_plan",
        "risk_level",
        "rule_id",
        "source_tool",
        "success_verdict",
        "suggested_rules",
    }
)

FORBIDDEN_REPAIR_ADVISORY_SUGGESTED_RULE_FIELDS: frozenset[str] = FORBIDDEN_REPAIR_ADVISORY_METADATA_FIELDS | frozenset(
    {
        "content",
        "operation",
        "operations",
        "patch",
        "patches",
        "write_file",
    }
)

ALLOWED_REPAIR_ADVISORY_SUGGESTED_RULE_FIELDS: frozenset[str] = frozenset(
    {
        "confidence",
        "evidence",
        "examples",
        "fix_template",
        "language",
        "name",
        "notes",
        "pattern",
        "rationale",
        "scope",
    }
)


def copy_valid_repair_advisory_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return advisory metadata after rejecting authoritative repair fields."""

    payload = dict(metadata or {})
    present = sorted(key for key in FORBIDDEN_REPAIR_ADVISORY_METADATA_FIELDS if key in payload)
    if present:
        raise ValueError(f"repair advisory metadata contains forbidden authoritative fields: {present}")
    return payload


def copy_valid_repair_advisory_suggested_rules(rules: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Return normalized non-authoritative repair-rule suggestions."""

    normalized: list[dict[str, Any]] = []
    for raw_rule in rules or ():
        if not isinstance(raw_rule, Mapping):
            raise ValueError("repair advisory suggested_rules entries must be mappings")
        present = sorted(key for key in FORBIDDEN_REPAIR_ADVISORY_SUGGESTED_RULE_FIELDS if key in raw_rule)
        if present:
            raise ValueError(f"repair advisory suggested rule contains forbidden authoritative fields: {present}")
        unknown = sorted(key for key in raw_rule if key not in ALLOWED_REPAIR_ADVISORY_SUGGESTED_RULE_FIELDS)
        if unknown:
            raise ValueError(f"repair advisory suggested rule contains unsupported fields: {unknown}")
        pattern = str(raw_rule.get("pattern") or "").strip()
        fix_template = str(raw_rule.get("fix_template") or "").strip()
        if not pattern or not fix_template:
            raise ValueError("repair advisory suggested rule requires pattern and fix_template")
        evidence_raw = raw_rule.get("evidence")
        evidence = (
            [str(item or "").strip() for item in evidence_raw]
            if isinstance(evidence_raw, Sequence) and not isinstance(evidence_raw, str)
            else []
        )
        rule = {
            "pattern": pattern,
            "fix_template": fix_template,
            "confidence": max(0.0, min(float(raw_rule.get("confidence") or 0.0), 1.0)),
            "evidence": [item for item in evidence if item],
        }
        for key in ("name", "language", "rationale", "scope", "notes"):
            value = str(raw_rule.get(key) or "").strip()
            if value:
                rule[key] = value
        examples_raw = raw_rule.get("examples")
        if isinstance(examples_raw, Sequence) and not isinstance(examples_raw, str):
            examples = [str(item or "").strip() for item in examples_raw if str(item or "").strip()]
            if examples:
                rule["examples"] = examples
        normalized.append(rule)
    return normalized


__all__ = [
    "ALLOWED_REPAIR_ADVISORY_SUGGESTED_RULE_FIELDS",
    "FORBIDDEN_REPAIR_ADVISORY_METADATA_FIELDS",
    "FORBIDDEN_REPAIR_ADVISORY_SUGGESTED_RULE_FIELDS",
    "copy_valid_repair_advisory_metadata",
    "copy_valid_repair_advisory_suggested_rules",
]
