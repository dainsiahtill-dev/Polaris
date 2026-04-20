from __future__ import annotations

import time
from pathlib import Path

from polaris.kernelone.context.engine.engine import ContextEngine
from polaris.kernelone.context.engine.models import ContextItem
from polaris.kernelone.single_agent.subagent_runtime import (
    SubagentConfig,
    SubagentSpawner,
)


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


def test_subagent_timeout_is_enforced(tmp_path: Path) -> None:
    class _MessagesAPI:
        def create(self, **kwargs):
            del kwargs
            time.sleep(0.2)
            return object()

    class _LLMClient:
        messages = _MessagesAPI()

    spawner = SubagentSpawner(
        workspace=str(tmp_path),
        llm_client=_LLMClient(),
        model="test-model",
    )

    result = spawner.spawn(
        task_description="Do not hang forever",
        context={},
        config=SubagentConfig(
            max_iterations=1,
            timeout_seconds=0.05,
        ),
    )

    assert result.success is False
    assert "timed out" in result.result.lower()
