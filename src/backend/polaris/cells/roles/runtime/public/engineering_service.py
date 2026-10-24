"""Workspace-bound entry for the engineering loop and its single session store."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import subprocess
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractContextManager
from contextvars import ContextVar
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any, Literal, cast

from polaris.cells.roles.runtime.internal.engineering_session import (
    EngineeringSessionConflictError as EngineeringSessionConflictError,
    EngineeringSessionStore,
    WorkspaceBusyError,
)
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    ArtifactObservation,
    BrowserObservationConfig,
    PermissionScope,
    RoleRunCommand,
    RoleRunResult,
    RunLimits,
    ToolObservation,
)
from polaris.kernelone.exceptions import PathSecurityError
from polaris.kernelone.llm.engine.client import ProviderConfig, ResilientLLMClient
from polaris.kernelone.llm.engine.contracts import AIRequest, AIResponse, TaskType
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.engine.invocation_budget import invoke_with_budget
from polaris.kernelone.llm.engine.resilience import RetryConfig, TimeoutConfig
from polaris.kernelone.process.process_tree import ProcessTreeDrainError

_TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
logger = logging.getLogger(__name__)


class _NativeEngineProvider(AIExecutor):
    """Name the existing native executor for the resilient client's protocol."""

    name = "kernelone_native"


