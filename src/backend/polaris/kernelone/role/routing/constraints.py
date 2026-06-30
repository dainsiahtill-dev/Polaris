"""Routing constraint checks and manual-vs-inferred conflict resolution."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.kernelone.role.loaders import (
        PersonaLoader,
        ProfessionLoader,
    )
    from polaris.kernelone.role.routing.context import RoutingContext
    from polaris.kernelone.role.routing.result import ResolvedTriple, RoutingInference, RoutingManualSpec

logger = logging.getLogger(__name__)


class RoutingConstraintEngine:
    """Checks whether an Anchor + Profession + Persona triple is allowed."""

    def __init__(
        self,
        persona_loader: PersonaLoader,
        profession_loader: ProfessionLoader,
    ) -> None:
        self._persona_loader = persona_loader
        self._profession_loader = profession_loader

    def is_allowed(
        self,
        anchor_id: str,
        profession_id: str,
        persona_id: str,
        context: RoutingContext,
    ) -> bool:
        """Check whether the requested routing triple is allowed."""
        profession = self._profession_loader.load(profession_id)
        persona = self._persona_loader.load(persona_id)

        if not profession or not persona:
            return True

        if (
            hasattr(profession, "routing")
            and hasattr(profession.routing, "excludes_personas")
            and persona_id in profession.routing.excludes_personas
        ):
            logger.debug("Persona %s excluded by profession %s", persona_id, profession_id)
            return False

        if hasattr(persona, "routing") and hasattr(persona.routing, "excludes_domains"):
            domain = context.domain
            if domain in persona.routing.excludes_domains:
                logger.debug("Domain %s excluded by persona %s", domain, persona_id)
                return False

        return True

    def get_allowed_set(
        self,
        profession_id: str,
        context: RoutingContext,
    ) -> tuple[list[str], list[str]]:
        """Get allowed anchor and persona lists for a profession."""
        del context
        profession = self._profession_loader.load(profession_id)

        if not profession:
            return [], []

        anchors: list[str] = []
        personas: list[str] = []

        if hasattr(profession, "routing"):
            routing = profession.routing
            if hasattr(routing, "compatible_anchors"):
                anchors = routing.compatible_anchors.copy()
            if hasattr(routing, "compatible_personas"):
                personas = routing.compatible_personas.copy()

        return anchors, personas


class ConflictResolver:
    """Resolve conflicts between user-specified and system-inferred routing."""

    def __init__(
        self,
        persona_loader: PersonaLoader,
        profession_loader: ProfessionLoader,
    ) -> None:
        self._persona_loader = persona_loader
        self._profession_loader = profession_loader

    def resolve(
        self,
        manual: RoutingManualSpec | None,
        inferred: RoutingInference,
        context: RoutingContext,
    ) -> ResolvedTriple:
        """Resolve manual-vs-inferred conflict and return final routing triple."""
        if manual is None:
            return ResolvedTriple(
                anchor_id=inferred.anchor_id,
                profession_id=inferred.profession_id,
                persona_id=inferred.persona_id,
                resolution="inferred_only",
                warnings=[],
            )

        persona_conflict = False
        if manual.persona_id and manual.profession_id:
            persona_conflict = self._check_persona_profession_conflict(manual.persona_id, manual.profession_id)
        elif manual.persona_id:
            persona_conflict = self._check_persona_profession_conflict(manual.persona_id, inferred.profession_id)

        if persona_conflict:
            fallback_persona = self._get_fallback_persona(manual.persona_id or "unknown", context)

            return ResolvedTriple(
                anchor_id=manual.anchor_id or inferred.anchor_id,
                profession_id=inferred.profession_id,
                persona_id=fallback_persona,
                resolution="persona_relaxed",
                warnings=[
                    f"Persona '{manual.persona_id}' incompatible with profession '{inferred.profession_id}', "
                    f"switched to allowed Persona '{fallback_persona}'"
                ],
            )

        return ResolvedTriple(
            anchor_id=manual.anchor_id or inferred.anchor_id,
            profession_id=manual.profession_id or inferred.profession_id,
            persona_id=manual.persona_id or inferred.persona_id,
            resolution="manual_preferred",
            warnings=[],
        )

    def _check_persona_profession_conflict(self, persona_id: str, profession_id: str) -> bool:
        """Check if Persona and Profession are mutually exclusive."""
        persona = self._persona_loader.load(persona_id)
        profession = self._profession_loader.load(profession_id)

        if not persona or not profession:
            return False

        if (
            hasattr(persona, "routing")
            and hasattr(persona.routing, "excludes_domains")
            and hasattr(profession, "domain")
            and profession.domain in persona.routing.excludes_domains
        ):
            return True

        return (
            hasattr(profession, "routing")
            and hasattr(profession.routing, "excludes_personas")
            and persona_id in profession.routing.excludes_personas
        )

    def _get_fallback_persona(self, original_persona_id: str, context: RoutingContext) -> str:
        """Get an allowed fallback persona for the current routing context."""
        del original_persona_id, context
        return "gongbu_shilang"
