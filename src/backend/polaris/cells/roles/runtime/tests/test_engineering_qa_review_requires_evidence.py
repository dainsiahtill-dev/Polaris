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


def test_placeholder_rejection_is_sent_back(tmp_path) -> None:
    """A canned 'review not started' is an unstarted review, not a finding."""

    clauses = ("条款一：支持刹车", "条款二：月相只改亮度")
    tools, reviewer = _tools(tmp_path, clauses)
    result = asyncio.run(
        tools.execute(
            "qa-1",
            "report_review",
            {
                "approved": False,
                "feedback": "Initial placeholder pending source review and runtime verification.",
                "major_findings": ["Placeholder - review not yet performed"],
                "clause_findings": [
                    {"clause": clause, "status": "unknown", "evidence": "Review not started."}
                    for clause in clauses
                ],
            },
        )
    )
    assert not result.ok
    assert "placeholder" in result.content
    assert reviewer.calls == []


def test_substantive_unknown_evidence_is_accepted(tmp_path) -> None:
    clauses = ("条款一：支持刹车", "条款二：月相只改亮度")
    tools, reviewer = _tools(tmp_path, clauses)
    result = asyncio.run(
        tools.execute(
            "qa-1",
            "report_review",
            {
                "approved": False,
                "feedback": "刹车距离没有断言",
                "major_findings": ["缺少刹车距离断言"],
                "clause_findings": [
                    {
                        "clause": clause,
                        "status": "unknown",
                        "evidence": "已读 src/core/brake.ts：只有接口没有距离断言；npm test 通过但无该用例。",
                    }
                    for clause in clauses
                ],
            },
        )
    )
    assert result.ok
    assert reviewer.calls and reviewer.calls[0][0] is False
