"""Cell-level LLM executor using infrastructure adapter.

This module provides Cell-level LLM execution capabilities that wrap the
infrastructure adapter without exposing kernelone types to Cells.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from polaris.infrastructure.llm import AppLLMRuntimeAdapter

logger = logging.getLogger(__name__)


class TaskType(Enum):
    """LLM task type enumeration."""

    DIALOGUE = "dialogue"
    GENERATION = "generation"
    INTERVIEW = "interview"
    CLASSIFICATION = "classification"


@dataclass
class CellAIRequest:
    """Cell-defined AI request (infrastructure-agnostic)."""

    task_type: TaskType = TaskType.DIALOGUE
    role: str = ""
    input: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class CellAIResponse:
    """Cell-defined AI response."""

    ok: bool = False
    output: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class CellAIExecutor:
    """Cell-level AI executor using infrastructure adapter.

    This executor uses the AppLLMRuntimeAdapter to delegate to the
    KernelOne LLM runtime without exposing kernelone types to the Cell.
    """

    def __init__(self, workspace: str = ".") -> None:
        self.workspace = workspace
        self._adapter = AppLLMRuntimeAdapter()

    async def invoke(self, request: CellAIRequest) -> CellAIResponse:
        """Invoke the LLM (non-streaming)."""
        try:
            from polaris.kernelone.llm import KernelLLM

            kernel = KernelLLM(self._adapter)
            result = await kernel.invoke(  # type: ignore[attr-defined]
                task_type=request.task_type.value,
                role=request.role,
                prompt=request.input,
                options=request.options,
                context=request.context,
            )
            return CellAIResponse(
                ok=True,
                output=str(result.get("output", "") if isinstance(result, dict) else result),
                metadata=result if isinstance(result, dict) else {},
            )
        except (RuntimeError, ValueError) as exc:
            logger.warning("[CellAIExecutor] invoke failed: %s", exc)
            return CellAIResponse(ok=False, error=str(exc))

    async def invoke_stream(self, request: CellAIRequest):
        """Invoke the LLM (streaming).

        Yields Cell-defined stream events.
        """
        try:
            from polaris.kernelone.llm import KernelLLM

            kernel = KernelLLM(self._adapter)
            async for event in kernel.invoke_stream(  # type: ignore[attr-defined]
                task_type=request.task_type.value,
                role=request.role,
                prompt=request.input,
                options=request.options,
                context=request.context,
            ):
                # Convert kernelone event to cell-defined event
                event_type = getattr(event, "type", None)
                if event_type is None:
                    continue

                type_value = getattr(event_type, "value", str(event_type))

                if type_value == "chunk":
                    yield {
                        "type": "chunk",
                        "chunk": getattr(event, "chunk", ""),
                    }
                elif type_value == "reasoning_chunk":
                    yield {
                        "type": "reasoning_chunk",
                        "reasoning": getattr(event, "reasoning", ""),
                    }
                elif type_value == "complete":
                    yield {
                        "type": "complete",
                        "meta": getattr(event, "meta", {}),
                    }
                elif type_value == "error":
                    yield {
                        "type": "error",
                        "error": getattr(event, "error", "unknown"),
                    }

        except (RuntimeError, ValueError) as exc:
            logger.warning("[CellAIExecutor] invoke_stream failed: %s", exc)
            yield {"type": "error", "error": str(exc)}


def normalize_list(value: Any, *, limit: int = 10) -> list[str]:
    """Normalize various input types to a list of strings."""
    items: list[str] = []

    def _append(candidate: Any) -> None:
        if isinstance(candidate, str):
            for line in candidate.replace("\r\n", "\n").split("\n"):
                line = line.strip()
                if line:
                    items.append(line)
            return
        if isinstance(candidate, (list, tuple)):
            for item in candidate:
                _append(item)
            return
        if isinstance(candidate, dict):
            for item in candidate.values():
                _append(item)
            return
        text = str(candidate or "").strip()
        if text:
            items.append(text)

    _append(value)
    unique_items: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            unique_items.append(item)
            if len(unique_items) >= limit:
                break
    return unique_items


def truncate_text(text: str, limit: int) -> str:
    """Truncate text to limit characters."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def split_lines(value: str) -> list[str]:
    """Split text into lines."""
    return [line.strip() for line in str(value or "").replace("\r\n", "\n").split("\n") if line.strip()]


class ResponseNormalizer:
    """Response normalization utilities (from kernelone.llm.engine)."""

    @staticmethod
    def extract_json_object(text: str) -> dict[str, Any] | None:
        """Extract JSON object from text."""
        import json
        import re

        text = text.strip()

        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try code blocks
        patterns = [
            r"```json\s*(.*?)\s*```",
            r"```\s*(.*?)\s*```",
        ]
        for pattern in patterns:
            for match in re.findall(pattern, text, re.DOTALL):
                try:
                    return json.loads(match.strip())
                except json.JSONDecodeError:
                    continue

        # Try <output> tags
        output_match = re.search(r"<output>(.*?)</output>", text, re.DOTALL)
        if output_match:
            try:
                return json.loads(output_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try first { to last }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and start < end:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass

        return None

    @staticmethod
    def looks_truncated_json(text: str) -> bool:
        """Check if text looks like truncated JSON."""
        import re

        text = text.strip()
        # Check for unclosed braces
        open_braces = len(re.findall(r"\{", text))
        close_braces = len(re.findall(r"\}", text))
        return open_braces > close_braces


__all__ = [
    "CellAIExecutor",
    "CellAIRequest",
    "CellAIResponse",
    "ResponseNormalizer",
    "TaskType",
    "normalize_list",
    "split_lines",
    "truncate_text",
]
