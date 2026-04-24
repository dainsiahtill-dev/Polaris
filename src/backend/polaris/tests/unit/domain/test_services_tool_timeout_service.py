"""Tests for polaris.domain.services.tool_timeout_service."""

from __future__ import annotations

from polaris.domain.services.tool_timeout_service import (
    TimeoutConfig,
    ToolTier,
    ToolTimeoutService,
    get_tool_timeout_service,
    reset_tool_timeout_service,
)


class TestToolTier:
    def test_values(self) -> None:
        assert ToolTier.FOREGROUND.value == "foreground"
        assert ToolTier.BACKGROUND.value == "background"
        assert ToolTier.CRITICAL.value == "critical"
        assert ToolTier.FAST.value == "fast"


class TestTimeoutConfig:
    def test_defaults(self) -> None:
        cfg = TimeoutConfig(default=120, max_limit=600)
        assert cfg.default == 120
        assert cfg.max_limit == 600
        assert cfg.min_limit == 1


class TestToolTimeoutService:
    def test_get_timeout_default(self) -> None:
        svc = ToolTimeoutService()
        assert svc.get_timeout(ToolTier.FOREGROUND) == 120
        assert svc.get_timeout(ToolTier.BACKGROUND) == 300
        assert svc.get_timeout(ToolTier.CRITICAL) == 600
        assert svc.get_timeout(ToolTier.FAST) == 30

    def test_get_timeout_requested(self) -> None:
        svc = ToolTimeoutService()
        assert svc.get_timeout(ToolTier.FOREGROUND, requested=60) == 60

    def test_get_timeout_clamped_high(self) -> None:
        svc = ToolTimeoutService()
        assert svc.get_timeout(ToolTier.FOREGROUND, requested=9999) == 600

    def test_get_timeout_clamped_low(self) -> None:
        svc = ToolTimeoutService()
        assert svc.get_timeout(ToolTier.BACKGROUND, requested=1) == 10

    def test_get_timeout_from_string(self) -> None:
        svc = ToolTimeoutService()
        assert svc.get_timeout("fast") == 30

    def test_validate_timeout_valid(self) -> None:
        svc = ToolTimeoutService()
        is_valid, clamped, reason = svc.validate_timeout(ToolTier.FOREGROUND, 60)
        assert is_valid is True
        assert clamped == 60
        assert reason == "OK"

    def test_validate_timeout_too_low(self) -> None:
        svc = ToolTimeoutService()
        is_valid, clamped, reason = svc.validate_timeout(ToolTier.FOREGROUND, 0)
        assert is_valid is False
        assert clamped == 1
        assert "minimum" in reason

    def test_validate_timeout_too_high(self) -> None:
        svc = ToolTimeoutService()
        is_valid, clamped, reason = svc.validate_timeout(ToolTier.FOREGROUND, 9999)
        assert is_valid is False
        assert clamped == 600
        assert "maximum" in reason

    def test_get_config(self) -> None:
        svc = ToolTimeoutService()
        cfg = svc.get_config(ToolTier.CRITICAL)
        assert cfg.default == 600
        assert cfg.max_limit == 1800

    def test_custom_timeouts(self) -> None:
        custom = {
            ToolTier.FOREGROUND: TimeoutConfig(default=60, max_limit=300),
        }
        svc = ToolTimeoutService(custom_timeouts=custom)
        assert svc.get_timeout(ToolTier.FOREGROUND) == 60
        assert svc.get_timeout(ToolTier.BACKGROUND) == 300  # unchanged

    def test_adjust_for_budget_high(self) -> None:
        svc = ToolTimeoutService()
        assert svc.adjust_for_budget(ToolTier.FOREGROUND, 100, 0.6) == 100

    def test_adjust_for_budget_medium(self) -> None:
        svc = ToolTimeoutService()
        assert svc.adjust_for_budget(ToolTier.FOREGROUND, 100, 0.3) == 75

    def test_adjust_for_budget_low(self) -> None:
        svc = ToolTimeoutService()
        assert svc.adjust_for_budget(ToolTier.FOREGROUND, 100, 0.15) == 50

    def test_adjust_for_budget_critical(self) -> None:
        svc = ToolTimeoutService()
        assert svc.adjust_for_budget(ToolTier.FOREGROUND, 100, 0.05) == 1


class TestGlobalFunctions:
    def test_get_and_reset(self) -> None:
        reset_tool_timeout_service()
        svc1 = get_tool_timeout_service()
        svc2 = get_tool_timeout_service()
        assert svc1 is svc2
        reset_tool_timeout_service()
        svc3 = get_tool_timeout_service()
        assert svc3 is not svc1
