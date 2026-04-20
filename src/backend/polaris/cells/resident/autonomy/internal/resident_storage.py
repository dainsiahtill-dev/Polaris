"""Resident storage helpers with explicit UTF-8 I/O."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.domain.models.resident import (
    CapabilityGraphSnapshot,
    DecisionRecord,
    ExperimentRecord,
    GoalProposal,
    ImprovementProposal,
    MetaInsight,
    ResidentAgenda,
    ResidentIdentity,
    ResidentRuntimeState,
    SkillArtifact,
    SkillProposal,
)
from polaris.infrastructure.compat.io_utils import write_json_atomic
from polaris.kernelone.fs.jsonl.ops import append_jsonl
from polaris.kernelone.storage import (
    resolve_runtime_path,
    resolve_workspace_persistent_path,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True)
class ResidentPaths:
    root_dir: str
    meta_state_path: str
    identity_path: str
    agenda_path: str
    goals_path: str
    insights_path: str
    capability_graph_path: str
    skills_path: str
    skill_proposals_path: str  # Phase 1.3
    experiments_path: str
    improvements_path: str
    decision_trace_path: str
    runtime_state_path: str
    tick_history_path: str


class ResidentStorage:
    """Filesystem persistence for resident state."""

    def __init__(self, workspace: str) -> None:
        resolved_workspace = str(Path(workspace or ".").expanduser().resolve())
        root_dir = resolve_workspace_persistent_path(
            resolved_workspace,
            "workspace/meta/resident",
        )
        self.workspace = resolved_workspace
        self.paths = ResidentPaths(
            root_dir=root_dir,
            meta_state_path=os.path.join(root_dir, "meta_cognition.json"),
            identity_path=os.path.join(root_dir, "identity.json"),
            agenda_path=os.path.join(root_dir, "agenda.json"),
            goals_path=os.path.join(root_dir, "goals.json"),
            insights_path=os.path.join(root_dir, "insights.json"),
            capability_graph_path=os.path.join(root_dir, "capability_graph.json"),
            skills_path=os.path.join(root_dir, "skills.json"),
            skill_proposals_path=os.path.join(root_dir, "skill_proposals.json"),  # Phase 1.3
            experiments_path=os.path.join(root_dir, "experiments.json"),
            improvements_path=os.path.join(root_dir, "improvements.json"),
            decision_trace_path=os.path.join(root_dir, "decision_trace.jsonl"),
            runtime_state_path=resolve_runtime_path(
                resolved_workspace,
                "runtime/state/resident.state.json",
            ),
            tick_history_path=os.path.join(root_dir, "tick_history.jsonl"),
        )
        self.ensure_dirs()

    def ensure_dirs(self) -> None:
        Path(self.paths.root_dir).mkdir(parents=True, exist_ok=True)
        runtime_parent = os.path.dirname(self.paths.runtime_state_path)
        if runtime_parent:
            Path(runtime_parent).mkdir(parents=True, exist_ok=True)

    def read_json(self, path: str, default: Any) -> Any:
        if not path or not os.path.isfile(path):
            return default
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            return data
        except (RuntimeError, ValueError):
            return default

    def write_json(self, path: str, payload: dict[str, Any]) -> None:
        write_json_atomic(path, payload)

    def read_jsonl(self, path: str) -> list[dict[str, Any]]:
        if not path or not os.path.isfile(path):
            return []
        rows: list[dict[str, Any]] = []
        try:
            with open(path, encoding="utf-8") as handle:
                for raw_line in handle:
                    line = str(raw_line or "").strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except (RuntimeError, ValueError):
                        continue
                    if isinstance(item, dict):
                        rows.append(item)
        except (RuntimeError, ValueError):
            return rows
        return rows

    def append_jsonl(self, path: str, payload: dict[str, Any]) -> None:
        append_jsonl(path, payload, buffered=False)

    def _load_items_container(self, path: str) -> list[dict[str, Any]]:
        data = self.read_json(path, {})
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return [item for item in data["items"] if isinstance(item, dict)]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    def save_items(self, path: str, items: Sequence[Any]) -> None:
        self.write_json(path, {"items": [item.to_dict() for item in items]})

    def load_identity(self) -> ResidentIdentity | None:
        data = self.read_json(self.paths.identity_path, {})
        if not isinstance(data, dict) or not data:
            return None
        return ResidentIdentity.from_dict(data)

    def save_identity(self, identity: ResidentIdentity) -> None:
        self.write_json(self.paths.identity_path, identity.to_dict())

    def load_agenda(self) -> ResidentAgenda | None:
        data = self.read_json(self.paths.agenda_path, {})
        if not isinstance(data, dict) or not data:
            return None
        return ResidentAgenda.from_dict(data)

    def save_agenda(self, agenda: ResidentAgenda) -> None:
        self.write_json(self.paths.agenda_path, agenda.to_dict())

    def load_runtime_state(self) -> ResidentRuntimeState | None:
        data = self.read_json(self.paths.runtime_state_path, {})
        if not isinstance(data, dict) or not data:
            return None
        return ResidentRuntimeState.from_dict(data)

    def save_runtime_state(self, state: ResidentRuntimeState) -> None:
        self.write_json(self.paths.runtime_state_path, state.to_dict())

    def load_goals(self) -> list[GoalProposal]:
        return [GoalProposal.from_dict(item) for item in self._load_items_container(self.paths.goals_path)]

    def save_goals(self, goals: Sequence[GoalProposal]) -> None:
        self.save_items(self.paths.goals_path, goals)

    def load_insights(self) -> list[MetaInsight]:
        return [MetaInsight.from_dict(item) for item in self._load_items_container(self.paths.insights_path)]

    def save_insights(self, insights: Sequence[MetaInsight]) -> None:
        self.save_items(self.paths.insights_path, insights)

    def load_meta_state(self) -> dict[str, Any]:
        data = self.read_json(self.paths.meta_state_path, {})
        return data if isinstance(data, dict) else {}

    def save_meta_state(self, payload: dict[str, Any]) -> None:
        self.write_json(self.paths.meta_state_path, payload)

    def load_capability_graph(self) -> CapabilityGraphSnapshot | None:
        data = self.read_json(self.paths.capability_graph_path, {})
        if not isinstance(data, dict) or not data:
            return None
        return CapabilityGraphSnapshot.from_dict(data)

    def save_capability_graph(self, graph: CapabilityGraphSnapshot) -> None:
        self.write_json(self.paths.capability_graph_path, graph.to_dict())

    def load_skills(self) -> list[SkillArtifact]:
        return [SkillArtifact.from_dict(item) for item in self._load_items_container(self.paths.skills_path)]

    def save_skills(self, skills: Sequence[SkillArtifact]) -> None:
        self.save_items(self.paths.skills_path, skills)

    # Phase 1.3: Skill Proposal storage
    def load_skill_proposals(self) -> list[SkillProposal]:
        return [SkillProposal.from_dict(item) for item in self._load_items_container(self.paths.skill_proposals_path)]

    def save_skill_proposals(self, proposals: Sequence[SkillProposal]) -> None:
        self.save_items(self.paths.skill_proposals_path, proposals)

    def find_skill_proposal_by_pattern(self, pattern: str) -> SkillProposal | None:
        """Find a skill proposal by pattern (for duplicate detection)."""
        proposals = self.load_skill_proposals()
        pattern_normalized = str(pattern or "").strip().lower()
        for proposal in proposals:
            if proposal.pattern.strip().lower() == pattern_normalized:
                return proposal
        return None

    def load_experiments(self) -> list[ExperimentRecord]:
        return [ExperimentRecord.from_dict(item) for item in self._load_items_container(self.paths.experiments_path)]

    def save_experiments(self, experiments: Sequence[ExperimentRecord]) -> None:
        self.save_items(self.paths.experiments_path, experiments)

    def load_improvements(self) -> list[ImprovementProposal]:
        return [
            ImprovementProposal.from_dict(item) for item in self._load_items_container(self.paths.improvements_path)
        ]

    def save_improvements(self, improvements: Sequence[ImprovementProposal]) -> None:
        self.save_items(self.paths.improvements_path, improvements)

    def append_decision(self, decision: DecisionRecord) -> None:
        self.append_jsonl(self.paths.decision_trace_path, decision.to_dict())

    def load_decisions(self, *, limit: int = 200) -> list[DecisionRecord]:
        rows = self.read_jsonl(self.paths.decision_trace_path)
        if limit > 0:
            rows = rows[-limit:]
        return [DecisionRecord.from_dict(item) for item in rows]

    def append_tick_history(self, payload: dict[str, Any]) -> None:
        self.append_jsonl(self.paths.tick_history_path, payload)
