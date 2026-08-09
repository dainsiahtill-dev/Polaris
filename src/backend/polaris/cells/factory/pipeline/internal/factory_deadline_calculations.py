"""Pure Factory deadline/budget calculation helpers.

Extracted from ``OrchestrationStageExecutor`` as part of the incremental
god-class decomposition. Every function here is pure (no ``self``) and
operates on a ``context: dict[str, Any]`` carrying infrastructure
configuration and the ``factory_run_deadline_epoch_seconds`` deadline.

The God-class retains thin one-line delegate wrappers so that existing
callers and the characterization-test suite continue to resolve the same
methods on ``OrchestrationStageExecutor``.
"""

from __future__ import annotations

import contextlib
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from polaris.kernelone.constants import MAX_LLM_PROVIDER_TIMEOUT_SECONDS
from polaris.kernelone.llm.budget_policy import (
    FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS,
    chief_engineer_generation_floor_seconds_for_output_tokens,
    chief_engineer_portfolio_generation_floor_seconds,
)

from .factory_deadline_policy import (
    FactoryDeadlineAdmissionV1,
    FactoryDeadlineBudgetPolicyV1,
    TaskDependencyScheduleV1,
    resolve_chief_engineer_portfolio_admission,
    resolve_director_dispatch_admission,
)

# ── Module-level tuning constants ────────────────────────────────────────
# Moved here from factory_stage_executor.py. The original module retains
# its own copies for now (used by _validate_pm_plan_language_consistency and
# other non-extracted methods); a future cleanup pass should unify them.

_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_ENV = "KERNELONE_FACTORY_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_COUNT"
_DEFAULT_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_COUNT = 4
_DIRECTOR_DISPATCH_TIMEOUT_GRACE_SECONDS = 60
_DIRECTOR_DISPATCH_DEADLINE_SAFETY_SECONDS = 5
_DIRECTOR_SETTLEMENT_BARRIER_BUDGET_SECONDS = 5
_DIRECTOR_FIRST_MATERIALIZATION_MIN_BUDGET_ENV = "KERNELONE_FACTORY_DIRECTOR_FIRST_MATERIALIZATION_MIN_BUDGET_SECONDS"
_DIRECTOR_FIRST_MATERIALIZATION_MIN_BUDGET_SECONDS = 90.0
_QUALITY_GATE_RESERVED_BUDGET_ENV = "KERNELONE_FACTORY_QUALITY_GATE_RESERVED_BUDGET_SECONDS"
_QUALITY_GATE_RESERVED_BUDGET_SECONDS = 120.0
_QUALITY_GATE_MIN_START_BUDGET_SECONDS = 15.0
_QUALITY_GATE_MIN_QA_START_BUDGET_SECONDS = FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS
_CHIEF_ENGINEER_MIN_LLM_START_BUDGET_SECONDS = FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS
_DIRECTOR_TIMEOUT_ENV_KEYS = (
    "KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS",
    "KERNELONE_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS",
    "KERNELONE_DIRECTOR_LLM_TIMEOUT_MAX_SECONDS",
)
_DEFAULT_CHIEF_ENGINEER_LLM_TIMEOUT_SECONDS = 600
_CHIEF_ENGINEER_EXECUTION_ATTEMPT_SETTLEMENT_GRACE_SECONDS = 30
_CHIEF_ENGINEER_LLM_TIMEOUT_ENV_KEYS = (
    "KERNELONE_FACTORY_CHIEF_ENGINEER_LLM_TIMEOUT_SECONDS",
    "KERNELONE_FACTORY_CE_LLM_TIMEOUT_SECONDS",
    "KERNELONE_CHIEF_ENGINEER_LLM_TIMEOUT_SECONDS",
)


@dataclass(frozen=True, slots=True)
class ChiefEngineerExecutionAttemptLeaseBudget:
    """One bounded lease policy derived from the admitted CE timeout."""

    lease_ttl_seconds: int
    heartbeat_interval_seconds: float

    def __post_init__(self) -> None:
        if self.lease_ttl_seconds <= 0:
            raise ValueError("chief_engineer_execution_attempt_lease_ttl_must_be_positive")
        if not 0 < self.heartbeat_interval_seconds < self.lease_ttl_seconds:
            raise ValueError("chief_engineer_execution_attempt_heartbeat_interval_out_of_bounds")


# ── Deadline remaining ───────────────────────────────────────────────────


def factory_deadline_remaining_seconds(context: dict[str, Any]) -> float | None:
    """Return seconds until the factory deadline, or ``None`` if unset."""

    raw_deadline = context.get("factory_run_deadline_epoch_seconds")
    if raw_deadline is None:
        return None
    try:
        deadline_epoch = float(str(raw_deadline).strip())
    except (TypeError, ValueError):
        return None
    if deadline_epoch <= 0:
        return None
    return max(0.0, deadline_epoch - datetime.now(timezone.utc).timestamp())


