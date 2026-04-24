from __future__ import annotations

import asyncio
from typing import Any

from polaris.cells.llm.evaluation.public.service import run_connectivity_suite
from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelInfo, ModelListResult, Usage


class _OllamaSuccessProvider:
    def __init__(self) -> None:
        self.invoke_calls: list[dict[str, Any]] = []
        self.list_calls = 0

    def health(self, config: dict[str, Any]) -> HealthResult:
        return HealthResult(ok=True, latency_ms=12)

    def list_models(self, config: dict[str, Any]) -> ModelListResult:
        self.list_calls += 1
        return ModelListResult(
            ok=True,
            supported=True,
            models=[
                ModelInfo(id="glm-4.7-flash:latest"),
            ],
        )

    def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
        self.invoke_calls.append({"prompt": prompt, "model": model, "config": config})
        return InvokeResult(ok=True, output="OK", latency_ms=34, usage=Usage())


class _OllamaMissingModelProvider:
    def __init__(self) -> None:
        self.invoke_called = False

    def health(self, config: dict[str, Any]) -> HealthResult:
        return HealthResult(ok=True, latency_ms=10)

    def list_models(self, config: dict[str, Any]) -> ModelListResult:
        return ModelListResult(
            ok=True,
            supported=True,
            models=[ModelInfo(id="llama3.1:8b")],
        )

    def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
        self.invoke_called = True
        return InvokeResult(ok=True, output="OK", latency_ms=1, usage=Usage())


class _HealthOnlyProvider:
    def __init__(self) -> None:
        self.list_called = False
        self.invoke_called = False

    def health(self, config: dict[str, Any]) -> HealthResult:
        return HealthResult(ok=True, latency_ms=21)

    def list_models(self, config: dict[str, Any]) -> ModelListResult:
        self.list_called = True
        return ModelListResult(ok=True, supported=True, models=[])

    def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
        self.invoke_called = True
        return InvokeResult(ok=True, output="unused", latency_ms=1, usage=Usage())


def test_connectivity_suite_ollama_runs_real_invoke(monkeypatch):
    from polaris.cells.llm.evaluation.internal import suites

    provider = _OllamaSuccessProvider()
    monkeypatch.setattr(
        suites,
        "get_provider_manager",
        lambda: type("_Manager", (), {"get_provider_instance": staticmethod(lambda _type: provider)})(),
    )

    model = "glm-4.7-flash:latest"
    provider_cfg = {
        "type": "ollama",
        "base_url": "http://120.24.117.59:11434",
        "options": {"num_ctx": 131072},
    }

    result = asyncio.run(run_connectivity_suite(provider_cfg, model))

    assert result["ok"] is True
    assert result["latency_ms"] == 46
    assert result["details"]["health"]["status"] == "healthy"
    assert result["details"]["model_available"]["status"] == "available"
    assert result["details"]["invoke_smoke"]["status"] == "ok"
    assert provider.list_calls == 1
    assert len(provider.invoke_calls) == 1

    invoke_call = provider.invoke_calls[0]
    assert invoke_call["model"] == model
    assert invoke_call["prompt"] == "Reply with exactly: OK"

    options = invoke_call["config"]["options"]
    assert options["num_ctx"] == 131072
    assert options["num_predict"] == 8
    assert options["temperature"] == 0.0
    assert options["top_p"] == 1.0
    assert options["top_k"] == 1


def test_connectivity_suite_ollama_fails_when_model_not_installed(monkeypatch):
    from polaris.cells.llm.evaluation.internal import suites

    provider = _OllamaMissingModelProvider()
    monkeypatch.setattr(
        suites,
        "get_provider_manager",
        lambda: type("_Manager", (), {"get_provider_instance": staticmethod(lambda _type: provider)})(),
    )

    result = asyncio.run(
        run_connectivity_suite(
            {"type": "ollama", "base_url": "http://120.24.117.59:11434"},
            "glm-4.7-flash:latest",
        )
    )

    assert result["ok"] is False
    assert "not installed" in result["error"]
    assert result["details"]["model_available"]["status"] == "unavailable"
    assert provider.invoke_called is False


def test_connectivity_suite_non_ollama_keeps_health_only(monkeypatch):
    from polaris.cells.llm.evaluation.internal import suites

    provider = _HealthOnlyProvider()
    monkeypatch.setattr(
        suites,
        "get_provider_manager",
        lambda: type("_Manager", (), {"get_provider_instance": staticmethod(lambda _type: provider)})(),
    )

    result = asyncio.run(
        run_connectivity_suite(
            {"type": "openai_compat", "base_url": "https://api.example.com"},
            "gpt-4.1-mini",
        )
    )

    assert result["ok"] is True
    assert result["latency_ms"] == 21
    assert result["details"]["health"]["status"] == "healthy"
    assert "model_available" not in result["details"]
    assert "invoke_smoke" not in result["details"]
    assert provider.list_called is False
    assert provider.invoke_called is False
