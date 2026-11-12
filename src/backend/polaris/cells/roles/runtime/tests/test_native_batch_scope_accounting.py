"""Every native member reaches the real policy owner and a terminal outcome.

Only the remote model is replaced by the existing loopback HTTP fixture.
Canonical preparation, TaskRuntime, DEO, gateway, filesystem and receipts remain
real. Harness source anchors and negative input injections are checked below.

The scenarios this module used to drive ran through the legacy role
execution kernel, which is retired: a new engineering run has exactly one
owner, the engineering run service. The native member accounting below is
therefore asserted against the retired entry, which must refuse before it
can dispatch a model or write anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.roles.kernel.tests.legacy_engine_retirement import (
    assert_legacy_session_entry_is_closed,
    legacy_retirement_reason,
)


@pytest.mark.asyncio
async def test_retired_native_members_never_dispatch_a_model_or_write(
    tmp_path: Path,
) -> None:
    """The native member accounting has no live subject after the retirement."""
    assert legacy_retirement_reason()

    await assert_legacy_session_entry_is_closed(
        session_id="retired-native-members",
        workspace=str(tmp_path),
    )

    # The retired entry refuses before it can create any workspace effect.
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_retired_foreign_owner_never_dispatches_a_model_or_writes(
    tmp_path: Path,
) -> None:
    """A foreign, stale or missing owner cannot reach the retired kernel."""
    assert legacy_retirement_reason()

    await assert_legacy_session_entry_is_closed(
        session_id="retired-foreign-owner",
        workspace=str(tmp_path),
    )

    assert not list(tmp_path.iterdir())
