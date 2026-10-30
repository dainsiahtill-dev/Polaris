"""Decision register for the PM (尚书令) planning cell.

A decision register is a standard PMBOK governance artifact: the durable log of
the deliberate choices a project manager makes during planning, each recorded
with its context, the options that were considered, the chosen statement, the
rationale, an accountable owner, and an ADR-lite lifecycle status. It is the
missing third of the PM governance trio that already has the RAID register
(:mod:`.raid_register`) and the milestone registry (:mod:`.milestones`).

This is DISTINCT from the in-memory execution decisions tracked on the live
agent (``PMAgent._decisions``): those record transient EXECUTION-FLOW choices
(``CONTINUE`` / ``RETRY`` / ``STOP`` on a task result) in agent memory. This
store is for GOVERNANCE decisions -- a deliberate choice made with rationale --
which is a different concept. This module touches neither the agent nor that
list; the two never couple.

The store mirrors the sibling registers' storage shape exactly: one JSON file
per entry under ``runtime/pm/decisions/{decision_id}.json``,
KernelFileSystem-backed atomic writes, traversal-guarded ids, fail-closed loads
(one corrupt file degrades exactly one record), explicit UTF-8 on every
read/write, and an append-only history. It is pure/deterministic modulo the
clock/nonce seams, 100% generic meta-tooling -- no project/business/domain
literals -- and imports NOTHING from ``chief_engineer`` and NOTHING from
``polaris.delivery``.

This increment is purely additive: nothing here is wired into the live planning
path, and the store is wired to NOTHING, so the from-scratch + repair regression
floor is provably untouched.

No env-gate predicate ships here (unlike the sibling registers). Their gates
guard a planned gated WRITE from a documented future call-site (RAID
auto-registration on dispatch; milestone architect-schema ingestion). The
decision register has no such gated write: its only described future use is a
DEFERRED READ that folds an open/accepted-decisions summary into the project
report, and a read-fold needs no write-gate (``project_report`` already consumes
the sibling ``summarize()`` reads ungated). Shipping a predicate that nothing
calls would be the YAGNI dead-constant smell, so the gate is deliberately
omitted; :func:`test_no_decision_gate_predicate_shipped` pins that choice.

Deferred integration (DESIGN ONLY this increment -- built nowhere outside this
file and its test):

(a) Project-report decision fold. A later wiring increment, in the
    ``project_report`` gather/compose layer (never this module), would call
    :meth:`DecisionRegister.summarize` (and optionally
    :meth:`DecisionRegister.list` for the open/accepted records), add an
    optional ``decision_summary`` input to ``compose_project_report`` parallel
    to the existing ``raid_summary``/``milestone_summary`` parameters, add a
    ``decisions`` field to the report dataclass, and render an accepted/proposed
    tally. With nothing registered, :meth:`summarize` returns an all-zero block,
    so the fold is neutral for existing projects. Because the report already
    reads the sibling summaries ungated, no gate is needed -- which is precisely
    why no gate predicate ships here.

(b) Optional read-only ingestion of the in-memory execution decisions. The same
    future wiring layer (never this module, never the agent) could read the
    transient ``PMAgent._decisions`` list and, for any it wants to durably
    govern, map each into a :meth:`DecisionRegister.register` call. This is a
    one-way projection of a DIFFERENT concept into the governance store: the
    agent is not edited, its in-memory list is not replaced, and no coupling to
    ``chief_engineer`` is introduced.

Both deferred folds are read-only and additive, so the regression floor cannot
move.
"""

from __future__ import annotations

import json
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

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class DecisionStatus(str, Enum):
    """The ADR-lite lifecycle state of a governance decision.

    ``PROPOSED`` is the active (non-terminal) entry state. ``DEFERRED`` is a
    holding state that can re-open. ``ACCEPTED`` is a committed choice that can
    only be ``SUPERSEDED`` (never silently un-accepted). ``REJECTED`` and
    ``SUPERSEDED`` are terminal: once reached a decision accepts no further
    transition (only the recorded self-no-op).
    """

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    DEFERRED = "deferred"


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
    return token[:80] or "decision"


