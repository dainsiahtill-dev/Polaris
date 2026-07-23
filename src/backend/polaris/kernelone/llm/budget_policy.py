"""Execution budget policy — single source for LLM output/timeout budget facts.

Phase 1 of ``docs/blueprints/EXECUTION_BUDGET_POLICY_BLUEPRINT_20260703.md``
(placement amendment: the shared module lives in KernelOne because consumers
span three cells — ``roles.kernel``, ``roles.adapters``, ``factory.pipeline`` —
and cross-cell imports of another cell's internals are forbidden while
KernelOne imports are always legal).

This module owns:

- the previously hand-copied named constants (``7000`` forced-write ceiling,
  ``128_000`` hard output clamp, retry caps, retry timeout defaults);
- the ONE parser for ``KERNELONE_DIRECTOR_FORCED_WRITE_OUTPUT_TOKENS`` (was
  duplicated in ``roles.adapters`` director adapter and ``roles.kernel``
  llm_caller tool_helpers with diverging edge semantics — see
  :func:`forced_write_output_token_ceiling`);
- the shared budget/timeout context-key tuples both reader stacks scan;
- :func:`classify_turn_kind` — the single turn-kind classifier built from the
  EXISTING detection signals (stage labels, retry context markers, forced tool
  surface) previously string-matched independently per call path;
- :class:`ResolvedBudgetV1` — the frozen, observability-only projection of the
  budget a request was ACTUALLY sent with.

Reader key-set divergence (documented, deliberately NOT merged)
===============================================================

Two scanner families read output budgets out of a turn context and their key
sets differ. Phase 1 pins the difference instead of silently unioning it:

- ``BUDGET_CONTEXT_KEYS_CANONICAL`` (canonical scan: ``llm_caller/helpers``
  ``_resolve_context_max_tokens_override`` and its ``transaction_factory``
  delegate) walks payload keys ``task_execution_contract`` and
  ``director_execution_contract`` **which are absent from**
  ``BUDGET_STRATEGY_PAYLOAD_KEYS``.
- ``BUDGET_STRATEGY_PAYLOAD_KEYS`` (strategy-payload scan:
  ``llm_caller/tool_helpers`` first-call forced-write budget) walks nested
  containers ``budget_policy`` and ``llm_sampling`` (see
  ``STRATEGY_NESTED_CONTAINER_KEYS``) **which the canonical scan does not**
  (canonical nests only into ``context_budget`` —
  ``CANONICAL_NESTED_CONTAINER_KEYS``).

Keys present in exactly one list:

- only in ``BUDGET_CONTEXT_KEYS_CANONICAL``: ``task_execution_contract``,
  ``director_execution_contract``;
- only in ``STRATEGY_NESTED_CONTAINER_KEYS`` (strategy scan):
  ``budget_policy``, ``llm_sampling``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

# ---------------------------------------------------------------------------
# Named constants (previously hand-copied per call path)
# ---------------------------------------------------------------------------

#: Hard per-request output-token clamp. Every ``min(x, 128_000)`` hand-copy in
#: llm_caller helpers/tool_helpers and the director adapter converges here.
HARD_OUTPUT_TOKEN_CLAMP: Final[int] = 128_000

#: Shared output budget for Chief Engineer calls that must return a complete
#: structured blueprint. The provider may require extended thinking, so the
#: ordinary 4k role default is not a viable ceiling for this turn class.
DEFAULT_CHIEF_ENGINEER_STRUCTURED_OUTPUT_TOKENS: Final[int] = HARD_OUTPUT_TOKEN_CLAMP

# Factory's project-level CE turn needs materially more room than an ordinary
# 4k role response, but granting the 128k hard ceiling to every tiny portfolio
# makes the physical generation deadline unrelated to project size.  Keep a
# 16k reasoning/output floor and scale by declared PM task count; sufficiently
# large portfolios still reach the shared hard ceiling.
CHIEF_ENGINEER_PORTFOLIO_OUTPUT_TOKEN_FLOOR: Final[int] = 16_384
CHIEF_ENGINEER_PORTFOLIO_OUTPUT_TOKENS_PER_TASK: Final[int] = 4_096

#: Conservative measured provider streaming rate for advanced coding models
#: (kimi-for-coding and peers) under structured-output load. Used only to model
#: the physical wall-clock floor for generating a full CE portfolio; the
#: admission gate fails closed below it rather than EXECUTE a doomed call.
CHIEF_ENGINEER_STREAMING_TOKENS_PER_SECOND_FLOOR: Final[float] = 80.0

#: Existing deployment override retained as the single compatibility key for
#: both portfolio planning and task fission. Central ownership prevents those
#: two Chief Engineer paths from drifting back to different output budgets.
CHIEF_ENGINEER_STRUCTURED_OUTPUT_TOKEN_ENV: Final[str] = "KERNELONE_CE_FISSION_MAX_TOKENS"

#: CAP semantics: a forced-write retry / first-call materialization turn must
#: not inherit the full execution budget; its output is capped at this ceiling
#: (or lower, if the surrounding context already carries a smaller budget).
FORCED_WRITE_OUTPUT_TOKEN_CEILING: Final[int] = 7_000

#: Lower bound applied when parsing/deriving a forced-write output budget.
FORCED_WRITE_OUTPUT_TOKEN_FLOOR: Final[int] = 512

#: Single env override for the forced-write output ceiling; parsed in ONE
#: place (:func:`forced_write_output_token_ceiling`).
FORCED_WRITE_OUTPUT_TOKEN_ENV: Final[str] = "KERNELONE_DIRECTOR_FORCED_WRITE_OUTPUT_TOKENS"

#: CAP semantics: output cap for the required-tool re-ask (provider returned
#: prose despite final-request required tools). Numerically equal to
#: :data:`FORCED_WRITE_OUTPUT_TOKEN_CEILING`; kept as a named alias so the two
#: retry families remain independently tunable facts in Phase 2.
REQUIRED_TOOL_RETRY_OUTPUT_TOKEN_CAP: Final[int] = FORCED_WRITE_OUTPUT_TOKEN_CEILING

#: Timeout cap for the required-tool re-ask. No env var exists for this value
#: today (the forced-write retry timeout has one — see
#: :data:`FORCED_WRITE_RETRY_TIMEOUT_ENV`).
REQUIRED_TOOL_RETRY_TIMEOUT_SECONDS: Final[float] = 120.0

#: FLOOR semantics (opposite direction from the caps above): reserved output
#: budget for the reasoning-truncation re-ask (5th floor, 2026-06-15).
REASONING_TRUNCATION_RETRY_OUTPUT_TOKENS: Final[int] = 8_000

#: FLOOR semantics: a pure-create forced write must emit a COMPLETE file body
#: in one shot, so its retry reserves AT LEAST this many output tokens
#: (``retry_escalation_policy`` create-retry floor). Numerically equal to
#: :data:`FORCED_WRITE_OUTPUT_TOKEN_CEILING` but the participation direction is
#: ``max`` (floor), not ``min`` (cap) — do not collapse the two names.
RETRY_CREATE_OUTPUT_FLOOR_TOKENS: Final[int] = FORCED_WRITE_OUTPUT_TOKEN_CEILING

#: Default timeout cap applied to Director forced-write retry stages.
FORCED_WRITE_RETRY_TIMEOUT_SECONDS: Final[float] = 120.0

#: Existing env override for the forced-write retry stage timeout.
FORCED_WRITE_RETRY_TIMEOUT_ENV: Final[str] = "KERNELONE_DIRECTOR_RETRY_LLM_TIMEOUT_SECONDS"

#: Lower bound for the forced-write retry stage timeout parse.
FORCED_WRITE_RETRY_TIMEOUT_MIN_SECONDS: Final[float] = 10.0

#: Minimum wall-clock budget required to START a factory LLM stage call
#: (chief-engineer task planning AND the workspace-quality-repair loop).
#: Bench r46 evidence: the CE copy was lowered 45.0 → 40.0 in r46, while the
#: workspace-quality-repair sibling silently kept 45.0 — the exact
#: "path N+1 regresses" failure mode this module exists to end. One constant,
#: one place, value 40.0 (blueprint §1 quantified evidence).
FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS: Final[float] = 40.0

#: Default timeout for the Factory Director dispatch stage. This value is
#: injected into the stage context as both stage timeout and LLM-call timeout.
DEFAULT_DIRECTOR_DISPATCH_TIMEOUT_SECONDS: Final[int] = 1_800

#: Env keys that can raise the Factory Director dispatch timeout. Keep the key
#: order in one place so HTTP/router and factory-stage code cannot drift.
DIRECTOR_DISPATCH_TIMEOUT_ENV_KEYS: Final[tuple[str, ...]] = (
    "KERNELONE_FACTORY_DIRECTOR_DISPATCH_TIMEOUT_SECONDS",
    "KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS",
    "KERNELONE_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS",
    "KERNELONE_DIRECTOR_LLM_TIMEOUT_MAX_SECONDS",
)

# ---------------------------------------------------------------------------
# Shared context-key tuples (reader consolidation — Phase 1 step 4)
# ---------------------------------------------------------------------------

#: Top-level output-budget keys every scanner checks first, in priority order.
OUTPUT_BUDGET_CONTEXT_KEYS: Final[tuple[str, ...]] = (
    "llm_max_tokens",
    "max_output_tokens",
    "max_tokens",
)

#: Canonical strategy/contract payload keys (helpers + transaction_factory
#: scan). Superset of :data:`BUDGET_STRATEGY_PAYLOAD_KEYS` — see module
#: docstring for the enumerated divergence.
BUDGET_CONTEXT_KEYS_CANONICAL: Final[tuple[str, ...]] = (
    "task_execution_contract",
    "director_execution_contract",
    "task_execution_strategy",
    "director_execution_strategy",
    "execution_strategy",
)

#: Strategy payload keys scanned by the tool_helpers first-call forced-write
#: budget path. Missing ``task_execution_contract`` /
#: ``director_execution_contract`` relative to the canonical list.
BUDGET_STRATEGY_PAYLOAD_KEYS: Final[tuple[str, ...]] = (
    "task_execution_strategy",
    "director_execution_strategy",
    "execution_strategy",
)

#: Nested budget keys probed inside a strategy/contract payload.
STRATEGY_NESTED_BUDGET_KEYS: Final[tuple[str, ...]] = (
    "output_budget_tokens",
    "llm_max_tokens",
    "max_output_tokens",
    "max_tokens",
)

#: Nested containers the CANONICAL scan descends into.
CANONICAL_NESTED_CONTAINER_KEYS: Final[tuple[str, ...]] = ("context_budget",)

#: Nested containers the STRATEGY-PAYLOAD scan descends into (tool_helpers).
STRATEGY_NESTED_CONTAINER_KEYS: Final[tuple[str, ...]] = (
    "budget_policy",
    "context_budget",
    "llm_sampling",
)

#: Per-call timeout override keys (trusted runtime context), priority order.
TIMEOUT_OVERRIDE_CONTEXT_KEYS: Final[tuple[str, ...]] = (
    "llm_call_timeout_seconds",
    "request_timeout_seconds",
    "timeout_seconds",
)

#: Per-call timeout CEILING keys (may only reduce, never extend).
TIMEOUT_CEILING_CONTEXT_KEYS: Final[tuple[str, ...]] = (
    "llm_call_timeout_ceiling_seconds",
    "request_timeout_ceiling_seconds",
    "timeout_ceiling_seconds",
)

# ---------------------------------------------------------------------------
# Turn-kind classification signals (existing detectors, consolidated)
# ---------------------------------------------------------------------------

#: Stage labels the director adapter treats as forced-write retries.
FORCED_WRITE_STAGE_MARKERS: Final[tuple[str, ...]] = (
    "no_write_materialization_retry",
    "empty_write_content_retry",
    "contract_violation_retry",
)

#: Context keys the director adapter treats as forced-write retry markers.
FORCED_WRITE_CONTEXT_KEYS: Final[tuple[str, ...]] = (
    "director_no_write_materialization_retry",
    "director_empty_write_retry",
)

TURN_KIND_FIRST_CALL: Final[str] = "first_call"
TURN_KIND_ORDINARY_FOLLOWUP: Final[str] = "ordinary_followup"
TURN_KIND_FORCED_WRITE_RETRY: Final[str] = "forced_write_retry"
TURN_KIND_REQUIRED_TOOL_RETRY: Final[str] = "required_tool_retry"
TURN_KIND_REASONING_TRUNCATION_RETRY: Final[str] = "reasoning_truncation_retry"
TURN_KIND_REPAIR_SUBCALL: Final[str] = "repair_subcall"
TURN_KIND_FINALIZATION: Final[str] = "finalization"

_TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY = "_transaction_kernel_forced_tool_definitions"
_TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY = "_transaction_kernel_forced_tool_choice"


def _coerce_positive_int(value: Any) -> int | None:
    """Parse a strictly positive int; bools, garbage and non-positives → None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _coerce_positive_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def clamp_output_tokens(value: int, *, floor: int = 1) -> int:
    """The ONE implementation of the ``max(floor, min(x, 128_000))`` clamp."""
    return max(floor, min(int(value), HARD_OUTPUT_TOKEN_CLAMP))


