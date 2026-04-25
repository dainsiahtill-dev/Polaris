"""Tests for polaris.kernelone.db.contracts."""

from __future__ import annotations

import pytest
from polaris.kernelone.db.contracts import (
    SQLAlchemyConnectOptions,
    SQLiteConnectOptions,
)


class TestSQLiteConnectOptions:
    def test_defaults(self) -> None:
        opts = SQLiteConnectOptions()
        assert opts.timeout_seconds == 30.0
        assert opts.check_same_thread is False
        assert opts.isolation_level is None
        assert opts.detect_types == 0
        assert opts.uri is False
        assert opts.row_factory == "row"
        assert opts.pragmas == {}

    def test_custom_values(self) -> None:
        opts = SQLiteConnectOptions(
            timeout_seconds=60.0,
            check_same_thread=True,
            isolation_level="IMMEDIATE",
            detect_types=1,
            uri=True,
            row_factory=None,
            pragmas={"journal_mode": "wal"},
        )
        assert opts.timeout_seconds == 60.0
        assert opts.check_same_thread is True
        assert opts.isolation_level == "IMMEDIATE"
        assert opts.detect_types == 1
        assert opts.uri is True
        assert opts.row_factory is None
        assert opts.pragmas == {"journal_mode": "wal"}

    def test_frozen_immutable(self) -> None:
        opts = SQLiteConnectOptions()
        with pytest.raises(AttributeError):
            opts.timeout_seconds = 10.0

    def test_hashable(self) -> None:
        opts = SQLiteConnectOptions()
        assert hash(opts) == hash(opts)

    def test_equality(self) -> None:
        a = SQLiteConnectOptions()
        b = SQLiteConnectOptions()
        assert a == b

    def test_inequality(self) -> None:
        a = SQLiteConnectOptions(timeout_seconds=30.0)
        b = SQLiteConnectOptions(timeout_seconds=60.0)
        assert a != b


class TestSQLAlchemyConnectOptions:
    def test_defaults(self) -> None:
        opts = SQLAlchemyConnectOptions()
        assert opts.connect_args == {}
        assert opts.pool_class is None
        assert opts.pool_pre_ping is True
        assert opts.echo is False

    def test_custom_values(self) -> None:
        opts = SQLAlchemyConnectOptions(
            connect_args={"timeout": 30},
            pool_class="QueuePool",
            pool_pre_ping=False,
            echo=True,
        )
        assert opts.connect_args == {"timeout": 30}
        assert opts.pool_class == "QueuePool"
        assert opts.pool_pre_ping is False
        assert opts.echo is True

    def test_frozen_immutable(self) -> None:
        opts = SQLAlchemyConnectOptions()
        with pytest.raises(AttributeError):
            opts.echo = True

    def test_equality(self) -> None:
        a = SQLAlchemyConnectOptions()
        b = SQLAlchemyConnectOptions()
        assert a == b
