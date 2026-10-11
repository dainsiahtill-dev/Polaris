"""Literal JSON data must survive protocol cleanup, including incomplete prefixes.

Synthetic complete examples are integration controls, never recovered plans.
The optional frozen prefix is forensic input only; its missing suffix stays unknown.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from polaris.cells.roles.kernel.internal.output_parser import OutputParser
from polaris.cells.roles.kernel.internal.tool_call_protocol import CanonicalToolCallParser
from polaris.cells.roles.kernel.internal.turn_engine.artifacts import AssistantRawContent
from polaris.cells.roles.kernel.internal.turn_engine.utils import sanitize_assistant_transcript_message


def test_actual_captured_prefix_preserved_without_phantom_html_call() -> None:
    root = os.environ.get("POL91_FROZEN_INPUTS")
    if not root:
        pytest.skip("requires immutable POL91 forensic inputs")
    document = json.loads(
        (Path(root) / "20261009T203000535731Z-journal.enriched.jsonl.json").read_text(encoding="utf-8")
    )
    prefix = document["data"][3]["raw"]["data"]["metadata"]["response_content"][:8000]
    assert hashlib.sha256(prefix.encode("utf-8")).hexdigest() == (
        "99f05185d4249ea862d957a03f057b3b13fb14e4b1fbf129115dd60696206b21"
    )
    result = OutputParser().parse_thinking(prefix)
    assert result.clean_content == prefix
    assert result.thinking is None
    calls, remainder = CanonicalToolCallParser.extract_text_calls_and_remainder(prefix)
    assert calls == []
    assert remainder == prefix
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.clean_content)


@pytest.mark.parametrize("tag", ["html", "thinking", "output", "answer", "tool_result", "write_file"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_tags_in_different_json_fields_remain_literal(tag: str, wrapped: bool) -> None:
    payload = json.dumps(
        {"tasks": [{"id": "first", "value": f'<{tag}>引号 " \\'}, {"id": "second", "value": f"</{tag}>"}]},
        ensure_ascii=False,
    )
    source = f"<thinking>real reasoning</thinking><output>{payload}</output>" if wrapped else payload
    result = OutputParser().parse_thinking(source)
    assert result.clean_content == payload
    assert result.thinking == ("real reasoning" if wrapped else None)
    assert sanitize_assistant_transcript_message(payload) == payload
    assert CanonicalToolCallParser.parse_text_calls(payload) == []


def test_incomplete_json_string_is_preserved_and_stays_invalid() -> None:
    payload = '{"tasks":[{"value":"<thinking>unfinished <write_file>{} </write_file>'
    assert OutputParser().parse_thinking(payload).clean_content == payload
    assert CanonicalToolCallParser.parse_text_calls(payload) == []
    with pytest.raises(json.JSONDecodeError):
        json.loads(payload)


def test_canonical_wrapper_text_inside_json_value_is_not_a_tool_call() -> None:
    literal = '[tool_call]{"tool":"write_file","args":{"file":"x","content":"中文 \\\\""}}[/tool_call]'
    payload = json.dumps({"literal": literal, "newlines": "line\n\n\n\nend"}, ensure_ascii=False)
    assert OutputParser().parse_thinking(payload).clean_content == payload
    assert CanonicalToolCallParser.parse_text_calls(payload, allowed_tool_names=["write_file"]) == []


@pytest.mark.parametrize("envelope", ["xml", "xml_args", "angle", "bracket"])
def test_real_tool_envelope_keeps_json_args_and_literal_closing_tags(envelope: str) -> None:
    args = {"file": "page.html", "content": '<html>中文 " \\ </write_file></args></tool_call></html>'}
    serialized = json.dumps(args, ensure_ascii=False)
    canonical = json.dumps({"tool": "write_file", "args": args}, ensure_ascii=False)
    source = {
        "xml": f"<write_file>{serialized}</write_file>",
        "xml_args": f"<write_file><args>{serialized}</args></write_file>",
        "angle": f"<tool_call>{canonical}</tool_call>",
        "bracket": f"[tool_call]{canonical}[/tool_call]",
    }[envelope]
    calls, remainder = CanonicalToolCallParser.extract_text_calls_and_remainder(
        source, allowed_tool_names=["write_file"]
    )
    assert len(calls) == 1
    assert calls[0].tool == "write_file"
    assert calls[0].args == args
    assert remainder == ""
    disallowed, untouched = CanonicalToolCallParser.extract_text_calls_and_remainder(
        source, allowed_tool_names=["read_file"]
    )
    assert disallowed == []
    assert untouched == source


def test_native_tool_args_preserved_and_text_never_becomes_executable() -> None:
    args = {"file": "page.html", "content": "<html><thinking>literal</thinking></html>"}
    parser = OutputParser()
    calls = parser.parse_execution_tool_calls(
        AssistantRawContent("<write_file>{}</write_file>"),
        allowed_tool_names=["write_file"],
        native_tool_calls=[
            {
                "id": "native-1",
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": json.dumps(args),
                },
            }
        ],
    )
    assert len(calls) == 1
    assert calls[0].args == args
    assert parser.parse_execution_tool_calls(AssistantRawContent("<write_file>{}</write_file>")) == []


def test_actual_result_wrapper_with_literal_closing_tag_leaves_only_answer() -> None:
    source = '<tool_result id="r">{"value":"literal </tool_result>"}</tool_result><output>answer</output>'
    assert OutputParser().parse_thinking(source).clean_content == "answer"


def test_real_thinking_inside_output_wrapper_is_still_extracted() -> None:
    result = OutputParser().parse_thinking("<output><thinking>reason</thinking>answer</output>")
    assert result.clean_content == "answer"
    assert result.thinking == "reason"


@pytest.mark.parametrize("partial", [False, True])
def test_transaction_completion_and_pm_consumer_preserve_synthetic_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, partial: bool
) -> None:
    from types import SimpleNamespace

    from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter
    from polaris.cells.roles.kernel.internal.kernel import transaction_turn_completion as completion
    from polaris.cells.roles.kernel.tests.conftest import _build_kernel
    from polaris.cells.roles.profile.public.service import RoleExecutionMode, RoleTurnRequest

    # Synthetic complete JSON, not a recovered historical plan or delivery stub.
    tasks = [
        {
            "id": "SYNTHETIC-1",
            "title": "Fixture first",
            "goal": "Literal <html>",
            "steps": ["Keep literal <thinking>value</thinking>"],
            "target_files": ["first.txt"],
            "acceptance": ["Preserve source data"],
            "depends_on": [],
        },
        {
            "id": "SYNTHETIC-2",
            "title": "Fixture second",
            "goal": "Literal </html>",
            "steps": ["Preserve Unicode 中文"],
            "target_files": ["second.txt"],
            "acceptance": ["Keep second identity"],
            "depends_on": ["SYNTHETIC-1"],
        },
    ]
    payload = json.dumps({"tasks": tasks}, ensure_ascii=False)
    if partial:
        payload = payload[:-5]
    kernel = _build_kernel()
    kernel.workspace = str(tmp_path)
    profile = kernel.registry.get_profile_or_raise("pm")
    request = RoleTurnRequest(mode=RoleExecutionMode.CHAT, workspace=str(tmp_path), message="synthetic fixture")
    # Substitute only external persistence ports; parser, result projection,
    # history construction, PM JSON admission and normalization remain real.
    monkeypatch.setattr(completion, "_commit_turn_to_snapshot", lambda **kwargs: None)
    monkeypatch.setattr(completion, "append_role_turn_task_boundary_verdict", lambda **kwargs: None)
    result = completion.build_transaction_turn_completion_result(
        kernel=kernel,
        role="pm",
        profile=profile,
        request=request,
        fingerprint=SimpleNamespace(full_hash="synthetic-fingerprint", core_hash="synthetic-core"),
        turn_id="synthetic-turn",
        tk_result={"kind": "final_answer", "visible_content": payload},
        response_schema=None,
        runtime_tool_policy_audit={},
        tool_filter_audit=None,
        context_gateway=SimpleNamespace(record_projection_outcome=lambda **kwargs: {}),
        context_result=SimpleNamespace(token_estimate=0),
    )
    assert result.content == payload
    diagnostics: list[dict] = []
    contracts = PMAdapter(workspace=str(tmp_path))._extract_task_contracts(
        result.content, directive="synthetic preservation fixture", diagnostics=diagnostics
    )
    if partial:
        assert contracts == []
        assert diagnostics
    else:
        assert [row["id"] for row in contracts] == ["SYNTHETIC-1", "SYNTHETIC-2"]
        assert [row["goal"] for row in contracts] == ["Literal <html>", "Literal </html>"]
        assert contracts[1]["depends_on"] == ["SYNTHETIC-1"]


def test_frozen_complete_task_objects_are_independent_forensic_fixtures(tmp_path: Path) -> None:
    from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter

    root = os.environ.get("POL91_FROZEN_INPUTS")
    if not root:
        pytest.skip("requires immutable POL91 forensic inputs")
    document = json.loads(
        (Path(root) / "20261009T203000535731Z-journal.enriched.jsonl.json").read_text(encoding="utf-8")
    )
    prefix = document["data"][3]["raw"]["data"]["metadata"]["response_content"][:8000]
    for start, end, digest in [
        (10, 3154, "942bb3dd52755a48ae40cb0c97df08537a7d47ab96e3af4f6771b0368fc052b5"),
        (3155, 7303, "dad3c0a2bea53b4e733434efe8405dbb744cb4feba5eb2ff93861ba5890bdd61"),
    ]:
        fragment = prefix[start:end]
        assert hashlib.sha256(fragment.encode("utf-8")).hexdigest() == digest
        task = json.loads(fragment)
        # Each object is tested independently, never stitched into a plan.
        forensic_envelope = json.dumps({"tasks": [task]}, ensure_ascii=False)
        clean = OutputParser().parse_thinking(forensic_envelope).clean_content
        assert clean == forensic_envelope
        rows = PMAdapter(workspace=str(tmp_path))._extract_task_contracts(clean, directive="forensic fixture only")
        assert len(rows) == 1
        assert rows[0]["id"] == task["id"]
        assert rows[0]["depends_on"] == task["depends_on"]


@pytest.mark.parametrize("stage", ["canonical_remainder", "transcript_sanitizer", "visible_parser"])
@pytest.mark.parametrize(
    "payload",
    [
        '{"tasks":[{"value":"中文  ',
        '{"tasks":[{"value":"中文\\n\\t  ',
        '{"tasks":[{"value":"escaped \\"quote  ',
        '{"tasks":[{"value":"backslash \\\\  ',
        '{"tasks":[{"value":"escaped backslash quote \\\\\\"  ',
        '{"tasks":[{"value":"中文　',
        '{"tasks":[{"value":"\\u4e2d\\u6587  ',
        '{"tasks":[{"value":"  ',
        '{"tasks":[{"value":"literal <html> data \n\t',
    ],
    ids=[
        "root-valid-prefix",
        "newline-escapes",
        "escaped-quote",
        "backslash",
        "backslash-quote",
        "unicode-whitespace",
        "unicode-escapes",
        "whitespace-only-value",
        "raw-forensic",
    ],
)
def test_open_json_string_transport_whitespace_preserved(stage: str, payload: str, tmp_path: Path) -> None:
    from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter

    if stage == "canonical_remainder":
        calls, actual = CanonicalToolCallParser.extract_text_calls_and_remainder(payload)
        assert calls == []
    elif stage == "transcript_sanitizer":
        actual = sanitize_assistant_transcript_message(payload)
    else:
        result = OutputParser().parse_thinking(payload)
        assert result.thinking is None
        actual = result.clean_content
    assert actual.encode("utf-8") == payload.encode("utf-8")
    with pytest.raises(json.JSONDecodeError):
        json.loads(actual)
    assert PMAdapter(workspace=str(tmp_path))._extract_task_contracts(actual, directive="partial fixture") == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (" \n\tanswer \n\t", "answer"),
        (" \n\t", ""),
        (' \n {"tasks":[{"value":"中文  "}]} \n\t', '{"tasks":[{"value":"中文  "}]}'),
        (' \n {"tasks":[{"value":"中文  ', '{"tasks":[{"value":"中文  '),
        (' \n {"tasks":[{"value": \n\t', '{"tasks":[{"value":'),
        (' \n {"tasks":[{"value":"中文" \n\t', '{"tasks":[{"value":"中文"'),
    ],
)
def test_transport_whitespace_outside_json_data_still_trimmed(source: str, expected: str) -> None:
    calls, remainder = CanonicalToolCallParser.extract_text_calls_and_remainder(source)
    assert calls == []
    assert remainder == expected
    assert sanitize_assistant_transcript_message(source) == expected
    assert OutputParser().parse_thinking(source).clean_content == expected


def test_real_tool_removed_before_open_string_remainder_preserves_spaces() -> None:
    payload = '{"tasks":[{"value":"中文  '
    source = ' \n<tool_call>{"tool":"read_file","args":{"file":"a.py"}}</tool_call> \n' + payload
    calls, remainder = CanonicalToolCallParser.extract_text_calls_and_remainder(source)
    assert len(calls) == 1
    assert calls[0].tool == "read_file"
    assert calls[0].args == {"file": "a.py", "max_bytes": 200000, "range_required": False}
    assert remainder == payload
    assert sanitize_assistant_transcript_message(source) == payload
    assert OutputParser().parse_thinking(source).clean_content == payload


@pytest.mark.parametrize("error_type", [ValueError, TypeError, AttributeError])
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (' \n {"tasks":[{"value":"中文  ', '{"tasks":[{"value":"中文  '),
        (' \n {"value":"中文  "} \n\t', '{"value":"中文  "}'),
        (" \n answer \n\t", "answer"),
    ],
)
def test_sanitizer_error_path_retains_data_aware_whitespace_trimming(
    monkeypatch: pytest.MonkeyPatch, error_type: type[Exception], source: str, expected: str
) -> None:
    # Exercise the established exception path only; normal production parser
    # behavior is covered without substitution in every test above.
    def reject(*args: object, **kwargs: object) -> None:
        raise error_type("isolated parser failure")

    monkeypatch.setattr(CanonicalToolCallParser, "extract_text_calls_and_remainder", reject)
    assert sanitize_assistant_transcript_message(source) == expected
