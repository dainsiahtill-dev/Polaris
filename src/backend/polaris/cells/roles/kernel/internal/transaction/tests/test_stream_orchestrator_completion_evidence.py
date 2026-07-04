from __future__ import annotations

from polaris.cells.control_plane.run_ledger.public import (
    project_completion_audit_evidence_to_metadata,
)


def test_project_completion_audit_evidence_overwrites_stale_stream_monitoring() -> None:
    monitoring = {"native_tool_calls_count": 7, "native_tool_call_names": ["stale_tool"]}
    decision_metadata = {
        "native_tool_calls_count": 1,
        "native_tool_call_names": ["write_file"],
    }

    project_completion_audit_evidence_to_metadata(
        monitoring,
        decision_metadata,
        overwrite_native_facts=True,
    )

    assert monitoring["native_tool_calls_count"] == 1
    assert monitoring["native_tool_call_names"] == ["write_file"]
