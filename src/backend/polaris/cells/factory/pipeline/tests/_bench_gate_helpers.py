"""Canonical disk-event fixture shared by split gate tests."""

from typing import Any


def _disk_llm_event(
    role: str,
    provider: str,
    model: str,
    *,
    event: str = "llm_call_end",
    source: str = "roles.kernel.events",
    run_id: str = "test-run-001",
) -> dict[str, Any]:
    """Create an event matching _emit_llm_event_to_disk schema."""
    return {
        "schema_version": 1,
        "ts": "2026-06-21T00:00:00",
        "ts_epoch": 1750464000.0,
        "seq": 1,
        "event_id": "abcd1234",
        "run_id": run_id,
        "iteration": 1,
        "role": role,
        "source": source,
        "event": event,
        "data": {
            "event_type": event,
            "role": role,
            "model": model,
            "provider": provider,
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "metadata": {"call_id": "c0", "workspace": "/tmp/test"},
        },
    }
