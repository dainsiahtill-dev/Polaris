"""Tech Radar storage and stack-policy helpers.

A 技术总监's tech radar: each library/technology is placed on a ring
(adopt / trial / hold / deprecated). ``hold`` and ``deprecated`` are
stack-policy violations when a blueprint depends on them. One JSON file per
entry under ``runtime/tech_radar/{entry_id}.json`` — same hardened,
traversal-guarded, UTF-8, atomic pattern as the Risk Register.
"""

from __future__ import annotations

import builtins
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.cells.chief_engineer.blueprint.public.contracts import (
    RegisterTechRadarCommandV1,
    StackPolicyViolationV1,
    TechRadarEntryV1,
    TechRadarEventV1,
    TechRadarRing,
    UpdateTechRadarRingCommandV1,
)
from polaris.kernelone.storage import resolve_logical_path

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_SAFE_ID_FULLMATCH = re.compile(r"^[A-Za-z0-9_.-]+$")

# Rings that block a blueprint's dependency (a real CTO's "do not use").
_VIOLATION_RINGS = frozenset({TechRadarRing.HOLD, TechRadarRing.DEPRECATED})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _nonce() -> str:
    stamp = _utc_now().replace(":", "").replace("-", "").replace(".", "")
    return f"{stamp}{uuid.uuid4().hex[:8]}"


def _safe_token(value: str) -> str:
    token = _SAFE_ID_RE.sub("_", str(value or "").strip()).strip("._-")
    return token[:80] or "lib"


def _validate_record_id(value: str) -> str:
    """Reject ids that could escape the storage directory (path traversal)."""

    token = str(value or "").strip()
    if not token or ".." in token or not _SAFE_ID_FULLMATCH.match(token):
        raise ValueError(f"unsafe entry_id: {value!r}")
    return token


def _normalize_library(value: str) -> str:
    return str(value or "").strip().lower()


def _coerce_ring(value: Any) -> TechRadarRing:
    if isinstance(value, TechRadarRing):
        return value
    try:
        return TechRadarRing(str(value or "adopt").strip().lower() or "adopt")
    except ValueError:
        return TechRadarRing.ADOPT


