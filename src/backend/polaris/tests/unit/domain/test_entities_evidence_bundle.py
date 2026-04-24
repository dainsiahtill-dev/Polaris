"""Tests for polaris.domain.entities.evidence_bundle."""

from __future__ import annotations

from datetime import datetime, timezone

from polaris.domain.entities.evidence_bundle import (
    ChangeType,
    EvidenceBundle,
    FileChange,
    PerfEvidence,
    SourceType,
    StaticAnalysisEvidence,
    TestRunEvidence,
)


class TestSourceType:
    def test_values(self) -> None:
        assert SourceType.DIRECTOR_RUN.value == "director_run"
        assert SourceType.MANUAL.value == "manual"
        assert SourceType.EXPERIMENT.value == "experiment"
        assert SourceType.REVIEW.value == "review"


class TestChangeType:
    def test_values(self) -> None:
        assert ChangeType.ADDED.value == "added"
        assert ChangeType.MODIFIED.value == "modified"
        assert ChangeType.DELETED.value == "deleted"
        assert ChangeType.RENAMED.value == "renamed"


class TestFileChange:
    def test_to_dict(self) -> None:
        change = FileChange(
            path="src/main.py",
            change_type=ChangeType.MODIFIED,
            lines_added=10,
            lines_deleted=2,
        )
        d = change.to_dict()
        assert d["path"] == "src/main.py"
        assert d["change_type"] == "modified"
        assert d["lines_added"] == 10
        assert d["lines_deleted"] == 2

    def test_from_dict(self) -> None:
        d = {
            "path": "src/main.py",
            "change_type": "added",
            "before_sha": "abc",
            "after_sha": "def",
            "patch": "diff",
            "patch_ref": None,
            "language": "python",
            "lines_added": 5,
            "lines_deleted": 0,
            "related_symbols": ["foo"],
        }
        change = FileChange.from_dict(d)
        assert change.path == "src/main.py"
        assert change.change_type == ChangeType.ADDED
        assert change.related_symbols == ["foo"]

    def test_is_large_patch_false_when_none(self) -> None:
        change = FileChange(path="a.py", change_type=ChangeType.MODIFIED)
        assert change.is_large_patch is False

    def test_is_large_patch_true_when_big(self) -> None:
        big_patch = "x" * (100 * 1024 + 1)
        change = FileChange(path="a.py", change_type=ChangeType.MODIFIED, patch=big_patch)
        assert change.is_large_patch is True

    def test_is_large_patch_false_when_small(self) -> None:
        small_patch = "small"
        change = FileChange(path="a.py", change_type=ChangeType.MODIFIED, patch=small_patch)
        assert change.is_large_patch is False


class TestTestRunEvidence:
    def test_to_dict(self) -> None:
        ev = TestRunEvidence(
            test_command="pytest",
            exit_code=0,
            total_tests=10,
            passed=10,
            failed=0,
            skipped=0,
            duration_seconds=5.5,
        )
        d = ev.to_dict()
        assert d["test_command"] == "pytest"
        assert d["passed"] == 10

    def test_from_dict(self) -> None:
        d = {
            "test_command": "pytest",
            "exit_code": 1,
            "total_tests": 5,
            "passed": 3,
            "failed": 2,
            "skipped": 0,
            "duration_seconds": 10.0,
            "failed_tests": ["test_a"],
            "coverage_percent": 80.0,
            "raw_output_ref": "ref",
        }
        ev = TestRunEvidence.from_dict(d)
        assert ev.failed_tests == ["test_a"]
        assert ev.coverage_percent == 80.0


class TestPerfEvidence:
    def test_to_dict(self) -> None:
        ev = PerfEvidence(benchmark_command="bench", metrics={"latency": 100.0})
        d = ev.to_dict()
        assert d["benchmark_command"] == "bench"
        assert d["metrics"] == {"latency": 100.0}

    def test_from_dict(self) -> None:
        d = {
            "benchmark_command": "bench",
            "metrics": {"latency": 100.0},
            "baseline_comparison": None,
            "flamegraph_ref": None,
        }
        ev = PerfEvidence.from_dict(d)
        assert ev.metrics == {"latency": 100.0}

    def test_from_dict_defaults(self) -> None:
        ev = PerfEvidence.from_dict({})
        assert ev.metrics == {}


class TestStaticAnalysisEvidence:
    def test_to_dict(self) -> None:
        ev = StaticAnalysisEvidence(tool_name="ruff", issues=[{"msg": "error"}])
        d = ev.to_dict()
        assert d["tool_name"] == "ruff"

    def test_from_dict(self) -> None:
        ev = StaticAnalysisEvidence.from_dict({"tool_name": "mypy", "issues": [], "issue_count_by_severity": {}})
        assert ev.tool_name == "mypy"


