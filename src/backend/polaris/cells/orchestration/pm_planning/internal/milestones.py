"""Milestones registry for the PM (尚书令) planning cell.

A milestone is a standard project-management commitment: a named, governed
checkpoint the plan is steered toward and reported against. Real project
managers track milestones as first-class records with their own status
lifecycle and a schedule-aware health view (on track / at risk / overdue) so a
slipping commitment surfaces before it silently misses.

This module is the missing durable store behind the profession declaration
``requires_milestones: true`` (today only a bare ``list[str]`` of names and
prompt hints exist, with NO enforcing store). It provides:

* :class:`MilestoneRecordV1` -- one immutable milestone record (+ ``to_dict``).
* :class:`MilestoneStatus` -- the lifecycle ladder.
* :func:`classify_milestone_health` -- a PURE, I/O-free health classifier that
  maps a record + a caller-supplied current iteration (+ an optional schedule)
  to a generic health band; it never raises.
* :class:`MilestoneRegister` -- a workspace-scoped store, one JSON file per
  record under ``runtime/pm/milestones/{milestone_id}.json``.

Milestones are anchored by a director **iteration index** (an ``int``), NOT a
calendar date, so the model stays deterministic and project-agnostic: Polaris
plans in director iterations and the schedule is expressed in effort/iteration
units, never wall-clock time.

The store mirrors the sibling RAID register's storage shape exactly:
KernelFileSystem-backed atomic writes, traversal-guarded ids, fail-closed loads
(one corrupt file degrades exactly one record), and explicit UTF-8 on every
read/write. It is pure/deterministic modulo the clock/nonce seams, 100% generic
meta-tooling -- no project/business/domain literals -- and imports NOTHING from
``chief_engineer`` and NOTHING from ``polaris.delivery``. The only sibling
import is :class:`~.dependency_validator.Schedule`, used solely inside the pure
classifier to refine ``at_risk`` from critical-path membership; the persistence
layer imports no scheduling.

This increment is purely additive: nothing here is wired into the live planning
path. The ``KERNELONE_PM_MILESTONE_GATE`` predicate ships unit-tested but is
called by nothing, exactly like the RAID register's risk gate.

Deferred integration (DESIGN ONLY this increment -- built nowhere outside this
file and its test, both behind the default-off gate):

(a) Architect-schema ingestion. A later wiring increment would read the
    architect's declared milestone names (a free-form ``list[str]`` field on the
    design schema) and call :meth:`MilestoneRegister.register` once per
    not-yet-present name (idempotent on name), all guarded by
    ``if not _milestone_gate_enabled(): return``. That closes the gap behind the
    profession's ``requires_milestones`` declaration, which today has no
    enforcing store. The ingestion would live in the future wiring layer, never
    in this module.

(b) Status-rollup fold. Behind the SAME default-off gate, a later increment
    would call :meth:`MilestoneRegister.summarize` and fold the ``overdue`` /
    ``next_upcoming`` tally (and optionally an at-risk count) into a new optional
    field on the PM status rollup, defaulting to neutral when the gate is OFF.
    The rollup module is NOT edited this increment.

Both deferred paths stay off by default, so the from-scratch + repair regression
floor is provably untouched.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.security.record_id_guard import validate_storage_record_id
from polaris.kernelone.storage import resolve_logical_path

from .dependency_validator import Schedule

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")

_MILESTONE_GATE_ENV = "KERNELONE_PM_MILESTONE_GATE"
_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})

# The ONLY health threshold. Generic, documented, with ZERO domain meaning: a
# non-terminal milestone whose target iteration is now (delta 0) or within this
# many iterations of "now" (delta 1), with work remaining, classifies as
# ``at_risk``. Anything further out (delta >= 2) reads ``on_track``.
_AT_RISK_HORIZON = 1


class MilestoneStatus(str, Enum):
    """The lifecycle state of a milestone.

    ``PLANNED`` and ``IN_PROGRESS`` are the active (non-terminal) states.
    ``ACHIEVED``, ``MISSED`` and ``CANCELLED`` are terminal: once reached, a
    milestone accepts no further transition (only the recorded self-no-op).
    """

    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    ACHIEVED = "achieved"
    MISSED = "missed"
    CANCELLED = "cancelled"


def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string ending in ``Z``."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _nonce() -> str:
    """Build a collision-resistant nonce (microsecond UTC stamp + random suffix)."""
    stamp = _utc_now().replace(":", "").replace("-", "").replace(".", "")
    return f"{stamp}{uuid.uuid4().hex[:8]}"


def _safe_token(value: str) -> str:
    """Sanitize a token to the ``[A-Za-z0-9_.-]`` charset, capped at 80 chars."""
    token = _SAFE_ID_RE.sub("_", str(value or "").strip()).strip("._-")
    return token[:80] or "milestone"


def _validate_record_id(value: str) -> str:
    """Reject ids that could escape the storage directory (path traversal).

    Delegates to the canonical SSoT in
    ``polaris.kernelone.security.record_id_guard``; kept as a thin local
    wrapper so existing call sites and test monkeypatch targets resolve
    unchanged. Fail-closed: anything but a bare safe token raises.
    """
    return validate_storage_record_id(value, label="milestone id")


def _require_non_empty(name: str, value: str) -> None:
    """Raise ``ValueError`` if a string field is empty/whitespace."""
    if not str(value or "").strip():
        raise ValueError(f"milestone record field {name!r} must be non-empty")


def _coerce_status(value: Any) -> MilestoneStatus:
    """Coerce a loaded value to a :class:`MilestoneStatus`, defaulting ``PLANNED``."""
    if isinstance(value, MilestoneStatus):
        return value
    try:
        return MilestoneStatus(str(value or "planned").strip().lower() or "planned")
    except ValueError:
        return MilestoneStatus.PLANNED


def _coerce_task_ids(value: Any) -> tuple[str, ...]:
    """Coerce a loaded value to a tuple of non-empty task-id strings (fail-closed)."""
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return (str(value).strip(),) if str(value).strip() else ()


def _coerce_history(value: Any) -> tuple[dict[str, str], ...]:
    """Coerce a loaded value to a tuple of history events (fail-closed).

    Each event is normalized to ``{at, actor, action, note}`` with string
    values. Non-mapping items are dropped; a bad/absent value yields ``()``.
    """
    if not isinstance(value, (list, tuple)):
        return ()
    events: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        events.append(
            {
                "at": str(item.get("at") or ""),
                "actor": str(item.get("actor") or ""),
                "action": str(item.get("action") or ""),
                "note": str(item.get("note") or ""),
            }
        )
    return tuple(events)


def _coerce_target_iteration(value: Any) -> int | None:
    """Coerce a loaded value to an iteration index ``int`` or ``None`` (fail-closed).

    Accepts a real ``int`` (but not ``bool``, which is an ``int`` subclass) or a
    clean integer string. Anything else -- including ``None``, a float, or a
    non-numeric string -- yields ``None`` (unscheduled), never raising. ``None``
    is preserved honestly and never coerced to ``0``.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        token = value.strip()
        if token and (token.lstrip("-").isdigit()):
            try:
                return int(token)
            except ValueError:
                return None
    return None


