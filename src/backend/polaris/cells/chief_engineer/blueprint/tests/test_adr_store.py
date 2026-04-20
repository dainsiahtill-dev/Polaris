"""Tests for ADRStore.

Covers blueprint creation, ADR proposal, compilation, reversion,
delta application, and disk persistence roundtrips.
"""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.internal.adr_store import (
    ADRStore,
    BlueprintADR,
    BlueprintBase,
    _apply_delta,
)


@pytest.fixture
def store() -> Generator[ADRStore, None, None]:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield ADRStore(workspace=tmpdir)


class TestCreateBlueprint:
    """Tests for blueprint creation."""

    def test_create_basic(self, store: ADRStore) -> None:
        bp = store.create_blueprint("bp1", {"title": "Plan"})
        assert isinstance(bp, BlueprintBase)
        assert bp.blueprint_id == "bp1"
        assert bp.version == 1
        assert bp.base_schema == {"title": "Plan"}
        assert bp.status == "approved"

    def test_create_persists_to_disk(self, store: ADRStore) -> None:
        store.create_blueprint("bp1", {"title": "Plan"})
        ids = store._persistence.list_all()
        assert "bp1" in ids


class TestProposeADR:
    """Tests for ADR proposal."""

    def test_propose_basic(self, store: ADRStore) -> None:
        store.create_blueprint("bp1", {"title": "Plan"})
        adr = store.propose_adr(
            blueprint_id="bp1",
            related_task_ids=["T-1"],
            decision="Add cache",
            context="Slow API",
            delta={"type": "add_step", "payload": {"step": {"title": "Cache"}}},
        )
        assert isinstance(adr, BlueprintADR)
        assert adr.adr_id == "ADR-bp1-001"
        assert adr.status == "proposed"

    def test_propose_sequence_increments(self, store: ADRStore) -> None:
        store.create_blueprint("bp1", {})
        adr1 = store.propose_adr("bp1", [], "d1", "c1", {})
        adr2 = store.propose_adr("bp1", [], "d2", "c2", {})
        assert adr1.adr_id == "ADR-bp1-001"
        assert adr2.adr_id == "ADR-bp1-002"

    def test_propose_missing_blueprint_raises(self, store: ADRStore) -> None:
        with pytest.raises(ValueError, match="Blueprint bp_missing not found"):
            store.propose_adr("bp_missing", [], "d", "c", {})


class TestCompile:
    """Tests for ADR compilation."""

    def test_compile_no_adrs(self, store: ADRStore) -> None:
        store.create_blueprint("bp1", {"title": "Plan", "version": 1})
        compiled = store.compile("bp1")
        assert compiled == {"title": "Plan", "version": 1}

    def test_compile_applies_add_step(self, store: ADRStore) -> None:
        store.create_blueprint("bp1", {"construction_steps": []})
        store.propose_adr(
            "bp1",
            [],
            "Add step",
            "",
            {"type": "add_step", "payload": {"after_step": 0, "step": {"title": "S1"}}},
        )
        compiled = store.compile("bp1")
        assert compiled["construction_steps"] == [{"title": "S1"}]

    def test_compile_marks_adrs_compiled(self, store: ADRStore) -> None:
        store.create_blueprint("bp1", {})
        adr = store.propose_adr("bp1", [], "d", "c", {})
        assert adr.status == "proposed"
        store.compile("bp1")
        assert adr.status == "compiled"
        assert adr.compiled_at_ms is not None

    def test_compile_increments_version(self, store: ADRStore) -> None:
        store.create_blueprint("bp1", {})
        store.propose_adr("bp1", [], "d", "c", {})
        bp = store._blueprints["bp1"]
        assert bp.version == 1
        store.compile("bp1")
        assert bp.version == 2

    def test_compile_skips_reverted(self, store: ADRStore) -> None:
        store.create_blueprint("bp1", {"construction_steps": []})
        adr = store.propose_adr(
            "bp1",
            [],
            "Add step",
            "",
            {"type": "add_step", "payload": {"after_step": 0, "step": {"title": "S1"}}},
        )
        store.revert_adr(adr.adr_id)
        compiled = store.compile("bp1")
        assert compiled["construction_steps"] == []


