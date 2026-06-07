"""Verification system for Director v2 - Anti-hallucination mechanisms.

Migrated from old Director's multi-layer defense:
- ExistenceGate: File existence pre-check
- SoftCheck: Progressive validation (missing files + unresolved imports)
- ProgressDelta: Stall detection
- ImpactAnalyzer: Risk assessment for changes
- EvidenceCollector: Detailed evidence for audit
"""

from .evidence_collector import (
    EvidenceCollector,
    EvidencePackage,
    EvidenceType,
    FileEvidence,
    LLMEvidence,
    ToolEvidence,
    VerificationEvidence,
    create_evidence_collector,
)
from .existence_gate import ExistenceGate, GateResult, check_mode
from .impact_analyzer import ImpactAnalyzer, ImpactResult, RiskLevel, analyze_impact, assess_patch_risk
from .progress_delta import ProgressDelta, ProgressTracker, compute_progress_delta, detect_stall
from .soft_check import SoftCheck, SoftCheckResult, check_missing_targets, detect_unresolved_imports

__all__ = [
    "EvidenceCollector",
    "EvidencePackage",
    "EvidenceType",
    "ExistenceGate",
    "FileEvidence",
    "GateResult",
    "ImpactAnalyzer",
    "ImpactResult",
    "LLMEvidence",
    "ProgressDelta",
    "ProgressTracker",
    "RiskLevel",
    "SoftCheck",
    "SoftCheckResult",
    "ToolEvidence",
    "VerificationEvidence",
    "analyze_impact",
    "assess_patch_risk",
    "check_missing_targets",
    "check_mode",
    "compute_progress_delta",
    "create_evidence_collector",
    "detect_stall",
    "detect_unresolved_imports",
]
