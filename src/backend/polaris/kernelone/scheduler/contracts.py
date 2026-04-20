"""Scheduler subsystem contracts for KernelOne.

This module defines the stable port surface for the scheduler subsystem.
The canonical SchedulerPort is declared in
``polaris.kernelone.contracts.technical.master_types``.

This file re-exports it and provides additional type aliases for convenience.

Architecture:
    - SchedulerPort (from master_types): the core async scheduling contract
    - SimpleScheduler implements SchedulerPort as the default in-process adapter
    - Replace SimpleScheduler with a persistent adapter (Redis, NATS JetStream, etc.)
      for distributed scenarios

Design constraints:
    - KernelOne-only: no Polaris business semantics
    - All schedule kinds (ONCE/PERIODIC/DELAYED/CRON) must be supported
    - Handler execution must be injectable (not hard-coded)
"""

from __future__ import annotations

from polaris.kernelone.contracts.technical import (
    ScheduledTask,
    ScheduleKind,
    ScheduleResult,
    SchedulerPort,
    ScheduleSpec,
)

__all__ = [
    "ScheduleKind",
    "ScheduleResult",
    "ScheduleSpec",
    "ScheduledTask",
    "SchedulerPort",
]
