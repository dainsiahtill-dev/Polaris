"""Scope-authority projections for cross-task repair routing."""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from polaris.kernelone.quality.file_ownership_ledger import (
    build_file_ownership_handoff_requests,
    owner_task_identifier_token_aliases,
    task_identifier_token_aliases,
)

_SCHEMA_VERSION = "scope-authority-decision/1"


def _clean_token(value: Any) -> str:
    return str(value or "").strip()


def _normalize_target(raw: Any) -> str:
    target = _clean_token(raw).replace("\\", "/")
    while target.startswith("./"):
        target = target[2:]
    return target


def normalize_declared_scope_path(value: Any, *, workspace_name: str = "") -> str:
    """Normalize a declared task-scope path without consulting the filesystem."""

    token = _clean_token(value).strip("'\"`")
    token = token.replace("\\", "/").strip().lstrip("./")
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
        path = _clean_token(value)
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
            "handoff_request_count": len(requests),
            "owner_found_count": owner_found_count,
            "owner_unknown_count": owner_unknown_count,
            "recommended_routes": sorted(
                {
                    route
                    for item in requests
                    if (route := _clean_token(item.get("recommended_route")))
                }
            ),
            "deferred": True,
        }


def ownership_handoff_requests_from_scope_payload(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Extract ownership handoff requests from scope-authority projections.

    Scope handoff evidence may be projected as a full scope-authority decision,
    nested below a task-boundary scope filter, or as a flat compatibility field.
    This read-only extractor keeps downstream routers from duplicating that
    shape knowledge.
    """

    candidates: list[Any] = []
    scope_filter_raw = payload.get("task_boundary_scope_filter")
    if isinstance(scope_filter_raw, Mapping):
        candidates.extend(_ownership_handoff_candidate_values(scope_filter_raw))
    candidates.extend(_ownership_handoff_candidate_values(payload))

    empty_list_seen = False
    for candidate in candidates:
        if not isinstance(candidate, list):
            continue
        requests = tuple(dict(item) for item in candidate if isinstance(item, Mapping) and item)
        if requests:
            return requests
        empty_list_seen = True
    if empty_list_seen:
        return ()
    return ()


def owner_task_retry_handoff_requests_from_scope_payload(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return handoff requests that can be routed to the owning task."""

    return tuple(
        request
        for request in ownership_handoff_requests_from_scope_payload(payload)
        if bool(request.get("owner_found")) and _clean_token(request.get("recommended_route")) == "owner_task_retry"
    )


def unresolved_owner_handoff_requests_from_scope_payload(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return handoff requests that still need ownership resolution."""

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


def _ownership_handoff_candidate_values(payload: Mapping[str, Any]) -> tuple[Any, ...]:
    scope_authority_raw = payload.get("scope_authority")
    scope_authority: Mapping[str, Any] = scope_authority_raw if isinstance(scope_authority_raw, Mapping) else {}
    return (
        payload.get("ownership_handoff_requests"),
        scope_authority.get("ownership_handoff_requests"),
    )


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
    "task_record_identifier_tokens",
    "unresolved_owner_handoff_requests_from_scope_payload",
]
