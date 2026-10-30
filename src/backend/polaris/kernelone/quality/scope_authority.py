"""Scope-authority projections for cross-task repair routing."""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from polaris.kernelone.quality.file_ownership_ledger import (
    build_file_ownership_handoff_requests,
    normalize_file_ownership_target,
    owner_task_identifier_token_aliases,
    task_identifier_token_aliases,
)

_SCHEMA_VERSION = "scope-authority-decision/1"


@dataclass(frozen=True, slots=True)
class ScopeAuthorityOwnerHandoffIndex:
    """Read-only owner-routing projection for scope-authority handoffs.

    This object is a projection, not an authorization source. It keeps
    downstream orchestration layers from rebuilding owner matching with local
    string heuristics while preserving the file ownership ledger as the source
    of truth.
    """

    all_handoff_requests: tuple[dict[str, Any], ...]
    owner_handoff_requests: tuple[dict[str, Any], ...]
    unknown_owner_handoff_requests: tuple[dict[str, Any], ...]
    matched_owner_handoff_by_task_key: dict[str, dict[str, Any]]
    unmatched_owner_handoff_requests: tuple[dict[str, Any], ...]


def _clean_token(value: Any) -> str:
    return str(value or "").strip()


def _normalize_target(raw: Any) -> str:
    return normalize_file_ownership_target(raw)


