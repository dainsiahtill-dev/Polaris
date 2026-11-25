from __future__ import annotations

import asyncio
import json
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
                        "clause": clauses[0],
                        "status": "fail",
                        "evidence": "已读 src/core/brake.ts：只有接口没有距离断言；npm test 通过但无该用例。",
                    },
                    {
                        "clause": clauses[1],
                        "status": "unknown",
                        "evidence": "已读 src/core/moon.ts：亮度对照缺失，尚未定位。",
                    },
                ],
            },
        )
    )
    assert result.ok
    assert reviewer.calls and reviewer.calls[0][0] is False


def test_an_all_unknown_matrix_is_not_a_rejection(tmp_path) -> None:
    """实测 L1-01：QA 在"刚开始读源码"时交了一份 9 条全 unknown 的矩阵。

    那不是裁决。宿主当成终局拒绝，整轮停在 unknown。
    """

    clauses = ("支持刹车", "月相亮度", "重置复现")
    tools, reviewer = _tools(tmp_path, clauses)
    result = asyncio.run(
        tools.execute(
            "qa-1",
            "report_review",
            {
                "approved": False,
                "feedback": "Exploration turn: no approval decision yet.",
                "major_findings": [],
                "clause_findings": [
                    {"clause": str(index), "status": "unknown", "evidence": "Investigation in progress."}
                    for index in range(1, 4)
                ],
            },
        )
    )
    assert not result.ok
    assert reviewer.calls == []


def test_numbered_clause_references_cover_the_matrix(tmp_path) -> None:
    """A small reviewer must be able to reference clauses by number."""

    clauses = ("条款一：支持刹车", "条款二：月相只改亮度")
    tools, reviewer = _tools(tmp_path, clauses)
    result = asyncio.run(
        tools.execute(
            "qa-1",
            "report_review",
            {
                "approved": False,
                "feedback": "缺少亮度对照",
                "major_findings": ["缺少亮度对照"],
                "clause_findings": [
                    {"clause": "1", "status": "unknown", "evidence": "已读 src/core/brake.ts：接口存在但无距离断言。"},
                    {"clause": "2", "status": "fail", "evidence": "已跑 npm test：无月相亮度对照用例；dist 构建通过。"},
                ],
            },
        )
    )
    assert result.ok
    assert reviewer.calls and reviewer.calls[0][0] is False


def test_out_of_range_clause_reference_is_host_completed(tmp_path) -> None:
    """越界/未匹配的标签不再让整份矩阵被拒：没覆盖到的条款由宿主补全并点名。

    实测旧行为：9 次 report_review 里 7 次被"缺条款"拒回，QA 53 轮仍交不出裁决。
    """

    clauses = ("条款一：支持刹车", "条款二：月相只改亮度")
    tools, _reviewer = _tools(tmp_path, clauses)
    result = asyncio.run(
        tools.execute(
            "qa-1",
            "report_review",
            {
                "approved": False,
                "feedback": "条款一未查到对应实现",
                "major_findings": ["条款一未查到对应实现"],
                "clause_findings": [
                    {"clause": "9", "status": "unknown", "evidence": "已读 src/core/brake.ts：未见对应实现。"},
                    {"clause": "2", "status": "unknown", "evidence": "已读 src/core/moon.ts：未见亮度对照。"},
                ],
            },
        )
    )
    assert result.ok
    payload = json.loads(result.content)
    assert [item["clause"] for item in payload["host_completed_clauses"]] == ["条款一：支持刹车"]


def test_an_all_unknown_matrix_is_a_progress_report_not_a_verdict(tmp_path) -> None:
    """实测 L1-01：QA 交了 9 条全 unknown 的"探索轮"，宿主当成终局裁决。

    拒绝之后必须退回让它继续取证，否则整轮停在 unknown。
    """

    tools, reviewer = _tools(tmp_path, ("支持刹车", "月相亮度", "重置复现"))
    result = asyncio.run(
        tools.execute(
            "qa-1",
            "report_review",
            {
                "approved": False,
                "feedback": "Exploration turn: no approval decision yet",
                "major_findings": [],
                "clause_findings": [
                    {"clause": str(index), "status": "unknown", "evidence": "Investigation in progress."}
                    for index in range(1, 4)
                ],
            },
        )
    )
    assert not result.ok
    assert reviewer.calls == []


def test_a_no_decision_report_is_sent_back_for_a_real_ruling(tmp_path) -> None:
    """实测 L1-01：QA 交了一份全 unknown 的"Exploration turn / No approval decision"。

    那不是裁决，是进度汇报。宿主当成终局 → project_outcome=unknown。
    """

    tools, reviewer = _tools(tmp_path, ("支持刹车", "月相亮度", "重置复现"))
    result = asyncio.run(
        tools.execute(
            "qa-1",
            "report_review",
            {
                "approved": False,
                "feedback": "Exploration turn: no approval decision yet.",
                "major_findings": [],
                "clause_findings": [
                    {"clause": str(index), "status": "unknown", "evidence": "Investigation in progress; no evidence collected yet."}
                    for index in range(1, 4)
                ],
            },
        )
    )
    assert not result.ok
    assert reviewer.calls == []


def test_pending_browser_verification_is_a_placeholder(tmp_path) -> None:
    """实测 4a157380 L1-01：新批次仍交了一份全 unknown、证据写"Pending browser
    verification of the delivered page." 的报告，被当成终局 → 整轮 interrupted。
    """

    tools, reviewer = _tools(tmp_path, ("支持刹车", "月相亮度"))
    result = asyncio.run(
        tools.execute(
            "qa-1",
            "report_review",
            {
                "approved": False,
                "feedback": "Beginning QA review of the candidate.",
                "major_findings": [],
                "clause_findings": [
                    {"clause": "1", "status": "unknown", "evidence": "Pending browser verification of the delivered page."},
                    {"clause": "2", "status": "unknown", "evidence": "Pending source and browser verification of controls."},
                ],
            },
        )
    )
    assert not result.ok
    assert reviewer.calls == []


def test_a_bare_receipt_is_evidence_even_with_pending_wording(tmp_path) -> None:
    """回执优先于措辞表：真实引用里出现 "pending" 不该被误判成占位。"""

    tools, reviewer = _tools(tmp_path, ("支持刹车",))
    result = asyncio.run(
        tools.execute(
            "qa-1",
            "report_review",
            {
                "approved": False,
                "feedback": "重置后 pending 状态未清空",
                "major_findings": ["重置后 pending 状态未清空"],
                "clause_findings": [
                    {"clause": "支持刹车", "status": "fail", "evidence": "verify-1"},
                ],
            },
        )
    )
    assert result.ok
    assert reviewer.calls and reviewer.calls[0][0] is False
