"""Gated live-wiring glue that lets the PM (尚书令) WSJF prioritizer affect dispatch order.

The ``orchestration.pm_planning`` cell already ships three landed-but-unwired
capability cores: the CPM scheduler (:mod:`.dependency_validator`), the WSJF
scorer (:mod:`.wsjf`), and the RAID register (:mod:`.raid_register`). This module
is the single seam that connects them to the live planning path, so the PM can
finalize task order by *Weighted Shortest Job First* economics rather than the
bare ordinal ``priority`` integer.

It is **default-OFF** behind the ``KERNELONE_PM_WSJF_ORDER`` environment gate and
**floor-safe by construction**: with the gate off (the default, and the state
during any L2-floor bench run), :func:`reorder_tasks_by_wsjf` returns the input
list object *unchanged* -- not a copy -- so ``run_pm_planning_iteration`` is
byte-for-byte identical to its pre-wiring behavior. The gate exists only so the
WSJF reorder can be flipped on later in a controlled lane; landing it off is
provably behavior-neutral.

Boundary discipline (§7): this module imports ONLY stdlib plus the three
same-cell sibling internals. It does NOT import ``polaris.delivery`` or
``chief_engineer`` and therefore adds no new cross-cell dependency edge.

§8 discipline: every term here is generic PM/WSJF/RAID vocabulary; there are no
project/business/domain literals.
"""

from __future__ import annotations

import os
from typing import Any

from .dependency_validator import DependencyCycleError, compute_schedule
from .raid_register import RaidRegister
from .wsjf import score_tasks_wsjf

# Distinct from raid_register's KERNELONE_PM_RISK_GATE so the WSJF reorder and the
# RAID register gate flip independently in the controlled (codex) lane.
_WSJF_ORDER_GATE_ENV: str = "KERNELONE_PM_WSJF_ORDER"

# Local literal mirroring raid_register._TRUE_TOKENS byte-for-byte. The spec says
# "mirror", not "share": the value set is a trivial literal, and keeping it local
# avoids a fragile cross-module underscore-private dependency.
_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})

__all__ = ["reorder_tasks_by_wsjf"]


def _wsjf_order_enabled(value: str | None = None) -> bool:
    """Return whether the WSJF dispatch-order gate is enabled (default OFF).

    With no explicit ``value`` the gate is read from the
    ``KERNELONE_PM_WSJF_ORDER`` environment variable, so the wiring call site can
    simply call ``_wsjf_order_enabled()`` and inherit the OFF-by-default guard
    from this single place. An explicit ``value`` overrides the environment (used
    by tests). Mirrors ``raid_register._risk_gate_enabled`` exactly.

    Returns ``True`` only for the recognized truthy tokens (case/whitespace
    insensitive); every other value -- including unset/``None``, ``""``, ``"0"``,
    ``"false"``, ``"off"`` and unrecognized tokens -- returns ``False``.
    Fail-closed: ambiguity resolves to OFF (the current live behavior).
    """
    token = value if value is not None else os.environ.get(_WSJF_ORDER_GATE_ENV)
    return str(token or "").strip().lower() in _TRUE_TOKENS


