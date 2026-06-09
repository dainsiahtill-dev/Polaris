"""Application entry point wiring the service mesh together.

This is the top-level composition root. It calls into ``api_service.handlers``
which in turn fans out to the auth and storage layers, all sharing the single
``SessionContext`` DTO defined in ``core.contracts``.
"""

from __future__ import annotations

from api_service.handlers import delete_resource, login
from billing.invoices import issue_invoice


def main() -> None:
    """Run a representative end-to-end flow over the service mesh."""
    session = login("alice", secret="hunter2")
    print(delete_resource(session, resource_id="res-1"))
    print(issue_invoice(session, amount_cents=4200))


if __name__ == "__main__":
    main()
