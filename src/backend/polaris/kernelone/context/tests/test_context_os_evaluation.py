from __future__ import annotations

import pytest
from polaris.kernelone.context.context_os import (
    CodeContextDomainAdapter,
    ContextOSQualityCase,
    ContextOSRolloutGatePolicy,
    StateFirstContextOS,
    evaluate_context_os_rollout_gate,
    evaluate_context_os_suite,
)


@pytest.mark.asyncio
async def test_context_os_quality_suite_reports_recovery_metrics() -> None:
    engine = StateFirstContextOS(domain_adapter=CodeContextDomainAdapter())
    projection = await engine.project(
        messages=[
            {
                "role": "user",
                "content": "Fix polaris/kernelone/context/session_continuity.py and preserve context.engine behavior on 2026-03-26.",
                "sequence": 1,
            },
            {
                "role": "assistant",
                "content": "Decision: keep SessionContinuityEngine as facade and continue the refactor.",
                "sequence": 2,
            },
            {
                "role": "tool",
                "content": "```python\nfrom polaris.kernelone.context.session_continuity import SessionContinuityEngine\n```",
                "sequence": 3,
            },
        ],
        recent_window_messages=2,
    )
    artifact_id = projection.snapshot.artifact_store[0].artifact_id

    summary = evaluate_context_os_suite(
        projection.snapshot,
        [
            ContextOSQualityCase(
                case_id="fact",
                query="session_continuity.py",
                expected_fact_contains="session_continuity.py",
            ),
            ContextOSQualityCase(
                case_id="state",
                expected_state_path="task_state.current_goal",
                expected_state_contains="session_continuity.py",
                expected_open_loop_contains="continue the refactor",
                expected_decision_contains="keep SessionContinuityEngine as facade",
            ),
            ContextOSQualityCase(
                case_id="artifact",
                expected_artifact_id=artifact_id,
                expected_artifact_contains="SessionContinuityEngine",
                expected_temporal_contains="2026-03-26",
            ),
            ContextOSQualityCase(
                case_id="abstain",
                query="zzqxjv_missing_needle_42",
                expect_no_results=True,
            ),
        ],
        engine=engine,
    )

    assert summary.total_cases == 4
    assert summary.exact_fact_recovery >= 0.5
    assert summary.decision_preservation >= 0.5
    assert summary.open_loop_continuity >= 0.5
    assert summary.artifact_restore_precision >= 0.5
    assert summary.temporal_update_correctness >= 0.5
    assert summary.abstention >= 1.0
    assert summary.compaction_regret <= 0.5

    gate = evaluate_context_os_rollout_gate(
        summary,
        policy=ContextOSRolloutGatePolicy(
            min_cases=4,
            min_exact_fact_recovery=0.5,
            min_decision_preservation=0.5,
            min_open_loop_continuity=0.5,
            min_artifact_restore_precision=0.5,
            min_temporal_update_correctness=0.5,
            min_abstention=1.0,
            max_compaction_regret=0.5,
            promote_to_mode="mainline",
        ),
    )
    assert gate.passed is True
    assert gate.recommended_mode == "mainline"
    assert gate.failures == ()


@pytest.mark.asyncio
async def test_context_os_rollout_gate_reports_threshold_failures() -> None:
    engine = StateFirstContextOS(domain_adapter=CodeContextDomainAdapter())
    projection = await engine.project(
        messages=[
            {
                "role": "user",
                "content": "Track only a weak hint.",
                "sequence": 1,
            }
        ],
        recent_window_messages=1,
    )
    summary = evaluate_context_os_suite(
        projection.snapshot,
        [
            ContextOSQualityCase(
                case_id="failing",
                expected_state_path="task_state.current_goal",
                expected_state_contains="missing-target",
            )
        ],
        engine=engine,
    )

    gate = evaluate_context_os_rollout_gate(
        summary,
        policy=ContextOSRolloutGatePolicy(
            min_cases=1,
            min_exact_fact_recovery=1.0,
            min_decision_preservation=1.0,
            min_open_loop_continuity=1.0,
            min_artifact_restore_precision=1.0,
            min_temporal_update_correctness=1.0,
            min_abstention=1.0,
            max_compaction_regret=0.0,
        ),
    )
    assert gate.passed is False
    assert gate.recommended_mode == "shadow"
    assert any(item.metric == "exact_fact_recovery" for item in gate.failures)


