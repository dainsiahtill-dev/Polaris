"""Cognitive Governance - Verification Card bindings for cognitive pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool-category classification (mirrors _TOOL_SPECS "category" field semantics)
# ---------------------------------------------------------------------------
_INTENT_CATEGORY_MAP: dict[str, str] = {
    # read-category intents
    "read_file": "read",
    "explain": "read",
    "search": "read",
    "repo_tree": "read",
    "repo_read_head": "read",
    "repo_read_slice": "read",
    "repo_rg": "read",
    # write-category intents
    "create_file": "write",
    "modify_file": "write",
    "delete_file": "write",
    # exec-category intents
    "execute": "exec",
    "run_command": "exec",
    "test": "exec",
}

_VALID_CATEGORIES = frozenset({"read", "write", "exec"})

# Risk-level ordered sets for category validation
_LOW_RISK_CATEGORIES = frozenset({"read"})
_MEDIUM_RISK_CATEGORIES = frozenset({"write"})
_HIGH_RISK_CATEGORIES = frozenset({"exec"})


@dataclass(frozen=True)
class VCResult:
    """Result of a verification card check."""

    vc_id: str
    status: str  # PASS | WARN | FAIL | SKIP
    message: str
    details: dict[str, object] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.details is None:
            object.__setattr__(self, "details", {})


class CognitiveGovernance:
    """
    Governance checks for cognitive pipeline.

    Implements VC (Verification Card) checks at each phase
    to ensure cognitive processing meets quality gates.
    """

    # Confidence thresholds from schema
    MIN_CONFidence_FOR_HIGH_RISK = 0.7
    MIN_CONFidence_FOR_MEDIUM_RISK = 0.5
    MIN_CONFidence_FOR_LOW_RISK = 0.3

    # VC-ID mappings
    VC_INTENT_001 = "VC-Intent-001"  # Intent clarity check
    VC_CT_001 = "VC-CT-001"  # Confidence threshold check
    VC_EVOLUTION_001 = "VC-Evolution-001"  # Learning validation
    VC_VALUE_001 = "VC-Value-001"  # Value alignment check
    VC_CAUTIOUS_001 = "VC-Cautious-001"  # Cautious execution check
    VC_META_001 = "VC-Meta-001"  # Meta-cognition check
    VC_CONSISTENCY_001 = "VC-Consistency-001"  # Reasoning consistency

    async def verify_pre_perception(self, message: str) -> VCResult:
        """
        Pre-perception checks.

        VC-Intent-001: Verify message is non-empty and parseable.
        """
        if not message or not message.strip():
            return VCResult(
                vc_id=self.VC_INTENT_001,
                status="FAIL",
                message="Empty message received",
            )

        if len(message) > 10000:
            return VCResult(
                vc_id=self.VC_INTENT_001,
                status="FAIL",
                message="Message too long (>10000 chars)",
            )

        return VCResult(
            vc_id=self.VC_INTENT_001,
            status="PASS",
            message="Message is parseable",
            details={"message_length": len(message)},
        )

    async def verify_post_perception(
        self,
        intent_type: str,
        confidence: float,
        *,
        consecutive_unknown_count: int = 0,
        confidence_trajectory: tuple[float, ...] = (),
    ) -> VCResult:
        """
        Post-perception checks.

        VC-Intent-001: Intent clarity check with enhanced rules:

        1. Intent type vs STANDARD_TOOLS category validation (read/write/exec).
        2. Confidence trajectory decline detection (>= 3 monotonically declining
           samples triggers WARN).
        3. Consecutive unknown intent detection: >= 2 consecutive unknowns
           downgraded to FAIL.
        """
        # --- Rule: unknown intent ---
        if intent_type == "unknown":
            # Consecutive unknown escalation
            if consecutive_unknown_count >= 1:
                return VCResult(
                    vc_id=self.VC_INTENT_001,
                    status="FAIL",
                    message=(f"Consecutive unknown intents ({consecutive_unknown_count + 1}); downgraded to FAIL"),
                    details={
                        "confidence": confidence,
                        "consecutive_unknown_count": consecutive_unknown_count + 1,
                    },
                )
            return VCResult(
                vc_id=self.VC_INTENT_001,
                status="FAIL",
                message="Could not determine intent type",
                details={"confidence": confidence},
            )

        # --- Rule: intent-category validation ---
        category = _INTENT_CATEGORY_MAP.get(intent_type)
        if category is None:
            # Intent not in known tool map -- not necessarily invalid (could be a
            # higher-level cognitive intent), but worth a WARN.
            logger.debug(
                "Intent %s not in _INTENT_CATEGORY_MAP; skipping category check",
                intent_type,
            )
        elif category not in _VALID_CATEGORIES:
            return VCResult(
                vc_id=self.VC_INTENT_001,
                status="WARN",
                message=f"Intent maps to unrecognized category '{category}'",
                details={"intent_type": intent_type, "category": category},
            )

        # --- Rule: confidence trajectory decline ---
        if len(confidence_trajectory) >= 3:
            recent = list(confidence_trajectory[-3:])
            declining = all(recent[i] > recent[i + 1] for i in range(len(recent) - 1))
            if declining:
                return VCResult(
                    vc_id=self.VC_INTENT_001,
                    status="WARN",
                    message="Confidence trajectory is monotonically declining",
                    details={
                        "confidence": confidence,
                        "trajectory_tail": recent,
                    },
                )

        # --- Rule: low confidence ---
        if confidence < 0.3:
            return VCResult(
                vc_id=self.VC_INTENT_001,
                status="WARN",
                message="Low confidence in intent detection",
                details={"confidence": confidence},
            )

        return VCResult(
            vc_id=self.VC_INTENT_001,
            status="PASS",
            message="Intent clarity acceptable",
            details={"intent_type": intent_type, "confidence": confidence},
        )

    async def verify_pre_reasoning(
        self,
        intent_type: str,
        confidence: float,
    ) -> VCResult:
        """
        Pre-reasoning checks.

        VC-CT-001: Confidence threshold check before reasoning.
        """
        # L0/L1 can proceed with lower confidence
        low_risk_types = {"read_file", "explain", "search"}
        medium_risk_types = {"create_file", "modify_file", "test", "plan"}

        if intent_type in low_risk_types:
            threshold = self.MIN_CONFidence_FOR_LOW_RISK
        elif intent_type in medium_risk_types:
            threshold = self.MIN_CONFidence_FOR_MEDIUM_RISK
        else:
            threshold = self.MIN_CONFidence_FOR_HIGH_RISK

        if confidence < threshold:
            return VCResult(
                vc_id=self.VC_CT_001,
                status="WARN",
                message=f"Confidence {confidence:.2f} below threshold {threshold:.2f}",
                details={"threshold": threshold, "confidence": confidence},
            )

        return VCResult(
            vc_id=self.VC_CT_001,
            status="PASS",
            message="Confidence threshold met",
            details={"threshold": threshold, "confidence": confidence},
        )

    async def verify_post_reasoning(
        self,
        probability: float,
        severity: str,
        blockers: tuple[str, ...],
        *,
        actions: tuple[str, ...] = (),
        accumulated_warn_count: int = 0,
    ) -> VCResult:
        """
        Post-reasoning checks.

        VC-CT-001: Reasoning quality check.
        VC-Cautious-001: High-severity check.

        Enhanced rules:
        1. Probability vs risk-level mismatch: high risk + probability < 0.7 -> WARN.
        2. Blocker-action conflict: if a blocker text contains an action name -> WARN.
        3. Accumulated risk: combined warn_count from GovernanceState informs decision.
        """
        issues: list[str] = []

        # --- Rule: probability vs risk-level mismatch ---
        if probability < self.MIN_CONFidence_FOR_HIGH_RISK and severity in (
            "high",
            "critical",
        ):
            issues.append(f"Low probability {probability:.2f} with {severity} severity")

        # --- Rule: critical blockers ---
        critical_blockers = [b for b in blockers if "critical" in b.lower()]
        if critical_blockers:
            return VCResult(
                vc_id=self.VC_CAUTIOUS_001,
                status="FAIL",
                message="Critical blockers prevent execution",
                details={"blockers": list(blockers)},
            )

        # --- Rule: blocker-action conflict ---
        for blocker in blockers:
            for action in actions:
                if action.lower() in blocker.lower() and action:
                    issues.append(f"Blocker '{blocker}' references action '{action}'")
                    break  # one match per blocker is enough

        # --- Rule: accumulated risk escalation ---
        if accumulated_warn_count >= 3:
            return VCResult(
                vc_id=self.VC_CT_001,
                status="FAIL",
                message=(f"Accumulated warn count ({accumulated_warn_count}) exceeds threshold; escalating to FAIL"),
                details={
                    "probability": probability,
                    "severity": severity,
                    "accumulated_warn_count": accumulated_warn_count,
                },
            )

        if issues:
            return VCResult(
                vc_id=self.VC_CT_001,
                status="WARN",
                message="; ".join(issues),
                details={"probability": probability, "severity": severity},
            )

        return VCResult(
            vc_id=self.VC_CT_001,
            status="PASS",
            message="Reasoning quality acceptable",
            details={"probability": probability, "severity": severity},
        )

    async def verify_reasoning_consistency(
        self,
        reasoning_chain_conclusion: str,
        intent_type: str,
        assumptions: tuple[str, ...],
    ) -> VCResult:
        """Check reasoning conclusion consistency with perceived intent.

        VC-Consistency-001: Reasoning-intent consistency gate.

        Rules:
        1. Conclusion must not be empty (-> FAIL).
        2. Conclusion should not contradict the primary intent via simple keyword
           conflict detection (-> WARN).
        3. If more than 5 assumptions but the conclusion is shorter than 80 chars,
           reasoning may be underspecified (-> WARN).
        """
        # Rule 1: empty conclusion
        if not reasoning_chain_conclusion or not reasoning_chain_conclusion.strip():
            return VCResult(
                vc_id=self.VC_CONSISTENCY_001,
                status="FAIL",
                message="Reasoning chain conclusion is empty",
                details={"intent_type": intent_type},
            )

        # Rule 2: keyword contradiction between conclusion and intent
        # Negation prefixes that flip meaning
        _contradiction_pairs: list[tuple[str, str]] = [
            ("do not", "create_file"),
            ("do not", "modify_file"),
            ("do not", "delete_file"),
            ("skip", "test"),
            ("avoid", "execute"),
            ("refrain", "run_command"),
        ]
        conclusion_lower = reasoning_chain_conclusion.lower()
        for negation_phrase, conflict_intent in _contradiction_pairs:
            if negation_phrase in conclusion_lower and intent_type == conflict_intent:
                return VCResult(
                    vc_id=self.VC_CONSISTENCY_001,
                    status="WARN",
                    message=(
                        f"Conclusion may contradict intent: '{negation_phrase}' in conclusion vs intent '{intent_type}'"
                    ),
                    details={
                        "intent_type": intent_type,
                        "negation": negation_phrase,
                    },
                )

        # Rule 3: assumptions overload with short conclusion
        if len(assumptions) > 5 and len(reasoning_chain_conclusion) < 80:
            return VCResult(
                vc_id=self.VC_CONSISTENCY_001,
                status="WARN",
                message=(
                    f"Too many assumptions ({len(assumptions)}) for a short conclusion; reasoning may be underspecified"
                ),
                details={
                    "assumption_count": len(assumptions),
                    "conclusion_length": len(reasoning_chain_conclusion),
                },
            )

        return VCResult(
            vc_id=self.VC_CONSISTENCY_001,
            status="PASS",
            message="Reasoning consistency verified",
            details={
                "intent_type": intent_type,
                "assumption_count": len(assumptions),
                "conclusion_length": len(reasoning_chain_conclusion),
            },
        )

    async def verify_pre_execution(
        self,
        execution_path: str,
        requires_confirmation: bool,
    ) -> VCResult:
        """
        Pre-execution checks.

        VC-Cautious-001: Verify cautious execution principles.
        """
        if execution_path == "full_pipe" and not requires_confirmation:
            return VCResult(
                vc_id=self.VC_CAUTIOUS_001,
                status="FAIL",
                message="L3+ action without user confirmation",
            )

        return VCResult(
            vc_id=self.VC_CAUTIOUS_001,
            status="PASS",
            message="Cautious execution verified",
            details={"path": execution_path, "requires_confirmation": requires_confirmation},
        )

    async def verify_post_execution(
        self,
        success: bool,
        verification_needed: bool,
    ) -> VCResult:
        """
        Post-execution checks.

        VC-Evolution-001: Verify learning was recorded.
        """
        if verification_needed and not success:
            return VCResult(
                vc_id=self.VC_EVOLUTION_001,
                status="WARN",
                message="Action failed but evolution not triggered",
            )

        return VCResult(
            vc_id=self.VC_EVOLUTION_001,
            status="PASS",
            message="Evolution recorded appropriately",
        )

    async def verify_meta_cognition(
        self,
        knowledge_boundary_confidence: float,
        output_confidence: float,
    ) -> VCResult:
        """
        Meta-cognition verification.

        VC-Meta-001: Verify meta-cognition snapshot is valid.
        """
        # Output confidence should generally be <= knowledge boundary
        if output_confidence > knowledge_boundary_confidence + 0.2:
            return VCResult(
                vc_id=self.VC_META_001,
                status="WARN",
                message="Output confidence exceeds knowledge boundary",
                details={
                    "knowledge_boundary": knowledge_boundary_confidence,
                    "output_confidence": output_confidence,
                },
            )

        return VCResult(
            vc_id=self.VC_META_001,
            status="PASS",
            message="Meta-cognition valid",
            details={
                "knowledge_boundary": knowledge_boundary_confidence,
                "output_confidence": output_confidence,
            },
        )

    async def verify_value_alignment(
        self,
        action_type: str,
        risk_level: str,
        intent_type: str,
    ) -> VCResult:
        """
        Value alignment verification.

        VC-Value-001: Verify action aligns with core values.

        Checks:
        - Action doesn't violate system integrity
        - User long-term benefit considered
        - No harmful patterns (quick_fix, ignore_warning, skip_test, etc.)
        """
        # High-risk actions always need verification
        if risk_level in ("high", "critical"):
            # Check for common harmful patterns in action intent
            harmful_patterns = {
                "delete_backup": "Deleting backups threatens system integrity",
                "skip_test": "Skipping tests increases defect risk",
                "ignore_warning": "Ignoring warnings may cause future issues",
                "quick_fix": "Quick fixes may introduce technical debt",
            }
            for pattern, reason in harmful_patterns.items():
                if pattern in intent_type.lower():
                    return VCResult(
                        vc_id=self.VC_VALUE_001,
                        status="WARN",
                        message=f"Potential value alignment issue: {reason}",
                        details={"pattern": pattern, "risk_level": risk_level},
                    )

        # Default: value alignment verified
        return VCResult(
            vc_id=self.VC_VALUE_001,
            status="PASS",
            message="Value alignment verified",
            details={"action_type": action_type, "risk_level": risk_level},
        )
