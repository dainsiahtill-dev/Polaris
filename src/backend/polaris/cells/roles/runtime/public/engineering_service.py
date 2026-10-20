"""Workspace-bound entry for the engineering loop and its single session store."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore, WorkspaceBusyError
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    PermissionScope,
    RoleRunCommand,
    RoleRunResult,
    RunLimits,
    ToolObservation,
)
from polaris.kernelone.llm.engine.client import ProviderConfig, ResilientLLMClient
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.engine.resilience import RetryConfig, TimeoutConfig
from polaris.kernelone.process.process_tree import ProcessTreeDrainError

_TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})


class _NativeEngineProvider(AIExecutor):
    """Name the existing native executor for the resilient client's protocol."""

    name = "kernelone_native"


def _native_client(workspace: Path, limits: RunLimits) -> ResilientLLMClient:
    # K1 owns model selection and provider timeouts. This one-provider wrapper
    # adds no fallback or retries and cannot shorten a provider's configured
    # deadline. The engineering loop also watches the actual run deadline.
    remaining = max(0.001, limits.deadline - time.time()) if limits.deadline is not None else float("inf")
    return ResilientLLMClient(
        [(_NativeEngineProvider(workspace=str(workspace)), ProviderConfig(provider_name="kernelone_native"))],
        default_retry_config=RetryConfig(max_attempts=1),
        default_timeout_config=TimeoutConfig(request_timeout=remaining, total_timeout=remaining),
    )


