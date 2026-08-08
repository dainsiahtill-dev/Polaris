"""Process composition for unattended project-completion convergence."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from polaris.cells.factory.verification_guard.public.contracts import (
    ProjectCompletionDiagnosticsV1,
    QueryProjectCompletionDiagnosticsV1,
)
from polaris.cells.factory.verification_guard.public.service import (
    query_project_completion_diagnostics,
)
from polaris.cells.orchestration.workflow_orchestration.internal.project_completion_supervisor import (
    EventDrivenProjectCompletionSupervisorV1,
)
from polaris.cells.orchestration.workflow_orchestration.public.project_completion import (
    AdvanceProjectCompletionCommandV1,
    ProjectCompletionIdentityV1,
    advance_project_completion,
)
from polaris.cells.orchestration.workflow_orchestration.public.project_completion_bootstrap import (
    bind_project_completion_convergence_runtime,
    bind_project_completion_supervisor,
    clear_project_completion_convergence_runtime,
    clear_project_completion_supervisor,
)
from polaris.cells.orchestration.workflow_runtime.internal.project_completion_cursor import (
    SqliteProjectCompletionCursorV1,
)
from polaris.cells.orchestration.workflow_runtime.public.model_ceiling import (
    ModelCeilingTerminalResultV1,
)
from polaris.cells.orchestration.workflow_runtime.public.project_completion_cursor import (
    ProjectCompletionCursorPortV1,
)
from polaris.cells.runtime.projection.public.contracts import (
    ProjectOutcomeAuthorityBindingV1,
    ProjectOutcomeAuthorityQueryV1,
)
from polaris.cells.runtime.projection.public.service import query_authoritative_project_outcome
from polaris.cells.runtime.task_market.public.service import (
    TaskMarketService,
    get_task_market_service,
    get_task_market_work_event,
)
from polaris.infrastructure.db.repositories.workflow_runtime_store import SqliteRuntimeStore
from polaris.kernelone.storage import resolve_runtime_path

from .project_completion_task_market_action_owner import (
    TaskMarketProjectCompletionActionOwnerV1,
)


class _ProjectOutcomePort:
    async def query_project_completion_outcome(
        self,
        identity: ProjectCompletionIdentityV1,
    ) -> ProjectOutcomeAuthorityBindingV1:
        return await query_authoritative_project_outcome(
            ProjectOutcomeAuthorityQueryV1(
                workspace=identity.workspace,
                project_id=identity.project_id,
                run_id=identity.run_id,
                completion_contract_hash=identity.completion_contract_hash,
            )
        )


class _ProjectDiagnosticsPort:
    async def query_project_completion_diagnostics(
        self,
        identity: ProjectCompletionIdentityV1,
    ) -> ProjectCompletionDiagnosticsV1:
        return await asyncio.to_thread(
            query_project_completion_diagnostics,
            QueryProjectCompletionDiagnosticsV1(
                workspace=identity.workspace,
                project_id=identity.project_id,
                run_id=identity.run_id,
                completion_contract_hash=identity.completion_contract_hash,
            ),
        )


class _ProjectModelCeilingPort:
    """Fail-closed bridge until an owner-qualified result is published.

    Convergence never derives model ceiling from local budgets.  Production
    composition may replace this port with a durable workflow_runtime owner
    projection; absence means park with the diagnostic's next action intact.
    """

    async def query_project_completion_model_ceiling(
        self,
        identity: ProjectCompletionIdentityV1,
        diagnostic_id: str,
    ) -> ModelCeilingTerminalResultV1 | None:
        del identity, diagnostic_id
        return None


async def _recover_project_completion_commands(
    cursor: ProjectCompletionCursorPortV1,
) -> tuple[AdvanceProjectCompletionCommandV1, ...]:
    registrations = await cursor.list_resumable_cursors()
    return tuple(
        AdvanceProjectCompletionCommandV1(
            identity=ProjectCompletionIdentityV1(
                workspace=registration.identity.workspace,
                project_id=registration.identity.project_id,
                run_id=registration.identity.run_id,
                completion_contract_hash=registration.identity.completion_contract_hash,
            ),
            max_actions=registration.limits.max_actions,
            max_dispatch_attempts=registration.limits.max_dispatch_attempts,
            max_no_progress_observations=registration.limits.max_no_progress_observations,
            dispatch_lease_seconds=registration.limits.dispatch_lease_seconds,
        )
        for registration in registrations
    )


@dataclass(slots=True)
class ProjectCompletionConvergenceRuntimeV1:
    """Lifespan-owned worker plus TaskMarket event listeners."""

    workspace: str
    supervisor: EventDrivenProjectCompletionSupervisorV1
    task_market: TaskMarketService
    wake_tasks: tuple[asyncio.Task[None], ...] = ()

    async def start(self) -> None:
        # Replay the transactional outbox before cursor recovery.  The local
        # threading events below are latency hints only; durable TaskMarket
        # state plus the workflow cursor remain restart authority.
        await asyncio.to_thread(self.task_market.relay_outbox_messages, self.workspace)
        await self.supervisor.start()
        self.wake_tasks = tuple(
            asyncio.create_task(
                self._watch_task_market(role),
                name=f"project_completion_task_market_wake:{role}",
            )
            for role in ("chief_engineer", "director", "qa")
        )

    async def stop(self) -> None:
        for role in ("chief_engineer", "director", "qa"):
            get_task_market_work_event(self.workspace, role).set()
        for task in self.wake_tasks:
            task.cancel()
        for task in self.wake_tasks:
            with suppress(asyncio.CancelledError):
                await task
        self.wake_tasks = ()
        await self.supervisor.stop()
        clear_project_completion_supervisor(self.supervisor)
        clear_project_completion_convergence_runtime()

    async def _watch_task_market(self, role: str) -> None:
        event = get_task_market_work_event(self.workspace, role)
        while True:
            await asyncio.to_thread(event.wait)
            # Clear immediately after observing the generation.  Clearing
            # before ``wait`` can discard a commit that lands between worker
            # startup (or the preceding convergence read) and the next wait.
            # Commits during ``wake`` remain set and trigger another pass.
            event.clear()
            await asyncio.to_thread(self.task_market.relay_outbox_messages, self.workspace)
            await self.supervisor.wake()


def configure_project_completion_convergence_runtime(
    workspace: str,
) -> ProjectCompletionConvergenceRuntimeV1:
    """Build production ports and bind one workspace-scoped runtime."""

    canonical_workspace = str(Path(workspace).expanduser().resolve())
    database_path = resolve_runtime_path(
        canonical_workspace,
        "runtime/state/project_completion/convergence.sqlite3",
    )
    cursor = SqliteProjectCompletionCursorV1(
        SqliteRuntimeStore(database_path, workspace=canonical_workspace)
    )
    task_market = get_task_market_service()
    bind_project_completion_convergence_runtime(
        cursor=cursor,
        outcome_port=_ProjectOutcomePort(),
        diagnostics_port=_ProjectDiagnosticsPort(),
        action_port=TaskMarketProjectCompletionActionOwnerV1(task_market),
        model_ceiling_port=_ProjectModelCeilingPort(),
    )

    supervisor = EventDrivenProjectCompletionSupervisorV1(
        advance=advance_project_completion,
        recover=lambda: _recover_project_completion_commands(cursor),
    )
    bind_project_completion_supervisor(supervisor)
    return ProjectCompletionConvergenceRuntimeV1(
        workspace=canonical_workspace,
        supervisor=supervisor,
        task_market=task_market,
    )


__all__ = [
    "ProjectCompletionConvergenceRuntimeV1",
    "configure_project_completion_convergence_runtime",
]
