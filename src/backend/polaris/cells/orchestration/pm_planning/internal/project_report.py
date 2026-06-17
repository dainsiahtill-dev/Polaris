"""Composed PM (尚书令) **project status report** for ``orchestration.pm_planning``.

A real project manager does not publish five disconnected views -- a schedule,
a RAID log, a status rollup, a WSJF backlog and a milestone tracker -- as
separate artefacts. They publish ONE *project status report*: a single document
that folds health, progress, ETA, the critical path, the top priorities, the
open RAID and the milestone commitments into a coherent picture. This module is
that composer.

The five pure capability cores already live in this cell's ``internal/`` and
were wired to nothing. This module reuses them (§7) and produces the
PM-publishable artefact:

* :func:`compose_project_report` -- a PURE, deterministic, clock-free composer
  that folds plain-data inputs (status counts, a RAID summary, a
  :class:`~.dependency_validator.Schedule`, pre-ranked WSJF scores, milestone
  records + summary) into a frozen :class:`ProjectReport`. It NEVER raises.
* :func:`render_project_markdown` -- a byte-stable Markdown renderer over a
  :class:`ProjectReport` (Health/Progress/ETA/Critical path/Top priorities/Open
  RAID/Milestones sections). Pure: output depends ONLY on the report.
* :func:`build_pm_project_report` -- the single GATHER+WRITE glue entry. It is
  the ONLY place with I/O: it reads the live PM task contract and the sibling
  RAID/milestone stores, computes the schedule/status/WSJF, calls the pure
  composer, and writes ``pm.status.md`` + ``pm.status.json`` atomically under
  ``runtime/pm``. It is DEFENSIVE/fail-closed: any gathering error degrades to a
  partial/empty report and it MUST NOT raise into the caller.

Design discipline (mirrors the sibling ``status_rollup`` module):

* The pure core is clock-free and env-free; the one clock seam (the
  caller-supplied ``generated_at``) and all I/O live ONLY in
  :func:`build_pm_project_report`.
* §8-clean: every constant/ordering tuple is generic meta-tooling with zero
  project/business/domain meaning.
* The cell may NOT import ``polaris.delivery``: the PM task contract is read
  DIRECTLY (``resolve_logical_path`` + ``json``), never via the delivery-layer
  loader. The only non-stdlib, non-sibling import is ``resolve_logical_path``
  from ``polaris.kernelone.storage`` (the declared ``storage.layout`` edge,
  already used by the sibling RAID and milestone registers), so this adds NO new
  cross-cell edge.
* This increment is purely additive: nothing on the live planning/dispatch
  decision path calls :func:`build_pm_project_report`, so the from-scratch +
  repair regression floor is provably untouched.

Residual coupling (documented, not a blocker): :func:`render_project_markdown`
does NOT call ``render_status_markdown``. The report stores a rollup *dict*, not
a frozen :class:`~.status_rollup.PmStatusRollup`; reconstructing the dataclass
purely to render is fragile, so the Health/Progress block is INLINED with the
same fixed format strings ``render_status_markdown`` uses. If that renderer's
format ever changes, this Health block must be updated in lockstep.

Status-vocabulary note: the on-disk PM task contract uses the status enum
``{todo, in_progress, review, done, failed, blocked}`` while the status rollup
keys on the TaskBoard vocabulary (success terminal ``"completed"``). The single
shape mismatch is adapted in :func:`_build_status_counts` here (``done`` ->
``completed``; ``failed`` is already terminal and passes through; every other
status passes through as its own active key), NOT in any core.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polaris.kernelone.storage import resolve_logical_path

from .dependency_validator import DependencyCycleError, Schedule, compute_schedule
from .milestones import MilestoneRecordV1, MilestoneRegister, classify_milestone_health
from .raid_register import RaidRegister
from .status_rollup import compute_status_rollup
from .wsjf import WsjfScore, score_tasks_wsjf

__all__ = [
    "ProjectReport",
    "build_pm_project_report",
    "compose_project_report",
    "render_project_markdown",
]


# --------------------------------------------------------------------------------------
# Generic documented constants (zero domain meaning, §8-clean).
# --------------------------------------------------------------------------------------

#: Default number of top-ranked WSJF entries surfaced in the report backlog.
_DEFAULT_TOP_N: int = 10

#: Canonical render order for RAID severity breakdowns. Hard-coded so byte
#: stability is independent of dict iteration order / ``PYTHONHASHSEED``.
_RAID_SEVERITY_ORDER: tuple[str, ...] = ("low", "medium", "high", "critical", "blocker")
#: Canonical render order for RAID status breakdowns (same rationale).
_RAID_STATUS_ORDER: tuple[str, ...] = ("open", "mitigating", "accepted", "resolved", "reverted")
#: Canonical render order for milestone status breakdowns (same rationale).
_MILESTONE_STATUS_ORDER: tuple[str, ...] = ("planned", "in_progress", "achieved", "missed", "cancelled")

#: Fixed float formatting for WSJF columns; mirrors ``wsjf._MARKDOWN_FLOAT_FMT``
#: semantics but is defined locally to avoid importing a private sibling symbol.
_WSJF_FLOAT_FMT: str = "{:.3f}"

#: The PM task contract logical path. Grounded from
#: ``delivery/cli/pm/config.py::CANONICAL_PM_TASKS_REL`` and duplicated here as a
#: bare logical-path string so the cell does not import the delivery layer.
_PM_TASKS_CONTRACT_REL: str = "runtime/contracts/pm_tasks.contract.json"

#: The success-terminal status in the rollup vocabulary.
_ROLLUP_COMPLETED_STATUS: str = "completed"

#: Adapter from the on-disk contract status enum to the rollup vocabulary. Only
#: ``done`` needs remapping (-> ``completed``); ``failed`` is already a rollup
#: terminal status and passes through; every other status passes through as its
#: own active key (fail-closed: an unknown status is counted as active, never
#: silently dropped or counted as success).
_CONTRACT_STATUS_TO_ROLLUP: dict[str, str] = {"done": _ROLLUP_COMPLETED_STATUS}


# --------------------------------------------------------------------------------------
# Result type.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectReport:
    """The composed, PM-publishable project status view (immutable, JSON-safe).

    Top-line fields (``health``/``percent_complete``/``eta_iterations``/
    ``makespan``/``critical_path``/``open_critical_or_blocker``) are lifted
    VERBATIM from the rollup so the report has a single source of truth and two
    derivations can never drift. The frozen dataclass makes a composed snapshot
    immutable and gives structural equality for byte-stable round-trips.
    """

    generated_at: str = ""
    current_iteration: int = 0
    rollup: dict[str, Any] = field(default_factory=dict)
    health: str = ""
    percent_complete: float = 0.0
    eta_iterations: float | None = None
    makespan: float = 0.0
    critical_path: tuple[str, ...] = ()
    top_wsjf: tuple[dict[str, Any], ...] = ()
    wsjf_total_scored: int = 0
    raid: dict[str, Any] = field(default_factory=dict)
    open_critical_or_blocker: int = 0
    milestones: dict[str, Any] = field(default_factory=dict)
    milestone_health: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict.

        Tuples become lists and ``eta_iterations`` preserves ``None`` honestly
        (``None`` = unknown horizon, never silently ``0.0``). The result is safe
        to feed straight to :func:`json.dumps`.
        """
        return {
            "generated_at": self.generated_at,
            "current_iteration": self.current_iteration,
            "rollup": dict(self.rollup),
            "health": self.health,
            "percent_complete": self.percent_complete,
            "eta_iterations": self.eta_iterations,
            "makespan": self.makespan,
            "critical_path": list(self.critical_path),
            "top_wsjf": [dict(entry) for entry in self.top_wsjf],
            "wsjf_total_scored": self.wsjf_total_scored,
            "raid": dict(self.raid),
            "open_critical_or_blocker": self.open_critical_or_blocker,
            "milestones": dict(self.milestones),
            "milestone_health": [dict(entry) for entry in self.milestone_health],
        }


