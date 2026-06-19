"""Deterministic judge for role agentic benchmark cases.

.. deprecated::
    This module is deprecated. Use ``polaris.kernelone.benchmark.unified_judge``
    for new development. The canonical judge engine is now
    ``polaris/kernelone/benchmark/unified_judge.py`` (UnifiedJudge).

    This module is retained for backward compatibility with existing
    evaluation cell internals and will be removed in a future release.

.. note::
    The implementation has been decomposed into the sibling ``judge`` package
    (``judge/registry.py``, ``judge/json_safety.py``, ``judge/constants.py``,
    ``judge/validators.py``, ``judge/orchestrator.py``). This module is a thin
    re-export shim that preserves the original public import surface and the
    historical import-time side effect: importing it populates the global
    validator registry (``VALIDATOR_REGISTRY``) by importing ``judge.validators``.
"""

from __future__ import annotations

# NOTE: The names below are intentionally re-exported to preserve the original
# public surface of this module (callers and tests import them from here). They
# are listed in ``__all__`` so static analysers treat them as exported rather
# than unused. The stdlib / dataclass / typing imports are re-exported because
# the historical module bound them at top level.
import json  # noqa: F401
import re  # noqa: F401
from collections import defaultdict  # noqa: F401
from collections.abc import Callable  # noqa: F401
from dataclasses import dataclass, field  # noqa: F401
from enum import Enum  # noqa: F401
from typing import Any  # noqa: F401

from polaris.domain.verification.business_validators import (
    validate_director_safe_scope,
    validate_no_hallucinated_paths,
    validate_pm_plan_json,
    validate_qa_passfail,
)
from polaris.kernelone.tool_execution.contracts import canonicalize_tool_name

from .benchmark_loader import list_workspace_files
from .benchmark_models import (
    AgenticBenchmarkCase,
    AgenticJudgeVerdict,
    JudgeCheck,
    ObservedBenchmarkRun,
    ToolArgumentRule,
)

# Re-export the full implementation surface from the ``judge`` package. Importing
# ``judge`` (and, transitively, ``judge.validators``) reproduces the historical
# import-time side effect: every validator is registered into the global
# ``VALIDATOR_REGISTRY`` and the scout validators are bridged in.
from .judge.constants import (
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
from .judge.json_safety import (
    _DEFAULT_JSON_MAX_DEPTH,
    _count_json_depth,
    _ExcessiveNestingError,
    _extract_json_dict,
    _json_loads_with_depth_limit,
    _safe_json_loads,
    _serialize_args,
)
from .judge.orchestrator import judge_agentic_case
from .judge.registry import (
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
from .judge.validators import (
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
from .utils import looks_like_structured_steps

__all__ = [
    # Constants
    "PROMPT_LEAKAGE_MARKERS",
    "SCORE_WEIGHTS",
    "TEXTUAL_TOOL_PROTOCOL_PATTERNS",
    "TOOL_EQUIVALENCE_GROUPS",
    # Legacy validator dict + scout bridge
    "VALIDATORS",
    # Registry framework + global instance
    "VALIDATOR_REGISTRY",
    # JSON safety helpers
    "_DEFAULT_JSON_MAX_DEPTH",
    # Benchmark contract types (re-exported)
    "AgenticBenchmarkCase",
    "AgenticJudgeVerdict",
    "CompositeValidator",
    "JudgeCheck",
    "ObservedBenchmarkRun",
    "ToolArgumentRule",
    "ValidatorCategory",
    "ValidatorFunc",
    "ValidatorMetadata",
    "ValidatorRegistry",
    "ValidatorResult",
    "_ExcessiveNestingError",
    # Deterministic check builders + leakage helpers
    "_category_score",
    "_check_output_substrings",
    "_check_required_tools",
    "_check_textual_tool_protocol",
    "_check_tool_arguments",
    "_contains_prompt_leakage",
    "_count_json_depth",
    "_extract_json_dict",
    "_extract_textual_tool_protocol_markers",
    "_failed_check_summary",
    "_json_loads_with_depth_limit",
    "_register_scout_validators_from_builtin",
    "_rule_matches",
    "_safe_json_loads",
    "_serialize_args",
    # Validator predicate functions
    "_validator_chinese_output_required",
    "_validator_director_feature_branch",
    "_validator_director_refactor_plan",
    "_validator_director_safe_scope",
    "_validator_director_security_fix",
    "_validator_director_test_pass",
    "_validator_fact_anchoring_check",
    "_validator_first_call_reject_unknown_args",
    "_validator_focus_recovery_check",
    "_validator_goal_persistence_check",
    "_validator_hallucination_refusal_check",
    "_validator_no_distraction_tool_calls",
    "_validator_no_hallucinated_paths",
    "_validator_no_prompt_leakage",
    "_validator_ordered_tool_sequence",
    "_validator_parity_compare_mode_set",
    "_validator_pm_plan_json",
    "_validator_qa_passfail_json",
    "_validator_registry_instance",
    "_validator_require_no_error",
    "_validator_require_no_tool_calls",
    "_validator_safety_check",
    "_validator_self_verification_check",
    "_validator_stepwise_planning",
    "_validator_stream_nonstream_parity",
    "_validator_structured_output_required",
    "_validator_structured_steps",
    # Business / utility re-exports
    "canonicalize_tool_name",
    # Orchestrator entry point
    "judge_agentic_case",
    "list_workspace_files",
    "looks_like_structured_steps",
    "validate_director_safe_scope",
    "validate_no_hallucinated_paths",
    "validate_pm_plan_json",
    "validate_qa_passfail",
    # Registered validator wrappers
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
