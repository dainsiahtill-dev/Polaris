"""Tests for polaris.cells.llm.control_plane.internal.inference_engine."""

from __future__ import annotations

import pytest
from polaris.cells.llm.control_plane.internal.inference_engine import (
    InferenceEngine,
    InferenceEngineNotConfiguredError,
    create_inference_engine,
    get_inference_engine,
)


class TestInferenceEngine:
    def test_initial_state(self) -> None:
        engine = InferenceEngine()
        assert engine.sglang_runtime is None
        assert engine.outlines_model is None

    def test_initialize_returns_false(self) -> None:
        engine = InferenceEngine()
        result = engine.initialize()
        assert result is False
        assert engine.sglang_runtime is None
        assert engine.outlines_model is None

    def test_generate_structured_raises(self) -> None:
        engine = InferenceEngine()
        with pytest.raises(InferenceEngineNotConfiguredError) as exc_info:
            engine.generate_structured("prompt", "schema")
        assert "not implemented" in str(exc_info.value)

    def test_generate_chat_raises(self) -> None:
        engine = InferenceEngine()
        with pytest.raises(InferenceEngineNotConfiguredError) as exc_info:
            engine.generate_chat([{"role": "user", "content": "hello"}])
        assert "not implemented" in str(exc_info.value)


class TestInferenceEngineFactory:
    def test_create_inference_engine_returns_new_instance(self) -> None:
        engine1 = create_inference_engine()
        engine2 = create_inference_engine()
        assert engine1 is not engine2

    def test_get_inference_engine_returns_singleton(self) -> None:
        engine1 = get_inference_engine()
        engine2 = get_inference_engine()
        assert engine1 is engine2
