"""Application-layer orchestrator for the Architect domain.

This module provides a high-level facade that encapsulates the architecture
design lifecycle: gather context, design, blueprint, and handoff.  Delivery
layers (CLI, HTTP, TUI) use this orchestrator instead of importing Cell
internals directly.

Call chain::

    delivery -> ArchitectOrchestrator -> cells.architect.design.public
                                       -> cells.audit.verdict.public
                                       -> kernelone.*

Architecture constraints (AGENTS.md):
    - Imports ONLY from Cell ``public/`` boundaries and ``kernelone`` contracts.
    - NEVER imports from ``internal/`` at module level.
    - All text I/O uses explicit UTF-8.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ArchitectDesignConfig",
    "ArchitectDesignLifecycleResult",
    "ArchitectOrchestrator",
    "ArchitectOrchestratorError",
    "BlueprintResult",
    "DesignResult",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ArchitectOrchestratorError(RuntimeError):
    """Application-layer error for Architect orchestration operations.

    Wraps lower-level Cell or KernelOne errors so delivery never catches
    infrastructure-specific exception types.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "architect_orchestrator_error",
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.cause = cause


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DesignResult:
    """Immutable snapshot of a single architecture design outcome."""

    design_id: str
    doc_type: str  # "requirements" | "adr" | "interface_contract" | "plan"
    title: str
    status: str  # "completed" | "failed"
    content_length: int = 0
    output_path: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BlueprintResult:
    """Immutable snapshot of a compiled architecture blueprint."""

    blueprint_id: str
    design_ids: tuple[str, ...]
    summary: str
    recommendation_paths: tuple[str, ...] = ()
    status: str = "ready"  # "ready" | "incomplete" | "failed"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArchitectDesignLifecycleResult:
    """Immutable snapshot of a full Architect design lifecycle outcome."""

    success: bool
    workspace: str
    designs: tuple[DesignResult, ...]
    blueprint: BlueprintResult | None = None
    handoff_package: dict[str, Any] = field(default_factory=dict)
    notes: str = ""


@dataclass(frozen=True, slots=True)
class ArchitectDesignConfig:
    """Configuration for Architect design execution."""

    workspace: str
    docs_dir: str = "docs/product"
    objective: str = ""
    constraints: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ArchitectOrchestrator
# ---------------------------------------------------------------------------


