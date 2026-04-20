"""Tests for BlueprintPersistence.

Covers atomic writes, loads, deletes, and batch operations.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator

import pytest
from polaris.cells.chief_engineer.blueprint.internal.blueprint_persistence import (
    BlueprintPersistence,
)


@pytest.fixture
def persistence() -> Generator[BlueprintPersistence, None, None]:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield BlueprintPersistence(workspace=tmpdir)


class TestSaveAndLoad:
    """Tests for basic save/load roundtrip."""

    def test_save_and_load(self, persistence: BlueprintPersistence) -> None:
        data = {"blueprint_id": "bp1", "title": "Test Plan", "version": 1}
        persistence.save("bp1", data)
        loaded = persistence.load("bp1")
        assert loaded == data

    def test_save_and_load_includes_status(self, persistence: BlueprintPersistence) -> None:
        data = {"blueprint_id": "bp1", "status": "approved", "version": 1}
        persistence.save("bp1", data)
        loaded = persistence.load("bp1")
        assert loaded == data

    def test_load_missing_returns_none(self, persistence: BlueprintPersistence) -> None:
        assert persistence.load("nonexistent") is None

    def test_save_overwrites_existing(self, persistence: BlueprintPersistence) -> None:
        persistence.save("bp1", {"version": 1})
        persistence.save("bp1", {"version": 2})
        loaded = persistence.load("bp1")
        assert loaded == {"version": 2}


class TestAtomicWrite:
    """Tests for atomic write semantics."""

    def test_no_temp_file_left_behind(self, persistence: BlueprintPersistence) -> None:
        persistence.save("bp1", {"x": 1})
        tmp_files = list(persistence._dir.glob("*.tmp"))
        assert not tmp_files

    def test_directory_created_lazily(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = os.path.join(tmpdir, "nested", "workspace")
            bp = BlueprintPersistence(workspace=workspace)
            bp.save("bp1", {"x": 1})
            assert os.path.isdir(os.path.join(workspace, "runtime", "blueprints"))


class TestDelete:
    """Tests for deletion."""

    def test_delete_existing(self, persistence: BlueprintPersistence) -> None:
        persistence.save("bp1", {"x": 1})
        assert persistence.delete("bp1") is True
        assert persistence.load("bp1") is None

    def test_delete_missing(self, persistence: BlueprintPersistence) -> None:
        assert persistence.delete("bp1") is False


class TestListAll:
    """Tests for listing persisted blueprints."""

    def test_empty_list(self, persistence: BlueprintPersistence) -> None:
        assert persistence.list_all() == []

    def test_sorted_ids(self, persistence: BlueprintPersistence) -> None:
        persistence.save("bp_z", {"x": 1})
        persistence.save("bp_a", {"x": 2})
        persistence.save("bp_m", {"x": 3})
        assert persistence.list_all() == ["bp_a", "bp_m", "bp_z"]


class TestLoadAll:
    """Tests for bulk loading."""

    def test_load_all_skips_invalid_json(self, persistence: BlueprintPersistence) -> None:
        persistence.save("bp1", {"x": 1})
        bad_path = persistence._dir / "bp2.json"
        with open(bad_path, "w", encoding="utf-8") as f:
            f.write("not json")
        all_data = persistence.load_all()
        assert len(all_data) == 1
        assert all_data[0]["x"] == 1

    def test_load_all_order(self, persistence: BlueprintPersistence) -> None:
        persistence.save("bp1", {"id": "bp1"})
        persistence.save("bp2", {"id": "bp2"})
        all_data = persistence.load_all()
        ids = [d["id"] for d in all_data]
        assert ids == ["bp1", "bp2"]
