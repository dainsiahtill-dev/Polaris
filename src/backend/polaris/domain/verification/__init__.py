"""Verification system for Director v2 - Anti-hallucination mechanisms.

Migrated from old Director's multi-layer defense:
- ExistenceGate: File existence pre-check
- SoftCheck: Progressive validation (missing files + unresolved imports)
- WriteGate: Write scope validation
- ProgressDelta: Stall detection
- ImpactAnalyzer: Risk assessment for changes
- EvidenceCollector: Detailed evidence for audit
"""

from .director_policy_gate import (
    DirectorPolicyObject,
    DirectorWritePolicyVerdict,
    ForbiddenPathRule,
    PackageManifestDiff,
    SectionDiff,
    diff_package_manifest,
    parse_agents_write_policy,
    validate_director_write_policy,
)
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
from .write_gate import WriteGate, WriteGateResult, validate_write_scope

__all__ = [
    "DirectorPolicyObject",
    "DirectorWritePolicyVerdict",
    "EvidenceCollector",
    "EvidencePackage",
    "EvidenceType",
    "ExistenceGate",
    "FileEvidence",
    "ForbiddenPathRule",
    "GateResult",
    "ImpactAnalyzer",
    "ImpactResult",
    "LLMEvidence",
    "PackageManifestDiff",
    "ProgressDelta",
    "ProgressTracker",
    "RiskLevel",
    "SectionDiff",
    "SoftCheck",
    "SoftCheckResult",
    "ToolEvidence",
    "VerificationEvidence",
    "WriteGate",
    "WriteGateResult",
    "analyze_impact",
    "assess_patch_risk",
    "check_missing_targets",
    "check_mode",
    "compute_progress_delta",
    "create_evidence_collector",
    "detect_stall",
    "detect_unresolved_imports",
    "diff_package_manifest",
    "parse_agents_write_policy",
    "validate_director_write_policy",
    "validate_write_scope",
]
