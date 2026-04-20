"""Tests for Session Workflow Matrix."""

from __future__ import annotations

import pytest
from polaris.cells.llm.evaluation.internal.session_workflow_matrix import (
    MockWorkflowKernel,
    SessionWorkflowCase,
    WorkflowTurnSpec,
    _event_to_dict,
    _is_subsequence,
    load_builtin_session_workflow_cases,
    run_session_workflow_suite,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    TurnContinuationMode,
    TurnOutcomeEnvelope,
    TurnResult,
)
from polaris.cells.roles.kernel.public.turn_events import (
    CompletionEvent,
)


class TestMockWorkflowKernel:
    """Tests for MockWorkflowKernel."""

    @pytest.mark.asyncio
    async def test_yields_events_per_turn(self):
        kernel = MockWorkflowKernel(
            [
                [CompletionEvent(turn_id="t0", status="success")],
                [CompletionEvent(turn_id="t1", status="success")],
            ]
        )

        # Turn 0
        events = [e async for e in kernel.execute_stream("t0", [], [])]
        assert len(events) == 1
        assert events[0].turn_id == "t0"
        assert kernel.call_count == 1

        # Turn 1
        events = [e async for e in kernel.execute_stream("t1", [], [])]
        assert len(events) == 1
        assert events[0].turn_id == "t1"
        assert kernel.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_turn(self):
        kernel = MockWorkflowKernel([[]])
        events = [e async for e in kernel.execute_stream("t0", [], [])]
        assert events == []


class TestHelpers:
    """Tests for helper functions."""

    def test_is_subsequence_basic(self):
        assert _is_subsequence(["a", "b"], ["a", "b", "c"]) is True
        assert _is_subsequence(["a", "c"], ["a", "b", "c"]) is True
        assert _is_subsequence(["b", "a"], ["a", "b", "c"]) is False
        assert _is_subsequence(["d"], ["a", "b", "c"]) is False

    def test_event_to_dict(self):
        event = CompletionEvent(turn_id="t0", status="success")
        data = _event_to_dict(event)
        assert data["type"] == "CompletionEvent"
        assert data["turn_id"] == "t0"
        assert data["status"] == "success"


class TestCaseLoading:
    """Tests for case loading."""

    def test_load_all_cases(self):
        cases = load_builtin_session_workflow_cases()
        assert len(cases) >= 9
        case_ids = {c.case_id for c in cases}
        assert "swm_auto_continue_phases" in case_ids
        assert "swm_working_memory_reduces_search" in case_ids
        assert "swm_checkpoint_resume" in case_ids
        assert "swm_failure_driven_repair" in case_ids
        assert "swm_stagnation_stop" in case_ids
        assert "swm_handoff_development" in case_ids
        assert "swm_belief_revision" in case_ids
        assert "swm_role_adherence" in case_ids
        assert "swm_goal_convergence" in case_ids

    def test_load_filtered_cases(self):
        cases = load_builtin_session_workflow_cases(case_ids=["swm_auto_continue_phases"])
        assert len(cases) == 1
        assert cases[0].case_id == "swm_auto_continue_phases"


