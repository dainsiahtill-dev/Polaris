"""Workspace-bound entry for the engineering loop and its single session store."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, cast

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
        writable_output_roots: tuple[Path, ...] = (),
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.store = EngineeringSessionStore(self.workspace)
        self._client = client
        self._permission_resolver = permission_resolver
        self._writable_output_roots = writable_output_roots
        self._tasks: dict[str, asyncio.Task[RoleRunResult]] = {}
        self._cancellations: dict[str, asyncio.Event] = {}
        self._retained_locks: dict[str, AbstractContextManager[None]] = {}
        # Ephemeral execution handles only. The single store owns all role facts.
        self._owner: ContextVar[
            tuple[RoleRunCommand, PermissionScope, RunLimits, asyncio.Event, ResilientLLMClient] | None
        ] = ContextVar("engineering_owner", default=None)
        self._active_role: ContextVar[str | None] = ContextVar("engineering_active_role", default=None)
        self._pending_ancestors: ContextVar[frozenset[tuple[int, str]]] = ContextVar(
            "engineering_pending_ancestors", default=frozenset()
        )

    def _assert_workspace(self, workspace: Path) -> None:
        if Path(workspace).resolve() != self.workspace:
            raise PermissionError("engineering service workspace mismatch")

    def observe(self, session_id: str, *, workspace: Path) -> dict[str, Any]:
        self._assert_workspace(workspace)
        row = self.store.load_run(session_id, workspace=self.workspace)
        if row["verification"] == "passed":
            verifier = self._verification(self._scope(row), self._limits(row), asyncio.Event())
            if verifier.current_verification(session_id) != "passed":
                # This is a current-input projection. Keep the bound historical
                # result durable; a read query must not rewrite run facts.
                row["verification"] = "unknown"
        return row

    async def start(
        self,
        command: RoleRunCommand,
        *,
        scope: PermissionScope,
        limits: RunLimits,
        request_key: str | None = None,
    ) -> str:
        return await self._start(
            command, scope=scope, limits=limits, request_key=request_key, cancel=asyncio.Event(), collaborative=True
        )

    async def _start(
        self,
        command: RoleRunCommand,
        *,
        scope: PermissionScope,
        limits: RunLimits,
        request_key: str | None,
        cancel: asyncio.Event,
        collaborative: bool = False,
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
                self._drive(command, scope=scope, limits=limits, cancel=cancel, lock=lock, collaborative=collaborative),
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
        self._assert_workspace(command.workspace)
        owner = self._owner.get()
        if owner is not None:
            original, host_scope, host_limits, host_cancel, client = owner
            if command.session_id != original.session_id or not self._scope_within(scope, host_scope):
                raise PermissionError("nested role exceeds its active session owner")
            if limits != host_limits or cancel is not host_cancel:
                raise PermissionError("nested role cannot replace owner limits or cancellation")
            return await self._run_current_role(command, scope=scope, limits=limits, cancel=cancel, client=client)
        session_id = await self._start(command, scope=scope, limits=limits, request_key=None, cancel=cancel)
        return await self._await_result(session_id, cancel)

    async def _await_result(self, session_id: str, cancel: asyncio.Event) -> RoleRunResult:
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

    async def _run_current_role(
        self,
        command: RoleRunCommand,
        *,
        scope: PermissionScope,
        limits: RunLimits,
        cancel: asyncio.Event,
        client: ResilientLLMClient,
    ) -> RoleRunResult:
        from polaris.cells.roles.runtime.internal.engineering_loop import run_engineering_loop

        parent_role = self._active_role.get()
        owner = self._owner.get()
        purpose = (
            "consultation"
            if parent_role is not None
            else "workflow"
            if owner is not None and owner[0].workflow == "collaborative"
            else "role"
        )
        token = self._active_role.set(command.role)
        try:
            return await run_engineering_loop(
                command,
                scope=scope,
                limits=limits,
                cancel=cancel,
                store=self.store,
                client=client,
                consult=self._consult_dispatch,
                pending_ancestors=self._pending_ancestors.get(),
                # Only _drive owns run terminal state, including final checks.
                project_run=False,
                purpose=purpose,
            )
        finally:
            self._active_role.reset(token)

    async def _consult_dispatch(self, role: str, question: str, turn_seq: int, call_id: str) -> str:
        token = self._pending_ancestors.set(self._pending_ancestors.get() | {(turn_seq, call_id)})
        try:
            owner = self._owner.get()
            if owner is None:
                raise ValueError("consultation requires an active session owner")
            return await self.consult_role(
                cast(Literal["pm", "chief_engineer"], role), question, session_id=owner[0].session_id
            )
        finally:
            self._pending_ancestors.reset(token)

    async def consult_role(self, role: Literal["pm", "chief_engineer"], question: str, *, session_id: str) -> str:
        """Return one read-only answer within the current Director's owned run."""
        from polaris.cells.roles.runtime.internal.engineering_roles import consultation_instruction, role_scope
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        owner = self._owner.get()
        if owner is None or owner[0].session_id != session_id:
            raise ValueError("consultation requires the active bound session owner")
        command, scope, limits, cancel, _client = owner
        if self._active_role.get() != "director":
            raise ValueError("consultation is one level and only available to the active Director")
        if "consult_role" not in {ToolSpecRegistry.get_canonical(name) for name in scope.enabled_tools}:
            raise PermissionError("consult_role is disabled by host scope")
        if (
            role not in {"pm", "chief_engineer"}
            or not isinstance(question, str)
            or not question.strip()
            or "\0" in question
        ):
            raise ValueError("consult_role requires PM or Chief Engineer and a nonempty question")
        result = await self.run_role(
            replace(
                command,
                role=role,
                instruction=consultation_instruction(command.instruction, role, question),
                workflow="direct",
                required_checks=(),
            ),
            scope=role_scope(role, scope),
            limits=limits,
            cancel=cancel,
        )
        if result.status == "cancelled":
            raise asyncio.CancelledError
        if result.status != "completed":
            raise ValueError(f"{role} consultation {result.status}: {result.text}")
        return result.text

    async def run_collaborative(self, instruction: str, *, session_id: str) -> RoleRunResult:
        """Run actual PM, CE and Director loops once, retaining ordinary advice."""
        from polaris.cells.roles.runtime.internal.engineering_roles import collaborative_instruction

        row = self.observe(session_id, workspace=self.workspace)
        if instruction != row["instruction"]:
            raise ValueError("collaborative instruction must retain the original user goal")
        if row["workflow"] != "collaborative":
            raise ValueError("the saved run did not select collaborative workflow")
        owner = self._owner.get()
        if owner is None:
            cancel = asyncio.Event()
            if session_id in self._tasks:
                return await self._await_result(session_id, cancel)
            # A cold pending run would allocate a new writer using persisted
            # grants. Revalidate them exactly as resume does before admission.
            scope = self._revalidated_scope(row) if row["status"] == "pending" else self._scope(row)
            identity = await self._start(
                self._command(row),
                scope=scope,
                limits=self._limits(row),
                request_key=row["request_key"],
                cancel=cancel,
                collaborative=True,
            )
            return await self._await_result(identity, cancel)
        if owner[0].session_id != session_id or self._active_role.get() is not None:
            raise ValueError("collaboration requires the top-level bound session owner")
        command, scope, limits, cancel, _client = owner
        events = self.store.load_events(session_id)
        workflow_frames = {
            event["seq"]
            for event in events
            if event["kind"] == "role_started" and event["payload"].get("purpose") == "workflow"
        }
        finished = {
            event["role"]: event["payload"]
            for event in events
            if event["kind"] == "role_finished" and event["payload"].get("frame_seq") in workflow_frames
        }
        advice: dict[str, str] = {}
        for role in ("pm", "chief_engineer"):
            saved = finished.get(role)
            if saved is not None and saved.get("status") == "completed":
                advice[role] = str(saved["text"])
                continue
            result = await self.run_role(
                replace(
                    command,
                    role=cast(Literal["pm", "chief_engineer"], role),
                    instruction=collaborative_instruction(instruction, pm_advice=advice.get("pm", "")),
                    workflow="direct",
                    required_checks=(),
                ),
                scope=scope,
                limits=limits,
                cancel=cancel,
            )
            if result.status != "completed":
                return result
            advice[role] = result.text
        return await self.run_role(
            replace(
                command,
                role="director",
                instruction=collaborative_instruction(
                    instruction, pm_advice=advice["pm"], ce_advice=advice["chief_engineer"]
                ),
                workflow="direct",
            ),
            scope=scope,
            limits=limits,
            cancel=cancel,
        )

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
        original_scope = self._revalidated_scope(row)
        if session_id in self._tasks or row["status"] == "completed":
            return session_id
        lock = self.store.acquire_workspace(self.workspace)
        lock.__enter__()
        admitted = False
        try:
            # Acquiring the real OS lock first prevents a second interpreter
            # from reconciling effects while their physical owner is alive.
            await self._reconcile_writes(row, scope=original_scope)
            unresolved = self.store.pending_tool_calls(session_id)
            if unresolved:
                detail = "Dispatched tool effects remain unknown; no automatic replay: " + ", ".join(
                    f"{call['tool']} ({call['call_id']})" for call in unresolved
                )
                self.store.update_run(session_id, status="interrupted", verification="unknown", text=detail)
                self.store.append_event(
                    session_id,
                    kind="resume_blocked",
                    role=row["role"],
                    payload={"reason": detail, "tools_dispatched": False},
                )
                raise RuntimeError("interrupted tool effect is unknown; reconcile it before resuming")
            events = self.store.load_events(session_id)
            frames = [
                event
                for event in events
                if event["kind"] == "role_started" and event["payload"].get("purpose") != "consultation"
            ]
            if frames:
                frame = frames[-1]
                finished = [
                    event
                    for event in events
                    if event["kind"] == "role_finished" and event["payload"].get("frame_seq") == frame["seq"]
                ]
                if finished and (frame["role"] == "director" or finished[-1]["payload"].get("status") != "completed"):
                    self.store.continue_role(
                        session_id,
                        role=frame["role"],
                        feedback="Resume the same goal using the committed native tool observations. "
                        "Cancellation did not roll back prior effects; do not repeat prior actions blindly.\n"
                        + json.dumps(
                            {"verification": row["verification"], "checks": row["checks"]}, ensure_ascii=False
                        ),
                    )
            command, limits, cancel = self._command(row), self._limits(row), asyncio.Event()
            self.store.update_run(session_id, status="running")
            self.store.append_event(
                session_id, kind="run_resumed", role=command.role, payload={"engine_version": self.engine_version}
            )
            self._cancellations[session_id] = cancel
            self._tasks[session_id] = asyncio.create_task(
                self._drive(command, scope=original_scope, limits=limits, cancel=cancel, lock=lock, collaborative=True),
                name=f"engineering:{session_id}",
            )
            admitted = True
            return session_id
        finally:
            if not admitted:
                lock.__exit__(None, None, None)

    async def _reconcile_writes(self, row: dict[str, Any], *, scope: PermissionScope) -> None:
        """Read back a bounded exact write; never reissue an unknown operation."""
        from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools

        session_id = str(row["session_id"])
        tools = EngineeringTools(self.workspace, scope, self._limits(row), asyncio.Event())
        events = self.store.load_events(session_id)
        for pending in self.store.pending_tool_calls(session_id):
            try:
                name, arguments = tools.normalize(pending["tool"], pending["arguments"])
                if name != "write_file" or set(arguments) != {"file", "content"}:
                    continue
                expected = arguments["content"]
                if (
                    not isinstance(expected, str)
                    or len(expected.encode("utf-8")) > self._limits(row).max_tool_output_bytes
                ):
                    continue
                started = next(
                    event
                    for event in events
                    if event["kind"] == "tool_started"
                    and event["payload"]["turn_seq"] == pending["turn_seq"]
                    and event["payload"]["call_id"] == pending["call_id"]
                )
                role = started["role"]
                if role != "director":
                    continue
                history = self.store.load_message_history(session_id, role=role)
                assistant = next(message for message in reversed(history) if message["role"] == "assistant")
                if [call.get("id") for call in assistant.get("tool_calls", [])] != [pending["call_id"]]:
                    continue
                # normalize checks current write authority; read_file checks
                # current read authority and the actual safe KFS path again.
                turn = self.store.append_event(
                    session_id,
                    kind="reconciliation_started",
                    role=role,
                    payload={"original_turn_seq": pending["turn_seq"], "original_call_id": pending["call_id"]},
                )
                call_id = f"readback-{turn}"
                read_arguments = {"file": arguments["file"], "max_bytes": self._limits(row).max_tool_output_bytes}
                self.store.record_tool_dispatch(
                    session_id, turn_seq=turn, call_id=call_id, tool="read_file", arguments=read_arguments, role=role
                )
                observed = await tools.execute(call_id, "read_file", read_arguments)
                self.store.record_tool_observation(session_id, turn_seq=turn, observation=observed, role=role)
                if observed.ok and not observed.interrupted and observed.content == expected:
                    self.store.record_write_reconciliation(
                        session_id,
                        turn_seq=pending["turn_seq"],
                        call_id=pending["call_id"],
                        role=role,
                        readback=observed,
                    )
            except (ValueError, OSError, StopIteration) as error:
                # Unsupported or ambiguous effects remain original unknowns.
                self.store.append_event(
                    session_id,
                    kind="reconciliation_unavailable",
                    role=row["role"],
                    payload={"turn_seq": pending["turn_seq"], "call_id": pending["call_id"], "reason": str(error)},
                )

    def _verification(self, scope: PermissionScope, limits: RunLimits, cancel: asyncio.Event) -> Any:
        from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification

        return EngineeringVerification(
            self.workspace,
            store=self.store,
            scope=scope,
            limits=limits,
            cancel=cancel,
            writable_output_roots=self._writable_output_roots,
        )

    async def _verify_director(
        self,
        command: RoleRunCommand,
        result: RoleRunResult,
        *,
        scope: PermissionScope,
        limits: RunLimits,
        cancel: asyncio.Event,
    ) -> RoleRunResult:
        """Use actual saved checks; failed checks return to this same Director."""
        if result.role != "director" or result.status != "completed":
            return result
        saved = self.store.load_run(command.session_id, workspace=self.workspace)
        commands = tuple(saved["required_checks"])
        if not commands:
            return result
        verifier = self._verification(scope, limits, cancel)
        while result.status == "completed":
            revision = int(self.store.load_run(command.session_id, workspace=self.workspace)["workspace_revision"])
            checks = await verifier.run_verification(
                commands, session_id=command.session_id, workspace_revision=revision
            )
            row = self.store.load_run(command.session_id, workspace=self.workspace)
            verification = row["verification"]
            if verification == "passed":
                verification = verifier.current_verification(command.session_id)
            if cancel.is_set() or verification == "unknown":
                return replace(
                    result,
                    status="cancelled" if cancel.is_set() else "interrupted",
                    verification="unknown",
                    checks=checks,
                )
            if verification != "failed":
                return replace(result, verification=verification, checks=checks)
            if limits.deadline is not None and time.time() >= limits.deadline:
                return replace(result, status="failed", verification="failed", checks=checks)
            feedback = (
                "Final required checks failed. Continue the same task using these actual results:\n"
                + json.dumps(
                    [
                        {
                            "command": actual,
                            "result": {"ok": check.ok, "exit_code": check.exit_code, "content": check.content},
                        }
                        for actual, check in zip(commands, checks, strict=False)
                    ],
                    ensure_ascii=False,
                )
            )
            self.store.continue_role(command.session_id, role="director", feedback=feedback)
            starts = [
                event
                for event in self.store.load_events(command.session_id)
                if event["kind"] == "role_started" and event["role"] == "director"
            ]
            actual_command = replace(
                command, role="director", instruction=starts[-1]["payload"]["instruction"], workflow="direct"
            )
            result = await self.run_role(actual_command, scope=scope, limits=limits, cancel=cancel)
            if result.status != "completed":
                current = self.store.load_run(command.session_id, workspace=self.workspace)
                return replace(
                    result,
                    verification=current["verification"],
                    checks=tuple(ToolObservation(**check) for check in current["checks"]),
                )
        return result

    def _revalidated_scope(self, row: dict[str, Any]) -> PermissionScope:
        """Re-admit saved grants against current host policy; never infer authority."""
        if self._permission_resolver is None:
            raise PermissionError("saved execution requires current permission policy")
        original_scope = self._scope(row)
        current_scope = self._permission_resolver(row)
        if not self._scope_within(original_scope, current_scope):
            raise PermissionError("saved scope exceeds current permission policy")
        return original_scope

    async def _drive(
        self,
        command: RoleRunCommand,
        *,
        scope: PermissionScope,
        limits: RunLimits,
        cancel: asyncio.Event,
        lock: AbstractContextManager[None],
        collaborative: bool = False,
    ) -> RoleRunResult:
        release_allowed = True
        drain_error: ProcessTreeDrainError | None = None
        drain_reporting_error: str | None = None
        try:
            client = self._client if self._client is not None else _native_client(self.workspace, limits)
            token = self._owner.set((command, scope, limits, cancel, client))
            try:
                if collaborative and command.workflow == "collaborative":
                    result = await self.run_collaborative(command.instruction, session_id=command.session_id)
                else:
                    result = await self.run_role(command, scope=scope, limits=limits, cancel=cancel)
                result = await self._verify_director(command, result, scope=scope, limits=limits, cancel=cancel)
            finally:
                self._owner.reset(token)
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
            drain_error = exc
            release_allowed = False
            self._retained_locks[command.session_id] = lock
            result = RoleRunResult(command.session_id, command.role, str(exc), "interrupted", "unknown", ())
            try:
                # Record physical uncertainty before generic terminal reporting.
                # Its final projection below may retry this idempotent fact;
                # persistent journal failure still raises the original drain error.
                self.store.update_run(command.session_id, status="interrupted", verification="unknown")
            except Exception as journal_error:  # noqa: BLE001 - retain original physical failure and report journal error.
                drain_reporting_error = f"{type(journal_error).__name__}: {journal_error}"
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
                    "drain_reporting_error": drain_reporting_error,
                },
            )
            return result
        except BaseException as journal_error:
            if drain_error is not None:
                # Reporting cannot replace the unresolved physical failure.
                # The outer execution owner retains its workspace writer lock.
                raise drain_error from journal_error
            raise
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
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        def roots_within(roots: tuple[Path, ...], ceiling: tuple[Path, ...]) -> bool:
            return all(any(root.resolve().is_relative_to(parent.resolve()) for parent in ceiling) for root in roots)

        return (
            {ToolSpecRegistry.get_canonical(name) for name in saved.enabled_tools}
            <= {ToolSpecRegistry.get_canonical(name) for name in current.enabled_tools}
            and (not saved.network_allowed or current.network_allowed)
            and roots_within(saved.read_roots, current.read_roots)
            and roots_within(saved.write_roots, current.write_roots)
        )