class TestRevertADR:
    """Tests for ADR reversion."""

    def test_revert_existing(self, store: ADRStore) -> None:
        store.create_blueprint("bp1", {})
        adr = store.propose_adr("bp1", [], "d", "c", {})
        assert store.revert_adr(adr.adr_id) is True
        assert adr.status == "reverted"

    def test_revert_missing(self, store: ADRStore) -> None:
        assert store.revert_adr("ADR-nope-001") is False


class TestHistory:
    """Tests for blueprint history queries."""

    def test_history_empty(self, store: ADRStore) -> None:
        store.create_blueprint("bp1", {})
        assert store.get_blueprint_history("bp1") == []

    def test_history_returns_summaries(self, store: ADRStore) -> None:
        store.create_blueprint("bp1", {})
        store.propose_adr("bp1", ["T-1"], "Add cache", "Slow API", {})
        hist = store.get_blueprint_history("bp1")
        assert len(hist) == 1
        assert hist[0]["decision"] == "Add cache"
        assert hist[0]["context"] == "Slow API"

    def test_history_missing_blueprint(self, store: ADRStore) -> None:
        assert store.get_blueprint_history("missing") == []


class TestPersistenceRoundtrip:
    """Tests that ADRStore survives process restart via disk."""

    def test_reload_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store1 = ADRStore(workspace=tmpdir)
            store1.create_blueprint("bp1", {"title": "Plan"})
            store1.propose_adr("bp1", [], "d", "c", {})
            store1.compile("bp1")

            store2 = ADRStore(workspace=tmpdir)
            bp = store2._blueprints["bp1"]
            assert bp.version == 2
            assert len(bp.adrs) == 1
            assert bp.adrs[0].status == "compiled"


class TestApplyDelta:
    """Unit tests for the delta application logic."""

    def test_add_step(self) -> None:
        schema: dict[str, Any] = {"construction_steps": [{"title": "A"}]}
        delta = {"type": "add_step", "payload": {"after_step": 0, "step": {"title": "B"}}}
        result = _apply_delta(schema, delta)
        assert result["construction_steps"] == [{"title": "B"}, {"title": "A"}]

    def test_add_step_default_append(self) -> None:
        schema: dict[str, Any] = {"construction_steps": [{"title": "A"}]}
        delta = {"type": "add_step", "payload": {"step": {"title": "B"}}}
        result = _apply_delta(schema, delta)
        assert result["construction_steps"] == [{"title": "A"}, {"title": "B"}]

    def test_modify_step(self) -> None:
        schema: dict[str, Any] = {"construction_steps": [{"title": "A", "risk": "low"}]}
        delta = {"type": "modify_step", "payload": {"step_index": 0, "changes": {"risk": "high"}}}
        result = _apply_delta(schema, delta)
        assert result["construction_steps"][0]["risk"] == "high"

    def test_remove_step(self) -> None:
        schema: dict[str, Any] = {"construction_steps": [{"title": "A"}, {"title": "B"}]}
        delta = {"type": "remove_step", "payload": {"step_index": 0}}
        result = _apply_delta(schema, delta)
        assert result["construction_steps"] == [{"title": "B"}]

    def test_add_file(self) -> None:
        schema: dict[str, Any] = {}
        delta = {"type": "add_file", "payload": {"category": "modified_files", "file": "src/x.py"}}
        result = _apply_delta(schema, delta)
        assert result["scope_for_apply"]["modified_files"] == ["src/x.py"]

    def test_remove_file(self) -> None:
        schema: dict[str, Any] = {"scope_for_apply": {"modified_files": ["src/x.py"]}}
        delta = {"type": "remove_file", "payload": {"file": "src/x.py"}}
        result = _apply_delta(schema, delta)
        assert result["scope_for_apply"]["modified_files"] == []

    def test_change_scope(self) -> None:
        schema: dict[str, Any] = {"scope_for_apply": {"old": []}}
        delta = {"type": "change_scope", "payload": {"new_scope": {"new": []}}}
        result = _apply_delta(schema, delta)
        assert result["scope_for_apply"] == {"new": []}

    def test_change_risk(self) -> None:
        schema: dict[str, Any] = {}
        delta = {"type": "change_risk", "payload": {"risk": "oom_possible"}}
        result = _apply_delta(schema, delta)
        assert result["risk_flags"] == ["oom_possible"]

    def test_unknown_delta_type_is_noop(self) -> None:
        schema: dict[str, Any] = {"x": 1}
        delta = {"type": "unknown", "payload": {}}
        result = _apply_delta(schema, delta)
        assert result == {"x": 1}
