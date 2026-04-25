"""Tree-sitter availability detection for KernelOne.

Provides a mechanism to detect whether tree-sitter language pack
is available and functional in the current environment.
"""

from __future__ import annotations

import signal
import time
from dataclasses import dataclass
from functools import lru_cache

from polaris.kernelone.errors import TimeoutError as _CanonicalTimeoutError


@dataclass(frozen=True)
class TreeSitterAvailability:
    """Tree-sitter availability status.

    Attributes:
        available: Whether tree-sitter is available.
        reason: Reason if unavailable (import_error, parser_unavailable, timeout).
        checked_at: Unix timestamp when the check was performed.
    """

    available: bool
    reason: str | None = None
    checked_at: float | None = None


class TimeoutError(_CanonicalTimeoutError):
    """Raised when a blocking operation exceeds the timeout.

    Inherits from polaris.kernelone.errors.TimeoutError (canonical).
    """

    pass


def _timeout_handler(signum: int, frame) -> None:
    """Signal handler for timeout."""
    raise TimeoutError("Tree-sitter availability check timed out", operation="tree-sitter availability check")


@lru_cache(maxsize=1)
def is_tree_sitter_available() -> TreeSitterAvailability:
    """Check if tree-sitter language pack is available.

    This function attempts to import tree_sitter_language_pack and obtain
    a Python parser. The result is cached after the first call.

    Returns:
        TreeSitterAvailability indicating whether tree-sitter is available.

    Raises:
        Any exception raised during the check is caught and converted
        to an appropriate availability result.
    """
    checked_at = time.time()

    # Set up timeout using signal (Unix-like systems)
    timeout_seconds = 5

    def check_with_timeout() -> TreeSitterAvailability:
        try:

            # Attempt to get the Python parser

            parser = get_parser("python")
            if parser is None:
                return TreeSitterAvailability(
                    available=False,
                    reason="parser_unavailable",
                    checked_at=checked_at,
                )

            return TreeSitterAvailability(
                available=True,
                reason=None,
                checked_at=checked_at,
            )
        except ImportError:
            return TreeSitterAvailability(
                available=False,
                reason="import_error",
                checked_at=checked_at,
            )
        except (RuntimeError, ValueError) as exc:
            # Catch any other unexpected errors but preserve the actual exception type
            return TreeSitterAvailability(
                available=False,
                reason=f"check_error:{type(exc).__name__}",
                checked_at=checked_at,
            )

    # Try signal-based timeout on Unix-like systems
    if hasattr(signal, "SIGALRM") and hasattr(signal, "alarm"):
        try:
            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(timeout_seconds)  # type: ignore[attr-defined]
            try:
                result = check_with_timeout()
            finally:
                signal.alarm(0)  # type: ignore[attr-defined]
                signal.signal(signal.SIGALRM, old_handler)
            return result
        except TimeoutError:
            return TreeSitterAvailability(
                available=False,
                reason="timeout",
                checked_at=checked_at,
            )
    else:
        # Fallback for Windows or systems without SIGALRM
        import threading

        result_holder: dict[str, TreeSitterAvailability | None] = {"result": None}
        exception_holder: dict[str, Exception | None] = {"exception": None}

        def target() -> None:
            try:
                result_holder["result"] = check_with_timeout()
            except (RuntimeError, ValueError) as e:
                exception_holder["exception"] = e

        thread = threading.Thread(target=target)
        thread.start()
        thread.join(timeout=timeout_seconds)

        if thread.is_alive():
            # Thread is still running, consider it a timeout
            return TreeSitterAvailability(
                available=False,
                reason="timeout",
                checked_at=checked_at,
            )

        if exception_holder["exception"] is not None:
            return TreeSitterAvailability(
                available=False,
                reason="import_error",
                checked_at=checked_at,
            )

        if result_holder["result"] is not None:
            return result_holder["result"]

        return TreeSitterAvailability(
            available=False,
            reason="import_error",
            checked_at=checked_at,
        )
