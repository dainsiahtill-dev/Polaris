"""LLM-driven context compression service for Polaris backend.

Provides intelligent context summarization using LLM.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol


class LLMClient(Protocol):
    """Protocol for LLM client."""

    async def complete(self, prompt: str, max_tokens: int = 2000) -> str:
        """Get completion from LLM."""
        ...


@dataclass
class CompactResult:
    """Result of LLM compression."""

    summary: str
    key_decisions: list[str]
    action_items: list[str]
    original_token_estimate: int
    compressed_token_estimate: int

    @property
    def compression_ratio(self) -> float:
        """Calculate compression ratio."""
        if self.original_token_estimate == 0:
            return 1.0
        return self.compressed_token_estimate / self.original_token_estimate


class LLMCompactService:
    """Service for LLM-driven context compression.

    Provides:
    - Narrative summarization (not just truncation)
    - Key decision extraction
    - Action item preservation
    - Identity anchor preservation
    """

    COMPACT_PROMPT = """You are a context compression assistant. Your task is to summarize the following conversation while preserving critical information.

Original Messages:
{messages}

Please provide:
1. A narrative summary of what happened (2-3 paragraphs)
2. Key decisions made (bullet points)
3. Action items or tasks created (bullet points)
4. Current state/context the user needs to know

Format your response as JSON:
{{
    "summary": "narrative summary",
    "key_decisions": ["decision 1", "decision 2"],
    "action_items": ["action 1", "action 2"],
    "current_context": "what the user needs to know to continue"
}}
"""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        """Initialize LLM compact service.

        Args:
            llm_client: LLM client for summarization
        """
        self._llm = llm_client

    async def compact(
        self,
        messages: list[dict[str, Any]],
        preserve_recent: int = 2,
    ) -> CompactResult:
        """Compress messages using LLM summarization.

        Args:
            messages: List of messages to compress
            preserve_recent: Number of recent messages to preserve verbatim

        Returns:
            CompactResult with summary and metadata
        """
        if not self._llm:
            raise RuntimeError("LLM client not configured")

        # Estimate original tokens
        original_text = json.dumps(messages)
        original_tokens = len(original_text) // 4

        # Format messages for LLM
        messages_text = self._format_messages(messages[:-preserve_recent] if preserve_recent > 0 else messages)

        # Get LLM summary
        prompt = self.COMPACT_PROMPT.format(messages=messages_text)
        response = await self._llm.complete(prompt, max_tokens=2000)

        # Parse response
        try:
            data = json.loads(response)
        except json.JSONDecodeError:
            # Fallback if LLM doesn't return valid JSON
            data = {
                "summary": response[:2000],
                "key_decisions": [],
                "action_items": [],
                "current_context": "",
            }

        summary_text = json.dumps(data)
        compressed_tokens = len(summary_text) // 4

        return CompactResult(
            summary=data.get("summary", ""),
            key_decisions=data.get("key_decisions", []),
            action_items=data.get("action_items", []),
            original_token_estimate=original_tokens,
            compressed_token_estimate=compressed_tokens,
        )

    def _format_messages(self, messages: list[dict[str, Any]]) -> str:
        """Format messages for LLM prompt."""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if isinstance(content, list):
                # Handle tool results
                content_parts = []
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "tool_result":
                            content_parts.append(f"[Tool {part.get('name')}: {part.get('content', '')[:200]}]")
                        elif part.get("type") == "text":
                            content_parts.append(part.get("text", ""))
                content = " ".join(content_parts)

            lines.append(f"[{role.upper()}] {content[:500]}")

        return "\n\n".join(lines)

    def create_identity_anchor(
        self,
        task: str,
        constraints: list[str],
        working_directory: str,
        key_decisions: list[str],
    ) -> str:
        """Create an identity anchor for context preservation.

        Args:
            task: Current task
            constraints: List of constraints
            working_directory: Working directory
            key_decisions: Key decisions made

        Returns:
            Identity anchor text
        """
        lines = [
            "=== CONTEXT PRESERVATION ===",
            "",
            f"TASK: {task}",
            "",
            f"WORKING DIRECTORY: {working_directory}",
            "",
        ]

        if constraints:
            lines.append("CONSTRAINTS:")
            for constraint in constraints:
                lines.append(f"  - {constraint}")
            lines.append("")

        if key_decisions:
            lines.append("KEY DECISIONS:")
            for decision in key_decisions:
                lines.append(f"  - {decision}")
            lines.append("")

        lines.append("==========================")

        return "\n".join(lines)

    async def compact_with_anchor(
        self,
        messages: list[dict[str, Any]],
        task: str,
        constraints: list[str],
        working_directory: str,
    ) -> tuple[str, CompactResult]:
        """Compress messages and create identity anchor.

        Args:
            messages: Messages to compress
            task: Current task
            constraints: Constraints
            working_directory: Working directory

        Returns:
            Tuple of (identity_anchor, compact_result)
        """
        result = await self.compact(messages)

        anchor = self.create_identity_anchor(
            task=task,
            constraints=constraints,
            working_directory=working_directory,
            key_decisions=result.key_decisions,
        )

        return anchor, result
