"""Public boundary for `factory.verification_guard`."""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "ExecutionResult",
    "IVerificationGuardService",
    "VerificationClaim",
    "VerificationCompletedEventV1",
    "VerificationGuardErrorV1",
    "VerificationGuardService",
    "VerificationReport",
    "VerificationStatus",
    "VerifyCompletionCommandV1",
    "VerifyCompletionResultV1",
    "get_verification_guard_service",
    "reset_verification_guard_service",
    "verify_completion",
]

_CONTRACT_EXPORTS = frozenset(
    {
        "ExecutionResult",
        "IVerificationGuardService",
        "VerificationClaim",
        "VerificationCompletedEventV1",
        "VerificationGuardErrorV1",
        "VerificationReport",
        "VerificationStatus",
        "VerifyCompletionCommandV1",
        "VerifyCompletionResultV1",
    }
)
_SERVICE_EXPORTS = frozenset(
    {
        "VerificationGuardService",
        "get_verification_guard_service",
        "reset_verification_guard_service",
        "verify_completion",
    }
)


def __getattr__(name: str) -> object:
    if name in _CONTRACT_EXPORTS:
        module = import_module("polaris.cells.factory.verification_guard.public.contracts")
    elif name in _SERVICE_EXPORTS:
        module = import_module("polaris.cells.factory.verification_guard.public.service")
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
