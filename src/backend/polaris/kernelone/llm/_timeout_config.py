"""Polaris AI Platform - Unified LLM Timeout Configuration

This module provides unified timeout configuration for both stream and non-stream
LLM invocations. It serves as the single source of truth for timeout settings,
replacing scattered global variables and duplicate env var handling.

Design principles:
1. KERNELONE_* / KERNELONE_* env vars are the canonical source (via _runtime_config.py)
2. Stream and non-stream timeouts are configurable independently but managed together
3. Backward compatibility via deprecated globals that delegate to this module
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from polaris.kernelone._runtime_config import resolve_env_float, resolve_env_int

if TYPE_CHECKING:
    pass

# ─── Module-level lock for thread-safe global state ───────────────────────────
_config_lock = threading.Lock()

# ─── Default timeout values ────────────────────────────────────────────────────
# These match the defaults in _runtime_config.py
_DEFAULT_INVOKE_TIMEOUT_SEC: float = 30.0
_DEFAULT_STREAM_TIMEOUT_SEC: float = 300.0
_DEFAULT_TOKEN_TIMEOUT_SEC: float = 60.0
_DEFAULT_MAX_CONCURRENCY: int = 100


# ─── Global configuration state ──────────────────────────────────────────────
# These are set once at module load and can be reset via _reset_config()
_global_invoke_timeout: float = _DEFAULT_INVOKE_TIMEOUT_SEC
_global_stream_timeout: float = _DEFAULT_STREAM_TIMEOUT_SEC
_global_token_timeout: float = _DEFAULT_TOKEN_TIMEOUT_SEC
_global_max_concurrency: int = _DEFAULT_MAX_CONCURRENCY


def _load_config_from_env() -> None:
    """Load timeout configuration from environment variables.

    Called once at module import. Uses _runtime_config.py's fallback chain:
    KERNELONE_* -> KERNELONE_* -> default
    """
    global _global_invoke_timeout, _global_stream_timeout, _global_token_timeout, _global_max_concurrency

    _global_invoke_timeout = resolve_env_float("llm_invoke_timeout_sec")
    if _global_invoke_timeout <= 0:
        _global_invoke_timeout = _DEFAULT_INVOKE_TIMEOUT_SEC

    _global_stream_timeout = resolve_env_float("llm_stream_timeout_sec")
    if _global_stream_timeout <= 0:
        _global_stream_timeout = _DEFAULT_STREAM_TIMEOUT_SEC

    _global_token_timeout = resolve_env_float("llm_token_timeout_sec")
    if _global_token_timeout <= 0:
        _global_token_timeout = _DEFAULT_TOKEN_TIMEOUT_SEC

    _global_max_concurrency = resolve_env_int("llm_max_concurrency")
    if _global_max_concurrency <= 0:
        _global_max_concurrency = _DEFAULT_MAX_CONCURRENCY


# Load config at module import
_load_config_from_env()


# ─── Public accessor functions ─────────────────────────────────────────────────


def get_invoke_timeout() -> float:
    """Get the configured non-stream invoke timeout in seconds.

    Returns:
        The invoke timeout in seconds (default: 30.0).
    """
    return _global_invoke_timeout


def get_stream_timeout() -> float:
    """Get the configured stream overall timeout in seconds.

    Returns:
        The stream timeout in seconds (default: 300.0).
    """
    return _global_stream_timeout


def get_token_timeout() -> float:
    """Get the configured per-token timeout in seconds.

    This is used for streaming to detect when the stream has stalled.

    Returns:
        The token timeout in seconds (default: 60.0).
    """
    return _global_token_timeout


def get_max_concurrency() -> int:
    """Get the maximum number of concurrent LLM invocations.

    Returns:
        The max concurrency limit (default: 100).
    """
    return _global_max_concurrency


def set_invoke_timeout(timeout_sec: float) -> None:
    """Set the invoke timeout (for testing).

    Args:
        timeout_sec: New timeout value in seconds.
    """
    global _global_invoke_timeout
    with _config_lock:
        _global_invoke_timeout = float(timeout_sec)


def set_stream_timeout(timeout_sec: float) -> None:
    """Set the stream timeout (for testing).

    Args:
        timeout_sec: New timeout value in seconds.
    """
    global _global_stream_timeout
    with _config_lock:
        _global_stream_timeout = float(timeout_sec)


def set_token_timeout(timeout_sec: float) -> None:
    """Set the token timeout (for testing).

    Args:
        timeout_sec: New timeout value in seconds.
    """
    global _global_token_timeout
    with _config_lock:
        _global_token_timeout = float(timeout_sec)


def reset_config() -> None:
    """Reset all timeout configuration to values from environment.

    This reloads from env vars, useful for tests that modify the environment.
    """
    _load_config_from_env()


# ─── Backward compatibility aliases ──────────────────────────────────────────
# These delegate to the unified config functions above

# Non-stream executor compatibility
INVOKE_TIMEOUT_SEC = _global_invoke_timeout


# Stream executor compatibility
_TOKEN_TIMEOUT: float = _global_token_timeout
_STREAM_TIMEOUT: float = _global_stream_timeout
_MAX_PENDING_TOOL_CALLS: int = 100
MAX_BUFFER_SIZE: int = 100


__all__ = [
    "INVOKE_TIMEOUT_SEC",
    "MAX_BUFFER_SIZE",
    "_MAX_PENDING_TOOL_CALLS",
    "_STREAM_TIMEOUT",
    "_TOKEN_TIMEOUT",
    "get_invoke_timeout",
    "get_max_concurrency",
    "get_stream_timeout",
    "get_token_timeout",
    "reset_config",
    "set_invoke_timeout",
    "set_stream_timeout",
    "set_token_timeout",
]
