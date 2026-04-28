"""Tests for context_pack_freshness governance rule.

Verifies that the ContextPackFreshnessChecker correctly:
1. Identifies cells with context packs
2. Flags stale packs (>7 days old)
3. Flags missing packs
4. Uses the correct rule_id

Rule ID: context_pack_is_primary_ai_entry
Reference: docs/governance/ci/scripts/check_context_pack_freshness.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest
import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[4]
CHECK_SCRIPT = BACKEND_ROOT / "docs" / "governance" / "ci" / "scripts" / "check_context_pack_freshness.py"
FITNESS_RULES_FILE = BACKEND_ROOT / "docs" / "governance" / "ci" / "fitness-rules.yaml"

# Dynamically import the check script module using importlib
_script_spec = importlib.util.spec_from_file_location("check_context_pack_freshness", CHECK_SCRIPT)
if _script_spec and _script_spec.loader:
    _check_module = importlib.util.module_from_spec(_script_spec)
    sys.modules["check_context_pack_freshness"] = _check_module
    _script_spec.loader.exec_module(_check_module)

    # Import from the loaded module
    ContextPackFreshnessChecker = _check_module.ContextPackFreshnessChecker
    FRESHNESS_THRESHOLD_SECONDS = _check_module.FRESHNESS_THRESHOLD_SECONDS
else:
    raise ImportError(f"Could not load check_context_pack_freshness from {CHECK_SCRIPT}")


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace with basic structure."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    # Create polaris/cells directory
    (workspace / "polaris" / "cells").mkdir(parents=True)
    # Create docs/graph/catalog directory
    (workspace / "docs" / "graph" / "catalog").mkdir(parents=True)

    return workspace


@pytest.fixture
def cells_yaml_content() -> str:
    """Minimal cells.yaml content for testing."""
    return yaml.dump(
        {
            "cells": [
                {"id": "test.cell_a", "public_contracts": {"modules": ["test.cell_a.public.contracts"]}},
                {"id": "test.cell_b", "public_contracts": {"modules": ["test.cell_b.public.contracts"]}},
                {"id": "test.cell_c", "public_contracts": {"modules": ["test.cell_c.public.contracts"]}},
                {"id": "test.cell_d", "public_contracts": {"modules": ["test.cell_d.public.contracts"]}},
            ]
        }
    )


@pytest.fixture
def valid_context_pack() -> str:
    """Return a valid context.pack.json content."""
    return json.dumps(
        {
            "cell_id": "test.cell",
            "version": "1.0.0",
            "descriptors": [],
            "generated_at": "2026-04-24T00:00:00Z",
        }
    )


class TestContextPackFreshnessRuleId:
    """Test that the rule_id is correctly set."""

    def test_rule_id_is_context_pack_is_primary_ai_entry(self) -> None:
        """Verify the rule_id is 'context_pack_is_primary_ai_entry'."""
        checker = ContextPackFreshnessChecker(workspace=BACKEND_ROOT)
        result = checker.check_context_pack_freshness()

        assert result.rule_id == "context_pack_is_primary_ai_entry"

    def test_fitness_rules_file_declares_rule(self) -> None:
        """Verify fitness-rules.yaml declares the context_pack_is_primary_ai_entry rule."""
        payload = yaml.safe_load(FITNESS_RULES_FILE.read_text(encoding="utf-8")) or {}
        rules = payload.get("rules", [])

        rule_ids = {str(item.get("id") or "").strip() for item in rules if isinstance(item, dict)}
        assert "context_pack_is_primary_ai_entry" in rule_ids


class TestContextPackFreshnessChecker:
    """Test ContextPackFreshnessChecker functionality."""

    def test_identifies_cells_with_context_packs(
        self,
        temp_workspace: Path,
        cells_yaml_content: str,
        valid_context_pack: str,
    ) -> None:
        """Test that checker correctly identifies cells with context packs."""
        # Setup: Create cells.yaml and cell directories with context packs
        cells_yaml_path = temp_workspace / "docs" / "graph" / "catalog" / "cells.yaml"
        cells_yaml_path.write_text(cells_yaml_content, encoding="utf-8")

        cell_a_path = temp_workspace / "polaris" / "cells" / "test" / "cell_a"
        (cell_a_path / "generated").mkdir(parents=True)
        (cell_a_path / "generated" / "context.pack.json").write_text(valid_context_pack, encoding="utf-8")

        # Run checker
        checker = ContextPackFreshnessChecker(workspace=temp_workspace)
        result = checker.check_context_pack_freshness()

        # Verify: Cell with pack should be found and flagged as fresh
        assert "test.cell_a" in str(result.evidence)
        assert "context.pack.json is fresh" in str(result.evidence)

    def test_flags_missing_packs(
        self,
        temp_workspace: Path,
        cells_yaml_content: str,
    ) -> None:
        """Test that checker flags cells with missing context packs."""
        # Setup: Create cells.yaml but no context packs
        cells_yaml_path = temp_workspace / "docs" / "graph" / "catalog" / "cells.yaml"
        cells_yaml_path.write_text(cells_yaml_content, encoding="utf-8")

        # Only create cell_a with a pack, cells b, c, d should be missing
        cell_a_path = temp_workspace / "polaris" / "cells" / "test" / "cell_a"
        (cell_a_path / "generated").mkdir(parents=True)
        (cell_a_path / "generated" / "context.pack.json").write_text(
            json.dumps({"cell_id": "test.cell_a"}), encoding="utf-8"
        )

        # Run checker
        checker = ContextPackFreshnessChecker(workspace=temp_workspace)
        result = checker.check_context_pack_freshness()

        # Verify: Missing packs should be flagged
        assert not result.passed
        assert "Missing context.pack.json: test.cell_b" in result.violations
        assert "Missing context.pack.json: test.cell_c" in result.violations
        assert "Missing context.pack.json: test.cell_d" in result.violations

    def test_flags_stale_packs(
        self,
        temp_workspace: Path,
        cells_yaml_content: str,
    ) -> None:
        """Test that checker flags packs older than 7 days as stale."""
        # Setup: Create cells.yaml and cell with a stale context pack
        cells_yaml_path = temp_workspace / "docs" / "graph" / "catalog" / "cells.yaml"
        cells_yaml_path.write_text(cells_yaml_content, encoding="utf-8")

        cell_a_path = temp_workspace / "polaris" / "cells" / "test" / "cell_a"
        (cell_a_path / "generated").mkdir(parents=True)

        pack_path = cell_a_path / "generated" / "context.pack.json"
        pack_path.write_text(json.dumps({"cell_id": "test.cell_a"}), encoding="utf-8")

        # Set file mtime to 8 days ago (stale)
        stale_time = time.time() - (FRESHNESS_THRESHOLD_SECONDS + 86400)
        os.utime(pack_path, (stale_time, stale_time))

        # Run checker
        checker = ContextPackFreshnessChecker(workspace=temp_workspace)
        result = checker.check_context_pack_freshness()

        # Verify: Stale packs should be flagged
        assert not result.passed
        assert any("stale" in v.lower() for v in result.violations)
        assert any("test.cell_a" in v for v in result.violations)

    def test_fresh_packs_not_flagged(
        self,
        temp_workspace: Path,
        cells_yaml_content: str,
    ) -> None:
        """Test that fresh packs (<7 days) are not flagged."""
        # Setup: Create cells.yaml and cell with a fresh context pack
        cells_yaml_content_fresh = yaml.dump(
            {
                "cells": [
                    {"id": "test.cell_a", "public_contracts": {"modules": ["test.cell_a.public.contracts"]}},
                ]
            }
        )
        cells_yaml_path = temp_workspace / "docs" / "graph" / "catalog" / "cells.yaml"
        cells_yaml_path.write_text(cells_yaml_content_fresh, encoding="utf-8")

        cell_a_path = temp_workspace / "polaris" / "cells" / "test" / "cell_a"
        (cell_a_path / "generated").mkdir(parents=True)

        pack_path = cell_a_path / "generated" / "context.pack.json"
        pack_path.write_text(json.dumps({"cell_id": "test.cell_a"}), encoding="utf-8")

        # Set file mtime to 1 day ago (fresh)
        fresh_time = time.time() - 86400
        os.utime(pack_path, (fresh_time, fresh_time))

        # Run checker
        checker = ContextPackFreshnessChecker(workspace=temp_workspace)
        result = checker.check_context_pack_freshness()

        # Verify: Fresh packs should not cause violations
        assert result.passed
        assert not any("stale" in v.lower() for v in result.violations)
        assert not any("test.cell_a" in v for v in result.violations)

    def test_validates_pack_structure(
        self,
        temp_workspace: Path,
        cells_yaml_content: str,
    ) -> None:
        """Test that checker validates pack structure."""
        # Setup: Create cells.yaml and cell with invalid context pack
        cells_yaml_path = temp_workspace / "docs" / "graph" / "catalog" / "cells.yaml"
        cells_yaml_path.write_text(cells_yaml_content, encoding="utf-8")

        cell_a_path = temp_workspace / "polaris" / "cells" / "test" / "cell_a"
        (cell_a_path / "generated").mkdir(parents=True)

        # Write pack without required 'cell_id' or 'id' field
        pack_path = cell_a_path / "generated" / "context.pack.json"
        pack_path.write_text(json.dumps({"version": "1.0.0"}), encoding="utf-8")

        # Run checker
        checker = ContextPackFreshnessChecker(workspace=temp_workspace)
        result = checker.check_context_pack_freshness()

        # Verify: Invalid pack should be flagged
        assert not result.passed
        assert any("cell_id" in v or "id" in v for v in result.violations)

    def test_handles_invalid_json(
        self,
        temp_workspace: Path,
        cells_yaml_content: str,
    ) -> None:
        """Test that checker handles invalid JSON in pack files."""
        # Setup: Create cells.yaml and cell with invalid JSON
        cells_yaml_path = temp_workspace / "docs" / "graph" / "catalog" / "cells.yaml"
        cells_yaml_path.write_text(cells_yaml_content, encoding="utf-8")

        cell_a_path = temp_workspace / "polaris" / "cells" / "test" / "cell_a"
        (cell_a_path / "generated").mkdir(parents=True)

        # Write invalid JSON
        pack_path = cell_a_path / "generated" / "context.pack.json"
        pack_path.write_text("{ invalid json }", encoding="utf-8")

        # Run checker
        checker = ContextPackFreshnessChecker(workspace=temp_workspace)
        result = checker.check_context_pack_freshness()

        # Verify: Invalid JSON should be flagged
        assert not result.passed
        assert any("JSON" in v or "json" in v for v in result.violations)


class TestContextPackPathDiscovery:
    """Test that checker finds context packs in correct locations."""

    def test_finds_pack_in_generated_directory(
        self,
        temp_workspace: Path,
    ) -> None:
        """Test that checker finds pack in generated/ subdirectory."""
        checker = ContextPackFreshnessChecker(workspace=temp_workspace)

        cell_path = temp_workspace / "polaris" / "cells" / "test" / "cell_a"
        (cell_path / "generated").mkdir(parents=True)
        generated_path = cell_path / "generated" / "context.pack.json"
        generated_path.write_text(json.dumps({"cell_id": "test.cell_a"}), encoding="utf-8")

        found_path = checker._find_context_pack_path("test.cell_a")
        assert found_path is not None
        assert found_path == generated_path

    def test_finds_pack_at_root_if_not_in_generated(
        self,
        temp_workspace: Path,
    ) -> None:
        """Test that checker falls back to root of cell directory."""
        checker = ContextPackFreshnessChecker(workspace=temp_workspace)

        cell_path = temp_workspace / "polaris" / "cells" / "test" / "cell_b"
        root_path = cell_path / "context.pack.json"
        root_path.parent.mkdir(parents=True, exist_ok=True)
        root_path.write_text(json.dumps({"cell_id": "test.cell_b"}), encoding="utf-8")

        found_path = checker._find_context_pack_path("test.cell_b")
        assert found_path is not None
        assert found_path == root_path

    def test_returns_none_when_pack_not_found(
        self,
        temp_workspace: Path,
    ) -> None:
        """Test that checker returns None when pack is not found."""
        checker = ContextPackFreshnessChecker(workspace=temp_workspace)

        found_path = checker._find_context_pack_path("nonexistent.cell")
        assert found_path is None


class TestContextPackFreshnessIntegration:
    """Integration tests using the actual check script."""

    def test_script_runs_successfully_on_backend(
        self,
        temp_workspace: Path,
    ) -> None:
        """Test that the check script can be executed."""
        # Use the real backend workspace for this test
        checker = ContextPackFreshnessChecker(workspace=BACKEND_ROOT)
        result = checker.check_context_pack_freshness()

        # Just verify the script runs without errors
        assert result.rule_id == "context_pack_is_primary_ai_entry"
        assert isinstance(result.passed, bool)
        assert isinstance(result.evidence, list)
        assert isinstance(result.violations, list)


class TestContextPackAgeFormatting:
    """Test the _format_age helper method."""

    def test_format_seconds(self) -> None:
        """Test formatting of age in seconds."""
        checker = ContextPackFreshnessChecker(workspace=BACKEND_ROOT)
        checker.current_time = 1000.0

        # 30 seconds ago
        assert checker._format_age(970.0) == "30s ago"

    def test_format_minutes(self) -> None:
        """Test formatting of age in minutes."""
        checker = ContextPackFreshnessChecker(workspace=BACKEND_ROOT)
        checker.current_time = 3600.0

        # 30 minutes ago
        assert checker._format_age(3600.0 - 1800.0) == "30m ago"

    def test_format_hours(self) -> None:
        """Test formatting of age in hours."""
        checker = ContextPackFreshnessChecker(workspace=BACKEND_ROOT)
        checker.current_time = 86400.0

        # 12 hours ago
        assert checker._format_age(86400.0 - 43200.0) == "12.0h ago"

    def test_format_days(self) -> None:
        """Test formatting of age in days."""
        checker = ContextPackFreshnessChecker(workspace=BACKEND_ROOT)
        checker.current_time = 86400.0 * 10

        # 5 days ago
        assert checker._format_age(86400.0 * 5) == "5.0d ago"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