class TestAttentionRuntimeReportGenerator:
    """Tests for Attention Runtime evaluation report generation."""

    def test_attention_runtime_eval_suite_from_mapping(self) -> None:
        """Test suite loading from dict."""
        from polaris.kernelone.context.context_os.evaluation import (
            AttentionRuntimeEvalSuite,
        )

        payload = {
            "version": 1,
            "suite_id": "test_suite_001",
            "description": "Test suite for report generation",
            "cases": [
                {
                    "case_id": "case_1",
                    "conversation": [
                        {"role": "user", "content": "请帮我实现登录功能"},
                        {"role": "assistant", "content": "需要我帮你实现吗？"},
                        {"role": "user", "content": "需要"},
                    ],
                    "expected_pending_followup_status": "confirmed",
                },
            ],
        }

        suite = AttentionRuntimeEvalSuite.from_mapping(payload)
        assert suite is not None
        assert suite.suite_id == "test_suite_001"
        assert len(suite.cases) == 1
        assert suite.cases[0].case_id == "case_1"

    def test_attention_runtime_eval_suite_to_dict(self) -> None:
        """Test suite serialization to dict."""
        from polaris.kernelone.context.context_os.evaluation import (
            AttentionRuntimeEvalSuite,
        )

        suite = AttentionRuntimeEvalSuite(
            version=1,
            suite_id="test_001",
            description="Test",
            cases=(),
        )
        d = suite.to_dict()
        assert d["suite_id"] == "test_001"
        assert d["version"] == 1

    @pytest.mark.asyncio
    async def test_generate_attention_runtime_report(self) -> None:
        """Test report generation from suite."""
        from polaris.kernelone.context.context_os.evaluation import (
            AttentionRuntimeEvalSuite,
            AttentionRuntimeQualityCase,
            generate_attention_runtime_report,
        )

        suite = AttentionRuntimeEvalSuite(
            version=1,
            suite_id="test_report_001",
            description="Test report generation",
            cases=(
                AttentionRuntimeQualityCase(
                    case_id="case_1",
                    conversation=[
                        {"role": "user", "content": "请帮我实现登录功能"},
                        {"role": "assistant", "content": "需要我帮你实现吗？"},
                        {"role": "user", "content": "需要"},
                    ],
                    expected_pending_followup_status="confirmed",
                ),
            ),
        )

        report = await generate_attention_runtime_report(suite)
        assert report is not None
        assert report.suite_id == "test_report_001"
        assert report.total_cases == 1
        assert report.generated_at != ""
        assert "attention_summary" in report.to_dict()

    def test_validate_attention_runtime_report_schema(self) -> None:
        """Test report schema validation."""
        from polaris.kernelone.context.context_os.evaluation import (
            validate_attention_runtime_report_schema,
        )

        # Valid report
        valid_report = {
            "version": 1,
            "suite_id": "test_001",
            "generated_at": "2026-03-27T00:00:00+00:00",
            "total_cases": 10,
            "passed_cases": 9,
            "failed_cases": 1,
            "pass_rate": 0.9,
            "attention_summary": {
                "total_cases": 10,
                "pass_rate": 0.9,
                "intent_carryover_accuracy": 0.95,
                "latest_turn_retention_rate": 1.0,
                "focus_regression_rate": 0.1,
                "false_clear_rate": 0.0,
                "pending_followup_resolution_rate": 1.0,
                "seal_while_pending_rate": 0.0,
                "continuity_focus_alignment_rate": 0.8,
                "context_redundancy_rate": 0.05,
            },
            "case_results": [],
            "failures": [],
        }

        is_valid, errors = validate_attention_runtime_report_schema(valid_report)
        assert is_valid is True
        assert len(errors) == 0

        # Invalid report - missing fields
        invalid_report = {
            "version": 1,
            "suite_id": "test_001",
            # Missing: generated_at, total_cases, etc.
        }

        is_valid, errors = validate_attention_runtime_report_schema(invalid_report)
        assert is_valid is False
        assert len(errors) > 0
        assert any("generated_at" in e for e in errors)

    def test_validate_attention_runtime_report_schema_ranges(self) -> None:
        """Test report schema validation for metric ranges."""
        from polaris.kernelone.context.context_os.evaluation import (
            validate_attention_runtime_report_schema,
        )

        # Invalid report - out of range values
        invalid_report = {
            "version": 1,
            "suite_id": "test_001",
            "generated_at": "2026-03-27T00:00:00+00:00",
            "total_cases": 10,
            "passed_cases": 9,
            "failed_cases": 1,
            "pass_rate": 1.5,  # Invalid: > 1.0
            "attention_summary": {
                "total_cases": 10,
                "pass_rate": 1.5,  # Invalid: > 1.0
                "intent_carryover_accuracy": 0.95,
                "latest_turn_retention_rate": 1.0,
                "focus_regression_rate": 0.1,
                "false_clear_rate": 0.0,
                "pending_followup_resolution_rate": 1.0,
                "seal_while_pending_rate": 0.0,
                "continuity_focus_alignment_rate": 0.8,
                "context_redundancy_rate": 1.5,  # Invalid: > 1.0
            },
            "case_results": [],
            "failures": [],
        }

        is_valid, errors = validate_attention_runtime_report_schema(invalid_report)
        assert is_valid is False
        assert any("0 and 1" in e for e in errors)
