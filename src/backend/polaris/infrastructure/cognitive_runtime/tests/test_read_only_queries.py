from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest
from polaris.application.cognitive_runtime.service import CognitiveRuntimeService
from polaris.domain.cognitive_runtime import ContextHandoffPack, RuntimeReceipt
from polaris.infrastructure.cognitive_runtime import CognitiveRuntimeSqliteStore
from polaris.infrastructure.cognitive_runtime.metrics_collector import CognitiveRuntimeMetricsCollector


def test_missing_queries_do_not_initialize_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "absent"
    writers = sum(t.name == "cognitive-runtime-sqlite-writer" for t in threading.enumerate())
    service = CognitiveRuntimeService()
    try:
        assert service.get_runtime_receipt(workspace=str(workspace), receipt_id="none") is None
        assert service.get_handoff_pack(workspace=str(workspace), handoff_id="none") is None
        assert not workspace.exists()
        assert sum(t.name == "cognitive-runtime-sqlite-writer" for t in threading.enumerate()) == writers
    finally:
        service.close()


def test_existing_query_does_not_initialize_schema_or_writer(tmp_path: Path, monkeypatch) -> None:
    writer = CognitiveRuntimeSqliteStore(str(tmp_path))
    writer.save_handoff_pack(
        ContextHandoffPack(
            handoff_id="handoff-1",
            workspace=str(tmp_path),
            created_at="2026-10-01T00:00:00+00:00",
            session_id="s1",
            current_goal="committed WAL goal",
        )
    )
    try:

        def forbidden_schema(_self):
            raise AssertionError("query initialized schema")

        monkeypatch.setattr(CognitiveRuntimeSqliteStore, "_init_schema", forbidden_schema)
        reader = CognitiveRuntimeService()
        try:
            handoff = reader.get_handoff_pack(workspace=str(tmp_path), handoff_id="handoff-1")
            assert handoff is not None and handoff.current_goal == "committed WAL goal"
        finally:
            reader.close()
    finally:
        writer.close()


def test_injected_store_is_bound_before_first_query(tmp_path: Path) -> None:
    writer = CognitiveRuntimeSqliteStore(str(tmp_path / "one"))
    service = CognitiveRuntimeService(store=writer)
    try:
        with pytest.raises(ValueError, match="different workspace"):
            service.get_handoff_pack(workspace=str(tmp_path / "other"), handoff_id="none")
    finally:
        service.close()


def test_readonly_store_rejects_write_and_reads_committed_receipt(tmp_path: Path) -> None:
    writer = CognitiveRuntimeSqliteStore(str(tmp_path))
    receipt = RuntimeReceipt(
        receipt_id="receipt-1",
        receipt_type="test",
        workspace=str(tmp_path),
        created_at="2026-10-01T00:00:00+00:00",
        payload={"ok": True},
    )
    writer.append_receipt(receipt)
    try:
        reader = CognitiveRuntimeSqliteStore(str(tmp_path), read_only=True)
        try:
            assert reader.get_receipt("receipt-1") == receipt
            with pytest.raises(RuntimeError, match="read-only"):
                reader.append_receipt(receipt)
        finally:
            reader.close()
    finally:
        writer.close()


def test_metrics_on_missing_namespace_do_not_initialize_storage(tmp_path: Path) -> None:
    workspace = tmp_path / "absent-metrics"
    result = CognitiveRuntimeMetricsCollector(str(workspace)).collect_metrics(str(workspace))
    assert result["total_cases"] == 0
    assert not workspace.exists()


def test_metrics_reader_does_not_close_injected_writer_database(tmp_path: Path) -> None:
    writer = CognitiveRuntimeSqliteStore(str(tmp_path))
    receipt = RuntimeReceipt(receipt_id="before", receipt_type="test", workspace=str(tmp_path),
                             created_at="2026-10-01T00:00:00+00:00", payload={})
    writer.append_receipt(receipt)
    try:
        collector = CognitiveRuntimeMetricsCollector(str(tmp_path), db_path=writer._db_path,
                                                     kernel_db=writer._kernel_db)
        collector.collect_metrics(str(tmp_path))
        assert writer.get_receipt("before") == receipt
        after = RuntimeReceipt(receipt_id="after", receipt_type="test", workspace=str(tmp_path),
                               created_at="2026-10-01T00:00:01+00:00", payload={})
        writer.append_receipt(after)
        assert writer.get_receipt("after") == after
    finally:
        writer.close()


def test_sqlite_writer_propagates_failed_statement_without_false_ack(tmp_path: Path) -> None:
    writer = CognitiveRuntimeSqliteStore(str(tmp_path))
    try:
        with pytest.raises(sqlite3.OperationalError):
            writer._submit_write(lambda connection: connection.execute("INSERT INTO nonexistent_table VALUES (1)"))
        receipt = RuntimeReceipt(receipt_id="after-error", receipt_type="test", workspace=str(tmp_path),
                                 created_at="2026-10-01T00:00:00+00:00", payload={})
        writer.append_receipt(receipt)
        assert writer.get_receipt("after-error") == receipt
    finally:
        writer.close()
