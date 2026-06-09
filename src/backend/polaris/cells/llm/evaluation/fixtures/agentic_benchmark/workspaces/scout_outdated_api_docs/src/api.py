"""DataSync client — current (v2) implementation.

This is the authoritative source of truth for the client's behaviour.
docs/API.md describes the older v1 contract and is now out of date.
"""

from __future__ import annotations

# Current default request timeout, in seconds. The v2 rewrite lowered this
# from the old v1 value because the slow batch endpoints were removed.
DEFAULT_TIMEOUT = 10

# Number of automatic retries on 5xx responses.
DEFAULT_RETRIES = 3


class DataSyncTimeout(Exception):
    """Raised when a request exceeds the configured timeout."""


class DataSyncClient:
    """Client for the DataSync service (v2).

    As of v2 the constructor only requires TWO parameters: ``base_url`` and
    ``api_key``. The ``region``, ``tenant_id`` and ``signing_secret`` arguments
    described in the old docs were removed when request signing moved
    server-side.
    """

    def __init__(self, base_url: str, api_key: str, timeout: int = DEFAULT_TIMEOUT) -> None:
        # Only base_url and api_key are required; timeout defaults to 10 seconds.
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout

    def request(self, path: str) -> str:
        """Issue a request to ``path`` using the configured timeout."""
        return f"GET {self.base_url}{path} (timeout={self.timeout}s)"
