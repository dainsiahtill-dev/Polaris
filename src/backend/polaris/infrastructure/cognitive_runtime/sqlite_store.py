from __future__ import annotations

import json
import os
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polaris.domain.cognitive_runtime import (
    ContextHandoffPack,
    DiffCellMapping,
    ProjectionCompileRequest,
    PromotionDecisionRecord,
    RollbackLedgerEntry,
    RuntimeReceipt,
)
from polaris.infrastructure.db.adapters import SqliteAdapter
from polaris.kernelone.constants import DEFAULT_SHORT_TIMEOUT_SECONDS
from polaris.kernelone.db import KernelDatabase
from polaris.kernelone.storage import normalize_logical_rel_path
from polaris.kernelone.storage.layout import resolve_existing_storage_roots_read_only


@dataclass(slots=True)
class _WriteTask:
    fn: Any
    done: threading.Event
    result: Any = None
    error: BaseException | None = None


class CognitiveRuntimeSqliteStore:
    """SQLite-backed receipt and handoff persistence for Cognitive Runtime."""

    def __init__(
        self,
        workspace: str,
        *,
        db_path: str = "runtime/cognitive_runtime/cognitive_runtime.sqlite",
        kernel_db: KernelDatabase | None = None,
        read_only: bool = False,
    ) -> None:
        self._read_only = bool(read_only)
        self._closed = False
        self._owns_kernel_db = kernel_db is None
        self._kernel_db = kernel_db or KernelDatabase(
            workspace,
            sqlite_adapter=SqliteAdapter(),
            allow_unmanaged_absolute=True,
        )
        if self._read_only:
            if os.path.isabs(db_path):
                self._db_path = os.path.abspath(db_path)
            else:
                logical = normalize_logical_rel_path(db_path)
                if not logical.startswith("runtime/"):
                    raise ValueError("read-only Cognitive Runtime DB requires a runtime path")
                roots = resolve_existing_storage_roots_read_only(workspace)
                if roots is None:
                    raise FileNotFoundError("Cognitive Runtime namespace is unavailable")
                self._db_path = os.path.join(roots.runtime_root, logical[len("runtime/") :])
            if not os.path.isfile(self._db_path):
                raise FileNotFoundError(self._db_path)
            return
        self._db_path = self._kernel_db.resolve_sqlite_path(db_path, ensure_parent=True)
        self._init_schema()
        self._write_queue: queue.Queue[_WriteTask | None] = queue.Queue(maxsize=1000)  # 有界队列防止内存泄漏
        self._writer_stop = threading.Event()
        self._write_connection = self._connect()
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="cognitive-runtime-sqlite-writer",
            daemon=True,
        )
        self._writer_thread.start()

    @property
    def workspace(self) -> str:
        return self._kernel_db.workspace

    @classmethod
    def open_existing_read_only(cls, workspace: str) -> CognitiveRuntimeSqliteStore | None:
        """Observe an existing namespace; never initialize one during a query."""
        try:
            return cls(workspace, read_only=True)
        except FileNotFoundError:
            return None

    def _connect_read_only(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("Cognitive Runtime SQLite store is closed")
        uri = Path(self._db_path).absolute().as_uri() + "?mode=ro"
        return self._kernel_db.sqlite(
            uri,
            timeout_seconds=DEFAULT_SHORT_TIMEOUT_SECONDS,
            check_same_thread=False,
            row_factory="row",
            uri=True,
            pragmas={"query_only": True, "busy_timeout": 30000},
            ensure_parent=False,
        )

    def _connect(self) -> sqlite3.Connection:
        return self._kernel_db.sqlite(
            self._db_path,
            timeout_seconds=DEFAULT_SHORT_TIMEOUT_SECONDS,
            check_same_thread=False,
            row_factory="row",
            pragmas={
                "journal_mode": "WAL",
                "busy_timeout": 30000,
                "synchronous": "NORMAL",
            },
            ensure_parent=True,
        )

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS cognitive_runtime_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    session_id TEXT,
                    run_id TEXT,
                    receipt_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    receipt_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_cognitive_runtime_receipts_session
                    ON cognitive_runtime_receipts(session_id, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_cognitive_runtime_receipts_run
                    ON cognitive_runtime_receipts(run_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS cognitive_runtime_handoffs (
                    handoff_id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    run_id TEXT,
                    created_at TEXT NOT NULL,
                    handoff_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_cognitive_runtime_handoffs_session
                    ON cognitive_runtime_handoffs(session_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS cognitive_runtime_diff_mappings (
                    mapping_id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    mapping_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_cognitive_runtime_diff_mappings_workspace
                    ON cognitive_runtime_diff_mappings(workspace, created_at DESC);

                CREATE TABLE IF NOT EXISTS cognitive_runtime_projection_requests (
                    request_id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    subject_ref TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    request_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_cognitive_runtime_projection_subject
                    ON cognitive_runtime_projection_requests(workspace, subject_ref, created_at DESC);

                CREATE TABLE IF NOT EXISTS cognitive_runtime_promotion_decisions (
                    decision_id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    subject_ref TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decision_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_cognitive_runtime_decisions_subject
                    ON cognitive_runtime_promotion_decisions(workspace, subject_ref, created_at DESC);

                CREATE TABLE IF NOT EXISTS cognitive_runtime_rollback_ledger (
                    rollback_id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    subject_ref TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    rollback_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_cognitive_runtime_rollback_subject
                    ON cognitive_runtime_rollback_ledger(workspace, subject_ref, created_at DESC);
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _writer_loop(self) -> None:
        while not self._writer_stop.is_set():
            try:
                task = self._write_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if task is None:
                self._write_queue.task_done()
                break
            try:
                task.result = self._run_write_task(task.fn)
            except (RuntimeError, ValueError, sqlite3.Error, OSError) as exc:
                task.error = exc
            finally:
                task.done.set()
                self._write_queue.task_done()

    def _run_write_task(self, fn: Any) -> Any:
        delay_seconds = 0.05
        last_error: sqlite3.OperationalError | None = None
        for _attempt in range(5):
            try:
                result = fn(self._write_connection)
                self._write_connection.commit()
                return result
            except sqlite3.OperationalError as exc:
                self._write_connection.rollback()
                if "locked" not in str(exc).lower():
                    raise
                last_error = exc
                time.sleep(delay_seconds)
                delay_seconds = min(delay_seconds * 2.0, 0.5)
        if last_error is not None:
            raise last_error
        raise RuntimeError("write task failed without sqlite error")

    def _submit_write(self, fn: Any) -> Any:
        if self._read_only:
            raise RuntimeError("Cognitive Runtime SQLite store is read-only")
        if self._writer_stop.is_set():
            raise RuntimeError("Cognitive Runtime SQLite store is closed")
        task = _WriteTask(fn=fn, done=threading.Event())
        self._write_queue.put(task)
        task.done.wait()
        if task.error is not None:
            raise task.error
        return task.result

    def append_receipt(self, receipt: RuntimeReceipt) -> RuntimeReceipt:
        receipt_json = json.dumps(receipt.to_dict(), ensure_ascii=False)
        self._submit_write(
            lambda conn: conn.execute(
                """
                INSERT OR REPLACE INTO cognitive_runtime_receipts
                (receipt_id, workspace, session_id, run_id, receipt_type, created_at, receipt_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.receipt_id,
                    receipt.workspace,
                    receipt.session_id,
                    receipt.run_id,
                    receipt.receipt_type,
                    receipt.created_at,
                    receipt_json,
                ),
            )
        )
        return receipt

    def get_receipt(self, receipt_id: str) -> RuntimeReceipt | None:
        conn = self._connect_read_only()
        try:
            row = conn.execute(
                "SELECT receipt_json FROM cognitive_runtime_receipts WHERE receipt_id = ?",
                (str(receipt_id or "").strip(),),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return RuntimeReceipt.from_mapping(json.loads(str(row["receipt_json"])))

    def list_receipts(
        self,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        limit: int = 20,
    ) -> list[RuntimeReceipt]:
        clauses: list[str] = []
        params: list[Any] = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        sql = "SELECT receipt_json FROM cognitive_runtime_receipts"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, int(limit)))

        conn = self._connect_read_only()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
        finally:
            conn.close()
        return [
            receipt
            for receipt in (RuntimeReceipt.from_mapping(json.loads(str(row["receipt_json"]))) for row in rows)
            if receipt is not None
        ]

    def save_handoff_pack(self, handoff: ContextHandoffPack) -> ContextHandoffPack:
        handoff_json = json.dumps(handoff.to_dict(), ensure_ascii=False)
        self._submit_write(
            lambda conn: conn.execute(
                """
                INSERT OR REPLACE INTO cognitive_runtime_handoffs
                (handoff_id, workspace, session_id, run_id, created_at, handoff_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    handoff.handoff_id,
                    handoff.workspace,
                    handoff.session_id,
                    handoff.run_id,
                    handoff.created_at,
                    handoff_json,
                ),
            )
        )
        return handoff

    def get_handoff_pack(self, handoff_id: str) -> ContextHandoffPack | None:
        conn = self._connect_read_only()
        try:
            row = conn.execute(
                "SELECT handoff_json FROM cognitive_runtime_handoffs WHERE handoff_id = ?",
                (str(handoff_id or "").strip(),),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return ContextHandoffPack.from_mapping(json.loads(str(row["handoff_json"])))

    def append_diff_mapping(self, mapping: DiffCellMapping) -> DiffCellMapping:
        mapping_json = json.dumps(mapping.to_dict(), ensure_ascii=False)
        self._submit_write(
            lambda conn: conn.execute(
                """
                INSERT OR REPLACE INTO cognitive_runtime_diff_mappings
                (mapping_id, workspace, created_at, mapping_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    mapping.mapping_id,
                    mapping.workspace,
                    mapping.created_at,
                    mapping_json,
                ),
            )
        )
        return mapping

    def get_diff_mapping(self, mapping_id: str) -> DiffCellMapping | None:
        conn = self._connect_read_only()
        try:
            row = conn.execute(
                "SELECT mapping_json FROM cognitive_runtime_diff_mappings WHERE mapping_id = ?",
                (str(mapping_id or "").strip(),),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return DiffCellMapping.from_mapping(json.loads(str(row["mapping_json"])))

    def append_projection_request(self, request: ProjectionCompileRequest) -> ProjectionCompileRequest:
        request_json = json.dumps(request.to_dict(), ensure_ascii=False)
        self._submit_write(
            lambda conn: conn.execute(
                """
                INSERT OR REPLACE INTO cognitive_runtime_projection_requests
                (request_id, workspace, subject_ref, created_at, request_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    request.request_id,
                    request.workspace,
                    request.subject_ref,
                    request.created_at,
                    request_json,
                ),
            )
        )
        return request

    def get_projection_request(self, request_id: str) -> ProjectionCompileRequest | None:
        conn = self._connect_read_only()
        try:
            row = conn.execute(
                "SELECT request_json FROM cognitive_runtime_projection_requests WHERE request_id = ?",
                (str(request_id or "").strip(),),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return ProjectionCompileRequest.from_mapping(json.loads(str(row["request_json"])))

    def append_promotion_decision(
        self,
        decision: PromotionDecisionRecord,
    ) -> PromotionDecisionRecord:
        decision_json = json.dumps(decision.to_dict(), ensure_ascii=False)
        self._submit_write(
            lambda conn: conn.execute(
                """
                INSERT OR REPLACE INTO cognitive_runtime_promotion_decisions
                (decision_id, workspace, subject_ref, created_at, decision_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_id,
                    decision.workspace,
                    decision.subject_ref,
                    decision.created_at,
                    decision_json,
                ),
            )
        )
        return decision

    def get_promotion_decision(self, decision_id: str) -> PromotionDecisionRecord | None:
        conn = self._connect_read_only()
        try:
            row = conn.execute(
                "SELECT decision_json FROM cognitive_runtime_promotion_decisions WHERE decision_id = ?",
                (str(decision_id or "").strip(),),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return PromotionDecisionRecord.from_mapping(json.loads(str(row["decision_json"])))

    def append_rollback_ledger_entry(self, entry: RollbackLedgerEntry) -> RollbackLedgerEntry:
        rollback_json = json.dumps(entry.to_dict(), ensure_ascii=False)
        self._submit_write(
            lambda conn: conn.execute(
                """
                INSERT OR REPLACE INTO cognitive_runtime_rollback_ledger
                (rollback_id, workspace, subject_ref, created_at, rollback_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    entry.rollback_id,
                    entry.workspace,
                    entry.subject_ref,
                    entry.created_at,
                    rollback_json,
                ),
            )
        )
        return entry

    def get_rollback_ledger_entry(self, rollback_id: str) -> RollbackLedgerEntry | None:
        conn = self._connect_read_only()
        try:
            row = conn.execute(
                "SELECT rollback_json FROM cognitive_runtime_rollback_ledger WHERE rollback_id = ?",
                (str(rollback_id or "").strip(),),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return RollbackLedgerEntry.from_mapping(json.loads(str(row["rollback_json"])))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._read_only:
            if self._owns_kernel_db:
                self._kernel_db.close()
            return
        if not self._writer_stop.is_set():
            self._writer_stop.set()
            self._write_queue.put(None)
            self._writer_thread.join(timeout=5.0)
            self._write_connection.close()
        self._kernel_db.close()
