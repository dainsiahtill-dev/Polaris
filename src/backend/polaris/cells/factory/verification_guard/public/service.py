"""Public service boundary for `factory.verification_guard`."""

from __future__ import annotations

from polaris.cells.factory.verification_guard.internal.project_completion_authority import (
    observe_project_completion_owner,
)
from polaris.cells.factory.verification_guard.internal.project_completion_diagnostics import (
    evaluate_project_completion_bundle,
)
from polaris.cells.factory.verification_guard.internal.project_physical_evidence import (
    run_project_completion_evidence as _run_project_completion_evidence,
)
from polaris.cells.factory.verification_guard.internal.safe_executor import (
    SafeExecutor,
)
from polaris.cells.factory.verification_guard.internal.verification_engine import (
    VerificationEngine,
)
from polaris.cells.factory.verification_guard.public.contracts import (
    IVerificationGuardService,
    ProjectCompletionDiagnosticsV1,
    ProjectCompletionOwnerObservationV1Error,
    QueryProjectCompletionDiagnosticsV1,
    RunProjectCompletionEvidenceCommandV1,
    RunProjectCompletionEvidenceResultV1,
    VerificationStatus,
    VerifyCompletionCommandV1,
    VerifyCompletionResultV1,
)


class VerificationGuardService(IVerificationGuardService):
    """Thin public wrapper over the verification engine."""

    def __init__(self, engine: VerificationEngine | None = None) -> None:
        self._engine = engine or VerificationEngine()

    def verify_completion(self, command: VerifyCompletionCommandV1) -> VerifyCompletionResultV1:
        """Verify a completion claim through the public command contract."""
        if not isinstance(command, VerifyCompletionCommandV1):
            raise TypeError("command must be a VerifyCompletionCommandV1")
        engine = self._engine
        if command.allowed_commands:
            engine = VerificationEngine(safe_executor=SafeExecutor(whitelist=command.allowed_commands))
        report = engine.verify(
            command.claim,
            workspace=command.workspace,
            strict_mode=command.strict_mode,
        )
        return VerifyCompletionResultV1(ok=report.status == VerificationStatus.PASS, report=report)

    def query_project_completion_diagnostics(
        self,
        query: QueryProjectCompletionDiagnosticsV1,
    ) -> ProjectCompletionDiagnosticsV1:
        """Evaluate the exact CE contract against bootstrap-bound owner facts."""

        if type(query) is not QueryProjectCompletionDiagnosticsV1:
            raise ProjectCompletionOwnerObservationV1Error(
                "invalid_project_completion_query_type",
                "Query must be an exact QueryProjectCompletionDiagnosticsV1",
            )
        return evaluate_project_completion_bundle(observe_project_completion_owner(query))

    def run_project_completion_evidence(
        self,
        command: RunProjectCompletionEvidenceCommandV1,
    ) -> RunProjectCompletionEvidenceResultV1:
        """Materialize one physical obligation effect without completion verdict."""

        return _run_project_completion_evidence(command)


_SERVICE_SINGLETON: VerificationGuardService | None = None


def get_verification_guard_service() -> VerificationGuardService:
    """Return the process-local verification guard service."""
    global _SERVICE_SINGLETON
    if _SERVICE_SINGLETON is None:
        _SERVICE_SINGLETON = VerificationGuardService()
    return _SERVICE_SINGLETON


def reset_verification_guard_service() -> None:
    """Reset the process-local verification guard service."""
    global _SERVICE_SINGLETON
    _SERVICE_SINGLETON = None


def verify_completion(command: VerifyCompletionCommandV1) -> VerifyCompletionResultV1:
    """Verify a completion claim through the default service."""
    return get_verification_guard_service().verify_completion(command)


def query_project_completion_diagnostics(
    query: QueryProjectCompletionDiagnosticsV1,
) -> ProjectCompletionDiagnosticsV1:
    """Query authoritative project-completion diagnostics."""

    return get_verification_guard_service().query_project_completion_diagnostics(query)


def run_project_completion_evidence(
    command: RunProjectCompletionEvidenceCommandV1,
) -> RunProjectCompletionEvidenceResultV1:
    """Materialize one exact contract obligation through its physical owner."""

    return get_verification_guard_service().run_project_completion_evidence(command)


__all__ = [
    "VerificationGuardService",
    "get_verification_guard_service",
    "query_project_completion_diagnostics",
    "reset_verification_guard_service",
    "run_project_completion_evidence",
    "verify_completion",
]
