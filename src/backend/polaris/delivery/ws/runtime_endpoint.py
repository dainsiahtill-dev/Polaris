"""Unified runtime websocket endpoint (Facade).

This file is a facade that imports from the refactored endpoint modules.
All implementation has been moved to polaris/delivery/ws/endpoints/ for better
maintainability and module size control.

See endpoints/ subdirectory for actual implementation:
- models.py: Type definitions and constants
- helpers.py: Utility functions
- stream.py: Stream sending functions
- protocol.py: v2 protocol handlers
- websocket_core.py: Core WebSocket endpoint
- websocket_loop.py: Main loop implementation

v2 Protocol (canonical):
- SUBSCRIBE with protocol=runtime.v2 for JetStream consumer mode
- ACK for cursor-based delivery confirmation
- PING/PONG for heartbeat
- RESYNC_REQUIRED for reconnection/resync
- strategy_receipt field for canonical context propagation

Legacy v1 Protocol (DEPRECATED — will be removed in v2.0):
- v1 clients send SUBSCRIBE/STATUS/SNAPSHOT without protocol field
- Legacy channel paths (pm_llm, director_llm) are deprecated
- Migrate to v2 protocol for all new integrations
"""

from __future__ import annotations

from fastapi import APIRouter

# Import the main WebSocket endpoint from refactored modules
from polaris.delivery.ws.endpoints.websocket_core import runtime_websocket

# Create router with the endpoint
router = APIRouter(prefix="/ws", tags=["Runtime WebSocket"])
router.add_api_websocket_route("/runtime", runtime_websocket)

__all__ = ["router"]