class EngineeringRunService:
    """Own live handles; persistent facts belong only to EngineeringSessionStore.

    ``permission_resolver`` is host policy, never model input. Resume requires
    it so a saved scope cannot silently outlive current platform permissions.
    """

    engine_version = "engineering_v2"

    def __init__(
        self,
        workspace: Path,
        *,
        client: ResilientLLMClient | None = None,
        permission_resolver: Callable[[dict[str, Any]], PermissionScope] | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.store = EngineeringSessionStore(self.workspace)
        self._client = client
        self._permission_resolver = permission_resolver
        self._tasks: dict[str, asyncio.Task[RoleRunResult]] = {}
        self._cancellations: dict[str, asyncio.Event] = {}
        self._retained_locks: dict[str, AbstractContextManager[None]] = {}

    def _assert_workspace(self, workspace: Path) -> None:
        if Path(workspace).resolve() != self.workspace:
            raise PermissionError("engineering service workspace mismatch")

    def observe(self, session_id: str, *, workspace: Path) -> dict[str, Any]:
        self._assert_workspace(workspace)
        return self.store.load_run(session_id, workspace=self.workspace)

    async def start(
        self,
        command: RoleRunCommand,
        *,
        scope: PermissionScope,
        limits: RunLimits,
        request_key: str | None = None,
    ) -> str:
        return await self._start(command, scope=scope, limits=limits, request_key=request_key, cancel=asyncio.Event())

    async def _start(
        self,
        command: RoleRunCommand,
        *,
        scope: PermissionScope,
        limits: RunLimits,
        request_key: str | None,
        cancel: asyncio.Event,
    ) -> str:
        self._assert_workspace(command.workspace)
        session_id = self.store.create_run(
            command, scope=scope, limits=limits, engine_version=self.engine_version, request_key=request_key
        )
        row = self.observe(session_id, workspace=self.workspace)
        if session_id in self._tasks or row["status"] in _TERMINAL or row["status"] == "running":
            return session_id
        command = self._command(row)
        lock = self.store.acquire_workspace(self.workspace)
        try:
            lock.__enter__()
        except WorkspaceBusyError:
            self.store.append_event(
                session_id,
                kind="admission_blocked",
                role=command.role,
                payload={"reason": "workspace_busy", "tools_dispatched": False},
            )
            raise
        try:
            self.store.update_run(session_id, status="running")
            self.store.append_event(
                session_id,
                kind="run_started",
                role=command.role,
                payload={"engine_version": self.engine_version},
            )
            self._cancellations[session_id] = cancel
            self._tasks[session_id] = asyncio.create_task(
                self._drive(command, scope=scope, limits=limits, cancel=cancel, lock=lock),
                name=f"engineering:{session_id}",
            )
        except BaseException:
            self._cancellations.pop(session_id, None)
            lock.__exit__(None, None, None)
            raise
        return session_id

    async def run_role(
        self, command: RoleRunCommand, *, scope: PermissionScope, limits: RunLimits, cancel: asyncio.Event
    ) -> RoleRunResult:
        session_id = await self._start(command, scope=scope, limits=limits, request_key=None, cancel=cancel)
        task = self._tasks.get(session_id)
        if task is None:
            row = self.observe(session_id, workspace=self.workspace)
            if row["status"] not in _TERMINAL:
                raise WorkspaceBusyError("engineering run is owned by another live execution")
            return self._result(row)

        async def forward_cancel() -> None:
            await cancel.wait()
            owned_cancel = self._cancellations.get(session_id)
            if owned_cancel is not None:
                owned_cancel.set()

        # A second caller can join an existing run. Its cancellation signals the
        # same owner; it never interrupts that owner's physical cleanup await.
        forwarding = (
            asyncio.create_task(forward_cancel()) if self._cancellations.get(session_id) is not cancel else None
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await self.cancel(session_id, workspace=self.workspace)
            raise
        finally:
            if forwarding is not None:
                forwarding.cancel()
                await asyncio.gather(forwarding, return_exceptions=True)

    async def cancel(self, session_id: str, *, workspace: Path) -> None:
        row = self.observe(session_id, workspace=workspace)
        task = self._tasks.get(session_id)
        if task is None:
            if row["status"] in _TERMINAL:
                return
            with self.store.acquire_workspace(self.workspace):
                pending = self.store.pending_tool_calls(session_id)
                self.store.update_run(
                    session_id,
                    status="interrupted" if pending or row["status"] == "running" else "cancelled",
                    verification="unknown" if pending else row["verification"],
                    text="No local execution owner; retained tool history requires reconciliation."
                    if pending
                    else row["text"],
                )
            return
        self._cancellations[session_id].set()
        # The loop owns interruption and awaits its physical worker. Repeated
        # cancellation only sets the same signal, so cleanup cannot be cancelled.
        await asyncio.gather(task, return_exceptions=True)

    async def resume(self, session_id: str, *, workspace: Path) -> str:
        row = self.observe(session_id, workspace=workspace)
        if row["engine_version"] != self.engine_version:
            raise ValueError("engineering run cannot change engine version on resume")
        if self._permission_resolver is None:
            raise PermissionError("resume requires current permission policy")
        original_scope = self._scope(row)
        current_scope = self._permission_resolver(row)
        if not self._scope_within(original_scope, current_scope):
            raise PermissionError("saved scope exceeds current permission policy")
        if session_id in self._tasks or row["status"] == "completed":
            return session_id
        if self.store.pending_tool_calls(session_id):
            raise RuntimeError("interrupted tool effect is unknown; reconcile it before resuming")
        with self.store.acquire_workspace(self.workspace):
            self.store.update_run(session_id, status="pending")
        return await self.start(
            self._command(row), scope=original_scope, limits=self._limits(row), request_key=row["request_key"]
        )

    async def _drive(
        self,
        command: RoleRunCommand,
        *,
        scope: PermissionScope,
        limits: RunLimits,
        cancel: asyncio.Event,
        lock: AbstractContextManager[None],
    ) -> RoleRunResult:
        release_allowed = True
        try:
            from polaris.cells.roles.runtime.internal.engineering_loop import run_engineering_loop

            if command.workflow != "direct":
                raise ValueError("collaborative workflow is not available in this engine build")
            client = self._client if self._client is not None else _native_client(self.workspace, limits)
            result = await run_engineering_loop(
                command, scope=scope, limits=limits, cancel=cancel, store=self.store, client=client
            )
        except asyncio.CancelledError:
            pending = self.store.pending_tool_calls(command.session_id)
            result = RoleRunResult(
                command.session_id,
                command.role,
                "Run cancelled; committed observations retained.",
                "cancelled",
                "unknown" if pending else "not_run",
                (),
            )
        except ProcessTreeDrainError as exc:
            release_allowed = False
            self._retained_locks[command.session_id] = lock
            result = RoleRunResult(command.session_id, command.role, str(exc), "interrupted", "unknown", ())
        except Exception as exc:  # noqa: BLE001 - persist the public execution boundary without replaying effects.
            pending = self.store.pending_tool_calls(command.session_id)
            result = RoleRunResult(
                command.session_id,
                command.role,
                f"{type(exc).__name__}: {exc}",
                "interrupted" if pending else "failed",
                "unknown" if pending else "not_run",
                (),
            )
        try:
            self.store.update_run(
                command.session_id,
                status=result.status,
                text=result.text,
                verification=result.verification,
                checks=result.checks,
            )
            self.store.append_event(
                command.session_id,
                kind="run_ended",
                role=command.role,
                payload={
                    "status": result.status,
                    "verification": result.verification,
                    "release_allowed": release_allowed,
                },
            )
            return result
        finally:
            if release_allowed:
                lock.__exit__(None, None, None)
            self._tasks.pop(command.session_id, None)
            self._cancellations.pop(command.session_id, None)

    @staticmethod
    def _command(row: dict[str, Any]) -> RoleRunCommand:
        return RoleRunCommand(
            row["session_id"],
            row["role"],
            row["instruction"],
            Path(row["workspace"]),
            workflow=row["workflow"],
            required_checks=tuple(row["required_checks"]),
        )

    @staticmethod
    def _scope(row: dict[str, Any]) -> PermissionScope:
        scope = row["scope"]
        return PermissionScope(
            tuple(Path(root) for root in scope["read_roots"]),
            tuple(Path(root) for root in scope["write_roots"]),
            frozenset(scope["enabled_tools"]),
            scope["network_allowed"],
        )

    @staticmethod
    def _limits(row: dict[str, Any]) -> RunLimits:
        limits = row["limits"]
        return RunLimits(limits["deadline"], limits["max_output_bytes"], limits["max_tool_output_bytes"])

    @staticmethod
    def _result(row: dict[str, Any]) -> RoleRunResult:
        return RoleRunResult(
            row["session_id"],
            row["role"],
            row["text"],
            row["status"],
            row["verification"],
            tuple(ToolObservation(**item) for item in row["checks"]),
        )

    @staticmethod
    def _scope_within(saved: PermissionScope, current: PermissionScope) -> bool:
        def roots_within(roots: tuple[Path, ...], ceiling: tuple[Path, ...]) -> bool:
            return all(any(root.resolve().is_relative_to(parent.resolve()) for parent in ceiling) for root in roots)

        return (
            saved.enabled_tools <= current.enabled_tools
            and (not saved.network_allowed or current.network_allowed)
            and roots_within(saved.read_roots, current.read_roots)
            and roots_within(saved.write_roots, current.write_roots)
        )