def _milestone_gate_enabled(value: str | None = None) -> bool:
    """Return whether the milestone wiring gate is enabled (default OFF).

    When no explicit ``value`` is given the gate reads the
    ``KERNELONE_PM_MILESTONE_GATE`` environment variable, so the future wiring
    layer (architect-schema ingestion and the status-rollup fold) can simply
    call ``_milestone_gate_enabled()`` and inherit OFF-by-default behaviour from
    one place rather than re-deriving the check at each call site (where it could
    accidentally default ON). An explicit ``value`` overrides the environment
    (used by tests).

    Returns ``True`` only for recognized truthy tokens (case/whitespace
    insensitive); any other value -- including unset/``None``, ``""``, ``"0"``,
    ``"false"``, ``"off"`` and unrecognized tokens -- returns ``False``.
    Fail-closed: ambiguity resolves OFF, matching the current live behaviour.
    """
    token = value if value is not None else os.environ.get(_MILESTONE_GATE_ENV)
    return str(token or "").strip().lower() in _TRUE_TOKENS


# The lifecycle table. A transition is legal only if the target appears in the
# current state's allow-set; terminal states allow nothing further. A
# self-transition (X -> X) always bypasses the table and is recorded as an
# idempotent no-op, paralleling the RAID register's re-append behaviour.
_ALLOWED_TRANSITIONS: dict[MilestoneStatus, frozenset[MilestoneStatus]] = {
    MilestoneStatus.PLANNED: frozenset(
        {
            MilestoneStatus.IN_PROGRESS,
            MilestoneStatus.ACHIEVED,
            MilestoneStatus.MISSED,
            MilestoneStatus.CANCELLED,
        }
    ),
    MilestoneStatus.IN_PROGRESS: frozenset(
        {
            MilestoneStatus.ACHIEVED,
            MilestoneStatus.MISSED,
            MilestoneStatus.CANCELLED,
            MilestoneStatus.PLANNED,
        }
    ),
    MilestoneStatus.ACHIEVED: frozenset(),
    MilestoneStatus.MISSED: frozenset(),
    MilestoneStatus.CANCELLED: frozenset(),
}


