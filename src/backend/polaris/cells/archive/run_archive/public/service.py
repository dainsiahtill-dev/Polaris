"""Public service exports for `archive.run_archive` cell."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING, Any

# Re-export index types so sibling cells in the archive sub-domain can consume
# them through the public boundary instead of crossing into internal/.
from polaris.cells.archive.run_archive.internal.history_manifest_repository import (
    FactoryIndexEntry,
    HistoryManifestRepository,
    TaskIndexEntry,
)
from polaris.cells.archive.run_archive.public.contracts import (
    ArchiveManifestV1,
    ArchiveRunCommandV1,
    GetArchiveManifestQueryV1,
    HistoryRunsResultV1,
    ListHistoryRunsQueryV1,
    RunArchivedEventV1,
    RunArchiveError,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine

logger = logging.getLogger(__name__)


def _run_background(coro: Coroutine[Any, Any, None]) -> None:
    """Run a coroutine in background, handling both running loop and no-loop contexts."""
    try:
        loop = asyncio.get_running_loop()
        _ = loop.create_task(coro)  # noqa: RUF006
        return
    except RuntimeError:
        pass

    def _runner() -> None:
        try:
            asyncio.run(coro)
        except OSError:
            logger.warning("Background run archive failed", exc_info=True)

    threading.Thread(target=_runner, daemon=True).start()


def create_run_archive_service(workspace: str):
    from polaris.cells.archive.run_archive.internal.history_archive_service import (
        HistoryArchiveService,
    )

    return HistoryArchiveService(workspace)


def archive_run(
    workspace: str,
    run_id: str,
    *,
    reason: str = "completed",
    status: str = "",
) -> dict[str, Any]:
    service = create_run_archive_service(workspace)
    manifest = service.archive_run(run_id=run_id, reason=reason, status=status)
    return manifest.to_dict()


def trigger_run_archive(
    workspace: str,
    run_id: str,
    *,
    reason: str = "completed",
    status: str = "",
) -> None:
    async def _do_archive() -> None:
        archive_run(workspace=workspace, run_id=run_id, reason=reason, status=status)

    _run_background(_do_archive())


def list_history_runs(
    workspace: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    service = create_run_archive_service(workspace)
    return [item.to_dict() for item in service.list_history_runs(limit=limit, offset=offset)]


def get_run_manifest(workspace: str, run_id: str) -> dict[str, Any] | None:
    service = create_run_archive_service(workspace)
    manifest = service.get_manifest("run", run_id)
    return manifest.to_dict() if manifest else None


def get_run_events(workspace: str, run_id: str) -> list[dict[str, Any]]:
    service = create_run_archive_service(workspace)
    return service.get_run_events(run_id)


def trigger_factory_archive(workspace: str, run_id: str, *, reason: str) -> None:
    """Public archive trigger for factory pipeline.

    Downstream cells must call this public port instead of importing
    ``archive.run_archive.internal`` modules directly.
    """
    from polaris.cells.archive.factory_archive.public.service import trigger_factory_archive as _trigger

    _trigger(
        workspace=workspace,
        factory_run_id=run_id,
        reason=str(reason or "").strip() or "completed",
    )


def create_archive_sink(bus: Any) -> Any:
    """Create an ArchiveSink instance wired to the given MessageBus.

    This is the canonical public factory for bootstrap layers that need
    to register UEP v2.0 consumers without crossing into internal/.
    """
    from polaris.cells.archive.run_archive.internal.archive_sink import ArchiveSink

    return ArchiveSink(bus)


__all__ = [
    "ArchiveManifestV1",
    "ArchiveRunCommandV1",
    "FactoryIndexEntry",
    "GetArchiveManifestQueryV1",
    "HistoryManifestRepository",
    "HistoryRunsResultV1",
    "ListHistoryRunsQueryV1",
    "RunArchiveError",
    "RunArchivedEventV1",
    "TaskIndexEntry",
    "archive_run",
    "create_archive_sink",
    "create_run_archive_service",
    "get_run_events",
    "get_run_manifest",
    "list_history_runs",
    "trigger_factory_archive",
    "trigger_run_archive",
]
