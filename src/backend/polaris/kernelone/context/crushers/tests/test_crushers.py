"""Tests for each deterministic crusher (T2-B).

Every crusher must satisfy the reject-if-not-smaller invariant: it returns
crushed text only when ``crushed_tokens < original_tokens``, otherwise the input
is returned unchanged with ``kind=NONE``.

Run with:
    pytest polaris/kernelone/context/crushers/tests/test_crushers.py -v
"""

from __future__ import annotations

import json

from polaris.kernelone.context.crushers import (
    CrushKind,
    crush_diff,
    crush_json,
    crush_log,
    crush_search,
)
from polaris.kernelone.context.crushers.base import finalize


class TestFinalizeInvariant:
    def test_rejects_non_smaller(self) -> None:
        original = "abcdefgh" * 50
        # A "crushed" candidate that is actually larger must be rejected.
        result = finalize(original, original + "more text padding here", CrushKind.LOG)
        assert result.kind is CrushKind.NONE
        assert result.text == original
        assert result.ratio == 1.0

    def test_accepts_smaller(self) -> None:
        original = "abcdefgh" * 50
        result = finalize(original, "tiny", CrushKind.LOG)
        assert result.kind is CrushKind.LOG
        assert result.text == "tiny"
        assert result.crushed_tokens < result.original_tokens
        assert 0.0 <= result.ratio <= 1.0

    def test_equal_tokens_rejected(self) -> None:
        original = "abcdefgh" * 50
        result = finalize(original, original, CrushKind.JSON)
        assert result.kind is CrushKind.NONE


class TestJsonCrush:
    def test_large_array_shrinks(self) -> None:
        rows = [{"id": i, "name": f"item-{i}", "active": True} for i in range(300)]
        text = json.dumps(rows)
        result = crush_json(text)
        assert result.kind is CrushKind.JSON
        assert result.crushed_tokens < result.original_tokens
        # Schema keys preserved.
        decoded = json.loads(result.text)
        assert "_schema_keys" in decoded
        assert any("name" in k for k in decoded["_schema_keys"])

    def test_numeric_outliers_kept(self) -> None:
        text = json.dumps(list(range(500)))
        result = crush_json(text)
        assert result.kind is CrushKind.JSON
        decoded = json.loads(result.text)
        outliers = decoded["data"]["_crushed"]["outliers"]
        assert outliers["min"] == 0
        assert outliers["max"] == 499

    def test_invalid_json_no_op(self) -> None:
        text = "{this is : not json," * 40
        result = crush_json(text)
        assert result.kind is CrushKind.NONE
        assert result.text == text

    def test_small_object_not_expanded(self) -> None:
        text = json.dumps({"a": 1, "b": 2})
        result = crush_json(text)
        # Adding schema envelope would expand -> rejected.
        assert result.crushed_tokens <= result.original_tokens


class TestLogCrush:
    def test_repeated_lines_collapse(self) -> None:
        lines = [f"2026-06-16 10:00:00 worker {i % 2} processed batch 42" for i in range(80)]
        text = "\n".join(lines)
        result = crush_log(text)
        assert result.kind is CrushKind.LOG
        assert result.crushed_tokens < result.original_tokens
        assert "collapsed" in result.text

    def test_error_lines_preserved(self) -> None:
        lines = [f"INFO step {i} ok" for i in range(60)]
        lines.insert(30, "ERROR: database connection refused on host db-7")
        text = "\n".join(lines)
        result = crush_log(text)
        assert "ERROR: database connection refused" in result.text

    def test_unique_lines_no_op(self) -> None:
        # No repetition to collapse -> not smaller -> rejected.
        text = "\n".join(f"INFO unique event {i} value {i * 13 + 1}" for i in range(60))
        result = crush_log(text)
        # Either rejected (NONE) or genuinely smaller; never larger.
        assert result.crushed_tokens <= result.original_tokens


class TestDiffCrush:
    def test_context_runs_collapse(self) -> None:
        lines = ["diff --git a/f b/f", "index abc..def 100644", "@@ -1,40 +1,40 @@"]
        lines += [f" context line {i}" for i in range(40)]
        lines += ["-removed line", "+added line"]
        text = "\n".join(lines)
        result = crush_diff(text)
        assert result.kind is CrushKind.DIFF
        assert result.crushed_tokens < result.original_tokens
        # Change lines preserved.
        assert "-removed line" in result.text
        assert "+added line" in result.text
        # Noise dropped.
        assert "index abc..def" not in result.text

    def test_changes_preserved(self) -> None:
        lines = ["@@ -1,3 +1,3 @@"]
        lines += [f" ctx {i}" for i in range(20)]
        lines += [f"-old {i}" for i in range(5)]
        lines += [f"+new {i}" for i in range(5)]
        text = "\n".join(lines)
        result = crush_diff(text)
        for i in range(5):
            assert f"-old {i}" in result.text
            assert f"+new {i}" in result.text


class TestSearchCrush:
    def test_duplicates_deduped(self) -> None:
        lines = ["src/mod.py:10: TODO fix this" for _ in range(50)]
        text = "\n".join(lines)
        result = crush_search(text)
        assert result.kind is CrushKind.SEARCH
        assert result.crushed_tokens < result.original_tokens
        assert "(x50)" in result.text

    def test_distinct_lines_no_op(self) -> None:
        lines = [f"src/mod.py:{i}: distinct match {i}" for i in range(50)]
        text = "\n".join(lines)
        result = crush_search(text)
        # No duplicates -> not smaller -> rejected; never larger.
        assert result.crushed_tokens <= result.original_tokens
