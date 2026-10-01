"""Recover one schema-proven write envelope; never infer competing targets."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest
from polaris.kernelone.llm.toolkit.parsers.native_function import NativeFunctionCallingParser
from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments_from_snapshot
from polaris.kernelone.tool_execution.contracts import frozen_node_to_value
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry, _freeze_tool_value


def test_native_assignment_label_uses_captured_write_alias() -> None:
    calls = NativeFunctionCallingParser.parse_openai(
        [
            {
                "id": "call-assignment",
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": '{"file=path":"tests/product.test.ts","content":"// keep file=path literally\\n"}',
                },
            }
        ]
    )
    assert calls[0].parse_error is None
    actual = normalize_tool_arguments_from_snapshot(
        ToolSpecRegistry.capture_effective_spec(calls[0].name), calls[0].arguments
    )
    assert actual == {"file": "tests/product.test.ts", "content": "// keep file=path literally\n"}


@pytest.mark.parametrize("other_key", ["file", "path", "file_path"])
def test_assignment_alias_does_not_choose_between_competing_targets(other_key: str) -> None:
    with pytest.raises(ValueError, match="conflicting_file_argument_alias"):
        normalize_tool_arguments_from_snapshot(
            ToolSpecRegistry.capture_effective_spec("write_file"),
            {"file=path": "a.py", other_key: "b.py", "content": "unchanged body"},
        )


def test_assignment_alias_keeps_identical_canonical_duplicate() -> None:
    actual = normalize_tool_arguments_from_snapshot(
        ToolSpecRegistry.capture_effective_spec("write_file"),
        {"file=path": "a.py", "file": "a.py", "content": "unchanged body"},
    )
    assert actual == {"file": "a.py", "content": "unchanged body"}


@pytest.mark.parametrize("wrapper", ["arguments", "args"])
def test_assignment_alias_conflict_rejected_after_supported_unwrapping(wrapper: str) -> None:
    with pytest.raises(ValueError, match="conflicting_file_argument_alias"):
        normalize_tool_arguments_from_snapshot(
            ToolSpecRegistry.capture_effective_spec("write_file"),
            {wrapper: {"file=path": "a.py", "file": "b.py", "content": "unchanged body"}},
        )


def test_assignment_alias_is_not_invented_for_old_snapshot() -> None:
    captured = ToolSpecRegistry.capture_effective_spec("write_file")
    spec = frozen_node_to_value(captured.canonical_effective_spec)
    assert isinstance(spec, dict)
    spec["arg_aliases"].pop("file=path", None)
    old = replace(captured, canonical_effective_spec=_freeze_tool_value(spec))
    arguments = {"file=path": "a.py", "content": "unchanged body"}
    assert normalize_tool_arguments_from_snapshot(old, arguments) == arguments


@pytest.mark.parametrize("tail", ["", "</parameter", "</parameter>"])
def test_single_path_key_write_preserves_body_and_is_idempotent(tail: str) -> None:
    snapshot = ToolSpecRegistry.capture_effective_spec("write_file")
    body = 'print("<parameter=literal>keep</parameter>")\n'
    arguments = {"file/src/example.py" + tail: body}
    before = deepcopy(arguments)

    actual = normalize_tool_arguments_from_snapshot(snapshot, arguments)

    assert actual == {"file": "src/example.py", "content": body}
    assert normalize_tool_arguments_from_snapshot(snapshot, actual) == actual
    assert arguments == before


def test_real_native_parser_then_captured_normalizer_decodes_envelope() -> None:
    calls = NativeFunctionCallingParser.parse_openai(
        [
            {
                "id": "call-write-1",
                "type": "function",
                "function": {"name": "write_file", "arguments": '{"file/src/example.py</parameter": "print(1)\\n"}'},
            }
        ]
    )
    assert len(calls) == 1 and calls[0].parse_error is None

    actual = normalize_tool_arguments_from_snapshot(
        ToolSpecRegistry.capture_effective_spec(calls[0].name), calls[0].arguments
    )

    assert actual == {"file": "src/example.py", "content": "print(1)\n"}


@pytest.mark.parametrize(
    "key",
    [
        "file/",
        "file//absolute.py",
        "file/../outside.py",
        "file/src/../outside.py",
        "file/src/./example.py",
        "file/C:/outside.py",
        "file/src\\example.py",
        "file/ example.py",
        "file/example.py ",
        "file/src/example.py\n",
        "file/src/example.py\x00",
        "file/src/example.py</parameter-extra",
        "file/src/example.py<parameter>",
        "file/src/example.py</parameter></parameter>",
        "path/src/example.py",
        "src/example.py",
    ],
)
def test_ambiguous_or_unsafe_key_is_not_reinterpreted(key: str) -> None:
    arguments = {key: "unchanged body"}

    actual = normalize_tool_arguments_from_snapshot(ToolSpecRegistry.capture_effective_spec("write_file"), arguments)

    assert actual == arguments
    assert "file" not in actual


@pytest.mark.parametrize("body", [None, 123, {"text": "body"}, ["body"]])
def test_non_string_body_is_not_invented(body: object) -> None:
    arguments = {"file/src/example.py": body}
    assert (
        normalize_tool_arguments_from_snapshot(ToolSpecRegistry.capture_effective_spec("write_file"), arguments)
        == arguments
    )


def test_multiple_encoded_targets_are_not_selected() -> None:
    arguments = {"file/a.py": "a", "file/b.py": "b"}
    assert (
        normalize_tool_arguments_from_snapshot(ToolSpecRegistry.capture_effective_spec("write_file"), arguments)
        == arguments
    )


def test_canonical_fields_are_never_overwritten_by_encoded_key() -> None:
    arguments = {"file": "a.py", "content": "a", "file/b.py": "b"}
    assert (
        normalize_tool_arguments_from_snapshot(ToolSpecRegistry.capture_effective_spec("write_file"), arguments)
        == arguments
    )


def test_other_file_tool_does_not_get_write_semantics() -> None:
    arguments = {"file/a.py": "a"}
    assert (
        normalize_tool_arguments_from_snapshot(ToolSpecRegistry.capture_effective_spec("edit_file"), arguments)
        == arguments
    )


@pytest.mark.parametrize(
    "change", ["wrong_content_type", "content_not_required", "missing_file_field", "extra_required_field"]
)
def test_nonstandard_captured_write_schema_is_not_reinterpreted(change: str) -> None:
    snapshot = ToolSpecRegistry.capture_effective_spec("write_file")
    spec = frozen_node_to_value(snapshot.canonical_effective_spec)
    assert isinstance(spec, dict)
    if change == "missing_file_field":
        spec["arguments"] = [item for item in spec["arguments"] if item["name"] != "file"]
    elif change == "extra_required_field":
        spec["arguments"].append({"name": "checksum", "type": "string", "required": True})
    else:
        for item in spec["arguments"]:
            if item["name"] == "content":
                item["type" if change == "wrong_content_type" else "required"] = (
                    "object" if change == "wrong_content_type" else False
                )
    overridden = replace(snapshot, canonical_effective_spec=_freeze_tool_value(spec))
    arguments = {"file/a.py": "a"}

    assert normalize_tool_arguments_from_snapshot(overridden, arguments) == arguments
