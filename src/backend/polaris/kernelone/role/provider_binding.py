"""Provider Dynamic Binding for Tri-Axis Role Engine.

This module enables professions to declare their preferred LLM providers
and supports Anchor-level overrides.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ProviderTier(Enum):
    """Provider tier levels."""

    PREMIUM = "premium"  # e.g., Claude Opus, GPT-4
    STANDARD = "standard"  # e.g., Claude Sonnet, GPT-4-Turbo
    ECONOMY = "economy"  # e.g., GPT-3.5, Claude Haiku


@dataclass
class ProviderBinding:
    """Defines provider requirements for a profession."""

    primary: str
    fallback: str | None = None
    complexity_threshold: str = "medium"  # low, medium, high
    tier: ProviderTier = ProviderTier.STANDARD

    @property
    def all_providers(self) -> list[str]:
        """Get all providers in priority order."""
        providers = [self.primary]
        if self.fallback:
            providers.append(self.fallback)
        return providers


@dataclass
class AnchorOverride:
    """Defines an override rule for a specific anchor."""

    anchor_id: str
    trigger_condition: str  # e.g., "task_complexity > threshold"
    override_provider: str
    override_tier: ProviderTier | None = None


@dataclass
class ProviderResolver:
    """Resolves the appropriate provider for a given context."""

    provider_bindings: dict[str, ProviderBinding] = field(default_factory=dict)
    anchor_overrides: list[AnchorOverride] = field(default_factory=list)
    default_binding: ProviderBinding | None = None

    def register_profession_provider(
        self,
        profession_id: str,
        binding: ProviderBinding,
    ) -> None:
        """Register a provider binding for a profession."""
        self.provider_bindings[profession_id] = binding
        logger.debug(f"Registered provider binding for profession: {profession_id}")

    def register_anchor_override(self, override: AnchorOverride) -> None:
        """Register an anchor-level provider override."""
        self.anchor_overrides.append(override)
        logger.debug(f"Registered anchor override for: {override.anchor_id}")

    def get_binding(self, profession_id: str) -> ProviderBinding | None:
        """Get the provider binding for a profession."""
        return self.provider_bindings.get(profession_id)

    def resolve(
        self,
        profession_id: str,
        anchor_id: str | None = None,
        task_complexity: str = "medium",
    ) -> ProviderBinding:
        """Resolve the appropriate provider for a context.

        Args:
            profession_id: The profession requesting the provider
            anchor_id: Optional anchor ID for override checking
            task_complexity: Task complexity level (low, medium, high)

        Returns:
            ProviderBinding with the resolved provider
        """
        # Start with profession's declared binding
        binding = self.provider_bindings.get(profession_id)

        # Check for anchor overrides
        if anchor_id:
            for override in self.anchor_overrides:
                if override.anchor_id == anchor_id and self._evaluate_condition(
                    override.trigger_condition, task_complexity
                ):
                    logger.info(f"Anchor override applied: {profession_id} -> {override.override_provider}")
                    return ProviderBinding(
                        primary=override.override_provider,
                        fallback=binding.fallback if binding else None,
                        tier=override.override_tier or ProviderTier.STANDARD,
                    )

        # Return profession's binding or default
        if binding:
            return binding

        if self.default_binding:
            return self.default_binding

        # Fallback to standard
        return ProviderBinding(
            primary="openai/gpt-4-turbo",
            fallback="anthropic/claude-sonnet",
            tier=ProviderTier.STANDARD,
        )

    def _evaluate_condition(self, condition: str, task_complexity: str) -> bool:
        """Evaluate a trigger condition.

        Supported conditions:
        - "task_complexity > threshold" (threshold: low, medium, high)
        - "task_complexity >= threshold"
        - "always"
        """
        if condition == "always":
            return True

        if "task_complexity" in condition:
            # Parse complexity comparison
            try:
                if ">=" in condition:
                    _, threshold = condition.split(">=")
                    threshold = threshold.strip()
                    return self._complexity_rank(task_complexity) >= self._complexity_rank(threshold)
                elif ">" in condition:
                    _, threshold = condition.split(">")
                    threshold = threshold.strip()
                    return self._complexity_rank(task_complexity) > self._complexity_rank(threshold)
            except ValueError:
                logger.warning(f"Failed to parse condition: {condition}")
                return False

        return False

    @staticmethod
    def _complexity_rank(complexity: str) -> int:
        """Get numeric rank for complexity level."""
        ranks = {"low": 1, "medium": 2, "high": 3}
        return ranks.get(complexity.lower(), 2)


# Global provider resolver instance
_provider_resolver: ProviderResolver | None = None


def get_provider_resolver() -> ProviderResolver:
    """Get the global ProviderResolver instance."""
    global _provider_resolver
    if _provider_resolver is None:
        _provider_resolver = ProviderResolver()
    return _provider_resolver


def init_provider_resolver_from_config() -> ProviderResolver:
    """Initialize the provider resolver from profession configurations.

    This loads provider bindings from all registered profession configurations.
    """
    from polaris.kernelone.role import get_profession_loader

    resolver = get_provider_resolver()

    # Load all profession configurations
    loader = get_profession_loader()
    professions = [
        "python_principal_architect",
        "security_auditor",
        "software_engineer",
        "project_manager",
        "quality_engineer",
    ]

    for profession_id in professions:
        profession = loader.load(profession_id)
        if profession and profession.provider:
            binding = ProviderBinding(
                primary=profession.provider.get("primary", "openai/gpt-4-turbo"),
                fallback=profession.provider.get("fallback"),
                complexity_threshold=profession.provider.get("complexity_threshold", "medium"),
            )
            resolver.register_profession_provider(profession_id, binding)

    return resolver
