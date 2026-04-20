"""TurnEngine quota management - Resource quota checking and recording.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

职责：
    封装 TurnEngine 中的配额检查与记录逻辑。
    包括 turn 级配额预检和并发工具配额槽位管理。
"""

from __future__ import annotations

import logging

from polaris.kernelone.resource.quota import QuotaStatus, ResourceQuotaManager

logger = logging.getLogger(__name__)


class TurnQuotaManager:
    """Manages resource quota checks and turn recording for TurnEngine."""

    def __init__(self) -> None:
        """Initialize with lazy-loaded global quota manager."""
        self._manager: ResourceQuotaManager | None = None

    def _get_manager(self) -> ResourceQuotaManager:
        """Get the global quota manager instance (lazy singleton)."""
        if self._manager is None:
            from polaris.kernelone.resource import get_global_quota_manager

            self._manager = get_global_quota_manager()
        return self._manager

    @staticmethod
    def build_agent_id(role: str, workspace: str, run_id: str | None = None) -> str:
        """Generate a stable agent ID for quota tracking.

        Args:
            role: Role identifier (e.g., "pm", "director").
            workspace: Workspace path.
            run_id: Optional run identifier for additional specificity.

        Returns:
            A unique agent ID string for quota management.
        """
        base = f"{role}@{workspace}"
        if run_id:
            return f"{base}@{run_id}"
        return base

    def check_before_turn(self, agent_id: str) -> tuple[bool, str]:
        """Check if an agent can proceed with a new turn.

        If the agent is not registered in the quota system, it is automatically
        allocated with default quotas.

        Args:
            agent_id: Unique identifier for the agent.

        Returns:
            Tuple of (allowed, reason). If allowed is True, the turn can proceed.
            If allowed is False, reason contains the denial message.
        """
        try:
            manager = self._get_manager()
            try:
                status = manager.check_quota(agent_id)
            except KeyError:
                manager.allocate(agent_id)
                status = manager.check_quota(agent_id)

            if status == QuotaStatus.SYSTEM_OVERLOADED:
                return (False, "System resource limits exceeded")
            if status in (QuotaStatus.DENIED_SINGLE, QuotaStatus.DENIED_MULTIPLE):
                usage = manager.get_usage(agent_id)
                return (
                    False,
                    f"Quota exceeded: turns={usage.turns}, concurrent_tools={usage.concurrent_tools}, "
                    f"wall_time={usage.wall_time_seconds}s",
                )
            return (True, "")
        except (KeyError, ValueError, RuntimeError) as exc:
            logger.debug("[TurnQuotaManager] Quota check failed (allowing turn): %s", exc)
            return (True, "")

    def record_turn(self, agent_id: str, wall_time_delta: float = 0.0) -> None:
        """Record turn completion and update quota usage.

        Args:
            agent_id: Unique identifier for the agent.
            wall_time_delta: Wall-clock time elapsed since last recording.
        """
        try:
            manager = self._get_manager()
            try:
                manager.increment_turn(agent_id)
                if wall_time_delta > 0:
                    manager.add_wall_time(agent_id, wall_time_delta)
            except KeyError:
                pass
        except (KeyError, ValueError, RuntimeError) as exc:
            logger.debug("[TurnQuotaManager] Failed to record turn in quota: %s", exc)

    def acquire_concurrent_tool(self, agent_id: str) -> bool:
        """Acquire a concurrent tool slot before execution.

        Args:
            agent_id: Unique identifier for the agent.

        Returns:
            True if a slot was acquired, False if quota exceeded.
        """
        try:
            manager = self._get_manager()
            return manager.acquire_concurrent_tool(agent_id)
        except (KeyError, ValueError, RuntimeError) as exc:
            logger.debug("[TurnQuotaManager] Failed to acquire tool quota (allowing execution): %s", exc)
            return True

    def release_concurrent_tool(self, agent_id: str) -> None:
        """Release a concurrent tool slot after execution.

        Args:
            agent_id: Unique identifier for the agent.
        """
        try:
            manager = self._get_manager()
            manager.release_concurrent_tool(agent_id)
        except (KeyError, ValueError, RuntimeError) as exc:
            logger.debug("[TurnQuotaManager] Failed to release tool quota: %s", exc)


__all__ = ["TurnQuotaManager"]
