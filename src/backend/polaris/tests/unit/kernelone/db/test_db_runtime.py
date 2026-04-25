"""Tests for polaris.kernelone.db.runtime."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from polaris.kernelone.db.contracts import SQLAlchemyConnectOptions, SQLiteConnectOptions
from polaris.kernelone.db.errors import DatabaseConnectionError, DatabaseDriverNotAvailableError
from polaris.kernelone.db.runtime import KernelDatabase, KernelDatabaseHealth


class TestKernelDatabaseHealth:
    def test_dataclass_fields(self) -> None:
        health = KernelDatabaseHealth(
            sqlite_adapter_ready=True,
            sqlalchemy_adapter_ready=False,
            lancedb_adapter_ready=True,
            workspace="/tmp/ws",
        )
        assert health.sqlite_adapter_ready is True
        assert health.sqlalchemy_adapter_ready is False
        assert health.lancedb_adapter_ready is True
        assert health.workspace == "/tmp/ws"


class TestKernelDatabaseInit:
    def test_default_workspace(self) -> None:
        db = KernelDatabase("/tmp/ws")
        assert db.workspace == "/tmp/ws"

    def test_empty_workspace_defaults_to_cwd(self) -> None:
        import os

        db = KernelDatabase("")
        assert db.workspace == os.path.abspath(os.getcwd())

    def test_adapters_optional(self) -> None:
        db = KernelDatabase("/tmp/ws")
        assert db._sqlite_adapter is None
        assert db._sqlalchemy_adapter is None
        assert db._lancedb_adapter is None

    def test_adapters_injected(self) -> None:
        sqlite = MagicMock()
        sqlalchemy = MagicMock()
        lancedb = MagicMock()
        db = KernelDatabase(
            "/tmp/ws",
            sqlite_adapter=sqlite,
            sqlalchemy_adapter=sqlalchemy,
            lancedb_adapter=lancedb,
        )
        assert db._sqlite_adapter is sqlite
        assert db._sqlalchemy_adapter is sqlalchemy
        assert db._lancedb_adapter is lancedb


class TestKernelDatabaseResolvePaths:
    def test_resolve_sqlite_path(self, tmp_path) -> None:
        db = KernelDatabase(str(tmp_path), allow_unmanaged_absolute=True)
        result = db.resolve_sqlite_path("test.db", ensure_parent=False)
        assert "test.db" in result

    def test_resolve_lancedb_path(self, tmp_path) -> None:
        db = KernelDatabase(str(tmp_path), allow_unmanaged_absolute=True)
        result = db.resolve_lancedb_path("mydb", ensure_exists=False)
        assert "mydb" in result


class TestKernelDatabaseSqlite:
    def test_raises_when_no_adapter(self, tmp_path) -> None:
        db = KernelDatabase(str(tmp_path))
        with pytest.raises(DatabaseDriverNotAvailableError, match="sqlite adapter is not configured"):
            db.sqlite("test.db")

    def test_connects_with_adapter(self, tmp_path) -> None:
        mock_conn = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.connect = MagicMock(return_value=mock_conn)
        db = KernelDatabase(str(tmp_path), sqlite_adapter=mock_adapter, allow_unmanaged_absolute=True)
        result = db.sqlite("test.db", ensure_parent=False)
        assert result is mock_conn
        mock_adapter.connect.assert_called_once()
        _, options = mock_adapter.connect.call_args[0]
        assert isinstance(options, SQLiteConnectOptions)

    def test_wraps_adapter_error(self, tmp_path) -> None:
        mock_adapter = MagicMock()
        mock_adapter.connect = MagicMock(side_effect=RuntimeError("boom"))
        db = KernelDatabase(str(tmp_path), sqlite_adapter=mock_adapter, allow_unmanaged_absolute=True)
        with pytest.raises(DatabaseConnectionError, match="failed to connect sqlite"):
            db.sqlite("test.db", ensure_parent=False)

    def test_tracks_connections(self, tmp_path) -> None:
        mock_conn = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.connect = MagicMock(return_value=mock_conn)
        db = KernelDatabase(str(tmp_path), sqlite_adapter=mock_adapter, allow_unmanaged_absolute=True)
        db.sqlite("test.db", ensure_parent=False)
        assert len(db._sqlite_connections) == 1
        assert db._sqlite_connections[0] is mock_conn

    def test_options_passed_correctly(self, tmp_path) -> None:
        mock_adapter = MagicMock()
        db = KernelDatabase(str(tmp_path), sqlite_adapter=mock_adapter, allow_unmanaged_absolute=True)
        db.sqlite(
            "test.db",
            timeout_seconds=60.0,
            check_same_thread=True,
            isolation_level="IMMEDIATE",
            detect_types=1,
            uri=True,
            row_factory=None,
            pragmas={"journal_mode": "wal"},
            ensure_parent=False,
        )
        _, options = mock_adapter.connect.call_args[0]
        assert options.timeout_seconds == 60.0
        assert options.check_same_thread is True
        assert options.isolation_level == "IMMEDIATE"
        assert options.detect_types == 1
        assert options.uri is True
        assert options.row_factory is None
        assert options.pragmas == {"journal_mode": "wal"}


class TestKernelDatabaseSqlalchemy:
    def test_raises_when_no_adapter(self, tmp_path) -> None:
        db = KernelDatabase(str(tmp_path))
        with pytest.raises(DatabaseDriverNotAvailableError, match="sqlalchemy adapter is not configured"):
            db.sqlalchemy("sqlite:///test.db")

    def test_empty_url_raises(self, tmp_path) -> None:
        mock_adapter = MagicMock()
        db = KernelDatabase(str(tmp_path), sqlalchemy_adapter=mock_adapter)
        with pytest.raises(ValueError, match="database_url is required"):
            db.sqlalchemy("")

    def test_creates_engine(self, tmp_path) -> None:
        mock_engine = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.create_engine = MagicMock(return_value=mock_engine)
        db = KernelDatabase(str(tmp_path), sqlalchemy_adapter=mock_adapter)
        result = db.sqlalchemy("sqlite:///test.db")
        assert result is mock_engine
        mock_adapter.create_engine.assert_called_once()
        _, options = mock_adapter.create_engine.call_args[0]
        assert isinstance(options, SQLAlchemyConnectOptions)

    def test_tracks_engines(self, tmp_path) -> None:
        mock_engine = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.create_engine = MagicMock(return_value=mock_engine)
        db = KernelDatabase(str(tmp_path), sqlalchemy_adapter=mock_adapter)
        db.sqlalchemy("sqlite:///test.db")
        assert len(db._engines) == 1
        assert db._engines[0] is mock_engine

    def test_wraps_adapter_error(self, tmp_path) -> None:
        mock_adapter = MagicMock()
        mock_adapter.create_engine = MagicMock(side_effect=ValueError("bad url"))
        db = KernelDatabase(str(tmp_path), sqlalchemy_adapter=mock_adapter)
        with pytest.raises(DatabaseConnectionError, match="failed to create sqlalchemy engine"):
            db.sqlalchemy("bad://url")


class TestKernelDatabaseLancedb:
    def test_raises_when_no_adapter(self, tmp_path) -> None:
        db = KernelDatabase(str(tmp_path))
        with pytest.raises(DatabaseDriverNotAvailableError, match="lancedb adapter is not configured"):
            db.lancedb("/tmp/db")

    def test_connects(self, tmp_path) -> None:
        mock_client = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.connect = MagicMock(return_value=mock_client)
        db = KernelDatabase(str(tmp_path), lancedb_adapter=mock_adapter, allow_unmanaged_absolute=True)
        result = db.lancedb("mydb", ensure_exists=False)
        assert result is mock_client

    def test_wraps_adapter_error(self, tmp_path) -> None:
        mock_adapter = MagicMock()
        mock_adapter.connect = MagicMock(side_effect=RuntimeError("boom"))
        db = KernelDatabase(str(tmp_path), lancedb_adapter=mock_adapter, allow_unmanaged_absolute=True)
        with pytest.raises(DatabaseConnectionError, match="failed to connect lancedb"):
            db.lancedb("mydb", ensure_exists=False)


class TestKernelDatabaseClose:
    def test_closes_sqlite_connections(self, tmp_path) -> None:
        mock_conn = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.connect = MagicMock(return_value=mock_conn)
        db = KernelDatabase(str(tmp_path), sqlite_adapter=mock_adapter, allow_unmanaged_absolute=True)
        db.sqlite("test.db", ensure_parent=False)
        db.close()
        mock_conn.close.assert_called_once()
        assert len(db._sqlite_connections) == 0

    def test_closes_all_engines(self, tmp_path) -> None:
        mock_engine = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.create_engine = MagicMock(return_value=mock_engine)
        db = KernelDatabase(str(tmp_path), sqlalchemy_adapter=mock_adapter)
        db.sqlalchemy("sqlite:///test.db")
        db.close()
        mock_adapter.dispose_engine.assert_called_once_with(mock_engine)
        assert len(db._engines) == 0

    def test_close_specific_engine(self, tmp_path) -> None:
        mock_engine1 = MagicMock()
        mock_engine2 = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.create_engine = MagicMock(side_effect=[mock_engine1, mock_engine2])
        db = KernelDatabase(str(tmp_path), sqlalchemy_adapter=mock_adapter)
        db.sqlalchemy("sqlite:///test1.db")
        db.sqlalchemy("sqlite:///test2.db")
        db.close(engine=mock_engine1)
        mock_adapter.dispose_engine.assert_called_once_with(mock_engine1)
        assert len(db._engines) == 1
        assert db._engines[0] is mock_engine2

    def test_noop_when_no_sqlalchemy_adapter(self, tmp_path) -> None:
        db = KernelDatabase(str(tmp_path))
        db.close()  # Should not raise

    def test_logs_warning_on_close_error(self, tmp_path) -> None:
        mock_conn = MagicMock()
        mock_conn.close = MagicMock(side_effect=RuntimeError("close failed"))
        mock_adapter = MagicMock()
        mock_adapter.connect = MagicMock(return_value=mock_conn)
        db = KernelDatabase(str(tmp_path), sqlite_adapter=mock_adapter, allow_unmanaged_absolute=True)
        db.sqlite("test.db", ensure_parent=False)
        db.close()  # Should not raise despite close error


class TestKernelDatabaseHealthMethod:
    def test_all_adapters_none(self, tmp_path) -> None:
        db = KernelDatabase(str(tmp_path))
        health = db.health()
        assert health.sqlite_adapter_ready is False
        assert health.sqlalchemy_adapter_ready is False
        assert health.lancedb_adapter_ready is False
        assert health.workspace == str(tmp_path)

    def test_all_adapters_ready(self, tmp_path) -> None:
        db = KernelDatabase(
            str(tmp_path),
            sqlite_adapter=MagicMock(),
            sqlalchemy_adapter=MagicMock(),
            lancedb_adapter=MagicMock(),
        )
        health = db.health()
        assert health.sqlite_adapter_ready is True
        assert health.sqlalchemy_adapter_ready is True
        assert health.lancedb_adapter_ready is True
