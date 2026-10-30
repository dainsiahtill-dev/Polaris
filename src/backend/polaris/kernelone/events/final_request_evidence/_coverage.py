"""Coverage-flag and evidence-ref projection helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from polaris.kernelone.events.final_request_evidence._constants import (
    _COVERAGE_FLAG_TO_REF,
    _COVERAGE_SOURCE_DETAIL_KEYS,
    _COVERAGE_SOURCE_HASH_KEYS,
    _COVERAGE_SOURCE_METADATA_FLAGS,
    _COVERAGE_SOURCE_STRUCTURED_REFS,
    _EVIDENCE_REQUIREMENT_TO_REF,
    _INCLUDED_EVIDENCE_COVERAGE_EXCLUDED_FLAGS,
    _METADATA_SUMMARY_FLAG_TO_REF,
    _STRUCTURED_EVIDENCE_FLAG_TO_KEY,
)
from polaris.kernelone.events.final_request_evidence._helpers import (
    _first_text,
    _text,
    _unique_texts,
)


def final_request_evidence_ref_for_requirement(value: Any) -> str:
    """Return the canonical evidence ref for a requirement or slot alias."""

    token = _text(value)
    return _EVIDENCE_REQUIREMENT_TO_REF.get(token.lower(), token)


def final_request_evidence_ref_for_coverage_flag(value: Any) -> str:
    """Return the canonical evidence ref represented by a coverage flag."""

    return _COVERAGE_FLAG_TO_REF.get(_text(value), "")


def final_request_evidence_refs_for_coverage_flags(
    coverage: Mapping[str, Any],
    *,
    require_present: bool = False,
    excluded_flags: Iterable[Any] = (),
) -> list[str]:
    """Project coverage flags to canonical evidence refs.

    `require_present` is used for included evidence. Required-ref fallback can
    intentionally project the configured coverage surface regardless of value.
    """

    excluded = {_text(flag) for flag in excluded_flags if _text(flag)}
    refs: list[str] = []
    for flag, present in coverage.items():
        normalized_flag = _text(flag)
        if not normalized_flag or normalized_flag in excluded:
            continue
        if require_present and not bool(present):
            continue
        ref = final_request_evidence_ref_for_coverage_flag(normalized_flag)
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def final_request_evidence_refs_for_metadata_summary(summary: Mapping[str, Any]) -> list[str]:
    """Project request metadata summary flags to canonical evidence refs."""

    refs: list[str] = []
    for flag, ref in _METADATA_SUMMARY_FLAG_TO_REF:
        if summary.get(flag) and ref not in refs:
            refs.append(ref)
    return refs


def final_request_structured_evidence_from_metadata_summary(summary: Mapping[str, Any]) -> dict[str, bool]:
    """Project request metadata summary flags to structured evidence booleans."""

    return {key: bool(summary.get(flag)) for flag, key in _STRUCTURED_EVIDENCE_FLAG_TO_KEY}


def final_request_included_evidence_refs(
    *,
    coverage: Mapping[str, Any],
    request_metadata_summary: Mapping[str, Any],
    receipt_refs: Iterable[Any] = (),
) -> list[str]:
    """Return canonical evidence refs present in the final provider request.

    Boundary:
        Included evidence is a KernelOne final-request projection. Role callers
        may provide coverage flags, structured metadata summary, and receipt
        references, but should not locally duplicate how those inputs become
        canonical included refs.
    """

    refs = ["final_provider_request"]
    refs.extend(
        final_request_evidence_refs_for_coverage_flags(
            coverage,
            require_present=True,
            excluded_flags=_INCLUDED_EVIDENCE_COVERAGE_EXCLUDED_FLAGS,
        )
    )
    refs.extend(final_request_evidence_refs_for_metadata_summary(request_metadata_summary))
    if list(receipt_refs):
        refs.append("receipt_store_refs")
    return _unique_texts(refs)


def build_final_request_coverage_sources(
    *,
    refs: Iterable[Any],
    included_refs: Iterable[Any],
    workflow_chain: Mapping[str, Any],
    request_metadata_summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Build structured coverage-source records for final-request evidence slots.

    Boundary:
        KernelOne owns how final-request evidence refs map to provenance hashes,
        detail summaries, and confidence labels. Callers provide the refs that
        are required or present for the current request; they should not
        duplicate this ref-to-source projection locally.
    """

    included = {final_request_evidence_ref_for_requirement(ref) for ref in included_refs}
    sources: list[dict[str, Any]] = []
    for raw_ref in refs:
        ref_type = final_request_evidence_ref_for_requirement(raw_ref)
        if not ref_type:
            continue
        present = ref_type in included
        source: dict[str, Any] = {
            "ref_type": ref_type,
            "present": present,
            "source": "final_provider_request",
            "confidence": _coverage_source_confidence(
                ref_type=ref_type,
                present=present,
                request_metadata_summary=request_metadata_summary,
            ),
            "freshness": "current_turn" if present else "unknown",
        }
        hash_value = _coverage_source_hash(
            ref_type=ref_type,
            workflow_chain=workflow_chain,
            request_metadata_summary=request_metadata_summary,
        )
        if hash_value:
            source["hash"] = hash_value
        details = _coverage_source_details(ref_type=ref_type, request_metadata_summary=request_metadata_summary)
        if details:
            source["details"] = details
        sources.append(source)
    return sources


def _coverage_source_hash(
    *,
    ref_type: str,
    workflow_chain: Mapping[str, Any],
    request_metadata_summary: Mapping[str, Any],
) -> str:
    summary_key, workflow_key = _COVERAGE_SOURCE_HASH_KEYS.get(ref_type, ("", ""))
    return _first_text(
        request_metadata_summary.get(summary_key) if summary_key else "",
        workflow_chain.get(workflow_key) if workflow_key else "",
    )


def _coverage_source_confidence(
    *,
    ref_type: str,
    present: bool,
    request_metadata_summary: Mapping[str, Any],
) -> str:
    structured_flag = _COVERAGE_SOURCE_METADATA_FLAGS.get(ref_type)
    if (structured_flag and request_metadata_summary.get(structured_flag)) or (
        present and ref_type in _COVERAGE_SOURCE_STRUCTURED_REFS
    ):
        return "structured_metadata"
    if present:
        return "text_heuristic"
    return "absent"


def _coverage_source_details(
    *,
    ref_type: str,
    request_metadata_summary: Mapping[str, Any],
) -> dict[str, Any]:
    detail_key = _COVERAGE_SOURCE_DETAIL_KEYS.get(ref_type, "")
    details = request_metadata_summary.get(detail_key) if detail_key else None
    return dict(details) if isinstance(details, Mapping) and details else {}
