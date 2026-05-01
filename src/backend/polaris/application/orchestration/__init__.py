"""Application-layer orchestration facades for PM, Director, QA, and Architect domains.

This package provides high-level orchestration services that encapsulate
workflow logic for the PM (Project Manager), Director, QA, and Architect
domains.  Each orchestrator is a thin facade that delegates to Cell public
contracts and KernelOne primitives — no business logic lives here.

Call chain (canonical pattern)::

    delivery -> application.orchestration -> cells.*.public / kernelone

Public surface:
    - ``PmOrchestrator``:  PM iteration lifecycle (planning, dispatch,
      blocked-policy, finalization).
    - ``DirectorOrchestrator``: Director task execution lifecycle
      (task discovery, role-session execution, result aggregation).
    - ``QaOrchestrator``: QA audit lifecycle (plan audit, execute review,
      compile verdict).
    - ``ArchitectOrchestrator``: Architecture design lifecycle (gather
      context, design, blueprint, handoff).

Architecture constraints (AGENTS.md):
    - Imports ONLY from Cell ``public/`` boundaries and ``kernelone`` contracts.
    - NEVER imports from ``internal/`` at module level.
    - All text I/O uses explicit UTF-8.
"""

from __future__ import annotations

from polaris.application.orchestration.architect_orchestrator import (
    ArchitectDesignConfig,
    ArchitectDesignLifecycleResult,
    ArchitectOrchestrator,
    ArchitectOrchestratorError,
    BlueprintResult,
    DesignResult,
)
from polaris.application.orchestration.director_orchestrator import (
    DirectorExecutionConfig,
    DirectorIterationResult,
    DirectorOrchestrator,
    DirectorOrchestratorError,
    DirectorTaskResult,
)
from polaris.application.orchestration.pm_orchestrator import (
    PmIterationContext,
    PmIterationResult,
    PmOrchestrator,
    PmOrchestratorError,
)
from polaris.application.orchestration.qa_orchestrator import (
    QaAuditConfig,
    QaAuditLifecycleResult,
    QaOrchestrator,
    QaOrchestratorError,
    QaReviewResult,
    QaVerdictResult,
)

__all__ = [
    "ArchitectDesignConfig",
    "ArchitectDesignLifecycleResult",
    "ArchitectOrchestrator",
    "ArchitectOrchestratorError",
    "BlueprintResult",
    "DesignResult",
    "DirectorExecutionConfig",
    "DirectorIterationResult",
    "DirectorOrchestrator",
    "DirectorOrchestratorError",
    "DirectorTaskResult",
    "PmIterationContext",
    "PmIterationResult",
    "PmOrchestrator",
    "PmOrchestratorError",
    "QaAuditConfig",
    "QaAuditLifecycleResult",
    "QaOrchestrator",
    "QaOrchestratorError",
    "QaReviewResult",
    "QaVerdictResult",
]
