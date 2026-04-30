import sys
from pathlib import Path

import pytest

# Skip this test - tool_output_json module has been migrated to polaris.kernelone.tool_execution.output_json
try:
    from tool_output_json import parse_json_stdout
except ImportError:
    pytest.importorskip("polaris.kernelone.tool_execution.output_json")

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = REPO_ROOT / "src" / "backend" / "core" / "polaris_loop"
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))


def test_parse_json_stdout_accepts_clean_json():
    payload, error = parse_json_stdout('{"ok": true, "tool": "precision_edit"}')

    assert error is None
    assert isinstance(payload, dict)
    assert payload["ok"] is True
    assert payload["tool"] == "precision_edit"


def test_parse_json_stdout_extracts_json_after_log_prefix():
    text = (
        "Ruff check failed: F401 unused import\n"
        '{"ok": false, "tool": "precision_edit", "error": "Quality gates failed"}'
    )
    payload, error = parse_json_stdout(text)

    assert error is None
    assert isinstance(payload, dict)
    assert payload["tool"] == "precision_edit"
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
