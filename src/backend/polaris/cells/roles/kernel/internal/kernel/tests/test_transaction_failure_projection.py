from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from polaris.cells.roles.kernel.internal.kernel import transaction_failure_projection as failure
from polaris.cells.roles.kernel.internal.transaction.tool_surface import TransactionToolSurfacePlan
from polaris.cells.roles.profile.public.service import RoleProfile
from polaris.kernelone.events.uep_publisher import UEPEventPublisher


@dataclass
class _Profile:
    role_id: str = "director"
    model: str = "test-model"
    provider_id: str = "test-provider"


def _conflict_surface() -> TransactionToolSurfacePlan:
    return TransactionToolSurfacePlan(
        tool_definitions=[],
        runtime_tool_policy_audit={"tool_policy_mode": "native"},
        tool_filter_audit={"missing": ["write_file"]},
        conflict_error="required tool removed",
    )


def test_tool_filter_conflict_result_records_projection() -> None:
    context_gateway = MagicMock(record_projection_outcome=MagicMock(return_value={}))
    result = failure.build_tool_filter_conflict_result(
        profile=cast(RoleProfile, _Profile()),
        fingerprint=object(),
        tool_surface=_conflict_surface(),
        context_gateway=context_gateway,
        context_result=type("Context", (), {"token_estimate": 13})(),
    )

    assert result.error == "required tool removed"
    assert result.execution_stats["tool_filter_blocked"] is True
    assert result.execution_stats["tool_policy_mode"] == "native"
    assert result.metadata["tool_filter_audit"] == {"missing": ["write_file"]}
    assert result.metadata["provider_id"] == "test-provider"
    context_gateway.record_projection_outcome.assert_called_once_with(success=False, tokens_used=13)


@pytest.mark.asyncio
async def test_stream_tool_filter_conflict_event_is_published() -> None:
    context_gateway = MagicMock(record_projection_outcome=MagicMock(return_value={}))
    publisher = type(
        "Publisher",
        (),
        {"publish_stream_event": AsyncMock()},
    )()

    event = await failure.publish_tool_filter_conflict_stream_event(
        workspace=".",
        role="director",
        stream_run_id="run-1",
        turn_id="turn-1",
        tool_surface=_conflict_surface(),
        context_gateway=context_gateway,
        context_result=type("Context", (), {"token_estimate": 17})(),
        uep_publisher=cast(UEPEventPublisher, publisher),
    )

    assert event["type"] == "error"
    assert event["error_type"] == "tool_schema_filter_conflict"
    assert event["metadata"] == {"tool_filter_audit": {"missing": ["write_file"]}}
    publisher.publish_stream_event.assert_awaited_once_with(
        workspace=".",
        run_id="run-1",
        role="director",
        event_type="error",
        payload=event,
    )
    context_gateway.record_projection_outcome.assert_called_once_with(success=False, tokens_used=17)
