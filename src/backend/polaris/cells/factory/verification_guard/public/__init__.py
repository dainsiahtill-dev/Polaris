"""Public boundary for `factory.verification_guard`."""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "CompletionEvidenceStateV1",
    "CompletionEvidenceStatusV1",
    "CompletionNextActionV1",
    "CompletionRetryClassV1",
    "ExecutionResult",
    "IProjectCompletionDiagnosticsService",
    "IVerificationGuardService",
    "ProjectArtifactObligationObservationV1",
    "ProjectCompletionContractObservationV1",
    "ProjectCompletionDiagnosticV1",
    "ProjectCompletionDiagnosticsV1",
    "ProjectCompletionEvidenceV1",
    "ProjectCompletionObligationsObservationV1",
    "ProjectCompletionOwnerEvidenceBundleV1",
    "ProjectCompletionOwnerObservationPortV1",
    "ProjectCompletionOwnerObservationV1",
    "ProjectCompletionOwnerObservationV1Error",
    "ProjectCompletionPhysicalArtifactInputV1",
    "ProjectCompletionPhysicalEvidenceEffectV1",
    "ProjectCompletionPhysicalEvidenceIntentV1",
    "ProjectCompletionPhysicalEvidencePortV1",
    "ProjectEntrypointObligationObservationV1",
    "ProjectKindAuthorityObservationV1",
    "ProjectKindObservationV1",
    "ProjectRepairCoverageV1",
    "ProjectVerificationCommandAuthorityObservationV1",
    "ProjectVerificationModalityObservationV1",
    "ProjectVerificationObligationObservationV1",
    "QueryProjectCompletionDiagnosticsV1",
    "RepairCoverageStatusV1",
    "RunProjectCompletionEvidenceCommandV1",
    "RunProjectCompletionEvidenceResultV1",
    "VerificationClaim",
    "VerificationCompletedEventV1",
    "VerificationGuardErrorV1",
    "VerificationGuardService",
    "VerificationReport",
    "VerificationStatus",
    "VerifyCompletionCommandV1",
    "VerifyCompletionResultV1",
    "bind_project_completion_owner_observation_port",
    "bind_project_completion_physical_evidence_port",
    "get_verification_guard_service",
    "query_project_completion_diagnostics",
    "reset_verification_guard_service",
    "run_project_completion_evidence",
    "verify_completion",
]

_CONTRACT_EXPORTS = frozenset(
    {
        "CompletionEvidenceStateV1",
        "CompletionEvidenceStatusV1",
        "CompletionNextActionV1",
        "CompletionRetryClassV1",
        "ExecutionResult",
        "IProjectCompletionDiagnosticsService",
        "IVerificationGuardService",
        "ProjectCompletionDiagnosticV1",
        "ProjectCompletionDiagnosticsV1",
        "ProjectCompletionContractObservationV1",
        "ProjectCompletionObligationsObservationV1",
        "ProjectCompletionEvidenceV1",
        "ProjectCompletionOwnerEvidenceBundleV1",
        "ProjectCompletionOwnerObservationPortV1",
        "ProjectCompletionOwnerObservationV1",
        "ProjectCompletionOwnerObservationV1Error",
        "ProjectCompletionPhysicalArtifactInputV1",
        "ProjectCompletionPhysicalEvidenceEffectV1",
        "ProjectCompletionPhysicalEvidenceIntentV1",
        "ProjectCompletionPhysicalEvidencePortV1",
        "ProjectRepairCoverageV1",
        "ProjectArtifactObligationObservationV1",
        "ProjectEntrypointObligationObservationV1",
        "ProjectKindObservationV1",
        "ProjectKindAuthorityObservationV1",
        "ProjectVerificationCommandAuthorityObservationV1",
        "ProjectVerificationModalityObservationV1",
        "ProjectVerificationObligationObservationV1",
        "QueryProjectCompletionDiagnosticsV1",
        "RepairCoverageStatusV1",
        "VerificationClaim",
        "VerificationCompletedEventV1",
        "VerificationGuardErrorV1",
        "VerificationReport",
        "VerificationStatus",
        "VerifyCompletionCommandV1",
        "VerifyCompletionResultV1",
        "RunProjectCompletionEvidenceCommandV1",
        "RunProjectCompletionEvidenceResultV1",
    }
)
_SERVICE_EXPORTS = frozenset(
    {
        "VerificationGuardService",
        "get_verification_guard_service",
        "query_project_completion_diagnostics",
        "reset_verification_guard_service",
        "run_project_completion_evidence",
        "verify_completion",
    }
)
_BOOTSTRAP_EXPORTS = frozenset(
    {
        "bind_project_completion_owner_observation_port",
        "bind_project_completion_physical_evidence_port",
    }
)


def __getattr__(name: str) -> object:
    if name in _CONTRACT_EXPORTS:
        module = import_module("polaris.cells.factory.verification_guard.public.contracts")
    elif name in _SERVICE_EXPORTS:
        module = import_module("polaris.cells.factory.verification_guard.public.service")
    elif name in _BOOTSTRAP_EXPORTS:
        module = import_module("polaris.cells.factory.verification_guard.public.bootstrap")
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
