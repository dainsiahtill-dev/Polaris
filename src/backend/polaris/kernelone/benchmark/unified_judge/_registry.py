"""Hand-maintained validator registries for the unified judge.

This module owns the two registries that map validator names to their
implementations:

- ``BUILTIN_VALIDATORS``: name -> ``ValidatorPort`` instance.
- ``VALIDATOR_SPECS``: name -> ``(category, critical, callable)`` tuples.

The CONTENT of these registries is byte-identical to the former monolith; only
the import paths of the validator classes changed. The registration mechanism
(hand-maintained dicts) is deliberately preserved unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._base import (
    ValidatorPort,
    _looks_like_structured_steps,
    _validate_pm_plan_json,
    _validate_qa_passfail,
)
from ._director import (
    DirectorFeatureBranchValidator,
    DirectorRefactorPlanValidator,
    DirectorSafeScopeValidator,
    DirectorSecurityFixValidator,
)
from ._prompt_leakage import (
    NoHallucinatedPathsValidator,
    NoPromptLeakageValidator,
    StructuredStepsValidator,
)
from ._safety import SafetyCheckValidator
from ._scout import (
    ScoutCodebaseMapValidator,
    ScoutDependencyReportValidator,
    ScoutDetectiveRootCauseValidator,
    ScoutDocFactsValidator,
    ScoutEvidencePathsValidator,
    ScoutMinReconValidator,
    ScoutReadOnlyContractValidator,
    ScoutSubagentUsedValidator,
)
from ._structured_output import (
    ChineseOutputRequiredValidator,
    FactAnchoringCheckValidator,
    FirstCallRejectUnknownArgsValidator,
    FocusRecoveryCheckValidator,
    HallucinationRefusalCheckValidator,
    OrderedToolSequenceValidator,
    ParityCompareModeSetValidator,
    RequireNoErrorValidator,
    RequireNoToolCallsValidator,
    SelfVerificationCheckValidator,
    StepwisePlanningValidator,
    StructuredOutputRequiredValidator,
)

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "BUILTIN_VALIDATORS",
    "VALIDATOR_SPECS",
]


BUILTIN_VALIDATORS: dict[str, ValidatorPort] = {
    "no_prompt_leakage": NoPromptLeakageValidator(),
    "structured_steps": StructuredStepsValidator(),
    "no_hallucinated_paths": NoHallucinatedPathsValidator(),
    # Director validators (migrated from deterministic_judge.py)
    "director_safe_scope": DirectorSafeScopeValidator(),
    "director_refactor_plan": DirectorRefactorPlanValidator(),
    "director_security_fix": DirectorSecurityFixValidator(),
    "director_feature_branch": DirectorFeatureBranchValidator(),
    # Output content validators (migrated from deterministic_judge.py)
    "require_no_error": RequireNoErrorValidator(),
    "first_call_reject_unknown_args": FirstCallRejectUnknownArgsValidator(),
    "require_no_tool_calls": RequireNoToolCallsValidator(),
    "parity_compare_mode_set": ParityCompareModeSetValidator(),
    "focus_recovery_check": FocusRecoveryCheckValidator(),
    "fact_anchoring_check": FactAnchoringCheckValidator(),
    "stepwise_planning": StepwisePlanningValidator(),
    "hallucination_refusal_check": HallucinationRefusalCheckValidator(),
    "ordered_tool_sequence": OrderedToolSequenceValidator(),
    "self_verification_check": SelfVerificationCheckValidator(),
    "structured_output_required": StructuredOutputRequiredValidator(),
    "chinese_output_required": ChineseOutputRequiredValidator(),
    "safety_check": SafetyCheckValidator(),
    # Scout (探子) read-only reconnaissance validators
    "scout_readonly_contract": ScoutReadOnlyContractValidator(),
    "scout_evidence_paths": ScoutEvidencePathsValidator(),
    "scout_codebase_map": ScoutCodebaseMapValidator(),
    "scout_dependency_report": ScoutDependencyReportValidator(),
    "scout_doc_facts": ScoutDocFactsValidator(),
    "scout_detective_root_cause": ScoutDetectiveRootCauseValidator(),
    "scout_subagent_used": ScoutSubagentUsedValidator(),
    "scout_min_recon": ScoutMinReconValidator(),
}

VALIDATOR_SPECS: dict[str, tuple[str, bool, Callable[[str], tuple[bool, str]]]] = {
    "pm_plan_json": ("contract", False, _validate_pm_plan_json),
    "qa_passfail_json": ("contract", False, _validate_qa_passfail),
    "structured_steps": ("contract", False, lambda t: (_looks_like_structured_steps(t), "structured steps validation")),
}
