"""Scope-authority projections for cross-task repair routing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from polaris.kernelone.quality.file_ownership_ledger import build_file_ownership_handoff_requests

_SCHEMA_VERSION = "scope-authority-decision/1"


def _clean_token(value: Any) -> str:
    return str(value or "").strip()


def _normalize_target(raw: Any) -> str:
    target = _clean_token(raw).replace("\\", "/")
    while target.startswith("./"):
        target = target[2:]
    return target


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
]
