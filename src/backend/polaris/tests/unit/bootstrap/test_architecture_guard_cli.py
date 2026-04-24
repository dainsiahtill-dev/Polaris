"""Tests for polaris.bootstrap.governance.architecture_guard_cli module.

This module tests the ExternalPluginArchitectureGuard class and its
plugin validation logic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.bootstrap.governance.architecture_guard_cli import (
    ExternalPluginArchitectureGuard,
    GuardIssue,
    GuardReport,
    main,
)


class TestGuardIssue:
    """Test GuardIssue dataclass."""

    def test_create_guard_issue(self) -> None:
        """Should create a GuardIssue with all fields."""
        issue = GuardIssue(
            check_id="test.check",
            severity="blocker",
            message="Test issue",
            path="test.py",
            line=10,
        )
        assert issue.check_id == "test.check"
        assert issue.severity == "blocker"
        assert issue.message == "Test issue"
        assert issue.path == "test.py"
        assert issue.line == 10

    def test_fingerprint(self) -> None:
        """Should generate stable fingerprint."""
        issue = GuardIssue(
            check_id="test.check",
            severity="blocker",
            message="Test issue",
        )
        fingerprint = issue.fingerprint()
        assert isinstance(fingerprint, str)
        assert len(fingerprint) == 64  # SHA256 hex

    def test_to_dict(self) -> None:
        """Should convert to dictionary."""
        issue = GuardIssue(
            check_id="test.check",
            severity="blocker",
            message="Test issue",
        )
        d = issue.to_dict()
        assert d["check_id"] == "test.check"
        assert d["severity"] == "blocker"
        assert "fingerprint" in d


class TestGuardReport:
    """Test GuardReport dataclass."""

    def test_create_guard_report(self) -> None:
        """Should create a GuardReport with all fields."""
        issues = (
            GuardIssue(
                check_id="test.check",
                severity="blocker",
                message="Test issue",
            ),
        )
        report = GuardReport(
            plugin_root="/test/plugin",
            mode="hard-fail",
            exit_code=1,
            issue_count=1,
            blocker_count=1,
            high_count=0,
            new_issue_count=0,
            issues=issues,
        )
        assert report.plugin_root == "/test/plugin"
        assert report.mode == "hard-fail"
        assert report.issue_count == 1
        assert report.blocker_count == 1

    def test_to_dict(self) -> None:
        """Should convert to dictionary."""
        report = GuardReport(
            plugin_root="/test/plugin",
            mode="audit-only",
            exit_code=0,
            issue_count=0,
            blocker_count=0,
            high_count=0,
            new_issue_count=0,
            issues=(),
        )
        d = report.to_dict()
        assert d["plugin_root"] == "/test/plugin"
        assert d["exit_code"] == 0
        assert "issues" in d


class TestExternalPluginArchitectureGuardInit:
    """Test guard initialization."""

    def test_init_requires_existing_path(self, tmp_path: Path) -> None:
        """Should raise ValueError if plugin_root doesn't exist."""
        nonexistent = tmp_path / "nonexistent"
        with pytest.raises(ValueError, match="does not exist"):
            ExternalPluginArchitectureGuard(plugin_root=nonexistent)

    def test_init_invalid_mode(self, tmp_path: Path) -> None:
        """Should raise ValueError for invalid mode."""
        with pytest.raises(ValueError, match="Unsupported mode"):
            ExternalPluginArchitectureGuard(
                plugin_root=tmp_path,
                mode="invalid-mode",
            )

    def test_init_valid_mode(self, tmp_path: Path) -> None:
        """Should accept valid modes."""
        # Create a dummy file to make tmp_path exist
        dummy_file = tmp_path / "dummy"
        dummy_file.write_text("dummy", encoding="utf-8")

        for mode in ["audit-only", "fail-on-new", "hard-fail"]:
            guard = ExternalPluginArchitectureGuard(
                plugin_root=tmp_path,
                mode=mode,
            )
            assert guard._mode == mode


