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
from typing import TYPE_CHECKING, Final
from uuid import uuid4

if TYPE_CHECKING:
    from polaris.cells.resident.autonomy.public.contracts import ResidentAutonomyResultV1

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


def _sink_cycle_fact_event(result: ResidentAutonomyResultV1) -> None:
    """Best-effort: publish a completed cycle to the canonical fact stream.

    Only ``completed`` cycles (resident active, loop advanced) are published, to
    avoid streaming no-op ``skipped_inactive`` ticks.  Failure is swallowed: the
    fact-stream sink is observability, not a cycle dependency.
    """
    if result.status != "completed":
        return
    try:
        from polaris.cells.events.fact_stream.public.contracts import AppendFactEventCommandV1
        from polaris.cells.events.fact_stream.public.service import append_fact_event

        append_fact_event(
            AppendFactEventCommandV1(
                workspace=result.workspace,
                stream="resident.cycle.events",
                event_type="resident.cycle.completed",
                source="resident.autotick",
                run_id=result.cycle_id,
                payload={
                    "cycle_id": result.cycle_id,
                    "status": result.status,
                    "actions": list(result.actions),
                    "metrics": dict(result.metrics),
                },
            )
        )
    except Exception:  # noqa: BLE001 - best-effort sink must never break the cycle
        logger.debug(
            "[resident-autotick] fact-stream sink skipped for cycle=%s",
            result.cycle_id,
            exc_info=True,
        )


async def run_autotick_once(workspace: str) -> ResidentAutonomyResultV1 | None:
    """Advance the resident loop a single cycle via the public contract. Never raises.

    Drives the declared ``RunResidentCycleCommandV1`` → ``ResidentAutonomyResultV1``
    contract (no ``force`` in context, so the cycle no-ops unless the resident is
    active) and publishes completed cycles to the canonical fact stream.  Returns
    the result on success, or ``None`` if the cycle failed (logged, not propagated).
    """

    def _cycle() -> ResidentAutonomyResultV1:
        # Imported lazily to keep this delivery module free of cell import cost
        # at process start and to avoid import cycles.
        from polaris.cells.resident.autonomy.public.contracts import RunResidentCycleCommandV1
        from polaris.cells.resident.autonomy.public.service import run_resident_cycle

        command = RunResidentCycleCommandV1(
            workspace=workspace,
            cycle_id=f"autotick-{uuid4().hex[:12]}",
            goal="scheduled_autonomy_cycle",
        )
        result = run_resident_cycle(command)
        _sink_cycle_fact_event(result)
        return result

    try:
        result = await asyncio.to_thread(_cycle)
    except Exception:
        logger.exception("[resident-autotick] cycle failed for workspace=%s", workspace)
        return None

    logger.info(
        "[resident-autotick] cycle workspace=%s status=%s actions=%s",
        workspace,
        result.status,
        result.actions,
    )
    return result


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
