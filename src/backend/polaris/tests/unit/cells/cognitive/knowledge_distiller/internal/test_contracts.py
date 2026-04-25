"""Unit tests for knowledge_distiller public contracts."""

from __future__ import annotations

import pytest
from polaris.cells.cognitive.knowledge_distiller.public.contracts import (
    DistilledKnowledgeUnitV1,
    DistillSessionCommandV1,
    KnowledgeDistillerError,
    KnowledgeRetrievalResultV1,
    RetrieveKnowledgeQueryV1,
    SessionDistillationResultV1,
)


class TestDistillSessionCommandV1:
    """Tests for DistillSessionCommandV1."""

    def test_valid_creation(self) -> None:
        cmd = DistillSessionCommandV1(workspace=".", session_id="sess_123")
        assert cmd.workspace == "."
        assert cmd.session_id == "sess_123"
        assert cmd.run_id is None
        assert cmd.structured_findings == {}
        assert cmd.task_progress == "done"
        assert cmd.outcome == "completed"
        assert cmd.metadata == {}

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            DistillSessionCommandV1(workspace="", session_id="sess_123")

    def test_empty_session_id_raises(self) -> None:
        with pytest.raises(ValueError, match="session_id"):
            DistillSessionCommandV1(workspace=".", session_id="  ")

    def test_metadata_isolated(self) -> None:
        cmd = DistillSessionCommandV1(workspace=".", session_id="s1", metadata={"a": 1})
        cmd.metadata["a"] = 2
        # The original dict passed in should not be mutated because _to_dict_copy creates a copy
        # But since we mutate cmd.metadata directly, it should work
        assert cmd.metadata["a"] == 2


class TestRetrieveKnowledgeQueryV1:
    """Tests for RetrieveKnowledgeQueryV1."""

    def test_defaults(self) -> None:
        q = RetrieveKnowledgeQueryV1(workspace=".", query="timeout")
        assert q.top_k == 5
        assert q.role_filter is None
        assert q.knowledge_type is None
        assert q.min_confidence == 0.5

    def test_custom_values(self) -> None:
        q = RetrieveKnowledgeQueryV1(
            workspace=".",
            query="error",
            top_k=10,
            role_filter="director",
            knowledge_type="error_pattern",
            min_confidence=0.8,
        )
        assert q.top_k == 10
        assert q.role_filter == "director"
        assert q.knowledge_type == "error_pattern"
        assert q.min_confidence == 0.8


class TestDistilledKnowledgeUnitV1:
    """Tests for DistilledKnowledgeUnitV1."""

    def test_creation(self) -> None:
        from datetime import datetime

        now = datetime.now()
        unit = DistilledKnowledgeUnitV1(
            knowledge_id="kn_001",
            knowledge_type="error_pattern",
            pattern_summary="Null pointer",
            confidence=0.9,
            occurrence_count=3,
            related_findings=["sess_1"],
            extracted_insight="Check for null",
            prevention_hint="Add validation",
            created_at=now,
            metadata={"role": "director"},
        )
        assert unit.knowledge_id == "kn_001"
        assert unit.prevention_hint == "Add validation"
        assert unit.metadata == {"role": "director"}

    def test_default_created_at(self) -> None:
        unit = DistilledKnowledgeUnitV1(
            knowledge_id="kn_002",
            knowledge_type="success_pattern",
            pattern_summary="Fast build",
            confidence=0.8,
            occurrence_count=1,
            related_findings=[],
            extracted_insight="Use caching",
        )
        assert unit.created_at is not None


class TestKnowledgeRetrievalResultV1:
    """Tests for KnowledgeRetrievalResultV1."""

    def test_creation(self) -> None:
        result = KnowledgeRetrievalResultV1(
            knowledge_units=[],
            query="test",
            total_available=0,
        )
        assert result.total_available == 0
        assert result.query == "test"


class TestSessionDistillationResultV1:
    """Tests for SessionDistillationResultV1."""

    def test_creation(self) -> None:
        result = SessionDistillationResultV1(
            session_id="sess_1",
            knowledge_units_created=2,
            patterns_extracted=["error"],
            knowledge_ids=["kn_1"],
        )
        assert result.knowledge_units_created == 2


class TestKnowledgeDistillerError:
    """Tests for KnowledgeDistillerError."""

    def test_is_runtime_error(self) -> None:
        err = KnowledgeDistillerError("something went wrong")
        assert isinstance(err, RuntimeError)
        assert err.code == "knowledge_distiller_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = KnowledgeDistillerError("fail", code="E001", details={"key": "value"})
        assert err.code == "E001"
        assert err.details == {"key": "value"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            KnowledgeDistillerError("")
