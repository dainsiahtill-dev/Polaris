"""DEPRECATED legacy auth module -- DO NOT USE.

This module predates the shared ``core.contracts.SessionContext`` DTO. It
defines its OWN ``build_session`` function that returns a plain ``dict`` and
does NOT import or use the shared DTO. It is kept only for historical
reference and is not imported anywhere in the live service mesh.

RED HERRING: there is a same-named ``build_session`` here and in
``auth_service/tokens.py``. The canonical, DTO-producing definition lives in
``auth_service/tokens.py``, NOT here.
"""

from __future__ import annotations


def build_session(user_id: str, token: str) -> dict[str, str]:
    """Legacy session builder returning a bare dict (no shared DTO)."""
    return {"user_id": user_id, "token": token}


def mint_token(user_id: str) -> str:
    """Legacy, insecure token derivation kept for reference only."""
    return f"legacy-{user_id}"
