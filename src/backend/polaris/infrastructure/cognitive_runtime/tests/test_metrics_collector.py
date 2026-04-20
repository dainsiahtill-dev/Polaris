"""Unit tests for CognitiveRuntimeMetricsCollector."""

from __future__ import annotations

import contextlib
import os
import tempfile
import time

import pytest
from polaris.domain.cognitive_runtime import ContextHandoffPack, RuntimeReceipt, TurnEnvelope
from polaris.infrastructure.cognitive_runtime import (
    CognitiveRuntimeMetrics,
    CognitiveRuntimeMetricsCollector,
    CognitiveRuntimeSqliteStore,
)


def _build_store() -> tuple[str, str]:
    """Create a temporary workspace and database path."""
    workspace = tempfile.mkdtemp(prefix="cognitive-runtime-metrics-")
    fd, db_path = tempfile.mkstemp(prefix="cognitive-runtime-metrics-", suffix=".sqlite")
    os.close(fd)
    if os.path.exists(db_path):
        os.unlink(db_path)
    return workspace, db_path


class TestCognitiveRuntimeMetrics:
    """Tests for CognitiveRuntimeMetrics frozen dataclass."""

    def test_metrics_to_dict(self) -> None:
        """Verify to_dict produces correct structure and rounding."""
        metrics = CognitiveRuntimeMetrics(
            total_cases=42,
            receipt_coverage=0.987654321,
            handoff_roundtrip_success_rate=0.999999999,
            state_restore_accuracy=0.98,
            transaction_envelope_coverage=0.985,
            receipt_write_failure_rate=0.0001,
            sqlite_write_p95_ms=35.555555,
        )
        result = metrics.to_dict()

        assert result["total_cases"] == 42
        assert result["receipt_coverage"] == 0.987654
        assert result["handoff_roundtrip_success_rate"] == 1.0
        assert result["state_restore_accuracy"] == 0.98
        assert result["transaction_envelope_coverage"] == 0.985
        assert result["receipt_write_failure_rate"] == 0.0001
        assert result["sqlite_write_p95_ms"] == 35.555555

    def test_metrics_default_values(self) -> None:
        """Verify default metrics have expected zero values."""
        metrics = CognitiveRuntimeMetrics()
        result = metrics.to_dict()

        assert result["total_cases"] == 0
        assert result["receipt_coverage"] == 0.0
        assert result["handoff_roundtrip_success_rate"] == 0.0
        assert result["state_restore_accuracy"] == 0.0
        assert result["transaction_envelope_coverage"] == 0.0
        assert result["receipt_write_failure_rate"] == 0.0
        assert result["sqlite_write_p95_ms"] == 0.0

    def test_metrics_are_frozen(self) -> None:
        """Verify metrics dataclass is frozen."""
        metrics = CognitiveRuntimeMetrics()
        with pytest.raises(Exception):  # frozen dataclass raises FrozenInstanceError
            metrics.total_cases = 10  # type: ignore[misc]


