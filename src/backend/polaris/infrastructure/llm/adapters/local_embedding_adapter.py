from __future__ import annotations

import logging

from polaris.kernelone.llm.embedding import KernelEmbeddingPort

logger = logging.getLogger(__name__)


class LocalTransformerEmbeddingAdapter(KernelEmbeddingPort):
    """Embedding adapter using local sentence-transformers and torch."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self.model_name = model_name
        try:
            import torch
            from sentence_transformers import SentenceTransformer

            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info(f"Initializing local embedding model {model_name} on {self.device}")
            self.model = SentenceTransformer(model_name, device=self.device)
        except ImportError as exc:
            logger.error("sentence-transformers or torch not installed. Cannot use local embedding.")
            raise RuntimeError("Required libraries for LocalTransformerEmbeddingAdapter missing.") from exc

    def get_embedding(self, text: str, model: str | None = None) -> list[float]:
        # Ignores model param if using fixed local model
        return self.model.encode(text).tolist()

    def get_fingerprint(self) -> str:
        return f"sentence-transformers/{self.model_name}:{self.device}"
