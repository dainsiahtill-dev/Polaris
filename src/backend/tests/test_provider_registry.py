from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from polaris.infrastructure.llm.providers.provider_registry import ProviderManager
from polaris.kernelone.llm.providers import BaseProvider, ProviderInfo, ValidationResult
from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelListResult, Usage


class _CountingProvider(BaseProvider):
    _counter_lock = threading.Lock()
    init_count = 0

    def __init__(self) -> None:
        # Artificial delay to magnify race windows.
        time.sleep(0.01)
        with self._counter_lock:
            type(self).init_count += 1

    @classmethod
    def reset_counter(cls) -> None:
        with cls._counter_lock:
            cls.init_count = 0

    @classmethod
    def get_provider_info(cls) -> ProviderInfo:
        return ProviderInfo(
            name="Counting Provider",
            type="counting",
            description="Test-only provider",
            version="1.0",
            author="tests",
            documentation_url="",
            supported_features=[],
            cost_class="LOCAL",
            provider_category="LLM",
            autonomous_file_access=False,
            requires_file_interfaces=False,
            model_listing_method="NONE",
        )

    @classmethod
    def get_default_config(cls) -> dict[str, Any]:
        return {}

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> ValidationResult:
        del config
        return ValidationResult(valid=True, errors=[], warnings=[], normalized_config={})

    def health(self, config: dict[str, Any]) -> HealthResult:
        del config
        return HealthResult(ok=True, latency_ms=1)

    def list_models(self, config: dict[str, Any]) -> ModelListResult:
        del config
        return ModelListResult(ok=True, models=[])

    def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
        del prompt, model, config
        return InvokeResult(ok=True, output="ok", latency_ms=1, usage=Usage())


def test_get_provider_instance_thread_safe() -> None:
    manager = ProviderManager()
    provider_type = "counting_thread_test"
    manager.register_provider(provider_type, _CountingProvider)
    _CountingProvider.reset_counter()

    with ThreadPoolExecutor(max_workers=32) as pool:
        instances = list(pool.map(lambda _: manager.get_provider_instance(provider_type), range(100)))

    first = instances[0]
    assert first is not None
    assert all(instance is first for instance in instances)
    assert _CountingProvider.init_count == 1


@pytest.mark.asyncio
async def test_get_provider_instance_async_thread_safe() -> None:
    manager = ProviderManager()
    provider_type = "counting_async_test"
    manager.register_provider(provider_type, _CountingProvider)
    _CountingProvider.reset_counter()

    tasks = [manager.get_provider_instance_async(provider_type) for _ in range(100)]
    instances = await asyncio.gather(*tasks)

    first = instances[0]
    assert first is not None
    assert all(instance is first for instance in instances)
    assert _CountingProvider.init_count == 1
