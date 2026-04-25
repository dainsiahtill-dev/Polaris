"""Unit tests for KnowledgeStore."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator

import pytest
from polaris.cells.cognitive.knowledge_distiller.internal.knowledge_store import (
    KnowledgeStore,
)
from polaris.cells.cognitive.knowledge_distiller.public.contracts import (
    DistilledKnowledgeUnitV1,
    RetrieveKnowledgeQueryV1,
)


class TestKnowledgeStore:
    """Tests for KnowledgeStore."""

    @pytest.fixture
    def store(self) -> Generator[KnowledgeStore, None, None]:
        with tempfile.TemporaryDirectory() as tmpdir:
            yield KnowledgeStore(workspace=tmpdir, knowledge_file=os.path.join(tmpdir, "knowledge.jsonl"))

    def test_store_and_retrieve(self, store: KnowledgeStore) -> None:
        unit = DistilledKnowledgeUnitV1(
            knowledge_id="kn_001",
            knowledge_type="error_pattern",
            pattern_summary="Null pointer exception",
            confidence=0.9,
            occurrence_count=1,
            related_findings=["sess_1"],
            extracted_insight="Check for null",
            metadata={"role": "director"},
        )
        store.store(unit)
        result = store.retrieve(RetrieveKnowledgeQueryV1(workspace=".", query="null pointer", top_k=5))
        assert len(result.knowledge_units) >= 1
        assert result.knowledge_units[0].knowledge_id == "kn_001"

    def test_retrieve_by_type(self, store: KnowledgeStore) -> None:
        unit = DistilledKnowledgeUnitV1(
            knowledge_id="kn_002",
            knowledge_type="success_pattern",
            pattern_summary="Fast build",
            confidence=0.8,
            occurrence_count=1,
            related_findings=["sess_2"],
            extracted_insight="Use caching",
        )
        store.store(unit)
        result = store.retrieve(
            RetrieveKnowledgeQueryV1(
                workspace=".",
                query="build",
                knowledge_type="success_pattern",
                top_k=5,
            )
        )
        assert len(result.knowledge_units) == 1

    def test_retrieve_by_role(self, store: KnowledgeStore) -> None:
        unit = DistilledKnowledgeUnitV1(
            knowledge_id="kn_003",
            knowledge_type="error_pattern",
            pattern_summary="Timeout",
            confidence=0.7,
            occurrence_count=1,
            related_findings=["sess_3"],
            extracted_insight="Increase timeout",
            metadata={"role": "pm"},
        )
        store.store(unit)
        result = store.retrieve(
            RetrieveKnowledgeQueryV1(
                workspace=".",
                query="timeout",
                role_filter="pm",
                top_k=5,
            )
        )
        assert len(result.knowledge_units) == 1

    def test_retrieve_min_confidence_filter(self, store: KnowledgeStore) -> None:
        unit = DistilledKnowledgeUnitV1(
            knowledge_id="kn_004",
            knowledge_type="error_pattern",
            pattern_summary="Low confidence bug",
            confidence=0.3,
            occurrence_count=1,
            related_findings=["sess_4"],
            extracted_insight="Uncertain",
        )
        store.store(unit)
        result = store.retrieve(
            RetrieveKnowledgeQueryV1(
                workspace=".",
                query="bug",
                min_confidence=0.5,
                top_k=5,
            )
        )
        assert len(result.knowledge_units) == 0

    def test_retrieve_top_k_limit(self, store: KnowledgeStore) -> None:
        for i in range(10):
            unit = DistilledKnowledgeUnitV1(
                knowledge_id=f"kn_{i:03d}",
                knowledge_type="error_pattern",
                pattern_summary=f"Bug {i}",
                confidence=0.5 + i * 0.05,
                occurrence_count=1,
                related_findings=[f"sess_{i}"],
                extracted_insight=f"Insight {i}",
            )
            store.store(unit)
        result = store.retrieve(RetrieveKnowledgeQueryV1(workspace=".", query="Bug", top_k=3))
        assert len(result.knowledge_units) == 3

    def test_merge_similar_knowledge(self, store: KnowledgeStore) -> None:
        unit1 = DistilledKnowledgeUnitV1(
            knowledge_id="kn_merge",
            knowledge_type="error_pattern",
            pattern_summary="Same bug",
            confidence=0.7,
            occurrence_count=1,
            related_findings=["sess_a"],
            extracted_insight="Fix it",
        )
        unit2 = DistilledKnowledgeUnitV1(
            knowledge_id="kn_merge2",
            knowledge_type="error_pattern",
            pattern_summary="Same bug",
            confidence=0.8,
            occurrence_count=1,
            related_findings=["sess_b"],
            extracted_insight="Fix it",
        )
        store.store(unit1)
        store.store(unit2)
        all_units = store.get_all()
        assert len(all_units) == 1
        merged = all_units[0]
        assert merged.occurrence_count == 2
        assert merged.confidence == 0.8
        assert set(merged.related_findings) == {"sess_a", "sess_b"}

    def test_get_all_empty(self, store: KnowledgeStore) -> None:
        assert store.get_all() == []

    def test_clear(self, store: KnowledgeStore) -> None:
        unit = DistilledKnowledgeUnitV1(
            knowledge_id="kn_005",
            knowledge_type="error_pattern",
            pattern_summary="Bug",
            confidence=0.9,
            occurrence_count=1,
            related_findings=["sess_5"],
            extracted_insight="Fix",
        )
        store.store(unit)
        store.clear()
        assert store.get_all() == []

    def test_persistence_reload(self, store: KnowledgeStore) -> None:
        unit = DistilledKnowledgeUnitV1(
            knowledge_id="kn_006",
            knowledge_type="error_pattern",
            pattern_summary="Persistent bug",
            confidence=0.9,
            occurrence_count=1,
            related_findings=["sess_6"],
            extracted_insight="Fix",
        )
        store.store(unit)
        # Create new store pointing to same file
        store2 = KnowledgeStore(workspace=store._workspace, knowledge_file=store._knowledge_file)
        result = store2.retrieve(RetrieveKnowledgeQueryV1(workspace=".", query="Persistent", top_k=5))
        assert len(result.knowledge_units) == 1
        assert result.knowledge_units[0].knowledge_id == "kn_006"

    def test_retrieve_no_match(self, store: KnowledgeStore) -> None:
        result = store.retrieve(RetrieveKnowledgeQueryV1(workspace=".", query="nonexistent", top_k=5))
        assert result.knowledge_units == []
        assert result.total_available == 0
