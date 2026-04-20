import hashlib
from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def generate_hash(content: str, *parts: str) -> str:
    """Deterministic hash for deduplication."""
    hasher = hashlib.sha1()
    hasher.update(content.encode("utf-8"))
    for part in parts:
        hasher.update(str(part).encode("utf-8"))
    return hasher.hexdigest()


class MemoryItem(BaseModel):
    """
    Enriched representation of an event for cognitive retrieval.
    Derived from events.jsonl, but stored in MEMORY.jsonl.
    """

    id: str = Field(default_factory=lambda: f"mem_{uuid4()}")
    source_event_id: str  # Stable UUID from events.jsonl
    step: int  # Canonical clock: Global Event Sequence
    timestamp: datetime
    role: str  # PM / Director / QA

    type: str  # observation / plan / reflection_summary
    kind: str  # error | info | success | warning | debug (Severity)

    text: str  # Natural language content
    importance: int  # 1-10 (Rules or LLM assigned)
    keywords: list[str]
    hash: str  # SHA1(text + type + role + context) for deduplication

    context: dict[str, Any] = Field(default_factory=dict)  # { "run_id": "...", "phase": "..." }
    # embedding_id is implicitly self.id

    @classmethod
    def create(
        cls,
        event_id: str,
        step: int,
        role: str,
        text: str,
        kind: str = "info",
        importance: int = 1,
        context: dict[str, Any] | None = None,
    ) -> "MemoryItem":
        context = context or {}
        # Calculate hash for dedup
        item_hash = generate_hash(text, role, kind, str(context.get("run_id", "")))

        return cls(
            source_event_id=event_id,
            step=step,
            timestamp=datetime.now(),
            role=role,
            type="observation",  # Default, can be refined
            kind=kind,
            text=text,
            importance=importance,
            keywords=[],  # To be filled by extractor
            hash=item_hash,
            context=context,
        )


class ReflectionNode(BaseModel):
    """
    Higher-level insight derived from multiple MemoryItems.
    Stored in REFLECTIONS.jsonl.
    """

    id: str = Field(default_factory=lambda: f"ref_{uuid4()}")
    created_step: int
    expiry_steps: int  # How long this insight remains valid (Decay)

    type: str  # heuristic / summary / preference
    scope: list[str]  # e.g., ["npm", "network"] - limits applicability
    confidence: float  # 0.0 - 1.0

    text: str
    evidence_mem_ids: list[str]  # Back-links to memories that formed this
    importance: int


class PromptContext(BaseModel):
    """
    Logged structure representing what was injected into the LLM context.
    Ensures observability of the 'cognitive state'.
    """

    run_id: str
    phase: str
    step: int
    persona_id: str
    retrieved_mem_ids: list[str]
    retrieved_mem_scores: list[float] = Field(default_factory=list)
    retrieved_ref_ids: list[str]
    strategy: str = "combined_ranking"
    token_usage_estimate: int
