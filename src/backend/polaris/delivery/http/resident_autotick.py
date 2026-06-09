"""Opt-in background driver that periodically advances the Resident loop.

This is a *delivery-layer scheduler only*: it owns the asyncio cadence and
lifecycle and delegates all domain work to the ``resident.autonomy`` public
contract (``get_resident_service(...).tick``).  It exists to give the otherwise
"never-fires" autonomy loop an unattended ignition source on the server's
primary workspace.

Design notes
------------
- **Disabled by default.**  Enable with ``KERNELONE_RESIDENT_AUTOTICK=1``.  The
  unattended loop triggers ``goal_governor.generate`` (autonomous goal
  proposals), so it must be an explicit ops choice.  On-demand ticking via the
  HTTP ``/v2/resident/tick`` endpoint / UI button is always available regardless
  of this flag.
- ``tick(force=False)`` is a no-op until the resident has been explicitly
  ``start()``-ed (``runtime_state.active``), so the loop is doubly gated.
- ``tick()`` is a synchronous, lock-guarded, *pure-compute* call (no LLM); it is
  dispatched through ``asyncio.to_thread`` so it never blocks the event loop.
- The loop **never** crashes the application: a failed tick is logged and the
  cadence continues.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Final

logger = logging.getLogger(__name__)

ENABLE_ENV: Final = "KERNELONE_RESIDENT_AUTOTICK"
INTERVAL_ENV: Final = "KERNELONE_RESIDENT_AUTOTICK_INTERVAL_SECONDS"
_DEFAULT_ENABLED: Final = False
_DEFAULT_INTERVAL_SECONDS: Final = 600.0
_MIN_INTERVAL_SECONDS: Final = 30.0
_TRUTHY: Final = frozenset({"1", "true", "yes", "on"})


def is_autotick_enabled() -> bool:
    """Return whether the background auto-tick loop is enabled via env."""
    raw = str(os.environ.get(ENABLE_ENV, "")).strip().lower()
    if not raw:
        return _DEFAULT_ENABLED
    return raw in _TRUTHY


def resolve_interval_seconds() -> float:
    """Resolve the tick cadence in seconds, clamped to a sane floor."""
    raw = str(os.environ.get(INTERVAL_ENV, "")).strip()
    if not raw:
        return _DEFAULT_INTERVAL_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "[resident-autotick] invalid %s=%r; falling back to %.0fs",
            INTERVAL_ENV,
            raw,
            _DEFAULT_INTERVAL_SECONDS,
        )
        return _DEFAULT_INTERVAL_SECONDS
    if value < _MIN_INTERVAL_SECONDS:
        logger.warning(
            "[resident-autotick] %s=%.1f below floor; clamping to %.0fs",
            INTERVAL_ENV,
            value,
            _MIN_INTERVAL_SECONDS,
        )
        return _MIN_INTERVAL_SECONDS
    return value


async def run_autotick_once(workspace: str) -> dict[str, Any] | None:
    """Advance the resident loop a single tick. Never raises.

    Returns the resident status payload on success, or ``None`` if the tick
    failed (the failure is logged, not propagated).
    """

    def _tick() -> dict[str, Any]:
        # Imported lazily to keep this delivery module free of cell-internal
        # import cost at process start and to avoid import cycles.
        from polaris.cells.resident.autonomy.public.service import get_resident_service

        return get_resident_service(workspace).tick(force=False)

    try:
        status = await asyncio.to_thread(_tick)
    except Exception:
        logger.exception("[resident-autotick] tick failed for workspace=%s", workspace)
        return None

    runtime = status.get("runtime") if isinstance(status, dict) else None
    if isinstance(runtime, dict) and runtime.get("active"):
        logger.info(
            "[resident-autotick] ticked workspace=%s summary=%s",
            workspace,
            runtime.get("last_summary"),
        )
    return status


async def _loop(workspace: str, interval_seconds: float) -> None:
    logger.info(
        "[resident-autotick] enabled for workspace=%s interval=%.0fs",
        workspace,
        interval_seconds,
    )
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            await run_autotick_once(workspace)
    except asyncio.CancelledError:
        logger.info("[resident-autotick] stopped for workspace=%s", workspace)
        raise


def maybe_start_resident_autotick(workspace: str) -> asyncio.Task[None] | None:
    """Start the background auto-tick loop when enabled.

    Args:
        workspace: The server's primary workspace to drive.

    Returns:
        The scheduled :class:`asyncio.Task`, or ``None`` when disabled or when no
        workspace is configured.
    """
    normalized = str(workspace or "").strip()
    if not normalized:
        return None
    if not is_autotick_enabled():
        return None
    interval = resolve_interval_seconds()
    return asyncio.create_task(_loop(normalized, interval), name="resident-autotick")
