"""Governed goal proposal pipeline for the resident subsystem."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from polaris.domain.models.resident import (
    CapabilityGraphSnapshot,
    GoalProposal,
    GoalStatus,
    GoalType,
    ImprovementProposal,
    ImprovementStatus,
    MetaInsight,
    coerce_float,
    utc_now_iso,
)

if TYPE_CHECKING:
    from polaris.cells.resident.autonomy.internal.resident_storage import ResidentStorage


def _stable_goal_fingerprint(
    goal_type: GoalType,
    title: str,
    source: str,
    scope: Iterable[str],
) -> str:
    raw = "||".join(
        [
            goal_type.value,
            str(title or "").strip().lower(),
            str(source or "").strip().lower(),
            "|".join(sorted(str(item).strip().lower() for item in scope if str(item).strip())),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _scope_from_evidence(evidence_refs: Iterable[str]) -> list[str]:
    scope: list[str] = []
    for ref in evidence_refs:
        token = str(ref or "").strip().replace("\\", "/")
        if not token:
            continue
        parts = [part for part in token.split("/") if part]
        if len(parts) >= 2:
            scope.append("/".join(parts[:2]))
        elif parts:
            scope.append(parts[0])
    return list(dict.fromkeys(scope))[:6]


class GoalGovernor:
    """Create, approve, reject, and materialize governed goals."""

    def __init__(self, storage: ResidentStorage) -> None:
        self._storage = storage

    def list_goals(self, *, status: str = "") -> list[GoalProposal]:
        goals = self._storage.load_goals()
        status_token = str(status or "").strip().lower()
        if not status_token:
            return goals
        return [goal for goal in goals if goal.status.value == status_token]

    def create_manual_proposal(self, payload: Mapping[str, Any]) -> GoalProposal:
        type_token = str(payload.get("goal_type") or GoalType.MAINTENANCE.value).strip().lower()
        try:
            goal_type = GoalType(type_token)
        except (RuntimeError, ValueError):
            goal_type = GoalType.MAINTENANCE
        title = str(payload.get("title") or "").strip()
        motivation = str(payload.get("motivation") or "").strip()
        source = str(payload.get("source") or "manual").strip() or "manual"
        evidence_refs = [str(item).strip() for item in payload.get("evidence_refs") or [] if str(item).strip()]
        scope = [str(item).strip() for item in payload.get("scope") or [] if str(item).strip()]
        raw_budget = payload.get("budget")
        budget = dict(raw_budget) if isinstance(raw_budget, Mapping) else {"max_tasks": 2, "max_parallel_tasks": 1}
        if not scope:
            scope = _scope_from_evidence(evidence_refs)
        proposal = GoalProposal(
            goal_type=goal_type,
            title=title,
            motivation=motivation,
            source=source,
            expected_value=coerce_float(
                payload.get("expected_value"),
                default=0.6,
                minimum=0.0,
                maximum=1.0,
            ),
            risk_score=coerce_float(
                payload.get("risk_score"),
                default=0.2,
                minimum=0.0,
                maximum=1.0,
            ),
            scope=scope,
            budget=budget,
            evidence_refs=evidence_refs,
            derived_from=[str(item).strip() for item in payload.get("derived_from") or [] if str(item).strip()],
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
        proposal.fingerprint = _stable_goal_fingerprint(
            proposal.goal_type,
            proposal.title,
            proposal.source,
            proposal.scope,
        )
        goals = self._storage.load_goals()
        if any(goal.fingerprint == proposal.fingerprint for goal in goals):
            for goal in goals:
                if goal.fingerprint == proposal.fingerprint:
                    return goal
        goals.append(proposal)
        self._storage.save_goals(goals)
        return proposal

    def generate(
        self,
        *,
        insights: Iterable[MetaInsight],
        capability_graph: CapabilityGraphSnapshot | None,
        improvements: Iterable[ImprovementProposal],
        max_new: int = 6,
    ) -> list[GoalProposal]:
        goals = self._storage.load_goals()
        seen = {goal.fingerprint for goal in goals if goal.fingerprint}
        new_goals: list[GoalProposal] = []

        for insight in insights:
            goal_type = GoalType.MAINTENANCE
            if insight.insight_type in {"strategy_risk", "failure_cluster"}:
                goal_type = GoalType.RELIABILITY
            elif insight.insight_type == "prediction_gap":
                goal_type = GoalType.CAPABILITY
            elif insight.insight_type == "strategy_strength":
                goal_type = GoalType.KNOWLEDGE
            title = self._title_from_insight(insight)
            scope = _scope_from_evidence(insight.evidence_refs)
            fingerprint = _stable_goal_fingerprint(goal_type, title, insight.insight_type, scope)
            if fingerprint in seen:
                continue
            proposal = GoalProposal(
                goal_type=goal_type,
                title=title,
                motivation=insight.summary,
                source=f"meta_cognition:{insight.insight_type}",
                expected_value=min(1.0, max(0.2, insight.confidence)),
                risk_score=0.15 if goal_type == GoalType.KNOWLEDGE else 0.35,
                scope=scope,
                budget={"max_tasks": 2, "max_parallel_tasks": 1},
                evidence_refs=list(insight.evidence_refs),
                fingerprint=fingerprint,
                derived_from=[insight.insight_id],
            )
            seen.add(fingerprint)
            goals.append(proposal)
            new_goals.append(proposal)
            if len(new_goals) >= max_new:
                break

        if capability_graph is not None and len(new_goals) < max_new:
            for gap in capability_graph.gaps:
                fingerprint = _stable_goal_fingerprint(
                    GoalType.CAPABILITY,
                    f"Close capability gap: {gap}",
                    "capability_graph",
                    [gap],
                )
                if fingerprint in seen:
                    continue
                proposal = GoalProposal(
                    goal_type=GoalType.CAPABILITY,
                    title=f"Close capability gap: {gap}",
                    motivation=f"Capability graph marks `{gap}` as underpowered under repeated use.",
                    source="capability_graph",
                    expected_value=0.65,
                    risk_score=0.25,
                    scope=[gap],
                    budget={"max_tasks": 2, "max_parallel_tasks": 1},
                    evidence_refs=[],
                    fingerprint=fingerprint,
                    derived_from=[gap],
                )
                seen.add(fingerprint)
                goals.append(proposal)
                new_goals.append(proposal)
                if len(new_goals) >= max_new:
                    break

        if len(new_goals) < max_new:
            for improvement in improvements:
                if improvement.status != ImprovementStatus.PROPOSED:
                    continue
                fingerprint = _stable_goal_fingerprint(
                    GoalType.MAINTENANCE,
                    f"Validate improvement: {improvement.title}",
                    improvement.target_surface,
                    [improvement.target_surface],
                )
                if fingerprint in seen:
                    continue
                proposal = GoalProposal(
                    goal_type=GoalType.MAINTENANCE,
                    title=f"Validate improvement: {improvement.title}",
                    motivation=improvement.description,
                    source=f"self_improvement:{improvement.target_surface}",
                    expected_value=min(1.0, max(0.2, improvement.confidence)),
                    risk_score=0.3,
                    scope=[improvement.target_surface],
                    budget={"max_tasks": 2, "max_parallel_tasks": 1},
                    evidence_refs=list(improvement.evidence_refs),
                    fingerprint=fingerprint,
                    derived_from=list(improvement.experiment_ids),
                )
                seen.add(fingerprint)
                goals.append(proposal)
                new_goals.append(proposal)
                if len(new_goals) >= max_new:
                    break

        if new_goals:
            self._storage.save_goals(goals)
        return new_goals

    def approve_goal(self, goal_id: str, note: str = "") -> GoalProposal | None:
        goals = self._storage.load_goals()
        updated: GoalProposal | None = None
        for goal in goals:
            if goal.goal_id != goal_id:
                continue
            goal.status = GoalStatus.APPROVED
            goal.approval_note = str(note or "").strip()
            goal.updated_at = utc_now_iso()
            goal.pm_contract_outline = self._build_pm_contract(goal)
            updated = goal
            break
        if updated is not None:
            self._storage.save_goals(goals)
        return updated

    def reject_goal(self, goal_id: str, note: str = "") -> GoalProposal | None:
        goals = self._storage.load_goals()
        updated: GoalProposal | None = None
        for goal in goals:
            if goal.goal_id != goal_id:
                continue
            goal.status = GoalStatus.REJECTED
            goal.approval_note = str(note or "").strip()
            goal.updated_at = utc_now_iso()
            updated = goal
            break
        if updated is not None:
            self._storage.save_goals(goals)
        return updated

    def materialize_goal(self, goal_id: str) -> dict[str, Any] | None:
        goals = self._storage.load_goals()
        contract: dict[str, Any] | None = None
        for goal in goals:
            if goal.goal_id != goal_id:
                continue
            if goal.status not in {GoalStatus.APPROVED, GoalStatus.MATERIALIZED}:
                raise ValueError("goal must be approved before materialization")
            if not goal.pm_contract_outline:
                goal.pm_contract_outline = self._build_pm_contract(goal)
            goal.status = GoalStatus.MATERIALIZED
            goal.updated_at = utc_now_iso()
            contract = dict(goal.pm_contract_outline)
            break
        if contract is not None:
            self._storage.save_goals(goals)
        return contract

    def _title_from_insight(self, insight: MetaInsight) -> str:
        if insight.insight_type == "strategy_strength":
            return f"Codify successful strategy: {insight.strategy_tag or 'resident-default'}"
        if insight.insight_type == "prediction_gap":
            return f"Reduce prediction gap in {insight.strategy_tag or 'decision quality'}"
        if insight.insight_type == "failure_cluster":
            return f"Stabilize failure cluster: {insight.strategy_tag or 'workflow stage'}"
        return f"Harden strategy: {insight.strategy_tag or 'resident-default'}"

    def _build_pm_contract(self, goal: GoalProposal) -> dict[str, Any]:
        title_token = goal.title.replace("`", "").strip() or "Resident goal"
        scope_paths = list(goal.scope) or ["src/backend", "docs"]
        analyze_task_id = f"{goal.goal_id}-A"
        execute_task_id = f"{goal.goal_id}-B"
        return {
            "focus": "resident_goal_materialization",
            "overall_goal": title_token,
            "metadata": {
                "resident_goal_id": goal.goal_id,
                "resident_goal_type": goal.goal_type.value,
                "resident_source": goal.source,
            },
            "tasks": [
                {
                    "id": analyze_task_id,
                    "title": f"Analyze governed goal: {title_token}",
                    "goal": f"Assess the governed resident goal `{title_token}` and refine the execution contract before implementation.",
                    "assigned_to": "ChiefEngineer",
                    "scope_paths": scope_paths,
                    "target_files": [],
                    "phase": "analysis",
                    "execution_checklist": [
                        "Review the resident goal evidence refs and summarize the failure or opportunity.",
                        "Constrain the implementation scope and identify the minimal contract for execution.",
                        "Define the concrete verification path and expected evidence artifacts.",
                    ],
                    "acceptance_criteria": [
                        'Run `rg -n "resident_goal_id|resident_goal_type" docs src/backend` after updating artifacts and verify the new contract references are discoverable.',
                        "The resulting contract captures explicit scope, constraints, and evidence paths for downstream execution.",
                    ],
                    "metadata": {
                        "resident_goal_id": goal.goal_id,
                        "resident_goal_type": goal.goal_type.value,
                    },
                },
                {
                    "id": execute_task_id,
                    "title": f"Execute governed goal: {title_token}",
                    "goal": f"Implement the approved resident goal `{title_token}` within the bounded scope and verification budget.",
                    "assigned_to": "Director",
                    "scope_paths": scope_paths,
                    "target_files": scope_paths,
                    "phase": "implementation",
                    "depends_on": [analyze_task_id],
                    "execution_checklist": [
                        "Apply the scoped implementation implied by the approved resident goal.",
                        "Capture evidence artifacts for every changed code or document path.",
                        "Run the narrowest deterministic verification command and record the outcome.",
                    ],
                    "acceptance_criteria": [
                        "Run `python -m pytest -q` or the repo-specific verification command relevant to the changed scope and capture the output path.",
                        "The implementation links back to the resident goal id and preserves bounded scope without bypassing PM governance.",
                    ],
                    "metadata": {
                        "resident_goal_id": goal.goal_id,
                        "resident_goal_type": goal.goal_type.value,
                    },
                },
            ],
        }
