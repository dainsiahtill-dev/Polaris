"""Chief Engineer role agent implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Any

from polaris.cells.chief_engineer.blueprint.internal.blueprint_persistence import (
    BlueprintPersistence,
)
from polaris.cells.roles.runtime.public.service import (
    AgentMessage,
    MessageType,
    RoleAgent,
)


@dataclass
class ConstructionBlueprint:
    """In-memory construction blueprint."""

    blueprint_id: str
    task_id: str
    title: str
    doc_id: str = ""
    modules: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    methods: list[dict[str, Any]] = field(default_factory=list)
    dependencies: dict[str, list[str]] = field(default_factory=dict)
    scope_for_apply: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    flexible_zone: dict[str, Any] = field(default_factory=dict)
    escalation_triggers: list[str] = field(default_factory=list)
    status: str = "approved"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "blueprint_id": self.blueprint_id,
            "task_id": self.task_id,
            "title": self.title,
            "doc_id": self.doc_id,
            "modules": list(self.modules),
            "files": list(self.files),
            "methods": list(self.methods),
            "dependencies": {k: list(v) for k, v in self.dependencies.items()},
            "scope_for_apply": list(self.scope_for_apply),
            "constraints": dict(self.constraints),
            "flexible_zone": dict(self.flexible_zone),
            "escalation_triggers": list(self.escalation_triggers),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ConstructionStore:
    """Thread-safe construction store with optional disk persistence."""

    def __init__(self, persistence: BlueprintPersistence | None = None) -> None:
        """Initialize the store.

        Args:
            persistence: Optional disk persistence layer. When provided,
                writes are mirrored to disk and reads fall back to disk on cache miss.
        """
        self._lock = RLock()
        self._by_id: dict[str, ConstructionBlueprint] = {}
        self._persistence = persistence

    def _from_dict(self, data: dict[str, Any]) -> ConstructionBlueprint:
        """Deserialize a blueprint dictionary into a ConstructionBlueprint."""
        return ConstructionBlueprint(
            blueprint_id=str(data.get("blueprint_id") or "").strip(),
            task_id=str(data.get("task_id") or "").strip(),
            title=str(data.get("title") or "Construction Plan").strip() or "Construction Plan",
            doc_id=str(data.get("doc_id") or "").strip(),
            modules=[dict(item) for item in list(data.get("modules", [])) if isinstance(item, dict)],
            files=[dict(item) for item in list(data.get("files", [])) if isinstance(item, dict)],
            methods=[dict(item) for item in list(data.get("methods", [])) if isinstance(item, dict)],
            dependencies={str(k): list(v) for k, v in dict(data.get("dependencies", {})).items()},
            scope_for_apply=[str(item).strip() for item in list(data.get("scope_for_apply", [])) if str(item).strip()],
            constraints=dict(data.get("constraints", {})),
            flexible_zone=dict(data.get("flexible_zone", {})),
            escalation_triggers=[
                str(item).strip() for item in list(data.get("escalation_triggers", [])) if str(item).strip()
            ],
            status=str(data.get("status") or "draft").strip() or "draft",
            created_at=str(data.get("created_at") or datetime.now().isoformat()).strip(),
            updated_at=str(data.get("updated_at") or datetime.now().isoformat()).strip(),
        )

    def save(self, blueprint: ConstructionBlueprint) -> None:
        with self._lock:
            blueprint.updated_at = datetime.now().isoformat()
            self._by_id[blueprint.blueprint_id] = blueprint
            if self._persistence is not None:
                self._persistence.save(blueprint.blueprint_id, blueprint.to_dict())

    def get(self, blueprint_id: str) -> ConstructionBlueprint | None:
        token = str(blueprint_id or "").strip()
        with self._lock:
            cached = self._by_id.get(token)
            if cached is not None:
                return cached
            if self._persistence is not None:
                data = self._persistence.load(token)
                if data is not None:
                    blueprint = self._from_dict(data)
                    self._by_id[token] = blueprint
                    return blueprint
            return None

    def list_all(self) -> list[ConstructionBlueprint]:
        with self._lock:
            # Ensure cache is hydrated from disk
            if self._persistence is not None:
                for blueprint_id in self._persistence.list_all():
                    if blueprint_id not in self._by_id:
                        data = self._persistence.load(blueprint_id)
                        if data is not None:
                            self._by_id[blueprint_id] = self._from_dict(data)
            rows = list(self._by_id.values())
        return sorted(rows, key=lambda item: item.updated_at, reverse=True)

    def list_by_task(self, task_id: str) -> list[ConstructionBlueprint]:
        token = str(task_id or "").strip()
        with self._lock:
            # Hydrate from disk if persistence is enabled
            if self._persistence is not None:
                for blueprint_id in self._persistence.list_all():
                    if blueprint_id not in self._by_id:
                        data = self._persistence.load(blueprint_id)
                        if data is not None:
                            self._by_id[blueprint_id] = self._from_dict(data)
            rows = [item for item in self._by_id.values() if item.task_id == token]
        return sorted(rows, key=lambda item: item.updated_at, reverse=True)


class ChiefEngineerAgent(RoleAgent):
    """Chief Engineer role agent (`工部尚书`).

    Capabilities:
    - impact_analysis: Analyzes change impact across files/modules
    - technical_tradeoffs: Structured evaluation of technical decisions
    - adr_auto_generation: Automatic ADR record creation from blueprint changes
    - constraint_propagation: Feeds constraints back to Director for execution
    """

    def __init__(self, workspace: str) -> None:
        super().__init__(workspace=workspace, agent_name="ChiefEngineer")
        persistence = BlueprintPersistence(workspace=workspace)
        self._store = ConstructionStore(persistence=persistence)
        self._adr_store = None  # Lazy initialization
        self._impact_cache: dict[str, dict[str, Any]] = {}  # blueprint_id -> impact analysis

    def setup_toolbox(self) -> None:
        tb = self.toolbox
        tb.register(
            "create_construction_plan",
            self._tool_create_construction_plan,
            description="Create construction plan",
            parameters={"task_id": "Task id", "title": "Title", "modules": "Module entries"},
        )
        tb.register(
            "update_scope_for_apply",
            self._tool_update_scope_for_apply,
            description="Update scope list",
            parameters={"blueprint_id": "Blueprint id", "scope_additions": "List", "scope_removals": "List"},
        )
        tb.register(
            "set_constraints",
            self._tool_set_constraints,
            description="Set execution constraints",
            parameters={"blueprint_id": "Blueprint id", "constraints": "Constraint object"},
        )
        tb.register(
            "set_flexible_zone",
            self._tool_set_flexible_zone,
            description="Set flexible zone object",
            parameters={"blueprint_id": "Blueprint id", "flexible_zone": "Object"},
        )
        tb.register(
            "set_escalation_triggers",
            self._tool_set_escalation_triggers,
            description="Set escalation trigger list",
            parameters={"blueprint_id": "Blueprint id", "triggers": "List"},
        )
        tb.register(
            "get_construction_plan",
            self._tool_get_construction_plan,
            description="Get a construction plan",
            parameters={"blueprint_id": "Blueprint id"},
        )
        tb.register(
            "list_construction_plans",
            self._tool_list_construction_plans,
            description="List construction plans",
            parameters={"task_id": "Optional task id"},
        )
        # Phase 2.2: New autonomous capabilities
        tb.register(
            "analyze_change_impact",
            self._tool_analyze_change_impact,
            description="Analyze impact of proposed changes across modules/files",
            parameters={"blueprint_id": "Blueprint id", "proposed_files": "List of files"},
        )
        tb.register(
            "evaluate_technical_tradeoffs",
            self._tool_evaluate_technical_tradeoffs,
            description="Evaluate technical tradeoffs for a decision",
            parameters={"decision": "Decision text", "options": "List of options", "context": "Decision context"},
        )
        tb.register(
            "generate_adr",
            self._tool_generate_adr,
            description="Auto-generate ADR from blueprint changes",
            parameters={"blueprint_id": "Blueprint id", "decision": "Decision text", "context": "Context"},
        )

    def _next_blueprint_id(self, task_id: str) -> str:
        token = str(task_id or "task").strip().replace(" ", "_")
        return f"ce_{token}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

    def _tool_create_construction_plan(
        self,
        task_id: str,
        title: str,
        modules: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        blueprint = ConstructionBlueprint(
            blueprint_id=self._next_blueprint_id(task_id),
            task_id=str(task_id or "").strip(),
            title=str(title or "Construction Plan").strip() or "Construction Plan",
            modules=[dict(item) for item in list(modules or []) if isinstance(item, dict)],
        )
        self._store.save(blueprint)
        return {"ok": True, "blueprint": blueprint.to_dict()}

    def _tool_update_scope_for_apply(
        self,
        blueprint_id: str,
        scope_additions: list[str] | None = None,
        scope_removals: list[str] | None = None,
    ) -> dict[str, Any]:
        blueprint = self._store.get(blueprint_id)
        if blueprint is None:
            return {"ok": False, "error": "blueprint_not_found", "blueprint_id": blueprint_id}
        current = {str(item).strip() for item in blueprint.scope_for_apply if str(item).strip()}
        for item in list(scope_additions or []):
            token = str(item).strip()
            if token:
                current.add(token)
        for item in list(scope_removals or []):
            token = str(item).strip()
            if token:
                current.discard(token)
        blueprint.scope_for_apply = sorted(current)
        self._store.save(blueprint)
        return {"ok": True, "blueprint": blueprint.to_dict()}

    def _tool_set_constraints(
        self,
        blueprint_id: str,
        constraints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        blueprint = self._store.get(blueprint_id)
        if blueprint is None:
            return {"ok": False, "error": "blueprint_not_found", "blueprint_id": blueprint_id}
        blueprint.constraints = dict(constraints or {})
        self._store.save(blueprint)
        return {"ok": True, "blueprint": blueprint.to_dict()}

    def _tool_set_flexible_zone(
        self,
        blueprint_id: str,
        flexible_zone: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        blueprint = self._store.get(blueprint_id)
        if blueprint is None:
            return {"ok": False, "error": "blueprint_not_found", "blueprint_id": blueprint_id}
        blueprint.flexible_zone = dict(flexible_zone or {})
        self._store.save(blueprint)
        return {"ok": True, "blueprint": blueprint.to_dict()}

    def _tool_set_escalation_triggers(
        self,
        blueprint_id: str,
        triggers: list[str] | None = None,
    ) -> dict[str, Any]:
        blueprint = self._store.get(blueprint_id)
        if blueprint is None:
            return {"ok": False, "error": "blueprint_not_found", "blueprint_id": blueprint_id}
        blueprint.escalation_triggers = [str(item).strip() for item in list(triggers or []) if str(item).strip()]
        self._store.save(blueprint)
        return {"ok": True, "blueprint": blueprint.to_dict()}

    def _tool_get_construction_plan(self, blueprint_id: str) -> dict[str, Any]:
        blueprint = self._store.get(blueprint_id)
        if blueprint is None:
            return {"ok": False, "error": "blueprint_not_found", "blueprint_id": blueprint_id}
        return {"ok": True, "blueprint": blueprint.to_dict()}

    def _tool_list_construction_plans(self, task_id: str | None = None) -> dict[str, Any]:
        rows = self._store.list_by_task(task_id or "") if str(task_id or "").strip() else self._store.list_all()
        return {"ok": True, "count": len(rows), "blueprints": [item.to_dict() for item in rows]}

    def _tool_analyze_change_impact(
        self,
        blueprint_id: str,
        proposed_files: list[str] | None = None,
    ) -> dict[str, Any]:
        """Phase 2.2: Analyze impact of proposed changes across modules/files.

        Args:
            blueprint_id: Target blueprint
            proposed_files: List of files being changed

        Returns:
            Impact analysis with affected modules, risk level, and recommendations
        """
        blueprint = self._store.get(blueprint_id)
        if blueprint is None:
            return {"ok": False, "error": "blueprint_not_found", "blueprint_id": blueprint_id}

        files = [str(f).strip() for f in (proposed_files or []) if str(f).strip()]
        if not files:
            return {"ok": False, "error": "no_files_proposed"}

        # Analyze impact based on blueprint scope and dependencies
        scope_set = set(blueprint.scope_for_apply)
        all_files = set(files)

        # Direct impact: files in blueprint scope
        direct_impact = sorted(scope_set & all_files)

        # Indirect impact: files depending on directly affected modules
        indirect_modules: set[str] = set()
        for dep_module, dep_list in blueprint.dependencies.items():
            if any(f in dep_list for f in direct_impact):
                indirect_modules.add(dep_module)

        # Calculate risk level based on scope coverage
        scope_coverage = len(direct_impact) / max(len(all_files), 1)
        risk_level = "low"
        if scope_coverage < 0.3:
            risk_level = "high"
        elif scope_coverage < 0.7:
            risk_level = "medium"

        impact_result = {
            "ok": True,
            "blueprint_id": blueprint_id,
            "direct_impact": direct_impact,
            "indirect_impact": sorted(indirect_modules),
            "risk_level": risk_level,
            "scope_coverage": round(scope_coverage, 2),
            "recommendations": self._generate_impact_recommendations(
                direct_impact, sorted(indirect_modules), risk_level
            ),
        }

        # Cache result
        self._impact_cache[blueprint_id] = impact_result
        return impact_result

    def _generate_impact_recommendations(
        self,
        direct: list[str],
        indirect: list[str],
        risk: str,
    ) -> list[str]:
        """Generate recommendations based on impact analysis."""
        recs = []
        if risk == "high":
            recs.append("High risk change - ensure rollback plan is ready")
            recs.append("Consider incremental rollout with feature flag")
        if direct:
            recs.append(f"Direct impact on {len(direct)} files - run targeted tests")
        if indirect:
            recs.append(f"Indirect impact on {len(indirect)} modules - verify downstream consumers")
        if not recs:
            recs.append("Low impact change - proceed with standard review")
        return recs

    def _tool_evaluate_technical_tradeoffs(
        self,
        decision: str,
        options: list[dict[str, Any]] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Phase 2.2: Evaluate technical tradeoffs for a decision.

        Args:
            decision: Decision text to evaluate
            options: List of option dicts with pros/cons
            context: Decision context (team_size, timeline, etc.)

        Returns:
            Tradeoff evaluation with scores and recommendation
        """
        ctx = context or {}
        opt_list = options or []

        # Default evaluation criteria
        criteria = ["maintainability", "scalability", "complexity", "time_to_value", "risk"]
        weights = {
            "maintainability": 0.25,
            "scalability": 0.20,
            "complexity": 0.20,
            "time_to_value": 0.20,
            "risk": 0.15,
        }

        scores: list[dict[str, Any]] = []
        for i, opt in enumerate(opt_list):
            opt_name = opt.get("name", f"Option {i + 1}")
            pros = opt.get("pros", [])
            cons = opt.get("cons", [])

            # Score each criterion based on pros/cons
            criterion_scores: dict[str, float] = {}
            for criterion in criteria:
                score = 0.5  # Neutral baseline
                # Check if pros mention the criterion
                pros_text = " ".join(pros).lower() if isinstance(pros, list) else str(pros).lower()
                cons_text = " ".join(cons).lower() if isinstance(cons, list) else str(cons).lower()

                if criterion in pros_text:
                    score += 0.2
                if criterion in cons_text:
                    score -= 0.2
                criterion_scores[criterion] = max(0.0, min(1.0, score))

            # Calculate weighted total
            total = sum(criterion_scores.get(c, 0.5) * weights.get(c, 0.2) for c in criteria)
            scores.append(
                {
                    "option": opt_name,
                    "criterion_scores": criterion_scores,
                    "total_score": round(total, 2),
                }
            )

        # Sort by total score
        scores.sort(key=lambda x: x["total_score"], reverse=True)
        recommended = scores[0]["option"] if scores else None

        return {
            "ok": True,
            "decision": decision,
            "criteria": criteria,
            "weights": weights,
            "evaluations": scores,
            "recommended": recommended,
            "context": ctx,
        }

    def _tool_generate_adr(
        self,
        blueprint_id: str,
        decision: str,
        context: str | None = None,
    ) -> dict[str, Any]:
        """Phase 2.2: Auto-generate ADR from blueprint changes.

        Args:
            blueprint_id: Source blueprint
            decision: Decision text
            context: Additional context

        Returns:
            Generated ADR record
        """
        blueprint = self._store.get(blueprint_id)
        if blueprint is None:
            return {"ok": False, "error": "blueprint_not_found", "blueprint_id": blueprint_id}

        # Lazy initialize ADR store
        adr_store = self._adr_store
        if adr_store is None:
            from polaris.cells.chief_engineer.blueprint.internal.adr_store import (  # type: ignore[attr-defined]
                BlueprintADRStore,
            )

            adr_store = BlueprintADRStore(workspace=self.workspace)
            self._adr_store = adr_store

        # Generate ADR ID
        existing_adr_count = len(adr_store.list_by_blueprint(blueprint_id))
        adr_id = f"ADR-{blueprint_id}-{existing_adr_count + 1:03d}"

        import time

        adr_record = {
            "adr_id": adr_id,
            "blueprint_id": blueprint_id,
            "related_task_ids": [blueprint.task_id],
            "decision": decision,
            "context": context or "",
            "delta": {
                "scope": blueprint.scope_for_apply,
                "constraints": blueprint.constraints,
                "modules": blueprint.modules,
            },
            "status": "proposed",
            "proposed_at_ms": int(time.time() * 1000),
        }

        # Store in ADR store if available
        try:
            from polaris.cells.chief_engineer.blueprint.internal.adr_store import (
                BlueprintADR,  # type: ignore[attr-defined]
            )

            adr: BlueprintADR = BlueprintADR(  # type: ignore[call-overload]
                adr_id=adr_id,
                blueprint_id=blueprint_id,
                related_task_ids=[blueprint.task_id],
                decision=decision,
                context=str(context or ""),
                delta={
                    "scope": blueprint.scope_for_apply,
                    "constraints": blueprint.constraints,
                    "modules": blueprint.modules,
                },
                status="proposed",
                proposed_at_ms=int(time.time() * 1000),
            )
            adr_store.save(adr)
        except (ImportError, TypeError):
            # Fallback if ADR store not available
            pass

        return {"ok": True, "adr": adr_record}

    def handle_message(self, message: AgentMessage) -> AgentMessage | None:
        payload = dict(message.payload or {})
        if message.type != MessageType.TASK:
            return None
        action = str(payload.get("action") or "").strip().lower()
        if action == "generate_blueprint":
            result = self._tool_create_construction_plan(
                task_id=str(payload.get("task_id") or "").strip(),
                title=str(payload.get("title") or "Construction Plan").strip(),
                modules=payload.get("modules") if isinstance(payload.get("modules"), list) else [],
            )
        elif action == "update_scope":
            result = self._tool_update_scope_for_apply(
                blueprint_id=str(payload.get("blueprint_id") or "").strip(),
                scope_additions=payload.get("scope_additions")
                if isinstance(payload.get("scope_additions"), list)
                else [],
                scope_removals=payload.get("scope_removals") if isinstance(payload.get("scope_removals"), list) else [],
            )
        elif action == "get_construction_plan":
            result = self._tool_get_construction_plan(
                blueprint_id=str(payload.get("blueprint_id") or "").strip(),
            )
        else:
            result = {"ok": False, "error": "unsupported_action", "action": action}

        return AgentMessage.create(
            msg_type=MessageType.RESULT,
            sender=self.agent_name,
            receiver=message.sender,
            payload={"role": "chief_engineer", "action": action, "result": result},
            correlation_id=message.id,
        )

    def run_cycle(self) -> bool:
        message = self.message_queue.receive(block=False)
        if message is None:
            return False
        response = self.handle_message(message)
        if response is not None:
            self.message_queue.send(response)
        return True
