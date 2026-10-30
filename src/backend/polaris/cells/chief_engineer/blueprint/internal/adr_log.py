"""Architecture Decision Log storage and helpers.

A human-facing Architecture Decision Record (ADR) ledger — distinct from
the internal construction-plan ADR *compiler* in ``adr_store.py``. Each
decision is one JSON file under ``runtime/adr_log/{adr_id}.json``, using
the same atomic / UTF-8 / traversal-hardened pattern as the Risk Register.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.cells.chief_engineer.blueprint.public.contracts import (
    ADREventV1,
    ADRRecordV1,
    ADRStatus,
    RegisterADRCommandV1,
    UpdateADRStatusCommandV1,
)
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.security.record_id_guard import validate_storage_record_id
from polaris.kernelone.storage import resolve_logical_path

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _nonce() -> str:
    """Build a collision-resistant nonce (microsecond UTC + random suffix)."""

    stamp = _utc_now().replace(":", "").replace("-", "").replace(".", "")
    return f"{stamp}{uuid.uuid4().hex[:8]}"


def _safe_token(value: str) -> str:
    token = _SAFE_ID_RE.sub("_", str(value or "").strip()).strip("._-")
    return token[:80] or "adr"


def _validate_record_id(value: str) -> str:
    """Reject ids that could escape the storage directory (path traversal).

    Delegates to the canonical SSoT in
    ``polaris.kernelone.security.record_id_guard``; kept as a thin local
    wrapper so existing call sites and test monkeypatch targets resolve
    unchanged. Fail-closed: anything but a bare safe token raises.
    """
    return validate_storage_record_id(value, label="adr_id")


def _coerce_status(value: Any) -> ADRStatus:
    if isinstance(value, ADRStatus):
        return value
    try:
        return ADRStatus(str(value or "proposed").strip().lower() or "proposed")
    except ValueError:
        return ADRStatus.PROPOSED


def _coerce_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return (str(value).strip(),) if str(value).strip() else ()


class ADRDecisionLog:
    """Workspace-scoped Architecture Decision Record ledger."""

    def __init__(self, workspace: str, *, ensure_directory: bool = True) -> None:
        self._dir = Path(resolve_logical_path(workspace, "runtime/adr_log"))
        self._fs = KernelFileSystem(workspace, get_default_adapter())
        if ensure_directory:
            self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def register(self, command: RegisterADRCommandV1) -> ADRRecordV1:
        now = _utc_now()
        adr_id = f"adr_{_safe_token(command.title)}_{_nonce()}"
        record = ADRRecordV1(
            adr_id=adr_id,
            title=command.title,
            status=ADRStatus.PROPOSED,
            context=command.context,
            decision=command.decision,
            consequences=command.consequences,
            owner=command.owner,
            decided_at=now,
            alternatives=command.alternatives,
            related_task_ids=command.related_task_ids,
            supersedes=command.supersedes,
            history=(
                {
                    "at": now,
                    "actor": command.owner,
                    "action": "proposed",
                    "note": "",
                },
            ),
        )
        self._save(record)
        # Mark a superseded predecessor, if any.
        if command.supersedes:
            self._mark_superseded(command.supersedes, by_adr_id=adr_id, actor=command.owner)
        return record

    def update_status(self, command: UpdateADRStatusCommandV1, actor: str) -> ADRRecordV1:
        safe_id = _validate_record_id(command.adr_id)
        current = self.load(safe_id)
        if current is None:
            raise FileNotFoundError(f"adr not found: {safe_id}")
        history = (
            *current.history,
            {
                "at": _utc_now(),
                "actor": actor,
                "action": f"status:{command.status.value}",
                "note": command.note,
            },
        )
        next_record = ADRRecordV1(
            adr_id=current.adr_id,
            title=current.title,
            status=command.status,
            context=current.context,
            decision=current.decision,
            consequences=current.consequences,
            owner=current.owner,
            decided_at=current.decided_at,
            alternatives=current.alternatives,
            related_task_ids=current.related_task_ids,
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
        status: ADRStatus | None = None,
        task_id: str | None = None,
    ) -> list[ADRRecordV1]:
        records: list[ADRRecordV1] = []
        for path in sorted(self._dir.glob("*.json")):
            record = self._load_path(path)
            if record is None:
                continue
            if status and record.status != status:
                continue
            if task_id and task_id not in record.related_task_ids:
                continue
            records.append(record)
        return records

    def load(self, adr_id: str) -> ADRRecordV1 | None:
        safe_id = _validate_record_id(adr_id)
        path = self._dir / f"{safe_id}.json"
        if not path.exists():
            return None
        return self._load_path(path)

    def summarize(self) -> dict[str, Any]:
        records = self.list()
        by_status: dict[str, int] = {s.value: 0 for s in ADRStatus}
        for record in records:
            by_status[record.status.value] += 1
        return {"total": len(records), "by_status": by_status}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _mark_superseded(self, adr_id: str, *, by_adr_id: str, actor: str) -> None:
        try:
            current = self.load(adr_id)
        except ValueError:
            return
        if current is None or current.status == ADRStatus.SUPERSEDED:
            return
        history = (
            *current.history,
            {
                "at": _utc_now(),
                "actor": actor,
                "action": "status:superseded",
                "note": f"superseded by {by_adr_id}",
            },
        )
        self._save(
            ADRRecordV1(
                adr_id=current.adr_id,
                title=current.title,
                status=ADRStatus.SUPERSEDED,
                context=current.context,
                decision=current.decision,
                consequences=current.consequences,
                owner=current.owner,
                decided_at=current.decided_at,
                alternatives=current.alternatives,
                related_task_ids=current.related_task_ids,
                supersedes=current.supersedes,
                history=history,
            )
        )

    def _save(self, record: ADRRecordV1) -> None:
        safe_id = _validate_record_id(record.adr_id)
        self._fs.write_json_atomic(
            f"runtime/adr_log/{safe_id}.json",
            record.to_dict(),
            ensure_ascii=False,
            indent=2,
        )

    def _load_path(self, path: Path) -> ADRRecordV1 | None:
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        return ADRRecordV1(
            adr_id=str(data.get("adr_id") or path.stem),
            title=str(data.get("title") or ""),
            status=_coerce_status(data.get("status")),
            context=str(data.get("context") or ""),
            decision=str(data.get("decision") or ""),
            consequences=str(data.get("consequences") or ""),
            owner=str(data.get("owner") or ""),
            decided_at=str(data.get("decided_at") or ""),
            alternatives=_coerce_str_tuple(data.get("alternatives")),
            related_task_ids=_coerce_str_tuple(data.get("related_task_ids")),
            supersedes=(str(data.get("supersedes") or "").strip() or None),
            history=tuple(data.get("history") or ()),
        )


def build_adr_event(
    *,
    adr_id: str,
    workspace: str,
    action: str,
    actor: str,
    note: str = "",
) -> ADREventV1:
    """Build an ``ADREventV1`` stamped with the current UTC time."""

    at = _utc_now()
    return ADREventV1(
        event_id=f"adrevt_{_nonce()}",
        adr_id=adr_id,
        workspace=workspace,
        action=action,
        actor=actor,
        at=at,
        note=note,
    )


__all__ = [
    "ADRDecisionLog",
    "build_adr_event",
]