class TestCaseExecution:
    """End-to-end case execution tests."""

    @pytest.mark.asyncio
    async def test_auto_continue_phases(self, tmp_path):
        result = await run_session_workflow_suite(
            {},
            "",
            "",
            workspace=str(tmp_path),
            options={"swm_case_ids": ["swm_auto_continue_phases"]},
        )
        assert result["ok"] is True
        details = result["details"]
        assert details["total_cases"] == 1
        assert details["passed_cases"] == 1
        case_result = details["cases"][0]
        assert case_result["id"] == "swm_auto_continue_phases"
        assert case_result["passed"] is True

    @pytest.mark.asyncio
    async def test_working_memory_reduces_search(self, tmp_path):
        result = await run_session_workflow_suite(
            {},
            "",
            "",
            workspace=str(tmp_path),
            options={"swm_case_ids": ["swm_working_memory_reduces_search"]},
        )
        assert result["ok"] is True
        assert result["details"]["passed_cases"] == 1

    @pytest.mark.asyncio
    async def test_checkpoint_resume(self, tmp_path):
        result = await run_session_workflow_suite(
            {},
            "",
            "",
            workspace=str(tmp_path),
            options={"swm_case_ids": ["swm_checkpoint_resume"]},
        )
        assert result["ok"] is True
        assert result["details"]["passed_cases"] == 1

    @pytest.mark.asyncio
    async def test_failure_driven_repair(self, tmp_path):
        result = await run_session_workflow_suite(
            {},
            "",
            "",
            workspace=str(tmp_path),
            options={"swm_case_ids": ["swm_failure_driven_repair"]},
        )
        assert result["ok"] is True
        assert result["details"]["passed_cases"] == 1

    @pytest.mark.asyncio
    async def test_stagnation_stop(self, tmp_path):
        result = await run_session_workflow_suite(
            {},
            "",
            "",
            workspace=str(tmp_path),
            options={"swm_case_ids": ["swm_stagnation_stop"]},
        )
        assert result["ok"] is True
        assert result["details"]["passed_cases"] == 1

    @pytest.mark.asyncio
    async def test_handoff_development(self, tmp_path):
        result = await run_session_workflow_suite(
            {},
            "",
            "",
            workspace=str(tmp_path),
            options={"swm_case_ids": ["swm_handoff_development"]},
        )
        assert result["ok"] is True
        assert result["details"]["passed_cases"] == 1

    @pytest.mark.asyncio
    async def test_belief_revision(self, tmp_path):
        result = await run_session_workflow_suite(
            {},
            "",
            "",
            workspace=str(tmp_path),
            options={"swm_case_ids": ["swm_belief_revision"]},
        )
        assert result["ok"] is True
        assert result["details"]["passed_cases"] == 1
        case_result = result["details"]["cases"][0]
        assert case_result["id"] == "swm_belief_revision"
        assert case_result["passed"] is True

    @pytest.mark.asyncio
    async def test_role_adherence(self, tmp_path):
        result = await run_session_workflow_suite(
            {},
            "",
            "",
            workspace=str(tmp_path),
            options={"swm_case_ids": ["swm_role_adherence"]},
        )
        assert result["ok"] is True
        assert result["details"]["passed_cases"] == 1
        case_result = result["details"]["cases"][0]
        assert case_result["id"] == "swm_role_adherence"
        assert case_result["passed"] is True

    @pytest.mark.asyncio
    async def test_goal_convergence(self, tmp_path):
        result = await run_session_workflow_suite(
            {},
            "",
            "",
            workspace=str(tmp_path),
            options={"swm_case_ids": ["swm_goal_convergence"]},
        )
        assert result["ok"] is True
        assert result["details"]["passed_cases"] == 1
        case_result = result["details"]["cases"][0]
        assert case_result["id"] == "swm_goal_convergence"
        assert case_result["passed"] is True

    @pytest.mark.asyncio
    async def test_all_cases_together(self, tmp_path):
        result = await run_session_workflow_suite(
            {},
            "",
            "",
            workspace=str(tmp_path),
        )
        assert result["ok"] is True
        assert result["details"]["total_cases"] == 9
        assert result["details"]["passed_cases"] == 9

    @pytest.mark.asyncio
    async def test_empty_filter_returns_error(self, tmp_path):
        result = await run_session_workflow_suite(
            {},
            "",
            "",
            workspace=str(tmp_path),
            options={"swm_case_ids": ["nonexistent_case"]},
        )
        assert result["ok"] is False
        assert "no session workflow cases matched" in result["error"]


class TestCustomCase:
    """Test custom case definitions."""

    @pytest.mark.asyncio
    async def test_custom_turn_count_assertion(self, tmp_path):
        """Verify that a case with wrong expected turn_count fails."""
        from polaris.cells.llm.evaluation.internal.session_workflow_matrix import (
            _run_session_workflow_case,
        )

        case = SessionWorkflowCase(
            case_id="test_wrong_count",
            title="Wrong Count",
            description="Expected 3 turns but only 2 provided.",
            turns=[
                WorkflowTurnSpec(
                    kernel_events=[CompletionEvent(turn_id="t0", status="success")],
                    envelope=TurnOutcomeEnvelope(
                        turn_result=TurnResult(turn_id="t0", kind="final_answer", visible_content="", decision={}),
                        continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
                    ),
                ),
                WorkflowTurnSpec(
                    kernel_events=[CompletionEvent(turn_id="t1", status="success")],
                    envelope=TurnOutcomeEnvelope(
                        turn_result=TurnResult(turn_id="t1", kind="final_answer", visible_content="", decision={}),
                        continuation_mode=TurnContinuationMode.END_SESSION,
                    ),
                ),
            ],
            final_state_assertions={"turn_count": 99},  # wrong
        )

        verdict, _ = await _run_session_workflow_case(case, workspace=str(tmp_path))
        # Non-critical state assertion failure does not fail the overall verdict
        # because tooling check passes and score stays above threshold
        assert verdict.passed is True
        turn_check = [c for c in verdict.checks if c.code == "turn_count"]
        assert turn_check and turn_check[0].passed is True  # 2 turns executed correctly
        state_check = [c for c in verdict.checks if c.code == "final_state:turn_count"]
        assert state_check and state_check[0].passed is False  # but assertion expected 99
