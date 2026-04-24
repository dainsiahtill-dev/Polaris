"""Tests for polaris.infrastructure.db.adapters.sqlalchemy_adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from polaris.infrastructure.db.adapters.sqlalchemy_adapter import SqlAlchemyAdapter


class TestSqlAlchemyAdapter:
    """Test SqlAlchemyAdapter."""

    def test_init(self) -> None:
        adapter = SqlAlchemyAdapter()
        assert adapter is not None

    def test_create_engine_success(self) -> None:
        adapter = SqlAlchemyAdapter()
        mock_options = MagicMock()
        mock_options.connect_args = {}
        mock_options.pool_pre_ping = True
        mock_options.echo = False
        mock_options.pool_class = None

        mock_engine = MagicMock()

        with patch("sqlalchemy.create_engine") as mock_create:
            mock_create.return_value = mock_engine
            adapter.create_engine("sqlite:///:memory:", mock_options)
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["connect_args"] == {}
            assert call_kwargs["pool_pre_ping"] is True
            assert call_kwargs["echo"] is False

    def test_create_engine_with_pool_class(self) -> None:
        adapter = SqlAlchemyAdapter()
        mock_options = MagicMock()
        mock_options.connect_args = {}
        mock_options.pool_pre_ping = False
        mock_options.echo = False
        mock_pool_class = MagicMock()
        mock_options.pool_class = mock_pool_class

        mock_engine = MagicMock()

        with patch("sqlalchemy.create_engine") as mock_create:
            mock_create.return_value = mock_engine
            adapter.create_engine("sqlite:///:memory:", mock_options)
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["poolclass"] == mock_pool_class

    def test_create_engine_missing_dependency(self) -> None:
        adapter = SqlAlchemyAdapter()
        mock_options = MagicMock()
        mock_options.connect_args = {}
        mock_options.pool_pre_ping = False
        mock_options.echo = False
        mock_options.pool_class = None

        with (
            patch("sqlalchemy.create_engine", side_effect=ImportError("sqlalchemy is not installed")),
            pytest.raises(ImportError, match="sqlalchemy is not installed"),
        ):
            adapter.create_engine("sqlite:///:memory:", mock_options)

    def test_dispose_engine_with_dispose(self) -> None:
        adapter = SqlAlchemyAdapter()
        mock_engine = MagicMock()
        mock_engine.dispose = MagicMock()

        adapter.dispose_engine(mock_engine)
        mock_engine.dispose.assert_called_once()

    def test_dispose_engine_without_dispose(self) -> None:
        adapter = SqlAlchemyAdapter()
        mock_engine = MagicMock(spec=[])  # No dispose method

        # Should not raise
        adapter.dispose_engine(mock_engine)