class TestExternalPluginArchitectureGuardRun:
    """Test guard run method."""

    def test_run_without_manifests_returns_blockers(self, tmp_path: Path) -> None:
        """Should report blockers for missing manifests."""
        guard = ExternalPluginArchitectureGuard(
            plugin_root=tmp_path,
            mode="hard-fail",
        )
        report = guard.run()

        assert report.exit_code == 1
        assert report.blocker_count >= 2  # plugin.yaml and cell.yaml missing

    def test_run_with_minimal_valid_manifests(self, tmp_path: Path) -> None:
        """Should pass with minimal valid manifests."""
        # Create minimal valid manifests
        plugin_yaml = tmp_path / "plugin.yaml"
        plugin_yaml.write_text(
            json.dumps(
                {
                    "manifest_version": "1.0",
                    "plugin_id": "test.plugin",
                    "display_name": "Test Plugin",
                    "publisher": "Test",
                    "plugin_version": "1.0.0",
                    "cell_id": "test.plugin",
                    "cell_manifest": "cell.yaml",
                    "sdk": {"version": "1.0"},
                    "runtime": {
                        "process_model": "isolated_process",
                        "default_enabled": False,
                    },
                    "capabilities": {"tokens": []},
                    "verification": {},
                    "distribution": {"format": "tarball"},
                }
            ),
            encoding="utf-8",
        )

        cell_yaml = tmp_path / "cell.yaml"
        cell_yaml.write_text(
            json.dumps(
                {
                    "id": "test.plugin",
                    "owned_paths": ["plugin/"],
                    "public_contracts": {"modules": []},
                    "depends_on": [],
                    "state_owners": [],
                    "effects_allowed": [],
                    "verification": {},
                }
            ),
            encoding="utf-8",
        )

        guard = ExternalPluginArchitectureGuard(
            plugin_root=tmp_path,
            mode="audit-only",
        )
        report = guard.run()

        assert report.exit_code == 0


class TestExternalPluginArchitectureGuardWriteBaseline:
    """Test baseline writing."""

    def test_write_baseline(self, tmp_path: Path) -> None:
        """Should write baseline file."""
        report = GuardReport(
            plugin_root=str(tmp_path),
            mode="fail-on-new",
            exit_code=0,
            issue_count=0,
            blocker_count=0,
            high_count=0,
            new_issue_count=0,
            issues=(),
        )

        baseline_path = tmp_path / "baseline.json"
        guard = ExternalPluginArchitectureGuard(
            plugin_root=tmp_path,
            mode="fail-on-new",
        )
        guard.write_baseline(baseline_path, report)

        assert baseline_path.exists()
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
        assert "issue_fingerprints" in data


class TestMain:
    """Test main CLI entrypoint."""

    def test_main_with_missing_plugin_root(self) -> None:
        """Should raise ValueError for missing plugin root."""
        with pytest.raises(ValueError, match="does not exist"):
            main(["check_external_plugin", "--plugin-root", "/nonexistent"])

    def test_main_audit_only_mode(self, tmp_path: Path) -> None:
        """Should run in audit-only mode without errors."""
        # Create minimal valid manifests for audit mode
        plugin_yaml = tmp_path / "plugin.yaml"
        plugin_yaml.write_text(
            json.dumps(
                {
                    "manifest_version": "1.0",
                    "plugin_id": "test.plugin",
                    "display_name": "Test Plugin",
                    "publisher": "Test",
                    "plugin_version": "1.0.0",
                    "cell_id": "test.plugin",
                    "cell_manifest": "cell.yaml",
                    "sdk": {"version": "1.0"},
                    "runtime": {
                        "process_model": "isolated_process",
                        "default_enabled": False,
                    },
                    "capabilities": {"tokens": []},
                    "verification": {},
                    "distribution": {"format": "tarball"},
                }
            ),
            encoding="utf-8",
        )

        cell_yaml = tmp_path / "cell.yaml"
        cell_yaml.write_text(
            json.dumps(
                {
                    "id": "test.plugin",
                    "owned_paths": ["plugin/"],
                    "public_contracts": {"modules": []},
                    "depends_on": [],
                    "state_owners": [],
                    "effects_allowed": [],
                    "verification": {},
                }
            ),
            encoding="utf-8",
        )

        result = main(
            [
                "check_external_plugin",
                "--plugin-root",
                str(tmp_path),
                "--mode",
                "audit-only",
            ]
        )

        # Should complete without errors
        assert result == 0
