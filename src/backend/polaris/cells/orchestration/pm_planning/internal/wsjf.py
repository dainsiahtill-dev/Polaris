"""WSJF (Weighted Shortest Job First) prioritization core for ``orchestration.pm_planning``.

This module is the pure economic scorer that lets the PM (尚书令) role rank tasks by
``WSJF = Cost of Delay / Job Size`` (SAFe / Weighted Shortest Job First) instead of by a
bare ordinal ``priority`` integer. Highest WSJF ranks first: the most valuable, time-critical
work that is also the smallest job is done first.

Design properties (all enforced):
  * **Pure / deterministic / I/O-free / clock-free / env-free.** No file access, no
    ``os.environ``/``getenv``, no ``datetime``/``time.time``. The same inputs always produce
    byte-identical outputs, with a fully specified deterministic tie-break.
  * **§8-clean.** Every weight, boost, and scale is a generic, documented constant with zero
    domain/business/project meaning. ``WSJF``/``value``/``effort``/``priority`` are generic PM
    terms only.
  * **§7 reuse.** ``job_size`` reuses the Critical Path Method per-task effort that the schedule
    already computed (``Schedule.weights``), and the critical-path fold reuses
    ``Schedule.critical_path``. RAID pressure is folded as **plain data** (an optional
    ``task_id -> open_critical_or_blocker`` mapping) so this core needs no ``raid_register``
    import and stays trivially testable with plain dicts.
  * **Boundary-clean.** Imports only stdlib plus the same-cell sibling ``.dependency_validator``
    (for the :class:`Schedule` type and the shared ``_task_weight`` effort semantics). It does
    NOT import ``polaris.delivery`` and adds no new cross-cell edge.

This increment ships the **pure scorer only**. It is wired to nothing: the three live raw-priority
sort sites (``pipeline.py``, ``internal/pm_agent.py``, ``pm_dispatch/internal/shangshuling_registry.py``)
stay byte-untouched, so the regression floor cannot move. A future increment, behind a default-off
env gate, would replace the ordinal sort at those sites with this WSJF ranking, keeping the raw
sort as the gate-off fallback. No env gate is needed in the pure core itself: an unused pure
function is inherently off.

WSJF formula
------------
``cost_of_delay`` is the sum of three generic, normalized components::

    cost_of_delay = value_component + time_criticality_component + risk_reduction_component

    value_component            = value_weight            * value
    time_criticality_component = time_criticality_weight * (optional_field
                                                            + priority_urgency
                                                            + critical_path_boost?)
    risk_reduction_component   = risk_reduction_weight   * (optional_field
                                                            + raid_pressure_boost?)

``job_size`` is the CPM per-task effort, epsilon-floored, and::

    wsjf = cost_of_delay / job_size

The ``job_size`` epsilon floor is applied *before* the division (so never
:class:`ZeroDivisionError`), and a pathological float overflow is clamped to the largest finite
float, so WSJF is always finite and JSON-safe.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .dependency_validator import Schedule, _task_weight

__all__ = ["WsjfScore", "render_wsjf_markdown", "score_tasks_wsjf"]


# --------------------------------------------------------------------------------------
# Generic documented constants (zero domain meaning, §8-clean).
# --------------------------------------------------------------------------------------

#: Multiplier on the raw business-``value`` component of Cost of Delay.
_DEFAULT_VALUE_WEIGHT: float = 1.0
#: Multiplier on the time-criticality component of Cost of Delay.
_DEFAULT_TIME_CRITICALITY_WEIGHT: float = 1.0
#: Multiplier on the risk-reduction / opportunity component of Cost of Delay.
_DEFAULT_RISK_REDUCTION_WEIGHT: float = 1.0
#: Additive boost folded into time-criticality (before its weight) when a task sits on the
#: schedule critical path. A critical-path task is, by CPM, more time-critical.
_DEFAULT_CRITICAL_PATH_BOOST: float = 2.0
#: Additive boost folded into risk-reduction (before its weight) when a task carries any open
#: critical/blocker RAID pressure (``raid_pressure > 0``).
_DEFAULT_RAID_PRESSURE_BOOST: float = 2.0
#: Fallback used when a task carries no usable integer ``priority`` (mirrors the live
#: ``normalize_priority(..., fallback=9999)`` ordinal semantics).
_DEFAULT_PRIORITY_FALLBACK: int = 9999
#: Numerator of the inverse-priority urgency term: ``priority_urgency = scale / max(priority, 1)``.
#: Lower priority int -> higher urgency (priority 1 -> 10.0, priority 5 -> 2.0).
_PRIORITY_INVERSION_SCALE: float = 10.0
#: Lower bound applied to ``job_size`` to guarantee a finite WSJF (divide-by-zero floor).
_JOB_SIZE_EPSILON: float = 1e-9
#: Largest finite float; a pathological WSJF overflow (a huge Cost of Delay over an epsilon-floored
#: job size) is clamped here so the score stays finite and JSON-safe while still ranking first.
_WSJF_MAX: float = sys.float_info.max
#: Fixed float formatting used by :func:`render_wsjf_markdown` for byte-stable output.
_MARKDOWN_FLOAT_FMT: str = "{:.3f}"


# --------------------------------------------------------------------------------------
# Generic field names / aliases (generic PM terms only, §8-clean).
# --------------------------------------------------------------------------------------

#: Single generic business-value field name.
_VALUE_FIELD: str = "value"
#: Optional time-criticality aliases, in precedence order (first present wins).
_TIME_CRITICALITY_ALIASES: tuple[str, ...] = ("time_criticality", "criticality", "urgency")
#: Optional risk-reduction / opportunity aliases, in precedence order (first present wins).
_RISK_REDUCTION_ALIASES: tuple[str, ...] = ("risk_reduction", "opportunity_enablement", "risk")


# --------------------------------------------------------------------------------------
# Result type.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class WsjfScore:
    """One task's WSJF score and its fully decomposed Cost-of-Delay components.

    ``wsjf`` is the ranking key (highest first). ``cost_of_delay`` is the sum of the three
    components. ``job_size`` is the epsilon-floored CPM effort (verbatim ``Schedule.weights``
    for any in-schedule task). The flags ``on_critical_path`` and ``raid_pressure`` record the
    two reuse folds that drive the critical-path and RAID boosts.
    """

    task_id: str
    wsjf: float
    cost_of_delay: float
    job_size: float
    value_component: float
    time_criticality_component: float
    risk_reduction_component: float
    on_critical_path: bool
    raid_pressure: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe ``dict`` of every field (stable key set)."""
        return {
            "task_id": self.task_id,
            "wsjf": self.wsjf,
            "cost_of_delay": self.cost_of_delay,
            "job_size": self.job_size,
            "value_component": self.value_component,
            "time_criticality_component": self.time_criticality_component,
            "risk_reduction_component": self.risk_reduction_component,
            "on_critical_path": self.on_critical_path,
            "raid_pressure": self.raid_pressure,
        }


