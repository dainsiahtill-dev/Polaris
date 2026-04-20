from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from polaris.kernelone.constants import DEFAULT_SHORT_TIMEOUT_SECONDS

SQLiteRowFactory = Callable[[Any, tuple[Any, ...]], Any]


@dataclass(frozen=True)
class SQLiteConnectOptions:
    """Immutable options for opening a SQLite connection via KernelSQLiteAdapter.

    Attributes:
        timeout_seconds: Busy-timeout passed to ``sqlite3.connect``. Default 30 s.
        check_same_thread: Whether to restrict connection access to one thread.
            Default False (safer for threaded use).
        isolation_level: Transaction mode. None = autocommit; "DEFERRED",
            "IMMEDIATE", "EXCLUSIVE" = explicit locking.
        detect_types: Bitmask enabling automatic type converters.
        uri: If True, ``path`` is interpreted as a URI.
        row_factory: Row factory name (e.g. "row"), callable, or None.
        pragmas: Dict of ``PRAGMA key=value`` statements executed on connect.
    """

    timeout_seconds: float = DEFAULT_SHORT_TIMEOUT_SECONDS
    check_same_thread: bool = False
    isolation_level: str | None = None
    detect_types: int = 0
    uri: bool = False
    row_factory: str | SQLiteRowFactory | None = "row"
    pragmas: dict[str, str | int | float | bool] = field(default_factory=dict)


@dataclass(frozen=True)
class SQLAlchemyConnectOptions:
    """Immutable options for creating a SQLAlchemy engine via KernelSQLAlchemyAdapter.

    Attributes:
        connect_args: Extra arguments forwarded to the DBAPI ``connect()`` call.
        pool_class: Custom pool class (e.g. ``QueuePool``). None = default.
        pool_pre_ping: If True, test connections with a SELECT before use.
            Default True to catch stale connections.
        echo: If True, log all SQL statements. Default False.
    """

    connect_args: dict[str, Any] = field(default_factory=dict)
    pool_class: Any = None
    pool_pre_ping: bool = True
    echo: bool = False


class KernelSQLiteAdapterPort(Protocol):
    """Abstract port for establishing a SQLite database connection.

    Implementations: ``SqliteAdapter`` (in-process). All file operations
    use UTF-8 encoding as required by KernelOne.
    """

    def connect(self, path: str, options: SQLiteConnectOptions) -> Any:
        """Open a SQLite connection.

        Args:
            path: Absolute or relative path to the SQLite database file.
            options: Connection options including timeout and pragmas.

        Returns:
            A DBAPI connection object (e.g. ``sqlite3.Connection``).
        """


class KernelSQLAlchemyAdapterPort(Protocol):
    """Abstract port for creating and disposing SQLAlchemy engines.

    Implementations: ``SQLAlchemyAdapter`` (in-process). Used when the
    Cell requires relational DB access beyond SQLite.
    """

    def create_engine(self, database_url: str, options: SQLAlchemyConnectOptions) -> Any:
        """Create and return a SQLAlchemy engine.

        Args:
            database_url: RFC-compliant database URL
                (e.g. "sqlite:///path.db", "postgresql+psycopg2://user:pass@host/db").
            options: Engine options including pool settings and pre-ping.

        Returns:
            A SQLAlchemy ``Engine`` instance.
        """

    def dispose_engine(self, engine: Any) -> None:
        """Dispose of an engine and close all pooled connections.

        Args:
            engine: The engine instance returned by ``create_engine``.
        """


class KernelLanceDbAdapterPort(Protocol):
    """Abstract port for connecting to a LanceDB instance.

    Implementations: ``LanceDbAdapter`` (in-process). Used for
    vector / semantic search operations.
    """

    def connect(self, uri: str) -> Any:
        """Connect to or create a LanceDB database at the given URI.

        Args:
            uri: Directory path or ``lc://`` URI for the LanceDB database.

        Returns:
            A LanceDB database client instance.
        """
