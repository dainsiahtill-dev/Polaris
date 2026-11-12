"""Fresh quality projection through real Engine, Factory cutoff, native HTTP and DEO.

The scenario this module used to drive ran through the legacy role execution
kernel, which is retired: a new engineering run has exactly one owner, the
engineering run service, and the legacy write entry refuses before it can
admit a write or project a fresh quality observation. The fresh quality
continuation subject therefore has no live subject, and the module asserts
the retirement instead of pretending the retired path still works.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.roles.kernel.tests.legacy_engine_retirement import (
    assert_legacy_session_entry_is_closed,
    legacy_retirement_reason,
)


@pytest.mark.asyncio
async def test_retired_fresh_quality_cannot_admit_a_write_or_project_residuals(
    tmp_path: Path,
) -> None:
    """The retired entry refuses before it can admit a write or project quality."""
    assert legacy_retirement_reason()

    await assert_legacy_session_entry_is_closed(
        session_id="retired-fresh-quality-continuation",
        workspace=str(tmp_path),
    )

    assert not list(tmp_path.iterdir())
