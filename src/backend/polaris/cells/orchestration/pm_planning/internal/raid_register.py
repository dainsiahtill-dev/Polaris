"""RAID register storage for the PM (尚书令) planning cell.

A RAID register is standard PM doctrine (PMBOK/PRINCE2): a living log that
tracks **R**isks (future uncertain events), **A**ssumptions (believed-true but
unverified), **I**ssues (problems that have already occurred), and
**D**ependencies (things the project relies on). A real PM scores risks by
``probability x impact`` and drives each entry through a shared lifecycle.

This module is the workspace-scoped store: one JSON file per entry under
``runtime/pm/raid/{entry_id}.json``. It is pure, deterministic (modulo the
clock/nonce seams), fail-closed on load, and 100% generic meta-tooling --
no project/business/domain literals.

The enum *values* are byte-identical to the chief-engineer Risk Register
vocabulary for cross-role alignment, but this module imports NOTHING from
``chief_engineer``: PM is upstream of CE in the role chain and the
``orchestration.pm_planning`` cell does not declare a dependency on
``chief_engineer.blueprint``. The PM-owned enums also *extend* CE with a RAID
category axis and a probability/likelihood axis that CE has no equivalent for.

This increment is purely additive: nothing here is wired into the live
planning path, and the ``KERNELONE_PM_RISK_GATE`` predicate is shipped and
unit-tested but called by nothing.
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

from polaris.kernelone.storage import resolve_logical_path

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_SAFE_ID_FULLMATCH = re.compile(r"^[A-Za-z0-9_.-]+$")
_RISK_GATE_ENV = "KERNELONE_PM_RISK_GATE"
_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})


class RaidCategory(str, Enum):
    """The RAID axis a register entry belongs to.

    This axis is RAID-native and has no chief-engineer equivalent: CE tracks
    risks only, whereas a PM register spans all four categories.
    """

    RISK = "risk"
    ASSUMPTION = "assumption"
    ISSUE = "issue"
    DEPENDENCY = "dependency"


class RaidSeverity(str, Enum):
    """Impact axis. Values are aligned with the CE Risk Register vocabulary."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    BLOCKER = "blocker"


class RaidProbability(str, Enum):
    """Likelihood axis (RAID-native; CE has no probability concept).

    Required for ``probability x impact`` scoring on a standard 5x5 matrix.
    """

    RARE = "rare"
    UNLIKELY = "unlikely"
    POSSIBLE = "possible"
    LIKELY = "likely"
    ALMOST_CERTAIN = "almost_certain"


class RaidStatus(str, Enum):
    """Lifecycle ladder, shared across all four categories.

    Values are aligned with the CE Risk Register vocabulary.
    """

    OPEN = "open"
    MITIGATING = "mitigating"
    ACCEPTED = "accepted"
    RESOLVED = "resolved"
    REVERTED = "reverted"


_SEVERITY_WEIGHT: dict[RaidSeverity, int] = {
    RaidSeverity.LOW: 1,
    RaidSeverity.MEDIUM: 2,
    RaidSeverity.HIGH: 3,
    RaidSeverity.CRITICAL: 4,
    RaidSeverity.BLOCKER: 5,
}

_PROBABILITY_WEIGHT: dict[RaidProbability, int] = {
    RaidProbability.RARE: 1,
    RaidProbability.UNLIKELY: 2,
    RaidProbability.POSSIBLE: 3,
    RaidProbability.LIKELY: 4,
    RaidProbability.ALMOST_CERTAIN: 5,
}


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
    return token[:80] or "raid"


def _validate_record_id(value: str) -> str:
    """Reject ids that could escape the storage directory (path traversal).

    The id is treated as untrusted (it may arrive from HTTP path parameters or
    programmatic callers), so it must be a bare safe token: alphanumerics plus
    ``_ . -``, with no path separators and no ``..`` sequence. Fail-closed:
    anything else raises ``ValueError``.
    """
    token = str(value or "").strip()
    if not token or ".." in token or not _SAFE_ID_FULLMATCH.match(token):
        raise ValueError(f"unsafe raid entry id: {value!r}")
    return token


def _risk_gate_enabled(value: str | None = None) -> bool:
    """Return whether the RAID planning gate is enabled (default OFF).

    With no explicit ``value`` the gate is read from the
    ``KERNELONE_PM_RISK_GATE`` environment variable, so the future wiring layer
    can simply call ``_risk_gate_enabled()`` and inherit the OFF-by-default
    guard from this single place rather than re-deriving it at the call site
    (where it could accidentally default ON). An explicit ``value`` overrides
    the environment (used by tests).

    Returns ``True`` only for the recognized truthy tokens (case/whitespace
    insensitive); every other value -- including unset/``None``, ``""``,
    ``"0"``, ``"false"``, ``"off"`` and unrecognized tokens -- returns
    ``False``. Fail-closed: ambiguity resolves to OFF (the current live behavior).
    """
    token = value if value is not None else os.environ.get(_RISK_GATE_ENV)
    return str(token or "").strip().lower() in _TRUE_TOKENS


