"""Director task execution strategy derivation.

This module turns ``TaskExecutionProfileV1`` into runtime controls for prompt
budget, output tokens, sampling, ContextOS intent, and final-request audit. It
owns no LLM calls and performs no file I/O.
"""

from __future__ import annotations

from typing import Any, Mapping

from polaris.cells.director.tasking.internal.execution_contract import build_task_execution_contract
from polaris.cells.director.tasking.public.contracts import (
    TaskExecutionProfileV1,
    TaskExecutionStrategyV1,
)

_DEFAULT_OUTPUT_BUDGETS: dict[str, int] = {
    "bugfix": 64_000,
    "code_review": 48_000,
    "config": 48_000,
    "database": 96_000,
    "devops": 48_000,
    "docs": 48_000,
    "integration": 96_000,
    "observability": 64_000,
    "refactor": 96_000,
    "security": 64_000,
    "tests": 64_000,
    "validation": 64_000,
    "write_code": 128_000,
}

_DEFAULT_INPUT_BUDGETS: dict[str, int] = {
    "bugfix": 64_000,
    "code_review": 48_000,
    "config": 40_000,
    "database": 72_000,
    "devops": 48_000,
    "docs": 40_000,
    "integration": 96_000,
    "observability": 72_000,
    "refactor": 96_000,
    "security": 80_000,
    "tests": 72_000,
    "validation": 64_000,
    "write_code": 128_000,
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _is_factory_bench(metadata: Mapping[str, Any]) -> bool:
    return (
        any(
            str(metadata.get(key) or "").strip()
            for key in (
                "factory_bench_session_id",
                "factory_bench_project_id",
                "factory_bench_project_workspace",
            )
        )
        or metadata.get("factory_bench_level") is not None
    )


def _complexity_bonus(profile: TaskExecutionProfileV1, metadata: Mapping[str, Any]) -> int:
    score = 0
    target_count = len(profile.target_files)
    scope_count = len(profile.scope_paths)
    if target_count >= 2:
        score += 1
    if target_count >= 5:
        score += 1
    if scope_count >= 3:
        score += 1
    if profile.task_type in {"integration", "refactor", "write_code"}:
        score += 1
    if profile.project_type in {"frontend", "service", "api", "database"}:
        score += 1
    if _mapping(metadata.get("previous_verification_result")) or _mapping(metadata.get("task_context")):
        score += 1
    if _is_factory_bench(metadata):
        score += 2
    return score


def _scale_budget(base: int, bonus: int, *, hard_cap: int, maximize_at_bonus: int | None = None) -> int:
    if maximize_at_bonus is not None and bonus >= maximize_at_bonus:
        return hard_cap
    multiplier = 1.0 + min(max(bonus, 0), 5) * 0.25
    return min(max(int(base * multiplier), base), hard_cap)


def _evidence_requirements(profile: TaskExecutionProfileV1) -> tuple[str, ...]:
    requirements = ["pm_task_contract", "chief_engineer_blueprint", "target_files_or_declared_scopes"]
    if profile.task_type in {"bugfix", "tests", "validation"} or "repair" in profile.phase:
        requirements.append("failed_gate_or_verification_evidence")
    if profile.task_type in {"write_code", "integration", "refactor"} or len(profile.target_files) >= 3:
        requirements.append("architecture_or_file_plan")
    if profile.language != "generic":
        requirements.append("language_best_practices")
    if profile.framework:
        requirements.append("framework_best_practices")
    return tuple(dict.fromkeys(requirements))


def _context_budget_policy(profile: TaskExecutionProfileV1, input_budget_tokens: int) -> dict[str, Any]:
    evidence_share = 0.18
    code_share = 0.30
    contract_share = 0.18
    retrieval_share = 0.20
    history_share = 0.06
    output_reserve_share = 0.08
    if profile.task_type in {"bugfix", "tests", "validation"} or "repair" in profile.phase:
        evidence_share = 0.28
        code_share = 0.26
        contract_share = 0.16
        retrieval_share = 0.18
        history_share = 0.04
        output_reserve_share = 0.08
    elif profile.task_type in {"write_code", "integration", "refactor"}:
        evidence_share = 0.14
        code_share = 0.34
        contract_share = 0.20
        retrieval_share = 0.20
        history_share = 0.04
        output_reserve_share = 0.08
    return {
        "schema_version": "task.execution_context_budget_policy.v1",
        "input_budget_tokens": input_budget_tokens,
        "contract_share": contract_share,
        "code_share": code_share,
        "evidence_share": evidence_share,
        "retrieval_share": retrieval_share,
        "history_share": history_share,
        "output_reserve_share": output_reserve_share,
        "policy_source": "task.execution_strategy.v1",
    }


def _cognitive_strategy_override(strategy: TaskExecutionStrategyV1) -> dict[str, Any]:
    return {
        "cognitive_runtime": {
            "applied": True,
            "source": strategy.schema_version,
            "input_budget_tokens": strategy.input_budget_tokens,
            "output_budget_tokens": strategy.output_budget_tokens,
        },
        "exploration": {
            "max_expansion_depth": 4,
            "neighbor_expansion_aggressive": True,
        },
        "read_escalation": {
            "full_read_allowed": True,
        },
        "compaction": {
            "trigger_at_budget_pct": min(0.95, max(0.75, 1.0 - strategy.min_context_utilization)),
        },
        "task_execution": {
            "schema_version": strategy.schema_version,
            "prompt_max_chars": strategy.prompt_max_chars,
            "context_underutilized_policy": strategy.context_underutilized_policy,
            "evidence_requirements": list(strategy.evidence_requirements),
        },
    }


def resolve_director_execution_strategy(
    profile: TaskExecutionProfileV1,
    *,
    metadata: Mapping[str, Any] | None = None,
    model_window_tokens: int | None = None,
) -> TaskExecutionStrategyV1:
    """Resolve runtime budget and audit strategy from a Director task profile."""

    normalized_metadata = _mapping(metadata)
    bonus = _complexity_bonus(profile, normalized_metadata)
    task_type = profile.task_type
    output_base = _DEFAULT_OUTPUT_BUDGETS.get(task_type, 16_000)
    input_base = _DEFAULT_INPUT_BUDGETS.get(task_type, 64_000)
    if "repair" in profile.phase:
        output_base = max(output_base, 64_000)
        input_base = max(input_base, 96_000)
    if _is_factory_bench(normalized_metadata):
        output_base = max(output_base, 128_000)
        input_base = max(input_base, 160_000)

    output_budget = _scale_budget(output_base, bonus, hard_cap=128_000, maximize_at_bonus=3)
    input_budget = _scale_budget(input_base, bonus, hard_cap=512_000)
    model_window = _int_value(model_window_tokens, 0)
    if model_window > 0:
        input_budget = min(input_budget, max(8_000, int(model_window * 0.55)))
    prompt_max_chars = max(40_000, min(input_budget * 4, 1_500_000))

    policy = "block_if_missing_evidence" if task_type in {"bugfix", "tests", "validation"} else "warn"
    min_utilization = 0.02 if model_window >= 500_000 else 0.08
    if task_type in {"write_code", "integration", "refactor"}:
        min_utilization = max(min_utilization, 0.05)

    return TaskExecutionStrategyV1(
        profile_schema_version=profile.schema_version,
        profile_hash_source=profile.schema_version,
        temperature=profile.temperature,
        temperature_phase=profile.temperature_phase,
        sampling_mode=profile.sampling_mode,
        output_budget_tokens=output_budget,
        input_budget_tokens=input_budget,
        prompt_max_chars=prompt_max_chars,
        min_context_utilization=min_utilization,
        context_underutilized_policy=policy,
        evidence_requirements=_evidence_requirements(profile),
        context_budget_policy=_context_budget_policy(profile, input_budget),
        target_files=profile.target_files,
        scope_paths=profile.scope_paths,
        signal_evidence={
            "complexity_bonus": bonus,
            "task_type": profile.task_type,
            "phase": profile.phase,
            "project_type": profile.project_type,
            "model_window_tokens": model_window,
        },
    )


def apply_execution_strategy_overrides(
    *,
    context: dict[str, Any],
    metadata: dict[str, Any],
    profile: TaskExecutionProfileV1,
    strategy: TaskExecutionStrategyV1,
) -> None:
    """Write strategy controls into trusted runtime context and metadata."""

    profile_payload = profile.to_dict()
    strategy_payload = strategy.to_dict()
    execution_contract_payload = build_task_execution_contract(
        profile,
        strategy,
        metadata=metadata,
    ).to_dict()
    context["director_execution_profile"] = profile_payload
    context.setdefault("task_execution_profile", profile_payload)
    context["director_execution_strategy"] = strategy_payload
    context["task_execution_strategy"] = strategy_payload
    context["task_execution_contract"] = execution_contract_payload
    context["director_execution_contract"] = execution_contract_payload
    context["_transaction_kernel_temperature_override"] = strategy.temperature
    context["llm_max_tokens"] = strategy.output_budget_tokens
    context["max_output_tokens"] = strategy.output_budget_tokens
    context["task_execution_prompt_max_chars"] = strategy.prompt_max_chars
    context["task_execution_context_budget_policy"] = dict(strategy.context_budget_policy)
    context["task_execution_min_context_utilization"] = strategy.min_context_utilization
    context["cognitive_strategy_override"] = _cognitive_strategy_override(strategy)

    metadata["director_execution_profile"] = profile_payload
    metadata.setdefault("task_execution_profile", profile_payload)
    metadata["director_execution_strategy"] = strategy_payload
    metadata["task_execution_strategy"] = strategy_payload
    metadata["task_execution_contract"] = execution_contract_payload
    metadata["director_execution_contract"] = execution_contract_payload
    metadata["temperature"] = strategy.temperature
    metadata["temperature_phase"] = strategy.temperature_phase
    metadata["temperature_source"] = strategy.source
    metadata["llm_max_tokens"] = strategy.output_budget_tokens
    metadata["max_output_tokens"] = strategy.output_budget_tokens
    metadata["task_execution_strategy_source"] = strategy.source
    metadata["cognitive_strategy_override"] = _cognitive_strategy_override(strategy)
