"""
Model Resolution Utilities
Provides model name validation and fallback mechanisms for all LLM providers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ModelResolutionResult:
    """Result of model name resolution"""

    model: str
    source: str  # 'role_config', 'provider_config', 'provider_default', 'hardcoded_fallback'
    is_valid: bool
    warning: str | None = None


@dataclass
class ModelValidationResult:
    """Result of model name validation"""

    is_valid: bool
    error: str | None = None


# Hardcoded fallback models by provider type
MODEL_FALLBACKS: dict[str, str] = {
    "openai": "gpt-4",
    "openai_compat": "gpt-4",
    "anthropic": "claude-3-sonnet-20240229",
    "anthropic_compat": "claude-3-sonnet-20240229",
    "kimi": "kimi-k2-thinking-turbo",
    "minimax": "abab6.5-chat",
    "gemini_api": "gemini-1.5-pro",
    "ollama": "llama2",
    "codex_cli": "gpt-4-codex",
    "codex_sdk": "gpt-4",
    "gemini_cli": "gemini-1.5-pro",
    "custom_https": "gpt-4",
    "deepseek": "deepseek-chat",
    "moonshot": "moonshot-v1-8k",
    "stepfun": "step-1v-8k",
    "zhipu": "chatglm_pro",
}


def resolve_model_name(
    model: str | None,
    default_model: str | None = None,
    provider_type: str | None = None,
    role_model: str | None = None,
) -> ModelResolutionResult:
    """
    Resolve the model name with multiple fallback strategies.

    Priority:
    1. role_model (from role configuration)
    2. model (explicitly specified)
    3. default_model (from provider configuration)
    4. hardcoded fallback (by provider type)
    5. universal fallback ('gpt-4')
    """
    # Priority 1: role_model
    if role_model and role_model.strip():
        return ModelResolutionResult(model=role_model.strip(), source="role_config", is_valid=True)

    # Priority 2: explicitly specified model
    if model and model.strip():
        return ModelResolutionResult(model=model.strip(), source="provider_config", is_valid=True)

    # Priority 3: default_model from provider
    if default_model and default_model.strip():
        return ModelResolutionResult(model=default_model.strip(), source="provider_default", is_valid=True)

    # Priority 4: hardcoded fallback by provider type
    if provider_type:
        fallback_model = MODEL_FALLBACKS.get(provider_type)
        if fallback_model:
            return ModelResolutionResult(
                model=fallback_model,
                source="hardcoded_fallback",
                is_valid=True,
                warning=f"Using default model {fallback_model} for provider type {provider_type}",
            )

    # Priority 5: universal fallback
    return ModelResolutionResult(
        model="gpt-4",
        source="universal_fallback",
        is_valid=False,
        warning="Could not determine model, using universal fallback 'gpt-4'",
    )


def validate_model_name(model: str, provider_type: str | None = None) -> ModelValidationResult:
    """
    Validate a model name.

    Checks:
    - Not empty
    - No invalid characters
    - Reasonable length
    """
    # Check for empty or non-string
    if not model or not isinstance(model, str):
        return ModelValidationResult(is_valid=False, error="Model name cannot be empty")

    stripped = model.strip()

    if not stripped:
        return ModelValidationResult(is_valid=False, error="Model name cannot be empty after stripping whitespace")

    # Check for invalid characters (XSS prevention, etc.)
    invalid_chars = ["<", ">", '"', "'", "&"]
    for char in invalid_chars:
        if char in stripped:
            return ModelValidationResult(is_valid=False, error=f"Model name contains invalid character: {char}")

    # Check length
    if len(stripped) > 100:
        return ModelValidationResult(is_valid=False, error="Model name is too long (max 100 characters)")

    return ModelValidationResult(is_valid=True)


def get_default_model_for_provider(provider_type: str) -> str | None:
    """Get the default model for a given provider type"""
    return MODEL_FALLBACKS.get(provider_type)


def get_model_resolution_log(
    model: str,
    source: str,
    is_valid: bool,
    warning: str | None = None,
    provider_type: str | None = None,
) -> str:
    """Generate a log message for model resolution"""
    lines = [
        "Model resolution result:",
        f"  - Model: {model}",
        f"  - Source: {source}",
        f"  - Valid: {is_valid}",
    ]
    if provider_type:
        lines.append(f"  - Provider Type: {provider_type}")
    if warning:
        lines.append(f"  - Warning: {warning}")
    return "\n".join(lines)
