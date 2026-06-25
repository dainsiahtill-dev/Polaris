"""Shared policy for non-authoritative Director repair advisory metadata."""

from __future__ import annotations

from typing import Any, Mapping

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
    }
)


def copy_valid_repair_advisory_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return advisory metadata after rejecting authoritative repair fields."""

    payload = dict(metadata or {})
    present = sorted(key for key in FORBIDDEN_REPAIR_ADVISORY_METADATA_FIELDS if key in payload)
    if present:
        raise ValueError(f"repair advisory metadata contains forbidden authoritative fields: {present}")
    return payload


__all__ = [
    "FORBIDDEN_REPAIR_ADVISORY_METADATA_FIELDS",
    "copy_valid_repair_advisory_metadata",
]
