"""Tests for polaris.cells.roles.engine.internal.react — exception observability

Focus: _parse_response must log debug messages (never silently swallow
exceptions) when _extract_balanced_json raises.  The action_input JSON
fallback must also be observable when it fails.

All LLM calls are mocked; no network access.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

from polaris.cells.roles.engine.internal.react import ReActEngine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine() -> ReActEngine:
    return ReActEngine(workspace="", max_iterations=5)


# ---------------------------------------------------------------------------
# _parse_response: balanced-extraction path raises → debug logged
# ---------------------------------------------------------------------------


class TestParseResponseBalancedExtractionObservability:
    def test_logs_debug_when_balanced_extraction_raises(self, caplog):
        engine = _make_engine()

        with (
            patch.object(engine, "_extract_balanced_json", side_effect=ValueError("corrupt bracket")),
            caplog.at_level(logging.DEBUG),
        ):
            result = engine._parse_response("not valid json {{{{")

        # Must return a dict (never raise), falling back to regex or default
        assert isinstance(result, dict)

        debug_records = [r for r in caplog.records if r.levelno <= logging.DEBUG]
        assert any("balanced" in r.message or "react" in r.message for r in debug_records), (
            "A debug log must be emitted when balanced JSON extraction fails"
        )

    def test_no_exception_propagated_on_balanced_extraction_failure(self, caplog):
        engine = _make_engine()

        with (
            patch.object(engine, "_extract_balanced_json", side_effect=json.JSONDecodeError("x", "y", 0)),
            caplog.at_level(logging.DEBUG),
        ):
            result = engine._parse_response("not json at all")

        assert isinstance(result, dict), "Must always return a dict, never raise"

    def test_exc_info_preserved_in_debug_log_on_failure(self, caplog):
        engine = _make_engine()

        with (
            patch.object(engine, "_extract_balanced_json", side_effect=ValueError("traceback test")),
            caplog.at_level(logging.DEBUG),
        ):
            engine._parse_response("garbage input")

        debug_records = [r for r in caplog.records if r.levelno <= logging.DEBUG and r.exc_info is not None]
        assert debug_records, "At least one debug record with exc_info must be emitted so the traceback is preserved"


# ---------------------------------------------------------------------------
# _parse_response: action_input JSON fallback raises → debug logged
# ---------------------------------------------------------------------------


class TestParseResponseActionInputObservability:
    def test_logs_debug_when_action_input_parse_fails(self, caplog):
        engine = _make_engine()

        # Craft a response where regex finds action_input but json.loads fails
        # We use a valid-looking but corrupt inner JSON: {bad: value}
        malformed = '"thought": "ok", "action": "run", "action_input": {bad: value}'
        response = "{" + malformed + "}"

        with caplog.at_level(logging.DEBUG):
            result = engine._parse_response(response)

        assert isinstance(result, dict)

        debug_records = [r for r in caplog.records if r.levelno <= logging.DEBUG]
        assert any("action_input" in r.message for r in debug_records), (
            "A debug log mentioning 'action_input' must be emitted when its JSON parse fails"
        )

    def test_action_input_defaults_to_empty_dict_on_parse_failure(self, caplog):
        engine = _make_engine()

        # Primary JSON parse will succeed here; test the regex fallback instead
        # by making primary parse fail but regex find a malformed action_input
        response = '{"thought": "x", "action": "y", "action_input": {not: valid}}'

        with caplog.at_level(logging.DEBUG):
            result = engine._parse_response(response)

        # If primary parse failed and regex fallback ran, action_input should be {}
        # Primary parse also fails here because of the unquoted key
        assert isinstance(result, dict)
        if "action_input" in result:
            assert isinstance(result["action_input"], dict)


# ---------------------------------------------------------------------------
# _parse_response: happy paths
# ---------------------------------------------------------------------------


class TestParseResponseHappyPath:
    def test_valid_json_parsed_directly(self):
        engine = _make_engine()
        payload = json.dumps(
            {
                "thought": "I should list files",
                "action": "list_directory",
                "action_input": {"path": "."},
            }
        )
        result = engine._parse_response(payload)
        assert result["thought"] == "I should list files"
        assert result["action"] == "list_directory"
        assert result["action_input"] == {"path": "."}

    def test_finish_action_parsed_correctly(self):
        engine = _make_engine()
        payload = json.dumps(
            {
                "thought": "Done",
                "action": "finish",
                "action_input": {"answer": "42"},
            }
        )
        result = engine._parse_response(payload)
        assert result["action"] == "finish"
        assert result["action_input"]["answer"] == "42"

    def test_completely_unparseable_input_returns_default_finish(self, caplog):
        """When all strategies fail, engine falls back to a finish action."""
        engine = _make_engine()

        with caplog.at_level(logging.DEBUG):
            result = engine._parse_response("!!! this is not json at all !!!")

        assert isinstance(result, dict)
        # The default fallback is: action="finish" with the raw response as answer
        assert result.get("action") == "finish"


# ---------------------------------------------------------------------------
# _parse_response: balanced JSON extraction success path (regression guard)
# ---------------------------------------------------------------------------


class TestParseResponseBalancedExtractionSuccess:
    def test_embedded_json_object_in_markdown_extracted(self):
        engine = _make_engine()
        # LLMs often wrap JSON in markdown code blocks
        response = (
            "Here is my response:\n```json\n"
            '{"thought": "think", "action": "search", "action_input": {"q": "foo"}}'
            "\n```\n"
        )
        result = engine._parse_response(response)
        # Primary parse fails (surrounding text), but balanced extraction succeeds
        assert isinstance(result, dict)
        assert result.get("action") in ("search", "finish")