@dataclass(frozen=True)
class MilestoneRecordV1:
    """A single immutable milestone record.

    Only ``milestone_id`` is hard-required (the storage key must be non-empty so
    a filesystem path can always be built). Every other field tolerates an empty
    or default value so the fail-closed loader can recover a partially-populated
    record rather than discarding the whole entry.

    ``target_iteration`` anchors the commitment to a director iteration index;
    ``None`` means unscheduled (and therefore can never read as overdue). There
    is no calendar/date field anywhere -- the model is iteration-anchored.
    """

    milestone_id: str
    name: str = ""
    description: str = ""
    status: MilestoneStatus = MilestoneStatus.PLANNED
    target_iteration: int | None = None
    task_ids: tuple[str, ...] = field(default_factory=tuple)
    owner: str = ""
    acceptance: str = ""
    created_at: str = ""
    achieved_at: str | None = None
    history: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Validate the filesystem-critical primary-key invariant.

        Only ``milestone_id`` is hard-required: the storage key must be
        non-empty for any path to be built. ``name``/``created_at`` are always
        stamped by :meth:`MilestoneRegister.register`, but are deliberately NOT
        hard-required so the fail-closed loader can still recover a
        partially-populated record (empty name/created_at) rather than discarding
        the whole entry.
        """
        _require_non_empty("milestone_id", self.milestone_id)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict in a fixed key order.

        ``status`` is emitted as its bare string value, ``task_ids`` as a list,
        and ``history`` as a list of dicts. ``target_iteration`` and
        ``achieved_at`` preserve ``None`` honestly (never ``0``/``""``). The key
        order is fixed here (not via ``sort_keys``) so byte output is
        deterministic for a fixed clock/nonce, mirroring the RAID register.
        """
        return {
            "milestone_id": self.milestone_id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "target_iteration": self.target_iteration,
            "task_ids": list(self.task_ids),
            "owner": self.owner,
            "acceptance": self.acceptance,
            "created_at": self.created_at,
            "achieved_at": self.achieved_at,
            "history": [dict(event) for event in self.history],
        }


