"""Tests for deterministic SWE-bench scoring metrics (Phase 0 measurement base)."""

from __future__ import annotations

from typing import Any

from polaris.kernelone.benchmark.swebench_metrics import (
    SCORE_SCHEMA_VERSION,
    PatchHunk,
    aggregate_score_records,
    build_score_record,
    gold_file_metrics,
    gold_hunk_overlap,
    parse_patch_hunks,
    patch_files,
    pure_f2p_resolved,
)


def _diff(path: str, old_start: int, old_length: int) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -{old_start},{old_length} +{old_start},{old_length + 1} @@\n"
        "-old\n"
        "+new\n"
        "+more\n"
    )


GOLD = _diff("django/db/models/sql/compiler.py", 500, 12)


class TestParsePatchHunks:
    def test_single_hunk(self) -> None:
        hunks = parse_patch_hunks(GOLD)
        assert hunks == [PatchHunk(path="django/db/models/sql/compiler.py", old_start=500, old_length=12)]

    def test_multi_file_multi_hunk(self) -> None:
        text = _diff("a.py", 1, 2) + _diff("b.py", 30, 4)
        hunks = parse_patch_hunks(text)
        assert [(h.path, h.old_start, h.old_length) for h in hunks] == [("a.py", 1, 2), ("b.py", 30, 4)]

    def test_omitted_length_defaults_to_one(self) -> None:
        text = "diff --git a/x.py b/x.py\n@@ -7 +7,2 @@\n-old\n+new\n+more\n"
        assert parse_patch_hunks(text) == [PatchHunk(path="x.py", old_start=7, old_length=1)]

    def test_prose_and_orphan_hunks_ignored(self) -> None:
        text = "Here is my fix:\n@@ -1,2 +1,2 @@\nnot a real diff\n"
        assert parse_patch_hunks(text) == []
        assert patch_files(text) == set()

    def test_empty_input(self) -> None:
        assert parse_patch_hunks("") == []
        assert patch_files("") == set()


class TestGoldFileMetrics:
    def test_hit_and_recall(self) -> None:
        gold = _diff("a.py", 1, 2) + _diff("b.py", 5, 2)
        model = _diff("a.py", 90, 3) + _diff("unrelated.py", 1, 1)
        metrics = gold_file_metrics(model, gold)
        assert metrics["gold_file_hit"] is True
        assert metrics["gold_file_recall"] == 0.5
        assert metrics["gold_files"] == ["a.py", "b.py"]
        assert metrics["model_files"] == ["a.py", "unrelated.py"]

    def test_miss(self) -> None:
        metrics = gold_file_metrics(_diff("wrong.py", 1, 1), GOLD)
        assert metrics["gold_file_hit"] is False
        assert metrics["gold_file_recall"] == 0.0

    def test_empty_model_patch_scores_zero(self) -> None:
        metrics = gold_file_metrics("", GOLD)
        assert metrics["gold_file_hit"] is False
        assert metrics["gold_file_recall"] == 0.0

    def test_empty_gold_patch_scores_zero_not_error(self) -> None:
        metrics = gold_file_metrics(_diff("a.py", 1, 1), "")
        assert metrics["gold_file_hit"] is False
        assert metrics["gold_file_recall"] == 0.0


class TestGoldHunkOverlap:
    def test_exact_intersection(self) -> None:
        model = _diff("django/db/models/sql/compiler.py", 505, 3)
        assert gold_hunk_overlap(model, GOLD) == 1.0

    def test_same_file_far_away_no_overlap(self) -> None:
        model = _diff("django/db/models/sql/compiler.py", 900, 3)
        assert gold_hunk_overlap(model, GOLD) == 0.0

    def test_slack_rescues_near_miss(self) -> None:
        # Gold covers [500, 511]; model hunk at 515 misses strict, hits slack=10.
        model = _diff("django/db/models/sql/compiler.py", 515, 2)
        assert gold_hunk_overlap(model, GOLD) == 0.0
        assert gold_hunk_overlap(model, GOLD, slack=10) == 1.0

    def test_right_lines_wrong_file_no_overlap(self) -> None:
        model = _diff("other.py", 500, 12)
        assert gold_hunk_overlap(model, GOLD) == 0.0

    def test_fraction_over_multiple_gold_hunks(self) -> None:
        gold = _diff("a.py", 10, 2) + _diff("b.py", 50, 2)
        model = _diff("a.py", 11, 1)
        assert gold_hunk_overlap(model, gold) == 0.5

    def test_empty_gold_returns_zero(self) -> None:
        assert gold_hunk_overlap(_diff("a.py", 1, 1), "") == 0.0


def _report(
    *,
    applied: bool = True,
    f2p_fail: list[str] | None = None,
    f2p_pass: list[str] | None = None,
    p2p_fail: list[str] | None = None,
    resolved: bool = False,
) -> dict[str, Any]:
    return {
        "resolved": resolved,
        "patch_successfully_applied": applied,
        "tests_status": {
            "FAIL_TO_PASS": {"failure": f2p_fail or [], "success": f2p_pass or []},
            "PASS_TO_PASS": {"failure": p2p_fail or [], "success": []},
        },
    }