def compute_risk_score(probability: Any, severity: Any) -> int:
    """Return the ``probability x impact`` score on a standard 5x5 matrix.

    Pure, deterministic, and fail-closed: any unknown/mistyped input coerces to
    the same defaults the loader uses (POSSIBLE for probability, MEDIUM for
    severity), so the result is always in the range ``1..25`` and never raises.
    """
    prob = _PROBABILITY_WEIGHT.get(probability) if isinstance(probability, RaidProbability) else None
    sev = _SEVERITY_WEIGHT.get(severity) if isinstance(severity, RaidSeverity) else None
    if prob is None:
        prob = _PROBABILITY_WEIGHT[RaidProbability.POSSIBLE]
    if sev is None:
        sev = _SEVERITY_WEIGHT[RaidSeverity.MEDIUM]
    return prob * sev


def compute_risk_band(score: int) -> str:
    """Map a 1..25 risk score to a generic ``low``/``medium``/``high`` band.

    Thresholds are deterministic and project-agnostic: ``<=4`` low, ``5..12``
    medium, ``>=13`` high.
    """
    if score <= 4:
        return "low"
    if score <= 12:
        return "medium"
    return "high"


def _coerce_category(value: Any) -> RaidCategory:
    """Coerce a loaded value to a :class:`RaidCategory`, defaulting to ``RISK``."""
    if isinstance(value, RaidCategory):
        return value
    try:
        return RaidCategory(str(value or "risk").strip().lower() or "risk")
    except ValueError:
        return RaidCategory.RISK


def _coerce_severity(value: Any) -> RaidSeverity:
    """Coerce a loaded value to a :class:`RaidSeverity`, defaulting to ``MEDIUM``."""
    if isinstance(value, RaidSeverity):
        return value
    try:
        return RaidSeverity(str(value or "medium").strip().lower() or "medium")
    except ValueError:
        return RaidSeverity.MEDIUM


def _coerce_probability(value: Any) -> RaidProbability:
    """Coerce a loaded value to a :class:`RaidProbability`, default ``POSSIBLE``."""
    if isinstance(value, RaidProbability):
        return value
    try:
        return RaidProbability(str(value or "possible").strip().lower() or "possible")
    except ValueError:
        return RaidProbability.POSSIBLE


def _coerce_status(value: Any) -> RaidStatus:
    """Coerce a loaded value to a :class:`RaidStatus`, defaulting to ``OPEN``."""
    if isinstance(value, RaidStatus):
        return value
    try:
        return RaidStatus(str(value or "open").strip().lower() or "open")
    except ValueError:
        return RaidStatus.OPEN


def _coerce_links(value: Any) -> tuple[str, ...]:
    """Coerce a loaded value to a tuple of non-empty link strings (fail-closed)."""
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


@dataclass(frozen=True)
class RaidRecordV1:
    """A single immutable RAID register entry.

    The record is category-neutral: the register holds risks, assumptions,
    issues and dependencies under one shared schema and lifecycle. ``detail``
    is a single generic free-text field that generalizes a risk mitigation /
    issue corrective action / assumption validation plan / dependency
    contingency, avoiding category-specific schema branches.
    """

    entry_id: str
    category: RaidCategory
    task_id: str
    title: str
    severity: RaidSeverity
    probability: RaidProbability
    owner: str = ""
    detail: str = ""
    status: RaidStatus = RaidStatus.OPEN
    detected_at: str = ""
    links: tuple[str, ...] = field(default_factory=tuple)
    supersedes: str | None = None
    history: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Validate the filesystem-critical primary-key invariant.

        Only ``entry_id`` is hard-required: it is the storage key and must be
        non-empty for any path to be built. ``title``/``detected_at`` are
        always stamped by :meth:`RaidRegister.register`, but are deliberately
        NOT hard-required here so the fail-closed loader can still recover a
        partially-populated record (empty title/detected_at) rather than
        discarding the whole entry.
        """
        _require_non_empty("entry_id", self.entry_id)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict, including derived read-only fields.

        ``risk_score`` and ``risk_band`` are derived from the probability and
        severity enums (not stored as source-of-truth) so they cannot drift.
        """
        return {
            "entry_id": self.entry_id,
            "category": self.category.value,
            "task_id": self.task_id,
            "title": self.title,
            "severity": self.severity.value,
            "probability": self.probability.value,
            "owner": self.owner,
            "detail": self.detail,
            "status": self.status.value,
            "detected_at": self.detected_at,
            "links": list(self.links),
            "supersedes": self.supersedes,
            "history": [dict(event) for event in self.history],
            "risk_score": compute_risk_score(self.probability, self.severity),
            "risk_band": compute_risk_band(compute_risk_score(self.probability, self.severity)),
        }


