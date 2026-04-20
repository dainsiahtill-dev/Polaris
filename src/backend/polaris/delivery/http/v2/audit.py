"""Compatibility facade for migrated Polaris audit router."""

from __future__ import annotations

from polaris.delivery.http.audit_router import router

__all__ = ["router"]
