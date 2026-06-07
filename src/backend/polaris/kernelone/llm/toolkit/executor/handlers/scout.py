"""Scout reconnaissance tool handler (scout_probe).

Exposes the read-only ``roles.scout`` reconnaissance cell AS a canonical tool so
the role kernel's executor can dispatch ``scout_probe`` when a role's LLM calls
it. The probe is side-effect-free: it only runs ``read``-classified tools through
the same ``AgentAccelToolExecutor`` the roles use.

Circular-import note: the scout cell imports the executor
(``read_tool_adapter.py``), so this module must NOT import the scout cell at
module load time. All scout imports are LAZY, inside the handler body.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from polaris.cells.roles.scout.public.contracts import ScoutReportV1
    from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor


def register_handlers() -> dict[str, Any]:
    """Return a dict of scout_* handler names to handler functions."""
    return {
        "scout_probe": _handle_scout_probe,
    }


def _run_probe_sync(workspace: str, query: str, mode: str) -> ScoutReportV1:
    """Run the async scout probe synchronously, with or without a running loop.

    ``AgentAccelToolExecutor.execute`` is sync and may be called either from a
    plain sync context (no event loop) or from within a running loop (e.g. a
    role turn driven by ``asyncio``). Handle both: if a loop is already running,
    execute the coroutine on a fresh loop in a worker thread; otherwise use
    ``asyncio.run``.
    """
    # Lazy imports: the scout cell imports the executor, so importing it at
    # module top level would create a circular import.
    from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1
    from polaris.cells.roles.scout.public.service import build_default_scout_service

    target = ScoutProbeTargetV1(query=query, mode=mode)
    service = build_default_scout_service(workspace)

    async def _probe() -> ScoutReportV1:
        return await service.probe(target)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop in this thread — safe to drive a fresh one.
        return asyncio.run(_probe())

    # A loop is already running on this thread; run the coroutine on its own
    # loop inside a dedicated worker thread to avoid re-entrancy errors.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: asyncio.run(_probe()))
        return future.result()


def _handle_scout_probe(self: AgentAccelToolExecutor, **kwargs: Any) -> dict[str, Any]:
    """Handle scout_probe tool call.

    Read-only code/symbol reconnaissance. Returns a distilled summary plus ranked
    findings. On bad input returns ``{"ok": False, "error": ...}``.
    """
    query = kwargs.get("query") or kwargs.get("q")
    query_str = str(query or "").strip()
    if not query_str:
        return {"ok": False, "error": "Missing required parameter: query"}

    mode = str(kwargs.get("mode") or "locate").strip() or "locate"

    workspace = str(self.workspace)

    try:
        report = _run_probe_sync(workspace, query_str, mode)
    except ValueError as exc:
        # e.g. invalid mode or empty query rejected by the contract.
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
    }
