from __future__ import annotations

from pathlib import Path

from polaris.kernelone.context.engine.engine import ContextEngine
from polaris.kernelone.context.engine.models import ContextItem


def test_context_engine_summarize_items_llm_uses_deterministic_summary(
    tmp_path: Path,
) -> None:
    engine = ContextEngine(str(tmp_path))
    items = [
        ContextItem(
            kind="memory",
            provider="memory",
            content_or_pointer="Implemented task board claim flow and updated state machine.",
            priority=10,
        ),
        ContextItem(
            kind="event",
            provider="events",
            content_or_pointer="Director is waiting for QA feedback before marking the task complete.",
            priority=8,
        ),
    ]

    summarized, summary_text = engine._summarize_items_llm(
        items,
        {"task_id": "task-1", "goal": "Stabilize runtime task board"},
    )

    assert len(summarized) == 1
    assert "placeholder" not in summary_text.lower()
    assert "Stabilize runtime task board" in summary_text
    assert "memory/memory" in summary_text
