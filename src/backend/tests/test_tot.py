"""Tests for polaris.cells.roles.engine.internal.tot — exception observability

Focus: the three JSON-parsing fallback methods in ToTEngine must log debug
messages (never silently swallow exceptions) when _extract_balanced_json
raises.  All LLM calls are mocked; no network access.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

from polaris.cells.roles.engine.internal.tot import ToTEngine

# ---------------------------------------------------------------------------
# Minimal stubs so tests run without a real BaseEngine/LLM runtime
# ---------------------------------------------------------------------------

def _make_engine() -> ToTEngine:
    engine = ToTEngine(workspace="", max_branches=2, max_depth=2)
    return engine


# ---------------------------------------------------------------------------
# _parse_thoughts_response: balanced-extraction path raises → debug logged
# ---------------------------------------------------------------------------

class TestParseThoughtsResponseObservability:
    def test_logs_debug_when_balanced_extraction_raises(self, caplog):
        engine = _make_engine()

        # Force _extract_balanced_json to raise a ValueError
        with patch.object(engine, "_extract_balanced_json", side_effect=ValueError("corrupt")):
            with caplog.at_level(logging.DEBUG):
                result = engine._parse_thoughts_response("not json at all {{")

        # Must not raise; must return a degraded but non-empty list
        assert isinstance(result, list)
        assert len(result) >= 1

        # The failure must be observable in logs
        debug_records = [r for r in caplog.records if r.levelno <= logging.DEBUG]
        assert any("thoughts" in r.message for r in debug_records), (
            "A debug log mentioning 'thoughts' must be emitted when balanced extraction fails"
        )

    def test_no_exception_propagated_on_parse_failure(self, caplog):
        engine = _make_engine()

        with patch.object(engine, "_extract_balanced_json", side_effect=json.JSONDecodeError("x", "y", 0)):
            with caplog.at_level(logging.DEBUG):
                result = engine._parse_thoughts_response("{broken: json")

        assert isinstance(result, list), "Must always return a list, never raise"

    def test_valid_json_array_returned_directly(self):
        engine = _make_engine()
        payload = json.dumps([{"thought": "hello", "reasoning": "r", "confidence": 0.9}])
        result = engine._parse_thoughts_response(payload)
        assert result == [{"thought": "hello", "reasoning": "r", "confidence": 0.9}]

    def test_valid_json_object_wrapped_in_list(self):
        engine = _make_engine()
        payload = json.dumps({"thought": "hi", "reasoning": "r", "confidence": 0.8})
        result = engine._parse_thoughts_response(payload)
        assert isinstance(result, list)
        assert result[0]["thought"] == "hi"


# ---------------------------------------------------------------------------
# _parse_evaluation_response: balanced-extraction path raises → debug logged
# ---------------------------------------------------------------------------

class TestParseEvaluationResponseObservability:
    def test_logs_debug_when_balanced_extraction_raises(self, caplog):
        engine = _make_engine()

        with patch.object(engine, "_extract_balanced_json", side_effect=ValueError("bad bracket")):
            with caplog.at_level(logging.DEBUG):
                result = engine._parse_evaluation_response("not valid json {{{")

        assert isinstance(result, dict)
        # Must fall through to default {"score": 0.5, "reasoning": ""}
        assert "score" in result

        debug_records = [r for r in caplog.records if r.levelno <= logging.DEBUG]
        assert any("evaluation" in r.message for r in debug_records), (
            "A debug log mentioning 'evaluation' must be emitted when balanced extraction fails"
        )

    def test_no_exception_propagated_on_parse_failure(self, caplog):
        engine = _make_engine()

        with patch.object(engine, "_extract_balanced_json", side_effect=json.JSONDecodeError("x", "y", 0)):
            with caplog.at_level(logging.DEBUG):
                result = engine._parse_evaluation_response("garbage input")

        assert isinstance(result, dict), "Must always return a dict, never raise"

    def test_valid_json_returned_directly(self):
        engine = _make_engine()
        payload = json.dumps({"score": 0.85, "reasoning": "good", "feasibility": "high"})
        result = engine._parse_evaluation_response(payload)
        assert result["score"] == 0.85

    def test_default_score_on_total_parse_failure(self, caplog):
        """When all parse strategies fail, default dict with score=0.5 is returned."""
        engine = _make_engine()

        with patch.object(engine, "_extract_balanced_json", side_effect=ValueError("x")):
            with caplog.at_level(logging.DEBUG):
                result = engine._parse_evaluation_response("completely unparseable @@@ %%% ###")

        assert result.get("score") == 0.5


# ---------------------------------------------------------------------------
# _parse_finish_response: balanced-extraction path raises → debug logged
# ---------------------------------------------------------------------------

class TestParseFinishResponseObservability:
    def test_logs_debug_when_balanced_extraction_raises(self, caplog):
        engine = _make_engine()

        with patch.object(engine, "_extract_balanced_json", side_effect=ValueError("oops")):
            with caplog.at_level(logging.DEBUG):
                result = engine._parse_finish_response("invalid json input @@@")

        assert isinstance(result, dict)

        debug_records = [r for r in caplog.records if r.levelno <= logging.DEBUG]
        assert any("finish" in r.message for r in debug_records), (
            "A debug log mentioning 'finish' must be emitted when balanced extraction fails"
        )

    def test_no_exception_propagated_on_parse_failure(self, caplog):
        engine = _make_engine()

        with patch.object(engine, "_extract_balanced_json", side_effect=json.JSONDecodeError("x", "y", 0)):
            with caplog.at_level(logging.DEBUG):
                result = engine._parse_finish_response("bad input")

        assert isinstance(result, dict), "Must always return a dict, never raise"

    def test_valid_json_returned_directly(self):
        engine = _make_engine()
        payload = json.dumps({"answer": "42", "reasoning": "because", "selected": "plan A"})
        result = engine._parse_finish_response(payload)
        assert result["answer"] == "42"

    def test_answer_fallback_to_truncated_input(self, caplog):
        """When all strategies fail, answer defaults to first 200 chars of input."""
        engine = _make_engine()
        raw = "x" * 300  # longer than 200 chars, not valid JSON

        with patch.object(engine, "_extract_balanced_json", side_effect=ValueError("x")):
            with caplog.at_level(logging.DEBUG):
                result = engine._parse_finish_response(raw)

        assert "answer" in result
        assert len(result["answer"]) <= 200
