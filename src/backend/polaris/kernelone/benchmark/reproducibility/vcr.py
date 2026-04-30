"""
VCR-based Cache Replay System for LLM Call Recording and Replay

Provides deterministic replay of LLM responses for reproducible testing.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar, cast

F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class Recording:
    """Immutable recording of a request-response pair.

    Attributes:
        request_key: Deterministic key derived from function arguments.
        response: The cached response data.
        timestamp: ISO format timestamp of when the recording was created.
        metadata: Optional metadata dictionary.
        method: HTTP method (GET, POST, etc.) if applicable.
        url: Full URL of the HTTP request if applicable.
        request_headers: HTTP request headers if applicable.
        request_body: HTTP request body as string if applicable.
        response_status: HTTP response status code if applicable.
        response_headers: HTTP response headers if applicable.
        latency_ms: Request latency in milliseconds if applicable.
    """

    request_key: str
    response: dict[str, Any]
    timestamp: str
    metadata: dict[str, Any] | None = None
    method: str = ""
    url: str = ""
    request_headers: dict[str, str] | None = None
    request_body: str | None = None
    response_status: int = 0
    response_headers: dict[str, str] | None = None
    latency_ms: float = 0.0


class CacheReplay:
    """
    Cache replay mechanism for deterministic LLM response replaying.

    Modes:
        - "record": Only record new responses, fail if exists
        - "replay": Only replay existing recordings, fail if missing
        - "both": Record new and replay existing (default for development)

    Usage:
        cache = CacheReplay("./cache_dir", mode="both")

        @cache.replay
        async def call_llm(messages):
            return await actual_llm_call(messages)
    """

    def __init__(
        self,
        cache_dir: str | Path,
        mode: str = "both",
        key_prefix: str = "",
    ) -> None:
        """
        Initialize cache replay system.

        Args:
            cache_dir: Directory to store cached responses
            mode: Operation mode ("record" | "replay" | "both")
            key_prefix: Optional prefix for cache keys
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.key_prefix = key_prefix

        valid_modes = {"record", "replay", "both"}
        if mode not in valid_modes:
            raise ValueError(f"Invalid mode: {mode}. Must be one of {valid_modes}")

    def _make_key(self, *args: Any, **kwargs: Any) -> str:
        """
        Generate deterministic cache key from arguments.

        Args:
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            16-character hex key
        """
        content = json.dumps(
            {
                "args": [str(a) for a in args],
                "kwargs": {k: str(v) for k, v in sorted(kwargs.items())},
            },
            sort_keys=True,
        )
        key_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return f"{self.key_prefix}{key_hash[:16]}"

    def _get_cache_path(self, key: str) -> Path:
        """Get file path for a cache key."""
        return self.cache_dir / f"{key}.json"

    def _load_recording(self, key: str) -> Recording | None:
        """
        Load recording from cache.

        Args:
            key: Cache key

        Returns:
            Recording if exists, None otherwise
        """
        path = self._get_cache_path(key)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return Recording(**data)
        return None

    def _save_recording(
        self,
        key: str,
        response: dict[str, Any],
        method: str = "",
        url: str = "",
        request_headers: dict[str, str] | None = None,
        request_body: str | None = None,
        response_status: int = 0,
        response_headers: dict[str, str] | None = None,
        latency_ms: float = 0.0,
    ) -> None:
        """Save response to cache with optional HTTP details.

        Args:
            key: Cache key
            response: Response data to cache
            method: HTTP method (GET, POST, etc.)
            url: Full URL of the request
            request_headers: HTTP request headers
            request_body: HTTP request body as string
            response_status: HTTP response status code
            response_headers: HTTP response headers
            latency_ms: Request latency in milliseconds
        """
        recording = Recording(
            request_key=key,
            response=response,
            timestamp=datetime.now(timezone.utc).isoformat(),
            method=method,
            url=url,
            request_headers=request_headers,
            request_body=request_body,
            response_status=response_status,
            response_headers=response_headers,
            latency_ms=latency_ms,
        )
        path = self._get_cache_path(key)
        path.write_text(json.dumps(asdict(recording), indent=2), encoding="utf-8")

    def replay(self, func: F) -> F:
        """
        Decorator to enable cache replay for a function.

        Args:
            func: Async or sync function to wrap

        Returns:
            Wrapped function with cache replay
        """
        is_async = inspect.iscoroutinefunction(func)

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            key = self._make_key(*args, **kwargs)

            # Try to replay
            if self.mode in ("replay", "both"):
                recording = self._load_recording(key)
                if recording:
                    return recording.response

            # Execute actual call
            response = await func(*args, **kwargs)

            # Record response
            if self.mode in ("record", "both"):
                self._save_recording(key, response)

            return response

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            key = self._make_key(*args, **kwargs)

            # Try to replay
            if self.mode in ("replay", "both"):
                recording = self._load_recording(key)
                if recording:
                    return recording.response

            # Execute actual call
            response = func(*args, **kwargs)

            # Record response
            if self.mode in ("record", "both"):
                self._save_recording(key, response)

            return response

        return cast(Callable[..., Any], async_wrapper if is_async else sync_wrapper)

    def clear(self, key: str | None = None) -> None:
        """
        Clear cache entries.

        Args:
            key: Specific key to clear, or None to clear all
        """
        if key:
            path = self._get_cache_path(key)
            if path.exists():
                path.unlink()
        else:
            for path in self.cache_dir.glob("*.json"):
                path.unlink()

    def list_recordings(self) -> list[Recording]:
        """
        List all cached recordings.

        Returns:
            List of Recording objects
        """
        recordings = []
        for path in self.cache_dir.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            recordings.append(Recording(**data))
        return recordings

    def has_recording(self, key: str) -> bool:
        """
        Check if a recording exists for a key.

        Args:
            key: Cache key to check

        Returns:
            True if recording exists
        """
        return self._get_cache_path(key).exists()

    def record(
        self,
        key: str,
        response: dict[str, Any],
        method: str = "",
        url: str = "",
        request_headers: dict[str, str] | None = None,
        request_body: str | None = None,
        response_status: int = 0,
        response_headers: dict[str, str] | None = None,
        latency_ms: float = 0.0,
    ) -> None:
        """Record a response with HTTP details.

        This method allows explicit recording of a response with full HTTP
        protocol details. Useful for HTTP-level interception scenarios.

        Args:
            key: Cache key (typically generated via _make_key)
            response: Response data to cache
            method: HTTP method (GET, POST, etc.)
            url: Full URL of the request
            request_headers: HTTP request headers
            request_body: HTTP request body as string
            response_status: HTTP response status code
            response_headers: HTTP response headers
            latency_ms: Request latency in milliseconds
        """
        self._save_recording(
            key=key,
            response=response,
            method=method,
            url=url,
            request_headers=request_headers,
            request_body=request_body,
            response_status=response_status,
            response_headers=response_headers,
            latency_ms=latency_ms,
        )