# --------------------------------------------------------------------------------------
# Pure composer (clock-free, env-free, never raises).
# --------------------------------------------------------------------------------------


def _coerce_iteration(current_iteration: int) -> int:
    """Return a genuine non-``bool`` ``int`` iteration, fail-closed to ``0``."""
    if isinstance(current_iteration, bool):
        return 0
    if isinstance(current_iteration, int):
        return current_iteration
    return 0


def compose_project_report(
    *,
    status_counts: Mapping[str, int],
    raid_summary: Mapping[str, Any],
    schedule: Schedule,
    wsjf_scores: Sequence[WsjfScore],
    milestone_records: Sequence[MilestoneRecordV1],
    milestone_summary: Mapping[str, Any],
    current_iteration: int = 0,
    requirements_total: int | None = None,
    requirements_covered: int | None = None,
    requirements_coverage: float | None = None,
    velocity: float | None = None,
    completed_ids: Iterable[str] | None = None,
    top_n: int = _DEFAULT_TOP_N,
    generated_at: str = "",
) -> ProjectReport:
    """Fold plain-data inputs into a frozen :class:`ProjectReport` (PURE).

    Deterministic, clock-free, §8-clean and total: it reuses the five cores
    (§7) without re-deriving any of their logic and NEVER raises. The caller
    stamps ``generated_at`` (default ``""`` keeps the composer clock-free).

    Args:
        status_counts: Aggregate rollup-vocabulary ``status -> count`` mapping
            (the gather half adapts the contract status enum first).
        raid_summary: The dict from :meth:`RaidRegister.summarize` (stored
            verbatim and folded into the rollup).
        schedule: A :class:`Schedule` from :func:`compute_schedule` (§7 reuse).
        wsjf_scores: The PRE-RANKED list from :func:`score_tasks_wsjf`. The
            composer takes the already-ranked list (not a raw task list) so it
            never re-derives the WSJF ranking or the RAID-pressure projection
            (both gather-half concerns). Only the top ``top_n`` are surfaced.
        milestone_records: The records from :meth:`MilestoneRegister.list`
            (already ``milestone_id``-sorted, so the per-record health order is
            deterministic).
        milestone_summary: The dict from :meth:`MilestoneRegister.summarize`
            (stored verbatim).
        current_iteration: The caller-owned iteration clock; threaded into
            :func:`classify_milestone_health` and rendered. ``int``-guarded.
        requirements_total, requirements_covered, requirements_coverage:
            Optional requirements-coverage signals (pass through to the rollup;
            ``None`` renders honestly as untracked).
        velocity: Throughput per iteration; only a finite ``> 0`` value yields a
            non-``None`` ``eta_iterations`` (the rollup enforces this).
        completed_ids: Optional exact completed-id set for exact remaining
            effort (forwarded to the rollup).
        top_n: How many top-ranked WSJF entries to surface (default
            :data:`_DEFAULT_TOP_N`).
        generated_at: Caller-stamped ISO timestamp, stored verbatim.

    Returns:
        A frozen :class:`ProjectReport`.
    """
    iteration = _coerce_iteration(current_iteration)

    rollup = compute_status_rollup(
        status_counts=status_counts,
        raid_summary=raid_summary,
        schedule=schedule,
        requirements_total=requirements_total,
        requirements_covered=requirements_covered,
        requirements_coverage=requirements_coverage,
        velocity=velocity,
        completed_ids=completed_ids,
        generated_at=generated_at,
    )
    rollup_dict = rollup.to_dict()

    score_list = list(wsjf_scores)
    bounded_top_n = max(0, top_n)
    top = tuple(score.to_dict() for score in score_list[:bounded_top_n])
    wsjf_total_scored = len(score_list)

    milestone_health = tuple(
        {
            "milestone_id": record.milestone_id,
            "name": record.name,
            "status": record.status.value,
            "target_iteration": record.target_iteration,
            "band": classify_milestone_health(record, current_iteration=iteration, schedule=schedule),
        }
        for record in milestone_records
    )

    # Lift top-line fields FROM THE ROLLUP (single source of truth -- never
    # re-derive from the schedule/raid directly, so two derivations can't drift).
    health = str(rollup.rag).upper()

    return ProjectReport(
        generated_at=generated_at,
        current_iteration=iteration,
        rollup=rollup_dict,
        health=health,
        percent_complete=rollup.percent_complete,
        eta_iterations=rollup.eta_iterations,
        makespan=rollup.makespan,
        critical_path=tuple(rollup.critical_path),
        top_wsjf=top,
        wsjf_total_scored=wsjf_total_scored,
        raid=dict(raid_summary),
        open_critical_or_blocker=rollup.open_critical_or_blocker,
        milestones=dict(milestone_summary),
        milestone_health=milestone_health,
    )


