"""Freeze, persist, pin, and account for each physical provider dispatch."""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import suppress
from typing import Any, AsyncContextManager, Literal, TypeVar

from polaris.cells.roles.kernel.public.physical_attempt_control import (
    ABORT_FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
    BEGIN_FACTORY_PHYSICAL_ATTEMPT_START_SCHEMA,
    COMMIT_FACTORY_PHYSICAL_ATTEMPT_START_SCHEMA,
    FAIL_FACTORY_PHYSICAL_ATTEMPT_TERMINAL_SCHEMA,
    MARK_FACTORY_PHYSICAL_ATTEMPT_START_AMBIGUOUS_SCHEMA,
    RESERVE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
    SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
    AbortFactoryPhysicalAttemptReservationV1,
    BeginFactoryPhysicalAttemptStartV1,
    CommitFactoryPhysicalAttemptStartV1,
    FactoryPhysicalAttemptControlPort,
    FactoryPhysicalAttemptDefiniteStartNotPersistedProofV1,
    FactoryPhysicalAttemptLeaseV1,
    FactoryPhysicalAttemptReservationV1,
    FactoryPhysicalAttemptStartPermitV1,
    FailFactoryPhysicalAttemptTerminalV1,
    MarkFactoryPhysicalAttemptStartAmbiguousV1,
    ProviderAttemptStartReceiptV1,
    ProviderAttemptTerminalReceiptV1,
    ReserveFactoryPhysicalAttemptV1,
    SettleFactoryPhysicalAttemptV1,
)
from polaris.kernelone.events.final_request_evidence import (
    ContextSnapshotAuditPinV1,
    canonical_final_request_hash,
    redact_provider_transport,
)
from polaris.kernelone.llm.engine.context_store_retention import ContextSnapshotAuditPinRepository
from polaris.kernelone.llm.engine.contracts import (
    FrozenFinalProviderAttemptV1,
    ProviderAttemptInFlightDrainPort,
)

from .final_provider_attempt_inflight import ProviderAttemptInFlightCoordinator
from .final_provider_attempt_lifecycle import StrictProviderAttemptLifecycleStore
from .final_provider_attempt_qualification import _FinalProviderAttemptQualificationProofV1

_ResultT = TypeVar("_ResultT")
_StreamResponseT = TypeVar("_StreamResponseT")
_MAX_BUFFERED_STREAM_EVENTS = 4096
_MAX_BUFFERED_STREAM_EVENTS_PER_OUTPUT_TOKEN = 4
_MAX_BUFFERED_STREAM_EVENTS_HARD = 65_536
_MAX_BUFFERED_STREAM_BYTES = 8 * 1024 * 1024
_STREAM_CONTEXT_CLEANUP_TIMEOUT_SECONDS = 5.0
_STREAM_CONTEXT_CLEANUP_CANCEL_GRACE_SECONDS = 1.0


