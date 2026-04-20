"""Tests for internal/react.py — ReActEngine JSON parsing and loop logic."""

from __future__ import annotations

from polaris.cells.roles.engine.internal.base import EngineContext, EngineStatus
from polaris.cells.roles.engine.internal.react import ReActEngine


def _make_engine(**kwargs) -> ReActEngine:
    return ReActEngine(workspace="/tmp", **kwargs)


class TestReActJSONParsing:
    """4-pass JSON parsing — tested in isolation via _parse_response."""

    def test_parse_direct_json(self) -> None:
        engine = _make_engine()
        raw = '{"thought":" 分析 ","action":"finish","action_input":{"answer":"done"}}'
        result = engine._parse_response(raw)
        assert result["thought"] == " 分析 "
        assert result["action"] == "finish"
        assert result["action_input"]["answer"] == "done"

    def test_parse_json_with_thought_action_only(self) -> None:
        engine = _make_engine()
        raw = '{"thought":"reasoning","action":"search_code","action_input":{"query":"x"}}'
        result = engine._parse_response(raw)
        assert result["thought"] == "reasoning"
        assert result["action"] == "search_code"

    def test_parse_response_with_preamble(self) -> None:
        """Parsing should succeed even with text before the JSON."""
        engine = _make_engine()
        raw = 'Here is my response:\n{"thought":"think","action":"finish","action_input":{"answer":"ok"}}'
        result = engine._parse_response(raw)
        assert result["action"] == "finish"

    def test_parse_malformed_json_falls_back_to_partial(self) -> None:
        """Invalid JSON should fall back to partial regex extraction."""
        engine = _make_engine()
        raw = '{"thought":"partial'
        result = engine._parse_response(raw)
        # Should not raise; should produce a result
        assert isinstance(result, dict)

    def test_parse_totally_invalid_returns_default(self) -> None:
        """Completely unparseable input should return a default finish response."""
        engine = _make_engine()
        raw = "!!! nothing here !!!"
        result = engine._parse_response(raw)
        assert result["action"] == "finish"
        assert "action_input" in result


class TestReActFindMatchingBracket:
    """Bracket matching for balanced JSON extraction."""

    def test_find_matching_brace_simple(self) -> None:
        engine = _make_engine()
        text = '{"key":"value"}'
        pos = engine._find_matching_bracket(text, 0, "{", "}")
        assert text[pos] == "}"
        assert pos == len(text) - 1

    def test_find_matching_brace_nested(self) -> None:
        engine = _make_engine()
        text = '{"a":{"b":"c"}}'
        start = text.find("{")
        pos = engine._find_matching_bracket(text, start, "{", "}")
        assert text[pos] == "}"
        assert pos == len(text) - 1

    def test_find_matching_brace_with_strings(self) -> None:
        engine = _make_engine()
        text = '{"key":"{nested}"}'
        start = text.find("{")
        pos = engine._find_matching_bracket(text, start, "{", "}")
        assert text[pos] == "}"
        # The "}" inside the string should not match
        assert pos == len(text) - 1

    def test_find_matching_brace_escaped_quote(self) -> None:
        engine = _make_engine()
        text = '{"key":"it\\"s done"}'
        start = text.find("{")
        pos = engine._find_matching_bracket(text, start, "{", "}")
        assert text[pos] == "}"
        assert pos == len(text) - 1

    def test_find_matching_brace_unmatched(self) -> None:
        engine = _make_engine()
        text = '{"key":'
        start = text.find("{")
        pos = engine._find_matching_bracket(text, start, "{", "}")
        assert pos == -1

    def test_find_matching_bracket_array(self) -> None:
        engine = _make_engine()
        text = "[[1,2],[3,4]]"
        start = text.find("[")
        pos = engine._find_matching_bracket(text, start, "[", "]")
        assert text[pos] == "]"
        assert pos == len(text) - 1


class TestReActExtractBalancedJSON:
    """Balanced-bracket JSON extraction fallback."""

    def test_extract_balanced_object(self) -> None:
        engine = _make_engine()
        text = 'some prefix {"key":"value"} some suffix'
        result = engine._extract_balanced_json(text)
        assert result == {"key": "value"}

    def test_extract_balanced_array(self) -> None:
        engine = _make_engine()
        text = "prefix [1,2,3] suffix"
        result = engine._extract_balanced_json(text)
        assert result == [1, 2, 3]

    def test_extract_balanced_nested(self) -> None:
        """Balanced-bracket extraction finds the outermost matching braces.

        For 'x {"a":{"b":[1,2]}} y', the algorithm starts at the first
        '{' (position 2), then walks to find its matching '}' (position 20,
        the outer object's closing brace, not the '}' inside "b":[1,2]).
        """
        engine = _make_engine()
        text = 'x {"a":{"b":[1,2]}} y'
        result = engine._extract_balanced_json(text)
        assert result == {"a": {"b": [1, 2]}}

    def test_extract_balanced_no_json(self) -> None:
        engine = _make_engine()
        result = engine._extract_balanced_json("no json here")
        assert result is None


