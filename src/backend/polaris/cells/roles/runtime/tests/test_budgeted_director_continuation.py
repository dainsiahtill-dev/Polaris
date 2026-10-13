"""Budgeted task sessions must not stop at an unrelated implicit turn ceiling."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.roles.profile.public.service import RoleTurnResult
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
from polaris.cells.roles.runtime.tests.test_kernel_session_continuation import _install_runtime_boundary


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
@pytest.mark.parametrize("explicit_limit,expected", [(None, "ok"), (3, "failed")])
async def test_budgeted_session_retains_explicit_limit_only(tmp_path, monkeypatch, explicit_limit, expected):
    for index in range(1, 13):
        (tmp_path / f"input{index}.txt").write_text(f"evidence-{index}", encoding="utf-8")
    kernel = _ReadingKernel(tmp_path)
    service = _install_runtime_boundary(monkeypatch, tmp_path, kernel)
    policy = {"enabled": True}
    if explicit_limit is not None:
        policy["max_auto_turns"] = explicit_limit
    command = ExecuteRoleSessionCommandV1(
        role="director",
        workspace=str(tmp_path),
        session_id="budgeted-read",
        host_kind="director_adapter",
        task_id="TASK-READ",
        user_message="Read each task evidence file to finish the task",
        timeout_seconds=20,
        context={"continuous_execution": policy},
    )
    result = await service.execute_role_session(command)
    assert result.status == expected
    assert len(kernel.requests) == (12 if explicit_limit is None else explicit_limit)
