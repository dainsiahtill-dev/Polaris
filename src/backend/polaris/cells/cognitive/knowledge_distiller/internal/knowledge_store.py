"""Knowledge Store - persistent storage for distilled knowledge units."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from polaris.kernelone.fs.jsonl.locking import file_lock
from polaris.kernelone.storage import resolve_runtime_path

from ..public.contracts import (
    DistilledKnowledgeUnitV1,
    KnowledgeRetrievalResultV1,
    RetrieveKnowledgeQueryV1,
)

logger = logging.getLogger(__name__)


class KnowledgeStore:
    """Persistent storage for distilled knowledge units.

    Stores knowledge in JSONL format for durability,
    with in-memory index for fast retrieval.
    """

    def __init__(
        self,
        workspace: str = ".",
        *,
        knowledge_file: str | None = None,
    ) -> None:
        self._workspace = str(workspace or ".")
        self._knowledge_file = knowledge_file or resolve_runtime_path(
            self._workspace, "runtime/knowledge/distilled_knowledge.jsonl"
        )
        self._lock_file = f"{self._knowledge_file}.lock"

        # In-memory index: knowledge_id -> knowledge unit
        self._by_id: dict[str, DistilledKnowledgeUnitV1] = {}

        # In-memory indexes for fast lookup
        self._by_type: dict[str, set[str]] = {}  # knowledge_type -> knowledge_ids
        self._by_role: dict[str, set[str]] = {}  # role -> knowledge_ids
        self._text_index: dict[str, set[str]] = {}  # token -> knowledge_ids

        # Ensure directory exists
        knowledge_dir = os.path.dirname(self._knowledge_file) or "."
        os.makedirs(knowledge_dir, exist_ok=True)

        # Load existing knowledge
        self._load()

    def _load(self) -> None:
        """Load knowledge from JSONL file."""
        if not os.path.exists(self._knowledge_file):
            return

        try:
            with open(self._knowledge_file, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    # Parse datetime
                    if isinstance(data.get("created_at"), str):
                        data["created_at"] = datetime.fromisoformat(data["created_at"])
                    unit = DistilledKnowledgeUnitV1(**data)
                    self._add_to_indexes(unit)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("Failed to load knowledge store from %s: %s", self._knowledge_file, exc)

    def _persist(self, unit: DistilledKnowledgeUnitV1) -> None:
        """Append unit to JSONL file."""
        os.makedirs(os.path.dirname(self._knowledge_file) or ".", exist_ok=True)
        data = {
            "knowledge_id": unit.knowledge_id,
            "knowledge_type": unit.knowledge_type,
            "pattern_summary": unit.pattern_summary,
            "confidence": unit.confidence,
            "occurrence_count": unit.occurrence_count,
            "related_findings": unit.related_findings,
            "extracted_insight": unit.extracted_insight,
            "prevention_hint": unit.prevention_hint,
            "created_at": unit.created_at.isoformat() if unit.created_at else datetime.now(timezone.utc).isoformat(),
            "metadata": unit.metadata,
        }

        with (
            file_lock(self._lock_file, timeout_sec=5.0),
            open(self._knowledge_file, "a", encoding="utf-8", newline="\n") as f,
        ):
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    def _add_to_indexes(self, unit: DistilledKnowledgeUnitV1) -> None:
        """Add unit to in-memory indexes."""
        self._by_id[unit.knowledge_id] = unit

        # Index by type
        if unit.knowledge_type not in self._by_type:
            self._by_type[unit.knowledge_type] = set()
        self._by_type[unit.knowledge_type].add(unit.knowledge_id)

        # Index by role from metadata
        role = unit.metadata.get("role")
        if role:
            if role not in self._by_role:
                self._by_role[role] = set()
            self._by_role[role].add(unit.knowledge_id)

        # Index by text tokens
        text = f"{unit.pattern_summary} {unit.extracted_insight}".lower()
        tokens = self._tokenize(text)
        for token in tokens:
            if token not in self._text_index:
                self._text_index[token] = set()
            self._text_index[token].add(unit.knowledge_id)

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Simple tokenizer."""
        import re

        tokens = re.findall(r"\b[a-zA-Z0-9_]{2,}\b", text.lower())
        return set(tokens)

    def store(self, unit: DistilledKnowledgeUnitV1) -> None:
        """Store a knowledge unit."""
        # Check if this pattern already exists (merge if so)
        existing = self._find_similar(unit)
        if existing:
            # Merge with existing
            merged = self._merge_knowledge(existing, unit)
            self._by_id[existing.knowledge_id] = merged
            logger.debug("Merged knowledge unit %s with existing %s", unit.knowledge_id, existing.knowledge_id)
        else:
            # Store new
            self._add_to_indexes(unit)
            self._persist(unit)
            logger.debug("Stored new knowledge unit %s", unit.knowledge_id)

    def _find_similar(self, unit: DistilledKnowledgeUnitV1) -> DistilledKnowledgeUnitV1 | None:
        """Find similar existing knowledge unit."""
        # Simple hash-based deduplication
        pattern_hash = hash(unit.pattern_summary.lower().strip())
        for existing in self._by_id.values():
            if hash(existing.pattern_summary.lower().strip()) == pattern_hash:
                return existing
        return None

    def _merge_knowledge(
        self,
        existing: DistilledKnowledgeUnitV1,
        new: DistilledKnowledgeUnitV1,
    ) -> DistilledKnowledgeUnitV1:
        """Merge two knowledge units."""
        # Combine related findings
        combined_related = list(set(existing.related_findings + new.related_findings))

        # Use higher confidence
        higher_confidence = max(existing.confidence, new.confidence)

        # Increment occurrence count
        new_occurrence = existing.occurrence_count + new.occurrence_count

        # Merge prevention hints (keep existing if new doesn't have one)
        prevention = existing.prevention_hint or new.prevention_hint

        merged = DistilledKnowledgeUnitV1(
            knowledge_id=existing.knowledge_id,  # Keep original ID
            knowledge_type=existing.knowledge_type,
            pattern_summary=existing.pattern_summary,
            confidence=higher_confidence,
            occurrence_count=new_occurrence,
            related_findings=combined_related,
            extracted_insight=existing.extracted_insight,
            prevention_hint=prevention,
            created_at=existing.created_at,
            metadata={**existing.metadata, **new.metadata},
        )

        # Overwrite in persisted store (append will happen from store())
        self._overwrite_persisted(merged)
        return merged

    def _overwrite_persisted(self, unit: DistilledKnowledgeUnitV1) -> None:
        """Overwrite a unit in the JSONL file."""
        # Read all lines, replace the matching knowledge_id, rewrite
        if not os.path.exists(self._knowledge_file):
            return

        lines_to_keep: list[str] = []
        try:
            with open(self._knowledge_file, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if data.get("knowledge_id") != unit.knowledge_id:
                        lines_to_keep.append(line)

            # Append new version
            data = {
                "knowledge_id": unit.knowledge_id,
                "knowledge_type": unit.knowledge_type,
                "pattern_summary": unit.pattern_summary,
                "confidence": unit.confidence,
                "occurrence_count": unit.occurrence_count,
                "related_findings": unit.related_findings,
                "extracted_insight": unit.extracted_insight,
                "prevention_hint": unit.prevention_hint,
                "created_at": unit.created_at.isoformat()
                if unit.created_at
                else datetime.now(timezone.utc).isoformat(),
                "metadata": unit.metadata,
            }
            lines_to_keep.append(json.dumps(data, ensure_ascii=False) + "\n")

            # Rewrite file
            with (
                file_lock(self._lock_file, timeout_sec=5.0),
                open(self._knowledge_file, "w", encoding="utf-8", newline="\n") as f,
            ):
                f.writelines(lines_to_keep)

        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to overwrite persisted knowledge: %s", exc)

    def retrieve(self, query: RetrieveKnowledgeQueryV1) -> KnowledgeRetrievalResultV1:
        """Retrieve relevant knowledge units for a query."""
        candidate_ids: set[str] | None = None

        # Filter by type
        if query.knowledge_type:
            type_ids = self._by_type.get(query.knowledge_type, set())
            candidate_ids = type_ids if candidate_ids is None else candidate_ids.intersection(type_ids)

        # Filter by role
        if query.role_filter:
            role_ids = self._by_role.get(query.role_filter, set())
            candidate_ids = role_ids if candidate_ids is None else candidate_ids.intersection(role_ids)

        # Text search
        query_tokens = self._tokenize(query.query.lower())
        if query_tokens:
            token_ids: set[str] = set()
            for token in query_tokens:
                ids = self._text_index.get(token, set())
                token_ids = token_ids.union(ids)
            candidate_ids = token_ids if candidate_ids is None else candidate_ids.intersection(token_ids)

        # If no filters, return all
        if candidate_ids is None:
            candidate_ids = set(self._by_id.keys())

        # Get candidates and score
        candidates = [self._by_id[kid] for kid in candidate_ids if kid in self._by_id]

        # Filter by confidence
        candidates = [c for c in candidates if c.confidence >= query.min_confidence]

        # Sort by confidence and occurrence
        candidates.sort(key=lambda c: c.confidence * 0.6 + min(c.occurrence_count / 10, 1.0) * 0.4, reverse=True)

        # Limit to top_k
        results = candidates[: query.top_k]

        return KnowledgeRetrievalResultV1(
            knowledge_units=results,
            query=query.query,
            total_available=len(candidates),
        )

    def get_all(self) -> list[DistilledKnowledgeUnitV1]:
        """Get all knowledge units."""
        return list(self._by_id.values())

    def clear(self) -> None:
        """Clear all knowledge (for testing)."""
        self._by_id.clear()
        self._by_type.clear()
        self._by_role.clear()
        self._text_index.clear()
        if os.path.exists(self._knowledge_file):
            os.remove(self._knowledge_file)