@dataclass(frozen=True)
class RaidEventV1:
    """A generic, audit-friendly RAID lifecycle event.

    Shipped for future ``audit.evidence`` emission. It is emitted on NO live
    path this increment and can be dropped at wiring time without touching the
    store/record/enum surface.
    """

    entry_id: str
    workspace: str
    action: str
    actor: str
    note: str = ""
    at: str = ""

    def to_dict(self) -> dict[str, str]:
        """Serialize the event to a JSON-safe dict."""
        return {
            "entry_id": self.entry_id,
            "workspace": self.workspace,
            "action": self.action,
            "actor": self.actor,
            "note": self.note,
            "at": self.at,
        }


def build_raid_event(
    *,
    entry_id: str,
    workspace: str,
    action: str,
    actor: str,
    note: str = "",
) -> RaidEventV1:
    """Build a timestamped :class:`RaidEventV1` (pure factory, no I/O)."""
    return RaidEventV1(
        entry_id=entry_id,
        workspace=workspace,
        action=action,
        actor=actor,
        note=note,
        at=_utc_now(),
    )


def _require_non_empty(name: str, value: str) -> None:
    """Raise ``ValueError`` if a required string field is empty/whitespace."""
    if not str(value or "").strip():
        raise ValueError(f"raid record field {name!r} must be non-empty")


