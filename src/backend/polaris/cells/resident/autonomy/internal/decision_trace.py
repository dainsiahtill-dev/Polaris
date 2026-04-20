"""Decision trace recording and retrieval."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from polaris.domain.models.resident import DecisionRecord, DecisionVerdict

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from polaris.cells.resident.autonomy.internal.resident_storage import ResidentStorage


class DecisionTraceRecorder:
    """Append-only decision trace."""

    def __init__(self, storage: ResidentStorage) -> None:
        self._storage = storage

    def record(self, payload: DecisionRecord | Mapping[str, Any]) -> DecisionRecord:
        record = payload if isinstance(payload, DecisionRecord) else DecisionRecord.from_dict(payload)
        self._storage.append_decision(record)
        return record

    def list_recent(
        self,
        *,
        limit: int = 100,
        actor: str = "",
        verdict: str = "",
    ) -> list[DecisionRecord]:
        decisions = self._storage.load_decisions(limit=max(1, int(limit or 1)))
        actor_token = str(actor or "").strip().lower()
        verdict_token = str(verdict or "").strip().lower()
        filtered: list[DecisionRecord] = []
        for record in decisions:
            if actor_token and record.actor.strip().lower() != actor_token:
                continue
            if verdict_token and record.verdict.value != verdict_token:
                continue
            filtered.append(record)
        return filtered

    def count_by_verdict(self, decisions: Iterable[DecisionRecord]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in decisions:
            token = (
                record.verdict.value
                if isinstance(record.verdict, DecisionVerdict)
                else str(record.verdict or "unknown")
            )
            counts[token] = counts.get(token, 0) + 1
        return counts
