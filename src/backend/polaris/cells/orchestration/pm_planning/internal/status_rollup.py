"""Project status rollup for the PM (尚书令) planning cell.

A status rollup is the report a real project manager publishes every cycle:
percent-complete, a RAG (red/amber/green) health band, an ETA/forecast, the
critical path, an open-RAID summary, and requirements coverage. This module is
the **pure core** of that report.

It is pure, deterministic, I/O-free, clock-free and 100% generic meta-tooling
(no project/business/domain literals). It consumes *plain-data* inputs -- an
aggregate ``status -> count`` mapping, the dict returned by
:meth:`RaidRegister.summarize`, and a :class:`Schedule` from the sibling
:mod:`.dependency_validator` -- and produces a frozen :class:`PmStatusRollup`
plus a byte-stable markdown renderer. It reuses the landed CPM schedule (§7)
instead of reinventing scheduling, and never reaches into the RAID store or the
delivery layer.

This increment is purely additive: nothing here is wired into the live planning
path, so the regression floor cannot move. The future delivery glue (a CLI
function ``build_pm_status_rollup`` next to ``report_utils.py``) is responsible
for gathering live inputs, stamping the real UTC clock into ``generated_at``,
calling :func:`compute_status_rollup`, and writing ``pm.status.md`` /
``pm.status.json``. The clock lives ONLY in that glue; the core stays
deterministic.

Definitions (deterministic, documented verbatim):

* ``total_tasks`` = sum of all (fail-closed coerced) status counts.
* ``completed_tasks`` = the count for status ``"completed"`` ONLY -- the single
  success terminal in the TaskBoard ``TaskStatus`` vocabulary
  (``queued/pending/blocked/ready/claimed/in_progress/completed/failed/
  cancelled/timeout``).
* ``terminal_tasks`` = sum over ``{completed, failed, cancelled, timeout}``.
* ``active_tasks`` = ``max(0, total_tasks - terminal_tasks)``; unknown/stray
  status keys contribute to ``total_tasks`` only, so they fall into
  ``active_tasks``.
* ``percent_complete`` = ``completed_tasks / total_tasks`` when ``total_tasks``
  is positive, else ``0.0``; rounded to :data:`_PROGRESS_PRECISION` dp. Only
  ``"completed"`` is success: ``failed``/``cancelled``/``timeout`` are terminal
  but NOT achieved, so they raise ``terminal_tasks`` (lowering ``active_tasks``)
  without inflating ``percent_complete``. An empty project is ``0.0`` done,
  never ``None``.

RAG band (generic, deterministic, §8-clean -- every input is a severity count,
a completion ratio, or schedule presence; zero domain logic), evaluated in this
exact precedence:

1. ``red`` iff ``open_critical_or_blocker > 0``. A live critical/blocker RAID
   entry is an unconditional RED; the completion overlay never masks it.
2. else ``green`` iff ``percent_complete >= 1.0`` (completion overlay).
3. else ``amber`` iff a high-severity open risk exists
   (``by_severity['high'] > 0``) OR the project is materially behind
   (``active_tasks > 0`` AND ``makespan > 0.0`` AND
   ``percent_complete < _AT_RISK_PROGRESS_FLOOR``).
4. else ``green``.

ETA / effort (no fabrication):

* ``remaining_effort`` (aggregate, shipped default) =
  ``round(sum(schedule.weights.values()) * (1 - completed_fraction), prec)``
  where ``completed_fraction = completed_tasks / total_tasks`` when
  ``total_tasks`` is positive else ``0.0``. When ``completed_ids`` is supplied,
  the EXACT computation is used instead:
  ``round(sum(weights for ids not in completed_ids), prec)``. The aggregate form
  is an explicit approximation; the exact form is opt-in for the future glue.
* ``eta_iterations`` = ``remaining_effort / velocity`` ONLY when ``velocity`` is
  a finite float ``> 0``; in every other case (``None``, ``0``, negative,
  ``NaN``, ``+/-inf``) it is ``None``. A status report must never invent a
  horizon. When velocity is positive and ``remaining_effort == 0.0``,
  ``eta_iterations == 0.0`` (a valid "done" forecast, not ``None``).

Requirements coverage is carried as a ``0..1`` ratio (unit-uniform with
``percent_complete``); ``None`` distinguishes "untracked" from "0% covered".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any

from .dependency_validator import Schedule

__all__ = ["PmStatusRollup", "compute_status_rollup", "render_status_markdown"]

# Number of decimal places used when rounding progress / coverage ratios so two
# structurally-equal rollups compare equal and ``to_dict()`` snapshots are
# byte-stable.
_PROGRESS_PRECISION: int = 4

# Generic "materially behind" schedule-progress threshold. The only tunable in
# the RAG rule; it carries no domain meaning and is NOT tuned to any project.
_AT_RISK_PROGRESS_FLOOR: float = 0.5

# Terminal TaskBoard statuses (run has ended, by any outcome).
_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled", "timeout"})

# The single success terminal (a task that actually achieved its goal).
_COMPLETED_STATUS: str = "completed"


def _coerce_count(value: Any) -> int:
    """Coerce one status-count value to a non-negative ``int`` (fail-closed).

    Anything that is not a real, non-negative integer -- including ``bool``
    (``True``/``False`` must not silently count as ``1``/``0``), ``None``,
    strings, floats, and negative integers -- degrades to ``0`` rather than
    raising.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value >= 0:
        return value
    return 0


