"""Workflow runtime, orchestration, and vision-service errors.

Internal submodule of :mod:`polaris.kernelone.errors`.
Public symbols are re-exported from the package ``__init__``.
"""

from __future__ import annotations

from polaris.kernelone.errors._base import KernelOneError


class WorkflowRuntimeError(KernelOneError):
    """Workflow runtime error."""

    def __init__(
        self,
        message: str,
        *,
        workflow_id: str = "",
        code: str = "WORKFLOW_RUNTIME_ERROR",
        **kwargs,
    ) -> None:
        super().__init__(message, code=code, **kwargs)
        if workflow_id:
            self.details["workflow_id"] = workflow_id


class WorkflowUnavailableError(WorkflowRuntimeError):
    """Workflow unavailable error."""

    def __init__(
        self,
        message: str,
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", True)
        super().__init__(
            message,
            code="WORKFLOW_UNAVAILABLE_ERROR",
            **kwargs,
        )


class ProcessRunnerError(WorkflowRuntimeError):
    """Process runner error."""

    def __init__(
        self,
        message: str,
        *,
        process_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="PROCESS_RUNNER_ERROR",
            **kwargs,
        )
        if process_id:
            self.details["process_id"] = process_id


class LauncherError(WorkflowRuntimeError):
    """Launcher error."""

    def __init__(
        self,
        message: str,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="LAUNCHER_ERROR",
            **kwargs,
        )


class OrchestrationError(WorkflowRuntimeError):
    """Orchestration error."""

    def __init__(
        self,
        message: str,
        *,
        orchestration_type: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="ORCHESTRATION_ERROR",
            **kwargs,
        )
        if orchestration_type:
            self.details["orchestration_type"] = orchestration_type


class VisionServiceError(KernelOneError):
    """Vision service error."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "VISION_SERVICE_ERROR",
        **kwargs,
    ) -> None:
        super().__init__(message, code=code, **kwargs)


class VisionNotAvailableError(VisionServiceError):
    """Vision not available error."""

    def __init__(
        self,
        message: str,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="VISION_NOT_AVAILABLE_ERROR",
            **kwargs,
        )


class InferenceEngineNotConfiguredError(VisionServiceError):
    """Inference engine not configured error."""

    def __init__(
        self,
        message: str,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="INFERENCE_ENGINE_NOT_CONFIGURED_ERROR",
            **kwargs,
        )
