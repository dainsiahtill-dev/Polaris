"""Application-layer orchestration facades for PM and Director domains.

This package provides high-level orchestration services that encapsulate
workflow logic for the PM (Project Manager) and Director domains.  Each
orchelator is a thin facade that delegates to Cell public contracts and
KernelOne primitives — no business logic lives here.

Call chain (canonical pattern)::

    delivery -> application.orchestration -> cells.*.public / kernelone

Public surface:
    - ``PmOrchestrator``:  PM iteration lifecycle (planning, dispatch,
      blocked-policy, finalization).
    - ``DirectorOrchestrator``: Director task execution lifecycle
      (task discovery, role-session execution, result aggregation).

Architecture constraints (AGENTS.md):
    - Imports ONLY from Cell ``public/`` boundaries and ``kernelone`` contracts.
    - NEVER imports from ``internal/`` at module level.
    - All text I/O uses explicit UTF-8.
"""

from __future__ import annotations

from polaris.application.orchestration.director_orchestrator import (
    DirectorOrchestrator,
    DirectorOrchestratorError,
)
from polaris.application.orchestration.pm_orchestrator import (
    PmOrchestrator,
    PmOrchestratorError,
)

__all__ = [
    "DirectorOrchestrator",
    "DirectorOrchestratorError",
    "PmOrchestrator",
    "PmOrchestratorError",
]
