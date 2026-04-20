"""ShadowPlayer: Replays HTTP exchanges from cassette.

Handles the replay logic for intercepted HTTP calls.
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

import httpx
from polaris.kernelone.benchmark.reproducibility.shadow_replay.exceptions import (
    UnrecordedRequestError,
)

if TYPE_CHECKING:
    from polaris.kernelone.benchmark.reproducibility.shadow_replay.cassette import (
        Cassette,
        CassetteEntry,
    )
    from polaris.kernelone.benchmark.reproducibility.shadow_replay.http_intercept import (
        HTTPExchange,
    )

logger = logging.getLogger(__name__)


class MockResponse(httpx.Response):
    """Mock httpx.Response for replay.

    Inherits from httpx.Response to ensure full API compatibility.
    """

    def __init__(
        self,
        status_code: int,
        headers: dict[str, str],
        content: bytes,
        request: httpx.Request | None = None,
    ) -> None:
        # Call parent __init__ with minimal required args
        super().__init__(
            status_code=status_code,
            headers=headers,
            content=content,
            request=request,
        )

    # Override is_stream_consumed to track state
    @property
    def is_stream_consumed(self) -> bool:
        return self._is_stream_consumed

    @is_stream_consumed.setter
    def is_stream_consumed(self, value: bool) -> None:
        self._is_stream_consumed = value

    def __repr__(self) -> str:
        return f"MockResponse(status_code={self.status_code}, headers={self.headers})"


class ShadowPlayer:
    """Replays HTTP exchanges from a cassette file.

    Usage:
        player = ShadowPlayer(cassette)
        await player.start()
        # ... HTTP calls are intercepted and replayed ...
        await player.stop()
    """

    def __init__(
        self,
        cassette: Cassette,
        strict: bool = True,
    ) -> None:
        """Initialize player.

        Args:
            cassette: Cassette to replay from
            strict: If True, raise UnrecordedRequestError for missing entries
        """
        self.cassette = cassette
        self.strict = strict
        self._playback_count = 0
        self._miss_count = 0

    def _compute_body_hash(self, body: bytes | None) -> str:
        """Compute body hash for matching.

        Args:
            body: Request body bytes

        Returns:
            SHA256 hash (first 32 chars) or empty string
        """
        if not body:
            return ""
        return hashlib.sha256(body).hexdigest()[:32]

    def _find_entry(
        self,
        method: str,
        url: str,
        body_hash: str | None = None,
    ) -> CassetteEntry | None:
        """Find a matching entry in the cassette.

        Args:
            method: HTTP method
            url: Full URL
            body_hash: Optional body hash for precise matching

        Returns:
            Matching CassetteEntry or None
        """
        return self.cassette.find_entry(method, url, body_hash)

    async def intercept(self, exchange: HTTPExchange) -> tuple[bool, httpx.Response | None]:
        """Handle an intercepted exchange and return mocked response.

        This is the callback registered with http_intercept.

        Args:
            exchange: The captured HTTP exchange

        Returns:
            Tuple of (should_proceed, response):
            - should_proceed=False, response=MockResponse: short-circuit and return mock
            - should_proceed=True, response=None: proceed with real HTTP call
        """
        # Compute body hash for matching
        body_hash = self._compute_body_hash(exchange.body)

        # Find matching entry (try exact match first, then URL-only)
        entry = self._find_entry(
            method=exchange.method,
            url=exchange.url,
            body_hash=body_hash if body_hash else None,
        )

        if entry is None and body_hash:
            # Fall back to URL-only match if body hash doesn't match
            entry = self._find_entry(
                method=exchange.method,
                url=exchange.url,
                body_hash=None,
            )

        if entry is None:
            self._miss_count += 1
            msg = f"[ShadowPlayer] No recording found for: {exchange.method} {exchange.url}"
            if body_hash:
                msg += f" (body_hash={body_hash})"

            if self.strict:
                logger.error("[ShadowPlayer] %s", msg)
                raise UnrecordedRequestError(
                    method=exchange.method,
                    url=exchange.url,
                    body_hash=body_hash,
                )
            else:
                logger.warning("[ShadowPlayer] %s", msg)
                return (
                    False,
                    MockResponse(
                        status_code=404,
                        headers={"Content-Type": "application/json"},
                        content=b'{"error": "Recording not found"}',
                    ),
                )

        self._playback_count += 1

        logger.debug(
            "[ShadowPlayer] Replayed #%d: %s %s -> %d",
            self._playback_count,
            entry.request.method,
            entry.request.url,
            entry.response.status_code,
        )

        # Build mock response - use full body if available, fallback to preview
        response_body = b""
        if entry.response.body is not None:
            # Use full body (stored as base64)
            response_body = entry.response.get_body_bytes() or b""
        elif entry.response.body_preview:
            # Fall back to truncated preview
            response_body = entry.response.body_preview.encode("utf-8")

        return (
            False,  # Short-circuit, don't make real call
            MockResponse(
                status_code=entry.response.status_code,
                headers=entry.response.headers,
                content=response_body,
            ),
        )

    async def start(self) -> None:
        """Start replay mode."""
        logger.info(
            "[ShadowPlayer] Started replay from cassette '%s' (strict=%s, entries=%d)",
            self.cassette.cassette_id,
            self.strict,
            len(self.cassette.format.entries),
        )

    async def stop(self) -> None:
        """Stop replay and report statistics."""
        logger.info(
            "[ShadowPlayer] Stopped. Played: %d, Missed: %d",
            self._playback_count,
            self._miss_count,
        )

    @property
    def playback_count(self) -> int:
        """Number of exchanges played back."""
        return self._playback_count

    @property
    def miss_count(self) -> int:
        """Number of exchanges not found in cassette."""
        return self._miss_count
