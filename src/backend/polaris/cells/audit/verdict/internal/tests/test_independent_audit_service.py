"""Tests for polaris.cells.audit.verdict.internal.independent_audit_service."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from polaris.cells.audit.verdict.internal.independent_audit_service import (
    AuditContext,
    AuditVerdict,
    IndependentAuditService,
)


class TestAuditVerdict:
    """AuditVerdict dataclass tests."""

    def test_is_pass_true_when_accepted_is_true(self) -> None:
        verdict = AuditVerdict(accepted=True, raw_output="output", summary="summary")
        assert verdict.is_pass is True
        assert verdict.is_fail is False
        assert verdict.is_inconclusive is False

    def test_is_fail_true_when_accepted_is_false(self) -> None:
        verdict = AuditVerdict(accepted=False, raw_output="output", summary="summary")
        assert verdict.is_pass is False
        assert verdict.is_fail is True
        assert verdict.is_inconclusive is False

    def test_is_inconclusive_true_when_accepted_is_none(self) -> None:
        verdict = AuditVerdict(accepted=None, raw_output="output", summary="summary")
        assert verdict.is_pass is False
        assert verdict.is_fail is False
        assert verdict.is_inconclusive is True

    def test_to_dict_contains_all_fields(self) -> None:
        verdict = AuditVerdict(
            accepted=True,
            raw_output="some output",
            summary="test summary",
            findings=["finding1", "finding2"],
            defect_ticket={"id": "DEF001"},
            provider_info={"model": "test-model"},
        )
        result = verdict.to_dict()
        assert result["accepted"] is True
        assert result["is_pass"] is True
        assert result["is_fail"] is False
        assert result["summary"] == "test summary"
        assert result["findings"] == ["finding1", "finding2"]
        assert result["defect_ticket"] == {"id": "DEF001"}
        assert result["provider_info"] == {"model": "test-model"}
        assert "timestamp" in result
        assert "raw_output_preview" in result

    def test_raw_output_preview_truncated(self) -> None:
        long_output = "x" * 1000
        verdict = AuditVerdict(accepted=True, raw_output=long_output)
        result = verdict.to_dict()
        assert len(result["raw_output_preview"]) == 500


class TestAuditContext:
    """AuditContext dataclass tests."""

    def test_basic_context_creation(self) -> None:
        context = AuditContext(task_id="task-123", plan_text="Test plan")
        assert context.task_id == "task-123"
        assert context.plan_text == "Test plan"
        assert context.step == 0
        assert context.changed_files == []

    def test_full_context_creation(self) -> None:
        context = AuditContext(
            task_id="task-456",
            plan_text="Plan",
            memory_summary="Memory",
            target_note="Note",
            changed_files=["a.py", "b.py"],
            planner_output="Planner output",
            executor_output="Executor output",
            tool_results="Tool results",
            reviewer_summary="Review summary",
            patch_risk_summary="Risk summary",
            step=1,
            run_id="run-789",
        )
        assert context.task_id == "task-456"
        assert len(context.changed_files) == 2
        assert context.step == 1
        assert context.run_id == "run-789"


class TestIndependentAuditService:
    """IndependentAuditService tests."""

    @pytest.mark.asyncio
    async def test_no_llm_caller_returns_inconclusive(self) -> None:
        service = IndependentAuditService(llm_caller=None)
        context = AuditContext(task_id="test-task")
        verdict = await service.run_audit(context)
        assert verdict.is_inconclusive
        assert "No LLM caller configured" in verdict.raw_output

    @pytest.mark.asyncio
    async def test_llm_call_failure_returns_inconclusive(self) -> None:
        def failing_caller(role: str, prompt: str) -> tuple[str, dict[str, str]]:
            raise RuntimeError("LLM connection failed")

        service = IndependentAuditService(llm_caller=failing_caller)
        context = AuditContext(task_id="test-task")
        verdict = await service.run_audit(context)
        assert verdict.is_inconclusive
        assert "failed" in verdict.summary.lower()

    @pytest.mark.asyncio
    async def test_successful_pass_verdict(self) -> None:
        def pass_caller(role: str, prompt: str) -> tuple[str, dict[str, str]]:
            return '{"acceptance":"PASS","summary":"All good","findings":[]}', {"model": "test"}

        service = IndependentAuditService(llm_caller=pass_caller)
        context = AuditContext(task_id="test-task")
        verdict = await service.run_audit(context)
        assert verdict.is_pass
        assert verdict.summary == "All good"

    @pytest.mark.asyncio
    async def test_successful_fail_verdict(self) -> None:
        def fail_caller(role: str, prompt: str) -> tuple[str, dict[str, str]]:
            return '{"acceptance":"FAIL","summary":"Issues found","findings":["Issue 1"]}', {"model": "test"}

        service = IndependentAuditService(llm_caller=fail_caller)
        context = AuditContext(task_id="test-task")
        verdict = await service.run_audit(context)
        assert verdict.is_fail
        assert verdict.summary == "Issues found"
        assert verdict.findings == ["Issue 1"]

    @pytest.mark.asyncio
    async def test_json_wrapped_in_markdown_parsed(self) -> None:
        def md_caller(role: str, prompt: str) -> tuple[str, dict[str, str]]:
            return '```json\n{"acceptance":"PASS","summary":"OK","findings":[]}\n```', {"model": "test"}

        service = IndependentAuditService(llm_caller=md_caller)
        context = AuditContext(task_id="test-task")
        verdict = await service.run_audit(context)
        assert verdict.is_pass

    @pytest.mark.asyncio
    async def test_inconclusive_verdict_retried(self) -> None:
        call_count = [0]

        def retry_caller(role: str, prompt: str) -> tuple[str, dict[str, str]]:
            call_count[0] += 1
            if call_count[0] == 1:
                return '{"acceptance":"MAYBE","summary":"Unclear"}', {"model": "test"}
            return '{"acceptance":"PASS","summary":"Final decision","findings":[]}', {"model": "test"}

        service = IndependentAuditService(llm_caller=retry_caller)
        context = AuditContext(task_id="test-task")
        verdict = await service.run_audit(context, max_retries=2)
        assert verdict.is_pass
        assert call_count[0] == 2

    @pytest.mark.asyncio
    async def test_build_audit_prompt_includes_changed_files(self) -> None:
        async def capture_caller(role: str, prompt: str) -> tuple[str, dict[str, str]]:
            assert "a.py" in prompt
            assert "b.py" in prompt
            return '{"acceptance":"PASS","summary":"OK","findings":[]}', {}

        service = IndependentAuditService(llm_caller=capture_caller)  # type: ignore[arg-type]
        context = AuditContext(
            task_id="test-task",
            changed_files=["a.py", "b.py"],
            executor_output="Some output",
        )
        await service.run_audit(context)

    @pytest.mark.asyncio
    async def test_build_audit_prompt_includes_evidence_summary(self) -> None:
        mock_evidence = MagicMock()
        mock_evidence.file_changes = ["f1.py"]
        mock_evidence.tool_outputs = ["output1"]
        mock_evidence.verification_results = ["result1"]
        mock_evidence.has_critical_issues.return_value = False

        async def capture_caller(role: str, prompt: str) -> tuple[str, dict[str, str]]:
            assert "Evidence Summary" in prompt
            assert "File changes: 1" in prompt
            return '{"acceptance":"PASS","summary":"OK","findings":[]}', {}

        service = IndependentAuditService(llm_caller=capture_caller)  # type: ignore[arg-type]
        context = AuditContext(
            task_id="test-task",
            evidence_package=mock_evidence,
        )
        await service.run_audit(context)

    @pytest.mark.asyncio
    async def test_audit_history_tracked(self) -> None:
        def simple_caller(role: str, prompt: str) -> tuple[str, dict[str, str]]:
            return '{"acceptance":"PASS","summary":"OK","findings":[]}', {"model": "test"}

        service = IndependentAuditService(llm_caller=simple_caller)
        context = AuditContext(task_id="test-task")
        await service.run_audit(context)
        await service.run_audit(context)

        history = service.get_audit_history()
        assert len(history) == 2

    def test_get_stats_empty(self) -> None:
        service = IndependentAuditService()
        stats = service.get_stats()
        assert stats["total"] == 0
        assert stats["pass"] == 0
        assert stats["fail"] == 0
        assert stats["inconclusive"] == 0

    @pytest.mark.asyncio
    async def test_get_stats_with_results(self) -> None:
        def pass_caller(role: str, prompt: str) -> tuple[str, dict[str, str]]:
            return '{"acceptance":"PASS","summary":"OK","findings":[]}', {}

        def fail_caller(role: str, prompt: str) -> tuple[str, dict[str, str]]:
            return '{"acceptance":"FAIL","summary":"Bad","findings":[]}', {}

        def inconclusive_caller(role: str, prompt: str) -> tuple[str, dict[str, str]]:
            return "random text", {}

        service = IndependentAuditService()
        context = AuditContext(task_id="t1")

        # Patch the llm_caller dynamically
        service._llm_caller = pass_caller  # type: ignore[method-assign]
        await service.run_audit(context)
        service._llm_caller = fail_caller  # type: ignore[method-assign]
        await service.run_audit(context)
        service._llm_caller = inconclusive_caller  # type: ignore[method-assign]
        await service.run_audit(context)

        stats = service.get_stats()
        assert stats["total"] == 3
        assert stats["pass"] == 1
        assert stats["fail"] == 1
        assert stats["inconclusive"] == 1
        assert stats["pass_rate"] == pytest.approx(1 / 3)

    def test_parse_verdict_empty_output(self) -> None:
        service = IndependentAuditService()
        verdict = service._parse_verdict("", {"model": "test"})
        assert verdict.is_inconclusive
        assert verdict.raw_output == ""

    def test_parse_verdict_alternate_true_values(self) -> None:
        service = IndependentAuditService()
        for value in ("TRUE", "YES", "APPROVED"):
            verdict = service._parse_verdict(f'{{"acceptance":"{value}","summary":"","findings":[]}}', {})
            assert verdict.is_pass, f"Failed for {value}"

    def test_parse_verdict_alternate_false_values(self) -> None:
        service = IndependentAuditService()
        for value in ("FALSE", "NO", "REJECTED"):
            verdict = service._parse_verdict(f'{{"acceptance":"{value}","summary":"","findings":[]}}', {})
            assert verdict.is_fail, f"Failed for {value}"

    def test_parse_verdict_fallback_to_text_search(self) -> None:
        service = IndependentAuditService()
        verdict = service._parse_verdict("RESULT: PASS", {})
        assert verdict.is_pass

    def test_parse_verdict_findings_normalized_from_string(self) -> None:
        service = IndependentAuditService()
        verdict = service._parse_verdict('{"acceptance":"PASS","summary":"","findings":"single finding"}', {})
        assert verdict.findings == ["single finding"]

    def test_parse_verdict_findings_filtered(self) -> None:
        service = IndependentAuditService()
        verdict = service._parse_verdict('{"acceptance":"PASS","summary":"","findings":[null,"", "valid"]}', {})
        assert verdict.findings == ["valid"]

    def test_mentions_missing_evidence_true(self) -> None:
        service = IndependentAuditService()
        context = AuditContext(task_id="t1", planner_output="some output", executor_output="more output")
        assert service._mentions_missing_evidence("planner输出为空", context) is True

    def test_mentions_missing_evidence_false_no_evidence(self) -> None:
        service = IndependentAuditService()
        context = AuditContext(task_id="t1", planner_output="", executor_output="")
        assert service._mentions_missing_evidence("planner输出为空", context) is False

    def test_mentions_missing_evidence_false_no_marker(self) -> None:
        service = IndependentAuditService()
        context = AuditContext(task_id="t1", planner_output="some output")
        assert service._mentions_missing_evidence("everything is fine", context) is False

    def test_defect_ticket_extracted(self) -> None:
        service = IndependentAuditService()
        verdict = service._parse_verdict(
            '{"acceptance":"FAIL","summary":"","findings":[],"defect_ticket":{"id":"BUG-1"}}',
            {},
        )
        assert verdict.defect_ticket == {"id": "BUG-1"}
