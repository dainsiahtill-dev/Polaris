"""Tests for polaris.infrastructure.db.adapters.lancedb_adapter module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from polaris.infrastructure.db.adapters.lancedb_adapter import LanceDbAdapter
from polaris.kernelone.db.errors import DatabaseDriverNotAvailableError


class TestLanceDbAdapterConnect:
    def test_connect_success(self):
        adapter = LanceDbAdapter()
        mock_db = MagicMock()
        with patch("lancedb.connect", return_value=mock_db) as mock_connect:
            result = adapter.connect("/tmp/test.lance")

        mock_connect.assert_called_once_with("/tmp/test.lance")
        assert result is mock_db

    def test_connect_import_error_raises(self):
        adapter = LanceDbAdapter()
        with patch("lancedb.connect", side_effect=ImportError("No module named 'lancedb'")):
            with pytest.raises(DatabaseDriverNotAvailableError, match="lancedb is not installed"):
                adapter.connect("/tmp/test.lance")

    def test_connect_returns_connection_object(self):
        adapter = LanceDbAdapter()
        mock_db = MagicMock()
        mock_db.tables = ["table1", "table2"]
        with patch("lancedb.connect", return_value=mock_db):
            result = adapter.connect("/tmp/test.lance")

        assert hasattr(result, "tables")
        assert result.tables == ["table1", "table2"]

    def test_connect_uri_passed_through(self):
        adapter = LanceDbAdapter()
        with patch("lancedb.connect") as mock_connect:
            adapter.connect("s3://bucket/path")

        mock_connect.assert_called_once_with("s3://bucket/path")

    def test_connect_empty_uri(self):
        adapter = LanceDbAdapter()
        mock_db = MagicMock()
        with patch("lancedb.connect", return_value=mock_db) as mock_connect:
            result = adapter.connect("")

        mock_connect.assert_called_once_with("")
        assert result is mock_db
