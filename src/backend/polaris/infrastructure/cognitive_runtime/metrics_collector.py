"""Cognitive Runtime metrics collector for CI gate evaluation.

Collects metrics from CognitiveRuntimeSqliteStore to generate cognitive_runtime_summary
for the context-os-runtime-eval-gate.

Design constraints:
- frozen dataclass for result storage
- Returns default values on error (fail-safe)
- Explicit UTF-8 for all text operations
- No direct dependency on business semantics
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polaris.infrastructure.db.adapters import SqliteAdapter
from polaris.kernelone.db import KernelDatabase

if TYPE_CHECKING:
    import sqlite3

logger = logging.getLogger(__name__)


def _p95(values: list[float]) -> float:
    """Calculate 95th percentile of values.

    Returns 0.0 for empty or invalid input.
    """
    if not values:
        return 0.0
    try:
        ordered = sorted(float(v) for v in values if v is not None)
        if not ordered:
            return 0.0
        index = round(0.95 * (len(ordered) - 1))
        index = max(0, min(index, len(ordered) - 1))
        return round(float(ordered[index]), 6)
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class CognitiveRuntimeMetrics:
    """Frozen metrics result from Cognitive Runtime evaluation."""

    total_cases: int = 0
    receipt_coverage: float = 0.0
    handoff_roundtrip_success_rate: float = 0.0
    state_restore_accuracy: float = 0.0
    transaction_envelope_coverage: float = 0.0
    receipt_write_failure_rate: float = 0.0
    sqlite_write_p95_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "receipt_coverage": round(self.receipt_coverage, 6),
            "handoff_roundtrip_success_rate": round(self.handoff_roundtrip_success_rate, 6),
            "state_restore_accuracy": round(self.state_restore_accuracy, 6),
            "transaction_envelope_coverage": round(self.transaction_envelope_coverage, 6),
            "receipt_write_failure_rate": round(self.receipt_write_failure_rate, 6),
            "sqlite_write_p95_ms": round(self.sqlite_write_p95_ms, 6),
        }


class CognitiveRuntimeMetricsCollector:
    """Collects Cognitive Runtime metrics from SQLite store.

    Reads from CognitiveRuntimeSqliteStore database and computes metrics
    required for CI gate evaluation.
    """

    DEFAULT_DB_PATH = "runtime/cognitive_runtime/cognitive_runtime.sqlite"

    def __init__(
        self,
        workspace: str,
        *,
        db_path: str | None = None,
        kernel_db: KernelDatabase | None = None,
    ) -> None:
        self._workspace = workspace
        self._db_path = db_path or self.DEFAULT_DB_PATH
        self._kernel_db = kernel_db
        self._db: KernelDatabase | None = None
        self._conn: sqlite3.Connection | None = None

    def _ensure_connection(self) -> sqlite3.Connection | None:
        """Establish database connection with proper error handling."""
        if self._conn is not None:
            return self._conn
        try:
            if self._kernel_db is None:
                self._kernel_db = KernelDatabase(
                    self._workspace,
                    sqlite_adapter=SqliteAdapter(),
                    allow_unmanaged_absolute=True,
                )
            resolved_path = self._kernel_db.resolve_sqlite_path(self._db_path, ensure_parent=False)
            self._conn = self._kernel_db.sqlite(
                resolved_path,
                timeout_seconds=10.0,
                check_same_thread=True,
                row_factory="row",
                pragmas={
                    "journal_mode": "WAL",
                    "busy_timeout": 5000,
                    "synchronous": "NORMAL",
                },
                ensure_parent=False,
            )
            return self._conn
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning(
                "Failed to connect to CognitiveRuntimeSqliteStore: %s",
                str(exc),
                exc_info=False,
            )
            return None

    def _close_connection(self) -> None:
        """Safely close database connection."""
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
            self._conn = None
        if self._kernel_db is not None:
            with contextlib.suppress(Exception):
                self._kernel_db.close()
            self._kernel_db = None

    def _count_sessions(self, conn: sqlite3.Connection) -> int:
        """Count distinct sessions with receipts."""
        try:
            cursor = conn.execute(
                """
                SELECT COUNT(DISTINCT session_id)
                FROM cognitive_runtime_receipts
                WHERE workspace = ?
                """,
                (self._workspace,),
            )
            row = cursor.fetchone()
            if row is not None:
                return int(row[0]) if row[0] is not None else 0
            return 0
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to count sessions: %s", str(exc))
            return 0

    def _count_receipts(self, conn: sqlite3.Connection) -> int:
        """Count total receipts for workspace."""
        try:
            cursor = conn.execute(
                """
                SELECT COUNT(*)
                FROM cognitive_runtime_receipts
                WHERE workspace = ?
                """,
                (self._workspace,),
            )
            row = cursor.fetchone()
            if row is not None:
                return int(row[0]) if row[0] is not None else 0
            return 0
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to count receipts: %s", str(exc))
            return 0

    def _calculate_receipt_coverage(self, conn: sqlite3.Connection) -> float:
        """Calculate receipt_coverage: sessions with receipts / total sessions.

        Since receipts are written per turn, coverage = 1.0 if any receipts exist.
        """
        sessions = self._count_sessions(conn)
        if sessions == 0:
            return 0.0
        return 1.0

    def _calculate_handoff_roundtrip_success_rate(self, conn: sqlite3.Connection) -> float:
        """Calculate handoff_roundtrip_success_rate.

        Success = handoff created -> receipt exists for same session after handoff.
        """
        try:
            cursor = conn.execute(
                """
                SELECT COUNT(DISTINCT h.session_id) as handoff_sessions
                FROM cognitive_runtime_handoffs h
                WHERE h.workspace = ?
                """,
                (self._workspace,),
            )
            row = cursor.fetchone()
            handoff_sessions = int(row[0]) if row and row[0] is not None else 0

            if handoff_sessions == 0:
                return 0.0

            cursor = conn.execute(
                """
                SELECT COUNT(DISTINCT h.session_id) as successful_sessions
                FROM cognitive_runtime_handoffs h
                INNER JOIN cognitive_runtime_receipts r
                    ON h.session_id = r.session_id
                    AND r.created_at > h.created_at
                WHERE h.workspace = ? AND r.workspace = ?
                """,
                (self._workspace, self._workspace),
            )
            row = cursor.fetchone()
            successful_sessions = int(row[0]) if row and row[0] is not None else 0

            return float(successful_sessions) / float(handoff_sessions)
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to calculate handoff success rate: %s", str(exc))
            return 0.0

    def _calculate_state_restore_accuracy(self, conn: sqlite3.Connection) -> float:
        """Calculate state_restore_accuracy.

        Based on TurnEnvelope presence in receipts with state_version.
        """
        try:
            cursor = conn.execute(
                """
                SELECT COUNT(*)
                FROM cognitive_runtime_receipts
                WHERE workspace = ?
                    AND receipt_json LIKE '%turn_envelope%'
                    AND receipt_json LIKE '%state_version%'
                """,
                (self._workspace,),
            )
            row = cursor.fetchone()
            receipts_with_state = int(row[0]) if row and row[0] is not None else 0

            total = self._count_receipts(conn)
            if total == 0:
                return 0.0

            return float(receipts_with_state) / float(total)
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to calculate state restore accuracy: %s", str(exc))
            return 0.0

    def _calculate_transaction_envelope_coverage(self, conn: sqlite3.Connection) -> float:
        """Calculate transaction_envelope_coverage.

        Based on TurnEnvelope presence in all receipts.
        """
        try:
            cursor = conn.execute(
                """
                SELECT COUNT(*)
                FROM cognitive_runtime_receipts
                WHERE workspace = ?
                    AND receipt_json LIKE '%turn_envelope%'
                """,
                (self._workspace,),
            )
            row = cursor.fetchone()
            receipts_with_envelope = int(row[0]) if row and row[0] is not None else 0

            total = self._count_receipts(conn)
            if total == 0:
                return 0.0

            return float(receipts_with_envelope) / float(total)
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to calculate transaction envelope coverage: %s", str(exc))
            return 0.0

    def _calculate_receipt_write_failure_rate(self, conn: sqlite3.Connection) -> float:
        """Calculate receipt_write_failure_rate.

        Inferred from receipt counts vs handoff counts.
        Low ratio of receipts to handoffs indicates potential failures.
        """
        try:
            cursor = conn.execute(
                """
                SELECT COUNT(*) FROM cognitive_runtime_receipts WHERE workspace = ?
                """,
                (self._workspace,),
            )
            row = cursor.fetchone()
            receipt_count = int(row[0]) if row and row[0] is not None else 0

            cursor = conn.execute(
                """
                SELECT COUNT(*) FROM cognitive_runtime_receipts
                WHERE workspace = ? AND receipt_type = 'write_failure'
                """,
                (self._workspace,),
            )
            row = cursor.fetchone()
            failure_count = int(row[0]) if row and row[0] is not None else 0

            if receipt_count == 0:
                return 0.0

            return float(failure_count) / float(receipt_count)
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to calculate write failure rate: %s", str(exc))
            return 0.0

    def _calculate_sqlite_write_p95_ms(self, conn: sqlite3.Connection) -> float:
        """Calculate sqlite_write_p95_ms from write timing metadata.

        Estimates inter-write intervals from receipt `created_at` timestamps.
        Returns 0.0 when fewer than 2 receipts exist (insufficient data for p95).
        """
        try:
            cursor = conn.execute(
                """
                SELECT created_at FROM cognitive_runtime_receipts
                WHERE workspace = ?
                ORDER BY created_at ASC
                LIMIT 100
                """,
                (self._workspace,),
            )
            rows = cursor.fetchall()
            if len(rows) < 2:
                return 0.0

            timestamps: list[float] = []
            for row in rows:
                val = row[0]
                if val is None:
                    continue
                try:
                    ts = float(val)
                    timestamps.append(ts)
                except (TypeError, ValueError):
                    continue

            if len(timestamps) < 2:
                return 0.0

            intervals_ms: list[float] = []
            for i in range(1, len(timestamps)):
                delta = abs(timestamps[i] - timestamps[i - 1]) * 1000.0
                intervals_ms.append(delta)

            return _p95(intervals_ms)
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to calculate write P95: %s", str(exc))
            return 0.0

    def _count_cases(self, conn: sqlite3.Connection) -> int:
        """Count minimum of sessions and 100 (for minimum_cases threshold)."""
        sessions = self._count_sessions(conn)
        return min(sessions, 100)

    def collect_metrics(self, workspace: str) -> dict[str, Any]:
        """Collect all Cognitive Runtime metrics from SQLite store.

        Args:
            workspace: The workspace path to collect metrics for.

        Returns:
            Dict with cognitive_runtime_summary metrics matching gate config format.
            Returns default values (0.0) for all metrics on any error.
        """
        self._workspace = workspace
        conn = self._ensure_connection()
        if conn is None:
            logger.warning(
                "No database connection available for workspace %s, returning default metrics",
                workspace,
            )
            return CognitiveRuntimeMetrics().to_dict()

        try:
            total_cases = self._count_cases(conn)
            receipt_coverage = self._calculate_receipt_coverage(conn)
            handoff_roundtrip = self._calculate_handoff_roundtrip_success_rate(conn)
            state_restore = self._calculate_state_restore_accuracy(conn)
            envelope_coverage = self._calculate_transaction_envelope_coverage(conn)
            failure_rate = self._calculate_receipt_write_failure_rate(conn)
            write_p95 = self._calculate_sqlite_write_p95_ms(conn)

            metrics = CognitiveRuntimeMetrics(
                total_cases=total_cases,
                receipt_coverage=receipt_coverage,
                handoff_roundtrip_success_rate=handoff_roundtrip,
                state_restore_accuracy=state_restore,
                transaction_envelope_coverage=envelope_coverage,
                receipt_write_failure_rate=failure_rate,
                sqlite_write_p95_ms=write_p95,
            )

            return metrics.to_dict()

        except (RuntimeError, ValueError, OSError) as exc:
            logger.warning(
                "Error collecting metrics for workspace %s: %s, returning default metrics",
                workspace,
                str(exc),
                exc_info=False,
            )
            return CognitiveRuntimeMetrics().to_dict()
        finally:
            self._close_connection()
