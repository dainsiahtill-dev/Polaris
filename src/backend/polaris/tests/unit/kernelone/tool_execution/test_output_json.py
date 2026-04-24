"""Tests for polaris.kernelone.tool_execution.output_json."""

from __future__ import annotations

from polaris.kernelone.tool_execution.output_json import parse_json_stdout


class TestParseJsonStdout:
    def test_empty_string(self) -> None:
        payload, error = parse_json_stdout("")
        assert payload == {}
        assert error is None

    def test_valid_json(self) -> None:
        payload, error = parse_json_stdout('{"ok": true}')
        assert payload == {"ok": True}
        assert error is None

    def test_valid_json_list(self) -> None:
        payload, error = parse_json_stdout("[1, 2, 3]")
        assert payload == [1, 2, 3]
        assert error is None

    def test_json_with_extra_log_lines(self) -> None:
        text = "INFO: starting\n{\"ok\": true, \"tool\": \"test\"}\nDONE"
        payload, error = parse_json_stdout(text)
        assert payload == {"ok": True, "tool": "test"}
        assert error is None

    def test_invalid_json(self) -> None:
        payload, error = parse_json_stdout("not json")
        assert payload is None
        assert error is not None

    def test_multiple_json_objects_prefers_contract_shape(self) -> None:
        text = '{\"a\": 1} {\"ok\": true}'
        payload, error = parse_json_stdout(text)
        assert payload == {"ok": True}
        assert error is None

    def test_none_input(self) -> None:
        payload, error = parse_json_stdout(None)  # type: ignore[arg-type]
        assert payload == {}
        assert error is None
