"""Strategy registry — singleton registry with DI support.

Profiles are registered by profile_id.
Resolution respects the cascade: explicit session override → domain default → role overlay → canonical.

Zero behavior drift: this module provides the framework only.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from polaris.kernelone.constants import RoleId

from .strategy_contracts import (
    ResolvedStrategy,
    StrategyBundle,
    StrategyProfile,
)
from .strategy_profiles import BUILTIN_PROFILES

_logger = logging.getLogger(__name__)


def _stable_repr(val: Any) -> str:
    """Render a value as a stable string for SHA-256 hashing."""
    return json.dumps(val, sort_keys=True, ensure_ascii=False)


# Canonical bundle identity
CANONICAL_BUNDLE = StrategyBundle(
    bundle_id="kernelone.default.v1",
    bundle_version="1.0.0",
)


# ------------------------------------------------------------------
# StrategyRegistry
# ------------------------------------------------------------------


class StrategyRegistry:
    """Singleton registry for strategy profiles.

    Supports both module-level singleton and constructor injection (for tests).

    Profile resolution cascade (highest → lowest priority):
        1. explicit session override (passed at resolve time)
        2. role overlay (matched by role name)
        3. domain default (matched by domain name)
        4. global canonical (canonical_balanced)

    Usage::

        registry = get_registry()
        resolved = registry.resolve(domain="code", role="director")
        print(resolved.profile.profile_id)          # e.g. "canonical_balanced"
        print(resolved.profile_hash)                # stable hash
    """

    _instance: StrategyRegistry | None = None

    def __init__(self) -> None:
        self._profiles: dict[str, StrategyProfile] = {}
        self._bundles: dict[str, StrategyBundle] = {"kernelone.default.v1": CANONICAL_BUNDLE}
        # Register built-in profiles
        for profile in BUILTIN_PROFILES.values():
            self.register(profile)

    # ------------------------------------------------------------------
    # Singleton access
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> StrategyRegistry:
        """Return the singleton instance, creating it if needed."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton. Use only in tests."""
        cls._instance = None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, profile: StrategyProfile) -> None:
        """Register a profile by profile_id. Overwrites existing entry."""
        if profile.profile_id in self._profiles:
            _logger.debug("Overwriting existing profile: %s", profile.profile_id)
        self._profiles[profile.profile_id] = profile
        _logger.debug("Registered profile: %s", profile.profile_id)

    def register_bundle(self, bundle: StrategyBundle) -> None:
        """Register a bundle by bundle_id."""
        self._bundles[bundle.bundle_id] = bundle

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, profile_id: str) -> StrategyProfile:
        """Get a profile by profile_id. Raises KeyError if not found."""
        if profile_id not in self._profiles:
            available = ", ".join(sorted(self._profiles.keys())) or "(none)"
            raise KeyError(f"Profile '{profile_id}' not found. Available: {available}")
        return self._profiles[profile_id]

    def get_bundle(self, bundle_id: str) -> StrategyBundle:
        """Get a bundle by bundle_id. Raises KeyError if not found."""
        if bundle_id not in self._bundles:
            available = ", ".join(sorted(self._bundles.keys())) or "(none)"
            raise KeyError(f"Bundle '{bundle_id}' not found. Available: {available}")
        return self._bundles[bundle_id]

    def list_profiles(self) -> list[StrategyProfile]:
        """Return all registered profiles, sorted by profile_id."""
        return sorted(self._profiles.values(), key=lambda p: p.profile_id)

    def list_profile_ids(self) -> list[str]:
        """Return all registered profile_ids, sorted."""
        return sorted(self._profiles.keys())

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(
        self,
        profile_id: str | None = None,
        domain: str | None = None,
        role: str | None = None,
        override: dict[str, Any] | None = None,
    ) -> ResolvedStrategy:
        """Resolve the effective strategy for a run.

        Resolution order (highest → lowest priority):
            1. explicit profile_id
            2. role-specific default
            3. domain-specific default
            4. canonical_balanced

        Args:
            profile_id: Explicit profile selection (bypasses all defaults).
            domain: Target domain (e.g. "code", "document"). Used for domain defaults.
            role: Role name (e.g. "director", "pm"). Used for role-specific defaults.
            override: Session-level override dict merged on top of resolved profile.

        Returns:
            ResolvedStrategy with profile, bundle, and stable profile_hash.

        Raises:
            KeyError: if the resolved profile_id is not registered.
        """
        resolved_id = self._resolve_profile_id(profile_id=profile_id, domain=domain, role=role)
        base_profile = self.get(resolved_id)

        # Apply session-level overrides on top of resolved profile
        merged_overrides = dict(base_profile.overrides)
        if override:
            merged_overrides = self._deep_merge(merged_overrides, override)

        # Build effective profile with overrides applied
        effective = StrategyProfile(
            profile_id=base_profile.profile_id,
            profile_version=base_profile.profile_version,
            bundle_id=base_profile.bundle_id,
            overrides=merged_overrides,
            metadata=base_profile.metadata,
        )

        bundle = self.get_bundle(base_profile.bundle_id)
        profile_hash = self._resolve_profile_hash(effective)

        return ResolvedStrategy(
            profile=effective,
            bundle=bundle,
            profile_hash=profile_hash,
            overrides_applied=merged_overrides,
        )

    def resolve_profile_hash(self, profile: StrategyProfile) -> str:
        """Generate a stable SHA-256 hash for a resolved profile.

        The hash covers: profile_id + profile_version + bundle_id + overrides (sorted).
        This is used as the stable strategy identity in receipts.
        """
        return self._resolve_profile_hash(profile)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_profile_id(
        self,
        profile_id: str | None,
        domain: str | None,
        role: str | None,
    ) -> str:
        """Select the effective profile_id."""
        # 1. Explicit takes precedence
        if profile_id and profile_id in self._profiles:
            return profile_id

        # 2. Role-specific defaults (extensible; currently maps role → profile)
        if role and role in _ROLE_DEFAULTS:
            default = _ROLE_DEFAULTS[role]
            if default in self._profiles:
                return default

        # 3. Domain-specific defaults
        if domain and domain in _DOMAIN_DEFAULTS:
            default = _DOMAIN_DEFAULTS[domain]
            if default in self._profiles:
                return default

        # 4. Canonical fallback
        return "canonical_balanced"

    def _resolve_profile_hash(self, profile: StrategyProfile) -> str:
        """Compute stable SHA-256 for a profile's resolved config."""
        # Canonical serialization for hashing
        parts = [
            f"pid={profile.profile_id}",
            f"pver={profile.profile_version}",
            f"bid={profile.bundle_id}",
        ]
        # Sort overrides keys for deterministic output
        for key in sorted(profile.overrides.keys()):
            val = profile.overrides[key]
            parts.append(f"{key}={_stable_repr(val)}")
        raw = "|".join(parts).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """Deep-merge override dict into base dict."""
        result = dict(base)
        for key, val in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(val, dict):
                result[key] = StrategyRegistry._deep_merge(result[key], val)
            else:
                result[key] = val
        return result


