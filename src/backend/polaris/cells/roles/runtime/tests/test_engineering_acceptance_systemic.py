"""Systemic-defect regression tests: acceptance dimensions and estimator alignment."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_acceptance import (
    acceptance_dimensions_summary,
    assess_fidelity,
    clause_coverage_gap,
    extract_goal_clauses,
    has_truncated_goal_clauses,
    normalize_clause_findings,
    require_observed_clause_evidence,
)
from polaris.cells.roles.runtime.internal.engineering_roles import interpret_review_reports, register_review_tool
from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RunLimits
from polaris.kernelone.llm.engine.prompt_budget import compress_native_tool_history
from polaris.kernelone.llm.engine.token_estimator import TokenEstimator
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

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
    instruction = "\n".join(f"- 第{i}条要求条目内容" for i in range(40))
    clauses = extract_goal_clauses(instruction)
    assert len(clauses) == 32


def test_normalize_clause_findings_validation() -> None:
    assert normalize_clause_findings(None) == []
    findings = normalize_clause_findings([{"clause": "海浪", "status": "pass", "evidence": "call_abc"}])
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


def test_native_prose_citation_requires_every_reference_to_be_current() -> None:
    import pytest

    identity = "call_6327ad35ab3b9f404b903214"
    checks = [{"call_id": identity, "tool": "execute_command", "ok": True, "exit_code": 0}]

    def validate(text: str) -> None:
        findings = normalize_clause_findings([{"clause": "File matches", "status": "pass", "evidence": text}])
        require_observed_clause_evidence(findings, events=[], checks=checks, frame_seq=10, report_turn_seq=20)

    validate(f"Current QA check {identity} (execute_command, exit 0) confirms the clause.")
    checks.append({"call_id": "verification-72-0", "tool": "execute_command", "ok": True, "exit_code": 0})
    validate(f"Current QA check {identity} and host verification-72-0 both passed.")
    for invalid in [
        f"x{identity}",
        f"{identity}-other",
        f"file/{identity}",
        f"{identity}.bad",
        f"{identity}, call_missing",
        f"{identity} " + "e" * 450 + " toolu_unknown",
        "output looks right",
    ]:
        with pytest.raises(ValueError):
            validate(invalid)


def test_fidelity_full_pass_matrix_passes() -> None:
    clauses = extract_goal_clauses(_PELICAN_GOAL)
    findings = normalize_clause_findings(
        [{"clause": clause, "status": "pass", "evidence": "call_x"} for clause in clauses]
    )
    verdict = assess_fidelity(goal_clauses=clauses, findings=findings, qa_verdict="approved")
    assert verdict.status == "passed"


def test_fidelity_containment_matching_and_fail() -> None:
    clauses = ["场景：公路沿海岸延伸，一侧是缓缓起伏的海浪。"]
    findings = normalize_clause_findings([{"clause": "海浪", "status": "fail", "evidence": "frame shows grass"}])
    verdict = assess_fidelity(goal_clauses=clauses, findings=findings, qa_verdict="approved")
    assert verdict.status == "failed"


def test_fidelity_single_clause_follows_verdict() -> None:
    verdict = assess_fidelity(goal_clauses=["only one clause"], findings=[], qa_verdict="approved")
    assert verdict.status == "passed"


def test_unavailable_clause_remains_unknown_through_report_and_fidelity() -> None:
    raw = [{"clause": "Review rendering", "status": "unknown", "evidence": "Renderer unavailable"}]
    result = assess_fidelity(
        goal_clauses=["Review rendering"], findings=normalize_clause_findings(raw), qa_verdict="approved"
    )
    assert (result.status, result.covered, result.missing) == ("unknown", 0, ("Review rendering",))
    for approved in (True, False):
        report = {
            "call_id": "review",
            "content": json.dumps({"approved": approved, "feedback": "Cannot assess", "clause_findings": raw}),
        }
        verdict, _, _ = interpret_review_reports([report])
        assert verdict == "unknown"


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
        {
            "role": "assistant",
            "content": "capturing",
            "tool_calls": [
                {"id": "call_0", "type": "function", "function": {"name": "browser_observe", "arguments": "{}"}},
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_0",
            "content": '{"tool":"browser_observe","ok":true,"images":[{"data_base64":"' + image_bytes + '"}]}',
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


def test_clause_coverage_gap_never_invents_coverage() -> None:
    findings = normalize_clause_findings(
        [
            {"clause": "海浪", "status": "pass", "evidence": "call_a"},
            {"clause": "喉囊", "status": "fail", "evidence": "call_b"},
        ]
    )
    gap = clause_coverage_gap(["场景：公路沿海岸延伸，一侧是缓缓起伏的海浪。", "喉囊", "太阳"], findings)
    # "海浪" covers the long clause by containment; "喉囊" is covered (and failing);
    # "太阳" has no finding and must stay in the gap.
    assert gap == ["太阳"]
    assert clause_coverage_gap([], findings) == []


def test_shared_fragment_cannot_certify_two_distinct_clauses() -> None:
    clauses = ["Open detail view", "Open settings view"]
    findings = normalize_clause_findings([{"clause": "Open", "status": "pass", "evidence": "read"}])
    assert clause_coverage_gap(clauses, findings) == clauses
    assert assess_fidelity(goal_clauses=clauses, findings=findings, qa_verdict="approved").status == "unknown"


def test_normal_markdown_sections_and_distinct_long_requirements_survive_extraction() -> None:
    prefix = "Read and validate input before proceeding. " * 4
    first, second = prefix + "Return results sorted.", prefix + "Reject unordered input."
    assert extract_goal_clauses(f"## Output\n{first}\n## Error behavior\n{second}") == [first, second]


def test_role_instructions_only_advertise_browser_on_a_visual_host() -> None:
    from polaris.cells.roles.runtime.internal.engineering_roles import role_instruction

    for role in ("director", "qa"):
        assert "browser_observe" not in role_instruction(role, visual=False)
        assert "browser_observe" in role_instruction(role, visual=True)


def test_report_review_schema_exposes_clause_matrix() -> None:
    """Production root cause (pelican-bike runs): the provider-facing
    report_review schema omitted clause_findings, so a function-calling QA
    never submitted the matrix and fidelity stayed unknown for every
    multi-clause goal. The channel must be visible in the schema, not only in
    the prose prompt."""
    register_review_tool()
    schema = ToolSpecRegistry.get_llm_schema("report_review")
    assert schema is not None
    parameters = schema["function"]["parameters"]
    clause_property = parameters["properties"]["clause_findings"]
    items = clause_property["items"]
    assert set(items["required"]) == {"clause", "status", "evidence"}
    assert items["properties"]["status"]["enum"] == ["pass", "fail", "unknown"]


def _review_tools(workspace: Path, clauses: tuple[str, ...]) -> EngineeringTools:
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


def test_multi_clause_review_surface_requires_matrix(tmp_path: Path) -> None:
    tools = _review_tools(tmp_path, ("clause one", "clause two"))
    schema = next(item for item in tools.schemas() if item["function"]["name"] == "report_review")
    assert "clause_findings" in schema["function"]["parameters"]["required"]


def test_single_clause_review_surface_keeps_matrix_optional(tmp_path: Path) -> None:
    tools = _review_tools(tmp_path, ("only clause",))
    schema = next(item for item in tools.schemas() if item["function"]["name"] == "report_review")
    assert "clause_findings" not in (schema["function"]["parameters"].get("required") or [])


def test_multi_clause_report_review_rejects_partial_matrix(tmp_path: Path) -> None:
    tools = _review_tools(tmp_path, ("clause one", "clause two"))
    observation = asyncio.run(
        tools.execute(
            "call_partial",
            "report_review",
            {
                "approved": True,
                "feedback": "looks fine",
                "clause_findings": [{"clause": "clause one", "status": "pass", "evidence": "call_x"}],
            },
        )
    )
    assert observation.ok is False
    assert "clause two" in observation.content


def test_multi_clause_report_review_accepts_full_matrix(tmp_path: Path) -> None:
    tools = _review_tools(tmp_path, ("clause one", "clause two"))
    observation = asyncio.run(
        tools.execute(
            "call_full",
            "report_review",
            {
                "approved": True,
                "feedback": "every clause observed",
                "clause_findings": [
                    {"clause": "clause one", "status": "pass", "evidence": "call_x"},
                    {"clause": "clause two", "status": "pass", "evidence": "call_y"},
                ],
            },
        )
    )
    assert observation.ok is True
    payload = json.loads(observation.content)
    assert [item["clause"] for item in payload["clause_findings"]] == ["clause one", "clause two"]
    tools = _review_tools(tmp_path, ("only clause",))
    observation = asyncio.run(tools.execute("call_single", "report_review", {"approved": True, "feedback": "observed"}))
    assert observation.ok is True


def test_extract_goal_clauses_truncation_is_flagged() -> None:
    short = "\n".join(f"- 第{i}条要求条目内容" for i in range(10))
    catalog_scale = "\n".join(f"- 第{i}条要求条目内容" for i in range(25))
    long = "\n".join(f"- 第{i}条要求条目内容" for i in range(40))
    assert has_truncated_goal_clauses(short) is False
    # The frozen catalog's largest real goal is 25 clauses; it must fit.
    assert has_truncated_goal_clauses(catalog_scale) is False
    assert has_truncated_goal_clauses(long) is True
    assert len(extract_goal_clauses(long)) == 32


def test_goal_clauses_use_frozen_user_requirements_only() -> None:
    """Fidelity is about the user's goal: the platform notice and the delivery
    contract sections must not become clauses QA has to rule on."""
    instruction = (
        "Use the current engineering_v2 execution loop for the frozen requirements below.\n"
        "Preserve every original business goal verbatim.\n\n"
        "--- Frozen original requirements (verbatim) ---\n"
        "# 项目需求 — 示例\n\n"
        "## 目标与使用体验\n"
        "观察问题：悲伤的花与兴奋的花能否跳出不同灯光舞蹈？\n"
        "| 输入／操作 | 预期结果 |\n"
        "| --- | --- |\n"
        "| 种子7、同布局运行10秒 | 重置重跑位置与闪光序列一致； |\n"
        "1. 准备输入。花朵有悲伤、平静、兴奋三档。\n\n"
        "## 交付与质量要求\n"
        "- 完整可运行的实现落盘到工作区根。\n"
        "- 必须提供至少一种真实可执行入口。\n\n"
        "## 工程深度要求\n"
        "- min_files: 3\n"
    )
    clauses = extract_goal_clauses(instruction)
    assert clauses == [
        "观察问题：悲伤的花与兴奋的花能否跳出不同灯光舞蹈？",
        "| 输入／操作 | 预期结果 |",
        "| 种子7、同布局运行10秒 | 重置重跑位置与闪光序列一致； |",
        "准备输入。花朵有悲伤、平静、兴奋三档。",
    ]
    assert has_truncated_goal_clauses(instruction) is False


def test_goal_clauses_non_markdown_fallback() -> None:
    instruction = "用 Three.js 制作一个 3D 小场景。\n- 场景：公路沿海岸延伸。\n- 角色：鹈鹕有修长的脖子。\n"
    assert extract_goal_clauses(instruction) == [
        "用 Three.js 制作一个 3D 小场景。",
        "场景：公路沿海岸延伸。",
        "角色：鹈鹕有修长的脖子。",
    ]


def test_fidelity_truncated_goal_stays_unknown_even_with_full_prefix() -> None:
    """A capped clause list cannot certify the whole goal: passing on the visible
    prefix would be exactly the self-deception the fidelity dimension exists to
    prevent."""
    instruction = "\n".join(f"- 第{i}条要求条目内容" for i in range(40))
    clauses = extract_goal_clauses(instruction)
    findings = normalize_clause_findings(
        [{"clause": clause, "status": "pass", "evidence": "call_x"} for clause in clauses]
    )
    verdict = assess_fidelity(
        goal_clauses=clauses,
        findings=findings,
        qa_verdict="approved",
        goal_truncated=has_truncated_goal_clauses(instruction),
    )
    assert verdict.status == "unknown"
    assert verdict.missing


def test_read_only_write_targets_names_build_output() -> None:
    """The read-only corrective note must name the path the check tried to write
    (a build output), not a generic dependency-install hint."""
    from polaris.cells.roles.runtime.internal.engineering_verification import is_read_only_failure
    from polaris.cells.roles.runtime.public.contracts.engineering_run import ToolObservation
    from polaris.cells.roles.runtime.public.engineering_service import read_only_write_targets

    checks = (
        ToolObservation(
            "verification-1-0",
            "execute_command",
            False,
            "stdout:\nerror TS5033: Could not write file '/ws/dist/main.js': EROFS: read-only file system, "
            "mkdir '/ws/dist'.\n",
            2,
            False,
        ),
    )
    assert read_only_write_targets(checks) == ["/ws/dist/main.js", "/ws/dist"]
    assert read_only_write_targets((ToolObservation("c2", "read_file", False, "missing", None, False),)) == []
    # Detection and path extraction are case-insensitive: coreutils prints
    # "Read-only file system" with a capital R and no EROFS token.
    capitalized = (
        ToolObservation(
            "verification-2-0",
            "execute_command",
            False,
            "stdout:\nstderr:\nmkdir: cannot create directory 'dist': Read-only file system\n",
            1,
            False,
        ),
    )
    assert read_only_write_targets(capitalized) == ["dist"]
    # npm reports a failed mkdir as ENOENT with a `syscall mkdir` line.
    npm_style = (
        ToolObservation(
            "verification-3-0",
            "execute_command",
            False,
            "npm ERR! code ENOENT\nnpm ERR! syscall mkdir\nnpm ERR! path /ws/node_modules\n",
            1,
            False,
        ),
    )
    assert is_read_only_failure(str(npm_style[0].content))
    assert read_only_write_targets(npm_style) == []
    assert not is_read_only_failure("AssertionError: expected 1 got 2")


def test_distilled_verification_failure_names_read_only_write() -> None:
    """A read-only write failure must not be reported as a code location to fix."""
    from polaris.cells.roles.runtime.internal.engineering_loop import _distill_verification_failure

    content = (
        "stdout:\nerror TS5033: Could not write file '/ws/dist/main.js': EROFS: read-only file system, "
        "mkdir '/ws/dist'.\n"
    )
    message = _distill_verification_failure(content)
    assert "read-only" in message and "/ws/dist" in message and "execute_command" in message
    code_failure = _distill_verification_failure("src/main.ts:12: error TS2304: Cannot find name 'x'")
    assert "src/main.ts" in code_failure


def test_report_review_fail_finding_with_approved_true_is_treated_as_rejected(tmp_path: Path) -> None:
    """A fail clause means the candidate is not acceptable; do not burn a QA turn
    rejecting the call when the model contradicts itself (measured 17 retries)."""
    tools = _review_tools(tmp_path, ("clause one", "clause two"))
    observation = asyncio.run(
        tools.execute(
            "call_contradiction",
            "report_review",
            {
                "approved": True,
                "feedback": "looks fine",
                "clause_findings": [
                    {"clause": "clause one", "status": "pass", "evidence": "call_x"},
                    {"clause": "clause two", "status": "fail", "evidence": "call_y"},
                ],
            },
        )
    )
    assert observation.ok is True
    assert json.loads(observation.content)["approved"] is False
