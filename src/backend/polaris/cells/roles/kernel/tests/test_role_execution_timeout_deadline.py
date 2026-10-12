"""Session budgets reuse the canonical physical execution deadline clamp."""

import time

import pytest
from polaris.cells.roles.kernel.public import llm_caller_contracts


def test_public_execution_timeout_clamp_keeps_budget_without_deadline() -> None:
    assert llm_caller_contracts.clamp_role_execution_timeout_to_deadline(timeout_seconds=12, context={}) == 12


def test_public_execution_timeout_clamp_respects_absolute_deadline() -> None:
    limit = llm_caller_contracts.clamp_role_execution_timeout_to_deadline(
        timeout_seconds=30,
        context={"factory_run_deadline_epoch_seconds": time.time() + 3},
    )
    assert 1 <= limit <= 3


def test_public_execution_timeout_clamp_rejects_expired_deadline() -> None:
    with pytest.raises(RuntimeError, match="execution_deadline_exhausted"):
        llm_caller_contracts.clamp_role_execution_timeout_to_deadline(
            timeout_seconds=30,
            context={"factory_run_deadline_epoch_seconds": time.time() - 1},
        )