class RaidRegister:
    """Workspace-scoped RAID register storage.

    Each entry lives in its own JSON file under ``runtime/pm/raid``. Writes are
    atomic (temp-file + ``os.replace``), ids are traversal-guarded, and loads
    are fail-closed: a single corrupt file degrades that one entry and never
    breaks the register. The store is pure/deterministic and env-free -- the
    ``KERNELONE_PM_RISK_GATE`` predicate lives outside it, at the future
    wiring layer.
    """

    def __init__(self, workspace: str, *, ensure_directory: bool = True) -> None:
        """Resolve the storage directory under ``runtime/pm/raid``."""
        self._dir = Path(resolve_logical_path(workspace, "runtime/pm/raid"))
        if ensure_directory:
            self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def register(
        self,
        *,
        task_id: str,
        category: RaidCategory,
        title: str,
        severity: RaidSeverity,
        probability: RaidProbability,
        owner: str = "",
        detail: str = "",
        links: tuple[str, ...] = (),
        supersedes: str | None = None,
    ) -> RaidRecordV1:
        """Create, persist and return a new ``OPEN`` RAID entry.

        The entry id is built as ``raid_{category}_{safe(task_id)}_{nonce}`` so
        it is always traversal-guard-clean by construction. ``task_id`` may be
        empty for project-level entries. The history is seeded with a single
        ``registered`` event.
        """
        now = _utc_now()
        entry_id = f"raid_{category.value}_{_safe_token(task_id)}_{_nonce()}"
        record = RaidRecordV1(
            entry_id=entry_id,
            category=category,
            task_id=task_id,
            title=title,
            severity=severity,
            probability=probability,
            owner=owner,
            detail=detail,
            status=RaidStatus.OPEN,
            detected_at=now,
            links=_coerce_links(links),
            supersedes=supersedes,
            history=(
                {
                    "at": now,
                    "actor": owner,
                    "action": "registered",
                    "note": "",
                },
            ),
        )
        self._save(record)
        return record

    def update_status(
        self,
        entry_id: str,
        status: RaidStatus,
        *,
        actor: str,
        note: str = "",
    ) -> RaidRecordV1:
        """Transition an entry to ``status`` and append an append-only event.

        The prior history is preserved (never mutated in place); a new
        ``status:{value}`` event is appended and the whole record rewritten
        atomically. Raises ``FileNotFoundError`` if the entry does not exist.
        """
        safe_id = _validate_record_id(entry_id)
        current = self.load(safe_id)
        if current is None:
            raise FileNotFoundError(f"raid entry not found: {safe_id}")
        history = (
            *current.history,
            {
                "at": _utc_now(),
                "actor": actor,
                "action": f"status:{status.value}",
                "note": note,
            },
        )
        next_record = RaidRecordV1(
            entry_id=current.entry_id,
            category=current.category,
            task_id=current.task_id,
            title=current.title,
            severity=current.severity,
            probability=current.probability,
            owner=current.owner,
            detail=current.detail,
            status=status,
            detected_at=current.detected_at,
            links=current.links,
            supersedes=current.supersedes,
            history=history,
        )
        self._save(next_record)
        return next_record

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def load(self, entry_id: str) -> RaidRecordV1 | None:
        """Load one entry by id; return ``None`` if absent. Traversal-guarded."""
        safe_id = _validate_record_id(entry_id)
        path = self._dir / f"{safe_id}.json"
        if not path.exists():
            return None
        return self._load_path(path)

    def list(
        self,
        *,
        category: RaidCategory | None = None,
        task_id: str | None = None,
        severity: RaidSeverity | None = None,
        status: RaidStatus | None = None,
    ) -> list[RaidRecordV1]:
        """Return entries (entry-id-sorted) matching the AND of all filters."""
        records: list[RaidRecordV1] = []
        if not self._dir.exists():
            return records
        for path in sorted(self._dir.glob("*.json")):
            record = self._load_path(path)
            if record is None:
                continue
            if category is not None and record.category is not category:
                continue
            if task_id is not None and record.task_id != task_id:
                continue
            if severity is not None and record.severity is not severity:
                continue
            if status is not None and record.status is not status:
                continue
            records.append(record)
        return records

    def summarize(
        self,
        *,
        task_id: str | None = None,
        category: RaidCategory | None = None,
    ) -> dict[str, Any]:
        """Return tallies over the (optionally filtered) register.

        Every enum-member key is present with a zero default.
        ``open_critical_or_blocker`` counts entries whose status is ``OPEN``
        and severity is in ``{CRITICAL, BLOCKER}``.
        """
        records = self.list(task_id=task_id, category=category)
        by_category: dict[str, int] = {c.value: 0 for c in RaidCategory}
        by_severity: dict[str, int] = {s.value: 0 for s in RaidSeverity}
        by_status: dict[str, int] = {s.value: 0 for s in RaidStatus}
        open_blocker = 0
        for record in records:
            by_category[record.category.value] += 1
            by_severity[record.severity.value] += 1
            by_status[record.status.value] += 1
            if record.status is RaidStatus.OPEN and record.severity in {
                RaidSeverity.BLOCKER,
                RaidSeverity.CRITICAL,
            }:
                open_blocker += 1
        return {
            "total": len(records),
            "by_category": by_category,
            "by_severity": by_severity,
            "by_status": by_status,
            "open_critical_or_blocker": open_blocker,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _save(self, record: RaidRecordV1) -> None:
        """Atomically persist a record to ``{entry_id}.json``.

        The temp companion is ``{name}.tmp`` (not ``with_suffix``) because
        ``.`` is a legal id char, so a dotted entry id would otherwise make
        ``with_suffix`` clobber only the last segment. ``os.replace`` makes the
        swap atomic, so a reader globbing ``*.json`` never sees a partial file.
        """
        safe_id = _validate_record_id(record.entry_id)
        path = self._dir / f"{safe_id}.json"
        tmp = path.parent / (path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(record.to_dict(), handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def _load_path(self, path: Path) -> RaidRecordV1 | None:
        """Load and coerce one entry file; return ``None`` on any read error.

        Fail-closed: ``OSError``/``JSONDecodeError``/``TypeError`` and non-dict
        payloads all degrade to ``None`` rather than raising.
        """
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            return RaidRecordV1(
                entry_id=str(data.get("entry_id") or path.stem),
                category=_coerce_category(data.get("category")),
                task_id=str(data.get("task_id") or ""),
                title=str(data.get("title") or ""),
                severity=_coerce_severity(data.get("severity")),
                probability=_coerce_probability(data.get("probability")),
                owner=str(data.get("owner") or ""),
                detail=str(data.get("detail") or ""),
                status=_coerce_status(data.get("status")),
                detected_at=str(data.get("detected_at") or ""),
                links=_coerce_links(data.get("links")),
                supersedes=(str(data.get("supersedes") or "").strip() or None),
                history=_coerce_history(data.get("history")),
            )
        except ValueError:
            return None


__all__ = [
    "RaidCategory",
    "RaidEventV1",
    "RaidProbability",
    "RaidRecordV1",
    "RaidRegister",
    "RaidSeverity",
    "RaidStatus",
    "build_raid_event",
    "compute_risk_band",
    "compute_risk_score",
]
