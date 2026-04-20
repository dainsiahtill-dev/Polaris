"""Shared utility functions used across the accel package.

Consolidates commonly duplicated helpers (_utc_now, path normalization,
logging setup) into a single module to reduce copy-paste drift.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def utc_now() -> datetime:
    """Return the current UTC time as a datetime object."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Path normalization helpers
# ---------------------------------------------------------------------------


def normalize_path_str(path: str) -> str:
    """Normalize a relative path string: backslash → slash, strip, drop './' prefix."""
    normalized = str(path or "").replace("\\", "/").strip()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def normalize_path_abs(path: Path) -> Path:
    """Resolve a Path to an absolute path safely (avoids Windows resolve quirks)."""
    return Path(os.path.abspath(str(path)))


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

_ACCEL_LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger under the ``accel`` hierarchy.

    Usage::

        from accel.utils import get_logger
        logger = get_logger(__name__)
        logger.info("indexing complete")
    """
    logger = logging.getLogger(f"accel.{name}" if not name.startswith("accel.") else name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_ACCEL_LOG_FORMAT))
        logger.addHandler(handler)
        logger.setLevel(
            logging.DEBUG if os.environ.get("ACCEL_DEBUG", "").lower() in {"1", "true", "yes"} else logging.WARNING
        )
    return logger
