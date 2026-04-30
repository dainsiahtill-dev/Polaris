"""Tests for polaris.kernelone.llm.model_resolver.

Pure function tests for model resolution, validation, fallback lookup,
and log formatting.
"""

from __future__ import annotations

import pytest
from polaris.kernelone.llm.model_resolver import (
    MODEL_FALLBACKS,
    ModelResolutionResult,
    ModelValidationResult,
    get_default_model_for_provider,
    get_model_resolution_log,
    resolve_model_name,
    validate_model_name,
)

# =============================================================================
# resolve_model_name — priority 1: role_model
# =============================================================================


@pytest.mark.parametrize(
    "role_model",
    ["gpt-4", "  gpt-4  ", "claude-3-opus"],
)
def test_resolve_prefers_role_model(role_model: str) -> None:
    result = resolve_model_name(model=None, default_model=None, provider_type=None, role_model=role_model)
    assert result.source == "role_config"
    assert result.is_valid is True
    assert result.model == role_model.strip()


def test_resolve_role_model_overrides_explicit_model() -> None:
    result = resolve_model_name(model="gpt-3.5", default_model="gpt-4", provider_type="openai", role_model="claude-3")
    assert result.source == "role_config"
    assert result.model == "claude-3"


def test_resolve_empty_role_model_falls_through() -> None:
    result = resolve_model_name(model="gpt-4", default_model=None, provider_type=None, role_model="")
    assert result.source == "provider_config"
    assert result.model == "gpt-4"


def test_resolve_whitespace_only_role_model_falls_through() -> None:
    result = resolve_model_name(model="gpt-4", default_model=None, provider_type=None, role_model="   ")
    assert result.source == "provider_config"
    assert result.model == "gpt-4"


def test_resolve_none_role_model_falls_through() -> None:
    result = resolve_model_name(model="gpt-4", default_model=None, provider_type=None, role_model=None)
    assert result.source == "provider_config"


# =============================================================================
# resolve_model_name — priority 2: explicit model
# =============================================================================


@pytest.mark.parametrize(
    "model",
    ["gpt-4", "  gpt-4  ", "custom-model-v1"],
)
def test_resolve_uses_explicit_model(model: str) -> None:
    result = resolve_model_name(model=model, default_model=None, provider_type=None, role_model=None)
    assert result.source == "provider_config"
    assert result.model == model.strip()
    assert result.is_valid is True


def test_resolve_explicit_model_overrides_default() -> None:
    result = resolve_model_name(model="gpt-4", default_model="gpt-3.5", provider_type="openai", role_model=None)
    assert result.source == "provider_config"
    assert result.model == "gpt-4"


def test_resolve_empty_explicit_model_falls_through() -> None:
    result = resolve_model_name(model="", default_model="gpt-4", provider_type=None, role_model=None)
    assert result.source == "provider_default"


def test_resolve_whitespace_only_explicit_model_falls_through() -> None:
    result = resolve_model_name(model="   ", default_model="gpt-4", provider_type=None, role_model=None)
    assert result.source == "provider_default"


# =============================================================================
# resolve_model_name — priority 3: default_model
# =============================================================================


def test_resolve_uses_default_model() -> None:
    result = resolve_model_name(model=None, default_model="gpt-4-turbo", provider_type=None, role_model=None)
    assert result.source == "provider_default"
    assert result.model == "gpt-4-turbo"
    assert result.is_valid is True


def test_resolve_default_model_strips_whitespace() -> None:
    result = resolve_model_name(model=None, default_model="  gpt-4  ", provider_type=None, role_model=None)
    assert result.model == "gpt-4"


def test_resolve_empty_default_model_falls_through() -> None:
    result = resolve_model_name(model=None, default_model="", provider_type="openai", role_model=None)
    assert result.source == "hardcoded_fallback"


# =============================================================================
# resolve_model_name — priority 4: hardcoded fallback by provider_type
# =============================================================================


@pytest.mark.parametrize(
    "provider_type,expected_model",
    [
        ("openai", "gpt-4"),
        ("anthropic", "claude-3-sonnet-20240229"),
        ("kimi", "kimi-k2-thinking-turbo"),
        ("deepseek", "deepseek-chat"),
        ("ollama", "llama2"),
        ("custom_https", "gpt-4"),
    ],
)
def test_resolve_hardcoded_fallback(provider_type: str, expected_model: str) -> None:
    result = resolve_model_name(model=None, default_model=None, provider_type=provider_type, role_model=None)
    assert result.source == "hardcoded_fallback"
    assert result.model == expected_model
    assert result.is_valid is True
    assert result.warning is not None
    assert provider_type in result.warning


def test_resolve_unknown_provider_type_falls_to_universal() -> None:
    result = resolve_model_name(model=None, default_model=None, provider_type="unknown_provider", role_model=None)
    assert result.source == "universal_fallback"
    assert result.model == "gpt-4"
    assert result.is_valid is False
    assert result.warning is not None


def test_resolve_no_provider_type_falls_to_universal() -> None:
    result = resolve_model_name(model=None, default_model=None, provider_type=None, role_model=None)
    assert result.source == "universal_fallback"
    assert result.model == "gpt-4"
    assert result.is_valid is False


# =============================================================================
# resolve_model_name — priority 5: universal fallback
# =============================================================================


def test_resolve_universal_fallback_fields() -> None:
    result = resolve_model_name(model=None, default_model=None, provider_type=None, role_model=None)
    assert result.model == "gpt-4"
    assert result.source == "universal_fallback"
    assert result.is_valid is False
    assert "universal fallback" in (result.warning or "").lower()


# =============================================================================
# validate_model_name
# =============================================================================


