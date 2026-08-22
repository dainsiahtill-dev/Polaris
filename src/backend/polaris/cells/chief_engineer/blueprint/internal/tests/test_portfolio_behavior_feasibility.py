"""Unit tests for CE pre-freeze cross-task behavior feasibility."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from polaris.cells.chief_engineer.blueprint.internal.portfolio_behavior_feasibility import (
    PortfolioBehaviorFeasibilityError,
    validate_portfolio_behavior_feasibility,
)


def _completion_contract() -> SimpleNamespace:
    return SimpleNamespace(
        obligations=SimpleNamespace(
            artifacts=(
                SimpleNamespace(
                    obligation_id="artifact-source",
                    semantic_role="source",
                    applicability="required",
                    owner_task_id="TASK-1",
                ),
                SimpleNamespace(
                    obligation_id="artifact-test",
                    semantic_role="test",
                    applicability="required",
                    owner_task_id="TASK-2",
                ),
            ),
            entrypoints=(),
            verification=(),
        )
    )


def _invariant() -> dict[str, object]:
    return {
        "invariant_id": "INV-1",
        "owner_task_id": "TASK-1",
        "consumer_task_ids": ["TASK-2"],
        "covered_obligation_ids": ["artifact-source", "artifact-test"],
    }


def test_cross_task_source_and_test_share_behavior_authority() -> None:
    validate_portfolio_behavior_feasibility(
        task_ids=("TASK-1", "TASK-2"),
        invariants=(_invariant(),),
        task_bindings={"TASK-1": ("INV-1",), "TASK-2": ("INV-1",)},
        completion_contract=_completion_contract(),
    )


def test_cross_task_test_without_shared_behavior_fails_before_freeze() -> None:
    with pytest.raises(PortfolioBehaviorFeasibilityError, match="lacks a shared production behavior invariant"):
        validate_portfolio_behavior_feasibility(
            task_ids=("TASK-1", "TASK-2"),
            invariants=(),
            task_bindings={"TASK-1": (), "TASK-2": ()},
            completion_contract=_completion_contract(),
        )


def test_behavior_authority_rejects_unknown_task_and_obligation() -> None:
    invariant = _invariant()
    invariant["consumer_task_ids"] = ["TASK-3"]
    invariant["covered_obligation_ids"] = ["artifact-unknown"]

    with pytest.raises(PortfolioBehaviorFeasibilityError) as raised:
        validate_portfolio_behavior_feasibility(
            task_ids=("TASK-1", "TASK-2"),
            invariants=(invariant,),
            task_bindings={"TASK-1": ("INV-1",), "TASK-2": ()},
            completion_contract=_completion_contract(),
        )

    assert raised.value.details["task_ids"] == ["TASK-3"]
    assert raised.value.details["obligation_ids"] == ["artifact-unknown"]


def test_behavior_bindings_must_cover_exact_portfolio_task_set() -> None:
    with pytest.raises(PortfolioBehaviorFeasibilityError, match="exact portfolio task set"):
        validate_portfolio_behavior_feasibility(
            task_ids=("TASK-1", "TASK-2"),
            invariants=(_invariant(),),
            task_bindings={"TASK-1": ("INV-1",)},
            completion_contract=_completion_contract(),
        )
