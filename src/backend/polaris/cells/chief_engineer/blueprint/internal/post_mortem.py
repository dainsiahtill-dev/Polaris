"""Post-Mortem / Incident Review storage and helpers.

A 技术总监's blameless incident-review log: each post-mortem is one JSON
file under ``runtime/post_mortems/{incident_id}.json`` — same hardened,
traversal-guarded, UTF-8, atomic pattern as the Risk Register. Closes the
failure-learning loop alongside the (forward-looking) Risk Register.
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
    IncidentSeverity,
    ListPostMortemsQueryV1,
    PostMortemEventV1,
    PostMortemRecordV1,
    PostMortemStatus,
    RegisterPostMortemCommandV1,
    UpdatePostMortemStatusCommandV1,
)
from polaris.kernelone.storage import resolve_logical_path

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_SAFE_ID_FULLMATCH = re.compile(r"^[A-Za-z0-9_.-]+$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _nonce() -> str:
    stamp = _utc_now().replace(":", "").replace("-", "").replace(".", "")
    return f"{stamp}{uuid.uuid4().hex[:8]}"


def _safe_token(value: str) -> str:
    token = _SAFE_ID_RE.sub("_", str(value or "").strip()).strip("._-")
    return token[:80] or "incident"


def _validate_record_id(value: str) -> str:
    """Reject ids that could escape the storage directory (path traversal)."""

    token = str(value or "").strip()
    if not token or ".." in token or not _SAFE_ID_FULLMATCH.match(token):
        raise ValueError(f"unsafe incident_id: {value!r}")
    return token


def _coerce_severity(value: Any) -> IncidentSeverity:
    if isinstance(value, IncidentSeverity):
        return value
    try:
        return IncidentSeverity(str(value or "sev3").strip().lower() or "sev3")
    except ValueError:
        return IncidentSeverity.SEV3


def _coerce_status(value: Any) -> PostMortemStatus:
    if isinstance(value, PostMortemStatus):
        return value
    try:
        return PostMortemStatus(str(value or "draft").strip().lower() or "draft")
    except ValueError:
        return PostMortemStatus.DRAFT


def _coerce_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return (str(value).strip(),) if str(value).strip() else ()


class PostMortemLog:
    """Workspace-scoped post-mortem / incident-review log."""

    def __init__(self, workspace: str, *, ensure_directory: bool = True) -> None:
        self._dir = Path(resolve_logical_path(workspace, "runtime/post_mortems"))
        if ensure_directory:
            self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def register(self, command: RegisterPostMortemCommandV1) -> PostMortemRecordV1:
        now = _utc_now()
        incident_id = f"incident_{_safe_token(command.title)}_{_nonce()}"
        record = PostMortemRecordV1(
            incident_id=incident_id,
            title=command.title,
            severity=command.severity,
            summary=command.summary,
            root_cause=command.root_cause,
            impact=command.impact,
            status=PostMortemStatus.DRAFT,
            occurred_at=command.occurred_at,
            owner=command.owner,
            recorded_at=now,
            timeline=command.timeline,
            action_items=command.action_items,
            related_risk_ids=command.related_risk_ids,
            history=(
                {
                    "at": now,
                    "actor": command.owner,
                    "action": "recorded",
                    "note": "",
                },
            ),
        )
        self._save(record)
        return record

    def update_status(self, command: UpdatePostMortemStatusCommandV1, actor: str) -> PostMortemRecordV1:
        safe_id = _validate_record_id(command.incident_id)
        current = self.load(safe_id)
        if current is None:
            raise FileNotFoundError(f"post_mortem not found: {safe_id}")
        history = (
            *current.history,
            {
                "at": _utc_now(),
                "actor": actor,
                "action": f"status:{command.status.value}",
                "note": command.note,
            },
        )
        next_record = PostMortemRecordV1(
            incident_id=current.incident_id,
            title=current.title,
            severity=current.severity,
            summary=current.summary,
            root_cause=current.root_cause,
            impact=current.impact,
            status=command.status,
            occurred_at=current.occurred_at,
            owner=current.owner,
            recorded_at=current.recorded_at,
            timeline=current.timeline,
            action_items=current.action_items,
            related_risk_ids=current.related_risk_ids,
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
        severity: IncidentSeverity | None = None,
        status: PostMortemStatus | None = None,
    ) -> builtins.list[PostMortemRecordV1]:
        records: list[PostMortemRecordV1] = []
        for path in sorted(self._dir.glob("*.json")):
            record = self._load_path(path)
            if record is None:
                continue
            if severity and record.severity != severity:
                continue
            if status and record.status != status:
                continue
            records.append(record)
        return records

    def list_for_query(self, query: ListPostMortemsQueryV1) -> builtins.list[PostMortemRecordV1]:
        records: list[PostMortemRecordV1] = []
        for path in sorted(self._dir.glob("*.json")):
            record = self._load_path(path)
            if record is None:
                continue
            if query.severity and record.severity != query.severity:
                continue
            if query.status and record.status != query.status:
                continue
            records.append(record)
        return records

    def load(self, incident_id: str) -> PostMortemRecordV1 | None:
        safe_id = _validate_record_id(incident_id)
        path = self._dir / f"{safe_id}.json"
        if not path.exists():
            return None
        return self._load_path(path)

    def summarize(self) -> dict[str, Any]:
        """Aggregate counts for the post-mortem log.

        ``open_action_items`` is the total number of action items on records
        that are not yet ``CLOSED``. Action items are bare strings (no
        per-item done flag), so this is an upper-bound "outstanding
        follow-ups" signal — a ``PUBLISHED`` record whose actions are all
        done still contributes its full count until moved to ``CLOSED``.
        """
        records = self.list()
        by_severity: dict[str, int] = {s.value: 0 for s in IncidentSeverity}
        by_status: dict[str, int] = {s.value: 0 for s in PostMortemStatus}
        open_action_items = 0
        for record in records:
            by_severity[record.severity.value] += 1
            by_status[record.status.value] += 1
            if record.status != PostMortemStatus.CLOSED:
                open_action_items += len(record.action_items)
        return {
            "total": len(records),
            "by_severity": by_severity,
            "by_status": by_status,
            "open_action_items": open_action_items,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _save(self, record: PostMortemRecordV1) -> None:
        safe_id = _validate_record_id(record.incident_id)
        path = self._dir / f"{safe_id}.json"
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(record.to_dict(), handle, ensure_ascii=False, indent=2)
        tmp.replace(path)

    def _load_path(self, path: Path) -> PostMortemRecordV1 | None:
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        return PostMortemRecordV1(
            incident_id=str(data.get("incident_id") or path.stem),
            title=str(data.get("title") or ""),
            severity=_coerce_severity(data.get("severity")),
            summary=str(data.get("summary") or ""),
            root_cause=str(data.get("root_cause") or ""),
            impact=str(data.get("impact") or ""),
            status=_coerce_status(data.get("status")),
            occurred_at=str(data.get("occurred_at") or ""),
            owner=str(data.get("owner") or ""),
            recorded_at=str(data.get("recorded_at") or ""),
            timeline=_coerce_str_tuple(data.get("timeline")),
            action_items=_coerce_str_tuple(data.get("action_items")),
            related_risk_ids=_coerce_str_tuple(data.get("related_risk_ids")),
            history=tuple(data.get("history") or ()),
        )


def build_post_mortem_event(
    *,
    incident_id: str,
    workspace: str,
    action: str,
    actor: str,
    note: str = "",
) -> PostMortemEventV1:
    """Build a ``PostMortemEventV1`` stamped with the current UTC time."""

    at = _utc_now()
    return PostMortemEventV1(
        event_id=f"pmevt_{_nonce()}",
        incident_id=incident_id,
        workspace=workspace,
        action=action,
        actor=actor,
        at=at,
        note=note,
    )


__all__ = ["PostMortemLog", "build_post_mortem_event"]
