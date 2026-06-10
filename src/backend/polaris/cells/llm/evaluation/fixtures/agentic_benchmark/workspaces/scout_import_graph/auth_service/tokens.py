"""Token minting and validation for the auth service.

Imports the shared ``SessionContext`` DTO from ``core.contracts`` and builds
fresh session contexts after a successful credential exchange.
"""

from __future__ import annotations

import hashlib

from core.contracts import SessionContext


def mint_token(user_id: str, secret: str) -> str:
    """Derive a deterministic opaque token from ``user_id`` and ``secret``."""
    digest = hashlib.sha256(f"{user_id}:{secret}".encode()).hexdigest()
    return f"tok_{digest[:16]}"


def build_session(
    user_id: str,
    secret: str,
    scopes: tuple[str, ...],
) -> SessionContext:
    """Mint a token and assemble the shared ``SessionContext`` DTO."""
    token = mint_token(user_id, secret)
    return SessionContext(user_id=user_id, token=token, scopes=scopes)
