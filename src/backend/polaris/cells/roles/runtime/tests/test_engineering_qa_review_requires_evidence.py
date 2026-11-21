from __future__ import annotations

import asyncio
import time

from polaris.cells.roles.runtime.internal.engineering_roles import register_review_tool
from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RunLimits


class _Review:
    def __init__(self) -> None:
        self.calls: list[tuple[bool, list[object]]] = []

    def __call__(self, approved: bool, findings: list[object]) -> None:
        self.calls.append((approved, findings))


def _tools(workspace, review_clauses: tuple[str, ...]) -> tuple[EngineeringTools, _Review]:
    (workspace / ".polaris").mkdir(parents=True, exist_ok=True)
    register_review_tool()
    reviewer = _Review()
    tools = EngineeringTools(
        workspace,
        PermissionScope((workspace,), (workspace,), frozenset({"report_review"}), False),
        RunLimits(time.time() + 15, 10_000, 2_000),
        asyncio.Event(),
        review=reviewer,
        review_clauses=review_clauses,
    )
    return tools, reviewer


def test_rejection_without_clause_findings_is_sent_back(tmp_path) -> None:
    tools, reviewer = _tools(tmp_path, ("支持刹车",))
    result = asyncio.run(
        tools.execute(
            "qa-1",
            "report_review",
            {"approved": False, "feedback": "刚开始检查", "major_findings": [], "clause_findings": []},
        )
    )
    assert not result.ok
    assert "cannot reject the candidate before reviewing" in result.content
    assert reviewer.calls == []


def test_rejection_without_evidence_is_sent_back(tmp_path) -> None:
    tools, reviewer = _tools(tmp_path, ("支持刹车",))
    result = asyncio.run(
        tools.execute(
            "qa-1",
            "report_review",
            {
                "approved": False,
                "feedback": "未完成审查",
                "major_findings": [],
                "clause_findings": [{"clause": "支持刹车", "status": "fail", "evidence": ""}],
            },
        )
    )
    assert not result.ok
    assert "evidence" in result.content
    assert reviewer.calls == []


def test_rejection_with_evidence_is_accepted(tmp_path) -> None:
    tools, reviewer = _tools(tmp_path, ("支持刹车",))
    result = asyncio.run(
        tools.execute(
            "qa-1",
            "report_review",
            {
                "approved": False,
                "feedback": "刹车无效",
                "major_findings": ["刹车无效"],
                "clause_findings": [{"clause": "支持刹车", "status": "fail", "evidence": "verify-1"}],
            },
        )
    )
    assert result.ok
    assert reviewer.calls and reviewer.calls[0][0] is False
