"""Run/session-scoped in-flight ownership for physical provider attempts."""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass

from polaris.kernelone.llm.engine.contracts import (
    FrozenFinalProviderAttemptV1,
    ProviderAttemptDrainResultV1,
    ProviderAttemptTerminalFailureV1,
)

_SUPPORTED_SCOPES = frozenset({"factory", "role_session"})


class ProviderAttemptDrainError(RuntimeError):
    """A scoped drain failed closed with typed in-flight diagnostics."""

    def __init__(self, message: str, *, code: str, result: ProviderAttemptDrainResultV1) -> None:
        super().__init__(message)
        self.code = code
        self.result = result


@dataclass(frozen=True, slots=True)
class _Waiter:
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[None]


class ProviderAttemptInFlightCoordinator:
    """Own exactly one Factory run or role session's physical attempts."""

    def __init__(self, *, verification_scope: str, scope_id: str) -> None:
        scope = str(verification_scope or "").strip()
        identifier = str(scope_id or "").strip()
        if scope not in _SUPPORTED_SCOPES:
            raise ValueError("provider attempt verification scope must be factory or role_session")
        if not identifier:
            raise ValueError("provider attempt scope_id is required")
        self._verification_scope = scope
        self._scope_id = identifier
        self._lock = threading.RLock()
        self._attempt_number = 0
        self._inflight: dict[str, FrozenFinalProviderAttemptV1] = {}
        self._terminal_failures: dict[str, ProviderAttemptTerminalFailureV1] = {}
        self._waiters: set[_Waiter] = set()

    @classmethod
    def for_factory_run(cls, factory_run_id: str) -> ProviderAttemptInFlightCoordinator:
        return cls(verification_scope="factory", scope_id=factory_run_id)

    @classmethod
    def for_role_session(cls, role_session_id: str) -> ProviderAttemptInFlightCoordinator:
        return cls(verification_scope="role_session", scope_id=role_session_id)

    @property
    def verification_scope(self) -> str:
        return self._verification_scope

    @property
    def scope_id(self) -> str:
        return self._scope_id

    @property
    def inflight_request_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._inflight))

    def mint_attempt_identity(self) -> tuple[int, str]:
        """Mint a thread-safe monotonic number and globally unique request id."""

        with self._lock:
            self._attempt_number += 1
            return self._attempt_number, uuid.uuid4().hex

    def register(self, attempt: FrozenFinalProviderAttemptV1) -> None:
        self._validate_attempt_scope(attempt)
        with self._lock:
            request_id = attempt.provider_request_id
            if request_id in self._inflight or request_id in self._terminal_failures:
                raise RuntimeError("duplicate provider attempt registration")
            self._inflight[request_id] = attempt

    def terminal_acked(self, provider_request_id: str) -> None:
        request_id = str(provider_request_id or "").strip()
        with self._lock:
            if request_id in self._terminal_failures:
                raise RuntimeError("provider attempt terminal persistence already failed")
            if request_id not in self._inflight:
                raise RuntimeError("late or unknown provider attempt settlement")
            del self._inflight[request_id]
            self._notify_waiters_locked()

    def terminal_failed(self, provider_request_id: str, error: BaseException) -> None:
        request_id = str(provider_request_id or "").strip()
        error_type = type(error).__name__
        error_message = str(error).strip() or error_type
        with self._lock:
            if request_id in self._terminal_failures:
                raise RuntimeError("duplicate provider attempt terminal failure")
            if request_id not in self._inflight:
                raise RuntimeError("late or unknown provider attempt settlement")
            self._terminal_failures[request_id] = ProviderAttemptTerminalFailureV1(
                provider_request_id=request_id,
                error_type=error_type,
                error=error_message[:500],
            )
            self._notify_waiters_locked()

    async def wait_settled(
        self,
        *,
        verification_scope: str,
        scope_id: str,
        timeout_seconds: float | None = None,
    ) -> ProviderAttemptDrainResultV1:
        self._validate_wait_scope(verification_scope=verification_scope, scope_id=scope_id)
        timeout = self._validated_timeout(timeout_seconds)
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
        while True:
            with self._lock:
                result = self._snapshot_locked()
                if result.terminal_failures:
                    raise ProviderAttemptDrainError(
                        "provider attempt terminal persistence failed",
                        code="provider_attempt_terminal_persistence_failed",
                        result=result,
                    )
                if result.settled:
                    return result
                waiter = _Waiter(loop=loop, future=loop.create_future())
                self._waiters.add(waiter)
            try:
                if deadline is None:
                    await waiter.future
                else:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise TimeoutError
                    await asyncio.wait_for(asyncio.shield(waiter.future), timeout=remaining)
            except TimeoutError as exc:
                with self._lock:
                    timeout_result = self._snapshot_locked()
                raise ProviderAttemptDrainError(
                    "provider attempt drain timed out",
                    code="provider_attempt_drain_timeout",
                    result=timeout_result,
                ) from exc
            finally:
                with self._lock:
                    self._waiters.discard(waiter)

    def snapshot(self) -> ProviderAttemptDrainResultV1:
        with self._lock:
            return self._snapshot_locked()

    def _validate_attempt_scope(self, attempt: FrozenFinalProviderAttemptV1) -> None:
        if attempt.verification_scope != self._verification_scope or attempt.scope_id != self._scope_id:
            raise RuntimeError("provider attempt scope mismatch")
        if self._verification_scope == "factory" and attempt.factory_run_id != self._scope_id:
            raise RuntimeError("provider attempt Factory run mismatch")
        if self._verification_scope == "role_session" and attempt.factory_run_id:
            raise RuntimeError("role-session provider attempt cannot claim Factory scope")

    def _validate_wait_scope(self, *, verification_scope: str, scope_id: str) -> None:
        if str(verification_scope) != self._verification_scope or str(scope_id) != self._scope_id:
            raise ProviderAttemptDrainError(
                "provider attempt drain scope mismatch",
                code="provider_attempt_drain_scope_mismatch",
                result=self.snapshot(),
            )

    @staticmethod
    def _validated_timeout(timeout_seconds: float | None) -> float | None:
        if timeout_seconds is None:
            return None
        if isinstance(timeout_seconds, bool):
            raise ValueError("timeout_seconds must be positive or None")
        timeout = float(timeout_seconds)
        if timeout <= 0:
            raise ValueError("timeout_seconds must be positive or None")
        return timeout

    def _snapshot_locked(self) -> ProviderAttemptDrainResultV1:
        failures = tuple(self._terminal_failures[key] for key in sorted(self._terminal_failures))
        inflight = tuple(sorted(self._inflight))
        return ProviderAttemptDrainResultV1(
            verification_scope=self._verification_scope,
            scope_id=self._scope_id,
            settled=not inflight and not failures,
            inflight_request_ids=inflight,
            terminal_failures=failures,
        )

    def _notify_waiters_locked(self) -> None:
        if self._inflight and not self._terminal_failures:
            return
        waiters = tuple(self._waiters)
        self._waiters.clear()
        for waiter in waiters:
            waiter.loop.call_soon_threadsafe(self._wake_waiter, waiter.future)

    @staticmethod
    def _wake_waiter(future: asyncio.Future[None]) -> None:
        if not future.done():
            future.set_result(None)


__all__ = [
    "ProviderAttemptDrainError",
    "ProviderAttemptInFlightCoordinator",
]
