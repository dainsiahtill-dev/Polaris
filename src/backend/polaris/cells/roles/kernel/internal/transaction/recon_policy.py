"""Reconnaissance requirement policy for TransactionKernel turns."""

from __future__ import annotations

import os
from typing import Any

_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}


def resolve_recon_required(role: str, profile: Any) -> bool:
    """Return whether a role turn must execute a successful recon tool first.

    This is a transaction-layer policy. It is intentionally independent of the
    retired TurnEngine path so RoleExecutionKernel and TransactionKernel share
    the same read-side landing invariant.
    """
    context_policy = getattr(profile, "context_policy", None)
    if bool(getattr(context_policy, "recon_mode", False)):
        return True

    env_value = os.getenv("KERNELONE_SCOUT_RECON_MODE", "").strip().lower()
    role_id = str(role or "").strip().lower()
    return env_value in _TRUTHY_ENV_VALUES and role_id == "scout"


__all__ = ["resolve_recon_required"]
