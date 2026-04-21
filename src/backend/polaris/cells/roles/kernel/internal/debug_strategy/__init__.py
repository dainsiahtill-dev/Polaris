"""Debug Strategy Package - 调试策略包。

基于Superpowers的"Systematic Debugging"设计精华，
为Polaris提供原生的系统化调试能力。
"""

from polaris.cells.roles.kernel.internal.debug_strategy.evidence_collector import (
    EvidenceCollector,
)
from polaris.cells.roles.kernel.internal.debug_strategy.hypothesis_generator import (
    HypothesisGenerator,
)
from polaris.cells.roles.kernel.internal.debug_strategy.models import (
    DebugPlan,
    DebugStep,
    ErrorClassification,
    ErrorContext,
)
from polaris.cells.roles.kernel.internal.debug_strategy.strategy_engine import (
    DebugStrategyEngine,
)
from polaris.cells.roles.kernel.internal.debug_strategy.types import (
    DebugPhase,
    DebugStrategy,
    ErrorCategory,
)

__all__ = [
    "DebugPhase",
    "DebugPlan",
    "DebugStep",
    "DebugStrategy",
    "DebugStrategyEngine",
    "ErrorCategory",
    "ErrorClassification",
    "ErrorContext",
    "EvidenceCollector",
    "HypothesisGenerator",
]
