"""Action Parser 综合测试。"""

from polaris.cells.roles.kernel.internal.output.action_parser import (
    extract_thinking_block,
    parse_action_block,
)


class TestActionBlockParsing:
    """测试各种 Action 块格式。"""

    def test_standard_format(self) -> None:
        text = """[Action]: repo_tree
[Arguments]: {"path": ".", "depth": 2}
[Status]: In Progress
[Marker]: None"""
        block = parse_action_block(text)
        assert block is not None
        assert block.tool_name == "repo_tree"
        assert block.arguments == {"path": ".", "depth": 2}
        assert block.status == "In Progress"

    def test_with_marker_spaces(self) -> None:
        text = """[Action]: edit_file
[Arguments]: {"file": "test.py"}
[Status]: Completed
[Marker]: Added by agent"""
        block = parse_action_block(text)
        assert block is not None
        assert block.marker == "Added by agent"

    def test_complex_json_args(self) -> None:
        text = """[Action]: search_replace
[Arguments]: {"file": "a.py", "search": "old", "replace": "new", "flags": ["i", "g"]}
[Status]: Completed
[Marker]: None"""
        block = parse_action_block(text)
        assert block.arguments["flags"] == ["i", "g"]

    def test_empty_json_args(self) -> None:
        text = """[Action]: test
[Arguments]: {}
[Status]: Completed
[Marker]: None"""
        block = parse_action_block(text)
        assert block.arguments == {}

    def test_missing_action_returns_none(self) -> None:
        text = "No action here"
        assert parse_action_block(text) is None

    def test_only_action_returns_none(self) -> None:
        text = "[Action]: test_only"
        assert parse_action_block(text) is None


class TestThinkingExtraction:
    """测试 <thinking> 块提取。"""

    def test_simple_thinking(self) -> None:
        text = "<thinking>My thoughts</thinking> [Action]: test"
        assert extract_thinking_block(text) == "My thoughts"

    def test_multiline_thinking(self) -> None:
        text = """<thinking>
Line 1
Line 2
</thinking>"""
        result = extract_thinking_block(text)
        assert "Line 1" in result
        assert "Line 2" in result

    def test_thinking_with_leading_whitespace(self) -> None:
        text = "    <thinking>  Spaced thoughts  </thinking>"
        assert extract_thinking_block(text) == "Spaced thoughts"

    def test_no_thinking_returns_none(self) -> None:
        text = "No thinking block"
        assert extract_thinking_block(text) is None

    def test_empty_thinking_returns_none(self) -> None:
        text = "<thinking></thinking>"
        result = extract_thinking_block(text)
        assert result == "" or result is None