# --------------------------------------------------------------------------------------
# Module-private fail-closed readers.
# --------------------------------------------------------------------------------------


def _coerce_nonneg_float(value: Any) -> float:
    """Fail-closed numeric read: a real ``int``/``float`` (not ``bool``) clamped to ``>= 0.0``.

    ``bool``, ``None``, and any non-numeric / non-finite input coerce to ``0.0`` so a stray
    ``True`` never silently reads as ``1.0`` and a junk string never raises.
    """
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):  # NaN / +-inf
            return 0.0
        return number if number > 0.0 else 0.0
    return 0.0


def _coerce_nonneg_int(value: Any) -> int:
    """Fail-closed count read: a real non-``bool`` number truncated to a ``>= 0`` ``int``.

    Delegates to :func:`_coerce_nonneg_float` (so ``bool``/``None``/``NaN``/``inf``/strings/lists
    all degrade to ``0``) then truncates. Used for the RAID-pressure fold so a present-but-junk
    mapping value -- e.g. a ``None`` projected for a task with no RAID record -- never raises.
    """
    return int(_coerce_nonneg_float(value))


def _read_first_alias(task: dict[str, Any], aliases: tuple[str, ...]) -> float:
    """Return the first present alias among ``aliases`` coerced fail-closed to a non-negative float.

    Precedence follows the alias order. A present-but-junk value (``None``/``str``/``bool``)
    coerces to ``0.0`` but still counts as "present" and stops the search, so a later alias does
    not silently override an explicitly-supplied (if unusable) earlier field.
    """
    for alias in aliases:
        if alias in task:
            return _coerce_nonneg_float(task.get(alias))
    return 0.0


