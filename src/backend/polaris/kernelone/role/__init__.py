"""Role Composition Module.

Tri-Axis Role Composition Engine for Polaris.
"""

from polaris.kernelone.role.composer import (
    ComposedPrompt,
    PromptMetadata,
    RoleComposer,
    get_role_composer,
)
from polaris.kernelone.role.hotswap import (
    FallbackChain,
    HotSwapContext,
    HotSwapEngine,
    PromptModifier,
    SwapEvent,
    SwapReason,
    get_hot_swap_engine,
)
from polaris.kernelone.role.loaders import (
    AnchorConfig,
    AnchorLoader,
    PersonaConfig,
    PersonaLoader,
    ProfessionConfig,
    ProfessionLoader,
    RecipeConfig,
    RecipeLoader,
    get_anchor_loader,
    get_persona_loader,
    get_profession_loader,
    get_recipe_loader,
)
from polaris.kernelone.role.provider_binding import (
    AnchorOverride,
    ProviderBinding,
    ProviderResolver,
    ProviderTier,
    get_provider_resolver,
    init_provider_resolver_from_config,
)
from polaris.kernelone.role.schema_validator import (
    ConfigValidationError,
    SchemaValidator,
    validate_all_configs,
)
from polaris.kernelone.role.stage import (
    Stage,
    StageExecutionContext,
    StageTransition,
    StageType,
    TransitionCondition,
    WorkflowDefinition,
    create_workflow_from_config,
)

__all__ = [  # noqa: RUF022
    # Composer
    "ComposedPrompt",
    "get_role_composer",
    "PromptMetadata",
    "RoleComposer",
    # Loaders
    "AnchorConfig",
    "AnchorLoader",
    "get_anchor_loader",
    "get_persona_loader",
    "get_profession_loader",
    "get_recipe_loader",
    "PersonaConfig",
    "PersonaLoader",
    "ProfessionConfig",
    "ProfessionLoader",
    "RecipeConfig",
    "RecipeLoader",
    # Schema
    "ConfigValidationError",
    "SchemaValidator",
    "validate_all_configs",
    # Stage
    "create_workflow_from_config",
    "Stage",
    "StageExecutionContext",
    "StageTransition",
    "StageType",
    "TransitionCondition",
    "WorkflowDefinition",
    # Hot-swap
    "FallbackChain",
    "get_hot_swap_engine",
    "HotSwapContext",
    "HotSwapEngine",
    "PromptModifier",
    "SwapEvent",
    "SwapReason",
    # Provider Binding
    "AnchorOverride",
    "get_provider_resolver",
    "init_provider_resolver_from_config",
    "ProviderBinding",
    "ProviderResolver",
    "ProviderTier",
]
