"""A/B prompt routing with weighted random sampling."""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class RouteDecision:
    variant: str
    random_value: float


class ABPromptRouter:
    """Route prompt requests by configured traffic weights."""

    def __init__(self, *, seed: int = 42) -> None:
        self._random = random.Random(seed)

    def route(self, weights: dict[str, float]) -> RouteDecision:
        if not weights:
            raise ValueError("weights cannot be empty")
        normalized: list[tuple[str, float]] = []
        total = 0.0
        for variant, weight in weights.items():
            w = max(0.0, float(weight))
            normalized.append((variant, w))
            total += w
        if total <= 0.0:
            raise ValueError("weights total must be positive")

        value = self._random.random() * total
        cumulative = 0.0
        for variant, weight in normalized:
            cumulative += weight
            if value <= cumulative:
                return RouteDecision(variant=variant, random_value=value / total)
        return RouteDecision(variant=normalized[-1][0], random_value=value / total)


__all__ = [
    "ABPromptRouter",
    "RouteDecision",
]
