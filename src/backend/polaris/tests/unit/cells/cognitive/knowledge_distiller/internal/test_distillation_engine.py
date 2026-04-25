"""Unit tests for DistillationEngine."""

from __future__ import annotations

import pytest
from polaris.cells.cognitive.knowledge_distiller.internal.distillation_engine import (
    DistillationEngine,
)
from polaris.cells.cognitive.knowledge_distiller.public.contracts import (
    DistillSessionCommandV1,
    SessionDistillationResultV1,
)


class TestDistillationEngine:
    """Tests for DistillationEngine."""

    @pytest.fixture
    def engine(self) -> DistillationEngine:
        return DistillationEngine(workspace=".")

    def test_distill_session_no_patterns(self, engine: DistillationEngine) -> None:
        command = DistillSessionCommandV1(
            workspace=".",
            session_id="sess_empty",
            structured_findings={},
            outcome="completed",
        )
        result = engine.distill_session(command)
        assert isinstance(result, SessionDistillationResultV1)
        assert result.knowledge_units_created == 0
        assert result.patterns_extracted == []
        assert result.knowledge_ids == []

    def test_distill_session_error_pattern(self, engine: DistillationEngine) -> None:
        command = DistillSessionCommandV1(
            workspace=".",
            session_id="sess_err",
            structured_findings={
                "error_summary": "Index out of range",
                "suspected_files": ["bug.py"],
            },
            outcome="failed",
        )
        result = engine.distill_session(command)
        assert result.knowledge_units_created >= 1
        assert "error_pattern" in result.patterns_extracted
        assert len(result.knowledge_ids) >= 1

    def test_distill_session_success_pattern(self, engine: DistillationEngine) -> None:
        command = DistillSessionCommandV1(
            workspace=".",
            session_id="sess_ok",
            structured_findings={
                "verified_results": ["test_passed"],
                "patched_files": ["fix.py"],
            },
            outcome="completed",
        )
        result = engine.distill_session(command)
        assert result.knowledge_units_created >= 1
        assert "success_pattern" in result.patterns_extracted

    def test_distill_session_stagnation_pattern(self, engine: DistillationEngine) -> None:
        command = DistillSessionCommandV1(
            workspace=".",
            session_id="sess_stag",
            structured_findings={
                "_findings_trajectory": [
                    {"task_progress": "step1"},
                    {"task_progress": "step1"},
                    {"task_progress": "step1"},
                ],
            },
            outcome="stagnation",
        )
        result = engine.distill_session(command)
        assert result.knowledge_units_created >= 1
        assert "stagnation_pattern" in result.patterns_extracted

    def test_get_knowledge_store(self, engine: DistillationEngine) -> None:
        store = engine.get_knowledge_store()
        assert store is not None
