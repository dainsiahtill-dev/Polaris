"""Regression tests for stream audit metadata preservation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.turn_engine.stream_handler import StreamEventHandler


@pytest.mark.asyncio
async def test_process_stream_materializes_context_metadata_audit(tmp_path: Path) -> None:
    audit = {
        "ok": True,
        "expected": True,
        "source": "test",
        "prompt_digest": "stream123",
    }

    async def _raw_stream() -> AsyncIterator[dict[str, Any]]:
        yield {"type": "chunk", "content": "Verified."}
        yield {
            "type": "context_metadata",
            "model": "test-stream-model",
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 4,
                "total_tokens": 15,
            },
            "context_os_audit": audit,
        }

    handler = StreamEventHandler(workspace=str(tmp_path))
    events = [
        event
        async for event in handler.process_stream(
            _raw_stream(),
            round_index=0,
            start_time=0.0,
            profile=SimpleNamespace(),
        )
    ]

    visible_chunks = [event for event in events if event.get("type") == "content_chunk"]
    materialized = events[-1]
    usage = materialized["usage"]

    assert visible_chunks[-1]["content"] == "Verified."
    assert materialized["type"] == "_internal_materialize"
    assert materialized["model"] == "test-stream-model"
    assert usage["prompt_tokens"] == 11
    assert usage["context_os_audit"]["prompt_digest"] == "stream123"
