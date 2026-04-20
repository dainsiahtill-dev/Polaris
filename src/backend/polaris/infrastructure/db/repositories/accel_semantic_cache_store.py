"""Semantic cache store (migrated from legacy accel storage)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.infrastructure.db.adapters import SqliteAdapter
from polaris.kernelone.db import KernelDatabase
from polaris.kernelone.utils.time_utils import utc_now

if TYPE_CHECKING:
    import sqlite3

logger = logging.getLogger(__name__)

_SQL_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


# Backward compatibility alias
_utc_now = utc_now


def _validate_sql_identifier(name: str, identifier_type: str = "identifier") -> None:
    """验证 SQL 标识符（表名、列名）以防止注入攻击."""
    if not name or not isinstance(name, str):
        raise ValueError(f"Invalid {identifier_type}: must be a non-empty string")
    if not _SQL_IDENTIFIER_RE.match(name):
        raise ValueError(f"Invalid {identifier_type}: '{name}' contains invalid characters")


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_utc(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_path_token(value: str) -> str:
    return str(value or "").replace("\\", "/").strip().lower()


def normalize_token_list(values: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in values or []:
        token = str(item or "").strip().lower()
        if not token:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def normalize_changed_files(values: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in values or []:
        token = _normalize_path_token(item)
        if not token:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def make_stable_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def context_changed_fingerprint(changed_files: list[str]) -> str:
    return make_stable_hash({"changed_files": normalize_changed_files(changed_files)})


def task_signature(task_tokens: list[str], hint_tokens: list[str]) -> str:
    return make_stable_hash(
        {
            "task_tokens": normalize_token_list(task_tokens),
            "hint_tokens": normalize_token_list(hint_tokens),
        }
    )


def jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    inter = len(left.intersection(right))
    union = len(left.union(right))
    if union <= 0:
        return 0.0
    return float(inter) / float(union)


class SemanticCacheStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._kernel_db = KernelDatabase(
            str(self._db_path.parent),
            sqlite_adapter=SqliteAdapter(),
            allow_unmanaged_absolute=True,
        )
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return self._kernel_db.sqlite(
            str(self._db_path),
            timeout_seconds=10.0,
            isolation_level=None,
            check_same_thread=False,
            pragmas={
                "busy_timeout": 5000,
                "journal_mode": "WAL",
                "synchronous": "NORMAL",
            },
            ensure_parent=True,
        )

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS context_cache (
                      cache_key TEXT PRIMARY KEY,
                      task_signature TEXT NOT NULL,
                      task_tokens_json TEXT NOT NULL,
                      hint_tokens_json TEXT NOT NULL,
                      changed_files_json TEXT NOT NULL,
                      changed_fingerprint TEXT NOT NULL,
                      budget_fingerprint TEXT NOT NULL,
                      config_hash TEXT NOT NULL,
                      safety_fingerprint TEXT NOT NULL DEFAULT '',
                      git_head TEXT NOT NULL DEFAULT '',
                      changed_files_state_json TEXT NOT NULL DEFAULT '[]',
                      payload_json TEXT NOT NULL,
                      created_utc TEXT NOT NULL,
                      expires_utc TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS verify_plan_cache (
                      cache_key TEXT PRIMARY KEY,
                      changed_fingerprint TEXT NOT NULL,
                      runtime_fingerprint TEXT NOT NULL,
                      config_hash TEXT NOT NULL,
                      commands_json TEXT NOT NULL,
                      created_utc TEXT NOT NULL,
                      expires_utc TEXT NOT NULL
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_context_expiry ON context_cache(expires_utc)")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_context_lookup ON context_cache(task_signature, budget_fingerprint, config_hash)"
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_verify_plan_expiry ON verify_plan_cache(expires_utc)")
                self._ensure_column(
                    conn,
                    table_name="context_cache",
                    column_name="safety_fingerprint",
                    column_ddl="TEXT NOT NULL DEFAULT ''",
                )
                self._ensure_column(
                    conn,
                    table_name="context_cache",
                    column_name="git_head",
                    column_ddl="TEXT NOT NULL DEFAULT ''",
                )
                self._ensure_column(
                    conn,
                    table_name="context_cache",
                    column_name="changed_files_state_json",
                    column_ddl="TEXT NOT NULL DEFAULT '[]'",
                )
            finally:
                conn.close()

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        *,
        table_name: str,
        column_name: str,
        column_ddl: str,
    ) -> None:
        _validate_sql_identifier(table_name, "table")
        _validate_sql_identifier(column_name, "column")
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing = {str(row[1]) for row in rows if len(row) > 1}
        if column_name in existing:
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_ddl}")

    def _prune_table(self, conn: sqlite3.Connection, table_name: str, max_entries: int) -> None:
        _validate_sql_identifier(table_name, "table")
        now_text = _utc_text(_utc_now())
        conn.execute(f"DELETE FROM {table_name} WHERE expires_utc <= ?", (now_text,))
        max_keep = max(1, int(max_entries))
        count_row = conn.execute(f"SELECT COUNT(1) FROM {table_name}").fetchone()
        count = int(count_row[0]) if count_row else 0
        if count <= max_keep:
            return
        overflow = count - max_keep
        conn.execute(
            f"""
            DELETE FROM {table_name}
            WHERE cache_key IN (
              SELECT cache_key FROM {table_name}
              ORDER BY created_utc ASC
              LIMIT ?
            )
            """,
            (overflow,),
        )

    def get_context_exact(self, cache_key: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT payload_json, expires_utc
                    FROM context_cache
                    WHERE cache_key = ?
                    """,
                    (cache_key,),
                ).fetchone()
                if row is None:
                    return None
                expires = _parse_utc(str(row[1]))
                if expires is None or expires <= _utc_now():
                    conn.execute("DELETE FROM context_cache WHERE cache_key = ?", (cache_key,))
                    return None
                payload = json.loads(str(row[0]))
                if not isinstance(payload, dict):
                    return None
                return payload
            finally:
                conn.close()

    def get_context_hybrid(
        self,
        *,
        task_tokens: list[str],
        hint_tokens: list[str],
        changed_files: list[str],
        budget_fingerprint: str,
        config_hash: str,
        threshold: float,
        safety_fingerprint: str = "",
        max_candidates: int = 80,
    ) -> tuple[dict[str, Any] | None, float]:
        task_set = set(normalize_token_list(task_tokens + hint_tokens))
        changed_set = set(normalize_changed_files(changed_files))
        min_threshold = max(0.0, min(1.0, float(threshold)))
        best_payload: dict[str, Any] | None = None
        best_score = 0.0

        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT payload_json, task_tokens_json, hint_tokens_json, changed_files_json
                    FROM context_cache
                    WHERE budget_fingerprint = ?
                      AND config_hash = ?
                      AND safety_fingerprint = ?
                      AND expires_utc > ?
                    ORDER BY created_utc DESC
                    LIMIT ?
                    """,
                    (
                        str(budget_fingerprint),
                        str(config_hash),
                        str(safety_fingerprint or ""),
                        _utc_text(_utc_now()),
                        max(1, int(max_candidates)),
                    ),
                ).fetchall()
            finally:
                conn.close()

        for row in rows:
            try:
                payload = json.loads(str(row[0]))
                row_task_tokens = set(normalize_token_list(json.loads(str(row[1]))))
                row_hint_tokens = set(normalize_token_list(json.loads(str(row[2]))))
                row_changed_files = set(normalize_changed_files(json.loads(str(row[3]))))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue

            row_task_set = row_task_tokens.union(row_hint_tokens)
            task_score = jaccard_similarity(task_set, row_task_set)
            changed_score = jaccard_similarity(changed_set, row_changed_files)

            if changed_set or row_changed_files:
                score = (0.7 * task_score) + (0.3 * changed_score)
            else:
                score = (0.9 * task_score) + 0.1
            score = max(0.0, min(1.0, score))
            if score >= min_threshold and score > best_score:
                best_score = score
                best_payload = payload

        return best_payload, float(best_score)

    def put_context(
        self,
        *,
        cache_key: str,
        task_signature_value: str,
        task_tokens: list[str],
        hint_tokens: list[str],
        changed_files: list[str],
        changed_fingerprint: str,
        budget_fingerprint: str,
        config_hash: str,
        payload: dict[str, Any],
        ttl_seconds: int,
        max_entries: int,
        safety_fingerprint: str = "",
        git_head: str = "",
        changed_files_state: list[dict[str, Any]] | None = None,
    ) -> None:
        created = _utc_now()
        expires = created + timedelta(seconds=max(1, int(ttl_seconds)))
        changed_state_payload = changed_files_state if isinstance(changed_files_state, list) else []
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO context_cache (
                      cache_key,
                      task_signature,
                      task_tokens_json,
                      hint_tokens_json,
                      changed_files_json,
                      changed_fingerprint,
                      budget_fingerprint,
                      config_hash,
                      safety_fingerprint,
                      git_head,
                      changed_files_state_json,
                      payload_json,
                      created_utc,
                      expires_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                      task_signature=excluded.task_signature,
                      task_tokens_json=excluded.task_tokens_json,
                      hint_tokens_json=excluded.hint_tokens_json,
                      changed_files_json=excluded.changed_files_json,
                      changed_fingerprint=excluded.changed_fingerprint,
                      budget_fingerprint=excluded.budget_fingerprint,
                      config_hash=excluded.config_hash,
                      safety_fingerprint=excluded.safety_fingerprint,
                      git_head=excluded.git_head,
                      changed_files_state_json=excluded.changed_files_state_json,
                      payload_json=excluded.payload_json,
                      created_utc=excluded.created_utc,
                      expires_utc=excluded.expires_utc
                    """,
                    (
                        str(cache_key),
                        str(task_signature_value),
                        json.dumps(normalize_token_list(task_tokens), ensure_ascii=False),
                        json.dumps(normalize_token_list(hint_tokens), ensure_ascii=False),
                        json.dumps(normalize_changed_files(changed_files), ensure_ascii=False),
                        str(changed_fingerprint),
                        str(budget_fingerprint),
                        str(config_hash),
                        str(safety_fingerprint or ""),
                        str(git_head or ""),
                        json.dumps(changed_state_payload, ensure_ascii=False),
                        json.dumps(payload, ensure_ascii=False),
                        _utc_text(created),
                        _utc_text(expires),
                    ),
                )
                self._prune_table(conn, "context_cache", max_entries=max_entries)
            finally:
                conn.close()

    def explain_context_miss(
        self,
        *,
        task_signature_value: str,
        budget_fingerprint: str,
        config_hash: str,
        safety_fingerprint: str,
        changed_fingerprint: str,
        git_head: str,
    ) -> dict[str, Any]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT changed_fingerprint, safety_fingerprint, git_head, expires_utc
                    FROM context_cache
                    WHERE task_signature = ?
                      AND budget_fingerprint = ?
                      AND config_hash = ?
                    ORDER BY created_utc DESC
                    LIMIT 1
                    """,
                    (
                        str(task_signature_value),
                        str(budget_fingerprint),
                        str(config_hash),
                    ),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return {"reason": "no_prior_entry"}
        prev_changed_fingerprint = str(row[0] or "")
        prev_safety_fingerprint = str(row[1] or "")
        prev_git_head = str(row[2] or "")
        expires = _parse_utc(str(row[3]))
        now = _utc_now()
        if expires is not None and expires <= now:
            return {"reason": "expired"}
        if prev_safety_fingerprint and prev_safety_fingerprint != str(safety_fingerprint):
            if prev_git_head and git_head and prev_git_head != str(git_head):
                return {"reason": "git_head_changed"}
            if prev_changed_fingerprint != str(changed_fingerprint):
                return {"reason": "changed_files_set_changed"}
            return {"reason": "changed_files_state_changed"}
        if prev_changed_fingerprint != str(changed_fingerprint):
            return {"reason": "changed_files_set_changed"}
        return {"reason": "similarity_below_threshold_or_not_cached"}

    def get_verify_plan(self, cache_key: str) -> list[str] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT commands_json, expires_utc
                    FROM verify_plan_cache
                    WHERE cache_key = ?
                    """,
                    (cache_key,),
                ).fetchone()
                if row is None:
                    return None
                expires = _parse_utc(str(row[1]))
                if expires is None or expires <= _utc_now():
                    conn.execute("DELETE FROM verify_plan_cache WHERE cache_key = ?", (cache_key,))
                    return None
                payload = json.loads(str(row[0]))
                if not isinstance(payload, list):
                    return None
                return [str(item) for item in payload if str(item).strip()]
            finally:
                conn.close()

    def put_verify_plan(
        self,
        *,
        cache_key: str,
        changed_fingerprint: str,
        runtime_fingerprint: str,
        config_hash: str,
        commands: list[str],
        ttl_seconds: int,
        max_entries: int,
    ) -> None:
        created = _utc_now()
        expires = created + timedelta(seconds=max(1, int(ttl_seconds)))
        normalized_commands = [str(item) for item in commands if str(item).strip()]

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO verify_plan_cache (
                      cache_key,
                      changed_fingerprint,
                      runtime_fingerprint,
                      config_hash,
                      commands_json,
                      created_utc,
                      expires_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                      changed_fingerprint=excluded.changed_fingerprint,
                      runtime_fingerprint=excluded.runtime_fingerprint,
                      config_hash=excluded.config_hash,
                      commands_json=excluded.commands_json,
                      created_utc=excluded.created_utc,
                      expires_utc=excluded.expires_utc
                    """,
                    (
                        str(cache_key),
                        str(changed_fingerprint),
                        str(runtime_fingerprint),
                        str(config_hash),
                        json.dumps(normalized_commands, ensure_ascii=False),
                        _utc_text(created),
                        _utc_text(expires),
                    ),
                )
                self._prune_table(conn, "verify_plan_cache", max_entries=max_entries)
            finally:
                conn.close()


__all__ = [
    "SemanticCacheStore",
    "context_changed_fingerprint",
    "jaccard_similarity",
    "make_stable_hash",
    "normalize_changed_files",
    "normalize_token_list",
    "task_signature",
]
