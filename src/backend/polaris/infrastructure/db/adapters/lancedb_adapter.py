from __future__ import annotations

from typing import Any

from polaris.kernelone.db.errors import DatabaseDriverNotAvailableError


class LanceDbAdapter:
    """LanceDB adapter for KernelDatabase."""

    def connect(self, uri: str) -> Any:
        try:
            import lancedb
        except ImportError as exc:  # pragma: no cover - dependency fallback
            raise DatabaseDriverNotAvailableError("lancedb is not installed") from exc
        return lancedb.connect(uri)
