"""Tests for polaris.infrastructure.db.adapters.sqlalchemy_adapter module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from polaris.infrastructure.db.adapters.sqlalchemy_adapter import SqlAlchemyAdapter
from polaris.kernelone.db.errors import DatabaseDriverNotAvailableError


class TestSqlAlchemyAdapterCreateEngine:
    def test_create_engine_basic(self):
        adapter = SqlAlchemyAdapter()
        mock_options = MagicMock()
        mock_options.connect_args = {}
        mock_options.pool_pre_ping = True
        mock_options.echo = False
        mock_options.pool_class = None

        mock_engine = MagicMock()
        with patch("sqlalchemy.create_engine", return_value=mock_engine) as mock_create:
            result = adapter.create_engine("sqlite:///:memory:", mock_options)

        mock_create.assert_called_once_with(
            "sqlite:///:memory:",
            connect_args={},
            pool_pre_ping=True,
            echo=False,
        )
        assert result is mock_engine

    def test_create_engine_with_pool_class(self):
        adapter = SqlAlchemyAdapter()
        mock_options = MagicMock()
        mock_options.connect_args = {"timeout": 30}
        mock_options.pool_pre_ping = False
        mock_options.echo = True
        mock_pool_class = MagicMock()
        mock_options.pool_class = mock_pool_class

        mock_engine = MagicMock()
        with patch("sqlalchemy.create_engine", return_value=mock_engine) as mock_create:
            adapter.create_engine("postgresql://user:pass@localhost/db", mock_options)

        mock_create.assert_called_once_with(
            "postgresql://user:pass@localhost/db",
            connect_args={"timeout": 30},
            pool_pre_ping=False,
            echo=True,
            poolclass=mock_pool_class,
        )

    def test_create_engine_import_error_raises(self):
        adapter = SqlAlchemyAdapter()
        mock_options = MagicMock()
        mock_options.connect_args = {}
        mock_options.pool_pre_ping = True
        mock_options.echo = False
        mock_options.pool_class = None

        with patch("sqlalchemy.create_engine", side_effect=ImportError("No module named 'sqlalchemy'")):
            with pytest.raises(DatabaseDriverNotAvailableError, match="sqlalchemy is not installed"):
                adapter.create_engine("sqlite:///:memory:", mock_options)

    def test_create_engine_connect_args_copied(self):
        adapter = SqlAlchemyAdapter()
        original_args = {"timeout": 30, "check_same_thread": False}
        mock_options = MagicMock()
        mock_options.connect_args = original_args
        mock_options.pool_pre_ping = True
        mock_options.echo = False
        mock_options.pool_class = None

        with patch("sqlalchemy.create_engine") as mock_create:
            adapter.create_engine("sqlite:///:memory:", mock_options)

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["connect_args"] == original_args
        assert call_kwargs["connect_args"] is not original_args  # Should be a copy


class TestSqlAlchemyAdapterDisposeEngine:
    def test_dispose_engine_with_dispose_method(self):
        adapter = SqlAlchemyAdapter()
        mock_engine = MagicMock()
        adapter.dispose_engine(mock_engine)
        mock_engine.dispose.assert_called_once()

    def test_dispose_engine_without_dispose_method(self):
        adapter = SqlAlchemyAdapter()
        mock_engine = MagicMock()
        del mock_engine.dispose
        # Should not raise
        adapter.dispose_engine(mock_engine)

    def test_dispose_engine_dispose_not_callable(self):
        adapter = SqlAlchemyAdapter()
        mock_engine = MagicMock()
        mock_engine.dispose = "not_callable"
        # Should not raise
        adapter.dispose_engine(mock_engine)

    def test_dispose_engine_none_input(self):
        adapter = SqlAlchemyAdapter()
        # Should not raise
        adapter.dispose_engine(None)
