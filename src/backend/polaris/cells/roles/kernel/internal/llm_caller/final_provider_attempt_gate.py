"""Freeze, persist, pin, and account for each physical provider dispatch."""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import suppress
from typing import Any, AsyncContextManager, Literal, TypeVar

from polaris.kernelone.events.final_request_evidence import (
    ContextSnapshotAuditPinV1,
    canonical_final_request_hash,
    redact_provider_transport,
)
from polaris.kernelone.llm.engine.context_store_retention import ContextSnapshotAuditPinRepository
from polaris.kernelone.llm.engine.contracts import FrozenFinalProviderAttemptV1

from .final_provider_attempt_inflight import ProviderAttemptInFlightCoordinator
from .final_provider_attempt_lifecycle import StrictProviderAttemptLifecycleStore

_ResultT = TypeVar("_ResultT")
_StreamResponseT = TypeVar("_StreamResponseT")


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
        lifecycle: StrictProviderAttemptLifecycleStore,
        snapshot_store: Any,
        drain_coordinator: ProviderAttemptInFlightCoordinator,
    ) -> None:
        del workspace
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
        drain_coordinator: ProviderAttemptInFlightCoordinator,
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
            lifecycle=lifecycle
            or StrictProviderAttemptLifecycleStore.for_factory_run(
                workspace=workspace,
                factory_run_id=factory_run_id,
            ),
            snapshot_store=snapshot_store or DurableFinalProviderAttemptSnapshotStore(workspace),
            drain_coordinator=drain_coordinator,
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
        )

    @property
    def drain_coordinator(self) -> ProviderAttemptInFlightCoordinator:
        return self._drain_coordinator

    def dispatch_sync(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], _ResultT],
    ) -> _ResultT:
        attempt, pin = self._start_attempt(wire_request)
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
            self._append_terminal_sync(attempt, pin, status=status, error=error)

    async def dispatch_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], Awaitable[_ResultT]],
    ) -> _ResultT:
        """Dispatch a native async transport after durable start registration."""

        attempt, pin = self._start_attempt(wire_request)
        try:
            result = await send(attempt.dispatch_view)
        except asyncio.CancelledError as cancellation:
            await self._append_terminal_async(
                attempt,
                pin,
                status="cancelled",
                error=f"CancelledError: {cancellation}",
            )
            raise cancellation
        except BaseException as exc:
            terminal_cancelled = await self._append_terminal_async(
                attempt,
                pin,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            if terminal_cancelled:
                raise asyncio.CancelledError from exc
            raise
        terminal_cancelled = await self._append_terminal_async(
            attempt,
            pin,
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
            attempt, pin = self._start_attempt(wire_request)
            stream_context: AsyncContextManager[_StreamResponseT] | None = None
            response: _StreamResponseT | None = None
            response_entered = False
            primary_error: BaseException | None = None
            primary_traceback: Any = None
            cleanup_error_text = ""
            cleanup_cancelled = False
            cancellation_observed = False

            try:
                stream_context = open_stream(attempt.dispatch_view)
                response = await stream_context.__aenter__()
                response_entered = True
                async for item in consume(response):
                    yield item
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
                cancellation_observed = await self._await_task_resisting_cancellation(exit_task)
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

        attempt, pin = self._start_attempt(wire_request)

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
                    status=worker_status,
                    error=f"{type(worker_error).__name__}: {worker_error}",
                )
                raise
            self._append_terminal_sync(attempt, pin, status="completed", error="")
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

    def _start_attempt(self, wire_request: Mapping[str, Any]) -> tuple[FrozenFinalProviderAttemptV1, Any]:
        attempt = self._freeze(wire_request)
        pin = self._snapshot_store.persist_and_pin(attempt)
        self._lifecycle.append_start(
            attempt,
            context_snapshot_ref=pin.context_snapshot_ref,
            pin_hash=pin.pin_hash,
        )
        self._drain_coordinator.register(attempt)
        return attempt, pin

    def _append_terminal_sync(
        self, attempt: FrozenFinalProviderAttemptV1, pin: Any, *, status: str, error: str
    ) -> None:
        try:
            self._lifecycle.append_terminal(
                attempt,
                context_snapshot_ref=pin.context_snapshot_ref,
                pin_hash=pin.pin_hash,
                status=status,
                error=error,
            )
        except BaseException as terminal_error:
            self._drain_coordinator.terminal_failed(attempt.provider_request_id, terminal_error)
            raise
        self._drain_coordinator.terminal_acked(attempt.provider_request_id)

    async def _append_terminal_async(
        self,
        attempt: FrozenFinalProviderAttemptV1,
        pin: Any,
        *,
        status: str,
        error: str,
    ) -> bool:
        terminal = asyncio.create_task(
            asyncio.to_thread(
                self._append_terminal_sync,
                attempt,
                pin,
                status=status,
                error=error,
            )
        )
        return await self._await_task_resisting_cancellation(terminal)

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

    def _freeze(self, wire_request: Mapping[str, Any]) -> FrozenFinalProviderAttemptV1:
        wire = copy.deepcopy(dict(wire_request))
        self._validate_wire_equivalence(wire)
        attempt_number, provider_request_id = self._drain_coordinator.mint_attempt_identity()
        durable_wire = redact_provider_transport(wire)
        semantic_hash = canonical_final_request_hash(self._semantic_request)
        wire_hash = canonical_final_request_hash(durable_wire)
        composite_hash = canonical_final_request_hash(
            {
                "semantic_request_hash": semantic_hash,
                "physical_wire_hash": wire_hash,
                "provider_request_id": provider_request_id,
                "request_freeze_id": self._request_freeze_id,
            }
        )
        durable_view = {
            "schema_version": "llm.frozen_final_provider_attempt.v1",
            "canonical_semantic_request": self._semantic_request,
            "physical_wire": durable_wire,
        }
        return FrozenFinalProviderAttemptV1(
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
            semantic_request_hash=semantic_hash,
            physical_wire_hash=wire_hash,
            composite_request_hash=composite_hash,
            dispatch_view=wire,
            durable_view=durable_view,
        )

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
