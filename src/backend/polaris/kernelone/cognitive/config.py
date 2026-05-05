"""Cognitive Life Form Feature Flags and Configuration.

This module provides unified profile-based cognitive switch management.

## Profile System (PR-07)

The cognitive configuration is organized into named profiles that can be loaded
via `get_cognitive_profile()`. Profiles provide consistent defaults across all
cognitive capabilities:

- **dev**: Development profile with shadow mode for execution/evolution
- **staging**: Staging profile with limited evolution and full feature parity
- **prod**: Production profile with all features enabled

### Profile Loading Priority
1. tenant_override (future: LaunchDarkly/Unleash)
2. user_override (future: per-user flags)
3. env (COGNITIVE_ENV environment variable)
4. default (falls back to "dev")

### Backward Compatibility
Individual environment variables are still supported but deprecated.
Environment variables take precedence over profile values when set.
"""

from __future__ import annotations

import os
from typing import TypedDict

# =============================================================================
# Profile Definitions
# =============================================================================

COGNITIVE_PROFILES: dict[str, CognitiveProfile] = {
    "dev": {
        "enabled": True,
        "perception": True,
        "reasoning": True,
        "execution": False,  # shadow mode
        "evolution": False,
        "use_llm": False,
        "governance": True,
        "value_alignment": False,
        "personality": False,
        "telemetry": True,
    },
    "staging": {
        "enabled": True,
        "perception": True,
        "reasoning": True,
        "execution": True,
        "evolution": "limited",
        "use_llm": True,
        "governance": True,
        "value_alignment": True,
        "personality": True,
        "telemetry": True,
    },
    "prod": {
        "enabled": True,
        "perception": True,
        "reasoning": True,
        "execution": True,
        "evolution": True,
        "use_llm": True,
        "governance": True,
        "value_alignment": True,
        "personality": True,
        "telemetry": True,
    },
}


class CognitiveProfile(TypedDict, total=False):
    """Type definition for a cognitive profile configuration."""

    enabled: bool
    perception: bool
    reasoning: bool
    execution: bool | str  # bool or "limited"
    evolution: bool | str
    use_llm: bool
    governance: bool
    value_alignment: bool
    personality: bool
    telemetry: bool


def get_cognitive_profile(
    env: str | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> CognitiveProfile:
    """
    Get cognitive configuration profile.

    Args:
        env: Environment name (dev/staging/prod). Defaults to COGNITIVE_ENV env var.
        tenant_id: Tenant ID for multi-tenant override (future use).
        user_id: User ID for per-user override (future use).

    Returns:
        CognitiveProfile with all cognitive capability flags.

    Note:
        Priority: tenant_override > user_override > env > default
        Currently only env-based loading is implemented.
        Tenant/user overrides will be added in future versions.
    """
    if env is None:
        env = os.environ.get("COGNITIVE_ENV", "dev")

    base = COGNITIVE_PROFILES.get(env, COGNITIVE_PROFILES["dev"])

    # Dynamic override support (future: integrate with LaunchDarkly/Unleash)
    # Currently returns base; later versions will fetch from FlagService
    return CognitiveProfile(**base)


# =============================================================================
# Backward Compatibility (Deprecated - use get_cognitive_profile())
# =============================================================================

# Phase-gated feature flags
COGNITIVE_ENABLED = os.environ.get("KERNELONE_COGNITIVE_ENABLED", "0") == "1"
PERCEPTION_ENABLED = os.environ.get("KERNELONE_COGNITIVE_PERCEPTION", "0") == "1"
REASONING_ENABLED = os.environ.get("KERNELONE_COGNITIVE_REASONING", "0") == "1"
EXECUTION_ENABLED = os.environ.get("KERNELONE_COGNITIVE_EXECUTION", "0") == "1"
EVOLUTION_ENABLED = os.environ.get("KERNELONE_COGNITIVE_EVOLUTION", "0") == "1"
PERSONALITY_ENABLED = os.environ.get("KERNELONE_COGNITIVE_PERSONALITY", "0") == "1"

# Additional feature flags (used in orchestrator)
# Aliases for consistency with COGNITIVE_ENABLE_* naming pattern
COGNITIVE_ENABLE_EVOLUTION = EVOLUTION_ENABLED
COGNITIVE_ENABLE_PERSONALITY = PERSONALITY_ENABLED
COGNITIVE_ENABLE_GOVERNANCE = os.environ.get("COGNITIVE_ENABLE_GOVERNANCE", "0") == "1"
COGNITIVE_ENABLE_VALUE_ALIGNMENT = os.environ.get("COGNITIVE_ENABLE_VALUE_ALIGNMENT", "0") == "1"
COGNITIVE_USE_LLM = os.environ.get("COGNITIVE_USE_LLM", "0") == "1"


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default


# Risk thresholds
RISK_BYPASS_THRESHOLD = _env_float("KERNELONE_RISK_BYPASS_THRESHOLD", 0.0)
RISK_FAST_THINK_THRESHOLD = _env_float("KERNELONE_RISK_FAST_THINK_THRESHOLD", 1.0)
UNCERTAINTY_FULL_PIPE_THRESHOLD = _env_float("KERNELONE_UNCERTAINTY_THRESHOLD", 0.6)

# Rollback config
MAX_ROLLBACK_STEPS = 3

# Thinking phase config
MAX_THINKING_TIME_SECONDS = _env_float("KERNELONE_MAX_THINKING_TIME", 30.0)
ENABLE_DEVILS_ADVOCATE = os.environ.get("KERNELONE_ENABLE_DEVILS_ADVOCATE", "1") not in ("0", "false")

# Governance thresholds
GOVERNANCE_CONFIDENCE_HIGH_RISK = _env_float("COGNITIVE_GOVERNANCE_HIGH_RISK", 0.7)
GOVERNANCE_CONFIDENCE_MEDIUM_RISK = _env_float("COGNITIVE_GOVERNANCE_MEDIUM_RISK", 0.5)
GOVERNANCE_CONFIDENCE_LOW_RISK = _env_float("COGNITIVE_GOVERNANCE_LOW_RISK", 0.3)

# Evolution thresholds
EVOLUTION_RECURRENCE_THRESHOLD = _env_int("COGNITIVE_EVOLUTION_RECURRENCE_THRESHOLD", 3)
EVOLUTION_CALIBRATION_WINDOW = _env_int("COGNITIVE_EVOLUTION_CALIBRATION_WINDOW", 50)

# Telemetry configuration
COGNITIVE_ENABLE_TELEMETRY = os.environ.get("KERNELONE_COGNITIVE_ENABLE_TELEMETRY", "0") == "1"
