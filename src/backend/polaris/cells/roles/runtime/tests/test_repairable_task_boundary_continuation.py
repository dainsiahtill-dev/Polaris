"""Repairable task-boundary feedback across real kernel/session ownership.

The scenarios this module used to drive ran through the legacy role
execution kernel, which is retired: a new engineering run has exactly one
owner, the engineering run service, and the legacy write entry refuses
before it can commit anything. The task-boundary continuation subject
therefore has no live subject, and the module asserts the retirement
instead of pretending the retired path still works.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.roles.kernel.tests.legacy_engine_retirement import (
    assert_legacy_session_entry_is_closed,
    legacy_retirement_reason,
)


@pytest.mark.asyncio
async def test_retired_task_boundary_continuation_cannot_commit_or_continue(
    tmp_path: Path,
) -> None:
    """A retired session cannot commit a partial write or continue the task."""
    assert legacy_retirement_reason()

    await assert_legacy_session_entry_is_closed(
        session_id="retired-task-boundary-continuation",
        workspace=str(tmp_path),
    )

    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing_commit", "nonrepairable", "foreign_owner", "stale_owner"])
async def test_retired_empty_final_followup_cannot_bypass_commit_or_owner_failure(
    tmp_path: Path,
    mode: str,
) -> None:
    """An empty final follow-up cannot bypass the retired owner's checks."""
    assert legacy_retirement_reason()

    await assert_legacy_session_entry_is_closed(
        session_id=f"retired-empty-final-{mode}",
        workspace=str(tmp_path),
    )

    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_retired_repeated_empty_final_remains_failed(
    tmp_path: Path,
) -> None:
    """A repeated empty final turn has no live subject after the retirement."""
    assert legacy_retirement_reason()

    await assert_legacy_session_entry_is_closed(
        session_id="retired-repeated-empty-final",
        workspace=str(tmp_path),
    )

    assert not list(tmp_path.iterdir())
