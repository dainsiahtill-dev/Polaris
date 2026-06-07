"""Scout reconnaissance tool handler (scout_probe).

This KernelOne handler exposes the read-only ``scout_probe`` tool. To keep a
single source of truth, it DELEGATES to the ``roles.scout`` cell's public
service (``ScoutProbeService``) instead of re-implementing reconnaissance.

The cell import is performed LAZILY inside the handler body (never at module
load) so that loading the executor's handler registry does not create a
load-time KernelOne -> Cells dependency or an import cycle. Only the cell's
PUBLIC contract is used.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor


def register_handlers() -> dict[str, Any]:
    """Return a dict of scout_* handler names to handler functions."""
    return {
        "scout_probe": _handle_scout_probe,
    }


def _run_coro_sync(coro: Any) -> Any:
    """Run an async coroutine to completion from a synchronous handler.

    Works whether or not an event loop is already running in this thread: if a
    loop is already running, the coroutine is executed in a dedicated worker
    thread with its own loop to avoid re-entrancy errors.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _handle_scout_probe(self: AgentAccelToolExecutor, **kwargs: Any) -> dict[str, Any]:
    """Handle a ``scout_probe`` tool call by delegating to the scout cell.

    Read-only code/symbol reconnaissance. Returns a distilled summary plus
    ranked findings produced by ``ScoutProbeService``. On bad input or failure
    returns ``{"ok": False, "error": ...}`` (fail-soft for one tool dispatch).
    """
    query = kwargs.get("query") or kwargs.get("q")
    query_str = str(query or "").strip()
    if not query_str:
        return {"ok": False, "error": "Missing required parameter: query"}

    mode = str(kwargs.get("mode") or "locate").strip() or "locate"
    workspace = str(self.workspace)

    try:
        # Lazy, public-contract-only import: the roles.scout cell is the single
        # implementation of reconnaissance. Imported here (not at module load)
        # to avoid a load-time KernelOne -> Cells dependency / import cycle.
        from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1
        from polaris.cells.roles.scout.public.service import build_default_scout_service

        service = build_default_scout_service(workspace)
        target = ScoutProbeTargetV1(query=query_str, mode=mode)
        report = _run_coro_sync(service.probe(target))
    except ValueError as exc:
        return {"ok": False, "error": f"scout_probe rejected input: {exc}"}
    except Exception as exc:  # noqa: BLE001 - fail-soft boundary for one tool dispatch
        logger.warning("scout_probe failed: %s", exc)
        return {"ok": False, "error": f"scout_probe failed: {type(exc).__name__}: {exc}"}

    return {
        "ok": True,
        "stdout": report.summary,
        "findings": [finding.to_dict() for finding in report.findings],
        "content_hash": report.content_hash,
        "coverage": report.coverage,
        "confidence": report.confidence,
    }
