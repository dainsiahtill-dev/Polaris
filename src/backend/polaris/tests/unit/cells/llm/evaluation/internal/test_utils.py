"""Unit tests for polaris.cells.llm.evaluation.internal.utils."""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.llm.evaluation.internal.utils import (
    cosine_similarity,
    dedupe,
    get_embedding_vector,
    indent,
    looks_like_deflection,
    looks_like_structured_steps,
    new_test_run_id,
    semantic_criteria_hits,
    split_thinking_output,
    truncate,
    utc_now,
    write_json_atomic,
)


class TestUtcNow:
    """Tests for utc_now function."""

    def test_returns_iso_string(self) -> None:
        result = utc_now()
        assert isinstance(result, str)
        assert "T" in result or "+" in result or result.endswith("Z")


class TestNewTestRunId:
    """Tests for new_test_run_id function."""

    def test_returns_string(self) -> None:
        run_id = new_test_run_id()
        assert isinstance(run_id, str)
        assert len(run_id) == 8

    def test_unique(self) -> None:
        ids = {new_test_run_id() for _ in range(20)}
        assert len(ids) == 20


class TestDedupe:
    """Tests for dedupe function."""

    def test_removes_duplicates(self) -> None:
        assert dedupe(["a", "b", "a", "c"]) == ["a", "b", "c"]

    def test_case_insensitive(self) -> None:
        assert dedupe(["A", "a", "B"]) == ["A", "B"]

    def test_strips_whitespace(self) -> None:
        assert dedupe(["  a  ", "a"]) == ["a"]

    def test_skips_empty(self) -> None:
        assert dedupe(["", "a", "  ", "b"]) == ["a", "b"]

    def test_preserves_order(self) -> None:
        assert dedupe(["z", "a", "z", "b"]) == ["z", "a", "b"]


class TestTruncate:
    """Tests for truncate function."""

    def test_no_truncate_short(self) -> None:
        assert truncate("hello", 10) == "hello"

    def test_truncate_long(self) -> None:
        assert truncate("hello world", 8) == "hello..."

    def test_empty(self) -> None:
        assert truncate("", 5) == ""

    def test_none(self) -> None:
        assert truncate(None, 5) == ""  # type: ignore[arg-type]


class TestIndent:
    """Tests for indent function."""

    def test_basic(self) -> None:
        assert indent("line1\nline2", spaces=2) == "  line1\n  line2"

    def test_single_line(self) -> None:
        assert indent("hello", spaces=4) == "    hello"

    def test_default_spaces(self) -> None:
        result = indent("hello")
        assert result.startswith("  ")


class TestSplitThinkingOutput:
    """Tests for split_thinking_output function."""

    def test_thinking_tag(self) -> None:
        text = "<thinking>reasoning</thinking>answer"
        thinking, answer = split_thinking_output(text)
        assert thinking == "reasoning"
        assert answer == "answer"

    def test_think_tag(self) -> None:
        text = "<think>reasoning</think>answer"
        thinking, _answer = split_thinking_output(text)
        assert thinking == "reasoning"

    def test_no_thinking(self) -> None:
        text = "just an answer"
        thinking, answer = split_thinking_output(text)
        assert thinking == ""
        assert answer == "just an answer"

    def test_empty(self) -> None:
        thinking, answer = split_thinking_output("")
        assert thinking == ""
        assert answer == ""


class TestLooksLikeDeflection:
    """Tests for looks_like_deflection function."""

    def test_detects_cannot(self) -> None:
        assert looks_like_deflection("I cannot do that") is True

    def test_detects_cant(self) -> None:
        assert looks_like_deflection("I can't help") is True

    def test_detects_unable(self) -> None:
        assert looks_like_deflection("I'm not able to") is True

    def test_detects_as_ai(self) -> None:
        assert looks_like_deflection("As an AI, I cannot") is True

    def test_no_deflection(self) -> None:
        assert looks_like_deflection("Here is the solution") is False

    def test_empty(self) -> None:
        assert looks_like_deflection("") is False


