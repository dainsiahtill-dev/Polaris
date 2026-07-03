"""Tests for tool-dispatch failure projection helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.kernel.tool_dispatch_projection import (
    append_tool_dispatch_dropped_control_plane_events,
    llm_metadata_from_ledger_on_error,
    tool_schema_names_for_error_audit,
)


def test_llm_metadata_from_ledger_on_error_projects_audit_and_dropped_flag() -> None:
    ledger = SimpleNamespace(
        llm_calls=[
            {
                "metadata": {
                    "context_snapshot_ref": "runtime/contexts/aa/context.json",
                    "usage": {"prompt_tokens": 10},
                }
            }
        ],
        anomaly_flags=[
            {
                "type": "TOOL_DISPATCH_DROPPED",
                "native_tool_calls_count": 2,
                "provider_response_hash": "hash-1",
            }
        ],
    )

    metadata = llm_metadata_from_ledger_on_error(ledger, messages=[], tool_definitions=[])

    assert metadata["context_snapshot_ref"] == "runtime/contexts/aa/context.json"
    assert metadata["usage"] == {"prompt_tokens": 10}
    assert metadata["tool_dispatch_dropped"] is True
    assert metadata["transaction_kernel_error_audit_available"] is True


def test_llm_metadata_from_ledger_on_error_builds_degraded_projection() -> None:
    metadata = llm_metadata_from_ledger_on_error(
        SimpleNamespace(llm_calls=[], anomaly_flags=[]),
        messages=[{"role": "user", "content": "run"}],
        tool_definitions=[
            {"type": "function", "function": {"name": "write_file"}},
            {"type": "function", "function": {"name": "execute_command"}},
        ],
    )

    assert metadata["provider_request_snapshot_degraded"] is True
    assert metadata["provider_request_assembly_projection"] == {
        "schema_version": "llm.provider_request_assembly_projection.v1",
        "source": "roles.kernel.transaction_error_path",
        "message_count": 1,
        "tool_schema_count": 2,
        "tool_names": ["write_file", "execute_command"],
    }


def test_tool_schema_names_for_error_audit_ignores_invalid_shapes() -> None:
    assert tool_schema_names_for_error_audit(
        [
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": ""}},
            {"type": "function", "function": None},
            {"name": "legacy_shape"},
        ]
    ) == ["read_file"]


def test_append_tool_dispatch_dropped_events_preserves_native_envelopes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    captured: dict[str, Any] = {}

    def fake_append_run_ledger_event(command: Any) -> None:
        captured["event"] = command.event

    monkeypatch.setattr(
        "polaris.cells.control_plane.run_ledger.public.append_run_ledger_event",
        fake_append_run_ledger_event,
    )
    monkeypatch.setattr(
        "polaris.cells.roles.kernel.internal.kernel.tool_dispatch_projection.append_director_task_boundary_verdict",
        lambda **kwargs: captured.setdefault("task_boundary", kwargs),
    )

    envelopes = [
        {"envelope_id": "native-read", "tool_name": "read_file"},
        {"envelope_id": "native-write", "tool_name": "write_file"},
    ]
    append_tool_dispatch_dropped_control_plane_events(
        role="director",
        profile=SimpleNamespace(role_id="director"),
        request=SimpleNamespace(run_id="run-1", task_id="TASK-1", context_override={}),
        workspace=str(tmp_path),
        turn_id="turn-1",
        error_metadata={
            "anomaly_flags": [
                {
                    "type": "TOOL_DISPATCH_DROPPED",
                    "native_tool_calls_count": 0,
                    "native_tool_call_envelopes": envelopes,
                    "provider_response_hash": "hash-1",
                }
            ]
        },
        reason="tool dispatch dropped",
    )

    lifecycle = captured["event"]["tool_call_lifecycle_receipt"]
    assert lifecycle["native_tool_calls_count"] == 2
    assert lifecycle["native_tool_call_envelope_refs"] == envelopes
    assert lifecycle["dropped_tool_calls"] == [
        {"tool_name": "read_file", "envelope_id": "native-read", "reason": "tool_dispatch_dropped"},
        {"tool_name": "write_file", "envelope_id": "native-write", "reason": "tool_dispatch_dropped"},
    ]
    assert captured["task_boundary"]["tool_dispatch"]["native_tool_calls_count"] == 2