def _priority_int(task: dict[str, Any], fallback: int) -> int:
    """Read the raw ordinal ``priority`` fail-closed.

    A genuine non-``bool`` ``int`` is returned as-is; ``bool``/``None``/``str``/``float``/absent
    all fall back to ``fallback`` (mirrors the live ``normalize_priority(..., fallback=9999)``).
    """
    raw = task.get("priority")
    if isinstance(raw, bool):
        return fallback
    if isinstance(raw, int):
        return raw
    return fallback


def _priority_urgency(priority_int: int) -> float:
    """Inverse-priority urgency term: ``scale / max(priority_int, 1)`` (lower int -> higher).

    Generic and bounded (max ``_PRIORITY_INVERSION_SCALE`` at priority 1); the ``max(.., 1)``
    guard keeps it finite and non-negative even for zero/negative priority ints.
    """
    return _PRIORITY_INVERSION_SCALE / max(priority_int, 1)


def _job_size(task_id: str, task: dict[str, Any], schedule: Schedule, epsilon: float) -> float:
    """Return the epsilon-floored CPM job size (effort) for ``task_id``.

    Primary path reuses ``Schedule.weights[task_id]`` verbatim (§7 reuse #3) so that for any
    in-schedule task ``job_size == schedule.weights[task_id]`` byte-identically and
    ``WSJF == cost_of_delay / effort`` by construction. The fallback path (task absent from the
    schedule — e.g. the caller passed a superset list) recomputes via the *same* shared
    ``_task_weight`` the schedule uses, never a re-implemented effort extraction. The final
    ``max(weight, epsilon)`` floor guarantees a finite WSJF even for a degenerate weight.
    """
    weight = schedule.weights.get(task_id)
    if weight is None:
        weight = _task_weight(task)
    return max(weight, epsilon)


def _finite_wsjf(cost_of_delay: float, job_size: float) -> float:
    """Return ``cost_of_delay / job_size`` clamped to a finite, JSON-safe value.

    ``job_size`` is epsilon-floored (strictly positive) and ``cost_of_delay`` is non-negative, so
    the quotient is never ``NaN``/``-inf``; the only non-finite case is a ``+inf`` overflow from a
    huge Cost of Delay over a tiny job size, which is clamped to :data:`_WSJF_MAX` so WSJF stays
    finite (and ``to_dict`` stays JSON-safe) while that near-zero-effort task still ranks first.
    """
    quotient = cost_of_delay / job_size
    if quotient != quotient or quotient in (float("inf"), float("-inf")):  # NaN / +-inf
        return _WSJF_MAX
    return quotient


# --------------------------------------------------------------------------------------
# Public scorer + renderer.
# --------------------------------------------------------------------------------------