class TestCognitiveRuntimeMetricsCollector:
    """Tests for CognitiveRuntimeMetricsCollector."""

    def test_collect_metrics_returns_dict(self) -> None:
        """Verify collect_metrics returns a dict."""
        workspace, db_path = _build_store()
        try:
            store = CognitiveRuntimeSqliteStore(workspace, db_path=db_path)
            collector = CognitiveRuntimeMetricsCollector(
                workspace,
                db_path=db_path,
                kernel_db=store._kernel_db,
            )
            result = collector.collect_metrics(workspace)

            assert isinstance(result, dict)
            assert "total_cases" in result
            assert "receipt_coverage" in result
            assert "handoff_roundtrip_success_rate" in result
            assert "state_restore_accuracy" in result
            assert "transaction_envelope_coverage" in result
            assert "receipt_write_failure_rate" in result
            assert "sqlite_write_p95_ms" in result
            store.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_collect_metrics_empty_workspace(self) -> None:
        """Verify metrics with no data returns zeros."""
        workspace, db_path = _build_store()
        try:
            store = CognitiveRuntimeSqliteStore(workspace, db_path=db_path)
            collector = CognitiveRuntimeMetricsCollector(
                workspace,
                db_path=db_path,
                kernel_db=store._kernel_db,
            )
            result = collector.collect_metrics(workspace)

            assert result["total_cases"] == 0
            assert result["receipt_coverage"] == 0.0
            store.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_collect_metrics_with_receipts(self) -> None:
        """Verify receipt_coverage is 1.0 when receipts exist."""
        workspace, db_path = _build_store()
        try:
            store = CognitiveRuntimeSqliteStore(workspace, db_path=db_path)
            for i in range(5):
                store.append_receipt(
                    RuntimeReceipt(
                        receipt_id=f"receipt-{i}",
                        receipt_type="turn",
                        workspace=workspace,
                        created_at=f"2026-03-27T00:00:{i:02d}+00:00",
                        payload={"index": i},
                        session_id="session-1",
                        turn_envelope=TurnEnvelope(
                            turn_id=f"turn-{i}",
                            state_version=i,
                        ),
                    )
                )
            time.sleep(0.1)

            collector = CognitiveRuntimeMetricsCollector(
                workspace,
                db_path=db_path,
                kernel_db=store._kernel_db,
            )
            result = collector.collect_metrics(workspace)

            assert result["total_cases"] == 1
            assert result["receipt_coverage"] == 1.0
            assert result["state_restore_accuracy"] == 1.0
            assert result["transaction_envelope_coverage"] == 1.0
            store.close()
        finally:
            time.sleep(0.1)
            if os.path.exists(db_path):
                with contextlib.suppress(PermissionError):
                    os.unlink(db_path)

    def test_collect_metrics_handoff_roundtrip(self) -> None:
        """Verify handoff_roundtrip_success_rate calculation."""
        workspace, db_path = _build_store()
        try:
            store = CognitiveRuntimeSqliteStore(workspace, db_path=db_path)
            store.save_handoff_pack(
                ContextHandoffPack(
                    handoff_id="handoff-1",
                    workspace=workspace,
                    created_at="2026-03-27T00:00:00+00:00",
                    session_id="session-1",
                )
            )
            store.append_receipt(
                RuntimeReceipt(
                    receipt_id="receipt-after-handoff",
                    receipt_type="turn",
                    workspace=workspace,
                    created_at="2026-03-27T00:00:01+00:00",
                    payload={},
                    session_id="session-1",
                )
            )
            time.sleep(0.1)

            collector = CognitiveRuntimeMetricsCollector(
                workspace,
                db_path=db_path,
                kernel_db=store._kernel_db,
            )
            result = collector.collect_metrics(workspace)

            assert result["handoff_roundtrip_success_rate"] == 1.0
            store.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_collect_metrics_handoff_no_receipt(self) -> None:
        """Verify handoff_roundtrip_success_rate is 0 when no receipt after handoff."""
        workspace, db_path = _build_store()
        try:
            store = CognitiveRuntimeSqliteStore(workspace, db_path=db_path)
            store.save_handoff_pack(
                ContextHandoffPack(
                    handoff_id="handoff-no-receipt",
                    workspace=workspace,
                    created_at="2026-03-27T00:00:00+00:00",
                    session_id="session-no-receipt",
                )
            )
            time.sleep(0.1)

            collector = CognitiveRuntimeMetricsCollector(
                workspace,
                db_path=db_path,
                kernel_db=store._kernel_db,
            )
            result = collector.collect_metrics(workspace)

            assert result["handoff_roundtrip_success_rate"] == 0.0
            store.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_collect_metrics_failure_rate(self) -> None:
        """Verify receipt_write_failure_rate calculation."""
        workspace, db_path = _build_store()
        try:
            store = CognitiveRuntimeSqliteStore(workspace, db_path=db_path)
            for i in range(10):
                store.append_receipt(
                    RuntimeReceipt(
                        receipt_id=f"receipt-{i}",
                        receipt_type="turn",
                        workspace=workspace,
                        created_at=f"2026-03-27T00:00:{i:02d}+00:00",
                        payload={},
                        session_id="session-1",
                    )
                )
            store.append_receipt(
                RuntimeReceipt(
                    receipt_id="receipt-failure-1",
                    receipt_type="write_failure",
                    workspace=workspace,
                    created_at="2026-03-27T00:00:11+00:00",
                    payload={},
                    session_id="session-1",
                )
            )
            time.sleep(0.1)

            collector = CognitiveRuntimeMetricsCollector(
                workspace,
                db_path=db_path,
                kernel_db=store._kernel_db,
            )
            result = collector.collect_metrics(workspace)

            assert result["receipt_write_failure_rate"] == pytest.approx(0.090909, abs=0.01)
            store.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_collect_metrics_connection_error(self) -> None:
        """Verify graceful handling of connection errors."""
        workspace, _ = _build_store()
        collector = CognitiveRuntimeMetricsCollector(
            workspace,
            db_path="/nonexistent/path/to/database.sqlite",
        )
        result = collector.collect_metrics(workspace)

        assert isinstance(result, dict)
        assert result["total_cases"] == 0
        assert result["receipt_coverage"] == 0.0

    def test_collect_metrics_multiple_sessions(self) -> None:
        """Verify total_cases counts distinct sessions."""
        workspace, db_path = _build_store()
        try:
            store = CognitiveRuntimeSqliteStore(workspace, db_path=db_path)
            for session_idx in range(3):
                for turn_idx in range(2):
                    store.append_receipt(
                        RuntimeReceipt(
                            receipt_id=f"receipt-s{session_idx}-t{turn_idx}",
                            receipt_type="turn",
                            workspace=workspace,
                            created_at=f"2026-03-27T00:{session_idx:02d}:{turn_idx:02d}+00:00",
                            payload={},
                            session_id=f"session-{session_idx}",
                        )
                    )
            time.sleep(0.1)

            collector = CognitiveRuntimeMetricsCollector(
                workspace,
                db_path=db_path,
                kernel_db=store._kernel_db,
            )
            result = collector.collect_metrics(workspace)

            assert result["total_cases"] == 3
            assert result["receipt_coverage"] == 1.0
            store.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestMetricsCollectorEdgeCases:
    """Edge case tests for CognitiveRuntimeMetricsCollector."""

    def test_workspace_override(self) -> None:
        """Verify workspace parameter is properly used."""
        workspace, db_path = _build_store()
        try:
            store = CognitiveRuntimeSqliteStore(workspace, db_path=db_path)
            store.append_receipt(
                RuntimeReceipt(
                    receipt_id="receipt-1",
                    receipt_type="turn",
                    workspace=workspace,
                    created_at="2026-03-27T00:00:00+00:00",
                    payload={},
                    session_id="session-1",
                )
            )
            time.sleep(0.1)

            collector = CognitiveRuntimeMetricsCollector(workspace, db_path=db_path)
            result = collector.collect_metrics(workspace)

            assert result["receipt_coverage"] == 1.0
            store.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_empty_session_id_handling(self) -> None:
        """Verify handling of receipts without session_id."""
        workspace, db_path = _build_store()
        try:
            store = CognitiveRuntimeSqliteStore(workspace, db_path=db_path)
            store.append_receipt(
                RuntimeReceipt(
                    receipt_id="receipt-no-session",
                    receipt_type="turn",
                    workspace=workspace,
                    created_at="2026-03-27T00:00:00+00:00",
                    payload={},
                    session_id=None,
                )
            )
            time.sleep(0.1)

            collector = CognitiveRuntimeMetricsCollector(
                workspace,
                db_path=db_path,
                kernel_db=store._kernel_db,
            )
            result = collector.collect_metrics(workspace)

            assert result["total_cases"] == 0
            store.close()
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)
