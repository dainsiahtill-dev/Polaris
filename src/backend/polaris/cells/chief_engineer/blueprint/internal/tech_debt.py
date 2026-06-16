"""Tech-Debt Ledger storage and helpers.

Persists Tech-Debt Ledger entries as one JSON file per entry under
``runtime/tech_debt/{debt_id}.json``. Reuses the atomic write pattern
from ``BlueprintPersistence`` to keep the storage layer uniform.
"""

from __future__ import annotations

import builtins
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.cells.chief_engineer.blueprint.public.contracts import (
    ListTechDebtQueryV1,
    RegisterTechDebtCommandV1,
    TechDebtEventV1,
    TechDebtRecordV1,
    TechDebtSeverity,
    TechDebtStatus,
    UpdateTechDebtStatusCommandV1,
)
from polaris.kernelone.storage import resolve_logical_path

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_token(value: str) -> str:
    token = _SAFE_ID_RE.sub("_", str(value or "").strip()).strip("._-")
    return token[:80] or "debt"


def _coerce_severity(value: Any) -> TechDebtSeverity:
    if isinstance(value, TechDebtSeverity):
        return value
    return TechDebtSeverity(str(value or "minor").strip().lower() or "minor")


def _coerce_status(value: Any) -> TechDebtStatus:
    if isinstance(value, TechDebtStatus):
        return value
    return TechDebtStatus(str(value or "registered").strip().lower() or "registered")


def _coerce_evidence(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return (str(value).strip(),) if str(value).strip() else ()


class TechDebtLedger:
    """Workspace-scoped Tech-Debt Ledger storage."""

    def __init__(self, workspace: str, *, ensure_directory: bool = True) -> None:
        self._dir = Path(resolve_logical_path(workspace, "runtime/tech_debt"))
        if ensure_directory:
            self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def register(self, command: RegisterTechDebtCommandV1) -> TechDebtRecordV1:
        now = _utc_now()
        nonce = now.replace(":", "").replace("-", "").replace(".", "")
        debt_id = f"debt_{_safe_token(command.surface)}_{nonce}"
        record = TechDebtRecordV1(
            debt_id=debt_id,
            title=command.title,
            description=command.description,
            severity=command.severity,
            surface=command.surface,
            owner=command.owner,
            evidence=command.evidence,
            status=TechDebtStatus.REGISTERED,
            registered_at=now,
            history=(
                {
                    "at": now,
                    "actor": command.owner,
                    "action": "registered",
                    "note": "",
                },
            ),
        )
        self._save(record)
        return record

    def update_status(
        self,
        command: UpdateTechDebtStatusCommandV1,
        actor: str,
    ) -> TechDebtRecordV1:
        current = self.load(command.debt_id)
        if current is None:
            raise FileNotFoundError(f"tech_debt not found: {command.debt_id}")
        history = (
            *current.history,
            {
                "at": _utc_now(),
                "actor": actor,
                "action": f"status:{command.status.value}",
                "note": command.note,
            },
        )
        next_record = TechDebtRecordV1(
            debt_id=current.debt_id,
            title=current.title,
            description=current.description,
            severity=current.severity,
            surface=current.surface,
            owner=current.owner,
            evidence=current.evidence,
            status=command.status,
            registered_at=current.registered_at,
            history=history,
        )
        self._save(next_record)
        return next_record

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list(
        self,
        *,
        severity: TechDebtSeverity | None = None,
        surface: str | None = None,
        status: TechDebtStatus | None = None,
    ) -> list[TechDebtRecordV1]:
        records: list[TechDebtRecordV1] = []
        for path in sorted(self._dir.glob("*.json")):
            record = self._load_path(path)
            if record is None:
                continue
            if severity and record.severity != severity:
                continue
            if status and record.status != status:
                continue
            if surface and record.surface != surface:
                continue
            records.append(record)
        return records

    def list_for_query(self, query: ListTechDebtQueryV1) -> builtins.list[TechDebtRecordV1]:
        records: builtins.list[TechDebtRecordV1] = []
        for path in sorted(self._dir.glob("*.json")):
            record = self._load_path(path)
            if record is None:
                continue
            if query.severity and record.severity != query.severity:
                continue
            if query.status and record.status != query.status:
                continue
            if query.surface and record.surface != query.surface:
                continue
            records.append(record)
        return records

    def load(self, debt_id: str) -> TechDebtRecordV1 | None:
        path = self._dir / f"{debt_id}.json"
        if not path.exists():
            return None
        return self._load_path(path)

    def summarize(self, *, surface: str | None = None) -> dict[str, Any]:
        records = self.list(surface=surface)
        by_severity: dict[str, int] = {s.value: 0 for s in TechDebtSeverity}
        by_status: dict[str, int] = {s.value: 0 for s in TechDebtStatus}
        for record in records:
            by_severity[record.severity.value] += 1
            by_status[record.status.value] += 1
        return {
            "total": len(records),
            "by_severity": by_severity,
            "by_status": by_status,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _save(self, record: TechDebtRecordV1) -> None:
        path = self._dir / f"{record.debt_id}.json"
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(record.to_dict(), handle, ensure_ascii=False, indent=2)
        tmp.replace(path)

    def _load_path(self, path: Path) -> TechDebtRecordV1 | None:
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        return TechDebtRecordV1(
            debt_id=str(data.get("debt_id") or path.stem),
            title=str(data.get("title") or ""),
            description=str(data.get("description") or ""),
            severity=_coerce_severity(data.get("severity")),
            surface=str(data.get("surface") or ""),
            owner=str(data.get("owner") or ""),
            evidence=_coerce_evidence(data.get("evidence")),
            status=_coerce_status(data.get("status")),
            registered_at=str(data.get("registered_at") or ""),
            history=tuple(data.get("history") or ()),
        )


def build_tech_debt_event(
    *,
    debt_id: str,
    workspace: str,
    action: str,
    actor: str,
    note: str = "",
) -> TechDebtEventV1:
    """Build a ``TechDebtEventV1`` stamped with the current UTC time."""

    at = _utc_now()
    nonce = at.replace(":", "").replace("-", "").replace(".", "")
    return TechDebtEventV1(
        event_id=f"debtevt_{nonce}",
        debt_id=debt_id,
        workspace=workspace,
        action=action,
        actor=actor,
        at=at,
        note=note,
    )


__all__ = [
    "TechDebtLedger",
    "build_tech_debt_event",
]
