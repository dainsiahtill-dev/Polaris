"""Configuration helpers for Polaris workflow orchestration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

from polaris.kernelone.constants import MAX_WORKFLOW_TIMEOUT_SECONDS

SUPPORTED_ORCHESTRATION_RUNTIMES = ("workflow",)
_TRUTHY_VALUES = {"1", "true", "yes", "on"}


def _parse_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    if not token:
        return default
    if token in _TRUTHY_VALUES:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_int(value: object, default: int) -> int:
    try:
        return int(str(value).strip())
    except (RuntimeError, ValueError):
        return default


def _parse_float(value: object, default: float) -> float:
    try:
        return float(str(value).strip())
    except (RuntimeError, ValueError):
        return default


def resolve_orchestration_runtime(
    raw: object = "",
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    values = dict(environ or os.environ)

    token = str(raw or values.get("KERNELONE_ORCHESTRATION_RUNTIME", "workflow") or "workflow").strip().lower()

    if token not in SUPPORTED_ORCHESTRATION_RUNTIMES:
        return "workflow"
    return token


@dataclass(frozen=True)
class InternalWorkflowConfig:
    """Runtime configuration for self-hosted workflow runtime."""

    enabled: bool = True
    namespace: str = "polaris"
    task_queue: str = "polaris-queue"
    retry_max_attempts: int = 3
    retry_initial_interval_seconds: float = 1.0
    retry_backoff_coefficient: float = 2.0
    workflow_execution_timeout_seconds: int = MAX_WORKFLOW_TIMEOUT_SECONDS
    rpc_timeout_seconds: float = 1.0

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        force_enable: bool = False,
    ) -> InternalWorkflowConfig:
        values = dict(environ or os.environ)
        return cls(
            # workflow is the only supported runtime and is always enabled.
            enabled=True,
            namespace=str(values.get("KERNELONE_WORKFLOW_NAMESPACE", "polaris") or "polaris"),
            task_queue=str(values.get("KERNELONE_WORKFLOW_TASK_QUEUE", "polaris-queue") or "polaris-queue"),
            retry_max_attempts=max(
                1,
                _parse_int(values.get("KERNELONE_WORKFLOW_RETRY_MAX_ATTEMPTS", 3), 3),
            ),
            retry_initial_interval_seconds=max(
                0.1,
                _parse_float(
                    values.get("KERNELONE_WORKFLOW_RETRY_INITIAL_INTERVAL_SECONDS", 1.0),
                    1.0,
                ),
            ),
            retry_backoff_coefficient=max(
                1.0,
                _parse_float(
                    values.get("KERNELONE_WORKFLOW_RETRY_BACKOFF_COEFFICIENT", 2.0),
                    2.0,
                ),
            ),
            workflow_execution_timeout_seconds=max(
                60,
                _parse_int(
                    values.get("KERNELONE_WORKFLOW_WORKFLOW_TIMEOUT_SECONDS", MAX_WORKFLOW_TIMEOUT_SECONDS),
                    MAX_WORKFLOW_TIMEOUT_SECONDS,
                ),
            ),
            rpc_timeout_seconds=max(
                0.1,
                _parse_float(
                    values.get("KERNELONE_WORKFLOW_RPC_TIMEOUT_SECONDS", 1.0),
                    1.0,
                ),
            ),
        )


# Backward-compatible alias - avoid name collision with kernelone.workflow.base.WorkflowConfig
WorkflowConfig = InternalWorkflowConfig
