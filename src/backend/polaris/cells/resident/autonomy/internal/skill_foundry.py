"""Derive reusable resident skills from repeated successful decisions."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from polaris.cells.resident.autonomy.internal.meta_cognition import selected_strategy_tags
from polaris.domain.models.resident import DecisionRecord, DecisionVerdict, SkillArtifact, utc_now_iso

if TYPE_CHECKING:
    from collections.abc import Iterable

    from polaris.cells.resident.autonomy.internal.resident_storage import ResidentStorage


def _primary_strategy_tag(record: DecisionRecord) -> str:
    tags = selected_strategy_tags(record)
    return str(tags[0] if tags else "").strip()


class SkillFoundry:
    """Build evidence-backed skill artifacts from successful execution traces."""

    def __init__(self, storage: ResidentStorage) -> None:
        self._storage = storage

    def extract(self, decisions: Iterable[DecisionRecord]) -> list[SkillArtifact]:
        decision_list = list(decisions)
        existing = {skill.name: skill for skill in self._storage.load_skills() if skill.name}
        grouped: dict[str, list[DecisionRecord]] = defaultdict(list)

        for record in decision_list:
            if record.verdict != DecisionVerdict.SUCCESS:
                continue
            tag = _primary_strategy_tag(record)
            if not tag:
                continue
            key = f"{record.actor or 'unknown'}::{record.stage or 'unknown'}::{tag}"
            grouped[key].append(record)

        artifacts: list[SkillArtifact] = []
        for key, records in grouped.items():
            if len(records) < 2:
                continue
            actor, stage, tag = key.split("::", 2)
            trigger = f"{actor}::{stage}"
            name = f"{actor}:{stage}:{tag}"
            evidence_refs: list[str] = []
            source_ids: list[str] = []
            failure_modes: list[str] = []
            for record in records:
                evidence_refs.extend(record.evidence_refs[:2])
                source_ids.append(record.decision_id)
                outcome_status = str(record.actual_outcome.get("status") or "").strip()
                if outcome_status:
                    failure_modes.append(f"unexpected_{outcome_status}")
            skill = existing.get(name)
            steps = [
                f"Prefer strategy tag `{tag}` when `{trigger}` recurs.",
                "Validate expected outcomes before execution.",
                "Capture evidence refs for every critical decision boundary.",
            ]
            if skill is None:
                skill = SkillArtifact(
                    name=name,
                    trigger=trigger,
                    preconditions=[
                        f"actor={actor}",
                        f"stage={stage}",
                        f"strategy_tag={tag}",
                    ],
                    steps=steps,
                    evidence_refs=list(dict.fromkeys(evidence_refs))[:8],
                    failure_modes=sorted(dict.fromkeys(failure_modes))[:6],
                    confidence=min(0.95, 0.45 + (len(records) * 0.08)),
                    source_decision_ids=list(dict.fromkeys(source_ids)),
                )
            else:
                skill.preconditions = [
                    f"actor={actor}",
                    f"stage={stage}",
                    f"strategy_tag={tag}",
                ]
                skill.steps = steps
                skill.evidence_refs = list(dict.fromkeys(skill.evidence_refs + evidence_refs))[:12]
                skill.failure_modes = sorted(dict.fromkeys(skill.failure_modes + failure_modes))[:8]
                skill.confidence = min(0.99, max(skill.confidence, 0.45 + (len(records) * 0.08)))
                skill.source_decision_ids = list(dict.fromkeys(skill.source_decision_ids + source_ids))[:20]
                skill.version += 1
                skill.updated_at = utc_now_iso()
            artifacts.append(skill)

        touched = {item.name for item in artifacts if item.name}
        for name, skill in existing.items():
            if name not in touched:
                artifacts.append(skill)

        artifacts = sorted(
            artifacts,
            key=lambda item: (item.confidence, len(item.source_decision_ids)),
            reverse=True,
        )
        self._storage.save_skills(artifacts)
        return artifacts
