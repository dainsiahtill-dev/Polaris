"""Tests for ConstructionStore persistence integration.

Covers backward compatibility (no persistence), dual-write semantics,
and process-restart recovery via BlueprintPersistence.
"""

from __future__ import annotations

import tempfile
from collections.abc import Generator

import pytest
from polaris.cells.chief_engineer.blueprint.internal.blueprint_persistence import (
    BlueprintPersistence,
)
from polaris.cells.chief_engineer.blueprint.internal.chief_engineer_agent import (
    ConstructionBlueprint,
    ConstructionStore,
)


@pytest.fixture
def persistent_store() -> Generator[ConstructionStore, None, None]:
    with tempfile.TemporaryDirectory() as tmpdir:
        persistence = BlueprintPersistence(workspace=tmpdir)
        yield ConstructionStore(persistence=persistence)


class TestBackwardCompatibility:
    """Tests that ConstructionStore works without persistence."""

    def test_save_and_get_in_memory_only(self) -> None:
        store = ConstructionStore()
        bp = ConstructionBlueprint(blueprint_id="bp1", task_id="t1", title="Plan")
        store.save(bp)
        assert store.get("bp1") == bp

    def test_list_all_in_memory(self) -> None:
        store = ConstructionStore()
        store.save(ConstructionBlueprint(blueprint_id="bp1", task_id="t1", title="A"))
        store.save(ConstructionBlueprint(blueprint_id="bp2", task_id="t1", title="B"))
        rows = store.list_all()
        assert len(rows) == 2


class TestPersistenceIntegration:
    """Tests for dual-write and recovery behavior."""

    def test_save_persists_to_disk(self, persistent_store: ConstructionStore) -> None:
        bp = ConstructionBlueprint(blueprint_id="bp1", task_id="t1", title="Plan")
        persistent_store.save(bp)
        # Simulate process restart by creating a new store against the same workspace
        assert persistent_store._persistence is not None
        new_store = ConstructionStore(persistence=persistent_store._persistence)
        recovered = new_store.get("bp1")
        assert recovered is not None
        assert recovered.blueprint_id == "bp1"
        assert recovered.title == "Plan"

    def test_get_hydrates_from_disk(self, persistent_store: ConstructionStore) -> None:
        bp = ConstructionBlueprint(blueprint_id="bp1", task_id="t1", title="Plan")
        persistent_store.save(bp)
        # Clear memory cache
        persistent_store._by_id.clear()
        recovered = persistent_store.get("bp1")
        assert recovered is not None
        assert recovered.title == "Plan"

    def test_list_all_hydrates_missing_entries(self, persistent_store: ConstructionStore) -> None:
        bp = ConstructionBlueprint(blueprint_id="bp1", task_id="t1", title="Plan")
        persistent_store.save(bp)
        persistent_store._by_id.clear()
        rows = persistent_store.list_all()
        assert len(rows) == 1
        assert rows[0].blueprint_id == "bp1"

    def test_list_by_task_hydrates_from_disk(self, persistent_store: ConstructionStore) -> None:
        persistent_store.save(ConstructionBlueprint(blueprint_id="bp1", task_id="t1", title="A"))
        persistent_store.save(ConstructionBlueprint(blueprint_id="bp2", task_id="t2", title="B"))
        persistent_store._by_id.clear()
        rows = persistent_store.list_by_task("t1")
        assert len(rows) == 1
        assert rows[0].blueprint_id == "bp1"

    def test_memory_cache_preferred_over_disk(self, persistent_store: ConstructionStore) -> None:
        bp = ConstructionBlueprint(blueprint_id="bp1", task_id="t1", title="Original")
        persistent_store.save(bp)
        # Mutate in-memory copy without saving
        cached = persistent_store._by_id["bp1"]
        cached.title = "Mutated"
        recovered = persistent_store.get("bp1")
        assert recovered is not None
        assert recovered.title == "Mutated"

    def test_round_trip_complex_fields(self, persistent_store: ConstructionStore) -> None:
        bp = ConstructionBlueprint(
            blueprint_id="bp1",
            task_id="t1",
            title="Plan",
            modules=[{"name": "mod1"}],
            files=[{"path": "src/x.py"}],
            methods=[{"name": "foo"}],
            dependencies={"a": ["b"]},
            scope_for_apply=["src/"],
            constraints={"max_lines": 100},
            flexible_zone={"allow_refactor": True},
            escalation_triggers=["oom"],
            status="approved",
        )
        persistent_store.save(bp)
        persistent_store._by_id.clear()
        recovered = persistent_store.get("bp1")
        assert recovered is not None
        assert recovered.modules == [{"name": "mod1"}]
        assert recovered.dependencies == {"a": ["b"]}
        assert recovered.scope_for_apply == ["src/"]
        assert recovered.constraints == {"max_lines": 100}
        assert recovered.escalation_triggers == ["oom"]
        assert recovered.status == "approved"