def normalize_declared_scope_path(value: Any, *, workspace_name: str = "") -> str:
    """Normalize a declared task-scope path without consulting the filesystem."""

    token = _clean_token(value).strip("'\"`")
    token = token.replace("\\", "/").strip()
    while token.startswith("./"):
        token = token[2:]
    while token.endswith((".", ":", "，", "。", "；", ";", ",")):
        token = token[:-1].strip()
    if not token:
        return ""
    parts = [part for part in token.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return ""
    workspace_prefix = _clean_token(workspace_name).lower()
    if workspace_prefix and len(parts) > 1 and parts[0].lower() == workspace_prefix:
        parts = parts[1:]
    return "/".join(parts)


def glob_declared_scope_path_matches(path: str, pattern: str) -> bool:
    """Case-insensitive glob match for declared task scopes."""

    normalized_path = normalize_declared_scope_path(path).casefold()
    normalized_pattern = normalize_declared_scope_path(pattern).casefold()
    if not normalized_path or not normalized_pattern:
        return False
    if fnmatch.fnmatch(normalized_path, normalized_pattern):
        return True
    if "/**/" not in normalized_pattern:
        return False
    shallow_pattern = normalized_pattern.replace("/**/", "/")
    return fnmatch.fnmatch(normalized_path, shallow_pattern)


def path_matches_declared_scope_candidate(path: str, candidate: str) -> bool:
    """Return whether a path belongs to one normalized declared scope candidate."""

    normalized_path = normalize_declared_scope_path(path)
    normalized_candidate = normalize_declared_scope_path(candidate).rstrip("/")
    if not normalized_path or not normalized_candidate:
        return False
    if any(ch in normalized_candidate for ch in ("*", "?")):
        return glob_declared_scope_path_matches(normalized_path, normalized_candidate)
    path_folded = normalized_path.casefold()
    candidate_folded = normalized_candidate.casefold()
    if path_folded == candidate_folded:
        return True
    return path_folded.startswith(f"{candidate_folded}/")


def path_matches_any_declared_scope_candidate(path: str, candidates: Sequence[str]) -> bool:
    """Return whether a path belongs to any declared task-scope candidate."""

    normalized_path = normalize_declared_scope_path(path)
    if not normalized_path:
        return False
    return any(path_matches_declared_scope_candidate(normalized_path, candidate) for candidate in candidates)


def partition_paths_by_declared_scope(
    paths: Sequence[Any],
    declared_scope_candidates: Sequence[Any],
    *,
    workspace_name: str = "",
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Partition paths into in-scope and out-of-scope buckets.

    ``declared_scope_candidates`` is authoritative task scope evidence. When it
    is empty, every non-empty path remains in-scope; this preserves existing
    Director behavior for tasks without declared file targets while still
    centralizing the path-matching semantics in ScopeAuthority.

    Complexity:
        O(p * c) time where ``p`` is path count and ``c`` is scope candidate
        count; O(p + c) memory for normalized/deduplicated rows.
    """

    normalized_candidates = tuple(
        candidate
        for value in declared_scope_candidates
        if (candidate := normalize_declared_scope_path(value, workspace_name=workspace_name))
    )
    in_scope: list[str] = []
    out_of_scope: list[str] = []
    seen: set[str] = set()
    for value in paths:
        path = normalize_declared_scope_path(value, workspace_name=workspace_name)
        if not path or path in seen:
            continue
        seen.add(path)
        if not normalized_candidates or path_matches_any_declared_scope_candidate(path, normalized_candidates):
            in_scope.append(path)
        else:
            out_of_scope.append(path)
    return tuple(in_scope), tuple(out_of_scope)


def _dedupe_targets(values: Iterable[Any]) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        target = _normalize_target(value)
        if not target or target in seen:
            continue
        seen.add(target)
        output.append(target)
    return tuple(output)


@dataclass(frozen=True, slots=True)
class ScopeAuthorityDecision:
    """Read-only decision for out-of-scope repair targets.

    The decision is not write authorization. It explains why a current task
    deferred targets and which owning task, if any, should receive the retry.
    """

    reason: str
    requesting_task_id: str
    task_declared_write_targets: tuple[str, ...]
    out_of_scope_repair_target_files: tuple[str, ...]
    ownership_handoff_requests: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        requests = [dict(item) for item in self.ownership_handoff_requests]
        owner_task_retry_requests = [
            dict(item)
            for item in requests
            if bool(item.get("owner_found")) and _clean_token(item.get("recommended_route")) == "owner_task_retry"
        ]
        unresolved_owner_requests = [
            dict(item)
            for item in requests
            if not bool(item.get("owner_found"))
            or _clean_token(item.get("recommended_route")) == "scope_authority_resolution"
        ]
        owner_found_count = sum(1 for item in requests if bool(item.get("owner_found")))
        owner_unknown_count = sum(1 for item in requests if not bool(item.get("owner_found")))
        return {
            "schema_version": _SCHEMA_VERSION,
            "authority": "kernelone.quality.scope_authority",
            "reason": self.reason,
            "requesting_task_id": self.requesting_task_id,
            "task_declared_write_targets": list(self.task_declared_write_targets),
            "out_of_scope_repair_target_files": list(self.out_of_scope_repair_target_files),
            "ownership_handoff_requests": requests,
            "owner_task_retry_handoff_requests": owner_task_retry_requests,
            "unresolved_owner_handoff_requests": unresolved_owner_requests,
            "handoff_request_count": len(requests),
            "owner_found_count": owner_found_count,
            "owner_unknown_count": owner_unknown_count,
            "recommended_routes": sorted(
                {route for item in requests if (route := _clean_token(item.get("recommended_route")))}
            ),
            "deferred": True,
        }


def _bounded_projection_list(payload: Mapping[str, Any], key: str, *, limit: int) -> list[Any]:
    raw_values = payload.get(key)
    if not isinstance(raw_values, Sequence) or isinstance(raw_values, (str, bytes, bytearray)):
        return []
    output: list[Any] = []
    for item in raw_values[:limit]:
        if isinstance(item, Mapping):
            output.append(dict(item))
        else:
            output.append(item)
    return output


def scope_authority_decision_summary(
    decision: ScopeAuthorityDecision | Mapping[str, Any],
    *,
    limit: int = 12,
) -> dict[str, Any]:
    """Return a bounded display/audit projection for a scope decision.

    ScopeAuthority remains the full read-only authority object. This helper is
    only a compact projection for receipts, UI, or task-boundary evidence; it
    does not grant writes or change the underlying owner-routing decision.

    Complexity:
        O(k) time and memory where ``k`` is the bounded number of projected
        values across the known summary fields.
    """

    if isinstance(decision, ScopeAuthorityDecision):
        payload: Mapping[str, Any] = decision.to_dict()
    elif isinstance(decision, Mapping):
        nested = decision.get("scope_authority")
        payload = nested if isinstance(nested, Mapping) and "task_declared_write_targets" not in decision else decision
    else:
        payload = {}
    bounded_limit = max(0, int(limit))
    return {
        "task_declared_write_targets": _bounded_projection_list(
            payload, "task_declared_write_targets", limit=bounded_limit
        ),
        "out_of_scope_repair_target_files": _bounded_projection_list(
            payload, "out_of_scope_repair_target_files", limit=bounded_limit
        ),
        "ownership_handoff_requests": _bounded_projection_list(
            payload, "ownership_handoff_requests", limit=bounded_limit
        ),
        "owner_task_retry_handoff_requests": _bounded_projection_list(
            payload, "owner_task_retry_handoff_requests", limit=bounded_limit
        ),
        "unresolved_owner_handoff_requests": _bounded_projection_list(
            payload, "unresolved_owner_handoff_requests", limit=bounded_limit
        ),
    }


def ownership_handoff_requests_from_scope_payload(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Extract ownership handoff requests from scope-authority projections.

    Scope handoff evidence may be projected as a full scope-authority decision,
    nested below a task-boundary scope filter, or as a flat compatibility field.
    This read-only extractor keeps downstream routers from duplicating that
    shape knowledge.
    """

    requests = _handoff_requests_from_scope_payload(payload, "ownership_handoff_requests")
    if requests:
        return requests
    return _classified_handoff_requests_from_scope_payload(payload)


def owner_task_retry_handoff_requests_from_scope_payload(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return handoff requests that can be routed to the owning task."""

    projected = _handoff_requests_from_scope_payload(payload, "owner_task_retry_handoff_requests")
    if projected:
        return projected
    return tuple(
        request
        for request in ownership_handoff_requests_from_scope_payload(payload)
        if bool(request.get("owner_found")) and _clean_token(request.get("recommended_route")) == "owner_task_retry"
    )


def unresolved_owner_handoff_requests_from_scope_payload(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return handoff requests that still need ownership resolution."""

    projected = _handoff_requests_from_scope_payload(payload, "unresolved_owner_handoff_requests")
    if projected:
        return projected
    return tuple(
        request
        for request in ownership_handoff_requests_from_scope_payload(payload)
        if not bool(request.get("owner_found"))
        or _clean_token(request.get("recommended_route")) == "scope_authority_resolution"
    )


def task_record_identifier_tokens(record: Mapping[str, Any]) -> frozenset[str]:
    """Return normalized identifier aliases for a task-board record.

    This is read-only routing evidence. It does not authorize writes or mutate
    task state; callers use it to match a scope-authority owner handoff request
    back to the owning task row.
    """

    tokens: set[str] = set()
    for value in (
        record.get("id"),
        record.get("task_id"),
        record.get("external_task_id"),
        record.get("pm_task_id"),
        record.get("source_task_id"),
    ):
        token = _clean_token(value)
        if token:
            tokens.update(task_identifier_token_aliases(token))
    metadata_raw = record.get("metadata")
    metadata: Mapping[str, Any] = metadata_raw if isinstance(metadata_raw, Mapping) else {}
    for key in ("external_task_id", "pm_task_id", "source_task_id", "task_id"):
        token = _clean_token(metadata.get(key))
        if token:
            tokens.update(task_identifier_token_aliases(token))
    return frozenset(tokens)


def owner_handoff_identifier_tokens(request: Mapping[str, Any]) -> frozenset[str]:
    """Return normalized owner identifier aliases for one handoff request."""

    tokens: set[str] = set()
    raw_tokens = request.get("owner_task_identifier_tokens")
    if isinstance(raw_tokens, list):
        for value in raw_tokens:
            token = _clean_token(value)
            if token:
                tokens.add(token)
    if tokens:
        return frozenset(tokens)
    tokens.update(owner_task_identifier_token_aliases(request.get("owner_step_id"), request.get("owner_parent")))
    return frozenset(tokens)


def matching_owner_handoff_request(
    record: Mapping[str, Any],
    handoff_requests: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return the owner handoff request matching a task-board record."""

    if not handoff_requests:
        return {}
    tokens = task_record_identifier_tokens(record)
    if not tokens:
        return {}
    for request in handoff_requests:
        if tokens & owner_handoff_identifier_tokens(request):
            return dict(request)
    return {}


def build_owner_handoff_index(
    payload: Mapping[str, Any],
    task_records: Sequence[Mapping[str, Any]],
) -> ScopeAuthorityOwnerHandoffIndex:
    """Build a task-record index for owner handoff routing evidence.

    ``payload`` may be a full repair payload, a task-boundary scope-filter
    payload, or a nested scope-authority projection. ``task_records`` are
    read-only task-board rows. The returned index never mutates task state and
    never expands write scope; callers decide how to route matched requests.
    """

    all_handoff_requests = ownership_handoff_requests_from_scope_payload(payload)
    owner_handoff_requests = owner_task_retry_handoff_requests_from_scope_payload(payload)
    unknown_owner_handoff_requests = unresolved_owner_handoff_requests_from_scope_payload(payload)

    matched_owner_handoff_keys: set[tuple[str, tuple[str, ...]]] = set()
    matched_owner_handoff_by_task_key: dict[str, dict[str, Any]] = {}
    for record in task_records:
        if not isinstance(record, Mapping):
            continue
        owner_handoff_request = matching_owner_handoff_request(record, owner_handoff_requests)
        if not owner_handoff_request:
            continue
        task_key = task_record_routing_key(record)
        if task_key:
            matched_owner_handoff_by_task_key[task_key] = owner_handoff_request
        matched_owner_handoff_keys.add(_owner_handoff_match_key(owner_handoff_request))

    unmatched_owner_handoff_requests = tuple(
        dict(request)
        for request in owner_handoff_requests
        if _owner_handoff_match_key(request) not in matched_owner_handoff_keys
    )
    return ScopeAuthorityOwnerHandoffIndex(
        all_handoff_requests=tuple(dict(request) for request in all_handoff_requests),
        owner_handoff_requests=tuple(dict(request) for request in owner_handoff_requests),
        unknown_owner_handoff_requests=tuple(dict(request) for request in unknown_owner_handoff_requests),
        matched_owner_handoff_by_task_key=matched_owner_handoff_by_task_key,
        unmatched_owner_handoff_requests=unmatched_owner_handoff_requests,
    )


def owner_handoff_index_summary(
    index: ScopeAuthorityOwnerHandoffIndex | None = None,
    *,
    limit: int = 12,
) -> dict[str, Any]:
    """Return a bounded display/audit projection for owner handoff routing.

    The index is still a read-only projection; this helper centralizes the
    public summary shape so orchestration layers do not rebuild count/list
    fields with local owner-routing knowledge.

    Complexity:
        O(k) time and memory where ``k`` is the bounded number of projected
        handoff requests.
    """

    bounded_limit = max(0, int(limit))
    if index is None:
        return {
            "ownership_handoff_count": 0,
            "matched_owner_handoff_count": 0,
            "matched_owner_handoff_routes": [],
            "unmatched_owner_handoff_count": 0,
            "unmatched_owner_handoff_requests": [],
            "unknown_owner_handoff_count": 0,
            "unknown_owner_handoff_requests": [],
        }
    matched_routes: list[dict[str, Any]] = []
    for task_key, request in index.matched_owner_handoff_by_task_key.items():
        if len(matched_routes) >= bounded_limit:
            break
        matched_routes.append({"task_key": task_key, "request": dict(request)})
    return {
        "ownership_handoff_count": len(index.all_handoff_requests),
        "matched_owner_handoff_count": len(index.matched_owner_handoff_by_task_key),
        "matched_owner_handoff_routes": matched_routes,
        "unmatched_owner_handoff_count": len(index.unmatched_owner_handoff_requests),
        "unmatched_owner_handoff_requests": [
            dict(request) for request in index.unmatched_owner_handoff_requests[:bounded_limit]
        ],
        "unknown_owner_handoff_count": len(index.unknown_owner_handoff_requests),
        "unknown_owner_handoff_requests": [
            dict(request) for request in index.unknown_owner_handoff_requests[:bounded_limit]
        ],
    }


@dataclass(frozen=True, slots=True)
class ScopeAuthorityOwnerHandoffRouting:
    """Canonical owner-handoff routing result.

    This is the single-entry-point projection that downstream orchestrators
    consume to decide what to do with out-of-scope repair targets.  It
    composes ``ScopeAuthorityOwnerHandoffIndex`` with the bounded summary
    projection so consumers never need to rebuild routing logic with local
    string heuristics.

    Read-only: it does not authorize writes or mutate task state.
    """

    index: ScopeAuthorityOwnerHandoffIndex
    summary: dict[str, Any]
    owner_routing_keys: tuple[str, ...]
    has_routable_handoffs: bool
    has_unresolved_handoffs: bool


def resolve_owner_handoff_routing(
    payload: Mapping[str, Any],
    task_records: Sequence[Mapping[str, Any]],
    *,
    summary_limit: int = 12,
) -> ScopeAuthorityOwnerHandoffRouting:
    """Resolve owner-handoff routing from a scope-authority payload.

    This is the canonical single-entry-point for routing out-of-scope repair
    targets to their owning tasks.  It chains payload extraction, task-record
    matching, and bounded summary projection into one atomic call so downstream
    orchestrators do not rebuild owner matching with local heuristics.

    ``payload`` may be a full repair payload, a task-boundary scope-filter
    payload, or a nested scope-authority projection (same shapes accepted by
    ``ownership_handoff_requests_from_scope_payload``).

    ``task_records`` are read-only task-board rows; they are never mutated.

    Complexity:
        O(r * t) time where ``r`` is handoff request count and ``t`` is task
        record count; O(r + t) memory for normalized/deduplicated rows.

    Returns:
        A ``ScopeAuthorityOwnerHandoffRouting`` containing the index, bounded
        summary, sorted routing keys, and convenience flags for consumers that
        only need to branch on "has anything to route?".
    """

    index = build_owner_handoff_index(payload, task_records)
    summary = owner_handoff_index_summary(index, limit=summary_limit)
    routing_keys = tuple(sorted(index.matched_owner_handoff_by_task_key))
    has_routable = bool(routing_keys) or bool(index.unmatched_owner_handoff_requests)
    has_unresolved = bool(index.unknown_owner_handoff_requests)
    return ScopeAuthorityOwnerHandoffRouting(
        index=index,
        summary=summary,
        owner_routing_keys=routing_keys,
        has_routable_handoffs=has_routable,
        has_unresolved_handoffs=has_unresolved,
    )


def task_record_routing_key(record: Mapping[str, Any]) -> str:
    """Return the stable task-row routing key used by owner handoff indexes.

    This key is read-only routing evidence. It must not be used as a write
    authorization source; write authorization remains scoped by the execution
    envelope and tool guards.
    """

    return _clean_token(record.get("id") or record.get("task_id"))


def _owner_handoff_match_key(request: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
    target_file = _normalize_target(request.get("target_file"))
    return target_file, tuple(sorted(owner_handoff_identifier_tokens(request)))


def _ownership_handoff_candidate_values(payload: Mapping[str, Any]) -> tuple[Any, ...]:
    return _handoff_candidate_values(payload, "ownership_handoff_requests")


def _handoff_candidate_values(payload: Mapping[str, Any], key: str) -> tuple[Any, ...]:
    scope_authority_raw = payload.get("scope_authority")
    scope_authority: Mapping[str, Any] = scope_authority_raw if isinstance(scope_authority_raw, Mapping) else {}
    return (
        payload.get(key),
        scope_authority.get(key),
    )


def _handoff_requests_from_scope_payload(payload: Mapping[str, Any], key: str) -> tuple[dict[str, Any], ...]:
    """Read-only extractor for typed handoff-request rows from a scope payload.

    ``payload`` may be a full repair payload, a task-boundary scope-filter
    payload, or a nested scope-authority projection. Candidates are scanned in
    priority order:

    1. ``task_boundary_scope_filter[key]``
    2. ``task_boundary_scope_filter.scope_authority[key]``
    3. ``payload[key]``
    4. ``payload.scope_authority[key]``
    5. last-to-first ``rounds[*].repair_summary.task_boundary_scope_filter``
    6. last-to-first ``rounds[*].repair_summary``

    Each candidate must be a ``list`` or ``tuple`` of ``Mapping`` rows. Any
    non-Mapping row is ignored (string parsing is never used to recover
    evidence). Within a candidate, duplicate Mapping rows are deduplicated in a
    stable, order-preserving way. An empty ``list`` or ``tuple`` at a higher
    priority is treated as explicit-empty evidence and prevents fallthrough to
    lower-priority candidates.
    """

    candidates: list[Any] = []
    scope_filter_raw = payload.get("task_boundary_scope_filter")
    if isinstance(scope_filter_raw, Mapping):
        candidates.extend(_handoff_candidate_values(scope_filter_raw, key))
    candidates.extend(_handoff_candidate_values(payload, key))
    rounds_raw = payload.get("rounds")
    if isinstance(rounds_raw, (list, tuple)):
        for item in reversed(rounds_raw):
            if not isinstance(item, Mapping):
                continue
            summary_raw = item.get("repair_summary")
            if isinstance(summary_raw, Mapping):
                summary_filter = summary_raw.get("task_boundary_scope_filter")
                if isinstance(summary_filter, Mapping):
                    candidates.extend(_handoff_candidate_values(summary_filter, key))
                candidates.extend(_handoff_candidate_values(summary_raw, key))

    empty_sequence_seen = False
    for candidate in candidates:
        if not isinstance(candidate, (list, tuple)) or isinstance(candidate, (str, bytes, bytearray)):
            continue
        requests, had_mapping_row = _dedupe_mapping_rows(candidate)
        if requests:
            return requests
        if had_mapping_row or len(candidate) == 0:
            empty_sequence_seen = True
    if empty_sequence_seen:
        return ()
    return ()


def _dedupe_mapping_rows(candidate: list[Any] | tuple[Any, ...]) -> tuple[tuple[dict[str, Any], ...], bool]:
    """Return order-preserved unique Mapping rows from a candidate container.

    Returns ``(unique_rows, had_mapping_row)``. ``had_mapping_row`` is ``True``
    if the candidate contained at least one Mapping entry, even when all rows
    deduplicated away; this lets callers distinguish explicit-empty containers
    from payloads that never carried row-shaped evidence.

    Deduplication uses a stable string fingerprint for each row so it never
    raises on rows carrying nested lists, dicts, or unhashable values. The
    fingerprint is purely an identity/equality check for repeated rows; no
    string parsing is used to recover evidence.
    """

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    had_mapping_row = False
    for item in candidate:
        if not isinstance(item, Mapping) or not item:
            continue
        had_mapping_row = True
        fingerprint = _mapping_row_fingerprint(item)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(dict(item))
    return tuple(unique), had_mapping_row


def _mapping_row_fingerprint(row: Mapping[str, Any]) -> str:
    """Return a stable string fingerprint for a single Mapping row."""

    parts: list[str] = []
    for key in sorted(row.keys(), key=lambda value: str(value)):
        parts.append(f"{key!r}:{_fingerprint_value(row[key])}")
    return "{" + ",".join(parts) + "}"


def _fingerprint_value(value: Any) -> str:
    if isinstance(value, Mapping):
        return _mapping_row_fingerprint(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_fingerprint_value(item) for item in value) + "]"
    if isinstance(value, (set, frozenset)):
        return "{" + ",".join(sorted(_fingerprint_value(item) for item in value)) + "}"
    return repr(value)


def _classified_handoff_requests_from_scope_payload(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    requests: list[dict[str, Any]] = []
    for key in ("owner_task_retry_handoff_requests", "unresolved_owner_handoff_requests"):
        for request in _handoff_requests_from_scope_payload(payload, key):
            if request not in requests:
                requests.append(dict(request))
    return tuple(requests)


def build_scope_authority_decision(
    *,
    workspace: str,
    cache_root: str,
    task_declared_write_targets: Sequence[Any],
    out_of_scope_repair_target_files: Sequence[Any],
    requesting_task_id: str,
    reason: str,
) -> ScopeAuthorityDecision:
    """Build the read-only scope decision for a task-boundary defer."""

    targets = _dedupe_targets(out_of_scope_repair_target_files)
    handoff_requests: tuple[dict[str, Any], ...] = ()
    if _clean_token(workspace) and targets:
        handoff_requests = build_file_ownership_handoff_requests(
            _clean_token(workspace),
            _clean_token(cache_root),
            list(targets),
            requesting_task_id=_clean_token(requesting_task_id),
            reason=_clean_token(reason),
        )
    return ScopeAuthorityDecision(
        reason=_clean_token(reason),
        requesting_task_id=_clean_token(requesting_task_id),
        task_declared_write_targets=_dedupe_targets(task_declared_write_targets),
        out_of_scope_repair_target_files=targets,
        ownership_handoff_requests=handoff_requests,
    )


__all__ = [
    "ScopeAuthorityDecision",
    "ScopeAuthorityOwnerHandoffIndex",
    "ScopeAuthorityOwnerHandoffRouting",
    "build_owner_handoff_index",
    "build_scope_authority_decision",
    "glob_declared_scope_path_matches",
    "matching_owner_handoff_request",
    "normalize_declared_scope_path",
    "owner_handoff_identifier_tokens",
    "owner_task_retry_handoff_requests_from_scope_payload",
    "ownership_handoff_requests_from_scope_payload",
    "partition_paths_by_declared_scope",
    "path_matches_any_declared_scope_candidate",
    "path_matches_declared_scope_candidate",
    "resolve_owner_handoff_routing",
    "scope_authority_decision_summary",
    "task_record_identifier_tokens",
    "task_record_routing_key",
    "unresolved_owner_handoff_requests_from_scope_payload",
]
