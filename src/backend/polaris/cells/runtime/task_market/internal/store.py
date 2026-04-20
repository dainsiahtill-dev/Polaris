"""Storage backend factory for ``runtime.task_market``.

Provides a unified interface over either the JSON file backend
(``TaskMarketJSONStore``) or the SQLite WAL backend
(``TaskMarketSQLiteStore``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias

from polaris.kernelone.fs.text_ops import write_json_atomic
from polaris.kernelone.storage import resolve_runtime_path

from .models import TaskWorkItemRecord

if TYPE_CHECKING:
    from .store_sqlite import TaskMarketSQLiteStore

BackendType = Literal["json", "sqlite"]


class TaskMarketStoreProtocol(Protocol):
    def load_items(self) -> dict[str, TaskWorkItemRecord]: ...

    def save_items(self, items: dict[str, TaskWorkItemRecord]) -> None: ...

    def append_dead_letter(self, payload: dict[str, object]) -> None: ...

    def load_dead_letters(self, *, limit: int = 200) -> list[dict[str, object]]: ...

    def append_transition(
        self,
        task_id: str,
        from_status: str,
        to_status: str,
        event_type: str,
        worker_id: str,
        lease_token: str,
        version: int,
        metadata: dict[str, Any],
    ) -> None: ...

    def load_transitions(self, task_id: str) -> list[dict[str, object]]: ...

    def append_outbox_message(self, record: dict[str, object]) -> None: ...

    def load_outbox_messages(
        self,
        workspace: str,
        *,
        statuses: tuple[str, ...] = ("pending", "failed"),
        limit: int = 200,
    ) -> list[dict[str, object]]: ...

    def mark_outbox_message_sent(
        self,
        workspace: str,
        outbox_id: str,
        *,
        delivered_at: str = "",
    ) -> None: ...

    def mark_outbox_message_failed(
        self,
        workspace: str,
        outbox_id: str,
        *,
        error_message: str,
        failed_at: str = "",
    ) -> None: ...

    def upsert_human_review_request(self, record: dict[str, object]) -> None: ...

    def load_human_review_requests(
        self,
        workspace: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, object]]: ...

    def upsert_plan_revision(self, record: dict[str, object]) -> None: ...

    def load_plan_revisions(
        self,
        workspace: str,
        *,
        plan_id: str = "",
        limit: int = 200,
    ) -> list[dict[str, object]]: ...

    def append_change_order(self, record: dict[str, object]) -> None: ...

    def load_change_orders(
        self,
        workspace: str,
        *,
        plan_id: str = "",
        limit: int = 200,
    ) -> list[dict[str, object]]: ...

    def begin(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def save_items_and_outbox_atomic(
        self,
        *,
        items: dict[str, TaskWorkItemRecord],
        transitions: list[dict[str, Any]],
        outbox_records: list[dict[str, Any]],
    ) -> None: ...


# ---------------------------------------------------------------------------
# JSON Backend (kept for dev / fallback)
# ---------------------------------------------------------------------------


class TaskMarketJSONStore:
    """Workspace-local JSON file store (original implementation)."""

    def __init__(self, workspace: str) -> None:
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise ValueError("workspace is required")
        self._workspace = workspace_token
        self._root = Path(resolve_runtime_path(self._workspace, "runtime/task_market"))
        self._items_path = Path(resolve_runtime_path(self._workspace, "runtime/task_market/work_items.json"))
        self._dead_letters_path = Path(resolve_runtime_path(self._workspace, "runtime/task_market/dead_letters.json"))
        self._plan_revisions_path = Path(
            resolve_runtime_path(self._workspace, "runtime/task_market/plan_revisions.json")
        )
        self._change_orders_path = Path(resolve_runtime_path(self._workspace, "runtime/task_market/change_orders.json"))
        self._outbox_messages_path = Path(
            resolve_runtime_path(self._workspace, "runtime/task_market/outbox_messages.json")
        )

    @property
    def items_path(self) -> Path:
        return self._items_path

    @property
    def dead_letters_path(self) -> Path:
        return self._dead_letters_path

    def load_items(self) -> dict[str, TaskWorkItemRecord]:
        payload = self._read_json(self._items_path, default={"schema_version": 1, "items": []})
        rows_any = payload.get("items")
        rows: list[object] = rows_any if isinstance(rows_any, list) else []
        items: dict[str, TaskWorkItemRecord] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            item = TaskWorkItemRecord.from_dict(row)
            if not item.task_id:
                continue
            items[item.task_id] = item
        return items

    def save_items(self, items: dict[str, TaskWorkItemRecord]) -> None:
        rows = [item.to_dict() for item in items.values()]
        rows.sort(key=lambda entry: str(entry.get("task_id") or ""))
        payload = {"schema_version": 1, "items": rows}
        self._root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(str(self._items_path), payload)

    def append_dead_letter(self, payload: dict[str, object]) -> None:
        entries = self.load_dead_letters(limit=10_000)
        entries.append(dict(payload))
        data = {"schema_version": 1, "items": entries}
        self._root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(str(self._dead_letters_path), data)

    def load_dead_letters(self, *, limit: int = 200) -> list[dict[str, object]]:
        payload = self._read_json(self._dead_letters_path, default={"schema_version": 1, "items": []})
        rows_any = payload.get("items")
        rows: list[object] = rows_any if isinstance(rows_any, list) else []
        records = [dict(row) for row in rows if isinstance(row, dict)]
        if limit < 1:
            return []
        return records[-limit:]

    # ---- stubs for SQLite-only methods (no-op on JSON backend) ----

    def append_transition(
        self,
        task_id: str,
        from_status: str,
        to_status: str,
        event_type: str,
        worker_id: str,
        lease_token: str,
        version: int,
        metadata: dict[str, Any],
    ) -> None:
        pass

    def load_transitions(self, task_id: str) -> list[dict[str, object]]:
        return []

    def upsert_human_review_request(self, record: dict[str, object]) -> None:
        pass

    def load_human_review_requests(self, workspace: str, *, limit: int = 100) -> list[dict[str, object]]:
        return []

    def append_outbox_message(self, record: dict[str, object]) -> None:
        workspace = str(record.get("workspace") or "").strip()
        outbox_id = str(record.get("outbox_id") or "").strip()
        if not workspace or not outbox_id:
            return
        entries = self.load_outbox_messages(workspace=workspace, statuses=("pending", "failed", "sent"), limit=10_000)
        replaced = False
        for index, entry in enumerate(entries):
            if str(entry.get("outbox_id") or "").strip() == outbox_id:
                entries[index] = dict(record)
                replaced = True
                break
        if not replaced:
            entries.append(dict(record))
        payload = {"schema_version": 1, "items": entries}
        self._root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(str(self._outbox_messages_path), payload)

    def load_outbox_messages(
        self,
        workspace: str,
        *,
        statuses: tuple[str, ...] = ("pending", "failed"),
        limit: int = 200,
    ) -> list[dict[str, object]]:
        payload = self._read_json(self._outbox_messages_path, default={"schema_version": 1, "items": []})
        rows_any = payload.get("items")
        rows: list[object] = rows_any if isinstance(rows_any, list) else []
        allowed = {str(token).strip().lower() for token in statuses if str(token).strip()}
        records = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("workspace") or "").strip() != workspace:
                continue
            status = str(row.get("status") or "").strip().lower()
            if allowed and status not in allowed:
                continue
            records.append(dict(row))
        records.sort(key=lambda row: str(row.get("created_at") or ""), reverse=False)
        if limit < 1:
            return []
        return records[:limit]

    def mark_outbox_message_sent(
        self,
        workspace: str,
        outbox_id: str,
        *,
        delivered_at: str = "",
    ) -> None:
        entries = self.load_outbox_messages(workspace=workspace, statuses=("pending", "failed", "sent"), limit=10_000)
        updated = False
        for entry in entries:
            if str(entry.get("outbox_id") or "").strip() != outbox_id:
                continue
            entry["status"] = "sent"
            entry["delivered_at"] = str(delivered_at or "").strip()
            updated = True
            break
        if not updated:
            return
        payload = {"schema_version": 1, "items": entries}
        self._root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(str(self._outbox_messages_path), payload)

    def mark_outbox_message_failed(
        self,
        workspace: str,
        outbox_id: str,
        *,
        error_message: str,
        failed_at: str = "",
    ) -> None:
        entries = self.load_outbox_messages(workspace=workspace, statuses=("pending", "failed", "sent"), limit=10_000)
        updated = False
        for entry in entries:
            if str(entry.get("outbox_id") or "").strip() != outbox_id:
                continue
            attempts = _safe_int(entry.get("attempts"), default=0, min_value=0) + 1
            entry["attempts"] = attempts
            entry["status"] = "failed"
            entry["last_error"] = str(error_message or "").strip()
            entry["failed_at"] = str(failed_at or "").strip()
            updated = True
            break
        if not updated:
            return
        payload = {"schema_version": 1, "items": entries}
        self._root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(str(self._outbox_messages_path), payload)

    def upsert_plan_revision(self, record: dict[str, object]) -> None:
        workspace = str(record.get("workspace") or "").strip()
        plan_id = str(record.get("plan_id") or "").strip()
        plan_revision_id = str(record.get("plan_revision_id") or "").strip()
        if not workspace or not plan_id or not plan_revision_id:
            return

        entries = self.load_plan_revisions(workspace=workspace, plan_id="", limit=10_000)
        replaced = False
        for index, entry in enumerate(entries):
            if (
                str(entry.get("workspace") or "").strip() == workspace
                and str(entry.get("plan_id") or "").strip() == plan_id
                and str(entry.get("plan_revision_id") or "").strip() == plan_revision_id
            ):
                entries[index] = dict(record)
                replaced = True
                break
        if not replaced:
            entries.append(dict(record))
        payload = {"schema_version": 1, "items": entries}
        self._root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(str(self._plan_revisions_path), payload)

    def load_plan_revisions(
        self,
        workspace: str,
        *,
        plan_id: str = "",
        limit: int = 200,
    ) -> list[dict[str, object]]:
        payload = self._read_json(self._plan_revisions_path, default={"schema_version": 1, "items": []})
        rows_any = payload.get("items")
        rows: list[object] = rows_any if isinstance(rows_any, list) else []
        records = [
            dict(row) for row in rows if isinstance(row, dict) and str(row.get("workspace") or "").strip() == workspace
        ]
        if plan_id:
            records = [row for row in records if str(row.get("plan_id") or "").strip() == plan_id]
        records.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        if limit < 1:
            return []
        return records[:limit]

    def append_change_order(self, record: dict[str, object]) -> None:
        payload = self._read_json(self._change_orders_path, default={"schema_version": 1, "items": []})
        rows_any = payload.get("items")
        rows: list[dict[str, object]] = (
            [dict(row) for row in rows_any if isinstance(row, dict)] if isinstance(rows_any, list) else []
        )
        rows.append(dict(record))
        data = {"schema_version": 1, "items": rows}
        self._root.mkdir(parents=True, exist_ok=True)
        write_json_atomic(str(self._change_orders_path), data)

    def load_change_orders(
        self,
        workspace: str,
        *,
        plan_id: str = "",
        limit: int = 200,
    ) -> list[dict[str, object]]:
        payload = self._read_json(self._change_orders_path, default={"schema_version": 1, "items": []})
        rows_any = payload.get("items")
        rows: list[object] = rows_any if isinstance(rows_any, list) else []
        records = [
            dict(row) for row in rows if isinstance(row, dict) and str(row.get("workspace") or "").strip() == workspace
        ]
        if plan_id:
            records = [row for row in records if str(row.get("plan_id") or "").strip() == plan_id]
        records.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        if limit < 1:
            return []
        return records[:limit]

    def begin(self) -> None:
        """No-op for JSON backend (no transaction support)."""
        pass

    def commit(self) -> None:
        """No-op for JSON backend."""
        pass

    def rollback(self) -> None:
        """No-op for JSON backend."""
        pass

    def save_items_and_outbox_atomic(
        self,
        *,
        items: dict[str, TaskWorkItemRecord],
        transitions: list[dict[str, Any]],
        outbox_records: list[dict[str, Any]],
    ) -> None:
        """Best-effort atomic write for JSON backend (falls back to sequential writes)."""
        self.save_items(items)
        for t in transitions:
            self.append_transition(
                task_id=t["task_id"],
                from_status=t["from_status"],
                to_status=t["to_status"],
                event_type=t["event_type"],
                worker_id=t["worker_id"],
                lease_token=t["lease_token"],
                version=t["version"],
                metadata=t["metadata"],
            )
        for rec in outbox_records:
            self.append_outbox_message(rec)

    def _read_json(self, path: Path, *, default: dict[str, object]) -> dict[str, object]:
        if not path.exists():
            return dict(default)
        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return dict(default)
        if not isinstance(raw, dict):
            return dict(default)
        return raw


def _safe_int(value: object, *, default: int, min_value: int = 0) -> int:
    try:
        parsed = int(str(value or "").strip() or str(default))
    except (TypeError, ValueError):
        return max(min_value, default)
    return max(min_value, parsed)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

# Re-export for backwards compatibility.
TaskMarketStore: TypeAlias = TaskMarketStoreProtocol


def get_store(workspace: str, backend: BackendType | None = None) -> TaskMarketStore:
    """Return the appropriate store backend.

    The backend can be overridden via the ``POLARIS_TASK_MARKET_STORE``
    environment variable or the ``backend`` parameter.  Default is ``sqlite``
    when the parameter is not provided.
    """
    if backend is None:
        backend = str(os.environ.get("POLARIS_TASK_MARKET_STORE", "sqlite") or "sqlite").strip().lower()  # type: ignore[assignment]

    if backend == "json":
        return TaskMarketJSONStore(workspace)

    # Default to SQLite.
    from .store_sqlite import TaskMarketSQLiteStore

    return TaskMarketSQLiteStore(workspace)


__all__ = [
    "BackendType",
    "TaskMarketJSONStore",
    "TaskMarketSQLiteStore",
    "TaskMarketStore",
    "get_store",
]
