from __future__ import annotations

from polaris.cells.roles.kernel.internal.output_parser import OutputParser


def _to_payload(calls):
    return [(call.tool, dict(call.args)) for call in calls]


def test_parse_tool_calls_supports_patch_file_search_replace() -> None:
    parser = OutputParser()
    content = (
        "PATCH_FILE: src/role_agent_service.py\n"
        "<<<<<<< SEARCH\n"
        "def old():\n"
        "    pass\n"
        "=======\n"
        "def old():\n"
        "    return 1\n"
        ">>>>>>> REPLACE\n"
        "END PATCH_FILE\n"
    )

    calls = parser.parse_tool_calls(
        content,
        allowed_tool_names=["edit_file", "write_file"],
    )
    payload = _to_payload(calls)
    assert ("edit_file", {
        "file": "src/role_agent_service.py",
        "search": "def old():\n    pass",
        "replace": "def old():\n    return 1",
    }) in payload


def test_parse_tool_calls_converts_empty_search_to_write_file() -> None:
    parser = OutputParser()
    content = (
        "PATCH_FILE: src/new_module.py\n"
        "<<<<<<< SEARCH\n"
        "<empty>\n"
        "=======\n"
        "print('hello')\n"
        ">>>>>>> REPLACE\n"
        "END PATCH_FILE\n"
    )

    calls = parser.parse_tool_calls(
        content,
        allowed_tool_names=["write_file"],
    )
    payload = _to_payload(calls)
    assert payload == [("write_file", {"file": "src/new_module.py", "content": "print('hello')"})]


def test_parse_tool_calls_rejects_unsafe_patch_path() -> None:
    parser = OutputParser()
    content = (
        "PATCH_FILE: ../../../etc/passwd\n"
        "<<<<<<< SEARCH\n"
        "<empty>\n"
        "=======\n"
        "malicious\n"
        ">>>>>>> REPLACE\n"
        "END PATCH_FILE\n"
    )

    calls = parser.parse_tool_calls(
        content,
        allowed_tool_names=["edit_file", "write_file"],
    )
    assert calls == []


def test_parse_tool_calls_filters_by_allowed_tool_names() -> None:
    parser = OutputParser()
    content = (
        "PATCH_FILE: src/app.py\n"
        "<<<<<<< SEARCH\n"
        "<empty>\n"
        "=======\n"
        "print('x')\n"
        ">>>>>>> REPLACE\n"
        "END PATCH_FILE\n"
    )

    calls = parser.parse_tool_calls(content, allowed_tool_names=["edit_file"])
    assert calls == []


def test_parse_tool_calls_supports_native_openai_tool_calls() -> None:
    parser = OutputParser()
    native_calls = [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "glob",
                "arguments": "{\"pattern\":\"**/*.py\",\"path\":\".\"}",
            },
        }
    ]
    calls = parser.parse_tool_calls(
        "",
        allowed_tool_names=["glob"],
        native_tool_calls=native_calls,
        native_provider="openai",
    )
    payload = _to_payload(calls)
    assert payload == [("glob", {"pattern": "**/*.py", "path": "."})]


def test_parse_tool_calls_supports_native_anthropic_tool_calls() -> None:
    parser = OutputParser()
    native_calls = [
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "file_exists",
            "input": {"path": "tui_runtime.md"},
        }
    ]
    calls = parser.parse_tool_calls(
        "",
        allowed_tool_names=["file_exists"],
        native_tool_calls=native_calls,
        native_provider="anthropic",
    )
    payload = _to_payload(calls)
    assert payload == [("file_exists", {"path": "tui_runtime.md"})]


def test_parse_tool_calls_does_not_misparse_read_file_tags_as_file_protocol() -> None:
    parser = OutputParser()
    content = (
        "[read_file]\n"
        "file: src/expense_tracker/runtime_config.py\n"
        "[/read_file]\n"
        "[read_file]\n"
        "file: tests/test_expense_tracker.py\n"
        "[/read_file]\n"
    )

    calls = parser.parse_tool_calls(
        content,
        allowed_tool_names=["read_file", "write_file", "edit_file"],
    )
    payload = _to_payload(calls)
    assert payload == [
        ("read_file", {"file": "src/expense_tracker/runtime_config.py"}),
        ("read_file", {"file": "tests/test_expense_tracker.py"}),
    ]


def test_parse_tool_calls_still_supports_file_protocol_with_end_file_markers() -> None:
    parser = OutputParser()
    content = (
        "FILE: src/expense_tracker/runtime_config.py\n"
        "APP_NAME = \"expense-tracker\"\n"
        "END FILE\n"
    )

    calls = parser.parse_tool_calls(
        content,
        allowed_tool_names=["write_file"],
    )
    payload = _to_payload(calls)
    assert payload == [
        (
            "write_file",
            {
                "file": "src/expense_tracker/runtime_config.py",
                "content": "APP_NAME = \"expense-tracker\"",
            },
        )
    ]


def test_extract_json_supports_single_quote_fence_with_json_hint() -> None:
    parser = OutputParser()
    content = """
''' json
{
  "ok": true,
  "count": 2
}
'''
"""
    payload = parser.extract_json(content)
    assert payload == {"ok": True, "count": 2}


def test_extract_search_replace_delegates_to_unified_parser() -> None:
    parser = OutputParser()
    content = (
        "PATCH_FILE: src/role_agent_service.py\n"
        "<<<<<<< SEARCH\n"
        "def old():\n"
        "    pass\n"
        "=======\n"
        "def old():\n"
        "    return 1\n"
        ">>>>>>> REPLACE\n"
        "END PATCH_FILE\n"
    )

    payload = parser.extract_search_replace(content)
    assert payload == [
        {
            "file": "src/role_agent_service.py",
            "search": "def old():\n    pass",
            "replace": "def old():\n    return 1",
        }
    ]


def test_extract_search_replace_keeps_legacy_fallback() -> None:
    parser = OutputParser()
    content = (
        "<<<<<<< SEARCH\n"
        "x = 1\n"
        "=======\n"
        "x = 2\n"
        ">>>>>>> REPLACE\n"
    )

    payload = parser.extract_search_replace(content)
    assert payload == [{"search": "x = 1", "replace": "x = 2"}]
