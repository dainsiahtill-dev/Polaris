"""Token estimation and budget management service.

.. deprecated::
    This module is a backward-compatibility re-export.
    The canonical location is now ``polaris.domain.services.token_service``.
    All new code should import from ``polaris.domain.services`` instead.

Infrastructure modules that need TokenService should import from here to avoid
breaking existing call sites. New code should use ``polaris.domain.services``.
"""

from polaris.domain.services.token_service import (
    BudgetStatus,
    TokenEstimate,
    TokenService,
    estimate_tokens,
    get_token_service,
    reset_token_service,
)

__all__ = [
    "BudgetStatus",
    "TokenEstimate",
    "TokenService",
    "estimate_tokens",
    "get_token_service",
    "reset_token_service",
]
