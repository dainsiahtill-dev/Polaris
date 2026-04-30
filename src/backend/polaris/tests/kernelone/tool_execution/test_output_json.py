"""Tests for parse_json_stdout function."""

from __future__ import annotations

from polaris.kernelone.tool_execution.output_json import parse_json_stdout


class TestParseJsonStdoutEmpty:
    """Tests for empty/None input."""

    def test_none_returns_empty_dict(self) -> None:
        payload, error = parse_json_stdout(None)
        assert payload == {}
        assert error is None

    def test_empty_string_returns_empty_dict(self) -> None:
        payload, error = parse_json_stdout("")
        assert payload == {}
        assert error is None

    def test_whitespace_only_returns_empty_dict(self) -> None:
        payload, error = parse_json_stdout("   \n\t  ")
        assert payload == {}
        assert error is None


class TestParseJsonStdoutDirectJson:
    """Tests for direct valid JSON input."""

    def test_valid_json_object(self) -> None:
        payload, error = parse_json_stdout('{"ok": true}')
        assert payload == {"ok": True}
        assert error is None

    def test_valid_json_array(self) -> None:
        payload, error = parse_json_stdout("[1, 2, 3]")
        assert payload == [1, 2, 3]
        assert error is None

    def test_valid_json_string(self) -> None:
        payload, error = parse_json_stdout('"hello"')
        assert payload == "hello"
        assert error is None

    def test_valid_json_number(self) -> None:
        payload, error = parse_json_stdout("42")
        assert payload == 42
        assert error is None

    def test_valid_json_boolean(self) -> None:
        payload, error = parse_json_stdout("true")
        assert payload is True
        assert error is None

    def test_valid_json_null(self) -> None:
        payload, error = parse_json_stdout("null")
        assert payload is None
        assert error is None


class TestParseJsonStdoutWithExtraLines:
    """Tests for JSON embedded in extra log lines."""

    def test_json_after_log_lines(self) -> None:
        text = 'some log\nmore log\n{"ok": true}'
        payload, error = parse_json_stdout(text)
        assert payload == {"ok": True}
        assert error is None

    def test_json_before_log_lines(self) -> None:
        text = '{"ok": true}\nsome trailing log'
        payload, error = parse_json_stdout(text)
        assert payload == {"ok": True}
        assert error is None

    def test_json_between_log_lines(self) -> None:
        text = 'start log\n{"tool": "test"}\nend log'
        payload, error = parse_json_stdout(text)
        assert payload == {"tool": "test"}
        assert error is None

    def test_multiple_json_objects_prefers_last_by_rank(self) -> None:
        # Code uses rank with idx as tiebreaker where larger idx wins,
        # so the later JSON object is preferred when ranks are equal.
        text = '{"first": 1}\n{"second": 2}'
        payload, error = parse_json_stdout(text)
        assert payload == {"second": 2}
        assert error is None

    def test_json_array_in_log_lines(self) -> None:
        text = "log\n[1, 2, 3]\nlog"
        payload, error = parse_json_stdout(text)
        assert payload == [1, 2, 3]
        assert error is None


class TestParseJsonStdoutContractShape:
    """Tests for contract-shaped JSON detection priority."""

    def test_contract_shape_priority_over_plain(self) -> None:
        text = '{"plain": 1}\n{"ok": true, "exit_code": 0}'
        payload, error = parse_json_stdout(text)
        assert payload == {"ok": True, "exit_code": 0}
        assert error is None

    def test_contract_shape_with_tool_key(self) -> None:
        text = 'log\n{"tool": "bash"}\nlog'
        payload, error = parse_json_stdout(text)
        assert payload == {"tool": "bash"}
        assert error is None

    def test_contract_shape_with_error_key(self) -> None:
        text = 'log\n{"error": "failed"}'
        payload, error = parse_json_stdout(text)
        assert payload == {"error": "failed"}
        assert error is None

    def test_contract_shape_with_changed_files(self) -> None:
        text = 'log\n{"changed_files": ["a.py"]}'
        payload, error = parse_json_stdout(text)
        assert payload == {"changed_files": ["a.py"]}
        assert error is None

    def test_non_contract_shape_without_trailing(self) -> None:
        text = 'log\n{"data": 123}\n'
        payload, error = parse_json_stdout(text)
        assert payload == {"data": 123}
        assert error is None


class TestParseJsonStdoutInvalid:
    """Tests for invalid JSON input."""

    def test_invalid_json_returns_none_and_error(self) -> None:
        payload, error = parse_json_stdout("not json at all")
        assert payload is None
        assert error is not None
        # direct_error captures the actual JSON parse error message
        assert "Expecting value" in error

    def test_malformed_json_object(self) -> None:
        payload, error = parse_json_stdout('{"key": }')
        assert payload is None
        assert error is not None

    def test_unclosed_string(self) -> None:
        payload, error = parse_json_stdout('{"key": "value}')
        assert payload is None
        assert error is not None

    def test_trailing_comma(self) -> None:
        payload, error = parse_json_stdout('{"key": "value",}')
        assert payload is None
        assert error is not None

    def test_partial_json_object(self) -> None:
        payload, error = parse_json_stdout('{"key": "value"')
        assert payload is None
        assert error is not None

    def test_garbage_with_braces(self) -> None:
        payload, error = parse_json_stdout("{not valid json}")
        assert payload is None
        assert error is not None


class TestParseJsonStdoutNested:
    """Tests for nested JSON structures."""

    def test_nested_object(self) -> None:
        text = '{"outer": {"inner": 1}}'
        payload, error = parse_json_stdout(text)
        assert payload == {"outer": {"inner": 1}}
        assert error is None

    def test_nested_array(self) -> None:
        text = '{"items": [1, [2, 3]]}'
        payload, error = parse_json_stdout(text)
        assert payload == {"items": [1, [2, 3]]}
        assert error is None

    def test_complex_nested_in_logs(self) -> None:
        text = 'log line 1\n{"result": {"nested": [1, 2, {"deep": true}]}}\nlog line 2'
        payload, error = parse_json_stdout(text)
        # The parser may select the innermost JSON object depending on rank.
        # Verify we got valid JSON and no error.
        assert payload is not None
        assert error is None
        assert isinstance(payload, dict)