class TechRadarLedger:
    """Workspace-scoped Tech-Radar ledger."""

    def __init__(self, workspace: str, *, ensure_directory: bool = True) -> None:
        self._dir = Path(resolve_logical_path(workspace, "runtime/tech_radar"))
        if ensure_directory:
            self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def register(self, command: RegisterTechRadarCommandV1) -> TechRadarEntryV1:
        now = _utc_now()
        entry_id = f"radar_{_safe_token(command.library)}_{_nonce()}"
        record = TechRadarEntryV1(
            entry_id=entry_id,
            library=command.library,
            ring=command.ring,
            rationale=command.rationale,
            owner=command.owner,
            decided_at=now,
            supersedes=command.supersedes,
            history=(
                {
                    "at": now,
                    "actor": command.owner,
                    "action": f"ring:{command.ring.value}",
                    "note": "",
                },
            ),
        )
        self._save(record)
        if command.supersedes:
            self._supersede(command.supersedes)
        return record

    def update_ring(self, command: UpdateTechRadarRingCommandV1, actor: str) -> TechRadarEntryV1:
        safe_id = _validate_record_id(command.entry_id)
        current = self.load(safe_id)
        if current is None:
            raise FileNotFoundError(f"tech_radar entry not found: {safe_id}")
        history = (
            *current.history,
            {
                "at": _utc_now(),
                "actor": actor,
                "action": f"ring:{command.ring.value}",
                "note": command.note,
            },
        )
        next_record = TechRadarEntryV1(
            entry_id=current.entry_id,
            library=current.library,
            ring=command.ring,
            rationale=current.rationale,
            owner=current.owner,
            decided_at=current.decided_at,
            supersedes=current.supersedes,
            history=history,
        )
        self._save(next_record)
        return next_record

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list(self, *, ring: TechRadarRing | None = None) -> list[TechRadarEntryV1]:
        records: list[TechRadarEntryV1] = []
        for path in sorted(self._dir.glob("*.json")):
            record = self._load_path(path)
            if record is None:
                continue
            if ring and record.ring != ring:
                continue
            records.append(record)
        return records

    def load(self, entry_id: str) -> TechRadarEntryV1 | None:
        safe_id = _validate_record_id(entry_id)
        path = self._dir / f"{safe_id}.json"
        if not path.exists():
            return None
        return self._load_path(path)

    def summarize(self) -> dict[str, Any]:
        records = self.list()
        by_ring: dict[str, int] = {r.value: 0 for r in TechRadarRing}
        for record in records:
            by_ring[record.ring.value] += 1
        return {"total": len(records), "by_ring": by_ring}

    def check_stack_policy(self, libraries: builtins.list[str]) -> builtins.list[StackPolicyViolationV1]:
        """Return a violation for each requested library on a hold/deprecated ring.

        The latest (most-recently-decided) entry per library wins, so a library
        moved back to ``adopt`` after a ``hold`` is no longer a violation.
        """
        wanted = {_normalize_library(lib) for lib in libraries if _normalize_library(lib)}
        if not wanted:
            return []
        latest: dict[str, TechRadarEntryV1] = {}
        for record in self.list():
            key = _normalize_library(record.library)
            if key not in wanted:
                continue
            prior = latest.get(key)
            # Total-order tie-break: newest decided_at wins, and entry_id breaks
            # a decided_at tie deterministically (independent of glob/iteration
            # order). Without the entry_id key a same-microsecond collision would
            # let whichever file sorts last shadow a genuinely-newer decision.
            if prior is None or (record.decided_at, record.entry_id) > (prior.decided_at, prior.entry_id):
                latest[key] = record
        violations: list[StackPolicyViolationV1] = []
        for record in latest.values():
            if record.ring in _VIOLATION_RINGS:
                violations.append(
                    StackPolicyViolationV1(
                        library=record.library,
                        ring=record.ring,
                        rationale=record.rationale,
                    )
                )
        return sorted(violations, key=lambda v: v.library)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _supersede(self, entry_id: str) -> None:
        try:
            current = self.load(entry_id)
        except ValueError:
            return
        if current is None or current.ring == TechRadarRing.DEPRECATED:
            return
        history = (
            *current.history,
            {
                "at": _utc_now(),
                "actor": current.owner,
                "action": "ring:deprecated",
                "note": "superseded",
            },
        )
        self._save(
            TechRadarEntryV1(
                entry_id=current.entry_id,
                library=current.library,
                ring=TechRadarRing.DEPRECATED,
                rationale=current.rationale,
                owner=current.owner,
                decided_at=current.decided_at,
                supersedes=current.supersedes,
                history=history,
            )
        )

    def _save(self, record: TechRadarEntryV1) -> None:
        safe_id = _validate_record_id(record.entry_id)
        path = self._dir / f"{safe_id}.json"
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(record.to_dict(), handle, ensure_ascii=False, indent=2)
        tmp.replace(path)

    def _load_path(self, path: Path) -> TechRadarEntryV1 | None:
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        return TechRadarEntryV1(
            entry_id=str(data.get("entry_id") or path.stem),
            library=str(data.get("library") or ""),
            ring=_coerce_ring(data.get("ring")),
            rationale=str(data.get("rationale") or ""),
            owner=str(data.get("owner") or ""),
            decided_at=str(data.get("decided_at") or ""),
            supersedes=(str(data.get("supersedes") or "").strip() or None),
            history=tuple(data.get("history") or ()),
        )


def build_tech_radar_event(
    *,
    entry_id: str,
    workspace: str,
    action: str,
    actor: str,
    note: str = "",
) -> TechRadarEventV1:
    """Build a ``TechRadarEventV1`` stamped with the current UTC time."""

    at = _utc_now()
    return TechRadarEventV1(
        event_id=f"radarevt_{_nonce()}",
        entry_id=entry_id,
        workspace=workspace,
        action=action,
        actor=actor,
        at=at,
        note=note,
    )


__all__ = ["TechRadarLedger", "build_tech_radar_event"]
