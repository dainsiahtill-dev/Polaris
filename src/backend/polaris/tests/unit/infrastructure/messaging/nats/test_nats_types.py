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


def test_runtime_event_from_dict_accepts_empty_run_id_for_non_run_events() -> None:
    """File-edit / non-run-scoped events legitimately carry no run_id.

    Regression guard: ``from_dict`` previously required a non-empty ``run_id``,
    but ``file_event_broadcaster`` (hardcoded ``run_id=""``), the log_pipeline
    writer (``event.run_id or ""``) and ``run_ledger`` status events
    (``run_id or ... or ""``) all emit empty ``run_id`` for events outside a
    factory run. The strict requirement made the JetStream consumer drop every
    such event ("RuntimeEventEnvelope field 'run_id' is required" spam),
    starving the runtime WebSocket feed. run_id is optional on the dataclass
    (default ``""``) and must round-trip empty/missing as ``""``.
    """
    empty_envelope = RuntimeEventEnvelope.from_dict(_runtime_event_payload(run_id=""))
    assert empty_envelope.run_id == ""

    missing_payload = _runtime_event_payload()
    missing_payload.pop("run_id")
    missing_envelope = RuntimeEventEnvelope.from_dict(missing_payload)
    assert missing_envelope.run_id == ""
