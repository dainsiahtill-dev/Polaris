"""ADR (Architecture Decision Record) incremental blueprint engine.

This module implements an ADR-based evolution mechanism for construction
blueprints. The base blueprint is created once and never overwritten;
subsequent changes are expressed as structured ADR deltas and compiled
on demand.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from polaris.cells.chief_engineer.blueprint.internal.blueprint_persistence import (
    BlueprintPersistence,
)


@dataclass(frozen=True)
class BlueprintADR:
    """An incremental architecture decision record for a blueprint.

    Attributes:
        adr_id: Unique ADR identifier, e.g. "ADR-BP-001-001".
        blueprint_id: Parent blueprint identifier.
        related_task_ids: Tasks that motivated this decision.
        decision: Short human-readable decision description.
        context: Why the decision was made.
        delta: Structured change payload.
        status: One of "proposed", "approved", "compiled", "reverted".
        proposed_at_ms: Proposal timestamp.
        compiled_at_ms: Compilation timestamp, or None.
        supersedes: Optional ADR ID that this ADR replaces.
    """

    adr_id: str
    blueprint_id: str
    related_task_ids: list[str]
    decision: str
    context: str
    delta: dict[str, Any]
    status: str
    proposed_at_ms: int
    compiled_at_ms: int | None = None
    supersedes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "adr_id": self.adr_id,
            "blueprint_id": self.blueprint_id,
            "related_task_ids": list(self.related_task_ids),
            "decision": self.decision,
            "context": self.context,
            "delta": dict(self.delta),
            "status": self.status,
            "proposed_at_ms": self.proposed_at_ms,
            "compiled_at_ms": self.compiled_at_ms,
            "supersedes": self.supersedes,
        }


@dataclass
class BlueprintBase:
    """Immutable base schema for a blueprint.

    Attributes:
        blueprint_id: Unique blueprint identifier.
        version: Compilation version number (increments on each compile).
        base_schema: The initial construction_plan dictionary.
        adrs: List of applied ADRs.
        status: Lifecycle status of the blueprint.
        created_at_ms: Creation timestamp.
        last_compiled_at_ms: Last compilation timestamp.
    """

    blueprint_id: str
    version: int
    base_schema: dict[str, Any]
    adrs: list[BlueprintADR] = field(default_factory=list)
    status: str = "draft"
    created_at_ms: int = 0
    last_compiled_at_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "blueprint_id": self.blueprint_id,
            "version": self.version,
            "base_schema": copy.deepcopy(self.base_schema),
            "adrs": [adr.to_dict() for adr in self.adrs],
            "status": self.status,
            "created_at_ms": self.created_at_ms,
            "last_compiled_at_ms": self.last_compiled_at_ms,
        }


def _now_epoch_ms() -> int:
    import time

    return int(time.time() * 1000)


class ADRStore:
    """Storage, compilation, and query engine for blueprint ADRs.

    The store keeps blueprints in memory for fast access and persists
    them to disk via ``BlueprintPersistence``.
    """

    def __init__(self, workspace: str) -> None:
        """Initialize the ADR store bound to a workspace.

        Args:
            workspace: Workspace root path.
        """
        self._persistence = BlueprintPersistence(workspace)
        self._blueprints: dict[str, BlueprintBase] = {}
        self._load_all_from_disk()

    def _load_all_from_disk(self) -> None:
        """Hydrate the in-memory cache from disk."""
        for data in self._persistence.load_all():
            bp = self._deserialize_blueprint(data)
            self._blueprints[bp.blueprint_id] = bp

    def _persist_blueprint(self, blueprint_id: str) -> None:
        """Serialize and save a blueprint to disk."""
        bp = self._blueprints.get(blueprint_id)
        if bp is None:
            return
        self._persistence.save(blueprint_id, self._serialize_blueprint(bp))

    @staticmethod
    def _serialize_blueprint(bp: BlueprintBase) -> dict[str, Any]:
        return bp.to_dict()

    @staticmethod
    def _deserialize_blueprint(data: dict[str, Any]) -> BlueprintBase:
        adr_list = [
            BlueprintADR(
                adr_id=str(a["adr_id"]),
                blueprint_id=str(a["blueprint_id"]),
                related_task_ids=list(a.get("related_task_ids", [])),
                decision=str(a["decision"]),
                context=str(a["context"]),
                delta=dict(a.get("delta", {})),
                status=str(a["status"]),
                proposed_at_ms=int(a["proposed_at_ms"]),
                compiled_at_ms=int(a["compiled_at_ms"]) if a.get("compiled_at_ms") is not None else None,
                supersedes=str(a["supersedes"]) if a.get("supersedes") is not None else None,
            )
            for a in data.get("adrs", [])
        ]
        return BlueprintBase(
            blueprint_id=str(data["blueprint_id"]),
            version=int(data["version"]),
            base_schema=dict(data.get("base_schema", {})),
            adrs=adr_list,
            status=str(data.get("status", "draft")),
            created_at_ms=int(data.get("created_at_ms", 0)),
            last_compiled_at_ms=int(data.get("last_compiled_at_ms", 0)),
        )

    def create_blueprint(
        self,
        blueprint_id: str,
        base_schema: dict[str, Any],
    ) -> BlueprintBase:
        """Create a new blueprint base schema.

        Args:
            blueprint_id: Unique identifier for the blueprint.
            base_schema: Initial construction_plan dictionary.

        Returns:
            The newly created BlueprintBase.
        """
        now = _now_epoch_ms()
        bp = BlueprintBase(
            blueprint_id=blueprint_id,
            version=1,
            base_schema=copy.deepcopy(base_schema),
            status="approved",
            created_at_ms=now,
            last_compiled_at_ms=now,
        )
        self._blueprints[blueprint_id] = bp
        self._persist_blueprint(blueprint_id)
        return bp

    def propose_adr(
        self,
        blueprint_id: str,
        related_task_ids: list[str],
        decision: str,
        context: str,
        delta: dict[str, Any],
        supersedes: str | None = None,
    ) -> BlueprintADR:
        """Propose a new ADR for an existing blueprint.

        Args:
            blueprint_id: Target blueprint identifier.
            related_task_ids: Tasks motivating the change.
            decision: Short decision description.
            context: Decision rationale.
            delta: Structured change payload.
            supersedes: Optional ADR ID being replaced.

        Returns:
            The newly created BlueprintADR.

        Raises:
            ValueError: If the blueprint does not exist.
        """
        bp = self._blueprints.get(blueprint_id)
        if bp is None:
            raise ValueError(f"Blueprint {blueprint_id} not found")

        seq = len(bp.adrs) + 1
        adr = BlueprintADR(
            adr_id=f"ADR-{blueprint_id}-{seq:03d}",
            blueprint_id=blueprint_id,
            related_task_ids=list(related_task_ids),
            decision=decision,
            context=context,
            delta=dict(delta),
            status="proposed",
            proposed_at_ms=_now_epoch_ms(),
            supersedes=supersedes,
        )
        bp.adrs.append(adr)
        self._persist_blueprint(blueprint_id)
        return adr

    def compile(self, blueprint_id: str) -> dict[str, Any]:
        """Compile the base schema and all approved ADRs into the latest plan.

        Args:
            blueprint_id: Target blueprint identifier.

        Returns:
            The fully compiled construction_plan dictionary.

        Raises:
            ValueError: If the blueprint does not exist.
        """
        bp = self._blueprints.get(blueprint_id)
        if bp is None:
            raise ValueError(f"Blueprint {blueprint_id} not found")

        compiled = copy.deepcopy(bp.base_schema)
        for adr in bp.adrs:
            if adr.status in ("proposed", "approved"):
                compiled = _apply_delta(compiled, adr.delta)
                object.__setattr__(adr, "status", "compiled")
                object.__setattr__(adr, "compiled_at_ms", _now_epoch_ms())

        bp.version += 1
        bp.last_compiled_at_ms = _now_epoch_ms()
        self._persist_blueprint(blueprint_id)
        return compiled

    def get_blueprint_history(self, blueprint_id: str) -> list[dict[str, Any]]:
        """Return the evolution history of a blueprint.

        Args:
            blueprint_id: Target blueprint identifier.

        Returns:
            List of ADR summary dictionaries.
        """
        bp = self._blueprints.get(blueprint_id)
        if bp is None:
            return []
        return [
            {
                "adr_id": adr.adr_id,
                "decision": adr.decision,
                "context": adr.context,
                "status": adr.status,
                "proposed_at_ms": adr.proposed_at_ms,
            }
            for adr in bp.adrs
        ]

    def revert_adr(self, adr_id: str) -> bool:
        """Revert an ADR so it is skipped in future compilations.

        Args:
            adr_id: ADR identifier to revert.

        Returns:
            True if found and reverted, False otherwise.
        """
        for bp in self._blueprints.values():
            for adr in bp.adrs:
                if adr.adr_id == adr_id:
                    object.__setattr__(adr, "status", "reverted")
                    self._persist_blueprint(bp.blueprint_id)
                    return True
        return False

    def get_compiled_plan(self, blueprint_id: str) -> dict[str, Any] | None:
        """Return the last compiled plan without mutating store state.

        Args:
            blueprint_id: Target blueprint identifier.

        Returns:
            The compiled plan dictionary, or None if not found.
        """
        bp = self._blueprints.get(blueprint_id)
        if bp is None:
            return None
        compiled = copy.deepcopy(bp.base_schema)
        for adr in bp.adrs:
            if adr.status in ("proposed", "approved", "compiled"):
                compiled = _apply_delta(compiled, adr.delta)
        return compiled


def _apply_delta(schema: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    """Apply a single structured delta to a blueprint schema."""
    delta_type = delta.get("type")
    payload = delta.get("payload", {})

    if delta_type == "add_step":
        step = payload.get("step")
        if step is not None:
            steps = schema.setdefault("construction_steps", [])
            after = payload.get("after_step", len(steps))
            steps.insert(after, step)

    elif delta_type == "modify_step":
        steps = schema.get("construction_steps", [])
        idx = payload.get("step_index", 0)
        if isinstance(steps, list) and 0 <= idx < len(steps):
            step = steps[idx]
            if isinstance(step, dict):
                step.update(payload.get("changes", {}))

    elif delta_type == "remove_step":
        steps = schema.get("construction_steps", [])
        idx = payload.get("step_index", -1)
        if isinstance(steps, list) and 0 <= idx < len(steps):
            steps.pop(idx)

    elif delta_type == "add_file":
        file_path = payload.get("file")
        if file_path is not None:
            files = schema.setdefault("scope_for_apply", {}).setdefault(payload.get("category", "modified_files"), [])
            if isinstance(files, list):
                files.append(file_path)

    elif delta_type == "remove_file":
        file_path = payload.get("file")
        if file_path is not None:
            scope = schema.get("scope_for_apply", {})
            if isinstance(scope, dict):
                for cat in scope.values():
                    if isinstance(cat, list) and file_path in cat:
                        cat.remove(file_path)

    elif delta_type == "change_scope":
        schema["scope_for_apply"] = payload.get("new_scope", {})

    elif delta_type == "change_risk":
        flags = schema.setdefault("risk_flags", [])
        if isinstance(flags, list):
            flags.append(payload.get("risk"))

    return schema