def classify_milestone_health(
    record: MilestoneRecordV1,
    *,
    current_iteration: int,
    schedule: Schedule | None = None,
) -> str:
    """Classify a milestone's health into a generic band (PURE, no I/O).

    Returns one of ``"achieved"``, ``"cancelled"``, ``"on_track"``,
    ``"at_risk"`` or ``"overdue"``. The function reads no clock, environment or
    dict ordering: ``current_iteration`` is supplied by the caller (the clock
    seam lives in the caller, mirroring the status-rollup discipline), so it is
    deterministic -- identical ``(record, current_iteration, schedule)`` always
    yields the same band. It performs only attribute reads, integer comparisons
    and membership tests, and therefore never raises on a well-typed record.

    Base band (top match wins):

    1. status ``ACHIEVED``  -> ``"achieved"``  (terminal mirror).
    2. status ``CANCELLED`` -> ``"cancelled"`` (terminal mirror; voided, not overdue).
    3. status ``MISSED``    -> ``"overdue"``   (a missed milestone never reads
       green; deliberate reuse of the overdue band -- there is no separate
       ``missed`` band).
    4. ``target_iteration is None`` -> ``"on_track"`` (FAIL-CLOSED: a milestone
       with no target cannot be overdue; checked before any arithmetic so
       ``None`` never reaches a comparison).
    5. ``current_iteration > target_iteration`` -> ``"overdue"`` (strictly past
       due, non-terminal).
    6. ``target_iteration - current_iteration <= _AT_RISK_HORIZON`` -> ``"at_risk"``
       (due now [delta 0] or within the horizon [delta 1]).
    7. otherwise -> ``"on_track"``.

    Optional schedule refinement (applied only when ``schedule is not None``,
    AFTER the base band is computed) -- TIGHTEN-ONLY and monotone-conservative.
    It may only PROMOTE a base ``"on_track"`` to ``"at_risk"``; it never demotes
    any band and never relaxes ``overdue``/``achieved``/``cancelled``/``at_risk``.
    The promotion triggers when the base band is ``"on_track"``, the record has
    at least one linked task id, and at least one linked id is BOTH on
    ``schedule.critical_path`` AND unfinished. With no per-task completion flag
    available, critical-path membership is the unfinished proxy (critical-path
    tasks are slack-zero on the chain that drives the makespan). A linked id that
    the schedule does not place (absent from ``schedule.weights``) is treated as
    out-of-schedule and is never a trigger -- absence is no-signal, never
    inferred as finished, so nothing is fabricated.
    """
    status = record.status
    if status is MilestoneStatus.ACHIEVED:
        return "achieved"
    if status is MilestoneStatus.CANCELLED:
        return "cancelled"
    if status is MilestoneStatus.MISSED:
        return "overdue"

    target = record.target_iteration
    # Fail-closed defensive parity: only a genuine int target reaches arithmetic;
    # anything else (None or an unexpected type) cannot be overdue -> on_track.
    if not isinstance(target, int) or isinstance(target, bool):
        return "on_track"

    if current_iteration > target:
        return "overdue"
    if target - current_iteration <= _AT_RISK_HORIZON:
        return "at_risk"

    band = "on_track"
    if schedule is not None and _schedule_flags_at_risk(record, schedule):
        band = "at_risk"
    return band


def _schedule_flags_at_risk(record: MilestoneRecordV1, schedule: Schedule) -> bool:
    """Return whether a linked task sits on the schedule's critical path (pure).

    Only ids the schedule actually places (present in ``schedule.weights``) and
    that lie on ``schedule.critical_path`` count; an id the schedule does not
    place contributes no signal. Used solely to tighten an ``on_track`` band to
    ``at_risk`` -- never to relax any band.
    """
    if not record.task_ids:
        return False
    critical = set(schedule.critical_path)
    placed = schedule.weights
    return any(task_id in placed and task_id in critical for task_id in record.task_ids)


