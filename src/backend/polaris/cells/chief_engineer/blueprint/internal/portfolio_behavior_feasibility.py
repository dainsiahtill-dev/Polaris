"""Pure pre-freeze feasibility checks for CE cross-task behavior authority."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


class PortfolioBehaviorFeasibilityError(ValueError):
    """Raised before any immutable portfolio artifact is persisted."""

    def __init__(self, message: str, *, details: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.details = dict(details)


def validate_portfolio_behavior_feasibility(
    *,
    task_ids: Sequence[str],
    invariants: Sequence[Mapping[str, Any]],
    task_bindings: Mapping[str, Sequence[str]],
    completion_contract: Any,
) -> None:
    """Require reference closure and source/test cross-owner behavior linkage."""

    known_tasks = set(task_ids)
    if set(task_bindings) != known_tasks:
        raise PortfolioBehaviorFeasibilityError(
            "shared behavior task bindings must cover the exact portfolio task set",
            details={"task_ids": sorted(known_tasks), "binding_task_ids": sorted(task_bindings)},
        )
    by_id: dict[str, Mapping[str, Any]] = {}
    known_obligations = {
        item.obligation_id
        for group in (
            completion_contract.obligations.artifacts,
            completion_contract.obligations.entrypoints,
            completion_contract.obligations.verification,
        )
        for item in group
    }
    for invariant in invariants:
        invariant_id = str(invariant.get("invariant_id") or "")
        if invariant_id in by_id:
            raise PortfolioBehaviorFeasibilityError(
                "shared behavior invariant ids must be unique",
                details={"invariant_ids": [invariant_id]},
            )
        by_id[invariant_id] = invariant
        owner = str(invariant.get("owner_task_id") or "")
        consumers = {str(item) for item in invariant.get("consumer_task_ids") or ()}
        covered = {str(item) for item in invariant.get("covered_obligation_ids") or ()}
        unknown_tasks = sorted(({owner} | consumers) - known_tasks)
        unknown_obligations = sorted(covered - known_obligations)
        if unknown_tasks or unknown_obligations:
            raise PortfolioBehaviorFeasibilityError(
                "shared behavior invariant references unknown authority identities",
                details={
                    "invariant_ids": [invariant_id],
                    "task_ids": unknown_tasks,
                    "obligation_ids": unknown_obligations,
                },
            )
        missing_refs = sorted(
            task_id for task_id in {owner, *consumers} if invariant_id not in set(task_bindings.get(task_id, ()))
        )
        if missing_refs:
            raise PortfolioBehaviorFeasibilityError(
                "behavior invariant owner and consumers must reference the invariant",
                details={"invariant_ids": [invariant_id], "task_ids": missing_refs},
            )

    unknown_binding_refs = sorted(
        {str(ref) for refs in task_bindings.values() for ref in refs if str(ref) not in by_id}
    )
    if unknown_binding_refs:
        raise PortfolioBehaviorFeasibilityError(
            "task behavior bindings reference unknown invariants",
            details={"invariant_ids": unknown_binding_refs},
        )

    required_sources: dict[str, set[str]] = {}
    required_tests: dict[str, set[str]] = {}
    for artifact in completion_contract.obligations.artifacts:
        if artifact.applicability != "required" or not artifact.owner_task_id:
            continue
        if artifact.semantic_role in {"source", "entrypoint"}:
            required_sources.setdefault(artifact.owner_task_id, set()).add(artifact.obligation_id)
        elif artifact.semantic_role == "test":
            required_tests.setdefault(artifact.owner_task_id, set()).add(artifact.obligation_id)

    for test_owner, test_obligations in required_tests.items():
        source_owners = set(required_sources) - {test_owner}
        if not source_owners:
            continue
        linked = False
        for invariant in invariants:
            owner = str(invariant.get("owner_task_id") or "")
            consumers = {str(item) for item in invariant.get("consumer_task_ids") or ()}
            covered = {str(item) for item in invariant.get("covered_obligation_ids") or ()}
            if (
                owner in source_owners
                and test_owner in consumers
                and bool(covered & required_sources[owner])
                and bool(covered & test_obligations)
            ):
                linked = True
                break
        if not linked:
            raise PortfolioBehaviorFeasibilityError(
                "cross-task test ownership lacks a shared production behavior invariant",
                details={
                    "task_ids": sorted({test_owner, *source_owners}),
                    "obligation_ids": sorted(
                        test_obligations | set().union(*(required_sources[o] for o in source_owners))
                    ),
                    "invariant_ids": sorted(by_id),
                },
            )


__all__ = ["PortfolioBehaviorFeasibilityError", "validate_portfolio_behavior_feasibility"]
