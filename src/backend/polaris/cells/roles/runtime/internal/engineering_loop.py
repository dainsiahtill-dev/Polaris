"""One native-tool loop for each professional role; no model-maintained protocol."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any, Literal

from polaris.cells.roles.runtime.internal.engineering_roles import register_consult_tool, role_instruction, role_scope
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools, bounded_output
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    PermissionScope,
    RoleRunCommand,
    RoleRunResult,
    RunLimits,
    ToolObservation,
)
from polaris.kernelone.llm.contracts.tool import ToolCall
from polaris.kernelone.llm.engine.client import ResilientLLMClient
from polaris.kernelone.llm.engine.contracts import AIRequest, AIResponse, TaskType
from polaris.kernelone.llm.engine.invocation_budget import invoke_with_budget
from polaris.kernelone.llm.toolkit.parsers.core import parse_tool_calls
from polaris.kernelone.process.process_tree import ProcessTreeDrainError


def _native_calls(response: AIResponse) -> list[ToolCall]:
    """Validate the complete native envelope before any tool can have effects."""
    raw = response.raw
    if not isinstance(raw, dict):
        raise ValueError("native provider response is missing its raw object")
    calls: Any = []
    terminal: Any = None
    if "choices" in raw:
        choices = raw["choices"]
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ValueError("native provider response requires one choice")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ValueError("native provider response has no assistant message")
        calls = message.get("tool_calls", [])
        terminal = choice.get("finish_reason")
    elif "content" in raw:
        blocks = raw["content"]
        if not isinstance(blocks, list) or any(not isinstance(block, dict) for block in blocks):
            raise ValueError("native provider content must be an object list")
        calls = [block for block in blocks if block.get("type") == "tool_use"]
        terminal = raw.get("stop_reason")
    else:
        raise ValueError("unsupported native provider response envelope")
    if not isinstance(calls, list):
        raise ValueError("native tool_calls must be a list")
    if terminal in {"tool_calls", "tool_use"} and not calls:
        raise ValueError("native provider announced tools without complete calls")
    if calls and terminal not in {"tool_calls", "tool_use", "stop", "end_turn"}:
        raise ValueError(f"native tool turn is incomplete: {terminal}")
    identities: set[str] = set()
    for call in calls:
        if not isinstance(call, dict):
            raise ValueError("native tool_calls contains a non-object entry")
        if call.get("type") not in {"function", "tool_use"}:
            raise ValueError("native tool_calls contains an unsupported envelope")
        identity = call.get("id")
        if not isinstance(identity, str) or not identity.strip() or identity in identities:
            raise ValueError("native tool turn has a missing, invalid, or duplicate call id")
        identities.add(identity)
    parsed = parse_tool_calls(tool_calls=calls, allow_text_fallback=False)
    if len(parsed) != len(calls) or any(call.parse_error for call in parsed):
        detail = "; ".join(call.parse_error for call in parsed if call.parse_error)
        raise ValueError(f"native tool turn rejected: {detail or 'incomplete parser result'}")
    return parsed


async def _invoke(
    client: ResilientLLMClient, request: AIRequest, cancel: asyncio.Event, deadline: float | None
) -> AIResponse:
    invocation = client.invoke(request)
    if deadline is not None:
        invocation = invoke_with_budget(invocation, max(0.001, deadline - time.time()))
    worker = asyncio.create_task(invocation)
    cancellation = asyncio.create_task(cancel.wait())
    try:
        timeout = max(0.0, deadline - time.time()) if deadline is not None else None
        done, _pending = await asyncio.wait(
            {worker, cancellation}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if cancellation in done or not done:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            if cancel.is_set():
                raise asyncio.CancelledError
            raise TimeoutError("engineering provider deadline exceeded")
        return await worker
    except asyncio.CancelledError:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        raise
    finally:
        cancellation.cancel()
        await asyncio.gather(cancellation, return_exceptions=True)


async def run_engineering_loop(
    command: RoleRunCommand,
    *,
    scope: PermissionScope,
    limits: RunLimits,
    cancel: asyncio.Event,
    store: EngineeringSessionStore,
    client: ResilientLLMClient,
    consult: Callable[[str, str, int, str], Awaitable[str]] | None = None,
    verify: Callable[[tuple[str, ...], int, str], Awaitable[ToolObservation]] | None = None,
    pending_ancestors: frozenset[tuple[int, str]] = frozenset(),
    project_run: bool = True,
    purpose: str = "role",
) -> RoleRunResult:
    """Run one role while the public service holds the workspace writer lock.

    Prepared requests below are auxiliary input evidence, not the final physical
    provider body. ContextOS transport-body closure is a separate integration.
    No controller, lock, or second journal is created here.
    """
    from polaris.cells.roles.runtime.internal.engineering_roles import (
        interpret_review_reports,
        register_review_tool,
        register_verification_tool,
    )

    register_consult_tool()
    register_review_tool()
    register_verification_tool()
    scope = role_scope(command.role, scope)
    active_dispatch: tuple[int, str] | None = None

    async def consult_current(role: str, question: str) -> str:
        if consult is None or active_dispatch is None:
            raise ValueError("consultation requires an active tool dispatch")
        return await consult(role, question, *active_dispatch)

    async def verify_current(commands: tuple[str, ...]) -> ToolObservation:
        if verify is None or active_dispatch is None or command.role != "director":
            raise ValueError("verify_project requires an active Director dispatch")
        return await verify(commands, *active_dispatch)

    def review_current() -> None:
        if command.role != "qa" or active_dispatch is None:
            raise ValueError("report_review requires an active QA dispatch")
        if not any(
            event["kind"] == "tool_observed"
            and event["role"] == "qa"
            and event["seq"] > frame_seq
            and (
                event["payload"].get("tool") == "read_file"
                or (event["payload"].get("tool") == "repo_rg" and str(event["payload"].get("content", "")).strip())
            )
            and event["payload"].get("ok") is True
            and not event["payload"].get("interrupted")
            and event["payload"].get("turn_seq", 0) < active_dispatch[0]
            for event in store.load_events(command.session_id)
        ):
            raise ValueError("report_review requires a successful current-frame source read in a previous turn")

    tools = EngineeringTools(
        command.workspace,
        scope,
        limits,
        cancel,
        consult=consult_current if consult is not None and command.role == "director" else None,
        review=review_current if command.role == "qa" else None,
        verify=verify_current if verify is not None and command.role == "director" else None,
    )
    events = store.load_events(command.session_id)
    starts = [event for event in events if event["kind"] == "role_started" and event["role"] == command.role]
    last_start = starts[-1] if starts else None
    unfinished = last_start is not None and not any(
        event["kind"] == "role_finished" and event["role"] == command.role and event["seq"] > last_start["seq"]
        for event in events
    )
    if (
        unfinished
        and last_start is not None
        and last_start["payload"].get("instruction") == command.instruction
        and last_start["payload"].get("purpose") == purpose
    ):
        frame_seq = last_start["seq"]
        messages = store.load_message_history(command.session_id, role=command.role, after_seq=frame_seq)
    else:
        frame_seq = store.append_event(
            command.session_id,
            kind="role_started",
            role=command.role,
            payload={"instruction": command.instruction, "purpose": purpose},
        )
        messages = []
    if not messages:
        messages = [
            {"role": "system", "content": role_instruction(command.role)},
            {"role": "user", "content": command.instruction},
        ]
        for message in messages:
            store.append_message(command.session_id, role=command.role, message=message)
    if project_run:
        store.update_run(command.session_id, status="running")
    output_bytes = 0

    def pending_calls() -> list[dict[str, Any]]:
        return [
            call
            for call in store.pending_tool_calls(command.session_id)
            if (call["turn_seq"], call["call_id"]) not in pending_ancestors
        ]

    def finish(status: Literal["completed", "failed", "cancelled", "interrupted"], text: str) -> RoleRunResult:
        pending = pending_calls()
        if pending:
            # Dispatch is durable before an effect; only a durable observation
            # can close it. Exceptions/cancellation cannot prove no effect.
            status = "interrupted"
            text = (
                f"{len(pending)} dispatched tool call(s) have no durable observation; effects are unknown. "
                f"Reconcile actual effects before continuing; automatic replay is blocked. {text}"
            )
        result = RoleRunResult(
            command.session_id,
            command.role,
            bounded_output(text, limits.max_output_bytes),
            status,
            "unknown" if status == "interrupted" else "not_run",
            (),
        )
        store.append_event(
            command.session_id,
            kind="role_finished",
            role=command.role,
            payload={"frame_seq": frame_seq, **asdict(result)},
        )
        if project_run:
            store.update_run(
                command.session_id, status=result.status, text=result.text, verification=result.verification, checks=()
            )
        return result

    try:
        if pending_calls():
            return finish("interrupted", "An earlier dispatch still requires reconciliation.")
        while True:
            if cancel.is_set():
                return finish("cancelled", "Engineering role cancelled; no further tools dispatched.")
            if limits.deadline is not None and time.time() >= limits.deadline:
                return finish("failed", "Engineering run deadline exceeded.")
            turn_seq = store.append_event(command.session_id, kind="turn_started", role=command.role, payload={})
            options: dict[str, Any] = {"messages": list(messages), "tools": tools.schemas(), "tool_choice": "auto"}
            request = AIRequest(
                task_type=TaskType.DIALOGUE,
                role=command.role,
                input=command.instruction,
                options=options,
                context={
                    "session_id": command.session_id,
                    "workspace": str(command.workspace),
                    "engine_version": "engineering_v2",
                    "turn_seq": turn_seq,
                    "call_id": f"{command.session_id}:{command.role}:{turn_seq}",
                },
            )
            store.append_event(
                command.session_id,
                kind="request_prepared",
                role=command.role,
                payload={
                    "turn_seq": turn_seq,
                    "evidence_kind": "auxiliary_prepared_request",
                    "request": request.to_dict(),
                },
            )
            response = await _invoke(client, request, cancel, limits.deadline)
            store.append_event(
                command.session_id,
                kind="provider_response",
                role=command.role,
                payload={
                    "turn_seq": turn_seq,
                    "ok": response.ok,
                    "raw": response.raw,
                    "error": response.error,
                    "model": response.model,
                    "provider_id": response.provider_id,
                    "trace_id": response.trace_id,
                },
            )
            if cancel.is_set():
                return finish("cancelled", "Engineering role cancelled before tool dispatch.")
            if not response.ok:
                return finish("failed", f"Native provider failed: {response.error or 'unspecified provider error'}")
            calls = _native_calls(response)
            output_bytes += len(response.output.encode("utf-8"))
            if output_bytes > limits.max_output_bytes:
                return finish("failed", "Engineering role output budget exceeded before tool dispatch.")
            assistant: dict[str, Any] = {"role": "assistant", "content": response.output}
            if calls:
                assistant["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)},
                    }
                    for call in calls
                ]
            messages.append(assistant)
            store.append_message(command.session_id, role=command.role, message=assistant)
            if not calls:
                store.append_event(
                    command.session_id, kind="turn_finished", role=command.role, payload={"turn_seq": turn_seq}
                )
                return finish("completed", response.output)
            review_reports: list[dict[str, Any]] = []
            for call in calls:
                if cancel.is_set():
                    return finish(
                        "cancelled", "Engineering role cancelled; remaining native calls were not dispatched."
                    )
                store.record_tool_dispatch(
                    command.session_id,
                    turn_seq=turn_seq,
                    call_id=call.id,
                    tool=call.name,
                    arguments=call.arguments,
                    role=command.role,
                )
                active_dispatch = (turn_seq, call.id)
                try:
                    observation = await tools.execute(call.id, call.name, call.arguments)
                finally:
                    active_dispatch = None
                store.record_tool_observation(
                    command.session_id, turn_seq=turn_seq, observation=observation, role=command.role
                )
                if observation.tool == "report_review" and observation.ok and not observation.interrupted:
                    review_reports.append(asdict(observation))
                if (observation.tool in {"write_file", "edit_file"} and observation.ok) or (
                    observation.tool == "execute_command"
                    and (observation.exit_code is not None or observation.interrupted)
                ):
                    run = store.load_run(command.session_id, workspace=command.workspace)
                    store.update_run(
                        command.session_id,
                        workspace_revision=int(run["workspace_revision"]) + 1,
                        verification="not_run",
                        checks=(),
                    )
                message = {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(asdict(observation), ensure_ascii=False),
                }
                messages.append(message)
                store.append_message(command.session_id, role=command.role, message=message)
                if observation.interrupted or pending_calls():
                    # A nested read role can also lose a durable observation.
                    # No later parent effect may run while that dispatch is unknown.
                    return finish("cancelled" if cancel.is_set() else "interrupted", observation.content)
            store.append_event(
                command.session_id, kind="turn_finished", role=command.role, payload={"turn_seq": turn_seq}
            )
            if review_reports:
                _verdict, feedback, _call_id = interpret_review_reports(review_reports)
                return finish("completed", feedback)
    except asyncio.CancelledError:
        cancel.set()
        return finish("cancelled", "Engineering role cancelled after active execution cleanup.")
    except ProcessTreeDrainError as drain_error:
        # Preserve the unresolved started tool and let the physical owner keep
        # the run blocked. An unknown process effect is not a failed test.
        try:
            store.append_event(command.session_id, kind="execution_drain_unproved", role=command.role, payload={})
            finish("interrupted", "Physical execution cleanup could not be proved.")
        except BaseException as journal_error:
            # Reporting failure cannot turn an unproved physical drain into an
            # ordinary exception for which the owner may release its writer.
            raise drain_error from journal_error
        raise
    except Exception as exc:  # noqa: BLE001 - report a provider/tool failure at the role boundary, never retry effects.
        store.append_event(
            command.session_id,
            kind="role_error",
            role=command.role,
            payload={"error_type": type(exc).__name__, "error": str(exc)},
        )
        return finish("failed", f"{type(exc).__name__}: {exc}")
