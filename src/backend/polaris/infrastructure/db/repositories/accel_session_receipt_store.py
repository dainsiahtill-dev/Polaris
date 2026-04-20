"""Session receipt store (migrated from legacy accel storage)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from polaris.infrastructure.db.adapters import SqliteAdapter
from polaris.kernelone.db import KernelDatabase
from polaris.kernelone.utils.time_utils import utc_now_iso

if TYPE_CHECKING:
    import sqlite3

_SESSION_STATUSES = {
    "open",
    "active",
    "closed",
    "expired",
    "canceled",
    "failed",
    "succeeded",
}
_RECEIPT_STATUSES = {
    "queued",
    "running",
    "succeeded",
    "failed",
    "canceled",
    "degraded",
    "partial",
}
_RECOVERABLE_RECEIPT_STATUSES = {"queued", "running"}


# Backward compatibility alias
_utc_now = utc_now_iso


def _parse_utc(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize_bool_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_receipt_status(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"success", "ok", "completed"}:
        return "succeeded"
    if token in {"cancelled", "cancelling"}:
        return "canceled"
    if token not in _RECEIPT_STATUSES:
        raise SessionReceiptError(
            "E_INVALID_STATE",
            f"unsupported receipt status: {value}",
        )
    return token


_VALID_SESSION_FINAL_STATUSES = {"closed", "canceled", "failed", "succeeded"}


def _normalize_session_final_status(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token == "cancelled":
        token = "canceled"
    if not token:
        token = "closed"
    if token not in _VALID_SESSION_FINAL_STATUSES:
        valid = ", ".join(sorted(_VALID_SESSION_FINAL_STATUSES))
        raise SessionReceiptError(
            "E_INVALID_STATE",
            f"unsupported session final_status: {value!r}. valid values: {valid}",
        )
    return token


def _normalize_ttl_seconds(value: Any, default_value: int = 1800) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default_value)
    return max(30, min(24 * 3600, parsed))


def _to_meta_json(meta: Any) -> str:
    if isinstance(meta, dict):
        return json.dumps(meta, ensure_ascii=False)
    if isinstance(meta, str):
        text = str(meta).strip()
        if not text:
            return "{}"
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return json.dumps(parsed, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            return "{}"
    return "{}"


class SessionReceiptError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)

    def __str__(self) -> str:
        return str(self.message)


class SessionReceiptStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._kernel_db = KernelDatabase(
            str(self._db_path.parent),
            sqlite_adapter=SqliteAdapter(),
            allow_unmanaged_absolute=True,
        )
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return self._kernel_db.sqlite(
            str(self._db_path),
            timeout_seconds=10.0,
            isolation_level=None,
            check_same_thread=False,
            row_factory="row",
            pragmas={"busy_timeout": 5000},
            ensure_parent=True,
        )

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                  session_id TEXT PRIMARY KEY,
                  run_id TEXT NOT NULL,
                  status TEXT NOT NULL,
                  owner TEXT NOT NULL,
                  lease_owner TEXT NOT NULL DEFAULT '',
                  lease_id TEXT NOT NULL DEFAULT '',
                  lease_until TEXT NOT NULL DEFAULT '',
                  ttl_seconds INTEGER NOT NULL,
                  meta_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS receipts (
                  job_id TEXT PRIMARY KEY,
                  session_id TEXT NOT NULL,
                  run_id TEXT NOT NULL,
                  tool TEXT NOT NULL,
                  args_hash TEXT NOT NULL,
                  status TEXT NOT NULL,
                  ts TEXT NOT NULL,
                  evidence_run INTEGER NOT NULL DEFAULT 0,
                  changed_files TEXT NOT NULL DEFAULT '',
                  result_ref TEXT NOT NULL DEFAULT '',
                  error_code TEXT NOT NULL DEFAULT '',
                  error_message TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS receipt_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  job_id TEXT NOT NULL,
                  session_id TEXT NOT NULL,
                  run_id TEXT NOT NULL,
                  tool TEXT NOT NULL,
                  from_status TEXT NOT NULL,
                  to_status TEXT NOT NULL,
                  ts TEXT NOT NULL,
                  error_code TEXT NOT NULL DEFAULT '',
                  error_message TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_receipts_session_ts ON receipts(session_id, ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_receipts_run_ts ON receipts(run_id, ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_receipts_tool_status_ts ON receipts(tool, status, ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_receipt_events_job_ts ON receipt_events(job_id, ts)")
        finally:
            conn.close()

    def _session_row_as_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "session_id": str(row["session_id"]),
            "run_id": str(row["run_id"]),
            "status": str(row["status"]),
            "owner": str(row["owner"]),
            "lease_owner": str(row["lease_owner"]),
            "lease_id": str(row["lease_id"]),
            "lease_until": str(row["lease_until"]),
            "ttl_seconds": int(row["ttl_seconds"] or 0),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "meta_json": str(row["meta_json"] or "{}"),
        }

    def _receipt_row_as_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "job_id": str(row["job_id"]),
            "session_id": str(row["session_id"]),
            "run_id": str(row["run_id"]),
            "tool": str(row["tool"]),
            "args_hash": str(row["args_hash"]),
            "status": str(row["status"]),
            "ts": str(row["ts"]),
            "evidence_run": bool(int(row["evidence_run"] or 0)),
            "changed_files": str(row["changed_files"] or ""),
            "result_ref": str(row["result_ref"] or ""),
            "error_code": str(row["error_code"] or ""),
            "error_message": str(row["error_message"] or ""),
        }

    def open_session(
        self,
        *,
        run_id: str,
        session_id: str = "",
        owner: str = "codex",
        ttl_seconds: int = 1800,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run_id_value = str(run_id).strip()
        if not run_id_value:
            raise SessionReceiptError("E_INVALID_STATE", "run_id is required")
        session_id_value = str(session_id).strip() or f"s_{uuid4().hex[:12]}"
        owner_value = str(owner).strip() or "codex"
        ttl_value = _normalize_ttl_seconds(ttl_seconds)
        now = _utc_now()
        lease_until = (datetime.now(timezone.utc) + timedelta(seconds=ttl_value)).isoformat()
        lease_id = f"lease_{uuid4().hex[:16]}"
        meta_json = _to_meta_json(meta if isinstance(meta, dict) else {})

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id_value,),
            ).fetchone()
            if existing is not None and str(existing["run_id"]) != run_id_value:
                raise SessionReceiptError(
                    "E_SESSION_CONFLICT",
                    "session_id is already bound to another run_id",
                )
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO sessions(
                      session_id, run_id, status, owner, lease_owner, lease_id, lease_until,
                      ttl_seconds, meta_json, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id_value,
                        run_id_value,
                        "open",
                        owner_value,
                        owner_value,
                        lease_id,
                        lease_until,
                        ttl_value,
                        meta_json,
                        now,
                        now,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE sessions
                    SET run_id = ?, status = ?, owner = ?, lease_owner = ?, lease_id = ?,
                        lease_until = ?, ttl_seconds = ?, meta_json = ?, updated_at = ?
                    WHERE session_id = ?
                    """,
                    (
                        run_id_value,
                        "open",
                        owner_value,
                        owner_value,
                        lease_id,
                        lease_until,
                        ttl_value,
                        meta_json,
                        now,
                        session_id_value,
                    ),
                )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id_value,),
            ).fetchone()
            if row is None:
                raise SessionReceiptError("E_INVALID_STATE", "failed to open session")
            return self._session_row_as_payload(row)
        finally:
            conn.close()

    def attach_session(
        self,
        *,
        session_id: str,
        run_id: str,
        actor: str = "policy",
        readonly: bool = True,
    ) -> dict[str, Any]:
        session_id_value = str(session_id).strip()
        run_id_value = str(run_id).strip()
        actor_value = str(actor).strip() or "policy"
        if not session_id_value or not run_id_value:
            raise SessionReceiptError("E_INVALID_STATE", "session_id and run_id are required")
        readonly_flag = _normalize_bool_flag(readonly)
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id_value,),
            ).fetchone()
            if row is None:
                raise SessionReceiptError("E_SESSION_NOT_FOUND", "session not found")
            if str(row["run_id"]) != run_id_value:
                raise SessionReceiptError(
                    "E_SESSION_CONFLICT",
                    "run_id does not match session run_id",
                )
            lease_until_dt = _parse_utc(str(row["lease_until"]))
            is_expired = (
                lease_until_dt is not None and str(row["lease_until"]).strip() != "" and lease_until_dt <= now_dt
            )
            if is_expired:
                conn.execute(
                    "UPDATE sessions SET status = ?, updated_at = ? WHERE session_id = ?",
                    ("expired", now, session_id_value),
                )
                conn.commit()
                raise SessionReceiptError("E_SESSION_EXPIRED", "session lease expired")
            if readonly_flag:
                conn.commit()
                row_after = conn.execute(
                    "SELECT * FROM sessions WHERE session_id = ?",
                    (session_id_value,),
                ).fetchone()
                if row_after is None:
                    raise SessionReceiptError("E_SESSION_NOT_FOUND", "session not found")
                return self._session_row_as_payload(row_after)

            lease_owner = str(row["lease_owner"] or "")
            lease_id_current = str(row["lease_id"] or "")
            if (
                lease_owner
                and lease_owner != actor_value
                and lease_id_current
                and lease_until_dt is not None
                and lease_until_dt > now_dt
            ):
                raise SessionReceiptError("E_SESSION_CONFLICT", "lease is owned by another actor")

            ttl_value = _normalize_ttl_seconds(int(row["ttl_seconds"] or 1800))
            next_lease = (now_dt + timedelta(seconds=ttl_value)).isoformat()
            lease_id_new = f"lease_{uuid4().hex[:16]}"
            conn.execute(
                """
                UPDATE sessions
                SET status = ?, lease_owner = ?, lease_id = ?, lease_until = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (
                    "active",
                    actor_value,
                    lease_id_new,
                    next_lease,
                    now,
                    session_id_value,
                ),
            )
            conn.commit()
            row_after = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id_value,),
            ).fetchone()
            if row_after is None:
                raise SessionReceiptError("E_SESSION_NOT_FOUND", "session not found")
            return self._session_row_as_payload(row_after)
        finally:
            conn.close()

    def heartbeat_session(self, *, session_id: str, lease_id: str) -> dict[str, Any]:
        session_id_value = str(session_id).strip()
        lease_id_value = str(lease_id).strip()
        if not session_id_value or not lease_id_value:
            raise SessionReceiptError("E_INVALID_STATE", "session_id and lease_id are required")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id_value,),
            ).fetchone()
            if row is None:
                raise SessionReceiptError("E_SESSION_NOT_FOUND", "session not found")
            if str(row["lease_id"] or "") != lease_id_value:
                raise SessionReceiptError("E_SESSION_CONFLICT", "lease_id mismatch")
            lease_until_dt = _parse_utc(str(row["lease_until"]))
            if lease_until_dt is not None and lease_until_dt <= now_dt:
                conn.execute(
                    "UPDATE sessions SET status = ?, updated_at = ? WHERE session_id = ?",
                    ("expired", now, session_id_value),
                )
                conn.commit()
                raise SessionReceiptError("E_SESSION_EXPIRED", "session lease expired")
            ttl_value = _normalize_ttl_seconds(int(row["ttl_seconds"] or 1800))
            next_lease = (now_dt + timedelta(seconds=ttl_value)).isoformat()
            conn.execute(
                """
                UPDATE sessions
                SET status = ?, lease_until = ?, updated_at = ?
                WHERE session_id = ?
                """,
                ("active", next_lease, now, session_id_value),
            )
            conn.commit()
            row_after = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id_value,),
            ).fetchone()
            if row_after is None:
                raise SessionReceiptError("E_SESSION_NOT_FOUND", "session not found")
            return self._session_row_as_payload(row_after)
        finally:
            conn.close()

    def close_session(self, *, session_id: str, final_status: str = "closed") -> dict[str, Any]:
        session_id_value = str(session_id).strip()
        if not session_id_value:
            raise SessionReceiptError("E_INVALID_STATE", "session_id is required")
        final_status_value = _normalize_session_final_status(final_status)
        now = _utc_now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id_value,),
            ).fetchone()
            if row is None:
                raise SessionReceiptError("E_SESSION_NOT_FOUND", "session not found")
            conn.execute(
                """
                UPDATE sessions
                SET status = ?, lease_owner = '', lease_id = '', lease_until = '', updated_at = ?
                WHERE session_id = ?
                """,
                (final_status_value, now, session_id_value),
            )
            conn.commit()
            row_after = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id_value,),
            ).fetchone()
            if row_after is None:
                raise SessionReceiptError("E_SESSION_NOT_FOUND", "session not found")
            return self._session_row_as_payload(row_after)
        finally:
            conn.close()

    def upsert_receipt(
        self,
        *,
        job_id: str,
        session_id: str = "",
        run_id: str = "",
        tool: str,
        args_hash: str,
        status: str,
        evidence_run: bool = False,
        changed_files: str = "",
        result_ref: str = "",
        error_code: str = "",
        error_message: str = "",
    ) -> dict[str, Any]:
        job_id_value = str(job_id).strip()
        tool_value = str(tool).strip()
        args_hash_value = str(args_hash).strip()
        if not job_id_value or not tool_value or not args_hash_value:
            raise SessionReceiptError(
                "E_INVALID_STATE",
                "job_id, tool, and args_hash are required",
            )
        status_value = _normalize_receipt_status(status)
        session_id_value = str(session_id).strip()
        run_id_value = str(run_id).strip()
        changed_files_value = str(changed_files).strip()
        result_ref_value = str(result_ref).strip()
        error_code_value = str(error_code).strip()
        error_message_value = str(error_message).strip()
        now = _utc_now()

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if session_id_value:
                session_row = conn.execute(
                    "SELECT * FROM sessions WHERE session_id = ?",
                    (session_id_value,),
                ).fetchone()
                if session_row is None:
                    raise SessionReceiptError("E_SESSION_NOT_FOUND", "session not found")
                session_run_id = str(session_row["run_id"])
                if run_id_value and session_run_id != run_id_value:
                    raise SessionReceiptError(
                        "E_SESSION_CONFLICT",
                        "run_id does not match session run_id",
                    )
                if not run_id_value:
                    run_id_value = session_run_id

            existing = conn.execute(
                "SELECT * FROM receipts WHERE job_id = ?",
                (job_id_value,),
            ).fetchone()
            if existing is not None:
                existing_hash = str(existing["args_hash"] or "")
                if existing_hash and existing_hash != args_hash_value:
                    raise SessionReceiptError(
                        "E_ARGS_HASH_MISMATCH",
                        "args_hash mismatch for existing job_id",
                    )
                previous_status = str(existing["status"] or "")
                conn.execute(
                    """
                    UPDATE receipts
                    SET session_id = ?, run_id = ?, tool = ?, args_hash = ?, status = ?, ts = ?,
                        evidence_run = ?, changed_files = ?, result_ref = ?, error_code = ?, error_message = ?
                    WHERE job_id = ?
                    """,
                    (
                        session_id_value or str(existing["session_id"] or ""),
                        run_id_value or str(existing["run_id"] or ""),
                        tool_value,
                        args_hash_value,
                        status_value,
                        now,
                        int(_normalize_bool_flag(evidence_run)),
                        changed_files_value,
                        result_ref_value,
                        error_code_value,
                        error_message_value,
                        job_id_value,
                    ),
                )
                if previous_status != status_value:
                    conn.execute(
                        """
                        INSERT INTO receipt_events(
                          job_id, session_id, run_id, tool, from_status, to_status, ts, error_code, error_message
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            job_id_value,
                            session_id_value or str(existing["session_id"] or ""),
                            run_id_value or str(existing["run_id"] or ""),
                            tool_value,
                            previous_status,
                            status_value,
                            now,
                            error_code_value,
                            error_message_value,
                        ),
                    )
            else:
                conn.execute(
                    """
                    INSERT INTO receipts(
                      job_id, session_id, run_id, tool, args_hash, status, ts, evidence_run,
                      changed_files, result_ref, error_code, error_message
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id_value,
                        session_id_value,
                        run_id_value,
                        tool_value,
                        args_hash_value,
                        status_value,
                        now,
                        int(_normalize_bool_flag(evidence_run)),
                        changed_files_value,
                        result_ref_value,
                        error_code_value,
                        error_message_value,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO receipt_events(
                      job_id, session_id, run_id, tool, from_status, to_status, ts, error_code, error_message
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id_value,
                        session_id_value,
                        run_id_value,
                        tool_value,
                        "none",
                        status_value,
                        now,
                        error_code_value,
                        error_message_value,
                    ),
                )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM receipts WHERE job_id = ?",
                (job_id_value,),
            ).fetchone()
            if row is None:
                raise SessionReceiptError("E_JOB_NOT_FOUND", "receipt not found")
            return self._receipt_row_as_payload(row)
        finally:
            conn.close()

    def update_receipt_status(
        self,
        *,
        job_id: str,
        status: str,
        result_ref: str = "",
        error_code: str = "",
        error_message: str = "",
    ) -> dict[str, Any]:
        existing = self.get_receipt(job_id=job_id)
        if existing is None:
            raise SessionReceiptError("E_JOB_NOT_FOUND", "receipt not found")
        return self.upsert_receipt(
            job_id=str(existing["job_id"]),
            session_id=str(existing["session_id"]),
            run_id=str(existing["run_id"]),
            tool=str(existing["tool"]),
            args_hash=str(existing["args_hash"]),
            status=status,
            evidence_run=bool(existing["evidence_run"]),
            changed_files=str(existing["changed_files"]),
            result_ref=result_ref or str(existing["result_ref"]),
            error_code=error_code or str(existing["error_code"]),
            error_message=error_message or str(existing["error_message"]),
        )

    def get_receipt(self, *, job_id: str) -> dict[str, Any] | None:
        job_id_value = str(job_id).strip()
        if not job_id_value:
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM receipts WHERE job_id = ?",
                (job_id_value,),
            ).fetchone()
            if row is None:
                return None
            return self._receipt_row_as_payload(row)
        finally:
            conn.close()

    def list_receipts(
        self,
        *,
        session_id: str = "",
        run_id: str = "",
        tool: str = "",
        status: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        filters: list[str] = []
        params: list[Any] = []
        session_id_value = str(session_id).strip()
        run_id_value = str(run_id).strip()
        tool_value = str(tool).strip()
        status_value = str(status).strip().lower()
        limit_value = max(1, min(500, int(limit)))
        if session_id_value:
            filters.append("session_id = ?")
            params.append(session_id_value)
        if run_id_value:
            filters.append("run_id = ?")
            params.append(run_id_value)
        if tool_value:
            filters.append("tool = ?")
            params.append(tool_value)
        if status_value:
            filters.append("status = ?")
            params.append(status_value)

        where_clause = ""
        if filters:
            where_clause = " WHERE " + " AND ".join(filters)
        sql = "SELECT * FROM receipts" + where_clause + " ORDER BY ts DESC, job_id DESC LIMIT ?"
        params.append(limit_value)

        conn = self._connect()
        try:
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [self._receipt_row_as_payload(row) for row in rows]
        finally:
            conn.close()

    def recover_expired_running_receipts(self, *, terminal_status: str = "failed") -> int:
        terminal_status_value = _normalize_receipt_status(terminal_status)
        if terminal_status_value not in {"failed", "canceled"}:
            raise SessionReceiptError(
                "E_INVALID_STATE",
                "terminal_status must be failed or canceled",
            )
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT r.job_id
                FROM receipts AS r
                INNER JOIN sessions AS s ON s.session_id = r.session_id
                WHERE r.status IN (?, ?)
                  AND s.lease_until != ''
                  AND s.lease_until < ?
                """,
                ("queued", "running", now),
            ).fetchall()
            affected = 0
            for row in rows:
                job_id_value = str(row["job_id"] or "").strip()
                if not job_id_value:
                    continue
                receipt_row = conn.execute(
                    "SELECT * FROM receipts WHERE job_id = ?",
                    (job_id_value,),
                ).fetchone()
                if receipt_row is None:
                    continue
                previous_status = str(receipt_row["status"] or "")
                if previous_status not in _RECOVERABLE_RECEIPT_STATUSES:
                    continue
                session_id_value = str(receipt_row["session_id"] or "")
                run_id_value = str(receipt_row["run_id"] or "")
                tool_value = str(receipt_row["tool"] or "")
                conn.execute(
                    """
                    UPDATE receipts
                    SET status = ?, ts = ?, error_code = ?, error_message = ?
                    WHERE job_id = ?
                    """,
                    (
                        terminal_status_value,
                        now,
                        "E_SESSION_EXPIRED",
                        "session lease expired while receipt remained running",
                        job_id_value,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO receipt_events(
                      job_id, session_id, run_id, tool, from_status, to_status, ts, error_code, error_message
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id_value,
                        session_id_value,
                        run_id_value,
                        tool_value,
                        previous_status,
                        terminal_status_value,
                        now,
                        "E_SESSION_EXPIRED",
                        "session lease expired while receipt remained running",
                    ),
                )
                if session_id_value:
                    conn.execute(
                        """
                        UPDATE sessions
                        SET status = ?, updated_at = ?
                        WHERE session_id = ?
                        """,
                        ("expired", now, session_id_value),
                    )
                affected += 1
            conn.commit()
            return int(affected)
        finally:
            conn.close()


__all__ = ["SessionReceiptError", "SessionReceiptStore"]
