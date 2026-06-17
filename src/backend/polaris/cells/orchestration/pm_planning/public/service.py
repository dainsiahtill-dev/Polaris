"""Stable service exports for orchestration.pm_planning."""

from __future__ import annotations

from ..internal.pm_agent import PMAgent
from ..internal.project_report import ProjectReport, build_pm_project_report
from ..internal.shared_quality import (
    detect_integration_verify_command,
    run_integration_verify_runner,
)
from ..internal.task_quality_gate import (
    autofix_pm_contract_for_quality,
    evaluate_pm_task_quality,
)
from ..service import PMService, ProcessHandle, get_pm_service, reset_pm_service
from .contracts import (
    GeneratePmTaskContractCommandV1,
    GetPmPlanningStatusQueryV1,
    PmPlanningError,
    PmTaskContractGeneratedEventV1,
    PmTaskContractResultV1,
)

__all__ = [
    "GeneratePmTaskContractCommandV1",
    "GetPmPlanningStatusQueryV1",
    "PMAgent",
    "PMService",
    "PmPlanningError",
    "PmTaskContractGeneratedEventV1",
    "PmTaskContractResultV1",
    "ProcessHandle",
    "ProjectReport",
    "autofix_pm_contract_for_quality",
    "build_pm_project_report",
    "detect_integration_verify_command",
    "evaluate_pm_task_quality",
    "get_pm_service",
    "reset_pm_service",
    "run_integration_verify_runner",
]
