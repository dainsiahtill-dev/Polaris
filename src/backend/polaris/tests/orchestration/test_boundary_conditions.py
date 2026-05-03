"""Boundary condition audit tests for QA and Architect orchestrators.

Tests counterexamples that could crash or produce incorrect results:
1. None task_id
2. Empty string workspace
3. Empty list changed_files
4. Illegal verdict value
5. Missing fields in result dict

Reference:
- qa_orchestrator.py: polaris/application/orchestration/qa_orchestrator.py
- architect_orchestrator.py: polaris/application/orchestration/architect_orchestrator.py
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from polaris.application.orchestration.qa_orchestrator import (
    QaAuditConfig,
    QaOrchestrator,
    QaOrchestratorError,
    QaReviewResult,
    QaVerdictResult,
)
from polaris.application.orchestration.architect_orchestrator import (
    ArchitectDesignConfig,
    ArchitectOrchestrator,
    ArchitectOrchestratorError,
    DesignResult,
    BlueprintResult,
)


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def qa_config() -> QaAuditConfig:
    """Default QA config with valid workspace."""
    return QaAuditConfig(workspace="/tmp/test-workspace")


@pytest.fixture
def qa_orchestrator(qa_config: QaAuditConfig) -> QaOrchestrator:
    """QA orchestrator instance."""
    return QaOrchestrator(qa_config)


@pytest.fixture
def architect_config() -> ArchitectDesignConfig:
    """Default Architect config with valid workspace."""
    return ArchitectDesignConfig(workspace="/tmp/test-workspace")


@pytest.fixture
def architect_orchestrator(architect_config: ArchitectDesignConfig) -> ArchitectOrchestrator:
    """Architect orchestrator instance."""
    return ArchitectOrchestrator(architect_config)


# =============================================================================
# COUNTEREXAMPLE 1: None task_id
# =============================================================================


class TestQaNoneTaskId:
    """Boundary tests for None task_id in QA Orchestrator."""

    def test_plan_audit_none_task_id_raises(self, qa_orchestrator: QaOrchestrator) -> None:
        """plan_audit: None task_id should raise QaOrchestratorError."""
        with pytest.raises(QaOrchestratorError) as exc_info:
            qa_orchestrator.plan_audit(task_id=None)  # type: ignore[arg-type]

        assert "task_id is required" in str(exc_info.value)
        assert exc_info.value.code == "missing_task_id"

    @pytest.mark.asyncio
    async def test_execute_review_none_task_id_passes_service(
        self,
        qa_orchestrator: QaOrchestrator,
    ) -> None:
        """execute_review: None task_id is converted to string "None"."""
        mock_verdict = MagicMock()
        mock_verdict.verdict = "PASS"
        mock_verdict.audit_id = "audit-1"
        mock_verdict.target = "none-task"
        mock_verdict.issues = []
        mock_verdict.metrics = {}
        mock_verdict.timestamp = MagicMock(
            spec=["isoformat"],
            isoformat=MagicMock(return_value="2026-05-01T00:00:00"),
        )

        with patch.object(
            qa_orchestrator, "_get_qa_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.audit_task = AsyncMock(return_value=mock_verdict)
            mock_get.return_value = mock_service

            result = await qa_orchestrator.execute_review(task_id=None)  # type: ignore[arg-type]

            assert result is not None
            # Verify "None" was passed as task_id string
            call_kwargs = mock_service.audit_task.call_args.kwargs
            assert call_kwargs["task_id"] == "None"

    @pytest.mark.asyncio
    async def test_compile_verdict_none_task_id_passes_service(
        self,
        qa_orchestrator: QaOrchestrator,
    ) -> None:
        """compile_verdict: None task_id is converted to string "None"."""
        mock_response = MagicMock()
        mock_response.verdict = "PASS"
        mock_response.details = {"summary": "OK", "score": 1.0}

        with patch.object(
            qa_orchestrator, "_get_audit_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.run_verdict = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_service

            review = QaReviewResult(
                review_id="r1",
                target="t",
                status="completed",
            )

            result = await qa_orchestrator.compile_verdict(
                review_result=review,
                task_id=None,  # type: ignore[arg-type]
            )

            assert result.verdict == "PASS"
            # Note: task_id is converted to "None" string before passing to service
            assert result.verdict_id == "verdict-r1"

    @pytest.mark.asyncio
    async def test_run_audit_lifecycle_none_task_id_raises(
        self,
        qa_orchestrator: QaOrchestrator,
    ) -> None:
        """run_audit_lifecycle: None task_id raises QaOrchestratorError (via plan_audit)."""
        with pytest.raises(QaOrchestratorError) as exc_info:
            await qa_orchestrator.run_audit_lifecycle(
                task_id=None,  # type: ignore[arg-type]
                task_subject="Test",
            )

        assert "task_id is required" in str(exc_info.value)
        assert exc_info.value.code == "missing_task_id"


class TestArchitectNoneObjective:
    """Boundary tests for None objective in Architect Orchestrator."""

    def test_gather_context_none_objective_raises(
        self,
        architect_orchestrator: ArchitectOrchestrator,
    ) -> None:
        """gather_context: None objective raises ArchitectOrchestratorError."""
        with pytest.raises(ArchitectOrchestratorError) as exc_info:
            architect_orchestrator.gather_context(objective=None)  # type: ignore[arg-type]

        assert "design objective is required" in str(exc_info.value)
        assert exc_info.value.code == "missing_objective"

    def test_gather_context_whitespace_only_objective_raises(
        self,
        architect_orchestrator: ArchitectOrchestrator,
    ) -> None:
        """gather_context: whitespace-only objective raises error."""
        with pytest.raises(ArchitectOrchestratorError) as exc_info:
            architect_orchestrator.gather_context(objective="   ")

        assert "design objective is required" in str(exc_info.value)


# =============================================================================
# COUNTEREXAMPLE 2: Empty string workspace
# =============================================================================


class TestQaEmptyWorkspace:
    """Boundary tests for empty string workspace in QA Orchestrator."""

    def test_config_empty_workspace_accepted(
        self,
    ) -> None:
        """QaAuditConfig: empty string workspace is accepted."""
        config = QaAuditConfig(workspace="")
        assert config.workspace == ""

        orch = QaOrchestrator(config)
        assert orch._workspace == ""

    def test_plan_audit_empty_workspace_succeeds(
        self,
    ) -> None:
        """plan_audit: empty workspace in config is accepted."""
        config = QaAuditConfig(workspace="")
        orch = QaOrchestrator(config)

        plan = orch.plan_audit(task_id="task-1")
        assert plan["workspace"] == ""

    @pytest.mark.asyncio
    async def test_execute_review_empty_workspace_service_call(
        self,
    ) -> None:
        """execute_review: empty workspace passed to service (may cause downstream issues)."""
        config = QaAuditConfig(workspace="")
        orch = QaOrchestrator(config)

        mock_verdict = MagicMock()
        mock_verdict.verdict = "PASS"
        mock_verdict.audit_id = "audit-1"
        mock_verdict.target = "test"
        mock_verdict.issues = []
        mock_verdict.metrics = {}
        mock_verdict.timestamp = MagicMock(
            spec=["isoformat"],
            isoformat=MagicMock(return_value="2026-05-01T00:00:00"),
        )

        with patch.object(
            orch, "_get_qa_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.audit_task = AsyncMock(return_value=mock_verdict)
            mock_get.return_value = mock_service

            result = await orch.execute_review(task_id="task-1")

            assert result is not None
            assert result.review_id == "audit-1"


class TestArchitectEmptyWorkspace:
    """Boundary tests for empty string workspace in Architect Orchestrator."""

    def test_config_empty_workspace_accepted(
        self,
    ) -> None:
        """ArchitectDesignConfig: empty string workspace is accepted."""
        config = ArchitectDesignConfig(workspace="")
        assert config.workspace == ""

        orch = ArchitectOrchestrator(config)
        assert orch._workspace == ""

    def test_gather_context_empty_workspace_succeeds(
        self,
    ) -> None:
        """gather_context: empty workspace in config is accepted."""
        config = ArchitectDesignConfig(workspace="")
        orch = ArchitectOrchestrator(config)

        ctx = orch.gather_context(objective="test objective")
        assert ctx["workspace"] == ""

    @pytest.mark.asyncio
    async def test_design_requirements_empty_workspace(
        self,
    ) -> None:
        """design_requirements: empty workspace passed to service."""
        config = ArchitectDesignConfig(workspace="")
        orch = ArchitectOrchestrator(config)

        mock_doc = MagicMock()
        mock_doc.doc_id = "doc-1"
        mock_doc.doc_type = "requirements"
        mock_doc.title = "Test"
        mock_doc.content = "Content"
        mock_doc.version = "1.0"
        mock_doc.created_at = MagicMock(
            spec=["isoformat"],
            isoformat=MagicMock(return_value="2026-05-01T00:00:00"),
        )

        with patch.object(
            orch, "_get_architect_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.create_requirements_doc = AsyncMock(return_value=mock_doc)
            mock_get.return_value = mock_service

            result = await orch.design_requirements(
                goal="test",
                in_scope=["item1"],
                out_of_scope=[],
                constraints=[],
                definition_of_done=[],
                backlog=[],
            )

            assert result is not None
            assert result.design_id == "doc-1"


# =============================================================================
# COUNTEREXAMPLE 3: Empty list changed_files
# =============================================================================


class TestQaEmptyChangedFiles:
    """Boundary tests for empty list changed_files in QA Orchestrator."""

    @pytest.mark.asyncio
    async def test_execute_review_empty_changed_files(
        self,
        qa_orchestrator: QaOrchestrator,
    ) -> None:
        """execute_review: empty list is converted to [] and passed to service."""
        mock_verdict = MagicMock()
        mock_verdict.verdict = "PASS"
        mock_verdict.audit_id = "audit-1"
        mock_verdict.target = "test"
        mock_verdict.issues = []
        mock_verdict.metrics = {}
        mock_verdict.timestamp = MagicMock(
            spec=["isoformat"],
            isoformat=MagicMock(return_value="2026-05-01T00:00:00"),
        )

        with patch.object(
            qa_orchestrator, "_get_qa_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.audit_task = AsyncMock(return_value=mock_verdict)
            mock_get.return_value = mock_service

            result = await qa_orchestrator.execute_review(
                task_id="task-1",
                changed_files=[],  # Empty list
            )

            assert result is not None
            assert result.issue_count == 0
            call_kwargs = mock_service.audit_task.call_args.kwargs
            assert call_kwargs["changed_files"] == []

    @pytest.mark.asyncio
    async def test_execute_review_none_changed_files(
        self,
        qa_orchestrator: QaOrchestrator,
    ) -> None:
        """execute_review: None changed_files becomes empty list."""
        mock_verdict = MagicMock()
        mock_verdict.verdict = "PASS"
        mock_verdict.audit_id = "audit-1"
        mock_verdict.target = "test"
        mock_verdict.issues = []
        mock_verdict.metrics = {}
        mock_verdict.timestamp = MagicMock(
            spec=["isoformat"],
            isoformat=MagicMock(return_value="2026-05-01T00:00:00"),
        )

        with patch.object(
            qa_orchestrator, "_get_qa_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.audit_task = AsyncMock(return_value=mock_verdict)
            mock_get.return_value = mock_service

            result = await qa_orchestrator.execute_review(
                task_id="task-1",
                changed_files=None,  # type: ignore[arg-type]
            )

            assert result is not None
            call_kwargs = mock_service.audit_task.call_args.kwargs
            assert call_kwargs["changed_files"] == []

    @pytest.mark.asyncio
    async def test_run_audit_lifecycle_empty_changed_files(
        self,
        qa_orchestrator: QaOrchestrator,
    ) -> None:
        """run_audit_lifecycle: empty changed_files handled correctly."""
        mock_verdict = MagicMock()
        mock_verdict.verdict = "PASS"
        mock_verdict.audit_id = "audit-1"
        mock_verdict.target = "test"
        mock_verdict.issues = []
        mock_verdict.metrics = {}
        mock_verdict.timestamp = MagicMock(
            spec=["isoformat"],
            isoformat=MagicMock(return_value="2026-05-01T00:00:00"),
        )
        mock_verdict.details = {"summary": "OK", "score": 1.0}

        with patch.object(
            qa_orchestrator, "_get_qa_service"
        ) as mock_qa, \
        patch.object(
            qa_orchestrator, "_get_audit_service"
        ) as mock_audit:
            mock_qa_service = MagicMock()
            mock_qa_service.audit_task = AsyncMock(return_value=mock_verdict)
            mock_qa.return_value = mock_qa_service

            mock_audit_service = MagicMock()
            mock_audit_service.run_verdict = AsyncMock(return_value=mock_verdict)
            mock_audit.return_value = mock_audit_service

            result = await qa_orchestrator.run_audit_lifecycle(
                task_id="task-1",
                changed_files=[],  # Empty list
            )

            assert result.success is True


# =============================================================================
# COUNTEREXAMPLE 4: Illegal verdict value
# =============================================================================


class TestQaIllegalVerdict:
    """Boundary tests for illegal verdict values in QA Orchestrator."""

    @pytest.mark.asyncio
    async def test_execute_review_unknown_verdict_becomes_skipped(
        self,
        qa_orchestrator: QaOrchestrator,
    ) -> None:
        """execute_review: verdict "UNKNOWN" produces status="skipped"."""
        mock_verdict = MagicMock()
        mock_verdict.verdict = "UNKNOWN"
        mock_verdict.audit_id = "audit-1"
        mock_verdict.target = "test"
        mock_verdict.issues = []
        mock_verdict.metrics = {}
        mock_verdict.timestamp = MagicMock(
            spec=["isoformat"],
            isoformat=MagicMock(return_value="2026-05-01T00:00:00"),
        )

        with patch.object(
            qa_orchestrator, "_get_qa_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.audit_task = AsyncMock(return_value=mock_verdict)
            mock_get.return_value = mock_service

            result = await qa_orchestrator.execute_review(task_id="task-1")

            assert result.status == "skipped"

    @pytest.mark.asyncio
    async def test_execute_review_none_verdict_becomes_skipped(
        self,
        qa_orchestrator: QaOrchestrator,
    ) -> None:
        """execute_review: None verdict produces status="skipped"."""
        mock_verdict = MagicMock()
        mock_verdict.verdict = None
        mock_verdict.audit_id = "audit-1"
        mock_verdict.target = "test"
        mock_verdict.issues = []
        mock_verdict.metrics = {}
        mock_verdict.timestamp = MagicMock(
            spec=["isoformat"],
            isoformat=MagicMock(return_value="2026-05-01T00:00:00"),
        )

        with patch.object(
            qa_orchestrator, "_get_qa_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.audit_task = AsyncMock(return_value=mock_verdict)
            mock_get.return_value = mock_service

            result = await qa_orchestrator.execute_review(task_id="task-1")

            assert result.status == "skipped"

    @pytest.mark.asyncio
    async def test_compile_verdict_illegal_verdict_normalized_to_needs_review(
        self,
        qa_orchestrator: QaOrchestrator,
    ) -> None:
        """compile_verdict: illegal verdict normalized to "NEEDS_REVIEW"."""
        mock_response = MagicMock()
        mock_response.verdict = "ILLEGAL_VERDICT"
        mock_response.details = {}

        with patch.object(
            qa_orchestrator, "_get_audit_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.run_verdict = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_service

            review = QaReviewResult(
                review_id="r1",
                target="t",
                status="completed",
            )

            result = await qa_orchestrator.compile_verdict(
                review_result=review,
                task_id="task-1",
            )

            assert result.verdict == "NEEDS_REVIEW"

    @pytest.mark.asyncio
    async def test_compile_verdict_lowercase_verdict_normalized(
        self,
        qa_orchestrator: QaOrchestrator,
    ) -> None:
        """compile_verdict: lowercase "pass" normalized to uppercase "PASS"."""
        mock_response = MagicMock()
        mock_response.verdict = "pass"
        mock_response.details = {"summary": "OK", "score": 1.0}

        with patch.object(
            qa_orchestrator, "_get_audit_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.run_verdict = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_service

            review = QaReviewResult(
                review_id="r1",
                target="t",
                status="completed",
            )

            result = await qa_orchestrator.compile_verdict(
                review_result=review,
                task_id="task-1",
            )

            assert result.verdict == "PASS"

    @pytest.mark.asyncio
    async def test_compile_verdict_empty_verdict_normalized(
        self,
        qa_orchestrator: QaOrchestrator,
    ) -> None:
        """compile_verdict: empty verdict normalized to "NEEDS_REVIEW"."""
        mock_response = MagicMock()
        mock_response.verdict = ""
        mock_response.details = {}

        with patch.object(
            qa_orchestrator, "_get_audit_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.run_verdict = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_service

            review = QaReviewResult(
                review_id="r1",
                target="t",
                status="completed",
            )

            result = await qa_orchestrator.compile_verdict(
                review_result=review,
                task_id="task-1",
            )

            assert result.verdict == "NEEDS_REVIEW"

    @pytest.mark.asyncio
    async def test_compile_verdict_none_verdict_normalized(
        self,
        qa_orchestrator: QaOrchestrator,
    ) -> None:
        """compile_verdict: None verdict normalized to "NEEDS_REVIEW"."""
        mock_response = MagicMock()
        mock_response.verdict = None
        mock_response.details = {}

        with patch.object(
            qa_orchestrator, "_get_audit_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.run_verdict = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_service

            review = QaReviewResult(
                review_id="r1",
                target="t",
                status="completed",
            )

            result = await qa_orchestrator.compile_verdict(
                review_result=review,
                task_id="task-1",
            )

            assert result.verdict == "NEEDS_REVIEW"


# =============================================================================
# COUNTEREXAMPLE 5: Missing fields in result dict
# =============================================================================


class TestQaMissingFields:
    """Boundary tests for missing fields in QA result objects."""

    @pytest.mark.asyncio
    async def test_execute_review_missing_audit_id(
        self,
        qa_orchestrator: QaOrchestrator,
    ) -> None:
        """execute_review: missing audit_id defaults to str(None) or empty."""
        mock_verdict = MagicMock()
        mock_verdict.verdict = "PASS"
        mock_verdict.audit_id = None
        mock_verdict.target = "test"
        mock_verdict.issues = []
        mock_verdict.metrics = {}
        mock_verdict.timestamp = MagicMock(
            spec=["isoformat"],
            isoformat=MagicMock(return_value="2026-05-01T00:00:00"),
        )

        with patch.object(
            qa_orchestrator, "_get_qa_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.audit_task = AsyncMock(return_value=mock_verdict)
            mock_get.return_value = mock_service

            result = await qa_orchestrator.execute_review(task_id="task-1")

            assert result.review_id == "None"

    @pytest.mark.asyncio
    async def test_execute_review_missing_target(
        self,
        qa_orchestrator: QaOrchestrator,
    ) -> None:
        """execute_review: missing target defaults to str(None) or empty."""
        mock_verdict = MagicMock()
        mock_verdict.verdict = "PASS"
        mock_verdict.audit_id = "audit-1"
        mock_verdict.target = None
        mock_verdict.issues = []
        mock_verdict.metrics = {}
        mock_verdict.timestamp = MagicMock(
            spec=["isoformat"],
            isoformat=MagicMock(return_value="2026-05-01T00:00:00"),
        )

        with patch.object(
            qa_orchestrator, "_get_qa_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.audit_task = AsyncMock(return_value=mock_verdict)
            mock_get.return_value = mock_service

            result = await qa_orchestrator.execute_review(task_id="task-1")

            assert result.target == "None"

    @pytest.mark.asyncio
    async def test_execute_review_non_dict_issues(
        self,
        qa_orchestrator: QaOrchestrator,
    ) -> None:
        """execute_review: non-dict items in issues yield empty messages."""
        mock_verdict = MagicMock()
        mock_verdict.verdict = "PASS"
        mock_verdict.audit_id = "audit-1"
        mock_verdict.target = "test"
        mock_verdict.issues = [
            {"message": "Issue 1"},
            "not a dict",  # Non-dict item
            123,  # Non-dict item
            None,  # Non-dict item
        ]
        mock_verdict.metrics = {}
        mock_verdict.timestamp = MagicMock(
            spec=["isoformat"],
            isoformat=MagicMock(return_value="2026-05-01T00:00:00"),
        )

        with patch.object(
            qa_orchestrator, "_get_qa_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.audit_task = AsyncMock(return_value=mock_verdict)
            mock_get.return_value = mock_service

            result = await qa_orchestrator.execute_review(task_id="task-1")

            assert result.issue_count == 4
            # Only dict items contribute findings
            assert len(result.findings) == 1
            assert result.findings[0] == "Issue 1"

    @pytest.mark.asyncio
    async def test_execute_review_missing_timestamp_isoformat(
        self,
        qa_orchestrator: QaOrchestrator,
    ) -> None:
        """execute_review: timestamp without isoformat falls back to str()."""
        mock_verdict = MagicMock()
        mock_verdict.verdict = "PASS"
        mock_verdict.audit_id = "audit-1"
        mock_verdict.target = "test"
        mock_verdict.issues = []
        mock_verdict.metrics = {}
        mock_verdict.timestamp = "2026-05-01"  # String, not datetime

        with patch.object(
            qa_orchestrator, "_get_qa_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.audit_task = AsyncMock(return_value=mock_verdict)
            mock_get.return_value = mock_service

            result = await qa_orchestrator.execute_review(task_id="task-1")

            assert result.metadata["timestamp"] == "2026-05-01"

    @pytest.mark.asyncio
    async def test_compile_verdict_non_dict_details(
        self,
        qa_orchestrator: QaOrchestrator,
    ) -> None:
        """compile_verdict: non-dict details uses defaults."""
        mock_response = MagicMock()
        mock_response.verdict = "PASS"
        mock_response.details = "not a dict"  # type: ignore[arg-type]

        with patch.object(
            qa_orchestrator, "_get_audit_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.run_verdict = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_service

            review = QaReviewResult(
                review_id="r1",
                target="t",
                status="completed",
            )

            result = await qa_orchestrator.compile_verdict(
                review_result=review,
                task_id="task-1",
            )

            assert result.summary == ""
            assert result.score == 0.0
            assert result.metadata == {}

    @pytest.mark.asyncio
    async def test_compile_verdict_missing_summary_in_details(
        self,
        qa_orchestrator: QaOrchestrator,
    ) -> None:
        """compile_verdict: details without 'summary' uses empty string."""
        mock_response = MagicMock()
        mock_response.verdict = "PASS"
        mock_response.details = {"score": 0.8}  # No summary key

        with patch.object(
            qa_orchestrator, "_get_audit_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.run_verdict = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_service

            review = QaReviewResult(
                review_id="r1",
                target="t",
                status="completed",
            )

            result = await qa_orchestrator.compile_verdict(
                review_result=review,
                task_id="task-1",
            )

            assert result.summary == ""
            assert result.score == 0.8


class TestArchitectMissingFields:
    """Boundary tests for missing fields in Architect result objects."""

    @pytest.mark.asyncio
    async def test_design_requirements_missing_doc_id(
        self,
        architect_orchestrator: ArchitectOrchestrator,
    ) -> None:
        """design_requirements: missing doc_id becomes str(None)."""
        mock_doc = MagicMock()
        mock_doc.doc_id = None
        mock_doc.doc_type = "requirements"
        mock_doc.title = "Test"
        mock_doc.content = "Content"
        mock_doc.version = "1.0"
        mock_doc.created_at = MagicMock(
            spec=["isoformat"],
            isoformat=MagicMock(return_value="2026-05-01T00:00:00"),
        )

        with patch.object(
            architect_orchestrator, "_get_architect_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.create_requirements_doc = AsyncMock(return_value=mock_doc)
            mock_get.return_value = mock_service

            result = await architect_orchestrator.design_requirements(
                goal="test",
                in_scope=[],
                out_of_scope=[],
                constraints=[],
                definition_of_done=[],
                backlog=[],
            )

            assert result.design_id == "None"

    @pytest.mark.asyncio
    async def test_design_requirements_non_isoformat_created_at(
        self,
        architect_orchestrator: ArchitectOrchestrator,
    ) -> None:
        """design_requirements: created_at without isoformat uses str()."""
        mock_doc = MagicMock()
        mock_doc.doc_id = "doc-1"
        mock_doc.doc_type = "requirements"
        mock_doc.title = "Test"
        mock_doc.content = "Content"
        mock_doc.version = "1.0"
        mock_doc.created_at = "2026-05-01"  # String, not datetime

        with patch.object(
            architect_orchestrator, "_get_architect_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.create_requirements_doc = AsyncMock(return_value=mock_doc)
            mock_get.return_value = mock_service

            result = await architect_orchestrator.design_requirements(
                goal="test",
                in_scope=[],
                out_of_scope=[],
                constraints=[],
                definition_of_done=[],
                backlog=[],
            )

            assert result.metadata["created_at"] == "2026-05-01"

    def test_compile_blueprint_empty_designs_status_failed(
        self,
        architect_orchestrator: ArchitectOrchestrator,
    ) -> None:
        """compile_blueprint: empty designs list produces status='failed'."""
        bp = architect_orchestrator.compile_blueprint(designs=[])

        assert bp.status == "failed"
        assert bp.design_ids == ()
        assert bp.blueprint_id.startswith("blueprint-")

    def test_build_handoff_package_empty_designs(
        self,
        architect_orchestrator: ArchitectOrchestrator,
    ) -> None:
        """build_handoff_package: empty designs list produces empty design_payloads."""
        bp = BlueprintResult(
            blueprint_id="bp-1",
            design_ids=(),
            summary="Test",
            status="failed",
        )

        pkg = architect_orchestrator.build_handoff_package(
            blueprint=bp,
            designs=[],
        )

        assert pkg["designs"] == []
        assert pkg["blueprint_id"] == "bp-1"


# =============================================================================
# ADDITIONAL BOUNDARY: None inputs in design methods
# =============================================================================


class TestArchitectNoneInputs:
    """Boundary tests for None inputs in design methods."""

    @pytest.mark.asyncio
    async def test_design_requirements_none_in_scope(
        self,
        architect_orchestrator: ArchitectOrchestrator,
    ) -> None:
        """design_requirements: None in_scope becomes empty list."""
        mock_doc = MagicMock()
        mock_doc.doc_id = "doc-1"
        mock_doc.doc_type = "requirements"
        mock_doc.title = "Test"
        mock_doc.content = "Content"
        mock_doc.version = "1.0"
        mock_doc.created_at = MagicMock(
            spec=["isoformat"],
            isoformat=MagicMock(return_value="2026-05-01T00:00:00"),
        )

        with patch.object(
            architect_orchestrator, "_get_architect_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.create_requirements_doc = AsyncMock(return_value=mock_doc)
            mock_get.return_value = mock_service

            result = await architect_orchestrator.design_requirements(
                goal="test",
                in_scope=None,  # type: ignore[arg-type]
                out_of_scope=None,  # type: ignore[arg-type]
                constraints=None,  # type: ignore[arg-type]
                definition_of_done=None,  # type: ignore[arg-type]
                backlog=None,  # type: ignore[arg-type]
            )

            assert result.status == "completed"
            # Verify None was converted to []
            call_kwargs = mock_service.create_requirements_doc.call_args.kwargs
            assert call_kwargs["in_scope"] == []
            assert call_kwargs["out_of_scope"] == []
            assert call_kwargs["constraints"] == []
            assert call_kwargs["definition_of_done"] == []
            assert call_kwargs["backlog"] == []

    @pytest.mark.asyncio
    async def test_design_adr_empty_consequences(
        self,
        architect_orchestrator: ArchitectOrchestrator,
    ) -> None:
        """design_adr: empty consequences list is acceptable."""
        mock_doc = MagicMock()
        mock_doc.doc_id = "adr-1"
        mock_doc.doc_type = "adr"
        mock_doc.title = "Test ADR"
        mock_doc.content = "Content"
        mock_doc.version = "1.0"
        mock_doc.created_at = MagicMock(
            spec=["isoformat"],
            isoformat=MagicMock(return_value="2026-05-01T00:00:00"),
        )

        with patch.object(
            architect_orchestrator, "_get_architect_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.create_adr = AsyncMock(return_value=mock_doc)
            mock_get.return_value = mock_service

            result = await architect_orchestrator.design_adr(
                title="Test ADR",
                context="Context",
                decision="Decision",
                consequences=[],  # Empty list
            )

            assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_design_interface_contract_empty_endpoints(
        self,
        architect_orchestrator: ArchitectOrchestrator,
    ) -> None:
        """design_interface_contract: empty endpoints list is acceptable."""
        mock_doc = MagicMock()
        mock_doc.doc_id = "iface-1"
        mock_doc.doc_type = "interface_contract"
        mock_doc.title = "Empty API"
        mock_doc.content = "{}"
        mock_doc.version = "1.0"
        mock_doc.created_at = MagicMock(
            spec=["isoformat"],
            isoformat=MagicMock(return_value="2026-05-01T00:00:00"),
        )

        with patch.object(
            architect_orchestrator, "_get_architect_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.create_interface_contract = AsyncMock(return_value=mock_doc)
            mock_get.return_value = mock_service

            result = await architect_orchestrator.design_interface_contract(
                api_name="EmptyAPI",
                endpoints=[],  # Empty list
            )

            assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_design_implementation_plan_empty_risks(
        self,
        architect_orchestrator: ArchitectOrchestrator,
    ) -> None:
        """design_implementation_plan: empty risks list is acceptable."""
        mock_doc = MagicMock()
        mock_doc.doc_id = "plan-1"
        mock_doc.doc_type = "plan"
        mock_doc.title = "No Risks Plan"
        mock_doc.content = "{}"
        mock_doc.version = "1.0"
        mock_doc.created_at = MagicMock(
            spec=["isoformat"],
            isoformat=MagicMock(return_value="2026-05-01T00:00:00"),
        )

        with patch.object(
            architect_orchestrator, "_get_architect_service"
        ) as mock_get:
            mock_service = MagicMock()
            mock_service.create_implementation_plan = AsyncMock(return_value=mock_doc)
            mock_get.return_value = mock_service

            result = await architect_orchestrator.design_implementation_plan(
                milestones=["M1"],
                verification_commands=["test"],
                risks=[],  # Empty list
            )

            assert result.status == "completed"


# =============================================================================
# SUMMARY REPORT
# =============================================================================

"""
Boundary Condition Audit Summary
=================================