def _validate_record_id(value: str) -> str:
    """Reject ids that could escape the storage directory (path traversal).

    Delegates to the canonical SSoT in
    ``polaris.kernelone.security.record_id_guard``; kept as a thin local
    wrapper so existing call sites and test monkeypatch targets resolve
    unchanged. Fail-closed: anything but a bare safe token raises.
    """
    return validate_storage_record_id(value, label="decision id")


def _require_non_empty(name: str, value: str) -> None:
    """Raise ``ValueError`` if a string field is empty/whitespace."""
    if not str(value or "").strip():
        raise ValueError(f"decision record field {name!r} must be non-empty")


def _coerce_status(value: Any) -> DecisionStatus:
    """Coerce a loaded value to a :class:`DecisionStatus`, defaulting ``PROPOSED``."""
    if isinstance(value, DecisionStatus):
        return value
    try:
        return DecisionStatus(str(value or "proposed").strip().lower() or "proposed")
    except ValueError:
        return DecisionStatus.PROPOSED


def _coerce_str_tuple(value: Any) -> tuple[str, ...]:
    """Coerce a loaded value to a tuple of non-empty strings (fail-closed).

    Used for ``options``, ``task_ids`` and ``risk_ids``. ``None`` and empty
    values yield ``()``; a scalar becomes a one-element tuple; list/tuple items
    are stripped and empties dropped.
    """
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


# The ADR-lite lifecycle table. A transition is legal only if the target appears
# in the current state's allow-set; terminal states allow nothing further. A
# self-transition (X -> X) always bypasses the table and is recorded as an
# idempotent no-op, paralleling the sibling registers' re-append behaviour. An
# ACCEPTED decision can only be SUPERSEDED -- never silently un-accepted.
_ALLOWED_TRANSITIONS: dict[DecisionStatus, frozenset[DecisionStatus]] = {
    DecisionStatus.PROPOSED: frozenset(
        {
            DecisionStatus.ACCEPTED,
            DecisionStatus.REJECTED,
            DecisionStatus.DEFERRED,
            DecisionStatus.SUPERSEDED,
        }
    ),
    DecisionStatus.DEFERRED: frozenset(
        {
            DecisionStatus.PROPOSED,
            DecisionStatus.ACCEPTED,
            DecisionStatus.REJECTED,
            DecisionStatus.SUPERSEDED,
        }
    ),
    DecisionStatus.ACCEPTED: frozenset({DecisionStatus.SUPERSEDED}),
    DecisionStatus.REJECTED: frozenset(),
    DecisionStatus.SUPERSEDED: frozenset(),
}


@dataclass(frozen=True)
class DecisionRecordV1:
    """A single immutable governance-decision record.

    Only ``decision_id`` is hard-required (the storage key must be non-empty so
    a filesystem path can always be built). Every other field tolerates an empty
    or default value so the fail-closed loader can recover a partially-populated
    record rather than discarding the whole entry.

    ``risk_ids`` links a decision to sibling RAID entries WITHOUT importing the
    RAID register: the link is a plain id string, AND-filterable in
    :meth:`DecisionRegister.list`/:meth:`DecisionRegister.summarize`.
    """

    decision_id: str
    title: str = ""
    context: str = ""
    options: tuple[str, ...] = field(default_factory=tuple)
    decision: str = ""
    rationale: str = ""
    owner: str = ""
    status: DecisionStatus = DecisionStatus.PROPOSED
    task_ids: tuple[str, ...] = field(default_factory=tuple)
    risk_ids: tuple[str, ...] = field(default_factory=tuple)
    decided_at: str = ""
    history: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Validate the filesystem-critical primary-key invariant.

        Only ``decision_id`` is hard-required: the storage key must be non-empty
        for any path to be built. ``title``/``decided_at`` are always stamped by
        :meth:`DecisionRegister.register`, but are deliberately NOT hard-required
        so the fail-closed loader can still recover a partially-populated record
        (empty title/decided_at) rather than discarding the whole entry.
        """
        _require_non_empty("decision_id", self.decision_id)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict in a fixed key order.

        ``status`` is emitted as its bare string value, ``options``/``task_ids``/
        ``risk_ids`` as lists, and ``history`` as a list of dicts. The key order
        is fixed here (not via ``sort_keys``) so byte output is deterministic for
        a fixed clock/nonce, mirroring the sibling registers. There are no
        derived fields (decisions have no risk_score analogue).
        """
        return {
            "decision_id": self.decision_id,
            "title": self.title,
            "context": self.context,
            "options": list(self.options),
            "decision": self.decision,
            "rationale": self.rationale,
            "owner": self.owner,
            "status": self.status.value,
            "task_ids": list(self.task_ids),
            "risk_ids": list(self.risk_ids),
            "decided_at": self.decided_at,
            "history": [dict(event) for event in self.history],
        }


