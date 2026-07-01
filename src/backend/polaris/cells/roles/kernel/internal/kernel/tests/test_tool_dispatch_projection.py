"""Tests for tool-dispatch failure projection helpers."""

from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.roles.kernel.internal.kernel.tool_dispatch_projection import (
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