class TestReActFindJSONObject:
    """First/last brace JSON object extraction."""

    def test_find_json_object_simple(self) -> None:
        engine = _make_engine()
        text = 'before {"key":"val"} after'
        result = engine._find_json_object(text)
        assert result == '{"key":"val"}'

    def test_find_json_object_no_brace(self) -> None:
        engine = _make_engine()
        result = engine._find_json_object("no braces")
        assert result is None

    def test_find_json_object_only_open(self) -> None:
        engine = _make_engine()
        result = engine._find_json_object("only { open")
        assert result is None


class TestReActStepLogic:
    """ReAct step() action dispatch."""

    def test_finish_action_returns_completed_status(self) -> None:
        """'finish' action should set status COMPLETED."""
        import asyncio

        engine = _make_engine(max_iterations=5)

        async def run():
            ctx = EngineContext(workspace="/tmp", role="director", task="test")
            result = await engine.step(ctx)
            return result

        loop = asyncio.new_event_loop()
        try:
            # First call — will try to parse empty LLM response
            # which falls back to finish action
            result = loop.run_until_complete(run())
            # Empty LLM response → fallback finish action
            assert result.status == EngineStatus.COMPLETED
        finally:
            loop.close()


class TestReActEngineLifecycle:
    """Engine lifecycle — init, can_continue, _build_prompt, _format_observation."""

    def test_max_iterations_capped_at_100(self) -> None:
        engine = _make_engine(max_iterations=999)
        assert engine.max_iterations == 100

    def test_max_iterations_minimum_1(self) -> None:
        engine = _make_engine(max_iterations=0)
        assert engine.max_iterations == 1

    def test_strategy_property(self) -> None:
        from polaris.cells.roles.engine.internal.base import EngineStrategy

        engine = _make_engine()
        assert engine.strategy == EngineStrategy.REACT

    def test_build_prompt_contains_task(self) -> None:
        engine = _make_engine()
        ctx = EngineContext(workspace="/tmp", role="director", task="implement login")
        prompt = engine._build_prompt(ctx)
        assert "implement login" in prompt

    def test_format_observation_with_content_key(self) -> None:
        engine = _make_engine()
        obs = engine._format_observation("search_code", {"content": "found 5 files"})
        assert "found 5 files" in obs

    def test_format_observation_with_output_key(self) -> None:
        engine = _make_engine()
        obs = engine._format_observation("run_command", {"output": "exit 0"})
        assert "exit 0" in obs

    def test_format_observation_with_error_key(self) -> None:
        engine = _make_engine()
        obs = engine._format_observation("search", {"error": "path not found"})
        assert "path not found" in obs

    def test_format_observation_with_none(self) -> None:
        engine = _make_engine()
        obs = engine._format_observation("act", {})  # Empty dict instead of None
        assert obs == "No result"

    def test_format_observation_truncates_long_output(self) -> None:
        engine = _make_engine()
        long_output = {"content": "x" * 1000}
        obs = engine._format_observation("act", long_output)
        assert len(obs) <= 500

    def test_prune_history(self) -> None:
        engine = _make_engine()
        # Fill history beyond limit
        engine._history = [
            {"thought": f"step {i}", "action": "a", "action_input": {}, "observation": "o"} for i in range(20)
        ]
        engine._max_history_length = 5
        engine._prune_history()
        assert len(engine._history) <= 5

    def test_build_partial_answer_with_history(self) -> None:
        engine = _make_engine()
        engine._history = [
            {"thought": "thinking about the solution", "action": "act", "action_input": {}, "observation": "obs"}
        ]
        engine._steps = [type("Step", (), {"step_index": 0})()]
        partial = engine._build_partial_answer()
        assert isinstance(partial, str)
        assert len(partial) > 0

    def test_build_partial_answer_empty_history(self) -> None:
        engine = _make_engine()
        engine._steps = []
        partial = engine._build_partial_answer()
        assert isinstance(partial, str)
        assert "0" in partial  # mentions step count