@pytest.mark.parametrize(
    "model",
    ["gpt-4", "claude-3-sonnet-20240229", "a" * 100, "model-with-dashes_123"],
)
def test_validate_valid_model_names(model: str) -> None:
    result = validate_model_name(model)
    assert result.is_valid is True
    assert result.error is None


def test_validate_empty_string() -> None:
    result = validate_model_name("")
    assert result.is_valid is False
    assert "empty" in (result.error or "").lower()


def test_validate_whitespace_only() -> None:
    result = validate_model_name("   ")
    assert result.is_valid is False
    assert "stripping" in (result.error or "").lower()


def test_validate_none_model() -> None:
    result = validate_model_name(None)  # type: ignore[arg-type]
    assert result.is_valid is False
    assert "empty" in (result.error or "").lower()


@pytest.mark.parametrize(
    "char",
    ["<", ">", '"', "'", "&"],
)
def test_validate_rejects_invalid_characters(char: str) -> None:
    result = validate_model_name(f"model{char}name")
    assert result.is_valid is False
    assert char in (result.error or "")


def test_validate_too_long() -> None:
    result = validate_model_name("a" * 101)
    assert result.is_valid is False
    assert "too long" in (result.error or "").lower()


def test_validate_exactly_100_chars_ok() -> None:
    result = validate_model_name("a" * 100)
    assert result.is_valid is True


def test_validate_strips_before_length_check() -> None:
    result = validate_model_name("  " + "a" * 100 + "  ")
    assert result.is_valid is True


def test_validate_strips_before_invalid_char_check() -> None:
    result = validate_model_name("  model<name  ")
    assert result.is_valid is False
    assert "<" in (result.error or "")


# =============================================================================
# get_default_model_for_provider
# =============================================================================


@pytest.mark.parametrize(
    "provider_type,expected",
    [
        ("openai", "gpt-4"),
        ("anthropic", "claude-3-sonnet-20240229"),
        ("deepseek", "deepseek-chat"),
        ("ollama", "llama2"),
    ],
)
def test_get_default_model_for_provider(provider_type: str, expected: str) -> None:
    assert get_default_model_for_provider(provider_type) == expected


def test_get_default_model_for_unknown_provider() -> None:
    assert get_default_model_for_provider("nonexistent") is None


def test_get_default_model_empty_string() -> None:
    assert get_default_model_for_provider("") is None


# =============================================================================
# get_model_resolution_log
# =============================================================================


def test_get_model_resolution_log_basic() -> None:
    log = get_model_resolution_log(model="gpt-4", source="provider_config", is_valid=True)
    assert "Model resolution result:" in log
    assert "Model: gpt-4" in log
    assert "Source: provider_config" in log
    assert "Valid: True" in log


def test_get_model_resolution_log_with_provider() -> None:
    log = get_model_resolution_log(model="claude-3", source="role_config", is_valid=True, provider_type="anthropic")
    assert "Provider Type: anthropic" in log


def test_get_model_resolution_log_with_warning() -> None:
    log = get_model_resolution_log(model="gpt-4", source="universal_fallback", is_valid=False, warning="Fallback used")
    assert "Warning: Fallback used" in log


def test_get_model_resolution_log_all_fields() -> None:
    log = get_model_resolution_log(
        model="gpt-4",
        source="hardcoded_fallback",
        is_valid=True,
        warning="Default model",
        provider_type="openai",
    )
    assert "Model: gpt-4" in log
    assert "Source: hardcoded_fallback" in log
    assert "Valid: True" in log
    assert "Provider Type: openai" in log
    assert "Warning: Default model" in log


def test_get_model_resolution_log_omits_optional_when_none() -> None:
    log = get_model_resolution_log(model="gpt-4", source="provider_config", is_valid=True)
    assert "Provider Type:" not in log
    assert "Warning:" not in log


# =============================================================================
# MODEL_FALLBACKS constant
# =============================================================================


def test_model_fallbacks_is_dict() -> None:
    assert isinstance(MODEL_FALLBACKS, dict)


def test_model_fallbacks_has_expected_providers() -> None:
    expected = {
        "openai",
        "openai_compat",
        "anthropic",
        "anthropic_compat",
        "kimi",
        "minimax",
        "gemini_api",
        "ollama",
        "codex_cli",
        "codex_sdk",
        "gemini_cli",
        "custom_https",
        "deepseek",
        "moonshot",
        "stepfun",
        "zhipu",
    }
    assert set(MODEL_FALLBACKS.keys()) == expected


def test_model_fallbacks_values_are_non_empty_strings() -> None:
    for model in MODEL_FALLBACKS.values():
        assert isinstance(model, str)
        assert model.strip() != ""


# =============================================================================
# ModelResolutionResult / ModelValidationResult dataclasses
# =============================================================================


def test_model_resolution_result_defaults() -> None:
    result = ModelResolutionResult(model="gpt-4", source="test", is_valid=True)
    assert result.warning is None


def test_model_validation_result_defaults() -> None:
    result = ModelValidationResult(is_valid=True)
    assert result.error is None


def test_model_resolution_result_equality() -> None:
    r1 = ModelResolutionResult(model="a", source="s", is_valid=True, warning="w")
    r2 = ModelResolutionResult(model="a", source="s", is_valid=True, warning="w")
    r3 = ModelResolutionResult(model="b", source="s", is_valid=True, warning="w")
    assert r1 == r2
    assert r1 != r3


def test_model_validation_result_equality() -> None:
    v1 = ModelValidationResult(is_valid=False, error="bad")
    v2 = ModelValidationResult(is_valid=False, error="bad")
    v3 = ModelValidationResult(is_valid=True, error="bad")
    assert v1 == v2
    assert v1 != v3
