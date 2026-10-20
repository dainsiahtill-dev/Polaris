"""Private observation provenance cannot be asserted by prompt content."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from polaris.kernelone.context.contracts import ContextSource
from polaris.kernelone.context.prompt_safety import (
    bind_tool_observation_source,
    project_tool_result_data,
    validate_tool_observation_sources,
)


def bound_observation():
    native = {"cmd": "npx tsc -p tsconfig.json", "timeout": 60}
    arguments = {"command": "npx tsc -p tsconfig.json", "timeout": 60}
    result = {"command": {"timeout_seconds": 60}, "exit_code": 0, "stdout": "source job_token: is literal"}
    content = json.dumps({"status": "success", "result": result})
    source = bind_tool_observation_source(
        source_id="committed-turn:call",
        role="director",
        tool_name="execute_command",
        call_id="call",
        arguments=arguments,
        native_arguments=native,
        result=result,
        content=content,
        snapshot_hash="s" * 64,
    )
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call",
                    "type": "function",
                    "function": {
                        "name": "execute_command",
                        "arguments": json.dumps(arguments),
                    },
                }
            ],
        },
        {"role": "tool", "name": "execute_command", "tool_call_id": "call", "content": content},
    ]
    return messages, source


def test_only_original_owner_frame_matches_final_native_slot() -> None:
    messages, source = bound_observation()
    assert validate_tool_observation_sources(messages, [source], role="director") == ({1}, [])
    assert source.metadata["arguments_hash"] != source.metadata["native_arguments_hash"]
    assert validate_tool_observation_sources(messages, [replace(source)], role="director")[1]


def test_caller_context_source_or_trusted_flag_does_not_attest_tool_data() -> None:
    messages, original = bound_observation()
    caller = ContextSource(
        source_type=original.source_type,
        source_id=original.source_id,
        role="director",
        text=original.text,
        tokens=1,
        importance=1,
        metadata={"trusted": True, "prompt_safe": True},
    )
    assert validate_tool_observation_sources(messages, [caller], role="director") == (
        set(),
        ["tool_observation_source_unowned"],
    )


@pytest.mark.parametrize("field", ["native_arguments_hash", "native_arguments_json", "result_hash"])
def test_private_metadata_mutation_cannot_rebind_original_source(field) -> None:
    messages, source = bound_observation()
    source.metadata[field] = "tampered"
    assert validate_tool_observation_sources(messages, [source], role="director")[1]


@pytest.mark.parametrize(
    "authority_key", ["authorization", "Authorization", "capabilityToken", "job_token", "JobToken"]
)
def test_recursive_projection_excludes_authority_but_preserves_source_text(authority_key) -> None:
    source = 'const capability_token = "example"; // timeout_seconds: 60'
    result = {"nested": [{authority_key: {"secret": "private"}, "content": source}]}
    assert project_tool_result_data(result) == {"nested": [{"content": source}]}
    assert authority_key in result["nested"][0]
