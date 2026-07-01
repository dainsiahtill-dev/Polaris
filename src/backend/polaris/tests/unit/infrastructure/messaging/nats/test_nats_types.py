"""Runtime event envelope wire-contract tests."""

from __future__ import annotations

from typing import Any

import pytest
from polaris.infrastructure.messaging.nats.nats_types import (
    RuntimeEventEnvelope,
    create_runtime_event,
)


def _runtime_event_payload(**overrides: Any) -> dict[str, Any]:
    payload = create_runtime_event(
        workspace_key="workspace-key",
        run_id="run-1",
        channel="llm",
        kind="task.updated",
        payload={"task_id": "task-1"},
        meta={"source": "test"},
        trace_id="trace-1",
    ).to_dict()
    payload.update(overrides)
    return payload


def test_runtime_event_from_dict_round_trips_canonical_event() -> None:
    envelope = RuntimeEventEnvelope.from_dict(_runtime_event_payload(cursor="12"))

    assert envelope.schema_version == "runtime.v2"
    assert envelope.run_id == "run-1"
    assert envelope.channel == "llm"
    assert envelope.kind == "task.updated"
    assert envelope.cursor == 12
    assert envelope.payload == {"task_id": "task-1"}
    assert envelope.meta == {"source": "test"}


@pytest.mark.parametrize(
    "field_name",
    [
        "schema_version",
        "event_id",
        "workspace_key",
        "run_id",
        "channel",
        "kind",
        "ts",
    ],
)
def test_runtime_event_from_dict_rejects_missing_required_wire_fields(field_name: str) -> None:
    payload = _runtime_event_payload()
    payload.pop(field_name)

    with pytest.raises(ValueError, match=field_name):
        RuntimeEventEnvelope.from_dict(payload)


@pytest.mark.parametrize(
    ("override", "expected_message"),
    [
        ({"schema_version": "runtime.v1"}, "schema_version"),
        ({"ts": "not-a-timestamp"}, "ISO 8601"),
        ({"cursor": "not-an-int"}, "cursor"),
        ({"cursor": -1}, "non-negative"),
        ({"payload": []}, "payload"),
        ({"meta": []}, "meta"),
    ],
)
def test_runtime_event_from_dict_rejects_invalid_wire_fields(
    override: dict[str, Any],
    expected_message: str,
) -> None:
    with pytest.raises(ValueError, match=expected_message):
        RuntimeEventEnvelope.from_dict(_runtime_event_payload(**override))