def reorder_tasks_by_wsjf(
    tasks: list[dict[str, Any]],
    workspace: str,
    *,
    current_iteration: int = 0,
) -> list[dict[str, Any]]:
    """Reorder PM contract tasks highest-WSJF-first, behind the default-OFF gate.

    GATE OFF (default): returns the SAME ``tasks`` list object, unchanged. This is
    the floor-safety guarantee -- the added call is a pure identity rebind, so
    ``run_pm_planning_iteration`` stays byte-for-byte identical to HEAD. The off
    path reads exactly one environment variable and returns; it constructs no
    :class:`RaidRegister` (no ``runtime/pm/raid`` mkdir effect), computes no
    schedule, opens no file, and cannot raise.

    GATE ON: computes the CPM schedule, folds in per-task open RAID
    critical/blocker pressure, scores every task by WSJF
    (``cost_of_delay / job_size``), and returns a NEW list -- a stable permutation
    of the SAME dict objects -- ordered highest-WSJF-first. The reorder is:

    * **Total/permutation**: every input dict appears exactly once; none added,
      dropped, or duplicated.
    * **No-mutation**: no task dict is written; the original position lives in a
      side dict keyed by ``id(task)``.
    * **Stable**: equal-rank tasks (including every unscored / empty-id task,
      which all share the sentinel rank) keep their input relative order.
    * **Conservative**: unscored / empty-id / not-in-ranking tasks land at the
      END (after all scored tasks), in input order -- they carry no WSJF signal,
      so they never leapfrog a scored task.

    DEFENSIVE: the entire gate-on body is wrapped so any reachable failure
    degrades to a no-op (``return tasks`` unchanged) and NEVER raises into the
    planning pipeline.

    Args:
        tasks: PM contract tasks (each a dict with ``id``/``priority``/
            ``estimated_effort``/``value``/``depends_on``). A non-list input is
            returned unchanged regardless of gate state.
        workspace: Workspace root used to read the RAID register (gate-on only).
        current_iteration: Reserved for a future iteration-aware policy
            (accepted but unused this increment); documented so it is not
            mistaken for dead code.

    Returns:
        Gate-off: the exact input list object. Gate-on: a new list containing the
        same dict objects in highest-WSJF-first order.
    """
    # Argument-shape guard runs BEFORE any env read: a malformed input degrades to
    # a no-op for both gate states.
    if not isinstance(tasks, list):
        return tasks

    # Gate-off fast path: return the SAME object (not list(tasks)/tasks[:]). This
    # is the byte-identity guarantee and the ``result is tasks`` unit test.
    if not _wsjf_order_enabled():
        return tasks

    del current_iteration  # Reserved for a future iteration-aware policy.

    try:
        schedule = compute_schedule(tasks)

        # Construct the register ONCE with the directory side effect suppressed:
        # summarize() is read-only, so the read-only gate-on path needs no mkdir.
        register = RaidRegister(workspace, ensure_directory=False)

        # Summarize RAID pressure once per DISTINCT non-empty normalized id. The id
        # normalization is byte-identical to score_tasks_wsjf's
        # (str(task.get("id") or "").strip(), wsjf.py) so a task's lookup id always
        # matches its WsjfScore.task_id; if wsjf's normalization ever changes, this
        # must track it.
        seen: set[str] = set()
        raid_pressure_by_task: dict[str, int] = {}
        for task in tasks:
            tid = str(task.get("id") or "").strip()
            if tid and tid not in seen:
                seen.add(tid)
                raid_pressure_by_task[tid] = int(register.summarize(task_id=tid)["open_critical_or_blocker"])

        scores = score_tasks_wsjf(tasks, schedule, raid_pressure_by_task=raid_pressure_by_task)

        # Rank-by-normalized-id map; first (highest-ranked) occurrence wins on a
        # duplicate id. NOT a positional zip of scores<->tasks: score_tasks_wsjf
        # re-sorts by (-wsjf, job_size, task_id), so positions do not correspond.
        rank: dict[str, int] = {}
        for index, score in enumerate(scores):
            rank.setdefault(score.task_id, index)

        # Original positional index for a stable, deterministic secondary key. Kept
        # in a side dict keyed by id(task) so no task dict is ever mutated.
        original_index = {id(task): index for index, task in enumerate(tasks)}

        # Sentinel rank is strictly greater than every real rank (0..len-1), so
        # unscored / empty-id tasks sort to the END in input order.
        sentinel = len(tasks)

        return sorted(
            tasks,
            key=lambda task: (
                rank.get(str(task.get("id") or "").strip(), sentinel),
                original_index[id(task)],
            ),
        )
    except (DependencyCycleError, KeyError, TypeError, ValueError, OSError):
        # Fail-closed to a no-op so the reorder can NEVER raise into planning.
        # DependencyCycleError: cyclic depends_on from compute_schedule (a
        #   ValueError subclass, listed explicitly for clarity).
        # KeyError / TypeError: malformed task dicts feeding score_tasks_wsjf or
        #   the id/rank lookups.
        # ValueError: broad numeric-coercion parent (subsumes DependencyCycleError).
        # OSError: RaidRegister disk/JSON I/O while summarizing.
        return tasks
