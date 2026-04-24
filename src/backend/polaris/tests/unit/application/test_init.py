"""Tests for polaris.application.__init__ re-exports."""

from __future__ import annotations

import polaris.application as app


class TestApplicationExports:
    def test_domain_exceptions_exported(self) -> None:
        assert hasattr(app, "DomainException")
        assert hasattr(app, "ValidationError")
        assert hasattr(app, "NotFoundError")
        assert hasattr(app, "ConflictError")
        assert hasattr(app, "PermissionDeniedError")
        assert hasattr(app, "ServiceUnavailableError")
        assert hasattr(app, "BusinessRuleError")
        assert hasattr(app, "ProcessError")
        assert hasattr(app, "LLMError")
        assert hasattr(app, "InfrastructureError")
        assert hasattr(app, "StorageError")
        assert hasattr(app, "NetworkError")
        assert hasattr(app, "ExternalServiceError")
        assert hasattr(app, "AuthenticationError")
        assert hasattr(app, "RateLimitError")
        assert hasattr(app, "StateError")
        assert hasattr(app, "ConfigurationError")
        assert hasattr(app, "TimeoutError")

    def test_domain_entities_exported(self) -> None:
        assert hasattr(app, "Task")
        assert hasattr(app, "TaskStatus")
        assert hasattr(app, "TaskPriority")
        assert hasattr(app, "TaskResult")
        assert hasattr(app, "TaskEvidence")
        assert hasattr(app, "TaskStateError")

    def test_worker_entities_exported(self) -> None:
        assert hasattr(app, "Worker")
        assert hasattr(app, "WorkerStatus")
        assert hasattr(app, "WorkerType")
        assert hasattr(app, "WorkerHealth")
        assert hasattr(app, "WorkerCapabilities")
        assert hasattr(app, "WorkerStateError")

    def test_kernelone_contracts_exported(self) -> None:
        assert hasattr(app, "Result")
        assert hasattr(app, "KernelError")
        assert hasattr(app, "KernelOneError")
        assert hasattr(app, "TaggedError")
        assert hasattr(app, "Effect")
        assert hasattr(app, "EffectTracker")
        assert hasattr(app, "Envelope")
        assert hasattr(app, "TraceContext")
        assert hasattr(app, "StreamChunk")
        assert hasattr(app, "SubsystemHealth")
        assert hasattr(app, "LockPort")
        assert hasattr(app, "LockOptions")
        assert hasattr(app, "LockAcquireResult")
        assert hasattr(app, "SchedulerPort")
        assert hasattr(app, "ScheduledTask")
        assert hasattr(app, "ScheduleResult")

    def test_services_exported(self) -> None:
        assert hasattr(app, "BackgroundTask")
        assert hasattr(app, "BackgroundTaskService")
        assert hasattr(app, "ExecutionResult")
        assert hasattr(app, "LLMCompactService")
        assert hasattr(app, "SecurityService")
        assert hasattr(app, "ToolTimeoutService")
        assert hasattr(app, "TranscriptService")
        assert hasattr(app, "TokenService")

    def test_models_exported(self) -> None:
        assert hasattr(app, "ConfigSnapshot")
        assert hasattr(app, "ConfigValidationResult")
