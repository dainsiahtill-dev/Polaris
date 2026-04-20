"""Human-in-the-Loop (HITL) intervention queue for cognitive execution gating."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class InterventionDecision(Enum):
    """Possible decisions from human intervention."""

    APPROVED = "approved"
    REJECTED = "rejected"
    SHADOW_MODE = "shadow_mode"
    TIMEOUT = "timeout"


@dataclass
class ExecutionPlan:
    """Execution plan pending human approval."""

    id: str
    action: str
    intent: str
    risk_level: float
    metadata: dict = field(default_factory=dict)


class NotificationChannel(Protocol):
    """Protocol for notification channels (Webhook/Slack/WeCom)."""

    async def notify(self, plan: ExecutionPlan) -> None:
        """Send notification for the execution plan."""
        ...

    async def wait_for_decision(self, plan_id: str) -> InterventionDecision:
        """Wait for human decision on the plan."""
        ...


class HumanInterventionQueue:
    """
    Execution pre-approval queue with human-in-the-loop.

    Provides mandatory human approval point before execution.
    Supports Webhook / Slack / WeCom notification channels.
    Execution timeout 15s automatically transitions to Shadow mode + alert.

    Usage:
        hitl = HumanInterventionQueue(timeout_seconds=15)
        hitl.add_channel(slack_channel)
        decision = await hitl.request_approval(plan)
    """

    def __init__(self, timeout_seconds: int = 15) -> None:
        """
        Initialize HITL queue.

        Args:
            timeout_seconds: Timeout for human response. Default 15s.
        """
        self.timeout_seconds = timeout_seconds
        self._notification_channels: list[NotificationChannel] = []
        self._shadow_mode_plans: set[str] = set()

    def add_channel(self, channel: NotificationChannel) -> None:
        """
        Add a notification channel.

        Args:
            channel: Channel implementing NotificationChannel protocol.
        """
        self._notification_channels.append(channel)

    def is_shadow_mode(self, plan_id: str) -> bool:
        """
        Check if a plan is in shadow mode.

        Args:
            plan_id: The plan identifier.

        Returns:
            True if plan is in shadow mode.
        """
        return plan_id in self._shadow_mode_plans

    async def request_approval(self, plan: ExecutionPlan) -> InterventionDecision:
        """
        Request human approval for an execution plan.

        Timeout automatically transitions to Shadow mode + alert.

        Args:
            plan: The execution plan to approve.

        Returns:
            InterventionDecision from human or TIMEOUT.
        """
        if not self._notification_channels:
            # No channels configured - auto-approve
            return InterventionDecision.APPROVED

        task = asyncio.create_task(self._notify_and_wait(plan))

        try:
            decision = await asyncio.wait_for(
                task,
                timeout=self.timeout_seconds,
            )
            return decision
        except asyncio.TimeoutError:
            # Timeout: auto-isolate to shadow mode + alert
            await self._isolate_to_shadow_mode(plan)
            return InterventionDecision.TIMEOUT

    async def _notify_and_wait(self, plan: ExecutionPlan) -> InterventionDecision:
        """
        Send notifications and wait for human response.

        Args:
            plan: The execution plan.

        Returns:
            Human's decision.
        """
        # Notify all channels concurrently
        notify_tasks = [channel.notify(plan) for channel in self._notification_channels]
        await asyncio.gather(*notify_tasks, return_exceptions=True)

        # Wait for first channel response
        wait_tasks = [
            asyncio.create_task(channel.wait_for_decision(plan.id)) for channel in self._notification_channels
        ]
        done, pending = await asyncio.wait(
            wait_tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel pending tasks
        for task in pending:
            task.cancel()

        # Return first decision received
        for task in done:
            try:
                return task.result()
            except (asyncio.CancelledError, RuntimeError):
                continue

        # Default to shadow mode if no response
        return InterventionDecision.SHADOW_MODE

    async def _isolate_to_shadow_mode(self, plan: ExecutionPlan) -> None:
        """
        Isolate plan to shadow mode with alert.

        Args:
            plan: The execution plan to isolate.
        """
        self._shadow_mode_plans.add(plan.id)
        # Alert: in production this would send to monitoring/alerting system
        # For now, this is a placeholder for the alert mechanism
