from __future__ import annotations

import json

from polaris.cells.roles.runtime.internal.engineering_evidence_graph import (
    evidence_graph_projection,
    pending_evidence_obligations,
    required_probe,
)


def test_obligations_direct_qa_to_missing_readme_and_dom_evidence() -> None:
    instruction = (
        "实现花园页面。\n"
        "README需说清月相映射与步长。\n"
        "输入错误显示在原便签旁，点击完成后显示完成标记。\n"
        "核心测试应核对情绪、亮度与重置。"
    )
    pending = pending_evidence_obligations(instruction, [])
    assert [item["required_probe"] for item in pending] == [
        "browser_dom_query",
        "read_file:README.md",
        "browser_replay_and_dom_query",
        "machine_check",
    ]


def test_successful_clause_matrix_closes_only_proven_obligations() -> None:
    instruction = "实现页面。\nREADME需说清步长。"
    report = {
        "approved": False,
        "clause_findings": [{"clause": "实现页面。", "status": "pass", "evidence": "obs-1"}],
    }
    events = [
        {
            "kind": "tool_observed",
            "payload": {"tool": "report_review", "ok": True, "content": json.dumps(report)},
        }
    ]
    projection = evidence_graph_projection(instruction, events)
    assert projection["total_clause_count"] == 2
    assert [item["clause"] for item in projection["pending"]] == ["README需说清步长。"]
    assert projection["pending"][0]["status"] == "unknown"


def test_markdown_table_layout_is_not_an_obligation() -> None:
    instruction = "| 输入／操作 | 预期结果 |\n| --- | --- |\n| 点击按钮 | 显示完成标记 |"
    assert required_probe("README需说清月相映射") == "read_file:README.md"
    projection = evidence_graph_projection(instruction, [])
    assert projection["total_clause_count"] == 1
    assert projection["pending"][0]["clause"] == "| 点击按钮 | 显示完成标记 |"
