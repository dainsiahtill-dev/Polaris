"""Evidence-slot and tool-slot builders for final-request coverage."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.kernelone.events.final_request_evidence._helpers import (
    _as_mapping,
    _string_list,
    _text,
)


def missing_required_refs_from_evidence_slots(evidence_coverage: Mapping[str, Any]) -> list[str]:
    """Return required evidence refs marked missing by structured evidence slots."""

    slots = evidence_coverage.get("evidence_slots")
    if not isinstance(slots, list):
        return []
    missing_refs: list[str] = []
    seen: set[str] = set()
    for item in slots:
        if not isinstance(item, Mapping):
            continue
        if _text(item.get("schema_version")) != "polaris.final_request_evidence_slot.v1":
            continue
        if item.get("required") is not True or item.get("missing") is not True:
            continue
        ref_type = _text(item.get("ref_type"))
        if ref_type and ref_type not in seen:
            seen.add(ref_type)
            missing_refs.append(ref_type)
    return missing_refs


def missing_required_tools_from_tool_slots(evidence_coverage: Mapping[str, Any]) -> list[str] | None:
    """Return required tools marked missing by structured tool-evidence slots."""

    slots = evidence_coverage.get("tool_evidence_slots")
    if not isinstance(slots, list):
        return None
    missing_tools: list[str] = []
    seen: set[str] = set()
    for item in slots:
        if not isinstance(item, Mapping):
            continue
        if _text(item.get("schema_version")) != "polaris.final_request_tool_slot.v1":
            continue
        if item.get("required") is not True or item.get("missing") is not True:
            continue
        tool_name = _text(item.get("tool_name"))
        if tool_name and tool_name not in seen:
            seen.add(tool_name)
            missing_tools.append(tool_name)
    return missing_tools


def missing_required_refs_from_evidence_coverage(
    evidence_coverage: Mapping[str, Any],
    existing_evidence: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return missing required evidence refs from coverage slots or legacy fields.

    Boundary:
        This owns the precedence for final-request missing-ref projection:
        structured evidence slots are authoritative when present; otherwise the
        legacy ``missing_required_refs`` field is used for compatibility.
    """

    slot_refs = missing_required_refs_from_evidence_slots(evidence_coverage)
    if slot_refs:
        return slot_refs
    existing = _as_mapping(existing_evidence)
    return _string_list(evidence_coverage.get("missing_required_refs") or existing.get("missing_required_refs"))


def missing_required_tools_from_evidence_coverage(
    evidence_coverage: Mapping[str, Any],
    existing_evidence: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return missing required tools from tool slots or legacy fields.

    Boundary:
        Structured tool slots carry the current authority. A present slot list
        may intentionally produce an empty result; only when slots are absent do
        we fall back to legacy ``missing_required_tools`` fields.
    """

    slot_tools = missing_required_tools_from_tool_slots(evidence_coverage)
    if slot_tools is not None:
        return slot_tools
    existing = _as_mapping(existing_evidence)
    return _string_list(evidence_coverage.get("missing_required_tools") or existing.get("missing_required_tools"))


def build_final_request_evidence_slots(
    *,
    coverage_sources: list[dict[str, Any]],
    required_refs: list[str],
    included_refs: list[str],
    missing_required_refs: list[str],
) -> list[dict[str, Any]]:
    """Build typed final-request evidence slots from coverage refs.

    Boundary:
        The slot schema is the KernelOne final-request evidence contract.
        Callers may decide which refs are required/present/missing, but should
        not locally duplicate the slot shape.
    """

    required = set(required_refs)
    included = set(included_refs)
    missing = set(missing_required_refs)
    slots: list[dict[str, Any]] = []
    for source in coverage_sources:
        ref_type = _text(source.get("ref_type"))
        if not ref_type:
            continue
        slot = {
            "schema_version": "polaris.final_request_evidence_slot.v1",
            "ref_type": ref_type,
            "required": ref_type in required,
            "present": ref_type in included,
            "missing": ref_type in missing,
            "source": _text(source.get("source") or "final_provider_request"),
            "confidence": _text(source.get("confidence") or "absent"),
            "freshness": _text(source.get("freshness") or "unknown"),
        }
        hash_value = _text(source.get("hash"))
        if hash_value:
            slot["hash"] = hash_value
        details = source.get("details")
        if isinstance(details, Mapping) and details:
            slot["details"] = dict(details)
        slots.append(slot)
    return slots


def build_final_request_tool_slots(
    *,
    required_tools: list[str],
    available_tools: list[str],
    missing_required_tools: list[str],
) -> list[dict[str, Any]]:
    """Build typed final-request tool slots from tool coverage facts."""

    required = set(required_tools)
    available = set(available_tools)
    missing = set(missing_required_tools)
    slots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tool_name in [*required_tools, *available_tools]:
        token = _text(tool_name)
        if not token or token in seen:
            continue
        seen.add(token)
        slots.append(
            {
                "schema_version": "polaris.final_request_tool_slot.v1",
                "tool_name": token,
                "required": token in required,
                "present": token in available,
                "missing": token in missing,
                "source": "final_provider_request.tools",
                "confidence": "tool_schema" if token in available else "absent",
                "freshness": "current_turn" if token in available else "unknown",
            }
        )
    return slots
