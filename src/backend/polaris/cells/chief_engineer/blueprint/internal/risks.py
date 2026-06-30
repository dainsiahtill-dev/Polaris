"""Risk Register storage and helpers.

Persists Risk Register entries as one JSON file per entry under
``runtime/risks/{risk_id}.json``. Reuses the atomic write pattern from
``BlueprintPersistence`` to keep the storage layer uniform.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.cells.chief_engineer.blueprint.public.contracts import (
    RegisterRiskCommandV1,
    RiskEventV1,
    RiskRecordV1,
    RiskSeverity,
    RiskStatus,
    UpdateRiskStatusCommandV1,
)
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.storage import resolve_logical_path

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_SAFE_ID_FULLMATCH = re.compile(r"^[A-Za-z0-9_.-]+$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _nonce() -> str:
    """Build a collision-resistant nonce (microsecond UTC + random suffix)."""

    stamp = _utc_now().replace(":", "").replace("-", "").replace(".", "")
    return f"{stamp}{uuid.uuid4().hex[:8]}"


def _safe_token(value: str) -> str:
    token = _SAFE_ID_RE.sub("_", str(value or "").strip()).strip("._-")
    return token[:80] or "risk"


def _validate_record_id(value: str) -> str:
    """Reject ids that could escape the storage directory (path traversal).

    ``risk_id`` arrives from untrusted HTTP path parameters, so it must be a
    bare safe token: alphanumerics plus ``_ . -``, no path separators and no
    ``..`` sequence. Fail-closed: anything else raises ``ValueError`` (the
    HTTP layer maps that to 400).
    """
    token = str(value or "").strip()
    if not token or ".." in token or not _SAFE_ID_FULLMATCH.match(token):
        raise ValueError(f"unsafe risk_id: {value!r}")
    return token


def _coerce_severity(value: Any) -> RiskSeverity:
    if isinstance(value, RiskSeverity):
        return value
    try:
        return RiskSeverity(str(value or "medium").strip().lower() or "medium")
    except ValueError:
        return RiskSeverity.MEDIUM


def _coerce_status(value: Any) -> RiskStatus:
    if isinstance(value, RiskStatus):
        return value
    try:
        return RiskStatus(str(value or "open").strip().lower() or "open")
    except ValueError:
        return RiskStatus.OPEN


def _coerce_links(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return (str(value).strip(),) if str(value).strip() else ()


class RiskRegister:
    """Workspace-scoped Risk Register storage.

    Each risk lives in its own JSON file under ``runtime/risks``. Writes
    are atomic (temp-file + replace) to match ``BlueprintPersistence``.
    """

    def __init__(self, workspace: str, *, ensure_directory: bool = True) -> None:
        self._dir = Path(resolve_logical_path(workspace, "runtime/risks"))
        self._fs = KernelFileSystem(workspace, get_default_adapter())
        if ensure_directory:
            self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def register(self, command: RegisterRiskCommandV1) -> RiskRecordV1:
        now = _utc_now()
        risk_id = f"risk_{_safe_token(command.task_id)}_{_nonce()}"
        record = RiskRecordV1(
            risk_id=risk_id,
            task_id=command.task_id,
            title=command.title,
            severity=command.severity,
            owner=command.owner,
            mitigation=command.mitigation,
            status=RiskStatus.OPEN,
            detected_at=now,
            links=command.links,
            supersedes=command.supersedes,
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

    def update_status(self, command: UpdateRiskStatusCommandV1, actor: str) -> RiskRecordV1:
        safe_id = _validate_record_id(command.risk_id)
        current = self.load(safe_id)
        if current is None:
            raise FileNotFoundError(f"risk not found: {safe_id}")
        history = (
            *current.history,
            {
                "at": _utc_now(),
                "actor": actor,
                "action": f"status:{command.status.value}",
                "note": command.note,
            },
        )
        next_record = RiskRecordV1(
            risk_id=current.risk_id,
            task_id=current.task_id,
            title=current.title,
            severity=current.severity,
            owner=current.owner,
            mitigation=current.mitigation,
            status=command.status,
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

    def list(
        self,
        *,
        task_id: str | None = None,
        severity: RiskSeverity | None = None,
        status: RiskStatus | None = None,
    ) -> list[RiskRecordV1]:
        records: list[RiskRecordV1] = []
        for path in sorted(self._dir.glob("*.json")):
            record = self._load_path(path)
            if record is None:
                continue
            if task_id and record.task_id != task_id:
                continue
            if severity and record.severity != severity:
                continue
            if status and record.status != status:
                continue
            records.append(record)
        return records

    def load(self, risk_id: str) -> RiskRecordV1 | None:
        safe_id = _validate_record_id(risk_id)
        path = self._dir / f"{safe_id}.json"
        if not path.exists():
            return None
        return self._load_path(path)

    def summarize(self, *, task_id: str | None = None) -> dict[str, Any]:
        records = self.list(task_id=task_id)
        by_severity: dict[str, int] = {s.value: 0 for s in RiskSeverity}
        by_status: dict[str, int] = {s.value: 0 for s in RiskStatus}
        open_blocker = 0
        for record in records:
            by_severity[record.severity.value] += 1
            by_status[record.status.value] += 1
            if record.status == RiskStatus.OPEN and record.severity in {
                RiskSeverity.BLOCKER,
                RiskSeverity.CRITICAL,
            }:
                open_blocker += 1
        return {
            "total": len(records),
            "by_severity": by_severity,
            "by_status": by_status,
            "open_critical_or_blocker": open_blocker,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _save(self, record: RiskRecordV1) -> None:
        safe_id = _validate_record_id(record.risk_id)
        self._fs.write_json_atomic(
            f"runtime/risks/{safe_id}.json",
            record.to_dict(),
            ensure_ascii=False,
            indent=2,
        )

    def _load_path(self, path: Path) -> RiskRecordV1 | None:
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        return RiskRecordV1(
            risk_id=str(data.get("risk_id") or path.stem),
            task_id=str(data.get("task_id") or ""),
            title=str(data.get("title") or ""),
            severity=_coerce_severity(data.get("severity")),
            owner=str(data.get("owner") or ""),
            mitigation=str(data.get("mitigation") or ""),
            status=_coerce_status(data.get("status")),
            detected_at=str(data.get("detected_at") or ""),
            links=_coerce_links(data.get("links")),
            supersedes=(str(data.get("supersedes") or "").strip() or None),
            history=tuple(data.get("history") or ()),
        )


def build_risk_event(
    *,
    risk_id: str,
    workspace: str,
    action: str,
    actor: str,
    note: str = "",
) -> RiskEventV1:
    """Build a ``RiskEventV1`` stamped with the current UTC time."""

    at = _utc_now()
    return RiskEventV1(
        event_id=f"riskevt_{_nonce()}",
        risk_id=risk_id,
        workspace=workspace,
        action=action,
        actor=actor,
        at=at,
        note=note,
    )


__all__ = [
    "RiskRegister",
    "build_risk_event",
]
