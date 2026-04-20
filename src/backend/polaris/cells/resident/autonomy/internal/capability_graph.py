"""Capability graph derived from skills and decision performance."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from polaris.cells.resident.autonomy.internal.meta_cognition import selected_strategy_tags
from polaris.domain.models.resident import (
    CapabilityGraphSnapshot,
    CapabilityNode,
    DecisionRecord,
    DecisionVerdict,
    SkillArtifact,
    utc_now_iso,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from polaris.cells.resident.autonomy.internal.resident_storage import ResidentStorage


class CapabilityGraph:
    """Maintain an evidence-backed capability graph."""

    def __init__(self, storage: ResidentStorage) -> None:
        self._storage = storage

    def rebuild(
        self,
        decisions: Iterable[DecisionRecord],
        skills: Iterable[SkillArtifact],
    ) -> CapabilityGraphSnapshot:
        decision_list = list(decisions)
        skill_list = list(skills)
        strategy_stats: dict[str, dict[str, int]] = defaultdict(
            lambda: {"attempts": 0, "successes": 0, "evidence_count": 0}
        )
        for record in decision_list:
            for tag in selected_strategy_tags(record):
                bucket = strategy_stats[tag]
                bucket["attempts"] += 1
                bucket["evidence_count"] += len(record.evidence_refs)
                if record.verdict == DecisionVerdict.SUCCESS:
                    bucket["successes"] += 1

        capability_map: dict[str, CapabilityNode] = {}
        for tag, bucket in strategy_stats.items():
            attempts = max(1, int(bucket["attempts"]))
            success_rate = float(bucket["successes"]) / float(attempts)
            capability_map[tag] = CapabilityNode(
                name=tag,
                kind="strategy",
                score=min(1.0, round((success_rate * 0.8) + min(attempts, 5) * 0.04, 4)),
                success_rate=round(success_rate, 4),
                attempts=attempts,
                evidence_count=int(bucket["evidence_count"]),
                supporting_strategy_tags=[tag],
                updated_at=utc_now_iso(),
            )

        for skill in skill_list:
            key = skill.name or skill.skill_id
            existing = capability_map.get(key)
            if existing is None:
                capability_map[key] = CapabilityNode(
                    name=key,
                    kind="skill",
                    score=round(skill.confidence, 4),
                    success_rate=round(skill.confidence, 4),
                    attempts=len(skill.source_decision_ids),
                    evidence_count=len(skill.evidence_refs),
                    supporting_skill_ids=[skill.skill_id],
                    updated_at=utc_now_iso(),
                )
                continue
            existing.kind = "hybrid"
            existing.supporting_skill_ids = sorted(
                list(dict.fromkeys([*existing.supporting_skill_ids, skill.skill_id]))
            )
            existing.evidence_count += len(skill.evidence_refs)
            existing.score = round(min(1.0, (existing.score + skill.confidence) / 2.0), 4)
            existing.success_rate = round(max(existing.success_rate, skill.confidence), 4)
            existing.updated_at = utc_now_iso()

        capabilities = sorted(
            capability_map.values(),
            key=lambda item: (item.score, item.attempts, item.evidence_count),
            reverse=True,
        )
        gaps = [node.name for node in capabilities if node.attempts >= 2 and node.score < 0.45][:10]
        snapshot = CapabilityGraphSnapshot(
            generated_at=utc_now_iso(),
            capabilities=capabilities,
            gaps=gaps,
        )
        self._storage.save_capability_graph(snapshot)
        return snapshot