class TestPureF2pResolved:
    def test_all_target_tests_pass(self) -> None:
        assert pure_f2p_resolved(_report(f2p_pass=["t1", "t2"])) is True

    def test_shields_env_flaky_p2p_failures(self) -> None:
        # The exact requests-2317 shape: gold-equivalent patch, network P2P red.
        assert pure_f2p_resolved(_report(f2p_pass=["t1"], p2p_fail=["test_connection_error"])) is True

    def test_target_failure_fails(self) -> None:
        assert pure_f2p_resolved(_report(f2p_fail=["t1"], f2p_pass=["t2"])) is False

    def test_no_passing_targets_fails(self) -> None:
        assert pure_f2p_resolved(_report()) is False

    def test_not_applied_fails(self) -> None:
        assert pure_f2p_resolved(_report(applied=False, f2p_pass=["t1"])) is False

    def test_missing_report_fields_fail_closed(self) -> None:
        assert pure_f2p_resolved({}) is False


class TestScoreRecord:
    def test_record_is_schema_stamped_and_complete(self) -> None:
        model = _diff("django/db/models/sql/compiler.py", 505, 3)
        record = build_score_record(
            instance_id="django__django-15213",
            model_patch=model,
            gold_patch=GOLD,
            report=_report(f2p_pass=["t1"], resolved=True),
        )
        assert record["schema_version"] == SCORE_SCHEMA_VERSION
        assert record["resolved"] is True
        assert record["pure_f2p_resolved"] is True
        assert record["gold_file_hit"] is True
        assert record["gold_hunk_overlap"] == 1.0
        assert record["empty_patch"] is False
        assert record["patch_lines"] == model.count("\n")

    def test_empty_patch_and_empty_report(self) -> None:
        record = build_score_record(instance_id="x__y-1", model_patch="", gold_patch=GOLD, report={})
        assert record["empty_patch"] is True
        assert record["resolved"] is False
        assert record["pure_f2p_resolved"] is False
        assert record["gold_file_hit"] is False
        assert record["f2p_pass_count"] == 0

    def test_aggregate(self) -> None:
        good = build_score_record(
            instance_id="a",
            model_patch=_diff("django/db/models/sql/compiler.py", 505, 3),
            gold_patch=GOLD,
            report=_report(f2p_pass=["t"], resolved=True),
        )
        bad = build_score_record(instance_id="b", model_patch="", gold_patch=GOLD, report={})
        agg = aggregate_score_records([good, bad])
        assert agg["total"] == 2
        assert agg["resolved"] == 1
        assert agg["pure_f2p_resolved"] == 1
        assert agg["gold_file_hit_rate"] == 0.5
        assert agg["mean_gold_hunk_overlap"] == 0.5
        assert agg["non_empty_patches"] == 1
        assert agg["schema_version"] == SCORE_SCHEMA_VERSION

    def test_aggregate_empty(self) -> None:
        agg = aggregate_score_records([])
        assert agg["total"] == 0
        assert agg["resolved_rate"] == 0.0


class TestPairedReport:
    @staticmethod
    def _record(iid: str, *, resolved: bool, pure: bool, hit: bool, hunk: float) -> dict[str, Any]:
        return {
            "instance_id": iid,
            "resolved": resolved,
            "pure_f2p_resolved": pure,
            "gold_file_hit": hit,
            "gold_hunk_overlap": hunk,
            "gold_file_recall": 1.0 if hit else 0.0,
            "gold_hunk_overlap_slack10": hunk,
            "empty_patch": False,
        }

    def test_paired_delta_direction_and_shared_instances(self) -> None:
        from polaris.kernelone.benchmark.swebench_metrics import (
            PAIRED_SCHEMA_VERSION,
            build_paired_report,
        )

        weak = [
            [
                self._record("i1", resolved=False, pure=False, hit=False, hunk=0.0),
                self._record("i2", resolved=False, pure=False, hit=True, hunk=0.5),
            ]
        ]
        strong = [
            [
                self._record("i1", resolved=True, pure=True, hit=True, hunk=1.0),
                self._record("i2", resolved=True, pure=True, hit=True, hunk=1.0),
                self._record("i3", resolved=True, pure=True, hit=True, hunk=1.0),
            ]
        ]
        report = build_paired_report("weak", weak, "strong", strong)
        assert report["schema_version"] == PAIRED_SCHEMA_VERSION
        assert report["shared_instances"] == ["i1", "i2"]
        assert report["paired_delta"]["i1"]["resolved"] == 1.0
        assert report["paired_delta"]["i2"]["gold_hunk_overlap"] == 0.5
        assert report["delta_summary_b_minus_a"]["resolved"] == 1.0
        assert report["arms"]["strong"]["mean_resolved_rate"] == 1.0
        assert report["arms"]["weak"]["mean_resolved_rate"] == 0.0

    def test_repeats_average_per_instance(self) -> None:
        from polaris.kernelone.benchmark.swebench_metrics import build_paired_report

        flaky = [
            [self._record("i1", resolved=True, pure=True, hit=True, hunk=1.0)],
            [self._record("i1", resolved=False, pure=False, hit=True, hunk=1.0)],
        ]
        stable = [
            [self._record("i1", resolved=True, pure=True, hit=True, hunk=1.0)],
            [self._record("i1", resolved=True, pure=True, hit=True, hunk=1.0)],
        ]
        report = build_paired_report("flaky", flaky, "stable", stable)
        assert report["per_instance"]["flaky"]["i1"]["resolved"] == 0.5
        assert report["paired_delta"]["i1"]["resolved"] == 0.5

    def test_empty_arms(self) -> None:
        from polaris.kernelone.benchmark.swebench_metrics import build_paired_report

        report = build_paired_report("a", [], "b", [])
        assert report["shared_instances"] == []
        assert report["delta_summary_b_minus_a"]["resolved"] == 0.0
