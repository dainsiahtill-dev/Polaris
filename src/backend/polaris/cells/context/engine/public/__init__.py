"""Public boundary for `context.engine`."""

from polaris.kernelone.context.contracts import ContextBudget
from polaris.kernelone.context.engine import (
    ContextCache,
    ContextEngine,
    ContextItem,
    ContextPack,
    ContextRequest,
)

from .contracts import (
    BuildRoleContextCommandV1,
    ContextEngineError,
    ContextResolvedEventV1,
    ResolveRoleContextQueryV1,
    RoleContextResultV1,
)
from .precision_mode import (
    CostStrategy,
    merge_policy,
    normalize_cost_class,
    resolve_cost_class,
    route_by_cost_model,
)
from .service import (
    build_context_window,
    get_anthropomorphic_context_v2,
)

__all__ = [
    "BuildRoleContextCommandV1",
    "ContextBudget",
    "ContextCache",
    "ContextEngine",
    "ContextEngineError",
    "ContextItem",
    "ContextPack",
    "ContextRequest",
    "ContextResolvedEventV1",
    "CostStrategy",
    "ResolveRoleContextQueryV1",
    "RoleContextResultV1",
    "build_context_window",
    "get_anthropomorphic_context_v2",
    "merge_policy",
    "normalize_cost_class",
    "resolve_cost_class",
    "route_by_cost_model",
]
