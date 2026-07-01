"""Deterministic judge implementation package.

This package holds the implementation of the deterministic agentic-benchmark
judge, decomposed from the retired historical single-module
``internal/deterministic_judge.py``.

Module layout:
    - :mod:`json_safety`   — hardened JSON parsing + serialization helpers
    - :mod:`registry`      — validator plugin framework + global registry
    - :mod:`constants`     — judge-local constants + deterministic check builders
    - :mod:`validators`    — the validator predicate family + registration block
    - :mod:`orchestrator`  — ``judge_agentic_case`` (single production entry point)

IMPORTANT — import-time side effect:
    Importing this package imports :mod:`validators`, which executes the
    ``@VALIDATOR_REGISTRY.register(...)`` decorators and the scout-validator
    bridge at import time. This reproduces the historical side effect whereby
    importing the retired ``deterministic_judge`` module populated the global
    validator registry. Importing this canonical package preserves that
    side effect without keeping the old re-export module alive.
"""

from __future__ import annotations

from .constants import (
    PROMPT_LEAKAGE_MARKERS,
    SCORE_WEIGHTS,
    TEXTUAL_TOOL_PROTOCOL_PATTERNS,
    TOOL_EQUIVALENCE_GROUPS,
    _category_score,
    _check_output_substrings,
    _check_required_tools,
    _check_textual_tool_protocol,
    _check_tool_arguments,
    _contains_prompt_leakage,
    _extract_textual_tool_protocol_markers,
    _failed_check_summary,
    _rule_matches,
)
from .json_safety import (
    _DEFAULT_JSON_MAX_DEPTH,
    _count_json_depth,
    _ExcessiveNestingError,
    _extract_json_dict,
    _json_loads_with_depth_limit,
    _safe_json_loads,
    _serialize_args,
)
from .orchestrator import judge_agentic_case
from .registry import (
    VALIDATOR_REGISTRY,
    CompositeValidator,
    ValidatorCategory,
    ValidatorFunc,
    ValidatorMetadata,
    ValidatorRegistry,
    ValidatorResult,
    _validator_registry_instance,
    validator_registry,
)

# Importing ``validators`` fires the registration side effect (registers every
# validator into VALIDATOR_REGISTRY and bridges the scout validators).
from .validators import (
    VALIDATORS,
    _register_scout_validators_from_builtin,
    _validator_chinese_output_required,
    _validator_director_feature_branch,
    _validator_director_refactor_plan,
    _validator_director_safe_scope,
    _validator_director_security_fix,
    _validator_director_test_pass,
    _validator_fact_anchoring_check,
    _validator_first_call_reject_unknown_args,
    _validator_focus_recovery_check,
    _validator_goal_persistence_check,
    _validator_hallucination_refusal_check,
    _validator_no_distraction_tool_calls,
    _validator_no_hallucinated_paths,
    _validator_no_prompt_leakage,
    _validator_ordered_tool_sequence,
    _validator_parity_compare_mode_set,
    _validator_pm_plan_json,
    _validator_qa_passfail_json,
    _validator_require_no_error,
    _validator_require_no_tool_calls,
    _validator_safety_check,
    _validator_self_verification_check,
    _validator_stepwise_planning,
    _validator_stream_nonstream_parity,
    _validator_structured_output_required,
    _validator_structured_steps,
    validator_chinese_output_required,
    validator_director_feature_branch,
    validator_director_refactor_plan,
    validator_director_safe_scope,
    validator_director_security_fix,
    validator_director_test_pass,
    validator_fact_anchoring_check,
    validator_first_call_reject_unknown_args,
    validator_focus_recovery_check,
    validator_goal_persistence_check,
    validator_hallucination_refusal_check,
    validator_no_distraction_tool_calls,
    validator_no_hallucinated_paths,
    validator_no_prompt_leakage,
    validator_ordered_tool_sequence,
    validator_parity_compare_mode_set,
    validator_pm_plan_json,
    validator_qa_passfail_json,
    validator_require_no_error,
    validator_require_no_tool_calls,
    validator_safety_check,
    validator_self_verification_check,
    validator_stepwise_planning,
    validator_stream_nonstream_parity,
    validator_structured_output_required,
    validator_structured_steps,
)

__all__ = [
    "PROMPT_LEAKAGE_MARKERS",
    "SCORE_WEIGHTS",
    "TEXTUAL_TOOL_PROTOCOL_PATTERNS",
    "TOOL_EQUIVALENCE_GROUPS",
    "VALIDATORS",
    "VALIDATOR_REGISTRY",
    "CompositeValidator",
    "ValidatorCategory",
    "ValidatorFunc",
    "ValidatorMetadata",
    "ValidatorRegistry",
    "ValidatorResult",
    "judge_agentic_case",
    "validator_chinese_output_required",
    "validator_director_feature_branch",
    "validator_director_refactor_plan",
    "validator_director_safe_scope",
    "validator_director_security_fix",
    "validator_director_test_pass",
    "validator_fact_anchoring_check",
    "validator_first_call_reject_unknown_args",
    "validator_focus_recovery_check",
    "validator_goal_persistence_check",
    "validator_hallucination_refusal_check",
    "validator_no_distraction_tool_calls",
    "validator_no_hallucinated_paths",
    "validator_no_prompt_leakage",
    "validator_ordered_tool_sequence",
    "validator_parity_compare_mode_set",
    "validator_pm_plan_json",
    "validator_qa_passfail_json",
    "validator_registry",
    "validator_require_no_error",
    "validator_require_no_tool_calls",
    "validator_safety_check",
    "validator_self_verification_check",
    "validator_stepwise_planning",
    "validator_stream_nonstream_parity",
    "validator_structured_output_required",
    "validator_structured_steps",
]
