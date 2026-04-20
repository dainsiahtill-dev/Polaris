import io
import logging
from typing import Any

try:
    import pyarrow as pa

    ARROW_AVAILABLE = True
except ImportError:
    ARROW_AVAILABLE = False

logger = logging.getLogger("app.services.arrow_service")


class ArrowService:
    def __init__(self) -> None:
        self.available = ARROW_AVAILABLE

    def to_arrow_ipc(self, data: list[dict[str, Any]], schema: Any | None = None) -> bytes | None:
        """
        Converts a list of dicts to an Arrow IPC stream (bytes).
        Returns None if pyarrow is unavailable.
        """
        if not self.available:
            logger.warning("PyArrow not available, skipping IPC conversion.")
            return None

        try:
            # Create Table
            table = pa.Table.from_pylist(data, schema=schema)

            # Serialize to IPC Stream (in-memory)
            sink = io.BytesIO()
            with pa.ipc.new_stream(sink, table.schema) as writer:
                writer.write_table(table)

            return sink.getvalue()
        except (RuntimeError, ValueError) as e:
            logger.error(f"Failed to convert to Arrow IPC: {e}")
            return None

    def get_status(self):
        return {"available": self.available, "version": pa.__version__ if self.available else None}


_service = ArrowService()


def get_arrow_service() -> ArrowService:
    return _service