class ArchitectOrchestrator:
    """High-level facade for the Architecture design lifecycle.

    Responsibilities:
        1. Gather context – collect workspace state, constraints, and
           objectives from delivery-layer input.
        2. Design – invoke ``architect.design`` Cell to produce architecture
           documents (requirements, ADRs, interface contracts, plans).
        3. Blueprint – aggregate individual designs into a unified blueprint.
        4. Handoff – package the blueprint into a canonical handoff artifact
           that downstream Cells (Director, QA) can consume.

    The orchestrator is stateless and cheap to construct.  All mutable
    state (Architect service handles, design cache) is obtained lazily
    inside each public method so that import-time side effects are avoided.
    """

    def __init__(self, config: ArchitectDesignConfig) -> None:
        self._config = config
        self._workspace = str(config.workspace)
        self._architect_service: Any | None = None

    # -- lazy service resolution --------------------------------------------

    def _get_architect_service(self) -> Any:
        """Lazily resolve ``ArchitectService`` from the ``architect.design`` Cell."""
        if self._architect_service is not None:
            return self._architect_service
        try:
            from polaris.cells.architect.design.public import (
                ArchitectConfig,
                ArchitectService,
            )

            cfg = ArchitectConfig(
                workspace=self._workspace,
                docs_dir=self._config.docs_dir,
            )
            self._architect_service = ArchitectService(config=cfg)
            return self._architect_service
        except (ImportError, RuntimeError, ValueError, OSError) as exc:
            raise ArchitectOrchestratorError(
                f"Failed to resolve ArchitectService: {exc}",
                code="architect_service_resolution_error",
                cause=exc,
            ) from exc

    # -- step 1: gather context ---------------------------------------------

    def gather_context(
        self,
        *,
        objective: str | None = None,
        constraints: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Gather and normalize design context.

        This step validates inputs, merges them with configuration defaults,
        and returns a context dict that can be passed to ``design``.

        Args:
            objective: Design objective (overrides config default).
            constraints: Design constraints (overrides config defaults).
            context: Additional context (overrides config defaults).

        Returns:
            Context dict with keys:
            ``workspace``, ``objective``, ``constraints``, ``context``.

        Raises:
            ArchitectOrchestratorError: if the objective is missing or empty.
        """
        merged_objective = str(objective or self._config.objective).strip()
        if not merged_objective:
            raise ArchitectOrchestratorError(
                "design objective is required",
                code="missing_objective",
            )

        merged_constraints = dict(self._config.constraints)
        if constraints is not None:
            merged_constraints.update(constraints)

        merged_context = dict(self._config.context)
        if context is not None:
            merged_context.update(context)

        return {
            "workspace": self._workspace,
            "objective": merged_objective,
            "constraints": merged_constraints,
            "context": merged_context,
        }

    # -- step 2: design -----------------------------------------------------

    async def design_requirements(
        self,
        *,
        goal: str,
        in_scope: list[str],
        out_of_scope: list[str],
        constraints: list[str],
        definition_of_done: list[str],
        backlog: list[str],
    ) -> DesignResult:
        """Create a requirements document via the ``architect.design`` Cell.

        Args:
            goal: The design goal / objective.
            in_scope: Items in scope.
            out_of_scope: Items out of scope.
            constraints: Design constraints.
            definition_of_done: Acceptance criteria.
            backlog: Backlog items.

        Returns:
            ``DesignResult`` snapshot.

        Raises:
            ArchitectOrchestratorError: if the design Cell raises an
                unexpected exception.
        """
        service = self._get_architect_service()
        try:
            doc = await service.create_requirements_doc(
                goal=goal,
                in_scope=list(in_scope) if in_scope is not None else [],
                out_of_scope=list(out_of_scope) if out_of_scope is not None else [],
                constraints=list(constraints) if constraints is not None else [],
                definition_of_done=list(definition_of_done) if definition_of_done is not None else [],
                backlog=list(backlog) if backlog is not None else [],
            )

            return DesignResult(
                design_id=str(doc.doc_id),
                doc_type=str(doc.doc_type),
                title=str(doc.title),
                status="completed",
                content_length=len(str(doc.content)),
                metadata={
                    "version": str(doc.version),
                    "created_at": (
                        doc.created_at.isoformat()
                        if hasattr(doc.created_at, "isoformat")
                        else str(doc.created_at)
                    ),
                },
            )
        except (AttributeError, RuntimeError, ValueError) as exc:
            raise ArchitectOrchestratorError(
                f"Requirements design failed: {exc}",
                code="requirements_design_failed",
                cause=exc,
            ) from exc

    async def design_adr(
        self,
        *,
        title: str,
        context: str,
        decision: str,
        consequences: list[str],
    ) -> DesignResult:
        """Create an Architecture Decision Record via the ``architect.design`` Cell.

        Args:
            title: ADR title.
            context: Decision context.
            decision: The decision made.
            consequences: List of consequences.

        Returns:
            ``DesignResult`` snapshot.

        Raises:
            ArchitectOrchestratorError: if the design Cell raises an
                unexpected exception.
        """
        service = self._get_architect_service()
        try:
            doc = await service.create_adr(
                title=title,
                context=context,
                decision=decision,
                consequences=list(consequences),
            )

            return DesignResult(
                design_id=str(doc.doc_id),
                doc_type=str(doc.doc_type),
                title=str(doc.title),
                status="completed",
                content_length=len(str(doc.content)),
                metadata={
                    "version": str(doc.version),
                    "created_at": (
                        doc.created_at.isoformat()
                        if hasattr(doc.created_at, "isoformat")
                        else str(doc.created_at)
                    ),
                },
            )
        except (AttributeError, RuntimeError, ValueError) as exc:
            raise ArchitectOrchestratorError(
                f"ADR design failed: {exc}",
                code="adr_design_failed",
                cause=exc,
            ) from exc

    async def design_interface_contract(
        self,
        *,
        api_name: str,
        endpoints: list[dict[str, Any]],
    ) -> DesignResult:
        """Create an interface contract via the ``architect.design`` Cell.

        Args:
            api_name: API / interface name.
            endpoints: List of endpoint dicts.

        Returns:
            ``DesignResult`` snapshot.

        Raises:
            ArchitectOrchestratorError: if the design Cell raises an
                unexpected exception.
        """
        service = self._get_architect_service()
        try:
            doc = await service.create_interface_contract(
                api_name=api_name,
                endpoints=list(endpoints),
            )

            return DesignResult(
                design_id=str(doc.doc_id),
                doc_type=str(doc.doc_type),
                title=str(doc.title),
                status="completed",
                content_length=len(str(doc.content)),
                metadata={
                    "version": str(doc.version),
                    "created_at": (
                        doc.created_at.isoformat()
                        if hasattr(doc.created_at, "isoformat")
                        else str(doc.created_at)
                    ),
                },
            )
        except (AttributeError, RuntimeError, ValueError) as exc:
            raise ArchitectOrchestratorError(
                f"Interface contract design failed: {exc}",
                code="interface_contract_design_failed",
                cause=exc,
            ) from exc

    async def design_implementation_plan(
        self,
        *,
        milestones: list[str],
        verification_commands: list[str],
        risks: list[dict[str, Any]],
    ) -> DesignResult:
        """Create an implementation plan via the ``architect.design`` Cell.

        Args:
            milestones: Delivery milestones.
            verification_commands: Commands to verify each milestone.
            risks: Risk dicts with ``risk`` and ``mitigation`` keys.

        Returns:
            ``DesignResult`` snapshot.

        Raises:
            ArchitectOrchestratorError: if the design Cell raises an
                unexpected exception.
        """
        service = self._get_architect_service()
        try:
            doc = await service.create_implementation_plan(
                milestones=list(milestones),
                verification_commands=list(verification_commands),
                risks=list(risks),
            )

            return DesignResult(
                design_id=str(doc.doc_id),
                doc_type=str(doc.doc_type),
                title=str(doc.title),
                status="completed",
                content_length=len(str(doc.content)),
                metadata={
                    "version": str(doc.version),
                    "created_at": (
                        doc.created_at.isoformat()
                        if hasattr(doc.created_at, "isoformat")
                        else str(doc.created_at)
                    ),
                },
            )
        except (AttributeError, RuntimeError, ValueError) as exc:
            raise ArchitectOrchestratorError(
                f"Implementation plan design failed: {exc}",
                code="implementation_plan_design_failed",
                cause=exc,
            ) from exc

    # -- step 3: blueprint --------------------------------------------------

    def compile_blueprint(
        self,
        *,
        designs: list[DesignResult],
        summary: str = "",
    ) -> BlueprintResult:
        """Compile individual designs into a unified blueprint.

        This aggregates design IDs, collects output paths, and produces a
        canonical ``BlueprintResult`` that represents the complete
        architecture package.

        Args:
            designs: List of design results to aggregate.
            summary: Optional human-readable summary.

        Returns:
            ``BlueprintResult`` snapshot.
        """
        design_ids = tuple(d.design_id for d in designs if d.status == "completed")
        paths = tuple(d.output_path for d in designs if d.output_path)

        status = "ready" if len(design_ids) == len(designs) else "incomplete"
        if not design_ids:
            status = "failed"

        return BlueprintResult(
            blueprint_id=f"blueprint-{self._workspace}",
            design_ids=design_ids,
            summary=summary or f"Blueprint with {len(design_ids)} design(s)",
            recommendation_paths=paths,
            status=status,
            metadata={
                "workspace": self._workspace,
                "total_designs": len(designs),
                "completed_designs": len(design_ids),
            },
        )

    # -- step 4: handoff ----------------------------------------------------

    def build_handoff_package(
        self,
        *,
        blueprint: BlueprintResult,
        designs: list[DesignResult],
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a canonical handoff package for downstream Cells.

        The handoff package follows the ``ContextHandoffPack`` convention
        so that Director and QA Cells can consume it without knowing
        Architect internals.

        Args:
            blueprint: The compiled blueprint.
            designs: List of design results included in the handoff.
            extra: Optional extra metadata to merge.

        Returns:
            Handoff package dict.
        """
        design_payloads = []
        for d in designs:
            design_payloads.append(
                {
                    "design_id": d.design_id,
                    "doc_type": d.doc_type,
                    "title": d.title,
                    "status": d.status,
                    "content_length": d.content_length,
                    "output_path": d.output_path,
                    "metadata": dict(d.metadata),
                }
            )

        package = {
            "handoff_type": "architect_blueprint",
            "workspace": self._workspace,
            "blueprint_id": blueprint.blueprint_id,
            "blueprint_status": blueprint.status,
            "summary": blueprint.summary,
            "design_ids": list(blueprint.design_ids),
            "recommendation_paths": list(blueprint.recommendation_paths),
            "designs": design_payloads,
            "metadata": dict(extra or {}),
        }

        logger.info(
            "architect handoff package built: blueprint_id=%s designs=%s",
            blueprint.blueprint_id,
            len(design_payloads),
        )

        return package

    # -- convenience: full lifecycle ----------------------------------------

    async def run_design_lifecycle(
        self,
        *,
        objective: str,
        requirements: dict[str, Any] | None = None,
        adrs: list[dict[str, Any]] | None = None,
        interfaces: list[dict[str, Any]] | None = None,
        plans: list[dict[str, Any]] | None = None,
    ) -> ArchitectDesignLifecycleResult:
        """Run the complete Architecture design lifecycle.

        This is the **primary high-level entry point** for delivery layers.
        It orchestrates gather context -> design -> blueprint -> handoff
        while keeping all Cell-internal details hidden.

        Args:
            objective: The overarching design objective.
            requirements: Optional requirements spec dict with keys:
                ``goal``, ``in_scope``, ``out_of_scope``, ``constraints``,
                ``definition_of_done``, ``backlog``.
            adrs: Optional list of ADR dicts with keys:
                ``title``, ``context``, ``decision``, ``consequences``.
            interfaces: Optional list of interface dicts with keys:
                ``api_name``, ``endpoints``.
            plans: Optional list of plan dicts with keys:
                ``milestones``, ``verification_commands``, ``risks``.

        Returns:
            ``ArchitectDesignLifecycleResult`` snapshot.
        """
        logger.info(
            "architect design lifecycle start: workspace=%s objective=%s",
            self._workspace,
            objective,
        )

        # 1. Gather context
        ctx = self.gather_context(objective=objective)

        designs: list[DesignResult] = []

        # 2. Design – requirements
        if requirements is not None:
            try:
                dr = await self.design_requirements(
                    goal=requirements.get("goal", ctx["objective"]),
                    in_scope=requirements.get("in_scope", []),
                    out_of_scope=requirements.get("out_of_scope", []),
                    constraints=requirements.get("constraints", []),
                    definition_of_done=requirements.get("definition_of_done", []),
                    backlog=requirements.get("backlog", []),
                )
                designs.append(dr)
            except ArchitectOrchestratorError as exc:
                logger.warning("Requirements design failed: %s", exc)
                designs.append(
                    DesignResult(
                        design_id="req-failed",
                        doc_type="requirements",
                        title="Requirements (failed)",
                        status="failed",
                        error=str(exc),
                    )
                )

        # 2b. Design – ADRs
        for adr in adrs or []:
            try:
                dr = await self.design_adr(
                    title=adr["title"],
                    context=adr["context"],
                    decision=adr["decision"],
                    consequences=adr.get("consequences", []),
                )
                designs.append(dr)
            except (ArchitectOrchestratorError, KeyError) as exc:
                logger.warning("ADR design failed: %s", exc)
                designs.append(
                    DesignResult(
                        design_id="adr-failed",
                        doc_type="adr",
                        title=adr.get("title", "ADR (failed)"),
                        status="failed",
                        error=str(exc),
                    )
                )

        # 2c. Design – interface contracts
        for iface in interfaces or []:
            try:
                dr = await self.design_interface_contract(
                    api_name=iface["api_name"],
                    endpoints=iface.get("endpoints", []),
                )
                designs.append(dr)
            except (ArchitectOrchestratorError, KeyError) as exc:
                logger.warning("Interface contract design failed: %s", exc)
                designs.append(
                    DesignResult(
                        design_id="contract-failed",
                        doc_type="interface_contract",
                        title=iface.get("api_name", "Interface (failed)"),
                        status="failed",
                        error=str(exc),
                    )
                )

        # 2d. Design – implementation plans
        for plan in plans or []:
            try:
                dr = await self.design_implementation_plan(
                    milestones=plan.get("milestones", []),
                    verification_commands=plan.get("verification_commands", []),
                    risks=plan.get("risks", []),
                )
                designs.append(dr)
            except (ArchitectOrchestratorError, KeyError) as exc:
                logger.warning("Implementation plan design failed: %s", exc)
                designs.append(
                    DesignResult(
                        design_id="plan-failed",
                        doc_type="plan",
                        title="Implementation Plan (failed)",
                        status="failed",
                        error=str(exc),
                    )
                )

        # 3. Blueprint
        blueprint = self.compile_blueprint(
            designs=designs,
            summary=f"Architecture blueprint for: {ctx['objective']}",
        )

        # 4. Handoff
        handoff = self.build_handoff_package(
            blueprint=blueprint,
            designs=designs,
            extra={"objective": ctx["objective"], "constraints": ctx["constraints"]},
        )

        success = blueprint.status == "ready"

        logger.info(
            "architect design lifecycle complete: workspace=%s success=%s designs=%s",
            self._workspace,
            success,
            len(designs),
        )

        return ArchitectDesignLifecycleResult(
            success=success,
            workspace=self._workspace,
            designs=tuple(designs),
            blueprint=blueprint,
            handoff_package=handoff,
            notes=f"Design lifecycle completed with {len(designs)} design(s)",
        )
