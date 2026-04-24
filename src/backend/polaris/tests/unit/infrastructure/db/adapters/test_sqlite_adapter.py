"""Tests for polaris.infrastructure.db.adapters.sqlite_adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from polaris.infrastructure.db.adapters.sqlite_adapter import (
    _PRAGMA_NAME_RE,
    SqliteAdapter,
    _format_pragma_value,
)


class TestFormatPragmaValue:
    def test_bool_true(self) -> None:
        assert _format_pragma_value(True) == "ON"

    def test_bool_false(self) -> None:
        assert _format_pragma_value(False) == "OFF"

    def test_int(self) -> None:
        assert _format_pragma_value(42) == "42"

    def test_float(self) -> None:
        assert _format_pragma_value(3.14) == "3.14"

    def test_simple_string(self) -> None:
        assert _format_pragma_value("journal") == "journal"

    def test_string_with_hyphen(self) -> None:
        assert _format_pragma_value("wal-mode") == "wal-mode"

    def test_string_with_underscore(self) -> None:
        assert _format_pragma_value("my_setting") == "my_setting"

    def test_string_with_dot(self) -> None:
        assert _format_pragma_value("file.version") == "file.version"

    def test_string_with_single_quote_escaped(self) -> None:
        result = _format_pragma_value("it's")
        assert result == "'it''s'"

    def test_string_with_multiple_quotes(self) -> None:
        result = _format_pragma_value("test's value")
        assert result == "'test''s value'"

    def test_empty_string(self) -> None:
        result = _format_pragma_value("")
        assert result == "''"


class TestPragmaNameRegex:
    def test_valid_name(self) -> None:
        assert _PRAGMA_NAME_RE.fullmatch("journal_mode") is not None

    def test_valid_name_with_number(self) -> None:
        assert _PRAGMA_NAME_RE.fullmatch("cache_size") is not None

    def test_valid_name_underscore_start(self) -> None:
        assert _PRAGMA_NAME_RE.fullmatch("_pragma") is not None

    def test_invalid_starts_with_number(self) -> None:
        assert _PRAGMA_NAME_RE.fullmatch("1pragma") is None

    def test_invalid_contains_hyphen(self) -> None:
        assert _PRAGMA_NAME_RE.fullmatch("pragma-mode") is None


class TestApplyRowFactory:
    def test_none_does_nothing(self) -> None:
        """When row_factory is None, the method returns early without setting anything."""
        adapter = SqliteAdapter()
        # Use spec to track attribute access
        import sqlite3

        conn = MagicMock(spec=sqlite3.Connection)
        # Should not raise - returns early when row_factory is None
        adapter._apply_row_factory(conn, None)

    def test_string_row_lowercase(self) -> None:
        import sqlite3

        adapter = SqliteAdapter()
        conn = MagicMock(spec=sqlite3.Connection)
        adapter._apply_row_factory(conn, "row")
        assert conn.row_factory == sqlite3.Row

    def test_string_row_uppercase(self) -> None:
        import sqlite3

        adapter = SqliteAdapter()
        conn = MagicMock(spec=sqlite3.Connection)
        adapter._apply_row_factory(conn, "ROW")
        assert conn.row_factory == sqlite3.Row

    def test_string_row_with_whitespace(self) -> None:
        import sqlite3

        adapter = SqliteAdapter()
        conn = MagicMock(spec=sqlite3.Connection)
        adapter._apply_row_factory(conn, "  row  ")
        assert conn.row_factory == sqlite3.Row

    def test_string_invalid_raises(self) -> None:
        adapter = SqliteAdapter()
        conn = MagicMock()
        with pytest.raises(ValueError, match="unsupported sqlite row_factory string"):
            adapter._apply_row_factory(conn, "invalid")

    def test_callable(self) -> None:
        adapter = SqliteAdapter()
        conn = MagicMock()

        def factory(row: object) -> object:
            return row

        adapter._apply_row_factory(conn, factory)
        assert conn.row_factory == factory

    def test_non_callable_non_string_raises(self) -> None:
        adapter = SqliteAdapter()
        conn = MagicMock()
        with pytest.raises(ValueError, match="unsupported sqlite row_factory type"):
            adapter._apply_row_factory(conn, 123)  # type: ignore[arg-type]


class TestSqliteAdapterConnect:
    def test_connect_with_default_options(self) -> None:
        adapter = SqliteAdapter()
        mock_conn = MagicMock()

        mock_options = MagicMock()
        mock_options.isolation_level = None
        mock_options.timeout_seconds = 5.0
        mock_options.check_same_thread = True
        mock_options.detect_types = 0
        mock_options.uri = False
        mock_options.row_factory = None
        mock_options.pragmas = {}

        with patch("polaris.infrastructure.db.adapters.sqlite_adapter.sqlite3.connect", return_value=mock_conn):
            result = adapter.connect(":memory:", mock_options)

        assert result is mock_conn
        mock_conn.execute.assert_not_called()  # No pragmas

    def test_connect_with_deferred_isolation(self) -> None:
        adapter = SqliteAdapter()
        mock_conn = MagicMock()

        mock_options = MagicMock()
        mock_options.isolation_level = "DEFERRED"
        mock_options.timeout_seconds = 5.0
        mock_options.check_same_thread = True
        mock_options.detect_types = 0
        mock_options.uri = False
        mock_options.row_factory = None
        mock_options.pragmas = {}

        with patch("polaris.infrastructure.db.adapters.sqlite_adapter.sqlite3.connect") as mock_connect:
            mock_connect.return_value = mock_conn
            adapter.connect(":memory:", mock_options)
            assert mock_connect.call_args.kwargs.get("isolation_level") == "DEFERRED"

    def test_connect_with_exclusive_isolation(self) -> None:
        adapter = SqliteAdapter()
        mock_conn = MagicMock()

        mock_options = MagicMock()
        mock_options.isolation_level = "EXCLUSIVE"
        mock_options.timeout_seconds = 5.0
        mock_options.check_same_thread = True
        mock_options.detect_types = 0
        mock_options.uri = False
        mock_options.row_factory = None
        mock_options.pragmas = {}

        with patch("polaris.infrastructure.db.adapters.sqlite_adapter.sqlite3.connect") as mock_connect:
            mock_connect.return_value = mock_conn
            adapter.connect(":memory:", mock_options)
            assert mock_connect.call_args.kwargs.get("isolation_level") == "EXCLUSIVE"

    def test_connect_with_invalid_isolation_falls_back_to_deferred(self) -> None:
        adapter = SqliteAdapter()
        mock_conn = MagicMock()

        mock_options = MagicMock()
        mock_options.isolation_level = "INVALID"
        mock_options.timeout_seconds = 5.0
        mock_options.check_same_thread = True
        mock_options.detect_types = 0
        mock_options.uri = False
        mock_options.row_factory = None
        mock_options.pragmas = {}

        with patch("polaris.infrastructure.db.adapters.sqlite_adapter.sqlite3.connect") as mock_connect:
            mock_connect.return_value = mock_conn
            adapter.connect(":memory:", mock_options)
            assert mock_connect.call_args.kwargs.get("isolation_level") == "DEFERRED"

    def test_connect_with_row_factory(self) -> None:
        adapter = SqliteAdapter()
        mock_conn = MagicMock()

        mock_options = MagicMock()
        mock_options.isolation_level = None
        mock_options.timeout_seconds = 5.0
        mock_options.check_same_thread = True
        mock_options.detect_types = 0
        mock_options.uri = False
        mock_options.row_factory = "row"
        mock_options.pragmas = {}

        with patch("polaris.infrastructure.db.adapters.sqlite_adapter.sqlite3.connect", return_value=mock_conn):
            adapter.connect(":memory:", mock_options)

        import sqlite3

        assert mock_conn.row_factory == sqlite3.Row

    def test_connect_with_pragmas(self) -> None:
        adapter = SqliteAdapter()
        mock_conn = MagicMock()

        mock_options = MagicMock()
        mock_options.isolation_level = None
        mock_options.timeout_seconds = 5.0
        mock_options.check_same_thread = True
        mock_options.detect_types = 0
        mock_options.uri = False
        mock_options.row_factory = None
        mock_options.pragmas = {"journal_mode": "WAL", "synchronous": 1}

        with patch("polaris.infrastructure.db.adapters.sqlite_adapter.sqlite3.connect", return_value=mock_conn):
            adapter.connect(":memory:", mock_options)

        # Verify pragmas were executed
        assert mock_conn.execute.call_count == 2
        calls = mock_conn.execute.call_args_list
        assert calls[0][0][0] == "PRAGMA journal_mode=WAL"
        assert calls[1][0][0] == "PRAGMA synchronous=1"

    def test_connect_with_invalid_pragma_name(self) -> None:
        adapter = SqliteAdapter()
        mock_conn = MagicMock()

        mock_options = MagicMock()
        mock_options.isolation_level = None
        mock_options.timeout_seconds = 5.0
        mock_options.check_same_thread = True
        mock_options.detect_types = 0
        mock_options.uri = False
        mock_options.row_factory = None
        mock_options.pragmas = {"invalid-name": "value"}

        with (
            patch("polaris.infrastructure.db.adapters.sqlite_adapter.sqlite3.connect", return_value=mock_conn),
            pytest.raises(ValueError, match="invalid sqlite pragma name"),
        ):
            adapter.connect(":memory:", mock_options)


class TestSqliteAdapterApplyPragmas:
    def test_valid_pragma_journal_mode(self) -> None:
        adapter = SqliteAdapter()
        mock_conn = MagicMock()

        adapter._apply_pragmas(mock_conn, {"journal_mode": "WAL"})
        mock_conn.execute.assert_called_once_with("PRAGMA journal_mode=WAL")

    def test_valid_pragma_with_bool_value(self) -> None:
        adapter = SqliteAdapter()
        mock_conn = MagicMock()

        adapter._apply_pragmas(mock_conn, {"foreign_keys": True})
        mock_conn.execute.assert_called_once_with("PRAGMA foreign_keys=ON")

    def test_valid_pragma_with_int_value(self) -> None:
        adapter = SqliteAdapter()
        mock_conn = MagicMock()

        adapter._apply_pragmas(mock_conn, {"cache_size": 2000})
        mock_conn.execute.assert_called_once_with("PRAGMA cache_size=2000")

    def test_multiple_pragmas(self) -> None:
        adapter = SqliteAdapter()
        mock_conn = MagicMock()

        adapter._apply_pragmas(mock_conn, {"journal_mode": "DELETE", "synchronous": 2})
        assert mock_conn.execute.call_count == 2

    def test_empty_pragmas(self) -> None:
        adapter = SqliteAdapter()
        mock_conn = MagicMock()

        adapter._apply_pragmas(mock_conn, {})
        mock_conn.execute.assert_not_called()
