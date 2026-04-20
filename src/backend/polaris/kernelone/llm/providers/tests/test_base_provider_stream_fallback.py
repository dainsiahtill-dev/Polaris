from __future__ import annotations

import pytest
from polaris.kernelone.llm.providers.base_provider import (
    BaseProvider,
    ProviderInfo,
    ValidationResult,
)
from polaris.kernelone.llm.types import (
    HealthResult,
    InvokeResult,
    ModelListResult,
    estimate_usage,
)


class _DummyProvider(BaseProvider):
    @classmethod
    def get_provider_info(cls) -> ProviderInfo:
        return ProviderInfo(
            name="Dummy",
            type="dummy",
            description="test",
            version="1.0.0",
            author="test",
            documentation_url="",
            supported_features=[],
            cost_class="FIXED",
            provider_category="LLM",
            autonomous_file_access=False,
            requires_file_interfaces=False,
            model_listing_method="NONE",
        )

    @classmethod
    def get_default_config(cls) -> dict[str, object]:
        return {}

    @classmethod
    def validate_config(cls, config: dict[str, object]) -> ValidationResult:
        return ValidationResult(valid=True, errors=[], warnings=[], normalized_config=dict(config))

    def health(self, config: dict[str, object]) -> HealthResult:
        del config
        return HealthResult(ok=True, latency_ms=0)

    def list_models(self, config: dict[str, object]) -> ModelListResult:
        del config
        return ModelListResult(ok=True, supported=True, models=[])

    def invoke(self, prompt: str, model: str, config: dict[str, object]) -> InvokeResult:
        del model, config
        output = "0123456789ABCDEFGHIJ"
        return InvokeResult(
            ok=True,
            output=output,
            latency_ms=1,
            usage=estimate_usage(prompt, output),
        )


@pytest.mark.asyncio
async def test_default_stream_fallback_emits_single_chunk() -> None:
    provider = _DummyProvider()
    chunks = [chunk async for chunk in provider.invoke_stream("hello", "dummy-model", {})]
    assert chunks == ["0123456789ABCDEFGHIJ"]
