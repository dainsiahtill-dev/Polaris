"""Tests for the deterministic crush savings observation (T2-B observe-first).

These tests prove that the savings benchmark runs the real crushers over
representative synthetic samples, reports the documented per-type fields, and
fail-closes on any never-expand violation. They do NOT exercise any live
compression path.

Run with:
    pytest polaris/kernelone/context/crushers/tests/test_savings_report.py -v
"""

from __future__ import annotations

import json

from polaris.kernelone.context.crushers import CrushKind, estimate_tokens
from polaris.kernelone.context.crushers.savings_report import (
    SavingsReport,
    TypeSavings,
    build_sample_corpus,
    main,
    measure_savings,
    render_report,
)


class TestSampleCorpus:
    def test_corpus_has_one_sample_per_crush_type(self) -> None:
        corpus = build_sample_corpus()
        assert set(corpus) == {
            "json_records",
            "json_numbers",
            "log_repetitive",
            "diff_context_heavy",
            "search_duplicates",
        }

    def test_samples_exceed_min_crush_threshold(self) -> None:
        # Below the threshold the crushers no-op; samples must actually engage.
        from polaris.kernelone.context.crushers import MIN_CRUSH_BYTES

        for label, text in build_sample_corpus().items():
            assert len(text.encode("utf-8")) >= MIN_CRUSH_BYTES, label

    def test_samples_are_utf8_clean(self) -> None:
        for text in build_sample_corpus().values():
            text.encode("utf-8")


class TestMeasureSavings:
    def test_reports_every_sample(self) -> None:
        report = measure_savings()
        assert len(report.entries) == len(build_sample_corpus())

    def test_each_entry_has_documented_fields(self) -> None:
        report = measure_savings()
        for entry in report.entries:
            assert isinstance(entry, TypeSavings)
            assert entry.original_tokens >= 0
            assert entry.crushed_tokens >= 0
            assert 0.0 <= entry.ratio <= 1.0
            assert isinstance(entry.kind, CrushKind)
            assert isinstance(entry.reject_if_not_smaller_held, bool)

    def test_structured_samples_actually_shrink(self) -> None:
        # The representative structured samples must demonstrate real savings;
        # otherwise the observation proves nothing.
        report = measure_savings()
        crushed = [e for e in report.entries if e.kind is not CrushKind.NONE]
        assert crushed, "no sample was crushed -- benchmark proves nothing"
        for entry in crushed:
            assert entry.crushed_tokens < entry.original_tokens, entry.label
            assert entry.saved_tokens > 0

    def test_never_expand_invariant_holds_for_all(self) -> None:
        report = measure_savings()
        assert report.all_invariants_held()
        for entry in report.entries:
            assert entry.crushed_tokens <= entry.original_tokens, entry.label

    def test_original_tokens_match_canonical_estimator(self) -> None:
        # Confirms the report reuses the canonical estimator, not a private one.
        corpus = build_sample_corpus()
        report = measure_savings(corpus)
        by_label = {e.label: e for e in report.entries}
        for label, text in corpus.items():
            assert by_label[label].original_tokens == estimate_tokens(text), label

    def test_deterministic(self) -> None:
        assert measure_savings().to_dict() == measure_savings().to_dict()

    def test_custom_corpus_is_honoured(self) -> None:
        report = measure_savings({"only": json.dumps([{"id": i} for i in range(200)])})
        assert [e.label for e in report.entries] == ["only"]


class TestAggregateReport:
    def test_totals_are_consistent(self) -> None:
        report = measure_savings()
        assert report.total_original_tokens == sum(e.original_tokens for e in report.entries)
        assert report.total_crushed_tokens == sum(e.crushed_tokens for e in report.entries)
        assert report.total_crushed_tokens <= report.total_original_tokens

    def test_total_ratio_in_unit_range(self) -> None:
        report = measure_savings()
        assert 0.0 <= report.total_ratio <= 1.0

    def test_empty_report_is_neutral(self) -> None:
        report = SavingsReport()
        assert report.total_original_tokens == 0
        assert report.total_ratio == 1.0
        assert report.all_invariants_held() is True

    def test_invariant_violation_surfaces_fail_closed(self) -> None:
        # A fabricated entry that expanded must flip the aggregate flag to False;
        # the observation must never silently pass an expanding crush.
        bad = TypeSavings(
            label="bad",
            original_tokens=10,
            crushed_tokens=20,
            ratio=1.0,
            kind=CrushKind.NONE,
            reject_if_not_smaller_held=False,
        )
        assert SavingsReport(entries=(bad,)).all_invariants_held() is False


class TestRenderAndCli:
    def test_render_is_valid_json(self) -> None:
        rendered = render_report(measure_savings())
        decoded = json.loads(rendered)
        assert "entries" in decoded
        assert decoded["all_invariants_held"] is True
        assert "total_saved_tokens" in decoded

    def test_render_preserves_documented_per_type_fields(self) -> None:
        decoded = json.loads(render_report(measure_savings()))
        for entry in decoded["entries"]:
            assert set(entry) >= {
                "label",
                "kind",
                "original_tokens",
                "crushed_tokens",
                "ratio",
                "reject_if_not_smaller_held",
            }

    def test_main_returns_zero_when_invariants_hold(self) -> None:
        assert main() == 0
