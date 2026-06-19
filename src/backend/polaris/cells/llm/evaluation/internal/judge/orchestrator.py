"""The single production entry point for deterministic agentic judging.

``judge_agentic_case`` is the only public production caller surface of the
deterministic judge (used by ``llm.evaluation.public.service.judge_agentic_case``
and the internal ``agentic_benchmark`` runner). It runs the deterministic check
builders and the registered validators against an observed run and aggregates a
weighted verdict.
"""

from __future__ import annotations

from collections import defaultdict

from ..benchmark_loader import list_workspace_files
from ..benchmark_models import (
    AgenticBenchmarkCase,
    AgenticJudgeVerdict,
    JudgeCheck,
    ObservedBenchmarkRun,
)
from .constants import (
    SCORE_WEIGHTS,
    _category_score,
    _check_output_substrings,
    _check_required_tools,
    _check_textual_tool_protocol,
    _check_tool_arguments,
    _failed_check_summary,
)
from .registry import VALIDATOR_REGISTRY
from .validators import VALIDATORS


def judge_agentic_case(
    case: AgenticBenchmarkCase,
    observed: ObservedBenchmarkRun,
    *,
    workspace_files: list[str] | None = None,
) -> AgenticJudgeVerdict:
    """Judge an agentic benchmark case against observed execution.

    Args:
        case: The benchmark case to judge.
        observed: The observed execution run to evaluate.
        workspace_files: Optional list of known workspace files for path validation.

    Returns:
        AgenticJudgeVerdict containing the judgment results.
    """
    known_paths = list(workspace_files or list_workspace_files(observed.workspace))
    combined_output = (str(observed.output or "") + "\n" + str(observed.thinking or "")).strip()
    checks: list[JudgeCheck] = []
    checks.extend(_check_required_tools(case, observed))
    checks.extend(_check_tool_arguments(case, observed))
    checks.extend(_check_output_substrings(case, observed))
    checks.extend(_check_textual_tool_protocol(observed))

    for validator_name in case.judge.validators:
        # Try new registry first, fall back to legacy VALIDATORS dict
        registry_result = VALIDATOR_REGISTRY.get(validator_name)
        if registry_result is not None:
            metadata, validator_func = registry_result
            payload = combined_output if validator_name == "no_prompt_leakage" else str(observed.output or "")
            ok, message = validator_func(payload, observed, known_paths)
            checks.append(
                JudgeCheck(
                    code=f"validator:{validator_name}",
                    category=metadata.category.value,
                    passed=bool(ok),
                    message=str(message or validator_name),
                    critical=metadata.critical,
                )
            )
            continue

        # Fallback to legacy VALIDATORS dict for backward compatibility
        spec = VALIDATORS.get(validator_name)
        if spec is None:
            checks.append(
                JudgeCheck(
                    code=f"validator:{validator_name}",
                    category="contract",
                    passed=False,
                    message=f"unknown validator `{validator_name}`",
                    critical=True,
                )
            )
            continue
        category, critical, validator = spec
        payload = combined_output if validator_name == "no_prompt_leakage" else str(observed.output or "")
        ok, message = validator(payload, observed, known_paths)
        checks.append(
            JudgeCheck(
                code=f"validator:{validator_name}",
                category=category,
                passed=bool(ok),
                message=str(message or validator_name),
                critical=critical,
            )
        )

    grouped: dict[str, list[JudgeCheck]] = defaultdict(list)
    for item in checks:
        grouped[item.category].append(item)

    category_scores = {category: _category_score(grouped.get(category, [])) for category in SCORE_WEIGHTS}
    overall_score = sum(category_scores[name] * weight for name, weight in SCORE_WEIGHTS.items())
    critical_failures = [item for item in checks if item.critical and not item.passed]
    passed = not critical_failures and overall_score >= case.judge.score_threshold

    return AgenticJudgeVerdict(
        case_id=case.case_id,
        passed=passed,
        score=overall_score,
        threshold=case.judge.score_threshold,
        categories=category_scores,
        summary=_failed_check_summary(checks),
        checks=tuple(checks),
    )
