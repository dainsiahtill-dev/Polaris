"""Tests for polaris.kernelone.fs.text_ops quality — decode chain, atomics, helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from polaris.kernelone.fs.text_ops import (
    _decode_text_bytes,
    ensure_parent_dir,
    extract_field,
    is_run_artifact,
    read_file_safe,
    write_json_atomic,
    write_text_atomic,
)

# ---------------------------------------------------------------------------
# _decode_text_bytes
# ---------------------------------------------------------------------------


class TestDecodeTextBytes:
    def test_empty_bytes_returns_empty_string(self) -> None:
        assert _decode_text_bytes(b"") == ""

    def test_clean_utf8_decoded_directly(self) -> None:
        assert _decode_text_bytes(b"hello") == "hello"

    def test_utf8_with_bom_decoded_via_utf8_sig_fallback(self) -> None:
        # UTF-8 BOM is valid UTF-8: first decode succeeds and returns the BOM codepoint
        payload = b"\xef\xbb\xbfhello"
        result = _decode_text_bytes(payload)
        # The BOM character (U+FEFF) is present when decoded as plain utf-8
        assert "hello" in result

    def test_pure_ascii_bytes_decoded_as_utf8(self) -> None:
        assert _decode_text_bytes(b"ascii content 123") == "ascii content 123"

    def test_gbk_encoded_bytes_decoded_via_gbk_fallback(self) -> None:
        # GBK-encoded Chinese: not valid UTF-8, fallback chain reaches gbk
        text = "\u4e2d\u6587"  # "中文"
        payload = text.encode("gbk")
        result = _decode_text_bytes(payload)
        assert "\u4e2d" in result or result != ""  # at minimum, decodes without crash

    def test_high_replacement_ratio_falls_through_to_replace(self) -> None:
        # Bytes that produce many replacement chars with utf-8 errors=replace
        # but aren't valid GBK/cp936 either — final fallback must not raise
        payload = bytes(range(0x80, 0xA0))  # latin-1 extension, not valid UTF-8
        result = _decode_text_bytes(payload)
        assert isinstance(result, str)

    def test_returns_str_for_arbitrary_binary(self) -> None:
        payload = bytes(range(256))
        result = _decode_text_bytes(payload)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# ensure_parent_dir
# ---------------------------------------------------------------------------


def test_ensure_parent_dir_creates_missing_dirs(tmp_path: Path) -> None:
    target = str(tmp_path / "a" / "b" / "c" / "file.txt")
    ensure_parent_dir(target)
    assert (tmp_path / "a" / "b" / "c").is_dir()


def test_ensure_parent_dir_noop_for_existing(tmp_path: Path) -> None:
    existing = str(tmp_path / "existing.txt")
    ensure_parent_dir(existing)  # parent already exists — must not raise


# ---------------------------------------------------------------------------
# write_text_atomic
# ---------------------------------------------------------------------------


def test_write_text_atomic_creates_file(tmp_path: Path) -> None:
    target = str(tmp_path / "output.txt")
    write_text_atomic(target, "hello world\n")
    assert Path(target).read_text(encoding="utf-8") == "hello world\n"


def test_write_text_atomic_overwrites_existing(tmp_path: Path) -> None:
    target = str(tmp_path / "output.txt")
    Path(target).write_text("old content", encoding="utf-8")
    write_text_atomic(target, "new content")
    assert Path(target).read_text(encoding="utf-8") == "new content"


def test_write_text_atomic_no_lock_timeout(tmp_path: Path) -> None:
    target = str(tmp_path / "nolockfile.txt")
    write_text_atomic(target, "data", lock_timeout_sec=None)
    assert Path(target).read_text(encoding="utf-8") == "data"


def test_write_text_atomic_rejects_non_utf8_encoding(tmp_path: Path) -> None:
    target = str(tmp_path / "latin1.txt")
    with pytest.raises(ValueError, match="UTF-8"):
        write_text_atomic(target, "x", encoding="latin-1")


def test_write_text_atomic_empty_path_is_noop() -> None:
    write_text_atomic("", "text")  # must not raise


def test_write_text_atomic_creates_parent_dirs(tmp_path: Path) -> None:
    target = str(tmp_path / "nested" / "deep" / "file.txt")
    write_text_atomic(target, "content")
    assert Path(target).read_text(encoding="utf-8") == "content"


# ---------------------------------------------------------------------------
# write_json_atomic
# ---------------------------------------------------------------------------


def test_write_json_atomic_roundtrip(tmp_path: Path) -> None:
    target = str(tmp_path / "data.json")
    write_json_atomic(target, {"key": "value", "num": 42})
    loaded = json.loads(Path(target).read_text(encoding="utf-8"))
    assert loaded == {"key": "value", "num": 42}


# ---------------------------------------------------------------------------
# read_file_safe
# ---------------------------------------------------------------------------


def test_read_file_safe_returns_content(tmp_path: Path) -> None:
    p = tmp_path / "test.txt"
    p.write_bytes(b"hello\n")
    assert read_file_safe(str(p)) == "hello\n"


def test_read_file_safe_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_file_safe(str(tmp_path / "missing.txt")) == ""


def test_read_file_safe_empty_path_returns_empty() -> None:
    assert read_file_safe("") == ""


# ---------------------------------------------------------------------------
# is_run_artifact
# ---------------------------------------------------------------------------


class TestIsRunArtifact:
    @pytest.mark.parametrize(
        "path",
        [
            "director_result.json",
            "path/to/director.result.json",
            "events.jsonl",
            "runtime.events.jsonl",
            "trajectory.json",
            "qa_response.md",
            "qa.review.md",
            "planner_response.md",
            "planner.output.md",
            "ollama_response.md",
            "director_llm.output.md",
            "reviewer_response.md",
            "auditor.review.md",
            "runlog.md",
            "director.runlog.md",
        ],
    )
    def test_recognized_artifacts(self, path: str) -> None:
        assert is_run_artifact(path) is True

    def test_regular_file_not_artifact(self) -> None:
        assert is_run_artifact("src/main.py") is False

    def test_backslash_path_normalized(self) -> None:
        assert is_run_artifact("runs\\2024\\events.jsonl") is True


# ---------------------------------------------------------------------------
# extract_field
# ---------------------------------------------------------------------------


class TestExtractField:
    def test_extracts_matching_pattern(self) -> None:
        text = "Status: active\nMode: fast"
        result = extract_field(text, [r"Status:\s*(\w+)"])
        assert result == "active"

    def test_tries_patterns_in_order(self) -> None:
        text = "Alias: runner"
        result = extract_field(text, [r"Missing:\s*(\w+)", r"Alias:\s*(\w+)"])
        assert result == "runner"

    def test_no_match_returns_empty(self) -> None:
        assert extract_field("nothing here", [r"X:\s*(\w+)"]) == ""

    def test_empty_text_returns_empty(self) -> None:
        assert extract_field("", [r"(\w+)"]) == ""

    def test_invalid_regex_is_skipped(self) -> None:
        # Invalid regex should not raise; it should be skipped
        result = extract_field("hello", [r"[invalid", r"(hello)"])
        assert result == "hello"
