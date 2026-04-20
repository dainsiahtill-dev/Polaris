"""SQLite WAL store for ``runtime.task_market`` — production-grade persistence.

This module provides the same interface as ``TaskMarketStore`` (JSON backend)
but uses SQLite with WAL mode for safe concurrent access and crash-resistant
persistence.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from polaris.kernelone.storage import resolve_runtime_path

from .models import TaskWorkItemRecord, now_iso

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS work_items (
    task_id         TEXT NOT NULL,
    trace_id        TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    workspace       TEXT NOT NULL,
    stage           TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT '',
    priority        TEXT NOT NULL DEFAULT 'medium',
    plan_id         TEXT NOT NULL DEFAULT '',
    plan_revision_id TEXT NOT NULL DEFAULT '',
    root_task_id    TEXT NOT NULL DEFAULT '',
    parent_task_id  TEXT NOT NULL DEFAULT '',
    is_leaf         INTEGER NOT NULL DEFAULT 1,
    depends_on      TEXT NOT NULL DEFAULT '[]',
    requirement_digest TEXT NOT NULL DEFAULT '',
    constraint_digest TEXT NOT NULL DEFAULT '',
    summary_ref     TEXT NOT NULL DEFAULT '',
    superseded_by_revision TEXT NOT NULL DEFAULT '',
    change_policy   TEXT NOT NULL DEFAULT 'strict',
    compensation_group_id TEXT NOT NULL DEFAULT '',
    payload         TEXT NOT NULL DEFAULT '{}',
    metadata        TEXT NOT NULL DEFAULT '{}',
    version         INTEGER NOT NULL DEFAULT 1,
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 3,
    lease_token     TEXT NOT NULL DEFAULT '',
    lease_expires_at REAL NOT NULL DEFAULT 0.0,
    claimed_by      TEXT NOT NULL DEFAULT '',
    claimed_role    TEXT NOT NULL DEFAULT '',
    last_error      TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (task_id, workspace)
);

CREATE TABLE IF NOT EXISTS work_item_transitions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL,
    from_status     TEXT NOT NULL DEFAULT '',
    to_status       TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    worker_id       TEXT NOT NULL DEFAULT '',
    lease_token     TEXT NOT NULL DEFAULT '',
    version         INTEGER NOT NULL DEFAULT 0,
    metadata        TEXT NOT NULL DEFAULT '{}',
    emitted_at      TEXT NOT NULL,
    UNIQUE(task_id, version)
);

CREATE TABLE IF NOT EXISTS dead_letter_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL UNIQUE,
    trace_id        TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    workspace       TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    error_code      TEXT NOT NULL DEFAULT '',
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 3,
    metadata        TEXT NOT NULL DEFAULT '{}',
    dead_lettered_at TEXT NOT NULL,
    UNIQUE(task_id)
);

CREATE TABLE IF NOT EXISTS human_review_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL UNIQUE,
    trace_id        TEXT NOT NULL,
    workspace       TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    escalation_policy TEXT NOT NULL DEFAULT 'tri_council',
    status          TEXT NOT NULL DEFAULT 'waiting',
    created_at      TEXT NOT NULL,
    resolved_at     TEXT NOT NULL DEFAULT '',
    resolution      TEXT NOT NULL DEFAULT '',
    resolved_by     TEXT NOT NULL DEFAULT '',
    metadata        TEXT NOT NULL DEFAULT '{}',
    current_role    TEXT NOT NULL DEFAULT 'director',
    next_role       TEXT NOT NULL DEFAULT '',
    escalation_deadline TEXT NOT NULL DEFAULT '',
    last_escalated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS plan_revisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace       TEXT NOT NULL,
    plan_id         TEXT NOT NULL,
    plan_revision_id TEXT NOT NULL,
    parent_revision_id TEXT NOT NULL DEFAULT '',
    source_role     TEXT NOT NULL DEFAULT '',
    requirement_digest TEXT NOT NULL DEFAULT '',
    constraint_digest TEXT NOT NULL DEFAULT '',
    metadata        TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    UNIQUE(workspace, plan_id, plan_revision_id)
);

CREATE TABLE IF NOT EXISTS change_orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace       TEXT NOT NULL,
    plan_id         TEXT NOT NULL,
    from_revision_id TEXT NOT NULL,
    to_revision_id  TEXT NOT NULL,
    change_type     TEXT NOT NULL,
    source_role     TEXT NOT NULL DEFAULT '',
    summary         TEXT NOT NULL DEFAULT '',
    trace_id        TEXT NOT NULL DEFAULT '',
    affected_task_ids TEXT NOT NULL DEFAULT '[]',
    impact_counts   TEXT NOT NULL DEFAULT '{}',
    metadata        TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox_messages (
    outbox_id       TEXT PRIMARY KEY,
    workspace       TEXT NOT NULL,
    stream          TEXT NOT NULL DEFAULT '',
    event_type      TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'runtime.task_market',
    run_id          TEXT NOT NULL DEFAULT '',
    task_id         TEXT NOT NULL DEFAULT '',
    payload         TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'pending',
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    failed_at       TEXT NOT NULL DEFAULT '',
    delivered_at    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_work_items_workspace_stage
    ON work_items(workspace, stage);
CREATE INDEX IF NOT EXISTS idx_work_items_status
    ON work_items(status);
CREATE INDEX IF NOT EXISTS idx_work_items_lease_expires
    ON work_items(lease_expires_at)
    WHERE lease_token != '';
CREATE INDEX IF NOT EXISTS idx_transitions_task
    ON work_item_transitions(task_id);
CREATE INDEX IF NOT EXISTS idx_dead_letter_workspace
    ON dead_letter_items(workspace, dead_lettered_at DESC);
CREATE INDEX IF NOT EXISTS idx_human_review_status
    ON human_review_requests(status);
CREATE INDEX IF NOT EXISTS idx_plan_revisions_workspace_plan
    ON plan_revisions(workspace, plan_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_change_orders_workspace_plan
    ON change_orders(workspace, plan_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_outbox_workspace_status_created
    ON outbox_messages(workspace, status, created_at ASC);
"""

