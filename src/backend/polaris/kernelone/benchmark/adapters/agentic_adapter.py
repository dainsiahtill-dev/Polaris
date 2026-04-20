"""Agentic benchmark adapter.

This adapter bridges the unified benchmark interface to the
roles.runtime streaming interface for Agentic evaluation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from polaris.kernelone.benchmark.unified_models import (
    ObservedBenchmarkRun,
    ToolCallObservation,
    UnifiedBenchmarkCase,
)
from polaris.kernelone.events.constants import (
    EVENT_TYPE_COMPLETE,
    EVENT_TYPE_CONTENT_CHUNK,
    EVENT_TYPE_THINKING_CHUNK,
    EVENT_TYPE_TOOL_CALL,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class AgenticBenchmarkAdapter:
    """Adapter for Agentic benchmark execution via roles.runtime.

    This adapter collects observations by streaming role sessions
    through the roles.runtime public interface.

    Example:
        adapter = AgenticBenchmarkAdapter()
        async for event in adapter.stream_session(case, workspace):
            print(event)
    """

    def __init__(self) -> None:
        """Initialize the adapter."""
        self._session_events: list[dict[str, Any]] = []

    async def stream_session(
        self,
        case: UnifiedBenchmarkCase,
        workspace: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream role session events for a benchmark case.

        Args:
            case: The benchmark case to execute.
            workspace: The workspace path.

        Yields:
            Event dictionaries from the role runtime.
        """
        import uuid

        try:
            from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
            from polaris.cells.roles.runtime.public.service import stream_role_session_command
        except ImportError as exc:
            yield {"type": "error", "error": f"import error: {exc}"}
            return

        session_id = f"agentic-bench-{case.case_id}-{uuid.uuid4().hex[:8]}"

        metadata: dict[str, Any] = {
            "benchmark_run": True,
            "benchmark_case_id": case.case_id,
            "benchmark_mode": "agentic",
        }

        command = ExecuteRoleSessionCommandV1(
            role=case.role,
            session_id=session_id,
            workspace=workspace,
            user_message=case.prompt,
            history=case.history,
            context=case.context,
            metadata=metadata,
            stream=True,
        )

        try:
            async for event in stream_role_session_command(command):
                self._session_events.append(event)
                yield event
        except (RuntimeError, ValueError) as exc:
            yield {"type": "error", "error": str(exc)}

    def parse_observation(
        self,
        case: UnifiedBenchmarkCase,
        workspace: str,
        events: list[dict[str, Any]],
    ) -> ObservedBenchmarkRun:
        """Parse events into an ObservedBenchmarkRun.

        Args:
            case: The benchmark case.
            workspace: The workspace path.
            events: List of streamed events.

        Returns:
            ObservedBenchmarkRun with parsed execution trace.
        """
        output_chunks: list[str] = []
        thinking_chunks: list[str] = []
        tool_calls: list[ToolCallObservation] = []

        for idx, event in enumerate(events):
            event_type = str(event.get("type") or "")

            if event_type == EVENT_TYPE_CONTENT_CHUNK:
                output_chunks.append(str(event.get("content") or ""))
            elif event_type == EVENT_TYPE_THINKING_CHUNK:
                thinking_chunks.append(str(event.get("content") or ""))
            elif event_type == EVENT_TYPE_TOOL_CALL:
                tool_calls.append(
                    ToolCallObservation(
                        tool=str(event.get("tool") or ""),
                        args=dict(event.get("args") or {}),
                        event_index=idx,
                    )
                )
            elif event_type == EVENT_TYPE_COMPLETE:
                result = event.get("result")
                if result:
                    content = getattr(result, "content", None)
                    if content:
                        output_chunks = [str(content)]
                    thinking = getattr(result, "thinking", None)
                    if thinking:
                        thinking_chunks = [str(thinking)]

        return ObservedBenchmarkRun(
            case_id=case.case_id,
            role=case.role,
            workspace=workspace,
            output="".join(output_chunks).strip(),
            thinking="".join(thinking_chunks).strip(),
            tool_calls=tuple(tool_calls),
            event_count=len(events),
        )

    def clear_events(self) -> None:
        """Clear cached session events."""
        self._session_events.clear()

    @property
    def session_events(self) -> list[dict[str, Any]]:
        """Get cached session events."""
        return list(self._session_events)
