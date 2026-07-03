"""Architecture guard for LLM provider configuration validation naming."""

from __future__ import annotations

from pathlib import Path

from polaris.infrastructure import llm as infrastructure_llm
from polaris.infrastructure.llm import providers as infrastructure_providers
from polaris.infrastructure.llm.providers import async_base_provider
from polaris.kernelone.llm import providers
from polaris.kernelone.llm.providers import base_provider

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_FILES = (
    _BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "providers" / "base_provider.py",
    _BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "providers" / "__init__.py",
    _BACKEND_ROOT / "polaris" / "infrastructure" / "llm" / "providers" / "__init__.py",
    _BACKEND_ROOT / "polaris" / "infrastructure" / "llm" / "providers" / "async_base_provider.py",
)


def test_provider_config_validation_uses_explicit_result_name() -> None:
    """The generic provider ValidationResult alias must not be restored."""
    assert hasattr(base_provider, "ProviderConfigValidationResult")
    assert hasattr(providers, "ProviderConfigValidationResult")
    assert hasattr(infrastructure_providers, "ProviderConfigValidationResult")
    assert hasattr(async_base_provider, "ProviderConfigValidationResult")

    assert not hasattr(base_provider, "ValidationResult")
    assert not hasattr(providers, "ValidationResult")
    assert not hasattr(infrastructure_providers, "ValidationResult")
    assert not hasattr(infrastructure_llm, "ValidationResult")
    assert not hasattr(async_base_provider, "ValidationResult")

    for path in _FILES:
        source = path.read_text(encoding="utf-8")
        assert "ValidationResult = ProviderConfigValidationResult" not in source
        assert '"ValidationResult"' not in source
