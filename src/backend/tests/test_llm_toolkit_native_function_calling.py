import json

from polaris.kernelone.llm.toolkit.native_function_calling import ToolResult


def test_tool_result_serializes_non_json_output() -> None:
    class _NotSerializable:
        pass

    result = ToolResult(
        tool_call_id="call_1",
        name="demo_tool",
        output={"raw": _NotSerializable()},
    )

    payload = json.loads(result.to_openai_format()["content"])
    assert "raw" in payload
    assert isinstance(payload["raw"], str)


def test_tool_result_wraps_non_dict_payload() -> None:
    result = ToolResult(
        tool_call_id="call_2",
        name="demo_tool",
        output=["a", 1, True],
    )

    payload = json.loads(result.to_openai_format()["content"])
    assert payload["value"] == ["a", 1, True]
