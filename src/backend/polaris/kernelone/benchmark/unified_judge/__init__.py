"""Unified Benchmark Framework - Deterministic Judge Engine.

This package is the lossless successor of the former ``unified_judge`` module.
It re-exports every previously-public symbol from the same import path
(``polaris.kernelone.benchmark.unified_judge``) so that
``import ...unified_judge`` and ``from ...unified_judge import X`` keep
resolving identically for all external importers.

Design Patterns
---------------
- Strategy Pattern: Validators are pluggable strategies
- Observer Pattern: Check results are observable
- Chain of Responsibility: Checks are executed in chain

Example
-------
    from polaris.kernelone.benchmark import UnifiedJudge, UnifiedBenchmarkCase

    judge = UnifiedJudge()
    verdict = judge.judge(case, observed)
    if verdict.passed:
        print("Benchmark PASSED")
"""

from __future__ import annotations

from polaris.kernelone.tools.tool_kinds import is_write_tool_name

from ._base import (
    _DEFAULT_JSON_MAX_DEPTH,
    _SCOUT_READ_FILE_TOOLS,
    _SCOUT_RECON_TOOLS,
    _SCOUT_RELATIONAL_MARKERS,
    PROMPT_LEAKAGE_MARKERS,
    UnifiedJudge,
    ValidatorPort,
    _count_json_depth,
    _ExcessiveNestingError,
    _extract_json_dict,
    _looks_like_structured_steps,
    _safe_json_loads,
    _scout_has_recon_tool_call,
    _scout_localizes_anchor,
    _validate_director_safe_scope_domain,
    _validate_pm_plan_json,
    _validate_qa_passfail,
    aggregate_overall_score,
)
from ._director import (
    DirectorFeatureBranchValidator,
    DirectorRefactorPlanValidator,
    DirectorSafeScopeValidator,
    DirectorSecurityFixValidator,
)
from ._prompt_leakage import (
    DistractionCheckValidator,
    GoalPersistenceValidator,
    NoHallucinatedPathsValidator,
    NoPromptLeakageValidator,
    StructuredStepsValidator,
    TDDNoRegressionValidator,
)
from ._registry import BUILTIN_VALIDATORS, VALIDATOR_SPECS
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

__all__ = [
    # Registries
    "BUILTIN_VALIDATORS",
    "PROMPT_LEAKAGE_MARKERS",
    "VALIDATOR_SPECS",
    # JSON helpers
    "_DEFAULT_JSON_MAX_DEPTH",
    "_SCOUT_READ_FILE_TOOLS",
    # Scout shared helpers
    "_SCOUT_RECON_TOOLS",
    "_SCOUT_RELATIONAL_MARKERS",
    "ChineseOutputRequiredValidator",
    # Domain validators
    "DirectorFeatureBranchValidator",
    "DirectorRefactorPlanValidator",
    "DirectorSafeScopeValidator",
    "DirectorSecurityFixValidator",
    "DistractionCheckValidator",
    "FactAnchoringCheckValidator",
    "FirstCallRejectUnknownArgsValidator",
    "FocusRecoveryCheckValidator",
    "GoalPersistenceValidator",
    "HallucinationRefusalCheckValidator",
    "NoHallucinatedPathsValidator",
    "NoPromptLeakageValidator",
    "OrderedToolSequenceValidator",
    "ParityCompareModeSetValidator",
    "RequireNoErrorValidator",
    "RequireNoToolCallsValidator",
    "SafetyCheckValidator",
    "ScoutCodebaseMapValidator",
    "ScoutDependencyReportValidator",
    "ScoutDetectiveRootCauseValidator",
    "ScoutDocFactsValidator",
    "ScoutEvidencePathsValidator",
    "ScoutMinReconValidator",
    "ScoutReadOnlyContractValidator",
    "ScoutSubagentUsedValidator",
    "SelfVerificationCheckValidator",
    "StepwisePlanningValidator",
    "StructuredOutputRequiredValidator",
    "StructuredStepsValidator",
    "TDDNoRegressionValidator",
    # Base infrastructure
    "UnifiedJudge",
    "ValidatorPort",
    "_ExcessiveNestingError",
    "_count_json_depth",
    "_extract_json_dict",
    "_looks_like_structured_steps",
    "_safe_json_loads",
    "_scout_has_recon_tool_call",
    "_scout_localizes_anchor",
    # Re-exported domain helper (used by director validators)
    "_validate_director_safe_scope_domain",
    "_validate_pm_plan_json",
    "_validate_qa_passfail",
    "aggregate_overall_score",
    "is_write_tool_name",
]


def _wire_cross_module_namespace() -> None:
    """Inject sibling symbols into each submodule globals for free-name lookup.

    Functions and methods defined in the validator submodules resolve free
    names via their own module ``__dict__``. In the former monolith every
    helper was a module-global; after the split those helpers live in sibling
    modules. After the package re-exports every symbol, copy non-owned names
    into each submodule so cross-module calls remain lossless without
    rewriting call sites. Ownership is each submodule's ``__all__``.
    """
    import sys

    pkg = sys.modules[__name__]
    shared = {key: value for key, value in pkg.__dict__.items() if not key.startswith("__")}
    for mod in (
        sys.modules.get("polaris.kernelone.benchmark.unified_judge._base"),
        sys.modules.get("polaris.kernelone.benchmark.unified_judge._prompt_leakage"),
        sys.modules.get("polaris.kernelone.benchmark.unified_judge._director"),
        sys.modules.get("polaris.kernelone.benchmark.unified_judge._structured_output"),
        sys.modules.get("polaris.kernelone.benchmark.unified_judge._safety"),
        sys.modules.get("polaris.kernelone.benchmark.unified_judge._scout"),
        sys.modules.get("polaris.kernelone.benchmark.unified_judge._registry"),
    ):
        if mod is None:
            continue
        owned = set(getattr(mod, "__all__", ()) or ())
        for key, value in shared.items():
            if key not in owned:
                mod.__dict__[key] = value


_wire_cross_module_namespace()