class DecisionRegister:
    """Workspace-scoped governance-decision store.

    Each decision lives in its own JSON file under ``runtime/pm/decisions``.
    Writes are atomic (temp-file + ``os.replace``), ids are traversal-guarded,
    and loads are fail-closed: a single corrupt file degrades exactly one record
    and never breaks the register. The store is pure/deterministic modulo the
    clock/nonce seams and env-free -- no env-gate predicate ships, because the
    only described future use is a read-fold into the project report.
    """

    def __init__(self, workspace: str, *, ensure_directory: bool = True) -> None:
        """Resolve the storage directory under ``runtime/pm/decisions``."""
        self._fs = KernelFileSystem(workspace, get_default_adapter())
        self._dir = Path(resolve_logical_path(workspace, "runtime/pm/decisions"))
        if ensure_directory:
            self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def register(
        self,
        *,
        title: str,
        context: str = "",
        options: tuple[str, ...] = (),
        decision: str = "",
        rationale: str = "",
        owner: str = "",
        task_ids: tuple[str, ...] = (),
        risk_ids: tuple[str, ...] = (),
        actor: str = "",
    ) -> DecisionRecordV1:
        """Create, persist and return a new ``PROPOSED`` decision.

        ``title`` is the id seed and must be non-empty. The id is built as
        ``decision_{safe(title)}_{nonce}`` so it is always traversal-guard-clean
        by construction. The history is seeded with a single ``registered``
        event (actor falling back to ``owner``).
        """
        _require_non_empty("title", title)
        now = _utc_now()
        decision_id = f"decision_{_safe_token(title)}_{_nonce()}"
        record = DecisionRecordV1(
            decision_id=decision_id,
            title=title,
            context=context,
            options=_coerce_str_tuple(options),
            decision=decision,
            rationale=rationale,
            owner=owner,
            status=DecisionStatus.PROPOSED,
            task_ids=_coerce_str_tuple(task_ids),
            risk_ids=_coerce_str_tuple(risk_ids),
            decided_at=now,
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
        decision_id: str,
        status: DecisionStatus,
        *,
        actor: str,
        note: str = "",
    ) -> DecisionRecordV1:
        """Transition a decision to ``status`` and append an append-only event.

        The lifecycle is enforced fail-closed: a transition not allowed by
        :data:`_ALLOWED_TRANSITIONS` raises ``ValueError`` BEFORE any write and
        leaves the on-disk record byte-untouched. A self-transition (``X -> X``)
        bypasses the table and is recorded as an idempotent no-op. ``decided_at``
        is NOT re-stamped on transition -- decisions have a single registration
        timestamp and an append-only history. Raises ``FileNotFoundError`` if the
        decision does not exist.
        """
        safe_id = _validate_record_id(decision_id)
        current = self.load(safe_id)
        if current is None:
            raise FileNotFoundError(f"decision not found: {safe_id}")
        allowed = _ALLOWED_TRANSITIONS[current.status]
        if status is not current.status and status not in allowed:
            raise ValueError(f"illegal decision transition: {current.status.value}->{status.value}")
        event = {
            "at": _utc_now(),
            "actor": actor,
            "action": f"status:{status.value}",
            "note": note,
        }
        history = (*current.history, event)
        next_record = DecisionRecordV1(
            decision_id=current.decision_id,
            title=current.title,
            context=current.context,
            options=current.options,
            decision=current.decision,
            rationale=current.rationale,
            owner=current.owner,
            status=status,
            task_ids=current.task_ids,
            risk_ids=current.risk_ids,
            decided_at=current.decided_at,
            history=history,
        )
        self._save(next_record)
        return next_record

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def load(self, decision_id: str) -> DecisionRecordV1 | None:
        """Load one decision by id; return ``None`` if absent. Traversal-guarded."""
        safe_id = _validate_record_id(decision_id)
        path = self._dir / f"{safe_id}.json"
        if not path.exists():
            return None
        return self._load_path(path)

    def list(
        self,
        *,
        status: DecisionStatus | None = None,
        task_id: str | None = None,
        risk_id: str | None = None,
    ) -> list[DecisionRecordV1]:
        """Return decisions (filename-sorted) matching the AND of all filters."""
        records: list[DecisionRecordV1] = []
        if not self._dir.exists():
            return records
        for path in sorted(self._dir.glob("*.json")):
            record = self._load_path(path)
            if record is None:
                continue
            if status is not None and record.status is not status:
                continue
            if task_id is not None and task_id not in record.task_ids:
                continue
            if risk_id is not None and risk_id not in record.risk_ids:
                continue
            records.append(record)
        return records

    def summarize(
        self,
        *,
        task_id: str | None = None,
        risk_id: str | None = None,
    ) -> dict[str, Any]:
        """Return tallies over the (optionally filtered) register.

        Every :class:`DecisionStatus` value is present in ``by_status`` with a
        zero default. ``accepted`` and ``proposed`` are surfaced as a top-level
        tally for the deferred project-report fold. One corrupt file never breaks
        the summary.
        """
        records = self.list(task_id=task_id, risk_id=risk_id)
        by_status: dict[str, int] = {s.value: 0 for s in DecisionStatus}
        for record in records:
            by_status[record.status.value] += 1
        return {
            "total": len(records),
            "by_status": by_status,
            "accepted": by_status[DecisionStatus.ACCEPTED.value],
            "proposed": by_status[DecisionStatus.PROPOSED.value],
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _save(self, record: DecisionRecordV1) -> None:
        """Atomically persist a record to ``{decision_id}.json`` via KFS."""
        safe_id = _validate_record_id(record.decision_id)
        self._fs.write_json_atomic(
            f"runtime/pm/decisions/{safe_id}.json",
            record.to_dict(),
            ensure_ascii=False,
            indent=2,
        )

    def _load_path(self, path: Path) -> DecisionRecordV1 | None:
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
            return DecisionRecordV1(
                decision_id=str(data.get("decision_id") or path.stem),
                title=str(data.get("title") or ""),
                context=str(data.get("context") or ""),
                options=_coerce_str_tuple(data.get("options")),
                decision=str(data.get("decision") or ""),
                rationale=str(data.get("rationale") or ""),
                owner=str(data.get("owner") or ""),
                status=_coerce_status(data.get("status")),
                task_ids=_coerce_str_tuple(data.get("task_ids")),
                risk_ids=_coerce_str_tuple(data.get("risk_ids")),
                decided_at=str(data.get("decided_at") or ""),
                history=_coerce_history(data.get("history")),
            )
        except ValueError:
            return None


__all__ = [
    "DecisionRecordV1",
    "DecisionRegister",
    "DecisionStatus",
]
