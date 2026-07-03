from polaris.kernelone.tool_execution.output_json import parse_json_stdout


def test_parse_json_stdout_accepts_clean_json():
    payload, error = parse_json_stdout('{"ok": true, "tool": "edit_blocks"}')

    assert error is None
    assert isinstance(payload, dict)
    assert payload["ok"] is True
    assert payload["tool"] == "edit_blocks"


def test_parse_json_stdout_extracts_json_after_log_prefix():
    text = (
        "Ruff check failed: F401 unused import\n"
        '{"ok": false, "tool": "edit_blocks", "error": "Quality gates failed"}'
    )
    payload, error = parse_json_stdout(text)

    assert error is None
    assert isinstance(payload, dict)
    assert payload["tool"] == "edit_blocks"
    assert payload["ok"] is False


def test_parse_json_stdout_prefers_contract_payload_when_multiple_json_objects():
    text = (
        '{"message":"debug"}\n'
        '{"ok": true, "tool": "repo_read_head", "exit_code": 0}'
    )
    payload, error = parse_json_stdout(text)

    assert error is None
    assert isinstance(payload, dict)
    assert payload["tool"] == "repo_read_head"
    assert payload["ok"] is True


def test_parse_json_stdout_returns_error_for_unparseable_output():
    payload, error = parse_json_stdout("not a json payload")

    assert payload is None
    assert isinstance(error, str) and error