# ── Director dispatch timeout / budget ───────────────────────────────────


def director_dispatch_timeout_seconds(
    context: dict[str, Any],
    *,
    task_count: int,
    materialization_pending: bool = False,
) -> int:
    remaining_task_count = max(1, int(task_count))
    resolved_timeout: int | None = None
    raw_override = context.get("director_dispatch_timeout_seconds")
    if raw_override is not None:
        with contextlib.suppress(TypeError, ValueError):
            resolved_timeout = max(1, int(raw_override))

    def _parse_timeout(raw: Any) -> int | None:
        if raw is None:
            return None
        try:
            value = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        return value

    stage_timeout = _parse_timeout(context.get("timeout"))
    if resolved_timeout is None:
        llm_timeout_candidates: list[int] = []
        for key in ("director_llm_timeout_seconds", "llm_call_timeout_seconds"):
            value = _parse_timeout(context.get(key))
            if value is not None:
                llm_timeout_candidates.append(value)
        for env_key in _DIRECTOR_TIMEOUT_ENV_KEYS:
            value = _parse_timeout(os.getenv(env_key))
            if value is not None:
                llm_timeout_candidates.append(value)
        if llm_timeout_candidates:
            resolved_timeout = max(llm_timeout_candidates) + _DIRECTOR_DISPATCH_TIMEOUT_GRACE_SECONDS

    if resolved_timeout is None:
        resolved_timeout = stage_timeout or 600

    remaining_seconds = factory_deadline_remaining_seconds(context)
    if remaining_seconds is not None:
        quality_gate_reserve = director_downstream_reserved_budget_seconds(
            context,
            materialization_pending=materialization_pending,
            remaining_task_count=remaining_task_count,
        )
        safety_budget = (
            quality_gate_reserve
            if remaining_seconds > quality_gate_reserve
            else _DIRECTOR_DISPATCH_DEADLINE_SAFETY_SECONDS
        )
        deadline_timeout = int(max(1.0, remaining_seconds - safety_budget))
        return max(1, min(resolved_timeout, deadline_timeout))

    return resolved_timeout


def factory_deadline_budget_policy(
    context: dict[str, Any],
    *,
    chief_engineer_generation_floor_seconds: float = 0.0,
    director_first_task_min_seconds: float | None = None,
    quality_gate_reserved_seconds: float | None = None,
    director_settlement_barrier_seconds: int | None = None,
) -> FactoryDeadlineBudgetPolicyV1:
    """Resolve infrastructure configuration into the pure deadline policy.

    Optional resolved inputs preserve the executor's test/override seam while
    keeping policy construction in this pure helper. The extraction previously
    bypassed monkeypatched executor resolvers and silently reverted budgets to
    module defaults.
    """

    resolved_director_first = (
        director_first_materialization_min_budget_seconds(context)
        if director_first_task_min_seconds is None
        else director_first_task_min_seconds
    )
    resolved_quality_reserve = (
        quality_gate_reserved_budget_seconds(context)
        if quality_gate_reserved_seconds is None
        else quality_gate_reserved_seconds
    )
    resolved_settlement_barrier = (
        director_dispatch_timeout_settle_grace_seconds(context)
        if director_settlement_barrier_seconds is None
        else director_settlement_barrier_seconds
    )

    return FactoryDeadlineBudgetPolicyV1(
        chief_engineer_min_start_seconds=math.ceil(_CHIEF_ENGINEER_MIN_LLM_START_BUDGET_SECONDS),
        director_first_task_min_seconds=math.ceil(resolved_director_first),
        director_followup_task_min_seconds=math.ceil(FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS),
        quality_gate_reserved_seconds=math.ceil(resolved_quality_reserve),
        quality_gate_min_start_reserved_seconds=math.ceil(
            _QUALITY_GATE_MIN_START_BUDGET_SECONDS + _QUALITY_GATE_MIN_QA_START_BUDGET_SECONDS,
        ),
        safety_seconds=int(_DIRECTOR_DISPATCH_DEADLINE_SAFETY_SECONDS),
        director_settlement_barrier_seconds=min(
            _DIRECTOR_SETTLEMENT_BARRIER_BUDGET_SECONDS,
            resolved_settlement_barrier,
        ),
        chief_engineer_generation_floor_seconds=math.ceil(chief_engineer_generation_floor_seconds),
    )


def director_first_materialization_min_budget_seconds(context: dict[str, Any]) -> float:
    raw_value = context.get("director_first_materialization_min_budget_seconds")
    if raw_value is None:
        raw_value = context.get("factory_director_first_materialization_min_budget_seconds")
    if raw_value is None:
        raw_value = os.getenv(_DIRECTOR_FIRST_MATERIALIZATION_MIN_BUDGET_ENV)
    try:
        value = (
            float(str(raw_value).strip())
            if raw_value is not None
            else _DIRECTOR_FIRST_MATERIALIZATION_MIN_BUDGET_SECONDS
        )
    except (TypeError, ValueError):
        value = _DIRECTOR_FIRST_MATERIALIZATION_MIN_BUDGET_SECONDS
    return max(30.0, min(value, 600.0))


