"""Prompt contract composition tests for tool-calling matrix benchmark."""

from __future__ import annotations

import pytest
from polaris.cells.llm.evaluation.internal.tool_calling_matrix import (
    CASES_ROOT,
    ToolCallingMatrixCase,
    _compose_case_prompt,
    _normalize_judge_args,
    load_builtin_tool_calling_matrix_cases,
    load_tool_calling_matrix_case,
)


def _make_case(*, judge: dict[str, object]) -> ToolCallingMatrixCase:
    return ToolCallingMatrixCase(
        case_id="case_demo",
        level="L3",
        role="director",
        title="demo",
        prompt="Read and edit a file.",
        judge=judge,
    )


def test_compose_case_prompt_appends_contract_for_stream() -> None:
    case = _make_case(
        judge={
            "stream": {
                "required_tools": ["repo_read_head", "append_to_file"],
                "forbidden_tools": ["execute_command"],
                "min_tool_calls": 2,
                "max_tool_calls": 3,
                "ordered_tool_groups": [["repo_read_head"], ["append_to_file"]],
            }
        }
    )

    prompt = _compose_case_prompt(case, mode="stream")

    assert "[Benchmark Tool Contract]" in prompt
    assert "repo_read_head" in prompt
    assert "append_to_file" in prompt
    assert "execute_command" in prompt
    assert "between 2 and 3" in prompt
    assert "[repo_read_head] -> [append_to_file]" in prompt


def test_compose_case_prompt_respects_non_stream_mode_spec() -> None:
    case = _make_case(
        judge={
            "stream": {"required_tools": ["repo_tree"]},
            "non_stream": {"required_tools": ["repo_rg"], "min_tool_calls": 1},
        }
    )

    prompt = _compose_case_prompt(case, mode="non_stream")

    assert "repo_rg" in prompt
    assert "repo_tree" not in prompt
    assert ">= 1" in prompt


def test_compose_case_prompt_returns_original_when_mode_missing() -> None:
    case = _make_case(judge={"stream": {"required_tools": ["repo_tree"]}})

    prompt = _compose_case_prompt(case, mode="non_stream")

    assert prompt == case.prompt


def test_compose_case_prompt_includes_safety_refusal_contract() -> None:
    case = _make_case(
        judge={
            "stream": {
                "require_no_tool_calls": True,
                "require_refusal": True,
                "required_refusal_markers": ["不允许", "forbidden"],
            }
        }
    )

    prompt = _compose_case_prompt(case, mode="stream")

    assert "Do not call any tools for this case." in prompt
    assert "Any tool call is a hard failure for this case" in prompt
    assert "Provide an explicit safety refusal" in prompt
    assert "不允许" in prompt
    assert "forbidden" in prompt


def test_compose_case_prompt_includes_required_any_and_output_tokens() -> None:
    case = _make_case(
        judge={
            "stream": {
                "required_any_tools": [["repo_rg", "search_code"], ["search_replace", "precision_edit"]],
                "required_output_substrings": ["DEBUG = False"],
                "min_tool_calls": 3,
            }
        }
    )

    prompt = _compose_case_prompt(case, mode="stream")

    assert "Required tool groups:" in prompt
    assert "repo_rg" in prompt
    assert "search_replace" in prompt
    assert "DEBUG = False" in prompt
    assert "Do not stop after the first tool result" in prompt
    assert "must emit at least 3 tool calls in the same batch" in prompt
    assert "emit all required native tool calls in one batch" in prompt


def test_compose_case_prompt_does_not_infer_verification_when_forbidden() -> None:
    case = _make_case(
        judge={
            "stream": {
                "required_any_tools": [["repo_read_head", "read_file"]],
                "forbidden_tools": ["execute_command"],
                "required_output_substrings": ["DEBUG", "API_HOST"],
                "min_tool_calls": 2,
            }
        }
    )

    prompt = _compose_case_prompt(case, mode="stream")

    assert "Forbidden tools: execute_command." in prompt
    assert "complete required verification/tool steps" not in prompt
    assert "include them after completing required tool steps" in prompt


def test_compose_case_prompt_adds_read_search_write_contract_for_repo_rg() -> None:
    case = _make_case(
        judge={
            "stream": {
                "required_tools": ["repo_rg", "search_replace"],
                "min_tool_calls": 2,
            }
        }
    )

    prompt = _compose_case_prompt(case, mode="stream")

    assert "Read/search + write contract detected" in prompt
    assert "Discovery-only batches are invalid for this case" in prompt
    assert "读取后必须继续发出写入/编辑调用" in prompt


def test_compose_case_prompt_includes_required_tool_equivalence_hints() -> None:
    case = _make_case(
        judge={
            "stream": {
                "required_tools": ["repo_rg", "search_replace"],
                "min_tool_calls": 2,
            }
        }
    )

    prompt = _compose_case_prompt(case, mode="stream")

    assert "Equivalent tools accepted:" in prompt
    assert "search_replace ->" in prompt
    assert "precision_edit" in prompt


def test_normalize_judge_args_strips_dot_slash_for_repo_tree() -> None:
    normalized = _normalize_judge_args("repo_tree", {"path": "./backend/"})
    assert normalized["path"] == "backend"


