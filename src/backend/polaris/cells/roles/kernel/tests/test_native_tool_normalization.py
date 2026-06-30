"""Native tool-call normalization for weak-model tool dialects."""

from __future__ import annotations

import json
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.turn_decision_decoder import (
    DecodeConfig,
    TurnDecisionDecoder,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    RawLLMResponse,
    TurnDecisionKind,
    TurnId,
)


def _native_tool(name: str, arguments: dict[str, Any], *, call_id: str = "call_1") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


def _decode_single_tool(native_tool: dict[str, Any]) -> dict[str, Any]:
    decoder = TurnDecisionDecoder(config=DecodeConfig(domain="code"))
    response = RawLLMResponse(
        content="",
        thinking=None,
        native_tool_calls=[native_tool],
        model="qwen3.6",
        usage={},
    )

    decision = decoder.decode(response, TurnId("turn_native_tool_normalization"))

    assert decision["kind"] == TurnDecisionKind.TOOL_BATCH
    assert decision["tool_batch"] is not None
    return decision["tool_batch"]["invocations"][0]


@pytest.mark.parametrize("tool_name", ["create_file", "new_file", "create"])
def test_write_file_native_tool_aliases_decode_to_write_file(tool_name: str) -> None:
    invocation = _decode_single_tool(
        _native_tool(
            tool_name,
            {"path": "src/app.py", "text": "print('ok')\n"},
            call_id="call_create",
        )
    )

    assert invocation["tool_name"] == "write_file"
    assert invocation["arguments"] == {"file": "src/app.py", "content": "print('ok')\n"}


@pytest.mark.parametrize("tool_name", ["put_file", "write", "write_to_file", "file_write"])
def test_additional_write_file_native_tool_aliases_decode_to_write_file(tool_name: str) -> None:
    invocation = _decode_single_tool(
        _native_tool(
            tool_name,
            {"target": "src/app.py", "source_code": "print('ok')\n"},
            call_id=f"call_{tool_name}",
        )
    )

    assert invocation["tool_name"] == "write_file"
    assert invocation["arguments"] == {"file": "src/app.py", "content": "print('ok')\n"}


@pytest.mark.parametrize("tool_name", ["save_file", "overwrite_file", "upsert_file"])
def test_write_file_native_tool_storage_aliases_decode_to_write_file(tool_name: str) -> None:
    invocation = _decode_single_tool(
        _native_tool(
            tool_name,
            {"filename": "src/app.py", "body": "print('ok')\n"},
            call_id="call_save",
        )
    )

    assert invocation["tool_name"] == "write_file"
    assert invocation["arguments"] == {"file": "src/app.py", "content": "print('ok')\n"}


@pytest.mark.parametrize("tool_name", ["modify_file", "update_file", "patch_file"])
def test_edit_file_native_tool_aliases_decode_to_edit_file(tool_name: str) -> None:
    invocation = _decode_single_tool(
        _native_tool(
            tool_name,
            {
                "target_file": "src/app.py",
                "start_line": 3,
                "end_line": 5,
                "content": "print('changed')\n",
            },
            call_id="call_edit",
        )
    )

    assert invocation["tool_name"] == "edit_file"
    assert invocation["arguments"]["file"] == "src/app.py"
    assert invocation["arguments"]["start_line"] == 3
    assert invocation["arguments"]["end_line"] == 5
    assert invocation["arguments"]["content"] == "print('changed')\n"


def test_google_function_call_parameters_decode_to_arguments() -> None:
    invocation = _decode_single_tool(
        {
            "id": "call_google",
            "functionCall": {
                "name": "write_file",
                "parameters": {"filename": "src/app.py", "body": "print('ok')\n"},
            },
        }
    )

    assert invocation["tool_name"] == "write_file"
    assert invocation["arguments"] == {"file": "src/app.py", "content": "print('ok')\n"}


def test_openai_function_parameters_decode_to_arguments() -> None:
    invocation = _decode_single_tool(
        {
            "id": "call_parameters",
            "type": "function",
            "function": {
                "name": "write_file",
                "parameters": {"target_path": "src/app.py", "text": "print('ok')\n"},
            },
        }
    )

    assert invocation["tool_name"] == "write_file"
    assert invocation["arguments"] == {"file": "src/app.py", "content": "print('ok')\n"}


def test_openai_arguments_envelope_decode_to_arguments() -> None:
    invocation = _decode_single_tool(
        _native_tool(
            "write_file",
            {
                "name": "create_file",
                "input": {"target_path": "src/app.py", "text": "print('ok')\n"},
            },
            call_id="call_nested_envelope",
        )
    )

    assert invocation["tool_name"] == "write_file"
    assert invocation["arguments"] == {"file": "src/app.py", "content": "print('ok')\n"}