def _native_client(
    workspace: Path,
    limits: RunLimits,
    native_request_observer: Callable[[AIRequest, Mapping[str, Any]], None] | None = None,
) -> ResilientLLMClient:
    # K1 owns model selection and provider timeouts. This one-provider wrapper
    # adds no fallback or retries and cannot shorten a provider's configured
    # deadline. The engineering loop also watches the actual run deadline.
    remaining = max(0.001, limits.deadline - time.time()) if limits.deadline is not None else float("inf")
    return ResilientLLMClient(
        [
            (
                _NativeEngineProvider(workspace=str(workspace), native_request_observer=native_request_observer),
                ProviderConfig(provider_name="kernelone_native"),
            )
        ],
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
        require_qa: bool = False,
        event_publisher: Callable[[dict[str, Any]], Awaitable[bool]] | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self._event_publisher = event_publisher
        self._publication_loop: asyncio.AbstractEventLoop | None = None
        self._publication_tail: asyncio.Task[None] | None = None
        self._publication_error: str | None = None
        self._native_observation_error: str | None = None
        self.store = EngineeringSessionStore(self.workspace, on_committed_events=self._committed_events)
        self._client = client
        self._permission_resolver = permission_resolver
        from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification

        self._writable_output_roots = EngineeringVerification.validate_declarations(
            self.workspace, writable_output_roots
        )
        self._require_qa = require_qa
        self._tasks: dict[str, asyncio.Task[RoleRunResult]] = {}
        self._cancellations: dict[str, asyncio.Event] = {}
        self._retained_locks: dict[str, AbstractContextManager[None]] = {}
        self._shutdown_error: BaseException | None = None
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

    @property
    def event_delivery_error(self) -> str | None:
        """Report an observed transport gap, without changing execution facts."""
        return self._publication_error or self.store.event_delivery_error

    def _committed_events(self, events: tuple[dict[str, Any], ...]) -> None:
        if self._event_publisher is None:
            return
        loop = self._publication_loop
        if loop is None or loop.is_closed():
            self._publication_error = "Engineering event publication unavailable: no active host event loop"
            logger.error(self._publication_error)
            return
        loop.call_soon_threadsafe(self._queue_committed_events, events)

    def _queue_committed_events(self, events: tuple[dict[str, Any], ...]) -> None:
        previous = self._publication_tail

        async def publish() -> None:
            if previous is not None:
                await previous
            for event in events:
                try:
                    assert self._event_publisher is not None
                    if not await self._event_publisher(event):
                        raise RuntimeError("publisher did not acknowledge the committed event")
                except Exception as error:  # noqa: BLE001 - publication cannot replay a committed execution effect.
                    self._publication_error = (
                        f"Engineering event publication failed at {event['session_id']}:{event['seq']}: {error}"
                    )
                    logger.error(self._publication_error)

        self._publication_tail = asyncio.create_task(publish(), name="engineering-event-publication")

    async def flush_events(self) -> None:
        """Join ephemeral publication handles; never recreate or replay effects."""
        await asyncio.sleep(0)
        while self._publication_tail is not None:
            current = self._publication_tail
            await asyncio.shield(current)
            if current is self._publication_tail:
                break

    def record_native_request_observation(self, request: AIRequest, observation: Mapping[str, Any]) -> None:
        """Bind a host physical-capture observation to the same durable role turn."""
        context = request.context
        workspace = Path(str(context.get("workspace", "")))
        self._assert_workspace(workspace)
        session_id = str(context.get("session_id", ""))
        row = self.store.load_run(session_id, workspace=self.workspace)
        if context.get("engine_version") != self.engine_version or row["engine_version"] != self.engine_version:
            raise ValueError("native request observation engine mismatch")
        turn_seq = context.get("turn_seq")
        if not any(
            event["seq"] == turn_seq and event["kind"] == "turn_started" and event["role"] == request.role
            for event in self.store.load_events(session_id, kinds=("turn_started",), role=request.role)
        ):
            raise ValueError("native request observation has no matching host role turn")
        payload = dict(observation)
        reference = payload.get("context_snapshot_ref")
        if reference is not None and (
            not isinstance(reference, str) or re.fullmatch(r"[0-9a-f]{24}", reference) is None
        ):
            payload.pop("context_snapshot_ref", None)
            payload["snapshot_status"] = "invalid_reference"
        payload.update({"turn_seq": turn_seq, "call_id": context.get("call_id"), "role": request.role})
        self.store.append_event(session_id, kind="llm_request_observed", role=str(request.role), payload=payload)

    def _observe_native_request(self, request: AIRequest, observation: Mapping[str, Any]) -> None:
        try:
            self.record_native_request_observation(request, observation)
        except Exception as error:  # noqa: BLE001 - an observation error must not cause a second provider send.
            self._native_observation_error = f"Native request observation unavailable: {type(error).__name__}: {error}"
            logger.error(self._native_observation_error)

    def snapshot(self, session_id: str, *, workspace: Path) -> dict[str, Any]:
        """Expose one committed snapshot for initial and reconnect hydration."""
        self._assert_workspace(workspace)
        snapshot = self.store.load_snapshot(session_id, workspace=workspace)
        row = self._fresh_verification(snapshot["run"])
        return {
            **row,
            "events": snapshot["events"],
            "role_events": snapshot["role_events"],
            "last_seq": snapshot["last_seq"],
            "event_delivery_error": self.event_delivery_error,
            "native_observation_error": self._native_observation_error,
        }

    def current_snapshot(self, *, workspace: Path) -> dict[str, Any] | None:
        """Discover current identity, then apply the same read-only freshness checks."""
        self._assert_workspace(workspace)
        session_id = self.current_session_id(workspace=workspace)
        return self.snapshot(session_id, workspace=workspace) if session_id is not None else None

    def current_session_id(self, *, workspace: Path) -> str | None:
        """Resolve committed current identity without claiming fresh acceptance."""
        self._assert_workspace(workspace)
        return self.store.find_current_session_id(workspace=workspace, engine_version=self.engine_version)

    def request_identity(self, request_key: str, *, workspace: Path) -> dict[str, Any] | None:
        """Read committed request identity only; it carries no current verdict."""
        self._assert_workspace(workspace)
        session_id = self.store.find_request(request_key)
        if session_id is None:
            return None
        row = self.store.load_run(session_id, workspace=self.workspace)
        return {
            "session_id": session_id,
            "request_key": row["request_key"],
            "instruction_sha256": hashlib.sha256(row["instruction"].encode("utf-8")).hexdigest(),
            "workflow": row["workflow"],
            "required_checks": row["required_checks"],
            "permission_profile_id": row.get("permission_profile_id"),
        }

    def find_request(self, request_key: str, *, workspace: Path) -> dict[str, Any] | None:
        """Read saved request identity through the same workspace owner."""
        self._assert_workspace(workspace)
        session_id = self.store.find_request(request_key)
        return self.observe(session_id, workspace=workspace) if session_id is not None else None

    async def chat_role(
        self,
        role: str,
        message: str,
        *,
        history: tuple[dict[str, str], ...] = (),
        limits: RunLimits | None = None,
    ) -> AIResponse:
        """Discuss through native K1 without engineering admission or action tools."""
        identities = {"pm": "PM", "chief_engineer": "Chief Engineer", "director": "Director", "qa": "QA"}
        if role not in identities or not message.strip():
            raise ValueError("a supported role and non-empty message are required")
        messages = [
            {
                "role": "system",
                "content": f"You are {identities[role]}. Discuss the user's question in ordinary "
                "text. This discussion has no action tools and creates no engineering task. "
                "Do not claim files changed, commands ran, or checks passed.",
            }
        ]
        for item in history:
            if (
                set(item) - {"role", "content"}
                or item.get("role") not in {"user", "assistant"}
                or not isinstance(item.get("content"), str)
            ):
                raise ValueError("discussion history accepts only user and assistant text")
            messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": message})
        host_limits = limits if limits is not None else RunLimits(None, 1_000_000, 64_000)
        options: dict[str, Any] = {"messages": messages, "tools": [], "tool_choice": "none"}
        if host_limits.deadline is not None:
            remaining = host_limits.deadline - time.time()
            if remaining <= 0:
                raise TimeoutError("discussion provider deadline exceeded")
        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role=role,
            input=message,
            options=options,
            context={"workspace": str(self.workspace), "engine_version": self.engine_version},
        )
        client = self._client if self._client is not None else _native_client(self.workspace, host_limits)
        if host_limits.deadline is None:
            response = await client.invoke(request)
        else:
            response = await invoke_with_budget(client.invoke(request), max(0.001, host_limits.deadline - time.time()))
        if response.ok:
            from polaris.cells.roles.runtime.internal.engineering_loop import _native_calls

            if _native_calls(response):
                raise ValueError("discussion provider returned action tool calls")
        return response

    def observe(self, session_id: str, *, workspace: Path) -> dict[str, Any]:
        self._assert_workspace(workspace)
        row = self.store.load_run(session_id, workspace=self.workspace)
        return self._fresh_verification(row)

    def _fresh_verification(self, row: dict[str, Any]) -> dict[str, Any]:
        needs_qa = row.get("require_qa") and row.get("qa_verdict") in {"approved", "rejected"}
        if row["verification"] == "passed" or needs_qa:
            try:
                scope = self._revalidated_scope(row) if self._permission_resolver is not None else self._scope(row)
                verifier = self._verification(
                    scope, self._limits(row), asyncio.Event(), output_roots=self._saved_outputs(row)
                )
                with verifier._observe_current_inputs():
                    return self._observed_verification(row, verifier=verifier, scope=scope)
            except (OSError, ValueError, RuntimeError, PathSecurityError) as error:
                if row["verification"] == "passed":
                    row["verification"] = "unknown"
                    row["verification_detail"] = f"Cannot observe current verification inputs: {error}"
                if needs_qa:
                    row["qa_verdict"] = "unknown"
        if row["verification"] == "unknown" or (row.get("require_qa") and row.get("qa_verdict") == "unknown"):
            row.update(project_outcome="unknown", acceptance_basis=None)
        return row

    def _observed_verification(self, row: dict[str, Any], *, verifier: Any, scope: PermissionScope) -> dict[str, Any]:
        """Apply existing verdict predicates to this query's actual KFS inputs."""
        session_id = str(row["session_id"])
        if row["verification"] == "passed":
            try:
                current = verifier.current_verification(session_id)
                if current == "passed" and not row.get("require_qa"):
                    self._require_visual_input(
                        session_id,
                        role="director",
                        scope=scope,
                        current_binding=lambda: verifier.review_binding(session_id),
                    )
            except (OSError, ValueError, RuntimeError, PathSecurityError) as error:
                current = "unknown"
                row["verification_detail"] = f"Cannot observe current verification inputs: {error}"
            if current != "passed":
                # A missing/replaced output grant cannot prove a current pass.
                # Preserve the historical binding and keep this query read-only.
                row["verification"] = "unknown"
        if row.get("require_qa") and row.get("qa_verdict") in {"approved", "rejected"}:
            try:
                scope = self._revalidated_scope(row)
                decisions = self.store.load_events(session_id, kinds=("qa_review_completed",))
                if (
                    (row["qa_verdict"] == "approved" and row["verification"] != "passed")
                    or not decisions
                    or decisions[-1]["payload"].get("input_binding") != verifier.review_binding(session_id)
                ):
                    row["qa_verdict"] = "unknown"
                elif row["qa_verdict"] == "approved":
                    decision = decisions[-1]["payload"]
                    reports = [
                        event
                        for event in self.store.load_events(session_id, kinds=("tool_observed",), role="qa")
                        if event["payload"].get("call_id") == decision.get("report_call_id")
                        and event["payload"].get("tool") == "report_review"
                    ]
                    if scope.browser is not None and scope.browser.require_visual_review:
                        if not reports:
                            raise ValueError("current visual QA report is unavailable")
                        self._require_visual_input(
                            session_id,
                            role="qa",
                            scope=scope,
                            current_binding=lambda: verifier.review_binding(session_id),
                            frame_seq=decision.get("frame_seq"),
                            turn_seq=reports[-1]["payload"].get("turn_seq"),
                        )
            except (OSError, ValueError, RuntimeError, PathSecurityError):
                row["qa_verdict"] = "unknown"
        if row["verification"] == "unknown" or (row.get("require_qa") and row.get("qa_verdict") == "unknown"):
            row.update(project_outcome="unknown", acceptance_basis=None)
        return row

    async def start(
        self,
        command: RoleRunCommand,
        *,
        scope: PermissionScope,
        limits: RunLimits,
        request_key: str | None = None,
        permission_profile_id: str | None = None,
    ) -> str:
        return await self._start(
            command,
            scope=scope,
            limits=limits,
            request_key=request_key,
            cancel=asyncio.Event(),
            collaborative=True,
            permission_profile_id=permission_profile_id,
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
        permission_profile_id: str | None = None,
    ) -> str:
        self._assert_workspace(command.workspace)
        self._publication_loop = asyncio.get_running_loop()
        from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        if self._require_qa and (
            command.role != "director"
            or "report_review" not in {ToolSpecRegistry.get_canonical(name) for name in scope.enabled_tools}
        ):
            raise PermissionError("required QA needs Director ownership and explicit report_review permission")
        EngineeringVerification.validate_declarations(
            self.workspace, self._writable_output_roots, write_roots=scope.write_roots
        )
        declared_outputs: tuple[Path, ...] | None = self._writable_output_roots
        execution_policy: str | None = "bash_pipefail_v1"
        existing_id = self.store.find_request(request_key) if request_key is not None else command.session_id
        if existing_id is not None:
            try:
                existing = self.store.load_run(existing_id, workspace=self.workspace)
                execution_policy = existing.get("verification_execution_policy")
                if existing.get("verification_output_roots") is None and not self._writable_output_roots:
                    declared_outputs = None
            except KeyError:
                pass
        session_id = self.store.create_run(
            command,
            scope=scope,
            limits=limits,
            engine_version=self.engine_version,
            request_key=request_key,
            permission_profile_id=permission_profile_id,
            require_qa=self._require_qa,
            verification_output_roots=declared_outputs,
            verification_execution_policy=execution_policy,
        )
        row = self.observe(session_id, workspace=self.workspace)
        if session_id in self._tasks or row["status"] in _TERMINAL or row["status"] == "running":
            return session_id
        command = self._command(row)
        if row.get("permission_profile_id") is not None:
            scope = self._revalidated_scope(row)
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

    async def wait(self, session_id: str, *, workspace: Path) -> RoleRunResult:
        """Join the existing owner, including its physical cleanup, without a new run."""
        self._assert_workspace(workspace)
        self.observe(session_id, workspace=workspace)
        return await self._await_result(session_id, asyncio.Event())

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
            # Historical declarations are readable context, not current grants.
            # Keep current-host validation on the existing Verifier effect path.
            saved_outputs = (
                self.store.load_run(command.session_id, workspace=self.workspace).get("verification_output_roots")
                if command.role == "director"
                else None
            )
            return await run_engineering_loop(
                command,
                scope=scope,
                limits=limits,
                cancel=cancel,
                store=self.store,
                client=client,
                consult=self._consult_dispatch,
                verify=self._verify_dispatch,
                verification_output_declarations=(
                    tuple(Path(root) for root in saved_outputs) if saved_outputs is not None else None
                ),
                observe=self._observe_dispatch if scope.browser is not None else None,
                artifact_binding=(
                    lambda: self._verification(
                        scope,
                        limits,
                        cancel,
                        output_roots=self._saved_outputs(
                            self.store.load_run(command.session_id, workspace=self.workspace)
                        ),
                    ).review_binding(command.session_id)
                ),
                visual_binding=(
                    lambda: self._verification(
                        scope,
                        limits,
                        cancel,
                        output_roots=self._saved_outputs(
                            self.store.load_run(command.session_id, workspace=self.workspace)
                        )
                        if scope.browser is not None
                        else None,
                    ).review_binding(command.session_id)
                )
                if scope.browser is not None
                else None,
                pending_ancestors=self._pending_ancestors.get(),
                # Only _drive owns run terminal state, including final checks.
                project_run=False,
                purpose=purpose,
                authorize=(lambda: self._authorize_tools(command.session_id))
                if self._permission_resolver is not None
                else None,
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

    async def _verify_dispatch(self, commands: tuple[str, ...], turn_seq: int, call_id: str) -> ToolObservation:
        """Let the active Director request actual checks within unchanged host grants."""
        from polaris.cells.roles.runtime.internal.engineering_verification import verification_observation
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        owner = self._owner.get()
        if owner is None or self._active_role.get() != "director":
            raise ValueError("verify_project requires the active Director session owner")
        command, scope, limits, cancel, _client = owner
        row = self.store.load_run(command.session_id, workspace=self.workspace)
        if self._permission_resolver is not None:
            scope = self._revalidated_scope(row)
        enabled = {ToolSpecRegistry.get_canonical(name) for name in scope.enabled_tools}
        if not {"verify_project", "execute_command"} <= enabled:
            raise PermissionError("verify_project and execute_command must both be granted by the host")
        if cancel.is_set():
            raise asyncio.CancelledError
        if limits.deadline is not None and time.time() >= limits.deadline:
            raise ValueError("engineering run deadline exceeded")
        verifier = self._verification(scope, limits, cancel, output_roots=self._saved_outputs(row))
        requested_by = (turn_seq, call_id)
        await verifier.run_verification(
            commands,
            session_id=command.session_id,
            workspace_revision=int(row["workspace_revision"]),
            requested_by=requested_by,
        )
        completed = [
            event["payload"]
            for event in self.store.load_events(command.session_id)
            if event["kind"] == "verification_completed"
            and event["payload"].get("input_binding", {}).get("requested_by") == list(requested_by)
        ]
        if not completed:
            # Keep the already-journaled parent pending. Observed child exits
            # cannot replace a missing durable final input binding or permit replay.
            raise RuntimeError("verify_project has no matching durable verification completion")
        return verification_observation(call_id, completed[-1], limits.max_tool_output_bytes)

    def _saved_artifact(
        self, session_id: str, reference: object, binding: Mapping[str, Any], *, require_current: bool = True
    ) -> ArtifactObservation:
        """Resolve committed bytes against their original producer and current inputs."""
        import base64
        import hashlib

        from polaris.cells.roles.runtime.internal.engineering_artifacts import inputs_digest
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        if not isinstance(reference, str) or re.fullmatch(r"artifact_[0-9a-f]{32}", reference) is None:
            raise ValueError("invalid artifact_ref")
        artifact = self.store.artifact(session_id, reference)
        producer = self.store.artifact_producer(session_id, reference)
        if artifact is None or producer is None:
            raise ValueError("artifact_ref has no committed producer in this run")
        if ToolSpecRegistry.get_canonical(str(producer.get("tool"))) != "execute_command":
            raise ValueError("artifact_ref does not originate in a command observation")
        completed = [
            event
            for event in self.store.load_events(session_id, kinds=("tool_output_capture_completed",))
            if event["payload"].get("call_id") == producer["call_id"]
            and event["payload"].get("turn_seq") == producer["turn_seq"]
            and event["role"] == producer["role"]
            and reference in event["payload"].get("artifact_refs", [])
            and event["seq"] > producer["tool_observed_event_seq"]
        ]
        if len(completed) != 1:
            raise ValueError("artifact_ref capture has no unique durable completion")
        original = completed[0]["payload"].get("input_binding")
        if (
            not isinstance(original, dict)
            or artifact.source_digest is None
            or artifact.source_digest != inputs_digest(original)
        ):
            raise ValueError("artifact_ref original source binding is unknown")

        def comparable(value: Mapping[str, Any]) -> dict[str, Any]:
            return {key: item for key, item in value.items() if key != "verification_seq"}

        previous, current = comparable(original), comparable(binding)
        if not require_current:
            previous = {key: value for key, value in previous.items() if key not in {"workspace_revision", "inputs"}}
            current = {key: value for key, value in current.items() if key not in {"workspace_revision", "inputs"}}
        if previous != current:
            raise ValueError("artifact_ref is stale or current host authority changed")
        data = base64.b64decode(artifact.data_base64, validate=True)
        if hashlib.sha256(data).hexdigest() != artifact.sha256:
            raise ValueError("artifact_ref saved bytes failed integrity verification")
        return artifact

    async def _observe_dispatch(self, arguments: Mapping[str, Any], turn_seq: int, call_id: str) -> ToolObservation:
        """Bind an authorized read-only capture before its durable tool result."""
        from polaris.cells.roles.runtime.internal.engineering_browser import capture_browser_observation
        from polaris.cells.roles.runtime.internal.engineering_roles import role_scope
        from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        owner, role = self._owner.get(), self._active_role.get()
        if owner is None or role not in {"director", "qa"}:
            raise ValueError("browser_observe requires the active Director or QA session owner")
        command, scope, limits, cancel, _client = owner
        row = self.store.load_run(command.session_id, workspace=self.workspace)
        if self._permission_resolver is not None:
            scope = self._revalidated_scope(row)
        permitted = role_scope(role, scope)
        if permitted.browser is None or "browser_observe" not in {
            ToolSpecRegistry.get_canonical(name) for name in permitted.enabled_tools
        }:
            raise PermissionError("browser_observe requires unchanged explicit host permission")
        if cancel.is_set():
            raise asyncio.CancelledError
        if limits.deadline is not None and time.time() >= limits.deadline:
            raise ValueError("engineering run deadline exceeded")
        readonly = replace(permitted, write_roots=(), network_allowed=False)
        if "revisit_call_ids" in arguments:
            from polaris.cells.roles.runtime.internal.engineering_visual import original_visual_capture

            identities = arguments["revisit_call_ids"]
            if (
                set(arguments) != {"revisit_call_ids"}
                or not isinstance(identities, list)
                or not identities
                or any(not isinstance(identity, str) or not identity.strip() for identity in identities)
                or len(set(identities)) != len(identities)
            ):
                raise ValueError(
                    "revisit_call_ids must be distinct existing capture IDs, exclusive with capture arguments"
                )
            events = self.store.load_events(command.session_id, role=role)
            provenance = []
            tools = EngineeringTools(
                self.workspace,
                readonly,
                limits,
                cancel,
                observe=lambda args: self._observe_dispatch(args, turn_seq, call_id),
            )
            for identity in identities:
                original = original_visual_capture(events, role, identity)
                capture_arguments = original["dispatch"]["payload"]["arguments"]
                # Historical dispatch preserves native arguments. Reapply the
                # same registry normalization before current host authorization.
                _, authorized_arguments = tools.normalize("browser_observe", dict(capture_arguments))
                if "artifact_ref" in authorized_arguments:
                    verifier = self._verification(scope, limits, cancel, output_roots=self._saved_outputs(row))
                    self._saved_artifact(
                        command.session_id,
                        authorized_arguments["artifact_ref"],
                        verifier.review_binding(command.session_id),
                        require_current=False,
                    )
                    entry = None
                else:
                    entry = tools._path(str(authorized_arguments.get("entry", "index.html")), write=False)
                if "web_root" in authorized_arguments:
                    web_root = tools._path(str(authorized_arguments["web_root"]), write=False)
                    if entry is None or not entry.is_relative_to(web_root):
                        raise ValueError("saved browser web_root must contain entry")
                provenance.append(
                    {
                        "call_id": identity,
                        "role": role,
                        "original_turn_seq": original["observation"]["payload"]["turn_seq"],
                        "frame_count": len(original["images"]),
                        "capture_arguments": capture_arguments,
                        "image_sha256": [image.sha256 for image in original["images"]],
                        "freshness": "original_binding_only_not_a_new_capture",
                    }
                )
            # The existing tool journal stores references only. Projection reads
            # exact original pixels; no capture/binding/second byte store exists.
            return ToolObservation(
                call_id,
                "browser_observe",
                True,
                json.dumps({"revisit_call_ids": identities, "saved_captures": provenance}, ensure_ascii=False),
                None,
                False,
            )
        verifier = self._verification(scope, limits, cancel, output_roots=self._saved_outputs(row))
        binding: dict[str, Any] = {}
        try:
            binding = verifier.review_binding(command.session_id)
            tools = EngineeringTools(self.workspace, readonly, limits, cancel)

            async def run_process(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
                return await tools._process(argv, timeout=timeout, writes=())

            saved_artifact = (
                self._saved_artifact(command.session_id, arguments["artifact_ref"], binding)
                if "artifact_ref" in arguments
                else None
            )
            observed = await capture_browser_observation(
                call_id,
                dict(arguments),
                workspace=self.workspace,
                scope=readonly,
                run_process=run_process,
                **({"saved_artifact": saved_artifact} if saved_artifact is not None else {}),
            )
            if observed.call_id != call_id or observed.tool != "browser_observe":
                raise ValueError("browser observation identity mismatch")
            if binding != verifier.review_binding(command.session_id):
                observed = replace(
                    observed, ok=False, images=(), content="Current inputs changed during browser capture."
                )
            elif cancel.is_set():
                observed = replace(observed, ok=False, images=(), interrupted=True)
            elif not observed.ok or observed.interrupted or not observed.images:
                observed = replace(observed, ok=False, images=())
        except subprocess.TimeoutExpired:
            observed = ToolObservation(
                call_id,
                "browser_observe",
                False,
                "Browser capture timed out after physical cleanup.",
                None,
                cancel.is_set(),
                timed_out=True,
            )
        except (OSError, ValueError, PathSecurityError) as error:
            observed = ToolObservation(
                call_id, "browser_observe", False, f"{type(error).__name__}: {error}", None, False
            )
        # Cancellation and unknown physical drains propagate; the already
        # dispatched tool remains pending until its original owner reconciles it.
        self.store.append_event(
            command.session_id,
            kind="visual_observation_completed",
            role=role,
            payload={
                "ok": observed.ok,
                "requested_by": [turn_seq, call_id],
                "input_binding": binding,
                "image_sha256": [image.sha256 for image in observed.images],
            },
        )
        return observed

    def _require_visual_input(
        self,
        session_id: str,
        *,
        role: str,
        scope: PermissionScope,
        current_binding: Mapping[str, Any] | Callable[[], Mapping[str, Any]],
        turn_seq: int | None = None,
        frame_seq: int | None = None,
    ) -> None:
        """Apply the current-frame native-image condition at the service boundary."""
        if scope.browser is None or not scope.browser.require_visual_review:
            return
        from polaris.cells.roles.runtime.internal.engineering_visual import require_current_visual_input

        events = self.store.load_events(session_id, kinds=("role_started", "turn_started"), role=role)
        starts = [event for event in events if event["kind"] == "role_started" and event["role"] == role]
        if frame_seq is None:
            frame_seq = starts[-1]["seq"] if starts else 0
        if type(frame_seq) is not int or (turn_seq is not None and type(turn_seq) is not int):
            raise ValueError("current visual role identity is invalid")
        turns = [
            event["seq"]
            for event in events
            if event["kind"] == "turn_started" and event["role"] == role and event["seq"] > frame_seq
        ]
        if not frame_seq or not turns or (turn_seq is not None and turn_seq not in turns):
            raise ValueError("current visual evidence is required in an actual current role turn")
        require_current_visual_input(
            self.store,
            session_id,
            role=role,
            frame_seq=frame_seq,
            turn_seq=turns[-1] if turn_seq is None else turn_seq,
            current_binding=current_binding() if callable(current_binding) else current_binding,
        )

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
                    role=role,
                    instruction=collaborative_instruction(
                        instruction, current_role=role, pm_advice=advice.get("pm", "")
                    ),
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

    async def shutdown(self) -> None:
        """Drain this host's actual owners; repeated successful shutdown is inert.

        Owner failures and unknown physical cleanup remain failures. A cancelled
        host waiter still waits for the existing cancellation path to finish.
        """
        owners = tuple(self._tasks.items())
        for session_id, _task in owners:
            self._cancellations[session_id].set()
        for session_id, task in owners:
            cleanup = asyncio.create_task(self.cancel(session_id, workspace=self.workspace))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await asyncio.shield(cleanup)
                raise
            if task.cancelled():
                raise asyncio.CancelledError
            failure = task.exception()
            if failure is not None:
                self._shutdown_error = failure
        if self._retained_locks:
            self._shutdown_error = self._shutdown_error or RuntimeError(
                "engineering shutdown has unresolved physical cleanup; writer locks retained"
            )
        await self.flush_events()
        if self._shutdown_error is not None:
            raise self._shutdown_error

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
        self._publication_loop = asyncio.get_running_loop()
        row = self.observe(session_id, workspace=workspace)
        if row["engine_version"] != self.engine_version:
            raise ValueError("engineering run cannot change engine version on resume")
        from polaris.cells.roles.runtime.internal.engineering_execution_policy import resolve_execution_policy

        resolve_execution_policy(row.get("verification_execution_policy"))
        original_scope = self._revalidated_scope(row)
        if session_id in self._tasks or (
            row["status"] == "completed" and row["verification"] != "unknown" and row.get("qa_verdict") != "unknown"
        ):
            return session_id
        lock = self.store.acquire_workspace(self.workspace)
        lock.__enter__()
        admitted = False
        try:
            # Acquiring the real OS lock first prevents a second interpreter
            # from reconciling effects while their physical owner is alive.
            self.store.reconcile_verification_calls(
                session_id, max_output_bytes=self._limits(row).max_tool_output_bytes
            )
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
                if (
                    frame["role"] == "qa"
                    and row.get("qa_verdict") == "unknown"
                    and self._pending_review(session_id) is None
                ):
                    self.store.continue_role(
                        session_id,
                        role="director",
                        feedback="The current candidate no longer has a valid QA approval. Inspect current inputs and retain committed prior observations; continue the same goal and revalidate before fresh independent review.",
                    )
                if (
                    finished
                    and frame["role"] != "qa"
                    and (frame["role"] == "director" or finished[-1]["payload"].get("status") != "completed")
                ):
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

    def _saved_outputs(self, row: dict[str, Any]) -> tuple[Path, ...]:
        saved = row.get("verification_output_roots")
        if saved is None:
            # Older rows can already carry an exact output declaration in their
            # durable verification binding. Never infer it from today's host.
            completed = self.store.load_events(str(row["session_id"]), kinds=("verification_completed",))
            saved = (
                completed[-1]["payload"].get("input_binding", {}).get("scope", {}).get("output_roots", [])
                if completed
                else []
            )
        roots = tuple(Path(root) for root in saved)
        if not set(roots) <= set(self._writable_output_roots):
            raise PermissionError("saved verification output declarations exceed current host policy")
        return roots

    def _verification(
        self,
        scope: PermissionScope,
        limits: RunLimits,
        cancel: asyncio.Event,
        *,
        output_roots: tuple[Path, ...] | None = None,
    ) -> Any:
        from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification

        owner = self._owner.get()
        return EngineeringVerification(
            self.workspace,
            store=self.store,
            scope=scope,
            limits=limits,
            cancel=cancel,
            writable_output_roots=self._writable_output_roots if output_roots is None else output_roots,
            authorize=(lambda: self._authorize_tools(owner[0].session_id))
            if owner is not None and self._permission_resolver is not None
            else None,
        )

    def _authorize_tools(self, session_id: str) -> None:
        """Recheck current host policy without expanding the saved execution ceiling."""
        self._revalidated_scope(self.store.load_run(session_id, workspace=self.workspace))

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
        commands = tuple(saved["verification_commands"])
        while not commands:
            from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

            enabled = {ToolSpecRegistry.get_canonical(name) for name in scope.enabled_tools}
            if not {"verify_project", "execute_command"} <= enabled or cancel.is_set():
                return result
            if limits.deadline is not None and time.time() >= limits.deadline:
                return replace(result, status="failed")
            result = await self._continue_director(
                command,
                "No current project verification has been executed. Continue the original goal: "
                "choose meaningful actual project checks and call verify_project. "
                "Inspect and repair their actual failures; a text completion is not delivery evidence.",
                scope=scope,
                limits=limits,
                cancel=cancel,
            )
            if result.status != "completed":
                return result
            saved = self.store.load_run(command.session_id, workspace=self.workspace)
            commands = tuple(saved["verification_commands"])
        pending_review = self._pending_review(command.session_id)
        resume_review = False
        while result.status == "completed":
            current = self.store.load_run(command.session_id, workspace=self.workspace)
            # Ordinary Director preparation can materialize declared outputs.
            # A new attempt gets a fresh, then frozen effective write subset.
            verifier = self._verification(scope, limits, cancel, output_roots=self._saved_outputs(current))
            if pending_review is not None:
                resume_review = (
                    pending_review["payload"].get("input_binding") == verifier.review_binding(command.session_id)
                    and verifier.current_verification(command.session_id) == "passed"
                )
                pending_review = None
            commands = tuple(current["verification_commands"])
            revision = int(current["workspace_revision"])
            history = self.store.load_events(command.session_id)
            # A prior implicit attempt cannot turn an accepted native choice
            # into a host-owned recipe. Derive ownership from this same journal.
            model_selected = any(
                event["kind"] == "verification_started"
                and event["payload"].get("input_binding", {}).get("requested_by") is not None
                for event in history
            )
            completed = verifier.current_completed_result(command.session_id)
            if resume_review or completed is not None:
                checks = tuple(ToolObservation(**check) for check in current["checks"])
            else:
                previous = next(
                    (event["payload"] for event in reversed(history) if event["kind"] == "verification_completed"),
                    None,
                )
                uncertain = previous is not None and previous.get("verification") == "unknown"
                if current["verification"] == "unknown" or uncertain:
                    # A resumed text reply is not a new decision to repeat an
                    # uncertain check. Invalidating candidate evidence with a
                    # write/ordinary command does not erase that durable fact.
                    # Only a new explicit native verification can replace it.
                    return replace(
                        result,
                        status="interrupted",
                        verification="unknown",
                        checks=tuple(
                            ToolObservation(**check)
                            for check in (
                                previous["checks"] if uncertain and previous is not None else current["checks"]
                            )
                        ),
                    )
                if model_selected:
                    if limits.deadline is not None and time.time() >= limits.deadline:
                        return replace(result, status="failed", verification=current["verification"])
                    feedback = (
                        "There is no valid verification of the current candidate. Your previous check selection "
                        "will not be replayed automatically. Continue the original goal and explicitly call "
                        "verify_project with meaningful current checks. You may revise your choices; host-required "
                        "commands remain included. Prepare inputs with authorized ordinary tools first.\n"
                        + json.dumps(
                            {"required_checks": current["required_checks"], "verification_commands": commands},
                            ensure_ascii=False,
                        )
                    )
                    result = await self._continue_director(command, feedback, scope=scope, limits=limits, cancel=cancel)
                    if result.status != "completed":
                        current = self.store.load_run(command.session_id, workspace=self.workspace)
                        return replace(
                            result,
                            verification=current["verification"],
                            checks=tuple(ToolObservation(**check) for check in current["checks"]),
                        )
                    resume_review = False
                    continue
                checks = await verifier.run_verification(
                    commands, session_id=command.session_id, workspace_revision=revision
                )
            resume_review = False
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
                result = replace(result, verification=verification, checks=checks)
                if verification == "passed" and not saved["require_qa"]:
                    try:
                        self._require_visual_input(
                            command.session_id,
                            role="director",
                            scope=scope,
                            current_binding=partial(verifier.review_binding, command.session_id),
                        )
                    except ValueError as error:
                        if limits.deadline is not None and time.time() >= limits.deadline:
                            return replace(result, status="failed", text=str(error))
                        # Checks can rebuild declared outputs. Ask for their
                        # resulting pixels only after resolving durable checks;
                        # the next finalization reuses this current receipt.
                        result = await self._continue_director(
                            command,
                            "Current checks already passed. Capture the resulting current candidate with "
                            "browser_observe, inspect the returned images in a later model turn, then finish. "
                            "A new verification is not needed while this candidate and its checks remain current.\n"
                            + str(error),
                            scope=scope,
                            limits=limits,
                            cancel=cancel,
                        )
                        continue
                if not saved["require_qa"] or verification != "passed":
                    return result
                verdict, feedback = await self._review_qa(
                    command, scope=scope, limits=limits, cancel=cancel, verifier=verifier
                )
                if verdict == "approved":
                    return result
                if verdict != "rejected" or cancel.is_set():
                    return replace(result, status="cancelled" if cancel.is_set() else "interrupted")
                result = await self._continue_director(
                    command,
                    "Independent QA rejected the current candidate. Continue the same original goal using actual review findings:\n"
                    + feedback,
                    scope=scope,
                    limits=limits,
                    cancel=cancel,
                )
                if result.status != "completed":
                    current = self.store.load_run(command.session_id, workspace=self.workspace)
                    return replace(
                        result,
                        verification=current["verification"],
                        checks=tuple(ToolObservation(**check) for check in current["checks"]),
                    )
                continue
            if limits.deadline is not None and time.time() >= limits.deadline:
                return replace(result, status="failed", verification="failed", checks=checks)
            actual_results = [
                {
                    "command": actual,
                    "result": {"ok": check.ok, "exit_code": check.exit_code, "content": check.content},
                }
                for actual, check in zip(commands, checks, strict=False)
            ]
            feedback = (
                "Current selected checks failed. Continue the original goal using actual results. "
                "You may revise your selected recipe through verify_project; host-required commands remain "
                "included. Successful current preparation through ordinary authorized tools is preparation "
                "evidence and need not be repeated in read-only verification.\n"
                if model_selected
                else "Final required checks failed. Continue the same task using these actual results:\n"
            ) + json.dumps(
                {
                    "required_checks": current["required_checks"],
                    "verification_commands": commands,
                    "checks": actual_results,
                }
                if model_selected
                else actual_results,
                ensure_ascii=False,
            )
            result = await self._continue_director(command, feedback, scope=scope, limits=limits, cancel=cancel)
            if result.status != "completed":
                current = self.store.load_run(command.session_id, workspace=self.workspace)
                return replace(
                    result,
                    verification=current["verification"],
                    checks=tuple(ToolObservation(**check) for check in current["checks"]),
                )
        return result

    async def _continue_director(
        self,
        command: RoleRunCommand,
        feedback: str,
        *,
        scope: PermissionScope,
        limits: RunLimits,
        cancel: asyncio.Event,
    ) -> RoleRunResult:
        """Reopen the same task with its native history, advice and actual feedback."""
        self.store.continue_role(command.session_id, role="director", feedback=feedback)
        starts = [
            event
            for event in self.store.load_events(command.session_id)
            if event["kind"] == "role_started" and event["role"] == "director"
        ]
        return await self.run_role(
            replace(command, role="director", instruction=starts[-1]["payload"]["instruction"], workflow="direct"),
            scope=scope,
            limits=limits,
            cancel=cancel,
        )

    def _pending_review(self, session_id: str) -> dict[str, Any] | None:
        pending = None
        for event in self.store.load_events(session_id):
            if event["kind"] == "qa_review_started":
                pending = event
            elif event["kind"] == "qa_review_completed":
                pending = None
        return pending

    async def _review_qa(
        self,
        command: RoleRunCommand,
        *,
        scope: PermissionScope,
        limits: RunLimits,
        cancel: asyncio.Event,
        verifier: Any,
    ) -> tuple[str, str]:
        """Consume only current-frame observed QA reports, independently bound by the host."""
        from polaris.cells.roles.runtime.internal.engineering_roles import interpret_review_reports

        row = self.store.load_run(command.session_id, workspace=self.workspace)
        self._revalidated_scope(row)
        binding = verifier.review_binding(command.session_id)
        instruction = (
            "Original user goal:\n"
            + row["instruction"]
            + "\n\nCurrent actual required checks (these do not establish semantic correctness):\n"
            + json.dumps(
                [
                    {"command": name, "result": check}
                    for name, check in zip(row["verification_commands"], row["checks"], strict=False)
                ],
                ensure_ascii=False,
            )
            + "\n\nRead the current relevant implementation using native read tools. Check the original requirements, behavior and actual artifacts, including incomplete or hollow implementations. "
            "Then submit report_review with a justified decision. Do not treat Director text as evidence."
        )
        pending = self._pending_review(command.session_id)
        reusable = pending is not None and pending["payload"].get("input_binding") == binding
        if not reusable:
            self.store.append_event(
                command.session_id, kind="qa_review_started", role="qa", payload={"input_binding": binding}
            )
        events = self.store.load_events(command.session_id)
        starts = [event for event in events if event["kind"] == "role_started" and event["role"] == "qa"]
        durable_reports = [
            event
            for event in events
            if reusable
            and starts
            and event["seq"] > starts[-1]["seq"]
            and event["kind"] == "tool_observed"
            and event["role"] == "qa"
            and event["payload"].get("tool") == "report_review"
            and event["payload"].get("ok") is True
            and not event["payload"].get("interrupted")
        ]
        if durable_reports:
            _verdict, feedback, _call_id = interpret_review_reports([event["payload"] for event in durable_reports])
            result = RoleRunResult(command.session_id, "qa", feedback, "completed", "not_run", ())
            self.store.append_event(
                command.session_id,
                kind="role_finished",
                role="qa",
                payload={
                    "frame_seq": starts[-1]["seq"],
                    "status": "completed",
                    "text": result.text,
                    "continuation_basis": "durable_native_report",
                },
            )
        else:
            result = await self.run_role(
                replace(command, role="qa", instruction=instruction, workflow="direct", required_checks=()),
                scope=scope,
                limits=limits,
                cancel=cancel,
            )
        events = self.store.load_events(command.session_id)
        frame = next(event for event in reversed(events) if event["kind"] == "role_started" and event["role"] == "qa")
        reports = [
            event
            for event in events
            if event["kind"] == "tool_observed"
            and event["role"] == "qa"
            and event["seq"] > frame["seq"]
            and event["payload"].get("tool") == "report_review"
            and event["payload"].get("ok") is True
            and not event["payload"].get("interrupted")
        ]
        verdict, feedback = "unknown", result.text
        report_call_id = None
        try:
            self._revalidated_scope(row)
            if (
                result.status == "completed"
                and reports
                and binding == verifier.review_binding(command.session_id)
                and verifier.current_verification(command.session_id) == "passed"
            ):
                verdict, feedback, report_call_id = interpret_review_reports([event["payload"] for event in reports])
                if verdict == "approved":
                    report = next(
                        event for event in reversed(reports) if event["payload"].get("call_id") == report_call_id
                    )
                    self._require_visual_input(
                        command.session_id,
                        role="qa",
                        scope=scope,
                        current_binding=lambda: verifier.review_binding(command.session_id),
                        frame_seq=frame["seq"],
                        turn_seq=report["payload"].get("turn_seq"),
                    )
        except (ValueError, OSError, RuntimeError, PathSecurityError) as error:
            verdict = "unknown"
            feedback = f"Current QA evidence cannot establish acceptance: {error}"
        self.store.append_event(
            command.session_id,
            kind="qa_review_completed",
            role="qa",
            payload={
                "verdict": verdict,
                "feedback": feedback,
                "frame_seq": frame["seq"],
                "report_call_id": report_call_id,
                "input_binding": binding,
            },
        )
        return verdict, feedback

    def _revalidated_scope(self, row: dict[str, Any]) -> PermissionScope:
        """Re-admit saved grants against current host policy; never infer authority."""
        if self._permission_resolver is None:
            raise PermissionError("saved execution requires current permission policy")
        self._saved_outputs(row)
        original_scope = self._scope(row)
        current_scope = self._permission_resolver(row)
        from polaris.kernelone.process import resolve_toolchain_bin_dirs

        saved_bins = resolve_toolchain_bin_dirs(
            original_scope.toolchain_bin_dirs,
            workspace=self.workspace,
            read_roots=original_scope.read_roots,
            write_roots=original_scope.write_roots,
        )
        if saved_bins != original_scope.toolchain_bin_dirs:
            raise PermissionError("saved toolchain directories changed canonical identity")
        current_scope = replace(
            current_scope,
            toolchain_bin_dirs=resolve_toolchain_bin_dirs(
                current_scope.toolchain_bin_dirs,
                workspace=self.workspace,
                read_roots=current_scope.read_roots,
                write_roots=current_scope.write_roots,
            ),
        )
        if not self._scope_within(original_scope, current_scope) or original_scope.browser != current_scope.browser:
            raise PermissionError("saved scope exceeds current permission policy")
        return original_scope

    def _uncommitted_final_checks(self, session_id: str) -> tuple[ToolObservation, ...] | None:
        """Retain actual final checks when their acceptance transaction did not commit.

        The explicit verification turn owns these durable observations. Ordinary
        Director commands and earlier completed attempts are not final checks.
        No command is repeated and no current-input acceptance binding is inferred.
        """
        events = self.store.load_events(session_id)
        attempts = [event for event in events if event["kind"] == "verification_started"]
        if not attempts:
            return None
        attempt = attempts[-1]
        later = [event for event in events if event["seq"] > attempt["seq"]]
        if any(event["kind"] in {"verification_completed", "role_started"} for event in later):
            return None
        checks = []
        for event in later:
            payload = event["payload"]
            if (
                event["kind"] == "tool_observed"
                and payload.get("turn_seq") == attempt["seq"]
                and payload.get("tool") == "execute_command"
            ):
                checks.append(
                    ToolObservation(
                        payload["call_id"],
                        payload["tool"],
                        payload["ok"],
                        payload["content"],
                        payload["exit_code"],
                        payload["interrupted"],
                        timed_out=payload.get("timed_out", False),
                    )
                )
        return tuple(checks)

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
            client = (
                self._client
                if self._client is not None
                else _native_client(self.workspace, limits, self._observe_native_request)
            )
            token = self._owner.set((command, scope, limits, cancel, client))
            try:
                saved = self.store.load_run(command.session_id, workspace=self.workspace)
                pending_review = self._pending_review(command.session_id)
                resume_review = False
                if saved["require_qa"] and pending_review is not None:
                    verifier = self._verification(scope, limits, cancel, output_roots=self._saved_outputs(saved))
                    resume_review = (
                        pending_review["payload"].get("input_binding") == verifier.review_binding(command.session_id)
                        and verifier.current_verification(command.session_id) == "passed"
                    )
                    if not resume_review:
                        self.store.append_event(
                            command.session_id,
                            kind="qa_review_completed",
                            role="qa",
                            payload={
                                "verdict": "unknown",
                                "feedback": "Saved QA inputs changed before review closure; historical observations cannot accept the current candidate.",
                                "input_binding": pending_review["payload"].get("input_binding"),
                                "report_call_id": None,
                            },
                        )
                        frames = [
                            event
                            for event in self.store.load_events(command.session_id)
                            if event["kind"] == "role_started" and event["role"] == "qa"
                        ]
                        if frames:
                            self.store.append_event(
                                command.session_id,
                                kind="role_finished",
                                role="qa",
                                payload={
                                    "frame_seq": frames[-1]["seq"],
                                    "status": "interrupted",
                                    "text": "Saved QA inputs changed.",
                                },
                            )
                        self.store.continue_role(
                            command.session_id,
                            role="director",
                            feedback="Current workspace or verification inputs changed after a saved QA attempt. Continue the same goal from committed observations, inspect current files, and revalidate; old QA reports do not apply.",
                        )
                if resume_review:
                    result = RoleRunResult(
                        command.session_id,
                        "director",
                        saved["text"],
                        "completed",
                        saved["verification"],
                        tuple(ToolObservation(**check) for check in saved["checks"]),
                    )
                elif collaborative and command.workflow == "collaborative":
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
            actual_checks = self._uncommitted_final_checks(command.session_id)
            result = RoleRunResult(
                command.session_id,
                command.role,
                f"{type(exc).__name__}: {exc}",
                "interrupted" if pending else "failed",
                "unknown" if pending or actual_checks is not None else "not_run",
                actual_checks or (),
            )
        try:
            if result.status != "completed":
                current = self.store.load_run(command.session_id, workspace=self.workspace)
                if current["verification"] != "not_run":
                    # Native verification can finish inside the role before a
                    # cancellation/provider error ends that role. Keep its real
                    # durable facts; the role reply does not own check results.
                    result = replace(
                        result,
                        verification=current["verification"],
                        checks=tuple(ToolObservation(**check) for check in current["checks"]),
                    )
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
            self._shutdown_error = journal_error
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
        browser = scope.get("browser")
        if browser is not None:
            try:
                browser = BrowserObservationConfig(**{**browser, "python_paths": tuple(browser["python_paths"])})
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("saved browser configuration is invalid") from error
        reads = tuple(Path(root) for root in scope["read_roots"])
        writes = tuple(Path(root) for root in scope["write_roots"])
        raw_bins = scope.get("toolchain_bin_dirs", ())
        if not isinstance(raw_bins, (list, tuple)):
            raise ValueError("saved toolchain directories must be an ordered path sequence")
        bins = []
        for value in raw_bins:
            if not isinstance(value, str) or not value or "\0" in value or ":" in value:
                raise ValueError("saved toolchain directory is invalid")
            path = Path(value)
            if not path.is_absolute() or str(path) != value or ".." in path.parts:
                raise ValueError("saved toolchain directory must be a canonical absolute path")
            # Decode the saved fact without following today's filesystem.
            # Recovery and every effect revalidate existence/mount authority.
            bins.append(path)
        return PermissionScope(
            reads,
            writes,
            frozenset(scope["enabled_tools"]),
            scope["network_allowed"],
            browser,
            toolchain_bin_dirs=tuple(bins),
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

        # PATH priority is an ordered authority. Retain original bins only;
        # a new profile may add directories but cannot reorder/revoke saved ones.
        remaining = iter(current.toolchain_bin_dirs)
        toolchains_within = all(any(path == granted for granted in remaining) for path in saved.toolchain_bin_dirs)
        return (
            toolchains_within
            and {ToolSpecRegistry.get_canonical(name) for name in saved.enabled_tools}
            <= {ToolSpecRegistry.get_canonical(name) for name in current.enabled_tools}
            and (not saved.network_allowed or current.network_allowed)
            and roots_within(saved.read_roots, current.read_roots)
            and roots_within(saved.write_roots, current.write_roots)
            and (saved.browser is None or saved.browser == current.browser)
        )
