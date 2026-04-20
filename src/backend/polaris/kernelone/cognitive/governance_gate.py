"""Cognitive Governance Gate - Centralized governance checks for cognitive pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from polaris.kernelone.cognitive.governance import CognitiveGovernance
from polaris.kernelone.cognitive.governance.law_invariants import CognitiveLawGuard

if TYPE_CHECKING:
    from polaris.kernelone.cognitive.perception.models import UncertaintyAssessment


@dataclass(frozen=True)
class GovernanceCheckResult:
    """Result of a governance gate check."""

    blocked: bool
    content: str | None
    block_reason: str | None
    vc_id: str | None
    metadata: dict[str, object]


class CognitiveGovernanceGate:
    """
    Centralized governance gate for all cognitive pipeline checks.

    Consolidates:
    - CognitiveGovernance VC checks at each phase
    - CognitiveLawGuard L1/L2/L3 runtime enforcement
    """

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self._governance = CognitiveGovernance() if enabled else None
        self._law_guard = CognitiveLawGuard()

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    async def check_pre_perception(self, message: str) -> GovernanceCheckResult:
        """Check message before perception."""
        if not self._enabled or self._governance is None:
            return GovernanceCheckResult(blocked=False, content=None, block_reason=None, vc_id=None, metadata={})

        result = await self._governance.verify_pre_perception(message)
        if result.status == "FAIL":
            return GovernanceCheckResult(
                blocked=True,
                content=f"Governance blocked: {result.message}",
                block_reason=f"Governance: {result.vc_id}",
                vc_id=result.vc_id,
                metadata={"governance_vc": result.vc_id},
            )
        return GovernanceCheckResult(blocked=False, content=None, block_reason=None, vc_id=None, metadata={})

    async def check_post_perception(
        self,
        intent_type: str,
        confidence: float,
    ) -> GovernanceCheckResult:
        """Check after perception."""
        if not self._enabled or self._governance is None:
            return GovernanceCheckResult(blocked=False, content=None, block_reason=None, vc_id=None, metadata={})

        result = await self._governance.verify_post_perception(intent_type, confidence)
        if result.status == "FAIL":
            return GovernanceCheckResult(
                blocked=True,
                content=f"Governance blocked: {result.message}",
                block_reason=f"Governance: {result.vc_id}",
                vc_id=result.vc_id,
                metadata={"governance_vc": result.vc_id},
            )
        return GovernanceCheckResult(blocked=False, content=None, block_reason=None, vc_id=None, metadata={})

    async def check_pre_reasoning(
        self,
        intent_type: str,
        confidence: float,
    ) -> GovernanceCheckResult:
        """Check before reasoning."""
        if not self._enabled or self._governance is None:
            return GovernanceCheckResult(blocked=False, content=None, block_reason=None, vc_id=None, metadata={})

        result = await self._governance.verify_pre_reasoning(intent_type, confidence)
        if result.status == "FAIL":
            return GovernanceCheckResult(
                blocked=True,
                content=f"Governance blocked: {result.message}",
                block_reason=f"Governance: {result.vc_id}",
                vc_id=result.vc_id,
                metadata={"governance_vc": result.vc_id},
            )
        return GovernanceCheckResult(blocked=False, content=None, block_reason=None, vc_id=None, metadata={})

    async def check_post_reasoning(
        self,
        probability: float,
        severity: str,
        blockers: tuple[str, ...],
    ) -> GovernanceCheckResult:
        """Check after reasoning."""
        if not self._enabled or self._governance is None:
            return GovernanceCheckResult(blocked=False, content=None, block_reason=None, vc_id=None, metadata={})

        result = await self._governance.verify_post_reasoning(
            probability=float(probability),
            severity=severity,
            blockers=blockers,
        )
        if result.status == "FAIL":
            return GovernanceCheckResult(
                blocked=True,
                content=f"Governance blocked: {result.message}",
                block_reason=f"Governance: {result.vc_id}",
                vc_id=result.vc_id,
                metadata={"governance_vc": result.vc_id},
            )
        return GovernanceCheckResult(blocked=False, content=None, block_reason=None, vc_id=None, metadata={})

    async def check_pre_execution(
        self,
        execution_path: str,
        requires_confirmation: bool,
    ) -> GovernanceCheckResult:
        """Check before execution."""
        if not self._enabled or self._governance is None:
            return GovernanceCheckResult(blocked=False, content=None, block_reason=None, vc_id=None, metadata={})

        result = await self._governance.verify_pre_execution(
            execution_path=execution_path,
            requires_confirmation=requires_confirmation,
        )
        if result.status == "FAIL":
            return GovernanceCheckResult(
                blocked=True,
                content=f"Governance blocked: {result.message}",
                block_reason=f"Governance: {result.vc_id}",
                vc_id=result.vc_id,
                metadata={"governance_vc": result.vc_id},
            )
        return GovernanceCheckResult(blocked=False, content=None, block_reason=None, vc_id=None, metadata={})

    def check_l1_truthfulness(
        self,
        uncertainty_score: float,
        confidence: float,
        reasoning_contradicted: bool,
    ) -> None:
        """L1 truthfulness check: Truthfulness > Consistency."""
        self._law_guard.check_l1_truthfulness(
            admitted_uncertainty=uncertainty_score > 0.3,
            confidence=confidence,
            reasoning_contradicted=reasoning_contradicted,
        )

    def check_l2_understanding(
        self,
        intent_type: str,
        uncertainty: UncertaintyAssessment,
        execution_path: str,
    ) -> None:
        """L2 understanding check: Understanding > Execution."""
        self._law_guard.check_l2_understanding(
            intent_type=intent_type,
            uncertainty_score=uncertainty.uncertainty_score,
            execution_path=execution_path,
        )

    def check_l3_evolution(
        self,
        has_error: bool,
        evolution_recorded: bool,
        consecutive_failures: int,
    ) -> None:
        """L3 evolution check: Evolution > Correctness."""
        self._law_guard.check_l3_evolution(
            has_error=has_error,
            evolution_recorded=evolution_recorded,
            consecutive_failures=consecutive_failures,
        )
