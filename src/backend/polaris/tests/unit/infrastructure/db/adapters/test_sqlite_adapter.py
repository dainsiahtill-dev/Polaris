"""Tests for polaris.infrastructure.db.adapters.sqlite_adapter module."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from polaris.infrastructure.db.adapters.sqlite_adapter import SqliteAdapter, _format_pragma_value


class TestFormatPragmaValue:
    def test_bool_true(self):
        assert _format_pragma_value(True) == "ON"

    def test_bool_false(self):
        assert _format_pragma_value(False) == "OFF"

    def test_int(self):
        assert _format_pragma_value(42) == "42"

    def test_float(self):
        assert _format_pragma_value(3.14) == "3.14"

    def test_string_alphanumeric(self):
        assert _format_pragma_value("fast") == "fast"

    def test_string_with_dot(self):
        assert _format_pragma_value("val.ue") == "val.ue"

    def test_string_with_hyphen(self):
        assert _format_pragma_value("val-ue") == "val-ue"

    def test_string_with_special_chars(self):
        assert _format_pragma_value("val'ue") == "'val''ue'"


class TestSqliteAdapterConnect:
    def test_connect_default_isolation(self):
        adapter = SqliteAdapter()
        mock_options = MagicMock()
        mock_options.isolation_level = None
        mock_options.timeout_seconds = 5.0
        mock_options.check_same_thread = True
        mock_options.detect_types = 0
        mock_options.uri = False
        mock_options.row_factory = None
        mock_options.pragmas = {}

        mock_conn = MagicMock()
        with patch("sqlite3.connect", return_value=mock_conn) as mock_connect:
            result = adapter.connect(":memory:", mock_options)

        mock_connect.assert_called_once_with(
            ":memory:",
            timeout=5.0,
            check_same_thread=True,
            isolation_level=None,
            detect_types=0,
            uri=False,
        )
        assert result is mock_conn

    def test_connect_deferred_isolation(self):
        adapter = SqliteAdapter()
        mock_options = MagicMock()
        mock_options.isolation_level = "DEFERRED"
        mock_options.timeout_seconds = 5.0
        mock_options.check_same_thread = True
        mock_options.detect_types = 0
        mock_options.uri = False
        mock_options.row_factory = None
        mock_options.pragmas = {}

        mock_conn = MagicMock()
        with patch("sqlite3.connect", return_value=mock_conn):
            adapter.connect(":memory:", mock_options)

        # isolation_level should be passed through for valid values
        call_kwargs = sqlite3.connect.call_args[1]
        assert call_kwargs["isolation_level"] == "DEFERRED"

    def test_connect_invalid_isolation_fallback(self):
        adapter = SqliteAdapter()
        mock_options = MagicMock()
        mock_options.isolation_level = "INVALID"
        mock_options.timeout_seconds = 5.0
        mock_options.check_same_thread = True
        mock_options.detect_types = 0
        mock_options.uri = False
        mock_options.row_factory = None
        mock_options.pragmas = {}

        mock_conn = MagicMock()
        with patch("sqlite3.connect", return_value=mock_conn):
            adapter.connect(":memory:", mock_options)

        call_kwargs = sqlite3.connect.call_args[1]
        assert call_kwargs["isolation_level"] == "DEFERRED"

    def test_connect_applies_row_factory_string(self):
        adapter = SqliteAdapter()
        mock_options = MagicMock()
        mock_options.isolation_level = None
        mock_options.timeout_seconds = 5.0
        mock_options.check_same_thread = True
        mock_options.detect_types = 0
        mock_options.uri = False
        mock_options.row_factory = "row"
        mock_options.pragmas = {}

        mock_conn = MagicMock()
        with patch("sqlite3.connect", return_value=mock_conn):
            adapter.connect(":memory:", mock_options)

        assert mock_conn.row_factory == sqlite3.Row

    def test_connect_applies_row_factory_callable(self):
        adapter = SqliteAdapter()
        mock_options = MagicMock()
        mock_options.isolation_level = None
        mock_options.timeout_seconds = 5.0
        mock_options.check_same_thread = True
        mock_options.detect_types = 0
        mock_options.uri = False
        mock_options.row_factory = lambda cursor, row: row
        mock_options.pragmas = {}

        mock_conn = MagicMock()
        with patch("sqlite3.connect", return_value=mock_conn):
            adapter.connect(":memory:", mock_options)

        assert mock_conn.row_factory is not None
        assert callable(mock_conn.row_factory)

    def test_connect_row_factory_none_no_op(self):
        adapter = SqliteAdapter()
        mock_options = MagicMock()
        mock_options.isolation_level = None
        mock_options.timeout_seconds = 5.0
        mock_options.check_same_thread = True
        mock_options.detect_types = 0
        mock_options.uri = False
        mock_options.row_factory = None
        mock_options.pragmas = {}

        mock_conn = MagicMock()
        with patch("sqlite3.connect", return_value=mock_conn):
            adapter.connect(":memory:", mock_options)

        assert not mock_conn.__setattr__.called or mock_conn.row_factory is None

    def test_connect_row_factory_invalid_string_raises(self):
        adapter = SqliteAdapter()
        mock_options = MagicMock()
        mock_options.isolation_level = None
        mock_options.timeout_seconds = 5.0
        mock_options.check_same_thread = True
        mock_options.detect_types = 0
        mock_options.uri = False
        mock_options.row_factory = "invalid"
        mock_options.pragmas = {}

        mock_conn = MagicMock()
        with patch("sqlite3.connect", return_value=mock_conn):
            with pytest.raises(ValueError, match="unsupported sqlite row_factory string"):
                adapter.connect(":memory:", mock_options)

    def test_connect_row_factory_invalid_type_raises(self):
        adapter = SqliteAdapter()
        mock_options = MagicMock()
        mock_options.isolation_level = None
        mock_options.timeout_seconds = 5.0
        mock_options.check_same_thread = True
        mock_options.detect_types = 0
        mock_options.uri = False
        mock_options.row_factory = 123
        mock_options.pragmas = {}

        mock_conn = MagicMock()
        with patch("sqlite3.connect", return_value=mock_conn):
            with pytest.raises(ValueError, match="unsupported sqlite row_factory type"):
                adapter.connect(":memory:", mock_options)

    def test_connect_applies_pragmas(self):
        adapter = SqliteAdapter()
        mock_options = MagicMock()
        mock_options.isolation_level = None
        mock_options.timeout_seconds = 5.0
        mock_options.check_same_thread = True
        mock_options.detect_types = 0
        mock_options.uri = False
        mock_options.row_factory = None
        mock_options.pragmas = {"journal_mode": "WAL", "cache_size": -2000}

        mock_conn = MagicMock()
        with patch("sqlite3.connect", return_value=mock_conn):
            adapter.connect(":memory:", mock_options)

        assert mock_conn.execute.call_count == 2
        mock_conn.execute.assert_any_call("PRAGMA journal_mode=WAL")
        mock_conn.execute.assert_any_call("PRAGMA cache_size=-2000")

    def test_connect_invalid_pragma_name_raises(self):
        adapter = SqliteAdapter()
        mock_options = MagicMock()
        mock_options.isolation_level = None
        mock_options.timeout_seconds = 5.0
        mock_options.check_same_thread = True
        mock_options.detect_types = 0
        mock_options.uri = False
        mock_options.row_factory = None
        mock_options.pragmas = {"123invalid": "value"}

        mock_conn = MagicMock()
        with patch("sqlite3.connect", return_value=mock_conn):
            with pytest.raises(ValueError, match="invalid sqlite pragma name"):
                adapter.connect(":memory:", mock_options)

    def test_connect_empty_pragmas_no_execute(self):
        adapter = SqliteAdapter()
        mock_options = MagicMock()
        mock_options.isolation_level = None
        mock_options.timeout_seconds = 5.0
        mock_options.check_same_thread = True
        mock_options.detect_types = 0
        mock_options.uri = False
        mock_options.row_factory = None
        mock_options.pragmas = {}

        mock_conn = MagicMock()
        with patch("sqlite3.connect", return_value=mock_conn):
            adapter.connect(":memory:", mock_options)

        # Only no pragma executes
        mock_conn.execute.assert_not_called()
