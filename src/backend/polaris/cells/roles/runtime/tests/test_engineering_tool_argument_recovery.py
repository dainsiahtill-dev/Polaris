"""Host-side recovery for malformed tool arguments (real-run failure taxonomy).

Live runs across 55 bench workspaces showed 320 host rejections and 86% of
``report_review`` calls failing on shapes the host could absorb. These tests pin
the normalizations without weakening the fail-closed boundaries.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from polaris.cells.roles.runtime.internal.engineering_acceptance import (
    clause_coverage_gap,
    extract_goal_clauses,
    normalize_clause_findings,
)
from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
from polaris.cells.roles.runtime.internal.engineering_verification import command_may_write
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RunLimits


def _writer_tools(workspace: Path) -> EngineeringTools:
    (workspace / ".polaris").mkdir(parents=True, exist_ok=True)
    return EngineeringTools(
        workspace,
        PermissionScope((workspace,), (workspace,), frozenset({"read_file", "write_file", "edit_file"}), False),
        RunLimits(None, 100_000, 16_000),
        asyncio.Event(),
    )


def test_extract_goal_clauses_drops_lead_ins_and_normalizes_checkboxes() -> None:
    instruction = (
        "先固定这些实验判定：\n"
        "| 输入／操作 | 预期结果 |\n"
        "| --- | --- |\n"
        "| 种子7、同布局运行10秒 | 重置重跑位置与闪光序列一致； |\n"
        "交付时请逐项核验：\n"
        "- [ ] 全0卡使用中性盘面，不除零；\n"
        "- [x] 负值输入明确拒绝。\n"
    )
    clauses = extract_goal_clauses(instruction)
    assert "先固定这些实验判定：" not in clauses
    assert "交付时请逐项核验：" not in clauses
    assert "| 输入／操作 | 预期结果 |" not in clauses
    assert "| 种子7、同布局运行10秒 | 重置重跑位置与闪光序列一致； |" in clauses
    assert "全0卡使用中性盘面，不除零；" in clauses
    assert "负值输入明确拒绝。" in clauses


def test_clause_references_accept_numbers_ranges_and_enumeration_markers() -> None:
    clauses = ["甲要求", "乙要求", "丙要求", "丁要求", "戊要求"]
    findings = normalize_clause_findings(
        [
            {"clause": "1", "status": "pass", "evidence": "call_a"},
            {"clause": "2-3", "status": "pass", "evidence": "call_b"},
            {"clause": "C4", "status": "fail", "evidence": "call_c"},
            {"clause": "条款5", "status": "unknown", "evidence": "call_d"},
        ]
    )
    assert clause_coverage_gap(clauses, findings) == []
    assert clause_coverage_gap(clauses, findings[:3]) == ["戊要求"]


def test_clause_reference_refuses_ambiguous_or_invented_labels() -> None:
    clauses = ["Open detail view", "Open settings view"]
    findings = normalize_clause_findings([{"clause": "Open", "status": "pass", "evidence": "call_a"}])
    assert clause_coverage_gap(clauses, findings) == clauses
    invented = normalize_clause_findings(
        [{"clause": "something else entirely", "status": "pass", "evidence": "call_a"}]
    )
    assert clause_coverage_gap(clauses, invented) == clauses
    # An enumeration marker is not a guess: "C2" names the second clause.
    enumerated = normalize_clause_findings([{"clause": "C2 settings", "status": "pass", "evidence": "call_a"}])
    assert clause_coverage_gap(clauses, enumerated) == ["Open detail view"]


def _review_tools(workspace: Path, clauses: tuple[str, ...]) -> EngineeringTools:
    from polaris.cells.roles.runtime.internal.engineering_roles import register_review_tool

    (workspace / ".polaris").mkdir(parents=True, exist_ok=True)
    register_review_tool()
    return EngineeringTools(
        workspace,
        PermissionScope((workspace,), (), frozenset({"read_file", "report_review"}), False),
        RunLimits(None, 100_000, 16_000),
        asyncio.Event(),
        review=lambda approved, findings: None,
        review_clauses=clauses,
    )


def test_report_review_accepts_numeric_and_enumeration_matrix(tmp_path: Path) -> None:
    tools = _review_tools(tmp_path, ("clause one", "clause two", "clause three"))
    observation = asyncio.run(
        tools.execute(
            "call_numbers",
            "report_review",
            {
                "approved": True,
                "feedback": "every clause observed",
                "clause_findings": [
                    {"clause": "1", "status": "pass", "evidence": "call_x"},
                    {"clause": "C2", "status": "pass", "evidence": "call_y"},
                    {"clause": "3", "status": "pass", "evidence": "call_z"},
                ],
            },
        )
    )
    assert observation.ok is True
    payload = json.loads(observation.content)
    assert payload["clause_match"] == "reference"
    assert [item["clause"] for item in payload["clause_findings"]] == ["1", "C2", "3"]


def test_partial_matrix_is_sent_back_twice_then_graded(tmp_path: Path) -> None:
    """The coverage gate corrects, it does not loop until the QA budget is gone."""
    tools = _review_tools(tmp_path, ("clause one", "clause two", "clause three"))
    partial = {
        "approved": True,
        "feedback": "best effort",
        "clause_findings": [{"clause": "1", "status": "pass", "evidence": "call_x"}],
    }
    for attempt in (1, 2):
        observation = asyncio.run(tools.execute(f"call_partial_{attempt}", "report_review", dict(partial)))
        assert observation.ok is False
        assert "cover every acceptance clause" in observation.content
    final = asyncio.run(tools.execute("call_partial_3", "report_review", dict(partial)))
    assert final.ok is True
    payload = json.loads(final.content)
    assert payload["clause_match"] == "partial"
    assert [item["clause"] for item in payload["clause_findings"]] == ["1"]


def test_write_file_accepts_path_alias_and_null_body(tmp_path: Path) -> None:
    tools = _writer_tools(tmp_path)
    aliased = asyncio.run(
        tools.execute("call_alias", "write_file", {"path": "src/a.ts", "content": "export const a = 1;\n"})
    )
    assert aliased.ok is True
    assert (tmp_path / "src/a.ts").read_text(encoding="utf-8") == "export const a = 1;\n"

    null_body = asyncio.run(tools.execute("call_null", "write_file", {"file": "src/b.ts", "content": None}))
    assert null_body.ok is True
    assert (tmp_path / "src/b.ts").read_text(encoding="utf-8") == ""
    assert "content was omitted" in null_body.content


def test_edit_file_zero_hit_returns_closest_lines(tmp_path: Path) -> None:
    tools = _writer_tools(tmp_path)
    asyncio.run(tools.execute("call_seed", "write_file", {"file": "src/c.ts", "content": "alpha\nbeta\ngamma\n"}))
    observation = asyncio.run(
        tools.execute(
            "call_miss",
            "edit_file",
            {"file": "src/c.ts", "search": "beta changed", "replace": "delta"},
        )
    )
    assert observation.ok is False
    assert "observed 0" in observation.content
    assert "closest current lines" in observation.content
    assert "2: beta" in observation.content


def test_command_may_write_is_conservative() -> None:
    for read_only in ("ls -la", "cat src/main.ts", "rg -n pattern", "find . -type f", "pwd"):
        assert command_may_write(read_only) is False, read_only
    for writer in (
        "rm -rf dist",
        "npm install",
        "npx tsc -p .",
        "echo x > out.txt",
        "cat a | tee b",
        "python -m pip install x",
        "sed -i s/a/b/ f",
    ):
        assert command_may_write(writer) is True, writer


def test_read_file_miss_names_the_closest_existing_paths(tmp_path: Path) -> None:
    """A miss on the wrong extension must not be a dead end."""

    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tools" / "serve.ts").write_text("export const root = '.';\n", encoding="utf-8")
    (tmp_path / "dist" / "tools").mkdir(parents=True, exist_ok=True)
    (tmp_path / "dist" / "tools" / "serve.js").write_text("export const root = '.';\n", encoding="utf-8")
    tools = _writer_tools(tmp_path)
    observation = asyncio.run(tools.execute("call_miss", "read_file", {"file": "tools/serve.js"}))
    assert observation.ok is False
    assert "tools/serve.js" in observation.content
    assert "tools/serve.ts" in observation.content
    assert "dist/tools/serve.js" in observation.content


def test_read_file_miss_without_candidates_stays_short(tmp_path: Path) -> None:
    tools = _writer_tools(tmp_path)
    observation = asyncio.run(tools.execute("call_miss", "read_file", {"file": "nothing/here.ts"}))
    assert observation.ok is False
    assert "nothing/here.ts" in observation.content
    assert "相近现有文件" not in observation.content