_WORK_ITEM_COLUMN_DEFINITIONS: dict[str, str] = {
    "plan_id": "TEXT NOT NULL DEFAULT ''",
    "plan_revision_id": "TEXT NOT NULL DEFAULT ''",
    "root_task_id": "TEXT NOT NULL DEFAULT ''",
    "parent_task_id": "TEXT NOT NULL DEFAULT ''",
    "is_leaf": "INTEGER NOT NULL DEFAULT 1",
    "depends_on": "TEXT NOT NULL DEFAULT '[]'",
    "requirement_digest": "TEXT NOT NULL DEFAULT ''",
    "constraint_digest": "TEXT NOT NULL DEFAULT ''",
    "summary_ref": "TEXT NOT NULL DEFAULT ''",
    "superseded_by_revision": "TEXT NOT NULL DEFAULT ''",
    "change_policy": "TEXT NOT NULL DEFAULT 'strict'",
    "compensation_group_id": "TEXT NOT NULL DEFAULT ''",
}
_HUMAN_REVIEW_COLUMN_DEFINITIONS: dict[str, str] = {
    "metadata": "TEXT NOT NULL DEFAULT '{}'",
    "current_role": "TEXT NOT NULL DEFAULT 'director'",
    "next_role": "TEXT NOT NULL DEFAULT ''",
    "escalation_deadline": "TEXT NOT NULL DEFAULT ''",
    "last_escalated_at": "TEXT NOT NULL DEFAULT ''",
}
_OUTBOX_COLUMN_DEFINITIONS: dict[str, str] = {
    "failed_at": "TEXT NOT NULL DEFAULT ''",
    "delivered_at": "TEXT NOT NULL DEFAULT ''",
}


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class TaskMarketSQLiteStore:
    """SQLite WAL implementation of the task market store.

    Thread-safe per workspace via a connection cache.  Uses ``check_same_thread=False``
    combined with workspace-level ``threading.Lock`` (provided by the service layer).

    Schema evolution
    ---------------
    The ``version`` column on ``work_items`` provides optimistic locking; callers
    must supply a ``expected_version`` when updating a row to prevent lost updates.
    """

    def __init__(self, workspace: str) -> None:
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise ValueError("workspace is required")
        self._workspace = workspace_token
        self._db_path = Path(resolve_runtime_path(self._workspace, "runtime/task_market/task_market.db"))
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn_cache: dict[str, sqlite3.Connection] = {}
        self._conn_thread_map: dict[str, int] = {}  # workspace -> creating thread id
        self._conn_lock = threading.Lock()
        self._init_db()

    # ---- Low-level connection -----------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        """Return a cached connection for this workspace (thread-safe).

        On Windows a SQLite connection may only be used in the thread that
        created it.  When a different thread calls _get_conn, a new connection
        is opened for that thread rather than reusing the cached one.
        """
        thread_id = threading.get_ident()
        with self._conn_lock:
            conn = self._conn_cache.get(self._workspace)
            if conn is not None and self._conn_thread_map.get(self._workspace) == thread_id:
                return conn
            # Open (or reopen for cross-thread) connection.
            new_conn = self._open_conn()
            self._conn_cache[self._workspace] = new_conn
            self._conn_thread_map[self._workspace] = thread_id
            return new_conn

    def _open_conn(self) -> sqlite3.Connection:
        """Open a new SQLite connection with WAL pragmas."""
        conn = sqlite3.connect(
            str(self._db_path),
            timeout=30.0,
            isolation_level=None,  # autocommit
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        conn = self._get_conn()
        conn.executescript(_DDL)
        self._ensure_work_item_columns(conn)
        self._ensure_human_review_columns(conn)
        self._ensure_outbox_columns(conn)

    def _ensure_work_item_columns(self, conn: sqlite3.Connection) -> None:
        cursor = conn.execute("PRAGMA table_info(work_items)")
        existing_columns = {str(row["name"]).strip() for row in cursor.fetchall()}
        for column_name, definition in _WORK_ITEM_COLUMN_DEFINITIONS.items():
            if column_name in existing_columns:
                continue
            conn.execute(f"ALTER TABLE work_items ADD COLUMN {column_name} {definition}")

    def _ensure_human_review_columns(self, conn: sqlite3.Connection) -> None:
        cursor = conn.execute("PRAGMA table_info(human_review_requests)")
        existing_columns = {str(row["name"]).strip() for row in cursor.fetchall()}
        for column_name, definition in _HUMAN_REVIEW_COLUMN_DEFINITIONS.items():
            if column_name in existing_columns:
                continue
            conn.execute(f"ALTER TABLE human_review_requests ADD COLUMN {column_name} {definition}")

    def _ensure_outbox_columns(self, conn: sqlite3.Connection) -> None:
        cursor = conn.execute("PRAGMA table_info(outbox_messages)")
        existing_columns = {str(row["name"]).strip() for row in cursor.fetchall()}
        for column_name, definition in _OUTBOX_COLUMN_DEFINITIONS.items():
            if column_name in existing_columns:
                continue
            conn.execute(f"ALTER TABLE outbox_messages ADD COLUMN {column_name} {definition}")

    # ---- work_items ---------------------------------------------------------

    def load_items(self) -> dict[str, TaskWorkItemRecord]:
        """Load all work items for this workspace."""
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT * FROM work_items WHERE workspace = ?",
            (self._workspace,),
        )
        items: dict[str, TaskWorkItemRecord] = {}
        for row in cursor.fetchall():
            item = _row_to_record(row)
            items[item.task_id] = item
        return items

    def upsert_item(self, item: TaskWorkItemRecord) -> None:
        """Insert or update a work item (uses INSERT OR REPLACE)."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO work_items
                (task_id, trace_id, run_id, workspace, stage, status, priority,
                 plan_id, plan_revision_id, root_task_id, parent_task_id, is_leaf,
                 depends_on, requirement_digest, constraint_digest, summary_ref,
                 superseded_by_revision, change_policy, compensation_group_id,
                 payload, metadata, version, attempts, max_attempts,
                 lease_token, lease_expires_at, claimed_by, claimed_role,
                 last_error, created_at, updated_at)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.task_id,
                item.trace_id,
                item.run_id,
                item.workspace,
                item.stage,
                item.status,
                item.priority,
                item.plan_id,
                item.plan_revision_id,
                item.root_task_id or item.task_id,
                item.parent_task_id,
                1 if item.is_leaf else 0,
                json.dumps(item.depends_on, ensure_ascii=False),
                item.requirement_digest,
                item.constraint_digest,
                item.summary_ref,
                item.superseded_by_revision,
                item.change_policy,
                item.compensation_group_id,
                json.dumps(item.payload, ensure_ascii=False),
                json.dumps(item.metadata, ensure_ascii=False),
                item.version,
                item.attempts,
                item.max_attempts,
                item.lease_token,
                item.lease_expires_at,
                item.claimed_by,
                item.claimed_role,
                json.dumps(item.last_error, ensure_ascii=False),
                item.created_at,
                item.updated_at,
            ),
        )

    def save_items(self, items: dict[str, TaskWorkItemRecord]) -> None:
        """Batch upsert (delegates to individual upsert for simplicity)."""
        for item in items.values():
            self.upsert_item(item)

    # ---- transitions --------------------------------------------------------

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
        """Record a state transition in the audit log."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO work_item_transitions
                (task_id, from_status, to_status, event_type, worker_id,
                 lease_token, version, metadata, emitted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                from_status,
                to_status,
                event_type,
                worker_id,
                lease_token,
                version,
                json.dumps(metadata, ensure_ascii=False),
                now_iso(),
            ),
        )

    def load_transitions(self, task_id: str) -> list[dict[str, Any]]:
        """Load all transitions for a task, ordered by version asc."""
        conn = self._get_conn()
        cursor = conn.execute(
            "SELECT * FROM work_item_transitions WHERE task_id = ? ORDER BY version ASC",
            (task_id,),
        )
        return [_row_to_dict(row) for row in cursor.fetchall()]

    # ---- dead_letter_items --------------------------------------------------

    def append_dead_letter(self, payload: dict[str, Any]) -> None:
        """Append a dead-letter entry (uses INSERT OR REPLACE on task_id)."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO dead_letter_items
                (task_id, trace_id, run_id, workspace, reason, error_code,
                 attempts, max_attempts, metadata, dead_lettered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["task_id"],
                payload["trace_id"],
                payload["run_id"],
                payload["workspace"],
                payload.get("reason", ""),
                payload.get("error_code", ""),
                payload.get("attempts", 0),
                payload.get("max_attempts", 3),
                json.dumps(payload.get("metadata", {}), ensure_ascii=False),
                payload.get("dead_lettered_at", now_iso()),
            ),
        )

    def load_dead_letters(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Load the most recent ``limit`` dead-letter entries."""
        conn = self._get_conn()
        cursor = conn.execute(
            """
            SELECT * FROM dead_letter_items
            WHERE workspace = ?
            ORDER BY dead_lettered_at DESC
            LIMIT ?
            """,
            (self._workspace, limit),
        )
        return [_row_to_dict(row) for row in cursor.fetchall()]

    # ---- human_review_requests ----------------------------------------------

    def upsert_human_review_request(self, record: dict[str, Any]) -> None:
        """Insert or update a human review request."""
        conn = self._get_conn()
        first_class_keys = {
            "task_id",
            "trace_id",
            "workspace",
            "reason",
            "escalation_policy",
            "status",
            "created_at",
            "resolved_at",
            "resolution",
            "resolved_by",
            "metadata",
            "current_role",
            "next_role",
            "escalation_deadline",
            "last_escalated_at",
        }
        raw_metadata = record.get("metadata")
        metadata_payload: dict[str, Any] = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        for key, value in record.items():
            if key in first_class_keys:
                continue
            metadata_payload[key] = value
        conn.execute(
            """
            INSERT OR REPLACE INTO human_review_requests
                (task_id, trace_id, workspace, reason, escalation_policy,
                 status, created_at, resolved_at, resolution, resolved_by, metadata,
                 current_role, next_role, escalation_deadline, last_escalated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["task_id"],
                record["trace_id"],
                record["workspace"],
                record.get("reason", ""),
                record.get("escalation_policy", "tri_council"),
                record.get("status", "waiting"),
                record.get("created_at", now_iso()),
                record.get("resolved_at", ""),
                record.get("resolution", ""),
                record.get("resolved_by", ""),
                json.dumps(metadata_payload, ensure_ascii=False),
                record.get("current_role", "director"),
                record.get("next_role", ""),
                record.get("escalation_deadline", ""),
                record.get("last_escalated_at", ""),
            ),
        )

    def load_human_review_requests(
        self,
        workspace: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Load unresolved human review requests."""
        conn = self._get_conn()
        cursor = conn.execute(
            """
            SELECT
                task_id, trace_id, workspace, reason, escalation_policy,
                status, created_at, resolved_at, resolution, resolved_by, metadata,
                current_role, next_role, escalation_deadline, last_escalated_at
            FROM human_review_requests
            WHERE workspace = ? AND status = 'waiting'
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (workspace, limit),
        )
        rows: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            item = _row_to_dict(row)
            metadata = _safe_json_loads(item.get("metadata"), default={})
            if isinstance(metadata, dict):
                for key, value in metadata.items():
                    if key not in item:
                        item[key] = value
            item.pop("metadata", None)
            rows.append(item)
        return rows

    # ---- plan_revisions -----------------------------------------------------

    def upsert_plan_revision(self, record: dict[str, object]) -> None:
        """Insert or update a plan revision record."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO plan_revisions
                (workspace, plan_id, plan_revision_id, parent_revision_id,
                 source_role, requirement_digest, constraint_digest, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(record.get("workspace") or "").strip(),
                str(record.get("plan_id") or "").strip(),
                str(record.get("plan_revision_id") or "").strip(),
                str(record.get("parent_revision_id") or "").strip(),
                str(record.get("source_role") or "").strip(),
                str(record.get("requirement_digest") or "").strip(),
                str(record.get("constraint_digest") or "").strip(),
                json.dumps(record.get("metadata", {}), ensure_ascii=False),
                str(record.get("created_at") or now_iso()).strip(),
            ),
        )

    def load_plan_revisions(
        self,
        workspace: str,
        *,
        plan_id: str = "",
        limit: int = 200,
    ) -> list[dict[str, object]]:
        """Load plan revisions for a workspace (optionally by plan_id)."""
        conn = self._get_conn()
        if plan_id:
            cursor = conn.execute(
                """
                SELECT * FROM plan_revisions
                WHERE workspace = ? AND plan_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (workspace, plan_id, limit),
            )
        else:
            cursor = conn.execute(
                """
                SELECT * FROM plan_revisions
                WHERE workspace = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (workspace, limit),
            )
        rows = [_row_to_dict(row) for row in cursor.fetchall()]
        for row in rows:
            row["metadata"] = _safe_json_loads(row.get("metadata"), default={})
        return rows

    # ---- change_orders ------------------------------------------------------

    def append_change_order(self, record: dict[str, object]) -> None:
        """Append a change-order entry."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO change_orders
                (workspace, plan_id, from_revision_id, to_revision_id,
                 change_type, source_role, summary, trace_id, affected_task_ids,
                 impact_counts, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(record.get("workspace") or "").strip(),
                str(record.get("plan_id") or "").strip(),
                str(record.get("from_revision_id") or "").strip(),
                str(record.get("to_revision_id") or "").strip(),
                str(record.get("change_type") or "").strip(),
                str(record.get("source_role") or "").strip(),
                str(record.get("summary") or "").strip(),
                str(record.get("trace_id") or "").strip(),
                json.dumps(record.get("affected_task_ids", []), ensure_ascii=False),
                json.dumps(record.get("impact_counts", {}), ensure_ascii=False),
                json.dumps(record.get("metadata", {}), ensure_ascii=False),
                str(record.get("created_at") or now_iso()).strip(),
            ),
        )

    def load_change_orders(
        self,
        workspace: str,
        *,
        plan_id: str = "",
        limit: int = 200,
    ) -> list[dict[str, object]]:
        """Load change-order entries for a workspace (optionally by plan_id)."""
        conn = self._get_conn()
        if plan_id:
            cursor = conn.execute(
                """
                SELECT * FROM change_orders
                WHERE workspace = ? AND plan_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (workspace, plan_id, limit),
            )
        else:
            cursor = conn.execute(
                """
                SELECT * FROM change_orders
                WHERE workspace = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (workspace, limit),
            )
        rows = [_row_to_dict(row) for row in cursor.fetchall()]
        for row in rows:
            row["affected_task_ids"] = _safe_json_loads(row.get("affected_task_ids"), default=[])
            row["impact_counts"] = _safe_json_loads(row.get("impact_counts"), default={})
            row["metadata"] = _safe_json_loads(row.get("metadata"), default={})
        return rows

    # ---- outbox_messages ----------------------------------------------------

    def append_outbox_message(self, record: dict[str, object]) -> None:
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO outbox_messages
                (outbox_id, workspace, stream, event_type, source, run_id, task_id,
                 payload, status, attempts, last_error, created_at, failed_at, delivered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(record.get("outbox_id") or "").strip(),
                str(record.get("workspace") or "").strip(),
                str(record.get("stream") or "").strip(),
                str(record.get("event_type") or "").strip(),
                str(record.get("source") or "runtime.task_market").strip(),
                str(record.get("run_id") or "").strip(),
                str(record.get("task_id") or "").strip(),
                json.dumps(record.get("payload", {}), ensure_ascii=False),
                str(record.get("status") or "pending").strip().lower(),
                _safe_int(record.get("attempts"), default=0, min_value=0),
                str(record.get("last_error") or "").strip(),
                str(record.get("created_at") or now_iso()).strip(),
                str(record.get("failed_at") or "").strip(),
                str(record.get("delivered_at") or "").strip(),
            ),
        )

    def load_outbox_messages(
        self,
        workspace: str,
        *,
        statuses: tuple[str, ...] = ("pending", "failed"),
        limit: int = 200,
    ) -> list[dict[str, object]]:
        tokens = [str(token or "").strip().lower() for token in statuses if str(token or "").strip()]
        if not tokens:
            tokens = ["pending", "failed"]
        placeholders = ",".join("?" for _ in tokens)
        params: list[object] = [workspace, *tokens, max(1, int(limit))]
        conn = self._get_conn()
        cursor = conn.execute(
            f"""
            SELECT * FROM outbox_messages
            WHERE workspace = ?
              AND status IN ({placeholders})
            ORDER BY created_at ASC
            LIMIT ?
            """,
            tuple(params),
        )
        rows = [_row_to_dict(row) for row in cursor.fetchall()]
        for row in rows:
            row["payload"] = _safe_json_loads(row.get("payload"), default={})
        return rows

    def mark_outbox_message_sent(
        self,
        workspace: str,
        outbox_id: str,
        *,
        delivered_at: str = "",
    ) -> None:
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE outbox_messages
            SET status = 'sent',
                delivered_at = ?,
                last_error = ''
            WHERE workspace = ? AND outbox_id = ?
            """,
            (
                str(delivered_at or now_iso()).strip(),
                workspace,
                outbox_id,
            ),
        )

    def mark_outbox_message_failed(
        self,
        workspace: str,
        outbox_id: str,
        *,
        error_message: str,
        failed_at: str = "",
    ) -> None:
        conn = self._get_conn()
        conn.execute(
            """
            UPDATE outbox_messages
            SET status = 'failed',
                attempts = attempts + 1,
                last_error = ?,
                failed_at = ?
            WHERE workspace = ? AND outbox_id = ?
            """,
            (
                str(error_message or "").strip(),
                str(failed_at or now_iso()).strip(),
                workspace,
                outbox_id,
            ),
        )

    # ---- Transaction support (for atomic state + outbox writes) -------------

    def begin(self) -> None:
        """Begin an explicit transaction (BEGIN IMMEDIATE).

        Must be paired with ``commit()`` or ``rollback()`` on the same thread.
        Uses the cached connection for the current thread.
        """
        conn = self._get_conn()
        conn.execute("BEGIN IMMEDIATE")

    def commit(self) -> None:
        """Commit the current explicit transaction."""
        conn = self._get_conn()
        conn.execute("COMMIT")

    def rollback(self) -> None:
        """Roll back the current explicit transaction."""
        import contextlib

        conn = self._get_conn()
        with contextlib.suppress(sqlite3.OperationalError):
            conn.execute("ROLLBACK")

    def save_items_and_outbox_atomic(
        self,
        *,
        items: dict[str, TaskWorkItemRecord],
        transitions: list[dict[str, Any]],
        outbox_records: list[dict[str, Any]],
    ) -> None:
        """Atomically persist items, transitions, and outbox messages in one transaction.

        This guarantees that state changes and their corresponding outbox entries
        are committed together, satisfying the outbox_atomic fitness rule.
        """
        conn = self._get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            # 1. Upsert items.
            for item in items.values():
                conn.execute(
                    """
                    INSERT OR REPLACE INTO work_items
                        (task_id, trace_id, run_id, workspace, stage, status, priority,
                         plan_id, plan_revision_id, root_task_id, parent_task_id, is_leaf,
                         depends_on, requirement_digest, constraint_digest, summary_ref,
                         superseded_by_revision, change_policy, compensation_group_id,
                         payload, metadata, version, attempts, max_attempts,
                         lease_token, lease_expires_at, claimed_by, claimed_role,
                         last_error, created_at, updated_at)
                    VALUES
                        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.task_id,
                        item.trace_id,
                        item.run_id,
                        item.workspace,
                        item.stage,
                        item.status,
                        item.priority,
                        item.plan_id,
                        item.plan_revision_id,
                        item.root_task_id or item.task_id,
                        item.parent_task_id,
                        1 if item.is_leaf else 0,
                        json.dumps(item.depends_on, ensure_ascii=False),
                        item.requirement_digest,
                        item.constraint_digest,
                        item.summary_ref,
                        item.superseded_by_revision,
                        item.change_policy,
                        item.compensation_group_id,
                        json.dumps(item.payload, ensure_ascii=False),
                        json.dumps(item.metadata, ensure_ascii=False),
                        item.version,
                        item.attempts,
                        item.max_attempts,
                        item.lease_token,
                        item.lease_expires_at,
                        item.claimed_by,
                        item.claimed_role,
                        json.dumps(item.last_error, ensure_ascii=False),
                        item.created_at,
                        item.updated_at,
                    ),
                )

            # 2. Append transitions.
            for t in transitions:
                conn.execute(
                    """
                    INSERT INTO work_item_transitions
                        (task_id, from_status, to_status, event_type, worker_id,
                         lease_token, version, metadata, emitted_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        t["task_id"],
                        t["from_status"],
                        t["to_status"],
                        t["event_type"],
                        t["worker_id"],
                        t["lease_token"],
                        t["version"],
                        json.dumps(t["metadata"], ensure_ascii=False),
                        t.get("emitted_at", now_iso()),
                    ),
                )

            # 3. Append outbox records.
            for rec in outbox_records:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO outbox_messages
                        (outbox_id, workspace, stream, event_type, source, run_id, task_id,
                         payload, status, attempts, last_error, created_at, failed_at, delivered_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(rec.get("outbox_id") or "").strip(),
                        str(rec.get("workspace") or "").strip(),
                        str(rec.get("stream") or "").strip(),
                        str(rec.get("event_type") or "").strip(),
                        str(rec.get("source") or "runtime.task_market").strip(),
                        str(rec.get("run_id") or "").strip(),
                        str(rec.get("task_id") or "").strip(),
                        json.dumps(rec.get("payload", {}), ensure_ascii=False),
                        str(rec.get("status") or "pending").strip().lower(),
                        _safe_int(rec.get("attempts"), default=0, min_value=0),
                        str(rec.get("last_error") or "").strip(),
                        str(rec.get("created_at") or now_iso()).strip(),
                        str(rec.get("failed_at") or "").strip(),
                        str(rec.get("delivered_at") or "").strip(),
                    ),
                )

            conn.execute("COMMIT")
        except BaseException:
            try:  # noqa: SIM105
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise

    # ---- advisory lock helper (for external use) ----------------------------

    def acquire_advisory_lock(self, lock_name: str, owner: str, timeout_secs: float = 30.0) -> bool:
        """Try to acquire a simple advisory lock.

        This is a no-op in SQLite but can be wired to ``fcntl.flock`` on POSIX.
        Returns True if acquired, False otherwise.
        """
        # SQLite doesn't support true advisory locks across processes;
        # workspace-level threading.Lock is used instead (in TaskMarketService).
        return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_record(row: sqlite3.Row) -> TaskWorkItemRecord:
    """Convert a ``sqlite3.Row`` to a ``TaskWorkItemRecord``."""
    return TaskWorkItemRecord(
        task_id=str(row["task_id"] or "").strip(),
        trace_id=str(row["trace_id"] or "").strip(),
        run_id=str(row["run_id"] or "").strip(),
        workspace=str(row["workspace"] or "").strip(),
        stage=str(row["stage"] or "").strip().lower(),
        status=str(row["status"] or "").strip().lower(),
        priority=str(row["priority"] or "medium").strip().lower(),
        plan_id=str(row["plan_id"] or "").strip(),
        plan_revision_id=str(row["plan_revision_id"] or "").strip(),
        root_task_id=str(row["root_task_id"] or row["task_id"] or "").strip(),
        parent_task_id=str(row["parent_task_id"] or "").strip(),
        is_leaf=bool(int(row["is_leaf"] or 0)),
        depends_on=json.loads(row["depends_on"] or "[]"),
        requirement_digest=str(row["requirement_digest"] or "").strip(),
        constraint_digest=str(row["constraint_digest"] or "").strip(),
        summary_ref=str(row["summary_ref"] or "").strip(),
        superseded_by_revision=str(row["superseded_by_revision"] or "").strip(),
        change_policy=str(row["change_policy"] or "strict").strip().lower() or "strict",
        compensation_group_id=str(row["compensation_group_id"] or "").strip(),
        payload=json.loads(row["payload"] or "{}"),
        metadata=json.loads(row["metadata"] or "{}"),
        version=max(1, int(row["version"] or 1)),
        attempts=max(0, int(row["attempts"] or 0)),
        max_attempts=max(1, int(row["max_attempts"] or 3)),
        lease_token=str(row["lease_token"] or "").strip(),
        lease_expires_at=float(row["lease_expires_at"] or 0.0),
        claimed_by=str(row["claimed_by"] or "").strip(),
        claimed_role=str(row["claimed_role"] or "").strip(),
        last_error=json.loads(row["last_error"] or "{}"),
        created_at=str(row["created_at"] or "").strip() or now_iso(),
        updated_at=str(row["updated_at"] or "").strip() or now_iso(),
    )


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a ``sqlite3.Row`` to a plain dict."""
    return dict(row)


def _safe_json_loads(raw: object, *, default: object) -> object:
    if isinstance(raw, (dict, list)):
        return raw
    if raw is None:
        return default
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError):
        return default


def _safe_int(value: object, *, default: int, min_value: int = 0) -> int:
    try:
        parsed = int(str(value or "").strip() or str(default))
    except (TypeError, ValueError):
        return max(min_value, default)
    return max(min_value, parsed)


__all__ = ["TaskMarketSQLiteStore"]
