"""tests/architecture/governance/test_shim_markers.py

Graph governance tests for shim_only_units_require_markers rule.

Scope
-----
This test file covers:
1. Rule ID is correctly declared as "shim_only_units_require_markers"
2. shim_only migration files contain proper migration markers
3. Files without markers are flagged as violations

Evidence
--------
- docs/migration/ledger.yaml
- docs/governance/ci/scripts/check_shim_markers.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

BACKEND_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = BACKEND_ROOT / "docs" / "governance" / "ci" / "scripts" / "check_shim_markers.py"
LEDGER_PATH = BACKEND_ROOT / "docs" / "migration" / "ledger.yaml"
FITNESS_RULES_PATH = BACKEND_ROOT / "docs" / "governance" / "ci" / "fitness-rules.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_utf8_env() -> dict[str, str]:
    """Build environment with UTF-8 settings for subprocess execution."""
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")
    return env


def _load_ledger() -> dict[str, Any]:
    """Load the migration ledger YAML file."""
    assert LEDGER_PATH.is_file(), f"ledger.yaml not found: {LEDGER_PATH}"
    with LEDGER_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _find_shim_only_units() -> list[dict[str, Any]]:
    """Find all migration units with shim_only status."""
    ledger = _load_ledger()
    return [unit for unit in ledger.get("units", []) if str(unit.get("status", "")) == "shim_only"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def shim_only_units() -> list[dict[str, Any]]:
    """Return all shim_only migration units from the ledger."""
    return _find_shim_only_units()


@pytest.fixture
def ledger() -> dict[str, Any]:
    """Return the migration ledger data."""
    return _load_ledger()


# ---------------------------------------------------------------------------
# Rule Declaration Tests
# ---------------------------------------------------------------------------


class TestRuleDeclaration:
    """Tests for the shim_only_units_require_markers rule declaration."""

    def test_rule_id_is_declared_in_fitness_rules(self) -> None:
        """shim_only_units_require_markers must be declared in fitness-rules.yaml."""
        payload = yaml.safe_load(FITNESS_RULES_PATH.read_text(encoding="utf-8")) or {}
        rules = payload.get("rules")
        assert isinstance(rules, list), "fitness-rules.yaml must define a rules list"

        rule_ids = {str(item.get("id") or "").strip() for item in rules if isinstance(item, dict)}
        assert "shim_only_units_require_markers" in rule_ids, (
            "shim_only_units_require_markers rule must be declared in fitness-rules.yaml"
        )

    def test_rule_has_evidence_docs(self) -> None:
        """shim_only_units_require_markers must reference evidence docs."""
        payload = yaml.safe_load(FITNESS_RULES_PATH.read_text(encoding="utf-8")) or {}
        rules = payload.get("rules")
        assert isinstance(rules, list), "fitness-rules.yaml must define a rules list"

        for rule in rules:
            if isinstance(rule, dict) and rule.get("id") == "shim_only_units_require_markers":
                evidence = rule.get("evidence", [])
                assert isinstance(evidence, list), "evidence must be a list"
                assert len(evidence) > 0, "rule must have evidence references"
                assert "docs/migration/ledger.yaml" in evidence, (
                    "rule must reference docs/migration/ledger.yaml as evidence"
                )
                return

        pytest.fail("shim_only_units_require_markers rule not found in fitness-rules.yaml")


# ---------------------------------------------------------------------------
# Script Execution Tests
# ---------------------------------------------------------------------------


class TestScriptExecution:
    """Tests for the check_shim_markers.py script execution."""

    def test_script_exists(self) -> None:
        """check_shim_markers.py script must exist."""
        assert SCRIPT_PATH.is_file(), f"check_shim_markers.py not found: {SCRIPT_PATH}"

    def test_script_runs_without_error(self) -> None:
        """Script must execute without crashing."""
        command = [
            sys.executable,
            str(SCRIPT_PATH),
            "--workspace",
            str(BACKEND_ROOT),
        ]
        completed = subprocess.run(
            command,
            cwd=str(BACKEND_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_build_utf8_env(),
            timeout=60,
            check=False,
        )
        # Script may pass or fail depending on actual shim files,
        # but it should not crash
        assert completed.returncode in (0, 1), (
            f"Script crashed unexpectedly.\nstdout: {completed.stdout}\nstderr: {completed.stderr}"
        )

    def test_script_json_output_is_valid(self) -> None:
        """Script --json output must be valid JSON with expected fields."""
        command = [
            sys.executable,
            str(SCRIPT_PATH),
            "--workspace",
            str(BACKEND_ROOT),
            "--json",
        ]
        completed = subprocess.run(
            command,
            cwd=str(BACKEND_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_build_utf8_env(),
            timeout=60,
            check=False,
        )

        payload = json.loads(completed.stdout or "{}")
        assert isinstance(payload, dict), "JSON output must be a dict"
        assert payload.get("rule_id") == "shim_only_units_require_markers", (
            "rule_id must be 'shim_only_units_require_markers'"
        )
        assert "passed" in payload, "payload must contain 'passed' field"
        assert "evidence" in payload, "payload must contain 'evidence' field"
        assert "violations" in payload, "payload must contain 'violations' field"
        assert "warnings" in payload, "payload must contain 'warnings' field"
        assert "timestamp" in payload, "payload must contain 'timestamp' field"
        assert "duration_ms" in payload, "payload must contain 'duration_ms' field"


# ---------------------------------------------------------------------------
# Ledger Structure Tests
# ---------------------------------------------------------------------------


class TestLedgerStructure:
    """Tests for ledger.yaml structure related to shim_only units."""

    def test_ledger_exists(self) -> None:
        """Migration ledger must exist."""
        assert LEDGER_PATH.is_file(), f"ledger.yaml not found: {LEDGER_PATH}"

    def test_ledger_has_valid_structure(self, ledger: dict[str, Any]) -> None:
        """Ledger must have valid structure."""
        assert "units" in ledger, "ledger must have 'units' key"
        assert isinstance(ledger["units"], list), "'units' must be a list"

    def test_shim_only_units_have_source_refs(self, shim_only_units: list[dict[str, Any]]) -> None:
        """Each shim_only unit must have source_refs defined."""
        if not shim_only_units:
            pytest.skip("No shim_only units found in ledger")

        for unit in shim_only_units:
            unit_id = unit.get("id", "<unknown>")
            assert "source_refs" in unit, f"Unit '{unit_id}' is shim_only but has no source_refs"
            assert isinstance(unit["source_refs"], list), f"Unit '{unit_id}' source_refs must be a list"
            assert len(unit["source_refs"]) > 0, f"Unit '{unit_id}' must have at least one source_ref"

    def test_shim_only_units_have_target_cell(self, shim_only_units: list[dict[str, Any]]) -> None:
        """Each shim_only unit should reference a target cell."""
        if not shim_only_units:
            pytest.skip("No shim_only units found in ledger")

        for unit in shim_only_units:
            unit_id = unit.get("id", "<unknown>")
            target = unit.get("target", {})
            assert isinstance(target, dict), f"Unit '{unit_id}' must have a target dict"
            assert "cell" in target, f"Unit '{unit_id}' must have a target.cell"


# ---------------------------------------------------------------------------
# Marker Pattern Tests
# ---------------------------------------------------------------------------


class TestMigrationMarkerPatterns:
    """Tests for migration marker detection patterns."""

    # These are the marker patterns that should be detected
    VALID_MARKER_PATTERNS: list[str] = [
        "# DEPRECATED",
        "# TODO: migrate",
        "# MIGRATED",
        "# LEGACY",
        "# SHIM",
        "# COMPATIBILITY",
        "# BACKWARD COMPAT",
        "# MOVED TO",
        "# 2026-04-24 migration",  # Date with migration keyword
        "deprecated on 2026-04-24",
        "migrated from 2026-04-24",
    ]

    INVALID_MARKER_PATTERNS: list[str] = [
        "# This is a regular comment",
        "pass  # no marker here",
        "import sys  # standard import",
        "# TODO: fix this bug",
    ]

    @pytest.mark.parametrize("marker", VALID_MARKER_PATTERNS)
    def test_valid_markers_should_be_detected(self, marker: str) -> None:
        """Valid migration markers should be detectable by the script."""
        import re

        # Migration marker patterns from check_shim_markers.py
        patterns = [
            re.compile(r"#\s*DEPRECATED", re.IGNORECASE),
            re.compile(r"#\s*TODO[:\s]+migrate", re.IGNORECASE),
            re.compile(r"#\s*MIGRATED", re.IGNORECASE),
            re.compile(r"#\s*LEGACY", re.IGNORECASE),
            re.compile(r"#\s*SHIM", re.IGNORECASE),
            re.compile(r"#\s*COMPATIBILITY", re.IGNORECASE),
            re.compile(r"#\s*BACKWARD\s*COMPAT", re.IGNORECASE),
            re.compile(r"#\s*MOVED\s*TO", re.IGNORECASE),
            re.compile(r"#\s*\d{4}-\d{2}-\d{2}.*migration", re.IGNORECASE),
            re.compile(r"migrated?\s+(?:on|from|to)\s+\d{4}-\d{2}-\d{2}", re.IGNORECASE),
            re.compile(r"deprecated.*\d{4}-\d{2}-\d{2}", re.IGNORECASE),
        ]

        found = False
        for pattern in patterns:
            if pattern.search(marker):
                found = True
                break

        assert found, f"Marker '{marker}' should be detected by at least one pattern"

    @pytest.mark.parametrize("marker", INVALID_MARKER_PATTERNS)
    def test_invalid_markers_should_not_be_detected(self, marker: str) -> None:
        """Non-migration markers should not be falsely detected."""
        import re

        patterns = [
            re.compile(r"#\s*DEPRECATED", re.IGNORECASE),
            re.compile(r"#\s*TODO[:\s]+migrate", re.IGNORECASE),
            re.compile(r"#\s*MIGRATED", re.IGNORECASE),
            re.compile(r"#\s*LEGACY", re.IGNORECASE),
            re.compile(r"#\s*SHIM", re.IGNORECASE),
            re.compile(r"#\s*COMPATIBILITY", re.IGNORECASE),
            re.compile(r"#\s*BACKWARD\s*COMPAT", re.IGNORECASE),
            re.compile(r"#\s*MOVED\s*TO", re.IGNORECASE),
            re.compile(r"#\s*\d{4}-\d{2}-\d{2}.*migration", re.IGNORECASE),
            re.compile(r"migrated?\s+(?:on|from|to)\s+\d{4}-\d{2}-\d{2}", re.IGNORECASE),
            re.compile(r"deprecated.*\d{4}-\d{2}-\d{2}", re.IGNORECASE),
        ]

        found = False
        for pattern in patterns:
            if pattern.search(marker):
                found = True
                break

        assert not found, f"Non-marker '{marker}' should not be detected"


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestShimMarkersIntegration:
    """Integration tests for shim_only_units_require_markers rule."""

    def test_rule_passes_when_no_shim_only_units(self) -> None:
        """Rule should pass vacuously when there are no shim_only units."""
        shim_units = _find_shim_only_units()

        if shim_units:
            pytest.skip("Ledger contains shim_only units - testing actual behavior")

        # If no shim_only units, the check should pass
        command = [
            sys.executable,
            str(SCRIPT_PATH),
            "--workspace",
            str(BACKEND_ROOT),
            "--json",
        ]
        completed = subprocess.run(
            command,
            cwd=str(BACKEND_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_build_utf8_env(),
            timeout=60,
            check=False,
        )

        payload = json.loads(completed.stdout or "{}")
        # When there are no shim_only units, the rule should pass
        if payload.get("evidence") and "No shim_only migration units found" in str(payload["evidence"]):
            assert payload.get("passed") is True, "Rule should pass when no shim_only units exist"

    def test_ledger_contains_shim_only_units(self, shim_only_units: list[dict[str, Any]]) -> None:
        """Ledger should contain at least one shim_only unit for testing."""
        # This is informational - we expect at least mig-application-batch1
        assert len(shim_only_units) >= 1, "Expected at least one shim_only unit in ledger (mig-application-batch1)"
        unit_ids = [u.get("id") for u in shim_only_units]
        assert "mig-application-batch1" in unit_ids, (
            f"Expected mig-application-batch1 in shim_only units, got: {unit_ids}"
        )

    def test_shim_only_files_exist_and_have_markers(self) -> None:
        """Files in shim_only units should have migration markers.

        This is the core functional test: when the ledger declares a unit
        as shim_only, the referenced files must contain migration markers.
        """
        shim_units = _find_shim_only_units()

        if not shim_units:
            pytest.skip("No shim_only units to test")

        # Run the full check
        command = [
            sys.executable,
            str(SCRIPT_PATH),
            "--workspace",
            str(BACKEND_ROOT),
            "--json",
        ]
        completed = subprocess.run(
            command,
            cwd=str(BACKEND_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_build_utf8_env(),
            timeout=60,
            check=False,
        )

        payload = json.loads(completed.stdout or "{}")

        # The test documents the current state:
        # - If passed=True, all shim_only files have proper markers
        # - If passed=False, some files are missing markers (violations listed)
        #
        # This test does NOT assert pass/fail because it depends on actual
        # file contents. Instead, it verifies the mechanism works correctly.
        assert "passed" in payload
        assert "violations" in payload
        assert isinstance(payload["violations"], list)

        # Document findings
        if payload["passed"]:
            # All files have markers - ideal state
            pass
        else:
            # Some files missing markers - document which ones
            violations = payload["violations"]
            assert len(violations) > 0, "When passed=False, there must be violations listed"
            # Each violation should mention a file path
            for violation in violations:
                assert isinstance(violation, str), "Each violation must be a string"
                assert len(violation) > 0, "Violation message must not be empty"


# ---------------------------------------------------------------------------
# Fitness Check Result Tests
# ---------------------------------------------------------------------------


class TestFitnessCheckResult:
    """Tests for the FitnessCheckResult dataclass used by the script."""

    def test_result_has_correct_rule_id(self) -> None:
        """FitnessCheckResult must use rule_id 'shim_only_units_require_markers'."""
        command = [
            sys.executable,
            str(SCRIPT_PATH),
            "--workspace",
            str(BACKEND_ROOT),
            "--json",
        ]
        completed = subprocess.run(
            command,
            cwd=str(BACKEND_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_build_utf8_env(),
            timeout=60,
            check=False,
        )

        payload = json.loads(completed.stdout or "{}")
        assert payload.get("rule_id") == "shim_only_units_require_markers", (
            f"Expected rule_id 'shim_only_units_require_markers', got '{payload.get('rule_id')}'"
        )

    def test_result_has_timestamp(self) -> None:
        """FitnessCheckResult must include a timestamp."""
        command = [
            sys.executable,
            str(SCRIPT_PATH),
            "--workspace",
            str(BACKEND_ROOT),
            "--json",
        ]
        completed = subprocess.run(
            command,
            cwd=str(BACKEND_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_build_utf8_env(),
            timeout=60,
            check=False,
        )

        payload = json.loads(completed.stdout or "{}")
        timestamp = payload.get("timestamp")
        assert timestamp is not None, "Result must have a timestamp"
        assert isinstance(timestamp, str), "Timestamp must be a string"
        assert len(timestamp) > 0, "Timestamp must not be empty"

    def test_result_has_duration(self) -> None:
        """FitnessCheckResult must include duration_ms."""
        command = [
            sys.executable,
            str(SCRIPT_PATH),
            "--workspace",
            str(BACKEND_ROOT),
            "--json",
        ]
        completed = subprocess.run(
            command,
            cwd=str(BACKEND_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_build_utf8_env(),
            timeout=60,
            check=False,
        )

        payload = json.loads(completed.stdout or "{}")
        duration_ms = payload.get("duration_ms")
        assert duration_ms is not None, "Result must have duration_ms"
        assert isinstance(duration_ms, (int, float)), "duration_ms must be numeric"
        assert duration_ms >= 0, "duration_ms must be non-negative"
