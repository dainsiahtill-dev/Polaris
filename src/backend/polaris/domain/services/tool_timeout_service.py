"""Tool timeout service with tiered limits for Polaris backend.

Provides different timeout limits for foreground vs background tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ToolTier(str, Enum):
    """Tool execution tier."""

    FOREGROUND = "foreground"  # Interactive tools
    BACKGROUND = "background"  # Background/async tools
    CRITICAL = "critical"  # Critical path tools
    FAST = "fast"  # Quick checks


@dataclass
class TimeoutConfig:
    """Timeout configuration for a tier."""

    default: int  # Default timeout in seconds
    max_limit: int  # Maximum allowed timeout
    min_limit: int = 1  # Minimum allowed timeout


class ToolTimeoutService:
    """Service for managing tool timeouts with tiered limits.

    Provides:
    - Tiered timeout defaults (foreground: 120s, background: 300s)
    - Timeout validation and clamping
    - Budget-aware timeout adjustments
    """

    DEFAULT_TIMEOUTS: dict[ToolTier, TimeoutConfig] = {
        ToolTier.FOREGROUND: TimeoutConfig(
            default=120,  # 2 minutes for interactive tools
            max_limit=600,  # 10 minutes max
            min_limit=1,
        ),
        ToolTier.BACKGROUND: TimeoutConfig(
            default=300,  # 5 minutes for background tools
            max_limit=3600,  # 1 hour max
            min_limit=10,
        ),
        ToolTier.CRITICAL: TimeoutConfig(
            default=600,  # 10 minutes for critical operations
            max_limit=1800,  # 30 minutes max
            min_limit=5,
        ),
        ToolTier.FAST: TimeoutConfig(
            default=30,  # 30 seconds for quick checks
            max_limit=60,  # 1 minute max
            min_limit=1,
        ),
    }

    def __init__(
        self,
        custom_timeouts: dict[ToolTier, TimeoutConfig] | None = None,
    ) -> None:
        """Initialize timeout service.

        Args:
            custom_timeouts: Override default timeout configs
        """
        self._timeouts = self.DEFAULT_TIMEOUTS.copy()
        if custom_timeouts:
            self._timeouts.update(custom_timeouts)

    def get_timeout(
        self,
        tier: ToolTier | str,
        requested: int | None = None,
    ) -> int:
        """Get validated timeout for a tier.

        Args:
            tier: Tool tier
            requested: Requested timeout (uses default if None)

        Returns:
            Validated timeout in seconds
        """
        if isinstance(tier, str):
            tier = ToolTier(tier)

        config = self._timeouts[tier]

        if requested is None:
            return config.default

        # Clamp to valid range
        return max(config.min_limit, min(requested, config.max_limit))

    def validate_timeout(
        self,
        tier: ToolTier | str,
        timeout: int,
    ) -> tuple[bool, int, str]:
        """Validate a timeout request.

        Args:
            tier: Tool tier
            timeout: Requested timeout

        Returns:
            Tuple of (is_valid, clamped_timeout, reason)
        """
        if isinstance(tier, str):
            tier = ToolTier(tier)

        config = self._timeouts[tier]
        clamped = max(config.min_limit, min(timeout, config.max_limit))

        if timeout < config.min_limit:
            return False, clamped, f"Timeout below minimum {config.min_limit}s"
        if timeout > config.max_limit:
            return False, clamped, f"Timeout exceeds maximum {config.max_limit}s"

        return True, timeout, "OK"

    def get_config(self, tier: ToolTier | str) -> TimeoutConfig:
        """Get timeout config for a tier.

        Args:
            tier: Tool tier

        Returns:
            Timeout configuration
        """
        if isinstance(tier, str):
            tier = ToolTier(tier)
        return self._timeouts[tier]

    def adjust_for_budget(
        self,
        tier: ToolTier | str,
        base_timeout: int,
        budget_remaining: float,  # 0.0 to 1.0
    ) -> int:
        """Adjust timeout based on budget constraints.

        Args:
            tier: Tool tier
            base_timeout: Base timeout
            budget_remaining: Remaining budget ratio (0.0 to 1.0)

        Returns:
            Adjusted timeout
        """
        if budget_remaining > 0.5:
            # Plenty of budget, use full timeout
            return base_timeout
        elif budget_remaining > 0.2:
            # Reduce by 25%
            return int(base_timeout * 0.75)
        elif budget_remaining > 0.1:
            # Reduce by 50%
            return int(base_timeout * 0.5)
        else:
            # Critical budget, use minimum
            config = self.get_config(tier)
            return config.min_limit


# Global instance
_timeout_service: ToolTimeoutService | None = None


def get_tool_timeout_service() -> ToolTimeoutService:
    """Get global timeout service."""
    global _timeout_service
    if _timeout_service is None:
        _timeout_service = ToolTimeoutService()
    return _timeout_service


def reset_tool_timeout_service() -> None:
    """Reset global timeout service."""
    global _timeout_service
    _timeout_service = None
