"""Action Parser 单元测试。"""

from polaris.cells.roles.kernel.internal.output.action_parser import (
    extract_thinking_block,
    parse_action_block,
)


def test_basic_action_block() -> None:
    text = """[Action]: repo_tree
[Arguments]: {"path": "."}
[Status]: In Progress
[Marker]: None"""
    block = parse_action_block(text)
    assert block is not None
    assert block.tool_name == "repo_tree"
    assert block.arguments == {"path": "."}
    assert block.status == "In Progress"


def test_with_marker() -> None:
    text = """[Action]: edit_file
[Arguments]: {"file": "test.py"}
[Status]: Completed
[Marker]: Added by agent"""
    block = parse_action_block(text)
    assert block.marker == "Added by agent"


def test_complex_json_args() -> None:
    text = """[Action]: search_replace
[Arguments]: {"file": "a.py", "search": "old", "replace": "new", "flags": ["i", "g"]}
[Status]: Completed
[Marker]: None"""
    block = parse_action_block(text)
    assert block.arguments["flags"] == ["i", "g"]


def test_invalid_json_fallback() -> None:
    text = """[Action]: test
[Arguments]: {invalid json}
[Status]: Completed
[Marker]: None"""
    block = parse_action_block(text)
    assert block.arguments == {}


def test_missing_action_returns_none() -> None:
    text = "No action here"
    assert parse_action_block(text) is None


def test_thinking_extraction() -> None:
    text = "<thinking>My thoughts</thinking> [Action]: test"
    assert extract_thinking_block(text) == "My thoughts"


def test_multiline_thinking() -> None:
    text = """<thinking>
Line 1
Line 2
</thinking>"""
    assert "Line 1" in extract_thinking_block(text)


def test_no_thinking_returns_none() -> None:
    text = "No thinking block"
    assert extract_thinking_block(text) is None
