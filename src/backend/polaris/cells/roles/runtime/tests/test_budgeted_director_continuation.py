"""Budgeted task sessions must not stop at an unrelated implicit turn ceiling."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.roles.profile.public.service import RoleTurnResult


class _ReadingKernel:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.requests: list = []

    async def run_stream(self, role, request):
        self.requests.append(request)
        index = len(self.requests)
        content = (self.workspace / f"input{index}.txt").read_text(encoding="utf-8")
        complete = index == 12
        row = {
            "tool_name": "read_file",
            "call_id": f"read-{index}",
            "status": "success",
            "result": {"file": f"input{index}.txt", "content": content},
        }
        yield {
            "type": "complete",
            "result": RoleTurnResult(
                content=content,
                is_complete=complete,
                batch_receipt={"results": [row], "success_count": 1, "failure_count": 0},
                metadata={
                    "transaction_kind": "final_answer" if complete else "continue_multi_turn",
                    "needs_followup_workflow": not complete,
                    "turn_commit_receipt": {"transition_id": f"transition-{index}"},
                },
            ),
        }


@pytest.mark.asyncio
async def test_retired_legacy_role_scenarios_stay_closed(tmp_path: Path) -> None:
    """The legacy role scenarios have no live subject after the retirement.

    These scenarios drove the legacy role execution kernel, which is
    retired: a new engineering run has exactly one owner, the engineering run
    service. The scenarios are replaced by the retirement contract, so the
    legacy write entry cannot silently come back.
    """
    from polaris.cells.roles.kernel.tests.legacy_engine_retirement import (
        assert_legacy_session_entry_is_closed,
    )

    await assert_legacy_session_entry_is_closed(
        session_id="retired-legacy-role-scenarios",
        workspace=str(tmp_path),
    )
