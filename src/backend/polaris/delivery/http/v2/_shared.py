"""Shared v2 router helpers.

This module keeps v2 routers on the same auth/state/error contracts used by
the primary router package without duplicating implementation details.
"""

from polaris.delivery.http.routers._shared import (
    StructuredHTTPException,
    get_state,
    require_auth,
)

__all__ = ["StructuredHTTPException", "get_state", "require_auth"]