def _coerce_int_key(mapping: Mapping[str, Any], key: str) -> int:
    """Read ``mapping[key]`` as a non-negative ``int`` (fail-closed).

    A missing key, or any value that is not a non-negative integer, yields
    ``0``. Never raises.
    """
    return _coerce_count(mapping.get(key))


def _is_positive_finite(value: float | None) -> bool:
    """Return whether ``value`` is a finite real number strictly greater than 0.

    Rejects ``None``, ``bool`` (a velocity must be a real measurement, not a
    flag), non-numeric types, ``0``, negatives, ``NaN`` and ``+/-inf``. Used as
    the velocity guard so an unmeasured/garbage velocity yields ``eta = None``
    rather than a fabricated or exploding horizon.
    """
    if value is None or isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return isfinite(value) and value > 0.0


def _coerce_ratio(value: Any) -> float | None:
    """Coerce a supplied ``0..1`` ratio to a clamped float, or ``None``.

    Non-numeric / ``bool`` / non-finite inputs degrade to ``None``; finite
    numbers are clamped into ``[0.0, 1.0]``. Never raises.
    """
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)) or not isfinite(value):
        return None
    return min(1.0, max(0.0, float(value)))


@dataclass(frozen=True)
class PmStatusRollup:
    """Immutable snapshot of project status, ready to render or serialize.

    Every field is a plain JSON-safe primitive (or a tuple of strings). The
    rollup is frozen so a computed snapshot cannot be mutated after the fact;
    fixed-precision rounding makes two structurally-equal rollups compare equal.
    """

    total_tasks: int
    completed_tasks: int
    terminal_tasks: int
    active_tasks: int
    percent_complete: float
    rag: str
    open_risks: int
    open_critical_or_blocker: int
    makespan: float
    critical_path: tuple[str, ...]
    remaining_effort: float
    eta_iterations: float | None
    requirements_total: int
    requirements_covered: int
    requirements_coverage: float | None
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict.

        ``critical_path`` becomes a list; ``eta_iterations`` and
        ``requirements_coverage`` preserve ``None`` (the honest "unknown" /
        "untracked" sentinels). The result is safe to feed straight to
        ``json.dumps``.
        """
        return {
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "terminal_tasks": self.terminal_tasks,
            "active_tasks": self.active_tasks,
            "percent_complete": self.percent_complete,
            "rag": self.rag,
            "open_risks": self.open_risks,
            "open_critical_or_blocker": self.open_critical_or_blocker,
            "makespan": self.makespan,
            "critical_path": list(self.critical_path),
            "remaining_effort": self.remaining_effort,
            "eta_iterations": self.eta_iterations,
            "requirements_total": self.requirements_total,
            "requirements_covered": self.requirements_covered,
            "requirements_coverage": self.requirements_coverage,
            "generated_at": self.generated_at,
        }


def _resolve_requirements(
    *,
    requirements_total: int | None,
    requirements_covered: int | None,
    requirements_coverage: float | None,
) -> tuple[int, int, float | None]:
    """Resolve the three requirements fields fail-closed and unit-uniformly.

    Returns ``(total, covered, coverage_ratio)`` where ``coverage_ratio`` is a
    ``0..1`` ratio rounded to :data:`_PROGRESS_PRECISION`, or ``None`` when no
    requirements are tracked (``total <= 0``) -- distinguishing "untracked" from
    "0% covered".

    Precedence for ``covered``: the authoritative supplied count when given;
    else ``round(coverage_ratio * total)`` when a ratio is supplied; else ``0``.
    The core deliberately does NOT recompute ``covered`` from
    ``coverage * total`` when an explicit count exists, to avoid a rounding
    mismatch with the authoritative value.
    """
    total = _coerce_count(requirements_total)
    if total <= 0:
        return 0, 0, None

    supplied_ratio = _coerce_ratio(requirements_coverage)
    coerced_covered = _coerce_count(requirements_covered) if requirements_covered is not None else None

    if coerced_covered is not None:
        covered = min(coerced_covered, total)
        ratio = covered / total
    elif supplied_ratio is not None:
        covered = min(round(supplied_ratio * total), total)
        ratio = supplied_ratio
    else:
        covered = 0
        ratio = 0.0

    return total, covered, round(ratio, _PROGRESS_PRECISION)


def _compute_remaining_effort(
    schedule: Schedule,
    *,
    completed_fraction: float,
    completed_ids: Iterable[str] | None,
) -> float:
    """Compute remaining effort from CPM weights (§7 reuse, never recomputed).

    EXACT path (opt-in): when ``completed_ids`` is supplied, sum the weights of
    every scheduled id NOT in that set (ids absent from ``weights`` contribute
    ``0``). AGGREGATE path (shipped default): scale the total weight by the
    non-completed fraction. Both clamp at ``0.0``.
    """
    if completed_ids is not None:
        done = {str(tid) for tid in completed_ids}
        remaining = sum(weight for tid, weight in schedule.weights.items() if tid not in done)
    else:
        total_weight = sum(schedule.weights.values())
        remaining = total_weight * (1.0 - completed_fraction)
    return round(max(0.0, remaining), _PROGRESS_PRECISION)


def _compute_rag(
    *,
    open_critical_or_blocker: int,
    high_severity: int,
    percent_complete: float,
    active_tasks: int,
    makespan: float,
) -> str:
    """Map the generic signals to a ``red``/``amber``/``green`` band.

    See the module docstring for the exact precedence. Every input is a count,
    a ratio, or schedule presence; there is no domain logic.
    """
    if open_critical_or_blocker > 0:
        return "red"
    if percent_complete >= 1.0:
        return "green"
    materially_behind = active_tasks > 0 and makespan > 0.0 and percent_complete < _AT_RISK_PROGRESS_FLOOR
    if high_severity > 0 or materially_behind:
        return "amber"
    return "green"


def compute_status_rollup(
    *,
    status_counts: Mapping[str, int],
    raid_summary: Mapping[str, Any],
    schedule: Schedule,
    requirements_total: int | None = None,
    requirements_covered: int | None = None,
    requirements_coverage: float | None = None,
    velocity: float | None = None,
    completed_ids: Iterable[str] | None = None,
    generated_at: str = "",
) -> PmStatusRollup:
    """Compute the pure :class:`PmStatusRollup` from plain-data inputs.

    Pure, deterministic and total: every input is coerced fail-closed, so this
    NEVER raises on bad/partial/``None`` data (missing RAID keys degrade to
    ``0``, a non-positive/``NaN``/``inf`` velocity yields ``eta = None``, an
    empty ``status_counts`` yields a 0%-complete project).

    Args:
        status_counts: Aggregate ``TaskStatus`` value -> count mapping.
        raid_summary: The dict from :meth:`RaidRegister.summarize` (reads
            ``by_status['open']``, ``by_severity['high']`` and
            ``open_critical_or_blocker``, all fail-closed).
        schedule: A :class:`Schedule` from :func:`compute_schedule` (§7 reuse);
            ``makespan``, ``critical_path`` and ``weights`` are read verbatim.
        requirements_total: Total tracked requirements (``0``/``None`` ->
            untracked, ``requirements_coverage`` becomes ``None``).
        requirements_covered: Authoritative verified count, if known.
        requirements_coverage: A ``0..1`` ratio, if a count is not supplied. The
            future glue divides the ``0..100`` percent source by 100 first.
        velocity: Throughput in effort-units per iteration; must be a finite
            ``> 0`` float for an ETA to be produced.
        completed_ids: Optional exact completed-id set for an exact
            ``remaining_effort`` (the aggregate approximation is used when
            ``None``).
        generated_at: Caller-stamped ISO timestamp, stored verbatim (default
            ``""`` keeps the core clock-free).

    Returns:
        A frozen :class:`PmStatusRollup`.
    """
    total_tasks = sum(_coerce_count(value) for value in status_counts.values())
    completed_tasks = _coerce_int_key(status_counts, _COMPLETED_STATUS)
    terminal_tasks = sum(_coerce_int_key(status_counts, status) for status in _TERMINAL_STATUSES)
    # Guard against malformed inputs where a coerced terminal sum would exceed
    # the total (e.g. an overlapping/duplicated key supplied by a buggy caller).
    terminal_tasks = min(terminal_tasks, total_tasks)
    active_tasks = max(0, total_tasks - terminal_tasks)

    completed_fraction = completed_tasks / total_tasks if total_tasks > 0 else 0.0
    percent_complete = round(completed_fraction, _PROGRESS_PRECISION) if total_tasks > 0 else 0.0

    by_status = raid_summary.get("by_status")
    open_risks = _coerce_int_key(by_status, "open") if isinstance(by_status, Mapping) else 0
    by_severity = raid_summary.get("by_severity")
    high_severity = _coerce_int_key(by_severity, "high") if isinstance(by_severity, Mapping) else 0
    open_critical_or_blocker = _coerce_int_key(raid_summary, "open_critical_or_blocker")

    makespan = float(schedule.makespan)
    critical_path = tuple(schedule.critical_path)

    remaining_effort = _compute_remaining_effort(
        schedule,
        completed_fraction=completed_fraction,
        completed_ids=completed_ids,
    )
    eta_iterations: float | None = None
    if velocity is not None and _is_positive_finite(velocity):
        eta_iterations = round(remaining_effort / float(velocity), _PROGRESS_PRECISION)

    req_total, req_covered, req_coverage = _resolve_requirements(
        requirements_total=requirements_total,
        requirements_covered=requirements_covered,
        requirements_coverage=requirements_coverage,
    )

    rag = _compute_rag(
        open_critical_or_blocker=open_critical_or_blocker,
        high_severity=high_severity,
        percent_complete=percent_complete,
        active_tasks=active_tasks,
        makespan=makespan,
    )

    return PmStatusRollup(
        total_tasks=total_tasks,
        completed_tasks=completed_tasks,
        terminal_tasks=terminal_tasks,
        active_tasks=active_tasks,
        percent_complete=percent_complete,
        rag=rag,
        open_risks=open_risks,
        open_critical_or_blocker=open_critical_or_blocker,
        makespan=makespan,
        critical_path=critical_path,
        remaining_effort=remaining_effort,
        eta_iterations=eta_iterations,
        requirements_total=req_total,
        requirements_covered=req_covered,
        requirements_coverage=req_coverage,
        generated_at=generated_at,
    )


def _format_ratio_percent(ratio: float | None) -> str:
    """Render a ``0..1`` ratio as a one-decimal percent, or ``n/a`` for None."""
    if ratio is None:
        return "n/a"
    return f"{ratio * 100:.1f}%"


def render_status_markdown(rollup: PmStatusRollup) -> str:
    """Render a :class:`PmStatusRollup` to a deterministic markdown report.

    Byte-stable: the output depends ONLY on ``rollup`` (no clock, no env, no
    dict-iteration nondeterminism), so calling it twice on the same rollup
    returns identical strings. ``None`` ETA renders as ``unknown`` and an empty
    critical path / untracked requirements render as ``n/a``.
    """
    eta_line = "ETA: unknown" if rollup.eta_iterations is None else f"ETA: {rollup.eta_iterations:g} iterations"

    critical_path = " -> ".join(rollup.critical_path) if rollup.critical_path else "n/a"

    lines = [
        "# Project Status Rollup",
        "",
        f"Health: {rollup.rag.upper()}",
        f"Progress: {rollup.percent_complete * 100:.1f}% complete",
        (
            f"Tasks: {rollup.completed_tasks}/{rollup.total_tasks} completed, "
            f"{rollup.active_tasks} active, {rollup.terminal_tasks} terminal"
        ),
        eta_line,
        f"Remaining effort: {rollup.remaining_effort:g} units",
        f"Makespan: {rollup.makespan:g} units",
        f"Critical path: {critical_path}",
        (f"Open RAID: {rollup.open_risks} open, {rollup.open_critical_or_blocker} critical/blocker"),
        (
            f"Requirements coverage: "
            f"{_format_ratio_percent(rollup.requirements_coverage)} "
            f"({rollup.requirements_covered}/{rollup.requirements_total})"
        ),
        f"Generated at: {rollup.generated_at or 'n/a'}",
        "",
    ]
    return "\n".join(lines)
