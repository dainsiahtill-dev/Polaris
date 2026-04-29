"""Comprehensive tests for polaris.domain.entities.evidence_bundle."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from polaris.domain.entities.evidence_bundle import (
    BundleComparison,
    ChangeType,
    EvidenceBundle,
    FileChange,
    PerfEvidence,
    SourceType,
    StaticAnalysisEvidence,
    TestRunEvidence,
)


class TestSourceType:
    def test_members(self):
        assert SourceType.DIRECTOR_RUN.value == "director_run"
        assert SourceType.MANUAL.value == "manual"
        assert SourceType.EXPERIMENT.value == "experiment"
        assert SourceType.REVIEW.value == "review"

    def test_from_value(self):
        assert SourceType("manual") == SourceType.MANUAL

    def test_from_invalid_value_raises(self):
        with pytest.raises(ValueError):
            SourceType("nonexistent")


class TestChangeType:
    def test_members(self):
        assert ChangeType.ADDED.value == "added"
        assert ChangeType.MODIFIED.value == "modified"
        assert ChangeType.DELETED.value == "deleted"
        assert ChangeType.RENAMED.value == "renamed"

    def test_from_invalid_value_raises(self):
        with pytest.raises(ValueError):
            ChangeType("copied")


class TestFileChangeCreation:
    def test_minimal_creation(self):
        fc = FileChange(path="src/foo.py", change_type=ChangeType.ADDED)
        assert fc.path == "src/foo.py"
        assert fc.change_type == ChangeType.ADDED
        assert fc.before_sha is None
        assert fc.after_sha is None
        assert fc.patch is None
        assert fc.patch_ref is None
        assert fc.language is None
        assert fc.lines_added == 0
        assert fc.lines_deleted == 0
        assert fc.related_symbols == []

    def test_full_creation(self):
        fc = FileChange(
            path="src/foo.py",
            change_type=ChangeType.MODIFIED,
            before_sha="abc123",
            after_sha="def456",
            patch="@@ -1,3 +1,3 @@",
            patch_ref="ref://patch/1",
            language="python",
            lines_added=5,
            lines_deleted=3,
            related_symbols=["FooClass", "bar_method"],
        )
        assert fc.before_sha == "abc123"
        assert fc.after_sha == "def456"
        assert fc.patch == "@@ -1,3 +1,3 @@"
        assert fc.patch_ref == "ref://patch/1"
        assert fc.language == "python"
        assert fc.lines_added == 5
        assert fc.lines_deleted == 3
        assert fc.related_symbols == ["FooClass", "bar_method"]

    def test_empty_related_symbols(self):
        fc = FileChange(path="a.py", change_type=ChangeType.DELETED, related_symbols=[])
        assert fc.related_symbols == []


class TestFileChangeSerialization:
    def test_to_dict(self):
        fc = FileChange(path="a.py", change_type=ChangeType.RENAMED, lines_added=2, lines_deleted=1)
        d = fc.to_dict()
        assert d["path"] == "a.py"
        assert d["change_type"] == "renamed"
        assert d["before_sha"] is None
        assert d["lines_added"] == 2
        assert d["lines_deleted"] == 1
        assert d["related_symbols"] == []

    def test_from_dict_minimal(self):
        d = {"path": "b.py", "change_type": "added"}
        fc = FileChange.from_dict(d)
        assert fc.path == "b.py"
        assert fc.change_type == ChangeType.ADDED
        assert fc.lines_added == 0
        assert fc.lines_deleted == 0
        assert fc.related_symbols == []

    def test_from_dict_full(self):
        d = {
            "path": "c.py",
            "change_type": "modified",
            "before_sha": "abc",
            "after_sha": "def",
            "patch": "patch content",
            "patch_ref": "ref://1",
            "language": "python",
            "lines_added": 10,
            "lines_deleted": 5,
            "related_symbols": ["sym1"],
        }
        fc = FileChange.from_dict(d)
        assert fc.path == "c.py"
        assert fc.change_type == ChangeType.MODIFIED
        assert fc.before_sha == "abc"
        assert fc.after_sha == "def"
        assert fc.patch == "patch content"
        assert fc.patch_ref == "ref://1"
        assert fc.language == "python"
        assert fc.lines_added == 10
        assert fc.lines_deleted == 5
        assert fc.related_symbols == ["sym1"]

    def test_from_dict_missing_optional(self):
        d = {
            "path": "d.py",
            "change_type": "deleted",
            "lines_added": 3,
        }
        fc = FileChange.from_dict(d)
        assert fc.lines_deleted == 0
        assert fc.related_symbols == []

    def test_roundtrip(self):
        fc = FileChange(
            path="e.py",
            change_type=ChangeType.ADDED,
            related_symbols=["sym"],
            lines_added=7,
        )
        d = fc.to_dict()
        fc2 = FileChange.from_dict(d)
        assert fc == fc2


class TestFileChangeIsLargePatch:
    def test_none_patch(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED)
        assert fc.is_large_patch is False

    def test_small_patch(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED, patch="small")
        assert fc.is_large_patch is False

    def test_empty_patch(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED, patch="")
        assert fc.is_large_patch is False

    def test_boundary_exactly_100kb(self):
        patch = "x" * (100 * 1024)
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED, patch=patch)
        assert fc.is_large_patch is True

    def test_just_under_100kb(self):
        patch = "x" * (100 * 1024 - 1)
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED, patch=patch)
        assert fc.is_large_patch is False

    def test_over_100kb(self):
        patch = "x" * (100 * 1024 + 1)
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED, patch=patch)
        assert fc.is_large_patch is True


class TestTestRunEvidence:
    def test_creation_defaults(self):
        tre = TestRunEvidence(
            test_command="pytest",
            exit_code=0,
            total_tests=10,
            passed=8,
            failed=1,
            skipped=1,
            duration_seconds=5.5,
        )
        assert tre.test_command == "pytest"
        assert tre.exit_code == 0
        assert tre.total_tests == 10
        assert tre.passed == 8
        assert tre.failed == 1
        assert tre.skipped == 1
        assert tre.duration_seconds == 5.5
        assert tre.failed_tests == []
        assert tre.coverage_percent is None
        assert tre.raw_output_ref is None

    def test_creation_full(self):
        tre = TestRunEvidence(
            test_command="pytest",
            exit_code=1,
            total_tests=5,
            passed=4,
            failed=1,
            skipped=0,
            duration_seconds=2.0,
            failed_tests=["test_a"],
            coverage_percent=85.5,
            raw_output_ref="ref://output",
        )
        assert tre.failed_tests == ["test_a"]
        assert tre.coverage_percent == 85.5
        assert tre.raw_output_ref == "ref://output"

    def test_to_dict(self):
        tre = TestRunEvidence(
            test_command="pytest", exit_code=0, total_tests=1, passed=1, failed=0, skipped=0, duration_seconds=1.0
        )
        d = tre.to_dict()
        assert d["test_command"] == "pytest"
        assert d["exit_code"] == 0
        assert d["failed_tests"] == []
        assert d["coverage_percent"] is None

    def test_from_dict(self):
        d = {
            "test_command": "pytest",
            "exit_code": 1,
            "total_tests": 3,
            "passed": 2,
            "failed": 1,
            "skipped": 0,
            "duration_seconds": 0.5,
            "failed_tests": ["test_b"],
            "coverage_percent": 90.0,
            "raw_output_ref": None,
        }
        tre = TestRunEvidence.from_dict(d)
        assert tre.test_command == "pytest"
        assert tre.failed_tests == ["test_b"]
        assert tre.coverage_percent == 90.0

    def test_roundtrip(self):
        tre = TestRunEvidence(
            test_command="pytest",
            exit_code=0,
            total_tests=5,
            passed=5,
            failed=0,
            skipped=0,
            duration_seconds=3.0,
            failed_tests=["x"],
            coverage_percent=100.0,
        )
        d = tre.to_dict()
        tre2 = TestRunEvidence.from_dict(d)
        assert tre == tre2


class TestPerfEvidence:
    def test_creation_defaults(self):
        pe = PerfEvidence()
        assert pe.benchmark_command is None
        assert pe.metrics == {}
        assert pe.baseline_comparison is None
        assert pe.flamegraph_ref is None

    def test_creation_full(self):
        pe = PerfEvidence(
            benchmark_command="hyperfine",
            metrics={"time": 1.2},
            baseline_comparison={"time": 1.5},
            flamegraph_ref="ref://flame",
        )
        assert pe.benchmark_command == "hyperfine"
        assert pe.metrics == {"time": 1.2}
        assert pe.baseline_comparison == {"time": 1.5}
        assert pe.flamegraph_ref == "ref://flame"

    def test_to_dict(self):
        pe = PerfEvidence(metrics={"a": 1.0})
        d = pe.to_dict()
        assert d["benchmark_command"] is None
        assert d["metrics"] == {"a": 1.0}
        assert d["baseline_comparison"] is None

    def test_from_dict_minimal(self):
        d = {"metrics": {"a": 1.0}}
        pe = PerfEvidence.from_dict(d)
        assert pe.benchmark_command is None
        assert pe.metrics == {"a": 1.0}
        assert pe.baseline_comparison is None
        assert pe.flamegraph_ref is None

    def test_from_dict_empty(self):
        pe = PerfEvidence.from_dict({})
        assert pe.metrics == {}
        assert pe.benchmark_command is None

    def test_roundtrip(self):
        pe = PerfEvidence(benchmark_command="bench", metrics={"x": 2.0})
        d = pe.to_dict()
        pe2 = PerfEvidence.from_dict(d)
        assert pe == pe2


class TestStaticAnalysisEvidence:
    def test_creation_defaults(self):
        sae = StaticAnalysisEvidence(tool_name="ruff")
        assert sae.tool_name == "ruff"
        assert sae.issues == []
        assert sae.issue_count_by_severity == {}

    def test_creation_full(self):
        sae = StaticAnalysisEvidence(
            tool_name="mypy",
            issues=[{"msg": "error"}],
            issue_count_by_severity={"high": 2},
        )
        assert sae.tool_name == "mypy"
        assert sae.issues == [{"msg": "error"}]
        assert sae.issue_count_by_severity == {"high": 2}

    def test_to_dict(self):
        sae = StaticAnalysisEvidence(tool_name="ruff")
        d = sae.to_dict()
        assert d["tool_name"] == "ruff"
        assert d["issues"] == []
        assert d["issue_count_by_severity"] == {}

    def test_from_dict_minimal(self):
        d = {"tool_name": "flake8"}
        sae = StaticAnalysisEvidence.from_dict(d)
        assert sae.tool_name == "flake8"
        assert sae.issues == []
        assert sae.issue_count_by_severity == {}

    def test_from_dict_full(self):
        d = {
            "tool_name": "pylint",
            "issues": [{"line": 1, "msg": "warning"}],
            "issue_count_by_severity": {"medium": 1},
        }
        sae = StaticAnalysisEvidence.from_dict(d)
        assert sae.tool_name == "pylint"
        assert len(sae.issues) == 1
        assert sae.issue_count_by_severity == {"medium": 1}

    def test_roundtrip(self):
        sae = StaticAnalysisEvidence(tool_name="bandit", issues=[{"id": "B101"}])
        d = sae.to_dict()
        sae2 = StaticAnalysisEvidence.from_dict(d)
        assert sae == sae2


class TestEvidenceBundleCreation:
    def test_minimal_creation(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED)
        eb = EvidenceBundle(
            bundle_id="b1",
            workspace="/tmp/ws",
            base_sha="abc",
            change_set=[fc],
        )
        assert eb.bundle_id == "b1"
        assert eb.workspace == "/tmp/ws"
        assert eb.base_sha == "abc"
        assert eb.head_sha is None
        assert eb.working_tree_dirty is True
        assert eb.test_results is None
        assert eb.performance_snapshot is None
        assert eb.static_analysis is None
        assert eb.source_type == SourceType.MANUAL
        assert eb.source_run_id is None
        assert eb.source_task_id is None
        assert eb.source_goal_id is None
        assert eb.metadata == {}
        assert isinstance(eb.created_at, datetime)

    def test_full_creation(self):
        fc = FileChange(path="a.py", change_type=ChangeType.MODIFIED)
        tre = TestRunEvidence(
            test_command="pytest", exit_code=0, total_tests=1, passed=1, failed=0, skipped=0, duration_seconds=1.0
        )
        pe = PerfEvidence(metrics={"time": 1.0})
        sae = StaticAnalysisEvidence(tool_name="ruff")
        created = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        eb = EvidenceBundle(
            bundle_id="b2",
            workspace="/ws",
            base_sha="base",
            head_sha="head",
            working_tree_dirty=False,
            change_set=[fc],
            test_results=tre,
            performance_snapshot=pe,
            static_analysis=sae,
            source_type=SourceType.DIRECTOR_RUN,
            source_run_id="run-1",
            source_task_id="task-1",
            source_goal_id="goal-1",
            metadata={"key": "value"},
            created_at=created,
        )
        assert eb.head_sha == "head"
        assert eb.working_tree_dirty is False
        assert eb.test_results == tre
        assert eb.performance_snapshot == pe
        assert eb.static_analysis == sae
        assert eb.source_type == SourceType.DIRECTOR_RUN
        assert eb.source_run_id == "run-1"
        assert eb.metadata == {"key": "value"}
        assert eb.created_at == created

    def test_empty_change_set(self):
        eb = EvidenceBundle(
            bundle_id="b3",
            workspace="/ws",
            base_sha="abc",
            change_set=[],
        )
        assert eb.change_set == []


class TestEvidenceBundleProperties:
    def test_total_lines_changed(self):
        fc1 = FileChange(path="a.py", change_type=ChangeType.ADDED, lines_added=10, lines_deleted=2)
        fc2 = FileChange(path="b.py", change_type=ChangeType.MODIFIED, lines_added=5, lines_deleted=3)
        eb = EvidenceBundle(bundle_id="b1", workspace="/ws", base_sha="abc", change_set=[fc1, fc2])
        assert eb.total_lines_changed == (15, 5)

    def test_total_lines_changed_empty(self):
        eb = EvidenceBundle(bundle_id="b1", workspace="/ws", base_sha="abc", change_set=[])
        assert eb.total_lines_changed == (0, 0)

    def test_affected_files(self):
        fc1 = FileChange(path="a.py", change_type=ChangeType.ADDED)
        fc2 = FileChange(path="b.py", change_type=ChangeType.MODIFIED)
        eb = EvidenceBundle(bundle_id="b1", workspace="/ws", base_sha="abc", change_set=[fc1, fc2])
        assert eb.affected_files == ["a.py", "b.py"]

    def test_affected_files_empty(self):
        eb = EvidenceBundle(bundle_id="b1", workspace="/ws", base_sha="abc", change_set=[])
        assert eb.affected_files == []

    def test_affected_symbols(self):
        fc1 = FileChange(path="a.py", change_type=ChangeType.ADDED, related_symbols=["foo", "bar"])
        fc2 = FileChange(path="b.py", change_type=ChangeType.MODIFIED, related_symbols=["bar", "baz"])
        eb = EvidenceBundle(bundle_id="b1", workspace="/ws", base_sha="abc", change_set=[fc1, fc2])
        symbols = eb.affected_symbols
        assert len(symbols) == 3
        assert set(symbols) == {"foo", "bar", "baz"}

    def test_affected_symbols_empty(self):
        eb = EvidenceBundle(bundle_id="b1", workspace="/ws", base_sha="abc", change_set=[])
        assert eb.affected_symbols == []

    def test_get_change_for_file_found(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED)
        eb = EvidenceBundle(bundle_id="b1", workspace="/ws", base_sha="abc", change_set=[fc])
        assert eb.get_change_for_file("a.py") == fc

    def test_get_change_for_file_missing(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED)
        eb = EvidenceBundle(bundle_id="b1", workspace="/ws", base_sha="abc", change_set=[fc])
        assert eb.get_change_for_file("missing.py") is None

    def test_compute_content_hash_consistency(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED)
        eb = EvidenceBundle(bundle_id="b1", workspace="/ws", base_sha="abc", head_sha="def", change_set=[fc])
        h1 = eb.compute_content_hash()
        h2 = eb.compute_content_hash()
        assert h1 == h2
        assert len(h1) == 16

    def test_compute_content_hash_different_files(self):
        fc1 = FileChange(path="a.py", change_type=ChangeType.ADDED)
        fc2 = FileChange(path="b.py", change_type=ChangeType.ADDED)
        eb1 = EvidenceBundle(bundle_id="b1", workspace="/ws", base_sha="abc", change_set=[fc1])
        eb2 = EvidenceBundle(bundle_id="b2", workspace="/ws", base_sha="abc", change_set=[fc2])
        assert eb1.compute_content_hash() != eb2.compute_content_hash()

    def test_compute_content_hash_different_sha(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED)
        eb1 = EvidenceBundle(bundle_id="b1", workspace="/ws", base_sha="abc", head_sha="def", change_set=[fc])
        eb2 = EvidenceBundle(bundle_id="b2", workspace="/ws", base_sha="abc", head_sha="xyz", change_set=[fc])
        assert eb1.compute_content_hash() != eb2.compute_content_hash()


class TestEvidenceBundleSerialization:
    def test_to_dict_structure(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED)
        eb = EvidenceBundle(bundle_id="b1", workspace="/ws", base_sha="abc", change_set=[fc])
        d = eb.to_dict()
        assert d["bundle_id"] == "b1"
        assert d["workspace"] == "/ws"
        assert d["base_sha"] == "abc"
        assert d["head_sha"] is None
        assert d["working_tree_dirty"] is True
        assert d["source_type"] == "manual"
        assert d["test_results"] is None
        assert d["performance_snapshot"] is None
        assert d["static_analysis"] is None
        assert d["source_run_id"] is None
        assert d["source_task_id"] is None
        assert d["source_goal_id"] is None
        assert d["metadata"] == {}
        assert isinstance(d["created_at"], str)
        assert len(d["change_set"]) == 1
        assert d["change_set"][0]["path"] == "a.py"

    def test_to_dict_with_nested(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED)
        tre = TestRunEvidence(
            test_command="pytest", exit_code=0, total_tests=1, passed=1, failed=0, skipped=0, duration_seconds=1.0
        )
        eb = EvidenceBundle(
            bundle_id="b1",
            workspace="/ws",
            base_sha="abc",
            change_set=[fc],
            test_results=tre,
            source_type=SourceType.EXPERIMENT,
        )
        d = eb.to_dict()
        assert d["test_results"]["test_command"] == "pytest"
        assert d["source_type"] == "experiment"

    def test_from_dict_minimal(self):
        d = {
            "bundle_id": "b1",
            "workspace": "/ws",
            "base_sha": "abc",
            "change_set": [],
            "created_at": "2024-01-01T12:00:00+00:00",
        }
        eb = EvidenceBundle.from_dict(d)
        assert eb.bundle_id == "b1"
        assert eb.change_set == []
        assert eb.source_type == SourceType.MANUAL
        assert eb.working_tree_dirty is True
        assert eb.metadata == {}

    def test_from_dict_full(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED)
        tre = TestRunEvidence(
            test_command="pytest", exit_code=0, total_tests=1, passed=1, failed=0, skipped=0, duration_seconds=1.0
        )
        pe = PerfEvidence(metrics={"time": 1.0})
        sae = StaticAnalysisEvidence(tool_name="ruff")
        eb = EvidenceBundle(
            bundle_id="b1",
            workspace="/ws",
            base_sha="abc",
            head_sha="def",
            change_set=[fc],
            test_results=tre,
            performance_snapshot=pe,
            static_analysis=sae,
            source_type=SourceType.DIRECTOR_RUN,
            source_run_id="run-1",
            source_task_id="task-1",
            source_goal_id="goal-1",
            metadata={"k": "v"},
        )
        d = eb.to_dict()
        eb2 = EvidenceBundle.from_dict(d)
        assert eb2.bundle_id == "b1"
        assert eb2.head_sha == "def"
        assert eb2.working_tree_dirty is True
        assert eb2.test_results is not None
        assert eb2.test_results.exit_code == 0
        assert eb2.performance_snapshot is not None
        assert eb2.static_analysis is not None
        assert eb2.source_type == SourceType.DIRECTOR_RUN
        assert eb2.metadata == {"k": "v"}

    def test_from_dict_with_source_type(self):
        d = {
            "bundle_id": "b1",
            "workspace": "/ws",
            "base_sha": "abc",
            "change_set": [],
            "created_at": "2024-01-01T12:00:00+00:00",
            "source_type": "experiment",
        }
        eb = EvidenceBundle.from_dict(d)
        assert eb.source_type == SourceType.EXPERIMENT

    def test_json_roundtrip(self):
        fc = FileChange(path="a.py", change_type=ChangeType.MODIFIED, lines_added=5)
        eb = EvidenceBundle(bundle_id="b1", workspace="/ws", base_sha="abc", change_set=[fc])
        json_str = eb.to_json()
        assert isinstance(json_str, str)
        assert "b1" in json_str
        eb2 = EvidenceBundle.from_json(json_str)
        assert eb2.bundle_id == "b1"
        assert len(eb2.change_set) == 1
        assert eb2.change_set[0].lines_added == 5

    def test_bundle_comparison_type_alias(self):
        bc: BundleComparison = {"key": "value", "count": 42}
        assert bc["key"] == "value"
