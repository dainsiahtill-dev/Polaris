"""KernelOne event contracts and type definitions.

This module provides contracts (Protocols) for the event subsystem.
Event type string constants are defined in constants.py for single source of truth.

Architecture:
    - constants.py: Event type string constants (single source of truth)
    - contracts.py: Protocol definitions for event interfaces
    - message_bus.py: MessageType enum (internal bus protocol)
    - typed/schemas.py: TypedEvent discriminated union

CRITICAL: All event type strings should be imported from constants.py.
Do NOT define new event type string constants in this file.
"""

from __future__ import annotations