def quality_gate_reserved_budget_seconds(context: dict[str, Any]) -> float:
    raw_value = context.get("quality_gate_reserved_budget_seconds")
    if raw_value is None:
        raw_value = context.get("factory_quality_gate_reserved_budget_seconds")
    if raw_value is None:
        raw_value = os.getenv(_QUALITY_GATE_RESERVED_BUDGET_ENV)
    try:
        value = float(str(raw_value).strip()) if raw_value is not None else _QUALITY_GATE_RESERVED_BUDGET_SECONDS
    except (TypeError, ValueError):
        value = _QUALITY_GATE_RESERVED_BUDGET_SECONDS
    minimum = _QUALITY_GATE_MIN_START_BUDGET_SECONDS + _QUALITY_GATE_MIN_QA_START_BUDGET_SECONDS
    return max(minimum, min(value, 600.0))


def director_downstream_reserved_budget_seconds(
    context: dict[str, Any],
    *,
    materialization_pending: bool,
    remaining_task_count: int,
) -> float:
    """Reserve only executable downstream work at the Director boundary.

    Project quality and QA cannot run while more than one declared owner
    task still has to materialize its targets. In that state the scheduler
    retains the minimum budget needed to start both downstream stages,
    rather than the configured full quality allowance. The final owner (or
    an already materialized workspace) keeps the full reserve.

    Complexity:
        O(1) time and memory.
    """

    configured_reserve = quality_gate_reserved_budget_seconds(context)
    minimum_reserve = _QUALITY_GATE_MIN_START_BUDGET_SECONDS + _QUALITY_GATE_MIN_QA_START_BUDGET_SECONDS
    if materialization_pending and max(1, int(remaining_task_count)) > 1:
        return min(configured_reserve, minimum_reserve)
    return configured_reserve


def director_dispatch_timeout_settle_grace_seconds(context: dict[str, Any]) -> int:
    raw_value = context.get("director_dispatch_timeout_settle_grace_seconds")
    if raw_value is None:
        raw_value = os.getenv("KERNELONE_DIRECTOR_DISPATCH_TIMEOUT_SETTLE_GRACE_SECONDS")
    try:
        value = int(float(str(raw_value).strip()) if raw_value is not None else 45)
    except (TypeError, ValueError):
        value = 45
    return max(0, min(value, 120))


def director_dispatch_deadline_admission_decision(
    context: dict[str, Any],
    *,
    requested_timeout_seconds: int,
    first_materialization_pending: bool,
    materialization_pending: bool,
    dependency_schedule: TaskDependencyScheduleV1,
) -> FactoryDeadlineAdmissionV1:
    """Return the canonical typed admission for one Director dispatch."""

    return resolve_director_dispatch_admission(
        remaining_seconds=factory_deadline_remaining_seconds(context),
        requested_timeout_seconds=requested_timeout_seconds,
        dependency_schedule=dependency_schedule,
        first_materialization_pending=first_materialization_pending,
        materialization_pending=materialization_pending,
        policy=factory_deadline_budget_policy(context),
    )


# ── Chief Engineer timeout / lease / admission ──────────────────────────


def chief_engineer_llm_timeout_seconds(context: dict[str, Any]) -> int:
    def _parse_timeout(raw: Any) -> int | None:
        if raw is None:
            return None
        try:
            parsed = Decimal(str(raw).strip())
        except (InvalidOperation, TypeError, ValueError):
            return None
        if not parsed.is_finite() or parsed <= 0:
            return None
        if parsed >= MAX_LLM_PROVIDER_TIMEOUT_SECONDS:
            return MAX_LLM_PROVIDER_TIMEOUT_SECONDS
        value = int(parsed)
        return value if value > 0 else None

    for key in (
        "chief_engineer_llm_timeout_seconds",
        "ce_llm_timeout_seconds",
        "llm_call_timeout_seconds",
        "request_timeout_seconds",
        "timeout_seconds",
    ):
        value = _parse_timeout(context.get(key))
        if value is not None:
            return value

    for env_key in _CHIEF_ENGINEER_LLM_TIMEOUT_ENV_KEYS:
        value = _parse_timeout(os.getenv(env_key))
        if value is not None:
            return value

    return _DEFAULT_CHIEF_ENGINEER_LLM_TIMEOUT_SECONDS