def _buffered_stream_item_bytes(item: object) -> int:
    try:
        encoded = json.dumps(
            item,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise RuntimeError("factory_provider_stream_buffer_item_unserializable") from exc
    return len(encoded)


def _qualified_stream_event_limit(wire_request: Mapping[str, Any]) -> int:
    """Bound structured stream events from the exact physical output budget."""

    body = wire_request.get("body")
    if not isinstance(body, Mapping):
        return _MAX_BUFFERED_STREAM_EVENTS
    raw_budget = next(
        (
            body.get(key)
            for key in ("max_tokens", "max_output_tokens", "max_completion_tokens")
            if body.get(key) is not None
        ),
        None,
    )
    if isinstance(raw_budget, bool) or not isinstance(raw_budget, int):
        return _MAX_BUFFERED_STREAM_EVENTS
    if raw_budget <= 0:
        return _MAX_BUFFERED_STREAM_EVENTS
    scaled_limit = raw_budget * _MAX_BUFFERED_STREAM_EVENTS_PER_OUTPUT_TOKEN
    return min(
        _MAX_BUFFERED_STREAM_EVENTS_HARD,
        max(_MAX_BUFFERED_STREAM_EVENTS, scaled_limit),
    )


class DurableFinalProviderAttemptSnapshotStore:
    """Content-addressed snapshot plus permanent Factory audit pin."""

    def __init__(self, workspace: str) -> None:
        self._repository = ContextSnapshotAuditPinRepository(workspace=workspace)

    def persist_and_pin(self, attempt: FrozenFinalProviderAttemptV1) -> ContextSnapshotAuditPinV1:
        if attempt.verification_scope != "factory" or not attempt.factory_run_id:
            raise RuntimeError("Factory governed snapshot pin requires a factory run")
        snapshot = {
            "schema_version": "llm.provider_request_snapshot.v2",
            "provider_request_id": attempt.provider_request_id,
            "request_freeze_id": attempt.request_freeze_id,
            "factory_run_id": attempt.factory_run_id,
            "run_id": attempt.run_id,
            "turn_id": attempt.turn_id,
            "call_id": attempt.call_id,
            "role": attempt.role,
            "provider": attempt.provider,
            "model": attempt.model,
            "execution_authority_hash": attempt.execution_authority_hash,
            "attempt_budget": attempt.attempt_budget,
            "authority_attempt_ordinal": attempt.authority_attempt_ordinal,
            "semantic_request_hash": attempt.semantic_request_hash,
            "physical_wire_hash": attempt.physical_wire_hash,
            "composite_request_hash": attempt.composite_request_hash,
            "durable_view": attempt.durable_copy(),
        }
        return self._repository.persist_snapshot_and_pin(
            snapshot=snapshot,
            factory_run_id=attempt.factory_run_id,
            role=attempt.role,
            verification_scope=attempt.verification_scope,
            request_freeze_id=attempt.request_freeze_id,
            provider_request_id=attempt.provider_request_id,
            composite_request_hash=attempt.composite_request_hash,
            snapshot_source="roles.kernel.final_provider_attempt",
        )


class FinalProviderAttemptGate:
    """Run-bound physical-dispatch port; one call equals one physical attempt."""

    def __init__(
        self,
        *,
        workspace: str,
        verification_scope: Literal["factory", "role_session"],
        factory_run_id: str,
        scope_id: str | None = None,
        run_id: str,
        role: str,
        turn_id: str,
        call_id: str,
        request_freeze_id: str,
        provider: str,
        model: str,
        semantic_request: Mapping[str, Any],
        lifecycle: StrictProviderAttemptLifecycleStore | None,
        snapshot_store: Any,
        drain_coordinator: ProviderAttemptInFlightCoordinator | None = None,
        physical_attempt_control_port: FactoryPhysicalAttemptControlPort | None = None,
        execution_authority_hash: str = "",
        attempt_budget: int = 0,
        qualification_proof: _FinalProviderAttemptQualificationProofV1 | None = None,
    ) -> None:
        self._workspace = str(workspace)
        self._verification_scope = verification_scope
        self._factory_run_id = str(factory_run_id)
        self._scope_id = self._resolve_scope_id(scope_id)
        self._run_id = str(run_id)
        self._role = str(role)
        self._turn_id = str(turn_id)
        self._call_id = str(call_id)
        self._request_freeze_id = str(request_freeze_id)
        self._provider = str(provider)
        self._model = str(model)
        self._semantic_request = self._json_copy(semantic_request)
        self._lifecycle = lifecycle
        self._snapshot_store = snapshot_store
        self._physical_attempt_control_port = physical_attempt_control_port
        self._execution_authority_hash = str(execution_authority_hash)
        self._attempt_budget = attempt_budget
        self._qualification_context_snapshot_ref: str
        self._final_request_context_audit: dict[str, Any] | None
        self._qualification_proof: _FinalProviderAttemptQualificationProofV1 | None
        if self._verification_scope == "factory":
            if type(qualification_proof) is not _FinalProviderAttemptQualificationProofV1:
                raise RuntimeError("final_provider_attempt_qualification_proof_required")
            try:
                qualification_proof.validate_gate_binding(
                    workspace=self._workspace,
                    factory_run_id=self._factory_run_id,
                    run_id=self._run_id,
                    role=self._role,
                    turn_id=self._turn_id,
                    call_id=self._call_id,
                    request_freeze_id=self._request_freeze_id,
                    provider=self._provider,
                    model=self._model,
                    semantic_request=self._semantic_request,
                )
                self._qualification_context_snapshot_ref = qualification_proof.context_snapshot_ref
                self._final_request_context_audit = qualification_proof.audit()
                self._qualification_proof = qualification_proof
            except (RuntimeError, TypeError, ValueError) as exc:
                raise RuntimeError("final_provider_attempt_qualification_proof_invalid") from exc
            if not isinstance(self._physical_attempt_control_port, FactoryPhysicalAttemptControlPort):
                raise RuntimeError("factory_physical_attempt_control_port_required")
            if not isinstance(self._physical_attempt_control_port, ProviderAttemptInFlightDrainPort):
                raise RuntimeError("factory_physical_attempt_coordinator_scope_mismatch")
            if drain_coordinator is not None:
                raise RuntimeError("factory_physical_attempt_coordinator_scope_mismatch")
            self._drain_coordinator: ProviderAttemptInFlightDrainPort = self._physical_attempt_control_port
            if len(self._execution_authority_hash) != 64 or any(
                character not in "0123456789abcdef" for character in self._execution_authority_hash
            ):
                raise RuntimeError("factory_physical_attempt_execution_authority_hash_mismatch")
            if type(self._attempt_budget) is not int or self._attempt_budget <= 0:
                raise RuntimeError("factory_physical_attempt_budget_mismatch")
        else:
            if qualification_proof is not None:
                raise RuntimeError("role_session_factory_qualification_proof_forbidden")
            self._qualification_context_snapshot_ref = ""
            self._final_request_context_audit = None
            self._qualification_proof = None
            if (
                self._physical_attempt_control_port is not None
                or self._execution_authority_hash
                or self._attempt_budget != 0
            ):
                raise RuntimeError("role_session_factory_physical_attempt_authority_forbidden")
            if type(drain_coordinator) is not ProviderAttemptInFlightCoordinator:
                raise RuntimeError("provider attempt drain coordinator scope mismatch")
            self._drain_coordinator = drain_coordinator
        if (
            self._drain_coordinator.verification_scope != self._verification_scope
            or self._drain_coordinator.scope_id != self._scope_id
        ):
            raise RuntimeError("provider attempt drain coordinator scope mismatch")
        self._validate_role_identity()

    @classmethod
    def for_factory_run(
        cls,
        *,
        workspace: str,
        factory_run_id: str,
        run_id: str,
        role: str,
        turn_id: str,
        call_id: str,
        request_freeze_id: str,
        provider: str,
        model: str,
        semantic_request: Mapping[str, Any],
        lifecycle: StrictProviderAttemptLifecycleStore | None = None,
        snapshot_store: Any | None = None,
        physical_attempt_control_port: FactoryPhysicalAttemptControlPort,
        execution_authority_hash: str,
        attempt_budget: int,
        qualification_proof: _FinalProviderAttemptQualificationProofV1,
    ) -> FinalProviderAttemptGate:
        return cls(
            workspace=workspace,
            verification_scope="factory",
            factory_run_id=factory_run_id,
            scope_id=factory_run_id,
            run_id=run_id,
            role=role,
            turn_id=turn_id,
            call_id=call_id,
            request_freeze_id=request_freeze_id,
            provider=provider,
            model=model,
            semantic_request=semantic_request,
            lifecycle=lifecycle,
            snapshot_store=snapshot_store or DurableFinalProviderAttemptSnapshotStore(workspace),
            drain_coordinator=None,
            physical_attempt_control_port=physical_attempt_control_port,
            execution_authority_hash=execution_authority_hash,
            attempt_budget=attempt_budget,
            qualification_proof=qualification_proof,
        )

    @classmethod
    def for_role_session(
        cls,
        *,
        workspace: str,
        role_session_id: str,
        run_id: str,
        role: str,
        turn_id: str,
        call_id: str,
        request_freeze_id: str,
        provider: str,
        model: str,
        semantic_request: Mapping[str, Any],
        snapshot_store: Any,
        lifecycle: StrictProviderAttemptLifecycleStore | None = None,
        drain_coordinator: ProviderAttemptInFlightCoordinator,
    ) -> FinalProviderAttemptGate:
        if snapshot_store is None:
            raise ValueError("role-session provider gate requires an explicit snapshot store")
        return cls(
            workspace=workspace,
            verification_scope="role_session",
            factory_run_id="",
            scope_id=role_session_id,
            run_id=run_id,
            role=role,
            turn_id=turn_id,
            call_id=call_id,
            request_freeze_id=request_freeze_id,
            provider=provider,
            model=model,
            semantic_request=semantic_request,
            lifecycle=lifecycle
            or StrictProviderAttemptLifecycleStore.for_role_session(
                workspace=workspace,
                role_session_id=role_session_id,
            ),
            snapshot_store=snapshot_store,
            drain_coordinator=drain_coordinator,
            physical_attempt_control_port=None,
            execution_authority_hash="",
            attempt_budget=0,
            qualification_proof=None,
        )

    @property
    def drain_coordinator(self) -> ProviderAttemptInFlightDrainPort:
        return self._drain_coordinator

    def _require_lifecycle(self) -> StrictProviderAttemptLifecycleStore:
        lifecycle = self._lifecycle
        if lifecycle is None:
            if self._verification_scope != "factory":
                raise RuntimeError("provider_attempt_lifecycle_store_required")
            lifecycle = StrictProviderAttemptLifecycleStore.for_factory_run(
                workspace=self._workspace,
                factory_run_id=self._factory_run_id,
            )
            self._lifecycle = lifecycle
        return lifecycle

    def dispatch_sync(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], _ResultT],
    ) -> _ResultT:
        attempt, pin, lease = self._start_attempt(wire_request)
        status = "failed"
        error = ""
        try:
            result = send(attempt.dispatch_view)
            status = "completed"
            return result
        except BaseException as exc:
            status = "cancelled" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed"
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._append_terminal_sync(attempt, pin, lease, status=status, error=error)

    async def dispatch_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], Awaitable[_ResultT]],
    ) -> _ResultT:
        """Dispatch a native async transport after durable start registration."""

        attempt, pin, lease = self._start_attempt(wire_request)
        try:
            result = await send(attempt.dispatch_view)
        except asyncio.CancelledError as cancellation:
            await self._append_terminal_async(
                attempt,
                pin,
                lease,
                status="cancelled",
                error=f"CancelledError: {cancellation}",
            )
            raise cancellation
        except BaseException as exc:
            terminal_cancelled = await self._append_terminal_async(
                attempt,
                pin,
                lease,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            if terminal_cancelled:
                raise asyncio.CancelledError from exc
            raise
        terminal_cancelled = await self._append_terminal_async(
            attempt,
            pin,
            lease,
            status="completed",
            error="",
        )
        if terminal_cancelled:
            raise asyncio.CancelledError
        return result

    def dispatch_stream_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        open_stream: Callable[[Mapping[str, Any]], AsyncContextManager[_StreamResponseT]],
        consume: Callable[[_StreamResponseT], AsyncIterator[_ResultT]],
    ) -> AsyncIterator[_ResultT]:
        """Keep one physical attempt open until its response is fully consumed.

        ``open_stream`` owns the concrete response and session resources.  The
        gate drives its async context explicitly so cleanup finishes before the
        shielded durable terminal is appended.  Every call to this method is
        exactly one physical transport attempt; retry loops remain outside.
        """

        async def _dispatch() -> AsyncIterator[_ResultT]:
            attempt, pin, lease = self._start_attempt(wire_request)
            stream_context: AsyncContextManager[_StreamResponseT] | None = None
            response: _StreamResponseT | None = None
            response_entered = False
            primary_error: BaseException | None = None
            primary_traceback: Any = None
            cleanup_error_text = ""
            cleanup_cancelled = False
            cancellation_observed = False
            buffered_items: list[_ResultT] = []
            buffered_bytes = 0
            buffered_event_limit = _qualified_stream_event_limit(attempt.dispatch_view)

            try:
                stream_context = open_stream(attempt.dispatch_view)
                response = await stream_context.__aenter__()
                response_entered = True
                async for item in consume(response):
                    # A provider chunk is not authoritative success evidence.
                    # Hold every item inside the physical-attempt boundary until
                    # the response is closed and the strict terminal lifecycle
                    # fact is durably acknowledged.  Otherwise a terminal fsync
                    # failure could leak apparently successful output upstream.
                    item_bytes = _buffered_stream_item_bytes(item)
                    next_event_count = len(buffered_items) + 1
                    next_buffered_bytes = buffered_bytes + item_bytes
                    if next_event_count > buffered_event_limit or next_buffered_bytes > _MAX_BUFFERED_STREAM_BYTES:
                        exceeded = "events" if next_event_count > buffered_event_limit else "bytes"
                        raise RuntimeError(
                            "factory_provider_stream_buffer_limit_exceeded:"
                            f"dimension={exceeded}:event_count={next_event_count}:"
                            f"event_limit={buffered_event_limit}:buffered_bytes={next_buffered_bytes}:"
                            f"byte_limit={_MAX_BUFFERED_STREAM_BYTES}"
                        )
                    buffered_items.append(item)
                    buffered_bytes += item_bytes
            except BaseException as exc:  # noqa: BLE001 - terminalize and re-raise all physical failures
                primary_error = exc
                primary_traceback = exc.__traceback__

            if response_entered and stream_context is not None:

                async def _close_stream_context() -> BaseException | None:
                    try:
                        await stream_context.__aexit__(
                            type(primary_error) if primary_error is not None else None,
                            primary_error,
                            primary_traceback,
                        )
                    except BaseException as cleanup_error:  # noqa: BLE001 - return evidence without killing loop
                        return cleanup_error
                    return None

                exit_task = asyncio.create_task(_close_stream_context())
                cleanup_wait_cancelled, cleanup_timed_out = await self._await_task_until_timeout_resisting_cancellation(
                    exit_task,
                    timeout_seconds=_STREAM_CONTEXT_CLEANUP_TIMEOUT_SECONDS,
                )
                cancellation_observed = cancellation_observed or cleanup_wait_cancelled
                cleanup_error: BaseException | None
                if cleanup_timed_out:
                    exit_task.cancel()
                    (
                        cancel_wait_cancelled,
                        cancel_timed_out,
                    ) = await self._await_task_until_timeout_resisting_cancellation(
                        exit_task,
                        timeout_seconds=_STREAM_CONTEXT_CLEANUP_CANCEL_GRACE_SECONDS,
                    )
                    cancellation_observed = cancellation_observed or cancel_wait_cancelled
                    if cancel_timed_out:
                        exit_task.add_done_callback(self._consume_task_outcome)
                    cleanup_error = RuntimeError(
                        f"provider_stream_cleanup_timeout:timeout_seconds={_STREAM_CONTEXT_CLEANUP_TIMEOUT_SECONDS:g}"
                    )
                else:
                    cleanup_error = exit_task.result()
                if cleanup_error is not None:
                    cleanup_error_text = f"{type(cleanup_error).__name__}: {cleanup_error}"
                    cleanup_cancelled = isinstance(
                        cleanup_error,
                        (asyncio.CancelledError, GeneratorExit, KeyboardInterrupt, SystemExit),
                    )
                    if primary_error is None:
                        primary_error = cleanup_error
                        primary_traceback = cleanup_error.__traceback__
                    else:
                        primary_error.add_note(f"stream cleanup failed: {cleanup_error_text}")

            if (
                isinstance(primary_error, (asyncio.CancelledError, GeneratorExit, KeyboardInterrupt, SystemExit))
                or cleanup_cancelled
                or cancellation_observed
            ):
                status = "cancelled"
            elif primary_error is None:
                status = "completed"
            else:
                status = "failed"
            error = "" if primary_error is None else f"{type(primary_error).__name__}: {primary_error}"
            if cleanup_error_text and cleanup_error_text not in error:
                error = f"{error}; stream cleanup failed: {cleanup_error_text}".lstrip("; ")

            terminal_cancelled = await self._append_terminal_async(
                attempt,
                pin,
                lease,
                status=status,
                error=error,
            )
            cancellation_observed = cancellation_observed or terminal_cancelled
            if cancellation_observed and not isinstance(primary_error, GeneratorExit):
                if primary_error is None:
                    raise asyncio.CancelledError
                raise asyncio.CancelledError from primary_error
            if primary_error is not None:
                raise primary_error.with_traceback(primary_traceback)
            for item in buffered_items:
                yield item

        return _dispatch()

    async def dispatch_blocking_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], _ResultT],
    ) -> _ResultT:
        """Run one blocking transport in a worker that owns terminal settlement.

        Cancelling the caller never cancels or prematurely settles the worker.
        Concrete transport cleanup remains the transport callback's responsibility.
        """

        attempt, pin, lease = self._start_attempt(wire_request)

        def _worker_owned_dispatch() -> _ResultT:
            try:
                worker_result = send(attempt.dispatch_view)
            except BaseException as worker_error:
                worker_status = (
                    "cancelled"
                    if isinstance(worker_error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit))
                    else "failed"
                )
                self._append_terminal_sync(
                    attempt,
                    pin,
                    lease,
                    status=worker_status,
                    error=f"{type(worker_error).__name__}: {worker_error}",
                )
                raise
            self._append_terminal_sync(attempt, pin, lease, status="completed", error="")
            return worker_result

        worker = asyncio.create_task(asyncio.to_thread(_worker_owned_dispatch))
        try:
            result = await asyncio.shield(worker)
        except asyncio.CancelledError as cancellation:
            with suppress(BaseException):
                # The worker owns durable settlement; its transport/terminal
                # exception is retained by its task and drain diagnostics.
                await self._await_task_resisting_cancellation(worker)
            raise cancellation
        return result

    def _start_attempt(
        self,
        wire_request: Mapping[str, Any],
    ) -> tuple[FrozenFinalProviderAttemptV1, Any, FactoryPhysicalAttemptLeaseV1 | None]:
        attempt, reservation = self._freeze(wire_request)
        try:
            lifecycle = self._require_lifecycle()
            pin = self._snapshot_store.persist_and_pin(attempt)
        except BaseException:
            if reservation is not None:
                self._abort_plain_reservation(reservation)
            raise
        if reservation is None:
            lifecycle.append_start(
                attempt,
                context_snapshot_ref=pin.context_snapshot_ref,
                pin_hash=pin.pin_hash,
            )
            self._require_session_drain_coordinator().register(attempt)
            return attempt, pin, None

        control = self._require_factory_control_port()
        try:
            start_permit = control.begin_start(self._begin_start_command(reservation))
        except BaseException:
            self._abort_plain_reservation(reservation)
            raise
        try:
            start_receipt = lifecycle.append_start(
                attempt,
                start_permit=start_permit,
                context_snapshot_ref=pin.context_snapshot_ref,
                pin_hash=pin.pin_hash,
            )
        except BaseException as start_error:
            self._resolve_failed_start_persistence(reservation, start_permit, start_error)
            raise
        try:
            if type(start_receipt) is not ProviderAttemptStartReceiptV1:
                raise RuntimeError("provider_attempt_start_receipt_exact_type_required")
            lease = control.commit_started(self._commit_start_command(start_permit, start_receipt))
        except BaseException:
            self._mark_start_ambiguous(start_permit)
            raise
        return attempt, pin, lease

    def _append_terminal_sync(
        self,
        attempt: FrozenFinalProviderAttemptV1,
        pin: Any,
        lease: FactoryPhysicalAttemptLeaseV1 | None,
        *,
        status: str,
        error: str,
    ) -> None:
        try:
            terminal_receipt = self._require_lifecycle().append_terminal(
                attempt,
                lease=lease,
                context_snapshot_ref=pin.context_snapshot_ref,
                pin_hash=pin.pin_hash,
                status=status,
                error=error,
            )
            if self._verification_scope == "factory":
                if type(lease) is not FactoryPhysicalAttemptLeaseV1:
                    raise RuntimeError("factory_physical_attempt_lease_exact_type_required")
                if type(terminal_receipt) is not ProviderAttemptTerminalReceiptV1:
                    raise RuntimeError("provider_attempt_terminal_receipt_exact_type_required")
                self._require_factory_control_port().settle(
                    SettleFactoryPhysicalAttemptV1(
                        schema_version=SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
                        lease=lease,
                        terminal_receipt=terminal_receipt,
                    )
                )
        except BaseException as terminal_error:
            if type(lease) is FactoryPhysicalAttemptLeaseV1:
                try:
                    self._require_factory_control_port().terminal_persistence_failed(
                        FailFactoryPhysicalAttemptTerminalV1(
                            schema_version=FAIL_FACTORY_PHYSICAL_ATTEMPT_TERMINAL_SCHEMA,
                            lease=lease,
                            failure_code="terminal_persistence_failed",
                            error_type=type(terminal_error).__name__,
                            error=(str(terminal_error).strip() or type(terminal_error).__name__)[:500],
                        )
                    )
                except Exception as control_error:  # noqa: BLE001 - preserve primary terminal failure evidence
                    terminal_error.add_note(f"physical attempt terminal failure projection failed: {control_error}")
            if self._verification_scope == "role_session":
                self._require_session_drain_coordinator().terminal_failed(
                    attempt.provider_request_id,
                    terminal_error,
                )
            raise
        if self._verification_scope == "role_session":
            self._require_session_drain_coordinator().terminal_acked(attempt.provider_request_id)

    async def _append_terminal_async(
        self,
        attempt: FrozenFinalProviderAttemptV1,
        pin: Any,
        lease: FactoryPhysicalAttemptLeaseV1 | None,
        *,
        status: str,
        error: str,
    ) -> bool:
        terminal = asyncio.create_task(
            asyncio.to_thread(
                self._append_terminal_sync,
                attempt,
                pin,
                lease,
                status=status,
                error=error,
            )
        )
        return await self._await_task_resisting_cancellation(terminal)

    def _require_factory_control_port(self) -> FactoryPhysicalAttemptControlPort:
        port = self._physical_attempt_control_port
        if not isinstance(port, FactoryPhysicalAttemptControlPort):
            raise RuntimeError("factory_physical_attempt_control_port_required")
        return port

    def _require_session_drain_coordinator(self) -> ProviderAttemptInFlightCoordinator:
        coordinator = self._drain_coordinator
        if type(coordinator) is not ProviderAttemptInFlightCoordinator:
            raise RuntimeError("provider attempt drain coordinator scope mismatch")
        return coordinator

    def _abort_plain_reservation(self, reservation: FactoryPhysicalAttemptReservationV1) -> None:
        self._require_factory_control_port().abort_reservation(
            AbortFactoryPhysicalAttemptReservationV1(
                schema_version=ABORT_FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
                reservation=reservation,
                start_permit=None,
                definite_start_not_persisted_proof=None,
            )
        )

    def _mark_start_ambiguous(self, start_permit: FactoryPhysicalAttemptStartPermitV1) -> None:
        self._require_factory_control_port().mark_start_ambiguous(
            MarkFactoryPhysicalAttemptStartAmbiguousV1(
                schema_version=MARK_FACTORY_PHYSICAL_ATTEMPT_START_AMBIGUOUS_SCHEMA,
                start_permit=start_permit,
                reason_code="start_persistence_or_commit_ambiguous",
            )
        )

    def _resolve_failed_start_persistence(
        self,
        reservation: FactoryPhysicalAttemptReservationV1,
        start_permit: FactoryPhysicalAttemptStartPermitV1,
        start_error: BaseException,
    ) -> None:
        try:
            absence_proof = self._require_lifecycle().prove_start_not_persisted(start_permit)
        except BaseException as proof_error:  # noqa: BLE001 - uncertainty must remain fail-closed
            self._mark_start_ambiguous(start_permit)
            start_error.add_note(
                "strict start absence proof failed; reservation marked ambiguous: "
                f"{type(proof_error).__name__}: {proof_error}"
            )
            return
        if absence_proof is None:
            self._mark_start_ambiguous(start_permit)
            return
        if type(absence_proof) is not FactoryPhysicalAttemptDefiniteStartNotPersistedProofV1:
            self._mark_start_ambiguous(start_permit)
            start_error.add_note("strict start absence proof returned a non-exact proof")
            return
        self._require_factory_control_port().abort_reservation(
            AbortFactoryPhysicalAttemptReservationV1(
                schema_version=ABORT_FACTORY_PHYSICAL_ATTEMPT_RESERVATION_SCHEMA,
                reservation=reservation,
                start_permit=start_permit,
                definite_start_not_persisted_proof=absence_proof,
            )
        )

    @staticmethod
    def _begin_start_command(
        reservation: FactoryPhysicalAttemptReservationV1,
    ) -> BeginFactoryPhysicalAttemptStartV1:
        return BeginFactoryPhysicalAttemptStartV1(
            schema_version=BEGIN_FACTORY_PHYSICAL_ATTEMPT_START_SCHEMA,
            verification_scope=reservation.verification_scope,
            factory_run_id=reservation.factory_run_id,
            run_id=reservation.run_id,
            role=reservation.role,
            turn_id=reservation.turn_id,
            call_id=reservation.call_id,
            request_freeze_id=reservation.request_freeze_id,
            execution_authority_hash=reservation.execution_authority_hash,
            attempt_budget=reservation.attempt_budget,
            provider=reservation.provider,
            model=reservation.model,
            semantic_request_hash=reservation.semantic_request_hash,
            physical_wire_hash=reservation.physical_wire_hash,
            composite_request_hash=reservation.composite_request_hash,
            reservation_id=reservation.reservation_id,
            provider_request_id=reservation.provider_request_id,
            authority_attempt_ordinal=reservation.authority_attempt_ordinal,
        )

    @staticmethod
    def _commit_start_command(
        start_permit: FactoryPhysicalAttemptStartPermitV1,
        start_receipt: ProviderAttemptStartReceiptV1,
    ) -> CommitFactoryPhysicalAttemptStartV1:
        return CommitFactoryPhysicalAttemptStartV1(
            schema_version=COMMIT_FACTORY_PHYSICAL_ATTEMPT_START_SCHEMA,
            verification_scope=start_permit.verification_scope,
            factory_run_id=start_permit.factory_run_id,
            run_id=start_permit.run_id,
            role=start_permit.role,
            turn_id=start_permit.turn_id,
            call_id=start_permit.call_id,
            request_freeze_id=start_permit.request_freeze_id,
            execution_authority_hash=start_permit.execution_authority_hash,
            attempt_budget=start_permit.attempt_budget,
            provider=start_permit.provider,
            model=start_permit.model,
            semantic_request_hash=start_permit.semantic_request_hash,
            physical_wire_hash=start_permit.physical_wire_hash,
            composite_request_hash=start_permit.composite_request_hash,
            reservation_id=start_permit.reservation_id,
            provider_request_id=start_permit.provider_request_id,
            authority_attempt_ordinal=start_permit.authority_attempt_ordinal,
            start_permit_id=start_permit.start_permit_id,
            start_receipt=start_receipt,
        )

    @staticmethod
    async def _await_task_resisting_cancellation(task: asyncio.Task[Any]) -> bool:
        cancellation_observed = False
        while True:
            try:
                await asyncio.shield(task)
                return cancellation_observed
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
                cancellation_observed = True

    @staticmethod
    async def _await_task_until_timeout_resisting_cancellation(
        task: asyncio.Task[Any],
        *,
        timeout_seconds: float,
    ) -> tuple[bool, bool]:
        """Wait without transferring caller cancellation; report a hard timeout."""

        cancellation_observed = False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout_seconds)
        while True:
            if task.done():
                return cancellation_observed, False
            remaining_seconds = deadline - loop.time()
            if remaining_seconds <= 0:
                return cancellation_observed, True
            try:
                done, _pending = await asyncio.wait({task}, timeout=remaining_seconds)
            except asyncio.CancelledError:
                cancellation_observed = True
                continue
            if task in done:
                return cancellation_observed, False
            return cancellation_observed, True

    @staticmethod
    def _consume_task_outcome(task: asyncio.Task[Any]) -> None:
        with suppress(BaseException):
            task.result()

    def _resolve_scope_id(self, explicit_scope_id: str | None) -> str:
        explicit = str(explicit_scope_id or "").strip()
        if self._verification_scope == "factory":
            if not self._factory_run_id:
                raise RuntimeError("Factory provider gate requires factory_run_id")
            if explicit and explicit != self._factory_run_id:
                raise RuntimeError("Factory provider gate scope_id mismatch")
            return self._factory_run_id
        if self._factory_run_id:
            raise RuntimeError("role-session provider gate cannot claim factory_run_id")
        if not explicit:
            raise RuntimeError("role-session provider gate requires explicit scope_id")
        return explicit

    def _freeze(
        self,
        wire_request: Mapping[str, Any],
    ) -> tuple[FrozenFinalProviderAttemptV1, FactoryPhysicalAttemptReservationV1 | None]:
        wire = copy.deepcopy(dict(wire_request))
        if self._verification_scope == "factory":
            proof = self._qualification_proof
            if type(proof) is not _FinalProviderAttemptQualificationProofV1:
                raise RuntimeError("final_provider_attempt_qualification_proof_required")
            try:
                proof.validate_gate_binding(
                    workspace=self._workspace,
                    factory_run_id=self._factory_run_id,
                    run_id=self._run_id,
                    role=self._role,
                    turn_id=self._turn_id,
                    call_id=self._call_id,
                    request_freeze_id=self._request_freeze_id,
                    provider=self._provider,
                    model=self._model,
                    semantic_request=self._semantic_request,
                    wire_request=wire,
                )
            except (RuntimeError, TypeError, ValueError) as exc:
                raise RuntimeError("final_provider_attempt_qualification_proof_invalid") from exc
            semantic_candidate_hash = proof.semantic_candidate_hash
        else:
            semantic_candidate_hash = ""
        if self._verification_scope != "factory":
            # Role-session requests retain the legacy semantic projection.
            # Factory requests have already passed the stricter provider-native
            # proof check above; re-applying an OpenAI-shaped projection here
            # would reject valid Anthropic bodies and tolerate keys it omits.
            self._validate_wire_equivalence(wire)
        durable_wire = redact_provider_transport(wire)
        semantic_hash = canonical_final_request_hash(self._semantic_request)
        wire_hash = canonical_final_request_hash(durable_wire)
        reservation: FactoryPhysicalAttemptReservationV1 | None = None
        if self._verification_scope == "factory":
            reservation = self._require_factory_control_port().reserve(
                ReserveFactoryPhysicalAttemptV1(
                    schema_version=RESERVE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
                    verification_scope="factory",
                    factory_run_id=self._factory_run_id,
                    run_id=self._run_id,
                    role=self._role,
                    turn_id=self._turn_id,
                    call_id=self._call_id,
                    request_freeze_id=self._request_freeze_id,
                    execution_authority_hash=self._execution_authority_hash,
                    attempt_budget=self._attempt_budget,
                    provider=self._provider,
                    model=self._model,
                    semantic_request_hash=semantic_hash,
                    physical_wire_hash=wire_hash,
                )
            )
            attempt_number = reservation.authority_attempt_ordinal
            provider_request_id = reservation.provider_request_id
            composite_hash = reservation.composite_request_hash
            execution_authority_hash = reservation.execution_authority_hash
            attempt_budget = reservation.attempt_budget
            authority_attempt_ordinal = reservation.authority_attempt_ordinal
        else:
            attempt_number, provider_request_id = self._require_session_drain_coordinator().mint_attempt_identity()
            composite_hash = canonical_final_request_hash(
                {
                    "semantic_request_hash": semantic_hash,
                    "physical_wire_hash": wire_hash,
                    "provider_request_id": provider_request_id,
                    "request_freeze_id": self._request_freeze_id,
                }
            )
            execution_authority_hash = ""
            attempt_budget = 0
            authority_attempt_ordinal = 0
        durable_view = {
            "schema_version": "llm.frozen_final_provider_attempt.v1",
            "canonical_semantic_request": self._semantic_request,
            "physical_wire": durable_wire,
        }
        if self._final_request_context_audit is not None:
            durable_view["final_request_qualification"] = {
                "schema_version": "llm.final_provider_attempt_qualification.v1",
                "context_snapshot_ref": self._qualification_context_snapshot_ref,
                "final_request_context_audit": self._final_request_context_audit,
            }
        if reservation is not None:
            durable_view["physical_attempt_authority"] = {
                "execution_authority_hash": execution_authority_hash,
                "attempt_budget": attempt_budget,
                "authority_attempt_ordinal": authority_attempt_ordinal,
            }
        attempt = FrozenFinalProviderAttemptV1(
            provider_request_id=provider_request_id,
            request_freeze_id=self._request_freeze_id,
            factory_run_id=self._factory_run_id,
            scope_id=self._scope_id,
            run_id=self._run_id,
            turn_id=self._turn_id,
            call_id=self._call_id,
            role=self._role,
            provider=self._provider,
            model=self._model,
            attempt_number=attempt_number,
            verification_scope=self._verification_scope,
            execution_authority_hash=execution_authority_hash,
            attempt_budget=attempt_budget,
            authority_attempt_ordinal=authority_attempt_ordinal,
            semantic_candidate_hash=semantic_candidate_hash,
            semantic_request_hash=semantic_hash,
            physical_wire_hash=wire_hash,
            composite_request_hash=composite_hash,
            dispatch_view=wire,
            durable_view=durable_view,
        )
        return attempt, reservation

    def _validate_role_identity(self) -> None:
        messages = self._semantic_request.get("messages")
        if not isinstance(messages, list) or not messages:
            raise RuntimeError("governed semantic request requires messages")
        first = messages[0]
        expected = f"polaris.role_identity.v1:{self._role}"
        if (
            not isinstance(first, dict)
            or first.get("role") != "system"
            or expected not in str(first.get("content") or "")
        ):
            raise RuntimeError("governed semantic request role identity mismatch")

    def _validate_wire_equivalence(self, wire: Mapping[str, Any]) -> None:
        body = wire.get("body")
        if not isinstance(body, Mapping):
            raise RuntimeError("governed physical wire requires a complete body")
        for key in ("messages", "tools", "tool_choice", "response_format"):
            expected = self._semantic_request.get(key)
            actual = body.get(key)
            if expected != actual:
                raise RuntimeError(f"provider wire semantic projection mismatch: {key}")
        if body.get("model") != self._model:
            raise RuntimeError("provider wire semantic projection mismatch: model")
        expected_options = self._semantic_request.get("semantic_options")
        if isinstance(expected_options, Mapping):
            for key, expected in expected_options.items():
                actual = body.get(key)
                if key == "max_tokens" and self._is_valid_retry_max_tokens(expected=expected, actual=actual):
                    continue
                if actual != expected:
                    raise RuntimeError(f"provider wire semantic projection mismatch: {key}")

    @staticmethod
    def _is_valid_retry_max_tokens(*, expected: Any, actual: Any) -> bool:
        """Allow only a positive retry-time shrink of the authorized output cap."""

        if isinstance(expected, bool) or isinstance(actual, bool):
            return False
        return isinstance(expected, int) and isinstance(actual, int) and 0 < actual <= expected

    @staticmethod
    def _json_copy(value: Mapping[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            decoded = json.loads(encoded)
        except (TypeError, ValueError) as exc:
            raise ValueError("provider_config_not_snapshot_safe") from exc
        if not isinstance(decoded, dict):
            raise ValueError("provider_config_not_snapshot_safe")
        return decoded