class MilestoneRegister:
    """Workspace-scoped milestone store.

    Each milestone lives in its own JSON file under ``runtime/pm/milestones``.
    Writes are atomic (temp-file + ``os.replace``), ids are traversal-guarded,
    and loads are fail-closed: a single corrupt file degrades exactly one record
    and never breaks the register. The store is pure/deterministic modulo the
    clock/nonce seams and env-free -- the ``KERNELONE_PM_MILESTONE_GATE``
    predicate lives outside it, for the future wiring layer.
    """

    def __init__(self, workspace: str, *, ensure_directory: bool = True) -> None:
        """Resolve the storage directory under ``runtime/pm/milestones``."""
        self._fs = KernelFileSystem(workspace, get_default_adapter())
        self._dir = Path(resolve_logical_path(workspace, "runtime/pm/milestones"))
        if ensure_directory:
            self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def register(
        self,
        *,
        name: str,
        description: str = "",
        target_iteration: int | None = None,
        task_ids: tuple[str, ...] = (),
        owner: str = "",
        acceptance: str = "",
        actor: str = "",
    ) -> MilestoneRecordV1:
        """Create, persist and return a new ``PLANNED`` milestone.

        ``name`` is the id seed and must be non-empty. The id is built as
        ``milestone_{safe(name)}_{nonce}`` so it is always traversal-guard-clean
        by construction. The history is seeded with a single ``registered``
        event (actor falling back to ``owner``).
        """
        _require_non_empty("name", name)
        now = _utc_now()
        milestone_id = f"milestone_{_safe_token(name)}_{_nonce()}"
        record = MilestoneRecordV1(
            milestone_id=milestone_id,
            name=name,
            description=description,
            status=MilestoneStatus.PLANNED,
            target_iteration=target_iteration,
            task_ids=_coerce_task_ids(task_ids),
            owner=owner,
            acceptance=acceptance,
            created_at=now,
            achieved_at=None,
            history=(
                {
                    "at": now,
                    "actor": actor or owner,
                    "action": "registered",
                    "note": "",
                },
            ),
        )
        self._save(record)
        return record

    def update_status(
        self,
        milestone_id: str,
        status: MilestoneStatus,
        *,
        actor: str,
        note: str = "",
        current_iteration: int | None = None,
    ) -> MilestoneRecordV1:
        """Transition a milestone to ``status`` and append an append-only event.

        The lifecycle is enforced fail-closed: a transition not allowed by
        :data:`_ALLOWED_TRANSITIONS` raises ``ValueError`` and leaves the
        on-disk record byte-untouched. A self-transition (``X -> X``) bypasses
        the table and is recorded as an idempotent no-op. The first transition
        into ``ACHIEVED`` stamps ``achieved_at`` once (idempotent thereafter).
        Raises ``FileNotFoundError`` if the milestone does not exist.

        ``current_iteration`` is accepted for caller symmetry and forward
        compatibility (a future increment could auto-transition past-target
        milestones to ``MISSED`` behind the gate); it is INERT this increment and
        does not alter the transition.
        """
        safe_id = _validate_record_id(milestone_id)
        current = self.load(safe_id)
        if current is None:
            raise FileNotFoundError(f"milestone not found: {safe_id}")
        allowed = _ALLOWED_TRANSITIONS[current.status]
        if status is not current.status and status not in allowed:
            raise ValueError(f"illegal milestone transition: {current.status.value}->{status.value}")
        event = {
            "at": _utc_now(),
            "actor": actor,
            "action": f"status:{status.value}",
            "note": note,
        }
        history = (*current.history, event)
        achieved_at = (
            _utc_now() if (status is MilestoneStatus.ACHIEVED and current.achieved_at is None) else current.achieved_at
        )
        next_record = MilestoneRecordV1(
            milestone_id=current.milestone_id,
            name=current.name,
            description=current.description,
            status=status,
            target_iteration=current.target_iteration,
            task_ids=current.task_ids,
            owner=current.owner,
            acceptance=current.acceptance,
            created_at=current.created_at,
            achieved_at=achieved_at,
            history=history,
        )
        self._save(next_record)
        return next_record

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def load(self, milestone_id: str) -> MilestoneRecordV1 | None:
        """Load one milestone by id; return ``None`` if absent. Traversal-guarded."""
        safe_id = _validate_record_id(milestone_id)
        path = self._dir / f"{safe_id}.json"
        if not path.exists():
            return None
        return self._load_path(path)

    def list(
        self,
        *,
        status: MilestoneStatus | None = None,
        target_iteration: int | None = None,
        task_id: str | None = None,
    ) -> list[MilestoneRecordV1]:
        """Return milestones (filename-sorted) matching the AND of all filters."""
        records: list[MilestoneRecordV1] = []
        if not self._dir.exists():
            return records
        for path in sorted(self._dir.glob("*.json")):
            record = self._load_path(path)
            if record is None:
                continue
            if status is not None and record.status is not status:
                continue
            if target_iteration is not None and record.target_iteration != target_iteration:
                continue
            if task_id is not None and task_id not in record.task_ids:
                continue
            records.append(record)
        return records

    def summarize(
        self,
        *,
        current_iteration: int | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Return tallies over the (optionally task-scoped) register.

        Every :class:`MilestoneStatus` value is present in ``by_status`` with a
        zero default. When ``current_iteration`` is supplied, ``overdue`` counts
        records the pure classifier bands as ``"overdue"`` and ``next_upcoming``
        is the nearest non-terminal milestone whose target iteration is at or
        after ``current_iteration`` (smallest target, tie-broken by
        ``milestone_id``). When ``current_iteration`` is ``None`` both stay
        fail-closed neutral (``overdue == 0``, ``next_upcoming is None``). One
        corrupt file never breaks the summary.
        """
        records = self.list(task_id=task_id)
        by_status: dict[str, int] = {s.value: 0 for s in MilestoneStatus}
        for record in records:
            by_status[record.status.value] += 1

        overdue = 0
        next_upcoming: dict[str, Any] | None = None
        if current_iteration is not None:
            best: tuple[int, str] | None = None
            for record in records:
                band = classify_milestone_health(record, current_iteration=current_iteration)
                if band == "overdue":
                    overdue += 1
                target = record.target_iteration
                if (
                    record.status
                    not in {
                        MilestoneStatus.ACHIEVED,
                        MilestoneStatus.MISSED,
                        MilestoneStatus.CANCELLED,
                    }
                    and target is not None
                    and target >= current_iteration
                ):
                    candidate = (target, record.milestone_id)
                    if best is None or candidate < best:
                        best = candidate
            if best is not None:
                next_upcoming = {"milestone_id": best[1], "target_iteration": best[0]}

        return {
            "total": len(records),
            "by_status": by_status,
            "overdue": overdue,
            "next_upcoming": next_upcoming,
            "current_iteration": current_iteration,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _save(self, record: MilestoneRecordV1) -> None:
        """Atomically persist a record to ``{milestone_id}.json`` via KFS."""
        safe_id = _validate_record_id(record.milestone_id)
        self._fs.write_json_atomic(
            f"runtime/pm/milestones/{safe_id}.json",
            record.to_dict(),
            ensure_ascii=False,
            indent=2,
        )

    def _load_path(self, path: Path) -> MilestoneRecordV1 | None:
        """Load and coerce one record file; return ``None`` on any read error.

        Fail-closed: ``OSError``/``JSONDecodeError``/``TypeError`` and non-dict
        payloads all degrade to ``None`` rather than raising, and the record
        build is wrapped so a residual ``ValueError`` (e.g. empty primary key)
        also degrades to ``None``.
        """
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            return MilestoneRecordV1(
                milestone_id=str(data.get("milestone_id") or path.stem),
                name=str(data.get("name") or ""),
                description=str(data.get("description") or ""),
                status=_coerce_status(data.get("status")),
                target_iteration=_coerce_target_iteration(data.get("target_iteration")),
                task_ids=_coerce_task_ids(data.get("task_ids")),
                owner=str(data.get("owner") or ""),
                acceptance=str(data.get("acceptance") or ""),
                created_at=str(data.get("created_at") or ""),
                achieved_at=(str(data.get("achieved_at")).strip() if data.get("achieved_at") else None),
                history=_coerce_history(data.get("history")),
            )
        except ValueError:
            return None


__all__ = [
    "MilestoneRecordV1",
    "MilestoneRegister",
    "MilestoneStatus",
    "classify_milestone_health",
]
