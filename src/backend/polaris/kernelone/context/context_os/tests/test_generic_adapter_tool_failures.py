"""Regression coverage for generic ContextOS handling of tool failures."""

from __future__ import annotations

from polaris.kernelone.context.context_os.domain_adapters.generic import GenericContextDomainAdapter
from polaris.kernelone.context.context_os.models_v2 import TranscriptEventV2 as TranscriptEvent


def test_tool_failure_summary_is_observation_not_deliverable() -> None:
    adapter = GenericContextDomainAdapter()
    event = TranscriptEvent(
        event_id="evt_tool_failure",
        sequence=1,
        role="assistant",
        kind="assistant_turn",
        content=(
            '[tool_failure_summary]\n{"tool":"write_file","error_type":"tool_failure",'
            '"reason":"write_file failed","receipt_detail":"omitted"}'
        ),
    )

    hints = adapter.extract_state_hints(event)

    assert hints.deliverables == ()
    assert hints.open_loops == ()
    assert hints.goals == ()


def test_non_deliverable_metadata_suppresses_state_hints() -> None:
    adapter = GenericContextDomainAdapter()
    event = TranscriptEvent(
        event_id="evt_tool_failure_digest",
        sequence=2,
        role="assistant",
        kind="assistant_turn",
        content='{"schema_version":"tool_failure_summary_digest.v1","receipt_detail":"omitted"}',
        metadata=(("non_deliverable", True), ("observation_only", True)),
    )

    hints = adapter.extract_state_hints(event)

    assert hints.deliverables == ()
    assert hints.open_loops == ()
    assert hints.goals == ()