# --------------------------------------------------------------------------------------
# Pure renderer (byte-stable; depends ONLY on the report).
# --------------------------------------------------------------------------------------


def _render_health_block(report: ProjectReport) -> list[str]:
    """Render the Health & Progress section (INLINED status-rollup format)."""
    eta_line = "ETA: unknown" if report.eta_iterations is None else f"ETA: {report.eta_iterations:g} iterations"
    critical_path = " -> ".join(report.critical_path) if report.critical_path else "n/a"
    return [
        "## Health & Progress",
        f"Health: {report.health}",
        f"Progress: {report.percent_complete * 100:.1f}% complete",
        eta_line,
        f"Makespan: {report.makespan:g} units",
        f"Critical path: {critical_path}",
        f"Open critical/blocker RAID: {report.open_critical_or_blocker}",
        "",
    ]


def _render_wsjf_block(report: ProjectReport) -> list[str]:
    """Render the Top Priorities (WSJF) section as a fixed Markdown table."""
    lines = [
        "## Top Priorities (WSJF)",
        f"Top {len(report.top_wsjf)} of {report.wsjf_total_scored} scored",
    ]
    if not report.top_wsjf:
        lines.append("n/a")
        lines.append("")
        return lines
    lines.append("| Rank | Task | WSJF | CoD | Job Size | Critical Path | RAID |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for rank, entry in enumerate(report.top_wsjf, start=1):
        on_path = "yes" if entry.get("on_critical_path") else "no"
        lines.append(
            "| {rank} | {task} | {wsjf} | {cod} | {job} | {path} | {raid} |".format(
                rank=rank,
                task=entry.get("task_id", ""),
                wsjf=_WSJF_FLOAT_FMT.format(_as_float(entry.get("wsjf"))),
                cod=_WSJF_FLOAT_FMT.format(_as_float(entry.get("cost_of_delay"))),
                job=_WSJF_FLOAT_FMT.format(_as_float(entry.get("job_size"))),
                path=on_path,
                raid=_as_int(entry.get("raid_pressure")),
            )
        )
    lines.append("")
    return lines


def _render_raid_block(report: ProjectReport) -> list[str]:
    """Render the Open RAID section, iterating the canonical order tuples."""
    raid = report.raid
    by_severity = raid.get("by_severity")
    by_status = raid.get("by_status")
    severity_map: Mapping[str, Any] = by_severity if isinstance(by_severity, Mapping) else {}
    status_map: Mapping[str, Any] = by_status if isinstance(by_status, Mapping) else {}
    lines = [
        "## Open RAID",
        f"Total: {_as_int(raid.get('total', 0))}",
        f"Open critical/blocker: {_as_int(raid.get('open_critical_or_blocker', 0))}",
        "By severity:",
    ]
    for severity in _RAID_SEVERITY_ORDER:
        lines.append(f"- {severity}: {_as_int(severity_map.get(severity, 0))}")
    lines.append("By status:")
    for status in _RAID_STATUS_ORDER:
        lines.append(f"- {status}: {_as_int(status_map.get(status, 0))}")
    lines.append("")
    return lines


def _render_milestone_block(report: ProjectReport) -> list[str]:
    """Render the Milestones section (summary + per-milestone health table)."""
    milestones = report.milestones
    by_status = milestones.get("by_status")
    status_map: Mapping[str, Any] = by_status if isinstance(by_status, Mapping) else {}
    next_upcoming = milestones.get("next_upcoming")
    if isinstance(next_upcoming, Mapping):
        next_line = f"{next_upcoming.get('milestone_id', 'n/a')}@it{next_upcoming.get('target_iteration', 'n/a')}"
    else:
        next_line = "n/a"
    lines = [
        "## Milestones",
        f"Total: {_as_int(milestones.get('total', 0))}",
        f"Overdue: {_as_int(milestones.get('overdue', 0))}",
        f"Next upcoming: {next_line}",
        "By status:",
    ]
    for status in _MILESTONE_STATUS_ORDER:
        lines.append(f"- {status}: {_as_int(status_map.get(status, 0))}")
    lines.append("| Milestone | Name | Status | Target It | Health Band |")
    lines.append("| --- | --- | --- | --- | --- |")
    for entry in report.milestone_health:
        target = entry.get("target_iteration")
        target_text = "n/a" if target is None else str(target)
        lines.append(
            "| {mid} | {name} | {status} | {target} | {band} |".format(
                mid=entry.get("milestone_id", ""),
                name=entry.get("name", ""),
                status=entry.get("status", ""),
                target=target_text,
                band=entry.get("band", ""),
            )
        )
    lines.append("")
    return lines


def render_project_markdown(report: ProjectReport) -> str:
    """Render a :class:`ProjectReport` to a deterministic Markdown document.

    Byte-stable: the output depends ONLY on ``report`` (no clock, no env, no
    dict-iteration nondeterminism), so two calls on the same report return
    identical strings. RAID/milestone breakdowns iterate the hard-coded
    canonical order tuples, never dict order, so the output is independent of
    ``PYTHONHASHSEED``. ``None`` renders as fixed sentinels (``unknown`` ETA,
    ``n/a`` empty critical path / empty WSJF / missing next-upcoming).
    """
    lines: list[str] = [
        "# PM Project Status Report",
        "",
        f"Generated at: {report.generated_at or 'n/a'}",
        f"Iteration: {report.current_iteration}",
        "",
    ]
    lines.extend(_render_health_block(report))
    lines.extend(_render_wsjf_block(report))
    lines.extend(_render_raid_block(report))
    lines.extend(_render_milestone_block(report))
    return "\n".join(lines)


def _as_float(value: Any) -> float:
    """Fail-closed float read for rendering (``bool``/junk/non-finite -> ``0.0``)."""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return 0.0
        return number
    return 0.0


def _as_int(value: Any) -> int:
    """Fail-closed int read for rendering (``bool``/junk -> ``0``)."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == value and value not in (float("inf"), float("-inf")):
        return int(value)
    return 0


# --------------------------------------------------------------------------------------
# Gather half: the ONLY place with I/O / sibling-register construction / a clock seam.
# --------------------------------------------------------------------------------------


def _load_pm_tasks(workspace: str) -> list[dict[str, Any]]:
    """Read the PM task contract directly and return its task dicts (fail-closed).

    Reads ``runtime/contracts/pm_tasks.contract.json`` via
    :func:`resolve_logical_path` (NOT the delivery-layer loader, which the cell
    may not import) with explicit UTF-8. Any read/parse error -- a missing file,
    corrupt JSON (the sanitized on-disk sample is literally ``{invalid_json}``),
    a non-dict top level, or a ``tasks`` value that is not a list -- degrades to
    an empty list rather than raising. Non-dict task entries are filtered out so
    the downstream cores (which skip non-dicts) get a clean list.
    """
    try:
        path = resolve_logical_path(workspace, _PM_TASKS_CONTRACT_REL)
    except (OSError, ValueError, TypeError):
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list):
        return []
    return [task for task in raw_tasks if isinstance(task, dict)]


def _build_status_counts(tasks: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Tally contract task statuses into the rollup vocabulary (status adapter).

    The on-disk contract status enum is ``{todo, in_progress, review, done,
    failed, blocked}`` whereas the status rollup keys on the TaskBoard
    vocabulary (success terminal ``"completed"``). The single shape mismatch is
    adapted HERE (not in any core) via :data:`_CONTRACT_STATUS_TO_ROLLUP`:
    ``done`` -> ``completed``; ``failed`` is already a rollup terminal status and
    passes through; every other status passes through as its own active key.
    Empty/blank statuses are dropped so they do not inflate the total.
    """
    counts: dict[str, int] = {}
    for task in tasks:
        raw = str(task.get("status") or "").strip().lower()
        if not raw:
            continue
        mapped = _CONTRACT_STATUS_TO_ROLLUP.get(raw, raw)
        counts[mapped] = counts.get(mapped, 0) + 1
    return counts


def _task_ids(tasks: Sequence[dict[str, Any]]) -> list[str]:
    """Return the non-empty, stripped task ids in contract order (deduplicated)."""
    ids: list[str] = []
    seen: set[str] = set()
    for task in tasks:
        task_id = str(task.get("id") or "").strip()
        if task_id and task_id not in seen:
            seen.add(task_id)
            ids.append(task_id)
    return ids


def _safe_raid_summary(raid: RaidRegister | None) -> dict[str, Any]:
    """Return ``raid.summarize()`` fail-closed to ``{}`` on any store error."""
    if raid is None:
        return {}
    try:
        return raid.summarize()
    except (OSError, ValueError, TypeError):
        return {}


def _safe_raid_pressure(raid: RaidRegister | None, task_ids: Sequence[str]) -> dict[str, int]:
    """Project per-task open-critical/blocker RAID pressure (fail-closed per id).

    Mirrors the deferred-glue contract in :func:`score_tasks_wsjf`: each id maps
    to ``raid.summarize(task_id=id)["open_critical_or_blocker"]``, with any
    per-id store error or missing key degrading to ``0`` so a junk store never
    breaks the scorer.
    """
    pressure: dict[str, int] = {}
    if raid is None:
        return pressure
    for task_id in task_ids:
        try:
            summary = raid.summarize(task_id=task_id)
        except (OSError, ValueError, TypeError):
            pressure[task_id] = 0
            continue
        value = summary.get("open_critical_or_blocker", 0)
        pressure[task_id] = value if isinstance(value, int) and not isinstance(value, bool) else 0
    return pressure


def _safe_milestone_records(register: MilestoneRegister | None) -> list[MilestoneRecordV1]:
    """Return ``register.list()`` fail-closed to ``[]`` on any store error."""
    if register is None:
        return []
    try:
        return register.list()
    except (OSError, ValueError, TypeError):
        return []


def _safe_milestone_summary(register: MilestoneRegister | None, current_iteration: int) -> dict[str, Any]:
    """Return ``register.summarize(...)`` fail-closed to ``{}`` on any store error."""
    if register is None:
        return {}
    try:
        return register.summarize(current_iteration=current_iteration)
    except (OSError, ValueError, TypeError):
        return {}


def _write_text_atomic(path: Path, text: str) -> None:
    """Atomically write UTF-8 ``text`` to ``path`` (temp companion + replace).

    The temp companion is ``{name}.tmp`` (string-concat, not ``with_suffix``)
    because ``.`` is a legal name char -- mirrors the sibling RAID/milestone
    ``_save`` idiom. ``os.replace`` makes the swap atomic so a reader never sees
    a partial file.
    """
    tmp = path.parent / (path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def build_pm_project_report(
    workspace: str,
    *,
    current_iteration: int = 0,
    generated_at: str = "",
) -> dict[str, Any]:
    """Gather the live PM stores, compose the report, write it, return a summary.

    This is the single GATHER+WRITE entry. It is DEFENSIVE/fail-closed: any
    gathering or write error degrades to a partial/empty report and it MUST NOT
    raise into the caller. There is no bare ``except`` -- every failure mode is
    caught by a targeted exception type.

    Gather order:
      1. Read the PM task contract directly (``_load_pm_tasks``; fail-closed).
      2. Adapt the contract status enum into the rollup vocabulary
         (``_build_status_counts``).
      3. Construct the sibling RAID + milestone registers (same cell, same
         ``resolve_logical_path`` idiom). Construction failure degrades the store
         to ``None`` (treated as empty).
      4. Compute the CPM schedule; a :class:`DependencyCycleError` degrades to an
         empty schedule and marks the report degraded.
      5. Summarize RAID + project per-task RAID pressure (guarded).
      6. Score the backlog by WSJF (§7 reuse) against the schedule.
      7. List milestone records + summarize (guarded).
      8. Call the pure :func:`compose_project_report`.
      9. Write ``pm.status.md`` + ``pm.status.json`` atomically under
         ``runtime/pm`` (consistent with the ``runtime/pm/raid`` +
         ``runtime/pm/milestones`` sibling layout). A write error returns a
         summary with ``ok=False`` rather than raising.

    Args:
        workspace: The workspace root (resolved via ``resolve_logical_path``).
        current_iteration: The iteration clock; threaded into the milestone
            health bands and the milestone summary so the ``overdue`` count and
            the per-record bands agree.
        generated_at: Caller-stamped ISO timestamp (the caller owns the clock).

    Returns:
        A JSON-safe summary dict with the report's headline figures, the written
        artefact paths, an ``ok`` flag and a ``degraded`` flag.
    """
    degraded = False

    tasks = _load_pm_tasks(workspace)
    status_counts = _build_status_counts(tasks)

    raid: RaidRegister | None
    try:
        raid = RaidRegister(workspace)
    except (OSError, ValueError, TypeError):
        raid = None
        degraded = True

    milestone_register: MilestoneRegister | None
    try:
        milestone_register = MilestoneRegister(workspace)
    except (OSError, ValueError, TypeError):
        milestone_register = None
        degraded = True

    try:
        schedule = compute_schedule(tasks)
    except DependencyCycleError:
        schedule = compute_schedule([])
        degraded = True

    raid_summary = _safe_raid_summary(raid)
    raid_pressure_by_task = _safe_raid_pressure(raid, _task_ids(tasks))

    scores = score_tasks_wsjf(tasks, schedule, raid_pressure_by_task=raid_pressure_by_task)

    milestone_records = _safe_milestone_records(milestone_register)
    milestone_summary = _safe_milestone_summary(milestone_register, current_iteration)

    report = compose_project_report(
        status_counts=status_counts,
        raid_summary=raid_summary,
        schedule=schedule,
        wsjf_scores=scores,
        milestone_records=milestone_records,
        milestone_summary=milestone_summary,
        current_iteration=current_iteration,
        velocity=None,
        generated_at=generated_at,
    )

    out_dir = Path(resolve_logical_path(workspace, "runtime/pm"))
    md_path = out_dir / "pm.status.md"
    json_path = out_dir / "pm.status.json"
    ok = True
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_text_atomic(md_path, render_project_markdown(report))
        _write_text_atomic(json_path, json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    except OSError:
        ok = False
        degraded = True

    rollup = report.rollup
    return {
        "ok": ok,
        "rag": report.health,
        "percent_complete": report.percent_complete,
        "makespan": report.makespan,
        "open_critical_or_blocker": report.open_critical_or_blocker,
        "total_tasks": rollup.get("total_tasks", 0),
        "wsjf_total_scored": report.wsjf_total_scored,
        "milestones_total": report.milestones.get("total", 0),
        "md_path": str(md_path),
        "json_path": str(json_path),
        "degraded": degraded,
        "generated_at": generated_at,
    }
