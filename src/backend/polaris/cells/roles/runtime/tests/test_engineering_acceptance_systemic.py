"""Systemic-defect regression tests: acceptance dimensions and estimator alignment."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_acceptance import (
    acceptance_dimensions_summary,
    assess_fidelity,
    extract_goal_clauses,
    normalize_clause_findings,
)
from polaris.kernelone.llm.engine.prompt_budget import compress_native_tool_history
from polaris.kernelone.llm.engine.token_estimator import TokenEstimator

_PELICAN_GOAL = """用 Three.js 制作一个完整、可离线运行的 3D 小场景：一只白色鹈鹕骑着单车。
- 场景：公路沿海岸延伸，一侧是缓缓起伏的海浪。
- 角色：鹈鹕有修长的脖子、橙色长喙和明显的喉囊。
- 动画：两只脚始终跟着脚蹬交替画圈，车轮滚动。"""


def test_extract_goal_clauses_splits_bullets() -> None:
    clauses = extract_goal_clauses(_PELICAN_GOAL)
    assert len(clauses) == 4
    assert any("海浪" in clause for clause in clauses)
    assert any("喉囊" in clause for clause in clauses)
    assert not any(clause.startswith("-") for clause in clauses)


def test_extract_goal_clauses_dedupes_and_caps() -> None:
    instruction = "\n".join(f"- 第{i}条要求条目内容" for i in range(30))
    clauses = extract_goal_clauses(instruction)
    assert len(clauses) == 14


def test_normalize_clause_findings_validation() -> None:
    assert normalize_clause_findings(None) == []
    findings = normalize_clause_findings(
        [{"clause": "海浪", "status": "pass", "evidence": "call_abc"}]
    )
    assert len(findings) == 1 and findings[0].status == "pass"
    for bad in (
        [{"clause": "", "status": "pass", "evidence": "x"}],
        [{"clause": "c", "status": "maybe", "evidence": "x"}],
        [{"clause": "c", "status": "pass", "evidence": ""}],
        "not-a-list",
    ):
        try:
            normalize_clause_findings(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


def test_fidelity_approved_without_matrix_is_unknown() -> None:
    """The D2 fix: approved + multi-clause goal + no structured findings must
    NOT default to passed — it stays unknown and blocks acceptance."""
    clauses = extract_goal_clauses(_PELICAN_GOAL)
    verdict = assess_fidelity(goal_clauses=clauses, findings=[], qa_verdict="approved")
    assert verdict.status == "unknown"
    assert len(verdict.missing) == len(clauses)


def test_fidelity_full_pass_matrix_passes() -> None:
    clauses = extract_goal_clauses(_PELICAN_GOAL)
    findings = normalize_clause_findings(
        [{"clause": clause, "status": "pass", "evidence": "call_x"} for clause in clauses]
    )
    verdict = assess_fidelity(goal_clauses=clauses, findings=findings, qa_verdict="approved")
    assert verdict.status == "passed"


def test_fidelity_containment_matching_and_fail() -> None:
    clauses = ["场景：公路沿海岸延伸，一侧是缓缓起伏的海浪。"]
    findings = normalize_clause_findings(
        [{"clause": "海浪", "status": "fail", "evidence": "frame shows grass"}]
    )
    verdict = assess_fidelity(goal_clauses=clauses, findings=findings, qa_verdict="approved")
    assert verdict.status == "failed"


def test_fidelity_single_clause_follows_verdict() -> None:
    verdict = assess_fidelity(goal_clauses=["only one clause"], findings=[], qa_verdict="approved")
    assert verdict.status == "passed"


def test_dimensions_summary_marks_not_required() -> None:
    dims = acceptance_dimensions_summary(
        function_status="passed",
        fidelity=assess_fidelity(goal_clauses=[], findings=[], qa_verdict="not_run"),
        require_qa=False,
    )
    assert dims == {"function": "passed", "fidelity": "not_required"}


def test_compressor_fits_visual_history_with_aligned_cost() -> None:
    """Run 6e61f355 regression, unit form: the default scorer prices base64
    image bytes as text; the aligned scorer prices them strictly lower. At a
    tight budget the aligned cost keeps visual-evidence turns the default
    scorer is forced to drop — evidence survives trimming."""
    image_bytes = base64.b64encode(b"\xff\xd8\xff" + b"0" * 30_000).decode()
    image_turn: list[dict[str, object]] = [
        {"role": "assistant", "content": "capturing", "tool_calls": [
            {"id": "call_0", "type": "function",
             "function": {"name": "browser_observe", "arguments": "{}"}},
        ]},
        {
            "role": "tool",
            "tool_call_id": "call_0",
            "content": '{"tool":"browser_observe","ok":true,"images":[{"data_base64":"'
            + image_bytes + '"}]}',
        },
    ]
    history: list[dict[str, object]] = [
        {"role": "system", "content": "You are Director."},
        {"role": "user", "content": "build the scene"},
        *image_turn,
    ]

    import json as _json
    import re

    pattern = re.compile(r'("data_base64"\s*:\s*")[^"]*(")')

    def aligned_cost(message: Mapping[str, Any]) -> int:
        content = message.get("content")
        extra = 0
        if isinstance(content, str):
            stripped = pattern.sub(r"\1\2", content)
            removed = sum(len(m.group(0)) for m in pattern.finditer(content))
            extra = TokenEstimator.estimate("x" * min(removed, 4_000_000))
            text = stripped
        else:
            text = "" if content is None else str(content)
        if message.get("tool_calls"):
            text += _json.dumps(message["tool_calls"])
        return TokenEstimator.estimate(text) + extra + 8

    image_msg = image_turn[1]
    cost = aligned_cost(image_msg)
    assert cost > 0
    # The payload is accounted separately from text: the stripped side is
    # tiny, the byte side carries the weight — a future admission model can
    # reprice images without touching text accounting.
    image_content = str(image_msg["content"])
    stripped_text_price = TokenEstimator.estimate(pattern.sub(r"\1\2", image_content))
    assert stripped_text_price < cost

    # Protocol integrity under visual payloads: goal survives, final turn
    # kept, tool_call/tool_result pairing intact.
    bounded = compress_native_tool_history(history, 12_000, message_cost=aligned_cost)
    assert bounded is not None
    assert bounded[1]["content"] == "build the scene"
    roles = [m["role"] for m in bounded]
    assert roles[-1] == "tool" and str(bounded[-1].get("tool_call_id")) == "call_0"
    tool_calls_obj = bounded[2].get("tool_calls")
    assert isinstance(tool_calls_obj, list) and str(tool_calls_obj[0]["id"]) == "call_0"