class TestLooksLikeStructuredSteps:
    """Tests for looks_like_structured_steps function."""

    def test_numbered_list(self) -> None:
        assert looks_like_structured_steps("1. First\n2. Second") is True

    def test_bullet_list(self) -> None:
        assert looks_like_structured_steps("- Item 1\n- Item 2") is True

    def test_step_keyword(self) -> None:
        assert looks_like_structured_steps("Step 1: do this") is True

    def test_first_second(self) -> None:
        assert looks_like_structured_steps("First, do this. Second, do that.") is True

    def test_no_structure(self) -> None:
        assert looks_like_structured_steps("Just a plain paragraph.") is False

    def test_empty(self) -> None:
        assert looks_like_structured_steps("") is False


class TestCosineSimilarity:
    """Tests for cosine_similarity function."""

    def test_identical_vectors(self) -> None:
        vec = [1.0, 0.0, 0.0]
        assert math.isclose(cosine_similarity(vec, vec), 1.0)

    def test_orthogonal_vectors(self) -> None:
        assert math.isclose(cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_opposite_vectors(self) -> None:
        assert math.isclose(cosine_similarity([1.0, 0.0], [-1.0, 0.0]), -1.0)

    def test_different_lengths(self) -> None:
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0

    def test_empty_vectors(self) -> None:
        assert cosine_similarity([], []) == 0.0

    def test_zero_vector(self) -> None:
        assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


class TestGetEmbeddingVector:
    """Tests for get_embedding_vector function."""

    def test_port_not_set_raises(self) -> None:
        with (
            patch(
                "polaris.kernelone.llm.embedding.get_default_embedding_port",
                side_effect=RuntimeError("not set"),
            ),
            pytest.raises(RuntimeError, match="KernelEmbeddingPort is not set"),
        ):
            get_embedding_vector("hello")

    def test_port_returns_vector(self) -> None:
        mock_port = MagicMock()
        mock_port.get_embedding.return_value = [0.1, 0.2, 0.3]
        with patch(
            "polaris.kernelone.llm.embedding.get_default_embedding_port",
            return_value=mock_port,
        ):
            result = get_embedding_vector("hello")
        assert result == [0.1, 0.2, 0.3]

    def test_port_raises_returns_none(self) -> None:
        mock_port = MagicMock()
        mock_port.get_embedding.side_effect = ValueError("fail")
        with patch(
            "polaris.kernelone.llm.embedding.get_default_embedding_port",
            return_value=mock_port,
        ):
            result = get_embedding_vector("hello")
        assert result is None

    def test_empty_vector_returns_none(self) -> None:
        mock_port = MagicMock()
        mock_port.get_embedding.return_value = []
        with patch(
            "polaris.kernelone.llm.embedding.get_default_embedding_port",
            return_value=mock_port,
        ):
            result = get_embedding_vector("hello")
        assert result is None


class TestSemanticCriteriaHits:
    """Tests for semantic_criteria_hits function."""

    def test_no_criteria(self) -> None:
        assert semantic_criteria_hits("answer", []) == {}

    def test_embedding_failure(self) -> None:
        with patch(
            "polaris.cells.llm.evaluation.internal.utils.get_embedding_vector",
            return_value=None,
        ):
            result = semantic_criteria_hits("answer", ["c1", "c2"])
        assert result == {"c1": 0.0, "c2": 0.0}

    def test_success(self) -> None:
        with patch(
            "polaris.cells.llm.evaluation.internal.utils.get_embedding_vector",
            return_value=[1.0, 0.0],
        ):
            result = semantic_criteria_hits("answer", ["c1"])
        assert "c1" in result
        assert isinstance(result["c1"], float)


class TestWriteJsonAtomic:
    """Tests for write_json_atomic function."""

    def test_writes_json(self, tmp_path) -> None:
        path = str(tmp_path / "test.json")
        write_json_atomic(path, {"key": "value"})
        import json

        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data == {"key": "value"}
