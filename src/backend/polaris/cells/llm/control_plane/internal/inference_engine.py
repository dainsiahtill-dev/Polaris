"""Inference Engine — optional local backends; primary LLM path is provider_runtime/dialogue.

Calling generation without a configured backend must **not** return synthetic success text.
"""

from __future__ import annotations

import importlib.util
import logging
from typing import Any

SGLANG_AVAILABLE = importlib.util.find_spec("sglang") is not None
OUTLINES_AVAILABLE = importlib.util.find_spec("outlines") is not None

logger = logging.getLogger(__name__)


class InferenceEngineNotConfiguredError(RuntimeError):
    """No local inference backend is configured or reachable."""


class InferenceEngine:
    """Optional local inference stack; defaults to fail-fast (no silent stubs)."""

    def __init__(self) -> None:
        self.sglang_runtime: Any = None
        self.outlines_model: Any = None

    def initialize(self, model_path: str = "meta-llama/Meta-Llama-3-8B-Instruct") -> bool:
        """Returns ``True`` only when a real backend is wired (currently none)."""
        if SGLANG_AVAILABLE:
            logger.info("InferenceEngine: SGLang present but not wired (%s).", model_path)
        if OUTLINES_AVAILABLE:
            logger.info("InferenceEngine: Outlines present but not wired (%s).", model_path)
        self.sglang_runtime = None
        self.outlines_model = None
        return False

    def generate_structured(self, prompt: str, schema: str) -> dict[str, Any] | None:
        raise InferenceEngineNotConfiguredError(
            "Local structured inference is not implemented. "
            "Use llm.provider_runtime / llm.dialogue with a configured provider, "
            "or wire Outlines/SGLang explicitly in InferenceEngine."
        )

    def generate_chat(self, history: list[dict[str, str]]) -> str:
        raise InferenceEngineNotConfiguredError(
            "Local chat inference is not implemented. "
            "Use llm.provider_runtime / llm.dialogue with a configured provider, "
            "or wire SGLang explicitly in InferenceEngine."
        )


_inference_engine_instance: InferenceEngine | None = None


def create_inference_engine() -> InferenceEngine:
    """Return a new inference engine instance (no shared mutable singleton)."""
    return InferenceEngine()


def get_inference_engine() -> InferenceEngine:
    """Backward-compatible lazy singleton (prefer create_inference_engine + DI)."""
    global _inference_engine_instance
    if _inference_engine_instance is None:
        _inference_engine_instance = InferenceEngine()
    return _inference_engine_instance
