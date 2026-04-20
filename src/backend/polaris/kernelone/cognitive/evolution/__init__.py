"""Evolution Layer - Belief tracking and continuous learning."""

from polaris.kernelone.cognitive.evolution.belief_decay import (
    BeliefDecayEngine,
    DecayPolicy,
)
from polaris.kernelone.cognitive.evolution.bias_defense import (
    BiasDefenseEngine,
    BiasDetectionResult,
)
from polaris.kernelone.cognitive.evolution.engine import EvolutionEngine
from polaris.kernelone.cognitive.evolution.integrity import EvolutionIntegrityGuard
from polaris.kernelone.cognitive.evolution.knowledge_precipitation import (
    KnowledgePrecipitation,
    PrecipitatedKnowledge,
)
from polaris.kernelone.cognitive.evolution.store import EvolutionStore

__all__ = [
    "BeliefDecayEngine",
    "BiasDefenseEngine",
    "BiasDetectionResult",
    "DecayPolicy",
    "EvolutionEngine",
    "EvolutionIntegrityGuard",
    "EvolutionStore",
    "KnowledgePrecipitation",
    "PrecipitatedKnowledge",
]
