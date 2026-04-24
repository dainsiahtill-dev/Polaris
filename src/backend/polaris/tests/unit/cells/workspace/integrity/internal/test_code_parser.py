"""Tests for polaris.cells.workspace.integrity.internal.code_parser."""

from __future__ import annotations

from polaris.cells.workspace.integrity.internal.code_parser import (
    CodeParser,
    get_code_parser,
)


class TestCodeParser:
    def test_init(self) -> None:
        parser = CodeParser()
        assert hasattr(parser, "available")
        assert hasattr(parser, "parsers")

    def test_get_parser_when_unavailable(self) -> None:
        parser = CodeParser()
        parser.available = False
        assert parser.get_parser("python") is None

    def test_parse_file_unsupported_extension(self) -> None:
        parser = CodeParser()
        result = parser.parse_file("content", ".unknown")
        assert result["parsed"] is False
        assert result["reason"] == "unsupported_or_missing_lib"

    def test_parse_file_no_lang(self) -> None:
        parser = CodeParser()
        result = parser.parse_file("content", ".txt")
        assert result["parsed"] is False

    def test_parse_file_when_unavailable(self) -> None:
        parser = CodeParser()
        parser.available = False
        result = parser.parse_file("x = 1", ".py")
        assert result["parsed"] is False
        assert result["reason"] == "unsupported_or_missing_lib"


class TestGetCodeParser:
    def test_returns_singleton(self) -> None:
        p1 = get_code_parser()
        p2 = get_code_parser()
        assert p1 is p2
        assert isinstance(p1, CodeParser)
