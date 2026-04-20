from __future__ import annotations

import re
import sqlite3
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from polaris.kernelone.db.contracts import SQLiteConnectOptions

_PRAGMA_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _format_pragma_value(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    if isinstance(value, (int, float)):
        return str(value)
    token = str(value)
    if re.fullmatch(r"[A-Za-z0-9_.-]+", token):
        return token
    escaped = token.replace("'", "''")
    return f"'{escaped}'"


class SqliteAdapter:
    """SQLite adapter for KernelDatabase."""

    def connect(self, path: str, options: SQLiteConnectOptions) -> sqlite3.Connection:
        isolation: Literal["DEFERRED", "EXCLUSIVE", "IMMEDIATE"] | None
        if options.isolation_level is None:
            isolation = None
        elif options.isolation_level in ("DEFERRED", "EXCLUSIVE", "IMMEDIATE"):
            isolation = options.isolation_level  # type: ignore[assignment]
        else:
            isolation = "DEFERRED"  # default
        conn = sqlite3.connect(
            path,
            timeout=float(options.timeout_seconds),
            check_same_thread=bool(options.check_same_thread),
            isolation_level=isolation,
            detect_types=int(options.detect_types),
            uri=bool(options.uri),
        )
        self._apply_row_factory(conn, options.row_factory)
        self._apply_pragmas(conn, options.pragmas)
        return conn

    def _apply_row_factory(
        self,
        conn: sqlite3.Connection,
        row_factory: str | Any | None,
    ) -> None:
        if row_factory is None:
            return
        if isinstance(row_factory, str):
            token = row_factory.strip().lower()
            if token == "row":
                conn.row_factory = sqlite3.Row
                return
            raise ValueError(f"unsupported sqlite row_factory string: {row_factory}")
        if callable(row_factory):
            conn.row_factory = row_factory
            return
        raise ValueError(f"unsupported sqlite row_factory type: {type(row_factory)!r}")

    def _apply_pragmas(
        self,
        conn: sqlite3.Connection,
        pragmas: dict[str, str | int | float | bool],
    ) -> None:
        for name, value in pragmas.items():
            pragma_name = str(name or "").strip()
            if not _PRAGMA_NAME_RE.fullmatch(pragma_name):
                raise ValueError(f"invalid sqlite pragma name: {name}")
            sql = f"PRAGMA {pragma_name}={_format_pragma_value(value)}"
            conn.execute(sql)