# ------------------------------------------------------------------
# Domain / Role default mappings
# ------------------------------------------------------------------

# Maps domain → default profile_id
_DOMAIN_DEFAULTS: dict[str, str] = {
    "code": "canonical_balanced",
    "document": "speed_first",
    "fiction": "speed_first",
    "research": "deep_research",
}

# Maps role → default profile_id
# Note: Using RoleId enum values as keys where available, with string fallbacks
# for extensibility (e.g., "coder", "writer" are not Polaris roles).
_ROLE_DEFAULTS: dict[RoleId | str, str] = {
    # Execution family
    RoleId.DIRECTOR: "canonical_balanced",
    "coder": "canonical_balanced",
    "writer": "speed_first",
    # Governance
    RoleId.PM: "canonical_balanced",
    RoleId.ARCHITECT: "deep_research",
    RoleId.CHIEF_ENGINEER: "deep_research",
    RoleId.QA: "canonical_balanced",
}


# ------------------------------------------------------------------
# Module-level singleton accessor
# ------------------------------------------------------------------


def get_registry() -> StrategyRegistry:
    """Return the global StrategyRegistry singleton.

    This is the preferred entry point for production code.
    Tests should construct StrategyRegistry() directly or use reset_instance().
    """
    return StrategyRegistry.get_instance()


# ------------------------------------------------------------------
# Backward-compat alias for StrategyRegistry in old code
# ------------------------------------------------------------------


def resolve_bundle_hash(bundle: StrategyBundle) -> str:
    """Compute a stable hash for a bundle definition."""
    raw = f"bid={bundle.bundle_id}|bver={bundle.bundle_version}".encode()
    return hashlib.sha256(raw).hexdigest()


def resolve_profile_hash(profile: StrategyProfile) -> str:
    """Compute a stable hash for a profile's resolved config.

    Convenience wrapper that uses a temporary registry instance.
    """
    return StrategyRegistry().resolve_profile_hash(profile)


def reset_instance() -> None:
    """Reset the global singleton. Tests only."""
    StrategyRegistry.reset_instance()  # type: ignore[arg-type]


__all__ = [
    "CANONICAL_BUNDLE",
    "ResolvedStrategy",
    "StrategyBundle",
    "StrategyProfile",
    "StrategyRegistry",
    "get_registry",
    "reset_instance",
    "resolve_bundle_hash",
    "resolve_profile_hash",
]