class TestEvidenceBundle:
    def test_to_dict_roundtrip(self) -> None:
        bundle = EvidenceBundle(
            bundle_id="b1",
            workspace=".",
            base_sha="abc",
            change_set=[
                FileChange(path="a.py", change_type=ChangeType.ADDED, lines_added=5),
            ],
        )
        d = bundle.to_dict()
        assert d["bundle_id"] == "b1"
        assert d["change_set"][0]["path"] == "a.py"

    def test_from_dict_roundtrip(self) -> None:
        created = datetime.now(timezone.utc)
        d = {
            "bundle_id": "b1",
            "workspace": ".",
            "base_sha": "abc",
            "head_sha": "def",
            "working_tree_dirty": True,
            "change_set": [
                {
                    "path": "a.py",
                    "change_type": "added",
                    "before_sha": None,
                    "after_sha": None,
                    "patch": None,
                    "patch_ref": None,
                    "language": None,
                    "lines_added": 5,
                    "lines_deleted": 0,
                    "related_symbols": [],
                }
            ],
            "created_at": created.isoformat(),
            "test_results": None,
            "performance_snapshot": None,
            "static_analysis": None,
            "source_type": "manual",
            "source_run_id": None,
            "source_task_id": None,
            "source_goal_id": None,
            "metadata": {},
        }
        bundle = EvidenceBundle.from_dict(d)
        assert bundle.bundle_id == "b1"
        assert len(bundle.change_set) == 1
        assert bundle.change_set[0].lines_added == 5

    def test_to_json(self) -> None:
        bundle = EvidenceBundle(
            bundle_id="b1",
            workspace=".",
            base_sha="abc",
            change_set=[],
        )
        json_str = bundle.to_json()
        assert "b1" in json_str
        assert "bundle_id" in json_str

    def test_from_json(self) -> None:
        bundle = EvidenceBundle(
            bundle_id="b1",
            workspace=".",
            base_sha="abc",
            change_set=[],
        )
        json_str = bundle.to_json()
        restored = EvidenceBundle.from_json(json_str)
        assert restored.bundle_id == "b1"

    def test_total_lines_changed(self) -> None:
        bundle = EvidenceBundle(
            bundle_id="b1",
            workspace=".",
            base_sha="abc",
            change_set=[
                FileChange(path="a.py", change_type=ChangeType.ADDED, lines_added=10, lines_deleted=2),
                FileChange(path="b.py", change_type=ChangeType.MODIFIED, lines_added=5, lines_deleted=3),
            ],
        )
        added, deleted = bundle.total_lines_changed
        assert added == 15
        assert deleted == 5

    def test_affected_files(self) -> None:
        bundle = EvidenceBundle(
            bundle_id="b1",
            workspace=".",
            base_sha="abc",
            change_set=[
                FileChange(path="a.py", change_type=ChangeType.ADDED),
                FileChange(path="b.py", change_type=ChangeType.MODIFIED),
            ],
        )
        assert bundle.affected_files == ["a.py", "b.py"]

    def test_affected_symbols(self) -> None:
        bundle = EvidenceBundle(
            bundle_id="b1",
            workspace=".",
            base_sha="abc",
            change_set=[
                FileChange(path="a.py", change_type=ChangeType.ADDED, related_symbols=["Foo", "bar"]),
                FileChange(path="b.py", change_type=ChangeType.MODIFIED, related_symbols=["Foo", "baz"]),
            ],
        )
        symbols = bundle.affected_symbols
        assert sorted(symbols) == ["Foo", "bar", "baz"]

    def test_get_change_for_file(self) -> None:
        bundle = EvidenceBundle(
            bundle_id="b1",
            workspace=".",
            base_sha="abc",
            change_set=[FileChange(path="a.py", change_type=ChangeType.ADDED)],
        )
        assert bundle.get_change_for_file("a.py") is not None
        assert bundle.get_change_for_file("b.py") is None

    def test_compute_content_hash(self) -> None:
        bundle = EvidenceBundle(
            bundle_id="b1",
            workspace=".",
            base_sha="abc",
            head_sha="def",
            change_set=[FileChange(path="a.py", change_type=ChangeType.ADDED)],
        )
        h = bundle.compute_content_hash()
        assert isinstance(h, str)
        assert len(h) == 16

    def test_compute_content_hash_consistency(self) -> None:
        bundle1 = EvidenceBundle(
            bundle_id="b1",
            workspace=".",
            base_sha="abc",
            head_sha="def",
            change_set=[FileChange(path="a.py", change_type=ChangeType.ADDED)],
        )
        bundle2 = EvidenceBundle(
            bundle_id="b1",
            workspace=".",
            base_sha="abc",
            head_sha="def",
            change_set=[FileChange(path="a.py", change_type=ChangeType.ADDED)],
        )
        assert bundle1.compute_content_hash() == bundle2.compute_content_hash()
