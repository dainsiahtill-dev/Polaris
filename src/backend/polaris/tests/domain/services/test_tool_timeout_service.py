"""Tests for tool_timeout_service module."""

from __future__ import annotations

from polaris.domain.services.tool_timeout_service import (
    TimeoutConfig,
    ToolTier,
    ToolTimeoutService,
    get_tool_timeout_service,
    reset_tool_timeout_service,
)


# =============================================================================
# ToolTier
# =============================================================================
def test_tool_tier_values():
    assert ToolTier.FOREGROUND.value == "foreground"
    assert ToolTier.BACKGROUND.value == "background"
    assert ToolTier.CRITICAL.value == "critical"
    assert ToolTier.FAST.value == "fast"


# =============================================================================
# TimeoutConfig
# =============================================================================
def test_timeout_config_defaults():
    config = TimeoutConfig(default=120, max_limit=600)
    assert config.default == 120
    assert config.max_limit == 600
    assert config.min_limit == 1


def test_timeout_config_explicit_min():
    config = TimeoutConfig(default=300, max_limit=3600, min_limit=10)
    assert config.min_limit == 10


# =============================================================================
# ToolTimeoutService initialization
# =============================================================================
def test_service_default_timeouts():
    service = ToolTimeoutService()
    fg = service.get_config(ToolTier.FOREGROUND)
    assert fg.default == 120
    assert fg.max_limit == 600
    assert fg.min_limit == 1

    bg = service.get_config(ToolTier.BACKGROUND)
    assert bg.default == 300
    assert bg.max_limit == 3600
    assert bg.min_limit == 10


def test_service_custom_timeouts():
    custom = {
        ToolTier.FAST: TimeoutConfig(default=15, max_limit=45, min_limit=5),
    }
    service = ToolTimeoutService(custom_timeouts=custom)
    fast = service.get_config(ToolTier.FAST)
    assert fast.default == 15
    assert fast.max_limit == 45
    assert fast.min_limit == 5


# =============================================================================
# get_timeout
# =============================================================================
def test_get_timeout_default():
    service = ToolTimeoutService()
    assert service.get_timeout(ToolTier.FOREGROUND) == 120
    assert service.get_timeout(ToolTier.BACKGROUND) == 300
    assert service.get_timeout(ToolTier.CRITICAL) == 600
    assert service.get_timeout(ToolTier.FAST) == 30


def test_get_timeout_with_string_tier():
    service = ToolTimeoutService()
    assert service.get_timeout("foreground") == 120
    assert service.get_timeout("background") == 300


def test_get_timeout_requested_within_range():
    service = ToolTimeoutService()
    assert service.get_timeout(ToolTier.FOREGROUND, requested=60) == 60
    assert service.get_timeout(ToolTier.FOREGROUND, requested=600) == 600


def test_get_timeout_clamped_high():
    service = ToolTimeoutService()
    # foreground max is 600
    assert service.get_timeout(ToolTier.FOREGROUND, requested=999) == 600


def test_get_timeout_clamped_low():
    service = ToolTimeoutService()
    # foreground min is 1
    assert service.get_timeout(ToolTier.FOREGROUND, requested=0) == 1


def test_get_timeout_none_uses_default():
    service = ToolTimeoutService()
    assert service.get_timeout(ToolTier.CRITICAL, requested=None) == 600


# =============================================================================
# validate_timeout
# =============================================================================
def test_validate_timeout_valid():
    service = ToolTimeoutService()
    valid, clamped, reason = service.validate_timeout(ToolTier.FAST, timeout=30)
    assert valid is True
    assert clamped == 30
    assert reason == "OK"


def test_validate_timeout_too_high():
    service = ToolTimeoutService()
    valid, clamped, reason = service.validate_timeout(ToolTier.FAST, timeout=90)
    assert valid is False
    assert clamped == 60
    assert "exceeds maximum" in reason


def test_validate_timeout_too_low():
    service = ToolTimeoutService()
    valid, clamped, reason = service.validate_timeout(ToolTier.BACKGROUND, timeout=5)
    assert valid is False
    assert clamped == 10
    assert "below minimum" in reason


def test_validate_timeout_with_string_tier():
    service = ToolTimeoutService()
    valid, clamped, _reason = service.validate_timeout("fast", timeout=45)
    assert valid is True
    assert clamped == 45


# =============================================================================
# get_config
# =============================================================================
def test_get_config_returns_copy_behavior():
    service = ToolTimeoutService()
    config = service.get_config("foreground")
    assert isinstance(config, TimeoutConfig)
    assert config.default == 120


# =============================================================================
# adjust_for_budget
# =============================================================================
def test_adjust_for_budget_high():
    service = ToolTimeoutService()
    assert service.adjust_for_budget(ToolTier.FOREGROUND, 120, 0.75) == 120
    assert service.adjust_for_budget(ToolTier.FOREGROUND, 120, 0.51) == 120


def test_adjust_for_budget_medium():
    service = ToolTimeoutService()
    assert service.adjust_for_budget(ToolTier.FOREGROUND, 120, 0.5) == 90
    assert service.adjust_for_budget(ToolTier.FOREGROUND, 120, 0.21) == 90


def test_adjust_for_budget_low():
    service = ToolTimeoutService()
    assert service.adjust_for_budget(ToolTier.FOREGROUND, 120, 0.2) == 60
    assert service.adjust_for_budget(ToolTier.FOREGROUND, 120, 0.11) == 60


def test_adjust_for_budget_critical():
    service = ToolTimeoutService()
    result = service.adjust_for_budget(ToolTier.FOREGROUND, 120, 0.1)
    assert result == 1  # min_limit for foreground


def test_adjust_for_budget_zero():
    service = ToolTimeoutService()
    result = service.adjust_for_budget(ToolTier.BACKGROUND, 300, 0.0)
    assert result == 10  # min_limit for background


def test_adjust_for_budget_with_string_tier():
    service = ToolTimeoutService()
    assert service.adjust_for_budget("fast", 30, 0.1) == 1


# =============================================================================
# Global instance
# =============================================================================
def test_global_singleton():
    reset_tool_timeout_service()
    s1 = get_tool_timeout_service()
    s2 = get_tool_timeout_service()
    assert s1 is s2


def test_global_reset():
    reset_tool_timeout_service()
    s1 = get_tool_timeout_service()
    reset_tool_timeout_service()
    s2 = get_tool_timeout_service()
    assert s1 is not s2