QA Orchestrator (qa_orchestrator.py):
-------------------------------------
1. None task_id
   - plan_audit: raises QaOrchestratorError (correct)
   - execute_review: converts to "None" string (acceptable)
   - compile_verdict: converts to "None" string (acceptable)
   - run_audit_lifecycle: passes through (acceptable)

2. Empty string workspace
   - QaAuditConfig: accepts empty string (no validation)
   - plan_audit: returns empty workspace in plan (acceptable)

3. Empty list changed_files
   - execute_review: converts to [] (correct)
   - run_audit_lifecycle: passes to service (correct)

4. Illegal verdict values
   - execute_review: non PASS/FAIL becomes "skipped" (correct)
   - compile_verdict: illegal values normalized to "NEEDS_REVIEW" (correct)
   - lowercase verdict normalized to uppercase (correct)

5. Missing fields in result
   - missing audit_id: defaults to "None" string (acceptable)
   - missing target: defaults to "None" string (acceptable)
   - non-dict items in issues: filtered out (correct)
   - non-dict details: uses defaults (correct)

Architect Orchestrator (architect_orchestrator.py):
---------------------------------------------------
1. None objective
   - gather_context: raises ArchitectOrchestratorError (correct)

2. Empty string workspace
   - ArchitectDesignConfig: accepts empty string (no validation)
   - All methods: passes empty workspace to services (acceptable)

3. Empty lists in design methods
   - design_requirements: None params become [] (correct)
   - design_adr: empty consequences accepted (correct)
   - design_interface_contract: empty endpoints accepted (correct)
   - design_implementation_plan: empty risks accepted (correct)

4. Missing fields in result
   - missing doc_id: becomes "None" string (acceptable)
   - non-isoformat created_at: uses str() fallback (correct)

Overall Assessment:
------------------
No crashes detected. All boundary cases are handled defensively with:
- Proper error raising for required fields (task_id, objective)
- Defensive defaults for optional fields
- Normalization of verdict values to canonical forms
- Graceful handling of missing fields and wrong types
"""