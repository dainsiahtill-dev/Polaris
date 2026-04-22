from __future__ import annotations

import os

from polaris.infrastructure.llm.adapters.ollama_runtime_adapter import (
    OllamaRuntimeAdapter,
)
from polaris.kernelone.llm.embedding import KernelEmbeddingPort


class OllamaEmbeddingAdapter(KernelEmbeddingPort):
    """Embedding adapter backed by Ollama HTTP endpoint."""

    def __init__(self, default_model: str = "nomic-embed-text") -> None:
        self.default_model = str(default_model or "nomic-embed-text").strip()
        self.host = str(os.environ.get("OLLAMA_HOST", "http://120.24.117.59:11434")).strip()
        self._runtime = OllamaRuntimeAdapter()

    def get_embedding(self, text: str, model: str | None = None) -> list[float]:
        vector = self._runtime.embed(
            text=str(text or ""),
            model=str(model or self.default_model),
            timeout_seconds=30,
            host=self.host,
        )
        return vector if isinstance(vector, list) else []

    def get_fingerprint(self) -> str:
        return f"ollama/{self.default_model}"
