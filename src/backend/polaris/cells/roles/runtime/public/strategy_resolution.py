"""Strategy-resolution methods for `RoleRuntimeService`.

Lossless split: this module holds ``_StrategyResolutionMixin`` — the per-turn
strategy-profile resolution, strategy-run construction, receipt emission, and
the overlay-aware ``resolve_strategy`` cascade that were previously defined
directly on ``RoleRuntimeService``. They are factored into a mixin so the
concrete class keeps every method as a real class attribute (preserving
monkeypatch / attribute-identity behavior) while their bodies live here.

The methods reach the rest of the service through ``self`` (resolved via the
MRO at runtime) plus stateless helpers imported from sibling modules. The one
hard reference to the concrete class — ``resolve_strategy`` calling the
domain-resolution classmethods — is resolved through a call-time import to avoid
an import cycle while preserving byte-identical symbol resolution.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.runtime.public.cognitive_strategy import _deep_merge_strategy_overrides
from polaris.cells.roles.runtime.public.persistence import (
    emit_strategy_receipt as _emit_strategy_receipt_impl,
    resolve_session_override as _resolve_session_override_impl,
)
from polaris.kernelone.context import (
    ContextBudget,
    ResolvedStrategy,
    StrategyRunContext,
    get_registry,
)

if TYPE_CHECKING:
    from pathlib import Path


class _StrategyResolutionMixin:
    """Strategy-profile resolution behavior for ``RoleRuntimeService``."""

    if TYPE_CHECKING:
        # Instance state initialised in ``RoleRuntimeService.__init__`` and
        # domain-resolution classmethods that remain on the concrete class.
        # Declared here so the ``self.*`` references typecheck without importing
        # the concrete class (which would create an import cycle).
        _turn_indices: dict[str, int]

        @classmethod
        def _resolve_execution_domain(
            cls,
            command_domain: str | None = None,
            context: Mapping[str, Any] | None = None,
            metadata: Mapping[str, Any] | None = None,
            role: str | None = None,
        ) -> tuple[str, bool]: ...

        @classmethod
        def _strategy_domain_from_execution(cls, execution_domain: str) -> str: ...

    def _next_turn_index(self, session_id: str | None) -> int:
        """Return and increment the turn counter for a session."""
        if not session_id:
            return 0
        idx = self._turn_indices.get(session_id, 0)
        self._turn_indices[session_id] = idx + 1
        return idx

    def _resolve_session_override(self, session_id: str) -> dict[str, Any] | None:
        """Read session strategy override from roles.session source-of-truth.

        Delegates to extracted persistence module for actual implementation.
        """
        return _resolve_session_override_impl(session_id)

    def resolve_strategy_profile(
        self,
        domain: str | None = None,
        role: str | None = None,
        session_override: dict[str, Any] | None = None,
        current_turn_override: Mapping[str, Any] | None = None,
        prefer_domain_default: bool = False,
    ) -> ResolvedStrategy:
        """Resolve the effective strategy profile for a run.

        Resolution order (StrategyRegistry.resolve):
            1. Explicit session_override (highest priority)
            2. Domain-specific default
            3. canonical_balanced fallback

        Args:
            domain: Target domain ("code", "document", "research", "general").
            role: Role name ("director", "pm", etc.).
            session_override: Session-level strategy override dict.
            prefer_domain_default: When True, domain default takes precedence
                over role default for the base profile selection.

        Returns:
            ResolvedStrategy with profile, bundle, and hash.
        """
        execution_domain, _ = self._resolve_execution_domain(
            command_domain=domain,
            role=role,
        )
        strategy_domain = self._strategy_domain_from_execution(execution_domain)
        registry = get_registry()
        merged_override = _deep_merge_strategy_overrides(session_override, current_turn_override)
        return registry.resolve(
            domain=strategy_domain,
            role=None if prefer_domain_default else role,
            override=merged_override or None,
        )

    def create_strategy_run(
        self,
        domain: str,
        role: str | None,
        session_id: str | None,
        budget: ContextBudget | None,
        workspace: str,
        domain_explicit: bool = False,
        include_session_override: bool = False,
        current_turn_override: Mapping[str, Any] | None = None,
    ) -> StrategyRunContext:
        """Create a per-turn StrategyRunContext with resolved strategy identity.

        This is the canonical constructor for a strategy run. Call before each
        LLM turn; emit the receipt after the turn completes.

        Args:
            domain: Target execution domain.
            role: Role name.
            session_id: Session ID (None for task/oneshot runs).
            budget: Current context budget snapshot.
            workspace: Workspace directory path.
            domain_explicit: Whether the caller explicitly requested a domain.
            include_session_override: When True, attempt to load session-level
                strategy override from roles.session source-of-truth.

        Returns:
            StrategyRunContext carrying strategy identity and mutable accumulators.
        """
        # Pull session-level override from roles.session if session_id is available.
        session_override: dict[str, Any] | None = None
        if include_session_override and session_id:
            session_override = self._resolve_session_override(session_id)

        execution_domain, _ = self._resolve_execution_domain(
            command_domain=domain,
            role=role,
        )
        resolved = self.resolve_strategy_profile(
            domain=execution_domain,
            role=role,
            session_override=session_override,
            current_turn_override=current_turn_override,
            prefer_domain_default=domain_explicit,
        )
        turn_index = self._next_turn_index(session_id)
        return StrategyRunContext.from_resolved(
            resolved,
            turn_index=turn_index,
            session_id=session_id or "",
            workspace=workspace,
            role=role,
            domain=execution_domain,
            budget=budget,
        )

    @staticmethod
    def emit_strategy_receipt(
        run_ctx: StrategyRunContext,
        workspace: str,
    ) -> Path:
        """Persist a strategy run's receipt to `<metadata_dir>/runtime/strategy_runs/`.

        Delegates to extracted persistence module for actual implementation.
        """
        return _emit_strategy_receipt_impl(run_ctx, workspace)

    @staticmethod
    def resolve_strategy(
        domain: str | None = None,
        role: str | None = None,
        overlay_id: str | None = None,
        session_override: dict[str, Any] | None = None,
    ) -> ResolvedStrategy:
        """Resolve the effective strategy for a role execution.

        Resolution cascade (highest → lowest priority):
            1. explicit session_override (caller-supplied overrides)
            2. role overlay (matched by role + target domain + parent profile)
            3. role-default profile (from StrategyRegistry._ROLE_DEFAULTS)
            4. domain-default profile (from StrategyRegistry._DOMAIN_DEFAULTS)
            5. canonical_balanced fallback

        Args:
            domain: Target domain (e.g. ``"code"``, ``"document"``).
            role: Role name (e.g. ``"director"``, ``"architect"``, ``"qa"``).
            overlay_id: Specific overlay to apply
                (e.g. ``"director.execution"``, ``"architect.analysis"``).
                If None, the RoleOverlayRegistry selects the best matching
                overlay for the resolved role-default profile.
            session_override: Caller-supplied overrides merged last.

        Returns:
            ResolvedStrategy with the fully resolved profile, bundle, and hash.
            When an overlay is applied, the returned profile_id is the overlay_id
            (e.g. ``"director.execution"``), not the parent profile id.

        Raises:
            KeyError: If the resolved profile or overlay is not found.
        """
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService
        from polaris.kernelone.context import (
            ResolvedStrategy,
            StrategyProfile,
            StrategyRegistry,
            get_overlay_registry,
        )

        execution_domain, domain_explicit = RoleRuntimeService._resolve_execution_domain(
            command_domain=domain,
            role=role,
        )
        strategy_domain = RoleRuntimeService._strategy_domain_from_execution(
            execution_domain,
        )

        # Step 1: resolve the base profile via StrategyRegistry
        registry = StrategyRegistry.get_instance()
        parent_strategy = registry.resolve(
            domain=strategy_domain,
            role=None if domain_explicit else role,
            override=None,
        )

        # Step 2: apply role overlay if available
        if role is not None:
            overlay_reg = get_overlay_registry()
            try:
                # Determine parent profile id from the resolved base strategy
                parent_profile_id = parent_strategy.profile.profile_id
                if overlay_id:
                    # Explicit overlay requested: look it up directly
                    overlay = overlay_reg.get(overlay_id)
                    # Verify it matches the requested role
                    if overlay.role != role:
                        raise KeyError(f"overlay {overlay_id!r} is for role {overlay.role!r}, not {role!r}")
                    # Merge overlay + session overrides on top of parent's effective overrides
                    from polaris.kernelone.context.strategy_overlay_registry import _deep_merge

                    merged_overrides: dict[str, Any] = _deep_merge(
                        parent_strategy.overrides_applied,
                        overlay.overrides_by_strategy(),
                    )
                    if session_override:
                        merged_overrides = _deep_merge(merged_overrides, session_override)

                    # Build the effective profile with overlay's overlay_id
                    effective_profile = StrategyProfile(
                        profile_id=overlay.overlay_id,
                        profile_version="overlay.1",
                        bundle_id=parent_strategy.bundle.bundle_id,
                        overrides=merged_overrides,
                        metadata=parent_strategy.profile.metadata,
                    )
                    new_hash = registry.resolve_profile_hash(effective_profile)
                    return ResolvedStrategy(
                        profile=effective_profile,
                        bundle=parent_strategy.bundle,
                        profile_hash=new_hash,
                        overrides_applied=merged_overrides,
                    )
                else:
                    # Auto-select: let the overlay registry find the best match
                    resolved = overlay_reg.resolve(
                        role=role,
                        parent_profile_id=parent_profile_id,
                        domain=execution_domain,
                        parent_overrides=parent_strategy.overrides_applied,
                        explicit_override=session_override,
                    )
                    # Build effective profile with overlay's overlay_id
                    effective_profile = StrategyProfile(
                        profile_id=resolved.profile_id,
                        profile_version="overlay.1",
                        bundle_id=parent_strategy.bundle.bundle_id,
                        overrides=resolved.effective_overrides,
                        metadata=parent_strategy.profile.metadata,
                    )
                    new_hash = registry.resolve_profile_hash(effective_profile)
                    return ResolvedStrategy(
                        profile=effective_profile,
                        bundle=parent_strategy.bundle,
                        profile_hash=new_hash,
                        overrides_applied=resolved.effective_overrides,
                    )
            except KeyError:
                # No overlay registered for this role; fall through to base profile
                pass

        # Step 3: no overlay found — return base profile with session override
        if session_override:
            return registry.resolve(
                domain=strategy_domain,
                role=None if domain_explicit else role,
                override=session_override,
            )
        return parent_strategy
