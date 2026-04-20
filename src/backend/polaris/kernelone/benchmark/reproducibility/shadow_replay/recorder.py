"""ShadowRecorder: Records HTTP exchanges to cassette.

Handles the recording logic for intercepted HTTP calls.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from polaris.kernelone.benchmark.reproducibility.shadow_replay.cassette import (
    Cassette,
    HTTPRequest,
    HTTPResponse,
)

if TYPE_CHECKING:
    import httpx
    from polaris.kernelone.benchmark.reproducibility.shadow_replay.http_intercept import (
        HTTPExchange,
    )

logger = logging.getLogger(__name__)


class ShadowRecorder:
    """Records HTTP exchanges to a cassette file.

    Usage:
        recorder = ShadowRecorder(cassette)
        await recorder.start()
        # ... make HTTP calls ...
        await recorder.stop()
    """

    def __init__(
        self,
        cassette: Cassette,
        auto_save: bool = True,
    ) -> None:
        """Initialize recorder.

        Args:
            cassette: Cassette to record to
            auto_save: Whether to save after each entry
        """
        self.cassette = cassette
        self.auto_save = auto_save
        self._exchange_count = 0

    async def intercept(self, exchange: HTTPExchange) -> tuple[bool, httpx.Response | None]:
        """Handle an intercepted exchange and record it.

        This is the callback registered with http_intercept.

        Args:
            exchange: The captured HTTP exchange

        Returns:
            Tuple of (should_proceed, response):
            - should_proceed=True, response=None: proceed with real call (already done)
            - should_proceed=False, response=real response: return real response after recording
        """
        self._exchange_count += 1

        # Build request
        request = HTTPRequest.from_raw(
            method=exchange.method,
            url=exchange.url,
            headers=exchange.headers,
            body=exchange.body,
        )

        # Build response
        response = HTTPResponse.from_raw(
            status_code=exchange.response_status,
            headers=exchange.response_headers,
            body=exchange.response_body,
        )

        # Add to cassette (sanitization happens at save time)
        self.cassette.add_entry(
            request=request,
            response=response,
            latency_ms=exchange.latency_ms,
        )

        # Auto-save if enabled (cassette.save() applies sanitization)
        if self.auto_save:
            self.cassette.save()

        logger.debug(
            "[ShadowRecorder] Recorded #%d: %s %s -> %d",
            self._exchange_count,
            exchange.method,
            exchange.url,
            exchange.response_status,
        )

        # Return (proceed=False, response=original) to return real response
        # The real HTTP call was already made before this callback
        return (False, exchange.response_object)

    async def start(self) -> None:
        """Start recording."""
        logger.info(
            "[ShadowRecorder] Started recording to cassette '%s'",
            self.cassette.cassette_id,
        )

    async def stop(self) -> None:
        """Stop recording and save cassette."""
        if self.auto_save:
            self.cassette.save()
        logger.info(
            "[ShadowRecorder] Stopped. Recorded %d exchanges to '%s'",
            self._exchange_count,
            self.cassette.cassette_id,
        )

    @property
    def exchange_count(self) -> int:
        """Number of exchanges recorded."""
        return self._exchange_count