def chief_engineer_structured_output_tokens(
    environ: Mapping[str, str] | None = None,
) -> int:
    """Resolve one budget for every structured Chief Engineer response.

    Args:
        environ: Optional environment mapping. Injection keeps policy tests
            deterministic without mutating process-global state.

    Returns:
        Positive output-token budget bounded by the platform hard clamp.
    """

    source = os.environ if environ is None else environ
    parsed = _coerce_positive_int(source.get(CHIEF_ENGINEER_STRUCTURED_OUTPUT_TOKEN_ENV))
    if parsed is None:
        return DEFAULT_CHIEF_ENGINEER_STRUCTURED_OUTPUT_TOKENS
    return clamp_output_tokens(parsed)


def chief_engineer_portfolio_output_tokens(
    task_count: int,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Resolve a task-scaled output budget for one Factory CE portfolio.

    The shared structured-output policy remains the authoritative deployment
    cap.  This projection only chooses the amount actually requested for a
    portfolio of ``task_count`` PM contracts, preventing small projects from
    inheriting the full 128k physical generation budget.
    """

    normalized_task_count = max(1, int(task_count))
    scaled_budget = max(
        CHIEF_ENGINEER_PORTFOLIO_OUTPUT_TOKEN_FLOOR,
        normalized_task_count * CHIEF_ENGINEER_PORTFOLIO_OUTPUT_TOKENS_PER_TASK,
    )
    return min(chief_engineer_structured_output_tokens(environ), clamp_output_tokens(scaled_budget))


def chief_engineer_portfolio_generation_floor_seconds(
    task_count: int,
    environ: Mapping[str, str] | None = None,
) -> float:
    """Model the physical wall-clock floor to stream one full CE portfolio.

    The Factory deadline admission gate uses this as a fail-closed floor: when
    the deadline-clipped budget available to the CE stage is below the time the
    provider physically needs to stream the requested output tokens, EXECUTE
    would deterministically end in ``provider_stream_timeout``. The model is
    ``requested_output_tokens / conservative_streaming_rate``; the rate floor is
    deliberately pessimistic so the floor never over-promises.
    """

    requested_tokens = chief_engineer_portfolio_output_tokens(task_count, environ)
    return requested_tokens / CHIEF_ENGINEER_STREAMING_TOKENS_PER_SECOND_FLOOR


def forced_write_output_token_ceiling() -> int:
    """Parse ``KERNELONE_DIRECTOR_FORCED_WRITE_OUTPUT_TOKENS`` — single parser.

    Semantics (converged on the stricter tool_helpers implementation): a
    missing, non-numeric or NON-POSITIVE env value falls back to the default
    ceiling of :data:`FORCED_WRITE_OUTPUT_TOKEN_CEILING`; valid values are
    clamped to ``[FORCED_WRITE_OUTPUT_TOKEN_FLOOR, HARD_OUTPUT_TOKEN_CLAMP]``.
    (The retired adapter copy let ``0``/negative degrade to the 512 floor —
    an accidental, undocumented divergence; fail-back-to-default wins.)
    """
    parsed = _coerce_positive_int(os.environ.get(FORCED_WRITE_OUTPUT_TOKEN_ENV))
    value = parsed if parsed is not None else FORCED_WRITE_OUTPUT_TOKEN_CEILING
    return clamp_output_tokens(value, floor=FORCED_WRITE_OUTPUT_TOKEN_FLOOR)


def forced_write_retry_timeout_seconds(*, upper: float) -> float:
    """Parse ``KERNELONE_DIRECTOR_RETRY_LLM_TIMEOUT_SECONDS`` — single parser.

    Bounded to ``[FORCED_WRITE_RETRY_TIMEOUT_MIN_SECONDS, upper]`` where
    ``upper`` is the already-resolved stage timeout (the retry cap may only
    shrink it). Missing/invalid/non-positive env values use the
    :data:`FORCED_WRITE_RETRY_TIMEOUT_SECONDS` default.
    """
    parsed = _coerce_positive_float(os.environ.get(FORCED_WRITE_RETRY_TIMEOUT_ENV))
    value = FORCED_WRITE_RETRY_TIMEOUT_SECONDS if parsed is None else parsed
    return max(FORCED_WRITE_RETRY_TIMEOUT_MIN_SECONDS, min(value, upper))


def resolve_director_dispatch_timeout_seconds(env: Mapping[str, Any] | None = None) -> int:
    """Resolve the Factory Director dispatch timeout from budget policy facts.

    The resolved value is projected into the Factory Director stage as both
    stage timeout and LLM-call timeout. Env overrides may raise the default
    timeout; invalid or non-positive values are ignored.
    """

    source = os.environ if env is None else env
    candidates = [DEFAULT_DIRECTOR_DISPATCH_TIMEOUT_SECONDS]
    for env_key in DIRECTOR_DISPATCH_TIMEOUT_ENV_KEYS:
        raw = source.get(env_key)
        if raw is None:
            continue
        try:
            value = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            continue
        if value > 0:
            candidates.append(value)
    return max(candidates)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _stage_label(context: Mapping[str, Any], options: Mapping[str, Any]) -> str:
    label = str(options.get("stage_label") or "").strip()
    if label:
        return label.lower()
    timeout_budget = _mapping(context.get("director_role_call_timeout_budget"))
    return str(timeout_budget.get("stage_label") or "").strip().lower()


def _tool_surface_disabled(context: Mapping[str, Any], options: Mapping[str, Any]) -> bool:
    forced_definitions = context.get(_TRANSACTION_KERNEL_FORCED_TOOL_DEFINITIONS_KEY)
    forced_choice = str(context.get(_TRANSACTION_KERNEL_FORCED_TOOL_CHOICE_KEY) or "").strip().lower()
    if isinstance(forced_definitions, list) and not forced_definitions and forced_choice == "none":
        return True
    return str(options.get("tool_choice") or "").strip().lower() == "none"


def classify_turn_kind(context: Any, options: Any = None) -> str:
    """Classify a turn from the EXISTING per-path detection signals.

    ``context`` is the trusted runtime context / context override mapping;
    ``options`` optionally carries call-shaping hints (``stage_label``,
    ``tool_choice``). Non-mapping inputs classify as ordinary followup.

    Signal sources (consolidated, not invented):

    - finalization: explicit empty forced tool definitions + tool_choice
      ``none`` (TransactionKernel finalization marker) or an options-level
      ``tool_choice="none"``;
    - required_tool_retry: ``required_tool_retry`` /
      ``required_tool_retry_budget`` context markers (llm_caller invoker);
    - reasoning_truncation_retry: ``reasoning_truncation_retry`` marker;
    - forced_write_retry: adapter stage labels
      (:data:`FORCED_WRITE_STAGE_MARKERS`), adapter context markers
      (:data:`FORCED_WRITE_CONTEXT_KEYS`) or the stamped
      ``director_forced_write_output_budget`` payload;
    - repair_subcall: ``quality_repair`` stage labels (quality gate) or a
      ``director_quality_repair`` context payload;
    - first_call: ``first_call`` stage label, the
      ``director_first_call_materialization_scope`` marker or the stamped
      ``director_first_call_output_budget`` payload.
    """
    context_map = _mapping(context)
    options_map = _mapping(options)
    stage_label = _stage_label(context_map, options_map)

    if _tool_surface_disabled(context_map, options_map):
        return TURN_KIND_FINALIZATION
    if bool(context_map.get("required_tool_retry")) or isinstance(
        context_map.get("required_tool_retry_budget"), Mapping
    ):
        return TURN_KIND_REQUIRED_TOOL_RETRY
    if bool(context_map.get("reasoning_truncation_retry")):
        return TURN_KIND_REASONING_TRUNCATION_RETRY
    if (
        any(marker in stage_label for marker in FORCED_WRITE_STAGE_MARKERS)
        or any(key in context_map for key in FORCED_WRITE_CONTEXT_KEYS)
        or isinstance(context_map.get("director_forced_write_output_budget"), Mapping)
    ):
        return TURN_KIND_FORCED_WRITE_RETRY
    if stage_label.startswith("quality_repair") or isinstance(context_map.get("director_quality_repair"), Mapping):
        return TURN_KIND_REPAIR_SUBCALL
    if (
        stage_label == TURN_KIND_FIRST_CALL
        or isinstance(context_map.get("director_first_call_materialization_scope"), Mapping)
        or isinstance(context_map.get("director_first_call_output_budget"), Mapping)
    ):
        return TURN_KIND_FIRST_CALL
    return TURN_KIND_ORDINARY_FOLLOWUP


# ---------------------------------------------------------------------------
# ResolvedBudgetV1 — observability-only projection of the ACTUAL budget
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedBudgetV1:
    """Frozen projection of the budget a request was actually sent with.

    Observability only: values are copied from the already-resolved request
    options — building this object must never change a resolved number.
    ``provenance`` maps each field to the mechanism that produced it (and, in
    Phase 2+, the value it overrode). ``output_floor_tokens=0`` means no
    explicit output floor was visible at this call site.
    """

    max_output_tokens: int
    output_floor_tokens: int
    llm_timeout_seconds: float
    request_timeout_seconds: float
    turn_kind: str
    provenance: Mapping[str, Any] = field(default_factory=dict)

    schema_version: str = "kernelone.execution_budget.v1"

    def to_payload(self) -> dict[str, Any]:
        """JSON-serializable dict for request-context / audit stamping."""
        return {
            "schema_version": self.schema_version,
            "max_output_tokens": int(self.max_output_tokens),
            "output_floor_tokens": int(self.output_floor_tokens),
            "llm_timeout_seconds": float(self.llm_timeout_seconds),
            "request_timeout_seconds": float(self.request_timeout_seconds),
            "turn_kind": str(self.turn_kind),
            "provenance": {str(key): value for key, value in dict(self.provenance).items()},
        }


def resolve_execution_budget(
    *,
    role_id: str,
    context: Mapping[str, Any] | None,
    request_options: Mapping[str, Any] | None,
    max_output_tokens: int,
    llm_timeout_seconds: float,
    request_timeout_seconds: float | None = None,
    context_max_tokens_present: bool = False,
    context_timeout_present: bool = False,
    output_floor_tokens: int = 0,
    output_floor_provenance: str = "no_explicit_floor_visible",
) -> ResolvedBudgetV1:
    """Freeze the ACTUAL resolved request budget as a typed projection.

    This resolver is intentionally observability-only: callers pass in the
    already-resolved provider request numbers. The function centralizes
    provenance, turn-kind classification and JSON-ready budget shape without
    recalculating or expanding the execution budget.
    """

    normalized_role = str(role_id or "").strip().lower()
    resolved_request_timeout = (
        float(request_timeout_seconds) if request_timeout_seconds is not None else float(llm_timeout_seconds)
    )
    provenance: dict[str, Any] = {
        "max_output_tokens": ("context_override" if context_max_tokens_present else "requested_clamped"),
        "output_floor_tokens": str(output_floor_provenance or "no_explicit_floor_visible"),
        "llm_timeout_seconds": (
            "director_timeout_policy"
            if normalized_role == "director"
            else ("context_override" if context_timeout_present else "role_default")
        ),
        "request_timeout_seconds": "same_funnel_as_llm_timeout",
        "turn_kind": "classify_turn_kind",
    }
    return ResolvedBudgetV1(
        max_output_tokens=int(max_output_tokens),
        output_floor_tokens=max(0, int(output_floor_tokens)),
        llm_timeout_seconds=float(llm_timeout_seconds),
        request_timeout_seconds=resolved_request_timeout,
        turn_kind=classify_turn_kind(context, request_options),
        provenance=provenance,
    )


__all__ = [
    "BUDGET_CONTEXT_KEYS_CANONICAL",
    "BUDGET_STRATEGY_PAYLOAD_KEYS",
    "CANONICAL_NESTED_CONTAINER_KEYS",
    "CHIEF_ENGINEER_PORTFOLIO_OUTPUT_TOKENS_PER_TASK",
    "CHIEF_ENGINEER_PORTFOLIO_OUTPUT_TOKEN_FLOOR",
    "CHIEF_ENGINEER_STRUCTURED_OUTPUT_TOKEN_ENV",
    "DEFAULT_CHIEF_ENGINEER_STRUCTURED_OUTPUT_TOKENS",
    "DEFAULT_DIRECTOR_DISPATCH_TIMEOUT_SECONDS",
    "DIRECTOR_DISPATCH_TIMEOUT_ENV_KEYS",
    "FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS",
    "FORCED_WRITE_CONTEXT_KEYS",
    "FORCED_WRITE_OUTPUT_TOKEN_CEILING",
    "FORCED_WRITE_OUTPUT_TOKEN_ENV",
    "FORCED_WRITE_OUTPUT_TOKEN_FLOOR",
    "FORCED_WRITE_RETRY_TIMEOUT_ENV",
    "FORCED_WRITE_RETRY_TIMEOUT_MIN_SECONDS",
    "FORCED_WRITE_RETRY_TIMEOUT_SECONDS",
    "FORCED_WRITE_STAGE_MARKERS",
    "HARD_OUTPUT_TOKEN_CLAMP",
    "OUTPUT_BUDGET_CONTEXT_KEYS",
    "REASONING_TRUNCATION_RETRY_OUTPUT_TOKENS",
    "REQUIRED_TOOL_RETRY_OUTPUT_TOKEN_CAP",
    "REQUIRED_TOOL_RETRY_TIMEOUT_SECONDS",
    "RETRY_CREATE_OUTPUT_FLOOR_TOKENS",
    "STRATEGY_NESTED_BUDGET_KEYS",
    "STRATEGY_NESTED_CONTAINER_KEYS",
    "TIMEOUT_CEILING_CONTEXT_KEYS",
    "TIMEOUT_OVERRIDE_CONTEXT_KEYS",
    "TURN_KIND_FINALIZATION",
    "TURN_KIND_FIRST_CALL",
    "TURN_KIND_FORCED_WRITE_RETRY",
    "TURN_KIND_ORDINARY_FOLLOWUP",
    "TURN_KIND_REASONING_TRUNCATION_RETRY",
    "TURN_KIND_REPAIR_SUBCALL",
    "TURN_KIND_REQUIRED_TOOL_RETRY",
    "ResolvedBudgetV1",
    "chief_engineer_portfolio_generation_floor_seconds",
    "chief_engineer_portfolio_output_tokens",
    "chief_engineer_structured_output_tokens",
    "clamp_output_tokens",
    "classify_turn_kind",
    "forced_write_output_token_ceiling",
    "forced_write_retry_timeout_seconds",
    "resolve_director_dispatch_timeout_seconds",
    "resolve_execution_budget",
]
