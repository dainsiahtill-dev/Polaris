"""Guards the context_projection_matrix eval suite (deterministic, no LLM)."""

from __future__ import annotations

import pytest
from polaris.cells.llm.evaluation.internal.context_projection_matrix import (
    run_context_projection_matrix_suite,
)


@pytest.mark.asyncio
async def test_projection_matrix_all_pass_no_leaks() -> None:
    result = await run_context_projection_matrix_suite({}, "m", "director", workspace=".")
    assert result["ok"] is True
    summary = result["details"]["summary"]
    assert summary["failed_cases"] == 0
    assert summary["total_cases"] >= 8
    assert summary["control_plane_leaks_total"] == 0
    # receipt offloading must demonstrate real token (char) savings
    assert summary["receipt_chars_saved_total"] > 5000


@pytest.mark.asyncio
async def test_projection_matrix_covers_content_leak_and_signal_safety() -> None:
    result = await run_context_projection_matrix_suite({}, "m", "director", workspace=".")
    cases = {c["case"]: c for c in result["details"]["cases"]}
    # the content-level control-plane leak fix must be exercised and passing
    assert cases["content_level_control_plane_stripped"]["passed"] is True
    # signal-role (user/assistant) content must remain verbatim
    assert cases["signal_role_content_untouched"]["passed"] is True
