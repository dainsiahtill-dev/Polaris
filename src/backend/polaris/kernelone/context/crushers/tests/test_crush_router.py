"""Tests for the crush router + content-type detection (T2-B).

Run with:
    pytest polaris/kernelone/context/crushers/tests/test_crush_router.py -v
"""

from __future__ import annotations

import json

from polaris.kernelone.context.crushers import (
    MIN_CRUSH_BYTES,
    CrushKind,
    crush_by_type,
    detect_content_type,
)


def _pad(text: str, target: int = MIN_CRUSH_BYTES + 256) -> str:
    """Pad text past the min-crush byte threshold without changing its type."""
    if len(text.encode("utf-8")) >= target:
        return text
    return text


class TestDetectContentType:
    def test_detect_json_object(self) -> None:
        text = json.dumps({"a": 1, "b": [1, 2, 3]})
        assert detect_content_type(text) is CrushKind.JSON

    def test_detect_json_array(self) -> None:
        text = json.dumps([{"x": 1}, {"x": 2}])
        assert detect_content_type(text) is CrushKind.JSON

    def test_detect_diff(self) -> None:
        text = "diff --git a/f b/f\n@@ -1,2 +1,2 @@\n-old\n+new\n context\n"
        assert detect_content_type(text) is CrushKind.DIFF

    def test_detect_log_by_level(self) -> None:
        text = "\n".join(f"[INFO] step {i} running" for i in range(10))
        assert detect_content_type(text) is CrushKind.LOG

    def test_detect_log_by_timestamp(self) -> None:
        text = "\n".join(f"2026-06-16 10:00:0{i} did a thing" for i in range(9))
        assert detect_content_type(text) is CrushKind.LOG

    def test_detect_search(self) -> None:
        text = "\n".join(f"src/mod.py:{i}: match here" for i in range(10))
        assert detect_content_type(text) is CrushKind.SEARCH

    def test_detect_plain_text_is_none(self) -> None:
        text = "just a paragraph of prose with no structure at all here"
        assert detect_content_type(text) is CrushKind.NONE

    def test_invalid_json_not_detected_as_json(self) -> None:
        text = "{not valid json at all"
        assert detect_content_type(text) is not CrushKind.JSON


class TestCrushByType:
    def test_below_threshold_is_skipped(self) -> None:
        small = json.dumps({"a": 1})
        assert len(small.encode("utf-8")) < MIN_CRUSH_BYTES
        result = crush_by_type(small)
        assert result.kind is CrushKind.NONE
        assert result.text == small

    def test_empty_is_no_op(self) -> None:
        result = crush_by_type("")
        assert result.kind is CrushKind.NONE
        assert result.text == ""

    def test_explicit_hint_string(self) -> None:
        rows = [{"id": i, "name": "row"} for i in range(200)]
        text = json.dumps(rows)
        result = crush_by_type(text, content_type="json")
        assert result.kind is CrushKind.JSON
        assert result.crushed_tokens < result.original_tokens

    def test_explicit_hint_enum(self) -> None:
        rows = [{"id": i} for i in range(200)]
        text = json.dumps(rows)
        result = crush_by_type(text, content_type=CrushKind.JSON)
        assert result.kind is CrushKind.JSON

    def test_unknown_hint_is_none(self) -> None:
        text = "x" * (MIN_CRUSH_BYTES + 10)
        result = crush_by_type(text, content_type="totally-unknown")
        assert result.kind is CrushKind.NONE
        assert result.text == text

    def test_result_never_expands(self) -> None:
        # Large but incompressible-ish content: result must never be bigger.
        text = "\n".join(f"unique line {i} with random text {i * 7}" for i in range(60))
        result = crush_by_type(text)
        assert result.crushed_tokens <= result.original_tokens
        assert len(result.text) <= len(text) or result.kind is CrushKind.NONE

    def test_utf8_cjk_preserved(self) -> None:
        rows = [{"名称": f"项目{i}", "值": i} for i in range(120)]
        text = json.dumps(rows, ensure_ascii=False)
        result = crush_by_type(text)
        # Round-trips through UTF-8 without raising and stays valid text.
        assert isinstance(result.text, str)
        result.text.encode("utf-8")
        assert result.ratio <= 1.0
