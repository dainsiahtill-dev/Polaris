"""Resident service orchestrating persistent agency subsystems."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict
from pathlib import Path
from threading import Lock
from typing import Any

from polaris.cells.audit.evidence.public.service import create_evidence_bundle_service
from polaris.cells.orchestration.pm_dispatch.public.service import OrchestrationCommandService
from polaris.cells.resident.autonomy.internal.capability_graph import CapabilityGraph
from polaris.cells.resident.autonomy.internal.counterfactual_lab import CounterfactualLab
from polaris.cells.resident.autonomy.internal.decision_trace import DecisionTraceRecorder

# Phase 1.2: Goal Execution Projection
from polaris.cells.resident.autonomy.internal.execution_projection import (
    get_execution_projection_service,
)
from polaris.cells.resident.autonomy.internal.goal_governor import GoalGovernor
from polaris.cells.resident.autonomy.internal.meta_cognition import StrategyInsightEngine
from polaris.cells.resident.autonomy.internal.pm_bridge import ResidentPMBridge
from polaris.cells.resident.autonomy.internal.resident_storage import ResidentStorage
from polaris.cells.resident.autonomy.internal.self_improvement_lab import SelfImprovementLab
from polaris.cells.resident.autonomy.internal.skill_foundry import SkillFoundry

# Phase 1.1: EvidenceBundle integration
from polaris.domain.entities.evidence_bundle import SourceType
from polaris.domain.models.resident import (
    DecisionRecord,
    DecisionVerdict,
    ExperimentRecord,
    GoalProposal,
    ImprovementStatus,
    ResidentAgenda,
    ResidentIdentity,
    ResidentMode,
    ResidentRuntimeState,
    SkillArtifact,
    utc_now_iso,
)


class _ResidentServiceCache:
    """Thread-safe LRU cache for ResidentService instances.

    Replaces the previous 3-global-dict + global-counter approach with a single
    cohesive class.  A single lock guards all mutations, eliminating the
    nested-lock ordering hazard of the prior implementation.
    """

    def __init__(self, max_size: int = 100) -> None:
        self._max_size = max_size
        self._services: dict[str, ResidentService] = {}
        self._access: dict[str, int] = {}
        self._counter: int = 0
        self._lock = Lock()

    def get_or_create(self, workspace: str) -> ResidentService:
        """Return cached service or create a new one, evicting LRU if needed."""
        with self._lock:
            service = self._services.get(workspace)
            if service is None:
                if len(self._services) >= self._max_size and self._access:
                    oldest = min(self._access, key=lambda k: self._access[k])
                    self._services.pop(oldest, None)
                    self._access.pop(oldest, None)
                service = ResidentService(workspace)
                self._services[workspace] = service
            self._counter += 1
            self._access[workspace] = self._counter
            return service

    def clear(self) -> None:
        with self._lock:
            self._services.clear()
            self._access.clear()
            self._counter = 0


_RESIDENT_CACHE = _ResidentServiceCache(max_size=100)


def _normalize_workspace(workspace: str) -> str:
    return str(Path(workspace or ".").expanduser().resolve())


def _coerce_mode(value: ResidentMode | str | None, default: ResidentMode) -> ResidentMode:
    if isinstance(value, ResidentMode):
        return value
    token = str(value or "").strip().lower()
    if not token:
        return default
    try:
        return ResidentMode(token)
    except (RuntimeError, ValueError):
        return default


class ResidentService:
    """Long-lived resident engineer service with explicit persistence boundaries."""

    def __init__(self, workspace: str, *, auto_bundle_enabled: bool = True) -> None:
        self.workspace = _normalize_workspace(workspace)
        self.storage = ResidentStorage(self.workspace)
        self.recorder = DecisionTraceRecorder(self.storage)
        self.meta_cognition = StrategyInsightEngine(self.storage)
        self.capability_graph = CapabilityGraph(self.storage)
        self.goal_governor = GoalGovernor(self.storage)
        self.counterfactual_lab = CounterfactualLab(self.storage)
        self.skill_foundry = SkillFoundry(self.storage)
        self.self_improvement_lab = SelfImprovementLab(self.storage)
        self._evidence_service = create_evidence_bundle_service()
        self._execution_projection_service = get_execution_projection_service()
        self._auto_bundle_enabled = auto_bundle_enabled
        self._lock = Lock()
        self.identity = self.storage.load_identity() or ResidentIdentity(
            active_workspace=self.workspace,
        )
        self.identity.active_workspace = self.workspace
        self.agenda = self.storage.load_agenda() or ResidentAgenda()
        self.runtime_state = self.storage.load_runtime_state() or ResidentRuntimeState(
            active=False,
            mode=self.identity.operating_mode,
        )
        self.runtime_state.mode = self.identity.operating_mode
        self._persist_core_state()

    def recover(self) -> dict[str, Any]:
        with self._lock:
            self.identity = self.storage.load_identity() or self.identity
            self.identity.active_workspace = self.workspace
            self.agenda = self.storage.load_agenda() or self.agenda
            self.runtime_state = self.storage.load_runtime_state() or self.runtime_state
            self.runtime_state.mode = self.identity.operating_mode
            self._persist_core_state()
            return self.get_status(include_details=True)

    def start(self, mode: ResidentMode | str | None = None) -> dict[str, Any]:
        with self._lock:
            selected_mode = _coerce_mode(mode, self.identity.operating_mode or ResidentMode.OBSERVE)
            now = utc_now_iso()
            self.identity.operating_mode = selected_mode
            self.identity.active_workspace = self.workspace
            self.identity.updated_at = now
            self.runtime_state.active = True
            self.runtime_state.mode = selected_mode
            self.runtime_state.updated_at = now
            self.runtime_state.last_error = ""
            self._persist_core_state()
            return self.get_status(include_details=True)

    def stop(self) -> dict[str, Any]:
        with self._lock:
            now = utc_now_iso()
            self.runtime_state.active = False
            self.runtime_state.updated_at = now
            self.identity.updated_at = now
            self._persist_core_state()
            return self.get_status(include_details=True)

    def set_mode(self, mode: ResidentMode | str) -> dict[str, Any]:
        with self._lock:
            selected_mode = _coerce_mode(mode, self.identity.operating_mode)
            now = utc_now_iso()
            self.identity.operating_mode = selected_mode
            self.identity.updated_at = now
            self.runtime_state.mode = selected_mode
            self.runtime_state.updated_at = now
            self._persist_core_state()
            return self.get_status(include_details=True)

    def update_identity(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            now = utc_now_iso()
            name = str(payload.get("name") or "").strip()
            mission = str(payload.get("mission") or "").strip()
            owner = str(payload.get("owner") or "").strip()
            if name:
                self.identity.name = name
            if mission:
                self.identity.mission = mission
            if owner:
                self.identity.owner = owner
            if isinstance(payload.get("values"), list):
                self.identity.values = [str(item).strip() for item in payload.get("values") or [] if str(item).strip()]
            if isinstance(payload.get("memory_lineage"), list):
                self.identity.memory_lineage = [
                    str(item).strip() for item in payload.get("memory_lineage") or [] if str(item).strip()
                ]
            if isinstance(payload.get("capability_profile"), Mapping):
                capability_profile: dict[str, float] = {}
                for key, value in payload.get("capability_profile", {}).items():
                    name = str(key).strip()
                    if not name:
                        continue
                    try:
                        capability_profile[name] = float(value)
                    except (RuntimeError, ValueError):
                        capability_profile[name] = 0.0
                self.identity.capability_profile = capability_profile
            self.identity.operating_mode = _coerce_mode(
                payload.get("operating_mode"),
                self.identity.operating_mode,
            )
            self.identity.active_workspace = self.workspace
            self.identity.updated_at = now
            self.runtime_state.mode = self.identity.operating_mode
            self.runtime_state.updated_at = now
            self._persist_core_state()
            return self.identity.to_dict()

    def record_decision(self, payload: DecisionRecord | Mapping[str, Any]) -> DecisionRecord:
        with self._lock:
            return self._record_locked_decision(payload)

    def list_decisions(self, *, limit: int = 100, actor: str = "", verdict: str = "") -> list[DecisionRecord]:
        return self.recorder.list_recent(limit=limit, actor=actor, verdict=verdict)

    def get_decision_evidence_bundle(self, decision_id: str) -> dict[str, Any] | None:
        """获取决策关联的证据包详情。

        Phase 1.1: Decision traceability - retrieve EvidenceBundle for a decision.
        """
        # Find the decision
        decisions = self.storage.load_decisions(limit=10000)
        decision = None
        for d in decisions:
            if d.decision_id == decision_id:
                decision = d
                break

        if not decision or not decision.evidence_bundle_id:
            return None

        bundle = self._evidence_service.get_bundle(self.workspace, decision.evidence_bundle_id)
        if not bundle:
            return None

        return {
            "decision_id": decision_id,
            "evidence_bundle_id": decision.evidence_bundle_id,
            "bundle": bundle.to_dict(),
        }

    def list_goals(self, *, status: str = "") -> list[GoalProposal]:
        return self.goal_governor.list_goals(status=status)

    def create_goal_proposal(self, payload: Mapping[str, Any]) -> GoalProposal:
        with self._lock:
            goal = self.goal_governor.create_manual_proposal(payload)
            self._refresh_agenda_view()
            return goal

    def approve_goal(self, goal_id: str, note: str = "") -> GoalProposal | None:
        with self._lock:
            goal = self.goal_governor.approve_goal(goal_id, note=note)
            self._refresh_agenda_view()
            return goal

    def reject_goal(self, goal_id: str, note: str = "") -> GoalProposal | None:
        with self._lock:
            goal = self.goal_governor.reject_goal(goal_id, note=note)
            self._refresh_agenda_view()
            return goal

    def materialize_goal(self, goal_id: str) -> dict[str, Any] | None:
        with self._lock:
            contract = self.goal_governor.materialize_goal(goal_id)
            self._refresh_agenda_view()
            return contract

    def stage_goal(
        self,
        goal_id: str,
        *,
        promote_to_pm_runtime: bool = False,
        ramdisk_root: str = "",
    ) -> dict[str, Any] | None:
        with self._lock:
            staged = self._stage_goal_locked(
                goal_id,
                promote_to_pm_runtime=promote_to_pm_runtime,
                ramdisk_root=ramdisk_root,
            )
            self._refresh_agenda_view()
            return staged

    async def run_goal(
        self,
        goal_id: str,
        *,
        settings: Any | None = None,
        run_type: str = "pm",
        run_director: bool = False,
        director_iterations: int = 1,
    ) -> dict[str, Any] | None:
        settings_obj = settings
        ramdisk_root = str(getattr(settings_obj, "ramdisk_root", "") or "").strip()

        with self._lock:
            staged = self._stage_goal_locked(
                goal_id,
                promote_to_pm_runtime=True,
                ramdisk_root=ramdisk_root,
            )
            if staged is None:
                return None

        command_service = OrchestrationCommandService(settings_obj)
        pm_run = await command_service.execute_pm_run(
            workspace=self.workspace,
            run_type=run_type,
            options={
                "directive": str(staged["pm_run"]["directive"]),
                "run_director": bool(run_director),
                "director_iterations": max(1, int(director_iterations or 1)),
                "metadata": dict(staged["pm_run"]["metadata"]),
            },
        )

        with self._lock:
            goals = self.storage.load_goals()
            goal = self._find_goal(goals, goal_id)
            if goal is None:
                return None
            goal.materialization_artifacts = dict(goal.materialization_artifacts)
            goal.materialization_artifacts["pm_run"] = asdict(pm_run)
            goal.updated_at = utc_now_iso()
            self.storage.save_goals(goals)
            self._record_locked_decision(
                {
                    "workspace": self.workspace,
                    "actor": "resident",
                    "stage": "goal_pm_run",
                    "goal_id": goal.goal_id,
                    "summary": f"Triggered governed PM run for resident goal `{goal.title or goal.goal_id}`.",
                    "strategy_tags": ["goal_governance", "pm_bridge"],
                    "expected_outcome": {
                        "promoted_to_pm_runtime": True,
                        "run_director": bool(run_director),
                    },
                    "actual_outcome": {
                        "run_id": pm_run.run_id,
                        "status": pm_run.status,
                        "message": pm_run.message,
                    },
                    "verdict": (
                        DecisionVerdict.SUCCESS.value
                        if str(pm_run.status or "").strip().lower() not in {"failed", "error"}
                        else DecisionVerdict.FAILURE.value
                    ),
                    "evidence_refs": [
                        str(goal.materialization_artifacts.get("pm_contract_path") or ""),
                        str(goal.materialization_artifacts.get("pm_plan_path") or ""),
                    ],
                    "confidence": 0.8,
                },
                source_type=SourceType.DIRECTOR_RUN,
                source_run_id=pm_run.run_id,
                source_goal_id=goal.goal_id,
            )
            self._refresh_agenda_view()
            return {
                "goal": goal.to_dict(),
                "staging": staged,
                "pm_run": asdict(pm_run),
            }

    def list_skills(self) -> list[SkillArtifact]:
        return self.storage.load_skills()

    def list_experiments(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.storage.load_experiments()]

    def list_improvements(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.storage.load_improvements()]

    def run_meta_cognition(self, decisions: Iterable[DecisionRecord] | None = None) -> dict[str, Any]:
        records = list(decisions) if decisions is not None else self.storage.load_decisions(limit=1000)
        return self.meta_cognition.refresh(records)

    def run_skill_foundry(self, decisions: Iterable[DecisionRecord] | None = None) -> list[SkillArtifact]:
        records = list(decisions) if decisions is not None else self.storage.load_decisions(limit=1000)
        return self.skill_foundry.extract(records)

    def rebuild_capabilities(
        self,
        decisions: Iterable[DecisionRecord] | None = None,
        skills: Iterable[SkillArtifact] | None = None,
    ) -> dict[str, Any]:
        records = list(decisions) if decisions is not None else self.storage.load_decisions(limit=1000)
        skill_list = list(skills) if skills is not None else self.storage.load_skills()
        graph = self.capability_graph.rebuild(records, skill_list)
        return graph.to_dict()

    def run_counterfactual_lab(self, decisions: Iterable[DecisionRecord] | None = None) -> list[dict[str, Any]]:
        records = list(decisions) if decisions is not None else self.storage.load_decisions(limit=1000)
        return [item.to_dict() for item in self.counterfactual_lab.replay(records)]

    def run_self_improvement_lab(self, experiments: Iterable[Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
        if experiments is None:
            experiment_models = self.storage.load_experiments()
        else:
            experiment_models = [
                item if isinstance(item, ExperimentRecord) else ExperimentRecord.from_dict(item) for item in experiments
            ]
        return [item.to_dict() for item in self.self_improvement_lab.propose(experiment_models)]

    def tick(self, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            if not force and not self.runtime_state.active:
                return self.get_status(include_details=True)
            try:
                decisions = self.storage.load_decisions(limit=1000)
                meta_state = self.meta_cognition.refresh(decisions)
                skills = self.skill_foundry.extract(decisions)
                capability_graph = self.capability_graph.rebuild(decisions, skills)
                experiments = self.counterfactual_lab.replay(decisions)
                improvements = self.self_improvement_lab.propose(experiments)
                insights = self.storage.load_insights()
                self.goal_governor.generate(
                    insights=insights,
                    capability_graph=capability_graph,
                    improvements=improvements,
                )
                self._refresh_identity_capabilities(capability_graph.to_dict())
                now = utc_now_iso()
                self.runtime_state.last_tick_at = now
                self.runtime_state.tick_count += 1
                self.runtime_state.last_error = ""
                self.runtime_state.last_summary = {
                    "decision_count": len(decisions),
                    "insight_count": len(meta_state.get("insights") or []),
                    "skill_count": len(skills),
                    "capability_count": len(capability_graph.capabilities),
                    "experiment_count": len(experiments),
                    "improvement_count": len(improvements),
                    "goal_count": len(self.storage.load_goals()),
                }
                self.runtime_state.updated_at = now
                self.storage.save_runtime_state(self.runtime_state)
                self.storage.append_tick_history(
                    {
                        "timestamp": now,
                        "summary": dict(self.runtime_state.last_summary),
                        "mode": self.runtime_state.mode.value,
                        "active": self.runtime_state.active,
                    }
                )
                self._refresh_agenda_view()
            except (RuntimeError, ValueError) as exc:
                self.runtime_state.last_error = str(exc).strip()
                self.runtime_state.updated_at = utc_now_iso()
                self.storage.save_runtime_state(self.runtime_state)
                raise
            return self.get_status(include_details=True)

    def get_status(self, *, include_details: bool = False) -> dict[str, Any]:
        goals = self.storage.load_goals()
        payload: dict[str, Any] = {
            "workspace": self.workspace,
            "identity": self.identity.to_dict(),
            "runtime": self.runtime_state.to_dict(),
            "agenda": self.agenda.to_dict(),
            "counts": {
                "decisions": len(self.storage.load_decisions(limit=1000)),
                "goals": len(goals),
                "skills": len(self.storage.load_skills()),
                "experiments": len(self.storage.load_experiments()),
                "improvements": len(self.storage.load_improvements()),
            },
        }
        if include_details:
            payload["decisions"] = [item.to_dict() for item in self.storage.load_decisions(limit=200)]
            payload["goals"] = [item.to_dict() for item in goals]
            payload["insights"] = [item.to_dict() for item in self.storage.load_insights()]
            payload["skills"] = [item.to_dict() for item in self.storage.load_skills()]
            payload["experiments"] = [item.to_dict() for item in self.storage.load_experiments()]
            payload["improvements"] = [item.to_dict() for item in self.storage.load_improvements()]
            graph = self.storage.load_capability_graph()
            payload["capability_graph"] = (
                graph.to_dict() if graph else {"generated_at": "", "capabilities": [], "gaps": []}
            )
        return payload

    def _persist_core_state(self) -> None:
        self.storage.save_identity(self.identity)
        self.storage.save_agenda(self.agenda)
        self.storage.save_runtime_state(self.runtime_state)

    def _record_locked_decision(
        self,
        payload: DecisionRecord | Mapping[str, Any],
        *,
        source_type: SourceType = SourceType.MANUAL,
        source_run_id: str | None = None,
        source_task_id: str | None = None,
        source_goal_id: str | None = None,
        auto_bundle: bool | None = None,
    ) -> DecisionRecord:
        record = payload if isinstance(payload, DecisionRecord) else DecisionRecord.from_dict(payload)
        if not record.actor:
            raise ValueError("decision actor is required")
        if not record.stage:
            raise ValueError("decision stage is required")
        if not record.workspace:
            record.workspace = self.workspace
        if not record.summary:
            record.summary = f"{record.actor}::{record.stage}"
        if not record.selected_option_id and len(record.options) == 1:
            record.selected_option_id = record.options[0].option_id
        if not record.strategy_tags and record.selected_option_id:
            for option in record.options:
                if option.option_id == record.selected_option_id:
                    record.strategy_tags = [tag for tag in option.strategy_tags if str(tag).strip()]
                    break

        # Phase 1.1: Auto-create EvidenceBundle if enabled
        should_bundle = auto_bundle if auto_bundle is not None else self._auto_bundle_enabled
        if should_bundle and not record.evidence_bundle_id:
            try:
                bundle = self._evidence_service.create_from_working_tree(
                    workspace=self.workspace,
                    base_sha=self._evidence_service._get_current_commit(self.workspace),
                    source_type=source_type,
                    source_run_id=source_run_id or record.decision_id,  # Use decision_id as fallback
                    source_task_id=source_task_id,
                    source_goal_id=source_goal_id or record.goal_id,
                )
                record.evidence_bundle_id = bundle.bundle_id
                record.affected_files = bundle.affected_files
                # Collect affected symbols from all changes
                symbols = set()
                for change in bundle.change_set:
                    symbols.update(change.related_symbols)
                record.affected_symbols = list(symbols)
            except (RuntimeError, ValueError) as e:
                # EvidenceBundle creation failure is a data-integrity issue.
                # We log at ERROR (not WARNING) so it surfaces in alerting pipelines,
                # but we do NOT raise so that the decision itself can still be recorded.
                # Callers that require evidence completeness should check
                # ``record.evidence_bundle_id`` after recording.
                import logging

                logging.getLogger(__name__).error(
                    "Failed to create EvidenceBundle for decision %s: %s",
                    record.decision_id,
                    e,
                    exc_info=True,
                )

        recorded = self.recorder.record(record)
        self.runtime_state.last_summary = {
            "decision_id": recorded.decision_id,
            "actor": recorded.actor,
            "stage": recorded.stage,
            "verdict": recorded.verdict.value,
            "timestamp": recorded.timestamp,
            "evidence_bundle_id": recorded.evidence_bundle_id,
        }
        self.runtime_state.updated_at = utc_now_iso()
        self.storage.save_runtime_state(self.runtime_state)
        return recorded

    def _stage_goal_locked(
        self,
        goal_id: str,
        *,
        promote_to_pm_runtime: bool,
        ramdisk_root: str,
    ) -> dict[str, Any] | None:
        contract = self.goal_governor.materialize_goal(goal_id)
        if contract is None:
            return None

        goals = self.storage.load_goals()
        goal = self._find_goal(goals, goal_id)
        if goal is None:
            return None

        bridge = ResidentPMBridge(self.storage, self.workspace, ramdisk_root=ramdisk_root)
        staged = bridge.stage_goal(
            goal,
            contract,
            promote_to_pm_runtime=promote_to_pm_runtime,
        )
        goal.materialization_artifacts = dict(staged.get("artifacts") or {})
        goal.materialization_artifacts["staged_at"] = str(staged.get("staged_at") or "")
        goal.materialization_artifacts["promoted_to_pm_runtime"] = bool(staged.get("promoted_to_pm_runtime"))
        goal.materialization_artifacts["pm_run"] = dict(staged.get("pm_run") or {})
        if isinstance(staged.get("promotion"), Mapping):
            goal.materialization_artifacts["promotion"] = dict(staged["promotion"])
        goal.updated_at = utc_now_iso()
        self.storage.save_goals(goals)
        self._record_locked_decision(
            {
                "workspace": self.workspace,
                "actor": "resident",
                "stage": "goal_staging",
                "goal_id": goal.goal_id,
                "summary": f"Staged governed resident goal `{goal.title or goal.goal_id}`.",
                "strategy_tags": ["goal_governance", "pm_bridge"],
                "expected_outcome": {
                    "promoted_to_pm_runtime": promote_to_pm_runtime,
                    "goal_status": goal.status.value,
                },
                "actual_outcome": {
                    "resident_contract_path": goal.materialization_artifacts.get("resident_contract_path"),
                    "resident_plan_path": goal.materialization_artifacts.get("resident_plan_path"),
                    "pm_contract_path": goal.materialization_artifacts.get("pm_contract_path"),
                    "pm_plan_path": goal.materialization_artifacts.get("pm_plan_path"),
                },
                "verdict": DecisionVerdict.SUCCESS.value,
                "evidence_refs": [
                    str(goal.materialization_artifacts.get("resident_contract_path") or ""),
                    str(goal.materialization_artifacts.get("resident_plan_path") or ""),
                    str(goal.materialization_artifacts.get("pm_contract_path") or ""),
                    str(goal.materialization_artifacts.get("pm_plan_path") or ""),
                ],
                "confidence": 0.85,
            },
            source_type=SourceType.MANUAL,
            source_goal_id=goal.goal_id,
        )
        return {
            "goal": goal.to_dict(),
            **staged,
        }

    @staticmethod
    def _find_goal(goals: list[GoalProposal], goal_id: str) -> GoalProposal | None:
        for goal in goals:
            if goal.goal_id == goal_id:
                return goal
        return None

    def _refresh_identity_capabilities(self, graph_payload: Mapping[str, Any]) -> None:
        capabilities = graph_payload.get("capabilities") if isinstance(graph_payload, Mapping) else []
        profile: dict[str, float] = {}
        if isinstance(capabilities, list):
            for item in capabilities[:12]:
                if not isinstance(item, Mapping):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                try:
                    profile[name] = float(item.get("score") or 0.0)
                except (RuntimeError, ValueError):
                    profile[name] = 0.0
        self.identity.capability_profile = profile
        self.identity.active_workspace = self.workspace
        self.identity.updated_at = utc_now_iso()
        self.storage.save_identity(self.identity)

    def _refresh_agenda_view(self) -> None:
        goals = self.storage.load_goals()
        pending = [goal for goal in goals if goal.status.value == "pending"]
        approved = [goal for goal in goals if goal.status.value == "approved"]
        materialized = [goal for goal in goals if goal.status.value == "materialized"]
        experiments = self.storage.load_experiments()
        improvements = self.storage.load_improvements()
        insights = self.storage.load_insights()
        graph = self.storage.load_capability_graph()

        current_focus = [goal.title for goal in approved[:3]]
        if not current_focus:
            current_focus = [goal.title for goal in pending[:3]]
        if not current_focus and graph is not None:
            current_focus = list(graph.gaps[:3])

        risk_register = [
            insight.summary for insight in insights if insight.insight_type in {"strategy_risk", "failure_cluster"}
        ][:6]
        next_actions: list[str] = []
        if pending:
            next_actions.append(f"Review {len(pending)} pending governed goal proposals.")
        if experiments:
            next_actions.append(f"Shadow-test {len(experiments)} counterfactual experiment candidates.")
        proposed_improvements = [item for item in improvements if item.status == ImprovementStatus.PROPOSED]
        if proposed_improvements:
            next_actions.append(f"Evaluate {len(proposed_improvements)} self-improvement proposals in shadow runtime.")

        self.agenda.current_focus = current_focus
        self.agenda.pending_goal_ids = [goal.goal_id for goal in pending]
        self.agenda.approved_goal_ids = [goal.goal_id for goal in approved]
        self.agenda.materialized_goal_ids = [goal.goal_id for goal in materialized]
        self.agenda.risk_register = risk_register
        self.agenda.next_actions = next_actions
        self.agenda.active_experiment_ids = [item.experiment_id for item in experiments[:10]]
        self.agenda.active_improvement_ids = [item.improvement_id for item in proposed_improvements[:10]]
        self.agenda.last_tick_at = self.runtime_state.last_tick_at
        self.agenda.tick_count = self.runtime_state.tick_count
        self.agenda.updated_at = utc_now_iso()
        self.storage.save_agenda(self.agenda)

    # Phase 1.2: Goal Execution Projection
    def get_goal_execution_view(
        self, goal_id: str, task_progress: list[dict[str, Any]] | None = None
    ) -> dict[str, Any] | None:
        """获取目标执行投影视图。

        Args:
            goal_id: 目标ID
            task_progress: 任务进度列表（可选，不提供则尝试从 materialization_artifacts 获取）

        Returns:
            GoalExecutionView 的字典形式，或 None（目标不存在）
        """
        # 查找目标
        goals = self.storage.load_goals()
        goal = self._find_goal(goals, goal_id)
        if goal is None:
            return None

        # 如果没有提供任务进度，尝试从 materialization_artifacts 获取
        if task_progress is None:
            artifacts = goal.materialization_artifacts or {}
            # 尝试从 PM run 结果获取任务列表
            pm_run = artifacts.get("pm_run", {})
            if pm_run:
                # 从 pm_run 中提取任务信息
                task_progress = self._extract_tasks_from_pm_run(pm_run)
            else:
                # 从 goal contract 获取
                contract = artifacts.get("pm_contract_outline", {})
                task_progress = self._extract_tasks_from_contract(contract)

        # 获取开始时间
        started_at = None
        if goal.materialization_artifacts:
            started_at = goal.materialization_artifacts.get("staged_at")

        # 构建执行投影
        view = self._execution_projection_service.build_projection(
            goal_id=goal_id,
            task_progress=task_progress or [],
            started_at=started_at,
        )

        return view.to_dict()

    def list_goal_executions(self) -> list[dict[str, Any]]:
        """列出所有目标的执行投影（仅 approved 和 materialized 状态）。"""
        goals = self.storage.load_goals()
        active_goals = [g for g in goals if g.status.value in ("approved", "materialized")]

        results = []
        for goal in active_goals:
            view = self.get_goal_execution_view(goal.goal_id)
            if view:
                results.append(view)

        return results

    def update_goal_execution_progress(
        self,
        goal_id: str,
        task_progress: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """更新目标执行进度（由 Director 调用）。

        Args:
            goal_id: 目标ID
            task_progress: 更新的任务进度列表

        Returns:
            更新后的执行投影
        """
        # 构建新投影
        view = self.get_goal_execution_view(goal_id, task_progress)
        if view is None:
            return None

        # 更新缓存
        goal = None
        for g in self.storage.load_goals():
            if g.goal_id == goal_id:
                goal = g
                break

        if goal:
            # 保存执行进度到 materialization_artifacts
            if goal.materialization_artifacts is None:
                goal.materialization_artifacts = {}
            goal.materialization_artifacts["execution_progress"] = task_progress
            goal.materialization_artifacts["execution_updated_at"] = utc_now_iso()

            # 保存目标
            goals = self.storage.load_goals()
            for i, g in enumerate(goals):
                if g.goal_id == goal_id:
                    goals[i] = goal
                    break
            self.storage.save_goals(goals)

        return view

    def _extract_tasks_from_pm_run(self, pm_run: dict[str, Any]) -> list[dict[str, Any]]:
        """从 PM run 结果提取任务列表。"""
        tasks = []
        # 尝试从 run 的 iterations 中提取
        iterations = pm_run.get("iterations", [])
        for iteration in iterations:
            director_result = iteration.get("director_result", {})
            director_tasks = director_result.get("tasks", [])
            for task in director_tasks:
                tasks.append(
                    {
                        "task_id": task.get("task_id", ""),
                        "subject": task.get("subject", ""),
                        "status": self._map_director_status(task.get("status", "")),
                        "progress_percent": 1.0 if task.get("status") == "completed" else 0.0,
                    }
                )
        return tasks

    def _extract_tasks_from_contract(self, contract: dict[str, Any]) -> list[dict[str, Any]]:
        """从 contract 提取任务列表。"""
        tasks = []
        phases = contract.get("phases", [])
        for phase in phases:
            phase_tasks = phase.get("tasks", [])
            for task in phase_tasks:
                tasks.append(
                    {
                        "task_id": task.get("task_id", ""),
                        "subject": task.get("description", ""),
                        "status": "pending",
                        "progress_percent": 0.0,
                    }
                )
        return tasks

    def _map_director_status(self, status: str) -> str:
        """映射 Director 任务状态到执行投影状态。"""
        mapping = {
            "pending": "pending",
            "in_progress": "in_progress",
            "running": "in_progress",
            "completed": "completed",
            "done": "completed",
            "failed": "failed",
            "error": "failed",
            "blocked": "blocked",
        }
        return mapping.get(status.lower(), "pending")


def get_resident_service(workspace: str) -> ResidentService:
    """Get or create a ResidentService for the given workspace (LRU-cached)."""
    return _RESIDENT_CACHE.get_or_create(_normalize_workspace(workspace))


def reset_resident_services() -> None:
    _RESIDENT_CACHE.clear()


def record_resident_decision(workspace: str, payload: DecisionRecord | Mapping[str, Any]) -> dict[str, Any]:
    service = get_resident_service(workspace)
    return service.record_decision(payload).to_dict()
