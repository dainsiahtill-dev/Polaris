from __future__ import annotations

from typing import TYPE_CHECKING, Any

from polaris.kernelone.db.errors import DatabaseDriverNotAvailableError

if TYPE_CHECKING:
    from polaris.kernelone.db.contracts import SQLAlchemyConnectOptions


class SqlAlchemyAdapter:
    """SQLAlchemy adapter for KernelDatabase."""

    def create_engine(self, database_url: str, options: SQLAlchemyConnectOptions) -> Any:
        try:
            from sqlalchemy import create_engine
        except ImportError as exc:  # pragma: no cover - dependency fallback
            raise DatabaseDriverNotAvailableError("sqlalchemy is not installed") from exc
        kwargs: dict[str, Any] = {
            "connect_args": dict(options.connect_args),
            "pool_pre_ping": bool(options.pool_pre_ping),
            "echo": bool(options.echo),
        }
        if options.pool_class is not None:
            kwargs["poolclass"] = options.pool_class
        return create_engine(database_url, **kwargs)

    def dispose_engine(self, engine: Any) -> None:
        dispose_fn = getattr(engine, "dispose", None)
        if callable(dispose_fn):
            dispose_fn()
