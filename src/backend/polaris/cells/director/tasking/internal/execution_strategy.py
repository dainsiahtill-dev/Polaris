"""Director task execution strategy derivation.

This module turns ``TaskExecutionProfileV1`` into runtime controls for prompt
budget, output tokens, sampling, ContextOS intent, and final-request audit. It
owns no LLM calls and performs no file I/O.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any, Mapping

from polaris.cells.director.tasking.internal.execution_contract import build_task_execution_contract
from polaris.cells.director.tasking.internal.execution_envelope import (
    build_execution_envelope,
    collect_admitted_execution_envelopes,
)
from polaris.cells.director.tasking.public.contracts import (
    TaskExecutionProfileV1,
    TaskExecutionStrategyV1,
)
from polaris.kernelone.llm.budget_policy import (
    BUDGET_CONTEXT_KEYS_CANONICAL,
    OUTPUT_BUDGET_CONTEXT_KEYS,
    STRATEGY_NESTED_BUDGET_KEYS,
    STRATEGY_NESTED_CONTAINER_KEYS,
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

_SOURCE_FILE_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".dart",
        ".ex",
        ".exs",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".mjs",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".scala",
        ".swift",
        ".ts",
        ".tsx",
    }
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _string_value(payload: Mapping[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return default


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
    return score


def _scale_budget(base: int, bonus: int, *, hard_cap: int, maximize_at_bonus: int | None = None) -> int:
    if maximize_at_bonus is not None and bonus >= maximize_at_bonus:
        return hard_cap
    multiplier = 1.0 + min(max(bonus, 0), 5) * 0.25
    return min(max(int(base * multiplier), base), hard_cap)


def _requires_module_interface_contract(profile: TaskExecutionProfileV1) -> bool:
    """Require CE interface evidence when Director must coordinate multiple source files."""

    source_targets = [
        path for path in profile.target_files if "." + str(path).rsplit(".", 1)[-1].lower() in _SOURCE_FILE_SUFFIXES
    ]
    if len(source_targets) < 2:
        return False
    if profile.task_type in {"config", "docs"}:
        return False
    return profile.task_type in {"write_code", "integration", "refactor", "bugfix", "tests", "validation"} or bool(
        profile.language and profile.language != "generic"
    )


def _has_declared_dependencies(metadata: Mapping[str, Any]) -> bool:
    containers = (
        metadata,
        _mapping(metadata.get("task")),
        _mapping(metadata.get("task_context")),
        _mapping(metadata.get("metadata")),
    )
    for container in containers:
        for key in (
            "depends_on",
            "depends_on_external",
            "depends_on_task_ids",
            "resolved_depends_on_task_ids",
            "dependency_task_ids",
            "blocked_by",
        ):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return True
            if isinstance(value, (list, tuple, set, frozenset, dict)) and bool(value):
                return True
    return False


def _evidence_requirements(
    profile: TaskExecutionProfileV1,
    metadata: Mapping[str, Any],
) -> tuple[str, ...]:
    requirements = ["pm_task_contract", "chief_engineer_blueprint", "target_files_or_declared_scopes"]
    if profile.task_type == "bugfix" or "repair" in profile.phase:
        requirements.append("failed_gate_evidence")
    if _has_declared_dependencies(metadata):
        requirements.append("actual_sibling_exports")
    if profile.task_type in {"write_code", "integration", "refactor"} or len(profile.target_files) >= 3:
        requirements.append("architecture_or_file_plan")
    if _requires_module_interface_contract(profile):
        requirements.append("module_interface_contract")
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
        evidence_requirements=_evidence_requirements(profile, normalized_metadata),
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


def _call_output_budget_ceilings(context: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, int]:
    """Read existing call facts as ceilings, never as a new allocation source."""
    ceilings: dict[str, int] = {}

    def collect(payload: Mapping[str, Any], prefix: str, keys: tuple[str, ...]) -> None:
        for key in keys:
            value = payload.get(key)
            if value is None or isinstance(value, bool):
                continue
            try:
                limit = int(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if limit > 0:
                ceilings[f"{prefix}.{key}"] = limit

    for prefix, payload in (
        ("context", context),
        ("context.metadata", _mapping(context.get("metadata"))),
        ("metadata", metadata),
    ):
        collect(payload, prefix, OUTPUT_BUDGET_CONTEXT_KEYS)
        for key in (
            *BUDGET_CONTEXT_KEYS_CANONICAL,
            "execution_envelope",
            "task_execution_envelope",
            "director_execution_envelope",
        ):
            typed = _mapping(payload.get(key))
            collect(typed, f"{prefix}.{key}", STRATEGY_NESTED_BUDGET_KEYS)
            for container_key in (*STRATEGY_NESTED_CONTAINER_KEYS, "model_policy"):
                collect(
                    _mapping(typed.get(container_key)),
                    f"{prefix}.{key}.{container_key}",
                    STRATEGY_NESTED_BUDGET_KEYS,
                )
    return ceilings


def _task_strategy_observation_origin(strategy: TaskExecutionStrategyV1) -> dict[str, Any]:
    origin = strategy.to_dict()
    seen: set[int] = set()
    # Flatten historical projections too. This is provenance only: neither the
    # original task budget nor any nested prior-call budget can enlarge a call.
    for _ in range(64):
        prior = _mapping(_mapping(origin.get("signal_evidence")).get("call_output_budget_derivation"))
        if not prior:
            return deepcopy(origin)
        candidate = prior.get("task_strategy")
        if not isinstance(candidate, Mapping) or id(candidate) in seen:
            raise ValueError("director_call_budget_provenance_invalid")
        seen.add(id(candidate))
        origin = dict(candidate)
    raise ValueError("director_call_budget_provenance_invalid")


def _call_execution_strategy(
    strategy: TaskExecutionStrategyV1, *, context: Mapping[str, Any], metadata: Mapping[str, Any]
) -> TaskExecutionStrategyV1:
    ceilings = _call_output_budget_ceilings(context, metadata)
    output_tokens = (
        min(strategy.output_budget_tokens, *ceilings.values()) if ceilings else strategy.output_budget_tokens
    )
    if (
        output_tokens == strategy.output_budget_tokens
        and "call_output_budget_derivation" not in strategy.signal_evidence
    ):
        return strategy
    # The supplied task allocation is immutable. Only this call's derived
    # strategy is projected; the original stays observation-only provenance.
    return replace(
        strategy,
        output_budget_tokens=output_tokens,
        signal_evidence={
            **{
                key: deepcopy(value)
                for key, value in strategy.signal_evidence.items()
                if key != "call_output_budget_derivation"
            },
            "call_output_budget_derivation": {
                "schema_version": "director.call_output_budget_derivation.v1",
                "source": "admitted_call_ceilings",
                "task_strategy": _task_strategy_observation_origin(strategy),
                "effective_output_budget_tokens": output_tokens,
                "ceiling_sources": ceilings,
                "observation_only": True,
            },
        },
    )


def apply_execution_strategy_overrides(
    *,
    context: dict[str, Any],
    metadata: dict[str, Any],
    profile: TaskExecutionProfileV1,
    strategy: TaskExecutionStrategyV1,
) -> None:
    """Project strategy defaults without enlarging an admitted call ceiling."""

    strategy = _call_execution_strategy(strategy, context=context, metadata=metadata)
    output_tokens = strategy.output_budget_tokens

    profile_payload = profile.to_dict()
    strategy_payload = strategy.to_dict()
    execution_contract = build_task_execution_contract(
        profile,
        strategy,
        metadata=metadata,
    )
    execution_contract_payload = execution_contract.to_dict()
    merged_evidence = {**context, **metadata}
    admitted_envelopes = collect_admitted_execution_envelopes(context, _mapping(context.get("metadata")), metadata)
    execution_envelope = build_execution_envelope(
        workspace=_string_value(
            merged_evidence, "workspace", "factory_bench_project_workspace", default="unknown-workspace"
        ),
        task_id=_string_value(merged_evidence, "task_id", "pm_task_id", "backlog_ref", default="unknown-task"),
        run_id=_string_value(merged_evidence, "run_id", "factory_run_id", default="unknown-run"),
        trace_id=_string_value(merged_evidence, "trace_id", "call_id", default="unknown-trace"),
        profile=profile,
        strategy=strategy,
        contract=execution_contract,
        metadata=merged_evidence,
        admitted_envelopes=admitted_envelopes,
    )
    execution_envelope_payload = execution_envelope.to_dict()
    context["director_execution_profile"] = profile_payload
    context.setdefault("task_execution_profile", profile_payload)
    context["director_execution_strategy"] = strategy_payload
    context["task_execution_strategy"] = strategy_payload
    context["task_execution_contract"] = execution_contract_payload
    context["director_execution_contract"] = execution_contract_payload
    context["task_execution_envelope"] = execution_envelope_payload
    context["director_execution_envelope"] = execution_envelope_payload
    context["execution_envelope_hash"] = execution_envelope.envelope_hash
    context["_transaction_kernel_temperature_override"] = strategy.temperature
    context["llm_max_tokens"] = output_tokens
    context["max_output_tokens"] = output_tokens
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
    metadata["task_execution_envelope"] = execution_envelope_payload
    metadata["director_execution_envelope"] = execution_envelope_payload
    metadata["execution_envelope_hash"] = execution_envelope.envelope_hash
    metadata["temperature"] = strategy.temperature
    metadata["temperature_phase"] = strategy.temperature_phase
    metadata["temperature_source"] = strategy.source
    metadata["llm_max_tokens"] = output_tokens
    metadata["max_output_tokens"] = output_tokens
    metadata["task_execution_strategy_source"] = strategy.source
    metadata["cognitive_strategy_override"] = _cognitive_strategy_override(strategy)
    for payload in (context, metadata):
        # An existing provider alias must not keep advertising the task default
        # after the scalar and all typed current-call facts have been narrowed.
        if "max_tokens" in payload:
            payload["max_tokens"] = output_tokens
        if "execution_strategy" in payload:
            payload["execution_strategy"] = strategy_payload
        if "execution_envelope" in payload:
            payload["execution_envelope"] = execution_envelope_payload
        forced_budget = payload.get("director_forced_write_output_budget")
        if isinstance(forced_budget, Mapping):
            payload["director_forced_write_output_budget"] = {**forced_budget, "max_tokens": output_tokens}
    # Keep any inherited current-call projections coherent too, without
    # mutating the caller's nested metadata or introducing another fact source.
    nested_metadata = _mapping(context.get("metadata"))
    for key in (
        *OUTPUT_BUDGET_CONTEXT_KEYS,
        *BUDGET_CONTEXT_KEYS_CANONICAL,
        "execution_envelope",
        "task_execution_envelope",
        "director_execution_envelope",
        "execution_envelope_hash",
        "cognitive_strategy_override",
        "director_forced_write_output_budget",
    ):
        if key in nested_metadata:
            if key == "execution_envelope":
                nested_metadata[key] = execution_envelope_payload
            elif key in metadata:
                nested_metadata[key] = metadata[key]
    if isinstance(context.get("metadata"), Mapping):
        context["metadata"] = nested_metadata
