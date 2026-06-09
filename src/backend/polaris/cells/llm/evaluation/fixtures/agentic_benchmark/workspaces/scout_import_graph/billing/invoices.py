"""Billing invoice generation.

Consumes the shared ``SessionContext`` DTO to attribute invoices to a tenant.
"""

from __future__ import annotations

from core.contracts import SessionContext


def issue_invoice(session: SessionContext, amount_cents: int) -> dict[str, object]:
    """Issue an invoice attributed to the session's tenant via the shared DTO."""
    return {
        "tenant": session.tenant,
        "user_id": session.user_id,
        "amount_cents": amount_cents,
    }
