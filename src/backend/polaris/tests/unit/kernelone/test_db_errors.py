"""Tests for polaris.kernelone.db.errors."""

from __future__ import annotations

import pytest
from polaris.kernelone.db.errors import (
    DatabaseConnectionError,
    DatabaseDriverNotAvailableError,
    DatabasePathError,
    DatabasePolicyError,
    KernelDatabaseError,
)


class TestKernelDatabaseError:
    def test_is_runtime_error_subclass(self) -> None:
        assert issubclass(KernelDatabaseError, RuntimeError)

    def test_can_be_raised_and_caught(self) -> None:
        with pytest.raises(KernelDatabaseError):
            raise KernelDatabaseError("test error")

    def test_message_preserved(self) -> None:
        exc = KernelDatabaseError("custom message")
        assert str(exc) == "custom message"


class TestDatabasePathError:
    def test_is_kernel_database_error_subclass(self) -> None:
        assert issubclass(DatabasePathError, KernelDatabaseError)

    def test_can_be_raised(self) -> None:
        with pytest.raises(DatabasePathError):
            raise DatabasePathError("bad path")

    def test_caught_by_kernel_database_error(self) -> None:
        with pytest.raises(KernelDatabaseError):
            raise DatabasePathError("bad path")


class TestDatabasePolicyError:
    def test_is_kernel_database_error_subclass(self) -> None:
        assert issubclass(DatabasePolicyError, KernelDatabaseError)

    def test_can_be_raised(self) -> None:
        with pytest.raises(DatabasePolicyError):
            raise DatabasePolicyError("policy violation")

    def test_caught_by_kernel_database_error(self) -> None:
        with pytest.raises(KernelDatabaseError):
            raise DatabasePolicyError("policy violation")


class TestDatabaseDriverNotAvailableError:
    def test_is_kernel_database_error_subclass(self) -> None:
        assert issubclass(DatabaseDriverNotAvailableError, KernelDatabaseError)

    def test_can_be_raised(self) -> None:
        with pytest.raises(DatabaseDriverNotAvailableError):
            raise DatabaseDriverNotAvailableError("driver missing")


class TestDatabaseConnectionError:
    def test_is_kernel_database_error_subclass(self) -> None:
        assert issubclass(DatabaseConnectionError, KernelDatabaseError)

    def test_can_be_raised(self) -> None:
        with pytest.raises(DatabaseConnectionError):
            raise DatabaseConnectionError("connection failed")


class TestExceptionHierarchy:
    def test_all_caught_by_base(self) -> None:
        exceptions = [
            DatabasePathError("path"),
            DatabasePolicyError("policy"),
            DatabaseDriverNotAvailableError("driver"),
            DatabaseConnectionError("conn"),
        ]
        for exc in exceptions:
            with pytest.raises(KernelDatabaseError):
                raise exc

    def test_distinct_types(self) -> None:
        assert DatabasePathError is not DatabasePolicyError
        assert DatabaseConnectionError is not DatabaseDriverNotAvailableError
