"""Role Overlay contracts — layered strategy overrides per role.

RoleOverlay allows a role to inherit a foundation profile and apply
role-specific overrides without forking the base profile.

Resolution order (per blueprint §4.3):
    1. explicit session override
    2. role overlay (this layer)
    3. parent foundation profile

Zero behavior drift: overlays only add/override keys; base profile
fields not mentioned in any override retain their parent values.

No existing logic is modified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.constants import RoleId

# ------------------------------------------------------------------
# RoleOverlay
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class RoleOverlay:
    """Role叠加层：继承 foundation profile + role-specific overrides.

    A RoleOverlay refines a parent StrategyProfile for a specific role
    and execution context (e.g. "director doing code execution" vs
    "director writing documentation").

    Attributes:
        role:
            Role name, e.g. ``"director"``, ``"pm"``, ``"architect"``,
            ``"qa"``.
        parent_profile_id:
            Which foundation profile this overlay extends.
            Must exist in the StrategyRegistry at resolve time.
        overlay_id:
            Unique identifier within the role namespace.
            Format: ``"{role}.{variant}"``, e.g. ``"director.execution"``.
        exploration_overrides:
            Override keys for the ``exploration`` sub-strategy.
            Keys not present here are inherited from the parent.
        read_escalation_overrides:
            Override keys for the ``read_escalation`` sub-strategy.
        compaction_overrides:
            Override keys for the ``compaction`` sub-strategy.
        cache_overrides:
            Override keys for the ``cache`` sub-strategy.
        history_overrides:
            Override keys for the ``history_materialization`` sub-strategy.
        session_continuity_overrides:
            Override keys for the ``session_continuity`` sub-strategy.
        metadata:
            Human-readable description of what this overlay targets.

    Example::

        director_execution = RoleOverlay(
            role="director",
            parent_profile_id="canonical_balanced",
            overlay_id="director.execution",
            exploration_overrides={
                "map_first": True,
                "max_expansion_depth": 3,
            },
            compaction_overrides={
                "trigger_at_budget_pct": 0.80,
            },
        )
    """

    role: RoleId | str  # RoleId preferred; str allowed for backward compatibility
    parent_profile_id: str
    overlay_id: str
    exploration_overrides: dict[str, Any] = field(default_factory=dict)
    read_escalation_overrides: dict[str, Any] = field(default_factory=dict)
    compaction_overrides: dict[str, Any] = field(default_factory=dict)
    cache_overrides: dict[str, Any] = field(default_factory=dict)
    history_overrides: dict[str, Any] = field(default_factory=dict)
    session_continuity_overrides: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)

    def overrides_by_strategy(self) -> dict[str, dict[str, Any]]:
        """Return a flat dict keyed by strategy name for registry merge.

        Returns:
            Dict mapping strategy name to its override dict.
            Strategies with empty overrides are omitted.
        """
        result: dict[str, dict[str, Any]] = {}
        if self.exploration_overrides:
            result["exploration"] = self.exploration_overrides
        if self.read_escalation_overrides:
            result["read_escalation"] = self.read_escalation_overrides
        if self.compaction_overrides:
            result["compaction"] = self.compaction_overrides
        if self.cache_overrides:
            result["cache"] = self.cache_overrides
        if self.history_overrides:
            result["history_materialization"] = self.history_overrides
        if self.session_continuity_overrides:
            result["session_continuity"] = self.session_continuity_overrides
        return result

    def effective_profile_id(self) -> str:
        """Return the effective profile identifier after overlay application.

        Used as the resolved ``profile_id`` in the final ResolvedStrategy.
        """
        return self.overlay_id


# ------------------------------------------------------------------
# ResolvedOverlayStrategy
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ResolvedOverlayStrategy:
    """A RoleOverlay resolved against its parent profile.

    Produced by RoleOverlayRegistry.resolve().
    """

    overlay: RoleOverlay
    parent_profile_id: str
    effective_overrides: dict[str, Any] = field(default_factory=dict)
    profile_id: str = ""

    def __post_init__(self) -> None:
        if not self.profile_id:
            object.__setattr__(self, "profile_id", self.overlay.overlay_id)


__all__ = [
    "ResolvedOverlayStrategy",
    "RoleOverlay",
]