def chief_engineer_execution_attempt_lease_budget(
    execution_timeout_seconds: int,
) -> ChiefEngineerExecutionAttemptLeaseBudget:
    """Derive one bounded TaskRuntime TTL and heartbeat cadence."""

    if (
        isinstance(execution_timeout_seconds, bool)
        or not isinstance(execution_timeout_seconds, int)
        or execution_timeout_seconds <= 0
        or execution_timeout_seconds > MAX_LLM_PROVIDER_TIMEOUT_SECONDS
    ):
        raise ValueError("chief_engineer_execution_timeout_seconds_out_of_bounds")
    lease_ttl_seconds = execution_timeout_seconds + _CHIEF_ENGINEER_EXECUTION_ATTEMPT_SETTLEMENT_GRACE_SECONDS
    heartbeat_interval_seconds = min(
        float(_CHIEF_ENGINEER_EXECUTION_ATTEMPT_SETTLEMENT_GRACE_SECONDS),
        lease_ttl_seconds / 3.0,
    )
    return ChiefEngineerExecutionAttemptLeaseBudget(
        lease_ttl_seconds=lease_ttl_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )


def chief_engineer_deadline_projection_decision(
    context: dict[str, Any],
    *,
    requested_timeout_seconds: int,
    dependency_schedule: TaskDependencyScheduleV1,
    output_tokens: int | None = None,
) -> FactoryDeadlineAdmissionV1:
    """Return admission for one project-level Chief Engineer LLM call.

    ``output_tokens`` overrides the modeled generation floor for bounded
    sub-calls (e.g. the output-schema repair requests far fewer tokens than
    a full portfolio); ``None`` models the full portfolio floor.
    """

    if output_tokens is None:
        portfolio_task_count = max(1, len(dependency_schedule.active_task_ids))
        generation_floor_seconds = chief_engineer_portfolio_generation_floor_seconds(portfolio_task_count)
    else:
        generation_floor_seconds = chief_engineer_generation_floor_seconds_for_output_tokens(output_tokens)
    return resolve_chief_engineer_portfolio_admission(
        remaining_seconds=factory_deadline_remaining_seconds(context),
        requested_timeout_seconds=requested_timeout_seconds,
        dependency_schedule=dependency_schedule,
        policy=factory_deadline_budget_policy(
            context,
            chief_engineer_generation_floor_seconds=generation_floor_seconds,
        ),
    )


# ── CE projection semantic enrichment ────────────────────────────────────


def chief_engineer_projection_semantic_terms(task_context: dict[str, Any]) -> list[str]:
    def _mapping(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    def _extend_terms(raw: Any, terms: list[str]) -> None:
        values = raw if isinstance(raw, (list, tuple, set)) else [raw]
        for item in values:
            token = str(item or "").strip()
            if token and token not in terms:
                terms.append(token)

    terms: list[str] = []
    depth_contract = _mapping(task_context.get("delivery_depth_contract"))
    plan_document = _mapping(task_context.get("delivery_plan_document"))
    product_intent = _mapping(depth_contract.get("product_intent"))
    product_summary = _mapping(plan_document.get("product_summary"))
    _extend_terms(product_intent.get("primary_entities"), terms)
    _extend_terms(product_summary.get("core_terms"), terms)
    _extend_terms(task_context.get("feature_keywords"), terms)
    return terms[:8]


def enrich_chief_engineer_projection_context(task_context: dict[str, Any]) -> None:
    terms = chief_engineer_projection_semantic_terms(task_context)
    if not terms:
        return

    semantic_phrase = ", ".join(terms[:4])

    def _string_list(raw: Any) -> list[str]:
        if isinstance(raw, (list, tuple)):
            return [str(item).strip() for item in raw if str(item or "").strip()]
        token = str(raw or "").strip()
        return [token] if token else []

    def _append_unique(key: str, value: str) -> None:
        rows = _string_list(task_context.get(key))
        if value not in rows:
            rows.append(value)
        task_context[key] = rows

    _append_unique(
        "acceptance_criteria",
        f"Preserve and verify domain behavior for {semantic_phrase}.",
    )
    _append_unique(
        "execution_checklist",
        f"Carry the PM domain terms through the implementation plan: {semantic_phrase}.",
    )
    task_context["chief_engineer_projection_semantic_terms"] = terms


# ── Misc ─────────────────────────────────────────────────────────────────


def director_binding_timeout_quarantine_count() -> int:
    raw = os.environ.get(_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_ENV, "")
    try:
        value = int(str(raw).strip()) if str(raw).strip() else _DEFAULT_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_COUNT
    except (TypeError, ValueError):
        value = _DEFAULT_DIRECTOR_BINDING_TIMEOUT_QUARANTINE_COUNT
    return max(2, value)


def director_binding_identity(provider_id: str, model: str, binding_id: str = "") -> str:
    return f"{str(provider_id or '').strip()}|{str(model or '').strip()}|{str(binding_id or '').strip()}"
