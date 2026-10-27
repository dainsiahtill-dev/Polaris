"""Scout reconnaissance tool handler (scout_probe).

This KernelOne handler exposes the read-only ``scout_probe`` tool. To keep a
single source of truth, it DELEGATES to the ``roles.scout`` cell's public
service (``ScoutProbeService``) instead of re-implementing reconnaissance.

The cell import is performed LAZILY inside the handler body (never at module
load) so that loading the executor's handler registry does not create a
load-time KernelOne -> Cells dependency or an import cycle. Only the cell's
PUBLIC contract is used.

Authority
---------
The read scope and the tool whitelist are taken from the CALLING executor —
the host that actually holds the committed authority — never from the tool
arguments. The model cannot widen its own scope by passing extra parameters:
the executor drops arguments that the ToolSpec does not declare, and this
handler reads only the declared ``query``/``mode``.
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


def _host_read_roots(self: AgentAccelToolExecutor) -> tuple[str, ...]:
    """The workspace-relative roots the calling executor committed for reading.

    Returns an empty tuple when the executor committed no readable path (an
    explicitly empty or invalid scope). The scope itself is resolved by the
    canonical ``read_scope`` helper, so this handler introduces no second path
    validator.
    """
    from polaris.kernelone.exceptions import PathSecurityError
    from polaris.kernelone.llm.toolkit.executor.read_scope import resolve_committed_read_paths

    token = getattr(self, "_capability_token", {})
    try:
        authorized = resolve_committed_read_paths(self._kernel_fs, token)
    except (OSError, ValueError, PathSecurityError):
        return ()
    if authorized is None:
        # No committed scope at all: the legacy undefined scope, which the
        # caller may read. It is reported as "no scope" and never as a
        # certified authorization.
        return ()
    roots: list[str] = []
    for path in authorized:
        try:
            relative = self._kernel_fs.to_workspace_relative_path(str(path))
        except (OSError, ValueError, PathSecurityError):
            continue
        roots.append(relative)
    return tuple(roots)


def _current_authority(self: AgentAccelToolExecutor) -> Any:
    """Read the host executor's authority as it is RIGHT NOW.

    The host may shrink its committed scope after a report was cached. This
    reader re-reads the executor's live token, whitelist and roots so a
    cached report is validated against the current authority instead of a
    frozen snapshot.
    """
    from polaris.cells.roles.scout.public.service import ScoutReadAuthorityV1

    return ScoutReadAuthorityV1(
        capability_token=dict(getattr(self, "_capability_token", {})),
        allowed_tools=getattr(self, "_allowed_tools", None),
        read_roots=_host_read_roots(self),
        borrowed_executor=self,
    )


def _handle_scout_probe(self: AgentAccelToolExecutor, **kwargs: Any) -> dict[str, Any]:
    """Handle a ``scout_probe`` tool call by delegating to the scout cell.

    Read-only code/symbol reconnaissance. Returns a distilled summary plus
    ranked findings produced by ``ScoutProbeService``. On bad input or failure
    returns ``{"ok": False, "error": ...}`` (fail-soft for one tool dispatch).
    """
    query = kwargs.get("query") or kwargs.get("q")
    query_str = str(query or "").strip()
    if not query_str:
        return {
            "ok": False,
            "error": (
                "Missing required parameter: query. Pass the reconnaissance "
                'question as a string, e.g. {"query": "where is the session '
                'budget enforced?", "mode": "locate"} — describe WHAT to find; '
                "the scout chooses the read tools itself."
            ),
        }

    mode = str(kwargs.get("mode") or "locate").strip() or "locate"
    workspace = str(self.workspace)

    try:
        # Lazy, public-contract-only import: the roles.scout cell is the single
        # implementation of reconnaissance. Imported here (not at module load)
        # to avoid a load-time KernelOne -> Cells dependency / import cycle.
        from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1
        from polaris.cells.roles.scout.public.service import (
            ScoutProbeDeniedError,
            ScoutReadAuthorityV1,
            build_default_scout_service,
        )

        # The committed read scope and the tool whitelist come from the
        # CALLING executor — the host that actually holds the authority.
        # The token is carried verbatim so the canonical read-scope gate
        # applies exactly the host's scope. A missing scope key means "no
        # scope committed" (the legacy undefined scope); an explicitly empty
        # scope stays empty and is refused by the scout cell.
        #
        # The authority is read LIVE (not frozen): the host may shrink its
        # scope after this call started, and a cached report must then be
        # re-validated against the shrunk scope rather than served stale.
        authority = ScoutReadAuthorityV1(
            capability_token=dict(getattr(self, "_capability_token", {})),
            allowed_tools=getattr(self, "_allowed_tools", None),
            read_roots=_host_read_roots(self),
            borrowed_executor=self,
            authority_reader=lambda: _current_authority(self),
            # The host's own per-tool authority for the nested read tools.
            # It comes from the executor's HOST (the role gateway), never from
            # the model's arguments, so the gateway's blacklist and path
            # scope apply to the composite's internal reads too.
            tool_authorizer=getattr(self, "_tool_authorizer", None),
        )

        service = build_default_scout_service(workspace, authority=authority)
        target = ScoutProbeTargetV1(query=query_str, mode=mode)
        try:
            report = _run_coro_sync(service.probe(target))
        finally:
            # The scout cell owns the executor it creates for its reads; the
            # calling executor is owned by the caller and is never closed here.
            service.close()
    except ScoutProbeDeniedError as exc:
        return {"ok": False, "error": f"scout_probe denied by host read authority: {exc}"}
    except ValueError as exc:
        return {"ok": False, "error": f"scout_probe rejected input: {exc}"}
    except Exception as exc:  # noqa: BLE001 - fail-soft boundary for one tool dispatch
        logger.warning("scout_probe failed: %s", exc)
        return {"ok": False, "error": f"scout_probe failed: {type(exc).__name__}: {exc}"}

    findings = [finding.to_dict() for finding in report.findings]
    # Phase-2 A7: pin confirmed localizations so the RoleSignalPlane re-injects
    # them every turn — one-shot tool results get forgotten under retry
    # pressure (run10a: gold compiler.py recalled, then abandoned).
    # The findings are already confined to the host's committed read scope, so
    # the anchor store never receives an unauthorized path.
    try:
        from polaris.kernelone.context.scout_anchor_store import record_scout_anchors

        record_scout_anchors(workspace, query_str, findings)
    except Exception as exc:  # noqa: BLE001 - anchor persistence is fail-soft
        logger.debug("scout anchor persistence skipped: %s", exc)

    return {
        "ok": True,
        "stdout": report.summary,
        "findings": findings,
        "content_hash": report.content_hash,
        "coverage": report.coverage,
        "confidence": report.confidence,
    }
