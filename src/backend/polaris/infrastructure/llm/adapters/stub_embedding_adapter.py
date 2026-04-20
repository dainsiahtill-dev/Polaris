from __future__ import annotations

import random

from polaris.kernelone.llm.embedding import KernelEmbeddingPort


class StubEmbeddingAdapter(KernelEmbeddingPort):
    """Stub embedding adapter for testing and environments without AI infrastructure."""

    def get_embedding(self, text: str, model: str | None = None) -> list[float]:
        # Deterministic pseudo-random vector based on text
        seed = sum(ord(c) for c in text)
        rng = random.Random(seed)
        return [rng.uniform(-1, 1) for _ in range(384)]

    def get_fingerprint(self) -> str:
        return "stub/pseudo-random:cpu"
