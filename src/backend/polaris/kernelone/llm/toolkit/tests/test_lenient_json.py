"""ADR-0090: bounded lenient JSON repair for weak-model tool arguments."""

from __future__ import annotations

from polaris.kernelone.llm.toolkit.parsers.lenient_json import parse_lenient_json_object


class TestStrictPassThrough:
    def test_valid_object_not_marked_repaired(self) -> None:
        obj, repaired = parse_lenient_json_object('{"pattern": "class X", "max": 5}')

        assert obj == {"pattern": "class X", "max": 5}
        assert repaired is False

    def test_non_object_json_rejected(self) -> None:
        assert parse_lenient_json_object('["a", "b"]') == (None, False)
        assert parse_lenient_json_object('"text"') == (None, False)

    def test_non_string_input_rejected(self) -> None:
        assert parse_lenient_json_object(None) == (None, False)
        assert parse_lenient_json_object(123) == (None, False)


class TestRepairs:
    def test_trailing_comma(self) -> None:
        obj, repaired = parse_lenient_json_object('{"file": "a.py", "start": 1,}')

        assert obj == {"file": "a.py", "start": 1}
        assert repaired is True

    def test_single_quotes(self) -> None:
        obj, repaired = parse_lenient_json_object("{'pattern': 'class X'}")

        assert obj == {"pattern": "class X"}
        assert repaired is True

    def test_unescaped_newline_inside_string(self) -> None:
        obj, repaired = parse_lenient_json_object('{"replace": "line1\nline2"}')

        assert obj == {"replace": "line1\nline2"}
        assert repaired is True

    def test_missing_closing_brace(self) -> None:
        obj, repaired = parse_lenient_json_object('{"pattern": "class ExpressionWrapper"')

        assert obj == {"pattern": "class ExpressionWrapper"}
        assert repaired is True

    def test_unterminated_string_and_brace(self) -> None:
        obj, repaired = parse_lenient_json_object('{"pattern": "class X')

        assert obj == {"pattern": "class X"}
        assert repaired is True

    def test_code_fence_stripped(self) -> None:
        obj, repaired = parse_lenient_json_object('```json\n{"file": "a.py"}\n```')

        assert obj == {"file": "a.py"}
        assert repaired is True

    def test_smart_quotes(self) -> None:
        obj, repaired = parse_lenient_json_object("{“pattern”: “x”}")

        assert obj == {"pattern": "x"}
        assert repaired is True

    def test_combined_failures(self) -> None:
        obj, repaired = parse_lenient_json_object('{"file": "a.py", "blocks": ["x", "y",]')

        assert obj == {"file": "a.py", "blocks": ["x", "y"]}
        assert repaired is True


class TestFailClosed:
    def test_hopeless_garbage_returns_none(self) -> None:
        assert parse_lenient_json_object("call repo_rg with pattern class X") == (None, False)

    def test_deeply_unbalanced_rejected(self) -> None:
        text = "{" * 20 + '"a": 1'

        obj, _ = parse_lenient_json_object(text)

        assert obj is None

    def test_apostrophe_inside_double_quoted_string_safe(self) -> None:
        obj, repaired = parse_lenient_json_object('{"msg": "it\'s fine"}')

        assert obj == {"msg": "it's fine"}
        assert repaired is False
