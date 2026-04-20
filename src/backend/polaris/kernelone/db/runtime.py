from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any

from polaris.kernelone.constants import DEFAULT_SHORT_TIMEOUT_SECONDS

from .contracts import (
    KernelLanceDbAdapterPort,
    KernelSQLAlchemyAdapterPort,
    KernelSQLiteAdapterPort,
    SQLAlchemyConnectOptions,
    SQLiteConnectOptions,
)
from .errors import DatabaseConnectionError, DatabaseDriverNotAvailableError
from .policy import resolve_lancedb_path, resolve_sqlite_path

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KernelDatabaseHealth:
    sqlite_adapter_ready: bool
    sqlalchemy_adapter_ready: bool
    lancedb_adapter_ready: bool
    workspace: str


class KernelDatabase:
    """Kernel-level DB boundary for SQLite/SQLAlchemy/LanceDB."""

    def __init__(
        self,
        workspace: str,
        *,
        sqlite_adapter: KernelSQLiteAdapterPort | None = None,
        sqlalchemy_adapter: KernelSQLAlchemyAdapterPort | None = None,
        lancedb_adapter: KernelLanceDbAdapterPort | None = None,
        allow_unmanaged_absolute: bool = True,
    ) -> None:
        self._workspace = os.path.abspath(str(workspace or os.getcwd()))
        self._sqlite_adapter = sqlite_adapter
        self._sqlalchemy_adapter = sqlalchemy_adapter
        self._lancedb_adapter = lancedb_adapter
        self._allow_unmanaged_absolute = bool(allow_unmanaged_absolute)
        self._engine_lock = threading.Lock()
        self._engines: list[Any] = []
        self._sqlite_lock = threading.Lock()
        self._sqlite_connections: list[Any] = []

    @property
    def workspace(self) -> str:
        return self._workspace

    def resolve_sqlite_path(self, path: str, *, ensure_parent: bool = True) -> str:
        return resolve_sqlite_path(
            self._workspace,
            path,
            allow_unmanaged_absolute=self._allow_unmanaged_absolute,
            ensure_parent=ensure_parent,
        )

    def resolve_lancedb_path(self, path: str, *, ensure_exists: bool = True) -> str:
        return resolve_lancedb_path(
            self._workspace,
            path,
            allow_unmanaged_absolute=self._allow_unmanaged_absolute,
            ensure_exists=ensure_exists,
        )

    def sqlite(
        self,
        path: str,
        *,
        timeout_seconds: float = DEFAULT_SHORT_TIMEOUT_SECONDS,
        check_same_thread: bool = False,
        isolation_level: str | None = None,
        detect_types: int = 0,
        uri: bool = False,
        row_factory: str | Any | None = "row",
        pragmas: dict[str, str | int | float | bool] | None = None,
        ensure_parent: bool = True,
    ) -> Any:
        if self._sqlite_adapter is None:
            raise DatabaseDriverNotAvailableError("sqlite adapter is not configured")
        resolved_path = self.resolve_sqlite_path(path, ensure_parent=ensure_parent)
        options = SQLiteConnectOptions(
            timeout_seconds=float(timeout_seconds),
            check_same_thread=bool(check_same_thread),
            isolation_level=isolation_level,
            detect_types=int(detect_types),
            uri=bool(uri),
            row_factory=row_factory,
            pragmas=dict(pragmas or {}),
        )
        try:
            conn = self._sqlite_adapter.connect(resolved_path, options)
        except (RuntimeError, ValueError) as exc:
            raise DatabaseConnectionError(f"failed to connect sqlite database: {resolved_path}") from exc
        with self._sqlite_lock:
            self._sqlite_connections.append(conn)
        return conn

    def sqlalchemy(
        self,
        database_url: str,
        *,
        connect_args: dict[str, Any] | None = None,
        pool_class: Any = None,
        pool_pre_ping: bool = True,
        echo: bool = False,
    ) -> Any:
        if self._sqlalchemy_adapter is None:
            raise DatabaseDriverNotAvailableError("sqlalchemy adapter is not configured")
        token = str(database_url or "").strip()
        if not token:
            raise ValueError("database_url is required")
        options = SQLAlchemyConnectOptions(
            connect_args=dict(connect_args or {}),
            pool_class=pool_class,
            pool_pre_ping=bool(pool_pre_ping),
            echo=bool(echo),
        )
        try:
            engine = self._sqlalchemy_adapter.create_engine(token, options)
        except (RuntimeError, ValueError) as exc:
            raise DatabaseConnectionError(f"failed to create sqlalchemy engine: {token}") from exc
        with self._engine_lock:
            self._engines.append(engine)
        return engine

    def lancedb(self, path: str, *, ensure_exists: bool = True) -> Any:
        if self._lancedb_adapter is None:
            raise DatabaseDriverNotAvailableError("lancedb adapter is not configured")
        resolved = self.resolve_lancedb_path(path, ensure_exists=ensure_exists)
        try:
            return self._lancedb_adapter.connect(resolved)
        except (RuntimeError, ValueError) as exc:
            raise DatabaseConnectionError(f"failed to connect lancedb: {resolved}") from exc

    def close(self, engine: Any | None = None) -> None:
        with self._sqlite_lock:
            sqlite_targets = list(self._sqlite_connections)
            self._sqlite_connections.clear()
        for conn in sqlite_targets:
            try:
                conn.close()
            except (RuntimeError, ValueError) as exc:
                _logger.warning("kernelone.db.runtime.close_sqlite_conn failed: %s", exc, exc_info=True)
                continue

        if self._sqlalchemy_adapter is None:
            return
        with self._engine_lock:
            if engine is not None:
                targets = [engine]
                self._engines = [item for item in self._engines if item is not engine]
            else:
                targets = list(self._engines)
                self._engines.clear()
        for target in targets:
            try:
                self._sqlalchemy_adapter.dispose_engine(target)
            except (RuntimeError, ValueError) as exc:
                _logger.warning("kernelone.db.runtime.dispose_engine failed: %s", exc, exc_info=True)
                continue

    def health(self) -> KernelDatabaseHealth:
        return KernelDatabaseHealth(
            sqlite_adapter_ready=self._sqlite_adapter is not None,
            sqlalchemy_adapter_ready=self._sqlalchemy_adapter is not None,
            lancedb_adapter_ready=self._lancedb_adapter is not None,
            workspace=self._workspace,
        )
