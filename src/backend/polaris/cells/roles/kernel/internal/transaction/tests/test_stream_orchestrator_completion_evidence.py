from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.control_plane.run_ledger.public import project_native_tool_call_facts_to_metadata
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import (
    native_tool_call_facts_from_response,
)


def test_project_native_tool_call_facts_overwrites_stale_stream_monitoring() -> None:
    response = SimpleNamespace(
        content="",
        model="gpt-test",
        native_tool_calls=[{"function": {"name": "write_file"}}],
    )
    monitoring = {"native_tool_calls_count": 7, "native_tool_call_names": ["stale_tool"]}

    project_native_tool_call_facts_to_metadata(monitoring, native_tool_call_facts_from_response(response, {}))

    assert monitoring["native_tool_calls_count"] == 1
    assert monitoring["native_tool_call_names"] == ["write_file"]