class TestBridgeCaseLoading:
    """Loading validation for ADR-0080 bridge cases."""

    BRIDGE_CASE_IDS = [
        "bridge_wm_prefers_read_over_search",
        "bridge_emits_session_patch",
        "bridge_verifying_prefers_test",
        "bridge_uses_failure_context",
    ]

    @pytest.mark.parametrize("case_id", BRIDGE_CASE_IDS)
    def test_bridge_case_json_file_exists(self, case_id: str) -> None:
        path = CASES_ROOT / f"{case_id}.json"
        assert path.exists(), f"Bridge case fixture missing: {path}"

    @pytest.mark.parametrize("case_id", BRIDGE_CASE_IDS)
    def test_bridge_case_loads_successfully(self, case_id: str) -> None:
        path = CASES_ROOT / f"{case_id}.json"
        case = load_tool_calling_matrix_case(path)
        assert case.case_id == case_id
        assert case.role == "director"
        assert case.level == "L5"
        assert case.critical is True
        assert case.weight == 1.0
        assert "adr-0080" in case.tags

    @pytest.mark.parametrize("case_id", BRIDGE_CASE_IDS)
    def test_bridge_case_has_judge_with_stream_and_non_stream(self, case_id: str) -> None:
        path = CASES_ROOT / f"{case_id}.json"
        case = load_tool_calling_matrix_case(path)
        judge = case.judge
        assert "stream" in judge, f"{case_id}: missing stream judge"
        assert "non_stream" in judge, f"{case_id}: missing non_stream judge"
        assert "score_threshold" in judge

    def test_all_bridge_cases_discoverable_via_builtin_loader(self) -> None:
        cases = load_builtin_tool_calling_matrix_cases(case_ids=self.BRIDGE_CASE_IDS)
        loaded_ids = {c.case_id for c in cases}
        assert loaded_ids == set(self.BRIDGE_CASE_IDS)

    def test_bridge_cases_filter_by_prefix(self) -> None:
        cases = load_builtin_tool_calling_matrix_cases(case_ids=["bridge_"])
        loaded_ids = {c.case_id for c in cases}
        assert set(self.BRIDGE_CASE_IDS).issubset(loaded_ids)

    def test_bridge_wm_prefers_read_over_search_structure(self) -> None:
        case = load_tool_calling_matrix_case(CASES_ROOT / "bridge_wm_prefers_read_over_search.json")
        stream = case.judge["stream"]
        assert stream.get("required_tools") == ["read_file"]
        assert "repo_rg" in stream.get("forbidden_tools", [])
        assert "repo_tree" in stream.get("forbidden_tools", [])
        assert stream.get("first_tool") == "read_file"

    def test_bridge_emits_session_patch_structure(self) -> None:
        case = load_tool_calling_matrix_case(CASES_ROOT / "bridge_emits_session_patch.json")
        stream = case.judge["stream"]
        assert stream.get("require_no_tool_calls") is True
        assert "<SESSION_PATCH>" in stream.get("required_output_substrings", [])
        non_stream = case.judge["non_stream"]
        assert non_stream.get("require_no_tool_calls") is True

    def test_bridge_verifying_prefers_test_structure(self) -> None:
        case = load_tool_calling_matrix_case(CASES_ROOT / "bridge_verifying_prefers_test.json")
        stream = case.judge["stream"]
        assert "execute_command" in stream.get("required_tools", [])
        assert "repo_rg" in stream.get("forbidden_tools", [])

    def test_bridge_uses_failure_context_structure(self) -> None:
        case = load_tool_calling_matrix_case(CASES_ROOT / "bridge_uses_failure_context.json")
        stream = case.judge["stream"]
        assert stream.get("required_tools") == ["read_file"]
        assert "<SESSION_PATCH>" in stream.get("required_output_substrings", [])
        # required_any_tools should be a list of lists
        any_tools = stream.get("required_any_tools", [])
        assert len(any_tools) == 1
        assert "search_replace" in any_tools[0]

    def test_bridge_cases_prompt_contains_working_memory_zones(self) -> None:
        """Continuation-prompt bridge cases embed the 4-zone XML structure.

        bridge_emits_session_patch is exempt: it tests pure output formatting,
        not continuation prompt injection.
        """
        for case_id in self.BRIDGE_CASE_IDS:
            if case_id == "bridge_emits_session_patch":
                continue
            case = load_tool_calling_matrix_case(CASES_ROOT / f"{case_id}.json")
            assert "<Goal>" in case.prompt, f"{case_id}: missing <Goal> zone"
            assert "</Goal>" in case.prompt, f"{case_id}: missing </Goal>"
            assert "<WorkingMemory>" in case.prompt, f"{case_id}: missing <WorkingMemory>"
            assert "</WorkingMemory>" in case.prompt, f"{case_id}: missing </WorkingMemory>"
            assert "<Instruction>" in case.prompt, f"{case_id}: missing <Instruction>"
            assert "</Instruction>" in case.prompt, f"{case_id}: missing </Instruction>"

    def test_bridge_cases_parity_required(self) -> None:
        for case_id in self.BRIDGE_CASE_IDS:
            case = load_tool_calling_matrix_case(CASES_ROOT / f"{case_id}.json")
            parity = case.judge.get("parity", {})
            assert parity.get("required") is True, f"{case_id}: parity.required must be True"

    def test_bridge_cases_workspace_fixture_set(self) -> None:
        for case_id in self.BRIDGE_CASE_IDS:
            case = load_tool_calling_matrix_case(CASES_ROOT / f"{case_id}.json")
            assert case.workspace_fixture, f"{case_id}: workspace_fixture is required"
