"""llm.control_plane InferenceEngine must not return silent mock success."""

import pytest
from polaris.cells.llm.control_plane.internal.inference_engine import (
    InferenceEngine,
    InferenceEngineNotConfiguredError,
    create_inference_engine,
)


def test_generate_structured_raises_not_configured():
    eng = create_inference_engine()
    with pytest.raises(InferenceEngineNotConfiguredError):
        eng.generate_structured("p", "{}")


def test_generate_chat_raises_not_configured():
    eng = InferenceEngine()
    with pytest.raises(InferenceEngineNotConfiguredError):
        eng.generate_chat([{"role": "user", "content": "hi"}])


def test_initialize_returns_false_without_wired_backend():
    eng = InferenceEngine()
    assert eng.initialize() is False