@pytest.mark.parametrize(
    ("name_key", "arguments_key"),
    [
        ("toolName", "toolInput"),
        ("tool_name", "tool_arguments"),
        ("function_name", "function_arguments"),
    ],
)
def test_flat_provider_tool_envelopes_decode_to_canonical_tool(name_key: str, arguments_key: str) -> None:
    invocation = _decode_single_tool(
        {
            "id": "call_flat",
            name_key: "save_file",
            arguments_key: {"target_file": "src/app.py", "source": "print('ok')\n"},
        }
    )

    assert invocation["tool_name"] == "write_file"
    assert invocation["arguments"] == {"file": "src/app.py", "content": "print('ok')\n"}


def test_flat_provider_camel_case_arguments_decode_to_canonical_tool() -> None:
    invocation = _decode_single_tool(
        {
            "id": "call_flat_camel",
            "toolName": "write",
            "toolInput": {"targetPath": "src/app.py", "sourceCode": "print('ok')\n"},
        }
    )

    assert invocation["tool_name"] == "write_file"
    assert invocation["arguments"] == {"file": "src/app.py", "content": "print('ok')\n"}


def test_edit_blocks_line_range_aliases_decode_to_canonical_arguments() -> None:
    invocation = _decode_single_tool(
        _native_tool(
            "edit_blocks",
            {
                "path": "src/app.py",
                "start_line": 3,
                "end_line": 5,
                "new_content": "def main():\n    return 0\n",
            },
            call_id="call_edit",
        )
    )

    assert invocation["tool_name"] == "edit_blocks"
    assert invocation["arguments"] == {
        "file": "src/app.py",
        "start": 3,
        "end": 5,
        "replace": "def main():\n    return 0\n",
    }


@pytest.mark.parametrize("file_key", ["target_file", "filename", "target_path"])
def test_common_target_file_aliases_decode_to_file(file_key: str) -> None:
    invocation = _decode_single_tool(
        _native_tool(
            "edit_blocks",
            {
                file_key: "src/app.py",
                "start_line": 3,
                "end_line": 5,
                "new_content": "def main():\n    return 0\n",
            },
            call_id=f"call_edit_{file_key}",
        )
    )

    assert invocation["tool_name"] == "edit_blocks"
    assert invocation["arguments"]["file"] == "src/app.py"
    assert invocation["arguments"]["start"] == 3
    assert invocation["arguments"]["end"] == 5
    assert invocation["arguments"]["replace"] == "def main():\n    return 0\n"


def test_qwen_textual_function_call_is_not_executable() -> None:
    decoder = TurnDecisionDecoder(config=DecodeConfig(domain="code"))
    response = RawLLMResponse(
        content=(
            "I will create the file now.\n"
            "<function=write_file>\n"
            "<parameter=path>src/app.py</parameter>\n"
            "<parameter=text>print('ok')\n</parameter>\n"
            "</function>"
        ),
        thinking=None,
        native_tool_calls=[],
        model="qwen3.6",
        usage={},
    )

    decision = decoder.decode(response, TurnId("turn_qwen_textual_tool"))

    assert decision["kind"] == TurnDecisionKind.FINAL_ANSWER
    assert decision["tool_batch"] is None


def test_textual_function_call_does_not_recover_when_native_call_fails_decode() -> None:
    decoder = TurnDecisionDecoder(config=DecodeConfig(domain="code"))
    response = RawLLMResponse(
        content=(
            "I will create the file now.\n"
            "<function=write_file>\n"
            "<parameter=path>src/app.py</parameter>\n"
            "<parameter=text>print('ok')\n</parameter>\n"
            "</function>"
        ),
        thinking=None,
        native_tool_calls=[
            {
                "id": "call_bad_native",
                "type": "function",
                "function": {"name": "write_file", "arguments": ["not", "a", "mapping"]},
            }
        ],
        model="qwen3.6",
        usage={},
    )

    decision = decoder.decode(response, TurnId("turn_native_fail_textual_recovery"))

    assert decision["kind"] == TurnDecisionKind.FINAL_ANSWER
    assert decision["tool_batch"] is None
    assert decision["metadata"]["decode_failures"][0]["tool"] == "write_file"


def test_textual_tool_recovery_ignores_thinking() -> None:
    decoder = TurnDecisionDecoder(config=DecodeConfig(domain="code"))
    response = RawLLMResponse(
        content="I need to inspect the repository first.",
        thinking=(
            "<function=write_file>"
            "<parameter=path>src/app.py</parameter>"
            "<parameter=text>print('unsafe')</parameter>"
            "</function>"
        ),
        native_tool_calls=[],
        model="qwen3.6",
        usage={},
    )

    decision = decoder.decode(response, TurnId("turn_thinking_textual_tool"))

    assert decision["kind"] == TurnDecisionKind.FINAL_ANSWER
    assert decision["tool_batch"] is None
