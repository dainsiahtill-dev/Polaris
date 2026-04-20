"""SQLite runtime store with execution/event/task-state persistence."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polaris.infrastructure.db.adapters import SqliteAdapter
from polaris.kernelone.constants import DEFAULT_SHORT_TIMEOUT_SECONDS
from polaris.kernelone.db import KernelDatabase
from polaris.kernelone.utils import _now
from polaris.kernelone.workflow.base import WorkflowSnapshot

logger = logging.getLogger(__name__)

_TERMINAL_TASK_STATES = {"completed", "failed", "cancelled", "blocked", "skipped"}

_SQL_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_sql_identifier(name: str, identifier_type: str = "identifier") -> None:
    """验证 SQL 标识符（表名、列名）以防止注入攻击."""
    if not name or not isinstance(name, str):
        raise ValueError(f"Invalid {identifier_type}: must be a non-empty string")
    if not _SQL_IDENTIFIER_RE.match(name):
        raise ValueError(f"Invalid {identifier_type}: '{name}' contains invalid characters")
    reserved = {"table", "index", "trigger", "view"}
    if name.lower() in reserved:
        raise ValueError(f"Invalid {identifier_type}: '{name}' is a reserved keyword")


@dataclass
class WorkflowEvent:
    """Immutable workflow event record."""

    id: int
    workflow_id: str
    seq: int
    event_type: str
    payload: dict[str, Any]
    created_at: str


@dataclass
class WorkflowTaskState:
    """Task-level execution snapshot."""

    workflow_id: str
    task_id: str
    task_type: str
    handler_name: str
    status: str
    attempt: int
    max_attempts: int
    started_at: str | None = None
    ended_at: str | None = None
    result: dict[str, Any] | None = None
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""


@dataclass
class WorkflowExecution:
    """Workflow execution snapshot."""

    workflow_id: str
    workflow_name: str
    status: str
    run_id: str
    created_at: str
    updated_at: str
    close_time: str | None = None
    result: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    pending_actions: list[dict[str, Any]] = field(default_factory=list)


class SqliteRuntimeStore:
    """SQLite event-sourced runtime persistence.

    并发优化:
    - 依赖 SQLite WAL 模式支持并发读取
    - 写入操作使用 workflow_id 级细粒度锁
    - 读操作不加全局锁
    """

    # 写入锁桶数量
    _WRITE_LOCK_BUCKETS = 32

    def __init__(
        self,
        db_path: str = ":memory:",
        *,
        kernel_db: KernelDatabase | None = None,
        workspace: str | None = None,
    ) -> None:
        resolved_workspace = str(Path(workspace or ".").resolve())
        self._kernel_db = kernel_db or KernelDatabase(
            resolved_workspace,
            sqlite_adapter=SqliteAdapter(),
            allow_unmanaged_absolute=True,
        )
        self._db_path = self._kernel_db.resolve_sqlite_path(db_path, ensure_parent=True)
        # 细粒度写入锁: 按 workflow_id 哈希分片
        self._write_locks: list[asyncio.Lock] = [asyncio.Lock() for _ in range(self._WRITE_LOCK_BUCKETS)]
        self._init_schema()

    def _get_write_lock(self, workflow_id: str) -> asyncio.Lock:
        """获取 workflow_id 对应的写入锁。"""
        bucket = hash(workflow_id) % self._WRITE_LOCK_BUCKETS
        return self._write_locks[bucket]

    def _get_conn(self) -> sqlite3.Connection:
        return self._kernel_db.sqlite(
            self._db_path,
            timeout_seconds=DEFAULT_SHORT_TIMEOUT_SECONDS,
            check_same_thread=False,
            row_factory="row",
            pragmas={
                "journal_mode": "WAL",
                "busy_timeout": 30000,
            },
            ensure_parent=True,
        )

    @staticmethod
    def _now() -> str:
        return _now()

    @staticmethod
    def _loads_json(raw: str | None, default: Any) -> Any:
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (RuntimeError, ValueError):
            return default

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        *,
        table: str,
        column: str,
        column_sql: str,
    ) -> None:
        _validate_sql_identifier(table, "table")
        _validate_sql_identifier(column, "column")
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {str(row["name"]).strip() for row in rows}
        if column in existing:
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_sql}")

    def _init_schema(self) -> None:
        conn = self._get_conn()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflow_execution (
                    workflow_id TEXT PRIMARY KEY,
                    workflow_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running',
                    run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    close_time TEXT,
                    result_json TEXT,
                    metadata_json TEXT
                );

                CREATE TABLE IF NOT EXISTS workflow_event (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(workflow_id, seq)
                );

                CREATE TABLE IF NOT EXISTS workflow_event_sequence (
                    workflow_id TEXT PRIMARY KEY,
                    next_seq INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS workflow_task_state (
                    workflow_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    handler_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 1,
                    started_at TEXT,
                    ended_at TEXT,
                    result_json TEXT,
                    error_text TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (workflow_id, task_id)
                );

                CREATE INDEX IF NOT EXISTS idx_event_workflow
                    ON workflow_event(workflow_id, seq);

                CREATE INDEX IF NOT EXISTS idx_event_sequence_workflow
                    ON workflow_event_sequence(workflow_id);

                CREATE INDEX IF NOT EXISTS idx_task_state_workflow_status
                    ON workflow_task_state(workflow_id, status);
                """
            )
            self._ensure_column(
                conn,
                table="workflow_execution",
                column="metadata_json",
                column_sql="TEXT",
            )
            conn.commit()
            logger.info("SQLite runtime store initialized at %s", self._db_path)
        finally:
            conn.close()

    def init_schema(self) -> None:
        self._init_schema()

    async def create_execution(
        self,
        workflow_id: str,
        workflow_name: str,
        payload: dict[str, Any],
    ) -> None:
        write_lock = self._get_write_lock(workflow_id)
        async with write_lock:
            now = self._now()
            payload_json = json.dumps(payload, ensure_ascii=False)
            metadata_json = json.dumps({"payload": payload}, ensure_ascii=False)
            conn = self._get_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO workflow_execution
                    (workflow_id, workflow_name, status, run_id, created_at, updated_at, metadata_json)
                    VALUES (?, ?, 'running', ?, ?, ?, ?)
                    """,
                    (workflow_id, workflow_name, workflow_id, now, now, metadata_json),
                )
                conn.execute(
                    """
                    INSERT INTO workflow_event
                    (workflow_id, seq, event_type, payload_json, created_at)
                    VALUES (?, 1, 'workflow_started', ?, ?)
                    """,
                    (workflow_id, payload_json, now),
                )
                conn.execute(
                    """
                    INSERT INTO workflow_event_sequence(workflow_id, next_seq)
                    VALUES (?, 2)
                    ON CONFLICT(workflow_id) DO UPDATE SET
                        next_seq = CASE
                            WHEN workflow_event_sequence.next_seq < 2 THEN 2
                            ELSE workflow_event_sequence.next_seq
                        END
                    """,
                    (workflow_id,),
                )
                conn.commit()
            finally:
                conn.close()

    async def append_event(
        self,
        workflow_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> WorkflowEvent:
        write_lock = self._get_write_lock(workflow_id)
        async with write_lock:
            now = self._now()
            payload_json = json.dumps(payload, ensure_ascii=False)
            attempts = 0
            while True:
                attempts += 1
                conn = self._get_conn()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute(
                        "SELECT next_seq FROM workflow_event_sequence WHERE workflow_id = ?",
                        (workflow_id,),
                    ).fetchone()
                    if row is None:
                        max_row = conn.execute(
                            "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM workflow_event WHERE workflow_id = ?",
                            (workflow_id,),
                        ).fetchone()
                        seq = int(max_row["max_seq"]) + 1
                        conn.execute(
                            """
                            INSERT INTO workflow_event_sequence(workflow_id, next_seq)
                            VALUES (?, ?)
                            ON CONFLICT(workflow_id) DO UPDATE SET
                                next_seq = excluded.next_seq
                            """,
                            (workflow_id, seq + 1),
                        )
                    else:
                        seq = int(row["next_seq"])
                        conn.execute(
                            "UPDATE workflow_event_sequence SET next_seq = ? WHERE workflow_id = ?",
                            (seq + 1, workflow_id),
                        )
                    cursor = conn.execute(
                        """
                        INSERT INTO workflow_event
                        (workflow_id, seq, event_type, payload_json, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (workflow_id, seq, event_type, payload_json, now),
                    )
                    conn.execute(
                        """
                        UPDATE workflow_execution
                        SET updated_at = ?
                        WHERE workflow_id = ?
                        """,
                        (now, workflow_id),
                    )
                    conn.commit()
                    event_id = cursor.lastrowid or 0
                    return WorkflowEvent(
                        id=event_id,
                        workflow_id=workflow_id,
                        seq=seq,
                        event_type=event_type,
                        payload=payload,
                        created_at=now,
                    )
                except sqlite3.IntegrityError:
                    conn.rollback()
                    if attempts >= 3:
                        raise
                    await asyncio.sleep(0.01 * attempts)
                finally:
                    conn.close()

    async def get_events(
        self,
        workflow_id: str,
        *,
        limit: int | None = None,
    ) -> list[WorkflowEvent]:
        conn = self._get_conn()
        try:
            if isinstance(limit, int) and limit > 0:
                cursor = conn.execute(
                    """
                    SELECT id, workflow_id, seq, event_type, payload_json, created_at
                    FROM workflow_event
                    WHERE workflow_id = ?
                    ORDER BY seq DESC
                    LIMIT ?
                    """,
                    (workflow_id, int(limit)),
                )
                rows = list(reversed(cursor.fetchall()))
            else:
                cursor = conn.execute(
                    """
                    SELECT id, workflow_id, seq, event_type, payload_json, created_at
                    FROM workflow_event
                    WHERE workflow_id = ?
                    ORDER BY seq
                    """,
                    (workflow_id,),
                )
                rows = cursor.fetchall()
            return [
                WorkflowEvent(
                    id=int(row["id"]),
                    workflow_id=str(row["workflow_id"]),
                    seq=int(row["seq"]),
                    event_type=str(row["event_type"]),
                    payload=self._loads_json(row["payload_json"], {}),
                    created_at=str(row["created_at"]),
                )
                for row in rows
            ]
        finally:
            conn.close()

    async def get_execution(self, workflow_id: str) -> WorkflowExecution | None:
        conn = self._get_conn()
        try:
            row = conn.execute(
                """
                SELECT workflow_id, workflow_name, status, run_id, created_at, updated_at,
                       close_time, result_json, metadata_json
                FROM workflow_execution
                WHERE workflow_id = ?
                """,
                (workflow_id,),
            ).fetchone()
            if row is None:
                return None
            return WorkflowExecution(
                workflow_id=str(row["workflow_id"]),
                workflow_name=str(row["workflow_name"]),
                status=str(row["status"]),
                run_id=str(row["run_id"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                close_time=str(row["close_time"]) if row["close_time"] else None,
                result=self._loads_json(row["result_json"], None),
                metadata=self._loads_json(row["metadata_json"], {}),
            )
        finally:
            conn.close()

    async def update_execution(
        self,
        workflow_id: str,
        status: str | None = None,
        result: dict[str, Any] | None = None,
        close_time: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        write_lock = self._get_write_lock(workflow_id)
        async with write_lock:
            now = self._now()
            conn = self._get_conn()
            try:
                if status is not None:
                    conn.execute(
                        """
                        UPDATE workflow_execution
                        SET status = ?, updated_at = ?
                        WHERE workflow_id = ?
                        """,
                        (status, now, workflow_id),
                    )
                if result is not None:
                    conn.execute(
                        """
                        UPDATE workflow_execution
                        SET result_json = ?, updated_at = ?
                        WHERE workflow_id = ?
                        """,
                        (json.dumps(result, ensure_ascii=False), now, workflow_id),
                    )
                if close_time is not None:
                    conn.execute(
                        """
                        UPDATE workflow_execution
                        SET close_time = ?, updated_at = ?
                        WHERE workflow_id = ?
                        """,
                        (close_time, now, workflow_id),
                    )
                if metadata is not None:
                    conn.execute(
                        """
                        UPDATE workflow_execution
                        SET metadata_json = ?, updated_at = ?
                        WHERE workflow_id = ?
                        """,
                        (json.dumps(metadata, ensure_ascii=False), now, workflow_id),
                    )
                conn.commit()
            finally:
                conn.close()

    async def upsert_task_state(
        self,
        *,
        workflow_id: str,
        task_id: str,
        task_type: str,
        handler_name: str,
        status: str,
        attempt: int,
        max_attempts: int,
        started_at: str | None = None,
        ended_at: str | None = None,
        result: dict[str, Any] | None = None,
        error: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        write_lock = self._get_write_lock(workflow_id)
        async with write_lock:
            now = self._now()
            conn = self._get_conn()
            try:
                conn.execute(
                    """
                    INSERT INTO workflow_task_state (
                        workflow_id,
                        task_id,
                        task_type,
                        handler_name,
                        status,
                        attempt,
                        max_attempts,
                        started_at,
                        ended_at,
                        result_json,
                        error_text,
                        metadata_json,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(workflow_id, task_id) DO UPDATE SET
                        task_type = excluded.task_type,
                        handler_name = excluded.handler_name,
                        status = excluded.status,
                        attempt = excluded.attempt,
                        max_attempts = excluded.max_attempts,
                        started_at = excluded.started_at,
                        ended_at = excluded.ended_at,
                        result_json = excluded.result_json,
                        error_text = excluded.error_text,
                        metadata_json = excluded.metadata_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        workflow_id,
                        task_id,
                        task_type,
                        handler_name,
                        status,
                        int(attempt),
                        int(max_attempts),
                        started_at,
                        ended_at,
                        json.dumps(result, ensure_ascii=False) if result is not None else None,
                        str(error or "").strip(),
                        json.dumps(metadata or {}, ensure_ascii=False),
                        now,
                    ),
                )
                conn.execute(
                    """
                    UPDATE workflow_execution
                    SET updated_at = ?
                    WHERE workflow_id = ?
                    """,
                    (now, workflow_id),
                )
                conn.commit()
            finally:
                conn.close()

    async def list_task_states(self, workflow_id: str) -> list[WorkflowTaskState]:
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """
                SELECT workflow_id, task_id, task_type, handler_name, status, attempt, max_attempts,
                       started_at, ended_at, result_json, error_text, metadata_json, updated_at
                FROM workflow_task_state
                WHERE workflow_id = ?
                ORDER BY task_id
                """,
                (workflow_id,),
            )
            rows = cursor.fetchall()
            return [
                WorkflowTaskState(
                    workflow_id=str(row["workflow_id"]),
                    task_id=str(row["task_id"]),
                    task_type=str(row["task_type"]),
                    handler_name=str(row["handler_name"]),
                    status=str(row["status"]),
                    attempt=int(row["attempt"]),
                    max_attempts=int(row["max_attempts"]),
                    started_at=str(row["started_at"]) if row["started_at"] else None,
                    ended_at=str(row["ended_at"]) if row["ended_at"] else None,
                    result=self._loads_json(row["result_json"], None),
                    error=str(row["error_text"] or "").strip(),
                    metadata=self._loads_json(row["metadata_json"], {}),
                    updated_at=str(row["updated_at"]),
                )
                for row in rows
            ]
        finally:
            conn.close()

    async def create_snapshot(self, workflow_id: str) -> WorkflowSnapshot:
        execution = await self.get_execution(workflow_id)
        if execution is None:
            raise ValueError(f"Workflow {workflow_id} not found")
        task_states = await self.list_task_states(workflow_id)
        pending_actions: list[dict[str, Any]] = []
        for task_state in task_states:
            if task_state.status.lower() in _TERMINAL_TASK_STATES:
                continue
            pending_actions.append(
                {
                    "task_id": task_state.task_id,
                    "status": task_state.status,
                    "attempt": task_state.attempt,
                    "max_attempts": task_state.max_attempts,
                    "updated_at": task_state.updated_at,
                }
            )
        return WorkflowSnapshot(
            workflow_id=execution.workflow_id,
            workflow_name=execution.workflow_name,
            status=execution.status,
            run_id=execution.run_id,
            start_time=execution.created_at,
            close_time=execution.close_time,
            result=execution.result,
            pending_actions=pending_actions,
        )

    async def list_workflows(
        self,
        status: str | None = None,
        limit: int = 100,
    ) -> list[WorkflowExecution]:
        conn = self._get_conn()
        try:
            if status:
                cursor = conn.execute(
                    """
                    SELECT workflow_id, workflow_name, status, run_id, created_at, updated_at,
                           close_time, result_json, metadata_json
                    FROM workflow_execution
                    WHERE status = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (status, max(1, int(limit))),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT workflow_id, workflow_name, status, run_id, created_at, updated_at,
                           close_time, result_json, metadata_json
                    FROM workflow_execution
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (max(1, int(limit)),),
                )
            rows = cursor.fetchall()
            return [
                WorkflowExecution(
                    workflow_id=str(row["workflow_id"]),
                    workflow_name=str(row["workflow_name"]),
                    status=str(row["status"]),
                    run_id=str(row["run_id"]),
                    created_at=str(row["created_at"]),
                    updated_at=str(row["updated_at"]),
                    close_time=str(row["close_time"]) if row["close_time"] else None,
                    result=self._loads_json(row["result_json"], None),
                    metadata=self._loads_json(row["metadata_json"], {}),
                )
                for row in rows
            ]
        finally:
            conn.close()
