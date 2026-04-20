"""KernelOne contracts package.

Provides technical contracts (shared types/interfaces) that cross-cut
multiple KernelOne subsystems. Anchors the ACGA 2.0 "contract-first" principle.

Sub-packages:
    technical/  - Master types: Envelope, Result, Effect, Lock, Scheduler,
                  Stream, Health, TraceContext
"""

from __future__ import annotations

from . import technical

__all__ = ["technical"]
