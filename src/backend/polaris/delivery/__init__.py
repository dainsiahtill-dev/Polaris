"""Delivery layer for Polaris.

Role: HTTP/WS/CLI transport entry points — thin, stateless adapters.
Delivery converts external protocols (HTTP, WebSocket, CLI) into calls
to Cells via their public contracts. It carries no business logic.

Call chain (correct pattern):
    delivery -> application -> domain/kernelone/cells

Current branch status (migration):
    delivery currently calls Cells directly in many paths; this is
    acceptable during migration as Cells are the primary capability
    carriers. The application layer provides a facade for cross-cutting
    concerns but does not contain new business logic in this branch.

Sub-packages:
    cli/   - CLI entry points (pm, audit, factory, director, loop-director)
    http/  - FastAPI routers, middleware, and app factory
    ws/    - WebSocket real-time event channels

Architecture constraints:
    - delivery must NOT import infrastructure adapters directly
    - delivery must NOT implement business orchestration (belongs in Cells)
    - delivery must NOT hold application state (stateless adapters only)
    - All I/O must be UTF-8
"""

from __future__ import annotations

from . import cli, http, ws

__all__ = [
    "cli",
    "http",
    "ws",
]