def score_tasks_wsjf(
    tasks: list[dict[str, Any]],
    schedule: Schedule,
    *,
    raid_pressure_by_task: Mapping[str, int] | None = None,
    value_weight: float = _DEFAULT_VALUE_WEIGHT,
    time_criticality_weight: float = _DEFAULT_TIME_CRITICALITY_WEIGHT,
    risk_reduction_weight: float = _DEFAULT_RISK_REDUCTION_WEIGHT,
    critical_path_boost: float = _DEFAULT_CRITICAL_PATH_BOOST,
    raid_pressure_boost: float = _DEFAULT_RAID_PRESSURE_BOOST,
    priority_fallback: int = _DEFAULT_PRIORITY_FALLBACK,
    job_size_epsilon: float = _JOB_SIZE_EPSILON,
) -> list[WsjfScore]:
    """Score ``tasks`` by WSJF against a precomputed ``schedule``; rank highest WSJF first.

    Each task is read fail-closed (non-dict entries are skipped, mirroring
    :func:`compute_schedule`). The result is sorted by the deterministic key
    ``(-wsjf, job_size, task_id)``: highest WSJF first, then smallest job first, then task id.

    Parameters
    ----------
    tasks:
        The task dicts. For the verbatim ``job_size`` guarantee, pass the *identical* list that
        produced ``schedule`` (so every id is a key of ``schedule.weights``).
    schedule:
        A :class:`Schedule` from :func:`compute_schedule` over the same tasks (supplies the §7
        reused ``weights`` and ``critical_path``).
    raid_pressure_by_task:
        Optional plain-data RAID fold: ``task_id -> open_critical_or_blocker`` count. The deferred
        glue projects ``RaidRegister.summarize(task_id=X)["open_critical_or_blocker"]`` into this
        mapping; the pure core never imports or constructs ``RaidRegister``. Missing keys / ``None``
        mean no RAID pressure (fail-closed).
    value_weight, time_criticality_weight, risk_reduction_weight:
        Multipliers on the three Cost-of-Delay components.
    critical_path_boost:
        Additive boost into time-criticality when a task is on ``schedule.critical_path``.
    raid_pressure_boost:
        Additive boost into risk-reduction when a task carries ``raid_pressure > 0``.
    priority_fallback:
        Fallback ordinal when ``priority`` is absent/unusable.
    job_size_epsilon:
        Lower bound on ``job_size`` (divide-by-zero floor).

    Returns
    -------
    list[WsjfScore]
        One score per dict task, ranked highest WSJF first.
    """
    # Built ONCE outside the loop: O(1) membership, zero set-iteration-order leak.
    critical_set = set(schedule.critical_path)
    raid = raid_pressure_by_task or {}

    scores: list[WsjfScore] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "").strip()
        on_path = task_id in critical_set
        raid_pressure = _coerce_nonneg_int(raid.get(task_id))

        job_size = _job_size(task_id, task, schedule, job_size_epsilon)

        value_component = value_weight * _coerce_nonneg_float(task.get(_VALUE_FIELD))

        time_criticality_component = time_criticality_weight * (
            _read_first_alias(task, _TIME_CRITICALITY_ALIASES)
            + _priority_urgency(_priority_int(task, priority_fallback))
            + (critical_path_boost if on_path else 0.0)
        )

        risk_reduction_component = risk_reduction_weight * (
            _read_first_alias(task, _RISK_REDUCTION_ALIASES) + (raid_pressure_boost if raid_pressure > 0 else 0.0)
        )

        cost_of_delay = value_component + time_criticality_component + risk_reduction_component
        wsjf = _finite_wsjf(cost_of_delay, job_size)

        scores.append(
            WsjfScore(
                task_id=task_id,
                wsjf=wsjf,
                cost_of_delay=cost_of_delay,
                job_size=job_size,
                value_component=value_component,
                time_criticality_component=time_criticality_component,
                risk_reduction_component=risk_reduction_component,
                on_critical_path=on_path,
                raid_pressure=raid_pressure,
            )
        )

    scores.sort(key=lambda s: (-s.wsjf, s.job_size, s.task_id))
    return scores


def render_wsjf_markdown(scores: list[WsjfScore]) -> str:
    """Render ``scores`` as a byte-stable Markdown table, rows in the given (ranked) order.

    Pure and clock-free: the output contains no date/time token. Floats are formatted with the
    fixed ``_MARKDOWN_FLOAT_FMT`` so two calls on the same list are byte-identical.
    """
    header = (
        "| Rank | Task | WSJF | CoD | Job Size | Value | Time Criticality | Risk Reduction | Critical Path | RAID |"
    )
    separator = "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    lines = [header, separator]
    for rank, score in enumerate(scores, start=1):
        lines.append(
            "| {rank} | {task} | {wsjf} | {cod} | {job} | {value} | {tc} | {risk} | {path} | {raid} |".format(
                rank=rank,
                task=score.task_id,
                wsjf=_MARKDOWN_FLOAT_FMT.format(score.wsjf),
                cod=_MARKDOWN_FLOAT_FMT.format(score.cost_of_delay),
                job=_MARKDOWN_FLOAT_FMT.format(score.job_size),
                value=_MARKDOWN_FLOAT_FMT.format(score.value_component),
                tc=_MARKDOWN_FLOAT_FMT.format(score.time_criticality_component),
                risk=_MARKDOWN_FLOAT_FMT.format(score.risk_reduction_component),
                path="yes" if score.on_critical_path else "no",
                raid=score.raid_pressure,
            )
        )
    return "\n".join(lines)
