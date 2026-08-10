"""Governance enums for chief_engineer.blueprint public contracts."""

from __future__ import annotations

from enum import Enum

# ---------------------------------------------------------------------------
# Enums (Tier-1 governance surface)
# ---------------------------------------------------------------------------


class RiskSeverity(str, Enum):
    """Severity ladder for a Risk Register entry."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    BLOCKER = "blocker"


class RiskStatus(str, Enum):
    """Lifecycle status of a Risk Register entry."""

    OPEN = "open"
    MITIGATING = "mitigating"
    ACCEPTED = "accepted"
    RESOLVED = "resolved"
    REVERTED = "reverted"


class TechDebtSeverity(str, Enum):
    """Severity ladder for a Tech-Debt Ledger entry."""

    TRIVIAL = "trivial"
    MINOR = "minor"
    MAJOR = "major"
    SEVERE = "severe"
    FATAL = "fatal"


class TechDebtStatus(str, Enum):
    """Lifecycle status of a Tech-Debt Ledger entry."""

    REGISTERED = "registered"
    ACKNOWLEDGED = "acknowledged"
    SCHEDULED = "scheduled"
    PAID = "paid"
    WONTFIX = "wontfix"


class RollbackStrategy(str, Enum):
    """Rollback strategy attached to a blueprint."""

    GIT_REVERT = "git_revert"
    MANIFEST_RESTORE = "manifest_restore"
    FILE_SNAPSHOT = "file_snapshot"


class ADRStatus(str, Enum):
    """Lifecycle status of an Architecture Decision Record (decision log).

    This is the human-facing decision-log status — distinct from the
    internal construction-plan ADR compiler in ``adr_store.py``.
    """

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"


class TechRadarRing(str, Enum):
    """ThoughtWorks-style Tech-Radar ring for a library / technology.

    ``adopt`` and ``trial`` are permitted; ``hold`` and ``deprecated`` are
    stack-policy violations when a blueprint depends on them.
    """

    ADOPT = "adopt"
    TRIAL = "trial"
    HOLD = "hold"
    DEPRECATED = "deprecated"


class IncidentSeverity(str, Enum):
    """Incident severity ladder for a post-mortem (sev1 = most severe)."""

    SEV1 = "sev1"
    SEV2 = "sev2"
    SEV3 = "sev3"
    SEV4 = "sev4"


class ReleaseDecision(str, Enum):
    """Executive release / change-advisory verdict.

    ``go`` = clear; ``conditional_go`` = warnings only (ship with awareness);
    ``no_go`` = at least one hard blocker (release must not proceed).
    """

    GO = "go"
    CONDITIONAL_GO = "conditional_go"
    NO_GO = "no_go"


class PostMortemStatus(str, Enum):
    """Lifecycle status of a post-mortem / incident review."""

    DRAFT = "draft"
    REVIEWING = "reviewing"
    PUBLISHED = "published"
    ACTIONS_OPEN = "actions_open"
    CLOSED = "closed"
