"""Role Overlay registry — singleton registry for RoleOverlay instances.

Provides registration, lookup, and full strategy resolution by
merging a RoleOverlay on top of its parent StrategyProfile.

No existing logic is modified.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.context.strategy_overlay_contracts import (
    ResolvedOverlayStrategy,
    RoleOverlay,
)

if TYPE_CHECKING:
    from polaris.kernelone.context.strategy_contracts import (
        ResolvedStrategy,
    )

_logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge overlay dict into base, returning a new dict.

    Nested dicts are merged recursively; scalar values are replaced.
    """
    result = dict(base)
    for key, val in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _resolve_overlay_hash(overlay: RoleOverlay, parent_overrides: dict[str, Any]) -> str:
    """Compute a stable SHA-256 hash of a resolved overlay profile.

    Covers overlay_id + parent_profile_id + effective overrides.
    Used for receipt identity and A/B comparison.
    """
    payload = {
        "overlay_id": overlay.overlay_id,
        "role": overlay.role,
        "parent_profile_id": overlay.parent_profile_id,
        "effective_overrides": overlay.overrides_by_strategy(),
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------
# RoleOverlayRegistry
# ------------------------------------------------------------------


class RoleOverlayRegistry:
    """Singleton registry for RoleOverlay instances.

    Supports DI: pass an existing registry instance to bypass singleton.

    Usage::

        reg = RoleOverlayRegistry()
        reg.register(director_execution)
        resolved = reg.resolve(role="director", parent_profile_id="canonical_balanced")

        # Or use the module-level accessor:
        from polaris.kernelone.context.strategy_overlay_registry import get_overlay_registry
        reg = get_overlay_registry()
    """

    _instance: RoleOverlayRegistry | None = None

    def __init__(self) -> None:
        # Keyed by overlay_id (unique)
        self._overlays: dict[str, RoleOverlay] = {}
        # Index: role -> list of overlay_ids
        self._role_index: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # Singleton access
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> RoleOverlayRegistry:
        """Return the global singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._register_builtins()
        return cls._instance

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, overlay: RoleOverlay) -> None:
        """Register a RoleOverlay.

        Overwrites an existing overlay with the same overlay_id.
        """
        if not overlay.overlay_id:
            raise ValueError("overlay_id must not be empty")
        if not overlay.role:
            raise ValueError("role must not be empty")

        self._overlays[overlay.overlay_id] = overlay

        # Update role index
        role = overlay.role
        if role not in self._role_index:
            self._role_index[role] = []
        if overlay.overlay_id not in self._role_index[role]:
            self._role_index[role].append(overlay.overlay_id)

        _logger.debug(
            "Registered RoleOverlay: %s (role=%s, parent=%s)",
            overlay.overlay_id,
            overlay.role,
            overlay.parent_profile_id,
        )

    def _register_builtins(self) -> None:
        """Pre-populate the registry with built-in overlays.

        Called automatically when the singleton is first created.
        Overlays are imported lazily here to avoid circular imports.
        """
        # Import here to avoid circular import at module level
        try:
            from polaris.kernelone.context.strategy_overlay_definitions import (
                BUILTIN_OVERLAYS,
            )

            for overlay in BUILTIN_OVERLAYS.values():
                self.register(overlay)
        except ImportError:  # pragma: no cover
            # Bootstrap case: definitions not yet created
            _logger.debug("No builtin overlays found at startup.")

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get(self, overlay_id: str) -> RoleOverlay:
        """Return the named overlay. Raises KeyError if not found."""
        if overlay_id not in self._overlays:
            raise KeyError(f"Unknown overlay_id: {overlay_id!r}")
        return self._overlays[overlay_id]

    def list_for_role(self, role: str) -> list[RoleOverlay]:
        """Return all overlays registered for a given role, in registration order."""
        ids = self._role_index.get(role, [])
        return [self._overlays[oid] for oid in ids if oid in self._overlays]

    def list_all(self) -> list[RoleOverlay]:
        """Return all registered overlays, in registration order."""
        return list(self._overlays.values())

    def overlay_ids(self) -> list[str]:
        """Return all registered overlay IDs."""
        return list(self._overlays.keys())

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(
        self,
        role: str,
        parent_profile_id: str,
        domain: str | None = None,
        parent_overrides: dict[str, Any] | None = None,
        explicit_override: dict[str, Any] | None = None,
    ) -> ResolvedOverlayStrategy:
        """Resolve a RoleOverlay for a role + parent profile.

        Resolution order:
            1. Find the overlay matching (role, parent_profile_id).
               If multiple overlays match the role, the one whose
               parent_profile_id matches exactly is preferred;
               otherwise the first registered overlay for the role is used.
            2. Deep-merge overlay strategy overrides on top of parent_overrides.
            3. Apply explicit_override last (caller-supplied overrides).

        Args:
            role: Role name (e.g. "director").
            parent_profile_id: Parent foundation profile (e.g. "canonical_balanced").
            domain: Optional execution domain ("code"/"document"/...).
                When provided, overlays with matching metadata.target_domain
                are preferred.
            parent_overrides: Pre-existing overrides from the parent profile
                (e.g. from StrategyRegistry.resolve).
            explicit_override: Caller-supplied overrides merged last.

        Returns:
            ResolvedOverlayStrategy with the effective merged overrides
            and the resolved profile ID.

        Raises:
            KeyError: If no overlay is registered for the given role
                and parent_profile_id combination.
        """
        # Step 1: find the overlay
        overlay = self._find_overlay(role, parent_profile_id, domain=domain)
        if overlay is None:
            raise KeyError(f"No RoleOverlay registered for role={role!r}, parent_profile_id={parent_profile_id!r}")

        # Step 2: base overrides = parent_overrides (already from the parent profile)
        base = dict(parent_overrides) if parent_overrides else {}

        # Step 3: merge overlay strategy overrides
        overlay_strategies = overlay.overrides_by_strategy()
        merged: dict[str, Any] = _deep_merge(base, overlay_strategies)

        # Step 4: apply explicit caller override last
        if explicit_override:
            merged = _deep_merge(merged, explicit_override)

        # Step 5: build the resolved overlay strategy
        resolved = ResolvedOverlayStrategy(
            overlay=overlay,
            parent_profile_id=overlay.parent_profile_id,
            effective_overrides=merged,
            profile_id=overlay.overlay_id,
        )
        return resolved

    def _find_overlay(
        self,
        role: str,
        parent_profile_id: str,
        domain: str | None = None,
    ) -> RoleOverlay | None:
        """Find the best matching overlay for a role + parent_profile_id.

        Preference order:
            1. Exact (role, parent_profile_id, target_domain) match
            2. Domain-only match within role overlays
            3. Exact (role, parent_profile_id) match
            4. First registered overlay for the role
        """
        candidates = self.list_for_role(role)
        if not candidates:
            return None

        domain_token = str(domain or "").strip().lower()

        # Exact parent + domain first
        if domain_token:
            for oc in candidates:
                target_domain = str((oc.metadata or {}).get("target_domain") or "").strip().lower()
                if oc.parent_profile_id == parent_profile_id and target_domain == domain_token:
                    return oc

            # If parent doesn't match, still honor explicit domain intent.
            for oc in candidates:
                target_domain = str((oc.metadata or {}).get("target_domain") or "").strip().lower()
                if target_domain == domain_token:
                    return oc

        # Exact parent_profile_id match
        for oc in candidates:
            if oc.parent_profile_id == parent_profile_id:
                return oc

        # Fall back to first registered overlay for this role
        _logger.debug(
            "No overlay for role=%r with parent=%r and domain=%r; falling back to first overlay %r",
            role,
            parent_profile_id,
            domain_token,
            candidates[0].overlay_id,
        )
        return candidates[0]

    def resolve_full(
        self,
        role: str,
        parent_profile_id: str,
        parent_strategy: ResolvedStrategy,
        domain: str | None = None,
        explicit_override: dict[str, Any] | None = None,
    ) -> ResolvedStrategy:
        """Resolve a full ResolvedStrategy by combining parent + overlay.

        This is the canonical entry point used by RoleRuntimeService:
            1. Start from the parent's ResolvedStrategy (from StrategyRegistry).
            2. Apply RoleOverlay overrides on top.
            3. Recompute the profile hash with the merged overrides.

        Args:
            role: Role name.
            parent_profile_id: Parent foundation profile ID.
            parent_strategy: Pre-resolved strategy from StrategyRegistry.
            domain: Optional execution domain used for overlay preference.
            explicit_override: Optional caller overrides merged last.

        Returns:
            ResolvedStrategy with overlay applied.
        """
        from polaris.kernelone.context.strategy_contracts import (
            ResolvedStrategy,
            StrategyProfile,
        )
        from polaris.kernelone.context.strategy_registry import (
            resolve_profile_hash,
        )

        # Resolve the overlay layer
        overlay_resolved = self.resolve(
            role=role,
            parent_profile_id=parent_profile_id,
            domain=domain,
            parent_overrides=parent_strategy.overrides_applied,
            explicit_override=explicit_override,
        )

        # Build the effective StrategyProfile
        effective_profile = StrategyProfile(
            profile_id=overlay_resolved.profile_id,
            profile_version=overlay_resolved.overlay.parent_profile_id + ".overlay.1",
            bundle_id=parent_strategy.bundle.bundle_id,
            overrides=overlay_resolved.effective_overrides,
            metadata=parent_strategy.profile.metadata,
        )

        # Recompute profile hash with overlay applied
        new_hash = resolve_profile_hash(effective_profile)

        return ResolvedStrategy(
            profile=effective_profile,
            bundle=parent_strategy.bundle,
            profile_hash=new_hash,
            overrides_applied=overlay_resolved.effective_overrides,
        )

    # ------------------------------------------------------------------
    # Reset (for testing only)
    # ------------------------------------------------------------------

    @classmethod
    def _reset_instance(cls) -> None:
        """Reset the singleton — for unit tests only."""
        cls._instance = None


# ------------------------------------------------------------------
# Module-level singleton accessor
# ------------------------------------------------------------------


def get_overlay_registry() -> RoleOverlayRegistry:
    """Return the global RoleOverlayRegistry singleton."""
    return RoleOverlayRegistry.get_instance()


__all__ = [
    "RoleOverlayRegistry",
    "get_overlay_registry",
]
