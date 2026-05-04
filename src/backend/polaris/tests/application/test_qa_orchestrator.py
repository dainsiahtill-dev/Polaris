"""Tests for polaris.application.orchestration.qa_orchestrator module.

Covers:
- QaOrchestrator: plan_audit, execute_review, compile_verdict, run_audit_lifecycle,
  query_verdict
- Error handling and edge cases
- Service resolution errors
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.application.orchestration.qa_orchestrator import (
    QaAuditConfig,
    QaAuditLifecycleResult,
    QaOrchestrator,
    QaOrchestratorError,
    QaReviewResult,
    QaVerdictResult,
)

if TYPE_CHECKING:
    pass


# =============================================================================
# Test fixtures
# =============================================================================


@pytest.fixture
def config() -> QaAuditConfig:
    """Create a basic QA audit config."""
    return QaAuditConfig(workspace="/tmp/test-workspace")


@pytest.fixture
def orchestrator(config: QaAuditConfig) -> QaOrchestrator:
    """Create a QaOrchestrator instance."""
    return QaOrchestrator(config)


@pytest.fixture
def mock_verdict() -> MagicMock:
    """Create a mock verdict result."""
    verdict = MagicMock()
    verdict.verdict = "PASS"
    verdict.audit_id = "audit-123"
    verdict.target = "test-task"
    verdict.issues = []
    verdict.metrics = {}
    verdict.timestamp = MagicMock(spec=["isoformat"], isoformat=MagicMock(return_value="2026-05-01T00:00:00"))
    return verdict


# =============================================================================
# Tests for QaOrchestrator construction
# =============================================================================


class TestQaOrchestratorInit:
    """Tests for QaOrchestrator initialization."""

    def test_init_stores_config(self, config: QaAuditConfig) -> None:
        """Verify config is stored correctly."""
        orch = QaOrchestrator(config)
        assert orch._config is config
        assert orch._workspace == "/tmp/test-workspace"

    def test_init_default_services_are_none(self, config: QaAuditConfig) -> None:
        """Verify lazy services are initially None."""
        orch = QaOrchestrator(config)
        assert orch._qa_service is None
        assert orch._audit_service is None

    def test_init_run_id_from_config(self, config: QaAuditConfig) -> None:
        """Verify run_id is extracted from config."""
        orch = QaOrchestrator(config)
        assert orch._run_id == ""


# =============================================================================
# Tests for plan_audit
# =============================================================================


class TestPlanAudit:
    """Tests for the plan_audit method."""

    def test_plan_audit_empty_task_id_raises(self, orchestrator: QaOrchestrator) -> None:
        """Verify empty task_id raises QaOrchestratorError."""
        with pytest.raises(QaOrchestratorError, match="task_id is required"):
            orchestrator.plan_audit(task_id="")

    def test_plan_audit_whitespace_task_id_raises(self, orchestrator: QaOrchestrator) -> None:
        """Verify whitespace-only task_id raises error."""
        with pytest.raises(QaOrchestratorError, match="task_id is required"):
            orchestrator.plan_audit(task_id="   ")

    def test_plan_audit_valid_task_id(self, orchestrator: QaOrchestrator) -> None:
        """Verify valid task_id returns plan."""
        plan = orchestrator.plan_audit(task_id="task-123")

        assert plan["task_id"] == "task-123"
        assert plan["workspace"] == "/tmp/test-workspace"
        assert "run_id" in plan
        assert "criteria" in plan
        assert "evidence_paths" in plan

    def test_plan_audit_strips_task_id_whitespace(self, orchestrator: QaOrchestrator) -> None:
        """Verify task_id whitespace is stripped."""
        plan = orchestrator.plan_audit(task_id="  task-456  ")
        assert plan["task_id"] == "task-456"

    def test_plan_audit_merges_criteria(self) -> None:
        """Verify criteria are merged from config and parameter."""
        config = QaAuditConfig(workspace="/tmp", criteria={"key1": "value1"})
        orch = QaOrchestrator(config)

        plan = orch.plan_audit(task_id="task", criteria={"key2": "value2"})

        assert plan["criteria"]["key1"] == "value1"
        assert plan["criteria"]["key2"] == "value2"

    def test_plan_audit_none_criteria_uses_config(self) -> None:
        """Verify None criteria uses config defaults."""
        config = QaAuditConfig(workspace="/tmp", criteria={"default": "value"})
        orch = QaOrchestrator(config)

        plan = orch.plan_audit(task_id="task", criteria=None)

        assert plan["criteria"]["default"] == "value"

    def test_plan_audit_evidence_paths_from_parameter(self, orchestrator: QaOrchestrator) -> None:
        """Verify evidence_paths from parameter overrides config."""
        plan = orchestrator.plan_audit(
            task_id="task",
            evidence_paths=("/path/from/param",),
        )

        assert "/path/from/param" in plan["evidence_paths"]

    def test_plan_audit_evidence_paths_filter_empty(self, orchestrator: QaOrchestrator) -> None:
        """Verify empty evidence paths are filtered."""
        plan = orchestrator.plan_audit(
            task_id="task",
            evidence_paths=("/valid/path", "", "  ", "/another/valid"),
        )

        assert "/valid/path" in plan["evidence_paths"]
        assert "" not in plan["evidence_paths"]
        assert "  " not in plan["evidence_paths"]
        assert "/another/valid" in plan["evidence_paths"]

    def test_plan_audit_none_evidence_paths(self, orchestrator: QaOrchestrator) -> None:
        """Verify None evidence_paths returns empty tuple."""
        plan = orchestrator.plan_audit(task_id="task", evidence_paths=None)
        assert plan["evidence_paths"] == ()

    def test_plan_audit_with_run_id(self) -> None:
        """Verify run_id is included in plan."""
        config = QaAuditConfig(workspace="/tmp", run_id="run-abc")
        orch = QaOrchestrator(config)

        plan = orch.plan_audit(task_id="task")

        assert plan["run_id"] == "run-abc"


# =============================================================================
# Tests for execute_review
# =============================================================================


class TestExecuteReview:
    """Tests for the execute_review method."""

    @pytest.mark.asyncio
    async def test_execute_review_pass(
        self,
        orchestrator: QaOrchestrator,
        mock_verdict: MagicMock,
    ) -> None:
        """Verify PASS verdict produces completed status."""
        mock_verdict.verdict = "PASS"

        with patch.object(orchestrator, "_get_qa_service") as mock_get:
            mock_service = MagicMock()
            mock_service.audit_task = AsyncMock(return_value=mock_verdict)
            mock_get.return_value = mock_service

            result = await orchestrator.execute_review(task_id="task-123")

            assert result.status == "completed"
            assert result.issue_count == 0
            assert result.review_id == "audit-123"

    @pytest.mark.asyncio
    async def test_execute_review_fail(
        self,
        orchestrator: QaOrchestrator,
        mock_verdict: MagicMock,
    ) -> None:
        """Verify FAIL verdict produces completed status."""
        mock_verdict.verdict = "FAIL"
        mock_verdict.issues = [{"message": "Issue 1"}, {"message": "Issue 2"}]

        with patch.object(orchestrator, "_get_qa_service") as mock_get:
            mock_service = MagicMock()
            mock_service.audit_task = AsyncMock(return_value=mock_verdict)
            mock_get.return_value = mock_service

            result = await orchestrator.execute_review(task_id="task-123")

            assert result.status == "completed"
            assert result.issue_count == 2
            assert len(result.findings) == 2

    @pytest.mark.asyncio
    async def test_execute_review_skipped_for_unknown_verdict(
        self,
        orchestrator: QaOrchestrator,
        mock_verdict: MagicMock,
    ) -> None:
        """Verify unknown verdict produces skipped status."""
        mock_verdict.verdict = "UNKNOWN"

        with patch.object(orchestrator, "_get_qa_service") as mock_get:
            mock_service = MagicMock()
            mock_service.audit_task = AsyncMock(return_value=mock_verdict)
            mock_get.return_value = mock_service

            result = await orchestrator.execute_review(task_id="task-123")

            assert result.status == "skipped"

    @pytest.mark.asyncio
    async def test_execute_review_handles_none_verdict(
        self,
        orchestrator: QaOrchestrator,
        mock_verdict: MagicMock,
    ) -> None:
        """Verify None verdict is handled gracefully."""
        mock_verdict.verdict = None

        with patch.object(orchestrator, "_get_qa_service") as mock_get:
            mock_service = MagicMock()
            mock_service.audit_task = AsyncMock(return_value=mock_verdict)
            mock_get.return_value = mock_service

            result = await orchestrator.execute_review(task_id="task-123")

            assert result.status == "skipped"

    @pytest.mark.asyncio
    async def test_execute_review_service_error(self, orchestrator: QaOrchestrator) -> None:
        """Verify service errors are wrapped correctly."""
        with patch.object(orchestrator, "_get_qa_service") as mock_get:
            mock_service = MagicMock()
            mock_service.audit_task = AsyncMock(side_effect=RuntimeError("boom"))
            mock_get.return_value = mock_service

            with pytest.raises(QaOrchestratorError) as exc_info:
                await orchestrator.execute_review(task_id="task-123")

            assert "QA review execution failed" in str(exc_info.value)
            assert exc_info.value.code == "qa_review_failed"

    @pytest.mark.asyncio
    async def test_execute_review_with_changed_files(
        self,
        orchestrator: QaOrchestrator,
        mock_verdict: MagicMock,
    ) -> None:
        """Verify changed_files are passed to service."""
        with patch.object(orchestrator, "_get_qa_service") as mock_get:
            mock_service = MagicMock()
            mock_service.audit_task = AsyncMock(return_value=mock_verdict)
            mock_get.return_value = mock_service

            await orchestrator.execute_review(
                task_id="task-123",
                task_subject="Test task",
                changed_files=["file1.py", "file2.py"],
            )

            mock_service.audit_task.assert_called_once()
            call_kwargs = mock_service.audit_task.call_args.kwargs
            assert call_kwargs["changed_files"] == ["file1.py", "file2.py"]


# =============================================================================
# Tests for compile_verdict
# =============================================================================


class TestCompileVerdict:
    """Tests for the compile_verdict method."""

    @pytest.mark.asyncio
    async def test_compile_verdict_normalizes_pass(self, orchestrator: QaOrchestrator, mock_verdict: MagicMock) -> None:
        """Verify PASS verdict is normalized correctly."""
        mock_verdict.verdict = "PASS"
        mock_verdict.details = {"summary": "All checks passed", "score": 1.0}

        with patch.object(orchestrator, "_get_audit_service") as mock_get:
            mock_service = MagicMock()
            mock_service.run_verdict = AsyncMock(return_value=mock_verdict)
            mock_get.return_value = mock_service

            review_result = QaReviewResult(
                review_id="r1",
                target="t",
                status="completed",
                findings=(),
            )

            result = await orchestrator.compile_verdict(
                review_result=review_result,
                task_id="task-123",
            )

            assert result.verdict == "PASS"

    @pytest.mark.asyncio
    async def test_compile_verdict_normalizes_fail(self, orchestrator: QaOrchestrator, mock_verdict: MagicMock) -> None:
        """Verify FAIL verdict is normalized correctly."""
        mock_verdict.verdict = "FAIL"
        mock_verdict.details = {"summary": "Checks failed", "score": 0.0}

        with patch.object(orchestrator, "_get_audit_service") as mock_get:
            mock_service = MagicMock()
            mock_service.run_verdict = AsyncMock(return_value=mock_verdict)
            mock_get.return_value = mock_service

            review_result = QaReviewResult(
                review_id="r1",
                target="t",
                status="completed",
            )

            result = await orchestrator.compile_verdict(
                review_result=review_result,
                task_id="task-123",
            )

            assert result.verdict == "FAIL"

    @pytest.mark.asyncio
    async def test_compile_verdict_normalizes_unexpected_verdict(
        self, orchestrator: QaOrchestrator, mock_verdict: MagicMock
    ) -> None:
        """Verify unexpected verdict is normalized to NEEDS_REVIEW."""
        mock_verdict.verdict = "UNKNOWN"
        mock_verdict.details = {}

        with patch.object(orchestrator, "_get_audit_service") as mock_get:
            mock_service = MagicMock()
            mock_service.run_verdict = AsyncMock(return_value=mock_verdict)
            mock_get.return_value = mock_service

            review_result = QaReviewResult(
                review_id="r1",
                target="t",
                status="completed",
            )

            result = await orchestrator.compile_verdict(
                review_result=review_result,
                task_id="task-123",
            )

            assert result.verdict == "NEEDS_REVIEW"

    @pytest.mark.asyncio
    async def test_compile_verdict_service_error(self, orchestrator: QaOrchestrator) -> None:
        """Verify service errors are wrapped correctly."""
        with patch.object(orchestrator, "_get_audit_service") as mock_get:
            mock_service = MagicMock()
            mock_service.run_verdict = AsyncMock(side_effect=RuntimeError("boom"))
            mock_get.return_value = mock_service

            review_result = QaReviewResult(
                review_id="r1",
                target="t",
                status="completed",
            )

            with pytest.raises(QaOrchestratorError) as exc_info:
                await orchestrator.compile_verdict(
                    review_result=review_result,
                    task_id="task-123",
                )

            assert "QA verdict compilation failed" in str(exc_info.value)
            assert exc_info.value.code == "qa_verdict_failed"

    @pytest.mark.asyncio
    async def test_compile_verdict_with_metadata(self, orchestrator: QaOrchestrator, mock_verdict: MagicMock) -> None:
        """Verify metadata is passed to verdict command."""
        mock_verdict.verdict = "PASS"
        mock_verdict.details = {"summary": "OK", "score": 1.0}

        with patch.object(orchestrator, "_get_audit_service") as mock_get:
            mock_service = MagicMock()
            mock_service.run_verdict = AsyncMock(return_value=mock_verdict)
            mock_get.return_value = mock_service

            review_result = QaReviewResult(
                review_id="r1",
                target="t",
                status="completed",
            )

            result = await orchestrator.compile_verdict(
                review_result=review_result,
                task_id="task-123",
                metadata={"extra": "data"},
            )

            assert result.verdict == "PASS"


# =============================================================================
# Tests for query_verdict
# =============================================================================


class TestQueryVerdict:
    """Tests for the query_verdict method."""

    @pytest.mark.asyncio
    async def test_query_verdict_success(self, orchestrator: QaOrchestrator) -> None:
        """Verify successful verdict query."""
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status = "complete"
        mock_response.verdict = "PASS"
        mock_response.details = {"summary": "OK"}
        mock_response.error_code = None
        mock_response.error_message = None

        with patch.object(orchestrator, "_get_audit_service") as mock_get:
            mock_service = MagicMock()
            mock_service.query_verdict = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_service

            result = await orchestrator.query_verdict(task_id="task-123")

            assert result["ok"] is True
            assert result["verdict"] == "PASS"
            assert result["status"] == "complete"

    @pytest.mark.asyncio
    async def test_query_verdict_with_no_task_id(self, orchestrator: QaOrchestrator) -> None:
        """Verify query works without task_id."""
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status = "no_results"
        mock_response.verdict = None
        mock_response.details = {}
        mock_response.error_code = None
        mock_response.error_message = None

        with patch.object(orchestrator, "_get_audit_service") as mock_get:
            mock_service = MagicMock()
            mock_service.query_verdict = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_service

            result = await orchestrator.query_verdict()

            assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_query_verdict_service_error(self, orchestrator: QaOrchestrator) -> None:
        """Verify service errors are wrapped correctly."""
        with patch.object(orchestrator, "_get_audit_service") as mock_get:
            mock_service = MagicMock()
            mock_service.query_verdict = AsyncMock(side_effect=RuntimeError("boom"))
            mock_get.return_value = mock_service

            with pytest.raises(QaOrchestratorError) as exc_info:
                await orchestrator.query_verdict(task_id="task-123")

            assert "QA verdict query failed" in str(exc_info.value)
            assert exc_info.value.code == "qa_verdict_query_failed"


# =============================================================================
# Tests for run_audit_lifecycle
# =============================================================================


class TestRunAuditLifecycle:
    """Tests for the run_audit_lifecycle convenience method."""

    @pytest.mark.asyncio
    async def test_run_audit_lifecycle_success(self, orchestrator: QaOrchestrator, mock_verdict: MagicMock) -> None:
        """Verify full audit lifecycle completes successfully."""
        mock_verdict.verdict = "PASS"
        mock_verdict.details = {"summary": "OK", "score": 1.0}

        with patch.object(orchestrator, "_get_qa_service") as mock_qa, \
             patch.object(orchestrator, "_get_audit_service") as mock_audit:
            mock_qa_service = MagicMock()
            mock_qa_service.audit_task = AsyncMock(return_value=mock_verdict)
            mock_qa.return_value = mock_qa_service

            mock_audit_service = MagicMock()
            mock_audit_service.run_verdict = AsyncMock(return_value=mock_verdict)
            mock_audit.return_value = mock_audit_service

            result = await orchestrator.run_audit_lifecycle(
                task_id="task-123",
                task_subject="Test task",
                changed_files=["file1.py"],
            )

            assert isinstance(result, QaAuditLifecycleResult)
            assert result.success is True
            assert result.review is not None
            assert result.verdict is not None
            assert result.review.status == "completed"
            assert result.verdict.verdict == "PASS"

    @pytest.mark.asyncio
    async def test_run_audit_lifecycle_fails_when_review_fails(
        self, orchestrator: QaOrchestrator, mock_verdict: MagicMock
    ) -> None:
        """Verify lifecycle fails when review status is not completed."""
        mock_verdict.verdict = "UNKNOWN"  # Causes skipped status

        with patch.object(orchestrator, "_get_qa_service") as mock_qa, \
             patch.object(orchestrator, "_get_audit_service") as mock_audit:
            mock_qa_service = MagicMock()
            mock_qa_service.audit_task = AsyncMock(return_value=mock_verdict)
            mock_qa.return_value = mock_qa_service

            mock_verdict.details = {"summary": "OK", "score": 1.0}
            mock_audit_service = MagicMock()
            mock_audit_service.run_verdict = AsyncMock(return_value=mock_verdict)
            mock_audit.return_value = mock_audit_service

            result = await orchestrator.run_audit_lifecycle(task_id="task-123")

            assert result.success is False

    @pytest.mark.asyncio
    async def test_run_audit_lifecycle_fails_when_verdict_is_not_pass(
        self, orchestrator: QaOrchestrator, mock_verdict: MagicMock
    ) -> None:
        """Verify lifecycle fails when verdict is not PASS."""
        mock_verdict.verdict = "FAIL"

        with patch.object(orchestrator, "_get_qa_service") as mock_qa, \
             patch.object(orchestrator, "_get_audit_service") as mock_audit:
            mock_qa_service = MagicMock()
            mock_qa_service.audit_task = AsyncMock(return_value=mock_verdict)
            mock_qa.return_value = mock_qa_service

            mock_verdict.details = {"summary": "Failed", "score": 0.0}
            mock_audit_service = MagicMock()
            mock_audit_service.run_verdict = AsyncMock(return_value=mock_verdict)
            mock_audit.return_value = mock_audit_service

            result = await orchestrator.run_audit_lifecycle(task_id="task-123")

            assert result.success is False


# =============================================================================
# Tests for lazy service resolution
# =============================================================================


class TestServiceResolution:
    """Tests for lazy service resolution."""

    def test_get_qa_service_import_error(self, config: QaAuditConfig) -> None:
        """Verify ImportError during service resolution is wrapped correctly."""
        orch = QaOrchestrator(config)

        with patch(
            "polaris.cells.qa.audit_verdict.public.QAService",
            side_effect=ImportError("Module not found"),
        ):
            with pytest.raises(QaOrchestratorError) as exc_info:
                orch._get_qa_service()

            assert exc_info.value.code == "qa_service_resolution_error"

    def test_get_audit_service_import_error(self, config: QaAuditConfig) -> None:
        """Verify ImportError during service resolution is wrapped correctly."""
        orch = QaOrchestrator(config)

        with patch(
            "polaris.cells.audit.verdict.public.IndependentAuditService",
            side_effect=ImportError("Module not found"),
        ):
            with pytest.raises(QaOrchestratorError) as exc_info:
                orch._get_audit_service()

            assert exc_info.value.code == "audit_service_resolution_error"

    def test_get_qa_service_caches_service(self, config: QaAuditConfig) -> None:
        """Verify service is cached after first call."""
        orch = QaOrchestrator(config)

        mock_instance = MagicMock()
        with patch(
            "polaris.cells.qa.audit_verdict.public.QAService",
            return_value=mock_instance,
        ):
            service1 = orch._get_qa_service()
            service2 = orch._get_qa_service()

            assert service1 is service2 is mock_instance


# =============================================================================
# Tests for value objects
# =============================================================================


class TestQaReviewResult:
    """Tests for QaReviewResult value object."""

    def test_review_result_has_dataclass_fields(self) -> None:
        """Verify QaReviewResult has expected dataclass fields."""
        import dataclasses

        assert dataclasses.is_dataclass(QaReviewResult)
        result = QaReviewResult(
            review_id="r1",
            target="t",
            status="completed",
        )

        # Verify frozen dataclass by checking params
        assert getattr(type(result), "__dataclass_params__", None) is not None

    def test_review_result_default_values(self) -> None:
        """Verify default values are set correctly."""
        result = QaReviewResult(
            review_id="r1",
            target="t",
            status="completed",
        )

        assert result.issue_count == 0
        assert result.findings == ()
        assert result.error == ""
        assert result.metadata == {}


class TestQaVerdictResult:
    """Tests for QaVerdictResult value object."""

    def test_verdict_result_has_dataclass_fields(self) -> None:
        """Verify QaVerdictResult has expected dataclass fields."""
        import dataclasses

        assert dataclasses.is_dataclass(QaVerdictResult)
        result = QaVerdictResult(
            verdict="PASS",
            verdict_id="v1",
            summary="OK",
        )

        # Verify frozen dataclass by checking params
        assert getattr(type(result), "__dataclass_params__", None) is not None

    def test_verdict_result_default_score(self) -> None:
        """Verify default score is 0.0."""
        result = QaVerdictResult(
            verdict="PASS",
            verdict_id="v1",
            summary="OK",
        )

        assert result.score == 0.0

    def test_verdict_result_default_tuples(self) -> None:
        """Verify default tuples are empty."""
        result = QaVerdictResult(
            verdict="PASS",
            verdict_id="v1",
            summary="OK",
        )

        assert result.findings == ()
        assert result.suggestions == ()


class TestQaAuditConfig:
    """Tests for QaAuditConfig value object."""

    def test_config_default_values(self) -> None:
        """Verify default config values."""
        config = QaAuditConfig(workspace="/tmp/ws")

        assert config.run_id == ""
        assert config.criteria == {}
        assert config.evidence_paths == ()
        assert config.auto_audit is True
        assert config.min_coverage == 0.7

    def test_config_custom_values(self) -> None:
        """Verify custom config values."""
        config = QaAuditConfig(
            workspace="/tmp/ws",
            run_id="run-123",
            criteria={"key": "value"},
            evidence_paths=("/path1", "/path2"),
            auto_audit=False,
            min_coverage=0.8,
        )

        assert config.run_id == "run-123"
        assert config.criteria == {"key": "value"}
        assert config.evidence_paths == ("/path1", "/path2")
        assert config.auto_audit is False
        assert config.min_coverage == 0.8


class TestQaAuditLifecycleResult:
    """Tests for QaAuditLifecycleResult value object."""

    def test_lifecycle_result_default_values(self) -> None:
        """Verify default values are set correctly."""
        result = QaAuditLifecycleResult(
            success=True,
            task_id="task-123",
            workspace="/tmp/ws",
        )

        assert result.review is None
        assert result.verdict is None
        assert result.notes == ""

    def test_lifecycle_result_with_results(self) -> None:
        """Verify results are included."""
        review = QaReviewResult(
            review_id="r1",
            target="t",
            status="completed",
        )
        verdict = QaVerdictResult(
            verdict="PASS",
            verdict_id="v1",
            summary="OK",
        )

        result = QaAuditLifecycleResult(
            success=True,
            task_id="task-123",
            workspace="/tmp/ws",
            review=review,
            verdict=verdict,
            notes="All checks passed",
        )

        assert result.review is review
        assert result.verdict is verdict
        assert result.notes == "All checks passed"
